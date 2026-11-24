"""Employee directory sync (inventory MS directory feed -> tasks.employees)."""

from __future__ import annotations

import requests
from psycopg2.extras import Json

from config import Config
from models.tasks_db import tasks_db


def _rows_from_inventory_db() -> list[dict]:
    """Read-only copy of the inventory module's cached MS directory (same data its API serves)."""
    from models.inventory_db import inventory_db

    return inventory_db.query("SELECT * FROM ms_directory_employees ORDER BY name")


def _rows_from_source() -> list[dict]:
    if not (Config.TASKS_EMPLOYEES_SOURCE_URL or "").strip():
        return _rows_from_inventory_db()
    resp = requests.get(Config.TASKS_EMPLOYEES_SOURCE_URL, headers={"Accept": "application/json"}, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Employee directory returned HTTP {resp.status_code}")
    body = resp.json()
    return body if isinstance(body, list) else (body.get("data") or [])


def map_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            active = int(r.get("status")) == 1
        except (TypeError, ValueError):
            active = False
        eid = r.get("employee_id")
        updated = r.get("updated_at")
        out.append({
            "id": str(r.get("ms_user_id") if r.get("ms_user_id") is not None else "").strip(),
            "full_name": str(r.get("name") or "").strip(),
            "email": str(r.get("email") or "").strip().lower(),
            "job_title": r.get("job_title_name"),
            "department": r.get("department_name"),
            "badge_employee_id": None if eid is None else str(eid),
            "is_active": active,
            "source_updated_at": updated.isoformat() if hasattr(updated, "isoformat") else updated,
        })
    return out


def sync_employees() -> dict:
    """Pulls the directory and upserts it. Returns the counts from fn_upsert_employees."""
    mapped = map_rows(_rows_from_source())
    return tasks_db.call("SELECT fn_upsert_employees(%s::jsonb)", [Json(mapped)])
