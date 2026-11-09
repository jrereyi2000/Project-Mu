"""PROSPECT and SCOUT inbox kinds (SCOUTING_REVAMP_PLAN.md §2.8, §4 U5; engines/inbox/kinds/prospect.py, scout.py).

Offline: both kinds run against an in-memory SQLite database with the scouting tables they read, through the shared
inbox FakeCursor (psycopg dialect translated). A PROSPECT thread runs open -> ask (menu and typed) -> close on the
fakes; SCOUT notices fire once per window and edition. No PostgreSQL connection is opened.
"""
import pathlib
import sqlite3
import uuid

import pytest

from engines import prospect_ids
from engines import scouting_interviews as si
from engines import scouting_value as sv
from engines.inbox import InboxError, registry
from engines.inbox.kinds import prospect, scout
from tests.test_inbox_store import FakeCursor

SEASON = 2026
DRAFT = 2027
UID1 = "11111111-1111-1111-1111-111111111111"
UID2 = "22222222-2222-2222-2222-222222222222"
KEY1 = "2027:JOHNSMITH:QB"
KEY2 = "2027:JANEDOE:WR"

DDL = """
CREATE TABLE teams (id INTEGER PRIMARY KEY, abbreviation TEXT, name TEXT);
CREATE TABLE draft_prospects (id INTEGER PRIMARY KEY AUTOINCREMENT, draft_season INT, full_name TEXT,
                              position TEXT, college TEXT, height_in INT, weight INT, age INT);
CREATE TABLE scout_prospect_ids (prospect_uid TEXT PRIMARY KEY, birth_key TEXT, draft_season INT, pgid INT,
                                 prospect_key TEXT, key_history TEXT, status TEXT, source_person_id TEXT,
                                 first_seen TEXT, created_at TEXT);
CREATE TABLE scout_prospects (id INTEGER PRIMARY KEY AUTOINCREMENT, draft_season INT, prospect_key TEXT,
                              prospect_id INT, status TEXT, class_year TEXT, conference TEXT);
CREATE TABLE scout_runs (draft_season INT, step INT, season INT, week INT, stage INT,
                         PRIMARY KEY (draft_season, step));
CREATE TABLE scout_consensus (draft_season INT, step INT, prospect_key TEXT, season INT, week INT, stage INT,
                              grade REAL, rank INT, round_label TEXT, delta_grade REAL, delta_rank INT, err REAL,
                              event_ids TEXT, PRIMARY KEY (draft_season, step, prospect_key));
CREATE TABLE scout_events (id INTEGER PRIMARY KEY AUTOINCREMENT, draft_season INT, step INT, prospect_key TEXT,
                           kind TEXT, team_id INT, payload TEXT, impact REAL, headline TEXT, blurb TEXT, seq INT);
CREATE TABLE scout_team_boards (draft_season INT, step INT, team_id INT, prospect_key TEXT, team_grade REAL,
                                team_rank INT, fit REAL, bias REAL, err REAL,
                                PRIMARY KEY (draft_season, step, team_id, prospect_key));
CREATE TABLE scout_reports (draft_season INT, step INT, prospect_uid TEXT, prospect_key TEXT, edition TEXT,
                            grade REAL, rank INT, round_label TEXT, role TEXT, traits TEXT, strengths TEXT,
                            concerns TEXT, flags TEXT, body TEXT, text_source TEXT, scout_id INT, voice TEXT,
                            team_id INT);
CREATE TABLE scout_interviews (id INTEGER PRIMARY KEY AUTOINCREMENT, draft_season INT, team_id INT,
                               prospect_uid TEXT, prospect_key TEXT, kind TEXT, thread_id INT, window_step INT,
                               logged_step INT, status TEXT, questions_max INT, tone TEXT, disposition_src TEXT,
                               turns TEXT, summary_grade TEXT, opened_at TEXT, closed_at TEXT,
                               UNIQUE (draft_season, team_id, prospect_uid, kind));
"""


def scouting_db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(DDL)
    return conn


@pytest.fixture
def cur():
    return FakeCursor(scouting_db())


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    monkeypatch.setenv("MU_INBOX_LLM", "0")


# ------------------------------------------------------------------ seeding helpers ---
def seed_team(cur, tid=1, abbr="CAR", name="Carolina Panthers"):
    cur.execute("INSERT INTO teams (id, abbreviation, name) VALUES (%s, %s, %s)", (tid, abbr, name))


def seed_run(cur, draft_season, step):
    cur.execute("INSERT INTO scout_runs (draft_season, step, season, week, stage) VALUES (%s, %s, %s, %s, %s)",
                (draft_season, step, draft_season - 1, step, None))


def seed_prospect(cur, draft_season, uid, key, step, name="John Smith", position="QB", college="Oregon",
                  rank=10, grade=88.0, delta_rank=None, status="ACTIVE"):
    cur.execute("INSERT INTO scout_prospect_ids (prospect_uid, birth_key, draft_season, prospect_key, status, "
                "first_seen) VALUES (%s, %s, %s, %s, 'OK', 't')", (uid, f"board:{draft_season}:{key}",
                                                                   draft_season, key))
    cur.execute("INSERT INTO draft_prospects (draft_season, full_name, position, college, height_in, weight, age) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)", (draft_season, name, position, college, 75, 220, 21))
    cur.execute("SELECT id FROM draft_prospects WHERE draft_season = %s AND full_name = %s ORDER BY id DESC LIMIT 1",
                (draft_season, name))
    dpid = cur.fetchone()[0]
    cur.execute("INSERT INTO scout_prospects (draft_season, prospect_key, prospect_id, status, class_year, "
                "conference) VALUES (%s, %s, %s, %s, %s, %s)", (draft_season, key, dpid, status, "junior", "Big Ten"))
    cur.execute("INSERT INTO scout_consensus (draft_season, step, prospect_key, season, week, stage, grade, rank, "
                "round_label, delta_grade, delta_rank, err, event_ids) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s)",
                (draft_season, step, key, draft_season - 1, step, None, grade, rank, "1st round", None, delta_rank,
                 0.0, "[]"))


def seed_event(cur, draft_season, step, key, kind, payload, seq=0):
    cur.execute("INSERT INTO scout_events (draft_season, step, prospect_key, kind, team_id, payload, impact, "
                "headline, blurb, seq) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (draft_season, step, key, kind, None, payload, 0.0, "", "", seq))


def team_dict(tid=1, abbr="CAR", season=SEASON, week=24):
    return {"team_id": tid, "abbr": abbr, "season": season, "week": week, "offseason_stage": None}


def open_combine(cur):
    seed_team(cur)
    seed_run(cur, DRAFT, 24)
    seed_prospect(cur, DRAFT, UID1, KEY1, 24)
    seed_event(cur, DRAFT, 24, KEY1, "COMBINE", {"interviews": "strong"})
    return si.open_interview(cur, DRAFT, 1, UID1, "COMBINE")


def thread(iid, **kw):
    row = {"id": 1, "team_id": 1, "kind": "PROSPECT", "season": SEASON, "week": 24,
           "link_table": "scout_interviews", "link_id": iid, "counterpart_type": "prospect",
           "counterpart_id": UID1, "counterpart_name": "John Smith"}
    row.update(kw)
    return row


def pure_prospect():
    return {"prospect_uid": UID1, "full_name": "John Smith", "position": "QB", "college": "Oregon",
            "school": sv.school("Oregon"), "height_in": 75, "weight": 220, "age": 21, "class_year": "junior",
            "conference": "Big Ten", "status": "ACTIVE", "events": []}


# --------------------------------------------------------------------------------- registry ---
def test_both_kinds_are_discovered():
    kinds = registry.kinds(refresh=True)
    assert kinds.get("PROSPECT") is prospect
    assert kinds.get("SCOUT") is scout
    assert kinds["PROSPECT"].ACTOR_ROLES == ("GM",)
    assert kinds["SCOUT"].ACTOR_ROLES == ("GM",)


def test_scout_cannot_start_and_prospects_never_scan(cur):
    with pytest.raises(InboxError) as e:
        scout.start(cur, team_dict(), {})
    assert e.value.status == 400
    assert prospect.scan(cur, SEASON, 24, team_dict()) == []


# -------------------------------------------------------------------------------- PROSPECT start ---
def test_start_opens_an_interview_and_links_it(cur):
    seed_team(cur)
    seed_run(cur, DRAFT, 24)
    seed_prospect(cur, DRAFT, UID1, KEY1, 24)
    seed_event(cur, DRAFT, 24, KEY1, "COMBINE", {"interviews": "strong"})
    trig = prospect.start(cur, team_dict(), {"kind": "PROSPECT", "prospect_uid": UID1, "interview": "COMBINE"})
    assert trig.kind == "PROSPECT" and trig.intent == "GREETING"
    assert trig.counterpart_id == prospect_ids.person_ref(cur, UID1) == UID1
    assert trig.counterpart_name == "John Smith"
    assert trig.link_table == "scout_interviews" and trig.link_id is not None
    assert trig.source_ref == f"interview:{trig.link_id}"
    assert trig.subject == "Combine interview: John Smith, QB, Oregon"
    assert trig.facts["assets"][0] == {"kind": "prospect", "name": "John Smith", "position": "quarterback",
                                       "school": "Oregon"}


def test_start_resolves_a_prospect_key(cur):
    seed_team(cur)
    seed_run(cur, DRAFT, 24)
    seed_prospect(cur, DRAFT, UID1, KEY1, 24)
    seed_event(cur, DRAFT, 24, KEY1, "COMBINE", {"interviews": "strong"})
    trig = prospect.start(cur, team_dict(), {"kind": "PROSPECT", "prospect_key": KEY1, "interview": "COMBINE"})
    assert trig.counterpart_id == UID1


def test_start_maps_an_interview_error_to_409(cur):
    seed_team(cur)
    seed_run(cur, DRAFT, 23)                              # window closed
    seed_prospect(cur, DRAFT, UID1, KEY1, 23)
    with pytest.raises(InboxError) as e:
        prospect.start(cur, team_dict(week=23), {"kind": "PROSPECT", "prospect_uid": UID1, "interview": "COMBINE"})
    assert e.value.status == 409


# -------------------------------------------------------------------------------- PROSPECT options ---
def test_options_list_the_menu_and_end(cur):
    interview = open_combine(cur)
    opts = prospect.options(cur, thread(interview["id"]), None)
    ids = [o["id"] for o in opts]
    assert ids[-1] == "END_INTERVIEW"
    for topic in ("FOOTBALL_IQ", "SCHEME", "WORK_HABITS", "LEADERSHIP", "PRESSURE"):
        assert f"ASK_{topic}" in ids
    assert all(o["form"] is None and o["needs_note"] is False for o in opts)


def test_options_empty_after_the_questions_are_used(cur):
    interview = open_combine(cur)
    for _ in range(si.QUESTIONS["COMBINE"]):
        prospect.act(cur, thread(interview["id"]), "ASK_FOOTBALL_IQ", {}, None)
    assert prospect.options(cur, thread(interview["id"]), None) == []


# ---------------------------------------------------------------------------------- PROSPECT act ---
def test_act_menu_option_returns_an_answer(cur):
    interview = open_combine(cur)
    res = prospect.act(cur, thread(interview["id"]), "ASK_FOOTBALL_IQ", {}, None)
    assert res.intent == f"ANSWER_{res.engine_ref['grade']}"
    assert res.engine_ref["topic"] == "FOOTBALL_IQ"
    assert res.engine_ref["grade"] in si.ANSWER_GRADES
    assert res.engine_ref["classified_by"] == "MENU"
    assert res.engine_ref["interview_id"] == interview["id"]
    assert not res.close and res.facts["topic"] == "FOOTBALL_IQ"


def test_act_typed_question_uses_the_classifier(cur):
    interview = open_combine(cur)
    res = prospect.act(cur, thread(interview["id"]), None, {}, "How do you lead your teammates?")
    assert res.intent.startswith("ANSWER_")
    assert res.engine_ref["topic"] == "LEADERSHIP"
    assert res.engine_ref["classified_by"] == "RULE"


def test_last_question_closes_with_a_summary(cur):
    interview = open_combine(cur)
    t = thread(interview["id"])
    for topic in ("FOOTBALL_IQ", "SCHEME", "WORK_HABITS", "LEADERSHIP"):
        res = prospect.act(cur, t, f"ASK_{topic}", {}, None)
    assert res.intent == "SUMMARY" and res.close
    assert res.facts["summary_grade"] is not None


def test_end_interview_closes(cur):
    interview = open_combine(cur)
    res = prospect.act(cur, thread(interview["id"]), "END_INTERVIEW", {}, None)
    assert res.intent == "SUMMARY" and res.close
    assert res.facts["summary_grade"] is None


# --------------------------------------------------------------------- PROSPECT template/validate ---
def test_template_and_validate(cur):
    facts = si.answer_facts(pure_prospect(), {"topic": "FOOTBALL_IQ", "question": "Q?", "fact": None}, "SOLID")
    text = prospect.template("ANSWER_SOLID", facts)
    assert text and "playbook" in text
    ok, why = prospect.validate(text, facts)
    assert ok, why
    ok2, why2 = prospect.validate("I have a 90 OVR rating.", facts)
    assert not ok2 and why2
    assert "impressed" in prospect.template("SUMMARY", {"name": "John Smith", "summary_grade": "IMPRESSIVE",
                                                        "best": "FOOTBALL_IQ", "worst": None})
    assert "John Smith" in prospect.template("GREETING", facts)


# ------------------------------------------------------------------------------------ SCOUT scan ---
def test_scan_fires_combine_and_visits_at_step_24(cur):
    seed_team(cur)
    seed_run(cur, DRAFT, 24)
    trigs = scout.scan(cur, SEASON, 24, team_dict())
    by_intent = {t.intent: t for t in trigs}
    assert set(by_intent) == {"COMBINE_OPEN", "VISITS_OPEN"}
    assert by_intent["COMBINE_OPEN"].source_ref == f"scout:{DRAFT}:COMBINE_OPEN:24"
    assert by_intent["VISITS_OPEN"].source_ref == f"scout:{DRAFT}:VISITS_OPEN:24"
    assert by_intent["COMBINE_OPEN"].facts["budget"]["combine_left"] == si.COMBINE_BUDGET
    assert by_intent["COMBINE_OPEN"].counterpart_id == "department"
    assert by_intent["COMBINE_OPEN"].counterpart_name == "Mu Scouting Department"


def test_scan_fires_only_visits_at_step_25(cur):
    seed_team(cur)
    seed_run(cur, DRAFT, 25)
    trigs = scout.scan(cur, SEASON, 25, team_dict(week=25))
    assert [t.intent for t in trigs] == ["VISITS_OPEN"]
    assert trigs[0].source_ref == f"scout:{DRAFT}:VISITS_OPEN:25"


def test_scan_fires_nothing_outside_the_windows(cur):
    seed_team(cur)
    seed_run(cur, DRAFT, 23)
    assert scout.scan(cur, SEASON, 23, team_dict(week=23)) == []


def test_scan_fires_nothing_at_step_26(cur):
    seed_team(cur)
    seed_run(cur, DRAFT, 26)
    assert scout.scan(cur, SEASON, 26, team_dict(week=26)) == []


def test_scan_fires_reports_ready_once_per_edition(cur):
    seed_team(cur)
    seed_run(cur, DRAFT, 26)
    seed_prospect(cur, DRAFT, UID1, KEY1, 26, name="John Smith", position="QB", rank=5, delta_rank=5)
    seed_prospect(cur, DRAFT, UID2, KEY2, 26, name="Jane Doe", position="WR", rank=6, delta_rank=3)
    cur.execute("INSERT INTO scout_team_boards (draft_season, step, team_id, prospect_key, team_grade, team_rank) "
                "VALUES (%s, %s, %s, %s, %s, %s)", (DRAFT, 26, 1, KEY1, 85.0, 1))
    cur.execute("INSERT INTO scout_team_boards (draft_season, step, team_id, prospect_key, team_grade, team_rank) "
                "VALUES (%s, %s, %s, %s, %s, %s)", (DRAFT, 26, 1, KEY2, 84.0, 2))
    cur.execute("INSERT INTO scout_reports (draft_season, step, prospect_uid, prospect_key, edition, grade, rank, "
                "round_label, role, traits, strengths, concerns, flags, body, text_source) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (DRAFT, 26, UID1, KEY1, "FINAL", 88.0, 5, "1st round", "DAY1_STARTER", "[]", "[]", "[]", "[]",
                 "{}", "TEMPLATE"))
    trigs = scout.scan(cur, SEASON, 26, team_dict(week=26))
    assert [t.intent for t in trigs] == ["REPORTS_READY"]
    trig = trigs[0]
    assert trig.source_ref == f"scout:{DRAFT}:REPORTS_READY:26"
    assert trig.facts["edition"] == "FINAL"
    risers = trig.facts["risers"]
    assert risers == [{"name": "John Smith", "rank": 5}, {"name": "Jane Doe", "rank": 6}]
    assert all(set(r) == {"name", "rank"} for r in risers)


# ------------------------------------------------------------------------------------- SCOUT act ---
def test_shortlist_names_and_positions_only(cur):
    seed_team(cur)
    seed_run(cur, DRAFT, 24)
    seed_prospect(cur, DRAFT, UID1, KEY1, 24, name="John Smith", position="QB", rank=5)
    seed_prospect(cur, DRAFT, UID2, KEY2, 24, name="Jane Doe", position="WR", rank=6)
    cur.execute("INSERT INTO scout_team_boards (draft_season, step, team_id, prospect_key, team_grade, team_rank) "
                "VALUES (%s, %s, %s, %s, %s, %s)", (DRAFT, 24, 1, KEY2, 84.0, 1))
    cur.execute("INSERT INTO scout_team_boards (draft_season, step, team_id, prospect_key, team_grade, team_rank) "
                "VALUES (%s, %s, %s, %s, %s, %s)", (DRAFT, 24, 1, KEY1, 85.0, 2))
    res = scout.act(cur, {"team_id": 1, "season": SEASON}, "SHORTLIST", {}, None)
    assert res.intent == "SHORTLIST"
    items = res.facts["shortlist"]
    assert [i["name"] for i in items] == ["Jane Doe", "John Smith"]       # team-board order
    for i in items:
        assert set(i) == {"name", "position"}


def test_shortlist_skips_already_interviewed(cur):
    seed_team(cur)
    seed_run(cur, DRAFT, 24)
    seed_prospect(cur, DRAFT, UID1, KEY1, 24, name="John Smith", position="QB", rank=5)
    seed_prospect(cur, DRAFT, UID2, KEY2, 24, name="Jane Doe", position="WR", rank=6)
    cur.execute("INSERT INTO scout_team_boards (draft_season, step, team_id, prospect_key, team_grade, team_rank) "
                "VALUES (%s, %s, %s, %s, %s, %s)", (DRAFT, 24, 1, KEY1, 85.0, 1))
    cur.execute("INSERT INTO scout_team_boards (draft_season, step, team_id, prospect_key, team_grade, team_rank) "
                "VALUES (%s, %s, %s, %s, %s, %s)", (DRAFT, 24, 1, KEY2, 84.0, 2))
    cur.execute("INSERT INTO scout_interviews (draft_season, team_id, prospect_uid, prospect_key, kind, window_step, "
                "status, questions_max, tone, disposition_src, turns) VALUES (%s, %s, %s, %s, %s, %s, 'OPEN', %s, "
                "'STRONG', 'NEUTRAL', '[]')", (DRAFT, 1, UID1, KEY1, "COMBINE", 24, si.QUESTIONS["COMBINE"]))
    res = scout.act(cur, {"team_id": 1, "season": SEASON}, "SHORTLIST", {}, None)
    assert [i["name"] for i in res.facts["shortlist"]] == ["Jane Doe"]


def test_acknowledge_closes(cur):
    res = scout.act(cur, {"team_id": 1, "season": SEASON}, "ACKNOWLEDGE", {}, None)
    assert res.intent == "ACKNOWLEDGED" and res.close


def test_scout_templates_and_validate(cur):
    assert "combine" in scout.template("COMBINE_OPEN", {"budget": {"combine_left": 15, "visits_left": 6}})
    assert "visits" in scout.template("VISITS_OPEN", {"budget": {"combine_left": 15, "visits_left": 6}})
    line = scout.template("REPORTS_READY", {"edition": "FINAL", "risers": [{"name": "John Smith", "rank": 5}]})
    assert "FINAL" in line and "John Smith" in line
    ok, why = scout.validate("The FINAL reports are ready.", {"edition": "FINAL"})
    assert ok, why
