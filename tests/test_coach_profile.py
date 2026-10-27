"""Coach career totals and tendency wording (engines/coach_profile.py). No database required.

Run: python3 -m unittest tests.test_coach_profile
"""
import unittest

from engines.coach_profile import (accumulate, describe_tendencies, effective_tendencies, fetch_games, record_line,
                                   season_record, staff_matchup, tenure_record, with_team_record)

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

    def test_playoff_record_with_team_counts_only_this_tenure(self):
        # CAR's earlier coach (2030-2034) won a playoff game in 2034; the new coach started in 2035.
        full_2035 = [game(2035, w, CAR, NYJ, 20, 10) for w in range(1, 18)]
        games = full_2035 + [game(2034, 19, CAR, ATL, 30, 3, "WILD_CARD"),
                             game(2035, 19, CAR, ATL, 27, 20, "WILD_CARD"),
                             game(2035, 20, NYJ, CAR, 24, 17, "DIVISIONAL"),
                             game(2035, 21, NYJ, ATL, 24, 17, "CONFERENCE")]   # not his team
        self.assertEqual(tenure_record(games, CAR, 2035, 2035), ("17-0", "1-1"))
        # A coach who changed teams: his NYJ playoff games don't count toward his CAR record.
        nyj = [game(2034, 19, NYJ, ATL, 30, 3, "WILD_CARD"), game(2034, 23, NYJ, ATL, 30, 3, "SUPER_BOWL")]
        self.assertEqual(tenure_record(nyj + games, CAR, 2035, 2035)[1], "1-1")

    def test_playoff_record_with_team_over_several_seasons(self):
        games = ([game(s, w, CAR, NYJ, 20, 10) for s in (2034, 2035) for w in range(1, 18)]
                 + [game(2034, 19, CAR, ATL, 30, 3, "WILD_CARD"), game(2034, 23, CAR, NYJ, 30, 3, "SUPER_BOWL"),
                    game(2035, 20, ATL, CAR, 30, 3, "DIVISIONAL")])
        self.assertEqual(tenure_record(games, CAR, 2034, 2035), ("34-0", "2-1"))

    def test_no_playoff_games_reads_zero_zero(self):
        games = [game(2034, w, CAR, NYJ, 10, 20) for w in range(1, 18)] + [game(2035, 1, CAR, NYJ, 20, 10)]
        self.assertEqual(tenure_record(games, CAR, 2034, 2035), ("1-17", "0-0"))

    def test_records_with_team_null_when_an_earlier_season_is_missing(self):
        games = [game(2034, w, CAR, NYJ, 20, 10) for w in range(1, 10)] + \
                [game(2034, 19, CAR, ATL, 30, 3, "WILD_CARD")]
        self.assertEqual(tenure_record(games, CAR, 2034, 2035), (None, None))

    def test_games_past_the_cutoff_are_not_counted(self):
        rows = [(2035, w, "REGULAR_SEASON", CAR, NYJ, 20, 10) for w in range(1, 18)] + \
               [(2035, 19, "WILD_CARD", CAR, ATL, 27, 20), (2035, 20, "DIVISIONAL", ATL, CAR, 30, 3)]

        class Cur:
            def execute(self, *a):
                pass

            def fetchall(self):
                return rows

        games = fetch_games(Cur(), {CAR}, 2035, 2035, lambda s, w: w <= 19)   # article written before week 20
        self.assertEqual(tenure_record(games, CAR, 2035, 2035), ("17-0", "1-0"))
        self.assertEqual(season_record(games, CAR, 2035)["playoffs"], "1-0")

    def test_tendencies_are_words_with_their_source(self):
        coach = {"coach_name": "X", "offensive_scheme": "Spread", "defensive_scheme": "3-4", "pass_lean": "High",
                 "fourth_down_aggressiveness": "Low", "blitz_rate": "Medium", "man_coverage": None}
        d = describe_tendencies(coach)
        self.assertTrue(d["pass_lean"].startswith("High (pass-heavy"))
        self.assertIn("not yet measured", d["pass_lean"])          # no measurement: the real-world tier
        self.assertNotIn("blitz_rate", d)                             # no blitz data: never shown
        self.assertIsNone(d["man_coverage"])
        self.assertNotIn("%", "".join(v for v in d.values() if v))

    def test_measured_tiers_replace_the_real_world_ones(self):
        coach = {"coach_name": "X", "offensive_scheme": "Spread", "defensive_scheme": "3-4", "pass_lean": "High",
                 "fourth_down_aggressiveness": "Low", "blitz_rate": "High", "man_coverage": "Low",
                 "observed": {"pass_lean": ("Low", "measured in this league: 16 games this season")}}
        eff = effective_tendencies(coach)
        self.assertEqual(eff["pass_lean"][0], "Low")
        self.assertEqual(eff["fourth_down_aggressiveness"][0], "Low")   # not measured: real-world fallback
        self.assertIn("real-world staff philosophy", eff["man_coverage"][1])   # never measured
        self.assertNotIn("blitz_rate", eff)
        line = staff_matchup(coach, dict(coach, coach_name="Y"))
        self.assertIn("X: Spread, pass lean Low (run-heavy; measured)", line["offense"])
        self.assertEqual(line["defense"], "Y: 3-4 front, coverage Low (zone-first coverage; reputation)")
        self.assertIn("zone-first coverage; reputation", line["defense"])
        self.assertIsNone(staff_matchup(None, coach))


class WithTeamRecordTests(unittest.TestCase):
    """with_team_record: finished seasons from coach_season_lines (2026-2034 generated, 2035 on archived), the
    current season from games (JR 2026-10-27: with-team records count from 2026)."""

    LINE = {"wins": 10, "losses": 7, "ties": 0, "playoff_wins": 1, "playoff_losses": 1}

    def test_lines_then_current_games(self):
        lines = {2034: self.LINE, 2035: {"wins": 9, "losses": 8, "ties": 0, "playoff_wins": 0, "playoff_losses": 0}}
        games = [game(2036, w, CAR, NYJ, 21, 10) for w in range(1, 4)] + [game(2036, 4, ATL, CAR, 30, 3)]
        self.assertEqual(with_team_record(lines, games, CAR, 2034, 2036), ("22-16", "1-1"))

    def test_ties_show(self):
        lines = {2035: {"wins": 8, "losses": 8, "ties": 1, "playoff_wins": 0, "playoff_losses": 0}}
        self.assertEqual(with_team_record(lines, [], CAR, 2035, 2036), ("8-8-1", "0-0"))

    def test_missing_finished_season_is_unknown(self):
        # 2034 has no line and no full season of games: unknown, never a partial total
        lines = {2035: self.LINE}
        self.assertEqual(with_team_record(lines, [], CAR, 2034, 2036), (None, None))

    def test_finished_season_from_games_without_a_line(self):
        games = [game(2035, w, CAR, NYJ, 20, 10) for w in range(1, 18)]
        self.assertEqual(with_team_record({}, games, CAR, 2035, 2036), ("17-0", "0-0"))

    def test_line_wins_over_games_for_a_finished_season(self):
        games = [game(2035, 1, CAR, NYJ, 20, 10)]
        self.assertEqual(with_team_record({2035: self.LINE}, games, CAR, 2035, 2036), ("10-7", "1-1"))


if __name__ == "__main__":
    unittest.main()
