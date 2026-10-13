"""One place that talks to Claude, shared by every AI feature. Needs
ANTHROPIC_API_KEY in the environment (Render settings, or .env locally)."""

import json
import os

MODEL = "claude-opus-5-5"


class AIError(Exception):
    """A failure with a message that is safe to show the host."""


def is_configured():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def ask_json(system, prompt, schema, effort="medium"):
    """One request whose answer must match `schema`. Returns the parsed JSON."""
    import anthropic
    client = anthropic.Anthropic(timeout=120.0, max_retries=2)
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
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
        raise AIError("The AI declined this request. Try again, or carry on without it.")
    if response.stop_reason == "max_tokens":
        raise AIError("The AI's answer was cut off. Try again.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        raise AIError("The AI's answer couldn't be read. Try again.")
