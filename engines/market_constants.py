"""Franchise market and legacy constants shared by the popularity engines and writer tools.

B (Base Market Gravity) and S_all_time (Legacy Floor) come from docs/models/base_market_gravity_specification.md
and docs/models/legacy_floor.md. The L / D / N breakdown of B (local scale, local fervor, national reach) is only
published for the eight archetype teams in that spec; other teams carry None for L, D and N.

Recent Super Bowl hardware is NOT hardcoded here: recent_hardware_bumps() derives it from games.game_type = 'SUPER_BOWL'.
"""

TEAM_MARKET = {
    "Cowboys":    {"B": 85.85, "S_all_time": 82.3, "L": 75, "D": 82, "N": 100},
    "Eagles":     {"B": 71.00, "S_all_time": 55.1, "L": 64, "D": 78, "N": 72},
    "49ers":      {"B": 70.90, "S_all_time": 78.1},
    "Packers":    {"B": 70.70, "S_all_time": 88.1, "L": 24, "D": 98, "N": 94},
    "Steelers":   {"B": 70.65, "S_all_time": 89.8, "L": 32, "D": 92, "N": 91},
    "Bears":      {"B": 70.50, "S_all_time": 58.4},
    "Giants":     {"B": 69.90, "S_all_time": 74.1, "L": 82, "D": 44, "N": 80},
    "Chiefs":     {"B": 67.70, "S_all_time": 70.0},
    "Patriots":   {"B": 66.30, "S_all_time": 82.3},
    "Broncos":    {"B": 63.00, "S_all_time": 60.6},
    "Commanders": {"B": 59.65, "S_all_time": 56.6},
    "Bills":      {"B": 56.60, "S_all_time": 34.1, "L": 18, "D": 100, "N": 58},
    "Raiders":    {"B": 53.85, "S_all_time": 60.9},
    "Seahawks":   {"B": 53.80, "S_all_time": 40.3},
    "Jets":       {"B": 53.55, "S_all_time": 24.8},
    "Vikings":    {"B": 53.30, "S_all_time": 45.2},
    "Lions":      {"B": 52.65, "S_all_time": 35.0},
    "Browns":     {"B": 51.35, "S_all_time": 42.4},
    "Ravens":     {"B": 51.10, "S_all_time": 71.5},
    "Dolphins":   {"B": 47.80, "S_all_time": 59.6},
    "Saints":     {"B": 47.20, "S_all_time": 26.2},
    "Rams":       {"B": 44.45, "S_all_time": 63.1},
    "Falcons":    {"B": 43.70, "S_all_time": 17.5},
    "Texans":     {"B": 43.05, "S_all_time": 14.7},
    "Buccaneers": {"B": 39.30, "S_all_time": 25.9},
    "Colts":      {"B": 39.00, "S_all_time": 52.8},
    "Bengals":    {"B": 38.15, "S_all_time": 23.4},
    "Chargers":   {"B": 36.35, "S_all_time": 31.7, "L": 61, "D": 22, "N": 24},
    "Cardinals":  {"B": 33.00, "S_all_time": 23.6},
    "Titans":     {"B": 32.20, "S_all_time": 34.6},
    "Panthers":   {"B": 31.90, "S_all_time": 25.3},
    "Jaguars":    {"B": 31.90, "S_all_time": 13.9, "L": 22, "D": 48, "N": 28},
}

DEFAULT_MARKET = {"B": 50.0, "S_all_time": 50.0}

# Franchise POP hardware bump over the trailing 5 seasons (Section 2 of PROJECT_SPEC: popularity).
SB_WIN_BUMP = 25
SB_RUNNER_UP_BUMP = 5
HARDWARE_WINDOW = 5


def market(team_name: str) -> dict:
    m = TEAM_MARKET.get(team_name, DEFAULT_MARKET)
    return {"B": m["B"], "S_all_time": m["S_all_time"], "L": m.get("L"), "D": m.get("D"), "N": m.get("N")}


def super_bowl_results(cur, through_season: int) -> list:
    """[(season, winner_team_id, loser_team_id)] for every Super Bowl up to and including through_season."""
    cur.execute("""
        SELECT season,
               CASE WHEN home_score > away_score THEN home_team_id ELSE away_team_id END,
               CASE WHEN home_score > away_score THEN away_team_id ELSE home_team_id END
        FROM games
        WHERE game_type = 'SUPER_BOWL' AND status = 'COMPLETED' AND season <= %s
        ORDER BY season
    """, (through_season,))
    return cur.fetchall()


def recent_hardware_bumps(cur, season: int) -> dict:
    """{team_id: bump} from Super Bowls in the HARDWARE_WINDOW seasons before `season`."""
    bumps = {}
    for sb_season, winner, loser in super_bowl_results(cur, season - 1):
        if sb_season < season - HARDWARE_WINDOW:
            continue
        bumps[winner] = bumps.get(winner, 0) + SB_WIN_BUMP
        bumps[loser] = bumps.get(loser, 0) + SB_RUNNER_UP_BUMP
    return bumps
