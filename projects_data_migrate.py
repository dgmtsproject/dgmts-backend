#!/usr/bin/env python3
"""
One-time data migration: Supabase (public.archive_*) -> local Postgres (projects schema).
Run ON THE SERVER (has psycopg2 + network egress to Supabase).

  export SOURCE_DSN="postgresql://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres"
  export TARGET_DSN="postgresql://dgmts_projects_user:<password>@127.0.0.1:5432/dgmts_static_db"
  python3 projects_data_migrate.py            # check + copy
  python3 projects_data_migrate.py --check    # only compare structure and counts

SOURCE_DSN = the Supabase *Session pooler* URI of the project that holds the archive_* tables.
TARGET_DSN = the dgmts_projects_user role (see projects_db_setup.sql).

Safe to re-run: it clears the target tables first, inside one transaction, so
a failure leaves the target unchanged. The Supabase source is only READ.
"""
import sys

import psycopg2
import psycopg2.extras

# Parent-before-child (FK-safe for INSERT). Delete happens in reverse.
TABLES = [
    "archive_service_types",
    "archive_projects",
    "archive_project_pocs",
    "archive_project_team",
    "archive_project_vendors",
    "archive_project_attachments",
    "archive_project_invoices",
    "archive_project_source_folders",
    "archive_project_service_types",
]
# source table -> target table (app access flags created for the standalone app)
OPTIONAL = {"projects_user_access": "user_access"}
PAGE = 2000


def conv(v, udt):
    """jsonb columns arrive as dict/list; bind them as JSON (arrays stay arrays)."""
    if udt in ("jsonb", "json") and isinstance(v, (dict, list)):
        return psycopg2.extras.Json(v)
    return v


def columns(cur, schema, table):
    cur.execute(
        "SELECT column_name, data_type, udt_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
        (schema, table),
    )
    return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def table_exists(cur, schema, table):
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
                (schema, table))
    return cur.fetchone() is not None


def main():
    import os
    check_only = "--check" in sys.argv
    source_dsn, target_dsn = os.environ.get("SOURCE_DSN"), os.environ.get("TARGET_DSN")
    if not source_dsn or not target_dsn:
        sys.exit("Set SOURCE_DSN and TARGET_DSN environment variables first.")

    src = psycopg2.connect(source_dsn)
    src.set_session(readonly=True)
    tgt = psycopg2.connect(target_dsn)
    scur, tcur = src.cursor(), tgt.cursor()
    tcur.execute("SET search_path TO projects")

    pairs = [(t, t) for t in TABLES]
    for s_name, t_name in OPTIONAL.items():
        if table_exists(scur, "public", s_name):
            pairs.append((s_name, t_name))

    # ---- 1) Structure check: every source column must exist in the target ----
    problems, notes = [], []
    for s_name, t_name in pairs:
        sc, tc = columns(scur, "public", s_name), columns(tcur, "projects", t_name)
        if not tc:
            problems.append(f"{t_name}: table missing in target (run projects_schema.sql)")
            continue
        for col, (dtype, udt) in sc.items():
            if col not in tc:
                problems.append(f"{t_name}.{col}: exists in Supabase but not in target")
            elif tc[col][1] != udt:
                notes.append(f"{t_name}.{col}: type differs (source {udt}, target {tc[col][1]})")
        for col in tc:
            if col not in sc:
                notes.append(f"{t_name}.{col}: only in target (will be left at its default)")
    for n in notes:
        print("note:", n)
    if problems:
        print("\nSTRUCTURE PROBLEMS - fix the target schema first:")
        for p in problems:
            print("  -", p)
        sys.exit(1)

    # ---- 2) Counts ----
    src_counts = {}
    for s_name, _ in pairs:
        scur.execute(f'SELECT count(*) FROM public."{s_name}"')
        src_counts[s_name] = scur.fetchone()[0]
    print("\nSource row counts:")
    for s_name, _ in pairs:
        print(f"  {s_name:36} {src_counts[s_name]:>8}")
    if check_only:
        print("\n--check: nothing copied.")
        return

    try:
        for _, t_name in reversed(pairs):
            tcur.execute(f"DELETE FROM projects.{t_name}")

        for s_name, t_name in pairs:
            tcols = columns(tcur, "projects", t_name)
            scols = list(columns(scur, "public", s_name))
            cols = [c for c in scols if c in tcols]
            collist = ", ".join(f'"{c}"' for c in cols)
            cursor = src.cursor(name=f"cur_{s_name}")  # server-side, streams big tables
            cursor.itersize = PAGE
            cursor.execute(f'SELECT {collist} FROM public."{s_name}"')
            n = 0
            while True:
                rows = cursor.fetchmany(PAGE)
                if not rows:
                    break
                psycopg2.extras.execute_values(
                    tcur,
                    f"INSERT INTO projects.{t_name} ({collist}) VALUES %s",
                    [[conv(v, tcols[c][1]) for v, c in zip(r, cols)] for r in rows],
                    page_size=PAGE,
                )
                n += len(rows)
            cursor.close()
            print(f"  copied {t_name}: {n}")

        tcur.execute(
            "SELECT setval(pg_get_serial_sequence('projects.archive_service_types','id'), "
            "COALESCE((SELECT max(id) FROM projects.archive_service_types), 1))"
        )

        # ---- 3) Verify counts before committing ----
        bad = []
        for s_name, t_name in pairs:
            tcur.execute(f"SELECT count(*) FROM projects.{t_name}")
            got = tcur.fetchone()[0]
            if got != src_counts[s_name]:
                bad.append(f"{t_name}: source {src_counts[s_name]} vs target {got}")
        if bad:
            raise RuntimeError("Row count mismatch: " + "; ".join(bad))

        tgt.commit()
        print("\nMigration complete; all row counts match.")
    except Exception as e:  # noqa: BLE001
        tgt.rollback()
        print(f"\nMIGRATION FAILED (rolled back, target unchanged): {e}")
        raise
    finally:
        scur.close(); tcur.close(); src.close(); tgt.close()


if __name__ == "__main__":
    main()
