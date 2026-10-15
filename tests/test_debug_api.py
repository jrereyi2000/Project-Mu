"""server/debug_api.py jobs: start, one at a time, status from the exit code, stop, token. A dummy command
stands in for the weekly run (a real one would take over the console); no database."""
import os, sys, tempfile, time, unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import server.debug_api as dbg


def wait_for(client, job_id, status, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/debug/jobs/{job_id}/log").json()["job"]
        if job["status"] == status:
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} never reached {status}")


class JobsTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(dbg.router)
        self.client = TestClient(app)
        self.tmp = tempfile.mkdtemp()
        self.script = "print('hello from the job')"
        patches = [mock.patch.object(dbg, "JOBS_DIR", self.tmp),
                   mock.patch.object(dbg, "env", lambda k, d=None: "tok" if k == "DEBUG_TOKEN" else d),
                   mock.patch.object(dbg, "job_command", lambda kind, resume=None: [sys.executable, "-c", self.script])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.h = {"X-Debug-Token": "tok"}

    def start(self, kind="backfill_week"):
        r = self.client.post("/api/debug/jobs", json={"kind": kind}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def test_token_required(self):
        self.assertEqual(self.client.post("/api/debug/jobs", json={"kind": "scan_week"}).status_code, 401)
        self.assertEqual(self.client.post("/api/debug/jobs", json={"kind": "scan_week"},
                                          headers={"X-Debug-Token": "nope"}).status_code, 401)

    def test_job_runs_and_logs(self):
        job_id = self.start()
        job = wait_for(self.client, job_id, "done")
        self.assertEqual(job["exit_code"], 0)
        self.assertIn("hello from the job", self.client.get(f"/api/debug/jobs/{job_id}/log").json()["log"])
        listed = self.client.get("/api/debug/jobs").json()
        self.assertEqual(listed[0]["kind_label"], "Backfill previous week (no articles)")

    def test_roster_scan_job(self):
        job_id = self.start(kind="roster_scan")
        job = wait_for(self.client, job_id, "done")
        self.assertEqual(job["exit_code"], 0)
        listed = self.client.get("/api/debug/jobs").json()
        self.assertEqual(listed[0]["kind_label"], "Roster scan")

    def test_draft_scan_job(self):
        job_id = self.start(kind="draft_scan")
        job = wait_for(self.client, job_id, "done")
        self.assertEqual(job["exit_code"], 0)
        listed = self.client.get("/api/debug/jobs").json()
        self.assertEqual(listed[0]["kind_label"], "Draft prospect scan")

    def test_failure_and_one_at_a_time(self):
        self.script = "import time, sys; time.sleep(1.5); sys.exit(3)"
        job_id = self.start()
        busy = self.client.post("/api/debug/jobs", json={"kind": "scan_week"}, headers=self.h)
        self.assertEqual(busy.status_code, 409)
        job = wait_for(self.client, job_id, "failed")
        self.assertEqual(job["exit_code"], 3)

    def test_stop(self):
        self.script = "import time\ntry:\n    time.sleep(30)\nexcept KeyboardInterrupt:\n    print('interrupted'); raise SystemExit(130)"
        job_id = self.start()
        wait_for(self.client, job_id, "running")
        time.sleep(0.5)                     # let the dummy reach its sleep
        self.assertEqual(self.client.post(f"/api/debug/jobs/{job_id}/stop", headers=self.h).status_code, 200)
        job = wait_for(self.client, job_id, "stopped")
        self.assertEqual(job["exit_code"], 130)

    def test_resume_must_be_a_captures_folder(self):
        r = self.client.post("/api/debug/jobs", json={"kind": "scan_week", "resume": "../etc"}, headers=self.h)
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
