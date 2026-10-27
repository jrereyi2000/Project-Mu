"""engines/career_milestones.py: round marks crossed this season (and who got there first), and the next marks within
reach. Pure tests on built rows; the integration test reads madden_test and writes nothing."""
import unittest

import psycopg
import pytest

from engines import career_milestones as cm


def row(pid, stat, before, now, games=17, name=None):
    return {"player_id": pid, "name": name or f"P{pid}", "team": "CAR", "position": "QB", "stat": stat,
            "career_before": before, "this_season": now, "games": games}


class CrossedTest(unittest.TestCase):
    def test_crossing_and_first_alone(self):
        out = cm.compute([row(1, "pass_yards", 48000, 3000), row(2, "pass_yards", 30000, 5000)], 2036, False, 0)
        marks = {(c["player_id"], c["mark"]): c for c in out["crossed"]}
        self.assertEqual(marks[(1, 50000)]["first"], "alone")          # nobody had 50,000 before
        self.assertFalse([k for k in marks if k[0] == 2])              # 30,000 -> 35,000 crosses no new mark

    def test_first_shared_within_a_season(self):
        out = cm.compute([row(1, "pass_tds", 290, 20), row(2, "pass_tds", 295, 10)], 2036, False, 0)
        firsts = {c["player_id"]: c["first"] for c in out["crossed"] if c["mark"] == 300}
        self.assertEqual(firsts, {1: "shared", 2: "shared"})

    def test_not_first_when_someone_had_it(self):
        out = cm.compute([row(1, "pass_tds", 290, 20), row(2, "pass_tds", 310, 5)], 2036, False, 0)
        crossed = [c for c in out["crossed"] if c["player_id"] == 1 and c["mark"] == 300][0]
        self.assertIsNone(crossed["first"])


class ChasingTest(unittest.TestCase):
    def test_in_season_reach_is_the_pace_over_games_left(self):
        # 9,700 so far, 300 a game over 10 games: 7 left -> 2,100 more: 10,000 is in reach
        out = cm.compute([row(1, "rush_yards", 6700, 3000, games=10)], 2036, True, 7)
        self.assertEqual([(c["mark"], c["needs"]) for c in out["chasing"]], [(10000, 300)])
        out = cm.compute([row(1, "rush_yards", 6700, 3000, games=10)], 2036, True, 0)
        self.assertEqual(out["chasing"], [])                            # no games left

    def test_after_the_season_reach_is_a_season_total(self):
        out = cm.compute([row(1, "def_sacks", 40, 8)], 2036, False, 0)
        self.assertEqual([c["mark"] for c in out["chasing"]], [50])    # needs 2, had 8 this season
        out = cm.compute([row(1, "def_sacks", 30, 8)], 2036, False, 0)
        self.assertEqual(out["chasing"], [])                            # needs 12, had 8

    def test_would_be_first_compares_against_everyone_now(self):
        out = cm.compute([row(1, "rec_tds", 45, 3), row(2, "rec_tds", 46, 6)], 2036, False, 0)
        chase = {c["player_id"]: c for c in out["chasing"]}
        self.assertFalse(chase[1]["first"])                             # player 2 already has 52
        self.assertIn(2, {c["player_id"] for c in out["crossed"]})

    def test_inactive_players_dont_chase(self):
        self.assertEqual(cm.compute([row(1, "rec_tds", 49, 0)], 2036, False, 0)["chasing"], [])


@pytest.mark.integration
class LiveTest(unittest.TestCase):
    def test_2036_reads(self):
        with psycopg.connect(cm.CONN_INFO) as conn:
            out = cm.season_milestones(conn.cursor(), 2036)
        self.assertEqual(out["since"], 2026)
        for c in out["crossed"]:
            self.assertIn(c["first"], (None, "alone", "shared"))
            self.assertGreaterEqual(c["career"], c["mark"])
        for c in out["chasing"]:
            self.assertGreater(c["mark"], c["career"])
            self.assertAlmostEqual(c["needs"], c["mark"] - c["career"], places=1)


if __name__ == "__main__":
    unittest.main()
