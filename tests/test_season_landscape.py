"""Unit tests for the brief's division standings (newsroom/season_landscape.py). No database required.

Run: python3 -m unittest tests.test_season_landscape
"""
import unittest

from newsroom.season_landscape import division_standings, records_by_team

TEAMS = [
    {"id": 1, "name": "Bills", "city": "Buffalo", "conference": "AFC", "division": "AFC East"},
    {"id": 2, "name": "Dolphins", "city": "Miami", "conference": "AFC", "division": "AFC East"},
    {"id": 3, "name": "Chiefs", "city": "Kansas City", "conference": "AFC", "division": "AFC West"},
]


def g(week, home, away, hs, as_):
    return {"week": week, "home_team_id": home, "away_team_id": away, "home_score": hs, "away_score": as_}


class SeasonLandscapeTests(unittest.TestCase):
    def test_records_and_order(self):
        games = [g(1, 1, 2, 24, 17), g(2, 2, 3, 30, 10), g(3, 1, 3, 20, 20)]
        st = division_standings(TEAMS, games, before_week=4)
        self.assertEqual(list(st), ["AFC East", "AFC West"])
        self.assertTrue(st["AFC East"][0].startswith("Buffalo Bills (ID: 1) 1-0-1, PF 44, PA 37, diff +7"))
        self.assertEqual(records_by_team(st), {1: "1-0-1", 2: "1-1", 3: "0-1-1"})

    def test_publication_week_and_later_are_excluded(self):
        games = [g(1, 1, 2, 24, 17), g(5, 2, 1, 40, 0)]
        self.assertEqual(records_by_team(division_standings(TEAMS, games, before_week=5))[2], "0-1")

    def test_playoff_games_do_not_count(self):
        games = [g(18, 1, 2, 24, 17), g(19, 2, 1, 40, 0)]
        self.assertEqual(records_by_team(division_standings(TEAMS, games, before_week=21))[1], "1-0")

    def test_week_1_everyone_is_winless(self):
        self.assertEqual(set(records_by_team(division_standings(TEAMS, [], before_week=1)).values()), {"0-0"})


if __name__ == "__main__":
    unittest.main()
