"""Tests for engines/report_card_engine.py (NFLPA_REPORT_CARD_PLAN.md §4, U1). Pure, no DB."""
import pytest

from engines.report_card_engine import (
    BASE_BUDGETS,
    CAPEX_CATEGORIES,
    CATEGORIES,
    FACILITY_WEIGHTS,
    GRADE_BANDS,
    GRADES,
    advance_upgrade,
    annual_cash_pool,
    apply_policy,
    apply_upgrade,
    base_budget,
    degrade,
    facility_score,
    grade_to_score,
    load_baseline,
    opex_cost,
    order_upgrade,
    score_to_grade,
    success_dividend,
    upgrade_cost,
    upgrade_target_grade,
)


# ----------------------------------------------------------------- grade scale ----
def test_grade_scale_has_fourteen_ordered_grades():
    assert len(GRADES) == 14
    assert GRADES[0] == "A+"
    assert GRADES[-1] == "F-"


def test_grade_scores_are_monotonic():
    scores = [grade_to_score(g) for g in GRADES]
    assert scores == sorted(scores, reverse=True)
    assert grade_to_score("A+") == 1.0
    assert grade_to_score("F-") == 0.0


def test_each_grade_sits_inside_its_band():
    for g in GRADES:
        lo, hi = GRADE_BANDS[g[0]]
        assert lo <= grade_to_score(g) <= hi, g


def test_score_to_grade_round_trips():
    for g in GRADES:
        assert score_to_grade(grade_to_score(g)) == g


def test_score_to_grade_clamps_and_extremes():
    assert score_to_grade(1.5) == "A+"
    assert score_to_grade(-0.5) == "F-"
    assert score_to_grade(0.90) == "A-"
    assert score_to_grade(0.60) == "C"


# ----------------------------------------------------------------- facility score ----
def test_weights_sum_to_one():
    assert sum(FACILITY_WEIGHTS.values()) == pytest.approx(1.0)


def test_facility_score_uniform_cards():
    assert facility_score({k: "A+" for k in CATEGORIES}) == pytest.approx(1.0)
    assert facility_score({k: "F-" for k in CATEGORIES}) == pytest.approx(0.0)


def test_facility_score_hand_computed():
    card = {"weight_room": "B", "training_room": "B", "locker_room": "B", "food_nutrition": "B",
            "team_travel": "B", "family_treatment": "B", "head_coach": "B", "ownership": "B"}
    # all B = 0.80, and the weights sum to 1.0, so the score is exactly 0.80.
    assert facility_score(card) == pytest.approx(0.80)


def test_baseline_loads_thirty_two_teams():
    baseline = load_baseline()
    assert len(baseline) == 32
    for abbr, card in baseline.items():
        assert set(CATEGORIES) <= set(card)
        for cat in CATEGORIES:
            assert card[cat] in GRADES
        assert card["wealth_band"] in BASE_BUDGETS
        assert card["summary_text"].strip()


def test_baseline_facility_scores_and_known_ordering():
    baseline = load_baseline()
    scores = {abbr: facility_score(card) for abbr, card in baseline.items()}
    for s in scores.values():
        assert 0.0 <= s <= 1.0
    # Known strong vs known weak franchises (real NFLPA 2024-2026 data).
    for strong in ("MIN", "MIA", "DAL", "LAR"):
        for weak in ("CIN", "AZ", "JAX", "CAR", "WAS"):
            assert scores[strong] > scores[weak], (strong, weak)


# ----------------------------------------------------------------- upgrades ----
def test_upgrade_costs():
    assert upgrade_cost("MINOR") == 5_000_000
    assert upgrade_cost("STANDARD") == 12_000_000
    assert upgrade_cost("OVERHAUL") == 25_000_000


def test_apply_upgrade_grade_effects():
    card = {"weight_room": "C-", "training_room": "C", "locker_room": "D", "food_nutrition": "F"}
    assert apply_upgrade(card, "weight_room", "MINOR")["weight_room"] == "C"          # +1 sub-tier
    assert apply_upgrade(card, "training_room", "STANDARD")["training_room"] == "B"    # +1 full tier
    assert apply_upgrade(card, "locker_room", "OVERHAUL")["locker_room"] == "A+"       # -> A+
    assert apply_upgrade(card, "food_nutrition", "OVERHAUL")["food_nutrition"] == "A+"


def test_apply_upgrade_does_not_mutate_input():
    card = {"weight_room": "C-"}
    out = apply_upgrade(card, "weight_room", "MINOR")
    assert card["weight_room"] == "C-"
    assert out["weight_room"] == "C"


def test_upgrade_target_grade_minor_is_clamped_at_a_plus():
    assert upgrade_target_grade("A", "MINOR") == "A+"
    assert upgrade_target_grade("A+", "MINOR") == "A+"


def test_order_and_advance_upgrade_state_machine():
    card = {"weight_room": "C"}
    order = order_upgrade(card, "weight_room", "STANDARD", season=2036, team_abbr="CAR")
    assert order["cost_dollars"] == 12_000_000
    assert order["target_grade"] == "B"
    assert order["completion_season"] == 2037
    assert order["status"] == "IN_PROGRESS"

    assert advance_upgrade(order, 2036)["status"] == "IN_PROGRESS"      # still under construction
    assert advance_upgrade(order, 2037)["status"] == "COMPLETED"        # completes at the target season
    assert advance_upgrade(order, 2040)["status"] == "COMPLETED"


def test_opex_costs_and_effects():
    assert opex_cost("luxury_travel") == 3_500_000
    assert opex_cost("family_daycare") == 1_000_000
    assert opex_cost("private_chef") == 2_000_000

    card = {"team_travel": "D", "family_treatment": "C", "food_nutrition": "D"}
    assert apply_policy(card, "luxury_travel")["team_travel"] == "A"
    assert apply_policy(card, "family_daycare")["family_treatment"] == "A"
    assert apply_policy(card, "private_chef")["food_nutrition"] == "A"


def test_apply_policy_never_lowers_a_grade():
    card = {"team_travel": "A+", "family_treatment": "A", "food_nutrition": "A+"}
    assert apply_policy(card, "luxury_travel")["team_travel"] == "A+"
    assert apply_policy(card, "family_daycare")["family_treatment"] == "A"
    assert apply_policy(card, "private_chef")["food_nutrition"] == "A+"


# ----------------------------------------------------------------- degradation ----
def test_degrade_drops_one_sub_tier_per_three_seasons():
    card = {"weight_room": "B", "training_room": "C+", "locker_room": "A", "food_nutrition": "F-",
            "head_coach": "A"}
    out = degrade(card, 3)
    assert out["weight_room"] == "B-"
    assert out["training_room"] == "C"
    assert out["locker_room"] == "A-"
    assert out["food_nutrition"] == "F-"      # floored
    assert out["head_coach"] == "A"           # culture categories do not degrade


def test_degrade_seasons_below_three_change_nothing():
    card = {"weight_room": "B"}
    assert degrade(card, 0) == card
    assert degrade(card, 2) == card


def test_degrade_scales_with_seasons():
    card = {"weight_room": "A"}
    assert degrade(card, 3)["weight_room"] == "A-"
    assert degrade(card, 6)["weight_room"] == "B+"


# ----------------------------------------------------------------- cash pool ----
def test_base_budgets():
    assert base_budget("TIER_1") == 45_000_000
    assert base_budget("TIER_2") == 33_000_000
    assert base_budget("TIER_3") == 22_000_000
    assert base_budget("TIER_4") == 13_000_000


def test_success_dividend_wins_and_playoffs():
    assert success_dividend(wins=8) == 0
    assert success_dividend(wins=12) == 1_200_000           # 4 wins above 8 x $300k
    assert success_dividend(wins=0, home_playoff_games=2) == 5_000_000
    assert success_dividend(wins=0, sb_appearance=True) == 2_500_000
    assert success_dividend(wins=0, sb_championship=True) == 5_000_000   # subsumes the appearance
    assert success_dividend(wins=0, top5_popularity=True) == 2_000_000


def test_success_dividend_is_hard_capped():
    div = success_dividend(wins=17, home_playoff_games=3, sb_championship=True, top5_popularity=True)
    # 9*300k + 3*2.5M + 5M + 2M = 17.2M uncapped; capped at +10M.
    assert div == 10_000_000


def test_annual_cash_pool_adds_base_and_dividend():
    assert annual_cash_pool("TIER_4", wins=12) == 13_000_000 + 1_200_000
