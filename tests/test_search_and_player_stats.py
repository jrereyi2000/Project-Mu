import pytest
from fastapi.testclient import TestClient
from server.api import app

pytestmark = pytest.mark.integration

client = TestClient(app)

def test_global_search_teams():
    res = client.get("/api/search?q=panthers")
    assert res.status_code == 200
    data = res.json()
    assert "teams" in data
    assert "players" in data
    assert any(t["abbr"] == "car" for t in data["teams"])

def test_global_search_players():
    res = client.get("/api/search?q=crawford")
    assert res.status_code == 200
    data = res.json()
    assert len(data["players"]) > 0
    crawford = next((p for p in data["players"] if "George Crawford" in p["name"]), None)
    assert crawford is not None
    assert crawford["position"] == "QB"
    assert crawford["teamDisplayAbbr"] == "CAR"
    assert crawford["overall"] == 99

def test_global_search_empty():
    res = client.get("/api/search?q=")
    assert res.status_code == 200
    data = res.json()
    assert data["teams"] == []
    assert data["players"] == []

def test_fantasy_search_players_unplayed_season():
    # Season 2036 has 0 completed games, should still return projected players
    res = client.get("/api/fantasy/players?season=2036")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] > 0
    assert len(data["players"]) > 0
    # Top player should have projected fantasy points
    top_p = data["players"][0]
    assert top_p["fpts"] > 0
    assert "name" in top_p
    assert "position" in top_p
    assert "team" in top_p

def test_player_past_season_career_stats():
    # George Crawford should have 2036, 2035, and historical 2034 stats
    res = client.get("/api/player/325?season=2036")
    assert res.status_code == 200
    data = res.json()
    assert "career_stats" in data
    seasons = [s["season"] for s in data["career_stats"]]
    assert 2036 in seasons
    assert 2035 in seasons
    assert 2034 in seasons

def test_player_past_season_direct_query():
    # Querying a historical season like 2034 should not 404 or return error
    res = client.get("/api/player/325?season=2034")
    assert res.status_code == 200
    data = res.json()
    assert "error" not in data
    assert data["player"]["name"] == "George Crawford"
    assert len(data["game_log"]) == 18

def test_veteran_historical_seasons():
    # Trey'Mane Davis (80) has historical seasons 2032-2035
    res = client.get("/api/player/80?season=2036")
    assert res.status_code == 200
    data = res.json()
    seasons = [s["season"] for s in data["career_stats"]]
    assert 2032 in seasons
    assert 2033 in seasons
    assert 2034 in seasons
    assert 2035 in seasons
