"""Checks for the room screen (speaker / TV): watch-only, nothing secret.
Run:  py -m unittest -v"""

import json
import os
import shutil
import tempfile
import unittest

from tests import test_p04

P04 = test_p04.P04


class Room(test_p04.P04):
    """Reuses the P04 setup: Alice (Pike), Bob (Thorne, the killer), Cara (Juniper)."""

    def room(self, key=None):
        key = key or self.s.host("GET", "/api/host")["room_key"]
        status, text = self.s.call("GET", "/api/room", key)
        return status, (json.loads(text) if status == 200 else text)

    def test_only_the_room_link_opens_it(self):
        self.assertEqual(self.room()[0], 200)
        for key in (self.alice["token"], self.s.host_key, "nope-not-a-key-000000000000"):
            self.assertEqual(self.s.call("GET", "/api/room", key)[0], 403)
        self.assertEqual(self.s.call("GET", f"/room/{self.s.host('GET', '/api/host')['room_key']}")[0], 200)

    def test_announces_who_is_questioned_and_the_question_but_nothing_secret(self):
        self.ask(self.alice, "interrogate", "Where were you during the toast?", target=self.cara)
        status, room = self.room()
        ev = room["events"][0]
        self.assertEqual((ev["text"], ev["question"]),
                         ("Juniper Vale, you are being questioned!", "Where were you during the toast?"))
        raw = json.dumps(room)
        for secret in ("AI LINE", "AI TIP", "Alice", "Cara", "Pike",
                       self.s.story["solution"]["motive"]):
            self.assertNotIn(secret, raw)

    def test_confession_is_announced_without_naming_anyone(self):
        self.s.host("POST", f"/api/host/guests/{self.alice['id']}/evidence", {"evidence_id": "vial"})
        self.ask(self.alice, "interrogate", "Explain this vial!", target=self.bob, evidence="vial")
        kinds = [(e["kind"], e["text"]) for e in self.room()[1]["events"]]
        self.assertIn(("confession", "Someone has confessed!"), kinds)

    def test_unanswered_questions_are_not_announced(self):
        self.mode = "nomatch"
        self.ask(self.alice, "interrogate", "Favourite colour?", target=self.cara)
        self.assertEqual(self.room()[1]["events"], [])

    def test_new_room_link_retires_the_old_one(self):
        old = self.s.host("GET", "/api/host")["room_key"]
        new = self.s.host("POST", "/api/host/room/new-link", {})["room_key"]
        self.assertNotEqual(old, new)
        self.assertEqual(self.s.call("GET", "/api/room", old)[0], 403)
        self.assertEqual(self.s.call("GET", "/api/room", new)[0], 200)


del P04  # keep the test runner from collecting P04's tests twice

# Don't rerun every P04 test a second time under this class
for name in [n for n in dir(test_p04.P04) if n.startswith("test_")]:
    if getattr(Room, name) is getattr(test_p04.P04, name):
        setattr(Room, name, None)

if __name__ == "__main__":
    unittest.main()
