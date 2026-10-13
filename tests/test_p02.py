"""P02 acceptance checks: assessment that saves and resumes, hard limits,
manual casting with warnings/blocks, and all-at-once character release.
Run:  py -m unittest -v"""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest

from server import db as dbmod
from tests.test_p01 import Server

ALL_ANSWERS = {"spotlight": 4, "acting": 4, "investigation": 4, "deception": 4, "missions": 4, "preparation": 4}
SHY_ANSWERS = {"spotlight": 1, "acting": 1, "investigation": 3, "deception": 1, "missions": 1, "preparation": 1}


class P02(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.dbfile = os.path.join(self.dir, "test.db")
        self.s = Server(self.dbfile)
        state = self.s.host("POST", "/api/host/guests", {"name": "Alice"})
        state = self.s.host("POST", "/api/host/guests", {"name": "Bob"})
        self.alice, self.bob = state["guests"]

    def tearDown(self):
        self.s.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def me(self, guest):
        status, text = self.s.call("GET", "/api/me", guest["token"])
        self.assertEqual(status, 200, text)
        return json.loads(text)

    def save(self, guest, body):
        return self.s.call("POST", "/api/me/assessment", guest["token"], body)

    def finish(self, guest, answers=ALL_ANSWERS, limits=None, note=""):
        status, text = self.save(guest, {"answers": answers, "limits": limits or {"killer": "ok", "romance": "ok"},
                                         "note": note, "submit": True})
        self.assertEqual(status, 200, text)

    def assign(self, guest, character_id, confirm=False):
        return self.s.call("POST", f"/api/host/guests/{guest['id']}/assign", self.s.host_key,
                           {"character_id": character_id, "confirm": confirm})

    def guest_row(self, guest):
        return next(g for g in self.s.host("GET", "/api/host")["guests"] if g["id"] == guest["id"])

    # P02-1 Involvement assessment
    def test_six_questions_save_and_resume(self):
        view = self.me(self.alice)
        self.assertEqual(view["stage"], "assessment")
        self.assertEqual(len(view["assessment"]["questions"]), 6)
        self.save(self.alice, {"answers": {"spotlight": 2, "acting": 3}})
        self.save(self.alice, {"answers": {"spotlight": 9, "nonsense": 1}})   # invalid: ignored
        view = self.me(self.alice)                                            # e.g. after closing the page
        self.assertEqual(view["stage"], "assessment")
        self.assertEqual(view["assessment"]["answers"], {"spotlight": 2, "acting": 3})

    def test_submit_requires_everything(self):
        status, _ = self.save(self.alice, {"answers": {"spotlight": 2}, "submit": True})
        self.assertEqual(status, 400)
        status, _ = self.save(self.alice, {"answers": ALL_ANSWERS, "submit": True})
        self.assertEqual(status, 400, "limits must be chosen too")
        self.finish(self.alice)
        self.assertEqual(self.me(self.alice)["stage"], "waiting")

    def test_host_sees_preferences_and_preparation(self):
        self.finish(self.alice, SHY_ANSWERS, {"killer": "no", "romance": "unsure"}, note="No spiders please")
        row = self.guest_row(self.alice)
        self.assertTrue(row["assessment_done"])
        self.assertEqual(row["answers"]["preparation"], 1)
        self.assertEqual(row["limits"], {"killer": "no", "romance": "unsure"})
        self.assertEqual(row["note"], "No spiders please")

    def test_answers_are_private_to_host(self):
        self.finish(self.alice, SHY_ANSWERS, {"killer": "no", "romance": "ok"}, note="secret note")
        raw = self.s.call("GET", "/api/me", self.bob["token"])[1]
        self.assertNotIn("secret note", raw)
        self.assertEqual(self.s.call("GET", "/api/host", self.bob["token"])[0], 403)

    # P02-2 Hard boundaries
    def test_not_ok_blocks_assignment(self):
        self.finish(self.alice, limits={"killer": "no", "romance": "ok"})
        for confirm in (False, True):
            status, text = self.assign(self.alice, "doctor", confirm)   # doctor is the killer
            self.assertEqual(status, 409)
            self.assertIn("said no to being the murderer", text.lower())
        self.assertIsNone(self.guest_row(self.alice)["character_id"])

    def test_no_preference_needs_host_confirmation(self):
        self.finish(self.alice, limits={"killer": "ok", "romance": "unsure"})
        status, text = self.assign(self.alice, "niece")                 # niece has romance
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(text)["code"], "needs_confirm")
        status, _ = self.assign(self.alice, "niece", confirm=True)
        self.assertEqual(status, 200)

    def test_unanswered_guest_also_needs_confirmation_for_sensitive_roles(self):
        status, text = self.assign(self.bob, "doctor")
        self.assertEqual(json.loads(text)["code"], "needs_confirm")

    # P02-3 Manual role assignment
    def test_load_warnings(self):
        self.finish(self.alice, SHY_ANSWERS)
        fit = self.guest_row(self.alice)["fit"]
        self.assertIn("Role needs Bluffing 3+, they chose 1", fit["doctor"]["warnings"])
        self.assertEqual(fit["butler"], {"blocked": [], "check": [], "warnings": []})
        self.assertEqual(self.assign(self.alice, "doctor")[0], 200, "warnings don't stop the host")

    def test_release_refuses_a_role_the_guest_later_refused(self):
        self.finish(self.alice)
        self.assertEqual(self.assign(self.alice, "doctor")[0], 200)
        self.save(self.alice, {"limits": {"killer": "no"}})
        status, text = self.s.call("POST", "/api/host/release", self.s.host_key, {})
        self.assertEqual(status, 409)
        self.assertIn("Alice", text)

    # P02-4 Character release
    def test_characters_hidden_until_release(self):
        self.finish(self.alice)
        self.assign(self.alice, "doctor")
        raw = self.s.call("GET", "/api/me", self.alice["token"])[1]
        self.assertIsNone(json.loads(raw)["character"])
        for c in self.s.story["characters"]:
            for text in c["secrets"]:
                self.assertNotIn(text, raw)
        self.assertEqual(self.s.call("POST", "/api/host/release", self.alice["token"], {})[0], 403)
        self.s.host("POST", "/api/host/release", {})
        self.s.host("POST", "/api/host/release", {})          # pressing twice is harmless
        view = self.me(self.alice)
        self.assertEqual(view["stage"], "character")
        self.assertEqual(view["character"]["name"], "Dr. Silas Thorne")
        bob = self.me(self.bob)                                # released but not cast
        self.assertEqual(bob["stage"], "character")
        self.assertIsNone(bob["character"])

    def test_answers_locked_after_release(self):
        self.s.host("POST", "/api/host/release", {})
        self.assertEqual(self.save(self.alice, {"answers": {"spotlight": 1}})[0], 409)


class Upgrade(unittest.TestCase):
    def test_p01_database_upgrades_without_losing_guests(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "old.db")
            conn = sqlite3.connect(path)
            for stmt in dbmod.MIGRATIONS[0]:
                conn.execute(stmt)
            conn.execute("INSERT INTO game VALUES (1, 'key', 'now')")
            conn.execute("INSERT INTO guests VALUES ('g00000001', 'Old Guest', 'tok', 'butler', 'now')")
            conn.commit()
            conn.close()
            db = dbmod.Database(sqlite_path=path)
            db.init_schema()
            db.init_schema()   # running again changes nothing
            with db.tx() as t:
                g = t.one("SELECT * FROM guests")
                self.assertEqual((g["name"], g["character_id"], g["assessment_done"]), ("Old Guest", "butler", 0))
                self.assertIsNone(t.one("SELECT released_at FROM game")["released_at"])
            db.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
