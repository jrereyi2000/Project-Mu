#!/usr/bin/env python3
"""
One-time rebuild of a season's backfilled game lines from corrected season totals
(docs/PIPELINE_MIGRATION.md, phase 3; JR, Oct 14).

  python3 tools/rebuild_backfill.py --db madden_p3test --season 2035 [--keep-team Panthers]
  python3 tools/rebuild_backfill.py --db madden --season 2035 --yes      # live: after ./db/backup_db.sh

The old ingestion left season_stats lines under the wrong players, and the backfill had already
shared those totals out over weeks 1-16; the backfill only ever fills games with no lines, so it
can't correct them. This deletes the season's player_stats and team_stats rows for every completed
game the kept team (default Panthers: JR's games, their lines are real) didn't play, then runs the
backfill (engines.game_stat_generator) so every other game is filled from the corrected totals.
On a database other than the live one the analytics engines are blocked (they write to the live
database); on the live one they run, as after any weekly load.
"""
import argparse, os, sys

import psycopg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project root
from tools.check_capture_load import ENGINES, check  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--keep-team", default="Panthers", help="team whose games' lines are real and kept")
    ap.add_argument("--yes", action="store_true", help="required for the live database")
    a = ap.parse_args()
    live = a.db == "madden"
    if live and not a.yes:
        sys.exit("This rewrites the live database's game lines: run ./db/backup_db.sh, then add --yes")
    with psycopg.connect(f"host=localhost dbname={a.db} user=postgres password=postgres") as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM teams WHERE name = %s", (a.keep_team,))
        keep = cur.fetchone()
        if not keep:
            sys.exit(f"No team {a.keep_team!r}")
        cur.execute("""SELECT id FROM games WHERE season = %s AND status = 'COMPLETED'
                       AND %s NOT IN (home_team_id, away_team_id)""", (a.season, keep[0]))
        ids = [r[0] for r in cur.fetchall()]
        cur.execute("DELETE FROM player_stats WHERE game_id = ANY(%s)", (ids,))
        n_ps = cur.rowcount
        cur.execute("DELETE FROM team_stats WHERE game_id = ANY(%s)", (ids,))
        n_ts = cur.rowcount
        conn.commit()
    print(f"Cleared {n_ps} player_stats and {n_ts} team_stats rows from {len(ids)} games "
          f"(kept every {a.keep_team} game); running the backfill")
    if not live:
        for name in ENGINES:
            sys.modules[name] = None             # analytics would write to the live database
    from sqlalchemy import create_engine
    import engines.game_stat_generator as g
    url = f"postgresql://postgres:postgres@localhost:5432/{a.db}"
    g.DATABASE_URL, g.engine = url, create_engine(url)
    g.generate_team_and_player_stats(season_year=a.season)
    sys.exit(0 if check(a.db, a.season) else 1)


if __name__ == "__main__":
    main()
