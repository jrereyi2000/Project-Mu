"""
OWNER: the team owner writes to JR's GM (docs/in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md §2.3, §8 Q1; U5).

On JR's teams the owner only talks: he never vetoes JR's moves or fires his personas. Behind the switchboard's
`owner_meddling` feature (engines/owner_context.feature_mode): only when it is **active** does the scan send
anything (off and shadow send nothing, so the inbox looks exactly as before). Three triggers, each once per source:

  EXPECTATION   week 1 of the season: what he expects, from his traits and the team's direction
                (source owner:expect:<season>)
  PRESSURE      three straight regular-season losses (source owner:pressure:<season>:streak:<week of the first
                loss>) or, once the regular season is complete, a losing season (owner:pressure:<season>:losing)
  REACTION      a big move by JR this season: a written trade (franchise_pending_moves TRADE, WRITTEN in a league
                office run of this season) or an accepted free-agent offer of at least BIG_SIGNING_VALUE
                (contract_offers from JR); source owner:move:trade:<id> / owner:move:signing:<id>

JR answers with ACKNOWLEDGE, REASSURE or PUSH_BACK; the exchange closes the thread and is recorded as a
relationships `meaningful_interaction` (outcome positive / positive / mixed) in the inbox outbox, with the owner as
subject and JR's GM as target. The engine sets the appraisal, so the model is not asked for one (judge False).
The owner's identity alias is `db-owner` with counterpart_id `owner:<TEAM>`; the events wait in the outbox until
the owners' identities exist in the league (U6's team_owners and the lifecycle batch).
"""
from engines.inbox import InboxError, appraisal
from engines.inbox.registry import ActResult, Trigger

KIND = "OWNER"
COUNTERPART_TYPE = "owner"
ACTOR_ROLES = ("GM",)
CAN_START = False
FEATURE = "owner_meddling"
VOICE = "a team owner: direct, protective of the franchise and its fans, used to being listened to"
STREAK = 3                              # straight losses before a pressure note
BIG_SIGNING_VALUE = 60_000_000          # an accepted free-agent deal this large is a big move (estimate)
REGULAR_WEEKS = 18

EXPECTATIONS = {        # (trait, expectation) in priority order; the first the owner holds is used
    "HEADLINE_HUNTER": "win now and make noise doing it",
    "PATIENT_BUILDER": "build it the right way; steady progress matters more than one season",
    "MEDDLER": "be kept informed on every big decision",
    "DEEP_POCKETS": "spend what it takes to compete",
    "BUDGET_CONSCIOUS": "get value for every dollar",
    "HANDS_OFF": "run football operations; he trusts the people he hired",
}
DIRECTION_LINE = {"CONTEND": "contend for a title", "RETOOL": "retool and stay competitive",
                  "REBUILD": "rebuild for the long run"}
REPLIES = {"ACKNOWLEDGE": ("ACKNOWLEDGED", "positive", "routine"),
           "REASSURE": ("REASSURED", "positive", "meaningful"),
           "PUSH_BACK": ("PUSHED_BACK", "mixed", "meaningful")}


def _owner(team_abbr):
    """The reviewed owner profile's {key, team, name, traits, sliders}, or None (no inbox owner without one)."""
    from engines import owner_context as oc
    return oc.owner_views().get(str(team_abbr).upper())


def _expectation(view, direction):
    traits = list((view or {}).get("traits") or [])
    for t, line in EXPECTATIONS.items():
        if t in traits:
            return line
    return DIRECTION_LINE.get(direction or "", "compete")


def _record(cur, team_id, season):
    cur.execute("SELECT wins, losses FROM team_seasons WHERE team_id = %s AND season = %s", (team_id, season))
    row = cur.fetchone()
    return (int(row[0] or 0), int(row[1] or 0)) if row else (0, 0)


def _direction(cur, team_id, season):
    cur.execute("SELECT direction FROM gm_team_seasons WHERE team_id = %s AND season = %s", (team_id, season))
    row = cur.fetchone()
    return row[0] if row else None


def _results(cur, team_id, season):
    """[(week, won)] of the team's completed regular-season games, in week order."""
    cur.execute("""SELECT week, home_team_id, home_score, away_score FROM games
                   WHERE season = %s AND game_type = 'REGULAR_SEASON' AND status = 'COMPLETED'
                     AND %s IN (home_team_id, away_team_id) ORDER BY week""", (season, team_id))
    out = []
    for week, home, hs, as_ in cur.fetchall():
        if hs is None or as_ is None or hs == as_:
            out.append((week, None))
            continue
        out.append((week, (hs > as_) == (home == team_id)))
    return out


def losing_streak_start(results, streak=STREAK):
    """Pure. The week of the first loss of the current run of `streak`+ straight losses, or None."""
    run = []
    for week, won in results:
        run = run + [week] if won is False else []
    return run[0] if len(run) >= streak else None


def _season_complete(cur, season):
    cur.execute("""SELECT count(*), count(*) FILTER (WHERE status <> 'COMPLETED') FROM games
                   WHERE season = %s AND game_type = 'REGULAR_SEASON'""", (season,))
    total, open_ = cur.fetchone()
    return bool(total) and not open_


def _regclass(cur, table):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
    row = cur.fetchone()
    return bool(row and row[0])


def _big_moves(cur, team_id, season):
    out = []
    if _regclass(cur, "franchise_pending_moves") and _regclass(cur, "league_office_runs"):
        cur.execute("""SELECT m.id FROM franchise_pending_moves m JOIN league_office_runs r ON r.id = m.written_run
                       WHERE m.team_id = %s AND m.kind = 'TRADE' AND m.status = 'WRITTEN' AND r.season = %s
                       ORDER BY m.id""", (team_id, season))
        out += [("trade", r[0], {"move": "trade"}) for r in cur.fetchall()]
    if _regclass(cur, "contract_offers"):
        cur.execute("""SELECT o.id, p.player_name FROM contract_offers o LEFT JOIN players p ON p.id = o.player_id
                       WHERE o.team_id = %s AND o.season = %s AND o.from_user AND o.status = 'ACCEPTED'
                         AND o.total_value >= %s ORDER BY o.id""", (team_id, season, BIG_SIGNING_VALUE))
        out += [("signing", r[0], {"move": "free-agent signing", "player": r[1]}) for r in cur.fetchall()]
    return out


def _trigger(team, view, subject, source_ref, facts, intent, season):
    return Trigger(KIND, team["team_id"], COUNTERPART_TYPE, view["key"] or f"owner:{team['abbr']}", view["name"],
                   subject, source_ref, facts, intent, expires_season=season, actor_role="GM")


def scan(cur, season, week, team):
    from engines import owner_context as oc
    if oc.feature_mode(cur, FEATURE) != "active":
        return []
    view = _owner(team["abbr"])
    if view is None:
        return []
    tid = team["team_id"]
    base = {"owner": view["name"], "team": team["abbr"], "owner_traits": list(view.get("traits") or [])}
    out = []
    if week == 1:
        direction = _direction(cur, tid, season)
        out.append(_trigger(team, view, f"{season} expectations", f"owner:expect:{season}",
                            dict(base, direction=direction, expectation=_expectation(view, direction)),
                            "EXPECTATION", season))
    if isinstance(week, int) and 1 <= week <= REGULAR_WEEKS + 1:
        start = losing_streak_start(_results(cur, tid, season))
        if start is not None:
            out.append(_trigger(team, view, "This losing streak", f"owner:pressure:{season}:streak:{start}",
                                dict(base, straight_losses=STREAK), "PRESSURE", season))
    if _season_complete(cur, season):
        wins, losses = _record(cur, tid, season)
        if wins < losses:
            out.append(_trigger(team, view, f"The {season} season", f"owner:pressure:{season}:losing",
                                dict(base, wins=wins, losses=losses), "PRESSURE", season))
    for kind, ref, facts in _big_moves(cur, tid, season):
        out.append(_trigger(team, view, f"Your {facts['move']}", f"owner:move:{kind}:{ref}",
                            dict(base, **{k: v for k, v in facts.items() if v is not None}), "REACTION", season))
    return out


def options(cur, thread, last):
    return [{"id": "ACKNOWLEDGE", "label": "Understood"},
            {"id": "REASSURE", "label": "Reassure him"},
            {"id": "PUSH_BACK", "label": "Push back"}]


def act(cur, thread, option_id, payload, note):
    if option_id not in REPLIES:
        raise InboxError(400, f"answer the owner with one of {', '.join(REPLIES)}")
    intent, outcome, significance = REPLIES[option_id]
    facts = {"owner": thread.get("counterpart_name"), "reply": option_id}
    at = {"season": thread["season"], "week": thread["week"], "offseason_stage": thread.get("offseason_stage")}
    ap = {"outcome": outcome, "significance": significance, "mistreated": False,
          "reason": f"JR's GM answered the owner: {option_id.lower().replace('_', ' ')}"}
    msg = {"id": f"owner:{thread['id']}:{option_id}"}
    event = appraisal.interaction_event(thread, msg, None, None, ap, at)
    appraisal.record(cur, event, thread_id=thread["id"])
    return ActResult(facts=facts, intent=intent, engine_ref={"owner_reply": option_id}, close=True, judge=False)


def template(intent, facts):
    owner = facts.get("owner") or "The owner"
    team = facts.get("team") or "the team"
    if intent == "EXPECTATION":
        return f"{owner} here. A new season for {team}. What I expect: {facts.get('expectation')}."
    if intent == "PRESSURE":
        if facts.get("wins") is not None:
            return f"{owner} here. A losing season isn't acceptable for {team}. I want to hear your plan."
        return f"{owner} here. Three straight losses. I want to know what you're going to do about it."
    if intent == "REACTION":
        what = facts.get("move") or "move"
        who = f" for {facts['player']}" if facts.get("player") else ""
        return f"{owner} here. I saw the {what}{who}. I hope you're right about it."
    if intent == "ACKNOWLEDGED":
        return "Good. Keep me posted."
    if intent == "REASSURED":
        return "That's what I wanted to hear. Don't let me down."
    if intent == "PUSHED_BACK":
        return "I hear you. It's your call on football, but it's my team. Results will settle it."
    return f"{owner} wants a word."


def start(cur, team, body):
    raise InboxError(400, "the owner starts these conversations")
