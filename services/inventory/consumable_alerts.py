"""
Minimum-quantity alerts for the Consumable Inventory.

Each consumable item has a `min_qty` (0 = no alert). When a branch's stock for
an item drops to or below it, ONE email goes to the configured recipients. The
email is sent only when the stock *crosses* the minimum (was above it, or had no
stock record yet), so consuming further below the line does not repeat it.
Failures here never affect the stock update itself.
"""

from __future__ import annotations

import re
import threading

from config import Config
from models.inventory_db import inventory_db
from services.inventory.email import send_inventory_email
from services.inventory.po_workflow import escape_html


def crossed_minimum(prev_qty: int | None, new_qty: int, min_qty: int) -> bool:
    if min_qty <= 0 or new_qty > min_qty:
        return False
    return prev_qty is None or prev_qty > min_qty


_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def split_emails(raw) -> list[str]:
    """Splits on commas, semicolons, spaces and new lines; lower-cases and de-duplicates."""
    out: list[str] = []
    for part in re.split(r"[,;\s]+", str(raw or "")):
        part = part.strip().lower()
        if part and part not in out:
            out.append(part)
    return out


def normalize_emails(raw) -> tuple[str | None, str | None]:
    """(comma-joined emails or None when blank, error message)."""
    emails = split_emails(raw)
    bad = [e for e in emails if not _EMAIL_RE.match(e)]
    if bad:
        return None, f"Not a valid email address: {', '.join(bad)}"
    return (", ".join(emails) or None), None


def default_recipients() -> list[str]:
    return split_emails(Config.INVENTORY_LOW_STOCK_NOTIFY_EMAILS)


def recipients(item: dict | None = None) -> list[str]:
    """The item's own list when it has one, otherwise the default list."""
    own = split_emails((item or {}).get("notify_emails"))
    return own or default_recipients()


def build_email(item: dict, branch: str, new_qty: int, min_qty: int) -> tuple[str, str]:
    code = escape_html(item.get("code") or "")
    desc = escape_html(item.get("description") or "")
    supplier = escape_html(item.get("supplier_name") or "-")
    subject = f"Low stock: {item.get('code') or ''} at {branch} ({new_qty} left, minimum {min_qty})"
    html = f"""
    <div style="font-family:Arial,sans-serif;font-size:14px;color:#222">
      <p>The stock of a consumable item has reached its minimum quantity.</p>
      <table style="border-collapse:collapse">
        <tr><td style="padding:4px 12px 4px 0"><b>Item</b></td><td>{code} &ndash; {desc}</td></tr>
        <tr><td style="padding:4px 12px 4px 0"><b>Branch</b></td><td>{escape_html(branch)}</td></tr>
        <tr><td style="padding:4px 12px 4px 0"><b>Available now</b></td><td>{new_qty}</td></tr>
        <tr><td style="padding:4px 12px 4px 0"><b>Minimum quantity</b></td><td>{min_qty}</td></tr>
        <tr><td style="padding:4px 12px 4px 0"><b>Supplier</b></td><td>{supplier}</td></tr>
      </table>
      <p>Please arrange a re-order.</p>
    </div>"""
    return subject, html


def _send(item: dict, branch: str, new_qty: int, min_qty: int) -> None:
    try:
        to = recipients(item)
        if not to:
            return
        subject, html = build_email(item, branch, new_qty, min_qty)
        ok, err = send_inventory_email(to, subject, html)
        if not ok:
            print(f"[inventory] low-stock email not sent: {err}")
    except Exception as e:  # noqa: BLE001
        print(f"[inventory] low-stock email failed: {e}")


def check_and_notify(item_id, branch: str, prev_qty: int | None, new_qty: int,
                     background: bool = True) -> bool:
    """Looks up the item's minimum and emails if it was just reached.
    Returns True when an email was triggered. Never raises."""
    try:
        item = inventory_db.query_one(
            """
            SELECT i.*, s.name AS supplier_name
            FROM consumable_items i LEFT JOIN suppliers s ON s.id = i.supplier_id
            WHERE i.id = %s
            """,
            [item_id],
        )
        if not item:
            return False
        min_qty = int(item.get("min_qty") or 0)
        if not crossed_minimum(prev_qty, int(new_qty), min_qty):
            return False
        if background:
            threading.Thread(target=_send, args=(item, branch, int(new_qty), min_qty), daemon=True).start()
        else:
            _send(item, branch, int(new_qty), min_qty)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[inventory] low-stock check failed: {e}")
        return False
