# @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history)
# @REPLACED-BY: NONE (Mu 2026-2036 history discarded; AUDIT_LEDGER.md §3b)
"""Pure tests for the history backfill's phase 0 inputs (engines/history_backfill/sources.py). No network, no DB."""
import unittest

from engines.history_backfill.sources import plausible_veteran, projection, sum_career


def _team(w, l, pf, pa, t=0):
    return {"wins": w, "losses": l, "ties": t, "points_for": pf, "points_against": pa}


class ProjectionTest(unittest.TestCase):
    def test_strong_team_projects_more_wins_and_keeps_its_record(self):
        standings = {"AAA": _team(5, 1, 180, 100), "BBB": _team(1, 5, 100, 180)}
        sched = {
            "AAA": [{"week": 7, "home": "AAA", "away": "BBB", "final": False}],
            "BBB": [{"week": 7, "home": "AAA", "away": "BBB", "final": False}],
        }
        rows = {r["team"]: r for r in projection(standings, sched)}
        self.assertGreater(rows["AAA"]["proj_wins"], 5.5)
        self.assertLess(rows["BBB"]["proj_wins"], 1.5)
        # one game between them: expected wins add up to the one game played
        self.assertAlmostEqual(rows["AAA"]["proj_wins"] + rows["BBB"]["proj_wins"], 7.0, places=1)
        self.assertEqual(rows["AAA"]["games_left"], 1)

    def test_finished_games_are_not_projected_again(self):
        standings = {"AAA": _team(1, 0, 20, 10)}
        sched = {"AAA": [{"week": 1, "home": "AAA", "away": "BBB", "final": True}]}
        row = projection(standings, sched)[0]
        self.assertEqual(row["games_left"], 0)
        self.assertEqual(row["proj_wins"], 1)


class SumCareerTest(unittest.TestCase):
    def test_sums_renames_and_fg_ranges(self):
        rows = [
            {"season": "2024", "games": "17", "passing_yards": "4000", "def_tackles_solo": "3", "def_tackle_assists": "2",
             "fg_made_20_29": "5", "fg_missed_0_19": "1", "fg_made_50_59": "2", "fg_missed_60_": "1"},
            {"season": "2025", "games": "10", "passing_yards": "NA", "def_sacks": "1.5"},
        ]
        out = sum_career(rows)
        self.assertEqual(out["games_played"], 27)
        self.assertEqual(out["pass_yards"], 4000)
        self.assertEqual(out["def_tackles"], 5)
        self.assertEqual(out["def_sacks"], 1.5)
        self.assertEqual((out["kick_fgm_29"], out["kick_fga_29"]), (5, 6))
        self.assertEqual((out["kick_fgm_50"], out["kick_fga_50"]), (2, 3))
        self.assertEqual((out["first_season"], out["last_season"], out["seasons"]), (2024, 2025, 2))


class PlausibleVeteranTest(unittest.TestCase):
    def test_real_2026_player_needs_ten_more_years(self):
        self.assertTrue(plausible_veteran(13, 3))
        self.assertTrue(plausible_veteran(12, 3))      # one missed season allowed
        self.assertFalse(plausible_veteran(0, 6))      # a 2036 rookie namesake
        self.assertTrue(plausible_veteran(None, 4))    # no 2035/2036 row: can't tell, keep the match


if __name__ == "__main__":
    unittest.main()
