"""
Career milestones since 2026 (docs/SEASON_CONTINUITY_PLAN.md phase 4; PROJECT_SPEC §3.3m).

A player's career in this league is every season_stat_lines row he has since 2026: the reconstructed 2026-2034 history
(source SYNTH, canon) and the captured seasons from 2035, the same basis as the player page's "Career since 2026"
(server/api.career_totals). Pre-2026 NFL careers are never added, so every total and milestone reads "since 2026".

For a season this finds, per stat (MILESTONES):
- crossed: players whose career total passed a round mark this season (before < mark <= now). `first` is "alone"
  when nobody had reached the mark before and he is the only one to reach it this season, "shared" when nobody had
  but several reached it this season (seasons have no dates, so there's no telling who got there first), else None;
- chasing: the next mark for players active this season, when it is within reach: while the regular season is being
  played, at most what his pace gives him over the games left; once it's over, at most his season total (in reach
  next season).

Read-only. compute() is pure; season_milestones() reads the database.
"""
import os

import psycopg

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
FIRST_SEASON = 2026
GAMES_PER_SEASON = 17

# stat column -> (label, round marks)
MILESTONES = {
    "pass_yards": ("passing yards", (10000, 20000, 30000, 40000, 50000, 60000, 70000, 80000)),
    "pass_tds": ("passing TDs", (100, 200, 300, 400, 500, 600)),
    "rush_yards": ("rushing yards", (5000, 7500, 10000, 12500, 15000, 17500)),
    "rush_tds": ("rushing TDs", (50, 75, 100, 125, 150)),
    "receptions": ("receptions", (500, 750, 1000, 1250)),
    "rec_yards": ("receiving yards", (5000, 7500, 10000, 12500, 15000, 17500, 20000)),
    "rec_tds": ("receiving TDs", (50, 75, 100, 125, 150)),
    "def_sacks": ("sacks", (50, 75, 100, 125, 150, 175, 200)),
    "def_ints": ("interceptions", (25, 40, 50, 60)),
    "def_tackles": ("tackles", (500, 750, 1000, 1250, 1500)),
    "kick_fgm": ("field goals made", (100, 200, 300, 400)),
}


def next_mark(marks, total):
    """The first mark above `total`, or None past the last one."""
    return next((m for m in marks if m > total), None)


def compute(rows, season, in_progress, games_left):
    """Pure. rows: [{player_id, name, team, position, stat, career_before, this_season, games}] (career_before: his
    total through season - 1; this_season: his line this season). Returns {crossed, chasing}."""
    crossed, chasing = [], []
    best_before, best_now = {}, {}
    for r in rows:
        best_before[r["stat"]] = max(best_before.get(r["stat"], 0), r["career_before"])
        best_now[r["stat"]] = max(best_now.get(r["stat"], 0), r["career_before"] + r["this_season"])
    for r in rows:
        label, marks = MILESTONES[r["stat"]]
        before, now = r["career_before"], r["career_before"] + r["this_season"]
        for m in marks:
            if before < m <= now:
                crossed.append({**_who(r), "stat": r["stat"], "label": label, "mark": m, "career": _num(now),
                                "first": best_before[r["stat"]] < m})
    for c in crossed:                                  # first to the mark: alone, or shared within the season
        if c["first"]:
            n = sum(1 for o in crossed if o["stat"] == c["stat"] and o["mark"] == c["mark"])
            c["first"] = "alone" if n == 1 else "shared"
        else:
            c["first"] = None
    for r in rows:
        label, marks = MILESTONES[r["stat"]]
        now = r["career_before"] + r["this_season"]
        if r["this_season"] <= 0:
            continue
        nxt = next_mark(marks, now)
        if nxt is None:
            continue
        need = nxt - now
        if in_progress:
            reach = (r["this_season"] / r["games"]) * games_left if r["games"] else 0
        else:
            reach = r["this_season"]
        if need <= reach:
            chasing.append({**_who(r), "stat": r["stat"], "label": label, "mark": nxt, "career": _num(now),
                            "needs": _num(need), "first": best_now[r["stat"]] < nxt})   # nobody's there yet
    crossed.sort(key=lambda c: (-c["mark"] / MILESTONES[c["stat"]][1][0], c["name"] or ""))
    chasing.sort(key=lambda c: (c["needs"] / c["mark"], c["name"] or ""))
    return {"crossed": crossed, "chasing": chasing}


def _who(r):
    return {"player_id": r["player_id"], "name": r["name"], "team": r["team"], "position": r["position"]}


def _num(v):
    f = float(v)
    return int(f) if f.is_integer() else round(f, 1)


def season_in_progress(cur, season):
    """(in progress, regular-season games left for the team with the most to play)."""
    cur.execute("""SELECT count(*) FILTER (WHERE status = 'COMPLETED'), count(*) FROM games
                   WHERE season = %s AND COALESCE(game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'""", (season,))
    done, total = cur.fetchone()
    if not total or done >= total:
        return False, 0
    cur.execute("""SELECT max(n) FROM (SELECT t, count(*) n FROM (
                       SELECT home_team_id t FROM games WHERE season = %s AND status <> 'COMPLETED'
                         AND COALESCE(game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
                       UNION ALL
                       SELECT away_team_id FROM games WHERE season = %s AND status <> 'COMPLETED'
                         AND COALESCE(game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON') x GROUP BY t) y""",
                (season, season))
    return True, cur.fetchone()[0] or 0


def season_milestones(cur, season):
    """Milestones for `season` (crossed this season, and the next ones in reach). {season, in_progress, crossed,
    chasing}."""
    in_progress, games_left = season_in_progress(cur, season)
    stats = list(MILESTONES)
    sums = ", ".join(f"COALESCE(sum({s}) FILTER (WHERE l.season < %s), 0)" for s in stats)
    this = ", ".join(f"COALESCE(sum({s}) FILTER (WHERE l.season = %s), 0)" for s in stats)
    cur.execute(f"""
        SELECT l.player_id, COALESCE(p.full_name, p.player_name),
               max(t.abbreviation) FILTER (WHERE l.season = %s), max(l.position) FILTER (WHERE l.season = %s),
               COALESCE(sum(l.games_played) FILTER (WHERE l.season = %s), 0), {sums}, {this}
        FROM season_stat_lines l JOIN players p ON p.id = l.player_id
        LEFT JOIN teams t ON t.id = l.team_id
        WHERE l.season BETWEEN %s AND %s
        GROUP BY l.player_id, p.full_name, p.player_name""",
                [season, season, season] + [season] * len(stats) + [season] * len(stats) + [FIRST_SEASON, season])
    rows = []
    for r in cur.fetchall():
        pid, name, team, pos, games = r[:5]
        before, now = r[5:5 + len(stats)], r[5 + len(stats):]
        for s, b, n in zip(stats, before, now):
            if b or n:
                rows.append({"player_id": pid, "name": name, "team": team, "position": pos, "stat": s,
                             "career_before": float(b), "this_season": float(n), "games": games or 0})
    out = compute(rows, season, in_progress, games_left)
    return {"season": season, "in_progress": in_progress, "since": FIRST_SEASON, **out}


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="Career milestones since 2026 for a season.")
    ap.add_argument("--season", type=int)
    args = ap.parse_args()
    with psycopg.connect(CONN_INFO) as conn:
        cur = conn.cursor()
        season = args.season
        if season is None:
            cur.execute("SELECT current_season FROM league_state LIMIT 1")
            season = cur.fetchone()[0]
        print(json.dumps(season_milestones(cur, season), indent=2, default=str))
