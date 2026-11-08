"""server/inbox_api.py: every §2.12 endpoint on patched data (FRONT_OFFICE_INBOX_PLAN.md U5).

Offline: the routes run against the shared in-memory SQLite fake (tests/test_inbox_store.py) through a patched
`_connect`; the model is off (voice.default_llm -> None), so replies and opening messages are templates. The tests
check the exact JSON contract of Mu_Network/src/types/franchiseInbox.ts: Actor, Thread (with actor/counterpart and
no state), Message (no facts, no surplus, appraisal {outcome, reason} only), options and drafts.
"""
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engines.inbox import actors, scan, store, voice
from server import inbox_api
from tests.test_inbox_store import InboxDB, thread_row


class ApiConn:
    """A psycopg-shaped connection over the shared SQLite dbt; the route owns commit/rollback/close."""

    def __init__(self, db):
        self.db = db

    def cursor(self):
        return self.db.cursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    # TestClient runs sync endpoints in a worker thread; the fake's in-memory connection must accept that.
    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: real_connect(*a, **dict(k, check_same_thread=False)))
    d = InboxDB()
    monkeypatch.setattr(actors, "user_teams", lambda: [t.upper() for t in d.user_teams])
    monkeypatch.setattr(voice, "default_llm", lambda: None)
    monkeypatch.setattr(scan, "_OPENED", set())            # the open scan's once-per-process cache
    monkeypatch.setattr(inbox_api, "_connect", lambda: ApiConn(d))
    return d


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(inbox_api.router)
    return TestClient(app)


def control(db, role="GM", persona_id="10"):
    actors.take_control(db.cursor(), "CAR", role, persona_id)


def make_thread(db, **kw):
    return store.create_thread(db.cursor(), thread_row(**kw))


THREAD_KEYS = {"id", "kind", "actor", "counterpart", "subject", "status", "unread", "season", "week", "actor_role",
               "actor_id", "counterpart_type", "counterpart_id", "counterpart_name", "started_by", "source_ref",
               "link_table", "link_id", "offseason_stage", "expires_season", "expires_week", "created_at",
               "updated_at"}
MESSAGE_KEYS = {"id", "seq", "author", "body", "source", "option_id", "engine_ref", "appraisal", "created_at"}


# ----------------------------------------------------------------------- actors ---

def test_actors_empty_then_control_and_release(client):
    body = client.get("/api/franchise/CAR/inbox/actors").json()
    assert body == {"controlled": [], "candidates": [
        {"role": "GM", "persona_id": "10", "name": "Dan Morgan", "also": None},
        {"role": "COACH", "persona_id": "41", "name": "Dave Canales", "also": None}]}

    r = client.post("/api/franchise/CAR/inbox/actors", json={"role": "GM", "persona_id": "10"})
    assert r.status_code == 201 and r.json() == {"role": "GM", "id": "10", "name": "Dan Morgan"}

    body = client.get("/api/franchise/CAR/inbox/actors").json()
    assert body["controlled"] == [{"role": "GM", "id": "10", "name": "Dan Morgan"}]

    r = client.delete("/api/franchise/CAR/inbox/actors/GM/10")
    assert r.status_code == 204 and r.content == b""
    assert client.get("/api/franchise/CAR/inbox/actors").json()["controlled"] == []


def test_actor_errors(client):
    assert client.post("/api/franchise/ATL/inbox/actors",
                       json={"role": "GM", "persona_id": "20"}).status_code == 403     # not a user team
    assert client.post("/api/franchise/CAR/inbox/actors",
                       json={"role": "GM", "persona_id": "20"}).status_code == 409      # Atlanta's GM
    assert client.post("/api/franchise/CAR/inbox/actors", json={"persona_id": "10"}).status_code == 400
    assert client.post("/api/franchise/CAR/inbox/actors", json={"role": "OWNER", "persona_id": "1"}).status_code == 400
    assert client.get("/api/franchise/XYZ/inbox/actors").status_code == 404             # no such team
    assert client.get("/api/franchise/ATL/inbox").status_code == 403


def test_coach_gm_candidate_carries_also_as_a_string(db, client):
    db.make_coach_gm()
    cands = client.get("/api/franchise/CAR/inbox/actors").json()["candidates"]
    assert cands == [{"role": "GM", "persona_id": "10", "name": "Dan Morgan", "also": "COACH"}]


# ------------------------------------------------------------------------ kinds ---

def test_kinds_are_the_ones_a_controlled_actor_can_use(db, client):
    assert client.get("/api/franchise/CAR/inbox/kinds").json() == []          # no actor, no kinds

    control(db, "GM", "10")
    kinds = {k["kind"]: k for k in client.get("/api/franchise/CAR/inbox/kinds").json()}
    assert {"GM_TALK", "CONTRACT", "OWNER", "REPORTER", "VISION"} <= set(kinds)
    assert "ROLE_TALK" not in kinds                                          # COACH only
    assert kinds["OWNER"]["can_start"] is False
    assert kinds["GM_TALK"]["can_start"] is True
    assert kinds["GM_TALK"]["actor_roles"] == ["GM"]
    assert kinds["VISION"]["actor_roles"] == ["GM", "COACH"]                # no ACTOR_ROLES -> the default
    assert all(set(k) == {"kind", "actor_roles", "can_start", "start_form"} for k in kinds.values())

    control(db, "COACH", "41")
    kinds = {k["kind"] for k in client.get("/api/franchise/CAR/inbox/kinds").json()}
    assert "ROLE_TALK" in kinds and "GM_TALK" in kinds


# ------------------------------------------------------------------------- list ---

def test_list_is_empty_with_no_actors_even_with_stored_threads(db, client):
    make_thread(db, source_ref="stored")
    body = client.get("/api/franchise/CAR/inbox").json()
    assert body == {"team": "CAR", "actors": [], "unread": 0, "threads": []}
    assert client.get("/api/franchise/CAR/inbox?as=GM").status_code == 403      # nobody controlled


def test_list_and_thread_shape(db, client):
    control(db)
    tid = make_thread(db, source_ref="list-me")
    store.mark_read(db.cursor(), tid)
    body = client.get("/api/franchise/CAR/inbox").json()
    assert body["team"] == "CAR" and body["actors"] == [{"role": "GM", "id": "10", "name": "Dan Morgan"}]
    thread = body["threads"][0]
    assert set(thread) == THREAD_KEYS and "state" not in thread and "team_id" not in thread
    assert thread["id"] == tid and thread["kind"] == "CONTRACT" and thread["subject"] == "Extension"
    assert thread["actor"] == {"role": "GM", "id": "10", "name": "Dan Morgan"}
    assert thread["counterpart"] == {"role": "player", "id": "100", "name": "Bryce Young"}
    assert thread["unread"] is False                          # a bool, never 0/1


def test_list_open_filter_includes_waiting_jr(db, client):
    control(db)
    open_tid = make_thread(db, source_ref="open")
    wait_tid = make_thread(db, source_ref="wait")
    closed_tid = make_thread(db, source_ref="closed")
    store.mark_read(db.cursor(), open_tid)
    store.add_message(db.cursor(), wait_tid, "COUNTERPART", "Talk to me.", "TEMPLATE")
    store.mark_read(db.cursor(), closed_tid)
    store.close_thread(db.cursor(), closed_tid)

    ids = [t["id"] for t in client.get("/api/franchise/CAR/inbox?status=OPEN").json()["threads"]]
    assert set(ids) == {open_tid, wait_tid}                   # Open means every open status
    ids = [t["id"] for t in client.get("/api/franchise/CAR/inbox?status=CLOSED").json()["threads"]]
    assert ids == [closed_tid]
    body = client.get("/api/franchise/CAR/inbox?status=OPEN").json()
    assert body["unread"] == 1                                 # the WAITING_JR thread


def test_list_kind_as_and_limit_filters(db, client):
    control(db, "GM", "10")
    control(db, "COACH", "41")
    g = make_thread(db, source_ref="gm", kind="CONTRACT")
    c = make_thread(db, source_ref="coach", kind="ROLE_TALK", actor_role="COACH", actor_id="41")
    assert [t["id"] for t in client.get("/api/franchise/CAR/inbox?kind=ROLE_TALK").json()["threads"]] == [c]
    assert [t["id"] for t in client.get("/api/franchise/CAR/inbox?as=COACH").json()["threads"]] == [c]
    assert [t["id"] for t in client.get("/api/franchise/CAR/inbox?as=GM").json()["threads"]] == [g]
    assert len(client.get("/api/franchise/CAR/inbox?limit=1").json()["threads"]) == 1
    assert client.get("/api/franchise/CAR/inbox?as=PLAYER").status_code == 403     # not a controlled role


# ----------------------------------------------------------------------- detail ---

def test_detail_marks_read_and_strips_facts_surplus_and_trust(db, client):
    control(db)
    tid = make_thread(db, source_ref="detail")
    store.add_message(db.cursor(), tid, "COUNTERPART", "We want 3 years.", "LLM",
                      options=[{"id": "MAKE_OFFER", "label": "Make an offer", "form": "contract_terms",
                                "needs_note": False, "default": True}],
                      facts={"secret": "never sent"},
                      engine_ref={"offer_id": 7, "surplus": 999, "turns": [{"surplus": 1, "action": "COUNTER"}]},
                      appraisal={"outcome": "positive", "significance": "meaningful", "mistreated": False,
                                 "reason": "a fair, direct offer"})
    assert store.get_thread(db.cursor(), tid)["unread"]

    body = client.get(f"/api/franchise/CAR/inbox/{tid}").json()
    assert set(body) == {"thread", "messages", "options"}
    assert set(body["thread"]) == THREAD_KEYS and body["thread"]["unread"] is False
    assert not store.get_thread(db.cursor(), tid)["unread"]                  # marked read

    (message,) = body["messages"]
    assert set(message) == MESSAGE_KEYS and "facts" not in message
    assert message["source"] == "LLM" and message["seq"] == 1
    assert message["engine_ref"] == {"offer_id": 7, "turns": [{"action": "COUNTER"}]}     # no surplus, ever
    assert message["appraisal"] == {"outcome": "positive", "reason": "a fair, direct offer"}   # no significance
    assert body["options"] == [{"id": "MAKE_OFFER", "label": "Make an offer", "form": "contract_terms",
                                "needs_note": False, "default": True}]


def test_detail_404_for_a_missing_or_another_teams_thread(db, client):
    control(db)
    other = make_thread(db, team_id=2, source_ref="atl")
    assert client.get(f"/api/franchise/CAR/inbox/{other}").status_code == 404
    assert client.get("/api/franchise/CAR/inbox/99999").status_code == 404


# ------------------------------------------------------------------------- draft ---

def test_draft_returns_a_draft_and_acts_on_nothing(db, client):
    control(db)
    tid = make_thread(db, source_ref="draft")
    before = (store.messages(db.cursor(), tid), store.get_thread(db.cursor(), tid))

    body = client.post(f"/api/franchise/CAR/inbox/{tid}/draft",
                       json={"text": "3 years, 40 million, 15 guaranteed"}).json()
    assert body == {"draft": {"option_id": "MAKE_OFFER",
                              "payload": {"years": 3, "total_value": 40_000_000, "bonus": 0,
                                          "guaranteed": 15_000_000},
                              "summary": "Offer 3 years, $40.0M total, $15.0M guaranteed, $0 bonus"}}
    after = (store.messages(db.cursor(), tid), store.get_thread(db.cursor(), tid))
    assert after == before                                                    # no message, no state change


def test_draft_is_null_for_a_kind_without_one_and_409_when_closed(db, client):
    control(db)
    owner = make_thread(db, source_ref="owner", kind="OWNER", counterpart_type="owner", counterpart_id="owner:CAR",
                        counterpart_name="The Owner")
    assert client.post(f"/api/franchise/CAR/inbox/{owner}/draft", json={"text": "40 million"}).json() == {"draft": None}

    closed = make_thread(db, source_ref="closed-draft")
    store.close_thread(db.cursor(), closed)
    assert client.post(f"/api/franchise/CAR/inbox/{closed}/draft", json={"text": "3 years, 40 million"}).status_code == 409
    assert client.post("/api/franchise/CAR/inbox/99999/draft", json={"text": "hi"}).status_code == 404


# ------------------------------------------------------------------------- reply ---

def test_reply_drives_exchange_and_closes(db, client):
    control(db)
    tid = make_thread(db, source_ref="reply", kind="OWNER", counterpart_type="owner",
                      counterpart_id="owner:CAR", counterpart_name="The Owner", subject="Season expectations")
    body = client.post(f"/api/franchise/CAR/inbox/{tid}/reply",
                       json={"option_id": "ACKNOWLEDGE", "payload": None, "note": None}).json()

    assert set(body) == {"thread", "messages", "options"}
    assert body["thread"]["status"] == "CLOSED" and body["options"] == []
    jr, them = body["messages"]
    assert (jr["author"], jr["source"], jr["option_id"]) == ("JR", "JR", "ACKNOWLEDGE")
    assert (them["author"], them["source"]) == ("COUNTERPART", "TEMPLATE")
    assert them["body"] == "Good. Keep me posted."                 # owner.template ACKNOWLEDGED
    assert all("facts" not in m for m in body["messages"])

    # a closed conversation is read-only
    assert client.post(f"/api/franchise/CAR/inbox/{tid}/reply",
                       json={"option_id": "ACKNOWLEDGE"}).status_code == 409


def test_reply_errors(db, client):
    control(db)
    tid = make_thread(db, source_ref="reply-errors", kind="OWNER", counterpart_type="owner",
                      counterpart_id="owner:CAR", counterpart_name="The Owner")
    assert client.post(f"/api/franchise/CAR/inbox/{tid}/reply",
                       json={"option_id": "SHRUG"}).status_code == 400
    assert client.post(f"/api/franchise/CAR/inbox/{tid}/reply",
                       json={"option_id": "ACKNOWLEDGE", "note": "x" * 601}).status_code == 400
    assert client.post("/api/franchise/CAR/inbox/99999/reply",
                       json={"option_id": "ACKNOWLEDGE"}).status_code == 404

    bogus = make_thread(db, source_ref="bogus", kind="BOGUS")
    assert client.post(f"/api/franchise/CAR/inbox/{bogus}/reply",
                       json={"option_id": "X"}).status_code == 400


# ------------------------------------------------------------------------ create ---

def test_start_opens_a_conversation(db, client):
    control(db)
    r = client.post("/api/franchise/CAR/inbox",
                    json={"kind": "REPORTER", "as": "GM", "note": "Anything for me?", "topic": "The season"})
    assert r.status_code == 201
    thread = r.json()
    assert set(thread) == THREAD_KEYS and "state" not in thread
    assert (thread["kind"], thread["status"], thread["unread"]) == ("REPORTER", "WAITING_JR", True)
    assert thread["actor"] == {"role": "GM", "id": "10", "name": "Dan Morgan"}
    assert thread["counterpart"] == {"role": "writer", "id": "car_beat", "name": "Joe Person"}
    assert thread["season"] == 2026 and thread["week"] == 5


def test_start_errors(db, client):
    control(db)
    assert client.post("/api/franchise/CAR/inbox", json={"as": "GM"}).status_code == 400
    assert client.post("/api/franchise/CAR/inbox", json={"kind": "REPORTER"}).status_code == 400
    assert client.post("/api/franchise/CAR/inbox",
                       json={"kind": "BOGUS", "as": "GM"}).status_code == 400
    assert client.post("/api/franchise/CAR/inbox",
                       json={"kind": "REPORTER", "as": "COACH"}).status_code == 403      # not controlled
    assert client.post("/api/franchise/CAR/inbox",
                       json={"kind": "REPORTER", "as": "GM", "note": "x" * 601}).status_code == 400


# ------------------------------------------------------------------------- close ---

def test_close_returns_the_thread_and_a_second_close_is_409(db, client):
    control(db)
    tid = make_thread(db, source_ref="close-me")
    body = client.post(f"/api/franchise/CAR/inbox/{tid}/close").json()
    assert set(body) == THREAD_KEYS and body["status"] == "CLOSED"
    assert client.get(f"/api/franchise/CAR/inbox/{tid}").status_code == 200     # stays readable
    assert client.post(f"/api/franchise/CAR/inbox/{tid}/close").status_code == 409
    assert client.post("/api/franchise/CAR/inbox/99999/close").status_code == 404


# -------------------------------------------------------------------- no migration ---

def test_without_the_migration_reads_are_empty_and_writes_are_409(monkeypatch):
    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: real_connect(*a, **dict(k, check_same_thread=False)))
    d = InboxDB(inbox=False)
    monkeypatch.setattr(actors, "user_teams", lambda: ["CAR"])
    monkeypatch.setattr(inbox_api, "_connect", lambda: ApiConn(d))
    app = FastAPI()
    app.include_router(inbox_api.router)
    client = TestClient(app)

    assert client.get("/api/franchise/CAR/inbox").json() == {
        "team": "CAR", "actors": [], "unread": 0, "threads": []}
    assert client.post("/api/franchise/CAR/inbox/actors",
                       json={"role": "GM", "persona_id": "10"}).status_code == 409
    assert client.post("/api/franchise/CAR/inbox",
                       json={"kind": "REPORTER", "as": "GM"}).status_code == 409
