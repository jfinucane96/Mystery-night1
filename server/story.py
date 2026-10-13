"""Loads the fixed story from content/story.json and builds the *views* of it
that each person may see. These view functions are the only way story text
reaches a phone, so privacy is decided here and nowhere else."""

import json
import os

STORY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "content", "story.json")


class StoryError(Exception):
    pass


def load(path=STORY_PATH):
    with open(path, encoding="utf-8") as f:
        story = json.load(f)
    problems = check(story)
    if problems:
        raise StoryError("content/story.json has problems:\n  - " + "\n  - ".join(problems))
    story["by_id"] = {c["id"]: c for c in story["characters"]}
    return story


def check(story):
    """Catch typos in the story file before the party, not during it."""
    problems = []
    for field in ("title", "setting", "victim", "characters", "solution"):
        if field not in story:
            problems.append(f"missing '{field}'")
    if problems:
        return problems
    ids = [c.get("id") for c in story["characters"]]
    if len(ids) != len(set(ids)):
        problems.append("two characters share the same id")
    for c in story["characters"]:
        for field in ("id", "name", "tagline", "biography", "relationships", "secrets", "objectives"):
            if field not in c:
                problems.append(f"character '{c.get('id', '?')}' is missing '{field}'")
        for r in c.get("relationships", []):
            if r.get("with") not in ids:
                problems.append(f"character '{c.get('id')}' has a relationship with unknown id '{r.get('with')}'")
    if story["solution"].get("killer") not in ids:
        problems.append("solution.killer is not a character id")
    return problems


def character_list(story):
    """Names and taglines only: safe for the host's casting dropdown."""
    return [{"id": c["id"], "name": c["name"], "tagline": c["tagline"]} for c in story["characters"]]


def guest_view(story, guest):
    """Everything one guest may see. Contains ONLY their own character."""
    view = {
        "title": story["title"],
        "setting": story["setting"],
        "guest_name": guest["name"],
        "character": None,
    }
    c = story["by_id"].get(guest.get("character_id") or "")
    if c:
        names = story["by_id"]
        view["character"] = {
            "name": c["name"],
            "tagline": c["tagline"],
            "biography": c["biography"],
            "relationships": [{"name": names[r["with"]]["name"], "text": r["text"]}
                              for r in c["relationships"]],
            "secrets": list(c["secrets"]),
            "objectives": list(c["objectives"]),
        }
    return view


def host_solution(story):
    s = story["solution"]
    return {
        "victim": story["victim"],
        "killer": story["by_id"][s["killer"]]["name"],
        "motive": s["motive"],
        "method": s["method"],
        "summary": s.get("summary", ""),
    }
