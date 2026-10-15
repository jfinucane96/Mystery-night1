"""The web server. Start it with:  py -m server.app   (see README.md)

Pages:   /play/<guest link code>   a guest's phone screen
         /host/<host link code>    the host's control screen
API:     /api/...  The page sends its link code in the X-Key header; the
         server decides what that code is allowed to see or do.
"""

import json
import os
import re
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ai_casting, ai_questions, casting, db as dbmod, game, play, questions, story as storymod, texting
from .game import GameError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
STATIC = {
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/style.css": ("style.css", "text/css; charset=utf-8"),
    "/static/mansion.svg": ("mansion.svg", "image/svg+xml"),
}
PAGE_PATTERN = re.compile(r"^/(play|host|room)/[A-Za-z0-9_-]{20,100}$")
MAX_BODY = 16 * 1024

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",          # private links never leak to other sites
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
}

ROUTES = []


def route(method, pattern, who, own_tx=False):
    """Register an API handler. `who` is 'guest' or 'host': checked before the handler runs.
    Normally the handler runs inside one database transaction. `own_tx` handlers instead
    open their own short transactions, so slow outside calls (AI, texting) never hold
    up other phones while they wait."""
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), who, own_tx, fn))
        return fn
    return deco


# ---------------------------------------------------------------- guest API

def guest_state(ctx, t, guest):
    released = game.is_released(t)
    view = storymod.guest_view(ctx.story, guest, released, casting.prep_level(guest))
    s = play.state(t)
    view["game"] = {"phase": s["phase"], "phase_name": play.PHASE_NAMES[s["phase"]],
                    "round": s["round"], "paused": s["paused"]}
    if released:
        view["stage"] = "character"
        view["tokens"] = play.balance(t, guest["id"])
        msgs = play.messages_for(t, guest["id"])
        view["messages"] = [{"id": m["id"], "body": m["body"], "at": m["created_at"], "read": m["read_at"] is not None}
                            for m in msgs]
        if s["phase"] in ("investigation", "accusation", "reveal"):
            view["murder"] = ctx.story["murder_announcement"]
        view["investigation"] = investigation_view(ctx, t, guest, s)
        return view
    a = ctx.assessment
    view["stage"] = "waiting" if guest["assessment_done"] else "assessment"
    view["assessment"] = {
        "intro": a["intro"],
        "questions": [{"id": q["id"], "prompt": q["prompt"], "options": q["options"]} for q in a["questions"]],
        "limits": a["limits"],
        "answers": casting.answers_of(guest),
        "chosen_limits": casting.limits_of(guest),
        "note": guest["limits_note"],
    }
    return view


def investigation_view(ctx, t, guest, s):
    """What a guest needs to ask questions: who they can question, their evidence,
    their notebook, and any interrogation aimed at them."""
    others = []
    for g in game.list_guests(t):
        c = ctx.story["by_id"].get(g["character_id"] or "")
        if c and g["id"] != guest["id"]:
            others.append({"guest_id": g["id"], "label": f"{c['name']} ({texting.first_name(g['name'])})"})
    evidence = questions.evidence_list(ctx.story)
    notebook, aimed = questions.guest_questions(t, ctx.story, guest)
    return {
        "open": s["phase"] == "investigation" and not s["paused"],
        "suggestions": ctx.story.get("suggested_questions", {}),
        "targets": others,
        "evidence": [{"id": e, "name": evidence[e]["name"], "description": evidence[e].get("description", "")}
                     for e in questions.evidence_of(t, guest["id"]) if e in evidence],
        "notebook": notebook,
        "questioned": aimed,
    }


@route("GET", r"/api/me", "guest")
def guest_me(ctx, t, body, guest):
    return guest_state(ctx, t, guest)


@route("POST", r"/api/me/ask", "guest", own_tx=True)
def guest_ask(ctx, _t, body, guest):
    """Ask a question: take the token, get the answer outside the lock, then keep or refund it."""
    with ctx.db.tx() as t:
        questions.refund_stale(t)
        q, new = questions.reserve(t, ctx.story, body.get("request_id"), guest, body.get("kind"),
                                   body.get("question"), body.get("target_guest_id") or "", body.get("evidence_id") or "")
        info = questions.context_for(t, ctx.story, q) if new else None
    if new:
        status, result = "failed", {"reason": "Something went wrong. Your token was returned."}
        try:
            if q["kind"] == "remember":
                status, result = ai_questions.answer_remember(info["character"], q["question"], info["items"], ctx.ai_ask_q)
            else:
                status, result = ai_questions.answer_interrogate(info["character"], info["asker_character"],
                                                                 q["question"], info["items"], ctx.ai_ask_q)
                if status == "unknown":
                    result = {"reason": f"{info['target_label']} has nothing to say about that. Nothing was sent "
                                        "to them, and no token was used. Try another question."}
        finally:
            with ctx.db.tx() as t:
                questions.settle(t, q["id"], status, result)
    with ctx.db.tx() as t:
        state = guest_state(ctx, t, game.guest_for_id(t, guest["id"]))
    state["asked"] = q["id"]
    return state


@route("POST", r"/api/me/interrogations/(?P<qid>[A-Za-z0-9_-]{8,64})/done", "guest")
def guest_interrogation_done(ctx, t, body, guest, qid):
    questions.mark_target_done(t, guest["id"], qid)
    return guest_state(ctx, t, guest)


@route("POST", r"/api/me/messages/read", "guest")
def guest_read_messages(ctx, t, body, guest):
    play.mark_read(t, guest["id"])
    return guest_state(ctx, t, guest)


@route("POST", r"/api/me/assessment", "guest")
def guest_save_assessment(ctx, t, body, guest):
    game.save_assessment(t, ctx.assessment, guest, body.get("answers"), body.get("limits"),
                         body.get("note"), bool(body.get("submit")))
    return guest_state(ctx, t, game.guest_for_token(t, guest["token"]))


# ----------------------------------------------------------------- host API

def guest_link(ctx, g):
    return f"{ctx.base_url}/play/{g['token']}"


def host_state(ctx, t):
    names = ctx.story["by_id"]
    a = ctx.assessment
    title = ctx.story["title"]
    texts = game.latest_texts(t)
    balances = play.all_balances(t)
    messages = play.all_messages(t)

    def guest_row(g):
        link = guest_link(ctx, g)
        return {
            "id": g["id"],
            "name": g["name"],
            "token": g["token"],
            "character_id": g["character_id"],
            "character_name": names[g["character_id"]]["name"] if g["character_id"] in names else None,
            "assessment_done": bool(g["assessment_done"]),
            "answers": casting.answers_of(g),
            "limits": casting.limits_of(g),
            "note": g["limits_note"],
            "phone": g["phone"],
            "sms_consent": bool(g["sms_consent"]),
            "is_host": bool(g["is_host"]),
            "texts": texts.get(g["id"], {}),
            "prep_level": casting.prep_level(g),
            # pre-written texts, for sending from the host's own phone
            "messages": {"invite": texting.invite_text(title, g["name"], link),
                         "release": texting.release_text(title, g["name"], link)},
            # how every character fits this guest: drives the dropdown and warnings
            "fit": {c["id"]: casting.fit(a, g, c) for c in ctx.story["characters"]},
            "tokens": balances.get(g["id"], {"public": 0, "private": 0}),
            "private_messages": messages.get(g["id"], []),
            "directions": [{**d, "sent": any(m["direction_id"] == d["id"] for m in messages.get(g["id"], []))}
                           for d in play.directions_for(ctx.story, g["character_id"])],
            "message_alert": texting.message_alert_text(title, g["name"], link),
            "evidence": questions.evidence_of(t, g["id"]),
        }

    return {
        "title": title,
        "game": {**play.state(t), "phases": [{"id": p, "name": play.PHASE_NAMES[p]} for p in play.PHASES],
                 **play.config(ctx.story)},
        "placeholder": ctx.story.get("placeholder", False),
        "released": game.is_released(t),
        "texting_automatic": texting.is_automatic(),
        "ai_configured": ai_casting.is_configured(),
        "suggestion": game.get_suggestion(t),
        "questions": [{"id": q["id"], "label": q["label"], "options": q["options"]} for q in a["questions"]],
        "limit_labels": {l["id"]: l["prompt"] for l in a["limits"]},
        "characters": storymod.character_list(ctx.story),
        "solution": storymod.host_solution(ctx.story),
        "room_key": game.room_key(t),
        "evidence": [{"id": e["id"], "name": e["name"]} for e in ctx.story.get("evidence", [])],
        "questions": questions.host_log(t, ctx.story),
        "guests": [guest_row(g) for g in game.list_guests(t)],
    }


@route("GET", r"/api/host", "host")
def host_get(ctx, t, body, _):
    return host_state(ctx, t)


@route("POST", r"/api/host/guests", "host")
def host_add_guest(ctx, t, body, _):
    game.add_guest(t, body.get("name"))
    return host_state(ctx, t)


@route("POST", r"/api/host/guests/(?P<gid>g[0-9a-f]{8})/assign", "host")
def host_assign(ctx, t, body, _, gid):
    game.assign_character(t, ctx.story, ctx.assessment, gid, body.get("character_id"),
                          confirm=bool(body.get("confirm")))
    return host_state(ctx, t)


def fresh_host_state(ctx, extra=None):
    with ctx.db.tx() as t:
        state = host_state(ctx, t)
    state.update(extra or {})
    return state


@route("POST", r"/api/host/guests/(?P<gid>g[0-9a-f]{8})/contact", "host")
def host_contact(ctx, t, body, _, gid):
    game.update_contact(t, gid, body.get("phone"), bool(body.get("consent")), bool(body.get("is_host")))
    return host_state(ctx, t)


def send_texts(ctx, jobs):
    """jobs: [(text_id, phone, body)]. Sends outside any transaction, then records results."""
    results = []
    for text_id, phone, body in jobs:
        try:
            ctx.send_text(phone, body)
            results.append((text_id, True, ""))
        except texting.TextError as e:
            results.append((text_id, False, str(e)))
    with ctx.db.tx() as t:
        for text_id, ok, detail in results:
            game.finish_text(t, text_id, ok, detail)
    return sum(1 for r in results if r[1]), [r[2] for r in results if not r[1]]


@route("POST", r"/api/host/guests/(?P<gid>g[0-9a-f]{8})/text-invite", "host", own_tx=True)
def host_text_invite(ctx, _t, body, _, gid):
    if not texting.is_automatic():
        raise GameError(409, "Automatic texting isn't set up yet. Use 'Text from my phone' instead.")
    with ctx.db.tx() as t:
        g = game.guest_for_id(t, gid)
        if not game.textable(g):
            raise GameError(400, f"Add {g['name']}'s phone number and tick that they agreed to texts first.")
        text_id = game.start_text(t, gid, "invite")
        if not text_id:
            raise GameError(409, "That invite is already being sent.")
        job = (text_id, g["phone"], texting.invite_text(ctx.story["title"], g["name"], guest_link(ctx, g)))
    sent, errors = send_texts(ctx, [job])
    return fresh_host_state(ctx, {"notice": f"Invite texted to {g['name']}." if sent else None,
                                  "error_detail": errors[0] if errors else None})


@route("POST", r"/api/host/release", "host", own_tx=True)
def host_release(ctx, _t, body, _):
    """Release characters, then text everyone who has a character (once per release)."""
    with ctx.db.tx() as t:
        game.release(t, ctx.story, ctx.assessment)
        round_ = game.release_round(t)
        jobs = []
        if texting.is_automatic():
            for g in game.list_guests(t):
                if g["character_id"] and game.textable(g):
                    text_id = game.start_text(t, g["id"], "release", round_,
                                              text_id=f"release-{g['id']}-{round_}")
                    if text_id:
                        jobs.append((text_id, g["phone"],
                                     texting.release_text(ctx.story["title"], g["name"], guest_link(ctx, g))))
    sent, errors = send_texts(ctx, jobs) if jobs else (0, [])
    notice = "Characters released."
    if jobs:
        notice += f" Texted {sent} guest{'' if sent == 1 else 's'}."
    return fresh_host_state(ctx, {
        "notice": notice,
        "error_detail": f"{len(errors)} text(s) failed. {errors[0]}" if errors else None,
        "just_released": True,
    })


_ai_busy = threading.Lock()


@route("POST", r"/api/host/suggest", "host", own_tx=True)
def host_suggest(ctx, _t, body, _):
    if not (ai_casting.is_configured() or ctx.ai_ask):
        raise GameError(409, "AI suggestions need an Anthropic API key (ANTHROPIC_API_KEY). See README.")
    if not _ai_busy.acquire(blocking=False):
        raise GameError(409, "Already working on a suggestion. Give it a moment.")
    try:
        with ctx.db.tx() as t:
            guests = game.list_guests(t)
        if not guests:
            raise GameError(400, "Add some guests first.")
        try:
            plan = ai_casting.suggest(ctx.story, ctx.assessment, guests, ask=ctx.ai_ask)
        except ai_casting.AIError as e:
            raise GameError(502, str(e))
        with ctx.db.tx() as t:
            game.save_suggestion(t, plan)
    finally:
        _ai_busy.release()
    return fresh_host_state(ctx)


# ---------------------------------------------------- P03: running the party

@route("POST", r"/api/host/phase", "host")
def host_phase(ctx, t, body, _):
    play.set_phase(t, ctx.story, body.get("phase"))
    return host_state(ctx, t)


@route("POST", r"/api/host/round", "host")
def host_round(ctx, t, body, _):
    number = body.get("number")
    if not isinstance(number, int):
        raise GameError(400, "Which round?")
    play.start_round(t, ctx.story, number)
    return host_state(ctx, t)


@route("POST", r"/api/host/pause", "host")
def host_pause(ctx, t, body, _):
    play.set_paused(t, bool(body.get("paused")))
    return host_state(ctx, t)


@route("POST", r"/api/host/grant", "host")
def host_grant(ctx, t, body, _):
    target = body.get("target")
    if target != "all" and not (isinstance(target, str) and re.match(r"^g[0-9a-f]{8}$", target)):
        raise GameError(400, "Grant to whom?")
    new = play.host_grant(t, body.get("request_id"), target, body.get("public", 0), body.get("private", 0))
    state = host_state(ctx, t)
    state["notice"] = "Tokens granted." if new else "Already granted (that tap was a repeat)."
    return state


@route("POST", r"/api/host/guests/(?P<gid>g[0-9a-f]{8})/message", "host", own_tx=True)
def host_message(ctx, _t, body, _, gid):
    """Send a private message; optionally text the guest a heads-up (never the message itself)."""
    with ctx.db.tx() as t:
        msg, new = play.send_message(t, ctx.story, body.get("request_id"), gid,
                                     body.get("body"), body.get("direction_id"))
        g = game.guest_for_id(t, gid)
        job = None
        if new and body.get("also_text") and texting.is_automatic() and game.textable(g):
            text_id = game.start_text(t, gid, "message", text_id=f"message-{msg['id']}")
            if text_id:
                job = (text_id, g["phone"], texting.message_alert_text(ctx.story["title"], g["name"], guest_link(ctx, g)))
    notice = f"Message sent to {g['name']}." if new else "Already sent (that tap was a repeat)."
    extra = {"notice": notice}
    if job:
        sent, errors = send_texts(ctx, [job])
        if sent:
            extra["notice"] += " They've been texted a heads-up."
        else:
            extra["error_detail"] = errors[0]
    elif new and body.get("also_text") and g["phone"]:
        extra["text_prompt"] = {"guest_id": gid}    # host page offers "Text from my phone"
    return fresh_host_state(ctx, extra)


@route("POST", r"/api/host/guests/(?P<gid>g[0-9a-f]{8})/evidence", "host")
def host_evidence(ctx, t, body, _, gid):
    if body.get("give", True):
        questions.give_evidence(t, ctx.story, gid, body.get("evidence_id"))
    else:
        questions.take_evidence(t, gid, body.get("evidence_id"))
    return host_state(ctx, t)


@route("POST", r"/api/host/room/new-link", "host")
def host_new_room_link(ctx, t, body, _):
    game.reset_room_key(t)
    return host_state(ctx, t)


# ------------------------------------------------- the room screen (watch only)

@route("GET", r"/api/room", "room")
def room_get(ctx, t, body, _):
    s = play.state(t)
    return {
        "title": ctx.story["title"],
        "phase": s["phase"], "phase_name": play.PHASE_NAMES[s["phase"]],
        "round": s["round"], "rounds": play.config(ctx.story)["rounds"], "paused": s["paused"],
        "murder": ctx.story["murder_announcement"],
        "events": questions.room_events(t, ctx.story),
    }


@route("POST", r"/api/host/suggest/apply", "host")
def host_apply_suggestion(ctx, t, body, _):
    game.apply_suggestion(t, ctx.story, ctx.assessment, confirm=bool(body.get("confirm")))
    return host_state(ctx, t)


@route("POST", r"/api/host/unrelease", "host")
def host_unrelease(ctx, t, body, _):
    game.unrelease(t)
    return host_state(ctx, t)


@route("POST", r"/api/host/guests/(?P<gid>g[0-9a-f]{8})/new-link", "host")
def host_new_link(ctx, t, body, _, gid):
    game.new_guest_link(t, gid)
    return host_state(ctx, t)


@route("POST", r"/api/host/guests/(?P<gid>g[0-9a-f]{8})/remove", "host")
def host_remove(ctx, t, body, _, gid):
    game.remove_guest(t, gid)
    return host_state(ctx, t)


# ------------------------------------------------------------- HTTP plumbing

class Context:
    def __init__(self, database, story, assessment, ai_ask=None, send_text=None, ai_ask_q=None):
        self.db = database
        self.story = story
        self.assessment = assessment
        self.base_url = None                         # start of guest links; set in make_server
        self.ai_ask = ai_ask                         # tests swap in a fake AI here
        self.send_text = send_text or texting.send   # ...and a fake texting service here
        self.ai_ask_q = ai_ask_q                     # ...and a fake AI for questions


class Handler(BaseHTTPRequestHandler):
    server_version = "MysteryNight"
    sys_version = ""
    ctx = None  # set by make_server

    def log_message(self, fmt, *args):
        # Log the method and path, but never the link code inside page URLs.
        msg = fmt % args
        msg = re.sub(r"/(play|host)/[A-Za-z0-9_-]+", r"/\1/<hidden>", msg)
        sys.stderr.write(f"{self.log_date_time_string()} {msg}\n")

    def _send(self, status, body, ctype):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload), "application/json; charset=utf-8")

    def _file(self, name, ctype):
        with open(os.path.join(WEB, name), "rb") as f:
            self._send(200, f.read(), ctype)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/"):
            return self._api("GET", path)
        if path == "/healthz":
            return self._send(200, "ok", "text/plain")
        if path == "/":
            return self._file("home.html", "text/html; charset=utf-8")
        if PAGE_PATTERN.match(path):
            return self._file("index.html", "text/html; charset=utf-8")
        if path in STATIC:
            return self._file(*STATIC[path])
        self._send(404, "Not found", "text/plain")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/"):
            return self._send(404, "Not found", "text/plain")
        self._api("POST", path)

    def _api(self, method, path):
        try:
            for m, pattern, who, own_tx, fn in ROUTES:
                match = pattern.match(path)
                if m == method and match:
                    break
            else:
                return self._json(404, {"error": "Not found."})
            body = self._read_body() if method == "POST" else {}
            key = self.headers.get("X-Key", "")
            with self.ctx.db.tx() as t:
                if who == "host":
                    if not game.is_host(t, key):
                        return self._json(403, {"error": "This action needs the host link."})
                    actor = None
                elif who == "room":
                    if not game.is_room(t, key):
                        return self._json(403, {"error": "This room link isn't valid."})
                    actor = None
                else:
                    actor = game.guest_for_token(t, key)
                    if not actor:
                        return self._json(404, {"error": "invalid_link"})
                if not own_tx:
                    result = fn(self.ctx, t, body, actor, **match.groupdict())
            if own_tx:
                result = fn(self.ctx, None, body, actor, **match.groupdict())
            self._json(200, result)
        except GameError as e:
            self._json(e.status, {"error": e.message, "code": e.code})
        except Exception:
            traceback.print_exc()
            self._json(500, {"error": "Something went wrong on the server. Please try again."})

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise GameError(413, "Request too large.")
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise GameError(415, "Expected JSON.")
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise GameError(400, "Invalid JSON.")
        if not isinstance(data, dict):
            raise GameError(400, "Invalid JSON.")
        return data


def load_content():
    """Load the story and assessment, refusing to start if either has mistakes."""
    story = storymod.load()
    assessment = casting.load()
    problems = casting.check_story(story, assessment)
    if problems:
        raise storymod.StoryError("Casting info in content/story.json has problems:\n  - "
                                  + "\n  - ".join(problems))
    return story, assessment


def make_server(database, story, assessment, host, port, ai_ask=None, send_text=None, ai_ask_q=None):
    database.init_schema()
    with database.tx() as t:
        host_key = game.ensure_game(t)
    ctx = Context(database, story, assessment, ai_ask, send_text, ai_ask_q)
    server = ThreadingHTTPServer((host, port), type("BoundHandler", (Handler,), {"ctx": ctx}))
    ctx.base_url = public_base_url(server.server_address[1])
    return server, host_key


def public_base_url(port):
    return (os.environ.get("RENDER_EXTERNAL_URL") or f"http://localhost:{port}").rstrip("/")


def load_local_keys():
    """On your own computer, read API keys from .env. DATABASE_URL is deliberately
    skipped so local testing never touches the online game."""
    path = os.path.join(ROOT, ".env")
    if os.environ.get("RENDER") or not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = (x.strip() for x in line.split("=", 1))
                if key != "DATABASE_URL":
                    os.environ.setdefault(key, value)


def main():
    load_local_keys()
    on_host_service = "PORT" in os.environ          # Render and similar set PORT
    port = int(os.environ.get("PORT") or os.environ.get("MM_PORT") or 8000)
    bind = "0.0.0.0" if on_host_service else os.environ.get("MM_HOST", "127.0.0.1")
    try:
        story, assessment = load_content()
    except (storymod.StoryError, casting.AssessmentError) as e:
        sys.exit(str(e))
    database = dbmod.from_env()
    server, host_key = make_server(database, story, assessment, bind, port)
    print(f"Mystery Night: '{story['title']}'", flush=True)
    print(f"Database: {database.describe()}", flush=True)
    print("Texting: " + ("automatic (Twilio)" if texting.is_automatic()
                         else "from the host's phone (Twilio not set up)"), flush=True)
    print("AI casting: " + ("on" if ai_casting.is_configured() else "off (no ANTHROPIC_API_KEY)"), flush=True)
    print(f"HOST LINK (keep private): {public_base_url(port)}/host/{host_key}", flush=True)
    print(f"Listening on {bind}:{port}. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
