"""Tests for Napper API and Engines (tests/test_napper_api.py)."""

import pytest
from fastapi.testclient import TestClient
from server.api import app
from engines.napper_chat_engine import generate_weekly_awards, generate_manager_banter
from engines.napper_trade_engine import generate_trade_proposals

from server.napper_api import get_db

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_napper_module():
    from engines.napper_seeder import seed_napper_database
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM napper_leagues WHERE is_seed = FALSE")
        conn.commit()
    seed_napper_database()
    yield
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM napper_leagues WHERE is_seed = FALSE")
        conn.commit()
    seed_napper_database()


def get_first_league_id() -> int:
    res = client.get("/api/napper/leagues")
    data = res.json()
    assert len(data.get("leagues", [])) > 0
    return data["leagues"][0]["id"]


def test_napper_profile():
    res = client.get("/api/napper/profile")
    assert res.status_code == 200
    data = res.json()
    assert "profile" in data
    assert data["profile"]["handle"] == "jrereyi2000"


def test_napper_leagues():
    res = client.get("/api/napper/leagues")
    assert res.status_code == 200
    data = res.json()
    assert "leagues" in data
    assert len(data["leagues"]) >= 3
    league_names = [lg["name"] for lg in data["leagues"]]
    assert "Solo 2026 8-Team SF PPR" in league_names


def test_napper_league_detail():
    league_id = get_first_league_id()
    res = client.get(f"/api/napper/league/{league_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["league"]["id"] == league_id
    assert len(data["teams"]) == 8


def test_napper_matchup():
    league_id = get_first_league_id()
    res = client.get(f"/api/napper/league/{league_id}/matchup?week=6")
    assert res.status_code == 200
    data = res.json()
    assert data["league_id"] == league_id
    assert data["week"] == 6
    assert "left_team" in data
    assert "right_team" in data
    assert len(data["starters"]) == 10  # 10 starting slots in SF PPR
    assert len(data["bench"]) >= 6


def test_napper_waivers():
    league_id = get_first_league_id()
    res = client.get(f"/api/napper/league/{league_id}/waivers")
    assert res.status_code == 200
    data = res.json()
    assert "players" in data
    assert len(data["players"]) > 0


def test_napper_trades():
    league_id = get_first_league_id()
    res = client.get(f"/api/napper/league/{league_id}/trades")
    assert res.status_code == 200
    data = res.json()
    assert "trades" in data
    assert len(data["trades"]) > 0


def test_napper_chat():
    league_id = get_first_league_id()
    res = client.get(f"/api/napper/league/{league_id}/chat")
    assert res.status_code == 200
    data = res.json()
    assert "messages" in data
    assert len(data["messages"]) > 0
    # verify awards bot is in chat
    senders = [m["sender_name"] for m in data["messages"]]
    assert "Napper Awards Bot" in senders


def test_napper_engines():
    league_id = get_first_league_id()
    awards = generate_weekly_awards(league_id, 6)
    assert isinstance(awards, list)
    banter = generate_manager_banter(league_id, 6)
    assert isinstance(banter, list)


def test_napper_teams_rosters():
    league_id = get_first_league_id()
    res = client.get(f"/api/napper/league/{league_id}/teams_rosters")
    assert res.status_code == 200
    data = res.json()
    assert "teams" in data
    assert len(data["teams"]) == 8
    user_team = next(t for t in data["teams"] if t["is_user"])
    assert len(user_team["players"]) > 0


def test_napper_create_league_cleans_seed_leagues():
    from engines.napper_seeder import seed_napper_database

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM napper_leagues WHERE is_seed = FALSE")
        conn.commit()

    try:
        # Create a new league
        res = client.post("/api/napper/leagues/create", json={
            "name": "Grand Line Dynasty",
            "teams_count": 8,
            "scoring_type": "PPR",
            "is_superflex": True,
            "status": "IN_SEASON"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["league"]["name"] == "Grand Line Dynasty"

        # Check that seed leagues have been removed
        l_res = client.get("/api/napper/leagues")
        assert l_res.status_code == 200
        leagues = l_res.json()["leagues"]
        assert len(leagues) == 1
        assert leagues[0]["name"] == "Grand Line Dynasty"
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM napper_leagues WHERE is_seed = FALSE")
            conn.commit()
        # Restore reference seed leagues
        seed_napper_database()
