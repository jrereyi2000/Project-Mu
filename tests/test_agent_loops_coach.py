"""CPU coach loop (COACH_PLAYER_AGENT_LOOPS_PLAN.md §2.2, §4). Synthetic: no database, no model."""
import pytest

from engines import depth_chart_engine as dce
from engines.agent_loops import base, coach
from personality import relationship_events as rev

U = "11111111-1111-4111-8111-111111111111"
COACH_REF = {"kind": "person", "universe_id": U, "role": "coach", "persona_id": "coach-1"}
PLAYER_REF = {"kind": "person", "universe_id": U, "role": "player", "persona_id": "player-7"}
REGISTRY = {"team:5": {"abbr": "CAR"}}

# QB: 1 (vet, 80) vs 2 (young, 79.5); WR: 10, 11, 12 start, 13 next (78 vs 77); CB: 20, 21 start, 22 far behind;
# LT: 30 starts, 31 is close but out injured; HB: 40 starts, 41 behind is close but already starts at FB.
NEUTRAL = {"QB": [1, 2, 3], "WR": [10, 11, 12, 13], "CB": [20, 21, 22], "LT": [30, 31], "HB": [40, 41],
           "FB": [41, 40]}


def cand(pid, slot, score, cohort="other", **kw):
    row = {"player_id": pid, "slot": slot, "neutral_score": score, "eligible": True, "out": False,
           "cohort": cohort, "cohort_weight": 1.0 if cohort != "other" else 0.0, "name": f"Name{pid} Last{pid}"}
    row.update(kw)
    return row


CANDS = [cand(1, "QB", 80.0, "veteran"), cand(2, "QB", 79.5, "youth"), cand(3, "QB", 60.0),
         cand(10, "WR", 85.0), cand(11, "WR", 82.0), cand(12, "WR", 78.0), cand(13, "WR", 77.0, "youth"),
         cand(20, "CB", 84.0), cand(21, "CB", 83.0), cand(22, "CB", 70.0),
         cand(30, "LT", 75.0), cand(31, "LT", 74.5, out=True),
         cand(40, "HB", 76.0), cand(41, "HB", 75.8), cand(41, "FB", 74.0), cand(40, "FB", 60.0)]

YOUNG_COACH = {"status": "stored", "sliders": {"warmth": 5, "drive": 5}, "traits": ["YOUTH_DEVELOPER"],
               "persona_id": "coach-1"}
PLAIN_COACH = {"status": "stored", "sliders": {"warmth": 5, "drive": 5}, "traits": [], "persona_id": "coach-2"}


def keys(options):
    return [o.key for o in options]


def test_chart_variants_only_inside_the_close_window_and_legal():
    opts = coach.chart_options(NEUTRAL, CANDS, close_window=2.0)
    assert keys(opts) == ["NEUTRAL", "SWAP:QB:2:1", "SWAP:WR:13:12"]
    for o in opts[1:]:
        chart = coach.apply_option(NEUTRAL, o)
        starters = dce.starters_by_base_slot(chart)
        flat = [p for ids in starters.values() for p in ids]
        assert len(flat) == len(set(flat))                      # nobody starts twice
        assert o.detail["gap"] <= 2.0
        assert sorted(chart[o.detail["slot"]]) == sorted(NEUTRAL[o.detail["slot"]])
    assert coach.apply_option(NEUTRAL, opts[0]) == NEUTRAL
    assert keys(coach.chart_options(NEUTRAL, CANDS, close_window=0.1)) == ["NEUTRAL"]
    wide = coach.chart_options(NEUTRAL, CANDS, close_window=50.0)
    assert len(wide) == 1 + coach.CHART_VARIANTS
    assert not any(":31:" in k or k.startswith("SWAP:HB:41") for k in keys(wide))   # injured / starts elsewhere


def test_youth_developer_leans_young():
    opts = coach.chart_options(NEUTRAL, CANDS, close_window=2.0)
    young = coach.chart_weights(opts, YOUNG_COACH, neutral_chart=NEUTRAL, candidates=CANDS, close_window=2.0)
    plain = coach.chart_weights(opts, PLAIN_COACH, neutral_chart=NEUTRAL, candidates=CANDS, close_window=2.0)
    assert young["SWAP:QB:2:1"] > plain["SWAP:QB:2:1"] == coach.VARIANT_BASE
    assert young["NEUTRAL"] == plain["NEUTRAL"] == coach.NEUTRAL_WEIGHT
    vets = coach.chart_weights(opts, PLAIN_COACH, neutral_chart=NEUTRAL, candidates=CANDS, close_window=2.0,
                               playing_time="VETERANS")
    assert vets["SWAP:QB:2:1"] == coach.VARIANT_BASE             # promoting the youngster gains no veteran lean


def test_choose_chart_records_and_replays():
    store = base.MemoryStore()
    opts = coach.chart_options(NEUTRAL, CANDS, close_window=2.0)
    out = coach.choose_chart(None, 5, 2026, 4, YOUNG_COACH, opts, llm=None, league_id=1, neutral_chart=NEUTRAL,
                             candidates=CANDS, close_window=2.0, store=store)
    d = out["decision"]
    assert d["kind"] == "CHART" and d["subject_key"] == "team:5:chart" and d["persona_type"] == "coach"
    assert out["chart"] == coach.apply_option(NEUTRAL, d["option"])
    again = coach.choose_chart(None, 5, 2026, 4, PLAIN_COACH, opts, llm=None, league_id=1, neutral_chart=NEUTRAL,
                               candidates=CANDS, close_window=2.0, store=store)
    assert again["decision"]["replayed"] and again["chart"] == out["chart"]


def test_starting_qb_call_is_marquee_and_uses_the_model():
    store = base.MemoryStore()
    opts = coach.chart_options(NEUTRAL, CANDS, close_window=2.0)
    assert coach.chart_marquee(opts, CANDS)
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return '{"choice": "SWAP:QB:2:1", "message": "Name2 Last2 is our QB."}'
    out = coach.choose_chart(None, 5, 2026, 4, PLAIN_COACH, opts, llm=llm, league_id=1, neutral_chart=NEUTRAL,
                             candidates=CANDS, close_window=2.0, store=store)
    assert len(calls) == 1 and out["decision"]["choice_source"] == "MODEL"
    assert out["chart"]["QB"][:2] == [2, 1] and out["decision"]["text"] == "Name2 Last2 is our QB."
    wr_only = [o for o in opts if not o.key.startswith("SWAP:QB")]
    assert not coach.chart_marquee(wr_only, CANDS)
    assert coach.chart_marquee(wr_only, [dict(c, incumbent=True) if c["player_id"] == 12 else c for c in CANDS])


def test_open_promise_weights_without_leaving_the_window():
    opts = coach.chart_options(NEUTRAL, CANDS, close_window=2.0)
    promise = {"player_id": 13, "slot": "WR", "depth": 3}
    base_w = coach.chart_weights(opts, PLAIN_COACH, neutral_chart=NEUTRAL, candidates=CANDS, close_window=2.0)
    with_p = coach.chart_weights(opts, PLAIN_COACH, neutral_chart=NEUTRAL, candidates=CANDS, close_window=2.0,
                                 open_promises=[promise])
    assert with_p["SWAP:WR:13:12"] == pytest.approx(base_w["SWAP:WR:13:12"] + coach.PROMISE_LEAN)
    assert with_p["NEUTRAL"] == base_w["NEUTRAL"] and with_p["SWAP:QB:2:1"] == base_w["SWAP:QB:2:1"]
    driven = dict(PLAIN_COACH, sliders={"warmth": 8, "drive": 8})
    assert coach.promise_lean(driven) > coach.promise_lean(PLAIN_COACH)
    far = {"player_id": 22, "slot": "CB", "depth": 1}               # 22 is outside the window: no option keeps it
    w = coach.chart_weights(opts, PLAIN_COACH, neutral_chart=NEUTRAL, candidates=CANDS, close_window=2.0,
                            open_promises=[far])
    assert w == base_w and not any(o.key.startswith("SWAP:CB") for o in opts)


ROLE = [{"season": 2026, "tick": 3, "assessment": "satisfied"}, {"season": 2026, "tick": 4, "assessment": "unsatisfied"},
        {"season": 2026, "tick": 5, "assessment": "unsatisfied"}]
PLAYER = {"player_id": 7, "name": "Name7 Last7"}


def test_promise_appears_only_after_two_unhappy_weeks():
    assert coach.promise_options(PLAYER, ROLE[:2], "WR") == []
    opts = coach.promise_options(PLAYER, ROLE, "WR")
    assert keys(opts) == ["NONE", "PROMISE"]
    assert opts[1].detail == {"player_id": 7, "slot": "WR", "depth": 1, "review_tick": 8}
    assert coach.promise_options(PLAYER, ROLE, "WR", open_promise=True) == []
    assert coach.promise_options(PLAYER, ROLE, "WR", tick=22) == []           # review would fall after week 23
    assert coach.promise_options(PLAYER, ROLE + [{"tick": 6, "assessment": "satisfied"}], "WR") == []


def test_p_promise_bounds():
    assert coach.p_promise(PLAIN_COACH) == pytest.approx(0.25)
    assert coach.p_promise({"sliders": {"warmth": 10}, "traits": ["PLAYERS_COACH"]}) == pytest.approx(0.6)
    assert coach.p_promise({"sliders": {"warmth": 0}, "traits": ["DISCIPLINARIAN"]}) == pytest.approx(0.05)
    assert coach.p_promise({"sliders": {"warmth": 7}, "traits": ["PLAYERS_COACH", "DISCIPLINARIAN"]}) == pytest.approx(0.35)
    assert coach.p_promise({}) == pytest.approx(0.25)


def _make_promise(store, tick=5, refs=True):
    opts = coach.promise_options(PLAYER, ROLE, "WR", tick=tick)
    kw = dict(universe_id=U, issuer=COACH_REF, beneficiary=PLAYER_REF) if refs else {}
    # warmth 10 + PLAYERS_COACH puts p at its 0.6 cap; force PROMISE by giving the model the call
    return coach.decide_promise(store, league_id=1, season=2026, tick=tick, team_id=5,
                                coach_view={"sliders": {"warmth": 10}, "traits": ["PLAYERS_COACH"]}, player=PLAYER,
                                options=opts, seen=True,
                                llm=lambda p: '{"choice": "PROMISE", "message": "The WR job is yours."}', **kw)


def test_promise_made_envelope():
    store = base.MemoryStore()
    out = _make_promise(store)
    d, ev = out["decision"], out["event"]
    assert d["chosen_key"] == "PROMISE" and d["kind"] == "PROMISE" and d["subject_key"] == "player:7:promise"
    norm = rev.validate_event(ev, team_registry=REGISTRY)
    assert norm["kind"] == "promise_made" and norm["subject"] == PLAYER_REF and norm["target"] == COACH_REF
    p = norm["payload"]
    assert p["issuer"] == COACH_REF and p["beneficiary"] == PLAYER_REF
    assert p["terms"]["criterion"] == {"type": "depth_chart_slot", "slot": "WR", "max_depth": 1}
    assert p["due_at"] == {"season": 2026, "tick": 8} and p["promise_id"] == "loop:1:2026:5:7"
    assert coach.open_promises(store, 1, 2026) == [{"promise_id": "loop:1:2026:5:7", "player_id": 7, "slot": "WR",
                                                    "depth": 1, "review_tick": 8, "team_id": 5}]
    assert _make_promise(base.MemoryStore(), refs=False)["event"] is None
    assert coach.decide_promise(store, league_id=1, season=2026, tick=5, team_id=5, coach_view={}, player=PLAYER,
                                options=[], llm=None) == {"decision": None, "event": None}


def _review(store, tick, depth=None, hurt=False):
    return coach.review_promises(store, 2026, tick, league_id=1, chart_depth=lambda t, p, s: depth,
                                 injured=lambda p: hurt)


@pytest.mark.parametrize("depth,expected", [(1, "kept"), (2, "broken")])
def test_promise_kept_or_broken_at_review(depth, expected):
    store = base.MemoryStore()
    _make_promise(store)
    assert _review(store, 7, depth=1) == []                      # not due yet
    [r] = _review(store, 8, depth=depth)
    assert r["resolution"] == expected and r["decision"]["kind"] == "PROMISE_REVIEW"
    norm = rev.validate_event(r["event"], team_registry=REGISTRY)
    assert norm["kind"] == "promise_resolved" and norm["payload"]["resolution"] == expected
    assert norm["payload"]["criterion_result"]["depth_at_due"] == depth
    assert coach.open_promises(store, 1, 2026) == []
    assert _review(store, 9, depth=depth) == []                  # judged once


def test_injured_at_review_is_re_reviewed_once_then_unknown():
    store = base.MemoryStore()
    _make_promise(store)
    [first] = _review(store, 8, hurt=True)
    assert first["resolution"] == "defer" and first["event"] is None
    assert coach.open_promises(store, 1, 2026)                     # still open
    [second] = _review(store, 9, hurt=True)
    norm = rev.validate_event(second["event"], team_registry=REGISTRY)
    assert second["resolution"] == "unknown" and norm["confidence"] == "low"
    assert _review(store, 10, hurt=True) == []


def test_injured_then_healthy_is_judged_at_the_re_review():
    store = base.MemoryStore()
    _make_promise(store)
    _review(store, 8, hurt=True)
    [r] = _review(store, 9, depth=1)
    assert r["resolution"] == "kept"


def test_events_validate_as_a_batch_and_append_through_the_store():
    store = base.MemoryStore()
    made = _make_promise(store)["event"]
    resolved = _review(store, 8, depth=1)[0]["event"]
    batch = rev.validate_event_batch([resolved, made], calendar={2026: 32}, team_registry=REGISTRY)
    assert [e["kind"] for e in batch] == ["promise_made", "promise_resolved"]
    assert rev.select_active(batch)["excluded"] == []

    class FakeRel:
        def __init__(self):
            self.calls = []

        def read_head(self, cur, universe_id):
            return 3

        def append_events(self, cur, events, *, expected_head, team_registry):
            self.calls.append((events, expected_head))
            return {"status": "appended", "head": 4}

    rel = FakeRel()
    receipts = coach.append_events(None, [made, None, resolved], team_registry=REGISTRY, rel_store=rel)
    assert receipts == [{"status": "appended", "head": 4}] and rel.calls[0][1] == 3 and len(rel.calls[0][0]) == 2


def test_resolve_person(monkeypatch):
    from personality import store as persona_store

    class Ident:
        persona_id = "player-7"
    monkeypatch.setattr(persona_store, "resolve_alias", lambda cur, u, role, ns, ext: Ident() if ext == "7" else None)
    assert coach.resolve_person(None, U, {"role": "player", "namespace": "db-player", "external_id": 7}) == PLAYER_REF
    assert coach.resolve_person(None, U, {"role": "player", "namespace": "db-player", "external_id": 8}) is None
