"""Checks for starting the party over.   Run:  py -m unittest -v"""

import json
import unittest

from server import game
from tests.test_p04 import P04 as _P04


class Reset(_P04):
    def test_reset_keeps_guests_and_links_but_wipes_progress(self):
        self.s.call("POST", f"/api/host/guests/{self.alice['id']}/contact", self.s.host_key,
                    {"phone": "555 123 4567", "consent": True, "is_host": True})
        self.ask(self.alice, "interrogate", "Where were you during the toast?", target=self.cara)
        room_before = self.s.host("GET", "/api/host")["room_key"]
        with self.s.db.tx() as t:
            game.reset_game(t)
        state = self.s.host("GET", "/api/host")          # host link still works
        self.assertEqual(state["room_key"], room_before)
        self.assertEqual((state["game"]["phase"], state["game"]["round"], state["released"]), ("setup", 0, False))
        self.assertEqual(state["questions"], [])
        alice = next(g for g in state["guests"] if g["name"] == "Alice")
        self.assertEqual((alice["phone"], alice["is_host"], alice["character_id"], alice["assessment_done"]),
                         ("+15551234567", True, None, False))
        self.assertEqual(len(state["guests"]), 3)
        self.assertEqual(self.s.call("GET", "/api/me", self.alice["token"])[0], 404, "old link retired")
        view = json.loads(self.s.call("GET", "/api/me", alice["token"])[1])
        self.assertEqual(view["stage"], "assessment")
        self.assertEqual(view["assessment"]["answers"], {})


for _name in [n for n in dir(_P04) if n.startswith("test_")]:
    setattr(Reset, _name, None)
del _P04

if __name__ == "__main__":
    unittest.main()
