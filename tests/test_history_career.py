"""Pure tests for the career-totals loader's matching and the remainder engine (docs/HISTORY_BACKFILL_PLAN.md
phase 1). No DB."""
import unittest

from engines.history_backfill.remainder import compute
from pipeline.load_career_totals import column_map, fits, match_rows, settle

RUSH = column_map("rushing")


def _row(name, pos, yards, att, td=0, gp=17):
    return {"NAME": name, "POS": pos, "GP": str(gp), "ATT": str(att), "YARDS": str(yards), "AVG": "4.0",
            "TD": str(td), "AVG G": "50", "FUMB": "0", "BTK": "0", "YAC": "0", "LONG": "40"}


# (player_id, name key, team_id, position, player_name), as load_capture.roster returns
PLAYERS = [(1, "JSMITH", 10, "HB", "J.Smith"), (2, "JSMITH", 20, "HB", "J.Smith"), (3, "AJONES", 10, "HB", "A.Jones")]


class FitsAndSettleTest(unittest.TestCase):
    def test_career_cannot_be_below_the_season(self):
        self.assertTrue(fits({"rush_attempts": 900}, {"rush_attempts": 800}))
        self.assertFalse(fits({"rush_attempts": 700}, {"rush_attempts": 800}))
        self.assertTrue(fits({"rush_attempts": 700}, None))
        self.assertTrue(fits({"rush_yards": -5}, {"rush_yards": 3}))   # yards can go down (kneel-downs): not checked

    def test_settle_needs_exactly_one_assignment(self):
        lines = {1: {"rush_attempts": 250}, 2: {"rush_attempts": 50}}
        # the 150-carry career can only be player 2's (player 1 ran 250 times this season)
        self.assertEqual(settle([{"rush_attempts": 1000}, {"rush_attempts": 150}], [1, 2], lines), {0: 1, 1: 2})
        # both fit both ways: can't tell
        self.assertIsNone(settle([{"rush_attempts": 1000}, {"rush_attempts": 1200}], [1, 2], lines))


class MatchRowsTest(unittest.TestCase):
    def test_unique_name_matches_and_unknown_is_flagged(self):
        got = match_rows("rushing", [_row("A.Jones", "HB", 3000, 700), _row("Z.Nobody", "HB", 10, 3)], RUSH,
                         PLAYERS, {})
        by_name = {r["NAME"]: (pid, flags) for pid, r, _, flags in got}
        self.assertEqual(by_name["A.Jones"], (3, []))
        self.assertEqual(by_name["Z.Nobody"], (None, ["unmatched"]))

    def test_shared_name_settled_by_season_lines(self):
        lines = {1: {"rush_attempts": 250}, 2: {"rush_attempts": 50}}
        got = match_rows("rushing", [_row("J.Smith", "HB", 5000, 1000, td=40, gp=90),
                                     _row("J.Smith", "HB", 600, 150, td=3, gp=20)], RUSH, PLAYERS, lines)
        self.assertEqual(sorted((pid, v["rush_yards"]) for pid, _, v, _ in got), [(1, 5000), (2, 600)])

    def test_team_list_wins_and_the_league_row_is_not_doubled(self):
        scoped = {20: [_row("J.Smith", "HB", 5000, 1000)]}
        got = match_rows("rushing", [_row("J.Smith", "HB", 5000, 1000), _row("J.Smith", "HB", 600, 150, td=3, gp=20)],
                         RUSH, PLAYERS, {}, scoped)
        self.assertEqual(sorted((pid, v["rush_yards"], tuple(f)) for pid, _, v, f in got),
                         [(1, 600, ()), (2, 5000, ("team_list",))])

    def test_games_played_is_kept(self):
        got = match_rows("rushing", [_row("A.Jones", "HB", 3000, 700, gp=80)], RUSH, PLAYERS, {})
        self.assertEqual(got[0][2]["games_played"], 80)


class ComputeTest(unittest.TestCase):
    def test_subtracts_captured_pre2026_and_canon(self):
        career = {7: {"rush_yards": 10000, "rush_btk": 50, "games_played": 150}}
        captured = {7: [{"rush_yards": 1200, "games_played": 17}, {"rush_yards": 1000, "games_played": 17}]}
        canon = {7: [{"rush_yards": 900}]}
        pre = {7: {"rush_yards": "2000", "games_played": "34"}}
        r = compute(career, captured, canon, pre, {7: 2024}, {"rush_yards", "games_played"})[7]
        self.assertEqual(r["mu"]["rush_yards"], 5800)
        self.assertEqual(r["generate"]["rush_yards"], 4900)
        self.assertEqual(r["generate"]["games_played"], 82)
        self.assertEqual(r["open_seasons"], 9)
        self.assertEqual(r["flags"], ["untracked_pre2026"])      # broken tackles have no real-career source
        self.assertEqual(r["untracked"], ["rush_btk"])

    def test_flags(self):
        career = {1: {"rush_yards": 100}, 2: {"rush_yards": 500}, 3: {"rush_yards": 500}}
        captured = {1: [{"rush_yards": 300}], 2: [{"rush_yards": 400}], 3: [{"rush_yards": 500}]}
        r = compute(career, captured, {}, {}, {1: 2030, 2: 2035, 3: 2020}, set())
        self.assertIn("negative", r[1]["flags"])
        self.assertEqual(r[1]["open_seasons"], 5)
        self.assertEqual(r[2]["flags"], ["no_open_seasons"])     # 100 yards left but he arrived in 2035
        self.assertEqual(r[3]["flags"], ["missing_pre2026"])


if __name__ == "__main__":
    unittest.main()
