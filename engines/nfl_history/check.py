"""Record book cross-check, report only (docs/done/REAL_NFL_HISTORY_PLAN.md §2.10; PROJECT_SPEC §2.9).

The record book (franchise_records / league_record_entries, source NFL: Pro-Football-Reference back to 1920) stays
the authority and never reads nfl_*. record_check(cur) compares every NFL row of 1999-2025 in a category nflverse can
compute (CHECK_CATEGORIES) with nfl_player_seasons (holder by name that season) or nfl_team_seasons, and
write_report() writes verify_output/nfl_history/record_check_<db>.md.

- compare(book_rows, player_values, team_values) -> [Mismatch] (pure)
"""
import collections
import dataclasses
import os

from engines.nfl_history.link import REPORT_DIR, normalize_name

FIRST, LAST = 1999, 2025
PLAYER_CATEGORIES = {   # record category -> nfl_player_seasons expression (regular season)
    "season_pass_yards": "pass_yards", "season_pass_tds": "pass_tds", "season_rush_yards": "rush_yards",
    "season_rush_tds": "rush_tds", "season_receptions": "receptions", "season_rec_yards": "rec_yards",
    "season_rec_tds": "rec_tds", "season_scrimmage_tds": "COALESCE(rush_tds, 0) + COALESCE(rec_tds, 0)",
    "season_sacks": "def_sacks", "season_def_interceptions": "def_ints", "season_fgm": "kick_fgm",
}
TEAM_CATEGORIES = {"season_team_wins": "wins", "season_team_points": "points_for",
                   "season_team_fewest_points_allowed": "points_against"}
CHECK_CATEGORIES = tuple(PLAYER_CATEGORIES) + tuple(TEAM_CATEGORIES)
TOLERANCE = {"season_sacks": 0.5}


@dataclasses.dataclass
class Mismatch:
    category: str
    season: int
    holder: str
    team_id: int | None
    book: float
    nfl: float | None
    status: str            # MISMATCH | NOT_FOUND


def compare(book_rows, player_values, team_values):
    """book_rows: [{category, value, holder, season, team_id}]; player_values: {(season, normalized name): {category:
    value}}; team_values: {(season, team_id): {category: value}}. Rows outside 1999-2025 or other categories skip."""
    out, seen = [], set()
    for r in book_rows:
        cat, season = r["category"], r["season"]
        if cat not in CHECK_CATEGORIES or not FIRST <= season <= LAST:
            continue
        key = (cat, season, r["holder"], r.get("team_id"))
        if key in seen:
            continue
        seen.add(key)
        if cat in TEAM_CATEGORIES:
            vals = team_values.get((season, r.get("team_id")))
        else:
            vals = player_values.get((season, normalize_name(r["holder"])))
        nfl = None if vals is None else vals.get(cat)
        book = float(r["value"])
        if nfl is None:
            out.append(Mismatch(cat, season, r["holder"], r.get("team_id"), book, None, "NOT_FOUND"))
        elif abs(float(nfl) - book) > TOLERANCE.get(cat, 0):
            out.append(Mismatch(cat, season, r["holder"], r.get("team_id"), book, float(nfl), "MISMATCH"))
    return out


def _fetch(cur):
    names = [d[0] for d in cur.description]
    return [r if isinstance(r, dict) else dict(zip(names, r)) for r in cur.fetchall()]


def record_check(cur):
    """(mismatches, rows checked) for this database's NFL record-book rows of 1999-2025."""
    cats = list(CHECK_CATEGORIES)
    book = []
    for table in ("league_record_entries", "franchise_records"):
        cur.execute(f"""SELECT category, value, holder, season, team_id FROM {table}
                        WHERE source = 'NFL' AND season BETWEEN %s AND %s AND category = ANY(%s)""",
                    (FIRST, LAST, cats))
        book += _fetch(cur)
    exprs = ", ".join(f"SUM({e}) AS {c}" for c, e in PLAYER_CATEGORIES.items())
    cur.execute(f"""SELECT p.display_name, s.season, {exprs}
                    FROM nfl_player_seasons s JOIN nfl_players p ON p.gsis_id = s.gsis_id
                    WHERE s.season_type = 'REG' GROUP BY p.display_name, s.season""")
    players = collections.defaultdict(dict)
    for r in _fetch(cur):
        k = (r["season"], normalize_name(r["display_name"]))
        for c in PLAYER_CATEGORIES:
            if r[c] is not None:
                # two people sharing a name in one season: keep the larger value (the record holder's)
                players[k][c] = max(float(r[c]), players[k].get(c, float("-inf")))
    cur.execute("SELECT season, team_id, wins, points_for, points_against FROM nfl_team_seasons")
    teams = {(r["season"], r["team_id"]): {c: r[e] for c, e in TEAM_CATEGORIES.items()} for r in _fetch(cur)}
    return compare(book, players, teams), len(book)


def write_report(mismatches, checked, db_label, report_dir=REPORT_DIR):
    os.makedirs(report_dir, exist_ok=True)
    path = os.path.join(report_dir, f"record_check_{db_label}.md")
    lines = [f"# Record book vs nflverse: {db_label}", "",
             f"NFL record-book rows checked (1999-2025, categories nflverse can compute): {checked}; "
             f"mismatches: {sum(1 for m in mismatches if m.status == 'MISMATCH')}; "
             f"holder not found: {sum(1 for m in mismatches if m.status == 'NOT_FOUND')}.", "",
             "The record book stays the authority; nothing is changed.", "",
             "| Status | Category | Season | Holder | Book | nflverse |", "|---|---|---|---|---|---|"]
    for m in sorted(mismatches, key=lambda m: (m.status, m.category, m.season, m.holder)):
        lines.append(f"| {m.status} | {m.category} | {m.season} | {m.holder} | {m.book:g} | "
                     f"{'' if m.nfl is None else format(m.nfl, 'g')} |")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path
