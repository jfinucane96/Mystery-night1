"""The Hollow Order (v2 build sheet): rules engine, case validation, and a complete
simulated eight-player evening over the real HTTP API.   Run:  py -m unittest -v"""

import json
import os
import shutil
import tempfile
import threading
import unittest

from server import app, casting, db as dbmod, hollow_content as hc, hollow_engine as eng, story as storymod
from tests.test_p01 import Server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_STORY = os.path.join(ROOT, "content", "story.json")
TOTAL = 12_000_000


class Engine(unittest.TestCase):
    FAMS = {"a": {"members": ["a1", "a2"]}, "b": {"members": ["b1", "b2"]},
            "c": {"members": ["c1", "c2"]}, "d": {"members": ["d1", "d2"]}}
    ORDER = ["a1", "a2", "b1", "b2", "c1", "c2", "d1", "d2"]
    FAMILY_OF = {m: f for f, v in FAMS.items() for m in v["members"]}

    def test_split_is_exact_and_handles_zero_and_negative_weights(self):
        self.assertEqual(sum(eng.split(TOTAL, {"a": 7, "b": 3, "c": 1}, ["a", "b", "c"]).values()), TOTAL)
        self.assertEqual(eng.split(TOTAL, {"a": -5, "b": 0, "c": 0}, ["a", "b", "c"]), {"a": 4000000, "b": 4000000, "c": 4000000})
        self.assertEqual(eng.split(10, {"a": 1, "b": 1, "c": 1}, ["a", "b", "c"]), {"a": 4, "b": 3, "c": 3})

    def test_suspicion_costs_influence(self):
        infl = eng.influence([["a1", "b1", "c1"]], [["a1", "d1", "d2"]], self.ORDER)
        self.assertEqual((infl["a1"], infl["b1"], infl["d1"]), (0, 2, -2))

    def test_transfers_void_if_either_family_is_eliminated(self):
        shares = {"a": 4_000_000, "b": 4_000_000, "c": 4_000_000}
        out, settled = eng.apply_transfers(shares, [("a", "d", 500_000), ("d", "a", 500_000), ("a", "b", 500_000)], {"a", "b", "c"})
        self.assertEqual(out, {"a": 3_500_000, "b": 4_500_000, "c": 4_000_000})
        self.assertEqual(settled, [("a", "b", 500_000)])

    def test_overcommitted_payer_is_scaled_never_negative(self):
        out, _ = eng.apply_transfers({"a": 1_000_000, "b": 11_000_000}, [("a", "b", 3_000_000), ("a", "b", 1_000_000)], {"a", "b"})
        self.assertEqual(out["a"], 0)
        self.assertEqual(sum(out.values()), 12_000_000)

    def test_settlement_always_totals_twelve_million(self):
        infl = {c: i for i, c in enumerate(self.ORDER)}
        wagers = {"a1": ("guilty", 900_000), "b1": ("not_guilty", 1_000_000), "c1": ("guilty", 100_000), "d1": ("guilty", 5)}
        r = eng.settle(TOTAL, self.FAMS, infl, "d", [("a", "b", 2_000_000), ("c", "d", 1_000_000)], wagers,
                       self.FAMILY_OF, True, self.ORDER)
        self.assertEqual(sum(r["payouts"].values()), TOTAL)
        self.assertEqual(r["payouts"]["d"], 0)
        self.assertEqual(r["wagers"]["pot"], 1_000_000)          # only b1 lost; d1's wager is void
        self.assertEqual(sum(r["wagers"]["won"].values()), 1_000_000)

    def test_wagers_with_no_winners_are_returned(self):
        r = eng.settle(TOTAL, self.FAMS, {}, "a", [], {"b1": ("guilty", 100_000)}, self.FAMILY_OF, False, self.ORDER)
        self.assertEqual(r["payouts"], {"a": 0, "b": 4_000_000, "c": 4_000_000, "d": 4_000_000})

    def test_four_of_eight_convicts_otherwise_lowest_influence_goes(self):
        fi = {"a": 5, "b": -2, "c": 9, "d": -2}
        self.assertEqual(eng.eliminate(4, "c1", self.FAMILY_OF, fi, list(self.FAMS)), ("c1", "c"))
        self.assertEqual(eng.eliminate(3, "c1", self.FAMILY_OF, fi, list(self.FAMS)), (None, "b"))


class Content(unittest.TestCase):
    def test_every_murderer_has_a_valid_fair_case(self):
        data = hc.load()
        story = storymod.load(REAL_STORY)
        self.assertEqual(hc.check(data), [])
        for culprit in data["order"]:
            case = hc.build(data, story, culprit)
            # The public evidence by the bridge singles out the murderer on BOTH routes
            for keys in (("feet", "sleeve", "scent"), ("hand", "side", "sigil")):
                tell = {"boots": "studded with hobnails", "slippers": "without a single nail"}
                words = [tell.get(v, data["words"][v]) for v in
                         (data["by_id"][culprit]["traits"][k] for k in keys)]
                public = " ".join(p["text"] for p in case["public"].values() if p["id"] not in ("nanny", "register"))
                for w in words:
                    self.assertIn(w, public, (culprit, w))
            # Innocent witnesses only; the decoy is cleared by the medical evidence
            self.assertNotIn(culprit, case["witnesses"].values())
            self.assertIn(data["by_id"][case["decoy"]]["traits"]["hand"], case["public"]["dagger_found"]["text"])

    def test_witness_network_is_one_to_one(self):
        data = hc.load()
        net = data["witness_network"]
        self.assertEqual(len({w["witness"] for w in net}), 8)
        self.assertEqual(len({w["subject"] for w in net}), 8)
        spouses = sum(1 for w in net if data["family_of"][w["witness"]] == data["family_of"][w["subject"]])
        self.assertGreaterEqual(spouses, 1)


class Evening(unittest.TestCase):
    """A whole party with eight simulated players."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.s = Server.__new__(Server)
        self.s.story = storymod.load(REAL_STORY)
        self.s.assessment = casting.load()
        self.data = hc.load()
        self.s.db = dbmod.Database(sqlite_path=os.path.join(self.dir, "t.db"))
        self.s.httpd, self.s.host_key = app.make_server(self.s.db, self.s.story, self.s.assessment, "127.0.0.1", 0,
                                                        hollow_data=self.data)
        self.s.base = f"http://127.0.0.1:{self.s.httpd.server_address[1]}"
        threading.Thread(target=self.s.httpd.serve_forever, daemon=True).start()
        self.p = {}
        for cid in self.data["order"]:
            state = self.s.host("POST", "/api/host/guests", {"name": f"Player {cid}"})
            g = next(x for x in state["guests"] if x["name"] == f"Player {cid}")
            self.s.host("POST", f"/api/host/guests/{g['id']}/assign", {"character_id": cid, "confirm": True})
            self.p[cid] = g["token"]
        self.s.host("POST", "/api/host/release", {})

    def tearDown(self):
        self.s.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def me(self, cid):
        status, text = self.s.call("GET", "/api/me", self.p[cid])
        self.assertEqual(status, 200)
        return json.loads(text)["hollow"]

    def act(self, cid, action, body, expect=200):
        status, text = self.s.call("POST", f"/api/me/hollow/{action}", self.p[cid], body)
        self.assertEqual(status, expect, text)
        return json.loads(text)

    def step(self, name):
        return self.s.host("POST", "/api/host/hollow/step", {"step": name})

    def room(self):
        key = self.s.host("GET", "/api/host")["room_key"]
        status, text = self.s.call("GET", "/api/room", key)
        return json.loads(text)["hollow"]

    def culprit(self):
        found = [c for c in self.data["order"] if self.me(c)["dossier"].get("truth")]
        self.assertEqual(len(found), 1)
        return found[0]

    def others(self, cid, n=3):
        """The next n players round the table: spreads votes evenly."""
        o = self.data["order"]
        i = o.index(cid)
        return [o[(i + k) % len(o)] for k in range(1, n + 1)]

    def test_the_host_and_room_never_learn_the_murderer(self):
        culprit = self.culprit()
        host = json.dumps(self.s.host("GET", "/api/host")["hollow"])
        room = json.dumps(self.room())
        for raw in (host, room):
            self.assertNotIn("culprit", raw)
            self.assertNotIn("terrible truth", raw)
            self.assertNotIn("It was you", raw)
        # nobody else's phone holds the truth, the reconstruction or another player's secret
        for c in self.data["order"]:
            raw = json.dumps(self.me(c))
            self.assertNotIn("It was you", raw)
            for other in self.data["order"]:
                if other != c:
                    self.assertNotIn(self.data["by_id"][other]["secret"]["text"], raw)
        self.assertTrue(self.me(culprit)["dossier"]["truth"]["tonight"])

    def test_released_before_this_version_still_gets_a_secret_murderer(self):
        with self.s.db.tx() as t:
            t.run("DELETE FROM hollow_state")
        self.assertTrue(self.s.host("GET", "/api/host")["hollow"]["ready"])
        self.culprit()

    def test_voice_is_room_only_and_falls_back_when_not_set_up(self):
        key = self.s.host("GET", "/api/host")["room_key"]
        self.assertEqual(self.s.call("POST", "/api/room/voice", self.p["grant"], {"text": "Hello"})[0], 403)
        self.assertEqual(self.s.call("POST", "/api/room/voice", key, {"text": "Hello"})[0], 503)

    def test_steps_move_one_at_a_time_and_late_writes_are_refused(self):
        self.assertEqual(self.s.call("POST", "/api/host/hollow/step", self.s.host_key, {"step": "covenant"})[0], 409)
        for st in ("reunion", "polls", "poll_results", "covenant", "covenant2", "ballot1"):
            self.step(st)
        self.act("grant", "ballot", {"kind": "inheritance", "round": 1, "picks": ["caroline", "nanor", "taylor"]})
        self.act("grant", "ballot", {"kind": "inheritance", "round": 1, "picks": ["grant", "nanor", "taylor"]}, 400)
        self.step("blackout")
        self.act("grant", "ballot", {"kind": "inheritance", "round": 1, "picks": ["caroline", "nanor", "taylor"]}, 409)

    def test_a_complete_evening(self):
        order = self.data["order"]
        culprit = self.culprit()
        self.step("reunion")
        self.assertEqual(len(self.me("grant")["memories"]), 1)
        self.step("polls")
        for c in order:
            self.act(c, "poll", {"poll_id": "p1", "choice": "griff" if c != "griff" else "grant"})
        self.act("grant", "polls", {"answers": {"p1": "griff"}}, 400)
        self.act("grant", "polls", {"answers": {p["id"]: "griff" for p in self.data["polls"]}})
        self.step("poll_results")
        self.assertEqual(self.room()["polls"][0]["winners"], ["Griff"])
        self.step("covenant")
        self.assertEqual(len(self.me("grant")["memories"]), 2)
        self.assertEqual([e["title"] for e in self.me("grant")["evidence"]], ["What Silas revealed at the robing ceremony"])
        self.assertTrue(self.room()["screen"].startswith("THE CEREMONY OF THE COVENANT"))
        self.step("covenant2")
        self.step("ballot1")
        for c in order:
            self.act(c, "ballot", {"kind": "inheritance", "round": 1, "picks": self.others(c)})
        self.assertNotIn("families", self.room()["boards"])           # open ballots don't move the board
        self.step("blackout")
        self.step("murder")
        me = self.me("grace")
        self.assertIn("Grant", me["witnessed"])
        self.assertTrue(me["blackout"])
        self.assertEqual(self.room()["title"], "THE HOLLOW ORDER SEES ALL")
        self.step("ghost")
        self.step("ballots2")
        for c in order:
            self.act(c, "ballot", {"kind": "inheritance", "round": 2, "picks": self.others(c)})
            self.act(c, "ballot", {"kind": "suspicion", "round": 1, "picks": self.others(c)[::-1]})
        self.step("deals")
        boards = self.room()["boards"]
        self.assertEqual(len(boards["families"]), 4)
        self.assertTrue(all(isinstance(n, str) for n in boards["suspicion"]))   # names only, no totals
        # Protect Me: Taylor pays Griff not to interrogate her. Force: Grace pays Jonathan to ask Grant s1.
        self.act("taylor", "offer", {"kind": "protect", "recipient": "griff", "amount": 500_000})
        self.act("grace", "offer", {"kind": "force", "recipient": "jonathan", "amount": 300_000,
                                    "terms": {"target": "grant", "track": "silas", "qid": "s1"}})
        self.act("nanor", "offer", {"kind": "protect", "recipient": "grant", "amount": 99_000_000}, 400)
        deals = {d["kind"]: d for d in self.me("griff")["deals"]}
        self.act("griff", "decide", {"id": deals["protect"]["id"], "accept": True})
        force = next(d for d in self.me("jonathan")["deals"] if d["kind"] == "force")
        self.act("jonathan", "decide", {"id": force["id"], "accept": True})
        self.step("interrogations")
        tried_reserved = False
        for _ in order:
            asker = next(c for c in order if self.me(c)["action"].get("my_turn"))
            if asker == "jonathan":
                target, track, qid = "grant", "silas", "s1"
            elif asker == "griff":
                target, track, qid = "taylor", "fire", "f4"         # breaks the protection deal
            else:
                k = order.index(asker)
                target = next(order[(k + d) % 8] for d in range(1, 8) if order[(k + d) % 8] != "grant")
                track, qid = "fire", f"f{k % 5 + 1}"
                if not tried_reserved:                               # Jonathan's reserved question is off limits
                    self.act(asker, "interrogate", {"target": "grant", "track": "silas", "qid": "s1"}, 409)
                    tried_reserved = True
            self.act(asker, "interrogate", {"target": target, "track": track, "qid": qid})
            self.act(asker, "interrogate", {"target": target, "track": track, "qid": qid}, 409)
            q = self.me(target)["action"]["questioned"]
            self.assertTrue(q["truth"] and q["lie"])
            self.act(target, "answer", {"choice": "lie" if target == culprit else "truth"})
        self.assertTrue(self.me(order[0])["action"]["done"])
        self.step("investigations")
        status = {d["kind"]: d["status"] for d in self.me("griff")["deals"]}
        self.assertEqual(status["protect"], "broken")
        self.assertEqual(next(d for d in self.me("jonathan")["deals"] if d["kind"] == "force")["status"], "honored")
        # Blackthornes investigate Nanor and get paid to drop it; Pemberwicks investigate Griff and see the clue.
        self.act("grant", "investigate", {"target": "nanor", "category": "physical"})
        self.act("nanor", "inv_target", {"family": "blackthorne", "action": "offer", "amount": 200_000})
        self.act("caroline", "inv_decide", {"action": "counter", "amount": 400_000})
        self.act("nanor", "inv_target", {"family": "blackthorne", "action": "accept"})
        self.assertEqual(self.me("grant")["clues"], [])
        self.act("jonathan", "investigate", {"target": "griff", "category": "motive"})
        self.act("griff", "inv_target", {"family": "pemberwick", "action": "decline"})
        self.assertIn("Okafor", self.me("danielle")["clues"][0]["text"])
        self.act("danielle", "investigate", {"target": "grace", "category": "motive"}, 409)
        self.step("bribes4")
        self.act("nanor", "offer", {"kind": "bribe", "recipient": "griff", "amount": 100_000,
                                    "terms": {"subject": "nanor", "position": 1}})
        self.act("griff", "decide", {"id": self.me("griff")["deals"][-1]["id"], "accept": True})
        self.step("ballots3")
        for c in order:
            picks = ["nanor", "grace", "grant"] if c == "griff" else self.others(c)
            self.act(c, "ballot", {"kind": "inheritance", "round": 3, "picks": picks})
            self.act(c, "ballot", {"kind": "suspicion", "round": 2, "picks": self.others(c)[::-1]})
        self.step("revelations")
        self.assertEqual(self.me("griff")["deals"][-1]["status"], "honored")
        room = self.room()
        tb = {r["name"]: r["status"] for r in room["truth_board"]}
        self.assertIn("LIED", tb.values())
        led = {r["name"]: r for r in room["ledger"]}
        self.assertEqual(led["Nanor"]["paid"], 500_000)                  # 400k hush money + 100k honoured bribe
        self.assertEqual(led["Taylor"]["paid"], 0)                       # broken deals never appear
        for st in ("confront", "temptation", "ballot4"):
            self.step(st)
        for c in order:
            self.act(c, "ballot", {"kind": "inheritance", "round": 4, "picks": self.others(c)})
        self.step("hotseat")
        pair = self.me(order[0])["action"]["pair_ids"]
        self.assertEqual(len(pair), 2)
        self.act(pair[0], "hotseat", {"choice": "defend"})
        self.assertEqual(self.room()["hotseat"]["cards"], [])               # nothing shown until both choose
        target = next(c for c in order if c not in pair)
        self.act(pair[1], "hotseat", {"choice": "accuse", "target": target})
        self.assertEqual(len(self.room()["hotseat"]["cards"]), 2)
        self.step("suspicion3")
        for c in order:
            picks = [culprit] + [x for x in self.others(c, 4) if x != culprit][:2] if c != culprit else self.others(c)
            self.act(c, "ballot", {"kind": "suspicion", "round": 3, "picks": picks})
        self.step("bridge")
        self.assertEqual(len(self.me("grant")["memories"]), 3)
        self.step("wagers")
        self.act("grant", "wager", {"side": "guilty", "amount": 100_000})
        self.act("caroline", "wager", {"side": "not_guilty", "amount": 50_000_000}, 409)
        self.step("verdict")
        for i, c in enumerate(order):
            self.act(c, "vote", {"yes": i < 5})
        self.act(order[0], "vote", {"yes": False}, 409)
        self.step("reveal")
        r = self.room()["reveal"]
        self.assertEqual(sum(p["amount"] for p in r["payouts"]), TOTAL)
        self.assertTrue(r["reconstruction"][0].endswith("It was you."))
        eliminated = [p for p in r["payouts"] if p["amount"] == 0]
        self.assertEqual(len(eliminated), 1)
        self.assertIn("It was you", json.dumps(self.room()))   # the truth is public only now
