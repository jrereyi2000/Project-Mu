"""server/franchise_api.py: every /api/franchise endpoint on fake data (no database). The DB readers are patched with
fixtures and the writes (franchise_pending_moves, contract_offers) go to a recording fake cursor, so the tests check
the exact JSON shapes (§2.7a and the module docstring), the preview math, the 409s and what gets queued or stored."""
import datetime
import json
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from engines import depth_chart_engine as dce
from server import franchise_api as fa

SEASON, WEEK = 2036, 5
CAR, ATL = 1, 2
NOW = datetime.datetime(2036, 10, 1, 12, 0)


class FakeCursor:
    """Records every execute; answers the INSERT ... RETURNING the module writes."""

    def __init__(self):
        self.executed = []
        self._row = None
        self.next_move = 100
        self.next_offer = 500

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "INSERT INTO franchise_pending_moves" in sql:
            self.next_move += 1
            self._row = (self.next_move, params[1], "PENDING", NOW, json.loads(params[2]))
        elif "INSERT INTO contract_offers" in sql:
            self.next_offer += 1
            self._row = (self.next_offer,)
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []

    def writes(self, table):
        return [(s, p) for s, p in self.executed if table in s and ("INSERT" in s or "UPDATE" in s)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, cur):
        self.cur = cur
        self.commits = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def contract_row(pid, length, remaining, total, bonus, years, ctype="STANDARD", drafted_round=None, team=CAR):
    return {"id": pid * 10, "player_id": pid, "team_id": team, "season": SEASON, "length": length,
            "years_remaining": remaining, "total_value": total, "bonus": bonus, "status": "ACTIVE",
            "contract_type": ctype, "guaranteed": 0, "signed_season": None, "signed_week": None,
            "last_restructure_season": None, "drafted_round": drafted_round, "years": years}


def player(pid, name, pos, ovr, age, yoe, contract=None, injury=None, archetype=None):
    return {"player_id": pid, "name": name, "position": pos, "age": age, "overall": ovr, "yoe": yoe,
            "archetype": archetype, "dev_trait": "Normal", "contract_row": contract, "injury": injury}


# QB: 4-year deal, $20M bonus ($5M a year), hits $30M/$30M/$25M in 2036-38; in 2036 he's in contract year 1.
QB = player(1, "Bryce Young", "QB", 84, 27, 4,
            contract_row(1, 4, 3, 100_000_000, 20_000_000, {2036: 30_000_000, 2037: 30_000_000, 2038: 25_000_000}))
# WR: $40M bonus ($10M a year) over 4 years, 2 left at $12M: cutting him costs more than his hit.
WR = player(2, "Old Receiver", "WR", 78, 31, 9,
            contract_row(2, 4, 2, 48_000_000, 40_000_000, {2036: 12_000_000, 2037: 12_000_000}))
RB = player(3, "Hurt Back", "HB", 72, 25, 3, contract_row(3, 2, 2, 4_000_000, 0, {2036: 2_000_000, 2037: 2_000_000}),
            injury={"injury": "Broken Leg", "weeks_out": 6, "on_ir": False})
LB = player(4, "Reserve Backer", "MLB", 70, 26, 4, contract_row(4, 1, 1, 1_500_000, 0, {2036: 1_500_000}),
            injury={"injury": "Torn ACL", "weeks_out": 10, "on_ir": True})
PS = player(5, "Squad Guard", "LG", 60, 23, 1)
VET = player(6, "Backup Passer", "QB", 70, 32, 10, contract_row(6, 2, 1, 6_000_000, 2_000_000, {2036: 3_000_000}))
ROOKIE = player(7, "Young Corner", "CB", 74, 22, 0,
                contract_row(7, 4, 4, 8_000_000, 2_000_000, {2036: 1_500_000, 2037: 2_000_000, 2038: 2_200_000,
                                                             2039: 2_300_000}, ctype="ROOKIE", drafted_round=1))
CAR_PLAYERS = [QB, WR, RB, LB, PS, VET, ROOKIE]
CAR_COMMITTED = 30_000_000 + 12_000_000 + 2_000_000 + 1_500_000 + 3_000_000 + 1_500_000
CAP = {SEASON: {"cap": 255_000_000, "cap_penalty": 1_000_000, "next_rookie_reserve": 10_000_000}}
ROOM = 255_000_000 - CAR_COMMITTED - 1_000_000

TEAMS = {"CAR": {"id": CAR, "abbr": "CAR", "name": "Panthers"}, "ATL": {"id": ATL, "abbr": "ATL", "name": "Falcons"},
         "AZ": {"id": 3, "abbr": "AZ", "name": "Cardinals"}}
QUEUED_KEYS = {"move_id", "kind", "status", "created_at", "player_id"}
ROSTER_PLAYER_KEYS = {"player_id", "name", "position", "age", "overall", "injury", "weeks_out", "contract", "cap_hit",
                      "ps_eligible", "ir_eligible", "pending_move"}
CONTRACT_KEYS = {"length", "years_left", "through", "total_value", "guaranteed", "contract_type"}
PREVIEW_KEYS = {"kind", "player_id", "name", "cap_hit", "cap_savings", "dead_money", "dead_money_next_year",
                "cap_room_before", "cap_room_after", "active_count_after", "practice_squad_count_after", "legal",
                "reasons"}
NEGOTIATION_KEYS = {"offer_id", "status", "utility", "bar", "parts", "counter", "note"}


def team_row(cur, abbr):
    clean = fa.ABBR_ALIASES.get(abbr.upper(), abbr.upper())
    if clean not in TEAMS:
        raise fa.HTTPException(404, f"Team '{abbr}' not found")
    return dict(TEAMS[clean])


class ApiTest(unittest.TestCase):
    players = CAR_PLAYERS

    def setUp(self):
        self.cur = FakeCursor()
        self.conn = FakeConn(self.cur)
        self.moves = []
        self.chart = (None, {})
        patches = {
            "_connect": lambda: self.conn,
            "user_teams": lambda: ["CAR"],
            "team_row": team_row,
            "league_state": lambda cur: (SEASON, WEEK),
            "roster_players": lambda cur, tid, s, w: [dict(p) for p in (self.players if tid == CAR else [])],
            "pending_moves": lambda cur, tid: list(self.moves),
            "cap_rows": lambda cur, tid, seasons: CAP if tid == CAR else {SEASON: {"cap": 255_000_000}},
            "stored_depth_chart": lambda cur, tid, s, w: self.chart,
            "team_scheme": lambda cur, tid, s: {},
            "player_ratings": lambda cur, ids, s: {},
            "market_fit": lambda cur, s: None,
            "team_context": lambda cur, tid, s: {"wins": 3, "losses": 1, "popularity_index": 60.0,
                                                 "direction": "CONTEND"},
        }
        self._patchers = [mock.patch.object(fa, k, v) for k, v in patches.items()]
        for p in self._patchers:
            p.start()
        app = FastAPI()
        app.include_router(fa.router)
        self.client = TestClient(app)

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def pending(self, kind, payload, move_id=7):
        self.moves.append({"move_id": move_id, "kind": kind, "status": "PENDING", "created_at": NOW,
                           "payload": payload})


class DepthChartTest(ApiTest):
    def check_shape(self, body, source):
        self.assertEqual(set(body), {"team", "season", "week", "source", "pending", "slots"})
        self.assertEqual((body["team"], body["season"], body["week"], body["source"]), ("CAR", SEASON, WEEK, source))
        self.assertEqual(set(body["slots"]), set(dce.POSITION_SLOTS))
        self.assertEqual(len(body["slots"]), 35)
        for entries in body["slots"].values():
            self.assertLessEqual(len(entries), 6)
            for e in entries:
                self.assertEqual(set(e), {"player_id", "name", "position", "overall", "injured"})
                self.assertIsInstance(e["injured"], bool)

    def test_stored_madden_chart(self):
        self.chart = ("MADDEN", {"QB": [1, 6], "HB": [3]})
        body = self.client.get("/api/franchise/CAR/depth-chart").json()
        self.check_shape(body, "MADDEN")
        self.assertIsNone(body["pending"])
        self.assertEqual([e["player_id"] for e in body["slots"]["QB"]], [1, 6])
        self.assertEqual(body["slots"]["QB"][0], {"player_id": 1, "name": "Bryce Young", "position": "QB",
                                                  "overall": 84, "injured": False})
        self.assertTrue(body["slots"]["HB"][0]["injured"])
        self.assertEqual(body["slots"]["WR"], [])

    def test_pending_jr_chart_wins(self):
        self.chart = ("MADDEN", {"QB": [1]})
        self.pending("DEPTH_CHART", {"slots": {"QB": [6, 1]}})
        body = self.client.get("/api/franchise/CAR/depth-chart").json()
        self.check_shape(body, "JR")
        self.assertEqual(set(body["pending"]), QUEUED_KEYS)
        self.assertIsNone(body["pending"]["player_id"])
        self.assertEqual([e["player_id"] for e in body["slots"]["QB"]], [6, 1])

    def test_no_chart_falls_back_to_staff(self):
        body = self.client.get("/api/franchise/CAR/depth-chart").json()
        self.check_shape(body, "MU_COACH")

    def test_put_queues_depth_chart_move(self):
        slots = {s: [] for s in dce.POSITION_SLOTS}
        slots["QB"] = [1, 6]
        r = self.client.put("/api/franchise/CAR/depth-chart", json={"slots": slots})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(set(body), QUEUED_KEYS)
        self.assertEqual((body["kind"], body["status"], body["player_id"]), ("DEPTH_CHART", "PENDING", None))
        self.assertEqual(body["created_at"], NOW.isoformat())
        inserts = [p for s, p in self.cur.writes("franchise_pending_moves") if "INSERT" in s]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0][1], "DEPTH_CHART")
        self.assertEqual(json.loads(inserts[0][2])["slots"]["QB"], [1, 6])
        self.assertTrue(any("CANCELLED" in s for s, _ in self.cur.writes("franchise_pending_moves")))
        self.assertEqual(self.conn.commits, 1)

    def test_put_refuses_bad_charts(self):
        slots = {s: [] for s in dce.POSITION_SLOTS}
        slots["QB"] = [999]
        self.assertEqual(self.client.put("/api/franchise/CAR/depth-chart", json={"slots": slots}).status_code, 400)
        slots["QB"] = [1, 1]
        self.assertEqual(self.client.put("/api/franchise/CAR/depth-chart", json={"slots": slots}).status_code, 400)
        del slots["QB"]
        r = self.client.put("/api/franchise/CAR/depth-chart", json={"slots": slots})
        self.assertEqual(r.status_code, 400)
        self.assertIn("detail", r.json())
        self.assertEqual(self.cur.writes("franchise_pending_moves"), [])

    def test_put_for_another_users_team_is_403(self):
        slots = {s: [] for s in dce.POSITION_SLOTS}
        self.assertEqual(self.client.put("/api/franchise/ATL/depth-chart", json={"slots": slots}).status_code, 403)

    def test_staff_returns_mu_coach_without_storing(self):
        r = self.client.post("/api/franchise/CAR/depth-chart/staff", json={})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.check_shape(body, "MU_COACH")
        self.assertIsNone(body["pending"])
        self.assertEqual(body["slots"]["QB"][0]["player_id"], 1)
        ids = {e["player_id"] for es in body["slots"].values() for e in es}
        self.assertNotIn(3, ids)                       # injured
        self.assertNotIn(5, ids)                       # practice squad
        self.assertFalse(any("INSERT" in s or "UPDATE" in s or "DELETE" in s for s, _ in self.cur.executed))
        self.assertEqual(self.conn.commits, 0)

    def test_unknown_team_404(self):
        r = self.client.get("/api/franchise/XXX/depth-chart")
        self.assertEqual(r.status_code, 404)
        self.assertIn("detail", r.json())

    def test_alias_abbreviation(self):
        self.assertEqual(self.client.get("/api/franchise/ARI/roster").json()["team"], "AZ")


class RosterCapTest(ApiTest):
    def test_roster_shape(self):
        self.pending("RELEASE", {"player_id": 2})
        body = self.client.get("/api/franchise/CAR/roster").json()
        self.assertEqual(set(body), {"team", "season", "week", "limits", "active", "practice_squad",
                                     "injured_reserve"})
        self.assertEqual(body["limits"], {"active": 65, "practice_squad": 12})
        self.assertEqual([p["player_id"] for p in body["active"]], [1, 2, 7, 3, 6])
        self.assertEqual([p["player_id"] for p in body["practice_squad"]], [5])
        self.assertEqual([p["player_id"] for p in body["injured_reserve"]], [4])
        for group in ("active", "practice_squad", "injured_reserve"):
            for p in body[group]:
                self.assertEqual(set(p), ROSTER_PLAYER_KEYS)
        qb = body["active"][0]
        self.assertEqual(set(qb["contract"]), CONTRACT_KEYS)
        self.assertEqual(qb["contract"], {"length": 4, "years_left": 3, "through": 2038, "total_value": 100_000_000,
                                          "guaranteed": 0, "contract_type": "STANDARD"})
        self.assertEqual(qb["cap_hit"], 30_000_000)
        self.assertIsNone(qb["injury"])
        self.assertIsNone(qb["weeks_out"])
        self.assertTrue(qb["ps_eligible"])             # 4 accrued, but a veteran slot is open
        squad = body["practice_squad"][0]
        self.assertIsNone(squad["contract"])
        self.assertIsNone(squad["cap_hit"])
        self.assertFalse(squad["ps_eligible"])
        rb = next(p for p in body["active"] if p["player_id"] == 3)
        self.assertEqual((rb["injury"], rb["weeks_out"], rb["ir_eligible"]), ("Broken Leg", 6, True))
        rookie = next(p for p in body["active"] if p["player_id"] == 7)
        self.assertTrue(rookie["ps_eligible"])
        self.assertEqual(body["active"][1]["pending_move"], "RELEASE")
        self.assertIsNone(body["active"][0]["pending_move"])

    def test_cap_shape_and_room(self):
        body = self.client.get("/api/franchise/CAR/cap?years=5").json()
        self.assertEqual(set(body), {"team", "season", "years"})
        self.assertEqual((body["team"], body["season"]), ("CAR", SEASON))
        self.assertEqual([y["season"] for y in body["years"]], list(range(2036, 2041)))
        for y in body["years"]:
            self.assertEqual(set(y), {"season", "cap", "committed", "dead_money", "rookie_reserve", "room",
                                      "estimated"})
            self.assertEqual(y["room"], y["cap"] - y["committed"] - y["dead_money"] - y["rookie_reserve"])
            for k in ("cap", "committed", "dead_money", "rookie_reserve", "room"):
                self.assertIsInstance(y[k], int)
        now, nxt = body["years"][0], body["years"][1]
        self.assertEqual((now["cap"], now["committed"], now["dead_money"], now["rookie_reserve"]),
                         (255_000_000, CAR_COMMITTED, 1_000_000, 0))
        self.assertFalse(now["estimated"])
        self.assertEqual(now["room"], ROOM)
        self.assertEqual(nxt["rookie_reserve"], 10_000_000)
        self.assertTrue(nxt["estimated"])
        self.assertEqual(nxt["committed"], 30_000_000 + 12_000_000 + 2_000_000 + 2_000_000)

    def test_cap_years_bounds(self):
        self.assertEqual(self.client.get("/api/franchise/CAR/cap?years=0").status_code, 400)


class MovesTest(ApiTest):
    def preview(self, kind, pid, **extra):
        r = self.client.post("/api/franchise/CAR/moves/preview",
                             json={"kind": kind, "payload": dict({"player_id": pid}, **extra)})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(set(body), PREVIEW_KEYS)
        return body

    def test_release_pre_june1(self):
        p = self.preview("RELEASE", 1)
        self.assertEqual((p["cap_hit"], p["dead_money"], p["dead_money_next_year"], p["cap_savings"]),
                         (30_000_000, 15_000_000, 0, 15_000_000))
        self.assertEqual(p["cap_room_before"], ROOM)
        self.assertEqual(p["cap_room_after"], ROOM + 15_000_000)
        self.assertEqual((p["active_count_after"], p["practice_squad_count_after"]), (4, 1))
        self.assertTrue(p["legal"])
        self.assertEqual(p["name"], "Bryce Young")

    def test_release_post_june1_splits_dead_money(self):
        p = self.preview("RELEASE", 1, post_june1=True)
        self.assertEqual((p["dead_money"], p["dead_money_next_year"], p["cap_savings"]),
                         (5_000_000, 10_000_000, 25_000_000))
        self.assertTrue(any("post-June 1" in r for r in p["reasons"]))

    def test_release_negative_savings(self):
        p = self.preview("RELEASE", 2)
        self.assertEqual((p["cap_hit"], p["dead_money"], p["cap_savings"]), (12_000_000, 20_000_000, -8_000_000))
        self.assertEqual(p["cap_room_after"], ROOM - 8_000_000)
        self.assertTrue(p["legal"])

    def test_ps_demote_and_promote(self):
        p = self.preview("PS_DEMOTE", 7)
        self.assertTrue(p["legal"], p["reasons"])
        self.assertEqual((p["active_count_after"], p["practice_squad_count_after"]), (4, 2))
        self.assertTrue(any(r.startswith("PS eligibility") for r in p["reasons"]))
        p = self.preview("PS_PROMOTE", 5)
        self.assertTrue(p["legal"], p["reasons"])
        self.assertIsNone(p["cap_hit"])
        self.assertLess(p["cap_savings"], 0)
        self.assertEqual(p["active_count_after"], 6)

    def test_ir_rules(self):
        self.assertTrue(self.preview("IR_PLACE", 3)["legal"])
        p = self.preview("IR_PLACE", 1)
        self.assertFalse(p["legal"])
        self.assertTrue(p["reasons"])
        p = self.preview("IR_ACTIVATE", 4)
        self.assertFalse(p["legal"])                   # still 10 weeks out
        self.assertEqual(p["cap_savings"], 0)

    def test_queue_move(self):
        r = self.client.post("/api/franchise/CAR/moves", json={"kind": "RELEASE", "payload": {"player_id": 2,
                                                                                               "post_june1": True}})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(set(body), QUEUED_KEYS)
        self.assertEqual((body["kind"], body["status"], body["player_id"]), ("RELEASE", "PENDING", 2))
        sql, params = self.cur.writes("franchise_pending_moves")[0]
        self.assertIn("'PENDING'", sql)
        payload = json.loads(params[2])
        self.assertEqual((payload["player_id"], payload["post_june1"]), (2, True))
        self.assertEqual(payload["preview"]["dead_money_next_year"], 10_000_000)
        self.assertNotIn("_problems", payload["preview"])
        self.assertEqual(self.conn.commits, 1)

    def test_illegal_move_is_409(self):
        r = self.client.post("/api/franchise/CAR/moves", json={"kind": "IR_PLACE", "payload": {"player_id": 1}})
        self.assertEqual(r.status_code, 409)
        self.assertIn("IR", r.json()["detail"])
        self.assertEqual(self.cur.writes("franchise_pending_moves"), [])

    def test_already_pending_is_409(self):
        self.pending("RELEASE", {"player_id": 1})
        r = self.client.post("/api/franchise/CAR/moves", json={"kind": "RELEASE", "payload": {"player_id": 1}})
        self.assertEqual(r.status_code, 409)

    def test_unknown_kind_and_player(self):
        r = self.client.post("/api/franchise/CAR/moves/preview", json={"kind": "TRADE", "payload": {"player_id": 1}})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/franchise/CAR/moves/preview", json={"kind": "RELEASE", "payload": {"player_id": 99}})
        self.assertEqual(r.status_code, 404)


class PreviewMathTest(unittest.TestCase):
    def test_promote_over_cap_is_illegal(self):
        p = fa.with_contract(dict(PS), SEASON)
        roster = {"ACTIVE": [], "PS": [p], "IR": []}
        out = fa.preview_move("PS_PROMOTE", p, roster, 100_000, SEASON, SEASON, False, {})
        self.assertFalse(out["legal"])
        self.assertLess(out["cap_room_after"], 0)

    def test_full_practice_squad(self):
        squad = [fa.with_contract(player(100 + i, f"P{i}", "WR", 50, 22, 0), SEASON) for i in range(12)]
        rookie = fa.with_contract(dict(ROOKIE), SEASON)
        roster = {"ACTIVE": [rookie], "PS": squad, "IR": []}
        out = fa.preview_move("PS_DEMOTE", rookie, roster, 1_000_000, SEASON, SEASON, False, {})
        self.assertFalse(out["legal"])
        self.assertEqual(out["practice_squad_count_after"], 13)

    def test_veteran_slots(self):
        vets = [fa.with_contract(player(100 + i, f"V{i}", "WR", 50, 28, 5), SEASON) for i in range(4)]
        vet = fa.with_contract(dict(VET), SEASON)
        self.assertEqual(fa.ps_eligibility(vet, SEASON, SEASON, fa.ps_veterans(vets, SEASON, SEASON))[0], False)
        self.assertEqual(fa.ps_eligibility(vet, SEASON, SEASON, 3)[0], True)

    def test_stage_names(self):
        self.assertEqual((fa.stage_for(5), fa.stage_for(20), fa.stage_for(26), fa.stage_for(27)),
                         ("REGULAR_WEEK", "PLAYOFF_WEEK", "FREE_AGENCY", "FREE_AGENCY"))
        self.assertEqual(fa.league_year(2036, 25), 2037)


class MedicalOverviewTest(ApiTest):
    def test_medical_shape(self):
        body = self.client.get("/api/franchise/CAR/medical").json()
        self.assertEqual(set(body), {"team", "season", "week", "players"})
        keys = {"player_id", "name", "position", "overall", "injury", "weeks_out", "status", "on_ir", "ir_eligible",
                "pending_move"}
        for p in body["players"]:
            self.assertEqual(set(p), keys)
            self.assertIsInstance(p["weeks_out"], int)
        self.assertEqual([p["player_id"] for p in body["players"][:2]], [4, 3])
        lb, rb = body["players"][0], body["players"][1]
        self.assertEqual((lb["status"], lb["on_ir"], lb["weeks_out"]), ("OUT", True, 10))
        self.assertEqual((rb["status"], rb["ir_eligible"]), ("OUT", True))
        qb = next(p for p in body["players"] if p["player_id"] == 1)
        self.assertEqual((qb["status"], qb["injury"], qb["weeks_out"]), ("HEALTHY", None, 0))

    def test_overview(self):
        games = [(10, 4, CAR, ATL, 24, 17, "COMPLETED", "Sun 1:00 PM", "REGULAR_SEASON"),
                 (11, 5, 3, CAR, 0, 0, "SCHEDULED", "Sun 4:25 PM", "REGULAR_SEASON")]
        self.pending("RELEASE", {"player_id": 2})
        with mock.patch.object(fa, "season_games", lambda cur, s: games), \
                mock.patch.object(fa, "team_names", lambda cur: {t["id"]: t for t in TEAMS.values()}):
            body = self.client.get("/api/franchise/CAR/overview").json()
        self.assertEqual(set(body), {"team", "season", "week", "league_year", "user_controlled", "record", "cap",
                                     "roster_counts", "next_game", "pending_moves"})
        self.assertEqual(body["record"], {"wins": 1, "losses": 0, "ties": 0})
        self.assertEqual(body["cap"]["room"], ROOM)
        self.assertEqual(body["roster_counts"]["active"], 5)
        self.assertEqual(body["next_game"]["opponent"]["abbr"], "AZ")
        self.assertFalse(body["next_game"]["home"])
        self.assertEqual(set(body["pending_moves"][0]), QUEUED_KEYS | {"payload"})
        self.assertTrue(body["user_controlled"])


class MarketTest(ApiTest):
    FA = {"player_id": 50, "name": "Street Corner", "position": "CB", "age": 26, "overall": 76, "yoe": 4,
          "archetype": None, "dev_trait": "Normal"}

    def test_free_agents_shape(self):
        with mock.patch.object(fa, "free_agent_rows", lambda cur, s, *a: [dict(self.FA)]):
            body = self.client.get("/api/franchise/free-agents?team=CAR&position=CB").json()
        self.assertEqual(set(body), {"season", "week", "in_season", "players"})
        self.assertTrue(body["in_season"])
        p = body["players"][0]
        self.assertEqual(set(p), {"player_id", "name", "position", "age", "overall", "dev_trait", "scheme_fit",
                                  "market_worth", "asking"})
        self.assertEqual(set(p["asking"]), {"years", "total_value", "guaranteed"})
        self.assertEqual(p["asking"]["years"], 2)
        self.assertEqual(p["scheme_fit"], 0.5)
        self.assertIsNone(p["market_worth"])           # no market fit on fake data

    def test_offer_scored_and_stored(self):
        fa_row = dict(self.FA, is_free_agent=True)
        with mock.patch.object(fa, "free_agent", lambda cur, s, pid: fa_row):
            r = self.client.post("/api/franchise/CAR/offers", json={"player_id": 50, "years": 2,
                                                                    "total_value": 6_000_000, "bonus": 1_000_000,
                                                                    "guaranteed": 2_000_000})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(set(body), NEGOTIATION_KEYS)
        self.assertIn(body["status"], ("ACCEPT", "REJECT", "COUNTER"))
        self.assertEqual(set(body["parts"]), {"financial", "opportunity", "contention", "scheme", "persona"})
        self.assertIsInstance(body["utility"], float)
        self.assertIsInstance(body["bar"], float)
        (sql, params), = self.cur.writes("contract_offers")
        self.assertIn("true", sql)                      # from_user
        self.assertEqual(params[2], "FREE_AGENT")
        self.assertEqual(params[3:8], (50, CAR, 2, 6_000_000, 1_000_000))
        self.assertEqual(params[9], body["utility"])
        self.assertEqual(params[10], {"ACCEPT": "ACCEPTED", "REJECT": "REJECTED", "COUNTER": "COUNTERED"}[body["status"]])
        self.assertEqual(body["offer_id"], 501)
        self.assertEqual(self.cur.writes("franchise_pending_moves"), [])   # not a signing

    def test_offer_refusals(self):
        with mock.patch.object(fa, "free_agent", lambda cur, s, pid: dict(self.FA, is_free_agent=False)):
            r = self.client.post("/api/franchise/CAR/offers", json={"player_id": 50, "years": 2,
                                                                    "total_value": 6_000_000})
        self.assertEqual(r.status_code, 409)
        r = self.client.post("/api/franchise/CAR/offers", json={"player_id": 50, "years": 6, "total_value": 1})
        self.assertEqual(r.status_code, 400)
        with mock.patch.object(fa, "free_agent", lambda cur, s, pid: dict(self.FA, is_free_agent=True)):
            r = self.client.post("/api/franchise/CAR/offers", json={"player_id": 50, "years": 1,
                                                                    "total_value": 900_000_000})
        self.assertEqual(r.status_code, 409)          # doesn't fit the cap


class ContractsTest(ApiTest):
    def test_expiring(self):
        with mock.patch.object(fa, "salaries_by_position",
                               lambda cur, s, T: {"QB": [50_000_000, 45_000_000, 40_000_000, 30_000_000, 3_000_000],
                                                  "CB": [20_000_000, 15_000_000, 10_000_000, 5_000_000]}):
            body = self.client.get("/api/franchise/CAR/contracts/expiring").json()
        self.assertEqual(set(body), {"team", "season", "week", "players"})
        ids = [p["player_id"] for p in body["players"]]
        self.assertEqual(ids[:1], [6])                  # the expiring deal first
        vet = body["players"][0]
        self.assertEqual(set(vet), {"player_id", "name", "position", "overall", "age", "contract", "cap_hit",
                                    "eligible", "eligible_reason", "options"})
        self.assertTrue(vet["eligible"])
        self.assertEqual(set(vet["contract"]), CONTRACT_KEYS)
        self.assertEqual(vet["options"]["tag_value"], 33_600_000)           # top-5 QB average
        self.assertIn("transition_tag_value", vet["options"])
        self.assertNotIn(1, ids)                        # not eligible, not expiring

    def test_fifth_year_option(self):
        p = fa.with_contract(dict(ROOKIE), 2038)       # contract year index 2, not his final year
        opt = fa.contract_options(p, 2038, {"CB": [20_000_000, 15_000_000, 10_000_000, 5_000_000]})
        self.assertEqual(opt["fifth_year_option"]["tier"], 4)
        self.assertNotIn("tag_value", opt)

    def test_propose(self):
        r = self.client.post("/api/franchise/CAR/contracts/6/propose",
                             json={"years": 2, "total_value": 8_000_000, "bonus": 2_000_000, "guaranteed": 4_000_000})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(set(r.json()), NEGOTIATION_KEYS)
        (sql, params), = self.cur.writes("contract_offers")
        self.assertEqual((params[2], params[3]), ("RESIGN", 6))
        self.assertIn("true", sql)

    def test_propose_ineligible_409(self):
        r = self.client.post("/api/franchise/CAR/contracts/1/propose", json={"years": 3, "total_value": 90_000_000})
        self.assertEqual(r.status_code, 409)
        self.assertIn("veteran", r.json()["detail"])

    def test_restructure_preview(self):
        r = self.client.post("/api/franchise/CAR/contracts/1/restructure-preview", json={"amount": 10_000_000})
        body = r.json()
        self.assertEqual(set(body), {"player_id", "amount", "max_amount", "cap_hit_before", "cap_hit_after",
                                     "cap_savings_now", "added_dead_money_future", "by_year", "legal", "reasons"})
        self.assertEqual(body["max_amount"], 22_500_000)
        self.assertEqual(body["cap_hit_before"], 30_000_000)
        self.assertEqual(body["cap_savings_now"], 10_000_000 - 3_333_334)
        self.assertEqual(body["added_dead_money_future"], 6_666_666)
        self.assertEqual([y["season"] for y in body["by_year"]], [2036, 2037, 2038])
        self.assertEqual(set(body["by_year"][0]), {"season", "cap_hit_before", "cap_hit_after"})
        self.assertTrue(body["legal"])
        body = self.client.post("/api/franchise/CAR/contracts/1/restructure-preview", json={"amount": 30_000_000}).json()
        self.assertFalse(body["legal"])
        self.assertEqual(body["cap_savings_now"], 0)
        self.assertTrue(body["reasons"])

    def test_picks(self):
        with mock.patch.object(fa, "pick_rows", lambda cur, tid, s, n: [(2037, 1, "CAR", None), (2037, 2, "ATL", 40)]):
            body = self.client.get("/api/franchise/CAR/picks?seasons=3").json()
        self.assertEqual(body, {"team": "CAR", "picks": [
            {"season": 2037, "round": 1, "original_team": "CAR", "overall_pick": None},
            {"season": 2037, "round": 2, "original_team": "ATL", "overall_pick": 40}]})


class TradeTest(ApiTest):
    KEYS = {"surplus_us", "surplus_them", "verdict", "counter", "cap_legal_us", "cap_legal_them", "cap_room_after_us",
            "cap_room_after_them", "reasons", "dialogue"}

    def evaluate(self, response, body=None):
        seen = {}

        def negotiate(cur, b, valuer, user, other, assets):
            seen["assets"] = assets
            eng = [{"type": "PLAYER", "player_id": 1, "from": CAR, "to": ATL},
                   {"type": "PICK", "pick": (2037, 2, ATL), "from": ATL, "to": CAR}]
            return {"engine_assets": eng, "response": response, "dialogue": "GM: hello"}

        with mock.patch.object(fa, "trade_books", lambda cur: ({}, None)), \
                mock.patch.object(fa, "negotiate_trade", negotiate), \
                mock.patch.object(fa, "contract_rows", lambda cur, s, ids: {1: QB["contract_row"]}):
            r = self.client.post("/api/franchise/CAR/trades/evaluate", json=body or {
                "other_team": "ATL", "send": {"players": [1], "picks": []},
                "receive": {"players": [], "picks": [{"season": 2037, "round": 2, "original_team": "ATL"}]}})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json(), seen

    def test_walk_away_is_reject_with_cap(self):
        body, seen = self.evaluate({"action": "WALK_AWAY", "judge": {"su": 5_000_000, "sg": -9_000_000,
                                                                     "mood": "INSULTED"}, "reasons": []})
        self.assertEqual(set(body), self.KEYS)
        self.assertEqual(body["verdict"], "REJECT")
        self.assertEqual((body["surplus_us"], body["surplus_them"]), (5_000_000, -9_000_000))
        self.assertIsNone(body["counter"])
        self.assertTrue(body["cap_legal_us"] and body["cap_legal_them"])
        # CAR sends a $30M hit and keeps $15M of bonus proration as dead money; ATL takes the $30M hit.
        self.assertEqual(body["cap_room_after_us"], ROOM + 30_000_000 - 15_000_000)
        self.assertEqual(body["cap_room_after_them"], 255_000_000 - 30_000_000)
        self.assertEqual(body["dialogue"], "GM: hello")
        self.assertEqual(seen["assets"][0], {"type": "PLAYER", "player_id": 1, "from": "CAR", "to": "ATL"})
        self.assertEqual(seen["assets"][1]["from"], "ATL")

    def test_counter_from_our_side(self):
        counter = {"assets": [{"type": "PLAYER", "player_id": 1, "from": CAR, "to": ATL},
                              {"type": "PICK", "pick": (2037, 3, CAR), "from": CAR, "to": ATL}]}
        body, _ = self.evaluate({"action": "COUNTER", "judge": {"su": 1, "sg": 2, "mood": "CLOSE"},
                                 "counter": counter, "reasons": []})
        self.assertEqual(body["verdict"], "COUNTER")
        self.assertEqual(body["counter"], {"send": {"players": [1], "picks": [{"season": 2037, "round": 3,
                                                                               "original_team": "CAR"}]},
                                           "receive": {"players": [], "picks": []}})

    def test_empty_trade_400(self):
        r = self.client.post("/api/franchise/CAR/trades/evaluate", json={"other_team": "ATL"})
        self.assertEqual(r.status_code, 400)


class GameTest(ApiTest):
    def test_game_center(self):
        games = [(10, 4, CAR, ATL, 24, 17, "COMPLETED", "Sun 1:00 PM", "REGULAR_SEASON"),
                 (11, 5, ATL, CAR, 0, 0, "SCHEDULED", "Sun 4:25 PM", "REGULAR_SEASON")]
        rows = [(CAR, 1, 24, 17, 380, 300, 250, 200, 130, 100, 1, 2),
                (ATL, 1, 17, 24, 300, 380, 200, 250, 100, 130, 2, 1)]
        with mock.patch.object(fa, "season_games", lambda cur, s: games), \
                mock.patch.object(fa, "team_names", lambda cur: {t["id"]: t for t in TEAMS.values()}), \
                mock.patch.object(fa, "rank_rows", lambda cur, s: rows), \
                mock.patch.object(fa, "team_ratings", lambda cur, tid, s: {"team_ovr": 80}):
            body = self.client.get("/api/franchise/CAR/game-center").json()
        self.assertEqual(set(body), {"team", "season", "week", "record", "next_game", "opponent", "ratings", "ranks",
                                     "matchups"})
        self.assertEqual(body["opponent"]["abbr"], "ATL")
        self.assertEqual(body["opponent"]["record"], {"wins": 0, "losses": 1, "ties": 0})
        self.assertEqual(body["ranks"]["team"]["points_for"], {"value": 24.0, "rank": 1})
        self.assertEqual(body["ranks"]["opponent"]["points_against"], {"value": 24.0, "rank": 2})
        self.assertEqual(len(body["matchups"]), 6)
        self.assertEqual(set(body["matchups"][0]), {"unit", "team_stat", "team_value", "team_rank", "opponent_stat",
                                                    "opponent_value", "opponent_rank", "edge"})

    def test_launch_calls_game_launch(self):
        out = {"pack": "Mu FOX Tier2 Mudded", "command": "cmd.exe ...", "started": False, "refused": None,
               "game": None}
        with mock.patch("pipeline.game_launch.launch", return_value=out) as launch:
            r = self.client.post("/api/franchise/CAR/game/launch", json={"dry_run": True})
        self.assertEqual(r.json(), out)
        args, kwargs = launch.call_args
        self.assertEqual(args[:3], ("CAR", None, None))
        self.assertTrue(kwargs["dry_run"])


class NoSaveWritesTest(unittest.TestCase):
    def test_module_never_touches_the_writer(self):
        import inspect
        src = inspect.getsource(fa)
        self.assertNotIn("save_writer.mjs", src.replace("calls save_writer", ""))
        self.assertNotIn("subprocess", src)


if __name__ == "__main__":
    unittest.main()
