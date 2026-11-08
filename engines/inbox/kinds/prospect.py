"""PROSPECT: JR's GM interviews a college prospect (docs/in_progress/SCOUTING_REVAMP_PLAN.md §2.6, §2.8; V2.10 U5).

The counterpart is the prospect (COUNTERPART_TYPE "prospect"; PERSONA_ROLE None until the personality draft plan).
JR starts the thread from the Draft War Room's Interviews card: `start` opens a `scout_interviews` row through
engines.scouting_interviews and links the inbox thread to it. The prospect's words are the inbox voice's (neutral: a
polite, nervous-but-prepared college prospect), graded by the engine before the words are written, with a template
fallback.

Every turn is `scouting_interviews.ask` -- a menu topic, or a typed question the classifier maps to a permitted topic
(label `draft_interview_classify`, only when MU_INBOX_LLM is on). The last question or END_INTERVIEW closes the
interview and the department scout writes the summary. The engine decides every grade, topic and summary; the LLM
only writes the words and, for a typed question, picks which engine-permitted topic it is. Prospects never write
first (`scan` returns nothing).
"""
import os

from engines import prospect_ids
from engines import scouting_interviews as si
from engines import scouting_narrative as sn
from engines.inbox import InboxError
from engines.inbox.registry import ActResult, Trigger

KIND = "PROSPECT"
COUNTERPART_TYPE = "prospect"
PERSONA_ROLE = None
ACTOR_ROLES = ("GM",)
VOICE = "a polite, nervous-but-prepared college prospect"
LINK_TABLE = "scout_interviews"


def _classify_llm():
    """The typed-question classifier, only when MU_INBOX_LLM is on (voice.default_llm's gate)."""
    if os.environ.get("MU_INBOX_LLM", "1").strip() == "0":
        return None
    try:
        from engines.gm_llm import make_llm
        return make_llm("draft_interview_classify")
    except Exception:
        return None


def _subject(kind, prospect):
    name = (prospect.get("full_name") or "the prospect").title()
    position = prospect.get("position") or ""
    school = (prospect.get("school") or {}).get("display") or (prospect.get("college") or "").title()
    line = ", ".join(p for p in (name, position, school) if p)
    prefix = "Combine interview" if kind == "COMBINE" else "Visit"
    return f"{prefix}: {line}"


def _prospect_sheet(prospect, team):
    """The prospect's fact sheet for the opening GREETING (same shape as scouting_interviews.answer_facts)."""
    sheet = sn.fact_sheet({"kind": "GREETING", "payload": {}, "blurb": ""}, prospect, team=team)
    sheet["assets"] = [{"kind": "prospect", "name": sheet["name"], "position": sheet["position"],
                        "school": sheet["school"]}]
    sheet["money"] = []
    return sheet


def scan(cur, season, week, team):
    return []                                        # prospects never write first


def start(cur, team, body):
    """body {"kind":"PROSPECT", "prospect_uid" (or prospect_key), "interview": "COMBINE"|"VISIT"} -> the interview's
    Trigger (GREETING, linked to the scout_interviews row). An InterviewError becomes InboxError(409, message)."""
    body = dict(body or {})
    draft_season = int(team["season"]) + 1
    uid = body.get("prospect_uid")
    if uid is None:
        key = body.get("prospect_key")
        if not key:
            raise InboxError(400, "a PROSPECT thread needs prospect_uid (or prospect_key)")
        uid = prospect_ids.uid_for(cur, draft_season, key)
        if uid is None:
            raise InboxError(409, f"no prospect for {key!r}")
    uid = str(uid)
    kind = str(body.get("interview") or "").upper()
    if kind not in ("COMBINE", "VISIT"):
        raise InboxError(400, "interview must be COMBINE or VISIT")
    try:
        interview = si.open_interview(cur, draft_season, team["team_id"], uid, kind)
    except si.InterviewError as e:
        raise InboxError(409, e.message) from None
    prospect = si._prospect(cur, draft_season, uid)
    abbr, name = si._team(cur, team["team_id"])
    return Trigger(KIND, team["team_id"], COUNTERPART_TYPE, prospect_ids.person_ref(cur, uid),
                   prospect["full_name"], _subject(kind, prospect), f"interview:{interview['id']}",
                   _prospect_sheet(prospect, (abbr, name)), "GREETING",
                   link_table=LINK_TABLE, link_id=interview["id"], actor_role="GM")


def _interview(cur, thread):
    """The thread's scout_interviews row, linking the interview back to the thread lazily (the thread exists only
    after `start` returns, so the reverse link is set on the first options/act)."""
    iid = thread.get("link_id")
    if not iid:
        return None
    row = si._interview_row(cur, iid)
    if row is not None and not row.get("thread_id") and thread.get("link_table") == "scout_interviews":
        row = si.link_thread(cur, iid, thread["id"])
    return row


def _menu(cur, interview):
    """The engine-permitted topic menu for this interview (the same topics scouting_interviews.ask uses)."""
    prospect = si._prospect(cur, interview["draft_season"], interview["prospect_uid"])
    events = si._events(cur, interview["draft_season"], prospect["prospect_key"])
    return si.topics(prospect, events, interview["kind"])


def options(cur, thread, last):
    """One ASK_<TOPIC> option per still-unasked menu topic while questions are left, plus END_INTERVIEW. A note with
    no option is a typed question (V2.9 calls act(option_id=None, note=...) "talk only")."""
    interview = _interview(cur, thread)
    if interview is None or interview.get("status") == "CLOSED":
        return []
    turns = interview.get("turns") or []
    left = int(interview.get("questions_max") or 0) - len(turns)
    if left <= 0:
        return []
    asked = {t.get("topic") for t in turns}
    out = [{"id": f"ASK_{m['topic']}", "label": m["question"], "form": None, "needs_note": False}
           for m in _menu(cur, interview) if m["topic"] not in asked]
    out.append({"id": "END_INTERVIEW", "label": "End the interview", "form": None, "needs_note": False})
    return out


def _ask(cur, thread, interview, topic=None, text=None):
    llm = _classify_llm() if text is not None else None
    try:
        result = si.ask(cur, interview["id"], topic=topic, text=text, llm=llm)
    except si.InterviewError as e:
        raise InboxError(409, e.message) from None
    turn = result["turn"]
    if result["questions_left"] <= 0:               # the last question closes with the scout's summary
        facts = si.close(cur, interview["id"])
        return ActResult(facts=facts, intent="SUMMARY", engine_ref={"interview_id": interview["id"]},
                         close=True, judge=False)
    return ActResult(facts=result["facts"], intent=f"ANSWER_{turn['grade']}",
                     engine_ref={"interview_id": interview["id"], "topic": turn["topic"], "grade": turn["grade"],
                                 "classified_by": turn["classified_by"]})


def act(cur, thread, option_id, payload, note):
    interview = _interview(cur, thread)
    if interview is None:
        raise InboxError(404, "no interview for this thread")
    if option_id == "END_INTERVIEW":
        facts = si.close(cur, interview["id"])
        return ActResult(facts=facts, intent="SUMMARY", engine_ref={"interview_id": interview["id"]},
                         close=True, judge=False)
    if option_id and str(option_id).startswith("ASK_"):
        return _ask(cur, thread, interview, topic=str(option_id)[4:])
    if option_id is None and note and note.strip():
        return _ask(cur, thread, interview, text=note)
    raise InboxError(400, "pick a question, end the interview, or type a question")


def template(intent, facts):
    if intent == "GREETING":
        return _greeting(facts)
    if intent == "SUMMARY":
        return si.summary_template(facts)
    return si.answer_template(facts)


def _greeting(facts):
    name = facts.get("name") or "The prospect"
    position = facts.get("position") or "prospect"
    school = facts.get("school")
    if school:
        return f"{name} here. I'm a {position} out of {school}. Thanks for bringing me in."
    return f"{name} here. I'm a {position}. Thanks for bringing me in."


def validate(text, facts):
    return si.validate_answer(text, facts)
