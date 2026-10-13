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
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db as dbmod, game, story as storymod
from .game import GameError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
STATIC = {
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/style.css": ("style.css", "text/css; charset=utf-8"),
}
PAGE_PATTERN = re.compile(r"^/(play|host)/[A-Za-z0-9_-]{20,100}$")
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


def route(method, pattern, who):
    """Register an API handler. `who` is 'guest' or 'host': checked before the handler runs."""
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), who, fn))
        return fn
    return deco


# ---------------------------------------------------------------- guest API

@route("GET", r"/api/me", "guest")
def guest_me(ctx, t, body, guest):
    return storymod.guest_view(ctx.story, guest)


# ----------------------------------------------------------------- host API

def host_state(ctx, t):
    names = ctx.story["by_id"]
    return {
        "title": ctx.story["title"],
        "placeholder": ctx.story.get("placeholder", False),
        "characters": storymod.character_list(ctx.story),
        "solution": storymod.host_solution(ctx.story),
        "guests": [{
            "id": g["id"],
            "name": g["name"],
            "token": g["token"],
            "character_id": g["character_id"],
            "character_name": names[g["character_id"]]["name"] if g["character_id"] in names else None,
        } for g in game.list_guests(t)],
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
    game.assign_character(t, ctx.story, gid, body.get("character_id"))
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
    def __init__(self, database, story):
        self.db = database
        self.story = story


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
            for m, pattern, who, fn in ROUTES:
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
                else:
                    actor = game.guest_for_token(t, key)
                    if not actor:
                        return self._json(404, {"error": "invalid_link"})
                result = fn(self.ctx, t, body, actor, **match.groupdict())
            self._json(200, result)
        except GameError as e:
            self._json(e.status, {"error": e.message})
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


def make_server(database, story, host, port):
    database.init_schema()
    with database.tx() as t:
        host_key = game.ensure_game(t)
    handler = type("BoundHandler", (Handler,), {"ctx": Context(database, story)})
    return ThreadingHTTPServer((host, port), handler), host_key


def public_base_url(port):
    return (os.environ.get("RENDER_EXTERNAL_URL") or f"http://localhost:{port}").rstrip("/")


def main():
    on_host_service = "PORT" in os.environ          # Render and similar set PORT
    port = int(os.environ.get("PORT") or os.environ.get("MM_PORT") or 8000)
    bind = "0.0.0.0" if on_host_service else os.environ.get("MM_HOST", "127.0.0.1")
    try:
        story = storymod.load()
    except storymod.StoryError as e:
        sys.exit(str(e))
    database = dbmod.from_env()
    server, host_key = make_server(database, story, bind, port)
    print(f"Mystery Night: '{story['title']}'", flush=True)
    print(f"Database: {database.describe()}", flush=True)
    print(f"HOST LINK (keep private): {public_base_url(port)}/host/{host_key}", flush=True)
    print(f"Listening on {bind}:{port}. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
