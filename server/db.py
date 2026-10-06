"""Persistence. Player identity (users) is stored separately from the per-game
player record and from character assignment (spec §3).

Two backends share one SQL dialect (`?` placeholders, ON CONFLICT upserts):
  * Postgres (Supabase) when DATABASE_URL is set
  * SQLite (local development and tests) otherwise, at MM_DB or data/mystery.db
"""

import os
import queue
import sqlite3
import threading

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "mystery.db")
DEFAULT_PG_SCHEMA = "mystery"    # NOT "public": Supabase exposes public tables via its Data API
PG_WRITE_LOCK = 727274           # advisory lock id; serialises writes like SQLite's BEGIN IMMEDIATE


def pg_schema():
    s = os.environ.get("MM_PG_SCHEMA") or DEFAULT_PG_SCHEMA
    if not s.replace("_", "").isalnum():
        raise ValueError("MM_PG_SCHEMA must be letters, digits and underscores")
    return s


TABLES = ["users", "auth_sessions", "games", "invitations", "players", "assessment_answers",
          "assignments", "disclosure_receipts", "audit_log"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,                    -- stored lower-case
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    host_user_id TEXT NOT NULL REFERENCES users(id),
    package_id TEXT NOT NULL,
    package_version TEXT NOT NULL,
    package_hash TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'DRAFT',
    casting_problems TEXT,
    casting_computed_at TEXT,
    assignment_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invitations (
    id TEXT PRIMARY KEY,
    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    label TEXT NOT NULL DEFAULT '',
    token_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',      -- pending|accepted|declined|revoked|closed
    claimed_by_user_id TEXT REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    responded_at TEXT
);
CREATE TABLE IF NOT EXISTS players (
    id TEXT PRIMARY KEY,
    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    identity_id TEXT NOT NULL REFERENCES users(id),
    invitation_id TEXT NOT NULL REFERENCES invitations(id),
    rsvp TEXT NOT NULL,                            -- accepted|declined
    assessment_version TEXT NOT NULL,
    assessment_status TEXT NOT NULL DEFAULT 'not_started',  -- not_started|in_progress|complete
    scores TEXT,
    boundaries TEXT,
    preparation_minutes INTEGER,
    assistance TEXT,
    stretch_preference TEXT,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (game_id, identity_id)
);
CREATE TABLE IF NOT EXISTS assessment_answers (
    player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL,
    option_id TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    PRIMARY KEY (player_id, question_id)
);
CREATE TABLE IF NOT EXISTS assignments (
    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    role_id TEXT NOT NULL,
    approval_state TEXT NOT NULL DEFAULT 'draft',  -- draft|approved
    source TEXT NOT NULL DEFAULT 'recommended',    -- recommended|override|forced
    forced_conflicts TEXT,                         -- JSON list the host explicitly overrode
    approved_by TEXT REFERENCES users(id),
    approved_at TEXT,
    assignment_version INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game_id, player_id),
    UNIQUE (game_id, role_id)
);
CREATE TABLE IF NOT EXISTS disclosure_receipts (
    id {autoid},
    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    claim_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    request_or_event_id TEXT NOT NULL,
    delivered_at TEXT NOT NULL,
    acknowledged_at TEXT,
    UNIQUE (game_id, player_id, claim_id, channel)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id {autoid},
    game_id TEXT,
    actor_user_id TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    at TEXT NOT NULL
);
"""

AUTOID = {"sqlite": "INTEGER PRIMARY KEY AUTOINCREMENT", "pg": "BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY"}


class Row(dict):
    """A row usable as row["name"], row[0] and dict(row) on both backends."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _unique(names):
    # Row is a dict, so duplicate column names would silently merge. Fail loudly instead.
    if len(set(names)) != len(names):
        raise ValueError(f"query returns duplicate column names {names}; alias them")
    return names


def _sqlite_row(cursor, values):
    return Row(zip(_unique([d[0] for d in cursor.description]), values))


def _pg_row_factory(cursor):
    names = _unique([d.name for d in cursor.description]) if cursor.description else []
    return lambda values: Row(zip(names, values))


class Conn:
    """Thin wrapper so store.py writes one dialect of SQL."""

    def __init__(self, raw, dialect, pool=None):
        self.raw, self.dialect, self._pool = raw, dialect, pool

    def _sql(self, sql):
        return sql.replace("%", "%%").replace("?", "%s") if self.dialect == "pg" else sql

    def execute(self, sql, params=()):
        return self.raw.execute(self._sql(sql), params)

    def executemany(self, sql, seq):
        if self.dialect == "pg":
            with self.raw.cursor() as cur:
                cur.executemany(self._sql(sql), seq)
        else:
            self.raw.executemany(sql, seq)

    def close(self):
        if self._pool:
            self._pool.put(self)
        else:
            self.raw.close()


class _PgPool:
    def __init__(self, url, size=5):
        self.url, self.idle, self.lock = url, queue.LifoQueue(), threading.Lock()
        self.size = size

    def _open(self):
        import psycopg
        # prepare_threshold=None keeps this working behind Supabase's transaction pooler too.
        raw = psycopg.connect(self.url, autocommit=True, prepare_threshold=None,
                              row_factory=_pg_row_factory, connect_timeout=10)
        raw.execute(f"SET search_path TO {pg_schema()}")
        return Conn(raw, "pg", self)

    def get(self):
        while True:
            try:
                c = self.idle.get_nowait()
            except queue.Empty:
                return self._open()
            if not (c.raw.closed or c.raw.broken):
                return c

    def put(self, c):
        from psycopg.pq import TransactionStatus
        if c.raw.closed or c.raw.broken or c.raw.info.transaction_status != TransactionStatus.IDLE \
                or self.idle.qsize() >= self.size:
            try:
                c.raw.close()
            except Exception:
                pass
            return
        self.idle.put(c)


_pool = None
_pool_lock = threading.Lock()


def database_url():
    return (os.environ.get("DATABASE_URL") or "").strip()


def backend_name():
    return "postgres" if database_url() else f"sqlite ({os.environ.get('MM_DB') or DEFAULT_PATH})"


def connect(path=None):
    global _pool
    url = database_url()
    if url and not path:
        with _pool_lock:
            if _pool is None:
                _pool = _PgPool(url)
        return _pool.get()
    path = path or os.environ.get("MM_DB") or DEFAULT_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    raw = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)  # explicit BEGIN/COMMIT
    raw.row_factory = _sqlite_row
    raw.execute("PRAGMA foreign_keys = ON")
    raw.execute("PRAGMA busy_timeout = 10000")
    return Conn(raw, "sqlite")


def init(path=None):
    if database_url() and not path:
        conn = connect()
        try:
            _init_pg(conn)
        finally:
            conn.close()
        return
    conn = connect(path)
    conn.raw.execute("PRAGMA journal_mode = WAL")
    conn.raw.executescript(SCHEMA.format(autoid=AUTOID["sqlite"]))
    _migrate(conn)
    conn.close()


# Columns added after a table was first created: (table, column, type).
MIGRATIONS = [("assignments", "forced_conflicts", "TEXT")]


def _migrate(conn):
    for table, column, ctype in MIGRATIONS:
        if conn.dialect == "pg":
            conn.raw.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ctype}")
        else:
            cols = [r["name"] for r in conn.raw.execute(f"PRAGMA table_info({table})")]
            if column not in cols:
                conn.raw.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")


def _init_pg(conn):
    conn.raw.execute(f"CREATE SCHEMA IF NOT EXISTS {pg_schema()}")
    conn.raw.execute(f"SET search_path TO {pg_schema()}")
    for stmt in SCHEMA.format(autoid=AUTOID["pg"]).split(";"):
        if stmt.strip():
            conn.raw.execute(stmt)
    _migrate(conn)
    # Defence in depth for Supabase: row-level security on with *no* policies means
    # the anon/authenticated API roles can read nothing even if this schema is ever
    # exposed. The server connects as the table owner, which RLS does not restrict.
    for t in TABLES:
        conn.raw.execute(f"ALTER TABLE {pg_schema()}.{t} ENABLE ROW LEVEL SECURITY")
    conn.raw.execute(f"""
        DO $$
        DECLARE r TEXT;
        BEGIN
          FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
              EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA {pg_schema()} FROM %I', r);
              EXECUTE format('REVOKE ALL ON SCHEMA {pg_schema()} FROM %I', r);
            END IF;
          END LOOP;
        END $$""")


class transaction:
    """with transaction(conn): ...  -- read-check-write runs atomically and
    writers are serialised (BEGIN IMMEDIATE on SQLite, an advisory lock on Postgres)."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        if self.conn.dialect == "pg":
            self.conn.raw.execute("BEGIN")
            self.conn.raw.execute(f"SELECT pg_advisory_xact_lock({PG_WRITE_LOCK})")
        else:
            self.conn.raw.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        self.conn.raw.execute("ROLLBACK" if exc_type else "COMMIT")
        return False
