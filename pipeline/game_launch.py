"""Launch JR's next game through the MMC Mod Manager (docs/done/V2_CORE_BUILD_PLAN.md §2.7, U14).

    launch(team, season, week, dry_run=False, cur=None) -> {"pack", "command", "started", "refused", "game"}

Order of checks, each refusing with a reason (refused = the reason, started false, nothing written or run):
  1. the team has PENDING franchise_pending_moves: they must be written into the save first (league office);
  2. Madden 27 is already running (pipeline/gpu_queue/process_check.is_madden_running);
  3. the team has exactly one game that week (games; season / week default to league_state's);
  4. the Mod Manager config and its `Mu Base` pack exist (pipeline/mod_packs.config_path, base_pack).
Then the game's pack is built with mod_packs (load_games -> choose -> build_pack). A dry run stops here and returns
the pack name and the command it would run. A real run also refuses while MMCModManager.exe is open (its UI saves its
own copy of the packs over the file), writes and selects the pack (mod_packs.write_pack), and starts

    cmd.exe /c start "" "<C:\\...\\MMCModManager.exe>" -launch "<pack>"

from WSL (cwd /mnt/c; `start` detaches it; the exe runs as Administrator, so Windows may ask for consent).

The manager's path: MMC_MOD_MANAGER_EXE (a WSL or Windows path to MMCModManager.exe) when set, else
mod_packs' MMC_MANAGER_DIR (default mod_packs.DEFAULT_MANAGER_DIR) + MMCModManager.exe. WSL paths under /mnt/<drive>/
become <DRIVE>:\\... for cmd.exe. The config: MMC_CONFIG, else mod_packs' search of the Windows user profiles.

    python3 -m pipeline.game_launch --team CAR [--season S --week W] [--dry-run]

Reads DATABASE_URL (default: the active franchise, db.franchises). Never writes the save.
"""
import argparse
import json
import os
import re
import subprocess
import sys

from pipeline import mod_packs
from pipeline.gpu_queue import process_check

MANAGER_EXE = "MMCModManager.exe"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# One detached process after Play Game (PRESS_CONFERENCE_PLAN §2.2): the reporters' judgments for the pressers
# just declined or approved, then the team's beat wakeup. Read-only-with-respect-to-the-save, and never inside a
# request JR waits on.
PRESS_WAKEUP_CODE = r"""
import json, sys
from newsroom import press_room

ids = json.loads(sys.argv[1])
team_id, season, week = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
conn_info = press_room._conn_info()
for conference_id in ids:
    try:
        press_room.close_effects(conn_info, conference_id, None)
    except Exception as e:
        print("press effects failed for %s: %s" % (conference_id, e))
try:
    from newsroom.swarm_dispatcher import SwarmDispatcher, build_weekly_assignment_brief
    dispatcher = SwarmDispatcher(debug_mode=False)
    brief = build_weekly_assignment_brief(conn_info, season=season, week=week)
    dispatcher.dispatch_team_local(team_id=team_id, brief=brief, season=season, week=week,
                                   live=True, save_to_db=True, max_workers=5)
except Exception as e:
    print("press wakeup failed for team %s: %s" % (team_id, e))
"""


def manager_exe() -> str:
    """The MMCModManager.exe path (WSL or Windows form) per the module docstring."""
    env = os.environ.get("MMC_MOD_MANAGER_EXE")
    if env:
        return env
    return os.path.join(os.environ.get("MMC_MANAGER_DIR", mod_packs.DEFAULT_MANAGER_DIR), MANAGER_EXE)


def windows_path(path: str) -> str:
    """/mnt/c/a/b -> C:\\a\\b; a path already in Windows form is kept."""
    m = re.match(r"^/mnt/([a-zA-Z])(/.*)?$", path)
    if m:
        return f"{m.group(1).upper()}:" + (m.group(2) or "/").replace("/", "\\")
    return path


def mod_packs_dir() -> str:
    """The Mod Manager's own folder, the working directory `-launch` needs: started from anywhere
    else, Frosty's executor fails to load the pack and the game never starts (2026-11-12,
    docs/reference/MUSE_EMBER_EVALUATION.md §2.2)."""
    exe = manager_exe()
    m = re.match(r"^([a-zA-Z]):[\\/](.*)$", exe)
    if m:                                   # a Windows path for the exe -> WSL form of its folder
        return "/mnt/" + m.group(1).lower() + "/" + m.group(2).replace("\\", "/").rsplit("/", 1)[0]
    return os.path.dirname(exe) or mod_packs.DEFAULT_MANAGER_DIR


def command(exe: str, pack: str) -> list:
    return ["cmd.exe", "/c", "start", "", windows_path(exe), "-launch", pack]


def command_text(argv: list) -> str:
    """The argv as one cmd.exe line: cmd.exe /c start "" "<exe>" -launch "<pack>"."""
    return f'{argv[0]} {argv[1]} {argv[2]} "" "{argv[4]}" {argv[5]} "{argv[6]}"'


def pending_moves(cur, team: str) -> int:
    cur.execute("""SELECT count(*) FROM franchise_pending_moves m JOIN teams t ON t.id = m.team_id
                   WHERE t.abbreviation = %s AND m.status = 'PENDING'""", (team,))
    row = cur.fetchone()
    return int(row[0] if row else 0)


def _result(pack=None, cmd=None, started=False, refused=None, game=None) -> dict:
    return {"pack": pack, "command": cmd, "started": started, "refused": refused, "game": game}


def _launch(cur, team: str, season, week, dry_run: bool) -> dict:
    team = (team or "").upper().strip()
    n = pending_moves(cur, team)
    if n:
        return _result(refused=f"{team} has {n} pending move{'s' if n != 1 else ''}: the league office must write "
                               f"them into the save first")
    if process_check.is_madden_running():
        return _result(refused="Madden 27 is already running")
    if season is None or week is None:
        s, w = mod_packs.current_week(cur)
        season = s if season is None else season
        week = w if week is None else week
    games = mod_packs.load_games(cur, season, week, team)
    if len(games) != 1:
        return _result(refused=f"{team} has {len(games)} games in {season} week {week} (need exactly one)")
    g = games[0]
    game = {"id": g.id, "season": g.season, "week": g.week, "home": g.home, "away": g.away,
            "game_time": g.game_time, "game_type": g.game_type}
    choice = mod_packs.choose(g)
    pack = choice.pack_name
    argv = command(manager_exe(), pack)
    text = command_text(argv)
    try:
        path = mod_packs.config_path()
        entries = mod_packs.build_pack(mod_packs.base_pack(path), choice)
    except (SystemExit, OSError, KeyError, ValueError) as e:
        return _result(pack, text, refused=f"mod pack: {e}", game=game)
    if dry_run:
        return _result(pack, text, game=game)
    try:
        if mod_packs.manager_running():
            return _result(pack, text, refused="MMCModManager.exe is open; close it first (it saves its own copy "
                                               "of the packs)", game=game)
    except SystemExit as e:
        return _result(pack, text, refused=str(e), game=game)
    mod_packs.write_pack(path, pack, entries)
    subprocess.run(argv, check=True, cwd=mod_packs_dir())
    return _result(pack, text, started=True, game=game)


def _commit(cur) -> None:
    conn = getattr(cur, "connection", None)
    if conn is not None:
        conn.commit()


def _press_payload(cur, team: str, result: dict):
    """Decline JR's unstarted presser (Play Game, §2.2) and return (conference ids, team id, season, week) for the
    detached judgments + beat wakeup, or None when nothing was open. Table missing -> no decline, no spawn."""
    from newsroom import press_room
    game = result.get("game") or {}
    season, week = int(game.get("season") or 0), int(game.get("week") or 0)
    if not season or not week:
        return None
    aliases = {"ARI": "AZ", "CRD": "AZ", "WSH": "WAS", "WTC": "WAS"}
    cur.execute("/* q:launch_team */ SELECT id FROM teams WHERE abbreviation IN (%s, %s)",
                (team, aliases.get(team, team)))
    row = cur.fetchone()
    if not row:
        return None
    team_id = int(row[0])
    ids = press_room.decline_open_for_launch(cur, team_id, season)
    return (ids, team_id, season, week) if ids else None


def _spawn_press_wakeup(payload) -> None:
    ids, team_id, season, week = payload
    subprocess.Popen(
        [sys.executable, "-c", PRESS_WAKEUP_CODE, json.dumps(ids), str(team_id), str(season), str(week)],
        cwd=ROOT, env={**os.environ}, start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def launch(team, season=None, week=None, dry_run=False, cur=None) -> dict:
    """Writes the game's mod pack and starts Madden through the Mod Manager (see the module docstring). A real launch
    also counts JR's unstarted presser as declined and starts the judgments and the beat wakeup, detached."""
    team = (team or "").upper().strip()
    if cur is not None:
        out = _launch(cur, team, season, week, dry_run)
        payload = None
        if not dry_run and not out.get("refused"):
            try:
                payload = _press_payload(cur, team, out)
            except Exception as e:      # noqa: BLE001 - the press hook never stops Play Game
                print(f"⚠️  press conference not declined before launch: {e}")
        _commit(cur)
        if payload:
            _spawn_press_wakeup(payload)
        return out
    with mod_packs.connect() as conn, conn.cursor() as c:
        out = _launch(c, team, season, week, dry_run)
        payload = None
        if not dry_run and not out.get("refused"):
            try:
                payload = _press_payload(c, team, out)
            except Exception as e:      # noqa: BLE001
                print(f"⚠️  press conference not declined before launch: {e}")
    if payload:
        _spawn_press_wakeup(payload)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Launch JR's game through the MMC Mod Manager")
    ap.add_argument("--team", required=True, help="team abbreviation")
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--dry-run", action="store_true", help="build the pack and command; write and run nothing")
    args = ap.parse_args(argv)
    out = launch(args.team, args.season, args.week, dry_run=args.dry_run)
    print(json.dumps(out, indent=2))
    if out["refused"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
