"""Combine interviews and top-30 visits (engines/scouting_interviews.py; SCOUTING_REVAMP_PLAN.md §2.6, §4 T-I1..T-I10).

Offline: the flow functions run against an in-memory SQLite database with the scouting tables they read, through the
shared inbox FakeCursor. The pure functions (tone, topics, grade, classifier, facts, templates, validator, summary)
take dicts and fake llms. No PostgreSQL connection is opened.
"""
import json
import pathlib
import sqlite3
import time
from collections import Counter

import pytest

from engines import scouting_interviews as si
from engines import scouting_value as sv
from tests.test_inbox_store import FakeCursor

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRAITS = ROOT / "newsroom" / "config" / "persona_traits.json"

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
CREATE TABLE scout_consensus (draft_season INT, step INT, prospect_key TEXT, rank INT, grade REAL, round_label TEXT,
                              PRIMARY KEY (draft_season, step, prospect_key));
CREATE TABLE scout_events (id INTEGER PRIMARY KEY AUTOINCREMENT, draft_season INT, step INT, prospect_key TEXT,
                           kind TEXT, team_id INT, payload TEXT, impact REAL, headline TEXT, blurb TEXT, seq INT);
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


# ------------------------------------------------------------------ seeding helpers ---
def seed_team(cur, tid=1, abbr="CAR", name="Carolina Panthers"):
    cur.execute("INSERT INTO teams (id, abbreviation, name) VALUES (%s, %s, %s)", (tid, abbr, name))


def seed_run(cur, draft_season, step):
    cur.execute("INSERT INTO scout_runs (draft_season, step, season, week, stage) VALUES (%s, %s, %s, %s, %s)",
                (draft_season, step, draft_season - 1, step, None))


def seed_prospect(cur, draft_season, uid, key, step, name, position="QB", college="MARYLAND", height=75, weight=220,
                  age=21, rank=10, status="ACTIVE", grade=88.0):
    cur.execute("INSERT INTO scout_prospect_ids (prospect_uid, birth_key, draft_season, prospect_key, status, "
                "first_seen) VALUES (%s, %s, %s, %s, 'OK', 't')", (uid, f"board:{draft_season}:{key}",
                                                                   draft_season, key))
    cur.execute("INSERT INTO draft_prospects (draft_season, full_name, position, college, height_in, weight, age) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)", (draft_season, name, position, college, height, weight, age))
    cur.execute("SELECT id FROM draft_prospects WHERE draft_season = %s AND full_name = %s ORDER BY id DESC LIMIT 1",
                (draft_season, name))
    dpid = cur.fetchone()[0]
    cur.execute("INSERT INTO scout_prospects (draft_season, prospect_key, prospect_id, status, class_year, "
                "conference) VALUES (%s, %s, %s, %s, %s, %s)", (draft_season, key, dpid, status, "junior", "Big Ten"))
    cur.execute("INSERT INTO scout_consensus (draft_season, step, prospect_key, rank, grade, round_label) "
                "VALUES (%s, %s, %s, %s, %s, %s)", (draft_season, step, key, rank, grade, "1st round"))


def seed_event(cur, draft_season, step, key, kind, payload, seq=0):
    cur.execute("INSERT INTO scout_events (draft_season, step, prospect_key, kind, team_id, payload, impact, "
                "headline, blurb, seq) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (draft_season, step, key, kind, None, payload, 0.0, "", "", seq))


def seed_interview(cur, draft_season, team_id, uid, key, kind, window_step, logged_step=None):
    cur.execute("INSERT INTO scout_interviews (draft_season, team_id, prospect_uid, prospect_key, kind, window_step, "
                "logged_step, status, questions_max, tone, disposition_src, turns) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, 'STRONG', 'NEUTRAL', '[]')",
                (draft_season, team_id, uid, key, kind, window_step, logged_step, si.QUESTIONS[kind]))


# ------------------------------------------------------------------ pure prospect / events ---
def pure_prospect(uid="u1", events=None):
    return {"prospect_uid": uid, "full_name": "John Smith", "position": "QB", "college": "MARYLAND",
            "school": sv.school("Maryland"), "height_in": 75, "weight": 220, "age": 21, "class_year": "junior",
            "conference": "Big Ten", "status": "ACTIVE", "events": events or []}


def game_events():
    return [
        {"step": 5, "kind": "GAME", "headline": "John Smith shines vs Michigan", "blurb": "",
         "payload": {"opponent": "Michigan", "label": "college week 6", "game_type": "REGULAR",
                     "stats": {"comp": 24, "att": 38, "pass_yds": 310, "pass_td": 3, "int": 0, "rush_yds": 20}}},
        {"step": 4, "kind": "GAME", "headline": "John Smith struggles vs Indiana", "blurb": "",
         "payload": {"opponent": "Indiana", "label": "college week 5", "game_type": "REGULAR",
                     "stats": {"comp": 18, "att": 34, "pass_yds": 180, "pass_td": 1, "int": 2, "rush_yds": 5}}},
    ]


# ----------------------------------------------------------------------------------- T-I1 ---
def test_windows_by_latest_step(cur):
    for step in (23, 24, 25, 26):
        seed_run(cur, 2027, step)
        w = si.window(cur, 2027)
        assert w["latest_step"] == step
        assert w["combine_open"] == (step == 24)
        assert w["visit_open"] == (step in (24, 25))
        assert w["visit_for_step"] == ({24: 25, 25: 26}.get(step))


def test_window_without_a_run(cur):
    assert si.window(cur, 2027) == {"latest_step": None, "combine_open": False, "visit_open": False,
                                    "visit_for_step": None}


# ----------------------------------------------------------------------------------- T-I2 ---
def test_budget_and_open_errors(cur):
    seed_run(cur, 2027, 24)
    seed_team(cur)
    for i in range(20):
        seed_prospect(cur, 2027, f"u{i}", f"2027:P{i}:QB", 24, f"Prospect {i}", rank=i + 1)
        if i < 16:
            seed_event(cur, 2027, 24, f"2027:P{i}:QB", "COMBINE", {"interviews": "strong"})

    assert si.budget(cur, 2027, 1) == {"combine_used": 0, "combine_left": 15, "visits_used": 0, "visits_left": 6}

    for i in range(15):
        si.open_interview(cur, 2027, 1, f"u{i}", "COMBINE")
    assert si.budget(cur, 2027, 1)["combine_left"] == 0
    with pytest.raises(si.InterviewError) as e:
        si.open_interview(cur, 2027, 1, "u15", "COMBINE")
    assert e.value.code == "NO_BUDGET"

    for i in range(6):
        si.open_interview(cur, 2027, 1, f"u{i}", "VISIT")
    assert si.budget(cur, 2027, 1)["visits_left"] == 0
    with pytest.raises(si.InterviewError) as e:
        si.open_interview(cur, 2027, 1, "u6", "VISIT")
    assert e.value.code == "NO_BUDGET"

    with pytest.raises(si.InterviewError) as e:
        si.open_interview(cur, 2027, 1, "u0", "COMBINE")
    assert e.value.code == "DUPLICATE"

    # no combine invite
    with pytest.raises(si.InterviewError) as e:
        si.open_interview(cur, 2027, 1, "u16", "COMBINE")
    assert e.value.code == "NOT_ELIGIBLE"

    # not active
    seed_prospect(cur, 2027, "ux", "2027:PX:QB", 24, "Prospect X", rank=5, status="WITHDRAWN")
    seed_event(cur, 2027, 24, "2027:PX:QB", "COMBINE", {"interviews": "strong"})
    with pytest.raises(si.InterviewError) as e:
        si.open_interview(cur, 2027, 1, "ux", "VISIT")
    assert e.value.code == "NOT_ELIGIBLE"

    # outside the top 150
    seed_prospect(cur, 2027, "uy", "2027:PY:QB", 24, "Prospect Y", rank=200)
    seed_event(cur, 2027, 24, "2027:PY:QB", "COMBINE", {"interviews": "strong"})
    with pytest.raises(si.InterviewError) as e:
        si.open_interview(cur, 2027, 1, "uy", "VISIT")
    assert e.value.code == "NOT_ELIGIBLE"


def test_window_closed_errors(cur):
    seed_team(cur)
    seed_prospect(cur, 2027, "u0", "2027:P0:QB", 23, "Prospect 0")
    seed_event(cur, 2027, 24, "2027:P0:QB", "COMBINE", {"interviews": "strong"})

    seed_run(cur, 2027, 23)
    with pytest.raises(si.InterviewError) as e:
        si.open_interview(cur, 2027, 1, "u0", "COMBINE")
    assert e.value.code == "WINDOW_CLOSED"
    with pytest.raises(si.InterviewError) as e:
        si.open_interview(cur, 2027, 1, "u0", "VISIT")
    assert e.value.code == "WINDOW_CLOSED"

    seed_run(cur, 2027, 25)
    with pytest.raises(si.InterviewError) as e:
        si.open_interview(cur, 2027, 1, "u0", "COMBINE")
    assert e.value.code == "WINDOW_CLOSED"

    seed_run(cur, 2027, 26)
    with pytest.raises(si.InterviewError) as e:
        si.open_interview(cur, 2027, 1, "u0", "VISIT")
    assert e.value.code == "WINDOW_CLOSED"


def test_no_questions_error(cur):
    seed_run(cur, 2027, 24)
    seed_team(cur)
    seed_prospect(cur, 2027, "u0", "2027:P0:QB", 24, "Prospect 0")
    seed_event(cur, 2027, 24, "2027:P0:QB", "COMBINE", {"interviews": "strong"})
    row = si.open_interview(cur, 2027, 1, "u0", "COMBINE")
    for _ in range(row["questions_max"]):
        si.ask(cur, row["id"], topic="FOOTBALL_IQ")
    with pytest.raises(si.InterviewError) as e:
        si.ask(cur, row["id"], topic="FOOTBALL_IQ")
    assert e.value.code == "NO_QUESTIONS"
    si.close(cur, row["id"])
    with pytest.raises(si.InterviewError) as e:
        si.ask(cur, row["id"], topic="FOOTBALL_IQ")
    assert e.value.code == "NO_QUESTIONS"


# ----------------------------------------------------------------------------------- T-I3 ---
def test_tone_never_contradicts_a_combine_verdict():
    strong = [{"kind": "COMBINE", "payload": {"interviews": "strong"}}]
    mixed = [{"kind": "COMBINE", "payload": {"interviews": "mixed"}}]
    assert si.tone_for(pure_prospect(), strong, {"tone": "MIXED"}) == "STRONG"
    assert si.tone_for(pure_prospect(), mixed, {"tone": "STRONG"}) == "MIXED"
    assert si.tone_for(pure_prospect(), strong) == "STRONG"
    assert si.tone_for(pure_prospect(), mixed) == "MIXED"


def test_tone_without_a_combine_event_is_seeded():
    p = pure_prospect()
    assert si.tone_for(p, [], {"tone": "STEADY"}) == "STEADY"
    assert si.tone_for(p, [], {"tone": "STRONG"}) == "STRONG"
    # seeded: deterministic for one uid, and always one of the three tones
    assert si.tone_for(p, [], None) in si._TONES
    assert si.tone_for(p, [], None) == si.tone_for(p, [], None)


# ----------------------------------------------------------------------------------- T-I4 ---
def _grade_mix(tone):
    c = Counter()
    for i in range(1000):
        c[si.answer_grade(tone, "FOOTBALL_IQ", pure_prospect(uid=f"p{i}"), 1, 2027, None)] += 1
    return c


def test_grade_mix_per_tone_and_same_topic_same_grade():
    strong = _grade_mix("STRONG")
    assert strong["IMPRESSIVE"] > strong["SOLID"] > strong["GUARDED"] > strong["CONCERNING"]
    assert strong["CONCERNING"] < 60

    steady = _grade_mix("STEADY")
    assert steady["SOLID"] > steady["GUARDED"] > steady["IMPRESSIVE"] > steady["CONCERNING"]

    mixed = _grade_mix("MIXED")
    assert mixed["GUARDED"] > mixed["CONCERNING"] > mixed["SOLID"] > mixed["IMPRESSIVE"]
    assert mixed["IMPRESSIVE"] < 100

    p = pure_prospect(uid="fixed")
    a = si.answer_grade("STRONG", "FOOTBALL_IQ", p, 1, 2027, None)
    b = si.answer_grade("STRONG", "FOOTBALL_IQ", p, 2, 2028, None)
    assert a == b


def test_leadership_tilt_with_a_captain_storyline():
    p = pure_prospect(uid="cap", events=[{"kind": "STORYLINE", "payload": {"subtype": "captain"}}])
    p_no = pure_prospect(uid="cap")
    # the captain storyline shifts the seeded z up by +0.2; with the same uid the grades may differ only upward
    g = si.answer_grade("STEADY", "LEADERSHIP", p, 1, 2027, None)
    g_no = si.answer_grade("STEADY", "LEADERSHIP", p_no, 1, 2027, None)
    assert si._GRADE_SCORE[g] >= si._GRADE_SCORE[g_no]


def test_disposition_tilt_hook():
    p = pure_prospect(uid="d1")
    disp = {"tone": "STRONG", "tilts": {"FOOTBALL_IQ": 5.0}}
    assert si.answer_grade("STEADY", "FOOTBALL_IQ", p, 1, 2027, disp) == "IMPRESSIVE"


# ----------------------------------------------------------------------------------- T-I5 ---
def test_menu_backing_events():
    events = [
        {"kind": "STORYLINE", "payload": {"subtype": "injury", "part": "ankle"}},
        {"kind": "STORYLINE", "payload": {"subtype": "character"}},
        {"kind": "DECLARE", "payload": {}},
        {"kind": "STORYLINE", "payload": {"subtype": "captain"}},
        {"kind": "ALL_STAR", "payload": {"game": "Shrine Bowl"}},
    ] + game_events()
    menu = si.topics(pure_prospect(events=events), events, "COMBINE")
    topics = [m["topic"] for m in menu]
    assert topics[:5] == ["FOOTBALL_IQ", "SCHEME", "WORK_HABITS", "LEADERSHIP", "PRESSURE"]
    for extra in ("INJURY", "CONSISTENCY", "DECLARE", "COMPETITION", "CAPTAIN"):
        assert extra in topics
    assert "OFF_TOPIC" not in topics

    menu_bare = si.topics(pure_prospect(events=game_events()), game_events(), "VISIT")
    topics_bare = [m["topic"] for m in menu_bare]
    assert topics_bare == ["FOOTBALL_IQ", "SCHEME", "WORK_HABITS", "LEADERSHIP", "PRESSURE"]
    assert "OFF_TOPIC" not in topics_bare


def test_competition_is_non_fbs_or_shrine_bowl():
    fcs = pure_prospect(uid="fcs", events=game_events())
    fcs["school"] = {"key": "ALABAMA STATE", "display": "Alabama State", "conference": "SWAC", "level": "FCS"}
    fcs["college"] = "ALABAMA STATE"
    assert any(m["topic"] == "COMPETITION" for m in si.topics(fcs, game_events(), "COMBINE"))
    # a P4 school with no Shrine Bowl does not get COMPETITION
    p4 = pure_prospect(events=game_events())
    p4["school"] = {"key": "ALABAMA", "display": "Alabama", "conference": "SEC", "level": "P4"}
    assert not any(m["topic"] == "COMPETITION" for m in si.topics(p4, game_events(), "COMBINE"))


# ----------------------------------------------------------------------------------- T-I6 ---
def test_validate_answer_rejections():
    facts = si.answer_facts(pure_prospect(), {"topic": "FOOTBALL_IQ", "question": "q", "fact": None},
                            "IMPRESSIVE", ("CAR", "Carolina Panthers"))
    ok, why = si.validate_answer("He ran a 4.2 forty.", facts)
    assert not ok and "4.2" in why
    ok, why = si.validate_answer("He is still recovering from a torn ACL.", facts)
    assert not ok
    ok, why = si.validate_answer("His OVR is great.", facts)
    assert not ok
    ok, why = si.validate_answer("He went to Alabama.", facts)
    assert not ok
    ok, why = si.validate_answer("The best player in the draft, an amazing talent.", facts)
    assert ok, why  # IMPRESSIVE is allowed to praise

    concern = si.answer_facts(pure_prospect(), {"topic": "FOOTBALL_IQ", "question": "q", "fact": None},
                              "CONCERNING", ("CAR", "Carolina Panthers"))
    ok, why = si.validate_answer("The best player in the draft, an amazing talent.", concern)
    assert not ok and "praise" in why
    # an IMPRESSIVE answer must not use a concern word
    ok, why = si.validate_answer("There is a real concern about him.", facts)
    assert not ok and "concern" in why


def test_templates_pass_their_own_validator():
    prospect = pure_prospect(events=game_events())
    events = game_events()
    menu = {m["topic"]: m for m in si.topics(prospect, events, "COMBINE")}
    for topic, by_grade in si.TEMPLATES.items():
        row = menu.get(topic) or {"topic": topic, "question": "", "fact": None}
        for grade in si.ANSWER_GRADES:
            facts = si.answer_facts(prospect, row, grade, ("CAR", "Carolina Panthers"))
            text = si.answer_template(facts)
            ok, why = si.validate_answer(text, facts)
            assert ok, (topic, grade, why, text)


def test_summary_template_and_grade():
    interview = {"kind": "COMBINE", "turns": [{"topic": "FOOTBALL_IQ", "grade": "IMPRESSIVE"},
                                              {"topic": "SCHEME", "grade": "SOLID"}],
                 "summary_grade": si._summary_grade([{"grade": "IMPRESSIVE"}, {"grade": "SOLID"}])}
    facts = si.summary_facts(interview, pure_prospect())
    assert facts["best"] == "FOOTBALL_IQ" and facts["worst"] == "SCHEME"
    text = si.summary_template(facts)
    assert "John Smith" in text and "next report" in text
    # IMPRESSIVE 3 + SOLID 2 -> 2.5 -> round half to even -> 2 -> SOLID
    assert si._summary_grade([{"grade": "IMPRESSIVE"}, {"grade": "SOLID"}]) == "SOLID"
    assert si._summary_grade([{"grade": "IMPRESSIVE"}]) == "IMPRESSIVE"
    assert si._summary_grade([{"grade": "CONCERNING"}]) == "CONCERNING"
    assert si._summary_grade([]) is None


# ----------------------------------------------------------------------------------- T-I7 ---
def test_classify_keyword_rule():
    menu = [{"topic": "INJURY"}, {"topic": "SCHEME"}]
    assert si.classify("How is the knee?", menu) == ("INJURY", "RULE")
    assert si.classify("Is the medical clear?", menu) == ("INJURY", "RULE")
    # a rule whose topic isn't in the menu falls through
    assert si.classify("How is the knee?", [{"topic": "SCHEME"}]) == ("OFF_TOPIC", "FALLBACK")


def test_classify_llm_and_fallbacks():
    menu = [{"topic": "SCHEME"}, {"topic": "FOOTBALL_IQ"}]
    assert si.classify("something typed", menu, llm=lambda p: "SCHEME") == ("SCHEME", "LLM")
    assert si.classify("something typed", menu, llm=lambda p: "POLITICS") == ("OFF_TOPIC", "LLM")
    assert si.classify("something typed", menu, llm=None) == ("OFF_TOPIC", "FALLBACK")


def test_classify_timeout_is_a_fallback(monkeypatch):
    monkeypatch.setattr(si, "CLASSIFY_TIMEOUT_S", 0.05)
    slow = lambda p: (time.sleep(1.0), "SCHEME")[1]
    assert si.classify("something typed", [{"topic": "SCHEME"}], llm=slow) == ("OFF_TOPIC", "FALLBACK")


# --------------------------------------------------------------------------------- eligible ---
def test_eligible_kinds_and_window(cur):
    seed_run(cur, 2027, 24)
    seed_team(cur)
    for i in range(3):
        seed_prospect(cur, 2027, f"u{i}", f"2027:P{i}:QB", 24, f"Prospect {i}", rank=i + 1)
        seed_event(cur, 2027, 24, f"2027:P{i}:QB", "COMBINE", {"interviews": "strong"})
    seed_prospect(cur, 2027, "u3", "2027:P3:QB", 24, "Prospect 3", rank=4)          # no combine invite

    by_uid = {e["prospect_uid"]: e for e in si.eligible(cur, 2027, 1)}
    assert by_uid["u0"]["kinds"] == ["COMBINE", "VISIT"]
    assert by_uid["u3"]["kinds"] == ["VISIT"]
    assert by_uid["u0"]["full_name"] == "Prospect 0" and by_uid["u0"]["rank"] == 1

    si.open_interview(cur, 2027, 1, "u0", "COMBINE")
    by_uid = {e["prospect_uid"]: e for e in si.eligible(cur, 2027, 1)}
    assert by_uid["u0"]["kinds"] == ["VISIT"]

    seed_run(cur, 2027, 25)                                                          # combine window closes
    by_uid = {e["prospect_uid"]: e for e in si.eligible(cur, 2027, 1)}
    assert all("COMBINE" not in e["kinds"] for e in by_uid.values())
    seed_run(cur, 2027, 26)                                                          # visits close too
    assert si.eligible(cur, 2027, 1) == []


# ----------------------------------------------------------------------------------- T-I8 ---
def test_jr_visit_keys_stamps_and_is_idempotent(cur):
    seed_run(cur, 2027, 25)
    seed_team(cur)
    for i in range(8):
        seed_prospect(cur, 2027, f"u{i}", f"2027:P{i}:QB", 25, f"Prospect {i}", rank=i + 1)
    # 6 opened at step 24 (for step 25), 2 at step 25 (for step 26), oldest first = insertion order
    for i in range(6):
        seed_interview(cur, 2027, 1, f"u{i}", f"2027:P{i}:QB", "VISIT", 24)
    for i in range(6, 8):
        seed_interview(cur, 2027, 1, f"u{i}", f"2027:P{i}:QB", "VISIT", 25)

    keys = si.jr_visit_keys(cur, 2027, 25, 1)
    assert keys == [f"2027:P{i}:QB" for i in range(6)]
    # second call returns the same set and stamps nothing new
    assert si.jr_visit_keys(cur, 2027, 25, 1) == keys
    cur.execute("SELECT COUNT(*) FROM scout_interviews WHERE team_id = 1 AND kind = 'VISIT' AND logged_step = 25")
    assert cur.fetchone()[0] == 6
    cur.execute("SELECT COUNT(*) FROM scout_interviews WHERE team_id = 1 AND kind = 'VISIT' AND logged_step IS NULL")
    assert cur.fetchone()[0] == 2

    assert si.jr_visit_keys(cur, 2027, 26, 1) == [f"2027:P{i}:QB" for i in range(6, 8)]


def test_jr_visit_keys_returns_the_current_key(cur):
    seed_run(cur, 2027, 25)
    seed_team(cur)
    seed_prospect(cur, 2027, "u0", "2027:P0:QB", 25, "Prospect 0", rank=1)
    seed_interview(cur, 2027, 1, "u0", "2027:P0:QB", "VISIT", 24)
    cur.execute("UPDATE scout_prospect_ids SET prospect_key = %s WHERE prospect_uid = 'u0'", ("2027:RENAMED:QB",))
    assert si.jr_visit_keys(cur, 2027, 25, 1) == ["2027:RENAMED:QB"]


# ----------------------------------------------------------------------------------- T-I9 ---
def test_prospect_disposition_is_none_and_class_stays_neutral(cur):
    seed_run(cur, 2027, 24)
    seed_team(cur)
    seed_prospect(cur, 2027, "u0", "2027:P0:QB", 24, "Prospect 0")
    seed_event(cur, 2027, 24, "2027:P0:QB", "COMBINE", {"interviews": "strong"})
    assert si.prospect_disposition(cur, "u0") is None
    row = si.open_interview(cur, 2027, 1, "u0", "COMBINE")
    assert row["disposition_src"] == "NEUTRAL"
    cur.execute("SELECT disposition_src FROM scout_interviews WHERE id = %s", (row["id"],))
    assert cur.fetchone()[0] == "NEUTRAL"


# ---------------------------------------------------------------------------------- T-I10 ---
def _persona_words():
    data = json.loads(TRAITS.read_text())
    words = set()
    for t in data["traits"].values():
        for k in ("id", "name", "label"):
            words.add(str(t[k]).lower())
    words.discard("guarded")          # the interview's own answer grade, not a personality leak
    return words


def test_no_persona_trait_leaks_and_no_writes_outside_interviews(cur):
    words = _persona_words()
    assert words, "persona_traits.json should have traits"
    prospect = pure_prospect(events=game_events())
    events = game_events()
    menu = {m["topic"]: m for m in si.topics(prospect, events, "COMBINE")}

    def check(artifact):
        hay = json.dumps(artifact, default=str).lower() if isinstance(artifact, dict) else str(artifact).lower()
        for w in words:
            assert w not in hay, w

    for topic, by_grade in si.TEMPLATES.items():
        row = menu.get(topic) or {"topic": topic, "question": "", "fact": None}
        for grade in si.ANSWER_GRADES:
            facts = si.answer_facts(prospect, row, grade, ("CAR", "Carolina Panthers"))
            check(facts)
            check(si.answer_template(facts))
    interview = {"kind": "COMBINE", "turns": [{"topic": "FOOTBALL_IQ", "grade": "IMPRESSIVE"}],
                 "summary_grade": "IMPRESSIVE"}
    check(si.summary_facts(interview, prospect))
    check(si.summary_template(si.summary_facts(interview, prospect)))
    check(si._classify_prompt("some question", [{"topic": "SCHEME"}]))

    # no interview function writes outside scout_interviews
    seed_run(cur, 2027, 24)
    seed_team(cur)
    seed_prospect(cur, 2027, "u0", "2027:P0:QB", 24, "Prospect 0")
    seed_event(cur, 2027, 24, "2027:P0:QB", "COMBINE", {"interviews": "strong"})
    seed_event(cur, 2027, 4, "2027:P0:QB", "GAME",
               {"opponent": "Indiana", "stats": {"comp": 18, "att": 34, "pass_yds": 180, "pass_td": 1, "int": 2}})
    seed_prospect(cur, 2027, "u1", "2027:P1:QB", 24, "Prospect 1", rank=2)
    seed_event(cur, 2027, 24, "2027:P1:QB", "COMBINE", {"interviews": "strong"})
    seed_interview(cur, 2027, 1, "u1", "2027:P1:QB", "VISIT", 24)
    cur.statements.clear()
    row = si.open_interview(cur, 2027, 1, "u0", "COMBINE")
    si.ask(cur, row["id"], topic="FOOTBALL_IQ")
    si.close(cur, row["id"])
    si.link_thread(cur, row["id"], 1)
    si.jr_visit_keys(cur, 2027, 25, 1)
    writes = [s for s in cur.statements if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert writes
    for s in writes:
        assert "scout_interviews" in s, s


# ----------------------------------------------------------------- the audit's edge cases ---
def test_ask_on_a_prospect_who_left_the_board_is_an_error_not_a_crash(cur):
    """M-0023: `ask` read prospect["prospect_key"] from a None `_prospect` (the board row gone, or a shared key) and
    died with TypeError, which the inbox turned into a 500 instead of a refusal."""
    seed_run(cur, 2027, 24)
    seed_team(cur)
    seed_prospect(cur, 2027, "u0", "2027:P0:QB", 24, "Prospect 0")
    seed_event(cur, 2027, 24, "2027:P0:QB", "COMBINE", {"interviews": "strong"})
    row = si.open_interview(cur, 2027, 1, "u0", "COMBINE")
    cur.execute("UPDATE scout_prospect_ids SET status = 'KEY_COLLISION' WHERE prospect_uid = %s", ("u0",))

    with pytest.raises(si.InterviewError) as e:
        si.ask(cur, row["id"], topic="FOOTBALL_IQ")
    assert e.value.code == "UNKNOWN_PROSPECT"


def test_a_combine_eligible_prospect_past_rank_150_is_still_listed(cur):
    """M-0023: `eligible` capped the whole read at VISIT_TOP, so a prospect with a combine invite but a rank below
    150 never appeared — COMBINE never needed the top-150 rule, only a stored invite."""
    seed_run(cur, 2027, 24)
    seed_team(cur)
    seed_prospect(cur, 2027, "u0", "2027:P0:QB", 24, "Prospect 0", rank=1)
    seed_prospect(cur, 2027, "u1", "2027:P1:QB", 24, "Prospect 1", rank=si.VISIT_TOP + 25)
    seed_event(cur, 2027, 24, "2027:P1:QB", "COMBINE", {"interviews": "strong"})    # invited, but a late-round name

    by_uid = {e["prospect_uid"]: e for e in si.eligible(cur, 2027, 1)}
    assert by_uid["u1"]["kinds"] == ["COMBINE"]                                     # not a VISIT: past the cut
    assert by_uid["u0"]["kinds"] == ["VISIT"]                                       # no invite of his own


def test_a_dry_run_lists_the_visit_keys_without_stamping_them(cur):
    """M-0023: default_extras ran in a dry run too, and jr_visit_keys stamped logged_step — a dry run wrote."""
    seed_run(cur, 2027, 24)
    seed_team(cur)
    seed_prospect(cur, 2027, "u0", "2027:P0:QB", 24, "Prospect 0")
    seed_interview(cur, 2027, 1, "u0", "2027:P0:QB", "VISIT", window_step=24)
    seed_run(cur, 2027, 25)

    keys = si.jr_visit_keys(cur, 2027, 25, 1, stamp=False)
    assert keys == ["2027:P0:QB"]
    cur.execute("SELECT logged_step FROM scout_interviews WHERE prospect_uid = %s", ("u0",))
    assert cur.fetchone()[0] is None                                                # untouched

    assert si.jr_visit_keys(cur, 2027, 25, 1) == ["2027:P0:QB"]                     # the real run stamps it
    cur.execute("SELECT logged_step FROM scout_interviews WHERE prospect_uid = %s", ("u0",))
    assert cur.fetchone()[0] == 25
