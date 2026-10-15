import json
import pytest
from newsroom.db_tools import get_mock_draft, get_draft_prospects
from newsroom.swarm_dispatcher import SwarmDispatcher


def test_get_draft_prospects_default():
    raw = get_draft_prospects.invoke({"top_n": 10})
    data = json.loads(raw)
    assert "prospects" in data
    assert data["returned_count"] == 10
    assert len(data["prospects"]) == 10
    
    first = data["prospects"][0]
    assert "full_name" in first
    assert "position" in first
    assert "overall_rating" in first
    assert "projected_pick" in first


def test_get_draft_prospects_position_filter():
    raw_qb = get_draft_prospects.invoke({"position": "QB", "top_n": 5})
    data_qb = json.loads(raw_qb)
    assert data_qb["returned_count"] > 0
    for p in data_qb["prospects"]:
        assert p["position"] == "QB"

    raw_edge = get_draft_prospects.invoke({"position": "EDGE", "top_n": 5})
    data_edge = json.loads(raw_edge)
    assert data_edge["returned_count"] > 0
    for p in data_edge["prospects"]:
        assert p["position"] in ("EDGE", "LEDG", "REDG", "DE")


def test_get_mock_draft():
    raw = get_mock_draft.invoke({"season": 2035})
    data = json.loads(raw)
    assert data["total_picks"] == 32
    assert "round_1_mock_draft" in data
    selections = data["round_1_mock_draft"]
    assert len(selections) == 32

    # Verify pick continuity 1..32 and presence of required fields
    picks = [s["pick"] for s in selections]
    assert picks == list(range(1, 33))

    for s in selections:
        assert "team" in s
        assert "prospect" in s
        assert "rationale" in s
        assert "fit_score" in s
        assert s["prospect"]["full_name"]


def test_tools_registered_in_dispatcher():
    dispatcher = SwarmDispatcher()
    tool_names = [t.name for t in dispatcher.tools]
    assert "get_mock_draft" in tool_names
    assert "get_draft_prospects" in tool_names

    # Ensure tools are present for OFFSEASON_MOCK_DRAFT
    mock_tools = [t.name for t in dispatcher.tools_for("OFFSEASON_MOCK_DRAFT")]
    assert "get_mock_draft" in mock_tools
    assert "get_draft_prospects" in mock_tools
