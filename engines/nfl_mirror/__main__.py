"""Command line for the NFL Weekly Mirror (NFL_WEEKLY_MIRROR_PLAN.md §2.11, §2.10; PROJECT_SPEC §3.3aa).

    python3 -m engines.nfl_mirror status
    python3 -m engines.nfl_mirror preview --week N [--dsn DSN]
    python3 -m engines.nfl_mirror trades --week N [--dsn DSN]
    python3 -m engines.nfl_mirror enable [--last-week N] --confirm DB [--dsn DSN]
    python3 -m engines.nfl_mirror disable --confirm DB [--dsn DSN]

`status` prints the settings row, the last mirrored week and the next real week's mode. `preview` runs the real
week's fetch, build and roster diff against the newest capture snapshot and prints the plan, writing nothing (it
does not plan the created players, injuries or staff changes: those are the league office's write, not a preview).
`trades` prints the unconfirmed switches of a week. `enable` / `disable` only change `nfl_mirror_settings`, and
each needs `--confirm <db>` naming the database its `--dsn` points at, so an enabled mirror is never a typo.

The engine modules are read-only here; every dependency is a module-level wrapper so the tests run offline.
"""
import argparse
import json
import os
import sys

from engines.nfl_mirror import contracts as mirror_contracts
from engines.nfl_mirror import diff as mirror_diff
from engines.nfl_mirror import fetch as mirror_fetch
from engines.nfl_mirror import real_state
from engines.nfl_mirror import store
from engines.nfl_mirror import trades as mirror_trades

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
CAPTURES = os.path.join(ROOT, "captures")


# -------------------------------------------------------------------------------------------- dependencies ---
def _default_dsn():
    from pipeline.load_save import DSN
    return DSN


def resolve_dsn(args):
    """`--dsn`, else DATABASE_URL (the franchise database exported by db/franchises), else Mu's local default."""
    return getattr(args, "dsn", None) or os.environ.get("DATABASE_URL") or _default_dsn()


def _connect(dsn):
    import psycopg
    return psycopg.connect(dsn)


def _db_name(dsn):
    """The database name of a DSN (`dbname=x ...` or a URL) or a bare name."""
    value = str(dsn or "").strip()
    for part in value.split():
        if part.startswith("dbname="):
            return part.split("=", 1)[1]
    if "://" in value:
        return value.rsplit("/", 1)[-1].split("?", 1)[0]
    return value


def _fetch_week(season, refresh=True):
    return mirror_fetch.fetch_week(season, refresh=refresh)


def _read_rows(fetched_file):
    return list(mirror_fetch.read_rows(fetched_file))


def _build_week(rows_rosters, rows_injuries, rows_depth, rows_games, season, week, sources=None):
    return real_state.build_week(rows_rosters, rows_injuries, rows_depth, rows_games, season, week, sources=sources)


def _plan_rosters(snapshot, real, links, trades, team_index, contracts, cap):
    return mirror_diff.plan_rosters(snapshot, real, links, trades, team_index, contracts, cap)


def _read_trades():
    return mirror_trades.read_trades()


def _newest_snapshot():
    """The newest captures/<run>/save.json, by mtime."""
    if not os.path.isdir(CAPTURES):
        raise FileNotFoundError(f"no captures directory at {CAPTURES}")
    paths = [os.path.join(CAPTURES, name, "save.json") for name in os.listdir(CAPTURES)]
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        raise FileNotFoundError(f"no capture snapshots (save.json) under {CAPTURES}")
    return max(paths, key=os.path.getmtime)


def _cached_games_rows(season):
    """The cached nflverse games.csv rows, or None when it isn't cached (status never downloads)."""
    from engines.nfl_history.fetch import cache_path
    path = cache_path(mirror_fetch.file_list(season)["games"][1])
    if not os.path.exists(path):
        return None
    return list(mirror_fetch.read_rows(path))


# ----------------------------------------------------------------------------------------------- helpers ---
def settings_season(cur):
    """The settings row's season (2026 when the tables aren't installed or there is no row)."""
    try:
        settings = store.read_settings(cur)
    except Exception:       # noqa: BLE001 - a read-only status must not fail on an unmigrated database
        settings = None
    return int(settings["season"]) if settings else store.DEFAULT_SEASON


def last_mirrored_week(cur):
    """(season, week, real_week, mode, status) of the newest nfl_mirror_weeks row, or None."""
    try:
        if not store.installed(cur):
            return None
        cur.execute("SELECT season, week, real_week, mode, status FROM nfl_mirror_weeks "
                    "ORDER BY season DESC, week DESC LIMIT 1")
        return cur.fetchone()
    except Exception:       # noqa: BLE001 - an unreadable table reads as "no mirrored week"
        return None


def team_codes(snapshot, season):
    """{save team code: team_index} from the snapshot's teams, through Mu's abbreviations (the diff's shape)."""
    from engines.nfl_history.teams import UnknownTeam, mu_abbr
    from pipeline import load_save as ls
    out = {}
    for team in snapshot.get("teams") or []:
        index = team.get("team_index")
        if index is None or int(index) >= ls.FREE_AGENT_TEAM_INDEX:
            continue
        short = team.get("short_name")
        if not short:
            continue
        short = ls.TEAM_ABBR_ALIASES.get(short, short)
        try:
            code = mu_abbr(short, season)
        except UnknownTeam:
            code = short
        out[code] = int(index)
    return out


def league_cap(snapshot):
    """The league cap in dollars from the snapshot (pipeline.league_office.league_cap)."""
    from pipeline import league_office as lo
    return lo.league_cap(None, snapshot)


def contract_book(files):
    """nflverse OTC's book, empty when the file isn't in this fetch."""
    fetched = files.get("contracts")
    if fetched is None:
        return mirror_contracts.ContractBook([])
    return mirror_contracts.ContractBook.from_otc(getattr(fetched, "path", fetched))


def unconfirmed_for_rows(rows, season, real_week, trades=None):
    """The real week's unconfirmed switches, built from already-read rows (no network)."""
    def build(week):
        return _build_week(rows["rosters"], rows["injuries"], rows["depth"], rows["games"], season, week)

    week = int(real_week)
    switches = mirror_trades.infer_switches(build(week - 1), build(week)) if week > 1 else []
    return mirror_trades.unconfirmed(switches, trades if trades is not None else _read_trades())


# ----------------------------------------------------------------------------------------------- status ---
def status_report(cur, season=None):
    """(lines, exit_code) for `status`."""
    settings = store.read_settings(cur)
    if settings is None:
        return (["NFL mirror settings: not installed (apply db/migrations/2027_02_27_nfl_mirror.sql)"], 1)
    season = int(season or settings["season"])
    last_week = settings["last_week"]
    lines = [f"NFL mirror: {'enabled' if settings['enabled'] else 'disabled'}  season {season}  "
             f"last_week {last_week if last_week is not None else 'through the Super Bowl'}  "
             f"injuries {settings['injuries_state']}"]
    last = last_mirrored_week(cur)
    if last is None:
        lines.append("last mirrored week: none")
        mu_week = 1
    else:
        last_season, week, real_week, mode, status = last
        lines.append(f"last mirrored week: season {last_season} week {week} "
                     f"(real week {real_week}, {mode}, {status or 'planned'})")
        mu_week = int(week) + 1
    try:
        real_week = real_state.real_week_for(mu_week)
    except ValueError:
        lines.append(f"next real week: none (Mu week {mu_week} is the Pro Bowl or past the Super Bowl)")
    else:
        games = _cached_games_rows(season)
        mode = real_state.week_mode(games, season, real_week) if games is not None else None
        lines.append(f"next real week: Mu week {mu_week} -> nflverse week {real_week}, "
                     f"mode {mode or 'unknown (games.csv not cached)'}")
    return lines, 0


def cmd_status(args):
    with _connect(resolve_dsn(args)) as conn, conn.cursor() as cur:
        lines, code = status_report(cur)
    for line in lines:
        print(line)
    return code


# ----------------------------------------------------------------------------------------------- preview ---
def preview_plan(cur, snapshot, season, week):
    """The real week built and diffed against `snapshot`, writing nothing.

    Returns {"real", "roster", "unconfirmed", "snapshot"}; it deliberately stops at the roster diff (creates,
    injuries and staff changes belong to the league office's plan and write)."""
    real_week = real_state.real_week_for(week)
    files = _fetch_week(season, refresh=True)
    rows = {name: _read_rows(files[name]) for name in ("rosters", "injuries", "depth", "games")}
    real = _build_week(rows["rosters"], rows["injuries"], rows["depth"], rows["games"], season, real_week,
                       sources=mirror_fetch.sources(files))
    links = dict(store.links(cur) or {})
    roster = _plan_rosters(snapshot, real, links, _read_trades(), team_codes(snapshot, season),
                           contract_book(files), league_cap(snapshot))
    return {"real": real, "roster": roster, "snapshot": snapshot,
            "unconfirmed": unconfirmed_for_rows(rows, season, real_week)}


def print_plan(plan, snapshot_path):
    real, roster = plan["real"], plan["roster"]
    print(f"NFL mirror preview from {snapshot_path}")
    print(f"  real week {real.week} ({real.mode}); {len(real.rosters or {})} real team(s)")
    ops = list(getattr(roster, "ops", None) or [])
    print(f"  roster ops: {len(ops)}")
    for op in ops:
        print(f"    {str(op.get('kind') or '').upper():8} gsis {op.get('gsis_id') or '-':12} "
              f"pgid {op.get('pgid')!s:>5} -> team {op.get('to_index')}  {op.get('why') or ''}")
    creates = list(getattr(roster, "creates", None) or [])
    print(f"  creates: {len(creates)}")
    for player in creates:
        name = getattr(player, "name", None) or (player.get("name") if isinstance(player, dict) else player)
        position = getattr(player, "position", None) or (player.get("position") if isinstance(player, dict) else "")
        print(f"    {name} ({position})")
    print(f"  practice-squad fills: {len(getattr(roster, 'ps_fill', None) or [])}")
    print(f"  review candidates: {len(getattr(roster, 'review', None) or [])}")
    unconfirmed = list(plan["unconfirmed"] or [])
    print(f"  unconfirmed switches: {len(unconfirmed)}")
    for switch in unconfirmed:
        print(f"    {switch.name} {switch.from_team} -> {switch.to_team} (week {switch.week})")
    print("  (preview only: nothing written)")


def cmd_preview(args):
    snapshot_path = _newest_snapshot()
    with open(snapshot_path) as fh:
        snapshot = json.load(fh)
    with _connect(resolve_dsn(args)) as conn, conn.cursor() as cur:
        plan = preview_plan(cur, snapshot, settings_season(cur), args.week)
    print_plan(plan, snapshot_path)
    return 0


# ------------------------------------------------------------------------------------------------ trades ---
def cmd_trades(args):
    with _connect(resolve_dsn(args)) as conn, conn.cursor() as cur:
        season = settings_season(cur)
    real_week = real_state.real_week_for(args.week)
    files = _fetch_week(season, refresh=False)
    rows = {name: _read_rows(files[name]) for name in ("rosters", "injuries", "depth", "games")}
    unconfirmed = unconfirmed_for_rows(rows, season, real_week)
    print(f"NFL mirror week {args.week} (real week {real_week}): {len(unconfirmed)} unconfirmed switch(es)")
    for switch in unconfirmed:
        print(f"  {switch.name:24} {switch.from_team} -> {switch.to_team} (week {switch.week}, {switch.status})")
    return 0


# ---------------------------------------------------------------------------------------- enable/disable ---
def _confirmed(args):
    target = _db_name(resolve_dsn(args))
    if (args.confirm or "").strip() != target:
        print(f"refused: --confirm must name the database the DSN points at ({target!r})", file=sys.stderr)
        return None
    return target


def cmd_enable(args):
    target = _confirmed(args)
    if target is None:
        return 2
    if args.last_week is not None and args.last_week < 1:
        print("refused: --last-week is a Mu week number (1 or later)", file=sys.stderr)
        return 2
    with _connect(resolve_dsn(args)) as conn, conn.cursor() as cur:
        store.set_enabled(cur, True, season=store.DEFAULT_SEASON, last_week=args.last_week)
        conn.commit()
    through = f"through Mu week {args.last_week}" if args.last_week is not None else "through the Super Bowl"
    print(f"NFL mirror enabled on {target}: season {store.DEFAULT_SEASON} {through}")
    return 0


def cmd_disable(args):
    target = _confirmed(args)
    if target is None:
        return 2
    with _connect(resolve_dsn(args)) as conn, conn.cursor() as cur:
        store.set_enabled(cur, False, season=store.DEFAULT_SEASON, last_week=None)
        conn.commit()
    print(f"NFL mirror disabled on {target}")
    return 0


# -------------------------------------------------------------------------------------------------- main ---
def build_parser():
    ap = argparse.ArgumentParser(
        prog="python3 -m engines.nfl_mirror",
        description="NFL Weekly Mirror: status, a read-only preview, the week's unconfirmed switches, and the "
                    "settings switch (each ON/OFF change needs --confirm naming the database).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="{status,preview,trades,enable,disable}")

    p = sub.add_parser("status", help="settings, the last mirrored week and the next real week's mode")
    p.add_argument("--dsn", help="database DSN (default: DATABASE_URL, else Mu's local default)")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("preview", help="fetch, build and diff a real week against the newest snapshot (writes nothing)")
    p.add_argument("--week", type=int, required=True, metavar="N", help="Mu week number")
    p.add_argument("--dsn", help="database DSN (default: DATABASE_URL, else Mu's local default)")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("trades", help="the week's team-to-team switches with no hand-kept trade leg")
    p.add_argument("--week", type=int, required=True, metavar="N", help="Mu week number")
    p.add_argument("--dsn", help="database DSN (default: DATABASE_URL, else Mu's local default)")
    p.set_defaults(func=cmd_trades)

    p = sub.add_parser("enable", help="turn the mirror on (needs --confirm <database>)")
    p.add_argument("--last-week", type=int, metavar="N",
                   help="stop mirroring after Mu week N (default: through the Super Bowl)")
    p.add_argument("--confirm", required=True, metavar="DB", help="the database name the --dsn points at")
    p.add_argument("--dsn", help="database DSN (default: DATABASE_URL, else Mu's local default)")
    p.set_defaults(func=cmd_enable)

    p = sub.add_parser("disable", help="turn the mirror off (needs --confirm <database>)")
    p.add_argument("--confirm", required=True, metavar="DB", help="the database name the --dsn points at")
    p.add_argument("--dsn", help="database DSN (default: DATABASE_URL, else Mu's local default)")
    p.set_defaults(func=cmd_disable)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:      # noqa: BLE001 - a CLI error is a message and a non-zero exit, not a traceback
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
