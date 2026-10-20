"""Console-free checks of madden_capture.py's Team Salary (contracts) capture
(docs/team_management/01_CONTRACT_SCAN_PLAN.md Phase 2). Frames come from tests/fixtures/team_salary/*.json,
OCR item lists in the shape --step writes; until Phase 0 they are synthetic placeholders."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

if sys.platform != "darwin":
    raise unittest.SkipTest("madden_capture.py needs macOS (Quartz, Vision)")

import madden_capture as mc

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "team_salary")


class _Frame:
    def __init__(self, items, size=(3024, 1888)):
        self.items = [(t, tuple(b)) for t, b in items]
        self.text = " | ".join(t for t, _ in self.items)
        self.size = tuple(size)
        self.path = None

    find = mc.Frame.find


def fixture(name):
    d = json.load(open(os.path.join(FIXTURES, name + ".json")))
    return _Frame(d["items"], d["size"])


class ClassifyTest(unittest.TestCase):
    def test_both_edges_are_team_salary(self):
        self.assertEqual(mc.classify(fixture("left_edge")), "TEAM_SALARY")
        self.assertEqual(mc.classify(fixture("right_edge")), "TEAM_SALARY")
        self.assertEqual(mc.classify(fixture("saints_after_l2")), "TEAM_SALARY")

    def test_hub_with_team_salary_highlighted_stays_hub(self):
        self.assertEqual(mc.classify(fixture("hub_team_salary_highlighted")), "HUB_ROSTER")

    def test_roster_list_and_player_stats_unchanged(self):
        f = _Frame([(t, (0, 0, 1, 1)) for t in ("PANTHERS", "ROSTER", "NAME", "PLAYER NAME", "MOR", "SAVINGS", "PENALTY")])
        self.assertEqual(mc.classify(f), "ROSTER_LIST")
        f = _Frame([(t, (0, 0, 1, 1)) for t in ("NFL", "TFL", "VIEW PLAYER CARD", "SORT", "R2", "DOWNS PLAYED")])
        self.assertEqual(mc.classify(f), "PLAYER_STATS")

    def test_team_salary_is_a_list_screen(self):
        self.assertIn("TEAM_SALARY", mc.LIST_SCREENS)


class EdgeTest(unittest.TestCase):
    def test_edges(self):
        left, right = fixture("left_edge"), fixture("right_edge")
        self.assertTrue(mc.salary_at_edge(left, "left"))
        self.assertFalse(mc.salary_at_edge(left, "right"))
        self.assertTrue(mc.salary_at_edge(right, "right"))
        self.assertFalse(mc.salary_at_edge(right, "left"))

    def test_team(self):
        self.assertEqual(mc.salary_team(fixture("left_edge")), "PANTHERS")
        self.assertEqual(mc.salary_team(fixture("saints_after_l2")), "SAINTS")


class CrossGuardTest(unittest.TestCase):
    def frame(self, item):
        return mock.Mock(highlighted=lambda labels: item)

    def test_team_salary_allowed(self):
        with mock.patch.object(mc, "press") as press:
            mc.cross("HUB_ROSTER", self.frame("TEAM SALARY"))
        press.assert_called_once_with("cross", wait=1.5)

    def test_refused_items(self):
        for item in ("TRADE", "FREE AGENCY", "CONTRACTS", "UPGRADE PLAYERS", None):
            with self.subTest(item=item), mock.patch.object(mc, "press") as press:
                with self.assertRaises(mc.Abort):
                    mc.cross("HUB_ROSTER", self.frame(item))
                press.assert_not_called()

    def test_cross_on_the_table_refused(self):
        with mock.patch.object(mc, "press") as press:
            with self.assertRaises(mc.Abort):
                mc.cross("TEAM_SALARY", self.frame(None))
            press.assert_not_called()


ORDER = ["PANTHERS", "SAINTS", "BUCCANEERS", "FALCONS"] + sorted(mc.TEAMS - {"PANTHERS", "SAINTS", "BUCCANEERS", "FALCONS"})


class FakeConsole:
    """L2 steps through ORDER and wraps; capture_team_salary writes a folder (or fails once if asked)."""
    def __init__(self, out, fail_on=None):
        self.i, self.out, self.fail_on, self.captured, self.to_top = 0, out, set(fail_on or ()), [], []

    def press(self, key, **kw):
        if key == "l2":
            self.i = (self.i + 1) % len(ORDER)

    def team(self, f=None):
        return ORDER[self.i]

    def open(self):
        self.i = 0
        return object()

    def capture(self, out, index, team, to_top=True):
        self.to_top.append((team, to_top))
        folder = os.path.join(out, "contracts", f"{index:02d}_{team.lower()}")
        os.makedirs(folder, exist_ok=True)
        open(os.path.join(folder, "salary_000.png"), "w").close()
        if team in self.fail_on:
            self.fail_on.discard(team)
            raise mc.Abort(f"{team} capture failed")
        self.captured.append(team)
        return 6

    def patches(self):
        return [mock.patch.object(mc, "open_team_salary", side_effect=self.open),
                mock.patch.object(mc, "salary_team", side_effect=self.team),
                mock.patch.object(mc, "Frame", return_value=object()),
                mock.patch.object(mc, "press", side_effect=self.press),
                mock.patch.object(mc, "capture_team_salary", side_effect=self.capture),
                mock.patch.object(mc, "stream_frozen", return_value=False),
                mock.patch.object(mc, "log"),
                mock.patch.object(mc.time, "sleep")]


def run(console, *args, **kw):
    ps = console.patches()
    for p in ps:
        p.start()
    try:
        return mc.run_contracts(*args, **kw)
    finally:
        for p in ps:
            p.stop()


class RunContractsTest(unittest.TestCase):
    def test_full_run(self):
        with tempfile.TemporaryDirectory() as out:
            c = FakeConsole(out)
            run(c, out)
            self.assertEqual(sorted(c.captured), sorted(mc.TEAMS))
            done = mc.read_done(os.path.join(out, "contracts", "done.txt"))
            self.assertEqual(done["PANTHERS"], 1)
            self.assertEqual(len(done), 32)
            targets = open(os.path.join(out, "contracts", "targets.txt")).read().split()
            self.assertEqual(len(targets), 32)
            # only the team the screen opened on is scrolled to the top; L2 opens the next at the top (C5)
            self.assertEqual(c.to_top[0], ("PANTHERS", True))
            self.assertTrue(all(not t for _, t in c.to_top[1:]))

    def test_subset_stops_when_targets_done(self):
        with tempfile.TemporaryDirectory() as out:
            c = FakeConsole(out)
            run(c, out, ["SAINTS", "BUCCANEERS"])
            self.assertEqual(c.captured, ["SAINTS", "BUCCANEERS"])
            self.assertEqual(c.i, 2)                    # no L2 past the last target
            self.assertEqual(open(os.path.join(out, "contracts", "targets.txt")).read().split(),
                             ["BUCCANEERS", "SAINTS"])

    def test_resume_keeps_targets_and_clears_partial(self):
        with tempfile.TemporaryDirectory() as out:
            cdir = os.path.join(out, "contracts")
            os.makedirs(os.path.join(cdir, "02_falcons"))         # an unfinished team
            with open(os.path.join(cdir, "targets.txt"), "w") as fh:
                fh.write("FALCONS\nSAINTS\n")
            with open(os.path.join(cdir, "done.txt"), "w") as fh:
                fh.write("1 SAINTS\n")
            os.makedirs(os.path.join(cdir, "01_saints"))
            c = FakeConsole(out)
            run(c, out, None, resume=True)                       # no names: targets.txt, not 32 teams
            self.assertEqual(c.captured, ["FALCONS"])
            self.assertTrue(os.path.isdir(os.path.join(cdir, "01_saints")))
            self.assertEqual(mc.read_done(os.path.join(cdir, "done.txt")), {"SAINTS": 1, "FALCONS": 2})

    def test_already_done_skips_the_console(self):
        with tempfile.TemporaryDirectory() as out:
            cdir = os.path.join(out, "contracts")
            os.makedirs(cdir)
            with open(os.path.join(cdir, "targets.txt"), "w") as fh:
                fh.write("SAINTS\n")
            with open(os.path.join(cdir, "done.txt"), "w") as fh:
                fh.write("1 SAINTS\n")
            c = FakeConsole(out)
            with mock.patch.object(mc, "open_team_salary") as op, mock.patch.object(mc, "log"):
                mc.run_contracts(out, None, resume=True)
            op.assert_not_called()

    def test_different_targets_in_same_folder_refused(self):
        with tempfile.TemporaryDirectory() as out:
            run(FakeConsole(out), out, ["SAINTS"])
            with self.assertRaises(mc.Abort):
                run(FakeConsole(out), out, ["FALCONS"], resume=True)

    def test_failed_team_retried_and_folder_cleared(self):
        with tempfile.TemporaryDirectory() as out:
            c = FakeConsole(out, fail_on={"SAINTS"})
            run(c, out, ["SAINTS"])
            self.assertEqual(c.captured, ["SAINTS"])
            self.assertEqual(c.to_top, [("SAINTS", False), ("SAINTS", True)])     # the retry scrolls up first
            self.assertEqual(os.listdir(os.path.join(out, "contracts", "01_saints")), ["salary_000.png"])

    def test_wrap_before_done_raises(self):
        with tempfile.TemporaryDirectory() as out:
            c = FakeConsole(out)
            orig = ORDER[:]
            try:
                ORDER.remove("VIKINGS")                          # L2 never reaches one target
                with self.assertRaises(mc.Abort) as e:
                    run(c, out)
                self.assertIn("VIKINGS", str(e.exception))
                self.assertFalse(os.path.exists(os.path.join(out, "contracts", "32_vikings")))
            finally:
                ORDER[:] = orig

    def test_unknown_team_name(self):
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaises(mc.Abort):
                run(FakeConsole(out), out, ["PANTHER"])


if __name__ == "__main__":
    unittest.main()


class PairAlignmentTest(unittest.TestCase):
    """salary_pair_aligned on the real frames of one page (aligned), and with the right half's rows moved
    down six rows, as in the 2026-10-19 Panthers run (page 2's right half was taken after the lagging Down
    presses landed)."""
    def setUp(self):
        from PIL import Image
        self.tmp = tempfile.TemporaryDirectory()
        self.left, self.right = (os.path.join(self.tmp.name, n) for n in ("l.png", "r.png"))
        for p in (self.left, self.right):
            Image.new("L", (3024, 1888)).save(p)
        self.items = {self.left: fixture("left_edge").items, self.right: fixture("right_edge").items}

    def tearDown(self):
        self.tmp.cleanup()

    def aligned(self):
        with mock.patch.object(mc, "ocr", side_effect=lambda p: self.items[p]):
            return mc.salary_pair_aligned(self.left, self.right)

    def test_same_page_aligned(self):
        self.assertTrue(self.aligned())

    def test_right_half_six_rows_lower(self):
        step = (1510 - 681) / 11                 # row pitch on these frames (first and 12th row tops)
        shifted = []
        for t, b in self.items[self.right]:
            if b[0] < 400 and b[1] > 600 and t.startswith(("$", "S")):
                b = (b[0], b[1] - 6 * step, b[2], b[3] - 6 * step)       # row 7 now shows at row 1
                if b[1] < 640:
                    continue
            shifted.append((t, b))
        self.items[self.right] = shifted
        self.assertFalse(self.aligned())

    def test_unreadable_counts_as_aligned(self):
        self.items[self.right] = []
        self.assertTrue(self.aligned())


class SettleTest(unittest.TestCase):
    def test_waits_until_two_frames_match(self):
        sigs = iter(["a", "b", "c", "c"])
        with mock.patch.object(mc, "snap", return_value="x"), \
                mock.patch.object(mc, "table_sig", side_effect=lambda p: next(sigs)), \
                mock.patch.object(mc, "same_table", side_effect=lambda a, b: a == b), \
                mock.patch.object(mc.time, "sleep"):
            self.assertTrue(mc.settle_list())

    def test_gives_up_after_timeout(self):
        n = iter(range(1000))
        with mock.patch.object(mc, "snap", return_value="x"), \
                mock.patch.object(mc, "table_sig", side_effect=lambda p: next(n)), \
                mock.patch.object(mc, "same_table", side_effect=lambda a, b: a == b), \
                mock.patch.object(mc.time, "sleep"), mock.patch.object(mc, "log"), \
                mock.patch.object(mc.time, "time", side_effect=[0, 1, 2, 3, 99]):
            self.assertFalse(mc.settle_list(timeout=5))


class WideListPairCheckTest(unittest.TestCase):
    def test_bad_pair_is_shot_again(self):
        checks = iter([False, True, True])
        frame = mock.Mock(path="/tmp/f.png")
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(mc, "wait_table_loaded"), mock.patch.object(mc, "press"), \
                mock.patch.object(mc, "settle_list") as settle, mock.patch.object(mc, "log"), \
                mock.patch.object(mc.subprocess, "run", side_effect=lambda a: open(a[2], "w").close()), \
                mock.patch.object(mc, "table_sig", side_effect=lambda p: p.rsplit("_", 1)[1] if "_0" not in p else "same"), \
                mock.patch.object(mc, "same_table", side_effect=lambda a, b: a == b), \
                mock.patch.object(mc.os, "remove"):
            trav = mock.Mock(return_value=frame)
            pages = mc.capture_wide_list(folder, "salary", rows_per_page=10, traverse=trav, settle=True,
                                         pair_check=lambda l, r: next(checks), max_pages=2)
        self.assertEqual(pages, 1)
        # first pair failed: one settle for the re-shoot, plus one after the page's Down presses
        self.assertGreaterEqual(settle.call_count, 2)
