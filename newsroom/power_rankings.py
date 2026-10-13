"""Weekly Power Rankings: parse the POWER_RANKINGS article into rows, validate them, and store them (PROJECT_SPEC §7.6).

The article's structure directive requires one header per team, e.g.
    ## 4. Denver Broncos (13-3) (Previous: 2 | Change: -2)
followed by a bold kicker and a 3–4 sentence blurb. Only the rank order, kicker and blurb are taken from the text.
Records and movement come from the database (team_seasons at publish time and the previous stored week), because the
writer's own numbers can be wrong. A ranking is stored only if it names all 32 teams exactly once, ranked 1–32.
"""

import re

TEAM_COUNT = 32

# "## 4. Denver Broncos (13-3) ...", tolerating ###, bold, "4)" and a missing record
HEADER_RE = re.compile(r"^\s*#{1,4}\s*(?:\*\*)?\s*(\d{1,2})\s*[.)]\s*(.+?)\s*$")
ANY_HEADER_RE = re.compile(r"^\s*#{1,4}\s")
RECORD_RE = re.compile(r"\(\s*\d{1,2}-\d{1,2}(?:-\d{1,2})?\s*\)")
RULE_RE = re.compile(r"^\s*(?:\*{3,}|-{3,}|={3,}|_{3,})\s*$", re.MULTILINE)
KICKER_RE = re.compile(r"^\s*\*\*(.+?)\*\*\s*(.*)$", re.DOTALL)


def load_team_index(cur) -> list:
    """[(team_id, nickname, city, abbreviation)] for name resolution."""
    cur.execute("SELECT id, name, city, abbreviation FROM teams ORDER BY id")
    return cur.fetchall()


def resolve_team(label: str, team_index: list):
    """Matches a header label ('Denver Broncos', 'Broncos', 'DEN') to a team id; None if zero or several match.
    NFL nicknames are unique, so the nickname decides; abbreviations are a fallback for bare labels."""
    name_part = re.sub(r"[*_`]", "", label.split("(")[0]).strip()
    words = re.findall(r"[a-z0-9]+", name_part.lower())
    hits = {tid for tid, nick, _city, _abbr in team_index if nick and nick.lower() in words}
    if len(hits) == 1:
        return hits.pop()
    if not hits and len(words) == 1:
        abbr_hits = {tid for tid, _n, _c, abbr in team_index if abbr and abbr.lower() == words[0]}
        if len(abbr_hits) == 1:
            return abbr_hits.pop()
    return None


def parse_power_rankings(text: str, team_index: list) -> tuple:
    """Returns (entries, errors). entries: [{rank, team_id, label, kicker, blurb}] in article order.
    errors is empty only when the list is a complete, valid 1–32 ranking."""
    lines = (text or "").splitlines()
    entries, current, body = [], None, []

    def close():
        if current is not None:
            current["kicker"], current["blurb"] = split_kicker("\n".join(body).strip())
            entries.append(current)

    for line in lines:
        m = HEADER_RE.match(line)
        team_id = resolve_team(m.group(2), team_index) if m else None
        if m and (team_id is not None or RECORD_RE.search(m.group(2))):
            # A numbered header is a team entry if it names a team or carries a W-L record (an unmatched team);
            # otherwise it's a numbered section header like "## 2. The 32-Team Enumeration".
            close()
            current = {"rank": int(m.group(1)), "label": m.group(2).strip("* "), "team_id": team_id}
            body = []
        elif ANY_HEADER_RE.match(line):
            close()               # a non-numbered header (e.g. "## The Walk-Off") ends the last team's blurb
            current, body = None, []
        elif current is not None:
            body.append(line)
    close()

    errors = []
    unresolved = [e["label"] for e in entries if e["team_id"] is None]
    if unresolved:
        errors.append("Could not match these headers to exactly one NFL team: " + "; ".join(unresolved))
    ranks = [e["rank"] for e in entries]
    missing_ranks = sorted(set(range(1, TEAM_COUNT + 1)) - set(ranks))
    dup_ranks = sorted({r for r in ranks if ranks.count(r) > 1})
    if missing_ranks:
        errors.append(f"Missing ranks: {missing_ranks}")
    if dup_ranks:
        errors.append(f"Ranks used more than once: {dup_ranks}")
    if any(r < 1 or r > TEAM_COUNT for r in ranks):
        errors.append(f"Ranks must be 1-{TEAM_COUNT}")
    ids = [e["team_id"] for e in entries if e["team_id"] is not None]
    names = {tid: f"{city} {nick}" for tid, nick, city, _a in team_index}
    dup_teams = sorted({names[t] for t in ids if ids.count(t) > 1})
    missing_teams = sorted(names[t] for t in set(names) - set(ids))
    if dup_teams:
        errors.append("Teams ranked more than once: " + ", ".join(dup_teams))
    if missing_teams:
        errors.append("Teams missing from the ranking: " + ", ".join(missing_teams))
    return entries, errors


def split_kicker(body: str) -> tuple:
    """'**Four-word recap: Living on borrowed time.** Three sentences...' → (kicker, blurb).
    The blurb is the first paragraph after the kicker; a horizontal rule or blank line ends it, so the closing
    walk-off after team #32 isn't swallowed."""
    body = RULE_RE.split(body, maxsplit=1)[0].strip()
    m = KICKER_RE.match(body)
    kicker, rest = (m.group(1).strip(), m.group(2)) if m else (None, body)
    paragraphs = [p for p in re.split(r"\n\s*\n", rest.strip()) if p.strip()]
    blurb = re.sub(r"\s+", " ", paragraphs[0]).strip() if paragraphs else ""
    return kicker, blurb


def previous_ranked_week(cur, season: int, week: int):
    cur.execute("SELECT MAX(week) FROM power_rankings WHERE season = %s AND week < %s", (season, week))
    row = cur.fetchone()
    return row[0] if row else None


def fetch_rankings(cur, season: int, week: int) -> dict:
    """{team_id: rank} for a stored week (empty if none)."""
    cur.execute("SELECT team_id, rank FROM power_rankings WHERE season = %s AND week = %s", (season, week))
    return dict(cur.fetchall())


def store_power_rankings(cur, season: int, week: int, article_id: int, persona: str, entries: list) -> int:
    """Replaces the stored ranking for (season, week). Records are snapshotted from team_seasons; movement is
    previous stored rank − new rank (positive = moved up). Returns rows written."""
    prev_week = previous_ranked_week(cur, season, week)
    previous = fetch_rankings(cur, season, prev_week) if prev_week else {}
    cur.execute("SELECT team_id, wins, losses FROM team_seasons WHERE season = %s", (season,))
    records = {t: f"{w or 0}-{l or 0}" for t, w, l in cur.fetchall()}

    cur.execute("DELETE FROM power_rankings WHERE season = %s AND week = %s", (season, week))
    rows = []
    for e in sorted(entries, key=lambda e: e["rank"]):
        prev = previous.get(e["team_id"])
        rows.append((season, week, e["rank"], e["team_id"], prev, (prev - e["rank"]) if prev else None,
                     records.get(e["team_id"]), e["kicker"], e["blurb"], article_id, persona))
    cur.executemany("""
        INSERT INTO power_rankings (season, week, rank, team_id, previous_rank, movement, record, kicker, blurb,
                                    article_id, persona)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, rows)
    return len(rows)


def previous_rankings_directive(cur, season: int, week: int) -> str:
    """Prompt block giving the writer last stored week's order so movement indicators are real."""
    prev_week = previous_ranked_week(cur, season, week)
    header_rule = ("Use exactly one numbered ## header per team, formatted: ## [Rank]. [City] [Team] ([W-L]) "
                   "(Previous: [last rank] | Change: [+N, -N or --]). Rank all 32 teams exactly once.")
    if not prev_week:
        return f"""
POWER RANKINGS CONTINUITY (MANDATORY):
- No earlier Power Rankings have been published this season. Write "(Previous: — | New)" for every team; do not invent prior ranks.
- {header_rule}
"""
    cur.execute("""
        SELECT pr.rank, t.city || ' ' || t.name
        FROM power_rankings pr JOIN teams t ON t.id = pr.team_id
        WHERE pr.season = %s AND pr.week = %s ORDER BY pr.rank
    """, (season, prev_week))
    order = "\n".join(f"  {rank}. {name}" for rank, name in cur.fetchall())
    return f"""
POWER RANKINGS CONTINUITY (MANDATORY):
- Last published Power Rankings (Week {prev_week}). Your movement indicators must be computed from this list; do not invent prior ranks:
{order}
- {header_rule}
"""


def fix_prompt(errors: list) -> str:
    return ("Your Power Rankings did not pass validation, so it can't be published:\n- " + "\n- ".join(errors) +
            "\nRewrite the COMPLETE article (headline, excerpt, all 32 teams, walk-off) fixing these problems. Rank all "
            "32 teams exactly once, ranks 1 to 32, each with a header formatted ## [Rank]. [City] [Team] ([W-L]). "
            "Do not call any more tools.")
