"""
Authentication shim for the Inventory module.

The Inventory frontend authenticates users through Microsoft SSO
(ms.dullesgeotechnical.com) and forwards the resulting bearer token on every
request. We validate that token by calling the MS directory's `/user/me`
endpoint and attach the resolved profile to `request.inventory_user`.

This is deliberately independent from auth/jwt_handler.py — it does not use the
Flask JWT scheme and shares no state with the existing backend.
"""

import time
from functools import wraps

import requests
from flask import request, jsonify

from config import Config


# Tiny in-process cache so we don't hit /user/me on every single API call.
# token -> (expiry_epoch, profile_dict)
_PROFILE_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 120


def _extract_bearer() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        token = header[len("Bearer "):].strip()
        return token or None
    return None


def _normalize_email(value) -> str:
    return str(value or "").strip().lower()


def _shape_profile(raw: dict) -> dict:
    """Normalize the /user/me payload into a flat profile dict.

    MS returns shapes like {data: {...}} or {user: {...}} or a flat object.
    """
    p = raw
    if isinstance(p.get("data"), dict):
        p = p["data"]
    if isinstance(p.get("user"), dict):
        p = {**p["user"], **p}

    email = (
        p.get("email")
        or p.get("mail")
        or (p.get("user") or {}).get("email")
        or ""
    )
    name = (
        p.get("full_name")
        or p.get("name")
        or p.get("employee_name")
        or p.get("display_name")
        or ""
    )
    user_id = p.get("user_id") or p.get("id") or p.get("employee_id")
    job_title = p.get("job_title_name") or p.get("jobTitleName") or ""

    return {
        "user_id": str(user_id).strip() if user_id is not None else "",
        "email": str(email).strip(),
        "email_normalized": _normalize_email(email),
        "full_name": str(name).strip(),
        "job_title_name": str(job_title).strip(),
        "raw": raw,
    }


def validate_ms_token(token: str) -> dict | None:
    """Return a normalized profile for a valid token, else None."""
    now = time.time()
    cached = _PROFILE_CACHE.get(token)
    if cached and cached[0] > now:
        return cached[1]

    url = f"{Config.INVENTORY_MS_API_BASE_URL.rstrip('/')}/user/me"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=15,
        )
    except requests.RequestException:
        return None

    if resp.status_code != 200:
        return None

    try:
        payload = resp.json()
    except ValueError:
        return None

    profile = _shape_profile(payload if isinstance(payload, dict) else {})
    if not profile["email"] and not profile["user_id"]:
        return None

    _PROFILE_CACHE[token] = (now + _CACHE_TTL_SECONDS, profile)
    return profile


def is_inventory_admin(email: str | None) -> bool:
    return _normalize_email(email) in set(Config.INVENTORY_ADMIN_EMAILS)


def _internal_secret_ok() -> bool:
    """True when the request carries the trusted server-to-server secret."""
    secret = Config.INVENTORY_INTERNAL_SECRET
    if not secret:
        return False
    provided = request.headers.get("X-Inventory-Internal-Secret", "")
    return bool(provided) and provided == secret


def _internal_principal() -> dict:
    return {
        "user_id": "",
        "email": None,
        "email_normalized": "",
        "full_name": "internal",
        "job_title_name": "",
        "internal": True,
        "raw": {},
    }


def ms_auth_required(f):
    """Require a valid MS bearer token. Attaches request.inventory_user."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if _internal_secret_ok():
            request.inventory_user = _internal_principal()
            request.inventory_bearer = Config.INVENTORY_MS_FALLBACK_BEARER
            return f(*args, **kwargs)
        token = _extract_bearer()
        if not token:
            return jsonify({"error": "Missing or invalid Authorization header"}), 401
        profile = validate_ms_token(token)
        if not profile:
            return jsonify({"error": "Invalid or expired token"}), 401
        request.inventory_user = profile
        request.inventory_bearer = token
        return f(*args, **kwargs)

    return decorated


def ms_admin_required(f):
    """Require a valid MS token AND inventory-admin email (or the internal secret)."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if _internal_secret_ok():
            request.inventory_user = _internal_principal()
            request.inventory_bearer = Config.INVENTORY_MS_FALLBACK_BEARER
            return f(*args, **kwargs)
        token = _extract_bearer()
        if not token:
            return jsonify({"error": "Missing or invalid Authorization header"}), 401
        profile = validate_ms_token(token)
        if not profile:
            return jsonify({"error": "Invalid or expired token"}), 401
        if not is_inventory_admin(profile.get("email")):
            return jsonify({"error": "Inventory admin access required"}), 403
        request.inventory_user = profile
        request.inventory_bearer = token
        return f(*args, **kwargs)

    return decorated
