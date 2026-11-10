"""SCOUT: the Mu Scouting Department writes to JR's GM (docs/done/SCOUTING_REVAMP_PLAN.md §2.6, §2.8; V2.10 U5).

A neutral inbox kind that tells JR when the combine and top-30 visit windows open and when a new report edition
lands, and answers a shortlist request. The counterpart is the department (COUNTERPART_TYPE "scout", counterpart id
"department", name "Mu Scouting Department"); PERSONA_ROLE "scout" maps to the neutral view until personality phase 6
gives scouts names and lenses. The scout writes first (`start` is refused). Each trigger fires once per step via its
source_ref (scout:<draft_season>:<trigger>:<step>). The scan reads only scouting tables, so it works in the offseason
when the inbox scans on page open.
"""
from engines import scouting_interviews as si
from engines import scouting_narrative as sn
from engines.inbox import InboxError
from engines.inbox.registry import ActResult, Trigger
from engines.inbox.helpers import regclass as _regclass

KIND = "SCOUT"
COUNTERPART_TYPE = "scout"
PERSONA_ROLE = "scout"
ACTOR_ROLES = ("GM",)
CAN_START = False
VOICE = "a neutral national scouting service"
COUNTERPART_ID = "department"
COUNTERPART_NAME = "Mu Scouting Department"

SHORTLIST_MAX = 8
RISER_MAX = 5


def scan(cur, season, week, team):
    """COMBINE_OPEN / VISITS_OPEN while the window is open, REPORTS_READY once per stored edition."""
    if not _regclass(cur, "scout_runs"):
        return []
    draft_season = int(season) + 1
    tid = team["team_id"]
    w = si.window(cur, draft_season)
    out = []
    if w["combine_open"] or w["visit_open"]:
        b = si.budget(cur, draft_season, tid)
        if w["combine_open"]:
            out.append(_notice(cur, team, draft_season, "COMBINE_OPEN", w, b))
        if w["visit_open"]:
            out.append(_notice(cur, team, draft_season, "VISITS_OPEN", w, b))
    if _regclass(cur, "scout_reports"):
        for edition, step in _editions(cur, draft_season):
            out.append(_report_notice(cur, team, draft_season, edition, step))
    return out


def _notice(cur, team, draft_season, intent, window, budget):
    facts = {"draft_season": draft_season, "team": team["abbr"], "window": window, "budget": budget}
    subject = "Combine interviews are open" if intent == "COMBINE_OPEN" else "Top-30 visits are open"
    return Trigger(KIND, team["team_id"], COUNTERPART_TYPE, COUNTERPART_ID, COUNTERPART_NAME, subject,
                   f"scout:{draft_season}:{intent}:{window['latest_step']}", facts, intent, actor_role="GM")


def _editions(cur, draft_season):
    cur.execute("SELECT DISTINCT edition, step FROM scout_reports WHERE draft_season = %s ORDER BY step",
                (draft_season,))
    return [(r[0], r[1]) for r in cur.fetchall()]


def _risers(cur, draft_season, step, team_id, limit=RISER_MAX):
    """The biggest consensus risers at `step` that sit on JR's team board, names and ranks only.

    **Needs proxy, accepted in writing (P1-14, 2026-11-09).** Membership in JR's stored team board stands in for
    "among JR's needs": the board is built with the GM's needs-weighted `fit` (scouting_boards.fit_bias), so a
    prospect there is one his front office has already weighed against what the roster lacks. The stricter
    alternative — filtering on the positions in `gm_team_seasons.needs` — would make the scouting inbox read a
    non-scouting table and couple it to the season-scan schema for a five-name courtesy line. Revisit only if the
    line reads wrong on a live board (then the fix is a positional filter, not a different message)."""
    if not (_regclass(cur, "scout_team_boards") and _regclass(cur, "scout_consensus")):
        return []
    cur.execute("""
        SELECT d.full_name, c.rank
        FROM scout_consensus c
        JOIN scout_team_boards b ON b.draft_season = c.draft_season AND b.step = c.step
            AND b.prospect_key = c.prospect_key AND b.team_id = %s
        JOIN scout_prospects sp ON sp.draft_season = c.draft_season AND sp.prospect_key = c.prospect_key
        LEFT JOIN draft_prospects d ON d.id = sp.prospect_id
        WHERE c.draft_season = %s AND c.step = %s AND c.delta_rank IS NOT NULL AND c.delta_rank > 0
        ORDER BY c.delta_rank DESC, c.rank LIMIT %s
    """, (team_id, draft_season, step, limit))
    return [{"name": r[0], "rank": r[1]} for r in cur.fetchall()]


def _report_notice(cur, team, draft_season, edition, step):
    facts = {"draft_season": draft_season, "team": team["abbr"], "edition": edition,
             "risers": _risers(cur, draft_season, step, team["team_id"])}
    return Trigger(KIND, team["team_id"], COUNTERPART_TYPE, COUNTERPART_ID, COUNTERPART_NAME,
                   f"{edition} scouting reports are ready", f"scout:{draft_season}:REPORTS_READY:{step}",
                   facts, "REPORTS_READY", actor_role="GM")


def options(cur, thread, last):
    return [{"id": "SHORTLIST", "label": "Who's on my board?", "form": None, "needs_note": False},
            {"id": "ACKNOWLEDGE", "label": "Thanks, noted", "form": None, "needs_note": False}]


def _shortlist(cur, thread):
    """Up to 8 prospects JR can still interview, in his team-board order at the latest step, names and positions
    only. A board row is not an invitation: a withdrawn name, a shared key or a prospect outside the interview
    windows stays off the list (`scouting_interviews.eligible` is the same rule the War Room shows)."""
    tid = thread["team_id"]
    draft_season = int(thread["season"]) + 1
    step = si.window(cur, draft_season)["latest_step"]
    if step is None or not _regclass(cur, "scout_team_boards"):
        return []
    allowed = {e["prospect_uid"] for e in si.eligible(cur, draft_season, tid)}
    if not allowed:
        return []
    cur.execute("SELECT prospect_key FROM scout_interviews WHERE draft_season = %s AND team_id = %s",
                (draft_season, tid))
    interviewed = {r[0] for r in cur.fetchall()}
    cur.execute("""
        SELECT spi.prospect_uid, b.prospect_key, d.full_name, d.position
        FROM scout_team_boards b
        JOIN scout_prospects sp ON sp.draft_season = b.draft_season AND sp.prospect_key = b.prospect_key
        JOIN scout_prospect_ids spi ON spi.draft_season = b.draft_season
             AND spi.prospect_key = b.prospect_key AND spi.status = 'OK'
        LEFT JOIN draft_prospects d ON d.id = sp.prospect_id
        WHERE b.draft_season = %s AND b.step = %s AND b.team_id = %s
        ORDER BY b.team_rank
    """, (draft_season, step, tid))
    out = []
    for uid, key, name, position in cur.fetchall():
        if key in interviewed or str(uid) not in allowed:
            continue
        out.append({"name": name, "position": position})
        if len(out) >= SHORTLIST_MAX:
            break
    return out


def act(cur, thread, option_id, payload, note):
    if option_id == "ACKNOWLEDGE":
        return ActResult(facts={}, intent="ACKNOWLEDGED", engine_ref={"reply": "ACKNOWLEDGE"},
                         close=True, judge=False)
    if option_id == "SHORTLIST":
        shortlist = _shortlist(cur, thread)
        return ActResult(facts={"shortlist": shortlist}, intent="SHORTLIST",
                         engine_ref={"shortlist": shortlist})
    raise InboxError(400, "answer with SHORTLIST or ACKNOWLEDGE")


def template(intent, facts):
    if intent == "COMBINE_OPEN":
        return _window_line(facts, "combine interviews")
    if intent == "VISITS_OPEN":
        return _window_line(facts, "top-30 visits")
    if intent == "REPORTS_READY":
        return _reports_line(facts)
    if intent == "SHORTLIST":
        return _shortlist_line(facts)
    if intent == "ACKNOWLEDGED":
        return "Mu Scouting Department: good luck with the board."
    return "Mu Scouting Department has an update."


def _window_line(facts, what):
    b = facts.get("budget") or {}
    if what == "combine interviews":
        return f"Mu Scouting Department: combine interviews are open. You have {b.get('combine_left')} combine slots left."
    return f"Mu Scouting Department: top-30 visits are open. You have {b.get('visits_left')} visits left."


def _reports_line(facts):
    edition = facts.get("edition") or "New"
    risers = facts.get("risers") or []
    if not risers:
        return f"Mu Scouting Department: the {edition} scouting reports are ready."
    named = ", ".join(f"{r['name']} (rank {r['rank']})" for r in risers)
    return f"Mu Scouting Department: the {edition} scouting reports are ready. Biggest risers on your board: {named}."


def _shortlist_line(facts):
    shortlist = facts.get("shortlist") or []
    if not shortlist:
        return "Mu Scouting Department: nothing new on your board right now."
    named = ", ".join(f"{p['name']} ({p['position']})" for p in shortlist)
    return f"Mu Scouting Department: your board, in order: {named}."


def validate(text, facts):
    return sn.validate_scout_text(text, facts)


def start(cur, team, body):
    raise InboxError(400, "the scouting department starts these conversations")
