"""
Durable email outbox for must-not-miss emails (payment notifications).

Why this exists: payment confirmation emails were silently lost when the
inline SMTP send failed (stale primary Gmail app password + no retry, and the
frontend swallows email errors). This module guarantees at-least-once delivery:

1. The fully-built mail options (from/to/bcc/subject/text/html) are written to
   the `email_outbox` table in the local Postgres (dgmts_static_db) BEFORE any
   send is attempted.
2. An inline send is attempted immediately so emails still arrive right away.
3. The single-worker scheduler (utils/scheduler.py, flock-guarded so only one
   Gunicorn worker runs it) calls process_pending() every 2 minutes and retries
   anything still pending with exponential backoff (max 1 hour between tries).

A crash between a successful SMTP send and mark_sent() can produce a duplicate
email on retry — never a missing one. Rows are claimed with
FOR UPDATE SKIP LOCKED so concurrent processors cannot double-send.

Email credentials come from the `email_config` table in the LOCAL Postgres
(the same copy the admin panel edits) — NOT the old DGMTS Static Supabase.
The secondary config is preferred because the primary row holds stale
credentials that fail SMTP auth; the other config is used as fallback.
"""

import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import psycopg2.extras

from models.static_db import static_db

# Give up (status='failed') after this many attempts; with the backoff below
# this covers roughly two days of retries.
MAX_ATTEMPTS = 50
MAX_BACKOFF_SECONDS = 3600
SMTP_TIMEOUT_SECONDS = 30

_table_ready = False
_table_lock = threading.Lock()


def _ensure_table():
    """Create email_outbox on first use so no manual migration is required."""
    global _table_ready
    if _table_ready:
        return
    with _table_lock:
        if _table_ready:
            return
        static_db.execute("""
            CREATE TABLE IF NOT EXISTS email_outbox (
                id BIGSERIAL PRIMARY KEY,
                kind TEXT NOT NULL,
                mail_options JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                sent_at TIMESTAMPTZ
            )
        """)
        static_db.execute("""
            CREATE INDEX IF NOT EXISTS idx_email_outbox_pending
            ON email_outbox (next_attempt_at) WHERE status = 'pending'
        """)
        _table_ready = True


def load_email_configs():
    """
    Usable email_config rows from local Postgres, SECONDARY FIRST.

    The primary row currently holds stale credentials that fail SMTP auth,
    so the working secondary config is preferred; primary stays as fallback.
    """
    rows = static_db.query('SELECT * FROM email_config')
    usable = [
        r for r in rows
        if (r.get('email_id') or '').strip() and (r.get('email_password') or '').strip()
    ]
    usable.sort(key=lambda r: 0 if r.get('type') == 'secondary' else 1)
    return usable


def smtp_settings_for(email_id):
    email_lower = (email_id or '').lower()
    if '@gmail.com' in email_lower:
        return {'host': 'smtp.gmail.com', 'port': 587}
    return {'host': 'smtp.office365.com', 'port': 587}


def _send_via_config(mail_options, config):
    """Send simple text/html mail (no attachments) through one SMTP config."""
    settings = smtp_settings_for(config['email_id'])

    msg = MIMEMultipart('alternative')
    msg['From'] = mail_options['from']
    to = mail_options['to']
    msg['To'] = to if isinstance(to, str) else ', '.join(to)
    msg['Subject'] = mail_options['subject']

    bcc = mail_options.get('bcc')
    if bcc:
        msg['Bcc'] = ', '.join(bcc) if isinstance(bcc, list) else bcc
    if mail_options.get('reply_to'):
        msg['Reply-To'] = mail_options['reply_to']
    if mail_options.get('text'):
        msg.attach(MIMEText(mail_options['text'], 'plain'))
    if mail_options.get('html'):
        msg.attach(MIMEText(mail_options['html'], 'html'))

    recipients = list(to) if isinstance(to, list) else [to]
    if bcc:
        recipients.extend(bcc if isinstance(bcc, list) else [bcc])

    server = smtplib.SMTP(settings['host'], settings['port'], timeout=SMTP_TIMEOUT_SECONDS)
    try:
        server.starttls()
        server.login(config['email_id'].strip(), config['email_password'].strip())
        server.sendmail(config['email_id'].strip(), recipients, msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass


def send_mail_options(mail_options, configs=None):
    """Try each config in order (secondary first); raise if all fail."""
    configs = configs if configs is not None else load_email_configs()
    if not configs:
        raise RuntimeError('No usable email configuration in email_config table')

    last_error = None
    for config in configs:
        try:
            _send_via_config(mail_options, config)
            print(f"[email-outbox] Sent '{mail_options.get('subject')}' using "
                  f"{config.get('type', '?')} config ({config['email_id']})")
            return config.get('type', '?')
        except Exception as e:
            last_error = e
            print(f"[email-outbox] Send failed using {config.get('type', '?')} "
                  f"config ({config['email_id']}): {e}")
    raise last_error


def enqueue(kind, mail_options):
    """Persist an email before attempting to send it. Returns the outbox id."""
    _ensure_table()
    rows = static_db.execute(
        'INSERT INTO email_outbox (kind, mail_options) VALUES (%s, %s) RETURNING id',
        (kind, psycopg2.extras.Json(mail_options)),
        returning=True,
    )
    return rows[0]['id']


def mark_sent(outbox_id):
    static_db.execute(
        "UPDATE email_outbox SET status = 'sent', sent_at = now(), last_error = NULL WHERE id = %s",
        (outbox_id,),
    )


def record_failure(outbox_id, error):
    """Bump attempts and schedule the next retry with exponential backoff."""
    static_db.execute(
        """
        UPDATE email_outbox
        SET attempts = attempts + 1,
            last_error = %s,
            next_attempt_at = now() + make_interval(secs => LEAST(60 * POWER(2, attempts), %s)),
            status = CASE WHEN attempts + 1 >= %s THEN 'failed' ELSE 'pending' END
        WHERE id = %s
        """,
        (str(error)[:2000], MAX_BACKOFF_SECONDS, MAX_ATTEMPTS, outbox_id),
    )


def send_outbox_email(outbox_id, mail_options, configs=None):
    """
    Attempt to send one enqueued email inline. On failure the row stays
    pending and the scheduler will retry it. Returns True if sent.
    """
    try:
        send_mail_options(mail_options, configs)
    except Exception as e:
        if outbox_id is not None:
            try:
                record_failure(outbox_id, e)
            except Exception as db_err:
                print(f"[email-outbox] Could not record failure for row {outbox_id}: {db_err}")
        return False

    if outbox_id is not None:
        try:
            mark_sent(outbox_id)
        except Exception as db_err:
            # Email went out; worst case the retry job sends a duplicate.
            print(f"[email-outbox] Sent but could not mark row {outbox_id} as sent: {db_err}")
    return True


def process_pending(limit=10):
    """
    Retry pending outbox emails. Called by the scheduler every 2 minutes
    (single worker); FOR UPDATE SKIP LOCKED keeps it safe even if invoked
    concurrently. Rows stay locked while their send is in flight.
    """
    _ensure_table()
    processed = sent = 0

    with static_db.cursor(commit=True) as cur:
        cur.execute(
            """
            SELECT id, kind, mail_options FROM email_outbox
            WHERE status = 'pending' AND next_attempt_at <= now()
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        if not rows:
            return {'processed': 0, 'sent': 0}

        configs = load_email_configs()
        for row in rows:
            processed += 1
            try:
                send_mail_options(dict(row['mail_options']), configs)
                cur.execute(
                    "UPDATE email_outbox SET status = 'sent', sent_at = now(), last_error = NULL WHERE id = %s",
                    (row['id'],),
                )
                sent += 1
            except Exception as e:
                print(f"[email-outbox] Retry failed for row {row['id']} ({row['kind']}): {e}")
                cur.execute(
                    """
                    UPDATE email_outbox
                    SET attempts = attempts + 1,
                        last_error = %s,
                        next_attempt_at = now() + make_interval(secs => LEAST(60 * POWER(2, attempts), %s)),
                        status = CASE WHEN attempts + 1 >= %s THEN 'failed' ELSE 'pending' END
                    WHERE id = %s
                    """,
                    (str(e)[:2000], MAX_BACKOFF_SECONDS, MAX_ATTEMPTS, row['id']),
                )

    if processed:
        print(f"[email-outbox] Retry pass: {sent}/{processed} pending emails sent")
    return {'processed': processed, 'sent': sent}
