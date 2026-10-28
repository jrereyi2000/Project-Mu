"""pipeline/teleport_contracts.py: Project Mu contracts -> PlayerContract fields, checked by converting back with
pipeline/load_save.contract_values (unit; the end-to-end write is in tests/test_teleport.py's integration test)."""
import unittest

from pipeline import teleport_contracts as tc
from pipeline.load_save import contract_values


def contract(**kw):
    base = {"season": 2036, "length": 4, "years_remaining": 3, "total_value": 17_600_000, "bonus": 8_720_000,
            "no_trade_clause": False}
    base.update(kw)
    return base


YEARS = {2036: 4_240_000, 2037: 4_500_000, 2038: 4_810_000, 2039: 0, 2040: 0}


def back(fields, season=2036):
    """PlayerContract fields as pc_save_reader.mjs reads them, through load_save.contract_values."""
    return contract_values({
        "length": fields["Length"], "current_year": fields["CurrentYear"],
        "salary_by_year": [fields[f"SalaryYear{i}"] for i in range(10)],
        "bonus_by_year": [fields[f"BonusYear{i}"] for i in range(7)],
        "no_trade_clause": fields["NoTradeClause"]}, season)


class ConvertTest(unittest.TestCase):
    def test_round_trip(self):
        fields, why = tc.madden_contract(contract(), YEARS)
        self.assertIsNone(why)
        self.assertEqual((fields["Length"], fields["CurrentYear"]), (4, 1))
        v = back(fields)
        self.assertEqual(v["years"], {2036: 4_240_000, 2037: 4_500_000, 2038: 4_810_000})
        self.assertEqual((v["length"], v["years_remaining"]), (4, 3))
        self.assertEqual(v["total_value"], 17_600_000)         # the played year takes what's left: 4.05M
        self.assertEqual(v["bonus"], 8_720_000)

    def test_bonus_is_prorated_evenly(self):
        fields, _ = tc.madden_contract(contract(), YEARS)
        self.assertEqual([fields[f"BonusYear{i}"] for i in range(4)], [218, 218, 218, 218])
        self.assertEqual(fields["SalaryYear1"], 424 - 218)
        v = back(fields)
        self.assertEqual(v["cut_penalty"], 3 * 2_180_000)      # bonus still to come

    def test_unused_slots_are_zeroed_and_extras_cleared(self):
        fields, _ = tc.madden_contract(contract(no_trade_clause=True), YEARS)
        self.assertTrue(all(fields[f"SalaryYear{i}"] == 0 for i in range(4, 10)))
        self.assertTrue(all(fields[f"BonusYear{i}"] == 0 for i in range(4, 7)))
        self.assertEqual(len([k for k in fields if k.startswith("SalaryYear")]), 10)
        self.assertEqual(len([k for k in fields if k.startswith("BonusYear")]), 7)
        self.assertTrue(fields["NoTradeClause"])
        self.assertEqual((fields["VoidYears"], fields["IsSalaryGuaranteedYears"], fields["TimesRestructured"],
                          fields["ExtraYearOption"], fields["SalaryDistribution"]), (0, 0, 0, False, "Custom"))

    def test_money_rounds_half_up_to_10k_units(self):
        self.assertEqual([tc.units(x) for x in (0, 4_999, 5_000, 1_234_567, 31_600_000)], [0, 0, 1, 123, 3160])
        fields, _ = tc.madden_contract(contract(length=1, years_remaining=1, bonus=0, total_value=1_234_567),
                                       {2036: 1_234_567})
        self.assertEqual(back(fields)["years"], {2036: 1_230_000})

    def test_a_small_year_passes_its_bonus_on(self):
        # Rookie-style deal: year 0 hit below the even share; the bonus total is kept.
        fields, _ = tc.madden_contract(contract(length=4, years_remaining=4, total_value=37_600_000,
                                                bonus=19_400_000),
                                       {2036: 2_000_000, 2037: 9_370_000, 2038: 11_900_000, 2039: 14_400_000})
        self.assertEqual([fields[f"BonusYear{i}"] for i in range(4)], [200, 580, 580, 580])
        self.assertEqual(fields["SalaryYear0"], 0)
        self.assertEqual(back(fields)["bonus"], 19_400_000)

    def test_prorate_bonus(self):
        self.assertEqual(tc.prorate_bonus(10, [5, 5, 5]), [4, 3, 3])
        self.assertEqual(tc.prorate_bonus(10, [1, 9, 9]), [1, 5, 4])
        self.assertEqual(tc.prorate_bonus(30, [5, 5, 5]), [5, 5, 5])          # can't hold it all
        self.assertEqual(tc.prorate_bonus(0, [5, 5]), [0, 0])
        self.assertEqual(tc.prorate_bonus(80, [10] * 8), [10] * 7 + [0])     # BonusYear0-6 only

    def test_years_past_the_salary_screen_repeat_the_last_shown(self):
        c = contract(length=7, years_remaining=6, total_value=259_000_000, bonus=143_000_000)
        hits = tc.cap_hits(c, {2036: 31_600_000, 2037: 38_700_000, 2038: 42_200_000, 2039: 49_300_000,
                               2040: 52_800_000})
        self.assertEqual(hits[1:], [31_600_000, 38_700_000, 42_200_000, 49_300_000, 52_800_000, 52_800_000])
        self.assertEqual(hits[0], 0)                       # the remaining hits already exceed the total

    def test_no_year_rows_spread_the_total(self):
        hits = tc.cap_hits(contract(length=2, years_remaining=2, total_value=10_000_000), {})
        self.assertEqual(hits, [5_000_000, 5_000_000])

    def test_years_add_moves_the_current_year(self):
        fields, _ = tc.madden_contract(contract(), YEARS, years_add=1)
        self.assertEqual(fields["CurrentYear"], 2)
        self.assertEqual(back(fields, 2037)["years"], {2037: 4_500_000, 2038: 4_810_000})

    def test_refusals(self):
        self.assertEqual(tc.madden_contract(contract(length=0, years_remaining=0), {})[1], "empty")
        self.assertEqual(tc.madden_contract(contract(length=1, years_remaining=1), {2036: 1}, years_add=1)[1],
                         "expired")
        self.assertEqual(tc.madden_contract(contract(length=11, years_remaining=11), {})[1], "too long")
        self.assertEqual(tc.madden_contract(contract(length=1, years_remaining=1, bonus=0), {2036: 200_000_000})[1],
                         "over the field maximum")


class AttachTest(unittest.TestCase):
    def test_add_contracts_counts_what_it_skips(self):
        edits = {"players": [{"pgid": 1, "set": {}}, {"pgid": 2, "set": {}}, {"pgid": 3, "set": {}}]}
        report = {"pairs": [{"donor_pgid": 1, "player_id": 10}, {"donor_pgid": 2, "player_id": 20},
                            {"donor_pgid": 3, "player_id": 30}]}
        snapshot = {"players": [{"pgid": 1, "contract": {"length": 1}}, {"pgid": 2, "contract": None},
                                {"pgid": 3, "contract": {"length": 1}}]}
        contracts = {10: (contract(), YEARS), 20: (contract(), YEARS)}         # 30 has none
        counts = tc.add_contracts(edits, report, contracts, snapshot)
        self.assertEqual(counts, {"written": 1, "donor_without_contract_record": 1, "no_project_mu_contract": 1,
                                  "donor_contract_kept": 2})
        self.assertIn("contract", edits["players"][0])
        self.assertNotIn("contract", edits["players"][1])


class LoadTest(unittest.TestCase):
    def test_load_contracts_groups_year_rows(self):
        class Cur:
            def execute(self, sql, args):
                self.args = args

            def fetchall(self):
                return [(7, 2036, 2, 2, 10, 0, False, 2036, 5), (7, 2036, 2, 2, 10, 0, False, 2037, 5),
                        (8, 2036, 1, 1, 3, None, True, None, None)]
        got = tc.load_contracts(Cur(), 2036)
        self.assertEqual(got[7][1], {2036: 5, 2037: 5})
        self.assertEqual(got[8], ({"season": 2036, "length": 1, "years_remaining": 1, "total_value": 3, "bonus": None,
                                   "no_trade_clause": True}, {}))


if __name__ == "__main__":
    unittest.main()
