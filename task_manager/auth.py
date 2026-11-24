"""
Authentication for the Task Manager module.

Two kinds of bearer token are accepted:

1. A company (Microsoft) token — the main system redirects users here with
   ?email&token. We confirm it with the MS directory's /user/me, then match the
   verified email to an active employee.
2. A session token ("tk.<payload>.<signature>") handed out by POST /auth/login
   (login id + password). It is signed with TASKS_SESSION_SECRET, so it cannot be
   forged or switched to another employee id from the browser.

Either way the result is an employee id, which is passed to the SQL workflow
functions as the acting user.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from functools import wraps

import requests
from flask import request

from config import Config
from models.tasks_db import tasks_db
from task_manager.errors import error_response

PREFIX = "tk."
_PROFILE_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 120
_ATTEMPTS: dict[str, list[float]] = {}
MAX_ATTEMPTS, WINDOW = 10, 300  # 10 password tries per 5 minutes per IP


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def session_enabled() -> bool:
    return bool(Config.TASKS_SESSION_SECRET and len(Config.TASKS_SESSION_SECRET) >= 16)


def _sign(payload_b64: str) -> str:
    return _b64(hmac.new(Config.TASKS_SESSION_SECRET.encode(), payload_b64.encode(), hashlib.sha256).digest())


def issue_session_token(employee_id: str) -> tuple[str, int]:
    ttl = Config.TASKS_SESSION_DAYS * 86400
    payload = _b64(json.dumps({"sub": employee_id, "exp": int(time.time()) + ttl}, separators=(",", ":")).encode())
    return f"{PREFIX}{payload}.{_sign(payload)}", ttl


def verify_session_token(token: str) -> str | None:
    """Employee id for a valid, unexpired session token, else None."""
    if not session_enabled() or not token.startswith(PREFIX):
        return None
    try:
        payload_b64, sig = token[len(PREFIX):].split(".", 1)
        if not hmac.compare_digest(sig, _sign(payload_b64)):
            return None
        data = json.loads(_unb64(payload_b64))
        if int(data.get("exp", 0)) < time.time():
            return None
        sub = str(data.get("sub") or "").strip()
        return sub or None
    except Exception:  # noqa: BLE001
        return None


def too_many_attempts(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _ATTEMPTS.get(ip, []) if now - t < WINDOW]
    _ATTEMPTS[ip] = recent
    return len(recent) >= MAX_ATTEMPTS


def record_attempt(ip: str) -> None:
    _ATTEMPTS.setdefault(ip, []).append(time.time())


def client_ip() -> str:
    return (request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0]).strip()


def extract_bearer() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer "):].strip() or None
    return None


def _email_from_ms(token: str) -> str | None:
    now = time.time()
    cached = _PROFILE_CACHE.get(token)
    if cached and cached[0] > now:
        return cached[1]
    try:
        resp = requests.get(
            f"{Config.TASKS_MS_API_BASE_URL.rstrip('/')}/user/me",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        raw = resp.json()
    except (requests.RequestException, ValueError):
        return None
    p = raw if isinstance(raw, dict) else {}
    if isinstance(p.get("data"), dict):
        p = p["data"]
    if isinstance(p.get("user"), dict):
        p = {**p["user"], **p}
    email = str(p.get("email") or p.get("mail") or "").strip().lower()
    if not email:
        return None
    _PROFILE_CACHE[token] = (now + _CACHE_TTL, email)
    return email


def employee_id_for_email(email: str) -> str | None:
    row = tasks_db.query_one(
        "SELECT id FROM v_employees WHERE is_active AND email IS NOT NULL AND lower(email) = %s LIMIT 1",
        [email.lower()],
    )
    return row["id"] if row else None


def _employee_active(employee_id: str) -> bool:
    return bool(tasks_db.query_one("SELECT 1 AS ok FROM v_employees WHERE id = %s AND is_active", [employee_id]))


def _ensure_admin_role(employee_id: str, email: str) -> None:
    """Company-login accounts listed in TASKS_ADMIN_EMAILS get the Admin tab."""
    if email.lower() not in Config.TASKS_ADMIN_EMAILS:
        return
    row = tasks_db.query_one("SELECT role FROM app_user_roles WHERE employee_id = %s", [employee_id])
    if row and row["role"] == "admin":
        return
    tasks_db.execute(
        "INSERT INTO app_user_roles (employee_id, email, role) VALUES (%s, %s, 'admin') "
        "ON CONFLICT (employee_id) DO UPDATE SET role = 'admin', email = EXCLUDED.email",
        [employee_id, email.lower()],
    )


def resolve_actor(token: str) -> tuple[str | None, str | None]:
    """(employee_id, problem). problem is 'invalid' (bad token) or 'not_in_directory'."""
    if token.startswith(PREFIX):
        emp = verify_session_token(token)
        if not emp or not _employee_active(emp):
            return None, "invalid"
        return emp, None
    email = _email_from_ms(token)
    if not email:
        return None, "invalid"
    emp = employee_id_for_email(email)
    if not emp:
        return None, "not_in_directory"
    _ensure_admin_role(emp, email)
    return emp, None


def actor_required(f):
    """Requires a valid token. Sets request.task_actor to the employee id."""

    @wraps(f)
    def decorated(*args, **kwargs):
        token = extract_bearer()
        if not token:
            return error_response("UNAUTHENTICATED", "Please sign in.", 401)
        try:
            actor, problem = resolve_actor(token)
        except Exception as e:  # noqa: BLE001
            print(f"[tasks] auth lookup failed: {e}")
            return error_response("UNKNOWN", "Sign-in check failed. Please try again.", 500)
        if problem == "not_in_directory":
            return error_response(
                "NOT_IN_DIRECTORY", "Your account isn't in the employee directory. Contact admin.", 403
            )
        if not actor:
            return error_response("UNAUTHENTICATED", "Your session has expired. Please sign in again.", 401)
        request.task_actor = actor
        return f(*args, **kwargs)

    return decorated
