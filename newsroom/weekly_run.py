"""Weekly national newsroom run: publishes the Editorial Matrix slate for the upcoming week.

A Week N slate is published *before* Week N kicks off: recaps cover Week N-1 and previews cover Week N
(see TEMPORAL_ARTICLE_MODES in newsroom/swarm_dispatcher.py). This module decides which week that is,
skips weeks that already have a national slate, and stops two runs from writing the same week at once.

It is called by the video pipeline after the stats sync (pipeline/main.py) and can be run by hand:

    python3 -m newsroom.weekly_run                    # next week to publish, skip if already published
    python3 -m newsroom.weekly_run --week 1           # preseason slate, before any games are played
    python3 -m newsroom.weekly_run --week 23          # Super Bowl week (no games complete Week 22)
    python3 -m newsroom.weekly_run --week 7 --force   # publish again even though Week 7 exists
    python3 -m newsroom.weekly_run --dry-run          # print the plan and prompts; no Gemini calls, no writes
"""
import argparse

DEFAULT_SEASON = 2035
DEFAULT_MODEL = "gemini-3.8-flash"  # same default as SwarmDispatcher
FINAL_WEEK = 23                 # Super Bowl week (see POSTSEASON_EDITORIAL_SLATES)
NATIONAL_SLOT_FILTER = "(slot IN ('Hero Slot', 'Standalone') OR slot LIKE 'Slot %%')"  # local wakeups use 'Local Beat N'
ADVISORY_LOCK_NAMESPACE = 470_118  # arbitrary constant for pg_try_advisory_lock(namespace, season*100 + week)


def publication_week(week_rows):
    """Week whose slate should be published next, from per-week game counts.

    week_rows: iterable of (week, non_bye_games, completed_games).
    A week counts as finished once all of its non-bye games are completed. The next slate is the latest
    finished week + 1, or Week 1 if nothing has finished. Returns None once the Super Bowl is finished.
    Partially played weeks don't count, so a stats capture mid-week maps to a slate that already exists.
    """
    finished = [w for w, total, done in week_rows if total > 0 and done == total]
    if not finished:
        return 1
    next_week = max(finished) + 1
    return next_week if next_week <= FINAL_WEEK else None


def _week_rows(cur, season: int):
    cur.execute("""
        SELECT week,
               COUNT(*) FILTER (WHERE status <> 'BYE')       AS non_bye_games,
               COUNT(*) FILTER (WHERE status = 'COMPLETED')  AS completed_games
        FROM games
        WHERE season = %s
        GROUP BY week
        ORDER BY week;
    """, (season,))
    return cur.fetchall()


def national_slate_exists(cur, season: int, week: int) -> bool:
    cur.execute(f"""
        SELECT 1 FROM news_feed
        WHERE season = %s AND week = %s AND content_type = 'ARTICLE' AND {NATIONAL_SLOT_FILTER}
        LIMIT 1;
    """, (season, week))
    return cur.fetchone() is not None


def run_weekly_swarm(
    season: int = DEFAULT_SEASON,
    week: int = None,
    force: bool = False,
    dry_run: bool = False,
    db_conn_info: str = None,
    model: str = None,
    max_workers: int = 5,
) -> dict:
    """Publishes the national slate for `week` (default: the next week to publish).

    Returns {"season", "week", "status"} where status is "published", "dry_run", "skipped_exists",
    "skipped_locked" or "season_complete"."""
    import psycopg
    from db.db_handler import DatabaseHandler
    from newsroom.swarm_dispatcher import SwarmDispatcher, build_weekly_assignment_brief

    conn_info = db_conn_info or DatabaseHandler().db_conn_info

    # Held for the whole run so a second trigger for the same week can't start while this one writes.
    with psycopg.connect(conn_info, autocommit=True) as lock_conn:
        with lock_conn.cursor() as cur:
            if week is None:
                week = publication_week(_week_rows(cur, season))
                if week is None:
                    print(f"[Newsroom] Season {season} is complete; no slate to publish.")
                    return {"season": season, "week": None, "status": "season_complete"}
            result = {"season": season, "week": week}

            if not dry_run and not force and national_slate_exists(cur, season, week):
                print(f"[Newsroom] Season {season} Week {week} national slate already published; skipping.")
                return {**result, "status": "skipped_exists"}

            cur.execute("SELECT pg_try_advisory_lock(%s, %s);", (ADVISORY_LOCK_NAMESPACE, season * 100 + week))
            if not cur.fetchone()[0]:
                print(f"[Newsroom] Season {season} Week {week} slate is already being generated; skipping.")
                return {**result, "status": "skipped_locked"}

            try:
                # Re-check under the lock: another run may have finished between the check and the lock.
                if not dry_run and not force and national_slate_exists(cur, season, week):
                    print(f"[Newsroom] Season {season} Week {week} national slate already published; skipping.")
                    return {**result, "status": "skipped_exists"}

                print(f"[Newsroom] Publishing Season {season} Week {week} national slate"
                      f"{' (dry run)' if dry_run else ''}...")
                dispatcher = SwarmDispatcher(debug_mode=dry_run)
                if not dry_run:
                    # Agents' "season so far" memories, through last week, before any prompt is built
                    from newsroom.agent_memory import refresh_season_summaries
                    n = refresh_season_summaries(conn_info, dispatcher.api_key, model or DEFAULT_MODEL, season, week)
                    print(f"[Newsroom] Refreshed {n} agent season summaries through Week {week - 1}.")
                brief = build_weekly_assignment_brief(conn_info, season=season, week=week)
                kwargs = {"model": model} if model else {}
                dispatcher.dispatch_weekly_national(
                    brief, season=season, week=week,
                    live=not dry_run, save_to_db=not dry_run, max_workers=max_workers, **kwargs,
                )
                return {**result, "status": "dry_run" if dry_run else "published"}
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s, %s);", (ADVISORY_LOCK_NAMESPACE, season * 100 + week))


def main():
    parser = argparse.ArgumentParser(description="Publish the weekly national newsroom slate.")
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    parser.add_argument("--week", type=int, default=None,
                        help="Slate week to publish (default: latest fully completed week + 1)")
    parser.add_argument("--force", action="store_true", help="Publish even if the week already has a national slate")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and prompts; no Gemini calls, no DB writes")
    parser.add_argument("--model", default=None, help="Override the Gemini model")
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()
    res = run_weekly_swarm(args.season, args.week, force=args.force, dry_run=args.dry_run,
                           model=args.model, max_workers=args.workers)
    print(f"[Newsroom] Result: {res}")


if __name__ == "__main__":
    main()
