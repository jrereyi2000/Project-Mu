#!/usr/bin/env python3
"""
Interactive tool to resolve unmatched players from capture extraction.

Usage:
  python3 -m pipeline.resolve_player <capture_folder>

Reads unmatched_players.json (or inspects extracted offensive CSVs:
stats_passing.csv, stats_rushing.csv, stats_receiving.csv), displays the
extracted stats, screenshot, and cropped row snippet, queries Postgres
for candidate players from current and past seasons, and interactively
links or creates records in players and player_seasons.
"""
import argparse
import glob
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

import psycopg

from db.db_handler import DatabaseHandler
from pipeline.capture_matching import db_player_name, name_key

try:
    from madden_capture import ocr
except Exception:
    ocr = None

OFFENSE_CATEGORIES = ("passing", "rushing", "receiving")


def get_connection(dsn: Optional[str] = None):
    conn_info = dsn or DatabaseHandler().db_conn_info
    return psycopg.connect(conn_info)


def get_current_season(cur, folder: str) -> int:
    """Determine the season from captures record or league_state."""
    folder_name = os.path.basename(os.path.normpath(folder))
    cur.execute("SELECT season FROM captures WHERE folder = %s LIMIT 1", (folder_name,))
    row = cur.fetchone()
    if row and row[0]:
        return int(row[0])
    cur.execute("SELECT current_season FROM league_state WHERE id = 1")
    row = cur.fetchone()
    return int(row[0]) if row and row[0] else 2036


def resolve_team(cur, team_input: str) -> Optional[Tuple[int, str, str]]:
    """Resolve team string (abbr or name) to (id, abbreviation, name)."""
    t = team_input.strip()
    if not t:
        return None
    cur.execute(
        "SELECT id, abbreviation, name FROM teams WHERE abbreviation ILIKE %s OR name ILIKE %s",
        (t, f"%{t}%")
    )
    rows = cur.fetchall()
    if not rows:
        return None
    for r in rows:
        if r[1].upper() == t.upper():
            return r
    return rows[0]


def find_candidate_players(cur, player_name: str, position: Optional[str] = None) -> List[Tuple]:
    """Query Postgres players for matching candidates, including former seasons."""
    last_name = player_name.split(".")[-1].split(" ")[-1].strip()
    if not last_name:
        return []
    clean_last = re.sub(r"[^A-Za-z]", "", last_name)
    if not clean_last:
        return []

    sql = """
        SELECT p.id, p.player_name, p.full_name, p.player_number,
               s.season, s.position, s.team_id, t.abbreviation AS team_abbr, t.name AS team_name
        FROM players p
        LEFT JOIN LATERAL (
            SELECT ps.season, ps.position, ps.team_id
            FROM player_seasons ps
            WHERE ps.player_id = p.id
            ORDER BY ps.season DESC
            LIMIT 1
        ) s ON true
        LEFT JOIN teams t ON t.id = s.team_id
        WHERE p.player_name ILIKE %s OR p.full_name ILIKE %s
        ORDER BY s.season DESC NULLS LAST, p.id
        LIMIT 10
    """
    pattern = f"%{clean_last}%"
    cur.execute(sql, (pattern, pattern))
    return cur.fetchall()


def prompt_resolve_player(cur, season: int, cat: str, player_name: str, position: str,
                          stats: Dict[str, Any], screenshot_path: Optional[str],
                          snippet_path: Optional[str], is_interactive: bool = True) -> Optional[Tuple[int, int]]:
    """
    Prompt user in terminal to resolve an unmatched player.
    Returns (player_id, team_id) if resolved, or None if skipped/cancelled.
    """
    if not is_interactive:
        return None

    print("\n" + "=" * 70)
    print(f"UNMATCHED PLAYER: {player_name} ({position}) in {cat.upper()}")
    print("=" * 70)
    if stats:
        stat_line = ", ".join(f"{k}: {v}" for k, v in stats.items() if v and k not in ("NAME", "POS"))
        print(f"Stats: {stat_line}")
    if screenshot_path and os.path.exists(screenshot_path):
        print(f"Screenshot: {screenshot_path}")
    if snippet_path and os.path.exists(snippet_path):
        print(f"Snippet:    {snippet_path}")

    candidates = find_candidate_players(cur, player_name, position)
    if candidates:
        print("\nMatching candidates found in database:")
        for idx, (pid, pname, fname, num, l_season, l_pos, l_tid, tabbr, tname) in enumerate(candidates, 1):
            history_info = f"Last: {l_season or '?'} {tabbr or '?'} ({l_pos or '?'})" if l_season else "No recorded season"
            num_str = f"#{num}" if num is not None else ""
            print(f"  [{idx}] ID {pid}: {pname} ({fname or pname}) | {l_pos or position} | {history_info} {num_str}")
    else:
        print("\nNo matching candidates found in database.")

    while True:
        print("\nOptions:")
        if candidates:
            print("  [1] Link to an existing candidate ID above (prompts for 2036 team)")
        print("  [2] Enter another existing player ID (prompts for 2036 team)")
        print("  [3] Create new player (prompts for full name, 2036 team, optional number/age/ovr)")
        print("  [s] Skip this player")
        print("  [q] Quit resolution")

        choice = input("Select option: ").strip().lower()
        if choice in ("q", "quit"):
            print("Quitting resolution.")
            return None
        if choice in ("s", "skip"):
            print(f"Skipping {player_name}.")
            return None

        if choice == "1" and candidates:
            cand_choice = 0
            if len(candidates) == 1:
                cand_choice = 1
            else:
                while True:
                    c_in = input(f"Enter candidate number [1-{len(candidates)}] or 'c' to cancel: ").strip()
                    if c_in.lower() == "c":
                        break
                    if c_in.isdigit() and 1 <= int(c_in) <= len(candidates):
                        cand_choice = int(c_in)
                        break
                    print("Invalid candidate selection.")
            if cand_choice == 0:
                continue

            selected = candidates[cand_choice - 1]
            sel_pid = selected[0]
            # Prompt for team
            team_info = None
            while not team_info:
                t_in = input(f"Enter {season} team for {selected[1]} (abbr or name, e.g. COL): ").strip()
                if not t_in:
                    print("Team is required.")
                    continue
                team_info = resolve_team(cur, t_in)
                if not team_info:
                    print(f"Team {t_in!r} not recognized. Try team abbreviation or city name.")

            team_id = team_info[0]
            cur.execute("""
                INSERT INTO player_seasons (player_id, season, team_id, position)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (player_id, season) DO UPDATE SET
                    team_id = EXCLUDED.team_id,
                    position = COALESCE(EXCLUDED.position, player_seasons.position)
            """, (sel_pid, season, team_id, position))
            print(f"Linked player {sel_pid} ({selected[1]}) to team {team_info[1]} for season {season}.")
            return (sel_pid, team_id)

        elif choice == "2":
            pid_in = input("Enter existing player ID: ").strip()
            if not pid_in.isdigit():
                print("Player ID must be a number.")
                continue
            pid_val = int(pid_in)
            cur.execute("SELECT id, player_name FROM players WHERE id = %s", (pid_val,))
            p_row = cur.fetchone()
            if not p_row:
                print(f"Player ID {pid_val} not found in database.")
                continue

            team_info = None
            while not team_info:
                t_in = input(f"Enter {season} team for {p_row[1]} (abbr or name, e.g. COL): ").strip()
                if not t_in:
                    print("Team is required.")
                    continue
                team_info = resolve_team(cur, t_in)
                if not team_info:
                    print(f"Team {t_in!r} not recognized.")

            team_id = team_info[0]
            cur.execute("""
                INSERT INTO player_seasons (player_id, season, team_id, position)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (player_id, season) DO UPDATE SET
                    team_id = EXCLUDED.team_id,
                    position = COALESCE(EXCLUDED.position, player_seasons.position)
            """, (pid_val, season, team_id, position))
            print(f"Linked player {pid_val} ({p_row[1]}) to team {team_info[1]} for season {season}.")
            return (pid_val, team_id)

        elif choice == "3":
            # Create new player
            full_name = input(f"Enter full name (e.g. James Murray) [default '{player_name}']: ").strip()
            if not full_name:
                full_name = player_name
            team_info = None
            while not team_info:
                t_in = input(f"Enter {season} team (abbr or name, e.g. COL): ").strip()
                if not t_in:
                    print("Team is required.")
                    continue
                team_info = resolve_team(cur, t_in)
                if not team_info:
                    print(f"Team {t_in!r} not recognized.")

            team_id = team_info[0]
            num_in = input("Enter jersey number [default 0]: ").strip()
            jersey_num = int(num_in) if num_in.isdigit() else 0

            age_in = input("Enter age [optional, press Enter to skip]: ").strip()
            age_val = int(age_in) if age_in.isdigit() else None

            ovr_in = input("Enter overall rating (OVR) [optional, press Enter to skip]: ").strip()
            ovr_val = int(ovr_in) if ovr_in.isdigit() else None

            parts = full_name.split()
            first = parts[0] if parts else ""
            last = " ".join(parts[1:]) if len(parts) > 1 else full_name
            p_display = db_player_name(first, last) if first and len(parts) > 1 else full_name

            cur.execute("""
                INSERT INTO players (player_name, full_name, player_number)
                VALUES (%s, %s, %s)
                RETURNING id
            """, (p_display, full_name, jersey_num))
            new_pid = cur.fetchone()[0]

            cur.execute("""
                INSERT INTO player_seasons (player_id, season, team_id, position, age, overall_rating)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id, season) DO UPDATE SET
                    team_id = EXCLUDED.team_id,
                    position = EXCLUDED.position,
                    age = COALESCE(EXCLUDED.age, player_seasons.age),
                    overall_rating = COALESCE(EXCLUDED.overall_rating, player_seasons.overall_rating)
            """, (new_pid, season, team_id, position, age_val, ovr_val))

            print(f"Created player {new_pid}: {p_display} ({full_name}) for {team_info[1]} season {season}.")
            return (new_pid, team_id)
        else:
            print("Invalid option. Please choose [1], [2], [3], [s], or [q].")


def find_and_crop_unmatched_player(folder: str, cat: str, player_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Search screenshots in folder/stats/ for player_name and save a 700x120 row snippet."""
    if not folder:
        return None, None
    sdir = os.path.join(folder, "stats")
    if not os.path.isdir(sdir):
        return None, None
    last_target = player_name.split(".")[-1].split(" ")[-1].upper().strip()
    clean_target = re.sub(r"[^A-Z]", "", player_name.upper())

    pattern = os.path.join(sdir, f"*_{cat.lower()}_*")
    candidates = sorted([p for p in glob.glob(pattern) if not p.endswith(".txt") and "_right_" not in p])
    if not candidates:
        candidates = sorted([p for p in glob.glob(os.path.join(sdir, "*"))
                             if not p.endswith(".txt") and "_right_" not in p
                             and (p.endswith(".png") or p.endswith(".webp"))])

    try:
        from PIL import Image
    except Exception:
        return None, None

    if ocr is None:
        return None, None

    for p in candidates:
        try:
            items = ocr(p)
        except Exception:
            continue
        for t, b in items:
            t_upper = t.upper()
            t_clean = re.sub(r"[^A-Z]", "", t_upper)
            if (len(clean_target) >= 3 and clean_target in t_clean) or (len(last_target) >= 3 and last_target in t_upper):
                try:
                    img = Image.open(p)
                    W, H = img.size
                    x0, y0, x1, y1 = b
                    mid_y = (y0 + y1) / 2
                    crop_y0 = max(0, int(round(mid_y - 60)))
                    crop_y1 = min(H, crop_y0 + 120)
                    crop_x0 = 0
                    crop_x1 = min(W, 700)
                    crop_img = img.crop((crop_x0, crop_y0, crop_x1, crop_y1))

                    crops_dir = os.path.join(folder, "unmatched_crops")
                    os.makedirs(crops_dir, exist_ok=True)
                    safe_name = re.sub(r"[^a-zA-Z0-9_]", "", player_name.replace(".", "_").replace(" ", "_")).lower()
                    crop_path = os.path.join(crops_dir, f"{cat.lower()}_{safe_name}.png")
                    crop_img.save(crop_path)
                    return p, crop_path
                except Exception:
                    continue
    return None, None


def read_unmatched_from_folder(folder: str, cur, season: int) -> List[Dict[str, Any]]:
    """Read unmatched_players.json or inspect offensive CSVs."""
    json_path = os.path.join(folder, "unmatched_players.json")
    if os.path.exists(json_path):
        try:
            with open(json_path) as fh:
                entries = json.load(fh)
            if entries:
                return entries
        except Exception as e:
            print(f"Warning: could not read {json_path}: {e}")

    # Fallback: inspect extracted CSVs for unmatched offensive players
    entries = []
    # Fetch roster for season
    cur.execute("""
        SELECT p.id, p.player_name, s.team_id, s.position FROM players p
        JOIN player_seasons s ON s.player_id = p.id AND s.season = %s
        WHERE s.team_id IS NOT NULL
    """, (season,))
    roster_players = [(pid, name_key(pn), t, pos, pn) for pid, pn, t, pos in cur.fetchall()]
    by_key = {}
    for pid, key, team, pos, pn in roster_players:
        by_key.setdefault((key, pos), []).append(pid)

    for cat in OFFENSE_CATEGORIES:
        csv_path = os.path.join(folder, f"stats_{cat}.csv")
        if not os.path.exists(csv_path):
            continue
        try:
            import csv
            with open(csv_path) as fh:
                rows = list(csv.DictReader(fh))
        except Exception:
            continue

        for r in rows:
            name = r.get("NAME")
            pos = r.get("POS")
            if not name or not pos:
                continue
            key = (name_key(name), pos)
            if not by_key.get(key):
                # Unmatched offensive player
                sc, cr = find_and_crop_unmatched_player(folder, cat, name)
                entries.append({
                    "category": cat,
                    "player_name": name,
                    "position": pos,
                    "stats": r,
                    "screenshot_path": sc,
                    "snippet_path": cr,
                    "season": season
                })
    return entries


def resolve_capture_folder(folder: str, dsn: Optional[str] = None):
    if not os.path.isdir(folder):
        sys.exit(f"Folder not found: {folder}")

    with get_connection(dsn) as conn:
        with conn.cursor() as cur:
            season = get_current_season(cur, folder)
            entries = read_unmatched_from_folder(folder, cur, season)
            if not entries:
                print(f"No unmatched players found in {folder}.")
                return

            print(f"Found {len(entries)} unmatched player record(s) to resolve for season {season}.")
            json_path = os.path.join(folder, "unmatched_players.json")

            remaining = list(entries)
            for entry in list(entries):
                res = prompt_resolve_player(
                    cur,
                    season=entry.get("season", season),
                    cat=entry.get("category", "stats"),
                    player_name=entry.get("player_name", "UNKNOWN"),
                    position=entry.get("position", "UNKNOWN"),
                    stats=entry.get("stats", {}),
                    screenshot_path=entry.get("screenshot_path"),
                    snippet_path=entry.get("snippet_path"),
                    is_interactive=True
                )
                if res:
                    conn.commit()
                    # Remove from remaining
                    remaining = [
                        e for e in remaining
                        if not (e.get("category") == entry.get("category") and e.get("player_name") == entry.get("player_name"))
                    ]
                    # Update unmatched_players.json
                    try:
                        with open(json_path, "w") as fh:
                            json.dump(remaining, fh, indent=2)
                    except Exception:
                        pass
                else:
                    # User quit or skipped
                    break

            if not remaining:
                print(f"\nAll unmatched players in {folder} have been resolved!")
                if os.path.exists(json_path):
                    try:
                        os.remove(json_path)
                    except OSError:
                        pass
            else:
                print(f"\n{len(remaining)} unmatched player(s) remain unresolved.")


def main():
    parser = argparse.ArgumentParser(description="Resolve unmatched players in capture folder")
    parser.add_argument("folder", help="Capture directory (e.g. captures/2026-10-18_1908)")
    args = parser.parse_args()
    resolve_capture_folder(args.folder)


if __name__ == "__main__":
    main()
