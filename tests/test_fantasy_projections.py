"""Tests for fantasy projection engine, including Week 1 preseason baseline projections."""
import unittest

from engines import fantasy_projection_engine as FPE


class FantasyProjectionEngineTest(unittest.TestCase):
    def setUp(self):
        self.data = FPE.SeasonData(
            season=2036,
            opponent={(1, 1): 2, (2, 1): 1, (1, 2): 2, (2, 2): 1},
            completed_weeks={1: set(), 2: set()},
            roster={
                101: {"name": "Star QB", "team_id": 1, "position": "QB", "group": "QB", "depth": 1, "ovr": 99},
                102: {"name": "Rookie QB", "team_id": 2, "position": "QB", "group": "QB", "depth": 1, "ovr": 82},
                103: {"name": "Injured HB", "team_id": 1, "position": "HB", "group": "RB", "depth": 1, "ovr": 90},
                104: {"name": "Backup HB", "team_id": 1, "position": "HB", "group": "RB", "depth": 2, "ovr": 80},
                105: {"name": "Healthy WR", "team_id": 2, "position": "WR", "group": "WR", "depth": 1, "ovr": 92},
            },
            injuries={
                (103, 1): {"weeks_remaining": 4, "is_ir": False},
            },
            prev_player_stats={
                101: {"games": 17, "total": 510.0, "ppg": 30.0},
                104: {"games": 10, "total": 60.0, "ppg": 6.0},
                105: {"games": 16, "total": 320.0, "ppg": 20.0},
            },
            prev_slot_priors={
                ("QB", 1): 17.0,
                ("RB", 1): 12.0,
                ("RB", 2): 5.0,
                ("WR", 1): 16.0,
            },
            prev_def_factors={
                (1, "QB"): 1.05,
                (2, "QB"): 0.95,
                (1, "WR"): 1.10,
                (2, "RB"): 1.00,
            },
            prev_def_stars={
                "QB": {1: 4, 2: 2},
                "RB": {1: 3, 2: 3},
                "WR": {1: 5, 2: 1},
            },
        )

    def test_week_1_projection_populates_projections_and_rankings(self):
        rows = FPE.project_week(self.data, 1)
        self.assertEqual(len(rows), 5)
        by_pid = {r["player_id"]: r for r in rows}

        # Star QB: 30.0 ppg over 17 games weighted with prior 17.0 over 4 games:
        # baseline = (510 + 4*17) / (17 + 4) = 578 / 21 ≈ 27.52
        # opp = 2, factor = 0.95 -> 27.52 * 0.95 ≈ 26.15
        star_qb = by_pid[101]
        self.assertAlmostEqual(star_qb["projected_points"], 26.15, places=1)
        self.assertEqual(star_qb["pos_rank"], "QB1")
        self.assertEqual(star_qb["ros_rank"], "QB1")
        self.assertGreater(star_qb["ros_total_points"], 0.0)
        self.assertEqual(star_qb["matchup_stars"], 2)
        self.assertEqual(star_qb["avg_points"], 30.0)
        self.assertIsNotNone(star_qb["delta"])

        # Rookie QB (no prior stats): uses slot prior scaled by OVR
        rookie_qb = by_pid[102]
        self.assertGreater(rookie_qb["projected_points"], 0.0)
        self.assertEqual(rookie_qb["pos_rank"], "QB2")
        self.assertIsNone(rookie_qb["avg_points"])
        self.assertIsNone(rookie_qb["delta"])

        # Injured player: 0.0 projected points, no pos_rank, is_out = True
        inj_hb = by_pid[103]
        self.assertEqual(inj_hb["projected_points"], 0.0)
        self.assertIsNone(inj_hb.get("pos_rank"))
        self.assertTrue(inj_hb["is_out"])
        # ros_total_points discounted for 4 injured weeks
        self.assertIsNotNone(inj_hb["ros_rank"])

    def test_week_2_empty_without_completed_games(self):
        # Week 2 still has no completed games in self.data, so it returns []
        rows = FPE.project_week(self.data, 2)
        self.assertEqual(rows, [])



class _ScriptedCursor:
    """Returns one scripted fetchall() result per execute(), in order; records each query and its params."""
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.results.pop(0) if self.results else []


class KickerDefenseProjectionTest(unittest.TestCase):
    def test_load_points_rows_tags_groups_and_reads_skill_lines_only(self):
        cur = _ScriptedCursor([
            [(1, 10, "HB", 1, 12.5), (2, 10, "WR", 1, None)],   # player_stats
            [(3, 10, 1, 9.0)],                                    # game_stat_kicking
            [(4, 20, 1, 14.0)],                                   # team defense
        ])
        rows = FPE.load_points_rows(cur, 2036)
        self.assertEqual(rows, [
            (1, 10, "RB", 1, 12.5), (2, 10, "WR", 1, 0.0), (3, 10, "K", 1, 9.0), (4, 20, "DEF", 1, 14.0),
        ])
        # K/DEF points come only from their own tables, never from player_stats (no double counting).
        self.assertEqual(cur.calls[0][1], (2036, list(FPE.SKILL_POSITIONS)))
        self.assertNotIn("K", FPE.SKILL_POSITIONS)
        self.assertNotIn("DEF", FPE.SKILL_POSITIONS)

    def _two_team_data(self):
        data = FPE.SeasonData(
            season=2036,
            opponent={(1, 1): 2, (2, 1): 1, (1, 2): 2, (2, 2): 1},
            completed_weeks={1: {1}, 2: {1}},
            roster={
                201: {"name": "Big Leg", "team_id": 1, "position": "K", "group": "K", "depth": 1, "ovr": 85},
                202: {"name": "Short Leg", "team_id": 2, "position": "K", "group": "K", "depth": 1, "ovr": 70},
                301: {"name": "Team 1 D/ST", "team_id": 1, "position": "DEF", "group": "DEF", "depth": 1, "ovr": 80},
                302: {"name": "Team 2 D/ST", "team_id": 2, "position": "DEF", "group": "DEF", "depth": 1, "ovr": 75},
            },
        )
        for pid, team, grp, opp, pts in [(201, 1, "K", 2, 12.0), (202, 2, "K", 1, 5.0),
                                         (301, 1, "DEF", 2, 15.0), (302, 2, "DEF", 1, 2.0)]:
            data.points[pid][1] = pts
            data.allowed[(opp, grp, 1)] += pts
        return data

    def test_week_2_projects_and_ranks_kickers_and_defenses(self):
        by_pid = {r["player_id"]: r for r in FPE.project_week(self._two_team_data(), 2)}
        self.assertEqual(set(by_pid), {201, 202, 301, 302})
        self.assertEqual(by_pid[201]["pos_rank"], "K1")
        self.assertEqual(by_pid[202]["pos_rank"], "K2")
        self.assertEqual(by_pid[301]["pos_rank"], "DEF1")
        self.assertEqual(by_pid[302]["pos_rank"], "DEF2")
        for r in by_pid.values():
            self.assertGreater(r["projected_points"], 0.0)
            self.assertIsNotNone(r["matchup_stars"])
            self.assertTrue(r["ros_rank"].startswith(r["group"]))

    def test_week_1_without_prior_season_uses_default_k_def_priors(self):
        data = self._two_team_data()
        data.completed_weeks = {1: set(), 2: set()}
        by_pid = {r["player_id"]: r for r in FPE.project_week(data, 1)}
        # No prior-season games: default slot prior × OVR adjustment (80 OVR → ×1.0), neutral matchup.
        self.assertAlmostEqual(by_pid[301]["projected_points"], FPE.DEFAULT_SLOT_PRIORS[("DEF", 1)], places=2)
        self.assertAlmostEqual(by_pid[201]["projected_points"],
                               round(FPE.DEFAULT_SLOT_PRIORS[("K", 1)] * 1.075, 2), places=2)
        self.assertEqual(by_pid[201]["pos_rank"], "K1")

    def test_league_sim_includes_kickers_and_defenses(self):
        from engines import fantasy_league_sim as sim
        data = self._two_team_data()
        data.roster[101] = {"name": "QB", "team_id": 1, "position": "QB", "group": "QB", "depth": 1, "ovr": 90}
        original = FPE.load_season
        FPE.load_season = lambda cur, season: data
        try:
            wd = sim.load_week_data(_ScriptedCursor([]), 2036)
        finally:
            FPE.load_season = original
        self.assertEqual(wd.group_of, {101: "QB", 201: "K", 202: "K", 301: "DEF", 302: "DEF"})
        self.assertEqual(wd.team_of, {101: 1, 201: 1, 202: 2, 301: 1, 302: 2})


if __name__ == "__main__":
    unittest.main()


class TeamChangeTest(unittest.TestCase):
    """League Transactions (team management step 2): traded players keep their history, released ones project 0."""
    def _data(self):
        opp = {}
        for w in (1, 2, 3):
            opp.update({(1, w): 2, (2, w): 1, (3, w): 4, (4, w): 3})
        data = FPE.SeasonData(
            season=2036, opponent=opp, completed_weeks={t: {1, 2} for t in (1, 2, 3, 4)},
            roster={
                201: {"name": "Traded WR", "team_id": 3, "position": "WR", "group": "WR", "depth": 1, "ovr": 85,
                      "released_week": None},
                202: {"name": "Cut WR", "team_id": 1, "position": "WR", "group": "WR", "depth": 99, "ovr": 70,
                      "released_week": 2},
                203: {"name": "Stays WR", "team_id": 2, "position": "WR", "group": "WR", "depth": 1, "ovr": 85,
                      "released_week": None},
            })
        data.points[201] = {1: 20.0, 2: 20.0}          # both weeks for his old team 1
        data.points[202] = {1: 5.0}
        data.points[203] = {1: 20.0, 2: 20.0}
        return data

    def test_traded_player_keeps_old_team_weeks(self):
        data = self._data()
        data.completed_weeks[3] = {2}                  # his new team played only week 2 so far
        rows = {r["player_id"]: r for r in FPE.project_week(data, 3)}
        self.assertEqual(rows[201]["avg_points"], 20.0)
        self.assertEqual(FPE.player_weeks(data, 201, {2}, 3), {1, 2})

    def test_released_player_projects_zero(self):
        rows = {r["player_id"]: r for r in FPE.project_week(self._data(), 3)}
        self.assertEqual(rows[202]["projected_points"], 0.0)
        self.assertEqual(rows[202]["ros_total_points"], 0.0)
        self.assertIsNone(rows[202].get("pos_rank"))
        self.assertGreater(rows[203]["projected_points"], 0.0)
