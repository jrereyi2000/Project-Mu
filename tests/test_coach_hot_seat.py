"""Unit tests for the coaching carousel: the coach hot seat (engines/coach_hot_seat.py: Black Monday and playoff
exits), coordinator firings and hiring picks (engines/coaching_carousel.py), and the offseason-manifest warnings
(newsroom/offseason_service.hot_seat_warnings). Pure: no database.

Run: python3 -m unittest tests.test_coach_hot_seat
"""
import random
import unittest

from engines.coach_hot_seat import MAX_EXIT_FIRES, MAX_FIRES, contender_seat, hot_seat
from engines.coaching_carousel import (APPEAL_WEIGHTS, coordinator_candidacy, coordinator_seat, opening_appeal,
                                      percentiles, pick)
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


class ContenderTests(unittest.TestCase):
    def row(self, **kw):
        r = coach(1, 12, 10.5, start_season=2030, made_playoffs=True, playoff_wins=0, champion=False, flops_before=1)
        r.update(kw)
        return r

    def test_one_and_done_contender_again(self):
        h = contender_seat([self.row()], SEASON)[1]
        self.assertEqual(h["inputs"]["terms"], {"no_playoff_win": 0.5, "contender": 0.5, "flops_before": 0.5,
                                                "shortfall": 0.0, "super_bowl": 0.0})
        self.assertEqual(h["inputs"]["p"], 0.35)

    def test_a_playoff_win_or_a_title_protects(self):
        self.assertEqual(contender_seat([self.row(playoff_wins=1)], SEASON)[1]["score"], 1.0)
        self.assertEqual(contender_seat([self.row(champion=True)], SEASON)[1]["inputs"]["safe"], "won the Super Bowl")
        self.assertEqual(contender_seat([self.row(super_bowl_win=SEASON - 1)], SEASON)[1]["score"], 0.5)

    def test_not_a_contender_and_short_tenure(self):
        self.assertEqual(contender_seat([self.row(wins=10, expected_wins=9.0, flops_before=0)], SEASON)[1]["score"], 0.5)
        self.assertEqual(contender_seat([self.row(start_season=SEASON - 1)], SEASON)[1]["inputs"]["safe"],
                         "tenure under 3 seasons")

    def test_cap_counts_earlier_exits(self):
        rows = [self.row(team_id=t, abbr=f"T{t}", flops_before=2) for t in range(40)]
        out = contender_seat(rows, SEASON, max_fires=0)
        self.assertFalse(any(v["fire"] for v in out.values()))
        self.assertLessEqual(sum(v["fire"] for v in contender_seat(rows, SEASON).values()), MAX_EXIT_FIRES)


class CoordinatorTests(unittest.TestCase):
    def row(self, **kw):
        r = {"team_id": 1, "abbr": "T1", "role": "DC", "coordinator_id": 9, "name": "DC Nine", "start_season": 2030,
             "unit_rank": 28, "talent_rank": 10, "unit_rank_before": 24, "hc_slant": "OFFENSIVE", "hc_score": 1.2,
             "playoff_wins": 0, "user_controlled": False}
        r.update(kw)
        return r

    def test_terms(self):
        t = coordinator_seat([self.row()], SEASON)[(1, "DC")]["inputs"]["terms"]
        self.assertEqual(t, {"gap": 1.8, "bottom_unit": 0.5, "bottom_again": 0.25, "scapegoat": 0.5,
                             "playoff_win": 0.0})

    def test_head_coach_on_that_side_takes_half_the_blame(self):
        t = coordinator_seat([self.row(hc_slant="DEFENSIVE")], SEASON)[(1, "DC")]["inputs"]["terms"]
        self.assertEqual(t["gap"], 0.9)

    def test_first_year_line_and_jr(self):
        r = self.row(start_season=SEASON, unit_rank=20, talent_rank=8, hc_score=0)     # 1.2 < 1.5 in year one
        self.assertEqual(coordinator_seat([r], SEASON)[(1, "DC")]["inputs"]["safe"], "below the line")
        r = self.row(user_controlled=True)
        self.assertEqual(coordinator_seat([r], SEASON)[(1, "DC")]["inputs"]["safe"], "JR's team")


class HiringTests(unittest.TestCase):
    def test_pick_is_seeded(self):
        cands = [{"key": "a", "score": 1.0}, {"key": "b", "score": 0.9}, {"key": "c", "score": -2.0}]
        self.assertEqual(pick(cands, "x")["key"], pick(cands, "x")["key"])
        picks = [pick(cands, f"k{i}")["key"] for i in range(400)]
        self.assertEqual(set(picks) & {"a", "b"}, {"a", "b"})             # noise: the close call goes both ways
        self.assertLess(picks.count("c"), 20)                             # a long shot almost never wins

    def test_candidacy(self):
        good = coordinator_candidacy(3, 15, playoff_wins=2, champion=True, years=5)
        bad = coordinator_candidacy(30, 5, years=1)
        self.assertGreater(good, 1.5)
        self.assertLess(bad, -1.5)
        self.assertEqual(coordinator_candidacy(None, None), 0.0)


class OpeningAppealTests(unittest.TestCase):
    def test_weights_and_missing_parts(self):
        self.assertAlmostEqual(sum(APPEAL_WEIGHTS.values()), 1.0)
        self.assertEqual(opening_appeal({}), 0.5)                         # nothing known: middling
        best = {k: 1.0 for k in APPEAL_WEIGHTS}
        self.assertEqual(opening_appeal(best), 1.0)

    def test_quarterback_matters_most_and_churn_hurts(self):
        qb_job = opening_appeal({"qb": 1.0, "cap": 0.0})
        cap_job = opening_appeal({"qb": 0.0, "cap": 1.0})
        self.assertGreater(qb_job, cap_job)
        self.assertAlmostEqual(opening_appeal({"churn": 2}), 0.4)

    def test_percentiles(self):
        self.assertEqual(percentiles({"a": 1, "b": 2, "c": 3, "d": None}), {"a": 0.0, "b": 0.5, "c": 1.0, "d": 0.5})
        self.assertEqual(percentiles({"a": 0, "b": 0}), {"a": 0.5, "b": 0.5})


class ManifestReconcileTests(unittest.TestCase):
    def test_before_the_hiring_cycle(self):
        firings = [{"team": "KC", "coach": "Ron Leonard"}]
        moves = [{"team": "CAR", "incoming_coach": "X"}, {"team": "DEN", "incoming_coach": "Y"}]
        w = hot_seat_warnings(firings, moves, hired=False, jr_teams={"CAR", "IND"})
        self.assertEqual(len(w), 2)
        self.assertTrue(w[0].startswith("KC: the hot seat fired Ron Leonard; Mu names his replacement"))
        self.assertTrue(w[1].startswith("DEN: Mu runs the coaching carousel for CPU teams"))

    def test_after_the_hiring_cycle(self):
        w = hot_seat_warnings([{"team": "KC", "coach": "Ron Leonard"}], [{"team": "IND"}], True, {"CAR", "IND"})
        self.assertEqual(w, [])


if __name__ == "__main__":
    unittest.main()
