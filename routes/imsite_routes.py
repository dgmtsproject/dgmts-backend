"""
Imsite instrumentation DB API — local Postgres (dgmts_db) CRUD.

Same contract as /api/dgmts-static/data so C:/Work/dgmts can swap the Supabase
client for a thin shim (see frontend/imsiteDbClient.ts.example).

- GET  /api/imsite/health
- POST /api/imsite/data   — select / insert / update / delete / upsert
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time
from decimal import Decimal

from flask import Blueprint, jsonify, request

from models.imsite_db import imsite_db

imsite_bp = Blueprint("imsite", __name__, url_prefix="/api/imsite")

ALLOWED_TABLES = frozenset({
    "instruments",
    "Projects",
    "ProjectUsers",
    "users",
    "sensor_readings",
    "reference_values",
    "time_based_reference_values",
    "sent_alerts",
    "sent_alert_logs",
    "alarms",
    "alarms_new",
})

IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _ident(name: str) -> str:
    if not name or not IDENT.match(name):
        raise ValueError(f"Invalid identifier: {name!r}")
    return name


def _qident(name: str) -> str:
    n = _ident(name)
    return '"' + n.replace('"', '""') + '"'


def _format_select_columns(cols) -> str:
    if not cols or (isinstance(cols, str) and cols.strip() == "*"):
        return "*"
    if not isinstance(cols, str):
        return "*"
    out = []
    for part in cols.split(","):
        p = part.strip()
        if not p or p == "*":
            continue
        # Reject PostgREST embeds like Projects(id, name) — not supported here.
        if "(" in p or ")" in p:
            raise ValueError(
                f"Embedded/relational select not supported: {p!r}. "
                "Query related tables separately."
            )
        out.append(_qident(p))
    return ", ".join(out) if out else "*"


def _where_clauses(filters, params):
    parts = []
    if not filters:
        return "TRUE"
    for f in filters:
        op = f.get("op")
        qcol = _qident(f["col"])
        if op == "eq":
            val = f.get("val")
            if val is None:
                parts.append(f"{qcol} IS NULL")
            else:
                parts.append(f"{qcol} = %s")
                params.append(val)
        elif op == "neq":
            val = f.get("val")
            if val is None:
                parts.append(f"{qcol} IS NOT NULL")
            else:
                parts.append(f"{qcol} <> %s")
                params.append(val)
        elif op == "in":
            vals = f.get("vals") or []
            if not vals:
                parts.append("FALSE")
            else:
                ph = ", ".join(["%s"] * len(vals))
                parts.append(f"{qcol} IN ({ph})")
                params.extend(vals)
        elif op == "gt":
            parts.append(f"{qcol} > %s")
            params.append(f.get("val"))
        elif op == "gte":
            parts.append(f"{qcol} >= %s")
            params.append(f.get("val"))
        elif op == "lt":
            parts.append(f"{qcol} < %s")
            params.append(f.get("val"))
        elif op == "lte":
            parts.append(f"{qcol} <= %s")
            params.append(f.get("val"))
        elif op == "is":
            # PostgREST-style IS NULL (val ignored / expected null)
            parts.append(f"{qcol} IS NULL")
        elif op == "not_is":
            parts.append(f"{qcol} IS NOT NULL")
        else:
            raise ValueError(f"Unsupported filter op: {op}")
    return " AND ".join(parts) if parts else "TRUE"


def _order_clause(order_list):
    if not order_list:
        return ""
    bits = []
    for o in order_list:
        qcol = _qident(o["col"])
        direction = "ASC" if o.get("asc", True) else "DESC"
        bits.append(f"{qcol} {direction}")
    return "ORDER BY " + ", ".join(bits)


def _jsonable(obj):
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, memoryview):
        return bytes(obj).hex()
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj).hex()
    return str(obj)


def _ok(data, status=200):
    return jsonify({"data": _jsonable(data), "error": None}), status


def _err(message, status=400):
    return jsonify({"data": None, "error": {"message": message}}), status


@imsite_bp.route("/health", methods=["GET", "OPTIONS"])
def imsite_health():
    if request.method == "OPTIONS":
        return "", 200, {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
        }
    ok = imsite_db.healthcheck()
    return jsonify({"status": "ok" if ok else "degraded", "db": ok}), (200 if ok else 503)


@imsite_bp.route("/projects", methods=["GET", "OPTIONS"])
def imsite_projects():
    """Simple GET for Postman smoke tests — lists rows from public."Projects"."""
    if request.method == "OPTIONS":
        return "", 200, {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
        }
    try:
        rows = imsite_db.query(
            'SELECT * FROM "Projects" ORDER BY id ASC'
        )
        return _ok(rows)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return _err(str(e), 500)


@imsite_bp.route("/instruments", methods=["GET", "OPTIONS"])
def imsite_instruments():
    """Simple GET for Postman — lists instruments (optional ?project_id=)."""
    if request.method == "OPTIONS":
        return "", 200, {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
        }
    try:
        project_id = request.args.get("project_id")
        if project_id is not None and project_id != "":
            rows = imsite_db.query(
                "SELECT * FROM instruments WHERE project_id = %s ORDER BY instrument_id ASC",
                (project_id,),
            )
        else:
            rows = imsite_db.query(
                "SELECT * FROM instruments ORDER BY instrument_id ASC"
            )
        return _ok(rows)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return _err(str(e), 500)


@imsite_bp.route("/data", methods=["POST", "OPTIONS"])
def imsite_data():
    if request.method == "OPTIONS":
        return "", 200, {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
        }

    body = request.get_json(silent=True) or {}
    action = body.get("action")
    table = body.get("table")

    try:
        if not table or table not in ALLOWED_TABLES:
            return _err(f"Table not allowed: {table}", 400)

        if action == "select":
            cols = body.get("columns") or "*"
            if cols != "*":
                if not isinstance(cols, str):
                    return _err("Invalid columns", 400)
                for part in cols.split(","):
                    part = part.strip()
                    if part == "*" or not part:
                        continue
                    if "(" in part or ")" in part:
                        return _err(
                            f"Embedded/relational select not supported: {part!r}. "
                            "Query related tables separately.",
                            400,
                        )
                    _ident(part)

            select_sql = _format_select_columns(cols) if isinstance(cols, str) else "*"
            filters = body.get("filters") or []
            params = []
            where_sql = _where_clauses(filters, params)
            order_sql = _order_clause(body.get("order") or [])
            limit = body.get("limit")
            offset = body.get("offset") or 0

            q = f"SELECT {select_sql} FROM {_qident(table)} WHERE {where_sql}"
            if order_sql:
                q += " " + order_sql
            if limit is not None:
                q += f" LIMIT {int(limit)}"
            if offset:
                q += f" OFFSET {int(offset)}"

            rows = imsite_db.query(q, params)
            single = body.get("single")
            maybe_single = body.get("maybe_single")

            if single:
                if len(rows) != 1:
                    return _err(
                        "JSON object requested, multiple (or no) rows returned",
                        406,
                    )
                return _ok(rows[0])

            if maybe_single:
                if len(rows) > 1:
                    return _err(
                        "JSON object requested, multiple rows returned",
                        406,
                    )
                return _ok(rows[0] if rows else None)

            return _ok(rows)

        if action == "insert":
            rows_in = body.get("rows") or []
            if not rows_in or not isinstance(rows_in, list):
                return _err("rows required", 400)

            want_returning = bool(body.get("returning", True))
            all_out = []
            for row in rows_in:
                if not isinstance(row, dict):
                    raise ValueError("Each row must be an object")
                key_list = list(row.keys())
                keys_quoted = [_qident(k) for k in key_list]
                vals = [row[k] for k in key_list]
                cols_sql = ", ".join(keys_quoted)
                ph = ", ".join(["%s"] * len(vals))
                q = f"INSERT INTO {_qident(table)} ({cols_sql}) VALUES ({ph})"
                if want_returning:
                    q += " RETURNING *"
                    all_out.extend(imsite_db.execute(q, vals, returning=True))
                else:
                    imsite_db.execute(q, vals, returning=False)

            if want_returning and len(rows_in) == 1:
                return _ok(all_out[0] if all_out else None)
            if want_returning:
                return _ok(all_out)
            return _ok(None)

        if action == "update":
            patch = body.get("patch") or {}
            filters = body.get("filters") or []
            want_returning = bool(body.get("returning", False))
            if not patch:
                return _err("patch required", 400)
            if not filters:
                return _err("At least one filter is required for update", 400)
            sets = []
            set_params = []
            for k, v in patch.items():
                sets.append(f"{_qident(k)} = %s")
                set_params.append(v)

            where_params = []
            where_sql = _where_clauses(filters, where_params)
            params = set_params + where_params
            q = f'UPDATE {_qident(table)} SET {", ".join(sets)} WHERE {where_sql}'
            if want_returning:
                q += " RETURNING *"
                out = imsite_db.execute(q, params, returning=True)
                return _ok(out)
            imsite_db.execute(q, params, returning=False)
            return _ok(None)

        if action == "delete":
            filters = body.get("filters") or []
            if not filters:
                return _err("At least one filter is required for delete", 400)
            want_returning = bool(body.get("returning", False))
            params = []
            where_sql = _where_clauses(filters, params)
            q = f"DELETE FROM {_qident(table)} WHERE {where_sql}"
            if want_returning:
                q += " RETURNING *"
                out = imsite_db.execute(q, params, returning=True)
                return _ok(out)
            imsite_db.execute(q, params, returning=False)
            return _ok(None)

        if action == "upsert":
            rows_in = body.get("rows") or []
            on_conflict = body.get("on_conflict") or ""
            if not rows_in:
                return _err("rows required", 400)
            cnames = []
            for c in on_conflict.split(","):
                s = c.strip()
                if s:
                    cnames.append(_ident(s))
            if not cnames:
                return _err("on_conflict is required for upsert", 400)

            key_list = list(rows_in[0].keys())
            for r in rows_in[1:]:
                if list(r.keys()) != key_list:
                    return _err("All upsert rows must share columns", 400)

            key_ids = [_ident(k) for k in key_list]
            cols_sql = ", ".join(_qident(k) for k in key_ids)
            flat = []
            ph_groups = []
            for row in rows_in:
                ph_groups.append("(" + ",".join(["%s"] * len(key_list)) + ")")
                flat.extend([row[k] for k in key_list])

            q = f'INSERT INTO {_qident(table)} ({cols_sql}) VALUES {", ".join(ph_groups)}'
            conflict_sql = ", ".join(_qident(c) for c in cnames)
            cset = set(cnames)
            non_conflict = [k for k in key_ids if k not in cset]
            if not non_conflict:
                q += f" ON CONFLICT ({conflict_sql}) DO NOTHING"
            else:
                set_parts = [f"{_qident(k)} = EXCLUDED.{_qident(k)}" for k in non_conflict]
                q += f' ON CONFLICT ({conflict_sql}) DO UPDATE SET {", ".join(set_parts)}'
            q += " RETURNING *"

            out = imsite_db.query(q, flat)
            return _ok(out)

        return _err(f"Unknown action: {action}", 400)

    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        return _err(str(e), 500)
