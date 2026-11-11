"""
tests/test_watch_save.py: Unit tests for pipeline/watch_save.py.
Verifies file debouncing, week classification, single execution locking, franchise routing
(one database per Madden franchise, db/franchises.py), --auto-init / --reseed guards, the ingest child,
and flag handling (--dry-run, --no-articles, --force).
"""
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import pytest

from pipeline import mod_packs
from pipeline import watch_save as ws

REAL_WRITE_PACK = mod_packs.write_pack     # the autouse fixture replaces the module attribute


@pytest.fixture(autouse=True)
def no_real_mod_manager(tmp_path, monkeypatch):
    """Never touch JR's real MMC manager_config.json or run tasklist.exe: MMC_CONFIG points at a missing temp file
    and manager_running / write_pack fail unless a test mocks them."""
    monkeypatch.setenv("MMC_CONFIG", str(tmp_path / "no_manager_config.json"))

    def refuse(*a, **k):
        raise AssertionError("a test reached the real Mod Manager")

    monkeypatch.setattr("pipeline.mod_packs.manager_running", refuse)
    monkeypatch.setattr("pipeline.mod_packs.write_pack", refuse)


@pytest.fixture(autouse=True)
def no_switchboard(monkeypatch):
    """The ingest reads the personality switchboard once (personality_modes), which opens a database connection;
    tests get None (no switchboard: every step falls back to its env flag) unless they patch it themselves."""
    monkeypatch.setattr("pipeline.watch_save.personality_modes", lambda *a, **k: None)


class DebouncerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.debouncer = ws.FileDebouncer(debounce_seconds=10)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_file(self, name, size=100, mtime_offset=0):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(b"x" * size)
        t = time.time() + mtime_offset
        os.utime(path, (t, t))
        return path

    def test_empty_file_not_settled(self):
        path = self.make_file("empty.save", size=0, mtime_offset=-20)
        self.assertFalse(self.debouncer.is_settled(path))

    def test_recent_file_not_settled(self):
        path = self.make_file("recent.save", size=100, mtime_offset=-2)
        # modified 2 seconds ago, debounce is 10s -> not settled
        self.assertFalse(self.debouncer.is_settled(path))

    def test_old_file_settled_immediately(self):
        # File untouched for 60 seconds
        path = self.make_file("old.save", size=100, mtime_offset=-60)
        self.assertTrue(self.debouncer.is_settled(path))

    def test_write_burst_resets_debounce(self):
        path = self.make_file("burst.save", size=100, mtime_offset=-5)
        now = time.time()

        # Check 1: 5 seconds old -> not settled
        self.assertFalse(self.debouncer.is_settled(path, now=now))

        # Check 2: write happens 3 seconds later
        now += 3
        with open(path, "wb") as f:
            f.write(b"x" * 200)
        os.utime(path, (now, now))
        self.assertFalse(self.debouncer.is_settled(path, now=now))

        # Check 3: 5 seconds after burst -> still not settled (needs 10s)
        now += 5
        self.assertFalse(self.debouncer.is_settled(path, now=now))

        # Check 4: 11 seconds after burst -> settled!
        now += 6
        self.assertTrue(self.debouncer.is_settled(path, now=now))

    def test_already_processed_tracking(self):
        path = self.make_file("proc.save", size=100, mtime_offset=-60)
        self.assertFalse(self.debouncer.is_already_processed(path))
        self.debouncer.mark_processed(path)
        self.assertTrue(self.debouncer.is_already_processed(path))

        # Modifying file makes it not processed anymore
        t = time.time()
        os.utime(path, (t, t))
        self.assertFalse(self.debouncer.is_already_processed(path))


class WeekClassificationTest(unittest.TestCase):
    def test_same_week(self):
        self.assertEqual(ws.classify_week(2036, 5, 2036, 5), "same_week")

    def test_new_week_same_season(self):
        self.assertEqual(ws.classify_week(2036, 6, 2036, 5), "new_week")

    def test_new_week_season_rollover(self):
        self.assertEqual(ws.classify_week(2037, 1, 2036, 23), "new_week")

    def test_older_week_same_season(self):
        self.assertEqual(ws.classify_week(2036, 4, 2036, 5), "older_week")

    def test_older_week_previous_season(self):
        self.assertEqual(ws.classify_week(2035, 10, 2036, 5), "older_week")

    def test_season_gap_far_future(self):
        self.assertEqual(ws.classify_week(2040, 1, 2036, 5), "season_gap")

    def test_no_current_week(self):
        self.assertEqual(ws.classify_week(2036, None, 2036, 5), "no_current_week")


class LockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lock_path = os.path.join(self.tmp, "test.lock")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lock_acquire_and_release(self):
        with ws.run_lock(self.lock_path):
            self.assertTrue(os.path.exists(self.lock_path))
            # Second acquire attempt within the with-block must fail
            with self.assertRaises(ws.LockError):
                with ws.run_lock(self.lock_path):
                    pass

        # Once released, can acquire again
        with ws.run_lock(self.lock_path):
            pass


class FakeCluster:
    """Stands in for Postgres in db.franchises and watch_save: databases are names with a state and a stamp."""

    def __init__(self):
        self.dbs = {}            # name -> {"state": "empty"|"seeded", "league_state": (season, week), "stamp": int|None}
        self.created, self.dropped, self.seeded = [], [], []

    @staticmethod
    def name(dsn):
        return dsn.split("dbname=")[1].split()[0]

    def patches(self):
        fr = "db.franchises"
        return [
            mock.patch(f"{fr}.db_exists", side_effect=lambda db: db in self.dbs),
            mock.patch(f"{fr}.create_db", side_effect=self.create_db),
            mock.patch(f"{fr}.drop_db", side_effect=self.drop_db),
            mock.patch(f"{fr}.db_state", side_effect=lambda dsn: self.dbs[self.name(dsn)]["state"]),
            mock.patch(f"{fr}.stamped_league_id", side_effect=lambda dsn: self.dbs[self.name(dsn)]["stamp"]),
            mock.patch(f"{fr}.stamp_league_id", side_effect=self.stamp),
            mock.patch("db.seed_clean_db.run_clean_seed", side_effect=self.seed),
            mock.patch("pipeline.watch_save.get_league_state", side_effect=self.league_state),
        ]

    def create_db(self, db):
        self.created.append(db)
        self.dbs[db] = {"state": "empty", "league_state": (None, None), "stamp": None}

    def drop_db(self, db):
        self.dropped.append(db)
        self.dbs.pop(db, None)

    def seed(self, dsn, season, week, user_teams, log):
        db = self.name(dsn)
        self.seeded.append((db, season, week, list(user_teams)))
        self.dbs[db].update(state="seeded", league_state=(season, week))

    def stamp(self, dsn, league_id):
        self.dbs[self.name(dsn)]["stamp"] = league_id

    def league_state(self, dsn):
        db = self.dbs.get(self.name(dsn))
        return db["league_state"] if db else (None, None)


class ProcessSaveTest(unittest.TestCase):
    """Franchise routing in process_save; the ingest child (run_ingest) is mocked, IngestSnapshotTest covers it."""

    LEAGUE_A = -1851690351
    LEAGUE_B = 2027975635

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reg_path = os.path.join(self.tmp, "franchises.json")
        self.env = mock.patch.dict(os.environ, {"MU_FRANCHISE_REGISTRY": self.reg_path,
                                                "DATABASE_URL": "host=localhost dbname=madden_test user=postgres"})
        self.env.start()
        self.cluster = FakeCluster()
        self.patchers = self.cluster.patches()
        for p in self.patchers:
            p.start()
        self.ingest = mock.patch("pipeline.watch_save.run_ingest", side_effect=self.fake_ingest).start()
        self.notify = mock.patch("pipeline.watch_save.send_notify").start()
        self.freezes = mock.patch("pipeline.weekly_freeze.freeze", side_effect=self.fake_freeze).start()
        self.snapshots = {}

    @staticmethod
    def fake_freeze(db, dsn, save_path, label, **kw):
        return {"db": db, "label": label, "dump_bytes": 1000, "save_name": os.path.basename(save_path)}

    def tearDown(self):
        mock.patch.stopall()
        self.env.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, name, league_id, season=2034, week_index=5):
        """A save file plus the snapshot the (mocked) reader returns for it."""
        path = os.path.join(self.tmp, name)
        with open(path, "w") as f:
            f.write("mock save")
        snap = {"meta": {"reader_version": 3, "game_year": 27},
                "season": {"calendar_year": season, "franchise_year": 0, "week_type": "RegularSeason",
                           "week_index": week_index},
                "teams": [], "players": [], "games": []}
        if league_id is not None:
            snap["franchise"] = {"league_id": league_id}
        self.snapshots[path] = snap
        return path

    def fake_snapshot_save(self, save_path, run_folder, **kw):
        os.makedirs(run_folder, exist_ok=True)
        snap_path = os.path.join(run_folder, "save.json")
        with open(snap_path, "w") as f:
            json.dump(self.snapshots[save_path], f)
        return snap_path

    def fake_ingest(self, snapshot_path, dsn, season, week, **kw):
        db = FakeCluster.name(dsn)
        if not kw.get("dry_run"):
            self.cluster.dbs[db]["league_state"] = (season, week)
        return {"status": "success", "dry_run": kw.get("dry_run", False), "season": season, "week": week}

    def process(self, path, **kw):
        with mock.patch("pipeline.watch_save.snapshot_save", side_effect=self.fake_snapshot_save):
            return ws.process_save(path, run_folder=os.path.join(self.tmp, "run"), **kw)

    def registry(self):
        from db import franchises
        return franchises.load_registry(self.reg_path)

    def test_new_franchise_without_auto_init_raises(self):
        with self.assertRaises(ws.WatcherError) as ctx:
            self.process(self.save("CAREER-A", self.LEAGUE_A))
        self.assertIn("--auto-init", str(ctx.exception))
        self.assertEqual(self.cluster.created, [])
        self.ingest.assert_not_called()

    def test_auto_init_creates_seeds_stamps_and_loads(self):
        res = self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True, user_teams=["IND"],
                           franchise_name="Colts 2034")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["db"], "madden_colts_2034")
        self.assertEqual(self.cluster.created, ["madden_colts_2034"])
        self.assertEqual(self.cluster.seeded, [("madden_colts_2034", 2034, 6, ["IND"])])
        self.assertEqual(self.cluster.dbs["madden_colts_2034"]["stamp"], self.LEAGUE_A)
        self.ingest.assert_called_once()
        self.assertIn("dbname=madden_colts_2034", self.ingest.call_args[0][1])
        reg = self.registry()
        self.assertEqual(reg["active"], str(self.LEAGUE_A))
        self.assertEqual(reg["franchises"][str(self.LEAGUE_A)]["saves"], ["CAREER-A"])

    def test_save_slots_of_one_franchise_share_a_database(self):
        self.process(self.save("CAREER-WEEK1", self.LEAGUE_A, week_index=0), auto_init=True)
        res = self.process(self.save("CAREER-WEEK2", self.LEAGUE_A, week_index=1), auto_init=True)
        self.assertEqual(res["status"], "success")
        self.assertEqual(len(self.cluster.created), 1)
        self.assertEqual(len(self.cluster.seeded), 1, "a seeded database is never seeded again")
        entry = self.registry()["franchises"][str(self.LEAGUE_A)]
        self.assertEqual(entry["saves"], ["CAREER-WEEK1", "CAREER-WEEK2"])
        self.assertEqual(self.cluster.dbs[entry["db"]]["league_state"], (2034, 2))

    def test_two_franchises_route_to_two_databases(self):
        a = self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        b = self.process(self.save("CAREER-B", self.LEAGUE_B, season=2026), auto_init=True)
        self.assertNotEqual(a["db"], b["db"])
        self.assertEqual(sorted(self.cluster.created), sorted([a["db"], b["db"]]))
        self.assertEqual(self.registry()["active"], str(self.LEAGUE_A), "the first franchise stays active")
        # A later week of franchise A goes to A's database, not B's (which is in an older season).
        res = self.process(self.save("CAREER-A2", self.LEAGUE_A, week_index=6))
        self.assertEqual(res["db"], a["db"])
        self.assertEqual(self.cluster.dbs[a["db"]]["league_state"], (2034, 7))
        self.assertEqual(self.cluster.dbs[b["db"]]["league_state"], (2026, 6))

    def test_existing_database_with_data_is_never_seeded(self):
        from db import franchises
        with franchises.editing_registry(self.reg_path) as reg:
            entry = franchises.register(reg, self.LEAGUE_A, name="colts")
        self.cluster.dbs[entry["db"]] = {"state": "seeded", "league_state": (2034, 5), "stamp": self.LEAGUE_A}
        res = self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        self.assertEqual(res["status"], "success")
        self.assertEqual(self.cluster.seeded, [])
        self.assertEqual(self.cluster.created, [])

    def test_stamp_mismatch_refuses_to_load(self):
        from db import franchises
        with franchises.editing_registry(self.reg_path) as reg:
            entry = franchises.register(reg, self.LEAGUE_A)
        self.cluster.dbs[entry["db"]] = {"state": "seeded", "league_state": (2034, 5), "stamp": self.LEAGUE_B}
        with self.assertRaises(ws.WatcherError) as ctx:
            self.process(self.save("CAREER-A", self.LEAGUE_A))
        self.assertIn("belongs to franchise", str(ctx.exception))
        self.ingest.assert_not_called()

    def test_reseed_needs_confirmation(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True, franchise_name="colts")
        with self.assertRaises(ws.WatcherError) as ctx:
            self.process(self.save("CAREER-A", self.LEAGUE_A), reseed=True)
        self.assertIn("--confirm-reseed madden_colts", str(ctx.exception))
        self.assertEqual(self.cluster.dropped, [])

    def test_reseed_drops_and_reseeds(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True, franchise_name="colts")
        res = self.process(self.save("CAREER-A", self.LEAGUE_A, week_index=0), reseed=True,
                           confirm_reseed="madden_colts")
        self.assertEqual(res["status"], "success")
        self.assertEqual(self.cluster.dropped, ["madden_colts"])
        self.assertEqual([s[:3] for s in self.cluster.seeded], [("madden_colts", 2034, 6), ("madden_colts", 2034, 1)])

    def test_dry_run_never_creates_a_database(self):
        res = self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True, dry_run=True)
        self.assertEqual(res["status"], "no_op")
        self.assertEqual(res["reason"], "new_franchise")
        self.assertEqual(self.cluster.created, [])
        self.assertEqual(self.registry()["franchises"], {})
        self.ingest.assert_not_called()

    def test_same_week_is_noop(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        res = self.process(self.save("CAREER-A-COPY", self.LEAGUE_A))
        self.assertEqual(res["status"], "no_op")
        self.assertEqual(res["reason"], "same_week")
        self.assertEqual(self.ingest.call_count, 1)
        self.notify.assert_not_called()

    def test_older_week_notifies_and_noops(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        res = self.process(self.save("CAREER-OLD", self.LEAGUE_A, week_index=2))
        self.assertEqual(res["reason"], "older_week")
        self.notify.assert_called_once()
        self.assertIn("older week", self.notify.call_args[0][0])

    def test_force_flag_bypasses_week_check(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        res = self.process(self.save("CAREER-A", self.LEAGUE_A), force=True)
        self.assertEqual(res["status"], "success")
        self.assertEqual(self.ingest.call_count, 2)

    def test_snapshot_without_league_id_falls_back_to_file_name(self):
        res = self.process(self.save("CAREER-OLDREADER", None), auto_init=True)
        self.assertEqual(res["franchise"], "file_career_oldreader")
        self.assertEqual(res["db"], "madden_file_career_oldreader")
        self.assertIsNone(self.cluster.dbs[res["db"]]["stamp"])

    def test_explicit_dsn_skips_routing(self):
        self.cluster.dbs["madden_test"] = {"state": "seeded", "league_state": (2034, 5), "stamp": None}
        res = self.process(self.save("CAREER-A", self.LEAGUE_A), dsn="host=localhost dbname=madden_test user=postgres")
        self.assertEqual(res["db"], "madden_test")
        self.assertEqual(self.registry()["franchises"], {})


class IngestSnapshotTest(unittest.TestCase):
    """The child side: load_save -> engines -> articles against DATABASE_URL."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.snapshot = os.path.join(self.tmp, "save.json")
        with open(self.snapshot, "w") as f:
            json.dump({"season": {"calendar_year": 2036, "week_type": "RegularSeason", "week_index": 5}}, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    @mock.patch("pipeline.watch_save.send_notify")
    @mock.patch("pipeline.watch_save.step_articles")
    @mock.patch("pipeline.watch_save.run_weekly_engines")
    @mock.patch("pipeline.load_save.load")
    def test_new_week_full_pipeline(self, mock_load, mock_engines, mock_articles, mock_notify):
        mock_load.return_value = {"league_state": "2036 week 5 -> 2036 week 6", "players": 100, "games_completed": 16}
        mock_articles.return_value = {"status": "published", "week": 6}

        res = ws.ingest_snapshot(self.snapshot, 2036, 6, save_name="CAREER-TEST")

        self.assertEqual(res["status"], "success")
        self.assertFalse(res["dry_run"])
        self.assertTrue(mock_load.call_args[1].get("commit"))
        mock_engines.assert_called_once_with(2036, mode="weekly", log_fn=mock.ANY)
        mock_articles.assert_called_once_with(2036, week=6)
        mock_notify.assert_called_once()

    @mock.patch("pipeline.watch_save.send_notify")
    @mock.patch("pipeline.watch_save.step_articles")
    @mock.patch("pipeline.watch_save.run_weekly_engines")
    @mock.patch("pipeline.load_save.load")
    def test_dry_run_skips_commit_engines_and_articles(self, mock_load, mock_engines, mock_articles, mock_notify):
        mock_load.return_value = {"league_state": "rolled back", "players": 100, "games_completed": 16}

        res = ws.ingest_snapshot(self.snapshot, 2036, 6, dry_run=True)

        self.assertTrue(res["dry_run"])
        self.assertFalse(mock_load.call_args[1].get("commit"))
        mock_engines.assert_not_called()
        mock_articles.assert_not_called()

    @mock.patch("pipeline.watch_save.send_notify")
    @mock.patch("pipeline.watch_save.step_articles")
    @mock.patch("pipeline.watch_save.run_weekly_engines")
    @mock.patch("pipeline.load_save.load")
    def test_no_articles_flag(self, mock_load, mock_engines, mock_articles, mock_notify):
        mock_load.return_value = {"league_state": "committed", "players": 100, "games_completed": 16}

        res = ws.ingest_snapshot(self.snapshot, 2036, 6, no_articles=True)

        self.assertEqual(res["status"], "success")
        mock_engines.assert_called_once()
        mock_articles.assert_not_called()

    @mock.patch("pipeline.watch_save.send_notify")
    @mock.patch("pipeline.watch_save.step_articles")
    @mock.patch("pipeline.watch_save.run_weekly_engines")
    @mock.patch("pipeline.load_save.load")
    def test_no_engines_flag_loads_only(self, mock_load, mock_engines, mock_articles, mock_notify):
        mock_load.return_value = {"league_state": "committed", "players": 100, "games_completed": 16}

        res = ws.ingest_snapshot(self.snapshot, 2036, 6, no_engines=True)

        self.assertEqual(res["status"], "success")
        self.assertTrue(mock_load.call_args[1].get("commit"))
        mock_engines.assert_not_called()
        mock_articles.assert_not_called()

    def test_run_ingest_passes_database_url_to_the_child(self):
        def fake_run(cmd, cwd, env):
            with open(os.path.join(self.tmp, "ingest_result.json"), "w") as f:
                json.dump({"status": "success", "argv": cmd}, f)
            return mock.Mock(returncode=0)

        with mock.patch("pipeline.watch_save.subprocess.run", side_effect=fake_run) as run:
            res = ws.run_ingest(self.snapshot, "host=localhost dbname=madden_colts user=postgres", 2034, 6,
                                no_articles=True, save_name="CAREER-A")
        self.assertEqual(res["status"], "success")
        self.assertEqual(run.call_args[1]["env"]["DATABASE_URL"], "host=localhost dbname=madden_colts user=postgres")
        self.assertIn("--no-articles", res["argv"])
        self.assertEqual(res["argv"][res["argv"].index("--week") + 1], "6")

    def _run_ingest_env(self, dsn, save_name):
        def fake_run(cmd, cwd, env):
            with open(os.path.join(self.tmp, "ingest_result.json"), "w") as f:
                json.dump({"status": "success"}, f)
            self.child_env = env
            return mock.Mock(returncode=0)

        self.child_env = None
        with mock.patch("pipeline.watch_save.subprocess.run", side_effect=fake_run):
            ws.run_ingest(self.snapshot, dsn, 2026, 1, no_articles=True, save_name=save_name)
        return self.child_env

    def test_a_tuning_ingest_disables_every_model_call(self):
        """P2-07: a CAREER-TUNE-* ingest into madden_tune_* must not reach a model, local or paid."""
        env = self._run_ingest_env("host=localhost dbname=madden_tune_p2a user=postgres", "CAREER-TUNE-P2A")
        self.assertEqual(env["MU_NO_LLM"], "1")

    def test_an_explicit_mu_no_llm_override_is_left_alone(self):
        with mock.patch.dict(os.environ, {"MU_NO_LLM": "0"}):
            env = self._run_ingest_env("host=localhost dbname=madden_tune_p2a user=postgres", "CAREER-TUNE-P2A")
        self.assertEqual(env["MU_NO_LLM"], "0", "an operator's explicit override wins")

    def test_a_live_ingest_keeps_model_calls(self):
        env = self._run_ingest_env("host=localhost dbname=madden_colts user=postgres", "CAREER-MU2026")
        self.assertIn(env.get("MU_NO_LLM"), (None, ""))

    def test_queue_nightly_jobs_hook(self):
        logs = []
        with mock.patch("pipeline.gpu_queue.queue.GPUQueue") as mock_q_cls:
            mock_q = mock.MagicMock()
            mock_q.list_jobs.return_value = []
            mock_q_cls.return_value = mock_q
            ws.queue_nightly_jobs(2036, 7, log_fn=logs.append)
            self.assertTrue(any("Nightly queue hook" in l for l in logs))
            self.assertTrue(any("GPU queue hook: checked queue, 0 pending jobs found" in l for l in logs))

    def test_queue_nightly_jobs_leaves_pending_jobs_alone(self):
        # Runs while Madden is usually still open: must return at once and not run or fail jobs
        from pipeline.gpu_queue import GPUJob, GPUQueue, SQLiteJobStore, STATUS_PENDING, mock_process_running
        queue = GPUQueue(store=SQLiteJobStore(":memory:"))
        queue.enqueue(GPUJob(id="headshot_job", kind="headshot", model_id="sdxl"))
        logs = []
        with mock.patch("pipeline.gpu_queue.queue.GPUQueue", return_value=queue), mock_process_running(True):
            ws.queue_nightly_jobs(2036, 7, log_fn=logs.append)
        self.assertEqual(queue.get_job("headshot_job").status, STATUS_PENDING)
        self.assertTrue(any("1 pending jobs found" in l for l in logs))

    def test_prepare_next_week_mod_pack_no_user_teams(self):
        logs = []
        with mock.patch("db.franchises.load_registry", return_value={"active": None, "franchises": {}}):
            ws.prepare_next_week_mod_pack("host=localhost dbname=madden_test", 2036, 8, log_fn=logs.append)
            self.assertTrue(any("no user teams registered" in l for l in logs))

    def test_prepare_next_week_mod_pack_with_user_teams(self):
        logs = []
        registry = {
            "active": "1",
            "franchises": {"1": {"db": "madden_car", "user_teams": ["CAR"]}}
        }
        mock_game = mock.MagicMock(
            id=1, season=2036, week=8, game_time="Sun 1:00 PM",
            game_type="REGULAR_SEASON", home="CAR", away="TB"
        )
        mock_choice = mock.MagicMock(
            window="FOX", tier=2, score=45.0, bca=True, pack_name="Mu FOX Tier2 BCA"
        )
        with mock.patch("db.franchises.load_registry", return_value=registry), \
             mock.patch("psycopg.connect") as mock_conn, \
             mock.patch("pipeline.mod_packs.load_games", return_value=[mock_game]), \
             mock.patch("pipeline.mod_packs.choose", return_value=mock_choice):
            ws.prepare_next_week_mod_pack("host=localhost dbname=madden_car", 2036, 8, log_fn=logs.append)
            self.assertTrue(any("Next-week mod pack plan for CAR" in l for l in logs))
            self.assertTrue(any("Mu FOX Tier2 BCA" in l for l in logs))
            # MMC_CONFIG points at a missing file (fixture): logged, nothing written
            self.assertTrue(any("not written" in l and "missing" in l for l in logs))


def fake_game(home="CAR", away="TB"):
    return mock.MagicMock(id=1, season=2036, week=8, game_time="Sun 1:00 PM", game_type="REGULAR_SEASON",
                          home=home, away=away)


class ModPackWriteTest(unittest.TestCase):
    """prepare_next_week_mod_pack writes the pack (mod_packs.write_pack, selected) unless the manager is open."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config = os.path.join(self.tmp, "manager_config.json")
        base = "m27_fox_presentation.fbmod:False|m27_cbs_presentation.fbmod:True|Other.fbmod:True"
        with open(self.config, "w") as f:
            json.dump({"Games": {"Madden27": {"Packs": {"Mu Base": base}, "Options": {"SelectedPack": "Default"}}}}, f)
        registry = {"active": "1", "franchises": {"1": {"db": "madden_car", "user_teams": ["CAR"]}}}
        self.patches = [
            mock.patch.dict(os.environ, {"MMC_CONFIG": self.config}),
            mock.patch("db.franchises.load_registry", return_value=registry),
            mock.patch("psycopg.connect"),
            mock.patch("pipeline.mod_packs.load_games", return_value=[fake_game()]),
            mock.patch("pipeline.mod_packs.choose", return_value=mod_packs.Choice("FOX", 2, 45.0, False)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        mock.patch.stopall()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def packs(self):
        with open(self.config) as f:
            return json.load(f)["Games"]["Madden27"]

    def test_pack_written_and_selected(self):
        logs = []
        with mock.patch("pipeline.mod_packs.manager_running", return_value=False), \
             mock.patch("pipeline.mod_packs.write_pack", side_effect=REAL_WRITE_PACK) as wp:
            written = ws.prepare_next_week_mod_pack("host=localhost dbname=madden_car", 2036, 8, log_fn=logs.append)
        self.assertEqual(written, ["Mu FOX Tier2 Mudded"])
        wp.assert_called_once()
        self.assertTrue(wp.call_args[1]["select"])
        cfg = self.packs()
        self.assertEqual(cfg["Options"]["SelectedPack"], "Mu FOX Tier2 Mudded")
        self.assertIn("m27_fox_presentation.fbmod:True", cfg["Packs"]["Mu FOX Tier2 Mudded"])
        self.assertIn("m27_cbs_presentation.fbmod:False", cfg["Packs"]["Mu FOX Tier2 Mudded"])
        self.assertTrue(any("wrote 'Mu FOX Tier2 Mudded'" in l for l in logs))

    def test_skipped_while_manager_running(self):
        logs = []
        with mock.patch("pipeline.mod_packs.manager_running", return_value=True), \
             mock.patch("pipeline.mod_packs.write_pack") as wp:
            written = ws.prepare_next_week_mod_pack("host=localhost dbname=madden_car", 2036, 8, log_fn=logs.append)
        self.assertEqual(written, [])
        wp.assert_not_called()
        self.assertTrue(any("MMCModManager.exe is open" in l for l in logs))
        self.assertEqual(self.packs()["Options"]["SelectedPack"], "Default")

    def test_skipped_without_base_pack(self):
        with open(self.config, "w") as f:
            json.dump({"Games": {"Madden27": {"Packs": {}}}}, f)
        logs = []
        with mock.patch("pipeline.mod_packs.manager_running", return_value=False), \
             mock.patch("pipeline.mod_packs.write_pack") as wp:
            written = ws.prepare_next_week_mod_pack("host=localhost dbname=madden_car", 2036, 8, log_fn=logs.append)
        self.assertEqual(written, [])
        wp.assert_not_called()
        self.assertTrue(any("not written" in l and "Mu Base" in l for l in logs))


class LeagueOfficeIngestTest(unittest.TestCase):
    """The child side with the league office: load -> engines -> league office -> articles -> mod pack."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.snapshot = os.path.join(self.tmp, "save.json")
        with open(self.snapshot, "w") as f:
            json.dump({"season": {"calendar_year": 2036, "week_type": "RegularSeason", "week_index": 5}}, f)
        self.save = os.path.join(self.tmp, "CAREER-A")
        with open(self.save, "w") as f:
            f.write("save")
        self.calls = mock.Mock()
        self.env = mock.patch.dict(os.environ, {"DATABASE_URL": "host=localhost dbname=madden_test user=postgres"})
        self.env.start()
        def record(name, ret=None):
            def side_effect(*a, **k):
                getattr(self.calls, name)()
                return ret
            return side_effect
        mock.patch("pipeline.load_save.load",
                   side_effect=record("load", {"league_state": "x", "players": 1, "games_completed": 1})).start()
        mock.patch("pipeline.watch_save.run_weekly_engines", side_effect=record("engines")).start()
        mock.patch("pipeline.watch_save.queue_nightly_jobs").start()
        self.articles = mock.patch("pipeline.watch_save.step_articles",
                                   side_effect=record("articles", {"status": "published", "week": 6})).start()
        self.pack = mock.patch("pipeline.watch_save.prepare_next_week_mod_pack",
                               side_effect=record("mod_pack", [])).start()
        self.notify = mock.patch("pipeline.watch_save.send_notify").start()

    def tearDown(self):
        mock.patch.stopall()
        self.env.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ingest(self, lo_result, **kw):
        def fake_lo(*a, **k):
            self.calls.league_office(*a, **k)
            return dict({"status": None, "run_id": 7, "error": None, "output_sha256": None, "replaced": False},
                        **lo_result)
        self.lo = mock.patch("pipeline.watch_save.run_league_office", side_effect=fake_lo).start()
        return ws.ingest_snapshot(self.snapshot, 2036, 6, save_name="CAREER-A", save_path=self.save,
                                  league_office=True, save_sha256="abc", **kw)

    def order(self):
        return [c[0] for c in self.calls.mock_calls]

    def test_league_office_runs_after_engines_before_articles(self):
        res = self.ingest({"status": "written", "replaced": True, "output_sha256": "out"})
        self.assertEqual(self.order(), ["load", "engines", "league_office", "articles", "mod_pack"])
        args, kw = self.lo.call_args
        self.assertEqual(args[:3], (self.snapshot, self.save, "host=localhost dbname=madden_test user=postgres"))
        self.assertFalse(kw["dry_run"])
        self.assertEqual(kw["expected_sha256"], "abc")
        self.assertEqual(res["league_office"]["output_sha256"], "out")
        self.assertIn("League office wrote CAREER-A", self.notify.call_args[0][0])
        # the mod pack is for the save's current week (Madden's next unplayed week)
        self.assertEqual(self.pack.call_args[0][1:3], (2036, 6))

    def test_new_players_are_bootstrapped_before_the_league_office(self):
        def load(*a, **k):
            self.calls.load()
            return {"league_state": "x", "players": 4, "players_created": 1, "players_created_ids": {5: 999},
                    "games_completed": 16}
        mock.patch("pipeline.load_save.load", side_effect=load).start()
        def boot_them(*a, **k):
            self.calls.new_players()
            return 1
        boot = mock.patch("pipeline.watch_save.bootstrap_new_players", side_effect=boot_them).start()
        res = self.ingest({"status": "written", "replaced": True, "output_sha256": "out"})
        self.assertEqual(self.order(), ["load", "new_players", "engines", "league_office", "articles", "mod_pack"])
        self.assertEqual(boot.call_args[0][2], {5: 999})
        self.assertEqual(res["new_players"], 1)

    def test_the_newcomer_check_runs_even_when_the_load_creates_nobody(self):
        """Eligibility is the database's, so a stranded newcomer is healed on any later ingest (JR, 2026-11-10)."""
        boot = mock.patch("pipeline.watch_save.bootstrap_new_players", return_value=2).start()
        res = self.ingest({"status": "written"})
        boot.assert_called_once()
        self.assertEqual(boot.call_args[0][2], {})
        self.assertEqual(res["new_players"], 2)

    def test_league_office_off(self):
        self.lo = mock.patch("pipeline.watch_save.run_league_office").start()
        res = ws.ingest_snapshot(self.snapshot, 2036, 6, save_name="CAREER-A", save_path=self.save)
        self.lo.assert_not_called()
        self.assertIsNone(res["league_office"])
        self.assertEqual(self.order(), ["load", "engines", "articles", "mod_pack"])

    def test_dry_run_flag_passed_through(self):
        self.ingest({"status": "dry_run"}, league_office_dry_run=True)
        self.assertTrue(self.lo.call_args[1]["dry_run"])

    def test_failure_still_runs_articles_and_notifies(self):
        res = self.ingest({"status": "failed", "error": "CompositionError: pgid 5 clash"})
        self.assertEqual(self.order(), ["load", "engines", "league_office", "articles", "mod_pack"])
        self.assertEqual(res["status"], "success")
        titles = [c[1].get("title") for c in self.notify.call_args_list]
        self.assertIn("Mu: league office failed", titles)
        self.assertIn("Mu: week done", titles)

    def test_needs_recovery_notifies_with_the_command(self):
        self.ingest({"status": "needs_recovery",
                     "error": "replaced; run `python3 -m pipeline.league_office --recover 7`"})
        msg = next(c[0][0] for c in self.notify.call_args_list if c[1].get("title") == "Mu: league office needs recovery")
        self.assertIn("python3 -m pipeline.league_office --recover 7", msg)
        self.assertIn("articles", self.order())

    def test_franchise_open_is_logged_not_fatal(self):
        logs = []
        self.lo = mock.patch("pipeline.watch_save.run_league_office",
                             return_value={"status": "franchise_open", "error": "Madden27.exe is running"}).start()
        res = ws.ingest_snapshot(self.snapshot, 2036, 6, save_name="CAREER-A", save_path=self.save,
                                 league_office=True, log_fn=logs.append)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["league_office"]["status"], "franchise_open")
        self.assertIn("LEAGUE_OFFICE_FRANCHISE_CLOSED=1 python3 -m pipeline.league_office",
                      res["league_office"]["retry_command"])
        self.articles.assert_called_once()
        self.assertTrue(any("[WARN] League office not run" in l for l in logs))
        titles = [c[1].get("title") for c in self.notify.call_args_list]
        self.assertEqual(titles, ["Mu: week done"], "a franchise-open refusal is not an error alert")

    def test_offseason_stage_only_loads_and_runs_the_league_office(self):
        def fake_lo(*a, **k):
            self.calls.league_office()
            return {"status": "written", "replaced": True, "output_sha256": "o", "run_id": 3}
        mock.patch("pipeline.watch_save.run_league_office", side_effect=fake_lo).start()
        res = ws.ingest_snapshot(self.snapshot, 2036, None, save_name="CAREER-A", save_path=self.save,
                                 league_office=True, stage_only=True)
        self.assertEqual(self.order(), ["load", "league_office"])
        self.assertEqual(res["league_office"]["status"], "written")


    def offseason_ingest(self, window):
        def fake_lo(*a, **k):
            self.calls.league_office()
            return {"status": "written", "replaced": True, "output_sha256": "o", "run_id": 3,
                    "retirement_window": window}
        mock.patch("pipeline.watch_save.run_league_office", side_effect=fake_lo).start()
        ws.ingest_snapshot(self.snapshot, 2036, None, save_name="CAREER-A", save_path=self.save,
                           league_office=True, stage_only=True)
        return self.notify.call_args

    def test_offseason_notification_carries_the_retirement_window_line(self):
        call = self.offseason_ingest({"role": "window", "stop": "RESIGN", "restored": [], "conflicts": [],
                                      "vanished": [], "blocked": [{"pgid": 2, "reasons": ["authority_missing"]}]})
        self.assertIn("Retirement window RESIGN: 0 restored; 1 late retirement(s) not restored", call[0][0])
        self.assertEqual(call[1]["priority"], "default")
        self.assertEqual(call[1]["title"], "Mu: offseason stage done")

    def test_vanished_rostered_player_makes_it_high_priority(self):
        call = self.offseason_ingest({"role": "window", "stop": "DRAFT", "restored": [], "conflicts": [],
                                      "blocked": [], "vanished": [{"pgid": 4, "kind": "VANISHED_ROSTERED",
                                                                   "name": "A B", "last": {"status": "Signed",
                                                                                           "team_index": 3}}]})
        self.assertIn("A B (team 3)", call[0][0])
        self.assertEqual(call[1]["priority"], "high")

    def test_no_window_line_without_a_window_summary(self):
        call = self.offseason_ingest(None)
        self.assertNotIn("Retirement window", call[0][0])
        self.assertEqual(call[1]["priority"], "default")


class RunLeagueOfficeTest(unittest.TestCase):
    """run_league_office: the in-process call into pipeline.league_office.run and its error mapping."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.snapshot = os.path.join(self.tmp, "save.json")
        with open(self.snapshot, "w") as f:
            json.dump({"season": {"calendar_year": 2036}}, f)
        self.save = os.path.join(self.tmp, "CAREER-A")
        with open(self.save, "w") as f:
            f.write("save")
        self.connect = mock.patch("psycopg.connect").start()

    def tearDown(self):
        mock.patch.stopall()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_lo(self, side_effect, **kw):
        with mock.patch("pipeline.league_office.run", side_effect=side_effect) as run:
            res = ws.run_league_office(self.snapshot, self.save, "dbname=madden_test", **kw)
        return res, run

    def test_written(self):
        def fake(cur, snapshot, save_path, stage, dry_run, confirm_franchise_closed, out):
            out(json.dumps({"run_id": 4, "stage": "REGULAR_WEEK", "status": "WRITTEN", "replaced": True,
                            "output_sha256": "new"}))
            return 4
        res, run = self.run_lo(fake)
        self.assertEqual((res["status"], res["run_id"], res["output_sha256"]), ("written", 4, "new"))
        self.assertEqual(run.call_args[1]["stage"], "auto")
        self.assertFalse(run.call_args[1]["dry_run"])
        self.assertEqual(run.call_args[0][2], self.save)

    def test_written_carries_the_retirement_window_summary(self):
        def fake(*a, out, **k):
            out(json.dumps({"run_id": 4, "status": "WRITTEN", "replaced": True,
                            "summary": {"retirement_window": {"role": "decision", "decided": {}}}}))
            return 4
        res, _ = self.run_lo(fake)
        self.assertEqual(res["retirement_window"], {"role": "decision", "decided": {}})

    def test_dry_run_passed_to_league_office(self):
        res, run = self.run_lo(lambda *a, **k: 5, dry_run=True)
        self.assertTrue(run.call_args[1]["dry_run"])
        self.assertEqual(res["status"], "dry_run")

    def test_env_var_confirms_franchise_closed(self):
        with mock.patch.dict(os.environ, {"LEAGUE_OFFICE_FRANCHISE_CLOSED": "1"}):
            _, run = self.run_lo(lambda *a, **k: 5, dry_run=True)
        self.assertTrue(run.call_args[1]["confirm_franchise_closed"])
        with mock.patch.dict(os.environ, {"LEAGUE_OFFICE_FRANCHISE_CLOSED": ""}):
            _, run = self.run_lo(lambda *a, **k: 5, dry_run=True)
        self.assertFalse(run.call_args[1]["confirm_franchise_closed"])

    def test_franchise_open_refusal(self):
        from pipeline import league_office as lo
        res, _ = self.run_lo(lo.FranchiseOpenError("Madden27.exe is running"))
        self.assertEqual(res["status"], "franchise_open")
        self.assertIn("LEAGUE_OFFICE_FRANCHISE_CLOSED", res["error"])

    def test_league_office_errors_are_failures(self):
        from pipeline import league_office as lo
        for err in (lo.CompositionError("clash"), lo.ProgressionIntegrationError("no hook"), lo.StageError("?")):
            res, _ = self.run_lo(err)
            self.assertEqual(res["status"], "failed")
            self.assertIn(type(err).__name__, res["error"])

    def test_failed_report_is_a_failure(self):
        def fake(*a, out, **k):
            out(json.dumps({"run_id": 9, "status": "FAILED", "error": "save_writer refused"}))
            return 9
        res, _ = self.run_lo(fake)
        self.assertEqual((res["status"], res["run_id"]), ("failed", 9))
        self.assertIn("save_writer refused", res["error"])

    def test_recovery_error_names_the_recover_command(self):
        from pipeline import league_office as lo

        def fake(*a, out, **k):
            out(json.dumps({"run_id": 12, "status": "NEEDS_RECOVERY"}))
            raise lo.RecoveryError("the replaced save's sha256 differs")
        res, _ = self.run_lo(fake)
        self.assertEqual(res["status"], "needs_recovery")
        self.assertIn("python3 -m pipeline.league_office --recover 12", res["error"])

    def test_changed_save_is_skipped(self):
        res, run = self.run_lo(lambda *a, **k: 1, expected_sha256="not-the-sha")
        run.assert_not_called()
        self.assertEqual(res["status"], "skipped")


class LeagueOfficeProcessSaveTest(ProcessSaveTest):
    """process_save's league office decisions (flags, offseason stops, its own write)."""

    def offseason_save(self, name, week_index=3, stage=2):
        path = self.save(name, self.LEAGUE_A, week_index=week_index)
        self.snapshots[path]["season"].update(week_type="Offseason", stage="Offseason", offseason_stage=stage)
        return path

    def lo_kw(self):
        return self.ingest.call_args[1]

    def test_new_week_runs_the_league_office(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        self.assertFalse(self.lo_kw()["league_office"], "not on the load that seeds the franchise")
        save = self.save("CAREER-A", self.LEAGUE_A, week_index=6)
        res = self.process(save)
        kw = self.lo_kw()
        self.assertTrue(kw["league_office"])
        self.assertFalse(kw["league_office_dry_run"])
        self.assertEqual(kw["save_path"], save)
        self.assertEqual(kw["save_sha256"], ws.file_sha256(save))
        self.assertEqual(res["classification"], "new_week")

    def test_no_league_office_flag(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        self.process(self.save("CAREER-A", self.LEAGUE_A, week_index=6), no_league_office=True)
        self.assertFalse(self.lo_kw()["league_office"])

    def test_league_office_dry_run_flag(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        self.process(self.save("CAREER-A", self.LEAGUE_A, week_index=6), league_office_dry_run=True)
        self.assertTrue(self.lo_kw()["league_office"])
        self.assertTrue(self.lo_kw()["league_office_dry_run"])

    def test_dry_run_skips_the_league_office(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        self.process(self.save("CAREER-A", self.LEAGUE_A, week_index=6), dry_run=True)
        self.assertFalse(self.lo_kw()["league_office"])

    def test_own_write_is_same_week(self):
        """The league office's write keeps the season and week: reading it again is a same_week no-op."""
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        save = self.save("CAREER-A", self.LEAGUE_A, week_index=6)
        self.process(save)
        with open(save, "w") as f:
            f.write("written by the league office")
        res = self.process(save)
        self.assertEqual((res["status"], res["reason"]), ("no_op", "same_week"))
        self.assertEqual(self.ingest.call_count, 2)

    def test_offseason_stop_new_then_same_stage(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        done = []
        with mock.patch("pipeline.watch_save.league_office_stage_done", side_effect=lambda dsn, info: bool(done)):
            res = self.process(self.offseason_save("CAREER-A"))
            self.assertEqual(res["classification"], "new_stage")
            self.assertTrue(self.lo_kw()["league_office"])
            self.assertTrue(self.lo_kw()["stage_only"])
            done.append(True)      # the league office wrote it: league_office_runs has a WRITTEN row
            res = self.process(self.offseason_save("CAREER-A"))
            self.assertEqual((res["status"], res["reason"]), ("no_op", "same_stage"))
        self.assertEqual(self.ingest.call_count, 2)

    def test_offseason_stop_without_league_office_is_a_no_op(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        res = self.process(self.offseason_save("CAREER-A"), no_league_office=True)
        self.assertEqual(res["reason"], "no_current_week")


class FreezeProcessSaveTest(ProcessSaveTest):
    """The paired database + save freeze after every committed ingest (pipeline/weekly_freeze.py)."""

    offseason_save = LeagueOfficeProcessSaveTest.offseason_save

    def test_new_week_is_frozen_with_its_save(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        save = self.save("CAREER-A", self.LEAGUE_A, week_index=6)
        res = self.process(save)
        args, kw = self.freezes.call_args
        self.assertEqual(args[0], "madden_l91a17291")
        self.assertEqual(args[2], save)
        self.assertEqual(args[3], "2034_w07")
        self.assertEqual((kw["season"], kw["week"]), (2034, 7))
        self.assertEqual(res["freeze"]["label"], "2034_w07")
        self.assertTrue(res["freeze"]["ok"])

    def test_seeding_load_is_frozen(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        self.assertEqual(self.freezes.call_count, 1)

    def test_no_freeze_dry_run_and_no_op_skip_it(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True, no_freeze=True)
        self.process(self.save("CAREER-A", self.LEAGUE_A, week_index=6), dry_run=True)
        self.process(self.save("CAREER-A", self.LEAGUE_A, week_index=5))     # same week: no-op
        self.freezes.assert_not_called()

    def test_offseason_stop_label(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        stage = {"stage": "FREE_AGENCY", "wave": 2, "season": 2034, "week": 26}
        with mock.patch("pipeline.watch_save.league_office_stage", return_value=stage), \
                mock.patch("pipeline.watch_save.league_office_stage_done", return_value=False):
            res = self.process(self.offseason_save("CAREER-A"))
        self.assertEqual(self.freezes.call_args[0][3], "2034_off_FREE_AGENCY_w2")
        self.assertEqual(res["freeze"]["stage"], stage)

    def test_freeze_failure_is_not_fatal(self):
        self.process(self.save("CAREER-A", self.LEAGUE_A), auto_init=True)
        self.freezes.side_effect = RuntimeError("pg_dump missing")
        res = self.process(self.save("CAREER-A", self.LEAGUE_A, week_index=6))
        self.assertEqual(res["status"], "success")
        self.assertFalse(res["freeze"]["ok"])
        self.assertIn("weekly freeze failed", self.notify.call_args[1]["title"])


class WatchLoopLeagueOfficeTest(unittest.TestCase):
    """The daemon's side: its own write isn't re-ingested; a franchise-open refusal is retried later."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.save = os.path.join(self.tmp, "CAREER-A")
        with open(self.save, "w") as f:
            f.write("week 6")
        t = time.time() - 60
        os.utime(self.save, (t, t))
        self.lock = os.path.join(self.tmp, "run.lock")
        self.debouncer = ws.FileDebouncer(debounce_seconds=0)     # settles on the second look at a change
        self.pending = {}
        mock.patch("pipeline.watch_save.send_notify").start()
        mock.patch("pipeline.gpu_queue.process_check.is_madden_running", return_value=True).start()

    def tearDown(self):
        mock.patch.stopall()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def league_office_writes(self, *a, **k):
        with open(self.save, "w") as f:
            f.write("week 6 + league office")
        t = time.time() - 30
        os.utime(self.save, (t, t))
        return {"status": "success", "snapshot_path": "s.json", "dsn": "dbname=madden_x",
                "league_office": {"status": "written", "replaced": True,
                                  "output_sha256": ws.file_sha256(self.save)}}

    def poll(self, **kw):
        ws.poll_once(self.tmp, "CAREER-*", self.debouncer, self.pending, self.lock, log_fn=lambda m: None, **kw)

    def test_own_write_not_reingested(self):
        with mock.patch("pipeline.watch_save.process_save", side_effect=self.league_office_writes) as ps:
            self.poll()
            self.assertEqual(ps.call_count, 1)
            self.poll()     # the file changed (the league office's write): settles, recognised by sha256
            self.poll()
            self.assertEqual(ps.call_count, 1)
        # Madden saving again afterwards is a new save
        with open(self.save, "w") as f:
            f.write("week 7")
        t = time.time() - 20
        os.utime(self.save, (t, t))
        with mock.patch("pipeline.watch_save.process_save", return_value={"status": "success"}) as ps:
            self.poll()
            self.poll()
            self.assertEqual(ps.call_count, 1)

    def franchise_open(self, *a, **k):
        return {"status": "success", "snapshot_path": os.path.join(self.tmp, "save.json"), "dsn": "dbname=madden_x",
                "save_sha256": ws.file_sha256(self.save), "league_office": {"status": "franchise_open"}}

    def test_franchise_open_retried_once_madden_closes(self):
        with mock.patch("pipeline.watch_save.process_save", side_effect=self.franchise_open):
            self.poll(league_office_dry_run=False)
        self.assertIn(self.save, self.pending)
        with mock.patch("pipeline.watch_save.run_league_office_child") as child:
            ws.retry_league_office(self.pending, self.debouncer, self.lock, madden_running=lambda: True,
                                   log_fn=lambda m: None)
            child.assert_not_called()
            self.assertIn(self.save, self.pending)
            child.return_value = {"status": "written", "replaced": False, "run_id": 3}
            ws.retry_league_office(self.pending, self.debouncer, self.lock, madden_running=lambda: False,
                                   log_fn=lambda m: None)
            child.assert_called_once()
            self.assertEqual(child.call_args[0][:3], (os.path.join(self.tmp, "save.json"), "dbname=madden_x",
                                                     self.save))
            self.assertEqual(child.call_args[1]["save_sha256"], ws.file_sha256(self.save))
        self.assertEqual(self.pending, {})

    def test_retry_that_replaces_the_save_refreezes_the_week(self):
        def franchise_open_with_freeze(*a, **k):
            res = self.franchise_open()
            res.update(db="madden_x", freeze={"label": "2034_w07", "season": 2034, "week": 7, "stage": None,
                                              "ok": True})
            return res
        with mock.patch("pipeline.watch_save.process_save", side_effect=franchise_open_with_freeze):
            self.poll()
        with mock.patch("pipeline.watch_save.run_league_office_child",
                        return_value={"status": "written", "replaced": True, "output_sha256": "abc", "run_id": 4}), \
                mock.patch("pipeline.weekly_freeze.freeze", return_value={"dump_bytes": 1, "save_name": "x"}) as fz:
            ws.retry_league_office(self.pending, self.debouncer, self.lock, madden_running=lambda: False,
                                   log_fn=lambda m: None)
        args, kw = fz.call_args
        self.assertEqual(args[:4], ("madden_x", "dbname=madden_x", self.save, "2034_w07"))
        self.assertTrue(kw["extra"]["refrozen_after_retry"])

    def test_newer_save_drops_the_retry(self):
        with mock.patch("pipeline.watch_save.process_save", side_effect=self.franchise_open):
            self.poll()
        with open(self.save, "w") as f:
            f.write("week 7")
        t = time.time() - 20
        os.utime(self.save, (t, t))
        with mock.patch("pipeline.watch_save.process_save", return_value={"status": "success"}):
            self.poll()
            self.poll()
        self.assertEqual(self.pending, {})


class FranchiseRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "franchises.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_db_names(self):
        from db import franchises as fr
        self.assertEqual(fr.db_name_for(-1851690351), "madden_l91a17291")
        self.assertEqual(fr.db_name_for(1, name="Colts 2034!"), "madden_colts_2034")
        self.assertEqual(fr.db_name_for(1, name="2036"), "madden_2036_2", "reserved names get a suffix")
        self.assertEqual(fr.db_name_for(1, name="colts", taken={"madden_colts"}), "madden_colts_2")

    def test_register_find_and_activate(self):
        from db import franchises as fr
        with fr.editing_registry(self.path) as reg:
            fr.register(reg, 11, name="colts", user_teams=["IND"], save_name="CAREER-A")
            fr.register(reg, 22, name="bears")
        reg = fr.load_registry(self.path)
        self.assertEqual(reg["active"], "11")
        self.assertEqual(fr.find(reg, "madden_bears")[0], "22")
        self.assertEqual(fr.find(reg, "colts")[0], "11")
        with self.assertRaises(fr.FranchiseError):
            fr.register(reg, 11)

    def test_export_active_dsn(self):
        from db import franchises as fr
        with fr.editing_registry(self.path) as reg:
            fr.register(reg, 11, name="colts")
        with mock.patch.dict(os.environ, {"MU_FRANCHISE_REGISTRY": self.path}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            try:
                self.assertEqual(fr.export_active_dsn(),
                                 "dbname=madden_colts host=localhost user=postgres password=postgres")
                os.environ["DATABASE_URL"] = "host=h dbname=madden_test user=u"
                self.assertEqual(fr.export_active_dsn(), "host=h dbname=madden_test user=u", "explicit DATABASE_URL wins")
            finally:
                os.environ["DATABASE_URL"] = "host=localhost dbname=madden_test user=postgres password=postgres"

    def test_reserved_databases_are_never_created_or_dropped(self):
        from db import franchises as fr
        for db in ("madden_2036", "madden_test", "madden", "postgres", "other_db"):
            with self.assertRaises(fr.FranchiseError):
                fr.create_db(db)
            with self.assertRaises(fr.FranchiseError):
                fr.drop_db(db)

    def test_cli_dbname(self):
        from db import franchises as fr
        with mock.patch.dict(os.environ, {"MU_FRANCHISE_REGISTRY": self.path}):
            self.assertEqual(fr.main(["dbname"]), 1)
            with fr.editing_registry(self.path) as reg:
                fr.register(reg, 11, name="colts")
            with mock.patch("builtins.print") as p:
                self.assertEqual(fr.main(["dbname"]), 0)
            p.assert_called_once_with("madden_colts")


class FindFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_find_save_files(self):
        f1 = os.path.join(self.tmp, "CAREER-GIANTS")
        f2 = os.path.join(self.tmp, "CAREER-AUTOSAVE")
        f3 = os.path.join(self.tmp, "ROSTER-DATA")
        for f in (f1, f2, f3):
            with open(f, "w") as fh:
                fh.write("x")

        # Set f2 newer than f1
        now = time.time()
        os.utime(f1, (now - 20, now - 20))
        os.utime(f2, (now - 5, now - 5))

        matches = ws.find_save_files(self.tmp, pattern="CAREER-*")
        self.assertEqual(matches, [f1, f2], "oldest first, so a backlog loads in week order")

        # Passing file path directly
        self.assertEqual(ws.find_save_files(f1), [f1])

    def test_a_tuning_clone_is_ignored(self):
        """P1-24: a CAREER-TUNE-* clone carries the real LeagueID and must not be routed into the live DB."""
        real = os.path.join(self.tmp, "CAREER-MU2026")
        tune = os.path.join(self.tmp, "CAREER-TUNE-A")
        for f in (real, tune):
            with open(f, "w") as fh:
                fh.write("x")
        now = time.time()
        os.utime(real, (now - 20, now - 20))
        os.utime(tune, (now - 5, now - 5))

        self.assertEqual(ws.find_save_files(self.tmp, pattern="CAREER-*"), [real])
        self.assertEqual(ws.find_save_files(tune), [], "a tuning save named directly is ignored too")
        self.assertEqual(ws.find_save_files(self.tmp, pattern="CAREER-*", include_tuning=True), [real, tune])
        self.assertEqual(ws.find_save_files(tune, include_tuning=True), [tune])

    def test_a_tuning_save_may_only_land_in_a_tuning_database(self):
        ws.check_tuning_destination("CAREER-TUNE-A", "madden_tune_a")
        ws.check_tuning_destination("CAREER-MU2026", "madden_mu2026")
        with self.assertRaises(ws.WatcherError):
            ws.check_tuning_destination("CAREER-TUNE-A", "madden_mu2026")
        self.assertTrue(ws.is_tuning_save("/saves/CAREER-TUNE-x"))
        self.assertFalse(ws.is_tuning_save("CAREER-MUBASE"))
        # Madden's Save As cannot type a hyphen, so an in-game clone has no hyphen after TUNE (M-0140).
        self.assertTrue(ws.is_tuning_save("/saves/CAREER-TUNECOACHBEFORE"))
        with self.assertRaises(ws.WatcherError):
            ws.check_tuning_destination("CAREER-TUNECOACHBEFORE", "madden_mu2026")


class TuningDsnSetupTest(unittest.TestCase):
    """P2-04/P2-06: --dsn bypasses routing, so creating/resetting a tuning database is explicit and tuning-only."""

    def setUp(self):
        self.logs = []
        self.log = self.logs.append

    def _patches(self, exists=False, state="empty"):
        return [
            mock.patch("pipeline.watch_save.franchises.db_exists", return_value=exists),
            mock.patch("pipeline.watch_save.franchises.db_state", return_value=state),
            mock.patch("pipeline.watch_save.franchises.create_db"),
            mock.patch("pipeline.watch_save.franchises.drop_db"),
            mock.patch("db.seed_clean_db.run_clean_seed"),
            mock.patch("pipeline.watch_save.franchises.stamp_league_id"),
        ]

    def test_no_setup_flags_is_the_old_behaviour(self):
        ps = self._patches()
        mocks = [p.start() for p in ps]
        for p in ps:
            self.addCleanup(p.stop)
        self.assertFalse(ws.setup_tuning_dsn("madden_tune_a", "dbname=madden_tune_a", "CAREER-TUNE-A",
                                             2026, 1, log_fn=self.log))
        mocks[2].assert_not_called()

    def test_it_refuses_a_non_tuning_destination(self):
        with self.assertRaises(ws.WatcherError):
            ws.setup_tuning_dsn("madden_mu2026", "dbname=madden_mu2026", "CAREER-TUNE-A", 2026, 1,
                                auto_init=True, log_fn=self.log)
        with self.assertRaises(ws.WatcherError):
            ws.setup_tuning_dsn("madden_tune_a", "dbname=madden_tune_a", "CAREER-MU2026", 2026, 1,
                                auto_init=True, log_fn=self.log)

    def test_reseed_needs_the_database_name(self):
        with self.assertRaises(ws.WatcherError):
            ws.setup_tuning_dsn("madden_tune_a", "dbname=madden_tune_a", "CAREER-TUNE-A", 2026, 1,
                                reseed=True, confirm_reseed="madden_tune_b", log_fn=self.log)

    def test_it_creates_and_seeds_a_missing_tuning_database(self):
        ps = self._patches(exists=False, state="empty")
        mocks = [p.start() for p in ps]
        for p in ps:
            self.addCleanup(p.stop)
        create, seed = mocks[2], mocks[4]
        self.assertEqual(ws.setup_tuning_dsn("madden_tune_a", "dbname=madden_tune_a", "CAREER-TUNE-A", 2026, 1,
                                             auto_init=True, log_fn=self.log, league_id=1287775425), "seeded")
        create.assert_called_once_with("madden_tune_a")
        self.assertEqual(seed.call_args[1]["season"], 2026)
        self.assertEqual(seed.call_args[1]["week"], 1)
        self.assertEqual(seed.call_args[1]["user_teams"], [])
        mocks[5].assert_called_once_with("dbname=madden_tune_a", 1287775425)

    def test_reseed_drops_then_seeds(self):
        ps = self._patches(exists=False, state="empty")
        mocks = [p.start() for p in ps]
        for p in ps:
            self.addCleanup(p.stop)
        drop, create, seed = mocks[3], mocks[2], mocks[4]
        self.assertEqual(ws.setup_tuning_dsn("madden_tune_a", "dbname=madden_tune_a", "CAREER-TUNE-A", 2026, 1,
                                             reseed=True, confirm_reseed="madden_tune_a", log_fn=self.log), "seeded")
        drop.assert_called_once_with("madden_tune_a")
        create.assert_called_once_with("madden_tune_a")
        seed.assert_called_once()

    def test_a_dry_run_that_would_create_is_a_no_op(self):
        ps = self._patches(exists=False, state="empty")
        mocks = [p.start() for p in ps]
        for p in ps:
            self.addCleanup(p.stop)
        create = mocks[2]
        self.assertEqual(ws.setup_tuning_dsn("madden_tune_a", "dbname=madden_tune_a", "CAREER-TUNE-A", 2026, 1,
                                             auto_init=True, dry_run=True, log_fn=self.log), "no_op")
        create.assert_not_called()


@pytest.mark.integration
class WatchSaveIntegrationTest(unittest.TestCase):
    """End-to-end integration test against madden_test using CAREER-GIANTS-AUTOSAVE."""

    def test_sample_save_dry_run_into_madden_test(self):
        sample_save = os.path.join(ws.ROOT, "CAREER-GIANTS-AUTOSAVE")
        if not os.path.exists(sample_save):
            self.skipTest("CAREER-GIANTS-AUTOSAVE not present")

        # Snapshot into a temp folder and run dry-run with --force as season 2099
        tmp_folder = tempfile.mkdtemp(prefix="watch_test_")
        try:
            dsn = os.environ["DATABASE_URL"]
            stored_season, stored_week = ws.get_league_state(dsn)
            res = ws.process_save(
                sample_save, season=2099, dry_run=True, force=True, run_folder=tmp_folder, dsn=dsn
            )
            self.assertEqual(res["status"], "success")
            self.assertTrue(res["dry_run"])
            self.assertEqual(res["season"], 2099)
            self.assertEqual(res["week"], 5)
            self.assertGreater(res["load_report"]["players"], 2000)
            self.assertGreater(res["load_report"]["games_completed"], 70)

            # Confirm league_state in madden_test was not changed (dry-run rolled back)
            after_season, after_week = ws.get_league_state(dsn)
            self.assertEqual((after_season, after_week), (stored_season, stored_week))
        finally:
            import shutil
            shutil.rmtree(tmp_folder, ignore_errors=True)



class RelationshipProducersStepTest(unittest.TestCase):
    """step_relationship_producers: right after the load on weekly and offseason ingests, off by default, never
    fatal (RELATIONSHIP_EVENT_PRODUCERS_PLAN.md §2.4)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.snapshot = os.path.join(self.tmp, "save.json")
        with open(self.snapshot, "w") as f:
            json.dump({"season": {"calendar_year": 2036, "week_type": "RegularSeason", "week_index": 5,
                                  "stage": "NFLSeason"}, "players": [], "teams": []}, f)
        self.env = mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://u@h/madden_test"})
        self.env.start()
        os.environ.pop(ws.PRODUCERS_ENV, None)

    def tearDown(self):
        self.env.stop()
        mock.patch.stopall()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _conn(self):
        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__.return_value = mock.MagicMock()
        return conn

    def test_off_by_default(self):
        logs = []
        connect = mock.Mock()
        self.assertEqual(ws.step_relationship_producers(self.snapshot, 2036, log_fn=logs.append,
                                                        connect=connect), {"status": "off"})
        connect.assert_not_called()
        with mock.patch.dict(os.environ, {ws.PRODUCERS_ENV: "off"}):
            self.assertEqual(ws.step_relationship_producers(self.snapshot, 2036, connect=connect)["status"], "off")

    def test_on_runs_one_save_tick_and_commits(self):
        conn = self._conn()
        with mock.patch.dict(os.environ, {ws.PRODUCERS_ENV: "on"}), \
                mock.patch("engines.relationship_producers.sources.tables_ready",
                           return_value={"producers": True, "relationships": True, "lifecycle": False}), \
                mock.patch("engines.relationship_producers.sources.league_id", return_value=77), \
                mock.patch("engines.relationship_producers.sources.roster_from_snapshot", return_value={1: 3}), \
                mock.patch("engines.relationship_producers.calendar.tick_for", return_value=6), \
                mock.patch("engines.relationship_producers.run.run_tick",
                           return_value={"status": "HELD", "counts": {"held": 4}}) as run_tick:
            res = ws.step_relationship_producers(self.snapshot, 2036, connect=lambda dsn: conn, log_fn=lambda m: None)
        self.assertEqual(res["status"], "HELD")
        args, kw = run_tick.call_args
        self.assertEqual(args[1:], (2036, 6))
        self.assertEqual((kw["source"], kw["league_id"], kw["roster"], kw["roster_season"]), ("SAVE", 77, {1: 3}, 2036))
        conn.commit.assert_called_once()

    def test_skipped_when_not_migrated(self):
        conn = self._conn()
        logs = []
        with mock.patch.dict(os.environ, {ws.PRODUCERS_ENV: "on"}), \
                mock.patch("engines.relationship_producers.sources.tables_ready",
                           return_value={"producers": True, "relationships": False, "lifecycle": False}), \
                mock.patch("engines.relationship_producers.calendar.tick_for", return_value=6), \
                mock.patch("engines.relationship_producers.run.run_tick") as run_tick:
            res = ws.step_relationship_producers(self.snapshot, 2036, connect=lambda dsn: conn, log_fn=logs.append)
        self.assertEqual(res["status"], "skipped")
        run_tick.assert_not_called()
        self.assertTrue(any("not migrated" in l for l in logs))

    def test_an_exception_is_recorded_and_never_raised(self):
        conn = self._conn()
        with mock.patch.dict(os.environ, {ws.PRODUCERS_ENV: "on"}), \
                mock.patch("pipeline.watch_save.send_notify") as notify, \
                mock.patch("engines.relationship_producers.sources.tables_ready",
                           return_value={"producers": True, "relationships": True, "lifecycle": True}), \
                mock.patch("engines.relationship_producers.sources.league_id", return_value=77), \
                mock.patch("engines.relationship_producers.sources.roster_from_snapshot", return_value={}), \
                mock.patch("engines.relationship_producers.calendar.tick_for", return_value=6), \
                mock.patch("engines.relationship_producers.run.run_tick", side_effect=RuntimeError("boom")), \
                mock.patch("engines.relationship_producers.run.record_failure") as failure:
            res = ws.step_relationship_producers(self.snapshot, 2036, connect=lambda dsn: conn, log_fn=lambda m: None)
        self.assertEqual(res, {"status": "failed", "error": "boom"})
        conn.rollback.assert_called()
        self.assertEqual(failure.call_args[0][1:5], (77, 2036, 6, "SAVE"))
        notify.assert_called_once()

    def _ingest(self, **kw):
        order = []
        mock.patch("pipeline.load_save.load", side_effect=lambda *a, **k: order.append("load") or {}).start()
        mock.patch("pipeline.watch_save.step_relationship_producers",
                   side_effect=lambda *a, **k: order.append("producers") or {"status": "off"}).start()
        mock.patch("pipeline.watch_save.run_weekly_engines", side_effect=lambda *a, **k: order.append("engines")).start()
        mock.patch("pipeline.watch_save.queue_nightly_jobs").start()
        mock.patch("pipeline.watch_save.prepare_next_week_mod_pack").start()
        mock.patch("pipeline.watch_save.send_notify").start()
        mock.patch("pipeline.watch_save.step_articles", return_value={"status": "published", "week": 6}).start()
        mock.patch("pipeline.watch_save.run_league_office",
                   side_effect=lambda *a, **k: order.append("league_office") or {"status": "written"}).start()
        mock.patch("pipeline.watch_save.report_league_office", return_value="").start()
        res = ws.ingest_snapshot(self.snapshot, 2036, kw.pop("week", 6), save_name="CAREER-A", **kw)
        return order, res

    def test_hook_runs_after_the_load_on_a_weekly_ingest(self):
        order, res = self._ingest()
        self.assertEqual(order[:3], ["load", "producers", "engines"])
        self.assertEqual(res["relationship_producers"], {"status": "off"})

    def test_hook_runs_on_an_offseason_stop(self):
        order, _ = self._ingest(week=None, stage_only=True, league_office=True, save_path=self.snapshot)
        self.assertEqual(order, ["load", "producers", "league_office"])

    def test_a_failing_step_never_stops_the_ingest(self):
        mock.patch("engines.relationship_producers.run.run_tick", side_effect=RuntimeError("boom")).start()
        with mock.patch.dict(os.environ, {ws.PRODUCERS_ENV: "on"}):
            mock.patch("pipeline.load_save.load", return_value={}).start()
            mock.patch("pipeline.watch_save.run_weekly_engines").start()
            mock.patch("pipeline.watch_save.queue_nightly_jobs").start()
            mock.patch("pipeline.watch_save.prepare_next_week_mod_pack").start()
            mock.patch("pipeline.watch_save.send_notify").start()
            mock.patch("pipeline.watch_save.step_articles", return_value={"status": "published", "week": 6}).start()
            with mock.patch("psycopg.connect", side_effect=RuntimeError("no database")):
                res = ws.ingest_snapshot(self.snapshot, 2036, 6, save_name="CAREER-A")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["relationship_producers"]["status"], "failed")


class RetirementWindowNoticeTest(unittest.TestCase):
    """retirement_window_notice (RETIREMENT_WINDOW_PLAN §2.5): the line and its priority rule."""

    def test_line_and_priority(self):
        base = {"role": "window", "stop": "FREE_AGENCY:1", "restored": [{"pgid": 1}], "conflicts": [],
                "vanished": [], "blocked": []}
        line, prio = ws.retirement_window_notice({"retirement_window": base})
        self.assertEqual((line, prio), ("Retirement window FREE_AGENCY:1: 1 restored.", "default"))
        line, prio = ws.retirement_window_notice({"retirement_window": dict(base, conflicts=[{"pgid": 3}])})
        self.assertEqual(prio, "high")
        self.assertIn("1 conflict(s)", line)
        line, prio = ws.retirement_window_notice({"retirement_window": {"role": "decision", "season": 2026,
                                                                        "decided": {"1": {"keep": True}}}})
        self.assertEqual(prio, "default")
        self.assertIn("decided 1 retirement(s) for 2026 (1 kept)", line)

    def test_no_line(self):
        self.assertIsNone(ws.retirement_window_notice(None))
        self.assertIsNone(ws.retirement_window_notice({"status": "failed"}))
        self.assertIsNone(ws.retirement_window_notice({"retirement_window": {"role": "image", "roster_image": {}}}))


def test_a_tuning_clone_is_refused_before_routing(tmp_path, monkeypatch):
    """B10 (chunk 9): routing writes the clone's name into the live franchise's registry before
    check_tuning_destination refuses (and --reseed would drop the live database). The refusal comes first now."""
    called = []
    monkeypatch.setattr(ws, "route_franchise", lambda *a, **k: called.append(a) or {"status": "new"})
    save = tmp_path / "CAREER-TUNE-X"
    save.write_bytes(b"x")
    monkeypatch.setattr(ws, "snapshot_save", lambda *a, **k: str(tmp_path / "save.json"))
    monkeypatch.setattr(ws, "current_week", lambda snapshot: 1)
    monkeypatch.setattr(ws, "franchise_id", lambda snapshot, path: "1")
    (tmp_path / "save.json").write_text('{"season": {"calendar_year": 2027}, "franchise": {"league_id": 1}}')
    with pytest.raises(ws.WatcherError):
        ws.process_save(str(save), run_folder=str(tmp_path / "run"), log_fn=lambda m: None)
    assert called == []                                       # routing never ran


class LockTuningDraftOrderWiringTest(unittest.TestCase):
    """P2-03: a tuning ingest at the DRAFT stop locks the coming draft's order first, because a
    tuning league has nobody to press the Draft Room's button and the league office has no picks
    to write without it. (Renamed from LockTuningDraftOrderTest, which a later class of the same name
    shadowed, so these four wiring tests were never collected.)"""

    def setUp(self):
        self.log = []

    def _connect(self, *fetchone_rows):
        import unittest.mock as m
        connect = m.patch("psycopg.connect").start()
        lock = m.patch("engines.draft_room.lock_order").start()
        self.addCleanup(m.patch.stopall)
        cur = m.MagicMock()
        cur.execute.return_value = cur
        cur.fetchone.side_effect = list(fetchone_rows)
        connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
        return connect, lock

    def test_it_locks_the_coming_draft_in_a_tuning_database(self):
        _, lock = self._connect((2026,), (0, 224), (672,))
        n = ws.lock_tuning_draft_order("dbname=madden_tune_a", "madden_tune_a", log_fn=self.log.append)
        self.assertEqual(n, 672)
        lock.assert_called_once()
        self.assertEqual(lock.call_args[0][1], 2027)          # the season after league_state's
        self.assertTrue(any("Locked the 2027 draft order" in ln for ln in self.log))

    def test_it_does_nothing_outside_a_tuning_database(self):
        connect, lock = self._connect()
        self.assertEqual(ws.lock_tuning_draft_order("dbname=madden_mu2026", "madden_mu2026",
                                                    log_fn=self.log.append), 0)
        connect.assert_not_called()
        lock.assert_not_called()

    def test_an_already_locked_order_is_left_alone(self):
        _, lock = self._connect((2026,), (224, 224))
        self.assertEqual(ws.lock_tuning_draft_order("dbname=madden_tune_a", "madden_tune_a",
                                                    log_fn=self.log.append), 0)
        lock.assert_not_called()

    def test_a_failure_is_a_warning_not_an_error(self):
        import unittest.mock as m
        m.patch("psycopg.connect", side_effect=RuntimeError("no db")).start()
        m.patch("engines.draft_room.lock_order").start()
        self.addCleanup(m.patch.stopall)
        self.assertEqual(ws.lock_tuning_draft_order("dbname=madden_tune_a", "madden_tune_a",
                                                    log_fn=self.log.append), 0)
        self.assertTrue(any("could not lock" in ln for ln in self.log))


class BootstrapTuningProgressionTest(unittest.TestCase):
    """A tuning database is seeded and loaded but never set up, so the league office refuses at the
    first progression stage until the neutral bootstrap that `league_office setup` gives a live
    franchise is applied (madden_tune_p2f, 2026-11-10)."""

    def setUp(self):
        self.log = []
        self.snapshot = {"players": [{"pgid": 1, "confidence": 50}, {"pgid": 2, "confidence": 50}]}

    def test_a_neutral_tuning_save_is_bootstrapped_for_its_season(self):
        import unittest.mock as m
        connect = m.patch("psycopg.connect").start()
        boot = m.patch("engines.progression_checkpoint.bootstrap_pass", return_value=["a", "b"]).start()
        self.addCleanup(m.patch.stopall)
        connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = m.MagicMock()
        n = ws.bootstrap_tuning_progression("dbname=madden_tune_a", "madden_tune_a", self.snapshot, 2026,
                                            source_fingerprint="sha", log_fn=self.log.append)
        self.assertEqual(n, 2)
        self.assertEqual(boot.call_args[0][2], 2026)
        self.assertTrue(boot.call_args[1]["form_neutral"])
        self.assertEqual(boot.call_args[1]["source_fingerprint"], "sha")
        self.assertTrue(any("neutral bootstrap for 2 players" in ln for ln in self.log))

    def test_a_save_that_is_not_neutral_is_left_alone(self):
        import unittest.mock as m
        connect = m.patch("psycopg.connect").start()
        boot = m.patch("engines.progression_checkpoint.bootstrap_pass").start()
        self.addCleanup(m.patch.stopall)
        save = {"players": [{"pgid": 1, "confidence": 61}]}
        self.assertEqual(ws.bootstrap_tuning_progression("dbname=madden_tune_a", "madden_tune_a", save, 2026,
                                                         log_fn=self.log.append), 0)
        boot.assert_not_called()
        connect.assert_not_called()
        self.assertTrue(any("not neutral" in ln for ln in self.log))

    def test_nothing_happens_outside_a_tuning_database(self):
        import unittest.mock as m
        boot = m.patch("engines.progression_checkpoint.bootstrap_pass").start()
        self.addCleanup(m.patch.stopall)
        self.assertEqual(ws.bootstrap_tuning_progression("dbname=madden_mu2026", "madden_mu2026", self.snapshot, 2026,
                                                         log_fn=self.log.append), 0)
        boot.assert_not_called()


class BootstrapNewPlayersTest(unittest.TestCase):
    """A player Mu has never tracked (no state, no event) gets neutral progression state at an ingest (JR,
    2026-11-10): the live league's first rookie class and madden_tune_p2g's 389 would otherwise fail every
    progression plan with "player N: explicit neutral bootstrap required"."""

    def setUp(self):
        self.log = []
        self.draft = {"players": [{"pgid": 999, "ratings": {"Speed": 80}, "overall": 65, "age": 23,
                                   "dev_trait": "College_Impact", "years_pro": 0, "confidence": 55}],
                      "season": {"calendar_year": 2026, "stage": "offseason", "offseason_stage": 9,
                                 "week_index": 11}}

    def run_it(self, state_rows=1, found=None):
        import unittest.mock as m
        connect = m.patch("psycopg.connect").start()
        cur = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [(True,), (state_rows,), (found or [(5, 999)],)]
        boot = m.patch("engines.progression_checkpoint.bootstrap_untracked",
                       return_value=([{"player_id": 5, "application_season": 2027}], [])).start()
        self.addCleanup(m.patch.stopall)
        n = ws.bootstrap_new_players("dbname=madden_car", self.draft, {},
                                     source_fingerprint="sha", log_fn=self.log.append)
        return n, boot, connect

    def test_an_untracked_player_is_bootstrapped_at_the_league_year_he_joins(self):
        n, boot, _ = self.run_it()
        self.assertEqual(n, 1)
        self.assertEqual(boot.call_args[0][2], 2027)          # the DRAFT stop in 2026 is the 2027 league year
        self.assertEqual(boot.call_args[1]["source_fingerprint"], "sha")
        self.assertTrue(any("never-tracked players" in ln and "2027" in ln for ln in self.log))

    def test_the_seeding_load_keeps_its_own_neutrality_gate(self):
        n, boot, _ = self.run_it(state_rows=0)
        self.assertEqual(n, 0)
        boot.assert_not_called()

    def test_an_unrecognised_stage_defers_to_the_next_ingest(self):
        import unittest.mock as m
        connect = m.patch("psycopg.connect").start()
        cur = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [(True,), (1,)]
        boot = m.patch("engines.progression_checkpoint.bootstrap_untracked").start()
        self.addCleanup(m.patch.stopall)
        self.assertEqual(ws.bootstrap_new_players("dbname=madden_car", {"players": [{"pgid": 1}], "season": {}},
                                                  log_fn=self.log.append), 0)
        boot.assert_not_called()
        self.assertTrue(any("deferred to the next ingest" in ln for ln in self.log))

    def test_nothing_happens_without_players_or_a_dsn(self):
        import unittest.mock as m
        boot = m.patch("engines.progression_checkpoint.bootstrap_untracked").start()
        connect = m.patch("psycopg.connect").start()
        self.addCleanup(m.patch.stopall)
        self.assertEqual(ws.bootstrap_new_players("dbname=madden_car", {"players": []}, {},
                                                  log_fn=self.log.append), 0)   # no players in the snapshot
        self.assertEqual(ws.bootstrap_new_players(None, self.draft, log_fn=self.log.append), 0)
        boot.assert_not_called()
        connect.assert_not_called()

    def test_a_database_without_managed_progression_is_left_alone(self):
        import unittest.mock as m
        connect = m.patch("psycopg.connect").start()
        cur = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = (None,)
        boot = m.patch("engines.progression_checkpoint.bootstrap_untracked").start()
        self.addCleanup(m.patch.stopall)
        self.assertEqual(ws.bootstrap_new_players("dbname=madden_car", self.draft, {},
                                                  log_fn=self.log.append), 0)
        boot.assert_not_called()


@pytest.mark.integration
class BootstrapNewPlayersRealDbTest(unittest.TestCase):
    """F2: the hook's own wiring (its DB checks, its stage -> season decision and its commit) on real Postgres.

    Runs against an explicitly named scratch database only; a database with the full schema is needed
    (`MU_PROGRESSION_STATE_TEST_DSN`, the same variable the progression integration tests use)."""

    def setUp(self):
        import psycopg
        dsn = os.environ.get("MU_PROGRESSION_STATE_TEST_DSN")
        if not dsn:
            self.skipTest("explicit scratch DSN not supplied")
        self.dsn = dsn
        self.conn = psycopg.connect(dsn)
        name = self.conn.execute("SELECT current_database()").fetchone()[0]
        if "test" not in name or name == "madden_2036":
            self.skipTest("explicitly named scratch database required")
        self.log = []

    def tearDown(self):
        if getattr(self, "conn", None) is not None:
            self.conn.rollback()
            self.conn.close()

    def _state(self, player_id):
        from engines import progression_state as ps
        return ps.load_state(self.conn.cursor(), player_id)

    def test_a_stranded_player_is_seeded_through_the_hook(self):
        cur = self.conn.cursor()
        if not cur.execute("SELECT to_regclass('public.progression_player_state') IS NOT NULL "
                           "AND to_regclass('public.players') IS NOT NULL").fetchone()[0]:
            self.skipTest("the scratch database lacks the progression or players schema")
        if not cur.execute("SELECT count(*) FROM progression_player_state").fetchone()[0]:
            self.skipTest("the scratch database has no progression state (the seeding gate would skip)")
        # The hook opens its own connection, so the player row must be committed before the call.
        cur.execute("DELETE FROM players WHERE madden_pgid = 990001")
        cur.execute("INSERT INTO players (player_name, full_name, origin, madden_pgid) "
                    "VALUES ('N','Newcomer','SAVE',990001) RETURNING id")
        pid = cur.fetchone()[0]
        self.conn.commit()
        snapshot = {"players": [{"pgid": 990001, "ratings": {"Speed": 85, "Awareness": 72}, "overall": 66,
                                 "age": 23, "dev_trait": "College_Impact", "years_pro": 0, "confidence": 57}],
                    "season": {"calendar_year": 2026, "stage": "offseason", "offseason_stage": 9,
                               "week_index": 11}}
        try:
            n = ws.bootstrap_new_players(self.dsn, snapshot, {}, source_fingerprint="sha-hook",
                                         log_fn=self.log.append)
            self.assertEqual(n, 1)
            state = self._state(pid)
            self.assertEqual((state["application_season"], state["base_ratings"]),
                             (2027, {"Speed": 85, "Awareness": 72}))
            self.assertEqual(ws.bootstrap_new_players(self.dsn, snapshot, {}, source_fingerprint="sha-hook",
                                                      log_fn=self.log.append), 0)
        finally:
            self.conn.rollback()
            cur.execute("DELETE FROM players WHERE madden_pgid = 990001")
            self.conn.commit()



class LockTuningDraftOrderTest(unittest.TestCase):
    """A tuning draft's order must be locked even when its compensatory picks are already numbered.

    `compensatory_picks_run` inserts the comp picks numbered at the free-agency waves, before the draft.
    Counting any numbered row read madden_tune_p2g's 2027 class as "already locked" (10 comps numbered,
    224 standard rows NULL), so the league office signed only the 10 comps (2026-11-10, M-0272). The
    guard skips only when every standard row of the class is numbered (M-0314 F1)."""

    def fake_connection(self, season, numbered, total, after=None):
        cur = mock.MagicMock()
        cur.execute.return_value = cur
        cur.fetchone.side_effect = [(season,), (numbered, total), (total if after is None else after,)]
        connect = mock.patch("psycopg.connect").start()
        connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
        self.addCleanup(mock.patch.stopall)
        return cur

    def test_a_fully_comped_class_is_not_already_locked(self):
        cur = self.fake_connection(2026, 0, 224, after=234)   # 0 standard picks numbered, 10 comps only
        lock = mock.patch("engines.draft_room.lock_order").start()
        n = ws.lock_tuning_draft_order("host=localhost dbname=madden_tune_x user=postgres", "madden_tune_x",
                                       log_fn=lambda *_: None)
        lock.assert_called_once()
        self.assertEqual(n, 234)
        guard = str(cur.execute.call_args_list[1][0][0])
        self.assertIn("pick_suffix", guard, "the lock guard must count standard picks, not comps")

    def test_a_partially_numbered_class_is_still_locked(self):
        self.fake_connection(2026, 1, 2)                  # one numbered standard row of two is not a lock
        lock = mock.patch("engines.draft_room.lock_order").start()
        ws.lock_tuning_draft_order("host=localhost dbname=madden_tune_x user=postgres", "madden_tune_x",
                                   log_fn=lambda *_: None)
        lock.assert_called_once()

    def test_an_already_locked_class_is_left_alone(self):
        self.fake_connection(2026, 224, 224)
        lock = mock.patch("engines.draft_room.lock_order").start()
        self.assertEqual(ws.lock_tuning_draft_order("host=localhost dbname=madden_tune_x user=postgres",
                                                    "madden_tune_x", log_fn=lambda *_: None), 0)
        lock.assert_not_called()


@pytest.mark.integration
class StandardPicksLockedRealDbTest(unittest.TestCase):
    """The guard's SQL on real Postgres: numbered comp rows never count as a locked order."""

    def setUp(self):
        import psycopg
        dsn = os.environ.get("MU_PROGRESSION_STATE_TEST_DSN")
        if not dsn:
            self.skipTest("explicit scratch DSN not supplied")
        self.dsn = dsn
        self.conn = psycopg.connect(dsn)
        self.cur = self.conn.cursor()
        if not self.cur.execute("SELECT to_regclass('public.draft_picks') IS NOT NULL "
                                "AND to_regclass('public.teams') IS NOT NULL").fetchone()[0]:
            self.skipTest("the scratch database lacks draft_picks or teams")
        self.cur.execute("INSERT INTO teams (id, name, abbreviation) VALUES "
                         "(901, 'Alpha', 'AAA'), (902, 'Beta', 'BBB') ON CONFLICT DO NOTHING")

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def add(self, round_no, team, overall, source, suffix):
        self.cur.execute("INSERT INTO draft_picks (draft_season, round, original_team_id, owner_team_id, "
                         "overall_pick, source, pick_suffix) VALUES (2099, %s, %s, %s, %s, %s, %s) "
                         "ON CONFLICT DO NOTHING", (round_no, team, team, overall, source, suffix))

    def test_comps_alone_do_not_read_as_locked(self):
        self.add(1, 901, None, "SEED", "")
        self.add(1, 902, None, "SEED", "")
        self.add(5, 901, 161, "COMP", "01")
        self.assertEqual(ws.standard_picks_locked(self.cur, 2099), (0, 2))   # (numbered, total) standard rows
        self.cur.execute("UPDATE draft_picks SET overall_pick = 1 WHERE draft_season = 2099 AND pick_suffix = ''")
        self.assertEqual(ws.standard_picks_locked(self.cur, 2099), (2, 2))


@pytest.mark.integration
class LockTuningDraftOrderRealDbTest(unittest.TestCase):
    """M-0314 F1/F2: the lock guard on real Postgres, by what it does rather than by the SQL it contains.

    A class with two NULL standard rows and one numbered comp must still be locked, and so must a class whose
    standard rows are only partly numbered (the F1 bug); only a class whose every standard row is numbered is
    left alone. On the parent the partial step fails with "already locked (1 picks)"."""

    SEASON, DRAFT = 2199, 2200

    def setUp(self):
        import psycopg
        dsn = os.environ.get("MU_PROGRESSION_STATE_TEST_DSN")
        if not dsn:
            self.skipTest("explicit scratch DSN not supplied")
        self.dsn = dsn
        self.conn = psycopg.connect(dsn)
        name = self.conn.execute("SELECT current_database()").fetchone()[0]
        if "test" not in name or name == "madden_2036":
            self.skipTest("explicitly named scratch database required")
        self.cur = self.conn.cursor()
        if not self.cur.execute("SELECT to_regclass('public.draft_picks') IS NOT NULL "
                                "AND to_regclass('public.teams') IS NOT NULL "
                                "AND to_regclass('public.league_state') IS NOT NULL").fetchone()[0]:
            self.skipTest("the scratch database lacks draft_picks, teams or league_state")
        self.previous = self.cur.execute("SELECT current_season FROM league_state WHERE id = 1").fetchone()
        self.cur.execute("INSERT INTO teams (id, name, abbreviation) VALUES "
                         "(901, 'Alpha', 'AAA'), (902, 'Beta', 'BBB') ON CONFLICT DO NOTHING")
        self.cur.execute("DELETE FROM draft_picks WHERE draft_season = %s", (self.DRAFT,))
        self.cur.execute("INSERT INTO draft_picks (draft_season, round, original_team_id, owner_team_id, "
                         "overall_pick, source, pick_suffix) VALUES "
                         "(%s, 1, 901, 901, NULL, 'SEED', ''), (%s, 1, 902, 902, NULL, 'SEED', ''), "
                         "(%s, 5, 901, 901, 161, 'COMP', '01')", (self.DRAFT, self.DRAFT, self.DRAFT))
        self.cur.execute("INSERT INTO league_state (id, current_season) VALUES (1, %s) "
                         "ON CONFLICT (id) DO UPDATE SET current_season = EXCLUDED.current_season", (self.SEASON,))
        self.conn.commit()

    def tearDown(self):
        try:
            self.cur.execute("DELETE FROM draft_picks WHERE draft_season = %s", (self.DRAFT,))
            if self.previous:
                self.cur.execute("UPDATE league_state SET current_season = %s WHERE id = 1", (self.previous[0],))
            else:
                self.cur.execute("DELETE FROM league_state WHERE id = 1")
            self.conn.commit()
        finally:
            self.conn.close()

    def test_a_partially_numbered_class_is_locked_and_a_full_one_is_not(self):
        # Comps numbered and every standard row NULL: not a lock (M-0272's case)
        with mock.patch("engines.draft_room.lock_order") as lock:
            n = ws.lock_tuning_draft_order(self.dsn, "madden_tune_scratch", log_fn=lambda *_: None)
        lock.assert_called_once()
        self.assertEqual(lock.call_args[0][1], self.DRAFT)
        self.assertEqual(n, 1)                        # the numbered comp is the only numbered pick (lock mocked)

        # One numbered standard row of two: still not a lock (M-0314 F1)
        self.cur.execute("UPDATE draft_picks SET overall_pick = 1 WHERE draft_season = %s "
                         "AND round = 1 AND original_team_id = 901", (self.DRAFT,))
        self.conn.commit()                            # the guard opens its own connection
        with mock.patch("engines.draft_room.lock_order") as lock:
            n = ws.lock_tuning_draft_order(self.dsn, "madden_tune_scratch", log_fn=lambda *_: None)
        lock.assert_called_once()
        self.assertEqual(n, 2)                        # the numbered standard row and the comp

        # Every standard row numbered: locked, left alone
        self.cur.execute("UPDATE draft_picks SET overall_pick = 1 "
                         "WHERE draft_season = %s AND COALESCE(pick_suffix, '') = ''", (self.DRAFT,))
        self.conn.commit()
        with mock.patch("engines.draft_room.lock_order") as lock:
            self.assertEqual(ws.lock_tuning_draft_order(self.dsn, "madden_tune_scratch",
                                                        log_fn=lambda *_: None), 0)
        lock.assert_not_called()


@pytest.mark.integration
class BootstrapUntrackedRetiredRealDbTest(unittest.TestCase):
    """M-0314 F4: a player the database records as retired (`players.retired_season`) is not a live
    newcomer, so `bootstrap_untracked` leaves him alone even when the save still lists him."""

    def setUp(self):
        import psycopg
        dsn = os.environ.get("MU_PROGRESSION_STATE_TEST_DSN")
        if not dsn:
            self.skipTest("explicit scratch DSN not supplied")
        self.conn = psycopg.connect(dsn)
        name = self.conn.execute("SELECT current_database()").fetchone()[0]
        if "test" not in name or name == "madden_2036":
            self.skipTest("explicitly named scratch database required")
        self.cur = self.conn.cursor()
        if not self.cur.execute("SELECT to_regclass('public.players') IS NOT NULL "
                                "AND to_regclass('public.progression_player_state') IS NOT NULL "
                                "AND to_regclass('public.progression_events') IS NOT NULL").fetchone()[0]:
            self.skipTest("the scratch database lacks the players or progression schema")
        self._clean()
        self.cur.execute("INSERT INTO players (player_name, full_name, origin, madden_pgid, retired_season) "
                         "VALUES ('R','Retired','SAVE',990011,2026), ('L','Live','SAVE',990012,NULL) RETURNING id")
        self.retired_id, self.live_id = [r[0] for r in self.cur.fetchall()]

    def _clean(self):
        self.cur.execute("DELETE FROM progression_player_state WHERE player_id IN "
                         "(SELECT id FROM players WHERE madden_pgid IN (990011, 990012))")
        self.cur.execute("DELETE FROM progression_events WHERE player_id IN "
                         "(SELECT id FROM players WHERE madden_pgid IN (990011, 990012))")
        self.cur.execute("DELETE FROM players WHERE madden_pgid IN (990011, 990012)")

    def tearDown(self):
        try:
            self.conn.rollback()
            self._clean()
            self.conn.commit()
        finally:
            self.conn.close()

    def test_a_retired_player_is_not_seeded_as_a_newcomer(self):
        from engines import progression_checkpoint as pc
        snapshot = {"players": [
            {"pgid": 990011, "ratings": {"Speed": 70, "Awareness": 75}, "overall": 72, "age": 33,
             "dev_trait": "Normal", "years_pro": 12},
            {"pgid": 990012, "ratings": {"Speed": 88, "Awareness": 68}, "overall": 66, "age": 23,
             "dev_trait": "College_Impact", "years_pro": 0}]}
        rows, no_ratings = pc.bootstrap_untracked(self.cur, snapshot, 2027, source_fingerprint="sha-retired")
        self.assertEqual([r["player_id"] for r in rows], [self.live_id])
        self.assertEqual(no_ratings, [])
