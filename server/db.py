"""Database access. Two backends with one interface:

- SQLite file (default, for testing on your own computer): data/party.db
- Postgres (Supabase) when DATABASE_URL is set (the hosted app on Render)

The whole app shares ONE connection guarded by a lock, so every request runs
its database work as a single transaction, one at a time. For a party of ~10
phones that is plenty fast, and it makes "charge a token exactly once" style
rules (later milestones) easy to get right.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SQLITE = os.path.join(ROOT, "data", "party.db")

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS game (
        id INTEGER PRIMARY KEY,
        host_key TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS guests (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        token TEXT NOT NULL UNIQUE,
        character_id TEXT UNIQUE,
        created_at TEXT NOT NULL
    )""",
]


class Database:
    def __init__(self, url=None, sqlite_path=None, schema=None):
        self.url = url
        self.sqlite_path = sqlite_path or DEFAULT_SQLITE
        self.schema = schema or os.environ.get("MM_PG_SCHEMA", "party")
        self.kind = "postgres" if url else "sqlite"
        self._conn = None
        self._lock = threading.Lock()

    def describe(self):
        if self.kind == "postgres":
            host = self.url.split("@")[-1].split("/")[0]  # never print the password
            return f"Postgres at {host} (schema '{self.schema}')"
        return f"SQLite file {self.sqlite_path}"

    def _connect(self):
        if self.kind == "sqlite":
            os.makedirs(os.path.dirname(self.sqlite_path), exist_ok=True)
            conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(self.url, row_factory=dict_row, connect_timeout=15,
                               prepare_threshold=None)
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            cur.execute(f'SET search_path TO "{self.schema}"')
        conn.commit()
        return conn

    def _live_conn(self):
        c = self._conn
        if c is None or (self.kind == "postgres" and (c.closed or c.broken)):
            self._conn = self._connect()
        return self._conn

    @contextmanager
    def tx(self):
        """Run a block of queries as one transaction: all saved, or none."""
        with self._lock:
            conn = self._live_conn()
            try:
                yield _Tx(conn, self.kind)
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    self._conn = None  # connection died; reconnect next time
                raise

    def init_schema(self):
        with self.tx() as t:
            for stmt in SCHEMA:
                t.run(stmt)


class _Tx:
    def __init__(self, conn, kind):
        self.conn = conn
        self.kind = kind

    def _exec(self, sql, params):
        if self.kind == "postgres":
            sql = sql.replace("?", "%s")
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return cur

    def run(self, sql, params=()):
        """Execute a statement; returns the number of rows changed."""
        return self._exec(sql, params).rowcount

    def all(self, sql, params=()):
        return [dict(r) for r in self._exec(sql, params).fetchall()]

    def one(self, sql, params=()):
        row = self._exec(sql, params).fetchone()
        return dict(row) if row is not None else None


def from_env():
    url = (os.environ.get("DATABASE_URL") or "").strip() or None
    if url and not url.startswith(("postgresql://", "postgres://")):
        # Never print the value itself: it contains the database password.
        raise SystemExit("DATABASE_URL is set but doesn't start with 'postgresql://'. "
                         "Paste only the part after 'DATABASE_URL=' from your .env file "
                         "(no '#' comment lines, quotes or spaces).")
    return Database(url=url,
                    sqlite_path=os.environ.get("MM_DB") or None)
