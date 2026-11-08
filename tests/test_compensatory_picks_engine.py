"""engines/compensatory_picks_engine.py: CBA comp-pick formula, cancellation, allocation and JC-2A (pure, no DB)."""
import datetime
import unittest

from engines import compensatory_picks_engine as E

# 20 distinct league APYs (1..20) give clean percentile boundaries with the standard percentile-rank formula:
#   apy 20 -> 0.95 (R3), 19 -> 0.90 (R4), 18 -> 0.85 (R5), 17 -> 0.80 (R6), 16 -> 0.75 (R6),
#   apy 15 -> 0.70 (R7), 14 -> 0.65 (R7), 13 -> 0.60 (non-qualifier).
LEAGUE_APYS = list(range(1, 21))

DRAFT_DEADLINE = datetime.date(2026, 5, 4)   # the Monday following the 2026 NFL Draft


def qfa(pid, team_id, apy, snap_pct=0.0, *, signed_date=None, waived=False, cut=False, is_unrestricted=True,
        contract_expired=True, all_pro_first=False, pro_bowl=False):
    return {"player_id": pid, "team_id": team_id, "apy": apy, "snap_pct": snap_pct,
            "all_pro_first": all_pro_first, "pro_bowl": pro_bowl, "waived": waived, "cut": cut,
            "is_unrestricted": is_unrestricted, "contract_expired": contract_expired,
            "signed_date": signed_date}


class QfaQualificationTest(unittest.TestCase):
    def test_cut_player_is_rejected(self):
        self.assertFalse(E.qfa_eligible(qfa(1, 1, 20, waived=True), DRAFT_DEADLINE))
        self.assertFalse(E.qfa_eligible(qfa(1, 1, 20, cut=True), DRAFT_DEADLINE))

    def test_restricted_or_non_expired_player_is_rejected(self):
        self.assertFalse(E.qfa_eligible(qfa(1, 1, 20, is_unrestricted=False), DRAFT_DEADLINE))
        self.assertFalse(E.qfa_eligible(qfa(1, 1, 20, contract_expired=False), DRAFT_DEADLINE))

    def test_ufa_signed_before_the_post_draft_deadline_is_accepted(self):
        self.assertTrue(E.qfa_eligible(qfa(1, 1, 20, signed_date=DRAFT_DEADLINE - datetime.timedelta(days=1)),
                                       DRAFT_DEADLINE))

    def test_ufa_signed_on_or_after_the_deadline_is_rejected(self):
        self.assertFalse(E.qfa_eligible(qfa(1, 1, 20, signed_date=DRAFT_DEADLINE), DRAFT_DEADLINE))
        self.assertFalse(E.qfa_eligible(qfa(1, 1, 20, signed_date=DRAFT_DEADLINE + datetime.timedelta(days=1)),
                                        DRAFT_DEADLINE))

    def test_missing_signing_date_is_rejected_when_a_deadline_is_given(self):
        self.assertFalse(E.qfa_eligible(qfa(1, 1, 20), DRAFT_DEADLINE))

    def test_no_deadline_skips_the_signing_date_check(self):
        self.assertTrue(E.qfa_eligible(qfa(1, 1, 20)))


class ApyPercentileTest(unittest.TestCase):
    def test_tier_boundaries(self):
        self.assertEqual(E.apy_tier(20, LEAGUE_APYS), (3, 100))
        self.assertEqual(E.apy_tier(19, LEAGUE_APYS), (4, 80))
        self.assertEqual(E.apy_tier(18, LEAGUE_APYS), (5, 60))
        self.assertEqual(E.apy_tier(17, LEAGUE_APYS), (6, 40))
        self.assertEqual(E.apy_tier(16, LEAGUE_APYS), (6, 40))
        self.assertEqual(E.apy_tier(15, LEAGUE_APYS), (7, 20))
        self.assertEqual(E.apy_tier(14, LEAGUE_APYS), (7, 20))
        self.assertEqual(E.apy_tier(13, LEAGUE_APYS), (None, None))

    def test_percentile_rank_ties_are_averaged(self):
        # a value equal to the only pool element ranks 0.5 (not 0, not 1)
        self.assertEqual(E.percentile_rank(10, [10]), 0.5)
        # ties share the midpoint: below 0, equal 2 -> (0 + 0.5*2)/2 = 0.5
        self.assertAlmostEqual(E.percentile_rank(10, [10, 10]), 0.5)

    def test_empty_pool_ranks_zero(self):
        self.assertEqual(E.percentile_rank(5, []), 0.0)


class FormulaScoreTest(unittest.TestCase):
    def test_snap_bonus_steps(self):
        self.assertEqual(E.snap_bonus(0.0), 0)
        self.assertEqual(E.snap_bonus(0.49), 0)
        self.assertEqual(E.snap_bonus(0.50), 5)
        self.assertEqual(E.snap_bonus(0.74), 5)
        self.assertEqual(E.snap_bonus(0.75), 10)
        self.assertEqual(E.snap_bonus(1.0), 10)

    def test_honor_points(self):
        self.assertEqual(E.honor_points(), 0)
        self.assertEqual(E.honor_points(pro_bowl=True), 10)
        self.assertEqual(E.honor_points(all_pro_first=True), 20)
        self.assertEqual(E.honor_points(all_pro_first=True, pro_bowl=True), 30)

    def test_formula_score_combines_apy_snap_and_honor(self):
        self.assertEqual(E.formula_score(100, 0.75, all_pro_first=True, pro_bowl=True), 140)
        self.assertEqual(E.formula_score(80, 0.50, pro_bowl=True), 95)

    def test_evaluate_qfa_returns_a_full_valuation(self):
        v = E.evaluate_qfa(qfa(1, 1, 20, snap_pct=0.75, all_pro_first=True, pro_bowl=True), LEAGUE_APYS)
        self.assertEqual(v["round_tier"], 3)
        self.assertEqual(v["apy_value"], 100)
        self.assertEqual(v["snap_bonus"], 10)
        self.assertEqual(v["honor"], 30)
        self.assertEqual(v["score"], 140)
        self.assertTrue(v["qualifies"])

    def test_evaluate_qfa_below_65th_percentile_is_a_non_qualifier(self):
        v = E.evaluate_qfa(qfa(1, 1, 13), LEAGUE_APYS)
        self.assertIsNone(v["round_tier"])
        self.assertFalse(v["qualifies"])
        self.assertEqual(v["score"], 0)


class CancellationTest(unittest.TestCase):
    def _t(self, tier, score=0):
        return {"round_tier": tier, "score": score}

    def test_equal_tier_pairs_cancel(self):
        # lost R3 + R5, gained R3 + R7 -> net zero
        self.assertEqual(E.cancel_qfas([self._t(3), self._t(5)], [self._t(3), self._t(7)]), [])

    def test_same_tier_matches_before_nearest(self):
        # lost R3 + R7, gained R7 -> the R7/R7 pair cancels, leaving the R3 (not the R7)
        self.assertEqual([d["round_tier"] for d in E.cancel_qfas([self._t(3), self._t(7)], [self._t(7)])], [3])

    def test_more_lost_than_gained_leaves_the_lowest_value_survivors(self):
        # lost R3 + R5, gained R3 -> R5 survives
        self.assertEqual([d["round_tier"] for d in E.cancel_qfas([self._t(3), self._t(5)], [self._t(3)])], [5])

    def test_unmatched_lost_yields_a_pick(self):
        self.assertEqual([d["round_tier"] for d in E.cancel_qfas([self._t(3)], [])], [3])

    def test_gained_without_lost_yields_nothing(self):
        self.assertEqual(E.cancel_qfas([], [self._t(3)]), [])

    def test_nearest_tier_cancels_when_no_same_tier_exists(self):
        # lost R3 + R6, gained R5 -> R5 cancels the R6 (nearest), leaving the R3
        self.assertEqual([d["round_tier"] for d in E.cancel_qfas([self._t(3), self._t(6)], [self._t(5)])], [3])


class AllocationTest(unittest.TestCase):
    def _unc(self, team_id, tier, score, pid):
        return {"team_id": team_id, "round_tier": tier, "score": score, "player_id": pid}

    def test_franchise_cap_of_four(self):
        uncancelled = [self._unc(1, 3, 100 - i, i) for i in range(6)]
        awarded = E.allocate_comp_picks(uncancelled)
        self.assertEqual(len(awarded), 4)
        self.assertTrue(all(p["team_id"] == 1 for p in awarded))

    def test_league_cap_of_thirty_two(self):
        uncancelled = [self._unc(i, 3, 1000 - i, i) for i in range(40)]
        awarded = E.allocate_comp_picks(uncancelled)
        self.assertEqual(len(awarded), 32)

    def test_best_score_wins_within_the_cap(self):
        uncancelled = [self._unc(i, 3, i, i) for i in range(5)]
        awarded = E.allocate_comp_picks(uncancelled, league_cap=3)
        self.assertEqual([p["score"] for p in awarded], [4, 3, 2])


class Jc2aTest(unittest.TestCase):
    def _dep(self, **kw):
        d = {"team_id": 1, "is_minority": True, "hire_from_role": "OC", "hire_to_role": "HC",
             "tenure_years": 3, "hired_away": True}
        d.update(kw)
        return d

    def test_minority_oc_to_hc_with_two_plus_years_triggers(self):
        self.assertTrue(E.jc2a_eligible(self._dep()))
        self.assertTrue(E.jc2a_eligible(self._dep(tenure_years=2)))

    def test_non_minority_short_tenure_or_no_promotion_does_not_trigger(self):
        self.assertFalse(E.jc2a_eligible(self._dep(is_minority=False)))
        self.assertFalse(E.jc2a_eligible(self._dep(tenure_years=1)))
        self.assertFalse(E.jc2a_eligible(self._dep(hired_away=False)))
        self.assertFalse(E.jc2a_eligible(self._dep(hire_from_role="HC")))
        self.assertFalse(E.jc2a_eligible(self._dep(hire_to_role="OC")))
        self.assertFalse(E.jc2a_eligible(self._dep(hire_from_role="GM")))

    def test_two_round_three_picks_across_consecutive_drafts(self):
        awards = E.jc2a_awards([self._dep()], draft_season=2026)
        self.assertEqual(len(awards), 2)
        self.assertEqual([a["draft_season"] for a in awards], [2027, 2028])
        self.assertTrue(all(a["round"] == 3 and a["award_kind"] == "JC2A" for a in awards))

    def test_jc2a_picks_do_not_count_against_the_cba_caps(self):
        result = E.calculate_comp_picks([], [], LEAGUE_APYS, jc2a_departures=[self._dep()], draft_season=2026,
                                        jc2a_enabled=True)
        self.assertEqual(len(result["cba_picks"]), 0)
        self.assertEqual(len(result["jc2a_picks"]), 2)
        self.assertEqual(result["total"], 2)

    def test_jc2a_is_off_by_default(self):
        # JR 2026-11-08: the rule stays available but nothing is awarded unless switched on.
        self.assertFalse(E.JC2A_ENABLED)
        result = E.calculate_comp_picks([], [], LEAGUE_APYS, jc2a_departures=[self._dep()], draft_season=2026)
        self.assertEqual(result["jc2a_picks"], [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(E.jc2a_awards([self._dep()], 2026)[0]["award_kind"], "JC2A")   # the rule itself still works


class FullCalculationTest(unittest.TestCase):
    def test_end_to_end_evaluates_cancels_and_allocates(self):
        # Team 1 loses two qualifying UFAs (R3 and R7) and gains one (R3): the R3s cancel, the R7 survives.
        before = DRAFT_DEADLINE - datetime.timedelta(days=1)
        lost = [qfa(1, 1, 20, signed_date=before), qfa(2, 1, 14, signed_date=before)]
        gained = [qfa(3, 1, 20, signed_date=before)]
        result = E.calculate_comp_picks(lost, gained, LEAGUE_APYS, post_draft_deadline=DRAFT_DEADLINE)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["cba_picks"][0]["round_tier"], 7)

    def test_cut_and_non_qualifying_players_are_dropped(self):
        # a cut player never counts; an APY below the 65th percentile never qualifies
        lost = [qfa(1, 1, 20, waived=True), qfa(2, 1, 13)]
        result = E.calculate_comp_picks(lost, [], LEAGUE_APYS)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["cba_picks"], [])

    def test_missing_draft_season_for_jc2a_is_rejected(self):
        with self.assertRaises(ValueError):
            E.calculate_comp_picks([], [], LEAGUE_APYS, jc2a_departures=[{"team_id": 1}], draft_season=None,
                                   jc2a_enabled=True)


if __name__ == "__main__":
    unittest.main()
