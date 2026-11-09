"""
Project Mu: Clean Database Seed Script (db/seed_clean_db.py)

Populates a fresh PostgreSQL database (created via db/init_db.sql) with the canonical
prerequisites needed to start a brand new franchise save with native PC save loading:

  1. Schema verification (ensures init_db.sql tables exist, or applies it if empty)
  2. 32 NFL Franchises in `teams` (abbreviations, colors, stadiums, divisions)
  3. `league_state` initialized to the designated starting season and week (e.g. 2024, 2025, 2026)
  4. `gm_team_settings` populated for all 32 teams with user-control flags
  5. NFL Baseline Record Book (real NFL history through 2025: categories, franchise records, top 10s; the old
     universe's 2026-2035 'BOOK' rows that two of its migrations load are deleted)
  6. Real NFL Head Coaches & Coordinators with schemes, slants, and starting tenures
  7. 32 GM Personas & 3 Draft Classes of Draft Capital (`draft_picks` via engines.gm_personas)
  8. Newsroom writers: the 38 archetype personas (agent_personas.json) into `persona_sliders`, `persona_traits`,
     `persona_views`, the 201 writer/social accounts (newsroom/config/agent_accounts.json) into `agent_memories`
     (with their person_key), and their seeded writer relationships into `persona_relationships`
  9. Default Napper Fantasy user profile

Usage:
  python3 -m db.seed_clean_db [--season 2024] [--week 1] [--user-team IND] [--db <conn>] [--dry-run]
"""

import argparse
import json
import os
import sys
from pathlib import Path
import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_ACCOUNTS_PATH = REPO_ROOT / "newsroom" / "config" / "agent_accounts.json"

TEAMS_DATA = [
    # (name, city, abbreviation, team_color, stadium_name, conference, division)
    ("Cardinals", "Arizona", "AZ", "#97233F", "State Farm Stadium", "NFC", "NFC West"),
    ("Falcons", "Atlanta", "ATL", "#A71930", "Mercedes-Benz Stadium", "NFC", "NFC South"),
    ("Ravens", "Baltimore", "BAL", "#241773", "M&T Bank Stadium", "AFC", "AFC North"),
    ("Bills", "Buffalo", "BUF", "#00338D", "Highmark Stadium", "AFC", "AFC East"),
    ("Panthers", "Carolina", "CAR", "#0085CA", "Bank of America Stadium", "NFC", "NFC South"),
    ("Bears", "Chicago", "CHI", "#0B162A", "Soldier Field", "NFC", "NFC North"),
    ("Bengals", "Cincinnati", "CIN", "#FB4F14", "Paycor Stadium", "AFC", "AFC North"),
    ("Browns", "Cleveland", "CLE", "#311D00", "Cleveland Browns Stadium", "AFC", "AFC North"),
    ("Cowboys", "Dallas", "DAL", "#003594", "AT&T Stadium", "NFC", "NFC East"),
    ("Broncos", "Denver", "DEN", "#FB4F14", "Empower Field at Mile High", "AFC", "AFC West"),
    ("Lions", "Detroit", "DET", "#0076B6", "Ford Field", "NFC", "NFC North"),
    ("Packers", "Green Bay", "GB", "#203731", "Lambeau Field", "NFC", "NFC North"),
    ("Texans", "Houston", "HOU", "#03202F", "NRG Stadium", "AFC", "AFC South"),
    ("Colts", "Indianapolis", "IND", "#002C5F", "Lucas Oil Stadium", "AFC", "AFC South"),
    ("Jaguars", "Jacksonville", "JAX", "#006778", "EverBank Stadium", "AFC", "AFC South"),
    ("Chiefs", "Kansas City", "KC", "#E31837", "GEHA Field at Arrowhead", "AFC", "AFC West"),
    ("Raiders", "Las Vegas", "LV", "#000000", "Allegiant Stadium", "AFC", "AFC West"),
    ("Chargers", "Los Angeles", "LAC", "#0080C6", "SoFi Stadium", "AFC", "AFC West"),
    ("Rams", "Los Angeles", "LAR", "#003594", "SoFi Stadium", "NFC", "NFC West"),
    ("Dolphins", "Miami", "MIA", "#008E97", "Hard Rock Stadium", "AFC", "AFC East"),
    ("Vikings", "Minnesota", "MIN", "#4F2683", "U.S. Bank Stadium", "NFC", "NFC North"),
    ("Patriots", "New England", "NE", "#002244", "Gillette Stadium", "AFC", "AFC East"),
    ("Saints", "New Orleans", "NO", "#D3BC8D", "Caesars Superdome", "NFC", "NFC South"),
    ("Giants", "New York", "NYG", "#0B2265", "MetLife Stadium", "NFC", "NFC East"),
    ("Jets", "New York", "NYJ", "#125740", "MetLife Stadium", "AFC", "AFC East"),
    ("Eagles", "Philadelphia", "PHI", "#004C54", "Lincoln Financial Field", "NFC", "NFC East"),
    ("Steelers", "Pittsburgh", "PIT", "#FFB612", "Acrisure Stadium", "AFC", "AFC North"),
    ("49ers", "San Francisco", "SF", "#AA0000", "Levi's Stadium", "NFC", "NFC West"),
    ("Seahawks", "Seattle", "SEA", "#002244", "Lumen Field", "NFC", "NFC West"),
    ("Buccaneers", "Tampa Bay", "TB", "#D50A0A", "Raymond James Stadium", "NFC", "NFC South"),
    ("Titans", "Tennessee", "TEN", "#0C2340", "Nissan Stadium", "AFC", "AFC South"),
    ("Commanders", "Washington", "WAS", "#5A1414", "Commanders Field", "NFC", "NFC East"),
]

COACHES_DATA = [
    # Staffs as they stood on the eve of the 2026 season (opener 2026-09-09), matching Madden 27's
    # starting staffs (JR, 2026-11-06). start_season = first season in this role with this team.
    # (team_abbr, coach_name, slant, offensive_scheme, defensive_scheme, pass_lean, fourth_down, blitz_rate, man_coverage, start_season)
    ("KC", "Andy Reid", "OFFENSIVE", "West Coast", "4-3", "High", "Medium", "Medium", "Medium", 2013),
    ("SF", "Kyle Shanahan", "OFFENSIVE", "Wide Zone West Coast", "4-3", "High", "High", "Low", "Low", 2017),
    ("LAR", "Sean McVay", "OFFENSIVE", "Wide Zone West Coast", "3-4", "High", "Medium", "Low", "Low", 2017),
    ("DET", "Dan Campbell", "CEO", "West Coast", "4-3", "High", "High", "Medium", "High", 2021),
    ("BAL", "Jesse Minter", "DEFENSIVE", "West Coast", "3-4", "Medium", "High", "Low", "Low", 2026),
    ("GB", "Matt LaFleur", "OFFENSIVE", "Wide Zone West Coast", "3-4", "Medium", "Medium", "Medium", "Medium", 2019),
    ("HOU", "DeMeco Ryans", "DEFENSIVE", "Wide Zone West Coast", "4-3", "Medium", "Low", "Low", "Medium", 2023),
    ("IND", "Shane Steichen", "OFFENSIVE", "Spread", "4-3", "Medium", "High", "Low", "Medium", 2023),
    ("MIN", "Kevin O'Connell", "OFFENSIVE", "Wide Zone West Coast", "3-4", "Medium", "Low", "High", "Low", 2022),
    ("CAR", "Dave Canales", "OFFENSIVE", "Wide Zone West Coast", "3-4", "Medium", "Medium", "Medium", "Medium", 2024),
    ("LAC", "Jim Harbaugh", "CEO", "Wide Zone West Coast", "3-4", "Low", "Low", "Low", "Low", 2024),
    ("PIT", "Mike McCarthy", "OFFENSIVE", "West Coast", "3-4", "High", "High", "Medium", "Medium", 2026),
    ("PHI", "Nick Sirianni", "OFFENSIVE", "Spread", "3-4", "Medium", "High", "Medium", "Medium", 2021),
    ("CIN", "Zac Taylor", "OFFENSIVE", "Spread", "4-3", "High", "Medium", "Low", "High", 2019),
    ("BUF", "Joe Brady", "OFFENSIVE", "Spread", "3-4", "High", "Medium", "Medium", "Medium", 2026),
    ("DAL", "Brian Schottenheimer", "OFFENSIVE", "West Coast", "3-4", "High", "Medium", "Low", "Low", 2025),
    ("MIA", "Jeff Hafley", "DEFENSIVE", "Wide Zone West Coast", "4-3", "Medium", "Medium", "Low", "Low", 2026),
    ("TB", "Todd Bowles", "DEFENSIVE", "Wide Zone West Coast", "3-4", "Low", "Medium", "High", "Medium", 2022),
    ("SEA", "Mike Macdonald", "DEFENSIVE", "Wide Zone West Coast", "3-4", "Low", "Low", "Low", "Medium", 2024),
    ("WAS", "Dan Quinn", "DEFENSIVE", "Spread", "4-3", "Medium", "Medium", "Medium", "Medium", 2024),
    ("CLE", "Todd Monken", "OFFENSIVE", "Spread", "4-3", "Medium", "High", "Medium", "Medium", 2026),
    ("DEN", "Sean Payton", "OFFENSIVE", "West Coast", "3-4", "Medium", "Medium", "Medium", "Medium", 2023),
    ("JAX", "Liam Coen", "OFFENSIVE", "Wide Zone West Coast", "4-3", "Medium", "Medium", "Medium", "Medium", 2025),
    ("NYJ", "Aaron Glenn", "DEFENSIVE", "West Coast", "4-3", "Medium", "Medium", "High", "High", 2025),
    ("CHI", "Ben Johnson", "OFFENSIVE", "West Coast", "4-3", "High", "High", "High", "Medium", 2025),
    ("AZ", "Mike LaFleur", "OFFENSIVE", "Wide Zone West Coast", "3-4", "Medium", "Medium", "Medium", "Medium", 2026),
    ("ATL", "Kevin Stefanski", "OFFENSIVE", "Wide Zone West Coast", "4-3", "Medium", "Medium", "Medium", "Medium", 2026),
    ("NE", "Mike Vrabel", "DEFENSIVE", "Erhardt-Perkins", "3-4", "Medium", "High", "Medium", "High", 2025),
    ("NO", "Kellen Moore", "OFFENSIVE", "Spread", "3-4", "High", "High", "Low", "Low", 2025),
    ("NYG", "John Harbaugh", "CEO", "West Coast", "3-4", "Medium", "High", "Medium", "Medium", 2026),
    ("LV", "Klint Kubiak", "OFFENSIVE", "Wide Zone West Coast", "4-3", "Medium", "Medium", "Low", "Low", 2026),
    ("TEN", "Robert Saleh", "DEFENSIVE", "Erhardt-Perkins", "4-3", "Low", "Medium", "Low", "Medium", 2026),
]

COORDINATORS_DATA = [
    # Exactly one OC and one DC per team, as titled on the eve of the 2026 season. Where the head coach calls
    # plays the titled coordinator is still listed; TB has no titled DC (Bowles calls the defense), so its
    # senior defensive coordinator-titled assistant, run game coordinator Larry Foote, fills the seat.
    # (team_abbr, role, coordinator_name, side, scheme, playbook, pass_lean, blitz_rate, man_coverage, start_season)
    ("KC", "OC", "Eric Bieniemy", "OFFENSIVE", "West Coast", "Kansas City Offense", "High", "Medium", "Medium", 2026),
    ("KC", "DC", "Steve Spagnuolo", "DEFENSIVE", "4-3", "Kansas City Defense", "Medium", "High", "High", 2019),
    ("SF", "OC", "Klay Kubiak", "OFFENSIVE", "Wide Zone West Coast", "San Francisco Offense", "High", "Low", "Low", 2025),
    ("SF", "DC", "Raheem Morris", "DEFENSIVE", "4-3", "San Francisco Defense", "Medium", "Low", "Low", 2026),
    ("DET", "OC", "Drew Petzing", "OFFENSIVE", "West Coast", "Detroit Offense", "High", "Medium", "High", 2026),
    ("DET", "DC", "Kelvin Sheppard", "DEFENSIVE", "4-3", "Detroit Defense", "Medium", "High", "High", 2025),
    ("BAL", "OC", "Declan Doyle", "OFFENSIVE", "West Coast", "Baltimore Offense", "Medium", "Medium", "Medium", 2026),
    ("BAL", "DC", "Anthony Weaver", "DEFENSIVE", "3-4", "Baltimore Defense", "Medium", "Medium", "Medium", 2026),
    ("IND", "OC", "Jim Bob Cooter", "OFFENSIVE", "Spread", "Colts Offense", "Medium", "Low", "Medium", 2023),
    ("IND", "DC", "Lou Anarumo", "DEFENSIVE", "4-3", "Indianapolis Defense", "Medium", "Medium", "Medium", 2025),
    ("CAR", "OC", "Brad Idzik", "OFFENSIVE", "Wide Zone West Coast", "Carolina Offense", "Medium", "Medium", "Medium", 2024),
    ("CAR", "DC", "Ejiro Evero", "DEFENSIVE", "3-4", "Carolina Defense", "Medium", "Medium", "Low", 2023),
    ("HOU", "OC", "Nick Caley", "OFFENSIVE", "Wide Zone West Coast", "Houston Offense", "Medium", "Low", "Medium", 2025),
    ("HOU", "DC", "Matt Burke", "DEFENSIVE", "4-3", "Houston Defense", "Low", "Low", "Medium", 2023),
    ("GB", "OC", "Adam Stenavich", "OFFENSIVE", "Wide Zone West Coast", "Packers Offense", "Medium", "Medium", "Medium", 2022),
    ("GB", "DC", "Jonathan Gannon", "DEFENSIVE", "3-4", "Green Bay Defense", "Medium", "Medium", "Low", 2026),
    ("LAR", "OC", "Nathan Scheelhaase", "OFFENSIVE", "Wide Zone West Coast", "LA Rams Offense", "High", "Low", "Low", 2026),
    ("LAR", "DC", "Chris Shula", "DEFENSIVE", "3-4", "LA Rams Defense", "Medium", "Low", "Low", 2024),
    ("MIN", "OC", "Wes Phillips", "OFFENSIVE", "Wide Zone West Coast", "Minnesota Offense", "High", "Low", "Low", 2022),
    ("MIN", "DC", "Brian Flores", "DEFENSIVE", "3-4", "Minnesota Defense", "Medium", "High", "High", 2023),
    ("CIN", "OC", "Dan Pitcher", "OFFENSIVE", "Spread", "Cincinnati Offense", "High", "Low", "High", 2024),
    ("CIN", "DC", "Al Golden", "DEFENSIVE", "4-3", "Cincinnati Defense", "Medium", "Medium", "Medium", 2025),
    ("PHI", "OC", "Sean Mannion", "OFFENSIVE", "Wide Zone West Coast", "Philadelphia Offense", "Medium", "Medium", "Low", 2026),
    ("PHI", "DC", "Vic Fangio", "DEFENSIVE", "3-4", "Philadelphia Defense", "Low", "Low", "Low", 2024),
    ("BUF", "OC", "Pete Carmichael Jr.", "OFFENSIVE", "Spread", "Buffalo Offense", "Medium", "Medium", "High", 2026),
    ("BUF", "DC", "Jim Leonhard", "DEFENSIVE", "3-4", "Buffalo Defense", "Medium", "High", "Medium", 2026),
    ("DAL", "OC", "Klayton Adams", "OFFENSIVE", "West Coast", "Dallas Offense", "High", "Low", "High", 2025),
    ("DAL", "DC", "Christian Parker", "DEFENSIVE", "3-4", "Dallas Defense", "Medium", "Low", "Low", 2026),
    ("PIT", "OC", "Brian Angelichio", "OFFENSIVE", "West Coast", "Pittsburgh Offense", "Medium", "Low", "Low", 2026),
    ("PIT", "DC", "Patrick Graham", "DEFENSIVE", "3-4", "Pittsburgh Defense", "Medium", "Medium", "Medium", 2026),
    ("MIA", "OC", "Bobby Slowik", "OFFENSIVE", "Wide Zone West Coast", "Dolphins Offense", "Medium", "Low", "Medium", 2026),
    ("MIA", "DC", "Sean Duggan", "DEFENSIVE", "4-3", "Miami Defense", "Medium", "Low", "Low", 2026),
    ("TB", "OC", "Zac Robinson", "OFFENSIVE", "Wide Zone West Coast", "Tampa Bay Offense", "High", "Low", "Low", 2026),
    ("TB", "DC", "Larry Foote", "DEFENSIVE", "3-4", "Tampa Bay Defense", "Low", "High", "Medium", 2025),
    ("LAC", "OC", "Mike McDaniel", "OFFENSIVE", "Wide Zone West Coast", "LA Chargers Offense", "Medium", "Low", "Low", 2026),
    ("LAC", "DC", "Chris O'Leary", "DEFENSIVE", "3-4", "LA Chargers Defense", "Medium", "Low", "Low", 2026),
    ("SEA", "OC", "Brian Fleury", "OFFENSIVE", "Wide Zone West Coast", "Seattle Offense", "Medium", "Low", "Low", 2026),
    ("SEA", "DC", "Aden Durde", "DEFENSIVE", "3-4", "Seattle Defense", "Low", "Low", "Medium", 2024),
    ("WAS", "OC", "David Blough", "OFFENSIVE", "Spread", "Washington Offense", "High", "Low", "Low", 2026),
    ("WAS", "DC", "Daronte Jones", "DEFENSIVE", "4-3", "Washington Defense", "Medium", "Medium", "Medium", 2026),
    ("CLE", "OC", "Travis Switzer", "OFFENSIVE", "Spread", "Cleveland Offense", "Medium", "Low", "Medium", 2026),
    ("CLE", "DC", "Mike Rutenberg", "DEFENSIVE", "4-3", "Cleveland Defense", "Medium", "Medium", "Medium", 2026),
    ("DEN", "OC", "Davis Webb", "OFFENSIVE", "West Coast", "Denver Offense", "Medium", "Low", "Medium", 2026),
    ("DEN", "DC", "Vance Joseph", "DEFENSIVE", "3-4", "Denver Defense", "Medium", "High", "High", 2023),
    ("JAX", "OC", "Grant Udinski", "OFFENSIVE", "Wide Zone West Coast", "Jacksonville Offense", "High", "Low", "Low", 2025),
    ("JAX", "DC", "Anthony Campanile", "DEFENSIVE", "4-3", "Jacksonville Defense", "Medium", "Medium", "Medium", 2025),
    ("NYJ", "OC", "Frank Reich", "OFFENSIVE", "West Coast", "Jets Offense", "Medium", "Low", "Low", 2026),
    ("NYJ", "DC", "Brian Duker", "DEFENSIVE", "4-3", "NY Jets Defense", "Medium", "High", "High", 2026),
    ("CHI", "OC", "Press Taylor", "OFFENSIVE", "West Coast", "Chicago Offense", "High", "Low", "Low", 2026),
    ("CHI", "DC", "Dennis Allen", "DEFENSIVE", "4-3", "Chicago Defense", "Medium", "High", "Medium", 2025),
    ("AZ", "OC", "Nathaniel Hackett", "OFFENSIVE", "Wide Zone West Coast", "Arizona Offense", "Medium", "Low", "Low", 2026),
    ("AZ", "DC", "Nick Rallis", "DEFENSIVE", "3-4", "Arizona Defense", "Medium", "Medium", "Medium", 2023),
    ("ATL", "OC", "Tommy Rees", "OFFENSIVE", "Wide Zone West Coast", "Atlanta Offense", "Medium", "Low", "Low", 2026),
    ("ATL", "DC", "Jeff Ulbrich", "DEFENSIVE", "4-3", "Atlanta Defense", "Medium", "Medium", "Medium", 2025),
    ("NE", "OC", "Josh McDaniels", "OFFENSIVE", "Erhardt-Perkins", "New England Offense", "Medium", "Low", "Low", 2025),
    ("NE", "DC", "Zak Kuhr", "DEFENSIVE", "3-4", "New England Defense", "Medium", "Medium", "High", 2026),
    ("NO", "OC", "Doug Nussmeier", "OFFENSIVE", "Spread", "New Orleans Offense", "High", "Low", "Low", 2025),
    ("NO", "DC", "Brandon Staley", "DEFENSIVE", "3-4", "New Orleans Defense", "Medium", "Low", "Low", 2025),
    ("NYG", "OC", "Matt Nagy", "OFFENSIVE", "West Coast", "NY Giants Offense", "High", "Medium", "Medium", 2026),
    ("NYG", "DC", "Dennard Wilson", "DEFENSIVE", "3-4", "NY Giants Defense", "Medium", "High", "High", 2026),
    ("LV", "OC", "Andrew Janocko", "OFFENSIVE", "Wide Zone West Coast", "Las Vegas Offense", "Medium", "Low", "Low", 2026),
    ("LV", "DC", "Rob Leonard", "DEFENSIVE", "4-3", "Las Vegas Defense", "Medium", "Low", "Low", 2026),
    ("TEN", "OC", "Brian Daboll", "OFFENSIVE", "Spread", "Titans Offense", "Medium", "Low", "Medium", 2026),
    ("TEN", "DC", "Gus Bradley", "DEFENSIVE", "4-3", "Titans Defense", "Low", "Low", "Low", 2026),
]


def execute_sql_file(cur, filepath: Path):
    """Executes an idempotent SQL file using the cursor."""
    if not filepath.exists():
        raise FileNotFoundError(f"SQL file not found: {filepath}")
    sql = filepath.read_text(encoding="utf-8")
    cur.execute(sql)


def seed_teams(cur) -> dict[str, int]:
    """Upserts the 32 NFL teams and returns {abbreviation: id}."""
    team_ids = {}
    for name, city, abbr, color, stadium, conf, div in TEAMS_DATA:
        cur.execute("""
            INSERT INTO teams (name, city, abbreviation, team_color, stadium_name, conference, division)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                city = EXCLUDED.city,
                abbreviation = EXCLUDED.abbreviation,
                team_color = EXCLUDED.team_color,
                stadium_name = EXCLUDED.stadium_name,
                conference = EXCLUDED.conference,
                division = EXCLUDED.division
            RETURNING id, abbreviation;
        """, (name, city, abbr, color, stadium, conf, div))
        row = cur.fetchone()
        team_ids[row[1]] = row[0]
    return team_ids


def seed_league_state(cur, season: int, week: int):
    """Initializes or updates the single league_state row."""
    cur.execute("""
        INSERT INTO league_state (id, current_season, current_week)
        VALUES (1, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            current_season = EXCLUDED.current_season,
            current_week = EXCLUDED.current_week;
    """, (season, week))


def seed_gm_team_settings(cur, team_ids: dict[str, int], user_teams: set[str]):
    """Seeds gm_team_settings for all 32 teams."""
    for abbr, tid in team_ids.items():
        is_user = abbr in user_teams
        cur.execute("""
            INSERT INTO gm_team_settings (team_id, user_controlled, suggestions_on)
            VALUES (%s, %s, TRUE)
            ON CONFLICT (team_id) DO UPDATE SET
                user_controlled = EXCLUDED.user_controlled,
                suggestions_on = EXCLUDED.suggestions_on;
        """, (tid, is_user))


def seed_records(cur):
    """Seeds canonical NFL historical records through 2025."""
    cur.execute("DROP VIEW IF EXISTS league_records, league_top10, franchise_records_current CASCADE;")
    migration_files = [
        REPO_ROOT / "db" / "migrations" / "2026_10_26_nfl_records.sql",
        REPO_ROOT / "db" / "migrations" / "2026_10_28_league_record_book.sql",
        REPO_ROOT / "db" / "migrations" / "2026_11_12_defense_kicking_records.sql",
        REPO_ROOT / "db" / "migrations" / "2026_11_15_defense_kicking_history.sql",
        REPO_ROOT / "db" / "migrations" / "2026_11_23_postseason_records.sql",
    ]
    for mf in migration_files:
        execute_sql_file(cur, mf)
    # 2026_10_28 and 2026_11_15 also load the old Mu universe's 2026-2035 record book (source 'BOOK'); a fresh
    # franchise starts from real NFL history only. The BOOK categories stay, empty until this league sets them.
    cur.execute("DELETE FROM franchise_records WHERE source <> 'NFL';")
    cur.execute("DELETE FROM league_record_entries WHERE source <> 'NFL';")


def seed_agent_accounts(cur, team_ids: dict[str, int], path: Path = AGENT_ACCOUNTS_PATH) -> int:
    """Seeds the newsroom's writer and social accounts (agent_memories identity rows) from agent_accounts.json."""
    with open(path) as f:
        accounts = json.load(f)["accounts"]
    for a in accounts:
        cur.execute("""
            INSERT INTO agent_memories (agent_id, archetype, assigned_team_id, persistent_memory, handle,
                                        display_name, bio, avatar_color, person_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (agent_id) DO UPDATE SET
                archetype = EXCLUDED.archetype,
                assigned_team_id = EXCLUDED.assigned_team_id,
                persistent_memory = EXCLUDED.persistent_memory,
                handle = EXCLUDED.handle,
                display_name = EXCLUDED.display_name,
                bio = EXCLUDED.bio,
                avatar_color = EXCLUDED.avatar_color,
                person_key = EXCLUDED.person_key;
        """, (a["agent_id"], a["archetype"], team_ids.get(a["team"]) if a["team"] else None,
              a["persistent_memory"], a["handle"], a["display_name"], a["bio"], a["avatar_color"],
              a.get("person_key") or a["agent_id"]))
    return len(accounts)


def seed_coaches_and_coordinators(cur, team_ids: dict[str, int], season: int):
    """Seeds head coaches, tenures, coordinators, and coordinator tenures."""
    # 1. Head Coaches
    for abbr, name, slant, off_scheme, def_scheme, p_lean, fourth, blitz, man, start_yr in COACHES_DATA:
        tid = team_ids.get(abbr)
        if not tid:
            continue
        cur.execute("""
            INSERT INTO coaches (coach_name, slant, offensive_scheme, defensive_scheme,
                                 pass_lean, fourth_down_aggressiveness, blitz_rate, man_coverage)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (coach_name) DO UPDATE SET
                slant = EXCLUDED.slant,
                offensive_scheme = EXCLUDED.offensive_scheme,
                defensive_scheme = EXCLUDED.defensive_scheme,
                pass_lean = EXCLUDED.pass_lean,
                fourth_down_aggressiveness = EXCLUDED.fourth_down_aggressiveness,
                blitz_rate = EXCLUDED.blitz_rate,
                man_coverage = EXCLUDED.man_coverage
            RETURNING id;
        """, (name, slant, off_scheme, def_scheme, p_lean, fourth, blitz, man))
        cid = cur.fetchone()[0]

        start = min(start_yr, season)
        cur.execute("""
            INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season)
            VALUES (%s, %s, %s, NULL)
            ON CONFLICT (coach_id, team_id, start_season) DO NOTHING;
        """, (cid, tid, start))

    # 2. Coordinators
    for abbr, role, name, side, scheme, playbook, p_lean, blitz, man, start_yr in COORDINATORS_DATA:
        tid = team_ids.get(abbr)
        if not tid:
            continue
        cur.execute("""
            INSERT INTO coordinators (coordinator_name, side, scheme, playbook, pass_lean, blitz_rate, man_coverage)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, (name, side, scheme, playbook, p_lean, blitz, man))
        coord_id = cur.fetchone()[0]

        start = min(start_yr, season)
        cur.execute("""
            INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season)
            VALUES (%s, %s, %s, %s, NULL)
            ON CONFLICT (coordinator_id, team_id, role, start_season) DO NOTHING;
        """, (coord_id, tid, role, start))


def seed_napper_profile(cur):
    """Seeds baseline Napper fantasy user profile."""
    cur.execute("""
        INSERT INTO napper_user_profiles (handle, display_name, avatar_url, trophies)
        VALUES (%s, %s, %s, 0)
        ON CONFLICT (handle) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            avatar_url = EXCLUDED.avatar_url;
    """, (
        "jrereyi2000",
        "The Straw Hat Pirates",
        "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=256&h=256&q=80",
    ))


def run_clean_seed(
    dsn: str,
    season: int = 2024,
    week: int = 1,
    user_teams: list[str] = None,
    skip_records: bool = False,
    skip_coaches: bool = False,
    skip_gms: bool = False,
    skip_writers: bool = False,
    skip_napper: bool = False,
    dry_run: bool = False,
    log=print,
) -> dict:
    """Master seeding entrypoint."""
    user_team_set = {t.strip().upper() for t in (user_teams or [])}
    report = {}

    log(f"--- Running Project Mu Clean Database Seed (Season {season}, Week {week}) ---")
    if user_team_set:
        log(f"User controlled teams: {', '.join(sorted(user_team_set))}")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # 1. Verify / Apply Schema
            cur.execute("SELECT to_regclass('public.teams');")
            if cur.fetchone()[0] is None:
                log("Table 'teams' not found. Applying db/init_db.sql...")
                init_sql_path = REPO_ROOT / "db" / "init_db.sql"
                execute_sql_file(cur, init_sql_path)
                log("Applied db/init_db.sql successfully.")

            # 2. Seed 32 Teams
            log("1. Seeding 32 NFL franchises...")
            team_ids = seed_teams(cur)
            report["teams"] = len(team_ids)
            log(f"   Seeded {len(team_ids)} teams.")

            # 3. Seed league_state
            log(f"2. Setting league_state to Season {season}, Week {week}...")
            seed_league_state(cur, season, week)
            report["league_state"] = (season, week)

            # 4. Seed gm_team_settings
            log("3. Seeding gm_team_settings...")
            seed_gm_team_settings(cur, team_ids, user_team_set)
            report["gm_settings"] = len(team_ids)

            # 5. Seed Historical Records
            if not skip_records:
                log("4. Seeding NFL historical records (through 2025)...")
                seed_records(cur)
                cur.execute("SELECT count(*) FROM franchise_records WHERE source = 'NFL';")
                report["nfl_franchise_records"] = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM league_record_entries WHERE source = 'NFL';")
                report["nfl_top10_records"] = cur.fetchone()[0]
                log(f"   Seeded {report['nfl_franchise_records']} franchise records and {report['nfl_top10_records']} top-10 marks.")
            else:
                log("4. Skipping NFL historical records.")

            # 6. Seed Coaches & Coordinators
            if not skip_coaches:
                log("5. Seeding real NFL head coaches & coordinators...")
                seed_coaches_and_coordinators(cur, team_ids, season)
                cur.execute("SELECT count(*) FROM coach_tenures WHERE end_season IS NULL;")
                report["active_coaches"] = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM coordinator_tenures WHERE end_season IS NULL;")
                report["active_coordinators"] = cur.fetchone()[0]
                log(f"   Seeded {report['active_coaches']} active head coaches and {report['active_coordinators']} coordinators.")
            else:
                log("5. Skipping coaches and coordinators.")

            # 7. Seed GM Personas & Draft Picks
            if not skip_gms:
                log("6. Seeding GM personas and 3 years of draft picks...")
                from engines.gm_personas import seed as seed_gms
                gm_rep = seed_gms(cur, season, llm=None, log=lambda *args: None)
                report["gms_seeded"] = len(gm_rep["seeded"])
                report["draft_picks"] = gm_rep["picks"]
                log(f"   Seeded {report['gms_seeded']} GM personas and {report['draft_picks']} draft picks.")
            else:
                log("6. Skipping GM personas.")

            # 8. Seed Writer Personas
            if not skip_writers:
                log("7. Seeding newsroom writer personas...")
                from newsroom.personas import seed_writers_to_db
                writers_count = seed_writers_to_db(cur)
                report["writers_seeded"] = writers_count
                report["agent_accounts"] = seed_agent_accounts(cur, team_ids)
                from newsroom import relationships
                report["writer_relationships"] = relationships.seed_writer_relationships(cur, season)
                log(f"   Seeded {writers_count} writer personas, {report['agent_accounts']} writer/social accounts "
                    f"and {report['writer_relationships']} writer relationships.")
            else:
                log("7. Skipping writer personas.")

            # 9. Seed Napper Default Profile
            if not skip_napper:
                log("8. Seeding default Napper fantasy profile...")
                seed_napper_profile(cur)
                report["napper_profile"] = "jrereyi2000"
            else:
                log("8. Skipping Napper profile.")

            if dry_run:
                log("DRY RUN: Rolling back transaction (no changes persisted).")
                conn.rollback()
            else:
                conn.commit()
                log("SUCCESS: Clean seed committed to database.")

    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=2024, help="Starting franchise calendar season (default 2024)")
    parser.add_argument("--week", type=int, default=1, help="Starting week (default 1)")
    parser.add_argument("--user-team", type=str, default="", help="Comma-separated abbreviations of user-controlled teams (e.g. IND or IND,CAR)")
    parser.add_argument("--db", type=str, default=None, help="Postgres connection DSN (default from DATABASE_URL)")
    parser.add_argument("--skip-records", action="store_true", help="Skip seeding NFL historical records")
    parser.add_argument("--skip-coaches", action="store_true", help="Skip seeding head coaches & coordinators")
    parser.add_argument("--skip-gms", action="store_true", help="Skip seeding GM personas & draft capital")
    parser.add_argument("--skip-writers", action="store_true", help="Skip seeding media writers into DB")
    parser.add_argument("--skip-napper", action="store_true", help="Skip seeding Napper profile")
    parser.add_argument("--dry-run", action="store_true", help="Roll back transaction without writing")

    args = parser.parse_args()
    dsn = args.db or os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
    user_teams = [t.strip().upper() for t in args.user_team.split(",") if t.strip()]

    run_clean_seed(
        dsn=dsn,
        season=args.season,
        week=args.week,
        user_teams=user_teams,
        skip_records=args.skip_records,
        skip_coaches=args.skip_coaches,
        skip_gms=args.skip_gms,
        skip_writers=args.skip_writers,
        skip_napper=args.skip_napper,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
