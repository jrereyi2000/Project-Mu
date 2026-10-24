"""server/front_office_api.py: the Team hub Front Office tab's cap, contracts, moves, draft-pick and staff blocks. The shaping tests need no
database; the endpoint test only reads the live one (skipped when Postgres is down) and writes nothing."""
import datetime
import unittest

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import front_office_api as fo


def contract(**kw):
    c = {"contract_id": 1, "player_id": 10, "name": "A Player", "position": "QB", "length": 4, "years_remaining": 2,
         "total_value": 40_000_000, "bonus": None, "cut_savings": 1_000_000, "cut_penalty": 2_000_000,
         "no_trade_clause": True, "incentive": "YES", "source": "SCAN",
         "scanned_at": datetime.datetime(2026, 10, 20), "capture_week": 4,
         "extension_years": None, "extension_value": None,
         "years": [(2037, 12_000_000, False), (2036, 10_000_000, False), (2038, 0, False)]}
    c.update(kw)
    return c


class ShapeTest(unittest.TestCase):
    def test_contract_terms_and_years(self):
        c = fo.shape_contract(contract(), 2036)
        self.assertEqual(c["cap_hit"], 10_000_000)
        self.assertEqual(c["through"], 2037)
        self.assertFalse(c["expiring"])
        self.assertIsNone(c["bonus"])                      # not known stays null, never $0
        self.assertTrue(c["no_trade_clause"] and c["incentive"])
        self.assertEqual([y["year"] for y in c["years"]], [2036, 2037, 2038])
        self.assertEqual(c["basis"], "scanned Week 4")
        self.assertIsNone(c["new_deal"])

    def test_contract_year_and_new_deal(self):
        c = fo.shape_contract(contract(years_remaining=1, extension_years=3, extension_value=30_000_000,
                                       years=[(2036, 5_000_000, False), (2037, 9_000_000, True)]), 2036)
        self.assertTrue(c["expiring"])
        self.assertEqual(c["new_deal"], {"years": 3, "total_value": 30_000_000, "starts": 2037})
        self.assertTrue(c["years"][1]["projected"])

    def test_signing_basis(self):
        c = fo.shape_contract(contract(source="TRANSACTION"), 2036)
        self.assertIn("from the signing", c["basis"])

    def test_committed_keeps_projected_apart(self):
        a = fo.shape_contract(contract(), 2036)
        b = fo.shape_contract(contract(player_id=11, years=[(2036, 1_000_000, False), (2037, 3_000_000, True)]), 2036)
        rows = {r["year"]: r for r in fo.committed([a, b], 2036)}
        self.assertEqual(len(rows), fo.YEAR_COLUMNS)
        self.assertEqual(rows[2036], {"year": 2036, "scanned": 11_000_000, "projected": 0, "players": 2})
        self.assertEqual(rows[2037], {"year": 2037, "scanned": 12_000_000, "projected": 3_000_000, "players": 2})
        self.assertEqual(rows[2038]["players"], 0)        # a $0 year isn't a commitment

    def test_cap_block(self):
        row = {"cap": 1, "adjusted_cap": 2, "cap_space": -3, "cap_space_rank": 32, "salaries": 4, "cap_penalty": 5,
               "total_spend": 6, "next_rookie_reserve": 7, "derived": True, "basis": "estimated: ..."}
        cap = fo.shape_cap(row, 32)
        self.assertEqual((cap["cap_space"], cap["dead_money"], cap["teams"]), (-3, 5, 32))
        self.assertTrue(cap["estimated"])

    def move(self, **kw):
        r = {"id": 5, "week": 3, "type": "SIGNED", "player_id": 10, "player_name": "JOHN DOE", "full_name": None,
             "position": "WR", "from_team_id": 2, "to_team_id": 1, "contract_years": 1, "contract_value": 900_000,
             "depth_slot": 4, "is_starter": False, "practice_squad": False, "notable": False}
        r.update(kw)
        return r

    def test_move_direction_and_origin(self):
        abbrs = {1: "CAR", 2: "ATL"}
        m = fo.shape_move(self.move(), 1, abbrs)
        self.assertEqual((m["direction"], m["from_team"], m["name"]), ("in", "ATL", "John Doe"))
        m = fo.shape_move(self.move(type="RELEASED", from_team_id=1, to_team_id=None, dead_money=2_000_000), 1, abbrs)
        self.assertEqual((m["direction"], m["from_team"], m["dead_money"]), ("out", None, 2_000_000))
        m = fo.shape_move(self.move(type="RE_SIGNED", from_team_id=None, new_deal_start=2037), 1, abbrs)
        self.assertEqual((m["direction"], m["new_deal_start"]), ("in", 2037))

    def test_trade_from_either_side(self):
        t = {"proposal_id": 9, "week": 5, "team_a": "CIN", "team_b": "GB", "legs": [
            {"type": "PLAYER", "from": "GB", "to": "CIN", "player_id": 60, "player_name": "Mike Keaton",
             "position": "FS", "cap_hit_now": 1_330_000},
            {"type": "PICK", "from": "CIN", "to": "GB", "pick": {"draft_season": 2037, "round": 5,
                                                                 "original_team": "CIN"}}]}
        cin, gb = fo.shape_trade(t, "CIN"), fo.shape_trade(t, "GB")
        self.assertEqual((cin["partner"], gb["partner"]), ("GB", "CIN"))
        self.assertEqual(cin["received"][0]["name"], "Mike Keaton")
        self.assertEqual(cin["sent"], gb["received"])
        self.assertEqual(cin["sent"][0], {"type": "PICK", "draft_season": 2037, "round": 5, "original_team": "CIN"})


class StaffShapeTest(unittest.TestCase):
    def test_needs_in_words_without_scores(self):
        needs = [{"group": "IOL", "score": 8.8, "injury": 3.0, "expiring": 4.0, "slot_gap": 6.0},
                 {"group": "QB", "score": 7.5, "injury": 0.0, "expiring": 0.0, "slot_gap": 6.0},
                 {"group": "S", "score": 5.7, "injury": 0.0, "expiring": 0.0, "slot_gap": 7.0},
                 {"group": "LB", "score": 5.1, "injury": 0.0, "expiring": 0.0, "slot_gap": 6.0}]
        top, urgent = fo.shape_needs(needs, {"group": "QB", "player_id": 3, "weeks_remaining": 4})
        self.assertEqual([n["group"] for n in top], ["IOL", "QB", "S"])
        self.assertEqual(top[0], {"group": "IOL", "label": "Interior line",
                                  "reasons": ["thin at the starting spots", "injuries", "expiring deals"]})
        self.assertEqual(top[1]["reasons"], ["thin at the starting spots"])
        self.assertEqual(urgent, {"group": "QB", "label": "Quarterback"})
        self.assertEqual(fo.shape_needs([], None), ([], None))

    def test_head_coach_tendencies(self):
        profile = {"coach": "A Coach", "slant": "DEFENSIVE", "with_team_since": 2034, "season_with_team": 3,
                   "replaced": None, "schemes": {"offense": "Spread", "defensive_front": "4-3"},
                   "this_season": {"regular_season": "5-4", "playoffs": None}, "record_with_team": None,
                   "playoff_record_with_team": None, "career": None, "super_bowls_with_team": [],
                   "coach_of_the_year": []}
        coach = {"pass_lean": "High", "fourth_down_aggressiveness": None, "man_coverage": "Low",
                 "observed": {"fourth_down_aggressiveness": ("Low", "measured in this league: 9 games this season")}}
        hc = fo.shape_head_coach(profile, coach)
        self.assertEqual(hc["tendencies"]["pass_lean"]["tier"], "High")
        self.assertFalse(hc["tendencies"]["pass_lean"]["measured"])     # the real-world tier, not measured here
        self.assertTrue(hc["tendencies"]["fourth_down_aggressiveness"]["measured"])
        self.assertEqual(hc["tendencies"]["man_coverage"]["meaning"], "zone-first coverage")
        self.assertIsNone(hc["record_with_team"])


def db_up():
    try:
        psycopg.connect(fo.CONN_INFO, connect_timeout=2).close()
        return True
    except psycopg.OperationalError:
        return False


@unittest.skipUnless(db_up(), "Postgres not reachable")
class EndpointTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(fo.router)
        self.client = TestClient(app)

    def test_team_payload(self):
        r = self.client.get("/api/team/car/front-office")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["team"]["abbreviation"], "CAR")
        self.assertEqual(len(data["year_columns"]), fo.YEAR_COLUMNS)
        if data["contracts"]:
            hits = [c["cap_hit"] or 0 for c in data["contracts"]]
            self.assertEqual(hits, sorted(hits, reverse=True))
            self.assertEqual(data["committed"][0]["year"], data["season"])

    def test_moves_and_picks(self):
        data = self.client.get("/api/team/CIN/front-office").json()
        if data["transactions"]:
            weeks = [m["week"] if m["week"] is not None else -1 for m in data["transactions"]["moves"]]
            self.assertEqual(weeks, sorted(weeks, reverse=True))
            self.assertNotIn("TRADED", {m["type"] for m in data["transactions"]["moves"]})
            for t in data["transactions"]["trades"]:
                self.assertNotEqual(t["partner"], "CIN")
                self.assertTrue(t["received"] or t["sent"])
        if data["draft_picks"]:
            picks = data["draft_picks"]["picks"]
            for d in data["draft_picks"]["drafts"]:
                own_or_gone = [p for p in picks if p["draft_season"] == d and p["status"] != "ACQUIRED"]
                self.assertEqual(len(own_or_gone), 7)        # every round of its own pick, kept or traded away
            for p in picks:
                self.assertEqual(p["status"] == "ACQUIRED", p["owner_team"] == "CIN" and p["original_team"] != "CIN")

    def test_staff(self):
        staff = self.client.get("/api/team/CAR/front-office").json()["staff"]
        if not staff:
            return
        self.assertTrue(staff["head_coach"]["name"])
        self.assertLessEqual(len(staff["needs"]), fo.TOP_NEEDS)
        for n in staff["needs"]:
            self.assertEqual(set(n), {"group", "label", "reasons"})       # no need scores
        if staff["gm"]:
            self.assertEqual(set(staff["gm"]), {"name", "bio", "since", "head_coach_too"})   # no persona dials
        self.assertIn(staff["direction"], (None, "CONTEND", "RETOOL", "REBUILD"))

    def test_alias_and_unknown_team(self):
        self.assertEqual(self.client.get("/api/team/ARI/front-office").json()["team"]["abbreviation"], "AZ")
        self.assertEqual(self.client.get("/api/team/XYZ/front-office").status_code, 404)


if __name__ == "__main__":
    unittest.main()
