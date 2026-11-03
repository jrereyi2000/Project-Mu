"""Cutoff-bounded SQL inputs for progression checkpoints and evidence reviews.

This adapter reads observations and schedules only. It grants no development,
stores no state, invents no routes/targets/EPA, and does not treat missing game
lines or absent playoff schedules as proof of a completed season. Comparative
signals are deliberately labelled box-score proxies with explicit reliability.
"""
from __future__ import annotations
from collections import defaultdict
import math
from typing import Any, Mapping

from engines.progression_evidence import POSTSEASON

POSITION_GROUPS = {
    "QB": "QB", "HB": "RB", "RB": "RB", "FB": "RB", "WR": "REC", "TE": "REC",
    "LT": "OL", "LG": "OL", "C": "OL", "RG": "OL", "RT": "OL",
    "DT": "FRONT", "LE": "FRONT", "RE": "FRONT", "LEDG": "FRONT", "REDG": "FRONT",
    "LOLB": "FRONT", "MLB": "FRONT", "ROLB": "FRONT", "MIKE": "FRONT", "WILL": "FRONT", "SAM": "FRONT",
    "CB": "DB", "FS": "DB", "SS": "DB", "K": "K", "P": "P",
}
EXCLUDED_TYPES = {"PRESEASON", "BYE", "PRO_BOWL"}

ROSTER_SQL = """
    SELECT ps.player_id, p.madden_pgid AS pgid, ps.team_id, ps.position, ps.age, ps.released_week
    FROM player_seasons ps JOIN players p ON p.id = ps.player_id
    WHERE ps.season = %s
"""
GAMES_SQL = """
    SELECT id AS game_id, season, week, home_team_id, away_team_id,
           home_score, away_score, status, COALESCE(game_type, 'REGULAR_SEASON') AS game_type
    FROM games WHERE season = %s AND week <= %s
    ORDER BY week, id
"""
LINES_SQL = """
    SELECT l.*, g.status, COALESCE(g.game_type, 'REGULAR_SEASON') AS game_type,
           COALESCE(s.position, gen.position, b.position, d.position, k.position, pu.position,
                    ps.position) AS position
    FROM game_stat_lines l JOIN games g ON g.id = l.game_id
    LEFT JOIN player_seasons ps ON ps.player_id = l.player_id AND ps.season = l.season
    LEFT JOIN player_stats s ON s.game_id = l.game_id AND s.player_id = l.player_id
    LEFT JOIN game_stat_general gen ON gen.game_id = l.game_id AND gen.player_id = l.player_id
    LEFT JOIN game_stat_blocking b ON b.game_id = l.game_id AND b.player_id = l.player_id
    LEFT JOIN game_stat_defense d ON d.game_id = l.game_id AND d.player_id = l.player_id
    LEFT JOIN game_stat_kicking k ON k.game_id = l.game_id AND k.player_id = l.player_id
    LEFT JOIN game_stat_punting pu ON pu.game_id = l.game_id AND pu.player_id = l.player_id
    WHERE l.season = %s AND l.week <= %s AND g.status = 'COMPLETED'
      AND COALESCE(g.game_type, 'REGULAR_SEASON') NOT IN ('PRESEASON', 'BYE', 'PRO_BOWL')
    ORDER BY l.week, l.game_id, l.player_id
"""


def _rows(cur):
    cols = [getattr(d, "name", None) or d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def _count(row, key):
    return _number(row.get(key)) or 0.0


def _measurement(row):
    """Return comparable proxy/denominator/metric/reliability/skill mapping.

    The conservative mappings intentionally omit receiving routes/coverage/pass
    depth skills. Directly recorded accuracy, ball security and sacks allowed
    support only broad existing ratings; no tracking metric is fabricated.
    """
    group = row.get("position_group")
    if group == "QB" and _count(row, "pass_attempts") > 0 and _number(row.get("pass_yards")) is not None:
        n = _count(row, "pass_attempts")
        return ((_count(row, "pass_yards") + 20 * _count(row, "pass_tds")
                 - 45 * _count(row, "interceptions")) / n, n, "passing_production_per_attempt_proxy", 0.8, [])
    if group == "RB" and _count(row, "rush_attempts") > 0 and _number(row.get("rush_yards")) is not None:
        n = _count(row, "rush_attempts")
        skills = ["Carrying"] if row.get("fumb") is not None and _count(row, "fumb") == 0 and n >= 5 else []
        return ((_count(row, "rush_yards") + 20 * _count(row, "rush_tds")
                 - 25 * _count(row, "fumb")) / n, n, "rushing_production_per_carry_proxy", 0.65, skills)
    if group == "REC" and _count(row, "receptions") > 0 and _number(row.get("rec_yards")) is not None:
        n = _count(row, "receptions")
        # B separately halves reception-only opportunity reliability.
        return ((_count(row, "rec_yards") + 20 * _count(row, "rec_tds")) / n,
                n, "receiving_production_per_reception_proxy", 1.0, [])
    if group == "OL" and _count(row, "downs_played") > 0 and _number(row.get("blk_sacks_allowed")) is not None:
        n = _count(row, "downs_played")
        return (-_count(row, "blk_sacks_allowed") / n, n, "sacks_allowed_per_down_proxy", 0.6, ["PassBlock"])
    if group in ("FRONT", "DB") and _count(row, "downs_played") > 0:
        fields = ("def_tackles", "def_tfl", "def_sacks", "def_ints", "def_deflections", "def_forced_fumbles")
        if not any(_number(row.get(key)) is not None for key in fields):
            return None
        n = _count(row, "downs_played")
        points = (5 * _count(row, "def_sacks") + 6 * _count(row, "def_ints")
                  + 2 * _count(row, "def_deflections") + 4 * _count(row, "def_forced_fumbles"))
        if group == "FRONT":
            points += _count(row, "def_tackles") + 2 * _count(row, "def_tfl")
        return points / n, n, f"{group.lower()}_production_per_down_proxy", 0.5, []
    if group == "K" and _count(row, "kick_fga") > 0 and _number(row.get("kick_fgm")) is not None:
        n = _count(row, "kick_fga")
        return _count(row, "kick_fgm") / n, n, "field_goal_accuracy_proxy", 0.6, ["KickAccuracy"]
    if group == "P" and _count(row, "punt_punts") > 0 and _number(row.get("punt_net_average")) is not None:
        return _count(row, "punt_net_average"), _count(row, "punt_punts"), "punt_net_average_proxy", 0.6, []
    return None


def _annotate_cohort_snapshot(lines):
    # Weighted sufficient statistics make all-player weekly comparison linear,
    # while allowing every player's own history to be excluded from his baseline.
    totals = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
    by_player = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
    cohort_players = defaultdict(set)
    measured = []
    for row in lines:
        value = _measurement(row)
        if value is None:
            continue
        score, opportunity, metric, quality, skills = value
        cohort = (row["position_group"], metric)
        measured.append((row, value, cohort))
        cohort_players[cohort].add(row["player_id"])
        for aggregate in (totals[cohort], by_player[(cohort, row["player_id"])]):
            aggregate[0] += opportunity
            aggregate[1] += score * opportunity
            aggregate[2] += score * score * opportunity
            aggregate[3] += 1
    for row, (score, opportunity, metric, quality, skills), cohort in measured:
        all_stats, own = totals[cohort], by_player[(cohort, row["player_id"])]
        weight, first, second = (all_stats[i] - own[i] for i in range(3))
        count = all_stats[3] - own[3]
        player_count = len(cohort_players[cohort]) - 1
        row.update(efficiency=score, efficiency_metric=metric, supported_skills=skills,
                   source="game_stat_lines", signal_reliability=quality)
        if player_count < 4 or weight <= 0:
            row["performance_signal"] = None
            row["signal_note"] = "insufficient position cohort: at least four other measured players required"
            continue
        mean = first / weight
        sd = math.sqrt(max(0.0, second / weight - mean * mean))
        row.update(baseline_efficiency=mean, efficiency_scale=sd,
                   signal_reliability=quality * min(1.0, player_count / 8))
        if sd <= 1e-9:
            row["performance_signal"] = None
            row["signal_note"] = "position cohort has no measurable variation"
        else:
            row["performance_signal"] = math.tanh((score - mean) / (2 * sd)) * count / (count + 4)
            row["signal_note"] = "cutoff-bounded leave-one-player-out box-score proxy; sample-shrunk"


def _annotate_signals(lines):
    """Freeze each game's comparison to the cohort known through its game week.

    A later weekly pass may prepare an older missed checkpoint. Reusing later
    peer outcomes for its baseline would change historical evidence. At most the
    season's 23 week-prefix snapshots are needed; each comparison remains linear.
    All games in the same week share that completed-week cutoff.
    """
    by_week = defaultdict(list)
    for row in lines:
        by_week[row["week"]].append(row)
    prefix = []
    for week in sorted(by_week):
        prefix.extend(by_week[week])
        copies = [dict(row) for row in prefix]
        _annotate_cohort_snapshot(copies)
        current = {(row["player_id"], row["game_id"]): row for row in copies if row["week"] == week}
        for row in by_week[week]:
            row.update(current[(row["player_id"], row["game_id"])])


def _completed(game):
    return (str(game.get("status", "")).upper() == "COMPLETED"
            and str(game.get("game_type", "REGULAR_SEASON")).upper() not in EXCLUDED_TYPES)


def _aligned_schedule(roster, player_games, games, week):
    actual = {line["game_id"]: line for line in player_games}
    actual_by_week = defaultdict(list)
    for line in player_games:
        actual_by_week[line["week"]].append(line)
    current_team = roster.get("team_id")
    released = roster.get("released_week")
    if released is not None and released <= week:
        current_team = None
    old_weeks = [line["week"] for line in player_games
                 if current_team is not None and line.get("team_id") not in (None, current_team)]
    last_contradiction = max(old_weeks, default=-1)
    candidates = defaultdict(list)
    for game in games:
        if not _completed(game):
            continue
        wk = game["week"]
        if game["game_id"] in actual:
            candidates[wk].append(game)
        elif (not actual_by_week.get(wk) and current_team is not None and wk > last_contradiction
              and current_team in (game.get("home_team_id"), game.get("away_team_id"))):
            candidates[wk].append(game)
    schedule = []
    notes = []
    for wk in sorted(candidates):
        choices = candidates[wk]
        if len(choices) > 1:
            notes.append(f"week {wk}: ambiguous multiple player-team games; one calendar appearance retained")
        # Prefer actual game evidence; never count old and destination team twice in a week.
        choice = max(choices, key=lambda g: (g["game_id"] in actual,
                     _count(actual.get(g["game_id"], {}), "downs_played"), str(g["game_id"])))
        schedule.append(dict(choice))
    if old_weeks:
        notes.append("historical team membership retained from actual game lines; unobserved trade dates are unknown")
    return schedule, current_team, notes


def build_inputs(rosters, games, lines, *, season, week):
    """Pure adapter core; output maps player id to root's checkpoint input shape."""
    games = [{**game, "game_type": game.get("game_type") or "REGULAR_SEASON"} for game in games if game.get("season") == season
             and game.get("week") is not None and game["week"] <= week
             and str(game.get("game_type", "")).upper() not in EXCLUDED_TYPES]
    game_by_id = {game["game_id"]: game for game in games}
    rosters = {row["player_id"]: dict(row) for row in rosters}
    by_player = defaultdict(list)
    selected_lines = []
    seen = set()
    for original in lines:
        gid, pid = original.get("game_id"), original.get("player_id")
        game = game_by_id.get(gid)
        if game is None or not _completed(game) or pid not in rosters or (pid, gid) in seen:
            continue
        seen.add((pid, gid))
        row = dict(original)
        row.update(game_id=gid, season=season, week=game["week"], status="COMPLETED", game_type=game["game_type"])
        position = row.get("position") or rosters[pid].get("position")
        row["position_group"] = POSITION_GROUPS.get(str(position).upper(), "DEFAULT")
        row.setdefault("source", "game_stat_lines")
        by_player[pid].append(row)
        selected_lines.append(row)
    _annotate_signals(selected_lines)
    completed_superbowl = any(_completed(g) and g["game_type"] == "SUPER_BOWL" for g in games)
    result = {}
    for pid, roster in rosters.items():
        player_games = sorted(by_player[pid], key=lambda row: (row["week"], str(row["game_id"])))
        schedule, team, notes = _aligned_schedule(roster, player_games, games, week)
        postseason = [g for g in schedule if g.get("game_type") in POSTSEASON]
        lost = False
        if team is not None:
            for game in postseason:
                if team not in (game.get("home_team_id"), game.get("away_team_id")):
                    continue
                hs, aws = _number(game.get("home_score")), _number(game.get("away_score"))
                if hs is None or aws is None:
                    continue
                lost |= (hs < aws if team == game.get("home_team_id") else aws < hs)
        closed = bool(lost or completed_superbowl)
        result[pid] = {"pgid": roster.get("pgid"), "player_games": player_games,
                       "team_games": schedule, "position": roster.get("position"),
                       "age": roster.get("age"), "season_closed": closed,
                       "playoff_run_closed": bool(postseason and closed),
                       "supported_skills": sorted({skill for row in player_games for skill in row.get("supported_skills", ())}),
                       "notes": notes}
    return result


def fetch_inputs(cur, season: int, week: int):
    """Fetch actual cutoff-completed observations; never query future results."""
    cur.execute(ROSTER_SQL, (season,))
    rosters = _rows(cur)
    cur.execute(GAMES_SQL, (season, week))
    games = _rows(cur)
    cur.execute(LINES_SQL, (season, week))
    lines = _rows(cur)
    return build_inputs(rosters, games, lines, season=season, week=week)
