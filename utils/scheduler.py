import atexit
import os
import schedule
import time
import threading
from services.sensor_service import fetch_and_store_all_sensor_data
from services.alert_service import (
    check_and_send_seismograph_alert,
    check_and_send_smg3_seismograph_alert,
    check_and_send_seismograph_instrument_13453_alert,
)
from services.rock_seismograph_service import check_and_send_rock_seismograph_alert
# Note: check_and_send_rock_seismograph_alert_test is only for local testing via test.py
from services.micromate_service import check_and_send_micromate_alert, check_and_send_instantel2_alert
from services.duration_alert_service import check_and_send_all_duration_alerts
from config import Config

_scheduler_thread = None
_scheduler_lock_file = None
_job_locks = {
    'instantel1': threading.Lock(),
    'instantel2': threading.Lock(),
}


def _run_exclusive(lock_key, fn, *args, **kwargs):
    """Skip if the previous run of this job is still in progress."""
    lock = _job_locks[lock_key]
    if not lock.acquire(blocking=False):
        print(f"[scheduler] Skipping {lock_key}: previous run still in progress")
        return None
    try:
        return fn(*args, **kwargs)
    finally:
        lock.release()


def _run_instantel1():
    return _run_exclusive('instantel1', check_and_send_micromate_alert)


def _run_instantel2():
    return _run_exclusive('instantel2', check_and_send_instantel2_alert)


def _run_email_outbox():
    """Retry pending outbox emails (payment notifications) so they are never lost."""
    try:
        from services.email_outbox_service import process_pending
        process_pending()
    except Exception as e:
        print(f"[scheduler] email outbox retry error: {e}")


def run_scheduler():
    """Run the scheduler in a background thread"""
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"[scheduler] run_pending error: {e}")
        time.sleep(30)


def setup_scheduled_tasks():
    """Setup all scheduled tasks"""
    # Per-instrument Syscom seismograph jobs
    schedule.every().minute.do(check_and_send_seismograph_alert)
    schedule.every().minute.do(check_and_send_smg3_seismograph_alert)
    schedule.every().minute.do(check_and_send_seismograph_instrument_13453_alert)

    # Instantel FTP data arrives ~every 30 min; checking every 5 min is enough
    # and avoids overlapping CSV/email work that starves Gunicorn HTTP workers.
    schedule.every(5).minutes.do(_run_instantel1)
    schedule.every(5).minutes.do(_run_instantel2)
    schedule.every(5).minutes.do(check_and_send_all_duration_alerts)

    # Retry any payment/notification emails that failed to send inline
    # (rows in email_outbox with status='pending'). Lightweight; single worker.
    schedule.every(2).minutes.do(_run_email_outbox)

    for instrument_id in Config.ROCK_SEISMOGRAPH_INSTRUMENTS.keys():
        schedule.every().minute.do(check_and_send_rock_seismograph_alert, instrument_id)

    print("⚠️  TILTMETER DATA FETCHING DISABLED - No data for nodes")
    print("✅ Instantel alert jobs scheduled every 5 minutes (single-worker scheduler)")


def _acquire_scheduler_lock():
    """
    Ensure only ONE Gunicorn/Passenger worker runs the scheduler.
    Without this, every worker starts Instantel/Syscom jobs and HTTP requests time out.
    """
    global _scheduler_lock_file
    lock_path = os.environ.get('DGMTS_SCHEDULER_LOCK', '/tmp/dgmts_scheduler.lock')
    try:
        lock_file = open(lock_path, 'w')
    except OSError as e:
        print(f"[scheduler] Could not open lock file {lock_path}: {e}")
        return False

    try:
        import fcntl
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        # Windows / no fcntl — allow start (dev only)
        print("[scheduler] fcntl unavailable; starting scheduler without cross-process lock")
    except BlockingIOError:
        lock_file.close()
        print("[scheduler] Another worker already owns the scheduler lock — skipping")
        return False
    except OSError as e:
        lock_file.close()
        print(f"[scheduler] flock failed: {e} — skipping")
        return False

    lock_file.write(str(os.getpid()))
    lock_file.flush()
    _scheduler_lock_file = lock_file

    def _release():
        try:
            if _scheduler_lock_file:
                _scheduler_lock_file.close()
        except Exception:
            pass

    atexit.register(_release)
    return True


def start_scheduler():
    """Start the scheduler in a background thread (once across all workers)."""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return _scheduler_thread

    if not _acquire_scheduler_lock():
        return None

    setup_scheduled_tasks()
    _scheduler_thread = threading.Thread(target=run_scheduler, daemon=True, name='dgmts-scheduler')
    _scheduler_thread.start()
    print(f"[scheduler] Started in PID {os.getpid()}")
    return _scheduler_thread
