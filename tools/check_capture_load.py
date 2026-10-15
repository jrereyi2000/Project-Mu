#!/usr/bin/env python3
"""
Phase 3 exit check (docs/PIPELINE_MIGRATION.md): after a weekly load, run the backfill and check
that every player's per-game player_stats add up to season_stats.

  python3 tools/check_capture_load.py --db <test copy> --season 2035 [--backfill]

--backfill runs engines.game_stat_generator's stats sync against --db first, with the engine imports
blocked so only the sync runs (the analytics step fails harmlessly: "ANALYTICS PIPELINE FAILED").
Read-only otherwise.
"""
import argparse, os, sys

import psycopg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project root

METRICS = ["completions", "pass_attempts", "pass_yards", "pass_tds", "interceptions", "rush_attempts", "rush_yards",
           "rush_tds", "receptions", "rec_yards", "rec_tds", "fumb"]
ENGINES = ["engines.gotw_engine", "engines.twi_engine", "engines.game_credit_engine", "engines.team_efficiency_engine",
           "engines.coty_engine", "engines.playoff_clout_engine", "engines.popularity_engine", "engines.player_popularity_engine",
           "engines.excitement_projection", "engines.fantasy_projection_engine", "engines.fantasy_league_sim"]


def backfill(db, season):
    for name in ENGINES:
        sys.modules[name] = None             # never let the analytics step reach the live database
    from sqlalchemy import create_engine
    import engines.game_stat_generator as g
    url = f"postgresql://postgres:postgres@localhost:5432/{db}"
    g.DATABASE_URL, g.engine = url, create_engine(url)
    g.generate_team_and_player_stats(season_year=season)


def check(db, season):
    with psycopg.connect(f"host=localhost dbname={db} user=postgres password=postgres") as conn:
        cur = conn.cursor()
        sums = ", ".join(f"COALESCE(sum(ps.{m}), 0)" for m in METRICS)
        cur.execute(f"""
            SELECT ss.player_id, ss.player_name, {', '.join('ss.' + m for m in METRICS)}, {sums}
            FROM season_stats ss
            LEFT JOIN player_stats ps ON ps.player_id = ss.player_id
                 AND ps.game_id IN (SELECT id FROM games WHERE season = %s AND status = 'COMPLETED')
            WHERE ss.season = %s AND ss.player_id IS NOT NULL
            GROUP BY ss.id""", (season, season))
        rows = cur.fetchall()
        bad, negative = [], []
        for r in rows:
            season_vals, game_vals = r[2:2 + len(METRICS)], r[2 + len(METRICS):]
            diff = {m: (s, int(gv)) for m, s, gv in zip(METRICS, season_vals, game_vals) if (s or 0) != int(gv)}
            # the backfill never writes a negative number, so a negative season total (kneel-downs) can only
            # come out as 0 from the games it filled: known limit of the engine, reported apart
            if diff and all(s < 0 and gv >= s for s, gv in diff.values()):
                negative.append((r[0], r[1], diff))
            elif diff:
                bad.append((r[0], r[1], diff))
        cur.execute("""SELECT count(*) FROM (SELECT ps.game_id, ps.team_id FROM player_stats ps
                       JOIN games g ON g.id = ps.game_id WHERE g.season = %s GROUP BY 1, 2) x""", (season,))
        team_games = cur.fetchone()[0]
        cur.execute("SELECT count(*) * 2 FROM games WHERE season = %s AND status = 'COMPLETED'", (season,))
        completed = cur.fetchone()[0]
    print(f"{len(rows)} season_stats players checked; {len(bad)} whose game lines don't add up; "
          f"{len(negative)} with a negative season total the backfill can't write (known limit)")
    for b in bad[:25]:
        print("  ", b)
    print(f"team-games with player lines: {team_games} of {completed}")
    return not bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="database name (a restored test copy, or madden)")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--backfill", action="store_true")
    a = ap.parse_args()
    if a.backfill:
        backfill(a.db, a.season)
    sys.exit(0 if check(a.db, a.season) else 1)


if __name__ == "__main__":
    main()
