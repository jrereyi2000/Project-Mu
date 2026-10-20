"""Scouting consensus, events and read helpers (docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md Phase 2).
Pure: no DB (the frozen 2037 class fixture stands in for draft_prospects)."""
import csv
import os
import re
import statistics
from unittest import mock

import pytest

from engines import scouting_engine as se
from engines import scouting_events as ev
from engines import scouting_value as sv
from engines.scouting_read import public_payload, visible_step

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "draft_class_2037.csv")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_fixture():
    with open(FIXTURE) as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ("id", "age", "ovr", "projected_pick", "height_in", "weight"):
            r[k] = int(r[k]) if r[k] not in ("", None) else None
    return se.build_class(rows, 2037, 4)


@pytest.fixture(scope="module")
def cls():
    return load_fixture()


@pytest.fixture(scope="module")
def season(cls):
    sim = se.chain(cls, 4, 27, 2037)
    events = {s: se.step_events(cls, sim[s], s, 2037) for s in sim}
    return sim, events


def test_same_seed_same_step(cls):
    a = se.chain(cls, 4, 9, 2037)[9]
    b = se.chain(cls, 4, 9, 2037)[9]
    assert a == b
    assert se.step_events(cls, a, 9, 2037) == se.step_events(cls, b, 9, 2037)


def test_chain_from_a_stored_step_matches(cls):
    full = se.chain(cls, 4, 12, 2037)
    start = (8, {k: r["err"] for k, r in full[8].items()})
    resumed = se.chain(cls, 4, 12, 2037, start=start)
    for k, r in full[12].items():
        assert abs(resumed[12][k]["err"] - r["err"]) < 0.01     # stored errs are rounded to 3 places


def test_rerun_without_replace_writes_nothing():
    cur = mock.MagicMock()
    with mock.patch.object(se, "current_step", return_value=(2036, 5)), \
         mock.patch.object(se, "stored_steps", return_value=[4, 5]), \
         mock.patch.object(se, "compute") as compute, mock.patch.object(se, "_write_step") as write:
        assert se.run(cur, 2037, 5, log=lambda *a: None) is None
    compute.assert_not_called()
    write.assert_not_called()


def test_noise_sd_follows_schedule_and_floor(cls):
    with mock.patch.object(se, "SEED_ALPHA", 0.0):
        sims = [se.chain(cls[:120], 4, 27, 2037, salt=str(i)) for i in range(25)]
    for step in (4, 10, 16, 22, 27):
        errs = [r["err"] for sim in sims for r in sim[step].values()]
        sd = statistics.pstdev(errs)
        assert abs(sd - se.sigma_at(step, 4)) < 0.2 * se.sigma_at(step, 4)
        assert sd >= se.SIGMA_DRAFT * 0.9
    assert se.sigma_at(27, 4) == se.SIGMA_DRAFT and se.sigma_at(40, 4) == se.SIGMA_DRAFT
    assert all(se.tau_at(s, 4) > 0 for s in range(5, 28))


def test_first_consensus_leans_on_madden_board(cls):
    first = se.chain(cls, 4, 4, 2037)[4]
    pick = {p["key"]: p["projected_pick"] for p in cls}
    true = {p["key"]: p["true_rank"] for p in cls}
    rank = {k: r["rank"] for k, r in first.items()}
    keys = list(rank)
    d_madden = sum(abs(rank[k] - pick[k]) for k in keys)
    d_true = sum(abs(rank[k] - true[k]) for k in keys)
    assert d_madden < d_true                       # the week-4 board looks more like Madden's PICK order than V


def test_risers_and_fallers_against_previous_step(cls, season):
    sim, _ = season
    up, down = se.risers_fallers(sim[10], limit=5)
    for k, r in up:
        assert r["delta_rank"] > 0 and sim[9][k]["rank"] - sim[10][k]["rank"] == r["delta_rank"]
    for k, r in down:
        assert r["delta_rank"] < 0
    assert all(r["delta_rank"] is None for r in sim[4].values())


def test_events_respect_policy(cls, season):
    sim, events = season
    n = len(cls)
    by = {p["key"]: p for p in cls}
    kinds = set()
    for step, per in events.items():
        for key, evs in per.items():
            p = by[key]
            group = ev.stat_group(p["position"])
            v_pct = 100.0 * (1 - (p["true_rank"] - 1) / n)
            members = {sv.colleges()["schools"][m]["display"]
                       for m in (sv.conference_members(p["conference"]) if p["conference"] else [])}
            for e in evs:
                kinds.add(e["kind"])
                pl = e["payload"]
                assert e["headline"] and e["blurb"]
                assert "OVR" not in e["blurb"] and "overall rating" not in e["blurb"].lower()
                if e["kind"] == "GAME":
                    assert set(pl["stats"]) == set(ev.STAT_KEYS[group])
                    assert abs(pl["_production_pct"] - v_pct) <= ev.PRODUCTION_BAND + 1e-6
                    if pl["game_type"] in ("REGULAR", "TITLE"):
                        assert pl["opponent"] in members or pl["opponent"] == "a non-conference opponent"
                    assert ev.phase(step) in ("REGULAR", "CONF_CHAMP", "BOWLS")
                if e["kind"] == "COMBINE":
                    m = pl["measurables"]
                    assert m["height_in"] == p["height_in"] and m["weight"] == p["weight"] and step == 24
                if e["kind"] == "DECLARE":
                    assert p["age"] == 21
                if e["kind"] == "STORYLINE" and pl.get("subtype") == "injury":
                    assert step <= ev.INJURY_LAST_STEP and pl["missed_games"] == 0 and pl["status"] == "day-to-day"
                if e["kind"] == "MEDICAL":
                    assert pl["result"] == "cleared"
                text = (e["headline"] + " " + e["blurb"]).lower()
                assert not re.search(r"\b(arrest|charged|suspend|freshman|torn|surgery|out for)", text)
    assert {"GAME", "STORYLINE", "DECLARE", "COMBINE", "PRO_DAY"} <= kinds


def test_class_years_are_age_legal(cls):
    for p in cls:
        assert p["class_year"] in sv.CLASS_YEARS[min(max(p["age"], 21), 24)]


def test_quiet_week_has_no_event_below_texture():
    p = {"key": "2037:X:WR", "full_name": "X", "position": "WR", "college": "GEORGIA", "school": sv.school("GEORGIA"),
         "conference": "SEC", "age": 22, "class_year": "senior", "height_in": 72, "weight": 200, "v_pct": 40.0}
    assert ev.generate(6, p, 0.1, 300, 2037) == []
    assert [e["kind"] for e in ev.generate(6, p, 0.1, 30, 2037)] in (["GAME"], [])   # texture (or a bye)


def test_unmapped_school_plays_non_conference():
    sched = ev.schedule("NOWHERE TECH", None, 2037)
    assert set(sched.values()) <= {"a non-conference opponent", None}


def test_public_payload_strips_hidden_keys():
    assert public_payload({"stats": {}, "_production_pct": 50}) == {"stats": {}}


def test_view_exposes_no_hidden_column():
    sql = open(os.path.join(ROOT, "db", "migrations", "2026_12_12_scouting.sql")).read()
    view = sql[sql.index("CREATE OR REPLACE VIEW vw_scout_board_public"):]
    view = view[:view.index(";")]
    select = re.sub(r"ORDER BY[^)]*", "", view.split("FROM scout_consensus")[0])    # ordering isn't output
    for hidden in ("true_value", "true_rank", "err", "impact", "ovr", "team_grade", "value_inputs"):
        assert not re.search(rf"\b{hidden}\b", select), hidden


def test_visible_step_rules():
    steps = [(4, 2036, 4, None), (5, 2036, 5, None), (23, 2036, 23, None), (24, 2036, 24, 1), (27, 2036, 24, 4)]
    assert visible_step(steps, 2036, 5) == 5                 # the Week 5 run's board is visible to Week 5
    assert visible_step(steps, 2036, 4) == 4
    assert visible_step(steps, 2036, 24, 1) == 24            # offseason: by stage
    assert visible_step(steps, 2036, 24, 4) == 27
    assert visible_step(steps, 2036, None) == 27
    assert visible_step(steps, 2036, 3) is None


def test_offseason_steps_map_to_24_27():
    cur = mock.MagicMock()
    with mock.patch("engines.gm_trade_engine.league_window", return_value=(2036, 24, "OFFSEASON")):
        for stage, step in ((None, 23), (1, 24), (2, 25), (3, 26), (4, 27), (5, None)):
            with mock.patch("engines.gm_trade_engine.read_stage", return_value=stage):
                assert se.current_step(cur) == (2036, step)
    with mock.patch("engines.gm_trade_engine.league_window", return_value=(2036, 9, "IN_SEASON")):
        assert se.current_step(cur) == (2036, 9)
    assert ev.phase(24) == "COMBINE" and ev.phase(25) == ev.phase(26) == "PRO_DAY" and ev.phase(27) == "DRAFT_WEEK"


def test_visits_are_team_scoped(cls, season):
    sim, events = season
    from engines import scouting_boards as sb
    ctxs = {tid: sb.team_context({"team_id": tid, "needs": [{"group": "QB", "score": 5, "slot_gap": 3}]})
            for tid in (5, 6, 7)}
    out = {"cls": cls, "cons": sim[25], "events": {}, "first_step": 4}
    cur = mock.MagicMock()
    cur.fetchall.return_value = [(5, "AAA", "Alphas"), (6, "BBB", "Betas"), (7, "CCC", "Gammas")]
    with mock.patch.object(se, "team_contexts", return_value=ctxs), \
         mock.patch.object(se, "stored_visits", return_value={}), \
         mock.patch.object(se, "volatility", return_value={}):
        se.default_extras(cur, out, 25, 2036)
    visits = [e for evs in out["events"].values() for e in evs if e["kind"] == "VISIT"]
    assert len(visits) == 3 * 6 and all(e["team_id"] in (5, 6, 7) for e in visits)
    for evs in out["events"].values():                               # one seq per visit to the same prospect
        seqs = [e["seq"] for e in evs if e["kind"] == "VISIT"]
        assert seqs == list(range(len(seqs)))
    assert {r[2] for r in out["boards"]} == {5, 6, 7} and len(out["boards"]) == 3 * 100
    assert all(e["kind"] != "VISIT" for evs in events[25].values() for e in evs)
