"""pipeline/game_launch.py: refusals (pending moves, Madden running, no game), the pack chosen for the game and the
dry-run command, with a fake cursor and mod_packs' config / process calls patched (no database, nothing run)."""
import unittest
from unittest import mock

from pipeline import game_launch as gl
from pipeline import mod_packs
from pipeline.gpu_queue import process_check

GAME = mod_packs.GameInfo(id=77, season=2036, week=5, game_time="Sun 8:20 PM", game_type="REGULAR_SEASON",
                          home="CAR", away="ATL", away_conference="NFC", division_game=True, home_pop_pct=0.9,
                          away_pop_pct=0.8, home_form=0.75)
BASE = [("m27_snf_presentation.fbmod", False), ("m27_fox_presentation.fbmod", True), ("Mudded Ball.fbmod", True),
        ("Other.fbmod", True)]


class FakeCursor:
    def __init__(self, pending=0):
        self.pending = pending
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return (self.pending,)


class LaunchTest(unittest.TestCase):
    def setUp(self):
        self.games = [GAME]
        self.loaded = []

        def load_games(cur, season, week=None, team=None, game_id=None):
            self.loaded.append((season, week, team))
            return list(self.games)

        self.written = []
        patches = [
            mock.patch.object(mod_packs, "load_games", load_games),
            mock.patch.object(mod_packs, "current_week", lambda cur: (2036, 5)),
            mock.patch.object(mod_packs, "config_path", lambda: "/mnt/c/fake/manager_config.json"),
            mock.patch.object(mod_packs, "base_pack", lambda path: list(BASE)),
            mock.patch.object(mod_packs, "manager_running", lambda: False),
            mock.patch.object(mod_packs, "write_pack", lambda *a, **k: self.written.append(a)),
            mock.patch.object(gl.subprocess, "run"),
            mock.patch.dict("os.environ", {"MMC_MANAGER_DIR": "/mnt/c/Madden27Modding/MMC"}, clear=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.run_mock = gl.subprocess.run
        process_check.set_process_check_mock(lambda: False)
        self.addCleanup(process_check.set_process_check_mock, None)
        import os
        os.environ.pop("MMC_MOD_MANAGER_EXE", None)

    def test_refuses_with_pending_moves(self):
        cur = FakeCursor(pending=2)
        out = gl.launch("car", 2036, 5, cur=cur)
        self.assertEqual(set(out), {"pack", "command", "started", "refused", "game"})
        self.assertFalse(out["started"])
        self.assertIn("2 pending moves", out["refused"])
        self.assertIn("franchise_pending_moves", cur.executed[0][0])
        self.assertEqual(cur.executed[0][1], ("CAR",))
        self.assertEqual(self.loaded, [])
        self.run_mock.assert_not_called()

    def test_refuses_while_madden_runs(self):
        with process_check.mock_process_running(True):
            out = gl.launch("CAR", 2036, 5, cur=FakeCursor())
        self.assertEqual(out["refused"], "Madden 27 is already running")
        self.assertIsNone(out["pack"])
        self.run_mock.assert_not_called()

    def test_dry_run_builds_command_for_the_games_pack(self):
        out = gl.launch("CAR", None, None, dry_run=True, cur=FakeCursor())
        self.assertIsNone(out["refused"])
        self.assertFalse(out["started"])
        self.assertEqual(self.loaded, [(2036, 5, "CAR")])        # league_state's week
        self.assertEqual(out["pack"], mod_packs.choose(GAME).pack_name)
        self.assertTrue(out["pack"].startswith("Mu SNF"))        # Sunday 8:20 PM = SNF
        self.assertEqual(out["command"], 'cmd.exe /c start "" "C:\\Madden27Modding\\MMC\\MMCModManager.exe" '
                                         f'-launch "{out["pack"]}"')
        self.assertEqual(out["game"]["id"], 77)
        self.assertEqual(self.written, [])
        self.run_mock.assert_not_called()

    def test_env_exe_override(self):
        with mock.patch.dict("os.environ", {"MMC_MOD_MANAGER_EXE": "D:\\Tools\\MMCModManager.exe"}):
            out = gl.launch("CAR", 2036, 5, dry_run=True, cur=FakeCursor())
        self.assertIn('"D:\\Tools\\MMCModManager.exe"', out["command"])

    def test_real_launch_writes_pack_and_starts(self):
        out = gl.launch("CAR", 2036, 5, cur=FakeCursor())
        self.assertTrue(out["started"])
        path, name, entries = self.written[0]
        self.assertEqual(name, out["pack"])
        self.assertIn(("m27_snf_presentation.fbmod", True), entries)
        self.assertIn(("m27_fox_presentation.fbmod", False), entries)
        argv = self.run_mock.call_args[0][0]
        self.assertEqual(argv, ["cmd.exe", "/c", "start", "", "C:\\Madden27Modding\\MMC\\MMCModManager.exe",
                                "-launch", out["pack"]])

    def test_refuses_while_manager_open(self):
        with mock.patch.object(mod_packs, "manager_running", lambda: True):
            out = gl.launch("CAR", 2036, 5, cur=FakeCursor())
        self.assertIn("MMCModManager.exe is open", out["refused"])
        self.assertEqual(self.written, [])
        self.run_mock.assert_not_called()

    def test_refuses_without_one_game(self):
        self.games = []
        out = gl.launch("CAR", 2036, 9, dry_run=True, cur=FakeCursor())
        self.assertIn("0 games", out["refused"])

    def test_missing_config_refuses(self):
        def no_config():
            raise SystemExit("set MMC_CONFIG: found 0 manager_config.json files")
        with mock.patch.object(mod_packs, "config_path", no_config):
            out = gl.launch("CAR", 2036, 5, dry_run=True, cur=FakeCursor())
        self.assertIn("MMC_CONFIG", out["refused"])
        self.assertIsNotNone(out["pack"])

    def test_windows_path(self):
        self.assertEqual(gl.windows_path("/mnt/c/A B/x.exe"), "C:\\A B\\x.exe")
        self.assertEqual(gl.windows_path("C:\\x.exe"), "C:\\x.exe")

    def test_cli_help(self):
        with self.assertRaises(SystemExit) as e:
            gl.main(["--help"])
        self.assertEqual(e.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
