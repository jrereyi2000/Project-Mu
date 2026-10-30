"""Per-game Madden 27 mod packs for the MMC Mod Manager (docs/MOD_INSTALL.md §4).

A game's pack is JR's hand-kept `Mu Base` pack (order and on/off for every mod; JR plays from `Default`, so it
can't be the base) with three groups switched for that game:

* Broadcast window, from games.game_time: Thu evening → TNF, Mon → MNF, Sat/Sun from 6 PM → SNF, anything else
  → CBS when the road team is AFC, FOX when it's NFC (the NFL's afternoon rule). CBS/FOX games get that network's
  presentation, Real Broadcast Music, scorebug and team logos; primetime games get their own presentation with the
  FOX scorebug and logos (its pack has primetime bugs) and no network music.
* Ball: BCA Pink for a game dated in October (newsroom.social_publish.kickoff), the Mudded ball otherwise.
* Crowd: Dynamic Crowd Density tier from crowd_score (home team popularity and form, opponent popularity,
  stakes); FULL (no crowd mod: Madden's sold-out stands) for playoff games and the very top regular-season games.

Packs are written into the Mod Manager's manager_config.json, one named pack per combination ("Mu FOX Tier2
Mudded"), only while MMCModManager.exe is closed (the open UI saves its own copy over the file). Pack strings list
mods lowest priority first.

    python3 -m pipeline.mod_packs plan [--season S] [--week W] [--team ABBR]
    python3 -m pipeline.mod_packs write (--game ID | --team ABBR) [--season S] [--week W] [--dry-run] [--launch]
    python3 -m pipeline.mod_packs seed-base --from PACK      # copy a pack into `Mu Base` (refuses to overwrite)

Reads DATABASE_URL (default: the active franchise, db.franchises), MMC_CONFIG and MMC_MANAGER_DIR.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MANAGER_DIR = "/mnt/c/Madden27Modding/MMC_Modding_Tools_v1.1.0.7/MMC_ModManager_v1.1.0.7"
GAME_PROFILE = "Madden27"
BASE_PACK = "Mu Base"
PACK_PREFIX = "Mu"
TASKLIST = "/mnt/c/Windows/System32/tasklist.exe"

PRIMETIME = ("SNF", "MNF", "TNF")
EVENING = dt.time(18, 0)
# Mods switched per game, matched by file name so a new version of a mod keeps working.
PRESENTATION = re.compile(r"^m27_(fox|cbs|snf|mnf|tnf)_presentation", re.I)
MUSIC = re.compile(r"^RealBroadcastMusic(CBS|FOX)", re.I)
SCOREBUG = re.compile(r"^M27_NFLScorebugs_(CBS|FOX)", re.I)
LOGOS = re.compile(r"^(cbs|fox) NFL Team Logos", re.I)
CROWD = re.compile(r"^Dynamic Crowd Density - Tier (\d)", re.I)
BALL_BCA = re.compile(r"^BCA Pink", re.I)
BALL_MUDDED = re.compile(r"^Mudded", re.I)

# crowd_score weights and stakes bonuses (points on a 0–100 scale), and tier floors
W_POP, W_FORM, W_OPP = 0.50, 0.30, 0.20
DIVISION_BONUS = 8
PRIMETIME_BONUS = 6
RACE_BONUS = 6            # week >= RACE_WEEK and the home team at .500 or better
RACE_WEEK = 15
FORM_FULL_GAMES = 4       # games this season before form stops leaning on last season
TIER_FLOORS = (("FULL", 80), (1, 54), (2, 40), (3, 25))   # anything lower is tier 4; calibrated on 2036 (§4)


@dataclass
class GameInfo:
    id: int
    season: int
    week: int
    game_time: str
    game_type: str
    home: str
    away: str
    away_conference: str
    division_game: bool
    home_pop_pct: float     # 0–1, rank among the league's teams
    away_pop_pct: float
    home_form: float        # 0–1 win share, blended with last season early on


# ------------------------------------------------------------------------------------------------ rules ---
def parse_game_time(game_time):
    """('Sun', time(13, 0)) from 'Sun 1:00 PM'; Sunday 1:00 PM when unknown."""
    parts = (game_time or "").split(" ", 1)
    day = parts[0] if len(parts) == 2 else "Sun"
    try:
        t = dt.datetime.strptime(parts[1].strip(), "%I:%M %p").time()
    except (IndexError, ValueError):
        t = dt.time(13, 0)
    return day, t


def broadcast_window(game_time, away_conference):
    day, t = parse_game_time(game_time)
    if day == "Thu" and t >= EVENING:
        return "TNF"
    if day == "Mon":
        return "MNF"
    if day in ("Sat", "Sun") and t >= EVENING:
        return "SNF"
    return "CBS" if (away_conference or "").upper() == "AFC" else "FOX"


def is_october(season, week, game_time):
    from newsroom.social_publish import kickoff
    return kickoff(season, {"week": week, "game_time": game_time}).month == 10


def crowd_score(g: GameInfo, window):
    base = 100 * (W_POP * g.home_pop_pct + W_FORM * g.home_form + W_OPP * g.away_pop_pct)
    bonus = (DIVISION_BONUS if g.division_game else 0) + (PRIMETIME_BONUS if window in PRIMETIME else 0)
    if g.week >= RACE_WEEK and g.home_form >= 0.5:
        bonus += RACE_BONUS
    return round(base + bonus, 1)


def crowd_tier(g: GameInfo, window):
    """'FULL' or 1–4, with the score that chose it (None for a playoff game)."""
    if g.game_type != "REGULAR_SEASON":
        return "FULL", None
    score = crowd_score(g, window)
    for tier, floor in TIER_FLOORS:
        if score >= floor:
            return tier, score
    return 4, score


@dataclass
class Choice:
    window: str
    tier: object
    score: object
    bca: bool

    @property
    def network(self):
        return "FOX" if self.window in PRIMETIME else self.window

    @property
    def pack_name(self):
        tier = "Full" if self.tier == "FULL" else f"Tier{self.tier}"
        return f"{PACK_PREFIX} {self.window} {tier} {'BCA' if self.bca else 'Mudded'}"


def choose(g: GameInfo):
    window = broadcast_window(g.game_time, g.away_conference)
    tier, score = crowd_tier(g, window)
    return Choice(window, tier, score, is_october(g.season, g.week, g.game_time))


# ------------------------------------------------------------------------------------------------ packs ---
def parse_pack(text):
    """[(file, enabled)] from 'a.fbmod:True|b.fbmod:False' (lowest priority first)."""
    out = []
    for item in filter(None, (text or "").split("|")):
        name, _, flag = item.rpartition(":")
        out.append((name, flag == "True"))
    return out


def format_pack(entries):
    return "|".join(f"{name}:{on}" for name, on in entries)


def switched(name, choice: Choice):
    """True/False for a mod this game decides, None for one left as the base pack has it."""
    if m := PRESENTATION.match(name):
        return m.group(1).upper() == choice.window
    if m := MUSIC.match(name):
        return m.group(1).upper() == choice.window
    if m := (SCOREBUG.match(name) or LOGOS.match(name)):
        return m.group(1).upper() == choice.network
    if m := CROWD.match(name):
        return choice.tier != "FULL" and int(m.group(1)) == choice.tier
    if BALL_BCA.match(name):
        return choice.bca
    if BALL_MUDDED.match(name):
        return not choice.bca
    return None


def build_pack(base_entries, choice: Choice):
    out = []
    for name, on in base_entries:
        decided = switched(name, choice)
        out.append((name, on if decided is None else decided))
    return out


# ------------------------------------------------------------------------------------------- the config ---
def config_path():
    if os.environ.get("MMC_CONFIG"):
        return os.environ["MMC_CONFIG"]
    found = glob.glob("/mnt/c/Users/*/AppData/Local/MMC/manager_config.json")
    if len(found) != 1:
        raise SystemExit(f"set MMC_CONFIG: found {len(found)} manager_config.json files")
    return found[0]


def manager_running():
    try:
        out = subprocess.run([TASKLIST, "/FI", "IMAGENAME eq MMCModManager.exe"], capture_output=True, text=True,
                             timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit("can't check whether MMCModManager.exe is running (tasklist.exe)")
    return "MMCModManager.exe" in out


def write_pack(path, name, entries, select=True):
    with open(path, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    game = cfg["Games"][GAME_PROFILE]
    game.setdefault("Packs", {})[name] = format_pack(entries)
    if select:
        game.setdefault("Options", {})["SelectedPack"] = name
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


def read_packs(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)["Games"][GAME_PROFILE].get("Packs", {})


def base_pack(path):
    packs = read_packs(path)
    if BASE_PACK not in packs:
        raise SystemExit(f"no '{BASE_PACK}' pack in {path}: create it with seed-base --from <pack>")
    return parse_pack(packs[BASE_PACK])


def launch(pack_name):
    manager_dir = os.environ.get("MMC_MANAGER_DIR", DEFAULT_MANAGER_DIR)
    exe = subprocess.run(["wslpath", "-w", os.path.join(manager_dir, "MMCModManager.exe")], capture_output=True,
                         text=True, check=True).stdout.strip()
    # start detaches it; the exe is set to run as Administrator, so Windows asks for consent
    subprocess.run(["cmd.exe", "/c", "start", "", exe, "-launch", pack_name], check=True, cwd="/mnt/c")


# --------------------------------------------------------------------------------------------- database ---
def connect():
    import psycopg2
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.path.insert(0, ROOT)
        from db.franchises import active_dsn
        dsn = active_dsn()
    return psycopg2.connect(dsn)


def _pop_pct(cur, season):
    """{team_id: 0–1 rank} from team_seasons.popularity_index (last season's when this one has none yet)."""
    for s in (season, season - 1):
        cur.execute("SELECT team_id, popularity_index FROM team_seasons WHERE season = %s "
                    "AND popularity_index IS NOT NULL AND popularity_index > 0", (s,))
        rows = cur.fetchall()
        if rows:
            ranked = sorted(rows, key=lambda r: r[1])
            n = len(ranked)
            return {tid: (i / (n - 1) if n > 1 else 0.5) for i, (tid, _) in enumerate(ranked)}
    return {}


def _win_share(cur, season, week=None):
    """{team_id: (wins + ties/2, games)} over completed regular-season games, before `week` when given."""
    cur.execute("""SELECT home_team_id, away_team_id, home_score, away_score FROM games
                   WHERE season = %s AND game_type = 'REGULAR_SEASON' AND status = 'COMPLETED'
                     AND (%s::int IS NULL OR week < %s)""", (season, week, week))
    out = {}
    for h, a, hs, as_ in cur.fetchall():
        for team, mine, theirs in ((h, hs, as_), (a, as_, hs)):
            w, n = out.get(team, (0.0, 0))
            out[team] = (w + (1.0 if mine > theirs else 0.5 if mine == theirs else 0.0), n + 1)
    return out


def form(team, now, last):
    w, n = now.get(team, (0.0, 0))
    lw, ln = last.get(team, (0.0, 0))
    last_share = lw / ln if ln else 0.5
    if n >= FORM_FULL_GAMES:
        return w / n
    return (w + last_share * (FORM_FULL_GAMES - n)) / FORM_FULL_GAMES


def load_games(cur, season, week=None, team=None, game_id=None):
    cur.execute("SELECT id, abbreviation, conference, division FROM teams")
    teams = {r[0]: r[1:] for r in cur.fetchall()}
    cur.execute("""SELECT id, season, week, game_time, game_type, home_team_id, away_team_id FROM games
                   WHERE (%(gid)s::int IS NOT NULL AND id = %(gid)s)
                      OR (%(gid)s::int IS NULL AND season = %(s)s AND (%(w)s::int IS NULL OR week = %(w)s))
                   ORDER BY week, id""", {"gid": game_id, "s": season, "w": week})
    rows = [r for r in cur.fetchall() if r[5] and r[6]]
    if team:
        rows = [r for r in rows if team.upper() in (teams[r[5]][0], teams[r[6]][0])]
    pops, shares, last = {}, {}, {}
    out = []
    for gid, s, wk, gtime, gtype, home, away in rows:
        if s not in pops:
            pops[s], last[s] = _pop_pct(cur, s), _win_share(cur, s - 1)
        if (s, wk) not in shares:
            shares[(s, wk)] = _win_share(cur, s, wk)
        out.append(GameInfo(
            id=gid, season=s, week=wk, game_time=gtime or "", game_type=gtype or "REGULAR_SEASON",
            home=teams[home][0], away=teams[away][0], away_conference=teams[away][1] or "",
            division_game=bool(teams[home][2]) and teams[home][2] == teams[away][2],
            home_pop_pct=pops[s].get(home, 0.5), away_pop_pct=pops[s].get(away, 0.5),
            home_form=form(home, shares[(s, wk)], last[s])))
    return out


def current_week(cur):
    cur.execute("SELECT current_season, current_week FROM league_state LIMIT 1")
    return cur.fetchone()


# -------------------------------------------------------------------------------------------------- CLI ---
def main(argv=None):
    ap = argparse.ArgumentParser(description="Per-game MMC mod packs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "write"):
        p = sub.add_parser(name)
        p.add_argument("--season", type=int)
        p.add_argument("--week", type=int)
        p.add_argument("--team", help="team abbreviation; plan: filter, write: that team's game in the week")
        if name == "write":
            p.add_argument("--game", type=int, help="games.id")
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--launch", action="store_true", help="start the game through the Mod Manager")
    seed = sub.add_parser("seed-base")
    seed.add_argument("--from", dest="source", required=True, help="pack to copy into the base pack")
    args = ap.parse_args(argv)

    if args.cmd == "seed-base":
        path = config_path()
        packs = read_packs(path)
        if BASE_PACK in packs:
            raise SystemExit(f"'{BASE_PACK}' already exists; edit it in the Mod Manager instead")
        if args.source not in packs:
            raise SystemExit(f"no pack '{args.source}'")
        if manager_running():
            raise SystemExit("MMCModManager.exe is open; close it first (it saves its own copy of the packs)")
        write_pack(path, BASE_PACK, parse_pack(packs[args.source]), select=False)
        print(f"copied '{args.source}' to '{BASE_PACK}'")
        return

    with connect() as conn, conn.cursor() as cur:
        season, week = current_week(cur)
        season = args.season or season
        week = args.week or (None if args.cmd == "plan" and args.team else week)
        games = load_games(cur, season, week, args.team, getattr(args, "game", None))

    if args.cmd == "plan":
        for g in games:
            c = choose(g)
            score = "" if c.score is None else f"{c.score:5.1f}"
            print(f"{g.season} wk{g.week:<2} {g.game_time:<12} {g.away:>3} @ {g.home:<3} {g.game_type:<14} "
                  f"{score:>5}  {c.pack_name}")
        return

    if len(games) != 1:
        raise SystemExit(f"write needs exactly one game, found {len(games)} (use --game or --team with --week)")
    g, c = games[0], choose(games[0])
    path = config_path()
    entries = build_pack(base_pack(path), c)
    on = [n for n, e in entries if e]
    print(f"{g.away} @ {g.home}, {g.season} week {g.week} {g.game_time}: {c.pack_name} "
          f"(crowd score {c.score}), {len(on)} mods on")
    if args.dry_run:
        print("\n".join(f"  {n}" for n in reversed(on)))
        return
    if manager_running():
        raise SystemExit("MMCModManager.exe is open; close it first (it saves its own copy of the packs)")
    write_pack(path, c.pack_name, entries)
    print(f"wrote pack '{c.pack_name}' to {path} and selected it")
    if args.launch:
        launch(c.pack_name)


if __name__ == "__main__":
    main()
