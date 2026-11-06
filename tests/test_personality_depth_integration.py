"""Depth-chart opportunity seam (PERSONALITY_DEVELOPMENT_COACHING_PLAN U4): the real staff algorithm,
synthetic players, real coaching policy output. No DB."""
import pytest

from engines import depth_chart_engine as dce
from engines import personality_coaching as pc
from engines import personality_development as pd
from tests.test_depth_chart_engine import SCHEME, by_id, make_roster


def coach(traits=("YOUTH_DEVELOPER",)):
    return {"status": "stored", "traits": list(traits), "sliders": {}, "persona_id": "hc"}


def cfg(mode="enabled"):
    return pc.CoachingConfig(version="trial-1", mode=mode, weights={"youth_developer": 3.0},
                             max_depth_score_shift=3.0, close_score_window=4.0)


def qb_room():
    roster = make_roster(seed=11)
    qbs = [p for p in roster if p["position"] == "QB"]
    vet, kid = qbs[0], qbs[1]
    vet.update(overall=80, age=31)
    kid.update(overall=78, age=22)
    for extra in qbs[2:]:
        extra.update(overall=60, age=27)
    return roster, vet, kid


def candidates(roster, slot, injuries=None):
    rows = []
    for p in roster:
        score = dce.slot_score(p, slot, SCHEME, None, None)
        cohort = "youth" if p["age"] <= dce.YOUTH_AGE else "veteran" if p["age"] >= dce.VETERAN_AGE else "other"
        rows.append({"player_id": p["player_id"], "slot": slot, "neutral_score": score or 0.0,
                     "eligible": score is not None, "out": dce.is_out(p["player_id"], injuries),
                     "cohort": cohort, "cohort_weight": 1.0})
    return rows


def test_defaults_and_empty_or_zero_adjustments_are_identical():
    roster = make_roster(seed=4)
    base = dce.staff_depth_chart(roster, SCHEME, {})
    assert dce.staff_depth_chart(roster, SCHEME, {}, score_adjustments=None) == base
    assert dce.staff_depth_chart(roster, SCHEME, {}, score_adjustments={}) == base
    qb = next(p for p in roster if p["position"] == "QB")["player_id"]
    assert dce.staff_depth_chart(roster, SCHEME, {}, score_adjustments={"QB": {qb: 0.0}}) == base


def test_off_and_shadow_policy_give_no_adjustments_and_identical_chart():
    roster, vet, kid = qb_room()
    base = dce.staff_depth_chart(roster, SCHEME, {}, weights=None)
    for config in (pc.OFF, cfg("shadow")):
        adj = pc.opportunity_adjustments(candidates=candidates(roster, "QB"), coach_view=coach(), config=config)
        assert dce.staff_depth_chart(roster, SCHEME, {}, weights=None, score_adjustments=adj["adjustments"]) == base


def test_youth_developer_flips_a_close_call_only():
    roster, vet, kid = qb_room()
    base = dce.staff_depth_chart(roster, SCHEME, {}, weights=None)
    assert base["QB"][0] == vet["player_id"]
    adj = pc.opportunity_adjustments(candidates=candidates(roster, "QB"), coach_view=coach(), config=cfg())
    chart = dce.staff_depth_chart(roster, SCHEME, {}, weights=None, score_adjustments=adj["adjustments"])
    assert chart["QB"][0] == kid["player_id"]
    # A wide gap stays with the veteran.
    kid["overall"] = 70
    adj = pc.opportunity_adjustments(candidates=candidates(roster, "QB"), coach_view=coach(), config=cfg())
    chart = dce.staff_depth_chart(roster, SCHEME, {}, weights=None, score_adjustments=adj["adjustments"])
    assert chart["QB"][0] == vet["player_id"]


def test_injured_player_never_appears_even_with_adjustment():
    roster, vet, kid = qb_room()
    injuries = {kid["player_id"]: {"out": True, "weeks": 2}}
    chart = dce.staff_depth_chart(roster, SCHEME, injuries, weights=None,
                                  score_adjustments={"QB": {kid["player_id"]: 3.0}})
    assert kid["player_id"] not in {pid for ids in chart.values() for pid in ids}


def test_base_starter_uniqueness_and_eligibility_preserved():
    roster = make_roster(seed=8)
    players = by_id(roster)
    cbs = [p for p in roster if p["position"] in ("CB", "FS", "SS")]
    adj = {"CB": {p["player_id"]: 3.0 for p in cbs if dce.eligibility_penalty("CB", p["position"]) is not None},
           "FS": {p["player_id"]: 2.0 for p in cbs if dce.eligibility_penalty("FS", p["position"]) is not None}}
    chart = dce.staff_depth_chart(roster, SCHEME, {}, score_adjustments=adj)
    starters = dce.starters_by_base_slot(chart, SCHEME)
    flat = [pid for ids in starters.values() for pid in ids]
    assert len(flat) == len(set(flat))
    for slot, ids in chart.items():
        assert len(ids) == len(set(ids)) <= dce.DEPTH
        for pid in ids:
            assert dce.eligibility_penalty(slot, players[pid]["position"]) is not None


def test_fallback_still_fills_empty_slot():
    roster = [p for p in make_roster(seed=2) if p["position"] != "LS"]
    chart = dce.staff_depth_chart(roster, SCHEME, {}, score_adjustments={})
    assert chart["LS"]


@pytest.mark.parametrize("bad", [
    {"XX": {1: 1.0}},
    {"QB": {999999: 1.0}},
    {"QB": {"QB_ID": float("nan")}},
    {"QB": {"QB_ID": 50.0}},
    {"QB": {"QB_ID": True}},
    {"QB": {"K_ID": 1.0}},
    {"QB": [1, 2]},
])
def test_invalid_adjustments_refused(bad):
    roster = make_roster(seed=4)
    qb = next(p for p in roster if p["position"] == "QB")["player_id"]
    k = next(p for p in roster if p["position"] == "K")["player_id"]
    fixed = {}
    for slot, row in bad.items():
        if isinstance(row, dict):
            row = {(qb if pid == "QB_ID" else k if pid == "K_ID" else pid): v for pid, v in row.items()}
        fixed[slot] = row
    with pytest.raises(ValueError):
        dce.staff_depth_chart(roster, SCHEME, {}, score_adjustments=fixed)


def test_learning_only_activation_cannot_change_chart():
    roster, vet, kid = qb_room()
    base = dce.staff_depth_chart(roster, SCHEME, {}, weights=None)
    learning = pd.DevelopmentConfig(version="v", mode="enabled", weights={"film": .3},
                                    max_relative_change=.5, max_annual_abs_points=5,
                                    max_annual_ovr_difference=1, max_per_rating_annual_difference=2)
    assert learning.mode == "enabled"
    adj = pc.opportunity_adjustments(candidates=candidates(roster, "QB"), coach_view=coach(), config=pc.OFF)
    assert dce.staff_depth_chart(roster, SCHEME, {}, weights=None, score_adjustments=adj["adjustments"]) == base


def test_coach_trust_scale_stays_zero():
    assert dce.COACH_TRUST_SCALE == 0.0
