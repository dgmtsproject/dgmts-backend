"""
Authentication + per-person access for the Projects Directory module.

Every request must carry the company-login bearer token. It is validated by
calling the directory's `/user/me` (result cached briefly), then the caller's
access flags are read from projects.user_access. Admin emails
(PROJECTS_ADMIN_EMAILS) always have every flag.

Unlike the inventory module's optional auth, these endpoints are strict:
no valid token -> 401, missing permission -> 403.

Independent of auth/jwt_handler.py and of inventory/ms_auth.py (shares no state).
"""

from __future__ import annotations

import time
from functools import wraps

import requests
from flask import jsonify, request

from config import Config
from models.projects_db import projects_db

FLAGS = ("can_dashboard", "can_projects", "can_chat", "can_edit", "is_admin")

_PROFILE_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 120


def _normalize_email(value) -> str:
    return str(value or "").strip().lower()


def _extract_bearer() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer "):].strip() or None
    return None


def _shape_profile(raw: dict) -> dict:
    p = raw
    if isinstance(p.get("data"), dict):
        p = p["data"]
    if isinstance(p.get("user"), dict):
        p = {**p["user"], **p}
    email = p.get("email") or p.get("mail") or ""
    name = p.get("full_name") or p.get("name") or p.get("employee_name") or p.get("display_name") or ""
    user_id = p.get("user_id") or p.get("id") or p.get("employee_id")
    return {
        "user_id": str(user_id).strip() if user_id is not None else "",
        "email": str(email).strip(),
        "email_normalized": _normalize_email(email),
        "full_name": str(name).strip(),
        "job_title_name": str(p.get("job_title_name") or p.get("jobTitleName") or "").strip(),
    }


def validate_ms_token(token: str) -> dict | None:
    now = time.time()
    cached = _PROFILE_CACHE.get(token)
    if cached and cached[0] > now:
        return cached[1]
    url = f"{Config.PROJECTS_MS_API_BASE_URL.rstrip('/')}/user/me"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
    except (requests.RequestException, ValueError):
        return None
    profile = _shape_profile(payload if isinstance(payload, dict) else {})
    if not profile["user_id"] or not profile["email"]:
        return None
    _PROFILE_CACHE[token] = (now + _CACHE_TTL_SECONDS, profile)
    return profile


def is_admin_email(email: str | None) -> bool:
    return _normalize_email(email) in set(Config.PROJECTS_ADMIN_EMAILS)


def load_access(profile: dict) -> dict:
    """Access flags for a validated profile. Admin emails get everything."""
    if is_admin_email(profile.get("email")):
        return {f: True for f in FLAGS}
    row = projects_db.query_one(
        "SELECT " + ", ".join(FLAGS) + " FROM user_access WHERE employee_user_id = %s",
        [profile["user_id"]],
    )
    access = {f: bool(row and row.get(f)) for f in FLAGS}
    if access["is_admin"]:
        return {f: True for f in FLAGS}
    return access


def access_required(flag: str | None = None):
    """Require a valid token and (optionally) one access flag.

    Attaches request.projects_user (profile), request.projects_access (flags)
    and request.projects_bearer (the caller's token).
    """

    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            token = _extract_bearer()
            if not token:
                return jsonify({"error": "Missing or invalid Authorization header"}), 401
            profile = validate_ms_token(token)
            if not profile:
                return jsonify({"error": "Invalid or expired token"}), 401
            try:
                access = load_access(profile)
            except Exception as e:  # noqa: BLE001
                return jsonify({"error": f"Access lookup failed: {e}"}), 500
            if flag and not access.get(flag):
                return jsonify({"error": "You don't have permission for this action."}), 403
            request.projects_user = profile
            request.projects_access = access
            request.projects_bearer = token
            return f(*args, **kwargs)

        return decorated

    return decorator
