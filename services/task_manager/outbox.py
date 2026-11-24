"""Sends queued notification_outbox rows (claim -> send -> mark), with retries handled by the SQL."""

from __future__ import annotations

import threading

from config import Config
from models.tasks_db import tasks_db
from services.task_manager import mailer
from services.task_manager.templates import render_mail

_drain_lock = threading.Lock()


def process_outbox(limit: int = 20) -> int:
    """Sends up to `limit` queued emails. Returns how many were attempted."""
    rows = tasks_db.call_rows("SELECT * FROM fn_claim_outbox(%s)", [limit])
    for row in rows:
        try:
            payload = dict(row.get("payload") or {})
            payload["kind"] = row["kind"]
            mail = render_mail(payload, Config.TASKS_APP_PUBLIC_BASE)
            mailer.send_mail(mail, row["recipient_email"])
            tasks_db.call("SELECT fn_mark_outbox(%s::uuid, true, NULL)", [row["id"]])
        except Exception as e:  # noqa: BLE001
            tasks_db.call("SELECT fn_mark_outbox(%s::uuid, false, %s)", [row["id"], mailer.redact(str(e) or "Send failed")])
    return len(rows)


def drain_in_background() -> None:
    """Called after a task changes so the emails go out right away instead of waiting for the next tick."""

    def run():
        if not _drain_lock.acquire(blocking=False):
            return
        try:
            for _ in range(5):
                if process_outbox() == 0:
                    break
        except Exception as e:  # noqa: BLE001
            print(f"[tasks] outbox drain failed: {e}")
        finally:
            _drain_lock.release()

    threading.Thread(target=run, daemon=True).start()
