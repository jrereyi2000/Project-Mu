"""engines/napper_weekly.run_league_week: step order and once-per-week trade proposals (no database)."""
import unittest
from unittest import mock

from engines import napper_weekly as NW


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append(" ".join(sql.split()))
        self._row = (1,) if "FROM napper_trades" in sql and self.conn.has_proposals else None

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, has_proposals):
        self.has_proposals = has_proposals
        self.sql = []

    def cursor(self):
        return _Cursor(self)


class RunLeagueWeekTest(unittest.TestCase):
    def _run(self, has_proposals, trades=True):
        calls = []
        lineup = lambda cur, league, week: calls.append(("lineups", league, week)) or [{"to_ir": ["X"]}]
        propose = lambda league, week, conn=None: calls.append(("propose", league, week)) or [{}, {}]
        process = lambda league, week, conn=None: calls.append(("process", league, week)) or [{}]
        conn = _Conn(has_proposals)
        with mock.patch.object(NW, "optimize_league_lineups", lineup), \
             mock.patch.object(NW, "generate_trade_proposals", propose), \
             mock.patch.object(NW, "process_ai_to_ai_trades", process):
            summary = NW.run_league_week(conn, 15, 5, trades=trades, log=lambda *_: None)
        return calls, summary, conn

    def test_order_lineups_trades_lineups_then_current_week(self):
        calls, summary, conn = self._run(has_proposals=False)
        self.assertEqual([c[0] for c in calls], ["lineups", "propose", "process", "lineups"])
        self.assertEqual((summary["proposals"], summary["trades_processed"], summary["ir_moves"]), (2, 1, 1))
        self.assertIn("UPDATE napper_leagues SET current_week = %s WHERE id = %s", conn.sql[-1])

    def test_rerun_skips_proposals_already_made_for_the_week(self):
        calls, summary, _ = self._run(has_proposals=True)
        self.assertEqual([c[0] for c in calls], ["lineups", "process", "lineups"])
        self.assertTrue(summary["trades_skipped"])

    def test_lineups_only(self):
        calls, _, _ = self._run(has_proposals=False, trades=False)
        self.assertEqual([c[0] for c in calls], ["lineups"])


if __name__ == "__main__":
    unittest.main()
