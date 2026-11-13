"""Advanced metrics on the same games, on real PostgreSQL and real saves (audit H3, M1).

H3: the audit's reproduction is MUBASE then P2H (the 2026 save at week 4). Madden did not track week 1's
inside/outside-pocket partition, so the Career deltas have 27 receptions against 20 targets and a season
catch rate of 1.35. Both sides of every metric now come from the Career deltas of the same tracked weeks,
and an untracked week is excluded and reported like an unknown week.

M1: the counters behind five metrics contradict the metrics' premises on MU2026 -> P2G.

Every test runs inside one transaction that is rolled back. The real saves are read (never written) through
pipeline/save_reader/pc_save_reader.mjs.

Scratch database only (a clean, seeded build; never a real or tuning database):

    docker exec -i madden-db psql -U postgres -c 'DROP DATABASE IF EXISTS mu_scratch_stats_metrics' \\
        -c 'CREATE DATABASE mu_scratch_stats_metrics'
    docker exec -i madden-db psql -U postgres -d mu_scratch_stats_metrics -v ON_ERROR_STOP=1 < db/init_db.sql
    python3 -m db.seed_clean_db --season 2026 --week 1 \\
        --db 'host=localhost dbname=mu_scratch_stats_metrics user=postgres password=postgres'

then

    STATS_METRICS_TEST_DSN='host=localhost dbname=mu_scratch_stats_metrics user=postgres password=postgres' \\
        python3 -m pytest -m integration tests/test_advanced_metrics_coverage.py
"""
import json
import os
import shutil
import subprocess
import unittest

import psycopg
import pytest

from engines import advanced_metrics as am
from pipeline import load_save as ls

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
READER = os.path.join(ROOT, "pipeline", "save_reader", "pc_save_reader.mjs")
SAVES_DIR = os.environ.get("MU_SAVES_DIR", "/mnt/c/Users/jrere/Documents/Madden NFL 27/saves")
DSN_ENV = "STATS_METRICS_TEST_DSN"
SCRATCH_DSN = os.environ.get(DSN_ENV, "")
HAVE_READER = (shutil.which("node") is not None
               and os.path.isdir(os.path.join(ROOT, "pipeline", "save_reader", "node_modules")))
IS_SCRATCH = "dbname=mu_scratch_" in SCRATCH_DSN


def read_save(name):
    """The save directory `name` as a snapshot dict (the reader only reads)."""
    out = subprocess.run(["node", READER, os.path.join(SAVES_DIR, name)],
                         capture_output=True, text=True, check=True, timeout=600)
    return json.loads(out.stdout)


@unittest.skipUnless(HAVE_READER and os.path.isdir(SAVES_DIR) and IS_SCRATCH,
                     f"needs node, the PC saves, a seeded mu_scratch_* database and {DSN_ENV}")
@pytest.mark.integration
class SameGamesMetricsTest(unittest.TestCase):
    """Audit H3 and M1 on the saves that produced them, rolled back."""

    @classmethod
    def setUpClass(cls):
        cls.snapshots = {}

    def snap(self, name):
        if name not in self.snapshots:
            self.snapshots[name] = read_save(name)
        return self.snapshots[name]

    def setUp(self):
        self.conn = psycopg.connect(SCRATCH_DSN)
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def one(self, sql, *args):
        self.cur.execute(sql, args)
        return self.cur.fetchone()

    def test_a_credited_previous_season_row_fills_the_complete_team_denominator(self):
        # audit-5 S1's complete fixture: two completed 2098 games with 50 + 50 team pass attempts, a season line
        # with 2 games, and career rows 2098 PRESEASON -> 2099 PRESEASON whose delta is credited to 2098. The
        # 2098 denominator must be both games (100) and target_share 20/100, not an empty window.
        self.cur.execute("INSERT INTO teams (name) VALUES ('SCRATCH-S1') RETURNING id")
        team = self.cur.fetchone()[0]
        self.cur.execute("INSERT INTO players (player_name, full_name) VALUES ('S. One', 'Scratch One') RETURNING id")
        pid = self.cur.fetchone()[0]
        self.cur.execute("INSERT INTO player_seasons (player_id, team_id, season) VALUES (%s, %s, 2098)", (pid, team))
        self.cur.execute("INSERT INTO season_stat_general (season, player_id, games_played) VALUES (2098, %s, 2)", (pid,))
        for week in (18, 23):
            self.cur.execute("INSERT INTO games (season, week, home_team_id) VALUES (2098, %s, %s) RETURNING id",
                             (week, team))
            gid = self.cur.fetchone()[0]
            self.cur.execute("INSERT INTO team_stats (game_id, team_name, passing_attempts, total_plays) "
                             "VALUES (%s, 'SCRATCH-S1', 50, 120)", (gid,))
        for week, games, targets, seq in ((0, 0, 0, 1), (0, 2, 20, 2)):
            self.cur.execute(
                "INSERT INTO player_career_counters (season, week, stage, player_id, madden_table, counters, "
                "games_played, load_seq) VALUES (%s, %s, 'PRESEASON', %s, 'CareerOffensiveStats', %s, %s, %s)",
                (2098 if seq == 1 else 2099, week, pid,
                 json.dumps({"GAMESPLAYED": games, "TARGETS": targets, "RECEIVECATCHES": targets // 2}), games, seq))

        metrics = am.player_metrics(self.cur, 2098, pid)

        self.assertEqual(metrics["inputs"]["team_pass_attempts"], 100)
        self.assertEqual(metrics["metrics"]["target_share"], pytest.approx(0.2))
        self.assertTrue(metrics["coverage"]["same_games"])

    def moore(self):
        """The audit's D. Moore (Bills): several players share the name."""
        return self.one("SELECT p.id FROM players p "
                        "JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = 2026 "
                        "JOIN teams t ON t.id = ps.team_id "
                        "WHERE p.player_name = 'D. Moore' AND t.name = 'Bills'")[0]

    def test_s3_a_week_reads_only_a_delta_isolated_to_its_game(self):
        # audit-5 S3: loads at DEBUGWEEK8, 10 and 12 leave deltas labelled (9, prev 7) and (11, prev 9).
        # Pittman's bye is week 9 and the second delta covers weeks 10-11, so neither can describe a single
        # week: week 9 has no game and week 10/11 keep only their box score, with no borrowed targets.
        for name in ("CAREER-DEBUGWEEK8", "CAREER-DEBUGWEEK10", "CAREER-DEBUGWEEK12"):
            ls.load_snapshot(self.cur, self.snap(name), season=2026)
        pittman = self.one("SELECT id FROM players WHERE player_name = 'M. Pittman'")[0]

        season = am.player_metrics(self.cur, 2026, pittman)
        self.assertEqual(season["inputs"]["targets"], 26)
        self.assertEqual(season["inputs"]["team_pass_attempts"], 83)
        self.assertAlmostEqual(season["metrics"]["target_share"], 26 / 83, places=10)
        self.assertAlmostEqual(season["metrics"]["catch_rate"], 15 / 26, places=10)

        week9 = am.player_metrics(self.cur, 2026, pittman, week=9)
        self.assertIsNone(week9["inputs"].get("receptions"))   # no week-9 game: week 8 is not borrowed
        self.assertIsNone(week9["inputs"].get("targets"))
        self.assertEqual(week9["coverage"]["games_covered"], 0)
        self.assertFalse(week9["coverage"]["same_games"])
        self.assertTrue(week9["coverage"]["reasons"])

        week10 = am.player_metrics(self.cur, 2026, pittman, week=10)
        self.assertEqual(week10["inputs"]["receptions"], 7)
        self.assertEqual(week10["inputs"]["rec_yards"], 70)
        self.assertIsNone(week10["inputs"].get("targets"))
        self.assertIsNone(week10["metrics"]["catch_rate"])
        self.assertIsNone(week10["metrics"]["target_share"])
        self.assertFalse(week10["coverage"]["same_games"])

        week11 = am.player_metrics(self.cur, 2026, pittman, week=11)
        self.assertEqual(week11["inputs"]["receptions"], 8)
        self.assertEqual(week11["inputs"]["rec_yards"], 73)
        self.assertIsNone(week11["inputs"].get("targets"))
        self.assertIsNone(week11["metrics"]["catch_rate"])
        self.assertIsNone(week11["metrics"]["target_share"])
        self.assertFalse(week11["coverage"]["same_games"])
        self.assertTrue(any("split" in reason for reason in week11["coverage"]["reasons"]))

    def test_s4_no_receiver_publishes_a_catch_rate_above_one(self):
        # audit-5 S4: D. Moore's delta has 11 receptions on 10 targets; the season and week 11 must not
        # publish 1.1, and coverage names the contradicted counter. Rice's valid tracked ratio still computes.
        for name in ("CAREER-DEBUGWEEK8", "CAREER-DEBUGWEEK10", "CAREER-DEBUGWEEK12"):
            ls.load_snapshot(self.cur, self.snap(name), season=2026)
        moore = self.moore()
        rice = self.one("SELECT id FROM players WHERE player_name = 'R. Rice'")[0]

        season = am.player_metrics(self.cur, 2026, moore)
        self.assertEqual((season["inputs"]["receptions"], season["inputs"]["targets"]), (11, 10))
        self.assertIsNone(season["metrics"]["catch_rate"])
        self.assertIsNone(season["metrics"]["target_share"])
        self.assertTrue(season["coverage"]["invalid_inputs"])
        self.assertTrue(any("exceed" in item for item in season["coverage"]["invalid_inputs"]))

        week11 = am.player_metrics(self.cur, 2026, moore, week=11)
        self.assertIsNone(week11["metrics"]["catch_rate"])
        self.assertIsNone(week11["metrics"]["target_share"])
        self.assertTrue(week11["coverage"]["invalid_inputs"])

        rice_season = am.player_metrics(self.cur, 2026, rice)
        self.assertAlmostEqual(rice_season["metrics"]["catch_rate"], 14 / 15, places=10)
        self.assertAlmostEqual(rice_season["metrics"]["target_share"], 0.2, places=10)

    def test_s1_a_cross_season_pair_without_full_team_history_publishes_no_share(self):
        # audit-5 S1: MU2026 -> P2G credits the whole 2026 season (17 games) to 2026, but only the early
        # 2026 team boxes are stored, so the denominator would be partial history. No share is published
        # (and never the 2027 club's attempts), and coverage names the gap.
        ls.load_snapshot(self.cur, self.snap("CAREER-MU2026"), season=2026)
        ls.load_snapshot(self.cur, self.snap("CAREER-TUNE-P2G"), season=2027)
        self.cur.execute("""
            SELECT DISTINCT p.id
            FROM players p
            LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = 2026
            LEFT JOIN teams t ON t.id = ps.team_id
            WHERE p.player_name IN ('M. Pittman', 'R. Rice')
               OR (p.player_name = 'D. Moore' AND t.name = 'Bills')""")
        players = [row[0] for row in self.cur.fetchall()]
        self.assertEqual(len(players), 3)

        for pid in players:
            season = am.player_metrics(self.cur, 2026, pid)
            self.assertIsNone(season["metrics"]["target_share"])
            self.assertIsNone(season["inputs"].get("team_pass_attempts"))
            self.assertTrue(season["coverage"]["team_denominator"])
            self.assertGreater(season["coverage"]["games_covered"], 9)

    def test_p2h_has_no_catch_rate_above_one_and_flags_the_untracked_week(self):
        # H3's reproduction. MUBASE then P2H; the audit measured Rice at 27 receptions / 20 targets = 1.35
        # and 123 of 272 receivers above 1.
        ls.load_snapshot(self.cur, self.snap("CAREER-MUBASE"), season=2026)
        ls.load_snapshot(self.cur, self.snap("CAREER-TUNE-P2H"), season=2026)

        rice = self.one("SELECT id FROM players WHERE player_name = 'R. Rice'")[0]
        metrics = am.player_metrics(self.cur, 2026, rice)
        self.assertIsNone(metrics["metrics"]["catch_rate"])
        self.assertIsNone(metrics["metrics"]["target_share"])
        self.assertEqual(metrics["coverage"]["untracked_weeks"], 1)
        self.assertGreaterEqual(metrics["weeks_unknown"], 1)
        self.assertEqual(metrics["coverage"]["games_covered"], 0)       # no tracked Career games
        self.assertEqual(metrics["coverage"]["stat_line_games"], 3)     # the box score still covers 3
        self.assertFalse(metrics["coverage"]["mixed_sources"])

        self.cur.execute(
            "SELECT DISTINCT player_id FROM player_week_counters "
            "WHERE credited_season = 2026 AND (delta ->> 'TARGETS')::int > 0")
        receivers = [row[0] for row in self.cur.fetchall()]
        self.assertGreater(len(receivers), 200)

        computed, over_one = 0, []
        for pid in receivers:
            value = am.player_metrics(self.cur, 2026, pid)["metrics"]["catch_rate"]
            if value is None:
                continue
            computed += 1
            if value > 1:
                over_one.append((pid, value))
        self.assertEqual(over_one, [], f"catch rate above 1 for {over_one}")
        self.assertGreater(computed, 0, "no receiver on a fully tracked team computed")

    def test_m1_counters_contradict_their_metrics_on_mu2026_to_p2g(self):
        ls.load_snapshot(self.cur, self.snap("CAREER-MU2026"), season=2026)
        ls.load_snapshot(self.cur, self.snap("CAREER-TUNE-P2G"), season=2027)

        row = self.one("""
            SELECT SUM((delta ->> 'DLINESACKS')::int)      AS full_sacks,
                   SUM((delta ->> 'DLINEHALFSACK')::int)   AS half_sacks,
                   SUM((delta ->> 'DEFPRESSURES')::int)    AS pressures,
                   SUM((delta ->> 'CTHALLOWED')::int)      AS catches_allowed,
                   SUM((delta ->> 'DEFTARGETS')::int)      AS def_targets,
                   SUM((delta ->> 'RECEIVECATCHES')::int)  AS receptions,
                   SUM((delta ->> 'TARGETS')::int)         AS targets,
                   SUM((delta ->> 'RUSH10YARDRUNS')::int)  AS rush_10plus,
                   SUM((delta ->> 'RUSH20YARDRUNS')::int)  AS rush_20plus
            FROM player_week_counters
            WHERE credited_season = 2026 AND status IN ('OK', 'NO_GAMES') AND delta IS NOT NULL""")
        sacks, half, pressures, catches_allowed, def_targets, receptions, targets, rush_10, rush_20 = row
        self.assertEqual((sacks, pressures), (1305, 732))          # the audit's M1 numbers
        # Sack conversion: more sacks than pressures (1.86 including the half sacks).
        self.assertGreater((sacks + 0.5 * half) / pressures, 1)
        # Burn %: catches allowed over defensive targets is nowhere near receptions over targets.
        self.assertGreater(abs(catches_allowed / def_targets - receptions / targets), 0.1)
        self.assertEqual((catches_allowed, def_targets), (10003, 48001))
        self.assertEqual((receptions, targets), (10124, 14117))
        # Explosive runs: 10+ runs are rarer than 20+ runs, so the buckets do not nest.
        self.assertLess(rush_10, rush_20)
        self.assertEqual((rush_10, rush_20), (179, 306))

        # Snap share: real players' downs played exceed their team's total plays.
        over_one = self.one("""
            WITH plays AS (
                SELECT t.id AS team_id, SUM(ts.total_plays) AS plays
                FROM team_stats ts
                JOIN games g ON g.id = ts.game_id
                JOIN teams t ON t.name = ts.team_name
                WHERE g.season = 2026
                GROUP BY t.id
            ), downs AS (
                SELECT w.player_id, ps.team_id, SUM((w.delta ->> 'DOWNSPLAYED')::int) AS downs
                FROM player_week_counters w
                JOIN player_seasons ps ON ps.player_id = w.player_id AND ps.season = 2026
                WHERE w.credited_season = 2026 AND w.status IN ('OK', 'NO_GAMES') AND w.delta IS NOT NULL
                GROUP BY w.player_id, ps.team_id
            )
            SELECT COUNT(*) FROM downs d JOIN plays p USING (team_id)
            WHERE d.downs > 0 AND p.plays > 0 AND d.downs > p.plays""")[0]
        self.assertGreater(over_one, 0)

        # The five metrics are classified BLOCKED, and compute() never returns them.
        keys = ("explosive_rate", "coverage_rating_lite", "burn_pct", "sack_conversion_rate", "snap_share")
        for key in keys:
            self.assertEqual(am.METRICS[key].status, am.BLOCKED, key)
            self.assertNotIn(key, am.compute({}))


@unittest.skipUnless(IS_SCRATCH, f"needs a seeded mu_scratch_* database and {DSN_ENV}")
@pytest.mark.integration
class IdentityAndIsolationTest(unittest.TestCase):
    """audit-5 stats3 R1/R2 on real PostgreSQL: per-game isolation and per-historical-game clubs.

    Every fixture is inserted into one transaction that is rolled back; no save is read. The tables, views and
    the public `player_metrics` path run unchanged.
    """

    def setUp(self):
        self.conn = psycopg.connect(SCRATCH_DSN)
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def team(self, name):
        self.cur.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id", (name,))
        return self.cur.fetchone()[0]

    def player(self, name, team_id, season):
        self.cur.execute("INSERT INTO players (player_name, full_name) VALUES (%s, %s) RETURNING id",
                         (name, f"{name} fixture"))
        pid = self.cur.fetchone()[0]
        self.cur.execute("INSERT INTO player_seasons (player_id, team_id, season) VALUES (%s, %s, %s)",
                         (pid, team_id, season))
        return pid

    def game(self, season, week, team_id, attempts=None, plays=100):
        """One game with the club as home team, and its box when `attempts` is given."""
        self.cur.execute("INSERT INTO games (season, week, home_team_id) VALUES (%s, %s, %s) RETURNING id",
                         (season, week, team_id))
        gid = self.cur.fetchone()[0]
        if attempts is not None:
            self.cur.execute("INSERT INTO team_stats (game_id, team_name, passing_attempts, total_plays) "
                             "SELECT %s, name, %s, %s FROM teams WHERE id = %s",
                             (gid, attempts, plays, team_id))
        return gid

    def both_game(self, season, week, home_id, away_id, home_attempts, away_attempts):
        """One game between two candidate clubs, with both boxes."""
        self.cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id) "
                         "VALUES (%s, %s, %s, %s) RETURNING id", (season, week, home_id, away_id))
        gid = self.cur.fetchone()[0]
        for team_id, attempts in ((home_id, home_attempts), (away_id, away_attempts)):
            self.cur.execute("INSERT INTO team_stats (game_id, team_name, passing_attempts, total_plays) "
                             "SELECT %s, name, %s, 100 FROM teams WHERE id = %s", (gid, attempts, team_id))
        return gid

    def line(self, game_id, player_id, team_id, **box):
        """The player's own game line (the per-game club identity)."""
        columns = ", ".join(box)
        placeholders = ", ".join(["%s"] * len(box))
        self.cur.execute(
            f"INSERT INTO player_stats (game_id, player_id, player_name, team_id, {columns}) "
            f"VALUES (%s, %s, 'Fixture', %s, {placeholders})",
            (game_id, player_id, team_id, *box.values()))

    def career(self, season, player_id, week, games, load_seq, **counters):
        self.cur.execute(
            "INSERT INTO player_career_counters (season, week, stage, player_id, madden_table, counters, "
            "games_played, load_seq) VALUES (%s, %s, %s, %s, 'CareerOffensiveStats', %s, %s, %s)",
            (season, week, "PRESEASON" if week <= 0 else "REGULAR", player_id, json.dumps(counters),
             games, load_seq))

    def season_games(self, season, player_id, games):
        self.cur.execute("INSERT INTO season_stat_general (season, player_id, games_played) VALUES (%s, %s, %s)",
                         (season, player_id, games))

    def test_r1_a_truncated_schedule_cannot_lend_a_two_game_delta_to_one_week(self):
        season = 2096
        team = self.team("B9-R1-TEAM")
        pid = self.player("B9 R1", team, season)
        g2 = self.game(season, 2, team, attempts=50)
        self.line(g2, pid, team, receptions=4, rec_yards=40)
        self.career(season, pid, 0, 0, 1, GAMESPLAYED=0)
        self.career(season, pid, 2, 2, 2, GAMESPLAYED=2, TARGETS=20, RECEIVECATCHES=10)

        week2 = am.player_metrics(self.cur, season, pid, week=2)

        self.assertEqual(week2["inputs"]["receptions"], 4)
        self.assertEqual(week2["inputs"]["rec_yards"], 40)
        self.assertIsNone(week2["inputs"].get("targets"))
        self.assertIsNone(week2["metrics"]["catch_rate"])
        self.assertIsNone(week2["metrics"]["target_share"])
        self.assertEqual(week2["coverage"]["games_covered"], 0)
        self.assertFalse(week2["coverage"]["same_games"])
        self.assertTrue(any("cannot be isolated" in reason for reason in week2["coverage"]["reasons"]))

        # Control: storing the missing week-1 game and box changes no counter or week-2 line, and the same
        # call must refuse as a split rather than publish either delta.
        self.game(season, 1, team, attempts=50)
        control = am.player_metrics(self.cur, season, pid, week=2)
        self.assertEqual(control["inputs"]["receptions"], 4)
        self.assertEqual(control["inputs"]["rec_yards"], 40)
        self.assertIsNone(control["inputs"].get("targets"))
        self.assertIsNone(control["metrics"]["target_share"])
        self.assertTrue(any("cannot be split" in reason for reason in control["coverage"]["reasons"]))

    def test_r1_a_missing_earlier_game_in_a_longer_interval_is_refused(self):
        season = 2096
        team = self.team("B9-R1B-TEAM")
        pid = self.player("B9 R1B", team, season)
        g3 = self.game(season, 3, team, attempts=40)
        self.line(g3, pid, team, receptions=5, rec_yards=55)
        self.career(season, pid, 1, 0, 1, GAMESPLAYED=0)
        self.career(season, pid, 3, 2, 2, GAMESPLAYED=2, TARGETS=11, RECEIVECATCHES=7)

        week3 = am.player_metrics(self.cur, season, pid, week=3)

        self.assertEqual(week3["inputs"]["receptions"], 5)
        self.assertIsNone(week3["inputs"].get("targets"))
        self.assertEqual(week3["coverage"]["games_covered"], 0)
        self.assertTrue(any("cannot be isolated" in reason for reason in week3["coverage"]["reasons"]))

    def test_r1_a_missing_box_and_an_absent_game_are_named_differently(self):
        season = 2096
        team = self.team("B9-R1C-TEAM")
        pid = self.player("B9 R1C", team, season)
        self.game(season, 5, team, attempts=None)          # the game is stored without its box
        self.career(season, pid, 4, 0, 1, GAMESPLAYED=0)
        self.career(season, pid, 5, 1, 2, GAMESPLAYED=1, TARGETS=9, RECEIVECATCHES=6)

        missing_box = am.player_metrics(self.cur, season, pid, week=5)
        self.assertEqual(missing_box["metrics"]["catch_rate"], pytest.approx(6 / 9))
        self.assertIsNone(missing_box["metrics"]["target_share"])
        self.assertEqual(missing_box["coverage"]["team_denominator"], "week 5: missing team box")

        self.game(season, 6, team, attempts=30)            # week 6 exists for the club...
        self.career(season, pid, 7, 1, 3, GAMESPLAYED=2, TARGETS=4, RECEIVECATCHES=3)
        absent = am.player_metrics(self.cur, season, pid, week=7)   # ...but not week 7
        self.assertIsNone(absent["inputs"].get("targets"))
        self.assertEqual(absent["coverage"]["games_covered"], 0)
        self.assertEqual(absent["coverage"]["team_denominator"],
                         "week 7: no game in the team's stored schedule")
        self.assertTrue(any("no game" in reason for reason in absent["coverage"]["reasons"]))

    def test_r1_a_genuine_aligned_one_game_delta_still_publishes(self):
        season = 2096
        team = self.team("B9-R1D-TEAM")
        pid = self.player("B9 R1D", team, season)
        g8 = self.game(season, 8, team, attempts=40)
        self.line(g8, pid, team, receptions=5, rec_yards=60)
        self.career(season, pid, 7, 0, 1, GAMESPLAYED=0)
        self.career(season, pid, 8, 1, 2, GAMESPLAYED=1, TARGETS=7, RECEIVECATCHES=5)

        week8 = am.player_metrics(self.cur, season, pid, week=8)

        self.assertEqual(week8["inputs"]["targets"], 7)
        self.assertEqual(week8["inputs"]["team_pass_attempts"], 40)
        self.assertEqual(week8["coverage"]["games_covered"], 1)
        self.assertTrue(week8["coverage"]["same_games"])
        self.assertAlmostEqual(week8["metrics"]["target_share"], 7 / 40, places=10)

    def test_r2_a_traded_player_s_season_denominator_is_bound_per_historical_game(self):
        season = 2095
        club_a = self.team("B9-R2-A")
        club_b = self.team("B9-R2-B")
        pid = self.player("B9 R2", club_b, season)          # the latest club
        g_a1 = self.game(season, 1, club_a, attempts=50)
        self.game(season, 1, club_b, attempts=100)          # club B's own, unrelated week-1 game
        g_b2 = self.game(season, 2, club_b, attempts=60)
        self.line(g_a1, pid, club_a, receptions=6)
        self.line(g_b2, pid, club_b, receptions=4)
        self.season_games(season, pid, 2)
        self.career(season, pid, 0, 0, 1, GAMESPLAYED=0)
        self.career(season, pid, 2, 2, 2, GAMESPLAYED=2, TARGETS=20, RECEIVECATCHES=10)

        season_out = am.player_metrics(self.cur, season, pid)

        self.assertEqual(season_out["inputs"]["team_pass_attempts"], 110)
        self.assertEqual(season_out["inputs"]["targets"], 20)
        self.assertAlmostEqual(season_out["metrics"]["target_share"], 20 / 110, places=10)
        self.assertTrue(season_out["coverage"]["same_games"])
        self.assertIsNone(season_out["coverage"]["team_denominator"])

    def test_r2_an_unknown_historical_club_refuses_the_share(self):
        season = 2095
        club_a = self.team("B9-R2C-A")
        club_b = self.team("B9-R2C-B")
        pid = self.player("B9 R2C", club_b, season)
        g_a1 = self.game(season, 1, club_a, attempts=50)
        g_b2 = self.game(season, 2, club_b, attempts=60)
        self.line(g_a1, pid, club_a, receptions=6)
        self.line(g_b2, pid, club_b, receptions=4)
        self.both_game(season, 3, club_a, club_b, 40, 40)   # both candidates play week 3
        self.season_games(season, pid, 3)
        self.career(season, pid, 0, 0, 1, GAMESPLAYED=0)
        self.career(season, pid, 3, 3, 2, GAMESPLAYED=3, TARGETS=20, RECEIVECATCHES=10)

        season_out = am.player_metrics(self.cur, season, pid)

        self.assertIsNone(season_out["metrics"]["target_share"])
        self.assertIsNone(season_out["inputs"]["team_pass_attempts"])
        self.assertIn("week 3", season_out["coverage"]["team_denominator"])
        self.assertIn("historical club cannot be established",
                      season_out["coverage"]["team_denominator"])

    def test_r2_a_same_season_return_binds_each_club_s_own_game(self):
        season = 2094
        club_a = self.team("B9-R2D-A")
        club_b = self.team("B9-R2D-B")
        pid = self.player("B9 R2D", club_b, season)
        g_a1 = self.game(season, 1, club_a, attempts=50)
        g_b2 = self.game(season, 2, club_b, attempts=60)
        g_a3 = self.game(season, 3, club_a, attempts=40)
        self.line(g_a1, pid, club_a, receptions=2)
        self.line(g_b2, pid, club_b, receptions=3)
        self.line(g_a3, pid, club_a, receptions=4)
        self.season_games(season, pid, 3)
        self.career(season, pid, 0, 0, 1, GAMESPLAYED=0)
        self.career(season, pid, 3, 3, 2, GAMESPLAYED=3, TARGETS=18, RECEIVECATCHES=9)

        season_out = am.player_metrics(self.cur, season, pid)

        self.assertEqual(season_out["inputs"]["team_pass_attempts"], 150)
        self.assertAlmostEqual(season_out["metrics"]["target_share"], 18 / 150, places=10)
        self.assertIsNone(season_out["coverage"]["team_denominator"])

    def test_r2_a_row_is_checked_against_its_own_club_s_tracking(self):
        season = 2097
        club_a = self.team("B9-R2E-A")
        club_b = self.team("B9-R2E-B")
        receiver = self.player("B9 R2E", club_b, season)
        qb_a = self.player("B9 R2E-QA", club_a, season)
        qb_b = self.player("B9 R2E-QB", club_b, season)
        g_a1 = self.game(season, 1, club_a, attempts=50)
        g_b2 = self.game(season, 2, club_b, attempts=60)
        self.line(g_a1, receiver, club_a, receptions=6)
        self.line(g_b2, receiver, club_b, receptions=2)
        # Club A's week 1 was not tracked (no pocket attempts); club B's week 2 was.
        self.career(season, qb_a, 0, 0, 1, GAMESPLAYED=0)
        self.career(season, qb_a, 1, 1, 2, GAMESPLAYED=1, PASSATTEMPTS=50,
                    INSIDEPOCKETTHROW_ATTEMPTS=0, OUTSIDEPOCKETTHROW_ATTEMPTS=0)
        self.career(season, qb_b, 0, 0, 1, GAMESPLAYED=0)
        self.career(season, qb_b, 2, 1, 2, GAMESPLAYED=1, PASSATTEMPTS=60,
                    INSIDEPOCKETTHROW_ATTEMPTS=60, OUTSIDEPOCKETTHROW_ATTEMPTS=0)
        self.career(season, receiver, 0, 0, 1, GAMESPLAYED=0)
        self.career(season, receiver, 1, 1, 2, GAMESPLAYED=1, TARGETS=9, RECEIVECATCHES=6)
        # cumulative counters: the week-2 row adds 5 targets and 2 catches to the week-1 totals
        self.career(season, receiver, 2, 2, 3, GAMESPLAYED=2, TARGETS=14, RECEIVECATCHES=8)

        season_out = am.player_metrics(self.cur, season, receiver)

        # The week-1 row belongs to untracked club A and is excluded; only the tracked week-2 row is used.
        self.assertEqual(season_out["coverage"]["untracked_weeks"], 1)
        self.assertEqual(season_out["inputs"]["targets"], 5)
        self.assertEqual(season_out["coverage"]["games_covered"], 1)
        self.assertEqual(season_out["inputs"]["team_pass_attempts"], 60)
        self.assertAlmostEqual(season_out["metrics"]["target_share"], 5 / 60, places=10)

    def test_r2a_a_missing_old_club_line_refuses_the_latest_club_s_denominator(self):
        # audit-stats3 R2a: the player's old-club line is gone, so club A vanishes from the candidate set and
        # the latest club's own week-1 game would stand in for it. The game he played has no retained identity,
        # so the denominator is refused rather than filled from club B (160 = 100 + 60 and 20/160 = 0.125).
        season = 2092
        club_a = self.team("B9-R2A-A")
        club_b = self.team("B9-R2A-B")
        pid = self.player("B9 R2A", club_b, season)          # the latest club
        g_a1 = self.game(season, 1, club_a, attempts=50)
        self.game(season, 1, club_b, attempts=100)           # club B's own, unrelated week-1 game
        g_b2 = self.game(season, 2, club_b, attempts=60)
        self.line(g_a1, pid, club_a, receptions=6)
        self.line(g_b2, pid, club_b, receptions=4)
        self.season_games(season, pid, 2)
        self.career(season, pid, 0, 0, 1, GAMESPLAYED=0)
        self.career(season, pid, 2, 2, 2, GAMESPLAYED=2, TARGETS=20, RECEIVECATCHES=10)
        # Only the old club's line is deleted: both games and both boxes stay, and the week is still unnamed.
        self.cur.execute("DELETE FROM player_stats WHERE game_id = %s AND player_id = %s", (g_a1, pid))

        season_out = am.player_metrics(self.cur, season, pid)

        self.assertIsNone(season_out["inputs"]["team_pass_attempts"])
        self.assertIsNone(season_out["inputs"]["team_total_plays"])
        self.assertIsNone(season_out["metrics"]["target_share"])
        self.assertEqual(season_out["inputs"]["targets"], 20)      # the numerator itself is untouched
        self.assertEqual(season_out["coverage"]["games_covered"], 2)
        self.assertIn("week 1", season_out["coverage"]["team_denominator"])
        self.assertIn("historical club cannot be established",
                      season_out["coverage"]["team_denominator"])

    def test_r2a_a_line_less_week_the_player_did_not_play_still_counts(self):
        # The other side of the rule: his lines account for both games his Career rows claim, so week 2 is a
        # game he did not appear in and still counts for the club's denominator (JR's DNP choice is untouched).
        season = 2091
        club = self.team("B9-R2B-TEAM")
        pid = self.player("B9 R2B", club, season)
        g1 = self.game(season, 1, club, attempts=50)
        self.game(season, 2, club, attempts=40)              # no line: he did not play this one
        g3 = self.game(season, 3, club, attempts=60)
        self.line(g1, pid, club, receptions=3)
        self.line(g3, pid, club, receptions=6)
        self.season_games(season, pid, 2)
        self.career(season, pid, 0, 0, 1, GAMESPLAYED=0)
        self.career(season, pid, 1, 1, 2, GAMESPLAYED=1, TARGETS=4, RECEIVECATCHES=3)
        self.career(season, pid, 3, 2, 3, GAMESPLAYED=2, TARGETS=9, RECEIVECATCHES=6)

        season_out = am.player_metrics(self.cur, season, pid)

        self.assertEqual(season_out["inputs"]["team_pass_attempts"], 150)
        self.assertEqual(season_out["metrics"]["target_share"], pytest.approx(9 / 150))
        self.assertIsNone(season_out["coverage"]["team_denominator"])
        self.assertTrue(season_out["coverage"]["same_games"])

    def traded_passer_fixture(self, season, later_club):
        """The audit's R2b fixture: receiver A/week 1 tracked as untracked, a passer whose later club moved.

        Club A's week 1 was not tracked (50 pass attempts, no pocket attempts); club B's week 2 was (60/60).
        The passer who threw club A's week 1 keeps his own week-1 game line, and only `player_seasons` says
        where he plays now: `later_club` is club B (a later trade to the receiver's own club) or a club that is
        not a candidate at all.
        """
        club_a = self.team("B9-R2F-A")
        club_b = self.team("B9-R2F-B")
        extra = club_b if later_club == "B" else self.team("B9-R2F-C")
        receiver = self.player("B9 R2F", club_b, season)
        qb_a = self.player("B9 R2F-QA", extra, season)
        qb_b = self.player("B9 R2F-QB", club_b, season)
        g_a1 = self.game(season, 1, club_a, attempts=50)
        g_b2 = self.game(season, 2, club_b, attempts=60)
        self.line(g_a1, receiver, club_a, receptions=6)
        self.line(g_b2, receiver, club_b, receptions=2)
        self.line(g_a1, qb_a, club_a, completions=20, pass_attempts=50)
        self.line(g_b2, qb_b, club_b, completions=30, pass_attempts=60)
        self.career(season, qb_a, 0, 0, 1, GAMESPLAYED=0)
        self.career(season, qb_a, 1, 1, 2, GAMESPLAYED=1, PASSATTEMPTS=50,
                    INSIDEPOCKETTHROW_ATTEMPTS=0, OUTSIDEPOCKETTHROW_ATTEMPTS=0)
        self.career(season, qb_b, 0, 0, 1, GAMESPLAYED=0)
        self.career(season, qb_b, 2, 1, 2, GAMESPLAYED=1, PASSATTEMPTS=60,
                    INSIDEPOCKETTHROW_ATTEMPTS=60, OUTSIDEPOCKETTHROW_ATTEMPTS=0)
        self.career(season, receiver, 0, 0, 1, GAMESPLAYED=0)
        self.career(season, receiver, 1, 1, 2, GAMESPLAYED=1, TARGETS=9, RECEIVECATCHES=6)
        self.career(season, receiver, 2, 2, 3, GAMESPLAYED=2, TARGETS=14, RECEIVECATCHES=8)
        return receiver

    def test_r2b_a_traded_passer_s_old_rows_keep_their_historical_club(self):
        # audit-stats3 R2b: only the passer's later club changed (A -> B), so his week-1 row must still be club
        # A's untracked interval: untracked 1, used targets 5, denominator 60, share 5/60.
        season = 2090
        receiver = self.traded_passer_fixture(season, later_club="B")

        season_out = am.player_metrics(self.cur, season, receiver)

        self.assertEqual(season_out["coverage"]["untracked_weeks"], 1)
        self.assertEqual(season_out["weeks_unknown"], 1)
        self.assertEqual(season_out["inputs"]["targets"], 5)
        self.assertEqual(season_out["coverage"]["games_covered"], 1)
        self.assertEqual(season_out["inputs"]["team_pass_attempts"], 60)
        self.assertEqual(season_out["metrics"]["target_share"], pytest.approx(5 / 60))

    def test_r2b_a_passer_who_moved_to_an_unrelated_club_is_still_tracked(self):
        # A passer who left for a club that is not a candidate at all must not vanish from the tracking query:
        # his historical week-1 row is still club A's, and the receiver's week-1 row stays untracked.
        season = 2089
        receiver = self.traded_passer_fixture(season, later_club="C")

        season_out = am.player_metrics(self.cur, season, receiver)

        self.assertEqual(season_out["coverage"]["untracked_weeks"], 1)
        self.assertEqual(season_out["inputs"]["targets"], 5)
        self.assertEqual(season_out["inputs"]["team_pass_attempts"], 60)
        self.assertEqual(season_out["metrics"]["target_share"], pytest.approx(5 / 60))
