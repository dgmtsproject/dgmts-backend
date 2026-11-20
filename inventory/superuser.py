"""
Temporary super-user sign-in for the Inventory module.

Why: until the company login (ms.dullesgeotechnical.com) lists the static
inventory site as an allowed return address, nobody can come back from it with
a token. This lets the team sign in meanwhile. It is checked ONLY on the backend:

- Enabled only when INVENTORY_SUPERUSER_PASSWORD_HASH and INVENTORY_SUPERUSER_SECRET
  are both set in .env (remove them to disable).
- The password is stored as a hash; sign-in is rate limited per IP.
- On success a signed token ("su.<payload>.<signature>") is returned, valid for
  INVENTORY_SUPERUSER_HOURS. It cannot be forged without the secret.
- The token acts as INVENTORY_SUPERUSER_EMAIL (default admin@gmail.com, an
  inventory admin), so it has the same access as that account.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from werkzeug.security import check_password_hash

from config import Config

PREFIX = "su."
_attempts: dict[str, list[float]] = {}
MAX_ATTEMPTS, WINDOW = 5, 300  # 5 tries per 5 minutes per IP


def enabled() -> bool:
    return bool(Config.INVENTORY_SUPERUSER_PASSWORD_HASH and Config.INVENTORY_SUPERUSER_SECRET)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload_b64: str) -> str:
    key = Config.INVENTORY_SUPERUSER_SECRET.encode()
    return _b64(hmac.new(key, payload_b64.encode(), hashlib.sha256).digest())


def issue_token() -> tuple[str, int]:
    ttl = Config.INVENTORY_SUPERUSER_HOURS * 3600
    payload = _b64(json.dumps({"sub": "superuser", "exp": int(time.time()) + ttl}).encode())
    return f"{PREFIX}{payload}.{_sign(payload)}", ttl


def principal_email() -> str:
    return Config.INVENTORY_SUPERUSER_EMAIL.strip().lower()


def verify_token(token: str) -> dict | None:
    """Returns the profile for a valid, unexpired token, else None."""
    if not enabled() or not token.startswith(PREFIX):
        return None
    try:
        payload_b64, sig = token[len(PREFIX):].split(".", 1)
        if not hmac.compare_digest(sig, _sign(payload_b64)):
            return None
        data = json.loads(_unb64(payload_b64))
        if int(data.get("exp", 0)) < time.time():
            return None
    except Exception:  # noqa: BLE001
        return None
    email = principal_email()
    return {
        "user_id": "",
        "email": email,
        "email_normalized": email,
        "full_name": "Super user",
        "job_title_name": "Temporary access",
        "superuser": True,
        "raw": {},
    }


def too_many_attempts(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _attempts.get(ip, []) if now - t < WINDOW]
    _attempts[ip] = recent
    return len(recent) >= MAX_ATTEMPTS


def record_attempt(ip: str) -> None:
    _attempts.setdefault(ip, []).append(time.time())


def check_credentials(username: str, password: str) -> bool:
    ok_user = hmac.compare_digest(username.strip().lower(),
                                  Config.INVENTORY_SUPERUSER_USERNAME.strip().lower())
    ok_pass = check_password_hash(Config.INVENTORY_SUPERUSER_PASSWORD_HASH or "", password or "")
    return ok_user and ok_pass
