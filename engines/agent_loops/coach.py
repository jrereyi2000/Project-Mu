"""The CPU coach loop (COACH_PLAYER_AGENT_LOOPS_PLAN.md §2.2).

A CPU coach decides his close depth-chart calls among the staff algorithm's legal charts, and can promise an
unhappy player of his a slot by a week. A promise is recorded (`promise_made`), reviewed against the MADDEN chart
at its review tick (`promise_resolved`: kept, broken, or unknown), and trust and trade requests react to it
through the relationships plan. JR's own coaches never run this loop.

  chart_options(neutral_chart, scored_candidates, close_window, *, scheme=None) -> [Option]
      NEUTRAL + up to CHART_VARIANTS one-swap variants: a starter and the first man behind him at one base slot,
      both healthy and eligible, neutral scores within close_window, and the promoted man starting nowhere else.
  apply_option(neutral_chart, option) -> chart
  chart_weights(options, coach_view, *, neutral_chart, candidates, close_window, playing_time=None,
                open_promises=()) -> {key: weight}
      The development plan's opportunity_adjustments (§2.5) are the lean: YOUTH_DEVELOPER toward the younger man,
      a reviewed veteran preference (team vision VETERANS) the other way. An open promise weights up the options
      that keep it, by the coach's drive and warmth; it never adds an option outside the window.
  chart_marquee(options, candidates) -> bool    a starting-QB call, or an incumbent starter benched while healthy
  choose_chart(cur, team_id, season, tick, coach_view, options, *, llm, ...) -> {chart, decision}
  promise_options(player, role_history, slot, *, tick=None, open_promise=False) -> [Option]   [] | NONE + PROMISE
  p_promise(coach_view) -> float               0.25 + 0.05 (warmth - 5) + 0.1 PLAYERS_COACH - 0.1 DISCIPLINARIAN
  decide_promise(cur, *, ...) -> {decision, event}
  review_promises(cur, season, tick, *, league_id, chart_depth, injured, ...) -> [review dicts]
  promise_made_event / promise_resolved_event   relationships-v1 envelopes (resolved PersonRefs)
  resolve_person(cur, universe_id, alias) -> PersonRef | None
  append_events(cur, events, *, team_registry) -> [receipts]   through personality.relationship_store

Every coefficient is a review-only estimate (plan §2.2).
"""
from __future__ import annotations

from copy import deepcopy

from engines import depth_chart_engine as dce
from engines import personality_coaching as pc
from engines.agent_loops import base

CHART_VARIANTS = 3
PROMISE_REVIEW_WEEKS = 3      # JR, 2026-11-03 (plan §8 Q2)
PROMISE_MIN_UNHAPPY_WEEKS = 2
PROMISE_REREVIEW_WEEKS = 1    # an injured (or unread) player is reviewed again once, a week later
LAST_PROMISE_TICK = 23        # relationship_producers.calendar.LAST_GAME_TICK: promises fall due in game weeks

NEUTRAL_WEIGHT = 1.0          # estimates
VARIANT_BASE = 0.15
LEAN_PER_POINT = 0.5          # weight per point of opportunity adjustment gap
PROMISE_LEAN = 0.6
P_PROMISE = dict(base=0.25, warmth=0.05, players_coach=0.1, disciplinarian=0.1, lo=0.05, hi=0.6)

LOOP_COACHING_VERSION = "agent-loop-chart-v1"
SOURCE_KIND = "agent_loop"
SCHEMA_VERSION = "relationships-v1"


# ------------------------------------------------------------------------------------------------ charts ---

def _cands(scored_candidates):
    return {(c["slot"], c["player_id"]): c for c in scored_candidates}


def _name(cands, pid):
    for (slot, p), c in cands.items():
        if p == pid and c.get("name"):
            return c["name"]
    return f"player {pid}"


def _healthy(c):
    return c is not None and bool(c.get("eligible")) and not c.get("out")


def chart_options(neutral_chart, scored_candidates, close_window, *, scheme=None):
    """NEUTRAL plus up to CHART_VARIANTS one-swap variants (module docstring), closest calls first."""
    cands = _cands(scored_candidates)
    counts = dce.starter_counts(scheme or {})
    starts = {pid for ids in dce.starters_by_base_slot(neutral_chart, scheme).values() for pid in ids}
    variants = []
    for slot_index, slot in enumerate(dce.BASE_SLOTS):
        ids = list(neutral_chart.get(slot) or [])
        n = counts.get(slot, 1)
        if len(ids) <= n:
            continue
        promote = ids[n]
        cp = cands.get((slot, promote))
        if not _healthy(cp) or promote in starts:
            continue
        for i in range(n):
            demote = ids[i]
            cd = cands.get((slot, demote))
            if not _healthy(cd):
                continue
            gap = abs(float(cd["neutral_score"]) - float(cp["neutral_score"]))
            if gap > close_window:
                continue
            variants.append((round(gap, 10), slot_index, i, slot, promote, demote, n))
    variants.sort()
    out = [base.Option("NEUTRAL", "KEEP", {}, "Keep the staff depth chart as it is")]
    for gap, _, i, slot, promote, demote, n in variants[:CHART_VARIANTS]:
        out.append(base.Option(
            f"SWAP:{slot}:{promote}:{demote}", "SWAP",
            {"slot": slot, "promote": promote, "demote": demote, "to_depth": i + 1, "from_depth": n + 1,
             "gap": gap},
            f"At {slot}, start {_name(cands, promote)} over {_name(cands, demote)} (a close call)"))
    return out


def _detail(option):
    return option.detail if isinstance(option, base.Option) else (option.get("detail") or {})


def _okey(option):
    return option.key if isinstance(option, base.Option) else option["key"]


def apply_option(neutral_chart, option):
    """The chart an option stands for: NEUTRAL is the staff chart; a SWAP trades the two men's places."""
    chart = {slot: list(ids) for slot, ids in neutral_chart.items()}
    d = _detail(option)
    if not d:
        return chart
    ids = chart[d["slot"]]
    a, b = ids.index(d["demote"]), ids.index(d["promote"])
    ids[a], ids[b] = ids[b], ids[a]
    return chart


def _depth_of(chart, slot, pid):
    ids = chart.get(slot) or []
    return ids.index(pid) + 1 if pid in ids else None


def keeps_promise(chart, promise):
    depth = _depth_of(chart, promise["slot"], promise["player_id"])
    return depth is not None and depth <= int(promise.get("depth", 1))


def _adjustments(candidates, coach_view, close_window, playing_time):
    config = pc.CoachingConfig(version=LOOP_COACHING_VERSION, mode="enabled",
                               weights={"youth_developer": 1.0, "team_vision": 1.0},
                               max_depth_score_shift=1.0, close_score_window=float(close_window))
    rows = [{k: c.get(k) for k in ("player_id", "slot", "neutral_score", "eligible", "out", "cohort",
                                   "cohort_weight") if k in c} for c in candidates]
    return pc.opportunity_adjustments(candidates=rows, coach_view=coach_view, config=config,
                                      playing_time=playing_time)["adjustments"]


def promise_lean(coach_view):
    drive, warmth = base.slider(coach_view, "drive"), base.slider(coach_view, "warmth")
    return PROMISE_LEAN * base.clamp(1 + 0.1 * (drive - 5) + 0.1 * (warmth - 5), 0.0, 2.0)


def chart_weights(options, coach_view, *, neutral_chart, candidates, close_window, playing_time=None,
                  open_promises=()):
    adj = _adjustments(candidates, coach_view, close_window, playing_time)
    lean = promise_lean(coach_view)
    out = {}
    for o in options:
        key, d = _okey(o), _detail(o)
        if not d:
            w = NEUTRAL_WEIGHT
        else:
            row = adj.get(d["slot"]) or {}
            w = VARIANT_BASE + LEAN_PER_POINT * max(0.0, row.get(d["promote"], 0.0) - row.get(d["demote"], 0.0))
        chart = apply_option(neutral_chart, o)
        for p in open_promises:
            if keeps_promise(chart, p) and not (d and keeps_promise(neutral_chart, p)):
                w += lean
        out[key] = round(w, 10)
    return out


def chart_marquee(options, candidates):
    cands = _cands(candidates)
    for o in options:
        d = _detail(o)
        if not d:
            continue
        if d["slot"] == "QB" and d["to_depth"] == 1:
            return True
        if (cands.get((d["slot"], d["demote"])) or {}).get("incumbent"):
            return True
    return False


def chart_facts(team_id, options, candidates, team_name=None):
    cands = _cands(candidates)
    calls = []
    for o in options:
        d = _detail(o)
        if d:
            calls.append({"slot": d["slot"], "promote": _name(cands, d["promote"]),
                          "demote": _name(cands, d["demote"])})
    facts = {"team_id": team_id, "close_calls": calls}
    if team_name:
        facts["team"] = team_name
    return facts


def choose_chart(cur, team_id, season, tick, coach_view, options, *, llm, league_id, neutral_chart, candidates,
                 close_window, playing_time=None, open_promises=(), facts=None, seen=False, store=None,
                 cause_refs=()):
    """The coach's chart for this tick and its decision row. One decision per team per tick
    (subject 'team:<id>:chart'); a starting-QB call or a benched incumbent is a marquee case."""
    persona = dict(coach_view or {}, persona_type="coach")
    weights = chart_weights(options, coach_view, neutral_chart=neutral_chart, candidates=candidates,
                            close_window=close_window, playing_time=playing_time, open_promises=open_promises)
    decision = base.decide(cur, league_id=league_id, season=season, tick=tick, kind="CHART",
                           subject=f"team:{team_id}:chart", options=options, persona=persona, weights=weights,
                           facts=facts if facts is not None else chart_facts(team_id, options, candidates),
                           seen=seen, llm=llm, marquee=chart_marquee(options, candidates), store=store,
                           cause_refs=cause_refs)
    return {"chart": apply_option(neutral_chart, decision["option"]), "decision": decision}


# ---------------------------------------------------------------------------------------------- promises ---

def unhappy_weeks(role_history):
    """Trailing run of 'unsatisfied' role assessments (oldest first)."""
    n = 0
    for row in reversed(list(role_history or [])):
        if row.get("assessment") != "unsatisfied":
            break
        n += 1
    return n


def promise_options(player, role_history, slot, *, tick=None, open_promise=False):
    """[] unless he has PROMISE_MIN_UNHAPPY_WEEKS unsatisfied weeks in a row, no open promise, and the review
    falls in a game week; else NONE and PROMISE {slot, depth 1, review_tick = tick + PROMISE_REVIEW_WEEKS}."""
    history = list(role_history or [])
    if open_promise or unhappy_weeks(history) < PROMISE_MIN_UNHAPPY_WEEKS:
        return []
    tick = int(tick if tick is not None else history[-1]["tick"])
    review = tick + PROMISE_REVIEW_WEEKS
    if review > LAST_PROMISE_TICK:
        return []
    name = player.get("name") or f"player {player['player_id']}"
    return [base.Option("NONE", "NO_PROMISE", {}, "Make no promise"),
            base.Option("PROMISE", "PROMISE", {"player_id": player["player_id"], "slot": slot, "depth": 1,
                                               "review_tick": review},
                        f"Promise {name} the {slot} starting job by week {review}")]


def p_promise(coach_view):
    t = base.traits_of(coach_view)
    p = (P_PROMISE["base"] + P_PROMISE["warmth"] * (base.slider(coach_view, "warmth") - 5)
         + (P_PROMISE["players_coach"] if "PLAYERS_COACH" in t else 0.0)
         - (P_PROMISE["disciplinarian"] if "DISCIPLINARIAN" in t else 0.0))
    return base.clamp(p, P_PROMISE["lo"], P_PROMISE["hi"])


def promise_id_for(league_id, season, tick, player_id):
    return f"loop:{league_id}:{season}:{tick}:{player_id}"


def decision_ref(row):
    return f"agent_loop_decisions:{row['league_id']}:{row['season']}:{row['tick']}:{row['kind']}:{row['subject_key']}"


def promise_rows(store, league_id, season):
    """Made promises this season (PROMISE decisions that chose PROMISE), oldest first."""
    return [r for r in store.find(league_id, season, "PROMISE") if r["chosen_key"] == "PROMISE"]


def _reviews(store, league_id, season):
    out = {}
    for r in store.find(league_id, season, "PROMISE_REVIEW"):
        out.setdefault(r["facts"]["promise_id"], []).append(r)
    return out


def open_promises(store, league_id, season, *, team_id=None):
    """Promises made this season without a final review: [{promise_id, player_id, slot, depth, review_tick,
    team_id}]; the next chart choice weights them as a soft preference."""
    reviews = _reviews(store, league_id, season)
    out = []
    for r in promise_rows(store, league_id, season):
        f = r["facts"]
        if team_id is not None and f.get("team_id") != team_id:
            continue
        if any(v["chosen_key"] != "DEFER" for v in reviews.get(f["promise_id"], [])):
            continue
        out.append({k: f.get(k) for k in ("promise_id", "player_id", "slot", "depth", "review_tick", "team_id")})
    return out


def decide_promise(cur, *, league_id, season, tick, team_id, coach_view, player, options, llm, seen=False,
                   store=None, universe_id=None, issuer=None, beneficiary=None, cause_refs=()):
    """The coach's NONE / PROMISE decision for one unhappy player (subject 'player:<id>:promise'). A made promise
    with resolved refs (issuer = the coach's PersonRef, beneficiary = the player's) also returns its promise_made
    envelope; without them the decision still stands and `event` is None (the refs can be resolved later)."""
    if not options:
        return {"decision": None, "event": None}
    st = base.store_for(cur, store)
    pid = player["player_id"]
    promise = next((_detail(o) for o in options if _okey(o) == "PROMISE"), {})
    facts = {"promise_id": promise_id_for(league_id, season, tick, pid), "player_id": pid,
             "player_name": player.get("name"), "team_id": team_id, "slot": promise.get("slot"),
             "depth": promise.get("depth"), "review_tick": promise.get("review_tick")}
    if issuer is not None and beneficiary is not None:
        facts["refs"] = {"universe_id": universe_id, "issuer": issuer, "beneficiary": beneficiary}
    p = p_promise(coach_view)
    decision = base.decide(st, league_id=league_id, season=season, tick=tick, kind="PROMISE",
                           subject=f"player:{pid}:promise", options=options,
                           persona=dict(coach_view or {}, persona_type="coach"),
                           weights={"PROMISE": p, "NONE": 1.0 - p}, facts=facts, seen=seen, llm=llm, store=st,
                           cause_refs=cause_refs)
    event = None
    f = decision["facts"]
    if decision["chosen_key"] == "PROMISE" and f.get("refs"):
        event = promise_made_event(universe_id=f["refs"]["universe_id"], issuer=f["refs"]["issuer"],
                                   beneficiary=f["refs"]["beneficiary"], team_id=team_id, season=season,
                                   tick=tick, slot=f["slot"], depth=f["depth"], review_tick=f["review_tick"],
                                   promise_id=f["promise_id"], authority_ref=decision_ref(decision))
    return {"decision": decision, "event": event}


def resolution(depth, promised_depth):
    if depth is None:
        return "unknown"
    return "kept" if int(depth) <= int(promised_depth) else "broken"


def review_promises(cur, season, tick, *, league_id, chart_depth, injured, store=None):
    """Review every promise due at this tick. chart_depth(team_id, player_id, slot) -> his depth at that slot on
    the MADDEN chart this tick (a large number when he isn't listed), or None with no chart read; injured(player_id)
    -> bool. Injured or unread at the first review: a DEFER review, judged again PROMISE_REREVIEW_WEEKS later; at
    that second review the resolution is unknown if he is still injured or unread. Each review is a
    PROMISE_REVIEW decision (subject 'promise:<promise_id>'), and a final one carries its promise_resolved
    envelope when the promise recorded resolved refs. Returns [{promise_id, resolution, decision, event}]."""
    st = base.store_for(cur, store)
    reviews = _reviews(st, league_id, season)
    out = []
    for r in promise_rows(st, league_id, season):
        f = r["facts"]
        done = reviews.get(f["promise_id"], [])
        if any(v["chosen_key"] != "DEFER" for v in done):
            continue
        deferred = [v for v in done if v["chosen_key"] == "DEFER"]
        due = (deferred[-1]["facts"]["next_review_tick"] if deferred else f["review_tick"])
        if due != tick:
            continue
        hurt = bool(injured(f["player_id"]))
        depth = None if hurt else chart_depth(f["team_id"], f["player_id"], f["slot"])
        if (hurt or depth is None) and not deferred:
            result = "DEFER"
        else:
            result = "UNKNOWN" if hurt else resolution(depth, f["depth"]).upper()
        facts = {"promise_id": f["promise_id"], "player_id": f["player_id"], "team_id": f["team_id"],
                 "slot": f["slot"], "depth": f["depth"], "depth_at_review": depth, "injured": hurt,
                 "next_review_tick": tick + PROMISE_REREVIEW_WEEKS if result == "DEFER" else None}
        option = base.Option(result, "REVIEW", {}, f"Promise review: {result.lower()}")
        decision = base.decide(st, league_id=league_id, season=season, tick=tick, kind="PROMISE_REVIEW",
                               subject=f"promise:{f['promise_id']}", options=[option], persona=None,
                               weights={result: 1.0}, facts=facts, seen=False, llm=None, store=st,
                               cause_refs=[decision_ref(r)])
        event = None
        refs = f.get("refs")
        if result != "DEFER" and refs:
            event = promise_resolved_event(
                universe_id=refs["universe_id"], issuer=refs["issuer"], beneficiary=refs["beneficiary"],
                season=season, tick=tick, promise_id=f["promise_id"], resolution=result.lower(),
                criterion_result={"slot": f["slot"], "max_depth": f["depth"], "depth_at_due": depth,
                                  "chart_source": "MADDEN", "injured": hurt},
                outcome_refs=[decision_ref(decision)])
        out.append({"promise_id": f["promise_id"], "resolution": result.lower(), "decision": decision,
                    "event": event})
    return out


# --------------------------------------------------------------------------------------------- envelopes ---

def _envelope(*, kind, universe_id, source_event_id, season, tick, subject, target, payload, refs, confidence):
    env = {"schema_version": SCHEMA_VERSION, "universe_id": universe_id,
           "event_id": f"{source_event_id}|{subject['persona_id']}", "source_event_id": source_event_id,
           "source_kind": SOURCE_KIND, "source_refs": [], "kind": kind,
           "occurred_at": {"season": int(season), "tick": int(tick)}, "subject": deepcopy(subject),
           "target": deepcopy(target), "actor": deepcopy(target), "basis": "verified_simulation",
           "confidence": confidence, "reviewer": None, "evidence_scope": [], "visibility": "private",
           "attribution": {"responsible": [], "reason_code": None, "evidence_refs": list(refs)},
           "payload": payload, "supersedes_source_event_id": None}
    return env


def promise_made_event(*, universe_id, issuer, beneficiary, team_id, season, tick, slot, depth, review_tick,
                       promise_id, authority_ref):
    """promise_made (relationships §2.2): subject = the player (beneficiary), target and actor = the coach
    (issuer); criterion {type: depth_chart_slot, slot, max_depth}; due at the review tick."""
    payload = {"promise_id": promise_id, "issuer": deepcopy(issuer), "beneficiary": deepcopy(beneficiary),
               "terms": {"kind": "role", "description": f"{slot} depth {depth} or better by week {review_tick}",
                         "criterion": {"type": "depth_chart_slot", "slot": slot, "max_depth": int(depth)}},
               "due_at": {"season": int(season), "tick": int(review_tick)}, "authority_ref": authority_ref,
               "team_key": f"team:{int(team_id)}"}
    return _envelope(kind="promise_made", universe_id=universe_id,
                     source_event_id=f"mu:loop:promise:{promise_id}:made", season=season, tick=tick,
                     subject=beneficiary, target=issuer, payload=payload, refs=[authority_ref], confidence="high")


def promise_resolved_event(*, universe_id, issuer, beneficiary, season, tick, promise_id, resolution,
                           criterion_result, outcome_refs):
    payload = {"promise_id": promise_id, "resolution": resolution, "criterion_result": deepcopy(criterion_result),
               "outcome_refs": list(outcome_refs), "beneficiary_ack_ref": None}
    return _envelope(kind="promise_resolved", universe_id=universe_id,
                     source_event_id=f"mu:loop:promise:{promise_id}:resolved", season=season, tick=tick,
                     subject=beneficiary, target=issuer, payload=payload, refs=list(outcome_refs),
                     confidence="high" if resolution != "unknown" else "low")


def resolve_person(cur, universe_id, alias):
    """PersonRef for a {role, namespace, external_id} alias (relationship producers' namespaces), or None."""
    from personality import store as persona_store
    ident = persona_store.resolve_alias(cur, universe_id, alias["role"], alias["namespace"],
                                        str(alias["external_id"]))
    if ident is None:
        return None
    return {"kind": "person", "universe_id": universe_id, "role": alias["role"], "persona_id": ident.persona_id}


def append_events(cur, events, *, team_registry, rel_store=None):
    """Append promise envelopes through the relationships store, one batch per universe; returns the receipts."""
    if rel_store is None:
        from personality import relationship_store as rel_store
    by_universe = {}
    for ev in events:
        if ev is not None:
            by_universe.setdefault(ev["universe_id"], []).append(ev)
    receipts = []
    for universe_id, batch in sorted(by_universe.items()):
        head = rel_store.read_head(cur, universe_id)
        receipts.append(rel_store.append_events(cur, batch, expected_head=head, team_registry=team_registry))
    return receipts
