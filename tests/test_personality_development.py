"""Pure development policy (PERSONALITY_DEVELOPMENT_COACHING_PLAN U1): no DB, synthetic views."""
import copy
import math

import pytest

from engines import personality_development as pd
from engines import progression_policy as policy

SLIDERS = dict(temperament=5, sociability=5, confidence=5, openness=5, warmth=5, drive=5)


def view(traits=(), status="stored", origin="generated", **sliders):
    if status == "missing":
        return dict(status="missing", origin=None, sliders=dict(SLIDERS), traits=[], revision=None,
                    persona_id=None, view_version="views-v2")
    return dict(status=status, origin=origin, sliders={**SLIDERS, **sliders}, traits=list(traits),
                revision=3, persona_id="p-1", catalogue_version="cat", view_version="views-v2")


def cfg(mode="enabled", **weights):
    return pd.DevelopmentConfig(version="trial-1", mode=mode, weights=weights,
                                max_relative_change=0.5, max_annual_abs_points=10,
                                max_annual_ovr_difference=2, max_per_rating_annual_difference=3)


def neutral(kind="MENTAL", **deltas):
    deltas = deltas or ({"Awareness": 1.0, "PlayRecognition": 0.5} if kind == "MENTAL"
                        else {"Catching": 1.0, "ShortRouteRunning": 0.4})
    return {"skill_deltas": deltas, "public_summary": {"checkpoint_kind": kind},
            "target_band": {"group_ceilings": {"physical": 80, "technical": 80, "mental": 80}}}


def effect(result, player=None, coaches=(), config=None, **context):
    return pd.evaluate_learning_effect(neutral_result=result, player_view=player, coach_views=list(coaches),
                                       mentor_view=None, context=context, config=config)


# ------------------------------------------------------------------ config

def test_default_config_is_off_and_neutral():
    assert pd.DevelopmentConfig().mode == "off"
    out = effect(neutral(), view(["FILM_ROOM_RAT"]), config=None)
    assert out["skill_deltas"] == {} and out["attribution"]["reasons"] == ["OFF"]


@pytest.mark.parametrize("kwargs", [
    dict(weights={"unknown": 1}),
    dict(weights={"film": -0.1}),
    dict(weights={"coasting": 0.1}),
    dict(weights={"film": float("nan")}),
    dict(max_relative_change=1.5),
    dict(max_annual_abs_points=float("inf")),
    dict(max_per_rating_annual_difference=-1),
    dict(mode="loud"),
])
def test_invalid_configs_fail(kwargs):
    with pytest.raises(pd.DevelopmentPolicyError):
        pd.DevelopmentConfig(**kwargs)


def test_enabled_needs_version_and_every_cap():
    with pytest.raises(pd.DevelopmentPolicyError, match="version"):
        pd.DevelopmentConfig(mode="enabled", max_relative_change=.1, max_annual_abs_points=1,
                             max_annual_ovr_difference=1, max_per_rating_annual_difference=1)
    with pytest.raises(pd.DevelopmentPolicyError, match="cap"):
        pd.DevelopmentConfig(version="v", mode="enabled", max_relative_change=.1)
    with pytest.raises(pd.DevelopmentPolicyError, match="version"):
        pd.DevelopmentConfig(mode="shadow")


def test_from_dict_rejects_unknown_fields_and_hash_is_stable():
    with pytest.raises(pd.DevelopmentPolicyError, match="unknown"):
        pd.DevelopmentConfig.from_dict({"mode": "off", "preset": "x"})
    a = pd.DevelopmentConfig.from_dict(cfg().as_dict())
    assert a == cfg() and a.config_hash() == cfg().config_hash()


# ------------------------------------------------------------------ channels

def test_zero_weights_enabled_equal_neutral():
    out = effect(neutral(), view(["FILM_ROOM_RAT", "PERFECTIONIST"], drive=10), config=cfg())
    assert out["skill_deltas"] == {}


def test_film_scales_only_mental_learning():
    out = effect(neutral(), view(["FILM_ROOM_RAT"]), config=cfg(film=0.2))
    assert out["skill_deltas"] == {"Awareness": 0.2, "PlayRecognition": 0.1}
    tech = effect(neutral("TECHNICAL"), view(["FILM_ROOM_RAT"]), config=cfg(film=0.2))
    assert tech["skill_deltas"] == {}


def test_perfectionist_scales_only_technical_learning():
    out = effect(neutral("TECHNICAL"), view(["PERFECTIONIST"]), config=cfg(technical_preparation=0.1))
    assert out["skill_deltas"] == {"Catching": 0.1, "ShortRouteRunning": 0.04}
    assert effect(neutral(), view(["PERFECTIONIST"]), config=cfg(technical_preparation=0.1))["skill_deltas"] == {}


def test_drive_is_centered_at_five():
    assert effect(neutral(), view(drive=5), config=cfg(drive=0.2))["skill_deltas"] == {}
    up = effect(neutral(), view(drive=10), config=cfg(drive=0.2))["skill_deltas"]
    down = effect(neutral(), view(drive=0), config=cfg(drive=0.2))["skill_deltas"]
    assert up["Awareness"] == pytest.approx(0.2) and down["Awareness"] == pytest.approx(-0.2)


def test_coasting_reduces_but_never_below_zero_learning():
    out = effect(neutral(), view(["COASTING"]), config=cfg(coasting=-0.3))["skill_deltas"]
    assert out["Awareness"] == pytest.approx(-0.3)
    # Clamped once to max_relative_change: total learning never goes negative.
    big = effect(neutral(), view(["COASTING"]), config=cfg(coasting=-5))["skill_deltas"]
    assert big["Awareness"] == pytest.approx(-0.5)


def test_one_aggregate_relative_cap():
    out = effect(neutral(), view(["FILM_ROOM_RAT"], drive=10),
                 coaches=[{"view": view(["TEACHER"]), "exposure": 1}],
                 config=cfg(film=0.4, drive=0.4, teaching=0.4))
    assert out["skill_deltas"]["Awareness"] == pytest.approx(0.5)
    assert "RELATIVE_CAP" in out["attribution"]["reasons"]


def test_zero_neutral_learning_stays_zero_and_negative_or_physical_untouched():
    result = neutral(Awareness=0.0, PlayRecognition=-1.0)
    assert effect(result, view(["FILM_ROOM_RAT"]), config=cfg(film=0.3))["skill_deltas"] == {}
    tech = neutral("TECHNICAL", Speed=1.0, Injury=1.0, Strength=2.0)
    assert effect(tech, view(["PERFECTIONIST"]), config=cfg(technical_preparation=0.3))["skill_deltas"] == {}


def test_review_unchanged():
    out = effect(neutral("REVIEW", Awareness=1.0), view(["FILM_ROOM_RAT"]), config=cfg(film=0.3))
    assert out["skill_deltas"] == {} and "REVIEW_UNCHANGED" in out["attribution"]["reasons"]


def test_neutral_result_is_not_mutated_and_band_untouched():
    result = neutral()
    before = copy.deepcopy(result)
    effect(result, view(["FILM_ROOM_RAT"]), config=cfg(film=0.3))
    assert result == before


def test_headroom_keeps_targets_inside_99():
    out = effect(neutral(Awareness=1.0), view(["FILM_ROOM_RAT"]), config=cfg(film=0.5),
                 ratings={"Awareness": 98.2})
    assert out["skill_deltas"].get("Awareness", 0) <= 99 - 98.2 - 1.0 + 1e-9 or "Awareness" not in out["skill_deltas"]


# ------------------------------------------------------------------ teaching

def test_teaching_needs_verified_exposure_and_scales_with_it():
    coach = view(["TEACHER"])
    assert effect(neutral(), view(), coaches=[coach], config=cfg(teaching=0.2))["skill_deltas"] == {}
    half = effect(neutral(), view(), coaches=[{"view": coach, "exposure": .5}], config=cfg(teaching=0.2))
    assert half["skill_deltas"]["Awareness"] == pytest.approx(0.1)


def test_strongest_teacher_not_summed():
    coaches = [{"view": view(["TEACHER"]), "exposure": 1, "persona_id": "hc"},
               {"view": view(["TEACHER"]), "exposure": 1, "persona_id": "oc"},
               {"view": view(["TEACHER"]), "exposure": .5, "persona_id": "pos"}]
    out = effect(neutral(), view(), coaches=coaches, config=cfg(teaching=0.2))
    assert out["skill_deltas"]["Awareness"] == pytest.approx(0.2)


def test_qb_specialty_is_strongest_of_generic_or_specialty_for_qbs_only():
    coach = {"view": view(["TEACHER", "QB_WHISPERER"]), "exposure": 1}
    qb = effect(neutral(), view(), coaches=[coach], config=cfg(teaching=0.1, qb_teaching=0.3), is_qb=True)
    assert qb["skill_deltas"]["Awareness"] == pytest.approx(0.3)
    wr = effect(neutral(), view(), coaches=[coach], config=cfg(teaching=0.1, qb_teaching=0.3), is_qb=False)
    assert wr["skill_deltas"]["Awareness"] == pytest.approx(0.1)


def test_coach_traits_without_teaching_meaning_do_nothing():
    coach = {"view": view(["DISCIPLINARIAN", "PLAYERS_COACH", "YOUTH_DEVELOPER", "MENTOR"]), "exposure": 1}
    assert effect(neutral(), view(), coaches=[coach], config=cfg(teaching=.3, mentor=.3))["skill_deltas"] == {}


def test_missing_coach_or_player_is_neutral():
    out = effect(neutral(), view(status="missing"), coaches=[{"view": view(status="missing"), "exposure": 1}],
                 config=cfg(film=.3, teaching=.3, drive=.3))
    assert out["skill_deltas"] == {}
    assert "PLAYER_PROFILE_MISSING" in out["attribution"]["reasons"]


def test_unknown_origin_is_neutral_via_core_view():
    from personality.core import feature_view
    assert feature_view(None, "development")["status"] == "missing"


# ------------------------------------------------------------------ invariance

@pytest.mark.parametrize("change", [
    dict(confidence=0), dict(confidence=10), dict(sociability=0), dict(warmth=10),
    dict(temperament=10), dict(openness=0)])
def test_irrelevant_sliders_change_nothing(change):
    config = cfg(film=.2, drive=.2, coasting=-.2)
    base = effect(neutral(), view(["FILM_ROOM_RAT"]), config=config)["skill_deltas"]
    assert effect(neutral(), view(["FILM_ROOM_RAT"], **change), config=config)["skill_deltas"] == base


@pytest.mark.parametrize("trait", ["MONEY_MOTIVATED", "HOMETOWN_HERO", "QUIET", "LONER", "SHOWMAN",
                                   "RING_CHASER", "OLD_SCHOOL", "WORKOUT_FANATIC", "YOUTH_DEVELOPER"])
def test_money_quiet_hometown_loner_traits_are_invariant(trait):
    config = cfg(film=.2, drive=.2, coasting=-.2, technical_preparation=.2, teaching=.2, mentor=.2)
    base = effect(neutral(), view(["FILM_ROOM_RAT"]), config=config)["skill_deltas"]
    assert effect(neutral(), view(["FILM_ROOM_RAT", trait]), config=config)["skill_deltas"] == base


def test_workout_fanatic_is_reported_deferred():
    out = effect(neutral(), view(["WORKOUT_FANATIC"]), config=cfg(film=.2))
    assert "PHYSICAL_TRAINING_DEFERRED" in out["attribution"]["reasons"]


def test_shadow_returns_neutral_with_private_comparison():
    out = effect(neutral(), view(["FILM_ROOM_RAT"]), config=cfg("shadow", film=.2))
    assert out["skill_deltas"] == {}
    assert out["attribution"]["shadow_skill_deltas"]["Awareness"] == pytest.approx(0.2)


def test_real_policy_output_feeds_effect_without_rerolling():
    ratings = {"Awareness": 60, "PlayRecognition": 55, "Catching": 70}
    kwargs = dict(player_id=7, earned_season=2034, event_id="e", rules_version="r", ratings=ratings,
                  underlying_ovr=65, band={"group_ceilings": {"physical": 80, "technical": 80, "mental": 80}},
                  evidence={"evidence_ids": [1, 2, 3, 4], "meaningful_appearances": 4, "opportunities": 100,
                            "reliability": .8, "performance_signal": .5, "window_id": "w"},
                  checkpoint_kind="MENTAL")
    first = policy.evaluate_development(**kwargs)
    out = effect(first, view(["FILM_ROOM_RAT"]), config=cfg(film=.2))
    assert policy.evaluate_development(**kwargs) == first
    for skill, amount in out["skill_deltas"].items():
        assert amount == pytest.approx(first["skill_deltas"][skill] * .2)


# ------------------------------------------------------------------ mentor

def test_mentor_adjustment_scales_existing_component_only():
    comp = {"Awareness": .1, "PlayRecognition": .05, "Speed": .3}
    out = pd.mentor_adjustment(neutral_component=comp, mentor_view=view(["MENTOR"]),
                               verified_exposure=True, config=cfg(mentor=.2))
    assert out["differences"] == {"Awareness": pytest.approx(.02), "PlayRecognition": pytest.approx(.01)}


@pytest.mark.parametrize("mentor,exposure,reason", [
    (None, True, "MENTOR_PROFILE_MISSING"),
    (view(["MENTOR"]), False, "NO_VERIFIED_MENTOR_EXPOSURE"),
    (view(["LONER"]), True, "NO_MENTOR_TRAIT")])
def test_mentor_absence_and_loner_are_neutral(mentor, exposure, reason):
    out = pd.mentor_adjustment(neutral_component={"Awareness": .1}, mentor_view=mentor,
                               verified_exposure=exposure, config=cfg(mentor=.2))
    assert out["differences"] == {} and reason in out["reasons"]


def test_loner_never_erases_neutral_component():
    out = pd.mentor_adjustment(neutral_component={"Awareness": .1}, mentor_view=view(["LONER"]),
                               verified_exposure=True, config=cfg(mentor=.9))
    assert all(v >= 0 for v in out["differences"].values())


def test_zero_component_gives_zero_difference():
    out = pd.mentor_adjustment(neutral_component={}, mentor_view=view(["MENTOR"]),
                               verified_exposure=True, config=cfg(mentor=.5))
    assert out["differences"] == {}


# ------------------------------------------------------------------ annual limits

def linear_ovr(ratings):
    return sum(ratings.values()) / len(ratings)


def limit(neutral_t, proposed_t, applied=None, config=None, ovr_at=linear_ovr):
    return pd.limit_annual_effect(annual_start=neutral_t, neutral_target=neutral_t, proposed_target=proposed_t,
                                  applied_effects=applied, config=config or cfg(), ovr_at=ovr_at)


def test_limit_off_returns_neutral():
    out = limit({"A": 50, "B": 50}, {"A": 51, "B": 50}, config=pd.OFF)
    assert out["target"] == {"A": 50, "B": 50} and out["annual_effects"] == {}


def test_per_rating_cap_counts_applied_effects():
    out = limit({"A": 50, "B": 50}, {"A": 53, "B": 50}, applied={"A": 2})
    assert out["annual_effects"] == {"A": 1.0} and "PER_RATING_CAP" in out["reasons"]


def test_abs_spend_counts_both_signs_and_does_not_reset():
    config = pd.DevelopmentConfig(version="v", mode="enabled", max_relative_change=.5, max_annual_abs_points=2,
                                  max_annual_ovr_difference=10, max_per_rating_annual_difference=10)
    out = limit({"A": 50, "B": 50}, {"A": 51, "B": 49}, applied={"by_rating": {}, "abs_spend": 1}, config=config)
    assert sum(abs(v) for v in out["annual_effects"].values()) == pytest.approx(1)
    assert out["remaining_budget"]["abs_points"] == 0
    # Net cancellation (+1, -1) cannot hide spend.
    assert "ANNUAL_POINTS_CAP" in out["reasons"]


def test_ovr_cap_scales_effect():
    config = pd.DevelopmentConfig(version="v", mode="enabled", max_relative_change=.5, max_annual_abs_points=100,
                                  max_annual_ovr_difference=.5, max_per_rating_annual_difference=10)
    out = limit({"A": 50, "B": 50}, {"A": 53, "B": 50}, config=config)
    assert out["annual_effects"]["A"] == pytest.approx(1.0, abs=1e-6)
    assert "ANNUAL_OVR_CAP" in out["reasons"]


def test_rounded_ovr_plateau_cannot_hide_movement():
    # A rounding OVR function shows no change, but rating caps still bind.
    out = limit({"A": 50, "B": 50}, {"A": 55, "B": 50}, ovr_at=lambda r: 50)
    assert out["annual_effects"] == {"A": 3.0}


def test_refuses_when_ovr_cap_unprovable():
    config = pd.DevelopmentConfig(version="v", mode="enabled", max_relative_change=.5, max_annual_abs_points=100,
                                  max_annual_ovr_difference=.1, max_per_rating_annual_difference=10)
    # Prior applied effects already exceed the OVR envelope: no new effect.
    out = limit({"A": 52, "B": 50}, {"A": 53, "B": 50}, applied={"A": 2}, config=config)
    assert out["annual_effects"] == {}


def test_limit_rejects_mismatched_fields():
    with pytest.raises(pd.DevelopmentPolicyError):
        limit({"A": 50}, {"B": 50})


def test_targets_stay_inside_bounds():
    out = limit({"A": 98.5, "B": 50}, {"A": 100.5, "B": 50})
    assert out["target"]["A"] <= 99
