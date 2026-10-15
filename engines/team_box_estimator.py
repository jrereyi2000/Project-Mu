"""
Per-game team conversions, red zone and penalties where no box score was captured (PROJECT_SPEC §2.6).

  python3 -m engines.team_box_estimator [--season N] [--dry-run]

Madden's Team Stats screens give season totals (team_seasons: third/fourth-down and two-point conversions
and attempts, red zone on offense and defense, penalties and penalty yards); a captured game box score gives
some of them per game (conversions, red-zone TDs/FGs/percentage, penalty yards; no attempts or penalty
count). Every team_stats cell that is still NULL gets the team's season remainder (season total minus the
cells already stored), split across the games that need it. In a normal weekly run that is one new game per
team, so the remainder is exactly that week's change; the first run spreads a whole backlog by score:

- Red zone: each game's score is split into touchdowns and field goals (closest to the team's season TD
  share); red-zone TDs and FGs can't exceed them. Trips are balanced against both the offense's season
  remainder and the opponent's red-zone defense remainder (iterative proportional fitting), then rounded to
  match the offense exactly.
- Third downs: conversions follow points scored, failed attempts follow the opposite; both scale with the
  game's combined score (pace).
- Fourth downs: attempts rise with the deficit (trailing teams go for it).
- Two-point tries: follow touchdowns and the deficit; never more than the game's TDs.
- Penalties: count follows the captured penalty yards when known, else the game's combined score (more
  snaps, more flags); yards follow the count.

Filled columns are listed in team_stats.estimated_fields. A cell that was the only one its team needed for that
column is exact, not an estimate (the season total's change since the cells already stored), and is also
listed in team_stats.exact_fields; a cell from a multi-game split (the first run's backlog) is not. Stored cells (captured or estimated earlier) are
never changed, so earlier weeks don't shift when a new week is added. Regular-season games only, and only
weeks the latest loaded weekly capture covers (its season totals include them).
"""
import argparse
import os

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

SEASON_FIELDS = ("third_down_conv", "third_down_att", "fourth_down_conv", "fourth_down_att",
                 "two_point_conv", "two_point_att", "red_zone_att", "red_zone_td", "red_zone_fg",
                 "red_zone_def_att", "red_zone_def_td", "red_zone_def_fg", "penalties", "penalty_yards")
GAME_FIELDS = ("third_down_conversions", "third_down_attempts", "fourth_down_conversions", "fourth_down_attempts",
               "two_point_conversions", "two_point_attempts", "red_zone_attempts", "red_zone_tds", "red_zone_fgs",
               "red_zone_pct", "penalties", "penalty_yards")
# game column -> season total it must add up to
TOTAL_OF = {"third_down_conversions": "third_down_conv", "third_down_attempts": "third_down_att",
            "fourth_down_conversions": "fourth_down_conv", "fourth_down_attempts": "fourth_down_att",
            "two_point_conversions": "two_point_conv", "two_point_attempts": "two_point_att",
            "red_zone_attempts": "red_zone_att", "red_zone_tds": "red_zone_td", "red_zone_fgs": "red_zone_fg",
            "penalties": "penalties", "penalty_yards": "penalty_yards"}


def apportion(total, weights, caps=None):
    """Split integer `total` across cells in proportion to `weights` (largest remainder), keeping each cell
    at or under its cap when the caps allow; what the caps can't hold is spread ignoring them.
    Returns (counts, spilled)."""
    n = len(weights)
    total = max(0, int(total))
    if n == 0:
        return [], total
    caps = list(caps) if caps is not None else [None] * n
    out = [0] * n
    remaining = total
    active = [i for i in range(n) if caps[i] is None or caps[i] > 0]
    while remaining > 0 and active:
        w = {i: max(float(weights[i]), 0.0) for i in active}
        ws = sum(w.values())
        if ws <= 0:
            w = {i: 1.0 for i in active}
            ws = float(len(active))
        shares = {i: remaining * w[i] / ws for i in active}
        capped = [i for i in active if caps[i] is not None and out[i] + shares[i] > caps[i]]
        if capped:
            for i in capped:
                remaining -= caps[i] - out[i]
                out[i] = caps[i]
            active = [i for i in active if i not in capped]
            continue
        floors = {i: int(shares[i]) for i in active}
        for i in active:
            out[i] += floors[i]
        left = remaining - sum(floors.values())
        for i in sorted(active, key=lambda i: (-(shares[i] - floors[i]), -w[i], i))[:left]:
            out[i] += 1
        remaining = 0
    spilled = remaining
    if remaining > 0:
        extra, _ = apportion(remaining, [max(float(x), 0.0) for x in weights])
        out = [a + b for a, b in zip(out, extra)]
    return out, spilled


def score_split(points, td_share):
    """(touchdowns, field goals) that best explain `points`: about one extra point per TD, ratio closest to
    the team's TD share. 2 points with no TD is a safety."""
    points = max(0, int(points or 0))
    best, best_cost = (0, 0), None
    for td in range(points // 6 + 1):
        for fg in range((points - 6 * td) // 3 + 1):
            extra = points - 6 * td - 3 * fg
            cost = abs(extra - td)
            if extra > 2 * td:
                cost += 2 * (extra - 2 * td)          # needs safeties
            if td + fg:
                cost += 3 * abs(td / (td + fg) - td_share)
            if best_cost is None or cost < best_cost - 1e-9:
                best, best_cost = (td, fg), cost
    return best


def ipf(seeds, row_of, col_of, row_targets, col_targets, iterations=60):
    """Scale cell seeds so each row (offense team) and column (defense team) sums to its target."""
    vals = {c: max(s, 1e-6) for c, s in seeds.items()}
    for _ in range(iterations):
        for key_of, targets in ((row_of, row_targets), (col_of, col_targets)):
            sums = {}
            for c, v in vals.items():
                sums[key_of[c]] = sums.get(key_of[c], 0.0) + v
            for c in vals:
                k = key_of[c]
                if k in targets and sums.get(k, 0) > 0:
                    vals[c] *= max(targets[k], 0) / sums[k]
    return vals


def load(cur, season):
    """Season totals and the completed regular-season rows the latest weekly capture covers."""
    cur.execute("SELECT MAX(week) FROM captures WHERE mode = 'weekly' AND status = 'loaded' AND season = %s",
                (season,))
    covered = (cur.fetchone() or [None])[0]
    if not covered:
        return None, {}, []
    cur.execute(f"SELECT team_id, {', '.join(SEASON_FIELDS)} FROM team_seasons WHERE season = %s", (season,))
    totals = {r[0]: dict(zip(SEASON_FIELDS, r[1:])) for r in cur.fetchall()}
    cur.execute(f"""
        SELECT ts.id, g.id, g.week, t.id, CASE WHEN g.home_team_id = t.id THEN g.away_team_id ELSE g.home_team_id END,
               CASE WHEN g.home_team_id = t.id THEN g.home_score ELSE g.away_score END,
               CASE WHEN g.home_team_id = t.id THEN g.away_score ELSE g.home_score END,
               ts.estimated_fields, ts.exact_fields, {', '.join('ts.' + f for f in GAME_FIELDS)}
        FROM team_stats ts
        JOIN games g ON g.id = ts.game_id
        JOIN teams t ON t.name = ts.team_name
        WHERE g.season = %s AND g.status = 'COMPLETED' AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
          AND g.week <= %s
        ORDER BY g.week, g.id, t.id
    """, (season, covered))
    rows = []
    for r in cur.fetchall():
        row = {"id": r[0], "game_id": r[1], "week": r[2], "team": r[3], "opp": r[4],
               "pts": r[5] or 0, "opp_pts": r[6] or 0, "estimated": list(r[7] or []),
               "exact": list(r[8] or [])}
        row.update(dict(zip(GAME_FIELDS, r[9:])))
        rows.append(row)
    return covered, totals, rows


def estimate(rows, totals):
    """Fill the NULL cells of `rows` in place. Returns a report of warnings."""
    warnings = []
    if not rows:
        return warnings
    by_team = {}
    for r in rows:
        by_team.setdefault(r["team"], []).append(r)
    league_combined = sum(r["pts"] + r["opp_pts"] for r in rows) / len(rows) or 1.0
    for r in rows:
        r["pace"] = 0.85 + 0.15 * (r["pts"] + r["opp_pts"]) / league_combined
        r["plays"] = 0.5 + 0.5 * (r["pts"] + r["opp_pts"]) / league_combined   # penalties: more snaps, more flags
    team_avg_pts = {t: (sum(r["pts"] for r in rs) / len(rs)) or 1.0 for t, rs in by_team.items()}
    for t, rs in by_team.items():
        tot = totals.get(t, {})
        rz_td, rz_fg = tot.get("red_zone_td") or 0, tot.get("red_zone_fg") or 0
        share = min(0.85, max(0.4, rz_td / (rz_td + rz_fg))) if rz_td + rz_fg else 0.6
        for r in rs:
            r["td_g"], r["fg_g"] = score_split(r["pts"], share)
            r["rel"] = r["pts"] / team_avg_pts[t]

    clamped = set()   # (team, column) whose stored cells exceed the season total: the remainder isn't exact

    def fill(r, field, value, cells=()):
        """Store an estimated cell; it is exact when it was the team's only missing cell for the column."""
        r[field] = int(value)
        if field not in r["estimated"]:
            r["estimated"].append(field)
        if len(cells) == 1 and (r["team"], field) not in clamped and field not in r["exact"]:
            r["exact"].append(field)

    def remainder(t, rs, total_key, field):
        total = totals.get(t, {}).get(total_key)
        if total is None:
            return None
        rem = total - sum(r[field] for r in rs if r[field] is not None)
        if rem < 0:
            warnings.append(f"team {t}: stored {field} exceed the season total by {-rem}")
            clamped.add((t, field))
        return max(0, rem)

    def conv_and_att(t, rs, conv, att, conv_key, att_key, conv_w, fail_w, cap=None):
        """Conversions first, then attempts = conversions + failures (attempts never below conversions)."""
        cells = [r for r in rs if r[conv] is None]
        rem = remainder(t, rs, conv_key, conv)
        if cells and rem is not None:
            caps = [cap(r) for r in cells] if cap else None
            vals, spilled = apportion(rem, [conv_w(r) for r in cells], caps)
            if spilled:
                warnings.append(f"team {t}: {spilled} {conv} placed over the per-game cap")
            for r, v in zip(cells, vals):
                fill(r, conv, v, cells)
        cells = [r for r in rs if r[att] is None and r[conv] is not None]
        rem = remainder(t, rs, att_key, att)
        if cells and rem is not None:
            fails_total = rem - sum(r[conv] for r in cells)
            if fails_total < 0:
                warnings.append(f"team {t}: {att} remainder is {-fails_total} below the conversions it must cover")
            caps = [max(0, cap(r) - r[conv]) for r in cells] if cap else None
            vals, _ = apportion(max(0, fails_total), [fail_w(r) for r in cells], caps)
            for r, v in zip(cells, vals):
                fill(r, att, r[conv] + v, cells)

    for t, rs in by_team.items():
        # Third downs: scoring offenses convert, stalled ones fail.
        conv_and_att(t, rs, "third_down_conversions", "third_down_attempts", "third_down_conv", "third_down_att",
                     lambda r: r["pace"] * (0.6 + 0.4 * r["rel"]),
                     lambda r: r["pace"] * max(0.2, 1.4 - 0.4 * r["rel"]))
        # Fourth downs: trailing teams go for it.
        fourth_w = lambda r: 0.3 + max(0, r["opp_pts"] - r["pts"]) / 7
        conv_and_att(t, rs, "fourth_down_conversions", "fourth_down_attempts", "fourth_down_conv", "fourth_down_att",
                     fourth_w, fourth_w)
        # Two-point tries: only after touchdowns, more when chasing.
        two_w = lambda r: r["td_g"] * (0.15 + max(0, r["opp_pts"] - r["pts"]) / 14)
        conv_and_att(t, rs, "two_point_conversions", "two_point_attempts", "two_point_conv", "two_point_att",
                     two_w, two_w, cap=lambda r: r["td_g"])
        # Penalties: count from the captured yards when known, else pace; yards follow the count.
        tot = totals.get(t, {})
        ypp = (tot.get("penalty_yards") or 0) / tot["penalties"] if tot.get("penalties") else 8.0
        cells = [r for r in rs if r["penalties"] is None]
        rem = remainder(t, rs, "penalties", "penalties")
        if cells and rem is not None:
            vals, _ = apportion(rem, [r["penalty_yards"] / ypp if r["penalty_yards"] is not None else 6 * r["plays"]
                                      for r in cells])
            for r, v in zip(cells, vals):
                fill(r, "penalties", v, cells)
        cells = [r for r in rs if r["penalty_yards"] is None and r["penalties"] is not None]
        rem = remainder(t, rs, "penalty_yards", "penalty_yards")
        if cells and rem is not None:
            vals, _ = apportion(rem, [r["penalties"] for r in cells])
            for r, v in zip(cells, vals):
                fill(r, "penalty_yards", v, cells)

    # Red zone, balanced across offense and defense: TDs, FGs and empty trips separately.
    for key, off_key, def_key, field in (("td", "red_zone_td", "red_zone_def_td", "red_zone_tds"),
                                         ("fg", "red_zone_fg", "red_zone_def_fg", "red_zone_fgs")):
        cells = [r for r in rows if r[field] is None]
        if not cells:
            continue
        row_t = {t: remainder(t, rs, off_key, field) for t, rs in by_team.items()}
        col_t = {}
        for d in by_team:
            total = totals.get(d, {}).get(def_key)
            if total is not None:
                col_t[d] = max(0, total - sum(r[field] for r in rows if r["opp"] == d and r[field] is not None))
        seeds = {r["id"]: r[f"{key}_g"] + 0.05 for r in cells}
        fitted = ipf(seeds, {r["id"]: r["team"] for r in cells}, {r["id"]: r["opp"] for r in cells},
                     {t: v for t, v in row_t.items() if v is not None}, col_t)
        for t, rs in by_team.items():
            tc = [r for r in rs if r[field] is None]
            if not tc or row_t.get(t) is None:
                continue
            vals, spilled = apportion(row_t[t], [fitted[r["id"]] for r in tc], [r[f"{key}_g"] for r in tc])
            if spilled:
                warnings.append(f"team {t}: {spilled} {field} more than the scores explain")
            for r, v in zip(tc, vals):
                fill(r, field, v, tc)
    cells = [r for r in rows if r["red_zone_attempts"] is None and r["red_zone_tds"] is not None
             and r["red_zone_fgs"] is not None]
    if cells:
        def empty_rem(t, keyed_rows, att_k):
            """Trips still to place that ended without a score: the season's trips not yet stored, minus the
            TDs and FGs of the games whose trips are missing."""
            total = totals.get(t, {}).get(att_k)
            if total is None:
                return None
            stored = sum(r["red_zone_attempts"] for r in keyed_rows if r["red_zone_attempts"] is not None)
            scored = sum(r["red_zone_tds"] + r["red_zone_fgs"] for r in keyed_rows if r["red_zone_attempts"] is None)
            return max(0, total - stored - scored)
        row_t = {t: empty_rem(t, rs, "red_zone_att") for t, rs in by_team.items()}
        col_t = {}
        for d in by_team:
            vs = [r for r in rows if r["opp"] == d]
            v = empty_rem(d, vs, "red_zone_def_att")
            if v is not None:
                col_t[d] = v
        seeds = {r["id"]: r["pace"] * (1.2 - 0.4 * min(r["rel"], 2)) for r in cells}
        fitted = ipf(seeds, {r["id"]: r["team"] for r in cells}, {r["id"]: r["opp"] for r in cells},
                     {t: v for t, v in row_t.items() if v is not None}, col_t)
        for t, rs in by_team.items():
            tc = [r for r in rs if r in cells]
            if not tc or row_t.get(t) is None:
                continue
            vals, _ = apportion(row_t[t], [fitted[r["id"]] for r in tc])
            for r, v in zip(tc, vals):
                fill(r, "red_zone_attempts", r["red_zone_tds"] + r["red_zone_fgs"] + v, tc)
    for r in rows:
        if r["red_zone_pct"] is None and r["red_zone_attempts"] is not None and r["red_zone_tds"] is not None:
            r["red_zone_pct"] = round(100.0 * r["red_zone_tds"] / r["red_zone_attempts"], 1) if r["red_zone_attempts"] else 0.0
            r["estimated"].append("red_zone_pct")
            known = lambda f: f not in r["estimated"] or f in r["exact"]
            if known("red_zone_tds") and known("red_zone_attempts"):
                r["exact"].append("red_zone_pct")
    return warnings


def check(rows, totals):
    """Per-team differences between the stored rows and the season totals (offense exact, defense close)."""
    diffs, def_off = {}, 0
    for t, tot in totals.items():
        rs = [r for r in rows if r["team"] == t]
        if not rs:
            continue
        for field, key in TOTAL_OF.items():
            if tot.get(key) is None or any(r[field] is None for r in rs):
                continue
            d = sum(r[field] for r in rs) - tot[key]
            if d:
                diffs[(t, field)] = d
        vs = [r for r in rows if r["opp"] == t]
        for field, key in (("red_zone_attempts", "red_zone_def_att"), ("red_zone_tds", "red_zone_def_td"),
                           ("red_zone_fgs", "red_zone_def_fg")):
            if tot.get(key) is not None and vs and all(r[field] is not None for r in vs):
                def_off += abs(sum(r[field] for r in vs) - tot[key])
    return diffs, def_off


def run(season=None, dry_run=False, conn_info=DATABASE_URL, quiet=False):
    with psycopg.connect(conn_info) as conn:
        with conn.cursor() as cur:
            if season is None:
                cur.execute("SELECT current_season FROM league_state WHERE id = 1")
                season = cur.fetchone()[0]
            covered, totals, rows = load(cur, season)
            if not covered:
                if not quiet:
                    print(f"[team box] no loaded weekly capture for {season}; nothing estimated")
                return {"season": season, "filled_rows": 0}
            before = {r["id"]: list(r["estimated"]) for r in rows}
            warnings = estimate(rows, totals)
            changed = [r for r in rows if r["estimated"] != before[r["id"]]]
            for r in changed:
                cur.execute(f"UPDATE team_stats SET {', '.join(f'{f} = %s' for f in GAME_FIELDS)}, "
                            f"estimated_fields = %s, exact_fields = %s WHERE id = %s",
                            [r[f] for f in GAME_FIELDS] + [r["estimated"], r["exact"], r["id"]])
            diffs, def_off = check(rows, totals)
            report = {"season": season, "through_week": covered, "rows": len(rows), "filled_rows": len(changed),
                      "warnings": warnings, "offense_mismatches": diffs, "red_zone_defense_off_by": def_off}
            if dry_run:
                conn.rollback()
            else:
                conn.commit()
            if not quiet:
                print(f"[team box] season {season} through week {covered}: filled {len(changed)} of {len(rows)} rows"
                      f"{' (dry run, rolled back)' if dry_run else ''}; offense totals off in {len(diffs)} places; "
                      f"red-zone defense totals off by {def_off} in all")
                for w in warnings:
                    print("  ⚠️", w)
            report["rows_data"] = rows
            return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, help="default: league_state.current_season")
    ap.add_argument("--dry-run", action="store_true", help="estimate and report, then roll back")
    a = ap.parse_args()
    run(a.season, a.dry_run)
