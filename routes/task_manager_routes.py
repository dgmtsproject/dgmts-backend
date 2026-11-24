"""
Task Manager REST API (fully isolated module) at /api/tasks.

The workflow rules live in the SQL functions of the `tasks` schema (tasks_schema.sql);
every route here authenticates the caller, resolves the acting employee id and calls
the matching fn_* function. See dgmts-task-manager/API_CONTRACT.md for the shapes.

Errors are JSON {"code": "...", "message": "..."} with the codes the frontend expects.
"""

from flask import Blueprint, jsonify, request
from psycopg2.extras import Json

from config import Config
from models.tasks_db import tasks_db
from services.task_manager import employees as employee_sync
from services.task_manager import mailer
from services.task_manager.outbox import drain_in_background
from task_manager import auth
from task_manager.errors import error_response, from_db_error

task_manager_bp = Blueprint("task_manager", __name__, url_prefix="/api/tasks")

FILTERS = {"open", "completed", "denied", "all"}


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _text(body: dict, key: str):
    v = body.get(key)
    return None if v is None else str(v)


def _call(sql: str, params: list, notify: bool = False):
    """Runs one workflow function; maps database errors to the JSON error format."""
    try:
        result = tasks_db.call(sql, params)
    except Exception as e:  # noqa: BLE001
        return from_db_error(e)
    if notify:
        drain_in_background()
    return jsonify(result if result is not None else {})


def _filter() -> str:
    value = (request.args.get("filter") or "open").strip().lower()
    return value if value in FILTERS else "open"


def _employee_json(employee_id: str):
    return tasks_db.call(
        "SELECT jsonb_build_object('id', v.id, 'full_name', v.full_name, 'email', v.email, "
        "'job_title', v.job_title, 'department', v.department, 'is_active', v.is_active, "
        "'role', coalesce((SELECT role FROM app_user_roles WHERE employee_id = v.id), 'user')) "
        "FROM v_employees v WHERE v.id = %s",
        [employee_id],
    )


# --------------------------------------------------------------------------
@task_manager_bp.route("/health", methods=["GET"])
def health():
    db_ok, err = True, None
    try:
        tasks_db.query_one("SELECT 1 AS ok")
    except Exception as e:  # noqa: BLE001
        db_ok, err = False, str(e)
    return jsonify({
        "module": "tasks", "status": "ok", "db": db_ok, "error": err,
        "smtp": mailer.smtp_configured(), "session_login": auth.session_enabled(),
    })


# --------------------------------------------------------------------------
# Sign-in
# --------------------------------------------------------------------------
@task_manager_bp.route("/auth/login", methods=["POST"])
def login():
    """Login id + password sign-in (the admin account and admin-created users)."""
    if not auth.session_enabled():
        return error_response("VALIDATION", "Password sign-in is not available right now.", 503)
    ip = auth.client_ip()
    if auth.too_many_attempts(ip):
        return error_response("VALIDATION", "Too many attempts. Try again in a few minutes.", 429)
    b = _body()
    try:
        person = tasks_db.call(
            "SELECT fn_testing_lookup_login(%s, %s)", [str(b.get("login") or ""), str(b.get("password") or "")]
        )
        if person and Config.TASKS_PASSWORD_LOGIN_ADMIN_ONLY:
            row = tasks_db.query_one("SELECT directory_managed FROM employees WHERE id = %s", [person["id"]])
            if row and row["directory_managed"]:
                auth.record_attempt(ip)
                return error_response("VALIDATION", "Use your company account to sign in.", 403)
    except Exception as e:  # noqa: BLE001
        auth.record_attempt(ip)
        return from_db_error(e)
    token, ttl = auth.issue_session_token(person["id"])
    return jsonify({"token": token, "expires_in": ttl, "employee": person})


@task_manager_bp.route("/auth/me", methods=["GET"])
@auth.actor_required
def auth_me():
    row = tasks_db.query_one("SELECT email FROM v_employees WHERE id = %s", [request.task_actor])
    return jsonify({"email": row["email"] if row else None})


@task_manager_bp.route("/employees/me", methods=["GET"])
@auth.actor_required
def employees_me():
    person = _employee_json(request.task_actor)
    if not person:
        return error_response("NOT_IN_DIRECTORY", "Your account isn't in the employee directory. Contact admin.", 403)
    return jsonify(person)


@task_manager_bp.route("/employees", methods=["GET"])
@auth.actor_required
def employees_list():
    return _call("SELECT fn_list_active_employees(%s)", [request.task_actor])


# --------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------
@task_manager_bp.route("/tasks", methods=["POST"])
@auth.actor_required
def create_task():
    b = _body()
    return _call(
        "SELECT fn_create_task(%s, %s, %s::date, %s, %s, %s)",
        [request.task_actor, _text(b, "assigned_to"), _text(b, "due_date"), _text(b, "description"),
         _text(b, "reference"), _text(b, "cc")],
        notify=True,
    )


def _task_action(name: str, fn: str, fields: list[str]):
    """POST /tasks/<id>/<name> -> fn_xxx(actor, task_id, *fields)."""

    def view(task_id):
        b = _body()
        marks = ", ".join(["%s"] * len(fields))
        sql = f"SELECT {fn}(%s, %s::uuid{', ' + marks if fields else ''})"
        return _call(sql, [request.task_actor, task_id] + [_text(b, f) for f in fields], notify=True)

    view.__name__ = f"task_{name.replace('-', '_')}"
    task_manager_bp.add_url_rule(f"/tasks/<task_id>/{name}", view_func=auth.actor_required(view), methods=["POST"])


_task_action("accept", "fn_accept_task", ["note"])
_task_action("deny", "fn_deny_task", ["reason"])
_task_action("forward", "fn_forward_task", ["to_employee", "note"])
_task_action("complete", "fn_complete_task", ["note"])
_task_action("accept-and-complete", "fn_accept_and_complete", ["note"])
_task_action("cancel", "fn_cancel_task", ["reason"])
_task_action("reassign", "fn_reassign_task", ["to_employee", "note"])
_task_action("note", "fn_update_task_note", ["note"])


@task_manager_bp.route("/tasks/mine", methods=["GET"])
@auth.actor_required
def my_tasks():
    return _call("SELECT fn_list_my_tasks(%s, %s)", [request.task_actor, _filter()])


@task_manager_bp.route("/tasks/delegated", methods=["GET"])
@auth.actor_required
def delegated_tasks():
    return _call("SELECT fn_list_delegated_tasks(%s, %s)", [request.task_actor, _filter()])


@task_manager_bp.route("/tasks/counts", methods=["GET"])
@auth.actor_required
def task_counts():
    return _call("SELECT fn_task_counts(%s)", [request.task_actor])


@task_manager_bp.route("/tasks/<task_id>/history", methods=["GET"])
@auth.actor_required
def task_history(task_id):
    return _call("SELECT fn_task_history(%s, %s::uuid)", [request.task_actor, task_id])


# --------------------------------------------------------------------------
# Accept / reject / complete links from emails (no sign-in: the token is the proof)
# --------------------------------------------------------------------------
@task_manager_bp.route("/email-action", methods=["POST"])
def email_action():
    b = _body()
    return _call(
        "SELECT fn_apply_email_action(%s, %s, %s)",
        [_text(b, "token") or "", (_text(b, "action") or "").lower(), _text(b, "reason")],
        notify=True,
    )


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------
@task_manager_bp.route("/admin/tasks", methods=["GET"])
@auth.actor_required
def admin_tasks():
    return _call(
        "SELECT fn_admin_list_tasks(%s, %s, %s)",
        [request.task_actor, (request.args.get("employee") or None), (request.args.get("status") or None)],
    )


@task_manager_bp.route("/admin/notifications", methods=["GET"])
@auth.actor_required
def admin_notifications():
    return _call("SELECT fn_admin_list_notifications(%s)", [request.task_actor])


@task_manager_bp.route("/admin/notifications/<notification_id>/retry", methods=["POST"])
@auth.actor_required
def admin_retry(notification_id):
    return _call("SELECT fn_admin_retry_notification(%s, %s::uuid)", [request.task_actor, notification_id], notify=True)


@task_manager_bp.route("/admin/employees", methods=["GET"])
@auth.actor_required
def admin_employees():
    return _call("SELECT fn_admin_list_employees(%s)", [request.task_actor])


def _require_admin():
    try:
        if not tasks_db.call("SELECT fn_is_admin(%s)", [request.task_actor]):
            return error_response("NOT_ALLOWED", "Admin only", 403)
    except Exception as e:  # noqa: BLE001
        return from_db_error(e)
    return None


@task_manager_bp.route("/admin/employees/sync", methods=["POST"])
@auth.actor_required
def admin_sync():
    denied = _require_admin()
    if denied:
        return denied
    try:
        return jsonify(employee_sync.sync_employees())
    except Exception as e:  # noqa: BLE001
        text = str(e)
        if "VALIDATION" in text:
            return from_db_error(e)
        print(f"[tasks] employee sync failed: {e}")
        return error_response("VALIDATION", "The employee directory could not be reached. Try again later.", 502)


@task_manager_bp.route("/admin/test-users", methods=["POST"])
@auth.actor_required
def admin_create_user():
    b = _body()
    return _call(
        "SELECT fn_admin_create_test_user(%s, %s, %s, %s, %s)",
        [request.task_actor, _text(b, "login_id"), _text(b, "full_name"), _text(b, "email"), _text(b, "password")],
    )
