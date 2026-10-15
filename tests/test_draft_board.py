"""draft_prospects / mock_draft_picks access (pipeline/draft_board.py) with a recording fake cursor."""
import unittest

from pipeline import draft_board as B
from pipeline.ingest_prospects import Prospect, height_inches, inches_height, load_prospects_from_csv


class Cur:
    """Records SQL; fetchall() hands out the queued result sets in order."""
    def __init__(self, *results):
        self.calls, self.results = [], list(results)

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))

    def executemany(self, sql, rows):
        self.calls.append((" ".join(sql.split()), list(rows)))

    def fetchall(self):
        return self.results.pop(0)


def prospect(pick, name, pos="CB", college="LSU", height="6'1\"", ovr=70, pid=None):
    return Prospect(projected_pick=pick, full_name=name, position=pos, college=college, height=height, weight=200,
                    age=22, overall_rating=ovr, projected_round="1", round_pick=pick, prospect_id=pid)


class HeightTest(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(height_inches("6'2\""), 74)
        self.assertEqual(inches_height(74), "6'2\"")
        self.assertIsNone(height_inches("5'71\""))           # impossible inches: left blank
        self.assertIsNone(height_inches(None))


class BoardTest(unittest.TestCase):
    def test_write_replaces_the_class(self):
        cur = Cur()
        n = B.write_board(cur, 2036, [prospect(1, "A"), prospect(2, "B", height=None, ovr=None)], "2026-10-15_full_board3")
        self.assertEqual(n, 2)
        (delete, params), (insert, rows) = cur.calls
        self.assertEqual((delete, params), ("DELETE FROM draft_prospects WHERE draft_season = %s", (2036,)))
        self.assertEqual(rows[0], (2036, 1, "1", 1, "A", "CB", "LSU", 73, 200, 22, 70, None, None, "2026-10-15_full_board3"))
        self.assertEqual((rows[1][7], rows[1][10]), (None, None))     # blanks stored as NULL

    def board_rows(self):
        return [(11, 1, "1", 1, "A", "LEDG", "LSU", 77, 250, 22, 75, None, None),
                (12, 2, "1", 2, "B", "QB", None, None, 220, 21, None, None, None),
                (13, 3, "1", 3, "C", "REDG", "USC", 76, 260, 22, 72, None, None)]

    def test_read_board(self):
        board = B.read_board(Cur(self.board_rows()), 2036)
        self.assertEqual([p.full_name for p in board], ["A", "B", "C"])
        self.assertEqual((board[0].prospect_id, board[0].height), (11, "6'5\""))
        self.assertEqual((board[1].college, board[1].height, board[1].overall_rating), (None, None, None))

    def test_read_board_position_group_and_limit(self):
        self.assertEqual([p.full_name for p in B.read_board(Cur(self.board_rows()), 2036, position="edge")], ["A", "C"])
        self.assertEqual([p.full_name for p in B.read_board(Cur(self.board_rows()), 2036, position="REDG")], ["C"])
        self.assertEqual(len(B.read_board(Cur(self.board_rows()), 2036, limit=2)), 2)

    def test_fixture_board_writes(self):
        board = load_prospects_from_csv("tests/fixtures/prospects_board.csv", strict=False)
        cur = Cur()
        self.assertEqual(B.write_board(cur, 2036, board), 39)


class RookieLinkTest(unittest.TestCase):
    def test_links_by_name_and_position_group(self):
        prospects = [(1, "DAVE VOGEL", "LEDG"), (2, "MIKE SMITH", "CB"), (3, "JOE BROWN", "QB"), (4, "JOE BROWN", "QB")]
        rookies = [(501, "Dave Vogel", "REDG"),           # same group (EDGE): linked
                   (502, "Mike Smith", "CB"), (503, "Mike Smith", "CB"),   # two rookies share the name: left alone
                   (504, "Joe Brown", "QB")]              # two prospects share the name: left alone
        cur = Cur(prospects, rookies)
        self.assertEqual(B.link_rookies(cur, 2036), {"linked": 1, "unlinked": 3})
        updates = [c for c in cur.calls if c[0].startswith("UPDATE")]
        self.assertEqual([u[1] for u in updates], [(501, 1)])
        self.assertIn("years_of_experience = 0", cur.calls[1][0])

    def test_nothing_to_link(self):
        self.assertEqual(B.link_rookies(Cur([]), 2036), {"linked": 0, "unlinked": 0})


class MockRunTest(unittest.TestCase):
    def test_save_replaces_the_week(self):
        cur = Cur()
        sel = [{"pick": 1, "team_id": 7, "locked": True, "selection_status": "Missed Playoffs", "fit_score": 61.5,
                "rationale": "need", "prospect": {"prospect_id": 11}}]
        self.assertEqual(B.save_mock(cur, 2035, 20, sel), 1)
        (delete, params), (_, rows) = cur.calls
        self.assertEqual(params, (2035, 20))
        self.assertEqual(rows, [(2035, 20, 1, 7, 11, True, "Missed Playoffs", 61.5, "need")])

    def test_read_mock(self):
        row = (1, 7, "Eagles", "PHI", True, "Missed Playoffs", 61.5, "need",
               11, 1, "1", 1, "DAVE VOGEL", "LEDG", "GEORGIA", 77, 280, 22, 75, None, None)
        out = B.read_mock(Cur([row]), 2035, 20)
        self.assertEqual((out[0]["team"], out[0]["locked"], out[0]["fit_score"]), ("Eagles", True, 61.5))
        self.assertEqual((out[0]["prospect"]["full_name"], out[0]["prospect"]["height"], out[0]["prospect"]["prospect_id"]),
                         ("DAVE VOGEL", "6'5\"", 11))


if __name__ == "__main__":
    unittest.main()
