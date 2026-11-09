"""
ROLE_TALK: JR's head coach talks to one of his own players about his role (FRONT_OFFICE_INBOX_PLAN.md §2.2-§2.8,
D11, D16; U3).

The staff chart is computed with `depth_chart_engine.staff_depth_chart`; the chart in force is read from the stored
`depth_charts` rows (newest week up to the scan's week, JR's chart before MU_COACH before MADDEN). The scan opens a
thread when the player lost his starting job (`LOST_STARTING_JOB`: depth 1 at a base slot in the chart in force last
week, not now) or when the staff chart has started him for two straight weeks while JR's chart has not (`WANTS_MORE`).

JR answers with `PROMISE` (form `promise`: slot, the depth he'll reach, the review week >= next week), `EXPLAIN`
(note required) or `NO_CHANGE`. A promise NEVER writes a depth chart (D11): it appends
`{promise_id, slot, max_depth, due_season, due_week, made_at_depth}` to the thread state and records a
`promise_made` relationship event through `engines.inbox.appraisal`. At the due week `review_promises` reads the
actual chart and records `promise_resolved` (kept / broken / unknown, never broken without a chart), exactly once,
with a SYSTEM line in the thread. The real move happens only in the Depth Chart module.

Only a controlled COACH actor is served. Nothing here commits.
"""
from engines import depth_chart_engine as dce
from engines.inbox import InboxError, appraisal, store
from engines.inbox.registry import ActResult, Trigger

KIND = "ROLE_TALK"
COUNTERPART_TYPE = "player"
ACTOR_ROLES = ("COACH",)
START_FORM = "player_pick"          # wave 4: the new-conversation picker's form
VOICE = "a professional football player: direct, competitive, careful not to sound like a problem"
PROMISE_REVIEW_WEEKS = 2
MAX_REVIEW_WEEK = 23                 # the last regular-season tick a depth chart is checked against


# ------------------------------------------------------------------------------------------------- helpers ---

def _regclass(cur, table):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
    row = cur.fetchone()
    return bool(row and row[0])


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _roster(cur, team_id, season):
    """[{player_id, name, position, age, overall, archetype}] of the team's active players this season."""
    if not (_regclass(cur, "player_seasons") and _regclass(cur, "players")):
        return []
    cur.execute("""SELECT ps.player_id, COALESCE(p.full_name, p.player_name), ps.position, ps.age,
                          ps.overall_rating, ps.archetype
                   FROM player_seasons ps JOIN players p ON p.id = ps.player_id
                   WHERE ps.season = %s AND ps.team_id = %s AND ps.released_week IS NULL
                     AND COALESCE(ps.position, '') <> 'DEF'
                   ORDER BY ps.player_id""", (season, team_id))
    rows = cur.fetchall()
    return [{"player_id": r[0], "name": r[1], "position": r[2], "age": r[3], "overall": r[4], "archetype": r[5]}
            for r in rows]


def _player_name(cur, player_id):
    cur.execute("SELECT COALESCE(full_name, player_name) FROM players WHERE id = %s", (_int(player_id),))
    row = cur.fetchone()
    return row[0] if row else str(player_id)


def _staff_chart(cur, team_id, season, week):
    """The coach's own chart for the team right now (`staff_depth_chart`), or {} without a roster."""
    from server import franchise_api as fa
    players = _roster(cur, team_id, season)
    if not players:
        return {}
    ratings = {}
    if _regclass(cur, "player_attributes"):
        ratings = fa.player_ratings(cur, [p["player_id"] for p in players], season)
    scheme = fa.team_scheme(cur, team_id, season)
    injuries = {}
    if _regclass(cur, "injuries"):
        for pid, inj in fa.injury_rows(cur, team_id, season, week).items():
            injuries[pid] = {"out": bool(inj["on_ir"]) or int(inj["weeks_out"] or 0) > 0,
                             "weeks": int(inj["weeks_out"] or 0)}
    roster = [{"player_id": p["player_id"], "position": p["position"], "archetype": p["archetype"],
               "overall": p["overall"], "age": p["age"], "ratings": ratings.get(p["player_id"]) or {},
               "stamina": (ratings.get(p["player_id"]) or {}).get("Stamina")} for p in players]
    return dce.staff_depth_chart(roster, scheme, injuries)


def _chart_in_force(cur, team_id, season, week):
    """{source, season, week, chart}: the newest stored chart up to `week`, JR before MU_COACH before MADDEN at
    that week (the plan's "actual chart"), or None. Never writes one."""
    if week is None or not _regclass(cur, "depth_charts"):
        return None
    cur.execute("SELECT max(week) FROM depth_charts WHERE team_id = %s AND season = %s AND week <= %s "
                "AND source IN ('JR','MU_COACH','MADDEN')", (team_id, season, week))
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    wk = int(row[0])
    for source in ("JR", "MU_COACH", "MADDEN"):
        chart = dce.load(cur, team_id, season, wk, source)
        if chart:
            return {"source": source, "season": season, "week": wk, "chart": chart}
    return None


def _stored_chart_at(cur, team_id, season, week, source):
    """{source, season, week, chart} for the newest chart of one source at or before `week`, or None."""
    if week is None or not _regclass(cur, "depth_charts"):
        return None
    cur.execute("SELECT max(week) FROM depth_charts WHERE team_id = %s AND season = %s AND source = %s "
                "AND week <= %s", (team_id, season, source, week))
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    chart = dce.load(cur, team_id, season, int(row[0]), source)
    return {"source": source, "season": season, "week": int(row[0]), "chart": chart} if chart else None


def _starter_slots(chart, player_id):
    """The base slots where the player is a starter (depth 1) in the chart, [] otherwise."""
    pid, out = _int(player_id), []
    counts = dce.starter_counts({})
    for slot in dce.BASE_SLOTS:
        ids = [_int(x) for x in (chart.get(slot) or []) if x is not None]
        if pid is not None and pid in ids[:counts.get(slot, 1)]:
            out.append(slot)
    return out


def _depth_at(force, slot, player_id):
    """The player's 1-based depth at `slot` in a chart-in-force dict, or None."""
    if not force:
        return None
    ids = [_int(x) for x in ((force.get("chart") or {}).get(slot) or []) if x is not None]
    pid = _int(player_id)
    return ids.index(pid) + 1 if pid is not None and pid in ids else None


def _trigger(team, player, subject, source_ref, facts, intent):
    actor = team.get("actor")
    return Trigger(KIND, team["team_id"], COUNTERPART_TYPE, str(player["player_id"]), player["name"], subject,
                   source_ref, facts, intent, actor_role="COACH",
                   actor_id=(actor.persona_id if actor is not None else None), state=dict(facts, topic=intent))


# ---------------------------------------------------------------------------------------------------- scan ---

def scan(cur, season, week, team):
    """LOST_STARTING_JOB and WANTS_MORE triggers for the team's players."""
    if week is None or not _regclass(cur, "depth_charts"):
        return []
    tid, season, week = team["team_id"], int(season), int(week)
    roster = _roster(cur, tid, season)
    if not roster:
        return []
    out = []
    now = _chart_in_force(cur, tid, season, week)
    prev = _chart_in_force(cur, tid, season, week - 1) if week > 1 else None
    staff_now = _staff_chart(cur, tid, season, week)
    staff_prev = _stored_chart_at(cur, tid, season, week - 1, "MU_COACH") if week > 1 else None
    jr_now = _stored_chart_at(cur, tid, season, week, "JR")
    jr_prev = _stored_chart_at(cur, tid, season, week - 1, "JR") if week > 1 else None
    current = now["chart"] if now else staff_now

    for p in roster:
        base = {"team": team["abbr"], "player": p["name"], "player_id": p["player_id"],
                "position": p["position"], "season": season, "week": week}
        was = _starter_slots(prev["chart"], p["player_id"]) if prev else []
        if was and not _starter_slots(current, p["player_id"]):
            out.append(_trigger(team, p, f"{p['name']}'s role", f"role:lost:{p['player_id']}:{season}:{week}",
                                dict(base, slot=was[0], last_week=week - 1), "LOST_STARTING_JOB"))
            continue
        if not (staff_now and staff_prev and jr_now and jr_prev):
            continue
        staff_slot = _starter_slots(staff_now, p["player_id"])
        if not staff_slot or _starter_slots(jr_now["chart"], p["player_id"]):
            continue
        if not _starter_slots(staff_prev["chart"], p["player_id"]) \
                or _starter_slots(jr_prev["chart"], p["player_id"]):
            continue
        out.append(_trigger(team, p, f"{p['name']} wants more snaps",
                            f"role:wants:{p['player_id']}:{staff_slot[0]}:{season}:{week}",
                            dict(base, slot=staff_slot[0], staff_depth=1,
                                 jr_depth=_depth_at(jr_now, staff_slot[0], p["player_id"])), "WANTS_MORE"))
    return out


# ------------------------------------------------------------------------------------------------- options ---

def options(cur, thread, last):
    """PROMISE (form promise), EXPLAIN (note required), NO_CHANGE."""
    state = thread.get("state") or {}
    week = _int(thread.get("week"), 1)
    due = min(max(week + PROMISE_REVIEW_WEEKS, week + 1), MAX_REVIEW_WEEK)
    return [
        {"id": "PROMISE", "label": "Promise him a bigger role", "form": "promise", "needs_note": False,
         "default": {"slot": state.get("slot"), "max_depth": 1, "due_week": due}},
        {"id": "EXPLAIN", "label": "Explain the decision", "form": None, "needs_note": True, "default": None},
        {"id": "NO_CHANGE", "label": "No change for now", "form": None, "needs_note": False, "default": None},
    ]


# ----------------------------------------------------------------------------------------------------- act ---

def _facts(thread):
    state = thread.get("state") or {}
    return {"team": state.get("team"), "player": state.get("player"), "player_id": state.get("player_id"),
            "position": state.get("position"), "slot": state.get("slot"), "season": thread.get("season"),
            "week": thread.get("week"), "topic": state.get("topic")}


def act(cur, thread, option_id, payload, note):
    """PROMISE appends to state.promises and records promise_made; no chart is ever written (D11)."""
    state = dict(thread.get("state") or {})
    facts = _facts(thread)
    from engines.inbox import scan                     # the week JR answers, not the week the talk opened
    now = scan.league_time(cur)
    this_season, this_week = ((int(now[0]), int(now[1])) if now
                              else (thread.get("season"), _int(thread.get("week"), 1)))
    at = {"season": this_season, "week": this_week, "offseason_stage": thread.get("offseason_stage")}
    if option_id == "PROMISE":
        p = dict(payload or {})
        slot = str(p.get("slot") or state.get("slot") or "").strip().upper()
        if slot not in dce.POSITION_SLOTS:
            raise InboxError(400, f"a role promise needs a depth chart slot, not {slot or 'none'!r}")
        max_depth = _int(p.get("max_depth", p.get("depth")), 1)
        if not 1 <= max_depth <= dce.DEPTH:
            raise InboxError(400, f"the promised depth must be 1-{dce.DEPTH}")
        due_season = _int(p.get("due_season"), this_season)
        if due_season is None or not this_season <= due_season <= this_season + 1:
            raise InboxError(400, "the review season must be this season or the next")
        due_week = _int(p.get("due_week", p.get("week")), this_week + PROMISE_REVIEW_WEEKS)
        if not 1 <= due_week <= MAX_REVIEW_WEEK:
            raise InboxError(400, f"the review week must fall in weeks 1-{MAX_REVIEW_WEEK}")
        if (due_season, due_week) <= (this_season, this_week):   # a next-season week 3 IS after this season's week 20
            raise InboxError(400, "the review week must be after this week")
        promises = list(state.get("promises") or [])
        promise_id = appraisal.promise_id_for(thread, len(promises) + 1)
        made_at = _depth_at(_chart_in_force(cur, thread["team_id"], this_season, this_week), slot,
                            thread.get("counterpart_id"))
        promise = {"promise_id": promise_id, "slot": slot, "max_depth": max_depth, "due_season": due_season,
                   "due_week": due_week, "made_at_depth": made_at}
        promises.append(promise)
        state["promises"] = promises
        event = appraisal.promise_made(thread, None, None,
                                       {"type": "depth_chart_slot", "slot": slot, "max_depth": max_depth},
                                       {"season": due_season, "week": due_week}, promise_id=promise_id, at=at)
        appraisal.record(cur, event, thread_id=thread["id"])
        facts.update(slot=slot, max_depth=max_depth, due_season=due_season, due_week=due_week,
                     promise_id=promise_id, made_at_depth=made_at)
        return ActResult(facts=facts, intent="PROMISE", engine_ref={"promise_id": promise_id}, close=True,
                         state=state)
    if option_id == "EXPLAIN":
        if not note or not note.strip():
            raise InboxError(400, "explaining the decision needs a note")
        facts["explanation"] = note.strip()
        return ActResult(facts=facts, intent="EXPLAIN", engine_ref={"explanation": True}, close=True,
                         state=state)
    if option_id == "NO_CHANGE":
        return ActResult(facts=facts, intent="NO_CHANGE", engine_ref={"role": "no change"}, close=True,
                         state=state)
    raise InboxError(400, "answer the player with PROMISE, EXPLAIN or NO_CHANGE")


# -------------------------------------------------------------------------------------------- the loop ---

def loop_reply(cur, thread, intent, facts, llm, history=(), note=None):
    """The player's own words in this role talk (COACH_PLAYER_AGENT_LOOPS_PLAN §2.5), or None when the player_loop
    feature is off. The thread state's `unhappy_weeks`, when present, is passed as a stance weight."""
    from engines.inbox import voice
    from personality import features as pf
    league_id = voice.loop_league_id(cur, thread)
    if league_id is None or pf.mode(cur, league_id, 'player_loop') == 'off':
        return None
    from engines.agent_loops import player
    loop_facts = dict(facts or {})
    state = thread.get("state") or {}
    if "unhappy_weeks" in state:
        loop_facts["unhappy_weeks"] = state["unhappy_weeks"]
    view = voice.persona_view(cur, "player", thread.get("counterpart_id"))
    res = player.respond_inbox(cur, voice.loop_thread(thread), view, loop_facts, llm=llm, league_id=league_id,
                               message_seq=len(history or ()))
    return (res or {}).get("text") or None


# ------------------------------------------------------------------------------------------------ template ---

def template(intent, facts):
    player = facts.get("player") or "the player"
    slot = facts.get("slot") or "the position"
    if intent == "LOST_STARTING_JOB":
        return (f"Coach, I was starting at {slot} last week and I'm not now. What changed? I want to know where "
                f"I stand.")
    if intent == "WANTS_MORE":
        return (f"Coach, your chart has me starting at {slot} but the depth chart doesn't. I've been waiting two "
                f"weeks. I want those reps.")
    if intent == "PROMISE":
        return (f"I hear you. I'll have you at {slot} depth {facts.get('max_depth')} by week "
                f"{facts.get('due_week')}. Keep working and I'll hold up my end.")
    if intent == "EXPLAIN":
        return f"Here's where I see it: {facts.get('explanation') or 'the reps are going the way they are for a reason.'}"
    if intent == "NO_CHANGE":
        return "I'm not making a change there right now. Keep working and make it impossible to ignore you."
    return f"{player} wants to talk about his role."


# ----------------------------------------------------------------------------------------- promise review ---

def _resolution_line(promise, event):
    resolution = (event.get("payload") or {}).get("resolution")
    if resolution == "kept":
        return (f"The promise to play him at {promise['slot']} depth {promise['max_depth']} or better by week "
                f"{promise['due_week']} was kept.")
    if resolution == "broken":
        return (f"The promise to play him at {promise['slot']} depth {promise['max_depth']} or better by week "
                f"{promise['due_week']} was not kept.")
    return (f"Couldn't check the promise for {promise['slot']} by week {promise['due_week']}: no depth chart.")


def review_promises(cur, season, week, team):
    """Resolve promises due this week against the actual chart, once each. Returns how many were reviewed."""
    if week is None or not _regclass(cur, "depth_charts"):
        return 0
    tid, week = team["team_id"], int(week)
    reviewed = 0
    for thread in store.list_threads(cur, tid, kind=KIND, limit=None):
        state = dict(thread.get("state") or {})
        promises = state.get("promises") or []
        if not promises:
            continue
        changed = False
        for promise in promises:
            if promise.get("resolution"):
                continue
            if _int(promise.get("due_season")) != int(season) or _int(promise.get("due_week"), 99999) > week:
                continue
            # Judge against the chart in force at the due week (in the promise's own season): a promotion made
            # after the deadline isn't a kept promise, and the current chart would call it one.
            force = _chart_in_force(cur, tid, _int(promise.get("due_season"), thread.get("season")),
                                    _int(promise.get("due_week"), week))
            depth = _depth_at(force, promise.get("slot"), thread.get("counterpart_id"))
            chart_row = ({"depth": depth, "source": force["source"], "season": force["season"],
                          "week": force["week"]} if force is not None and depth is not None else None)
            event = appraisal.promise_resolved(thread, promise, chart_row, at={"season": season, "week": week})
            appraisal.record(cur, event, thread_id=thread["id"])
            promise["resolution"] = (event.get("payload") or {}).get("resolution")
            promise["resolved_season"], promise["resolved_week"] = int(season), week
            store.add_message(cur, thread["id"], "SYSTEM", _resolution_line(promise, event), "ENGINE",
                              at={"season": season, "week": week})
            reviewed += 1
            changed = True
        if changed:
            store.update_state(cur, thread["id"], state)
            store.close_thread(cur, thread["id"])
    return reviewed


# --------------------------------------------------------------------------------------------------- start ---

def _active_roster_player(cur, team_id, player_id, season):
    """The picker's rule (`server/inbox_api._player_targets`): an ACTIVE contract and not on IR, so a typed start
    and the picker offer the same players. A database without those tables can't say otherwise and allows it."""
    from engines.inbox import scan
    week = (scan.league_time(cur) or (None, None))[1]
    if _regclass(cur, "player_contracts"):
        cur.execute("SELECT count(*) FROM player_contracts WHERE player_id = %s AND season = %s "
                    "AND status = 'ACTIVE'", (player_id, season))
        row = cur.fetchone()
        if not (row and row[0]):
            return False
    if _regclass(cur, "injuries") and week is not None:
        cur.execute("""SELECT count(*) FROM injuries i
                       WHERE i.team_id = %s AND i.season = %s AND i.player_id = %s AND i.is_ir
                         AND i.week = (SELECT max(i2.week) FROM injuries i2
                                       WHERE i2.team_id = i.team_id AND i2.season = i.season AND i2.week <= %s)""",
                    (team_id, season, player_id, week))
        row = cur.fetchone()
        if row and row[0]:
            return False
    return True


def start(cur, team, body):
    """JR (as his coach) opens a role talk about a named player."""
    player_id = _int(body.get("player_id", body.get("player")))
    if player_id is None:
        raise InboxError(400, "choose the player to talk to")
    roster = {p["player_id"]: p for p in _roster(cur, team["team_id"], team.get("season"))}
    player = roster.get(player_id)
    if player is None:
        raise InboxError(404, f"player {player_id} isn't on {team['abbr']}'s roster")
    if not _active_roster_player(cur, team["team_id"], player_id, team.get("season")):
        raise InboxError(409, f"{player['name']} isn't on the active roster")
    topic = str(body.get("topic") or body.get("note") or "His role").strip()[:120]
    facts = {"team": team["abbr"], "player": player["name"], "player_id": player_id,
             "position": player["position"], "season": team.get("season"), "week": team.get("week"),
             "topic": topic, "slot": body.get("slot")}
    return _trigger(team, player, topic,
                    f"role:jr:{player_id}:{team.get('season')}:{team.get('week')}", facts, "JR_STARTED")
