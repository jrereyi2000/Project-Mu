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

from pipeline import watch_save as ws


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
        self.snapshots = {}

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

