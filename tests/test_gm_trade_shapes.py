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
