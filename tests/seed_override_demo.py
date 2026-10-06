"""Seed a local demo game where nobody fits the required killer role, so the
host's willingness grid and override controls can be tried in the browser.

    py tests/seed_override_demo.py        (server must be running on :8000)

Uses the throwaway accounts in tests/demo_accounts.json.
"""

import http.cookiejar
import json
import os
import urllib.error
import urllib.request

BASE = "http://localhost:8000"
HERE = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS = json.load(open(os.path.join(HERE, "demo_accounts.json"), encoding="utf-8"))


class Client:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None):
        req = urllib.request.Request(BASE + path, method=method,
                                     data=json.dumps(body or {}).encode() if method != "GET" else None)
        if method != "GET":
            req.add_header("Content-Type", "application/json")
            req.add_header("X-Requested-With", "mystery")
        try:
            with self.opener.open(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


def sign_in(acct):
    c = Client()
    st, _ = c.call("POST", "/api/auth/login", {"email": acct["email"], "password": acct["password"]})
    if st != 200:
        st, r = c.call("POST", "/api/auth/register", {"email": acct["email"], "password": acct["password"],
                                                      "display_name": acct["display_name"]})
        assert st == 201, r
    return c


def main():
    host = sign_in(ACCOUNTS["host"])
    st, game = host.call("POST", "/api/games", {"title": "Override demo (nobody fits the killer)",
                                                "package_id": "lemon-tart", "package_version": "1.0.0"})
    assert st == 201, game
    gid = game["id"]
    _, assessment = None, None
    for key, killer, level in (("carol", "allowed", "c"), ("dev", "blocked", "b")):
        guest = sign_in(ACCOUNTS[key])
        _, inv = host.call("POST", f"/api/games/{gid}/invitations", {"label": ACCOUNTS[key]["display_name"]})
        st, r = guest.call("POST", f"/api/invitations/{inv['token']}/respond", {"response": "accept"})
        assert st == 200, r
        _, a = guest.call("GET", f"/api/games/{gid}/assessment")
        for q in a["questions"]:
            guest.call("PUT", f"/api/games/{gid}/assessment/answers/{q['id']}", {"option_id": level})
        bounds = {t["id"]: "allowed" for t in a["tags"]}
        bounds["plays_killer"] = killer
        st, r = guest.call("PUT", f"/api/games/{gid}/assessment/preferences",
                           {"boundaries": bounds, "preparation_minutes": 30,
                            "assistance": "some", "stretch_preference": "modest"})
        assert st == 200, r
        assert guest.call("POST", f"/api/games/{gid}/assessment/submit")[0] == 200
    st, cast = host.call("POST", f"/api/games/{gid}/casting/recommend")
    print("problems:", cast.get("recommendation_problems"))
    print(f"open as host: {BASE}/#/game/{gid}")


if __name__ == "__main__":
    main()
