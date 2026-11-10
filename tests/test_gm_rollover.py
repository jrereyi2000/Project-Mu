"""GM offseason, draft day and season rollover (docs/done/team_management/03_GM_AGENTS_PLAN.md Phase 7). Pure: no DB."""
from engines import gm_personas, gm_rollover as gr, gm_trade_engine as ge, gm_voices
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


def test_hot_seat_hire_writes_a_card(monkeypatch):
    """gm_rollover._hire_gm -> make_persona -> insert_gm: the hired GM always carries a card and a source."""
    from engines import coaching_carousel as cc
    monkeypatch.setattr(cc, "opening_parts", lambda cur, prior, ids: {})
    monkeypatch.setattr(cc, "opening_appeal", lambda parts: 0.5)
    monkeypatch.setattr(gr, "_gm_budget", lambda *a, **k: 20_000_000)
    monkeypatch.setattr(gr, "gm_candidate_pool", lambda abbr, season: [])
    monkeypatch.setattr(gr, "gm_search",
                        lambda pool, appeal=None, budget=None:
                        {"tier": "STANDARD", "salary": 1_000_000, "stature": 0.5})
    monkeypatch.setattr(gm_personas, "read_teams",
                        lambda cur, season: {"ATL": {"id": 1, "city": "Atlanta", "name": "Falcons",
                                                     "coach": "X"}})
    monkeypatch.setattr(gm_personas, "read_open", lambda cur: {})
    captured = {}

    def fake_insert(cur, team_id, p, traits, seed):
        captured.update({"p": p, "team_id": team_id, "traits": traits, "seed": seed})
        return 99

    monkeypatch.setattr(gm_personas, "insert_gm", fake_insert)

    class Cur:
        def execute(self, q, a=None):
            pass

        def fetchone(self):
            return None

    rep = gr._hire_gm(Cur(), 2037, 2036, "ATL", 1, None, why="hot seat", log=lambda *a: None)
    p = captured["p"]
    assert gm_voices.parse_card(p["voice_card"]) is not None
    assert p["voice_card_source"] in {"AUTHORED", "LLM", "TEMPLATE"}
    assert rep["gm"] == p["gm_name"]


# ---------------------------------------------------- competitive GM search (GM_HIRING_SEARCH_PLAN §2.3) ----
def test_gm_candidate_pool_salaries_and_acceptance_floors():
    pool = {c["tier"]: c for c in gr.gm_candidate_pool("CAR", 2036)}
    assert pool["PROMOTION"]["salary"] == 2_750_000
    assert pool["STANDARD"]["salary"] == 5_750_000
    assert pool["ELITE"]["salary"] == 10_000_000
    assert pool["PROMOTION"]["salary"] < pool["STANDARD"]["salary"] < pool["ELITE"]["salary"]
    assert {t: c["min_appeal"] for t, c in pool.items()} == {"ELITE": 0.55, "STANDARD": 0.35, "PROMOTION": 0.0}
    assert gr.GM_MIN_APPEAL == {"ELITE": 0.55, "STANDARD": 0.35, "PROMOTION": 0.0}
    assert gr.GM_APPEAL_WEIGHT == {"ELITE": 0.40, "STANDARD": 0.20, "PROMOTION": 0.05}


def test_gm_candidate_score_weights_appeal_by_tier():
    elite = {"key": "E", "tier": "ELITE", "stature": 0.80, "min_appeal": 0.55}
    standard = {"key": "S", "tier": "STANDARD", "stature": 0.50, "min_appeal": 0.35}
    assert gr.gm_candidate_score(elite, 0.75) == round(0.80 + 0.40 * (0.75 - 0.55), 4)
    assert gr.gm_candidate_score(standard, 0.75) == round(0.50 + 0.20 * (0.75 - 0.35), 4)
    assert gr.gm_candidate_score(elite, 0.50) == 0.80          # appeal below the floor is never a surplus


def test_chunk7_g1_appeal_changes_the_pick():
    pool = gr.gm_candidate_pool("CAR", 2036)
    picks = {appeal: gr.gm_search(pool, appeal=appeal, budget=30_000_000)["tier"]
             for appeal in (0.0, 0.5, 1.0)}
    assert picks == {0.0: "PROMOTION", 0.5: "STANDARD", 1.0: "ELITE"}


def test_chunk7_g2_all_tiers_reachable_by_budget():
    pool = gr.gm_candidate_pool("CAR", 2036)
    picks = {budget: gr.gm_search(pool, appeal=0.6, budget=budget)["tier"]
             for budget in (0, 1_000_000, 5_750_000, 10_000_000)}
    assert picks == {0: "PROMOTION", 1_000_000: "PROMOTION", 5_750_000: "STANDARD", 10_000_000: "ELITE"}
    assert set(picks.values()) == {"PROMOTION", "STANDARD", "ELITE"}


def test_gm_search_records_appeal_and_budget_refusals():
    pool = gr.gm_candidate_pool("CAR", 2036)
    low = gr.gm_search(pool, appeal=0.40, budget=50_000_000)        # ELITE refuses below its 0.55 floor
    assert low["tier"] == "STANDARD"
    assert next(d for d in low["declined"] if d["tier"] == "ELITE") == {
        "key": "CAR:ELITE", "tier": "ELITE", "reason": "appeal", "min_appeal": 0.55, "appeal": 0.40}

    willing = gr.gm_search(pool, appeal=0.70, budget=6_000_000)     # ELITE accepts but cannot be afforded
    assert willing["tier"] == "STANDARD"
    assert next(d for d in willing["declined"] if d["tier"] == "ELITE") == {
        "key": "CAR:ELITE", "tier": "ELITE", "reason": "budget", "salary": 10_000_000, "budget": 6_000_000}


def test_meddler_owner_pushes_a_0_56_opening_below_the_elite_floor():
    pool = gr.gm_candidate_pool("CAR", 2036)
    res = gr.gm_search(pool, appeal=0.56, budget=50_000_000, owner_view={"traits": ["MEDDLER"]})
    assert res["effective_appeal"] == 0.51                         # appeal_term -0.05
    assert res["tier"] == "STANDARD"
    assert any(d["tier"] == "ELITE" and d["reason"] == "appeal" and d["appeal"] == 0.51
               for d in res["declined"])


def test_owner_can_pick_promotion_over_a_low_stature_standard():
    view = {"traits": ["BUDGET_CONSCIOUS", "MEDDLER"], "sliders": {"openness": 10}}
    pool = [
        {"key": "T:ELITE", "tier": "ELITE", "salary": 10_000_000, "stature": 0.85, "min_appeal": 0.55},
        {"key": "T:STANDARD", "tier": "STANDARD", "salary": 5_750_000, "stature": 0.48, "min_appeal": 0.35},
        {"key": "T:PROMOTION", "tier": "PROMOTION", "salary": 2_750_000, "stature": 0.38, "min_appeal": 0.0},
    ]
    res = gr.gm_search(pool, appeal=0.58, budget=30_000_000, owner_view=view)
    assert res["effective_appeal"] == 0.53                         # MEDDLER appeal_term
    assert res["tier"] == "PROMOTION"                              # BUDGET_CONSCIOUS + MEDDLER + openness
    assert gr.gm_search(pool, appeal=0.58, budget=30_000_000)["tier"] == "ELITE"   # a neutral owner picks ELITE


# ---------------------------------------------------- owner_hiring wiring in _hire_gm (§2.4) ----
OWNER_HIRE_POOL = [
    {"key": "ATL:ELITE", "tier": "ELITE", "salary": 10_000_000, "stature": 0.85, "min_appeal": 0.55},
    {"key": "ATL:STANDARD", "tier": "STANDARD", "salary": 5_750_000, "stature": 0.55, "min_appeal": 0.35},
    {"key": "ATL:PROMOTION", "tier": "PROMOTION", "salary": 2_750_000, "stature": 0.30, "min_appeal": 0.0},
]


def _hire_with_owner(monkeypatch, mode, *, user_ids=(), view=None):
    from engines import coaching_carousel as cc
    from engines import owner_context as oc
    monkeypatch.setattr(cc, "opening_parts", lambda cur, prior, ids: {})
    monkeypatch.setattr(cc, "opening_appeal", lambda parts: 0.56)          # ELITE accepts a neutral owner, not a MEDDLER
    monkeypatch.setattr(gr, "_gm_budget", lambda *a, **k: 30_000_000)
    monkeypatch.setattr(gr, "gm_candidate_pool", lambda abbr, season: [dict(c) for c in OWNER_HIRE_POOL])
    monkeypatch.setattr(oc, "feature_mode", lambda cur, f: mode)
    monkeypatch.setattr(oc, "user_team_ids", lambda cur: set(user_ids))
    monkeypatch.setattr(oc, "team_owner_views", lambda cur, **k: {1: view} if view is not None else {})
    monkeypatch.setattr(gm_personas, "read_teams",
                        lambda cur, season: {"ATL": {"id": 1, "city": "Atlanta", "name": "Falcons", "coach": "X"}})
    monkeypatch.setattr(gm_personas, "read_open", lambda cur: {})
    monkeypatch.setattr(gm_personas, "insert_gm", lambda *a, **k: 99)

    class Cur:
        def execute(self, q, a=None):
            pass

        def fetchone(self):
            return None

    return gr._hire_gm(Cur(), 2037, 2036, "ATL", 1, None, why="hot seat", log=lambda *a: None)


def test_hire_gm_owner_hiring_off_shadow_and_active(monkeypatch):
    meddler = {"traits": ["MEDDLER"]}
    off = _hire_with_owner(monkeypatch, "off", view=meddler)
    assert off["tier"] == "ELITE" and "owner_shadow" not in off

    shadow = _hire_with_owner(monkeypatch, "shadow", view=meddler)
    assert shadow["tier"] == "ELITE"                              # shadow hires as off
    assert shadow["owner_shadow"] == {"picked": "ELITE", "owner_pick": "STANDARD",
                                      "appeal": 0.56, "owner_appeal": 0.51}

    active = _hire_with_owner(monkeypatch, "active", view=meddler)
    assert active["tier"] == "STANDARD"                           # the owner's terms apply
    assert active["effective_appeal"] == 0.51
    assert "owner_shadow" not in active
    assert any(d["key"] == "ATL:ELITE" and d["reason"] == "appeal" for d in active["declined"])


def test_hire_gm_never_applies_owner_terms_to_jrs_teams(monkeypatch):
    for mode in ("shadow", "active"):
        rep = _hire_with_owner(monkeypatch, mode, user_ids={1}, view={"traits": ["MEDDLER"]})
        assert rep["tier"] == "ELITE" and "owner_shadow" not in rep


def test_hire_gm_without_a_reviewed_owner_uses_the_neutral_search(monkeypatch):
    for mode in ("shadow", "active"):
        rep = _hire_with_owner(monkeypatch, mode, view=None)
        assert rep["tier"] == "ELITE" and "owner_shadow" not in rep


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


def test_the_post_super_bowl_hot_seat_scores_the_season_just_played(monkeypatch):
    """chunk-7 B10 (CONFIRMED by the audit): the hot seat runs before the coming season is scanned, and
    `fo_quality.fetch_rows` joins `player_seasons` for the season it scores — so scoring season+1 found no rated rows
    and every GM's drafting fell to None, which `hot_seat_term` counts as 0."""
    from engines import fo_standing

    calls = []
    monkeypatch.setattr(fo_standing, "live",
                        lambda cur, season: calls.append(season) or [{"team_id": 1, "score": 0.5, "rank": 2}])

    class Cur:
        def execute(self, sql, params=None):
            # a populated card for 2036 only: 3,000 rows, every one rated
            self._row = (3000, 3000) if params and params[0] == 2036 and "FROM player_seasons" in sql else (
                (0, 0) if "FROM player_seasons" in sql else None)

        def fetchone(self):
            return self._row

    assert gr.front_office_scores(Cur(), 2037, [1]) == [{"fo_score": 0.5, "fo_rank": 2}]
    assert calls == [2036]                       # the season whose card exists, not the coming one


def test_a_season_whose_card_exists_is_scored_as_itself():
    class Cur:
        def execute(self, sql, params=None):
            self._row = (3000, 2990) if params and params[0] == 2037 else (0, 0)

        def fetchone(self):
            return self._row

    assert gr.scored_season(Cur(), 2037) == 2037


def test_a_stub_card_does_not_count_and_the_fallback_says_so():
    """M-0107: a handful of rated rows is not a card — a half-loaded scan must not pass for one."""
    class Cur:
        def execute(self, sql, params=None):
            self._row = (3000, 5) if params and params[0] == 2036 else (0, 0)   # 2036: five rated rows

        def fetchone(self):
            return self._row

    logs = []
    assert gr.scored_season(Cur(), 2037, log=logs.append) == 2037       # nothing qualifies: the asked season
    assert any("no rated player card" in m for m in logs)               # and it says so


def test_the_fallback_hire_is_not_listed_among_the_refusals():
    """M-0105 B1: with nobody affordable the search hires the cheapest willing candidate and also recorded him as a
    budget refusal — 'he declined and we hired him anyway'."""
    pool = gr.gm_candidate_pool("CAR", 2036)
    res = gr.gm_search(pool, appeal=0.9, budget=0)
    assert res["tier"] == "PROMOTION"
    assert res["key"] not in {d["key"] for d in res["declined"]}
    assert res["declined"]                                             # the others are still named


def test_a_failing_owner_read_does_not_abort_the_hire(monkeypatch):
    """M-0105 B2: `team_owner_views` ran without a savepoint, so a SQL error left the caller's transaction aborted
    and took the hire — and everything after it — with it."""
    from engines import coaching_carousel as cc
    from engines import owner_context as oc
    monkeypatch.setattr(cc, "opening_parts", lambda cur, prior, ids: {})
    monkeypatch.setattr(cc, "opening_appeal", lambda parts: 0.56)
    monkeypatch.setattr(gr, "_gm_budget", lambda *a, **k: 30_000_000)
    monkeypatch.setattr(gr, "gm_candidate_pool", lambda abbr, season: [dict(c) for c in OWNER_HIRE_POOL])
    monkeypatch.setattr(oc, "feature_mode", lambda cur, f: "active")
    monkeypatch.setattr(oc, "user_team_ids", lambda cur: set())

    def boom(cur, **k):
        raise RuntimeError("no such column: owner_traits")

    monkeypatch.setattr(oc, "team_owner_views", boom)
    monkeypatch.setattr(gm_personas, "read_teams",
                        lambda cur, season: {"ATL": {"id": 1, "city": "Atlanta", "name": "Falcons", "coach": "X"}})
    monkeypatch.setattr(gm_personas, "read_open", lambda cur: {})
    monkeypatch.setattr(gm_personas, "insert_gm", lambda *a, **k: 99)

    class Cur:
        def __init__(self):
            self.statements = []

        def execute(self, q, a=None):
            self.statements.append(" ".join(str(q).split()).upper())

        def fetchone(self):
            return None

    cur = Cur()
    rep = gr._hire_gm(cur, 2037, 2036, "ATL", 1, None, why="hot seat", log=lambda *a: None)
    assert rep["tier"] == "ELITE"                                      # the neutral search still hires
    assert "ROLLBACK TO SAVEPOINT GM_OWNER_VIEW" in cur.statements      # and the failed read was contained


def test_a_fired_gm_is_bought_out_from_the_firing_season():
    """M-0150 M1: the settle used the stored remaining_years (last rolled for the previous league year) and started
    the schedule in the firing season, so a 4-year deal paid 5 seasons."""
    class Cur:
        def __init__(self):
            self.sql = []

        def execute(self, q, a=None):
            self.sql.append((" ".join(str(q).split()), a))

        def fetchone(self):
            if self.sql[-1][0].startswith("SELECT id, annual_salary"):
                return (9, 3_000_000, 3, 0, 4, 2033)       # stored remaining 3, but the deal ends after 2036
            return None

    cur = Cur()
    gr._settle_gm_contract(cur, 101, log=lambda *a: None, person_season=2035)
    updates = [a for s, a in cur.sql if s.startswith("UPDATE staff_contracts")]
    assert len(updates) == 1
    remaining, buyout, total, fired, years, row_id = updates[0]
    assert (remaining, row_id) == (0, 9)
    assert (buyout, total) == (6_000_000, 6_000_000)       # 2035 and 2036, not the stored three
    assert (fired, years) == (2035, 2)                     # charged from the firing season, which no payroll covers
