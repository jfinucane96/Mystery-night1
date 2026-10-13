"""Checks for short character versions (low-prep guests). Uses a fake AI.
Run:  py -m unittest -v"""

import json
import os
import shutil
import tempfile
import unittest

from server import ai_briefs
from tests.test_p01 import Server
from tests.test_p02 import ALL_ANSWERS

LOW_PREP = dict(ALL_ANSWERS, preparation=1)


class Briefs(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.prompts = []

        def fake_ai(sheet, schema):
            self.prompts.append(sheet)
            names = schema["properties"]["relationships"]["items"]["properties"]["name"]["enum"]
            return {"who_you_are": "QUICK: you are someone interesting.",
                    "the_evening": "QUICK: a party goes wrong.",
                    "relationships": [{"name": n, "text": f"QUICK about {n}"} for n in names]}

        self.s = Server(os.path.join(self.dir, "t.db"), ai_ask_brief=fake_ai)
        self.s.host("POST", "/api/host/guests", {"name": "Lowkey Lou"})
        state = self.s.host("POST", "/api/host/guests", {"name": "Keen Kim"})
        self.lou, self.kim = state["guests"]
        for g, answers in ((self.lou, LOW_PREP), (self.kim, ALL_ANSWERS)):
            status, text = self.s.call("POST", "/api/me/assessment", g["token"], {
                "answers": answers, "limits": {"killer": "ok", "romance": "ok"}, "submit": True})
            self.assertEqual(status, 200, text)
        self.s.host("POST", f"/api/host/guests/{self.lou['id']}/assign", {"character_id": "butler"})
        self.s.host("POST", f"/api/host/guests/{self.kim['id']}/assign", {"character_id": "doctor"})

    def tearDown(self):
        self.s.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def row(self, g, state=None):
        state = state or self.s.host("GET", "/api/host")
        return next(x for x in state["guests"] if x["id"] == g["id"])

    def me(self, g):
        return json.loads(self.s.call("GET", "/api/me", g["token"])[1])

    def test_prep_answer_sets_level(self):
        self.assertEqual(self.row(self.lou)["prep_level"], "short")
        self.assertEqual(self.row(self.kim)["prep_level"], "full")

    def test_make_drafts_only_for_low_prep_guests(self):
        state = self.s.host("POST", "/api/host/briefs/make", {})
        self.assertEqual(self.row(self.lou, state)["brief"]["status"], "draft")
        self.assertIsNone(self.row(self.kim, state)["brief"])
        self.assertEqual(len(self.prompts), 1)

    def test_ai_never_sees_secrets_or_objectives(self):
        self.s.host("POST", "/api/host/briefs/make", {})
        butler = self.s.story["by_id"]["butler"]
        for text in butler["secrets"] + butler["objectives"]:
            self.assertNotIn(text, self.prompts[0])

    def test_guest_sees_short_version_only_after_approval(self):
        self.s.host("POST", "/api/host/briefs/make", {})
        self.s.host("POST", "/api/host/release", {})
        self.assertIsNone(self.me(self.lou)["character"]["short"], "draft is not shown")
        self.s.host("POST", f"/api/host/guests/{self.lou['id']}/brief", {"status": "approved"})
        c = self.me(self.lou)["character"]
        self.assertEqual(c["short"]["who_you_are"], "QUICK: you are someone interesting.")
        butler = self.s.story["by_id"]["butler"]
        self.assertEqual(c["secrets"], butler["secrets"], "secrets always word for word")
        self.assertEqual(c["objectives"], butler["objectives"])
        self.assertEqual(c["biography"], butler["biography"], "full details still available")
        self.assertIsNone(self.me(self.kim)["character"]["short"])

    def test_nothing_shown_before_release(self):
        self.s.host("POST", "/api/host/briefs/make", {})
        self.s.host("POST", f"/api/host/guests/{self.lou['id']}/brief", {"status": "approved"})
        self.assertNotIn("QUICK", self.s.call("GET", "/api/me", self.lou["token"])[1])

    def test_changing_role_discards_short_version(self):
        self.s.host("POST", "/api/host/briefs/make", {})
        self.s.host("POST", f"/api/host/guests/{self.lou['id']}/brief", {"status": "approved"})
        self.s.host("POST", f"/api/host/guests/{self.lou['id']}/assign", {"character_id": "painter", "confirm": True})
        self.assertIsNone(self.row(self.lou)["brief"])
        self.s.host("POST", "/api/host/release", {})
        self.assertIsNone(self.me(self.lou)["character"]["short"])

    def test_use_full_version_instead(self):
        self.s.host("POST", f"/api/host/guests/{self.lou['id']}/brief", {"status": "full"})
        self.assertEqual(self.row(self.lou)["brief"]["status"], "full")
        state = self.s.host("POST", "/api/host/briefs/make", {})          # "make all" skips them now
        self.assertEqual(self.row(self.lou, state)["brief"]["status"], "full")
        self.assertEqual(self.s.call("POST", f"/api/host/guests/{self.lou['id']}/brief", self.s.host_key,
                                     {"status": "approved"})[0], 409, "nothing to approve")

    def test_make_again_for_one_guest(self):
        self.s.host("POST", "/api/host/briefs/make", {})
        self.s.host("POST", f"/api/host/guests/{self.lou['id']}/brief", {"status": "approved"})
        state = self.s.host("POST", "/api/host/briefs/make", {"guest_id": self.lou["id"]})
        self.assertEqual(self.row(self.lou, state)["brief"]["status"], "draft", "a new draft needs approving again")

    def test_guests_cannot_make_or_approve(self):
        self.assertEqual(self.s.call("POST", "/api/host/briefs/make", self.lou["token"], {})[0], 403)
        self.assertEqual(self.s.call("POST", f"/api/host/guests/{self.lou['id']}/brief", self.lou["token"],
                                     {"status": "approved"})[0], 403)

    def test_incomplete_ai_answer_is_rejected(self):
        with self.assertRaises(ai_briefs.ai.AIError):
            ai_briefs.make_short(self.s.story, self.s.story["by_id"]["butler"],
                                 ask=lambda sheet, schema: {"who_you_are": "x", "the_evening": "y", "relationships": []})


if __name__ == "__main__":
    unittest.main()
