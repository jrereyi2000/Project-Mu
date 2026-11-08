"""server/press_api.py: the eight podium endpoints on a bare FastAPI app with patched lifecycle and fake data.

No live database: the DB cursor answers the two reads the GET makes, every press_room lifecycle function is
patched, and the shapes are checked against Mu_Network/src/types/press.ts (§2.11).
"""
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from newsroom import press_room
from server import press_api

SEASON, WEEK = 2036, 5
CAR, ATL = 1, 2

CONF = {"id": 7, "season": SEASON, "week": WEEK, "game_id": 100, "team": "CAR", "opponent": "ATL",
        "result": "W", "score": "24-17", "subject_name": "Marcus Whitfield", "controller": "USER",
        "status": "SCHEDULED", "end_reason": None, "tier": "BIG", "attendees": [], "turns": []}
LIVE_CONF = dict(CONF, status="LIVE")
REVIEW_CONF = dict(CONF, status="REVIEW")
CLOSED_CONF = dict(CONF, status="CLOSED")
ROW = {"id": 7, "team_id": CAR, "controller": "USER", "season": SEASON, "week": WEEK, "status": "SCHEDULED"}
HANDS = [{"agent_id": "beat_car", "display_name": "Joe Beat", "handle": "@beat_car", "role": "BEAT",
          "pool_tier": None, "picked_so_far": 0, "follow_up": False}]
QUESTION = {"id": 11, "seq": 2, "speaker": "REPORTER", "kind": "QUESTION",
            "reporter": {"agent_id": "beat_car", "display_name": "Joe Beat", "handle": "@beat_car",
                         "role": "BEAT", "pool_tier": None},
            "text": "Coach, what changed?", "reply_to": None, "source": "AGENT", "issues": [], "on_record": True}
ANSWER = {"id": 12, "seq": 3, "speaker": "SUBJECT", "kind": "ANSWER", "reporter": None,
          "text": "We played hard.", "reply_to": 11, "source": "USER", "issues": [], "on_record": True}


class FakeCursor:
    def __init__(self):
        self._row = None
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "api_last_game" in sql:
            self._row = (100, CAR, ATL)
        elif "api_team_game_conference" in sql:
            self._row = (7,) if params[1] == CAR else (8, "SCHEDULED")
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self):
        self.cursors = []
        self.commits = 0

    def cursor(self):
        cur = FakeCursor()
        self.cursors.append(cur)
        return cur

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def team_row(cur, abbr):
    clean = press_api.ABBR_ALIASES.get((abbr or "").upper(), (abbr or "").upper())
    if clean == "CAR":
        return {"id": CAR, "abbr": "CAR", "name": "Panthers"}
    if clean == "ATL":
        return {"id": ATL, "abbr": "ATL", "name": "Falcons"}
    raise press_api.HTTPException(404, f"Team '{abbr}' not found")


def conference_row(cur, cid):
    return ROW if cid == 7 else None


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.conn = FakeConn()
        self.row = dict(ROW)
        self.conf = dict(CONF)
        self.patchers = [
            mock.patch.object(press_api, "_connect", lambda: self.conn),
            mock.patch.object(press_api, "team_row", team_row),
            mock.patch.object(press_api, "league_state", lambda cur: (SEASON, WEEK)),
            mock.patch.object(press_api, "user_teams", lambda: ["CAR"]),
            mock.patch.object(press_api.press_room, "get_conference_row", lambda cur, cid: self.row if cid == 7 else None),
            mock.patch.object(press_api.press_room, "build_conference", lambda cur, cid, include_hands=None: self.conf),
        ]
        for patcher in self.patchers:
            patcher.start()
        app = FastAPI()
        app.include_router(press_api.router)
        self.client = TestClient(app)

    def tearDown(self):
        for patcher in self.patchers:
            patcher.stop()

    def status(self, method, path, **kwargs):
        return getattr(self.client, method)(path, **kwargs)


class GetPressTest(ApiTest):
    def test_get_returns_conference_and_opponent(self):
        body = self.status("get", "/api/franchise/CAR/press").json()
        self.assertEqual(set(body), {"conference", "opponent_presser"})
        self.assertEqual(body["conference"]["id"], 7)
        self.assertEqual(body["opponent_presser"], {"conference_id": 8, "status": "SCHEDULED"})

    def test_unknown_team_is_404(self):
        response = self.status("get", "/api/franchise/XXX/press")
        self.assertEqual(response.status_code, 404)


class LifecycleTest(ApiTest):
    def test_start_returns_conference_and_hands(self):
        with mock.patch.object(press_api.press_room, "start_user",
                               lambda cur, cid, opening=None: {"conference": self.conf, "hands": HANDS}):
            response = self.status("post", "/api/franchise/CAR/press/7/start", json={"opening": "Thanks"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"conference", "hands"})
        self.assertEqual(body["hands"][0]["agent_id"], "beat_car")

    def test_start_is_403_for_ai_team(self):
        def forbidden(cur, cid, opening=None):
            raise press_room.PressForbidden("This press conference belongs to an AI coach.")

        self.row = dict(ROW, controller="AGENT")
        with mock.patch.object(press_api.press_room, "start_user", forbidden):
            response = self.status("post", "/api/franchise/CAR/press/7/start", json={})
        self.assertEqual(response.status_code, 403)

    def test_start_is_409_in_wrong_state(self):
        def conflict(cur, cid, opening=None):
            raise press_room.PressConflict("Press conference 7 is LIVE; expected SCHEDULED.")

        with mock.patch.object(press_api.press_room, "start_user", conflict):
            response = self.status("post", "/api/franchise/CAR/press/7/start", json={})
        self.assertEqual(response.status_code, 409)

    def test_pick_refuses_unraised_hand(self):
        def bad(cur, cid, agent_id):
            raise press_room.PressBadRequest("That reporter does not have a hand up.")

        with mock.patch.object(press_api.press_room, "pick_user", bad):
            response = self.status("post", "/api/franchise/CAR/press/7/pick", json={"agent_id": "nobody"})
        self.assertEqual(response.status_code, 400)

    def test_pick_returns_question(self):
        with mock.patch.object(press_api.press_room, "pick_user", lambda cur, cid, agent_id: QUESTION):
            body = self.status("post", "/api/franchise/CAR/press/7/pick", json={"agent_id": "beat_car"}).json()
        self.assertEqual(set(body), {"question"})
        self.assertEqual(body["question"]["kind"], "QUESTION")
        self.assertIsNotNone(body["question"]["reporter"])

    def test_answer_returns_next_hands(self):
        payload = {"answer": ANSWER, "hands": HANDS, "ended": False, "end_reason": None}
        with mock.patch.object(press_api.press_room, "answer_user",
                               lambda cur, cid, tid, text, no_comment: payload):
            body = self.status("post", "/api/franchise/CAR/press/7/answer",
                               json={"turn_id": 11, "answer": "We played hard."}).json()
        self.assertEqual(set(body), {"answer", "hands", "ended", "end_reason"})
        self.assertEqual(body["hands"][0]["agent_id"], "beat_car")

    def test_answer_requires_exactly_one_of_answer_or_no_comment(self):
        response = self.status("post", "/api/franchise/CAR/press/7/answer", json={"turn_id": 11})
        self.assertEqual(response.status_code, 400)
        response = self.status("post", "/api/franchise/CAR/press/7/answer",
                               json={"turn_id": 11, "answer": "x", "no_comment": True})
        self.assertEqual(response.status_code, 400)

    def test_end_returns_review_conference(self):
        self.conf = dict(REVIEW_CONF)
        with mock.patch.object(press_api.press_room, "end_user", lambda cur, cid: self.conf):
            body = self.status("post", "/api/franchise/CAR/press/7/end", json={}).json()
        self.assertEqual(body["status"], "REVIEW")

    def test_edit_turn(self):
        with mock.patch.object(press_api.press_room, "edit_user",
                               lambda cur, cid, tid, text=None, on_record=None: ANSWER):
            body = self.status("put", "/api/franchise/CAR/press/7/turns/12",
                               json={"answer": "We played hard.", "on_record": True}).json()
        self.assertEqual(body["id"], 12)
        self.assertEqual(body["speaker"], "SUBJECT")

    def test_approve_reports_effects_and_wakeup(self):
        self.conf = dict(CLOSED_CONF)
        with mock.patch.object(press_api.press_room, "approve", lambda cur, cid: self.conf), \
                mock.patch.object(press_api, "_start_close_and_wakeup", lambda cid, team, s, w: (True, True)):
            response = self.status("post", "/api/franchise/CAR/press/7/approve", json={})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"conference", "effects_started", "wakeup_started"})
        self.assertEqual((body["effects_started"], body["wakeup_started"]), (True, True))

    def test_history_returns_closed_conferences(self):
        with mock.patch.object(press_api.press_room, "history_ids", lambda cur, s, w: [7]):
            body = self.status("get", "/api/franchise/CAR/press/history?season=2036&week=5").json()
        self.assertEqual(set(body), {"conferences"})
        self.assertEqual(body["conferences"][0]["id"], 7)

    def test_conference_must_belong_to_the_team(self):
        self.row = dict(ROW, team_id=ATL)
        response = self.status("post", "/api/franchise/CAR/press/7/start", json={})
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
