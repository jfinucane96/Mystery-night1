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
        if "chapter" in c and not isinstance(c["chapter"], str):
            problems.append(f"character '{c.get('id')}': chapter must be text")
        for r in c.get("relationships", []):
            if r.get("with") not in ids:
                problems.append(f"character '{c.get('id')}' has a relationship with unknown id '{r.get('with')}'")
    if story["solution"].get("killer") not in ids:
        problems.append("solution.killer is not a character id")
    m = story.get("murder_announcement", {})
    if not (m.get("title") and m.get("text")):
        problems.append("murder_announcement needs a title and text")
    seen = set()
    for d in story.get("directions", []):
        if d.get("id") in seen or not d.get("id"):
            problems.append(f"direction id '{d.get('id')}' is missing or used twice")
        seen.add(d.get("id"))
        if d.get("character") not in ids:
            problems.append(f"direction '{d.get('id')}' is for unknown character '{d.get('character')}'")
        if not d.get("text"):
            problems.append(f"direction '{d.get('id')}' has no text")
    problems += check_knowledge(story, ids)
    return problems


STANCES = ("truth", "evade", "lie")


def check_knowledge(story, ids):
    """Facts, lies and evidence used by AI questions (P04)."""
    problems = []
    evidence_ids = [e.get("id") for e in story.get("evidence", [])]
    for e in story.get("evidence", []):
        if not (e.get("id") and e.get("name")):
            problems.append("every evidence item needs an id and a name")
    if len(evidence_ids) != len(set(evidence_ids)):
        problems.append("two evidence items share the same id")
    fact_ids = set()
    for c in story["characters"]:
        for f in c.get("knowledge", []):
            where = f"character '{c['id']}' fact '{f.get('id')}'"
            if not f.get("id") or f["id"] in fact_ids:
                problems.append(f"{where}: id is missing or used twice")
            fact_ids.add(f.get("id"))
            if not f.get("text") or not f.get("topics"):
                problems.append(f"{where}: needs text and topics")
            if f.get("source") not in ("firsthand", "heard"):
                problems.append(f"{where}: source must be 'firsthand' or 'heard'")
            if f.get("source") == "heard" and f.get("heard_from") not in ids:
                problems.append(f"{where}: heard_from must be a character id")
            w = f.get("when_asked", {})
            if w.get("stance") not in STANCES or not w.get("says"):
                problems.append(f"{where}: when_asked needs a stance ({'/'.join(STANCES)}) and 'says'")
            for ev in w.get("confess_with", []):
                if ev not in evidence_ids:
                    problems.append(f"{where}: confess_with names unknown evidence '{ev}'")
            if w.get("confess_with") and not w.get("confession"):
                problems.append(f"{where}: has confess_with but no confession text")
        # Every role must be able to answer the basics everyone asks
        topics = {tp for f in c.get("knowledge", []) for tp in f.get("topics", [])}
        for core, meaning in story.get("core_topics", {}).items():
            if core not in topics:
                problems.append(f"character '{c['id']}' needs a fact with topic '{core}' ({meaning})")
    return problems


def character_list(story):
    """For the host's casting screen: names, taglines and casting needs (no secrets)."""
    return [{"id": c["id"], "name": c["name"], "tagline": c["tagline"], "casting": c["casting"]}
            for c in story["characters"]]


def guest_view(story, guest, released, prep="light"):
    """Everything one guest may see. Contains ONLY their own character, and only
    once the host has released characters. `prep` decides whether their chapter
    opens first ('deep') or sits behind a button ('light')."""
    view = {
        "title": story["title"],
        "setting": story["setting"],
        "guest_name": guest["name"],
        "character": None,
    }
    c = story["by_id"].get(guest.get("character_id") or "")
    if c and released:
        names = story["by_id"]
        view["character"] = {
            "name": c["name"],
            "tagline": c["tagline"],
            "biography": c["biography"],
            "relationships": [{"name": names[r["with"]]["name"], "text": r["text"]}
                              for r in c["relationships"]],
            "secrets": list(c["secrets"]),
            "objectives": list(c["objectives"]),
            "chapter": c.get("chapter") or None,
            "prep": prep,
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
