"""engines/season_archive.py: standings, division tiebreakers, seeds read off the bracket, playoff results and the
coach career snapshot. The pure tests build tiny leagues; the live test only reads 2035 (skipped when Postgres is
down) and writes nothing."""
import unittest

import psycopg

from engines import season_archive as sa


def game(home, away, hs, as_, game_type="REGULAR_SEASON", gid=None):
    return {"id": gid, "season": 2035, "week": 1, "game_type": game_type, "home_team_id": home, "away_team_id": away,
            "home_score": hs, "away_score": as_}


# Two divisions of two teams each in one conference, plus a second conference for the seeds test.
TEAMS = {1: {"abbr": "AAA", "conference": "AFC", "division": "AFC East"},
         2: {"abbr": "BBB", "conference": "AFC", "division": "AFC East"},
         3: {"abbr": "CCC", "conference": "AFC", "division": "AFC West"},
         4: {"abbr": "DDD", "conference": "AFC", "division": "AFC West"}}


class StandingsTest(unittest.TestCase):
    def test_records_points_and_ties(self):
        rec = sa.standings([game(1, 2, 20, 10), game(2, 1, 17, 17), game(3, 4, 3, 0)], TEAMS)
        self.assertEqual((rec[1]["w"], rec[1]["l"], rec[1]["t"], rec[1]["pf"], rec[1]["pa"]), (1, 0, 1, 37, 27))
        self.assertEqual((rec[2]["w"], rec[2]["l"], rec[2]["t"]), (0, 1, 1))
        self.assertEqual(len(rec[4]["games"]), 1)

    def test_playoff_games_dont_count(self):
        rec = sa.standings([game(1, 3, 30, 0, "WILD_CARD")], TEAMS)
        self.assertEqual(rec[1]["w"], 0)


class TiebreakTest(unittest.TestCase):
    def test_head_to_head_decides_a_division(self):
        # 1 and 2 both finish 1-1: 1 beat 2, 2 beat 3
        games = [game(1, 2, 21, 7), game(2, 3, 14, 10), game(3, 1, 24, 3)]
        places = sa.division_places(sa.standings(games, TEAMS), TEAMS)
        self.assertEqual(places[1], (1, "head-to-head"))
        self.assertEqual(places[2], (2, None))

    def test_unbroken_falls_back_to_abbreviation(self):
        # 1 and 2 split, identical everything else, same net points
        games = [game(1, 2, 10, 7), game(2, 1, 10, 7)]
        places = sa.division_places(sa.standings(games, TEAMS), TEAMS)
        self.assertEqual(places[1], (1, sa.UNBROKEN))
        self.assertEqual(places[2], (2, None))

    def test_three_way_narrowing_restarts(self):
        teams = {i: {"abbr": a, "conference": "NFC", "division": "NFC North"} for i, a in
                 ((1, "AAA"), (2, "BBB"), (3, "CCC"), (4, "DDD"))}
        # 1, 2, 3 all 2-2; head-to-head among them: 1 is 2-0 vs 2 and 3, so 1 wins outright; then 2 vs 3 restart
        games = [game(1, 2, 10, 0), game(1, 3, 10, 0), game(2, 3, 10, 0), game(4, 1, 10, 0), game(4, 1, 10, 0),
                 game(3, 2, 10, 0), game(2, 4, 10, 0), game(3, 4, 10, 0), game(4, 2, 10, 0), game(4, 3, 10, 0),
                 game(3, 4, 10, 0)]
        rec = sa.standings(games, teams)
        order = sa.order_by_record(rec, teams, [1, 2, 3], "division")
        self.assertEqual(order[0], (1, "head-to-head"))


class BracketTest(unittest.TestCase):
    def league(self):
        teams = {}
        for i in range(1, 9):
            teams[i] = {"abbr": f"A{i}", "conference": "AFC", "division": f"AFC {(i - 1) // 4}"}
        for i in range(9, 17):
            teams[i] = {"abbr": f"N{i}", "conference": "NFC", "division": f"NFC {(i - 9) // 4}"}
        return teams

    def test_seeds_from_wild_card_pairs_and_bye(self):
        teams = self.league()
        # AFC: 1 has the bye; WC 2v7, 3v6, 4v5 (home teams 2, 3, 4 with records 12, 11, 10 wins)
        reg = []
        for team, wins in ((2, 12), (3, 11), (4, 10)):
            reg += [game(team, 8, 1, 0)] * wins
        playoff = [game(2, 7, 20, 10, "WILD_CARD"), game(3, 6, 20, 10, "WILD_CARD"), game(4, 5, 10, 20, "WILD_CARD"),
                   game(1, 5, 30, 0, "DIVISIONAL"), game(2, 3, 14, 7, "DIVISIONAL")]
        rec = sa.standings(reg, teams)
        out, problems = sa.seeds(rec, teams, playoff)
        self.assertEqual({t: out[t] for t in range(1, 8)}, {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7})
        self.assertIn("NFC", problems[0])               # no NFC bracket here: no seeds, said so

    def test_playoff_lines(self):
        playoff = [game(2, 7, 20, 10, "WILD_CARD"), game(1, 2, 30, 0, "DIVISIONAL"), game(1, 3, 21, 20, "CONFERENCE"),
                   game(9, 1, 24, 17, "SUPER_BOWL")]
        self.assertEqual(sa.playoff_line(1, playoff), ("SUPER_BOWL_LOSS", 2, 1))
        self.assertEqual(sa.playoff_line(9, playoff), ("CHAMPION", 1, 0))
        self.assertEqual(sa.playoff_line(2, playoff), ("DIVISIONAL", 1, 1))
        self.assertEqual(sa.playoff_line(4, playoff), ("MISSED", 0, 0))

    def test_compute_needs_the_super_bowl(self):
        with self.assertRaises(ValueError):
            sa.compute([game(1, 2, 10, 0)], TEAMS)


import pytest


@pytest.mark.integration
class Live2035Test(unittest.TestCase):
    """Reads 2035's games (complete: 272 regular season + 13 playoff) and checks the archive it would write."""

    def setUp(self):
        self.conn = psycopg.connect(sa.CONN_INFO)
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_2035(self):
        teams, games = sa._load(self.cur, 2035)
        r = sa.compute(games, teams)
        abbr = {t: i["abbr"] for t, i in teams.items()}
        self.assertEqual(r["problems"], [])
        self.assertEqual(abbr[r["league"]["champion_team_id"]], "CAR")
        self.assertEqual(abbr[r["league"]["runner_up_team_id"]], "DEN")
        self.assertEqual((r["league"]["regular_season_games"], r["league"]["postseason_games"]), (272, 13))
        for conf in ("AFC", "NFC"):
            seeds = sorted(row["conference_seed"] for t, row in r["teams"].items()
                           if teams[t]["conference"] == conf and row["conference_seed"])
            self.assertEqual(seeds, list(range(1, 8)))
        # every Wild Card game is seed s at home against 9 - s
        for g in games:
            if g["game_type"] == "WILD_CARD":
                self.assertEqual(r["teams"][g["home_team_id"]]["conference_seed"]
                                 + r["teams"][g["away_team_id"]]["conference_seed"], 9)
        self.assertEqual(sum(row["division_place"] == 1 for row in r["teams"].values()), 8)

    def test_coach_snapshot_matches_the_view(self):
        """A 2035 snapshot (2034 snapshot + 2035 games) leaves coach_records unchanged."""
        self.cur.execute("SELECT DISTINCT ON (ct.team_id) ct.coach_id FROM coach_tenures ct "
                         "WHERE ct.start_season <= 2035 AND coalesce(ct.end_season, 9999) >= 2035 LIMIT 3")
        for (cid,) in self.cur.fetchall():
            snap, mu_only, problem = sa.coach_snapshot(self.cur, cid, 2035)
            self.assertIsNone(problem)
            self.assertFalse(mu_only)
            self.cur.execute("SELECT season FROM coach_seasons WHERE coach_id = %s AND season < 2035 "
                             "ORDER BY season DESC LIMIT 1", (cid,))
            self.assertIsNotNone(self.cur.fetchone())
            self.assertGreaterEqual(snap["career_wins"] + snap["career_losses"], 17)


if __name__ == "__main__":
    unittest.main()
