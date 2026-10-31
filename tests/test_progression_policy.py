"""Candidate policy tests: evidence is not an automatic permanent reward."""
import copy
import json

import pytest

from engines import progression_policy as pp


def band(technical=85, mental=85, physical=90):
    return {"floor": 60, "spread": 3,
            "group_ceilings": {"physical": physical, "technical": technical, "mental": mental}}


def evidence(**changes):
    out = {"window_id": "window-a", "evidence_ids": ["g1", "g2", "g3", "g4"],
           "eligible": True, "reliability": 1.0, "performance_signal": 0.9,
           "meaningful_appearances": 4, "opportunities": 200,
           "trigger": "PLAYOFF_RUN", "supported_skills": ["Catching", "Awareness"]}
    out.update(changes)
    return out


def decide(**changes):
    args = dict(player_id=123, earned_season=2037, event_id="event-a", rules_version="candidate-v1",
                ratings={"Awareness": 70, "PlayRecognition": 70, "Catching": 80, "Speed": 90},
                underlying_ovr=82, band=band(), evidence=evidence(), checkpoint_kind="REVIEW", age=25)
    args.update(changes)
    return pp.evaluate_development(**args)


def test_eligibility_alone_never_awards_improvement(monkeypatch):
    monkeypatch.setattr(pp, "_unit_draw", lambda *args: 0.0)
    for ev in (evidence(performance_signal=None), evidence(performance_signal=-0.8),
               evidence(reliability=0), evidence(meaningful_appearances=0),
               evidence(opportunities=0), evidence(evidence_ids=[]), evidence(eligible=False)):
        result = decide(evidence=ev)
        assert result["outcome"] == "NO_CHANGE"
        assert result["skill_deltas"] == result["band_changes"] == {}


def test_review_probability_can_leave_eligible_strong_player_unchanged(monkeypatch):
    monkeypatch.setattr(pp, "_unit_draw", lambda *args: 0.99)
    assert decide()["outcome"] == "NO_CHANGE"


def test_review_can_award_skill_band_or_both_separately(monkeypatch):
    monkeypatch.setattr(pp, "_unit_draw", lambda *args: 0.0)
    both = decide()
    assert both["outcome"] == "BOTH"
    assert both["band_changes"] == {"technical": 2.7, "mental": 2.7}
    assert both["skill_deltas"]["Catching"] > 0
    skill = decide(band=band(95, 95))
    assert skill["outcome"] == "SKILL" and not skill["band_changes"]
    band_only = decide(evidence=evidence(supported_skills=[]))
    assert band_only["outcome"] == "BAND" and not band_only["skill_deltas"]


def test_short_complete_playoff_run_can_receive_review_award(monkeypatch):
    monkeypatch.setattr(pp, "_unit_draw", lambda *args: 0.0)
    result = decide(evidence=evidence(meaningful_appearances=3, reliability=0.1875,
                                     evidence_ids=["division", "conference", "superbowl"]))
    assert result["outcome"] == "BOTH"
    assert result["provenance"]["trigger"] == "PLAYOFF_RUN"


def test_older_player_is_eligible_without_hard_age_cutoff(monkeypatch):
    monkeypatch.setattr(pp, "_unit_draw", lambda *args: 0.0)
    young, old = decide(age=23), decide(age=34)
    assert old["outcome"] == "BOTH"
    assert 0 < old["skill_deltas"]["Awareness"] < young["skill_deltas"]["Awareness"]


def test_regular_mental_learning_does_not_need_breakout_eligibility():
    ev = evidence(eligible=False, meaningful_appearances=4, performance_signal=None)
    result = decide(checkpoint_kind="MENTAL", evidence=ev)
    assert result["outcome"] == "SKILL"
    assert set(result["skill_deltas"]) == {"Awareness", "PlayRecognition"}
    assert all(0 < value < 1 for value in result["skill_deltas"].values())
    assert not result["band_changes"]


def test_technical_learning_requires_explicit_supported_skill_mapping():
    ev = evidence(eligible=False, supported_skills=[])
    assert decide(checkpoint_kind="TECHNICAL", evidence=ev)["outcome"] == "NO_CHANGE"
    result = decide(checkpoint_kind="TECHNICAL", evidence=ev, supported_skills=["Catching"])
    assert set(result["skill_deltas"]) == {"Catching"}


def test_policy_cannot_boost_physical_or_fixed_ratings(monkeypatch):
    monkeypatch.setattr(pp, "_unit_draw", lambda *args: 0.0)
    result = decide(evidence=evidence(supported_skills=["Speed", "Acceleration", "Injury", "Catching"]),
                    ratings={"Speed": 90, "Acceleration": 90, "Injury": 80, "Catching": 70})
    assert set(result["skill_deltas"]) == {"Catching"}
    assert "physical" not in result["band_changes"]
    assert result["target_band"]["group_ceilings"]["physical"] == 90


def test_no_slump_potential_loss(monkeypatch):
    monkeypatch.setattr(pp, "_unit_draw", lambda *args: 0.0)
    before = band()
    result = decide(band=before, evidence=evidence(performance_signal=-1))
    assert result["target_band"] == before
    assert not result["band_changes"]


def test_soft_resistance_uses_ovr_scalar_and_never_hard_stops_growth():
    assert pp.growth_resistance(75, 85) > pp.growth_resistance(85, 85) > pp.growth_resistance(95, 85) > 0
    low_mental = decide(checkpoint_kind="MENTAL", underlying_ovr=85,
                        ratings={"Awareness": 30, "PlayRecognition": 30})
    high_mental = decide(checkpoint_kind="MENTAL", underlying_ovr=85,
                         ratings={"Awareness": 80, "PlayRecognition": 80})
    assert low_mental["skill_deltas"] == high_mental["skill_deltas"]


def test_seed_is_stable_and_inputs_remain_unchanged():
    ev, prior_band = evidence(), band()
    before = copy.deepcopy((ev, prior_band))
    one = decide(evidence=ev, band=prior_band)
    assert one == decide(evidence=ev, band=prior_band)
    assert (ev, prior_band) == before
    values = {json.dumps(decide(player_id=pid), sort_keys=True) for pid in range(10)}
    assert len(values) > 1


def test_label_is_configurable_display_only_and_never_writes_madden(monkeypatch):
    monkeypatch.setattr(pp, "_unit_draw", lambda *args: 0.0)
    result = decide()
    assert result["desired_label"] == "Superstar"
    assert result["actual_dev_write"] is None
    assert "TraitDevelopment" not in result
    assert pp.summarize_band(band(84, 84)) == "Star"
    config = pp.PolicyConfig(label_thresholds=(60, 70, 80))
    assert pp.summarize_band(band(84, 84), config) == "X-Factor"
    # Changing label candidates cannot feed back into actual skills/band amounts.
    altered = decide(config=config)
    assert altered["skill_deltas"] == result["skill_deltas"]
    assert altered["band_changes"] == result["band_changes"]


def test_public_summary_contains_no_hidden_values_or_profile_inferences(monkeypatch):
    monkeypatch.setattr(pp, "_unit_draw", lambda *args: 0.0)
    result = decide()
    assert set(result["public_summary"]) == {"outcome", "reasons", "checkpoint_kind"}
    assert all(key not in result["public_summary"] for key in
               ("target_band", "band_changes", "floor", "spread", "group_ceilings", "durability"))
    assert result["provenance"]["evidence_ids"] == evidence()["evidence_ids"]
    assert result["provenance"]["rules_version"] == "candidate-v1"


def test_form_fields_are_not_consumed_as_permanent_improvement():
    original = decide(checkpoint_kind="MENTAL")
    changed = decide(checkpoint_kind="MENTAL", evidence=evidence(form=15, seasonal_form=15))
    assert original == changed


@pytest.mark.parametrize("kwargs", [
    {"underlying_ovr": float("nan")}, {"evidence": evidence(reliability=2)},
    {"evidence": evidence(performance_signal=2)}, {"ratings": {"Awareness": 100}},
    {"checkpoint_kind": "OFFSEASON"}, {"event_id": ""}, {"age": -1},
])
def test_invalid_policy_inputs_are_rejected(kwargs):
    with pytest.raises(ValueError):
        decide(**kwargs)


def test_invalid_policy_config_rejected():
    with pytest.raises(ValueError):
        pp.PolicyConfig(label_thresholds=(85, 75, 95))
    with pytest.raises(ValueError):
        pp.PolicyConfig(approach_width=0)


def test_display_label_candidates_cannot_change_seeded_development():
    config = pp.PolicyConfig(label_thresholds=(50, 60, 70), label_technical_weight=0.1)
    for pid in range(30):
        standard, different_labels = decide(player_id=pid), decide(player_id=pid, config=config)
        for key in ("outcome", "skill_deltas", "band_changes", "target_band"):
            assert standard[key] == different_labels[key]
