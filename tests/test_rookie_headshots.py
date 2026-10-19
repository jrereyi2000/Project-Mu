"""Unit tests for headshots/rookie_headshots.py."""

import io
import os
import tempfile
import unittest
from unittest import mock

from PIL import Image

import headshots.rookie_headshots as rh


class TestRookieHeadshots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.static_dir = os.path.join(self.tmp, "static", "players")
        self.backup_dir = os.path.join(self.tmp, "backup")
        os.makedirs(self.static_dir, exist_ok=True)
        os.makedirs(self.backup_dir, exist_ok=True)

    def test_get_rookies_needing_headshots(self):
        mock_rows = [
            (101, "Rookie One", "Rams", "QB", "6'2\"", 215, 22, 1, 1001),
            (102, "Rookie Two", "Panthers", "WR", "6'0\"", 195, 21, 2, 1002),
        ]
        # Simulate Rookie One already has a headshot file
        open(os.path.join(self.static_dir, "101.webp"), "w").close()

        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = mock_rows
        mock_cur.__enter__.return_value = mock_cur

        mock_conn = mock.MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__.return_value = mock_conn

        with mock.patch("psycopg.connect", return_value=mock_conn):
            needing, total = rh.get_rookies_needing_headshots(
                season=2036,
                drafted_only=False,
                static_dir=self.static_dir,
            )

        self.assertEqual(total, 2)
        self.assertEqual(len(needing), 1)
        self.assertEqual(needing[0]["id"], 102)
        self.assertEqual(needing[0]["full_name"], "Rookie Two")

    def test_generate_player_headshot_existing_file_skipped(self):
        target = os.path.join(self.static_dir, "101.webp")
        open(target, "w").close()

        player = {
            "id": 101,
            "full_name": "Rookie One",
            "team": "Rams",
            "position": "QB",
            "height": "6'2\"",
            "weight": 215,
            "age": 22,
        }
        res = rh.generate_player_headshot(
            player=player,
            static_dir=self.static_dir,
            backup_dir=self.backup_dir,
        )
        self.assertEqual(res, target)

    @mock.patch("headshots.rookie_headshots.ensure_api_key")
    @mock.patch("headshots.rookie_headshots.get_thread_client")
    def test_generate_player_headshot_success(self, mock_get_client, mock_ensure_api):
        fake_img = Image.new("RGBA", (1200, 896), (255, 0, 0, 255))
        buf = io.BytesIO()
        fake_img.save(buf, format="PNG")
        fake_png_bytes = buf.getvalue()

        mock_part = mock.MagicMock()
        mock_part.inline_data.data = fake_png_bytes

        mock_candidate = mock.MagicMock()
        mock_candidate.content.parts = [mock_part]

        mock_resp = mock.MagicMock()
        mock_resp.candidates = [mock_candidate]

        mock_client = mock.MagicMock()
        mock_client.models.generate_content.return_value = mock_resp
        mock_get_client.return_value = mock_client

        mock_rembg_session = mock.MagicMock()
        with mock.patch("headshots.rookie_headshots.get_thread_rembg_session", return_value=mock_rembg_session):
            with mock.patch("rembg.remove", return_value=fake_img):
                player = {
                    "id": 105,
                    "full_name": "Test Player",
                    "team": "Vikings",
                    "position": "WR",
                    "height": "6'3\"",
                    "weight": 205,
                    "age": 23,
                }
                out = rh.generate_player_headshot(
                    player=player,
                    static_dir=self.static_dir,
                    backup_dir=self.backup_dir,
                )

        self.assertTrue(os.path.exists(out))
        saved_im = Image.open(out)
        self.assertEqual(saved_im.size, (700, 508))
        self.assertEqual(saved_im.format, "WEBP")

        backup_out = os.path.join(self.backup_dir, "105.webp")
        self.assertTrue(os.path.exists(backup_out))

    @mock.patch("headshots.rookie_headshots.get_rookies_needing_headshots")
    def test_generate_rookie_headshots_dry_run(self, mock_needing):
        mock_needing.return_value = ([{"id": 1}], 5)
        report = rh.generate_rookie_headshots(
            season=2036,
            dry_run=True,
            static_dir=self.static_dir,
        )
        self.assertEqual(report["total_rookies"], 5)
        self.assertEqual(report["already_had"], 4)
        self.assertEqual(report["generated"], 0)


if __name__ == "__main__":
    unittest.main()
