"""Offseason editorial service layer for Project Mu.

Handles:
1. Parsing and strict schema validation of all-in-one manifest.json.
2. Structured dry-run manifest preview with qualitative tiering and All-Pro verification.
3. Transactional execution of head coach and coordinator tenure transitions with invariant enforcement.
4. Compilation of anti-leakage event facts packets for each offseason story type.
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
ROOT_DIR = Path(__file__).resolve().parent.parent

VALID_SLANTS = {"OFFENSIVE", "DEFENSIVE", "CEO"}
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def convert_ovr_to_qualitative_tier(ovr: Union[int, float]) -> str:
    """Converts a numeric player OVR to an approved qualitative tier.
    Enforces the strict anti-leakage rule: numeric OVR must NEVER be exposed to models.
    """
    val = int(ovr)
    if val >= 95:
        return "Tier 1 superstar / elite franchise cornerstone"
    elif val >= 90:
        return "High-impact marquee starter / Pro Bowl caliber talent"
    else:
        raise ValueError(f"OVR {val} is below the 90+ threshold for marquee free agency evaluation")


def load_manifest(path_or_dict: Union[str, Path, Dict[str, Any]]) -> Dict[str, Any]:
    """Parses and validates the all-in-one offseason manifest schema:
    - schema_version: 1
    - season: int
    - publication_week: default 24
    - event_dates: dict with keys {"super_bowl", "coaching", "signings", "draft"} formatted YYYY-MM-DD
    - coaching_moves: list of coaching hire/change dicts
    - signings: list of marquee signings (validated ovr >= 90)
    - draft_results: {"source_file": "..."} or list of picks
    """
    if isinstance(path_or_dict, (str, Path)):
        p = Path(path_or_dict)
        if not p.is_absolute():
            p = ROOT_DIR / p
        if not p.exists():
            raise FileNotFoundError(f"Manifest file not found: {path_or_dict}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    elif isinstance(path_or_dict, dict):
        data = dict(path_or_dict)
    else:
        raise TypeError(f"Expected str, Path, or dict for manifest, got {type(path_or_dict)}")

    # 1. Validate top-level schema
    if "schema_version" not in data or data["schema_version"] != 1:
        raise ValueError(f"Invalid schema_version '{data.get('schema_version')}'; expected 1")

    if "season" not in data or not isinstance(data["season"], int):
        raise ValueError("Manifest must declare integer 'season'")

    season = data["season"]
    pub_week = data.get("publication_week", 24)
    if pub_week != 24:
        raise ValueError(f"Offseason editorial package publication_week must be 24 (got {pub_week})")
    data["publication_week"] = 24

    # 2. Validate event_dates
    event_dates = data.get("event_dates")
    if not isinstance(event_dates, dict):
        raise ValueError("Manifest must include 'event_dates' dictionary")
    required_events = ("super_bowl", "coaching", "signings", "draft")
    for evt in required_events:
        d = event_dates.get(evt)
        if not d or not isinstance(d, str) or not DATE_PATTERN.match(d.strip()):
            raise ValueError(f"event_dates must include valid YYYY-MM-DD for '{evt}' (got '{d}')")

    # 3. Validate coaching_moves
    coaching_moves = data.get("coaching_moves", [])
    if not isinstance(coaching_moves, list):
        raise ValueError("'coaching_moves' must be a list")

    for i, m in enumerate(coaching_moves):
        if not isinstance(m, dict):
            raise ValueError(f"coaching_moves[{i}] must be a dictionary")
        for req in ("team", "incoming_coach", "incoming_slant"):
            if not m.get(req):
                raise ValueError(f"coaching_moves[{i}] missing required field '{req}'")
        slant = str(m["incoming_slant"]).upper()
        if slant not in VALID_SLANTS:
            raise ValueError(f"coaching_moves[{i}] invalid incoming_slant '{m['incoming_slant']}'; must be one of {sorted(VALID_SLANTS)}")
        m["incoming_slant"] = slant
        if "effective_season" not in m:
            m["effective_season"] = season + 1

    # 4. Validate signings (strictly 90+ OVR)
    signings = data.get("signings", [])
    if not isinstance(signings, list):
        raise ValueError("'signings' must be a list")

    for i, s in enumerate(signings):
        if not isinstance(s, dict):
            raise ValueError(f"signings[{i}] must be a dictionary")
        for req in ("player_name", "position", "former_team", "new_team", "ovr"):
            if s.get(req) is None or str(s.get(req)).strip() == "":
                raise ValueError(f"signings[{i}] missing required field '{req}'")
        try:
            ovr_val = int(s["ovr"])
        except (ValueError, TypeError):
            raise ValueError(f"signings[{i}] has non-integer ovr '{s.get('ovr')}'")
        if ovr_val < 90:
            raise ValueError(
                f"Signing '{s['player_name']}' has OVR {ovr_val} < 90. "
                "Sub-90 signings are forbidden in marquee free agency."
            )
        s["ovr"] = ovr_val

    # 5. Validate draft_results
    draft_results = data.get("draft_results")
    if draft_results is not None:
        if isinstance(draft_results, dict):
            if "source_file" not in draft_results and "picks" not in draft_results:
                raise ValueError("'draft_results' dict must contain 'source_file' or 'picks'")
        elif isinstance(draft_results, list):
            pass
        else:
            raise ValueError("'draft_results' must be a dict or list")

    return data


def resolve_team(conn, team_identifier: Union[str, int]) -> Dict[str, Any]:
    """Resolves team identifier (ID, abbreviation, city, or name) against the teams table."""
    with conn.cursor() as cur:
        if isinstance(team_identifier, int) or (isinstance(team_identifier, str) and team_identifier.isdigit()):
            cur.execute("SELECT id, name, city, abbreviation FROM teams WHERE id = %s", (int(team_identifier),))
            row = cur.fetchone()
            if row:
                return dict(row)
        s = str(team_identifier).strip()
        cur.execute("""
            SELECT id, name, city, abbreviation FROM teams
            WHERE abbreviation ILIKE %s OR name ILIKE %s OR (city || ' ' || name) ILIKE %s
            LIMIT 1
        """, (s, s, s))
        row = cur.fetchone()
        if row:
            return dict(row)
    raise ValueError(f"Could not resolve team '{team_identifier}' in teams table")


def check_player_all_pro_honors(conn, player_name: str) -> Dict[str, Any]:
    """Checks the awards table for actual Official AP All-Pro selections.
    Enforces the rule: 'All-Pro' descriptor must NEVER be used without DB verification.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT season, award_type FROM awards
            WHERE player_name ILIKE %s AND award_type IN ('First Team All-Pro', 'Second Team All-Pro')
            ORDER BY season ASC
        """, (player_name.strip(),))
        rows = cur.fetchall()
        if rows:
            selections = [f"{r['award_type']} ({r['season']})" for r in rows]
            return {
                "has_all_pro": True,
                "all_pro_selections": selections,
                "descriptor_allowed": True,
                "notes": f"Official AP All-Pro selection on record: {', '.join(selections)}",
            }
        else:
            return {
                "has_all_pro": False,
                "all_pro_selections": [],
                "descriptor_allowed": False,
                "notes": "No Official AP All-Pro selections on record; 'All-Pro' descriptor strictly forbidden.",
            }


# Offseason signing candidates (team management step 5, phase 7): the League Transactions scan's offseason SIGNED
# rows of 90+ players, with their terms, for JR to pick the 2-3 marquee signings from. Nothing is picked here.
SIGNING_CANDIDATE_MIN_OVR = 90


def fetch_offseason_signings(conn, season: int) -> List[Dict[str, Any]]:
    """The season's offseason SIGNED transactions (week NULL; offseason rows carry the finished season's year) with
    the player's latest rating and the team that last released him, if any."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT t.id AS transaction_id, t.player_id, COALESCE(p.full_name, t.player_name) AS player_name,
                   t.position, tt.abbreviation AS new_team, t.contract_years, t.contract_value,
                   COALESCE(ft.abbreviation, (
                       SELECT rt.abbreviation FROM transactions r JOIN teams rt ON rt.id = r.from_team_id
                       WHERE r.player_id = t.player_id AND r.type = 'RELEASED' AND r.id < t.id
                       ORDER BY r.id DESC LIMIT 1)) AS former_team,
                   (SELECT ps.overall_rating FROM player_seasons ps
                     WHERE ps.player_id = t.player_id AND ps.season IN (%s, %s)
                     ORDER BY ps.season DESC LIMIT 1) AS ovr
            FROM transactions t
            LEFT JOIN players p ON p.id = t.player_id
            LEFT JOIN teams tt ON tt.id = t.to_team_id
            LEFT JOIN teams ft ON ft.id = t.from_team_id
            WHERE t.season = %s AND t.week IS NULL AND t.type = 'SIGNED'
            ORDER BY t.contract_value DESC NULLS LAST, t.id;
        """, (season, season + 1, season))
        return [dict(r) for r in cur.fetchall()]


def signing_terms(row: Dict[str, Any]) -> str:
    """A signing's headline terms as fact (R5), e.g. '3 years, $54.6M total (from the signing)'."""
    from newsroom.roster_moves import money_text
    years, value = row.get("contract_years"), row.get("contract_value")
    if not years and value is None:
        return "not recorded: state no contract terms"
    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if value is not None:
        parts.append(f"{money_text(value)} total")
    return ", ".join(parts) + " (from the signing)"


def signing_candidates(rows: List[Dict[str, Any]], min_ovr: int = SIGNING_CANDIDATE_MIN_OVR) -> List[Dict[str, Any]]:
    """Offseason signings of 90+ players as manifest candidates (JR picks 2-3), most expensive first."""
    out = []
    for r in rows:
        if (r.get("ovr") or 0) < min_ovr:
            continue
        out.append({
            "transaction_id": r.get("transaction_id"),
            "player_name": r.get("player_name"),
            "position": r.get("position"),
            "former_team": r.get("former_team") or "unknown (fill in)",
            "new_team": r.get("new_team"),
            "ovr": r.get("ovr"),
            "qualitative_tier": convert_ovr_to_qualitative_tier(r["ovr"]),
            "terms": signing_terms(r),
        })
    return out


def match_signing_terms(signing: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    """The terms of a manifest signing from its offseason SIGNED row (by player name), else 'not recorded'."""
    name = str(signing.get("player_name") or "").strip().lower()
    for r in rows:
        if str(r.get("player_name") or "").strip().lower() == name:
            return signing_terms(r)
    return "not recorded: state no contract terms"


def coach_hot_seat_firings(conn, season: int) -> Optional[List[Dict[str, Any]]]:
    """[{team, coach}] the coach hot seat (engines/coach_hot_seat.py) fired after `season`; None when it never
    decided that season (before the hot seat existed)."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM coach_hot_seat WHERE season = %s", (season,))
        if not cur.fetchone()["n"]:
            return None
        cur.execute("""SELECT t.abbreviation AS team, c.coach_name AS coach FROM coach_hot_seat h
                       JOIN teams t ON t.id = h.team_id JOIN coaches c ON c.id = h.coach_id
                       WHERE h.season = %s AND h.fired ORDER BY t.abbreviation""", (season,))
        return [dict(r) for r in cur.fetchall()]


def hot_seat_warnings(firings: List[Dict[str, Any]], moves: List[Dict[str, Any]]) -> List[str]:
    """Pure. Fired teams with no coaching move, and head-coach hires at teams the hot seat didn't fire."""
    fired = {f["team"]: f["coach"] for f in firings}
    hired = {m["team"] for m in moves}             # every coaching move installs a new head coach
    out = [f"{t}: the hot seat fired {c}; add the replacement you hired in Madden to coaching_moves"
           for t, c in sorted(fired.items()) if t not in hired]
    out += [f"{t}: a head-coach hire the hot seat didn't fire for; did Madden fire this coach on its own?"
            for t in sorted(hired - set(fired))]
    return out


def preview_manifest(path_or_dict: Union[str, Path, Dict[str, Any]], conn_info: Optional[str] = None) -> Dict[str, Any]:
    """Generates a structured dry-run validation preview of the manifest:
    - Resolves teams, coaches, slants
    - Qualitative tiers and All-Pro eligibility for signings
    - Draft pick count and source status
    - Non-fatal warnings
    """
    manifest = load_manifest(path_or_dict)
    conn_str = conn_info or CONN_INFO

    preview: Dict[str, Any] = {
        "valid": True,
        "season": manifest["season"],
        "publication_week": manifest["publication_week"],
        "event_dates": manifest["event_dates"],
        "resolved_teams": [],
        "coaching_moves": [],
        "hot_seat_firings": [],
        "signings": [],
        "signing_candidates": [],
        "draft_summary": {},
        "warnings": [],
        "summary": "",
        "error": None,
    }

    try:
        with psycopg.connect(conn_str, row_factory=dict_row) as conn:
            # 1. Preview Coaching Moves
            resolved_team_set = set()
            for m in manifest.get("coaching_moves", []):
                t_info = resolve_team(conn, m["team"])
                resolved_team_set.add(t_info["abbreviation"])
                origin_desc = None
                if m.get("origin"):
                    orig = m["origin"]
                    if isinstance(orig, dict):
                        origin_desc = f"{orig.get('role')} at {orig.get('team')}"
                    else:
                        origin_desc = str(orig)

                preview["coaching_moves"].append({
                    "team": t_info["abbreviation"],
                    "team_name": f"{t_info['city']} {t_info['name']}",
                    "action": m.get("action", "hire"),
                    "outgoing_coach": m.get("outgoing_coach"),
                    "incoming_coach": m["incoming_coach"],
                    "incoming_slant": m["incoming_slant"],
                    "origin": origin_desc,
                    "imported_offensive_scheme": m.get("imported_offensive_scheme"),
                    "retained_defensive_scheme": m.get("retained_defensive_scheme"),
                    "effective_season": m.get("effective_season", manifest["season"] + 1),
                    "notes": m.get("notes"),
                })

            # 1b. Reconcile with the coach hot seat: every team Mu fired a coach for needs its Madden replacement, and a
            # head-coach change Mu didn't make means Madden fired someone on its own.
            try:
                with conn.transaction():
                    firings = coach_hot_seat_firings(conn, manifest["season"])
            except Exception:   # no coach_hot_seat table yet
                firings = None
            if firings is not None:
                preview["hot_seat_firings"] = firings
                preview["warnings"].extend(hot_seat_warnings(firings, preview["coaching_moves"]))

            # 2. Preview Signings with Qualitative Conversion & All-Pro Check
            for s in manifest.get("signings", []):
                f_team = resolve_team(conn, s["former_team"])
                n_team = resolve_team(conn, s["new_team"])
                resolved_team_set.add(f_team["abbreviation"])
                resolved_team_set.add(n_team["abbreviation"])

                qual_tier = convert_ovr_to_qualitative_tier(s["ovr"])
                all_pro_info = check_player_all_pro_honors(conn, s["player_name"])

                preview["signings"].append({
                    "player_name": s["player_name"],
                    "position": s["position"],
                    "former_team": f_team["abbreviation"],
                    "new_team": n_team["abbreviation"],
                    "qualitative_tier": qual_tier,
                    "all_pro_status": all_pro_info["notes"],
                    "all_pro_descriptor_allowed": all_pro_info["descriptor_allowed"],
                    "contract_notes": s.get("contract_notes"),
                    "role_notes": s.get("role_notes"),
                })

            preview["resolved_teams"] = sorted(list(resolved_team_set))

            # 2b. Candidates for the marquee signings: the offseason's 90+ signings from League Transactions
            try:
                with conn.transaction():
                    preview["signing_candidates"] = signing_candidates(fetch_offseason_signings(conn, manifest["season"]))
            except Exception as ex:   # no transactions table yet: no candidates
                preview["warnings"].append(f"Signing candidates unavailable: {ex}")

            # 3. Preview Draft Results
            draft_meta = manifest.get("draft_results")
            if draft_meta:
                if isinstance(draft_meta, dict) and "source_file" in draft_meta:
                    sf = draft_meta["source_file"]
                    p = Path(sf)
                    if not p.is_absolute():
                        p = ROOT_DIR / p
                    if p.exists():
                        # Try to count rows
                        try:
                            from pipeline.draft_results import parse_raw_picks
                            if p.suffix.lower() == ".csv":
                                import csv
                                with open(p, "r", encoding="utf-8") as fh:
                                    reader = csv.DictReader(fh)
                                    rows = list(reader)
                            else:
                                with open(p, "r", encoding="utf-8") as fh:
                                    rows = json.load(fh)
                            picks = parse_raw_picks(rows)
                            preview["draft_summary"] = {
                                "source_file": sf,
                                "pick_count": len(picks),
                                "status": "Ready for ingestion" if len(picks) >= 32 else f"Warning: {len(picks)} picks found (expected at least 32)",
                            }
                            if len(picks) < 32:
                                preview["warnings"].append(f"Draft source file contains {len(picks)} picks (expected at least 32)")
                        except Exception as ex:
                            preview["draft_summary"] = {"source_file": sf, "error": str(ex)}
                            preview["warnings"].append(f"Failed to inspect draft file: {ex}")
                    else:
                        preview["draft_summary"] = {
                            "source_file": sf,
                            "pick_count": 0,
                            "status": "File does not exist on disk yet",
                        }
                        preview["warnings"].append(f"Draft results source_file '{sf}' not found on disk")
                elif isinstance(draft_meta, list):
                    preview["draft_summary"] = {
                        "pick_count": len(draft_meta),
                        "status": "Picks provided in manifest array",
                    }
                    if len(draft_meta) < 32:
                        preview["warnings"].append(f"Draft results list contains {len(draft_meta)} picks (expected at least 32)")

        # Summary line
        cm_count = len(preview["coaching_moves"])
        sig_count = len(preview["signings"])
        dp_count = preview["draft_summary"].get("pick_count", 0)
        preview["summary"] = (
            f"Season {manifest['season']} Offseason Manifest: "
            f"{cm_count} coaching move{'s' if cm_count != 1 else ''}, "
            f"{sig_count} marquee signing{'s' if sig_count != 1 else ''}, "
            f"{dp_count} draft pick{'s' if dp_count != 1 else ''}"
        )

    except Exception as e:
        preview["valid"] = False
        preview["error"] = str(e)
        preview["warnings"].append(str(e))
        logger.exception("Error generating manifest preview")

    return preview


def apply_coaching_moves(
    manifest: Dict[str, Any],
    dry_run: bool = False,
    conn: Optional[Any] = None,
    conn_info: Optional[str] = None,
) -> Dict[str, Any]:
    """Executes coach and coordinator tenure transitions transactionally:
    - Closes outgoing head coach tenure (end_season = concluded_season).
    - Inserts incoming coach into `coaches` if new (setting slant, schemes).
    - Opens incoming `coach_tenures` starting at concluded_season + 1.
    - Updates coordinator tenures for imported vs retained schemes.
    - Enforces invariants: exactly one active HC, OC, DC per touched team.
    - Rolls back cleanly if dry_run=True or if any invariant is violated.
    """
    manifest_data = load_manifest(manifest)
    concluded_season = manifest_data["season"]
    moves = manifest_data.get("coaching_moves", [])

    results: Dict[str, Any] = {
        "success": True,
        "concluded_season": concluded_season,
        "dry_run": dry_run,
        "applied_moves": [],
        "touched_teams": [],
    }

    class DryRunRollback(Exception):
        pass

    def _execute_moves(c):
        with c.cursor() as cur:
            touched_team_ids = set()

            for move in moves:
                team_info = resolve_team(c, move["team"])
                team_id = team_info["id"]
                touched_team_ids.add(team_id)

                incoming_coach_name = move["incoming_coach"].strip()
                incoming_slant = move["incoming_slant"].strip().upper()
                effective_season = int(move.get("effective_season", concluded_season + 1))
                outgoing_coach_name = move.get("outgoing_coach")

                # 1. Close outgoing head coach tenure at hiring team
                cur.execute("""
                    UPDATE coach_tenures
                    SET end_season = %s
                    WHERE team_id = %s
                      AND start_season <= %s
                      AND (end_season IS NULL OR end_season > %s)
                """, (concluded_season, team_id, concluded_season, concluded_season))

                # 2. Insert or update incoming coach in coaches table
                cur.execute("SELECT id, slant, offensive_scheme, defensive_scheme FROM coaches WHERE coach_name ILIKE %s", (incoming_coach_name,))
                coach_row = cur.fetchone()

                imported_off = move.get("imported_offensive_scheme") or "Spread"
                retained_def = move.get("retained_defensive_scheme") or "Base 4-3"
                imported_def = move.get("imported_defensive_scheme")
                retained_off = move.get("retained_offensive_scheme")

                if incoming_slant == "OFFENSIVE":
                    effective_off = imported_off
                    effective_def = retained_def
                elif incoming_slant == "DEFENSIVE":
                    effective_off = retained_off or imported_off
                    effective_def = imported_def or retained_def
                else:  # CEO
                    effective_off = imported_off
                    effective_def = retained_def

                if not coach_row:
                    cur.execute("""
                        INSERT INTO coaches (coach_name, slant, offensive_scheme, defensive_scheme, pass_lean, fourth_down_aggressiveness, blitz_rate, man_coverage)
                        VALUES (%s, %s, %s, %s, 'Medium', 'Medium', 'Medium', 'Medium')
                        RETURNING id
                    """, (incoming_coach_name, incoming_slant, effective_off, effective_def))
                    incoming_coach_id = cur.fetchone()["id"]
                else:
                    incoming_coach_id = coach_row["id"]
                    cur.execute("""
                        UPDATE coaches
                        SET slant = %s, offensive_scheme = %s, defensive_scheme = %s
                        WHERE id = %s
                    """, (incoming_slant, effective_off, effective_def, incoming_coach_id))

                # 3. Open incoming head coach tenure
                cur.execute("""
                    INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season)
                    VALUES (%s, %s, %s, NULL)
                    ON CONFLICT (coach_id, team_id, start_season) DO UPDATE
                    SET end_season = NULL
                """, (incoming_coach_id, team_id, effective_season))

                # 4. Handle Coordinator Origin Poach (if incoming coach was an OC/DC elsewhere)
                origin = move.get("origin")
                if origin and isinstance(origin, dict) and origin.get("team") and origin.get("role"):
                    origin_info = resolve_team(c, origin["team"])
                    origin_team_id = origin_info["id"]
                    touched_team_ids.add(origin_team_id)
                    origin_role = origin["role"].strip().upper()

                    # Close origin coordinator tenure at concluded season
                    cur.execute("""
                        UPDATE coordinator_tenures
                        SET end_season = %s
                        WHERE team_id = %s AND role = %s
                          AND start_season <= %s
                          AND (end_season IS NULL OR end_season > %s)
                    """, (concluded_season, origin_team_id, origin_role, concluded_season, concluded_season))

                    # Ensure origin team has an active replacement coordinator for effective_season
                    cur.execute("""
                        SELECT COUNT(*) AS cnt FROM coordinator_tenures
                        WHERE team_id = %s AND role = %s
                          AND start_season <= %s AND (end_season IS NULL OR end_season >= %s)
                    """, (origin_team_id, origin_role, effective_season, effective_season))
                    if cur.fetchone()["cnt"] == 0:
                        cur.execute("""
                            INSERT INTO coordinators (coordinator_name, side, scheme, playbook, pass_lean)
                            VALUES (%s, %s, %s, %s, 'Medium')
                            RETURNING id
                        """, (f"{origin_info['name']} Interim {origin_role}", "OFFENSIVE" if origin_role == "OC" else "DEFENSIVE", "Spread" if origin_role == "OC" else "Base 4-3", f"{origin_info['name']} {origin_role} System"))
                        new_c_id = cur.fetchone()["id"]
                        cur.execute("""
                            INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season)
                            VALUES (%s, %s, %s, %s, NULL)
                        """, (new_c_id, origin_team_id, origin_role, effective_season))

                # 5. Handle hiring team coordinator updates & scheme inheritance
                if incoming_slant == "OFFENSIVE":
                    # Offensive scheme imported: close old OC tenure and insert new OC
                    cur.execute("""
                        UPDATE coordinator_tenures
                        SET end_season = %s
                        WHERE team_id = %s AND role = 'OC'
                          AND start_season <= %s
                          AND (end_season IS NULL OR end_season > %s)
                    """, (concluded_season, team_id, concluded_season, concluded_season))

                    oc_name = f"{incoming_coach_name} Offensive Lieutenant"
                    cur.execute("""
                        INSERT INTO coordinators (coordinator_name, side, scheme, playbook, pass_lean)
                        VALUES (%s, 'OFFENSIVE', %s, %s, 'Medium')
                        RETURNING id
                    """, (oc_name, imported_off, f"{team_info['name']} Offense"))
                    new_oc_id = cur.fetchone()["id"]
                    cur.execute("""
                        INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season)
                        VALUES (%s, %s, 'OC', %s, NULL)
                    """, (new_oc_id, team_id, effective_season))

                    # Retain DC: verify active DC exists into effective_season
                    cur.execute("""
                        SELECT id FROM coordinator_tenures
                        WHERE team_id = %s AND role = 'DC'
                          AND start_season <= %s AND (end_season IS NULL OR end_season >= %s)
                    """, (team_id, effective_season, effective_season))
                    if not cur.fetchone():
                        # Ensure existing DC has end_season extended or create continuation
                        cur.execute("""
                            UPDATE coordinator_tenures
                            SET end_season = NULL
                            WHERE team_id = %s AND role = 'DC' AND end_season = %s
                        """, (team_id, concluded_season))

                elif incoming_slant == "DEFENSIVE":
                    # Defensive scheme imported: close old DC tenure and insert new DC
                    cur.execute("""
                        UPDATE coordinator_tenures
                        SET end_season = %s
                        WHERE team_id = %s AND role = 'DC'
                          AND start_season <= %s
                          AND (end_season IS NULL OR end_season > %s)
                    """, (concluded_season, team_id, concluded_season, concluded_season))

                    dc_name = f"{incoming_coach_name} Defensive Lieutenant"
                    cur.execute("""
                        INSERT INTO coordinators (coordinator_name, side, scheme, playbook, blitz_rate, man_coverage)
                        VALUES (%s, 'DEFENSIVE', %s, %s, 'Medium', 'Medium')
                        RETURNING id
                    """, (dc_name, effective_def, f"{team_info['name']} Defense"))
                    new_dc_id = cur.fetchone()["id"]
                    cur.execute("""
                        INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season)
                        VALUES (%s, %s, 'DC', %s, NULL)
                    """, (new_dc_id, team_id, effective_season))

                    # Retain OC: verify active OC exists
                    cur.execute("""
                        SELECT id FROM coordinator_tenures
                        WHERE team_id = %s AND role = 'OC'
                          AND start_season <= %s AND (end_season IS NULL OR end_season >= %s)
                    """, (team_id, effective_season, effective_season))
                    if not cur.fetchone():
                        cur.execute("""
                            UPDATE coordinator_tenures
                            SET end_season = NULL
                            WHERE team_id = %s AND role = 'OC' AND end_season = %s
                        """, (team_id, concluded_season))

                else:  # CEO Slant
                    # Verify both OC and DC tenures are active into effective_season
                    for role in ("OC", "DC"):
                        cur.execute("""
                            SELECT id FROM coordinator_tenures
                            WHERE team_id = %s AND role = %s
                              AND start_season <= %s AND (end_season IS NULL OR end_season >= %s)
                        """, (team_id, role, effective_season, effective_season))
                        if not cur.fetchone():
                            cur.execute("""
                                UPDATE coordinator_tenures
                                SET end_season = NULL
                                WHERE team_id = %s AND role = %s AND end_season = %s
                            """, (team_id, role, concluded_season))

                results["applied_moves"].append({
                    "team": team_info["abbreviation"],
                    "incoming_coach": incoming_coach_name,
                    "incoming_slant": incoming_slant,
                    "effective_season": effective_season,
                    "status": "APPLIED",
                })

            # 6. Invariant Verification: exactly 1 HC, 1 OC, 1 DC active per touched team in effective_season
            for tid in touched_team_ids:
                eff_s = concluded_season + 1
                cur.execute("""
                    SELECT COUNT(*) AS cnt FROM coach_tenures
                    WHERE team_id = %s AND start_season <= %s AND (end_season IS NULL OR end_season >= %s)
                """, (tid, eff_s, eff_s))
                hc_cnt = cur.fetchone()["cnt"]

                cur.execute("""
                    SELECT COUNT(*) AS cnt FROM coordinator_tenures
                    WHERE team_id = %s AND role = 'OC' AND start_season <= %s AND (end_season IS NULL OR end_season >= %s)
                """, (tid, eff_s, eff_s))
                oc_cnt = cur.fetchone()["cnt"]

                cur.execute("""
                    SELECT COUNT(*) AS cnt FROM coordinator_tenures
                    WHERE team_id = %s AND role = 'DC' AND start_season <= %s AND (end_season IS NULL OR end_season >= %s)
                """, (tid, eff_s, eff_s))
                dc_cnt = cur.fetchone()["cnt"]

                if hc_cnt != 1 or oc_cnt != 1 or dc_cnt != 1:
                    raise ValueError(
                        f"Staff invariant violation for team ID {tid} in season {eff_s}: "
                        f"expected exactly 1 HC (found {hc_cnt}), 1 OC (found {oc_cnt}), 1 DC (found {dc_cnt})"
                    )

            results["touched_teams"] = sorted(list(touched_team_ids))

            if dry_run:
                raise DryRunRollback()

    conn_str = conn_info or CONN_INFO
    if conn is not None:
        try:
            with conn.transaction():
                _execute_moves(conn)
        except DryRunRollback:
            pass
    else:
        with psycopg.connect(conn_str, row_factory=dict_row) as managed_conn:
            try:
                with managed_conn.transaction():
                    _execute_moves(managed_conn)
            except DryRunRollback:
                pass

    return results


def build_event_facts_packet(
    event_type: str,
    manifest: Dict[str, Any],
    conn: Optional[Any] = None,
    conn_info: Optional[str] = None,
) -> Dict[str, Any]:
    """Generates an anti-leakage facts bundle for each offseason story type:
    - super_bowl: Concluded season & Super Bowl game stats; NO post-game events.
    - coaching: Outgoing coach record, incoming coach slant/background/coordinator origin,
                scheme changes vs retained unit schemes; NO offseason signings or draft.
    - signings: 90+ OVR signings with OVR converted to qualitative tiers. Checks awards table
                for actual AP All-Pro honors before allowing the term. NO draft picks.
    - draft: Actual Round 1 picks from draft_prospects comparing actual_pick vs projected_pick.
    """
    manifest_data = load_manifest(manifest)
    season = manifest_data["season"]
    event_key = event_type.lower().strip()

    conn_str = conn_info or CONN_INFO
    owns_conn = conn is None
    active_conn = conn if conn is not None else psycopg.connect(conn_str, row_factory=dict_row)

    try:
        with active_conn.cursor() as cur:
            if event_key in ("super_bowl", "super_bowl_game_recap", "super_bowl_champion_retrospective", "super_bowl_runner_up_postmortem"):
                # Query Super Bowl game
                cur.execute("""
                    SELECT g.id, g.season, g.week, g.home_team_id, g.away_team_id, g.home_score, g.away_score,
                           ht.name as home_name, ht.city as home_city, ht.abbreviation as home_abbr,
                           at.name as away_name, at.city as away_city, at.abbreviation as away_abbr
                    FROM games g
                    JOIN teams ht ON ht.id = g.home_team_id
                    JOIN teams at ON at.id = g.away_team_id
                    WHERE g.season = %s AND (g.game_type = 'SUPER_BOWL' OR g.week = 23)
                    ORDER BY g.week DESC LIMIT 1
                """, (season,))
                sb_game = cur.fetchone()
                if not sb_game:
                    raise ValueError(f"No Super Bowl game found in games table for season {season}")

                home_won = sb_game["home_score"] > sb_game["away_score"]
                champ_id = sb_game["home_team_id"] if home_won else sb_game["away_team_id"]
                runner_up_id = sb_game["away_team_id"] if home_won else sb_game["home_team_id"]
                champ_abbr = sb_game["home_abbr"] if home_won else sb_game["away_abbr"]
                champ_name = f"{sb_game['home_city']} {sb_game['home_name']}" if home_won else f"{sb_game['away_city']} {sb_game['away_name']}"
                runner_abbr = sb_game["away_abbr"] if home_won else sb_game["home_abbr"]
                runner_name = f"{sb_game['away_city']} {sb_game['away_name']}" if home_won else f"{sb_game['home_city']} {sb_game['home_name']}"
                champ_score = max(sb_game["home_score"], sb_game["away_score"])
                runner_score = min(sb_game["home_score"], sb_game["away_score"])

                # Pull game box score
                from newsroom.db_tools import get_game_box_score
                box_score_fn = getattr(get_game_box_score, "func", get_game_box_score)
                box_score_data = json.loads(box_score_fn(sb_game["id"]))

                return {
                    "event_type": "super_bowl",
                    "season": season,
                    "publication_week": 24,
                    "game_id": sb_game["id"],
                    "matchup": f"{champ_name} ({champ_abbr}) {champ_score}, {runner_name} ({runner_abbr}) {runner_score}",
                    "champion": {
                        "team_id": champ_id,
                        "abbreviation": champ_abbr,
                        "name": champ_name,
                        "super_bowl_score": champ_score,
                    },
                    "runner_up": {
                        "team_id": runner_up_id,
                        "abbreviation": runner_abbr,
                        "name": runner_name,
                        "super_bowl_score": runner_score,
                    },
                    "box_score": box_score_data,
                    "anti_leakage_boundary": "Concluded season through Super Bowl Sunday only. Strictly no offseason transactions, coaching moves, offseason free-agent arrivals/departures, or draft picks. In-season signings and releases are part of the season.",
                }

            elif event_key in ("coaching", "coaching_carousel", "coaching_carousel_audit"):
                coaching_packet = []
                for move in manifest_data.get("coaching_moves", []):
                    t_info = resolve_team(active_conn, move["team"])
                    team_id = t_info["id"]
                    out_name = move.get("outgoing_coach")

                    # Outgoing coach record
                    outgoing_record = None
                    if out_name:
                        cur.execute("SELECT id FROM coaches WHERE coach_name ILIKE %s", (out_name.strip(),))
                        out_c = cur.fetchone()
                        if out_c:
                            from engines.coach_profile import season_record, tenure_record
                            cur.execute("""
                                SELECT season, week, home_team_id, away_team_id, home_score, away_score, game_type
                                FROM games WHERE completed_at IS NOT NULL AND season <= %s
                            """, (season,))
                            games_list = [dict(r) for r in cur.fetchall()]
                            s_rec = season_record(games_list, team_id, season)
                            outgoing_record = {
                                "coach_name": out_name,
                                "final_season_record": s_rec.get("regular_season"),
                                "final_playoff_record": s_rec.get("playoffs"),
                            }

                    origin_desc = None
                    if move.get("origin"):
                        orig = move["origin"]
                        if isinstance(orig, dict):
                            origin_desc = f"{orig.get('role')} with {orig.get('team')}"
                        else:
                            origin_desc = str(orig)

                    coaching_packet.append({
                        "team": t_info["abbreviation"],
                        "team_name": f"{t_info['city']} {t_info['name']}",
                        "outgoing_coach": outgoing_record or {"coach_name": out_name},
                        "incoming_coach": move["incoming_coach"],
                        "incoming_slant": move["incoming_slant"],
                        "origin": origin_desc,
                        "scheme_inheritance": {
                            "imported_offensive_scheme": move.get("imported_offensive_scheme"),
                            "retained_defensive_scheme": move.get("retained_defensive_scheme"),
                            "imported_defensive_scheme": move.get("imported_defensive_scheme"),
                            "retained_offensive_scheme": move.get("retained_offensive_scheme"),
                        },
                        "notes": move.get("notes"),
                    })

                return {
                    "event_type": "coaching",
                    "season": season,
                    "publication_week": 24,
                    "coaching_moves": coaching_packet,
                    "anti_leakage_boundary": "Concluded season and submitted coaching moves only. Strictly no offseason free-agency signings, offseason trades or draft selections. In-season moves are season history.",
                }

            elif event_key in ("signings", "free_agency", "free_agency_signing_grades"):
                signings_packet = []
                try:
                    with active_conn.transaction():
                        txn_rows = fetch_offseason_signings(active_conn, season)
                except Exception:
                    txn_rows = []
                for s in manifest_data.get("signings", []):
                    f_team = resolve_team(active_conn, s["former_team"])
                    n_team = resolve_team(active_conn, s["new_team"])

                    # Qualitative Tiering: Strip OVR completely
                    qual_tier = convert_ovr_to_qualitative_tier(s["ovr"])
                    all_pro_info = check_player_all_pro_honors(active_conn, s["player_name"])

                    signings_packet.append({
                        "player_name": s["player_name"],
                        "position": s["position"],
                        "former_team": f_team["abbreviation"],
                        "new_team": n_team["abbreviation"],
                        "qualitative_tier": qual_tier,
                        "all_pro_verification": all_pro_info,
                        "contract_notes": s.get("contract_notes"),
                        "signing_terms": match_signing_terms(s, txn_rows),
                        "role_notes": s.get("role_notes"),
                    })

                return {
                    "event_type": "signings",
                    "season": season,
                    "publication_week": 24,
                    "marquee_signings": signings_packet,
                    "anti_leakage_boundary": "Concluded season, coaching carousel updates, and submitted marquee 90+ signings (qualitative tiers only). Contract terms only as cited from the signing terms or get_player_contract; never infer a missing term. Strictly no draft selections.",
                }

            elif event_key in ("draft", "draft_class", "nfl_draft_class_recap"):
                # Fetch actual round 1 picks from draft_prospects for the single target class
                # Prioritize canonical incoming rookie class (season + 1); fallback to (season) if stored under concluded year
                cur.execute("""
                    SELECT dp.actual_pick, dp.full_name, dp.position, dp.college, dp.projected_pick,
                           dp.selection_notes, t.abbreviation as drafted_team_abbr, t.name as drafted_team_name
                    FROM draft_prospects dp
                    LEFT JOIN teams t ON t.id = dp.drafted_team_id
                    WHERE dp.draft_season = %s AND dp.actual_pick IS NOT NULL
                    ORDER BY dp.actual_pick ASC
                """, (season + 1,))
                db_picks = cur.fetchall()
                if not db_picks:
                    cur.execute("""
                        SELECT dp.actual_pick, dp.full_name, dp.position, dp.college, dp.projected_pick,
                               dp.selection_notes, t.abbreviation as drafted_team_abbr, t.name as drafted_team_name
                        FROM draft_prospects dp
                        LEFT JOIN teams t ON t.id = dp.drafted_team_id
                        WHERE dp.draft_season = %s AND dp.actual_pick IS NOT NULL
                        ORDER BY dp.actual_pick ASC
                    """, (season,))
                    db_picks = cur.fetchall()

                picks_packet = []
                if db_picks and len(db_picks) >= 32:
                    for p in db_picks[:32]:
                        act = p["actual_pick"]
                        proj = p["projected_pick"]
                        diff = (proj - act) if proj else None
                        if diff is None:
                            val_cat = "Unranked / Off-Board Selection"
                        elif diff >= 5:
                            val_cat = "Value / Steal"
                        elif diff <= -5:
                            val_cat = "Reach / Aggressive Bet"
                        else:
                            val_cat = "Drafted Near Projection"

                        picks_packet.append({
                            "actual_pick": act,
                            "drafted_team": p["drafted_team_abbr"] or "Unknown",
                            "player_name": p["full_name"],
                            "position": p["position"],
                            "college": p["college"],
                            "projected_pick": proj,
                            "pick_delta": diff,
                            "value_category": val_cat,
                            "selection_notes": p["selection_notes"],
                        })
                else:
                    # Fallback to manifest draft_results source file or list if DB not loaded yet
                    dr = manifest_data.get("draft_results")
                    if isinstance(dr, dict) and "source_file" in dr:
                        p_file = Path(dr["source_file"])
                        if not p_file.is_absolute():
                            p_file = ROOT_DIR / p_file
                        if p_file.exists():
                            from pipeline.draft_results import parse_raw_picks
                            if p_file.suffix.lower() == ".csv":
                                import csv
                                with open(p_file, "r", encoding="utf-8") as fh:
                                    raw_picks = list(csv.DictReader(fh))
                            else:
                                with open(p_file, "r", encoding="utf-8") as fh:
                                    raw_picks = json.load(fh)
                            parsed = parse_raw_picks(raw_picks)
                            for item in sorted(parsed, key=lambda x: x["overall_pick"])[:32]:
                                picks_packet.append({
                                    "actual_pick": item["overall_pick"],
                                    "drafted_team": str(item["team_val"]),
                                    "player_name": item["player_name"],
                                    "position": item["position"],
                                    "college": item["college"],
                                    "projected_pick": None,
                                    "value_category": "Manifest Ingestion Pending",
                                    "selection_notes": item.get("notes"),
                                })

                return {
                    "event_type": "draft",
                    "season": season,
                    "publication_week": 24,
                    "first_round_picks_count": len(picks_packet),
                    "picks": picks_packet,
                    "anti_leakage_boundary": "First-Round draft selections exclusively (picks 1–32). Full offseason context available.",
                }

            else:
                raise ValueError(f"Unknown offseason event_type '{event_type}'")

    finally:
        if owns_conn and active_conn:
            active_conn.close()
