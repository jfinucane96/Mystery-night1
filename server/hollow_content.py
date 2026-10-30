"""The Hollow Order: loads content/hollow.json and builds the *case* for whichever
character was secretly chosen as Silas's murderer.

Backstory: Eleanor Vale died in the 1867 fire by accident, and all eight children
played some part in it (each character's childhood memory says what). There is ONE
mystery tonight: who killed Silas. Every case shares one canon (dossiers, the witness
network, memories); what changes per murderer is the decoy and hiding place in `cases`.
The mystery is solved by asking the right questions about the blackout: everyone's alibi,
who was with whom, and what they saw. Physical evidence is kept light (a planted dagger,
and a late sharp-or-blunt hint matched against the personal items guests carry).
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
    ("investigations", 4, "Private questions (one per couple)"),
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
    # Two guests per weapon kind, so the doctor's second look narrows it to exactly two
    groups = {}
    for c in data["characters"]:
        it = c.get("item") or {}
        if not all(it.get(k) for k in ("name", "short", "owner", "group")):
            problems.append(f"character '{c['id']}' needs an item with name, short, owner and group")
            continue
        if it["group"] not in data["clue_groups"]:
            problems.append(f"character '{c['id']}': unknown item group '{it['group']}'")
        groups.setdefault(it["group"], []).append(c["id"])
    for g, members in groups.items():
        if len(members) != 2 or data["family_of"][members[0]] == data["family_of"][members[1]]:
            problems.append(f"item group '{g}' must hold two guests from different families")
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
    p = [f"missing '{f}'" for f in ("decoy", "hidden") if not case.get(f)]
    if p:
        return p
    decoy = case["decoy"]
    if decoy == cid or data["family_of"][decoy] == data["family_of"][cid]:
        p.append("the decoy must be from another family")
    al = alibis(data, cid)
    if al["pairs"] is None or len(al["pairs"]) != 3:
        p.append("can't pair up the innocent guests")
    elif cid in (al["alone"], al["saw_alone"], al["heard"]):
        p.append("a key witness is the murderer")
    return p


# Where everyone was when the lights went out. After the first ballot the guests drifted around the
# house with their drinks; Silas stayed alone in the Great Hall. Simple rooms everyone knows.
LOCATIONS = ["in the Library", "in the Dining Room", "in the Drawing Room", "in the Kitchen"]
MURDER_ROOM = "the Great Hall"


def alibis(data, culprit):
    """Where everyone was in the dark. Six innocents were in pairs and vouch for each other;
    one innocent was alone by the window (the murderer's false alibi); a witness in one pair
    saw that person alone in a lightning flash; someone in another pair felt the killer pass."""
    order = data["order"]
    fam = data["family_of"]
    start = order.index(culprit)
    ring = [order[(start + k) % len(order)] for k in range(1, len(order))]
    alone = next(c for c in ring if fam[c] != fam[culprit])
    rest = [c for c in ring if c != alone]
    pairs = _pair_up(rest, fam)
    where = {alone: LOCATIONS[0]}
    partner = {}
    for k, (a, b) in enumerate(pairs):
        where[a] = where[b] = LOCATIONS[k + 1]
        partner[a], partner[b] = b, a
    return {"alone": alone, "where": where, "partner": partner, "pairs": pairs,
            "saw_alone": pairs[0][0], "heard": pairs[1][0], "claim": LOCATIONS[0]}


def _pair_up(people, fam):
    """Pairs with no married couples in them (deterministic)."""
    if not people:
        return []
    first = people[0]
    for other in people[1:]:
        if fam[other] != fam[first]:
            rest = [p for p in people[1:] if p != other]
            sub = _pair_up(rest, fam)
            if sub is not None:
                return [(first, other)] + sub
    return None


def short(data, cid):
    """'Grant', 'Danielle': the character's first name."""
    return {"danielle": "Danielle"}.get(cid, cid.capitalize())


def item(data, cid):
    return data["by_id"][cid]["item"]


# ------------------------------------------------------------ building a case

def build(data, story, culprit):
    """Everything the game needs for one murderer, derived deterministically.
    The mystery is solved by questioning: the murderer's alibi names a guest who was really
    alone, and one other guest saw that guest alone in a flash of lightning."""
    if culprit not in data["by_id"]:
        raise HollowError("unknown murderer")
    case = data["cases"][culprit]
    name = lambda c: story["by_id"][c]["name"] if c in story.get("by_id", {}) else short(data, c)
    nick = lambda c: short(data, c)
    decoy = case["decoy"]
    it = item(data, culprit)
    group = data["clue_groups"][it["group"]]
    secret = data["by_id"][culprit]["secret"]
    weapon_you = it["name"] if it["name"].startswith(("your ", "the ")) else it["name"]

    memories = {cid: list(data["by_id"][cid]["memories"]) for cid in data["order"]}
    al = alibis(data, culprit)
    x, w, v = al["alone"], al["saw_alone"], al["heard"]

    # --- the blackout: where everyone really was, and what they noticed
    blackout = {}
    for cid in data["order"]:
        if cid == culprit:
            continue
        loc = al["where"][cid]
        partner = al["partner"].get(cid)
        text = (f"When the lights went out you were {loc}, with {nick(partner)}. You held on to each other until they came back."
                if partner else f"When the lights went out you were {loc}, on your own. Nobody can vouch for you.")
        if cid == x:
            text += " You heard quick footsteps out in the corridor, going toward the Great Hall, but you couldn't see whose."
        if cid == w:
            text += f" In one flash of lightning you could see straight into the Library: {nick(x)} was in there, alone. Nobody else."
        if cid == v:
            text += " Just after the lights died, someone in a robe brushed hard past you in the doorway, hurrying toward the Great Hall. You never saw a face."
        blackout[cid] = text
    kill = (f"YOU KILLED SILAS. When the lightning took the lights, you slipped into the Great Hall, where Silas was "
            f"alone, and struck him down with {weapon_you}. "
            f"You pushed the ceremonial dagger under the cushion of {name(decoy)}'s chair to throw suspicion their way, "
            f"hid your {it['short']} {case['hidden']}, and were back before the lights returned.")
    blackout[culprit] = kill

    # --- public evidence (kept light: the questions do the work)
    public = {}
    for pid, p in data["public"].items():
        public[pid] = {"id": pid, "title": p["title"], "text": p["text"], "at": PUBLIC_AT[p["phase"]]}
    sharp = group["first"] == "sharp"
    extra = [
        ("dagger_found", "p4", "The dagger is found",
         f"The missing ceremonial dagger has been found pushed under the cushion of {name(decoy)}'s chair. "
         f"There is no blood on it, and its blade is far too blunt to have done this. Somebody wanted it found there."),
        ("medical", "bridge", "The doctor's report",
         "Silas was not killed with the ceremonial dagger. "
         + ("He was stabbed once, with something narrow and sharp." if sharp
            else "He was killed by a single heavy blow to the head with something hard and blunt.")),
    ]
    for pid, at, t, text in extra:
        public[pid] = {"id": pid, "title": t, "text": text, "at": PUBLIC_AT[at]}

    # --- the reveal (Silas speaks to the murderer)
    reconstruction = [
        f"{name(culprit)}. It was you.",
        f"I knew what you had done, {nick(culprit)}. '{secret['title']}'. I had the proof, and tonight I meant to read it "
        f"aloud. So when the lightning took the lights, you slipped into the Great Hall and struck me down with "
        f"your own {it['short']}.",
        f"You hid the dagger under {nick(decoy)}'s chair, and you told everyone you had been in the Library with "
        f"{nick(x)}. But {nick(x)} was alone, and {nick(w)} saw it in the lightning. A lie needs a witness, my dear. Yours had one.",
    ]
    return {
        "culprit": culprit, "memories": memories, "blackout": blackout, "public": public,
        "reconstruction": reconstruction, "decoy": decoy, "alibi": al,
        "truth": {
            "title": "Your terrible truth",
            "why": (f"Silas knew your darkest secret ('{secret['title']}') and had the proof. Tonight he was going to "
                    f"expose it in front of everyone, and take your family's fortune with it."),
            "tonight": kill,
            "cover": (f"Your story: you were {al['where'][x]} with {nick(x)} the whole time. {nick(x)} really was there, "
                      f"alone in the dark, so {nick(x)} can't prove you weren't. Never admit you went into the Great Hall."),
            "goal": "Nobody must ever find out. Lie when you need to, deflect suspicion onto others, and make sure your family still inherits.",
        },
    }


# --------------------------------------------------- answers, clues and cards

def answer(data, case, target, qid, story):
    """The two scripted answers the target may choose from, as (truth, lie)."""
    base = data["by_id"][target]["answers"][qid]
    truth, lie = base["truth"], base["lie"]
    if qid not in ("s1", "s2", "s5"):
        return truth, lie
    al = case["alibi"]
    nick = lambda c: short(data, c)
    x = al["alone"]
    if target == case["culprit"]:
        return {
            "s1": ("In the Great Hall. With Silas.", f"{al['claim'][0].upper()}{al['claim'][1:]}, with {nick(x)}."),
            "s2": ("Nobody. I was with Silas, and then he was dead.", f"{nick(x)}. We were together {al['claim']} the whole time."),
            "s5": ("Silas's last breath.", "Nothing at all. Just the thunder."),
        }[qid]
    where = al["where"][target]
    partner = al["partner"].get(target)
    seen = "Only the thunder, and my companion gripping my arm."
    if target == x:
        seen = "I heard quick footsteps out in the corridor, going toward the Great Hall. I couldn't see whose."
    if target == al["saw_alone"]:
        seen = f"In one flash of lightning I could see straight into the Library. {nick(x)} was in there, alone. Nobody else."
    if target == al["heard"]:
        seen = "Someone in a robe brushed hard past me in the doorway just after the lights died, hurrying toward the Great Hall. I never saw a face."
    return {
        "s1": (f"{where[0].upper()}{where[1:]}.", "I honestly don't know. It was pitch dark."),
        "s2": ((f"{nick(partner)}. We held on to each other until the lights came back." if partner
                else f"Nobody. I was on my own {where}, I'm afraid."),
               ("Nobody. I was alone." if partner else "I'm not sure. Someone may have been near me.")),
        "s5": (seen, "Nothing at all."),
    }[qid]


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
    """A couple's private question: the guest's true answer, for that couple only."""
    if category in {q["id"] for q in data["questions"]["silas"]}:
        truth, _ = answer(data, case, target, category, story)
        return f"{short(data, target)}, privately: “{truth}”"
    c = data["by_id"][target]
    n = short(data, target)
    culprit = target == case["culprit"]
    it = c["item"]
    if category == "motive":
        s = c["secret"]
        return f"Silas's file on {n}, marked '{s['title']}': {s['proof']} Silas had evidence that could ruin them."
    if category == "last_night":
        return f"{SEEN[target]} What really happened: {CORROBORATION[target]}"
    if category == "belongings":
        if culprit:
            return f"{n}'s {it['short']} is nowhere to be seen. {n} says it was left with their coat. It isn't with their coat."
        return f"{n}'s {it['short']} is with them, clean, exactly where it should be."
    if category == "physical":
        if culprit:
            return f"There are tiny dark spots on the cuff of {n}'s robe, hastily dabbed with water. They might be wine. They might not."
        return f"{n}'s robe and hands are spotless. Nothing out of place."
    raise HollowError("unknown category")


def defend_card(data, cid):
    return {"title": f"In {short(data, cid)}'s defence", "text": CORROBORATION[cid]}


def accuse_card(data, target, n):
    if n == 0:
        return {"title": f"Against {short(data, target)}: a witness", "text": SEEN[target]}
    s = data["by_id"][target]["secret"]
    return {"title": f"Against {short(data, target)}: Silas's file", "text": f"Silas's file marked '{s['title']}': {s['proof']}"}
