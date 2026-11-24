"""
Background jobs for the Task Manager, run by a daemon thread inside the Flask process:

  * every TASKS_OUTBOX_INTERVAL_SECONDS  send queued emails
  * every TASKS_EMPLOYEE_SYNC_MINUTES    refresh the employee directory
  * every tick                           run the 8am due digest (once per day, guarded in SQL)

Only one gunicorn worker may run them (file lock), the same approach as utils/scheduler.py.
"""

from __future__ import annotations

import os
import threading
import time

from config import Config

_started = False
_lock_file = None  # kept open for the life of the process


def _acquire_single_runner() -> bool:
    global _lock_file
    try:
        import fcntl

        path = os.environ.get("TASKS_JOBS_LOCK", "/tmp/dgmts_tasks_jobs.lock")
        _lock_file = open(path, "w")
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except Exception:  # noqa: BLE001 - another worker holds it (or no fcntl: assume single process)
        return os.name == "nt"


def _loop():
    from services.task_manager import digest, employees, outbox

    last_sync = 0.0
    while True:
        try:
            if time.time() - last_sync >= Config.TASKS_EMPLOYEE_SYNC_MINUTES * 60:
                last_sync = time.time()
                try:
                    employees.sync_employees()
                except Exception as e:  # noqa: BLE001
                    print(f"[tasks] employee sync failed: {e}")
            try:
                digest.run_due_digest()
            except Exception as e:  # noqa: BLE001
                print(f"[tasks] due digest failed: {e}")
            outbox.process_outbox()
        except Exception as e:  # noqa: BLE001
            print(f"[tasks] job loop error: {e}")
        time.sleep(max(5, Config.TASKS_OUTBOX_INTERVAL_SECONDS))


def start_jobs() -> None:
    global _started
    if _started or not Config.TASKS_JOBS_ENABLED:
        return
    _started = True
    if not _acquire_single_runner():
        return
    threading.Thread(target=_loop, name="tasks-jobs", daemon=True).start()
    print("[tasks] background jobs started")
