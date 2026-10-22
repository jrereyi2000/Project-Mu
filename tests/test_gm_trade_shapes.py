"""In-season trade shapes (JR, 2026-10-20, from Spotrac's 2025 in-season trades). Synthetic books: no database."""
from engines import gm_trade_engine as ge
from tests.test_gm_trade_engine import FITS, books, deal, make_player, pk, pl

SEASON = 2036


def p(ovr, age=28, end=2037, pid=900, team=2, group="WR"):
    return make_player(pid, team, group, ovr, age=age, end=end)


def test_price_ladder_by_ovr_age_and_contract():
    assert ge.ladder(p(72), SEASON) == (6, 7)
    assert ge.ladder(p(77), SEASON) == (5, 6)
    assert ge.ladder(p(82), SEASON) == (4, 5)
    assert ge.ladder(p(86), SEASON) == (3, 3)
    assert ge.ladder(p(86, age=25, end=2038), SEASON) == (2, 2)       # young with 2+ years left: R2
    assert ge.ladder(p(86, age=31), SEASON) == (3, 4)                 # older: a round cheaper
    assert ge.ladder(p(82, end=2036), SEASON) == (4, 6)               # expiring: a round cheaper
    assert ge.ladder(p(82, age=24, end=2039), SEASON) == (3, 5)       # young and cheap: a round dearer
    assert ge.ladder(p(88), SEASON) is None                           # 88+: blockbuster only


def shaped(b, assets, a=1, bt=2):
    return ge.classify_shape(deal(assets, a, bt), b)


def test_rental_shapes():
    b = books(extra=[p(82)])
    assert shaped(b, [pl(900, 2, 1), pk(4, 1, 1, 2)])[:2] == ("RENTAL", True)
    assert shaped(b, [pl(900, 2, 1), pk(4, 1, 1, 2), pk(6, 1, 1, 2)])[:2] == ("RENTAL", True)     # a 4th + 6th
    assert shaped(b, [pl(900, 2, 1), pk(5, 1, 1, 2), pk(7, 2, 2, 1)])[:2] == ("RENTAL", True)     # player + 7th for a 5th
    assert not shaped(b, [pl(900, 2, 1), pk(6, 1, 1, 2)])[1]                                      # under the ladder
    assert not shaped(b, [pl(900, 2, 1), pk(3, 1, 1, 2)])[1]                                      # over the ladder
    assert not shaped(b, [pl(900, 2, 1), pk(4, 1, 1, 2), pk(5, 1, 1, 2), pk(6, 1, 1, 2)])[1]      # 3 picks
    assert not shaped(b, [pl(900, 2, 1), pk(4, 1, 1, 2), pk(4, 2, 2, 1)])[1]                      # the pick back isn't later


def test_no_first_or_second_outside_a_blockbuster():
    b = books(extra=[p(84)])
    ok, why = shaped(b, [pl(900, 2, 1), pk(1, 1, 1, 2)])[1:]
    assert not ok
    ok, why = shaped(b, [pl(900, 2, 1), pk(4, 1, 1, 2), pk(2, 1, 1, 2)])[1:]
    assert not ok


def test_one_player_a_side_and_like_for_like_swaps():
    b = books(extra=[p(80, pid=900, team=2, group="CB"), p(79, pid=901, team=1, group="S"),
                     p(70, pid=902, team=1, group="CB"), p(80, pid=903, team=1, group="WR"),
                     p(75, pid=904, team=2, group="CB")])
    assert shaped(b, [pl(900, 2, 1), pl(901, 1, 2)])[:2] == ("SWAP", True)                      # CB for S
    assert shaped(b, [pl(900, 2, 1), pl(901, 1, 2), pk(6, 2, 2, 1)])[:2] == ("SWAP", True)
    assert not shaped(b, [pl(900, 2, 1), pl(902, 1, 2)])[1]                                       # 80 vs 70
    assert not shaped(b, [pl(900, 2, 1), pl(903, 1, 2)])[1]                                       # CB for WR
    assert not shaped(b, [pl(900, 2, 1), pl(901, 1, 2), pk(3, 1, 1, 2)])[1]                       # not a late pick
    assert not shaped(b, [pl(900, 2, 1), pl(904, 2, 1), pk(4, 1, 1, 2)])[1]                       # 2 players a side


def blockbuster_books(week=8, seller_record=(1, 5), buyer_dir="CONTEND", seller_dir="REBUILD", age=26, n=0):
    b = books(direction_a=buyer_dir, direction_b=seller_dir, week=week,
              extra=[p(92, age=age, end=2038), p(70, age=23, pid=950, team=1, group="WR")])
    b["wl"] = {1: (5, 1), 2: seller_record}
    b["blockbusters"] = n
    b["picks"].update({(2038, 1, 1): 1})
    return ge.finish_books(b)


def test_blockbuster_gating():
    assets = [pl(900, 2, 1), pk(1, 1, 1, 2), pk(1, 1, 1, 2, season=2038), pl(950, 1, 2)]
    assert shaped(blockbuster_books(), assets)[:2] == ("BLOCKBUSTER", True)
    assert shaped(blockbuster_books(), [pl(900, 2, 1), pk(1, 1, 1, 2), pk(2, 1, 1, 2)])[:2] == ("BLOCKBUSTER", True)
    for b in (blockbuster_books(week=5), blockbuster_books(seller_record=(4, 2)), blockbuster_books(buyer_dir="RETOOL"),
              blockbuster_books(age=30), blockbuster_books(n=ge.MAX_BLOCKBUSTERS), blockbuster_books(seller_dir="CONTEND")):
        assert not shaped(b, assets)[1]
    assert not shaped(blockbuster_books(), [pl(900, 2, 1), pk(3, 1, 1, 2)])[1]                    # a star for a 3rd
    assert not shaped(blockbuster_books(), [pl(900, 2, 1), pk(4, 1, 1, 2)])[1]                    # 88+: no rental


def test_shape_is_a_legality_check_in_season_only():
    b = books(extra=[p(82)])
    bad = deal([pl(900, 2, 1), pk(1, 1, 1, 2)])
    assert not ge.check_legality(bad, b)["shape"]["ok"]
    b["window"] = "OFFSEASON"
    assert "shape" not in ge.check_legality(bad, b)


def test_generated_packages_are_all_valid_shapes():
    b = books(direction_b="REBUILD", extra=[p(82, age=29, end=2037)])
    x = {"type": "PLAYER", "player_id": 900, "from": 2, "to": 1}
    rentals = ge.rental_packages(b, 1, 2, x)
    assert rentals and all(ge.classify_shape(d, b)[1] for d in rentals)
    assert all(len([a for a in d["assets"] if a["from"] == 1]) <= 2 for d in rentals)
    assert {min(a["pick"][1] for a in d["assets"] if a["from"] == 1) for d in rentals} <= {4, 5}
    swaps = ge.swap_packages(b, ge.Valuer(b, FITS), 1, 2, x)
    assert all(ge.classify_shape(d, b)[1] for d in swaps)


def test_sellers_are_losing_or_rebuilding_and_no_starting_qb_targets():
    b = books(direction_b="RETOOL", extra=[p(82)])
    b["wl"] = {1: (3, 1), 2: (3, 1)}
    assert not ge.is_seller(b, 2) and ge.shaped_buy_deals(b, ge.Valuer(b, FITS), 1, ["WR"]) == []
    b["wl"][2] = (1, 3)
    assert ge.is_seller(b, 2)
    v = ge.Valuer(ge.finish_books(b), FITS)
    qb = next(pid for pid in b["roster"][2] if b["players"][pid]["group"] == "QB" and pid in b["ctx"][2]["starter_ids"])
    assert all(x.get("player_id") != qb for d in ge.shaped_buy_deals(b, v, 1, ["QB"]) for x in d["assets"])


def test_pacing_reset_is_in_the_counting_queries():
    class Cur:
        def __init__(self):
            self.sql = []
            self.rows = []

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
        pass                                                           # the fake has no players; only the SQL matters
    counts = [q for q in cur.sql if "count(*) FILTER (WHERE status = 'EXECUTED'" in q]
    made = [q for q in cur.sql if "week = %s AND origin = 'ENGINE'" in q]
    assert counts and "counts_for_pacing" in counts[0]
    assert made and "counts_for_pacing" in made[0]


def test_trade_finder_builds_and_judges_packages():
    b = books(direction_b="REBUILD", extra=[p(82, age=29, end=2037)])
    b["wl"] = {1: (3, 1), 2: (1, 3)}
    b = ge.finish_books(b)
    r = ge.find_packages(b, ge.Valuer(b, FITS), 900)
    assert r["notes"] == [] and r["window"] == "IN_SEASON"
    t = r["teams"][0]
    assert t["team_id"] == 1 and t["tried"] > 0
    for d in t["viable"]:
        assert not d["failed"] and d["shape"] in ("RENTAL", "SWAP") and ge.legal(d["legality"])
    if not t["viable"]:
        assert t["best_miss"] and t["best_miss"]["failed"] and sum(t["misses"].values()) >= 1
    assert ge.find_packages(b, ge.Valuer(b, FITS), 12345) == {"error": "player 12345 isn't on a roster this season"}


def test_trade_finder_explains_a_star_outside_the_deadline_weeks():
    b = books(direction_b="REBUILD", week=5, extra=[p(92, age=26, end=2038)])
    r = ge.find_packages(b, ge.Valuer(b, FITS), 900)
    t = r["teams"][0]
    assert t["n_viable"] == 0 and t["tried"] > 0 and "shape" in t["best_miss"]["failed"]
    assert "weeks 7-8" in t["best_miss"]["legality"]["shape"]["detail"]


# ------------------------------------------------- pick depth (JR, 2026-10-22, week 8: SF and PHI) ----
def test_third_own_pick_out_of_a_draft_is_blocked():
    b = books(extra=[p(82)])
    b["picks"][(2037, 4, 1)] = 2                             # two of team 1's 2037 picks already traded away
    b["picks"][(2037, 5, 1)] = 2
    d = deal([pl(900, 2, 1), pk(6, 1, 1, 2)])
    chk = ge.pick_depth_check(d, b)
    assert not chk["ok"] and "3 of its 2037 picks" in chk["detail"]
    assert ge.pick_depth_check(deal([pl(900, 2, 1), pk(6, 1, 1, 2, season=2038)]), b)["ok"]   # another draft
    b["ts"][1]["direction_score"] = ge.STRONG_CONTEND_D                  # a strong contender may go all in
    assert ge.pick_depth_check(d, b)["ok"]


def test_open_proposals_count_toward_the_pick_limit():
    open_ = [{"id": 7, "status": "PROPOSED", "origin": "ENGINE", "team_a_id": 1, "team_b_id": 2,
              "trade_window": "IN_SEASON", "expires_week": 4, "assets": [pk(4, 1, 1, 2), pk(5, 1, 1, 2)]}]
    b = books(extra=[p(82)], open_=open_)
    assert not ge.pick_depth_check(deal([pl(900, 2, 1), pk(6, 1, 1, 2)]), b)["ok"]
    own = {ge.asset_key(x) for x in open_[0]["assets"]}           # the open proposal's own re-check
    assert ge.pick_depth_check(deal(open_[0]["assets"]), b, ignore_booking=own)["ok"]


def test_season_cap_on_own_picks_across_drafts():
    b = books(extra=[p(82)])
    for k in ((2037, 5, 1), (2037, 6, 1), (2038, 3, 1), (2038, 5, 1)):     # PHI: #10 and #15
        b["picks"][k] = 2
    b["picks_sent"] = {1: {(2037, 5, 1), (2037, 6, 1), (2038, 3, 1), (2038, 5, 1)}}
    chk = ge.pick_depth_check(deal([pl(900, 2, 1), pk(6, 1, 1, 2, season=2039)]), b)
    assert not chk["ok"] and "sent 5 of its own picks this season" in chk["detail"]
    b["picks_sent"][1].discard((2038, 5, 1))                              # four sent so far: one more is the cap
    assert ge.pick_depth_check(deal([pl(900, 2, 1), pk(6, 1, 1, 2, season=2039)]), b)["ok"]
    assert ge.pick_depth_check(deal([pl(900, 2, 1), pk(7, 2, 1, 2, season=2039)]), b)["ok"]   # an acquired pick


def test_picks_get_dearer_as_a_draft_thins():
    b = books()
    v0, r0 = ge.Valuer(b, FITS).value(pk(6, 1, 1, 2), 1), ge.Valuer(b, FITS).value(pk(6, 1, 1, 2), 2)
    b["picks"][(2037, 4, 1)] = 2
    b["picks"][(2037, 5, 1)] = 2
    v2 = ge.Valuer(b, FITS).value(pk(6, 1, 1, 2), 1)
    assert v2 == round(v0 * (1 + 2 * ge.PICK_SCARCITY))
    assert ge.Valuer(b, FITS).value(pk(6, 1, 1, 2), 2) == r0     # the receiver feels no scarcity


def test_smallest_accepted_package_is_chosen():
    big = {"assets": [1, 2, 3], "joint": 5e6}
    small = {"assets": [1, 2], "joint": 1e6}
    assert ge.choose([big, small], __import__("random").Random(1)) is small


# ---------------------------------------------- starter depth (JR, 2026-10-22, Teddy Higgins) ----
def test_starter_without_a_healthy_backup_stays():
    b = books(extra=[p(82)])
    dts = [pid for pid in b["roster"][1] if b["players"][pid]["group"] == "DT"]
    backup = min(dts, key=lambda pid: b["players"][pid]["ovr"])
    starter = max(dts, key=lambda pid: b["players"][pid]["ovr"])
    ok = ge.depth_check(deal([pl(900, 2, 1), pl(starter, 1, 2)]), b)
    assert ok["ok"]                                              # his backup takes the slot
    inj = {backup: {"weeks_remaining": 4, "is_ir": True}}
    b = books(extra=[p(82)], injuries=inj)
    chk = ge.depth_check(deal([pl(900, 2, 1), pl(starter, 1, 2)]), b)
    assert not chk["ok"] and "AAA 1 healthy DT for 2 starter slots" in chk["detail"]
    edge = make_player(901, 2, "DT", 76)
    b = books(extra=[edge], injuries=inj)                        # a DT back fills the slot
    assert ge.depth_check(deal([pl(901, 2, 1), pl(starter, 1, 2)]), b)["ok"]


def test_last_healthy_center_stays():
    c1, c2 = make_player(950, 1, "IOL", 79), make_player(951, 1, "IOL", 77)
    c1["pos"] = c2["pos"] = "C"
    rt = make_player(952, 2, "OT", 79)
    b = books(extra=[c1, c2, rt])
    d = deal([pl(952, 2, 1), pl(951, 1, 2)])
    assert ge.depth_check(d, b)["ok"]                            # Giamello healthy: Higgins can go
    b = books(extra=[c1, c2, rt], injuries={950: {"weeks_remaining": 1, "is_ir": True}})
    chk = ge.depth_check(d, b)
    assert not chk["ok"] and "AAA no healthy C left" in chk["detail"]
