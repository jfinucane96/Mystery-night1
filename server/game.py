"""Game state: the one party, its host key and its guests.
Every function takes an open transaction `t` (see db.Database.tx)."""

import json
import secrets
from datetime import datetime, timezone

from . import casting, texting

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


def room_key(t):
    """The watch-only link for the room screen, created the first time it's needed."""
    row = t.one("SELECT room_key FROM game WHERE id = 1")
    if row["room_key"]:
        return row["room_key"]
    key = _new_secret()
    t.run("UPDATE game SET room_key = ? WHERE id = 1", (key,))
    return key


def reset_room_key(t):
    key = _new_secret()
    t.run("UPDATE game SET room_key = ? WHERE id = 1", (key,))
    return key


def is_room(t, key):
    row = t.one("SELECT room_key FROM game WHERE id = 1")
    return bool(row and row["room_key"] and key and secrets.compare_digest(row["room_key"], key))


def is_host(t, key):
    row = t.one("SELECT host_key FROM game WHERE id = 1")
    return bool(row and key and secrets.compare_digest(row["host_key"], key))


def guest_for_token(t, token):
    if not token:
        return None
    return t.one("SELECT * FROM guests WHERE token = ?", (token,))


def list_guests(t):
    return t.all("SELECT * FROM guests ORDER BY created_at, name")


def guest_for_id(t, guest_id):
    return _get_guest(t, guest_id)


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
        t.run("UPDATE game SET released_at = ?, release_round = release_round + 1 WHERE id = 1", (_now(),))


def unrelease(t):
    t.run("UPDATE game SET released_at = NULL WHERE id = 1")


def new_guest_link(t, guest_id):
    """Replace a guest's link (e.g. it was sent to the wrong person). The old link stops working."""
    _get_guest(t, guest_id)
    t.run("UPDATE guests SET token = ? WHERE id = ?", (_new_secret(), guest_id))


def remove_guest(t, guest_id):
    _get_guest(t, guest_id)
    t.run("DELETE FROM texts WHERE guest_id = ?", (guest_id,))
    t.run("DELETE FROM balances WHERE guest_id = ?", (guest_id,))
    t.run("DELETE FROM messages WHERE guest_id = ?", (guest_id,))
    t.run("DELETE FROM guest_evidence WHERE guest_id = ?", (guest_id,))
    t.run("DELETE FROM clue_finds WHERE guest_id = ?", (guest_id,))
    t.run("DELETE FROM missions_done WHERE guest_id = ?", (guest_id,))
    t.run("DELETE FROM guests WHERE id = ?", (guest_id,))


# ------------------------------------------------------------ starting over

GAME_TABLES = ("texts", "balances", "grants", "messages", "questions", "guest_evidence",
               "clue_finds", "missions_done", "ai_suggestions")


def reset_game(t):
    """Start the party over: keep the guest list (names, phones, who is the host),
    the host link and the room link; wipe answers, characters and all game
    progress, and give every guest a fresh link (old links stop working)."""
    for table in GAME_TABLES:
        t.run(f"DELETE FROM {table}")
    for g in list_guests(t):
        t.run("UPDATE guests SET token = ?, answers = '{}', limits = '{}', limits_note = '', "
              "assessment_done = 0, character_id = NULL WHERE id = ?", (_new_secret(), g["id"]))
    t.run("UPDATE game SET released_at = NULL, release_round = 0, phase = 'setup', round = 0, paused = 0 WHERE id = 1")


# ------------------------------------------------------------ contact details

def update_contact(t, guest_id, phone, consent, is_host):
    _get_guest(t, guest_id)
    number = texting.normalize_phone(phone)
    if number is None:
        raise GameError(400, "That phone number doesn't look right. Use 10 digits, e.g. 555 123 4567.")
    if is_host:
        t.run("UPDATE guests SET is_host = 0 WHERE id <> ?", (guest_id,))
    t.run("UPDATE guests SET phone = ?, sms_consent = ?, is_host = ? WHERE id = ?",
          (number, 1 if consent and number else 0, 1 if is_host else 0, guest_id))


def textable(guest):
    return bool(guest["phone"] and guest["sms_consent"])


# ---------------------------------------------------------------- text history

def start_text(t, guest_id, kind, round_=0, text_id=None):
    """Record a text about to be sent. Returns its id, or None if it's a duplicate."""
    text_id = text_id or f"{kind}-{guest_id}-{secrets.token_hex(4)}"
    if t.one("SELECT id FROM texts WHERE id = ?", (text_id,)):
        return None
    if kind == "invite" and t.one("SELECT id FROM texts WHERE guest_id = ? AND kind = 'invite' AND status = 'sending'",
                                  (guest_id,)):
        return None
    t.run("INSERT INTO texts (id, guest_id, kind, round, status, detail, created_at) VALUES (?, ?, ?, ?, 'sending', '', ?)",
          (text_id, guest_id, kind, round_, _now()))
    return text_id


def finish_text(t, text_id, ok, detail=""):
    t.run("UPDATE texts SET status = ?, detail = ? WHERE id = ?", ("sent" if ok else "failed", detail[:300], text_id))


def latest_texts(t):
    """{guest_id: {kind: {status, detail, at}}} using each guest's newest text of each kind."""
    out = {}
    for row in t.all("SELECT * FROM texts ORDER BY created_at"):
        out.setdefault(row["guest_id"], {})[row["kind"]] = {
            "status": row["status"], "detail": row["detail"], "at": row["created_at"]}
    return out


def release_round(t):
    row = t.one("SELECT release_round FROM game WHERE id = 1")
    return row["release_round"] if row else 0


# -------------------------------------------------------------- AI suggestions

def save_suggestion(t, data):
    t.run("DELETE FROM ai_suggestions")
    t.run("INSERT INTO ai_suggestions (id, data, created_at) VALUES (1, ?, ?)", (json.dumps(data), _now()))


def get_suggestion(t):
    row = t.one("SELECT * FROM ai_suggestions WHERE id = 1")
    if not row:
        return None
    data = json.loads(row["data"])
    data["created_at"] = row["created_at"]
    return data


def apply_suggestion(t, story, assessment, confirm=False):
    """Assign every role from the saved whole-cast plan in one go."""
    plan = get_suggestion(t)
    if not plan:
        raise GameError(404, "There's no suggestion to apply. Press 'Suggest a cast' first.")
    guests = {g["id"]: g for g in list_guests(t)}
    rows = [r for r in plan["cast"] if r["guest_id"] in guests]
    needs_check = []
    for r in rows:
        if r["role_id"] in story["by_id"]:
            f = casting.fit(assessment, guests[r["guest_id"]], story["by_id"][r["role_id"]])
            if f["blocked"]:
                raise GameError(409, f"The suggestion for {guests[r['guest_id']]['name']} no longer fits their limits. "
                                     "Ask for a new suggestion.")
            if f["check"]:
                needs_check.append(guests[r["guest_id"]]["name"])
    if needs_check and not confirm:
        raise GameError(409, "Some suggested roles touch things these guests haven't said yes to: "
                        + ", ".join(needs_check) + ". Have you checked with them?", code="needs_confirm")
    for r in rows:                                   # clear first so roles can swap between guests
        t.run("UPDATE guests SET character_id = NULL WHERE id = ?", (r["guest_id"],))
    for r in rows:
        if r["role_id"] in story["by_id"]:
            assign_character(t, story, assessment, r["guest_id"], r["role_id"], confirm=True)

