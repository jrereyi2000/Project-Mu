"""Progression receipt integration (PERSONALITY_DEVELOPMENT_COACHING_PLAN U3) on fake receipt storage.

Real checkpoint/offseason/report modules; progression_state's storage functions are replaced by an
in-memory fake with the same receipt semantics (PREPARED proposals spend nothing; APPLIED spends once).
"""
import copy

import pytest

from engines import personality_development as pdev
from engines import progression_checkpoint as cp
from engines import progression_offseason as offseason
from engines import progression_report as report
from engines import progression_state as state
from tests.test_form_engine import ratings_for

SCHEMA = pdev.POLICY_SCHEMA


class FakeStore:
    def __init__(self, base, band):
        self.states = {1: {"player_id": 1, "application_season": 2034, "revision": 0,
                           "base_ratings": dict(base), "band": copy.deepcopy(band), "source_fingerprint": "boot"}}
        self.events = {}
        self.accounting = {}

    def install(self, monkeypatch):
        import contextlib
        monkeypatch.setattr(state, "load_state", lambda cur, pid, for_update=False: copy.deepcopy(self.states.get(pid)))
        monkeypatch.setattr(state, "load_events", self.load_events)
        monkeypatch.setattr(state, "load_annual_applied",
                            lambda cur, pid, season: dict(self.accounting.get((pid, season), {})))
        monkeypatch.setattr(state, "prepare_event", self.prepare_event)
        monkeypatch.setattr(state, "capture_report_context",
                            lambda cur, current, **kw: {"before_base": dict(current["base_ratings"]),
                                                        "before_band": current["band"], "position": kw.get("position")})
        monkeypatch.setattr(state, "_savepoint", lambda cur: contextlib.nullcontext())

    def load_events(self, cur, pid, season, status="APPLIED"):
        rows = [copy.deepcopy(e) for e in self.events.values() if e["player_id"] == pid and e["earned_season"] == season
                and (status is None or e["status"] == status)]
        return sorted(rows, key=lambda e: (e["base_revision"], e["event_id"]))

    def prepare_event(self, cur, **kw):
        identity = state.event_id(kw["player_id"], kw["earned_season"], kw["kind"], kw["ordinal"], kw["rules_version"])
        proposal = dict(event_id=identity, status="PREPARED", player_id=kw["player_id"],
                        earned_season=kw["earned_season"], application_season=kw["application_season"],
                        kind=kw["kind"], ordinal=kw["ordinal"], rules_version=kw["rules_version"],
                        source_fingerprint=kw["source_fingerprint"], base_revision=kw["base_revision"],
                        candidate_base=dict(kw["candidate_base"]), candidate_band=dict(kw["candidate_band"]),
                        overlay=dict(kw.get("overlay") or {}), annual_deltas=dict(kw.get("annual_deltas") or {}),
                        evidence_ids=sorted(str(v) for v in kw.get("evidence_ids") or ()),
                        metadata=copy.deepcopy(kw.get("metadata") or {}))
        state._canonical(proposal)
        old = self.events.get(identity)
        if old:
            keys = [k for k in proposal if k != "status"]
            if any(old[k] != proposal[k] for k in keys):
                raise state.ProgressionStateError("event identity already has a different proposal")
            return copy.deepcopy(old)
        if self.states[kw["player_id"]]["revision"] != kw["base_revision"]:
            raise state.ProgressionStateError("stale authoritative base revision")
        self.events[identity] = proposal
        return copy.deepcopy(proposal)

    def acknowledge(self, event):
        e = self.events[event["event_id"]]
        st = self.states[e["player_id"]]
        assert st["revision"] == e["base_revision"]
        e["status"] = "APPLIED"
        st.update(revision=st["revision"] + 1, base_ratings=dict(e["candidate_base"]),
                  band=dict(e["candidate_band"]), application_season=e["application_season"])
        totals = self.accounting.setdefault((e["player_id"], e["earned_season"]), {})
        for k, v in e["annual_deltas"].items():
            totals[k] = totals.get(k, 0) + v
        return copy.deepcopy(e)


def line(n, **change):
    return {"game_id": n, "season": 2034, "week": n, "status": "COMPLETED", "game_type": "REGULAR_SEASON",
            "position_group": "REC", "opportunities": 40, "performance_signal": .9, **change}


@pytest.fixture
def fake(monkeypatch):
    base = ratings_for("WR", 70)
    band = {"floor": 60, "spread": 3, "group_ceilings": {"physical": 90, "technical": 85, "mental": 85}}
    store = FakeStore(base, band)
    store.install(monkeypatch)
    # Off/zero must never query the personality core.
    from personality import store as pstore
    monkeypatch.setattr(pstore, "resolve_alias", lambda *a, **k: (_ for _ in ()).throw(AssertionError("core read")))
    return store


def view(traits=(), drive=5, pid="p"):
    return {"status": "stored", "origin": "generated", "traits": list(traits), "revision": 1, "persona_id": pid,
            "catalogue_version": "cat", "view_version": "views-v2",
            "sliders": dict(temperament=5, sociability=5, confidence=5, openness=5, warmth=5, drive=drive)}


def context(player=None, coach=None, intervals=None):
    coaches = {"db-coach:9": coach} if coach else {}
    return {"player_id": 1, "player_view": player or view(), "coach_views": coaches, "mentor_view": None,
            "intervals": intervals if intervals is not None else
            ([{"start_week": 1, "end_week": 18, "coach_ids": ["db-coach:9"], "mentor_id": None}] if coach else []),
            "context_hash": "ctx", "source_event_ids": []}


def cfg(mode="enabled", version="trial-1", abs_points=10.0, **weights):
    weights = weights or {"film": .3, "technical_preparation": .3, "teaching": .3}
    return pdev.DevelopmentConfig(version=version, mode=mode, weights=weights, max_relative_change=.5,
                                  max_annual_abs_points=abs_points, max_annual_ovr_difference=2,
                                  max_per_rating_annual_difference=3)


def prepare(week, games=None, **kw):
    games = games if games is not None else [line(n) for n in range(1, week + 1)]
    return cp.prepare_player(None, player_id=1, pgid=101, earned_season=2034, application_season=2034,
                             as_of_week=week, source_fingerprint=f"w{week}", player_games=games, team_games=games,
                             position="WR", **kw)


def requested_total(event):
    return sum(v for d in event["metadata"]["decisions"] for v in d["result"]["skill_deltas"].values())


def test_off_equality_and_no_core_query(fake):
    a = prepare(8)
    fake.events.clear()
    b = prepare(8, development_config=pdev.OFF, development_context=context(view(["FILM_ROOM_RAT"])))
    fake.events.clear()
    c = prepare(8, development_config=pdev.DevelopmentConfig(version="v", mode="off", weights={"film": .5}))
    assert a == b == c and SCHEMA not in a["metadata"]


def test_enabled_zero_weights_equal_off(fake):
    a = prepare(8)
    fake.events.clear()
    zero = pdev.DevelopmentConfig(version="v", mode="enabled", max_relative_change=.5, max_annual_abs_points=1,
                                  max_annual_ovr_difference=1, max_per_rating_annual_difference=1)
    b = prepare(8, development_config=zero, development_context=context(view(["FILM_ROOM_RAT"])))
    assert a["candidate_base"] == b["candidate_base"] and a["metadata"]["fractions"] == b["metadata"]["fractions"]
    assert b["metadata"][SCHEMA]["attributed_effect"] == {}


def test_enabled_adds_capped_effect_before_quantization_and_keeps_neutral_decisions(fake):
    neutral = prepare(8)
    fake.events.clear()
    event = prepare(8, development_config=cfg(), development_context=context(view(["FILM_ROOM_RAT"])))
    block = event["metadata"][SCHEMA]
    assert block["attributed_effect"] and set(block["attributed_effect"]) <= {"Awareness", "PlayRecognition"}
    assert event["metadata"]["decisions"] == neutral["metadata"]["decisions"]
    gained = lambda e: {k: e["candidate_base"][k] - fake.states[1]["base_ratings"][k] + e["metadata"]["fractions"].get(k, 0)
                        for k in block["attributed_effect"]}
    for k, v in block["attributed_effect"].items():
        assert gained(event)[k] == pytest.approx(gained(neutral)[k] + v)
    assert block["mentor"]["status"] == "disabled"
    assert block["public_reasons"] == ["PERSONALITY"]


def test_shadow_changes_no_target(fake):
    neutral = prepare(8)
    fake.events.clear()
    event = prepare(8, development_config=cfg("shadow"), development_context=context(view(["FILM_ROOM_RAT"])))
    assert event["candidate_base"] == neutral["candidate_base"]
    assert event["metadata"]["fractions"] == neutral["metadata"]["fractions"]
    block = event["metadata"][SCHEMA]
    assert block["attributed_effect"] == {} and block["shadow_effect"] and block["annual_abs_spend"] == 0


def test_missing_profile_and_missing_context_are_neutral(fake):
    neutral = prepare(8)
    fake.events.clear()
    missing = {"status": "missing", "sliders": {}, "traits": [], "persona_id": None, "revision": None}
    a = prepare(8, development_config=cfg(), development_context=context(missing))
    assert a["candidate_base"] == neutral["candidate_base"] and a["metadata"][SCHEMA]["attributed_effect"] == {}
    fake.events.clear()
    b = prepare(8, development_config=cfg(), development_context=None)
    assert "NO_CONTEXT" in b["metadata"][SCHEMA]["reasons"]


def test_context_for_another_player_fails(fake):
    with pytest.raises(state.ProgressionStateError):
        prepare(8, development_config=cfg(), development_context={**context(), "player_id": 2})


def test_prepared_spends_nothing_applied_spends_once(fake):
    first = prepare(4, development_config=cfg(), development_context=context(view(["FILM_ROOM_RAT"])))
    assert first["status"] == "PREPARED"
    pending_next = prepare(8, development_config=cfg(), development_context=context(view(["FILM_ROOM_RAT"])))
    assert pending_next["metadata"][SCHEMA]["prior_applied"]["abs_spend"] == 0
    del fake.events[pending_next["event_id"]]
    fake.acknowledge(first)
    after = prepare(8, development_config=cfg(), development_context=context(view(["FILM_ROOM_RAT"])))
    prior = after["metadata"][SCHEMA]["prior_applied"]
    assert prior["abs_spend"] == pytest.approx(first["metadata"][SCHEMA]["annual_abs_spend"])
    assert prior["by_rating"] == first["metadata"][SCHEMA]["attributed_effect"]


def test_retry_returns_original_snapshot_and_changed_pending_retry_fails(fake):
    first = prepare(4, development_config=cfg(), development_context=context(view(["FILM_ROOM_RAT"])))
    # A pending retry returns the frozen original snapshot, never rereads a newer profile revision.
    again = prepare(4, development_config=cfg(), development_context={**context(view(["FILM_ROOM_RAT"], drive=9)),
                                                                       "context_hash": "ctx2"})
    assert again == first
    # A changed input save for the same pending checkpoint refuses.
    games = [line(n) for n in range(1, 5)]
    with pytest.raises(state.ProgressionStateError, match="different input save"):
        cp.prepare_player(None, player_id=1, pgid=101, earned_season=2034, application_season=2034, as_of_week=4,
                          source_fingerprint="other-save", player_games=games, team_games=games, position="WR",
                          development_config=cfg(), development_context=context(view(["FILM_ROOM_RAT"])))
    fake.acknowledge(first)
    replay = prepare(4, development_config=cfg("enabled", version="trial-2"),
                     development_context=context(view(["PERFECTIONIST"])))
    assert replay["metadata"][SCHEMA] == first["metadata"][SCHEMA] and replay["status"] == "APPLIED"


def test_annual_spend_cap_holds_across_checkpoints_and_config_change(fake):
    games = [line(n, supported_skills=["Catching", "ShortRouteRunning"]) for n in range(1, 19)]
    total = 0.0
    for i, week in enumerate((4, 8, 12, 18)):
        config = cfg(version=f"trial-{i}", abs_points=0.25)  # a new version never resets the allowance
        event = prepare(week, games=games[:week], development_config=config,
                        development_context=context(view(["FILM_ROOM_RAT", "PERFECTIONIST"]),
                                                    coach=view(["TEACHER"], pid="c")),
                        season_closed=week == 18)
        total += event["metadata"][SCHEMA]["annual_abs_spend"]
        fake.acknowledge(event)
    assert 0 < total <= 0.25 + 1e-9


def test_teaching_follows_verified_intervals_only(fake):
    coach = view(["TEACHER"], pid="c")
    full = prepare(8, development_config=cfg(teaching=.4), development_context=context(coach=coach))
    fake.events.clear()
    # Traded at week 4: the destination coach covers only weeks 5-8, never retroactively.
    half = prepare(8, development_config=cfg(teaching=.4), development_context=context(
        coach=coach, intervals=[{"start_week": 5, "end_week": 8, "coach_ids": ["db-coach:9"], "mentor_id": None}]))
    fake.events.clear()
    none = prepare(8, development_config=cfg(teaching=.4), development_context=context(coach=coach, intervals=[]))
    f, h, n = (sum(e["metadata"][SCHEMA]["requested_effect"].values()) for e in (full, half, none))
    assert f > h > n == 0
    assert full["metadata"][SCHEMA]["public_reasons"] == ["COACHING"]


def test_offseason_target_includes_applied_effect_once(fake):
    event = prepare(8, development_config=cfg(), development_context=context(view(["FILM_ROOM_RAT"])))
    fake.acknowledge(event)
    effect = event["metadata"][SCHEMA]["attributed_effect"]
    start = event["metadata"]["annual_start"]
    target = {k: v + (3 if k in effect else 0) for k, v in start.items()}
    [final] = offseason.prepare_offseason(None, [{"player_id": 1, "pgid": 101, "target_ratings": target,
                                                  "position": "WR"}],
                                          earned_season=2034, application_season=2035, source_fingerprint="off",
                                          rules_version="r")
    assert final["metadata"][SCHEMA]["annual_effect_included"] == effect
    assert effect
    for k in effect:
        expected = int(start[k] + 3 + effect.get(k, 0))  # neutral + effect once, truncated, no carry re-paid
        assert final["candidate_base"][k] == expected


def test_offseason_without_effects_is_unchanged(fake):
    event = prepare(8)
    fake.acknowledge(event)
    start = event["metadata"]["annual_start"]
    [final] = offseason.prepare_offseason(None, [{"player_id": 1, "pgid": 101, "target_ratings": start}],
                                          earned_season=2034, application_season=2035, source_fingerprint="off",
                                          rules_version="r")
    assert SCHEMA not in final["metadata"]


def test_include_effects_pure_rules():
    assert offseason.include_personality_effects({"A": 50}, {}) == {"A": 50}
    assert offseason.include_personality_effects({"A": 98.5}, {"A": 1}) == {"A": 99.0}
    with pytest.raises(state.ProgressionStateError):
        offseason.include_personality_effects({"A": 50}, {"B": 1})
    events = [{"status": "PREPARED", "metadata": {SCHEMA: {"mode": "enabled", "attributed_effect": {"A": 1}}}},
              {"status": "APPLIED", "metadata": {SCHEMA: {"mode": "shadow", "attributed_effect": {"A": 1}}}},
              {"status": "APPLIED", "metadata": {SCHEMA: {"mode": "enabled", "attributed_effect": {"A": .5}}}}]
    assert offseason.applied_personality_effects(events) == {"A": .5}


def test_public_projection_names_only_applied_enabled_effects(fake):
    event = prepare(8, development_config=cfg(), development_context=context(view(["FILM_ROOM_RAT"], pid="secret-p")))
    proposed = report.project_event(event, audience="public")
    assert "PERSONALITY" not in proposed["reasons"]
    applied = fake.acknowledge(event)
    public = report.project_event(applied, audience="public")
    assert "PERSONALITY" in public["reasons"]
    clean = report.sanitize_public(public)
    assert "PERSONALITY" in clean["reasons"]
    text = repr(clean)
    assert "secret-p" not in text and "personality_development" not in text and "FILM_ROOM_RAT" not in text
    private = report.project_event(applied, audience="private")
    assert private["audit"]["personality_development"]["attributed_effect"]


def test_shadow_receipt_never_claims_an_effect(fake):
    event = prepare(8, development_config=cfg("shadow"), development_context=context(view(["FILM_ROOM_RAT"])))
    applied = fake.acknowledge(event)
    assert "PERSONALITY" not in report.project_event(applied, audience="public")["reasons"]


def test_prepare_pass_threads_development_config(fake, monkeypatch):
    seen = {}

    def capture(cur, **kw):
        seen.update(kw)
        return {"event_id": "x", "status": "PREPARED", "candidate_base": {}, "overlay": {},
                "metadata": {"pgid": 101}, "player_id": 1}
    monkeypatch.setattr(cp, "prepare_player", capture)
    monkeypatch.setattr(cp, "document_for_events", lambda events, snapshot: {"players": []})
    cp.prepare_pass(None, {"players": [{"pgid": 101}]}, 2034, 8, source_fingerprint="s",
                    player_inputs={1: {"pgid": 101, "development_context": {"player_id": 1}}},
                    development_config=cfg())
    assert seen["development_config"] == cfg() and seen["development_context"] == {"player_id": 1}


def test_rejects_wrong_config_type(fake):
    with pytest.raises(ValueError, match="DevelopmentConfig"):
        prepare(8, development_config={"mode": "enabled"})


def test_legacy_constants_remain_zero():
    from engines import progression
    assert progression.PERSONA_TERM == 0.0 and progression.COACH_TERM == 0.0
