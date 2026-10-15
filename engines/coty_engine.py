"""Coach of the Year race (PROJECT_SPEC §2.11): scores every head coach of a season from the regular season.

Inputs, each a z-score across the season's coaches who have it:
- win%                    (ties count half)
- wins above expectation  wins − expected wins, from Opponent-Adjusted Game Credit (§2.4) on regular-season games
- roster outperformance   team_ovr rank − win% rank (positive = winning more than the roster's rating says)
- improvement             win% − the team's previous regular-season win%, from `games`; only a full previous
                          season (FULL_SEASON_GAMES regular-season games) counts

COTY score = 50 + 10 · Σ wᵢ zᵢ / Σ wᵢ over the inputs the coach has, + 3 in a coach's first season with the team.
An input that is missing (no full previous regular season in `games`, no team_ovr) is skipped for that coach and the
remaining weights are rescaled. The coach is the one whose `coach_tenures` row covers the season.

CLI:
    python3 -m engines.coty_engine --season 2035
"""

import argparse
import statistics

import psycopg

from db.db_handler import DatabaseHandler
from engines.game_credit_engine import compute_game_credit, fetch_completed_games

WEIGHTS = {
    "win_pct": 0.30,
    "wins_above_expected": 0.20,
    "roster_outperformance": 0.20,
    "win_pct_change": 0.30,
}
FIRST_YEAR_BONUS = 3.0
FULL_SEASON_GAMES = 17   # a team's regular season; fewer previous-season games = improvement skipped
BASE, SCALE = 50.0, 10.0


def average_ranks(values: dict) -> dict:
    """{key: rank}, 1 = highest value; tied values share the mean of their ranks."""
    ordered = sorted(values, key=lambda k: values[k], reverse=True)
    ranks, i = {}, 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and values[ordered[j + 1]] == values[ordered[i]]:
            j += 1
        for k in ordered[i:j + 1]:
            ranks[k] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def zscores(values: dict) -> dict:
    """{key: z} over the keys with a value; {} when fewer than two values or no spread."""
    present = {k: v for k, v in values.items() if v is not None}
    if len(present) < 2:
        return {}
    mean, sd = statistics.fmean(present.values()), statistics.pstdev(present.values())
    if sd == 0:
        return {}
    return {k: (v - mean) / sd for k, v in present.items()}


def regular_season(games: list) -> list:
    """A NULL game_type counts as regular season, as in game_credit_engine.fetch_completed_games."""
    return [g for g in games if (g.get("game_type") or "REGULAR_SEASON") == "REGULAR_SEASON"]


def win_pcts(games: list) -> dict:
    """{team_id: regular-season win%} (ties count half)."""
    tally = {}
    for g in regular_season(games):
        for team, pf, pa in ((g["home_team_id"], g["home_score"], g["away_score"]),
                             (g["away_team_id"], g["away_score"], g["home_score"])):
            w, n = tally.get(team, (0.0, 0))
            tally[team] = (w + (1.0 if pf > pa else 0.5 if pf == pa else 0.0), n + 1)
    return {t: w / n for t, (w, n) in tally.items()}


def full_season_win_pcts(games: list, min_games: int = FULL_SEASON_GAMES) -> dict:
    """win_pcts for the teams that played at least `min_games` regular-season games, so a partly loaded season
    (a few backlog weeks, or a season still in progress) never stands in for last season's record."""
    played = {}
    for g in regular_season(games):
        for team in (g["home_team_id"], g["away_team_id"]):
            played[team] = played.get(team, 0) + 1
    return {t: pct for t, pct in win_pcts(games).items() if played[t] >= min_games}


def compute_coty(season: int, games: list, coaches: dict, team_ovr: dict, prev_win_pct: dict) -> list:
    """games: the season's completed games (non-regular-season ones are ignored). coaches: {team_id: {coach_id,
    coach_name, start_season}}. team_ovr: {team_id: OVR}. prev_win_pct: {team_id: last season's regular-season
    win%}, teams without one omitted. Returns one dict per coach whose team has played, best score first."""
    games = regular_season(games)
    if not games:
        return []
    credits, _, _ = compute_game_credit(games)
    teams = [t for t in credits if t in coaches]
    if not teams:
        return []
    win_pct = {t: credits[t].wins / credits[t].games for t in teams}
    rated = {t: team_ovr[t] for t in teams if team_ovr.get(t) is not None}
    ovr_rank, win_rank = average_ranks(rated), average_ranks({t: win_pct[t] for t in rated})

    raw = {
        "win_pct": win_pct,
        "wins_above_expected": {t: credits[t].wins - credits[t].expected_wins for t in teams},
        "roster_outperformance": {t: (ovr_rank[t] - win_rank[t]) if t in rated else None for t in teams},
        "win_pct_change": {t: (win_pct[t] - prev_win_pct[t]) if t in prev_win_pct else None for t in teams},
    }
    z = {name: zscores(vals) for name, vals in raw.items()}

    out = []
    for t in teams:
        c = credits[t]
        used = {name: z[name][t] for name in WEIGHTS if t in z[name]}
        total_w = sum(WEIGHTS[name] for name in used)
        blend = sum(WEIGHTS[name] * used[name] for name in used) / total_w if total_w else 0.0
        coach = coaches[t]
        first_year = coach.get("start_season") == season
        out.append({
            "coach_id": coach["coach_id"],
            "coach_name": coach["coach_name"],
            "team_id": t,
            "wins": c.wins,
            "losses": c.games - c.wins,
            "games": c.games,
            "win_pct": win_pct[t],
            "expected_wins": c.expected_wins,
            "wins_above_expected": raw["wins_above_expected"][t],
            "team_ovr": team_ovr.get(t),
            "ovr_rank": ovr_rank.get(t),
            "win_rank": win_rank.get(t),
            "roster_outperformance": raw["roster_outperformance"][t],
            "prev_win_pct": prev_win_pct.get(t),
            "win_pct_change": raw["win_pct_change"][t],
            "first_year_with_team": first_year,
            "inputs_used": sorted(used),
            "coty_score": BASE + SCALE * blend + (FIRST_YEAR_BONUS if first_year else 0.0),
        })
    out.sort(key=lambda r: r["coty_score"], reverse=True)
    for i, r in enumerate(out, start=1):
        r["coty_rank"] = i
    return out


def fetch_coaches(cur, season: int) -> dict:
    """{team_id: coach} for the head coach whose tenure covers `season`."""
    cur.execute("""
        SELECT ct.team_id, c.id, c.coach_name, ct.start_season
        FROM coach_tenures ct JOIN coaches c ON c.id = ct.coach_id
        WHERE ct.start_season <= %(s)s AND (ct.end_season IS NULL OR ct.end_season >= %(s)s)
    """, {"s": season})
    return {t: {"coach_id": cid, "coach_name": name, "start_season": start} for t, cid, name, start in cur.fetchall()}


def fetch_team_ovr(cur, season: int) -> dict:
    cur.execute("SELECT team_id, team_ovr FROM team_seasons WHERE season = %s AND team_ovr IS NOT NULL", (season,))
    return dict(cur.fetchall())


def fetch_inputs(cur, season: int) -> tuple:
    """(coaches, team_ovr, prev_win_pct) for `season`. The season's games are fetched by the caller, which may
    apply a week cutoff; the previous season is complete history and is read whole."""
    prev = full_season_win_pcts(fetch_completed_games(cur, season - 1))
    return fetch_coaches(cur, season), fetch_team_ovr(cur, season), prev


class CotyEngine:
    def __init__(self):
        self.db = DatabaseHandler()

    def calculate(self, season: int | None = None) -> list:
        if season is None:
            season = self.db.get_current_league_state()[0]
        print(f"--- Calculating Coach of the Year race for Season {season} ---")
        with psycopg.connect(self.db.db_conn_info) as conn:
            with conn.cursor() as cur:
                race = compute_coty(season, fetch_completed_games(cur, season), *fetch_inputs(cur, season))
                cur.execute("DELETE FROM coty_scores WHERE season = %s", (season,))
                cur.executemany("""
                    INSERT INTO coty_scores (coach_id, team_id, season, coty_score, coty_rank, win_pct,
                        wins_above_expected, roster_outperformance, win_pct_change, first_year_with_team)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, [(r["coach_id"], r["team_id"], season, round(r["coty_score"], 2), r["coty_rank"],
                       round(r["win_pct"], 3), round(r["wins_above_expected"], 2),
                       None if r["roster_outperformance"] is None else round(r["roster_outperformance"], 1),
                       None if r["win_pct_change"] is None else round(r["win_pct_change"], 3),
                       r["first_year_with_team"]) for r in race])
                cur.execute("SELECT id, abbreviation FROM teams")
                abbr = dict(cur.fetchall())
            conn.commit()

        if not race:
            print("No completed regular-season games; nothing to score.")
            return race
        if not any(r["win_pct_change"] is not None for r in race):
            print(f"No full {season - 1} regular season in games: improvement skipped, other inputs re-weighted.")
        print("\n🏆 COACH OF THE YEAR RACE")
        print("=" * 100)
        for r in race:
            change = "  n/a" if r["win_pct_change"] is None else f"{r['win_pct_change']:+.3f}"
            roster = "  n/a" if r["roster_outperformance"] is None else f"{r['roster_outperformance']:+5.1f}"
            print(f"{r['coty_rank']:>2}. {r['coach_name']:<22} {abbr.get(r['team_id'], ''):<4} "
                  f"{r['wins']:g}-{r['losses']:g} | Score {r['coty_score']:5.1f} | W−xW {r['wins_above_expected']:+4.1f} "
                  f"| Roster {roster} | ΔWin% {change}{' | 1st yr' if r['first_year_with_team'] else ''}")
        return race


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, help="default: league_state.current_season")
    CotyEngine().calculate(season=parser.parse_args().season)
