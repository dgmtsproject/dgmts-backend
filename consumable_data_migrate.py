#!/usr/bin/env python3
"""
One-time data migration for Consumable Inventory tables only:

  Supabase public.{suppliers, consumable_items, inventory_stock, consumption_logs}
    -> local Postgres inventory.{same names}

Does NOT touch purchase-management tables (inventory_items, purchase_requests, …).
Idempotent: clears the four target tables first, then copies.

Run ON THE SERVER (needs network egress to Supabase):

  export SOURCE_DSN="postgresql://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres"
  export TARGET_DSN="postgresql://dgmts_inventory_user:PASSWORD@127.0.0.1:5432/dgmts_static_db"
  python3 consumable_data_migrate.py
"""
import os
import sys

import psycopg2
import psycopg2.extras

# Parent-before-child for INSERT. DELETE happens in reverse.
TABLES = [
    "suppliers",
    "consumable_items",
    "inventory_stock",
    "consumption_logs",
]


def conv(v):
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
        for t in reversed(TABLES):
            tcur.execute(f"DELETE FROM inventory.{t}")

        counts = {}
        for t in TABLES:
            scur.execute(f"SELECT * FROM public.{t}")
            rows = scur.fetchall()
            counts[t] = len(rows)
            if not rows:
                print(f"  {t}: 0 rows")
                continue
            cols = list(rows[0].keys())
            collist = ", ".join('"' + c + '"' for c in cols)
            values = [[conv(r[c]) for c in cols] for r in rows]
            psycopg2.extras.execute_values(
                tcur,
                f"INSERT INTO inventory.{t} ({collist}) VALUES %s",
                values,
                page_size=50,
            )
            print(f"  {t}: {len(rows)} rows")

        tgt.commit()
        print("\nConsumable migration complete. Row counts:")
        for t in TABLES:
            print(f"  {t:22} {counts[t]}")
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
