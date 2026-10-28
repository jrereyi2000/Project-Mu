"""pipeline/teleport.py: mapping, pairing, edits and archetype settling (unit, on the stored Madden 27 formulas where a
real formula is needed), and a full dry run against the sample save with the edits applied to a copy (integration)."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import psycopg
import pytest

from engines import ovr_weights as ow
from pipeline import teleport as tp
from pipeline import teleport_contracts as tc
from pipeline.load_save import contract_values

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_SAVE = os.environ.get("MU_SAMPLE_SAVE", os.path.join(ROOT, "CAREER-GIANTS-AUTOSAVE"))
READER = os.path.join(ROOT, "pipeline", "save_reader", "pc_save_reader.mjs")
WRITER = os.path.join(ROOT, "pipeline", "save_reader", "save_writer.mjs")
DOC = ow.load_weights()

QB_RATINGS = {"Awareness": 80, "ThrowPower": 88, "ThrowAccuracyShort": 85, "ThrowAccuracyMid": 82,
              "ThrowAccuracyDeep": 78, "Acceleration": 70, "Speed": 70, "Agility": 70, "Injury": 85, "LongSnap": 10,
              "ThrowOnTheRun": 75, "ThrowUnderPressure": 75, "PlayAction": 75, "BreakSack": 70}


def mu(**kw):
    base = {"player_id": 1, "player_name": "G. Crawford", "full_name": "George Crawford", "height": "6'3\"",
            "weight": 229, "jersey": 10, "team": "CAR", "position": "QB", "archetype": "Strong Arm", "overall": 85,
            "age": 23, "years_pro": 2, "dev_trait": "Superstar X-Factor"}
    base.update(kw)
    base["madden_position"] = tp.TO_MADDEN_POSITION.get(base["position"], base["position"])
    base["madden_archetype"] = tp.madden_archetype(DOC, base["madden_position"], base["archetype"])
    return base


def donor(**kw):
    base = {"pgid": 500, "position": "QB", "archetype": "QB_StrongArm", "overall": 80, "ratings": dict(QB_RATINGS),
            "last_name": "Smith", "commentary_id": 4600}
    base.update(kw)
    return base


class MappingTest(unittest.TestCase):
    def test_archetypes_by_position(self):
        self.assertEqual(tp.madden_archetype(DOC, "C", "Power"), "C_Power")
        self.assertEqual(tp.madden_archetype(DOC, "LG", "• Power"), "G_Power")
        self.assertEqual(tp.madden_archetype(DOC, "CB", "Man To Man"), "CB_MantoMan")
        self.assertEqual(tp.madden_archetype(DOC, "LE", "Speed Rusher"), "DE_SmallerSpeedRusher")
        self.assertIsNone(tp.madden_archetype(DOC, "QB", ""))
        self.assertIsNone(tp.madden_archetype(DOC, "QB", "Power Back"))

    def test_positions(self):
        self.assertEqual(tp.TO_MADDEN_POSITION["LEDG"], "LE")
        self.assertEqual(tp.TO_MADDEN_POSITION["MIKE"], "MLB")
        self.assertEqual(tp.TO_MADDEN_POSITION["WILL"], "LOLB")

    def test_names_and_body(self):
        self.assertEqual(tp.split_name("KC Concepcion Jr", "K. Concepcion Jr"), ("KC", "Concepcion Jr"))
        self.assertEqual(tp.split_name(None, "G. Crawford"), ("G", "Crawford"))
        self.assertEqual(tp.height_inches("6'3\""), 75)
        self.assertIsNone(tp.height_inches(""))
        self.assertEqual(tp.surname_key("Smith III"), tp.surname_key("smith"))


class PairingTest(unittest.TestCase):
    def test_cost_tiers(self):
        m = mu()
        same = tp.pair_cost(m, donor(), DOC)
        other = tp.pair_cost(m, donor(archetype="QB_Scrambler"), DOC)
        far = tp.pair_cost(m, donor(position="WR", archetype="WR_Slot"), DOC)
        self.assertLess(same, tp.OTHER_ARCHETYPE + 30)
        self.assertGreaterEqual(other, tp.OTHER_ARCHETYPE)
        self.assertGreaterEqual(far, tp.ANYWHERE)

    def test_assignment_prefers_position_then_archetype(self):
        qb, wr = mu(), mu(player_id=2, position="WR", archetype="Slot", overall=80)
        donors = [donor(pgid=1, position="WR", archetype="WR_Slot",
                        ratings={"ShortRouteRunning": 80, "Catching": 80, "Agility": 80, "Awareness": 75}),
                  donor(pgid=2)]
        pairs = {m["player_id"]: d["pgid"] for m, d in tp.match_team([qb, wr], donors, DOC)}
        self.assertEqual(pairs, {1: 2, 2: 1})

    def test_more_players_than_donors_leaves_the_rest_unmatched(self):
        self.assertEqual(len(tp.match_team([mu(), mu(player_id=2)], [donor()], DOC)), 1)


class EditTest(unittest.TestCase):
    def test_identity_fields(self):
        edit, notes = tp.player_edit(mu(), donor(), DOC, age_add=1)
        s = edit["set"]
        self.assertEqual(edit["pgid"], 500)
        self.assertEqual((s["FirstName"], s["LastName"], s["Age"], s["YearsPro"], s["JerseyNum"]),
                         ("George", "Crawford", 24, 2, 10))
        self.assertEqual((s["Height"], s["Weight"], s["TraitDevelopment"]), (75, 69, "XFactor"))
        self.assertEqual(s["PLYR_COMMENT"], 0)                                  # Smith's clip would say the wrong name
        self.assertNotIn("Position", s)

    def test_same_surname_keeps_the_commentary_id(self):
        edit, _ = tp.player_edit(mu(full_name="Will Smith"), donor(), DOC)
        self.assertEqual(edit["set"]["PLYR_COMMENT"], 4600)

    def test_ratings_reach_the_target(self):
        edit, notes = tp.player_edit(mu(overall=88), donor(), DOC)
        ratings = {**QB_RATINGS, **{k[:-6]: v for k, v in edit["set"].items() if k.endswith("Rating")}}
        self.assertEqual(ow.predict_ovr(DOC, "QB", ratings, current="QB_StrongArm"), (88, "QB_StrongArm"))
        self.assertNotIn("LongSnapRating", edit["set"])

    def test_position_and_archetype_changes_are_written(self):
        edit, notes = tp.player_edit(mu(archetype="Scrambler"), donor(), DOC)
        self.assertEqual(edit["set"]["PlayerType"], "QB_Scrambler")
        edit, notes = tp.player_edit(mu(position="LT", archetype="Power"), donor(position="RT", archetype="OT_Power"), DOC)
        self.assertEqual(edit["set"]["Position"], "LT")

    def test_write_ovr_sets_the_grades(self):
        edit, _ = tp.player_edit(mu(overall=86), donor(), DOC, write_ovr=True)
        s = edit["set"]
        self.assertEqual((s["OverallRating"], s["PlayerType"]), (86, "QB_StrongArm"))
        self.assertEqual(s["OverallGrade1"], 86)                                # Strong Arm is QB slot 1

    def test_missing_ovr_keeps_the_ratings(self):
        edit, notes = tp.player_edit(mu(overall=None), donor(), DOC)
        self.assertFalse([k for k in edit["set"] if k.endswith("Rating")])
        self.assertTrue(any("no Project Mu OVR" in n for n in notes))


class SettleTest(unittest.TestCase):
    def test_a_rival_archetype_is_brought_back_below_the_target(self):
        doc = {"slots": {"X": ["A", "B"]}, "archetypes": {
            "A": {"terms": {"Speed": [0.5, 0.0], "Catching": [0.5, 0.0]}, "intercept": 0.0, "skills": ["Speed", "Catching"]},
            "B": {"terms": {"Speed": [0.9, 0.0], "Catching": [0.1, 0.0]}, "intercept": 0.0, "skills": ["Speed", "Catching"]}}}
        ratings = {"Speed": 90, "Catching": 60}                      # A = 75, B = 87
        settled, ok = tp.settle_archetype(doc, "X", "A", ratings, 75)
        self.assertTrue(ok)
        grades = ow.predict_grades(doc, "X", settled)
        self.assertEqual(grades["A"], 75)
        self.assertLessEqual(grades["B"], 75)


def _sample_ready():
    return (os.path.exists(SAMPLE_SAVE) and shutil.which("node")
            and os.path.isdir(os.path.join(ROOT, "pipeline", "save_reader", "node_modules")))


@pytest.mark.integration
@unittest.skipUnless(_sample_ready(), "needs the sample save, node and `npm install` in pipeline/save_reader")
class SampleTeleportTest(unittest.TestCase):
    """Teleports madden_test's league_state season onto the sample save and applies the edits to a copy."""

    @classmethod
    def setUpClass(cls):
        out = subprocess.run(["node", READER, SAMPLE_SAVE], capture_output=True, text=True, check=True, timeout=300)
        cls.snapshot = json.loads(out.stdout)
        with psycopg.connect(tp.DSN) as conn:
            cur = conn.cursor()
            cur.execute("SELECT current_season FROM league_state WHERE id = 1")
            cls.season = cur.fetchone()[0]
            cls.roster = tp.project_mu_roster(cur, cls.season)
            cls.contracts = tc.load_contracts(cur, cls.season)
        cls.edits, cls.report = tp.teleport(cls.snapshot, cls.roster, DOC)
        cls.contract_counts = tc.add_contracts(cls.edits, cls.report, cls.contracts, cls.snapshot)

    def test_everyone_is_matched_on_their_own_team(self):
        r = self.report
        self.assertGreater(r["matched"], 1500)
        self.assertLessEqual(len(r["unmatched"]), 5)
        self.assertEqual(r["unknown_teams"], {})
        self.assertEqual(len({e["pgid"] for e in self.edits["players"]}), len(self.edits["players"]))

    def test_edits_apply_and_hit_the_targets(self):
        with tempfile.TemporaryDirectory() as d:
            edits, out = os.path.join(d, "edits.json"), os.path.join(d, "CAREER-TELEPORTED")
            with open(edits, "w") as fh:
                json.dump(self.edits, fh)
            subprocess.run(["node", WRITER, SAMPLE_SAVE, edits, "--out", out], check=True, capture_output=True, timeout=600)
            after = json.loads(subprocess.run(["node", READER, out], capture_output=True, text=True, check=True,
                                              timeout=300).stdout)
        before = {p["pgid"]: p for p in self.snapshot["players"]}
        now = {p["pgid"]: p for p in after["players"]}
        self.assertEqual(after["games"], self.snapshot["games"])
        hit = total = 0
        for pr in self.report["pairs"]:
            p = now[pr["donor_pgid"]]
            self.assertEqual(p["team_index"], before[pr["donor_pgid"]]["team_index"])
            if pr["target_ovr"] is not None and p["position"] in DOC["slots"]:
                total += 1
                hit += ow.predict_ovr(DOC, p["position"], p["ratings"], current=p["archetype"])[0] == pr["target_ovr"]
        self.assertGreater(hit / total, 0.95)
        untouched = set(before) - {pr["donor_pgid"] for pr in self.report["pairs"]}
        self.assertTrue(all(before[k] == now[k] for k in untouched))

        # Contracts: every written one reads back as Project Mu's cap hits; the rest keep the donor's.
        self.assertGreater(self.contract_counts.get("written", 0), 1000)
        player_of = {pr["donor_pgid"]: pr["player_id"] for pr in self.report["pairs"]}
        for e in self.edits["players"]:
            if "contract" not in e:
                self.assertEqual(now[e["pgid"]]["contract"], before[e["pgid"]]["contract"])
                continue
            c, years = self.contracts[player_of[e["pgid"]]]
            got = contract_values(now[e["pgid"]]["contract"], self.season)
            self.assertEqual((got["length"], got["years_remaining"]), (c["length"], c["years_remaining"]))
            for y, want in years.items():
                if self.season <= y < self.season + c["years_remaining"]:
                    self.assertLessEqual(abs(got["years"][y] - want), tc.MONEY_UNIT // 2)
