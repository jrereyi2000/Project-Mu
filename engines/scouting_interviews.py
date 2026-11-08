"""Combine interviews and top-30 visits (docs/in_progress/SCOUTING_REVAMP_PLAN.md §2.6, V2.10 U3; PROJECT_SPEC §3.3g).

Interviews are never personality evidence: the words, answer grades, summaries, tone and `turns` are never read by the
personality core and never create, seed or change a prospect's profile. Once prospect personalities exist they only
shape an interview (its tone and per-topic tilts, through `prospect_disposition`), and no trait name, slider value or
personality label is ever put on a fact sheet, in a prompt, in a template, in an API response or in the UI.

Everything is deterministic. The words are written by the inbox voice (§2.8); this module supplies the engine: windows,
budgets, eligibility, tone, the permitted topic menu, the answer grade, the typed-question classifier, the fact sheets,
the fallback templates and their validator, the summary, and the `scout_interviews` rows. The LLM is
engines.gm_llm.make_llm(label) or None; tests pass a fake callable. Nothing here writes outside `scout_interviews`.
"""
import json
import random
import re
import threading

from psycopg.types.json import Jsonb

from engines import gm_llm
from engines import scouting_narrative as sn
from engines import scouting_value as sv
from engines.college_stats import aggregate_games

# ---------------------------------------------------------------- constants ---
COMBINE_STEP = 24                       # the latest stored step when a combine interview can be opened
VISIT_STEPS = (24, 25)                  # latest stored steps when a top-30 visit can be opened
COMBINE_BUDGET = 15                     # formal combine interviews per team per class
VISITS_PER_STEP = 6                     # = scouting_events.visits per_team
QUESTIONS = {"COMBINE": 4, "VISIT": 6}
ANSWER_GRADES = ("IMPRESSIVE", "SOLID", "GUARDED", "CONCERNING")
CLASSIFY_TIMEOUT_S = 4.0

VISIT_TOP = 150                         # the consensus cutoff for a visit
_GRADE_SCORE = {"IMPRESSIVE": 3, "SOLID": 2, "GUARDED": 1, "CONCERNING": 0}
_GRADE_BY_SCORE = {3: "IMPRESSIVE", 2: "SOLID", 1: "GUARDED", 0: "CONCERNING"}

_BASE = {"STRONG": 0.6, "STEADY": 0.2, "MIXED": -0.3}
_TOPIC_TILT = {"CAPTAIN": 0.4, "CONSISTENCY": -0.4, "INJURY": 0.0, "OFF_TOPIC": -0.2}
_TONES = ("STRONG", "STEADY", "MIXED")


class InterviewError(Exception):
    """An open_interview refusal. code: WINDOW_CLOSED | NO_BUDGET | NOT_ELIGIBLE | DUPLICATE | NO_QUESTIONS."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------- pure seeds ---
def _rng(*parts):
    return random.Random(":".join(str(p) for p in parts))


def _json(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        return json.loads(v)
    return v


# ------------------------------------------------------------------ windows ---
def window(cur, draft_season):
    """{"latest_step", "combine_open", "visit_open", "visit_for_step"}. A visit opened while step 24 is the latest
    becomes a step-25 VISIT; while 25 is the latest, a step-26 VISIT."""
    cur.execute("SELECT step FROM scout_runs WHERE draft_season = %s ORDER BY step DESC LIMIT 1", (draft_season,))
    row = cur.fetchone()
    latest = int(row[0]) if row else None
    if latest is None:
        return {"latest_step": None, "combine_open": False, "visit_open": False, "visit_for_step": None}
    return {"latest_step": latest,
            "combine_open": latest == COMBINE_STEP,
            "visit_open": latest in VISIT_STEPS,
            "visit_for_step": latest + 1 if latest in VISIT_STEPS else None}


def budget(cur, draft_season, team_id):
    """{"combine_used", "combine_left", "visits_used", "visits_left"}; visits are counted per visit_for_step."""
    cur.execute("SELECT id FROM scout_interviews WHERE draft_season = %s AND team_id = %s AND kind = 'COMBINE'",
                (draft_season, team_id))
    combine_used = len(cur.fetchall())
    vstep = window(cur, draft_season)["visit_for_step"]
    if vstep:
        cur.execute("SELECT id FROM scout_interviews WHERE draft_season = %s AND team_id = %s AND kind = 'VISIT' "
                    "AND window_step = %s", (draft_season, team_id, vstep - 1))
        visits_used = len(cur.fetchall())
    else:
        visits_used = 0
    return {"combine_used": combine_used, "combine_left": COMBINE_BUDGET - combine_used,
            "visits_used": visits_used, "visits_left": VISITS_PER_STEP - visits_used}


def _close_stale(cur, draft_season, team_id):
    """An interview still OPEN once its window closes is closed (summary_grade from its turns)."""
    w = window(cur, draft_season)
    if w["latest_step"] is None:
        return
    for kind, open_now in (("COMBINE", w["combine_open"]), ("VISIT", w["visit_open"])):
        if open_now:
            continue
        cur.execute("SELECT id, turns FROM scout_interviews WHERE draft_season = %s AND team_id = %s AND kind = %s "
                    "AND status = 'OPEN'", (draft_season, team_id, kind))
        for iid, turns in cur.fetchall():
            cur.execute("UPDATE scout_interviews SET status = 'CLOSED', summary_grade = %s, closed_at = now() "
                        "WHERE id = %s", (_summary_grade(_json(turns)), iid))


# ------------------------------------------------------------------- helpers ---
def _team(cur, team_id):
    cur.execute("SELECT abbreviation, name FROM teams WHERE id = %s", (team_id,))
    row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


def _prospect(cur, draft_season, prospect_uid):
    """The interviewable prospect's public fields, or None (unknown, or a KEY_COLLISION)."""
    cur.execute("""
        SELECT spi.prospect_key, spi.status, d.full_name, d.position, d.college, d.height_in, d.weight, d.age,
               sp.class_year, sp.conference, sp.status
        FROM scout_prospect_ids spi
        LEFT JOIN scout_prospects sp ON sp.draft_season = spi.draft_season AND sp.prospect_key = spi.prospect_key
        LEFT JOIN draft_prospects d ON d.id = sp.prospect_id
        WHERE spi.draft_season = %s AND spi.prospect_uid = %s""", (draft_season, str(prospect_uid)))
    row = cur.fetchone()
    if not row:
        return None
    key, id_status, full_name, position, college, height_in, weight, age, class_year, conference, status = row
    if id_status != "OK":
        return None
    prospect = {"prospect_uid": str(prospect_uid), "prospect_key": key, "full_name": full_name or "",
                "position": position, "college": college, "height_in": height_in, "weight": weight, "age": age,
                "class_year": class_year, "conference": conference, "status": status, "school": sv.school(college)}
    step = window(cur, draft_season)["latest_step"]
    if step is not None:
        cur.execute("SELECT rank, grade, round_label FROM scout_consensus WHERE draft_season = %s AND step = %s "
                    "AND prospect_key = %s", (draft_season, step, key))
        crow = cur.fetchone()
        if crow:
            prospect["rank"], prospect["grade"], prospect["round_label"] = crow[0], float(crow[1]), crow[2]
    return prospect


def _events(cur, draft_season, prospect_key, through_step=None):
    """The prospect's stored events (payload keys starting '_' stripped), ordered by step."""
    sql = "SELECT step, kind, headline, blurb, payload FROM scout_events WHERE draft_season = %s AND prospect_key = %s"
    args = [draft_season, prospect_key]
    if through_step is not None:
        sql += " AND step <= %s"
        args.append(through_step)
    cur.execute(sql + " ORDER BY step, seq, id", args)
    out = []
    for step, kind, headline, blurb, payload in cur.fetchall():
        out.append({"step": step, "kind": kind, "headline": headline, "blurb": blurb,
                    "payload": {k: v for k, v in (_json(payload) or {}).items() if not str(k).startswith("_")}})
    return out


def _interview_row(cur, interview_id):
    cur.execute("""SELECT id, draft_season, team_id, prospect_uid, prospect_key, kind, thread_id, window_step,
                          logged_step, status, questions_max, tone, disposition_src, turns, summary_grade,
                          opened_at, closed_at
                   FROM scout_interviews WHERE id = %s""", (interview_id,))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(zip(("id", "draft_season", "team_id", "prospect_uid", "prospect_key", "kind", "thread_id", "window_step",
                  "logged_step", "status", "questions_max", "tone", "disposition_src", "turns", "summary_grade",
                  "opened_at", "closed_at"), row))
    d["prospect_uid"] = str(d["prospect_uid"]) if d["prospect_uid"] is not None else None
    d["turns"] = _json(d["turns"])
    return d


def _summary_grade(turns):
    """IMPRESSIVE 3 .. CONCERNING 0, mean rounded."""
    scores = [_GRADE_SCORE.get(t.get("grade")) for t in (turns or []) if t.get("grade") in _GRADE_SCORE]
    if not scores:
        return None
    idx = max(0, min(3, int(round(sum(scores) / len(scores)))))
    return _GRADE_BY_SCORE[idx]


# ---------------------------------------------------------------------- tone ---
def tone_for(prospect, events, disposition=None):
    """With a COMBINE event the stored verdict wins (STRONG/MIXED, never contradicted). Without one, seeded by
    (prospect_uid, 'tone') 40/40/20 STRONG/STEADY/MIXED; a non-None disposition replaces the seeded draw only."""
    combine = next((e for e in (events or []) if e.get("kind") == "COMBINE"), None)
    if combine is not None:
        return "STRONG" if (combine.get("payload") or {}).get("interviews") == "strong" else "MIXED"
    if disposition is not None:
        tone = (disposition or {}).get("tone")
        if tone in _TONES:
            return tone
    return _rng(prospect.get("prospect_uid"), "tone").choices(_TONES, weights=(40, 40, 20))[0]


# --------------------------------------------------------------------- topics ---
TOPIC_QUESTIONS = {
    "FOOTBALL_IQ": "Put your favourite play on the whiteboard.",
    "SCHEME": "How would you fit what we do?",
    "WORK_HABITS": "Walk me through a normal week of your prep.",
    "LEADERSHIP": "How do you lead when things go sideways?",
    "PRESSURE": "Walk me through your worst game.",
    "INJURY": "How is the injury?",
    "CONSISTENCY": "Why has your tape been up and down?",
    "DECLARE": "Why did you declare early?",
    "COMPETITION": "How do you answer the level-of-competition question?",
    "CAPTAIN": "What did being a captain mean to you?",
}
TOPIC_WORDS = {
    "FOOTBALL_IQ": "football IQ", "SCHEME": "scheme fit", "WORK_HABITS": "work habits", "LEADERSHIP": "leadership",
    "PRESSURE": "handling his worst game", "INJURY": "the injury", "CONSISTENCY": "his consistency",
    "DECLARE": "why he declared", "COMPETITION": "his level of competition", "CAPTAIN": "his captaincy",
    "OFF_TOPIC": "staying on topic",
}

_VERDICT_RANK = {"STRUGGLES": 0, "STEADY": 1, "SHINES": 2}


def _pick_worst_game(log):
    """The worst game: a STRUGGLES line when there is one (most recent), else STEADY, else SHINES."""
    return min(log, key=lambda g: (_VERDICT_RANK.get(g.get("verdict"), 1), -int(g.get("step") or 0)))


def _worst_game_fact(prospect, events):
    agg = aggregate_games(events or [], prospect.get("position"))
    log = agg.get("game_log") or []
    if not log:
        return "a game I'd rather not put back on tape"
    worst = _pick_worst_game(log)
    return " · ".join(p for p in (worst.get("label"), worst.get("stat_line")) if p) \
        or "a game I'd rather not put back on tape"


def _event_of(events, kind):
    return next((e for e in (events or []) if e.get("kind") == kind), None)


def _story_fact(events, subtype):
    story = next((e for e in (events or []) if e.get("kind") == "STORYLINE"
                  and (e.get("payload") or {}).get("subtype") == subtype), None)
    return story


def _injury_fact(events):
    story = _story_fact(events, "injury")
    if story is not None:
        part = (story.get("payload") or {}).get("part") or "nagging"
        return f"a day-to-day {part} injury with no games missed"
    if _event_of(events, "MEDICAL") is not None:
        return "an old injury that was re-checked and cleared"
    return None


def _consistency_fact(events):
    if _story_fact(events, "character") is not None:
        return "coaches challenging me to bring the same effort every week"
    if _story_fact(events, "rough_outing") is not None:
        return "uneven tape from one rough outing"
    return None


def _declare_fact(events):
    return "skipping my final season of eligibility to enter this draft" if _event_of(events, "DECLARE") else None


def _competition_fact(prospect, events):
    school = prospect.get("school") or {}
    if school.get("level") and school.get("level") not in ("P4", "FBS"):
        return f"playing at a {school.get('level')} school"
    if any(e.get("kind") == "ALL_STAR" and (e.get("payload") or {}).get("game") == "Shrine Bowl"
           for e in (events or [])):
        return "playing at the Shrine Bowl"
    return None


def _captain_fact(events):
    return "being named a team captain" if _story_fact(events, "captain") else None


def _has_captain_storyline(prospect):
    return any(e.get("kind") == "STORYLINE" and (e.get("payload") or {}).get("subtype") == "captain"
               for e in (prospect.get("events") or []))


def topics(prospect, events, kind):
    """The engine-permitted menu: [{topic, question, fact}]. OFF_TOPIC is never in the menu."""
    out = [
        {"topic": "FOOTBALL_IQ", "question": TOPIC_QUESTIONS["FOOTBALL_IQ"], "fact": None},
        {"topic": "SCHEME", "question": TOPIC_QUESTIONS["SCHEME"], "fact": None},
        {"topic": "WORK_HABITS", "question": TOPIC_QUESTIONS["WORK_HABITS"], "fact": None},
        {"topic": "LEADERSHIP", "question": TOPIC_QUESTIONS["LEADERSHIP"], "fact": None},
        {"topic": "PRESSURE", "question": TOPIC_QUESTIONS["PRESSURE"], "fact": _worst_game_fact(prospect, events)},
    ]
    for topic, fact in (("INJURY", _injury_fact(events)), ("CONSISTENCY", _consistency_fact(events)),
                        ("DECLARE", _declare_fact(events)), ("COMPETITION", _competition_fact(prospect, events)),
                        ("CAPTAIN", _captain_fact(events))):
        if fact:
            out.append({"topic": topic, "question": TOPIC_QUESTIONS[topic], "fact": fact})
    return out


# --------------------------------------------------------------------- grade ---
def answer_grade(tone, topic, prospect, seq, draft_season, disposition=None):
    """The seeded answer grade: base + topic tilt (+ LEADERSHIP with a captain storyline) + 0.5·z seeded by
    (prospect_uid, topic). Cut points >0.6 IMPRESSIVE, >0.0 SOLID, >-0.5 GUARDED, else CONCERNING."""
    score = _BASE.get(tone, 0.2) + _TOPIC_TILT.get(topic, 0.0)
    if topic == "LEADERSHIP" and _has_captain_storyline(prospect):
        score += 0.2
    if disposition is not None:
        score += float((disposition or {}).get("tilts", {}).get(topic, 0.0))
    score += 0.5 * _rng(prospect.get("prospect_uid"), topic).gauss(0.0, 1.0)
    if score > 0.6:
        return "IMPRESSIVE"
    if score > 0.0:
        return "SOLID"
    if score > -0.5:
        return "GUARDED"
    return "CONCERNING"


# ----------------------------------------------------------------- classifier ---
KEYWORD_RULES = (
    (re.compile(r"\binjur|ankle|knee|hamstring|shoulder|wrist|hip|medical|heal|cleared", re.I), "INJURY"),
    (re.compile(r"consisten|up and down|rough outing", re.I), "CONSISTENCY"),
    (re.compile(r"\bdeclare|eligib|final season|skip", re.I), "DECLARE"),
    (re.compile(r"competition|level of|fcs|fbs|shrine|small school", re.I), "COMPETITION"),
    (re.compile(r"\bcaptain|patch|captaincy", re.I), "CAPTAIN"),
    (re.compile(r"\bleader|lead by|huddle|teammates|locker room", re.I), "LEADERSHIP"),
    (re.compile(r"scheme|fit\b|system|playbook", re.I), "SCHEME"),
    (re.compile(r"whiteboard|football iq|\biq\b|\breads?\b", re.I), "FOOTBALL_IQ"),
    (re.compile(r"work ethic|routine|lift|prep|habits|film room", re.I), "WORK_HABITS"),
    (re.compile(r"pressure|worst game|tough game|adversity", re.I), "PRESSURE"),
)


def timed_call(llm, prompt, timeout_s):
    """gm_llm.call(llm, prompt) on a worker thread; None on a timeout or an error (the thread finishes in the
    background). gm_llm has no timed_call, so it lives here."""
    if llm is None:
        return None
    box = {}

    def run():
        try:
            box["text"] = gm_llm.call(llm, prompt)
        except Exception:
            box["text"] = None

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        return None
    return box.get("text")


def _classify_prompt(text, menu):
    ids = ", ".join(row["topic"] for row in menu)
    return ("You are sorting a college prospect's typed interview question into one topic. Choose exactly one of "
            f"these topics and answer with its id only: {ids}. If none fits, answer NONE.\nQUESTION: {text}")


def classify(text, menu, llm=None):
    """(topic, classified_by). Keyword rules first (RULE, only when the rule's topic is in the menu); else one id
    from the llm (label draft_interview_classify): in the menu -> LLM, not in the menu -> OFF_TOPIC (LLM); no llm or
    a timeout -> OFF_TOPIC (FALLBACK)."""
    ids = {row["topic"] for row in menu}
    for rule, topic in KEYWORD_RULES:
        if rule.search(text or "") and topic in ids:
            return topic, "RULE"
    if llm is None:
        return "OFF_TOPIC", "FALLBACK"
    got = timed_call(llm, _classify_prompt(text, menu), CLASSIFY_TIMEOUT_S)
    if got is None:
        return "OFF_TOPIC", "FALLBACK"
    got = (got or "").strip().upper()
    if got in ids:
        return got, "LLM"
    return "OFF_TOPIC", "LLM"


# --------------------------------------------------------------------- facts ---
def _all_numbers(sheet):
    out = set()

    def walk(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            out.add(float(v))
        elif isinstance(v, str):
            for n in sn.NUM_RE.findall(v):
                out.add(float(n))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    walk(sheet)
    return sorted(out)


def _assets_for(sheet):
    return [{"kind": "prospect", "name": sheet.get("name"), "position": sheet.get("position"),
             "school": sheet.get("school")}]


def answer_facts(prospect, topic_row, grade, team=None):
    """The fact sheet one answer's words may use: the scouting sheet + topic/fact/grade + the gm_rationale sheet keys
    the inbox validator expects (numbers = every number on the sheet, assets = the prospect's name, money = [])."""
    sheet = sn.fact_sheet({"kind": topic_row["topic"], "payload": {}, "blurb": topic_row.get("question") or ""},
                          prospect, team=team)
    sheet["topic"] = topic_row["topic"]
    sheet["question"] = topic_row.get("question")
    sheet["fact"] = topic_row.get("fact")
    sheet["grade"] = grade
    sheet["numbers"] = _all_numbers(sheet)
    sheet["assets"] = _assets_for(sheet)
    sheet["money"] = []
    return sheet


def summary_facts(interview, prospect):
    """The department scout's fact sheet: summary_grade plus the best and worst topics of the turns."""
    turns = _json(interview.get("turns")) or []
    sheet = sn.fact_sheet({"kind": interview.get("kind"), "payload": {}, "blurb": ""}, prospect)
    sheet["summary_grade"] = interview.get("summary_grade")
    sheet["best"] = max(turns, key=lambda t: _GRADE_SCORE.get(t.get("grade"), 0)).get("topic") if turns else None
    sheet["worst"] = min(turns, key=lambda t: _GRADE_SCORE.get(t.get("grade"), 0)).get("topic") if turns else None
    sheet["numbers"] = _all_numbers(sheet)
    sheet["assets"] = _assets_for(sheet)
    sheet["money"] = []
    return sheet


# ----------------------------------------------------------------- templates ---
TEMPLATES = {
    "FOOTBALL_IQ": {
        "IMPRESSIVE": "I'd put our base concept on the board and walk you through every read, adjustment and check I "
                      "make. I can tell you why each one works against a single-high or a two-high look.",
        "SOLID": "I'd draw up our go-to play and talk through my reads. I know the playbook cold and I can explain "
                 "the adjustments.",
        "GUARDED": "I could draw something up if you want. I know the plays.",
        "CONCERNING": "Honestly, I'm more of a see-it-and-react player than a whiteboard guy. That side of the game "
                      "is a concern for me.",
    },
    "SCHEME": {
        "IMPRESSIVE": "I fit right into what you run. I've watched your scheme and I can point to the reps where I'd "
                      "do the same job.",
        "SOLID": "I think I fit well. I've played in something similar and I picked it up fast.",
        "GUARDED": "I can play in any scheme. I'll do whatever you ask.",
        "CONCERNING": "I've mostly been in one system, so there would be a real learning curve. That's a risk I'd "
                      "have to work through.",
    },
    "WORK_HABITS": {
        "IMPRESSIVE": "I'm the first one in and the last one out. My week is film, lifting and treatment, and I keep "
                      "the same routine every day.",
        "SOLID": "I keep a steady routine: lift, film, practice. I don't skip steps.",
        "GUARDED": "I work hard. I put the time in.",
        "CONCERNING": "I do my work, but I'm not as structured as I should be. My habits are a concern I'm still "
                      "trying to fix.",
    },
    "LEADERSHIP": {
        "IMPRESSIVE": "I lead by example and I'm not afraid to speak up in the huddle. My teammates look for me when "
                      "it gets loud.",
        "SOLID": "I try to bring the younger guys along and set a tone in practice.",
        "GUARDED": "I lead by how I play. I'm not a big rah-rah guy.",
        "CONCERNING": "I've never really been the vocal guy. Honestly, leading people is a concern for me.",
    },
    "PRESSURE": {
        "IMPRESSIVE": "I go back to {fact} all the time. I broke down every rep and I haven't repeated those mistakes.",
        "SOLID": "My worst game was {fact}. I learned from it and moved on.",
        "GUARDED": "Yeah, {fact}. It happens.",
        "CONCERNING": "{fact} still sits with me, and it's a concern I'm still working through.",
    },
    "INJURY": {
        "IMPRESSIVE": "That's behind me. I'm fully healed, I was cleared, and I haven't lost a step.",
        "SOLID": "It's healed. I'm back to normal and I feel good.",
        "GUARDED": "It's fine. It's healed.",
        "CONCERNING": "It's healed, they tell me. I still think about it more than I should.",
    },
    "CONSISTENCY": {
        "IMPRESSIVE": "I've cleaned that up. My last stretch of tape is my best and the effort is the same every snap.",
        "SOLID": "I've worked on it. The week-to-week consistency is coming.",
        "GUARDED": "Some weeks are better than others. I'm working on it.",
        "CONCERNING": "I know the tape is up and down. It's something I still struggle with.",
    },
    "DECLARE": {
        "IMPRESSIVE": "I declared because I'm ready. I've done everything I can at this level and I wanted the next "
                      "challenge.",
        "SOLID": "I felt it was the right time. I'm ready for this.",
        "GUARDED": "It was my decision. I'm here now.",
        "CONCERNING": "I declared before I was totally sure I was ready. That doubt is still a concern.",
    },
    "COMPETITION": {
        "IMPRESSIVE": "I've played the best competition available to me and I've handled it. Put my tape against "
                      "anyone's.",
        "SOLID": "I played who was in front of me and I produced. I can only control that.",
        "GUARDED": "It is what it is. I played my schedule.",
        "CONCERNING": "I know the level of competition is a question. It's a fair concern and I'm working to answer it.",
    },
    "CAPTAIN": {
        "IMPRESSIVE": "Being a captain meant setting the standard every day, not just wearing the patch. I held "
                      "people to it.",
        "SOLID": "I took the role seriously. I tried to be steady for my teammates.",
        "GUARDED": "It was an honor. I did my part.",
        "CONCERNING": "Honestly, the captain title meant more to the team than it did to me. I'm not sure I led the "
                      "way I should have.",
    },
    "OFF_TOPIC": {
        "IMPRESSIVE": "That's not really what you asked, but I'll answer the football question you meant. I'm ready "
                      "for any of it.",
        "SOLID": "I think I've answered the important parts. I'm here to talk football.",
        "GUARDED": "I'd rather stick to football.",
        "CONCERNING": "I'm not sure what you're asking, and I don't love the direction. Can we get back to football?",
    },
}


def answer_template(facts):
    """The fallback first-person answer for a turn's topic and grade; an 'as I said' lead when the topic repeats."""
    topic = facts.get("topic")
    grade = facts.get("grade")
    tmpl = TEMPLATES.get(topic, TEMPLATES["OFF_TOPIC"]).get(grade) or TEMPLATES["OFF_TOPIC"].get(grade)
    text = tmpl.format(**facts)
    if facts.get("again"):
        text = "As I said, " + text[0].lower() + text[1:]
    return text


def summary_template(facts):
    """The department scout's 2-3 sentence read from summary_grade and the best and worst topics."""
    name = facts.get("name") or "The prospect"
    best, worst = facts.get("best"), facts.get("worst")
    grade = facts.get("summary_grade")
    if grade == "IMPRESSIVE":
        body = (f"{name} impressed in the room. He was at his best on {TOPIC_WORDS.get(best, 'the football')}."
                if best else f"{name} impressed in the room. The answers were sharp across the board.")
    elif grade == "CONCERNING":
        body = (f"{name} left us with questions. He was hardest to like on {TOPIC_WORDS.get(worst, 'the football')}."
                if worst else f"{name} left us with questions. The answers raised more doubts than they settled.")
    elif grade == "GUARDED":
        body = (f"{name} kept his answers close to the vest. He said little on {TOPIC_WORDS.get(best, 'the football')}."
                if best else f"{name} kept his answers close to the vest and didn't offer much either way.")
    else:
        body = (f"{name} gave a steady interview. He was best on {TOPIC_WORDS.get(best, 'the football')} and a "
                f"little thinner on {TOPIC_WORDS.get(worst, 'the football')}."
                if best and worst else f"{name} gave a steady interview. Nothing moved the needle either way.")
    return f"{body} The department will fold this into the next report."


# ----------------------------------------------------------------- validator ---
CONCERN_WORDS = (
    r"\bconcern\w*", r"\bworr\w*", r"\bstruggl\w*", r"\brisk\w*", r"\braw\b", r"\bissue\w*", r"\bproblem\w*",
    r"\bflag\w*", r"\binconsisten\w*", r"\bsetback\w*", r"\bnot ready\b", r"\blimited\b", r"\bslow\w*",
)
_CONCERN_RE = tuple(re.compile(p, re.I) for p in CONCERN_WORDS)
_PRAISE_RE = re.compile(r"\b(amazing|excellent|elite|special|can'?t[- ]miss|no doubt|perfect|ideal|polished|"
                        r"best player|everything you want|franchise changer)\b", re.I)


def _all_praise(text):
    return bool(_PRAISE_RE.search(text)) and not any(p.search(text) for p in _CONCERN_RE)


def validate_answer(text, facts):
    """(ok, why): validate_scout_text + a grade check (an IMPRESSIVE/SOLID answer must not use a CONCERN_WORDS word,
    a CONCERNING one must not be all praise)."""
    ok, why = sn.validate_scout_text(text, facts)
    if not ok:
        return False, why
    grade = (facts or {}).get("grade")
    if grade in ("IMPRESSIVE", "SOLID"):
        for p in _CONCERN_RE:
            m = p.search(text)
            if m:
                return False, f"concern word '{m.group(0)}' in a {grade} answer"
    if grade == "CONCERNING" and _all_praise(text):
        return False, "a CONCERNING answer can't be all praise"
    return True, None


# ---------------------------------------------------------------------- flow ---
def prospect_disposition(cur, prospect_uid):
    """None for now: interviews observe pre-existing personality, they don't shape it (§2.6). The personality draft
    plan replaces the body with a read of the prospect's scoped 'scouting' view."""
    return None


def open_interview(cur, draft_season, team_id, prospect_uid, kind):
    """Checks the window, budget and eligibility (raises InterviewError), fixes tone/disposition_src/questions_max,
    inserts the scout_interviews row and returns it with its topic menu."""
    kind = str(kind or "").upper()
    if kind not in ("COMBINE", "VISIT"):
        raise InterviewError("NOT_ELIGIBLE", f"unknown interview kind {kind!r}")
    _close_stale(cur, draft_season, team_id)
    w = window(cur, draft_season)
    if kind == "COMBINE" and not w["combine_open"]:
        raise InterviewError("WINDOW_CLOSED", "combine interviews are closed")
    if kind == "VISIT" and not w["visit_open"]:
        raise InterviewError("WINDOW_CLOSED", "top-30 visits are closed")
    prospect = _prospect(cur, draft_season, prospect_uid)
    if prospect is None:
        raise InterviewError("NOT_ELIGIBLE", "unknown prospect")
    events = _events(cur, draft_season, prospect["prospect_key"], through_step=w["latest_step"])
    if kind == "COMBINE" and not any(e.get("kind") == "COMBINE" for e in events):
        raise InterviewError("NOT_ELIGIBLE", "no combine invite for this prospect")
    if kind == "VISIT":
        if prospect.get("status") != "ACTIVE":
            raise InterviewError("NOT_ELIGIBLE", "the prospect isn't active")
        if int(prospect.get("rank") or 0) > VISIT_TOP or not prospect.get("rank"):
            raise InterviewError("NOT_ELIGIBLE", "the prospect isn't in the consensus top 150")
    cur.execute("SELECT id FROM scout_interviews WHERE draft_season = %s AND team_id = %s AND prospect_uid = %s "
                "AND kind = %s", (draft_season, team_id, str(prospect_uid), kind))
    if cur.fetchone():
        raise InterviewError("DUPLICATE", "this prospect is already interviewed")
    b = budget(cur, draft_season, team_id)
    if kind == "COMBINE" and b["combine_used"] >= COMBINE_BUDGET:
        raise InterviewError("NO_BUDGET", "combine interview budget used up")
    if kind == "VISIT" and b["visits_used"] >= VISITS_PER_STEP:
        raise InterviewError("NO_BUDGET", "visit budget used up")
    disposition = prospect_disposition(cur, prospect_uid)
    tone = tone_for(prospect, events, disposition)
    cur.execute("""INSERT INTO scout_interviews
                   (draft_season, team_id, prospect_uid, prospect_key, kind, window_step, status, questions_max,
                    tone, disposition_src, turns)
                   VALUES (%s, %s, %s, %s, %s, %s, 'OPEN', %s, %s, %s, %s) RETURNING id""",
                (draft_season, team_id, str(prospect_uid), prospect["prospect_key"], kind, w["latest_step"],
                 QUESTIONS[kind], tone, "PERSONALITY" if disposition else "NEUTRAL", Jsonb([])))
    row = _interview_row(cur, cur.fetchone()[0])
    row["menu"] = topics(prospect, events, kind)
    return row


def ask(cur, interview_id, topic=None, text=None, llm=None):
    """One turn: a menu topic (MENU) or a classified typed question. Appends {seq, topic, grade, classified_by} to
    turns and returns it with answer_facts and the questions left. Refuses a closed interview or one with no
    questions left (NO_QUESTIONS)."""
    row = _interview_row(cur, interview_id)
    if row is None or row["status"] == "CLOSED":
        raise InterviewError("NO_QUESTIONS", "the interview is closed")
    turns = row["turns"] or []
    questions_left = row["questions_max"] - len(turns)
    if questions_left <= 0:
        raise InterviewError("NO_QUESTIONS", "no questions left")
    prospect = _prospect(cur, row["draft_season"], row["prospect_uid"])
    events = _events(cur, row["draft_season"], prospect["prospect_key"])
    prospect["events"] = events
    menu = topics(prospect, events, row["kind"])
    menu_rows = {m["topic"]: m for m in menu}
    if topic is not None:
        chosen = str(topic).upper()
        classified_by = "MENU"
        topic_row = menu_rows.get(chosen) or {"topic": chosen, "question": "", "fact": None}
    else:
        chosen, classified_by = classify(text, menu, llm)
        topic_row = menu_rows.get(chosen) or {"topic": chosen, "question": "", "fact": None}
    disposition = prospect_disposition(cur, row["prospect_uid"])
    grade = answer_grade(row["tone"], chosen, prospect, len(turns) + 1, row["draft_season"], disposition)
    turn = {"seq": len(turns) + 1, "topic": chosen, "grade": grade, "classified_by": classified_by}
    turns = list(turns) + [turn]
    cur.execute("UPDATE scout_interviews SET turns = %s WHERE id = %s", (Jsonb(turns), interview_id))
    facts = answer_facts(prospect, topic_row, grade, _team(cur, row["team_id"]))
    facts["again"] = chosen in {t.get("topic") for t in turns[:-1]}
    return {"turn": turn, "facts": facts, "questions_left": questions_left - 1}


def close(cur, interview_id):
    """summary_grade = the mean of the turn grades (IMPRESSIVE 3 .. CONCERNING 0, rounded), status CLOSED; returns
    summary_facts."""
    row = _interview_row(cur, interview_id)
    if row is None:
        raise InterviewError("NO_QUESTIONS", "no such interview")
    grade = _summary_grade(row["turns"])
    cur.execute("UPDATE scout_interviews SET status = 'CLOSED', summary_grade = %s, closed_at = now() WHERE id = %s",
                (grade, interview_id))
    row["status"], row["summary_grade"] = "CLOSED", grade
    prospect = _prospect(cur, row["draft_season"], row["prospect_uid"])
    return summary_facts(row, prospect)


def link_thread(cur, interview_id, thread_id):
    """Sets the interview's inbox thread."""
    cur.execute("UPDATE scout_interviews SET thread_id = %s WHERE id = %s", (thread_id, interview_id))
    return _interview_row(cur, interview_id)


def jr_visit_keys(cur, draft_season, step, team_id):
    """The team's VISIT interviews that are logged_step = step plus the unlogged ones opened at step - 1, which this
    stamps logged_step = step; at most VISITS_PER_STEP, oldest first. Returns each prospect's CURRENT prospect_key."""
    cur.execute("SELECT id, prospect_uid, prospect_key, opened_at FROM scout_interviews WHERE draft_season = %s "
                "AND team_id = %s AND kind = 'VISIT' AND logged_step = %s ORDER BY opened_at, id",
                (draft_season, team_id, step))
    already = cur.fetchall()
    cur.execute("SELECT id, prospect_uid, prospect_key, opened_at FROM scout_interviews WHERE draft_season = %s "
                "AND team_id = %s AND kind = 'VISIT' AND logged_step IS NULL AND window_step = %s ORDER BY opened_at, id",
                (draft_season, team_id, step - 1))
    candidates = cur.fetchall()
    room = VISITS_PER_STEP - len(already)
    stamped = candidates[:max(0, room)]
    for iid, _uid, _key, _opened in stamped:
        cur.execute("UPDATE scout_interviews SET logged_step = %s WHERE id = %s", (step, iid))
    merged = sorted(list(already) + stamped, key=lambda r: (r[3], r[0]))
    out = []
    for _iid, uid, key, _opened in merged:
        cur.execute("SELECT prospect_key FROM scout_prospect_ids WHERE draft_season = %s AND prospect_uid = %s "
                    "AND status = 'OK'", (draft_season, str(uid)))
        crow = cur.fetchone()
        out.append(crow[0] if crow else key)
    return out


def eligible(cur, draft_season, team_id, limit=VISIT_TOP):
    """The prospects JR can still interview (by the latest step's consensus rank), with the kinds open to each."""
    w = window(cur, draft_season)
    if w["latest_step"] is None:
        return []
    step = w["latest_step"]
    cur.execute("SELECT prospect_uid, kind FROM scout_interviews WHERE draft_season = %s AND team_id = %s",
                (draft_season, team_id))
    interviewed = {}
    for uid, kind in cur.fetchall():
        interviewed.setdefault(str(uid), set()).add(kind)
    cur.execute("SELECT prospect_key FROM scout_events WHERE draft_season = %s AND kind = 'COMBINE' AND step <= %s",
                (draft_season, step))
    combine_keys = {r[0] for r in cur.fetchall()}
    cur.execute("""SELECT c.rank, spi.prospect_uid, spi.prospect_key, d.full_name, d.position, d.college, sp.status
                   FROM scout_consensus c
                   JOIN scout_prospects sp ON sp.draft_season = c.draft_season AND sp.prospect_key = c.prospect_key
                   JOIN scout_prospect_ids spi ON spi.draft_season = c.draft_season AND spi.prospect_key = c.prospect_key
                   LEFT JOIN draft_prospects d ON d.id = sp.prospect_id
                   WHERE c.draft_season = %s AND c.step = %s AND spi.status = 'OK' AND c.rank <= %s
                   ORDER BY c.rank""", (draft_season, step, limit))
    out = []
    for rank, uid, key, full_name, position, college, status in cur.fetchall():
        uid = str(uid)
        done = interviewed.get(uid, set())
        kinds = []
        if w["combine_open"] and "COMBINE" not in done and key in combine_keys:
            kinds.append("COMBINE")
        if w["visit_open"] and "VISIT" not in done and status == "ACTIVE" and rank <= VISIT_TOP:
            kinds.append("VISIT")
        if kinds:
            out.append({"prospect_uid": uid, "prospect_key": key, "full_name": full_name, "position": position,
                        "college": college, "rank": rank, "kinds": kinds})
    return out
