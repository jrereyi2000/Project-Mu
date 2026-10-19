"""Network-free checks of extract.py's Team Salary reading (docs/team_management/01_CONTRACT_SCAN_PLAN.md
Phase 3). Gemini readings are seeded into extract._cache; the cap strip reader is mocked. The tables are
synthetic until Phase 0 drops real console frames in."""
import csv
import os
import tempfile
import unittest
from unittest import mock

import extract

LEFT_COLS = ["NAME", "CONV", "POS", "AGE", "OVR", "LEN", "REM", "TOTAL", "BONUS"]
RIGHT_COLS = ["BONUS", "SAVINGS", "PENALTY", "2036", "2037", "2038", "2039", "2040", "INCENTIVE", "NO TRADE"]

# (left row, right row) per player
PLAYERS = [
    (["B.Young", "", "QB", "25", "84", "4", "2", "$38.0M", "$24.6M"],
     ["$24.6M", "$0", "$24.6M", "$10.3M", "$11.9M", "$0", "$0", "$0", "NONE", "NO"]),
    (["D.Brown", "", "LT", "29", "88", "5", "3", "$90.0M", "$30.0M"],
     ["$30.0M", "-$2.1M", "$18.0M", "$20.4M", "$21.0M", "$22.5M", "$0", "$0", "PRO BOWL", "YES"]),
    (["J.Horn", "", "CB", "32", "86", "3", "1", "$45.0M", "$12.0M"],
     ["$12.0M", "$9.8M", "$4.0M", "$15.2M", "$0", "$0", "$0", "$0", "NONE", "NO"]),
    (["T.Ramos", "", "LEDG", "26", "79", "4", "4", "$8.2M", "$1.1M"],
     ["$1.1M", "$1.4M", "$1.1M", "$1.7M", "$1.9M", "$2.1M", "$2.4M", "$0", "NONE", "NO"]),
    (["V.Amaya", "", "FS", "23", "71", "4", "3", "$4.4M", "$200K"],
     ["$200K", "$800K", "$150K", "$950K", "$1.1M", "$1.2M", "$0", "$0", "NONE", "NO"]),
]

STRIP = {"cap_label": "36 CAP", "cap": "$305.2M", "rollover_label": "36 ROLLOVER", "rollover": "$12.1M",
         "adjusted_cap": "$317.3M", "cap_space": "$9.4M", "salaries": "$300.1M", "cap_penalty": "$7.8M",
         "total_spend": "$307.9M", "rookie_res_label": "37 ROOKIE RES", "rookie_res": "$11.2M"}


def page(cols, rows):
    return {"title": "PANTHERS", "columns": list(cols), "rows": [list(r) for r in rows]}


class SalaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.team = os.path.join(self.root, "contracts", "01_panthers")
        os.makedirs(self.team)
        self.seeded = []
        extract.FROM_BOTTOM.clear()

    def tearDown(self):
        for p in self.seeded:
            extract._cache.pop(p, None)
        self.tmp.cleanup()

    def seed(self, num, players, right_override=None):
        lp = os.path.join(self.team, f"salary_{num:03d}.png")
        rp = os.path.join(self.team, f"salary_right_{num:03d}.png")
        for p in (lp, rp):
            open(p, "w").close()
        extract._cache[lp] = page(LEFT_COLS, [l for l, _ in players])
        extract._cache[rp] = page(RIGHT_COLS, right_override or [r for _, r in players])
        self.seeded += [lp, rp]
        return lp, rp

    def read_csv(self, name):
        with open(os.path.join(self.root, name), newline="") as fh:
            return list(csv.reader(fh))

    def test_is_salary(self):
        self.assertTrue(extract._is_salary(os.path.join(self.team, "salary_000.png")))
        self.assertFalse(extract._is_salary(os.path.join(self.root, "roster", "01_panthers", "list_01.png")))
        self.assertFalse(extract._is_roster(os.path.join(self.team, "salary_000.png")))

    def test_salary_col(self):
        self.assertEqual(extract.salary_col("NO TRADE"), "NO TRADE CLAUSE")
        self.assertEqual(extract.salary_col("▼OVR"), "OVR")
        self.assertEqual(extract.salary_col("incentives"), "INCENTIVE")

    def test_pair_merges_and_overlap_not_duplicated(self):
        self.seed(0, PLAYERS[:3])
        self.seed(1, PLAYERS[2:])                       # last page overlaps J.Horn
        with mock.patch.object(extract, "_read_strip", return_value=dict(STRIP)):
            extract.extract_contracts(self.root)
        rows = self.read_csv("contracts.csv")
        header = rows[0]
        self.assertEqual(header, ["TEAM", "NAME", "POS", "AGE", "OVR", "LEN", "REM", "TOTAL", "BONUS", "SAVINGS",
                                  "PENALTY", "2036", "2037", "2038", "2039", "2040", "INCENTIVE", "NO TRADE CLAUSE"])
        self.assertEqual([r[1] for r in rows[1:]], ["B.Young", "D.Brown", "J.Horn", "T.Ramos", "V.Amaya"])
        brown = dict(zip(header, rows[2]))
        self.assertEqual(brown["TEAM"], "PANTHERS")
        self.assertEqual(brown["SAVINGS"], "-$2.1M")
        self.assertEqual(brown["2038"], "$22.5M")
        self.assertEqual(brown["NO TRADE CLAUSE"], "YES")
        cap = self.read_csv("team_cap.csv")
        self.assertEqual(cap[0], ["TEAM", "CAP_LABEL", "CAP", "ROLLOVER_LABEL", "ROLLOVER", "ADJUSTED_CAP",
                                  "CAP_SPACE", "SALARIES", "CAP_PENALTY", "TOTAL_SPEND", "ROOKIE_RES_LABEL",
                                  "ROOKIE_RES"])
        self.assertEqual(cap[1][:3], ["PANTHERS", "36 CAP", "$305.2M"])

    def test_misaligned_pair_is_reread(self):
        shifted = [r for _, r in PLAYERS[1:3]] + [PLAYERS[0][1]]          # right half one row off
        lp, rp = self.seed(0, PLAYERS[:3], right_override=shifted)
        good, left = page(RIGHT_COLS, [r for _, r in PLAYERS[:3]]), extract._cache[lp]
        with mock.patch.object(extract, "_read_table_remote", side_effect=lambda p: good if p == rp else left), \
                mock.patch.object(extract, "_read_strip", return_value=dict(STRIP)):
            extract.extract_contracts(self.root)
        self.assertEqual(len(self.read_csv("contracts.csv")), 4)

    def test_pair_that_never_aligns_stops(self):
        shifted = [r for _, r in PLAYERS[1:3]] + [PLAYERS[0][1]]
        lp, rp = self.seed(0, PLAYERS[:3], right_override=shifted)
        left, right = extract._cache[lp], extract._cache[rp]
        with mock.patch.object(extract, "_read_table_remote", side_effect=lambda p: right if p == rp else left), \
                mock.patch.object(extract, "_read_strip", return_value=dict(STRIP)):
            with self.assertRaises(RuntimeError) as e:
                extract.extract_contracts(self.root)
        self.assertIn("don't line up", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(self.root, "contracts.csv")))

    def test_row_count_mismatch_is_misaligned(self):
        self.assertTrue(extract.bonus_misaligned(page(LEFT_COLS, [l for l, _ in PLAYERS[:2]]),
                                                 page(RIGHT_COLS, [r for _, r in PLAYERS[:1]])))
        self.assertEqual(extract.bonus_misaligned(page(LEFT_COLS, [l for l, _ in PLAYERS]),
                                                  page(RIGHT_COLS, [r for _, r in PLAYERS])), [])


class CapStripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.team = os.path.join(self.tmp.name, "contracts", "01_panthers")
        os.makedirs(self.team)
        for n in ("salary_000.png", "salary_right_000.png", "salary_001.png"):
            open(os.path.join(self.team, n), "w").close()

    def tearDown(self):
        self.tmp.cleanup()

    def reads(self, *strips):
        return mock.patch.object(extract, "_read_strip", side_effect=[dict(s) for s in strips])

    def test_two_agreeing_reads(self):
        with self.reads(STRIP, STRIP) as r:
            self.assertEqual(extract.read_cap_strip(self.team), STRIP)
        self.assertEqual(r.call_count, 2)

    def test_disagreement_reads_a_third_time_and_majority_wins(self):
        bad = dict(STRIP, cap_space="$8.4M")
        with self.reads(STRIP, bad, STRIP) as r:
            self.assertEqual(extract.read_cap_strip(self.team), STRIP)
        self.assertEqual(r.call_count, 3)
        self.assertTrue(r.call_args_list[2].args[0].endswith("salary_001.png"))

    def test_no_majority_raises(self):
        with self.reads(STRIP, dict(STRIP, cap="$1M"), dict(STRIP, cap="$2M")):
            with self.assertRaises(RuntimeError):
                extract.read_cap_strip(self.team)

    def test_identity_miss_triggers_third_read(self):
        off = dict(STRIP, adjusted_cap="$327.3M")          # ADJUSTED CAP != CAP + ROLLOVER
        self.assertTrue(extract.cap_identity_misses(off))
        self.assertEqual(extract.cap_identity_misses(STRIP), [])
        with self.reads(off, off, STRIP) as r:
            self.assertEqual(extract.read_cap_strip(self.team), off)   # majority kept, warning printed
        self.assertEqual(r.call_count, 3)


if __name__ == "__main__":
    unittest.main()
