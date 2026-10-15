"""Observed offensive tendencies (PROJECT_SPEC §2.12): how often each team passes and goes for it on fourth down in
this league, after allowing for the score.

The real-world 2026 tiers on `coaches` didn't show up in the 2035 results (pass lean: no relation to pass rate;
fourth down: weak), so writers get these measured tiers instead. Both come from captured season totals:

- pass_rate_oe    pass share of plays (season_stats pass / (pass + rush) attempts) minus the share a league-wide
                  line on point differential per game predicts (teams that trail pass more)
- fourth_down_oe  fourth-down attempts per game (team_seasons.fourth_down_att, Team Stats capture) minus the rate
                  the same kind of line on point differential predicts (teams that trail go for it more)

Each is ranked across the teams that have at least MIN_GAMES regular-season games: the top 11/32 are High, the next
10/32 Medium, the rest Low (the same split as the real-world tiers). Teams with fewer games get the raw values but
no tier, and the writer tools fall back to last season's tier under the same coach. Season totals have no per-week
history, so these always reflect the latest capture.

CLI:
    python3 -m engines.tendency_engine --season 2035
"""

import argparse

import psycopg

from db.db_handler import DatabaseHandler

MIN_GAMES = 4
HIGH_SHARE, MEDIUM_SHARE = 11 / 32, 21 / 32


def ols_residuals(xs: list, ys: list) -> list:
    """Residuals of y on x with an intercept (all zero if x doesn't vary)."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
    return [y - (my + slope * (x - mx)) for x, y in zip(xs, ys)]


def tiers(values: dict) -> dict:
    """{key: 'High'|'Medium'|'Low'} by rank, highest value first: top 11/32 High, next 10/32 Medium, rest Low."""
    n = len(values)
    order = sorted(values, key=lambda k: values[k], reverse=True)
    high, medium = round(n * HIGH_SHARE), round(n * MEDIUM_SHARE)
    return {k: "High" if i < high else "Medium" if i < medium else "Low" for i, k in enumerate(order)}


def compute_observed(rows: dict) -> dict:
    """rows: {team_id: {games, pd_pg, pass_att, rush_att, fourth_att}} (missing values None) →
    {team_id: {pass_rate_oe, fourth_down_oe, observed_pass_lean, observed_fourth_down, tendency_games}}."""
    out = {t: {"pass_rate_oe": None, "fourth_down_oe": None, "observed_pass_lean": None,
               "observed_fourth_down": None, "tendency_games": r.get("games") or 0} for t, r in rows.items()}
    for metric, tier_key, value in (
        ("pass_rate_oe", "observed_pass_lean",
         lambda r: r["pass_att"] / (r["pass_att"] + r["rush_att"])
         if r.get("pass_att") is not None and r.get("rush_att") is not None and r["pass_att"] + r["rush_att"] else None),
        ("fourth_down_oe", "observed_fourth_down",
         lambda r: r["fourth_att"] / r["games"] if r.get("fourth_att") is not None and r.get("games") else None),
    ):
        teams = [t for t, r in rows.items() if r.get("games") and r.get("pd_pg") is not None and value(r) is not None]
        if len(teams) < 3:
            continue
        resid = ols_residuals([rows[t]["pd_pg"] for t in teams], [value(rows[t]) for t in teams])
        for t, v in zip(teams, resid):
            out[t][metric] = v
        ranked = {t: out[t][metric] for t in teams if rows[t]["games"] >= MIN_GAMES}
        for t, tier in tiers(ranked).items() if len(ranked) >= 3 else ():
            out[t][tier_key] = tier
    return out


def fetch_inputs(cur, season: int) -> dict:
    cur.execute("""
        SELECT team_id, count(*), sum(pts - opp)::float / count(*)
        FROM (SELECT home_team_id AS team_id, home_score AS pts, away_score AS opp FROM games
              WHERE season = %s AND status = 'COMPLETED' AND game_type = 'REGULAR_SEASON'
              UNION ALL
              SELECT away_team_id, away_score, home_score FROM games
              WHERE season = %s AND status = 'COMPLETED' AND game_type = 'REGULAR_SEASON') g
        GROUP BY team_id
    """, (season, season))
    rows = {t: {"games": n, "pd_pg": pd, "pass_att": None, "rush_att": None, "fourth_att": None}
            for t, n, pd in cur.fetchall()}
    cur.execute("""
        SELECT team_id, sum(pass_attempts)::float, sum(rush_attempts)::float FROM season_stats
        WHERE season = %s AND team_id IS NOT NULL GROUP BY team_id
    """, (season,))
    for t, pa, ra in cur.fetchall():
        if t in rows:
            rows[t].update(pass_att=pa, rush_att=ra)
    cur.execute("SELECT team_id, fourth_down_att FROM team_seasons WHERE season = %s", (season,))
    for t, fa in cur.fetchall():
        if t in rows:
            rows[t]["fourth_att"] = fa
    return rows


class TendencyEngine:
    def __init__(self):
        self.db = DatabaseHandler()

    def calculate(self, season: int | None = None) -> dict:
        if season is None:
            season = self.db.get_current_league_state()[0]
        print(f"--- Measuring observed offensive tendencies for Season {season} ---")
        with psycopg.connect(self.db.db_conn_info) as conn:
            with conn.cursor() as cur:
                observed = compute_observed(fetch_inputs(cur, season))
                for t, o in observed.items():
                    cur.execute("""
                        UPDATE team_seasons SET pass_rate_oe = %s, fourth_down_oe = %s, observed_pass_lean = %s,
                               observed_fourth_down = %s, tendency_games = %s
                        WHERE team_id = %s AND season = %s
                    """, (o["pass_rate_oe"], o["fourth_down_oe"], o["observed_pass_lean"],
                          o["observed_fourth_down"], o["tendency_games"], t, season))
                cur.execute("SELECT id, abbreviation FROM teams")
                abbr = dict(cur.fetchall())
            conn.commit()
        for t, o in sorted(observed.items(), key=lambda kv: -(kv[1]["pass_rate_oe"] or -9)):
            pr = f"{o['pass_rate_oe']:+.3f}" if o["pass_rate_oe"] is not None else "  n/a"
            fd = f"{o['fourth_down_oe']:+.2f}" if o["fourth_down_oe"] is not None else " n/a"
            print(f"  {abbr.get(t, t):<4} pass {pr} {o['observed_pass_lean'] or '-':<6} | "
                  f"4th {fd} {o['observed_fourth_down'] or '-':<6} | {o['tendency_games']} games")
        return observed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, help="default: league_state.current_season")
    TendencyEngine().calculate(season=parser.parse_args().season)
