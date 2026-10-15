"""P04 acceptance checks: private REMEMBER and public INTERROGATE questions,
permitted knowledge only, evidence-gated confessions, tokens charged once and
refunded when unanswered. Uses a fake AI.   Run:  py -m unittest -v"""

import json
import os
import shutil
import tempfile
import unittest

from server import ai
from tests.test_p01 import Server

THORNE_TRUTH = "powder from a glass vial"


class P04(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.mode = "first"        # how the fake AI behaves
        self.prompts = []

        def fake_ai(system, prompt, schema):
            self.prompts.append(prompt)
            props = schema["properties"]
            key = "fact_ids" if "fact_ids" in props else "claim_ids"
            ids = props[key]["items"]["enum"]
            if self.mode == "error":
                raise ai.AIError("AI is down")
            if self.mode == "nomatch":
                return {"matched": False, key: [], "answer": "", "spoken": "", "acting_tip": ""}
            if self.mode == "cheat":
                return {"matched": True, key: ["made-up-fact"], "answer": "INVENTED", "spoken": "INVENTED", "acting_tip": ""}
            if self.mode == "skip-confession":
                pick = [i for i in ids if i != "thorne-poison"][:1]
            else:
                pick = ids[:1]
            return {"matched": True, key: pick, "answer": f"AI MEMORY {pick}", "spoken": f"AI LINE {pick}",
                    "acting_tip": "AI TIP"}

        self.s = Server(os.path.join(self.dir, "t.db"), ai_ask_q=fake_ai)
        names = {}
        for name, char in (("Alice", "butler"), ("Bob", "doctor"), ("Cara", "painter")):
            state = self.s.host("POST", "/api/host/guests", {"name": name})
            g = next(x for x in state["guests"] if x["name"] == name)
            self.s.host("POST", f"/api/host/guests/{g['id']}/assign", {"character_id": char, "confirm": True})
            names[name] = g
        self.alice, self.bob, self.cara = names["Alice"], names["Bob"], names["Cara"]
        self.s.host("POST", "/api/host/release", {})
        self.s.host("POST", "/api/host/phase", {"phase": "investigation"})
        self.n = 0

    def tearDown(self):
        self.s.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def me(self, g):
        return json.loads(self.s.call("GET", "/api/me", g["token"])[1])

    def ask(self, g, kind, question, target=None, evidence="", request_id=None):
        self.n += 1
        body = {"request_id": request_id or f"req-{self.n:06d}", "kind": kind, "question": question,
                "target_guest_id": target["id"] if target else "", "evidence_id": evidence}
        status, text = self.s.call("POST", "/api/me/ask", g["token"], body)
        return status, json.loads(text)

    def latest(self, view):
        return view["investigation"]["notebook"][0]

    # P04-1 Permitted knowledge
    def test_remember_uses_only_own_memories(self):
        status, view = self.ask(self.alice, "remember", "What did I see near the champagne?")
        self.assertEqual(status, 200, view)
        q = self.latest(view)
        self.assertEqual(q["status"], "answered")
        self.assertIn("pike-saw-thorne", q["answer"])
        prompt = self.prompts[-1]
        self.assertIn("Dr. Thorne alone by the champagne tray", prompt)
        for other in ("niece", "doctor", "painter"):
            for f in self.s.story["by_id"][other]["knowledge"]:
                self.assertNotIn(f["text"], prompt, "no other character's facts")
        self.assertNotIn(self.s.story["solution"]["motive"], prompt)

    def test_hearsay_is_marked_as_heard(self):
        self.ask(self.alice, "remember", "What do I know about the will?")
        self.assertIn("Rosalind Ravenscroft told you this; you're not sure it's true", self.prompts[-1])

    def test_later_round_memories_stay_locked(self):
        self.ask(self.cara, "remember", "What about the wine bottles?")
        self.assertNotIn("missing bottles", self.prompts[-1])
        self.s.host("POST", "/api/host/round", {"number": 2})
        self.s.host("POST", "/api/host/grant", {"request_id": "extra-tok-1", "target": self.cara["id"], "public": 0, "private": 1})
        self.ask(self.cara, "remember", "What about the wine bottles?")
        self.assertIn("missing bottles", self.prompts[-1])

    def test_lies_never_reveal_the_truth(self):
        self.ask(self.alice, "interrogate", "Did you go near the champagne?", target=self.bob)
        prompt = self.prompts[-1]
        self.assertNotIn(THORNE_TRUTH, prompt)
        self.assertIn("I never went near the champagne", prompt)
        self.assertNotIn("confessing", prompt)

    # P04-2 Private memory questions
    def test_remember_is_private_and_costs_one_private_token(self):
        self.ask(self.alice, "remember", "What did I see near the champagne?")
        alice = self.me(self.alice)
        self.assertEqual(alice["tokens"], {"public": 2, "private": 0})
        self.assertNotIn("AI MEMORY", self.s.call("GET", "/api/me", self.bob["token"])[1])
        self.assertNotIn("AI MEMORY", self.s.call("GET", "/api/me", self.cara["token"])[1])

    def test_unknown_answers_cost_nothing(self):
        self.mode = "nomatch"
        status, view = self.ask(self.alice, "remember", "What's the capital of France?")
        self.assertEqual(self.latest(view)["status"], "unknown")
        self.assertIn("No token used", self.latest(view)["note"])
        self.assertEqual(view["tokens"]["private"], 1)

    def test_out_of_tokens(self):
        self.ask(self.alice, "remember", "What did I see near the champagne?")
        status, view = self.ask(self.alice, "remember", "What did I see near the champagne?")
        self.assertEqual(status, 409)
        self.assertIn("no private questions left", view["error"])

    # P04-3 Public interrogation
    def test_interrogation_goes_to_the_target(self):
        status, view = self.ask(self.alice, "interrogate", "Where were you during the toast?", target=self.cara)
        self.assertEqual(status, 200, view)
        mine = self.latest(view)
        self.assertEqual((mine["status"], mine["target"]), ("answered", "Cara (Juniper Vale)"))
        self.assertNotIn("AI LINE", json.dumps(mine), "the asker listens; they don't read the script")
        self.assertEqual(view["tokens"], {"public": 1, "private": 1})
        card = self.me(self.cara)["investigation"]["questioned"][0]
        self.assertEqual(card["asker"], "Alice (Mr. Edmund Pike)")
        self.assertTrue(card["spoken"].startswith("AI LINE"))
        self.assertEqual(card["acting_tip"], "AI TIP")
        self.assertNotIn("AI LINE", self.s.call("GET", "/api/me", self.bob["token"])[1])
        self.s.call("POST", f"/api/me/interrogations/{mine['id']}/done", self.cara["token"], {})
        self.assertTrue(self.latest(self.me(self.alice))["answered_aloud"])

    def test_cannot_interrogate_yourself_or_nobody(self):
        self.assertEqual(self.ask(self.alice, "interrogate", "Hello there?", target=self.alice)[0], 400)
        self.assertEqual(self.ask(self.alice, "interrogate", "Hello there?")[0], 400)

    def test_confession_needs_the_right_evidence(self):
        status, view = self.ask(self.alice, "interrogate", "Did you poison her?", target=self.bob, evidence="vial")
        self.assertEqual(status, 400, "Alice doesn't have the vial yet")
        self.s.host("POST", f"/api/host/guests/{self.alice['id']}/evidence", {"evidence_id": "napkin"})
        self.ask(self.alice, "interrogate", "Did you poison her?", target=self.bob, evidence="napkin")
        self.assertNotIn("confessing", self.prompts[-1], "the wrong evidence unlocks nothing")
        self.s.host("POST", f"/api/host/guests/{self.alice['id']}/evidence", {"evidence_id": "vial"})
        self.mode = "skip-confession"          # even if the AI ignores the confession...
        self.ask(self.alice, "interrogate", "Explain this vial!", target=self.bob, evidence="vial")
        card = self.me(self.bob)["investigation"]["questioned"][0]
        self.assertIn("I put it in her glass", card["spoken"], "...the authored confession is used")
        log = self.s.host("GET", "/api/host")["questions"][0]
        self.assertEqual(log["delivery"], "confessing")

    # P04-4 AI and token fallback
    def test_invented_facts_are_rejected(self):
        self.mode = "cheat"
        status, view = self.ask(self.alice, "remember", "What did I see near the champagne?")
        q = self.latest(view)
        self.assertNotIn("INVENTED", json.dumps(q))
        self.assertEqual(q["status"], "unknown")

    def test_ai_outage_uses_authored_text_or_refunds(self):
        self.mode = "error"
        status, view = self.ask(self.alice, "remember", "What did I see near the champagne?")
        q = self.latest(view)
        self.assertEqual(q["status"], "answered")
        self.assertIn("champagne tray", q["answer"])
        status, view = self.ask(self.cara, "remember", "Tell me about the moon landing")
        self.assertEqual(self.latest(view)["status"], "unknown")
        self.assertEqual(view["tokens"]["private"], 1, "refunded")

    def test_repeated_request_charges_once(self):
        self.ask(self.alice, "interrogate", "Where were you?", target=self.cara, request_id="same-req-01")
        status, view = self.ask(self.alice, "interrogate", "Where were you?", target=self.cara, request_id="same-req-01")
        self.assertEqual(status, 200)
        self.assertEqual(view["tokens"]["public"], 1)
        self.assertEqual(len(view["investigation"]["notebook"]), 1)
        self.assertEqual(len(self.prompts), 1, "the AI was asked once")

    def test_no_questions_when_paused_or_outside_investigation(self):
        self.s.host("POST", "/api/host/pause", {"paused": True})
        status, view = self.ask(self.alice, "remember", "What did I see near the champagne?")
        self.assertEqual(status, 409)
        self.assertIn("paused", view["error"])
        self.s.host("POST", "/api/host/pause", {"paused": False})
        self.s.host("POST", "/api/host/phase", {"phase": "accusation"})
        self.assertEqual(self.ask(self.alice, "remember", "What did I see?")[0], 409)
        self.assertEqual(self.me(self.alice)["tokens"]["private"], 1)

    def test_stuck_question_is_refunded(self):
        with self.s.db.tx() as t:
            t.run("UPDATE balances SET private = private - 1 WHERE guest_id = ?", (self.alice["id"],))
            t.run("INSERT INTO questions (id, asker_id, kind, question, status, charged, created_at) "
                  "VALUES ('stuck-req-01', ?, 'remember', 'x', 'pending', 1, '2020-01-01T00:00:00+00:00')",
                  (self.alice["id"],))
        self.ask(self.cara, "remember", "What did I see?")          # any question tidies up
        self.assertEqual(self.me(self.alice)["tokens"]["private"], 1)

    def test_guests_cannot_hand_out_evidence(self):
        status, _ = self.s.call("POST", f"/api/host/guests/{self.alice['id']}/evidence", self.alice["token"],
                                {"evidence_id": "vial"})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
