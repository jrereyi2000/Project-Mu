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
                   mock.patch.object(dbg, "job_command", lambda kind, resume=None, week=None: [sys.executable, "-c", self.script])]
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

    def test_season_scan_job(self):
        job_id = self.start(kind="season_scan")
        job = wait_for(self.client, job_id, "done")
        self.assertEqual(job["exit_code"], 0)
        listed = self.client.get("/api/debug/jobs").json()
        self.assertEqual(listed[0]["kind_label"], "Season scan (schedule, rosters, contracts, draft board)")

    def test_contract_scan_job(self):
        job_id = self.start(kind="contract_scan")
        job = wait_for(self.client, job_id, "done")
        self.assertEqual(job["exit_code"], 0)
        listed = self.client.get("/api/debug/jobs").json()
        self.assertEqual(listed[0]["kind_label"], "Contract scan (Team Salary, all 32 teams)")
        self.assertEqual(dbg.JOB_KINDS["contract_scan"][1], ["--mode", "contracts", "--no-articles"])

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

    def test_publish_articles_job(self):
        job_id = self.start(kind="publish_articles")
        job = wait_for(self.client, job_id, "done")
        self.assertEqual(job["exit_code"], 0)
        listed = self.client.get("/api/debug/jobs").json()
        self.assertEqual(listed[0]["kind_label"], "Publish articles (newsroom run)")

    def test_publish_articles_with_week(self):
        r = self.client.post("/api/debug/jobs", json={"kind": "publish_articles", "week": 18}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        job_id = r.json()["id"]
        job = wait_for(self.client, job_id, "done")
        self.assertEqual(job["exit_code"], 0)
        self.assertEqual(job.get("week"), 18)

    def test_job_invalid_week(self):
        r = self.client.post("/api/debug/jobs", json={"kind": "publish_articles", "week": 99}, headers=self.h)
        self.assertEqual(r.status_code, 400)

    def test_news_summary_endpoint(self):
        fake_conn = mock.MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.execute.side_effect = [
            mock.MagicMock(fetchone=lambda: {"current_season": 2035, "current_week": 21}),
            mock.MagicMock(fetchall=lambda: [{"season": 2035, "week": 21, "articles": 9, "tweets": 10}]),
            mock.MagicMock(fetchone=lambda: {"total_articles": 9, "total_tweets": 10}),
        ]
        with mock.patch.object(dbg, "connect", return_value=fake_conn):
            r = self.client.get("/api/debug/news")
            self.assertEqual(r.status_code, 200)
            data = r.json()
            self.assertEqual(data["current_season"], 2035)
            self.assertEqual(data["current_week"], 21)
            self.assertEqual(data["total_articles"], 9)
            self.assertEqual(data["total_tweets"], 10)
            self.assertEqual(len(data["summary"]), 1)

    def test_clear_news_validation(self):
        r = self.client.post("/api/debug/news/clear", json={"content_type": "ARTICLE"})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/debug/news/clear", json={"content_type": "ARTICLE"}, headers={"X-Debug-Token": "bad"})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/debug/news/clear", json={"content_type": "INVALID"}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/debug/news/clear", json={"content_type": "ARTICLE", "week": 25}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/debug/news/clear", json={"content_type": "ARTICLE", "season": 1999}, headers=self.h)
        self.assertEqual(r.status_code, 400)

    def test_clear_news_execution(self):
        fake_conn = mock.MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_cursor = mock.MagicMock()
        fake_conn.transaction.return_value.__enter__.return_value = fake_cursor
        fake_conn.execute.side_effect = [
            mock.MagicMock(fetchall=lambda: [{"id": 1}, {"id": 2}]),  # DELETE news_feed
            mock.MagicMock(fetchone=lambda: {"ex": True}),             # to_regclass agent_season_summaries
            mock.MagicMock(),                                          # DELETE agent_season_summaries
            mock.MagicMock(fetchone=lambda: {"remaining_articles": 5, "remaining_tweets": 20}),
        ]
        with mock.patch.object(dbg, "connect", return_value=fake_conn), \
             mock.patch.object(dbg, "backup_news_tables", return_value="news_feed_backup_mock.sql"):
            r = self.client.post("/api/debug/news/clear",
                                 json={"season": 2035, "week": 21, "content_type": "ARTICLE", "backup": True},
                                 headers=self.h)
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertEqual(data["deleted"], 2)
            self.assertEqual(data["remaining_articles"], 5)
            self.assertEqual(data["remaining_tweets"], 20)
            self.assertEqual(data["backup_file"], "news_feed_backup_mock.sql")
            self.assertEqual(data["scope"]["week"], 21)


if __name__ == "__main__":
    unittest.main()


class TransactionsDebugTest(unittest.TestCase):
    """League Transactions (team management step 2): the job kind and GET /api/debug/transactions, the latter
    against a temporary database only when TX_TEST_DATABASE_URL is set (never the live one)."""
    def test_job_kind(self):
        self.assertEqual(dbg.JOB_KINDS["transactions_scan"],
                         ("League transactions scan", ["--mode", "transactions", "--no-articles"]))
        cmd = dbg.job_command("transactions_scan")
        self.assertEqual(cmd[-4:], ["pipeline.weekly_capture", "--mode", "transactions", "--no-articles"])

    @unittest.skipUnless(os.environ.get("TX_TEST_DATABASE_URL"), "needs a scratch database")
    def test_endpoint_on_scratch_db(self):
        app = FastAPI()
        app.include_router(dbg.router)
        with mock.patch.object(dbg, "CONN_INFO", os.environ["TX_TEST_DATABASE_URL"]):
            body = TestClient(app).get("/api/debug/transactions?season=2036").json()
        self.assertTrue(body["installed"])
        self.assertIn("pending_rescans", body)
