"""Compensatory draft flow (docs/done/COMPENSATORY_PICKS_PLAN.md §4, U2): round renumbering with comp
picks, extended pick surplus values on the §2.3 decay curve, and rookie-scale contracts past the 224-pick
ceiling. Pure; no database."""
import math
import random

import pytest

from engines import contracts_engine as ce
from engines import trade_value as tv
from engines.trade_value import GENERIC_AGING, build_fits


# ------------------------------------------------------------- fixtures ----
def market_rows():
    rng = random.Random(1)
    rows = []
    for g, shift in (("QB", 0.5), ("WR", 0.2), ("CB", 0.1), ("RB", -0.3), ("K", -0.8)):
        n = 40 if g != "K" else 8
        for _ in range(n):
            o = rng.randint(65, 97)
            rows.append((g, o, math.exp(15 + shift + 0.09 * (o - 75) + rng.gauss(0, 0.2))))
    return rows


def draft_rows():
    rows = []
    for pick in range(1, 225):
        ovr = 78 - 4 * math.log(pick) + (1 if pick % 7 == 0 else 0)
        hit = 13_000_000 * pick ** -0.55 + 600_000
        rows.append((pick, ovr, hit))
    return rows


def aging_pairs():
    rng = random.Random(2)
    return [("WR", a, 80, 80 + GENERIC_AGING[min(5, max(0, (a - 21) // 3))] + rng.gauss(0, 1))
            for a in range(21, 36) for _ in range(20)]


FITS = build_fits(market_rows(), aging_pairs(), draft_rows())


# ------------------------------------------------- dynamic round boundaries (§2.5 / Q3) ----
def test_round_for_overall_pick_static():
    assert ce.round_for_overall_pick(1) == 1
    assert ce.round_for_overall_pick(32) == 1
    assert ce.round_for_overall_pick(33) == 2
    assert ce.round_for_overall_pick(96) == 3
    assert ce.round_for_overall_pick(97) == 4       # Round 4 starts at 97 in a plain 32-per-round draft
    assert ce.round_for_overall_pick(224) == 7
    assert ce.round_for_overall_pick(225) == 8
    assert ce.round_for_overall_pick(256) == 8


def test_round_for_overall_pick_shifts_with_comp_picks():
    schedule = [0, 0, 4, 0, 0, 0, 0]               # four Round-3 comp picks
    assert ce.round_for_overall_pick(96, schedule) == 3    # last standard Round-3 pick
    assert ce.round_for_overall_pick(97, schedule) == 3    # first Round-3 comp pick
    assert ce.round_for_overall_pick(100, schedule) == 3   # last Round-3 comp pick
    assert ce.round_for_overall_pick(101, schedule) == 4   # Round 4 now starts here
    # Round-7 comp picks pull overall picks 225+ back into round 7.
    r7 = [0, 0, 0, 0, 0, 0, 32]
    assert ce.round_for_overall_pick(225, r7) == 7
    assert ce.round_for_overall_pick(256, r7) == 7


def test_round_for_overall_pick_empty_schedule_is_static():
    assert ce.round_for_overall_pick(97, []) == ce.round_for_overall_pick(97) == 4
    assert ce.round_for_overall_pick(97, [0, 0, 0, 0, 0, 0, 0]) == 4


# ------------------------------------------------- comp pick values (§2.3) ----
def test_pick_curve_covers_comp_picks():
    curve = FITS["pick"]
    assert tv.DRAFT_PICKS == 256
    assert len(curve["ovr"]) == len(curve["hit"]) == len(curve["value"]) == 256
    assert all(b <= a + 1e-6 for a, b in zip(curve["value"], curve["value"][1:]))


def test_standard_pick_value_unchanged_by_comp_detection():
    # A plain pick still maps through (round-1)*32 + the projected slot (16 with no slots).
    pk = {"draft_season": 2037, "round": 3, "original_team_id": 5}
    assert tv.pick_value(pk, None, FITS, 2036) == round(tv.neutral_pick_value(80, FITS["pick"], FITS["market"]))
    # An empty pick_suffix is not a comp pick.
    pk2 = dict(pk, pick_suffix="")
    assert tv.pick_value(pk2, None, FITS, 2036) == tv.pick_value(pk, None, FITS, 2036)


def test_comp_pick_value_matches_exponential_decay():
    r = 3
    last = tv.neutral_pick_value(r * 32, FITS["pick"], FITS["market"])       # V(3, 32)
    v1 = tv.pick_value({"draft_season": 2037, "round": r, "original_team_id": 5, "pick_suffix": "1"},
                       None, FITS, 2036)
    assert v1 == round(last * math.exp(-tv.COMP_DECAY * 1))
    assert round(last * math.exp(-tv.COMP_DECAY * 1)) < round(last)


def test_comp_pick_value_decays_and_never_exceeds_last_standard_pick():
    ctx = {"direction": "REBUILD", "pick_affinity": 1.0, "slots": {5: 32}}
    r = 3
    standard = tv.pick_value({"draft_season": 2037, "round": r, "original_team_id": 5}, ctx, FITS, 2036)  # overall 96
    vals = [tv.pick_value({"draft_season": 2037, "round": r, "original_team_id": 5, "pick_suffix": str(s)},
                          ctx, FITS, 2036) for s in (1, 2, 3, 4)]
    assert vals[0] == round(standard * math.exp(-tv.COMP_DECAY * 1))
    assert all(b < a for a, b in zip(vals, vals[1:]))       # decays with the slot index
    assert all(v < standard for v in vals)                  # never exceeds the round's last standard pick
    assert standard > 0


def test_comp_slot_and_slot_in_round_also_detected():
    pk = {"draft_season": 2037, "round": 4, "original_team_id": 5}
    via_suffix = tv.pick_value(dict(pk, pick_suffix="2"), None, FITS, 2036)
    assert tv.pick_value(dict(pk, comp_slot=2), None, FITS, 2036) == via_suffix
    assert tv.pick_value(dict(pk, slot_in_round=2), None, FITS, 2036) == via_suffix


# ------------------------------------------------- rookie scale past 224 (§2.5) ----
def test_rookie_contract_extended_picks_225_through_256():
    cap = ce.CBA["REFERENCE_CAP"]
    totals = []
    for p in range(225, 257):
        c = ce.rookie_contract(p, 2036, cap)
        assert c["length"] == 4 and c["contract_type"] == "ROOKIE"
        assert c["drafted_round"] == 8 and c["guaranteed"] == 0
        total = sum(c["salary_by_year"]) + sum(c["bonus_by_year"])
        # The per-year minimum-salary floor can lift a late pick's total just above its raw scale.
        assert total >= ce._scale(ce.CBA["ROOKIE_SCALE"][p], cap)
        assert all(s >= ce.minimum_salary(i, cap) for i, s in enumerate(c["salary_by_year"]))
        totals.append(total)
    assert all(a > b for a, b in zip(totals, totals[1:]))  # still strictly decreasing past 224


def test_rookie_contract_uses_round_for_overall_pick_with_comp_schedule():
    cap = ce.CBA["REFERENCE_CAP"]
    schedule = [0, 0, 4, 0, 0, 0, 0]
    c = ce.rookie_contract(97, 2036, cap, comp_schedule=schedule)     # a Round-3 comp pick now
    assert c["length"] == 4 and c["contract_type"] == "ROOKIE"
    assert c["drafted_round"] == 3 and c["guaranteed"] == 0
    # The guaranteed-base round rules still hold under a comp schedule.
    r1 = ce.rookie_contract(1, 2036, cap, comp_schedule=schedule)
    assert r1["drafted_round"] == 1 and r1["guaranteed"] == sum(r1["salary_by_year"])
    r2 = ce.rookie_contract(33, 2036, cap, comp_schedule=schedule)
    assert r2["drafted_round"] == 2 and r2["guaranteed"] == r2["salary_by_year"][0]


def test_rookie_contract_still_rejects_out_of_range():
    with pytest.raises(ValueError):
        ce.rookie_contract(0, 2036, ce.CBA["REFERENCE_CAP"])
    with pytest.raises(ValueError):
        ce.rookie_contract(257, 2036, ce.CBA["REFERENCE_CAP"])
