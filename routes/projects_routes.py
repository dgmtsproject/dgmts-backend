"""
Projects Directory REST API (fully isolated module).

All endpoints live under /api/projects and talk only to the `projects` schema
through models/projects_db.py. Auth is strict (see projects/ms_auth.py): a valid
company-login bearer token plus a per-person access flag.

Disabling the module is a one-line change in app.py (PROJECTS_MODULE_ENABLED).
"""

import uuid
from datetime import date, datetime, time as dt_time
from decimal import Decimal

import requests
from flask import Blueprint, jsonify, request

from config import Config
from models.projects_db import projects_db
from projects.ms_auth import FLAGS, access_required, is_admin_email

projects_bp = Blueprint("projects", __name__, url_prefix="/api/projects")

# Columns the app lets staff edit inline (same list the frontend offers).
EDITABLE = {
    "project_name", "client", "year", "dgmts_project_manager", "poc_name", "poc_email",
    "poc_phone", "city", "state", "service_type", "project_summary", "notes",
    "notes_updated_at", "project_writeup", "project_writeup_updated_at",
}

LIST_COLUMNS = """
    p.project_number, p.project_name, p.client, p.city, p.county, p.state, p.year,
    p.is_verified, p.data_source, p.service_type, p.service_sector, p.department,
    p.branch_office, p.attachment_count, p.appears_in_zips,
    p.dgmts_project_manager, p.project_manager_1, p.poc_name, p.poc_email, p.poc_phone,
    p.project_summary, p.tasks_performed,
    p.notes, p.notes_updated_at, p.project_writeup, p.project_writeup_updated_at
"""


def _jsonable(obj):
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (datetime, date, dt_time)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    return str(obj)


def _ok(data, status=200):
    return jsonify(_jsonable(data)), status


def _json() -> dict:
    return request.get_json(silent=True) or {}


# --------------------------------------------------------------------------
# Health (no auth)
# --------------------------------------------------------------------------
@projects_bp.route("/health", methods=["GET"])
def health():
    db_ok, err = True, None
    try:
        projects_db.query_one("SELECT 1 AS ok")
    except Exception as e:  # noqa: BLE001
        db_ok, err = False, str(e)
    return jsonify({"module": "projects", "status": "ok", "db": db_ok, "error": err})


# --------------------------------------------------------------------------
# Who am I + what may I do
# --------------------------------------------------------------------------
@projects_bp.route("/me", methods=["GET"])
@access_required()
def me():
    u = request.projects_user
    return _ok({
        "user_id": u["user_id"],
        "email": u["email"],
        "name": u["full_name"] or u["email"],
        "job_title": u["job_title_name"],
        "access": request.projects_access,
    })


# --------------------------------------------------------------------------
# Dashboard + Projects table + project detail
# --------------------------------------------------------------------------
@projects_bp.route("/stats", methods=["GET"])
@access_required("can_dashboard")
def stats_rows():
    rows = projects_db.query(
        "SELECT project_number, year, state, service_sector, attachment_count, data_source "
        "FROM archive_projects"
    )
    return _ok(rows)


@projects_bp.route("/projects", methods=["GET"])
@access_required("can_projects")
def list_projects():
    rows = projects_db.query(
        f"""
        SELECT {LIST_COLUMNS},
               COALESCE((SELECT json_agg(json_build_object('name', c.name, 'phone', c.phone, 'email', c.email))
                         FROM archive_project_pocs c WHERE c.project_id = p.id), '[]'::json)
                 AS archive_project_pocs
        FROM archive_projects p
        ORDER BY p.is_verified DESC, p.project_number ASC
        """
    )
    return _ok(rows)


@projects_bp.route("/projects/<path:number>", methods=["GET"])
@access_required("can_projects")
def get_project(number):
    p = projects_db.query_one("SELECT * FROM archive_projects WHERE project_number = %s", [number])
    if not p:
        return jsonify({"error": "Project not found."}), 404
    pid = p["id"]
    p["archive_project_pocs"] = projects_db.query(
        "SELECT * FROM archive_project_pocs WHERE project_id = %s", [pid])
    p["archive_project_team"] = projects_db.query(
        "SELECT * FROM archive_project_team WHERE project_id = %s", [pid])
    p["archive_project_vendors"] = projects_db.query(
        "SELECT name FROM archive_project_vendors WHERE project_id = %s", [pid])
    p["archive_project_attachments"] = projects_db.query(
        "SELECT relative_path, file_name, file_ext FROM archive_project_attachments "
        "WHERE project_id = %s", [pid])
    p["archive_project_source_folders"] = projects_db.query(
        "SELECT year, folder_path FROM archive_project_source_folders WHERE project_id = %s", [pid])
    p["archive_project_invoices"] = projects_db.query(
        "SELECT invoice_number, invoice_date, amount, description FROM archive_project_invoices "
        "WHERE project_id = %s", [pid])
    p["archive_project_service_types"] = [
        {"archive_service_types": {"name": r["name"]}}
        for r in projects_db.query(
            "SELECT st.name FROM archive_project_service_types pst "
            "JOIN archive_service_types st ON st.id = pst.service_type_id "
            "WHERE pst.project_id = %s", [pid])
    ]
    return _ok(p)


@projects_bp.route("/projects/<path:number>", methods=["PATCH"])
@access_required("can_edit")
def update_project(number):
    data = _json()
    values = {k: v for k, v in data.items() if k in EDITABLE}
    if not values:
        return jsonify({"error": "Nothing to update."}), 400
    for k, v in list(values.items()):
        if v == "":
            values[k] = None
    set_sql = ", ".join(f"{c} = %s" for c in values)
    row = projects_db.execute_one(
        f"UPDATE archive_projects SET {set_sql} WHERE project_number = %s RETURNING project_number",
        list(values.values()) + [number],
    )
    if not row:
        return jsonify({"error": "Project not found."}), 404
    return _ok({"ok": True})


# --------------------------------------------------------------------------
# Chatbot dataset (the chat runs in the Next.js server, which forwards the
# user's token). One bulk read of what the lookup tools need.
# --------------------------------------------------------------------------
@projects_bp.route("/chat-data", methods=["GET"])
@access_required("can_chat")
def chat_data():
    projects = projects_db.query(
        """
        SELECT id, project_number, project_name, client, client_aka, year, state, city, county,
               service_sector, service_type, dgmts_project_manager, project_manager_1,
               project_manager_2, project_summary, tasks_performed, total_invoiced,
               payments_received, balance_due, sharepoint_file_count, has_sharepoint_extraction,
               invoice_range_end, dfr_range_end, data_range_end, sharepoint_last_modified, notes,
               jsonb_build_object('key_dates', enriched->'key_dates') AS enriched
        FROM archive_projects
        """
    )
    return _ok({
        "projects": projects,
        "invoices": projects_db.query(
            "SELECT project_id, invoice_number, invoice_date, amount, description "
            "FROM archive_project_invoices"),
        "pocs": projects_db.query(
            "SELECT project_id, name, title, company, email, phone, role FROM archive_project_pocs"),
        "team": projects_db.query(
            "SELECT project_id, name, role FROM archive_project_team"),
        "vendors": projects_db.query(
            "SELECT project_id, name FROM archive_project_vendors"),
    })


# --------------------------------------------------------------------------
# Access management (admins only)
# --------------------------------------------------------------------------
@projects_bp.route("/access", methods=["GET"])
@access_required("is_admin")
def list_access():
    """Every company employee merged with their saved access flags."""
    employees = []
    try:
        resp = requests.get(
            f"{Config.PROJECTS_MS_API_BASE_URL.rstrip('/')}/employee-list",
            params={"per_page": 1000},
            headers={"Authorization": f"Bearer {request.projects_bearer}", "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code == 200:
            body = resp.json()
            raw = body if isinstance(body, list) else (body.get("data") or [])
            for e in raw:
                if not isinstance(e, dict):
                    continue
                uid = str(e.get("user_id") or e.get("id") or e.get("employee_id") or "").strip()
                email = str(e.get("email") or e.get("mail") or "").strip().lower()
                if uid and email:
                    employees.append({
                        "userId": uid,
                        "email": email,
                        "name": str(e.get("name") or e.get("full_name") or e.get("employee_name") or "").strip(),
                        "jobTitle": str(e.get("job_title_name") or e.get("job_title") or "").strip(),
                    })
    except (requests.RequestException, ValueError):
        pass

    saved = {
        r["employee_user_id"]: r
        for r in projects_db.query("SELECT * FROM user_access")
    }
    people = []
    for e in employees:
        r = saved.get(e["userId"]) or {}
        people.append({
            **e,
            "bootstrapAdmin": is_admin_email(e["email"]),
            **{f: bool(r.get(f)) for f in FLAGS},
        })
    people.sort(key=lambda x: x["name"].lower())
    return _ok({"people": people, "loadedEmployees": len(employees)})


@projects_bp.route("/access", methods=["PUT"])
@access_required("is_admin")
def upsert_access():
    b = _json()
    user_id = str(b.get("userId") or "").strip()
    if not user_id:
        return jsonify({"error": "userId is required."}), 400
    flags = {f: bool(b.get(f)) for f in FLAGS}
    projects_db.execute(
        """
        INSERT INTO user_access (employee_user_id, email, name, job_title,
                                 can_dashboard, can_projects, can_chat, can_edit, is_admin,
                                 updated_by, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (employee_user_id) DO UPDATE SET
            email = EXCLUDED.email, name = EXCLUDED.name, job_title = EXCLUDED.job_title,
            can_dashboard = EXCLUDED.can_dashboard, can_projects = EXCLUDED.can_projects,
            can_chat = EXCLUDED.can_chat, can_edit = EXCLUDED.can_edit,
            is_admin = EXCLUDED.is_admin, updated_by = EXCLUDED.updated_by, updated_at = now()
        """,
        [
            user_id,
            str(b.get("email") or "").strip().lower() or None,
            str(b.get("name") or "").strip() or None,
            str(b.get("jobTitle") or "").strip() or None,
            flags["can_dashboard"], flags["can_projects"], flags["can_chat"],
            flags["can_edit"], flags["is_admin"],
            request.projects_user["email"],
        ],
    )
    return _ok({"ok": True})
