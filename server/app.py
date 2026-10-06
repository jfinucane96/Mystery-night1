"""HTTP entry point: python -m server.app  (see README.md)."""

import json
import os
import re
import sys
import traceback
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import auth, content, db, store
from .store import ApiError

WEB_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
# Only these files are ever served from disk. Content lives outside WEB_ROOT.
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8")}
MAX_BODY = 64 * 1024
CSRF_HEADER = "X-Requested-With"
SECURE_COOKIES = os.environ.get("MM_SECURE_COOKIES") == "1"

ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


# ------------------------------------------------------------------ routes
# Handlers receive (req, conn, user, body, *path_params) and return (status, payload).

@route("POST", r"/api/auth/register")
def _register(req, conn, user, body):
    token = store.register(conn, body.get("email"), body.get("password"), body.get("display_name"))
    req.set_session(token)
    return 201, {"user": auth.user_for_token(conn, token)}


@route("POST", r"/api/auth/login")
def _login(req, conn, user, body):
    token = store.login(conn, body.get("email"), body.get("password"))
    req.set_session(token)
    return 200, {"user": auth.user_for_token(conn, token)}


@route("POST", r"/api/auth/logout")
def _logout(req, conn, user, body):
    auth.delete_session(conn, req.session_token())
    req.set_session("", clear=True)
    return 200, {"ok": True}


@route("GET", r"/api/auth/me")
def _me(req, conn, user, body):
    return 200, {"user": user}


@route("GET", r"/api/packages")
def _packages(req, conn, user, body):
    store._require_user(user)
    return 200, {"packages": store.list_packages()}


@route("GET", r"/api/games")
def _games(req, conn, user, body):
    return 200, store.list_games(conn, user)


@route("POST", r"/api/games")
def _create_game(req, conn, user, body):
    return 201, store.create_game(conn, user, body.get("title"), body.get("package_id"), body.get("package_version"))


@route("GET", r"/api/games/([\w-]+)")
def _game(req, conn, user, body, gid):
    return 200, store.game_view(conn, user, gid)


@route("POST", r"/api/games/([\w-]+)/invitations")
def _invite(req, conn, user, body, gid):
    return 201, store.create_invitation(conn, user, gid, body.get("label"))


@route("POST", r"/api/games/([\w-]+)/invitations/([\w-]+)/revoke")
def _revoke(req, conn, user, body, gid, iid):
    return 200, store.revoke_invitation(conn, user, gid, iid)


@route("GET", r"/api/invitations/([\w-]+)")
def _preview_invite(req, conn, user, body, token):
    return 200, store.preview_invitation(conn, user, token)


@route("POST", r"/api/invitations/([\w-]+)/respond")
def _respond(req, conn, user, body, token):
    return 200, store.respond_invitation(conn, user, token, body.get("response"))


@route("GET", r"/api/games/([\w-]+)/assessment")
def _assessment(req, conn, user, body, gid):
    return 200, store.get_assessment(conn, user, gid)


@route("PUT", r"/api/games/([\w-]+)/assessment/answers/([\w-]+)")
def _answer(req, conn, user, body, gid, qid):
    return 200, store.save_answer(conn, user, gid, qid, body.get("option_id"))


@route("PUT", r"/api/games/([\w-]+)/assessment/preferences")
def _prefs(req, conn, user, body, gid):
    return 200, store.save_preferences(conn, user, gid, body)


@route("POST", r"/api/games/([\w-]+)/assessment/submit")
def _submit(req, conn, user, body, gid):
    return 200, store.submit_assessment(conn, user, gid)


@route("GET", r"/api/games/([\w-]+)/casting")
def _casting(req, conn, user, body, gid):
    return 200, store.get_casting(conn, user, gid)


@route("POST", r"/api/games/([\w-]+)/casting/recommend")
def _recommend(req, conn, user, body, gid):
    return 200, store.recommend_cast(conn, user, gid, force=body.get("force") is True)


@route("PUT", r"/api/games/([\w-]+)/casting/assignments/([\w-]+)")
def _override(req, conn, user, body, gid, pid):
    return 200, store.override_assignment(conn, user, gid, pid, body.get("role_id"), force=body.get("force") is True)


@route("POST", r"/api/games/([\w-]+)/casting/approve")
def _approve(req, conn, user, body, gid):
    return 200, store.approve_cast(conn, user, gid)


@route("GET", r"/api/games/([\w-]+)/me/character")
def _my_character(req, conn, user, body, gid):
    return 200, store.my_character(conn, user, gid)


@route("POST", r"/api/games/([\w-]+)/me/acknowledge")
def _ack(req, conn, user, body, gid):
    return 200, store.acknowledge_briefing(conn, user, gid)


@route("GET", r"/api/games/([\w-]+)/cast")
def _cast(req, conn, user, body, gid):
    return 200, store.cast_list(conn, user, gid)


@route("GET", r"/api/games/([\w-]+)/characters/([\w-]+)")
def _character(req, conn, user, body, gid, rid):
    return 200, store.character(conn, user, gid, rid)


@route("GET", r"/api/games/([\w-]+)/solution")
def _solution(req, conn, user, body, gid):
    return 200, store.solution(conn, user, gid)


# ------------------------------------------------------------------ handler

class Handler(BaseHTTPRequestHandler):
    server_version = "MysteryEngine/0.1"
    sys_version = ""

    def log_message(self, fmt, *args):
        # Strip query strings from logs; invite tokens travel in the URL fragment anyway.
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def _headers_common(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for c in getattr(self, "_set_cookies", []):
            self.send_header("Set-Cookie", c)

    def session_token(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return jar[auth.COOKIE_NAME].value if auth.COOKIE_NAME in jar else None

    def set_session(self, token, clear=False):
        attrs = f"{auth.COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax"
        attrs += "; Max-Age=0" if clear else f"; Max-Age={auth.SESSION_DAYS * 86400}"
        if SECURE_COOKIES:
            attrs += "; Secure"
        self._set_cookies = getattr(self, "_set_cookies", []) + [attrs]

    def _json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._headers_common()
        self.end_headers()
        self.wfile.write(data)

    def _static(self, path):
        name, ctype = STATIC[path]
        with open(os.path.join(WEB_ROOT, name), "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._headers_common()
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, method):
        path = self.path.split("?", 1)[0]
        if method == "GET" and path in STATIC:
            return self._static(path)
        if not path.startswith("/api/"):
            return self._json(404, {"error": "Not found."})
        for m, rx, fn in ROUTES:
            match = rx.match(path)
            if match and m == method:
                break
        else:
            return self._json(404 if not any(rx.match(path) for _, rx, _ in ROUTES) else 405,
                              {"error": "Not found."})
        body = {}
        if method != "GET":
            # CSRF: browsers can't send this header cross-site without a CORS preflight,
            # which this server never approves. SameSite=Lax cookies add a second layer.
            if self.headers.get(CSRF_HEADER) != "mystery" or \
                    not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._json(403, {"error": "Missing request headers."})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json(413, {"error": "Request too large."})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json(400, {"error": "Invalid JSON."})
        try:
            conn = db.connect()
        except Exception:
            traceback.print_exc()
            return self._json(503, {"error": "The game database is unreachable. Try again shortly."})
        try:
            user = auth.user_for_token(conn, self.session_token())
            status, payload = fn(self, conn, user, body, *match.groups())
            self._json(status, payload)
        except ApiError as e:
            self._json(e.status, {"error": e.message, **e.extra})
        except Exception:
            traceback.print_exc()
            self._json(500, {"error": "Something went wrong on the server."})
        finally:
            conn.close()

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        # Health checks and link previews: headers only, no body, no API access.
        ok = self.path.split("?", 1)[0] in STATIC
        self.send_response(200 if ok else 404)
        self.send_header("Content-Length", "0")
        self._headers_common()
        self.end_headers()

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")


def load_dotenv(path=os.path.join(os.path.dirname(WEB_ROOT), ".env")):
    """Minimal .env reader (KEY=VALUE lines). Real environment variables win,
    so hosts and tests can override anything in the file."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    load_dotenv()
    global SECURE_COOKIES
    SECURE_COOKIES = os.environ.get("MM_SECURE_COOKIES") == "1"
    cloud = "PORT" in os.environ                 # Render/Railway set PORT and expect 0.0.0.0
    host = os.environ.get("MM_HOST", "0.0.0.0" if cloud else "127.0.0.1")
    port = int(os.environ.get("PORT") or os.environ.get("MM_PORT") or "8000")
    content.load_assessment()
    for p in content.list_packages():          # fail fast on invalid content
        print(f"content ok: {p['package_id']}@{p['content_version']}")
    db.init()
    print(f"database: {db.backend_name()}", flush=True)
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print(f"Mystery engine listening on http://{'localhost' if host == '127.0.0.1' else host}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
