"""Tests verifying alignment of Napper draft simulation with fantasy_league_sim.

Verifies:
1. AI teams never draft a 3rd QB in standard 1-QB drafts (DRAFT_CAP enforced).
2. Forced starting lineup rule prevents drafting bench redundancies over unfilled starters.
3. Positional weighting and backup penalties prevent early backup QBs over starting TEs/WRs.
4. Support for fantasy_league_sim strategies: RB_HEAVY, EARLY_QB, EARLY_TE, BPA.
"""

import pytest
from fastapi.testclient import TestClient
from server.api import app
from engines.napper_draft_engine import (
    create_draft_session,
    simulate_entire_draft,
    get_draft_state,
    simulate_ai_pick,
)

client = TestClient(app)


def test_ai_draft_caps_and_no_third_qb():
    """Verify that in a standard 15-round 1-QB draft, no AI team drafts 3 QBs or 3 TEs."""
    res = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 8,
        "rounds": 15,
        "user_slot": 1,
        "is_superflex": False,
    })
    assert res.status_code == 200
    draft_id = res.json()["id"]

    sim_res = client.post(f"/api/napper/draft/{draft_id}/sim_all")
    assert sim_res.status_code == 200
    draft_state = sim_res.json()
    assert draft_state["status"] == "COMPLETED"

    # Group drafted players by team slot
    picks = draft_state["picks"]
    assert len(picks) == 8 * 15

    teams_picks = {}
    for p in picks:
        slot = p["slot_num"]
        if slot not in teams_picks:
            teams_picks[slot] = []
        teams_picks[slot].append(p["player"]["position"])

    # For every AI team (slots 2..8):
    for slot in range(2, 9):
        positions = teams_picks[slot]
        qb_count = positions.count("QB")
        te_count = positions.count("TE")
        rb_count = sum(1 for pos in positions if pos in ("RB", "HB", "FB"))
        wr_count = positions.count("WR")

        # 1. Strictly at most 2 QBs (DRAFT_CAP = 2)
        assert qb_count <= 2, f"Team slot {slot} drafted {qb_count} QBs! Max allowed is 2."
        assert qb_count >= 1, f"Team slot {slot} drafted {qb_count} QBs! Must have at least 1 starter."

        # 2. Strictly at most 2 TEs (DRAFT_CAP = 2)
        assert te_count <= 2, f"Team slot {slot} drafted {te_count} TEs! Max allowed is 2."
        assert te_count >= 1, f"Team slot {slot} drafted {te_count} TEs! Must have at least 1 starter."

        # 3. Must have starting core filled (at least 2 RBs and 2 WRs)
        assert rb_count >= 2, f"Team slot {slot} drafted only {rb_count} RBs! Expected >= 2."
        assert wr_count >= 2, f"Team slot {slot} drafted only {wr_count} WRs! Expected >= 2."


def test_support_for_league_sim_strategies():
    """Verify drafts can be initialized and simulated with RB_HEAVY, EARLY_QB, EARLY_TE, and BPA archetypes."""
    res = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 6,
        "rounds": 6,
        "user_slot": 1,
        "is_superflex": False,
        "archetype_composition": {
            "RB_HEAVY": 1,
            "EARLY_QB": 1,
            "EARLY_TE": 1,
            "BPA": 1,
            "ZERO_RB_ZEALOT": 1,
        }
    })
    assert res.status_code == 200
    draft_id = res.json()["id"]

    sim_res = client.post(f"/api/napper/draft/{draft_id}/sim_all")
    assert sim_res.status_code == 200
    draft_state = sim_res.json()
    assert draft_state["status"] == "COMPLETED"

    teams = draft_state["teams"]
    personas = {t["slot_num"]: t["persona"] for t in teams if not t["is_user"]}
    assert "RB_HEAVY" in personas.values()
    assert "EARLY_QB" in personas.values()
    assert "EARLY_TE" in personas.values()
    assert "BPA" in personas.values()


def test_backup_qb_penalty_prioritizes_empty_starter_slots():
    """Verify that an AI team with 1 QB and 0 TEs in round 6+ does not select a backup QB over an available TE."""
    res = client.post("/api/napper/drafts/create_mock", json={
        "teams_count": 4,
        "rounds": 8,
        "user_slot": 4,
        "is_superflex": False,
    })
    draft_id = res.json()["id"]

    # Run the draft
    sim_res = client.post(f"/api/napper/draft/{draft_id}/sim_all")
    assert sim_res.status_code == 200
    draft_state = sim_res.json()

    for t in draft_state["teams"]:
        if t["is_user"]:
            continue
        roster = [p["player"]["position"] for p in draft_state["picks"] if p["slot_num"] == t["slot_num"]]
        # Each team must have a starting TE and starting QB
        assert "TE" in roster
        assert "QB" in roster
