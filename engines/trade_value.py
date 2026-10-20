"""
Trade value model (docs/team_management/03_GM_AGENTS_PLAN.md §2.3, Phase 3; PROJECT_SPEC §2.17).

Everything is in dollars of surplus, so players and picks compare directly. Pure: the fits take rows, the values
take dicts; read_* gather the rows from the DB (read-only) and the fits are recomputed every run.

  W(ovr, group)   market worth: ln(cap_hit) = a_group + b (ovr - 75), from 2036 ACTIVE SCAN contracts of players
                  with 4+ years' experience (no rookie deals); a group with < 15 rows uses the league intercept.
                  x MARKET_PREMIUM: replacing a player costs more than the average existing deal.
  aging           mean same-player OVR change 2035 -> 2036 by age band and group, shrunk toward a generic curve;
                  dev trait adds +1/+2/+2 a year up to age 26 (Star / Superstar / X-Factor).
  player value    V_T = sum_y d_T[y] (W(ovr_y) u_T - cap_hit_y) through the contract's last season (projected
                  re-signing years count and are flagged); u_T 1.0 starter, 1 + 0.5 need share for a needed slot,
                  0.3 backup, x position_bias; youth_bias scales the worth term by 1 - 0.05 youth_bias (age - 28).
  pick value      expected OVR and rookie cap hit by overall pick from the 2036 draft, smoothed and monotone;
                  V = sum_k<4 d[k] (E[W(ovr_k)] u_k - ROOKIE_COST_WEIGHT hit_k) x pick_affinity x direction
                  (REBUILD 1.2, CONTEND 0.8). Calibration (JR reviews --report): Madden's rookie scale is so steep
                  that pure surplus would make the first picks the least valuable, so E[W] carries the outcome
                  spread (upside), u_k is the chance the pick starts, and rookie cost is weighted 0.25.

  python3 -m engines.trade_value --report [--season S]
"""
import argparse
import math
import os

DISCOUNTS = {"CONTEND": (1.0, 0.55, 0.35, 0.25, 0.2), "RETOOL": (0.8, 0.75, 0.6, 0.5, 0.4),
             "REBUILD": (0.35, 0.8, 0.9, 0.9, 0.85)}
HORIZON = 5
MIN_GROUP_ROWS = 15
OVR_PIVOT = 75
MARKET_PREMIUM = 1.3
STARTER_U, BACKUP_U, NEED_U = 1.0, 0.3, 0.5
YOUTH_PIVOT_AGE = 28
YOUTH_CLAMP = (0.7, 1.3)
DEV_BONUS = {"Star": 1, "Superstar": 2, "Superstar X-Factor": 2}
DEV_MAX_AGE = 26
AGE_BANDS = ((0, 23), (24, 26), (27, 29), (30, 31), (32, 33), (34, 99))
GENERIC_AGING = (1.5, 0.5, -0.3, -1.2, -2.2, -3.0)    # OVR a year by band, the prior the fit shrinks toward
AGING_SHRINK = 20                                       # pseudo-observations of the generic curve
# picks
DRAFT_PICKS = 224
PICK_YEARS = 4                                          # rookie deals are 4 years (2036 rookies: 177 of 188)
ROOKIE_ESCALATOR = 1.05                                 # rookie cap hits rise ~5% a year
ROOKIE_GROWTH = (2.5, 1.5, 1.5)                         # OVR a year in years 1-3: the 2036 OVR-by-experience table
                                                        # (67.8, 72.9, 75.1, 78.4) halved for survivor bias
PICK_SIGMA = (5.0, 1.5)                                 # outcome spread: sigma_k = 5 + 1.5 k OVR
STARTER_LINE, STARTER_SPREAD = 74.0, 4.0                # P(start) = Phi((ovr - 74)/4)
ROOKIE_COST_WEIGHT = 0.25
PICK_DIRECTION = {"REBUILD": 1.2, "CONTEND": 0.8}
SMOOTH_WINDOW = 4


# -------------------------------------------------------------------- fits ----
def fit_market(rows):
    """rows: [(group, ovr, cap_hit)] -> {"b", "a_league", "a": {group: a}, "n": {group: rows}}. One slope, group
    intercepts (within-group regression); groups under MIN_GROUP_ROWS use the league intercept."""
    rows = [(g, o, h) for g, o, h in rows if g and o is not None and h and h > 0]
    if len(rows) < 2:
        return {"b": 0.09, "a_league": math.log(3_000_000), "a": {}, "n": {}}
    by = {}
    for g, o, h in rows:
        by.setdefault(g, []).append((o - OVR_PIVOT, math.log(h)))
    num = den = 0.0
    for pts in by.values():
        mx = sum(x for x, _ in pts) / len(pts)
        my = sum(y for _, y in pts) / len(pts)
        num += sum((x - mx) * (y - my) for x, y in pts)
        den += sum((x - mx) ** 2 for x, _ in pts)
    b = num / den if den else 0.09
    allx = [x for pts in by.values() for x, _ in pts]
    ally = [y for pts in by.values() for _, y in pts]
    a_league = sum(ally) / len(ally) - b * sum(allx) / len(allx)
    a = {}
    for g, pts in by.items():
        if len(pts) >= MIN_GROUP_ROWS:
            a[g] = sum(y for _, y in pts) / len(pts) - b * sum(x for x, _ in pts) / len(pts)
    return {"b": b, "a_league": a_league, "a": a, "n": {g: len(p) for g, p in by.items()}}


def worth(ovr, group, market):
    """W(ovr, group): market worth in dollars for a season (x MARKET_PREMIUM)."""
    a = market["a"].get(group, market["a_league"])
    return MARKET_PREMIUM * math.exp(a + market["b"] * ((ovr or 0) - OVR_PIVOT))


def age_band(age):
    for i, (lo, hi) in enumerate(AGE_BANDS):
        if lo <= (age or 0) <= hi:
            return i
    return len(AGE_BANDS) - 1


def fit_aging(pairs, shrink=AGING_SHRINK):
    """pairs: [(group, age_before, ovr_before, ovr_after)] -> {"generic": [...], group: [delta by band]}: each
    cell's mean shrunk toward the league band mean, itself shrunk toward GENERIC_AGING."""
    cells, league = {}, {}
    for g, age, o0, o1 in pairs:
        if o0 is None or o1 is None or age is None:
            continue
        band = age_band(age)
        cells.setdefault((g, band), []).append(o1 - o0)
        league.setdefault(band, []).append(o1 - o0)

    def shrunk(vals, prior):
        n = len(vals)
        return (sum(vals) + shrink * prior) / (n + shrink)

    generic = [shrunk(league.get(i, []), GENERIC_AGING[i]) for i in range(len(AGE_BANDS))]
    out = {"generic": generic}
    for g in {g for g, _ in cells}:
        out[g] = [shrunk(cells.get((g, i), []), generic[i]) for i in range(len(AGE_BANDS))]
    return out


def ovr_path(ovr, age, group, dev, aging, years=HORIZON):
    """Projected OVR for this season and the next years-1: aging by band, plus the dev bonus up to age 26."""
    curve = aging.get(group, aging["generic"])
    bonus = DEV_BONUS.get(dev or "Normal", 0)
    out, o, a = [], float(ovr or 0), age or 27
    for k in range(years):
        out.append(o)
        o += curve[age_band(a)] + (bonus if a <= DEV_MAX_AGE else 0)
        a += 1
    return out


def _smooth_monotone(values, increasing=False):
    """Centered moving average (window +-SMOOTH_WINDOW) then pool-adjacent-violators to a non-increasing (or
    non-decreasing) sequence."""
    n = len(values)
    sm = []
    for i in range(n):
        win = [v for v in values[max(0, i - SMOOTH_WINDOW): i + SMOOTH_WINDOW + 1] if v is not None]
        sm.append(sum(win) / len(win) if win else None)
    known = [(i, v) for i, v in enumerate(sm) if v is not None]
    if not known:
        return [0.0] * n
    filled = []
    for i in range(n):                       # nearest known value for any gap
        filled.append(min(known, key=lambda kv: abs(kv[0] - i))[1] if sm[i] is None else sm[i])
    sign = 1 if increasing else -1
    blocks = []                              # [sum, count]
    for v in filled:
        blocks.append([sign * v, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, c = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
    out = []
    for s, c in blocks:
        out += [sign * s / c] * c
    return out


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def fit_pick_curve(draft_rows, market, picks=DRAFT_PICKS):
    """draft_rows: [(overall_pick, ovr, rookie_cap_hit_year1)] (ovr = the rookie's roster OVR, else the scouted
    one; hit may be None) -> {"ovr": [...], "hit": [...], "value": [...]} indexed pick-1, smoothed and monotone
    (OVR and hit non-increasing; the neutral value too). value = a RETOOL team's pick value, u from P(start)."""
    ovr = [None] * picks
    hit = [None] * picks
    for pick, o, h in draft_rows:
        if pick and 1 <= pick <= picks:
            if o is not None:
                ovr[pick - 1] = o if ovr[pick - 1] is None else (ovr[pick - 1] + o) / 2
            if h:
                hit[pick - 1] = h
    ovr_s = _smooth_monotone(ovr)
    hit_s = _smooth_monotone(hit)
    curve = {"ovr": ovr_s, "hit": hit_s}
    raw = [neutral_pick_value(i + 1, curve, market) for i in range(picks)]
    curve["value"] = _smooth_monotone(raw)
    return curve


def pick_years(pick, curve, market, discounts, offset=0):
    """Per-year (worth term, cost term, discount) for a pick: E[W] with the outcome spread, x P(start)."""
    i = min(max(pick, 1), len(curve["ovr"])) - 1
    b = market["b"]
    out, o = [], curve["ovr"][i]
    for k in range(PICK_YEARS):
        sigma = PICK_SIGMA[0] + PICK_SIGMA[1] * k
        u = BACKUP_U + (STARTER_U - BACKUP_U) * _phi((o - STARTER_LINE) / STARTER_SPREAD)
        w = worth(o, None, market) * math.exp(b * b * sigma * sigma / 2) * u
        cost = curve["hit"][i] * ROOKIE_ESCALATOR ** k
        d = discounts[min(k + offset, len(discounts) - 1)]
        out.append((w, cost, d))
        if k < len(ROOKIE_GROWTH):
            o += ROOKIE_GROWTH[k]
    return out


def neutral_pick_value(pick, curve, market, offset=0):
    return sum(d * (w - ROOKIE_COST_WEIGHT * c) for w, c, d in pick_years(pick, curve, market, DISCOUNTS["RETOOL"],
                                                                           offset))


# ----------------------------------------------------------------- values ----
def tradeable(p, injured=(), acquired=()):
    """(ok, reason). Out: no group (the DEF row), no active contract, a no-trade clause (C6), injured (C7: any
    injured player), acquired by trade this season (no flipping)."""
    if not p.get("group"):
        return False, "no position group"
    c = p.get("contract")
    if not c:
        return False, "no active contract"
    if c.get("no_trade_clause"):
        return False, "no-trade clause"
    if p["id"] in injured:
        return False, "injured"
    if p["id"] in acquired:
        return False, "acquired by trade this season"
    return True, None


def utility(p, ctx):
    """u_T(group): 1 + 0.5 x need share if he'd start in a slot T needs, 1.0 if he'd start, 0.3 as a backup;
    x position_bias. ctx: {team_id, starter_ids, lines {group: weakest starter OVR or None}, needs, position_bias}."""
    g = p["group"]
    if p.get("team_id") == ctx.get("team_id"):
        starts = p["id"] in ctx.get("starter_ids", ())
    else:
        line = ctx.get("lines", {}).get(g)
        starts = line is None or (p.get("ovr") or 0) > line
    if starts:
        needs = ctx.get("needs") or []
        total = sum(n["score"] for n in needs)
        score = next((n["score"] for n in needs if n["group"] == g), 0)
        u = STARTER_U + (NEED_U * score / total if total and score else 0.0)
    else:
        u = BACKUP_U
    return u * (ctx.get("position_bias") or {}).get(g, 1.0)


def youth_mult(age, youth_bias):
    m = 1 - 0.05 * (youth_bias or 0) * ((age or YOUTH_PIVOT_AGE) - YOUTH_PIVOT_AGE)
    return min(YOUTH_CLAMP[1], max(YOUTH_CLAMP[0], m))


def player_value(p, ctx, fits, season):
    """V_T in dollars and its parts. p: {id, team_id, group, ovr, age, dev, contract: {end_season, years: {year:
    (cap_hit, projected)}}}. ctx: utility()'s ctx + {direction, youth_bias}; ctx None = the neutral league value
    (RETOOL, u 1, no biases). Returns {value, u, years: [(year, worth, cap_hit, projected, discount)],
    projected: bool}."""
    neutral = ctx is None
    d = DISCOUNTS["RETOOL" if neutral else (ctx.get("direction") or "RETOOL")]
    u = 1.0 if neutral else utility(p, ctx)
    ym = 1.0 if neutral else youth_mult(p.get("age"), ctx.get("youth_bias"))
    c = p.get("contract") or {}
    end = c.get("end_season", season)
    path = ovr_path(p.get("ovr"), p.get("age"), p["group"], p.get("dev"), fits["aging"])
    total, rows, proj = 0.0, [], False
    for y in range(HORIZON):
        year = season + y
        if year > end:
            break                                            # he re-signs at market: no surplus after the deal
        hit, projected = (c.get("years") or {}).get(year, (0, False))
        w = worth(path[y], p["group"], fits["market"]) * u * ym
        total += d[y] * (w - (hit or 0))
        rows.append((year, round(w), hit or 0, bool(projected), d[y]))
        proj = proj or bool(projected)
    return {"value": round(total), "u": round(u, 3), "years": rows, "projected": proj}


def league_value(p, fits, season):
    return player_value(p, None, fits, season)["value"]


def projected_slot(team_id, ctx_ranks):
    """Next draft's slot (1-32) from the league's proj_wins ranks: {team_id: slot}; unknown = 16."""
    return (ctx_ranks or {}).get(team_id, 16)


def pick_value(pick, ctx, fits, season):
    """A draft pick's value to team T. pick: {draft_season, round, original_team_id}. ctx: {direction,
    pick_affinity, slots {team_id: 1-32}} or None (neutral). Next year's pick uses the original team's
    projected slot; later drafts use the middle of the round with one more year of discount per year out."""
    out = pick["draft_season"] - season - 1
    slot = projected_slot(pick["original_team_id"], (ctx or {}).get("slots")) if out == 0 else 16
    overall = (pick["round"] - 1) * 32 + slot
    direction = (ctx or {}).get("direction") or "RETOOL"
    d = DISCOUNTS["RETOOL" if ctx is None else direction]
    v = sum(dk * (w - ROOKIE_COST_WEIGHT * c) for w, c, dk in pick_years(overall, fits["pick"], fits["market"], d,
                                                                          out))
    if ctx is not None:
        v *= (ctx.get("pick_affinity") or 1.0) * PICK_DIRECTION.get(direction, 1.0)
    return round(v)


# --------------------------------------------------------------------- DB ----
def read_fit_rows(cur, season):
    """Rows for the three fits (read-only)."""
    from engines.gm_direction import position_group
    cur.execute("""SELECT ps.position, ps.overall_rating, y.cap_hit
                   FROM player_contracts c
                   JOIN player_contract_years y ON y.contract_id = c.id AND y.year = c.season
                   JOIN player_seasons ps ON ps.player_id = c.player_id AND ps.season = c.season
                   WHERE c.season = %s AND c.status = 'ACTIVE' AND c.source = 'SCAN'
                     AND ps.years_of_experience >= 4 AND NOT y.projected""", (season,))
    market = [(position_group(pos), o, h) for pos, o, h in cur.fetchall()]
    cur.execute("""SELECT b.position, b.age, b.overall_rating, a.overall_rating
                   FROM player_seasons a JOIN player_seasons b ON b.player_id = a.player_id AND b.season = a.season - 1
                   WHERE a.season = %s""", (season,))
    aging = [(position_group(pos), age, o0, o1) for pos, age, o0, o1 in cur.fetchall()]
    cur.execute("""SELECT d.actual_pick, COALESCE(ps.overall_rating, d.ovr),
                          (SELECT y.cap_hit FROM player_contracts c JOIN player_contract_years y ON y.contract_id = c.id
                           WHERE c.player_id = d.player_id AND c.status = 'ACTIVE' AND y.year = d.draft_season
                           ORDER BY c.id DESC LIMIT 1)
                   FROM draft_prospects d
                   LEFT JOIN player_seasons ps ON ps.player_id = d.player_id AND ps.season = d.draft_season
                   WHERE d.draft_season = (SELECT max(draft_season) FROM draft_prospects
                                           WHERE draft_season <= %s AND actual_pick IS NOT NULL)
                     AND d.actual_pick IS NOT NULL""", (season,))
    draft = cur.fetchall()
    return market, aging, draft


def build_fits(market_rows, aging_pairs, draft_rows):
    market = fit_market(market_rows)
    return {"market": market, "aging": fit_aging(aging_pairs), "pick": fit_pick_curve(draft_rows, market)}


def fits_from_db(cur, season):
    return build_fits(*read_fit_rows(cur, season))


def read_players(cur, season):
    """{player_id: player dict} for every rostered player with his ACTIVE contract (years with projected flags)."""
    from engines.gm_direction import position_group
    cur.execute("""SELECT ps.player_id, ps.team_id, ps.position, ps.overall_rating, ps.age, ps.dev_trait,
                          ps.years_of_experience, p.player_name, p.full_name
                   FROM player_seasons ps JOIN players p ON p.id = ps.player_id
                   WHERE ps.season = %s AND ps.released_week IS NULL AND ps.position <> 'DEF'
                     AND ps.team_id IS NOT NULL""", (season,))
    players = {}
    for pid, tid, pos, ovr, age, dev, yoe, name, full in cur.fetchall():
        players[pid] = {"id": pid, "team_id": tid, "pos": pos, "group": position_group(pos), "ovr": ovr, "age": age,
                        "dev": dev, "yoe": yoe, "name": full or name, "contract": None}
    cur.execute("""SELECT c.id, c.player_id, c.team_id, c.season, c.years_remaining, c.no_trade_clause, c.cut_penalty,
                          c.cut_savings, c.source, c.length, c.total_value, c.bonus, c.incentive
                   FROM player_contracts c WHERE c.season = %s AND c.status = 'ACTIVE'""", (season,))
    by_contract = {}
    for cid, pid, tid, s, rem, ntc, pen, sav, src, length, total, bonus, inc in cur.fetchall():
        if pid in players:
            c = {"contract_id": cid, "team_id": tid, "season": s, "years_remaining": rem,
                 "end_season": s + (rem or 1) - 1, "no_trade_clause": bool(ntc), "cut_penalty": pen,
                 "cut_savings": sav, "source": src, "length": length, "total_value": total, "bonus": bonus,
                 "incentive": inc, "years": {}}
            players[pid]["contract"] = c
            by_contract[cid] = c
    if by_contract:
        cur.execute("""SELECT contract_id, year, cap_hit, projected FROM player_contract_years
                       WHERE contract_id = ANY(%s)""", (list(by_contract),))
        for cid, year, hit, projected in cur.fetchall():
            by_contract[cid]["years"][year] = (hit, bool(projected))
    for c in by_contract.values():               # a re-signing's projected years extend the deal
        proj = [y for y, (h, pr) in c["years"].items() if pr and h]
        if proj:
            c["end_season"] = max(c["end_season"], max(proj))
    return players


# ------------------------------------------------------------------ report ----
def report(cur, season, log=print, top=40):
    fits = fits_from_db(cur, season)
    m = fits["market"]
    log(f"Market fit: ln(hit) = a + {m['b']:.4f} (ovr - 75); league W(75) ${worth(75, None, m) / 1e6:.2f}M, "
        f"W(85) ${worth(85, None, m) / 1e6:.2f}M, W(95) ${worth(95, None, m) / 1e6:.2f}M (x{MARKET_PREMIUM} premium)")
    for g in sorted(m["n"]):
        own = "own" if g in m["a"] else "league"
        log(f"  {g:5} n {m['n'][g]:4}  W(80) ${worth(80, g, m) / 1e6:6.2f}M  ({own} intercept)")
    log("Aging (OVR a year by band ≤23, 24-26, 27-29, 30-31, 32-33, 34+):")
    log("  generic " + " ".join(f"{v:+.2f}" for v in fits["aging"]["generic"]))
    players = read_players(cur, season)
    vals = []
    for p in players.values():
        if p["group"] and p["contract"]:
            v = player_value(p, None, fits, season)
            vals.append((v["value"], p, v))
    vals.sort(key=lambda x: -x[0])
    log(f"Top {top} players by league value V_L:")
    for v, p, d in vals[:top]:
        c = p["contract"]
        log(f"  ${v / 1e6:6.2f}M  {p['name']:24} {p['pos']:4} {p['ovr']} age {p['age']} {p['dev'] or '-':8} "
            f"through {c['end_season']}{' (projected)' if d['projected'] else ''}  "
            f"hit ${(c['years'].get(season, (0, 0))[0] or 0) / 1e6:.2f}M")
    log("Pick chart (neutral, next draft):")
    curve = fits["pick"]
    for pk in (1, 2, 5, 10, 16, 24, 32, 48, 64, 96, 128, 160, 192, 224):
        log(f"  pick {pk:3}: ${curve['value'][pk - 1] / 1e6:6.2f}M  (exp OVR {curve['ovr'][pk - 1]:.1f}, "
            f"rookie hit ${curve['hit'][pk - 1] / 1e6:.2f}M)")
    for r in range(1, 8):
        seg = curve["value"][(r - 1) * 32: r * 32]
        log(f"  round {r}: avg ${sum(seg) / len(seg) / 1e6:.2f}M")
    return fits


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--season", type=int)
    a = ap.parse_args(argv)
    import psycopg
    dsn = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT current_season FROM league_state WHERE id = 1")
        season = a.season or cur.fetchone()[0]
        report(cur, season)
        conn.rollback()


if __name__ == "__main__":
    main()
