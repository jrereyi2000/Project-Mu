"""Coach career totals and tendency wording (engines/coach_profile.py). No database required.

Run: python3 -m unittest tests.test_coach_profile
"""
import unittest

from engines.coach_profile import (accumulate, describe_tendencies, effective_tendencies, record_line,
                                   season_record, staff_matchup)

SNAP = {"season": 2034, "career_wins": 13, "career_losses": 4, "career_ties": 0, "winning_seasons": 1,
        "playoff_wins": 3, "playoff_losses": 0, "super_bowl_wins": 1, "super_bowl_losses": 0}
CAR, NYJ, ATL = 11, 18, 25


def game(season, week, home, away, hs, as_, game_type="REGULAR_SEASON"):
    return {"season": season, "week": week, "game_type": game_type, "home_team_id": home, "away_team_id": away,
            "home_score": hs, "away_score": as_}


class CoachProfileTests(unittest.TestCase):
    def test_adds_later_games_to_the_snapshot(self):
        tenures = [{"team_id": CAR, "start_season": 2034, "end_season": None}]
        games = [game(2035, 1, CAR, NYJ, 24, 10), game(2035, 2, ATL, CAR, 20, 17), game(2035, 3, CAR, ATL, 13, 13),
                 game(2034, 23, CAR, NYJ, 48, 27, "SUPER_BOWL"),   # already in the snapshot
                 game(2035, 4, NYJ, ATL, 30, 3)]                   # not his team
        t = accumulate(SNAP, tenures, games, 2035)
        self.assertEqual((t["career_wins"], t["career_losses"], t["career_ties"]), (14, 5, 1))
        self.assertEqual((t["playoff_wins"], t["super_bowl_wins"]), (3, 1))
        self.assertEqual(t["winning_seasons"], 1)   # 2035 isn't a complete regular season yet

    def test_winning_season_and_playoffs_after_a_full_season(self):
        tenures = [{"team_id": CAR, "start_season": 2034, "end_season": None}]
        games = [game(2035, w, CAR, NYJ, 20, 10) for w in range(1, 11)] + \
                [game(2035, w, CAR, NYJ, 10, 20) for w in range(11, 18)] + \
                [game(2035, 20, CAR, ATL, 31, 7, "DIVISIONAL"), game(2035, 21, ATL, CAR, 24, 21, "CONFERENCE")]
        t = accumulate(SNAP, tenures, games, 2035)
        self.assertEqual(t["winning_seasons"], 2)
        self.assertEqual((t["playoff_wins"], t["playoff_losses"]), (4, 1))
        self.assertEqual((t["super_bowl_wins"], t["super_bowl_losses"]), (1, 0))

    def test_follows_the_coach_across_teams(self):
        tenures = [{"team_id": NYJ, "start_season": 2030, "end_season": 2034},
                   {"team_id": CAR, "start_season": 2035, "end_season": None}]
        snap = dict(SNAP, season=2033)
        games = [game(2034, 1, NYJ, ATL, 21, 0), game(2034, 2, CAR, ATL, 21, 0), game(2035, 1, CAR, NYJ, 3, 21)]
        t = accumulate(snap, tenures, games, 2035)
        self.assertEqual((t["career_wins"], t["career_losses"]), (14, 5))   # NYJ win in 2034, CAR loss in 2035

    def test_no_snapshot_means_no_career(self):
        self.assertIsNone(accumulate(None, [], [], 2035))
        self.assertIsNone(record_line(None))

    def test_season_record_and_lines(self):
        games = [game(2035, 1, CAR, NYJ, 24, 10), game(2035, 19, CAR, ATL, 3, 9, "WILD_CARD")]
        self.assertEqual(season_record(games, CAR, 2035), {"regular_season": "1-0", "playoffs": "0-1"})
        line = record_line(dict(SNAP, career_ties=1))
        self.assertEqual(line["regular_season"], "13-4-1")
        self.assertEqual(line["super_bowls"], "1-0")

    def test_tendencies_are_words_with_their_source(self):
        coach = {"coach_name": "X", "offensive_scheme": "Spread", "defensive_scheme": "3-4", "pass_lean": "High",
                 "fourth_down_aggressiveness": "Low", "blitz_rate": "Medium", "man_coverage": None}
        d = describe_tendencies(coach)
        self.assertTrue(d["pass_lean"].startswith("High (pass-heavy"))
        self.assertIn("not yet measured", d["pass_lean"])          # no measurement: the real-world tier
        self.assertIn("real-world staff philosophy", d["blitz_rate"])
        self.assertIsNone(d["man_coverage"])
        self.assertNotIn("%", "".join(v for v in d.values() if v))

    def test_measured_tiers_replace_the_real_world_ones(self):
        coach = {"coach_name": "X", "offensive_scheme": "Spread", "defensive_scheme": "3-4", "pass_lean": "High",
                 "fourth_down_aggressiveness": "Low", "blitz_rate": "High", "man_coverage": "Low",
                 "observed": {"pass_lean": ("Low", "measured in this league: 16 games this season")}}
        eff = effective_tendencies(coach)
        self.assertEqual(eff["pass_lean"][0], "Low")
        self.assertEqual(eff["fourth_down_aggressiveness"][0], "Low")   # not measured: real-world fallback
        self.assertEqual(eff["blitz_rate"][0], "High")                  # never measured
        line = staff_matchup(coach, dict(coach, coach_name="Y"))
        self.assertIn("X: Spread, pass lean Low (run-heavy; measured)", line["offense"])
        self.assertIn("blitz High (blitz-heavy; reputation)", line["defense"])
        self.assertIn("zone-first coverage; reputation", line["defense"])
        self.assertIsNone(staff_matchup(None, coach))


if __name__ == "__main__":
    unittest.main()
