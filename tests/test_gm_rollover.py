"""GM offseason, draft day and season rollover (docs/team_management/03_GM_AGENTS_PLAN.md Phase 7). Pure: no DB."""
from engines import gm_trade_engine as ge
from engines.gm_personas import pick_seed_rows, picks_to_seed
from engines.gm_rollover import MAX_FIRES, hot_seat, rollover_actions

TEAMS = list(range(5, 37))


def gm_row(tid, wins=3, direction="CONTEND", start=2030, before=5, user=False):
    return {"team_id": tid, "abbr": f"T{tid}", "start_season": start, "direction": direction, "wins": wins,
            "wins_before": before, "user_controlled": user}


def test_hot_seat_rules():
    rows = [gm_row(t) for t in TEAMS]
    out = hot_seat(rows, 2036)
    assert out == hot_seat(rows, 2036)                                   # seeded
    assert sum(v["fire"] for v in out.values()) == MAX_FIRES              # capped
    good = hot_seat([gm_row(5, wins=13)], 2036)[5]
    assert not good["fire"] and good["score"] < 0
    assert not hot_seat([gm_row(5, start=2036)], 2036)[5]["fire"]         # one season in the job: safe
    assert not hot_seat([gm_row(5, user=True)], 2036)[5]["fire"]          # JR's team keeps its GM


def state(**kw):
    s = {"season": 2037, "team_ids": TEAMS, "hot_seat_decided": False, "hot_seat_rows": [gm_row(5)],
         "team_rows": set(), "direction_missing": set(), "fo_carry": {5}, "picks_existing": set(), "unused_past": 224}
    s.update(kw)
    return s


def test_rollover_is_idempotent():
    first = rollover_actions(state())
    assert first["hot_seat"] is not None and len(first["team_rows"]) == 32 and first["mark_used"]
    assert len(first["picks"]) == 32 * 7 * 3
    done = state(hot_seat_decided=True, team_rows=set(TEAMS), fo_carry=set(),
                 picks_existing={r[:3] for r in pick_seed_rows(TEAMS, 2037)}, unused_past=0)
    again = rollover_actions(done)
    assert again == {"hot_seat": None, "team_rows": [], "directions": [], "fo_carry": [], "picks": [],
                     "mark_used": False}


def test_pick_seeding_never_overwrites_traded_picks():
    existing = {r[:3] for r in pick_seed_rows(TEAMS, 2036)}               # 2037-2039 exist, some traded
    new = picks_to_seed(existing, TEAMS, 2037)                           # the new season adds 2040 only
    assert {r[0] for r in new} == {2040} and len(new) == 32 * 7
    assert all(r[:3] not in existing for r in new)


def test_offseason_budget_paced():
    target = ge.offseason_target(2036)
    assert 15 <= target <= 20 and target == ge.offseason_target(2036)
    stages = range(1, len(ge.OFFSEASON_STAGES) + 1)
    executed, expected = 0.0, []
    for st in stages:                                                    # each stage executes what it expects
        e = ge.offseason_budget(2036, st, executed, 0, 0)["e_w"]
        expected.append(e)
        executed += e
    assert abs(executed - target) < 0.05
    assert expected[ge.DRAFT_STAGE - 1] == max(expected)                 # the draft stage weighs 3x
    assert abs(expected[0] - target / 7) < 1e-2
    assert all(ge.offseason_budget(2036, s, 0, 0, 0)["n"] <= ge.MAX_NEW_PER_WEEK for s in stages)
    assert ge.offseason_budget(2036, 2, target, target, 0)["e_w"] == 0


def test_offseason_and_draft_day_expiry():
    props = [{"id": 1, "status": "PROPOSED", "trade_window": "OFFSEASON", "flags": ["stage:2"], "expires_week": 24},
             {"id": 2, "status": "PROPOSED", "trade_window": "OFFSEASON", "flags": ["stage:3"], "expires_week": 24},
             {"id": 3, "status": "APPROVED", "trade_window": "DRAFT_DAY", "flags": ["pick:4"], "expires_week": 24},
             {"id": 4, "status": "PROPOSED", "trade_window": "IN_SEASON", "flags": [], "expires_week": 8}]
    assert [p for p, _ in ge.expire_stale(props, 24, "OFFSEASON", stage=3)] == [1, 4]
    assert [p for p, _ in ge.expire_stale(props, 24, "DRAFT_DAY", pick=5)] == [3, 4]
    assert [p for p, _ in ge.expire_stale(props, 24, "DRAFT_DAY", pick=4)] == [4]
    assert [p for p, _ in ge.expire_stale(props, 1, "IN_SEASON")] == [1, 2, 3]   # the offseason is over
    assert ge.window_for(24) == "OFFSEASON" and ge.window_for(12) is None and ge.window_for(8) == "IN_SEASON"
    assert ge.trades_open(24, "DRAFT_DAY") and ge.roster_max("OFFSEASON") == 65


def test_draft_day_probability_is_capped_and_paced():
    p = ge.draft_day_p(2036, 0, 0, 0)
    assert 0 < p <= ge.DRAFT_DAY_MAX_P
    assert ge.draft_day_p(2036, 20, 20, 0) == 0                          # target met: no more trades
