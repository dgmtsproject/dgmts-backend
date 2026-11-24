"""Maps the workflow's database errors ("CODE: message") to JSON the frontend understands."""

import psycopg2
from flask import jsonify

CODES = {
    "VALIDATION": 400,
    "NOT_ALLOWED": 403,
    "NOT_IN_DIRECTORY": 403,
    "INVALID_TRANSITION": 409,
}


def split_message(text: str) -> tuple[str, str] | None:
    head, sep, rest = (text or "").partition(":")
    head = head.strip()
    if sep and head in CODES:
        return head, rest.strip() or head
    return None


def error_response(code: str, message: str, status: int):
    return jsonify({"code": code, "message": message}), status


def from_db_error(err: Exception):
    """RAISE EXCEPTION 'CODE: text' -> {code, message}; anything else is hidden behind a generic 500."""
    if isinstance(err, psycopg2.errors.RaiseException):
        text = getattr(getattr(err, "diag", None), "message_primary", None) or str(err)
        parsed = split_message(text)
        if parsed:
            return error_response(parsed[0], parsed[1], CODES[parsed[0]])
    print(f"[tasks] database error: {err}")
    return error_response("UNKNOWN", "Something went wrong. Please try again.", 500)
