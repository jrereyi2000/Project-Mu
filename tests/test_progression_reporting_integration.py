"""Receipt reporting reflects verified state, preserves retries and cannot leak private inputs."""
import json
import pytest
from engines import progression_checkpoint as cp
from engines import progression_offseason as offseason
from engines import progression_report as report
from engines import progression_state as state
from tests.test_progression_scenarios import career, scratch, snapshot, observed, line

pytestmark = pytest.mark.integration


def prepare(cur, base, week=4):
    games=[line(n) for n in range(1,week+1)]
    return cp.prepare_pass(cur,snapshot(base),2034,week,source_fingerprint=f"source-{week}",
        player_inputs={1:{"pgid":101,"position":"WR","player_games":games,"team_games":games}},
        forms={1:2})


def assert_public(value):
    text=json.dumps(value)
    for hidden in ("before_base","before_band","group_ceilings","durability","source_fingerprint",
                   "learning-variation","label_thresholds","audit","report_context","target_ratings"):
        assert hidden not in text


def test_preparation_explains_candidates_without_publishing_applied_reports(career):
    _,cur,base,_=career
    prepared=prepare(cur,base)
    public,=cp.explain_prepared(cur,prepared)
    private,=cp.explain_prepared(cur,prepared,audience="private")
    assert public["status"]=="PROPOSED"
    assert public["development_label"]["actual_before"]=="Normal"
    assert public["development_label"]["actual_after"] is None
    assert private["audit"]["before_ratings"]==base
    assert private["audit"]["requested_fractional"]
    assert_public(public)
    event=state._load_event(cur,prepared["event_ids"][0])
    assert event["status"]=="PREPARED" and "report_public" not in event["metadata"]
    assert state.load_state(cur,1)["base_ratings"]==base


def test_failed_write_never_publishes_report_or_awards_growth(career):
    _,cur,base,_=career
    prepared=prepare(cur,base)
    with pytest.raises(state.ProgressionStateError,match="readback"):
        cp.acknowledge_pass(cur,prepared,snapshot(base),output_fingerprint="failed",writer_verified=True)
    event=state._load_event(cur,prepared["event_ids"][0])
    assert event["status"]=="PREPARED" and "report_public" not in event["metadata"]
    assert state.load_state(cur,1)["base_ratings"]==base


def test_verified_report_and_generic_proposal_retry_are_idempotent(career):
    _,cur,base,_=career
    prepared=prepare(cur,base)
    original=state._load_event(cur,prepared["event_ids"][0])
    output=observed(prepared["document"],snapshot(base))
    applied,=cp.acknowledge_pass(cur,prepared,output,output_fingerprint="verified",writer_verified=True)
    public=applied["metadata"]["report_public"]
    assert public["status"]=="APPLIED"
    assert public["development_label"]["actual_after"]=="Normal"
    assert public["form"]["after_requested"]==2
    assert public["form"]["after_ovr_delta"]==2
    assert_public(public)
    kwargs={key:original[key] for key in ("player_id","earned_season","application_season","kind","ordinal",
        "rules_version","source_fingerprint","base_revision","candidate_base","candidate_band","overlay",
        "evidence_ids","evidence_purpose","annual_deltas","metadata")}
    replay=state.prepare_event(cur,**kwargs)
    assert replay["status"]=="APPLIED" and replay["metadata"]["report_public"]==public
    revision=state.load_state(cur,1)["revision"]
    again,=cp.acknowledge_pass(cur,prepared,output,output_fingerprint="verified",writer_verified=True)
    assert again["metadata"]["report_public"]==public
    assert state.load_state(cur,1)["revision"]==revision


def test_projection_failure_rolls_back_receipt_and_form(career,monkeypatch):
    _,cur,base,_=career
    prepared=prepare(cur,base)
    def fail(*args,**kwargs):
        raise ValueError("projection failure")
    monkeypatch.setattr(report,"project_event",fail)
    with pytest.raises(ValueError,match="projection failure"):
        cp.acknowledge_pass(cur,prepared,observed(prepared["document"],snapshot(base)),
                            output_fingerprint="out",writer_verified=True)
    assert state._load_event(cur,prepared["event_ids"][0])["status"]=="PREPARED"
    assert state.load_state(cur,1)["revision"]==0
    cur.execute("SELECT count(*) FROM player_form")
    assert cur.fetchone()[0]==0


def test_annual_report_has_same_schema_and_removes_previous_form(career):
    _,cur,base,_=career
    prepared=prepare(cur,base)
    output=observed(prepared["document"],snapshot(base))
    weekly,=cp.acknowledge_pass(cur,prepared,output,output_fingerprint="weekly",writer_verified=True)
    row={"player_id":1,"pgid":101,"position":"WR","actual_dev_before":"Normal",
         "target_ratings":{**base,"Awareness":base["Awareness"]+2}}
    annual,=offseason.prepare_offseason(cur,[row],earned_season=2034,application_season=2035,
                                      source_fingerprint="off",rules_version=cp.RULES_VERSION)
    document=cp.document_for_events([annual],output)
    envelope={"document":document,"event_ids":[annual["event_id"]],
              "source_fingerprint":"off","edits_hash":cp.fingerprint(document)}
    applied,=cp.acknowledge_pass(cur,envelope,observed(document,output),
                               output_fingerprint="off-out",writer_verified=True)
    public=applied["metadata"]["report_public"]
    assert public.keys()==weekly["metadata"]["report_public"].keys()
    assert public["calendar"]=={"earned_season":2034,"application_season":2035,"week":0}
    assert public["form"]["before_requested"]==2 and public["form"]["after_requested"]==0
    assert public["form"]["after_ovr_delta"]==0
    assert_public(public)
