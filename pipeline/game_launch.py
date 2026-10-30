"""Launch JR's next game through the MMC Mod Manager (docs/in_progress/V2_CORE_BUILD_PLAN.md §2.7, U14).

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
    subprocess.run(argv, check=True, cwd="/mnt/c")
    return _result(pack, text, started=True, game=game)


def launch(team, season=None, week=None, dry_run=False, cur=None) -> dict:
    """Writes the game's mod pack and starts Madden through the Mod Manager (see the module docstring)."""
    if cur is not None:
        return _launch(cur, team, season, week, dry_run)
    with mod_packs.connect() as conn, conn.cursor() as c:
        return _launch(c, team, season, week, dry_run)


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
