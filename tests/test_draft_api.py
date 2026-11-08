"""Tests for engines/college_stats.py and server/draft_api.py (Draft Central; docs/done/DRAFT_CENTRAL_PLAN.md)."""
import json
import unittest
import uuid
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from engines import college_stats as cs
from server import draft_api as dapi


class CollegeStatsPureTest(unittest.TestCase):
    def test_aggregate_qb_games_and_strip_hidden_keys(self):
        events = [
            {
                "step": 5,
                "kind": "GAME",
                "headline": "Von Porter shines vs Michigan",
                "blurb": "Maryland quarterback Von Porter (college week 6): 24-of-32 for 310 yards, 3 TD, 0 INT.",
                "payload": {
                    "opponent": "Michigan",
                    "game_type": "REGULAR",
                    "label": "college week 6",
                    "college_week": 6,
                    "stats": {"comp": 24, "att": 32, "pass_yds": 310, "pass_td": 3, "int": 0, "rush_yds": 28},
                    "_production_pct": 95.0,
                },
            },
            {
                "step": 4,
                "kind": "GAME",
                "headline": "Von Porter steady vs Indiana",
                "blurb": "Maryland quarterback Von Porter (college week 5): 20-of-30 for 240 yards, 2 TD, 1 INT.",
                "payload": {
                    "opponent": "Indiana",
                    "game_type": "REGULAR",
                    "label": "college week 5",
                    "college_week": 5,
                    "stats": {"comp": 20, "att": 30, "pass_yds": 240, "pass_td": 2, "int": 1, "rush_yds": 12},
                    "_production_pct": 82.0,
                },
            },
        ]
        agg = cs.aggregate_games(events, "QB")
        self.assertEqual(agg["stat_group"], "QB")
        self.assertEqual(agg["games"], 2)
        t = agg["totals"]
        self.assertEqual(t["comp"], 44)
        self.assertEqual(t["att"], 62)
        self.assertEqual(t["pass_yds"], 550)
        self.assertEqual(t["pass_td"], 5)
        self.assertEqual(t["int"], 1)
        self.assertEqual(t["rush_yds"], 40)
        self.assertAlmostEqual(t["comp_pct"], 71.0, places=1)
        self.assertTrue(t["passer_rating"] > 110.0)
        self.assertIn("2 G · 44/62 (71.0%) · 550 YDS · 5 TD · 1 INT", agg["summary_line"])
        # Sorted chronologically by step (step 4 then step 5)
        self.assertEqual([g["step"] for g in agg["game_log"]], [4, 5])
        self.assertEqual(agg["game_log"][0]["verdict"], "STEADY")
        self.assertEqual(agg["game_log"][1]["verdict"], "SHINES")
        for g in agg["game_log"]:
            self.assertNotIn("_production_pct", g["stats"])

    def test_aggregate_edge_and_cb(self):
        edge_evs = [{
            "step": 6,
            "kind": "GAME",
            "headline": "Taylor Norton shines vs Clemson",
            "blurb": "...",
            "payload": {"opponent": "Clemson", "stats": {"tackles": 8, "tfl": 3, "sacks": 2, "pressures": 9}},
        }]
        edge_agg = cs.aggregate_games(edge_evs, "LEDG")
        self.assertEqual(edge_agg["stat_group"], "EDGE")
        self.assertEqual(edge_agg["totals"]["sacks"], 2)
        self.assertIn("1 G · 8 TKL · 3 TFL · 2 SACK · 9 PRESS", edge_agg["summary_line"])

        cb_evs = [{
            "step": 6,
            "kind": "GAME",
            "headline": "Jamar Bodden struggles vs Ohio State",
            "blurb": "...",
            "payload": {"opponent": "Ohio State", "stats": {"targets": 8, "catches_allowed": 5, "pbu": 1, "int": 0}},
        }]
        cb_agg = cs.aggregate_games(cb_evs, "CB")
        self.assertEqual(cb_agg["totals"]["comp_pct_allowed"], 62.5)
        self.assertEqual(cb_agg["game_log"][0]["verdict"], "STRUGGLES")

    def test_combine_profile_percentiles(self):
        pre = cs.combine_profile([], "WR", height_in=73, weight=200)
        self.assertFalse(pre["participated"])
        self.assertEqual(pre["height"], "6'1\"")

        comb_ev = [{
            "step": 24,
            "kind": "COMBINE",
            "headline": "Fast Receiver runs 4.35 at the combine",
            "blurb": "...",
            "payload": {
                "measurables": {"height_in": 73, "weight": 200, "forty": 4.35, "vertical": 40.5,
                                "broad_jump": 132, "three_cone": 6.72, "bench": 16},
                "interviews": "strong",
                "medical": "cleared",
            },
        }]
        post = cs.combine_profile(comb_ev, "WR", height_in=73, weight=200)
        self.assertTrue(post["participated"])
        self.assertEqual(len(post["drills"]), 5)
        self.assertTrue(post["athletic_score"] >= 8.0)
        forty_drill = next(d for d in post["drills"] if d["key"] == "forty")
        self.assertTrue(forty_drill["percentile"] >= 90)


import pytest


@pytest.mark.integration
class DraftApiRoutesTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(dapi.router)
        self.client = TestClient(app)

    def test_board_and_dossier_against_live_or_mock(self):
        r_state = self.client.get("/api/draft/state")
        self.assertEqual(r_state.status_code, 200)
        st = r_state.json()
        self.assertIn("draft_season", st)
        self.assertIn("installed", st)

        r_board = self.client.get("/api/draft/board?limit=15")
        self.assertEqual(r_board.status_code, 200)
        b = r_board.json()
        self.assertIn("prospects", b)
        if b["prospects"]:
            first = b["prospects"][0]
            self.assertNotIn("ovr", first)
            self.assertNotIn("true_value", first)
            self.assertIn("stat_summary", first)
            self.assertIn("position_group", first)

            r_dos = self.client.get(f"/api/draft/prospect/{first['prospect_key']}")
            self.assertEqual(r_dos.status_code, 200)
            dos = r_dos.json()
            self.assertEqual(dos["prospect_key"], first["prospect_key"])
            self.assertIn("season_stats", dos)
            self.assertIn("game_log", dos)
            self.assertIn("combine", dos)
            self.assertIn("grade_history", dos)
            self.assertNotIn("ovr", dos["profile"])
            self.assertNotIn("true_value", dos["profile"])

    def test_board_filters(self):
        r_qb = self.client.get("/api/draft/board?position=QB&round_filter=R1")
        self.assertEqual(r_qb.status_code, 200)
        for p in r_qb.json().get("prospects", []):
            self.assertEqual(p["position_group"], "QB")
            self.assertLessEqual(p["rank"], 32)

    def test_movers_awards_needs_and_mock(self):
        r_mov = self.client.get("/api/draft/movers?limit=5")
        self.assertEqual(r_mov.status_code, 200)
        self.assertIn("risers", r_mov.json())
        self.assertIn("fallers", r_mov.json())

        r_aw = self.client.get("/api/draft/awards")
        self.assertEqual(r_aw.status_code, 200)
        self.assertIn("heisman_watch", r_aw.json())
        self.assertIn("awards", r_aw.json())

        r_needs = self.client.get("/api/draft/team-needs")
        self.assertEqual(r_needs.status_code, 200)
        teams = r_needs.json().get("teams", [])
        self.assertEqual(len(teams), 32)
        self.assertIn("draft_capital_points", teams[0])
        self.assertIn("needs", teams[0])

        r_mock = self.client.get("/api/draft/mock")
        self.assertEqual(r_mock.status_code, 200)
        picks = r_mock.json().get("picks", [])
        for pk in picks:
            self.assertNotIn("ovr", pk["prospect"])
            self.assertNotIn("overall_rating", pk["prospect"])

    def test_war_room_auth_and_actions(self):
        r_wr = self.client.get("/api/draft/war-room")
        self.assertEqual(r_wr.status_code, 200)
        self.assertIn("slots", r_wr.json())

        with mock.patch("server.debug_api.env", lambda k, d=None: "secret" if k == "DEBUG_TOKEN" else d):
            self.assertEqual(self.client.post("/api/draft/war-room/lock-order", json={}).status_code, 401)
            self.assertEqual(self.client.post("/api/draft/war-room/picks/1", json={"prospect_key": "k"}).status_code, 401)
            self.assertEqual(self.client.post("/api/draft/war-room/picks/1/undo").status_code, 401)


if __name__ == "__main__":
    unittest.main()


# ----------------------------------------------------------------------------------- V2.10 endpoints ----
def _fake_conn(cur):
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return cur

    return Conn()


def _hidden_keys(payload):
    found, stack = set(), [payload]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            found |= {str(k).upper() for k in x}
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return found


def test_report_endpoint_public_shape_and_no_hidden_keys():
    app = FastAPI()
    app.include_router(dapi.router)
    client = TestClient(app)
    uid = "00000000-0000-0000-0000-000000000001"
    report = {
        "prospect_uid": uid, "prospect_key": "2037:QB0:QB", "draft_season": 2037, "edition": "FALL",
        "step": 4, "grade": 80.0, "rank": 1, "round_label": "1st round", "role": "DAY1_STARTER",
        "role_words": "a Day 1 starter",
        "traits": [{"trait": "arm", "label": "arm strength", "score": 7.5, "word": "plus"}],
        "strengths": ["arm strength"], "concerns": [], "flags": ["INJURY_HISTORY"],
        "body": {"summary": "s", "strengths": "s", "concerns": "c", "production": "p", "athletic": "",
                 "projection": "p", "bottom_line": "b"},
        "text_source": "TEMPLATE",
    }
    cur = mock.MagicMock()
    with mock.patch.object(dapi, "_connect", return_value=_fake_conn(cur)), \
         mock.patch.object(dapi, "_league_state", return_value=(2036, 1)), \
         mock.patch("engines.prospect_ids.uid_for", return_value=uuid.UUID(uid)), \
         mock.patch("engines.scouting_reports.report_for", return_value=report), \
         mock.patch("engines.college_stats.step_label", return_value="College Week 4 (NFL Wk 4)"):
        r = client.get("/api/draft/prospect/2037:QB0:QB/report?draft_season=2037&edition=FALL")
    assert r.status_code == 200
    body = r.json()
    assert body["prospect_uid"] == uid
    assert body["edition"] == "FALL"
    assert body["step_label"] == "College Week 4 (NFL Wk 4)"
    assert body["scout"] == {"name": "Mu Scouting Department", "scout_id": None}
    assert body["editions"] == ["FALL", "BOWLS", "FINAL"]
    assert body["role_words"] == "a Day 1 starter"
    assert body["traits"][0]["score"] == 7.5
    assert body["flags"] == [{"flag": "INJURY_HISTORY", "words": "an injury history"}]
    assert not (_hidden_keys(body) & {"TRUE_VALUE", "RATINGS", "ERR", "IMPACT", "OVR", "TRUE_RANK", "VALUE_INPUTS"})


def test_report_endpoint_404_when_none():
    app = FastAPI()
    app.include_router(dapi.router)
    client = TestClient(app)
    cur = mock.MagicMock()
    with mock.patch.object(dapi, "_connect", return_value=_fake_conn(cur)), \
         mock.patch.object(dapi, "_league_state", return_value=(2036, 1)), \
         mock.patch("engines.prospect_ids.uid_for", return_value=None):
        r = client.get("/api/draft/prospect/2037:NOSUCH:QB/report?draft_season=2037")
    assert r.status_code == 404


def test_interviews_endpoint_shape():
    app = FastAPI()
    app.include_router(dapi.router)
    client = TestClient(app)
    cur = mock.MagicMock()
    cur.fetchall.return_value = [
        (1, "00000000-0000-0000-0000-000000000001", "2037:QB0:QB", "QB Zero", "VISIT", "OPEN",
         6, json.dumps([{"seq": 1, "topic": "FOOTBALL_IQ", "grade": "SOLID", "classified_by": "MENU"}]),
         "SOLID", 42),
    ]
    window = {"latest_step": 25, "combine_open": False, "visit_open": True, "visit_for_step": 26}
    budget = {"combine_used": 2, "combine_left": 13, "visits_used": 1, "visits_left": 5}
    eligible = [{"prospect_uid": "00000000-0000-0000-0000-000000000002", "prospect_key": "2037:QB1:QB",
                 "full_name": "QB One", "position": "QB", "college": "UGA", "rank": 12, "kinds": ["VISIT"]}]
    with mock.patch.object(dapi, "_connect", return_value=_fake_conn(cur)), \
         mock.patch.object(dapi, "_league_state", return_value=(2036, 1)), \
         mock.patch("server.franchise_api.team_row", return_value={"id": 1, "abbr": "CAR", "name": "Panthers"}), \
         mock.patch("server.franchise_api.require_user_team", return_value=None), \
         mock.patch("engines.scouting_interviews.window", return_value=window), \
         mock.patch("engines.scouting_interviews.budget", return_value=budget), \
         mock.patch("engines.scouting_interviews.eligible", return_value=eligible):
        r = client.get("/api/draft/interviews?team=CAR&draft_season=2037")
    assert r.status_code == 200
    body = r.json()
    assert body["team"] == "CAR"
    assert body["draft_season"] == 2037
    assert body["window"] == window
    assert body["budget"] == budget
    assert body["eligible"] == eligible
    iv = body["interviews"][0]
    assert iv == {"id": 1, "prospect_uid": "00000000-0000-0000-0000-000000000001",
                  "prospect_key": "2037:QB0:QB", "full_name": "QB Zero", "kind": "VISIT", "status": "OPEN",
                  "questions_left": 5, "summary_grade": "SOLID", "thread_id": 42}


def test_interviews_endpoint_403_for_non_user_team():
    app = FastAPI()
    app.include_router(dapi.router)
    client = TestClient(app)
    cur = mock.MagicMock()

    def forbidden(team):
        raise dapi.HTTPException(403, "not one of your teams")

    with mock.patch.object(dapi, "_connect", return_value=_fake_conn(cur)), \
         mock.patch("server.franchise_api.team_row", return_value={"id": 2, "abbr": "ATL", "name": "Falcons"}), \
         mock.patch("server.franchise_api.require_user_team", side_effect=forbidden):
        r = client.get("/api/draft/interviews?team=ATL&draft_season=2037")
    assert r.status_code == 403
