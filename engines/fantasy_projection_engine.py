"""Weekly fantasy projections → fantasy_projections (PROJECT_SPEC §2.3, §3.4).

For a target week w, every rostered QB/HB/FB/WR/TE, kicker (K) and team defense (DEF) gets a full-PPR projection
built only from games before week w, so a projection for a past week is what the engine would have said before
kickoff. Skill players score from player_stats.fantasy_points; kickers from game_stat_kicking (3 per FG, +1 at
40–49 yds, +2 at 50+, 1 per XP); DEF from game_stat_defense summed per team (sack 1, INT/FR/safety/block 2, TD 6)
plus a points-allowed tier (0 → 10, ≤6 → 7, ≤13 → 4, ≤20 → 1, ≤27 → 0, ≤34 → −1, else −4). The same SQL
(KICKING_POINTS_SQL / DEF_POINTS_SQL) scores Napper matchups in server/napper_api.py.

- Baseline: the player's PPR per team game to date (weeks the team played and the player had no stat line
  count as 0; a traded player also keeps every week he has a line for his old team), shrunk toward a
  depth-chart prior by PRIOR_GAMES pseudo-games. The prior is the league's
  average PPR per team game for that depth slot (e.g. RB2 = a team's second-highest-OVR running back).
- Matchup: the opponent's PPR allowed per game to the position group, relative to the league average,
  shrunk by DEF_SHRINK_GAMES pseudo-games and dampened by MATCHUP_EXPONENT.
- projected = baseline × matchup factor.

Also written per player: avg_points (PPR per appearance to date), delta (projected − avg_points),
matchup_stars (1 = toughest … 5 = easiest; the opponent's allowed-per-game split into five even groups
across the 32 defenses), pos_rank (QB/RB/WR/TE/K/DEF, RB = HB + FB), ros_total_points (sum of projections for weeks
w..18 against each remaining opponent) with ros_rank, and adp_rank carried over from the season's week 1
preseason ADP rows (engines/fantasy_adp_engine.py). Players on bye get a row with projected_points 0 and no
pos_rank (so they still appear in ROS rankings but not in that week's rankings). Injured players (on IR, or
weeks_remaining > 0 in the injuries table) project 0 with no pos_rank, and their ROS total skips the weeks out.
Released players (player_seasons.released_week set, League Transactions) have no team game: they project 0
like a bye, hold no depth slot, and have no ROS total.

Week 1 (no completed games yet) is projected from the prior season: each player's prior-season PPR per game
shrunk toward the prior season's depth-slot average by PRESEASON_PRIOR_GAMES pseudo-games (no history: the
slot average × an OVR adjustment; DEFAULT_SLOT_PRIORS when the prior season has no games), against the
prior season's defense-vs-position factors.

Backtest on 2035 (weeks 2–17, starters: QB1/RB1-2/WR1-3/TE1): mean absolute error ≈ 3.3 PPR, correlation
≈ 0.79 with actual points. Most of that comes from the per-game average; production in this league is very
stable week to week, and the matchup factor and recency weighting add little (recency was tested and dropped).

CLI:
    python3 -m engines.fantasy_projection_engine                       # current week from league_state
    python3 -m engines.fantasy_projection_engine --season 2035 --week 18
    python3 -m engines.fantasy_projection_engine --season 2035 --all-weeks   # backfill weeks 1–18
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
MIN_MATCHUP_RATIO = 0.25
SEASON_WEEKS = 18
POS_GROUP = {"QB": "QB", "HB": "RB", "FB": "RB", "WR": "WR", "TE": "TE", "K": "K", "DEF": "DEF"}
GROUPS = ("QB", "RB", "WR", "TE", "K", "DEF")
# Depth slots below these share one prior (e.g. every WR4+ is "WR4").
PRIOR_SLOTS = {"QB": 2, "RB": 3, "WR": 4, "TE": 2, "K": 1, "DEF": 1}
# Backtest population: fantasy starters by depth chart.
STARTER_SLOTS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DEF": 1}
PRESEASON_PRIOR_GAMES = 4.0
DEFAULT_SLOT_PRIORS = {
    ("QB", 1): 17.0, ("QB", 2): 7.5,
    ("RB", 1): 12.0, ("RB", 2): 5.5, ("RB", 3): 1.8,
    ("TE", 1): 8.5, ("TE", 2): 3.5,
    ("WR", 1): 16.0, ("WR", 2): 13.5, ("WR", 3): 8.0, ("WR", 4): 4.5,
    ("K", 1): 7.5,
    ("DEF", 1): 6.5,
}


@dataclass
class SeasonData:
    season: int
    opponent: dict                    # (team_id, week) -> opponent team_id (scheduled or completed)
    completed_weeks: dict             # team_id -> set of weeks with a completed game
    roster: dict                      # player_id -> {name, team_id, position, group, depth, ovr}
    points: dict = field(default_factory=lambda: defaultdict(dict))   # player_id -> {week: PPR}
    allowed: dict = field(default_factory=lambda: defaultdict(float)) # (def_team, group, week) -> PPR allowed
    injuries: dict = field(default_factory=dict)                      # (player_id, week) -> {weeks_remaining, is_ir}
    prev_player_stats: dict = field(default_factory=dict)             # player_id -> {'games': int, 'total': float, 'ppg': float}
    prev_slot_priors: dict = field(default_factory=dict)              # (group, slot) -> float
    prev_def_factors: dict = field(default_factory=dict)              # (def_team_id, group) -> float
    prev_def_stars: dict = field(default_factory=dict)                # group -> {def_team_id: int 1..5}


# Skill positions score from player_stats.fantasy_points; K and DEF score from their own stat tables below.
SKILL_POSITIONS = ("QB", "HB", "FB", "WR", "TE")
# Kicker points for one game_stat_kicking row `k`: yardage-based scoring (minimum 3 points under 30 yds,
# fractional 0.1 per yard: 3.x for 30–39, 4.x for 40–49, 5.x for 50+, with fg_long exact yardage for the longest kick),
# 1 per XP, -1 per missed FG or missed XP.
KICKING_POINTS_SQL = """(
    COALESCE(k.fgm_29, 0) * 3.0
    + COALESCE(k.fgm_39, 0) * 3.5
    + COALESCE(k.fgm_49, 0) * 4.5
    + COALESCE(k.fgm_50, 0) * 5.5
    + CASE 
        WHEN COALESCE(k.fgm, 0) > 0 AND COALESCE(k.fg_long, 0) >= 30 THEN
            (k.fg_long::numeric * 0.1) - (
                CASE 
                    WHEN k.fg_long >= 50 THEN 5.5
                    WHEN k.fg_long >= 40 THEN 4.5
                    ELSE 3.5
                END
            )
        ELSE 0.0
    END
    + COALESCE(k.xpm, 0) * 1.0
    - (COALESCE(k.fga, 0) - COALESCE(k.fgm, 0)) * 1.0
    - (COALESCE(k.xpa, 0) - COALESCE(k.xpm, 0)) * 1.0
)"""
# Team defense points for one game: `def` is game_stat_defense summed per (game, team), `ret` return TDs,
# `dst` the DEF player_seasons row, `g` the game. Plays (sack 1, INT/FR/safety/block 2, TD 6) plus points-allowed tier.
_DEF_ALLOWED_SQL = "(CASE WHEN g.home_team_id = dst.team_id THEN g.away_score ELSE g.home_score END)"
DEF_POINTS_SQL = f"""(COALESCE(def.sacks, 0) + 2 * COALESCE(def.ints, 0)
    + 2 * COALESCE(def.fumble_recoveries, 0) + 6 * (COALESCE(def.tds, 0) + COALESCE(ret.tds, 0))
    + 2 * COALESCE(def.safeties, 0) + 2 * COALESCE(def.blocks, 0)
    + CASE
        WHEN {_DEF_ALLOWED_SQL} = 0 THEN 10
        WHEN {_DEF_ALLOWED_SQL} <= 6 THEN 7
        WHEN {_DEF_ALLOWED_SQL} <= 13 THEN 4
        WHEN {_DEF_ALLOWED_SQL} <= 20 THEN 1
        WHEN {_DEF_ALLOWED_SQL} <= 27 THEN 0
        WHEN {_DEF_ALLOWED_SQL} <= 34 THEN -1
        ELSE -4
    END)"""
# Joins that give DEF_POINTS_SQL its `def` and `ret` aliases (append after a `games g` / `player_seasons dst` join).
DEF_STATS_JOIN_SQL = """LEFT JOIN (
    SELECT game_id, team_id, SUM(sacks) AS sacks, SUM(ints) AS ints,
           SUM(fumble_recoveries) AS fumble_recoveries, SUM(tds) AS tds,
           SUM(safeties) AS safeties, SUM(blocks) AS blocks
    FROM game_stat_defense GROUP BY game_id, team_id
) def ON def.game_id = g.id AND def.team_id = dst.team_id
LEFT JOIN (
    SELECT game_id, team_id, SUM(tds) AS tds FROM (
        SELECT game_id, team_id, tds FROM game_stat_kickreturn
        UNION ALL
        SELECT game_id, team_id, tds FROM game_stat_puntreturn
    ) r GROUP BY game_id, team_id
) ret ON ret.game_id = g.id AND ret.team_id = dst.team_id"""


def load_points_rows(cur, season: int) -> list:
    """Every completed-game fantasy line of the season as (player_id, team_id, group, week, points):
    skill players from player_stats, kickers from game_stat_kicking, team defenses from game_stat_defense."""
    cur.execute("""
        SELECT st.player_id, st.team_id, st.position, g.week, st.fantasy_points::float
        FROM player_stats st
        JOIN games g ON g.id = st.game_id
        WHERE g.season = %s AND g.status = 'COMPLETED' AND st.position = ANY(%s)
    """, (season, list(SKILL_POSITIONS)))
    rows = [(pid, tid, POS_GROUP[pos], wk, pts or 0.0) for pid, tid, pos, wk, pts in cur.fetchall()]
    cur.execute(f"""
        SELECT k.player_id, k.team_id, g.week, {KICKING_POINTS_SQL}::float
        FROM game_stat_kicking k
        JOIN games g ON g.id = k.game_id
        WHERE g.season = %s AND g.status = 'COMPLETED'
    """, (season,))
    rows += [(pid, tid, "K", wk, pts or 0.0) for pid, tid, wk, pts in cur.fetchall()]
    cur.execute(f"""
        SELECT dst.player_id, dst.team_id, g.week, {DEF_POINTS_SQL}::float
        FROM player_seasons dst
        JOIN games g ON g.season = dst.season AND g.status = 'COMPLETED'
            AND (g.home_team_id = dst.team_id OR g.away_team_id = dst.team_id)
        {DEF_STATS_JOIN_SQL}
        WHERE dst.season = %s AND dst.position = 'DEF'
    """, (season,))
    rows += [(pid, tid, "DEF", wk, pts or 0.0) for pid, tid, wk, pts in cur.fetchall()]
    return rows


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
        SELECT p.id, COALESCE(p.full_name, p.player_name), ps.team_id, ps.position, ps.overall_rating, ps.released_week
        FROM player_seasons ps
        JOIN players p ON p.id = ps.player_id
        WHERE ps.season = %s AND ps.position = ANY(%s) AND ps.team_id IS NOT NULL
    """, (season, list(POS_GROUP)))
    roster = {}
    by_team_group = defaultdict(list)
    for pid, name, team_id, pos, ovr, released in cur.fetchall():
        roster[pid] = {"name": name, "team_id": team_id, "position": pos, "group": POS_GROUP[pos], "ovr": ovr or 70,
                       "released_week": released}
        if released is None:                 # a released player (League Transactions) holds no depth slot
            by_team_group[(team_id, POS_GROUP[pos])].append((-(ovr or 0), pid))
    for players in by_team_group.values():
        for depth, (_, pid) in enumerate(sorted(players), start=1):
            roster[pid]["depth"] = depth
    for p in roster.values():
        p.setdefault("depth", 99)

    data = SeasonData(season, opponent, completed, roster)

    cur.execute("""
        SELECT player_id, week, weeks_remaining, is_ir
        FROM injuries
        WHERE season = %s AND player_id IS NOT NULL
    """, (season,))
    for pid, wk, w_rem, is_ir in cur.fetchall():
        data.injuries[(pid, wk)] = {
            "weeks_remaining": int(w_rem or 0),
            "is_ir": bool(is_ir),
        }

    # each line's player is its stored player_id (abbreviated names collide across players). A traded player's
    # lines count for him whatever team he had that week; the defense allowed is the line's own team's opponent.
    for pid, team_id, group, week, pts in load_points_rows(cur, season):
        opp = opponent.get((team_id, week))
        if opp is not None:
            data.allowed[(opp, group, week)] += pts
        if pid in roster:
            data.points[pid][week] = data.points[pid].get(week, 0.0) + pts

    # Prior season baseline data (for Week 1 projections and preseason calibration)
    prev_season = season - 1
    cur.execute("SELECT week, home_team_id, away_team_id FROM games WHERE season = %s AND status = 'COMPLETED'",
                (prev_season,))
    opp_map_prev = {}
    team_games_prev = defaultdict(int)
    for w, h, a in cur.fetchall():
        opp_map_prev[(h, w)] = a
        opp_map_prev[(a, w)] = h
        team_games_prev[h] += 1
        team_games_prev[a] += 1
    if team_games_prev:
        prev_rows = load_points_rows(cur, prev_season)

        games_n, totals = defaultdict(int), defaultdict(float)
        def_allowed_prev = defaultdict(float)
        for pid, tid, grp, w, pts in prev_rows:
            games_n[pid] += 1
            totals[pid] += pts
            opp = opp_map_prev.get((tid, w))
            if opp is not None:
                def_allowed_prev[(opp, grp)] += pts
        data.prev_player_stats = {pid: {"games": n, "total": totals[pid], "ppg": totals[pid] / n}
                                  for pid, n in games_n.items()}

        cur.execute("""
            SELECT p.id, ps.team_id, ps.position, ps.overall_rating
            FROM player_seasons ps
            JOIN players p ON p.id = ps.player_id
            WHERE ps.season = %s AND ps.position = ANY(%s) AND ps.team_id IS NOT NULL
        """, (prev_season, list(POS_GROUP)))
        by_team_grp_prev = defaultdict(list)
        roster_prev = {}
        for pid, tid, pos, ovr in cur.fetchall():
            grp = POS_GROUP[pos]
            roster_prev[pid] = {"team_id": tid, "group": grp}
            by_team_grp_prev[(tid, grp)].append((-(ovr or 0), pid))
        for plist in by_team_grp_prev.values():
            for depth, (_, pid) in enumerate(sorted(plist), start=1):
                roster_prev[pid]["depth"] = depth

        slot_sums = defaultdict(float)
        slot_counts = defaultdict(int)
        for pid, s in data.prev_player_stats.items():
            if pid in roster_prev:
                grp = roster_prev[pid]["group"]
                slot = min(roster_prev[pid]["depth"], PRIOR_SLOTS[grp])
                slot_sums[(grp, slot)] += s["total"]
                slot_counts[(grp, slot)] += s["games"]
        data.prev_slot_priors = {k: slot_sums[k] / slot_counts[k] for k in slot_sums if slot_counts[k] > 0}

        prev_allowed_pg = {g: {} for g in GROUPS}
        total_gms = sum(team_games_prev.values())
        for g in GROUPS:
            total_pts = sum(def_allowed_prev.get((t, g), 0.0) for t in team_games_prev)
            lg = total_pts / total_gms if total_gms else 0.0
            for t, gms in team_games_prev.items():
                t_tot = def_allowed_prev.get((t, g), 0.0)
                prev_allowed_pg[g][t] = t_tot / gms if gms else 0.0
                data.prev_def_factors[(t, g)] = _matchup_factor(t_tot, gms, lg)
        data.prev_def_stars = {g: _ntile5_ascending(prev_allowed_pg[g]) for g in GROUPS}

    return data


def _matchup_factor(team_allowed: float, games: int, league_per_game: float) -> float:
    """Opponent's allowed points per game relative to the league average, shrunk by DEF_SHRINK_GAMES and
    dampened by MATCHUP_EXPONENT. D/ST can score negative points, so the league average can be ≤ 0 (neutral 1.0)
    and a ratio can dip below zero (floored at MIN_MATCHUP_RATIO, the toughest possible matchup)."""
    if league_per_game <= 0:
        return 1.0
    shrunk = (team_allowed + DEF_SHRINK_GAMES * league_per_game) / ((games + DEF_SHRINK_GAMES) * league_per_game)
    return max(shrunk, MIN_MATCHUP_RATIO) ** MATCHUP_EXPONENT


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


def is_released(p: dict, week: int) -> bool:
    """Released (League Transactions, player_seasons.released_week) by `week`: he has no team game, projects 0."""
    rw = p.get("released_week")
    return rw is not None and rw <= week


def player_weeks(data: SeasonData, pid: int, team_weeks: set, week: int) -> set:
    """The weeks before `week` that count for a player's baseline: his current team's completed weeks plus every
    week he has a line (a traded player keeps his old team's games)."""
    return set(team_weeks) | {w for w in data.points[pid] if w < week}


def get_player_injury(data: SeasonData, pid: int, week: int) -> Optional[dict]:
    """Return injury status dict for player at target week: {'weeks_remaining': int, 'is_ir': bool} or None."""
    if (pid, week) in data.injuries:
        return data.injuries[(pid, week)]
    priors = [w for (p, w) in data.injuries if p == pid and w < week]
    if priors:
        latest = max(priors)
        info = data.injuries[(pid, latest)]
        rem = info["weeks_remaining"] - (week - latest)
        if info["is_ir"]:
            return {"weeks_remaining": max(0, rem), "is_ir": True}
        if rem > 0:
            return {"weeks_remaining": rem, "is_ir": False}
    return None


def _project_week_1(data: SeasonData) -> list:
    """Projection rows for Week 1 using prior season stats, depth chart priors, and prior defensive factors."""
    rows = []
    for pid, p in data.roster.items():
        team, g, ovr = p["team_id"], p["group"], p.get("ovr", 70)
        slot = min(p["depth"], PRIOR_SLOTS[g])
        slot_prior = data.prev_slot_priors.get((g, slot)) or DEFAULT_SLOT_PRIORS.get((g, slot), 8.0)
        s = data.prev_player_stats.get(pid)
        if s:
            baseline = (s["total"] + PRESEASON_PRIOR_GAMES * slot_prior) / (s["games"] + PRESEASON_PRIOR_GAMES)
            avg_points = s["ppg"]
        else:
            ovr_adj = max(0.6, min(1.4, 1.0 + (ovr - 80) * 0.015))
            baseline = slot_prior * ovr_adj
            avg_points = None

        opp = None if is_released(p, 1) else data.opponent.get((team, 1))
        inj = get_player_injury(data, pid, 1)
        is_ir = inj["is_ir"] if inj else False
        weeks_rem = inj["weeks_remaining"] if inj else 0
        is_out = is_ir or (weeks_rem > 0)

        factor = data.prev_def_factors.get((opp, g), 1.0) if opp is not None else 1.0
        stars = data.prev_def_stars.get(g, {}).get(opp, 3) if opp is not None else 3

        if is_out or opp is None:
            projected = 0.0
        else:
            projected = baseline * factor

        ros_total = 0.0
        for w in range(1, SEASON_WEEKS + 1):
            if (team, w) not in data.opponent or is_released(p, w):
                continue
            if is_ir and weeks_rem == 0:
                continue
            if (w - 1) < weeks_rem:
                continue
            ros_opp = data.opponent[(team, w)]
            ros_factor = data.prev_def_factors.get((ros_opp, g), 1.0)
            ros_total += baseline * ros_factor

        rows.append({
            "player_id": pid,
            "player_name": p["name"],
            "position": p["position"],
            "group": g,
            "team_id": team,
            "on_bye": opp is None,
            "is_out": is_out,
            "projected_points": round(projected, 2),
            "avg_points": round(avg_points, 2) if avg_points is not None else None,
            "delta": round(projected - avg_points, 2) if avg_points is not None and opp is not None and not is_out else None,
            "matchup_stars": stars if opp is not None and not is_out else None,
            "ros_total_points": round(ros_total, 2),
        })

    for g in GROUPS:
        group_rows = [r for r in rows if r["group"] == g]
        playing = sorted((r for r in group_rows if not r["on_bye"] and not r["is_out"]),
                         key=lambda r: (-r["projected_points"], -r["ros_total_points"], r["player_id"]))
        for i, r in enumerate(playing, start=1):
            r["pos_rank"] = f"{g}{i}"
        for i, r in enumerate(sorted(group_rows, key=lambda r: (-r["ros_total_points"], r["player_id"])), start=1):
            r["ros_rank"] = f"{g}{i}"
    return rows


def project_week(data: SeasonData, week: int) -> list:
    """Projection rows for `week`, using only completed games before it. Empty if there is no earlier data."""
    prior_weeks = {t: {w for w in wks if w < week} for t, wks in data.completed_weeks.items()}
    if not any(prior_weeks.values()):
        if week != 1:
            return []
        return _project_week_1(data)

    # Depth-slot priors: league PPR per team game for each (group, slot).
    slot_sum, slot_n = defaultdict(float), defaultdict(int)
    for pid, p in data.roster.items():
        if p.get("released_week") is not None:
            continue
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
            team_total = sum(data.allowed.get((team, g, w), 0.0) for w in wks)
            factor[(team, g)] = _matchup_factor(team_total, len(wks), lg)
    stars = {g: _ntile5_ascending(allowed_pg[g]) for g in GROUPS}

    rows = []
    for pid, p in data.roster.items():
        team, g = p["team_id"], p["group"]
        played = player_weeks(data, pid, prior_weeks.get(team, set()), week)
        player_pts = data.points[pid]
        prior = slot_prior.get((g, min(p["depth"], PRIOR_SLOTS[g])), 0.0)
        total = sum(player_pts.get(w, 0.0) for w in played)
        baseline = (total + PRIOR_GAMES * prior) / (len(played) + PRIOR_GAMES)

        appearances = [player_pts[w] for w in played if w in player_pts]
        avg_points = sum(appearances) / len(appearances) if appearances else None

        released = is_released(p, week)
        opp = None if released else data.opponent.get((team, week))
        inj = get_player_injury(data, pid, week)
        is_ir = inj["is_ir"] if inj else False
        weeks_rem = inj["weeks_remaining"] if inj else 0
        is_out = is_ir or (weeks_rem > 0)

        if is_out or opp is None:
            projected = 0.0
        else:
            projected = baseline * factor.get((opp, g), 1.0)

        ros_total = 0.0
        for w in range(week, SEASON_WEEKS + 1):
            if (team, w) not in data.opponent or is_released(p, w):
                continue
            if is_ir and weeks_rem == 0:
                continue
            if (w - week) < weeks_rem:
                continue
            ros_total += baseline * factor.get((data.opponent[(team, w)], g), 1.0)

        rows.append({
            "player_id": pid,
            "player_name": p["name"],
            "position": p["position"],
            "group": g,
            "team_id": team,
            "on_bye": opp is None,
            "is_out": is_out,
            "projected_points": round(projected, 2),
            "avg_points": round(avg_points, 2) if avg_points is not None else None,
            "delta": round(projected - avg_points, 2) if avg_points is not None and opp is not None and not is_out else None,
            "matchup_stars": stars[g].get(opp, 3) if opp is not None and not is_out else None,
            "ros_total_points": round(ros_total, 2),
        })

    for g in GROUPS:
        group_rows = [r for r in rows if r["group"] == g]
        playing = sorted((r for r in group_rows if not r["on_bye"] and not r["is_out"]),
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
    parser.add_argument("--all-weeks", action="store_true", help=f"Project weeks 1–{SEASON_WEEKS}")
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
    weeks = list(range(1, SEASON_WEEKS + 1)) if args.all_weeks else [week]
    print(f"🏈 Projecting fantasy points for {season}, week(s) {weeks[0]}–{weeks[-1]}...")
    project_weeks(season, weeks)


if __name__ == "__main__":
    main()
