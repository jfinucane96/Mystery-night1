"""Running the party (P03): phases, investigation rounds, pause, question
tokens and private messages. Every function takes an open transaction `t`.

Double taps and retries are safe: phase and round changes name the state to
move to (doing it twice changes nothing), and every grant or message carries
an id from the host's page, so a repeated id is ignored.
"""

import re
from datetime import datetime, timezone

from .game import GameError, list_guests, _get_guest

PHASES = ["setup", "arrival", "investigation", "accusation", "reveal"]
PHASE_NAMES = {"setup": "Setup", "arrival": "Arrival", "investigation": "Investigation",
               "accusation": "Accusations", "reveal": "The reveal"}
MAX_GRANT = 10
MAX_MESSAGE = 1000
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def config(story):
    g = story.get("game", {})
    return {
        "start_tokens": g.get("start_tokens", {"public": 2, "private": 1}),
        "round_tokens": g.get("round_tokens", {"public": 1, "private": 0}),
        "rounds": g.get("rounds", 3),
    }


def state(t):
    row = t.one("SELECT phase, round, paused FROM game WHERE id = 1")
    return {"phase": row["phase"], "round": row["round"], "paused": bool(row["paused"])}


def _check_request_id(request_id):
    if not isinstance(request_id, str) or not _REQUEST_ID.match(request_id):
        raise GameError(400, "Missing or invalid request id.")


# ---------------------------------------------------------------- phases

def set_phase(t, story, phase):
    """Move the game to `phase`. Starting the investigation begins round 1 and
    hands out the starting tokens (once, however many times it's pressed)."""
    if phase not in PHASES:
        raise GameError(400, "Unknown phase.")
    t.run("UPDATE game SET phase = ? WHERE id = 1", (phase,))
    if phase == "investigation" and state(t)["round"] == 0:
        t.run("UPDATE game SET round = 1 WHERE id = 1")
        tokens = config(story)["start_tokens"]
        grant(t, "round-1", "all", tokens["public"], tokens["private"], "Investigation begins")


def start_round(t, story, number):
    """Start investigation round `number`. Pressing it twice does nothing extra."""
    s = state(t)
    if s["phase"] != "investigation":
        raise GameError(409, "Rounds only happen during the investigation.")
    if number == s["round"]:
        return
    if number != s["round"] + 1:
        raise GameError(409, f"The investigation is on round {s['round']}.")
    t.run("UPDATE game SET round = ? WHERE id = 1", (number,))
    tokens = config(story)["round_tokens"]
    if tokens["public"] or tokens["private"]:
        grant(t, f"round-{number}", "all", tokens["public"], tokens["private"], f"Round {number}")


def set_paused(t, paused):
    t.run("UPDATE game SET paused = ? WHERE id = 1", (1 if paused else 0,))


def require_investigating(t):
    """Guard for investigative actions (questions in P04, clues in P05)."""
    s = state(t)
    if s["paused"]:
        raise GameError(409, "The game is paused. Hold on until the host resumes.")
    if s["phase"] != "investigation":
        raise GameError(409, "That's only possible during the investigation.")


# ---------------------------------------------------------------- tokens

def balance(t, guest_id):
    row = t.one("SELECT public, private FROM balances WHERE guest_id = ?", (guest_id,))
    return {"public": row["public"], "private": row["private"]} if row else {"public": 0, "private": 0}


def all_balances(t):
    return {r["guest_id"]: {"public": r["public"], "private": r["private"]}
            for r in t.all("SELECT * FROM balances")}


def grant(t, request_id, target, public, private, reason=""):
    """Add tokens to one guest or 'all'. Returns False if this grant already happened."""
    if not (isinstance(public, int) and isinstance(private, int)
            and 0 <= public <= MAX_GRANT and 0 <= private <= MAX_GRANT and (public or private)):
        raise GameError(400, f"Grant between 1 and {MAX_GRANT} tokens.")
    if t.one("SELECT id FROM grants WHERE id = ?", (request_id,)):
        return False
    guests = [g["id"] for g in list_guests(t)] if target == "all" else [_get_guest(t, target)["id"]]
    t.run("INSERT INTO grants (id, target, public, private, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
          (request_id, target, public, private, reason[:100], _now()))
    for gid in guests:
        t.run("INSERT INTO balances (guest_id) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM balances WHERE guest_id = ?)",
              (gid, gid))
        t.run("UPDATE balances SET public = public + ?, private = private + ? WHERE guest_id = ?",
              (public, private, gid))
    return True


def host_grant(t, request_id, target, public, private):
    _check_request_id(request_id)
    return grant(t, "host-" + request_id, target, public, private, "Host gift")


# -------------------------------------------------------------- messages

def directions_for(story, character_id):
    """Pre-written instructions in the story for one character."""
    return [d for d in story.get("directions", []) if d["character"] == character_id]


def send_message(t, story, request_id, guest_id, body=None, direction_id=None):
    """Send a private message (typed, or a pre-written direction). Returns (message, is_new)."""
    _check_request_id(request_id)
    g = _get_guest(t, guest_id)
    existing = t.one("SELECT * FROM messages WHERE id = ?", (request_id,))
    if existing:
        return existing, False
    if direction_id:
        d = next((d for d in directions_for(story, g["character_id"]) if d["id"] == direction_id), None)
        if not d:
            raise GameError(400, "That instruction isn't for this guest's character.")
        body = d["text"]
    body = (body or "").strip()
    if not body:
        raise GameError(400, "Type a message first.")
    if len(body) > MAX_MESSAGE:
        raise GameError(400, f"Messages can be at most {MAX_MESSAGE} characters.")
    t.run("INSERT INTO messages (id, guest_id, body, direction_id, created_at, read_at) VALUES (?, ?, ?, ?, ?, NULL)",
          (request_id, guest_id, body, direction_id or "", _now()))
    return t.one("SELECT * FROM messages WHERE id = ?", (request_id,)), True


def messages_for(t, guest_id):
    return t.all("SELECT id, body, direction_id, created_at, read_at FROM messages WHERE guest_id = ? "
                 "ORDER BY created_at DESC, id", (guest_id,))


def all_messages(t):
    out = {}
    for m in t.all("SELECT * FROM messages ORDER BY created_at DESC, id"):
        out.setdefault(m["guest_id"], []).append(
            {"id": m["id"], "body": m["body"], "direction_id": m["direction_id"],
             "created_at": m["created_at"], "read": m["read_at"] is not None})
    return out


def mark_read(t, guest_id):
    t.run("UPDATE messages SET read_at = ? WHERE guest_id = ? AND read_at IS NULL", (_now(), guest_id))


def forget_guest(t, guest_id):
    t.run("DELETE FROM balances WHERE guest_id = ?", (guest_id,))
    t.run("DELETE FROM messages WHERE guest_id = ?", (guest_id,))
