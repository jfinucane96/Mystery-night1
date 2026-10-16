"""QR clues and secret missions (P05).

A clue's QR label only carries its short code (e.g. VIAL-7K), never anything
secret. Finding a clue checks the guest, the phase and the round on the
server, then shows that guest the shared description plus whatever their own
character notices. Finding the same clue again changes nothing (and never
repeats a reward). Missions are confirmed by the host and reward a bonus clue,
once. Essential clues can always be released by the host as a backup.
"""

from datetime import datetime, timezone

from .game import GameError, _get_guest
from .play import require_investigating, state as game_state
from .questions import give_evidence

SOURCES = {"scan": "Scanned", "code": "Code typed", "host": "From the host", "mission": "Mission reward"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def by_id(story):
    return {c["id"]: c for c in story.get("clues", [])}


def by_code(story):
    return {c["code"]: c for c in story.get("clues", []) if c.get("code")}


def normalize_code(raw):
    return "".join(ch for ch in (raw or "").upper() if ch.isalnum() or ch == "-").strip("-")


def _grant(t, story, guest, clue, source):
    """Add a clue to a guest's journal. Returns False if they already had it."""
    if t.one("SELECT 1 AS x FROM clue_finds WHERE guest_id = ? AND clue_id = ?", (guest["id"], clue["id"])):
        return False
    t.run("INSERT INTO clue_finds (guest_id, clue_id, source, created_at) VALUES (?, ?, ?, ?)",
          (guest["id"], clue["id"], source, _now()))
    if clue.get("evidence"):
        give_evidence(t, story, guest["id"], clue["evidence"], source="clue")
    return True


def find(t, story, guest, raw_code, source="code"):
    """A guest scans a QR or types a code. Returns (clue, is_new)."""
    code = normalize_code(raw_code)
    clue = by_code(story).get(code)
    if not clue:
        # Same answer whether the code is wrong or simply not in this story: nothing to learn here
        raise GameError(404, "That code doesn't match any clue. Check it and try again.")
    if not guest["character_id"]:
        raise GameError(409, "You need a character before you can examine clues.")
    if t.one("SELECT 1 AS x FROM clue_finds WHERE guest_id = ? AND clue_id = ?", (guest["id"], clue["id"])):
        return clue, False                         # already found: show it again, no new reward
    require_investigating(t)
    current = game_state(t)["round"]
    if clue.get("from_round", 1) > current:
        raise GameError(409, f"This clue can't be examined yet. Try again in round {clue['from_round']}.")
    return clue, _grant(t, story, guest, clue, source)


def release(t, story, guest_id, clue_id):
    """Host backup route: give a guest a clue directly (works in any phase)."""
    guest = _get_guest(t, guest_id)
    clue = by_id(story).get(clue_id)
    if not clue:
        raise GameError(400, "That clue isn't in the story.")
    return _grant(t, story, guest, clue, "host")


def missions_for(story, character_id):
    return [m for m in story.get("missions", []) if m["character"] == character_id]


def confirm_mission(t, story, guest_id, mission_id):
    """Host confirms a secret mission: marks it done and gives the reward clue, once."""
    guest = _get_guest(t, guest_id)
    mission = next((m for m in missions_for(story, guest["character_id"]) if m["id"] == mission_id), None)
    if not mission:
        raise GameError(400, "That mission isn't for this guest's character.")
    if t.one("SELECT 1 AS x FROM missions_done WHERE guest_id = ? AND mission_id = ?", (guest_id, mission_id)):
        return False
    t.run("INSERT INTO missions_done (guest_id, mission_id, created_at) VALUES (?, ?, ?)",
          (guest_id, mission_id, _now()))
    _grant(t, story, guest, by_id(story)[mission["reward_clue"]], "mission")
    return True


def clue_view(story, clue, character_id, found_at=None, source=None):
    """What one guest sees of a clue: the shared text plus only their own character's view."""
    return {"id": clue["id"], "title": clue["title"], "text": clue["text"],
            "mine": clue.get("views", {}).get(character_id or "", ""),
            "evidence": next((e["name"] for e in story.get("evidence", []) if e["id"] == clue.get("evidence")), ""),
            "at": found_at, "source": SOURCES.get(source, "")}


def journal(t, story, guest):
    clues = by_id(story)
    return [clue_view(story, clues[r["clue_id"]], guest["character_id"], r["created_at"], r["source"])
            for r in t.all("SELECT * FROM clue_finds WHERE guest_id = ? ORDER BY created_at DESC", (guest["id"],))
            if r["clue_id"] in clues]


def guest_missions(t, story, guest):
    done = {r["mission_id"] for r in t.all("SELECT mission_id FROM missions_done WHERE guest_id = ?", (guest["id"],))}
    return [{"id": m["id"], "text": m["text"], "done": m["id"] in done}
            for m in missions_for(story, guest["character_id"])]


def host_overview(t, story):
    """For the host: every clue, who has found it, and each guest's missions."""
    names = {g["id"]: g["name"] for g in t.all("SELECT id, name FROM guests")}
    found = {}
    for r in t.all("SELECT * FROM clue_finds ORDER BY created_at"):
        found.setdefault(r["clue_id"], []).append(names.get(r["guest_id"], "?"))
    out = []
    for n, c in enumerate(story.get("clues", []), start=1):
        out.append({"id": c["id"], "n": n, "title": c["title"], "code": c.get("code", ""),
                    "prop": c.get("prop", ""), "where": c.get("where", ""), "from_round": c.get("from_round", 1),
                    "essential": bool(c.get("essential")), "found_by": found.get(c["id"], [])})
    return out


def host_guest_extras(t, story, guest):
    have = {r["clue_id"] for r in t.all("SELECT clue_id FROM clue_finds WHERE guest_id = ?", (guest["id"],))}
    clues = by_id(story)
    return {"clues_found": sorted(have),
            "missions": [{**m, "reward": clues[mm["reward_clue"]]["title"]}
                         for m, mm in zip(guest_missions(t, story, guest), missions_for(story, guest["character_id"]))]}


def labels(story, base_url):
    """Printable QR labels: only a number, the QR and the backup code (no spoilers on the prop)."""
    import segno
    out = []
    for n, c in enumerate(story.get("clues", []), start=1):
        if not c.get("code"):
            continue
        url = f"{base_url}/c/{c['code']}"
        out.append({"n": n, "code": c["code"], "url": url, "prop": c.get("prop", ""), "where": c.get("where", ""),
                    "from_round": c.get("from_round", 1),
                    "svg": segno.make(url, error="m").svg_inline(scale=1, border=4, omitsize=True, dark="#000", light="#fff")})
    return out
