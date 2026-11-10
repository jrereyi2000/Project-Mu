"""Public hook, authoritative observation separation and annual integration contracts."""
import json
from unittest.mock import patch
import pytest
from engines import progression as pg
from engines import progression_checkpoint as cp
from engines import progression_state as state
from engines import progression_inputs as inputs
from engines import progression_offseason as offseason
from pipeline import drift_check as drift
from tests.test_progression_scenarios import career, scratch, snapshot, observed
from tests.test_form_engine import DOC


def test_hook_requires_bound_observation_and_receipt_context():
    with pytest.raises(ValueError, match="verified receipt"):
        pg.in_season_checkpoint(None, 2034, 4)


@pytest.mark.parametrize("form", [3.01, -4, float("nan"), float("inf")])
def test_prepare_refuses_unbounded_form(form):
    with pytest.raises(ValueError, match="form must"):
        cp.prepare_player(None, player_id=1, pgid=101, earned_season=2034,
            application_season=2034, as_of_week=4, source_fingerprint="source",
            player_games=[], team_games=[], position="WR", form_value=form)


def test_weekly_adapter_forwards_supported_inputs_and_filters_absent_players(monkeypatch):
    data = {1: {"pgid": 101, "notes": ["known proxy"], "supported_skills": []},
            2: {"pgid": None}, 3: {"pgid": 999}}
    monkeypatch.setattr(inputs, "fetch_inputs", lambda *args: data)
    monkeypatch.setattr(cp.form, "weekly_form", lambda *args: {1: 2})
    with patch.object(cp, "prepare_pass", return_value={"prepared": True}) as prepare:
        result = cp.prepare_weekly_pass(None, {"players": [{"pgid": 101}]}, 2034, 4,
                                        source_fingerprint="source")
    assert result == {"prepared": True}
    assert prepare.call_args.kwargs["player_inputs"] == {1: data[1]}
    assert prepare.call_args.kwargs["forms"] == {1: 2}


@pytest.mark.integration
def test_bootstrap_newcomers_seeds_only_the_players_this_load_created(career):
    """A drafted rookie failed 2027 week 1 with "player N: explicit neutral bootstrap required" because nobody
    seeded players who join after setup (madden_tune_p2g; the live league's first rookie class would hit it too).
    The newcomer path seeds only the created ids, at the league year they join, and leaves existing state alone."""
    conn, cur, base, _ = career
    before = snapshot(base)
    newcomer = dict(before["players"][0], pgid=102, confidence=61)
    two = {"players": before["players"] + [newcomer], "league_settings": {}}
    with pytest.raises(state.ProgressionStateError, match="player 2: explicit neutral bootstrap required"):
        cp.prepare_player(cur, player_id=2, pgid=102, earned_season=2034, application_season=2034,
                          as_of_week=4, source_fingerprint="save", player_games=[], team_games=[],
                          position="WR", form_value=0.0, doc=DOC)
    rows = cp.bootstrap_newcomers(cur, two, 2034, source_fingerprint="save", pgid_by_player={2: 102})
    assert len(rows) == 1
    assert rows[0]["application_season"] == 2034 and rows[0]["base_ratings"] == base
    assert state.load_state(cur, 1)["source_fingerprint"] == "neutral-start"    # the setup cohort is untouched
    assert cp.bootstrap_newcomers(cur, two, 2034, source_fingerprint="save", pgid_by_player={2: 102}) == []
    event = cp.prepare_player(cur, player_id=2, pgid=102, earned_season=2034, application_season=2034,
                              as_of_week=4, source_fingerprint="save", player_games=[], team_games=[],
                              position="WR", form_value=0.0, doc=DOC)
    assert event["status"] in ("PREPARED", "APPLIED")


@pytest.mark.integration
def test_ingested_effective_ratings_cannot_replace_authoritative_base(career):
    conn, cur, base, _ = career
    cur.execute("CREATE TABLE player_attributes (id SERIAL,player_id INT,season INT,ratings JSONB)")
    observation = {**base, "Awareness": base["Awareness"] + 2}
    cur.execute("INSERT INTO player_attributes(player_id,season,ratings) VALUES(1,2034,%s::jsonb)",
                (json.dumps(observation),))
    # Even an erroneous prepared legacy overlay cannot attach itself to bootstrap state.
    cur.execute("INSERT INTO player_form VALUES(1,2034,4,2,%s::jsonb)",
                (json.dumps({"Awareness": 2}),))
    assert pg.fetch_player_attributes_map(cur, 2034)[1] == base
    assert drift.fetch_base_ratings(cur, 2034)[1] == base
    assert drift.fetch_form_overlays(cur, 2034, 4)[1] == {}
    cur.execute("SELECT ratings FROM player_attributes")
    assert cur.fetchone()[0] == observation


@pytest.mark.integration
def test_offseason_adapter_uses_annual_start_and_subtracts_verified_growth(career, monkeypatch):
    conn, cur, base, band = career
    event = state.prepare_event(cur, player_id=1, earned_season=2034,application_season=2034,
        kind="CHECKPOINT", ordinal="early", rules_version="v1",source_fingerprint="in",
        base_revision=0,candidate_base={**base,"Awareness":base["Awareness"]+1},
        candidate_band=band,annual_deltas={"Awareness":1},
        metadata={"annual_start":base,"fractions":{}})
    state.acknowledge_batch(cur,[event["event_id"]],source_fingerprint="in",output_fingerprint="applied",
                            edits_hash="hash",verified=True)
    monkeypatch.setattr(cp.psave,"fetch_pgid_map",lambda cur:{1:101})
    def annual(conn, season, **kwargs):
        assert kwargs["baseline_ratings"][1] == base
        return {"results":[{"player_id":1,"position":"WR",
                            "target_ratings":{**base,"Awareness":base["Awareness"]+2}}]}
    monkeypatch.setattr(pg,"run_progression",annual)
    before = state.load_state(cur,1)
    prepared = cp.prepare_offseason_pass(cur,snapshot(before["base_ratings"]),2035,
                                         source_fingerprint="offseason")
    proposed = state._load_event(cur,prepared["event_ids"][0])
    assert proposed["annual_deltas"]["Awareness"] == 1
    assert proposed["candidate_base"]["Awareness"] == base["Awareness"]+2
    assert proposed["overlay"] == {}
    assert state.load_state(cur,1) == before


@pytest.mark.integration
def test_old_checkpoint_cannot_render_targets_after_a_newer_receipt(career):
    _, cur, base, band = career
    from tests.test_progression_scenarios import line
    games = [line(n) for n in range(1,9)]
    prepared = cp.prepare_pass(cur,snapshot(base),2034,4,source_fingerprint="week4",
        player_inputs={1:{"pgid":101,"player_games":games[:4],"team_games":games[:4],"position":"WR"}},forms={1:2})
    output = observed(prepared["document"],snapshot(base))
    cp.acknowledge_pass(cur,prepared,output,output_fingerprint="out4",writer_verified=True)
    newer = cp.prepare_pass(cur,output,2034,8,source_fingerprint="week8",
        player_inputs={1:{"pgid":101,"player_games":games,"team_games":games,"position":"WR"}})
    cp.acknowledge_pass(cur,newer,observed(newer["document"],output),output_fingerprint="out8",writer_verified=True)
    with pytest.raises(state.ProgressionStateError,match="historical checkpoint"):
        cp.prepare_pass(cur,output,2034,4,source_fingerprint="week4",player_inputs={1:{"pgid":101,
            "player_games":games[:4],"team_games":games[:4],"position":"WR"}})


@pytest.mark.integration
def test_backfilled_technical_checkpoint_cannot_use_later_supported_skills(career,monkeypatch):
    _,cur,base,_ = career
    from tests.test_progression_scenarios import line
    games = [line(n,supported_skills=["Catching"] if n>=10 else []) for n in range(1,19)]
    calls=[]
    original=cp.policy.evaluate_development
    def capture(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(cp.policy,"evaluate_development",capture)
    cp.prepare_pass(cur,snapshot(base),2034,18,source_fingerprint="end",player_inputs={1:{"pgid":101,
        "player_games":games,"team_games":games,"position":"WR","season_closed":True,
        "supported_skills":["Catching"]}})
    technical=[call for call in calls if call["checkpoint_kind"]=="TECHNICAL"]
    assert technical and not technical[0]["supported_skills"]
    assert "Catching" in technical[-1]["supported_skills"]


@pytest.mark.integration
def test_full_annual_receipt_preserves_position_injury_band_and_summary(career,monkeypatch):
    _,cur,base,band=career
    from engines import ovr_weights as ow
    monkeypatch.setattr(cp.psave,"fetch_pgid_map",lambda cur:{1:101})
    groups={**band["group_ceilings"],"physical":band["group_ceilings"]["physical"]-2}
    row={"player_id":1,"position":"WR","checkpoint":"OFFSEASON","age_term":0,
         "target_ratings":{**base,"Awareness":base["Awareness"]+1},
         "floor":band["floor"],"spread":band["spread"],"group_ceilings":groups}
    monkeypatch.setattr(pg,"run_progression",lambda *args,**kwargs:{"results":[row]})
    recorded=[]
    monkeypatch.setattr(pg,"store_progression",lambda cur,rows,season:recorded.extend(rows))
    prepared=cp.prepare_offseason_pass(cur,snapshot(base),2035,source_fingerprint="annual")
    assert not recorded
    cp.acknowledge_pass(cur,prepared,observed(prepared["document"],snapshot(base)),
                        output_fingerprint="annual-out",writer_verified=True)
    assert len(recorded)==1 and recorded[0]["position"]=="WR"
    assert recorded[0]["group_ceilings"]["physical"]==groups["physical"]
    assert state.load_state(cur,1)["band"]["group_ceilings"]["physical"]==groups["physical"]
    assert recorded[0]["new_ovr"]==ow.predict_ovr(cp.ow.load_weights(),"WR",row["target_ratings"])[0]


@pytest.mark.integration
def test_weekly_progression_cannot_skip_offseason_reconciliation(career):
    _,cur,_,_=career
    with pytest.raises(state.ProgressionStateError,match="reconcile offseason"):
        cp.prepare_player(cur,player_id=1,pgid=101,earned_season=2035,application_season=2035,
            as_of_week=4,source_fingerprint="future",player_games=[],team_games=[],position="WR")


def test_normalize_save_ratings_clamps_madden_100s_only():
    # CAREER-MUBASE ships Jerry Rice with four ratings at 100; the engine model is 0..99.
    saved = {"Catching": 100, "Speed": 87, "Awareness": 99}
    assert cp.normalize_save_ratings(saved) == {"Catching": 99, "Speed": 87, "Awareness": 99}
    # Non-integers are the guard's business, not the normaliser's.
    assert cp.normalize_save_ratings({"Speed": 99.5}) == {"Speed": 99.5}
    assert cp.normalize_save_ratings(None) == {}
