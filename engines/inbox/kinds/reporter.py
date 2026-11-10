"""
REPORTER: any controlled persona sits down with the team's beat writer (FRONT_OFFICE_INBOX_PLAN.md §2.8, D4, D12;
U3).

The interviewer is the team's `LOCAL_BEAT_REPORTER` account in `agent_memories` (assigned_team_id = the team). The
scan opens an `INTERVIEW_REQUEST` at most once per source: a starter out 4+ weeks (`reporter:injury:...`), an
executed trade or signing (`reporter:trade:...` / `reporter:signing:...`), a 3-game streak
(`reporter:streak:...`), or a starter losing his job (`reporter:lost:...`). The store caps REPORTER at 2 open
threads per team.

The interview is open-ended (D12). JR answers each question `ON_RECORD` or `OFF_RECORD` (both need a note) or
`NO_COMMENT`, and may `END_INTERVIEW` at any time. After each answer the writer asks a follow-up from the facts or
wraps up: the template asks up to `TEMPLATE_QUESTIONS` = 3 then wraps, a model reply wraps when its JSON says
`"done": true` (recorded by `on_reply`), and `INTERVIEW_MAX_QUESTIONS` = 10 is a hard cap. Answers are stored for
later articles through `store.jr_statements`: one row per answered question, NO_COMMENT excluded.

`on_reply` is the hook U1 exposes for extra JSON keys; `voice.exchange` calls it after every counterpart reply.
Nothing here commits.
"""
from engines.inbox import InboxError, appraisal, store
from engines.inbox.kinds import role_talk
from engines.inbox.registry import ActResult, Trigger
from engines.inbox.helpers import regclass as _regclass
from engines.inbox.helpers import as_int as _int

KIND = "REPORTER"
COUNTERPART_TYPE = "writer"
ACTOR_ROLES = ("GM", "COACH", "PLAYER")
VOICE = "a local beat writer: curious, persistent, friendly but always working an angle"
EXTRA_JSON = '"done": true|false'
TEMPLATE_QUESTIONS = 3
INTERVIEW_MAX_QUESTIONS = 10
STREAK = 3
INJURY_WEEKS_OUT = 4
REGULAR_WEEKS = 18

QUESTION_OPTIONS = [
    {"id": "ON_RECORD", "label": "Answer on the record", "form": None, "needs_note": True, "default": None},
    {"id": "OFF_RECORD", "label": "Answer off the record", "form": None, "needs_note": True, "default": None},
    {"id": "NO_COMMENT", "label": "No comment", "form": None, "needs_note": False, "default": None},
    {"id": "END_INTERVIEW", "label": "End the interview", "form": None, "needs_note": False, "default": None},
]
END_OPTION = {"id": "END_INTERVIEW", "label": "End the interview", "form": None, "needs_note": False,
              "default": None}
MOVE_INTERVIEW_WEEKS = 2          # a move interview is offered for the scan week and the one before
FOLLOW_UPS = [
    "What does that mean for the locker room?",
    "How do you see his role changing from here?",
    "What do you need to see from the team this week?",
]


# ------------------------------------------------------------------------------------------------- helpers ---

def _beat_writer(cur, team_id):
    """The team's beat writer {agent_id, handle, name}, falling back to any assigned account, or None."""
    try:
        with appraisal._savepoint(cur):
            cur.execute("SELECT agent_id, handle, display_name FROM agent_memories "
                        "WHERE assigned_team_id = %s AND archetype = 'LOCAL_BEAT_REPORTER' ORDER BY id LIMIT 1",
                        (team_id,))
            row = cur.fetchone()
        if row:
            return {"agent_id": row[0], "handle": row[1], "name": row[2] or row[0]}
    except Exception:
        pass
    try:
        with appraisal._savepoint(cur):
            cur.execute("SELECT agent_id, handle, display_name FROM agent_memories "
                        "WHERE assigned_team_id = %s ORDER BY id LIMIT 1", (team_id,))
            row = cur.fetchone()
    except Exception:
        return None
    return {"agent_id": row[0], "handle": row[1], "name": row[2] or row[0]} if row else None


def _trigger(team, writer, subject, source_ref, facts, intent):
    actor = team.get("actor")
    return Trigger(KIND, team["team_id"], COUNTERPART_TYPE, str(writer["agent_id"]), writer["name"], subject,
                   source_ref, dict(facts, writer=writer["name"], handle=writer.get("handle"), team=team["abbr"]),
                   intent, actor_role=(actor.role if actor is not None else None),
                   actor_id=(actor.persona_id if actor is not None else None),
                   state={"questions_asked": 1, "topic": facts.get("topic"), "topic_ref": source_ref})


def _chart(cur, team_id, season, week):
    """The chart in force, or the staff chart when nothing is stored yet."""
    force = role_talk._chart_in_force(cur, team_id, season, week)
    if force:
        return force["chart"]
    return role_talk._staff_chart(cur, team_id, season, week) if _regclass(cur, "depth_charts") else {}


# ----------------------------------------------------------------------------------------------- triggers ---

def _injury(cur, team, writer, season, week):
    if not _regclass(cur, "injuries"):
        return []
    chart = _chart(cur, team["team_id"], season, week)
    if not chart:
        return []
    cur.execute("""SELECT player_id, injury_type, weeks_remaining, mu_weeks_out FROM injuries
                   WHERE team_id = %s AND season = %s AND player_id IS NOT NULL
                     AND week = (SELECT max(week) FROM injuries WHERE team_id = %s AND season = %s AND week <= %s)""",
                (team["team_id"], season, team["team_id"], season, week))
    out = []
    for pid, injury, remaining, mu_weeks in cur.fetchall():
        weeks = int(mu_weeks if mu_weeks is not None else (remaining or 0))
        slots = role_talk._starter_slots(chart, pid)
        if weeks < INJURY_WEEKS_OUT or not slots:
            continue
        name = role_talk._player_name(cur, pid)
        out.append(_trigger(team, writer, f"{name} out {weeks} weeks",
                            f"reporter:injury:{pid}:{season}:{week}",
                            {"topic": "INJURY", "player": name, "player_id": pid, "slot": slots[0],
                             "injury": injury, "weeks_out": weeks, "season": season, "week": week}, "INJURY"))
    return out


def _executed_moves(cur, team, writer, season, week):
    """The team's completed trades and signings recent enough to interview JR about (P5's window): the week being
    scanned and the one before it. A wider window kept asking about week-2 moves in week 18; a one-week window would
    miss a move written after that week's last scan, since the scan runs on page open. A trade is dated by its
    league-office run's week, a signing by `contract_offers.week` (`2027_02_18`); a NULL week is out of window."""
    out = []
    lo = week - (MOVE_INTERVIEW_WEEKS - 1)
    if _regclass(cur, "franchise_pending_moves") and _regclass(cur, "league_office_runs"):
        cur.execute("""SELECT m.id FROM franchise_pending_moves m JOIN league_office_runs r ON r.id = m.written_run
                       WHERE m.team_id = %s AND m.kind = 'TRADE' AND m.status = 'WRITTEN' AND r.season = %s
                         AND r.week BETWEEN %s AND %s
                       ORDER BY m.id""", (team["team_id"], season, lo, week))
        out += [_trigger(team, writer, "The trade you made", f"reporter:trade:{r[0]}",
                         {"topic": "MOVE", "move": "trade", "season": season, "week": week}, "MOVE")
                for r in cur.fetchall()]
    if _regclass(cur, "contract_offers"):
        cur.execute("""SELECT o.id, p.player_name FROM contract_offers o LEFT JOIN players p ON p.id = o.player_id
                       WHERE o.team_id = %s AND o.season = %s AND o.from_user AND o.status = 'ACCEPTED'
                         AND o.week BETWEEN %s AND %s
                       ORDER BY o.id""", (team["team_id"], season, lo, week))
        for oid, name in cur.fetchall():
            subject = f"The signing of {name}" if name else "The signing you made"
            out.append(_trigger(team, writer, subject, f"reporter:signing:{oid}",
                                {"topic": "MOVE", "move": "free-agent signing", "player": name,
                                 "season": season, "week": week}, "MOVE"))
    return out


def _results(cur, team_id, season):
    """[(week, won)] of the team's completed regular-season games, in week order."""
    if not _regclass(cur, "games"):
        return []
    cur.execute("""SELECT week, home_team_id, home_score, away_score FROM games
                   WHERE season = %s AND game_type = 'REGULAR_SEASON' AND status = 'COMPLETED'
                     AND %s IN (home_team_id, away_team_id) ORDER BY week""", (season, team_id))
    out = []
    for week, home, hs, as_ in cur.fetchall():
        if hs is None or as_ is None or hs == as_:
            continue
        out.append((week, (hs > as_) == (home == team_id)))
    return out


def _streak(results, n=STREAK):
    """('wins'|'losses', week the current run started) when the last n games share a result. The week is the
    start of the whole run, not of the last n games: otherwise a longer streak keys a new source_ref every week
    and re-opens the same interview."""
    if len(results) < n:
        return None
    won = results[-1][1]
    run, start = 0, None
    for week, w in reversed(results):
        if w != won:
            break
        run, start = run + 1, week
    if run < n:
        return None
    return ("wins" if won else "losses"), start


def _streak_trigger(cur, team, writer, season, week):
    got = _streak(_results(cur, team["team_id"], season))
    if got is None:
        return []
    result, first = got
    return [_trigger(team, writer, f"Three straight {result}", f"reporter:streak:{season}:{first}:{result}",
                     {"topic": "STREAK", "streak": STREAK, "result": result, "season": season, "week": week},
                     "STREAK")]


def _lost_job(cur, team, writer, season, week):
    if week <= 1 or not _regclass(cur, "depth_charts"):
        return []
    now = role_talk._chart_in_force(cur, team["team_id"], season, week)
    prev = role_talk._chart_in_force(cur, team["team_id"], season, week - 1)
    if not (now and prev):
        return []
    out = []
    for p in role_talk._roster(cur, team["team_id"], season):
        was = role_talk._starter_slots(prev["chart"], p["player_id"])
        if was and not role_talk._starter_slots(now["chart"], p["player_id"]):
            out.append(_trigger(team, writer, f"{p['name']} loses his job",
                                f"reporter:lost:{p['player_id']}:{season}:{week}",
                                {"topic": "ROLE", "player": p["name"], "player_id": p["player_id"],
                                 "slot": was[0], "season": season, "week": week}, "ROLE"))
    return out


# ---------------------------------------------------------------------------------------------------- scan ---

def scan(cur, season, week, team):
    """INTERVIEW_REQUEST triggers for the team's beat writer; the store caps open REPORTER threads at 2."""
    writer = _beat_writer(cur, team["team_id"])
    if writer is None:
        return []
    season, week = int(season), int(week)
    out = []
    out += _injury(cur, team, writer, season, week)
    out += _executed_moves(cur, team, writer, season, week)
    out += _streak_trigger(cur, team, writer, season, week)
    out += _lost_job(cur, team, writer, season, week)
    return out


# ------------------------------------------------------------------------------------------------- options ---

def _last_counterpart(cur, thread):
    return next((m for m in reversed(store.messages(cur, thread["id"])) if m["author"] == "COUNTERPART"), None)


def _limit(cur, thread):
    """The template asks up to 3; a model-written interview may run to the hard cap."""
    last = _last_counterpart(cur, thread)
    return INTERVIEW_MAX_QUESTIONS if last is not None and last.get("body_source") == "LLM" \
        else TEMPLATE_QUESTIONS


def options(cur, thread, last):
    state = thread.get("state") or {}
    if thread.get("status") not in store.OPEN_STATUSES or state.get("done"):
        return []
    asked = int(state.get("questions_asked") or 0)
    return [END_OPTION] if asked >= _limit(cur, thread) else list(QUESTION_OPTIONS)


# ----------------------------------------------------------------------------------------------------- act ---

def _facts(thread, state, **extra):
    facts = {"writer": thread.get("counterpart_name"), "topic": state.get("topic"),
             "season": thread.get("season"), "week": thread.get("week")}
    facts.update(extra)
    return facts


def act(cur, thread, option_id, payload, note):
    """ON_RECORD / OFF_RECORD (note required), NO_COMMENT, END_INTERVIEW; FOLLOWUP or WRAP by the count."""
    state = dict(thread.get("state") or {})
    at = {"season": thread["season"], "week": thread["week"], "offseason_stage": thread.get("offseason_stage")}
    if option_id == "END_INTERVIEW":
        state["ended"] = True
        facts = _facts(thread, state, option="END_INTERVIEW")
        return ActResult(facts=facts, intent="END", engine_ref={"interview": "END"}, close=True, state=state)
    if option_id not in ("ON_RECORD", "OFF_RECORD", "NO_COMMENT"):
        raise InboxError(400, "answer with ON_RECORD, OFF_RECORD, NO_COMMENT or END_INTERVIEW")
    if option_id in ("ON_RECORD", "OFF_RECORD") and not (note and note.strip()):
        raise InboxError(400, f"an answer {option_id.replace('_', ' ').lower()} needs a note")
    asked = int(state.get("questions_asked") or 0)
    wrap = asked >= _limit(cur, thread)
    if wrap:
        state["done"] = True
    else:
        state["questions_asked"] = asked + 1
    facts = _facts(thread, state, option=option_id, asked=asked, limit=_limit(cur, thread),
                   question=(_last_counterpart(cur, thread) or {}).get("body"))
    return ActResult(facts=facts, intent="WRAP" if wrap else "FOLLOWUP",
                     engine_ref={"interview": option_id, "questions_asked": int(state.get("questions_asked") or 0)},
                     close=wrap, state=state)


def on_reply(cur, thread, data):
    """The model's JSON may say the interview is over; record it and close the thread."""
    if not isinstance(data, dict) or not data.get("done"):
        return
    state = dict(thread.get("state") or {})
    state["done"] = True
    store.update_state(cur, thread["id"], state)
    store.close_thread(cur, thread["id"])


# ------------------------------------------------------------------------------------------------ template ---

def template(intent, facts):
    player = facts.get("player")
    if intent == "INJURY":
        return (f"{player} is going to miss {facts.get('weeks_out')} weeks with the {facts.get('injury')}. "
                f"How do you plan to cover for him?")
    if intent == "MOVE":
        if facts.get("player"):
            return f"You just made the move for {player}. What does he give you?"
        return "You made a move today. What was the thinking?"
    if intent == "STREAK":
        return f"Three straight {facts.get('result')}. What's different about this team right now?"
    if intent == "ROLE":
        return f"You moved {player} out of the starting job at {facts.get('slot')}. How did you explain it to him?"
    if intent == "OPEN":
        return f"{facts.get('topic') or 'The team'}. Where does that stand right now?"
    if intent == "FOLLOWUP":
        asked = max(0, int(facts.get("asked") or 0) - 1)
        return FOLLOW_UPS[asked % len(FOLLOW_UPS)]
    if intent == "WRAP":
        return "That's all I need. Thanks for your time."
    if intent == "END":
        return "Understood. Thanks for your time."
    return "Do you have a minute?"


# ---------------------------------------------------------------------------------------------- statements ---

def jr_statements(cur, team_id, season, week=None):
    """One row per answered interview question (NO_COMMENT excluded), for the writers (store.jr_statements)."""
    return store.jr_statements(cur, team_id, season, week)


# --------------------------------------------------------------------------------------------------- start ---

def start(cur, team, body):
    """JR (as any controlled persona) opens an interview with his beat writer."""
    writer = _beat_writer(cur, team["team_id"])
    if writer is None:
        raise InboxError(400, f"{team['abbr']} has no beat writer to interview with")
    topic = str(body.get("topic") or body.get("note") or "The team").strip()[:120]
    facts = {"topic": topic, "season": team.get("season"), "week": team.get("week"), "opening": "JR"}
    actor = team.get("actor")
    return Trigger(KIND, team["team_id"], COUNTERPART_TYPE, str(writer["agent_id"]), writer["name"], topic,
                   f"jr:reporter:{team.get('season')}:{team.get('week')}:{topic.lower()[:60]}",
                   dict(facts, writer=writer["name"], handle=writer.get("handle"), team=team["abbr"]), "OPEN",
                   actor_role=(actor.role if actor is not None else None),
                   actor_id=(actor.persona_id if actor is not None else None),
                   state={"questions_asked": 1, "topic": "OPEN"})
