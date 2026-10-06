import itertools
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import assessment, casting, content  # noqa: E402

PKG = content.load_package("lemon-tart", "1.0.0")
ASSESS = content.load_assessment()
TAGS = [t for t in PKG.content_tags]


def player(pid, level="c", boundaries=None, prep=60, assistance="some", stretch="modest"):
    answers = {q["id"]: level for q in ASSESS["questions"]}
    b = {t: "allowed" for t in TAGS}
    b.update(boundaries or {})
    return {"id": pid, "name": pid, "scores": assessment.score(ASSESS, answers), "boundaries": b,
            "preparation_minutes": prep, "assistance": assistance, "stretch_preference": stretch}


class ScoringTests(unittest.TestCase):
    def test_dimension_mean_and_involvement(self):
        answers = {q["id"]: "a" for q in ASSESS["questions"]}
        answers["q01"] = "d"            # spotlight answers: 100 and 0 -> 50
        s = assessment.score(ASSESS, answers)
        self.assertEqual(s["spotlight"], 50)
        self.assertEqual(s["commitment"], 0)
        self.assertEqual(s["involvement"], round(0.3 * 50))

    def test_incomplete_rejected(self):
        with self.assertRaises(ValueError):
            assessment.score(ASSESS, {"q01": "a"})


class CompatibilityTests(unittest.TestCase):
    def test_blocked_tag_excludes(self):
        p = player("p", boundaries={"plays_killer": "blocked"})
        self.assertTrue(casting.hard_conflicts(p, PKG.roles["r_baker"], PKG.content_tags))

    def test_unspecified_sensitive_tag_excludes(self):
        p = player("p", boundaries={"plays_killer": "unspecified"})
        self.assertTrue(casting.hard_conflicts(p, PKG.roles["r_baker"], PKG.content_tags))

    def test_unspecified_non_sensitive_tag_ok(self):
        p = player("p", boundaries={"public_deception": "unspecified"})
        self.assertFalse(casting.hard_conflicts(p, PKG.roles["r_baker"], PKG.content_tags))

    def test_preparation_is_hard(self):
        p = player("p", prep=15, stretch="high")
        self.assertTrue(casting.hard_conflicts(p, PKG.roles["r_baker"], PKG.content_tags))


class AssignmentTests(unittest.TestCase):
    def test_required_role_filled_and_incompatible_avoided(self):
        a = player("alice", level="d")
        b = player("bob", level="b", boundaries={"plays_killer": "blocked"})
        res = casting.recommend([a, b], PKG)
        self.assertEqual(res["problems"], [])
        self.assertEqual(res["assignments"]["alice"], "r_baker")
        self.assertNotEqual(res["assignments"]["bob"], "r_baker")
        self.assertEqual(casting.validate_cast([a, b], res["assignments"], PKG), [])

    def test_impossible_reported(self):
        a = player("alice", boundaries={"plays_killer": "blocked"})
        b = player("bob", boundaries={"plays_killer": "unspecified"})
        res = casting.recommend([a, b], PKG)
        self.assertTrue(any("Required role Ollie Finch can't be cast" in p for p in res["problems"]))

    def test_prep_shortfall_explained(self):
        # Reproduces "Test 1": both guests allow the killer role but offered only 30 minutes.
        a = player("Jonathan", prep=30)
        b = player("Dani", prep=30)
        res = casting.recommend([a, b], PKG)
        self.assertEqual(len(res["problems"]), 1)
        msg = res["problems"][0]
        self.assertIn("Jonathan: needs 40 min preparation; guest offered 30", msg)
        self.assertIn("Dani: needs 40 min preparation; guest offered 30", msg)

    def test_willingness_ordering(self):
        role = PKG.roles["r_baker"]
        def will(**kw):
            p = player("p", **kw)
            cost, _ = casting.fit(p, role)
            return casting.willingness(p, role, PKG.content_tags, cost)
        allowed, unsure, refused, short = (will(), will(boundaries={"plays_killer": "unspecified"}),
                                           will(boundaries={"plays_killer": "blocked"}), will(prep=30))
        self.assertEqual(allowed[1], "fits")
        self.assertEqual((unsure[1], short[1], refused[1]), ("stretch", "stretch", "refused"))
        self.assertGreater(allowed[0], unsure[0])
        self.assertGreater(unsure[0], refused[0])
        self.assertIn("prep 30 of 40 min", short[2])

    def test_force_fills_required_with_most_willing(self):
        keen = player("keen", level="d", prep=30)                                 # only short on prep
        shy = player("shy", level="d", prep=30, boundaries={"plays_killer": "blocked"})
        self.assertTrue(casting.recommend([keen, shy], PKG)["problems"])
        res = casting.recommend([keen, shy], PKG, force=True)
        self.assertEqual(res["problems"], [])
        self.assertEqual(res["assignments"]["keen"], "r_baker")
        self.assertEqual(list(res["forced"]), ["keen"])                          # only one guest overridden
        self.assertEqual(casting.validate_cast([keen, shy], res["assignments"], PKG, res["forced"]), [])
        self.assertTrue(casting.validate_cast([keen, shy], res["assignments"], PKG))   # not without the override

    def test_force_does_not_cover_new_conflicts(self):
        p = player("p", prep=30)
        q = player("q")
        forced = {"p": ["needs 40 min preparation; guest offered 30"]}
        p["boundaries"]["plays_killer"] = "blocked"                               # guest changed an answer later
        problems = casting.validate_cast([p, q], {"p": "r_baker", "q": "r_reporter"}, PKG, forced)
        self.assertTrue(any("No thanks" in x for x in problems))

    def test_hungarian_matches_brute_force(self):
        rng = random.Random(7)
        for _ in range(200):
            n, m = rng.randint(1, 4), rng.randint(4, 6)
            cost = [[rng.randint(0, 50) for _ in range(m)] for _ in range(n)]
            got = casting.hungarian(cost)
            best = min(sum(cost[i][c] for i, c in enumerate(perm))
                       for perm in itertools.permutations(range(m), n))
            self.assertEqual(sum(cost[i][c] for i, c in enumerate(got)), best)
            self.assertEqual(len(set(got)), n)


if __name__ == "__main__":
    unittest.main()
