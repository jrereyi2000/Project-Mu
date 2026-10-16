"""Year-End All-Pro Roster: parse the OFFICIAL_AP_ALL_PRO_ROSTER article into rows,
validate them, and store them in the awards table (PROJECT_SPEC §7.6).

The article structure directive requires modern AP All-Pro First and Second Team rosters:
- First-Team Offense (13 slots: QB, RB, FB, 3 WRs, TE, AP, 5 OL)
- First-Team Defense & Special Teams (12 defense + 6 special teams = 18 slots)
- Second-Team Offense (13 slots)
- Second-Team Defense & Special Teams (18 slots)
Total: 62 selections (31 First-Team, 31 Second-Team).

Each player bullet is formatted:
- **[Slot]**: [Player Full Name], [Team] — [1-sentence rationale]
Resolution matches on Player Name + Team + Position compatibility to avoid same-team/shared-name collisions.
"""

import re
from collections import Counter, defaultdict

# Canonical slots and their required counts per team level (First Team / Second Team)
OFFENSE_QUOTAS = {
    "QB": 1,
    "RB": 1,
    "FB": 1,
    "WR": 3,
    "TE": 1,
    "AP": 1,  # All-Purpose / Flex
    "LT": 1,
    "LG": 1,
    "C": 1,
    "RG": 1,
    "RT": 1,
}

DEFENSE_QUOTAS = {
    "EDGE": 2,
    "DI": 2,
    "LB": 3,
    "CB": 2,
    "SCB": 1,  # Slot Cornerback / Nickel
    "S": 2,
}

SPECIAL_TEAMS_QUOTAS = {
    "K": 1,
    "P": 1,
    "KR": 1,
    "PR": 1,
    "ST": 1,
    "LS": 1,
}

TOTAL_TEAM_SLOTS = sum(OFFENSE_QUOTAS.values()) + sum(DEFENSE_QUOTAS.values()) + sum(SPECIAL_TEAMS_QUOTAS.values())  # 31
ALL_SLOT_QUOTAS = {**OFFENSE_QUOTAS, **DEFENSE_QUOTAS, **SPECIAL_TEAMS_QUOTAS}

# Slot alias normalization -> Canonical slot key
SLOT_ALIASES = {
    "QB": "QB", "QUARTERBACK": "QB",
    "RB": "RB", "RUNNING BACK": "RB", "HALFBACK": "RB", "HB": "RB",
    "FB": "FB", "FULLBACK": "FB",
    "WR": "WR", "WIDE RECEIVER": "WR",
    "TE": "TE", "TIGHT END": "TE",
    "AP": "AP", "ALL-PURPOSE": "AP", "ALL PURPOSE": "AP", "FLEX": "AP", "ALL-PURPOSE PLAYER": "AP",
    "LT": "LT", "LEFT TACKLE": "LT",
    "LG": "LG", "LEFT GUARD": "LG",
    "C": "C", "CENTER": "C",
    "RG": "RG", "RIGHT GUARD": "RG",
    "RT": "RT", "RIGHT TACKLE": "RT",
    "EDGE": "EDGE", "EDGE RUSHER": "EDGE", "DE": "EDGE", "DEFENSIVE END": "EDGE",
    "DI": "DI", "INTERIOR LINEMAN": "DI", "DEFENSIVE TACKLE": "DI", "DT": "DI", "IDL": "DI", "NT": "DI", "NOSE TACKLE": "DI",
    "LB": "LB", "LINEBACKER": "LB", "OFF-BALL LINEBACKER": "LB", "ILB": "LB", "OLB": "LB",
    "CB": "CB", "CORNERBACK": "CB",
    "SCB": "SCB", "SLOT CORNERBACK": "SCB", "SLOT CB": "SCB", "NICKEL": "SCB", "NB": "SCB", "SLOT": "SCB",
    "S": "S", "SAFETY": "S", "FS": "S", "SS": "S", "FREE SAFETY": "S", "STRONG SAFETY": "S",
    "K": "K", "KICKER": "K", "PK": "K", "PLACEKICKER": "K",
    "P": "P", "PUNTER": "P",
    "KR": "KR", "KICK RETURNER": "KR",
    "PR": "PR", "PUNT RETURNER": "PR",
    "ST": "ST", "SPECIAL TEAMER": "ST", "SPECIAL TEAMS": "ST",
    "LS": "LS", "LONG SNAPPER": "LS",
}

# Position compatibility per slot (Madden roster position codes)
SLOT_COMPATIBLE_POSITIONS = {
    "QB": {"QB"},
    "RB": {"HB", "RB"},
    "FB": {"FB", "HB", "RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "AP": {"HB", "RB", "WR", "TE"},
    "LT": {"LT", "OT", "T"},
    "LG": {"LG", "OG", "G"},
    "C": {"C", "OL"},
    "RG": {"RG", "OG", "G"},
    "RT": {"RT", "OT", "T"},
    "EDGE": {"LEDG", "REDG", "LE", "RE", "LOLB", "ROLB", "DE", "EDGE"},
    "DI": {"DT", "NT", "DL"},
    "LB": {"MIKE", "WILL", "SAM", "MLB", "LOLB", "ROLB", "LB"},
    "CB": {"CB", "DB"},
    "SCB": {"CB", "DB"},
    "S": {"FS", "SS", "S", "DB"},
    "K": {"K", "PK"},
    "P": {"P"},
    "KR": None,  # Any position on that team (frequently WR/CB/HB)
    "PR": None,  # Any position on that team
    "ST": None,  # Any position on that team
    "LS": {"LS", "C", "TE", "OL"},
}

# Section detection regexes
HEADER_RE = re.compile(r"^\s*#{1,4}\s*(.+?)\s*$")
BULLET_RE = re.compile(
    r"^\s*[-*•]\s*(?:\*\*)?([A-Za-z0-9 /-]+?)(?:\*\*)?\s*[:\-]\s*(.+?)\s*(?:[—–-]|:\s)\s*(.*)$"
)


def load_roster_index(cur, season: int) -> dict:
    """Loads active roster and team metadata for the given season to enable position-aware player matching."""
    cur.execute("SELECT id, name, city, abbreviation FROM teams ORDER BY id")
    team_index = cur.fetchall()

    cur.execute("""
        SELECT p.id, p.full_name, p.player_name, ps.team_id, ps.position,
               t.name, t.city, t.abbreviation
        FROM player_seasons ps
        JOIN players p ON p.id = ps.player_id
        JOIN teams t ON t.id = ps.team_id
        WHERE ps.season = %s
    """, (season,))
    roster_rows = cur.fetchall()

    players = []
    for pid, fname, pname, tid, pos, tname, city, abbr in roster_rows:
        players.append({
            "player_id": pid,
            "full_name": fname or pname or "",
            "player_name": pname or "",
            "team_id": tid,
            "position": pos or "",
            "team_name": tname or "",
            "city": city or "",
            "abbreviation": abbr or "",
        })

    return {
        "teams": team_index,
        "players": players,
    }


def resolve_team(label: str, team_index: list):
    """Matches a label ('New England Patriots', 'Patriots', 'NE') to a team_id."""
    if not label:
        return None
    name_part = re.sub(r"[*_`]", "", label.split("(")[0]).strip()
    words = re.findall(r"[a-z0-9]+", name_part.lower())
    hits = {tid for tid, nick, _city, _abbr in team_index if nick and nick.lower() in words}
    if len(hits) == 1:
        return hits.pop()
    if not hits and words:
        for w in words:
            abbr_hits = {tid for tid, _n, _c, abbr in team_index if abbr and abbr.lower() == w}
            if len(abbr_hits) == 1:
                return abbr_hits.pop()
        city_hits = {tid for tid, _n, city, _a in team_index if city and city.lower() in " ".join(words)}
        if len(city_hits) == 1:
            return city_hits.pop()
    return None


def resolve_player(raw_player_team: str, canonical_slot: str, roster_index: dict):
    """Resolves a raw string (e.g. 'Drake Maye, New England Patriots' or 'Drake Maye (NE)')
    to a unique player dict using Player Name + Team ID + Position compatibility."""
    raw = raw_player_team.strip()
    name_str, team_str = "", ""

    # Check for 'Player Name (Team)'
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

    team_id = resolve_team(team_str, roster_index["teams"]) if team_str else None

    # Find candidate players
    candidates = roster_index["players"]
    if team_id is not None:
        candidates = [p for p in candidates if p["team_id"] == team_id]

    norm_name = name_str.lower()
    # 1. Exact full_name match
    exact_matches = [p for p in candidates if p["full_name"].lower() == norm_name]

    # 2. If no exact match, try player_name match (e.g. 'D. Maye' or partial)
    if not exact_matches:
        exact_matches = [p for p in candidates if p["player_name"].lower() == norm_name]

    # 3. If still no match, match if candidate's full_name ends with the query or tokens match
    if not exact_matches:
        tokens = set(re.findall(r"[a-z]+", norm_name))
        if tokens:
            exact_matches = [
                p for p in candidates
                if tokens.issubset(set(re.findall(r"[a-z]+", p["full_name"].lower())))
            ]

    # Position compatibility check to resolve any ambiguity or verify validity
    allowed_positions = SLOT_COMPATIBLE_POSITIONS.get(canonical_slot)
    if allowed_positions is not None:
        pos_filtered = [p for p in exact_matches if p["position"].upper() in allowed_positions]
        if pos_filtered:
            exact_matches = pos_filtered

    if len(exact_matches) == 1:
        return exact_matches[0], name_str, team_str, None
    elif len(exact_matches) > 1:
        dups = ", ".join(f"{p['full_name']} ({p['position']} - {p['abbreviation']})" for p in exact_matches)
        return None, name_str, team_str, f"Ambiguous match for '{name_str}' (candidates: {dups})"
    else:
        ctx = f" (team: '{team_str}')" if team_str else ""
        return None, name_str, team_str, f"Could not find player '{name_str}'{ctx} eligible for slot {canonical_slot}"


def parse_all_pro(text: str, roster_index: dict) -> tuple:
    """Parses an OFFICIAL_AP_ALL_PRO_ROSTER article into 58 award entries.
    Returns (entries, errors).
    entries: [{
        "season_award_type": "First Team All-Pro" | "Second Team All-Pro",
        "slot": str,
        "player_id": int,
        "team_id": int,
        "player_name": str,
        "position": str,
        "blurb": str
    }]
    """
    lines = (text or "").splitlines()
    entries = []
    current_team_level = None  # "First Team All-Pro" or "Second Team All-Pro"

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue

        # Check for section header
        hm = HEADER_RE.match(line_clean)
        if hm:
            htext = hm.group(1).lower()
            if "first" in htext or "1st" in htext:
                current_team_level = "First Team All-Pro"
            elif "second" in htext or "2nd" in htext:
                current_team_level = "Second Team All-Pro"
            continue

        if not current_team_level:
            continue

        bm = BULLET_RE.match(line_clean)
        if bm:
            raw_slot = bm.group(1).strip().upper()
            raw_player_team = bm.group(2).strip()
            blurb = bm.group(3).strip()

            canonical_slot = SLOT_ALIASES.get(raw_slot)
            if not canonical_slot:
                # Check if raw_slot starts with a known alias (e.g. 'WR1' -> 'WR', 'EDGE1' -> 'EDGE')
                trimmed = re.sub(r"\d+$", "", raw_slot).strip()
                canonical_slot = SLOT_ALIASES.get(trimmed)

            if canonical_slot:
                player, parsed_name, parsed_team, err_msg = resolve_player(
                    raw_player_team, canonical_slot, roster_index
                )
                entry = {
                    "team_level": current_team_level,
                    "slot": canonical_slot,
                    "raw_slot": raw_slot,
                    "raw_player_team": raw_player_team,
                    "blurb": blurb,
                    "player": player,
                    "error": err_msg,
                }
                entries.append(entry)

    # Validation
    errors = []
    first_team = [e for e in entries if e["team_level"] == "First Team All-Pro"]
    second_team = [e for e in entries if e["team_level"] == "Second Team All-Pro"]

    for level_name, level_entries in [("First Team All-Pro", first_team), ("Second Team All-Pro", second_team)]:
        # Check player resolution errors
        res_errors = [e["error"] for e in level_entries if e["error"]]
        if res_errors:
            errors.extend(res_errors)

        # Check slot counts
        counts = Counter(e["slot"] for e in level_entries)
        for slot, req in ALL_SLOT_QUOTAS.items():
            actual = counts.get(slot, 0)
            if actual < req:
                errors.append(f"{level_name}: Missing {req - actual} slot(s) for {slot} (found {actual}, expected {req})")
            elif actual > req:
                errors.append(f"{level_name}: Too many selections for {slot} (found {actual}, expected {req})")

        # Duplicate check: a player cannot be selected twice for the exact same slot
        pid_slot_counts = Counter(
            (e["player"]["player_id"], e["slot"]) for e in level_entries if e["player"]
        )
        for (pid, slot), count in pid_slot_counts.items():
            if count > 1:
                dup_entry = next(e for e in level_entries if e["player"] and e["player"]["player_id"] == pid)
                errors.append(f"{level_name}: Player '{dup_entry['player']['full_name']}' selected multiple times for slot {slot}")

        # A player cannot hold multiple primary offensive slots (AP/Flex is permitted as a dual honor)
        off_primary_pids = [
            e["player"]["player_id"] for e in level_entries
            if e["player"] and e["slot"] in OFFENSE_QUOTAS and e["slot"] != "AP"
        ]
        off_dups = [pid for pid, c in Counter(off_primary_pids).items() if c > 1]
        for pid in off_dups:
            dup_entry = next(e for e in level_entries if e["player"] and e["player"]["player_id"] == pid)
            errors.append(f"{level_name}: Player '{dup_entry['player']['full_name']}' selected at multiple primary offensive positions")

        # A player cannot hold multiple primary defensive slots
        def_pids = [
            e["player"]["player_id"] for e in level_entries
            if e["player"] and e["slot"] in DEFENSE_QUOTAS
        ]
        def_dups = [pid for pid, c in Counter(def_pids).items() if c > 1]
        for pid in def_dups:
            dup_entry = next(e for e in level_entries if e["player"] and e["player"]["player_id"] == pid)
            errors.append(f"{level_name}: Player '{dup_entry['player']['full_name']}' selected at multiple primary defensive positions")

    # Format cleanly into stored records if valid
    validated_records = []
    if not errors:
        for e in entries:
            p = e["player"]
            validated_records.append({
                "award_type": e["team_level"],
                "slot": e["slot"],
                "player_id": p["player_id"],
                "team_id": p["team_id"],
                "player_name": p["full_name"],
                "position": e["slot"],  # Store the All-Pro slot as position (e.g. QB, EDGE, AP)
                "blurb": e["blurb"],
            })

    return validated_records, errors


def all_pro_fix_prompt(errors: list) -> str:
    """Prompt sent to the writer LLM to trigger a one-shot rewrite fixing All-Pro validation errors."""
    bullet_list = "\n".join(f"- {e}" for e in errors)
    return f"""Your All-Pro Roster article could not be verified by the automated parser due to the following discrepancies:

{bullet_list}

MANDATORY CORRECTION INSTRUCTIONS:
Please provide a complete, corrected version of the article. Ensure:
1. Every player entry is formatted as a single Markdown bullet:
   - **[Slot]**: [Player Full Name], [Team Full Name or Abbreviation] — [1-sentence rationale]
2. Offense has exactly: 1 QB, 1 RB, 1 FB, 3 WRs, 1 TE, 1 AP, 1 LT, 1 LG, 1 C, 1 RG, 1 RT (13 players).
3. Defense has exactly: 2 EDGE, 2 DI, 3 LB, 2 CB, 1 SCB, 2 S (12 players).
4. Special Teams has exactly: 1 K, 1 P, 1 KR, 1 PR, 1 ST, 1 LS (6 players).
5. Both First-Team and Second-Team must be present in full (31 selections each = 62 total).
6. Verify spelling of every player and team name against real database rosters. Do not repeat players within the same unit.

Output your full revised article now:"""


def store_all_pro_teams(cur, season: int, week: int, article_id: int, persona: str, entries: list) -> int:
    """Replaces stored All-Pro awards for this season in the awards table. Returns rows written."""
    cur.execute("""
        DELETE FROM awards 
        WHERE season = %s AND award_type IN ('First Team All-Pro', 'Second Team All-Pro')
    """, (season,))

    rows = []
    for e in entries:
        rows.append((
            season,
            e["award_type"],
            e["player_id"],
            e["team_id"],
            e["player_name"],
            e["position"]
        ))

    cur.executemany("""
        INSERT INTO awards (season, award_type, player_id, team_id, player_name, position)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, rows)

    return len(rows)
