"""GM trade proposal engine (docs/done/team_management/03_GM_AGENTS_PLAN.md Phase 4). Synthetic books: no database."""
import math
from decimal import Decimal

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
    # The planner's own target was 20 executed; P3-08's measurement (TALK_STALL = 0.64) puts a full season at
    # ~13 executors, inside the real 5-21 band, with ~58% of it in weeks 7-8 and mostly rentals. A season no
    # longer lands near 20 because two thirds of the talks break down; the *observable* is what the band judges.
    r = ge.simulate_season(0.6, 2000, detail=True)
    assert 10 <= r["mean"] <= 16 and 5 <= r["mean"] <= 21
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
def _run_week(monkeypatch, b, dry_run=False, cand=None, resumed_teams=()):
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
        monkeypatch.setattr(ge, "evaluate_initiator", lambda deals, books, valuer: [cand])
        monkeypatch.setattr(ge, "choose_shaped", lambda cands, rng: cands[0])
        monkeypatch.setattr(ge, "choose_shaped_opening", lambda cands, persona, rng, urgent=False: cands[0])
        monkeypatch.setattr(ge, "choose", lambda cands, rng: cands[0])
        monkeypatch.setattr(ge, "choose_opening", lambda cands, persona, rng, urgent=False: cands[0])

    def fake_resume(cur, books, valuer, rng, llm, log=None):
        calls["resumed"] = calls.get("resumed", 0) + 1
        return {"talks": [], "agreed": [], "stalled": [], "teams": set(resumed_teams)}

    def fake_talks(cur, books, valuer, deals, rng, llm, log=None):
        calls["talks"].append(list(deals))
        return {"talks": [{"id": 7, "status": "AGREED"}], "agreed": [7], "stalled": [], "teams": {1, 2}}
    monkeypatch.setattr(gt, "resume_talks", fake_resume)
    monkeypatch.setattr(gt, "run_talks", fake_talks)
    rep = ge.run_week(object(), SEASON, WEEK, dry_run=dry_run, log=lambda m: None)
    return rep, calls


def test_a_team_carrying_a_talk_cannot_open_a_second_deal(monkeypatch):
    """P1-17: the carried talks are resumed before the search, so their teams are already in `used` and the search
    offers them no new deal this week."""
    rep, calls = _run_week(monkeypatch, star_books(), resumed_teams={1, 2})
    assert calls["resumed"] == 1
    assert calls["talks"] == [[]] and calls["store"] == []
    assert rep["proposals"] == []


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
    """The planner still reaches the season target at the old 25% breakdown assumption (the pre-P3-08 model);
    P3-08 replaced that *default* with the measured 64% share, which plans ~13 a season (inside the 5-21 band)."""
    old_mean = 18.56                                                # the pre-talk simulation, seed 0, 2000 seasons
    r = ge.simulate_season(0.6, 2000, detail=True, talk_stall=0.25)
    assert 18 <= r["mean"] <= 22
    assert abs(r["mean"] - old_mean) <= 2
    assert ge.simulate_season(0.6, 300, talk_stall=0.25) == ge.simulate_season(0.6, 300, talk_stall=0.25)
    assert ge.TALK_STALL == _trade_flow_2036()["breakdown_share"]


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


# --------------------------------------- rivalry x contention tax and unilateral search ----
def tax_books():
    """A bare book for the tax matrix: four teams across two conferences, three divisions."""
    return {"season": SEASON, "window": "IN_SEASON", "ctx": {}, "wl": {},
            "teams": {1: {"abbr": "AAA", "conference": "AFC", "division": "AFC East"},
                      2: {"abbr": "BBB", "conference": "AFC", "division": "AFC East"},
                      3: {"abbr": "CCC", "conference": "AFC", "division": "AFC North"},
                      4: {"abbr": "DDD", "conference": "NFC", "division": "NFC East"}},
            "ts": {}}


def status(b, tid, direction, wins=None, losses=None, proj=None):
    b["ts"][tid] = {"direction": direction, "proj_wins": proj}
    if wins is not None:
        b["wl"][tid] = (wins, losses)


def test_rivalry_and_contention_tax_matrix():
    b = tax_books()
    status(b, 1, "CONTEND")
    status(b, 4, "CONTEND")
    assert ge.contender_status(b, 1) == "CONTEND"
    # inter-conference: 0% unless both CONTEND (5%)
    assert ge.rival_tax(b, 1, 4) == ge.INTERCONF_CONTEND_TAX == 0.05
    assert ge.rival_tax(b, 4, 1) == 0.05
    status(b, 4, "REBUILD")
    assert ge.contender_status(b, 4) == "OUT"
    assert ge.rival_tax(b, 1, 4) == 0.0 and ge.rival_tax(b, 4, 1) == 0.0
    # same conference, different divisions
    status(b, 3, "REBUILD")
    assert ge.rival_tax(b, 1, 3) == ge.CONF_OUT_TAX == 0.05          # receiver OUT: not a playoff threat
    assert ge.rival_tax(b, 3, 1) == ge.CONFERENCE_TAX == 0.10        # OUT seller to a contender
    status(b, 3, "RETOOL", 2, 2)                                     # .500 RETOOL = HUNT
    assert ge.contender_status(b, 3) == "HUNT"
    assert ge.rival_tax(b, 3, 1) == ge.CONF_HUNT_TAX == 0.25         # both in the race, one HUNT
    assert ge.rival_tax(b, 1, 3) == 0.25
    status(b, 3, "CONTEND")
    assert ge.rival_tax(b, 1, 3) == ge.CONF_CONTEND_TAX == 0.40
    # same division
    status(b, 2, "REBUILD")
    assert ge.rival_tax(b, 1, 2) == ge.DIVISION_TAX == 0.50          # one OUT
    assert ge.rival_tax(b, 2, 1) == 0.50
    status(b, 2, "RETOOL", 3, 3)                                     # HUNT
    assert ge.contender_status(b, 2) == "HUNT"
    assert ge.rival_tax(b, 1, 2) == ge.DIV_HUNT_TAX == 0.65
    assert ge.rival_tax(b, 2, 1) == 0.65
    status(b, 2, "CONTEND")
    assert ge.rival_tax(b, 1, 2) == ge.DIV_CONTEND_TAX == 0.75
    # a losing RETOOL is OUT; the offseason HUNT test uses projected wins
    status(b, 3, "RETOOL", 1, 3)
    assert ge.contender_status(b, 3) == "OUT"
    b["window"] = "OFFSEASON"
    status(b, 3, "RETOOL", proj=9)
    assert ge.contender_status(b, 3) == "HUNT"
    status(b, 3, "RETOOL", proj=7)
    assert ge.contender_status(b, 3) == "OUT"


def test_division_contenders_block_starter_trades_in_season():
    starter = make_player(9300, 2, "WR", 85, age=27, hit=3_000_000, end=2037)
    d = deal([pl(9300, 2, 1), pk(3, 1, 1, 2)])
    both = rivals(books=books(direction_a="CONTEND", direction_b="CONTEND", extra=[starter]))
    chk = ge.division_check(d, both)
    assert not chk["ok"] and "starter (OVR 84+)" in chk["detail"] and "in the race" in chk["detail"]
    # a HUNT rival (winning RETOOL) is in the race too
    hunt = rivals(books=books(direction_a="CONTEND", direction_b="RETOOL", extra=[starter]))
    hunt["wl"] = {1: (4, 0), 2: (3, 2)}
    assert hunt["ctx"][2]["direction"] == "RETOOL" and not ge.division_check(d, hunt)["ok"]
    # an OUT seller may still move him, at the 50% division tax
    seller_out = rivals(books=books(direction_a="CONTEND", direction_b="REBUILD", extra=[starter]))
    ok = ge.division_check(d, seller_out)
    assert ok["ok"] and "50%" in ok["detail"]
    # out of season the hard 84 line is not applied (the 88+ elite block still is)
    both["window"] = "OFFSEASON"
    assert ge.division_check(d, both)["ok"]


def test_unilateral_search_does_not_require_receiver_acceptance():
    """Team 2 privately overvalues its own X-Factor receiver (a 3x position bias): Team 1 still makes a legal
    ladder offer from its own valuation and public_seller_value, while the old bilateral pre-screen drops them."""
    star = make_player(STAR, 2, "WR", 82, age=30, hit=5_000_000, end=2036)
    star["dev"] = "Superstar X-Factor"
    b = books(direction_b="REBUILD", extra=[star])
    b["gm"][2]["position_bias"] = {"WR": 3.0}
    b = ge.finish_books(b)
    v = ge.Valuer(b, FITS)
    deals = ge.shaped_buy_deals(b, v, 1, ["WR"])
    cands = ge.evaluate_initiator(deals, b, v)
    assert cands and all(ge.legal(c["legality"]) for c in cands)
    assert any(x.get("player_id") == STAR for c in cands for x in c["assets"])
    assert not any(any(x.get("player_id") == STAR for x in c["assets"]) for c in ge.evaluate(deals, b, v))


def _opening_cands():
    return [
        dict(deal([pl(STAR, 2, 1), pk(4, 1, 1, 2)]), hash="firm1", da=400_000, db=200_000, la=5_000_000,
             lb=4_900_000, joint=200_000, legality={}, shape="RENTAL"),
        dict(deal([pl(STAR, 2, 1), pk(5, 1, 1, 2)]), hash="anchor1", da=1_500_000, db=-500_000, la=5_000_000,
             lb=4_000_000, joint=-500_000, legality={}, shape="RENTAL"),
        dict(deal([pl(STAR, 2, 1), pk(6, 1, 1, 2)]), hash="anchor2", da=2_400_000, db=-900_000, la=5_000_000,
             lb=3_500_000, joint=-900_000, legality={}, shape="RENTAL"),
        dict(deal([pl(STAR, 2, 1), pk(7, 1, 1, 2)]), hash="firm2", da=300_000, db=100_000, la=5_000_000,
             lb=4_950_000, joint=100_000, legality={}, shape="RENTAL"),
    ]


def test_personality_drives_opening_posture_and_package():
    import random
    aggressive = {"aggressiveness": 0.95, "sliders": {"confidence": 9, "drive": 8, "warmth": 2},
                  "traits": ["CAP_MIND"]}
    measured = {"aggressiveness": 0.10, "sliders": {"confidence": 3, "drive": 3, "warmth": 9},
                "traits": ["MEASURED"]}
    assert ge.opening_posture(aggressive) >= 0.85 and ge.opening_posture(measured) <= 0.10
    assert ge.opening_posture(aggressive, urgent=True) < ge.opening_posture(aggressive)
    cands = _opening_cands()
    picks = lambda persona: [ge.choose_opening(cands, persona, random.Random(s)) for s in range(300)]
    agg, calm = picks(aggressive), picks(measured)
    n_anchor = lambda ps: sum(p["opening_posture"] == "ANCHOR" for p in ps)
    assert n_anchor(agg) > n_anchor(calm) + 50
    mean_da = lambda ps: sum(p["da"] for p in ps) / len(ps)
    assert mean_da(agg) > mean_da(calm)                       # the shark opens cheaper, for more surplus
    assert all(1 <= len(p["opening_options"]) <= ge.OPTION_K and p["opening_options"][0]["posture"] == "FIRM"
               for p in agg)


def test_recheck_keeps_unilateral_proposed_offer_when_initiator_still_accepts():
    star = make_player(STAR, 2, "WR", 82, age=27, hit=2_000_000, end=2037)
    assets = [pl(STAR, 2, 1), pk(4, 1, 1, 2)]
    open_ = [{"id": 41, "status": "PROPOSED", "origin": "ENGINE", "team_a_id": 1, "team_b_id": 2,
              "trade_window": "IN_SEASON", "expires_week": 4, "assets": assets}]

    class Cur:
        def execute(self, q, a=None):
            pass

    b = books(direction_a="CONTEND", direction_b="CONTEND", extra=[star], open_=open_)
    v = ge.Valuer(b, FITS)
    ok, da, db, _, _ = ge.initiator_accepts(deal(assets), v, b)
    assert ok and da >= ge.threshold(b["ctx"][1], False, b["window"])
    assert db < ge.threshold(b["ctx"][2], False, b["window"]) and not ge.accepts(deal(assets), v, b)[0]
    assert ge.recheck_open(Cur(), b, valuer=v) == []            # the initiator stands behind it: kept
    # a deal the initiating GM himself would now refuse is still expired
    stacked = [make_player(9500 + k, 1, "WR", 90, hit=2_000_000, end=2037) for k in range(3)]
    heavy = make_player(STAR, 2, "WR", 82, age=27, hit=6_000_000, end=2037)
    b2 = books(direction_a="CONTEND", direction_b="CONTEND", extra=[heavy] + stacked, open_=open_)
    v2 = ge.Valuer(b2, FITS)
    assert not ge.initiator_accepts(deal(assets), v2, b2)[0]
    out = ge.recheck_open(Cur(), b2, valuer=v2)
    assert out and out[0][0] == 41 and out[0][1].startswith("no longer accepted: AAA surplus")


# --------------------------------------------- fact sheet stats loaders (§2.3) ----
class _TableCur:
    """.execute records the query; .fetchall returns the rows registered for a table substring."""

    def __init__(self, rows):
        self.rows, self.last, self.args = rows, "", None

    def execute(self, q, a=None):
        self.last, self.args = " ".join(q.split()), a

    def fetchall(self):
        for key, val in self.rows.items():
            if key in self.last:
                return val
        return []


def test_load_season_stats_merges_the_season_tables_keyed_by_player():
    cur = _TableCur({
        "FROM season_stats ": [(10, 0, 0, 0, 0, 0, 0, 0, 0, 72, 986, 7)],
        "FROM season_stat_defense": [(12, 88, Decimal("9.5"), 2)],
        "FROM season_stat_blocking": [(14, 3)],
        "FROM season_stat_kicking": [(15, 20, 22, 51, 40, 41)],
        "FROM season_stat_punting": [(16, 60, Decimal("44.2"), 20)],
        "FROM season_stat_general": [(10, 15), (12, 17), (17, 9)],
    })
    stats = ge.load_season_stats(cur, 2036)
    assert stats[10]["receptions"] == 72 and stats[10]["rec_yards"] == 986 and stats[10]["games"] == 15
    assert stats[12]["tackles"] == 88 and stats[12]["sacks"] == Decimal("9.5") and stats[12]["games"] == 17
    assert stats[14]["sacks_allowed"] == 3
    assert stats[15]["fgm"] == 20 and stats[15]["fga"] == 22 and stats[15]["fg_long"] == 51
    assert stats[16]["punts"] == 60 and stats[16]["average"] == Decimal("44.2")
    assert 17 not in stats                                       # only a games-played row: no stat row at all


class _GamesCur:
    def __init__(self, games):
        self.games, self.last, self.args = games, "", None

    def execute(self, q, a=None):
        self.last, self.args = " ".join(q.split()), a

    def fetchall(self):
        season, week = self.args
        return [g for g in self.games if g[0] < week]


def test_load_team_form_reads_only_completed_games_before_the_week():
    games = [(1, 1, 2, 24, 17), (2, 2, 1, 20, 10), (3, 1, 2, 30, 7), (4, 1, 2, 27, 20),
             (5, 1, 2, 99, 0)]                                  # week 5 is at the query week: never read
    cur = _GamesCur(games)
    form = ge.load_team_form(cur, 2036, 5)
    assert "week < %s" in cur.last and cur.args == (2036, 5)
    assert form[1]["last3"] == ["W 27-20", "W 30-7", "L 10-20"] and form[1]["streak"] == "W2"
    assert form[2]["last3"] == ["L 20-27", "L 7-30", "W 20-10"] and form[2]["streak"] == "L2"


class _RecentCur:
    def __init__(self, meta, abbrs, off, deff):
        self.meta, self.abbrs, self.off, self.deff = meta, abbrs, off, deff
        self.last, self.args = "", None

    def execute(self, q, a=None):
        self.last, self.args = " ".join(q.split()), a

    def fetchall(self):
        q = self.last
        if "FROM player_seasons" in q:
            return [m for m in self.meta if m[0] in set(self.args[1])]
        if "FROM teams" in q:
            return self.abbrs
        if "FROM player_stats" in q:
            wanted, week = set(self.args[0]), self.args[2]
            return [g for g in self.off if g[0] in wanted and g[11] < week]
        if "FROM game_stat_defense" in q:
            wanted, week = set(self.args[0]), self.args[2]
            return [g for g in self.deff if g[0] in wanted and g[4] < week]
        return []


def test_recent_form_returns_at_most_three_games_before_the_week():
    meta = [(10, "WR", 1), (12, "MIKE", 1)]
    abbrs = [(1, "AAA"), (2, "BBB")]
    off = [(10, 0, 0, 0, 0, 0, 0, 0, 6, 88, 1, 2, 1, 2, 100),      # week 2 home: 6-88-1
           (10, 0, 0, 0, 0, 0, 0, 0, 4, 60, 0, 3, 2, 1, 101),      # week 3 away: 4-60-0
           (10, 0, 0, 0, 0, 0, 0, 0, 9, 120, 2, 4, 1, 2, 102),     # week 4 home: 9-120-2
           (10, 0, 0, 0, 0, 0, 0, 0, 50, 500, 5, 5, 1, 2, 103)]    # week 5: at the query week
    deff = [(12, 8, Decimal("1.5"), 1, 2, 1, 2, 200), (12, 6, 0, 0, 4, 1, 2, 201),
            (12, 99, 0, 0, 5, 1, 2, 202)]
    out = ge.recent_form(_RecentCur(meta, abbrs, off, deff), [10, 12], 2036, 5)
    assert out[10] == ["9-120-2 vs BBB", "4-60-0 @ BBB", "6-88-1 vs BBB"]
    assert out[12] == ["6 tkl, 0 sacks, 0 INT vs BBB", "8 tkl, 1.5 sacks, 1 INT vs BBB"]
    # games at or after the week never make a line
    assert not any("500" in line or "99 tkl" in line for lines in out.values() for line in lines)


def test_read_books_carries_season_stats_form_and_each_gms_card(monkeypatch):
    class Cur:
        def __init__(self):
            self.last, self.args = "", None

        def execute(self, q, a=None):
            self.last, self.args = " ".join(q.split()), a

        def fetchone(self):
            return (0, 0, None) if "count(*) FILTER" in self.last else (0,)

        def fetchall(self):
            q = self.last
            if "FROM teams WHERE abbreviation" in q:
                return [(1, "CAR", "Carolina", "Panthers", "NFC", "South"),
                        (2, "NO", "New Orleans", "Saints", "NFC", "South")]
            if "FROM gm_profiles g LEFT JOIN" in q:
                return [(1, 11, "Ann A", 0.6, 0.5, 1.0, 0.0, {}, "", False, True),
                        (2, 22, "Bob B", 0.6, 0.5, 1.0, 0.0, {}, "", False, True)]
            if "SELECT team_id, voice_card, voice_card_source FROM gm_profiles" in q:
                return [(1, {"register": "blunt"}, "AUTHORED"), (2, None, None)]
            if "FROM team_seasons" in q:
                return [(1, 3, 0), (2, 1, 2)]
            return []

    monkeypatch.setattr("newsroom.personas.persona_tables_exist", lambda cur: False)
    monkeypatch.setattr(ge.tv, "read_players", lambda cur, season: {})
    monkeypatch.setattr("engines.gm_direction.read_injuries", lambda *a, **k: {})
    monkeypatch.setattr(ge, "read_cap", lambda *a, **k: {})
    monkeypatch.setattr(ge, "read_proposals", lambda *a, **k: [])
    monkeypatch.setattr(ge, "apply_team_control", lambda cur, books: books)
    monkeypatch.setattr(ge, "load_season_stats", lambda cur, season: {10: {"receptions": 72}})
    monkeypatch.setattr(ge, "load_team_form",
                        lambda cur, season, week: {1: {"last3": ["W 24-17"], "streak": "W1"}})
    b = ge.read_books(Cur(), SEASON, 5)
    assert b["season_stats"] == {10: {"receptions": 72}}
    assert b["form"] == {1: {"last3": ["W 24-17"], "streak": "W1"}}
    assert b["gm"][1]["voice_card"] == {"register": "blunt"}
    assert b["gm"][1]["voice_card_source"] == "AUTHORED"
    assert b["gm"][2]["voice_card"] is None and b["gm"][2]["voice_card_source"] is None


# ------------------------------------------------- P3-08 trade-flow measurement ----
def _trade_flow_2036():
    import json
    from pathlib import Path
    path = Path(__file__).parent / "fixtures" / "trade_flow_2036_weeks4_8.json"
    return json.loads(path.read_text())


def test_talk_stall_is_the_measured_2036_breakdown_share():
    """TALK_STALL is a measurement, not an assumption (P3-08): the 2036 weeks 4-8 replay on a scratch clone of
    madden_2036 produced 16 STALLED of 25 CPU talks. The constant must follow that receipt, or --simulate-season
    plans 16 executed trades for a window the live engine fills with 9."""
    m = _trade_flow_2036()
    assert m["talks"] == 25 and m["outcomes"]["AGREED"] == 9 and m["outcomes"]["STALLED"] == 16
    assert abs(ge.TALK_STALL - m["breakdown_share"]) <= 0.01


def test_pacing_model_lands_in_the_real_band_at_the_measured_breakdown():
    """With the measured breakdown the planner agrees with the replay (9 executed, 10.1 predicted) and a full
    season sits inside the real 5-21 band; the old 0.25 assumption over-predicted the window by ~60%."""
    m = _trade_flow_2036()
    window = ge.simulate_season(seasons=500, first_week=4, talk_stall=m["breakdown_share"])
    assert abs(window - m["executed"]) <= 3                     # the replay measured 9 in this window
    season = ge.simulate_season(seasons=500, first_week=1, talk_stall=m["breakdown_share"])
    assert 5 <= season <= 21                                     # TUNING_ACCEPTANCE_THRESHOLDS A4/A5 band
    old = ge.simulate_season(seasons=500, first_week=4, talk_stall=0.25)
    assert old > m["executed"] + 3                               # the assumption this replaced


def test_the_default_pacing_model_uses_the_measured_value():
    """`--simulate-season` without --talk-stall must be the measured config, not a stale default."""
    from contextlib import redirect_stdout
    import io
    buf = io.StringIO()
    with redirect_stdout(buf):
        ge.main(["--simulate-season", "--seasons", "500", "--first-week", "4"])
    line = buf.getvalue().splitlines()[0]
    assert "talk stall 0.64" in line
