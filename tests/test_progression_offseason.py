"""Annual remainder reconciliation and scratch receipt replay tests."""
import pytest
from engines import progression_state as state
from engines import progression_offseason as off
from tests.test_progression_state import scratch

BASE = {"Awareness": 70, "Speed": 88}
BAND = {"group_ceilings": {"physical": 90, "technical": 85, "mental": 90}}


def test_offseason_awards_only_remainder_not_another_full_year():
    result = off.reconcile_annual(BASE, {**BASE, "Awareness": 72}, {**BASE, "Awareness": 75},
                                 {"Awareness": 2}, {})
    assert result["candidate_base"]["Awareness"] == 75
    assert result["annual_deltas"] == {"Awareness": 3}


def test_fractional_carry_is_not_added_on_top_of_full_annual_target():
    result = off.reconcile_annual(BASE, {**BASE, "Awareness": 71}, {**BASE, "Awareness": 72.8},
                                 {"Awareness": 1}, {"Awareness": .8})
    assert result["candidate_base"]["Awareness"] == 72
    assert result["annual_deltas"] == {"Awareness": 1}
    assert result["fractions"]["Awareness"] == .8


def test_lower_positive_estimate_does_not_claw_back_applied_learning():
    result = off.reconcile_annual(BASE, {**BASE, "Awareness": 75}, {**BASE, "Awareness": 73},
                                 {"Awareness": 5}, {})
    assert result["candidate_base"]["Awareness"] == 75
    assert result["annual_deltas"] == {}


def test_explicit_decline_can_reconcile_previous_inseason_improvement():
    result = off.reconcile_annual(BASE, {**BASE, "Awareness": 72}, {**BASE, "Awareness": 68},
                                 {"Awareness": 2}, {})
    assert result["candidate_base"]["Awareness"] == 68
    assert result["annual_deltas"] == {"Awareness": -4}


def test_missing_or_invalid_annual_fields_refused():
    with pytest.raises(state.ProgressionStateError):
        off.reconcile_annual(BASE, BASE, {"Awareness": 75}, {}, {})
    with pytest.raises(state.ProgressionStateError):
        off.reconcile_annual(BASE, BASE, {**BASE, "Speed": 100}, {}, {})


@pytest.mark.integration
def test_offseason_receipt_replay_and_dev_gate(scratch):
    _, cur, *_ = scratch
    state.bootstrap_state(cur, 1, 2026, BASE, BAND, "initial", form_neutral=True)
    checkpoint = state.prepare_event(cur, player_id=1, earned_season=2026, application_season=2026,
        kind="CHECKPOINT", ordinal="week8", rules_version="test", source_fingerprint="week8",
        base_revision=0, candidate_base={**BASE, "Awareness": 72}, candidate_band=BAND,
        annual_deltas={"Awareness": 2}, metadata={"annual_start": BASE, "fractions": {"Awareness": .5}})
    state.acknowledge_batch(cur, [checkpoint["event_id"]], source_fingerprint="week8",
        output_fingerprint="week8-written", edits_hash="checkpoint-edits", verified=True)
    assert off.annual_start_ratings(cur, 1, 2026) == BASE
    rows = [{"player_id": 1, "pgid": 101, "target_ratings": {**BASE, "Awareness": 75},
             "dev_after": "X-Factor"}]
    prepared = off.prepare_offseason(cur, rows, earned_season=2026, application_season=2027,
        source_fingerprint="rollover", rules_version="test")
    event = prepared[0]
    assert event["candidate_base"]["Awareness"] == 75
    assert event["annual_deltas"] == {"Awareness": 3}
    assert event["overlay"] == {}
    assert event["metadata"]["actual_dev_write"] is None
    assert state.load_state(cur, 1)["base_ratings"]["Awareness"] == 72
    state.acknowledge_batch(cur, [event["event_id"]], source_fingerprint="rollover",
        output_fingerprint="final-written", edits_hash="offseason-edits", verified=True)
    again = off.prepare_offseason(cur, [{**rows[0], "target_ratings": {**BASE, "Awareness": 99}}],
        earned_season=2026, application_season=2027, source_fingerprint="observed-after-write", rules_version="test")
    assert again[0]["event_id"] == event["event_id"]
    assert again[0]["status"] == "APPLIED"
    assert again[0]["candidate_base"]["Awareness"] == 75
    assert state.load_annual_applied(cur, 1, 2026) == {"Awareness": 5}


@pytest.mark.integration
def test_first_offseason_uses_explicit_bootstrap_start_and_rejects_missing_target(scratch):
    _, cur, *_ = scratch
    state.bootstrap_state(cur, 1, 2026, BASE, BAND, "initial", form_neutral=True)
    assert off.annual_start_ratings(cur, 1, 2026) == BASE
    with pytest.raises(state.ProgressionStateError, match="annual target missing"):
        off.prepare_offseason(cur, [{"player_id": 1, "pgid": 101}], earned_season=2026,
            application_season=2027, source_fingerprint="rollover", rules_version="test")
    assert state.load_events(cur, 1, 2026, status=None) == []


@pytest.mark.integration
def test_full_annual_result_retained_privately_for_verified_c2_persistence(scratch):
    _, cur, *_ = scratch
    state.bootstrap_state(cur, 1, 2026, BASE, BAND, "initial", form_neutral=True)
    row = {"player_id": 1, "pgid": 101, "target_ratings": {**BASE, "Awareness": 73},
           "checkpoint": "OFFSEASON", "durability": .8, "mileage": {"2026": {"wear": 1.2}}}
    events = off.prepare_offseason(cur, [row], earned_season=2026, application_season=2027,
                                  source_fingerprint="rollover", rules_version="test")
    assert events[0]["metadata"]["annual_result"] == row
    assert state.load_state(cur, 1)["base_ratings"] == BASE


@pytest.mark.integration
def test_old_offseason_cannot_replay_after_next_checkpoint_advances_state(scratch):
    _, cur, *_ = scratch
    state.bootstrap_state(cur, 1, 2026, BASE, BAND, "initial", form_neutral=True)
    rows = [{"player_id": 1, "pgid": 101, "target_ratings": {**BASE, "Awareness": 73}}]
    annual = off.prepare_offseason(cur, rows, earned_season=2026, application_season=2027,
                                  source_fingerprint="rollover", rules_version="test")[0]
    state.acknowledge_batch(cur, [annual["event_id"]], source_fingerprint="rollover",
        output_fingerprint="final-written", edits_hash="annual-edits", verified=True)
    newer = state.prepare_event(cur, player_id=1, earned_season=2027, application_season=2027,
        kind="CHECKPOINT", ordinal="week4", rules_version="test", source_fingerprint="new-week4",
        base_revision=1, candidate_base={**BASE, "Awareness": 74}, candidate_band=BAND,
        annual_deltas={"Awareness": 1})
    state.acknowledge_batch(cur, [newer["event_id"]], source_fingerprint="new-week4",
        output_fingerprint="new-week4-written", edits_hash="checkpoint-edits", verified=True)
    with pytest.raises(state.ProgressionStateError, match="historical offseason replay"):
        off.prepare_offseason(cur, rows, earned_season=2026, application_season=2027,
                              source_fingerprint="current-save", rules_version="test")
    assert state.load_state(cur, 1)["base_ratings"]["Awareness"] == 74


@pytest.mark.integration
def test_pending_offseason_cannot_render_after_competing_applied_change(scratch):
    _, cur, *_ = scratch
    state.bootstrap_state(cur, 1, 2026, BASE, BAND, "initial", form_neutral=True)
    rows = [{"player_id": 1, "pgid": 101, "target_ratings": {**BASE, "Awareness": 73}}]
    pending = off.prepare_offseason(cur, rows, earned_season=2026, application_season=2027,
                                   source_fingerprint="rollover", rules_version="test")[0]
    rival = state.prepare_event(cur, player_id=1, earned_season=2026, application_season=2026,
        kind="CHECKPOINT", ordinal="final-playoffs", rules_version="test", source_fingerprint="playoff",
        base_revision=0, candidate_base={**BASE, "Awareness": 71}, candidate_band=BAND,
        annual_deltas={"Awareness": 1})
    state.acknowledge_batch(cur, [rival["event_id"]], source_fingerprint="playoff",
        output_fingerprint="playoff-written", edits_hash="playoff-edits", verified=True)
    with pytest.raises(state.ProgressionStateError, match="pending offseason has a stale"):
        off.prepare_offseason(cur, rows, earned_season=2026, application_season=2027,
                              source_fingerprint="rollover", rules_version="test")
    assert state._load_event(cur, pending["event_id"])["status"] == "PREPARED"
    assert state.load_state(cur, 1)["base_ratings"]["Awareness"] == 71
