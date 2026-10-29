# @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (v2-teleport)
# @REPLACED-BY: NONE (fresh 2034 save, no teleport; AUDIT_LEDGER.md §3b)
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
        self.assertEqual(s["PLYR_COMMENT"], 1087)                               # Crawford's commentary ID from lookup
        self.assertNotIn("Position", s)

    def test_unrecorded_surname_gets_zero_commentary_id(self):
        edit, _ = tp.player_edit(mu(full_name="George UnrecordedName"), donor(), DOC)
        self.assertEqual(edit["set"]["PLYR_COMMENT"], 0)

    def test_recorded_surname_gets_the_commentary_id(self):
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


SCANNED = {**QB_RATINGS, "ThrowPower": 95, "ThrowAccuracyShort": 91, "Awareness": 88, "Speed": 81}


def scan(ratings=None, overall=None, season=2036):
    ratings = dict(SCANNED if ratings is None else ratings)
    if overall is None:
        overall = ow.predict_grade(DOC, "QB_StrongArm", ratings)
    return {"season": season, "capture_folder": "2026-10-28_attributes", "overall": overall, "ratings": ratings}


class ScanTest(unittest.TestCase):
    def written(self, edit):
        return {**QB_RATINGS, **{k[:-6]: v for k, v in edit["set"].items()
                                 if k.endswith("Rating") and k != "OverallRating"}}

    def test_scanned_ratings_are_written_as_read(self):
        s = scan()
        edit, notes = tp.player_edit(mu(overall=s["overall"], season=2036, scan=s), donor(), DOC)
        self.assertEqual(self.written(edit), SCANNED)
        self.assertEqual(notes, [])

    def test_the_scan_ovr_wins_over_an_older_project_mu_read_of_the_same_season(self):
        s = scan()
        edit, notes = tp.player_edit(mu(overall=s["overall"] - 2, season=2036, scan=s), donor(), DOC, write_ovr=True)
        self.assertEqual(self.written(edit), SCANNED)
        self.assertEqual(edit["set"]["OverallRating"], s["overall"])
        self.assertTrue(any(n.startswith("scan gap") for n in notes))

    def test_a_later_project_mu_season_moves_the_scan_by_the_ovr_change(self):
        s = scan(overall=90)                                    # shown OVR one off the fit: the change still applies
        before = ow.predict_grade(DOC, "QB_StrongArm", SCANNED)
        edit, notes = tp.player_edit(mu(overall=93, season=2037, scan=s), donor(), DOC)
        self.assertEqual(ow.predict_grade(DOC, "QB_StrongArm", self.written(edit)), before + 3)
        self.assertTrue(any(n.startswith("scan moved") for n in notes))

    def test_write_ovr_keeps_the_scanned_archetype_on_top(self):
        s = scan()
        edit, _ = tp.player_edit(mu(overall=s["overall"], season=2036, scan=s), donor(), DOC, write_ovr=True)
        st = edit["set"]
        self.assertEqual((st["OverallRating"], st["PlayerType"], st["OverallGrade1"]),
                         (s["overall"], "QB_StrongArm", s["overall"]))
        self.assertTrue(all(st[f"OverallGrade{i}"] <= s["overall"] for i in range(5)))

    def test_a_rating_the_list_does_not_show_keeps_the_donor_value(self):
        d = donor(ratings={**QB_RATINGS, "Personality": 40})
        s = scan(ratings={**SCANNED, "NotASaveField": 50})
        edit, _ = tp.player_edit(mu(overall=s["overall"], season=2036, scan=s), d, DOC)
        self.assertNotIn("NotASaveFieldRating", edit["set"])
        self.assertNotIn("PersonalityRating", edit["set"])

    def test_pairing_ignores_the_donor_ovr_for_a_scanned_player(self):
        m = mu(overall=95, scan=scan())
        self.assertEqual(tp.pair_cost(m, donor(), DOC), tp.SAME_ARCHETYPE)
        self.assertEqual(tp.pair_cost(m, donor(archetype="QB_Scrambler"), DOC), tp.OTHER_ARCHETYPE)

    def test_report_counts_scanned_players(self):
        snapshot = {"teams": [{"short_name": "CAR", "team_index": 4}],
                    "players": [{**donor(), "status": "Signed", "team_index": 4, "first_name": "Will",
                                 "appearance": {"commentary_id": 4600}},
                                {**donor(pgid=501), "status": "Signed", "team_index": 4, "first_name": "Sam",
                                 "appearance": {"commentary_id": 4600}}]}
        s = scan()
        roster = [mu(overall=s["overall"] - 1, season=2036), mu(player_id=2, season=2036)]
        _, report = tp.teleport(snapshot, [dict(r) for r in roster], DOC, scans={1: s})
        self.assertEqual(report["scanned"], 1)
        self.assertEqual(report["scan_ovr_minus_project_mu"], {1: 1})
        self.assertEqual(sorted(p["ratings"] for p in report["pairs"]), ["donor", "scan"])


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
