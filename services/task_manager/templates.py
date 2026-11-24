"""Email wording for Task Manager notifications (port of the original template module)."""

from __future__ import annotations

import re
from dataclasses import dataclass

PRIMARY = "#0f5f95"


@dataclass
class Mail:
    subject: str
    html: str
    text: str


def escape_html(value) -> str:
    return str(value if value is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def origin_of(app_url: str) -> str:
    return (app_url or "").strip().rstrip("/")


def short(text) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:77] + "…" if len(value) > 80 else value


def status_label(status) -> str:
    return {
        "pending": "Pending",
        "accepted": "Accepted",
        "in_progress": "In Progress",
        "denied": "Denied",
        "forwarded": "Forwarded",
        "completed": "Completed",
        "cancelled": "Cancelled",
    }.get(status or "", str(status or "").replace("_", " "))


def us_date(value) -> str:
    if not value:
        return ""
    parts = str(value)[:10].split("-")
    if len(parts) != 3 or not all(parts) or len(parts[0]) != 4:
        return str(value)
    year, month, day = parts
    return f"{month}/{day}/{year}"


def cc_subject(p: dict) -> str:
    event = p.get("event_type") or ""
    assignee = p.get("assignee_name")
    assigner = p.get("assigner_name")
    if event == "accepted":
        return f"CC: {assignee} accepted the task"
    if event == "denied":
        return f"CC: {assignee} denied the task"
    if event == "forwarded":
        return f"CC: {p.get('forwarder_name') or assignee} forwarded the task to {assignee}"
    if event == "completed":
        return f"CC: Task completed by {assignee}"
    if event == "cancelled":
        return f"CC: Task cancelled by {assigner}"
    if event == "reassigned":
        return f"CC: {assigner} reassigned the task to {assignee}"
    if event == "created":
        return f"CC: {assigner} assigned a task to {assignee}"
    return f"CC: Task status is {status_label(p.get('status'))}"


def subject_for(p: dict) -> str:
    desc = short(p.get("description"))
    kind = p.get("kind")
    assignee = p.get("assignee_name")
    if kind == "task_created":
        return f"New task assigned by {p.get('assigner_name')}: {desc}"
    if kind == "assigned_by_you":
        return f"You assigned a task to {assignee}: {desc}"
    if kind == "reassigned":
        return f"Task reassigned to {assignee}: {desc}"
    if kind in ("accepted", "in_progress"):
        return f"{assignee} accepted your task"
    if kind == "denied":
        return f"{assignee} denied your task"
    if kind == "forwarded_to_you":
        return f"Task forwarded to you by {p.get('forwarder_name') or assignee}"
    if kind == "forwarded_fyi":
        return f"{p.get('forwarder_name')} forwarded your task to {assignee}"
    if kind == "completed":
        return f"Task completed by {assignee}"
    if kind == "cancelled":
        return f"Task cancelled by {p.get('assigner_name')}"
    if kind == "cc_notice":
        return cc_subject(p)
    if kind == "due_digest":
        return "Tasks due tomorrow and overdue"
    return f"Task update: {desc}"


def button(href: str, label: str) -> str:
    return (
        f'<a href="{href}" style="display:inline-block;background:{PRIMARY};color:#fff;padding:10px 16px;'
        f'text-decoration:none;border-radius:6px;margin:0 8px 8px 0">{escape_html(label)}</a>'
    )


def action_links(p: dict, origin: str) -> tuple[str, str]:
    """Assignee actions open the static app. Reject collects a reason on that page."""
    from urllib.parse import quote

    token = (p.get("action_token") or "").strip()
    home = f"{origin}/"
    if p.get("link_for") != "assignee" or not token:
        return f"<p>{button(home, 'Open DGMTS Tasks')}</p>", home
    base = f"{origin}/email-action/?token={quote(token, safe='')}"
    status = "in_progress" if p.get("status") == "accepted" else p.get("status")
    if status == "pending":
        accept, deny = f"{base}&action=accept", f"{base}&action=deny"
        return f"<p>{button(accept, 'Accept')}{button(deny, 'Reject')}</p>", f"Accept: {accept}\nReject: {deny}"
    if status == "in_progress":
        complete, deny = f"{base}&action=complete", f"{base}&action=deny"
        return f"<p>{button(complete, 'Complete')}{button(deny, 'Reject')}</p>", f"Complete: {complete}\nReject: {deny}"
    return f"<p>{button(home, 'Open DGMTS Tasks')}</p>", home


def render_mail(payload: dict, app_url: str) -> Mail:
    p = payload
    subject = subject_for(p)
    origin = origin_of(app_url)
    links_html, links_text = action_links(p, origin)
    logo = f"{origin}/brand/dgmts.png"
    due = us_date(p.get("due_date"))
    status = status_label(p.get("status"))
    assignment = ""
    if p.get("assigner_name") and p.get("assignee_name"):
        assignment = (
            f"<p><strong>{escape_html(p['assigner_name'])}</strong> assigned this task to "
            f"<strong>{escape_html(p['assignee_name'])}</strong>.</p>"
        )
    digest = "".join(
        f"<li>#{t.get('task_no')} ({escape_html(t.get('bucket'))}) due {escape_html(us_date(t.get('due_date')))}: "
        f"{escape_html(t.get('description'))}</li>"
        for t in (p.get("tasks") or [])
    )
    task_no = p.get("task_no") if p.get("task_no") is not None else ""
    html = f"""<div style="font-family:Arial,Helvetica,sans-serif;color:#11384f">
    <div style="background:{PRIMARY};padding:16px">
      <img src="{logo}" alt="DGMTS" height="48" />
    </div>
    <div style="padding:16px">
      <p style="font-size:16px;font-weight:700">{escape_html(subject)}</p>
      {assignment}
      <p>Task #{task_no}</p>
      <p>{escape_html(p.get("description") or "")}</p>
      <p>Reference: {escape_html(p.get("reference") or "")}</p>
      <p>Due: {escape_html(due)}</p>
      <p>Status: {escape_html(status)}</p>
      <p>Note: {escape_html(p.get("note") or "")}</p>
      {f"<ul>{digest}</ul>" if digest else ""}
      {links_html}
    </div>
  </div>"""
    text_lines = [
        subject,
        f"{p['assigner_name']} assigned this task to {p['assignee_name']}."
        if p.get("assigner_name") and p.get("assignee_name") else "",
        f"Task #{p['task_no']}" if p.get("task_no") else "",
        p.get("description") or "",
        f"Reference: {p['reference']}" if p.get("reference") else "",
        f"Due: {due}" if due else "",
        f"Status: {status}" if status else "",
        f"Note: {p['note']}" if p.get("note") else "",
        links_text,
    ]
    return Mail(subject, html, "\n".join(line for line in text_lines if line))
