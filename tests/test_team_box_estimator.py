"""engines/team_box_estimator.py: per-game team conversions, red zone and penalties from season totals.
No database required.

Run: python3 -m unittest tests.test_team_box_estimator
"""
import copy
import unittest

from engines.team_box_estimator import GAME_FIELDS, TOTAL_OF, apportion, check, estimate, score_split


def game_row(rid, week, team, opp, pts, opp_pts, **known):
    row = {"id": rid, "game_id": rid // 2, "week": week, "team": team, "opp": opp, "pts": pts, "opp_pts": opp_pts,
           "estimated": [], "exact": []}
    row.update({f: None for f in GAME_FIELDS})
    row.update(known)
    return row


def season(scores):
    """Two teams (1, 2) playing each other every week: [(pts1, pts2), ...]."""
    rows = []
    for w, (a, b) in enumerate(scores, 1):
        rows.append(game_row(4 * w, w, 1, 2, a, b))
        rows.append(game_row(4 * w + 1, w, 2, 1, b, a))
    return rows


TOTALS = {
    1: dict(third_down_conv=30, third_down_att=70, fourth_down_conv=3, fourth_down_att=7, two_point_conv=1,
            two_point_att=2, red_zone_att=18, red_zone_td=10, red_zone_fg=5, red_zone_def_att=14,
            red_zone_def_td=6, red_zone_def_fg=5, penalties=30, penalty_yards=260),
    2: dict(third_down_conv=25, third_down_att=68, fourth_down_conv=4, fourth_down_att=9, two_point_conv=0,
            two_point_att=1, red_zone_att=14, red_zone_td=6, red_zone_fg=5, red_zone_def_att=18,
            red_zone_def_td=10, red_zone_def_fg=5, penalties=35, penalty_yards=300),
}
SCORES = [(31, 17), (24, 20), (10, 13), (27, 6), (17, 21)]


class ApportionTests(unittest.TestCase):
    def test_sums_and_caps(self):
        vals, spilled = apportion(10, [1, 1, 1], [2, 2, None])
        self.assertEqual(vals, [2, 2, 6])
        self.assertEqual(spilled, 0)
        vals, spilled = apportion(7, [3, 1], [2, 2])
        self.assertEqual(sum(vals), 7)
        self.assertEqual(spilled, 3)

    def test_zero_weights_split_evenly(self):
        self.assertEqual(apportion(4, [0, 0])[0], [2, 2])


class ScoreSplitTests(unittest.TestCase):
    def test_common_scores(self):
        self.assertEqual(score_split(24, 0.6), (3, 1))
        self.assertEqual(score_split(17, 0.6), (2, 1))
        self.assertEqual(score_split(3, 0.6), (0, 1))
        self.assertEqual(score_split(0, 0.6), (0, 0))


class EstimateTests(unittest.TestCase):
    def test_backlog_matches_season_totals(self):
        rows = season(SCORES)
        self.assertEqual(estimate(rows, TOTALS), [])
        diffs, _ = check(rows, TOTALS)
        self.assertEqual(diffs, {})
        for r in rows:
            self.assertLessEqual(r["third_down_conversions"], r["third_down_attempts"])
            self.assertLessEqual(r["fourth_down_conversions"], r["fourth_down_attempts"])
            self.assertLessEqual(r["two_point_attempts"], score_split(r["pts"], 0.6)[0] + 1)
            self.assertLessEqual(r["red_zone_tds"] + r["red_zone_fgs"], r["red_zone_attempts"])
            self.assertIn("third_down_attempts", r["estimated"])
            self.assertEqual(r["exact"], [])   # a multi-game split is an estimate
        # a 6-point game can't hold a red-zone field goal plus a TD's worth of trips that scored
        six = next(r for r in rows if r["pts"] == 6)
        self.assertLessEqual(six["red_zone_tds"] * 6 + six["red_zone_fgs"] * 3, 6)

    def test_captured_values_are_kept_and_only_gaps_filled(self):
        rows = season(SCORES)
        last = [r for r in rows if r["week"] == 5]
        for r in last:   # a captured box score: conversions, red zone and penalty yards, no attempts
            r.update(third_down_conversions=4, fourth_down_conversions=1, two_point_conversions=0,
                     red_zone_tds=1, red_zone_fgs=1, red_zone_attempts=3, red_zone_pct=33.3, penalty_yards=40)
        estimate(rows, TOTALS)
        for r in last:
            self.assertEqual(r["third_down_conversions"], 4)
            self.assertEqual(r["penalty_yards"], 40)
            self.assertNotIn("third_down_conversions", r["estimated"])
            self.assertIn("third_down_attempts", r["estimated"])
            self.assertGreaterEqual(r["third_down_attempts"], 4)
        self.assertEqual(check(rows, TOTALS)[0], {})

    def test_new_week_gets_exactly_the_change(self):
        rows = season(SCORES)
        estimate(rows, TOTALS)
        frozen = copy.deepcopy(rows)
        new = season(SCORES + [(20, 14)])[-2:]
        totals = copy.deepcopy(TOTALS)
        totals[1].update(third_down_conv=36, third_down_att=83, penalties=36, penalty_yards=310,
                         red_zone_att=21, red_zone_td=12, red_zone_fg=6)
        rows = frozen + new
        estimate(rows, totals)
        self.assertEqual([r for r in rows if r["week"] < 6], frozen)   # stored weeks don't shift
        t1 = next(r for r in rows if r["week"] == 6 and r["team"] == 1)
        self.assertEqual((t1["third_down_conversions"], t1["third_down_attempts"]), (6, 13))
        self.assertEqual((t1["penalties"], t1["penalty_yards"]), (6, 50))
        self.assertEqual((t1["red_zone_tds"], t1["red_zone_fgs"], t1["red_zone_attempts"]), (2, 1, 3))
        self.assertEqual(sorted(t1["exact"]), sorted(t1["estimated"]))   # the week's change is exact
        self.assertIn("red_zone_pct", t1["exact"])


if __name__ == "__main__":
    unittest.main()
