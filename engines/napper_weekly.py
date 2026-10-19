"""Weekly Napper step (docs/NAPPER_WEEKLY_IMPLEMENTATION_PLAN.md, Phase 5).

Run after the week's fantasy projections are written (engines/game_stat_generator.py calls it right after
the simulated leagues). For every IN_SEASON Napper league of the current season, for the upcoming week:

1. AI lineups (engines/napper_lineup_engine): IR moves and starters, so trades see current rosters.
2. AI trade proposals (engines/napper_trade_engine.generate_trade_proposals), once per league and week:
   skipped when the league already has AI proposals for that week, so a re-run adds no duplicate offers
   or banter.
3. Pending offers to AI teams are accepted or rejected (process_ai_to_ai_trades).
4. AI lineups again: accepted trades move players to the bench, so lineups are rebuilt.
5. napper_leagues.current_week = week.

Re-running a week only re-checks lineups. Offers to the human team stay pending for the user.

CLI:
    python3 -m engines.napper_weekly                    # current season, first week with scheduled games
    python3 -m engines.napper_weekly --week 5 --league 15
    python3 -m engines.napper_weekly --no-trades         # lineups only (the preseason step uses this)
    python3 -m engines.napper_weekly --dry-run           # print what would happen, roll back
"""

import argparse
from typing import List, Optional

import psycopg

from engines.napper_lineup_engine import DATABASE_URL, active_league_ids, optimize_league_lineups
from engines.napper_trade_engine import generate_trade_proposals, process_ai_to_ai_trades


def upcoming_week(cur, season: int) -> Optional[int]:
    """First week of the season that still has scheduled games (the week projections were just written for)."""
    cur.execute("SELECT MIN(week) FROM games WHERE season = %s AND status = 'SCHEDULED'", (season,))
    return cur.fetchone()[0]


def run_league_week(conn, league_id: int, week: int, trades: bool = True, log=print) -> dict:
    """The weekly step for one league on an open connection (the caller commits)."""
    summary = {"league": league_id, "week": week, "lineup_teams": 0, "ir_moves": 0,
               "proposals": 0, "trades_processed": 0, "trades_skipped": False}
    with conn.cursor() as cur:
        results = optimize_league_lineups(cur, league_id, week)
        summary["lineup_teams"] = len(results)
        summary["ir_moves"] = sum(len(r["to_ir"]) for r in results)
        if trades:
            cur.execute("SELECT 1 FROM napper_trades WHERE league_id = %s AND week = %s AND initiated_by_ai LIMIT 1",
                        (league_id, week))
            if cur.fetchone():
                summary["trades_skipped"] = True
            else:
                summary["proposals"] = len(generate_trade_proposals(league_id, week, conn=conn) or [])
            summary["trades_processed"] = len(process_ai_to_ai_trades(league_id, week, conn=conn) or [])
            optimize_league_lineups(cur, league_id, week)
        cur.execute("UPDATE napper_leagues SET current_week = %s WHERE id = %s", (week, league_id))
    log(f"   Napper league {league_id}, week {week}: lineups for {summary['lineup_teams']} AI teams "
        f"({summary['ir_moves']} to IR)"
        + ("" if not trades else
           f"; {'proposals already made' if summary['trades_skipped'] else str(summary['proposals']) + ' proposals'}, "
           f"{summary['trades_processed']} offers to AI teams decided"))
    return summary


def run_napper_week(season: Optional[int] = None, week: Optional[int] = None, league_ids: Optional[List[int]] = None,
                    trades: bool = True, conn_info: Optional[str] = None, dry_run: bool = False,
                    log=print) -> List[dict]:
    """Runs the weekly step for every active league (or `league_ids`). Each league commits on its own, so one
    failing league doesn't undo the others; with dry_run everything is rolled back."""
    summaries = []
    with psycopg.connect(conn_info or DATABASE_URL) as conn:
        conn._no_commit = dry_run          # the trade engine commits on its own unless this is set
        with conn.cursor() as cur:
            if season is None:
                cur.execute("SELECT current_season FROM league_state WHERE id = 1")
                season = int(cur.fetchone()[0])
            if week is None:
                week = upcoming_week(cur, season)
            if week is None:
                log(f"   Napper: no scheduled games left in {season}; nothing to do.")
                return summaries
            leagues = league_ids or active_league_ids(cur)
        for league_id in leagues:
            try:
                summaries.append(run_league_week(conn, league_id, week, trades, log))
                conn.rollback() if dry_run else conn.commit()
            except Exception as e:
                conn.rollback()
                log(f"   [WARN] Napper league {league_id}, week {week} failed: {e!r}")
    return summaries


def main():
    parser = argparse.ArgumentParser(description="Weekly Napper step: AI lineups and trades")
    parser.add_argument("--season", type=int)
    parser.add_argument("--week", type=int, help="Default: first week with scheduled games")
    parser.add_argument("--league", type=int, action="append", help="League id (repeatable; default: all active)")
    parser.add_argument("--no-trades", action="store_true", help="Lineups only")
    parser.add_argument("--dry-run", action="store_true", help="Roll back instead of committing")
    args = parser.parse_args()
    run_napper_week(args.season, args.week, args.league, trades=not args.no_trades, dry_run=args.dry_run)
    if args.dry_run:
        print("Dry run: rolled back.")


if __name__ == "__main__":
    main()
