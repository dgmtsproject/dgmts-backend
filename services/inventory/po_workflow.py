"""
Purchase-order workflow logic (emails, signed action links, employee lookup).

Ported from the Next.js route handlers / libs of the Inventory frontend
(po-workflow-notifications.ts, purchase-request-email-action.ts,
po-action-token.ts) so the frontend can be a plain static site.

Compatibility notes
-------------------
* Action-link tokens use the SAME format and secret as the Next.js app
  (`base64url(json).base64url(hmac-sha256)`), so links already sitting in
  people's inboxes keep working. The secret is INVENTORY_PO_EMAIL_ACTION_SECRET
  and MUST equal the PO_EMAIL_ACTION_SECRET value used by the Next.js app.
* Email subjects/bodies are copied from the Next.js implementation.
* Nothing here is imported by the rest of the backend; routes live in
  routes/inventory_workflow_routes.py and are attached defensively.
"""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import quote, urlencode

import requests
from flask import g, has_request_context

from config import Config
from models.inventory_db import inventory_db
from services.inventory.email import send_inventory_email

PO_EMAIL_TOKEN_TTL_SEC = 7 * 24 * 60 * 60

DEFAULT_ACCOUNTS_EMAILS = [
    "iaziz@dullesgeotechnical.com",
    "qhaider@dullesgeotechnical.com",
    "afarhan@dullesgeotechnical.com",
    "dgmts.project@gmail.com",
]

# The Next.js app used the same recipients for low-stock alerts.
LOW_STOCK_RECIPIENTS = "admin@example.com"


class WorkflowError(Exception):
    """Raised for expected failures; message is safe to show to the caller."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def escape_html(value) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def accounts_notify_emails() -> list[str]:
    raw = (Config.ORDER_PLACED_NOTIFY_EMAILS or "").strip()
    if raw:
        return [s.strip() for s in raw.split(",") if s.strip()]
    return list(DEFAULT_ACCOUNTS_EMAILS)


def api_public_base() -> str:
    """Public origin of THIS backend (used for emailed action links)."""
    return (Config.INVENTORY_API_PUBLIC_BASE or "").rstrip("/")


def ui_public_base() -> str:
    """Public origin of the Inventory frontend (where users land afterwards)."""
    return (Config.INVENTORY_APP_PUBLIC_BASE or "").rstrip("/")


def email_action_url(token: str) -> str:
    return f"{api_public_base()}/api/inventory/po/email-action?token={quote(token, safe='')}"


# --------------------------------------------------------------------------
# Signed action tokens (same format as the Next.js app)
# --------------------------------------------------------------------------
def sign_po_email_action_token(payload: dict) -> str:
    secret = Config.INVENTORY_PO_EMAIL_ACTION_SECRET
    if not secret or len(secret) < 16:
        raise WorkflowError(
            "INVENTORY_PO_EMAIL_ACTION_SECRET must be set (min 16 characters) for email actions."
        )
    payload_part = _b64url(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    sig = _b64url(hmac.new(secret.encode(), payload_part.encode(), hashlib.sha256).digest())
    return f"{payload_part}.{sig}"


def verify_po_email_action_token(token: str | None) -> dict | None:
    secret = Config.INVENTORY_PO_EMAIL_ACTION_SECRET
    if not secret or not token:
        return None
    i = token.rfind(".")
    if i <= 0:
        return None
    payload_part, sig = token[:i], token[i + 1:]
    expected = _b64url(hmac.new(secret.encode(), payload_part.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig.encode(), expected.encode()):
        return None
    try:
        data = json.loads(_b64url_decode(payload_part).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    po_number = data.get("poNumber")
    exp = data.get("exp")
    if not po_number or isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return None
    if time.time() > exp:
        return None

    action = data.get("action")
    if action == "order_placed":
        return {"poNumber": str(po_number), "action": "order_placed", "exp": exp}
    if action in ("approve", "reject"):
        if not data.get("supervisorId"):
            return None
        return {
            "poNumber": str(po_number),
            "action": action,
            "supervisorId": str(data["supervisorId"]),
            "exp": exp,
        }
    return None


# --------------------------------------------------------------------------
# Redirect targets (UI pages live on the static frontend)
# --------------------------------------------------------------------------
def email_reject_form_url(token: str) -> str:
    return f"{ui_public_base()}/purchase-request/email-reject?token={quote(token, safe='')}"


def email_result_url(status: str, action: str | None = None,
                     po_number: str | None = None, message: str | None = None) -> str:
    qs = [("status", status)]
    if action:
        qs.append(("action", action))
    if po_number:
        qs.append(("po", po_number))
    if message:
        qs.append(("message", message))
    return f"{ui_public_base()}/purchase-request/email-result?{urlencode(qs)}"


def sso_entry_url_for_purchase_orders(email_action_done: str | None = None,
                                      po_number: str | None = None,
                                      note: str | None = None) -> str:
    target = []
    if email_action_done:
        target.append(("emailActionDone", email_action_done))
    if po_number:
        target.append(("po", po_number))
    if note:
        target.append(("emailNote", note))
    target_qs = urlencode(target)
    next_path = "/dashboard/purchase-orders" + (f"?{target_qs}" if target_qs else "")
    return f"{ui_public_base()}/?{urlencode({'next': next_path})}"


# --------------------------------------------------------------------------
# Data access
# --------------------------------------------------------------------------
def load_po_row(po_number: str) -> dict:
    row = inventory_db.query_one(
        "SELECT * FROM purchase_requests WHERE po_number = %s", [po_number]
    )
    if not row:
        raise WorkflowError("Purchase request not found.")
    return row


def _live_ms_employee_email(user_id: str, bearer: str | None) -> str:
    """Last resort: ask the Microsoft directory API (cache miss)."""
    token = (bearer or "").strip() or (Config.INVENTORY_MS_FALLBACK_BEARER or "")
    if not token:
        return ""
    try:
        resp = requests.get(
            f"{Config.INVENTORY_MS_API_BASE_URL.rstrip('/')}/employee-list",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=20,
        )
        if resp.status_code != 200:
            return ""
        payload = resp.json()
    except (requests.RequestException, ValueError):
        return ""
    rows = payload if isinstance(payload, list) else (payload or {}).get("data") or []
    for e in rows:
        if isinstance(e, dict) and str(e.get("id") or e.get("user_id") or "") == user_id:
            return str(e.get("email") or "").strip()
    return ""


def find_employee_email(user_id, bearer: str | None = None) -> str:
    if user_id is None or str(user_id).strip() == "":
        return ""
    sid = str(user_id).strip()
    row = inventory_db.query_one(
        "SELECT email FROM ms_directory_employees WHERE ms_user_id::text = %s", [sid]
    )
    if row and str(row.get("email") or "").strip():
        return str(row["email"]).strip()
    row = inventory_db.query_one("SELECT email FROM users WHERE id::text = %s", [sid])
    if row and str(row.get("email") or "").strip():
        return str(row["email"]).strip()
    return _live_ms_employee_email(sid, bearer)


def resolve_emails(row: dict, bearer: str | None = None) -> tuple[str, str]:
    return (
        find_employee_email(row.get("requested_by"), bearer),
        find_employee_email(row.get("supervisor_id"), bearer),
    )


# --------------------------------------------------------------------------
# Email sending
# --------------------------------------------------------------------------
def post_send_email(to: list[str], subject: str, html: str) -> None:
    # Dry run (?dry_run=1): collect instead of sending, so recipients/content
    # can be checked safely against live data.
    if has_request_context():
        sink = g.get("po_dry_run_sink")
        if sink is not None:
            sink.append({"to": to, "subject": subject, "html": html})
            return
    ok, err = send_inventory_email(to, subject, html)
    if not ok:
        raise WorkflowError(err or "Email send failed.")


_FOOTER_HTML = (
    '<hr /><p style="color: #666; font-size: 12px;">'
    "Sent from the DGMTS Purchase Orders workflow.</p>"
)


def send_to_recipients(to: list[str], subject: str, message: str) -> None:
    seen, deduped = set(), []
    for e in to:
        e = (e or "").strip()
        if e and e not in seen:
            seen.add(e)
            deduped.append(e)
    if not deduped:
        raise WorkflowError("No recipient email addresses resolved.")
    html_body = (
        f"<p>{escape_html(message).replace(chr(10), '<br/>')}</p>\n"
        f"    {_FOOTER_HTML}"
    )
    post_send_email(deduped, subject, html_body)


def _qty(row: dict) -> str:
    return str(row["quantity"]) if row.get("quantity") is not None else "—"


def _item(row: dict) -> str:
    return row.get("item_description") or "-"


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------
def notify_approved_to_requester(po_number: str, bearer: str | None = None) -> None:
    row = load_po_row(po_number)
    requester, _ = resolve_emails(row, bearer)
    if not requester:
        raise WorkflowError("Could not resolve requester email from employee records.")
    message = "\n".join([
        "Your purchase request was approved.",
        f"PO Number: {row['po_number']}",
        f"Quantity: {_qty(row)}",
        f"Item: {_item(row)}",
        f"Status: {row.get('status') or 'Approved'}",
    ])
    send_to_recipients([requester], f"Purchase request approved: {row['po_number']}", message)


def notify_rejected_to_requester(po_number: str, bearer: str | None = None) -> None:
    row = load_po_row(po_number)
    requester, _ = resolve_emails(row, bearer)
    if not requester:
        raise WorkflowError("Could not resolve requester email from employee records.")
    reason = (row.get("rejection_reason") or "").strip()
    lines = [
        "Your purchase request was rejected.",
        f"PO Number: {row['po_number']}",
        f"Quantity: {_qty(row)}",
        f"Item: {_item(row)}",
        f"Status: {row.get('status') or 'Rejected'}",
    ]
    if reason:
        lines.append(f"Reason: {reason}")
    send_to_recipients([requester], f"Purchase request rejected: {row['po_number']}",
                       "\n".join(lines))


def _notify_requester_and_supervisor(po_number: str, bearer, headline: str,
                                     default_status: str, subject_prefix: str) -> None:
    row = load_po_row(po_number)
    requester, supervisor = resolve_emails(row, bearer)
    to = [e for e in (requester, supervisor) if e]
    if not to:
        raise WorkflowError(
            "Could not resolve requester or supervisor email from employee records."
        )
    message = "\n".join([
        headline,
        f"PO Number: {row['po_number']}",
        f"Quantity: {_qty(row)}",
        f"Item: {_item(row)}",
        f"Status: {row.get('status') or default_status}",
    ])
    send_to_recipients(to, f"{subject_prefix}: {row['po_number']}", message)


def notify_order_placed(po_number: str, bearer: str | None = None) -> None:
    _notify_requester_and_supervisor(
        po_number, bearer, "The purchase order has been marked as order placed.",
        "Order Placed", "Order placed")


def notify_delivered(po_number: str, bearer: str | None = None) -> None:
    _notify_requester_and_supervisor(
        po_number, bearer, "The purchase order has been marked as delivered.",
        "Delivered", "Delivered")


def notify_po_edited(po_number: str, editor_user_id: str, bearer: str | None = None) -> None:
    row = load_po_row(po_number)
    supervisor_id = str(row.get("supervisor_id") or "").strip()
    editor_id = str(editor_user_id or "").strip()
    if supervisor_id and editor_id and supervisor_id == editor_id:
        return
    requester, supervisor = resolve_emails(row, bearer)
    to = [e for e in (requester, supervisor) if e]
    if not to:
        return
    message = "\n".join([
        f"Purchase order {row['po_number']} was updated.",
        f"Item: {_item(row)}",
        f"Status: {row.get('status') or '-'}",
    ])
    send_to_recipients(to, f"Purchase order updated: {row['po_number']}", message)


def send_accounts_order_placed_prompt(po_number: str, item_description: str | None = None) -> None:
    exp = int(time.time()) + PO_EMAIL_TOKEN_TTL_SEC
    token = sign_po_email_action_token(
        {"poNumber": po_number, "action": "order_placed", "exp": exp}
    )
    action_url = email_action_url(token)
    recipients = accounts_notify_emails()
    if not recipients:
        return
    subject = f"PO {po_number} approved — mark order placed"
    item_html = (
        f"<p><strong>Item:</strong> {escape_html(item_description)}</p>"
        if item_description else ""
    )
    html_body = f"""
    <h2>Approved purchase order</h2>
    <p>PO <strong>{escape_html(po_number)}</strong> has been approved.</p>
    {item_html}
    <p style="margin:24px 0 8px;">When you have placed the order with the vendor:</p>
    <p style="margin:0;">
      <a href="{action_url}" style="display:inline-block;padding:12px 20px;background:#0f5f95;color:#ffffff;text-decoration:none;border-radius:6px;font-weight:600;">Order placed</a>
    </p>
    <p style="color:#666;font-size:12px;margin-top:24px;">Link expires in 7 days.</p>
  """.strip()
    post_send_email(recipients, subject, html_body)


def notify_new_po_to_supervisor(supervisor_id: str, items: list[dict],
                                requestee_name: str = "—", notes: str = "",
                                bearer: str | None = None) -> None:
    supervisor_email = find_employee_email(supervisor_id, bearer)
    if not supervisor_email:
        raise WorkflowError("Supervisor email could not be resolved from employee records.")
    if not items:
        raise WorkflowError("At least one PO item is required for notification.")

    exp = int(time.time()) + PO_EMAIL_TOKEN_TTL_SEC
    requestee = (requestee_name or "").strip() or "—"
    po_numbers = [i["poNumber"] for i in items]
    if len(items) == 1:
        subject = f"New purchase request: {po_numbers[0]}"
    else:
        subject = f"New purchase request: {len(po_numbers)} items ({', '.join(po_numbers)})"

    sections = []
    for item in items:
        approve = email_action_url(sign_po_email_action_token(
            {"poNumber": item["poNumber"], "action": "approve",
             "supervisorId": supervisor_id, "exp": exp}))
        reject = email_action_url(sign_po_email_action_token(
            {"poNumber": item["poNumber"], "action": "reject",
             "supervisorId": supervisor_id, "exp": exp}))
        po = escape_html(item["poNumber"])
        sections.append(f"""<div style="margin-bottom: 20px; padding: 16px; border: 1px solid #e2e8f0; border-radius: 8px;">
        <ul style="margin: 0 0 12px; padding-left: 20px;">
          <li><strong>PO Number:</strong> {po}</li>
          <li><strong>Quantity:</strong> {escape_html(item.get("quantity") or "—")}</li>
          <li><strong>Item:</strong> {escape_html(item.get("itemDescription") or "—")}</li>
        </ul>
        <p style="margin: 0;">
          <a href="{approve}" style="display:inline-block;padding:10px 16px;background:#16a34a;color:#ffffff;text-decoration:none;border-radius:6px;font-weight:600;">Approve {po}</a>
          <span style="display:inline-block;width:8px;"></span>
          <a href="{reject}" style="display:inline-block;padding:10px 16px;background:#dc2626;color:#ffffff;text-decoration:none;border-radius:6px;font-weight:600;">Reject {po}</a>
        </p>
      </div>""")

    submitted = "a request" if len(items) == 1 else f"{len(items)} requests"
    notes_html = f"<p><strong>Notes:</strong> {escape_html(notes)}</p>" if notes else ""
    html_body = f"""
    <h2>New purchase request</h2>
    <p><strong>{escape_html(requestee)}</strong> submitted {submitted} that need your approval.</p>
    {notes_html}
    {chr(10).join(sections)}
    <p style="color:#666;font-size:12px;margin-top:24px;">
      Links expire in 7 days.
    </p>
  """.strip()
    post_send_email([supervisor_email, *accounts_notify_emails()], subject, html_body)


def send_low_stock_email(item_name, quantity, threshold) -> None:
    subject = f"Low Stock Alert: {item_name}"
    html = (
        f"<p>Item <strong>{item_name}</strong> is below threshold.</p>"
        f"<p>Current Quantity: {quantity}</p><p>Threshold: {threshold}</p>"
    )
    post_send_email([LOW_STOCK_RECIPIENTS], subject, html)


# --------------------------------------------------------------------------
# Email-action processing (approve / reject / order placed)
# --------------------------------------------------------------------------
def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _update_po(po_number: str, values: dict) -> dict | None:
    cols = list(values.keys())
    set_sql = ", ".join(f"{c} = %s" for c in cols)
    return inventory_db.execute_one(
        f"UPDATE purchase_requests SET {set_sql} WHERE po_number = %s RETURNING *",
        [values[c] for c in cols] + [po_number],
    )


def load_open_supervisor_po(payload: dict) -> tuple[dict | None, str | None]:
    row = inventory_db.query_one(
        "SELECT * FROM purchase_requests WHERE po_number = %s", [payload["poNumber"]]
    )
    if not row:
        return None, "Purchase request was not found."
    if str(row.get("supervisor_id")) != str(payload["supervisorId"]):
        return None, "You are not authorized to act on this request."
    if row.get("status") != "Open":
        return None, f"This request is no longer open (current status: {row.get('status')})."
    return row, None


def process_order_placed(payload: dict) -> str:
    """Returns the URL to redirect the user to."""
    row = inventory_db.query_one(
        "SELECT * FROM purchase_requests WHERE po_number = %s", [payload["poNumber"]]
    )
    if not row:
        return email_result_url("error", message="Purchase request was not found.")
    if row.get("status") != "Approved":
        msg = ("This order has already been marked as placed."
               if row.get("status") == "Order Placed"
               else f"Order cannot be marked placed (current status: {row.get('status')}).")
        return email_result_url("error", message=msg)
    try:
        _update_po(payload["poNumber"],
                   {"status": "Order Placed", "order_placed_date": _now_iso()})
    except Exception as e:  # noqa: BLE001
        print(f"[po-workflow] email-action order_placed update failed: {e}")
        return email_result_url("error", message="Could not mark this order as placed.")
    try:
        notify_order_placed(payload["poNumber"])
    except Exception as e:  # noqa: BLE001
        print(f"[po-workflow] email-action order_placed notify failed: {e}")
        return sso_entry_url_for_purchase_orders(
            "Mark Placed", payload["poNumber"], "Updated, but notification emails failed.")
    return sso_entry_url_for_purchase_orders("Mark Placed", payload["poNumber"])


def process_approve(payload: dict) -> str:
    row, err = load_open_supervisor_po(payload)
    if err:
        return email_result_url("error", message=err)
    item_description = str(row.get("item_description") or "").strip()
    try:
        _update_po(payload["poNumber"], {
            "status": "Approved",
            "approval_date": _now_iso(),
            "supervisor_id": payload["supervisorId"],
            "rejection_reason": None,
            "approved_rejected_by": payload["supervisorId"],
        })
    except Exception as e:  # noqa: BLE001
        print(f"[po-workflow] email-action approve update failed: {e}")
        return email_result_url("error", message="Could not approve this request.")

    notes = []
    try:
        notify_approved_to_requester(payload["poNumber"])
    except Exception as e:  # noqa: BLE001
        print(f"[po-workflow] requester approve notify failed: {e}")
        notes.append("Requester approval email failed.")
    try:
        send_accounts_order_placed_prompt(payload["poNumber"], item_description or None)
    except Exception as e:  # noqa: BLE001
        print(f"[po-workflow] accounts order-placed prompt failed: {e}")
        notes.append("Accounts team (order placed) email failed.")
    return sso_entry_url_for_purchase_orders(
        "Approve", payload["poNumber"], " ".join(notes) if notes else None)


def process_reject(payload: dict, rejection_reason: str) -> str:
    row, err = load_open_supervisor_po(payload)
    if err:
        return email_result_url("error", message=err)
    try:
        _update_po(payload["poNumber"], {
            "status": "Rejected",
            "rejection_reason": rejection_reason.strip(),
            "approved_rejected_by": payload["supervisorId"],
        })
    except Exception as e:  # noqa: BLE001
        print(f"[po-workflow] email-action reject update failed: {e}")
        return email_result_url("error", message="Could not reject this request.")
    notes = []
    try:
        notify_rejected_to_requester(payload["poNumber"])
    except Exception as e:  # noqa: BLE001
        print(f"[po-workflow] requestee rejection notify failed: {e}")
        notes.append("Requester rejection email failed.")
    return sso_entry_url_for_purchase_orders(
        "Reject", payload["poNumber"], " ".join(notes) if notes else None)
