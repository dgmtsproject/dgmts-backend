"""
Purchase-order workflow endpoints (attached to the inventory blueprint).

These replace the Next.js server routes of the Inventory frontend so that the
frontend can be a static site:

  POST /api/inventory/po/workflow-notify               (was /api/purchase-request/workflow-notify)
  POST /api/inventory/po/send-supervisor-pending-email (was .../send-supervisor-pending-email)
  GET  /api/inventory/po/email-action?token=...        (was GET  .../email-action)
  POST /api/inventory/po/email-action                  (was POST .../email-action, reject form)
  GET  /api/inventory/po/email-reject-preview?token=.. (was .../email-reject-preview)
  POST /api/inventory/email/low-stock                  (was /api/email/low-stock)
  POST /api/inventory/superuser/login                  (temporary super-user sign-in)
  GET  /api/inventory/superuser/status                 ({enabled})

Add ?dry_run=1 to the two POST notification endpoints to see who would be
emailed (and the content) without sending anything.
"""

from urllib.parse import quote

from flask import g, jsonify, redirect, request

from inventory import superuser
from inventory.ms_auth import _extract_bearer
from services.inventory import po_workflow as wf


def _json() -> dict:
    return request.get_json(silent=True) or {}


def _dry_run() -> bool:
    return (request.args.get("dry_run") or "").lower() in ("1", "true", "yes") or bool(
        _json().get("dryRun")
    )


def _begin_dry_run():
    if _dry_run():
        g.po_dry_run_sink = []


def _ok():
    payload = {"success": True}
    sink = g.get("po_dry_run_sink")
    if sink is not None:
        payload["dryRun"] = True
        payload["wouldSend"] = sink
    return jsonify(payload)


def _parse_items(body: dict) -> list[dict]:
    raw = body.get("items")
    if isinstance(raw, list) and raw:
        items = []
        for entry in raw:
            row = entry if isinstance(entry, dict) else {}
            po = str(row.get("poNumber") or "").strip()
            if po:
                items.append({
                    "poNumber": po,
                    "quantity": str(row.get("quantity") or "").strip() or None,
                    "itemDescription": str(row.get("itemDescription") or "").strip() or None,
                })
        return items
    po = str(body.get("poNumber") or "").strip()
    if not po:
        return []
    return [{
        "poNumber": po,
        "quantity": str(body.get("quantity") or "").strip() or None,
        "itemDescription": str(body.get("itemDescription") or "").strip() or None,
    }]


def register_workflow_routes(bp):
    # Temporary super-user sign-in (see inventory/superuser.py). 404 unless configured.
    @bp.route("/superuser/login", methods=["POST"])
    def inventory_superuser_login():
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
        return jsonify({"token": token, "expires_in": ttl, "email": superuser.principal_email()})

    @bp.route("/superuser/status", methods=["GET"])
    def inventory_superuser_status():
        return jsonify({"enabled": superuser.enabled()})

    @bp.route("/po/workflow-notify", methods=["POST"])
    def po_workflow_notify():
        bearer = _extract_bearer()
        if not bearer:
            return jsonify({"error": "Missing Authorization bearer token."}), 401
        try:
            body = _json()
            po_number = str(body.get("poNumber") or "").strip()
            event = str(body.get("event") or "").strip()
            editor_user_id = str(body.get("editorUserId") or "").strip()
            if not po_number or not event:
                return jsonify({"error": "poNumber and event are required."}), 400

            _begin_dry_run()
            try:
                row = wf.load_po_row(po_number)
            except wf.WorkflowError:
                row = None
            item_description = str((row or {}).get("item_description") or "").strip()

            if event == "approved":
                wf.notify_approved_to_requester(po_number, bearer)
                wf.send_accounts_order_placed_prompt(po_number, item_description or None)
            elif event == "rejected":
                wf.notify_rejected_to_requester(po_number, bearer)
            elif event == "order_placed":
                wf.notify_order_placed(po_number, bearer)
            elif event == "delivered":
                wf.notify_delivered(po_number, bearer)
            elif event == "edited":
                if not editor_user_id:
                    return jsonify({"error": "editorUserId is required for edited event."}), 400
                wf.notify_po_edited(po_number, editor_user_id, bearer)
            else:
                return jsonify({"error": f"Unknown event: {event}"}), 400
            return _ok()
        except Exception as e:  # noqa: BLE001
            print(f"[po-workflow] workflow-notify failed: {e}")
            return jsonify({"error": str(e) or "Notification failed."}), 500

    @bp.route("/po/send-supervisor-pending-email", methods=["POST"])
    def po_send_supervisor_pending_email():
        try:
            bearer = _extract_bearer()
            body = _json()
            supervisor_id = str(body.get("supervisorId") or "").strip()
            requestee = str(body.get("requesteeName") or "").strip() or "—"
            notes = str(body.get("notes")).strip() if body.get("notes") is not None else ""
            items = _parse_items(body)
            if not supervisor_id or not items:
                return jsonify({"error": "supervisorId and at least one item are required."}), 400
            _begin_dry_run()
            wf.notify_new_po_to_supervisor(supervisor_id, items, requestee, notes, bearer)
            return _ok()
        except Exception as e:  # noqa: BLE001
            print(f"[po-workflow] send-supervisor-pending-email failed: {e}")
            return jsonify({"error": str(e) or "Failed to send email."}), 500

    @bp.route("/po/email-action", methods=["GET", "POST"])
    def po_email_action():
        """Public (the signed token is the credential), like the old Next.js route."""
        try:
            if request.method == "GET":
                token = request.args.get("token")
                if not token:
                    return redirect(wf.email_result_url("error", message="Missing action link."))
                payload = wf.verify_po_email_action_token(token)
                if not payload:
                    return redirect(wf.email_result_url(
                        "error", message="This link is invalid or has expired."))
                if payload["action"] == "reject":
                    return redirect(wf.email_reject_form_url(token))
                if payload["action"] == "order_placed":
                    return redirect(wf.process_order_placed(payload))
                if payload["action"] == "approve":
                    return redirect(wf.process_approve(payload))
                return redirect(wf.email_result_url("error", message="Invalid action link."))

            # POST: reject form (JSON or form-encoded). 303 so the browser
            # follows the redirect with GET (a 307 would re-POST to the UI page).
            if request.is_json:
                body = _json()
                token = str(body.get("token") or "").strip()
                reason = str(body.get("rejectionReason") or "")
            else:
                token = str(request.form.get("token") or "").strip()
                reason = str(request.form.get("rejectionReason") or "")
            if not token:
                return jsonify({"error": "Missing action link."}), 400
            payload = wf.verify_po_email_action_token(token)
            if not payload or payload["action"] != "reject":
                return redirect(wf.email_result_url(
                    "error", message="This reject link is invalid or has expired."), 303)
            if not reason.strip():
                return redirect(
                    f"{wf.email_reject_form_url(token)}&formError="
                    f"{quote('Please enter a rejection reason.', safe='')}", 303)
            return redirect(wf.process_reject(payload, reason), 303)
        except Exception as e:  # noqa: BLE001
            print(f"[po-workflow] email-action failed: {e}")
            return redirect(wf.email_result_url("error", message="Something went wrong."), 303)

    @bp.route("/po/email-reject-preview", methods=["GET"])
    def po_email_reject_preview():
        token = request.args.get("token")
        payload = wf.verify_po_email_action_token(token)
        if not payload or payload["action"] != "reject":
            return jsonify({"error": "This reject link is invalid or has expired."}), 400
        row, err = wf.load_open_supervisor_po(payload)
        if err:
            return jsonify({"error": err}), 400
        out = {"poNumber": payload["poNumber"]}
        desc = str(row.get("item_description") or "").strip()
        if desc:
            out["itemDescription"] = desc
        if row.get("quantity") is not None:
            out["quantity"] = str(row["quantity"])
        return jsonify(out)

    @bp.route("/email/low-stock", methods=["POST"])
    def email_low_stock():
        body = _json()
        try:
            wf.send_low_stock_email(
                body.get("itemName"), body.get("quantity"), body.get("threshold"))
        except Exception as e:  # noqa: BLE001
            # The old route also ignored send failures (callers fire-and-forget).
            print(f"[po-workflow] low-stock email failed: {e}")
        return jsonify({"success": True, "message": "Email processed"})
