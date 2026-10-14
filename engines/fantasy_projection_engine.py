"""Weekly fantasy projections → fantasy_projections (PROJECT_SPEC §2.3, §3.4).

For a target week w, every rostered QB/HB/FB/WR/TE gets a full-PPR projection built only from games before
week w, so a projection for a past week is what the engine would have said before kickoff:

- Baseline: the player's PPR per team game to date (weeks the team played and the player had no stat line
  count as 0), shrunk toward a depth-chart prior by PRIOR_GAMES pseudo-games. The prior is the league's
  average PPR per team game for that depth slot (e.g. RB2 = a team's second-highest-OVR running back).
- Matchup: the opponent's PPR allowed per game to the position group, relative to the league average,
  shrunk by DEF_SHRINK_GAMES pseudo-games and dampened by MATCHUP_EXPONENT.
- projected = baseline × matchup factor.

Also written per player: avg_points (PPR per appearance to date), delta (projected − avg_points),
matchup_stars (1 = toughest … 5 = easiest; the opponent's allowed-per-game split into five even groups
across the 32 defenses), pos_rank (QB/RB/WR/TE, RB = HB + FB), ros_total_points (sum of projections for weeks
w..18 against each remaining opponent) with ros_rank, and adp_rank carried over from the season's week 1
preseason ADP rows (engines/fantasy_adp_engine.py). Players on bye get a row with projected_points 0 and no
pos_rank (so they still appear in ROS rankings but not in that week's rankings).

Backtest on 2035 (weeks 2–17, starters: QB1/RB1-2/WR1-3/TE1): mean absolute error ≈ 3.3 PPR, correlation
≈ 0.79 with actual points. Most of that comes from the per-game average; production in this league is very
stable week to week, and the matchup factor and recency weighting add little (recency was tested and dropped).

CLI:
    python3 -m engines.fantasy_projection_engine                       # current week from league_state
    python3 -m engines.fantasy_projection_engine --season 2035 --week 18
    python3 -m engines.fantasy_projection_engine --season 2035 --all-weeks   # backfill weeks 2–18
    python3 -m engines.fantasy_projection_engine --season 2035 --backtest    # accuracy only, no writes
"""

import argparse
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import psycopg

from db.db_handler import DatabaseHandler

PRIOR_GAMES = 0.25
DEF_SHRINK_GAMES = 16
MATCHUP_EXPONENT = 0.5
SEASON_WEEKS = 18
POS_GROUP = {"QB": "QB", "HB": "RB", "FB": "RB", "WR": "WR", "TE": "TE"}
GROUPS = ("QB", "RB", "WR", "TE")
# Depth slots below these share one prior (e.g. every WR4+ is "WR4").
PRIOR_SLOTS = {"QB": 2, "RB": 3, "WR": 4, "TE": 2}
# Backtest population: fantasy starters by depth chart.
STARTER_SLOTS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1}


@dataclass
class SeasonData:
    season: int
    opponent: dict                    # (team_id, week) -> opponent team_id (scheduled or completed)
    completed_weeks: dict             # team_id -> set of weeks with a completed game
    roster: dict                      # player_id -> {name, team_id, position, group, depth}
    points: dict = field(default_factory=lambda: defaultdict(dict))   # player_id -> {week: PPR}
    allowed: dict = field(default_factory=lambda: defaultdict(float)) # (def_team, group, week) -> PPR allowed


def load_season(cur, season: int) -> SeasonData:
    cur.execute("SELECT week, home_team_id, away_team_id, status FROM games WHERE season = %s", (season,))
    opponent, completed = {}, defaultdict(set)
    for week, home, away, status in cur.fetchall():
        opponent[(home, week)] = away
        opponent[(away, week)] = home
        if status == "COMPLETED":
            completed[home].add(week)
            completed[away].add(week)

    cur.execute("""
        SELECT p.id, COALESCE(p.full_name, p.player_name), ps.team_id, ps.position, ps.overall_rating
        FROM player_seasons ps
        JOIN players p ON p.id = ps.player_id
        WHERE ps.season = %s AND ps.position = ANY(%s) AND ps.team_id IS NOT NULL
    """, (season, list(POS_GROUP)))
    roster = {}
    by_team_group = defaultdict(list)
    for pid, name, team_id, pos, ovr in cur.fetchall():
        roster[pid] = {"name": name, "team_id": team_id, "position": pos, "group": POS_GROUP[pos]}
        by_team_group[(team_id, POS_GROUP[pos])].append((-(ovr or 0), pid))
    for players in by_team_group.values():
        for depth, (_, pid) in enumerate(sorted(players), start=1):
            roster[pid]["depth"] = depth

    data = SeasonData(season, opponent, completed, roster)

    # each line's player is its stored player_id (abbreviated names collide across players)
    cur.execute("""
        SELECT st.player_id, st.team_id, st.position, g.week, st.fantasy_points::float
        FROM player_stats st
        JOIN games g ON g.id = st.game_id
        WHERE g.season = %s AND g.status = 'COMPLETED' AND st.position = ANY(%s)
    """, (season, list(POS_GROUP)))
    for pid, team_id, pos, week, pts in cur.fetchall():
        pts = pts or 0.0
        opp = opponent.get((team_id, week))
        if opp is not None:
            data.allowed[(opp, POS_GROUP[pos], week)] += pts
        if pid in roster and roster[pid]["team_id"] == team_id:
            data.points[pid][week] = data.points[pid].get(week, 0.0) + pts
    return data


def _ntile5_ascending(values: dict) -> dict:
    """Postgres NTILE(5) over values sorted ascending: bucket sizes differ by at most one, larger first."""
    ordered = sorted(values, key=lambda k: (values[k], k))
    n = len(ordered)
    base, extra = divmod(n, 5)
    out, i = {}, 0
    for bucket in range(5):
        size = base + (1 if bucket < extra else 0)
        for key in ordered[i:i + size]:
            out[key] = bucket + 1
        i += size
    return out


def project_week(data: SeasonData, week: int) -> list:
    """Projection rows for `week`, using only completed games before it. Empty if there is no earlier data."""
    prior_weeks = {t: {w for w in wks if w < week} for t, wks in data.completed_weeks.items()}
    if not any(prior_weeks.values()):
        return []

    # Depth-slot priors: league PPR per team game for each (group, slot).
    slot_sum, slot_n = defaultdict(float), defaultdict(int)
    for pid, p in data.roster.items():
        slot = (p["group"], min(p["depth"], PRIOR_SLOTS[p["group"]]))
        for w in prior_weeks.get(p["team_id"], ()):
            slot_sum[slot] += data.points[pid].get(w, 0.0)
            slot_n[slot] += 1
    slot_prior = {k: slot_sum[k] / slot_n[k] for k in slot_n}

    # Defense vs. position: PPR allowed per game, league average, shrunk matchup factor, and stars.
    league_allowed = {}
    allowed_pg = {g: {} for g in GROUPS}
    factor = {}
    for g in GROUPS:
        total = games = 0
        for team, wks in prior_weeks.items():
            team_total = sum(data.allowed.get((team, g, w), 0.0) for w in wks)
            total += team_total
            games += len(wks)
            if wks:
                allowed_pg[g][team] = team_total / len(wks)
        league_allowed[g] = total / games if games else 0.0
        for team, wks in prior_weeks.items():
            lg = league_allowed[g]
            if not lg:
                factor[(team, g)] = 1.0
                continue
            team_total = sum(data.allowed.get((team, g, w), 0.0) for w in wks)
            shrunk = (team_total + DEF_SHRINK_GAMES * lg) / ((len(wks) + DEF_SHRINK_GAMES) * lg)
            factor[(team, g)] = shrunk ** MATCHUP_EXPONENT
    stars = {g: _ntile5_ascending(allowed_pg[g]) for g in GROUPS}

    rows = []
    for pid, p in data.roster.items():
        team, g = p["team_id"], p["group"]
        played = prior_weeks.get(team, set())
        player_pts = data.points[pid]
        prior = slot_prior.get((g, min(p["depth"], PRIOR_SLOTS[g])), 0.0)
        total = sum(player_pts.get(w, 0.0) for w in played)
        baseline = (total + PRIOR_GAMES * prior) / (len(played) + PRIOR_GAMES)

        appearances = [player_pts[w] for w in played if w in player_pts]
        avg_points = sum(appearances) / len(appearances) if appearances else None

        opp = data.opponent.get((team, week))
        projected = baseline * factor.get((opp, g), 1.0) if opp is not None else 0.0
        ros_total = sum(
            baseline * factor.get((data.opponent[(team, w)], g), 1.0)
            for w in range(week, SEASON_WEEKS + 1) if (team, w) in data.opponent
        )
        rows.append({
            "player_id": pid,
            "player_name": p["name"],
            "position": p["position"],
            "group": g,
            "team_id": team,
            "on_bye": opp is None,
            "projected_points": round(projected, 2),
            "avg_points": round(avg_points, 2) if avg_points is not None else None,
            "delta": round(projected - avg_points, 2) if avg_points is not None and opp is not None else None,
            "matchup_stars": stars[g].get(opp, 3) if opp is not None else None,
            "ros_total_points": round(ros_total, 2),
        })

    for g in GROUPS:
        group_rows = [r for r in rows if r["group"] == g]
        playing = sorted((r for r in group_rows if not r["on_bye"]),
                         key=lambda r: (-r["projected_points"], -r["ros_total_points"], r["player_id"]))
        for i, r in enumerate(playing, start=1):
            r["pos_rank"] = f"{g}{i}"
        for i, r in enumerate(sorted(group_rows, key=lambda r: (-r["ros_total_points"], r["player_id"])), start=1):
            r["ros_rank"] = f"{g}{i}"
    return rows


def load_preseason_adp(cur, season: int) -> dict:
    """player_id -> preseason adp_rank from the season's week 1 rows (name + team where player_id is missing)."""
    cur.execute("""
        SELECT COALESCE(fp.player_id, p.id), fp.adp_rank
        FROM fantasy_projections fp
        LEFT JOIN players p ON fp.player_id IS NULL AND p.player_name = fp.player_name
        LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = fp.season AND ps.team_id = fp.team_id
        WHERE fp.season = %s AND fp.week = 1 AND fp.adp_rank IS NOT NULL
          AND (fp.player_id IS NOT NULL OR ps.id IS NOT NULL)
    """, (season,))
    adp = {}
    for pid, rank in cur.fetchall():
        if pid is not None and (pid not in adp or rank < adp[pid]):
            adp[pid] = rank
    return adp


def store_week(conn, season: int, week: int, rows: list, adp: dict) -> None:
    """Replace the week's projection rows. Rows for players no longer projected are removed (except week 1,
    which also holds the preseason ADP board); adp_rank is kept where we have none to write."""
    with conn.cursor() as cur:
        if week != 1:
            cur.execute("""
                DELETE FROM fantasy_projections
                WHERE season = %s AND week = %s AND (player_id IS NULL OR NOT (player_id = ANY(%s)))
            """, (season, week, [r["player_id"] for r in rows]))
        cur.executemany("""
            INSERT INTO fantasy_projections (
                season, week, player_id, player_name, position, team_id, projected_points, avg_points,
                matchup_stars, pos_rank, adp_rank, ros_rank, ros_total_points, delta
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (season, week, player_id) DO UPDATE SET
                player_name = EXCLUDED.player_name,
                position = EXCLUDED.position,
                team_id = EXCLUDED.team_id,
                projected_points = EXCLUDED.projected_points,
                avg_points = EXCLUDED.avg_points,
                matchup_stars = EXCLUDED.matchup_stars,
                pos_rank = EXCLUDED.pos_rank,
                adp_rank = COALESCE(EXCLUDED.adp_rank, fantasy_projections.adp_rank),
                ros_rank = EXCLUDED.ros_rank,
                ros_total_points = EXCLUDED.ros_total_points,
                delta = EXCLUDED.delta
        """, [(
            season, week, r["player_id"], r["player_name"], r["position"], r["team_id"], r["projected_points"],
            r["avg_points"], r["matchup_stars"], r.get("pos_rank"), adp.get(r["player_id"]), r["ros_rank"],
            r["ros_total_points"], r["delta"],
        ) for r in rows])
    conn.commit()


def project_weeks(season: int, weeks: list, conn_info: Optional[str] = None) -> dict:
    """Project and store each week; returns {week: rows written}."""
    written = {}
    with psycopg.connect(conn_info or DatabaseHandler().db_conn_info) as conn:
        with conn.cursor() as cur:
            data = load_season(cur, season)
            adp = load_preseason_adp(cur, season)
        for week in weeks:
            rows = project_week(data, week)
            if not rows:
                print(f"   Week {week}: no completed games before it, skipped.")
                continue
            store_week(conn, season, week, rows, adp)
            written[week] = len(rows)
            print(f"   Week {week}: {len(rows)} players projected.")
    return written


def project_upcoming_week(season: int, conn_info: Optional[str] = None) -> dict:
    """Project the first week that still has scheduled games (the pipeline's post-backfill step)."""
    with psycopg.connect(conn_info or DatabaseHandler().db_conn_info) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MIN(week) FROM games WHERE season = %s AND status = 'SCHEDULED'", (season,))
            week = cur.fetchone()[0]
    if week is None:
        print(f"   No scheduled games left in {season}; no fantasy projections to make.")
        return {}
    return project_weeks(season, [week], conn_info)


def backtest(season: int, conn_info: Optional[str] = None) -> None:
    with psycopg.connect(conn_info or DatabaseHandler().db_conn_info) as conn:
        with conn.cursor() as cur:
            data = load_season(cur, season)
    pairs = []
    for week in range(2, SEASON_WEEKS + 1):
        for r in project_week(data, week):
            p = data.roster[r["player_id"]]
            if r["on_bye"] or week not in data.completed_weeks.get(p["team_id"], ()):
                continue
            if p["depth"] > STARTER_SLOTS[p["group"]]:
                continue
            pairs.append((r["projected_points"], data.points[r["player_id"]].get(week, 0.0)))
    if not pairs:
        print("No completed weeks to backtest.")
        return
    n = len(pairs)
    mae = sum(abs(p - a) for p, a in pairs) / n
    mp = sum(p for p, _ in pairs) / n
    ma = sum(a for _, a in pairs) / n
    cov = sum((p - mp) * (a - ma) for p, a in pairs)
    corr = cov / math.sqrt(sum((p - mp) ** 2 for p, _ in pairs) * sum((a - ma) ** 2 for _, a in pairs))
    naive = sum(abs(ma - a) for _, a in pairs) / n
    print(f"Backtest {season}: {n} starter-weeks · MAE {mae:.2f} PPR (league-average guess {naive:.2f}) · "
          f"correlation {corr:.3f}")


def main():
    parser = argparse.ArgumentParser(description="Weekly fantasy projections")
    parser.add_argument("--season", type=int)
    parser.add_argument("--week", type=int, help="Week to project (default: league_state current week)")
    parser.add_argument("--all-weeks", action="store_true", help=f"Project weeks 2–{SEASON_WEEKS}")
    parser.add_argument("--backtest", action="store_true", help="Score accuracy on completed weeks; no writes")
    args = parser.parse_args()

    season, week = args.season, args.week
    if season is None or (week is None and not args.all_weeks):
        with psycopg.connect(DatabaseHandler().db_conn_info) as conn:
            row = conn.execute("SELECT current_season, current_week FROM league_state LIMIT 1").fetchone()
        season = season or row[0]
        week = week or row[1]

    if args.backtest:
        backtest(season)
        return
    weeks = list(range(2, SEASON_WEEKS + 1)) if args.all_weeks else [week]
    print(f"🏈 Projecting fantasy points for {season}, week(s) {weeks[0]}–{weeks[-1]}...")
    project_weeks(season, weeks)


if __name__ == "__main__":
    main()
