"""engines/napper_injuries: injury status, IR eligibility and the countdown from the latest report (no DB)."""
import unittest

from engines import napper_injuries as NI


class _Cursor:
    def __init__(self, rows, calls):
        self.rows, self.calls = rows, calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def cursor(self, row_factory=None):
        return _Cursor(self.rows, self.calls)


class _Outer:
    def __init__(self, conn):
        self.connection = conn


class NapperInjuriesTest(unittest.TestCase):
    def test_status_and_ir_eligibility(self):
        self.assertIsNone(NI.injury_status(None))
        self.assertEqual(NI.injury_status({"is_ir": True, "weeks_remaining": 0}), "IR")
        self.assertEqual(NI.injury_status({"is_ir": False, "weeks_remaining": 1}), "O")
        self.assertIsNone(NI.injury_status({"is_ir": False, "weeks_remaining": 0}))
        self.assertTrue(NI.is_ir_eligible({"is_ir": True, "weeks_remaining": 0}))
        self.assertTrue(NI.is_ir_eligible({"is_ir": False, "weeks_remaining": NI.IR_MIN_WEEKS}))
        self.assertFalse(NI.is_ir_eligible({"is_ir": False, "weeks_remaining": 1}))
        self.assertFalse(NI.is_ir_eligible(None))

    def test_map_counts_down_from_latest_report_and_drops_healed_players(self):
        rows = [
            # player_id, reported week, type, duration, weeks_remaining, is_ir
            (186, 3, "UPPER ARM FRACTURE", "2 - 4 WEEKS", 1, False),   # out 1 more week as of week 3
            (200, 2, "TORN ACL", "SEASON", 0, True),                   # IR stays IR
            (300, 4, "HIGH ANKLE SPRAIN", "3 - 5 WEEKS", 3, False),    # reported this week
            (400, 1, "HAMSTRING", "1 - 2 WEEKS", 2, False),            # healed by week 4
        ]
        conn = _Conn(rows)
        out = NI.get_injury_map(_Outer(conn), 2036, 4)
        self.assertEqual(set(out), {200, 300})
        self.assertEqual(out[200]["status"], "IR")
        self.assertEqual(out[300]["status"], "O")
        self.assertEqual(out[300]["weeks_remaining"], 3)
        self.assertEqual(conn.calls[0][1], (2036, 4))

        out3 = NI.get_injury_map(_Outer(_Conn(rows[:1])), 2036, 3)
        self.assertEqual(out3[186]["weeks_remaining"], 1)
        self.assertEqual(out3[186]["injury_type"], "UPPER ARM FRACTURE")

    def test_player_filter(self):
        conn = _Conn([])
        self.assertEqual(NI.get_injury_map(_Outer(conn), 2036, 4, []), {})
        self.assertEqual(conn.calls, [])
        NI.get_injury_map(_Outer(conn), 2036, 4, [186])
        self.assertIn("player_id = ANY", conn.calls[0][0])
        self.assertEqual(conn.calls[0][1], (2036, 4, [186]))


class InjuryPayloadFieldsTest(unittest.TestCase):
    def test_injury_fields_for_injured_and_healthy_players(self):
        from server.napper_api import _injury_fields
        injuries = {186: {"status": "O", "injury_type": "UPPER ARM FRACTURE", "duration": "2 - 4 WEEKS",
                          "weeks_remaining": 2, "is_ir": False}}
        self.assertEqual(_injury_fields(injuries, 186), {
            "injury_status": "O", "injury_type": "UPPER ARM FRACTURE",
            "injury_duration": "2 - 4 WEEKS", "injury_weeks_remaining": 2,
        })
        self.assertEqual(set(_injury_fields(injuries, 1).values()), {None})


if __name__ == "__main__":
    unittest.main()
