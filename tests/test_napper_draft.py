"""Tests for Napper Draft Engine and API Endpoints (tests/test_napper_draft.py)."""

import pytest
from fastapi.testclient import TestClient
from server.api import app
from engines.napper_draft_engine import (
    create_draft_session,
    get_draft_state,
    claim_slot,
    start_draft,
    make_draft_pick,
    simulate_ai_pick,
    simulate_until_user,
    simulate_entire_draft,
    finalize_draft,
)

client = TestClient(app)


def test_create_mock_draft_endpoint():
    """Tests POST /api/napper/drafts/create_mock with custom slots and snake ordering."""
    payload = {
        "teams_count": 10,
        "scoring_type": "PPR",
        "user_slot": 3,
        "rounds": 15,
        "is_superflex": True,
    }
    res = client.post("/api/napper/drafts/create_mock", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "PENDING"
    assert data["teams_count"] == 10
    assert data["rounds"] == 15
    assert data["total_picks"] == 150
    assert data["user_slot"] == 3
    assert data["is_mock"] is True
    assert data["current_pick"] == 1
    assert data["current_round"] == 1
    assert data["is_user_turn"] is False

    # Check teams
    teams = data["teams"]
    assert len(teams) == 10
    user_team = next((t for t in teams if t["slot_num"] == 3), None)
    assert user_team is not None
    assert user_team["is_user"] is True
    assert user_team["persona"] == "USER"

    # Verify snake draft order
    picks = data["picks"]
    assert len(picks) == 150

    # Round 1: slots 1 to 10
    assert picks[0]["overall_pick"] == 1
    assert picks[0]["round_num"] == 1
    assert picks[0]["pick_num"] == 1
    assert picks[0]["slot_num"] == 1

    assert picks[9]["overall_pick"] == 10
    assert picks[9]["round_num"] == 1
    assert picks[9]["pick_num"] == 10
    assert picks[9]["slot_num"] == 10

    # Round 2: slots 10 down to 1 (snake reversal)
    assert picks[10]["overall_pick"] == 11
    assert picks[10]["round_num"] == 2
    assert picks[10]["pick_num"] == 1
    assert picks[10]["slot_num"] == 10

    assert picks[19]["overall_pick"] == 20
    assert picks[19]["round_num"] == 2
    assert picks[19]["pick_num"] == 10
    assert picks[19]["slot_num"] == 1

    # Round 3: slots 1 to 10
    assert picks[20]["overall_pick"] == 21
    assert picks[20]["round_num"] == 3
    assert picks[20]["slot_num"] == 1


def test_claim_slot_endpoint():
    """Tests claiming a new draft position before draft start."""
    # Create draft at slot 2
    create_res = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 8,
        "rounds": 5,
        "user_slot": 2,
    })
    assert create_res.status_code == 200
    draft_id = create_res.json()["id"]

    # Claim slot 6
    claim_res = client.post(f"/api/napper/draft/{draft_id}/claim_slot", json={"slot": 6})
    assert claim_res.status_code == 200
    data = claim_res.json()

    assert data["user_slot"] == 6
    team_6 = next(t for t in data["teams"] if t["slot_num"] == 6)
    team_2 = next(t for t in data["teams"] if t["slot_num"] == 2)
    assert team_6["is_user"] is True
    assert team_2["is_user"] is False

    # Check picks picked_by updated
    slot_6_picks = [p for p in data["picks"] if p["slot_num"] == 6]
    for p in slot_6_picks:
        assert p["picked_by"] == "USER"

    # Test invalid slot
    bad_res = client.post(f"/api/napper/draft/{draft_id}/claim_slot", json={"slot": 99})
    assert bad_res.status_code == 400


def test_start_draft_endpoint():
    """Tests starting a draft session and verifying on-the-clock state."""
    create_res = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 6,
        "rounds": 4,
        "user_slot": 1,
    })
    draft_id = create_res.json()["id"]

    start_res = client.post(f"/api/napper/draft/{draft_id}/start")
    assert start_res.status_code == 200
    data = start_res.json()

    assert data["status"] == "IN_PROGRESS"
    assert data["current_pick"] == 1
    assert data["is_user_turn"] is True  # User is slot 1 and pick 1 is slot 1
    assert data["on_the_clock"] is not None
    assert data["on_the_clock"]["slot_num"] == 1
    assert data["on_the_clock"]["is_user"] is True


def test_available_players_endpoint():
    """Tests fetching available player pool with ADP ordering, filtering, and stats."""
    create_res = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 6,
        "rounds": 4,
        "user_slot": 1,
    })
    draft_id = create_res.json()["id"]

    # General pool
    res = client.get(f"/api/napper/draft/{draft_id}/available_players?limit=20")
    assert res.status_code == 200
    data = res.json()

    assert "players" in data
    assert "total" in data
    assert len(data["players"]) == 20
    assert data["total"] > 50

    p1 = data["players"][0]
    assert "id" in p1
    assert "name" in p1
    assert "position" in p1
    assert "team" in p1
    assert "headshot_url" in p1
    assert "adp" in p1
    assert "bye" in p1
    assert "age" in p1
    assert "ros_pct" in p1
    assert "projections" in p1
    assert "pts" in p1["projections"]
    assert "avg" in p1["projections"]
    assert "rush" in p1
    assert "rec" in p1
    assert "draft_count" in p1

    # Position filter QB
    qb_res = client.get(f"/api/napper/draft/{draft_id}/available_players?position=QB&limit=10")
    assert qb_res.status_code == 200
    qb_data = qb_res.json()
    assert all(p["position"] == "QB" for p in qb_data["players"])

    # Position filter RB
    rb_res = client.get(f"/api/napper/draft/{draft_id}/available_players?position=RB&limit=10")
    assert rb_res.status_code == 200
    rb_data = rb_res.json()
    assert all(p["position"] in ("RB", "HB", "FB") for p in rb_data["players"])

    # Search filter
    search_res = client.get(f"/api/napper/draft/{draft_id}/available_players?search=Davis")
    assert search_res.status_code == 200
    search_data = search_res.json()
    assert len(search_data["players"]) > 0
    assert any("Davis" in p["name"] for p in search_data["players"])


def test_draft_pick_and_sim_flow():
    """Tests user selection, AI step simulation, and sim_to_user advancement."""
    create_res = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 4,
        "rounds": 3,
        "user_slot": 2,  # User picks second
    })
    draft_id = create_res.json()["id"]

    client.post(f"/api/napper/draft/{draft_id}/start")

    # Step 1: Slot 1 is AI -> simulate_ai_pick via sim_step
    step_res = client.post(f"/api/napper/draft/{draft_id}/sim_step")
    assert step_res.status_code == 200
    step_data = step_res.json()

    assert step_data["current_pick"] == 2
    assert step_data["is_user_turn"] is True
    pick_1 = step_data["picks"][0]
    assert pick_1["player"] is not None
    assert pick_1["picked_by"] == "AI"
    assert pick_1["pick_notes"] is not None

    # Step 2: Slot 2 is User -> user makes pick
    avail_res = client.get(f"/api/napper/draft/{draft_id}/available_players?limit=5")
    chosen_player = avail_res.json()["players"][0]

    pick_res = client.post(f"/api/napper/draft/{draft_id}/pick", json={"player_id": chosen_player["id"]})
    assert pick_res.status_code == 200
    pick_data = pick_res.json()

    assert pick_data["current_pick"] == 3
    assert pick_data["is_user_turn"] is False
    pick_2 = pick_data["picks"][1]
    assert pick_2["player"]["id"] == chosen_player["id"]
    assert pick_2["picked_by"] == "USER"

    # Step 3: Test duplicate pick rejection
    dup_res = client.post(f"/api/napper/draft/{draft_id}/pick", json={"player_id": chosen_player["id"]})
    assert dup_res.status_code == 400

    # Step 4: sim_to_user:
    # User is slot 2. In 4-team snake draft:
    # Round 1: 1 (AI), 2 (User) -> done
    # Picks 3 (AI), 4 (AI)
    # Round 2: 5 (slot 4, AI), 6 (slot 3, AI), 7 (slot 2, User)
    # So sim_to_user should simulate picks 3, 4, 5, 6 and stop at pick 7!
    sim_res = client.post(f"/api/napper/draft/{draft_id}/sim_to_user")
    assert sim_res.status_code == 200
    sim_data = sim_res.json()

    assert sim_data["current_pick"] == 7
    assert sim_data["is_user_turn"] is True
    assert sim_data["on_the_clock"]["slot_num"] == 2


def test_simulate_all_and_finalization():
    """Tests fast-forwarding an entire draft, report card grades, and recap."""
    create_res = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 4,
        "rounds": 4,
        "user_slot": 1,
    })
    draft_id = create_res.json()["id"]

    all_res = client.post(f"/api/napper/draft/{draft_id}/sim_all")
    assert all_res.status_code == 200
    data = all_res.json()

    assert data["status"] == "COMPLETED"
    assert data["current_pick"] > data["total_picks"]

    # Verify all 16 picks filled
    assert len(data["picks"]) == 16
    for p in data["picks"]:
        assert p["player"] is not None
        assert p["player"]["id"] is not None

    # Verify recap details
    recap = data["recap"]
    assert recap is not None
    assert "grades" in recap
    assert len(recap["grades"]) == 4

    # Each team has a grade
    for slot_str, g_info in recap["grades"].items():
        assert g_info["grade"] in ("A+", "A", "B+", "B", "C+", "C", "D")
        assert g_info["projected_points"] > 0

    assert "best_value" in recap
    assert "biggest_reach" in recap


def test_league_draft_lifecycle():
    """Tests draft integration for a real Napper league, roster assignment, and schedule."""
    from engines.napper_seeder import seed_napper_database
    league_id = None
    try:
        # 1. Create a custom league in PRE_DRAFT status
        league_res = client.post("/api/napper/leagues/create", json={
            "name": "Draft Test League",
            "teams_count": 8,
            "scoring_type": "PPR",
            "status": "PRE_DRAFT",
            "is_superflex": True,
        })
        assert league_res.status_code == 200
        league = league_res.json()["league"]
        league_id = league["id"]
        assert league["status"] == "PRE_DRAFT"

        # 2. Access / create draft room for league with 11 rounds to test starter & bench assignment
        room_res = client.get(f"/api/napper/league/{league_id}/draft?rounds=11")
        assert room_res.status_code == 200
        draft_data = room_res.json()
        draft_id = draft_data["id"]

        assert draft_data["league_id"] == league_id
        assert draft_data["is_mock"] is False
        assert draft_data["teams_count"] == 8

        # 3. Simulate entire draft
        sim_res = client.post(f"/api/napper/draft/{draft_id}/sim_all")
        assert sim_res.status_code == 200
        final_state = sim_res.json()
        assert final_state["status"] == "COMPLETED"

        # 4. Verify league status transitioned to IN_SEASON
        lg_detail = client.get(f"/api/napper/league/{league_id}").json()
        assert lg_detail["league"]["status"] == "IN_SEASON"

        # 5. Verify rosters were populated for all teams with starting slots and bench
        roster_res = client.get(f"/api/napper/league/{league_id}/teams_rosters?week=1")
        assert roster_res.status_code == 200
        teams_rosters = roster_res.json()["teams"]
        assert len(teams_rosters) == 8

        for tm in teams_rosters:
            assert len(tm["players"]) > 0
            slots = [p["slot"] for p in tm["players"]]
            # Starting slots should be populated
            assert any(s in ("QB", "SUPERFLEX") for s in slots)
            assert any(s in ("RB", "FLEX") for s in slots)
            assert any(s in ("WR", "FLEX") for s in slots)
            assert any(s == "BN" for s in slots)

        # 6. Verify league chat contains draft recap message
        chat_res = client.get(f"/api/napper/league/{league_id}/chat")
        assert chat_res.status_code == 200
        messages = chat_res.json()["messages"]
        assert any("Draft" in m["message_text"] or (m["card_payload"] and m["card_payload"].get("type") == "DRAFT_RECAP") for m in messages)
    finally:
        if league_id:
            from engines.napper_draft_engine import get_db
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM napper_leagues WHERE id = %s", (league_id,))
                conn.commit()
        seed_napper_database()

