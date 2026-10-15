import os
import pytest
import tempfile

from pipeline.ingest_prospects import (
    Prospect,
    load_prospects_from_csv,
    normalize_height,
    normalize_position,
    parse_prospect_row,
)
from engines.mock_draft_engine import MockDraftEngine


def test_normalize_height():
    assert normalize_height("6'3\"") == "6'3\""
    assert normalize_height("6-4") == "6'4\""
    assert normalize_height("5 11") == "5'11\""
    assert normalize_height("6'0") == "6'0\""


def test_normalize_position():
    assert normalize_position("RB") == "HB"
    assert normalize_position("hb") == "HB"
    assert normalize_position("DE") == "EDGE"
    assert normalize_position("OG") == "IOL"
    assert normalize_position("QB") == "QB"


def test_parse_prospect_row_valid():
    row = {
        "projected_pick": "1",
        "full_name": "Dante Moore",
        "position": "QB",
        "college": "Oregon",
        "height": "6-3",
        "weight": "210",
        "age": "21",
        "overall_rating": "83",
        "archetype": "Field General",
        "notes": "Elite arm talent",
    }
    p = parse_prospect_row(row, 2)
    assert p.projected_pick == 1
    assert p.full_name == "Dante Moore"
    assert p.position == "QB"
    assert p.height == "6'3\""
    assert p.weight == 210
    assert p.age == 21
    assert p.overall_rating == 83
    assert p.archetype == "Field General"


def test_parse_prospect_row_invalid_ranges():
    # Weight too low
    row = {
        "projected_pick": "1",
        "full_name": "Ghost",
        "position": "WR",
        "college": "LSU",
        "height": "6-0",
        "weight": "100",  # < 140
        "age": "21",
        "overall_rating": "80",
    }
    with pytest.raises(ValueError, match="invalid weight"):
        parse_prospect_row(row, 2)

    # Invalid OVR
    row["weight"] = "190"
    row["overall_rating"] = "105"
    with pytest.raises(ValueError, match="invalid overall_rating"):
        parse_prospect_row(row, 2)


def test_load_prospects_from_csv(tmp_path):
    csv_file = tmp_path / "test_prospects.csv"
    csv_file.write_text(
        "PROJECTED_PICK,FULL_NAME,POSITION,COLLEGE,HEIGHT,WEIGHT,AGE,OVR\n"
        "2,Player Two,OT,Texas,6-5,310,21,80\n"
        "1,Player One,QB,Oregon,6-3,215,22,82\n"
    )

    prospects = load_prospects_from_csv(str(csv_file))
    assert len(prospects) == 2
    # Should be sorted by projected_pick
    assert prospects[0].projected_pick == 1
    assert prospects[0].full_name == "Player One"
    assert prospects[1].projected_pick == 2
    assert prospects[1].full_name == "Player Two"


def test_load_prospects_duplicate_pick_error(tmp_path):
    csv_file = tmp_path / "test_dups.csv"
    csv_file.write_text(
        "PROJECTED_PICK,FULL_NAME,POSITION,COLLEGE,HEIGHT,WEIGHT,AGE,OVR\n"
        "1,Player One,QB,Oregon,6-3,215,22,82\n"
        "1,Player Two,OT,Texas,6-5,310,21,80\n"
    )
    with pytest.raises(ValueError, match="Duplicate projected_pick 1"):
        load_prospects_from_csv(str(csv_file))


def test_mock_draft_engine_run():
    """A full round from the fixture board (blank cells included), with the order and needs stubbed."""
    from unittest import mock
    order = [{"pick": i, "team_id": i, "team": f"Team {i}", "abbreviation": f"T{i}", "record": "8-9",
              "locked": i <= 24, "selection_status": "x"} for i in range(1, 33)]
    engine = MockDraftEngine(season=2035, prospects_path="tests/fixtures/prospects_board.csv")
    with mock.patch.object(MockDraftEngine, "get_draft_order", return_value=order), \
         mock.patch.object(MockDraftEngine, "get_team_needs", return_value=[]):
        selections = engine.run_mock()
        first_24 = engine.run_mock(limit=24)

    assert len(selections) == 32
    picked_players = [s["prospect"]["full_name"] for s in selections]
    assert len(set(picked_players)) == 32
    assert (selections[0]["pick"], selections[31]["pick"]) == (1, 32)
    assert [s["locked"] for s in selections] == [True] * 24 + [False] * 8
    assert selections[0]["team_id"] == 1
    assert len(first_24) == 24

    board = engine.format_board(selections)
    assert "ROUND 1 MOCK DRAFT" in board and "#1" in board and "#32" in board
    assert "(provisional)" in board


def test_unrated_prospect_is_still_draftable():
    from engines.mock_draft_engine import UNRATED_OVR
    engine = MockDraftEngine(season=2035, prospects_path="unused.csv")
    p = Prospect(projected_pick=6, full_name="No Grade", position="CB", college=None, height=None, weight=None,
                 age=None, overall_rating=None)
    graded = Prospect(projected_pick=6, full_name="Graded", position="CB", college=None, height=None, weight=None,
                      age=None, overall_rating=int(UNRATED_OVR))
    assert engine.evaluate_fit(p, 6, [])[0] == engine.evaluate_fit(graded, 6, [])[0]


def test_only_a_database_mock_can_be_saved():
    engine = MockDraftEngine(season=2035, prospects_path="unused.csv")
    with pytest.raises(ValueError, match="draft_prospects"):
        engine.save(None, 20, [{"pick": 1, "prospect": {"prospect_id": None}}])
