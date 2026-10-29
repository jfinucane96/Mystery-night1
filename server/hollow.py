"""The Hollow Order (v2 build sheet): the six-phase game. Every function takes an
open transaction `t`; the server is the only authority on state.

Privacy rules live here:
- the murderer is chosen secretly when characters are released and is never in
  any host or room response (only the murderer's own phone learns it);
- a guest only ever sees their own dossier, their own deals and their own clues;
- the room/TV gets public material only, and only the two approved leaderboards.
"""

import json
import secrets
from datetime import datetime, timedelta, timezone

from . import hollow_content as hc
from . import hollow_engine as eng
from .game import GameError

INHERITANCE, SUSPICION = "inheritance", "suspicion"
BALLOT_STEPS = {
    "ballot1": [(INHERITANCE, 1)],
    "ballots2": [(INHERITANCE, 2), (SUSPICION, 1)],
    "ballots3": [(INHERITANCE, 3), (SUSPICION, 2)],
    "ballot4": [(INHERITANCE, 4)],
    "suspicion3": [(SUSPICION, 3)],
}
BALLOT_AT = {k: step for step, ks in BALLOT_STEPS.items() for k in ks}
DEAL_STEPS = {"deals": ("protect", "protect_couple", "force"), "bribes4": ("bribe",), "temptation": ("bribe",)}
BRIBE_ROUND = {"bribes4": 3, "temptation": 4}
REVEAL_AFTER = 60          # seconds an investigated guest has to respond before the clue can be opened
MAX_AMOUNT = 12_000_000

_cases = {}


def _now():
    return datetime.now(timezone.utc)


def _iso(dt=None):
    return (dt or _now()).isoformat(timespec="seconds")


def _id():
    return secrets.token_hex(6)


# ------------------------------------------------------------------ state

def state(t):
    row = t.one("SELECT * FROM hollow_state WHERE id = 1")
    if not row:
        t.run("INSERT INTO hollow_state (id, step, culprit, timer_end, paused, turn_order, ledger, extra, updated_at) "
              "VALUES (1, 'lobby', '', '', 0, '', '', '{}', ?)", (_iso(),))
        row = t.one("SELECT * FROM hollow_state WHERE id = 1")
    row["extra"] = json.loads(row["extra"] or "{}")
    return row


def _save_extra(t, extra):
    t.run("UPDATE hollow_state SET extra = ? WHERE id = 1", (json.dumps(extra),))


def ensure_culprit(t, data):
    """Pick the murderer, once, in secret. Anybody may be chosen, the host included."""
    s = state(t)
    if not s["culprit"]:
        t.run("UPDATE hollow_state SET culprit = ? WHERE id = 1", (secrets.choice(data["order"]),))


def get_case(t, data, story):
    s = state(t)
    if not s["culprit"]:
        return None
    key = (id(data), s["culprit"])
    if key not in _cases:
        _cases[key] = hc.build(data, story, s["culprit"])
    return _cases[key]


def reset(t):
    """Clear all Hollow Order progress (keeps the secret murderer unless `forget`)."""
    for table in ("h_polls", "h_ballots", "h_interrogations", "h_investigations", "h_deals", "h_hotseat",
                  "h_wagers", "h_votes", "h_events"):
        t.run(f"DELETE FROM {table}")
    t.run("UPDATE hollow_state SET step = 'lobby', timer_end = '', paused = 0, turn_order = '', ledger = '', "
          "extra = '{}' WHERE id = 1")


def forget_culprit(t):
    t.run("UPDATE hollow_state SET culprit = '' WHERE id = 1")


# ---------------------------------------------------------------- players

def players(t, data):
    """{character_id: guest row} for every guest playing a Hollow Order role."""
    out = {}
    for g in t.all("SELECT * FROM guests WHERE character_id IS NOT NULL"):
        if g["character_id"] in data["by_id"]:
            out[g["character_id"]] = g
    return out


def nick(story, data, cid):
    c = story["by_id"].get(cid)
    return (c.get("player") if c else None) or hc.short(data, cid)


def full(story, data, cid):
    c = story["by_id"].get(cid)
    return c["name"] if c else hc.short(data, cid)


def spouse(data, cid):
    return next(m for m in data["families"][data["family_of"][cid]]["members"] if m != cid)


def _require_step(t, *steps):
    s = state(t)
    if s["paused"]:
        raise GameError(409, "The game is paused.")
    if s["step"] not in steps:
        raise GameError(409, "That isn't open right now.")
    return s


def _event(t, kind, **data):
    row = t.one("SELECT MAX(seq) AS m FROM h_events")
    seq = (row["m"] or 0) + 1
    t.run("INSERT INTO h_events (seq, kind, data, created_at) VALUES (?, ?, ?, ?)",
          (seq, kind, json.dumps(data), _iso()))


# ---------------------------------------------------------------- steps

def set_step(t, data, story, step):
    """Host moves the game. Forward one step at a time; back any number (to reopen a
    window pressed too early). Running a step twice is harmless."""
    if step not in hc.STEP_IDS:
        raise GameError(400, "Unknown step.")
    s = state(t)
    cur, new = hc.STEP_IDS.index(s["step"]), hc.STEP_IDS.index(step)
    if new == cur:
        return
    if new > cur + 1:
        raise GameError(409, "Move one step at a time.")
    if not s["culprit"]:
        raise GameError(409, "Release the characters first.")
    if new > cur:
        _on_leave(t, data, story, s["step"])
    t.run("UPDATE hollow_state SET step = ?, timer_end = '', updated_at = ? WHERE id = 1", (step, _iso()))
    if new > cur:
        _on_enter(t, data, story, step)
    _event(t, "step", step=step)


def _on_leave(t, data, story, step):
    if step in DEAL_STEPS:                     # unanswered offers lapse when the window closes
        kinds = DEAL_STEPS[step]
        t.run(f"UPDATE h_deals SET status = 'expired' WHERE status = 'offered' AND kind IN "
              f"({','.join('?' * len(kinds))})", kinds)
    if step == "interrogations":
        verify_interrogation_deals(t, data)
    if step in ("ballots3", "ballot4"):
        verify_bribes(t, 3 if step == "ballots3" else 4)


def _on_enter(t, data, story, step):
    s = state(t)
    extra = s["extra"]
    if step == "interrogations" and not s["turn_order"]:
        infl = influence(t, data)
        order = eng.ranking(infl, [c for c in data["order"] if c in players(t, data)])
        t.run("UPDATE hollow_state SET turn_order = ? WHERE id = 1", (json.dumps(order),))
    if step == "revelations" and not s["ledger"]:
        t.run("UPDATE hollow_state SET ledger = ? WHERE id = 1", (json.dumps(ledger(t, data)),))
    if step == "hotseat":
        susp = suspicion(t, data, rounds=(1, 2))
        extra["hotseat"] = eng.ranking(susp, data["order"])[:2]
        _save_extra(t, extra)
        t.run("DELETE FROM h_hotseat")
    if step == "wagers" and not t.one("SELECT cid FROM h_wagers"):
        extra["nominee"] = eng.ranking(suspicion(t, data), data["order"])[0]
        _save_extra(t, extra)
    if step == "reveal":
        extra["outcome"] = outcome(t, data, story)
        _save_extra(t, extra)


def set_timer(t, minutes):
    if not isinstance(minutes, (int, float)) or not 0 <= minutes <= 60:
        raise GameError(400, "Choose 0–60 minutes.")
    end = _iso(_now() + timedelta(minutes=minutes)) if minutes else ""
    t.run("UPDATE hollow_state SET timer_end = ? WHERE id = 1", (end,))


def set_paused(t, paused):
    t.run("UPDATE hollow_state SET paused = ? WHERE id = 1", (1 if paused else 0,))
    _event(t, "pause" if paused else "resume")


CUES = {"thunder", "lightning", "scream", "ghost", "bell", "safety"}


def cue(t, name):
    if name not in CUES:
        raise GameError(400, "Unknown cue.")
    if name == "safety":
        set_paused(t, True)
    _event(t, "cue", cue=name)


# ---------------------------------------------------------------- polls

def answer_poll(t, data, cid, poll_id, choice):
    _require_step(t, "polls")
    if poll_id not in {p["id"] for p in data["polls"]}:
        raise GameError(400, "Unknown question.")
    if choice not in players(t, data):
        raise GameError(400, "Choose one of the guests.")
    t.run("DELETE FROM h_polls WHERE cid = ? AND poll_id = ?", (cid, poll_id))
    t.run("INSERT INTO h_polls (cid, poll_id, choice) VALUES (?, ?, ?)", (cid, poll_id, choice))


def poll_results(t, data, story):
    rows = t.all("SELECT poll_id, choice FROM h_polls")
    out = []
    for p in data["polls"]:
        counts = {}
        for r in rows:
            if r["poll_id"] == p["id"]:
                counts[r["choice"]] = counts.get(r["choice"], 0) + 1
        if counts:
            top = max(counts.values())
            winners = [nick(story, data, c) for c in data["order"] if counts.get(c) == top]
            out.append({"text": p["text"], "winners": winners, "votes": top})
    return out


# ---------------------------------------------------------------- ballots

def cast_ballot(t, data, cid, kind, round_, picks):
    s = _require_step(t, *BALLOT_STEPS)
    if (kind, round_) not in BALLOT_STEPS[s["step"]]:
        raise GameError(409, "That ballot isn't open.")
    who = players(t, data)
    if (not isinstance(picks, list) or len(picks) != 3 or len(set(picks)) != 3
            or any(p not in who for p in picks)):
        raise GameError(400, "Choose three different guests.")
    if cid in picks:
        raise GameError(400, "You can't vote for yourself.")
    t.run("DELETE FROM h_ballots WHERE kind = ? AND round = ? AND voter = ?", (kind, round_, cid))
    t.run("INSERT INTO h_ballots (kind, round, voter, picks, created_at) VALUES (?, ?, ?, ?, ?)",
          (kind, round_, cid, json.dumps(picks), _iso()))


def _closed(t, kind, round_):
    """A ballot counts on the boards only once its window has closed."""
    return hc.STEP_IDS.index(state(t)["step"]) > hc.STEP_IDS.index(BALLOT_AT[(kind, round_)])


def ballots(t, kind, rounds=None, closed_only=True):
    out = []
    for r in t.all("SELECT * FROM h_ballots WHERE kind = ? ORDER BY round, voter", (kind,)):
        if rounds and r["round"] not in rounds:
            continue
        if closed_only and not _closed(t, kind, r["round"]):
            continue
        out.append(json.loads(r["picks"]))
    return out


def influence(t, data):
    return eng.influence(ballots(t, INHERITANCE), ballots(t, SUSPICION), data["order"])


def suspicion(t, data, rounds=None):
    return eng.tally(ballots(t, SUSPICION, rounds))


def my_ballots(t, cid):
    return {f"{r['kind']}-{r['round']}": json.loads(r["picks"])
            for r in t.all("SELECT * FROM h_ballots WHERE voter = ?", (cid,))}


# ---------------------------------------------------------------- money

def transfers(t, data, statuses=("honored",)):
    rows = t.all("SELECT * FROM h_deals")
    return [(data["family_of"][d["payer"]], data["family_of"][d["recipient"]], d["amount"])
            for d in rows if d["status"] in statuses]


def projection(t, data):
    fams = data["families"]
    return eng.projection(data["game"]["total_estate"], fams, influence(t, data), transfers(t, data), list(fams))


def available(t, data, cid):
    """The most this player's family can still promise: its projected share minus
    everything it has already offered or agreed to pay."""
    fam = data["family_of"][cid]
    owed = sum(d["amount"] for d in t.all("SELECT * FROM h_deals WHERE status IN ('offered', 'accepted')")
               if data["family_of"][d["payer"]] == fam)
    return max(0, projection(t, data)[fam] - owed)


def _check_amount(t, data, cid, amount):
    step = data["game"]["deal_step"]
    if not isinstance(amount, int) or amount <= 0 or amount % step or amount > MAX_AMOUNT:
        raise GameError(400, f"Amounts are in steps of £{step:,}.")
    if data["game"]["rules"]["cap_offers_to_projection"] and amount > available(t, data, cid):
        raise GameError(409, f"Your family can only promise up to £{available(t, data, cid):,} right now.")


# ---------------------------------------------------------------- deals

def offer_deal(t, data, cid, kind, recipient, amount, terms):
    s = _require_step(t, *DEAL_STEPS)
    if kind not in DEAL_STEPS[s["step"]]:
        raise GameError(409, "That kind of deal isn't available now.")
    who = players(t, data)
    if recipient not in who or recipient == cid:
        raise GameError(400, "Choose another guest.")
    if data["family_of"][recipient] == data["family_of"][cid]:
        raise GameError(400, "You can't make a deal with your own family.")
    terms = terms or {}
    clean = {}
    if kind == "force":
        target, track, qid = terms.get("target"), terms.get("track"), terms.get("qid")
        if target not in who or target == recipient:
            raise GameError(400, "Choose who they must question.")
        if track not in data["questions"] or qid not in {q["id"] for q in data["questions"][track]}:
            raise GameError(400, "Choose the question.")
        if _combo_taken(t, target, track, qid, by=recipient):
            raise GameError(409, "That question has already been reserved or asked.")
        clean = {"target": target, "track": track, "qid": qid}
    if kind == "bribe":
        subject, position = terms.get("subject"), terms.get("position")
        if subject not in who or subject == recipient or position not in (1, 2, 3):
            raise GameError(400, "Choose who they must rank, and where.")
        clean = {"subject": subject, "position": position, "round": BRIBE_ROUND[s["step"]]}
    _check_amount(t, data, cid, amount)
    t.run("INSERT INTO h_deals (id, kind, payer, recipient, amount, terms, status, created_at, decided_at) "
          "VALUES (?, ?, ?, ?, ?, ?, 'offered', ?, '')", (_id(), kind, cid, recipient, amount, json.dumps(clean), _iso()))


def decide_deal(t, data, cid, deal_id, accept):
    s = _require_step(t, *DEAL_STEPS)
    d = t.one("SELECT * FROM h_deals WHERE id = ?", (deal_id,))
    if not d or d["recipient"] != cid:
        raise GameError(404, "That offer isn't yours to answer.")
    if d["status"] != "offered":
        raise GameError(409, "That offer has already been answered.")
    if d["kind"] not in DEAL_STEPS[s["step"]]:
        raise GameError(409, "That offer has lapsed.")
    if accept and d["kind"] == "force":
        tm = json.loads(d["terms"])
        if _combo_taken(t, tm["target"], tm["track"], tm["qid"], by=cid):
            raise GameError(409, "Someone else has already reserved that question.")
    t.run("UPDATE h_deals SET status = ?, decided_at = ? WHERE id = ?",
          ("accepted" if accept else "rejected", _iso(), deal_id))


def _combo_taken(t, target, track, qid, by):
    if t.one("SELECT id FROM h_interrogations WHERE target = ? AND track = ? AND qid = ?", (target, track, qid)):
        return True
    for d in t.all("SELECT * FROM h_deals WHERE kind = 'force' AND status IN ('accepted', 'honored', 'broken')"):
        tm = json.loads(d["terms"])
        if (tm["target"], tm["track"], tm["qid"]) == (target, track, qid) and d["recipient"] != by:
            return True
    return False


def verify_interrogation_deals(t, data):
    asked = t.all("SELECT * FROM h_interrogations WHERE qid <> ''")
    for d in t.all("SELECT * FROM h_deals WHERE kind IN ('protect', 'protect_couple', 'force') "
                   "AND status IN ('accepted', 'honored', 'broken')"):
        mine = [q for q in asked if q["interrogator"] == d["recipient"]]
        if d["kind"] == "force":
            tm = json.loads(d["terms"])
            ok = any((q["target"], q["track"], q["qid"]) == (tm["target"], tm["track"], tm["qid"]) for q in mine)
        else:
            protected = {d["payer"]} | ({spouse(data, d["payer"])} if d["kind"] == "protect_couple" else set())
            ok = not any(q["target"] in protected for q in mine)
        t.run("UPDATE h_deals SET status = ? WHERE id = ?", ("honored" if ok else "broken", d["id"]))


def verify_bribes(t, round_):
    for d in t.all("SELECT * FROM h_deals WHERE kind = 'bribe' AND status IN ('accepted', 'honored', 'broken')"):
        tm = json.loads(d["terms"])
        if tm["round"] != round_:
            continue
        b = t.one("SELECT picks FROM h_ballots WHERE kind = ? AND round = ? AND voter = ?",
                  (INHERITANCE, round_, d["recipient"]))
        ok = bool(b) and json.loads(b["picks"])[tm["position"] - 1] == tm["subject"]
        t.run("UPDATE h_deals SET status = ? WHERE id = ?", ("honored" if ok else "broken", d["id"]))


def ledger(t, data):
    """Per person: money paid and received in honoured deals (no counterparties)."""
    out = {c: {"paid": 0, "received": 0} for c in data["order"]}
    for d in t.all("SELECT * FROM h_deals WHERE status = 'honored'"):
        out[d["payer"]]["paid"] += d["amount"]
        out[d["recipient"]]["received"] += d["amount"]
    return out


# ---------------------------------------------------------------- interrogations

def turn_order(t):
    s = state(t)
    return json.loads(s["turn_order"]) if s["turn_order"] else []


def current_turn(t):
    """(index, interrogator, row) for the turn in progress; interrogator None when all done."""
    order = turn_order(t)
    rows = t.all("SELECT * FROM h_interrogations ORDER BY turn")
    for row in rows:
        if not row["answer"]:
            return row["turn"], row["interrogator"], row
    n = len(rows)
    return n, (order[n] if n < len(order) else None), None


def interrogate(t, data, cid, target, track, qid):
    _require_step(t, "interrogations")
    n, who, row = current_turn(t)
    if who != cid or row:
        raise GameError(409, "It isn't your turn to ask.")
    if target not in players(t, data) or target == cid:
        raise GameError(400, "Choose someone else to question.")
    if track not in data["questions"] or qid not in {q["id"] for q in data["questions"][track]}:
        raise GameError(400, "Choose a question.")
    if _combo_taken(t, target, track, qid, by=cid):
        raise GameError(409, "That exact question has already been asked of them, or someone has reserved it.")
    t.run("INSERT INTO h_interrogations (id, turn, interrogator, target, track, qid, answer, answer_text, created_at) "
          "VALUES (?, ?, ?, ?, ?, ?, '', '', ?)", (_id(), n, cid, target, track, qid, _iso()))
    _event(t, "question", turn=n)


def answer_interrogation(t, data, story, cid, choice):
    _require_step(t, "interrogations")
    n, who, row = current_turn(t)
    if not row or row["target"] != cid:
        raise GameError(409, "Nobody is questioning you right now.")
    if choice not in ("truth", "lie"):
        raise GameError(400, "Choose an answer.")
    case = get_case(t, data, story)
    truth, lie = hc.answer(data, case, cid, row["qid"], story)
    t.run("UPDATE h_interrogations SET answer = ?, answer_text = ? WHERE id = ?",
          (choice, truth if choice == "truth" else lie, row["id"]))
    _event(t, "answer", turn=n)


def skip_turn(t, data):
    """Host: skip a turn (player absent, or stuck answering)."""
    _require_step(t, "interrogations")
    n, who, row = current_turn(t)
    if row:
        t.run("UPDATE h_interrogations SET answer = 'skipped' WHERE id = ?", (row["id"],))
    elif who:
        t.run("INSERT INTO h_interrogations (id, turn, interrogator, target, track, qid, answer, answer_text, created_at) "
              "VALUES (?, ?, ?, '', '', '', 'skipped', '', ?)", (_id(), n, who, _iso()))


def question_text(data, track, qid):
    return next((q["text"] for q in data["questions"].get(track, []) if q["id"] == qid), "")


def truth_board(t, data):
    out = {}
    for c in data["order"]:
        rows = t.all("SELECT answer FROM h_interrogations WHERE target = ? AND answer IN ('truth', 'lie')", (c,))
        out[c] = "NOT QUESTIONED" if not rows else ("LIED" if any(r["answer"] == "lie" for r in rows) else "TOLD THE TRUTH")
    return out


# ---------------------------------------------------------------- private investigations

def investigate(t, data, cid, target, category):
    _require_step(t, "investigations")
    fam = data["family_of"][cid]
    if t.one("SELECT family FROM h_investigations WHERE family = ?", (fam,)):
        raise GameError(409, "Your family has already used its investigation.")
    if target not in players(t, data) or data["family_of"][target] == fam:
        raise GameError(400, "Choose a guest from another family.")
    if category not in {c["id"] for c in data["categories"]}:
        raise GameError(400, "Choose what to investigate.")
    if t.one("SELECT family FROM h_investigations WHERE target = ? AND category = ?", (target, category)):
        raise GameError(409, "Another family is already looking into exactly that. Choose something else.")
    t.run("INSERT INTO h_investigations (family, opener, target, category, status, offer, counter, created_at) "
          "VALUES (?, ?, ?, ?, 'waiting', 0, 0, ?)", (fam, cid, target, category, _iso()))


def _inv(t, family):
    row = t.one("SELECT * FROM h_investigations WHERE family = ?", (family,))
    if not row:
        raise GameError(404, "No such investigation.")
    return row


def _suppress(t, inv, payer, amount):
    t.run("INSERT INTO h_deals (id, kind, payer, recipient, amount, terms, status, created_at, decided_at) "
          "VALUES (?, 'suppress', ?, ?, ?, ?, 'honored', ?, ?)",
          (_id(), payer, inv["opener"], amount, json.dumps({"category": inv["category"]}), _iso(), _iso()))
    t.run("UPDATE h_investigations SET status = 'suppressed' WHERE family = ?", (inv["family"],))


def target_respond(t, data, cid, family, action, amount=0):
    """The investigated guest: offer money to keep it quiet, or let it go."""
    _require_step(t, "investigations")
    inv = _inv(t, family)
    if inv["target"] != cid:
        raise GameError(403, "That investigation isn't about you.")
    if inv["status"] == "waiting" and action == "decline":
        t.run("UPDATE h_investigations SET status = 'delivered' WHERE family = ?", (family,))
    elif inv["status"] == "waiting" and action == "offer":
        _check_amount(t, data, cid, amount)
        t.run("UPDATE h_investigations SET status = 'offered', offer = ? WHERE family = ?", (amount, family))
    elif inv["status"] == "countered" and action in ("accept", "reject"):
        if action == "accept":
            _check_amount(t, data, cid, inv["counter"])
            _suppress(t, inv, cid, inv["counter"])
        else:
            t.run("UPDATE h_investigations SET status = 'delivered' WHERE family = ?", (family,))
    else:
        raise GameError(409, "That can't be done now.")


def investigator_decide(t, data, cid, action, amount=0):
    """The investigating couple: accept the hush money, reject it, or counter once."""
    _require_step(t, "investigations")
    inv = _inv(t, data["family_of"][cid])
    if inv["status"] == "waiting" and action == "open":
        created = datetime.fromisoformat(inv["created_at"])
        if (_now() - created).total_seconds() < REVEAL_AFTER:
            raise GameError(409, "Give them a minute to respond.")
        t.run("UPDATE h_investigations SET status = 'delivered' WHERE family = ?", (inv["family"],))
        return
    if inv["status"] != "offered":
        raise GameError(409, "There's no offer to answer.")
    if action == "accept":
        _suppress(t, inv, inv["target"], inv["offer"])
    elif action == "reject":
        t.run("UPDATE h_investigations SET status = 'delivered' WHERE family = ?", (inv["family"],))
    elif action == "counter":
        step = data["game"]["deal_step"]
        if not isinstance(amount, int) or amount <= inv["offer"] or amount % step or amount > MAX_AMOUNT:
            raise GameError(400, "A counter must be higher than their offer.")
        t.run("UPDATE h_investigations SET status = 'countered', counter = ? WHERE family = ?", (amount, inv["family"]))
    else:
        raise GameError(400, "Unknown answer.")


# ---------------------------------------------------------------- hot seat, wagers, verdict

def hotseat_choose(t, data, cid, choice, target=None):
    s = _require_step(t, "hotseat")
    pair = s["extra"].get("hotseat", [])
    if cid not in pair:
        raise GameError(403, "You're not in the hot seat.")
    if t.one("SELECT cid FROM h_hotseat WHERE cid = ?", (cid,)):
        raise GameError(409, "You've already chosen.")
    if choice == "accuse":
        if target not in players(t, data) or target in pair:
            raise GameError(400, "Accuse one of the other six guests.")
    elif choice == "defend":
        target = cid
    else:
        raise GameError(400, "Choose to defend or accuse.")
    t.run("INSERT INTO h_hotseat (cid, choice, target) VALUES (?, ?, ?)", (cid, choice, target))
    if len(t.all("SELECT cid FROM h_hotseat")) == len(pair):
        _event(t, "hotseat")


def hotseat_cards(t, data):
    """Both cards appear on the TV only once both suspects have chosen."""
    s = state(t)
    pair = s["extra"].get("hotseat", [])
    rows = {r["cid"]: r for r in t.all("SELECT * FROM h_hotseat")}
    if not pair or len(rows) < len(pair):
        return None
    cards, used = [], {}
    for cid in pair:
        r = rows[cid]
        if r["choice"] == "defend":
            card = hc.defend_card(data, cid)
        else:
            n = used.get(r["target"], 0)
            used[r["target"]] = n + 1
            card = hc.accuse_card(data, r["target"], n)
        cards.append({"cid": cid, "choice": r["choice"], "target": r["target"], **card})
    return cards


def place_wager(t, data, cid, side, amount):
    s = _require_step(t, "wagers")
    if side not in ("guilty", "not_guilty"):
        raise GameError(400, "Wager GUILTY or NOT GUILTY.")
    step = data["game"]["deal_step"]
    if not isinstance(amount, int) or amount < 0 or amount % step:
        raise GameError(400, f"Wagers are in steps of £{step:,}.")
    fam = data["family_of"][cid]
    limit = int(projection(t, data)[fam] * data["game"]["wager_share"]) // step * step
    if amount > limit:
        raise GameError(409, f"You can wager at most £{limit:,} (a quarter of your family's projected share).")
    t.run("DELETE FROM h_wagers WHERE cid = ?", (cid,))
    t.run("INSERT INTO h_wagers (cid, side, amount) VALUES (?, ?, ?)", (cid, side, amount))


def vote(t, data, cid, yes):
    _require_step(t, "verdict")
    if t.one("SELECT cid FROM h_votes WHERE cid = ?", (cid,)):
        raise GameError(409, "You've already voted.")
    t.run("INSERT INTO h_votes (cid, vote) VALUES (?, ?)", (cid, "yes" if yes else "no"))


def outcome(t, data, story):
    s = state(t)
    nominee = s["extra"].get("nominee")
    yes = len(t.all("SELECT cid FROM h_votes WHERE vote = 'yes'"))
    infl = influence(t, data)
    fam_order = list(data["families"])
    convicted, eliminated = eng.eliminate(yes, nominee, data["family_of"],
                                          eng.family_influence(infl, data["families"]), fam_order)
    wagers = {r["cid"]: (r["side"], r["amount"]) for r in t.all("SELECT * FROM h_wagers")}
    result = eng.settle(data["game"]["total_estate"], data["families"], infl, eliminated, transfers(t, data),
                        wagers, data["family_of"], nominee == s["culprit"], data["order"])
    return {
        "culprit": s["culprit"], "nominee": nominee, "yes": yes, "convicted": convicted,
        "correct": convicted == s["culprit"], "eliminated": eliminated, "payouts": result["payouts"],
        "wagers": result["wagers"],
    }


# ---------------------------------------------------------------- views

def _boards(t, data, story):
    """The only two competitive boards the TV may show."""
    out = {}
    if ballots(t, INHERITANCE):
        proj = projection(t, data)
        out["families"] = [{"name": data["families"][f]["name"], "amount": eng.round_display(proj[f])}
                           for f in sorted(proj, key=lambda f: -proj[f])]
    if ballots(t, SUSPICION):
        out["suspicion"] = [nick(story, data, c) for c in eng.ranking(suspicion(t, data), data["order"])]
    return out


def _public(case, step):
    return [{"id": p["id"], "title": p["title"], "text": p["text"]}
            for p in case["public"].values() if hc.reached(step, p["at"])]


def _interrogation_public(t, data, story):
    n, who, row = current_turn(t)
    order = turn_order(t)
    log = []
    for r in t.all("SELECT * FROM h_interrogations WHERE qid <> '' ORDER BY turn"):
        log.append({"turn": r["turn"], "asker": nick(story, data, r["interrogator"]),
                    "target": nick(story, data, r["target"]), "track": r["track"],
                    "question": question_text(data, r["track"], r["qid"]),
                    "answer": r["answer_text"] if r["answer"] in ("truth", "lie") else None})
    return {"order": [nick(story, data, c) for c in order], "turn": n,
            "asker": nick(story, data, who) if who else None, "log": log, "done": who is None}


def room_view(t, data, story):
    s = state(t)
    step = s["step"]
    case = get_case(t, data, story)
    script = data["script"].get(step, {})
    view = {
        "step": step, "phase": hc.PHASE_NAMES[hc.PHASE_OF[step]], "step_name": hc.STEP_NAME[step],
        "title": script.get("title", ""), "text": script.get("text", ""), "silas": script.get("silas", ""),
        "screen": script.get("screen", ""),          # what the TV shows while it reads `silas` aloud
        "sequence": script.get("sequence"),          # a timed show: lines, sounds and visuals in order
        "timer_end": s["timer_end"], "paused": bool(s["paused"]),
        "boards": _boards(t, data, story) if hc.reached(step, "ballot1") and step not in ("blackout",) else {},
        "events": [{"seq": e["seq"], "kind": e["kind"], **json.loads(e["data"])}
                   for e in t.all("SELECT * FROM h_events ORDER BY seq DESC LIMIT 30")],
    }
    if case:
        new_here = [p for p in case["public"].values() if p["at"] == step]
        view["evidence"] = [{"title": p["title"], "text": p["text"]} for p in new_here]
    if step == "covenant2":
        view["portraits"] = [{"name": nick(story, data, c), "photo": (story["by_id"].get(c, {}).get("card") or {}).get("photo", "")}
                             for c in data["order"]]
    if step == "reunion":
        view["guests"] = [{"name": full(story, data, c), "player": nick(story, data, c)} for c in data["order"]]
    if step == "poll_results":
        view["polls"] = poll_results(t, data, story)
    if step == "interrogations":
        view["interrogation"] = _interrogation_public(t, data, story)
    if step in ("revelations", "confront") and s["ledger"]:
        tb = truth_board(t, data)
        led = json.loads(s["ledger"])
        view["truth_board"] = [{"name": nick(story, data, c), "status": tb[c]} for c in data["order"]]
        rows = [{"name": nick(story, data, c), **led[c]} for c in data["order"]]
        view["ledger"] = rows
        spender = max(rows, key=lambda r: r["paid"])
        earner = max(rows, key=lambda r: r["received"])
        view["ledger_notes"] = {"spender": spender["name"] if spender["paid"] else None,
                                "earner": earner["name"] if earner["received"] else None}
    if step == "hotseat":
        view["hotseat"] = {"pair": [nick(story, data, c) for c in s["extra"].get("hotseat", [])],
                           "cards": [{**c, "who": nick(story, data, c["cid"]), "target": nick(story, data, c["target"])}
                                     for c in (hotseat_cards(t, data) or [])]}
    if step in ("wagers", "verdict", "reveal") and s["extra"].get("nominee"):
        view["nominee"] = full(story, data, s["extra"]["nominee"])
    if step == "verdict":
        view["votes_cast"] = len(t.all("SELECT cid FROM h_votes"))
    if step == "reveal" and case:
        o = s["extra"]["outcome"]
        view["reveal"] = {
            "culprit": full(story, data, o["culprit"]),
            "convicted": full(story, data, o["convicted"]) if o["convicted"] else None,
            "correct": o["correct"], "yes": o["yes"],
            "eliminated": data["families"][o["eliminated"]]["name"],
            "reconstruction": case["reconstruction"],
            "payouts": [{"name": data["families"][f]["name"], "amount": a}
                        for f, a in sorted(o["payouts"].items(), key=lambda kv: -kv[1])],
        }
    return view


def host_view(t, data, story):
    """Spoiler-free: progress and counts, never contents, never the murderer."""
    s = state(t)
    step = s["step"]
    who = players(t, data)
    counts = {}
    for kind, r in BALLOT_STEPS.get(step, []):
        counts[f"{kind.capitalize()} ballot {r}"] = len(t.all(
            "SELECT voter FROM h_ballots WHERE kind = ? AND round = ?", (kind, r)))
    if step == "polls":
        counts["Polls answered"] = len({r["cid"] for r in t.all("SELECT cid FROM h_polls")})
    if step == "investigations":
        counts["Investigations started"] = len(t.all("SELECT family FROM h_investigations"))
    if step == "wagers":
        counts["Wagers placed"] = len(t.all("SELECT cid FROM h_wagers"))
    if step == "verdict":
        counts["Votes cast"] = len(t.all("SELECT cid FROM h_votes"))
    if step in DEAL_STEPS:
        counts["Offers made"] = len(t.all("SELECT id FROM h_deals WHERE created_at >= ?", (s["updated_at"],)))
    if step == "hotseat":
        counts["Hot-seat choices"] = len(t.all("SELECT cid FROM h_hotseat"))
    view = {
        "ready": bool(s["culprit"]), "step": step, "paused": bool(s["paused"]), "timer_end": s["timer_end"],
        "steps": [{"id": sid, "phase": hc.PHASE_NAMES[ph], "name": name} for sid, ph, name in hc.STEPS],
        "players": len(who), "counts": counts, "total": len(who),
        "script": data["script"].get(step, {}),
        "cues": sorted(CUES - {"safety"}),
    }
    if step == "interrogations":
        view["interrogation"] = _interrogation_public(t, data, story)
    return view


def guest_view(t, data, story, guest):
    """One player's phone: their own dossier, released in stages, plus whatever they can do now."""
    cid = guest["character_id"]
    s = state(t)
    step = s["step"]
    case = get_case(t, data, story)
    if cid not in data["by_id"] or not case:
        return None
    c = data["by_id"][cid]
    me_culprit = case["culprit"] == cid
    who = players(t, data)
    others = [{"id": o, "name": nick(story, data, o), "full": full(story, data, o)} for o in data["order"]
              if o in who and o != cid]
    secret = dict(c["secret"])
    dossier = {
        "intro": c["intro"], "secret": secret,
        "need_alive": c["need_alive"],
        "scandal": {"about": nick(story, data, c["scandal"]["about"]), "text": c["scandal"]["text"]},
        "last_night": next(w["truth"] for w in data["witness_network"] if w["subject"] == cid),
        "traits_1867": {k: data["words"][c["traits"][k]] for k in ("feet", "sleeve", "scent")},
        "traits_1892": {k: data["words"][c["traits"][k]] for k in ("hand", "side", "sigil")},
        "family": data["families"][c["family"]]["name"],
        "spouse": nick(story, data, spouse(data, cid)),
        "spouse_id": spouse(data, cid),
    }
    if me_culprit:
        dossier["truth"] = case["truth"]
    view = {
        "me": cid, "step": step, "step_name": hc.STEP_NAME[step], "phase": hc.PHASE_NAMES[hc.PHASE_OF[step]],
        "paused": bool(s["paused"]), "timer_end": s["timer_end"], "others": others,
        "dossier": dossier,
        "memories": [m for tier, m in enumerate(case["memories"][cid], start=1) if hc.reached(step, hc.MEMORY_TIER_AT[tier])],
        "evidence": _public(case, step),
        "script": data["script"].get(step, {}),
    }
    if hc.reached(step, "murder"):
        net = data["witness_network"]
        view["witnessed"] = next(w["saw"] for w in net if w["witness"] == cid)
        view["seen_by_someone"] = True
        view["blackout"] = case["blackout"][cid]
    if hc.reached(step, "covenant"):
        dossier["silas_holds"] = c["secret"]["proof"]
    view["action"] = _action(t, data, story, cid, s, case)
    view["deals"] = _my_deals(t, data, story, cid)
    view["clues"] = _my_clues(t, data, story, cid, case)
    view["available"] = available(t, data, cid) if step in DEAL_STEPS or step == "investigations" else None
    return view


def _my_deals(t, data, story, cid):
    out = []
    for d in t.all("SELECT * FROM h_deals ORDER BY created_at"):
        if cid not in (d["payer"], d["recipient"]):
            continue
        tm = json.loads(d["terms"])
        out.append({"id": d["id"], "kind": d["kind"], "amount": d["amount"], "status": d["status"],
                    "mine": d["payer"] == cid, "payer": nick(story, data, d["payer"]),
                    "recipient": nick(story, data, d["recipient"]), "summary": _deal_summary(data, story, d["kind"], tm, d)})
    return out


def _deal_summary(data, story, kind, tm, d):
    payer, rec = nick(story, data, d["payer"]), nick(story, data, d["recipient"])
    if kind == "protect":
        return f"{payer} pays {rec} not to interrogate {payer}."
    if kind == "protect_couple":
        return f"{payer} pays {rec} not to interrogate {payer} or their spouse."
    if kind == "force":
        return (f"{payer} pays {rec} to ask {nick(story, data, tm['target'])}: "
                f"“{question_text(data, tm['track'], tm['qid'])}”")
    if kind == "bribe":
        return f"{payer} pays {rec} to rank {nick(story, data, tm['subject'])} #{tm['position']} in inheritance ballot {tm['round']}."
    if kind == "suppress":
        return f"{payer} paid {rec}'s family to drop a private investigation."
    return ""


def _my_clues(t, data, story, cid, case):
    out = []
    fam = data["family_of"][cid]
    for inv in t.all("SELECT * FROM h_investigations WHERE family = ? AND status = 'delivered'", (fam,)):
        cat = next(c for c in data["categories"] if c["id"] == inv["category"])
        out.append({"title": f"{cat['name']}: {nick(story, data, inv['target'])}",
                    "text": hc.investigation_clue(data, case, story, inv["target"], inv["category"])})
    return out


def _action(t, data, story, cid, s, case):
    """What this player can (or must) do right now."""
    step = s["step"]
    if s["paused"]:
        return {"kind": "paused"}
    if step == "polls":
        mine = {r["poll_id"]: r["choice"] for r in t.all("SELECT * FROM h_polls WHERE cid = ?", (cid,))}
        return {"kind": "polls", "polls": data["polls"], "answers": mine}
    if step in BALLOT_STEPS:
        return {"kind": "ballots", "ballots": [{"kind": k, "round": r} for k, r in BALLOT_STEPS[step]],
                "mine": my_ballots(t, cid)}
    if step in DEAL_STEPS:
        return {"kind": "deals", "allowed": list(DEAL_STEPS[step]), "questions": data["questions"],
                "round": BRIBE_ROUND.get(step)}
    if step == "interrogations":
        n, who, row = current_turn(t)
        out = {"kind": "interrogations", **_interrogation_public(t, data, story),
               "my_turn": who == cid and not row, "questions": data["questions"],
               "taken": [[r["target"], r["track"], r["qid"]] for r in t.all("SELECT * FROM h_interrogations WHERE qid <> ''")],
               "reserved": _reserved_for_others(t, cid)}
        if row and row["target"] == cid:
            truth, lie = hc.answer(data, case, cid, row["qid"], story)
            out["questioned"] = {"by": nick(story, data, row["interrogator"]),
                                 "question": question_text(data, row["track"], row["qid"]),
                                 "truth": truth, "lie": lie}
        return out
    if step == "investigations":
        fam = data["family_of"][cid]
        mine = t.one("SELECT * FROM h_investigations WHERE family = ?", (fam,))
        about_me = t.all("SELECT * FROM h_investigations WHERE target = ? AND status IN ('waiting', 'countered')", (cid,))
        cats = {c["id"]: c for c in data["categories"]}
        return {
            "kind": "investigations", "categories": data["categories"],
            "mine": mine and {"target": nick(story, data, mine["target"]), "category": cats[mine["category"]]["name"],
                              "status": mine["status"], "offer": mine["offer"], "counter": mine["counter"],
                              "can_open": mine["status"] == "waiting" and
                              (_now() - datetime.fromisoformat(mine["created_at"])).total_seconds() >= REVEAL_AFTER},
            "taken": [[r["target"], r["category"]] for r in t.all("SELECT * FROM h_investigations")],
            "about_me": [{"family": r["family"], "category": cats[r["category"]]["name"], "status": r["status"],
                          "counter": r["counter"]} for r in about_me],
        }
    if step == "hotseat":
        pair = s["extra"].get("hotseat", [])
        mine = t.one("SELECT * FROM h_hotseat WHERE cid = ?", (cid,))
        return {"kind": "hotseat", "in_seat": cid in pair, "chosen": bool(mine),
                "pair": [nick(story, data, c) for c in pair], "pair_ids": pair}
    if step == "wagers":
        fam = data["family_of"][cid]
        mine = t.one("SELECT * FROM h_wagers WHERE cid = ?", (cid,))
        limit = int(projection(t, data)[fam] * data["game"]["wager_share"]) // data["game"]["deal_step"] * data["game"]["deal_step"]
        return {"kind": "wagers", "nominee": full(story, data, s["extra"].get("nominee")) if s["extra"].get("nominee") else None,
                "limit": limit, "mine": mine and {"side": mine["side"], "amount": mine["amount"]}}
    if step == "verdict":
        mine = t.one("SELECT vote FROM h_votes WHERE cid = ?", (cid,))
        return {"kind": "verdict", "nominee": full(story, data, s["extra"].get("nominee")) if s["extra"].get("nominee") else None,
                "voted": mine["vote"] if mine else None}
    if step == "reveal":
        o = s["extra"].get("outcome", {})
        fam = data["family_of"][cid]
        return {"kind": "reveal", "family_payout": o.get("payouts", {}).get(fam, 0),
                "eliminated": o.get("eliminated") == fam, "correct": o.get("correct")}
    return {"kind": "watch"}


def _reserved_for_others(t, cid):
    out = []
    for d in t.all("SELECT * FROM h_deals WHERE kind = 'force' AND status = 'accepted'"):
        if d["recipient"] != cid:
            tm = json.loads(d["terms"])
            out.append([tm["target"], tm["track"], tm["qid"]])
    return out


# ---------------------------------------------------------------- guest actions (one entry point)

def act(t, data, story, guest, action, body):
    cid = guest["character_id"]
    if cid not in data["by_id"]:
        raise GameError(403, "You don't have a role yet.")
    b = body
    if action == "poll":
        return answer_poll(t, data, cid, b.get("poll_id"), b.get("choice"))
    if action == "polls":
        answers = b.get("answers")
        if not isinstance(answers, dict) or set(answers) != {p["id"] for p in data["polls"]}:
            raise GameError(400, "Please answer every question.")
        for poll_id, choice in answers.items():
            answer_poll(t, data, cid, poll_id, choice)
        return
    if action == "ballot":
        return cast_ballot(t, data, cid, b.get("kind"), b.get("round"), b.get("picks"))
    if action == "offer":
        return offer_deal(t, data, cid, b.get("kind"), b.get("recipient"), b.get("amount"), b.get("terms"))
    if action == "decide":
        return decide_deal(t, data, cid, b.get("id"), bool(b.get("accept")))
    if action == "interrogate":
        return interrogate(t, data, cid, b.get("target"), b.get("track"), b.get("qid"))
    if action == "answer":
        return answer_interrogation(t, data, story, cid, b.get("choice"))
    if action == "investigate":
        return investigate(t, data, cid, b.get("target"), b.get("category"))
    if action == "inv_target":
        return target_respond(t, data, cid, b.get("family"), b.get("action"), b.get("amount") or 0)
    if action == "inv_decide":
        return investigator_decide(t, data, cid, b.get("action"), b.get("amount") or 0)
    if action == "hotseat":
        return hotseat_choose(t, data, cid, b.get("choice"), b.get("target"))
    if action == "wager":
        return place_wager(t, data, cid, b.get("side"), b.get("amount"))
    if action == "vote":
        return vote(t, data, cid, bool(b.get("yes")))
    raise GameError(404, "Unknown action.")
