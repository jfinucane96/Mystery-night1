"""AI questions (P04) and evidence.

REMEMBER (private token): a guest asks their own character's memory. Only
facts their character knows (and that are available this round) are used.
INTERROGATE (public token): a guest questions another character. That
character can only say what the story allows: the truth, an authored dodge,
an authored lie, or (only when the asker presents the right evidence) an
authored confession. The truth behind a lie never leaves this file.

Tokens: asking reserves one token (removed at once, in one transaction);
the question is then answered, or the token is refunded if the character
knows nothing about it or the AI fails. Repeating a request id returns the
first result instead of charging again.
"""

import json
import re
from datetime import datetime, timedelta, timezone

from .game import GameError, _get_guest
from .play import _check_request_id, require_investigating, state as game_state

KINDS = {"remember": "private", "interrogate": "public"}
MAX_QUESTION = 300
STALE_AFTER = timedelta(minutes=3)


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="microseconds")   # fine-grained so the newest question sorts first


# ---------------------------------------------------------------- evidence

def evidence_list(story):
    return {e["id"]: e for e in story.get("evidence", [])}


def give_evidence(t, story, guest_id, evidence_id, source="host"):
    _get_guest(t, guest_id)
    if evidence_id not in evidence_list(story):
        raise GameError(400, "That evidence isn't in the story.")
    if not t.one("SELECT 1 AS x FROM guest_evidence WHERE guest_id = ? AND evidence_id = ?", (guest_id, evidence_id)):
        t.run("INSERT INTO guest_evidence (guest_id, evidence_id, source, created_at) VALUES (?, ?, ?, ?)",
              (guest_id, evidence_id, source, _iso(_now())))


def take_evidence(t, guest_id, evidence_id):
    t.run("DELETE FROM guest_evidence WHERE guest_id = ? AND evidence_id = ?", (guest_id, evidence_id))


def evidence_of(t, guest_id):
    return [r["evidence_id"] for r in t.all(
        "SELECT evidence_id FROM guest_evidence WHERE guest_id = ? ORDER BY created_at", (guest_id,))]


# ------------------------------------------------- what may be said or remembered

def memories(story, character_id, round_):
    """Facts a character can REMEMBER now (the private truth, from their point of view)."""
    c = story["by_id"][character_id]
    names = story["by_id"]
    out = []
    for f in c.get("knowledge", []):
        if f.get("from_round", 1) > max(round_, 1):
            continue
        m = {"fact_id": f["id"], "memory": f["text"], "topics": f["topics"],
             "how_you_know": "you saw or did this yourself"}
        if f["source"] == "heard":
            m["how_you_know"] = (f"{names[f['heard_from']]['name']} told you this"
                                 + ("; you're not sure it's true" if f.get("certainty") == "unsure" else ""))
        out.append(m)
    return out


def claims(story, character_id, round_, evidence_id=""):
    """What a character may SAY when interrogated. Lies and dodges carry only their
    authored line, never the truth behind them. A confession appears only when the
    asker has presented evidence that unlocks it."""
    c = story["by_id"][character_id]
    out = []
    for f in c.get("knowledge", []):
        if f.get("from_round", 1) > max(round_, 1):
            continue
        w = f["when_asked"]
        if evidence_id and evidence_id in w.get("confess_with", []):
            out.append({"claim_id": f["id"], "says": w["confession"], "delivery": "confessing", "topics": f["topics"]})
            continue
        delivery = {"truth": "truthful", "evade": "evasive", "lie": "lying"}[w["stance"]]
        out.append({"claim_id": f["id"], "says": w["says"], "delivery": delivery, "topics": f["topics"]})
    return out


def keyword_match(question, items):
    """Backup when the AI is unavailable: pick items whose topics appear in the question."""
    words = set(re.findall(r"[a-z']+", question.lower()))
    scored = [(sum(1 for tp in i["topics"] if tp.lower() in words), n, i) for n, i in enumerate(items)]
    best = max((s for s, _, _ in scored), default=0)
    return [i for s, _, i in scored if s and s == best][:2]


# ------------------------------------------------------------ asking

def _row_view(row):
    out = dict(row)
    out["result"] = json.loads(row["result"] or "{}")
    return out


def refund_stale(t):
    """A question stuck 'pending' (e.g. the server restarted mid-answer) is refunded."""
    cutoff = _iso(_now() - STALE_AFTER)
    for q in t.all("SELECT * FROM questions WHERE status = 'pending' AND created_at < ?", (cutoff,)):
        settle(t, q["id"], "failed", {"reason": "Took too long. Your token was returned."})


def reserve(t, story, request_id, asker, kind, question, target_id="", evidence_id=""):
    """Validate a question and take its token. Returns (question row, is_new)."""
    _check_request_id(request_id)
    existing = t.one("SELECT * FROM questions WHERE id = ?", (request_id,))
    if existing:
        if existing["asker_id"] != asker["id"]:
            raise GameError(409, "Please try again.")
        return _row_view(existing), False
    require_investigating(t)
    if kind not in KINDS:
        raise GameError(400, "Unknown kind of question.")
    if not asker["character_id"]:
        raise GameError(409, "You need a character before you can ask questions.")
    question = (question or "").strip()
    if len(question) < 3:
        raise GameError(400, "Type your question first.")
    if len(question) > MAX_QUESTION:
        raise GameError(400, f"Keep questions under {MAX_QUESTION} characters.")
    if kind == "interrogate":
        target = _get_guest(t, target_id) if target_id else None
        if not target or not target["character_id"]:
            raise GameError(400, "Choose who you want to question.")
        if target["id"] == asker["id"]:
            raise GameError(400, "You can't interrogate yourself. Try a private question instead.")
        if evidence_id and evidence_id not in evidence_of(t, asker["id"]):
            raise GameError(400, "You don't have that evidence.")
    else:
        target_id, evidence_id = "", ""
    token = KINDS[kind]
    balance = t.one("SELECT public, private FROM balances WHERE guest_id = ?", (asker["id"],))
    if not balance or balance[token] < 1:
        raise GameError(409, f"You have no {token} questions left. The host may give you more.")
    t.run(f"UPDATE balances SET {token} = {token} - 1 WHERE guest_id = ?", (asker["id"],))
    t.run("INSERT INTO questions (id, asker_id, kind, target_id, question, evidence_id, status, charged, result, created_at) "
          "VALUES (?, ?, ?, ?, ?, ?, 'pending', 1, '{}', ?)",
          (request_id, asker["id"], kind, target_id, question, evidence_id, _iso(_now())))
    return _row_view(t.one("SELECT * FROM questions WHERE id = ?", (request_id,))), True


def settle(t, question_id, status, result):
    """Finish a pending question: 'answered' keeps the token; anything else refunds it, once."""
    q = t.one("SELECT * FROM questions WHERE id = ?", (question_id,))
    if not q or q["status"] != "pending":
        return
    charged = 1
    if status != "answered":
        token = KINDS[q["kind"]]
        t.run(f"UPDATE balances SET {token} = {token} + 1 WHERE guest_id = ?", (q["asker_id"],))
        charged = 0
    t.run("UPDATE questions SET status = ?, charged = ?, result = ?, answered_at = ? WHERE id = ?",
          (status, charged, json.dumps(result), _iso(_now()), question_id))


def mark_target_done(t, guest_id, question_id):
    q = t.one("SELECT * FROM questions WHERE id = ? AND target_id = ?", (question_id, guest_id))
    if not q:
        raise GameError(404, "That interrogation isn't for you.")
    if not q["target_done_at"]:
        t.run("UPDATE questions SET target_done_at = ? WHERE id = ?", (_iso(_now()), question_id))


def context_for(t, story, q):
    """Everything the answering step needs, gathered inside a transaction."""
    asker = _get_guest(t, q["asker_id"])
    round_ = game_state(t)["round"]
    if q["kind"] == "remember":
        return {"character": story["by_id"][asker["character_id"]], "items": memories(story, asker["character_id"], round_)}
    target = _get_guest(t, q["target_id"])
    first = (target["name"].split() or [target["name"]])[0]
    return {"character": story["by_id"][target["character_id"]],
            "target_label": f"{first} ({story['by_id'][target['character_id']]['name']})",
            "asker_character": story["by_id"][asker["character_id"]],
            "items": claims(story, target["character_id"], round_, q["evidence_id"])}


# ------------------------------------------------------------ views

def guest_questions(t, story, guest):
    """A guest's own notebook, plus interrogations aimed at them."""
    names = {g["id"]: g for g in t.all("SELECT id, name, character_id FROM guests")}

    def who(gid):
        g = names.get(gid)
        if not g:
            return "someone who left"
        c = story["by_id"].get(g["character_id"] or "")
        first = (g["name"].split() or [g["name"]])[0]
        return f"{first} ({c['name']})" if c else first

    mine = []
    for q in t.all("SELECT * FROM questions WHERE asker_id = ? ORDER BY created_at DESC", (guest["id"],)):
        r = json.loads(q["result"] or "{}")
        item = {"id": q["id"], "kind": q["kind"], "question": q["question"], "status": q["status"],
                "at": q["created_at"], "note": r.get("reason", "")}
        if q["kind"] == "remember" and q["status"] == "answered":
            item["answer"] = r.get("answer", "")
        if q["kind"] == "interrogate":
            item["target"] = who(q["target_id"])
            item["evidence"] = evidence_list(story).get(q["evidence_id"], {}).get("name", "")
            item["answered_aloud"] = bool(q["target_done_at"])
        mine.append(item)
    aimed = []
    for q in t.all("SELECT * FROM questions WHERE target_id = ? AND status = 'answered' ORDER BY created_at DESC",
                   (guest["id"],)):
        r = json.loads(q["result"] or "{}")
        aimed.append({"id": q["id"], "asker": who(q["asker_id"]), "question": q["question"],
                      "evidence": evidence_list(story).get(q["evidence_id"], {}).get("name", ""),
                      "spoken": r.get("spoken", ""), "acting_tip": r.get("acting_tip", ""),
                      "done": bool(q["target_done_at"]), "at": q["created_at"]})
    return mine, aimed


def host_log(t, story):
    names = {g["id"]: g["name"] for g in t.all("SELECT id, name FROM guests")}
    out = []
    for q in t.all("SELECT * FROM questions ORDER BY created_at DESC LIMIT 100"):
        r = json.loads(q["result"] or "{}")
        out.append({"id": q["id"], "kind": q["kind"], "asker": names.get(q["asker_id"], "?"),
                    "target": names.get(q["target_id"], "") if q["target_id"] else "",
                    "question": q["question"], "status": q["status"], "at": q["created_at"],
                    "evidence": evidence_list(story).get(q["evidence_id"], {}).get("name", ""),
                    "answer": r.get("answer") or r.get("spoken") or r.get("reason", ""),
                    "delivery": r.get("delivery", ""), "via": r.get("via", "")})
    return out
