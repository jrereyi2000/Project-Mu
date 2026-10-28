"""GM offseason, draft day and season rollover (docs/done/team_management/03_GM_AGENTS_PLAN.md Phase 7). Pure: no DB."""
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


class WindowCursor:
    def __init__(self, week, sb_played):
        self.week, self.sb, self.q = week, sb_played, None

    def execute(self, q, args=None):
        self.q = q

    def fetchone(self):
        if "league_state" in self.q:
            return (2036, self.week)
        return (1,) if self.sb else None


def test_league_window_after_the_super_bowl():
    assert ge.league_window(WindowCursor(4, False)) == (2036, 4, "IN_SEASON")
    assert ge.league_window(WindowCursor(12, False)) == (2036, 12, None)
    assert ge.league_window(WindowCursor(23, False)) == (2036, 23, None)            # Super Bowl still to play
    assert ge.league_window(WindowCursor(23, True)) == (2036, ge.OFFSEASON_WEEK, "OFFSEASON")
    assert ge.league_window(WindowCursor(24, False)) == (2036, 24, "OFFSEASON")


def test_offseason_trades_the_next_seasons_contracts_and_cap():
    from engines.trade_value import tradeable
    assert ge.value_season(2036, "OFFSEASON") == ge.value_season(2036, "DRAFT_DAY") == 2037
    assert ge.value_season(2036, "IN_SEASON") == 2036
    p = {"id": 1, "group": "WR", "contract": {"end_season": 2036, "no_trade_clause": False}}
    assert tradeable(p, season=2036) == (True, None)
    assert tradeable(p, season=2037) == (False, "contract expired")              # a free agent now


def test_next_stage():
    assert ge.next_stage(None) == 1 and ge.next_stage(4) == 5 and ge.next_stage(5) is None


def test_offseason_moves_use_the_new_cap_year():
    from pipeline import trade_moves as tm
    c = {"contract_id": 9, "season": 2036, "length": 3, "years_remaining": 3, "total_value": 9, "bonus": 0,
         "cut_savings": 0, "cut_penalty": 0, "no_trade_clause": False, "incentive": None,
         "years": {2036: (1_000_000, False), 2037: (4_000_000, False)}}
    state = {"teams": {1: {"name": "A"}, 2: {"name": "B"}}, "players": {7: {"team_id": 1, "pos": "WR", "name": "X Y",
                                                                           "contract": c}},
             "cap_rows": {1, 2}, "picks": {}, "cap_season": 2037}
    prop = {"id": 3, "season": 2036, "week": 24, "assets": [{"type": "PLAYER", "player_id": 7, "from": 1, "to": 2}]}
    ops, _ = tm.plan_trade_ops(prop, state)
    caps = {o["team_id"]: o for o in ops if o["op"] == "cap_delta"}
    assert caps[2]["salaries"] == 4_000_000 and caps[2]["season"] == 2037
    assert next(o for o in ops if o["op"] == "txn")["txn_date"] == "2036 OFFSEASON"
    _, warnings = tm.plan_trade_ops(prop, dict(state, cap_rows=set()))
    assert any("no 2037 cap row" in w for w in warnings)


def test_step_gm_next_stage(monkeypatch):
    """weekly_capture.step_gm(next_stage=True): the stage after the last one, only in the offseason."""
    import pipeline.weekly_capture as wc
    from engines import gm_trade_engine as gte
    runs, logs = [], []

    class Cur:
        def execute(self, q, a=None):
            pass

        def fetchone(self):
            return (True,)

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return Cur()

        def commit(self):
            pass

    monkeypatch.setattr("psycopg.connect", lambda *a, **k: Conn())
    monkeypatch.setattr("engines.gm_llm.make_llm", lambda *a, **k: None)
    monkeypatch.setattr(wc, "log", logs.append)
    monkeypatch.setattr(wc, "notify", lambda *a, **k: None)
    monkeypatch.setattr(gte, "run_week", lambda cur, season, week, **kw: runs.append(kw["stage"]) or
                        {"proposals": [], "expired": [], "rechecked": []})
    state = {"window": (2036, 24, "OFFSEASON"), "stage": 2}
    monkeypatch.setattr(gte, "league_window", lambda cur: state["window"])
    monkeypatch.setattr(gte, "read_stage", lambda cur, season: state["stage"])
    wc.step_gm(2036, next_stage=True)
    assert runs == [3]
    state["stage"] = 5
    wc.step_gm(2036, next_stage=True)
    state.update(stage=None, window=(2036, 5, "IN_SEASON"))
    wc.step_gm(2036, next_stage=True)
    assert runs == [3]                                                   # all done / not the offseason: nothing
    assert any("all 5 stages have run" in m for m in logs) and any("isn't in the offseason" in m for m in logs)
