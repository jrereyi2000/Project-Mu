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
