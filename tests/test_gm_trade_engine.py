"""GM trade proposal engine (docs/done/team_management/03_GM_AGENTS_PLAN.md Phase 4). Synthetic books: no database."""
import math

from engines import gm_trade_engine as ge
from engines import trade_value as tv
from engines.gm_direction import SLOTS

SEASON, WEEK = 2036, 4


def fits():
    rows = [(g, o, math.exp(15 + 0.09 * (o - 75))) for g in ("QB", "WR", "CB", "EDGE", "OT") for o in range(65, 98)]
    draft = [(p, 78 - 4 * math.log(p), 13e6 * p ** -0.55 + 6e5) for p in range(1, 225)]
    return tv.build_fits(rows, [], draft)


FITS = fits()


def make_player(pid, tid, group, ovr, age=27, hit=2_000_000, end=2037, ntc=False, penalty=0):
    pos = {"EDGE": "LEDG", "OT": "LT", "IOL": "LG", "LB": "MIKE", "S": "FS", "RB": "HB"}.get(group, group)
    return {"id": pid, "team_id": tid, "pos": pos, "group": group, "ovr": ovr, "age": age, "dev": "Normal",
            "name": f"Player {pid}",
            "contract": {"contract_id": pid * 10, "end_season": end, "no_trade_clause": ntc, "cut_penalty": penalty,
                         "years": {y: (hit, False) for y in range(SEASON, end + 1)}}}


def roster(tid, base, ovr=75):
    """Starters at `ovr`, one backup per group 5 below, padded to 50 with low LBs."""
    out = {}
    pid = base
    for g, n in SLOTS.items():
        for k in range(n + 1):
            pid += 1
            out[pid] = make_player(pid, tid, g, ovr if k < n else ovr - 5)
    while len(out) < 50:
        pid += 1
        out[pid] = make_player(pid, tid, "LB", 55)
    return out


def books(direction_a="CONTEND", direction_b="REBUILD", cap_a=20_000_000, cap_b=20_000_000, week=WEEK, extra=None,
          open_=None, injuries=None, gm_a=None):
    players = {}
    players.update(roster(1, 100))
    players.update(roster(2, 200))
    for p in (extra or []):
        players[p["id"]] = p
    b = {"season": SEASON, "week": week, "window": "IN_SEASON",
         "teams": {1: {"abbr": "AAA", "city": "Alpha", "name": "Ants"}, 2: {"abbr": "BBB", "city": "Beta", "name": "Bees"}},
         "gm": {1: dict({"gm_id": 1, "gm_name": "Ann A", "aggressiveness": 0.6, "risk_appetite": 0.5,
                         "pick_affinity": 1.0, "youth_bias": 0.0, "position_bias": {}, "voice": "", "user_controlled": False,
                         "suggestions_on": True}, **(gm_a or {})),
                2: {"gm_id": 2, "gm_name": "Bob B", "aggressiveness": 0.6, "risk_appetite": 0.5, "pick_affinity": 1.0,
                    "youth_bias": 0.0, "position_bias": {}, "voice": "", "user_controlled": False,
                    "suggestions_on": True}},
         "ts": {1: {"direction": direction_a, "needs": [{"group": "WR", "score": 10}], "surplus": [], "urgent_need": None,
                    "proj_wins": 11},
                2: {"direction": direction_b, "needs": [{"group": "CB", "score": 5}], "surplus": [], "urgent_need": None,
                    "proj_wins": 4}},
         "cap": {1: {"cap_space": cap_a, "derived": False}, 2: {"cap_space": cap_b, "derived": False}},
         "players": players, "injuries": injuries or {}, "acquired": set(),
         "picks": {(2037, r, t): t for r in range(1, 8) for t in (1, 2)},
         "open": open_ or [], "executed": 0, "decided": 0, "first_week": 1, "made_this_week": 0, "hashes": set()}
    return ge.finish_books(b)


STAR = 900


def star_books(**kw):
    star = make_player(STAR, 2, "WR", 82, age=30, hit=5_000_000, end=2036)
    return books(extra=[star] + kw.pop("extra", []), **kw)


def deal(assets, a=1, b=2):
    return {"a": a, "b": b, "assets": assets, "trigger": "NEED"}


def pl(pid, frm, to):
    return {"type": "PLAYER", "player_id": pid, "from": frm, "to": to}


def pk(rd, orig, frm, to, season=2037):
    return {"type": "PICK", "pick": (season, rd, orig), "from": frm, "to": to}


def base_deal():
    return deal([pl(STAR, 2, 1), pk(4, 1, 1, 2)])      # an 82 OVR veteran on an expiring deal: a 4th (the ladder)


def test_legal_baseline():
    b = star_books()
    checks = ge.check_legality(base_deal(), b, ge.Valuer(b, FITS))
    assert ge.legal(checks), checks


def test_cap_fails():
    b = star_books(cap_a=3_000_000)
    assert not ge.check_legality(base_deal(), b)["cap"]["ok"]


def test_ntc_fails():
    b = star_books()
    b["players"][STAR]["contract"]["no_trade_clause"] = True
    assert not ge.check_legality(base_deal(), b)["ntc"]["ok"]


def test_roster_fails_over_max():
    b = star_books()
    while len(b["roster"][1]) < ge.ROSTER_MAX_IN_SEASON:
        pid = 5000 + len(b["roster"][1])
        b["players"][pid] = make_player(pid, 1, "LB", 60)
        b["roster"][1].append(pid)
    chk = ge.check_legality(base_deal(), b)["roster"]                   # at 53, taking one more: release first
    assert chk["ok"] and chk["release"] == {"AAA": b["players"][min(
        (pid for pid in b["roster"][1] if pid not in b["ctx"][1]["starter_ids"]
         and pid not in b["ctx"][1]["rotation_ids"] and b["players"][pid]["group"] not in ("QB", "K", "P")),
        key=lambda pid: (b["players"][pid]["ovr"], -b["players"][pid]["age"]))]["name"]}
    assert ge.steps_text(b, dict(base_deal(), legality={"roster": chk}))[0].startswith("The Alpha Ants are at the roster max")
    pid = 5999
    b["players"][pid] = make_player(pid, 1, "LB", 60)
    b["roster"][1].append(pid)                                            # 54 already: one more is two over
    assert not ge.check_legality(base_deal(), b)["roster"]["ok"]
    # a team already over the max may still trade one-for-one
    swap = deal([pl(STAR, 2, 1), pl(b["roster"][1][-1], 1, 2)])
    assert ge.check_legality(swap, b)["roster"]["ok"]


def test_injured_reserve_does_not_count_toward_the_roster_max():
    # a full 53 plus players on IR (PC rosters: IR players stay Signed on the team)
    b = star_books()
    while len(b["roster"][1]) < ge.ROSTER_MAX_IN_SEASON:
        pid = 5000 + len(b["roster"][1])
        b["players"][pid] = make_player(pid, 1, "LB", 60)
        b["roster"][1].append(pid)
    ir = {}
    for pid in (6001, 6002, 6003):
        b["players"][pid] = make_player(pid, 1, "LB", 60)
        b["roster"][1].append(pid)
        ir[pid] = {"weeks_remaining": 8, "is_ir": True, "week": WEEK, "team_id": 1}
    b["injuries"] = ir
    b = ge.finish_books(b)
    assert ge.roster_count(b, 1) == ge.ROSTER_MAX_IN_SEASON
    chk = ge.check_legality(base_deal(), b)["roster"]                   # 53 active + 3 IR: one over, release first
    assert chk["ok"] and "AAA" in chk["release"]


def test_a_player_on_injured_reserve_moves_without_counting():
    b = star_books(injuries={STAR: {"weeks_remaining": 10, "is_ir": True, "week": WEEK, "team_id": 2}})
    before = ge.roster_count(b, 1), ge.roster_count(b, 2)
    assert ge.roster_after(base_deal(), b) == {1: before[0], 2: before[1]}


def test_qb_slot_minimum():
    b = star_books()
    qbs = [pid for pid in b["roster"][2] if b["players"][pid]["group"] == "QB"]
    d = deal([pl(q, 2, 1) for q in qbs] + [pk(1, 1, 1, 2)])
    assert not ge.check_legality(d, b)["slots"]["ok"]


def test_deadline_fails():
    b = star_books(week=ge.TRADE_DEADLINE_WEEK + 1)
    assert not ge.check_legality(base_deal(), b)["deadline"]["ok"]


def test_pick_ownership_fails():
    b = star_books()
    b["picks"][(2037, 4, 1)] = 2                           # AAA's 4th already belongs to BBB
    assert not ge.check_legality(base_deal(), b)["picks"]["ok"]


def test_double_booking_fails():
    other = {"id": 77, "status": "PROPOSED", "assets": [pl(STAR, 2, 1)]}
    b = star_books(open_=[other])
    assert not ge.check_legality(base_deal(), b)["booking"]["ok"]
    assert ge.check_legality(base_deal(), b, ignore_booking={("P", STAR)})["booking"]["ok"]


def test_fairness_fails():
    b = books(extra=[make_player(STAR, 2, "WR", 92, age=25, hit=1_000_000, end=2039)])
    lopsided = deal([pl(STAR, 2, 1), pk(7, 1, 1, 2)])
    assert not ge.check_legality(lopsided, b, ge.Valuer(b, FITS))["fairness"]["ok"]


def test_injured_player_not_tradeable():
    b = star_books(injuries={STAR: {"weeks_remaining": 1, "is_ir": False, "week": 4, "team_id": 2}})
    assert not ge.check_legality(base_deal(), b)["tradeable"]["ok"]


def test_acceptance_needs_both_sides():
    b = star_books()
    v = ge.Valuer(b, FITS)
    gift = deal([pl(STAR, 2, 1)])                          # nothing back: B says no
    ok, da, db = ge.accepts(gift, v, b)
    assert da > 0 and db < 0 and not ok
    swap = deal([pl(STAR, 2, 1), pk(1, 1, 1, 2), pk(2, 1, 1, 2)])
    ok, da, db = ge.accepts(swap, v, b)
    assert ok == (da >= ge.threshold(b["ctx"][1]) and db >= ge.threshold(b["ctx"][2]))


def test_dead_money_counts_against_the_sender():
    b = star_books()
    v = ge.Valuer(b, FITS)
    _, db0, _, _ = ge.deltas(base_deal(), v)
    b2 = star_books()
    b2["players"][STAR]["contract"]["cut_penalty"] = 10_000_000
    _, db1, _, _ = ge.deltas(base_deal(), ge.Valuer(b2, FITS))
    assert db1 < db0


def test_injury_trigger_only_for_contend_starter():
    qb = 101                                               # AAA's starting QB
    inj = {qb: {"weeks_remaining": 6, "is_ir": False, "week": 4, "team_id": 1}}
    b = books(injuries=inj)
    assert ge.injury_triggers(b)[1]["group"] == "QB"
    b = books(direction_a="RETOOL", injuries=inj)
    assert 1 not in ge.injury_triggers(b)
    short = {qb: {"weeks_remaining": 2, "is_ir": False, "week": 4, "team_id": 1}}
    assert 1 not in ge.injury_triggers(books(injuries=short))


def test_budget_mean_over_seeded_seasons():
    # 60% approval: a full season lands near the 20 target, ~60% of it in weeks 7-8, mostly rentals
    r = ge.simulate_season(0.6, 2000, detail=True)
    assert 17.5 <= r["mean"] <= 21
    late = r["by_week"][7] + r["by_week"][8]
    assert 0.5 <= late / r["mean"] <= 0.7
    assert r["by_shape"]["RENTAL"] > 4 * r["by_shape"]["SWAP"] > 0
    assert r["by_shape"]["BLOCKBUSTER"] <= ge.MAX_BLOCKBUSTERS
    assert ge.simulate_season(0.6, 300) == ge.simulate_season(0.6, 300)


def test_budget_is_seeded_and_capped():
    a = ge.weekly_budget(2036, 7, 0, 0, 0)
    assert a == ge.weekly_budget(2036, 7, 0, 0, 0)
    assert a["n"] <= ge.max_new(7)
    assert ge.weekly_budget(2036, 7, 0, 0, ge.IN_SEASON_MAX_OPEN)["n"] == 0
    assert ge.max_new(8) > ge.max_new(7) > ge.max_new(5) == ge.MAX_NEW_PER_WEEK
    assert ge.week_shape(8) == 6 and ge.week_shape(7) == 3 and ge.week_shape(3) == 1
    assert ge.scaled_target(17, 4) < 17 and ge.scaled_target(17, 1) == 17


def test_rerun_same_seed_no_duplicate():
    b = star_books()
    v = ge.Valuer(b, FITS)
    d = base_deal()
    first = ge.evaluate([d], b, v)
    b["hashes"].add(ge.deal_hash(d["a"], d["b"], d["assets"]))
    assert ge.evaluate([d], b, v) == []
    assert ge.deal_hash(1, 2, d["assets"]) == ge.deal_hash(2, 1, list(reversed(d["assets"])))
    del first


def test_expiry_after_week_and_at_deadline():
    props = [{"id": 1, "status": "PROPOSED", "expires_week": 4, "trade_window": "IN_SEASON"},
             {"id": 2, "status": "APPROVED", "expires_week": 5, "trade_window": "IN_SEASON"},
             {"id": 3, "status": "EXECUTED", "expires_week": 3, "trade_window": "IN_SEASON"}]
    assert [p for p, _ in ge.expire_stale(props, 5)] == [1]
    assert [p for p, _ in ge.expire_stale(props, ge.TRADE_DEADLINE_WEEK + 1)] == [1, 2]


def test_an_auto_approved_proposal_survives_the_week_change():
    """A talk-agreed CPU deal waits for the league office's retry (§2.5), so expire_stale leaves it alone; every
    other APPROVED proposal still expires (V2.11 U8)."""
    props = [{"id": 1, "status": "APPROVED", "expires_week": 4, "trade_window": "IN_SEASON",
              "flags": ["talks:7", "auto_approved"]},
             {"id": 2, "status": "APPROVED", "expires_week": 4, "trade_window": "IN_SEASON", "flags": []},
             {"id": 3, "status": "APPROVED", "expires_week": 4, "trade_window": "IN_SEASON"}]
    assert [p for p, _ in ge.expire_stale(props, 6)] == [2, 3]
    assert [p for p, _ in ge.expire_stale(props, ge.TRADE_DEADLINE_WEEK + 1)] == [2, 3]


def test_search_finds_legal_accepted_deal():
    b = star_books()
    v = ge.Valuer(b, FITS)
    cands = ge.evaluate(ge.buy_deals(b, v, 1), b, v)
    assert cands and all(ge.legal(c["legality"]) for c in cands)
    best = ge.choose(cands, __import__("random").Random(1))
    assert any(x.get("player_id") == STAR for x in best["assets"])
    assert ge.steps_text(b, best)[-1] == "Click Made it."


def test_user_team_with_suggestions_off_is_left_out():
    b = star_books()
    b["gm"][2]["user_controlled"], b["gm"][2]["suggestions_on"] = True, False
    v = ge.Valuer(b, FITS)
    assert ge.buy_deals(b, v, 1) == []


# ---------------------------------------------------------------- division rivals (JR, 2026-10-20) ----
def rivals(div_b="AFC East", conf_b="AFC", **kw):
    b = kw.pop("books", None) or star_books(**kw)
    b["teams"][1].update(conference="AFC", division="AFC East")
    b["teams"][2].update(conference=conf_b, division=div_b)
    return b


def test_elite_player_never_moves_inside_the_division():
    b = rivals(books=books(extra=[make_player(STAR, 2, "WR", 92, age=27, hit=5_000_000, end=2037)]))
    d = deal([pl(STAR, 2, 1), pk(1, 1, 1, 2), pk(2, 1, 1, 2)])
    chk = ge.check_legality(d, b)
    assert not chk["division"]["ok"] and "elite" in chk["division"]["detail"] and "AFC East" in chk["division"]["detail"]
    other = rivals(div_b="AFC North", books=books(extra=[make_player(STAR, 2, "WR", 92, age=27, hit=5_000_000, end=2037)]))
    assert ge.check_legality(d, other)["division"]["ok"]                    # same conference: allowed


def test_depth_player_inside_the_division_is_allowed_but_discounted():
    d = base_deal()                                                         # an 82 OVR receiver, below the elite line
    same, apart, conf = rivals(), rivals(div_b="NFC East", conf_b="NFC"), rivals(div_b="AFC West")
    assert ge.check_legality(d, same)["division"]["ok"]
    _, db_same, _, _ = ge.deltas(d, ge.Valuer(same, FITS))
    _, db_conf, _, _ = ge.deltas(d, ge.Valuer(conf, FITS))
    _, db_apart, _, lb = ge.deltas(d, ge.Valuer(apart, FITS))
    league_sent = ge.Valuer(apart, FITS).league(pl(STAR, 2, 1))
    assert round(db_apart - db_same) == round(ge.DIVISION_TAX * league_sent)    # the sender pays the rival tax
    assert round(db_apart - db_conf) == round(ge.CONFERENCE_TAX * league_sent)
    assert db_same < db_conf < db_apart


def test_proposal_1_shape_is_expired_by_the_recheck():
    """Jets get MIKE Jerome Pearman (96) from the Patriots for CB James Streets (76) and two firsts: AFC East."""
    pearman = make_player(9001, 2, "LB", 96, age=30, hit=2_400_000, end=2036)
    streets = make_player(9002, 1, "CB", 76, age=23, hit=2_700_000, end=2037)
    open_ = [{"id": 1, "status": "PROPOSED", "team_a_id": 1, "team_b_id": 2, "trade_window": "IN_SEASON",
              "expires_week": 4, "assets": [pl(9001, 2, 1), pl(9002, 1, 2), pk(1, 1, 1, 2),
                                            pk(1, 1, 1, 2, season=2038)]}]
    b = rivals(books=books(extra=[pearman, streets], open_=open_))
    b["picks"][(2038, 1, 1)] = 1

    class Cur:
        def __init__(self):
            self.sql = []

        def execute(self, q, a=None):
            self.sql.append((q, a))
    cur = Cur()
    out = ge.recheck_open(cur, b)
    assert len(out) == 1 and out[0][0] == 1
    assert out[0][1].startswith("no longer legal: division same division (AFC East): Player 9001 (96 OVR) is elite")
    assert cur.sql and cur.sql[0][1][:2] == ("EXPIRED", out[0][1])
    assert ge.recheck_open(Cur(), b, dry_run=True) == out                    # a dry run reports the same, writes nothing


def test_rookie_is_not_traded_in_his_rookie_season():
    """JR, 2026-10-20 (proposal #2 offered his rookie Amari Anderson): no in-season trade of a rookie."""
    b = star_books()
    b["players"][STAR]["yoe"] = 0
    chk = ge.check_legality(base_deal(), b)
    assert not chk["tradeable"]["ok"] and "rookie season" in chk["tradeable"]["detail"]
    assert not ge.free_player(b, STAR)
    off = star_books()
    off["players"][STAR]["yoe"] = 0
    off["window"] = "OFFSEASON"                                             # his rookie year is over
    assert ge.free_player(off, STAR)
    b["players"][STAR]["yoe"] = 1                                           # a second-year player can move
    assert ge.check_legality(base_deal(), b)["tradeable"]["ok"]


def test_team_keeps_its_x_factor_rotation_player():
    """JR, 2026-10-20 (proposal #3): the Panthers wouldn't send their Superstar X-Factor 87 DT (a third DT on a deep
    line) for an 80 Star edge and late picks. Same shape: AAA's 87 X-Factor DT behind two better DTs."""
    wagner = make_player(9101, 1, "DT", 87, age=25, hit=8_390_000, end=2039)
    wagner["dev"] = "Superstar X-Factor"
    roseman = make_player(9102, 2, "EDGE", 80, age=23, hit=3_280_000, end=2038)
    roseman["dev"] = "Star"
    b = books(extra=[wagner, roseman, make_player(9103, 1, "DT", 90), make_player(9104, 1, "DT", 89)])
    d = deal([pl(9102, 2, 1), pk(2, 2, 2, 1), pk(6, 2, 2, 1), pl(9101, 1, 2)])
    v = ge.Valuer(b, FITS)
    assert 9101 in b["ctx"][1]["rotation_ids"] or tv.utility(wagner, b["ctx"][1]) == tv.ROTATION_U
    ok, da, db = ge.accepts(d, v, b)
    assert not ok and da < ge.threshold(b["ctx"][1])


def test_recheck_expires_a_deal_a_gm_no_longer_accepts():
    wagner = make_player(9101, 1, "DT", 87, age=25, hit=8_390_000, end=2039)
    wagner["dev"] = "Superstar X-Factor"
    roseman = make_player(9102, 2, "EDGE", 80, age=23, hit=3_280_000, end=2038)
    assets = [pl(9101, 1, 2), pk(2, 2, 2, 1)]                 # a ladder-priced rental: R2 for a young 87
    open_ = [{"id": 3, "status": "PROPOSED", "origin": "ENGINE", "team_a_id": 1, "team_b_id": 2,
              "trade_window": "IN_SEASON", "expires_week": 4, "assets": assets}]
    b = books(extra=[wagner, roseman, make_player(9103, 1, "DT", 90), make_player(9104, 1, "DT", 89)], open_=open_)

    class Cur:
        def execute(self, q, a=None):
            pass
    out = ge.recheck_open(Cur(), b, valuer=ge.Valuer(b, FITS))
    assert out and out[0][0] == 3 and out[0][1].startswith("no longer accepted: AAA surplus")
    assert ge.recheck_open(Cur(), b) == []                          # without a valuer: legality only
    user = [dict(open_[0], origin="USER")]
    b2 = books(extra=[wagner, roseman, make_player(9103, 1, "DT", 90), make_player(9104, 1, "DT", 89)], open_=user)
    assert ge.recheck_open(Cur(), b2, valuer=ge.Valuer(b2, FITS)) == []   # JR's own trades aren't second-guessed


# ------------------------------------------------------- team control (D8, §2.10) ----
def test_team_control_comes_from_the_registry_when_it_has_an_entry(monkeypatch):
    b = books()
    monkeypatch.setattr("engines.front_office.controlled_team_ids", lambda cur: {2})
    ge.apply_team_control(None, b)
    assert b["gm"][1]["user_controlled"] is False and b["gm"][2]["user_controlled"] is True
    assert all(gm["suggestions_on"] for gm in b["gm"].values())     # still from gm_team_settings
    b = books()
    b["gm"][1]["user_controlled"] = b["gm"][2]["user_controlled"] = True    # stale gm_team_settings rows
    monkeypatch.setattr("engines.front_office.controlled_team_ids", lambda cur: set())   # the all-CPU era
    ge.apply_team_control(None, b)
    assert b["gm"][1]["user_controlled"] is False and b["gm"][2]["user_controlled"] is False


def test_team_control_falls_back_to_gm_team_settings_without_an_entry(monkeypatch):
    b = books()
    b["gm"][2]["user_controlled"] = True
    monkeypatch.setattr("engines.front_office.controlled_team_ids", lambda cur: None)
    ge.apply_team_control(None, b)
    assert b["gm"][1]["user_controlled"] is False and b["gm"][2]["user_controlled"] is True


def test_read_books_applies_the_registry_overlay(monkeypatch):
    seen = []

    def fake_apply(cur, books):
        seen.extend(sorted(books["gm"]))
        return books

    class Cur:
        def __init__(self):
            self.last = ""

        def execute(self, q, a=None):
            self.last = " ".join(q.split())

        def fetchone(self):
            return (0, 0, None) if "count(*) FILTER" in self.last else (0,)

        def fetchall(self):
            if "FROM gm_profiles" in self.last:                     # (team_id, gm id, name, ... control, suggestions)
                return [(1, 11, "Ann A", 0.6, 0.5, 1.0, 0.0, {}, "", False, True),
                        (2, 22, "Bob B", 0.6, 0.5, 1.0, 0.0, {}, "", True, True)]
            return []
    monkeypatch.setattr(ge, "apply_team_control", fake_apply)
    try:
        ge.read_books(Cur(), SEASON, 5)
    except Exception:
        pass                                                        # the fake has no players; the overlay call is what matters
    assert seen == [1, 2]                                           # both GM rows reached the overlay


# ------------------------------------------------------- CPU-to-CPU talks (§2.6) ----
def _run_week(monkeypatch, b, dry_run=False, cand=None):
    """run_week with every database boundary replaced: the search and the routing stay real."""
    calls = {"store": [], "talks": []}
    import engines.gm_direction as gd
    import engines.gm_talks as gt
    monkeypatch.setattr(ge, "read_proposals", lambda *a, **k: [])
    monkeypatch.setattr(ge, "expire_stale", lambda *a, **k: [])
    monkeypatch.setattr(ge, "read_books", lambda *a, **k: b)
    monkeypatch.setattr(ge, "recheck_open", lambda *a, **k: [])
    monkeypatch.setattr(ge, "injury_triggers", lambda books: {})
    monkeypatch.setattr(ge, "initiator_order", lambda books, rng: ([1], []))
    monkeypatch.setattr(ge, "store", lambda *a, **k: calls["store"].append(a) or 1)
    monkeypatch.setattr(ge.tv, "fits_from_db", lambda cur, season: FITS)
    monkeypatch.setattr(gd, "read_league", lambda *a, **k: {})
    monkeypatch.setattr(gd, "compute", lambda rows, week: {})
    monkeypatch.setattr(gd, "write", lambda *a, **k: None)
    if cand is not None:                                 # a scripted pick, for the flags a CPU deal carries
        monkeypatch.setattr(ge, "evaluate", lambda deals, books, valuer: [cand])
        monkeypatch.setattr(ge, "choose_shaped", lambda cands, rng: cands[0])
        monkeypatch.setattr(ge, "choose", lambda cands, rng: cands[0])

    def fake_talks(cur, books, valuer, deals, rng, llm, log=None):
        calls["talks"].append(list(deals))
        return {"talks": [{"id": 7, "status": "AGREED"}], "agreed": [7], "stalled": [], "teams": {1, 2}}
    monkeypatch.setattr(gt, "run_talks", fake_talks)
    rep = ge.run_week(object(), SEASON, WEEK, dry_run=dry_run, log=lambda m: None)
    return rep, calls


def test_cpu_to_cpu_picks_go_to_talks_instead_of_being_stored(monkeypatch):
    rep, calls = _run_week(monkeypatch, star_books())
    assert calls["store"] == []                                     # nothing PROPOSED for JR
    assert len(calls["talks"]) == 1 and len(calls["talks"][0]) == 1
    picked = calls["talks"][0][0]
    assert (picked["a"], picked["b"]) == (1, 2) and any(x.get("player_id") == STAR for x in picked["assets"])
    assert rep["proposals"] == [] and rep["talks"]["agreed"] == [7] and rep["talks"]["stalled"] == []


def test_a_cpu_pick_carries_the_stored_path_flags_onto_its_talk(monkeypatch):
    """A collected CPU pick keeps the stored path's shape / trade-request flags (advisory: and the LLM flags stay
    off), and a CPU blockbuster counts against the season cap across the run (V2.11 U8)."""
    b = star_books()
    cand = dict(deal([pl(STAR, 2, 1), pk(4, 1, 1, 2)]), hash="cpu-bb", da=1e6, db=-1e6, joint=-1e6,
                legality={}, shape="BLOCKBUSTER", request_id=55)
    rep, calls = _run_week(monkeypatch, b, cand=cand)
    picked = calls["talks"][0][0]
    assert "shape:BLOCKBUSTER" in picked["flags"] and "trade_request:55" in picked["flags"]
    assert not any(f.startswith(("advisory:", "llm_")) for f in picked["flags"])
    assert b["blockbusters"] == 1                                   # the cap holds within the run too
    assert rep["proposals"] == []


def test_a_cpu_pick_in_the_offseason_carries_its_stage():
    b = star_books()
    pick = dict(deal([pl(STAR, 2, 1), pk(4, 1, 1, 2)]), shape="RENTAL")
    flags, _caps = ge.proposal_flags(b, pick, "OFFSEASON", 3)
    assert "stage:3" in flags and "shape:RENTAL" in flags
    assert not any(f.startswith(("advisory:", "stage:")) for f in ge.proposal_flags(b, pick, "IN_SEASON", None)[0])


def test_a_deal_with_a_user_team_is_still_an_advisory_proposal(monkeypatch):
    b = star_books()
    b["gm"][1]["user_controlled"] = True
    rep, calls = _run_week(monkeypatch, b)
    assert calls["talks"] == [[]]                                   # no collected CPU deal; carried talks still run
    assert len(calls["store"]) == 1
    args = calls["store"][0]                                        # store(cur, books, pick, valuer, rationale, flags)
    pick = args[2]
    assert (pick["a"], pick["b"]) == (1, 2) and "advisory:AAA" in args[5]
    assert rep["proposals"] and rep["proposals"][0]["flags"] == args[5]


def test_cpu_deal_classification():
    b = books()
    assert ge.cpu_deal(b, deal([pl(STAR, 2, 1)]))
    b["gm"][2]["user_controlled"] = True
    assert not ge.cpu_deal(b, deal([pl(STAR, 2, 1)]))       # the sender is JR's team
    assert not ge.cpu_deal(b, deal([pl(STAR, 1, 2)]))       # the receiver is JR's team
    b["gm"][2]["user_controlled"] = False
    b["gm"][1]["user_controlled"] = True
    assert not ge.cpu_deal(b, deal([pl(STAR, 1, 2)]))


def test_a_dry_run_stores_no_proposal(monkeypatch):
    b = star_books()
    b["gm"][1]["user_controlled"] = True
    rep, calls = _run_week(monkeypatch, b, dry_run=True)
    assert calls["store"] == [] and rep["proposals"] and rep["proposals"][0]["id"] is None


def test_draft_day_and_round_entry_points_do_not_talk():
    import inspect
    for fn in (ge.run_draft_day, ge.run_draft_round):
        assert "talk_llm" not in inspect.signature(fn).parameters
        assert "run_talks" not in inspect.getsource(fn)


def test_decided_counts_stalled_but_rejected_players_do_not():
    class Cur:
        def __init__(self):
            self.sql = []

        def execute(self, q, a=None):
            self.sql.append(" ".join(q.split()))

        def fetchone(self):
            return (0, 0, None) if "count(*) FILTER" in self.sql[-1] else (0,)

        def fetchall(self):
            return []
    cur = Cur()
    try:
        ge.read_books(cur, SEASON, 5)
    except Exception:
        pass
    counts = [q for q in cur.sql if "count(*) FILTER (WHERE status = 'EXECUTED'" in q]
    assert counts and "IN ('EXECUTED', 'REJECTED', 'STALLED')" in counts[0]
    rejected = [q for q in cur.sql if "p.status = 'REJECTED'" in q]
    assert rejected and all("STALLED" not in q for q in rejected)


def test_simulate_season_with_talk_stall_stays_near_target():
    old_mean = 18.56                                                # the pre-talk simulation, seed 0, 2000 seasons
    r = ge.simulate_season(0.6, 2000, detail=True, talk_stall=0.25)
    assert 18 <= r["mean"] <= 22
    assert abs(r["mean"] - old_mean) <= 2
    assert ge.simulate_season(0.6, 300, talk_stall=0.25) == ge.simulate_season(0.6, 300, talk_stall=0.25)
    assert ge.TALK_STALL == 0.25


def test_player_jr_rejected_this_week_is_not_offered_again():
    b = star_books()
    b["rejected_players"] = {STAR}
    v = ge.Valuer(b, FITS)
    assert not any(any(x.get("player_id") == STAR for x in c["assets"]) for c in ge.evaluate(ge.buy_deals(b, v, 1), b, v))
    open_ = [{"id": 24, "status": "PROPOSED", "origin": "ENGINE", "team_a_id": 1, "team_b_id": 2,
              "trade_window": "IN_SEASON", "expires_week": 4, "assets": base_deal()["assets"]}]
    b = star_books(open_=open_)
    b["rejected_players"] = {STAR}

    class Cur:
        def execute(self, q, a=None):
            pass
    out = ge.recheck_open(Cur(), b)
    assert out == [(24, f"rejected this week: Player {STAR} was in a proposal JR rejected")]
    b["open"] = [dict(open_[0], origin="USER")]                 # JR's own deals aren't second-guessed
    assert ge.recheck_open(Cur(), b) == []
