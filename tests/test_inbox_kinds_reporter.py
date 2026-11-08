"""REPORTER, the beat-writer interview kind (FRONT_OFFICE_INBOX_PLAN.md §2.8, D4, D12; U3).

Offline: the InboxDB SQLite fake, extended with depth_charts, injuries, games and the executed-move tables.
Templates (llm=None) drive the open-ended interview; the model's `done` key is exercised through the kind's
`on_reply` hook.
"""
import pytest

from engines.inbox import InboxError, actors, scan as scan_mod, store, voice
from engines.inbox.kinds import reporter
from tests.test_inbox_store import InboxDB

SEASON, WEEK, CAR = 2026, 5, 1

EXTRA_DDL = """
CREATE TABLE depth_charts (
    team_id INT, season INT, week INT, position_slot TEXT, depth INT, player_id INT, source TEXT,
    PRIMARY KEY (team_id, season, week, source, position_slot, depth));
CREATE TABLE injuries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, team_id INT, season INT, week INT, player_id INT, injury_type TEXT,
    weeks_remaining INT, mu_weeks_out INT, is_ir BOOLEAN);
CREATE TABLE games (
    id INTEGER PRIMARY KEY AUTOINCREMENT, season INT, week INT, game_type TEXT, status TEXT, home_team_id INT,
    away_team_id INT, home_score INT, away_score INT);
CREATE TABLE league_office_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, season INT, status TEXT);
CREATE TABLE franchise_pending_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT, team_id INT, kind TEXT, status TEXT, payload TEXT, reason TEXT,
    written_run INT);
CREATE TABLE contract_offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, team_id INT, player_id INT, season INT, from_user BOOLEAN, status TEXT,
    total_value REAL);
ALTER TABLE players ADD COLUMN full_name TEXT;
ALTER TABLE player_seasons ADD COLUMN position TEXT;
ALTER TABLE player_seasons ADD COLUMN age INT;
ALTER TABLE player_seasons ADD COLUMN overall_rating INT;
ALTER TABLE player_seasons ADD COLUMN years_of_experience INT;
ALTER TABLE player_seasons ADD COLUMN archetype TEXT;
ALTER TABLE player_seasons ADD COLUMN dev_trait TEXT;
ALTER TABLE player_seasons ADD COLUMN released_week INT;
"""
ROSTER = """
UPDATE players SET full_name = player_name;
UPDATE player_seasons SET position = 'QB', age = 25, overall_rating = 90 WHERE player_id = 100;
UPDATE player_seasons SET position = 'WR', age = 25, overall_rating = 80 WHERE player_id = 101;
UPDATE player_seasons SET position = 'RB', age = 24, overall_rating = 85 WHERE player_id = 200;
INSERT INTO players (id, player_name, full_name) VALUES (103, 'Tommy Tremble', 'Tommy Tremble');
INSERT INTO player_seasons (player_id, team_id, season, position, age, overall_rating)
    VALUES (103, 1, 2026, 'TE', 26, 75);
"""


@pytest.fixture
def db(monkeypatch):
    d = InboxDB()
    monkeypatch.setattr(actors, "user_teams", lambda: [t.upper() for t in d.user_teams])
    d.conn.executescript(EXTRA_DDL)
    d.conn.executescript(ROSTER)
    return d


@pytest.fixture
def cur(db):
    return db.cursor()


def team(cur, role="GM", pid="10"):
    actors.take_control(cur, "CAR", role, pid)
    a = actors.actor_for(cur, "CAR", role)
    return {"team_id": CAR, "abbr": "CAR", "season": SEASON, "week": WEEK, "offseason_stage": None,
            "actors": actors.controlled(cur, "CAR"), "actor": a}


def put_chart(db, week, source, chart):
    rows = [(CAR, SEASON, week, slot, depth, pid, source)
            for slot, ids in chart.items() for depth, pid in enumerate(ids, start=1)]
    db.conn.executemany("INSERT INTO depth_charts VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def interview_thread(cur, body_source="TEMPLATE", asked=1, source_ref="reporter:test", option_body=None):
    actors.take_control(cur, "CAR", "GM", "10")
    tid = store.create_thread(cur, {
        "team_id": CAR, "kind": "REPORTER", "actor_role": "GM", "actor_id": "10",
        "counterpart_type": "writer", "counterpart_id": "car_beat", "counterpart_name": "Joe Person",
        "subject": "Injury update", "started_by": "COUNTERPART", "source_ref": source_ref,
        "season": SEASON, "week": WEEK, "state": {"questions_asked": asked, "topic": "INJURY"},
    })
    store.add_message(cur, tid, "COUNTERPART", option_body or "Bryce Young is out. How do you cover for him?",
                      body_source, options=list(reporter.QUESTION_OPTIONS))
    return store.get_thread(cur, tid)


# ----------------------------------------------------------------------------------------------- triggers ---

def test_beat_writer_chosen(cur):
    assert reporter._beat_writer(cur, CAR) == {"agent_id": "car_beat", "handle": "@carbeat", "name": "Joe Person"}
    assert reporter._beat_writer(cur, 2) is None


def test_scan_uses_the_beat_writer_as_counterpart(db, cur):
    put_chart(db, WEEK, "MU_COACH", {"QB": [100]})
    db.conn.execute("INSERT INTO injuries (team_id, season, week, player_id, injury_type, weeks_remaining) "
                    "VALUES (1, 2026, 5, 100, 'Hamstring', 5)")
    triggers = reporter.scan(cur, SEASON, WEEK, team(cur))
    injury = [t for t in triggers if t.intent == "INJURY"]
    assert len(injury) == 1
    t = injury[0]
    assert t.kind == "REPORTER" and t.counterpart_type == "writer" and t.counterpart_id == "car_beat"
    assert t.counterpart_name == "Joe Person" and t.actor_role == "GM" and t.actor_id == "10"
    assert t.facts["weeks_out"] == 5 and t.facts["topic"] == "INJURY"
    assert t.state == {"questions_asked": 1, "topic": "INJURY", "topic_ref": "reporter:injury:100:2026:5"}


def test_injury_shorter_than_four_weeks_is_not_a_trigger(db, cur):
    put_chart(db, WEEK, "MU_COACH", {"QB": [100]})
    db.conn.execute("INSERT INTO injuries (team_id, season, week, player_id, injury_type, weeks_remaining) "
                    "VALUES (1, 2026, 5, 100, 'Ankle', 2)")
    assert [t for t in reporter.scan(cur, SEASON, WEEK, team(cur)) if t.intent == "INJURY"] == []


def test_three_game_streak_trigger(db, cur):
    db.conn.executemany("INSERT INTO games (season, week, game_type, status, home_team_id, away_team_id, "
                        "home_score, away_score) VALUES (2026, ?, 'REGULAR_SEASON', 'COMPLETED', 1, 2, 10, 20)",
                        [(2,), (3,), (4,)])
    streak = [t for t in reporter.scan(cur, SEASON, WEEK, team(cur)) if t.intent == "STREAK"]
    assert len(streak) == 1 and streak[0].facts["result"] == "losses"
    assert streak[0].source_ref == "reporter:streak:2026:2:losses"


def test_executed_trade_trigger(db, cur):
    db.conn.execute("INSERT INTO league_office_runs (id, season, status) VALUES (1, 2026, 'WRITTEN')")
    db.conn.execute("INSERT INTO franchise_pending_moves (team_id, kind, status, written_run) "
                    "VALUES (1, 'TRADE', 'WRITTEN', 1)")
    moves = [t for t in reporter.scan(cur, SEASON, WEEK, team(cur)) if t.intent == "MOVE"]
    assert [t.source_ref for t in moves] == ["reporter:trade:1"]


def test_lost_starting_job_trigger(db, cur):
    put_chart(db, 4, "MU_COACH", {"QB": [100]})
    put_chart(db, 5, "MU_COACH", {"QB": [103]})
    role = [t for t in reporter.scan(cur, SEASON, WEEK, team(cur)) if t.intent == "ROLE"]
    assert len(role) == 1 and role[0].facts["player_id"] == 100 and role[0].source_ref == "reporter:lost:100:2026:5"


def test_at_most_two_open_interviews_per_team(db, cur):
    put_chart(db, WEEK, "MU_COACH", {"QB": [100], "WR": [101], "TE": [103]})
    db.conn.executemany("INSERT INTO injuries (team_id, season, week, player_id, injury_type, weeks_remaining) "
                        "VALUES (1, 2026, 5, ?, 'Knee', 6)", [(100,), (101,), (103,)])
    t = team(cur)
    kinds = [x for x in reporter.scan(cur, SEASON, WEEK, t) if x.intent == "INJURY"]
    assert len(kinds) == 3
    created = [scan_mod.open_thread(cur, reporter, trig, t["actor"], SEASON, WEEK) for trig in kinds]
    assert sum(1 for c in created if c is not None) == store.max_open("REPORTER") == 2


# ------------------------------------------------------------------------------------------- interview flow ---

def exchange(cur, thread, option, note=None):
    return voice.exchange(cur, reporter, thread["id"], option, {}, note, llm=None)


def test_template_interview_wraps_after_three_questions(cur):
    thread = interview_thread(cur)
    thread = exchange(cur, thread, "ON_RECORD", "He'll be back in a month.")
    assert thread["status"] == "WAITING_JR" and thread["state"]["questions_asked"] == 2
    thread = exchange(cur, thread, "ON_RECORD", "We have faith in the next man up.")
    assert thread["status"] == "WAITING_JR" and thread["state"]["questions_asked"] == 3
    thread = exchange(cur, thread, "ON_RECORD", "It's next man up.")
    assert thread["status"] == "CLOSED" and thread["state"]["done"] is True
    questions = [m for m in store.messages(cur, thread["id"]) if m["author"] == "COUNTERPART"]
    assert len(questions) == 4                                   # opening + two follow-ups + the wrap-up
    assert store.messages(cur, thread["id"])[-1]["body"] == "That's all I need. Thanks for your time."
    assert reporter.options(cur, thread, None) == []


def test_model_done_closes_the_interview(db, cur):
    thread = interview_thread(cur, body_source="LLM")
    reporter.on_reply(cur, thread, {"done": True})
    thread = store.get_thread(cur, thread["id"])
    assert thread["status"] == "CLOSED" and thread["state"]["done"] is True
    assert reporter.options(cur, thread, None) == []
    other = interview_thread(cur, body_source="LLM", source_ref="reporter:test:2")
    reporter.on_reply(cur, other, {"done": False})
    assert store.get_thread(cur, other["id"])["status"] == "WAITING_JR"


def test_exchange_applies_the_models_done_to_the_thread(db, cur):
    thread = interview_thread(cur)
    answer = '{"message": "That is all I need. Thanks for your time.", "done": true}'
    voice.exchange(cur, reporter, thread["id"], "ON_RECORD", {}, "He is out a month.",
                   llm=lambda prompt: answer, budget_s=5)
    after = store.get_thread(cur, thread["id"])
    assert after["status"] == "CLOSED" and after["state"]["done"] is True
    assert reporter.options(cur, after, None) == []


def test_hard_cap_is_ten_questions(cur):
    assert reporter.INTERVIEW_MAX_QUESTIONS == 10
    thread = interview_thread(cur, body_source="LLM", asked=10)
    thread = exchange(cur, thread, "NO_COMMENT")
    assert thread["status"] == "CLOSED" and thread["state"]["done"] is True
    thread = interview_thread(cur, body_source="LLM", asked=9, source_ref="reporter:test:2")
    thread = exchange(cur, thread, "NO_COMMENT")
    assert thread["status"] == "WAITING_JR" and thread["state"]["questions_asked"] == 10
    thread = exchange(cur, thread, "NO_COMMENT")
    assert thread["status"] == "CLOSED"


def test_end_interview_any_time(cur):
    thread = interview_thread(cur)
    thread = exchange(cur, thread, "END_INTERVIEW")
    assert thread["status"] == "CLOSED" and thread["state"]["ended"] is True
    assert store.messages(cur, thread["id"])[-1]["body"] == "Understood. Thanks for your time."


def test_on_record_and_off_record_need_a_note(cur):
    thread = interview_thread(cur)
    for option in ("ON_RECORD", "OFF_RECORD"):
        with pytest.raises(InboxError):
            exchange(cur, thread, option, None)
    with pytest.raises(InboxError):
        exchange(cur, thread, "SHOUT", "loud")


def test_no_comment_needs_no_note(cur):
    thread = interview_thread(cur)
    thread = exchange(cur, thread, "NO_COMMENT")
    jr = [m for m in store.messages(cur, thread["id"]) if m["author"] == "JR"]
    assert jr[-1]["option_id"] == "NO_COMMENT" and jr[-1]["body"] == "No comment"


def test_statements_store_answered_questions_only(cur):
    thread = interview_thread(cur)
    thread = exchange(cur, thread, "ON_RECORD", "He'll be back in a month.")
    thread = exchange(cur, thread, "OFF_RECORD", "We're looking at the waiver wire.")
    thread = exchange(cur, thread, "NO_COMMENT")
    rows = reporter.jr_statements(cur, CAR, SEASON, week=WEEK)
    assert len(rows) == 2
    assert [r["on_record"] for r in rows] == [True, False]
    assert [r["answer"] for r in rows] == ["He'll be back in a month.", "We're looking at the waiver wire."]
    assert rows[0]["writer_agent_id"] == "car_beat" and rows[0]["writer_handle"] == "@carbeat"
    assert rows[0]["actor_role"] == "GM" and rows[0]["actor_name"] == "Dan Morgan"
    assert rows[0]["question"].startswith("Bryce Young is out")
    assert all(r["subject"] == "Injury update" for r in rows)


def test_start_needs_a_beat_writer(cur):
    node = {"team_id": 2, "abbr": "ATL", "season": SEASON, "week": WEEK, "actors": [], "actor": None}
    with pytest.raises(InboxError):
        reporter.start(cur, node, {"topic": "The season"})
