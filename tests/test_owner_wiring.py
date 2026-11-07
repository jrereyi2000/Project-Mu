"""Owner wiring and the OWNER inbox kind (docs/in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md §2.3, §4, U5).

Offline only: pure engine functions, the league office test fakes and fake cursors. Off (and shadow) must give
today's results; active applies the owner terms; JR's teams never get one.
"""
import pytest

from engines import coach_hot_seat as chs
from engines import coaching_carousel as cc
from engines import gm_rollover as gr
from engines import owner_context as oc
from engines import owner_policy as op
from engines import transactions_engine as te
from engines.inbox import InboxError, appraisal, registry
from engines.inbox.kinds import owner as owner_kind
from pipeline import league_office_personality as lop
from tests import test_league_office_personality as tlop
from tests import test_transactions_engine as tte

SEASON = 2030
PATIENT = {"traits": ["PATIENT_BUILDER", "HANDS_OFF"], "sliders": {"temperament": 5, "drive": 5}}
HUNTER = {"traits": ["HEADLINE_HUNTER", "MEDDLER", "DEEP_POCKETS"], "sliders": {"temperament": 8, "drive": 8}}


def coach_row(tid, wins, *, start=SEASON - 3, user=False, **kw):
    return dict({"team_id": tid, "abbr": f"T{tid}", "coach_id": 100 + tid, "coach_name": f"Coach {tid}",
                 "start_season": start, "wins": wins, "losses": 17 - wins, "expected_wins": 8.5,
                 "made_playoffs": False, "history": [], "super_bowl_win": None, "user_controlled": user,
                 "coach_gm": False}, **kw)


# ------------------------------------------------------------------------------------------- owner context ---
def test_owner_views_are_the_32_reviewed_profiles():
    views = oc.owner_views()
    assert len(views) == 32
    car = views["CAR"]
    assert car["key"] == "owner:CAR" and car["traits"] and set(car["sliders"]) >= {"temperament", "drive"}


class Cur:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.sql, self._last = rows or {}, fail, [], None

    def execute(self, sql, params=()):
        self.sql.append(sql)
        if self.fail and "SAVEPOINT" not in sql:
            raise RuntimeError("boom")
        self._last = next((v for k, v in self.rows.items() if k in sql), [])

    def fetchone(self):
        return self._last[0] if self._last else None

    def fetchall(self):
        return list(self._last)


def test_feature_mode_reads_off_on_any_error_and_rejects_other_features():
    assert oc.feature_mode(Cur(fail=True), "owner_patience") == "off"
    assert oc.feature_mode(Cur({"to_regclass": [(False,)]}), "owner_hiring") == "off"
    with pytest.raises(ValueError):
        oc.feature_mode(Cur(), "media_voters")


def test_team_owner_views_by_abbreviation_without_excluded_teams():
    cur = Cur({"FROM teams": [(1, "CAR"), (2, "CHI"), (3, "XXX")]})
    views = oc.team_owner_views(cur, exclude={2}, views={"CAR": PATIENT, "CHI": HUNTER})
    assert views == {1: PATIENT}


def test_decisions_need_the_owner_decisions_table():
    cur = Cur({"to_regclass": [(False,)]})
    assert oc.record_decision(cur, season=SEASON, tick=23, team_id=1, move_kind="HIRE", move_ref={},
                              decision="VETO") is False
    assert oc.vetoes_used(cur, SEASON, 1) == 0


# --------------------------------------------------------------------------------------------- coach hot seat ---
def test_hot_seat_rows_without_owner_are_decided_as_before():
    rows = [coach_row(t, w) for t, w in ((1, 4), (2, 5), (3, 7), (4, 9), (5, 3), (6, 6), (7, 2))]
    assert chs.hot_seat(rows, SEASON) == chs.hot_seat([dict(r) for r in rows], SEASON)
    out = chs.hot_seat(rows, SEASON)
    assert all("owner" not in v["inputs"] for v in out.values())


def test_owner_line_scale_moves_the_line_and_tenure():
    patient = op.patience(PATIENT)
    hunter = op.patience(HUNTER)
    assert patient["hot_seat_line_scale"] > 1 > hunter["hot_seat_line_scale"]
    row = coach_row(1, 7, start=SEASON - 1)                     # second season
    base = chs.hot_seat([row], SEASON, min_fires=0)[1]
    with_patient = chs.hot_seat([dict(row, owner=patient)], SEASON, min_fires=0)[1]
    assert base["inputs"]["safe"] is None or base["inputs"]["safe"] == "below the line"
    assert with_patient["inputs"]["owner"]["min_tenure"] == chs.MIN_TENURE + 1
    assert with_patient["inputs"]["safe"] == "first season" and not with_patient["fire"]
    line = chs.hot_seat([dict(coach_row(2, 6), owner=hunter)], SEASON, min_fires=0)[2]["inputs"]["owner"]["line"]
    assert line == round(chs.HOT_SEAT_LINE * hunter["hot_seat_line_scale"], 4)


def test_owner_terms_never_fire_jr_and_keep_the_league_caps():
    hunter = op.patience(HUNTER)
    rows = [dict(coach_row(t, 3), owner=hunter) for t in range(1, 15)] + [dict(coach_row(20, 0, user=True),
                                                                            owner=hunter)]
    out = chs.hot_seat(rows, SEASON)
    assert sum(v["fire"] for v in out.values()) <= chs.MAX_FIRES
    assert not out[20]["fire"] and out[20]["inputs"]["safe"] == "JR's team"
    exits = [dict(coach_row(t, 12, made_playoffs=True, playoff_wins=0, champion=False, flops_before=2),
                  owner=hunter) for t in range(1, 8)]
    assert sum(v["fire"] for v in chs.contender_seat(exits, SEASON).values()) <= chs.MAX_EXIT_FIRES


def test_hot_seat_decide_by_mode():
    rows = [coach_row(t, w, start=SEASON - 1) for t, w in ((1, 6), (2, 5), (3, 4))]
    terms = {1: op.patience(PATIENT), 2: op.patience(PATIENT), 3: op.patience(PATIENT)}
    decide = lambda rs: chs.hot_seat(rs, SEASON, min_fires=0)        # noqa: E731
    rep = {"owner_shadow": []}
    assert chs._decide(rows, decide, "off", terms, rep, "BLACK_MONDAY") == decide(rows)
    shadow = chs._decide(rows, decide, "shadow", terms, rep, "BLACK_MONDAY")
    assert shadow == decide(rows)                                     # shadow equals today
    active = chs._decide(rows, decide, "active", terms, rep, "BLACK_MONDAY")
    assert all(v["inputs"]["owner"]["min_tenure"] == chs.MIN_TENURE + 1 for v in active.values())
    assert {d["team_id"] for d in rep["owner_shadow"]} == {t for t in shadow if shadow[t]["fire"]}
    assert not any(v["fire"] for v in active.values())
    jr = [coach_row(9, 1, user=True)]
    assert "owner" not in chs._with_owner(jr, {9: terms[1]})[0]


# ------------------------------------------------------------------------------------------------ GM rollover ---
def gm_row(tid, wins, user=False, start=SEASON - 4):
    return {"team_id": tid, "abbr": f"T{tid}", "start_season": start, "direction": "CONTEND", "wins": wins,
            "wins_before": 5, "user_controlled": user, "fo_score": None, "fo_rank": None}


def test_gm_hot_seat_owner_terms():
    rows = [gm_row(t, 3) for t in range(1, 9)]
    assert gr.hot_seat(rows, SEASON) == gr.hot_seat([dict(r) for r in rows], SEASON)
    patient, hunter = op.patience(PATIENT), op.patience(HUNTER)
    young = dict(gm_row(1, 2, start=SEASON - 1), owner=patient)
    assert gr.hot_seat([young], SEASON)[1]["inputs"]["owner"]["min_tenure"] == gr.MIN_TENURE + 1
    assert not gr.hot_seat([young], SEASON)[1]["fire"]
    owned = [dict(r, owner=hunter) for r in rows] + [dict(gm_row(20, 0, user=True), owner=hunter)]
    out = gr.hot_seat(owned, SEASON)
    assert sum(v["fire"] for v in out.values()) <= gr.MAX_FIRES and not out[20]["fire"]


# --------------------------------------------------------------------------------------------------- carousel ---
def test_carousel_hiring_terms_are_bounded_and_directional():
    pool = [{"key": "coach:1", "kind": "RETREAD", "score": 0.1, "former_head_coach": True},
            {"key": "coord:2", "kind": "POACHED", "score": 0.3, "age": 40},
            {"key": "outside:X", "kind": "OUTSIDE", "score": 0.5}]
    hunter = {c["key"]: c["score"] for c in cc.owner_adjusted(pool, HUNTER)}
    patient = {c["key"]: c["score"] for c in cc.owner_adjusted(pool, PATIENT)}
    assert hunter["coach:1"] > 0.1 and patient["coord:2"] > 0.3
    for adj in (cc.owner_adjusted(pool, HUNTER), cc.owner_adjusted(pool, PATIENT)):
        for a, b in zip(adj, pool):
            assert abs(a["score"] - b["score"]) <= op.HIRING_CAP
    assert cc.owner_adjusted(pool, None) == pool                       # a neutral owner changes nothing
    assert cc.owner_alternative(pool, pool[2], HUNTER)["key"] == "coach:1"
    assert cc.owner_alternative(pool, pool[0], {"traits": [], "sliders": {}}) is None
    assert op.appeal_term(HUNTER) == op.MEDDLER_APPEAL and op.appeal_term(PATIENT) == 0


# --------------------------------------------------------------------------------------- transactions engine ---
def test_offers_with_no_spending_are_todays_offers_and_knobs_bind():
    pool = [tte.fa(900 + i, g, 82) for i, g in enumerate(["QB", "WR", "CB", "EDGE", "OT", "S", "DT", "LB"] * 3)]
    lg = tte.base_league(pool)
    base = te.plan_offers(lg, "FREE_AGENCY", 3, "seed")
    assert te.plan_offers(lg, "FREE_AGENCY", 3, "seed", spending=None) == base
    assert te.plan_offers(lg, "FREE_AGENCY", 3, "seed", spending={}) == base
    deep, budget = op.spending(HUNTER), op.spending({"traits": ["BUDGET_CONSCIOUS"]})
    out = te.plan_offers(lg, "FREE_AGENCY", 3, "seed", spending={1: deep, 2: budget})
    n = lambda offers, t: len([o for o in offers if o["team_id"] == t])           # noqa: E731
    assert n(out, 2) <= budget["offers_per_wave"] and n(out, 1) <= deep["offers_per_wave"]
    assert n(out, 1) >= n(base, 1)
    others = [o for o in out if o["team_id"] not in (1, 2)]
    assert len(others) > 0


# ------------------------------------------------------------------------------------ league office wiring ---
class OwnerFeatures(tlop.FakeFeatures):
    def __init__(self, views=None, used=0):
        super().__init__()
        self.views, self.used, self.recorded = views or {}, used, []

    def owner_views(self, cur, exclude=()):
        return {t: v for t, v in self.views.items() if t not in set(exclude)}

    def owner_vetoes_used(self, cur, season, team_id):
        return self.used

    def owner_record(self, cur, **decision):
        self.recorded.append(decision)
        return True


def test_cpu_offers_off_is_the_baseline_call():
    deps = tlop.PDeps(tlop.snap_modes(), features=OwnerFeatures({1: HUNTER}), cpu=[{"team_id": 1}])
    assert deps.cpu_offers(None, SEASON, "FREE_AGENCY", 1, "s") == [{"team_id": 1}]
    assert not deps.receipts


def test_cpu_offers_active_passes_cpu_owner_spending(monkeypatch):
    seen = {}

    def offers(cur, season, stage, wave, seed, *, spending=None):
        seen["spending"] = spending
        return [{"team_id": 1}]

    monkeypatch.setattr(te, "cpu_offers", offers)
    deps = tlop.PDeps(tlop.snap_modes(owner_spending="active"), features=OwnerFeatures({1: HUNTER, 2: PATIENT}),
                      users={2})
    assert deps.cpu_offers(None, SEASON, "FREE_AGENCY", 1, "s") == [{"team_id": 1}]
    assert seen["spending"] == {1: op.spending(HUNTER)}                 # JR's team 2 has no owner knobs
    assert deps.receipts[-1]["status"] == "applied"


def test_cpu_offers_shadow_returns_the_baseline_and_records_counts(monkeypatch):
    monkeypatch.setattr(te, "fetch_league", lambda cur, s, t=None: "league")
    monkeypatch.setattr(te, "plan_offers", lambda lg, st, w, seed, *, spending=None: [{"team_id": 1}] * 6)
    deps = tlop.PDeps(tlop.snap_modes(owner_spending="shadow"), features=OwnerFeatures({1: HUNTER}),
                      cpu=[{"team_id": 1}] * 4)
    assert deps.cpu_offers(None, SEASON, "FREE_AGENCY", 1, "s") == [{"team_id": 1}] * 4
    r = deps.receipts[-1]
    assert r["status"] == "recorded" and r["baseline"] == {"1": 4} and r["adjusted"] == {"1": 6}


def sign(team, player):
    return {"kind": "SIGN", "player_id": player, "team_id": team, "list": "Roster", "reason": "free agency",
            "contract": {}, "offer_id": player * 10}


def meddling_deps(mode, monkeypatch, decision="VETO", users=()):
    def choice(move, view, **kw):
        keys = [o.key for o in op.meddle_options(move, view, vetoes_used=kw.get("vetoes_used", 0))]
        return (decision if decision in keys else "APPROVE") if keys else None

    monkeypatch.setattr(op, "meddle_choice", choice)
    feats = OwnerFeatures({1: HUNTER, 2: HUNTER})
    deps = tlop.PDeps(tlop.snap_modes(owner_meddling=mode), features=feats, users=set(users))
    deps.begin_stage("FREE_AGENCY", SEASON, 25, 1, "s")
    deps._fa_rank = {11: 3, 12: 80, 21: 1}
    return deps, feats


def test_meddling_active_drops_a_vetoed_top_signing_once(monkeypatch):
    deps, feats = meddling_deps("active", monkeypatch, users={2})
    moves = [sign(1, 11), sign(1, 12), sign(2, 21)]
    monkeypatch.setattr(lop.lo.Deps, "signings_from_decisions", lambda self, d, o: moves)
    kept = deps.signings_from_decisions([], [])
    assert [m["player_id"] for m in kept] == [12, 21]       # 12 isn't top-50; 21 is JR's team: never reviewed
    assert feats.recorded and feats.recorded[0]["decision"] == "VETO"
    # the veto cap: a second top signing for team 1 this season can't be vetoed
    deps._fa_rank[13] = 2
    monkeypatch.setattr(lop.lo.Deps, "signings_from_decisions", lambda self, d, o: [sign(1, 13)])
    assert [m["player_id"] for m in deps.signings_from_decisions([], [])] == [13]


def test_meddling_shadow_keeps_every_signing_and_records(monkeypatch):
    deps, feats = meddling_deps("shadow", monkeypatch)
    moves = [sign(1, 11), sign(2, 21)]
    monkeypatch.setattr(lop.lo.Deps, "signings_from_decisions", lambda self, d, o: moves)
    assert deps.signings_from_decisions([], []) == moves
    assert not feats.recorded and deps.receipts[-1]["status"] == "recorded"
    assert {d["decision"] for d in deps.receipts[-1]["decisions"]} == {"VETO"}


def test_free_agent_pool_ranks_by_overall():
    deps = tlop.PDeps(tlop.snap_modes())
    deps.free_agent_pool(None, SEASON, SEASON + 1)
    assert list(deps._fa_rank.values()) == [1]


def test_owner_features_are_in_the_personality_report():
    assert {"owner_spending", "owner_meddling"} <= set(lop.LEAGUE_OFFICE_FEATURES)


# ---------------------------------------------------------------------------------------------- inbox OWNER ---
TEAM = {"team_id": 1, "abbr": "CAR", "season": SEASON, "week": 1, "offseason_stage": None, "actors": []}


class KindCur:
    """Answers the OWNER kind's reads: direction, record, games, season state and big moves."""

    def __init__(self, games=(), record=(8, 9), complete=False, trades=(), signings=()):
        self.games, self.record, self.complete = list(games), record, complete
        self.trades, self.signings, self._last = list(trades), list(signings), []

    def execute(self, sql, params=()):
        if "to_regclass" in sql:
            self._last = [(True,)]
        elif "FROM gm_team_seasons" in sql:
            self._last = [("REBUILD",)]
        elif "FROM team_seasons" in sql:
            self._last = [self.record]
        elif "count(*)" in sql and "FROM games" in sql:
            self._last = [(272, 0 if self.complete else 10)]
        elif "FROM games" in sql:
            self._last = self.games
        elif "franchise_pending_moves" in sql:
            self._last = [(i,) for i in self.trades]
        elif "contract_offers" in sql:
            self._last = list(self.signings)
        else:
            self._last = []

    def fetchone(self):
        return self._last[0] if self._last else None

    def fetchall(self):
        return list(self._last)


@pytest.fixture
def active(monkeypatch):
    monkeypatch.setattr(oc, "feature_mode", lambda cur, f: "active")


def test_owner_kind_is_registered_for_gms():
    kind = registry.kinds(refresh=True)["OWNER"]
    assert kind.COUNTERPART_TYPE == "owner" and registry.actor_roles(kind) == ("GM",)


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_owner_kind_sends_nothing_unless_active(monkeypatch, mode):
    monkeypatch.setattr(oc, "feature_mode", lambda cur, f: mode)
    assert owner_kind.scan(KindCur(), SEASON, 1, TEAM) == []


def test_owner_kind_season_start_expectation(active):
    trigs = owner_kind.scan(KindCur(), SEASON, 1, TEAM)
    assert [t.intent for t in trigs] == ["EXPECTATION"]
    t = trigs[0]
    assert t.source_ref == f"owner:expect:{SEASON}" and t.counterpart_type == "owner"
    assert t.counterpart_id == "owner:CAR" and t.actor_role == "GM"
    assert t.facts["expectation"] == owner_kind.EXPECTATIONS["HEADLINE_HUNTER"]   # CAR: MEDDLER, HEADLINE_HUNTER
    assert "expect" in owner_kind.template(t.intent, t.facts)


def test_owner_kind_pressure_after_three_losses_and_a_losing_season(active):
    loss = lambda w: (w, 1, 10, 20)                                    # noqa: E731 - home team 1 loses
    win = lambda w: (w, 1, 30, 20)                                     # noqa: E731
    games = [win(1), loss(2), loss(3), loss(4)]
    trigs = owner_kind.scan(KindCur(games=games), SEASON, 5, TEAM)
    assert [t.source_ref for t in trigs] == [f"owner:pressure:{SEASON}:streak:2"]
    trigs = owner_kind.scan(KindCur(games=[win(1)], record=(6, 11), complete=True), SEASON, 19, TEAM)
    assert [t.source_ref for t in trigs] == [f"owner:pressure:{SEASON}:losing"]
    assert owner_kind.losing_streak_start([(1, False), (2, False)]) is None
    assert owner_kind.losing_streak_start([(1, False), (2, None), (3, False), (4, False), (5, False)]) == 3


def test_owner_kind_reacts_to_big_moves(active):
    trigs = owner_kind.scan(KindCur(trades=[7], signings=[(9, "Star Player")]), SEASON, 6, TEAM)
    assert [t.source_ref for t in trigs] == ["owner:move:trade:7", "owner:move:signing:9"]
    assert "Star Player" in owner_kind.template("REACTION", trigs[1].facts)


def test_owner_kind_reply_records_a_meaningful_interaction(monkeypatch):
    recorded = []
    monkeypatch.setattr(appraisal, "record", lambda cur, event, **kw: recorded.append((event, kw)))
    thread = {"id": 5, "kind": "OWNER", "team_id": 1, "season": SEASON, "week": 3, "offseason_stage": None,
              "actor_role": "GM", "actor_id": "12", "counterpart_type": "owner", "counterpart_id": "owner:CAR",
              "counterpart_name": "David Tepper"}
    res = owner_kind.act(None, thread, "PUSH_BACK", {}, None)
    assert res.close and res.judge is False and res.intent == "PUSHED_BACK"
    event, kw = recorded[0]
    assert event["kind"] == "meaningful_interaction" and event["payload"]["outcome"] == "mixed"
    assert event["subject"]["_alias"]["namespace"] == "db-owner" and kw == {"thread_id": 5}
    with pytest.raises(InboxError):
        owner_kind.act(None, thread, "FIRE_HIM", {}, None)
    with pytest.raises(InboxError):
        owner_kind.start(None, TEAM, {})
