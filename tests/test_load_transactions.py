"""pipeline/load_transactions.py: the pure effects planner (no database)."""
import os, tempfile, unittest

from pipeline import load_transactions as L
from pipeline.money import parse_money

TEAMS = {"JAGUARS": 1, "BEARS": 2, "PACKERS": 3, "SAINTS": 4}
W3, W4 = "2036 SEASON - WEEK 3", "2036 SEASON - WEEK 4"
PRE = "2036 PRESEASON - WEEK 2"


def player(i, first, last, team, pos, this_season=True, released=None):
    return dict(id=i, player_name=f"{first[0]}. {last}", full_name=f"{first} {last}", team_id=team, position=pos,
                released_week=released, this_season=this_season)


def contract(cid, team, years=(1_000_000, 1_000_000, 0, 0, 0), length=2):
    return dict(id=cid, team_id=team, length=length, years_remaining=length, total_value=sum(years), bonus=100,
                cut_savings=1, cut_penalty=2, no_trade_clause=False, incentive=None,
                years={2036 + i: y for i, y in enumerate(years)})


def state(players=(), contracts=None, existing=(), counts=None, apply_from=None):
    return L._new_state(2036, TEAMS, list(players), existing, counts, contracts, apply_from)


def entries(*rows, meta=None):
    """rows newest first: (heading, text)."""
    return L.expand([{"heading": h, "text": t, "flags": []} for h, t in rows], meta)


def ops(plan, kind=None):
    return [o for e in plan for o in e["ops"] if kind is None or o["op"] == kind]


class SignTest(unittest.TestCase):
    def test_sign_known_free_agent(self):
        st = state([player(10, "Devonta", "Taylor", 2, "WR", released=2), player(11, "Al", "Roe", 1, "CB")], contracts={})
        plan, rep = L.plan_effects(entries((W4, "JAGUARS SIGNED FREE AGENT (WR) DEVONTA TAYLOR TO 1 YEAR, $3.93M CONTRACT")),
                                   st, parse_money)
        self.assertEqual(plan[0]["txn"]["player"], 10)
        self.assertEqual(ops(plan, "move"), [{"op": "move", "player": 10, "team_id": 1, "position": "WR"}])
        ins = ops(plan, "contract_insert")[0]
        self.assertEqual((ins["team_id"], ins["length"], ins["total_value"]), (1, 1, 3_930_000))
        self.assertEqual(ins["years"], {2036: 3_930_000, 2037: 0, 2038: 0, 2039: 0, 2040: 0})
        self.assertEqual(plan[0]["txn"]["contract_value"], 3_930_000)
        self.assertEqual(ops(plan, "cap_delta"), [{"op": "cap_delta", "team_id": 1, "salaries": 3_930_000, "cap_penalty": 0}])

    def test_sign_unknown_creates_player(self):
        plan, rep = L.plan_effects(entries((W4, "JAGUARS SIGNED FREE AGENT (WR) DEVONTA TAYLOR JR. TO 1 YEAR, $850K CONTRACT")),
                                   state(), parse_money)
        create = ops(plan, "create_player")[0]
        self.assertEqual((create["player_name"], create["full_name"], create["team_id"]),
                         ("D. Taylor Jr.", "Devonta Taylor Jr.", 1))
        self.assertEqual(plan[0]["txn"]["player"], create["tmp"])
        self.assertEqual(len(rep["created_players"]), 1)
        self.assertEqual(ops(plan, "contract_insert"), [])      # no player_contracts table in this state

    def test_sign_ambiguous_no_move(self):
        st = state([player(10, "Jo", "Smith", 2, "CB", released=2), player(12, "Jo", "Smith", 3, "CB", released=1)])
        plan, rep = L.plan_effects(entries((W4, "JAGUARS SIGNED FREE AGENT (CB) JO SMITH TO 1 YEAR, $1M CONTRACT")), st, parse_money)
        self.assertIsNone(plan[0]["txn"]["player"])
        self.assertEqual(ops(plan, "move"), [])
        self.assertEqual(len(rep["ambiguous"]), 1)
        self.assertIn("ambiguous", plan[0]["txn"]["flags"])

    def test_sign_player_not_on_any_roster_this_season_needs_position(self):
        st = state([player(20, "Bo", "Poe", None, "QB", this_season=False)])
        plan, _ = L.plan_effects(entries((W4, "BEARS SIGNED FREE AGENT (WR) BO POE TO 1 YEAR, $1M CONTRACT")), st, parse_money)
        self.assertEqual(len(ops(plan, "create_player")), 1)     # a QB of that name isn't this WR
        st = state([player(20, "Bo", "Poe", None, "WR", this_season=False)])
        plan, _ = L.plan_effects(entries((W4, "BEARS SIGNED FREE AGENT (WR) BO POE TO 1 YEAR, $1M CONTRACT")), st, parse_money)
        self.assertEqual(plan[0]["txn"]["player"], 20)


class ReleaseResignTest(unittest.TestCase):
    def test_release_keeps_team(self):
        st = state([player(30, "Julius", "Slaton", 1, "WR")], contracts={30: contract(500, 1)})
        plan, rep = L.plan_effects(entries((W4, "JAGUARS RELEASE (WR) JULIUS SLATON")), st, parse_money)
        self.assertEqual(ops(plan, "release"), [{"op": "release", "player": 30, "week": 4, "team_id": 1, "position": "WR"}])
        self.assertEqual(ops(plan, "move"), [])
        self.assertEqual(ops(plan, "contract_status"), [{"op": "contract_status", "player": 30, "status": "RELEASED"}])
        # his 2036 cap hit leaves the books; the stored cut penalty (2) stays as dead money
        self.assertEqual(ops(plan, "cap_delta"), [{"op": "cap_delta", "team_id": 1, "salaries": -1_000_000, "cap_penalty": 2}])
        self.assertEqual(plan[0]["txn"]["from_team_id"], 1)

    def test_sign_then_release_same_week_oldest_first(self):
        st = state([player(10, "Devonta", "Taylor", 2, "WR", released=2)], contracts={})
        plan, _ = L.plan_effects(entries((W4, "JAGUARS RELEASE (WR) DEVONTA TAYLOR"),                     # newer
                                         (W4, "JAGUARS SIGNED FREE AGENT (WR) DEVONTA TAYLOR TO 1 YEAR, $1M CONTRACT")),
                                 st, parse_money)
        self.assertEqual([e["txn"]["type"] for e in plan], ["SIGNED", "RELEASED"])
        self.assertEqual([o["op"] for o in ops(plan) if o["op"] in ("move", "release")], ["move", "release"])
        self.assertEqual(ops(plan, "release")[0]["player"], 10)

    def test_resign_projects_years_after_current_deal(self):
        # current deal: 2 years left (2036, 2037); the new 4-year $120M deal starts in 2038, split evenly
        st = state([player(50, "Tom", "Brady", 4, "QB")], contracts={50: contract(700, 4)})
        plan, rep = L.plan_effects(entries((W4, "SAINTS RE-SIGNED (QB) TOM BRADY TO 4 YEARS, $120M CONTRACT")), st, parse_money)
        self.assertEqual([o["op"] for o in ops(plan)], ["contract_extend"])
        ext = ops(plan, "contract_extend")[0]
        self.assertEqual((ext["player"], ext["years"], ext["value"]), (50, 4, 120_000_000))
        self.assertEqual(ext["hits"], {2038: 30_000_000, 2039: 30_000_000, 2040: 30_000_000, 2041: 30_000_000})
        self.assertEqual(ops(plan, "cap_delta"), [])           # this season's cap is unchanged
        self.assertEqual(plan[0]["txn"]["contract_value"], 120_000_000)

    def test_resign_uses_scanned_shares(self):
        shares = L.average_shares([(2, [4_000_000, 6_000_000])] * 10)
        st = state([player(50, "Tom", "Brady", 4, "QB")], contracts={50: contract(700, 4, years=(1, 0, 0, 0, 0), length=1)})
        st["shares"] = shares
        plan, _ = L.plan_effects(entries((W4, "SAINTS RE-SIGNED (QB) TOM BRADY TO 2 YEARS, $10M CONTRACT")), st, parse_money)
        self.assertEqual(ops(plan, "contract_extend")[0]["hits"], {2037: 4_000_000, 2038: 6_000_000})

class SharesTest(unittest.TestCase):
    def test_even_split_without_enough_scans(self):
        self.assertEqual(L.year_shares(3, {}), [1 / 3] * 3)
        few = L.average_shares([(3, [1, 1, 2])] * 9)
        self.assertEqual(L.year_shares(3, few), [1 / 3] * 3)

    def test_average_by_length(self):
        sh = L.average_shares([(2, [1, 3])] * 6 + [(2, [3, 1])] * 4 + [(3, [1, 1, 1, 0, 0])] * 12
                              + [(6, [1] * 5)] * 20 + [(2, [0, 0])])
        self.assertEqual(sh[2][1], 10)
        self.assertAlmostEqual(sh[2][0][0], (6 * 0.25 + 4 * 0.75) / 10)
        self.assertNotIn(6, sh)                     # longer than the 5 visible years: never sampled
        self.assertEqual(L.year_shares(2, sh), sh[2][0])

    def test_projected_hits_sum_to_value(self):
        hits = L.projected_hits(10_000_001, 3, 2037, {})
        self.assertEqual(sorted(hits), [2037, 2038, 2039])
        self.assertEqual(sum(hits.values()), 10_000_001)


class KeysAndCutoffTest(unittest.TestCase):
    ROWS = ((W4, "JAGUARS RELEASE (WR) JULIUS SLATON"), (W3, "JAGUARS RELEASE (K) X Y"))

    def test_rows_before_week_1_skipped(self):
        before = [0]
        es = L.expand([{"heading": W4, "text": "JAGUARS RELEASE (WR) JULIUS SLATON", "flags": []},
                       {"heading": PRE, "text": "JAGUARS RELEASE (WR) A B", "flags": []},
                       {"heading": "2035 OFFSEASON - FREE AGENCY", "text": "BEARS RELEASE (K) C D", "flags": []}],
                      None, None, None, "2036 SEASON - WEEK 1", before)
        self.assertEqual([e["heading"] for e in es], [W4])
        self.assertEqual(before, [2])
        es = L.expand([{"heading": "2036 OFFSEASON - FREE AGENCY", "text": "BEARS RELEASE (K) C D", "flags": []}],
                      None, None, None, "2036 SEASON - WEEK 1")
        self.assertEqual(len(es), 1)                  # the season's own offseason is kept

    def test_trades_skipped(self):
        trades = []
        es = L.expand([{"heading": W4, "text": "BEARS TRADED (WR) JOHN DOE TO PACKERS", "flags": []},
                       {"heading": W4, "text": "JAGUARS RELEASE (WR) JULIUS SLATON", "flags": []}], None, None, trades)
        self.assertEqual([e["leg"]["type"] for e in es], ["RELEASED"])
        self.assertEqual(trades, ["BEARS TRADED (WR) JOHN DOE TO PACKERS"])

    def test_non_moves_skipped(self):
        skipped = []
        es = L.expand([{"heading": W4, "text": "THE DRAFT CLASS HAS BEEN EDITED", "flags": []},
                       {"heading": W4, "text": "JAGUARS RELEASE (WR) JULIUS SLATON", "flags": []}], None, skipped)
        self.assertEqual([e["text"] for e in es], ["JAGUARS RELEASE (WR) JULIUS SLATON"])
        self.assertEqual(skipped, ["THE DRAFT CLASS HAS BEEN EDITED"])

    def test_loading_twice_gives_no_ops(self):
        st = state([player(30, "Julius", "Slaton", 1, "WR")])
        es = entries(*self.ROWS)
        plan, _ = L.plan_effects(es, st, parse_money)
        self.assertEqual(len(plan), 2)
        keys = {(e["txn"]["season"], e["txn"]["txn_date"], e["txn"]["row_key"], e["txn"]["seq"]) for e in plan}
        st2 = state([player(30, "Julius", "Slaton", 1, "WR")], existing=keys, counts={(2036, W4): 1, (2036, W3): 1})
        plan2, rep2 = L.plan_effects(entries(*self.ROWS), st2, parse_money)
        self.assertEqual(plan2, [])
        self.assertEqual(rep2["transactions_new"], 0)

    def test_before_apply_from_is_history(self):
        st = state([player(30, "Julius", "Slaton", 1, "WR")], apply_from="2036 SEASON - WEEK 1")
        plan, rep = L.plan_effects(entries((W4, "JAGUARS RELEASE (WR) JULIUS SLATON"), (PRE, "JAGUARS RELEASE (WR) JULIUS SLATON")),
                                   st, parse_money)
        pre = [e for e in plan if e["txn"]["txn_date"] == PRE][0]
        self.assertFalse(pre["txn"]["applied"])
        self.assertEqual(pre["ops"], [])
        self.assertEqual(rep["not_applied"], 1)

    def test_older_season_rows_are_history(self):
        plan, rep = L.plan_effects(entries(("2035 SEASON - WEEK 9", "JAGUARS RELEASE (WR) X Y")), state(), parse_money)
        self.assertEqual((plan[0]["txn"]["season"], plan[0]["txn"]["applied"], plan[0]["ops"]), (2035, False, []))

    def test_missing_rows_stop_the_load(self):
        st = state(counts={(2036, W4): 3})
        with self.assertRaises(L.TransactionsError):
            L.plan_effects(entries((W4, "JAGUARS RELEASE (WR) X Y")), st, parse_money)

    def test_future_year_stops(self):
        with self.assertRaises(L.TransactionsError):
            L.plan_effects(entries(("2037 SEASON - WEEK 1", "JAGUARS RELEASE (WR) X Y")), state(), parse_money)

    def test_partial_oldest_group_left_out(self):
        es = entries((W4, "JAGUARS RELEASE (WR) A B"), (W3, "JAGUARS RELEASE (WR) C D"),
                     meta={"since": W4, "reached_end": False})
        self.assertEqual([e["heading"] for e in es], [W4])
        es = entries((W4, "JAGUARS RELEASE (WR) A B"), (W3, "JAGUARS RELEASE (WR) C D"), meta={"since": W4, "reached_end": True})
        self.assertEqual(len(es), 2)


class ReadTest(unittest.TestCase):
    def test_read_rows_and_effective_week(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "transactions.csv"), "w") as fh:
                fh.write("ORDER,PAGE,HEADING,TEXT,FLAGS\n1,0,2036 SEASON - WEEKT,B,\n0,0,2036 SEASON - WEEK 2,A,gap?\n")
            rows = L.read_rows(d)
        self.assertEqual([(r["heading"], r["text"], r["flags"]) for r in rows],
                         [("2036 SEASON - WEEK 2", "A", ["gap?"]), ("2036 SEASON - WEEK 1", "B", [])])
        self.assertEqual(L.effective_week(PRE), 0)
        self.assertEqual(L.effective_week("2036 OFFSEASON - FREE AGENCY"), 24)
        self.assertEqual(L.effective_week("2036 POST SEASON - WILD CARD"), 19)


if __name__ == "__main__":
    unittest.main()


class RestructureTest(unittest.TestCase):
    def test_restructure_frees_this_season_and_spreads_the_rest(self):
        c = contract(800, 4, years=(10_000_000, 10_000_000, 10_000_000, 0, 0), length=3)
        st = state([player(60, "Ty", "Simpson", 4, "QB")], contracts={60: c})
        plan, _ = L.plan_effects(entries((W4, "SAINTS HAVE RESTRUCTURED THE CONTRACT OF (QB) TY SIMPSON GENERATING "
                                              "$3M IN ADDITIONAL CAP SPACE")), st, parse_money)
        self.assertEqual(plan[0]["txn"]["type"], "RESTRUCTURED")
        self.assertEqual(ops(plan, "contract_restructure")[0]["shift"], {2036: -3_000_000, 2037: 1_500_000, 2038: 1_500_000})
        self.assertEqual(ops(plan, "cap_delta"), [{"op": "cap_delta", "team_id": 4, "salaries": -3_000_000, "cap_penalty": 0}])


class FormerPlayerTest(unittest.TestCase):
    def test_release_finds_a_player_with_no_row_this_season(self):
        st = state([player(480, "Kurt", "Campanaro", None, "QB", this_season=False)])
        plan, rep = L.plan_effects(entries((W4, "BEARS RELEASE (QB) KURT CAMPANARO")), st, parse_money)
        self.assertEqual(plan[0]["txn"]["player"], 480)
        self.assertEqual(rep["created_players"], [])
        self.assertEqual(ops(plan, "release")[0]["team_id"], 2)          # his row this season goes to the Bears


class TradeSightingsTest(unittest.TestCase):
    """Trade rows are skipped as moves and become step 3's sightings, only when that table exists."""

    class Cur:
        def __init__(self, installed):
            self.installed, self.sql, self.rowcount = installed, [], 1

        def execute(self, q, args=None):
            self.sql.append(" ".join(q.split()))

        def fetchone(self):
            return (self.installed,)

        def fetchall(self):
            return []

    ROW = "PANTHERS TRADE (WR) ANN ABLE TO SAINTS FOR 2037 2ND ROUND PICK"

    def test_expand_collects_trade_rows_with_headings(self):
        trades, trade_rows = [], []
        out = L.expand([{"heading": W4, "text": self.ROW, "flags": []}], trades=trades, trade_rows=trade_rows)
        self.assertEqual(out, [])
        self.assertEqual(trades, [self.ROW])
        self.assertEqual(trade_rows, [{"heading": W4, "text": self.ROW}])

    def test_sightings_only_when_table_exists(self):
        cur = self.Cur(False)
        self.assertEqual(L.record_sightings(cur, 2036, [{"heading": W4, "text": self.ROW}]),
                         {"new": 0, "installed": False})
        self.assertFalse(any(q.startswith("INSERT") for q in cur.sql))
        cur = self.Cur(True)
        rep = L.record_sightings(cur, 2036, [{"heading": W4, "text": self.ROW}])
        self.assertEqual(rep["new"], 1)
        self.assertEqual(sum(q.startswith("INSERT INTO gm_trade_sightings") for q in cur.sql), 1)
        self.assertFalse(any("player_seasons" in q and q.startswith("UPDATE") for q in cur.sql))
        self.assertEqual(L.record_sightings(self.Cur(True), 2036, []), {"new": 0})
