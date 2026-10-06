"""Password hashing and server-side login sessions."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

ITERATIONS = 600_000
SESSION_DAYS = 14
COOKIE_NAME = "mm_session"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(nbytes=9):
    return secrets.token_urlsafe(nbytes)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password, stored):
    try:
        algo, iters, salt, expected = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
    return hmac.compare_digest(dk.hex(), expected)


# A precomputed hash so unknown-email logins take as long as wrong-password ones.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def create_session(conn, user_id):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                 (token_hash(token), user_id, now(), expires))
    return token


def user_for_token(conn, token):
    if not token:
        return None
    row = conn.execute(
        "SELECT u.id, u.email, u.display_name, s.expires_at FROM auth_sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (token_hash(token),)).fetchone()
    if not row or row["expires_at"] < now():
        return None
    return {"id": row["id"], "email": row["email"], "display_name": row["display_name"]}


def delete_session(conn, token):
    if token:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash(token),))


def check_login(conn, email, password):
    row = conn.execute("SELECT id, password_hash FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        verify_password(password, _DUMMY_HASH)
        return None
    return row["id"] if verify_password(password, row["password_hash"]) else None
