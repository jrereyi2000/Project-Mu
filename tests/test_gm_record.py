"""Pure tests for GM records across tenures (engines/gm_record.py). No DB."""
import unittest

from engines.gm_record import tenure_facts, tenure_summary


def _season(season, w, l, t=0, place=2, result="MISSED"):
    return {"season": season, "wins": w, "losses": l, "ties": t, "division_place": place, "playoff_result": result}


def _tenure(end=None, reason=None, coach_id=None):
    return {"id": 1, "team_id": 14, "team": "Colts", "gm_name": "Russell Finch", "start_season": 2031,
            "end_season": end, "end_reason": reason, "coach_id": coach_id}


class TenureSummaryTest(unittest.TestCase):
    def test_totals_and_finishes(self):
        got = tenure_summary([_season(2032, 12, 5, place=1, result="WILD_CARD"),
                              _season(2031, 10, 7, result="CONFERENCE"), _season(2033, 5, 11, 1, place=4)])
        self.assertEqual(got["regular_season_record"], "27-23-1")
        self.assertEqual((got["winning_seasons"], got["division_titles"], got["playoff_trips"]), (2, 1, 2))
        self.assertEqual(got["best_finish"], "lost in the conference championship")
        self.assertEqual(got["by_season"][0], "2031: 10-7, lost in the conference championship")
        self.assertEqual(got["by_season"][2], "2033: 5-11-1, missed the playoffs")

    def test_titles_and_super_bowls(self):
        got = tenure_summary([_season(2034, 13, 4, result="CHAMPION"), _season(2035, 11, 6, result="SUPER_BOWL_LOSS")])
        self.assertEqual((got["titles"], got["super_bowl_appearances"]), ([2034], 2))
        self.assertEqual(got["best_finish"], "won the Super Bowl")

    def test_no_finished_season(self):
        self.assertIsNone(tenure_summary([]))


class TenureFactsTest(unittest.TestCase):
    def test_closed_tenure_says_how_it_ended(self):
        got = tenure_facts(_tenure(2035, "hot seat after 2035"), [_season(2035, 5, 12)], 2036)
        self.assertEqual((got["seasons"], got["current"], got["how_it_ended"]),
                         ("2031–2035", False, "hot seat after 2035"))

    def test_open_tenure_and_coach_gm(self):
        got = tenure_facts(_tenure(coach_id=7), [], 2036)
        self.assertEqual((got["seasons"], got["current"], got["record"]), ("2031–present", True,
                                                                          "no finished season yet"))
        self.assertEqual(got["role"], "head coach and general manager (full control)")
        self.assertNotIn("how_it_ended", got)

    def test_never_a_score(self):
        got = tenure_facts(_tenure(), [_season(2035, 9, 8)], 2036)
        self.assertFalse({"hot_seat_score", "fo_quality", "dev_quality", "q"} & set(got))


def test_writer_tool_is_registered():
    from newsroom import swarm_dispatcher as sd
    assert "get_gm_record" in dir(sd)


if __name__ == "__main__":
    unittest.main()
