"""Short character versions for guests who want little preparation.

The AI condenses the role's authored biography, relationships and the evening
into a quick read. It never sees the role's secrets or objectives: those are
always shown word for word, because the game depends on them. The host
approves every short version before any guest sees it.
"""

from . import ai, casting

SHORT_LEVELS = (1, 2)   # answers to the preparation question that get a short version

SYSTEM = """You condense a murder-mystery character sheet for a party guest who wants very little reading.

Rules:
- Only shorten and rephrase. Never add a fact, name, motive, event or clue that isn't in the sheet.
- Write to the guest as "you", in a warm, playful tone.
- "who_you_are": at most 3 short sentences.
- "the_evening": at most 2 short sentences.
- One "relationships" entry for every person in the sheet, each at most 1 short sentence."""


def prep_level(guest):
    """'short' or 'full', from the guest's answer to the preparation question."""
    answer = casting.answers_of(guest).get("preparation")
    return "short" if answer in SHORT_LEVELS else "full"


def build_request(story, character):
    names = story["by_id"]
    rels = [{"name": names[r["with"]]["name"], "text": r["text"]} for r in character["relationships"]]
    sheet = (f"Character: {character['name']} ({character['tagline']})\n\n"
             f"Who you are: {character['biography']}\n\n"
             f"The evening: {story['setting']}\n\n"
             "People you know:\n" + "\n".join(f"- {r['name']}: {r['text']}" for r in rels))
    rel_names = [r["name"] for r in rels]
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["who_you_are", "the_evening", "relationships"],
        "properties": {
            "who_you_are": {"type": "string"},
            "the_evening": {"type": "string"},
            "relationships": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["name", "text"],
                "properties": {"name": {"type": "string", "enum": rel_names or [""]},
                               "text": {"type": "string"}}}},
        },
    }
    return sheet, schema, rel_names


def ask_claude(prompt, schema):
    return ai.ask_json(SYSTEM, prompt, schema, effort="low")


def make_short(story, character, ask=None):
    """Returns the short version as {who_you_are, the_evening, relationships}."""
    sheet, schema, rel_names = build_request(story, character)
    raw = (ask or ask_claude)(sheet, schema)
    seen, rels = set(), []
    for r in raw.get("relationships", []):              # one line per real relationship, in order
        if r.get("name") in rel_names and r["name"] not in seen:
            seen.add(r["name"])
            rels.append({"name": r["name"], "text": str(r.get("text", ""))[:300]})
    rels.sort(key=lambda r: rel_names.index(r["name"]))
    if len(rels) != len(rel_names) or not raw.get("who_you_are"):
        raise ai.AIError("The AI left something out of the short version. Try 'Make again'.")
    return {"who_you_are": str(raw["who_you_are"])[:600],
            "the_evening": str(raw.get("the_evening", ""))[:400],
            "relationships": rels}
