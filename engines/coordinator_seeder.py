"""Coordinator and coach slant seeder for Project Mu.
(COACH_SLANTS_AND_COORDINATORS_PLAN.md §3, §4).

Seeds:
1. Baseline slants (OFFENSIVE, DEFENSIVE, CEO) for head coaches in `coaches`.
2. Baseline Offensive Coordinators and Defensive Coordinators in `coordinators` and `coordinator_tenures`.
3. Procedural fallback for franchises or coordinators missing explicit reference data.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Baseline slants for all 33 baseline coaches and coaching candidates (§3.1)
COACH_BASELINE_SLANTS: Dict[str, str] = {
    # 2026 Head Coaches + Marcus Whitfield (2034)
    "Marcus Whitfield": "OFFENSIVE",
    "Dave Canales": "OFFENSIVE",
    "Mike LaFleur": "OFFENSIVE",
    "Kevin Stefanski": "OFFENSIVE",
    "Joe Brady": "OFFENSIVE",
    "Ben Johnson": "OFFENSIVE",
    "Zac Taylor": "OFFENSIVE",
    "Todd Monken": "OFFENSIVE",
    "Brian Schottenheimer": "OFFENSIVE",
    "Peter Gaffney": "OFFENSIVE",  # EA Sean Payton proxy
    "Sean Payton": "OFFENSIVE",
    "Matt LaFleur": "OFFENSIVE",
    "Shane Steichen": "OFFENSIVE",
    "Liam Coen": "OFFENSIVE",
    "Ron Leonard": "OFFENSIVE",    # EA Andy Reid proxy
    "Andy Reid": "OFFENSIVE",
    "Klint Kubiak": "OFFENSIVE",
    "Sean McVay": "OFFENSIVE",
    "Kevin O'Connell": "OFFENSIVE",
    "Kellen Moore": "OFFENSIVE",
    "Nick Sirianni": "OFFENSIVE",
    "Mike McCarthy": "OFFENSIVE",
    "Kyle Shanahan": "OFFENSIVE",

    # Defensive Specialists
    "Jesse Minter": "DEFENSIVE",
    "Robert Saleh": "DEFENSIVE",
    "DeMeco Ryans": "DEFENSIVE",
    "Jeff Hafley": "DEFENSIVE",
    "Mike Vrabel": "DEFENSIVE",
    "Aaron Glenn": "DEFENSIVE",
    "Mike Macdonald": "DEFENSIVE",
    "Todd Bowles": "DEFENSIVE",
    "Dan Quinn": "DEFENSIVE",

    # CEO / Cultural & Game Management Architects
    "Dan Campbell": "CEO",
    "John Harbaugh": "CEO",
    "Jim Harbaugh": "CEO",

    # Additional carousel candidates & historical notables (§3.1)
    "Mike McDaniel": "OFFENSIVE",
    "Brian Callahan": "OFFENSIVE",
    "Doug Pederson": "OFFENSIVE",
    "Arthur Smith": "OFFENSIVE",
    "Sean McDermott": "DEFENSIVE",
    "Raheem Morris": "DEFENSIVE",
    "Jerod Mayo": "DEFENSIVE",
    "Jonathan Gannon": "DEFENSIVE",
    "Matt Eberflus": "DEFENSIVE",
    "Dennis Allen": "DEFENSIVE",
    "Mike Tomlin": "CEO",
}

# 2026 Reference Coordinators for all 32 NFL franchises
# Sourced from team schemes, 2026_11_01_coach_schemes.sql comments, and Madden 2026 baseline
BASELINE_STAFF_REFERENCE: Dict[str, Dict[str, Dict[str, Any]]] = {
    "ATL": {
        "OC": {"name": "Tommy Rees", "scheme": "Wide Zone West Coast", "playbook": "Atlanta Offense", "pass_lean": "Low"},
        "DC": {"name": "Jeff Ulbrich", "scheme": "4-3", "playbook": "Atlanta Defense", "blitz_rate": "High", "man_coverage": "Medium"},
    },
    "AZ": {
        "OC": {"name": "Drew Terrell", "scheme": "Wide Zone West Coast", "playbook": "Arizona Offense", "pass_lean": "High"},
        "DC": {"name": "Nick Rallis", "scheme": "3-4", "playbook": "Arizona Defense", "blitz_rate": "Medium", "man_coverage": "Medium"},
    },
    "BAL": {
        "OC": {"name": "Declan Doyle", "scheme": "Wide Zone West Coast", "playbook": "Baltimore Offense", "pass_lean": "Medium"},
        "DC": {"name": "Anthony Weaver", "scheme": "3-4", "playbook": "Baltimore Defense", "blitz_rate": "Low", "man_coverage": "Low"},
    },
    "BUF": {
        "OC": {"name": "Pete Carmichael Jr.", "scheme": "Spread", "playbook": "Buffalo Offense", "pass_lean": "Medium"},
        "DC": {"name": "Jim Leonhard", "scheme": "3-4", "playbook": "Buffalo Defense", "blitz_rate": "High", "man_coverage": "High"},
    },
    "CAR": {
        # Carolina: Dave Canales (2026-2033), Marcus Whitfield served as Panthers OC prior to taking over as HC in 2034 (§4.2)
        "OC_2026": {"name": "Marcus Whitfield", "scheme": "Wide Zone West Coast", "playbook": "Carolina Offense", "pass_lean": "Low", "origin_notes": "Panthers OC under Dave Canales (2026-2033); promoted to HC in 2034"},
        "OC_2034": {"name": "Brad Idzik", "scheme": "Wide Zone West Coast", "playbook": "Carolina Offense", "pass_lean": "Low", "origin_notes": "Panthers OC under Marcus Whitfield from 2034"},
        "DC": {"name": "Ejiro Evero", "scheme": "3-4", "playbook": "Carolina Defense", "blitz_rate": "Medium", "man_coverage": "Low", "origin_notes": "Fangio defensive tree"},
    },
    "CHI": {
        "OC": {"name": "Thomas Brown", "scheme": "Wide Zone West Coast", "playbook": "Chicago Offense", "pass_lean": "Medium"},
        "DC": {"name": "Dennis Allen", "scheme": "4-3", "playbook": "Chicago Defense", "blitz_rate": "Medium", "man_coverage": "High"},
    },
    "CIN": {
        "OC": {"name": "Dan Pitcher", "scheme": "Spread", "playbook": "Cincinnati Offense", "pass_lean": "High"},
        "DC": {"name": "Al Golden", "scheme": "4-3", "playbook": "Cincinnati Defense", "blitz_rate": "Low", "man_coverage": "High"},
    },
    "CLE": {
        "OC": {"name": "Ken Dorsey", "scheme": "Spread", "playbook": "Cleveland Offense", "pass_lean": "Low"},
        "DC": {"name": "Mike Rutenberg", "scheme": "4-3", "playbook": "Cleveland Defense", "blitz_rate": "High", "man_coverage": "Medium"},
    },
    "DAL": {
        "OC": {"name": "Scott Tolzien", "scheme": "West Coast", "playbook": "Dallas Offense", "pass_lean": "High"},
        "DC": {"name": "Christian Parker", "scheme": "3-4", "playbook": "Dallas Defense", "blitz_rate": "Low", "man_coverage": "High"},
    },
    "DEN": {
        "OC": {"name": "Joe Lombardi", "scheme": "West Coast", "playbook": "Denver Offense", "pass_lean": "High"},
        "DC": {"name": "Vance Joseph", "scheme": "3-4", "playbook": "Denver Defense", "blitz_rate": "High", "man_coverage": "High"},
    },
    "DET": {
        "OC": {"name": "Drew Petzing", "scheme": "West Coast", "playbook": "Detroit Offense", "pass_lean": "High"},
        "DC": {"name": "Kelvin Sheppard", "scheme": "4-3", "playbook": "Detroit Defense", "blitz_rate": "Medium", "man_coverage": "High"},
    },
    "GB": {
        "OC": {"name": "Adam Stenavich", "scheme": "Wide Zone West Coast", "playbook": "Green Bay Offense", "pass_lean": "Low"},
        "DC": {"name": "Jonathan Gannon", "scheme": "3-4", "playbook": "Green Bay Defense", "blitz_rate": "Medium", "man_coverage": "Medium"},
    },
    "HOU": {
        "OC": {"name": "Nick Caley", "scheme": "Wide Zone West Coast", "playbook": "Houston Offense", "pass_lean": "Medium"},
        "DC": {"name": "Matt Burke", "scheme": "4-3", "playbook": "Houston Defense", "blitz_rate": "Low", "man_coverage": "Medium"},
    },
    "IND": {
        "OC": {"name": "Jim Bob Cooter", "scheme": "Spread", "playbook": "Indianapolis Offense", "pass_lean": "High"},
        "DC": {"name": "Lou Anarumo", "scheme": "4-3", "playbook": "Indianapolis Defense", "blitz_rate": "High", "man_coverage": "High"},
    },
    "JAX": {
        "OC": {"name": "Press Taylor", "scheme": "Wide Zone West Coast", "playbook": "Jacksonville Offense", "pass_lean": "High"},
        "DC": {"name": "Anthony Campanile", "scheme": "4-3", "playbook": "Jacksonville Defense", "blitz_rate": "Medium", "man_coverage": "Low"},
    },
    "KC": {
        "OC": {"name": "Matt Nagy", "scheme": "West Coast", "playbook": "Kansas City Offense", "pass_lean": "High"},
        "DC": {"name": "Steve Spagnuolo", "scheme": "4-3", "playbook": "Kansas City Defense", "blitz_rate": "High", "man_coverage": "High"},
    },
    "LAC": {
        "OC": {"name": "Mike McDaniel", "scheme": "Wide Zone West Coast", "playbook": "LA Chargers Offense", "pass_lean": "Low"},
        "DC": {"name": "Chris O'Leary", "scheme": "3-4", "playbook": "LA Chargers Defense", "blitz_rate": "Low", "man_coverage": "Low"},
    },
    "LAR": {
        "OC": {"name": "Thomas Brown", "scheme": "Wide Zone West Coast", "playbook": "LA Rams Offense", "pass_lean": "High"},
        "DC": {"name": "Chris Shula", "scheme": "3-4", "playbook": "LA Rams Defense", "blitz_rate": "Low", "man_coverage": "Low"},
    },
    "LV": {
        "OC": {"name": "Luke Getsy", "scheme": "Wide Zone West Coast", "playbook": "Las Vegas Offense", "pass_lean": "Low"},
        "DC": {"name": "Rob Leonard", "scheme": "4-3", "playbook": "Las Vegas Defense", "blitz_rate": "Low", "man_coverage": "Low"},
    },
    "MIA": {
        "OC": {"name": "Bobby Slowik", "scheme": "Wide Zone West Coast", "playbook": "Miami Offense", "pass_lean": "High"},
        "DC": {"name": "Sean Duggan", "scheme": "4-3", "playbook": "Miami Defense", "blitz_rate": "Low", "man_coverage": "Low"},
    },
    "MIN": {
        "OC": {"name": "Wes Phillips", "scheme": "Wide Zone West Coast", "playbook": "Minnesota Offense", "pass_lean": "Medium"},
        "DC": {"name": "Brian Flores", "scheme": "3-4", "playbook": "Minnesota Defense", "blitz_rate": "High", "man_coverage": "Low"},
    },
    "NE": {
        "OC": {"name": "Josh McDaniels", "scheme": "Erhardt-Perkins", "playbook": "New England Offense", "pass_lean": "High"},
        "DC": {"name": "Zak Kuhr", "scheme": "3-4", "playbook": "New England Defense", "blitz_rate": "High", "man_coverage": "Medium"},
    },
    "NO": {
        "OC": {"name": "Ronald Curry", "scheme": "Spread", "playbook": "New Orleans Offense", "pass_lean": "Medium"},
        "DC": {"name": "Brandon Staley", "scheme": "3-4", "playbook": "New Orleans Defense", "blitz_rate": "Medium", "man_coverage": "Low"},
    },
    "NYG": {
        "OC": {"name": "Mike Kafka", "scheme": "West Coast", "playbook": "NY Giants Offense", "pass_lean": "High"},
        "DC": {"name": "Dennard Wilson", "scheme": "3-4", "playbook": "NY Giants Defense", "blitz_rate": "Low", "man_coverage": "Low"},
    },
    "NYJ": {
        "OC": {"name": "Frank Reich", "scheme": "West Coast", "playbook": "NY Jets Offense", "pass_lean": "Low"},
        "DC": {"name": "Brian Duker", "scheme": "4-3", "playbook": "NY Jets Defense", "blitz_rate": "Medium", "man_coverage": "High"},
    },
    "PHI": {
        "OC": {"name": "Kevin Patullo", "scheme": "Spread", "playbook": "Philadelphia Offense", "pass_lean": "Medium"},
        "DC": {"name": "Vic Fangio", "scheme": "3-4", "playbook": "Philadelphia Defense", "blitz_rate": "Low", "man_coverage": "High"},
    },
    "PIT": {
        "OC": {"name": "Arthur Smith", "scheme": "West Coast", "playbook": "Pittsburgh Offense", "pass_lean": "Medium"},
        "DC": {"name": "Patrick Graham", "scheme": "3-4", "playbook": "Pittsburgh Defense", "blitz_rate": "Low", "man_coverage": "Low"},
    },
    "SEA": {
        "OC": {"name": "Brian Fleury", "scheme": "Wide Zone West Coast", "playbook": "Seattle Offense", "pass_lean": "Low"},
        "DC": {"name": "Aden Durde", "scheme": "3-4", "playbook": "Seattle Defense", "blitz_rate": "Low", "man_coverage": "Medium"},
    },
    "SF": {
        "OC": {"name": "Klay Kubiak", "scheme": "Wide Zone West Coast", "playbook": "San Francisco Offense", "pass_lean": "High"},
        "DC": {"name": "Raheem Morris", "scheme": "4-3", "playbook": "San Francisco Defense", "blitz_rate": "Low", "man_coverage": "Low"},
    },
    "TB": {
        "OC": {"name": "Zac Robinson", "scheme": "Wide Zone West Coast", "playbook": "Tampa Bay Offense", "pass_lean": "Low"},
        "DC": {"name": "Larry Foote", "scheme": "3-4", "playbook": "Tampa Bay Defense", "blitz_rate": "High", "man_coverage": "Medium"},
    },
    "TEN": {
        "OC": {"name": "Brian Daboll", "scheme": "Erhardt-Perkins", "playbook": "Tennessee Offense", "pass_lean": "Low"},
        "DC": {"name": "Gus Bradley", "scheme": "4-3", "playbook": "Tennessee Defense", "blitz_rate": "Low", "man_coverage": "Low"},
    },
    "WAS": {
        "OC": {"name": "David Blough", "scheme": "Spread", "playbook": "Washington Offense", "pass_lean": "Medium"},
        "DC": {"name": "Joe Whitt Jr.", "scheme": "4-3", "playbook": "Washington Defense", "blitz_rate": "Medium", "man_coverage": "Medium"},
    },
}

# Procedural name components for fallback coordinator generation
PROCEDURAL_FIRST_NAMES = [
    "Dave", "Tom", "Greg", "Mark", "Bill", "Steve", "Jim", "John", "Dan",
    "Mike", "Chris", "Kevin", "Brian", "Matt", "Sean", "Jeff", "Brett",
    "Kyle", "Ryan", "Chad", "Todd", "Brad", "Scott", "Eric", "Aaron"
]
PROCEDURAL_LAST_NAMES = [
    "Callahan", "Vance", "Lombardi", "Holmgren", "Coughlin", "Martindale",
    "Zimmer", "Bowles", "Fangio", "Gruden", "Pederson", "Nagy", "Koetter",
    "Bevell", "Turner", "Carmichael", "Rees", "Terrell", "Durde", "Foote"
]


def generate_procedural_name(team_abbr: str, role: str) -> str:
    """Generates a deterministic, plausible coordinator name based on team and role."""
    seed_val = sum(ord(c) for c in team_abbr) + sum(ord(c) for c in role)
    first = PROCEDURAL_FIRST_NAMES[seed_val % len(PROCEDURAL_FIRST_NAMES)]
    last = PROCEDURAL_LAST_NAMES[(seed_val // 2) % len(PROCEDURAL_LAST_NAMES)]
    return f"{first} {last}"


def seed_coach_slants(cur) -> int:
    """Updates slant column for all head coaches based on COACH_BASELINE_SLANTS.

    Returns the count of coaches updated.
    """
    cur.execute("SELECT id, coach_name FROM coaches;")
    coaches = cur.fetchall()
    updated = 0
    for coach_id, name in coaches:
        slant = COACH_BASELINE_SLANTS.get(name, "OFFENSIVE")
        cur.execute("UPDATE coaches SET slant = %s WHERE id = %s", (slant, coach_id))
        updated += 1
    return updated


def _get_or_create_coordinator(cur, name: str, side: str, scheme: str, playbook: Optional[str] = None,
                               pass_lean: Optional[str] = None, blitz_rate: Optional[str] = None,
                               man_coverage: Optional[str] = None, origin_notes: Optional[str] = None) -> int:
    """Finds existing coordinator by name and side, or inserts a new record."""
    cur.execute("SELECT id FROM coordinators WHERE coordinator_name = %s AND side = %s LIMIT 1", (name, side))
    row = cur.fetchone()
    if row:
        coord_id = row[0]
        # Keep scheme and playbook up to date if specified
        cur.execute("""
            UPDATE coordinators SET scheme = %s, playbook = COALESCE(%s, playbook),
                                    pass_lean = COALESCE(%s, pass_lean), blitz_rate = COALESCE(%s, blitz_rate),
                                    man_coverage = COALESCE(%s, man_coverage), origin_notes = COALESCE(%s, origin_notes)
            WHERE id = %s
        """, (scheme, playbook, pass_lean, blitz_rate, man_coverage, origin_notes, coord_id))
        return coord_id

    cur.execute("""
        INSERT INTO coordinators (coordinator_name, side, scheme, playbook, pass_lean, blitz_rate, man_coverage, origin_notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (name, side, scheme, playbook, pass_lean, blitz_rate, man_coverage, origin_notes))
    return cur.fetchone()[0]


def _ensure_tenure(cur, coordinator_id: int, team_id: int, role: str, start_season: int, end_season: Optional[int]):
    """Inserts or updates coordinator tenure idempotently."""
    cur.execute("""
        INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (coordinator_id, team_id, role, start_season)
        DO UPDATE SET end_season = EXCLUDED.end_season
    """, (coordinator_id, team_id, role, start_season, end_season))


def seed_coordinators(cur, reference_data: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """Seeds baseline Offensive and Defensive Coordinators for all franchises in `teams`.

    Handles:
    - Carolina narrative (§4.2): Whitfield as OC (2026-2033), Brad Idzik as OC (2034-present),
      Ejiro Evero as DC (2026-present).
    - All other teams starting in 2026.
    - Procedural fallback for teams or coordinator slots missing reference data.

    Returns dict with summary counts: {'coordinators': int, 'tenures': int}.
    """
    staff_data = reference_data or BASELINE_STAFF_REFERENCE

    # Fetch all teams
    cur.execute("SELECT id, city, name, abbreviation FROM teams ORDER BY abbreviation")
    teams = cur.fetchall()

    coord_count = 0
    tenure_count = 0

    for team_id, city, team_name, abbr in teams:
        ref = staff_data.get(abbr, {})

        # Query team's 2026 head coach info for procedural fallback defaults
        cur.execute("""
            SELECT c.offensive_scheme, c.defensive_scheme, c.pass_lean, c.blitz_rate, c.man_coverage
            FROM coach_tenures ct
            JOIN coaches c ON c.id = ct.coach_id
            WHERE ct.team_id = %s AND ct.start_season <= 2026 AND COALESCE(ct.end_season, 9999) >= 2026
            ORDER BY ct.start_season DESC LIMIT 1
        """, (team_id,))
        hc_row = cur.fetchone()
        hc_off = hc_row[0] if hc_row and hc_row[0] else "Wide Zone West Coast"
        hc_def = hc_row[1] if hc_row and hc_row[1] else "4-3"
        hc_pass_lean = hc_row[2] if hc_row and hc_row[2] else "Medium"
        hc_blitz = hc_row[3] if hc_row and hc_row[3] else "Medium"
        hc_man = hc_row[4] if hc_row and hc_row[4] else "Medium"

        # Special handling for Carolina Panthers (§4.2)
        if abbr == "CAR":
            # 1. Marcus Whitfield as OC 2026-2033
            oc_26_ref = ref.get("OC_2026", {})
            car_oc_1_id = _get_or_create_coordinator(
                cur,
                name=oc_26_ref.get("name", "Marcus Whitfield"),
                side="OFFENSIVE",
                scheme=oc_26_ref.get("scheme", hc_off),
                playbook=oc_26_ref.get("playbook", f"{city} Offense"),
                pass_lean=oc_26_ref.get("pass_lean", hc_pass_lean),
                origin_notes=oc_26_ref.get("origin_notes", "Panthers OC under Dave Canales (2026-2033)")
            )
            _ensure_tenure(cur, car_oc_1_id, team_id, "OC", start_season=2026, end_season=2033)

            # 2. Brad Idzik as OC 2034-present
            oc_34_ref = ref.get("OC_2034", {})
            car_oc_2_id = _get_or_create_coordinator(
                cur,
                name=oc_34_ref.get("name", "Brad Idzik"),
                side="OFFENSIVE",
                scheme=oc_34_ref.get("scheme", hc_off),
                playbook=oc_34_ref.get("playbook", f"{city} Offense"),
                pass_lean=oc_34_ref.get("pass_lean", hc_pass_lean),
                origin_notes=oc_34_ref.get("origin_notes", "Panthers OC under Marcus Whitfield from 2034")
            )
            _ensure_tenure(cur, car_oc_2_id, team_id, "OC", start_season=2034, end_season=None)

            # 3. Ejiro Evero as DC 2026-present
            dc_ref = ref.get("DC", {})
            car_dc_id = _get_or_create_coordinator(
                cur,
                name=dc_ref.get("name", "Ejiro Evero"),
                side="DEFENSIVE",
                scheme=dc_ref.get("scheme", hc_def),
                playbook=dc_ref.get("playbook", f"{city} Defense"),
                blitz_rate=dc_ref.get("blitz_rate", hc_blitz),
                man_coverage=dc_ref.get("man_coverage", hc_man),
                origin_notes=dc_ref.get("origin_notes", "Fangio defensive tree DC")
            )
            _ensure_tenure(cur, car_dc_id, team_id, "DC", start_season=2026, end_season=None)

            coord_count += 3
            tenure_count += 3
            continue

        # Standard team seeding: 1 OC and 1 DC from 2026
        # Offensive Coordinator
        oc_ref = ref.get("OC")
        if oc_ref:
            oc_name = oc_ref.get("name", generate_procedural_name(abbr, "OC"))
            oc_scheme = oc_ref.get("scheme", hc_off)
            oc_playbook = oc_ref.get("playbook", f"{city} Offense")
            oc_pass_lean = oc_ref.get("pass_lean", hc_pass_lean)
            oc_notes = oc_ref.get("origin_notes", f"2026 baseline OC for {abbr}")
        else:
            # Procedural fallback
            oc_name = generate_procedural_name(abbr, "OC")
            oc_scheme = hc_off
            oc_playbook = f"{city} Offense"
            oc_pass_lean = hc_pass_lean
            oc_notes = f"Procedural baseline OC for {abbr} matching {hc_off}"

        oc_id = _get_or_create_coordinator(
            cur, name=oc_name, side="OFFENSIVE", scheme=oc_scheme,
            playbook=oc_playbook, pass_lean=oc_pass_lean, origin_notes=oc_notes
        )
        _ensure_tenure(cur, oc_id, team_id, "OC", start_season=2026, end_season=None)

        # Defensive Coordinator
        dc_ref = ref.get("DC")
        if dc_ref:
            dc_name = dc_ref.get("name", generate_procedural_name(abbr, "DC"))
            dc_scheme = dc_ref.get("scheme", hc_def)
            dc_playbook = dc_ref.get("playbook", f"{city} Defense")
            dc_blitz = dc_ref.get("blitz_rate", hc_blitz)
            dc_man = dc_ref.get("man_coverage", hc_man)
            dc_notes = dc_ref.get("origin_notes", f"2026 baseline DC for {abbr}")
        else:
            # Procedural fallback
            dc_name = generate_procedural_name(abbr, "DC")
            dc_scheme = hc_def
            dc_playbook = f"{city} Defense"
            dc_blitz = hc_blitz
            dc_man = hc_man
            dc_notes = f"Procedural baseline DC for {abbr} matching base front {hc_def}"

        dc_id = _get_or_create_coordinator(
            cur, name=dc_name, side="DEFENSIVE", scheme=dc_scheme,
            playbook=dc_playbook, blitz_rate=dc_blitz, man_coverage=dc_man, origin_notes=dc_notes
        )
        _ensure_tenure(cur, dc_id, team_id, "DC", start_season=2026, end_season=None)

        coord_count += 2
        tenure_count += 2

    return {"coordinators": coord_count, "tenures": tenure_count}


def seed_all_coordinators_and_slants(cur) -> Dict[str, Any]:
    """Runs slant seeding followed by coordinator and tenure seeding."""
    slants_updated = seed_coach_slants(cur)
    counts = seed_coordinators(cur)
    return {
        "slants_updated": slants_updated,
        "coordinators_seeded": counts["coordinators"],
        "tenures_seeded": counts["tenures"]
    }


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

    import psycopg
    from db.db_handler import DatabaseHandler

    db_handler = DatabaseHandler()
    with psycopg.connect(db_handler.db_conn_info) as conn:
        with conn.cursor() as cur:
            res = seed_all_coordinators_and_slants(cur)
            print(f"Coordinator and Slant seeding complete: {res}")
        conn.commit()
