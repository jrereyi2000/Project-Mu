"""Napper league history and restart (docs/SEASON_CONTINUITY_PLAN.md phase 3): final ranks at completion, the
restarted season's draft order, and the restart itself. The pure tests need no database; the integration test runs
on madden_test (tests/conftest.py) inside one transaction that is rolled back."""
import unittest

import psycopg
import pytest
from psycopg.rows import dict_row

from engines.napper_playoffs import final_ranks
from server import napper_api as na


class FinalRanksTest(unittest.TestCase):
    def test_finish_then_seed(self):
        seeds = {10: 1, 20: 2, 30: 3, 40: 4, 50: 5, 60: 6, 70: 7, 80: 8}
        finish = {10: "RUNNER_UP", 20: "SEMIFINAL", 30: "SEMIFINAL", 40: "QUARTERFINAL", 50: "QUARTERFINAL",
                  60: "QUARTERFINAL", 70: "MISSED", 80: "MISSED"}
        ranks = final_ranks(seeds, finish, champion=60)          # the 6 seed wins it all
        self.assertEqual(ranks[60], 1)
        self.assertEqual(ranks[10], 2)
        self.assertEqual((ranks[20], ranks[30]), (3, 4))
        self.assertEqual((ranks[40], ranks[50]), (5, 6))
        self.assertEqual((ranks[70], ranks[80]), (7, 8))


class RestartOrderTest(unittest.TestCase):
    def test_reverse_of_final_standings(self):
        teams = [{"id": 1, "final_rank": 1, "seed": 2}, {"id": 2, "final_rank": 3, "seed": 1},
                 {"id": 3, "final_rank": 2, "seed": 3}]
        self.assertEqual([t["id"] for t in na.restart_order(teams)], [2, 3, 1])   # last place picks first

    def test_unranked_teams_fall_back_to_seed(self):
        teams = [{"id": 5, "final_rank": None, "seed": 1}, {"id": 6, "final_rank": None, "seed": 2}]
        self.assertEqual([t["id"] for t in na.restart_order(teams)], [6, 5])


@pytest.mark.integration
class RestartIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(na.DATABASE_URL, row_factory=dict_row)
        self.cur = self.conn.cursor()
        if not na._lineage_installed(self.cur):
            self.skipTest("Napper lineage migration not in this database")
        self.cur.execute("""SELECT l.id, l.season FROM napper_leagues l
                            WHERE EXISTS (SELECT 1 FROM napper_teams t WHERE t.league_id = l.id AND t.user_id IS NOT NULL)
                            ORDER BY l.is_seed, l.id LIMIT 1""")
        row = self.cur.fetchone()
        if not row:
            self.skipTest("no Napper league with the user's team")
        self.league_id, self.season = row["id"], int(row["season"])
        # finish the league by hand: ranks by id, the first team champion
        self.cur.execute("SELECT id FROM napper_teams WHERE league_id = %s ORDER BY id", (self.league_id,))
        self.team_ids = [r["id"] for r in self.cur.fetchall()]
        for rank, tid in enumerate(self.team_ids, 1):
            self.cur.execute("UPDATE napper_teams SET final_rank = %s, playoff_finish = %s WHERE id = %s",
                             (rank, "CHAMPION" if rank == 1 else "MISSED", tid))
        self.cur.execute("UPDATE napper_leagues SET status = 'COMPLETE', champion_team_id = %s WHERE id = %s",
                         (self.team_ids[0], self.league_id))

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_refused_before_the_new_nfl_season(self):
        self.cur.execute("UPDATE league_state SET current_season = %s WHERE id = 1", (self.season,))
        with self.assertRaises(na.HTTPException) as e:
            na.restart_league(self.cur, self.league_id)
        self.assertEqual(e.exception.status_code, 400)

    def test_restart_copies_the_league_in_reverse_order(self):
        self.cur.execute("UPDATE league_state SET current_season = %s WHERE id = 1", (self.season + 1,))
        out = na.restart_league(self.cur, self.league_id)
        new_id = out["league"]["id"]
        self.assertEqual(out["league"]["season"], self.season + 1)
        self.assertEqual(out["league"]["status"], "PRE_DRAFT")
        # the last-place team (highest final rank) picks first, the champion last
        self.assertEqual(out["draft_order"][0]["last_season_rank"], len(self.team_ids))
        self.assertEqual(out["draft_order"][-1]["last_season_rank"], 1)
        self.cur.execute("SELECT team_name, persona, wins, seed FROM napper_teams WHERE league_id = %s ORDER BY id",
                         (new_id,))
        new_teams = self.cur.fetchall()
        self.assertEqual(len(new_teams), len(self.team_ids))
        self.assertEqual([t["seed"] for t in new_teams], list(range(1, len(self.team_ids) + 1)))   # ids in slot order
        self.assertTrue(all(t["wins"] == 0 for t in new_teams))
        self.cur.execute("SELECT previous_league_id, lineage_id FROM napper_leagues WHERE id = %s", (new_id,))
        link = self.cur.fetchone()
        self.assertEqual(link["previous_league_id"], self.league_id)
        # history walks the lineage; the old league can't be restarted twice
        self.cur.execute("SELECT count(*) AS n FROM napper_leagues WHERE COALESCE(lineage_id, id) = %s",
                         (link["lineage_id"],))
        self.assertGreaterEqual(self.cur.fetchone()["n"], 2)                  # also when the old league had no lineage
        with self.assertRaises(na.HTTPException) as e:
            na.restart_league(self.cur, self.league_id)
        self.assertEqual(e.exception.status_code, 409)

    def test_unfinished_league_is_refused(self):
        self.cur.execute("UPDATE napper_leagues SET status = 'IN_SEASON' WHERE id = %s", (self.league_id,))
        self.cur.execute("UPDATE league_state SET current_season = %s WHERE id = 1", (self.season + 1,))
        with self.assertRaises(na.HTTPException) as e:
            na.restart_league(self.cur, self.league_id)
        self.assertEqual(e.exception.status_code, 400)


@pytest.mark.integration
class RestartedDraftCustomizationTest(unittest.TestCase):
    """A restarted league's draft is customizable exactly like a first-year one: the reverse-standings order is only
    the default the draft room opens with; Set Order, Randomize and Your Slot work on it. The draft engine commits
    through its own connections, so this test commits to madden_test and puts everything back afterwards."""

    def setUp(self):
        from engines import napper_draft_engine as de
        self.de = de
        self.conn = psycopg.connect(na.DATABASE_URL, row_factory=dict_row)
        self.cur = self.conn.cursor()
        if not na._lineage_installed(self.cur):
            self.skipTest("Napper lineage migration not in this database")
        self.cur.execute("""SELECT l.id, l.season, l.status, l.champion_team_id FROM napper_leagues l
                            WHERE EXISTS (SELECT 1 FROM napper_teams t WHERE t.league_id = l.id AND t.user_id IS NOT NULL)
                              AND NOT EXISTS (SELECT 1 FROM napper_leagues n WHERE n.previous_league_id = l.id)
                            ORDER BY l.is_seed, l.id LIMIT 1""")
        self.old = self.cur.fetchone()
        if not self.old:
            self.skipTest("no Napper league with the user's team")
        self.cur.execute("SELECT current_season FROM league_state WHERE id = 1")
        self.season = self.cur.fetchone()["current_season"]
        self.cur.execute("SELECT id, final_rank, playoff_finish FROM napper_teams WHERE league_id = %s ORDER BY id",
                         (self.old["id"],))
        self.saved_teams = self.cur.fetchall()
        for rank, t in enumerate(self.saved_teams, 1):
            self.cur.execute("UPDATE napper_teams SET final_rank = %s WHERE id = %s", (rank, t["id"]))
        self.cur.execute("UPDATE napper_leagues SET status = 'COMPLETE' WHERE id = %s", (self.old["id"],))
        self.cur.execute("UPDATE league_state SET current_season = %s WHERE id = 1", (int(self.old["season"]) + 1,))
        self.new_id = None
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()
        if self.new_id:
            self.cur.execute("DELETE FROM napper_leagues WHERE id = %s", (self.new_id,))
        for t in self.saved_teams:
            self.cur.execute("UPDATE napper_teams SET final_rank = %s, playoff_finish = %s WHERE id = %s",
                             (t["final_rank"], t["playoff_finish"], t["id"]))
        self.cur.execute("UPDATE napper_leagues SET status = %s, champion_team_id = %s WHERE id = %s",
                         (self.old["status"], self.old["champion_team_id"], self.old["id"]))
        self.cur.execute("UPDATE league_state SET current_season = %s WHERE id = 1", (self.season,))
        self.conn.commit()
        self.conn.close()

    def test_default_order_then_set_order_and_randomize(self):
        out = na.restart_league(self.cur, self.old["id"])
        self.conn.commit()
        self.new_id = out["league"]["id"]
        expected = [s["team_id"] for s in out["draft_order"]]          # reverse of last season
        user_slot = next(s["slot"] for s in out["draft_order"] if s["is_user"])

        state = na.get_league_draft(self.new_id, rounds=15)            # the draft room's first load
        self.assertEqual(state["status"], "PENDING")
        self.assertEqual(state["user_slot"], user_slot)
        slots = sorted(state["settings"]["teams_info"], key=lambda t: t["slot_num"])
        self.assertEqual([t["team_id"] for t in slots], expected)

        # Set Order: reverse it, exactly as the draft room's modal does
        n = len(expected)
        state = self.de.set_draft_order(state["id"], slot_order=list(range(n, 0, -1)))
        slots = sorted(state["settings"]["teams_info"], key=lambda t: t["slot_num"])
        self.assertEqual([t["team_id"] for t in slots], list(reversed(expected)))
        self.assertEqual(state["user_slot"], n + 1 - user_slot)

        # Randomize keeps every team, the draft still pending
        state = self.de.randomize_draft_order(state["id"])
        self.assertEqual(state["status"], "PENDING")
        self.assertEqual(sorted(t["team_id"] for t in state["settings"]["teams_info"]), sorted(expected))


if __name__ == "__main__":
    unittest.main()
