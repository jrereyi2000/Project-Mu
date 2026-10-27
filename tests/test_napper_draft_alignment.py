"""Tests verifying alignment of Napper draft simulation with fantasy_league_sim.

Verifies:
1. AI teams never draft a 3rd QB in standard 1-QB drafts (DRAFT_CAP enforced).
2. Forced starting lineup rule prevents drafting bench redundancies over unfilled starters.
3. Positional weighting and backup penalties prevent early backup QBs over starting TEs/WRs.
4. Support for fantasy_league_sim strategies: RB_HEAVY, EARLY_QB, EARLY_TE, BPA.
"""

from contextlib import contextmanager
import pytest
from fastapi.testclient import TestClient
from server.api import app
from engines.napper_draft_engine import (
    create_draft_session,
    simulate_entire_draft,
    get_draft_state,
    simulate_ai_pick,
    get_db,
)

pytestmark = pytest.mark.integration

client = TestClient(app)


@contextmanager
def _league_draft_session(
    teams_count: int = 8,
    rounds: int = 15,
    user_slot: int = 1,
    is_superflex: bool = False,
    archetype_composition: dict | None = None,
):
    """Creates a PRE_DRAFT league and initializes its draft session, cleaning up on exit."""
    payload = {
        "name": "Alignment Test League",
        "teams_count": teams_count,
        "scoring_type": "PPR",
        "status": "PRE_DRAFT",
        "is_superflex": is_superflex,
    }
    if archetype_composition:
        payload["archetype_preset"] = "Custom"
        payload["archetype_composition"] = archetype_composition

    league_res = client.post("/api/napper/leagues/create", json=payload)
    assert league_res.status_code == 200, league_res.text
    league_id = league_res.json()["league"]["id"]
    try:
        draft_res = client.get(f"/api/napper/league/{league_id}/draft?rounds={rounds}")
        assert draft_res.status_code == 200, draft_res.text
        draft_data = draft_res.json()
        draft_id = draft_data["id"]
        if user_slot != 1:
            claim_res = client.post(f"/api/napper/draft/{draft_id}/claim_slot", json={"slot": user_slot})
            assert claim_res.status_code == 200, claim_res.text
            draft_data = claim_res.json()
        yield draft_data
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM napper_leagues WHERE id = %s", (league_id,))
            conn.commit()


def test_ai_draft_caps_and_no_third_qb():
    """Verify that in a standard 15-round 1-QB draft, no AI team drafts 3 QBs or 3 TEs."""
    with _league_draft_session(
        teams_count=8,
        rounds=15,
        user_slot=1,
        is_superflex=False,
    ) as draft_init:
        draft_id = draft_init["id"]

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
    with _league_draft_session(
        teams_count=8,
        rounds=6,
        user_slot=1,
        is_superflex=False,
        archetype_composition={
            "RB_HEAVY": 2,
            "EARLY_QB": 2,
            "EARLY_TE": 1,
            "BPA": 1,
            "ZERO_RB_ZEALOT": 1,
        },
    ) as draft_init:
        draft_id = draft_init["id"]

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
    with _league_draft_session(
        teams_count=8,
        rounds=8,
        user_slot=8,
        is_superflex=False,
    ) as draft_init:
        draft_id = draft_init["id"]

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

