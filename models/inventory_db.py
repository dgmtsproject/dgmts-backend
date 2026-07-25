"""
Isolated PostgreSQL connection layer for the Inventory & Purchase Management
module. Talks ONLY to the `inventory` schema (via a dedicated role + a pinned
search_path), so it can never touch the existing dgmts_static_db `public`
tables or the instrumentation Supabase.

Intentionally self-contained: this module imports nothing from
models/database.py or models/static_db.py. It mirrors the StaticDB pattern but
is a separate pool with separate credentials.
"""

import os
import threading
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2 import pool as _pool

from config import Config


class InventoryDB:
    """Psycopg2 pool returning dict rows, scoped to the inventory schema."""

    def __init__(self):
        self._pool = None
        self._lock = threading.Lock()

    def _ensure_pool(self):
        if self._pool is not None:
            return
        with self._lock:
            if self._pool is not None:
                return
            schema = Config.INVENTORY_DB_SCHEMA
            self._pool = _pool.ThreadedConnectionPool(
                minconn=int(os.getenv("INVENTORY_DB_POOL_MIN", "1")),
                maxconn=int(os.getenv("INVENTORY_DB_POOL_MAX", "10")),
                host=Config.INVENTORY_DB_HOST,
                port=Config.INVENTORY_DB_PORT,
                dbname=Config.INVENTORY_DB_NAME,
                user=Config.INVENTORY_DB_USER,
                password=Config.INVENTORY_DB_PASSWORD,
                connect_timeout=int(os.getenv("INVENTORY_DB_CONNECT_TIMEOUT", "10")),
                # Pin every connection to the inventory schema regardless of the
                # role's configured default. `public` is intentionally excluded.
                options=f"-c search_path={schema}",
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

    def execute_one(self, sql, params=None, returning: bool = True):
        """Execute and return a single dict row (or None) — convenience for
        INSERT ... RETURNING / UPDATE ... RETURNING of one row."""
        with self.cursor(commit=True) as cur:
            cur.execute(sql, params or ())
            if not returning:
                return cur.rowcount
            row = cur.fetchone()
            return dict(row) if row else None


inventory_db = InventoryDB()
