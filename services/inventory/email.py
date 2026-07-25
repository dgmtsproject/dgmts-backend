"""
Isolated SMTP mail sender for the Inventory module.

Uses INVENTORY_SMTP_* config (the custom mail system) and is intentionally a
separate implementation from services/email_service.py so the two modules stay
fully decoupled.
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from config import Config


def _recipients(to_email) -> list[str]:
    if isinstance(to_email, str):
        return [e.strip() for e in to_email.split(",") if e.strip()]
    return [str(e).strip() for e in (to_email or []) if str(e).strip()]


def smtp_configured() -> bool:
    return bool(
        Config.INVENTORY_SMTP_HOST
        and Config.INVENTORY_SMTP_USERNAME
        and Config.INVENTORY_SMTP_PASSWORD
    )


def send_inventory_email(to_email, subject: str, html_body: str) -> tuple[bool, str | None]:
    """Send an HTML email via the inventory SMTP server.

    Returns (success, error_message). If SMTP is not configured yet, returns
    (False, "SMTP not configured") without raising, so endpoints degrade
    gracefully until creds are provided.
    """
    if not smtp_configured():
        return False, "Inventory SMTP is not configured (set INVENTORY_SMTP_* env vars)."

    recipients = _recipients(to_email)
    if not recipients:
        return False, "No recipients."

    msg = MIMEMultipart()
    msg["From"] = Config.INVENTORY_SMTP_FROM or Config.INVENTORY_SMTP_USERNAME
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    try:
        if Config.INVENTORY_SMTP_USE_SSL:
            server = smtplib.SMTP_SSL(
                Config.INVENTORY_SMTP_HOST, Config.INVENTORY_SMTP_PORT, timeout=30
            )
        else:
            server = smtplib.SMTP(
                Config.INVENTORY_SMTP_HOST, Config.INVENTORY_SMTP_PORT, timeout=30
            )
            server.starttls()

        server.login(Config.INVENTORY_SMTP_USERNAME, Config.INVENTORY_SMTP_PASSWORD)
        server.sendmail(
            Config.INVENTORY_SMTP_FROM or Config.INVENTORY_SMTP_USERNAME,
            recipients,
            msg.as_string(),
        )
        server.quit()
        return True, None
    except smtplib.SMTPAuthenticationError as e:
        return False, f"SMTP auth error: {e}"
    except smtplib.SMTPException as e:
        return False, f"SMTP error: {e}"
    except Exception as e:  # noqa: BLE001
        return False, f"Failed to send email: {e}"
