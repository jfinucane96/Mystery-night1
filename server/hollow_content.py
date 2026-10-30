"""The Hollow Order: loads content/hollow.json and builds the *case* for whichever
character was secretly chosen as Silas's murderer.

Backstory: Eleanor Vale died in the 1867 fire by accident, and all eight children
played some part in it (each character's childhood memory says what). There is ONE
mystery tonight: who killed Silas. Every case shares one canon (dossiers, the witness
network, memories); what changes per murderer is the weapon and the decoy in `cases`,
plus the clue logic below. The clues are fair by construction: the Covenant register
gives every guest a unique combination of signing hand, circle side and robe sigil,
and the public evidence reveals the murderer's three, one at a time.
No AI writes any of this at runtime."""

import json
import os

PATH = os.environ.get("MM_HOLLOW") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "content", "hollow.json")

STEPS = [
    ("lobby", 0, "Before the party"),
    ("reunion", 1, "The Reunion: introductions"),
    ("polls", 1, "Who's Most Likely To…?"),
    ("poll_results", 1, "Poll results"),
    ("covenant", 2, "The Covenant, part 1: robes on"),
    ("covenant2", 2, "Begin the Covenant (blackout, then Silas's revelation)"),
    ("envelopes", 2, "Silas's envelopes: everyone reads their secret"),
    ("covenant3", 2, "Silas returns: the rules of the inheritance"),
    ("ballot1", 2, "Inheritance ballot 1"),
    ("blackout", 3, "Blackout"),
    ("murder", 3, "Lights up: Silas is dead"),
    ("ghost", 3, "The ghost returns"),
    ("ballots2", 3, "Inheritance ballot 2 + suspicion ballot 1"),
    ("deals_intro", 4, "Silas explains the deals"),
    ("deals", 4, "First deal window (offers open)"),
    ("interrogations", 4, "Public interrogations"),
    ("investigations", 4, "Private investigations"),
    ("bribes4", 4, "Inheritance bribes"),
    ("ballots3", 4, "Inheritance ballot 3 + suspicion ballot 2"),
    ("revelations", 5, "Silas's revenge: truth board and ledger"),
    ("confront", 5, "Confrontation"),
    ("temptation", 5, "The Last Temptation"),
    ("ballot4", 5, "Final inheritance ballot"),
    ("hotseat", 5, "The Devil's Defense"),
    ("suspicion3", 5, "Final suspicion ballot"),
    ("bridge", 5, "The ghost's last evidence"),
    ("wagers", 6, "The Devil's Wager"),
    ("verdict", 6, "The Final Tribunal"),
    ("reveal", 6, "The reveal and payouts"),
]
# Target minutes for each step (about three hours from the Reunion to the reveal)
PLAN_MINUTES = {
    "lobby": 0, "reunion": 15, "polls": 8, "poll_results": 5,
    "covenant": 5, "covenant2": 6, "envelopes": 4, "covenant3": 4, "ballot1": 3,
    "blackout": 2, "murder": 12, "ghost": 4, "ballots2": 6,
    "deals_intro": 3, "deals": 7, "interrogations": 20, "investigations": 10, "bribes4": 4, "ballots3": 4,
    "revelations": 6, "confront": 5, "temptation": 5, "ballot4": 3, "hotseat": 10, "suspicion3": 3, "bridge": 4,
    "wagers": 5, "verdict": 6, "reveal": 10,
}
STEP_IDS = [s[0] for s in STEPS]
PHASE_OF = {s[0]: s[1] for s in STEPS}
STEP_NAME = {s[0]: s[2] for s in STEPS}
PHASE_NAMES = {0: "Arrival", 1: "Phase 1 · The Reunion", 2: "Phase 2 · The Covenant", 3: "Phase 3 · The Murder",
               4: "Phase 4 · The Order's Inquisition", 5: "Phase 5 · Silas's Revenge", 6: "Phase 6 · The Final Tribunal"}

# Which step releases what. The childhood memory is part of the dossier from the start.
MEMORY_TIER_AT = {1: "lobby"}
PUBLIC_AT = {"reunion": "reunion", "covenant": "covenant", "covenant2": "covenant2", "covenant3": "covenant3",
             "murder": "murder", "p4": "deals",
             "p5": "revelations", "bridge": "bridge"}
CLUE_TRAITS = ("hand", "side", "sigil")


class HollowError(Exception):
    pass


def reached(step, target):
    return STEP_IDS.index(step) >= STEP_IDS.index(target)


def load(path=PATH):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["by_id"] = {c["id"]: c for c in data["characters"]}
    data["family_of"] = {cid: c["family"] for cid, c in data["by_id"].items()}
    data["order"] = [c["id"] for c in data["characters"]]
    problems = check(data)
    if problems:
        raise HollowError("content/hollow.json has problems:\n  - " + "\n  - ".join(problems))
    return data


def check(data):
    """Validate the canon and every case variant before the party, not during it."""
    problems = []
    ids = data["order"]
    if len(ids) != 8:
        problems.append("there must be exactly eight characters")
    for f, fam in data["families"].items():
        if len(fam["members"]) != 2 or any(m not in ids for m in fam["members"]):
            problems.append(f"family '{f}' must have two known members")
    combos = [tuple(data["by_id"][c]["traits"][k] for k in CLUE_TRAITS) for c in ids]
    if len(set(combos)) != len(combos):
        problems.append("the Covenant register doesn't single out every character")
    # Locked witness network: one witness per subject, one subject per witness, never yourself
    net = data["witness_network"]
    if sorted(w["witness"] for w in net) != sorted(ids) or sorted(w["subject"] for w in net) != sorted(ids):
        problems.append("witness network must give everyone exactly one witness and one subject")
    if any(w["witness"] == w["subject"] for w in net):
        problems.append("nobody may witness their own act")
    qids = [q["id"] for qs in data["questions"].values() for q in qs]
    for c in data["characters"]:
        for field in ("intro", "secret", "need_alive", "scandal", "memories", "answers", "blackout"):
            if not c.get(field):
                problems.append(f"character '{c['id']}' is missing '{field}'")
        for q in qids:
            a = c.get("answers", {}).get(q)
            if not a or not a.get("truth") or not a.get("lie"):
                problems.append(f"character '{c['id']}' needs truth and lie answers for {q}")
    for cid in ids:
        case = data["cases"].get(cid)
        if not case:
            problems.append(f"no case written for murderer '{cid}'")
            continue
        problems += [f"case '{cid}': {p}" for p in check_case(data, cid)]
    return problems


def check_case(data, cid):
    case = data["cases"][cid]
    p = [f"missing '{f}'" for f in ("decoy", "weapon") if not case.get(f)]
    if p:
        return p
    decoy = case["decoy"]
    if decoy == cid or data["family_of"][decoy] == data["family_of"][cid]:
        p.append("the decoy must be from another family")
    if data["by_id"][decoy]["traits"]["hand"] == data["by_id"][cid]["traits"]["hand"]:
        p.append("the decoy must sign with the other hand, so the medical evidence clears them")
    if dagger_witness(cid) == cid:
        p.append("the dagger witness is the murderer")
    return p


def dagger_witness(culprit):
    return next(c for c in ("caroline", "jonathan", "taylor") if c != culprit)


def short(data, cid):
    """'Grant', 'Danielle': the character's first name."""
    return {"danielle": "Danielle"}.get(cid, cid.capitalize())


# ------------------------------------------------------------ building a case

def build(data, story, culprit):
    """Everything the game needs for one murderer, derived deterministically."""
    if culprit not in data["by_id"]:
        raise HollowError("unknown murderer")
    case = data["cases"][culprit]
    W = data["words"]
    ct = data["by_id"][culprit]["traits"]
    name = lambda c: story["by_id"][c]["name"] if c in story.get("by_id", {}) else short(data, c)
    nick = lambda c: short(data, c)
    decoy = case["decoy"]
    dt = data["by_id"][decoy]["traits"]
    secret = data["by_id"][culprit]["secret"]

    memories = {cid: list(data["by_id"][cid]["memories"]) for cid in data["order"]}

    # --- the blackout
    blackout = {cid: data["by_id"][cid]["blackout"] for cid in data["order"]}
    blackout[dagger_witness(culprit)] += f" Then quick footsteps, going toward the {ct['side']} side of the circle."
    kill = (f"When the lightning took the lights, you crossed the circle from the {ct['side']} arc and struck Silas down "
            f"with {case['weapon']}. You lifted the dagger from its stand and pushed it under the cushion of {name(decoy)}'s "
            f"chair, then went back to your place before the lights returned. As he fell, Silas tore at your robe: threads are "
            f"missing from your {W[ct['sigil']]} sigil, and there is wax from the tall candles on your hem.")
    blackout[culprit] = kill

    # --- public evidence
    register = "\n".join(f"{nick(c)}: signed {data['by_id'][c]['traits']['hand']}-handed, {data['by_id'][c]['traits']['side']} arc, "
                         f"{W[data['by_id'][c]['traits']['sigil']]} sigil" for c in data["order"])
    public = {}
    for pid, p in data["public"].items():
        public[pid] = {"id": pid, "title": p["title"], "text": p["text"], "at": PUBLIC_AT[p["phase"]]}
    if "register" in public:
        public["register"]["text"] += "\n" + register
    extra = [
        ("medical", "p4", "The doctor's examination",
         f"Silas was not killed with the ceremonial dagger: its blade is blunt and his wound is not a blade wound at all. "
         f"From the angle of the blow, the doctor is certain the attacker struck {ct['hand']}-handed."),
        ("dagger_found", "p4", "The dagger is found",
         f"The missing dagger has been found pushed under the cushion of {name(decoy)}'s chair. It has no blood on it. "
         f"{nick(decoy)} signed the Covenant register {dt['hand']}-handed."),
        ("wax", "p5", "A trail of wax",
         f"A trail of fresh wax drops runs across the chapel floor between the altar and the {ct['side']} arc of the circle, "
         f"where the tall candles stand."),
        ("sigil", "bridge", "What Silas held",
         f"Silas's right hand was closed tight when he fell. In it: a few torn threads of embroidery from a {W[ct['sigil']]} sigil."),
    ]
    for pid, at, t, text in extra:
        public[pid] = {"id": pid, "title": t, "text": text, "at": PUBLIC_AT[at]}

    # --- the reveal (Silas speaks to the murderer)
    reconstruction = [
        f"{name(culprit)}. It was you.",
        f"I knew what you had done, {nick(culprit)}. '{secret['title']}'. I had the proof, and tonight I meant to read it "
        f"aloud. So when the lightning took the lights, you crossed from the {ct['side']} arc and struck me down with "
        f"{case['weapon']}.",
        f"Not the dagger: you took that to hang the blame on {nick(decoy)}. But your {ct['hand']} hand gave you away, the "
        f"candle wax marked your path, and my fingers kept your {W[ct['sigil']]} thread.",
    ]
    return {
        "culprit": culprit, "memories": memories, "blackout": blackout, "public": public,
        "reconstruction": reconstruction, "decoy": decoy, "dagger_witness": dagger_witness(culprit),
        "truth": {
            "title": "Your terrible truth",
            "why": (f"Silas knew your darkest secret ('{secret['title']}') and had the proof. Tonight he was going to "
                    f"expose it in front of everyone, and take your family's fortune with it."),
            "tonight": kill,
            "cover": "Tonight you never left your place in the circle. Keep to that.",
            "goal": "Nobody must ever find out. Lie when you need to, deflect suspicion onto others, and make sure your family still inherits.",
        },
    }


# --------------------------------------------------- answers, clues and cards

def answer(data, case, target, qid, story):
    """The two scripted answers the target may choose from, as (truth, lie)."""
    base = data["by_id"][target]["answers"][qid]
    truth, lie = base["truth"], base["lie"]
    if target != case["culprit"]:
        return truth, lie
    decoy = story["by_id"][case["decoy"]]["name"] if case["decoy"] in story.get("by_id", {}) else case["decoy"]
    over = {
        "s1": ("I crossed the circle in the dark, to Silas.", truth),
        "s2": ("No one. I wasn't in my place.", truth),
        "s5": (f"I took it from the stand and hid it under {decoy}'s cushion.", truth),
    }
    return over.get(qid, (truth, lie))


CORROBORATION = {
    "grant": "The groundsman confirms a fuse was replaced in the basement panel last night and the outside lamps worked afterwards. Tonight's blackout was lightning on the generator house.",
    "grace": "A scrap recovered from the river shows the red stain is sealing-wax pigment, and the letters are thirty years old.",
    "jonathan": "The beneficiary schedule Silas wanted disclosed has been found in Jonathan's desk. It concerns farmland, nothing more.",
    "nanor": "Mr Thimble the solicitor confirms the argument was about the Ravenscroft claim, and that Nanor's 'obstacles' were three lawyers and a deed.",
    "griff": "The village constable confirms a known thief was roughed up last night, and Griff's stolen instruments have been returned to the inn.",
    "caroline": "A note in Silas's own hand: 'Lady C's remedy, feverfew and willow. Head much better. Bless her.'",
    "taylor": "Taylor's intermediary confirms Silas refused the money and sent it back with a polite note.",
    "danielle": "The wooden box holds a sapphire brooch and a pawnbroker's receipt. Nothing else.",
}
SEEN = {
    "grant": "Last night Grant was seen in the manor basement with the cover off the electrical panel. He hid behind the coal bunker when someone came.",
    "grace": "Last night Grace was seen on the river bridge throwing papers and a red-stained cloth into the water.",
    "jonathan": "Last night Jonathan was heard behind the inn telling Silas: 'If you tell them what you know, I'll make certain you never speak again.'",
    "nanor": "Last night Nanor was heard telling the solicitor that anything, or anyone, standing between him and his claim 'will have to disappear'.",
    "griff": "Last night Griff was seen paying two rough men and telling them to 'break a few bones if you have to'.",
    "caroline": "Last night Caroline was seen tipping a small vial into Silas's glass at the inn.",
    "taylor": "Last night Taylor was seen paying a stranger to 'make sure Silas does not come tomorrow'.",
    "danielle": "Last night Danielle was seen leaving Silas's rooms with a small wooden box, looking over her shoulder.",
}


def investigation_clue(data, case, story, target, category):
    """Truthful but incomplete private intelligence."""
    c = data["by_id"][target]
    n = short(data, target)
    culprit = target == case["culprit"]
    W = data["words"]
    if category == "motive":
        s = c["secret"]
        return f"Silas's file on {n}, marked '{s['title']}': {s['proof']} Silas had evidence that could ruin them."
    if category == "last_night":
        return f"{SEEN[target]} What really happened: {CORROBORATION[target]}"
    if category == "opportunity":
        side = c["traits"]["side"]
        others = [short(data, o) for o in data["order"] if data["by_id"][o]["traits"]["side"] == side and o != target]
        return f"{n} stood on the {side} arc of the circle, with {', '.join(others[:-1])} and {others[-1]}. In the dark nobody can swear {n} stayed put."
    if category == "physical":
        sig = W[c["traits"]["sigil"]]
        if culprit:
            return f"Threads are missing from {n}'s {sig} sigil, pinned over from the inside, and there is fresh candle wax on the hem of their robe."
        return f"{n}'s robe is undamaged: the {sig} sigil is intact, and there is no fresh wax on the hem."
    raise HollowError("unknown category")


def defend_card(data, cid):
    return {"title": f"In {short(data, cid)}'s defence", "text": CORROBORATION[cid]}


def accuse_card(data, target, n):
    if n == 0:
        return {"title": f"Against {short(data, target)}: a witness", "text": SEEN[target]}
    s = data["by_id"][target]["secret"]
    return {"title": f"Against {short(data, target)}: Silas's file", "text": f"Silas's file marked '{s['title']}': {s['proof']}"}
