"""The Hollow Order (v2): loads content/hollow.json and builds the *case* for
whichever character was secretly chosen as the murderer.

Every case shares one canon (dossiers, the witness network, the 1867 hiding
places). What changes per murderer is authored in `cases` (motive, routes,
weapon, decoy) and the clue logic below. The clues are fair by construction:
Nanny Pruitt's list gives every child a unique combination of footwear, sleeve
and scent; the Covenant register gives every adult a unique combination of
signing hand, chair side and robe sigil. Each mystery therefore has two
independent public routes (1867 and 1892) that both end at the same person.
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
    ("ballot1", 2, "Inheritance ballot 1"),
    ("blackout", 3, "Blackout"),
    ("murder", 3, "Lights up: Silas is dead"),
    ("ghost", 3, "The ghost returns"),
    ("ballots2", 3, "Inheritance ballot 2 + suspicion ballot 1"),
    ("deals", 4, "First deal window"),
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
STEP_IDS = [s[0] for s in STEPS]
PHASE_OF = {s[0]: s[1] for s in STEPS}
STEP_NAME = {s[0]: s[2] for s in STEPS}
PHASE_NAMES = {0: "Arrival", 1: "Phase 1 · The Reunion", 2: "Phase 2 · The Covenant", 3: "Phase 3 · The Second Murder",
               4: "Phase 4 · The Order's Inquisition", 5: "Phase 5 · Silas's Revenge", 6: "Phase 6 · The Final Tribunal"}

# Which step releases what
MEMORY_TIER_AT = {1: "reunion", 2: "covenant", 3: "bridge"}
PUBLIC_AT = {"reunion": "reunion", "covenant": "covenant", "covenant2": "covenant2", "murder": "murder", "p4": "deals", "p5": "revelations",
             "bridge": "bridge"}

FEET = {"boots": "the clack of hobnailed boots", "slippers": "the soft pad of house slippers"}


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
    # Trait combinations must identify exactly one person in each route
    for keys in (("feet", "sleeve", "scent"), ("hand", "side", "sigil")):
        combos = [tuple(data["by_id"][c]["traits"][k] for k in keys) for c in ids]
        if len(set(combos)) != len(combos):
            problems.append(f"traits {keys} don't single out every character")
    # Locked witness network: one witness per subject, one subject per witness, never yourself
    net = data["witness_network"]
    if sorted(w["witness"] for w in net) != sorted(ids) or sorted(w["subject"] for w in net) != sorted(ids):
        problems.append("witness network must give everyone exactly one witness and one subject")
    if any(w["witness"] == w["subject"] for w in net):
        problems.append("nobody may witness their own act")
    qids = [q["id"] for qs in data["questions"].values() for q in qs]
    for c in data["characters"]:
        for field in ("intro", "secret", "need_alive", "scandal", "hiding", "memories", "answers", "blackout"):
            if not c.get(field):
                problems.append(f"character '{c['id']}' is missing '{field}'")
        if len(c.get("memories", [])) != 3:
            problems.append(f"character '{c['id']}' needs three memories")
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
    p = []
    for field in ("entry", "exit", "decoy", "weapon", "fire_motive", "cover"):
        if not case.get(field):
            p.append(f"missing '{field}'")
    if p:
        return p
    if case["entry"] not in data["routes"] or case["exit"] not in data["exits"]:
        return ["unknown entry or exit route"]
    w = witnesses(data, cid)
    for role, who in w.items():
        if who == cid:
            p.append(f"the {role} witness is the murderer")
    decoy = case["decoy"]
    if decoy == cid or data["family_of"][decoy] == data["family_of"][cid]:
        p.append("the decoy must be from another family")
    if data["by_id"][decoy]["traits"]["hand"] == data["by_id"][cid]["traits"]["hand"]:
        p.append("the decoy must sign with the other hand, so the medical evidence clears them")
    # Fairness: the mandatory public evidence must point at exactly one person, by each route
    for route in (("feet", "sleeve", "scent"), ("hand", "side", "sigil")):
        target = tuple(data["by_id"][cid]["traits"][k] for k in route)
        matches = [c for c in data["order"] if tuple(data["by_id"][c]["traits"][k] for k in route) == target]
        if matches != [cid]:
            p.append(f"route {route} doesn't single out the murderer")
    return p


def witnesses(data, culprit):
    case = data["cases"][culprit]
    scent = "grace" if culprit != "grace" else "danielle"
    dagger = next(c for c in ("caroline", "jonathan", "taylor") if c != culprit)
    return {"entry": data["routes"][case["entry"]]["witness"], "exit": data["exits"][case["exit"]]["witness"],
            "scent": scent, "dagger": dagger}


def short(data, cid):
    """'Grant', 'Dani': the player's first name as used on cards."""
    names = {"danielle": "Danielle"}
    return names.get(cid, cid.capitalize())


def title(data, cid):
    return data["by_id"][cid].get("name") or short(data, cid)


# ------------------------------------------------------------ building a case

def build(data, story, culprit):
    """Everything the game needs for one murderer, derived deterministically."""
    if culprit not in data["by_id"]:
        raise HollowError("unknown murderer")
    case = data["cases"][culprit]
    W = data["words"]
    ct = data["by_id"][culprit]["traits"]
    w = witnesses(data, culprit)
    name = lambda c: story["by_id"][c]["name"] if c in story.get("by_id", {}) else short(data, c)
    nick = lambda c: short(data, c)
    entry, exit_ = data["routes"][case["entry"]], data["exits"][case["exit"]]

    # --- memories (three tiers per character)
    memories = {}
    for cid in data["order"]:
        m = list(data["by_id"][cid]["memories"])
        if cid == w["entry"]:
            m[1] += " " + {
                "gallery": "At a quarter past eight someone else passed your clock toward the east wing. Your breath had fogged the little glass and you couldn't see who, but you heard their feet on the gallery boards: ",
                "servants": "At a quarter past eight a hand reached past the pantry door, took one of your seven candles and went up the servants' stair. You heard their feet on the stone steps: ",
                "passage": "At a quarter past eight someone squeezed past you in the pitch dark of the passage, heading for the east wing. You never saw them, but you heard their feet: ",
            }[case["entry"]] + FEET[ct["feet"]] + "."
        if cid == w["exit"]:
            m[2] += " " + {
                "laundry": "And at twenty-five past eight someone ran into the laundry and scrubbed their hands at the pump for a long time. Through the gap under the copper's lid you saw only a sleeve: ",
                "conservatory": "And at twenty-five past eight someone came through the conservatory from the house and out onto the terrace, fast. Between the ferns you glimpsed only a sleeve: ",
                "terrace": "Whoever opened it climbed out onto the terrace roof and dropped into the garden. In the glow from the room you saw only a sleeve: ",
            }[case["exit"]] + f"{W[ct['sleeve']]}."
        if cid == w["scent"]:
            m[2] += " " + {
                "grace": "When Eleanor cried out, a smell came under the cupboard door, sharp even through the first smoke: ",
                "danielle": "With the first smoke through the grate came a sharp smell you have never forgotten: ",
            }[cid] + f"{W[ct['scent']]}."
        memories[cid] = m
    hiding = data["by_id"][culprit]["hiding"]
    memories[culprit] = [
        f"Hide-and-seek, summer 1867. You were meant to be hiding {hiding}. You did not stay there.",
        f"Eleanor showed you the letter before the game began. {case['fire_motive']} At a quarter past eight you went {entry['name']} to the records room and found her there.",
        f"She would not give it to you. You snatched it and held it to her candle. The papers on the desk caught, then the curtains. You went out, pulled the door shut behind you and heard the latch fall. You left by {exit_['name']} at twenty-five past eight and were back {hiding.split(',')[0]} before anyone smelled smoke.",
    ]

    # --- the blackout (1892)
    blackout = {cid: data["by_id"][cid]["blackout"] for cid in data["order"]}
    blackout[w["dagger"]] += f" Then quick footsteps, going toward the {ct['side']} side of the circle."
    kill = (f"When the lightning took the lights, you crossed the circle from the {ct['side']} arc and struck Silas down "
            f"with {case['weapon']}. You lifted the dagger from its stand and pushed it under the cushion of {name(case['decoy'])}'s "
            f"chair, then went back to your place before the lights returned. As he fell, Silas tore at your robe: threads are "
            f"missing from your {W[ct['sigil']]} sigil, and there is wax from the tall candles on your hem.")
    blackout[culprit] = kill

    # --- public evidence specific to this case
    nanny = "\n".join(f"{nick(c)}: {W[data['by_id'][c]['traits']['feet']]}, {W[data['by_id'][c]['traits']['sleeve']]} sleeves, "
                      f"{'bathed in lavender water' if data['by_id'][c]['traits']['scent'] == 'lavender' else 'rubbed with camphor oil for a cold'}"
                      for c in data["order"])
    register = "\n".join(f"{nick(c)}: signed {data['by_id'][c]['traits']['hand']}-handed, {data['by_id'][c]['traits']['side']} arc, "
                         f"{W[data['by_id'][c]['traits']['sigil']]} sigil" for c in data["order"])
    decoy = case["decoy"]
    dt = data["by_id"][decoy]["traits"]
    public = {}
    for pid, p in data["public"].items():
        public[pid] = {"id": pid, "title": p["title"], "text": p["text"], "at": PUBLIC_AT[p["phase"]]}
    public["nanny"]["text"] += "\n" + nanny
    public["register"]["text"] += "\n" + register
    extra = [
        ("soot", "p4", "The 1867 soot print",
         f"The parents' inquiry kept a sketch of a child's footprint in the soot just outside the records room: "
         + ("a sole studded with hobnails." if ct["feet"] == "boots" else "a smooth, soft sole without a single nail.")),
        ("medical", "p4", "The doctor's examination",
         f"Silas was not killed with the ceremonial dagger: its blade is blunt and his wound is not a blade wound at all. "
         f"From the angle of the blow, the doctor is certain the attacker struck {ct['hand']}-handed."),
        ("dagger_found", "p4", "The dagger is found",
         f"The missing dagger has been found pushed under the cushion of {name(decoy)}'s chair. It has no blood on it. "
         f"{nick(decoy)} signed the Covenant register {dt['hand']}-handed."),
        ("wax", "p5", "A trail of wax",
         f"A trail of fresh wax drops runs across the chapel floor between the altar and the {ct['side']} arc of the circle, "
         f"where the tall candles stand."),
        ("latch", "p5", "The thread on the latch",
         f"Kept in the 1867 inquiry box: a thread of {W[ct['sleeve']]} wool, caught on the records-room latch, the kind "
         f"that comes from a child's sleeve."),
        ("sigil", "bridge", "What Silas held",
         f"Silas's right hand was closed tight when he fell. In it: a few torn threads of embroidery from a {W[ct['sigil']]} sigil."),
        ("constable", "bridge", "The constable's note, 1867",
         f"'In the records room, a strong smell of {W[ct['scent']]}, quite distinct from the smoke.'"),
    ]
    for pid, at, t, text in extra:
        public[pid] = {"id": pid, "title": t, "text": text, "at": PUBLIC_AT[at]}

    # --- the reveal (Silas speaks to the murderer)
    reconstruction = [
        f"{name(culprit)}. It was you.",
        f"Twenty-five years ago: {case['fire_motive']} At a quarter past eight you went {entry['name']} to the records "
        f"room. {nick(w['entry'])} heard {FEET[ct['feet']]}. You held my sister's letter to her own candle, and when the fire "
        f"caught you pulled the door shut and left her behind it. You went out through {exit_['name']}, where "
        f"{nick(w['exit'])} saw your {W[ct['sleeve']]} sleeve, and {nick(w['scent'])} never forgot the {W[ct['scent']]}.",
        f"Tonight, when the lightning took the lights, you crossed from the {ct['side']} arc and struck me down with "
        f"{case['weapon'].replace('your ', 'your ')}. Not the dagger: you took that to hang the blame on {nick(decoy)}. "
        f"Your {ct['hand']} hand gave you away, and my fingers kept your {W[ct['sigil']]} thread.",
    ]
    return {
        "culprit": culprit, "witnesses": w, "memories": memories, "blackout": blackout, "public": public,
        "reconstruction": reconstruction, "decoy": decoy,
        "truth": {
            "title": "Your terrible truth",
            "fire": memories[culprit][1] + " " + memories[culprit][2],
            "tonight": kill,
            "cover": case["cover"] + " Tonight you never left your place in the circle. Keep to that.",
            "goal": "Nobody must ever find out. Lie when you need to, deflect suspicion onto others, and make sure your family still inherits.",
        },
    }


# --------------------------------------------------- answers, clues and cards

def answer(data, case, target, qid, story):
    """The two scripted answers the target may choose from, as (truth, lie)."""
    c = data["by_id"][target]
    base = c["answers"][qid]
    truth, lie = base["truth"], base["lie"]
    if target != case["culprit"]:
        return truth, lie
    decoy = story["by_id"][case["decoy"]]["name"] if case["decoy"] in story.get("by_id", {}) else case["decoy"]
    over = {
        "f1": ("In the records room, with Eleanor.", truth),
        "f2": ("Only Eleanor. I was the one in that room with her.", truth),
        "f3": ("A letter, signed by my father. I burned it.", truth),
        "f4": ("Yes. It was me she was arguing with.", "No. I heard nothing at all."),
        "f5": ("I left my hiding place and went up to the records room.", "Nothing. I was exactly where I said."),
        "s1": ("I crossed the circle in the dark, to Silas.", truth),
        "s2": ("No one. I wasn't in my place.", truth),
        "s5": (f"I took it from the stand and hid it under {decoy}'s cushion.", truth),
    }
    return over.get(qid, (truth, lie))


CORROBORATION = {
    "grant": "The groundsman confirms a fuse was replaced in the basement panel last night and the outside lamps worked afterwards. Tonight's blackout was lightning on the generator house.",
    "grace": "A scrap recovered from the river shows the red stain is sealing-wax pigment, and the letters are thirty years old.",
    "jonathan": "The beneficiary schedule Silas wanted disclosed has been found in Jonathan's desk. It concerns farmland, not the fire.",
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
    w = case["witnesses"]
    W = data["words"]
    hiding = c["hiding"]
    if category == "fire_memory":
        if culprit:
            return f"{n} says they spent the whole game hidden {hiding}, but can't describe a single thing they saw or heard there between a quarter past and half past eight."
        text = f"{n} hid {hiding}."
        if target == w["entry"]:
            text += f" At a quarter past eight they heard someone heading for the east wing: {FEET[data['by_id'][case['culprit']]['traits']['feet']]}."
        elif target in (w["exit"], w["scent"]):
            text += " They remember something about whoever fled the fire, but it only came back to them recently."
        else:
            text += " They saw nobody near the records room."
        return text
    if category == "fire_evidence":
        if culprit:
            return f"When the alarm was raised at twenty-five to nine, {n} was found back {hiding.split(',')[0]}, with fresh soot on both cuffs. Nanny Pruitt scrubbed them that night without a word."
        return f"When the alarm was raised at twenty-five to nine, Nanny Pruitt counted {n} among the children on the lawn: frightened, but with clean hands and cuffs."
    if category == "history":
        if culprit:
            return f"{n}'s account to the 1867 inquiry and the one they gave Silas this year both skip the same ten minutes. Nobody saw {n} between a quarter past and twenty-five past eight."
        return f"{n} admitted something to Silas this year: “{c['answers']['f5']['truth']}”"
    if category == "motive":
        s = c["secret"]
        return f"Silas's file on {n}, marked '{s['title']}': {s['proof']} Silas had evidence that could ruin them."
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
