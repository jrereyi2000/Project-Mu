"""Pure coaching preferences (PERSONALITY_DEVELOPMENT_COACHING_PLAN U1)."""
import pytest

from engines import personality_coaching as pc


def view(traits=(), status="stored"):
    return {"status": status, "traits": list(traits), "sliders": {}, "persona_id": "c"}


def cfg(mode="enabled", **weights):
    return pc.CoachingConfig(version="trial-1", mode=mode, weights=weights, max_depth_score_shift=2.0,
                             close_score_window=3.0, max_scheme_score_shift=1.5)


def cand(pid, score, cohort="other", weight=1.0, slot="WR", eligible=True, out=False):
    return {"player_id": pid, "slot": slot, "neutral_score": score, "eligible": eligible, "out": out,
            "cohort": cohort, "cohort_weight": weight}


def adjust(cands, coach=None, config=None, **kw):
    return pc.opportunity_adjustments(candidates=cands, coach_view=coach, config=config, **kw)


def test_off_default():
    out = adjust([cand(1, 80, "youth")], view(["YOUTH_DEVELOPER"]))
    assert out["adjustments"] == {}


@pytest.mark.parametrize("kwargs", [dict(weights={"x": 1}), dict(weights={"youth_developer": -1}),
                                    dict(close_score_window=float("nan")), dict(mode="enabled"),
                                    dict(mode="enabled", version="v")])
def test_invalid_configs(kwargs):
    with pytest.raises(pc.CoachingPolicyError):
        pc.CoachingConfig(**kwargs)


def test_close_score_boundaries():
    cands = [cand(1, 80, "veteran"), cand(2, 77, "youth"), cand(3, 76.9, "youth")]
    out = adjust(cands, view(["YOUTH_DEVELOPER"]), cfg(youth_developer=1.0))
    assert out["adjustments"] == {"WR": {2: 1.0}}


def test_shift_clipped_once_and_cohort_weight_clipped():
    out = adjust([cand(1, 80), cand(2, 79, "youth", weight=5)], view(["YOUTH_DEVELOPER"]),
                 cfg(youth_developer=10, team_vision=10), playing_time="YOUTH")
    assert out["adjustments"]["WR"][2] == 2.0
    half = adjust([cand(1, 80), cand(2, 79, "youth", weight=.5)], view(["YOUTH_DEVELOPER"]),
                  cfg(youth_developer=1))
    assert half["adjustments"]["WR"][2] == .5


def test_illegal_and_out_candidates_never_move_or_set_best():
    cands = [cand(1, 95, eligible=False), cand(4, 90, out=True), cand(2, 80), cand(3, 78, "youth"),
             cand(5, 79, "youth", out=True), cand(6, 79, "youth", eligible=False)]
    out = adjust(cands, view(["YOUTH_DEVELOPER"]), cfg(youth_developer=1))
    assert out["adjustments"] == {"WR": {3: 1.0}}


def test_veteran_preference_needs_explicit_input_not_old_school():
    cands = [cand(1, 80, "youth"), cand(2, 79, "veteran")]
    assert adjust(cands, view(["OLD_SCHOOL"]), cfg(youth_developer=1, team_vision=1))["adjustments"] == {}
    out = adjust(cands, view(["OLD_SCHOOL"]), cfg(team_vision=1), playing_time="VETERANS")
    assert out["adjustments"] == {"WR": {2: 1.0}}


def test_merit_and_missing_coach_neutral():
    cands = [cand(1, 80), cand(2, 79, "youth")]
    assert adjust(cands, None, cfg(youth_developer=1, team_vision=1), playing_time="MERIT")["adjustments"] == {}
    assert adjust(cands, view(status="missing"), cfg(youth_developer=1))["adjustments"] == {}


def test_shadow_keeps_adjustments_private():
    out = adjust([cand(1, 80), cand(2, 79, "youth")], view(["YOUTH_DEVELOPER"]), cfg("shadow", youth_developer=1))
    assert out["adjustments"] == {} and out["shadow_adjustments"] == {"WR": {2: 1.0}}


def test_invalid_candidates_fail():
    with pytest.raises(pc.CoachingPolicyError):
        adjust([cand(1, float("inf"))], None, cfg())
    with pytest.raises(pc.CoachingPolicyError):
        adjust([cand(1, 80), cand(1, 70)], None, cfg())
    with pytest.raises(pc.CoachingPolicyError):
        adjust([cand(1, 80, "rookie")], None, cfg())
    with pytest.raises(pc.CoachingPolicyError):
        adjust([cand(1, 80)], None, cfg(), playing_time="STARS")


def test_slots_are_independent():
    cands = [cand(1, 80, slot="WR"), cand(2, 78, "youth", slot="WR"),
             cand(3, 70, slot="CB"), cand(4, 60, "youth", slot="CB")]
    out = adjust(cands, view(["YOUTH_DEVELOPER"]), cfg(youth_developer=1))
    assert out["adjustments"] == {"WR": {2: 1.0}}


# ------------------------------------------------------------------ scheme advice

def choice(cid, score, legal=True, tendency=False, cost=0.0):
    return {"choice_id": cid, "legal": legal, "neutral_score": score, "continuity_cost": cost,
            "supported_tendency": tendency}


def test_scheme_neutral_order_and_stable_ties():
    out = pc.rank_scheme_choices(choices=[choice("b", 5), choice("a", 5), choice("c", 6)],
                                 coach_view=None, config=None)
    assert out["ranked"] == ["c", "a", "b"] and out["advice_only"] is True


def test_illegal_choices_excluded():
    out = pc.rank_scheme_choices(choices=[choice("x", 9, legal=False), choice("y", 1)],
                                 coach_view=None, config=cfg(scheme_tendency=5))
    assert out["ranked"] == ["y"]


def test_only_supported_tendency_moves_bounded():
    choices = [choice("a", 6), choice("b", 5, tendency=True)]
    out = pc.rank_scheme_choices(choices=choices, coach_view=view(["SCHEME_NERD"]), config=cfg(scheme_tendency=5))
    assert out["ranked"] == ["b", "a"]
    far = [choice("a", 7), choice("b", 5, tendency=True)]
    assert pc.rank_scheme_choices(choices=far, coach_view=None, config=cfg(scheme_tendency=5))["ranked"] == ["a", "b"]


def test_scheme_nerd_is_context_only_and_continuity_not_recounted():
    choices = [choice("a", 6, cost=3), choice("b", 5)]
    out = pc.rank_scheme_choices(choices=choices, coach_view=view(["SCHEME_NERD"]), config=cfg(scheme_tendency=5))
    assert out["ranked"] == ["a", "b"]
    assert {"code": "CONTEXT_ONLY", "traits": ["SCHEME_NERD"]} in out["attribution"]


def test_scheme_shadow_does_not_change_ranking():
    choices = [choice("a", 6), choice("b", 5, tendency=True)]
    out = pc.rank_scheme_choices(choices=choices, coach_view=None, config=cfg("shadow", scheme_tendency=5))
    assert out["ranked"] == ["a", "b"] and out["shadow_ranked"] == ["b", "a"]


def test_scheme_rejects_bad_input():
    with pytest.raises(pc.CoachingPolicyError):
        pc.rank_scheme_choices(choices=[choice("a", 1), choice("a", 2)], coach_view=None, config=None)
    with pytest.raises(pc.CoachingPolicyError):
        pc.rank_scheme_choices(choices=[choice("a", float("nan"))], coach_view=None, config=None)
