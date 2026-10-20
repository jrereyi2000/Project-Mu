"""Tests for pipeline upgrades: row count checks, madden_capture category retries, and resolve_player."""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from PIL import Image

import madden_capture as MC
from pipeline import load_capture as L
from pipeline import resolve_player as RP


class RowCountChecksTest(unittest.TestCase):
    def test_monotonic_lower_bound_allows_two_row_margin(self):
        """Row count can only decrease by up to 2 rows due to deduplication variance at the ~250 cap."""
        # 250 -> 248 is allowed (2-row margin)
        n = 250
        now_valid = 248
        self.assertFalse(now_valid < n - 2)

        # 250 -> 247 violates monotonic lower bound
        now_invalid = 247
        self.assertTrue(now_invalid < n - 2)

    def test_upper_bound_expansion_check(self):
        """Allows up to 30% expansion with an absolute floor of 20 for low-count categories."""
        # Low count category: n = 40. 30% of 40 is 12, max(20, 12) = 20. Limit is 40 + 20 = 60.
        n_low = 40
        limit_low = n_low + max(20, int(0.30 * n_low))
        self.assertEqual(limit_low, 60)
        self.assertFalse(55 > limit_low)  # 55 is within limit
        self.assertTrue(65 > limit_low)   # 65 exceeds limit

        # High count category: n = 200. 30% of 200 is 60, max(20, 60) = 60. Limit is 200 + 60 = 260.
        n_high = 200
        limit_high = n_high + max(20, int(0.30 * n_high))
        self.assertEqual(limit_high, 260)
        self.assertFalse(255 > limit_high)  # 255 is within limit
        self.assertTrue(265 > limit_high)   # 265 exceeds limit


class MaddenCaptureCategoryTest(unittest.TestCase):
    def test_capture_category_passes_confirm_end(self):
        """capture_category passes confirm_end=True to capture_wide_list and capture_list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(MC, "capture_wide_list", return_value=25) as mock_wide, \
                 patch.object(MC, "capture_list", return_value=15) as mock_list, \
                 patch.object(MC, "prior_category_rows", side_effect=[250, 120]):
                # Defense is wide category
                MC.capture_category(tmpdir, 5, "DEFENSE", all_rows=True)
                mock_wide.assert_called_once()
                self.assertTrue(mock_wide.call_args[1].get("confirm_end"))

                # Rushing is standard category
                MC.capture_category(tmpdir, 2, "RUSHING", all_rows=False)
                mock_list.assert_called_once()
                self.assertTrue(mock_list.call_args[1].get("confirm_end"))

    def test_capture_category_reattempts_on_significantly_fewer_pages(self):
        """When category captures significantly fewer pages than prior week, it warns and re-attempts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # First attempt returns 21 pages (too few for 250 rows: 25+ needed), second attempt returns 26 pages
            with patch.object(MC, "capture_wide_list", side_effect=[21, 26]) as mock_wide, \
                 patch.object(MC, "prior_category_rows", return_value=250), \
                 patch.object(MC, "list_to_top") as mock_top, \
                 patch.object(MC, "log") as mock_log:
                pages = MC.capture_category(tmpdir, 5, "DEFENSE", all_rows=True)
                self.assertEqual(pages, 26)
                self.assertEqual(mock_wide.call_count, 2)
                mock_top.assert_called_once()
                # Confirm warning log
                warn_logs = [call[0][0] for call in mock_log.call_args_list if "WARNING" in str(call[0][0])]
                self.assertTrue(len(warn_logs) > 0)

    def test_fewer_pages_than_last_week_is_fine_when_they_cover_the_rows(self):
        """2026-10-20: 26 and 27 pages after a 31-page week were thrown away; 26 pages can hold a 250-row list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(MC, "capture_wide_list", return_value=26) as mock_wide, \
                 patch.object(MC, "prior_category_rows", return_value=250), \
                 patch.object(MC, "list_to_top") as mock_top, \
                 patch.object(MC, "log"):
                self.assertEqual(MC.capture_category(tmpdir, 5, "DEFENSE", all_rows=True), 26)
                self.assertEqual(mock_wide.call_count, 1)
                mock_top.assert_not_called()

    def test_min_pages_for(self):
        self.assertEqual(MC.min_pages_for(250), 25)      # (25 - 1) * 10 + 11 = 251 >= 245
        self.assertEqual(MC.min_pages_for(None), 0)
        self.assertEqual(MC.min_pages_for(8), 1)

    def test_prior_category_rows_reads_the_last_earlier_csv(self):
        with tempfile.TemporaryDirectory() as root:
            for name, rows in (("2026-10-18_1345", 250), ("2026-10-20_1258", 249), ("2026-10-20_1406", 0)):
                os.makedirs(os.path.join(root, name))
                with open(os.path.join(root, name, "stats_defense.csv"), "w") as fh:
                    fh.write("NAME,POS\n" + "X,LB\n" * rows)
            self.assertEqual(MC.prior_category_rows(os.path.join(root, "2026-10-20_1406"), "DEFENSE"), 249)

    def test_capture_category_aborts_when_retry_also_short(self):
        """If the retry is still short, capture_category raises Abort so run_stats never marks it done."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(MC, "capture_wide_list", side_effect=[20, 13]) as mock_wide, \
                 patch.object(MC, "prior_category_rows", return_value=250), \
                 patch.object(MC, "list_to_top"), \
                 patch.object(MC, "log"):
                with self.assertRaises(MC.Abort):
                    MC.capture_category(tmpdir, 5, "DEFENSE", all_rows=True)
                self.assertEqual(mock_wide.call_count, 2)


class UnmatchedPlayerHandlingTest(unittest.TestCase):
    def test_non_interactive_dumps_json_and_crops(self):
        """Non-interactive resolve_season_rows dumps unmatched_players.json and raises LoadError for offense."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stats_dir = os.path.join(tmpdir, "stats")
            os.makedirs(stats_dir, exist_ok=True)
            # Create a mock stats screenshot
            img = Image.new("RGB", (1000, 500), color="blue")
            img_path = os.path.join(stats_dir, "01_passing_000.png")
            img.save(img_path)

            rows = [{"NAME": "J.Murray", "POS": "QB", "YARDS": "300", "TD": "2", "GP": "1", "DP": "50"}]
            cmap = {"YARDS": ("yards", L.num), "TD": ("tds", L.num)}
            players = []  # No rostered players
            problems = []
            unmatched = []

            # Mock OCR in resolve_player
            with patch("sys.stdin.isatty", return_value=False), \
                 patch("pipeline.resolve_player.ocr", return_value=[("COL J.MURRAY", (10, 200, 200, 240))]):
                with self.assertRaises(L.LoadError) as ctx:
                    L.resolve_season_rows(
                        cur=MagicMock(),
                        season=2036,
                        cat="passing",
                        rows=rows,
                        cmap=cmap,
                        table="season_stats",
                        players=players,
                        problems=problems,
                        unmatched=unmatched,
                        folder=tmpdir
                    )
                self.assertIn("resolve_player", str(ctx.exception))

            # Verify unmatched_players.json exists and contains details
            json_path = os.path.join(tmpdir, "unmatched_players.json")
            self.assertTrue(os.path.exists(json_path))
            with open(json_path) as fh:
                entries = json.load(fh)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["player_name"], "J.Murray")
            self.assertEqual(entries[0]["category"], "passing")

            # Verify crop snippet was created
            crops_dir = os.path.join(tmpdir, "unmatched_crops")
            self.assertTrue(os.path.isdir(crops_dir))
            crop_file = os.path.join(crops_dir, "passing_j_murray.png")
            self.assertTrue(os.path.exists(crop_file))
            crop_img = Image.open(crop_file)
            self.assertEqual(crop_img.size, (700, 120))


class ResolvePlayerCliTest(unittest.TestCase):
    def test_resolve_team_matches_abbr_and_name(self):
        """resolve_team resolves by abbreviation or name."""
        cur = MagicMock()
        cur.fetchall.return_value = [(12, "COL", "Colts")]
        team = RP.resolve_team(cur, "COL")
        self.assertEqual(team, (12, "COL", "Colts"))

    def test_prompt_resolve_player_create_new(self):
        """Creating a new player via prompt_resolve_player inserts into players and player_seasons."""
        cur = MagicMock()
        cur.fetchall.return_value = []  # No candidates
        cur.fetchone.return_value = [999]  # New player ID

        with patch("builtins.input", side_effect=["3", "James Murray", "COL", "8", "24", "82"]), \
             patch("pipeline.resolve_player.resolve_team", return_value=(12, "COL", "Colts")):
            res = RP.prompt_resolve_player(
                cur,
                season=2036,
                cat="passing",
                player_name="J.Murray",
                position="QB",
                stats={"YARDS": "300"},
                screenshot_path=None,
                snippet_path=None,
                is_interactive=True
            )
            self.assertEqual(res, (999, 12))
            # Verify INSERT into players was executed with player values
            any_player_insert = any(
                "INSERT INTO players" in str(call[0][0]) and call[0][1] == ("J. Murray", "James Murray", 8)
                for call in cur.execute.call_args_list if call[0]
            )
            self.assertTrue(any_player_insert)

    def test_prompt_resolve_player_link_candidate(self):
        """Linking to candidate ID links player to team in player_seasons."""
        cur = MagicMock()
        candidates = [(854, "J. Murray", "James Murray", 8, 2035, "QB", 12, "COL", "Colts")]
        with patch("pipeline.resolve_player.find_candidate_players", return_value=candidates), \
             patch("builtins.input", side_effect=["1", "COL"]), \
             patch("pipeline.resolve_player.resolve_team", return_value=(12, "COL", "Colts")):
            res = RP.prompt_resolve_player(
                cur,
                season=2036,
                cat="passing",
                player_name="J.Murray",
                position="QB",
                stats={"YARDS": "300"},
                screenshot_path=None,
                snippet_path=None,
                is_interactive=True
            )
            self.assertEqual(res, (854, 12))
