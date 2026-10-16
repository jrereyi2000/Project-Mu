"""Unit tests for newsroom/award_winners.py (PROJECT_SPEC §7.6c).
No live database required.

Run: python3 -m unittest tests.test_award_winners
"""

import unittest
from unittest.mock import MagicMock
from newsroom.award_winners import (
    parse_award_winners,
    award_winners_fix_prompt,
    store_award_winners,
    resolve_coach,
    resolve_player_award,
)


class AwardWinnersTests(unittest.TestCase):
    def setUp(self):
        self.team_index = [
            (1, "Panthers", "Carolina", "CAR"),
            (2, "Broncos", "Denver", "DEN"),
            (3, "Texans", "Houston", "HOU"),
            (4, "Browns", "Cleveland", "CLE"),
        ]
        self.players = [
            {"player_id": 101, "full_name": "George Crawford", "player_name": "G. Crawford", "team_id": 1, "position": "QB", "team_name": "Panthers", "city": "Carolina", "abbreviation": "CAR"},
            {"player_id": 102, "full_name": "Trey'Mane Davis", "player_name": "T. Davis", "team_id": 1, "position": "WR", "team_name": "Panthers", "city": "Carolina", "abbreviation": "CAR"},
            {"player_id": 103, "full_name": "Isaiah Carson", "player_name": "I. Carson", "team_id": 1, "position": "WR", "team_name": "Panthers", "city": "Carolina", "abbreviation": "CAR"},
            {"player_id": 104, "full_name": "Cooper DeJean", "player_name": "C. DeJean", "team_id": 3, "position": "CB", "team_name": "Texans", "city": "Houston", "abbreviation": "HOU"},
            {"player_id": 105, "full_name": "Jarius Ellerbe", "player_name": "J. Ellerbe", "team_id": 4, "position": "CB", "team_name": "Browns", "city": "Cleveland", "abbreviation": "CLE"},
        ]
        self.roster_index = {
            "teams": self.team_index,
            "players": self.players,
        }
        self.coaches = [
            {"coach_id": 201, "coach_name": "Marcus Whitfield", "team_id": 1, "team_name": "Panthers", "city": "Carolina", "abbreviation": "CAR"},
            {"coach_id": 202, "coach_name": "Peter Gaffney", "team_id": 2, "team_name": "Broncos", "city": "Denver", "abbreviation": "DEN"},
        ]
        self.coach_index = {
            "teams": self.team_index,
            "coaches": self.coaches,
        }

    def test_parse_valid_awards_article(self):
        text = """
# George Crawford's Historic Campaign Secures MVP: Official 2035 NFL Award Winners

The 2035 regular season is in the books...

## MVP: George Crawford, QB, Carolina Panthers
Crawford broke the NFL passing TD record with 71 scores...

## OPOY: Trey'Mane Davis, WR, Carolina Panthers
Davis caught an NFL-record 29 touchdown passes...

## OROY: Isaiah Carson, WR, Carolina Panthers
Carson led all rookies with 19 touchdowns...

## DPOY: Cooper DeJean, CB, Houston Texans
DeJean anchored a suffocating Texans secondary with 8 interceptions...

## DROY: Jarius Ellerbe, CB, Cleveland Browns
Ellerbe locked down opposing WR1s across the AFC North...

## Coach of the Year: Marcus Whitfield, Carolina Panthers
Whitfield guided the Panthers to a historic 17-0 undefeated regular season...

### The Season in One Line
Perfection in Carolina redefined what was possible in professional football.
"""
        entries, errors = parse_award_winners(text, self.roster_index, self.coach_index)
        self.assertEqual(errors, [])
        self.assertEqual(len(entries), 6)

        entry_map = {e["award_type"]: e for e in entries}
        self.assertEqual(entry_map["MVP"]["player_name"], "George Crawford")
        self.assertEqual(entry_map["MVP"]["player_id"], 101)
        self.assertEqual(entry_map["MVP"]["team_id"], 1)

        self.assertEqual(entry_map["OPOY"]["player_name"], "Trey'Mane Davis")
        self.assertEqual(entry_map["OPOY"]["player_id"], 102)

        self.assertEqual(entry_map["OROY"]["player_name"], "Isaiah Carson")
        self.assertEqual(entry_map["OROY"]["player_id"], 103)

        self.assertEqual(entry_map["DPOY"]["player_name"], "Cooper DeJean")
        self.assertEqual(entry_map["DPOY"]["player_id"], 104)

        self.assertEqual(entry_map["DROY"]["player_name"], "Jarius Ellerbe")
        self.assertEqual(entry_map["DROY"]["player_id"], 105)

        self.assertEqual(entry_map["COTY"]["coach_name"], "Marcus Whitfield")
        self.assertEqual(entry_map["COTY"]["coach_id"], 201)
        self.assertEqual(entry_map["COTY"]["team_id"], 1)

    def test_missing_awards_reported(self):
        text = """
## MVP: George Crawford, QB, Carolina Panthers
## OPOY: Trey'Mane Davis, WR, Carolina Panthers
"""
        entries, errors = parse_award_winners(text, self.roster_index, self.coach_index)
        self.assertTrue(any("Missing required award headers" in e for e in errors))

    def test_unresolved_player_reported(self):
        text = """
## MVP: Unknown Superstar, QB, Carolina Panthers
## OPOY: Trey'Mane Davis, WR, Carolina Panthers
## OROY: Isaiah Carson, WR, Carolina Panthers
## DPOY: Cooper DeJean, CB, Houston Texans
## DROY: Jarius Ellerbe, CB, Cleveland Browns
## Coach of the Year: Marcus Whitfield, Carolina Panthers
"""
        entries, errors = parse_award_winners(text, self.roster_index, self.coach_index)
        self.assertTrue(any("Could not find player 'Unknown Superstar'" in e for e in errors))

    def test_unresolved_coach_reported(self):
        text = """
## MVP: George Crawford, QB, Carolina Panthers
## OPOY: Trey'Mane Davis, WR, Carolina Panthers
## OROY: Isaiah Carson, WR, Carolina Panthers
## DPOY: Cooper DeJean, CB, Houston Texans
## DROY: Jarius Ellerbe, CB, Cleveland Browns
## Coach of the Year: Unknown Coach, Carolina Panthers
"""
        entries, errors = parse_award_winners(text, self.roster_index, self.coach_index)
        self.assertTrue(any("Could not find coach 'Unknown Coach'" in e for e in errors))

    def test_fix_prompt_mentions_errors(self):
        errors = ["Missing required award headers: DPOY, DROY", "Could not find coach 'Ghost'"]
        prompt = award_winners_fix_prompt(errors)
        self.assertIn("Missing required award headers: DPOY, DROY", prompt)
        self.assertIn("Could not find coach 'Ghost'", prompt)
        self.assertIn("## MVP:", prompt)
        self.assertIn("## Coach of the Year:", prompt)

    def test_store_award_winners(self):
        cur = MagicMock()
        entries = [
            {"award_type": "MVP", "player_id": 101, "player_name": "George Crawford", "team_id": 1, "position": "QB", "coach_id": None, "coach_name": None},
            {"award_type": "COTY", "player_id": None, "player_name": None, "team_id": 1, "position": None, "coach_id": 201, "coach_name": "Marcus Whitfield"},
        ]
        n = store_award_winners(cur, 2035, 22, 935, "YATES", entries)
        self.assertEqual(n, 2)
        cur.execute.assert_any_call("""
        DELETE FROM awards 
        WHERE season = %s AND award_type IN ('MVP', 'OPOY', 'OROY', 'DPOY', 'DROY', 'COTY')
    """, (2035,))
        self.assertEqual(cur.execute.call_count, 3)


if __name__ == "__main__":
    unittest.main()
