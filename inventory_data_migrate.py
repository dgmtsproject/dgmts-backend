#!/usr/bin/env python3
"""
One-time data migration: Supabase (public schema) -> local Postgres (inventory schema).
Run ON THE SERVER (has psycopg2 + network egress to Supabase).

Set two connection strings, then run:

  export SOURCE_DSN="postgresql://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres"
  export TARGET_DSN="postgresql://dgmts_inventory_user:admin@127.0.0.1:5432/dgmts_static_db"
  python3 inventory_data_migrate.py

SOURCE_DSN = Supabase "Session pooler" URI (Dashboard -> Connect -> Session pooler),
with your database password filled in.
TARGET_DSN = the local inventory role (password you set in inventory_db_setup.sql).

Idempotent: safe to re-run — it clears the target tables first each time.
"""
import os
import sys
import psycopg2
import psycopg2.extras

# Parent-before-child order (FK-safe for INSERT). Delete happens in reverse.
TABLES = [
    "branches",
    "departments",
    "users",
    "inventory_items",
    "purchase_supervisor_job_titles",
    "inventory_po_action_permissions",
    "inventory_user_tab_permissions",
    "ms_directory_departments",
    "ms_directory_employees",
    "ms_directory_meta",
    "inventory_logs",        # references inventory_items + users
    "purchase_requests",     # references branches + departments
]


def conv(v):
    """Wrap dict/list so they bind as jsonb (e.g. ms_directory_employees.raw)."""
    if isinstance(v, (dict, list)):
        return psycopg2.extras.Json(v)
    return v


def main():
    source_dsn = os.environ.get("SOURCE_DSN")
    target_dsn = os.environ.get("TARGET_DSN")
    if not source_dsn or not target_dsn:
        sys.exit("Set SOURCE_DSN and TARGET_DSN environment variables first.")

    src = psycopg2.connect(source_dsn)
    tgt = psycopg2.connect(target_dsn)
    scur = src.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    tcur = tgt.cursor()
    tcur.execute("SET search_path TO inventory")

    try:
        # 1) Clear existing (seed) rows, children first so FKs are satisfied.
        for t in reversed(TABLES):
            tcur.execute(f"DELETE FROM inventory.{t}")

        # 2) Copy each table, columns by name (order-independent).
        counts = {}
        for t in TABLES:
            scur.execute(f"SELECT * FROM public.{t}")
            rows = scur.fetchall()
            counts[t] = len(rows)
            if not rows:
                continue
            cols = list(rows[0].keys())
            collist = ", ".join('"' + c + '"' for c in cols)
            values = [[conv(r[c]) for c in cols] for r in rows]
            psycopg2.extras.execute_values(
                tcur,
                f"INSERT INTO inventory.{t} ({collist}) VALUES %s",
                values,
                page_size=500,
            )
            print(f"  {t}: {len(rows)} rows")

        tgt.commit()
        print("\nMigration complete. Row counts:")
        for t in TABLES:
            print(f"  {t:35} {counts[t]}")
    except Exception as e:  # noqa: BLE001
        tgt.rollback()
        print(f"\nMIGRATION FAILED (rolled back): {e}")
        raise
    finally:
        scur.close()
        tcur.close()
        src.close()
        tgt.close()


if __name__ == "__main__":
    main()
