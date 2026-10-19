"""pipeline/contracts.py: the SQL and parameters the contract writes send (fake cursor, no database)."""
import unittest

from pipeline import contracts as C


class Cur:
    def __init__(self):
        self.calls, self.rowcount, self.next_id = [], 3, 100

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        self.next_id += 1
        return (self.next_id,)


class ContractWritesTest(unittest.TestCase):
    def test_supersede_active(self):
        cur = Cur()
        self.assertEqual(C.supersede_active(cur, 2036, [5], [1, 2]), 3)
        sql, params = cur.calls[0]
        self.assertIn("SET status = 'SUPERSEDED'", sql)
        self.assertIn("status = 'ACTIVE'", sql)
        self.assertIn("team_id = ANY(%s) OR player_id = ANY(%s)", sql)
        self.assertEqual(params, (2036, [5], [1, 2]))

    def test_insert_contract_and_years(self):
        cur = Cur()
        cid = C.insert_contract(cur, player_id=1, team_id=5, season=2036, length=4, years_remaining=2,
                                total_value=38_000_000, bonus=24_600_000, cut_savings=0, cut_penalty=24_600_000,
                                no_trade_clause=False, incentive=None, years={2037: 0, 2036: 10_300_000},
                                capture_id=9)
        self.assertEqual(cid, 101)
        sql, params = cur.calls[0]
        self.assertIn("INSERT INTO player_contracts", sql)
        self.assertEqual(params, (1, 5, 2036, 4, 2, 38_000_000, 24_600_000, 0, 24_600_000, False, None, "SCAN", 9))
        self.assertEqual([c[1] for c in cur.calls[1:]], [(101, 2036, 10_300_000), (101, 2037, 0)])

    def test_transaction_source(self):
        cur = Cur()
        C.insert_contract(cur, player_id=1, team_id=6, season=2036, length=1, years_remaining=1, total_value=3_930_000,
                          bonus=0, cut_savings=0, cut_penalty=0, no_trade_clause=False, incentive=None,
                          years={2036: 3_930_000}, source="TRANSACTION")
        self.assertEqual(cur.calls[0][1][11], "TRANSACTION")

    def test_upsert_team_cap(self):
        cur = Cur()
        C.upsert_team_cap(cur, 5, 2036, {"cap": 305_200_000, "cap_space": -1_000_000}, 9)
        sql, params = cur.calls[0]
        self.assertIn("ON CONFLICT (team_id, season) DO UPDATE SET cap = EXCLUDED.cap, cap_space = EXCLUDED.cap_space", sql)
        self.assertEqual(params, (5, 2036, 305_200_000, -1_000_000, 9))
