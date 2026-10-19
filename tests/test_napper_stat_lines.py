"""server/napper_api.format_stat_line: post-game box score lines on Napper player cards (no database)."""
import unittest

from server.napper_api import format_stat_line


class FormatStatLineTest(unittest.TestCase):
    def test_qb_passing_then_rushing(self):
        st = {"completions": 12, "pass_attempts": 19, "pass_yards": 210, "pass_tds": 1, "interceptions": 1,
              "rush_attempts": 10, "rush_yards": 121, "rush_tds": 1}
        self.assertEqual(format_stat_line("QB", st),
                         "12/19 CMP, 210 YD, 1 TD, 1 INT, 10 CAR, 121 YD, 1 TD")

    def test_rb_rushing_then_receiving_with_fumble(self):
        st = {"rush_attempts": 13, "rush_yards": 39, "receptions": 3, "rec_yards": 21, "fumb": 1}
        self.assertEqual(format_stat_line("RB", st), "13 CAR, 39 YD, 3 REC, 21 YD, 1 FUM")

    def test_wr_receiving_first(self):
        st = {"receptions": 6, "rec_yards": 69, "rush_attempts": 1, "rush_yards": 8}
        self.assertEqual(format_stat_line("WR", st), "6 REC, 69 YD, 1 CAR, 8 YD")

    def test_no_stats(self):
        self.assertEqual(format_stat_line("TE", {}), "No stats")

    def test_kicker(self):
        st = {"fgm": 3, "fga": 4, "fg_long": 53, "xpm": 2, "xpa": 2}
        self.assertEqual(format_stat_line("K", st), "3/4 FG, LNG 53, 2/2 XP")
        self.assertEqual(format_stat_line("K", {}), "No attempts")

    def test_defense(self):
        st = {"sacks": 2.5, "ints": 1, "fumble_recoveries": 0, "tds": 1, "points_allowed": 17}
        self.assertEqual(format_stat_line("DEF", st), "2.5 SACK, 1 INT, 1 TD, 17 PA")


if __name__ == "__main__":
    unittest.main()
