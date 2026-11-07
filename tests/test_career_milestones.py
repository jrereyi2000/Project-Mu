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


class RealCareerTest(unittest.TestCase):
    """Real NFL careers through 2025 count toward career_before (REAL_NFL_HISTORY_PLAN Q3)."""

    def league(self):
        return {1: {"name": "Vet", "team": "KC", "position": "QB", "games": 17,
                    "stats": {"pass_yards": (0.0, 4200.0), "pass_tds": (0.0, 30.0)}},
                2: {"name": "Rookie", "team": "NYJ", "position": "QB", "games": 17,
                    "stats": {"pass_yards": (0.0, 3000.0), "pass_tds": (0.0, 0.0)}}}

    def test_real_career_counts_toward_career_before_and_since(self):
        rows = cm.add_real_careers([], self.league(), {1: {"pass_yards": 37000, "pass_tds": 280}}, {1: 2017})
        vet = {r["stat"]: r for r in rows if r["player_id"] == 1}
        self.assertEqual(vet["pass_yards"]["career_before"], 37000)
        self.assertEqual(vet["pass_yards"]["since"], 2017)
        out = cm.compute(rows, 2026, False, 0)
        crossed = [(c["name"], c["mark"], c["since"]) for c in out["crossed"]]
        self.assertIn(("Vet", 40000, 2017), crossed)
        self.assertIn(("Vet", 300, 2017), crossed)

    def test_unlinked_player_unchanged(self):
        rows = cm.add_real_careers([], self.league(), {}, {})
        rookie = {r["stat"]: r for r in rows if r["player_id"] == 2}
        self.assertEqual(rookie["pass_yards"]["career_before"], 0)
        self.assertEqual(rookie["pass_yards"]["since"], 2026)
        self.assertNotIn("pass_tds", rookie)          # nothing before, nothing this season

    def test_season_milestones_reads_real_careers(self):
        from engines.nfl_history import read as nfl_read

        class Cur:
            def __init__(self):
                self.q = []

            def execute(self, sql, params=None):
                self.q.append(sql)

            def fetchone(self):
                return (17 * 16, 17 * 16)          # season complete

            def fetchall(self):
                return [(1, "Vet", "KC", "QB", 17) + tuple(0 for _ in cm.MILESTONES)
                        + tuple(4200 if s == "pass_yards" else 0 for s in cm.MILESTONES)]
        orig = (nfl_read.nfl_career_before, nfl_read.nfl_first_seasons)
        nfl_read.nfl_career_before = lambda cur, ids, stats: {1: {"pass_yards": 37000}}
        nfl_read.nfl_first_seasons = lambda cur, ids: {1: 2017}
        try:
            out = cm.season_milestones(Cur(), 2026)
        finally:
            nfl_read.nfl_career_before, nfl_read.nfl_first_seasons = orig
        self.assertTrue(out["real_career_included"])
        self.assertEqual([(c["mark"], c["since"]) for c in out["crossed"]], [(40000, 2017)])
