"""Tests for pipeline/commentary_names.py (Madden commentary surname lookup & rename tool)."""

import csv
import os
import tempfile
import unittest
import psycopg
import pytest

from pipeline import commentary_names as cn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "host=localhost dbname=madden_test user=postgres password=postgres",
)


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
