"""Checks for prep levels and character chapters.   Run:  py -m unittest -v"""

import json
import os
import shutil
import tempfile
import unittest

from tests.test_p01 import Server
from tests.test_p02 import ALL_ANSWERS


class Chapters(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.s = Server(os.path.join(self.dir, "t.db"))
        self.s.host("POST", "/api/host/guests", {"name": "Light Lou"})
        state = self.s.host("POST", "/api/host/guests", {"name": "Deep Dee"})
        by_name = {g["name"]: g for g in state["guests"]}
        self.lou, self.dee = by_name["Light Lou"], by_name["Deep Dee"]
        for g, prep in ((self.lou, 2), (self.dee, 3)):
            status, text = self.s.call("POST", "/api/me/assessment", g["token"], {
                "answers": dict(ALL_ANSWERS, preparation=prep),
                "limits": {"killer": "ok", "romance": "ok"}, "submit": True})
            self.assertEqual(status, 200, text)
        self.s.host("POST", f"/api/host/guests/{self.lou['id']}/assign", {"character_id": "butler"})
        self.s.host("POST", f"/api/host/guests/{self.dee['id']}/assign", {"character_id": "doctor"})

    def tearDown(self):
        self.s.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def me(self, g):
        return self.s.call("GET", "/api/me", g["token"])[1]

    def test_host_sees_prep_levels(self):
        levels = {g["name"]: g["prep_level"] for g in self.s.host("GET", "/api/host")["guests"]}
        self.assertEqual(levels, {"Light Lou": "light", "Deep Dee": "deep"})

    def test_chapter_hidden_until_release(self):
        self.assertNotIn("PLACEHOLDER CHAPTER", self.me(self.lou))

    def test_each_guest_gets_only_their_own_chapter(self):
        self.s.host("POST", "/api/host/release", {})
        dee = json.loads(self.me(self.dee))["character"]
        self.assertEqual(dee["prep"], "deep")
        self.assertEqual(dee["chapter"], self.s.story["by_id"]["doctor"]["chapter"])
        lou_raw = self.me(self.lou)
        self.assertEqual(json.loads(lou_raw)["character"]["prep"], "light")
        for c in self.s.story["characters"]:
            if c["id"] != "butler":
                self.assertNotIn(c["chapter"][:200], lou_raw)


if __name__ == "__main__":
    unittest.main()
