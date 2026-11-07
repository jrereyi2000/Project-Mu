"""
Relationship producer rules (RELATIONSHIP_EVENT_PRODUCERS_PLAN.md §2.1-§2.2). Pure: plain dicts in, envelopes out,
no database. Every outcome is a fixed rule (plan D1) recorded as payload.rule = '<rule>@producers-v2'.

Envelopes follow PERSONALITY_RELATIONSHIPS_PLAN.md §2.2. Two fields are left for store.py (unit U2) to fill once
the subject's identity resolves: `universe_id` (envelope, subject, target, actor and attribution refs all carry
None) and `subject.persona_id`. Until then `subject_alias` {role, namespace, external_id} names the subject
(db-player / db-coach / db-coordinator aliases, plan S13). Teams are TeamRefs with team_key "team:<teams.id>".
`facet_key(envelope)` is the per-subject facet the pending queue keys on.

Input shapes (all plain dicts; extra keys are ignored):

  obs (one player-week roster observation; role_window / tenure_window / eligible):
    {player_id, team_id (None: free agent), season, tick, origin (players.origin or None),
     tier ('STARTER'|'ROTATION'|'RESERVE'|None, from role_tier), has_chart (bool: a MADDEN chart that week),
     bye (bool), first_week_on_team (bool),
     injury (None | {injury_id, weeks_remaining, is_ir, injury_type}), rating_rank (int|None)}
  history (established_role): the player's earlier observations, oldest first, as
    [{season, tick, team_id, tier, eligible (bool)}]; only the trailing run on the newest entry's team counts.
  move side (move_events prev / curr): {player_id, team_id or None, season, tick, origin}; prev None = no earlier
    observation (a draft pick or a new player).
  cause (move_events): None (unknown) or {kind: 'RELEASE'|'EXPIRY'|'TRADE'|'SIGN'|'DRAFT',
    ref ('<table>:<id>', the transactions / league_office_runs / franchise_pending_moves row),
    jr_move (bool), above_minimum (bool, SIGN only)}
  contract (recognition / contract_year):
    {contract_id, player_id, team_id, season, tick, status, years_remaining, origin,
     superseded_contract_id, superseded_team_id, jr_move}
  injury (injury_situation / injury_resolved): {injury_id, player_id, team_id, season, tick, injury_type,
    weeks_remaining, is_ir, origin}; the caller passes the first row of a stint (and, for resolved, the healed tick).
  award: {award_id, season, tick, award_type, team_id, player_id | coach_id, origin}
  team players (team_result events): [{player_id, origin}] on the team that tick.
  results (streak_events): the team's games so far, oldest first: [{tick, result: 'W'|'L'|'T'}].
  outcome (season_outcome_events): {kind: 'MISSED_PLAYOFFS'|'MADE_PLAYOFFS'|'LOST_ROUND'|'WON_SUPER_BOWL',
    round (int, LOST_ROUND only)}
  staff move (staff_move_events): {staff_move_id, season, tick, team_id, role ('HC'|'OC'|'DC'),
    action ('HIRED'|'LEFT'|'REPLACED'), source (staff_moves.source), coach_id, coordinator_id,
    fired (bool: a hot-seat row with fired true preceded this LEFT)}
  hot seat (hot_seat_pressure): {season, tick, team_id, role ('HC'|'OC'|'DC'), coach_id | coordinator_id,
    score, fired, firing_line (optional, default FIRING_LINE)}

`source` is 'SAVE' (save-observed, confidence high) or 'CATCHUP' (catch-up inference, confidence medium).
Players whose origin starts with HISTORY are skipped (plan D3): their builders return None / [].
"""

from engines import depth_chart_engine as dce
from engines.relationship_producers import PRODUCER_RULES_VERSION
from engines.relationship_producers.calendar import (FIRST_OFFSEASON_TICK, LAST_GAME_TICK, advance, is_in_season,
                                                     time_point)

RELATIONSHIP_SCHEMA_VERSION = "relationships-v1"   # PERSONALITY_RELATIONSHIPS_PLAN §2.1
SOURCE_KIND = "relationship_producer"
REF_NAMESPACE = "mu-producer"
SOURCES = ("SAVE", "CATCHUP")

# Review-only estimates (plan §8 Q2); every event records PRODUCER_RULES_VERSION, so a change replays cleanly.
ROLE_ESTABLISHED_WEEKS = 4     # consecutive eligible weeks at a tier before it is his established role
INJURY_SITUATION_WEEKS = 2     # weeks out before an injury is a situation
STREAK_GAMES = 3               # wins or losses in a row before a streak is a team_result
HOT_SEAT_PRESSURE = 0.5        # share of the firing line a kept coach's score needs to be job pressure
FIRING_LINE = 1.0              # engines/coach_hot_seat.py HOT_SEAT_LINE (a row may pass its own firing_line)

RESIGN_TICK = 25               # pipeline.league_office.STAGE_WEEKS["RESIGN"]
OFFSEASON_START_TICK = 24      # pipeline.league_office.STAGE_WEEKS["OFFSEASON_START"]
TREE_TICK = FIRST_OFFSEASON_TICK   # coaching_tree: an edge forms once the season it crossed is over (COACHING_TREES §2.6)

TIERS = ("RESERVE", "ROTATION", "STARTER")         # low to high
TIER_RANK = {t: i for i, t in enumerate(TIERS)}
ROTATION_SLOTS = ("SLWR", "3DRB", "PWHB", "SUBLB", "SLCB", "RLE", "RRE", "RDT", "NT")   # KR, PR, KOS, GAD ignored
NO_INJURY = ("", "NONE", "UNINJURED")


# ---------------------------------------------------------------- refs ----

def _alias(role, namespace, external_id):
    return {"role": role, "namespace": namespace, "external_id": str(int(external_id))}


def player_alias(player_id):
    """db-player alias for players.id."""
    return _alias("player", "db-player", player_id)


def coach_alias(coach_id):
    """db-coach alias for coaches.id."""
    return _alias("coach", "db-coach", coach_id)


def coordinator_alias(coordinator_id):
    """db-coordinator alias for coordinators.id. Coordinators are core role 'coach' (the core has no coordinator
    role; lifecycle plan §2.1), told apart from head coaches by the namespace."""
    return _alias("coach", "db-coordinator", coordinator_id)


def person_subject(alias):
    """PersonRef for an alias, with universe_id and persona_id left for store.py."""
    return {"kind": "person", "universe_id": None, "role": alias["role"], "persona_id": None}


def team_key(team_id):
    return f"team:{int(team_id)}"


def team_ref(team_id):
    """TeamRef keyed on teams.id (stable inside a franchise and through relocation)."""
    return {"kind": "team", "universe_id": None, "team_key": team_key(team_id)}


def source_ref(table, row_id):
    return {"namespace": REF_NAMESPACE, "key": f"{table}:{row_id}"}


def is_history(origin):
    """True for players the HISTORY backfill created (origin HISTORY, HISTORY_FILLIN, ...)."""
    return str(origin or "").upper().startswith("HISTORY")


# ---------------------------------------------------------- source IDs ----

def tenure_source_id(season, tick, player_id):
    return f"mu:tenure:{season}:{tick}:{player_id}"


def role_source_id(season, tick, player_id):
    """The role_window's source_event_id; the inbox cites it in promise outcome_refs (plan S16)."""
    return f"mu:role:{season}:{tick}:{player_id}"


def release_source_id(season, tick, player_id):
    return f"mu:release:{season}:{tick}:{player_id}"


def contract_source_id(contract_id):
    return f"mu:contract:{contract_id}"


def arrival_source_id(season, tick, player_id, team_id):
    return f"mu:arrive:{season}:{tick}:{player_id}:{team_id}"


def departure_source_id(season, tick, player_id, team_id):
    return f"mu:depart:{season}:{tick}:{player_id}:{team_id}"


def injury_source_id(injury_id):
    return f"mu:injury:{injury_id}"


def injury_resolved_source_id(injury_id):
    """Healing is its own occurrence of the same injury, so it gets a suffix (plan §2.1 names only the injury)."""
    return f"mu:injury:{injury_id}:resolved"


def award_source_id(award_id):
    return f"mu:award:{award_id}"


def result_source_id(season, team_id, kind, tick):
    return f"mu:result:{season}:{team_id}:{kind}:{tick}"


def contract_year_source_id(season, player_id):
    return f"mu:contract-year:{season}:{player_id}"


def staff_source_id(staff_move_id):
    return f"mu:staff:{staff_move_id}"


def hot_seat_source_id(season, team_id, role):
    return f"mu:hotseat:{season}:{team_id}:{role}"


def tree_source_id(coach_id, coordinator_id):
    """One simulated coaching-tree edge (head coach -> his coordinator): both facets share it."""
    return f"mu:tree:{int(coach_id)}:{int(coordinator_id)}"


# ------------------------------------------------------------ envelope ----

def rule_name(name):
    return f"{name}@{PRODUCER_RULES_VERSION}"


def _confidence(source):
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
    return "high" if source == "SAVE" else "medium"


def _target_key(ref):
    if ref is None:
        return ""
    if ref.get("kind") == "team":
        return ref["team_key"]
    return f"{ref.get('role')}:{ref.get('persona_id')}"


def facet_key(envelope):
    """'<role>:<namespace>:<external_id>|<target key>|<kind>': one facet per affected subject of an occurrence."""
    a = envelope["subject_alias"]
    t = envelope.get("target_alias")
    target = f"{t['role']}:{t['namespace']}:{t['external_id']}" if t else _target_key(envelope["target"])
    return f"{a['role']}:{a['namespace']}:{a['external_id']}|{target}|{envelope['kind']}"


def envelope(*, kind, source_event_id, season, tick, alias, target, payload, rule, source="SAVE",
             visibility="public", actor=None, responsible=(), reason_code=None, refs=(), evidence_scope=(),
             own_refs=()):
    """One canonical envelope (relationships §2.2). universe_id and subject.persona_id stay None for store.py.

    `own_refs` become source_refs: only a row this occurrence owns outright (an injuries, awards, staff_moves or
    hot-seat row, a re-sign's new contract), because the relationships store lets one external ref name exactly
    one occurrence. Shared evidence (a roster, a depth chart, a game, a league-office run, a cause row naming
    several events) goes in `refs`, which land only in attribution.evidence_refs."""
    own_refs = list(own_refs)
    refs = own_refs + list(refs)
    env = {
        "schema_version": RELATIONSHIP_SCHEMA_VERSION,
        "universe_id": None,
        "event_id": None,
        "source_event_id": source_event_id,
        "source_kind": SOURCE_KIND,
        "source_refs": own_refs,
        "kind": kind,
        "occurred_at": time_point(season, tick),
        "subject": person_subject(alias),
        "subject_alias": dict(alias),
        "target": target,
        "actor": actor,
        "basis": "verified_simulation",
        "confidence": _confidence(source),
        "reviewer": None,
        "evidence_scope": sorted(set(evidence_scope)),
        "visibility": visibility,
        "attribution": {"responsible": list(responsible), "reason_code": reason_code,
                        "evidence_refs": [f"{r['namespace']}:{r['key']}" for r in refs]},
        "payload": dict(payload, rule=rule_name(rule)),
        "supersedes_source_event_id": None,
    }
    env["event_id"] = f"{source_event_id}|{facet_key(env)}"
    return env


def _situation(*, situation_kind, state, valid_until, source_event_id, season, tick, alias, target, rule, source,
               outcome_refs=(), refs=(), responsible=(), reason_code=None, actor=None, evidence_scope=(),
               visibility="public", extra=None, own_refs=()):
    payload = {"situation_kind": situation_kind, "state": state, "valid_until": valid_until,
               "outcome_refs": list(outcome_refs)}
    payload.update(extra or {})
    return envelope(kind="situation", source_event_id=source_event_id, season=season, tick=tick, alias=alias,
                    target=target, payload=payload, rule=rule, source=source, visibility=visibility, actor=actor,
                    responsible=responsible, reason_code=reason_code, refs=refs, evidence_scope=evidence_scope,
                    own_refs=own_refs)


def _treatment(*, treatment_kind, outcome, team_id, source_event_id, season, tick, alias, rule, source,
               outcome_refs=(), refs=(), reason_code=None, evidence_scope=(), own_refs=()):
    payload = {"team_key": team_key(team_id), "treatment_kind": treatment_kind, "outcome": outcome,
               "significance": "meaningful", "outcome_refs": list(outcome_refs)}
    return envelope(kind="treatment", source_event_id=source_event_id, season=season, tick=tick, alias=alias,
                    target=team_ref(team_id), payload=payload, rule=rule, source=source, actor=team_ref(team_id),
                    responsible=[team_ref(team_id)], reason_code=reason_code, refs=refs,
                    evidence_scope=evidence_scope, own_refs=own_refs)


# ------------------------------------------------------------ role tier ----

def role_tier(chart, scheme, player_id):
    """'STARTER' | 'ROTATION' | 'RESERVE' for one player-week from a MADDEN chart {slot: [player_id, ...]}.

    STARTER: a starter in any base slot (depth_chart_engine.starters_by_base_slot, the engine's starter counts for
    the head coach's scheme). ROTATION: depth 1 in a ROTATION_SLOTS slot, or the first player after the starters in
    a base slot. RESERVE: anything else (the caller only asks about rostered players)."""
    starters = dce.starters_by_base_slot(chart, scheme)
    if any(player_id in ids for ids in starters.values()):
        return "STARTER"
    if any(list(chart.get(slot) or [])[:1] == [player_id] for slot in ROTATION_SLOTS):
        return "ROTATION"
    counts = dce.starter_counts(scheme or {})
    for slot in dce.BASE_SLOTS:
        ids = list(chart.get(slot) or [])
        if len(ids) > counts[slot] and ids[counts[slot]] == player_id:
            return "ROTATION"
    return "RESERVE"


def rating_rank_at_position(team_players, player_id):
    """1-based OVR rank of player_id among team_players [{player_id, position, overall}] at his position (ties:
    lower player_id first), or None when he isn't listed. A fact only: it never sets an assessment (plan D2)."""
    me = next((p for p in team_players if p["player_id"] == player_id), None)
    if me is None:
        return None
    same = [p for p in team_players if p.get("position") == me.get("position")]
    same.sort(key=lambda p: (-(p.get("overall") or 0), p["player_id"]))
    return [p["player_id"] for p in same].index(player_id) + 1


# ----------------------------------------------------- eligibility, role ----

def _real_injury_type(injury_type):
    return str(injury_type or "").strip().upper() not in NO_INJURY


def eligible(obs):
    """(eligible, reason) for one player-week. reason is None when eligible, else the first of:
    'offseason' (tick > 23), 'no_chart', 'bye', 'injured' (weeks_remaining > 0 or is_ir, the Madden stuck-IR case
    included: injury_type NONE, 0 weeks, is_ir true), 'first_week_on_team'."""
    if not is_in_season(obs["tick"]):
        return False, "offseason"
    if not obs.get("has_chart"):
        return False, "no_chart"
    if obs.get("bye"):
        return False, "bye"
    inj = obs.get("injury")
    if inj and ((inj.get("weeks_remaining") or 0) > 0 or inj.get("is_ir")):
        return False, "injured"
    if obs.get("first_week_on_team"):
        return False, "first_week_on_team"
    return True, None


def _higher(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return a if TIER_RANK[a] >= TIER_RANK[b] else b


def established_role(history):
    """The established tier before the week being assessed, or None.

    Only the trailing run of `history` on the newest entry's team counts (a new team starts with none). Within a
    season it is the highest tier he held for ROLE_ESTABLISHED_WEEKS consecutive eligible weeks (holding a higher
    tier counts as holding the lower ones); ineligible weeks pause the count without breaking it. At a season
    boundary it resets to the tier he held for the most eligible weeks of the season just ended (ties: the higher
    tier; no eligible weeks: None), and later runs in the new season can raise it."""
    if not history:
        return None
    team = history[-1].get("team_id")
    run = []
    for h in reversed(history):
        if h.get("team_id") != team:
            break
        run.append(h)
    run.reverse()

    established = None
    season = None
    streaks = {t: 0 for t in TIERS}
    weeks = {t: 0 for t in TIERS}
    for h in run:
        if h["season"] != season:
            if season is not None:
                best = max(TIERS, key=lambda t: (weeks[t], TIER_RANK[t]))
                established = best if weeks[best] > 0 else None
            season = h["season"]
            streaks = {t: 0 for t in TIERS}
            weeks = {t: 0 for t in TIERS}
        if not h.get("eligible") or h.get("tier") not in TIER_RANK:
            continue                                          # pause: neither counts nor breaks the run
        rank = TIER_RANK[h["tier"]]
        weeks[h["tier"]] += 1
        for t in TIERS:
            streaks[t] = streaks[t] + 1 if rank >= TIER_RANK[t] else 0
            if streaks[t] >= ROLE_ESTABLISHED_WEEKS:
                established = _higher(established, t)
    return established


def assessment(obs, agreed_role):
    """'satisfied' | 'unsatisfied' | 'unknown' (plan D2: a demotion only while healthy)."""
    ok, _ = eligible(obs)
    tier = obs.get("tier")
    if not ok or agreed_role is None or tier not in TIER_RANK:
        return "unknown"
    return "satisfied" if TIER_RANK[tier] >= TIER_RANK[agreed_role] else "unsatisfied"


# -------------------------------------------------------------- windows ----

def _window(season, tick):
    return time_point(season, tick), advance(season, tick, 1)


def tenure_window(obs, *, coverage="complete", source="SAVE", flags=()):
    """tenure_window for a player on a team this tick (public), or None for a free agent or a HISTORY player.
    coverage 'complete' from the save, 'partial' when the catch-up inferred membership. flags (e.g.
    'cause_unknown' from move_events) go into payload.flags."""
    if obs.get("team_id") is None or is_history(obs.get("origin")):
        return None
    if coverage not in ("complete", "partial"):
        raise ValueError(f"coverage must be complete or partial, got {coverage!r}")
    season, tick, pid = obs["season"], obs["tick"], obs["player_id"]
    start, end = _window(season, tick)
    payload = {"team_key": team_key(obs["team_id"]), "start": start, "end": end, "coverage": coverage}
    if flags:
        payload["flags"] = sorted(set(flags))
    return envelope(kind="tenure_window", source_event_id=tenure_source_id(season, tick, pid), season=season,
                    tick=tick, alias=player_alias(pid), target=team_ref(obs["team_id"]), payload=payload,
                    rule="tenure_window", source=source,
                    refs=[source_ref("roster", f"{obs['team_id']}:{season}:{tick}")], evidence_scope=["roster"])


def role_window(obs, history, *, source="SAVE"):
    """role_window for a player on a team in an in-season tick with a MADDEN chart (private), or None (offseason,
    free agent, no chart, HISTORY player). agreed_role = established_role(history) (history: earlier weeks only);
    rating_rank_at_position is recorded as a fact and never changes the assessment (plan D2)."""
    if (obs.get("team_id") is None or is_history(obs.get("origin")) or obs["tick"] > LAST_GAME_TICK
            or not obs.get("has_chart") or obs.get("tier") not in TIER_RANK):
        return None
    season, tick, pid, team = obs["season"], obs["tick"], obs["player_id"], obs["team_id"]
    agreed = established_role(history)
    refs = [source_ref("depth_charts", f"{team}:{season}:{tick}:MADDEN")]
    scope = ["depth_charts"]
    inj = obs.get("injury")
    if inj and inj.get("injury_id") is not None:
        refs.append(source_ref("injuries", inj["injury_id"]))
        scope.append("injuries")
    start, end = _window(season, tick)
    ok, reason = eligible(obs)
    payload = {"team_key": team_key(team), "start": start, "end": end, "agreed_role": agreed,
               "observed_role": obs["tier"], "assessment": assessment(obs, agreed),
               "assessment_basis_refs": [f"{r['namespace']}:{r['key']}" for r in refs],
               "eligible": ok, "ineligible_reason": reason,
               "rating_rank_at_position": obs.get("rating_rank")}
    return envelope(kind="role_window", source_event_id=role_source_id(season, tick, pid), season=season, tick=tick,
                    alias=player_alias(pid), target=team_ref(team), payload=payload, rule="role_window",
                    source=source, visibility="private", refs=refs, evidence_scope=scope)


# ---------------------------------------------------------------- moves ----

def _arrival(curr, *, state, cause, source, reason_code):
    season, tick, pid, team = curr["season"], curr["tick"], curr["player_id"], curr["team_id"]
    refs = [source_ref(*cause["ref"].split(":", 1))] if cause.get("ref") else []
    return _situation(situation_kind="arrival", state=state, valid_until=time_point(season + 1, tick),
                      source_event_id=arrival_source_id(season, tick, pid, team), season=season, tick=tick,
                      alias=player_alias(pid), target=team_ref(team), rule=f"arrival_{cause['kind'].lower()}",
                      source=source, refs=refs, outcome_refs=[f"{r['namespace']}:{r['key']}" for r in refs],
                      responsible=[team_ref(team)], reason_code=reason_code, actor=team_ref(team),
                      evidence_scope=["roster"])


def _departure(prev, curr, *, state, cause, source, reason_code):
    season, tick, pid, team = curr["season"], curr["tick"], curr["player_id"], prev["team_id"]
    refs = [source_ref(*cause["ref"].split(":", 1))] if cause.get("ref") else []
    return _situation(situation_kind="departure", state=state, valid_until=time_point(season + 1, tick),
                      source_event_id=departure_source_id(season, tick, pid, team), season=season, tick=tick,
                      alias=player_alias(pid), target=team_ref(team), rule=f"departure_{cause['kind'].lower()}",
                      source=source, refs=refs, outcome_refs=[f"{r['namespace']}:{r['key']}" for r in refs],
                      responsible=[team_ref(team)], reason_code=reason_code, actor=team_ref(team),
                      evidence_scope=["roster"])


def move_events(prev, curr, cause, *, source="SAVE"):
    """Events for one player's roster change between consecutive observations (plan §2.2 move table).

    Returns {"events": [envelopes], "flags": [str]}; flags ['cause_unknown'] for a change with no known cause (the
    caller puts it on the tenure facts; no treatment, no situation).
      T -> free agent, RELEASE: treatment dismissal toward T + departure (negative)
      T -> free agent, EXPIRY:  departure (uncertain), no treatment
      T -> U, TRADE:            departure from T + arrival at U (both uncertain), no treatment
      T -> U, RELEASE:          dismissal + departure from T (negative) + arrival at U (uncertain; claimed)
      free agent / none -> U, DRAFT or SIGN: arrival (positive for a draft pick or an above-minimum signing,
                                else uncertain)
    A JR-written move records attribution.reason_code 'JR_MOVE'."""
    out = {"events": [], "flags": []}
    if is_history(curr.get("origin")) or (prev and is_history(prev.get("origin"))):
        return out
    before = prev.get("team_id") if prev else None
    after = curr.get("team_id")
    if before == after:
        return out
    kind = (cause or {}).get("kind")
    known = {
        (True, False): ("RELEASE", "EXPIRY"),       # (was on a team, is on a team)
        (True, True): ("TRADE", "RELEASE"),
        (False, True): ("DRAFT", "SIGN"),
    }[(before is not None, after is not None)]
    if kind not in known:
        out["flags"].append("cause_unknown")
        return out
    reason = "JR_MOVE" if cause.get("jr_move") else kind
    ev = out["events"]
    if kind == "RELEASE":
        season, tick, pid = curr["season"], curr["tick"], curr["player_id"]
        refs = [source_ref(*cause["ref"].split(":", 1))] if cause.get("ref") else []
        ev.append(_treatment(treatment_kind="dismissal", outcome="negative", team_id=before,
                             source_event_id=release_source_id(season, tick, pid), season=season, tick=tick,
                             alias=player_alias(pid), rule="release_dismissal", source=source, refs=refs,
                             outcome_refs=[f"{r['namespace']}:{r['key']}" for r in refs], reason_code=reason,
                             evidence_scope=["roster"]))
        ev.append(_departure(prev, curr, state="negative", cause=cause, source=source, reason_code=reason))
        if after is not None:
            ev.append(_arrival(curr, state="uncertain", cause=cause, source=source, reason_code=reason))
    elif kind == "EXPIRY":
        ev.append(_departure(prev, curr, state="uncertain", cause=cause, source=source, reason_code=reason))
    elif kind == "TRADE":
        ev.append(_departure(prev, curr, state="uncertain", cause=cause, source=source, reason_code=reason))
        ev.append(_arrival(curr, state="uncertain", cause=cause, source=source, reason_code=reason))
    else:
        positive = kind == "DRAFT" or bool(cause.get("above_minimum"))
        ev.append(_arrival(curr, state="positive" if positive else "uncertain", cause=cause, source=source,
                           reason_code=reason))
    return out


def recognition(contract, *, source="SAVE"):
    """treatment recognition (positive) for a new ACTIVE contract replacing a SUPERSEDED one on the same team (a
    re-sign or extension), or None."""
    if (is_history(contract.get("origin")) or contract.get("status") != "ACTIVE"
            or contract.get("superseded_contract_id") is None
            or contract.get("superseded_team_id") != contract.get("team_id")):
        return None
    refs = [source_ref("player_contracts", contract["contract_id"]),
            source_ref("player_contracts", contract["superseded_contract_id"])]
    return _treatment(treatment_kind="recognition", outcome="positive", team_id=contract["team_id"],
                      source_event_id=contract_source_id(contract["contract_id"]), season=contract["season"],
                      tick=contract["tick"], alias=player_alias(contract["player_id"]), rule="resign_recognition",
                      source=source, own_refs=refs[:1], refs=refs[1:],
                      outcome_refs=[f"{refs[0]['namespace']}:{refs[0]['key']}"],
                      reason_code="JR_MOVE" if contract.get("jr_move") else "RE_SIGNED",
                      evidence_scope=["player_contracts"])


# ----------------------------------------------------------- situations ----

def injury_situation(injury, *, source="SAVE"):
    """situation injury (negative) for the first row of a stint with weeks_remaining >= INJURY_SITUATION_WEEKS, or
    IR with a real injury_type; valid until the expected return tick. None for minor injuries and Madden's
    stuck-IR rows (injury_type NONE, 0 weeks)."""
    if is_history(injury.get("origin")):
        return None
    weeks = injury.get("weeks_remaining") or 0
    real = _real_injury_type(injury.get("injury_type"))
    if not ((weeks >= INJURY_SITUATION_WEEKS and real) or (injury.get("is_ir") and real)):
        return None
    season, tick = injury["season"], injury["tick"]
    ref = source_ref("injuries", injury["injury_id"])
    return _situation(situation_kind="injury", state="negative", valid_until=advance(season, tick, max(weeks, 1)),
                      source_event_id=injury_source_id(injury["injury_id"]), season=season, tick=tick,
                      alias=player_alias(injury["player_id"]), target=team_ref(injury["team_id"]),
                      rule="injury", source=source, own_refs=[ref], outcome_refs=[f"{ref['namespace']}:{ref['key']}"],
                      reason_code="INJURY", evidence_scope=["injuries"],
                      extra={"injury_type": injury.get("injury_type"), "weeks_out": weeks,
                             "is_ir": bool(injury.get("is_ir"))})


def injury_resolved(injury, healed_season, healed_tick, *, source="SAVE"):
    """situation injury 'resolved' when a player whose stint produced an injury situation heals (or None when the
    stint never qualified)."""
    if injury_situation(injury, source=source) is None:
        return None
    ref = source_ref("injuries", injury["injury_id"])
    return _situation(situation_kind="injury", state="resolved", valid_until=None,
                      source_event_id=injury_resolved_source_id(injury["injury_id"]), season=healed_season,
                      tick=healed_tick, alias=player_alias(injury["player_id"]), target=team_ref(injury["team_id"]),
                      rule="injury_resolved", source=source, refs=[ref],
                      outcome_refs=[injury_source_id(injury["injury_id"])], reason_code="INJURY_HEALED",
                      evidence_scope=["injuries"])


def award_situation(award, *, source="SAVE"):
    """situation award (positive, public) for a player's or head coach's awards row, valid until next season."""
    if award.get("player_id") is not None:
        if is_history(award.get("origin")):
            return None
        alias = player_alias(award["player_id"])
    elif award.get("coach_id") is not None:
        alias = coach_alias(award["coach_id"])
    else:
        return None
    if award.get("team_id") is None:
        return None
    season, tick = award["season"], award["tick"]
    ref = source_ref("awards", award["award_id"])
    return _situation(situation_kind="award", state="positive", valid_until=time_point(season + 1, 1),
                      source_event_id=award_source_id(award["award_id"]), season=season, tick=tick, alias=alias,
                      target=team_ref(award["team_id"]), rule="award", source=source, own_refs=[ref],
                      outcome_refs=[f"{ref['namespace']}:{ref['key']}"], reason_code="AWARD",
                      evidence_scope=["awards"], extra={"award_type": award.get("award_type")})


def _team_result(team_id, season, tick, players, *, kind, state, valid_until, rule, source, extra):
    sid = result_source_id(season, team_id, kind, tick)
    ref = source_ref("games", f"{team_id}:{season}:{tick}")
    return [_situation(situation_kind="team_result", state=state, valid_until=valid_until, source_event_id=sid,
                       season=season, tick=tick, alias=player_alias(p["player_id"]), target=team_ref(team_id),
                       rule=rule, source=source, refs=[ref], outcome_refs=[f"{ref['namespace']}:{ref['key']}"],
                       reason_code="TEAM_RESULT", evidence_scope=["games"], extra=extra)
            for p in sorted(players, key=lambda p: p["player_id"]) if not is_history(p.get("origin"))]


def streak_events(team_id, season, tick, results, players, *, next_game_tick=None, source="SAVE"):
    """team_result for every player on the team when its games up to this tick end in exactly STREAK_GAMES wins
    (positive) or losses (negative) in a row (one event per streak, on the game that reaches it). valid_until the
    next game (next_game_tick, else the next tick)."""
    played = [r for r in results if r["tick"] <= tick]
    if not played or played[-1]["tick"] != tick or played[-1]["result"] not in ("W", "L"):
        return []
    last = played[-1]["result"]
    run = 0
    for r in reversed(played):
        if r["result"] != last:
            break
        run += 1
    if run != STREAK_GAMES:
        return []
    until = time_point(season, next_game_tick) if next_game_tick else advance(season, tick, 1)
    kind = "win-streak" if last == "W" else "loss-streak"
    return _team_result(team_id, season, tick, players, kind=kind, state="positive" if last == "W" else "negative",
                        valid_until=until, rule=kind.replace("-", "_"), source=source,
                        extra={"result_kind": kind, "games": run})


SEASON_OUTCOMES = {"MISSED_PLAYOFFS": "negative", "MADE_PLAYOFFS": "positive", "LOST_ROUND": "uncertain",
                   "WON_SUPER_BOWL": "positive"}


def season_outcome_events(team_id, season, tick, outcome, players, *, source="SAVE"):
    """team_result for every player on the team: missed playoffs (negative), made playoffs (positive), lost in
    round N (uncertain: he got there), won the Super Bowl (positive); valid until next season's first tick."""
    kind = outcome["kind"]
    if kind not in SEASON_OUTCOMES:
        raise ValueError(f"unknown season outcome {kind!r}")
    label = kind.lower().replace("_", "-")
    extra = {"result_kind": label}
    if kind == "LOST_ROUND":
        label = f"lost-round-{int(outcome['round'])}"
        extra = {"result_kind": "lost-round", "round": int(outcome["round"])}
    return _team_result(team_id, season, tick, players, kind=label, state=SEASON_OUTCOMES[kind],
                        valid_until=time_point(season + 1, 1), rule="season_outcome", source=source, extra=extra)


def contract_year(contract, tick, *, source="SAVE"):
    """situation job_pressure (uncertain) for an ACTIVE contract with years_remaining 1 at the season's first tick,
    valid until RESIGN (tick 25), or None."""
    if (tick != 1 or is_history(contract.get("origin")) or contract.get("status") != "ACTIVE"
            or contract.get("years_remaining") != 1):
        return None
    season = contract["season"]
    ref = source_ref("player_contracts", contract["contract_id"])
    return _situation(situation_kind="job_pressure", state="uncertain", valid_until=time_point(season, RESIGN_TICK),
                      source_event_id=contract_year_source_id(season, contract["player_id"]), season=season,
                      tick=tick, alias=player_alias(contract["player_id"]), target=team_ref(contract["team_id"]),
                      rule="contract_year", source=source, refs=[ref],
                      outcome_refs=[f"{ref['namespace']}:{ref['key']}"], reason_code="CONTRACT_YEAR",
                      evidence_scope=["player_contracts"])


# ---------------------------------------------------------- coach facts ----

def _staff_alias(row):
    if row.get("role") == "HC":
        return coach_alias(row["coach_id"]) if row.get("coach_id") is not None else None
    return coordinator_alias(row["coordinator_id"]) if row.get("coordinator_id") is not None else None


def staff_move_events(move, *, source="SAVE"):
    """Coach facts from one staff_moves row (held by store.py until the coach has an identity, plan D4):
    LEFT after a hot-seat firing -> treatment dismissal toward the team; HIRED with source PROMOTED -> treatment
    recognition; any other HIRED -> situation arrival (positive). Anything else (a LEFT without a firing, i.e.
    poached or retired, and REPLACED) -> []."""
    alias = _staff_alias(move)
    if alias is None:
        return []
    season, tick, team = move["season"], move["tick"], move["team_id"]
    sid = staff_source_id(move["staff_move_id"])
    refs = [source_ref("staff_moves", move["staff_move_id"])]
    orefs = [f"{refs[0]['namespace']}:{refs[0]['key']}"]
    action = move.get("action")
    if action == "LEFT" and move.get("fired"):
        return [_treatment(treatment_kind="dismissal", outcome="negative", team_id=team, source_event_id=sid,
                           season=season, tick=tick, alias=alias, rule="coach_fired", source=source, own_refs=refs,
                           outcome_refs=orefs, reason_code="FIRED", evidence_scope=["staff_moves", "hot_seat"])]
    if action == "HIRED" and move.get("source") == "PROMOTED":
        return [_treatment(treatment_kind="recognition", outcome="positive", team_id=team, source_event_id=sid,
                           season=season, tick=tick, alias=alias, rule="coach_promoted", source=source, own_refs=refs,
                           outcome_refs=orefs, reason_code="PROMOTED", evidence_scope=["staff_moves"])]
    if action == "HIRED":
        return [_situation(situation_kind="arrival", state="positive", valid_until=time_point(season + 1, tick),
                           source_event_id=sid, season=season, tick=tick, alias=alias, target=team_ref(team),
                           rule="coach_hired", source=source, own_refs=refs, outcome_refs=orefs,
                           responsible=[team_ref(team)], reason_code="HIRED", actor=team_ref(team),
                           evidence_scope=["staff_moves"], extra={"staff_role": move.get("role")})]
    return []


def hot_seat_pressure(row, *, source="SAVE"):
    """situation job_pressure (uncertain) for a coach or coordinator kept (fired false) with score >=
    HOT_SEAT_PRESSURE x the firing line, valid until next season's offseason start; else None."""
    alias = _staff_alias(row)
    if alias is None or row.get("fired"):
        return None
    line = row.get("firing_line", FIRING_LINE)
    score = float(row.get("score") or 0)
    if score < HOT_SEAT_PRESSURE * line:
        return None
    season, tick, team, role = row["season"], row["tick"], row["team_id"], row["role"]
    table = "coach_hot_seat" if role == "HC" else "coordinator_hot_seat"
    ref = source_ref(table, f"{season}:{team}:{role}")
    return _situation(situation_kind="job_pressure", state="uncertain",
                      valid_until=time_point(season + 1, OFFSEASON_START_TICK),
                      source_event_id=hot_seat_source_id(season, team, role), season=season, tick=tick, alias=alias,
                      target=team_ref(team), rule="hot_seat_kept", source=source, own_refs=[ref],
                      outcome_refs=[f"{ref['namespace']}:{ref['key']}"], reason_code="HOT_SEAT",
                      evidence_scope=[table], extra={"score": round(score, 3), "firing_line": line,
                                                     "staff_role": role})


# --------------------------------------------------------- coaching trees ----

def coaching_tree_events(edge, *, source="SAVE"):
    """Two meaningful_interaction facets for one simulated coaching-tree edge (personality.coaching_trees.sim_edges):
    the protégé (db-coordinator) toward his mentor (db-coach) with direction 'mentee', and the mirror facet with
    direction 'mentor', at (crossed_season, TREE_TICK). Person targets go by target_alias, so store.py holds each
    facet until both identities exist."""
    mentor = coach_alias(edge["mentor_ref"]["external_id"])
    protege = coordinator_alias(edge["protege_ref"]["external_id"])
    coach_id, coordinator_id = mentor["external_id"], protege["external_id"]
    sid = tree_source_id(coach_id, coordinator_id)
    opportunity = f"coach_tenures:{coach_id}|coordinator_tenures:{coordinator_id}"
    out = []
    for direction, alias, other in (("mentee", protege, mentor), ("mentor", mentor, protege)):
        env = envelope(kind="meaningful_interaction", source_event_id=sid, season=edge["crossed_season"],
                       tick=TREE_TICK, alias=alias, target=person_subject(other),
                       payload={"relationship_kind": "mentorship", "outcome": edge["outcome"],
                                "significance": "meaningful", "opportunity_ref": opportunity,
                                "direction": direction, "seasons": edge["seasons"]},
                       rule="coaching_tree", source=source,
                       evidence_scope=["coach_tenures", "coordinator_tenures"])
        env["target_alias"] = dict(other)
        env["event_id"] = f"{sid}|{facet_key(env)}"
        out.append(env)
    return out
