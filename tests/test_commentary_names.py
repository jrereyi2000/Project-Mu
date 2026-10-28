"""Tests for pipeline/commentary_names.py (Madden commentary surname lookup & rename tool)."""

import csv
import json
import os
import tempfile
import unittest
from unittest import mock
import psycopg
import pytest

from pipeline import commentary_names as cn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "host=localhost dbname=madden_test user=postgres password=postgres",
)


_SAVE_PATH_PATCH = mock.patch.object(cn, "DEFAULT_SAVE_COMMENTARY_PATH", "/nonexistent/commentary_save.json")


def setUpModule():
    # Tests read the community file alone, not this machine's git-ignored save harvest.
    _SAVE_PATH_PATCH.start()
    cn._COMMENTARY_RAW = None


def tearDownModule():
    _SAVE_PATH_PATCH.stop()
    cn._COMMENTARY_RAW = None


def _lookup(raw: dict) -> dict:
    lookup: dict = {}
    cn._add_entries(lookup, raw, override=False)
    return lookup


class TestNormalization(unittest.TestCase):
    def test_suffix_stripping(self):
        self.assertEqual(cn.drop_suffixes("Kevin Winston Jr."), "Kevin Winston")
        self.assertEqual(cn.drop_suffixes("Kevin Winston Jr"), "Kevin Winston")
        self.assertEqual(cn.drop_suffixes("Kevin Winston, Jr."), "Kevin Winston")
        self.assertEqual(cn.drop_suffixes("Luther Burden III"), "Luther Burden")
        self.assertEqual(cn.drop_suffixes("Byron Murphy II"), "Byron Murphy")
        self.assertEqual(cn.drop_suffixes("Tommy Doman IV"), "Tommy Doman")
        self.assertEqual(cn.drop_suffixes("Marcus Jones V"), "Marcus Jones")
        self.assertEqual(cn.drop_suffixes("Frank Atkinson Sr."), "Frank Atkinson")
        self.assertEqual(cn.drop_suffixes("Frank Atkinson Sr"), "Frank Atkinson")
        self.assertEqual(cn.drop_suffixes("Ordinary Name"), "Ordinary Name")

    def test_accent_stripping(self):
        self.assertEqual(cn.strip_accents("José"), "Jose")
        self.assertEqual(cn.strip_accents("Alégre"), "Alegre")
        self.assertEqual(cn.strip_accents("Müller"), "Muller")
        self.assertEqual(cn.strip_accents("Peña"), "Pena")

    def test_normalize_surname(self):
        self.assertEqual(cn.normalize("Smith Jr."), "smith")
        self.assertEqual(cn.normalize("O'Brien"), "obrien")
        self.assertEqual(cn.normalize("Abdul-Quddus"), "abdul-quddus")
        self.assertEqual(cn.normalize("Müller III"), "muller")
        self.assertEqual(cn.normalize("  VAN BUREN  "), "van buren")

    def test_extract_surname_candidates(self):
        self.assertEqual(cn.extract_surname_candidates("Smith"), ["Smith"])
        self.assertEqual(cn.extract_surname_candidates("Travis Kelce"), ["Kelce"])
        self.assertEqual(cn.extract_surname_candidates("Kevin Winston Jr."), ["Winston"])
        self.assertEqual(cn.extract_surname_candidates("Steven Van Buren"), ["Van Buren", "Buren"])
        self.assertEqual(cn.extract_surname_candidates("Dani Dennis-Sutton"), ["Dennis-Sutton", "Sutton"])
        self.assertEqual(cn.extract_surname_candidates("R Mason Thomas"), ["Mason Thomas", "Thomas"])


class TestCommentaryLookup(unittest.TestCase):
    def test_known_surnames(self):
        self.assertEqual(cn.commentary_id("Smith"), 4600)
        self.assertEqual(cn.commentary_id("Johnson"), 2506)
        self.assertEqual(cn.commentary_id("Crawford"), 1087)
        self.assertEqual(cn.commentary_id("Travis Kelce"), 2579)
        self.assertEqual(cn.commentary_id("Abdul-Quddus"), 5)
        self.assertEqual(cn.commentary_id("O'Brien"), 3662)
        self.assertEqual(cn.commentary_id("Steven Van Buren"), 5126)
        self.assertEqual(cn.commentary_id("Quentin St. Louis"), 4682)

    def test_case_folding_and_suffixes(self):
        self.assertEqual(cn.commentary_id("smith"), 4600)
        self.assertEqual(cn.commentary_id("SMITH"), 4600)
        self.assertEqual(cn.commentary_id("Will Smith Jr."), 4600)
        self.assertEqual(cn.commentary_id("Luther Burden III"), 8065)
        self.assertEqual(cn.commentary_id("Kevin Winston Jr"), 5366)

    def test_unmatched_surname_returns_none(self):
        self.assertIsNone(cn.commentary_id("NonexistentFakeSurname999"))
        self.assertIsNone(cn.commentary_id(""))
        self.assertIsNone(cn.commentary_id(None))

    def test_missing_file_raises_filenotfound(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            cn.load_commentary_lookup("/nonexistent/path/to/commentary.json")
        self.assertIn("curl", str(ctx.exception))

    def test_suffixed_surname_with_own_recording(self):
        lookup = _lookup({"Samuel": 4317, "Samuel Jr": 8440, "Smith": 4600})
        self.assertEqual(cn.commentary_id("Asante Samuel Jr", lookup), 8440)
        self.assertEqual(cn.commentary_id("Deebo Samuel Sr", lookup), 4317)
        self.assertEqual(cn.commentary_id("Will Smith Jr.", lookup), 4600)


class TestSaveHarvest(unittest.TestCase):
    PLAYERS = [
        {"first_name": "Gabe", "last_name": "Jacas", "appearance": {"commentary_id": 9325}},
        {"first_name": "Derion", "last_name": "Kendrick", "appearance": {"commentary_id": 499}},
        {"first_name": "Kaleb", "last_name": "Elarms-Orr", "appearance": {"commentary_id": 9330}},
        {"first_name": "Will", "last_name": "Smith", "appearance": {"commentary_id": 4600}},
        {"first_name": "Jo", "last_name": "Nobody", "appearance": {"commentary_id": 0}},
    ]

    def test_harvest_skips_zero_and_keeps_save_ids(self):
        self.assertEqual(cn.harvest_save_ids(self.PLAYERS),
                         {"Elarms-Orr": 9330, "Jacas": 9325, "Kendrick": 499, "Smith": 4600})

    def test_save_ids_take_precedence_over_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path, save_path = os.path.join(tmp, "file.json"), os.path.join(tmp, "save.json")
            with open(file_path, "w") as f:
                json.dump({"Kendrick": 2592, "Orr": 3739, "Smith": 4600}, f)
            with open(save_path, "w") as f:
                json.dump(cn.harvest_save_ids(self.PLAYERS), f)
            file_only = cn.load_commentary_lookup(path=file_path)
            layered = cn.load_commentary_lookup(path=file_path, save_path=save_path)
        self.assertIsNone(cn.commentary_id("Gabe Jacas", file_only))
        self.assertEqual(cn.commentary_id("Gabe Jacas", layered), 9325)
        self.assertEqual(cn.commentary_id("Derion Kendrick", layered), 499)
        self.assertEqual(cn.commentary_id("Kaleb Elarms-Orr", file_only), 3739)
        self.assertEqual(cn.commentary_id("Kaleb Elarms-Orr", layered), 9330)

    def test_validate_lookup_buckets(self):
        lookup = _lookup({"Kendrick": 2592, "Smith": 4600, "Nobody": 7})
        result = cn.validate_lookup(self.PLAYERS, lookup)
        self.assertEqual([r[0] for r in result["agree"]], ["Will Smith"])
        self.assertEqual(result["disagree"], [("Derion Kendrick", 499, 2592)])
        self.assertEqual(len(result["missing"]), 2)
        self.assertEqual(result["file_only"], [("Jo Nobody", 7)])


class TestCandidateRanking(unittest.TestCase):
    def test_levenshtein_distance(self):
        self.assertEqual(cn.levenshtein_distance("kitten", "sitting"), 3)
        self.assertEqual(cn.levenshtein_distance("Smith", "Smith"), 0)
        self.assertEqual(cn.levenshtein_distance("Smith", "Smyth"), 1)

    def test_suggest_surnames_prefers_phonetic_and_first_letter(self):
        # Josephs should suggest Joseph
        suggestions = cn.suggest_surnames("Josephs", top_n=5)
        self.assertIn("Joseph", suggestions)
        # All top suggestions should start with 'J' (prefer same first letter)
        for s in suggestions:
            self.assertTrue(s.startswith("J") or s.startswith("j"))

    def test_suggest_surnames_avoids_team_surnames(self):
        # Without team restriction, 'Joseph' is top candidate for 'Josephs'
        suggestions_no_team = cn.suggest_surnames("Josephs", team_surnames=set(), top_n=5)
        self.assertEqual(suggestions_no_team[0], "Joseph")

        # When 'Joseph' is on the same team, it must be excluded
        suggestions_with_team = cn.suggest_surnames("Josephs", team_surnames={"Joseph"}, top_n=5)
        self.assertNotIn("Joseph", suggestions_with_team)

    def test_build_renamed_names(self):
        full, short = cn.build_renamed_names("Joshua Josephs", "J. Josephs", "Joseph")
        self.assertEqual(full, "Joshua Joseph")
        self.assertEqual(short, "J. Joseph")

        # Suffix preservation
        full, short = cn.build_renamed_names("Kevin Winston Jr.", "K. Winston Jr.", "Smith")
        self.assertEqual(full, "Kevin Smith Jr.")
        self.assertEqual(short, "K. Smith Jr.")


@pytest.mark.integration
class TestDatabaseApplyIntegration(unittest.TestCase):
    def setUp(self):
        # Connect to madden_test and insert a temporary test player
        self.test_player_id = 999988
        with psycopg.connect(TEST_DSN) as conn:
            with conn.cursor() as cur:
                # Ensure clean start
                cur.execute("DELETE FROM player_name_aliases WHERE player_id = %s;", (self.test_player_id,))
                cur.execute("DELETE FROM players WHERE id = %s;", (self.test_player_id,))
                cur.execute(
                    """
                    INSERT INTO players (id, full_name, player_name, height, weight, player_number)
                    VALUES (%s, 'Tester Josephs', 'T. Josephs', '6''2\"', 230, 99);
                    """,
                    (self.test_player_id,),
                )
            conn.commit()

    def tearDown(self):
        with psycopg.connect(TEST_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM player_name_aliases WHERE player_id = %s;", (self.test_player_id,))
                cur.execute("DELETE FROM players WHERE id = %s;", (self.test_player_id,))
            conn.commit()

    def test_dry_run_does_not_modify_database(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            writer = csv.writer(f)
            writer.writerow(["player_id", "name", "team", "pos", "ovr", "candidate_1", "candidate_2", "candidate_3", "candidate_4", "candidate_5", "chosen"])
            writer.writerow([str(self.test_player_id), "Tester Josephs", "SF", "LB", "85", "Joseph", "Jones", "James", "Judd", "Jack", "Joseph"])
            csv_path = f.name

        try:
            res = cn.run_apply(csv_path, dsn=TEST_DSN, commit=False)
            self.assertEqual(res["applied"], 1)
            self.assertFalse(res["committed"])

            # Verify player is unchanged in database
            with psycopg.connect(TEST_DSN) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT full_name, player_name FROM players WHERE id = %s;", (self.test_player_id,))
                    row = cur.fetchone()
                    self.assertEqual(row, ("Tester Josephs", "T. Josephs"))

                    # Verify no alias was written
                    cur.execute("SELECT COUNT(*) FROM player_name_aliases WHERE player_id = %s;", (self.test_player_id,))
                    count = cur.fetchone()[0]
                    self.assertEqual(count, 0)
        finally:
            if os.path.exists(csv_path):
                os.remove(csv_path)

    def test_commit_updates_player_and_creates_alias(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            writer = csv.writer(f)
            writer.writerow(["player_id", "name", "team", "pos", "ovr", "candidate_1", "candidate_2", "candidate_3", "candidate_4", "candidate_5", "chosen"])
            writer.writerow([str(self.test_player_id), "Tester Josephs", "SF", "LB", "85", "Joseph", "Jones", "James", "Judd", "Jack", "Joseph"])
            csv_path = f.name

        try:
            res = cn.run_apply(csv_path, dsn=TEST_DSN, commit=True)
            self.assertEqual(res["applied"], 1)
            self.assertTrue(res["committed"])

            # Verify player was updated in database
            with psycopg.connect(TEST_DSN) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT full_name, player_name FROM players WHERE id = %s;", (self.test_player_id,))
                    row = cur.fetchone()
                    self.assertEqual(row, ("Tester Joseph", "T. Joseph"))

                    # Verify alias was written
                    cur.execute("SELECT alias, kind FROM player_name_aliases WHERE player_id = %s;", (self.test_player_id,))
                    aliases = dict(cur.fetchall())
                    self.assertIn("Tester Josephs", aliases)
                    self.assertEqual(aliases["Tester Josephs"], "commentary_rename")

                    # Verify player search finds the player by old alias
                    pattern = "%Tester Josephs%"
                    cur.execute(
                        """
                        SELECT id FROM players p
                        WHERE p.full_name ILIKE %s
                           OR p.player_name ILIKE %s
                           OR EXISTS (
                               SELECT 1 FROM player_name_aliases pna
                               WHERE pna.player_id = p.id AND pna.alias ILIKE %s
                           );
                        """,
                        (pattern, pattern, pattern),
                    )
                    found = [r[0] for r in cur.fetchall()]
                    self.assertIn(self.test_player_id, found)
        finally:
            if os.path.exists(csv_path):
                os.remove(csv_path)


@pytest.mark.integration
class TestPlayerNameAliasesGuard(unittest.TestCase):
    def test_caching_and_check(self):
        cn._ALIASES_TABLE_EXISTS = None
        mock_cur = mock.MagicMock()
        mock_cur.fetchone.return_value = (False,)
        self.assertFalse(cn.player_name_aliases_exist(mock_cur))
        self.assertIsNone(cn._ALIASES_TABLE_EXISTS)

        mock_cur.fetchone.return_value = (True,)
        self.assertTrue(cn.player_name_aliases_exist(mock_cur))
        self.assertTrue(cn._ALIASES_TABLE_EXISTS)

        # Cached - does not execute again
        mock_cur.execute.reset_mock()
        self.assertTrue(cn.player_name_aliases_exist(mock_cur))
        mock_cur.execute.assert_not_called()

        # Force check bypasses cache
        self.assertTrue(cn.player_name_aliases_exist(mock_cur, force_check=True))
        mock_cur.execute.assert_called_once()
        cn._ALIASES_TABLE_EXISTS = None

    def test_apply_requires_table_with_helpful_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("player_id,name,team,pos,ovr,candidate_1,candidate_2,candidate_3,candidate_4,candidate_5,chosen\n")
            f.write("1,Test Player,CAR,QB,90,Smith,Jones,Brown,Davis,Wilson,Smith\n")
            csv_path = f.name
        try:
            with mock.patch("pipeline.commentary_names.player_name_aliases_exist", return_value=False):
                with self.assertRaises(RuntimeError) as ctx:
                    cn.run_apply(csv_path, dsn=TEST_DSN, commit=False)
                self.assertIn("apply db/migrations/2027_01_07_player_name_aliases.sql first", str(ctx.exception))
        finally:
            if os.path.exists(csv_path):
                os.remove(csv_path)

    def test_search_endpoints_query_building_absent_and_present(self):
        # 1. Test search_global query building when table is absent
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = []
        mock_cur.fetchone.return_value = {"total": 0, "current_season": 2036, "current_week": 1}

        with mock.patch("server.api.player_name_aliases_exist", return_value=False):
            with mock.patch("server.api.db_pool.connection") as mock_conn_ctx:
                mock_conn = mock.MagicMock()
                mock_conn.cursor.return_value.__enter__.return_value = mock_cur
                mock_conn_ctx.return_value.__enter__.return_value = mock_conn

                from server.api import search_global
                search_global("smith")
                sql = mock_cur.execute.call_args[0][0]
                self.assertNotIn("player_name_aliases", sql)

        # 2. Test search_global query building when table is present
        mock_cur.reset_mock()
        with mock.patch("server.api.player_name_aliases_exist", return_value=True):
            with mock.patch("server.api.db_pool.connection") as mock_conn_ctx:
                mock_conn = mock.MagicMock()
                mock_conn.cursor.return_value.__enter__.return_value = mock_cur
                mock_conn_ctx.return_value.__enter__.return_value = mock_conn

                from server.api import search_global
                search_global("smith")
                sql = mock_cur.execute.call_args[0][0]
                self.assertIn("player_name_aliases", sql)

        # 3. Test napper available_players when absent
        mock_cur.reset_mock()
        with mock.patch("server.napper_api.player_name_aliases_exist", return_value=False):
            with mock.patch("server.napper_api.get_db") as mock_get_db:
                mock_conn = mock.MagicMock()
                mock_conn.cursor.return_value.__enter__.return_value = mock_cur
                mock_get_db.return_value.__enter__.return_value = mock_conn

                from server.napper_api import get_available_players
                get_available_players(0, search="smith", position="ALL", limit=10, offset=0)
                sqls = [c[0][0] for c in mock_cur.execute.call_args_list]
                self.assertFalse(any("player_name_aliases" in s for s in sqls))

        # 4. Test napper available_players when present
        mock_cur.reset_mock()
        with mock.patch("server.napper_api.player_name_aliases_exist", return_value=True):
            with mock.patch("server.napper_api.get_db") as mock_get_db:
                mock_conn = mock.MagicMock()
                mock_conn.cursor.return_value.__enter__.return_value = mock_cur
                mock_get_db.return_value.__enter__.return_value = mock_conn

                from server.napper_api import get_available_players
                get_available_players(0, search="smith", position="ALL", limit=10, offset=0)
                sqls = [c[0][0] for c in mock_cur.execute.call_args_list]
                self.assertTrue(any("player_name_aliases" in s for s in sqls))

    def test_find_player_by_alias_integration(self):
        import psycopg
        class _Rollback(Exception):
            pass

        with psycopg.connect(TEST_DSN) as conn:
            with conn.cursor() as cur:
                try:
                    with cur.connection.transaction():
                        cur.execute("SELECT p.id, p.full_name FROM players p JOIN player_seasons ps ON ps.player_id = p.id WHERE ps.season = 2036 AND ps.position IN ('QB', 'WR') LIMIT 1")
                        row = cur.fetchone()
                        pid, pname = row[0], row[1]
                        test_alias = "UniqueTestZzzyxAlias"

                        cur.execute("INSERT INTO player_name_aliases (player_id, alias, kind, created_at) VALUES (%s, %s, 'test', NOW())", (pid, test_alias))

                        # Verify query using alias finds the player
                        cur.execute("""
                            SELECT p.id FROM players p
                            WHERE p.full_name ILIKE %s OR p.player_name ILIKE %s
                               OR EXISTS (SELECT 1 FROM player_name_aliases pna WHERE pna.player_id = p.id AND pna.alias ILIKE %s)
                        """, (f"%{test_alias}%", f"%{test_alias}%", f"%{test_alias}%"))
                        found_ids = [r[0] for r in cur.fetchall()]
                        self.assertIn(pid, found_ids)
                        raise _Rollback()
                except _Rollback:
                    pass
