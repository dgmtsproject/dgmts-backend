#!/usr/bin/env python3
"""
Diagnose Instantel 2 (UM16368) alert pipeline locally or on the VPS.

Run on the VPS (recommended — CSV files live there):

  cd /root/flask-app
  source venv/bin/activate
  python scripts/diagnose_instantel2_alerts.py

  # Skip sending email (threshold check only):
  python scripts/diagnose_instantel2_alerts.py --dry-run

  # Force email to a test address:
  python scripts/diagnose_instantel2_alerts.py --email you@example.com --force
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

# Allow running from repo root or /root/flask-app
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main():
    parser = argparse.ArgumentParser(description='Diagnose Instantel 2 alerts')
    parser.add_argument('--dry-run', action='store_true', help='Do not send emails')
    parser.add_argument('--email', action='append', default=[], help='Test recipient (repeatable)')
    parser.add_argument('--force', action='store_true', help='Force resend even if already logged')
    parser.add_argument('--hours', type=int, default=24, help='Lookback window in hours (default 24)')
    args = parser.parse_args()

    print('=' * 60)
    print('Instantel 2 (UM16368) alert diagnostics')
    print('=' * 60)

    # Step 1: CSV folder
    csv_dir = '/root/root/ftp-server/Dulles Test/UM16368/CSV'
    print(f'\n[1] CSV directory: {csv_dir}')
    print(f'    exists={os.path.exists(csv_dir)}')
    if os.path.exists(csv_dir):
        files = [f for f in os.listdir(csv_dir) if f.endswith('IDFH.csv')]
        files.sort()
        print(f'    IDFH files={len(files)}')
        if files:
            print(f'    oldest={files[0]}')
            print(f'    newest={files[-1]}')

    # Step 2: Instrument lookup
    print('\n[2] Finding instrument for UM16368...')
    t0 = time.time()
    try:
        from services.instrument_route_service import find_micromate_instrument

        instrument = find_micromate_instrument('UM16368')
        print(f'    took {time.time() - t0:.2f}s')
        if not instrument:
            print('    ERROR: no instrument row found')
            return 1
        print(f"    instrument_id={instrument.get('instrument_id')}")
        print(f"    sno={instrument.get('sno')}")
        print(f"    name={instrument.get('instrument_name')}")
        print(f"    is_active={instrument.get('is_active')}")
        print(f"    alert={instrument.get('alert_value')} warn={instrument.get('warning_value')} shut={instrument.get('shutdown_value')}")
        print(f"    alert_emails={instrument.get('alert_emails')}")
    except Exception:
        print('    FAILED:')
        traceback.print_exc()
        return 1

    # Step 3: Direct CSV read
    print('\n[3] Reading UM16368 CSVs in-process...')
    t0 = time.time()
    try:
        import pytz
        from services.micromate_service import get_um16368_readings

        est = pytz.timezone('US/Eastern')
        now = datetime.now(timezone.utc).astimezone(est)
        start = now - timedelta(hours=args.hours)
        from_date = start.strftime('%Y-%m-%d %H:%M:%S')
        to_date = now.strftime('%Y-%m-%d %H:%M:%S')
        print(f'    window={from_date} -> {to_date} EST')

        result = get_um16368_readings(from_datetime=from_date, to_datetime=to_date)
        elapsed = time.time() - t0
        readings = result.get('readings') or []
        summary = result.get('summary') or {}
        print(f'    took {elapsed:.2f}s')
        print(f'    readings={len(readings)} files_processed={summary.get("files_processed")} files_found={summary.get("files_found")}')
        if result.get('errors'):
            print(f'    errors={result["errors"][:5]}')
        if readings:
            print(f'    first Time={readings[0].get("Time")}')
            print(f'    last  Time={readings[-1].get("Time")}')
            sample = readings[-1]
            print(
                f'    last PPV L/T/V='
                f'{sample.get("Longitudinal_PPV")}/'
                f'{sample.get("Transverse_PPV")}/'
                f'{sample.get("Vertical_PPV")}'
            )
        if elapsed > 25:
            print('    WARNING: CSV read is slow; Gunicorn may still time out on HTTP endpoints')
    except Exception:
        print('    FAILED:')
        traceback.print_exc()
        return 1

    # Step 4: Full alert check
    if args.dry_run:
        print('\n[4] Skipping email send (--dry-run). Threshold scan only via CSV above.')
        print('DONE')
        return 0

    print('\n[4] Running check_and_send_instantel2_alert()...')
    t0 = time.time()
    try:
        from services.micromate_service import check_and_send_instantel2_alert

        custom_emails = args.email or None
        summary = check_and_send_instantel2_alert(
            custom_emails=custom_emails,
            time_window_minutes=args.hours * 60,
            force_resend=args.force,
        )
        print(f'    took {time.time() - t0:.2f}s')
        print(f'    summary={summary}')
    except Exception:
        print('    FAILED:')
        traceback.print_exc()
        return 1

    print('\nDONE')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
