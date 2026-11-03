"""
tests/test_weekly_freeze.py: pipeline/weekly_freeze.py (paired database + save freezes, list, restore).
pg_dump / pg_restore and database creation are faked; nothing touches Postgres.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from pipeline import weekly_freeze as wf


def fake_dumper(content=b"PGDMP fake"):
    def dump(db, dsn, out_path):
        with open(out_path, "wb") as fh:
            fh.write(content + db.encode())
    return dump


class FreezeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "freezes")
        self.saves = os.path.join(self.tmp, "saves")
        os.makedirs(self.saves)
        self.save = os.path.join(self.saves, "CAREER-MU")
        with open(self.save, "wb") as fh:
            fh.write(b"week 5 save")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_labels(self):
        self.assertEqual(wf.label_for(2026, 5), "2026_w05")
        self.assertEqual(wf.label_for(2026, 19), "2026_w19")
        self.assertEqual(wf.label_for(2027, None, {"stage": "RESIGN", "wave": None, "season": 2026}), "2026_off_RESIGN")
        self.assertEqual(wf.label_for(2027, None, {"stage": "FREE_AGENCY", "wave": 2, "season": 2026}),
                         "2026_off_FREE_AGENCY_w2")
        self.assertEqual(wf.label_for(2026, None), "2026_pre")

    def test_freeze_writes_dump_save_and_manifest(self):
        m = wf.freeze("madden_mu", "dbname=madden_mu", self.save, "2026_w05", season=2026, week=5,
                      extra={"articles": False}, root=self.root, dumper=fake_dumper())
        folder = os.path.join(self.root, "madden_mu", "2026_w05")
        self.assertEqual(sorted(os.listdir(folder)), ["CAREER-MU", "db.dump", "manifest.json"])
        with open(os.path.join(folder, "manifest.json")) as fh:
            self.assertEqual(json.load(fh), m)
        self.assertEqual(m["save_sha256"], wf.file_sha256(self.save))
        self.assertEqual((m["season"], m["week"], m["articles"]), (2026, 5, False))
        wf.verify(folder, m)

    def test_refreeze_replaces_the_label(self):
        wf.freeze("madden_mu", "d", self.save, "2026_w05", root=self.root, dumper=fake_dumper(b"one"))
        with open(self.save, "wb") as fh:
            fh.write(b"league office output")
        m = wf.freeze("madden_mu", "d", self.save, "2026_w05", root=self.root, dumper=fake_dumper(b"two"))
        base = os.path.join(self.root, "madden_mu")
        self.assertEqual(os.listdir(base), ["2026_w05"])
        with open(os.path.join(base, "2026_w05", "CAREER-MU"), "rb") as fh:
            self.assertEqual(fh.read(), b"league office output")
        self.assertEqual(m["save_sha256"], wf.file_sha256(self.save))

    def test_failed_dump_leaves_nothing_behind_and_keeps_the_old_freeze(self):
        wf.freeze("madden_mu", "d", self.save, "2026_w05", root=self.root, dumper=fake_dumper())

        def broken(db, dsn, out):
            raise wf.FreezeError("pg_dump failed")
        with self.assertRaises(wf.FreezeError):
            wf.freeze("madden_mu", "d", self.save, "2026_w05", root=self.root, dumper=broken)
        self.assertEqual(os.listdir(os.path.join(self.root, "madden_mu")), ["2026_w05"])

    def test_list(self):
        wf.freeze("madden_mu", "d", self.save, "2026_w05", season=2026, week=5, root=self.root, dumper=fake_dumper())
        wf.freeze("madden_mu", "d", self.save, "2026_w06", season=2026, week=6, root=self.root, dumper=fake_dumper())
        wf.freeze("madden_b", "d", self.save, "2026_w01", root=self.root, dumper=fake_dumper())
        self.assertEqual([m["label"] for m in wf.list_freezes("madden_mu", root=self.root)], ["2026_w05", "2026_w06"])
        self.assertEqual(len(wf.list_freezes(root=self.root)), 3)

    def test_verify_catches_a_changed_file(self):
        m = wf.freeze("madden_mu", "d", self.save, "2026_w05", root=self.root, dumper=fake_dumper())
        folder = os.path.join(self.root, "madden_mu", "2026_w05")
        with open(os.path.join(folder, "CAREER-MU"), "ab") as fh:
            fh.write(b"!")
        with self.assertRaises(wf.FreezeError):
            wf.verify(folder, m)

    def test_dump_db_uses_docker_without_local_pg_dump(self):
        runs = []

        def runner(cmd, **kw):
            runs.append(cmd)
            return mock.Mock(returncode=0, stderr=b"")
        with mock.patch("pipeline.weekly_freeze.shutil.which", return_value=None):
            wf.dump_db("madden_mu", "dbname=madden_mu", os.path.join(self.tmp, "x.dump"), runner=runner)
        self.assertEqual(runs[0][:3], ["docker", "exec", "madden-db"])
        self.assertIn("-Fc", runs[0])


class RestoreTest(unittest.TestCase):
    tearDown = FreezeTest.tearDown

    def setUp(self):
        FreezeTest.setUp(self)
        self.dbs = {"madden_mu"}
        self.restored = []
        fr = "db.franchises"
        for p in (mock.patch(f"{fr}.drop_db", side_effect=lambda db: self.dbs.discard(db)),
                  mock.patch(f"{fr}.create_db", side_effect=lambda db: self.dbs.add(db)),
                  mock.patch(f"{fr}.load_registry",
                             return_value={"active": "1", "franchises": {"1": {"db": "madden_mu"}}})):
            p.start()
        self.addCleanup(mock.patch.stopall)
        wf.freeze("madden_mu", "d", self.save, "2026_w05", season=2026, week=5, root=self.root, dumper=fake_dumper())
        with open(self.save, "wb") as fh:
            fh.write(b"week 9 save")

    def restorer(self, db, dump_path):
        self.restored.append((db, os.path.basename(os.path.dirname(dump_path))))

    def test_restore_league_puts_db_and_save_back_and_keeps_an_undo(self):
        wf.restore_league("madden_mu", "2026_w05", self.saves, "madden_mu", root=self.root, restorer=self.restorer,
                          dumper=fake_dumper(), madden_running=lambda: False, log_fn=lambda m: None)
        self.assertEqual(self.restored, [("madden_mu", "2026_w05")])
        with open(self.save, "rb") as fh:
            self.assertEqual(fh.read(), b"week 5 save")
        pre = [m for m in wf.list_freezes("madden_mu", root=self.root) if m["label"].startswith("pre_restore_")]
        self.assertEqual(len(pre), 1)
        self.assertEqual(pre[0]["restoring"], "2026_w05")
        with open(os.path.join(self.root, "madden_mu", pre[0]["label"], "CAREER-MU"), "rb") as fh:
            self.assertEqual(fh.read(), b"week 9 save")

    def test_restore_league_guards(self):
        kw = dict(root=self.root, restorer=self.restorer, dumper=fake_dumper(), log_fn=lambda m: None)
        with self.assertRaises(wf.FreezeError):
            wf.restore_league("madden_mu", "2026_w05", self.saves, "wrong", madden_running=lambda: False, **kw)
        with self.assertRaises(wf.FreezeError):
            wf.restore_league("madden_mu", "2026_w05", self.saves, "madden_mu", madden_running=lambda: True, **kw)
        with self.assertRaises(wf.FreezeError):
            wf.restore_league("madden_mu", "2030_w01", self.saves, "madden_mu", madden_running=lambda: False, **kw)
        self.assertEqual(self.restored, [])

    def test_restore_scratch_refuses_a_franchise_database(self):
        with self.assertRaises(wf.FreezeError):
            wf.restore_scratch("madden_mu", "2026_w05", "madden_mu", root=self.root, restorer=self.restorer)
        wf.restore_scratch("madden_mu", "2026_w05", "madden_snap", root=self.root, restorer=self.restorer)
        self.assertEqual(self.restored, [("madden_snap", "2026_w05")])
        with open(self.save, "rb") as fh:
            self.assertEqual(fh.read(), b"week 9 save", "the scratch restore never touches the save")


if __name__ == "__main__":
    unittest.main()
