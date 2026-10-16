"""P05 acceptance checks: QR clues (no secrets in the code, locked/invalid
codes explained), each character's own interpretation, the journal (no
repeat rewards), secret missions with one-time bonus clues, host backups.
Run:  py -m unittest -v"""

import json
import os
import shutil
import tempfile
import unittest

from tests.test_p01 import Server


class P05(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.s = Server(os.path.join(self.dir, "t.db"))
        g = {}
        for name, char in (("Alice", "butler"), ("Bob", "doctor"), ("Cara", "painter"), ("Dan", "niece")):
            state = self.s.host("POST", "/api/host/guests", {"name": name})
            g[name] = next(x for x in state["guests"] if x["name"] == name)
            self.s.host("POST", f"/api/host/guests/{g[name]['id']}/assign", {"character_id": char, "confirm": True})
        self.alice, self.bob, self.cara, self.dan = g["Alice"], g["Bob"], g["Cara"], g["Dan"]
        self.s.host("POST", "/api/host/release", {})
        self.s.host("POST", "/api/host/phase", {"phase": "investigation"})

    def tearDown(self):
        self.s.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def find(self, guest, code, source="scan"):
        status, text = self.s.call("POST", "/api/me/clues/find", guest["token"], {"code": code, "source": source})
        return status, json.loads(text)

    def journal(self, guest):
        return json.loads(self.s.call("GET", "/api/me", guest["token"])[1])["investigation"]["clues"]

    # P05-1 QR evidence
    def test_labels_carry_only_the_code(self):
        labels = self.s.host("GET", "/api/host/labels")["labels"]
        self.assertEqual([l["code"] for l in labels], ["VIAL-7K", "NPKN-4R", "LTTR-9D", "LDGR-2M"])
        self.assertTrue(labels[0]["url"].endswith("/c/VIAL-7K"))
        self.assertIn("<svg", labels[0]["svg"])
        self.assertEqual(self.s.call("GET", "/c/VIAL-7K")[0], 200, "the QR opens the game page")
        self.assertEqual(self.s.call("GET", "/api/host/labels", self.alice["token"])[0], 403)

    def test_finding_a_clue(self):
        status, view = self.find(self.bob, "vial-7k ")           # case and spaces don't matter
        self.assertEqual(status, 200, view)
        self.assertEqual(view["found"], {"id": "vial-clue", "new": True})
        clue = view["investigation"]["clues"][0]
        self.assertEqual(clue["title"], "An empty glass vial")
        self.assertEqual(clue["source"], "Scanned")
        self.assertEqual(clue["evidence"], "An empty glass vial")
        self.assertIn("vial", [e["id"] for e in view["investigation"]["evidence"]], "the clue is now evidence")

    def test_bad_and_locked_codes_explain_themselves(self):
        status, view = self.find(self.alice, "NOPE-00")
        self.assertEqual(status, 404)
        self.assertIn("doesn't match any clue", view["error"])
        status, view = self.find(self.alice, "LTTR-9D")              # unlocks in round 2
        self.assertEqual(status, 409)
        self.assertIn("round 2", view["error"])
        self.assertNotIn("handbag", json.dumps(view), "a locked clue reveals nothing")
        self.s.host("POST", "/api/host/round", {"number": 2})
        self.assertEqual(self.find(self.alice, "LTTR-9D")[0], 200)

    def test_no_new_clues_when_paused_or_before_the_murder(self):
        self.s.host("POST", "/api/host/pause", {"paused": True})
        self.assertEqual(self.find(self.alice, "VIAL-7K")[0], 409)
        self.s.host("POST", "/api/host/pause", {"paused": False})
        self.s.host("POST", "/api/host/phase", {"phase": "arrival"})
        self.assertEqual(self.find(self.alice, "VIAL-7K")[0], 409)

    # P05-2 Character interpretations
    def test_two_characters_see_different_details(self):
        bob = self.find(self.bob, "VIAL-7K")[1]["investigation"]["clues"][0]
        alice = self.find(self.alice, "VIAL-7K")[1]["investigation"]["clues"][0]
        cara = self.find(self.cara, "VIAL-7K")[1]["investigation"]["clues"][0]
        self.assertEqual(bob["text"], alice["text"])
        self.assertIn("from your medical bag", bob["mine"])
        self.assertIn("doctor's bag when he visits", alice["mine"])
        self.assertEqual(cara["mine"], "")

    def test_scans_stay_private(self):
        self.find(self.bob, "VIAL-7K")
        raw = self.s.call("GET", "/api/me", self.alice["token"])[1]
        self.assertNotIn("from your medical bag", raw)
        self.assertEqual(self.journal(self.alice), [])

    # P05-3 Evidence journal
    def test_repeat_scans_change_nothing(self):
        self.find(self.alice, "VIAL-7K")
        status, view = self.find(self.alice, "VIAL-7K")
        self.assertEqual(status, 200)
        self.assertEqual(view["found"]["new"], False)
        self.assertEqual(len(view["investigation"]["clues"]), 1)
        self.s.host("POST", "/api/host/phase", {"phase": "accusation"})
        self.assertEqual(self.find(self.alice, "VIAL-7K")[0], 200, "already-found clues can be reopened later")

    # P05-4 Secret missions and backups
    def test_guests_see_only_their_missions(self):
        me = json.loads(self.s.call("GET", "/api/me", self.alice["token"])[1])
        self.assertEqual([m["id"] for m in me["investigation"]["missions"]], ["pike-mission"])
        self.assertNotIn("letter J", self.s.call("GET", "/api/me", self.alice["token"])[1])

    def test_confirming_a_mission_rewards_once(self):
        state = self.s.host("POST", f"/api/host/guests/{self.alice['id']}/mission", {"mission_id": "pike-mission"})
        self.assertIn("Mission confirmed", state["notice"])
        state = self.s.host("POST", f"/api/host/guests/{self.alice['id']}/mission", {"mission_id": "pike-mission"})
        self.assertIn("Already", state["notice"])
        clues = self.journal(self.alice)
        self.assertEqual([c["id"] for c in clues], ["passport-clue"])
        self.assertEqual(clues[0]["source"], "Mission reward")
        me = json.loads(self.s.call("GET", "/api/me", self.alice["token"])[1])
        self.assertTrue(me["investigation"]["missions"][0]["done"])
        status, _ = self.s.call("POST", f"/api/host/guests/{self.bob['id']}/mission", self.s.host_key,
                                {"mission_id": "pike-mission"})
        self.assertEqual(status, 400, "Bob isn't Pike")

    def test_host_can_release_an_essential_clue_any_time(self):
        self.s.host("POST", "/api/host/phase", {"phase": "accusation"})
        self.s.host("POST", f"/api/host/guests/{self.cara['id']}/clue", {"clue_id": "napkin-clue"})
        clue = self.journal(self.cara)[0]
        self.assertEqual((clue["id"], clue["source"]), ("napkin-clue", "From the host"))
        self.assertIn("doctor wiping his hands", clue["mine"])
        overview = {c["id"]: c for c in self.s.host("GET", "/api/host")["clues"]}
        self.assertEqual(overview["napkin-clue"]["found_by"], ["Cara"])

    def test_guests_cannot_release_clues_or_confirm_missions(self):
        self.assertEqual(self.s.call("POST", f"/api/host/guests/{self.alice['id']}/clue", self.alice["token"],
                                     {"clue_id": "vial-clue"})[0], 403)
        self.assertEqual(self.s.call("POST", f"/api/host/guests/{self.alice['id']}/mission", self.alice["token"],
                                     {"mission_id": "pike-mission"})[0], 403)

    def test_every_essential_clue_has_a_backup(self):
        for c in self.s.story["clues"]:
            if c.get("essential"):
                self.assertTrue(c.get("backup"), c["id"])


if __name__ == "__main__":
    unittest.main()
