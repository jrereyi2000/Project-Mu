"""pipeline/load_save.py: mapping helpers (unit) and loads into madden_test that are always rolled back (integration)."""
import json
import os
import shutil
import subprocess
import unittest
from decimal import Decimal

import psycopg
import pytest

from pipeline import load_save as ls

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_SAVE = os.environ.get("MU_SAMPLE_SAVE", os.path.join(ROOT, "CAREER-GIANTS-AUTOSAVE"))
READER = os.path.join(ROOT, "pipeline", "save_reader", "pc_save_reader.mjs")
TEST_SEASON = 2099          # far from any real season; every load here is rolled back

PGID_SQL = ("ALTER TABLE players ADD COLUMN IF NOT EXISTS madden_pgid INT; "
            "CREATE UNIQUE INDEX IF NOT EXISTS players_madden_pgid ON players (madden_pgid) WHERE madden_pgid IS NOT NULL")


class MappingTest(unittest.TestCase):
    def test_positions(self):
        self.assertEqual(ls.position("LE"), "LEDG")
        self.assertEqual(ls.position("MLB"), "MIKE")
        self.assertEqual(ls.position("ROLB"), "WILL")
        self.assertEqual(ls.position("LOLB"), "SAM")
        self.assertEqual(ls.position("WR"), "WR")

    def test_dev_traits_by_rank(self):
        self.assertEqual(ls.dev_trait("Normal"), "Normal")
        self.assertEqual(ls.dev_trait("College_Impact"), "Star")
        self.assertEqual(ls.dev_trait("College_Star"), "Superstar")
        self.assertEqual(ls.dev_trait("College_Elite"), "X-Factor")
        self.assertEqual(ls.dev_trait("Star"), "Star")
        self.assertEqual(ls.dev_trait("Superstar"), "Superstar")
        self.assertEqual(ls.dev_trait("XFactor"), "X-Factor")
        self.assertEqual(ls.dev_trait("Hidden"), "Hidden")
        self.assertIsNone(ls.dev_trait(None))

    def test_archetypes(self):
        self.assertEqual(ls.archetype("HB_PowerBack"), "Power Back")
        self.assertEqual(ls.archetype("CB_MantoMan"), "Man To Man")
        self.assertEqual(ls.archetype("DE_SmallerSpeedRusher"), "Speed Rusher")
        self.assertEqual(ls.archetype("QB_FieldGeneral"), "Field General")

    def test_height(self):
        self.assertEqual(ls.height_text(69), "5'9\"")
        self.assertEqual(ls.height_text(72), "6'0\"")
        self.assertIsNone(ls.height_text(None))

    def test_game_weeks(self):
        self.assertEqual(ls.game_week("RegularSeason", 0), (1, "REGULAR_SEASON"))
        self.assertEqual(ls.game_week("WildcardPlayoff", 18), (19, "WILD_CARD"))
        self.assertEqual(ls.game_week("SuperBowl", 22), (23, "SUPER_BOWL"))
        self.assertIsNone(ls.game_week("PreSeason", 1))

    def test_passer_rating(self):
        self.assertEqual(ls.passer_rating(22, 27, 264, 3, 0), 144.4)
        self.assertEqual(ls.passer_rating(20, 20, 400, 5, 0), 158.3)
        self.assertIsNone(ls.passer_rating(0, 0, 0, 0, 0))

    def test_group_lines_splits_a_returner_row(self):
        row = {"stats": {"RECEIVECATCHES": 3, "RECEIVEYARDS": 40, "RECEIVELONGEST": 22, "KRETATTEMPTS": 2,
                         "KRETYARDS": 50, "KRETLONGEST": 30, "DOWNSPLAYED": 31}}
        out = ls.group_lines([row])
        self.assertEqual(out["offense"]["receptions"], 3)
        self.assertEqual(out["offense"]["rec_avg"], 13.3)
        self.assertEqual(out["kickreturn"], {"returns": 2, "yards": 50, "tds": 0, "longest": 30, "average": 25.0})
        self.assertEqual(out["general"], {"downs_played": 31})
        self.assertNotIn("defense", out)

    def test_group_lines_sums_counters_and_keeps_longest(self):
        a = {"stats": {"RUSHATTEMPTS": 10, "RUSHYARDS": 40, "RUSHLONGEST": 12}}
        b = {"stats": {"RUSHATTEMPTS": 5, "RUSHYARDS": 30, "RUSHLONGEST": 25}}
        off = ls.group_lines([a, b])["offense"]
        self.assertEqual((off["rush_attempts"], off["rush_yards"], off["rush_long"], off["rush_avg"]), (15, 70, 25, 4.7))

    def test_defense_sacks_and_tackles(self):
        d = ls.group_lines([{"stats": {"DEFTACKLES": 5, "ASSDEFTACKLES": 2, "DLINESACKS": 1, "DLINEHALFSACK": 1}}])["defense"]
        self.assertEqual((d["solo"], d["assists"], d["tackles"], d["sacks"]), (5, 2, 7, 1.5))

    def test_team_box(self):
        box = ls.team_box({"FIRSTDOWNS": 17, "OFFYARDS": 405, "TOTALYARDS": 483, "RUSHATTEMPTS": 30, "PASSATTEMPTS": 38,
                           "SACKSALLOWED": 2, "OFFRUSHYARDS": 118, "OFFPASSYARDS": 287, "THIRDDOWNS": 18,
                           "THIRDDOWNCONV": 8, "GIVEAWAYS": 1, "OFFREDZONES": 4, "OFFREDZONETDS": 2, "OFFREDZONEFGS": 1,
                           "PENALTIES": 11, "PENALTYYARDS": 119, "POSSESSIONTIME": 2166})
        self.assertEqual((box["total_offense"], box["total_yards_gained"], box["rushing_yards"], box["passing_yards"]),
                         (405, 483, 118, 287))
        self.assertEqual((box["total_plays"], box["third_down_conversions"], box["turnovers"]), (70, 8, 1))
        self.assertEqual((box["red_zone_attempts"], box["red_zone_pct"], box["time_of_possession_s"]), (4, 75.0, 2166))
        self.assertIsNone(ls.team_box({})["red_zone_pct"])

    def test_line_team_is_opposite_the_opponent(self):
        game = {"home_team_index": 3, "away_team_index": 7}
        self.assertEqual(ls.line_team([{"opponent_team_index": 3}], game, 30, 70, 99), 70)
        self.assertEqual(ls.line_team([{"opponent_team_index": 7}], game, 30, 70, 99), 30)
        self.assertEqual(ls.line_team([{}], game, 30, 70, 99), 99)

    def test_wrong_game_year_stops(self):
        with self.assertRaises(ls.LoadError):
            ls.load_snapshot(None, {"meta": {"game_year": 26}, "season": {}})


# De'Von Achane's contract in CAREER-GIANTS-AUTOSAVE (cap salary 1377 in year 1).
ACHANE_CONTRACT = {"length": 5, "current_year": 1, "salary_by_year": [114, 1050, 1422, 1422, 1571, 0, 0, 0, 0, 0],
                   "bonus_by_year": [201, 327, 297, 297, 297, 0, 0], "no_trade_clause": False}


class SaveValuesTest(unittest.TestCase):
    def test_contract_values(self):
        v = ls.contract_values(ACHANE_CONTRACT, 2027)
        self.assertEqual(v["years"], {2027: 13_770_000, 2028: 17_190_000, 2029: 17_190_000, 2030: 18_680_000})
        self.assertEqual((v["length"], v["years_remaining"]), (5, 4))
        self.assertEqual(v["total_value"], 69_980_000)
        self.assertEqual(v["bonus"], 14_190_000)
        self.assertEqual(v["cut_penalty"], 12_180_000)          # 327 + 297 * 3 still to be charged
        self.assertEqual(v["cut_savings"], 1_590_000)           # 1377 - 1218

    def test_words(self):
        self.assertEqual(ls.words("KneeACLSprain"), "KNEE ACL SPRAIN")
        self.assertEqual(ls.words("BackStrain"), "BACK STRAIN")
        self.assertEqual(ls.words("HandFingerBroken"), "HAND FINGER BROKEN")

    def test_injury_values(self):
        self.assertEqual(ls.injury_values({"type": "BackStrain", "min_weeks": 0, "max_weeks": 1}),
                         ("BACK STRAIN", "WEEK TO WEEK", 1, False))
        self.assertEqual(ls.injury_values({"type": "AnkleHighSprain", "min_weeks": 2, "max_weeks": 4, "injured_reserve": True}),
                         ("ANKLE HIGH SPRAIN", "2 - 4 WEEKS", 4, True))
        self.assertEqual(ls.injury_values({"type": "X", "min_weeks": -1, "max_weeks": 3})[1:3], ("0 - 3 WEEKS", 3))
        healed_on_ir = {"status": "Uninjured", "type": "Invalid_", "min_weeks": -1, "max_weeks": 0, "injured_reserve": True}
        self.assertEqual(ls.injury_values(healed_on_ir), (None, "WEEK TO WEEK", 1, True))

    def test_scoring_highlights_add_up_with_conversions(self):
        game = tiny_snapshot()["games"][0]
        plays = ls.scoring_highlights(game, "CHI", "AZ")
        pts = lambda team: sum(p["points"] for p in plays if p["team"] == team)
        self.assertEqual((pts("CHI"), pts("AZ")), (21, 10))
        self.assertEqual([p["play_type"] for p in plays][:3], ["TOUCHDOWN", "EXTRA POINT", "FIELD GOAL"])
        self.assertEqual((plays[0]["quarter"], plays[0]["clock"]), ("Q1", "10:00"))

    def test_current_week(self):
        snap = tiny_snapshot()
        self.assertEqual(ls.current_week(snap), 2)
        snap["season"].update(week_type="SuperBowl", week_index=22)
        self.assertEqual(ls.current_week(snap), 23)
        snap["season"].update(week_type="OffSeason", week_index=0)
        self.assertIsNone(ls.current_week(snap))


def tiny_snapshot():
    """Two teams, one finished game and one scheduled, a QB, a WR/returner, a free agent and a draft prospect."""
    def player(pgid, first, last, pos, team, status, game_stats=(), season_stats=(), contract=None, injury=None, ratings=None):
        return {"pgid": pgid, "row": pgid, "first_name": first, "last_name": last, "position": pos,
                "archetype": "QB_FieldGeneral" if pos == "QB" else "WR_Slot", "status": status, "team_index": team,
                "age": 25, "years_pro": 3, "jersey": 9, "height_in": 74, "weight_lbs": 210, "dev_trait": "College_Star",
                "overall": 80, "ratings": ratings or {"Speed": 85, "Awareness": 80, "ThrowPower": 92},
                "game_stats": list(game_stats), "season_stats": list(season_stats),
                "contract": contract, "injury": injury or {"status": "Uninjured", "injured_reserve": False}}
    qb_line = {"kind": "offense", "game_row": 1, "opponent_team_index": 1,
               "stats": {"PASSCOMPLETED": 22, "PASSATTEMPTS": 27, "PASSYARDS": 264, "PASSTDS": 3, "DOWNSPLAYED": 65}}
    wr_line = {"kind": "off_return", "game_row": 1, "opponent_team_index": 1,
               "stats": {"RECEIVECATCHES": 6, "RECEIVEYARDS": 90, "RECEIVETDS": 2, "KRETATTEMPTS": 2, "KRETYARDS": 41}}
    qb_season = {"kind": "offense", "stats": {"SEAS_YEAR": 1, "PASSCOMPLETED": 22, "PASSATTEMPTS": 27, "PASSYARDS": 264,
                                              "PASSTDS": 3, "GAMESPLAYED": 1, "DOWNSPLAYED": 65}}
    old_season = {"kind": "offense", "stats": {"SEAS_YEAR": 0, "PASSYARDS": 4000}}
    return {
        "meta": {"game_year": 27},
        "season": {"calendar_year": 2027, "franchise_year": 1, "week_index": 1, "week_type": "RegularSeason", "stage": "NFLSeason"},
        "teams": [{"team_index": 0, "short_name": "CHI"}, {"team_index": 1, "short_name": "ARI"},
                  {"team_index": 32, "short_name": "FA"}],
        "games": [
            {"row": 1, "franchise_year": 1, "week_type": "RegularSeason", "week_index": 0, "status": "HomeWon",
             "home_team_index": 0, "away_team_index": 1, "home_score": 21, "away_score": 10,
             "home_quarters": [7, 7, 0, 7], "away_quarters": [3, 0, 7, 0], "home_ot": 0, "away_ot": 0,
             "home_team_stats": {"FIRSTDOWNS": 19, "OFFYARDS": 351, "OFFPASSYARDS": 251, "OFFRUSHYARDS": 100,
                                 "PASSATTEMPTS": 27, "PASSTDS": 3, "RUSHATTEMPTS": 24, "GIVEAWAYS": 0},
             "scoring_plays": [
                 {"quarter": 1, "seconds_left": 600, "home_before": 0, "away_before": 0, "home_after": 6, "away_after": 0},
                 {"quarter": 1, "seconds_left": 120, "home_before": 7, "away_before": 0, "home_after": 7, "away_after": 3},
                 {"quarter": 2, "seconds_left": 30, "home_before": 7, "away_before": 3, "home_after": 13, "away_after": 3},
                 {"quarter": 3, "seconds_left": 400, "home_before": 14, "away_before": 3, "home_after": 14, "away_after": 9},
                 {"quarter": 4, "seconds_left": 65, "home_before": 14, "away_before": 10, "home_after": 20, "away_after": 10}]},
            {"row": 2, "franchise_year": 1, "week_type": "RegularSeason", "week_index": 1, "status": "Unplayed",
             "home_team_index": 1, "away_team_index": 0, "home_score": 0, "away_score": 0,
             "home_quarters": [0, 0, 0, 0], "away_quarters": [0, 0, 0, 0], "home_ot": 0, "away_ot": 0},
            {"row": 3, "franchise_year": 1, "week_type": "PreSeason", "week_index": 0, "status": "HomeWon",
             "home_team_index": 0, "away_team_index": 1, "home_score": 3, "away_score": 0,
             "home_quarters": [3, 0, 0, 0], "away_quarters": [0, 0, 0, 0], "home_ot": 0, "away_ot": 0},
        ],
        "players": [
            player(900001, "Test", "Passer", "QB", 0, "Signed", [qb_line], [qb_season, old_season], contract=ACHANE_CONTRACT),
            player(900002, "Test", "Catcher", "WR", 0, "Signed", [wr_line],
                   injury={"status": "Injured", "type": "KneeACLSprain", "min_weeks": 2, "max_weeks": 4,
                           "injured_reserve": False}),
            player(900003, "Free", "Agent", "WR", 32, "FreeAgent"),
            player(900004, "Draft", "Prospect", "QB", 32, "Draft"),
        ],
    }


@pytest.mark.integration
class LoadTinySnapshotTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(ls.DSN)
        self.cur = self.conn.cursor()
        self.cur.execute(PGID_SQL)

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def one(self, sql, *args):
        self.cur.execute(sql, args)
        return self.cur.fetchone()

    def test_load(self):
        r = ls.load_snapshot(self.cur, tiny_snapshot(), season=TEST_SEASON)
        self.assertEqual((r["players"], r["players_created"]), (3, 3))          # the prospect isn't loaded
        self.assertEqual(r["player_attributes"], 3)
        self.assertEqual((r["games"], r["games_completed"]), (2, 1))             # preseason skipped
        self.assertEqual(r["team_stat_rows"], 2)

        qb = self.one("SELECT id, player_name, full_name, height, origin FROM players WHERE madden_pgid = 900001")
        self.assertEqual(qb[1:], ("T. Passer", "Test Passer", "6'2\"", "SAVE"))
        pa = self.one("SELECT overall_rating, confirmed, flags, ratings FROM player_attributes "
                      "WHERE player_id = %s AND season = %s", qb[0], TEST_SEASON)
        self.assertEqual(pa[:3], (80, 3, "native_save"))
        self.assertEqual(pa[3], {"Speed": 85, "Awareness": 80, "ThrowPower": 92})
        self.assertEqual(r["player_attributes_in_season_changes"], 0)
        ps = self.one("SELECT t.abbreviation, position, dev_trait, archetype, years_of_experience FROM player_seasons p "
                      "JOIN teams t ON t.id = p.team_id WHERE player_id = %s AND season = %s", qb[0], TEST_SEASON)
        self.assertEqual(ps, ("CHI", "QB", "Superstar", "Field General", 3))
        fa = self.one("SELECT team_id FROM player_seasons p JOIN players x ON x.id = p.player_id "
                      "WHERE x.madden_pgid = 900003 AND season = %s", TEST_SEASON)
        self.assertEqual(fa, (None,))

        game = self.one("SELECT g.id, g.week, g.status, g.home_score, g.away_score, h.abbreviation, a.abbreviation "
                        "FROM games g JOIN teams h ON h.id = g.home_team_id JOIN teams a ON a.id = g.away_team_id "
                        "WHERE g.season = %s AND g.week = 1", TEST_SEASON)
        self.assertEqual(game[1:], (1, "COMPLETED", 21, 10, "CHI", "AZ"))
        self.assertEqual(self.one("SELECT status FROM games WHERE season = %s AND week = 2", TEST_SEASON), ("SCHEDULED",))

        line = self.one("SELECT completions, pass_yards, pass_tds, pass_rating, line_source, t.abbreviation "
                        "FROM player_stats s JOIN teams t ON t.id = s.team_id WHERE game_id = %s AND player_id = %s",
                        game[0], qb[0])
        self.assertEqual(line, (22, 264, 3, Decimal("144.4"), "CAPTURED", "CHI"))
        kr = self.one("SELECT returns, yards FROM game_stat_kickreturn k JOIN players x ON x.id = k.player_id "
                      "WHERE game_id = %s AND x.madden_pgid = 900002", game[0])
        self.assertEqual(kr, (2, 41))
        self.assertEqual(self.one("SELECT downs_played FROM game_stat_general WHERE game_id = %s AND player_id = %s",
                                  game[0], qb[0]), (65,))

        home = self.one("SELECT score, q1, q2, q3, q4, ot, passing_completions, passing_tds FROM team_stats "
                        "WHERE game_id = %s AND team_name = (SELECT name FROM teams WHERE abbreviation = 'CHI')", game[0])
        self.assertEqual(home, (21, 7, 7, 0, 7, 0, 22, 3))
        box = self.one("SELECT total_offense, passing_yards, rushing_yards, first_downs FROM team_stats "
                       "WHERE game_id = %s AND team_name = (SELECT name FROM teams WHERE abbreviation = 'CHI')", game[0])
        self.assertEqual(box, (351, 251, 100, 19))                               # from Madden's team box score
        away = self.one("SELECT passing_attempts, total_offense FROM team_stats WHERE game_id = %s "
                        "AND team_name = (SELECT name FROM teams WHERE abbreviation = 'AZ')", game[0])
        self.assertEqual(away, (0, 0))                                           # no box score: line totals only

        season = self.one("SELECT pass_yards, pass_tds, source FROM season_stats WHERE season = %s AND player_id = %s",
                          TEST_SEASON, qb[0])
        self.assertEqual(season, (264, 3, "CAPTURE"))                           # the year-0 row isn't this season

        contract = self.one("SELECT id, years_remaining, total_value, cut_penalty, status, source FROM player_contracts "
                            "WHERE player_id = %s AND season = %s", qb[0], TEST_SEASON)
        self.assertEqual(contract[1:], (4, 69_980_000, 12_180_000, "ACTIVE", "SCAN"))
        self.cur.execute("SELECT year, cap_hit FROM player_contract_years WHERE contract_id = %s ORDER BY year", (contract[0],))
        self.assertEqual(self.cur.fetchall()[0], (TEST_SEASON, 13_770_000))

        inj = self.one("SELECT week, injury_type, duration, weeks_remaining, t.abbreviation FROM injuries i "
                       "JOIN teams t ON t.id = i.team_id WHERE season = %s AND player_name = 'T. Catcher'", TEST_SEASON)
        self.assertEqual(inj, (2, "KNEE ACL SPRAIN", "2 - 4 WEEKS", 4, "CHI"))
        self.assertEqual(self.one("SELECT count(*) FROM injuries WHERE season = %s", TEST_SEASON), (1,))

        hl = self.one("SELECT highlights FROM game_highlights WHERE game_id = %s", game[0])[0]
        self.assertEqual(sum(p["points"] for p in hl if p["team"] == "CHI"), 21)
        self.assertEqual(sum(p["points"] for p in hl if p["team"] == "AZ"), 10)
        self.assertTrue(r["league_state"].startswith("kept"))                   # 2099 isn't the league's season

    def test_league_state_moves_forward_only(self):
        self.cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
        season, week = self.cur.fetchone()
        report = {}
        ls.update_league_state(self.cur, season, week + 1, report)
        self.assertEqual(self.one("SELECT current_week FROM league_state WHERE id = 1"), (week + 1,))
        ls.update_league_state(self.cur, season, week, report)
        self.assertEqual(self.one("SELECT current_week FROM league_state WHERE id = 1"), (week + 1,))
        ls.update_league_state(self.cur, season + 1, 1, report)
        self.assertEqual(self.one("SELECT current_season, current_week FROM league_state WHERE id = 1"), (season + 1, 1))
        ls.update_league_state(self.cur, season + 5, 3, report)
        self.assertEqual(self.one("SELECT current_season FROM league_state WHERE id = 1"), (season + 1,))

    def test_captured_highlights_are_kept(self):
        snap = tiny_snapshot()
        ls.load_snapshot(self.cur, snap, season=TEST_SEASON)
        gid = self.one("SELECT id FROM games WHERE season = %s AND week = 1", TEST_SEASON)[0]
        real = [{"quarter": "Q1", "clock": "9:12", "play_type": "PASS", "play": "A REAL CAPTURED PLAY"}]
        self.cur.execute("UPDATE game_highlights SET highlights = %s WHERE game_id = %s", (json.dumps(real), gid))
        ls.load_snapshot(self.cur, snap, season=TEST_SEASON)
        self.assertEqual(self.one("SELECT highlights FROM game_highlights WHERE game_id = %s", gid)[0], real)

    def test_reload_is_idempotent(self):
        snap = tiny_snapshot()
        ls.load_snapshot(self.cur, snap, season=TEST_SEASON)
        r = ls.load_snapshot(self.cur, snap, season=TEST_SEASON)
        self.assertEqual(r["players_created"], 0)
        self.assertEqual(self.one("SELECT count(*) FROM player_stats s JOIN games g ON g.id = s.game_id "
                                  "WHERE g.season = %s", TEST_SEASON), (2,))
        self.assertEqual(self.one("SELECT count(*) FROM games WHERE season = %s", TEST_SEASON), (2,))

    def attribute_rows(self, pgid):
        self.cur.execute("SELECT a.season, a.capture_folder, a.flags, a.ratings FROM player_attributes a "
                         "JOIN players p ON p.id = a.player_id WHERE p.madden_pgid = %s ORDER BY a.id", (pgid,))
        return self.cur.fetchall()

    def test_attributes_written_only_when_ratings_change(self):
        snap = tiny_snapshot()
        ls.load_snapshot(self.cur, snap, season=TEST_SEASON, capture_folder="week1")

        # Next week's capture, ratings unchanged: nothing new
        r = ls.load_snapshot(self.cur, snap, season=TEST_SEASON, capture_folder="week2")
        self.assertEqual((r["player_attributes"], r["player_attributes_unchanged"]), (0, 3))
        self.assertEqual(len(self.attribute_rows(900001)), 1)

        # A rating moves mid-season: one new row for that player, flagged
        changed = tiny_snapshot()
        changed["players"][0]["ratings"] = {"Speed": 84, "Awareness": 80, "ThrowPower": 92}
        r = ls.load_snapshot(self.cur, changed, season=TEST_SEASON, capture_folder="week3")
        self.assertEqual((r["player_attributes"], r["player_attributes_in_season_changes"]), (1, 1))
        rows = self.attribute_rows(900001)
        self.assertEqual([(f, x) for _, f, x, _ in rows], [("week1", "native_save"), ("week3", "in_season_change")])

        # Reloading a stored capture rewrites its own row, compared with what came before it
        ls.load_snapshot(self.cur, snap, season=TEST_SEASON, capture_folder="week1")
        r = ls.load_snapshot(self.cur, changed, season=TEST_SEASON, capture_folder="week3")
        self.assertEqual(r["player_attributes_in_season_changes"], 1)
        self.assertEqual(len(self.attribute_rows(900001)), 2)

        # New season: a baseline row even with the same ratings
        r = ls.load_snapshot(self.cur, changed, season=TEST_SEASON + 1, capture_folder="next_season")
        self.assertEqual(r["player_attributes_in_season_changes"], 0)
        self.assertEqual(self.attribute_rows(900001)[-1][:3], (TEST_SEASON + 1, "next_season", "native_save"))


@pytest.mark.integration
@unittest.skipUnless(os.path.exists(SAMPLE_SAVE) and shutil.which("node")
                     and os.path.isdir(os.path.join(ROOT, "pipeline", "save_reader", "node_modules")),
                     "needs the sample save, node and `npm install` in pipeline/save_reader")
class LoadSampleSaveTest(unittest.TestCase):
    """Reads the real sample save and loads it as a far-off season, rolled back."""

    @classmethod
    def setUpClass(cls):
        out = subprocess.run(["node", READER, SAMPLE_SAVE], capture_output=True, text=True, check=True, timeout=300)
        cls.snapshot = json.loads(out.stdout)

    def setUp(self):
        self.conn = psycopg.connect(ls.DSN)
        self.cur = self.conn.cursor()
        self.cur.execute(PGID_SQL)

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_season_totals_match_the_game_lines(self):
        r = ls.load_snapshot(self.cur, self.snapshot, season=TEST_SEASON)
        self.assertGreater(r["game_line_rows"], 1000)
        # In this save the season rows cover the same games as the per-game rows, so they must agree.
        self.cur.execute("""
            SELECT s.player_id, s.pass_yards, s.rush_yards, s.rec_yards,
                   COALESCE(SUM(l.pass_yards), 0), COALESCE(SUM(l.rush_yards), 0), COALESCE(SUM(l.rec_yards), 0)
            FROM season_stats s
            LEFT JOIN player_stats l ON l.player_id = s.player_id
                 AND l.game_id IN (SELECT id FROM games WHERE season = %s)
            WHERE s.season = %s
            GROUP BY s.player_id, s.pass_yards, s.rush_yards, s.rec_yards""", (TEST_SEASON, TEST_SEASON))
        rows = self.cur.fetchall()
        self.assertGreater(len(rows), 300)
        off = [row for row in rows if row[1:4] != row[4:7]]
        self.assertEqual(off, [], f"{len(off)} players' season totals differ from their game lines")

    def test_scoring_plays_add_up_to_the_final(self):
        ls.load_snapshot(self.cur, self.snapshot, season=TEST_SEASON)
        self.cur.execute("""
            SELECT g.id, g.home_score, g.away_score, h.abbreviation, a.abbreviation, gh.highlights
            FROM game_highlights gh JOIN games g ON g.id = gh.game_id
            JOIN teams h ON h.id = g.home_team_id JOIN teams a ON a.id = g.away_team_id WHERE g.season = %s""",
                         (TEST_SEASON,))
        rows = self.cur.fetchall()
        self.assertGreater(len(rows), 10)
        bad = [r[0] for r in rows
               if (sum(p["points"] for p in r[5] if p["team"] == r[3]), sum(p["points"] for p in r[5] if p["team"] == r[4]))
               != (r[1], r[2])]
        self.assertEqual(bad, [], f"{len(bad)} games' scoring plays don't add up to the final")

    def test_contract_cap_hit_is_the_save_cap_salary(self):
        """Contracts past their first year: this season's cap hit is PLYR_CAPSALARY for nearly all (a first-year
        contract often isn't; see load_contracts)."""
        r = ls.load_snapshot(self.cur, self.snapshot, season=TEST_SEASON)
        self.assertIsInstance(r["contracts_cap_hit_differs"], int)
        want = {p["pgid"]: p["cap_salary"] * ls.MONEY_UNIT for p in self.snapshot["players"]
                if p["status"] in ("Signed", "PracticeSquad") and p.get("contract")
                and p["contract"].get("length") and p["contract"]["current_year"] >= 1}
        self.cur.execute("""
            SELECT x.madden_pgid, y.cap_hit FROM player_contracts c JOIN players x ON x.id = c.player_id
            JOIN player_contract_years y ON y.contract_id = c.id AND y.year = c.season
            WHERE c.season = %s AND c.status = 'ACTIVE'""", (TEST_SEASON,))
        got = {k: v for k, v in self.cur.fetchall() if k in want}
        self.assertGreater(len(got), 500)
        off = [k for k in got if got[k] != want[k]]
        self.assertLess(len(off) / len(got), 0.05, f"{len(off)} of {len(got)} cap hits differ from the save's cap salary")

    def test_team_box_scores_match_the_game_lines(self):
        """Every completed game has Madden's team box score; its rushing yards and attempts equal the team's player
        lines wherever Madden has linked them."""
        ls.load_snapshot(self.cur, self.snapshot, season=TEST_SEASON)
        self.cur.execute("""
            SELECT count(*) FILTER (WHERE t.total_offense = 0) FROM team_stats t JOIN games g ON g.id = t.game_id
            WHERE g.season = %s AND g.status = 'COMPLETED'""", (TEST_SEASON,))
        self.assertEqual(self.cur.fetchone(), (0,))
        self.cur.execute("""
            SELECT t.game_id, t.team_name FROM team_stats t JOIN games g ON g.id = t.game_id JOIN teams tm ON tm.name = t.team_name
            JOIN (SELECT game_id, team_id, SUM(rush_yards) ry, SUM(rush_attempts) ra FROM player_stats GROUP BY 1, 2) l
              ON l.game_id = t.game_id AND l.team_id = tm.id
            WHERE g.season = %s AND (l.ry, l.ra) <> (t.rushing_yards, t.rushing_attempts)""", (TEST_SEASON,))
        self.assertEqual(self.cur.fetchall(), [])

    def test_every_completed_game_scores_add_up(self):
        ls.load_snapshot(self.cur, self.snapshot, season=TEST_SEASON)
        self.cur.execute("""
            SELECT g.id FROM games g JOIN team_stats t ON t.game_id = g.id
            WHERE g.season = %s AND g.status = 'COMPLETED' AND t.score <> t.q1 + t.q2 + t.q3 + t.q4 + t.ot""",
                         (TEST_SEASON,))
        self.assertEqual(self.cur.fetchall(), [])

    def test_player_attributes_loaded(self):
        r = ls.load_snapshot(self.cur, self.snapshot, season=TEST_SEASON)
        self.assertGreater(r["player_attributes"], 1500)
        self.cur.execute("SELECT count(*) FROM player_attributes WHERE season = %s", (TEST_SEASON,))
        self.assertGreater(self.cur.fetchone()[0], 1500)
