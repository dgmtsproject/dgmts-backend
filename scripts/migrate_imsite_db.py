#!/usr/bin/env python3
"""
One-shot: SSH to VPS, ensure dgmts_db exists, pg_dump Supabase public schema,
pg_restore into dgmts_db, grant privileges, print row-count comparison.

Required env:
  VPS_HOST, VPS_USER, VPS_PASS
  SB_PASS   (Supabase DB password for xmhiocoinswgxvqokuzd)
  TARGET_PASS (password for local dgmts_user)

Optional env:
  SB_REF (default xmhiocoinswgxvqokuzd)
  SB_POOLER (default aws-0-us-east-1.pooler.supabase.com)
  TARGET_DB / TARGET_USER (default dgmts_db / dgmts_user)
"""
from __future__ import annotations

import os
import sys

import paramiko

VPS_HOST = os.environ["VPS_HOST"]
VPS_USER = os.environ.get("VPS_USER", "root")
VPS_PASS = os.environ["VPS_PASS"]

SB_PASS = os.environ["SB_PASS"]
SB_REF = os.environ.get("SB_REF", "xmhiocoinswgxvqokuzd")
SB_DIRECT = f"db.{SB_REF}.supabase.co"
SB_POOLER = os.environ.get("SB_POOLER", "aws-0-ap-southeast-1.pooler.supabase.com")
SB_POOLER_USER = f"postgres.{SB_REF}"

TARGET_DB = os.environ.get("TARGET_DB", "dgmts_db")
TARGET_USER = os.environ.get("TARGET_USER", "dgmts_user")
TARGET_PASS = os.environ["TARGET_PASS"]
DUMP_PATH = "/tmp/imsite_supabase_public.dump"


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
    print(f"\n>>> {cmd[:220]}{'...' if len(cmd) > 220 else ''}")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    code = stdout.channel.recv_exit_status()
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print("STDERR:", err[:2000].rstrip())
    print(f"[exit {code}]")
    return code, out, err


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to {VPS_USER}@{VPS_HOST} ...")
    client.connect(VPS_HOST, username=VPS_USER, password=VPS_PASS, timeout=30)

    run(
        client,
        (
            "sudo -u postgres psql -v ON_ERROR_STOP=1 -c "
            "\"DO \\$\\$ BEGIN "
            f"CREATE ROLE {TARGET_USER} LOGIN PASSWORD '{TARGET_PASS}'; "
            "EXCEPTION WHEN duplicate_object THEN "
            f"ALTER ROLE {TARGET_USER} WITH LOGIN PASSWORD '{TARGET_PASS}'; "
            "END \\$\\$;\""
        ),
    )
    _, out, _ = run(
        client,
        f"sudo -u postgres psql -tc \"SELECT 1 FROM pg_database WHERE datname='{TARGET_DB}'\"",
    )
    if "1" not in out:
        run(
            client,
            f"sudo -u postgres psql -v ON_ERROR_STOP=1 -c "
            f"\"CREATE DATABASE {TARGET_DB} OWNER {TARGET_USER};\"",
        )
    run(
        client,
        f"sudo -u postgres psql -c "
        f"\"GRANT ALL PRIVILEGES ON DATABASE {TARGET_DB} TO {TARGET_USER}; "
        f"ALTER DATABASE {TARGET_DB} OWNER TO {TARGET_USER};\"",
    )

    _, out, _ = run(
        client,
        f"getent ahostsv4 {SB_DIRECT} | awk '{{print $1; exit}}'",
    )
    hostaddr = out.strip().splitlines()[0].strip() if out.strip() else ""
    print(f"Resolved IPv4: {hostaddr!r}")

    source_ok = False
    source_env = ""
    if hostaddr:
        test = (
            f"PGPASSWORD='{SB_PASS}' psql "
            f"\"host={SB_DIRECT} hostaddr={hostaddr} port=5432 dbname=postgres "
            f"user=postgres sslmode=require connect_timeout=20\" "
            f"-c \"SELECT current_database();\""
        )
        code, _, _ = run(client, test, timeout=60)
        if code == 0:
            source_ok = True
            source_env = (
                f"PGPASSWORD='{SB_PASS}' "
                f"PGHOST={SB_DIRECT} PGHOSTADDR={hostaddr} PGPORT=5432 "
                f"PGDATABASE=postgres PGUSER=postgres PGSSLMODE=require"
            )

    if not source_ok:
        test = (
            f"PGPASSWORD='{SB_PASS}' psql "
            f"\"host={SB_POOLER} port=5432 dbname=postgres "
            f"user={SB_POOLER_USER} sslmode=require connect_timeout=20\" "
            f"-c \"SELECT current_database();\""
        )
        code, _, _ = run(client, test, timeout=60)
        if code != 0:
            print("FATAL: cannot connect to Supabase Postgres")
            client.close()
            return 1
        source_env = (
            f"PGPASSWORD='{SB_PASS}' "
            f"PGHOST={SB_POOLER} PGPORT=5432 "
            f"PGDATABASE=postgres PGUSER={SB_POOLER_USER} PGSSLMODE=require"
        )

    run(
        client,
        (
            f"{source_env} psql -c "
            "\"SELECT relname AS table, n_live_tup AS approx_rows "
            "FROM pg_stat_user_tables WHERE schemaname='public' "
            "ORDER BY n_live_tup DESC;\""
        ),
        timeout=60,
    )

    print("\n=== pg_dump (this may take a while) ===")
    dump_cmd = (
        f"{source_env} pg_dump "
        f"--format=custom --no-owner --no-acl --schema=public "
        f"--file={DUMP_PATH}"
    )
    code, _, _ = run(client, dump_cmd, timeout=3600)
    if code != 0:
        print("FATAL: pg_dump failed")
        client.close()
        return 1
    run(client, f"ls -lh {DUMP_PATH}")

    print("\n=== prepare target + pg_restore ===")
    run(
        client,
        (
            f"sudo -u postgres psql -d {TARGET_DB} -v ON_ERROR_STOP=1 -c "
            "\"DROP SCHEMA public CASCADE; CREATE SCHEMA public; "
            f"GRANT ALL ON SCHEMA public TO {TARGET_USER}; "
            f"GRANT ALL ON SCHEMA public TO public;\""
        ),
    )
    run(
        client,
        f"sudo -u postgres pg_restore --no-owner --no-acl -d {TARGET_DB} {DUMP_PATH}",
        timeout=3600,
    )
    run(
        client,
        (
            f"sudo -u postgres psql -d {TARGET_DB} -c "
            "\"SELECT count(*) AS public_tables FROM pg_tables WHERE schemaname='public';\""
        ),
    )
    run(
        client,
        (
            f"sudo -u postgres psql -d {TARGET_DB} -v ON_ERROR_STOP=1 -c "
            f"\"ALTER SCHEMA public OWNER TO {TARGET_USER}; "
            f"GRANT ALL ON SCHEMA public TO {TARGET_USER}; "
            f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {TARGET_USER}; "
            f"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {TARGET_USER}; "
            f"GRANT ALL ON ALL FUNCTIONS IN SCHEMA public TO {TARGET_USER}; "
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT ALL ON TABLES TO {TARGET_USER}; "
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT ALL ON SEQUENCES TO {TARGET_USER};\""
        ),
    )
    run(
        client,
        (
            f"sudo -u postgres psql -d {TARGET_DB} -c "
            "\"DO \\$\\$ DECLARE r record; BEGIN "
            "FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP "
            f"EXECUTE format('ALTER TABLE public.%I OWNER TO {TARGET_USER}', r.tablename); "
            "END LOOP; "
            "FOR r IN SELECT sequence_name FROM information_schema.sequences "
            "WHERE sequence_schema='public' LOOP "
            f"EXECUTE format('ALTER SEQUENCE public.%I OWNER TO {TARGET_USER}', r.sequence_name); "
            "END LOOP; END \\$\\$;\""
        ),
    )

    print("\n=== target row counts ===")
    run(
        client,
        (
            f"sudo -u postgres psql -d {TARGET_DB} -c "
            "\"SELECT relname AS table, n_live_tup AS approx_rows "
            "FROM pg_stat_user_tables WHERE schemaname='public' "
            "ORDER BY n_live_tup DESC;\""
        ),
    )
    run(
        client,
        (
            f"sudo -u postgres psql -d {TARGET_DB} -c "
            "\"SELECT 'instruments' AS t, count(*) FROM instruments "
            "UNION ALL SELECT 'sensor_readings', count(*) FROM sensor_readings "
            "UNION ALL SELECT 'users', count(*) FROM users "
            "UNION ALL SELECT 'sent_alerts', count(*) FROM sent_alerts "
            "UNION ALL SELECT 'sent_alert_logs', count(*) FROM sent_alert_logs "
            "ORDER BY 1;\""
        ),
        timeout=600,
    )
    run(
        client,
        (
            f"PGPASSWORD='{TARGET_PASS}' psql "
            f"\"host=127.0.0.1 port=5432 dbname={TARGET_DB} user={TARGET_USER}\" "
            f"-c \"SELECT current_database(), current_user, "
            f"(SELECT count(*) FROM pg_tables WHERE schemaname='public') AS tables;\""
        ),
    )

    print(
        "\n=== CONNECTION ENV (for later app cutover) ===\n"
        f"DB_HOST=127.0.0.1\n"
        f"DB_PORT=5432\n"
        f"DB_NAME={TARGET_DB}\n"
        f"DB_USER={TARGET_USER}\n"
        f"DB_PASSWORD=<set TARGET_PASS>\n"
    )
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
