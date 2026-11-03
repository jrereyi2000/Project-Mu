"""Trade value model (docs/done/team_management/03_GM_AGENTS_PLAN.md Phase 3). Fixture rows only: no database."""
import math
import random

from engines import trade_value as tv
from engines.trade_value import (GENERIC_AGING, build_fits, fit_aging, fit_market, league_value, pick_value,
                                 player_value, tradeable, worth)


def market_rows():
    rng = random.Random(1)
    rows = []
    for g, shift in (("QB", 0.5), ("WR", 0.2), ("CB", 0.1), ("RB", -0.3), ("K", -0.8)):
        n = 40 if g != "K" else 8                              # K: too few rows -> league intercept
        for _ in range(n):
            o = rng.randint(65, 97)
            rows.append((g, o, math.exp(15 + shift + 0.09 * (o - 75) + rng.gauss(0, 0.2))))
    return rows


def draft_rows():
    rows = []
    for pick in range(1, 225):
        ovr = 78 - 4 * math.log(pick) + (1 if pick % 7 == 0 else 0)
        hit = 13_000_000 * pick ** -0.55 + 600_000
        rows.append((pick, ovr, hit))
    return rows


def aging_pairs():
    rng = random.Random(2)
    return [("WR", a, 80, 80 + GENERIC_AGING[min(5, max(0, (a - 21) // 3))] + rng.gauss(0, 1))
            for a in range(21, 36) for _ in range(20)]


FITS = build_fits(market_rows(), aging_pairs(), draft_rows())


def player(pid=1, group="WR", ovr=85, age=27, end=2038, hits=None, projected=(), team=1, dev="Normal", ntc=False):
    hits = hits or {y: 5_000_000 for y in range(2036, end + 1)}
    years = {y: (h, y in projected) for y, h in hits.items()}
    return {"id": pid, "team_id": team, "group": group, "ovr": ovr, "age": age, "dev": dev,
            "contract": {"end_season": end, "years": years, "no_trade_clause": ntc}}


def ctx(direction, team=2, needs=(), lines=None, youth=0.0):
    return {"team_id": team, "direction": direction, "needs": list(needs), "lines": lines or {"WR": 70},
            "starter_ids": set(), "position_bias": {}, "youth_bias": youth, "pick_affinity": 1.0, "slots": {}}


def test_worth_monotone_in_ovr():
    m = FITS["market"]
    vals = [worth(o, "WR", m) for o in range(60, 100)]
    assert all(b > a for a, b in zip(vals, vals[1:]))
    assert "K" not in m["a"] and worth(80, "K", m) == worth(80, None, m)     # small group -> league intercept


def test_aging_shrinks_toward_generic_on_small_samples():
    small = fit_aging([("TE", 30, 80, 90)])                                    # one +10 pair at age 30
    assert abs(small["TE"][3] - small["generic"][3]) < 1.0
    big = fit_aging([("TE", 30, 80, 90)] * 400)
    assert big["TE"][3] > 8


def test_surplus_ends_at_contract_end():
    short = player_value(player(end=2036), ctx("RETOOL"), FITS, 2036)
    long = player_value(player(end=2038), ctx("RETOOL"), FITS, 2036)
    assert [y[0] for y in short["years"]] == [2036]
    assert [y[0] for y in long["years"]] == [2036, 2037, 2038]


def test_projected_years_counted_and_flagged():
    p = player(end=2038, hits={2036: 5e6, 2037: 5e6, 2038: 7e6}, projected={2038})
    v = player_value(p, ctx("RETOOL"), FITS, 2036)
    assert v["projected"] and v["years"][-1][3] is True
    assert not player_value(player(end=2038), ctx("RETOOL"), FITS, 2036)["projected"]


def test_rebuild_values_veteran_below_contend():
    vet = player(age=31, ovr=88, end=2038)
    assert player_value(vet, ctx("REBUILD"), FITS, 2036)["value"] < player_value(vet, ctx("CONTEND"), FITS, 2036)["value"]


def test_need_raises_utility_and_backup_lowers_it():
    p = player(ovr=85)
    base = player_value(p, ctx("RETOOL"), FITS, 2036)["value"]
    need = player_value(p, ctx("RETOOL", needs=[{"group": "WR", "score": 10}]), FITS, 2036)["value"]
    backup = player_value(p, ctx("RETOOL", lines={"WR": 90}), FITS, 2036)["value"]
    assert need > base > backup


def test_pick_curve_monotone_and_r1_above_r2():
    v = FITS["pick"]["value"]
    assert all(b <= a + 1e-6 for a, b in zip(v, v[1:]))
    assert sum(v[:32]) / 32 > sum(v[32:64]) / 32 > 0
    r1 = pick_value({"draft_season": 2037, "round": 1, "original_team_id": 5}, None, FITS, 2036)
    r2 = pick_value({"draft_season": 2037, "round": 2, "original_team_id": 5}, None, FITS, 2036)
    later = pick_value({"draft_season": 2039, "round": 1, "original_team_id": 5}, None, FITS, 2036)
    assert r1 > r2 and r1 > 0 and later < r1


def test_pick_direction_multipliers():
    pk = {"draft_season": 2037, "round": 1, "original_team_id": 5}
    reb = pick_value(pk, ctx("REBUILD"), FITS, 2036)
    con = pick_value(pk, ctx("CONTEND"), FITS, 2036)
    assert reb > 0 and con > 0 and reb != con


def test_ntc_def_injured_and_flipped_excluded():
    assert tradeable(player(ntc=True)) == (False, "no-trade clause")
    assert tradeable(dict(player(), group=None))[0] is False                   # the DEF row has no group
    assert tradeable(dict(player(), contract=None)) == (False, "no active contract")
    assert tradeable(player(pid=7), injured={7}) == (False, "injured")
    assert tradeable(player(pid=7), acquired={7}) == (False, "acquired by trade this season")
    assert tradeable(player()) == (True, None)


def test_league_value_is_neutral():
    p = player(ovr=85)
    assert league_value(p, FITS, 2036) == player_value(p, None, FITS, 2036)["value"]
    assert fit_market([])["b"] > 0


def test_rookie_year_block():
    rookie = dict(player(), yoe=0)
    assert tradeable(rookie, rookie_year=True) == (False, "rookie season")
    assert tradeable(rookie) == (True, None)
    assert tradeable(dict(player(), yoe=1), rookie_year=True) == (True, None)


def test_dev_premium_own_team_above_outside_view():
    from engines.trade_value import DEV_PREMIUM, DEV_VISIBILITY, dev_premium
    xf = dict(player(team=1), dev="Superstar X-Factor")
    assert dev_premium(xf, own=True) == DEV_PREMIUM["Superstar X-Factor"]
    assert dev_premium(xf, own=False) == 1 + DEV_VISIBILITY * (DEV_PREMIUM["Superstar X-Factor"] - 1)
    assert dev_premium(dict(xf, dev="Hidden"), own=True) == dev_premium(dict(xf, dev=None), own=False) == 1.0
    own_ctx = dict(ctx("RETOOL", team=1), starter_ids={1})
    normal = dict(xf, dev="Normal")
    gap_own = player_value(xf, own_ctx, FITS, 2036)["value"] - player_value(normal, own_ctx, FITS, 2036)["value"]
    gap_out = player_value(xf, ctx("RETOOL"), FITS, 2036)["value"] - player_value(normal, ctx("RETOOL"), FITS, 2036)["value"]
    assert gap_own > gap_out > 0                     # his own team values the X-Factor premium most


def test_rotation_tier_between_starter_and_backup():
    from engines.trade_value import BACKUP_U, ROTATION_U, utility
    p = player(pid=5, team=1, ovr=85)
    base = {"team_id": 1, "starter_ids": set(), "rotation_ids": set(), "needs": [], "position_bias": {}}
    assert utility(p, base) == BACKUP_U
    assert utility(p, dict(base, rotation_ids={5})) == ROTATION_U
    assert utility(dict(p, dev="Superstar X-Factor"), base) == ROTATION_U   # an own X-Factor is never a deep backup
    out = {"team_id": 2, "lines": {"WR": 90}, "rotation_lines": {"WR": 80}, "needs": [], "position_bias": {}}
    assert utility(p, out) == ROTATION_U and utility(dict(p, ovr=75), out) == BACKUP_U


class _Cursor:
    """read_players' three queries: rostered players, ACTIVE contracts, contract years."""
    def __init__(self):
        self.calls = 0

    def execute(self, sql, args=None):
        self.calls += 1
        self.sql = sql

    def fetchall(self):
        if "FROM player_seasons" in self.sql:
            return [(1, 10, "QB", 80, 27, "Normal", 5, "A. Starter", "Al Starter"),
                    (2, 10, "WR", 60, 23, "Normal", 1, "S. Squad", "Sam Squad")]
        if "FROM player_contracts" in self.sql:
            return [(77, 1, 10, 2036, 2, False, 0, 0, "SCAN", 3, 9_000_000, 0, None)]
        return [(77, 2036, 3_000_000, False)]


def test_practice_squad_is_a_rostered_player_without_an_active_contract():
    players = tv.read_players(_Cursor(), 2036)
    assert (players[1]["practice_squad"], players[2]["practice_squad"]) == (False, True)


# ------------------------------------------------- cap-share market (CAP_RELATIVE_CONTRACTS_PLAN §2.2-§2.3) ----
from engines.cap_history import cap_in                                          # noqa: E402

CAPS = {2036: 367_000_000}


def test_cap_adjust_new_deal_keeps_its_apy():
    assert tv.cap_adjust([("WR", 85, 60_000_000, 4, 4)], CAPS, 2036) == [("WR", 85, 15_000_000)]


def test_cap_adjust_scales_an_old_deal_by_the_cap_ratio():
    (_, _, d), = tv.cap_adjust([("QB", 90, 100_000_000, 4, 2)], CAPS, 2036)       # signed 2034
    assert d == 25_000_000 * cap_in(2036, CAPS) / cap_in(2034, CAPS)
    assert d > 25_000_000


def test_cap_adjust_prices_on_apy_not_cap_hit():
    # A backloaded 4-year $40M deal signed this season: its later cap hits are far above $10M, but only APY counts.
    (_, _, d), = tv.cap_adjust([("CB", 80, 40_000_000, 4, 4)], CAPS, 2036)
    assert d == 10_000_000


def test_cap_adjust_drops_bad_rows():
    bad = [("WR", 80, 10_000_000, None, 1), ("WR", 80, 10_000_000, 0, 0), ("WR", 80, None, 3, 3),
           ("WR", 80, 0, 3, 3), ("WR", 80, 10_000_000, 2, 3), ("WR", 80, 10_000_000, 2, None)]
    assert tv.cap_adjust(bad, CAPS, 2036) == []


def test_fit_on_adjusted_rows_recovers_a_known_share_curve():
    """Deals signed at share(ovr) = exp(-4 + 0.1 (ovr - 75)) of the cap in their signing season, of every deal age,
    fit back to that share of this season's cap."""
    rows = []
    for ovr in range(65, 98):
        for age in range(4):
            apy = math.exp(-4 + 0.1 * (ovr - 75)) * cap_in(2036 - age, CAPS)
            rows.append(("WR", ovr, apy * 4, 4, 4 - age))
    m = fit_market(tv.cap_adjust(rows, CAPS, 2036))
    assert abs(m["b"] - 0.1) < 1e-6
    assert abs(math.exp(m["a"]["WR"]) / 367_000_000 - math.exp(-4)) < 1e-9
    raw = fit_market([(g, o, t / ln) for g, o, t, ln, _ in rows])                 # unadjusted APY trails the cap
    assert raw["a"]["WR"] < m["a"]["WR"]


def test_hand_built_market_has_no_cap_keys_and_still_works():
    assert "cap" not in FITS["market"] and "basis" not in FITS["market"]
    assert worth(80, "WR", FITS["market"]) > 0


def test_market_premium_is_jr_d2():
    assert tv.MARKET_PREMIUM == 1.20


class _DriftCursor:
    """league_caps' query and new_deal_shares' query: (season, position, total_value, length)."""
    def __init__(self, rows, caps=None):
        self.rows, self.caps = rows, caps or {}

    def execute(self, sql, args=None):
        self.sql, self.args = sql, args

    def fetchall(self):
        if "team_cap_seasons" in self.sql:
            return list(self.caps.items())
        assert "length = c.years_remaining" in self.sql and "SUPERSEDED" in self.sql
        return self.rows


def test_drift_rows_medians_by_season_and_group():
    caps = {2036: 300_000_000, 2037: 320_000_000}
    rows = [(2036, "WR", 3_000_000 * 3, 3), (2036, "WR", 6_000_000 * 2, 2), (2036, "WR", 9_000_000, 1),
            (2036, "QB", 30_000_000 * 4, 4),
            (2037, "WR", 6_400_000 * 2, 2), (2037, "WR", 3_200_000, 1)]
    out = tv.drift_rows(_DriftCursor(rows, caps), 2037)
    by = {(r["season"], r["group"]): r for r in out}
    assert [(r["season"], r["group"]) for r in out] == [(2036, "QB"), (2036, "WR"), (2037, "WR")]
    assert by[(2036, "WR")]["n"] == 3 and abs(by[(2036, "WR")]["median_share"] - 0.02) < 1e-12
    assert abs(by[(2036, "WR")]["p75_share"] - 0.025) < 1e-12                     # halfway between 0.02 and 0.03
    assert abs(by[(2036, "QB")]["median_share"] - 0.1) < 1e-12
    assert abs(by[(2037, "WR")]["median_share"] - 0.015) < 1e-12                  # 0.02 and 0.01 of $320M


def test_drift_report_prints_league_row_first():
    rows = [(2036, "WR", 6_000_000, 1), (2036, "QB", 30_000_000, 1)]
    lines = []
    tv.drift_report(_DriftCursor(rows, {2036: 300_000_000}), 2036, log=lines.append)
    assert lines[1].split()[:2] == ["group", "2036"]
    assert [ln.split()[0] for ln in lines[2:]] == ["LEAGUE", "QB", "WR"]
    assert "6.00% (2)" in lines[2] and "10.00% (1)" in lines[3]


def test_basis_line():
    assert tv.basis_line(2036, {2036: 367_000_000}) == (
        "Market basis: APY / cap at signing, scaled to the 2036 cap $367.0M (caps known: 2036; earlier seasons "
        "estimated at +6.5%/yr)")
    assert "estimated" in tv.basis_line(2038, {2036: 367_000_000}).split("(")[0]

