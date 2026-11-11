"""Career-counter attribution on real PostgreSQL (data/audit_reports/stats_ingest/report.md H1, H2, L2, L4).

Every test runs inside one transaction that is rolled back, so nothing is left in the database. The real-save
classes read the PC saves at MU_SAVES_DIR (never write to them) through pipeline/save_reader/pc_save_reader.mjs.
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
CAREER_TABLE = "CareerOffensiveStats"
HAVE_READER = (shutil.which("node") is not None
               and os.path.isdir(os.path.join(ROOT, "pipeline", "save_reader", "node_modules")))


def read_save(name):
    """The save directory `name` as a snapshot dict (the reader only reads)."""
    out = subprocess.run(["node", READER, os.path.join(SAVES_DIR, name)],
                         capture_output=True, text=True, check=True, timeout=600)
    return json.loads(out.stdout)


@pytest.mark.integration
class CounterTimelineTest(unittest.TestCase):
    """Hand-built load timelines: the rules, without waiting for a real save."""

    def setUp(self):
        self.conn = psycopg.connect(ls.DSN)
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def query(self, sql, *args):
        self.cur.execute(sql, args)
        return self.cur.fetchall()

    def one(self, sql, *args):
        self.cur.execute(sql, args)
        return self.cur.fetchone()

    def new_player(self, name):
        self.cur.execute("INSERT INTO players (player_name, full_name) VALUES (%s, %s) RETURNING id", (name, name))
        return self.cur.fetchone()[0]

    def counter(self, pid, season, week, stage, games, targets, seq):
        self.cur.execute(
            "INSERT INTO player_career_counters (season, week, stage, player_id, madden_table, counters, "
            "games_played, load_seq) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (season, week, stage, pid, CAREER_TABLE,
             json.dumps({"GAMESPLAYED": games, "TARGETS": targets}), games, seq))

    def test_load_order_keeps_every_week_after_an_off_season_load(self):
        # H2: the Super Bowl and the off-season load stay in 2026; a 2027 preseason load covers the tail of 2026
        # and is credited to 2026, not to 2027.
        pid = self.new_player("T. Rollover")
        for args in [(2026, 0, "PRESEASON", 0, 0, 1),
                     (2026, 2, "WEEK", 1, 10, 2),
                     (2026, 3, "WEEK", 2, 15, 3),
                     (2026, 23, "WEEK", 3, 20, 4),        # Super Bowl
                     (2026, -1, "OFFSEASON", 3, 22, 5),   # post-Super-Bowl off-season load
                     (2027, 0, "PRESEASON", 3, 24, 6)]:   # next season, before week 1
            self.counter(pid, *args)
        rows = self.query("SELECT week, stage, status, games_covered, credited_season, delta ->> 'TARGETS' "
                          "FROM player_week_counters WHERE player_id = %s ORDER BY load_seq", pid)
        self.assertEqual(rows, [
            (0, "PRESEASON", "BASELINE", 0, 2026, None),
            (2, "WEEK", "OK", 1, 2026, "10"),
            (3, "WEEK", "OK", 1, 2026, "5"),
            (23, "WEEK", "OK", 1, 2026, "5"),         # the Super Bowl is a 2026 game
            (-1, "OFFSEASON", "NO_GAMES", 0, 2026, "2"),
            (0, "PRESEASON", "NO_GAMES", 0, 2026, "2")])
        self.assertEqual(self.query(
            "SELECT season, games_covered, weeks_no_games, weeks_unknown, delta ->> 'TARGETS' "
            "FROM player_season_counters WHERE player_id = %s ORDER BY season", pid),
            [(2026, 3, 2, 0, "24")])

    def test_a_delta_across_a_season_boundary_is_never_credited_to_the_later_season(self):
        cross = self.new_player("T. Cross")
        for args in [(2026, 0, "PRESEASON", 0, 0, 10),
                     (2026, 18, "WEEK", 18, 100, 11),    # end of the 2026 regular season
                     (2027, 1, "WEEK", 21, 130, 12)]:    # 2027 week 2: the 2026 playoffs are in the delta
            self.counter(cross, *args)
        rows = self.query("SELECT week, status, games_covered, credited_season, delta ->> 'TARGETS' "
                          "FROM player_week_counters WHERE player_id = %s ORDER BY load_seq", cross)
        self.assertEqual(rows, [
            (0, "BASELINE", 0, 2026, None),
            (18, "OK", 18, 2026, "100"),
            (1, "CROSS_SEASON", 3, 2026, None)])     # cannot be split: flagged under 2026, not 2027
        self.assertEqual(self.query("SELECT season, games_covered, weeks_unknown "
                                    "FROM player_season_counters WHERE player_id = %s", cross),
                         [(2026, 18, 1)])

        tail = self.new_player("T. Tail")
        for args in [(2026, 0, "PRESEASON", 0, 0, 20),
                     (2026, 23, "WEEK", 24, 120, 21),    # a season-long load
                     (2027, 0, "PRESEASON", 27, 130, 22)]:  # the next save, before 2027 week 1
            self.counter(tail, *args)
        self.assertEqual(self.one("SELECT status, games_covered, credited_season, delta ->> 'TARGETS' "
                                  "FROM player_week_counters WHERE player_id = %s AND season = 2027",
                                  tail), ("OK", 3, 2026, "10"))
        self.assertEqual(self.query("SELECT season, games_covered FROM player_season_counters "
                                    "WHERE player_id = %s ORDER BY season", tail), [(2026, 27)])

    def test_the_first_career_row_reports_the_games_it_loses(self):
        # L2: the first row is PARTIAL (not BASELINE) when the player's season row says games were played.
        pid = self.new_player("T. Partial")
        self.cur.execute("INSERT INTO season_stat_general (season, player_id, games_played) VALUES (2026, %s, 5)", (pid,))
        self.counter(pid, 2026, 4, "WEEK", 5, 40, 1)
        self.assertEqual(self.one("SELECT status, games_covered, delta FROM player_week_counters "
                                  "WHERE player_id = %s", pid), ("PARTIAL", 5, None))
        self.assertEqual(self.one("SELECT weeks_partial, games_lost FROM player_season_counters "
                                  "WHERE player_id = %s AND season = 2026", pid), (1, 5))

    def test_no_games_growth_stays_in_the_season_total(self):
        # L4: a NO_GAMES row whose tracked counters grew is kept in the season sum and counted.
        pid = self.new_player("T. Nogames")
        for args in [(2026, 0, "PRESEASON", 0, 0, 1),
                     (2026, 1, "WEEK", 1, 10, 2),
                     (2026, 2, "WEEK", 1, 15, 3)]:      # GAMESPLAYED did not move; five targets did
            self.counter(pid, *args)
        self.assertEqual(self.one("SELECT status, delta ->> 'TARGETS' FROM player_week_counters "
                                  "WHERE player_id = %s AND week = 2", pid), ("NO_GAMES", "5"))
        self.assertEqual(self.one("SELECT delta ->> 'TARGETS', weeks_no_games FROM player_season_counters "
                                  "WHERE player_id = %s AND season = 2026", pid), ("15", 1))


@pytest.mark.integration
@unittest.skipUnless(HAVE_READER and os.path.isdir(SAVES_DIR),
                     "needs node, `npm install` in pipeline/save_reader and the PC saves")
class RealSaveAttributionTest(unittest.TestCase):
    """The audit's own reproductions, on the real saves (read only), rolled back."""

    @classmethod
    def setUpClass(cls):
        cls.snapshots = {}

    def snap(self, name):
        if name not in self.snapshots:
            self.snapshots[name] = read_save(name)
        return self.snapshots[name]

    def setUp(self):
        self.conn = psycopg.connect(ls.DSN)
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def one(self, sql, *args):
        self.cur.execute(sql, args)
        return self.cur.fetchone()

    def test_pittman_week_10_uses_his_week_10_game_only(self):
        # H1: the saves are at current weeks 8, 10 and 12, so the rows' weeks are 7, 9 and 11 (the last played
        # week). Pittman's week-8 game (2 rec, 25 yds) therefore lands in the row before week 9 and no longer
        # leaks into a week-10 query; week 10 reads only his week-10 game line (7 rec, 70 yds).
        for name in ("CAREER-DEBUGWEEK8", "CAREER-DEBUGWEEK10", "CAREER-DEBUGWEEK12"):
            ls.load_snapshot(self.cur, self.snap(name), season=2026)
        pid = self.one("SELECT id FROM players WHERE player_name = 'M. Pittman'")[0]
        self.cur.execute(
            "SELECT week, stage, prev_week, status, delta ->> 'RECEIVECATCHES' "
            "FROM player_week_counters WHERE season = 2026 AND player_id = %s ORDER BY load_seq", (pid,))
        self.assertEqual(self.cur.fetchall(), [
            (7, "WEEK", None, "PARTIAL", None),       # the first load's row, labelled with the last played week
            (9, "WEEK", 7, "OK", "2"),
            (11, "WEEK", 9, "OK", "15")])

        metrics = am.player_metrics(self.cur, 2026, pid, week=10)
        self.assertEqual((metrics["inputs"].get("receptions"), metrics["inputs"].get("rec_yards")), (7, 70))
        self.assertIsNone(metrics["inputs"].get("targets"))   # the week-8 delta's 2 targets are not borrowed
        self.assertEqual(metrics["weeks_unknown"], 0)

    def test_2026_games_are_credited_to_2026_when_the_next_save_is_2027(self):
        # H2: MU2026 (a 2026 save before week 1) then TUNE-P2G (a 2027 save before week 1). The delta covers the
        # 2026 season and belongs to 2026, not to 2027 week 1.
        ls.load_snapshot(self.cur, self.snap("CAREER-MU2026"), season=2026)
        ls.load_snapshot(self.cur, self.snap("CAREER-TUNE-P2G"), season=2027)
        self.assertEqual(self.one(
            "SELECT COUNT(*) FROM player_week_counters WHERE credited_season = 2027 AND status IN ('OK', 'NO_GAMES')"),
            (0,))
        self.assertEqual(self.one(
            "SELECT SUM(games_covered) FROM player_week_counters "
            "WHERE credited_season = 2026 AND status IN ('OK', 'NO_GAMES')"), (24316,))
        self.assertEqual(self.one(
            "SELECT SUM((delta ->> 'RECEIVECATCHES')::int) FROM player_week_counters "
            "WHERE credited_season = 2026 AND status IN ('OK', 'NO_GAMES')"), (10124,))
        # 2027 may still have rows for players first seen in the P2G save (a BASELINE credited to 2027), but no
        # delta is credited to it.
        self.assertEqual(self.one("SELECT COUNT(*) FROM player_season_counters "
                                  "WHERE season = 2027 AND delta IS NOT NULL"), (0,))
