"""pipeline/drift_check.py: the pure comparison fed with fake state, plus the fetch functions on a fake cursor."""
import copy
import unittest

from pipeline import drift_check as dc
from pipeline import load_save as ls

SEASON, WEEK = 2099, 3
CHI, AZ = 100, 101

# 5 years, current year 1: this season's cap hit 1050 + 327 = 1377 units = $13,770,000.
CONTRACT = {"length": 5, "current_year": 1, "salary_by_year": [114, 1050, 1422, 1422, 1571],
            "bonus_by_year": [201, 327, 297, 297, 297], "no_trade_clause": False}


def player(pgid, first, last, team, status="Signed", ratings=None, contract=None):
    return {"pgid": pgid, "first_name": first, "last_name": last, "team_index": team, "status": status,
            "ratings": ratings or {"Speed": 85, "Awareness": 80}, "contract": contract}


def snapshot():
    zero = lambda: {"table_id": 1, "row": 0, "field": "Field_1", "values": {"QB": 0, "LS": 0}}
    return {
        "meta": {"game_year": 27, "reader_version": 4},
        "season": {"calendar_year": SEASON, "franchise_year": 1, "week_type": "RegularSeason", "week_index": 2},
        "teams": [{"team_index": 0, "short_name": "CHI"}, {"team_index": 1, "short_name": "ARI"}],
        "players": [player(1, "Test", "Passer", 0, contract=CONTRACT),
                    player(2, "Test", "Catcher", 0, ratings={"Speed": 90, "Awareness": 70}),
                    player(3, "Free", "Agent", 32, status="FreeAgent")],
        "depth_charts": [{"team_index": 0, "slots": {"QB": [1, None, None, None, None, None],
                                                     "WR": [2, None, None, None, None, None]}}],
        "league_settings": {"sliders": {k: zero() for k in ls.SLIDER_KEYS}, "dev_trait_regression_enabled": None},
    }


def state():
    """Project Mu's stored state matching snapshot() exactly."""
    return {
        "teams": {0: CHI, 1: AZ},
        "player_ids": {1: 11, 2: 12, 3: 13},
        "base_ratings": {11: {"Speed": 85, "Awareness": 80}, 12: {"Speed": 88, "Awareness": 70}},
        "form_overlays": {12: {"Speed": 2}},      # Catcher: base 88 + form 2 = the save's 90
        "contracts": {11: {"team_id": CHI, "length": 5, "years_remaining": 4, "total_value": 69_980_000,
                           "bonus": 14_190_000, "cap_hit": 13_770_000}},
        "rosters": {11: CHI, 12: CHI, 13: None},
        "depth_charts": {CHI: {"source": "MU_COACH", "week": 3, "chart": {"QB": [11], "WR": [12]}}},
    }


class CompareTest(unittest.TestCase):
    def test_no_drift_when_everything_matches(self):
        self.assertEqual(dc.compare(snapshot(), state(), SEASON), {k: [] for k in dc.KINDS})

    def test_rating_drift_includes_form_overlay(self):
        snap = snapshot()
        snap["players"][0]["ratings"]["Speed"] = 87
        st = state()
        st["form_overlays"] = {}                 # without the overlay the catcher's 90 is drift against base 88
        out = dc.compare(snap, st, SEASON)["ratings"]
        self.assertEqual(sorted((e["player_id"], e["field"], e["expected"], e["found"]) for e in out),
                         [(11, "Speed", 85, 87), (12, "Speed", 88, 90)])
        self.assertIn(out[0]["name"], ("Test Passer", "Test Catcher"))

    def test_expected_ratings_clamped(self):
        self.assertEqual(dc.expected_ratings({"Speed": 98, "Awareness": 1}, {"Speed": 4, "Awareness": -3}),
                         {"Speed": 99, "Awareness": 0})

    def test_contract_drift_in_whole_dollars(self):
        snap = snapshot()
        c = copy.deepcopy(CONTRACT)
        c["salary_by_year"][1] = 1100            # +50 units this year = +$500,000
        snap["players"][0]["contract"] = c
        out = dc.compare(snap, state(), SEASON)["contracts"]
        got = {e["field"]: (e["expected"], e["found"]) for e in out}
        self.assertEqual(got, {"total_value": (69_980_000, 70_480_000), "cap_hit": (13_770_000, 14_270_000)})

    def test_money_unit_conversion_matches(self):
        # The DB's whole dollars equal the snapshot's $10,000 units x 10,000: no drift.
        v = ls.contract_values(CONTRACT, SEASON)
        self.assertEqual(v["years"][SEASON], 1377 * ls.MONEY_UNIT)
        self.assertEqual(dc.compare_contracts(snapshot(), state(), SEASON), [])

    def test_contract_released_and_new(self):
        snap = snapshot()
        snap["players"][0]["contract"] = None                  # released in the game
        snap["players"][1]["contract"] = CONTRACT              # signed in the game
        out = dc.compare(snap, state(), SEASON)["contracts"]
        by_pid = {e["player_id"]: e for e in out}
        self.assertEqual(by_pid[11]["field"], "contract")
        self.assertIsNone(by_pid[11]["found"])
        self.assertEqual(by_pid[11]["expected"]["total_value"], 69_980_000)
        self.assertIsNone(by_pid[12]["expected"])
        self.assertEqual(by_pid[12]["found"]["cap_hit"], 13_770_000)

    def test_roster_drift(self):
        snap = snapshot()
        snap["players"][1]["team_index"] = 1                   # traded to AZ in the game
        snap["players"][2].update(status="Signed", team_index=0)   # free agent signed by CHI
        snap["players"].append(player(4, "New", "Guy", 1))     # a player Project Mu doesn't know
        st = state()
        st["rosters"][99] = AZ                                 # stored player missing from the save
        out = dc.compare(snap, st, SEASON)["rosters"]
        got = sorted((e["player_id"] or 0, e["field"], e["expected"], e["found"]) for e in out)
        self.assertEqual(got, [(0, "player", None, AZ), (12, "team_id", CHI, AZ), (13, "team_id", None, CHI),
                               (99, "team_id", AZ, None)])

    def test_depth_chart_drift(self):
        snap = snapshot()
        snap["depth_charts"][0]["slots"]["QB"] = [None, 2, 1, 777, None, None]   # holes dropped; 777 unknown
        out = dc.compare(snap, state(), SEASON)["depth_charts"]
        self.assertEqual(out, [{"team_id": CHI, "field": "QB", "expected": [11], "found": [12, 11, "pgid:777"],
                                "source": "MU_COACH", "week": 3}])

    def test_depth_charts_skipped_for_v3_snapshot(self):
        snap = snapshot()
        del snap["depth_charts"]
        del snap["league_settings"]
        out = dc.compare(snap, state(), SEASON)
        self.assertEqual((out["depth_charts"], out["settings"]), ([], []))

    def test_settings_drift(self):
        snap = snapshot()
        snap["league_settings"]["sliders"]["regression_by_position"]["values"]["LS"] = 100
        out = dc.compare(snap, state(), SEASON)["settings"]
        self.assertEqual(out, [{"slider": "regression_by_position", "field": "LS", "expected": 0, "found": 100}])


class FakeCursor:
    """Answers each SELECT with the rows registered for a substring of its SQL."""
    def __init__(self, answers):
        self.answers, self.rows, self.calls = answers, [], []

    def execute(self, sql, args=None):
        self.calls.append((sql, args))
        self.rows = next((rows for key, rows in self.answers.items() if key in sql), [])

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FetchTest(unittest.TestCase):
    def test_depth_charts_newest_week_and_jr_wins(self):
        rows = [(CHI, 2, "MU_COACH", "QB", 1, 50), (CHI, 3, "MU_COACH", "QB", 1, 51), (CHI, 3, "JR", "QB", 1, 52),
                (CHI, 3, "JR", "QB", 2, 51), (AZ, 1, "MU_COACH", "WR", 1, 60)]
        cur = FakeCursor({"FROM depth_charts": rows})
        out = dc.fetch_depth_charts(cur, SEASON, WEEK)
        self.assertEqual(out[CHI], {"source": "JR", "week": 3, "chart": {"QB": [52, 51]}})
        self.assertEqual(out[AZ], {"source": "MU_COACH", "week": 1, "chart": {"WR": [60]}})
        self.assertEqual(cur.calls[0][1], [SEASON, WEEK])

    def test_diff_end_to_end_on_fake_cursor(self):
        cur = FakeCursor({
            "FROM teams": [("CHI", CHI), ("AZ", AZ)],
            "FROM players": [(1, 11), (2, 12), (3, 13)],
            "FROM player_attributes": [(11, {"Speed": 85, "Awareness": 80}), (12, {"Speed": 88, "Awareness": 70})],
            "FROM player_form": [(12, {"Speed": 2})],
            "FROM player_contracts": [(11, CHI, 5, 4, 69_980_000, 14_190_000, 13_770_000)],
            "FROM player_seasons": [(11, CHI), (12, CHI), (13, None)],
            "FROM depth_charts": [(CHI, 3, "MU_COACH", "QB", 1, 11), (CHI, 3, "MU_COACH", "WR", 1, 12)],
        })
        self.assertEqual(dc.diff(snapshot(), cur, SEASON, WEEK), {k: [] for k in dc.KINDS})
        snap = snapshot()
        snap["players"][0]["ratings"]["Awareness"] = 75
        report = dc.diff(snap, cur, SEASON, WEEK)
        self.assertEqual([(e["field"], e["expected"], e["found"]) for e in report["ratings"]], [("Awareness", 80, 75)])


if __name__ == "__main__":
    unittest.main()
