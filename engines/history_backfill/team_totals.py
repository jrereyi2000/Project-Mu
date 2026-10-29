# @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history)
# @REPLACED-BY: NONE (Mu 2026-2036 history discarded; AUDIT_LEDGER.md §3b)
"""
Team stat totals for 2026-2034 (docs/done/HISTORY_BACKFILL_PLAN.md §3.4; PROJECT_SPEC §3.3k).

Each team-season's totals (passing, rushing, defense, kicking, punting, returns, sacks allowed) are drawn from its
applied team arc (team_season_results SYNTH: points for and against): every stat is a least-squares line on points
for and against fitted to the 64 captured team-seasons of 2035-2036 (season_stat_lines summed by team; defense
sums run slightly low, since the season lists stop at 250 rows), plus Gaussian noise at the fit's residual SD. The
identities hold by construction: receptions, receiving yards and TDs equal the passing ones; sacks allowed equal
sacks taken; each season's interceptions and sacks made add up to those thrown and taken (scaled per season).

These are soft targets for player_lines (the known players' lines can run over them) and the base for fill-ins.
fit() and draw() are pure; calibrate() and arc() read the database.
"""
import random
from collections import defaultdict

OFFENSE = ("pass_attempts", "completions", "pass_yards", "pass_tds", "interceptions", "pass_sacks",
           "rush_attempts", "rush_yards", "rush_tds", "fumb")
DEFENSE = ("def_solo", "def_assists", "def_tfl", "def_sacks", "def_ints", "def_int_yds", "def_deflections",
           "def_forced_fumbles", "def_fumble_recoveries", "def_tds", "def_safeties")
SPECIAL = ("kick_fgm", "kick_fga", "kick_xpm", "kick_xpa", "punt_punts", "punt_yards", "punt_net_yards",
           "punt_inside_20", "kr_returns", "kr_yards", "pr_returns", "pr_yards")
STATS = OFFENSE + DEFENSE + SPECIAL
MIRROR = {"receptions": "completions", "rec_yards": "pass_yards", "rec_tds": "pass_tds",
          "blk_sacks_allowed": "pass_sacks"}
BALANCE = {"def_ints": "interceptions", "def_sacks": "pass_sacks"}   # league-wide each season: made = thrown/taken
# drawn as a rate of their base (in this order), not on their own: independent draws gave 60-70 yards a punt, 40%
# field goals and 3,200-yard passers with 10 TDs. Passing and rushing yards stay on the points regression.
RATIOS = {"pass_attempts": "pass_yards", "completions": "pass_attempts", "interceptions": "pass_attempts",
          "pass_tds": "pass_yards", "pass_sacks": "pass_attempts", "rush_attempts": "rush_yards",
          "rush_tds": "rush_yards", "punt_yards": "punt_punts", "punt_net_yards": "punt_punts", "punt_inside_20": "punt_punts",
          "kr_yards": "kr_returns", "pr_yards": "pr_returns", "kick_fgm": "kick_fga", "kick_xpm": "kick_xpa",
          "def_int_yds": "def_ints"}
CALIBRATION = (2035, 2036)
RATE_CLAMP = 1.5          # a drawn rate stays within 1.5 SD of the 2035-2036 mean (a 2% interception rate was 27 INTs)


def fit(samples):
    """samples: [(pf, pa, {stat: value})]. Returns {stat: (a, b_pf, b_pa, residual sd)} by least squares (pure)."""
    n = len(samples)
    pf = [s[0] for s in samples]
    pa = [s[1] for s in samples]
    mpf, mpa = sum(pf) / n, sum(pa) / n
    sxx = sum((x - mpf) ** 2 for x in pf)
    syy = sum((y - mpa) ** 2 for y in pa)
    sxy = sum((x - mpf) * (y - mpa) for x, y in zip(pf, pa))
    det = sxx * syy - sxy ** 2
    out = {}
    for stat in STATS:
        v = [float(s[2].get(stat) or 0) for s in samples]
        mv = sum(v) / n
        sxv = sum((x - mpf) * (z - mv) for x, z in zip(pf, v))
        syv = sum((y - mpa) * (z - mv) for y, z in zip(pa, v))
        b1 = (syy * sxv - sxy * syv) / det
        b2 = (sxx * syv - sxy * sxv) / det
        a = mv - b1 * mpf - b2 * mpa
        resid = [z - (a + b1 * x + b2 * y) for x, y, z in zip(pf, pa, v)]
        sd = (sum(r * r for r in resid) / max(1, n - 3)) ** 0.5
        out[stat] = (a, b1, b2, sd)
    for num, den in RATIOS.items():                      # rate = num / den per team-season: mean and spread
        rates = [float(s[2].get(num) or 0) / float(s[2][den]) for s in samples if s[2].get(den)]
        mean = sum(rates) / len(rates) if rates else 0.0
        out[("rate", num)] = (mean, (sum((r - mean) ** 2 for r in rates) / max(1, len(rates) - 1)) ** 0.5)
    return out


def draw(model, arc, seed=1):
    """arc: {(team, season): (pf, pa)}. Returns {(team, season): {stat: int}} (pure)."""
    rng = random.Random(seed)
    out = {}
    for key in sorted(arc):
        pf, pa = arc[key]
        row = {}
        for stat in STATS:
            a, b1, b2, sd = model[stat]
            row[stat] = max(0, int(round(a + b1 * pf + b2 * pa + rng.gauss(0, sd))))
        row["completions"] = min(row["completions"], row["pass_attempts"])
        for num, den in RATIOS.items():
            if ("rate", num) in model:
                mean, sd = model[("rate", num)]
                rate = min(mean + RATE_CLAMP * sd, max(mean - RATE_CLAMP * sd, rng.gauss(mean, sd)))
                row[num] = max(0, int(round(row[den] * max(0.0, rate))))
        row["kick_fgm"] = min(row["kick_fgm"], row["kick_fga"])
        row["kick_xpm"] = min(row["kick_xpm"], row["kick_xpa"])
        for k, src in MIRROR.items():
            row[k] = row[src]
        out[key] = row
    by_season = defaultdict(list)
    for key in out:
        by_season[key[1]].append(key)
    for keys in by_season.values():
        for made, thrown in BALANCE.items():
            total_made = sum(out[k][made] for k in keys)
            total_thrown = sum(out[k][thrown] for k in keys)
            if total_made:
                scale = total_thrown / total_made
                for k in keys:
                    out[k][made] = int(round(out[k][made] * scale))
    return out


def calibrate(cur):
    """The 2035-2036 team-seasons as fit() samples."""
    cols = ", ".join(f"sum({s})" for s in STATS)
    cur.execute(f"SELECT season, team_id, {cols} FROM season_stat_lines WHERE season = ANY(%s) GROUP BY 1, 2",
                (list(CALIBRATION),))
    totals = {(r[1], r[0]): dict(zip(STATS, r[2:])) for r in cur.fetchall()}
    cur.execute("""SELECT season, team, sum(pf), sum(pa) FROM (
                       SELECT season, home_team_id team, home_score pf, away_score pa FROM games
                       WHERE game_type = 'REGULAR_SEASON' AND status = 'COMPLETED' AND season = ANY(%s)
                       UNION ALL
                       SELECT season, away_team_id, away_score, home_score FROM games
                       WHERE game_type = 'REGULAR_SEASON' AND status = 'COMPLETED' AND season = ANY(%s)) g
                   GROUP BY 1, 2""", (list(CALIBRATION), list(CALIBRATION)))
    return [(float(pf), float(pa), totals[(t, s)]) for s, t, pf, pa in cur.fetchall() if (t, s) in totals]


def arc(cur, first=2026, last=2034):
    cur.execute("SELECT team_id, season, points_for, points_against FROM team_season_results "
                "WHERE season BETWEEN %s AND %s", (first, last))
    return {(t, s): (pf, pa) for t, s, pf, pa in cur.fetchall()}
