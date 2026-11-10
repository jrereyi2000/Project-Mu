"""Player loop (COACH_PLAYER_AGENT_LOOPS_PLAN.md §2.3, §4). Synthetic: no database, no model."""
import math
import re

import pytest

from engines import free_agency_engine as fae
from engines.agent_loops import base, player as pl
from engines.trade_value import worth

MARKET = {"b": 0.09, "a_league": math.log(3_000_000), "a": {"QB": math.log(6_000_000)}, "n": {}}
REBUILDER = {"team_id": 3, "win_pct": 0.25, "playoff_odds": 0.1, "popularity_index": 20, "direction": "REBUILD",
             "name": "Carolina"}
P = {"id": 7, "group": "WR", "position": "WR", "ovr": 85, "age": 27, "accrued_seasons": 5, "name": "Name7 Last7"}
BAR = fae.accept_bar(P, False)
NEUTRAL = {"sliders": {"confidence": 5, "warmth": 5, "temperament": 5}, "traits": []}


def offer(mult=0.8, years=2):
    total = int(round(worth(P["ovr"], P["group"], MARKET) * mult * years))
    return {"team_id": 3, "player_id": 7, "years": years, "total_value": total, "bonus": int(total * 0.1),
            "guaranteed": int(total * 0.4), "projected_depth": 1, "offer_id": 88}


def keys(opts):
    return [o.key for o in opts]


def ctx(**kw):
    c = dict(league_id=1, season=2026, tick=25, player=P, team=REBUILDER, market=MARKET, bar=BAR,
             store=base.MemoryStore())
    c.update(kw)
    return c


def test_options_appear_when_they_exist():
    counters = fae.counter_options(offer(), P, REBUILDER, MARKET, BAR, k=3)
    opts = pl.contract_options(offer(), P, REBUILDER, MARKET, BAR, accepts=False)
    assert keys(opts) == list(pl.COUNTER_KEYS[:len(counters)]) + ["REJECT"]
    assert [o.detail["counter"] for o in opts[:-1]] == counters
    leaky = pl.contract_options(offer(), P, REBUILDER, MARKET, BAR, accepts=False, leak_ok=True)
    assert keys(leaky)[-1] == "LEAK_AND_COUNTER" and leaky[-1].detail["counter"] == counters[1]
    assert keys(pl.contract_options(offer(mult=0.3), P, REBUILDER, MARKET, BAR, accepts=False, leak_ok=True)) == ["REJECT"]
    acc = pl.contract_options(offer(1.3), P, REBUILDER, MARKET, BAR, accepts=True)
    assert keys(acc)[0] == "ACCEPT" and "REJECT" not in keys(acc)


def _share(weights, ks):
    total = sum(weights.values())
    return sum(weights.get(k, 0) for k in ks) / total


def test_money_first_pushes_up_and_attachment_pulls_down():
    opts = pl.contract_options(offer(1.3), P, REBUILDER, MARKET, BAR, accepts=True)
    neutral = pl.contract_weights(opts, NEUTRAL)
    money = pl.contract_weights(opts, dict(NEUTRAL, traits=["MONEY_FIRST"], sliders={"confidence": 8}))
    attached = pl.contract_weights(opts, NEUTRAL, {"attachment": 1.0})
    hard = [k for k in ("COUNTER_ASK", "COUNTER_HARD") if k in neutral]
    soft = [k for k in ("ACCEPT", "COUNTER_MIN") if k in neutral]
    assert hard and _share(money, hard) > _share(neutral, hard)
    assert _share(attached, soft) > _share(neutral, soft)
    assert all(w > 0 for w in money.values())


def test_leak_weight_traits():
    assert pl.leak_weight({"traits": ["SPOTLIGHT_SEEKER", "QUOTABLE"]}) > pl.leak_weight({}) > \
        pl.leak_weight({"traits": ["GUARDED"]})


def test_respond_contract_records_and_maps_status():
    c = ctx()
    out = pl.respond_contract(None, NEUTRAL, offer(), c, llm=None)
    d = out["decision"]
    assert d["kind"] == "CONTRACT" and d["subject_key"] == "player:7:offer:88" and d["persona_type"] == "player"
    assert out["status"] in ("COUNTER", "REJECT")
    if out["status"] == "COUNTER":
        assert out["counter"] in fae.counter_options(offer(), P, REBUILDER, MARKET, BAR, k=3)
    again = pl.respond_contract(None, NEUTRAL, offer(), c, llm=None)
    assert again["decision"]["replayed"] and again["status"] == out["status"]


def _leak_once(store, pid, tick, llm=None):
    p = dict(P, id=pid, name=f"Name{pid} Last{pid}")
    c = ctx(player=p, store=store, tick=tick, team_name="Carolina")
    msg = '{"choice": "LEAK_AND_COUNTER", "message": "x"}'
    return pl.respond_contract(None, NEUTRAL, dict(offer(), player_id=pid), c, seen=True,
                               llm=llm or (lambda prompt: msg))


def test_leak_caps_per_player_and_per_week():
    store = base.MemoryStore()
    first = _leak_once(store, 7, 25)
    assert first["leak"] is not None and first["leak"]["visibility"] == "public"
    assert first["status"] == "COUNTER"
    assert not pl.leak_allowed(store, 1, 2026, 26, 7)                 # one per player per season
    assert pl.leak_allowed(store, 1, 2026, 25, 8)
    for pid in (8, 9):
        assert _leak_once(store, pid, 25)["leak"] is not None
    assert not pl.leak_allowed(store, 1, 2026, 25, 10)                # three a week
    late = _leak_once(store, 10, 25)
    assert late["leak"] is None and "LEAK_AND_COUNTER" not in [o["key"] for o in late["decision"]["options"]]
    assert pl.leak_allowed(store, 1, 2026, 26, 10)


def test_statement_holds_no_figures_beyond_offered_and_asked():
    store = base.MemoryStore()
    out = _leak_once(store, 7, 25)
    leak = out["leak"]
    assert set(leak["facts"]) == {"player_id", "player", "position", "team", "wants", "offered", "asked"}
    assert leak["facts"]["asked"] == {k: out["counter"][k] for k in ("years", "total_value")}
    assert not re.search(r"\d", pl.statement_text(leak["facts"]).replace(leak["facts"]["player"], ""))
    # a model statement with an invented figure is dropped for the template
    bad = _leak_once(base.MemoryStore(), 7, 25, llm=lambda p: '{"choice": "LEAK_AND_COUNTER", '
                     '"message": "Name7 Last7 wants 99,000,000 from Carolina."}')
    assert bad["leak"]["text"] == pl.statement_text(bad["leak"]["facts"])
    asked = leak["facts"]["asked"]["total_value"]
    good_msg = f"I want {asked:,} from Carolina. I've earned it."
    good = _leak_once(base.MemoryStore(), 7, 25,
                      llm=lambda p: '{"choice": "STATEMENT", "message": "%s"}' % good_msg
                      if "STATEMENT:" in p else '{"choice": "LEAK_AND_COUNTER", "message": "ok"}')
    assert good["leak"]["text"] == good_msg


def test_marquee_floor_and_contract_marquee():
    assert pl.marquee_floor(range(1, 101)) == 51
    assert pl.marquee_floor([5, 4]) == 0.0
    store = base.MemoryStore()
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return '{"choice": "REJECT", "message": "No."}'
    out = pl.respond_contract(None, NEUTRAL, offer(), ctx(store=store, marquee_floor=1.0), llm=llm)
    assert len(calls) == 1 and out["decision"]["choice_source"] == "MODEL" and out["status"] == "REJECT"
    out2 = pl.respond_contract(None, NEUTRAL, dict(offer(), offer_id=89), ctx(store=store, marquee_floor=1e12), llm=llm)
    assert len(calls) == 1 and out2["decision"]["choice_source"] == "POLICY"


def test_respond_inbox_uses_the_model_and_stores_the_reply():
    store = base.MemoryStore()
    thread = {"id": 41, "kind": "ROLE_TALK", "season": 2026, "week": 6, "league_id": 1}
    facts = {"player": "Name7 Last7", "slot": "WR", "unhappy_weeks": 2, "message_seq": 3}
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return '{"choice": "PATIENT", "message": "I hear you, Coach. I want the WR job back."}'
    out = pl.respond_inbox(None, thread, NEUTRAL, facts, llm=llm, store=store)
    assert len(calls) == 1 and out["stance"] == "PATIENT"
    assert out["text"] == "I hear you, Coach. I want the WR job back."
    d = out["decision"]
    assert d["kind"] == "INBOX_REPLY" and d["subject_key"] == "thread:41:msg:3" and d["choice_source"] == "MODEL"
    assert store.get(1, 2026, 6, "INBOX_REPLY", "thread:41:msg:3")["chosen_key"] == "PATIENT"
    again = pl.respond_inbox(None, thread, NEUTRAL, facts, llm=llm, store=store)
    assert len(calls) == 1 and again["decision"]["replayed"]
    contract = {"id": 42, "kind": "CONTRACT", "season": 2026, "week": 25, "league_id": 1}
    cfacts = {"engine_status": "COUNTER", "counters": {"COUNTER_MIN": {"years": 2, "total_value": 30000000}},
              "message_seq": 1}
    # B6 (chunk 8): when the engine countered, the player's options are its counters — a REJECT beside a live
    # counter let the words turn the offer down while the thread kept the counter.
    assert keys(pl.inbox_options(contract, cfacts)) == ["COUNTER_MIN"]
    res = pl.respond_inbox(None, contract, NEUTRAL, cfacts, llm=None, store=store)
    assert res["stance"] == "COUNTER_MIN" and res["text"] is None
    rejected = {"engine_status": "REJECT", "counters": {}, "message_seq": 1}
    assert keys(pl.inbox_options(contract, rejected)) == ["REJECT"]
    with pytest.raises(base.AgentLoopError):
        pl.inbox_options({"kind": "GM_TALK"}, {})


def test_a_shadow_leak_is_private_and_costs_no_model_call():
    """B5 (chunk 8): shadow recorded the leak as public and still called the model, so a shadow season would have
    published statements for counters that were never put to the team."""
    store = base.MemoryStore()
    seen = []
    p = dict(P, id=7, name="Name7 Last7")
    c = ctx(player=p, store=store, tick=25, team_name="Carolina", shadow=True)
    out = pl.respond_contract(None, NEUTRAL, dict(offer(), player_id=7), c, seen=True,
                              llm=lambda prompt: seen.append(prompt) or
                              '{"choice": "LEAK_AND_COUNTER", "message": "I want a new deal."}')
    assert out["leak"] is not None and out["leak"]["visibility"] == "private"
    assert len(seen) == 1                            # the contract decision's call only: the leak costs none
