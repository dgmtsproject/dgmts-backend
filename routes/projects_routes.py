"""
Projects Directory REST API (fully isolated module).

All endpoints live under /api/projects and talk only to the `projects` schema
through models/projects_db.py. Auth is strict (see projects/ms_auth.py): a valid
company-login bearer token plus a per-person access flag.

Disabling the module is a one-line change in app.py (PROJECTS_MODULE_ENABLED).
"""

import threading
import time
import uuid
from datetime import date, datetime, time as dt_time
from decimal import Decimal

import psycopg2
import requests
from flask import Blueprint, Response, jsonify, request

from config import Config
from models.projects_db import projects_db
from projects import superuser
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
# Temporary super-user sign-in (see projects/superuser.py). Returns 404 unless
# it is configured in .env, so it can be switched off by removing the settings.
# --------------------------------------------------------------------------
@projects_bp.route("/superuser/login", methods=["POST"])
def superuser_login():
    if not superuser.enabled():
        return jsonify({"error": "Not available."}), 404
    ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0]).strip()
    if superuser.too_many_attempts(ip):
        return jsonify({"error": "Too many attempts. Try again in a few minutes."}), 429
    b = _json()
    if not superuser.check_credentials(str(b.get("username") or ""), str(b.get("password") or "")):
        superuser.record_attempt(ip)
        return jsonify({"error": "Wrong username or password."}), 401
    token, ttl = superuser.issue_token()
    return _ok({"token": token, "expires_in": ttl})


# --------------------------------------------------------------------------
# Who am I + what may I do
# --------------------------------------------------------------------------
@projects_bp.route("/me", methods=["GET"])
@access_required()
def me():
    u = request.projects_user
    if not u.get("superuser"):
        _maybe_refresh_directory(request.projects_bearer)
    return _ok({
        "user_id": u["user_id"],
        "email": u["email"],
        "name": u["full_name"] or u["email"],
        "job_title": u["job_title_name"],
        "superuser": bool(u.get("superuser")),
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
# Access management (super user only). Only Dashboard / Projects / Chat can be
# granted here; editing projects and this page belong to the super user alone.
# --------------------------------------------------------------------------
GRANTABLE = ("can_dashboard", "can_projects", "can_chat")


@projects_bp.route("/access", methods=["GET"])
@access_required("is_admin")
def list_access():
    """Employee directory merged with the saved access flags."""
    saved = {r["employee_user_id"]: r for r in projects_db.query("SELECT * FROM user_access")}
    directory = projects_db.query(
        "SELECT user_id, name, email, job_title, department, active, updated_at "
        "FROM employee_directory ORDER BY lower(name)")
    people, seen = [], set()
    for e in directory:
        r = saved.get(e["user_id"]) or {}
        seen.add(e["user_id"])
        people.append({
            "userId": e["user_id"], "name": e["name"] or e["email"] or e["user_id"],
            "email": e["email"] or "", "jobTitle": e["job_title"] or "",
            "department": e["department"] or "", "active": e["active"],
            **{f: bool(r.get(f)) for f in GRANTABLE},
        })
    for uid, r in saved.items():  # people with a saved row who are not in the directory
        if uid in seen:
            continue
        people.append({
            "userId": uid, "name": r.get("name") or r.get("email") or uid, "email": r.get("email") or "",
            "jobTitle": r.get("job_title") or "", "department": "", "active": True,
            **{f: bool(r.get(f)) for f in GRANTABLE},
        })
    people.sort(key=lambda x: (x["name"] or "").lower())
    updated = max((e["updated_at"] for e in directory), default=None)
    return _ok({"people": people, "directoryUpdatedAt": updated, "directoryCount": len(directory)})


def _save_access(changes: list[dict], by: str) -> int:
    n = 0
    with projects_db.cursor() as cur:
        for b in changes:
            user_id = str(b.get("userId") or "").strip()
            if not user_id:
                continue
            flags = {f: bool(b.get(f)) for f in GRANTABLE}
            cur.execute(
                """
                INSERT INTO user_access (employee_user_id, email, name, job_title,
                                         can_dashboard, can_projects, can_chat, can_edit, is_admin,
                                         updated_by, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, FALSE, %s, now())
                ON CONFLICT (employee_user_id) DO UPDATE SET
                    email = EXCLUDED.email, name = EXCLUDED.name, job_title = EXCLUDED.job_title,
                    can_dashboard = EXCLUDED.can_dashboard, can_projects = EXCLUDED.can_projects,
                    can_chat = EXCLUDED.can_chat, can_edit = FALSE, is_admin = FALSE,
                    updated_by = EXCLUDED.updated_by, updated_at = now()
                """,
                [user_id, str(b.get("email") or "").strip().lower() or None,
                 str(b.get("name") or "").strip() or None, str(b.get("jobTitle") or "").strip() or None,
                 flags["can_dashboard"], flags["can_projects"], flags["can_chat"], by],
            )
            n += 1
    return n


@projects_bp.route("/access", methods=["PUT"])
@access_required("is_admin")
def upsert_access():
    """Save several people's flags in one transaction: {"changes": [{userId, ...flags}]}."""
    b = _json()
    changes = b.get("changes")
    if changes is None:  # a single person, for simple callers
        changes = [b]
    if not isinstance(changes, list) or not changes:
        return jsonify({"error": "Nothing to save."}), 400
    saved = _save_access(changes, request.projects_user["email"])
    return _ok({"ok": True, "saved": saved})


# --------------------------------------------------------------------------
# Employee directory refresh: whenever a real company user signs in and the
# cache is old, copy the company employee list into employee_directory. This
# keeps the super user's Access page current without a company token.
# --------------------------------------------------------------------------
_DIRECTORY_MAX_AGE = 24 * 3600
_directory_lock = threading.Lock()
_directory_checked_at = 0.0


def _refresh_directory(bearer: str) -> None:
    try:
        resp = requests.get(
            f"{Config.PROJECTS_MS_API_BASE_URL.rstrip('/')}/employee-list",
            params={"per_page": 1000},
            headers={"Authorization": f"Bearer {bearer}", "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            return
        body = resp.json()
        raw = body if isinstance(body, list) else (body.get("data") or [])
        rows = []
        for e in raw:
            if not isinstance(e, dict):
                continue
            uid = str(e.get("user_id") or e.get("id") or e.get("employee_id") or "").strip()
            email = str(e.get("email") or e.get("mail") or "").strip().lower()
            if uid and email:
                rows.append((uid, str(e.get("name") or e.get("full_name") or "").strip() or None, email,
                             str(e.get("job_title_name") or e.get("job_title") or "").strip() or None,
                             str(e.get("department_name") or "").strip() or None,
                             str(e.get("status", 1)) not in ("0", "false", "False")))
        if not rows:
            return
        with projects_db.cursor() as cur:
            for r in rows:
                cur.execute(
                    """
                    INSERT INTO employee_directory (user_id, name, email, job_title, department, active, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (user_id) DO UPDATE SET name = EXCLUDED.name, email = EXCLUDED.email,
                        job_title = EXCLUDED.job_title, department = EXCLUDED.department,
                        active = EXCLUDED.active, updated_at = now()
                    """, r)
    except Exception:  # noqa: BLE001  (best effort; never affects the request)
        pass


def _maybe_refresh_directory(bearer: str) -> None:
    global _directory_checked_at
    now = time.time()
    with _directory_lock:
        if now - _directory_checked_at < _DIRECTORY_MAX_AGE:
            return
        _directory_checked_at = now
    threading.Thread(target=_refresh_directory, args=(bearer,), daemon=True).start()


# --------------------------------------------------------------------------
# Files attached to a project's notes (any format). Everyone with Projects access
# can list and download; only people who can edit (the super user) can add or
# remove. Files are always served as downloads, never opened in the browser, so an
# uploaded web page or script can't run on our site.
# --------------------------------------------------------------------------
def _project_id(number: str):
    row = projects_db.query_one("SELECT id FROM archive_projects WHERE project_number = %s", [number])
    return row["id"] if row else None


def _clean_filename(name: str) -> str:
    name = (name or "file").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*').strip(" .")
    return name[:200] or "file"


def _file_meta(r: dict) -> dict:
    return {"id": r["id"], "file_name": r["file_name"], "content_type": r["content_type"],
            "size_bytes": r["size_bytes"], "uploaded_by": r["uploaded_by"], "uploaded_at": r["uploaded_at"]}


@projects_bp.route("/files/<number>", methods=["GET"])
@access_required("can_projects")
def list_note_files(number):
    pid = _project_id(number)
    if not pid:
        return jsonify({"error": "Project not found."}), 404
    return _ok(projects_db.query(
        "SELECT id, file_name, content_type, size_bytes, uploaded_by, uploaded_at "
        "FROM project_note_files WHERE project_id = %s ORDER BY uploaded_at DESC", [pid]))


@projects_bp.route("/files/<number>", methods=["POST"])
@access_required("can_edit")
def upload_note_files(number):
    pid = _project_id(number)
    if not pid:
        return jsonify({"error": "Project not found."}), 404
    limit = Config.PROJECTS_MAX_FILE_MB * 1024 * 1024
    uploads = request.files.getlist("file")
    if not uploads:
        return jsonify({"error": "No file received."}), 400
    saved, skipped = [], []
    for f in uploads:
        data = f.read(limit + 1)
        name = _clean_filename(f.filename)
        if not data:
            skipped.append(f"{name} (empty)")
            continue
        if len(data) > limit:
            skipped.append(f"{name} (over {Config.PROJECTS_MAX_FILE_MB} MB)")
            continue
        row = projects_db.execute_one(
            "INSERT INTO project_note_files (project_id, file_name, content_type, size_bytes, data, uploaded_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "RETURNING id, file_name, content_type, size_bytes, uploaded_by, uploaded_at",
            [pid, name, (f.mimetype or None), len(data), psycopg2.Binary(data), request.projects_user["full_name"] or request.projects_user["email"]])
        saved.append(row)
    status = 200 if saved else 400
    return _ok({"saved": saved, "skipped": skipped}, status)


@projects_bp.route("/files/<number>/<file_id>", methods=["GET"])
@access_required("can_projects")
def download_note_file(number, file_id):
    pid = _project_id(number)
    try:
        uuid.UUID(file_id)
    except ValueError:
        pid = None
    row = projects_db.query_one(
        "SELECT file_name, data FROM project_note_files WHERE id = %s AND project_id = %s", [file_id, pid]) if pid else None
    if not row:
        return jsonify({"error": "File not found."}), 404
    from urllib.parse import quote
    return Response(
        bytes(row["data"]),
        content_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(row['file_name'])}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@projects_bp.route("/files/<number>/<file_id>", methods=["DELETE"])
@access_required("can_edit")
def delete_note_file(number, file_id):
    pid = _project_id(number)
    try:
        uuid.UUID(file_id)
    except ValueError:
        pid = None
    if not pid or not projects_db.execute(
            "DELETE FROM project_note_files WHERE id = %s AND project_id = %s", [file_id, pid]):
        return jsonify({"error": "File not found."}), 404
    return _ok({"ok": True})


# --------------------------------------------------------------------------
# Saved chat conversations. Each person sees only their own (owner = their
# company user id; the temporary super user shares the id "superuser").
# --------------------------------------------------------------------------
MAX_MESSAGE_CHARS = 60000


def _owner() -> str:
    return request.projects_user["user_id"]


def _own_chat(chat_id: str):
    try:
        uuid.UUID(chat_id)
    except ValueError:
        return None
    return projects_db.query_one(
        "SELECT id, title, created_at, updated_at FROM chat_conversations WHERE id = %s AND owner_id = %s",
        [chat_id, _owner()])


@projects_bp.route("/chats", methods=["GET"])
@access_required("can_chat")
def list_chats():
    return _ok(projects_db.query(
        "SELECT id, title, updated_at FROM chat_conversations WHERE owner_id = %s "
        "ORDER BY updated_at DESC LIMIT 200", [_owner()]))


@projects_bp.route("/chats", methods=["POST"])
@access_required("can_chat")
def create_chat():
    title = str(_json().get("title") or "").strip()[:80] or "New chat"
    row = projects_db.execute_one(
        "INSERT INTO chat_conversations (owner_id, title) VALUES (%s, %s) RETURNING id, title, updated_at",
        [_owner(), title])
    return _ok(row, 201)


@projects_bp.route("/chats/<chat_id>", methods=["GET"])
@access_required("can_chat")
def get_chat(chat_id):
    chat = _own_chat(chat_id)
    if not chat:
        return jsonify({"error": "Conversation not found."}), 404
    chat["messages"] = projects_db.query(
        "SELECT role, content, created_at FROM chat_messages WHERE conversation_id = %s ORDER BY id", [chat_id])
    return _ok(chat)


@projects_bp.route("/chats/<chat_id>", methods=["PATCH"])
@access_required("can_chat")
def rename_chat(chat_id):
    title = str(_json().get("title") or "").strip()[:80]
    if not title:
        return jsonify({"error": "title is required."}), 400
    if not _own_chat(chat_id):
        return jsonify({"error": "Conversation not found."}), 404
    projects_db.execute("UPDATE chat_conversations SET title = %s WHERE id = %s", [title, chat_id])
    return _ok({"ok": True})


@projects_bp.route("/chats/<chat_id>", methods=["DELETE"])
@access_required("can_chat")
def delete_chat(chat_id):
    if not _own_chat(chat_id):
        return jsonify({"error": "Conversation not found."}), 404
    projects_db.execute("DELETE FROM chat_conversations WHERE id = %s", [chat_id])
    return _ok({"ok": True})


@projects_bp.route("/chats/<chat_id>/messages", methods=["POST"])
@access_required("can_chat")
def append_chat_messages(chat_id):
    """Append finished messages: {"messages": [{"role": "user"|"assistant", "content": "..."}]}."""
    chat = _own_chat(chat_id)
    if not chat:
        return jsonify({"error": "Conversation not found."}), 404
    items = _json().get("messages")
    if not isinstance(items, list) or not items:
        return jsonify({"error": "messages is required."}), 400
    clean = []
    for m in items:
        role, content = m.get("role"), str(m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            clean.append((role, content[:MAX_MESSAGE_CHARS]))
    if not clean:
        return jsonify({"error": "No non-empty messages."}), 400
    with projects_db.cursor() as cur:
        for role, content in clean:
            cur.execute("INSERT INTO chat_messages (conversation_id, role, content) VALUES (%s, %s, %s)",
                        [chat_id, role, content])
        first_user = next((c for r, c in clean if r == "user"), None)
        if first_user and chat["title"] == "New chat":
            cur.execute("UPDATE chat_conversations SET title = %s WHERE id = %s",
                        [" ".join(first_user.split())[:60], chat_id])
        cur.execute("UPDATE chat_conversations SET updated_at = now() WHERE id = %s", [chat_id])
    return _ok({"ok": True, "saved": len(clean)})


# --------------------------------------------------------------------------
# Chatbot pass-through. The browser runs the chat logic (tools, loop) but must
# never hold the Anthropic key: it posts the usual Messages API body here and we
# forward it with the key added server-side. Only the configured model and a
# capped max_tokens are allowed, so the endpoint can't be used as a free proxy.
# --------------------------------------------------------------------------
@projects_bp.route("/chat/v1/messages", methods=["POST"])
@access_required("can_chat")
def chat_messages():
    key = Config.PROJECTS_ANTHROPIC_API_KEY
    if not key:
        return jsonify({"error": "Chat is not configured on the server."}), 500
    body = _json()
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        return jsonify({"error": "messages is required."}), 400
    allowed = {"system", "tools", "messages", "stream", "temperature"}
    payload = {k: v for k, v in body.items() if k in allowed}
    payload["model"] = Config.PROJECTS_CHAT_MODEL
    payload["max_tokens"] = min(int(body.get("max_tokens") or Config.PROJECTS_CHAT_MAX_TOKENS),
                                Config.PROJECTS_CHAT_MAX_TOKENS)
    stream = bool(payload.get("stream"))
    try:
        upstream = requests.post(
            "https://api.anthropic.com/v1/messages",
            json=payload,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            stream=stream,
            timeout=(10, 180),
        )
    except requests.RequestException as e:
        return jsonify({"error": f"Could not reach the AI service: {e}"}), 502
    headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    ctype = upstream.headers.get("content-type", "application/json")
    if stream:
        return Response(upstream.iter_content(chunk_size=None), status=upstream.status_code,
                        content_type=ctype, headers=headers)
    return Response(upstream.content, status=upstream.status_code, content_type=ctype, headers=headers)
