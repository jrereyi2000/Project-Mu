"""The CPU coach loop's promise watcher (AGENT_LOOPS_FOLLOWUPS_PLAN.md §2.1).

One tick of the coach loop's promises for a save ingest: review every promise due at this tick, then offer a
promise to each CPU team's unhappy player who has just lost his starting job. CPU teams are every team not in the
franchise registry's `user_teams`; JR's coach never runs this loop.

  promise_tick(cur, *, league_id, season, tick, universe_id, mode, user_team_ids, snapshot=None, store=None,
               llm=None) -> {"status", "made", "reviewed", "kept", "broken", "unknown", "deferred", "errors"}

mode 'off' does nothing. 'shadow' records the decisions (agent_loop_decisions) and appends no relationship
envelopes; 'active' also appends promise_made / promise_resolved through the relationships store. The caller owns
the cursor and the transaction: nothing here connects, commits or rolls back.

Reviews come first, so a promise made this tick is not reviewed again until its due tick. Role history is read
from the relationships ledger's `role_window` events on the player's team (oldest first, mapped to {tick,
assessment} for coach.unhappy_weeks); roles are never re-derived here. The lost slot is the base slot where he
was a depth-1 starter in the MADDEN chart at tick - 1 and is not at tick (the same rule
engines/inbox/kinds/role_talk._starter_slots uses). Chart reads are cached per team per tick.
"""
from __future__ import annotations

from engines import depth_chart_engine as dce
from engines.agent_loops import base, coach

ABSENT_DEPTH = dce.DEPTH + 1          # a promise reviewed against a chart that does not list him: broken
MODES = ("shadow", "active")


# --------------------------------------------------------------------------------------------- db adapters ---
# Small cursor wrappers so the orchestration below stays readable and tests can replace one adapter at a time.
# Every function is read-only and uses the caller's cursor.

def _teams(cur):
    """Every team id in the league, oldest id first."""
    cur.execute("SELECT id FROM teams ORDER BY id")
    return [int(r[0]) for r in cur.fetchall()]


def _roster(cur, team_id, season):
    """[(player_id, name, madden_pgid)] of the team's active players this season (the role_talk rule)."""
    cur.execute("""SELECT ps.player_id, COALESCE(p.full_name, p.player_name), p.madden_pgid
                   FROM player_seasons ps JOIN players p ON p.id = ps.player_id
                   WHERE ps.season = %s AND ps.team_id = %s AND ps.released_week IS NULL
                     AND COALESCE(ps.position, '') <> 'DEF'
                   ORDER BY ps.player_id""", (int(season), int(team_id)))
    rows = []
    for r in cur.fetchall():
        pid, name, pgid = (r[0], r[1], r[2])
        rows.append((int(pid), name or f"player {pid}", None if pgid is None else int(pgid)))
    return rows


def _head_coach(cur, team_id, season):
    """The team's head coach id for the season (pipeline.league_office_personality.LiveFeatures.head_coach)."""
    cur.execute("SELECT to_regclass('coach_tenures') IS NOT NULL")
    row = cur.fetchone()
    if not row or not row[0]:
        return None
    cur.execute("SELECT coach_id FROM coach_tenures WHERE team_id = %s AND start_season <= %s "
                "AND COALESCE(end_season, 9999) >= %s ORDER BY start_season DESC, id DESC LIMIT 1",
                (int(team_id), int(season), int(season)))
    row = cur.fetchone()
    return None if row is None or row[0] is None else int(row[0])


def _coach_view(cur, universe_id, coach_id):
    """The coach's `coaching` feature view, or None when his db-coach alias is unresolved."""
    if coach_id is None:
        return None
    from personality import core, store
    ident = store.resolve_alias(cur, universe_id, "coach", "db-coach", str(int(coach_id)))
    if ident is None:
        return None
    return core.feature_view(store.read(cur, ident), "coaching")


def _pgid_namespace(cur, universe_id):
    """The lifecycle universe's pgid alias namespace (save-pgid:<lifetime>); the default when unreadable."""
    from personality import lifecycle as plc
    registration = None
    try:
        from personality import lifecycle_store
        registration = lifecycle_store.get_universe(cur, universe_id)
    except Exception:  # noqa: BLE001 - the default lifetime is the documented one
        registration = None
    return plc.pgid_namespace(registration or {})


def _player_persona(cur, universe_id, pgid, namespace):
    """The player's PersonRef through his pgid alias, or None when it does not resolve."""
    if pgid is None:
        return None
    return coach.resolve_person(cur, universe_id, {"role": "player", "namespace": namespace,
                                                   "external_id": str(int(pgid))})


def _coach_ref(cur, universe_id, coach_id):
    """The head coach's PersonRef through his db-coach alias, or None when unresolved."""
    if coach_id is None:
        return None
    return coach.resolve_person(cur, universe_id, {"role": "coach", "namespace": "db-coach",
                                                   "external_id": str(int(coach_id))})


def _role_history(cur, universe_id, player_ref, team_id, season, tick):
    """(history [{tick, assessment}], source_event_ids) from the role_window events on that team, oldest first."""
    if player_ref is None:
        return [], []
    from personality import relationship_store as rs
    events = rs.read_events(cur, subject=player_ref, as_of={"season": int(season), "tick": int(tick)})
    team_key = f"team:{int(team_id)}"
    windows = []
    for ev in events:
        payload = ev.get("payload") or {}
        if ev.get("kind") != "role_window" or payload.get("team_key") != team_key:
            continue
        start = payload.get("start") or {}
        if start.get("season") is None or start.get("tick") is None:
            continue
        windows.append((int(start["season"]), int(start["tick"]), ev.get("source_event_id") or "",
                        payload.get("assessment")))
    windows.sort(key=lambda w: (w[0], w[1], w[2]))
    history = [{"tick": w[1], "assessment": w[3]} for w in windows]
    return history, [w[2] for w in windows]


def _madden_chart(cur, team_id, season, tick):
    """The MADDEN depth chart stored for (team, season, tick); {} when there is none."""
    if tick is None or int(tick) < 1:
        return {}
    return dce.load(cur, int(team_id), int(season), int(tick), "MADDEN")


def _injured_ids(cur, season, tick, snapshot):
    """The players.id values injured at this tick: the snapshot's injury/status when given, else the injuries
    table for the season/week when it exists, else an empty set."""
    if snapshot is not None:
        injured_pgids = set()
        for p in snapshot.get("players") or []:
            inj = p.get("injury") or {}
            if p.get("pgid") is None:
                continue
            if inj.get("status") != "Uninjured" or inj.get("injured_reserve"):
                injured_pgids.add(int(p["pgid"]))
        if not injured_pgids:
            return set()
        cur.execute("SELECT id FROM players WHERE madden_pgid = ANY(%s)", (sorted(injured_pgids),))
        return {int(r[0]) for r in cur.fetchall()}
    cur.execute("SELECT to_regclass('injuries') IS NOT NULL")
    row = cur.fetchone()
    if not row or not row[0]:
        return set()
    cur.execute("SELECT player_id FROM injuries WHERE season = %s AND week = %s AND player_id IS NOT NULL "
                "AND (COALESCE(weeks_remaining, 0) > 0 OR is_ir)", (int(season), int(tick)))
    return {int(r[0]) for r in cur.fetchall()}


def _lost_slot(prev_chart, now_chart, player_id):
    """The first base slot where he was a depth-1 starter last tick and is not this tick, or None.

    Mirrors engines/inbox/kinds/role_talk._starter_slots: a starter is within the slot's first
    starter_counts({})[slot] entries (WR 3, CB 2, DT 2, else 1)."""
    if not prev_chart or not now_chart:
        return None
    pid = int(player_id)
    counts = dce.starter_counts({})
    for slot in dce.BASE_SLOTS:
        n = counts.get(slot, 1)
        before = [int(x) for x in (prev_chart.get(slot) or [])[:n] if x is not None]
        now = [int(x) for x in (now_chart.get(slot) or [])[:n] if x is not None]
        if pid in before and pid not in now:
            return slot
    return None


# -------------------------------------------------------------------------------------------------- tick ---

def _open_by_player(store, league_id, season, team_id):
    """{player_id} with a promise made this season and not finally reviewed on that team."""
    return {int(p["player_id"]) for p in coach.open_promises(store, league_id, season, team_id=team_id)
            if p.get("player_id") is not None}


def promise_tick(cur, *, league_id, season, tick, universe_id, mode, user_team_ids, snapshot=None, store=None,
                 llm=None):
    """One promise tick (module docstring). Returns the counts dict; mode 'off' returns {"status": "off"}."""
    if mode not in MODES:
        return {"status": "off"}
    season, tick, league_id = int(season), int(tick), int(league_id)
    st = base.store_for(cur, store)
    result = {"status": mode, "made": 0, "reviewed": 0, "kept": 0, "broken": 0, "unknown": 0, "deferred": 0,
              "errors": 0}
    events = []

    # -- reviews first: a promise made this tick is not re-reviewed until its due tick ----------------------
    charts = {}

    def chart_depth(team_id, player_id, slot):
        team_id = int(team_id)
        if team_id not in charts:
            charts[team_id] = _madden_chart(cur, team_id, season, tick)
        chart = charts[team_id]
        if not chart:
            return None
        ids = chart.get(slot) or []
        pid = int(player_id)
        return ids.index(pid) + 1 if pid in ids else ABSENT_DEPTH

    injured_set = _injured_ids(cur, season, tick, snapshot)

    def injured(player_id):
        return int(player_id) in injured_set

    try:
        reviews = coach.review_promises(cur, season, tick, league_id=league_id, chart_depth=chart_depth,
                                        injured=injured, store=st)
    except Exception:  # noqa: BLE001 - a review error never stops the creation pass
        result["errors"] += 1
        reviews = []
    for review in reviews:
        resolution = review.get("resolution")
        result["reviewed"] += 1
        if resolution in ("kept", "broken", "unknown"):
            result[resolution] += 1
        elif resolution == "defer":
            result["deferred"] += 1
        decision = review.get("decision") or {}
        if mode == "active" and review.get("event") is not None and not decision.get("replayed"):
            events.append(review["event"])

    # -- creation: an unhappy player who just lost a starting job on a CPU team -----------------------------
    # user_team_ids None (the registry cannot say) means JR's teams are unknown: never risk running his coach.
    if user_team_ids is not None:
        mine = {int(t) for t in user_team_ids}
        namespace = _pgid_namespace(cur, universe_id)
        for team_id in _teams(cur):
            if team_id in mine:
                continue
            try:
                hc = _head_coach(cur, team_id, season)
                view = _coach_view(cur, universe_id, hc)
                issuer = _coach_ref(cur, universe_id, hc)
                already = _open_by_player(st, league_id, season, team_id)
                now_chart = _madden_chart(cur, team_id, season, tick)
                prev_chart = _madden_chart(cur, team_id, season, tick - 1)
                roster = _roster(cur, team_id, season)
            except Exception:  # noqa: BLE001 - one team never stops the tick
                result["errors"] += 1
                continue
            for pid, name, pgid in roster:
                try:
                    if pid in already:
                        continue
                    slot = _lost_slot(prev_chart, now_chart, pid)
                    if slot is None:
                        continue
                    player_ref = _player_persona(cur, universe_id, pgid, namespace)
                    history, cause_refs = _role_history(cur, universe_id, player_ref, team_id, season, tick)
                    player = {"player_id": pid, "name": name}
                    options = coach.promise_options(player, history, slot, tick=tick)
                    if not options:
                        continue
                    beneficiary = player_ref
                    out = coach.decide_promise(cur, league_id=league_id, season=season, tick=tick, team_id=team_id,
                                               coach_view=view, player=player, options=options, llm=llm,
                                               seen=False, store=st, universe_id=universe_id, issuer=issuer,
                                               beneficiary=beneficiary, cause_refs=cause_refs)
                    decision = out.get("decision") or {}
                    if decision.get("chosen_key") == "PROMISE" and not decision.get("replayed"):
                        result["made"] += 1
                        if mode == "active" and out.get("event") is not None:
                            events.append(out["event"])
                except Exception:  # noqa: BLE001 - one player never stops the tick
                    result["errors"] += 1

    if mode == "active" and events:
        try:
            from engines.relationship_producers import sources
            coach.append_events(cur, events, team_registry=sources.team_registry(cur))
        except Exception:  # noqa: BLE001 - the decisions are still recorded; the caller logs the count
            result["errors"] += 1
    return result
