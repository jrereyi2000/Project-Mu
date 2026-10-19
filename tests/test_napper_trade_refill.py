"""fill_empty_starter_slots: AI teams refill starting slots a trade emptied (no database needed)."""
import unittest

from engines.napper_trade_engine import fill_empty_starter_slots


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if sql.lstrip().startswith("UPDATE"):
            self.conn.updates.append(params)
            return
        self._rows = self.conn.results.pop(0)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, results):
        self.results = list(results)
        self.updates = []

    def cursor(self, row_factory=None):
        return _FakeCursor(self)


class _Outer:
    def __init__(self, conn):
        self.connection = conn


FMT = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 0, "K": 1, "DEF": 1, "BN": 6}


def _roster(week):
    # (week, player_id, slot, position, projected, ros) — one RB starter was traded away
    return [
        (week, 1, "QB", "QB", 20.0, 300.0),
        (week, 2, "RB", "HB", 14.0, 200.0),
        (week, 4, "WR", "WR", 13.0, 180.0),
        (week, 5, "WR", "WR", 11.0, 170.0),
        (week, 6, "TE", "TE", 8.0, 120.0),
        (week, 7, "FLEX", "WR", 9.0, 150.0),
        (week, 8, "K", "K", 8.0, 120.0),
        (week, 9, "DEF", "DEF", 6.0, 90.0),
        (week, 10, "BN", "HB", 7.0, 110.0),    # incoming RB from the trade
        (week, 11, "BN", "HB", 12.0, 160.0),   # best bench RB
        (week, 12, "BN", "QB", 18.0, 250.0),   # not RB-eligible
    ]


class FillEmptyStarterSlotsTest(unittest.TestCase):
    def test_ai_team_fills_vacated_slot_with_best_eligible_bench_player_each_week(self):
        conn = _FakeConn([[(True, 2036, FMT)], _roster(5) + _roster(6)])
        moves = fill_empty_starter_slots(_Outer(conn), league_id=15, team_id=3, from_week=5)
        self.assertEqual(moves, [(5, 11, "RB"), (6, 11, "RB")])
        self.assertEqual(conn.updates, [("RB", 15, 3, 5, 11), ("RB", 15, 3, 6, 11)])

    def test_human_team_is_never_changed(self):
        conn = _FakeConn([[(False, 2036, FMT)]])
        self.assertEqual(fill_empty_starter_slots(_Outer(conn), 15, 1, 5), [])
        self.assertEqual(conn.updates, [])

    def test_full_lineup_and_ineligible_bench_make_no_moves(self):
        full = _roster(5) + [(5, 13, "RB", "HB", 10.0, 100.0)]
        conn = _FakeConn([[(True, 2036, FMT)], full])
        self.assertEqual(fill_empty_starter_slots(_Outer(conn), 15, 3, 5), [])

        no_rb = [r for r in _roster(5) if r[1] not in (10, 11)]
        conn = _FakeConn([[(True, 2036, FMT)], no_rb])
        self.assertEqual(fill_empty_starter_slots(_Outer(conn), 15, 3, 5), [])

    def test_empty_flex_takes_best_rb_wr_te(self):
        rows = [r for r in _roster(5) if r[1] != 7] + [(5, 13, "RB", "HB", 10.0, 100.0)]
        conn = _FakeConn([[(True, 2036, '{"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}')], rows])
        self.assertEqual(fill_empty_starter_slots(_Outer(conn), 15, 3, 5), [(5, 11, "FLEX")])


if __name__ == "__main__":
    unittest.main()
