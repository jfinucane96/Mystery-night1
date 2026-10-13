"""AI casting suggestions (Claude). Suggestions only: the host still assigns
every role, and the server drops any suggestion that breaks a guest's limits.

What the AI sees: guests' first names, answers, limits and notes, and each
role's name, tagline, biography and casting needs. Never secrets or the solution.
Needs ANTHROPIC_API_KEY in the environment.
"""

import json
import os

from . import casting
from .texting import first_name

MODEL = "claude-opus-5-5"

SYSTEM = """You help a host cast a murder-mystery party. Guests answered six questions (1 = shy, 4 = all-in) and set hard limits. Suggest roles that each guest will enjoy.

Rules, in order of importance:
1. Never give a guest a role containing content they marked "no".
2. Avoid content a guest marked "unsure" or left unanswered unless it clearly fits; if you do suggest it, say in the reason that the host should check with them.
3. A guest marked as the host knows the solution, so never make them the killer.
4. Each role goes to at most one guest. If there are more guests than roles, some guests get "none".
5. Match demanding (major) roles to guests with high spotlight, acting, bluffing and preparation answers; give low-pressure roles to quieter guests. Respect the notes.

Reasons must be one friendly sentence (under 25 words) that refers to the guest's answers. Write them for the host."""


class AIError(Exception):
    pass


def is_configured():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def build_request(story, assessment, guests):
    """The data sent to the AI, plus the JSON schema its answer must follow."""
    labels = {q["id"]: q["label"] for q in assessment["questions"]}
    limit_labels = {l["id"]: l["prompt"] for l in assessment["limits"]}
    roles = [{
        "role_id": c["id"],
        "name": c["name"],
        "tagline": c["tagline"],
        "about": c["biography"],
        "load": c["casting"]["load"],
        "needs": {labels[q]: n for q, n in c["casting"].get("needs", {}).items()},
        "sensitive_content": [limit_labels[x] for x in c["casting"].get("content", [])],
    } for c in story["characters"]]
    people = [{
        "guest_id": g["id"],
        "first_name": first_name(g["name"]),
        "is_host": bool(g.get("is_host")),
        "finished_questions": bool(g["assessment_done"]),
        "answers": {labels[q]: v for q, v in casting.answers_of(g).items() if q in labels},
        "limits": {limit_labels[k]: v for k, v in casting.limits_of(g).items() if k in limit_labels},
        "note": g.get("limits_note") or "",
    } for g in guests]
    role_ids = [c["id"] for c in story["characters"]] + ["none"]
    guest_ids = [g["id"] for g in guests]
    pick = {"type": "object", "additionalProperties": False, "required": ["role_id", "reason"],
            "properties": {"role_id": {"type": "string", "enum": role_ids}, "reason": {"type": "string"}}}
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["summary", "cast", "top_picks"],
        "properties": {
            "summary": {"type": "string"},
            "cast": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["guest_id", "role_id", "reason"],
                "properties": {"guest_id": {"type": "string", "enum": guest_ids},
                               "role_id": {"type": "string", "enum": role_ids},
                               "reason": {"type": "string"}}}},
            "top_picks": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["guest_id", "picks"],
                "properties": {"guest_id": {"type": "string", "enum": guest_ids},
                               "picks": {"type": "array", "items": pick}}}},
        },
    }
    prompt = ("Roles:\n" + json.dumps(roles, indent=1) + "\n\nGuests:\n" + json.dumps(people, indent=1) +
              "\n\nReturn `cast`: one entry per guest, giving the best overall casting for the whole party. "
              "Return `top_picks`: for each guest, their two best-suited roles regardless of the others. "
              "`summary`: one or two sentences on the overall plan.")
    return prompt, schema


def ask_claude(prompt, schema):
    """One request to Claude. Returns the parsed JSON answer."""
    import anthropic
    client = anthropic.Anthropic(timeout=120.0, max_retries=2)
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        raise AIError("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY.")
    except anthropic.PermissionDeniedError:
        raise AIError("The Anthropic API key isn't allowed to do this. Check your account and credit.")
    except anthropic.RateLimitError:
        raise AIError("The AI is busy right now. Try again in a minute.")
    except anthropic.BadRequestError as e:
        msg = str(getattr(e, "message", e))
        if "credit" in msg.lower():
            raise AIError("Your Anthropic account is out of credit. Add some at console.anthropic.com.")
        raise AIError(f"The AI request was rejected: {msg}")
    except anthropic.APIStatusError as e:
        raise AIError(f"The AI service had a problem ({e.status_code}). Try again shortly.")
    except anthropic.APIConnectionError:
        raise AIError("Couldn't reach the AI service. Try again shortly.")
    if response.stop_reason == "refusal":
        raise AIError("The AI declined to make a suggestion. Try again, or cast manually.")
    if response.stop_reason == "max_tokens":
        raise AIError("The AI's answer was cut off. Try again.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        raise AIError("The AI's answer couldn't be read. Try again.")


def clean(story, assessment, guests, raw):
    """Keep only suggestions the rules allow; drop anything invalid or duplicated."""
    by_guest = {g["id"]: g for g in guests}
    chars = story["by_id"]

    def allowed(g, role_id):
        return role_id in chars and not casting.fit(assessment, g, chars[role_id])["blocked"]

    cast, used = [], set()
    for row in raw.get("cast", []):
        g = by_guest.get(row.get("guest_id"))
        if not g or any(c["guest_id"] == g["id"] for c in cast):
            continue
        role = row.get("role_id")
        if role != "none" and (not allowed(g, role) or role in used):
            continue
        used.add(role)
        cast.append({"guest_id": g["id"], "role_id": role, "reason": str(row.get("reason", ""))[:300]})
    top = {}
    for row in raw.get("top_picks", []):
        g = by_guest.get(row.get("guest_id"))
        if not g or g["id"] in top:
            continue
        top[g["id"]] = [{"role_id": p["role_id"], "reason": str(p.get("reason", ""))[:300]}
                        for p in row.get("picks", [])
                        if p.get("role_id") != "none" and allowed(g, p.get("role_id"))][:2]
    return {"summary": str(raw.get("summary", ""))[:600], "cast": cast, "top_picks": top}


def suggest(story, assessment, guests, ask=None):
    prompt, schema = build_request(story, assessment, guests)
    raw = (ask or ask_claude)(prompt, schema)
    return clean(story, assessment, guests, raw)
