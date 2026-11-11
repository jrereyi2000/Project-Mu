"""Command line for the NFL Weekly Mirror (NFL_WEEKLY_MIRROR_PLAN.md §2.11, §2.10; PROJECT_SPEC §3.3aa).

    python3 -m engines.nfl_mirror status
    python3 -m engines.nfl_mirror preview --week N [--snapshot PATH] [--dsn DSN]
    python3 -m engines.nfl_mirror trades --week N [--dsn DSN]
    python3 -m engines.nfl_mirror enable [--last-week N] --confirm DB [--dsn DSN]
    python3 -m engines.nfl_mirror disable --confirm DB [--dsn DSN]

`status` prints the settings row, the last mirrored week and the next real week's mode. `preview` runs the real
week's fetch, build and roster diff against the newest capture snapshot of the database's own franchise, selected
by `league_state.madden_league_id`, season and stage, and prints the plan, writing nothing (it does not plan the
created players, injuries or staff changes: those are the league office's write, not a preview); `--snapshot`
names one explicitly, and its identity is checked against the database too. `trades` prints the unconfirmed
switches of a week. `enable` / `disable` only change `nfl_mirror_settings`, and each needs `--confirm <db>` naming
the database the connection actually opened, so an enabled mirror is never a typo.

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
    """The database name libpq resolves from a DSN, for messages only (never to authorize a write).

    Repeated options keep the last value and a URL's query parameters override its path, so this is not the
    authority for `--confirm`: `_connected_db_name` reads the database the connection actually opened."""
    value = str(dsn or "").strip()
    try:
        from psycopg.conninfo import conninfo_to_dict
        name = conninfo_to_dict(value).get("dbname")
    except Exception:       # noqa: BLE001 - a bare database name is not a conninfo string
        name = None
    return str(name) if name else value


def _connected_db_name(conn):
    """The database the opened connection targets, as libpq resolved it (a URL query override, a repeated option
    or a service/environment default included)."""
    info = getattr(conn, "info", None)
    name = getattr(info, "dbname", None) if info is not None else None
    if name:
        return str(name)
    with conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        row = cur.fetchone()
    if not row or row[0] is None:
        raise RuntimeError("the connection did not report its database name")
    return str(row[0])


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


class SnapshotIdentityError(RuntimeError):
    """A capture snapshot cannot be tied to the database being previewed (refuse instead of cross-combining)."""


def _capture_snapshot_paths():
    """Every captures/<run>/save.json, newest first by mtime."""
    if not os.path.isdir(CAPTURES):
        raise FileNotFoundError(f"no captures directory at {CAPTURES}")
    paths = [os.path.join(CAPTURES, name, "save.json") for name in os.listdir(CAPTURES)]
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        raise FileNotFoundError(f"no capture snapshots (save.json) under {CAPTURES}")
    return sorted(paths, key=os.path.getmtime, reverse=True)


def _load_snapshot(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        raise SnapshotIdentityError(f"cannot read snapshot {path}: {type(e).__name__}: {e}") from e


def _snapshot_identity(snapshot):
    """(league id, league season, stage) carried by a snapshot, or SnapshotIdentityError."""
    if not isinstance(snapshot, dict):
        raise SnapshotIdentityError("snapshot is not a JSON object")
    league_id = (snapshot.get("franchise") or {}).get("league_id")
    if league_id is None:
        raise SnapshotIdentityError("snapshot has no franchise.league_id")
    try:
        league_id = int(league_id)
    except (TypeError, ValueError) as e:
        raise SnapshotIdentityError(f"snapshot franchise.league_id {league_id!r} is not a number") from e
    from pipeline import league_office as lo
    try:
        stage = lo.detect_stage(snapshot)
        season = int(lo.league_season(snapshot, stage))
    except Exception as e:      # noqa: BLE001 - StageError or a malformed season block
        raise SnapshotIdentityError(f"snapshot has no recognisable season/stage: {e}") from e
    if stage not in (lo.REGULAR_WEEK, lo.PLAYOFF_WEEK):
        raise SnapshotIdentityError(f"snapshot stage {stage} is not a regular-season or playoff stage")
    return {"league_id": league_id, "season": season, "stage": stage}


def _stage_for_week(week):
    """The league office stage of a league_state.current_week, or None when it is not a game week."""
    from pipeline import league_office as lo
    try:
        week = int(week)
    except (TypeError, ValueError):
        return None
    if 1 <= week <= 18:
        return lo.REGULAR_WEEK
    if 19 <= week <= 23:
        return lo.PLAYOFF_WEEK
    return None


def _database_identity(cur, season):
    """The database's own identity, used to bind the preview's snapshot (never season alone: franchises share it)."""
    try:
        cur.execute("SELECT madden_league_id, current_season, current_week FROM league_state WHERE id = 1")
        row = cur.fetchone()
    except Exception as e:      # noqa: BLE001 - a missing table/column or an unseeded database must refuse
        raise SnapshotIdentityError(f"cannot read league_state: {type(e).__name__}: {e}") from e
    if not row:
        raise SnapshotIdentityError("league_state has no id = 1 row")
    league_id, db_season, db_week = row
    if league_id is None:
        raise SnapshotIdentityError(
            "league_state.madden_league_id is not set; cannot bind a capture to this database")
    try:
        league_id, db_season = int(league_id), int(db_season)
    except (TypeError, ValueError) as e:
        raise SnapshotIdentityError(f"league_state holds a non-numeric identity ({row!r})") from e
    stage = _stage_for_week(db_week)
    if stage is None:
        raise SnapshotIdentityError(
            f"league_state.current_week {db_week!r} is not a regular-season or playoff week; "
            "cannot bind a capture to this database")
    if season is not None and int(season) != db_season:
        raise SnapshotIdentityError(
            f"the mirror settings season {season} does not match league_state season {db_season}; "
            "refusing to bind a capture")
    return {"league_id": league_id, "season": db_season, "stage": stage}


def _validate_snapshot(snapshot, path, identity):
    meta = _snapshot_identity(snapshot)
    if meta["league_id"] != identity["league_id"]:
        raise SnapshotIdentityError(
            f"snapshot {path} is league {meta['league_id']}, not this database's league {identity['league_id']}")
    if meta["season"] != identity["season"]:
        raise SnapshotIdentityError(
            f"snapshot {path} is season {meta['season']}, not this database's season {identity['season']}")
    if meta["stage"] != identity["stage"]:
        raise SnapshotIdentityError(
            f"snapshot {path} is a {meta['stage']} save, not this database's {identity['stage']}")
    return meta


def _snapshot_for_database(identity, explicit=None):
    """(path, snapshot) verified against `identity`; an explicit --snapshot is checked too, never trusted."""
    if explicit:
        snapshot = _load_snapshot(explicit)
        _validate_snapshot(snapshot, explicit, identity)
        return explicit, snapshot
    try:
        paths = _capture_snapshot_paths()
    except FileNotFoundError as e:
        raise SnapshotIdentityError(str(e)) from e
    reasons = []
    for path in paths:
        try:
            snapshot = _load_snapshot(path)
            _validate_snapshot(snapshot, path, identity)
        except SnapshotIdentityError as e:
            reasons.append(str(e))
            continue
        return path, snapshot
    more = "; ..." if len(reasons) > 3 else ""
    raise SnapshotIdentityError(
        f"no capture snapshot matches league {identity['league_id']}, season {identity['season']}, "
        f"stage {identity['stage']} under {CAPTURES} (checked {len(paths)} capture(s): "
        f"{'; '.join(reasons[:3])}{more})")


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
def next_real_game_week(mu_week):
    """The next Mu week with a real NFL game, or None past the Super Bowl.

    Mu week 22 is the Pro Bowl (an internal no-game stop), so 21 goes to 23 and 20 still goes to 21."""
    week = int(mu_week) + 1
    while week <= 23:
        try:
            real_state.real_week_for(week)
        except ValueError:
            week += 1
        else:
            return week
    return None


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
        mu_week = next_real_game_week(int(week))
    if mu_week is None:
        lines.append("next real week: none (past the Super Bowl; no real NFL game after Mu week 23)")
    else:
        real_week = real_state.real_week_for(mu_week)
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
    with _connect(resolve_dsn(args)) as conn, conn.cursor() as cur:
        season = settings_season(cur)
        try:
            identity = _database_identity(cur, season)
            snapshot_path, snapshot = _snapshot_for_database(identity, args.snapshot)
        except SnapshotIdentityError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
        plan = preview_plan(cur, snapshot, season, args.week)
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
def _connect_confirmed(args):
    """(connection, resolved database name) after checking --confirm against the database actually opened.

    Returns (None, None) after refusing: the connection is closed without a cursor or a commit, so a mismatch
    writes nothing. libpq resolves repeated options, URL query overrides and service/environment defaults; only
    the opened connection knows the real target, and the update runs on that same connection."""
    conn = _connect(resolve_dsn(args))
    try:
        target = _connected_db_name(conn)
    except Exception as e:      # noqa: BLE001 - an unreadable identity must refuse, never write
        conn.close()
        print(f"refused: cannot read the opened database's name: {type(e).__name__}: {e}", file=sys.stderr)
        return None, None
    confirmed = (args.confirm or "").strip()
    if confirmed != target:
        conn.close()
        print(f"refused: --confirm {confirmed!r} does not name the database the DSN opened ({target!r})",
              file=sys.stderr)
        return None, None
    return conn, target


def cmd_enable(args):
    if args.last_week is not None and args.last_week < 1:
        print("refused: --last-week is a Mu week number (1 or later)", file=sys.stderr)
        return 2
    conn, target = _connect_confirmed(args)
    if conn is None:
        return 2
    try:
        with conn.cursor() as cur:
            store.set_enabled(cur, True, season=store.DEFAULT_SEASON, last_week=args.last_week)
        conn.commit()
    finally:
        conn.close()
    through = f"through Mu week {args.last_week}" if args.last_week is not None else "through the Super Bowl"
    print(f"NFL mirror enabled on {target}: season {store.DEFAULT_SEASON} {through}")
    return 0


def cmd_disable(args):
    conn, target = _connect_confirmed(args)
    if conn is None:
        return 2
    try:
        with conn.cursor() as cur:
            store.set_enabled(cur, False, season=store.DEFAULT_SEASON, last_week=None)
        conn.commit()
    finally:
        conn.close()
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

    p = sub.add_parser("preview", help="fetch, build and diff a real week against the newest snapshot of the "
                                       "database's own franchise (writes nothing)")
    p.add_argument("--week", type=int, required=True, metavar="N", help="Mu week number")
    p.add_argument("--snapshot", metavar="PATH", help="an explicit capture save.json; its league, season and stage "
                                                      "must match the database")
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
