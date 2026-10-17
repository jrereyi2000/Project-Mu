"""Tests for Napper API and Engines (tests/test_napper_api.py)."""

import pytest
from fastapi.testclient import TestClient
from server.api import app
from engines.napper_chat_engine import generate_weekly_awards, generate_manager_banter
from engines.napper_trade_engine import generate_trade_proposals

client = TestClient(app)


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
    res = client.get("/api/napper/league/1")
    assert res.status_code == 200
    data = res.json()
    assert data["league"]["id"] == 1
    assert len(data["teams"]) == 8


def test_napper_matchup():
    res = client.get("/api/napper/league/1/matchup?week=6")
    assert res.status_code == 200
    data = res.json()
    assert data["league_id"] == 1
    assert data["week"] == 6
    assert "left_team" in data
    assert "right_team" in data
    assert len(data["starters"]) == 10  # 10 starting slots in SF PPR
    assert len(data["bench"]) >= 6


def test_napper_waivers():
    res = client.get("/api/napper/league/1/waivers")
    assert res.status_code == 200
    data = res.json()
    assert "players" in data
    assert len(data["players"]) > 0


def test_napper_trades():
    res = client.get("/api/napper/league/1/trades")
    assert res.status_code == 200
    data = res.json()
    assert "trades" in data
    assert len(data["trades"]) > 0


def test_napper_chat():
    res = client.get("/api/napper/league/1/chat")
    assert res.status_code == 200
    data = res.json()
    assert "messages" in data
    assert len(data["messages"]) > 0
    # verify awards bot is in chat
    senders = [m["sender_name"] for m in data["messages"]]
    assert "Napper Awards Bot" in senders


def test_napper_engines():
    awards = generate_weekly_awards(1, 6)
    assert isinstance(awards, list)
    banter = generate_manager_banter(1, 6)
    assert isinstance(banter, list)
