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
    def _run(self, has_proposals, trades=True, waivers=True, week=5):
        calls = []
        waive = lambda cur, league, wk: calls.append(("waivers", league, wk)) or {"skipped": False, "transactions": [{}]}
        lineup = lambda cur, league, week: calls.append(("lineups", league, week)) or [{"to_ir": ["X"]}]
        propose = lambda league, week, conn=None: calls.append(("propose", league, week)) or [{}, {}]
        process = lambda league, week, conn=None: calls.append(("process", league, week)) or [{}]
        conn = _Conn(has_proposals)
        with mock.patch.object(NW, "optimize_league_lineups", lineup), \
             mock.patch.object(NW, "generate_trade_proposals", propose), \
             mock.patch.object(NW, "process_ai_to_ai_trades", process), \
             mock.patch.object(NW, "run_waivers", waive), \
             mock.patch.object(NW, "sync_playoffs", lambda cur, league: calls.append(("playoffs", league)) or {"status": "PROJECTED"}), \
             mock.patch.object(NW, "expire_pending_trades", lambda cur, league: calls.append(("expire", league)) or 3):
            summary = NW.run_league_week(conn, 15, week, trades=trades, waivers=waivers, log=lambda *_: None)
        return calls, summary, conn

    def test_order_lineups_waivers_trades_lineups_then_current_week(self):
        calls, summary, conn = self._run(has_proposals=False)
        self.assertEqual([c[0] for c in calls], ["playoffs", "lineups", "waivers", "propose", "process", "lineups"])
        self.assertEqual((summary["proposals"], summary["trades_processed"], summary["ir_moves"]), (2, 1, 1))
        self.assertEqual(summary["waiver_adds"], 1)
        self.assertIn("UPDATE napper_leagues SET current_week = %s WHERE id = %s", conn.sql[-1])

    def test_rerun_skips_proposals_already_made_for_the_week(self):
        calls, summary, _ = self._run(has_proposals=True)
        self.assertEqual([c[0] for c in calls], ["playoffs", "lineups", "waivers", "process", "lineups"])
        self.assertTrue(summary["trades_skipped"])

    def test_waivers_without_trades_still_rebuild_lineups(self):
        calls, _, _ = self._run(has_proposals=False, trades=False)
        self.assertEqual([c[0] for c in calls], ["playoffs", "lineups", "waivers", "lineups"])

    def test_lineups_only(self):
        calls, _, _ = self._run(has_proposals=False, trades=False, waivers=False)
        self.assertEqual([c[0] for c in calls], ["playoffs", "lineups"])

    def test_no_waiver_run_in_week_1(self):
        calls, _, _ = self._run(has_proposals=False, trades=False, week=1)
        self.assertEqual([c[0] for c in calls], ["playoffs", "lineups"])


class TradeDeadlineTest(unittest.TestCase):
    def test_week_11_still_trades(self):
        calls, summary, _ = RunLeagueWeekTest._run(RunLeagueWeekTest(), has_proposals=False, week=11)
        self.assertIn("propose", [c[0] for c in calls])
        self.assertFalse(summary["trades_closed"])

    def test_week_12_expires_offers_and_makes_no_trades(self):
        calls, summary, _ = RunLeagueWeekTest._run(RunLeagueWeekTest(), has_proposals=False, week=12)
        self.assertEqual([c[0] for c in calls], ["playoffs", "lineups", "waivers", "expire", "lineups"])
        self.assertEqual((summary["trades_closed"], summary["trades_expired"], summary["proposals"]), (True, 3, 0))

    def test_engine_refuses_after_the_deadline(self):
        from engines import napper_trade_engine as TE
        self.assertTrue(TE.trades_open(11))
        self.assertFalse(TE.trades_open(12))
        with mock.patch.object(TE, "get_connection", side_effect=AssertionError("no DB access expected")):
            self.assertEqual(TE.generate_trade_proposals(15, 12), [])
            self.assertEqual(TE.process_ai_to_ai_trades(15, 12), [])


if __name__ == "__main__":
    unittest.main()
