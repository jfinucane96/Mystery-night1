"""P01 acceptance checks: private links, host-only actions, private character
views, and state that survives a restart.   Run:  py -m unittest -v"""

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from server import app, db as dbmod, story as storymod


class Server:
    """Runs the real app on a free port with a throwaway SQLite file."""

    def __init__(self, sqlite_path):
        self.story, self.assessment = app.load_content()
        self.db = dbmod.Database(sqlite_path=sqlite_path)
        self.httpd, self.host_key = app.make_server(self.db, self.story, self.assessment, "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.db.close()

    def call(self, method, path, key=None, body=None):
        """Returns (status, raw response text)."""
        headers = {"X-Key": key} if key else {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def host(self, method, path, body=None):
        status, text = self.call(method, path, self.host_key, body)
        assert status == 200, (status, text)
        return json.loads(text)


class P01(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.dbfile = os.path.join(self.dir, "test.db")
        self.s = Server(self.dbfile)
        state = self.s.host("POST", "/api/host/guests", {"name": "Alice"})
        state = self.s.host("POST", "/api/host/guests", {"name": "Bob"})
        self.alice, self.bob = state["guests"]
        self.s.host("POST", f"/api/host/guests/{self.alice['id']}/assign", {"character_id": "butler"})
        self.s.host("POST", f"/api/host/guests/{self.bob['id']}/assign", {"character_id": "niece", "confirm": True})
        self.s.host("POST", "/api/host/release", {})

    def tearDown(self):
        self.s.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def me(self, guest, server=None):
        status, text = (server or self.s).call("GET", "/api/me", guest["token"])
        return status, text

    # P01-1 Player access
    def test_each_link_opens_only_its_guest(self):
        status, text = self.me(self.alice)
        self.assertEqual(status, 200)
        view = json.loads(text)
        self.assertEqual(view["guest_name"], "Alice")
        self.assertEqual(view["character"]["name"], "Mr. Edmund Pike")
        view = json.loads(self.me(self.bob)[1])
        self.assertEqual(view["character"]["name"], "Rosalind Ravenscroft")

    def test_invalid_and_replaced_links_fail(self):
        self.assertEqual(self.s.call("GET", "/api/me", "x" * 43)[0], 404)
        self.assertEqual(self.s.call("GET", "/api/me")[0], 404)
        self.s.host("POST", f"/api/host/guests/{self.alice['id']}/new-link", {})
        self.assertEqual(self.me(self.alice)[0], 404, "old link must stop working")
        self.s.host("POST", f"/api/host/guests/{self.bob['id']}/remove", {})
        self.assertEqual(self.me(self.bob)[0], 404, "removed guest's link must stop working")

    def test_no_signup_routes(self):
        for path in ("/api/auth/register", "/api/auth/login", "/api/signup"):
            self.assertEqual(self.s.call("POST", path, body={})[0], 404)

    # P01-2 Host access
    def test_guest_links_cannot_call_host_actions(self):
        gid = self.bob["id"]
        attempts = [("GET", "/api/host", None),
                    ("POST", "/api/host/guests", {"name": "Mallory"}),
                    ("POST", f"/api/host/guests/{gid}/assign", {"character_id": "doctor"}),
                    ("POST", f"/api/host/guests/{gid}/new-link", {}),
                    ("POST", f"/api/host/guests/{gid}/remove", {})]
        for key in (None, "wrong", self.alice["token"]):
            for method, path, body in attempts:
                self.assertEqual(self.s.call(method, path, key, body)[0], 403, (key, path))
        names = [g["name"] for g in self.s.host("GET", "/api/host")["guests"]]
        self.assertEqual(names, ["Alice", "Bob"])

    def test_host_sees_solution_and_one_character_per_guest(self):
        state = self.s.host("GET", "/api/host")
        self.assertEqual(state["solution"]["killer"], "Dr. Silas Thorne")
        status, text = self.s.call("POST", f"/api/host/guests/{self.bob['id']}/assign",
                                   self.s.host_key, {"character_id": "butler"})
        self.assertEqual(status, 409)
        self.assertIn("already assigned to Alice", text)

    # P01-3 Private character view
    def test_other_secrets_absent_from_guest_payload(self):
        raw = self.me(self.alice)[1]
        mine = self.s.story["by_id"]["butler"]
        for c in self.s.story["characters"]:
            for text in c["secrets"] + c["objectives"] + [c["biography"]]:
                if c is mine:
                    self.assertIn(json.dumps(text)[1:-1], raw)
                else:
                    self.assertNotIn(text, raw)
        sol = self.s.story["solution"]
        for text in (sol["motive"], sol["method"]):
            self.assertNotIn(text, raw)

    def test_unassigned_guest_sees_no_character(self):
        self.s.host("POST", f"/api/host/guests/{self.alice['id']}/assign", {"character_id": ""})
        view = json.loads(self.me(self.alice)[1])
        self.assertIsNone(view["character"])

    # P01-4 Saved state
    def test_state_survives_restart(self):
        self.s.stop()
        again = Server(self.dbfile)
        try:
            self.assertEqual(again.host_key, self.s.host_key, "host link must not change on restart")
            view = json.loads(self.me(self.alice, again)[1])
            self.assertEqual(view["character"]["name"], "Mr. Edmund Pike")
        finally:
            again.stop()
            self.s = Server(self.dbfile)  # so tearDown has something to stop

    # Pages
    def test_pages(self):
        self.assertEqual(self.s.call("GET", f"/play/{self.alice['token']}")[0], 200)
        self.assertEqual(self.s.call("GET", f"/host/{self.s.host_key}")[0], 200)
        self.assertEqual(self.s.call("GET", "/content/story.json")[0], 404)
        self.assertEqual(self.s.call("GET", "/static/../server/app.py")[0], 404)


class StoryFile(unittest.TestCase):
    def test_story_has_no_problems(self):
        app.load_content()


if __name__ == "__main__":
    unittest.main()
