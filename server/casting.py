"""The preference assessment and the casting rules that compare a guest's
answers and limits with what a character demands. No automatic casting:
this only produces warnings and blocks for the host's manual choices."""

import json
import os

ASSESSMENT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "content", "assessment.json")
LIMIT_CHOICES = ("ok", "no", "unsure")   # OK / Not OK / No preference


class AssessmentError(Exception):
    pass


def load(path=ASSESSMENT_PATH):
    with open(path, encoding="utf-8") as f:
        a = json.load(f)
    problems = []
    for q in a.get("questions", []):
        if len(q.get("options", [])) != 4:
            problems.append(f"question '{q.get('id')}' needs exactly 4 options")
    if len(a.get("questions", [])) != 6:
        problems.append("there should be exactly 6 questions")
    if problems:
        raise AssessmentError("content/assessment.json has problems:\n  - " + "\n  - ".join(problems))
    a["question_ids"] = [q["id"] for q in a["questions"]]
    a["limit_ids"] = [l["id"] for l in a["limits"]]
    a["by_id"] = {q["id"]: q for q in a["questions"]}
    return a


def check_story(story, assessment):
    """Problems with characters' casting info (called when the app starts)."""
    problems = []
    for c in story["characters"]:
        cast = c.get("casting")
        if not cast:
            problems.append(f"character '{c['id']}' is missing 'casting'")
            continue
        if cast.get("load") not in ("major", "low-pressure"):
            problems.append(f"character '{c['id']}': casting.load must be 'major' or 'low-pressure'")
        for q, level in cast.get("needs", {}).items():
            if q not in assessment["question_ids"] or level not in (1, 2, 3, 4):
                problems.append(f"character '{c['id']}': casting.needs '{q}: {level}' is not a question id with a level 1-4")
        for item in cast.get("content", []):
            if item not in assessment["limit_ids"]:
                problems.append(f"character '{c['id']}': casting.content '{item}' is not a limit id")
    killer = story["by_id"].get(story["solution"]["killer"])
    if killer and "killer" not in killer.get("casting", {}).get("content", []):
        problems.append("the killer's casting.content must include 'killer'")
    return problems


# --------------------------------------------------------------- guest data

def answers_of(guest):
    return json.loads(guest.get("answers") or "{}")


def limits_of(guest):
    return json.loads(guest.get("limits") or "{}")


def clean_answers(assessment, answers):
    """Keep only known questions with a choice of 1-4."""
    out = {}
    for q, v in (answers or {}).items():
        if q in assessment["by_id"] and v in (1, 2, 3, 4):
            out[q] = v
    return out


def clean_limits(assessment, limits):
    return {k: v for k, v in (limits or {}).items()
            if k in assessment["limit_ids"] and v in LIMIT_CHOICES}


def is_complete(assessment, answers, limits):
    return (all(q in answers for q in assessment["question_ids"])
            and all(l in limits for l in assessment["limit_ids"]))


# ----------------------------------------------------------------- the rules

def fit(assessment, guest, character):
    """How well one character suits one guest:
        blocked: the guest said Not OK to something in this role (can't assign)
        check:   the guest gave No preference / no answer to something sensitive (host must confirm)
        warnings: the role asks more than the guest said they'd enjoy (allowed, shown to the host)
    """
    answers, limits = answers_of(guest), limits_of(guest)
    labels = {l["id"]: l["prompt"].lower() for l in assessment["limits"]}
    blocked, check, warnings = [], [], []
    for item in character["casting"].get("content", []):
        choice = limits.get(item)
        if choice == "no":
            blocked.append(f"Said no to {labels[item]}")
        elif choice != "ok":
            check.append(f"Hasn't said whether they're OK with {labels[item]}")
    if not guest.get("assessment_done"):
        warnings.append("Hasn't finished the questions yet")
    for q, need in character["casting"].get("needs", {}).items():
        have = answers.get(q)
        label = assessment["by_id"][q]["label"]
        if have is not None and have < need:
            warnings.append(f"Role needs {label} {need}+, they chose {have}")
    return {"blocked": blocked, "check": check, "warnings": warnings}
