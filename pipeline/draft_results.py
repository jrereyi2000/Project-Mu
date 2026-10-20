"""Actual Round 1 draft results ingestion & persistence into draft_prospects.

Loads offline Round 1 results from CSV or JSON into draft_prospects:
- Matches existing scouted prospects for draft_season by normalized name + position.
- Updates actual_round ('1'), actual_round_pick (1-32), actual_pick (1-32), drafted_team_id, and selection_notes.
- Inserts off-board picks as new draft_prospects rows with projected_pick = NULL.
- Enforces 32-pick completeness and resets previous actual picks idempotently.

Usage:
  python3 -m pipeline.draft_results --season 2035 --file captures/offseason/2035/draft_results.csv [--dry-run]
"""

import argparse
import csv
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

from pipeline.capture_matching import name_key

SUFFIX_RE = re.compile(r"\b(JR|SR|[IVXLCDM]+)\.?\b", re.IGNORECASE)

POSITION_GROUP_MAP = {
    "QB": "QB",
    "HB": "RB", "RB": "RB", "FB": "RB",
    "WR": "WR",
    "TE": "TE",
    "LT": "OT", "RT": "OT", "OT": "OT",
    "LG": "IOL", "C": "IOL", "RG": "IOL", "IOL": "IOL", "OG": "IOL",
    "LEDG": "EDGE", "REDG": "EDGE", "EDGE": "EDGE", "DE": "EDGE",
    "DT": "DT", "NT": "DT",
    "MIKE": "LB", "WILL": "LB", "SAM": "LB", "LB": "LB", "ILB": "LB", "OLB": "LB",
    "CB": "CB",
    "FS": "S", "SS": "S", "S": "S",
    "K": "SPECIALISTS", "P": "SPECIALISTS", "LS": "SPECIALISTS",
}

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


def normalize_name(name: Optional[str]) -> str:
    """Normalize player name for comparison: strip suffixes, punctuation, and whitespace."""
    if not name:
        return ""
    cleaned = SUFFIX_RE.sub("", name).strip()
    return re.sub(r"[^A-Z0-9]", "", cleaned.upper())


def normalize_position(pos: Optional[str]) -> str:
    """Map detailed positions to standard position groups."""
    if not pos:
        return ""
    p = pos.strip().upper()
    return POSITION_GROUP_MAP.get(p, p)


def is_position_compatible(pos1: Optional[str], pos2: Optional[str]) -> bool:
    """Check if two position strings are compatible (either exact match or same group)."""
    if not pos1 or not pos2:
        return True
    p1 = pos1.strip().upper()
    p2 = pos2.strip().upper()
    if p1 == p2:
        return True
    g1 = normalize_position(p1)
    g2 = normalize_position(p2)
    return bool(g1 and g2 and g1 == g2)


def get_cursor(conn_or_cur):
    """Return a database cursor from either a connection, a cursor, or None."""
    if conn_or_cur is None:
        import psycopg
        conn = psycopg.connect(DSN)
        return conn.cursor(), conn, True
    if hasattr(conn_or_cur, "cursor"):
        return conn_or_cur.cursor(), conn_or_cur, False
    return conn_or_cur, None, False


def fetch_team_lookup(cur) -> Tuple[Dict[int, int], Dict[str, int]]:
    """Build team lookups by ID and by code/abbreviation/name."""
    cur.execute("SELECT id, name, abbreviation, city FROM teams")
    by_id = {}
    by_code = {}
    for tid, name, abbr, city in cur.fetchall():
        by_id[tid] = tid
        if abbr:
            by_code[abbr.strip().upper()] = tid
        if name:
            by_code[name.strip().upper()] = tid
        if city and name:
            by_code[f"{city.strip()} {name.strip()}".upper()] = tid
            by_code[city.strip().upper()] = tid
    return by_id, by_code


def resolve_team_id(team_val: Any, by_id: Dict[int, int], by_code: Dict[str, int], pick: int) -> int:
    """Resolve a team value (ID, abbreviation, city, or name) to teams.id."""
    if team_val is None:
        raise ValueError(f"Pick {pick}: team identifier is missing")
    if isinstance(team_val, int) and team_val in by_id:
        return team_val
    s = str(team_val).strip()
    if s.isdigit() and int(s) in by_id:
        return int(s)
    code = s.upper()
    if code in by_code:
        return by_code[code]
    raise ValueError(f"Pick {pick}: unable to resolve team '{team_val}' in teams table")


def fetch_prospects_for_season(cur, draft_season: int) -> Tuple[int, List[Tuple[int, str, Optional[str]]]]:
    """Fetch scouted board prospects for the given draft season (or draft_season + 1 if stored as upcoming class).
    Returns (resolved_draft_season, rows)."""
    cur.execute(
        "SELECT id, full_name, position FROM draft_prospects WHERE draft_season = %s AND projected_pick IS NOT NULL",
        (draft_season,),
    )
    rows = cur.fetchall()
    if not rows:
        cur.execute(
            "SELECT id, full_name, position FROM draft_prospects WHERE draft_season = %s AND projected_pick IS NOT NULL",
            (draft_season + 1,),
        )
        alt_rows = cur.fetchall()
        if alt_rows:
            return draft_season + 1, alt_rows
    return draft_season, rows


def parse_raw_picks(raw_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract and validate raw pick records from CSV or JSON dicts."""
    parsed = []
    for row in raw_records:
        norm_row = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
        pick_val = (
            norm_row.get("overall_pick")
            or norm_row.get("pick")
            or norm_row.get("actual_pick")
            or norm_row.get("overall")
            or norm_row.get("pick_number")
            or norm_row.get("number")
        )
        if pick_val is None:
            raise ValueError(f"Record missing pick number: {row}")
        try:
            pick_num = int(pick_val)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid pick number '{pick_val}' in record: {row}")

        team_val = (
            norm_row.get("team")
            or norm_row.get("team_id")
            or norm_row.get("drafted_team_id")
            or norm_row.get("abbreviation")
            or norm_row.get("team_abbr")
            or norm_row.get("team_name")
            or norm_row.get("franchise")
        )

        player_name = (
            norm_row.get("player_name")
            or norm_row.get("player")
            or norm_row.get("name")
            or norm_row.get("prospect_name")
            or norm_row.get("prospect")
            or norm_row.get("full_name")
        )

        position = norm_row.get("position") or norm_row.get("pos")
        college = norm_row.get("college") or norm_row.get("school") or norm_row.get("university")
        notes = (
            norm_row.get("selection_notes")
            or norm_row.get("notes")
            or norm_row.get("note")
            or norm_row.get("comments")
        )

        round_val = norm_row.get("round") or norm_row.get("actual_round")
        round_pick_val = norm_row.get("round_pick") or norm_row.get("actual_round_pick")

        parsed.append({
            "overall_pick": pick_num,
            "round": int(round_val) if round_val is not None and str(round_val).isdigit() else None,
            "round_pick": int(round_pick_val) if round_pick_val is not None and str(round_pick_val).isdigit() else None,
            "team_val": team_val,
            "player_name": (str(player_name).strip() if player_name is not None else ""),
            "position": (str(position).strip() if position is not None else None),
            "college": (str(college).strip() if college is not None else None),
            "notes": (str(notes).strip() if notes is not None else None),
        })
    return parsed


def validate_completeness(parsed_picks: List[Dict[str, Any]]) -> None:
    """Enforce completeness: at least 32 picks (Round 1), or full draft (all 7 rounds, 224+ picks, up to 300),
    with picks present consecutively from 1 to N without gaps or duplicates."""
    if not parsed_picks or len(parsed_picks) < 32:
        found_len = len(parsed_picks) if parsed_picks else 0
        raise ValueError(f"Draft results must contain at least 32 picks (found {found_len})")
    if len(parsed_picks) > 300:
        raise ValueError(f"Draft results exceeds maximum supported picks of 300 (found {len(parsed_picks)})")

    pick_nums = [p["overall_pick"] for p in parsed_picks]
    n_picks = len(parsed_picks)

    expected_picks = set(range(1, n_picks + 1))
    actual_picks = set(pick_nums)

    missing = sorted(expected_picks - actual_picks)
    duplicates = sorted(set(p for p in pick_nums if pick_nums.count(p) > 1))
    out_of_bounds = sorted(p for p in pick_nums if p < 1 or p > n_picks)

    errors = []
    if missing:
        errors.append(f"missing picks: {missing}")
    if duplicates:
        errors.append(f"duplicate picks: {duplicates}")
    if out_of_bounds:
        errors.append(f"out-of-range picks: {out_of_bounds}")

    if errors:
        raise ValueError(f"Draft results pick validation failed: {'; '.join(errors)}")

    for p in parsed_picks:
        if not p["player_name"]:
            raise ValueError(f"Pick {p['overall_pick']} has empty player name")


def resolve_and_load_picks(
    raw_records: List[Dict[str, Any]],
    draft_season: int,
    dry_run: bool = False,
    conn: Optional[Any] = None,
    cur: Optional[Any] = None,
) -> Dict[str, Any]:
    """Core logic to parse, validate, match prospects, and ingest 32 draft picks into draft_prospects."""
    parsed_picks = parse_raw_picks(raw_records)
    validate_completeness(parsed_picks)

    # Sort picks 1..32
    parsed_picks.sort(key=lambda x: x["overall_pick"])

    cursor, opened_conn, should_close = get_cursor(cur or conn)
    try:
        by_id, by_code = fetch_team_lookup(cursor)
        target_season, prospect_rows = fetch_prospects_for_season(cursor, draft_season)

        # Build prospect index: normalized_name -> list of (id, full_name, position)
        prospect_by_name: Dict[str, List[Tuple[int, str, Optional[str]]]] = {}
        prospect_by_namekey: Dict[str, List[Tuple[int, str, Optional[str]]]] = {}
        for pid, full_name, pos in prospect_rows:
            key = normalize_name(full_name)
            if key:
                prospect_by_name.setdefault(key, []).append((pid, full_name, pos))
            nk = name_key(full_name)
            if nk:
                prospect_by_namekey.setdefault(nk, []).append((pid, full_name, pos))

        warnings: List[str] = []
        resolved_selections: List[Dict[str, Any]] = []
        matched_prospect_ids: Dict[int, List[int]] = {}  # prospect_id -> list of pick numbers

        # Initial prospect resolution per pick
        for pick_item in parsed_picks:
            pick = pick_item["overall_pick"]
            player_name = pick_item["player_name"]
            pick_pos = pick_item["position"]
            team_id = resolve_team_id(pick_item["team_val"], by_id, by_code, pick)

            norm_name = normalize_name(player_name)
            candidates = prospect_by_name.get(norm_name, [])
            if not candidates:
                nk = name_key(player_name)
                candidates = prospect_by_namekey.get(nk, [])

            resolved_pid: Optional[int] = None
            if not candidates:
                warnings.append(
                    f"Pick {pick} ({player_name}, {pick_pos or 'N/A'}): "
                    f"prospect not found on board for season {target_season}"
                )
            elif len(candidates) == 1:
                cand_id, cand_name, cand_pos = candidates[0]
                if is_position_compatible(pick_pos, cand_pos):
                    resolved_pid = cand_id
                else:
                    warnings.append(
                        f"Pick {pick} ({player_name}, {pick_pos or 'N/A'}): "
                        f"name matched prospect '{cand_name}' but position '{cand_pos}' is incompatible"
                    )
            else:
                # Multiple candidates by name: filter by position
                pos_filtered = [c for c in candidates if is_position_compatible(pick_pos, c[2])]
                if len(pos_filtered) == 1:
                    resolved_pid = pos_filtered[0][0]
                else:
                    warnings.append(
                        f"Pick {pick} ({player_name}, {pick_pos or 'N/A'}): "
                        f"ambiguous prospect match ({len(candidates)} candidates found in draft_prospects)"
                    )

            if resolved_pid is not None:
                matched_prospect_ids.setdefault(resolved_pid, []).append(pick)

            calc_round = str(pick_item.get("round") or (((pick - 1) // 32) + 1))
            calc_round_pick = int(pick_item.get("round_pick") or (((pick - 1) % 32) + 1))

            resolved_selections.append({
                "draft_season": target_season,
                "overall_pick": pick,
                "actual_round": calc_round,
                "actual_round_pick": calc_round_pick,
                "team_id": team_id,
                "prospect_id": resolved_pid,
                "player_name": player_name,
                "position": pick_pos,
                "college": pick_item["college"],
                "notes": pick_item["notes"],
            })

        # Disallow ambiguous duplicate matches of the same prospect across multiple picks
        for pid, picks_with_pid in matched_prospect_ids.items():
            if len(picks_with_pid) > 1:
                for pick in picks_with_pid:
                    for sel in resolved_selections:
                        if sel["overall_pick"] == pick:
                            sel["prospect_id"] = None
                            warnings.append(
                                f"Pick {pick} ({sel['player_name']}): duplicate match for prospect ID {pid} "
                                f"across picks {picks_with_pid}; set prospect_id to NULL"
                            )

        matched_count = sum(1 for s in resolved_selections if s["prospect_id"] is not None)

        if not dry_run:
            # 1. Clear previously loaded actual picks for this draft season (idempotency)
            # Remove any unprojected off-board prospects that were inserted solely as draft results
            cursor.execute(
                "DELETE FROM draft_prospects WHERE draft_season = %s AND projected_pick IS NULL AND actual_pick IS NOT NULL",
                (target_season,),
            )
            # Reset actual_pick columns on existing board prospects
            cursor.execute(
                "UPDATE draft_prospects SET actual_round = NULL, actual_round_pick = NULL, actual_pick = NULL, "
                "drafted_team_id = NULL, selection_notes = NULL "
                "WHERE draft_season = %s AND actual_pick IS NOT NULL",
                (target_season,),
            )

            # 2. Update matched prospects or insert new off-board prospects
            for sel in resolved_selections:
                pid = sel["prospect_id"]
                if pid is not None:
                    cursor.execute(
                        """UPDATE draft_prospects 
                           SET actual_round = %s, actual_round_pick = %s, actual_pick = %s, 
                               drafted_team_id = %s, selection_notes = %s 
                           WHERE id = %s""",
                        (sel["actual_round"], sel["actual_round_pick"], sel["overall_pick"], sel["team_id"], sel["notes"], pid),
                    )
                else:
                    # Off-board or unresolved prospect: insert new row with projected_pick = NULL
                    cursor.execute(
                        """INSERT INTO draft_prospects 
                           (draft_season, projected_pick, actual_round, actual_round_pick, actual_pick, 
                            drafted_team_id, full_name, position, college, selection_notes)
                           VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            sel["draft_season"],
                            sel["actual_round"],
                            sel["actual_round_pick"],
                            sel["overall_pick"],
                            sel["team_id"],
                            sel["player_name"],
                            sel["position"],
                            sel["college"],
                            sel["notes"],
                        ),
                    )

            # 3. Team management step 4: check the Draft Room's Round 1 picks against the recap (MATCH / MISMATCH
            # on scout_draft_picks only; the recap stays the truth for actual_*), and mark the class DRAFTED / UDFA
            from engines.draft_room import validate as validate_draft_room
            room_report = validate_draft_room(cursor, target_season)

            if opened_conn and hasattr(opened_conn, "commit"):
                opened_conn.commit()
        else:
            room_report = None

        return {
            "status": "dry_run" if dry_run else "success",
            "draft_season": target_season,
            "picks_loaded": len(resolved_selections),
            "matched_prospects": matched_count,
            "warnings": warnings,
            "selections": resolved_selections,
            "draft_room": room_report,
        }
    finally:
        if should_close and opened_conn:
            opened_conn.close()


def load_draft_results_csv(
    file_path: Union[str, os.PathLike],
    draft_season: int,
    dry_run: bool = False,
    conn: Optional[Any] = None,
    cur: Optional[Any] = None,
) -> Dict[str, Any]:
    """Load and validate Round 1 draft results from a CSV file into draft_prospects."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Draft results CSV not found: {file_path}")

    raw_records = []
    with open(file_path, "r", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw_records.append(row)

    return resolve_and_load_picks(raw_records, draft_season, dry_run=dry_run, conn=conn, cur=cur)


def load_draft_results_json(
    data_or_path: Union[str, os.PathLike, List[Dict[str, Any]], Dict[str, Any]],
    draft_season: int,
    dry_run: bool = False,
    conn: Optional[Any] = None,
    cur: Optional[Any] = None,
) -> Dict[str, Any]:
    """Load and validate Round 1 draft results from a JSON file, JSON string, list, or dict into draft_prospects."""
    if isinstance(data_or_path, (str, os.PathLike)):
        p = str(data_or_path)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as fh:
                parsed = json.load(fh)
        else:
            parsed = json.loads(p)
    else:
        parsed = data_or_path

    if isinstance(parsed, dict):
        raw_records = (
            parsed.get("picks")
            or parsed.get("selections")
            or parsed.get("draft_results")
            or parsed.get("results")
            or []
        )
        if not raw_records and all(str(k).isdigit() for k in parsed.keys()):
            raw_records = list(parsed.values())
    elif isinstance(parsed, list):
        raw_records = parsed
    else:
        raise ValueError(f"Unsupported JSON structure: expected list or dict with picks, got {type(parsed).__name__}")

    return resolve_and_load_picks(raw_records, draft_season, dry_run=dry_run, conn=conn, cur=cur)


def main():
    parser = argparse.ArgumentParser(description="Ingest Round 1 draft results into draft_prospects.")
    parser.add_argument("--season", "--draft-season", dest="season", type=int, required=True, help="Draft season (e.g. 2035)")
    parser.add_argument("--file", type=str, required=True, help="Path to draft results CSV or JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Validate and resolve without writing to the database")

    args = parser.parse_args()

    file_path = args.file
    if not os.path.exists(file_path):
        print(f"Error: file not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    try:
        if file_path.lower().endswith(".json"):
            report = load_draft_results_json(file_path, args.season, dry_run=args.dry_run)
        else:
            report = load_draft_results_csv(file_path, args.season, dry_run=args.dry_run)
    except Exception as e:
        print(f"Error loading draft results: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nDraft Results Report for Season {args.season} ({'DRY RUN' if args.dry_run else 'COMMITTED'}):")
    print(f"  Picks loaded: {report['picks_loaded']}")
    print(f"  Matched prospects: {report['matched_prospects']} / {report['picks_loaded']}")
    if report["warnings"]:
        print(f"  Warnings ({len(report['warnings'])}):")
        for w in report["warnings"]:
            print(f"    - {w}")
    else:
        print("  Warnings: None")
    sys.exit(0)


if __name__ == "__main__":
    main()
