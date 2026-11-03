"""Pure report tests: calculations/proposals are not verified application."""
import copy
import json
from decimal import Decimal

import pytest

from engines import progression_report as report

DOC = {"slots": {"WR": ["WR_Test"]}, "archetypes": {"WR_Test": {
    "intercept": 0, "terms": {"Catching": [0.5, 0], "Speed": [0.5, 0]}}}}


def event(**changes):
    out = {"status": "PREPARED", "player_id": 17, "event_id": "checkpoint:123", "kind": "CHECKPOINT",
           "earned_season": 2037, "application_season": 2037,
           "candidate_base": {"Catching": 82, "Speed": 80, "Awareness": 71},
           "candidate_band": {"floor": 60, "spread": 3,
                              "group_ceilings": {"physical": 80, "technical": 90, "mental": 92}},
           "overlay": {"Catching": 4}, "annual_deltas": {"Catching": 2, "Awareness": 1},
           "evidence_ids": ["g2", "g1"],
           "metadata": {"week": 8, "position": "WR", "form": 2, "desired_label": "Superstar",
                        "fractions": {"Catching": 0.3},
                        "report_context": {"before_base": {"Catching": 80, "Speed": 80, "Awareness": 70},
                                           "before_overlay": {"Catching": 2}, "before_form": 1,
                                           "before_band": {"floor": 60, "spread": 3,
                                                           "group_ceilings": {"physical": 80, "technical": 88, "mental": 90}},
                                           "position": "WR", "actual_dev_before": "Normal"},
                        "decisions": [{"kind": "MENTAL", "evidence_ids": ["g1", "g2"],
                                       "evidence": {"reliability": 0.6},
                                       "result": {"skill_deltas": {"Catching": 0.6, "Awareness": 0.1},
                                                  "public_summary": {"reasons": ["EXPERIENTIAL_LEARNING"]},
                                                  "provenance": {"secret_seed": "hidden-seed"}}}]}}
    out.update(changes)
    return out


def annual(**changes):
    out = {"player_id": 17, "season": 2038, "position": "WR", "old_ovr": 80,
           "new_ovr": 90, "delta_ovr": 10, "cause": "CEILING",
           "ratings_before": {"Catching": 80, "Speed": 80},
           "target_ratings": {"Catching": 84, "Speed": 80}, "ratings_diff": {"Catching": 4},
           "dev_before": "Normal", "dev_after": "Star", "ceiling": 97,
           "age_term": -1, "league_term": 3, "upkeep_term": -0.5, "ceiling_term": 1.2,
           "calculation_status": "CALCULATED", "failure_code": None}
    out.update(changes)
    return out


def test_proposed_and_applied_are_explicit_and_do_not_infer_actual_trait():
    source = event()
    source["metadata"]["report_context"]["actual_dev_after"] = "Star"
    proposed = report.project_event(source, doc=DOC)
    assert proposed["status"] == "PROPOSED"
    assert proposed["development_label"] == {"desired": "Superstar", "actual_before": "Normal", "actual_after": None}
    source["status"] = "APPLIED"
    applied = report.project_event(source, doc=DOC)
    assert applied["status"] == "APPLIED"
    assert applied["development_label"]["actual_after"] == "Star"


def test_permanent_and_form_requested_effective_are_separate():
    result = report.project_event(event(), doc=DOC)
    assert result["permanent"] == {"before_ovr": 80, "after_ovr": 81, "planned_ovr": None,
                                   "rating_changes": {"Awareness": 1, "Catching": 2},
                                   "before_ovr_source": "RATINGS", "after_ovr_source": "RATINGS"}
    assert result["form"] == {"before_requested": 1, "after_requested": 2,
                              "before_effective_ovr": 81, "after_effective_ovr": 83,
                              "before_ovr_delta": 1, "after_ovr_delta": 2}
    assert result["evidence"] == {"ids": ["g1", "g2"], "count": 2, "reliability": 0.6}


def test_overlay_realization_can_be_smaller_than_request_at_rating_bounds():
    source = event(candidate_base={"Catching": 99, "Speed": 99}, overlay={"Catching": 10})
    source["metadata"]["form"] = 3
    result = report.project_event(source, doc=DOC)
    assert result["form"]["after_requested"] == 3
    assert result["form"]["after_effective_ovr"] == 99
    assert result["form"]["after_ovr_delta"] == 0


def test_missing_before_context_remains_unknown_not_reconstructed_from_delta():
    source = event()
    del source["metadata"]["report_context"]
    result = report.project_event(source, doc=DOC)
    assert result["permanent"]["before_ovr"] is None
    assert result["permanent"]["after_ovr"] == 81
    assert result["form"]["before_requested"] is None
    assert result["form"]["before_effective_ovr"] is None
    assert "MISSING_BEFORE_RATINGS" in result["failures"]


def test_missing_evidence_reliability_is_unknown_not_invented():
    source = event()
    source.pop("evidence_ids")
    source["metadata"]["decisions"] = []
    result = report.project_event(source, doc=DOC)
    assert result["evidence"] == {"ids": None, "count": None, "reliability": None}


def test_no_false_aggregate_reliability_for_multiple_decisions():
    source = event()
    source["metadata"]["decisions"].append({"evidence": {"reliability": 0.9}})
    assert report.project_event(source, doc=DOC)["evidence"]["reliability"] is None


def test_public_whitelist_hides_all_private_inputs_and_unsafe_reason_text():
    source = event()
    source["metadata"]["annual_result"] = annual()
    source["metadata"]["decisions"][0]["result"]["public_summary"]["reasons"].extend([
        "ceiling 97", "secret_seed=hidden-seed", {"durability": 0.2}])
    result = report.project_event(source, doc=DOC)
    encoded = json.dumps(result)
    for hidden in ("hidden-seed", "group_ceilings", "ceiling_term", "durability", "fractions", "before_base"):
        assert hidden not in encoded
    assert result["reasons"] == ["EXPERIENTIAL_LEARNING", "POTENTIAL_HEADROOM"]


def test_private_audit_keeps_provenance_fractions_and_nested_upkeep():
    source = event()
    source["metadata"]["annual_result"] = annual()
    result = report.project_event(source, audience="private", doc=DOC)
    audit = result["audit"]
    assert audit["requested_fractional"] == {"Awareness": 0.1, "Catching": 0.6}
    assert audit["carry"] == {"Catching": 0.3}
    assert audit["band_changes"]["group_deltas"] == {"physical": 0, "technical": 2, "mental": 2}
    assert audit["term_breakdown"]["included"]["upkeep_term"] == {"value": -0.5, "included_in": "league_term"}
    assert "upkeep_term" not in audit["term_breakdown"]["additive"]
    assert audit["provenance"]["metadata"]["decisions"][0]["result"]["provenance"]["secret_seed"] == "hidden-seed"


def test_annual_is_calculated_and_planned_ovr_not_confused_with_ratings_result():
    result = report.project_annual(annual(status="APPLIED"), doc=DOC)
    assert result["status"] == "CALCULATED"
    assert result["permanent"]["before_ovr"] == 80
    assert result["permanent"]["planned_ovr"] == 90
    assert result["permanent"]["after_ovr"] == 82
    assert result["development_label"]["actual_after"] is None
    assert all(value is None for value in result["form"].values())


def test_failed_distribution_has_explicit_failure_and_unknown_after_ratings():
    result = report.project_annual(annual(target_ratings=None, calculation_status="FAILED",
                                          failure_code="RATING_DISTRIBUTION_FAILED"), doc=DOC)
    assert result["permanent"]["planned_ovr"] == 90
    assert result["permanent"]["after_ovr"] is None
    assert "MISSING_TARGET_RATINGS" in result["failures"]
    assert result["permanent"]["rating_changes"] is None
    assert "RATING_DISTRIBUTION_FAILED" in result["failures"]


def test_explicit_recorded_old_ovr_survives_missing_raw_before_ratings():
    result = report.project_annual(annual(ratings_before=None, old_ovr=79), doc=DOC)
    assert result["permanent"]["before_ovr"] == 79
    assert result["permanent"]["before_ovr_source"] == "RECORDED_OVR"
    assert "MISSING_BEFORE_RATINGS" in result["failures"]


def test_offseason_calendar_is_application_week_zero_with_private_observed_week():
    source = event(kind="OFFSEASON", earned_season=2037, application_season=2038)
    source["metadata"]["week"] = 24
    result = report.project_event(source, audience="private", doc=DOC)
    assert result["calendar"] == {"earned_season": 2037, "application_season": 2038, "week": 0}
    assert result["audit"]["provenance"]["metadata"]["week"] == 24
    source["metadata"]["application_week"] = 1
    assert report.project_event(source, doc=DOC)["calendar"]["week"] == 1


def test_incomplete_rating_context_does_not_assume_missing_fields_are_zero():
    source = event(candidate_base={"Catching": 82})
    result = report.project_event(source, doc=DOC)
    assert result["permanent"]["after_ovr"] is None
    assert result["permanent"]["rating_changes"] == {"Catching": 2}
    assert "INCOMPLETE_RATING_CONTEXT" in result["failures"]


def test_projection_is_deterministic_json_safe_and_never_mutates_inputs():
    source = event()
    source["metadata"]["fractions"]["Catching"] = Decimal("0.3")
    before = copy.deepcopy(source)
    one = report.project_event(source, audience="private", doc=DOC)
    assert one == report.project_event(source, audience="private", doc=DOC)
    assert source == before
    json.dumps(one, allow_nan=False)


def test_sanitizer_strips_recursive_unknown_keys_and_pads_form_fields():
    source = report.project_event(event(status="APPLIED"), doc=DOC)
    source["audit"] = {"ceiling": 99}
    source["permanent"]["durability"] = 0.2
    source["permanent"]["rating_changes"]["ceiling"] = 99
    source["development_label"]["seed"] = "secret"
    source["form"].pop("before_ovr_delta")
    source["reasons"].append("ceiling 99")
    result = report.sanitize_public(source)
    assert result is not None
    encoded = json.dumps(result)
    assert "ceiling" not in encoded and "durability" not in encoded and "secret" not in encoded
    assert result["form"]["before_ovr_delta"] is None
    assert report.sanitize_public_report(source) == result


@pytest.mark.parametrize("change", [
    lambda x: x.update(schema_version=True), lambda x: x.update(schema_version=1.0),
    lambda x: x.update(status="FAILED"), lambda x: x.update(identity=[]),
    lambda x: x["calendar"].update(week=-1), lambda x: x["permanent"].update(after_ovr="81"),
    lambda x: x["evidence"].update(reliability=float("nan")),
    lambda x: x["form"].update(after_requested={"seed": 1}),
    lambda x: x["development_label"].update(desired="ceiling=99"),
    lambda x: x["permanent"].update(rating_changes={"Awareness": {"secret": 1}}),
])
def test_sanitizer_rejects_malformed_known_fields(change):
    source = report.project_event(event(), doc=DOC)
    change(source)
    assert report.sanitize_public(source) is None


def test_sanitizer_cannot_turn_proposed_trait_into_verified_trait():
    source = report.project_event(event(), doc=DOC)
    source["development_label"]["actual_after"] = "Star"
    assert report.sanitize_public(source)["development_label"]["actual_after"] is None


def test_audience_is_explicit_public_default_private_opt_in():
    assert "audit" not in report.project_annual(annual(), doc=DOC)
    with pytest.raises(ValueError):
        report.project_event(event(), audience="debug", doc=DOC)


def test_annual_does_not_confuse_unchanged_saved_dev_with_desired_band_label():
    result = report.project_annual(annual(dev_before="Star", dev_after="Star"), doc=DOC)
    assert result["development_label"] == {"desired": None, "actual_before": "Star", "actual_after": None}
    assert report.project_annual(annual(desired_label="Superstar"), doc=DOC)["development_label"]["desired"] == "Superstar"


def test_obsolete_throwaccuracy_term_uses_existing_model_zero_convention():
    doc = copy.deepcopy(DOC)
    doc["archetypes"]["WR_Test"]["terms"]["ThrowAccuracy"] = [0.1, 0]
    result = report.project_event(event(), doc=doc)
    assert result["permanent"]["before_ovr"] == 80
    assert result["permanent"]["after_ovr"] == 81
    assert "OVR_UNAVAILABLE" not in result["failures"]
    assert "ThrowAccuracy" not in result["permanent"]["rating_changes"]


def test_sanitizer_source_qualifiers_are_optional_but_cannot_be_raw_metadata():
    value = report.project_event(event(), doc=DOC)
    value["permanent"]["before_ovr_source"] = {"ceiling": 99}
    assert report.sanitize_public(value) is None


def test_missing_context_does_not_promote_stored_planned_deltas_to_realized_changes():
    source = event()
    source["metadata"].pop("report_context")
    assert report.project_event(source, doc=DOC)["permanent"]["rating_changes"] is None
    source = annual(ratings_before=None, target_ratings=None, ratings_diff={"Catching": 9})
    assert report.project_annual(source, doc=DOC)["permanent"]["rating_changes"] is None


def test_private_offseason_requested_entitlement_remains_separate_from_receipt_delta():
    source = event(kind="OFFSEASON")
    source["metadata"]["decisions"] = []
    source["metadata"]["annual_entitlement"] = {"Catching": 4.2}
    private = report.project_event(source, audience="private", doc=DOC)
    assert private["audit"]["requested_fractional"] == {"Catching": 4.2}
    assert private["permanent"]["rating_changes"]["Catching"] == 2


def test_positive_ceiling_pull_is_explained_as_headroom_not_resistance():
    public = report.project_annual(annual(cause="CEILING", ceiling_term=1.2), doc=DOC)
    assert public["reasons"] == ["POTENTIAL_HEADROOM"]
    assert "DEVELOPMENT_RESISTANCE" not in public["reasons"]
    private = report.project_annual(annual(continuity_term=0.2, mentor_term=0.1), audience="private", doc=DOC)
    assert private["audit"]["term_breakdown"]["additive"]["continuity_term"] == 0.2
    assert private["audit"]["term_breakdown"]["additive"]["mentor_term"] == 0.1
    assert "upkeep_term" not in private["audit"]["term_breakdown"]["additive"]


def test_calibration_and_calculation_audit_are_retained_only_privately():
    calculation = {"unclamped_delta": 9.4, "clamped_delta": 8, "clamp_adjustment": -1.4,
                   "rounded_target_ovr": 88, "rounding_adjustment": 0,
                   "bounded_target_ovr": 88, "boundary_adjustment": 0,
                   "realized_rating_ovr": 87, "rating_target_gap": -1}
    row = annual(calibration_term=-0.76, calculation=calculation)
    public = report.project_annual(row, doc=DOC)
    private = report.project_annual(row, audience="private", doc=DOC)
    assert "calculation" not in public and "audit" not in public
    assert "calibration_term" not in json.dumps(public)
    assert private["audit"]["calculation"] == calculation
    assert private["audit"]["term_breakdown"]["additive"]["calibration_term"] == -0.76
    source = event()
    source["metadata"]["annual_result"] = row
    projected = report.project_event(source, audience="private", doc=DOC)
    assert projected["audit"]["calculation"] == calculation
    assert projected["audit"]["term_breakdown"]["additive"]["calibration_term"] == -0.76
    sanitized = report.sanitize_public(projected)
    assert "calculation" not in json.dumps(sanitized)
    assert "calibration_term" not in json.dumps(sanitized)
