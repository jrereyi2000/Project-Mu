"""The Draft Room (docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md Phase 7). Pure: no DB."""
from unittest import mock

import pytest

from engines import draft_room as dr
from engines.draft_order import apply_owners

KEYS = [f"2037:P{i:02d}:WR" for i in range(1, 61)]
CONS = {k: {"rank": i + 1, "grade": 90 - i * 0.3} for i, k in enumerate(KEYS)}


def board(order):
    return [(k, 95 - i * 0.2, i + 1) for i, k in enumerate(order)]


def room(owner_of_1=5):
    slots = [{"pick": n, "round": 1, "original_team_id": n + 4, "owner_team_id": n + 4, "used": False}
             for n in range(1, 33)]
    slots[0]["owner_team_id"] = owner_of_1
    teams = {t: {"abbr": f"T{t}", "name": f"Team {t}", "user_controlled": t == 9, "gm": f"GM {t}", "voice": None}
             for t in range(5, 37)}
    boards = {t: board(KEYS[(t % 3):] + KEYS[:(t % 3)]) for t in teams}
    info = {k: {"prospect_key": k, "prospect_id": i, "status": "ACTIVE", "full_name": f"P{i}", "position": "WR",
                "college": "GEORGIA", "age": 22, "consensus_rank": CONS[k]["rank"], "grade": CONS[k]["grade"],
                "round_label": "1st round"} for i, k in enumerate(KEYS)}
    return {"slots": slots, "teams": teams, "boards": boards, "cons": CONS, "info": info, "available": set(KEYS),
            "recorded": [], "ctxs": {}}


def test_order_maps_through_owners():
    order = [{"pick": i, "team_id": 100 + i, "locked": True} for i in range(1, 33)]
    slots = apply_owners(order, {(1, 103): 120, (2, 101): 130}, 1)
    assert [s["owner_team_id"] for s in slots[:4]] == [101, 102, 120, 104]
    r2 = apply_owners(order, {(2, 101): 130}, 2)
    assert (r2[0]["pick"], r2[0]["round_pick"], r2[0]["owner_team_id"]) == (33, 1, 130)


def test_recorded_pick_leaves_the_pool_and_recommendations_are_stable():
    r = room()
    best, alts = dr.recommend(r["boards"][6], CONS, r["available"])
    assert (best, alts) == dr.recommend(r["boards"][6], CONS, r["available"])          # same input, same player
    pool = r["available"] - {best}
    assert dr.recommend(r["boards"][6], CONS, pool)[0] == alts[0]


def test_off_script_pick_changes_later_recommendations():
    r = room()
    plan = dr.project(r["slots"], r["boards"], CONS, r["available"], 1)
    on_script = plan[0][2]
    off = next(k for k in KEYS if k != on_script and k == plan[1][2])                  # take pick 2's player at 1
    after = dr.project(r["slots"], r["boards"], CONS, r["available"] - {off}, 2)
    assert [p for _, _, p in after] != [p for _, _, p in plan[1:]]
    assert off not in {p for _, _, p in after}


def test_reach_limit_and_board_fallback():
    with mock.patch.object(dr.sb, "REACH_LIMIT", 10):
        best, _ = dr.recommend(board(list(reversed(KEYS))), CONS, set(KEYS))
    assert CONS[best]["rank"] <= 10
    best, _ = dr.recommend([], CONS, {KEYS[5], KEYS[9]})                                # the board ran dry
    assert best == KEYS[5]


def test_choice_source():
    assert dr.choice_source(False, "a", "a") == "GM"
    assert dr.choice_source(False, "b", "a") == "OFF_SCRIPT"
    assert dr.choice_source(True, "a", "a") == "JR_TEAM"                                # JR's team: advice only


def test_traded_pick_moves_the_clock():
    before = dr._pick_card(room(owner_of_1=5), 1, rationale=False)
    after = dr._pick_card(room(owner_of_1=20), 1, rationale=False)
    assert (before["team"], after["team"]) == ("T5", "T20")
    assert after["original_team"] == "T5"
    assert dr._pick_card(room(owner_of_1=9), 1, rationale=False)["user_controlled"]


def test_next_pick_and_undo_last_only():
    assert dr.next_pick([]) == 1 and dr.next_pick([{}] * 31) == 32 and dr.next_pick([{}] * 32) is None
    cur = mock.MagicMock()
    with mock.patch.object(dr, "_recorded", return_value=[{"overall_pick": 1, "prospect_key": "a"},
                                                          {"overall_pick": 2, "prospect_key": "b"}]):
        with pytest.raises(dr.DraftRoomError):
            dr.undo_last(cur, 2037, n=1)
        with mock.patch.object(dr, "state", return_value={"ok": True}):
            assert dr.undo_last(cur, 2037, n=2) == {"ok": True}
    deletes = [c for c in cur.execute.call_args_list if "DELETE FROM scout_draft_picks" in c.args[0]]
    assert deletes and deletes[0].args[1] == (2037, 2)


def test_premiums_follow_the_buyers_board():
    r = room()
    prem = dr.premiums(r["boards"], CONS, r["available"], owner=5)
    assert 5 not in prem and all(dr.PREMIUM_RANGE[0] <= v <= dr.PREMIUM_RANGE[1] for v in prem.values())
    hot = {**r["boards"], 6: [(KEYS[0], 99.0, 1)]}
    assert dr.premiums(hot, CONS, r["available"], owner=5)[6] > dr.premiums(r["boards"], CONS, r["available"], 5)[7]


def test_validate_rows():
    rec = [{"overall_pick": 1, "team_id": 5, "prospect_id": 11}, {"overall_pick": 2, "team_id": 6, "prospect_id": 12},
           {"overall_pick": 3, "team_id": 7, "prospect_id": 13}]
    recap = {1: (11, 5), 2: (99, 6), 3: (13, 8)}
    out = dr.validate_rows(rec, recap)
    assert [(n, m) for n, m, _, _ in out] == [(1, "MATCH"), (2, "MISMATCH"), (3, "MISMATCH")]
    assert out[1][3] == "different player" and out[2][3] == "different team" and out[1][2] == 99
    assert dr.validate_rows([{"overall_pick": 4, "team_id": 1, "prospect_id": 1}], {})[0][1] == "MISMATCH"


def test_control_steps_name_the_player():
    lines = dr.control_steps("Carolina Panthers", {"full_name": "X Y", "position": "QB", "college": "UCLA"}, True)
    assert "X Y (QB, UCLA)" in lines[0] and "advice only" in lines[1]
