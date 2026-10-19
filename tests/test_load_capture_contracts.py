"""pipeline/load_capture.load_contracts and detect_mode for Team Salary runs (team management step 1, Phase 4).
A fake cursor returns a synthetic Panthers roster and records every statement; no database is opened."""
import csv
import os
import tempfile
import unittest

from pipeline import load_capture as L

YEARS = ["2036", "2037", "2038", "2039", "2040"]
HEADER = ["TEAM", "NAME", "POS", "AGE", "OVR", "LEN", "REM", "TOTAL", "BONUS", "SAVINGS", "PENALTY", *YEARS,
          "INCENTIVE", "NO TRADE CLAUSE"]
CAP_HEADER = ["TEAM", "CAP_LABEL", "CAP", "ROLLOVER_LABEL", "ROLLOVER", "ADJUSTED_CAP", "CAP_SPACE", "SALARIES",
              "CAP_PENALTY", "TOTAL_SPEND", "ROOKIE_RES_LABEL", "ROOKIE_RES"]
N = 46                                   # rows per team, inside the 45-56 bounds


def players(team_id, base):
    """(player_id, player_name, full_name, position, age, ovr) for a team."""
    tag = lambda i: "Player" + chr(97 + i // 26) + chr(97 + i % 26)          # name keys are letters only
    return [(base + i, f"P. {tag(i)}", f"Pat {tag(i)}", "WR", 22 + i % 10, 60 + i % 30) for i in range(N)]


ROSTER = {5: players(5, 1000), 6: players(6, 2000)}       # 5 = PANTHERS, 6 = SAINTS


def contract_rows(team, roster, cap_hit="$1.0M"):
    out = []
    for pid, pn, fn, pos, age, ovr in roster:
        out.append([team, pn.replace(". ", "."), pos, str(age), str(ovr), "3", "2", "$3.0M", "$600K", "$400K",
                    "$600K", cap_hit, "$1.0M", "$0", "$0", "$0", "NONE", "NO"])
    return out


def cap_row(team, label="36 CAP", rookie="37 ROOKIE RES", salaries="$46.0M"):
    return [team, label, "$305.2M", "35 ROLLOVER", "$12.1M", "$317.3M", "$263.5M", salaries, "$7.8M", "$53.8M",
            rookie, "$11.2M"]


class Cur:
    def __init__(self, roster=ROSTER):
        self.roster, self.calls, self.next_id, self.rowcount, self._rows = roster, [], 0, 7, []

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        self.calls.append((sql, params))
        if "FROM teams" in sql:
            self._rows = [(5, "PANTHERS"), (6, "SAINTS")] + [(100 + i, f"TEAM{i}") for i in range(30)]
        elif "FROM players p JOIN player_seasons" in sql:
            self._rows = [(pid, t, pn, fn, pos, age, ovr) for t, ps in self.roster.items()
                          for pid, pn, fn, pos, age, ovr in ps]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        self.next_id += 1
        return (self.next_id,)

    def sql(self, start):
        return [c for c in self.calls if c[0].startswith(start)]


class LoadContractsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rows, caps, targets=("PANTHERS",), header=HEADER):
        with open(os.path.join(self.folder, "contracts.csv"), "w", newline="") as fh:
            csv.writer(fh).writerows([header] + rows)
        with open(os.path.join(self.folder, "team_cap.csv"), "w", newline="") as fh:
            csv.writer(fh).writerows([CAP_HEADER] + caps)
        if targets is not None:
            os.makedirs(os.path.join(self.folder, "contracts"), exist_ok=True)
            with open(os.path.join(self.folder, "contracts", "targets.txt"), "w") as fh:
                fh.write("".join(t + "\n" for t in targets))

    def load(self, season=2036, cur=None):
        cur = cur or Cur()
        report = {"capture_id": 42}
        L.load_contracts(cur, self.folder, season, report)
        return cur, report

    def test_panthers_load(self):
        rows = contract_rows("PANTHERS", ROSTER[5])
        rows[0][-2], rows[0][-1] = "PRO BOWL", "YES"
        self.write(rows, [cap_row("PANTHERS")])
        cur, report = self.load()
        inserts = cur.sql("INSERT INTO player_contracts")
        self.assertEqual(len(inserts), N)
        first = inserts[0][1]
        self.assertEqual(first[:11], (1000, 5, 2036, 3, 2, 3_000_000, 600_000, 400_000, 600_000, True, "PRO BOWL"))
        self.assertEqual(first[11:], ("SCAN", 42))
        self.assertIsNone(inserts[1][1][10])                         # NONE incentive -> NULL
        years = cur.sql("INSERT INTO player_contract_years")
        self.assertEqual(len(years), N * 5)
        self.assertEqual([y[1][1:] for y in years[:5]], [(2036, 1_000_000), (2037, 1_000_000), (2038, 0), (2039, 0), (2040, 0)])
        caps = cur.sql("INSERT INTO team_cap_seasons")
        self.assertEqual(len(caps), 1)
        self.assertEqual(caps[0][1][:3], (5, 2036, 305_200_000))
        self.assertEqual(report["contracts"], N)
        self.assertEqual(report["superseded"], 7)
        self.assertEqual(report["cap_warnings"], [])

    def test_partial_run_supersedes_only_its_team(self):
        self.write(contract_rows("PANTHERS", ROSTER[5]), [cap_row("PANTHERS")])
        cur, _ = self.load()
        sup = cur.sql("UPDATE player_contracts SET status = 'SUPERSEDED'")
        self.assertEqual(len(sup), 1)
        season, teams, pids = sup[0][1]
        self.assertEqual((season, teams), (2036, [5]))
        self.assertEqual(pids, [p[0] for p in ROSTER[5]])
        # supersede runs before the first insert
        order = [c[0][:30] for c in cur.calls if c[0].startswith(("UPDATE player_contracts", "INSERT INTO player_contracts"))]
        self.assertTrue(order[0].startswith("UPDATE"))

    def test_year_check(self):
        self.write(contract_rows("PANTHERS", ROSTER[5]), [cap_row("PANTHERS")])
        self.load(2036)                                            # passes
        with self.assertRaises(L.LoadError) as e:
            self.load(2035)
        self.assertIn("TEAM SALARY shows 2036, league_state is 2035", str(e.exception))

    def test_year_columns_must_start_at_the_cap_year(self):
        header = HEADER[:11] + ["2037", "2038", "2039", "2040", "2041"] + HEADER[16:]
        self.write(contract_rows("PANTHERS", ROSTER[5]), [cap_row("PANTHERS")], header=header)
        with self.assertRaises(L.LoadError) as e:
            self.load()
        self.assertIn("year columns", str(e.exception))

    def test_rookie_reserve_label(self):
        self.write(contract_rows("PANTHERS", ROSTER[5]), [cap_row("PANTHERS", rookie="36 ROOKIE RES")])
        with self.assertRaises(L.LoadError):
            self.load()

    def test_ambiguous_row_writes_nothing(self):
        roster = {5: ROSTER[5] + [(9999, "P. Playeraa", "Pete Playeraa", "WR", 22, 60)]}   # a twin of row 0
        self.write(contract_rows("PANTHERS", ROSTER[5]), [cap_row("PANTHERS")])
        cur = Cur(roster)
        with self.assertRaises(L.LoadError) as e:
            self.load(cur=cur)
        self.assertIn("can't be told apart", str(e.exception))
        self.assertEqual(cur.sql("INSERT"), [])
        self.assertEqual(cur.sql("UPDATE"), [])

    def test_unknown_player_and_junk_money_stop(self):
        rows = contract_rows("PANTHERS", ROSTER[5])
        rows[0][1] = "Z.Nobody"
        rows[1][7] = "S3.0M"
        self.write(rows, [cap_row("PANTHERS")])
        with self.assertRaises(L.LoadError) as e:
            self.load()
        msg = str(e.exception)
        self.assertIn("not on the team's 2036 roster", msg)
        self.assertIn("not money", msg)
        self.assertIn("transactions scan", msg)

    def test_rem_over_len_stops(self):
        rows = contract_rows("PANTHERS", ROSTER[5])
        rows[0][6] = "4"
        self.write(rows, [cap_row("PANTHERS")])
        with self.assertRaises(L.LoadError):
            self.load()

    def test_teams_must_be_the_targets(self):
        self.write(contract_rows("PANTHERS", ROSTER[5]), [cap_row("PANTHERS")], targets=("PANTHERS", "SAINTS"))
        with self.assertRaises(L.LoadError) as e:
            self.load()
        self.assertIn("missing ['SAINTS']", str(e.exception))

    def test_no_targets_means_all_32(self):
        self.write(contract_rows("PANTHERS", ROSTER[5]), [cap_row("PANTHERS")], targets=None)
        with self.assertRaises(L.LoadError):
            self.load()

    def test_cap_warnings_not_fatal(self):
        self.write(contract_rows("PANTHERS", ROSTER[5]), [cap_row("PANTHERS", salaries="$99.0M")])
        _, report = self.load()
        self.assertTrue(any("SALARIES" in w for w in report["cap_warnings"]))

    def test_row_count_bounds(self):
        self.write(contract_rows("PANTHERS", ROSTER[5][:30]), [cap_row("PANTHERS")])
        with self.assertRaises(L.LoadError) as e:
            self.load()
        self.assertIn("30 contract rows", str(e.exception))


class DetectModeTest(unittest.TestCase):
    def folder(self, *files):
        d = tempfile.mkdtemp()
        for f in files:
            open(os.path.join(d, f), "w").close()
        return d

    def test_contracts_alone(self):
        self.assertEqual(L.detect_mode(self.folder("contracts.csv", "team_cap.csv")), "contracts")

    def test_season_with_contracts_stays_season(self):
        self.assertEqual(L.detect_mode(self.folder("contracts.csv", "team_cap.csv", "roster_cards.csv",
                                                   "season_schedule.csv")), "season")

    def test_contracts_loader_registered(self):
        self.assertIs(L.LOADERS["contracts"], L.load_contracts)


if __name__ == "__main__":
    unittest.main()
