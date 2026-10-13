"""Checks for texting guests and AI casting suggestions. Uses a fake AI and a
fake texting service, so nothing is sent and nothing costs money.
Run:  py -m unittest -v"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from server import texting
from tests.test_p01 import Server
from tests.test_p02 import ALL_ANSWERS

TWILIO_ENV = {"TWILIO_ACCOUNT_SID": "ACtest", "TWILIO_AUTH_TOKEN": "x", "TWILIO_FROM_NUMBER": "+15550000000"}


class Base(unittest.TestCase):
    ai_answer = None

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.sent = []          # texts the fake service "sent"
        self.fail_for = set()   # phone numbers the fake service refuses
        self.ai_prompts = []

        def fake_send(to, body):
            if to in self.fail_for:
                raise texting.TextError("Twilio refused the text: test failure")
            self.sent.append((to, body))
            return "SMfake"

        def fake_ai(prompt, schema):
            self.ai_prompts.append(prompt)
            return self.ai_answer(schema)

        self.s = Server(os.path.join(self.dir, "t.db"), ai_ask=fake_ai, send_text=fake_send)
        self.s.host("POST", "/api/host/guests", {"name": "Alice Smith"})
        state = self.s.host("POST", "/api/host/guests", {"name": "Bob Jones"})
        self.alice, self.bob = state["guests"]

    def tearDown(self):
        self.s.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def finish(self, guest, limits):
        status, text = self.s.call("POST", "/api/me/assessment", guest["token"],
                                   {"answers": ALL_ANSWERS, "limits": limits, "submit": True})
        self.assertEqual(status, 200, text)

    def contact(self, guest, phone, consent=True, is_host=False):
        return self.s.call("POST", f"/api/host/guests/{guest['id']}/contact", self.s.host_key,
                           {"phone": phone, "consent": consent, "is_host": is_host})

    def row(self, guest, state=None):
        state = state or self.s.host("GET", "/api/host")
        return next(g for g in state["guests"] if g["id"] == guest["id"])


class Contact(Base):
    def test_phone_numbers_are_normalised_and_checked(self):
        self.assertEqual(self.contact(self.alice, "(555) 123-4567")[0], 200)
        self.assertEqual(self.row(self.alice)["phone"], "+15551234567")
        self.assertEqual(self.contact(self.alice, "12345")[0], 400)
        self.contact(self.bob, "555 987 6543", consent=False)
        self.assertFalse(self.row(self.bob)["sms_consent"])

    def test_phone_numbers_never_reach_guests(self):
        self.contact(self.alice, "555 123 4567")
        self.contact(self.bob, "555 987 6543")
        raw = self.s.call("GET", "/api/me", self.bob["token"])[1]
        self.assertNotIn("5551234567", raw)
        self.assertNotIn("5559876543", raw)

    def test_host_cannot_be_the_killer(self):
        self.finish(self.alice, {"killer": "ok", "romance": "ok"})
        self.contact(self.alice, "", is_host=True)
        status, text = self.s.call("POST", f"/api/host/guests/{self.alice['id']}/assign", self.s.host_key,
                                   {"character_id": "doctor", "confirm": True})
        self.assertEqual(status, 409)
        self.assertIn("host", text)

    def test_only_one_guest_is_the_host(self):
        self.contact(self.alice, "", is_host=True)
        self.contact(self.bob, "", is_host=True)
        state = self.s.host("GET", "/api/host")
        self.assertEqual([self.row(self.alice, state)["is_host"], self.row(self.bob, state)["is_host"]], [False, True])


class ManualTexting(Base):
    def test_without_twilio_host_gets_prewritten_texts(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in TWILIO_ENV:
                os.environ.pop(k, None)
            self.contact(self.alice, "555 123 4567")
            state = self.s.host("GET", "/api/host")
            self.assertFalse(state["texting_automatic"])
            msg = self.row(self.alice, state)["messages"]["invite"]
            self.assertIn("Hi Alice!", msg)
            self.assertIn(f"/play/{self.alice['token']}", msg)
            self.assertEqual(self.s.call("POST", f"/api/host/guests/{self.alice['id']}/text-invite",
                                         self.s.host_key, {})[0], 409)
            self.s.host("POST", "/api/host/release", {})
            self.assertEqual(self.sent, [])


@mock.patch.dict(os.environ, TWILIO_ENV)
class AutomaticTexting(Base):
    def test_invite_needs_number_and_consent(self):
        url = f"/api/host/guests/{self.alice['id']}/text-invite"
        self.assertEqual(self.s.call("POST", url, self.s.host_key, {})[0], 400)
        self.contact(self.alice, "555 123 4567", consent=False)
        self.assertEqual(self.s.call("POST", url, self.s.host_key, {})[0], 400)
        self.contact(self.alice, "555 123 4567", consent=True)
        state = self.s.host("POST", url, {})
        self.assertEqual(self.sent[0][0], "+15551234567")
        self.assertIn(self.alice["token"], self.sent[0][1])
        self.assertEqual(self.row(self.alice, state)["texts"]["invite"]["status"], "sent")
        self.assertEqual(self.s.call("POST", url, self.bob["token"], {})[0], 403)

    def test_release_texts_each_cast_guest_once(self):
        self.contact(self.alice, "555 123 4567")
        self.contact(self.bob, "555 987 6543")
        self.s.host("POST", f"/api/host/guests/{self.alice['id']}/assign", {"character_id": "butler"})
        state = self.s.host("POST", "/api/host/release", {})
        self.assertEqual([to for to, _ in self.sent], ["+15551234567"], "Bob has no character: no text")
        self.assertIn("Texted 1 guest", state["notice"])
        self.s.host("POST", "/api/host/release", {})          # pressing again: no repeat texts
        self.assertEqual(len(self.sent), 1)
        self.s.host("POST", "/api/host/unrelease", {})        # a genuinely new release texts again
        self.s.host("POST", "/api/host/release", {})
        self.assertEqual(len(self.sent), 2)

    def test_failed_text_is_reported_and_release_still_happens(self):
        self.contact(self.alice, "555 123 4567")
        self.fail_for.add("+15551234567")
        self.s.host("POST", f"/api/host/guests/{self.alice['id']}/assign", {"character_id": "butler"})
        state = self.s.host("POST", "/api/host/release", {})
        self.assertTrue(state["released"])
        self.assertIn("failed", state["error_detail"])
        self.assertEqual(self.row(self.alice, state)["texts"]["release"]["status"], "failed")


class AICasting(Base):
    def setUp(self):
        super().setUp()
        a, b = self.alice["id"], self.bob["id"]
        # Deliberately includes rule-breaking suggestions the server must drop
        self.ai_answer = lambda schema: {
            "summary": "Alice leads, Bob supports.",
            "cast": [{"guest_id": a, "role_id": "doctor", "reason": "Bold answers."},
                     {"guest_id": b, "role_id": "doctor", "reason": "Duplicate role."}],
            "top_picks": [{"guest_id": a, "picks": [{"role_id": "doctor", "reason": "x"},
                                                   {"role_id": "niece", "reason": "y"}]},
                          {"guest_id": b, "picks": [{"role_id": "doctor", "reason": "said no"},
                                                   {"role_id": "butler", "reason": "z"}]}]}
        self.finish(self.alice, {"killer": "ok", "romance": "unsure"})
        self.finish(self.bob, {"killer": "no", "romance": "ok"})

    def test_suggestions_follow_the_rules(self):
        state = self.s.host("POST", "/api/host/suggest", {})
        sug = state["suggestion"]
        self.assertEqual(sug["summary"], "Alice leads, Bob supports.")
        self.assertEqual([(r["guest_id"], r["role_id"]) for r in sug["cast"]], [(self.alice["id"], "doctor")],
                         "Bob's duplicate/refused role is dropped")
        self.assertEqual([p["role_id"] for p in sug["top_picks"][self.bob["id"]]], ["butler"])
        self.assertIsNone(self.row(self.alice, state)["character_id"], "suggestions don't assign anything")

    def test_ai_never_sees_secrets_or_the_solution(self):
        self.s.host("POST", "/api/host/suggest", {})
        prompt = self.ai_prompts[0]
        for c in self.s.story["characters"]:
            for text in c["secrets"] + c["objectives"]:
                self.assertNotIn(text, prompt)
        self.assertNotIn(self.s.story["solution"]["motive"], prompt)
        self.assertNotIn("Smith", prompt, "first names only")

    def test_apply_whole_plan(self):
        self.s.host("POST", "/api/host/suggest", {})
        status, text = self.s.call("POST", "/api/host/suggest/apply", self.s.host_key, {})
        self.assertEqual(status, 200, text)
        self.assertEqual(self.row(self.alice)["character_id"], "doctor")

    def test_suggestion_survives_refresh_and_guests_cannot_ask(self):
        self.s.host("POST", "/api/host/suggest", {})
        self.assertIsNotNone(self.s.host("GET", "/api/host")["suggestion"])
        self.assertEqual(self.s.call("POST", "/api/host/suggest", self.alice["token"], {})[0], 403)
        self.assertNotIn("Alice leads", self.s.call("GET", "/api/me", self.alice["token"])[1])

    def test_ai_failure_is_explained(self):
        from server.ai_casting import AIError

        def broken(schema):
            raise AIError("Your Anthropic account is out of credit.")
        self.ai_answer = broken
        status, text = self.s.call("POST", "/api/host/suggest", self.s.host_key, {})
        self.assertEqual(status, 502)
        self.assertIn("out of credit", text)


if __name__ == "__main__":
    unittest.main()
