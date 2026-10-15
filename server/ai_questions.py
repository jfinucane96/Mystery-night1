"""Turning a question into an answer, using only what the story allows.

The AI gets just the allowed facts or lines (never the solution) and must
say which ones it used; the server checks those ids. If the AI is down or
its answer doesn't check out, a simple keyword match against each fact's
topics picks the authored wording instead. If nothing fits, the question
counts as unknown and the token is refunded.
"""

from . import ai
from .questions import keyword_match

TIPS = {
    "truthful": "You're telling the truth. Say it plainly.",
    "evasive": "Dodge it. Smile, and change the subject.",
    "lying": "This is a lie. Say it with confidence and hold their gaze.",
    "confessing": "The game is up. Let it all come out. This is your big moment.",
}

REMEMBER_SYSTEM = """You are the memory of a character in a murder-mystery party game. A player asks what their character remembers.

Rules:
- Use ONLY the memories provided. Never invent people, events, times, objects or clues.
- If no memory answers the question, set matched to false.
- Things the character was told must be presented as things they heard, saying who told them, not as facts.
- Speak to the player as "you", in 1-3 sentences, with a little atmosphere.
- List every memory you used in fact_ids."""

INTERROGATE_SYSTEM = """You write the line a party guest will say out loud while playing a character who is being questioned in a murder-mystery game.

Rules:
- Use ONLY the lines provided. Each has a delivery: truthful, evasive, lying or confessing. Keep its meaning exactly; you may only rephrase it in the character's voice.
- Never add facts, names, times, places or clues that aren't in the lines you use.
- If a "confessing" line is provided, the character is confronted with evidence: use it.
- If no line relates to the question, set matched to false.
- "spoken": first person, 1-3 sentences, easy to say aloud.
- "acting_tip": one short private tip for the guest on how to deliver it.
- List every line you used in claim_ids."""


def _remember_schema(ids):
    return {"type": "object", "additionalProperties": False, "required": ["matched", "fact_ids", "answer"],
            "properties": {"matched": {"type": "boolean"},
                           "fact_ids": {"type": "array", "items": {"type": "string", "enum": ids or [""]}},
                           "answer": {"type": "string"}}}


def _interrogate_schema(ids):
    return {"type": "object", "additionalProperties": False, "required": ["matched", "claim_ids", "spoken", "acting_tip"],
            "properties": {"matched": {"type": "boolean"},
                           "claim_ids": {"type": "array", "items": {"type": "string", "enum": ids or [""]}},
                           "spoken": {"type": "string"},
                           "acting_tip": {"type": "string"}}}


def _ask_claude(system, prompt, schema):
    return ai.ask_json(system, prompt, schema, effort="low")


def answer_remember(character, question, memories, ask=None):
    """Returns ('answered', result) or ('unknown', result)."""
    ask = ask or _ask_claude
    ids = [m["fact_id"] for m in memories]
    if memories and (ask is not _ask_claude or ai.is_configured()):
        prompt = (f"Character: {character['name']} ({character['tagline']})\n\nMemories:\n"
                  + "\n".join(f"- [{m['fact_id']}] {m['memory']} ({m['how_you_know']})" for m in memories)
                  + f"\n\nThe player asks: {question}")
        try:
            raw = ask(REMEMBER_SYSTEM, prompt, _remember_schema(ids))
            used = [i for i in raw.get("fact_ids", []) if i in ids]
            if not raw.get("matched") or not used:
                return "unknown", {"reason": "Your character doesn't remember anything about that. No token used."}
            if raw.get("answer", "").strip():
                return "answered", {"answer": raw["answer"].strip()[:700], "fact_ids": used, "via": "ai"}
        except ai.AIError:
            pass
    # Backup: authored wording
    hits = keyword_match(question, memories)
    if not hits:
        return "unknown", {"reason": "Your character doesn't remember anything about that. No token used."}
    lines = [m["memory"] if m["how_you_know"].startswith("you saw") else f"{m['memory']} ({m['how_you_know']}.)"
             for m in hits]
    return "answered", {"answer": "You remember: " + " ".join(lines), "fact_ids": [m["fact_id"] for m in hits],
                        "via": "backup"}


def answer_interrogate(character, asker_character, question, claims, ask=None):
    """Returns ('answered', result) or ('unknown', result)."""
    ask = ask or _ask_claude
    ids = [c["claim_id"] for c in claims]
    by_id = {c["claim_id"]: c for c in claims}
    confession = [c for c in claims if c["delivery"] == "confessing"]
    if claims and (ask is not _ask_claude or ai.is_configured()):
        prompt = (f"You are {character['name']} ({character['tagline']}). "
                  f"{asker_character['name']} is questioning you in front of everyone.\n\nYour lines:\n"
                  + "\n".join(f"- [{c['claim_id']}] ({c['delivery']}) {c['says']}" for c in claims)
                  + f"\n\nThe question: {question}")
        try:
            raw = ask(INTERROGATE_SYSTEM, prompt, _interrogate_schema(ids))
            used = [by_id[i] for i in raw.get("claim_ids", []) if i in by_id]
            spoken = raw.get("spoken", "").strip()
            if confession:
                # Evidence unlocked a confession: it must come out. If the AI didn't use it,
                # fall through to the authored confession below.
                if spoken and any(c["delivery"] == "confessing" for c in used):
                    return "answered", _result(spoken, raw.get("acting_tip"), used, "ai")
            elif not raw.get("matched") or not used:
                return "unknown", {"reason": "They don't know anything about that. No token used. Try another question."}
            elif spoken:
                return "answered", _result(spoken, raw.get("acting_tip"), used, "ai")
        except ai.AIError:
            pass
    # Backup: authored wording (a presented confession always wins)
    hits = confession or keyword_match(question, claims)
    if not hits:
        return "unknown", {"reason": "They don't know anything about that. No token used. Try another question."}
    return "answered", _result(" ".join(c["says"] for c in hits), None, hits, "backup")


def _result(spoken, tip, used, via):
    order = ["confessing", "lying", "evasive", "truthful"]
    delivery = next(d for d in order if any(c["delivery"] == d for c in used))
    return {"spoken": spoken.strip()[:700], "acting_tip": (tip or TIPS[delivery]).strip()[:300],
            "claim_ids": [c["claim_id"] for c in used], "delivery": delivery, "via": via}
