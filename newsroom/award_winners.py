"""Year-End Award Winners: parse the Week 22 AWARDS_TRACKER (AWARDS_WINNERS override) article,
validate the six major awards, and store them in the awards table (PROJECT_SPEC §7.6c).

The article structure directive mandates the six major annual regular-season honors:
- MVP: Most Valuable Player
- OPOY: Offensive Player of the Year
- OROY: Offensive Rookie of the Year
- DPOY: Defensive Player of the Year
- DROY: Defensive Rookie of the Year
- COTY: Coach of the Year

Each award is announced in a level-2 markdown header:
- ## MVP: [Player], [Position], [Team]
- ## OPOY: [Player], [Position], [Team]
- ## OROY: [Player], [Position], [Team]
- ## DPOY: [Player], [Position], [Team]
- ## DROY: [Player], [Position], [Team]
- ## Coach of the Year: [Coach], [Team] (or ## COTY: [Coach], [Team])
"""

import re
from newsroom.all_pro import resolve_team, load_roster_index

AWARD_KEYS = ["MVP", "OPOY", "OROY", "DPOY", "DROY", "COTY"]

AWARD_ALIASES = {
    "MVP": "MVP",
    "MOST VALUABLE PLAYER": "MVP",
    "OPOY": "OPOY",
    "OFFENSIVE PLAYER OF THE YEAR": "OPOY",
    "OROY": "OROY",
    "OFFENSIVE ROOKIE OF THE YEAR": "OROY",
    "OFFENSIVE ROOKIE": "OROY",
    "DPOY": "DPOY",
    "DEFENSIVE PLAYER OF THE YEAR": "DPOY",
    "DROY": "DROY",
    "DEFENSIVE ROOKIE OF THE YEAR": "DROY",
    "DEFENSIVE ROOKIE": "DROY",
    "COTY": "COTY",
    "COACH OF THE YEAR": "COTY",
    "HEAD COACH OF THE YEAR": "COTY",
}


def load_coach_index(cur, season: int) -> dict:
    """Loads active coaches and teams for resolving Coach of the Year."""
    cur.execute("""
        SELECT c.id, c.coach_name, ct.team_id, t.name, t.city, t.abbreviation
        FROM coach_tenures ct
        JOIN coaches c ON c.id = ct.coach_id
        JOIN teams t ON t.id = ct.team_id
        WHERE ct.start_season <= %s AND (ct.end_season IS NULL OR ct.end_season >= %s)
    """, (season, season))
    coaches = []
    for cid, cname, tid, tname, city, abbr in cur.fetchall():
        coaches.append({
            "coach_id": cid,
            "coach_name": cname,
            "team_id": tid,
            "team_name": tname or "",
            "city": city or "",
            "abbreviation": abbr or "",
        })

    cur.execute("SELECT id, name, city, abbreviation FROM teams ORDER BY id")
    team_index = cur.fetchall()

    return {
        "coaches": coaches,
        "teams": team_index,
    }


def resolve_coach(raw_str: str, coach_index: dict) -> tuple:
    """Resolves coach name and team from string like 'Marcus Whitfield, Carolina Panthers'
    or 'Peter Gaffney (Denver Broncos)'.
    Returns (coach_dict, coach_name, team_str, error_message).
    """
    raw = raw_str.strip()
    name_str, team_str = "", ""

    m_paren = re.search(r"^(.*?)\s*\((.*?)\)\s*$", raw)
    if m_paren:
        name_str, team_str = m_paren.group(1).strip(), m_paren.group(2).strip()
    elif "," in raw:
        parts = raw.split(",", 1)
        name_str, team_str = parts[0].strip(), parts[1].strip()
    elif " - " in raw or " — " in raw:
        parts = re.split(r"\s+[—–-]\s+", raw, maxsplit=1)
        name_str, team_str = parts[0].strip(), parts[1].strip()
    else:
        name_str = raw

    name_str = re.sub(r"[*_`]", "", name_str).strip()
    team_str = re.sub(r"[*_`]", "", team_str).strip()

    team_id = resolve_team(team_str, coach_index["teams"]) if team_str else None

    candidates = coach_index["coaches"]
    if team_id is not None:
        candidates = [c for c in candidates if c["team_id"] == team_id]

    norm_name = name_str.lower()
    matches = [c for c in candidates if c["coach_name"].lower() == norm_name]

    if not matches:
        tokens = set(re.findall(r"[a-z]+", norm_name))
        if tokens:
            matches = [
                c for c in candidates
                if tokens.issubset(set(re.findall(r"[a-z]+", c["coach_name"].lower())))
            ]

    if len(matches) == 1:
        return matches[0], name_str, team_str, None
    elif len(matches) > 1:
        dups = ", ".join(f"{c['coach_name']} ({c['abbreviation']})" for c in matches)
        return None, name_str, team_str, f"Ambiguous coach match for '{name_str}' (candidates: {dups})"
    else:
        ctx = f" (team: '{team_str}')" if team_str else ""
        return None, name_str, team_str, f"Could not find coach '{name_str}'{ctx}"


def resolve_player_award(raw_str: str, award_type: str, roster_index: dict) -> tuple:
    """Resolves player winner string like 'George Crawford, QB, Carolina Panthers'
    or 'Cooper DeJean (CB, Houston Texans)'.
    Returns (player_dict, player_name, pos, team_str, error_message).
    """
    raw = raw_str.strip()
    name_str, pos_str, team_str = "", "", ""

    # Check for name (pos, team) or name (team)
    m_paren = re.search(r"^(.*?)\s*\((.*?)\)\s*$", raw)
    if m_paren:
        name_str = m_paren.group(1).strip()
        inside = m_paren.group(2).strip()
        if "," in inside:
            parts = inside.split(",", 1)
            pos_str, team_str = parts[0].strip(), parts[1].strip()
        else:
            team_str = inside
    elif "," in raw:
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) >= 3:
            name_str = parts[0]
            pos_str = parts[1]
            team_str = ", ".join(parts[2:])
        else:
            name_str = parts[0]
            team_str = parts[1]
    else:
        name_str = raw

    name_str = re.sub(r"[*_`]", "", name_str).strip()
    pos_str = re.sub(r"[*_`]", "", pos_str).strip()
    team_str = re.sub(r"[*_`]", "", team_str).strip()

    team_id = resolve_team(team_str, roster_index["teams"]) if team_str else None

    candidates = roster_index["players"]
    if team_id is not None:
        candidates = [p for p in candidates if p["team_id"] == team_id]

    norm_name = name_str.lower()
    matches = [p for p in candidates if p["full_name"].lower() == norm_name]

    if not matches:
        matches = [p for p in candidates if p["player_name"].lower() == norm_name]

    if not matches:
        tokens = set(re.findall(r"[a-z]+", norm_name))
        if tokens:
            matches = [
                p for p in candidates
                if tokens.issubset(set(re.findall(r"[a-z]+", p["full_name"].lower())))
            ]

    if pos_str:
        norm_pos = pos_str.upper()
        pos_filtered = [p for p in matches if p["position"].upper() == norm_pos]
        if pos_filtered:
            matches = pos_filtered

    if len(matches) == 1:
        resolved = matches[0]
        final_pos = pos_str or resolved["position"]
        return resolved, name_str, final_pos, team_str, None
    elif len(matches) > 1:
        dups = ", ".join(f"{p['full_name']} ({p['position']} - {p['abbreviation']})" for p in matches)
        return None, name_str, pos_str, team_str, f"Ambiguous player match for '{name_str}' (candidates: {dups})"
    else:
        ctx = f" (team: '{team_str}')" if team_str else ""
        return None, name_str, pos_str, team_str, f"Could not find player '{name_str}'{ctx} for {award_type}"


def parse_award_winners(text: str, roster_index: dict, coach_index: dict) -> tuple:
    """Parses a Week 22 AWARDS_TRACKER (AWARDS_WINNERS override) article into the 6 major awards.
    Returns (entries, errors).
    entries: [{
        "award_type": "MVP" | "OPOY" | "OROY" | "DPOY" | "DROY" | "COTY",
        "player_id": int | None,
        "coach_id": int | None,
        "player_name": str | None,
        "coach_name": str | None,
        "team_id": int,
        "position": str | None,
    }]
    """
    entries = []
    errors = []
    found_awards = {}

    # Header regex: ## [Award]: [Winner Info]
    header_regex = re.compile(
        r"^##\s*([^:\n]+)[:\-—]\s*(.+)$",
        re.MULTILINE
    )

    for match in header_regex.finditer(text):
        raw_award = match.group(1).strip()
        raw_val = match.group(2).strip()

        clean_award = re.sub(r"[*_`]", "", raw_award).strip().upper()
        canon_award = AWARD_ALIASES.get(clean_award)

        if not canon_award:
            continue

        if canon_award in found_awards:
            errors.append(f"Duplicate award header found for {canon_award}")
            continue

        found_awards[canon_award] = raw_val

    missing = [a for a in AWARD_KEYS if a not in found_awards]
    if missing:
        errors.append(f"Missing required award headers: {', '.join(missing)}")

    for award_key in AWARD_KEYS:
        if award_key not in found_awards:
            continue

        val_str = found_awards[award_key]

        if award_key == "COTY":
            coach, c_name, t_str, err = resolve_coach(val_str, coach_index)
            if err:
                errors.append(f"COTY error: {err}")
            else:
                entries.append({
                    "award_type": "COTY",
                    "coach_id": coach["coach_id"],
                    "coach_name": coach["coach_name"],
                    "player_id": None,
                    "player_name": None,
                    "team_id": coach["team_id"],
                    "position": None,
                })
        else:
            player, p_name, pos, t_str, err = resolve_player_award(val_str, award_key, roster_index)
            if err:
                errors.append(f"{award_key} error: {err}")
            else:
                entries.append({
                    "award_type": award_key,
                    "player_id": player["player_id"],
                    "player_name": player["full_name"],
                    "coach_id": None,
                    "coach_name": None,
                    "team_id": player["team_id"],
                    "position": pos or player["position"],
                })

    return entries, errors


def award_winners_fix_prompt(errors: list) -> str:
    """Generates an in-flight validation fix prompt for the active model chat session."""
    err_list = "\n".join(f"- {e}" for e in errors)
    return f"""Your awards announcement article could not be stored in the canonical awards table because of the following header formatting or player/coach resolution errors:

{err_list}

Please rewrite your article ensuring all 6 major regular-season award winners are formatted with exact Markdown level-2 (##) headers in this exact format:
- ## MVP: [Player Full Name], [Position], [Team Name]
- ## OPOY: [Player Full Name], [Position], [Team Name]
- ## OROY: [Player Full Name], [Position], [Team Name]
- ## DPOY: [Player Full Name], [Position], [Team Name]
- ## DROY: [Player Full Name], [Position], [Team Name]
- ## Coach of the Year: [Coach Full Name], [Team Name]

Do not omit any of the 6 awards, and verify player and coach names and teams match real active rosters and coaching staffs.
Remember: never refer explicitly to internal engine mechanics or formulas (e.g. 'COTY score', 'coach scores', 'Coach of the Year metric', 'historical bonus', or internal point metrics) in the Coach of the Year section; describe real-world on-field achievements, records, and team performance instead."""


def store_award_winners(cur, season: int, week: int, article_id: int, persona: str, entries: list) -> int:
    """Replaces stored single-winner awards for this season in the awards table. Returns rows written."""
    cur.execute("""
        DELETE FROM awards 
        WHERE season = %s AND award_type IN ('MVP', 'OPOY', 'OROY', 'DPOY', 'DROY', 'COTY')
    """, (season,))

    rows_written = 0
    for e in entries:
        if e["award_type"] == "COTY":
            cur.execute("""
                INSERT INTO awards (season, award_type, coach_id, coach_name, team_id)
                VALUES (%s, 'COTY', %s, %s, %s)
            """, (season, e["coach_id"], e["coach_name"], e["team_id"]))
        else:
            cur.execute("""
                INSERT INTO awards (season, award_type, player_id, player_name, team_id, position)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (season, e["award_type"], e["player_id"], e["player_name"], e["team_id"], e["position"]))
        rows_written += 1

    return rows_written
