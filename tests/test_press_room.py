"""newsroom/press_room.py: the session engine, tables, runner, close effects and readers, offline.

A fake cursor dispatches on each statement's ``/* q:<name> */`` tag over in-memory tables, and a scripted model
answers reporter/coach/pick/judgment prompts without any network. Nothing touches a live database.
"""
import io
import json
import re
import threading
import unittest
from contextlib import redirect_stdout
from unittest import mock

from psycopg.types.json import Jsonb

from newsroom import press_agents, press_room
from personality import relationship_events as rev

SEASON, WEEK = 2036, 5
CAR, ATL = 1, 2
COACH_ID, OPP_COACH_ID = 10, 20
UNIVERSE = "universe:test"


def plain(value):
    return value.obj if isinstance(value, Jsonb) else value


def facts():
    return {
        "game": {"id": 100, "season": SEASON, "week": WEEK, "game_type": "REGULAR_SEASON",
                 "final": {"home": 24, "away": 17}},
        "teams": [
            {"team_id": CAR, "name": "Panthers", "abbr": "CAR", "home": True, "record_after": "3-2",
             "coach": {"id": COACH_ID, "name": "Marcus Whitfield", "tenure": 2, "schemes": {},
                       "tendencies": {}, "hot_seat": None}},
            {"team_id": ATL, "name": "Falcons", "abbr": "ATL", "home": False, "record_after": "2-3",
             "coach": {"id": OPP_COACH_ID, "name": "Terry Falcon", "tenure": 1, "schemes": {},
                       "tendencies": {}, "hot_seat": None}},
        ],
        "names": ["Marcus Whitfield", "Terry Falcon"],
        "numbers": {},
        "league": {},
        "box": "",
        "script": {},
    }


class FakeDB:
    def __init__(self):
        self.lock = threading.RLock()
        self.conferences = {}
        self.attendees = []
        self.hands = []
        self.turns = {}
        self.memories = []
        self.teams = {CAR: {"id": CAR, "abbreviation": "CAR", "name": "Panthers", "city": "Carolina"},
                      ATL: {"id": ATL, "abbreviation": "ATL", "name": "Falcons", "city": "Atlanta"}}
        self.games = {100: {"id": 100, "season": SEASON, "week": WEEK, "status": "COMPLETED",
                            "home_team_id": CAR, "away_team_id": ATL, "home_score": 24, "away_score": 17}}
        self.league = (SEASON, WEEK)
        self.auto = 1000

    def next_id(self):
        with self.lock:
            self.auto += 1
            return self.auto

    def add_conference(self, **overrides):
        with self.lock:
            cid = overrides.pop("id", None) or self.next_id()
            row = {"id": cid, "season": SEASON, "week": WEEK, "game_id": 100, "team_id": CAR,
                   "subject_type": "coach", "subject_id": COACH_ID, "subject_name": "Marcus Whitfield",
                   "controller": "AGENT", "status": "SCHEDULED", "end_reason": None, "interest": 70.0,
                   "tier": "BIG", "cast_inputs": {}, "facts": facts(), "patience": None, "model": "fake",
                   "calls": 0, "input_tokens": 0, "output_tokens": 0, "effects_applied": False,
                   "created_at": None, "started_at": None, "closed_at": None}
            row.update(overrides)
            self.conferences[cid] = row
            return cid

    def add_attendee(self, conference_id, agent_id, person_key=None, role="BEAT", tier=None,
                     display_name=None, handle=None):
        with self.lock:
            self.attendees.append({"conference_id": conference_id, "agent_id": agent_id,
                                   "person_key": person_key or agent_id, "role": role, "pool_tier": tier,
                                   "questions": 0, "passes": 0, "done": False, "hands": 0, "picked": 0,
                                   "judgment": None, "display_name": display_name or agent_id,
                                   "handle": handle or ("@" + agent_id)})

    def add_turn(self, conference_id, speaker, kind, text, agent_id=None, reply_to=None, about=None,
                 tone=None, source="AGENT", issues=None, on_record=True):
        with self.lock:
            seq = max([t["seq"] for t in self.turns.values() if t["conference_id"] == conference_id] or [0]) + 1
            turn_id = self.next_id()
            self.turns[turn_id] = {
                "id": turn_id, "conference_id": conference_id, "seq": seq, "speaker": speaker,
                "agent_id": agent_id, "kind": kind, "reply_to": reply_to, "text": text,
                "about": about or [], "tone": tone, "source": source, "issues": issues or [],
                "on_record": on_record,
            }
            return turn_id

    def attendee(self, conference_id, agent_id):
        for row in self.attendees:
            if row["conference_id"] == conference_id and row["agent_id"] == agent_id:
                return row
        return None


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self._row = None
        self._rows = []

    # -- helpers -------------------------------------------------------------------------------------
    def _conference(self, cid):
        row = self.db.conferences.get(cid)
        if row is None:
            return None
        team = self.db.teams.get(row["team_id"], {})
        game = self.db.games.get(row["game_id"], {})
        return [
            row["id"], row["season"], row["week"], row["game_id"], row["team_id"], row["subject_type"],
            row["subject_id"], row["subject_name"], row["controller"], row["status"], row["end_reason"],
            row["interest"], row["tier"], row["cast_inputs"], row["facts"], row["patience"], row["model"],
            row["calls"], row["input_tokens"], row["output_tokens"], row["effects_applied"],
            row["created_at"], row["started_at"], row["closed_at"], team.get("abbreviation"),
            team.get("name"), game.get("home_team_id"), game.get("away_team_id"), game.get("home_score"),
            game.get("away_score"), game.get("game_type", "REGULAR_SEASON"),
        ]

    def _attendee_rows(self, cid):
        out = []
        for row in self.db.attendees:
            if row["conference_id"] != cid:
                continue
            out.append([row["agent_id"], row["person_key"], row["role"], row["pool_tier"], row["questions"],
                        row["passes"], row["done"], row["hands"], row["picked"], row["judgment"],
                        row["display_name"], row["handle"]])
        return sorted(out, key=lambda r: (0 if r[2] == "BEAT" else 1, r[0]))

    def _turn_rows(self, cid):
        out = []
        for turn in sorted(self.db.turns.values(), key=lambda t: (t["seq"], t["id"])):
            if turn["conference_id"] != cid:
                continue
            att = self.db.attendee(cid, turn["agent_id"]) or {}
            out.append([turn["id"], turn["seq"], turn["speaker"], turn["agent_id"], turn["kind"],
                        turn["reply_to"], turn["text"], turn["about"], turn["tone"], turn["source"],
                        turn["issues"], turn["on_record"], att.get("display_name"),
                        att.get("handle"), att.get("role"), att.get("pool_tier")])
        return out

    # -- execute -------------------------------------------------------------------------------------
    def execute(self, sql, params=None):
        params = tuple(plain(p) for p in (params or ()))
        tag_match = re.search(r"/\* q:(\w+) \*/", sql)
        tag = tag_match.group(1) if tag_match else None
        with self.db.lock:
            self._row, self._rows = self._dispatch(tag, sql, params)

    def _dispatch(self, tag, sql, params):
        db = self.db
        if tag == "ready":
            return (True,), []
        if tag == "regclass":
            return (True,), []
        if tag == "league":
            return db.league, []
        if tag == "team_by_id":
            team = db.teams.get(params[0])
            return ((team["abbreviation"],) if team else None), []
        if tag == "team_abbr":
            for team in db.teams.values():
                if team["abbreviation"] in params:
                    return (team["id"], team["abbreviation"], team["name"], team["city"]), []
            return None, []
        if tag == "teams":
            return None, [(t["id"], t["abbreviation"], t["name"]) for t in db.teams.values()]
        if tag == "conference":
            return self._conference(params[0]), []
        if tag == "attendees":
            return None, self._attendee_rows(params[0])
        if tag == "turns":
            return None, self._turn_rows(params[0])
        if tag == "hand_rounds":
            return None, [[h["agent_id"], h["round"], h["picked"]] for h in db.hands
                          if h["conference_id"] == params[0]]
        if tag == "existing_conference":
            game_id, team_id, subject_type, subject_id = params
            for row in db.conferences.values():
                if (row["game_id"], row["team_id"], row["subject_type"]) == (game_id, team_id, subject_type) \
                        and row["subject_id"] == subject_id:
                    return (row["id"],), []
            return None, []
        if tag == "insert_conference":
            cid = db.add_conference(
                season=params[0], week=params[1], game_id=params[2], team_id=params[3], subject_type=params[4],
                subject_id=params[5], subject_name=params[6], controller=params[7], interest=params[8],
                tier=params[9], cast_inputs=params[10], facts=params[11], model=params[12])
            return (cid,), []
        if tag == "insert_attendee":
            db.add_attendee(params[0], params[1], person_key=params[2], role=params[3], tier=params[4])
            return None, []
        if tag == "insert_turn":
            cid, seq, speaker, agent_id, kind, reply_to, text, about, tone, source, issues, retries, on_record = params
            turn_id = db.next_id()
            db.turns[turn_id] = {"id": turn_id, "conference_id": cid, "seq": seq, "speaker": speaker,
                                 "agent_id": agent_id, "kind": kind, "reply_to": reply_to, "text": text,
                                 "about": about, "tone": tone, "source": source, "issues": issues,
                                 "on_record": on_record}
            return (turn_id,), []
        if tag in ("insert_hand", "pick_hand"):
            cid, round_no, agent_id = params[0], params[1], params[2]
            picked = tag == "pick_hand"
            source = params[3] if picked else None
            for hand in db.hands:
                if (hand["conference_id"], hand["round"], hand["agent_id"]) == (cid, round_no, agent_id):
                    hand.update(picked=picked, pick_source=source)
                    return None, []
            db.hands.append({"conference_id": cid, "round": round_no, "agent_id": agent_id,
                             "picked": picked, "pick_source": source})
            return None, []
        if tag == "save_patience":
            db.conferences[params[1]]["patience"] = params[0]
            return None, []
        if tag == "save_status":
            status, end_reason, started, closed, cid = params
            row = db.conferences[cid]
            row["status"] = status
            row["end_reason"] = end_reason
            if started and row["started_at"] is None:
                row["started_at"] = "started"
            if closed:
                row["closed_at"] = "closed"
            return None, []
        if tag == "save_counters":
            calls, inp, out, cid = params
            row = db.conferences[cid]
            row["calls"] += calls
            row["input_tokens"] += inp
            row["output_tokens"] += out
            return None, []
        if tag == "save_attendee":
            questions, passes, done, hands, picked, cid, agent_id = params
            row = db.attendee(cid, agent_id)
            if row:
                row.update(questions=questions, passes=passes, done=done, hands=hands, picked=picked)
            return None, []
        if tag == "save_judgment":
            judgment, cid, agent_id = params
            row = db.attendee(cid, agent_id)
            if row:
                row["judgment"] = judgment
            return None, []
        if tag == "save_effects":
            db.conferences[params[0]]["effects_applied"] = True
            return None, []
        if tag == "update_turn":
            text, on_record, issues, turn_id, cid = params
            turn = db.turns.get(turn_id)
            if turn and turn["conference_id"] == cid:
                turn.update(text=text, on_record=on_record, issues=issues)
            return None, []
        if tag == "mark_failed":
            row = db.conferences.get(params[0])
            if row and row["status"] != "CLOSED":
                row["status"] = "FAILED"
            return None, []
        if tag == "week_conferences":
            season, week = params[0], params[1]
            team_ids = params[2] if len(params) > 2 else None
            rows = [[r["id"], r["status"], r["controller"], r["team_id"]] for r in db.conferences.values()
                    if r["season"] == season and r["week"] == week
                    and (not team_ids or r["team_id"] in team_ids)]
            return None, rows
        if tag == "week_totals":
            season, week = params
            rows = [r for r in db.conferences.values() if r["season"] == season and r["week"] == week]
            return (sum(r["calls"] for r in rows), sum(r["input_tokens"] for r in rows),
                    sum(r["output_tokens"] for r in rows)), []
        if tag == "team_game":
            season, week, team_a, team_b = params
            for game in db.games.values():
                if game["season"] == season and game["week"] == week and game["status"] == "COMPLETED" \
                        and team_a in (game["home_team_id"], game["away_team_id"]):
                    return (game["id"],), []
            return None, []
        if tag == "team_game_conference":
            game_id, team_id = params
            for row in db.conferences.values():
                if row["game_id"] == game_id and row["team_id"] == team_id:
                    return (row["id"], row["status"]), []
            return None, []
        if tag == "launch_conferences":
            team_id, season = params
            return None, [[r["id"], r["status"]] for r in db.conferences.values()
                          if r["team_id"] == team_id and r["season"] == season
                          and r["status"] in ("SCHEDULED", "LIVE", "REVIEW")]
        if tag == "expire_conferences":
            season, week = params
            return None, [[r["id"], r["status"]] for r in db.conferences.values()
                          if r["season"] == season and r["week"] < week and r["controller"] == "USER"
                          and r["status"] in ("SCHEDULED", "LIVE", "REVIEW")]
        if tag == "effects_pending":
            season, week = params
            return None, [[r["id"]] for r in db.conferences.values()
                          if r["season"] == season and r["week"] <= week
                          and r["status"] in ("CLOSED", "DECLINED") and not r["effects_applied"]]
        if tag == "waiting_events":
            seen = sorted({a["conference_id"] for a in db.attendees
                           if isinstance(a.get("judgment"), dict) and not a["judgment"].get("appended")})
            return None, [[cid] for cid in seen]
        if tag == "waiting_judgments":
            return None, [[a["agent_id"], a["judgment"]] for a in db.attendees
                          if a["conference_id"] == params[0] and isinstance(a.get("judgment"), dict)]
        if tag == "approved_quotes":
            season, week = params[0], params[1]
            team_id = params[2] if len(params) > 2 else None
            out = []
            for row in db.conferences.values():
                if row["status"] != "CLOSED" or row["season"] != season or row["week"] != week:
                    continue
                if team_id is not None and row["team_id"] != team_id:
                    continue
                for turn in sorted(db.turns.values(), key=lambda t: t["seq"]):
                    if turn["conference_id"] != row["id"] or turn["speaker"] != "SUBJECT":
                        continue
                    if turn["kind"] != "ANSWER" or not turn["on_record"]:
                        continue
                    question = db.turns.get(turn["reply_to"])
                    if not question or question["speaker"] != "REPORTER":
                        continue
                    out.append([row["id"], row["game_id"], row["team_id"], row["subject_type"],
                                row["subject_id"], row["subject_name"], row["controller"], row["week"],
                                question["agent_id"], question["text"], turn["text"], turn["issues"]])
            return None, out
        if tag == "team_conference_latest":
            team_id, season, week = params[0], params[1], params[2]
            statuses = set(params[3:])
            matches = [r for r in db.conferences.values() if r["team_id"] == team_id
                       and r["season"] == season and r["week"] <= week and r["status"] in statuses]
            matches.sort(key=lambda r: (r["week"], r["id"]), reverse=True)
            return (matches[0]["id"],) if matches else None, []
        if tag == "team_conference_settled":
            team_id, season, week = params[0], params[1], params[2]
            matches = [r for r in db.conferences.values() if r["team_id"] == team_id
                       and r["season"] == season and r["week"] < week
                       and r["status"] in ("CLOSED", "DECLINED")]
            matches.sort(key=lambda r: (r["week"], r["id"]), reverse=True)
            return ((matches[0]["id"], matches[0]["status"]) if matches else None), []
        if tag == "history":
            season, week = params
            return None, [[r["id"]] for r in sorted(db.conferences.values(), key=lambda r: (r["game_id"], r["team_id"]))
                          if r["status"] == "CLOSED" and r["season"] == season and r["week"] == week]
        if tag == "press_memory":
            agent_id, season, week, team_id = params
            for row in db.memories:
                if (row["agent_id"], row["season"], row["week"], row["team_id"]) == (agent_id, season, week, team_id):
                    return (1,), []
            return None, []
        if tag == "insert_memory":
            agent_id, season, week, text, team_id, player_id = params
            db.memories.append({"agent_id": agent_id, "season": season, "week": week, "text": text,
                                "team_id": team_id, "player_id": player_id, "kind": "PRESS"})
            return None, []
        if tag == "tool_team":
            return (CAR, "CAR", "Panthers", "Carolina"), []
        if tag == "tool_conferences":
            season = params[1]
            return None, [[r["id"], r["status"]] for r in sorted(db.conferences.values(), key=lambda r: r["id"])
                          if r["season"] == season and r["status"] in ("CLOSED", "DECLINED")]
        if "to_regclass('press_conferences')" in sql:  # press_agents.skip_history's guard (untagged)
            return (True,), []
        if "FROM press_hands ph" in sql:              # press_agents.skip_history (untagged by design)
            person_key, season, coach_id = params
            hands = [h for h in db.hands
                     if (db.attendee(h["conference_id"], h["agent_id"]) or {}).get("person_key") == person_key
                     and db.conferences[h["conference_id"]]["season"] == season
                     and db.conferences[h["conference_id"]]["subject_id"] == coach_id]
            return (len(hands), sum(1 for h in hands if h["picked"]),
                    len({h["conference_id"] for h in hands})), []
        return None, []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, db):
        self.db = db
        self.commits = 0

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


QUESTION_WORDS = ["the first drive", "the pass rush", "the run game", "the coverage", "the turnovers",
                  "the red zone", "the line", "the tempo", "the finish", "the plan"]
COACH_ANSWER = {"text": "We'll look at the tape and credit the players.", "mood": 3, "wants_to_end": False}


class ScriptedModel:
    """Marker-driven fake model. Counts calls and can serve bad reporter/coach lines."""

    def __init__(self, passes_after=None, bad_question=False, bad_coach=False, judgment=None,
                 wants_to_end=False):
        self.calls = []
        self.passes_after = passes_after
        self.bad_question = bad_question
        self.bad_coach = bad_coach
        self.judgment = judgment
        self.wants_to_end = wants_to_end
        self.reporter_calls = 0
        self.bad_reporter_calls = 0
        self.coach_calls = 0
        self._lock = threading.Lock()

    def __call__(self, prompt):
        with self._lock:
            return self._reply(prompt)

    def _reply(self, prompt):
        self.calls.append(prompt)
        if "YOUR TASK: judge how the coach treated you" in prompt:
            if self.judgment is not None:
                return json.dumps(self.judgment)
            if "did not take questions" in prompt:
                return json.dumps({"exchanges": [], "treatment": {
                    "treatment_kind": "dismissal", "outcome": "negative", "significance": "meaningful",
                    "reason": "He never showed up."}, "memory": "He stood us up."})
            ids = [int(x) for x in re.findall(r"\[turn (\d+)\] You asked", prompt)]
            return json.dumps({"exchanges": [
                {"turn_id": tid, "relationship_kind": "collaboration", "outcome": "positive",
                 "significance": "routine", "reason": "He answered directly."} for tid in ids],
                "treatment": None, "memory": "He gave me a straight answer."})
        if "YOUR TASK: Give your opening statement" in prompt:
            return json.dumps({"text": "Credit the players; we'll look at the tape.", "mood": 3,
                               "wants_to_end": False})
        if "THE RAISED HANDS" in prompt:
            ids = re.findall(r"\[agent id: ([^\]]+)\]", prompt)
            return json.dumps({"agent_id": ids[0] if ids else "unknown", "reason": "The beat goes first."})
        if "YOUR TASK: Answer the last question" in prompt:
            self.coach_calls += 1
            if self.bad_coach:
                return json.dumps({"text": "Jake Delhomme is our starter.", "mood": 3, "wants_to_end": False})
            reply = dict(COACH_ANSWER)
            reply["wants_to_end"] = self.wants_to_end
            return json.dumps(reply)
        if "YOUR TASK: Close the press conference" in prompt:
            return json.dumps({"text": "That's all, thanks.", "mood": 3, "wants_to_end": True})
        if "RULES FOR YOUR QUESTION" in prompt:
            self.reporter_calls += 1
            if self.bad_question:
                self.bad_reporter_calls += 1
                return json.dumps({"action": "ASK", "text": "Coach, is Jake Delhomme starting?",
                                   "about": [], "tone": "neutral", "reply_to": None})
            if self.passes_after is not None and self.reporter_calls > self.passes_after:
                return json.dumps({"action": "PASS", "text": "", "about": [], "tone": "neutral",
                                   "reply_to": None})
            word = QUESTION_WORDS[self.reporter_calls % len(QUESTION_WORDS)]
            suffix = "abcdefghijklmnopqrstuvwxyz"[self.reporter_calls % 26]
            return json.dumps({"action": "ASK", "text": f"Coach, what did you make of {word} {suffix}?",
                               "about": [{"type": "team", "id": CAR}], "tone": "neutral", "reply_to": None})
        return "{}"


class RoomTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.patches = [
            mock.patch.object(press_room, "_connect", lambda conn_info: FakeConn(self.db)),
            mock.patch.object(press_room, "_universe_id", lambda cur: UNIVERSE),
            mock.patch.object(press_room, "_user_teams", lambda: ["CAR"]),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

    def cursor(self):
        return FakeCursor(self.db)

    def conference(self, attendees=("beat_car", "nat_1"), **overrides):
        cid = self.db.add_conference(**overrides)
        for agent_id in attendees:
            self.db.add_attendee(cid, agent_id, person_key="pk_" + agent_id,
                                 role="BEAT" if agent_id.startswith("beat") else "NATIONAL",
                                 tier=None if agent_id.startswith("beat") else "INSIDER",
                                 display_name=agent_id.replace("_", " ").title(), handle="@" + agent_id)
        return cid


class SessionTest(RoomTest):
    def test_ai_session_runs_out(self):
        cid = self.conference(attendees=("beat_car", "nat_1"))
        model = ScriptedModel(passes_after=2)
        result = press_room.run_session(self.cursor(), cid, model)
        self.assertEqual(result["status"], "CLOSED")
        self.assertEqual(result["end_reason"], "RAN_OUT")
        turns = [t for t in self.db.turns.values() if t["conference_id"] == cid]
        kinds = [t["kind"] for t in turns]
        self.assertEqual(kinds[0], "OPENING")
        self.assertIn("QUESTION", kinds)
        self.assertEqual(kinds[-1], "CLOSING")
        self.assertTrue(any(h["conference_id"] == cid for h in self.db.hands))

    def test_ai_session_patience(self):
        cid = self.conference(attendees=("beat_car",), patience={"budget": 1, "spent": 0})
        result = press_room.run_session(self.cursor(), cid, ScriptedModel())
        self.assertEqual((result["status"], result["end_reason"]), ("CLOSED", "PATIENCE"))

    def test_ai_session_cap(self):
        cid = self.conference(attendees=("beat_car",))
        with mock.patch.object(press_agents, "MAX_QUESTIONS", 1):
            result = press_room.run_session(self.cursor(), cid, ScriptedModel())
        self.assertEqual(result["end_reason"], "CAP")

    def test_bad_reporter_line_retried_then_dropped_and_coach_falls_back(self):
        cid = self.conference(attendees=("bad_beat", "good_beat"), patience={"budget": 1, "spent": 0})
        model = ScriptedModel(passes_after=1, bad_question=True, bad_coach=True)
        # one good reporter must ask so the coach can answer badly; patch the model to be bad only for one name
        good = ScriptedModel(passes_after=1)
        original = model.__call__

        def dispatch(prompt):
            if "RULES FOR YOUR QUESTION" in prompt and "Bad Beat" in prompt:
                return original(prompt)
            if "RULES FOR YOUR QUESTION" in prompt:
                return good(prompt)
            return original(prompt)

        with mock.patch.object(press_room, "PRESS_REPOLL", "all"):
            result = press_room.run_session(self.cursor(), cid, dispatch)
        self.assertEqual(result["status"], "CLOSED")
        self.assertEqual(model.bad_reporter_calls, 2)          # one retry, then dropped
        answers = [t for t in self.db.turns.values() if t["conference_id"] == cid and t["kind"] == "ANSWER"]
        self.assertTrue(answers)
        self.assertTrue(all(t["source"] == "FALLBACK" for t in answers))
        self.assertIn("tape", answers[0]["text"])

    def test_user_session_waits_and_resumes(self):
        cid = self.conference(attendees=("beat_car", "nat_1"), controller="USER", patience=None)
        model = ScriptedModel()
        first = press_room.run_session(self.cursor(), cid, model)
        self.assertEqual(self.db.conferences[cid]["status"], "LIVE")
        self.assertTrue(first["awaiting_pick"])
        agent_id = first["awaiting_pick"][0]["agent_id"]
        question = press_room.pick_user(self.cursor(), cid, agent_id)
        self.assertEqual(question["kind"], "QUESTION")
        waiting = press_room.run_session(self.cursor(), cid, model)
        self.assertEqual(waiting["awaiting_answer"], question["id"])
        with mock.patch.object(press_room, "_user_llm", lambda: model):
            resumed = press_room.answer_user(self.cursor(), cid, question["id"], "We played hard.", False)
        self.assertFalse(resumed["ended"])
        self.assertTrue(resumed["hands"])
        review = press_room.end_user(self.cursor(), cid)
        self.assertEqual(review["status"], "REVIEW")
        answer_turn = next(t for t in self.db.turns.values()
                           if t["conference_id"] == cid and t["kind"] == "ANSWER")
        edited = press_room.edit_user(self.cursor(), cid, answer_turn["id"], "We played hard, and better.",
                                      False)
        self.assertEqual(edited["on_record"], False)
        conf = press_room.approve(self.cursor(), cid)
        self.assertEqual(conf["status"], "CLOSED")

    def test_edits_only_in_review(self):
        cid = self.conference(attendees=("beat_car",), controller="USER", status="LIVE")
        answer_id = self.db.add_turn(cid, "SUBJECT", "ANSWER", "We played hard.", source="USER")
        with self.assertRaises(press_room.PressConflict):
            press_room.edit_user(self.cursor(), cid, answer_id, "changed")

    def test_pick_refuses_unraised_hand(self):
        cid = self.conference(attendees=("beat_car",), controller="USER", status="LIVE")
        with self.assertRaises(press_room.PressBadRequest):
            press_room.pick_user(self.cursor(), cid, "nobody")

    def test_decline_from_scheduled_and_review_is_approved(self):
        scheduled = self.conference(attendees=("beat_car",), controller="USER", status="SCHEDULED")
        declined = press_room.decline(self.cursor(), scheduled, "PLAY_GAME")
        self.assertEqual(declined["status"], "DECLINED")
        review = self.conference(attendees=("beat_car",), controller="USER", status="REVIEW")
        approved = press_room.decline(self.cursor(), review, "NEXT_INGEST")
        self.assertEqual(approved["status"], "CLOSED")

    def test_start_requires_user_and_scheduled(self):
        ai = self.conference(attendees=("beat_car",), controller="AGENT", status="SCHEDULED")
        with self.assertRaises(press_room.PressForbidden):
            press_room.start_user(self.cursor(), ai)
        live = self.conference(attendees=("beat_car",), controller="USER", status="LIVE")
        with self.assertRaises(press_room.PressConflict):
            press_room.start_user(self.cursor(), live)

    def test_expire_and_launch(self):
        old = self.conference(attendees=("beat_car",), controller="USER", status="SCHEDULED", week=WEEK - 1)
        self.assertIn(old, press_room.expire_user_pressers(self.cursor(), SEASON, WEEK))
        self.assertEqual(self.db.conferences[old]["status"], "DECLINED")
        new = self.conference(attendees=("beat_car",), controller="USER", status="LIVE", week=WEEK)
        self.assertIn(new, press_room.decline_open_for_launch(self.cursor(), CAR, SEASON))
        self.assertEqual(self.db.conferences[new]["status"], "DECLINED")


class EffectsTest(RoomTest):
    def closed_conference(self):
        cid = self.conference(attendees=("beat_car", "nat_1"), status="CLOSED")
        for agent_id in ("beat_car", "nat_1"):
            question = self.db.add_turn(cid, "REPORTER", "QUESTION", "Coach, what changed?", agent_id=agent_id)
            self.db.add_turn(cid, "SUBJECT", "ANSWER", "We played hard.", reply_to=question)
            self.db.hands.append({"conference_id": cid, "round": 1, "agent_id": agent_id,
                                  "picked": True, "pick_source": "AGENT"})
        return cid

    def test_close_effects_stores_appraisal_events_and_memory_once(self):
        cid = self.closed_conference()
        model = ScriptedModel()
        result = press_room._close_effects_cur(self.cursor(), cid, model)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["judged"], 2)
        self.assertEqual(result["memories"], 2)
        judgments = [a["judgment"] for a in self.db.attendees if a["conference_id"] == cid]
        for judgment in judgments:
            self.assertIn("appraisal", judgment)
            self.assertIn("events", judgment)
            self.assertFalse(judgment["appended"])
            self.assertTrue(judgment["events"])
        self.assertEqual(len(self.db.memories), 2)
        again = press_room._close_effects_cur(self.cursor(), cid, model)
        self.assertEqual(again["status"], "already")
        self.assertEqual(len(self.db.memories), 2)

    def test_declined_presser_is_appraised_and_remembered(self):
        cid = self.conference(attendees=("beat_car",), status="DECLINED")
        result = press_room._close_effects_cur(self.cursor(), cid, ScriptedModel())
        self.assertEqual(result["judged"], 1)
        judgment = next(a["judgment"] for a in self.db.attendees if a["conference_id"] == cid)
        self.assertEqual(judgment["appraisal"]["treatment"]["treatment_kind"], "dismissal")
        self.assertTrue(any(e["source_event_id"] == f"press:{cid}:declined" for e in judgment["events"]))
        self.assertIn("didn't take questions", self.db.memories[0]["text"])

    def test_no_persona_relationships_write(self):
        cid = self.closed_conference()
        cursor = self.cursor()
        seen = []
        original = cursor.execute

        def record(sql, params=None):
            seen.append(sql)
            return original(sql, params)

        cursor.execute = record
        press_room._close_effects_cur(cursor, cid, ScriptedModel())
        self.assertFalse(any("persona_relationships" in sql for sql in seen))

    def test_flush_returns_zero_without_store(self):
        cid = self.closed_conference()
        press_room._close_effects_cur(self.cursor(), cid, ScriptedModel())
        with mock.patch.object(press_room, "_relationship_store", lambda: None):
            self.assertEqual(press_room.flush_relationship_events(self.cursor(), cid), 0)

    def test_flush_appends_once_and_marks_appended(self):
        cid = self.closed_conference()
        press_room._close_effects_cur(self.cursor(), cid, ScriptedModel())

        class Store:
            EVENTS_TABLE = "persona_relationship_events"

            def __init__(self):
                self.appended = []

            def read_head(self, cur, universe_id):
                return 0

            def append_events(self, cur, events, *, expected_head, team_registry):
                self.appended.extend(events)
                return {"status": "appended", "appended": [{"source_event_id": e["source_event_id"]}
                                                           for e in events],
                        "duplicates": [], "rejected": []}

        store = Store()
        with mock.patch.object(press_room, "_relationship_store", lambda: store), \
                mock.patch.object(press_room, "_regclass_table", lambda cur, table: True):
            first = press_room.flush_relationship_events(self.cursor(), cid)
            second = press_room.flush_relationship_events(self.cursor(), cid)
        self.assertGreater(first, 0)
        self.assertEqual(second, 0)
        self.assertTrue(all(a["judgment"]["appended"] for a in self.db.attendees if a["conference_id"] == cid))
        self.assertTrue(store.appended)
        rev.validate_event_batch(store.appended, calendar={SEASON: 32},
                                 team_registry={f"team:{CAR}": {"team_id": CAR}})

    def test_skip_history_counts_hands_and_picks(self):
        cid = self.closed_conference()
        history = press_agents.skip_history(self.cursor(), "pk_beat_car", COACH_ID, SEASON)
        self.assertEqual((history["hands"], history["picked"], history["pressers"]), (1, 1, 1))


class RunnerTest(RoomTest):
    def test_subject_controller_reads_user_teams(self):
        self.assertEqual(press_room.subject_controller(self.cursor(), CAR), "USER")
        self.assertEqual(press_room.subject_controller(self.cursor(), ATL), "AGENT")
        with mock.patch.object(press_room, "_user_teams", lambda: []):
            self.assertEqual(press_room.subject_controller(self.cursor(), CAR), "AGENT")

    def test_run_week_is_idempotent_and_one_failure_does_not_stop_the_rest(self):
        good = self.conference(attendees=("beat_car",), controller="AGENT", status="SCHEDULED")
        bad = self.conference(attendees=("beat_car", "nat_1"), controller="AGENT", status="SCHEDULED")
        user = self.conference(attendees=("beat_car",), controller="USER", status="SCHEDULED")
        model = ScriptedModel(passes_after=2)
        real_run_session = press_room.run_session
        calls = {"good": 0, "bad": 0}

        def run(cur_factory, conference_id, llm, user_answer=None):
            if conference_id == bad:
                calls["bad"] += 1
                raise RuntimeError("boom")
            calls["good"] += 1
            return real_run_session(cur_factory, conference_id, llm, user_answer)

        with mock.patch.object(press_room, "schedule_week", lambda cur, s, w, llm=None: {}), \
                mock.patch.object(press_room, "run_session", run):
            first = press_room.run_week("fake-dsn", SEASON, WEEK, workers=1, llm=model)
            second = press_room.run_week("fake-dsn", SEASON, WEEK, workers=1, llm=model)

        self.assertEqual(self.db.conferences[good]["status"], "CLOSED")
        self.assertEqual(self.db.conferences[bad]["status"], "FAILED")
        self.assertEqual(self.db.conferences[user]["status"], "SCHEDULED")
        self.assertEqual(first["scheduled_user"], 1)
        self.assertEqual(first["failed"], 1)
        self.assertEqual(second["closed"], 1)
        self.assertEqual(calls["good"], 1)          # the CLOSED presser is skipped on the second run
        self.assertEqual(calls["bad"], 2)           # FAILED gets one retry


class WeekTest(RoomTest):
    def make_week(self):
        for team_id in range(3, 33):
            self.db.teams[team_id] = {"id": team_id, "abbreviation": f"T{team_id}", "name": f"Team {team_id}",
                                      "city": "City"}
        casts, facts_by_game = {}, {}
        team_ids = list(range(1, 33))
        for game_index in range(16):
            game_id = 200 + game_index
            home, away = team_ids[2 * game_index], team_ids[2 * game_index + 1]
            casts[game_id] = {
                "game_id": game_id, "interest": 60.0 + game_index, "tier": "NOTABLE", "inputs": {},
                "nationals": [{"agent_id": f"nat_{game_index}", "person_key": f"pk_nat_{game_index}",
                               "display_name": f"Nat {game_index}", "handle": f"@nat{game_index}",
                               "role": "NATIONAL", "pool_tier": "INSIDER"}],
                "beats": {home: [{"agent_id": f"beat_{home}", "person_key": f"pk_beat_{home}",
                                  "display_name": f"Beat {home}", "handle": f"@beat{home}",
                                  "role": "BEAT", "pool_tier": None}],
                          away: [{"agent_id": f"beat_{away}", "person_key": f"pk_beat_{away}",
                                  "display_name": f"Beat {away}", "handle": f"@beat{away}",
                                  "role": "BEAT", "pool_tier": None}]},
            }
            facts_by_game[game_id] = {
                "game": {"season": SEASON, "week": WEEK, "final": {"home": 24, "away": 17}},
                "teams": [
                    {"team_id": home, "name": f"Team {home}", "abbr": f"T{home}", "home": True,
                     "coach": {"id": 100 + home, "name": f"Coach {home}"}},
                    {"team_id": away, "name": f"Team {away}", "abbr": f"T{away}", "home": False,
                     "coach": {"id": 100 + away, "name": f"Coach {away}"}},
                ],
                "names": [f"Coach {home}", f"Coach {away}"], "numbers": {},
            }
        return casts, facts_by_game

    def test_schedule_week_casts_32_and_flags_two_user_pressers(self):
        casts, facts_by_game = self.make_week()
        with mock.patch.object(press_room.press_cast, "cast_week", lambda cur, s, w: casts), \
                mock.patch.object(press_room, "_facts_for_game", lambda cur, gid, c: facts_by_game[gid]), \
                mock.patch.object(press_room, "_user_teams", lambda: ["CAR", "ATL"]):
            first = press_room.schedule_week(self.cursor(), SEASON, WEEK, llm=None)
            second = press_room.schedule_week(self.cursor(), SEASON, WEEK, llm=None)
        self.assertEqual((first["scheduled"], first["user"], first["agent"]), (32, 2, 30))
        self.assertEqual(len(first["conferences"]), 32)
        self.assertEqual(len(self.db.conferences), 32)       # idempotent: no duplicates
        self.assertEqual(second["scheduled"], 32)

    def test_run_week_closes_thirty_ai_pressers_and_schedules_two_user_ones(self):
        for index in range(30):
            self.conference(attendees=(f"beat_{index}",), controller="AGENT", status="SCHEDULED")
        self.conference(attendees=("beat_user_a",), controller="USER", status="SCHEDULED")
        self.conference(attendees=("beat_user_b",), controller="USER", status="SCHEDULED")
        model = ScriptedModel(passes_after=2)
        with mock.patch.object(press_room, "schedule_week", lambda cur, s, w, llm=None: {}):
            summary = press_room.run_week("fake-dsn", SEASON, WEEK, workers=6, llm=model)
        self.assertEqual(summary["closed"], 30)
        self.assertEqual(summary["scheduled_user"], 2)
        self.assertEqual(summary["failed"], 0)


class ReaderTest(RoomTest):
    def seed_closed(self, status="CLOSED"):
        cid = self.conference(attendees=("beat_car",), status=status)
        question = self.db.add_turn(cid, "REPORTER", "QUESTION", "Coach, what changed?", agent_id="beat_car")
        self.db.add_turn(cid, "SUBJECT", "ANSWER", "We ran the ball better.", reply_to=question,
                         issues=[{"kind": "NUMBER", "claim": "three turnovers", "recorded": 2}])
        self.db.add_turn(cid, "SUBJECT", "NO_COMMENT", "", reply_to=self.db.add_turn(
            cid, "REPORTER", "QUESTION", "Coach, is he hurt?", agent_id="beat_car"))
        return cid

    def test_approved_quotes(self):
        cid = self.seed_closed()
        quotes = press_room.approved_quotes(self.cursor(), SEASON, WEEK)
        self.assertEqual(len(quotes), 1)
        answer = quotes[0]
        self.assertEqual(set(answer), set(press_room.APPROVED_QUOTE_KEYS))
        self.assertEqual(answer["question"], "Coach, what changed?")
        self.assertEqual(answer["agent_id"], "beat_car")
        self.assertEqual(answer["flags"][0]["kind"], "NUMBER")

    def test_brief_block(self):
        self.seed_closed()
        block = press_room.brief_block(self.cursor(), CAR, SEASON, WEEK + 1)   # the week that briefs game week 5
        self.assertEqual(block["subject"], "Marcus Whitfield")
        self.assertIn("Week 5", block["game"])
        self.assertEqual(block["exchanges"][0]["reporter"], "@beat_car")
        self.assertTrue(block["no_comment"])
        self.assertTrue(block["corrections"])
        self.assertIn("Quote the head coach", block["reporting_guidance"])

    def test_declined_note(self):
        self.conference(attendees=("beat_car",), status="DECLINED")
        note = press_room.declined_note(self.cursor(), CAR, SEASON, WEEK + 1)
        self.assertIn("did not take questions", note)
        self.assertIsNone(press_room.declined_note(self.cursor(), ATL, SEASON, WEEK + 1))

    def test_the_brief_takes_only_the_latest_settled_presser(self):
        """A week-4 decline must not ride along with a week-5 transcript, and a week-N postgame presser must not
        appear in week N's pregame brief."""
        self.conference(attendees=("beat_car",), status="DECLINED", week=WEEK - 1)
        self.conference(attendees=("beat_car",), status="CLOSED", week=WEEK)
        # week 5 briefs game week 4: the decline, never the week-5 transcript that hadn't happened yet
        self.assertIsNone(press_room.brief_block(self.cursor(), CAR, SEASON, WEEK))
        self.assertIsNotNone(press_room.declined_note(self.cursor(), CAR, SEASON, WEEK))
        # week 6 briefs game week 5: the transcript, and not the week-4 decline riding along beside it
        self.assertIsNotNone(press_room.brief_block(self.cursor(), CAR, SEASON, WEEK + 1))
        self.assertIsNone(press_room.declined_note(self.cursor(), CAR, SEASON, WEEK + 1))

    def test_build_conference_shape(self):
        cid = self.conference(attendees=("beat_car",), status="CLOSED")
        conf = press_room.build_conference(self.cursor(), cid)
        self.assertEqual(conf["team"], "CAR")
        self.assertEqual(conf["opponent"], "ATL")
        self.assertEqual((conf["result"], conf["score"]), ("W", "24-17"))
        self.assertEqual(conf["attendees"][0]["agent_id"], "beat_car")


class CliAndSchemaTest(unittest.TestCase):
    ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]

    def test_migration_and_init_db_define_the_four_tables(self):
        migration = (self.ROOT / "db" / "migrations" / "2027_01_20_press_conferences.sql").read_text()
        init = (self.ROOT / "db" / "init_db.sql").read_text()
        for sql in (migration, init):
            for table in ("press_conferences", "press_attendees", "press_hands", "press_turns"):
                self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", sql)
            self.assertIn("CHECK (status IN ('SCHEDULED', 'LIVE', 'REVIEW', 'CLOSED', 'DECLINED', 'FAILED'))", sql)
            self.assertIn("CHECK (kind IN ('OPENING', 'QUESTION', 'FOLLOW_UP', 'REACT', 'ANSWER', 'NO_COMMENT'", sql)
            self.assertIn("CHECK (pick_source IN ('AGENT', 'USER', 'FALLBACK'))", sql)
            self.assertIn("idx_press_conferences_week", sql)

    def test_cli_help_and_dry_run_make_no_model_calls(self):
        with self.assertRaises(SystemExit) as ctx:
            press_room.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)

        db = FakeDB()
        cast = {"game": {"game_id": 100, "interest": 70.0, "tier": "BIG",
                         "nationals": [{"agent_id": "nat_1", "display_name": "Nat Writer", "role": "NATIONAL",
                                        "person_key": "pk_nat_1", "pool_tier": "INSIDER"}],
                         "beats": {CAR: [{"agent_id": "beat_car", "display_name": "Joe Beat", "role": "BEAT",
                                          "person_key": "pk_beat_car", "pool_tier": None}]}}}

        def no_model(*args, **kwargs):
            raise AssertionError("dry run made a model call")

        with mock.patch.object(press_room, "_conn_info", lambda: "fake-dsn"), \
                mock.patch.object(press_room, "_connect", lambda conn_info: FakeConn(db)), \
                mock.patch.object(press_room.press_cast, "cast_week", lambda cur, s, w: cast), \
                mock.patch.object(press_room, "_facts_for_game", lambda cur, gid, casts: facts()), \
                mock.patch.object(press_room.press_agents, "reporter_turn", no_model), \
                mock.patch.object(press_room.press_agents, "_reporter_prompt", lambda a, s: "FIRST ROUND PROMPT"):
            out = io.StringIO()
            with redirect_stdout(out):
                press_room.main(["run-week", "--season", str(SEASON), "--week", str(WEEK), "--dry-run"])
        self.assertIn("FIRST ROUND PROMPT", out.getvalue())
        self.assertIn("BIG", out.getvalue())


if __name__ == "__main__":
    unittest.main()


class _ToolCursor:
    """Scripted cursor for get_press_conference: team row, then conference ids."""
    def __init__(self):
        self.out = []

    def execute(self, sql, params=()):
        s = str(sql)
        if "/* q:tool_team */" in s:
            self.out = [(12, "CAR", "Panthers", "Carolina")]
        elif "/* q:tool_conferences */" in s:
            self.out = [(1, "CLOSED")]
        else:
            self.out = []

    def fetchone(self):
        return self.out[0] if self.out else None

    def fetchall(self):
        return list(self.out)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _ToolConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_get_press_conference_never_quotes_an_off_the_record_answer(monkeypatch):
    """§2.6: only on-record answers reach the newsroom tool; a question goes with its off-record answer."""
    conf = {"team": "CAR", "subject_name": "Dave Canales", "week": 5, "opponent": "ATL", "result": "W",
            "score": "24-17", "end_reason": None, "turns": [
                {"speaker": "REPORTER", "reporter": {"handle": "joe"}, "text": "ON RECORD QUESTION"},
                {"speaker": "SUBJECT", "kind": "ANSWER", "text": "ON RECORD ANSWER", "on_record": True},
                {"speaker": "REPORTER", "reporter": {"handle": "joe"}, "text": "SECRET QUESTION"},
                {"speaker": "SUBJECT", "kind": "ANSWER", "text": "OFF THE RECORD ANSWER", "on_record": False},
            ]}
    cur = _ToolCursor()
    monkeypatch.setattr(press_room, "_tool_connect", lambda: _ToolConn(cur))
    monkeypatch.setattr(press_room, "_installed", lambda c: True)
    monkeypatch.setattr(press_room, "_resolve_tool_season", lambda c, s: 2026)
    monkeypatch.setattr(press_room, "_resolve_cutoff_week", lambda s, w=0: (None, None))
    monkeypatch.setattr(press_room, "build_conference", lambda c, cid, include_hands=False: conf)
    text = press_room.get_press_conference("CAR")
    assert "ON RECORD ANSWER" in text and "ON RECORD QUESTION" in text
    assert "OFF THE RECORD ANSWER" not in text and "SECRET QUESTION" not in text


def test_league_id_reads_the_personality_league_key():
    """personality_feature_modes is keyed by league_state.madden_league_id; a hardcoded 1 always read 'off'."""
    import re as _re

    class C:
        def __init__(self, mapping):
            self.mapping, self.out = mapping, None

        def execute(self, sql, params=()):
            tag = _re.search(r"/\* q:(\w+) \*/", sql).group(1)
            self.out = self.mapping.get(tag)

        def fetchone(self):
            return self.out

    assert press_room._league_id(C({"league": (2036, 5), "madden_league": (2085659472,)})) == 2085659472
    assert press_room._league_id(C({"league": (2036, 5)})) == 1          # a save without the column
    assert press_room._league_id(C({})) is None                          # no league_state row


def test_subject_does_not_hand_the_coach_sheet_over_as_a_profile():
    """`profile` used to hold the scheme dict, so `_personality_view` skipped its lookup and stored personas were
    never loaded in a real presser."""
    row = {"subject_id": 10, "subject_name": "Marcus Whitfield", "team_id": CAR}
    subject = press_room._subject(row)
    assert "profile" not in subject and subject["identity"] is None
    assert subject["persona_id"] == "10"


def test_subject_resolves_the_coach_lifecycle_persona(monkeypatch):
    """The coach's persona is the lifecycle one behind the db-coach:<id> alias; the raw coaches.id was rejected by
    relationship_store as unknown_identity, so no presser event was ever written."""
    from personality.core import Identity
    import personality.store as pstore
    from engines.relationship_producers import sources

    monkeypatch.setattr(sources, "league_id", lambda cur: 4242)
    monkeypatch.setattr(sources, "universe_id", lambda cur, league: "mu:00000000-0000-4000-8000-000000000001")
    monkeypatch.setattr(pstore, "resolve_alias",
                        lambda cur, u, role, ns, ext: Identity.adopted(u, role, "kyle-shanahan"))
    persona_id, identity = press_room._coach_persona(object(), 10)
    assert persona_id == "kyle-shanahan"
    assert identity == {"universe_id": "mu:00000000-0000-4000-8000-000000000001", "role": "coach",
                        "birth_key": "legacy:coach:kyle-shanahan"}


def test_effects_pending_covers_declined_and_earlier_weeks():
    """A DECLINED presser (and one whose background thread was interrupted) never got its judgments or events."""
    import unittest
    test = _StandaloneRoom()
    test.setUp()
    declined = test.conference(attendees=("beat_car",), status="DECLINED")
    earlier = test.conference(attendees=("beat_car",), status="CLOSED", week=WEEK - 1)
    current = test.conference(attendees=("beat_car",), status="CLOSED")
    ids = press_room._effects_pending_ids(test.cursor(), SEASON, WEEK)
    assert {declined, earlier, current} <= set(ids)


class _StandaloneRoom(RoomTest):
    """RoomTest without unittest's test runner, so a plain pytest function can reuse its fixtures."""
    def runTest(self):
        pass


def test_the_cutoff_is_always_applied(monkeypatch):
    """An explicit week used to return early and skip run_context entirely, and a future season's cutoff of 0 was
    treated as falsy."""
    from newsroom import run_context
    monkeypatch.setattr(run_context, "cutoff_week", lambda s: 5)
    assert press_room._resolve_cutoff_week(2036) == (5, None)
    assert press_room._resolve_cutoff_week(2036, 18) == (5, 18)      # a filter, never a bypass
    monkeypatch.setattr(run_context, "cutoff_week", lambda s: 0)
    assert press_room._resolve_cutoff_week(2037) == (0, None)        # a future season: nothing visible
    monkeypatch.setattr(run_context, "cutoff_week", lambda s: None)
    assert press_room._resolve_cutoff_week(2035) == (None, None)


def test_press_model_decides_the_provider(monkeypatch):
    """GLM_5_3_UNIFICATION_PLAN §2.3: PRESS_MODEL=glm-5.3-flash reaches OpenCode Go without LLM_PROVIDER_PRESS,
    and with PRESS_MODEL unset the press route stays Gemini."""
    for key in ("LLM_PROVIDER", "LLM_PROVIDER_PRESS", "MU_GO_TEXT_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert press_room.press_provider(None) == "gemini"                 # unset: the provider default
    assert press_room.press_provider("gemini-3.8-flash") == "gemini"
    assert press_room.press_provider("glm-5.3-flash") == "go"
    assert press_room.press_provider("go:glm-5.3-flash") == "go"
    monkeypatch.setenv("LLM_PROVIDER_PRESS", "gemini")                # the env still wins over the model id
    assert press_room.press_provider("glm-5.3-flash") == "gemini"
    monkeypatch.delenv("LLM_PROVIDER_PRESS")

    sent = {}

    def fake_generate_text(prompt, **kwargs):
        sent.update(kwargs, prompt=prompt)
        return "He did his job.", {"input_tokens": 3, "output_tokens": 2}

    monkeypatch.setattr("engines.llm_provider.generate_text", fake_generate_text)
    llm = press_room.MeteredLLM(model="glm-5.3-flash")
    assert llm("Why the loss?") == "He did his job."
    assert sent["prompt"] == "Why the loss?" and sent["source"] == "press"
    assert (sent["model"], sent["provider"]) == ("glm-5.3-flash", "go")
    assert (llm.calls, llm.input_tokens, llm.output_tokens) == (1, 3, 2)

