"""engines/contracts_engine.py: CBA rules and cap maths on synthetic contracts (pure, no database)."""
import unittest

from engines import contracts_engine as E

M = 1_000_000


def deal(length=4, current_year=0, salary=None, bonus=None, **kw):
    c = {"length": length, "current_year": current_year,
         "salary_by_year": salary if salary is not None else [5 * M] * length,
         "bonus_by_year": bonus if bonus is not None else [0] * length,
         "guaranteed": 0, "contract_type": "STANDARD", "signed_season": 2036, "last_restructure_season": None,
         "drafted_round": None}
    c.update(kw)
    return c


class CapMathsTest(unittest.TestCase):
    def test_cap_hit_is_base_plus_proration_plus_ltbe(self):
        c = deal(salary=[10 * M, 11 * M, 12 * M, 13 * M], bonus=[2 * M] * 4, ltbe_by_year=[500_000, 0, 0, 0])
        self.assertEqual(E.cap_hit(c, 0), 12_500_000)
        self.assertEqual(E.cap_hit(c, 3), 15 * M)
        self.assertEqual(E.cap_hit(c, 4), 0)              # past the contract

    def test_proration_over_min_length_and_5(self):
        # restructuring year 0 of a 7-year deal spreads over 5 years, of a 3-year deal over 3
        long = E.restructure(deal(length=7, salary=[10 * M] * 7), 0, 5 * M)
        self.assertEqual(long["bonus_by_year"], [M] * 5 + [0, 0])
        short = E.restructure(deal(length=3, salary=[10 * M] * 3), 0, 6 * M)
        self.assertEqual(short["bonus_by_year"], [2 * M] * 3)

    def test_dead_money_pre_june1_accelerates_all_proration(self):
        c = deal(salary=[10 * M] * 4, bonus=[3 * M] * 4, current_year=1)
        self.assertEqual(E.dead_money(c, 1), 9 * M)        # years 1..3 of proration
        self.assertEqual(E.cut_savings(c, 1), 13 * M - 9 * M)

    def test_dead_money_post_june1_splits(self):
        c = deal(salary=[10 * M] * 4, bonus=[3 * M] * 4, current_year=1)
        self.assertEqual(E.dead_money(c, 1, post_june1=True), 3 * M)
        self.assertEqual(E._dead_split(c, 1, post_june1=True), (3 * M, 6 * M))
        self.assertEqual(E.cut_savings(c, 1, post_june1=True), 10 * M)

    def test_guaranteed_base_of_this_year_is_dead(self):
        c = deal(salary=[10 * M] * 4, bonus=[2 * M] * 4, guaranteed=4 * M)
        self.assertEqual(E.dead_money(c, 0), 8 * M + 4 * M)
        g = deal(salary=[10 * M] * 4, guaranteed_by_year=[10 * M, 0, 0, 0])
        self.assertEqual(E.dead_money(g, 0), 10 * M)
        self.assertEqual(E.dead_money(g, 1), 0)

    def test_negative_cut_savings(self):
        c = deal(salary=[1 * M] * 4, bonus=[5 * M] * 4)
        self.assertEqual(E.cut_savings(c, 0), 6 * M - 20 * M)
        self.assertLess(E.cut_savings(c, 0), 0)

    def test_matches_load_save_rule_without_guarantees(self):
        # pipeline/load_save.contract_values: penalty = remaining bonus, savings = hit - penalty
        c = E.from_snapshot({"length": 4, "current_year": 1, "salary_by_year": [1050, 1100, 1200, 1300],
                             "bonus_by_year": [327, 327, 327, 327]})
        self.assertEqual(E.dead_money(c, 1), 3 * 327 * 10_000)
        self.assertEqual(E.cut_savings(c, 1), (1100 + 327 - 3 * 327) * 10_000)


class ExtensionWindowTest(unittest.TestCase):
    def test_drafted_rookie_after_year_3(self):
        c = deal(contract_type="ROOKIE", drafted_round=2, signed_season=2036)
        p = {"rookie_season": 2036}
        self.assertFalse(E.extension_eligible(c, p, 2038, 18)[0])     # year 3 regular season
        self.assertTrue(E.extension_eligible(c, p, 2038, 19)[0])      # year 3 over
        self.assertTrue(E.extension_eligible(c, p, 2038, 25)[0])      # offseason entering year 4
        self.assertFalse(E.extension_eligible(c, p, 2037, 30)[0])

    def test_udfa_after_year_2(self):
        c = deal(length=3, contract_type="UDFA", signed_season=2036)
        self.assertFalse(E.extension_eligible(c, {}, 2037, 10)[0])
        self.assertTrue(E.extension_eligible(c, {}, 2037, 24)[0])

    def test_veteran_final_year(self):
        c = deal(length=3, current_year=2, signed_season=2036)
        ok, reason = E.extension_eligible(c, {}, 2038, 5)
        self.assertTrue(ok)
        self.assertIn("final", reason)

    def test_veteran_12_months_since_signing_or_restructure(self):
        c = deal(length=5, current_year=0, signed_season=2036, signed_week=26)
        self.assertFalse(E.extension_eligible(c, {}, 2036, 30)[0])
        self.assertFalse(E.extension_eligible(c, {}, 2037, 25)[0])
        self.assertTrue(E.extension_eligible(c, {}, 2037, 26)[0])
        r = dict(c, current_year=1, last_restructure_season=2037)
        self.assertFalse(E.extension_eligible(r, {}, 2037, 30)[0])
        self.assertTrue(E.extension_eligible(r, {}, 2038, 1)[0])

    def test_one_year_deal_from_week_18(self):
        c = deal(length=1, salary=[2 * M], bonus=[0])
        self.assertFalse(E.extension_eligible(c, {}, 2036, 17)[0])
        self.assertTrue(E.extension_eligible(c, {}, 2036, 18)[0])
        self.assertTrue(E.extension_eligible(c, {}, 2036, 25)[0])

    def test_tagged_until_mid_july(self):
        c = deal(length=1, salary=[30 * M], bonus=[0], contract_type="FRANCHISE_NONEXCL")
        self.assertTrue(E.extension_eligible(c, {}, 2036, E.OFFSEASON_STAGE_WEEKS["FREE_AGENCY"])[0])
        self.assertTrue(E.extension_eligible(c, {}, 2036, E.TAG_DEADLINE_WEEK)[0])
        self.assertFalse(E.extension_eligible(c, {}, 2036, E.OFFSEASON_STAGE_WEEKS["PRESEASON"])[0])
        self.assertFalse(E.extension_eligible(c, {}, 2037, 9)[0])
        self.assertTrue(E.extension_eligible(c, {}, 2037, 18)[0])


SALARIES = {"QB": [50 * M, 48 * M, 46 * M, 44 * M, 42 * M, 40 * M, 38 * M, 36 * M, 34 * M, 32 * M]
            + [30 * M - i * M for i in range(20)]}


class OptionTagTenderTest(unittest.TestCase):
    def avg(self, lo, hi):
        ranked = sorted(SALARIES["QB"], reverse=True)[lo - 1:hi]
        return int(round(sum(ranked) / len(ranked)))

    def test_option_tiers(self):
        base = {"position": "QB", "drafted_round": 1}
        self.assertEqual(E.fifth_year_option(dict(base, pro_bowls=2), SALARIES), {"tier": 1, "salary": self.avg(1, 5)})
        self.assertEqual(E.fifth_year_option(dict(base, pro_bowls=1), SALARIES), {"tier": 2, "salary": self.avg(1, 10)})
        self.assertEqual(E.fifth_year_option(dict(base, snap_shares=[0.8, 0.5, 0.9]), SALARIES),
                         {"tier": 3, "salary": self.avg(3, 20)})
        self.assertEqual(E.fifth_year_option(dict(base, snap_shares=[0.8, 0.5, 0.6]), SALARIES),
                         {"tier": 4, "salary": self.avg(3, 25)})

    def test_option_first_round_only(self):
        self.assertEqual(E.fifth_year_option({"position": "QB", "drafted_round": 2, "pro_bowls": 3}, SALARIES),
                         {"tier": None, "salary": 0})

    def test_exclusive_franchise_tag(self):
        self.assertEqual(E.tag_value("FRANCHISE_EXCL", "QB", SALARIES, 10 * M, [255 * M]), 46 * M)
        self.assertEqual(E.tag_value("FRANCHISE_EXCL", "QB", SALARIES, 45 * M, [255 * M]), 54 * M)   # 120% prior

    def test_nonexclusive_tag_uses_cap_share_history(self):
        history = {"QB": [[25 * M] * 5, SALARIES["QB"]]}          # last year's top 5 averaged 25M at a 200M cap
        caps = [200 * M, 250 * M]
        expected = int(round((25 / 200 + 46 / 250) / 2 * 250 * M))
        self.assertEqual(E.tag_value("FRANCHISE_NONEXCL", "QB", history, 0, caps), expected)
        self.assertEqual(E.tag_value("FRANCHISE_NONEXCL", "QB", SALARIES, 0, caps), 46 * M)   # one year only

    def test_transition_tag_top_10(self):
        self.assertEqual(E.tag_value("TRANSITION", "QB", SALARIES, 0, [255 * M]), 41 * M)
        with self.assertRaises(ValueError):
            E.tag_value("PLAIN", "QB", SALARIES, 0, [255 * M])

    def test_tenders(self):
        cap = E.CBA["REFERENCE_CAP"]
        self.assertEqual(E.tender_value("ERFA", 1, cap), 915_000)
        self.assertEqual(E.tender_value("ERFA", 2, 2 * cap), 2 * 985_000)
        self.assertEqual(E.tender_value("RFA_FIRST", 3, cap), 6_008_000)
        self.assertEqual(E.tender_value("RFA_SECOND", 3, cap), 4_218_000)
        self.assertEqual(E.tender_value("RFA_ROFR", 3, cap), 2_985_000)
        self.assertGreater(E.tender_value("RFA_FIRST", 3, cap), E.tender_value("RFA_SECOND", 3, cap))
        with self.assertRaises(ValueError):
            E.tender_value("ERFA", 3, cap)
        with self.assertRaises(ValueError):
            E.tender_value("RFA_FIRST", 4, cap)

    def test_minimum_by_accrued_seasons(self):
        self.assertEqual(E.minimum_salary(0), 795_000)
        self.assertEqual(E.minimum_salary(12), 1_210_000)


class RestructureTest(unittest.TestCase):
    def test_converts_base_to_prorated_bonus(self):
        c = deal(salary=[20 * M] * 4, bonus=[1 * M] * 4, signed_season=2036, current_year=1)
        r = E.restructure(c, 1, 9 * M)
        self.assertEqual(r["salary_by_year"], [20 * M, 11 * M, 20 * M, 20 * M])
        self.assertEqual(r["bonus_by_year"], [1 * M, 4 * M, 4 * M, 4 * M])
        self.assertEqual(E.cap_hit(r, 1), E.cap_hit(c, 1) - 6 * M)        # immediate room
        self.assertGreater(E.dead_money(r, 2), E.dead_money(c, 2))         # more future dead money
        self.assertEqual(r["last_restructure_season"], 2037)
        self.assertEqual(c["salary_by_year"][1], 20 * M)                   # input untouched

    def test_refuses_more_than_90_percent(self):
        c = deal(salary=[10 * M] * 4)
        E.restructure(c, 0, 9 * M)                                         # exactly 90% is fine
        with self.assertRaises(ValueError):
            E.restructure(c, 0, 9 * M + 1)
        with self.assertRaises(ValueError):
            E.restructure(c, 0, 0)
        with self.assertRaises(ValueError):
            E.restructure(c, 4, M)

    def test_remainder_keeps_total(self):
        r = E.restructure(deal(length=3, salary=[10 * M] * 3), 0, 1_000_001)
        self.assertEqual(sum(r["bonus_by_year"]), 1_000_001)

    def test_guarantee_drops_by_converted_part(self):
        r = E.restructure(deal(salary=[10 * M] * 4, guaranteed=10 * M), 0, 6 * M)
        self.assertEqual(r["guaranteed"], 4 * M)


class RookieScaleTest(unittest.TestCase):
    def test_monotone_by_pick(self):
        cap = E.CBA["REFERENCE_CAP"]
        totals = [sum(E.rookie_contract(p, 2036, cap)["salary_by_year"])
                  + sum(E.rookie_contract(p, 2036, cap)["bonus_by_year"]) for p in range(1, 225)]
        self.assertTrue(all(a >= b for a, b in zip(totals, totals[1:])))
        self.assertGreater(totals[0], totals[-1] * 5)
        scale = [E.CBA["ROOKIE_SCALE"][p] for p in range(1, 225)]
        self.assertTrue(all(a > b for a, b in zip(scale, scale[1:])))

    def test_shape_and_rounds(self):
        c = E.rookie_contract(1, 2036, E.CBA["REFERENCE_CAP"])
        self.assertEqual((c["length"], c["current_year"], c["contract_type"], c["drafted_round"], c["signed_season"]),
                         (4, 0, "ROOKIE", 1, 2036))
        self.assertEqual(c["guaranteed"], sum(c["salary_by_year"]))
        self.assertEqual(E.rookie_contract(33, 2036, E.CBA["REFERENCE_CAP"])["drafted_round"], 2)
        last = E.rookie_contract(224, 2036, E.CBA["REFERENCE_CAP"])
        self.assertEqual((last["drafted_round"], last["guaranteed"]), (7, 0))
        self.assertGreaterEqual(last["salary_by_year"][0], E.minimum_salary(0))
        with self.assertRaises(ValueError):
            E.rookie_contract(225, 2036, E.CBA["REFERENCE_CAP"])

    def test_scales_with_cap(self):
        a = E.rookie_contract(10, 2036, 200 * M)
        b = E.rookie_contract(10, 2036, 300 * M)
        self.assertAlmostEqual(sum(b["salary_by_year"]) / sum(a["salary_by_year"]), 1.5, places=3)


class CapGrowthAndUnitsTest(unittest.TestCase):
    def test_next_cap_seeded_reproducible(self):
        a = E.next_cap(255_400_000, 2037, "league-1")
        self.assertEqual(a, E.next_cap(255_400_000, 2037, "league-1"))
        self.assertEqual(a % E.CBA["CAP_ROUND"], 0)
        g = E.CBA["CAP_GROWTH"]
        self.assertTrue(255_400_000 * (1 + g["lo"]) - 1e5 <= a <= 255_400_000 * (1 + g["hi"]) + 1e5)
        paths = {E.next_cap(255_400_000, 2037, f"s{i}") for i in range(20)}
        self.assertGreater(len(paths), 1)

    def test_to_madden_from_snapshot_round_trip(self):
        snap = {"length": 4, "current_year": 1, "salary_by_year": [1050, 1100, 1200, 1300, 0, 0, 0, 0, 0, 0],
                "bonus_by_year": [327, 327, 327, 327, 0, 0, 0, 0, 0, 0]}
        c = E.from_snapshot(snap)
        self.assertEqual(c["salary_by_year"][0], 10_500_000)
        self.assertEqual(c["contract_type"], "STANDARD")
        m = E.to_madden(c)
        self.assertEqual((m["Length"], m["CurrentYear"]), (4, 1))
        self.assertEqual([m[f"SalaryYear{i}"] for i in range(10)], snap["salary_by_year"])
        self.assertEqual([m[f"BonusYear{i}"] for i in range(10)], snap["bonus_by_year"])

    def test_to_madden_rounds_to_nearest_unit(self):
        m = E.to_madden(deal(length=2, salary=[1_004_999, 1_005_000], bonus=[333_333, 0]))
        self.assertEqual((m["SalaryYear0"], m["SalaryYear1"], m["BonusYear0"], m["BonusYear1"]), (100, 101, 33, 0))


if __name__ == "__main__":
    unittest.main()
