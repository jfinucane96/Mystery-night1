"""Game state: the one party, its host key and its guests.
Every function takes an open transaction `t` (see db.Database.tx)."""

import json
import secrets
from datetime import datetime, timezone

from . import casting

MAX_NAME = 40


class GameError(Exception):
    """A request the app refuses, with a message that is safe to show the user."""

    def __init__(self, status, message, code=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_secret():
    # 32 random bytes = 43 URL-safe characters: impossible to guess.
    return secrets.token_urlsafe(32)


def ensure_game(t):
    """Create the single game the first time the app starts. Returns the host key."""
    row = t.one("SELECT host_key FROM game WHERE id = 1")
    if row:
        return row["host_key"]
    key = _new_secret()
    t.run("INSERT INTO game (id, host_key, created_at) VALUES (1, ?, ?)", (key, _now()))
    return key


def reset_host_key(t):
    """Make a new host link; the old one stops working immediately."""
    ensure_game(t)
    key = _new_secret()
    t.run("UPDATE game SET host_key = ? WHERE id = 1", (key,))
    return key


def is_host(t, key):
    row = t.one("SELECT host_key FROM game WHERE id = 1")
    return bool(row and key and secrets.compare_digest(row["host_key"], key))


def guest_for_token(t, token):
    if not token:
        return None
    return t.one("SELECT * FROM guests WHERE token = ?", (token,))


def list_guests(t):
    return t.all("SELECT * FROM guests ORDER BY created_at, name")


def _get_guest(t, guest_id):
    g = t.one("SELECT * FROM guests WHERE id = ?", (guest_id,))
    if not g:
        raise GameError(404, "That guest no longer exists.")
    return g


def add_guest(t, name):
    name = (name or "").strip()
    if not name:
        raise GameError(400, "Please type the guest's name.")
    if len(name) > MAX_NAME:
        raise GameError(400, f"Names can be at most {MAX_NAME} characters.")
    if t.one("SELECT id FROM guests WHERE lower(name) = lower(?)", (name,)):
        raise GameError(409, f"There is already a guest called {name}.")
    gid = "g" + secrets.token_hex(4)
    t.run("INSERT INTO guests (id, name, token, character_id, created_at) VALUES (?, ?, ?, NULL, ?)",
          (gid, name, _new_secret(), _now()))
    return _get_guest(t, gid)


def assign_character(t, story, assessment, guest_id, character_id, confirm=False):
    """Host's manual casting. Blocks roles the guest said no to; roles touching
    something the guest didn't answer need `confirm` (host has checked with them)."""
    guest = _get_guest(t, guest_id)
    if character_id in ("", None):
        character_id = None
    elif character_id not in story["by_id"]:
        raise GameError(400, "That character isn't in the story.")
    else:
        other = t.one("SELECT name FROM guests WHERE character_id = ? AND id <> ?",
                      (character_id, guest_id))
        if other:
            name = story["by_id"][character_id]["name"]
            raise GameError(409, f"{name} is already assigned to {other['name']}. Unassign them first.")
        f = casting.fit(assessment, guest, story["by_id"][character_id])
        if f["blocked"]:
            raise GameError(409, f"Can't give this role to {guest['name']}: " + "; ".join(f["blocked"]) + ".")
        if f["check"] and not confirm:
            raise GameError(409, f"{guest['name']}: " + "; ".join(f["check"]) +
                            ". Have you checked with them that it's OK?", code="needs_confirm")
    t.run("UPDATE guests SET character_id = ? WHERE id = ?", (character_id, guest["id"]))


def save_assessment(t, assessment, guest, answers=None, limits=None, note=None, submit=False):
    """Guests save as they go (so they can stop and resume). `submit` marks it finished."""
    if is_released(t):
        raise GameError(409, "Characters have been released, so answers can't be changed now.")
    a = casting.answers_of(guest)
    a.update(casting.clean_answers(assessment, answers))
    lim = casting.limits_of(guest)
    lim.update(casting.clean_limits(assessment, limits))
    note = guest["limits_note"] if note is None else str(note).strip()[:500]
    done = guest["assessment_done"]
    if submit:
        if not casting.is_complete(assessment, a, lim):
            raise GameError(400, "Please answer every question first.")
        done = 1
    t.run("UPDATE guests SET answers = ?, limits = ?, limits_note = ?, assessment_done = ? WHERE id = ?",
          (json.dumps(a), json.dumps(lim), note, done, guest["id"]))


def is_released(t):
    row = t.one("SELECT released_at FROM game WHERE id = 1")
    return bool(row and row["released_at"])


def release(t, story, assessment):
    """Show every guest their character, all at once. Safe to press twice."""
    for g in list_guests(t):
        c = story["by_id"].get(g["character_id"] or "")
        if c and casting.fit(assessment, g, c)["blocked"]:
            raise GameError(409, f"{g['name']} has a role they said no to ({c['name']}). Change it before releasing.")
    if not is_released(t):
        t.run("UPDATE game SET released_at = ? WHERE id = 1", (_now(),))


def unrelease(t):
    t.run("UPDATE game SET released_at = NULL WHERE id = 1")


def new_guest_link(t, guest_id):
    """Replace a guest's link (e.g. it was sent to the wrong person). The old link stops working."""
    _get_guest(t, guest_id)
    t.run("UPDATE guests SET token = ? WHERE id = ?", (_new_secret(), guest_id))


def remove_guest(t, guest_id):
    _get_guest(t, guest_id)
    t.run("DELETE FROM guests WHERE id = ?", (guest_id,))
