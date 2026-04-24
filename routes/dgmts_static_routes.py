"""
Migration stack: local Postgres + on-disk files. Does NOT replace /api/dgmts-static/send-mail,
which continues to use DGMTS Static Supabase (see email_routes).

- POST /api/dgmts-static/data          — PostgREST-like CRUD (frontend dbClient shim when migrating)
- POST /api/dgmts-static/functions/notify-subscribers
- POST /api/dgmts-static/storage/<bucket>
- GET  /api/dgmts-static/media/<bucket>/<path:object_path>
"""

import re
import uuid
from pathlib import Path

from flask import Blueprint, request, jsonify, send_from_directory, abort

from config import Config
from models.static_db import static_db

dgmts_static_bp = Blueprint('dgmts_static', __name__, url_prefix='/api/dgmts-static')

ALLOWED_TABLES = frozenset({
    'blogs', 'categories', 'news', 'events', 'subscribers', 'subscriber_groups',
    'subscriber_group_members', 'subscriber_newsletter_email_logs', 'email_config',
    'payments', 'payment_portal_users', 'dgmts_contact_persons', 'credentials',
})

ALLOWED_BUCKETS = frozenset({'newsletter-images', 'blog-images', 'event-images'})

IDENT = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def _ident(name: str) -> str:
    if not name or not IDENT.match(name):
        raise ValueError(f'Invalid identifier: {name!r}')
    return name


def _qident(name: str) -> str:
    """Double-quote a PostgreSQL identifier (required for reserved words: user, order, group, …)."""
    n = _ident(name)
    return '"' + n.replace('"', '""') + '"'


def _format_select_columns(cols) -> str:
    if not cols or (isinstance(cols, str) and cols.strip() == '*'):
        return '*'
    if not isinstance(cols, str):
        return '*'
    out = []
    for part in cols.split(','):
        p = part.strip()
        if not p or p == '*':
            continue
        if '(' in p:
            out.append(p)
        else:
            out.append(_qident(p))
    return ', '.join(out) if out else '*'


def _where_clauses(filters, params):
    """Build SQL WHERE fragments; appends to params list in place."""
    parts = []
    if not filters:
        return 'TRUE'
    for f in filters:
        op = f.get('op')
        qcol = _qident(f['col'])
        if op == 'eq':
            val = f.get('val')
            if val is None:
                parts.append(f'{qcol} IS NULL')
            else:
                parts.append(f'{qcol} = %s')
                params.append(val)
        elif op == 'in':
            vals = f.get('vals') or []
            if not vals:
                parts.append('FALSE')
            else:
                ph = ','.join(['%s'] * len(vals))
                parts.append(f'{qcol} IN ({ph})')
                params.extend(vals)
        else:
            raise ValueError(f'Unsupported filter op: {op}')
    return ' AND '.join(parts) if parts else 'TRUE'


def _order_clause(order_list):
    if not order_list:
        return ''
    bits = []
    for o in order_list:
        qcol = _qident(o['col'])
        direction = 'ASC' if o.get('asc', True) else 'DESC'
        bits.append(f'{qcol} {direction}')
    return 'ORDER BY ' + ', '.join(bits)


@dgmts_static_bp.route('/data', methods=['POST', 'OPTIONS'])
def static_data():
    if request.method == 'OPTIONS':
        return '', 200, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
        }

    body = request.get_json(silent=True) or {}
    action = body.get('action')
    table = body.get('table')

    try:
        if not table or table not in ALLOWED_TABLES:
            return jsonify({'data': None, 'error': {'message': f'Table not allowed: {table}'}}), 400

        if action == 'select':
            cols = body.get('columns') or '*'
            if cols != '*':
                if not isinstance(cols, str):
                    return jsonify({'data': None, 'error': {'message': 'Invalid columns'}}), 400
                for part in cols.split(','):
                    part = part.strip()
                    if part == '*' or not part:
                        continue
                    if '(' in part:
                        continue
                    _ident(part)

            select_sql = _format_select_columns(cols) if isinstance(cols, str) else '*'
            filters = body.get('filters') or []
            params = []
            where_sql = _where_clauses(filters, params)
            order_sql = _order_clause(body.get('order') or [])
            limit = body.get('limit')
            offset = body.get('offset') or 0

            q = f'SELECT {select_sql} FROM {_qident(table)} WHERE {where_sql}'
            if order_sql:
                q += ' ' + order_sql
            if limit is not None:
                q += f' LIMIT {int(limit)}'
            if offset:
                q += f' OFFSET {int(offset)}'

            rows = static_db.query(q, params)
            single = body.get('single')
            maybe_single = body.get('maybe_single')

            if single:
                if len(rows) != 1:
                    return jsonify({
                        'data': None,
                        'error': {'message': 'JSON object requested, multiple (or no) rows returned'},
                    }), 406
                return jsonify({'data': rows[0], 'error': None})

            if maybe_single:
                if len(rows) > 1:
                    return jsonify({
                        'data': None,
                        'error': {'message': 'JSON object requested, multiple rows returned'},
                    }), 406
                return jsonify({'data': rows[0] if rows else None, 'error': None})

            return jsonify({'data': rows, 'error': None})

        if action == 'insert':
            rows_in = body.get('rows') or []
            if not rows_in or not isinstance(rows_in, list):
                return jsonify({'data': None, 'error': {'message': 'rows required'}}), 400

            want_returning = bool(body.get('returning', True))
            all_out = []
            for row in rows_in:
                if not isinstance(row, dict):
                    raise ValueError('Each row must be an object')
                key_list = list(row.keys())
                keys_quoted = [_qident(k) for k in key_list]
                vals = [row[k] for k in key_list]
                cols_sql = ', '.join(keys_quoted)
                ph = ', '.join(['%s'] * len(vals))
                q = f'INSERT INTO {_qident(table)} ({cols_sql}) VALUES ({ph})'
                if want_returning:
                    q += ' RETURNING *'
                    all_out.extend(static_db.execute(q, vals, returning=True))
                else:
                    static_db.execute(q, vals, returning=False)

            if want_returning and len(rows_in) == 1:
                return jsonify({'data': all_out[0] if all_out else None, 'error': None})
            if want_returning:
                return jsonify({'data': all_out, 'error': None})
            return jsonify({'data': None, 'error': None})

        if action == 'update':
            patch = body.get('patch') or {}
            filters = body.get('filters') or []
            want_returning = bool(body.get('returning', False))
            if not patch:
                return jsonify({'data': None, 'error': {'message': 'patch required'}}), 400
            if not filters:
                return jsonify({'data': None, 'error': {'message': 'At least one filter is required for update'}}), 400
            params = []
            where_sql = _where_clauses(filters, params)
            sets = []
            for k, v in patch.items():
                sets.append(f'{_qident(k)} = %s')
                params.append(v)
            q = f'UPDATE {_qident(table)} SET {", ".join(sets)} WHERE {where_sql}'
            if want_returning:
                q += ' RETURNING *'
                out = static_db.execute(q, params, returning=True)
                return jsonify({'data': out, 'error': None})
            static_db.execute(q, params, returning=False)
            return jsonify({'data': None, 'error': None})

        if action == 'delete':
            filters = body.get('filters') or []
            if not filters:
                return jsonify({'data': None, 'error': {'message': 'At least one filter is required for delete'}}), 400
            want_returning = bool(body.get('returning', False))
            params = []
            where_sql = _where_clauses(filters, params)
            q = f'DELETE FROM {_qident(table)} WHERE {where_sql}'
            if want_returning:
                q += ' RETURNING *'
                out = static_db.execute(q, params, returning=True)
                return jsonify({'data': out, 'error': None})
            static_db.execute(q, params, returning=False)
            return jsonify({'data': None, 'error': None})

        if action == 'upsert':
            rows_in = body.get('rows') or []
            on_conflict = body.get('on_conflict') or ''
            if not rows_in:
                return jsonify({'data': None, 'error': {'message': 'rows required'}}), 400
            cnames = []
            for c in on_conflict.split(','):
                s = c.strip()
                if s:
                    cnames.append(_ident(s))
            if not cnames:
                return jsonify({'data': None, 'error': {'message': 'on_conflict is required for upsert'}}), 400

            key_list = list(rows_in[0].keys())
            for r in rows_in[1:]:
                if list(r.keys()) != key_list:
                    return jsonify({'data': None, 'error': {'message': 'All upsert rows must share columns'}}), 400

            key_ids = [_ident(k) for k in key_list]
            cols_sql = ', '.join(_qident(k) for k in key_ids)
            flat = []
            ph_groups = []
            for row in rows_in:
                ph_groups.append('(' + ','.join(['%s'] * len(key_list)) + ')')
                flat.extend([row[k] for k in key_list])

            q = f'INSERT INTO {_qident(table)} ({cols_sql}) VALUES {", ".join(ph_groups)}'

            if cnames:
                conflict_sql = ', '.join(_qident(c) for c in cnames)
                cset = set(cnames)
                non_conflict = [k for k in key_ids if k not in cset]
                if not non_conflict:
                    q += f' ON CONFLICT ({conflict_sql}) DO NOTHING'
                else:
                    set_parts = [f'{_qident(k)} = EXCLUDED.{_qident(k)}' for k in non_conflict]
                    q += f' ON CONFLICT ({conflict_sql}) DO UPDATE SET {", ".join(set_parts)}'
            q += ' RETURNING *'

            out = static_db.query(q, flat)
            return jsonify({'data': out, 'error': None})

        return jsonify({'data': None, 'error': {'message': f'Unknown action: {action}'}}), 400

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'data': None, 'error': {'message': str(e)}}), 500


@dgmts_static_bp.route('/functions/notify-subscribers', methods=['POST', 'OPTIONS'])
def notify_subscribers():
    if request.method == 'OPTIONS':
        return '', 200, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization, x-client-info, apikey',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
        }

    data = request.get_json(silent=True) or {}
    blog_title = data.get('blogTitle', '')
    blog_slug = data.get('blogSlug', '')
    blog_excerpt = data.get('blogExcerpt', '')
    blog_author = data.get('blogAuthor', 'Admin')

    try:
        sub_rows = static_db.query(
            "SELECT email FROM subscribers WHERE is_active = TRUE"
        )
        if not sub_rows:
            return jsonify({'message': 'No active subscribers to notify'}), 200

        bcc = [r['email'] for r in sub_rows if r.get('email')]
        link = f"{Config.BLOG_BASE_URL.rstrip('/')}/{blog_slug}"

        # Reuse same Gmail SSL path as services.email_service (Config.EMAIL_USERNAME)
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        smtp_user = Config.EMAIL_USERNAME
        smtp_pass = Config.EMAIL_PASSWORD
        admin_to = Config.ADMIN_EMAIL

        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'📰 New Blog Post: {blog_title}'
        msg['From'] = f'DGMTS Newsletter <{smtp_user}>'
        msg['To'] = admin_to
        if bcc:
            msg['Bcc'] = ', '.join(bcc)

        text_body = f"""
NEW BLOG POST PUBLISHED
========================

Title: {blog_title}
Author: {blog_author}

{blog_excerpt}

Read: {link}
"""
        html_body = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif">
<div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;padding:20px;text-align:center">
<h1>📰 New Blog Post Published</h1></div>
<div style="padding:24px"><p><strong>{blog_title}</strong></p>
<p>By {blog_author}</p><p style="font-style:italic">{blog_excerpt}</p>
<p><a href="{link}">Read full post</a></p></div></body></html>"""

        msg.attach(MIMEText(text_body, 'plain'))
        msg.attach(MIMEText(html_body, 'html'))

        with smtplib.SMTP_SSL(Config.SMTP_SERVER, Config.SMTP_PORT) as s:
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [admin_to] + bcc, msg.as_string())

        return jsonify({'message': f'Notification sent to {len(bcc)} subscribers'}), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'message': str(e), 'error': str(e)}), 500


def _media_root() -> Path:
    root = Path(Config.STATIC_MEDIA_DIR)
    if not root.is_absolute():
        root = Path(__file__).resolve().parent.parent / root
    root.mkdir(parents=True, exist_ok=True)
    return root


@dgmts_static_bp.route('/storage/<bucket>', methods=['POST', 'OPTIONS'])
def storage_upload(bucket):
    if request.method == 'OPTIONS':
        return '', 200, {'Access-Control-Allow-Origin': '*'}

    if bucket not in ALLOWED_BUCKETS:
        return jsonify({'error': 'Invalid bucket'}), 400

    f = request.files.get('file')
    object_path = request.form.get('path') or request.form.get('key')
    if not f:
        return jsonify({'data': None, 'error': {'message': 'file required'}}), 400

    if not object_path:
        ext = Path(f.filename or '').suffix or '.bin'
        object_path = f'{uuid.uuid4().hex}{ext}'

    # prevent path escape
    safe = object_path.replace('\\', '/').lstrip('/')
    if '..' in safe:
        return jsonify({'data': None, 'error': {'message': 'Invalid path'}}), 400

    dest_dir = _media_root() / bucket
    dest_dir.mkdir(parents=True, exist_ok=True)
    full_path = dest_dir / safe
    full_path.parent.mkdir(parents=True, exist_ok=True)
    f.save(str(full_path))

    public = f"{Config.STATIC_APP_PUBLIC_BASE.rstrip('/')}/api/dgmts-static/media/{bucket}/{safe}"
    return jsonify({
        'data': {'path': safe, 'publicUrl': public},
        'error': None,
    })


@dgmts_static_bp.route('/media/<bucket>/<path:object_path>')
def media_get(bucket, object_path):
    if bucket not in ALLOWED_BUCKETS:
        abort(404)
    base = _media_root() / bucket
    if '..' in object_path:
        abort(404)
    return send_from_directory(str(base), object_path, conditional=True)
