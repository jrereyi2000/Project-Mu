"""pipeline/teleport_draft.py: prospect matching, DraftPlayer ranks and pick owners (unit, on the stored Madden 27
formulas), and a full run against the sample save with the edits applied to a copy and read back (integration)."""
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
from pipeline import teleport_draft as td

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_SAVE = os.environ.get("MU_SAMPLE_SAVE", os.path.join(ROOT, "CAREER-GIANTS-AUTOSAVE"))
SAVE_READER = os.path.join(ROOT, "pipeline", "save_reader")
DOC = ow.load_weights()

QB = {"Awareness": 60, "ThrowPower": 80, "ThrowAccuracyShort": 70, "ThrowAccuracyMid": 66, "ThrowAccuracyDeep": 62,
      "Acceleration": 75, "Speed": 75, "Agility": 72, "Injury": 85, "LongSnap": 10, "ThrowOnTheRun": 65,
      "ThrowUnderPressure": 60, "PlayAction": 60, "BreakSack": 65}
WR = {"Awareness": 55, "Speed": 90, "Acceleration": 90, "Agility": 88, "Catching": 75, "ShortRouteRunning": 70,
      "MediumRouteRunning": 68, "DeepRouteRunning": 72, "Release": 70, "CatchInTraffic": 65, "SpectacularCatch": 70,
      "Injury": 85}


def prospect(**kw):
    base = {"id": 1, "full_name": "TREY BETHEL", "position": "QB", "college": "WEST VIRGINIA", "height_in": 77,
            "weight": 230, "age": 22, "ovr": 70, "archetype": None, "projected_pick": 1, "true_rank": 1}
    base.update(kw)
    return base


def snap_player(pgid, position, archetype, ratings, overall=65, last="Smith", status="Draft"):
    return {"pgid": pgid, "position": position, "archetype": archetype, "overall": overall, "ratings": dict(ratings),
            "last_name": last, "status": status, "appearance": {"commentary_id": 4600}}


def draft_doc(prospects=(), picks=()):
    teams = [{"row": 0, "team_index": 14, "short_name": "SF"}, {"row": 1, "team_index": 0, "short_name": "CHI"},
             {"row": 7, "team_index": 6, "short_name": "ARI"}, {"row": 18, "team_index": 32, "short_name": "FA"}]
    return {"team_table_id": 5934, "draft_pick_table_id": 4540, "draft_player_table_id": 4135, "teams": teams,
            "prospects": list(prospects), "picks": list(picks)}


def dp_row(pgid, row, initial, true, status="Draft"):
    return {"row": row, "pgid": pgid, "contract_status": status, "draft_position": "QB", "initial_draft_rank": initial,
            "true_overall_ranking": true, "visible": True}


class NameAndPositionTest(unittest.TestCase):
    def test_capitals_become_names(self):
        self.assertEqual(td.prospect_name("TREY BETHEL"), "Trey Bethel")
        self.assertEqual(td.prospect_name("JOHN MCCAIN III"), "John McCain III")
        self.assertEqual(td.prospect_name("DJ SMITH JR"), "D.J. Smith Jr.")

    def test_every_madden_position_has_a_prospect_group(self):
        self.assertEqual(set(DOC["slots"]) - set(td.PROSPECT_POSITION), set())
        self.assertEqual((td.PROSPECT_POSITION["LT"], td.PROSPECT_POSITION["C"], td.PROSPECT_POSITION["ROLB"]),
                         ("OT", "IOL", "OLB"))

    def test_teleport_shape(self):
        m = td.as_teleport_player(prospect(position="LEDG", height_in=75))
        self.assertEqual((m["madden_position"], m["height"], m["years_pro"], m["full_name"]),
                         ("LE", "6'3\"", 0, "Trey Bethel"))


class MatchingTest(unittest.TestCase):
    def test_the_archetype_stays_unless_the_position_moves(self):
        d = snap_player(1, "QB", "QB_Scrambler", QB)
        self.assertEqual(td.best_archetype(DOC, "QB", d)[0], "QB_Scrambler")
        arch, grade = td.best_archetype(DOC, "WR", snap_player(2, "QB", "QB_Scrambler", WR))
        grades = ow.predict_grades(DOC, "WR", WR)
        self.assertEqual(grade, max(grades.values()))
        self.assertEqual(grades[arch], grade)

    def test_cost_tiers(self):
        m = td.as_teleport_player(prospect(position="LT", ovr=None))          # position tiers alone
        same = td.draft_cost(m, snap_player(1, "LT", "OT_Power", QB), DOC)
        near = td.draft_cost(m, snap_player(2, "RT", "OT_Power", QB), DOC)
        far = td.draft_cost(m, snap_player(3, "CB", "CB_Zone", QB), DOC)
        self.assertEqual((same, near, far), (td.SAME, td.NEIGHBOUR, td.ANYWHERE))
        m["overall"] = 70
        d = snap_player(1, "LT", "OT_Power", QB)
        self.assertEqual(td.draft_cost(m, d, DOC), abs(ow.predict_grade(DOC, "OT_Power", QB) - 70))

    def test_assignment_prefers_position(self):
        qb = td.as_teleport_player(prospect())
        wr = td.as_teleport_player(prospect(id=2, position="WR", full_name="A B"))
        pairs = td.match_class([qb, wr], [snap_player(10, "WR", "WR_DeepThreat", WR),
                                          snap_player(11, "QB", "QB_Scrambler", QB)], DOC)
        self.assertEqual({m["prospect_id"]: d["pgid"] for m, d in pairs}, {1: 11, 2: 10})


class DraftClassTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = {"players": [snap_player(10, "QB", "QB_Scrambler", QB), snap_player(11, "WR", "WR_DeepThreat", WR),
                                     snap_player(12, "QB", "QB_Scrambler", QB),
                                     snap_player(13, "QB", "QB_Scrambler", QB, status="Signed")]}
        self.draft = draft_doc(prospects=[dp_row(10, 1, 2, 0), dp_row(11, 2, 0, 1), dp_row(12, 3, 1, 2),
                                          dp_row(13, 4, 3, 3, status="PracticeSquad")])

    def test_prospects_take_over_and_ranks_follow_project_mu(self):
        mus = [prospect(id=1, position="QB", projected_pick=2, true_rank=1, ovr=72),
               prospect(id=2, position="WR", full_name="CARL ROSE", projected_pick=1, true_rank=2, ovr=68)]
        players, records, report = td.draft_class(self.snapshot, self.draft, mus, DOC)
        self.assertEqual((report["matched"], report["madden_prospects"], report["madden_left_over"]), (2, 3, 1))
        by_pgid = {e["pgid"]: e["set"] for e in players}
        self.assertNotIn(13, by_pgid)                                       # a signed player is no prospect
        qb_pgid = next(p for p, s in by_pgid.items() if s["FirstName"] == "Trey")
        ratings = {**QB, **{k[:-6]: v for k, v in by_pgid[qb_pgid].items() if k.endswith("Rating")}}
        self.assertEqual(ow.predict_ovr(DOC, "QB", ratings, current="QB_Scrambler")[0], 72)
        ranks = {r["row"]: r["set"] for r in records}
        row = {10: 1, 11: 2, 12: 3}
        # Board: Carl Rose (pick 1) 0, Trey Bethel (pick 2) 1, the leftover Madden QB 2.
        final = {pgid: {**{"InitialDraftRank": d["initial_draft_rank"], "TrueOverallRanking": d["true_overall_ranking"]},
                        **ranks.get(row[pgid], {})} for pgid, d in ((p["pgid"], p) for p in self.draft["prospects"][:3])}
        self.assertEqual(final[11]["InitialDraftRank"], 0)
        self.assertEqual(final[qb_pgid]["InitialDraftRank"], 1)
        self.assertEqual(final[qb_pgid]["TrueOverallRanking"], 0)
        leftover = ({10, 12} - {qb_pgid}).pop()
        self.assertEqual((final[leftover]["InitialDraftRank"], final[leftover]["TrueOverallRanking"]), (2, 2))
        self.assertTrue(all(r["tableId"] == 4135 and r["table"] == "DraftPlayer" for r in records))

    def test_a_position_move_rewrites_the_draft_position(self):
        mus = [prospect(id=1, position="WR", ovr=70), prospect(id=2, position="WR", ovr=60, projected_pick=2)]
        players, records, _ = td.draft_class(self.snapshot, self.draft, mus, DOC)
        moved = [r for r in records if "DraftPosition" in r["set"]]
        self.assertEqual([r["set"]["DraftPosition"] for r in moved], ["WR"])
        self.assertTrue(any(e["set"].get("Position") == "WR" for e in players))


class PicksTest(unittest.TestCase):
    PICKS = [{"row": 0, "year_offset": 0, "round": 1, "pick_number": 5, "original_team_row": 0, "current_team_row": 0,
              "selected": False},
             {"row": 1, "year_offset": 0, "round": 2, "pick_number": 40, "original_team_row": 1, "current_team_row": 0,
              "selected": False},
             {"row": 2, "year_offset": 1, "round": 1, "pick_number": 3, "original_team_row": 7, "current_team_row": 7,
              "selected": False}]

    def mu(self, season, rnd, orig, owner, overall=None):
        return {"draft_season": season, "round": rnd, "original": orig, "owner": owner, "overall_pick": overall}

    def test_owners_follow_project_mu(self):
        mu = [self.mu(2037, 1, "SF", "CHI"), self.mu(2037, 2, "CHI", "CHI"), self.mu(2038, 1, "AZ", "AZ"),
              self.mu(2039, 1, "SF", "SF")]
        records, report = td.pick_edits(draft_doc(picks=self.PICKS), mu, 2037)
        self.assertEqual(records, [
            {"table": "DraftPick", "tableId": 4540, "row": 0, "set": {"CurrentTeam": {"tableId": 5934, "row": 1}}},
            {"table": "DraftPick", "tableId": 4540, "row": 1, "set": {"CurrentTeam": {"tableId": 5934, "row": 1}}}])
        self.assertEqual((report["matched"], report["owner_changes"]), (3, 2))
        self.assertEqual(report["project_mu_picks_not_in_save"], [])           # 2039 R1 SF: no save pick that year
        self.assertIsInstance(report["pick_numbers"], str)

    def test_missing_rows_and_unknown_owners_are_reported(self):
        records, report = td.pick_edits(draft_doc(picks=self.PICKS), [self.mu(2037, 1, "SF", "XXX")], 2037)
        self.assertEqual(records, [])
        self.assertEqual(report["unknown_teams"], {"XXX": 1})
        self.assertEqual(sorted(report["save_picks_without_project_mu_row"]), ["2037 R2 CHI", "2038 R1 AZ"])

    def test_pick_numbers_only_with_the_full_order(self):
        picks = [{"row": i, "year_offset": 0, "round": i // 32 + 1, "pick_number": i, "original_team_row": 0,
                  "current_team_row": 0, "selected": False} for i in range(1)]
        mu = [self.mu(2037, 1, "SF", "SF", overall=8)]
        _, report = td.pick_edits(draft_doc(picks=picks), mu, 2037)
        self.assertIsInstance(report["pick_numbers"], str)                     # 1 of 224: not a full order
        teams = [{"row": i, "team_index": i, "short_name": f"T{i}"} for i in range(32)]
        picks = [{"row": r * 32 + t, "year_offset": 0, "round": r + 1, "pick_number": r * 32 + t,
                  "original_team_row": t, "current_team_row": t, "selected": False} for r in range(7) for t in range(32)]
        mu = [self.mu(2037, r + 1, f"T{t}", f"T{t}", overall=r * 32 + (31 - t) + 1) for r in range(7) for t in range(32)]
        doc = {**draft_doc(picks=picks), "teams": teams}
        records, report = td.pick_edits(doc, mu, 2037)
        self.assertEqual(report["pick_numbers"], 224)
        self.assertEqual(records[0]["set"], {"PickNumber": 31})


def _sample_ready():
    return (os.path.exists(SAMPLE_SAVE) and shutil.which("node")
            and os.path.isdir(os.path.join(SAVE_READER, "node_modules")))


def _node(script, *args):
    return subprocess.run(["node", os.path.join(SAVE_READER, script), *args], capture_output=True, text=True,
                          check=True, timeout=600).stdout


@pytest.mark.integration
@unittest.skipUnless(_sample_ready(), "needs the sample save, node and `npm install` in pipeline/save_reader")
class SampleDraftTest(unittest.TestCase):
    """Teleports madden_test's coming class and picks onto the sample save, applies the edits to a copy, reads it back."""

    @classmethod
    def setUpClass(cls):
        cls.snapshot = json.loads(_node("pc_save_reader.mjs", SAMPLE_SAVE))
        cls.draft = json.loads(_node("draft_tables.mjs", SAMPLE_SAVE))
        with psycopg.connect(td.DSN) as conn:
            cls.edits, cls.report = td.run(conn.cursor(), cls.snapshot, cls.draft, DOC)
            conn.rollback()
            cur = conn.cursor()
            cls.mu_picks = td.project_mu_picks(cur, cls.report["draft_season"])
        with tempfile.TemporaryDirectory() as d:
            edits, out = os.path.join(d, "edits.json"), os.path.join(d, "CAREER-DRAFT")
            with open(edits, "w") as fh:
                json.dump(cls.edits, fh)
            _node("save_writer.mjs", SAMPLE_SAVE, edits, "--out", out)
            cls.after = json.loads(_node("pc_save_reader.mjs", out))
            cls.draft_after = json.loads(_node("draft_tables.mjs", out))

    def test_the_save_layout(self):
        self.assertEqual(len(self.draft["picks"]), 672)
        self.assertEqual({p["year_offset"] for p in self.draft["picks"]}, {0, 1, 2})
        self.assertEqual({p["round"] for p in self.draft["picks"]}, set(range(1, 8)))
        self.assertGreater(sum(p["contract_status"] == "Draft" for p in self.draft["prospects"]), 350)

    def test_every_project_mu_prospect_lands(self):
        c = self.report["draft_class"]
        self.assertEqual(c["matched"], min(c["project_mu_prospects"], c["madden_prospects"]))
        now = {p["pgid"]: p for p in self.after["players"]}
        hit = total = 0
        for pr in c["pairs"]:
            p = now[pr["donor_pgid"]]
            self.assertEqual((p["first_name"], p["last_name"], p["status"]), (*tp.split_name(pr["name"], None), "Draft"))
            if pr["target_ovr"] is not None and p["position"] in DOC["slots"]:
                total += 1
                hit += ow.predict_ovr(DOC, p["position"], p["ratings"], current=p["archetype"])[0] == pr["target_ovr"]
        self.assertGreater(hit / total, 0.95)
        ranks = [p["initial_draft_rank"] for p in self.draft_after["prospects"] if p["contract_status"] == "Draft"]
        self.assertEqual(sorted(ranks), list(range(len(ranks))))

    def test_pick_owners_match_project_mu(self):
        rows = td.team_rows(self.draft_after)
        abbr = {row: a for a, row in rows.items()}
        mu = {(p["draft_season"], p["round"], p["original"]): p["owner"] for p in self.mu_picks}
        season = self.report["draft_season"]
        for pk in self.draft_after["picks"]:
            key = (season + pk["year_offset"], pk["round"], abbr[pk["original_team_row"]])
            self.assertEqual(abbr[pk["current_team_row"]], mu[key], key)

    def test_nothing_else_changed(self):
        self.assertEqual(self.after["games"], self.snapshot["games"])
        self.assertEqual(self.after["teams"], self.snapshot["teams"])
        touched = {pr["donor_pgid"] for pr in self.report["draft_class"]["pairs"]}
        before = {p["pgid"]: p for p in self.snapshot["players"]}
        self.assertTrue(all(before[p["pgid"]] == p for p in self.after["players"] if p["pgid"] not in touched))
        pick_rows = {r["row"] for r in self.edits["records"] if r["table"] == "DraftPick"}
        old = {p["row"]: p for p in self.draft["picks"]}
        for pk in self.draft_after["picks"]:
            if pk["row"] not in pick_rows:
                self.assertEqual(pk, old[pk["row"]])
