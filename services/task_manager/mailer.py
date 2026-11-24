"""Sends one Task Manager email through the configured SMTP account."""

from __future__ import annotations

import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import Config
from services.task_manager.templates import Mail

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def smtp_configured() -> bool:
    return bool(Config.TASKS_SMTP_HOST and Config.TASKS_SMTP_USERNAME and Config.TASKS_SMTP_PASSWORD)


def redact(message: str) -> str:
    secret = (Config.TASKS_SMTP_PASSWORD or "").replace(" ", "")
    if secret:
        message = message.replace(secret, "[redacted]").replace(Config.TASKS_SMTP_PASSWORD, "[redacted]")
    return message[:500]


def send_mail(mail: Mail, to: str) -> None:
    """Raises on any failure (the outbox records the error and retries)."""
    if not smtp_configured():
        raise RuntimeError("Task Manager SMTP is not configured (set TASKS_SMTP_* env vars).")
    sender = Config.TASKS_SMTP_FROM or Config.TASKS_SMTP_USERNAME
    if not _EMAIL.match(sender or ""):
        raise RuntimeError("TASKS_SMTP_FROM is not an email address")
    if re.search(r"[\r\n]", to) or not _EMAIL.match(to):
        raise RuntimeError("Recipient email is invalid")

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = re.sub(r"[\r\n]+", " ", mail.subject).strip()
    msg.attach(MIMEText(mail.text, "plain", "utf-8"))
    msg.attach(MIMEText(mail.html, "html", "utf-8"))

    password = (Config.TASKS_SMTP_PASSWORD or "").replace(" ", "")
    if Config.TASKS_SMTP_USE_SSL:
        server = smtplib.SMTP_SSL(Config.TASKS_SMTP_HOST, Config.TASKS_SMTP_PORT, timeout=30)
    else:
        server = smtplib.SMTP(Config.TASKS_SMTP_HOST, Config.TASKS_SMTP_PORT, timeout=30)
    try:
        server.ehlo()
        if not Config.TASKS_SMTP_USE_SSL:
            server.starttls()
            server.ehlo()
        server.login(Config.TASKS_SMTP_USERNAME, password)
        server.sendmail(Config.TASKS_SMTP_USERNAME, [to], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass
