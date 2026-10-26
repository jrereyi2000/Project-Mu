"""Unit tests for the coach hot seat (engines/coach_hot_seat.py) and its offseason-manifest reconciliation
(newsroom/offseason_service.hot_seat_warnings). Pure: no database.

Run: python3 -m unittest tests.test_coach_hot_seat
"""
import random
import unittest

from engines.coach_hot_seat import MAX_FIRES, hot_seat
from newsroom.offseason_service import hot_seat_warnings

SEASON = 2036


def coach(team_id, wins, expected, **kw):
    r = {"team_id": team_id, "abbr": f"T{team_id}", "coach_id": 100 + team_id, "coach_name": f"Coach {team_id}",
         "start_season": 2030, "wins": wins, "losses": 17 - wins, "expected_wins": expected, "wins_before": 9,
         "losses_before": 8, "made_playoffs": False, "playoffs_before": False, "super_bowl_win": None,
         "user_controlled": False, "coach_gm": False}
    r.update(kw)
    return r


class ScoreTests(unittest.TestCase):
    def test_terms(self):
        h = hot_seat([coach(1, 4, 8.0, wins_before=5, losses_before=12)], SEASON)[1]
        t = h["inputs"]["terms"]
        self.assertEqual((t["shortfall"], t["bad_record"], t["two_losing"]), (2.0, 0.5, 0.5))
        self.assertEqual(h["score"], 3.0)
        self.assertEqual(h["inputs"]["p"], 0.9)                     # capped at 0.9

    def test_protections(self):
        base = hot_seat([coach(1, 6, 9.0)], SEASON)[1]["score"]
        self.assertEqual(hot_seat([coach(1, 6, 9.0, playoffs_before=True)], SEASON)[1]["score"], base - 0.5)
        self.assertEqual(hot_seat([coach(1, 6, 9.0, super_bowl_win=SEASON - 2)], SEASON)[1]["score"], base - 1.0)
        self.assertEqual(hot_seat([coach(1, 6, 9.0, super_bowl_win=SEASON - 3)], SEASON)[1]["score"], base)

    def test_second_losing_season_needs_him_there_last_season(self):
        rows = [coach(1, 6, 7.0, start_season=SEASON, wins_before=3, losses_before=14)]
        self.assertEqual(hot_seat(rows, SEASON)[1]["inputs"]["terms"]["two_losing"], 0.0)

    def test_no_expected_wins_counts_no_shortfall(self):
        self.assertEqual(hot_seat([coach(1, 3, None)], SEASON)[1]["inputs"]["terms"]["shortfall"], 0.0)


class FiringTests(unittest.TestCase):
    def test_who_is_safe(self):
        cases = {"made the playoffs": {"made_playoffs": True}, "first season": {"start_season": SEASON},
                 "JR's team": {"user_controlled": True}, "also the GM": {"coach_gm": True}}
        for why, kw in cases.items():
            h = hot_seat([coach(1, 2, 10.0, **kw)], SEASON)[1]
            self.assertFalse(h["fire"])
            self.assertEqual(h["inputs"]["safe"], why)
        self.assertEqual(hot_seat([coach(1, 8, 9.0)], SEASON)[1]["inputs"]["safe"], "below the line")

    def test_seeded_and_capped_worst_first(self):
        ids, i = [], 0
        while len(ids) < MAX_FIRES + 2:
            if random.Random(f"coachseat:{SEASON}:T{i}").random() < 0.9:
                ids.append(i)
            i += 1
        rows = [coach(t, 2, 10.0 + n * 0.1) for n, t in enumerate(ids)]   # all score high, p = 0.9
        out = hot_seat(rows, SEASON)
        self.assertEqual(out, hot_seat(rows, SEASON))                     # deterministic
        fired = [t for t in ids if out[t]["fire"]]
        self.assertEqual(fired, ids[2:])                                  # the MAX_FIRES worst scores
        self.assertTrue(all(out[t]["inputs"].get("capped") for t in ids[:2]))


class ManifestReconcileTests(unittest.TestCase):
    def test_warnings(self):
        firings = [{"team": "ATL", "coach": "Kevin Stefanski"}, {"team": "KC", "coach": "Ron Leonard"}]
        moves = [{"team": "ATL", "incoming_coach": "X"}, {"team": "DEN", "incoming_coach": "Y"}]
        w = hot_seat_warnings(firings, moves)
        self.assertEqual(len(w), 2)
        self.assertTrue(w[0].startswith("KC: the hot seat fired Ron Leonard"))
        self.assertTrue(w[1].startswith("DEN: a head-coach hire the hot seat didn't fire for"))
        self.assertEqual(hot_seat_warnings([], []), [])


if __name__ == "__main__":
    unittest.main()
