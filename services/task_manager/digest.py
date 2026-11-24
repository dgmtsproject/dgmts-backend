"""Daily 8am (company time) digest of tasks due tomorrow and overdue."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from psycopg2.extras import Json

from config import Config
from models.tasks_db import tasks_db


def run_due_digest(now: datetime | None = None) -> dict:
    zone = ZoneInfo(Config.TASKS_COMPANY_TIMEZONE)
    local = (now or datetime.now(zone)).astimezone(zone)
    if local.hour != 8:
        return {"skipped": "not-8am"}
    day = local.date().isoformat()
    if not tasks_db.call("SELECT fn_try_start_reminder_run(%s::date)", [day]):
        return {"skipped": "already-ran"}
    rows = tasks_db.call("SELECT fn_digest_candidates(%s::date)", [day]) or []
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["assignee_email"], []).append(row)
    for email, tasks in grouped.items():
        if not tasks:
            continue
        payload = {"kind": "due_digest", "assignee_name": tasks[0]["assignee_name"], "tasks": tasks}
        tasks_db.call("SELECT fn_enqueue_digest(%s, %s::jsonb)", [email, Json(payload)])
    return {"queued": len(grouped)}
