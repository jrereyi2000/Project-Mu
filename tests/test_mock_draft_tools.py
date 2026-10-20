"""Writer tools for the draft board and the weekly mock draft (newsroom/db_tools.py), on the fixture board.
No test touches data/draft/, captures/ or the live database."""
import json
from unittest import mock

import pytest

from engines.mock_draft_engine import MockDraftEngine
from newsroom.db_tools import get_mock_draft, get_scouted_draft_board
from newsroom.swarm_dispatcher import SwarmDispatcher
from pipeline.ingest_prospects import load_prospects_from_csv

FIXTURE = "tests/fixtures/prospects_board.csv"


def fixture_board():
    board = load_prospects_from_csv(FIXTURE, strict=False)
    for i, p in enumerate(board, start=1):
        p.prospect_id = 100 + i
    return board


def fake_read_board(cur, draft_season, position="", limit=None):
    from pipeline.draft_board import position_group
    board = fixture_board()
    want = position.upper()
    if want:
        board = [p for p in board if p.position and want in (p.position, position_group(p.position))]
    return board[:limit] if limit else board


@pytest.fixture
def no_db():
    with mock.patch("newsroom.db_tools.psycopg.connect"), \
         mock.patch("newsroom.db_tools.resolve_season", return_value=2035):
        yield


def test_board_tool_shape_and_nulls(no_db):
    with mock.patch("pipeline.draft_board.read_board", side_effect=fake_read_board):
        data = json.loads(get_scouted_draft_board.invoke({"limit": 10}))
    assert (data["season"], data["draft_season"], data["total_prospects"], data["returned_count"]) == (2035, 2036, 39, 10)
    first = data["prospects"][0]
    assert set(first) == {"consensus_rank", "grade", "round_label", "rank_change", "projected_pick",
                          "projected_round", "round_pick", "name", "pos", "college", "ht", "wt", "age", "archetype",
                          "notes"}                       # no Madden OVR: writers see the consensus only (step 4, Q6)
    assert (first["projected_pick"], first["projected_round"], first["round_pick"]) == (1, "1", 1)
    assert first["grade"] is None and data["board"].startswith("Madden projected order")
    # the scan's blanks come back as null for the writer, not guessed
    by_pick = {p["projected_pick"]: p for p in data["prospects"]}
    assert by_pick[10]["ht"] is None


def test_board_tool_orders_by_consensus(no_db):
    grades = {100 + i: {"grade": 90 - abs(i - 20), "consensus_rank": abs(i - 20) + 1 + (i > 20) * 0.5,
                        "round_label": "1st round", "delta_rank": 2} for i in range(1, 40)}
    with mock.patch("pipeline.draft_board.read_board", side_effect=fake_read_board), \
         mock.patch("newsroom.db_tools._scouting_grades", return_value=grades):
        data = json.loads(get_scouted_draft_board.invoke({"limit": 3}))
    assert data["board"] == "scouting consensus"
    assert [p["consensus_rank"] for p in data["prospects"]] == sorted(p["consensus_rank"] for p in data["prospects"])
    assert data["prospects"][0]["projected_pick"] == 20 and "ovr" not in data["prospects"][0]


def test_board_tool_position_filter(no_db):
    with mock.patch("pipeline.draft_board.read_board", side_effect=fake_read_board):
        edge = json.loads(get_scouted_draft_board.invoke({"position": "edge", "limit": 50}))
        qb = json.loads(get_scouted_draft_board.invoke({"position": "QB", "limit": 50}))
    assert edge["filter_position"] == "EDGE" and edge["returned_count"] > 0
    assert {p["pos"] for p in edge["prospects"]} <= {"LEDG", "REDG", "EDGE"}
    assert {p["pos"] for p in qb["prospects"]} == {"QB"}
    assert edge["total_prospects"] == 39                    # the whole board, before the filter


def test_board_tool_without_a_board(no_db):
    with mock.patch("pipeline.draft_board.read_board", return_value=[]):
        assert "No scouted draft board" in get_scouted_draft_board.invoke({})


def draft_order(n=32):
    # picks 1-24 locked (eliminated teams), 25-32 still alive
    return [{"pick": i, "team_id": i, "team": f"Team {i}", "abbreviation": f"T{i}", "record": "8-9",
             "locked": i <= 24, "selection_status": "Missed Playoffs" if i <= 18 else
             "Wild Card Elimination" if i <= 24 else "Still Alive (provisional)"} for i in range(1, n + 1)]


@pytest.fixture
def engine_offline():
    with mock.patch.object(MockDraftEngine, "load_prospects", side_effect=lambda self=None: fixture_board()), \
         mock.patch.object(MockDraftEngine, "get_draft_order", return_value=draft_order()), \
         mock.patch.object(MockDraftEngine, "get_team_needs", return_value=[]):
        yield


def test_mock_tool_runs_saves_and_limits(no_db, engine_offline):
    with mock.patch("pipeline.draft_board.read_mock", return_value=[]), \
         mock.patch("newsroom.db_tools._draft_week", return_value=20), \
         mock.patch("pipeline.draft_board.save_mock") as save:
        data = json.loads(get_mock_draft.invoke({"limit": 24}))
    assert (data["source"], data["week"], data["total_picks"], data["locked_picks"]) == ("generated", 20, 24, 24)
    picks = data["round_1_mock_draft"]
    assert [p["pick"] for p in picks] == list(range(1, 25))
    assert all(p["locked"] for p in picks)
    # the whole round is saved for the week, so a later call with any limit sees the same picks
    season, week, saved = save.call_args.args[1:]
    assert (season, week, len(saved)) == (2035, 20, 32)
    assert not saved[24]["locked"]
    assert len({p["prospect"]["full_name"] for p in saved}) == 32
    assert all("overall_rating" not in p["prospect"] and "consensus_grade" in p["prospect"] for p in picks)


def test_mock_tool_returns_the_saved_run(no_db):
    saved = [{"pick": i, "team": f"Team {i}", "locked": True, "prospect": {"full_name": f"P{i}"}} for i in range(1, 33)]
    with mock.patch("pipeline.draft_board.read_mock", return_value=saved), \
         mock.patch("newsroom.db_tools._draft_week", return_value=20), \
         mock.patch.object(MockDraftEngine, "run_mock") as run:
        data = json.loads(get_mock_draft.invoke({}))
    run.assert_not_called()
    assert (data["source"], data["total_picks"]) == ("saved", 32)


def test_tools_registered_in_dispatcher():
    dispatcher = SwarmDispatcher()
    names = [t.name for t in dispatcher.tools_for("OFFSEASON_MOCK_DRAFT")]
    assert {"get_mock_draft", "get_scouted_draft_board", "get_draft_order", "get_team_draft_needs"} <= set(names)
    assert "get_draft_prospects" not in names
