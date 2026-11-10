"""Tests for engines/staff_contracts_engine.py (STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN.md §4, U1). Pure, no DB."""
import pytest

from engines.report_card_engine import annual_cash_pool, base_budget
from engines.staff_contracts_engine import (
    ACTIVE_FULL_CONTROL_CAP,
    STAFF_SALARY_SCALES,
    available_capex,
    available_capex_for,
    dead_buyouts,
    fire_contract,
    godfather_ceiling_ok,
    godfather_cooldown_ok,
    godfather_eligible,
    godfather_gate,
    is_expired,
    market_salary,
    offset_buyout,
    remaining_guaranteed,
    rollover,
    salary_tier,
    staff_payroll,
)


# ----------------------------------------------------------------------------- salary scales & tiering ----
def test_salary_scales_have_the_plan_bands():
    assert STAFF_SALARY_SCALES["HC"]["LEGENDARY"] == (16_000_000, 22_000_000)
    assert STAFF_SALARY_SCALES["HC"]["ESTABLISHED"] == (8_000_000, 14_000_000)
    assert STAFF_SALARY_SCALES["HC"]["PROMOTION"] == (4_500_000, 7_500_000)
    assert STAFF_SALARY_SCALES["GM"]["ELITE"] == (8_000_000, 12_000_000)
    assert STAFF_SALARY_SCALES["GM"]["STANDARD"] == (4_000_000, 7_500_000)
    assert STAFF_SALARY_SCALES["GM"]["PROMOTION"] == (2_000_000, 3_500_000)
    assert STAFF_SALARY_SCALES["COORD"]["TOP_TIER"] == (2_500_000, 4_000_000)
    assert STAFF_SALARY_SCALES["COORD"]["STANDARD"] == (1_500_000, 2_400_000)


def test_salary_tier_places_head_coaches_by_experience_and_championships():
    assert salary_tier("HC", experience=0, championships=0) == "PROMOTION"
    assert salary_tier("HC", experience=2, championships=0) == "PROMOTION"
    assert salary_tier("HC", experience=3, championships=0) == "ESTABLISHED"
    assert salary_tier("HC", experience=1, championships=1) == "LEGENDARY"
    assert salary_tier("HC", experience=5, championships=2) == "LEGENDARY"


def test_salary_tier_places_gms_by_championships_then_experience():
    for experience in (0, 1, 2):
        assert salary_tier("GM", experience=experience, championships=0) == "PROMOTION"
    for experience in (3, 4, 10):
        assert salary_tier("GM", experience=experience, championships=0) == "STANDARD"
    assert salary_tier("GM", experience=0, championships=1) == "ELITE"
    assert salary_tier("GM", experience=4, championships=1) == "ELITE"


def test_salary_tier_places_coordinators():
    assert salary_tier("OC", experience=0, championships=0) == "STANDARD"
    assert salary_tier("DC", experience=5, championships=0) == "TOP_TIER"
    assert salary_tier("OC", experience=0, championships=1) == "TOP_TIER"


def test_salary_tier_rejects_an_unknown_kind():
    with pytest.raises(KeyError):
        salary_tier("QB", experience=1)


def test_market_salary_is_the_band_midpoint_and_sits_inside_it():
    cases = [("HC", 0, 0, "PROMOTION"), ("HC", 3, 0, "ESTABLISHED"), ("HC", 0, 1, "LEGENDARY"),
             ("GM", 1, 0, "PROMOTION"), ("GM", 4, 0, "STANDARD"), ("GM", 6, 1, "ELITE"),
             ("OC", 0, 0, "STANDARD"), ("DC", 5, 0, "TOP_TIER")]
    for kind, exp, champs, band in cases:
        lo, hi = STAFF_SALARY_SCALES["COORD" if kind in ("OC", "DC") else kind][band]
        salary = market_salary(kind, exp, champs)
        assert salary == (lo + hi) // 2, (kind, band)
        assert lo <= salary <= hi, (kind, band)


def test_gm_market_salaries_are_the_three_tier_midpoints():
    assert market_salary("GM", experience=1, championships=0) == 2_750_000
    assert market_salary("GM", experience=4, championships=0) == 5_750_000
    assert market_salary("GM", experience=6, championships=1) == 10_000_000


# ------------------------------------------------------------------------------------ payroll & cash ----
def _staff(overrides=()):
    contracts = [
        {"person_kind": "HC", "annual_salary": 19_000_000, "remaining_years": 5},
        {"person_kind": "GM", "annual_salary": 10_000_000, "remaining_years": 4},
        {"person_kind": "OC", "annual_salary": 3_250_000, "remaining_years": 2},
        {"person_kind": "DC", "annual_salary": 1_950_000, "remaining_years": 3},
    ]
    for i, patch in overrides:
        contracts[i] = dict(contracts[i], **patch)
    return contracts


def test_staff_payroll_sums_the_four_staff_roles_only():
    contracts = _staff() + [{"person_kind": "QB", "annual_salary": 40_000_000}]
    assert staff_payroll(contracts) == 19_000_000 + 10_000_000 + 3_250_000 + 1_950_000


def test_dead_buyouts_sums_buyout_owed():
    contracts = [{"buyout_owed": 6_000_000}, {"buyout_owed": 0}, {"buyout_owed": 1_250_000}, {}]
    assert dead_buyouts(contracts) == 7_250_000


def test_available_capex_deducts_payroll_opex_and_buyouts_from_the_pool():
    pool = 45_000_000
    assert available_capex(pool, staff_payroll=5_000_000, opex=3_000_000, dead_buyouts=2_000_000) == 35_000_000


def test_available_capex_for_reuses_the_report_card_cash_pool():
    # TIER_1 base $45M + 2 wins over 8 ($600k) = $45.6M pool; a $10M payroll + $2M opex leaves $33.6M.
    pool = annual_cash_pool("TIER_1", wins=10)
    assert pool == 45_000_000 + 600_000
    assert available_capex_for("TIER_1", wins=10, staff_payroll=10_000_000, opex=2_000_000) == 33_600_000


def test_staff_payroll_deducts_from_a_tier_4_owner_cash_budget():
    # A cash-poor TIER_4 owner ($13M) paying a standard staff keeps only the remainder.
    payroll = market_salary("HC") + market_salary("GM") + market_salary("OC") + market_salary("DC")
    assert available_capex_for("TIER_4", staff_payroll=payroll) == base_budget("TIER_4") - payroll


# ----------------------------------------------------------------------------------- expiry & buyout ----
def test_rollover_decrements_remaining_years_each_season():
    contract = {"person_kind": "HC", "annual_salary": 6_000_000, "remaining_years": 4, "total_years": 5}
    assert rollover(contract)["remaining_years"] == 3
    assert rollover(rollover(rollover(rollover(contract))))["remaining_years"] == 0


def test_rollover_floors_at_zero_and_expires():
    contract = {"remaining_years": 1}
    out = rollover(contract)
    assert out["remaining_years"] == 0
    assert is_expired(out)
    assert not is_expired(contract)


def test_fire_contract_moves_remaining_guaranteed_into_buyout_owed():
    contract = {"annual_salary": 5_000_000, "remaining_years": 3, "buyout_owed": 0}
    out = fire_contract(contract)
    assert remaining_guaranteed(contract) == 15_000_000
    assert out["buyout_owed"] == 15_000_000
    assert out["remaining_years"] == 0


def test_fire_contract_adds_to_any_existing_buyout():
    contract = {"annual_salary": 5_000_000, "remaining_years": 3, "buyout_owed": 1_000_000}
    assert fire_contract(contract)["buyout_owed"] == 16_000_000


def test_offset_clause_reduces_the_owed_buyout_by_the_new_salary():
    fired = {"buyout_owed": 15_000_000}
    assert offset_buyout(fired, 8_000_000)["buyout_owed"] == 7_000_000
    # A new salary larger than the remaining debt zeroes it, never negative.
    assert offset_buyout(fired, 20_000_000)["buyout_owed"] == 0


# -------------------------------------------------------------------------------------- Godfather gate ----
def test_godfather_ceiling_and_cooldown():
    assert godfather_ceiling_ok(0) and godfather_ceiling_ok(1)
    assert not godfather_ceiling_ok(ACTIVE_FULL_CONTROL_CAP)
    assert godfather_cooldown_ok(2037, None)
    assert godfather_cooldown_ok(2037, 2032)          # exactly 5 seasons
    assert not godfather_cooldown_ok(2037, 2036)      # 1 season ago
    assert not godfather_cooldown_ok(2037, 2033)      # 4 seasons ago


def test_godfather_eligible_requires_both_gates():
    assert godfather_eligible(2037, active_full_control=1, last_godfather_season=None)
    assert not godfather_eligible(2037, active_full_control=2, last_godfather_season=None)
    assert not godfather_eligible(2037, active_full_control=0, last_godfather_season=2036)
    assert godfather_eligible(2037, active_full_control=0, last_godfather_season=2032)


class _FakeCursor:
    """A tiny fake cursor answering the two godfather_gate queries by their text."""

    def __init__(self, active_full_control, last_godfather_season):
        self._active = active_full_control
        self._last = last_godfather_season
        self.queries = []

    def execute(self, sql, *args):
        self.queries.append(sql)

    def fetchone(self):
        sql = self.queries[-1]
        return [self._active] if "staff_contracts" in sql else [self._last]


def test_godfather_gate_reads_both_ledgers():
    gate = godfather_gate(_FakeCursor(active_full_control=1, last_godfather_season=2030), 2037)
    assert gate["eligible"] is True
    assert gate["active_full_control"] == 1
    assert gate["last_godfather_season"] == 2030
    assert gate["ceiling_ok"] is True and gate["cooldown_ok"] is True

    gate = godfather_gate(_FakeCursor(active_full_control=2, last_godfather_season=2036), 2037)
    assert gate["eligible"] is False
    assert gate["ceiling_ok"] is False and gate["cooldown_ok"] is False
