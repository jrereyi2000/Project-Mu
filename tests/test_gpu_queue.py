"""
tests/test_gpu_queue.py
Unit tests for the Nightly GPU Queue Scheduler (pipeline/gpu_queue/).

Verifies:
- Data models, serialization, and priority inference.
- Job persistence for both SQLiteJobStore and JsonJobStore.
- Tier prioritization (Tier 1 text > Tier 2 headshots > Tier 3 audio).
- Model batching (jobs sharing an active model execute without reload).
- Madden process checking, pause when active, and resume when process exits.
- Idle timeout handling.
- Error handling and queue resilience.
- Standalone CLI commands (enqueue, list, run, clear, check-madden).
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import pytest

from pipeline.gpu_queue import (
    GPUJob,
    GPUQueue,
    GPUQueueRunner,
    SQLiteJobStore,
    JsonJobStore,
    enqueue,
    mock_job_handler,
    run_queue,
    TIER_1_TEXT,
    TIER_2_IMAGE,
    TIER_3_AUDIO,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    default_priority_for_kind,
    is_madden_running,
    is_process_running,
    set_process_check_mock,
    mock_process_running,
)
from pipeline.gpu_queue.cli import main as cli_main


class GPUJobModelTest(unittest.TestCase):
    """Test GPUJob model creation, serialization, and tier inference."""

    def test_default_priority_inference(self):
        # Tier 1: text
        job_text = GPUJob(kind="text", model_id="gemma4:12b")
        self.assertEqual(job_text.priority, TIER_1_TEXT)

        job_wire = GPUJob(kind="agent_wire", model_id="gemma4:12b")
        self.assertEqual(job_wire.priority, TIER_1_TEXT)

        job_scout = GPUJob(kind="scouting_notes", model_id="gemma4:12b")
        self.assertEqual(job_scout.priority, TIER_1_TEXT)

        # Tier 2: image/headshots/portraits
        job_headshot = GPUJob(kind="headshot", model_id="sdxl")
        self.assertEqual(job_headshot.priority, TIER_2_IMAGE)

        job_portrait = GPUJob(kind="portrait_conversion", model_id="converter")
        self.assertEqual(job_portrait.priority, TIER_2_IMAGE)

        # Tier 3: audio/tts
        job_audio = GPUJob(kind="audio_tts", model_id="chatter")
        self.assertEqual(job_audio.priority, TIER_3_AUDIO)

    def test_explicit_priority_override(self):
        # Override headshot to priority 1 (e.g. urgent re-render)
        job = GPUJob(kind="headshot", model_id="sdxl", priority=1)
        self.assertEqual(job.priority, 1)

    def test_dict_serialization_roundtrip(self):
        job = GPUJob(
            id="job_test_123",
            kind="text",
            model_id="gemma4:12b",
            payload={"prompt": "Write article", "temperature": 0.7},
            priority=TIER_1_TEXT,
        )
        d = job.to_dict()
        self.assertEqual(d["id"], "job_test_123")
        self.assertEqual(d["payload"]["prompt"], "Write article")

        restored = GPUJob.from_dict(d)
        self.assertEqual(restored.id, job.id)
        self.assertEqual(restored.kind, job.kind)
        self.assertEqual(restored.model_id, job.model_id)
        self.assertEqual(restored.payload, job.payload)
        self.assertEqual(restored.priority, job.priority)
        self.assertEqual(restored.status, STATUS_PENDING)


class JobStorePersistenceTest(unittest.TestCase):
    """Test offline job storage persistence in SQLite and JSON backends."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.sqlite_path = os.path.join(self.tmp_dir, "test_queue.db")
        self.json_path = os.path.join(self.tmp_dir, "test_queue.json")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _test_store_crud(self, store):
        # 1. Add jobs
        job1 = GPUJob(id="job_1", kind="text", model_id="m1", payload={"task": "a"})
        job2 = GPUJob(id="job_2", kind="headshot", model_id="m2", payload={"task": "b"})
        store.add(job1)
        store.add(job2)

        # 2. Get job
        j1 = store.get("job_1")
        self.assertIsNotNone(j1)
        self.assertEqual(j1.payload, {"task": "a"})

        # 3. List jobs
        jobs = store.list()
        self.assertEqual(len(jobs), 2)

        # 4. Update job
        j1.status = STATUS_RUNNING
        j1.started_at = "2026-10-30T00:00:00Z"
        store.update(j1)
        updated_j1 = store.get("job_1")
        self.assertEqual(updated_j1.status, STATUS_RUNNING)

        # List by status
        running_jobs = store.list(status=STATUS_RUNNING)
        self.assertEqual(len(running_jobs), 1)
        self.assertEqual(running_jobs[0].id, "job_1")

        # 5. Delete job
        self.assertTrue(store.delete("job_1"))
        self.assertIsNone(store.get("job_1"))
        self.assertEqual(len(store.list()), 1)

        # 6. Clear jobs
        store.clear()
        self.assertEqual(len(store.list()), 0)

    def test_sqlite_store(self):
        store = SQLiteJobStore(db_path=self.sqlite_path)
        self._test_store_crud(store)

        # Verify cross-instance persistence with the same SQLite file
        job = GPUJob(id="job_persist", kind="text", model_id="m1", payload={"p": 1})
        store.add(job)

        store2 = SQLiteJobStore(db_path=self.sqlite_path)
        loaded = store2.get("job_persist")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.payload, {"p": 1})

    def test_sqlite_in_memory_store(self):
        store = SQLiteJobStore(db_path=":memory:")
        self._test_store_crud(store)

    def test_json_store(self):
        store = JsonJobStore(json_path=self.json_path)
        self._test_store_crud(store)

        # Verify cross-instance persistence with the same JSON file
        job = GPUJob(id="job_persist_json", kind="text", model_id="m1", payload={"p": 2})
        store.add(job)

        store2 = JsonJobStore(json_path=self.json_path)
        loaded = store2.get("job_persist_json")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.payload, {"p": 2})


class TierPrioritizationTest(unittest.TestCase):
    """
    Test §7.8 priority order:
    Tier 1 text > Tier 2 headshots > Tier 3 audio.
    """

    def setUp(self):
        self.queue = GPUQueue(store=SQLiteJobStore(db_path=":memory:"))

    def test_tier_ordering_regardless_of_enqueue_order(self):
        # Enqueue in reverse order: Tier 3 first, then Tier 2, then Tier 1
        t3_job = GPUJob(id="audio_job", kind="audio_tts", model_id="tts_model")
        t2_job = GPUJob(id="image_job", kind="headshot", model_id="sdxl")
        t1_job = GPUJob(id="text_job", kind="text", model_id="gemma4")

        self.queue.enqueue(t3_job)
        self.queue.enqueue(t2_job)
        self.queue.enqueue(t1_job)

        # First job retrieved must be Tier 1 (text)
        j1 = self.queue.get_next_job()
        self.assertIsNotNone(j1)
        self.assertEqual(j1.id, "text_job")
        self.assertEqual(j1.priority, TIER_1_TEXT)

        # Second job retrieved must be Tier 2 (image)
        j2 = self.queue.get_next_job()
        self.assertIsNotNone(j2)
        self.assertEqual(j2.id, "image_job")
        self.assertEqual(j2.priority, TIER_2_IMAGE)

        # Third job retrieved must be Tier 3 (audio)
        j3 = self.queue.get_next_job()
        self.assertIsNotNone(j3)
        self.assertEqual(j3.id, "audio_job")
        self.assertEqual(j3.priority, TIER_3_AUDIO)

        # No more pending jobs
        self.assertIsNone(self.queue.get_next_job())

    def test_fifo_within_same_priority_tier(self):
        # Two jobs in Tier 1 with different creation times
        job_early = GPUJob(
            id="text_early",
            kind="text",
            model_id="gemma4",
            created_at="2026-10-30T01:00:00Z",
        )
        job_later = GPUJob(
            id="text_later",
            kind="text",
            model_id="gemma4",
            created_at="2026-10-30T02:00:00Z",
        )

        # Enqueue later one first
        self.queue.enqueue(job_later)
        self.queue.enqueue(job_early)

        # Oldest by created_at runs first
        j1 = self.queue.get_next_job()
        self.assertEqual(j1.id, "text_early")
        j2 = self.queue.get_next_job()
        self.assertEqual(j2.id, "text_later")


class ModelBatchingTest(unittest.TestCase):
    """
    Test model batching and sequential VRAM management:
    - Jobs sharing active model are batched together within a tier without reloading.
    - Explicit unloads happen before loading a different model.
    - Model is unloaded when queue becomes empty.
    """

    def setUp(self):
        self.queue = GPUQueue(store=SQLiteJobStore(db_path=":memory:"))
        self.load_calls = []
        self.unload_calls = []
        self.executed_jobs = []

        def mock_load(model_id):
            self.load_calls.append(model_id)
            return f"handle_{model_id}"

        def mock_unload(model_id):
            self.unload_calls.append(model_id)

        self.runner = GPUQueueRunner(
            queue=self.queue,
            is_madden_running_fn=lambda: False,
            load_model=mock_load,
            unload_model=mock_unload,
        )
        self.runner.register_handler(
            "text", lambda job: self.executed_jobs.append(job.id) or {"status": "done"}
        )
        self.runner.register_handler(
            "headshot", lambda job: self.executed_jobs.append(job.id) or {"status": "done"}
        )

    def test_model_batching_within_tier(self):
        # Enqueue:
        # 1. Tier 1, Model A
        # 2. Tier 1, Model B
        # 3. Tier 1, Model A
        # 4. Tier 2, Model A
        j1 = GPUJob(id="job1_mA_t1", kind="text", model_id="ModelA", priority=TIER_1_TEXT)
        j2 = GPUJob(id="job2_mB_t1", kind="text", model_id="ModelB", priority=TIER_1_TEXT)
        j3 = GPUJob(id="job3_mA_t1", kind="text", model_id="ModelA", priority=TIER_1_TEXT)
        j4 = GPUJob(id="job4_mA_t2", kind="headshot", model_id="ModelA", priority=TIER_2_IMAGE)

        self.queue.enqueue(j1)
        self.queue.enqueue(j2)
        self.queue.enqueue(j3)
        self.queue.enqueue(j4)

        # Run full queue
        total = self.runner.run_loop(poll_interval=0.01, stop_on_empty=True)
        self.assertEqual(total, 4)

        # Execution order check:
        # Job 1 (Model A, Tier 1) runs first -> loads Model A.
        # Job 3 (Model A, Tier 1) is batched next (shares active Model A without reload!).
        # Job 2 (Model B, Tier 1) runs next after Model A unloads and Model B loads.
        # Job 4 (Model A, Tier 2) runs after all Tier 1 jobs finish.
        self.assertEqual(
            self.executed_jobs,
            ["job1_mA_t1", "job3_mA_t1", "job2_mB_t1", "job4_mA_t2"],
        )

        # Model load / unload sequences
        # Model A loaded once for Job 1 & 3
        # Model A unloaded, Model B loaded for Job 2
        # Model B unloaded, Model A loaded for Job 4
        # Model A unloaded at queue completion
        self.assertEqual(self.load_calls, ["ModelA", "ModelB", "ModelA"])
        self.assertEqual(self.unload_calls, ["ModelA", "ModelB", "ModelA"])
        self.assertIsNone(self.runner.active_model)


class ProcessCheckAndPauseResumeTest(unittest.TestCase):
    """
    Test Madden 27 process detection, GPU pause during active game play,
    VRAM eviction, and resumption when the game exits.
    """

    def setUp(self):
        self.queue = GPUQueue(store=SQLiteJobStore(db_path=":memory:"))
        self.madden_active = False
        self.unload_calls = []

        def mock_is_madden():
            return self.madden_active

        def mock_unload(model_id):
            self.unload_calls.append(model_id)

        self.runner = GPUQueueRunner(
            queue=self.queue,
            is_madden_running_fn=mock_is_madden,
            unload_model=mock_unload,
            default_handler=mock_job_handler,
        )

    def test_process_checker_mock_hook(self):
        # Test global mock context manager (nested so the real process list,
        # which may include a running Madden, is never consulted)
        with mock_process_running(False):
            self.assertFalse(is_madden_running())
            with mock_process_running(True):
                self.assertTrue(is_madden_running())
            self.assertFalse(is_madden_running())

    def test_pause_when_madden_running_and_resume_when_exits(self):
        job = GPUJob(id="job_pause_test", kind="text", model_id="gemma4:12b")
        self.queue.enqueue(job)

        # 1. Madden is RUNNING
        self.madden_active = True
        did_run = self.runner.run_once()
        self.assertFalse(did_run)

        # Job is still pending
        saved_job = self.queue.get_job("job_pause_test")
        self.assertEqual(saved_job.status, STATUS_PENDING)

        # 2. Madden EXITS
        self.madden_active = False
        did_run = self.runner.run_once()
        self.assertTrue(did_run)

        # Job is now completed
        saved_job = self.queue.get_job("job_pause_test")
        self.assertEqual(saved_job.status, STATUS_COMPLETED)

    def test_evict_vram_when_madden_starts_running(self):
        # Model is currently loaded
        self.runner.load_active_model("gemma4:12b")
        self.assertEqual(self.runner.active_model, "gemma4:12b")

        # Madden starts
        self.madden_active = True
        did_run = self.runner.run_once()
        self.assertFalse(did_run)

        # Model was evicted immediately
        self.assertIsNone(self.runner.active_model)
        self.assertIn("gemma4:12b", self.unload_calls)

    def test_idle_timeout_override(self):
        job = GPUJob(id="job_idle_test", kind="text", model_id="gemma4")
        self.queue.enqueue(job)

        self.madden_active = True
        self.madden_idle = True
        self.runner.is_madden_idle_fn = lambda: self.madden_idle
        # Set idle timeout to 0.1s
        self.runner.idle_timeout = 0.1

        # First idle report starts the idle clock; still blocked
        did_run = self.runner.run_once()
        self.assertFalse(did_run)

        # Wait past idle timeout
        time.sleep(0.15)

        # Idle for the whole timeout: runner proceeds even though process is present
        did_run = self.runner.run_once()
        self.assertTrue(did_run)
        self.assertEqual(self.queue.get_job("job_idle_test").status, STATUS_COMPLETED)

    def test_idle_timeout_resets_when_player_returns(self):
        self.queue.enqueue(GPUJob(id="job_idle_reset", kind="text", model_id="gemma4"))
        self.madden_active = True
        self.madden_idle = True
        self.runner.is_madden_idle_fn = lambda: self.madden_idle
        self.runner.idle_timeout = 0.1

        self.assertFalse(self.runner.run_once())
        time.sleep(0.15)
        # Player came back before the next check: clock resets, still blocked
        self.madden_idle = False
        self.assertFalse(self.runner.run_once())
        self.madden_idle = True
        self.assertFalse(self.runner.run_once())
        self.assertEqual(self.queue.get_job("job_idle_reset").status, STATUS_PENDING)

    def test_idle_timeout_without_idle_fn_never_runs_during_game(self):
        self.queue.enqueue(GPUJob(id="job_no_idle_fn", kind="text", model_id="gemma4"))
        self.madden_active = True
        self.runner.idle_timeout = 0.05

        self.assertFalse(self.runner.run_once())
        time.sleep(0.1)
        # Time since Madden started is not idleness
        self.assertFalse(self.runner.run_once())
        self.assertEqual(self.queue.get_job("job_no_idle_fn").status, STATUS_PENDING)


class ErrorHandlingTest(unittest.TestCase):
    """Test resilient error handling in the GPU queue runner."""

    def setUp(self):
        self.queue = GPUQueue(store=SQLiteJobStore(db_path=":memory:"))

    def test_handler_exception_marks_job_failed_and_continues(self):
        job_fail = GPUJob(id="job_will_fail", kind="failing_kind", model_id="m1")
        job_ok = GPUJob(id="job_will_succeed", kind="ok_kind", model_id="m1")

        self.queue.enqueue(job_fail)
        self.queue.enqueue(job_ok)

        def failing_handler(job):
            raise RuntimeError("CUDA out of memory simulation")

        def ok_handler(job):
            return {"status": "all_good"}

        runner = GPUQueueRunner(
            queue=self.queue,
            is_madden_running_fn=lambda: False,
        )
        runner.register_handler("failing_kind", failing_handler)
        runner.register_handler("ok_kind", ok_handler)

        processed = runner.run_loop(poll_interval=0.01, stop_on_empty=True)
        self.assertEqual(processed, 2)

        # First job marked failed with error message
        j_fail = self.queue.get_job("job_will_fail")
        self.assertEqual(j_fail.status, STATUS_FAILED)
        self.assertIn("CUDA out of memory simulation", j_fail.error)

        # Second job completed normally
        j_ok = self.queue.get_job("job_will_succeed")
        self.assertEqual(j_ok.status, STATUS_COMPLETED)

    def test_job_without_handler_fails_instead_of_completing(self):
        self.queue.enqueue(GPUJob(id="job_no_handler", kind="audio_tts", model_id="m1"))
        loads = []
        runner = GPUQueueRunner(
            queue=self.queue,
            is_madden_running_fn=lambda: False,
            load_model=lambda m: loads.append(m),
        )

        self.assertTrue(runner.run_once())
        saved = self.queue.get_job("job_no_handler")
        self.assertEqual(saved.status, STATUS_FAILED)
        self.assertIn("No handler registered", saved.error)
        self.assertEqual(loads, [])

    def test_model_load_exception_marks_job_failed(self):
        job = GPUJob(id="job_bad_model", kind="text", model_id="broken_weights")
        self.queue.enqueue(job)

        def broken_loader(model_id):
            raise ValueError(f"Weights for {model_id} corrupt")

        runner = GPUQueueRunner(
            queue=self.queue,
            is_madden_running_fn=lambda: False,
            load_model=broken_loader,
            default_handler=mock_job_handler,
        )

        did_run = runner.run_once()
        self.assertTrue(did_run)
        saved = self.queue.get_job("job_bad_model")
        self.assertEqual(saved.status, STATUS_FAILED)
        self.assertIn("Weights for broken_weights corrupt", saved.error)


class CLITest(unittest.TestCase):
    """Test CLI commands: enqueue, list, run, clear, check-madden."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "cli_test.db")
        self.base_args = ["--store-type", "sqlite", "--db-path", self.db_path]

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_cli_enqueue_and_list(self):
        # 1. Enqueue text job
        ret = cli_main(
            self.base_args
            + [
                "enqueue",
                "--kind",
                "text",
                "--model",
                "gemma4:12b",
                "--payload",
                '{"prompt": "Hello"}',
                "--id",
                "cli_job_1",
            ]
        )
        self.assertEqual(ret, 0)

        # 2. List in JSON
        with patch("sys.stdout") as mock_stdout:
            ret = cli_main(self.base_args + ["list", "--json"])
            self.assertEqual(ret, 0)

        # Verify job is in DB
        store = SQLiteJobStore(self.db_path)
        job = store.get("cli_job_1")
        self.assertIsNotNone(job)
        self.assertEqual(job.kind, "text")
        self.assertEqual(job.priority, TIER_1_TEXT)

    def test_cli_run_and_clear(self):
        # Enqueue job
        cli_main(
            self.base_args
            + [
                "enqueue",
                "--kind",
                "headshot",
                "--model",
                "sdxl",
                "--id",
                "cli_job_2",
            ]
        )

        store = SQLiteJobStore(self.db_path)

        # Without --dry-run the CLI refuses (no real handlers are wired) and
        # leaves the job untouched
        with patch("sys.stderr"):
            ret = cli_main(self.base_args + ["run", "--once", "--ignore-madden"])
        self.assertEqual(ret, 1)
        self.assertEqual(store.get("cli_job_2").status, STATUS_PENDING)

        # Run with --dry-run, --ignore-madden and --once
        ret = cli_main(self.base_args + ["run", "--once", "--ignore-madden", "--dry-run"])
        self.assertEqual(ret, 0)

        job = store.get("cli_job_2")
        self.assertEqual(job.status, STATUS_COMPLETED)

        # Clear completed jobs
        ret = cli_main(self.base_args + ["clear", "--status", "completed"])
        self.assertEqual(ret, 0)
        self.assertEqual(len(store.list()), 0)

    def test_cli_check_madden(self):
        with mock_process_running(False):
            ret = cli_main(["check-madden"])
            self.assertEqual(ret, 0)

        with mock_process_running(True):
            ret = cli_main(["check-madden"])
            self.assertEqual(ret, 0)

    def test_cli_malformed_payload_error(self):
        ret = cli_main(
            self.base_args
            + [
                "enqueue",
                "--payload",
                '{"unclosed_brace": true',
            ]
        )
        self.assertEqual(ret, 1)


class PlatformProcessCheckTest(unittest.TestCase):
    """Test process checking under simulated Windows/WSL2 and Unix environments."""

    @patch("pipeline.gpu_queue.process_check._process_check_mock", None)
    @patch("pipeline.gpu_queue.process_check._find_tasklist_exe")
    @patch("subprocess.run")
    def test_tasklist_detection_windows_wsl2(self, mock_run, mock_find):
        mock_find.return_value = "/mnt/c/Windows/System32/tasklist.exe"
        
        # When Madden is in tasklist output
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='"Madden27.exe","2480","Console","1","4,210,000 K"\n"svchost.exe","1100"',
        )
        self.assertTrue(is_process_running(["Madden27.exe"]))

        # When Madden is NOT in tasklist output
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='"notepad.exe","1000"\n"svchost.exe","1100"',
        )
        self.assertFalse(is_process_running(["Madden27.exe"]))

    @patch("pipeline.gpu_queue.process_check._process_check_mock", None)
    @patch("pipeline.gpu_queue.process_check._find_tasklist_exe", return_value=None)
    @patch("shutil.which")
    @patch("subprocess.run")
    def test_pgrep_detection_unix(self, mock_run, mock_which, mock_find):
        def which_side_effect(cmd):
            if cmd == "pgrep":
                return "/usr/bin/pgrep"
            return None

        mock_which.side_effect = which_side_effect

        # Process found (returncode 0)
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(is_process_running(["Madden27"]))

        # Process not found (returncode 1)
        mock_run.return_value = MagicMock(returncode=1)
        self.assertFalse(is_process_running(["Madden27"]))


class ClaimAndRequeueTest(unittest.TestCase):
    """Atomic job claiming and recovery of jobs left running by a crash."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "claim_test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_two_queues_never_claim_the_same_job(self):
        q1 = GPUQueue(store=SQLiteJobStore(self.db_path))
        q2 = GPUQueue(store=SQLiteJobStore(self.db_path))
        q1.enqueue(GPUJob(id="only_job", kind="text", model_id="m"))

        # q2 already saw the job as pending, but q1 claims it first
        self.assertTrue(q1.store.claim("only_job", "t1"))
        self.assertFalse(q2.store.claim("only_job", "t2"))
        self.assertIsNone(q2.get_next_job())

    def test_requeue_running_returns_stale_jobs_to_pending(self):
        q = GPUQueue(store=SQLiteJobStore(self.db_path))
        q.enqueue(GPUJob(id="crashed_job", kind="text", model_id="m"))
        self.assertEqual(q.get_next_job().id, "crashed_job")

        self.assertEqual(q.requeue_running(), 1)
        saved = q.get_job("crashed_job")
        self.assertEqual(saved.status, STATUS_PENDING)
        self.assertIsNone(saved.started_at)

    def test_json_store_claim(self):
        store = JsonJobStore(os.path.join(self.tmp_dir, "q.json"))
        store.add(GPUJob(id="j", kind="text", model_id="m"))
        self.assertTrue(store.claim("j", "t"))
        self.assertFalse(store.claim("j", "t"))
        self.assertEqual(store.get("j").status, STATUS_RUNNING)


class TopLevelHelpersTest(unittest.TestCase):
    """Test top-level helper functions enqueue() and run_queue()."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "helpers_test.db")
        self.store = SQLiteJobStore(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_enqueue_and_run_queue(self):
        job_id = enqueue(
            GPUJob(kind="text", model_id="gemma4:12b", payload={"p": "1"}),
            store=self.store,
        )
        self.assertIsNotNone(job_id)

        processed = run_queue(
            store=self.store,
            is_madden_running=lambda: False,
            stop_on_empty=True,
            poll_interval=0.01,
            default_handler=mock_job_handler,
        )
        self.assertEqual(processed, 1)

        saved = self.store.get(job_id)
        self.assertEqual(saved.status, STATUS_COMPLETED)


if __name__ == "__main__":
    unittest.main()
