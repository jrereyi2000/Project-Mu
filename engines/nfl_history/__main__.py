"""CLI for real NFL history (docs/in_progress/REAL_NFL_HISTORY_PLAN.md §2.9, §2.12; PROJECT_SPEC §1.2).

  python3 -m engines.nfl_history fetch          [--seasons 1999-2025] [--refresh] [--awards]
  python3 -m engines.nfl_history load           --db DB [--seasons ...] [--only players,stats,games,teams,awards,draft]
                                                [--fetch] [--apply]
  python3 -m engines.nfl_history link           --db DB [--season 2026] [--snapshot PATH] [--apply]
  python3 -m engines.nfl_history coach-baseline --db DB [--apply]
  python3 -m engines.nfl_history init           --db DB [--snapshot PATH] [--apply]   # load, link, coach-baseline
  python3 -m engines.nfl_history check          --db DB                               # record book cross-check
  python3 -m engines.nfl_history status         --db DB

Without --apply every command prints what it would write and writes nothing (the link report is still written).
`load` reads only the cache (fill it with `fetch`) unless --fetch is given; awards come from the committed
data/history/nfl_awards_1999_2025.csv. The draft is loaded through DRAFT_LAST_SEASON (2026: Madden 27's real
rookies). --db takes a database name or DSN; madden and madden_2036 are refused.
"""
import argparse
import collections
import csv
import sys

from engines.nfl_history import awards, fetch, nflverse, store

KINDS = ("players", "stats", "games", "teams", "awards", "draft")
FIRST, LAST = 1999, 2025


def seasons_arg(s):
    a, _, b = s.partition("-")
    first, last = int(a), int(b or a)
    if first > last:
        raise argparse.ArgumentTypeError(f"bad season range {s!r}")
    return range(first, last + 1)


def _csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


# ---------- load ----------

def parse_all(seasons, only=KINDS, refresh_fetch=False):
    """Everything a load writes, parsed from the cache (pure apart from reading files): ({kind: rows}, files,
    report). Team columns still hold Mu abbreviations."""
    seasons = range(min(seasons), max(seasons) + 1)
    files = fetch.fetch_all(seasons) if refresh_fetch else fetch.cached(seasons)
    path = {f.cache_name: f.path for f in files}
    out, report = {}, {}
    players = nflverse.parse_players(_csv(path["nflverse_players.csv"]))
    lines, skipped, raw_stats = [], [], []
    if {"stats", "players"} & set(only):
        for s in seasons:
            for t in ("REG", "POST"):
                rows = _csv(path[fetch.nflverse_name(fetch.stats_url(s, t))])
                raw_stats += rows
                lines += nflverse.parse_player_seasons(rows, s, t, skipped)
    known = {p["gsis_id"] for p in players}
    stubs = [p for p in nflverse.players_from_stats(raw_stats) if p["gsis_id"] not in known]
    out["players"] = players + stubs
    known |= {p["gsis_id"] for p in stubs}
    report["players"] = {"players_csv": len(players), "from_stats_only": len(stubs)}
    if "stats" in only:
        out["stats"] = lines
        report["stats"] = {"skipped_no_team": skipped, "tackle_coverage": nflverse.coverage(lines)}
    games = nflverse.parse_games(_csv(path["nflverse_games.csv"]), seasons)
    if "games" in only:
        out["games"] = games
    if "teams" in only:
        std = _csv(path["nfldata_standings.csv"])
        out["teams"] = {"team_seasons": nflverse.team_seasons(games, std),
                        "coach_seasons": nflverse.coach_seasons(games)}
        report["teams"] = dict(collections.Counter(r["standings_source"] for r in out["teams"]["team_seasons"]))
    if "awards" in only:
        rows = []
        for r in awards.read_csv():
            if r["season"] not in seasons:
                continue
            code = r.get("team_abbr")
            try:
                team = nflverse.mu_abbr(code, r["season"]) if code else None
            except ValueError:
                team = None
            rows.append({"season": r["season"], "award_type": r["award_type"],
                         "gsis_id": r["gsis_id"] if r.get("gsis_id") in known else None, "pfr_id": r.get("pfr_id"),
                         "player_name": r.get("player_name"), "coach_name": r.get("coach_name"), "team_id": team,
                         "team_abbr": code, "position": r.get("position")})
        out["awards"] = rows
        report["awards"] = {"without_gsis": sum(1 for r in rows if not r["gsis_id"] and not r["coach_name"])}
    if "draft" in only:
        draft_seasons = range(min(seasons), max(max(seasons), fetch.DRAFT_LAST_SEASON) + 1)
        picks = nflverse.parse_draft(_csv(path["nflverse_draft_picks.csv"]), draft_seasons)
        for p in picks:
            if p["gsis_id"] not in known:
                p["gsis_id"] = None
        out["draft"] = picks
    return out, files, report


def counts_of(parsed):
    c = {}
    for k, v in parsed.items():
        c[k] = {t: len(r) for t, r in v.items()} if isinstance(v, dict) else len(v)
    return c


def team_ids(cur):
    cur.execute("SELECT abbreviation, id FROM teams")
    return {r[0]: r[1] for r in (tuple(x.values()) if isinstance(x, dict) else x for x in cur.fetchall())}


def write_all(cur, parsed, seasons, files, report):
    """Write the parsed kinds in one transaction (the caller commits); one nfl_history_loads row per kind."""
    ids = team_ids(cur)
    first, last = min(seasons), max(seasons)
    written = {}
    if "players" in parsed:
        written["players"] = store.upsert_players(cur, parsed["players"])
        store.record_load(cur, "players", None, None, files[:1], {"rows": written["players"]},
                          report.get("players", {}))
    for kind in ("stats", "games", "teams", "awards", "draft"):
        if kind not in parsed:
            continue
        rows = parsed[kind]
        lo, hi = (first, max(last, fetch.DRAFT_LAST_SEASON)) if kind == "draft" else (first, last)
        if kind == "teams":
            nflverse.resolve_team_ids(rows["team_seasons"], ids)
            nflverse.resolve_team_ids(rows["coach_seasons"], ids)
        else:
            nflverse.resolve_team_ids(rows, ids)
        written[kind] = store.replace_kind(cur, kind, lo, hi, rows)
        store.record_load(cur, kind, lo, hi, files, written[kind], report.get(kind, {}))
    return written


# ---------- commands ----------

def _print_counts(title, counts):
    print(title)
    for k, v in counts.items():
        print(f"  {k:8} {v}")


def cmd_fetch(args):
    files = fetch.fetch_all(args.seasons, args.refresh)
    print(f"{len(files)} nflverse files cached in {fetch.CACHE_DIR}")
    if args.awards:
        rows = awards.build(args.seasons, args.refresh)
        print(f"{len(rows)} award rows written to {awards.AWARDS_CSV}")
    return 0


def do_load(conn, args):
    only = tuple(k.strip() for k in args.only.split(",")) if args.only else KINDS
    bad = set(only) - set(KINDS)
    if bad:
        raise SystemExit(f"--only: unknown kinds {sorted(bad)}")
    parsed, files, report = parse_all(args.seasons, only, getattr(args, "fetch", False))
    parsed = {k: v for k, v in parsed.items() if k in only}
    _print_counts(f"load {min(args.seasons)}-{max(args.seasons)}" + ("" if args.apply else " (dry run)"),
                  counts_of(parsed))
    if report.get("stats", {}).get("skipped_no_team"):
        print(f"  skipped (no team code): {report['stats']['skipped_no_team']}")
    if not args.apply:
        return None
    with conn.cursor() as cur:
        written = write_all(cur, parsed, args.seasons, files, report)
    conn.commit()
    return written


def do_link(conn, args):
    from engines.nfl_history import link
    with conn.cursor() as cur:
        season = args.season
        if season is None:
            cur.execute("SELECT current_season FROM league_state WHERE id = 1")
            row = cur.fetchone()
            season = row[0] if row else 2026
        result = link.run_link(cur, season, args.snapshot, apply=args.apply, db_label=store.db_name(args.db))
    if args.apply:
        conn.commit()
    counts = collections.Counter(l["method"] for l in result.links)
    print(f"link season {season}" + ("" if args.apply else " (dry run)") + f": {len(result.links)} links "
          f"{dict(counts)}, {len(result.ambiguous)} ambiguous, {len(result.unmatched)} unmatched, "
          f"{len(result.refused)} refused overrides")
    print(f"  report: {result.report_paths[0]}")
    return result


def do_coach_baseline(conn, args):
    from engines.nfl_history import coach_baseline as cb
    with conn.cursor() as cur:
        current, every, nfl_rows = cb.read_inputs(cur)
        rows = cb.baseline_rows(current, nfl_rows)
        print(f"coach baseline {cb.BASELINE_SEASON}" + ("" if args.apply else " (dry run)") + f": {len(rows)} coaches")
        for r in rows:
            print(f"  {r['coach_name']:24} {r['career_wins']}-{r['career_losses']}-{r['career_ties']}"
                  f"  playoffs {r['playoff_wins']}-{r['playoff_losses']}  SB {r['super_bowl_wins']}-"
                  f"{r['super_bowl_losses']}" + ("  (first-time head coach)" if r["first_time"] else ""))
        if not args.apply:
            return rows
        cb.apply_baseline(cur, rows, every)
        store.record_load(cur, "coach_baseline", cb.BASELINE_SEASON, cb.BASELINE_SEASON, [],
                          {"coaches": len(rows), "first_time": sum(1 for r in rows if r["first_time"])}, {})
    conn.commit()
    return rows


def cmd_load(args):
    with store.connect(args.db) as conn:
        do_load(conn, args)
    return 0


def cmd_link(args):
    with store.connect(args.db) as conn:
        do_link(conn, args)
    return 0


def cmd_coach_baseline(args):
    with store.connect(args.db) as conn:
        do_coach_baseline(conn, args)
    return 0


def cmd_init(args):
    """load, link, coach-baseline in that order on one connection (each step commits when --apply)."""
    args.only, args.season = None, getattr(args, "season", None)
    with store.connect(args.db) as conn:
        do_load(conn, args)
        if not args.apply:
            print("(dry run: link and coach-baseline below read the tables as they are now, without this load)")
        do_link(conn, args)
        do_coach_baseline(conn, args)
    return 0


def cmd_check(args):
    from engines.nfl_history import check
    with store.connect(args.db) as conn, conn.cursor() as cur:
        mismatches, checked = check.record_check(cur)
        path = check.write_report(mismatches, checked, store.db_name(args.db))
        conn.rollback()
    print(f"record check: {checked} rows, {len(mismatches)} differences; report {path}")
    return 0


def cmd_status(args):
    from engines.nfl_history import read
    with store.connect(args.db) as conn, conn.cursor() as cur:
        if not read.installed(cur):
            print("nfl_* tables not installed (apply db/migrations/2027_02_04_nfl_history.sql)")
            return 1
        for t in ("nfl_players", "nfl_player_seasons", "nfl_games", "nfl_team_seasons", "nfl_coach_seasons",
                  "nfl_awards", "nfl_draft_picks", "nfl_player_links"):
            cur.execute(f"SELECT count(*) FROM {t}")
            print(f"  {t:20} {cur.fetchone()[0]}")
        cur.execute("SELECT method, count(*) FROM nfl_player_links GROUP BY method ORDER BY method")
        print("  links by method:", dict(cur.fetchall()))
        cur.execute("""SELECT DISTINCT ON (kind) kind, first_season, last_season, finished_at, counts
                       FROM nfl_history_loads ORDER BY kind, id DESC""")
        for kind, a, b, at, counts in cur.fetchall():
            print(f"  last {kind:14} {a}-{b} at {at}: {counts}")
        conn.rollback()
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="python3 -m engines.nfl_history",
                                 description="Real NFL history 1999-2025 in the nfl_* tables (dry run without --apply).")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="{fetch,load,link,coach-baseline,init,check,status}")

    def with_db(p):
        p.add_argument("--db", required=True, help="database name or DSN (madden and madden_2036 are refused)")
        return p

    p = sub.add_parser("fetch", help="download the nflverse files into the cache (network)")
    p.add_argument("--seasons", type=seasons_arg, default=range(FIRST, LAST + 1))
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--awards", action="store_true", help="also rebuild the awards CSV from Wikipedia")
    p.set_defaults(func=cmd_fetch)

    p = with_db(sub.add_parser("load", help="write players, stats, games, teams, awards and draft"))
    p.add_argument("--seasons", type=seasons_arg, default=range(FIRST, LAST + 1))
    p.add_argument("--only", help="comma list of " + ",".join(KINDS))
    p.add_argument("--fetch", action="store_true", help="download missing files instead of failing")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_load)

    p = with_db(sub.add_parser("link", help="link Madden players to real players and write the review report"))
    p.add_argument("--season", type=int, help="default league_state.current_season")
    p.add_argument("--snapshot", help="pc_save_reader snapshot JSON (draft year, college)")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_link)

    p = with_db(sub.add_parser("coach-baseline", help="write the 2025 coach_seasons baseline"))
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_coach_baseline)

    p = with_db(sub.add_parser("init", help="load, link and coach-baseline"))
    p.add_argument("--seasons", type=seasons_arg, default=range(FIRST, LAST + 1))
    p.add_argument("--snapshot", help="pc_save_reader snapshot JSON")
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_init)

    p = with_db(sub.add_parser("check", help="cross-check the seeded record book (read-only)"))
    p.set_defaults(func=cmd_check)

    p = with_db(sub.add_parser("status", help="row counts, link counts and the last load per kind"))
    p.set_defaults(func=cmd_status)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except store.RefusedDatabase as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
