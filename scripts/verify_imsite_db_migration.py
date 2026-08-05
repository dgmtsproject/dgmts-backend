#!/usr/bin/env python3
"""Compare exact row counts: Supabase source vs local dgmts_db."""
from __future__ import annotations

import os
import sys

import paramiko

VPS_HOST = os.environ["VPS_HOST"]
VPS_USER = os.environ.get("VPS_USER", "root")
VPS_PASS = os.environ["VPS_PASS"]
SB_PASS = os.environ["SB_PASS"]
SB_POOLER = os.environ.get("SB_POOLER", "aws-0-ap-southeast-1.pooler.supabase.com")
SB_USER = os.environ.get("SB_USER", "postgres.xmhiocoinswgxvqokuzd")

COUNT_SQL = r"""
SELECT 'alarms' AS t, count(*) FROM alarms
UNION ALL SELECT 'alarms_new', count(*) FROM alarms_new
UNION ALL SELECT 'instruments', count(*) FROM instruments
UNION ALL SELECT 'Projects', count(*) FROM "Projects"
UNION ALL SELECT 'ProjectUsers', count(*) FROM "ProjectUsers"
UNION ALL SELECT 'reference_values', count(*) FROM reference_values
UNION ALL SELECT 'sensor_readings', count(*) FROM sensor_readings
UNION ALL SELECT 'sent_alert_logs', count(*) FROM sent_alert_logs
UNION ALL SELECT 'sent_alerts', count(*) FROM sent_alerts
UNION ALL SELECT 'time_based_reference_values', count(*) FROM time_based_reference_values
UNION ALL SELECT 'users', count(*) FROM users
ORDER BY 1;
"""


def run(client, cmd, timeout=300):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(VPS_HOST, username=VPS_USER, password=VPS_PASS, timeout=30)

    # Upload SQL via SFTP to preserve quoted identifiers.
    sftp = client.open_sftp()
    with sftp.file("/tmp/count_tables.sql", "w") as f:
        f.write(COUNT_SQL)
    sftp.close()

    src_env = (
        f"PGPASSWORD='{SB_PASS}' PGHOST={SB_POOLER} PGPORT=5432 "
        f"PGDATABASE=postgres PGUSER={SB_USER} PGSSLMODE=require"
    )

    print("=== SOURCE (Supabase) ===")
    code, out, err = run(client, f"{src_env} psql -f /tmp/count_tables.sql")
    print(out)
    if err.strip():
        print("STDERR:", err[:800])

    print("=== TARGET (dgmts_db) ===")
    code, out, err = run(client, "sudo -u postgres psql -d dgmts_db -f /tmp/count_tables.sql")
    print(out)
    if err.strip():
        print("STDERR:", err[:800])

    # Append connection docs to flask-app .env if missing.
    code, out, err = run(
        client,
        "grep -E '^DB_NAME=|^IMSITE_DB_NAME=' /root/flask-app/.env || true",
    )
    if "DB_NAME=" not in out and "IMSITE_DB_NAME=" not in out:
        append = r"""
# Local Postgres copy of imsite Supabase (xmhiocoinswgxvqokuzd) — dump/restore only; app still uses SUPABASE_* until cutover
IMSITE_DB_HOST=127.0.0.1
IMSITE_DB_PORT=5432
IMSITE_DB_NAME=dgmts_db
IMSITE_DB_USER=dgmts_user
IMSITE_DB_PASSWORD=root123
"""
        sftp = client.open_sftp()
        with sftp.file("/root/flask-app/.env", "a") as f:
            f.write(append)
        sftp.close()
        print("Appended IMSITE_DB_* to /root/flask-app/.env")
    else:
        print("IMSITE/DB env already present:", out.strip())

    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
