"""
PRESS: a notice when JR's postgame press conference is waiting (docs/done/PRESS_CONFERENCE_PLAN.md §2.13; U8).

A notice, not a conversation: the press room (`newsroom/press_room.py`) owns the podium. The scan finds the team's
user-controlled pressers (`press_conferences.controller = 'USER'`) that are SCHEDULED, LIVE or REVIEW, one notice
per conference keyed by `source_ref = "press:<conference_id>"`, and points the thread at the row with
`link_table = 'press_conferences'`. AI-controlled (AGENT) pressers never produce a notice. A notice whose
conference is CLOSED, DECLINED or gone is closed by the next scan. The only option, OPEN_PRESSER, takes JR to the
Game Center; `start` raises 400 (JR takes the podium there, not from the inbox).

The four press tables are guarded with `to_regclass`: an unmigrated database scans to nothing rather than raising.
"""
from engines.inbox import InboxError, store
from engines.inbox.registry import ActResult, Trigger

KIND = "PRESS"
COUNTERPART_TYPE = "system"
ACTOR_ROLES = ("COACH",)                 # the presser is the head coach's; a coach-GM pair controls both
CAN_START = False                        # the press room starts these notices
VOICE = "the team's press office, concise and matter-of-fact"
COUNTERPART_ID = "press"
COUNTERPART_NAME = "Press Room"

OPEN_STATUSES = ("SCHEDULED", "LIVE", "REVIEW")     # presser states that wait on JR
OPEN_PRESSER = {"id": "OPEN_PRESSER", "label": "Go to the podium", "form": None, "needs_note": False,
                "default": None}


def _regclass(cur, table):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
    row = cur.fetchone()
    return bool(row and row[0])


def _close_finished(cur, team_id):
    """Close open PRESS notices whose conference is no longer SCHEDULED, LIVE or REVIEW (or no longer exists)."""
    cur.execute("SELECT id, status FROM press_conferences WHERE team_id = %s", (team_id,))
    status = {int(row[0]): str(row[1]) for row in cur.fetchall()}
    cur.execute("SELECT id, link_id, status FROM inbox_threads WHERE team_id = %s AND kind = %s", (team_id, KIND))
    for thread_id, link_id, thread_status in cur.fetchall():
        if thread_status not in store.OPEN_STATUSES:
            continue
        if status.get(int(link_id) if link_id is not None else None) not in OPEN_STATUSES:
            store.close_thread(cur, thread_id)


def scan(cur, season, week, team):
    """One notice per user-controlled presser waiting for JR; closes notices whose presser has finished."""
    if not _regclass(cur, "press_conferences"):
        return []
    team_id = team["team_id"]
    _close_finished(cur, team_id)
    cur.execute("SELECT id, season, week, subject_name, status FROM press_conferences "
                "WHERE team_id = %s AND controller = 'USER' "
                "AND status IN ('SCHEDULED', 'LIVE', 'REVIEW') ORDER BY season, week, id", (team_id,))
    actor = team.get("actor")
    out = []
    for cid, cseason, cweek, subject_name, status in cur.fetchall():
        subject = f"Week {cweek} postgame press conference"
        facts = {"team": team.get("abbr"), "season": cseason, "week": cweek, "conference_id": int(cid),
                 "status": status, "subject_name": subject_name}
        out.append(Trigger(KIND, team_id, COUNTERPART_TYPE, COUNTERPART_ID, COUNTERPART_NAME, subject,
                           f"press:{cid}", facts, "PRESS_READY", link_table="press_conferences", link_id=int(cid),
                           actor_role=(actor.role if actor is not None else "TEAM"),
                           actor_id=(actor.persona_id if actor is not None else "TEAM")))
    return out


def options(cur, thread, last):
    return [dict(OPEN_PRESSER)]


def act(cur, thread, option_id, payload, note):
    """OPEN_PRESSER only: the notice stays open until the presser itself is CLOSED or DECLINED."""
    if str(option_id or "").strip().upper() != "OPEN_PRESSER":
        raise InboxError(400, "answer the press notice with OPEN_PRESSER")
    conference_id = thread.get("link_id")
    return ActResult(facts={"conference_id": conference_id, "week": thread.get("week")}, intent="OPENED",
                     engine_ref={"press_conference": conference_id}, close=False, judge=False)


def template(intent, facts):
    if intent == "OPENED":
        return "The podium is in the Game Center. Take it when you're ready."
    week = facts.get("week")
    if week is None:
        return "Your postgame press conference is ready. Go to the Game Center to take the podium."
    return f"Your Week {week} postgame press conference is ready. Go to the Game Center to take the podium."


def start(cur, team, body):
    raise InboxError(400, "the press room starts these conversations; take the podium from the Game Center")
