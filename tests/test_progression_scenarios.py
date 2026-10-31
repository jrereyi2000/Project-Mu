"""Cross-module career scenarios on explicit disposable scratch schemas only."""
import copy

import pytest

from engines import progression_checkpoint as cp
from engines import progression_state as state
from engines import progression_policy as policy
from engines import ovr_weights as ow
from tests.test_form_engine import DOC, ratings_for
from tests.test_progression_state import scratch  # isolated, guarded DSN/schema fixture


@pytest.fixture
def career(scratch):
    conn, cur, _, _ = scratch
    cur.execute("""CREATE TABLE player_form (player_id INT, season INT, week INT, form NUMERIC,
                   overlay JSONB, UNIQUE(player_id,season,week))""")
    base = ratings_for("WR", 75)
    ovr = ow.predict_ovr(DOC, "WR", base)[0]
    band = {"floor": ovr - 5, "spread": 3,
            "group_ceilings": {"physical": 90, "technical": ovr, "mental": ovr}}
    state.bootstrap_state(cur, 1, 2034, base, band, "neutral-start", confidence=50, form_neutral=True)
    conn.commit()
    return conn, cur, base, band


def line(n, **change):
    return {"game_id": n, "season": 2034, "week": n, "status": "COMPLETED",
            "game_type": "REGULAR_SEASON", "position_group": "REC", "opportunities": 40,
            "performance_signal": .9, **change}


def snapshot(base):
    p = {"pgid": 101, "position": "WR", "ratings": dict(base), "confidence": 50,
         "archetype_grades": [], "dev_trait": "Normal", "traits": {}}
    ovr = cp.psave.ovr_fields(DOC, "WR", base, [], None)
    p["overall"], p["archetype"] = ovr["OverallRating"], ovr["PlayerType"]
    p["archetype_grades"] = [ovr.get(f"OverallGrade{i}", 0) for i in range(5)]
    return {"players": [p], "league_settings": {}}


def observed(document, prior):
    out = copy.deepcopy(prior)
    p = out["players"][0]
    for edit in document["players"]:
        for field, value in edit["set"].items():
            if field in ("ExperiencePoints", "SkillPoints"):
                continue
            if field.startswith("OverallGrade"):
                p["archetype_grades"][int(field.removeprefix("OverallGrade"))] = value
            elif field == "OverallRating":
                p["overall"] = value
            elif field == "PlayerType":
                p["archetype"] = value
            elif field == "ConfidenceRating":
                p["confidence"] = value
            elif field == "TraitDevelopment":
                p["dev_trait"] = value
            elif field.endswith("Rating"):
                p["ratings"][field.removesuffix("Rating")] = value
    return out


def prepare(cur, games, week, source, **kwargs):
    return cp.prepare_player(cur, player_id=1, pgid=101, earned_season=2034,
                             application_season=2034, as_of_week=week,
                             source_fingerprint=source, player_games=games, team_games=games,
                             position="WR", age=23, doc=DOC, **kwargs)


def apply(cur, event, prior):
    document = cp.document_for_events([event], prior, DOC)
    prepared = {"document": document, "event_ids": [event["event_id"]],
                "source_fingerprint": event["source_fingerprint"], "edits_hash": cp.fingerprint(document)}
    seen = observed(document, prior)
    cp.acknowledge_pass(cur, prepared, seen, output_fingerprint=cp.fingerprint(seen), writer_verified=True)
    return prepared, seen


@pytest.mark.integration
def test_rookie_growth_requires_receipt_and_retry_does_not_stack(career):
    _, cur, base, _ = career
    games = [line(n) for n in range(1, 5)]
    cfg = policy.PolicyConfig(mental_points_per_game=2, maximum_review_probability=0)
    first = prepare(cur, games, 4, "week4", config=cfg)
    assert first["candidate_base"]["Awareness"] > base["Awareness"]
    assert state.load_state(cur, 1)["base_ratings"] == base
    assert state.load_annual_applied(cur, 1, 2034) == {}
    document, seen = apply(cur, first, snapshot(base))
    accepted = state.load_state(cur, 1)
    assert accepted["base_ratings"] == first["candidate_base"]
    revision = accepted["revision"]
    retry = prepare(cur, games, 4, "different-autosave", config=cfg)
    assert retry["event_id"] == first["event_id"]
    cp.acknowledge_pass(cur, document, seen, output_fingerprint=cp.fingerprint(seen), writer_verified=True)
    assert state.load_state(cur, 1)["revision"] == revision
    assert state.load_state(cur, 1)["base_ratings"] == accepted["base_ratings"]


@pytest.mark.integration
def test_temporary_heater_disappears_without_contaminating_base(career):
    _, cur, base, _ = career
    cfg = policy.PolicyConfig(mental_points_per_game=0, technical_points_per_game=0,
                               maximum_review_probability=0)
    games = [line(n) for n in range(1, 4)]
    hot = prepare(cur, games, 3, "hot", form_value=3, config=cfg)
    _, hot_seen = apply(cur, hot, snapshot(base))
    assert hot["overlay"]
    assert state.load_state(cur, 1)["base_ratings"] == base
    # Post-Advance reingest sees overlaid ratings, but authoritative state stays base.
    cold = prepare(cur, games + [line(4, performance_signal=-.5)], 4, "cooled",
                   form_value=0, config=cfg)
    cold_doc, cold_seen = apply(cur, cold, hot_seen)
    assert cold_seen["players"][0]["ratings"] == base
    assert state.load_state(cur, 1)["base_ratings"] == base
    cur.execute("SELECT overlay FROM player_form WHERE player_id=1 AND season=2034 AND week=4")
    assert cur.fetchone()[0] == {}
    assert all("TraitDevelopment" not in e["set"] for e in cold_doc["document"]["players"])


@pytest.mark.integration
def test_three_game_playoff_run_revises_band_once_after_bye(career, monkeypatch):
    _, cur, base, band = career
    # Force the confidence branch to test plumbing, not a random frequency claim.
    monkeypatch.setattr(policy, "_unit_draw", lambda *args: 0.0)
    games = [line(n, week=w, game_type="PLAYOFFS") for n, w in enumerate((20, 21, 23), 1)]
    cfg = policy.PolicyConfig(maximum_review_probability=1)
    event = prepare(cur, games, 23, "playoff-final", season_closed=True,
                    playoff_run_closed=True, config=cfg)
    reviews = [d for d in event["metadata"]["decisions"] if d["kind"] == "REVIEW"]
    assert len(reviews) == 1
    assert event["candidate_band"]["group_ceilings"]["technical"] > band["group_ceilings"]["technical"]
    document, seen = apply(cur, event, snapshot(base))
    accepted = state.load_state(cur, 1)
    assert all("TraitDevelopment" not in e["set"] for e in document["document"]["players"])
    later = prepare(cur, games, 24, "offseason-arrival", season_closed=True,
                    playoff_run_closed=True, config=cfg)
    assert not [d for d in later["metadata"]["decisions"] if d["kind"] == "REVIEW"]
    assert later["candidate_band"] == accepted["band"]


@pytest.mark.integration
def test_failed_readback_cannot_acknowledge_development(career):
    _, cur, base, _ = career
    event = prepare(cur, [line(n) for n in range(1, 5)], 4, "week4",
                    config=policy.PolicyConfig(mental_points_per_game=2))
    document = cp.document_for_events([event], snapshot(base), DOC)
    prepared = {"document": document, "event_ids": [event["event_id"]],
                "source_fingerprint": "week4", "edits_hash": cp.fingerprint(document)}
    with pytest.raises(state.ProgressionStateError, match="readback mismatch"):
        cp.acknowledge_pass(cur, prepared, snapshot(base), output_fingerprint="bad", writer_verified=True)
    assert state.load_state(cur, 1)["base_ratings"] == base
    assert not state.load_annual_applied(cur, 1, 2034)


@pytest.mark.integration
def test_real_save_copy_checkpoint_receipt_and_reingest(scratch, tmp_path):
    import hashlib
    import json
    import shutil
    import subprocess
    from pathlib import Path
    from tests.test_progression_save import SAMPLE_SAVE, SAVE_READER, _sample_ready

    if not _sample_ready():
        pytest.skip("sample save, node or madden-franchise dependency missing")
    conn, cur, _, _ = scratch
    cur.execute("""CREATE TABLE player_form (player_id INT, season INT, week INT, form NUMERIC,
                   overlay JSONB, UNIQUE(player_id,season,week))""")
    source, output = tmp_path / "CAREER-COPY", tmp_path / "CAREER-WRITTEN"
    before_json, after_json, edits_json = (tmp_path / name for name in ("before.json", "after.json", "edits.json"))
    shutil.copyfile(SAMPLE_SAVE, source)
    original_hash = hashlib.sha256(Path(SAMPLE_SAVE).read_bytes()).hexdigest()
    def read(save, dest):
        subprocess.run(["node", str(Path(SAVE_READER) / "pc_save_reader.mjs"), str(save),
                        "--out", str(dest)], check=True, capture_output=True, timeout=300)
        return json.loads(dest.read_text())
    before = read(source, before_json)
    counts = {}
    for player in before["players"]:
        counts[player["pgid"]] = counts.get(player["pgid"], 0) + 1
    player = next(p for p in before["players"] if p["position"] == "WR" and p.get("confidence") == 50
                  and p.get("pgid", 0) > 0 and counts[p["pgid"]] == 1 and p.get("ratings"))
    base = dict(player["ratings"])
    base_ovr = ow.predict_ovr(DOC, "WR", base, current=player["archetype"])[0]
    band = {"floor": max(0, base_ovr - 5), "spread": 3,
            "group_ceilings": {"physical": base_ovr, "technical": min(99, base_ovr + 6),
                               "mental": min(99, base_ovr + 6)}}
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    state.bootstrap_state(cur, 1, 2034, base, band, source_hash, confidence=50, form_neutral=True)
    conn.commit()
    games = [line(n) for n in range(1, 5)]
    inputs = {1: {"pgid": player["pgid"], "position": "WR", "age": player.get("age", 23),
                  "player_games": games, "team_games": games}}
    planned = cp.prepare_pass(cur, before, 2034, 4, source_fingerprint=source_hash,
                              player_inputs=inputs, forms={1: 2})
    event = state._load_event(cur, planned["event_ids"][0])
    assert event["overlay"]
    assert event["candidate_base"] == base  # small learning remains fractional
    assert state.load_state(cur, 1)["base_ratings"] == base
    assert not state.load_annual_applied(cur, 1, 2034)
    edits_json.write_text(json.dumps(planned["document"]))
    writer = subprocess.run(["node", str(Path(SAVE_READER) / "save_writer.mjs"), str(source),
                             str(edits_json), "--out", str(output)], capture_output=True, text=True, timeout=600)
    assert writer.returncode == 0, writer.stderr[-2000:]
    after = read(output, after_json)
    output_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    assert output_hash != source_hash
    # A correctly hashed document cannot acknowledge a different event's targets.
    alternate = cp.prepare_player(cur, player_id=1, pgid=player["pgid"], earned_season=2034,
        application_season=2034, as_of_week=5, source_fingerprint=source_hash,
        player_games=games, team_games=games, position="WR", age=player.get("age", 23), form_value=-2)
    tampered = {**planned, "event_ids": [alternate["event_id"]]}
    with pytest.raises(state.ProgressionStateError, match="authoritative targets"):
        cp.acknowledge_pass(cur, tampered, after, output_fingerprint=output_hash, writer_verified=True)
    assert state.load_state(cur, 1)["base_ratings"] == base
    receipts = cp.acknowledge_pass(cur, planned, after, output_fingerprint=output_hash, writer_verified=True)
    assert receipts[0]["status"] == "APPLIED"
    assert state.load_state(cur, 1)["base_ratings"] == base
    revision = state.load_state(cur, 1)["revision"]
    retry = cp.prepare_pass(cur, after, 2034, 4, source_fingerprint=output_hash,
                            player_inputs=inputs, forms={1: 2})
    assert retry["event_ids"] == []
    assert state.load_state(cur, 1)["revision"] == revision
    assert state.load_state(cur, 1)["base_ratings"] == base
    assert hashlib.sha256(Path(SAMPLE_SAVE).read_bytes()).hexdigest() == original_hash
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
