"""
Inventory & Purchase Management REST API (fully isolated module).

All endpoints live under /api/inventory and talk only to the `inventory`
schema through models/inventory_db.py. Authorization is enforced here
(replacing Supabase RLS): every route requires a valid MS bearer token, and
admin-only actions additionally require an inventory-admin email.

Nothing in this file is imported by the existing backend; disabling the
module is a one-line change in app.py (guarded by INVENTORY_MODULE_ENABLED).
"""

import hashlib
import hmac
import json
import time

import requests
from flask import Blueprint, request, jsonify

from config import Config
from models.inventory_db import inventory_db
from inventory.ms_auth import (
    ms_auth_required,
    ms_admin_required,
    is_inventory_admin,
    _normalize_email,
)
from services.inventory.email import send_inventory_email

inventory_bp = Blueprint("inventory", __name__, url_prefix="/api/inventory")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _json() -> dict:
    return request.get_json(silent=True) or {}


def _pick(data: dict, allowed: set[str]) -> dict:
    return {k: v for k, v in data.items() if k in allowed}


def _insert(table: str, values: dict, returning: str = "*"):
    cols = list(values.keys())
    placeholders = ", ".join(["%s"] * len(cols))
    col_sql = ", ".join(cols)
    sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) RETURNING {returning}"
    return inventory_db.execute_one(sql, [values[c] for c in cols])


def _update(table: str, values: dict, where_sql: str, where_params: list, returning: str = "*"):
    if not values:
        # nothing to update; just return current row
        sql = f"SELECT {returning} FROM {table} WHERE {where_sql}"
        return inventory_db.query_one(sql, where_params)
    set_sql = ", ".join(f"{c} = %s" for c in values.keys())
    params = list(values.values()) + list(where_params)
    sql = f"UPDATE {table} SET {set_sql} WHERE {where_sql} RETURNING {returning}"
    return inventory_db.execute_one(sql, params)


# --------------------------------------------------------------------------
# Health (no auth) — quick reachability + DB check
# --------------------------------------------------------------------------
@inventory_bp.route("/health", methods=["GET"])
def health():
    db_ok = True
    err = None
    try:
        inventory_db.query_one("SELECT 1 AS ok")
    except Exception as e:  # noqa: BLE001
        db_ok = False
        err = str(e)
    return jsonify({"module": "inventory", "status": "ok", "db": db_ok, "error": err})


# --------------------------------------------------------------------------
# Branches & Departments
# --------------------------------------------------------------------------
@inventory_bp.route("/branches", methods=["GET"])
@ms_auth_required
def list_branches():
    rows = inventory_db.query("SELECT id, name FROM branches ORDER BY name")
    return jsonify(rows)


@inventory_bp.route("/branches", methods=["POST"])
@ms_admin_required
def create_branch():
    name = (_json().get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    row = _insert("branches", {"name": name})
    return jsonify(row), 201


@inventory_bp.route("/departments", methods=["GET"])
@ms_auth_required
def list_departments():
    rows = inventory_db.query("SELECT id, name FROM departments ORDER BY name")
    return jsonify(rows)


@inventory_bp.route("/departments", methods=["POST"])
@ms_admin_required
def create_department():
    name = (_json().get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    row = _insert("departments", {"name": name})
    return jsonify(row), 201


# --------------------------------------------------------------------------
# Users (no Supabase Auth — a user is just a row keyed to the MS directory)
# --------------------------------------------------------------------------
@inventory_bp.route("/users", methods=["GET"])
@ms_auth_required
def list_users():
    query = (request.args.get("query") or "").strip()
    barcode_id = (request.args.get("barcode_id") or "").strip()
    want_count = request.args.get("count") == "1"

    # Exact barcode lookup (badge scan) takes precedence.
    if barcode_id:
        rows = inventory_db.query(
            "SELECT * FROM users WHERE barcode_id = %s", [barcode_id]
        )
        return jsonify(rows)
    try:
        page = max(int(request.args.get("page", "1")), 1)
    except ValueError:
        page = 1
    try:
        page_size = min(max(int(request.args.get("page_size", "1000")), 1), 5000)
    except ValueError:
        page_size = 1000
    offset = (page - 1) * page_size

    where, params = "", []
    if query:
        where = "WHERE email ILIKE %s OR full_name ILIKE %s OR barcode_id ILIKE %s"
        like = f"%{query}%"
        params = [like, like, like]

    rows = inventory_db.query(
        f"SELECT * FROM users {where} ORDER BY full_name LIMIT %s OFFSET %s",
        params + [page_size, offset],
    )
    if not want_count:
        return jsonify(rows)
    total = inventory_db.query_one(f"SELECT COUNT(*) AS n FROM users {where}", params)
    return jsonify({"data": rows, "count": total["n"] if total else len(rows)})


@inventory_bp.route("/users", methods=["POST"])
@ms_admin_required
def create_user():
    data = _json()
    email = (data.get("email") or "").strip()
    full_name = (data.get("full_name") or "").strip()
    barcode_id = (data.get("barcode_id") or "").strip()
    if not email or not full_name or not barcode_id:
        return jsonify({"error": "email, full_name and barcode_id are required"}), 400

    values = {
        "email": email,
        "full_name": full_name,
        "barcode_id": barcode_id,
        "role": (data.get("role") or "employee"),
    }
    if data.get("id"):
        values["id"] = data["id"]
    if data.get("branch_id"):
        values["branch_id"] = data["branch_id"]
    try:
        row = _insert("users", values)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Could not create user: {e}"}), 400
    return jsonify(row), 201


@inventory_bp.route("/users/<user_id>", methods=["PATCH"])
@ms_admin_required
def update_user(user_id):
    values = _pick(_json(), {"email", "full_name", "role", "branch_id", "barcode_id"})
    row = _update("users", values, "id = %s", [user_id])
    if not row:
        return jsonify({"error": "User not found"}), 404
    return jsonify(row)


@inventory_bp.route("/users/<user_id>", methods=["DELETE"])
@ms_admin_required
def delete_user(user_id):
    n = inventory_db.execute("DELETE FROM users WHERE id = %s", [user_id])
    if not n:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"success": True})


# --------------------------------------------------------------------------
# Inventory items + logs
# --------------------------------------------------------------------------
@inventory_bp.route("/inventory-items", methods=["GET"])
@ms_auth_required
def list_inventory_items():
    branch_id = request.args.get("branch_id")
    barcode = request.args.get("barcode_id")
    where, params = [], []
    if branch_id:
        where.append("i.branch_id = %s")
        params.append(branch_id)
    if barcode:
        where.append("i.barcode_id = %s")
        params.append(barcode)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = inventory_db.query(
        f"""
        SELECT i.*, json_build_object('name', b.name) AS branches
        FROM inventory_items i
        LEFT JOIN branches b ON b.id = i.branch_id
        {where_sql}
        ORDER BY i.name
        """,
        params,
    )
    return jsonify(rows)


@inventory_bp.route("/inventory-items", methods=["POST"])
@ms_admin_required
def create_inventory_item():
    data = _json()
    values = _pick(data, {"name", "description", "barcode_id", "branch_id", "quantity", "threshold_value"})
    if not values.get("name") or not values.get("barcode_id"):
        return jsonify({"error": "name and barcode_id are required"}), 400
    try:
        row = _insert("inventory_items", values)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Could not create item: {e}"}), 400
    return jsonify(row), 201


@inventory_bp.route("/inventory-items/<item_id>", methods=["PATCH"])
@ms_auth_required
def update_inventory_item(item_id):
    data = _json()
    # Quantity updates allowed for any authenticated user (employees consume/restock);
    # structural edits require admin.
    structural = {"name", "description", "barcode_id", "branch_id", "threshold_value"}
    incoming = _pick(data, structural | {"quantity"})
    if (set(incoming.keys()) & structural) and not is_inventory_admin(
        request.inventory_user.get("email")
    ):
        return jsonify({"error": "Inventory admin access required to edit item details"}), 403
    row = _update("inventory_items", incoming, "id = %s", [item_id])
    if not row:
        return jsonify({"error": "Item not found"}), 404
    return jsonify(row)


@inventory_bp.route("/inventory-items/<item_id>", methods=["DELETE"])
@ms_admin_required
def delete_inventory_item(item_id):
    n = inventory_db.execute("DELETE FROM inventory_items WHERE id = %s", [item_id])
    if not n:
        return jsonify({"error": "Item not found"}), 404
    return jsonify({"success": True})


@inventory_bp.route("/inventory-logs", methods=["GET"])
@ms_auth_required
def list_inventory_logs():
    item_id = request.args.get("item_id")
    where, params = "", []
    if item_id:
        where = "WHERE item_id = %s"
        params = [item_id]
    rows = inventory_db.query(
        f"SELECT * FROM inventory_logs {where} ORDER BY timestamp DESC LIMIT 1000", params
    )
    return jsonify(rows)


@inventory_bp.route("/inventory-logs", methods=["POST"])
@ms_auth_required
def create_inventory_log():
    data = _json()
    values = _pick(data, {"item_id", "user_id", "action"})
    if not values.get("action"):
        return jsonify({"error": "action is required"}), 400
    row = _insert("inventory_logs", values)
    return jsonify(row), 201


# --------------------------------------------------------------------------
# Purchase requests / orders
# --------------------------------------------------------------------------
_PR_INSERT_COLS = {
    "branch_id", "requested_by", "supervisor_id", "department_id", "quantity",
    "item_description", "notes", "product_name", "status",
}
_PR_UPDATE_COLS = _PR_INSERT_COLS | {
    "approval_date", "order_placed_by", "order_placed_date", "delivery_date",
    "received_by", "approved_rejected_by", "rejection_reason", "po_number",
}


@inventory_bp.route("/purchase-requests", methods=["GET"])
@ms_auth_required
def list_purchase_requests():
    status = request.args.get("status")
    branch_id = request.args.get("branch_id")
    where, params = [], []
    if status:
        where.append("status = %s")
        params.append(status)
    if branch_id:
        where.append("branch_id = %s")
        params.append(branch_id)
    where_sql = ("WHERE " + " AND ".join(f"pr.{w}" for w in where)) if where else ""
    rows = inventory_db.query(
        f"""
        SELECT pr.*,
               json_build_object('name', b.name) AS branches,
               CASE WHEN d.id IS NULL THEN NULL
                    ELSE json_build_object('id', d.id, 'name', d.name) END AS department_info
        FROM purchase_requests pr
        LEFT JOIN branches b ON b.id = pr.branch_id
        LEFT JOIN departments d ON d.id = pr.department_id
        {where_sql}
        ORDER BY pr.request_date DESC
        """,
        params,
    )
    return jsonify(rows)


@inventory_bp.route("/purchase-requests/<po_number>", methods=["GET"])
@ms_auth_required
def get_purchase_request(po_number):
    row = inventory_db.query_one(
        "SELECT * FROM purchase_requests WHERE po_number = %s", [po_number]
    )
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(row)


@inventory_bp.route("/purchase-requests", methods=["POST"])
@ms_auth_required
def create_purchase_request():
    data = _json()
    values = _pick(data, _PR_INSERT_COLS)
    if not values.get("item_description") or values.get("quantity") in (None, ""):
        return jsonify({"error": "item_description and quantity are required"}), 400
    # po_number is generated by the DB trigger.
    row = _insert("purchase_requests", values)
    return jsonify(row), 201


@inventory_bp.route("/purchase-requests/<po_number>", methods=["PATCH"])
@ms_auth_required
def update_purchase_request(po_number):
    values = _pick(_json(), _PR_UPDATE_COLS)
    row = _update("purchase_requests", values, "po_number = %s", [po_number])
    if not row:
        return jsonify({"error": "Purchase request not found"}), 404
    return jsonify(row)


@inventory_bp.route("/purchase-requests/<po_number>", methods=["DELETE"])
@ms_admin_required
def delete_purchase_request(po_number):
    n = inventory_db.execute(
        "DELETE FROM purchase_requests WHERE po_number = %s", [po_number]
    )
    if not n:
        return jsonify({"error": "Purchase request not found"}), 404
    return jsonify({"success": True})


# --------------------------------------------------------------------------
# PO action permissions (Roles tab)
# --------------------------------------------------------------------------
_PO_PERM_COLS = {
    "employee_email", "employee_name", "job_title_display", "action_approve",
    "action_reject", "action_order_placed", "action_delivered", "action_edit",
    "is_purchase_supervisor",
}


@inventory_bp.route("/po-action-permissions", methods=["GET"])
@ms_auth_required
def list_po_action_permissions():
    rows = inventory_db.query("SELECT * FROM inventory_po_action_permissions")
    return jsonify(rows)


@inventory_bp.route("/po-action-permissions", methods=["PUT"])
@ms_admin_required
def upsert_po_action_permission():
    data = _json()
    employee_user_id = (data.get("employee_user_id") or "").strip()
    if not employee_user_id:
        return jsonify({"error": "employee_user_id is required"}), 400
    fields = _pick(data, _PO_PERM_COLS)
    cols = ["employee_user_id"] + list(fields.keys()) + ["updated_at"]
    vals = [employee_user_id] + list(fields.values())
    placeholders = ", ".join(["%s"] * (len(cols) - 1) + ["NOW()"])
    updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in fields.keys()] + ["updated_at = NOW()"])
    sql = f"""
        INSERT INTO inventory_po_action_permissions ({", ".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT (employee_user_id) DO UPDATE SET {updates}
        RETURNING *
    """
    row = inventory_db.execute_one(sql, vals)
    return jsonify(row)


# --------------------------------------------------------------------------
# Per-user dashboard tab permissions (frontend "role permissions" API)
# --------------------------------------------------------------------------
_TAB_PERM_COLS = {
    "employee_email", "employee_name", "job_title_display", "tab_dashboard",
    "tab_purchase_requests", "tab_purchase_orders",
}


@inventory_bp.route("/user-tab-permissions", methods=["GET"])
@ms_auth_required
def list_user_tab_permissions():
    rows = inventory_db.query("SELECT * FROM inventory_user_tab_permissions")
    return jsonify(rows)


@inventory_bp.route("/user-tab-permissions", methods=["PUT"])
@ms_admin_required
def upsert_user_tab_permission():
    data = _json()
    employee_user_id = (data.get("employee_user_id") or "").strip()
    if not employee_user_id:
        return jsonify({"error": "employee_user_id is required"}), 400
    fields = _pick(data, _TAB_PERM_COLS)
    cols = ["employee_user_id"] + list(fields.keys()) + ["updated_at"]
    vals = [employee_user_id] + list(fields.values())
    placeholders = ", ".join(["%s"] * (len(cols) - 1) + ["NOW()"])
    updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in fields.keys()] + ["updated_at = NOW()"])
    sql = f"""
        INSERT INTO inventory_user_tab_permissions ({", ".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT (employee_user_id) DO UPDATE SET {updates}
        RETURNING *
    """
    row = inventory_db.execute_one(sql, vals)
    return jsonify(row)


# --------------------------------------------------------------------------
# Purchase supervisor eligible job titles
# --------------------------------------------------------------------------
@inventory_bp.route("/supervisor-titles", methods=["GET"])
@ms_auth_required
def list_supervisor_titles():
    rows = inventory_db.query(
        "SELECT * FROM purchase_supervisor_job_titles ORDER BY job_title_display"
    )
    return jsonify(rows)


@inventory_bp.route("/supervisor-titles", methods=["POST"])
@ms_admin_required
def create_supervisor_title():
    data = _json()
    display = (data.get("job_title_display") or "").strip()
    if not display:
        return jsonify({"error": "job_title_display is required"}), 400
    normalized = (data.get("job_title_normalized") or display).strip().lower()
    normalized = " ".join(normalized.split())
    sql = """
        INSERT INTO purchase_supervisor_job_titles (job_title_normalized, job_title_display, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (job_title_normalized)
        DO UPDATE SET job_title_display = EXCLUDED.job_title_display, updated_at = NOW()
        RETURNING *
    """
    row = inventory_db.execute_one(sql, [normalized, display])
    return jsonify(row), 201


@inventory_bp.route("/supervisor-titles/<title_id>", methods=["DELETE"])
@ms_admin_required
def delete_supervisor_title(title_id):
    n = inventory_db.execute(
        "DELETE FROM purchase_supervisor_job_titles WHERE id = %s", [title_id]
    )
    if not n:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"success": True})


# --------------------------------------------------------------------------
# MS directory cache (employees / departments / meta) + sync
# --------------------------------------------------------------------------
@inventory_bp.route("/ms-directory/employees", methods=["GET"])
@ms_auth_required
def ms_directory_employees():
    q = (request.args.get("q") or "").strip().replace(",", " ")
    email_normalized = (request.args.get("email_normalized") or "").strip().lower()
    ms_user_id = (request.args.get("ms_user_id") or "").strip()

    if ms_user_id.isdigit():
        rows = inventory_db.query(
            "SELECT * FROM ms_directory_employees WHERE ms_user_id = %s", [int(ms_user_id)]
        )
        return jsonify(rows)
    if email_normalized:
        rows = inventory_db.query(
            "SELECT * FROM ms_directory_employees WHERE email_normalized = %s ORDER BY name",
            [email_normalized],
        )
        return jsonify(rows)

    where, params = "", []
    if q:
        where = "WHERE name ILIKE %s OR email ILIKE %s"
        like = f"%{q}%"
        params = [like, like]
    rows = inventory_db.query(
        f"SELECT * FROM ms_directory_employees {where} ORDER BY name", params
    )
    return jsonify(rows)


@inventory_bp.route("/ms-directory/departments", methods=["GET"])
@ms_auth_required
def ms_directory_departments():
    rows = inventory_db.query(
        "SELECT ms_dept_id, name FROM ms_directory_departments ORDER BY name"
    )
    return jsonify(rows)


@inventory_bp.route("/ms-directory/meta", methods=["GET"])
@ms_auth_required
def ms_directory_meta():
    row = inventory_db.query_one("SELECT * FROM ms_directory_meta WHERE id = 1")
    return jsonify(row or {})


def _ms_get(path: str, bearer: str):
    url = f"{Config.INVENTORY_MS_API_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {bearer}", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _as_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return v
    return []


@inventory_bp.route("/ms-directory/sync", methods=["POST"])
@ms_auth_required
def ms_directory_sync():
    bearer = request.inventory_bearer or Config.INVENTORY_MS_FALLBACK_BEARER
    if not bearer:
        return jsonify({"error": "No bearer available for MS sync"}), 400

    synced = {"employees": 0, "departments": 0}
    try:
        employees = _as_list(_ms_get("employee-list", bearer))
        departments = _as_list(_ms_get("department-list", bearer))
    except requests.RequestException as e:
        return jsonify({"error": f"MS API error: {e}"}), 502

    for d in departments:
        dept_id = d.get("id") or d.get("department_id") or d.get("dept_id")
        name = d.get("name") or d.get("department_name") or ""
        if dept_id is None:
            continue
        inventory_db.execute(
            """
            INSERT INTO ms_directory_departments (ms_dept_id, name, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (ms_dept_id) DO UPDATE
              SET name = EXCLUDED.name, updated_at = NOW()
            """,
            [int(dept_id), name],
        )
        synced["departments"] += 1

    for e in employees:
        uid = e.get("user_id") or e.get("id")
        if uid is None:
            continue
        email = e.get("email") or e.get("mail") or ""
        inventory_db.execute(
            """
            INSERT INTO ms_directory_employees
              (ms_user_id, employee_id, name, email, email_normalized, phone,
               job_title_name, department_name, status, image, raw, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (ms_user_id) DO UPDATE SET
              employee_id = EXCLUDED.employee_id,
              name = EXCLUDED.name,
              email = EXCLUDED.email,
              email_normalized = EXCLUDED.email_normalized,
              phone = EXCLUDED.phone,
              job_title_name = EXCLUDED.job_title_name,
              department_name = EXCLUDED.department_name,
              status = EXCLUDED.status,
              image = EXCLUDED.image,
              raw = EXCLUDED.raw,
              updated_at = NOW()
            """,
            [
                int(uid),
                str(e.get("employee_id")) if e.get("employee_id") is not None else None,
                str(e.get("name") or ""),
                email,
                _normalize_email(email),
                e.get("phone"),
                e.get("job_title_name") or e.get("jobTitleName"),
                e.get("department_name"),
                int(e["status"]) if str(e.get("status", "")).strip().isdigit() else None,
                e.get("image"),
                json.dumps(e),
            ],
        )
        synced["employees"] += 1

    inventory_db.execute(
        """
        UPDATE ms_directory_meta
           SET employees_synced_at = NOW(), departments_synced_at = NOW()
         WHERE id = 1
        """
    )
    return jsonify({"success": True, "synced": synced})


# --------------------------------------------------------------------------
# PO email-action token helpers + email send
# --------------------------------------------------------------------------
def _po_action_secret() -> str:
    return Config.INVENTORY_PO_EMAIL_ACTION_SECRET or Config.SECRET_KEY


def make_po_action_token(po_number: str, action: str, expires_in: int = 7 * 86400) -> str:
    exp = int(time.time()) + expires_in
    msg = f"{po_number}:{action}:{exp}".encode()
    sig = hmac.new(_po_action_secret().encode(), msg, hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_po_action_token(po_number: str, action: str, token: str) -> bool:
    try:
        exp_str, sig = token.split(".", 1)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        return False
    if exp < int(time.time()):
        return False
    msg = f"{po_number}:{action}:{exp}".encode()
    expected = hmac.new(_po_action_secret().encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


@inventory_bp.route("/purchase-requests/<po_number>/action-token", methods=["GET"])
@ms_auth_required
def get_po_action_token(po_number):
    action = (request.args.get("action") or "").strip()
    if action not in {"approve", "reject", "order_placed", "delivered"}:
        return jsonify({"error": "invalid action"}), 400
    return jsonify({"token": make_po_action_token(po_number, action)})


@inventory_bp.route("/email/send", methods=["POST"])
@ms_auth_required
def send_email_endpoint():
    data = _json()
    to = data.get("to")
    subject = (data.get("subject") or "").strip()
    html = data.get("html") or data.get("body") or ""
    if not to or not subject or not html:
        return jsonify({"error": "to, subject and html are required"}), 400
    ok, err = send_inventory_email(to, subject, html)
    if not ok:
        return jsonify({"error": err}), 502
    return jsonify({"success": True})
