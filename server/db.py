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

# Each step runs once, in order, and the database remembers how far it got.
# Never edit a step that has already run online: add a new one at the end.
MIGRATIONS = [
    # 1. P01: the game and its guests (IF NOT EXISTS: P01 databases already have these)
    [
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
    ],
    # 2. P02: assessment answers, hard limits and character release
    [
        "ALTER TABLE guests ADD COLUMN answers TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE guests ADD COLUMN limits TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE guests ADD COLUMN limits_note TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE guests ADD COLUMN assessment_done INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE game ADD COLUMN released_at TEXT",
    ],
    # 3. Texting and AI casting suggestions
    [
        "ALTER TABLE guests ADD COLUMN phone TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE guests ADD COLUMN sms_consent INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE guests ADD COLUMN is_host INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE game ADD COLUMN release_round INTEGER NOT NULL DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS texts (
            id TEXT PRIMARY KEY,
            guest_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            round INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS ai_suggestions (
            id INTEGER PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
    ],
    # 4. AI short versions (feature removed; step 5 drops the table)
    [
        """CREATE TABLE IF NOT EXISTS briefs (
            guest_id TEXT PRIMARY KEY,
            character_id TEXT NOT NULL,
            data TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
    ],
    # 5. Remove the AI short-version table (replaced by authored chapters in the story)
    [
        "DROP TABLE IF EXISTS briefs",
    ],
    # 6. P03: phases, rounds, pause, question tokens and private messages
    [
        "ALTER TABLE game ADD COLUMN phase TEXT NOT NULL DEFAULT 'setup'",
        "ALTER TABLE game ADD COLUMN round INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE game ADD COLUMN paused INTEGER NOT NULL DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS balances (
            guest_id TEXT PRIMARY KEY,
            public INTEGER NOT NULL DEFAULT 0,
            private INTEGER NOT NULL DEFAULT 0
        )""",
        # Every grant has an id; a repeated id (double tap, retry) is ignored
        """CREATE TABLE IF NOT EXISTS grants (
            id TEXT PRIMARY KEY,
            target TEXT NOT NULL,
            public INTEGER NOT NULL,
            private INTEGER NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            guest_id TEXT NOT NULL,
            body TEXT NOT NULL,
            direction_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            read_at TEXT
        )""",
    ],
    # 7. P04: AI questions (token reserved when asked, refunded if unanswered) and evidence
    [
        """CREATE TABLE IF NOT EXISTS questions (
            id TEXT PRIMARY KEY,
            asker_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            target_id TEXT NOT NULL DEFAULT '',
            question TEXT NOT NULL,
            evidence_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            charged INTEGER NOT NULL DEFAULT 0,
            result TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            answered_at TEXT,
            target_done_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS guest_evidence (
            guest_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (guest_id, evidence_id)
        )""",
    ],
    # 8. Room screen: a watch-only link for the computer on the speaker / TV
    [
        "ALTER TABLE game ADD COLUMN room_key TEXT NOT NULL DEFAULT ''",
    ],
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

    def close(self):
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

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
        """Bring the database up to date by running any migration steps it hasn't had yet."""
        with self.tx() as t:
            t.run("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = t.one("SELECT version FROM schema_version")
            if row is None:
                t.run("INSERT INTO schema_version (version) VALUES (0)")
            done = row["version"] if row else 0
            for number, step in enumerate(MIGRATIONS[done:], start=done + 1):
                for stmt in step:
                    t.run(stmt)
                t.run("UPDATE schema_version SET version = ?", (number,))


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
