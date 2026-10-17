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
        assert sim_res.status_code == 200, sim_res.json()
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


def test_snake_draft_order_8_10_12_teams():
    """Verifies snake draft pick orders, turns, and mathematical formulas for 8, 10, and 12 teams."""
    # Test 8-team draft
    res_8 = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 8,
        "rounds": 15,
        "user_slot": 1,
        "scoring_type": "PPR",
    })
    assert res_8.status_code == 200
    data_8 = res_8.json()
    picks_8 = data_8["picks"]
    assert len(picks_8) == 120

    # Round 1: slots 1 to 8
    assert picks_8[0]["round_num"] == 1 and picks_8[0]["pick_num"] == 1 and picks_8[0]["slot_num"] == 1
    assert picks_8[7]["round_num"] == 1 and picks_8[7]["pick_num"] == 8 and picks_8[7]["slot_num"] == 8

    # Round 2 turn: slot 8 picks at 2.1 (back-to-back at the turn!), slot 1 picks at 2.8
    assert picks_8[8]["round_num"] == 2 and picks_8[8]["pick_num"] == 1 and picks_8[8]["slot_num"] == 8
    assert picks_8[9]["round_num"] == 2 and picks_8[9]["pick_num"] == 2 and picks_8[8+1]["slot_num"] == 7
    assert picks_8[15]["round_num"] == 2 and picks_8[15]["pick_num"] == 8 and picks_8[15]["slot_num"] == 1

    # Round 3 turn: slot 1 picks at 3.1 (back-to-back at the turn!), slot 8 picks at 3.8
    assert picks_8[16]["round_num"] == 3 and picks_8[16]["pick_num"] == 1 and picks_8[16]["slot_num"] == 1
    assert picks_8[23]["round_num"] == 3 and picks_8[23]["pick_num"] == 8 and picks_8[23]["slot_num"] == 8

    # Round 4 turn: slot 8 picks at 4.1
    assert picks_8[24]["round_num"] == 4 and picks_8[24]["pick_num"] == 1 and picks_8[24]["slot_num"] == 8
    assert picks_8[31]["round_num"] == 4 and picks_8[31]["pick_num"] == 8 and picks_8[31]["slot_num"] == 1

    # Comprehensive verification of all 120 picks for 8-team
    for p in picks_8:
        r = p["round_num"]
        pk = p["pick_num"]
        overall = (r - 1) * 8 + pk
        expected_slot = pk if (r % 2 == 1) else (8 - pk + 1)
        assert p["overall_pick"] == overall
        assert p["slot_num"] == expected_slot

    # Test 10-team draft
    res_10 = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 10,
        "rounds": 15,
        "user_slot": 5,
        "scoring_type": "PPR",
    })
    assert res_10.status_code == 200
    data_10 = res_10.json()
    picks_10 = data_10["picks"]
    assert len(picks_10) == 150

    # Round 1 & Round 2 turns for 10-team
    assert picks_10[0]["slot_num"] == 1
    assert picks_10[9]["slot_num"] == 10
    assert picks_10[10]["slot_num"] == 10  # 2.1 is slot 10
    assert picks_10[19]["slot_num"] == 1   # 2.10 is slot 1
    assert picks_10[20]["slot_num"] == 1   # 3.1 is slot 1
    assert picks_10[29]["slot_num"] == 10  # 3.10 is slot 10

    for p in picks_10:
        r = p["round_num"]
        pk = p["pick_num"]
        overall = (r - 1) * 10 + pk
        expected_slot = pk if (r % 2 == 1) else (10 - pk + 1)
        assert p["overall_pick"] == overall
        assert p["slot_num"] == expected_slot

    # Test 12-team draft
    res_12 = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 12,
        "rounds": 15,
        "user_slot": 12,
        "scoring_type": "PPR",
    })
    assert res_12.status_code == 200
    data_12 = res_12.json()
    picks_12 = data_12["picks"]
    assert len(picks_12) == 180

    # Round 1 & Round 2 turns for 12-team
    assert picks_12[0]["slot_num"] == 1
    assert picks_12[11]["slot_num"] == 12  # 1.12 is slot 12
    assert picks_12[12]["slot_num"] == 12  # 2.1 is slot 12 (turn!)
    assert picks_12[13]["slot_num"] == 11  # 2.2 is slot 11
    assert picks_12[23]["slot_num"] == 1   # 2.12 is slot 1 (turn!)
    assert picks_12[24]["slot_num"] == 1   # 3.1 is slot 1
    assert picks_12[35]["slot_num"] == 12  # 3.12 is slot 12
    assert picks_12[36]["slot_num"] == 12  # 4.1 is slot 12
    assert picks_12[47]["slot_num"] == 1   # 4.12 is slot 1

    for p in picks_12:
        r = p["round_num"]
        pk = p["pick_num"]
        overall = (r - 1) * 12 + pk
        expected_slot = pk if (r % 2 == 1) else (12 - pk + 1)
        assert p["overall_pick"] == overall
        assert p["slot_num"] == expected_slot


def test_player_pool_season_2035_and_projections():
    """Verifies that available_players and fantasy/projections return season 2035 players."""
    # Test fantasy projections endpoint
    proj_res = client.get("/api/fantasy/projections?season=2035&week=1&limit=50")
    assert proj_res.status_code == 200
    proj_data = proj_res.json()
    assert proj_data["season"] == 2035
    assert proj_data["week"] == 1
    assert proj_data["count"] > 0
    players = proj_data["players"]
    positions = {p["position"] for p in players}
    assert any(pos in ("QB",) for pos in positions)
    assert any(pos in ("WR",) for pos in positions)
    assert any(pos in ("HB", "RB", "FB") for pos in positions)

    # Test available_players with draft_id=0
    pool_res = client.get("/api/napper/draft/0/available_players?limit=50")
    assert pool_res.status_code == 200
    pool_data = pool_res.json()
    assert pool_data["total"] > 50
    assert len(pool_data["players"]) == 50
    p1 = pool_data["players"][0]
    assert p1["adp"] >= 1
    assert p1["projections"]["pts"] > 0


def test_mock_draft_archetype_composition_and_randomized_order():
    """Tests creating mock drafts with custom archetype compositions, randomized slots, and claiming slots."""
    # 1. Create a draft with a specific archetype composition: 4 ANALYTICS_NERD, 3 ZERO_RB_ZEALOT, 2 HERO_RB
    custom_comp = {
        "ANALYTICS_NERD": 4,
        "ZERO_RB_ZEALOT": 3,
        "HERO_RB": 2,
    }
    res = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 10,
        "rounds": 5,
        "archetype_composition": custom_comp,
    })
    assert res.status_code == 200
    data = res.json()
    draft_id = data["id"]

    # Slot should be randomized between 1 and 10
    user_slot = data["user_slot"]
    assert 1 <= user_slot <= 10

    # Teams should have the requested archetype composition
    ai_teams = [t for t in data["teams"] if not t["is_user"]]
    assert len(ai_teams) == 9

    personas = [t["persona"] for t in ai_teams]
    assert personas.count("ANALYTICS_NERD") == 4
    assert personas.count("ZERO_RB_ZEALOT") == 3
    assert personas.count("HERO_RB") == 2

    # 2. Test randomizing draft order
    rand_res = client.post(f"/api/napper/draft/{draft_id}/randomize_order", json={"keep_user_slot": False})
    assert rand_res.status_code == 200
    rand_data = rand_res.json()
    new_user_slot = rand_data["user_slot"]
    assert 1 <= new_user_slot <= 10

    # Check picks reflect the updated user slot
    user_picks = [p for p in rand_data["picks"] if p["slot_num"] == new_user_slot]
    for up in user_picks:
        assert up["picked_by"] == "USER"
        assert up["team_name"] == "The Straw Hat Pirates"

    # 3. Test changing archetype composition via endpoint
    new_comp = {
        "TRASH_TALKER": 4,
        "WAIVER_HAWK": 3,
        "DIE_HARD": 2,
    }
    arch_res = client.post(f"/api/napper/draft/{draft_id}/set_archetypes", json={"archetype_composition": new_comp})
    assert arch_res.status_code == 200
    arch_data = arch_res.json()
    new_ai_teams = [t for t in arch_data["teams"] if not t["is_user"]]
    new_personas = [t["persona"] for t in new_ai_teams]
    assert new_personas.count("TRASH_TALKER") == 4
    assert new_personas.count("WAIVER_HAWK") == 3
    assert new_personas.count("DIE_HARD") == 2

    # 4. User can still claim any slot after randomization
    target_slot = 1 if new_user_slot != 1 else 10
    claim_res = client.post(f"/api/napper/draft/{draft_id}/claim_slot", json={"slot": target_slot})
    assert claim_res.status_code == 200
    claimed_data = claim_res.json()
    assert claimed_data["user_slot"] == target_slot
    claimed_user_team = next(t for t in claimed_data["teams"] if t["slot_num"] == target_slot)
    assert claimed_user_team["is_user"] is True


def test_set_draft_order_endpoint_with_slot_order():
    """Tests POST /api/napper/draft/{id}/set_order using a slot_order permutation."""
    create_res = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 8,
        "scoring_type": "PPR",
        "user_slot": 3,
        "rounds": 14,
        "is_superflex": True,
    })
    assert create_res.status_code == 200
    draft_id = create_res.json()["id"]

    # Invert the draft order: previous slot 8 becomes 1, ..., previous slot 1 becomes 8
    # Previous user slot was 3; in [8, 7, 6, 5, 4, 3, 2, 1], slot 3 is at index 5 -> slot 6!
    slot_order = [8, 7, 6, 5, 4, 3, 2, 1]
    order_res = client.post(f"/api/napper/draft/{draft_id}/set_order", json={"slot_order": slot_order})
    assert order_res.status_code == 200
    data = order_res.json()

    assert data["user_slot"] == 6
    user_team = next(t for t in data["teams"] if t["slot_num"] == 6)
    assert user_team["is_user"] is True
    assert user_team["team_name"] == "The Straw Hat Pirates"

    # Verify picks ownership updated
    user_picks = [p for p in data["picks"] if p["slot_num"] == 6]
    for p in user_picks:
        assert p["picked_by"] == "USER"
        assert p["team_name"] == "The Straw Hat Pirates"

    non_user_picks = [p for p in data["picks"] if p["slot_num"] != 6]
    for p in non_user_picks:
        assert p["picked_by"] == "AI"


def test_set_draft_order_endpoint_with_custom_teams_and_validation():
    """Tests POST /api/napper/draft/{id}/set_order with custom teams list and validation checks."""
    create_res = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 8,
        "scoring_type": "PPR",
        "user_slot": 1,
        "rounds": 12,
        "is_superflex": True,
    })
    assert create_res.status_code == 200
    draft_id = create_res.json()["id"]

    # 1. Invalid permutation (duplicate slots or wrong length)
    bad_res = client.post(f"/api/napper/draft/{draft_id}/set_order", json={"slot_order": [1, 2, 3, 4, 5, 6, 7]})
    assert bad_res.status_code == 400

    bad_res2 = client.post(f"/api/napper/draft/{draft_id}/set_order", json={"slot_order": [1, 1, 2, 3, 4, 5, 6, 7]})
    assert bad_res2.status_code == 400

    # 2. Reorder using custom teams payload
    current_teams = create_res.json()["teams"]
    # Move user (index 0) to slot 5 (index 4)
    reordered_teams = current_teams[1:5] + [current_teams[0]] + current_teams[5:]
    assert len(reordered_teams) == 8

    custom_res = client.post(f"/api/napper/draft/{draft_id}/set_order", json={"teams": reordered_teams})
    assert custom_res.status_code == 200
    custom_data = custom_res.json()
    assert custom_data["user_slot"] == 5

    user_team = next(t for t in custom_data["teams"] if t["slot_num"] == 5)
    assert user_team["is_user"] is True

    # 3. Cannot change order after draft has started
    start_res = client.post(f"/api/napper/draft/{draft_id}/start")
    assert start_res.status_code == 200

    started_order_res = client.post(f"/api/napper/draft/{draft_id}/set_order", json={"slot_order": [8, 7, 6, 5, 4, 3, 2, 1]})
    assert started_order_res.status_code == 400
    assert "Cannot set draft order after draft has started" in started_order_res.json()["detail"]


def test_available_players_includes_kickers():
    """Verifies that kickers are draftable players with proper ADP and projections."""
    create_res = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 8,
        "rounds": 15,
        "user_slot": 1,
    })
    assert create_res.status_code == 200
    draft_id = create_res.json()["id"]

    # 1. Query with position="K"
    res_k = client.get(f"/api/napper/draft/{draft_id}/available_players?position=K&limit=50")
    assert res_k.status_code == 200
    k_data = res_k.json()
    assert k_data["total"] > 0
    kickers = k_data["players"]
    assert len(kickers) > 0
    for k in kickers:
        assert k["position"] == "K"
        assert k["adp"] < 999  # Valid ADP assigned
        assert k["projections"]["pts"] > 0

    # 2. Query with position="ALL" with limit=600 includes kickers
    res_all = client.get(f"/api/napper/draft/{draft_id}/available_players?position=ALL&limit=600")
    assert res_all.status_code == 200
    all_data = res_all.json()
    all_positions = {p["position"] for p in all_data["players"]}
    assert "K" in all_positions


def test_draft_kicker_selection():
    """Verifies drafting a kicker advances the draft and records the selection."""
    create_res = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 4,
        "rounds": 3,
        "user_slot": 1,
    })
    assert create_res.status_code == 200
    draft_id = create_res.json()["id"]

    # Start draft
    client.post(f"/api/napper/draft/{draft_id}/start")

    # Find a kicker
    res_k = client.get(f"/api/napper/draft/{draft_id}/available_players?position=K&limit=10")
    kicker = res_k.json()["players"][0]
    kicker_id = kicker["id"]

    # Pick the kicker at pick #1
    pick_res = client.post(f"/api/napper/draft/{draft_id}/pick", json={"player_id": kicker_id})
    assert pick_res.status_code == 200
    draft_state = pick_res.json()

    # Verify pick was recorded
    first_pick = draft_state["picks"][0]
    assert first_pick["player"] is not None
    assert first_pick["player"]["id"] == kicker_id
    assert first_pick["player"]["position"] == "K"
    assert first_pick["player"]["name"] == kicker["name"]




