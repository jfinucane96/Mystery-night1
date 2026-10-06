"""End-to-end demonstration (spec §13) against a real server process:
host + two separately authenticated guests, privacy probes, and a restart.

    py -m unittest tests.test_e2e -v
"""

import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server import content  # noqa: E402

PKG = content.load_package("lemon-tart", "1.0.0")
ASSESS = content.load_assessment()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    """One browser-like identity with its own cookie jar."""

    def __init__(self, base, name):
        self.base, self.name = base, name
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None, headers=True):
        data = json.dumps(body or {}).encode() if method != "GET" else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if method != "GET" and headers:
            req.add_header("Content-Type", "application/json")
            req.add_header("X-Requested-With", "mystery")
        try:
            with self.opener.open(req) as r:
                raw = r.read().decode()
                return r.status, (json.loads(raw) if raw.startswith(("{", "[")) else raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            return e.code, (json.loads(raw) if raw.startswith("{") else raw)


def private_strings_for(pkg, role_id):
    r = pkg.roles[role_id]
    out = list(r["private"]["secrets"]) + [r["private"]["backstory"]]
    out += [pkg.knowledge[k]["claim"] for k in r["permitted_memory_ids"]]
    return out


def private_strings(role_id):
    return private_strings_for(PKG, role_id)


SOLUTION_STRINGS = [PKG.solution["motive"], PKG.solution["method"], PKG.solution["summary"]] + \
                   [f["statement"] for f in PKG.timeline]


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mm-e2e-")
        cls.db_path = os.path.join(cls.tmp, "e2e.db")
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.proc = None
        cls.server_env = {"MM_DB": cls.db_path, "MM_PORT": str(cls.port), "DATABASE_URL": ""}
        if os.environ.get("E2E_BACKEND") == "postgres":
            # Same flow against Postgres (e.g. Supabase from .env), in a throwaway schema.
            from server.app import load_dotenv
            load_dotenv()
            if not os.environ.get("DATABASE_URL"):
                raise unittest.SkipTest("E2E_BACKEND=postgres needs DATABASE_URL (in .env)")
            cls.server_env.update(DATABASE_URL=os.environ["DATABASE_URL"], MM_PG_SCHEMA="mystery_test")
            os.environ["MM_PG_SCHEMA"] = "mystery_test"
            from server import db as dbmod
            conn = dbmod.connect()
            conn.raw.execute("DROP SCHEMA IF EXISTS mystery_test CASCADE")
            conn.close()
        else:
            os.environ["DATABASE_URL"] = ""
            os.environ["MM_DB"] = cls.db_path
        cls.start_server()

    @classmethod
    def query(cls, sql):
        from server import db as dbmod
        conn = dbmod.connect()
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    @classmethod
    def start_server(cls):
        env = dict(os.environ, **cls.server_env)
        env.pop("PORT", None)
        cls.proc = subprocess.Popen([sys.executable, "-m", "server.app"], cwd=ROOT, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.base + "/api/auth/me", timeout=1)
                return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    @classmethod
    def stop_server(cls):
        if cls.proc:
            cls.proc.terminate()
            cls.proc.wait(timeout=10)
            cls.proc = None

    @classmethod
    def tearDownClass(cls):
        cls.stop_server()

    def step(self, msg):
        print(f"  ✓ {msg}", flush=True)

    def assertNoLeak(self, payload, forbidden, who):
        text = json.dumps(payload)
        for s in forbidden:
            self.assertNotIn(s, text, f"{who} received forbidden text: {s[:60]}")

    def answer_all(self, client, gid, picks, stop_after=None):
        for i, q in enumerate(ASSESS["questions"]):
            if stop_after is not None and i >= stop_after:
                return
            st, _ = client.call("PUT", f"/api/games/{gid}/assessment/answers/{q['id']}", {"option_id": picks[q["dimension"]]})
            self.assertEqual(st, 200)

    def test_full_casting_flow(self):
        host, alice, bob = (Client(self.base, n) for n in ("host", "alice", "bob"))
        anon = Client(self.base, "anon")
        print()

        # --- accounts and game -------------------------------------------------
        for c, email in ((host, "host@example.test"), (alice, "alice@example.test"), (bob, "bob@example.test")):
            st, r = c.call("POST", "/api/auth/register",
                           {"email": email, "password": "correct-horse-1", "display_name": c.name.title()})
            self.assertEqual(st, 201, r)
        st, r = host.call("POST", "/api/games", {"title": "Saturday Test Party", "package_id": "lemon-tart",
                                                 "package_version": "1.0.0"})
        self.assertEqual(st, 201, r)
        gid = r["id"]
        self.step("host created a game; three separate accounts registered")

        st, _ = host.call("POST", "/api/games", {"title": "x"}, headers=False)
        self.assertEqual(st, 403)
        self.step("state-changing request without CSRF header rejected (403)")

        invites = {}
        for label in ("Alice", "Bob", "Spare"):
            st, r = host.call("POST", f"/api/games/{gid}/invitations", {"label": label})
            self.assertEqual(st, 201)
            invites[label] = r["token"]
        st, r = alice.call("POST", f"/api/games/{gid}/invitations", {"label": "sneaky"})
        self.assertEqual(st, 404)
        st, _ = alice.call("GET", f"/api/games/{gid}")
        self.assertEqual(st, 404)
        self.step("3 invitations created; non-member cannot see game or invite (404)")

        # --- joining -----------------------------------------------------------
        st, r = host.call("POST", f"/api/invitations/{invites['Alice']}/respond", {"response": "accept"})
        self.assertEqual(st, 409)
        st, r = anon.call("POST", f"/api/invitations/{invites['Alice']}/respond", {"response": "accept"})
        self.assertEqual(st, 401)
        st, r = alice.call("GET", f"/api/invitations/{invites['Alice']}")
        self.assertEqual((st, r["game_title"]), (200, "Saturday Test Party"))
        st, r = alice.call("POST", f"/api/invitations/{invites['Alice']}/respond", {"response": "accept"})
        self.assertEqual((st, r["game_id"]), (200, gid))
        st, r = bob.call("POST", f"/api/invitations/{invites['Alice']}/respond", {"response": "accept"})
        self.assertEqual(st, 409)
        st, r = bob.call("POST", f"/api/invitations/{invites['Bob']}/respond", {"response": "accept"})
        self.assertEqual(st, 200)
        st, r = alice.call("POST", f"/api/invitations/not-a-real-token/respond", {"response": "accept"})
        self.assertEqual(st, 404)
        self.step("Alice and Bob joined with their own links; host self-join, reuse and bad tokens rejected")

        # --- assessment, resume after refresh ---------------------------------
        bold = {d: "d" for d in ASSESS["dimensions"]}
        self.answer_all(alice, gid, bold, stop_after=5)
        st, r = alice.call("GET", f"/api/games/{gid}/assessment")   # simulated page refresh
        self.assertEqual(len(r["answers"]), 5)
        self.assertNotIn("dimension", json.dumps(r["questions"]))
        self.assertNotIn("scoring_map", json.dumps(r))
        self.step("assessment resumed after refresh with 5/12 answers saved; no scoring data sent to player")

        st, r = alice.call("POST", f"/api/games/{gid}/assessment/submit")
        self.assertEqual(st, 400)
        self.answer_all(alice, gid, bold)
        tags = [t for t in PKG.content_tags]
        st, r = alice.call("PUT", f"/api/games/{gid}/assessment/preferences",
                           {"boundaries": {t: "allowed" for t in tags}, "preparation_minutes": 60,
                            "assistance": "none", "stretch_preference": "modest"})
        self.assertEqual(st, 200, r)
        st, r = alice.call("POST", f"/api/games/{gid}/assessment/submit")
        self.assertEqual((st, r), (200, {"status": "complete"}))
        self.step("Alice completed questionnaire and boundaries (incomplete submit rejected first)")

        st, r = host.call("POST", f"/api/games/{gid}/casting/recommend")
        self.assertEqual(st, 409)
        self.assertIn("Bob", r["error"])
        self.step("casting blocked while Bob's questionnaire is unfinished")

        quiet = {"spotlight": "a", "performance": "b", "deception": "a", "investigation": "c",
                 "mischief": "b", "commitment": "b"}
        self.answer_all(bob, gid, quiet)
        bob_bounds = {t: "unspecified" for t in tags}
        bob_bounds["plays_killer"] = "blocked"
        st, r = bob.call("PUT", f"/api/games/{gid}/assessment/preferences",
                         {"boundaries": bob_bounds, "preparation_minutes": 20,
                          "assistance": "full", "stretch_preference": "none"})
        self.assertEqual(st, 200, r)
        self.assertEqual(bob.call("POST", f"/api/games/{gid}/assessment/submit")[0], 200)
        self.step("Bob completed questionnaire, blocking 'plays_killer'")

        # --- before approval: nothing released ---------------------------------
        all_private = sum((private_strings(r) for r in PKG.roles), [])
        role_names = [r["name"] for r in PKG.roles.values()]
        for c in (alice, bob):
            self.assertEqual(c.call("GET", f"/api/games/{gid}/me/character")[0], 409)
            self.assertEqual(c.call("GET", f"/api/games/{gid}/cast")[0], 409)
            for rid in PKG.roles:
                self.assertEqual(c.call("GET", f"/api/games/{gid}/characters/{rid}")[0], 403)
            st, view = c.call("GET", f"/api/games/{gid}")
            self.assertNoLeak(view, all_private + role_names + SOLUTION_STRINGS, c.name)
            st, a = c.call("GET", f"/api/games/{gid}/assessment")
            self.assertNoLeak(a, all_private + role_names + SOLUTION_STRINGS, c.name)
        self.step("before approval: no character, cast list or role names visible to either guest")

        # --- host casting ------------------------------------------------------
        for c in (alice, bob):
            self.assertEqual(c.call("GET", f"/api/games/{gid}/casting")[0], 403)
            self.assertEqual(c.call("POST", f"/api/games/{gid}/casting/recommend")[0], 403)
            self.assertEqual(c.call("POST", f"/api/games/{gid}/casting/approve")[0], 403)
        self.step("guests cannot read or run casting (403)")

        st, cast = host.call("POST", f"/api/games/{gid}/casting/recommend")
        self.assertEqual(st, 200, cast)
        self.assertEqual(cast["recommendation_problems"], [])
        by_name = {p["name"]: p for p in cast["players"]}
        self.assertEqual(by_name["Alice"]["assignment"]["role_id"], "r_baker")
        bob_role = by_name["Bob"]["assignment"]["role_id"]
        self.assertNotEqual(bob_role, "r_baker")
        bob_baker = next(o for o in by_name["Bob"]["options"] if o["role_id"] == "r_baker")
        self.assertFalse(bob_baker["compatible"])
        self.assertTrue(any("Playing the murderer" in c for c in bob_baker["conflicts"]))
        self.step(f"recommendation: Alice -> Ollie Finch (required killer), Bob -> {PKG.roles[bob_role]['name']}; "
                  "Bob shown as incompatible with the killer role")

        bob_pid = by_name["Bob"]["id"]
        st, r = host.call("PUT", f"/api/games/{gid}/casting/assignments/{bob_pid}", {"role_id": "r_baker"})
        self.assertEqual(st, 409)
        self.assertIn("Playing the murderer", r["error"])
        st, r = host.call("PUT", f"/api/games/{gid}/casting/assignments/{bob_pid}", {"role_id": "r_organiser"})
        self.assertEqual(st, 409)   # sensitive romance tag left unspecified
        alt = next(rid for rid in ("r_gardener", "r_reporter") if rid != bob_role)
        st, r = host.call("PUT", f"/api/games/{gid}/casting/assignments/{bob_pid}", {"role_id": alt})
        self.assertEqual(st, 200, r)
        bob_role = alt
        self.step(f"override to incompatible roles rejected; compatible override to {PKG.roles[alt]['name']} accepted")

        self.assertEqual(alice.call("GET", f"/api/games/{gid}/me/character")[0], 409)
        st, r = host.call("POST", f"/api/games/{gid}/casting/approve")
        self.assertEqual((st, r["phase"]), (200, "TRAINING"), r)
        self.step("host approved the cast; phase -> TRAINING")

        # --- after approval: each sees only their own --------------------------
        st, a_char = alice.call("GET", f"/api/games/{gid}/me/character")
        self.assertEqual((st, a_char["id"]), (200, "r_baker"))
        self.assertEqual(a_char["secrets"], PKG.roles["r_baker"]["private"]["secrets"])
        st, b_char = bob.call("GET", f"/api/games/{gid}/me/character")
        self.assertEqual((st, b_char["id"]), (200, bob_role))
        self.step("Alice sees Ollie Finch's secrets; Bob sees his own character")

        others_for = {"alice": [r for r in PKG.roles if r != "r_baker"], "bob": [r for r in PKG.roles if r != bob_role]}
        for c, own in ((alice, "r_baker"), (bob, bob_role)):
            forbidden = sum((private_strings(r) for r in others_for[c.name]), []) + SOLUTION_STRINGS
            for rid in others_for[c.name]:
                st, r = c.call("GET", f"/api/games/{gid}/characters/{rid}")
                self.assertEqual(st, 403, f"{c.name} read {rid}")
            self.assertEqual(c.call("GET", f"/api/games/{gid}/characters/{own}")[0], 200)
            self.assertEqual(c.call("GET", f"/api/games/{gid}/solution")[0], 403)
            for path in ("", "/me/character", "/cast", "/assessment"):
                st, r = c.call("GET", f"/api/games/{gid}{path}")
                self.assertEqual(st, 200, path)
                self.assertNoLeak(r, forbidden, c.name)
        st, cast_list = bob.call("GET", f"/api/games/{gid}/cast")
        self.assertEqual({c["name"] for c in cast_list["cast"]}, {"Ollie Finch", PKG.roles[bob_role]["name"]})
        self.step("direct requests for the other character's file and the solution denied (403); "
                  "no secret text in any guest response")

        st, sol = host.call("GET", f"/api/games/{gid}/solution")
        self.assertEqual((st, sol["killer"]["id"]), (200, "r_baker"))
        for path in ("/api/games/x/solution", f"/api/games/{gid}/solution"):
            self.assertEqual(anon.call("GET", path)[0], 401)
        for path in ("/content/packages/lemon-tart/1.0.0/solution.json", "/../content/assessment/v1.json",
                     "/server/app.py", "/app.js/../../content/packages/lemon-tart/1.0.0/solution.json"):
            st, _ = anon.call("GET", path)
            self.assertEqual(st, 404, path)
        self.step("host can read the solution; anonymous and raw-file requests are refused")

        self.assertEqual(alice.call("POST", f"/api/games/{gid}/me/acknowledge")[0], 200)
        alice.call("GET", f"/api/games/{gid}/me/character")
        spare = Client(self.base, "spare")
        spare.call("POST", "/api/auth/register", {"email": "spare@example.test", "password": "correct-horse-1",
                                                  "display_name": "Spare"})
        st, _ = spare.call("POST", f"/api/invitations/{invites['Spare']}/respond", {"response": "accept"})
        self.assertEqual(st, 410)
        st, _ = alice.call("PUT", f"/api/games/{gid}/assessment/answers/q01", {"option_id": "a"})
        self.assertEqual(st, 409)
        self.step("unused invitation closed after approval; questionnaire locked")

        # --- restart -----------------------------------------------------------
        self.stop_server()
        self.start_server()
        st, a_again = alice.call("GET", f"/api/games/{gid}/me/character")
        self.assertEqual((st, a_again["id"], a_again["acknowledged"]), (200, "r_baker", True))
        st, b_again = bob.call("GET", f"/api/games/{gid}/me/character")
        self.assertEqual((st, b_again["id"]), (200, bob_role))
        st, hv = host.call("GET", f"/api/games/{gid}")
        self.assertEqual(hv["phase"], "TRAINING")
        self.assertEqual({p["display_name"]: p["approval_state"] for p in hv["players"]},
                         {"Alice": "approved", "Bob": "approved"})
        self.assertEqual(bob.call("GET", f"/api/games/{gid}/characters/r_baker")[0], 403)
        self.step("server restarted: sessions, approved assignments and privacy rules persist")

        receipts = self.query("SELECT player_id, claim_id, COUNT(*) AS n FROM disclosure_receipts "
                              f"WHERE game_id = '{gid}' GROUP BY player_id, claim_id")
        denied = self.query("SELECT COUNT(*) FROM audit_log WHERE action LIKE 'denied.%'")[0][0]
        self.assertEqual(len(receipts), 2)
        self.assertTrue(all(r["n"] == 1 for r in receipts))
        self.assertGreater(denied, 0)
        self.step(f"disclosure receipts recorded once per claim; {denied} denied requests audited")

    def test_forced_casting(self):
        """Reproduces the 'Test 1' situation: nobody fits the required killer role."""
        print()
        host = Client(self.base, "hostF")
        host.call("POST", "/api/auth/register", {"email": "hostf@example.test", "password": "correct-horse-1",
                                                 "display_name": "Host F"})
        gid = host.call("POST", "/api/games", {"title": "Forced", "package_id": "lemon-tart",
                                               "package_version": "1.0.0"})[1]["id"]
        guests = []
        for i, killer in enumerate(("allowed", "blocked")):
            c = Client(self.base, f"f{i}")
            c.call("POST", "/api/auth/register", {"email": f"f{i}@example.test", "password": "correct-horse-1",
                                                  "display_name": f"F{i}"})
            tok = host.call("POST", f"/api/games/{gid}/invitations", {"label": f"F{i}"})[1]["token"]
            self.assertEqual(c.call("POST", f"/api/invitations/{tok}/respond", {"response": "accept"})[0], 200)
            self.answer_all(c, gid, {d: "c" for d in ASSESS["dimensions"]})
            bounds = {t: "allowed" for t in PKG.content_tags}
            bounds["plays_killer"] = killer
            c.call("PUT", f"/api/games/{gid}/assessment/preferences",
                   {"boundaries": bounds, "preparation_minutes": 30, "assistance": "some", "stretch_preference": "modest"})
            self.assertEqual(c.call("POST", f"/api/games/{gid}/assessment/submit")[0], 200)
            guests.append(c)

        st, cast = host.call("POST", f"/api/games/{gid}/casting/recommend")
        self.assertTrue(any("can't be cast" in p for p in cast["recommendation_problems"]))
        self.assertEqual(host.call("POST", f"/api/games/{gid}/casting/approve")[0], 409)
        will = {p["name"]: {o["role_id"]: o for o in p["options"]} for p in cast["players"]}
        self.assertEqual(will["F1"]["r_baker"]["level"], "refused")
        self.assertEqual(will["F0"]["r_baker"]["level"], "stretch")
        self.assertGreater(will["F0"]["r_baker"]["willingness"], will["F1"]["r_baker"]["willingness"])
        self.assertEqual(guests[0].call("POST", f"/api/games/{gid}/casting/recommend", {"force": True})[0], 403)
        self.step("strict casting fails; willingness shows F0 'stretch' (prep) and F1 'refused' for the killer")

        st, cast = host.call("POST", f"/api/games/{gid}/casting/recommend", {"force": True})
        self.assertEqual((st, cast["recommendation_problems"], cast["validation_problems"]), (200, [], []))
        by = {p["name"]: p["assignment"] for p in cast["players"]}
        self.assertEqual((by["F0"]["role_id"], by["F0"]["source"]), ("r_baker", "forced"))
        self.assertEqual(by["F0"]["forced_conflicts"], ["needs 40 min preparation; guest offered 30"])
        self.assertEqual(by["F1"]["source"], "recommended")
        self.step("auto-assign gave the killer role to the more willing guest, overriding only their prep time")

        f1 = next(p for p in cast["players"] if p["name"] == "F1")
        st, r = host.call("PUT", f"/api/games/{gid}/casting/assignments/{f1['id']}", {"role_id": "r_baker"})
        self.assertEqual(st, 409)
        self.assertTrue(r["can_force"])
        st, cast = host.call("PUT", f"/api/games/{gid}/casting/assignments/{f1['id']}",
                             {"role_id": "r_baker", "force": True})
        self.assertEqual(st, 200, cast)
        by = {p["name"]: p["assignment"] for p in cast["players"]}
        self.assertEqual(by["F1"]["role_id"], "r_baker")
        self.assertIn("said No thanks to: Playing the murderer", by["F1"]["forced_conflicts"])
        self.assertEqual(cast["validation_problems"], [])
        self.step("manual override: forcing F1 (No thanks) into the killer role needs an explicit force, then succeeds")

        self.assertEqual(host.call("POST", f"/api/games/{gid}/casting/approve")[0], 200)
        st, mine = guests[1].call("GET", f"/api/games/{gid}/me/character")
        self.assertEqual((st, mine["id"]), (200, "r_baker"))
        self.assertNotIn("willingness", json.dumps(mine))
        log = self.query(f"SELECT action FROM audit_log WHERE game_id = '{gid}' AND action LIKE 'casting.%forced'")
        self.assertEqual(len(log), 2)
        self.step("approved; forced guest sees their character; both overrides are in the audit log")

    def test_ten_guests(self):
        pkg = content.load_package("lemon-tart", "1.1.0")
        self.assertEqual(len(pkg.roles), 10)
        print()
        host = Client(self.base, "host10")
        host.call("POST", "/api/auth/register", {"email": "host10@example.test", "password": "correct-horse-1",
                                                 "display_name": "Host Ten"})
        st, r = host.call("POST", "/api/games", {"title": "Ten Guest Party", "package_id": "lemon-tart",
                                                 "package_version": "1.1.0"})
        self.assertEqual(st, 201, r)
        gid = r["id"]
        st, pk = host.call("GET", "/api/packages")
        self.assertEqual(pk["packages"][0]["content_version"], "1.1.0")   # newest offered first

        letters = "abcd"
        preps = [60, 60, 45, 45, 30, 30, 30, 25, 20, 15]
        guests = []
        for i in range(11):
            c = Client(self.base, f"guest{i}")
            st, _ = c.call("POST", "/api/auth/register", {"email": f"g{i}@example.test", "password": "correct-horse-1",
                                                          "display_name": f"Guest {i}"})
            self.assertEqual(st, 201)
            st, inv = host.call("POST", f"/api/games/{gid}/invitations", {"label": f"Guest {i}"})
            st, r = c.call("POST", f"/api/invitations/{inv['token']}/respond", {"response": "accept"})
            if i == 10:
                self.assertEqual(st, 409)
                self.assertIn("full", r["error"])
                continue
            self.assertEqual(st, 200, r)
            picks = {d: letters[(i + k) % 4] for k, d in enumerate(ASSESS["dimensions"])}
            self.answer_all(c, gid, picks)
            bounds = {t: "unspecified" for t in pkg.content_tags}
            if i < 2:
                bounds["plays_killer"] = "allowed"
            if 2 <= i <= 4:
                bounds["romance_storyline"] = "allowed"
            if i == 9:
                bounds["public_deception"] = "blocked"
            st, r = c.call("PUT", f"/api/games/{gid}/assessment/preferences",
                           {"boundaries": bounds, "preparation_minutes": preps[i],
                            "assistance": ["none", "some", "full"][i % 3], "stretch_preference": "modest"})
            self.assertEqual(st, 200, r)
            self.assertEqual(c.call("POST", f"/api/games/{gid}/assessment/submit")[0], 200)
            guests.append(c)
        self.step("10 guests joined and completed questionnaires; an 11th was refused because the game is full")

        st, cast = host.call("POST", f"/api/games/{gid}/casting/recommend")
        self.assertEqual(st, 200, cast)
        self.assertEqual(cast["recommendation_problems"], [])
        roles = [p["assignment"]["role_id"] for p in cast["players"]]
        self.assertEqual(sorted(roles), sorted(pkg.roles))
        st, r = host.call("POST", f"/api/games/{gid}/casting/approve")
        self.assertEqual(st, 200, r)
        self.step("recommendation filled all 10 roles with no conflicts; host approved")

        seen = {}
        for c in guests:
            st, mine = c.call("GET", f"/api/games/{gid}/me/character")
            self.assertEqual(st, 200)
            seen[c.name] = mine["id"]
            forbidden = sum((private_strings_for(pkg, rid) for rid in pkg.roles if rid != mine["id"]), [])
            forbidden += [pkg.solution["motive"], pkg.solution["method"]]
            for path in ("", "/me/character", "/cast", "/assessment"):
                self.assertNoLeak(c.call("GET", f"/api/games/{gid}{path}")[1], forbidden, c.name)
            for rid in pkg.roles:
                if rid != mine["id"]:
                    self.assertEqual(c.call("GET", f"/api/games/{gid}/characters/{rid}")[0], 403)
            self.assertEqual(c.call("GET", f"/api/games/{gid}/solution")[0], 403)
        self.assertEqual(len(set(seen.values())), 10)
        st, cl = guests[0].call("GET", f"/api/games/{gid}/cast")
        self.assertEqual(len(cl["cast"]), 10)
        self.step("each of 10 guests sees only their own character; 90 cross-character requests denied")


if __name__ == "__main__":
    unittest.main(verbosity=2)
