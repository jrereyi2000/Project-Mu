"""Unit and integration tests for Round 1 draft results ingestion & persistence.

Tests:
- 32-pick validation (completeness, missing picks, duplicates, out-of-range, empty names)
- Team abbreviation / ID resolution
- Prospect matching against draft_prospects (exact match, suffix handling, position groups,
  position conflicts, off-board insertion, ambiguous match prevention)
- Dry-run vs live load execution
- CSV and JSON file parsing
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import draft_results as R


class FakeCursor:
    """Records SQL calls and yields queued results for testing without a live DB."""

    def __init__(self, *results):
        self.calls = []
        self.results = list(results)

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))

    def executemany(self, sql, rows):
        self.calls.append((" ".join(sql.split()), list(rows)))

    def fetchall(self):
        return self.results.pop(0) if self.results else []

    def fetchone(self):
        return (False,)                    # to_regclass: the scouting migration (Draft Room) isn't applied


def sample_32_picks():
    """Generate 32 distinct picks for testing."""
    return [
        {
            "pick": i,
            "team": "CAR" if i % 2 == 1 else "KC",
            "player_name": f"Player {i}",
            "position": "QB" if i == 1 else "CB",
            "college": "Test Univ",
            "notes": f"Note for pick {i}",
        }
        for i in range(1, 33)
    ]


class TestPickValidation(unittest.TestCase):
    def test_valid_32_picks(self):
        picks = sample_32_picks()
        parsed = R.parse_raw_picks(picks)
        R.validate_completeness(parsed)
        self.assertEqual(len(parsed), 32)

    def test_missing_pick_detection(self):
        picks = sample_32_picks()
        # Remove pick 7 and add pick 33
        picks = [p for p in picks if p["pick"] != 7]
        picks.append({"pick": 33, "team": "CAR", "player_name": "Player 33", "position": "WR"})
        parsed = R.parse_raw_picks(picks)
        with self.assertRaises(ValueError) as ctx:
            R.validate_completeness(parsed)
        self.assertIn("missing picks: [7]", str(ctx.exception))
        self.assertIn("out-of-range picks: [33]", str(ctx.exception))

    def test_duplicate_pick_detection(self):
        picks = sample_32_picks()
        # Duplicate pick 10 instead of pick 11
        picks[10] = dict(picks[9])
        parsed = R.parse_raw_picks(picks)
        with self.assertRaises(ValueError) as ctx:
            R.validate_completeness(parsed)
        self.assertIn("duplicate picks: [10]", str(ctx.exception))

    def test_valid_224_picks(self):
        picks = [
            {
                "pick": i,
                "team": "CAR" if i % 2 == 1 else "KC",
                "player_name": f"Prospect {i}",
                "position": "QB" if i % 4 == 0 else "WR",
            }
            for i in range(1, 225)
        ]
        parsed = R.parse_raw_picks(picks)
        R.validate_completeness(parsed)
        self.assertEqual(len(parsed), 224)

    def test_incorrect_pick_count(self):
        picks = sample_32_picks()[:30]  # Only 30 picks
        parsed = R.parse_raw_picks(picks)
        with self.assertRaises(ValueError) as ctx:
            R.validate_completeness(parsed)
        self.assertIn("at least 32 picks (found 30)", str(ctx.exception))

    def test_empty_player_name(self):
        picks = sample_32_picks()
        picks[4]["player_name"] = "   "
        parsed = R.parse_raw_picks(picks)
        with self.assertRaises(ValueError) as ctx:
            R.validate_completeness(parsed)
        self.assertIn("Pick 5 has empty player name", str(ctx.exception))


class TestTeamResolution(unittest.TestCase):
    def setUp(self):
        self.by_id = {1: 1, 2: 2}
        self.by_code = {
            "CAR": 1,
            "PANTHERS": 1,
            "CAROLINA PANTHERS": 1,
            "KC": 2,
            "CHIEFS": 2,
            "KANSAS CITY CHIEFS": 2,
        }

    def test_resolve_abbreviation(self):
        self.assertEqual(R.resolve_team_id("CAR", self.by_id, self.by_code, 1), 1)
        self.assertEqual(R.resolve_team_id("kc", self.by_id, self.by_code, 2), 2)

    def test_resolve_name(self):
        self.assertEqual(R.resolve_team_id("Carolina Panthers", self.by_id, self.by_code, 1), 1)
        self.assertEqual(R.resolve_team_id("Chiefs", self.by_id, self.by_code, 2), 2)

    def test_resolve_id(self):
        self.assertEqual(R.resolve_team_id(1, self.by_id, self.by_code, 1), 1)
        self.assertEqual(R.resolve_team_id("2", self.by_id, self.by_code, 2), 2)

    def test_unknown_team(self):
        with self.assertRaises(ValueError) as ctx:
            R.resolve_team_id("UNKNOWN", self.by_id, self.by_code, 5)
        self.assertIn("unable to resolve team 'UNKNOWN'", str(ctx.exception))


class TestProspectMatching(unittest.TestCase):
    def setUp(self):
        # Fake team lookup: CAR=1, KC=2
        self.team_rows = [(1, "Panthers", "CAR", "Carolina"), (2, "Chiefs", "KC", "Kansas City")]

    def test_exact_and_suffix_matching(self):
        # Prospect on board with suffix stripped
        prospect_rows = [
            (101, "Travis Hunter", "CB"),
            (102, "Cam Ward", "QB"),
        ]
        cur = FakeCursor(self.team_rows, prospect_rows)

        picks = sample_32_picks()
        picks[0]["player_name"] = "Cam Ward"
        picks[0]["position"] = "QB"
        picks[1]["player_name"] = "Travis Hunter Jr."
        picks[1]["position"] = "CB"

        report = R.resolve_and_load_picks(picks, 2035, dry_run=True, cur=cur)
        self.assertEqual(report["picks_loaded"], 32)
        # First two picks should match prospects 102 and 101
        self.assertEqual(report["selections"][0]["prospect_id"], 102)
        self.assertEqual(report["selections"][1]["prospect_id"], 101)
        self.assertEqual(report["matched_prospects"], 2)

    def test_position_group_compatibility(self):
        # LEDG on board matches EDGE in results
        prospect_rows = [(201, "James Pearce", "LEDG")]
        cur = FakeCursor(self.team_rows, prospect_rows)

        picks = sample_32_picks()
        picks[0]["player_name"] = "James Pearce Jr."
        picks[0]["position"] = "EDGE"

        report = R.resolve_and_load_picks(picks, 2035, dry_run=True, cur=cur)
        self.assertEqual(report["selections"][0]["prospect_id"], 201)

    def test_position_conflict_never_guesses(self):
        # Board has WR, results say CB -> conflict, keeps prospect_id None
        prospect_rows = [(301, "Alex Morgan", "WR")]
        cur = FakeCursor(self.team_rows, prospect_rows)

        picks = sample_32_picks()
        picks[0]["player_name"] = "Alex Morgan"
        picks[0]["position"] = "CB"

        report = R.resolve_and_load_picks(picks, 2035, dry_run=True, cur=cur)
        self.assertIsNone(report["selections"][0]["prospect_id"])
        self.assertTrue(any("incompatible" in w for w in report["warnings"]))

    def test_ambiguous_prospect_match(self):
        # Two prospects on board with same name and same position
        prospect_rows = [
            (401, "Jordan Smith", "EDGE"),
            (402, "Jordan Smith", "EDGE"),
        ]
        cur = FakeCursor(self.team_rows, prospect_rows)

        picks = sample_32_picks()
        picks[0]["player_name"] = "Jordan Smith"
        picks[0]["position"] = "EDGE"

        report = R.resolve_and_load_picks(picks, 2035, dry_run=True, cur=cur)
        self.assertIsNone(report["selections"][0]["prospect_id"])
        self.assertTrue(any("ambiguous prospect match" in w for w in report["warnings"]))

    def test_duplicate_prospect_match_across_picks(self):
        # Two picks accidentally match the same single prospect
        prospect_rows = [(501, "Star Player", "QB")]
        cur = FakeCursor(self.team_rows, prospect_rows)

        picks = sample_32_picks()
        picks[0]["player_name"] = "Star Player"
        picks[0]["position"] = "QB"
        picks[1]["player_name"] = "Star Player"
        picks[1]["position"] = "QB"

        report = R.resolve_and_load_picks(picks, 2035, dry_run=True, cur=cur)
        # Both should be set to None and warned
        self.assertIsNone(report["selections"][0]["prospect_id"])
        self.assertIsNone(report["selections"][1]["prospect_id"])
        self.assertTrue(any("duplicate match" in w for w in report["warnings"]))


class TestDryRunVsLiveLoad(unittest.TestCase):
    def setUp(self):
        self.team_rows = [(1, "Panthers", "CAR", "Carolina"), (2, "Chiefs", "KC", "Kansas City")]
        self.prospect_rows = [(10, "Player 1", "QB")]

    def test_dry_run_executes_no_writes(self):
        cur = FakeCursor(self.team_rows, self.prospect_rows)
        picks = sample_32_picks()
        report = R.resolve_and_load_picks(picks, 2035, dry_run=True, cur=cur)

        self.assertEqual(report["status"], "dry_run")
        self.assertEqual(report["picks_loaded"], 32)
        # Only SELECT queries executed, no UPDATE, DELETE or INSERT
        for sql, _ in cur.calls:
            self.assertTrue(sql.startswith("SELECT"), f"Unexpected write SQL in dry-run: {sql}")

    def test_live_load_executes_writes(self):
        cur = FakeCursor(self.team_rows, self.prospect_rows)
        picks = sample_32_picks()
        report = R.resolve_and_load_picks(picks, 2035, dry_run=False, cur=cur)

        self.assertEqual(report["status"], "success")
        sql_statements = [call[0] for call in cur.calls]

        # Should delete previous off-board unprojected rows
        self.assertTrue(any("DELETE FROM draft_prospects WHERE draft_season = %s AND projected_pick IS NULL" in s for s in sql_statements))
        # Should reset actual_pick on existing prospects
        self.assertTrue(any("UPDATE draft_prospects SET actual_round = NULL" in s for s in sql_statements))
        # Should update matched prospect (Player 1 -> id 10)
        self.assertTrue(any("UPDATE draft_prospects SET actual_round = %s" in s for s in sql_statements))
        # Should insert off-board prospects (Player 2..32)
        self.assertTrue(any("INSERT INTO draft_prospects" in s for s in sql_statements))


class TestCsvAndJsonLoading(unittest.TestCase):
    def setUp(self):
        self.team_rows = [(1, "Panthers", "CAR", "Carolina"), (2, "Chiefs", "KC", "Kansas City")]
        self.prospect_rows = [(10, "Player 1", "QB")]

    def test_load_csv_file(self):
        picks = sample_32_picks()
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("overall_pick,team,player_name,position,college,selection_notes\n")
            for p in picks:
                f.write(f"{p['pick']},{p['team']},{p['player_name']},{p['position']},{p['college']},{p['notes']}\n")
            temp_path = f.name

        try:
            cur = FakeCursor(self.team_rows, self.prospect_rows)
            report = R.load_draft_results_csv(temp_path, 2035, dry_run=True, cur=cur)
            self.assertEqual(report["picks_loaded"], 32)
            self.assertEqual(report["selections"][0]["player_name"], "Player 1")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_load_json_file(self):
        picks = sample_32_picks()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"picks": picks}, f)
            temp_path = f.name

        try:
            cur = FakeCursor(self.team_rows, self.prospect_rows)
            report = R.load_draft_results_json(temp_path, 2035, dry_run=True, cur=cur)
            self.assertEqual(report["picks_loaded"], 32)
            self.assertEqual(report["selections"][0]["player_name"], "Player 1")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_load_json_data_in_memory(self):
        picks = sample_32_picks()
        cur = FakeCursor(self.team_rows, self.prospect_rows)
        report = R.load_draft_results_json(picks, 2035, dry_run=True, cur=cur)
        self.assertEqual(report["picks_loaded"], 32)


class TestLiveDatabaseExecution(unittest.TestCase):
    """Test against the local Postgres database (madden-db) if available."""

    def test_live_db_round_trip(self):
        try:
            import psycopg
            conn = psycopg.connect(R.DSN)
        except Exception:
            self.skipTest("Live database not reachable")

        try:
            with conn:
                # Use a high test season (e.g. 9999) to avoid colliding with active league data
                test_season = 9999

                # Create 2 test prospects in draft_prospects for season 9999
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM draft_prospects WHERE draft_season = %s", (test_season,))
                    cur.execute(
                        """INSERT INTO draft_prospects 
                           (draft_season, projected_pick, projected_round, round_pick, full_name, position, college)
                           VALUES (%s, 1, '1', 1, 'Test Prospect Alpha', 'QB', 'Test State'),
                                  (%s, 2, '1', 2, 'Test Prospect Beta', 'CB', 'Test Tech')""",
                        (test_season, test_season),
                    )

                picks = sample_32_picks()
                picks[0]["player_name"] = "Test Prospect Alpha"
                picks[0]["position"] = "QB"
                picks[1]["player_name"] = "Test Prospect Beta"
                picks[1]["position"] = "CB"

                # Run live load
                report = R.resolve_and_load_picks(picks, test_season, dry_run=False, conn=conn)
                self.assertEqual(report["status"], "success")
                self.assertEqual(report["picks_loaded"], 32)
                self.assertEqual(report["matched_prospects"], 2)

                # Verify records in draft_prospects
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT actual_round, actual_round_pick, actual_pick, full_name, drafted_team_id 
                           FROM draft_prospects WHERE draft_season = %s AND actual_pick IS NOT NULL 
                           ORDER BY actual_pick""",
                        (test_season,),
                    )
                    rows = cur.fetchall()
                    self.assertEqual(len(rows), 32)
                    self.assertEqual(rows[0][0], "1")
                    self.assertEqual(rows[0][1], 1)
                    self.assertEqual(rows[0][2], 1)
                    self.assertEqual(rows[0][3], "Test Prospect Alpha")

                    # Verify idempotency by loading again
                    report2 = R.resolve_and_load_picks(picks, test_season, dry_run=False, conn=conn)
                    self.assertEqual(report2["picks_loaded"], 32)
                    cur.execute("SELECT count(*) FROM draft_prospects WHERE draft_season = %s AND actual_pick IS NOT NULL", (test_season,))
                    count = cur.fetchone()[0]
                    self.assertEqual(count, 32)

                    # Cleanup test data
                    cur.execute("DELETE FROM draft_prospects WHERE draft_season = %s", (test_season,))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
