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
    python3 -m newsroom.weekly_run --fantasy-only     # only the Mu Fantasy slate (docs/FANTASY_EDITORIAL_MATRIX.md)
    python3 -m newsroom.weekly_run --skip-fantasy     # national slate and Agent Wire posts, no fantasy slate
    python3 -m newsroom.weekly_run --social-only      # only the Agent Wire posts (docs/SOCIAL_MATRIX.md)
    python3 -m newsroom.weekly_run --skip-social      # national and fantasy slates, no Agent Wire posts

The Mu Fantasy slate (newsroom/fantasy_desk.py) runs after the national slate in the same run, Weeks 1–18,
with its own already-published check (slots 'Fantasy ...'). The week's ~100 Agent Wire posts
(newsroom/social_publish.py) run last, so they can share the week's articles; their check is any post with
slot 'Agent Wire'.
"""
import argparse

DEFAULT_MODEL = "gemini-3.8-flash"  # same default as SwarmDispatcher
FINAL_WEEK = 23                 # Super Bowl week (see POSTSEASON_EDITORIAL_SLATES)
NATIONAL_SLOT_FILTER = "(slot IN ('Hero Slot', 'Standalone') OR slot LIKE 'Slot %%')"  # local wakeups use 'Local Beat N'
FANTASY_SLOT_FILTER = "slot LIKE 'Fantasy %%'"
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


def _slate_exists(cur, season: int, week: int, slot_filter: str) -> bool:
    cur.execute(f"""
        SELECT 1 FROM news_feed
        WHERE season = %s AND week = %s AND content_type = 'ARTICLE' AND {slot_filter}
        LIMIT 1;
    """, (season, week))
    return cur.fetchone() is not None


def national_slate_exists(cur, season: int, week: int) -> bool:
    return _slate_exists(cur, season, week, NATIONAL_SLOT_FILTER)


def fantasy_slate_exists(cur, season: int, week: int) -> bool:
    return _slate_exists(cur, season, week, FANTASY_SLOT_FILTER)


def run_weekly_swarm(
    season: int = None,
    week: int = None,
    force: bool = False,
    dry_run: bool = False,
    db_conn_info: str = None,
    model: str = None,
    max_workers: int = 5,
    national: bool = True,
    fantasy: bool = True,
    social: bool = True,
) -> dict:
    """Publishes the national slate, the Mu Fantasy slate and then the Agent Wire posts for `week` (default: the
    next week to publish).

    Each slate is skipped when it already exists (unless force). Returns {"season", "week", "status",
    "national", "fantasy", "social"}: status is "published", "dry_run", "skipped_exists", "skipped_locked" or
    "season_complete" for the run as a whole; national/fantasy/social give each part's own status
    ("published", "dry_run", "skipped_exists", "not_requested", "no_fantasy_week")."""
    import psycopg
    from db.db_handler import DatabaseHandler
    from newsroom.fantasy_desk import fantasy_phase
    from newsroom.social_publish import run_social_week, social_slate_exists
    from newsroom.swarm_dispatcher import SwarmDispatcher, build_weekly_assignment_brief

    conn_info = db_conn_info or DatabaseHandler().db_conn_info
    if season is None:
        season = DatabaseHandler(conn_info).get_current_league_state()[0]

    def plan(cur):
        """Which slates still need writing: {'national': status-or-None, 'fantasy': status-or-None}."""
        n = "not_requested" if not national else (
            "skipped_exists" if not dry_run and not force and national_slate_exists(cur, season, week) else None)
        if not fantasy:
            f = "not_requested"
        elif fantasy_phase(week) is None:
            f = "no_fantasy_week"
        else:
            f = "skipped_exists" if not dry_run and not force and fantasy_slate_exists(cur, season, week) else None
        so = "not_requested" if not social else (
            "skipped_exists" if not dry_run and not force and social_slate_exists(cur, season, week) else None)
        return {"national": n, "fantasy": f, "social": so}

    # Held for the whole run so a second trigger for the same week can't start while this one writes.
    with psycopg.connect(conn_info, autocommit=True) as lock_conn:
        with lock_conn.cursor() as cur:
            if week is None:
                week = publication_week(_week_rows(cur, season))
                if week is None:
                    print(f"[Newsroom] Season {season} is complete; no slate to publish.")
                    return {"season": season, "week": None, "status": "season_complete"}
            result = {"season": season, "week": week}

            todo = plan(cur)
            if all(v is not None for v in todo.values()):
                print(f"[Newsroom] Season {season} Week {week}: nothing to publish "
                      f"(national: {todo['national']}, fantasy: {todo['fantasy']}, social: {todo['social']}).")
                return {**result, "status": "skipped_exists", **todo}

            cur.execute("SELECT pg_try_advisory_lock(%s, %s);", (ADVISORY_LOCK_NAMESPACE, season * 100 + week))
            if not cur.fetchone()[0]:
                print(f"[Newsroom] Season {season} Week {week} slate is already being generated; skipping.")
                return {**result, "status": "skipped_locked"}

            try:
                # Re-check under the lock: another run may have finished between the check and the lock.
                todo = plan(cur)
                if all(v is not None for v in todo.values()):
                    print(f"[Newsroom] Season {season} Week {week}: already published; skipping.")
                    return {**result, "status": "skipped_exists", **todo}

                done = "dry_run" if dry_run else "published"
                kwargs = {"model": model} if model else {}
                dispatcher = SwarmDispatcher(debug_mode=dry_run)
                if not dry_run:
                    # Agents' "season so far" memories, through last week, before any prompt is built
                    from newsroom.agent_memory import refresh_season_summaries
                    n = refresh_season_summaries(conn_info, dispatcher.api_key, model or DEFAULT_MODEL, season, week)
                    print(f"[Newsroom] Refreshed {n} agent season summaries through Week {week - 1}.")

                if todo["national"] is None:
                    print(f"[Newsroom] Publishing Season {season} Week {week} national slate"
                          f"{' (dry run)' if dry_run else ''}...")
                    brief = build_weekly_assignment_brief(conn_info, season=season, week=week)
                    dispatcher.dispatch_weekly_national(
                        brief, season=season, week=week,
                        live=not dry_run, save_to_db=not dry_run, max_workers=max_workers, **kwargs,
                    )
                    todo["national"] = done
                else:
                    print(f"[Newsroom] National slate: {todo['national']}.")

                if todo["fantasy"] is None:
                    print(f"[Newsroom] Publishing Season {season} Week {week} Mu Fantasy slate"
                          f"{' (dry run)' if dry_run else ''}...")
                    dispatcher.dispatch_weekly_fantasy(
                        season, week, live=not dry_run, save_to_db=not dry_run, max_workers=max_workers, **kwargs,
                    )
                    todo["fantasy"] = done
                else:
                    print(f"[Newsroom] Fantasy slate: {todo['fantasy']}.")

                if todo["social"] is None:
                    print(f"[Newsroom] Publishing Season {season} Week {week} Agent Wire posts"
                          f"{' (dry run)' if dry_run else ''}...")
                    res = run_social_week(conn_info, season, week, model=model, dry_run=dry_run, force=force,
                                          max_workers=max_workers)
                    todo["social"] = done if res.get("status") in ("published", "dry_run") else res.get("status")
                else:
                    print(f"[Newsroom] Agent Wire posts: {todo['social']}.")
                return {**result, "status": done, **todo}
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s, %s);", (ADVISORY_LOCK_NAMESPACE, season * 100 + week))


def main():
    parser = argparse.ArgumentParser(description="Publish the weekly national newsroom slate and the Mu Fantasy slate.")
    parser.add_argument("--season", type=int, default=None, help="default: league_state.current_season")
    parser.add_argument("--week", type=int, default=None,
                        help="Slate week to publish (default: latest fully completed week + 1)")
    parser.add_argument("--force", action="store_true", help="Publish even if the week already has a slate")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and prompts; no Gemini calls, no DB writes")
    parser.add_argument("--model", default=None, help="Override the Gemini model")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--skip-fantasy", action="store_true", help="Skip the Mu Fantasy slate")
    parser.add_argument("--fantasy-only", action="store_true", help="Publish only the Mu Fantasy slate")
    parser.add_argument("--skip-social", action="store_true", help="Don't write the Agent Wire posts")
    parser.add_argument("--social-only", action="store_true", help="Write only the Agent Wire posts")
    args = parser.parse_args()
    only = [f for f in ("fantasy_only", "social_only") if getattr(args, f)]
    if len(only) > 1:
        parser.error("--fantasy-only and --social-only can't be combined")
    if args.skip_fantasy and args.fantasy_only or args.skip_social and args.social_only:
        parser.error("--skip-X and --X-only can't be combined")
    res = run_weekly_swarm(args.season, args.week, force=args.force, dry_run=args.dry_run,
                           model=args.model, max_workers=args.workers,
                           national=not (args.fantasy_only or args.social_only),
                           fantasy=not (args.skip_fantasy or args.social_only),
                           social=not (args.skip_social or args.fantasy_only))
    print(f"[Newsroom] Result: {res}")


if __name__ == "__main__":
    main()
