"""CPU coach-promise watcher (AGENT_LOOPS_FOLLOWUPS_PLAN.md §2.1, U1). Offline: no database, no model.

`promise_tick` runs over replacement adapters (small cursor wrappers monkeypatched here) with a MemoryStore for
the decisions, and `step_coach_promises` over a fake connection. The policy draw is pinned so a PROMISE is chosen
when it is offered; the review resolution is still the engine's (chart depth / injury).
"""
import json
import os

import pytest

from engines.agent_loops import base, coach, watcher
from engines.agent_loops import watcher as w
from pipeline import watch_save as ws
from personality import relationship_events as rev

LEAGUE = 7
SEASON = 2026
U = "mu:11111111-1111-4111-8111-111111111111"
COACH_REF = {"kind": "person", "universe_id": U, "role": "coach", "persona_id": "coach-1"}
PLAYER_REF = {"kind": "person", "universe_id": U, "role": "player", "persona_id": "player-7"}
REGISTRY = {"team:5": {"abbr": "CAR"}}
PLAYER = {"player_id": 7, "name": "Name7 Last7"}
COACH_VIEW = {"status": "stored", "sliders": {"warmth": 10, "drive": 5}, "traits": ["PLAYERS_COACH"],
              "persona_id": "coach-1"}
SATISFIED = "satisfied"
UNSATISFIED = "unsatisfied"


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    monkeypatch.delenv(ws.COACH_PROMISES_ENV, raising=False)


@pytest.fixture(autouse=True)
def promise_policy(monkeypatch):
    """The seeded policy draw takes PROMISE whenever it is offered; review decisions have one option."""
    def choose(options, persona, weights, rng):
        keys = [o["key"] if isinstance(o, dict) else o.key for o in options]
        return "PROMISE" if "PROMISE" in keys else keys[0]
    monkeypatch.setattr(base, "policy_choice", choose)


def role_weeks(assessments):
    return [{"tick": 3 + i, "assessment": a} for i, a in enumerate(assessments)]


class World:
    """A CPU team 5 (HC 41, player 7), a user team 9, and the charts/history the tick reads."""

    def __init__(self):
        self.store = base.MemoryStore()
        self.teams = [5, 9]
        self.roster = {5: [(7, PLAYER["name"], 7007)]}
        self.head_coach = {5: 41}
        self.views = {41: COACH_VIEW}
        self.history = {(5, 7): [{"tick": 3, "assessment": SATISFIED},
                                 {"tick": 4, "assessment": UNSATISFIED},
                                 {"tick": 5, "assessment": UNSATISFIED}]}
        self.charts = {(5, 4): {"WR": [7]}, (5, 5): {"WR": [8]}}
        self.injured = set()
        self.appends = []

    def install(self, monkeypatch, mode="shadow"):
        self.mode = mode
        monkeypatch.setattr(w, "_pgid_namespace", lambda cur, uid: "save-pgid:1")
        monkeypatch.setattr(w, "_teams", lambda cur: list(self.teams))
        monkeypatch.setattr(w, "_roster", lambda cur, team_id, season: list(self.roster.get(team_id, [])))
        monkeypatch.setattr(w, "_head_coach", lambda cur, team_id, season: self.head_coach.get(team_id))
        monkeypatch.setattr(w, "_coach_view", lambda cur, uid, cid: self.views.get(cid))
        monkeypatch.setattr(w, "_coach_ref", lambda cur, uid, cid: COACH_REF if cid is not None else None)
        monkeypatch.setattr(w, "_player_persona",
                            lambda cur, uid, pgid, ns: PLAYER_REF if pgid is not None else None)

        def role_history(cur, uid, ref, team_id, season, tick):
            if ref is None:
                return [], []
            hist = self.history.get((team_id, 7), [])
            return hist, [f"role:{team_id}:{h['tick']}" for h in hist]

        monkeypatch.setattr(w, "_role_history", role_history)
        monkeypatch.setattr(w, "_madden_chart",
                            lambda cur, team_id, season, tick: self.charts.get((team_id, tick), {}))
        monkeypatch.setattr(w, "_injured_ids", lambda cur, season, tick, snapshot: set(self.injured))

        def append(cur, events, *, team_registry):
            self.appends.append((list(events), team_registry))
            return [{"status": "appended"}]

        monkeypatch.setattr(coach, "append_events", append)
        monkeypatch.setattr("engines.relationship_producers.sources.team_registry", lambda cur: REGISTRY)

    def tick(self, mode=None, tick=5, user_team_ids=(), snapshot=None):
        return watcher.promise_tick(None, league_id=LEAGUE, season=SEASON, tick=tick, universe_id=U,
                                    mode=mode or self.mode, user_team_ids=user_team_ids, snapshot=snapshot,
                                    store=self.store)


def make_promise(store, tick=4):
    """A PROMISE decision recorded at an earlier tick (review_tick = tick + 3)."""
    opts = coach.promise_options(PLAYER, role_weeks([UNSATISFIED, UNSATISFIED]), "WR", tick=tick)
    return coach.decide_promise(store, league_id=LEAGUE, season=SEASON, tick=tick, team_id=5,
                                coach_view=COACH_VIEW, player=PLAYER, options=opts, llm=None, seen=False,
                                store=store, universe_id=U, issuer=COACH_REF, beneficiary=PLAYER_REF)


def promise_rows(store):
    return store.find(LEAGUE, SEASON, "PROMISE")


# -------------------------------------------------------------------------------------------- creation ---
def test_one_unsatisfied_week_gives_no_promise(monkeypatch):
    world = World()
    world.history[(5, 7)] = role_weeks([SATISFIED, UNSATISFIED])
    world.install(monkeypatch, "shadow")
    res = world.tick()
    assert (res["made"], res["errors"]) == (0, 0)
    assert promise_rows(world.store) == []
    assert world.appends == []


def test_two_unsatisfied_weeks_offers_none_and_promise(monkeypatch):
    world = World()
    world.install(monkeypatch, "shadow")
    res = world.tick()
    rows = promise_rows(world.store)
    assert res["made"] == 1 and len(rows) == 1
    assert [o["key"] for o in rows[0]["options"]] == ["NONE", "PROMISE"]
    assert rows[0]["chosen_key"] == "PROMISE" and rows[0]["subject_key"] == "player:7:promise"
    assert rows[0]["facts"]["slot"] == "WR" and rows[0]["facts"]["review_tick"] == 8
    assert rows[0]["cause_refs"] == ["role:5:3", "role:5:4", "role:5:5"]


def test_no_lost_starting_slot_gives_no_promise(monkeypatch):
    world = World()
    world.charts[(5, 5)] = {"WR": [7, 9]}          # he is still the depth-1 starter
    world.install(monkeypatch, "shadow")
    res = world.tick()
    assert res["made"] == 0 and promise_rows(world.store) == []


def test_a_missing_chart_gives_no_promise(monkeypatch):
    world = World()
    world.charts.pop((5, 4))                        # no chart last tick
    world.install(monkeypatch, "shadow")
    assert world.tick()["made"] == 0
    assert promise_rows(world.store) == []


def test_an_open_promise_gives_no_second_offer(monkeypatch):
    world = World()
    world.install(monkeypatch, "shadow")
    make_promise(world.store, tick=4)               # review_tick 7, still open at tick 5
    assert len(coach.open_promises(world.store, LEAGUE, SEASON)) == 1
    res = world.tick()
    assert res["made"] == 0 and len(promise_rows(world.store)) == 1


def test_jr_user_teams_are_skipped(monkeypatch):
    world = World()
    world.install(monkeypatch, "shadow")
    res = world.tick(user_team_ids={5, 9})
    assert res["made"] == 0 and promise_rows(world.store) == [] and world.appends == []


def test_unknown_user_teams_skip_creation(monkeypatch):
    world = World()
    world.install(monkeypatch, "shadow")
    res = world.tick(user_team_ids=None)
    assert res["made"] == 0 and promise_rows(world.store) == []


def test_off_does_nothing(monkeypatch):
    world = World()
    world.install(monkeypatch, "off")
    assert world.tick(mode="off") == {"status": "off"}
    assert promise_rows(world.store) == []


# ---------------------------------------------------------------------------------------- envelopes ---
def test_shadow_records_the_promise_and_appends_nothing(monkeypatch):
    world = World()
    world.install(monkeypatch, "shadow")
    res = world.tick()
    assert res["made"] == 1
    assert promise_rows(world.store)[0]["chosen_key"] == "PROMISE"
    assert world.appends == []


def test_active_appends_a_promise_made_envelope(monkeypatch):
    world = World()
    world.install(monkeypatch, "active")
    res = world.tick()
    assert res["made"] == 1 and len(world.appends) == 1
    events, registry = world.appends[0]
    assert len(events) == 1 and registry == REGISTRY
    ev = events[0]
    assert ev["kind"] == "promise_made" and ev["subject"] == PLAYER_REF and ev["target"] == COACH_REF
    norm = rev.validate_event(ev, team_registry=REGISTRY)
    assert norm["payload"]["terms"]["criterion"] == {"type": "depth_chart_slot", "slot": "WR", "max_depth": 1}


def test_replaying_a_tick_does_not_double_append(monkeypatch):
    world = World()
    world.install(monkeypatch, "active")
    first = world.tick()
    second = world.tick()
    assert first["made"] == 1 and second["made"] == 0
    assert len(world.appends) == 1 and len(world.appends[0][0]) == 1
    assert len(promise_rows(world.store)) == 1


# ------------------------------------------------------------------------------------------- reviews ---
@pytest.mark.parametrize("chart,resolution", [({"WR": [7]}, "kept"), ({"WR": [8, 7]}, "broken")])
def test_due_promise_resolves_from_the_madden_chart(monkeypatch, chart, resolution):
    world = World()
    world.charts[(5, 8)] = chart
    world.install(monkeypatch, "shadow")
    make_promise(world.store, tick=5)               # review_tick 8
    res = world.tick(tick=8)
    assert res["reviewed"] == 1 and res[resolution] == 1
    review = world.store.find(LEAGUE, SEASON, "PROMISE_REVIEW")[0]
    assert review["chosen_key"] == resolution.upper()
    assert review["facts"]["depth_at_review"] == (1 if resolution == "kept" else 2)


@pytest.mark.parametrize("how", ["injured", "unread"])
def test_injured_or_unread_defers_once_then_unknown(monkeypatch, how):
    world = World()
    if how == "injured":
        world.injured = {7}
    else:
        world.charts.pop((5, 8), None)              # no MADDEN chart at the due tick
    world.install(monkeypatch, "shadow")
    make_promise(world.store, tick=5)               # review_tick 8
    first = world.tick(tick=8)
    assert first["deferred"] == 1 and first["reviewed"] == 1
    [defer] = world.store.find(LEAGUE, SEASON, "PROMISE_REVIEW")
    assert defer["chosen_key"] == "DEFER" and defer["facts"]["next_review_tick"] == 9
    assert coach.open_promises(world.store, LEAGUE, SEASON)       # still open
    second = world.tick(tick=9)                     # the one re-review: still injured / unread -> unknown
    assert second["unknown"] == 1
    assert world.store.find(LEAGUE, SEASON, "PROMISE_REVIEW")[-1]["chosen_key"] == "UNKNOWN"
    assert coach.open_promises(world.store, LEAGUE, SEASON) == []
    assert world.tick(tick=10)["reviewed"] == 0     # judged once


def test_a_failing_player_is_counted_and_the_tick_continues(monkeypatch):
    world = World()
    world.install(monkeypatch, "shadow")
    real = w._lost_slot

    def boom(prev, now, pid):
        raise RuntimeError("bad chart")
    monkeypatch.setattr(w, "_lost_slot", boom)
    res = world.tick()
    assert res["made"] == 0 and res["errors"] == 1
    monkeypatch.setattr(w, "_lost_slot", real)
    assert world.tick()["made"] == 1


# --------------------------------------------------------------------------------------------- step ---
def snapshot_file(tmp, season=2030, week=5):
    path = os.path.join(str(tmp), "save.json")
    with open(path, "w") as f:
        json.dump({"season": {"calendar_year": season, "week_type": "RegularSeason", "week_index": week}}, f)
    return path


def fake_conn(rows=(True, True, True)):
    from unittest import mock
    conn = mock.MagicMock()
    cur = mock.MagicMock()
    cur.fetchone.return_value = rows
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


def wire_step(monkeypatch, tmp, rows=(True, True, True)):
    from unittest import mock
    path = snapshot_file(tmp)
    monkeypatch.setattr("pipeline.league_office.detect_stage", lambda snap: "REGULAR_WEEK")
    monkeypatch.setattr("pipeline.league_office.league_season", lambda snap, stage: 2030)
    monkeypatch.setattr("pipeline.league_office.league_week", lambda snap, stage: 5)
    monkeypatch.setattr("engines.relationship_producers.calendar.tick_for", lambda snap, week: 5)
    monkeypatch.setattr("engines.relationship_producers.sources.league_id", lambda cur: LEAGUE)
    monkeypatch.setattr("personality.lifecycle_store.universe_for_league", lambda cur, league: {"universe_id": U})
    monkeypatch.setattr(ws, "registry_user_team_ids", lambda cur: {9})
    conn, cur = fake_conn(rows)
    return path, conn, cur


def test_step_off_by_default_and_with_the_kill_switch(monkeypatch, tmp_path):
    from unittest import mock
    path = snapshot_file(tmp_path)
    connect = mock.Mock()
    assert ws.step_coach_promises(path, 2030, connect=connect, log_fn=lambda m: None) == {"status": "off"}
    connect.assert_not_called()
    monkeypatch.setenv(ws.COACH_PROMISES_ENV, "off")
    assert ws.step_coach_promises(path, 2030, connect=connect)["status"] == "off"
    connect.assert_not_called()


def test_step_skips_when_not_migrated(monkeypatch, tmp_path):
    path, conn, _ = wire_step(monkeypatch, tmp_path, rows=(False, True, True))
    monkeypatch.setenv(ws.COACH_PROMISES_ENV, "on")
    res = ws.step_coach_promises(path, 2030, connect=lambda dsn: conn, log_fn=lambda m: None)
    assert res["status"] == "skipped" and res["reason"] == "not_migrated"
    assert res["missing"] == ["agent_loop_decisions"]
    conn.rollback.assert_called()


def test_step_skips_without_a_universe(monkeypatch, tmp_path):
    path, conn, _ = wire_step(monkeypatch, tmp_path)
    monkeypatch.setattr("personality.lifecycle_store.universe_for_league", lambda cur, league: None)
    monkeypatch.setenv(ws.COACH_PROMISES_ENV, "on")
    res = ws.step_coach_promises(path, 2030, connect=lambda dsn: conn, log_fn=lambda m: None)
    assert res == {"status": "skipped", "reason": "no_universe"}
    conn.rollback.assert_called()


def test_step_commits_the_decisions_in_shadow(monkeypatch, tmp_path):
    path, conn, _ = wire_step(monkeypatch, tmp_path)
    captured = {}

    def fake_tick(cur, **kw):
        captured.update(kw)
        return {"status": "shadow", "made": 1, "reviewed": 0, "kept": 0, "broken": 0, "unknown": 0,
                "deferred": 0, "errors": 0}

    monkeypatch.setattr(watcher, "promise_tick", fake_tick)
    modes = {"features": {"coach_loop": {"mode": "shadow"}}}
    res = ws.step_coach_promises(path, 2030, connect=lambda dsn: conn, log_fn=lambda m: None, modes=modes)
    assert captured["mode"] == "shadow" and captured["league_id"] == LEAGUE and captured["season"] == 2030
    assert captured["tick"] == 5 and captured["universe_id"] == U and captured["user_team_ids"] == {9}
    assert captured["snapshot"]["season"]["calendar_year"] == 2030
    assert res == {"status": "shadow", "season": 2030, "tick": 5,
                   "counts": {"made": 1, "reviewed": 0, "kept": 0, "broken": 0, "unknown": 0, "deferred": 0,
                              "errors": 0}}
    conn.commit.assert_called_once()


def test_step_failure_is_logged_notified_and_never_raised(monkeypatch, tmp_path):
    from unittest import mock
    path, conn, _ = wire_step(monkeypatch, tmp_path)
    monkeypatch.setattr(watcher, "promise_tick", mock.Mock(side_effect=RuntimeError("boom")))
    monkeypatch.setenv(ws.COACH_PROMISES_ENV, "on")
    with mock.patch("pipeline.watch_save.send_notify") as notify:
        res = ws.step_coach_promises(path, 2030, connect=lambda dsn: conn, log_fn=lambda m: None)
    assert res == {"status": "failed", "error": "boom"}
    conn.rollback.assert_called()
    notify.assert_called_once()


def test_ingest_carries_coach_promises_after_the_producers(monkeypatch, tmp_path):
    from unittest import mock
    order = []
    path = snapshot_file(tmp_path, season=2036)
    monkeypatch.setattr(ws, "personality_modes", lambda *a, **k: None)

    def step(name, ret):
        return lambda *a, **k: order.append(name) or ret

    monkeypatch.setattr("pipeline.load_save.load", lambda *a, **k: order.append("load") or {})
    monkeypatch.setattr(ws, "step_relationship_producers", step("producers", {"status": "off"}))
    monkeypatch.setattr(ws, "step_coach_promises", step("coach_promises", {"status": "off"}))
    monkeypatch.setattr(ws, "step_personality_evolution", step("evolution", {"status": "off"}))
    monkeypatch.setattr(ws, "step_focus_refresh", step("focus_refresh", {"status": "off"}))
    monkeypatch.setattr(ws, "step_trade_requests", step("trade_requests", {"status": "off"}))
    with mock.patch("pipeline.watch_save.send_notify"):
        res = ws.ingest_snapshot(path, 2036, None, save_name="CAREER-A", stage_only=True)
    assert order == ["load", "producers", "coach_promises", "evolution", "focus_refresh", "trade_requests"]
    assert res["coach_promises"] == {"status": "off"}
