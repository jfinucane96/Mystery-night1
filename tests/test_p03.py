"""P03 acceptance checks: host-controlled phases and rounds, pause, question
token balances and grants, private messages, safe repeats.
Run:  py -m unittest -v"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from server import play
from server.game import GameError
from tests.test_p01 import Server
from tests.test_texting_ai import TWILIO_ENV


class P03(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.sent = []
        self.s = Server(os.path.join(self.dir, "t.db"), send_text=lambda to, body: self.sent.append((to, body)))
        self.s.host("POST", "/api/host/guests", {"name": "Alice"})
        state = self.s.host("POST", "/api/host/guests", {"name": "Bob"})
        by = {g["name"]: g for g in state["guests"]}
        self.alice, self.bob = by["Alice"], by["Bob"]
        self.s.host("POST", f"/api/host/guests/{self.alice['id']}/assign", {"character_id": "butler"})
        self.s.host("POST", f"/api/host/guests/{self.bob['id']}/assign", {"character_id": "niece", "confirm": True})
        self.s.host("POST", "/api/host/release", {})

    def tearDown(self):
        self.s.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def me(self, g):
        status, text = self.s.call("GET", "/api/me", g["token"])
        self.assertEqual(status, 200, text)
        return json.loads(text)

    def tokens(self, g):
        return self.me(g)["tokens"]

    # P03-1 Phase controls
    def test_phases_move_forward_and_guests_see_them(self):
        self.assertEqual(self.me(self.alice)["game"]["phase"], "setup")
        self.s.host("POST", "/api/host/phase", {"phase": "arrival"})
        self.assertEqual(self.me(self.alice)["game"]["phase_name"], "Arrival")
        self.assertNotIn("murder", self.me(self.alice))
        self.s.host("POST", "/api/host/phase", {"phase": "investigation"})
        view = self.me(self.alice)
        self.assertEqual((view["game"]["phase"], view["game"]["round"]), ("investigation", 1))
        self.assertEqual(view["murder"]["title"], self.s.story["murder_announcement"]["title"])
        for phase in ("accusation", "reveal"):
            self.s.host("POST", "/api/host/phase", {"phase": phase})
            self.assertEqual(self.me(self.bob)["game"]["phase"], phase)
        self.assertEqual(self.s.call("POST", "/api/host/phase", self.s.host_key, {"phase": "party"})[0], 400)

    def test_pause_blocks_investigative_actions(self):
        self.s.host("POST", "/api/host/phase", {"phase": "investigation"})
        self.s.host("POST", "/api/host/pause", {"paused": True})
        self.assertTrue(self.me(self.alice)["game"]["paused"])
        with self.s.db.tx() as t:
            with self.assertRaises(GameError):
                play.require_investigating(t)
        self.s.host("POST", "/api/host/pause", {"paused": False})
        with self.s.db.tx() as t:
            play.require_investigating(t)          # no error once resumed

    def test_investigative_actions_need_the_investigation_phase(self):
        with self.s.db.tx() as t:
            with self.assertRaises(GameError):
                play.require_investigating(t)

    def test_guests_cannot_control_the_game(self):
        for path, body in (("/api/host/phase", {"phase": "reveal"}), ("/api/host/pause", {"paused": True}),
                           ("/api/host/round", {"number": 2}),
                           ("/api/host/grant", {"request_id": "abcdefgh1", "target": "all", "public": 5})):
            self.assertEqual(self.s.call("POST", path, self.alice["token"], body)[0], 403, path)

    # P03-2 Question balances
    def test_starting_investigation_gives_starting_tokens_once(self):
        self.assertEqual(self.tokens(self.alice), {"public": 0, "private": 0})
        self.s.host("POST", "/api/host/phase", {"phase": "investigation"})
        self.s.host("POST", "/api/host/phase", {"phase": "investigation"})      # double tap
        self.assertEqual(self.tokens(self.alice), {"public": 2, "private": 1})
        self.s.host("POST", "/api/host/phase", {"phase": "arrival"})            # going back and forth
        self.s.host("POST", "/api/host/phase", {"phase": "investigation"})
        self.assertEqual(self.tokens(self.bob), {"public": 2, "private": 1})

    def test_rounds_top_up_once_and_unused_tokens_carry_forward(self):
        self.s.host("POST", "/api/host/phase", {"phase": "investigation"})
        self.s.host("POST", "/api/host/round", {"number": 2})
        self.s.host("POST", "/api/host/round", {"number": 2})                    # double tap
        self.assertEqual(self.tokens(self.alice), {"public": 3, "private": 1})
        self.assertEqual(self.s.call("POST", "/api/host/round", self.s.host_key, {"number": 5})[0], 409)

    def test_rounds_only_during_investigation(self):
        self.assertEqual(self.s.call("POST", "/api/host/round", self.s.host_key, {"number": 2})[0], 409)

    def test_grants_to_everyone_or_one_guest_and_repeats_are_ignored(self):
        self.s.host("POST", "/api/host/grant", {"request_id": "tap-000001", "target": "all", "public": 1, "private": 0})
        state = self.s.host("POST", "/api/host/grant", {"request_id": "tap-000001", "target": "all", "public": 1, "private": 0})
        self.assertIn("repeat", state["notice"])
        self.s.host("POST", "/api/host/grant", {"request_id": "tap-000002", "target": self.bob["id"], "public": 0, "private": 2})
        self.assertEqual(self.tokens(self.alice), {"public": 1, "private": 0})
        self.assertEqual(self.tokens(self.bob), {"public": 1, "private": 2})
        for bad in ({"public": 0, "private": 0}, {"public": 99, "private": 0}, {"public": "1", "private": 0}):
            status, _ = self.s.call("POST", "/api/host/grant", self.s.host_key,
                                    {"request_id": "tap-000003", "target": "all", **bad})
            self.assertEqual(status, 400, bad)
        self.assertEqual(self.s.call("POST", "/api/host/grant", self.s.host_key,
                                     {"target": "all", "public": 1, "private": 0})[0], 400, "needs a request id")

    def test_balances_survive_restart(self):
        self.s.host("POST", "/api/host/phase", {"phase": "investigation"})
        self.s.stop()
        self.s = Server(os.path.join(self.dir, "t.db"))
        self.assertEqual(self.tokens(self.alice), {"public": 2, "private": 1})

    # P03-3 Private directions
    def test_messages_reach_only_their_guest(self):
        self.s.host("POST", f"/api/host/guests/{self.alice['id']}/message",
                    {"request_id": "msg-000001", "body": "Meet me by the fireplace."})
        self.s.host("POST", f"/api/host/guests/{self.alice['id']}/message",
                    {"request_id": "msg-000001", "body": "Meet me by the fireplace."})   # double tap
        alice = self.me(self.alice)
        self.assertEqual([m["body"] for m in alice["messages"]], ["Meet me by the fireplace."])
        self.assertFalse(alice["messages"][0]["read"])
        self.assertNotIn("fireplace", self.s.call("GET", "/api/me", self.bob["token"])[1])
        self.s.call("POST", "/api/me/messages/read", self.alice["token"], {})
        self.assertTrue(self.me(self.alice)["messages"][0]["read"])

    def test_prewritten_directions(self):
        state = self.s.host("GET", "/api/host")
        alice = next(g for g in state["guests"] if g["id"] == self.alice["id"])
        self.assertEqual([d["id"] for d in alice["directions"]], ["pike-cellar"], "only Pike's own instructions")
        state = self.s.host("POST", f"/api/host/guests/{self.alice['id']}/message",
                            {"request_id": "msg-000002", "direction_id": "pike-cellar"})
        alice = next(g for g in state["guests"] if g["id"] == self.alice["id"])
        self.assertTrue(alice["directions"][0]["sent"])
        self.assertIn("wine cellar", self.me(self.alice)["messages"][0]["body"])
        status, _ = self.s.call("POST", f"/api/host/guests/{self.bob['id']}/message", self.s.host_key,
                                {"request_id": "msg-000003", "direction_id": "pike-cellar"})
        self.assertEqual(status, 400, "Rosalind can't be sent Pike's instructions")

    def test_guests_cannot_send_messages(self):
        status, _ = self.s.call("POST", f"/api/host/guests/{self.bob['id']}/message", self.alice["token"],
                                {"request_id": "msg-000004", "body": "hi"})
        self.assertEqual(status, 403)

    @mock.patch.dict(os.environ, TWILIO_ENV)
    def test_text_alert_never_contains_the_message(self):
        self.s.call("POST", f"/api/host/guests/{self.alice['id']}/contact", self.s.host_key,
                    {"phone": "555 123 4567", "consent": True})
        state = self.s.host("POST", f"/api/host/guests/{self.alice['id']}/message",
                            {"request_id": "msg-000005", "body": "The butler did it.", "also_text": True})
        self.assertIn("texted", state["notice"])
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("butler did it", self.sent[0][1])
        self.assertIn(self.alice["token"], self.sent[0][1])

    # P03-4 Phone updates
    def test_removed_guest_loses_balance_and_messages(self):
        self.s.host("POST", "/api/host/phase", {"phase": "investigation"})
        self.s.host("POST", f"/api/host/guests/{self.bob['id']}/message", {"request_id": "msg-000006", "body": "x"})
        self.s.host("POST", f"/api/host/guests/{self.bob['id']}/remove", {})
        with self.s.db.tx() as t:
            self.assertIsNone(t.one("SELECT * FROM balances WHERE guest_id = ?", (self.bob["id"],)))
            self.assertIsNone(t.one("SELECT * FROM messages WHERE guest_id = ?", (self.bob["id"],)))


if __name__ == "__main__":
    unittest.main()
