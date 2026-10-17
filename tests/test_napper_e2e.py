"""End-to-End Test Suite for Napper Fantasy Platform (tests/test_napper_e2e.py).

Covers complete end-to-end flows:
1. League Overview: Fetching profile and all 3 reference leagues with accurate scores and records.
2. Matchup Week 6: Verifying split starter comparison table, slot alignment (QB, RB, WR, etc.), and bench.
3. Week Switching: Verifying seamless switching across weeks 1 to 17 without 404s.
4. Atomic Lineup Swaps: Verifying validation of illegal moves, atomic starter-bench swaps, and updated projections.
5. Trade Lifecycle: Proposing a trade, accepting it, verifying roster transfers and league chat announcement.
6. Waivers Lifecycle: Fetching free agents, submitting add/drop claim, verifying roster changes and chat alert.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import psycopg
from psycopg.rows import dict_row

from server.api import app
from server import napper_api

# Ensure napper_api router is mounted on app
if not any(getattr(r, "path", "").startswith("/api/napper") for r in app.routes):
    app.include_router(napper_api.router)

client = TestClient(app)
DATABASE_URL = napper_api.DATABASE_URL


def get_db_cursor():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


class TestNapperE2E:
    """Complete E2E test suite for Napper API and simulation workflows."""

    @classmethod
    def setup_class(cls):
        from engines.napper_seeder import seed_napper_database
        seed_napper_database()

    def test_01_user_profile_and_all_leagues(self):
        """Verify profile and all 3 reference leagues return accurate records and live scores."""
        # 1. Profile
        res = client.get("/api/napper/profile")
        assert res.status_code == 200
        prof_data = res.json()
        assert "profile" in prof_data
        assert prof_data["profile"]["handle"] == "jrereyi2000"
        assert "pending_trades_count" in prof_data

        # 2. Leagues
        res = client.get("/api/napper/leagues")
        assert res.status_code == 200
        data = res.json()
        assert "leagues" in data
        leagues = {lg["name"]: lg for lg in data["leagues"]}

        # All 3 reference leagues must be present
        assert "Solo 2026 8-Team SF PPR" in leagues
        assert "The Justice League" in leagues
        assert "The Avengers" in leagues

        # Solo league checks
        solo = leagues["Solo 2026 8-Team SF PPR"]
        assert solo["record"] == "5-0"
        assert solo["status"] == "IN_SEASON"
        assert solo["current_week"] == 6
        assert solo["user_team_name"] == "The Straw Hat Pirates"
        assert solo["opponent_team_name"] == "Team 3"
        assert solo["user_score"] >= 150.0
        assert solo["opponent_score"] >= 100.0
        assert solo["unplayed_count"] == 10

        # Justice League checks
        justice = leagues["The Justice League"]
        assert justice["record"] in ("2-3", "3-2", "4-1")
        assert justice["status"] == "IN_SEASON"
        assert justice["teams_count"] == 12

        # Avengers checks
        avengers = leagues["The Avengers"]
        assert avengers["record"] == "0-0"
        assert avengers["status"] == "PRE_DRAFT"
        assert avengers["unplayed_count"] == 0

    def test_02_matchup_week_6_starters_and_bench(self):
        """Verify Week 6 matchup has exactly 10 starter slots, proper player pairing, and bench players."""
        res = client.get("/api/napper/league/1/matchup?week=6")
        assert res.status_code == 200
        data = res.json()

        assert data["league_id"] == 1
        assert data["week"] == 6

        # Teams header
        left = data["left_team"]
        right = data["right_team"]
        assert left["team_name"] == "The Straw Hat Pirates"
        assert left["manager_handle"] == "jrereyi2000"
        assert left["win_prob"] >= 70

        assert right["team_name"] == "Team 3"
        assert right["win_prob"] <= 30

        # Starters table: exactly 10 slots
        starters = data["starters"]
        assert len(starters) == 10

        slot_names = [s["slot"] for s in starters]
        expected_slots = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPERFLEX", "K", "DEF"]
        assert slot_names == expected_slots

        # QB match: verify both left and right team have starting QBs with projections
        qb_slot = starters[0]
        assert qb_slot["slot"] == "QB"
        assert len(qb_slot["left_player"]["name"]) > 0
        assert qb_slot["left_player"]["position"] == "QB"
        assert qb_slot["left_player"]["proj_fpts"] > 15.0
        assert len(qb_slot["right_player"]["name"]) > 0
        assert qb_slot["right_player"]["position"] == "QB"

        # Bench players
        bench = data["bench"]
        assert len(bench) >= 6
        bench_names = [b["left_player"]["name"] for b in bench if b["left_player"]]
        assert len(bench_names) >= 4

    def test_03_week_switching_1_to_17(self):
        """Verify seamless week switching from week 1 through week 17 without missing matchups or rosters."""
        for wk in [1, 3, 6, 10, 14, 15, 16, 17]:
            res = client.get(f"/api/napper/league/1/matchup?week={wk}")
            assert res.status_code == 200, f"Failed on week {wk}"
            data = res.json()
            assert data["week"] == wk
            assert len(data["starters"]) == 10
            assert len(data["bench"]) >= 6

            # Verify players are populated
            starter_players = [s["left_player"] for s in data["starters"] if s["left_player"]]
            assert len(starter_players) == 10

            if wk < 6:
                assert starter_players[0]["game_status"] == "Final"
            else:
                assert starter_players[0]["game_status"] == "Yet to play"

    def test_04_atomic_lineup_swaps_and_validation(self):
        """Verify atomic validation of illegal moves, valid starter/bench swaps, and matchup updates."""
        # Query current matchup for week 6
        res = client.get("/api/napper/league/1/matchup?week=6")
        data = res.json()

        left_starters = [s["left_player"] for s in data["starters"] if s["left_player"]]
        left_bench = [b["left_player"] for b in data["bench"] if b["left_player"]]

        # Find starter and bench players
        starter_p = left_starters[1]  # RB slot
        starter_pid = starter_p["id"]
        starter_slot = starter_p["slot"] or "RB"

        bench_p = next(p for p in left_bench if p["position"] == "RB" and p["id"] != starter_pid)
        bench_pid = bench_p["id"]

        # 1. Invalid player validation
        bad_player_res = client.post("/api/napper/league/1/lineup", json={
            "team_id": 1,
            "swaps": [{"player_id": 9999999, "new_slot": "RB"}]
        })
        assert bad_player_res.status_code == 400
        assert "not on team" in bad_player_res.json()["detail"]

        # 2. Invalid slot validation
        bad_slot_res = client.post("/api/napper/league/1/lineup", json={
            "team_id": 1,
            "swaps": [{"player_id": starter_pid, "new_slot": "INVALID_SLOT"}]
        })
        assert bad_slot_res.status_code == 400
        assert "Invalid slot" in bad_slot_res.json()["detail"]

        # 3. Valid atomic swap: swap starter with bench player
        swap_res = client.post("/api/napper/league/1/lineup", json={
            "team_id": 1,
            "week": 6,
            "swaps": [
                {"player_id": starter_pid, "new_slot": "BN"},
                {"player_id": bench_pid, "new_slot": starter_slot},
            ]
        })
        assert swap_res.status_code == 200
        assert swap_res.json()["status"] == "ok"
        assert swap_res.json()["updated"] == 2

        # 4. Verify updated matchup reflects the change
        updated_res = client.get("/api/napper/league/1/matchup?week=6")
        updated_data = updated_res.json()
        updated_starters = [s["left_player"]["id"] for s in updated_data["starters"] if s["left_player"]]
        updated_bench = [b["left_player"]["id"] for b in updated_data["bench"] if b["left_player"]]

        assert bench_pid in updated_starters
        assert starter_pid in updated_bench

        # Revert swap to leave roster in clean state
        client.post("/api/napper/league/1/lineup", json={
            "team_id": 1,
            "week": 6,
            "swaps": [
                {"player_id": bench_pid, "new_slot": "BN"},
                {"player_id": starter_pid, "new_slot": starter_slot},
            ]
        })

    def test_05_trade_lifecycle_propose_accept_and_chat(self):
        """Verify full trade flow: propose trade, accept response, roster transfer, and chat notice."""
        with get_db_cursor() as conn:
            with conn.cursor() as cur:
                # Find bench players on Team 1 and Team 3
                cur.execute("""
                    SELECT player_id FROM napper_rosters
                    WHERE league_id = 1 AND team_id = 1 AND slot = 'BN' AND week = 6
                    LIMIT 1
                """)
                t1_player_id = cur.fetchone()["player_id"]

                cur.execute("""
                    SELECT player_id FROM napper_rosters
                    WHERE league_id = 1 AND team_id = 3 AND slot = 'BN' AND week = 6
                    LIMIT 1
                """)
                t3_player_id = cur.fetchone()["player_id"]

        # 1. Propose trade
        prop_res = client.post("/api/napper/league/1/trades/propose", json={
            "proposer_team_id": 1,
            "target_team_id": 3,
            "offered_player_ids": [t1_player_id],
            "requested_player_ids": [t3_player_id],
            "ai_rationale": "Mutually beneficial depth swap."
        })
        assert prop_res.status_code == 200
        trade_id = prop_res.json()["trade_id"]
        assert trade_id > 0

        # 2. Check trades list
        trades_res = client.get("/api/napper/league/1/trades")
        assert trades_res.status_code == 200
        trades = trades_res.json()["trades"]
        matched_trade = next((t for t in trades if t["id"] == trade_id), None)
        assert matched_trade is not None
        assert matched_trade["status"] == "PENDING"
        assert matched_trade["proposer"]["id"] == 1
        assert matched_trade["target"]["id"] == 3

        # 3. Respond with ACCEPT
        resp_res = client.post(f"/api/napper/league/1/trades/{trade_id}/respond", json={
            "action": "ACCEPT"
        })
        assert resp_res.status_code == 200
        assert resp_res.json()["trade_status"] == "ACCEPTED"

        # 4. Verify rosters swapped
        with get_db_cursor() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT team_id FROM napper_rosters
                    WHERE league_id = 1 AND player_id = %s AND week = 6
                """, (t1_player_id,))
                assert cur.fetchone()["team_id"] == 3  # Moved to team 3

                cur.execute("""
                    SELECT team_id FROM napper_rosters
                    WHERE league_id = 1 AND player_id = %s AND week = 6
                """, (t3_player_id,))
                assert cur.fetchone()["team_id"] == 1  # Moved to team 1

        # 5. Verify chat announcement
        chat_res = client.get("/api/napper/league/1/chat")
        assert chat_res.status_code == 200
        messages = chat_res.json()["messages"]
        trade_msgs = [m for m in messages if "Trade Completed" in m["message_text"]]
        assert len(trade_msgs) > 0

    def test_06_waiver_claim_and_chat_announcement(self):
        """Verify waivers flow: claim free agent, drop bench player, verify roster updates and chat alert."""
        # 1. Fetch available free agents
        waivers_res = client.get("/api/napper/league/1/waivers")
        assert waivers_res.status_code == 200
        free_agents = waivers_res.json()["players"]
        assert len(free_agents) > 0

        claim_player = free_agents[0]
        claim_pid = claim_player["player_id"]
        claim_name = claim_player["name"]

        # 2. Get a bench player from team 1 to drop
        with get_db_cursor() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT player_id FROM napper_rosters
                    WHERE league_id = 1 AND team_id = 1 AND slot = 'BN' AND week = 6
                    LIMIT 1
                """)
                drop_pid = cur.fetchone()["player_id"]

        # 3. Invalid claim: trying to drop a player not on the team
        bad_drop_res = client.post("/api/napper/league/1/waivers/claim", json={
            "team_id": 1,
            "add_player_id": claim_pid,
            "drop_player_id": 9999999,
        })
        assert bad_drop_res.status_code == 400

        # 4. Valid waiver claim
        claim_res = client.post("/api/napper/league/1/waivers/claim", json={
            "team_id": 1,
            "add_player_id": claim_pid,
            "drop_player_id": drop_pid,
        })
        assert claim_res.status_code == 200
        claim_data = claim_res.json()
        assert claim_data["status"] == "success"
        assert claim_data["added_player_id"] == claim_pid
        assert claim_data["dropped_player_id"] == drop_pid

        # 5. Verify roster updates
        with get_db_cursor() as conn:
            with conn.cursor() as cur:
                # Add player is now on team 1
                cur.execute("""
                    SELECT slot FROM napper_rosters
                    WHERE league_id = 1 AND team_id = 1 AND player_id = %s AND week = 6
                """, (claim_pid,))
                row = cur.fetchone()
                assert row is not None
                assert row["slot"] == "BN"

                # Drop player is no longer on team 1
                cur.execute("""
                    SELECT id FROM napper_rosters
                    WHERE league_id = 1 AND team_id = 1 AND player_id = %s AND week = 6
                """, (drop_pid,))
                assert cur.fetchone() is None

        # 6. Verify chat announcement
        chat_res = client.get("/api/napper/league/1/chat")
        assert chat_res.status_code == 200
        messages = chat_res.json()["messages"]
        waiver_msgs = [m for m in messages if claim_name in m["message_text"]]
        assert len(waiver_msgs) > 0
        assert "Waivers:" in waiver_msgs[-1]["message_text"]
