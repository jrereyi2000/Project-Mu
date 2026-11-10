"""The player loop (COACH_PLAYER_AGENT_LOOPS_PLAN.md §2.3): the player negotiates for himself, with no hired agent.

In CPU contract talks (re-sign, free agency, UDFA) a player picks how hard to push among the counters the free
agency engine says he would accept, or accepts, or rejects, and can go public with it (capped). In JR's inbox
CONTRACT and ROLE_TALK threads he supplies his own reply and stance. The choice changes only which clearing counter
is put to the team: the team's response stays with the contract engine and the league office's checks, and a HARD
counter the team won't meet fails as it would today.

  contract_options(offer, player, team, market, bar, *, accepts, leak_ok=False) -> [Option]
      accepts:  ACCEPT + the clearing counters (COUNTER_MIN / COUNTER_ASK / COUNTER_HARD, only those that exist)
      else:     the counters + REJECT, + LEAK_AND_COUNTER (ASK plus a public statement) when leak_ok and ASK exists
  contract_weights(options, player_view, ctx) -> {key: weight}
      MONEY_FIRST and confidence push toward ASK and HARD; earned attachment to the offering team pulls toward
      ACCEPT and MIN; SPOTLIGHT_SEEKER and QUOTABLE raise the leak odds, GUARDED lowers them (estimates)
  leak_allowed(store, league_id, season, tick, player_id) -> bool   1 per player per season, 3 league-wide a week
  marquee_floor(aavs, n=MARQUEE_TOP_N) -> float          the season's 50th-highest average salary
  statement_facts(...) / statement_text(facts)           only position, team, "wants a deal", offered and asked
  respond_contract(cur, player_view, offer, ctx, *, llm, seen) -> {decision, status, counter, leak}
  respond_inbox(cur, thread, player_view, facts, *, llm, ...) -> {decision, stance, text}
"""
from __future__ import annotations

from engines import free_agency_engine as fae
from engines.agent_loops import base

LEAK_MAX_PER_PLAYER_SEASON = 1    # JR, 2026-11-03 (plan §8 Q3)
LEAK_MAX_PER_WEEK = 3
MARQUEE_TOP_N = 50                # a contract whose ask is in the season's top 50 average salaries (estimate)
COUNTER_KEYS = ("COUNTER_MIN", "COUNTER_ASK", "COUNTER_HARD")

# policy weights (review-only estimates)
W = dict(accept=1.0, accept_attach=0.5, accept_push=0.6, min=0.8, min_pull=0.4, ask=0.5, ask_push=0.5,
         hard=0.15, hard_push=0.6, reject=0.1, leak=0.1, floor=0.01)
PUSH = dict(money_first=0.5, confidence=0.08, attachment=0.6)
LEAK = dict(spotlight=1.0, quotable=0.8, guarded=0.3)
ROLE_STANCES = ("BUY_IN", "PATIENT", "FRUSTRATED")


def _money(c):
    return {"years": c["years"], "total_value": c["total_value"]}


def _aav(c):
    return c["total_value"] / max(1, c["years"])


def _fmt(c):
    return f"{c['years']} years, ${c['total_value']:,}"


def contract_options(offer, player, team, market, bar, *, accepts, leak_ok=False):
    counters = fae.counter_options(offer, player, team, market, bar, k=3)
    out = []
    if accepts:
        out.append(base.Option("ACCEPT", "ACCEPT", {}, f"Accept the offer ({_fmt(offer)})"))
    for key, c in zip(COUNTER_KEYS, counters):
        out.append(base.Option(key, "COUNTER", {"counter": c}, f"Counter at {_fmt(c)}"))
    if not accepts:
        out.append(base.Option("REJECT", "REJECT", {}, "Turn the offer down"))
        ask = next((o for o in out if o.key == "COUNTER_ASK"), None)
        if leak_ok and ask is not None:
            out.append(base.Option("LEAK_AND_COUNTER", "COUNTER", {"counter": ask.detail["counter"], "leak": True},
                                   f"Counter at {_fmt(ask.detail['counter'])} and say publicly you want a deal"))
    return out


def push_of(player_view, attachment=0.0):
    t = base.traits_of(player_view)
    p = (PUSH["money_first"] * (1.0 if "MONEY_FIRST" in t else 0.0)
         + PUSH["confidence"] * (base.slider(player_view, "confidence") - 5)
         - PUSH["attachment"] * base.clamp(float(attachment or 0.0), 0.0, 1.0))
    return base.clamp(p, -1.0, 1.0)


def leak_weight(player_view):
    t = base.traits_of(player_view)
    w = W["leak"] * (1 + LEAK["spotlight"] * ("SPOTLIGHT_SEEKER" in t) + LEAK["quotable"] * ("QUOTABLE" in t))
    return w * (LEAK["guarded"] if "GUARDED" in t else 1.0)


def contract_weights(options, player_view, ctx=None):
    """{key: weight}; ctx may carry `attachment` (0..1, earned attachment to the offering team)."""
    attach = base.clamp(float((ctx or {}).get("attachment") or 0.0), 0.0, 1.0)
    push = push_of(player_view, attach)
    up, down = max(push, 0.0), max(-push, 0.0)
    raw = {"ACCEPT": W["accept"] + W["accept_attach"] * attach - W["accept_push"] * up,
           "COUNTER_MIN": W["min"] + W["min_pull"] * down,
           "COUNTER_ASK": W["ask"] + W["ask_push"] * push,
           "COUNTER_HARD": W["hard"] + W["hard_push"] * push,
           "REJECT": W["reject"],
           "LEAK_AND_COUNTER": leak_weight(player_view)}
    keys = [o.key if isinstance(o, base.Option) else o["key"] for o in options]
    return {k: round(max(W["floor"], raw[k]), 10) for k in keys}


def marquee_floor(aavs, n=MARQUEE_TOP_N):
    """The n-th highest average salary of the season (0 when fewer than n are known: every ask is marquee then,
    so callers pass the season's full list)."""
    vals = sorted((float(a) for a in aavs if a), reverse=True)
    return vals[n - 1] if len(vals) >= n else 0.0


def leak_allowed(store, league_id, season, tick, player_id):
    rows = store.find(league_id, season, "LEAK")
    mine = sum(1 for r in rows if r["facts"].get("player_id") == player_id)
    week = sum(1 for r in rows if r["tick"] == int(tick))
    return mine < LEAK_MAX_PER_PLAYER_SEASON and week < LEAK_MAX_PER_WEEK


def statement_facts(player, team_name, offer, asked):
    """The only facts a statement may use: his name, position and team, the offered and asked years/money."""
    return {"player_id": player.get("id") or player.get("player_id"), "player": player.get("name"),
            "position": player.get("position") or player.get("group"), "team": team_name,
            "wants": "a new deal", "offered": _money(offer), "asked": _money(asked)}


def statement_text(facts):
    """The template statement: no figures at all (the model's version may use only offered and asked)."""
    who = facts.get("player") or "The player"
    pos = f" ({facts['position']})" if facts.get("position") else ""
    team = facts.get("team") or "his team"
    return f"{who}{pos} says he wants a deal done with {team}, and he has made his ask."


def _pid(player):
    return player.get("id") if player.get("id") is not None else player.get("player_id")


def respond_contract(cur, player_view, offer, ctx, *, llm, seen=False):
    """A CPU player's response to one offer. ctx: {league_id, season, tick, player, team, market, bar, accepts?,
    offer_id?, team_name?, attachment?, marquee_floor?, store?, cause_refs?}. accepts defaults to the engine's own
    test (utility >= bar). Returns {decision, status ACCEPT | COUNTER | REJECT, counter, leak}: the leak is a public
    LEAK decision whose text is the grounded model statement or the template."""
    st = base.store_for(cur, ctx.get("store"))
    player, team, market, bar = ctx["player"], ctx.get("team") or {}, ctx.get("market"), ctx["bar"]
    league_id, season, tick = ctx["league_id"], ctx["season"], ctx["tick"]
    pid = _pid(player)
    accepts = ctx.get("accepts")
    if accepts is None:
        accepts = fae.offer_utility(offer, player, team, market)["utility"] >= bar
    leak_ok = leak_allowed(st, league_id, season, tick, pid)
    options = contract_options(offer, player, team, market, bar, accepts=accepts, leak_ok=leak_ok)
    counters = {o.key: o.detail["counter"] for o in options if o.key in COUNTER_KEYS}
    team_name = ctx.get("team_name") or team.get("name") or team.get("abbr")
    facts = {"player_id": pid, "player": player.get("name"), "position": player.get("position") or player.get("group"),
             "team": team_name, "offered": _money(offer), "counters": {k: _money(c) for k, c in counters.items()}}
    floor = ctx.get("marquee_floor")
    ask = counters.get("COUNTER_ASK") or counters.get("COUNTER_MIN")
    marquee = floor is not None and ask is not None and _aav(ask) >= float(floor)
    offer_id = ctx.get("offer_id") or offer.get("offer_id") or f"team{offer.get('team_id')}"
    persona = dict(player_view or {}, persona_type="player")
    decision = base.decide(st, league_id=league_id, season=season, tick=tick, kind="CONTRACT",
                           subject=f"player:{pid}:offer:{offer_id}", options=options, persona=persona,
                           weights=contract_weights(options, player_view, ctx), facts=facts, seen=seen, llm=llm,
                           marquee=marquee, store=st, cause_refs=ctx.get("cause_refs") or ())
    key = decision["chosen_key"]
    detail = (decision["option"] or {}).get("detail") or {}
    status = "ACCEPT" if key == "ACCEPT" else "REJECT" if key == "REJECT" else "COUNTER"
    leak = None
    if key == "LEAK_AND_COUNTER":
        # A shadow season records what he would have said but publishes nothing, and spends no model call: the
        # counter it leaks about was never put to the team.
        shadow = bool(ctx.get("shadow"))
        leak = _leak(st, player, persona, team_name, offer, detail["counter"], decision,
                     None if shadow else llm, league_id=league_id, season=season, tick=tick, shadow=shadow)
    return {"decision": decision, "status": status, "counter": detail.get("counter"), "leak": leak}


def _leak(st, player, persona, team_name, offer, asked, decision, llm, *, league_id, season, tick, shadow=False):
    facts = statement_facts(player, team_name, offer, asked)
    option = base.Option("STATEMENT", "STATEMENT", {}, "Say publicly that you want a deal")
    ref = (f"agent_loop_decisions:{league_id}:{season}:{tick}:CONTRACT:{decision['subject_key']}")
    row = base.decide(st, league_id=league_id, season=season, tick=tick, kind="LEAK",
                      subject=f"player:{facts['player_id']}:leak", options=[option], persona=persona,
                      weights={"STATEMENT": 1.0}, facts=facts, seen=True, llm=llm, store=st, cause_refs=[ref],
                      visibility="private" if shadow else "public")
    if row["text"] is None:
        row["text"] = statement_text(facts)
    return row


# ----------------------------------------------------------------------------------------------- inbox ---

def inbox_options(thread, facts):
    """CONTRACT threads: ACCEPT (when the engine accepts) and the engine's clearing counters, else REJECT.
    ROLE_TALK threads: BUY_IN, PATIENT, FRUSTRATED (the player's stance on what his coach told him)."""
    if thread.get("kind") == "CONTRACT":
        out = []
        if facts.get("engine_status") == "ACCEPT":
            out.append(base.Option("ACCEPT", "ACCEPT", {}, "Accept the offer"))
        for key in COUNTER_KEYS:
            c = (facts.get("counters") or {}).get(key)
            if c:
                out.append(base.Option(key, "COUNTER", {"counter": c}, f"Counter at {_fmt(c)}"))
        # A thread whose engine countered offers its counters and nothing else: a REJECT beside a live counter let
        # the player's words turn the offer down while the thread kept his counter on the table.
        if facts.get("engine_status") == "REJECT" or not out:
            out.append(base.Option("REJECT", "REJECT", {}, "Turn the offer down"))
        return out
    if thread.get("kind") == "ROLE_TALK":
        return [base.Option("BUY_IN", "STANCE", {}, "Buy in to what the coach said"),
                base.Option("PATIENT", "STANCE", {}, "Stay patient but wait to see it"),
                base.Option("FRUSTRATED", "STANCE", {}, "Say you are frustrated with your role")]
    raise base.AgentLoopError(f"respond_inbox handles CONTRACT and ROLE_TALK threads, not {thread.get('kind')!r}")


def role_weights(player_view, facts):
    warmth, temper = base.slider(player_view, "warmth"), base.slider(player_view, "temperament")
    unhappy = float(facts.get("unhappy_weeks") or 0)
    return {"BUY_IN": max(0.05, 1.0 + 0.1 * (warmth - 5) - 0.1 * unhappy),
            "PATIENT": 0.8,
            "FRUSTRATED": max(0.05, 0.3 + 0.1 * (temper - 5) + 0.15 * unhappy)}


def respond_inbox(cur, thread, player_view, facts, *, llm, league_id=None, options=None, store=None, message_seq=None):
    """The player's reply and stance in JR's CONTRACT / ROLE_TALK thread. Always seen: the model chooses among the
    options and speaks (its words kept only if grounded in facts); without a model the policy picks and the inbox
    keeps its neutral template. One decision per message (subject 'thread:<id>:msg:<seq>'); returns {decision,
    stance, text}."""
    opts = options if options is not None else inbox_options(thread, facts)
    if thread.get("kind") == "CONTRACT":
        weights = contract_weights(opts, player_view, facts)
    else:
        weights = role_weights(player_view, facts)
    seq = message_seq if message_seq is not None else facts.get("message_seq", thread.get("seq", 0))
    lid = league_id if league_id is not None else thread.get("league_id", 0)
    decision = base.decide(cur, league_id=lid, season=thread["season"], tick=thread.get("week") or thread.get("tick"),
                           kind="INBOX_REPLY", subject=f"thread:{thread['id']}:msg:{seq}", options=opts,
                           persona=dict(player_view or {}, persona_type="player"), weights=weights, facts=facts,
                           seen=True, llm=llm, store=store)
    return {"decision": decision, "stance": decision["chosen_key"], "text": decision["text"]}
