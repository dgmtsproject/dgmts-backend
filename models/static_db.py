"""
Local PostgreSQL connection layer for the dgmts_static_db database.
Replaces Supabase for marketing-site tables (blogs, events, subscribers, etc.).

The instrumentation tables (users, sensor_readings, instruments, sent_alerts, ...)
continue to use models/database.py and Supabase.
"""

import os
import threading
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2 import pool as _pool

from config import Config


class StaticDB:
    """Psycopg2 pool returning dict rows (Supabase-like shape)."""

    def __init__(self):
        self._pool = None
        self._lock = threading.Lock()

    def _ensure_pool(self):
        if self._pool is not None:
            return
        with self._lock:
            if self._pool is not None:
                return
            self._pool = _pool.ThreadedConnectionPool(
                minconn=int(os.getenv("STATIC_DB_POOL_MIN", "1")),
                maxconn=int(os.getenv("STATIC_DB_POOL_MAX", "10")),
                host=Config.STATIC_DB_HOST,
                port=Config.STATIC_DB_PORT,
                dbname=Config.STATIC_DB_NAME,
                user=Config.STATIC_DB_USER,
                password=Config.STATIC_DB_PASSWORD,
                connect_timeout=int(os.getenv("STATIC_DB_CONNECT_TIMEOUT", "10")),
            )

    @contextmanager
    def connection(self):
        self._ensure_pool()
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            try:
                self._pool.putconn(conn)
            except Exception:
                pass

    @contextmanager
    def cursor(self, commit: bool = True):
        with self.connection() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                yield cur
                if commit:
                    conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()

    def query(self, sql, params=None):
        with self.cursor(commit=False) as cur:
            cur.execute(sql, params or ())
            return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql, params=None):
        with self.cursor(commit=False) as cur:
            cur.execute(sql, params or ())
            row = cur.fetchone()
            return dict(row) if row else None

    def execute(self, sql, params=None, returning: bool = False):
        with self.cursor(commit=True) as cur:
            cur.execute(sql, params or ())
            if returning:
                return [dict(r) for r in cur.fetchall()]
            return cur.rowcount


static_db = StaticDB()
