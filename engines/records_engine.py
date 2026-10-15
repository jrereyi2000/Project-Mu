"""Records engine: checks a season of this league against the record book (PROJECT_SPEC §2.9).

The record book starts with real NFL history through 2025 (db/migrations/2026_10_26_nfl_records.sql: each franchise's
single-season record holders and the NFL's all-time top-10 lists), then the league's own record book for 2026–2035
(2026_10_28_league_record_book.sql, source 'BOOK'). This engine compares a league season with everything before it:

  - franchise records: a player's season (season_stats; defense and kicking from season_stat_lines, Madden's listed
    leaders only) or a team's season (regular-season games) that beats or ties
    its franchise's record;
  - NFL records and the all-time top 10: a season or a single game (player_stats) that would rank in the top 10.

`evaluate` only reads. `apply` stores the season's record rows with source 'LEAGUE' (it first deletes that season's
LEAGUE rows, so it can run after every week and the season's totals simply replace themselves). The record book is the
authority for its seasons: where a BOOK row exists for the same team, category and season, nothing is added, and the
report lists where the stats and the record book disagree. Old holders are never removed; the views pick the best value.

  python3 -m engines.records_engine --season 2035            # report only
  python3 -m engines.records_engine --season 2035 --apply    # report and store
"""
import argparse
import collections
import json

try:                     # imported lazily so the module (and its tests) load without a database driver
    import psycopg
except ImportError:      # pragma: no cover
    psycopg = None

REGULAR_SEASON_FINAL_WEEK = 18
GAMES_PER_SEASON = 17

# Season categories read from season_stats; single-game categories from player_stats.
PLAYER_STATS = {
    "pass_yards": "pass_yards", "pass_tds": "pass_tds", "rush_yards": "rush_yards", "rush_tds": "rush_tds",
    "receptions": "receptions", "rec_yards": "rec_yards", "rec_tds": "rec_tds",
    "scrimmage_tds": "COALESCE(rush_tds, 0) + COALESCE(rec_tds, 0)",
}
SEASON_PLAYER_CATEGORIES = {f"season_{k}": v for k, v in PLAYER_STATS.items()}
# Defense and kicking, from the Season Stats capture (season_stat_lines). Madden lists only each category's leaders
# (defenders by tackles), so a player without a listed line has unknown numbers: he gets no value, never a zero.
SEASON_DEFENSE_CATEGORIES = {
    "season_sacks": "def_sacks", "season_def_interceptions": "def_ints", "season_tackles": "def_tackles",
    "season_def_tds": "def_tds", "season_fgm": "kick_fgm", "season_fg_long": "kick_fg_long",
}
GAME_PLAYER_CATEGORIES = {
    "game_pass_yards": "ps.pass_yards", "game_pass_tds": "ps.pass_tds", "game_rush_yards": "ps.rush_yards",
    "game_rec_yards": "ps.rec_yards", "game_receptions": "ps.receptions",
    "game_scrimmage_tds": "COALESCE(ps.rush_tds, 0) + COALESCE(ps.rec_tds, 0)",
    "game_rush_tds": "ps.rush_tds", "game_rec_tds": "ps.rec_tds",
}
TEAM_CATEGORIES = ("season_team_wins", "season_team_points", "season_team_fewest_points_allowed")


def tidy_name(name):
    """Imported rosters sometimes store names in capitals; records use normal case."""
    if not name or not name.isupper():
        return name

    def word(w):
        if w.rstrip(".") in ("II", "III", "IV", "V"):
            return w
        return "-".join("'".join(x[:1].upper() + x[1:].lower() for x in part.split("'")) for part in w.split("-"))
    return " ".join(word(w) for w in name.split())


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# ------------------------------------------------------------------------------------------------- reading ---
def load_book(cur, season):
    """Categories, franchise records and top-10 entries from before this season (the baseline the season is measured
    against), plus the record book's rows for this season (BOOK), which are the authority for it."""
    cur.execute("SELECT category, label, scope, subject, better FROM record_categories ORDER BY sort_order")
    cats = {r[0]: {"label": r[1], "scope": r[2], "subject": r[3], "better": r[4]} for r in cur.fetchall()}
    cur.execute("SELECT team_id, category, value, holder, season, source FROM franchise_records WHERE season <= %s", (season,))
    franchise, book_rows = collections.defaultdict(list), []
    for team_id, cat, value, holder, s, source in cur.fetchall():
        if s < season:
            franchise[(team_id, cat)].append((float(value), holder, s))
        elif source == "BOOK":
            book_rows.append({"team_id": team_id, "category": cat, "value": float(value), "holder": holder})
    cur.execute("SELECT category, value, holder, season, team_id, source FROM league_record_entries WHERE season <= %s", (season,))
    entries, book_entries = collections.defaultdict(list), []
    for cat, value, holder, s, team_id, source in cur.fetchall():
        if s < season:
            entries[cat].append((float(value), holder, s))
        elif source == "BOOK":
            book_entries.append({"team_id": team_id, "category": cat, "value": float(value), "holder": holder})
    cur.execute("SELECT DISTINCT category FROM franchise_records WHERE source = 'BOOK'")
    book_categories = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT id, abbreviation, city, name FROM teams")
    teams = {r[0]: {"abbr": r[1], "name": f"{r[2]} {r[3]}"} for r in cur.fetchall()}
    return {"categories": cats, "franchise": franchise, "entries": entries, "teams": teams,
            "book_rows": book_rows, "book_entries": book_entries, "book_categories": book_categories}


def season_player_values(cur, season):
    """[(category, team_id, player_id, holder, value)] from season_stats."""
    cols = ", ".join(f"{expr} AS {cat}" for cat, expr in SEASON_PLAYER_CATEGORIES.items())
    cur.execute(f"""
        SELECT ss.team_id, ss.player_id, COALESCE(p.full_name, ss.player_name), {cols}
        FROM season_stats ss LEFT JOIN players p ON p.id = ss.player_id
        WHERE ss.season = %s AND ss.team_id IS NOT NULL
    """, (season,))
    out = []
    for row in cur.fetchall():
        team_id, player_id, name = row[0], row[1], tidy_name(row[2])
        for cat, value in zip(SEASON_PLAYER_CATEGORIES, row[3:]):
            if value:
                out.append((cat, team_id, player_id, name, float(value)))
    return out


def season_defense_values(cur, season):
    """[(category, team_id, player_id, holder, value)] for defense and kicking from season_stat_lines. Only players
    Madden lists in a category have a value there (NULL = not listed = unknown, so no entry)."""
    cols = ", ".join(SEASON_DEFENSE_CATEGORIES.values())
    cur.execute(f"""
        SELECT team_id, player_id, COALESCE(full_name, player_name), {cols}
        FROM season_stat_lines
        WHERE season = %s AND team_id IS NOT NULL
          AND (def_tackles IS NOT NULL OR kick_fga IS NOT NULL)
    """, (season,))
    out = []
    for row in cur.fetchall():
        team_id, player_id, name = row[0], row[1], tidy_name(row[2])
        for cat, value in zip(SEASON_DEFENSE_CATEGORIES, row[3:]):
            if value:
                out.append((cat, team_id, player_id, name, float(value)))
    return out


def team_seasons(cur, season):
    """{team_id: {"games", "wins", "losses", "ties", "pf", "pa"}} from completed regular-season games."""
    cur.execute("""
        SELECT home_team_id, away_team_id, home_score, away_score FROM games
        WHERE season = %s AND status = 'COMPLETED' AND week <= %s AND COALESCE(game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
    """, (season, REGULAR_SEASON_FINAL_WEEK))
    rec = collections.defaultdict(lambda: {"games": 0, "wins": 0, "losses": 0, "ties": 0, "pf": 0, "pa": 0})
    for home, away, hs, as_ in cur.fetchall():
        for team, pf, pa in ((home, hs, as_), (away, as_, hs)):
            r = rec[team]
            r["games"] += 1
            r["pf"] += pf
            r["pa"] += pa
            r["wins" if pf > pa else "losses" if pf < pa else "ties"] += 1
    return dict(rec)


def game_player_values(cur, season):
    """[(category, team_id, player_id, holder, value, game_id, week)] for regular-season games. Player names in
    player_stats are abbreviated; the full name comes from the line's player_id."""
    cols = ", ".join(f"{expr} AS {cat}" for cat, expr in GAME_PLAYER_CATEGORIES.items())
    cur.execute(f"""
        SELECT ps.team_id, ps.player_id, COALESCE(m.full_name, ps.player_name), ps.game_id, g.week, {cols}
        FROM player_stats ps
        JOIN games g ON g.id = ps.game_id
        LEFT JOIN players m ON m.id = ps.player_id
        WHERE g.season = %s AND g.status = 'COMPLETED' AND g.week <= %s
          AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
    """, (season, REGULAR_SEASON_FINAL_WEEK))
    out = []
    for row in cur.fetchall():
        team_id, player_id, name, game_id, week = row[0], row[1], tidy_name(row[2]), row[3], row[4]
        for cat, value in zip(GAME_PLAYER_CATEGORIES, row[5:]):
            if value:
                out.append((cat, team_id, player_id, name, float(value), game_id, week))
    return out


# -------------------------------------------------------------------------------------------------- checks ---
def _better(a, b, low):
    return a < b if low else a > b


def _rank(value, entries, low):
    """1 + the number of entries strictly better than value (ties share the rank)."""
    return 1 + sum(1 for v, _, _ in entries if _better(v, value, low))


def _num(v):
    return int(v) if float(v).is_integer() else v


def evaluate(cur, season):
    """Every record the season broke, tied or joined. Returns {"season", "complete", "games_played", "events"} where
    an event is {"type": "nfl_record" | "franchise_record" | "top10", "result": "broke" | "tied" | "ranks", ...}.

    Several players of the same season are measured against each other too: only the season's best can hold a
    franchise record, and the top-10 ranks count this season's other entries."""
    book = load_book(cur, season)
    cats, teams = book["categories"], book["teams"]
    low = lambda cat: cats[cat]["better"] == "LOW"
    holders_text = lambda rows, best: [h if h[:4].isdigit() else f"{h} ({s})" for v, h, s in rows if v == best]

    records = team_seasons(cur, season)
    complete = bool(records) and all(r["games"] >= GAMES_PER_SEASON for r in records.values())
    cands = [{"category": c, "team_id": t, "player_id": p, "holder": h, "value": v}
             for c, t, p, h, v in season_player_values(cur, season) + season_defense_values(cur, season)
             if c in cats]
    for team_id, r in records.items():
        rec = f"{r['wins']}-{r['losses']}" + (f"-{r['ties']}" if r["ties"] else "")
        values = {"season_team_wins": r["wins"], "season_team_points": r["pf"]}
        if r["games"] >= GAMES_PER_SEASON:                 # fewest points allowed needs a full season
            values["season_team_fewest_points_allowed"] = r["pa"]
        for cat, v in values.items():
            cands.append({"category": cat, "team_id": team_id, "player_id": None, "holder": f"{season} {teams[team_id]['name']}",
                          "value": float(v), "record": rec, "games": r["games"]})
    cands += [{"category": c, "team_id": t, "player_id": p, "holder": h, "value": v, "game_id": g, "week": w}
              for c, t, p, h, v, g, w in game_player_values(cur, season)]

    events = []
    base = lambda c: {k: c[k] for k in ("category", "team_id", "player_id", "holder") if k in c} | \
        {"label": cats[c["category"]]["label"], "team": teams[c["team_id"]]["name"], "value": _num(c["value"]),
         **{k: c[k] for k in ("record", "games", "game_id", "week") if k in c}}

    # Franchise records: the season's best for each team and category, against the franchise's history.
    by_team = collections.defaultdict(list)
    for c in cands:
        if (c["team_id"], c["category"]) in book["franchise"]:
            by_team[(c["team_id"], c["category"])].append(c)
    for (team_id, cat), group in by_team.items():
        old = book["franchise"][(team_id, cat)]
        best = (min if low(cat) else max)(v for v, _, _ in old)
        top = (min if low(cat) else max)(c["value"] for c in group)
        if not (_better(top, best, low(cat)) or top == best):
            continue
        for c in group:
            if c["value"] == top:
                events.append({"type": "franchise_record", "result": "broke" if top != best else "tied", **base(c),
                               "old_value": _num(best), "old_holders": holders_text(old, best)})

    # NFL all-time top 10 (players): rank among the existing entries and this season's other entries.
    by_cat = collections.defaultdict(list)
    for c in cands:
        if c["category"] in book["entries"]:
            by_cat[c["category"]].append(c)
    for cat, group in by_cat.items():
        entries = book["entries"][cat]
        best = (min if low(cat) else max)(v for v, _, _ in entries)
        for c in group:
            rank = _rank(c["value"], entries, low(cat)) + sum(1 for o in group if _better(o["value"], c["value"], low(cat)))
            if rank > 10:
                continue
            result = ("broke" if _better(c["value"], best, low(cat)) else "tied" if c["value"] == best else "ranks") \
                if rank == 1 else "ranks"
            events.append({"type": "top10", "result": result, "rank": rank, **base(c), "nfl_record": _num(best),
                           "nfl_record_holders": holders_text(entries, best)})

    # League-wide team records (computed: the best franchise record across teams).
    for cat in TEAM_CATEGORIES:
        rows = [(v, h, s) for (t, c), rs in book["franchise"].items() if c == cat for v, h, s in rs]
        group = [c for c in cands if c["category"] == cat]
        if not rows or not group:
            continue
        best = (min if low(cat) else max)(v for v, _, _ in rows)
        top = (min if low(cat) else max)(c["value"] for c in group)
        if _better(top, best, low(cat)) or top == best:
            for c in group:
                if c["value"] == top:
                    events.append({"type": "nfl_record", "result": "broke" if top != best else "tied", **base(c),
                                   "old_value": _num(best), "old_holders": holders_text(rows, best)})

    # The record book is the authority for its seasons: mark the events it confirms and list what it has that the
    # stats here don't (different data, or a category the engine doesn't compute).
    book_rows, book_entries = book["book_rows"], book["book_entries"]
    found = {(e["team_id"], e["category"]) for e in events if e["type"] == "franchise_record"}
    for e in events:
        if e["type"] == "franchise_record":
            e["in_book"] = any(b["team_id"] == e["team_id"] and b["category"] == e["category"] for b in book_rows)
    # Only real disagreements: a book record the engine could have found (a category it computes, with an earlier
    # record to beat), and a broken record in a category the book tracks (the book lists records broken, not ties,
    # and has no team or combined-TD records).
    computed = set(SEASON_PLAYER_CATEGORIES) | set(SEASON_DEFENSE_CATEGORIES) | set(GAME_PLAYER_CATEGORIES) | set(TEAM_CATEGORIES)
    book_only = [{**b, "team": teams[b["team_id"]]["name"], "label": cats[b["category"]]["label"], "value": _num(b["value"])}
                 for b in book_rows if (b["team_id"], b["category"]) not in found
                 and b["category"] in computed and (b["team_id"], b["category"]) in book["franchise"]]
    stats_only = [e for e in events if e["type"] == "franchise_record" and book_rows and not e["in_book"]
                  and e["result"] == "broke" and e["category"] in book.get("book_categories", set())]

    order = {"nfl_record": 0, "top10": 1, "franchise_record": 2}
    events.sort(key=lambda e: (order[e["type"]], e.get("rank", 0), list(cats).index(e["category"]), -float(e["value"])))
    return {"season": season, "complete": complete, "games_played": {teams[t]["abbr"]: r["games"] for t, r in records.items()},
            "events": events, "book_only": book_only, "stats_only": stats_only, "book_checked": bool(book_rows),
            "book_keys": {"franchise": sorted({(b["team_id"], b["category"]) for b in book_rows}),
                          "entries": sorted({(b["team_id"], b["category"]) for b in book_entries})}}


def apply(cur, season, result):
    """Stores the season's record rows (source 'LEAGUE'), replacing any stored earlier for the same season. Where the
    record book (BOOK) already has the season's row for that team and category, the book's row stands."""
    book_f = {tuple(k) for k in result["book_keys"]["franchise"]}
    book_e = {tuple(k) for k in result["book_keys"]["entries"]}
    cur.execute("DELETE FROM franchise_records WHERE source = 'LEAGUE' AND season = %s", (season,))
    cur.execute("DELETE FROM league_record_entries WHERE source = 'LEAGUE' AND season = %s", (season,))
    n_f = n_l = 0
    for e in result["events"]:
        if e["type"] == "franchise_record" and (e["team_id"], e["category"]) not in book_f:
            cur.execute("""INSERT INTO franchise_records (team_id, category, value, holder, player_id, season, source)
                           VALUES (%s, %s, %s, %s, %s, %s, 'LEAGUE') ON CONFLICT (team_id, category, holder, season) DO UPDATE
                           SET value = EXCLUDED.value, player_id = EXCLUDED.player_id""",
                        (e["team_id"], e["category"], e["value"], e["holder"], e["player_id"], season))
            n_f += 1
        elif e["type"] == "top10" and (e["team_id"], e["category"]) not in book_e:
            cur.execute("""INSERT INTO league_record_entries (category, value, holder, team_id, player_id, season, game_id, source)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, 'LEAGUE') ON CONFLICT DO NOTHING""",
                        (e["category"], e["value"], e["holder"], e["team_id"], e["player_id"], season, e.get("game_id")))
            n_l += 1
    return {"franchise_rows": n_f, "top10_rows": n_l}


# ----------------------------------------------------------------------------------------------------- CLI ---
def format_report(result) -> str:
    ev = result["events"]
    played = result["games_played"].values()
    out = [f"Records set in the {result['season']} season" +
           ("" if result["complete"] else f" (regular season not finished: {min(played, default=0)}–{max(played, default=0)} "
                                          f"of {GAMES_PER_SEASON} games played)")]
    num = lambda v: f"{v:,}" if isinstance(v, int) else str(v)

    def who(e):
        if e["holder"][:4].isdigit():                       # a team season
            return f"{e['team']} ({e['record']})"
        return f"{e['holder']} ({e['team']}" + (f", Week {e['week']}" if e.get("week") else "") + ")"

    nfl = [e for e in ev if e["type"] == "nfl_record" or (e["type"] == "top10" and e["result"] in ("broke", "tied"))]
    out.append("\nNFL records:")
    out += [f"  {e['label']}: {who(e)}, {num(e['value'])}: {'broke' if e['result'] == 'broke' else 'tied'} "
            f"{', '.join(e.get('old_holders') or e.get('nfl_record_holders'))} ({num(e.get('old_value', e.get('nfl_record')))})"
            for e in nfl] or ["  none"]
    top = [e for e in ev if e["type"] == "top10" and e["result"] == "ranks"]
    out.append("\nNFL all-time top 10 (also past the old record when it says so):")
    out += [f"  {e['label']}: {who(e)}, {num(e['value'])}: {ordinal(e['rank'])} all time"
            + (" (also past the old record)" if (e["value"] > e["nfl_record"]) else "") for e in top] or ["  none"]
    by_team = collections.defaultdict(list)
    for e in ev:
        if e["type"] == "franchise_record":
            by_team[e["team"]].append(e)
    out.append("\nFranchise records:")
    for team in sorted(by_team):
        out.append(f"  {team}")
        out += [f"    {e['label']}: " + (e["record"] if e["holder"][:4].isdigit() else e["holder"]) +
                f", {num(e['value'])}: {'broke' if e['result'] == 'broke' else 'tied'} {', '.join(e['old_holders'])} ({num(e['old_value'])})"
                for e in by_team[team]]
    if not by_team:
        out.append("  none")
    if result.get("book_checked") and not (result.get("book_only") or result.get("stats_only")):
        out.append(f"\nThe record book's {result['season']} entries and the stats agree.")
    if result.get("book_only") or result.get("stats_only"):
        out.append("\nWhere the record book and the stats disagree:")
        out += [f"  In the record book, not in the stats: {b['team']}, {b['label']}: {b['holder']}, {num(b['value'])}"
                for b in result.get("book_only", [])]
        out += [f"  In the stats, not in the record book: {e['team']}, {e['label']}: "
                + (e["record"] if e["holder"][:4].isdigit() else e["holder"]) + f", {num(e['value'])}"
                for e in result.get("stats_only", [])]
    return "\n".join(out)


def run(season: int | None = None, apply_changes: bool = True, conn_info: str | None = None) -> dict:
    """Run the records engine for `season` (default: league_state.current_season).
    Evaluates the season against the record book and, if apply_changes=True, stores
    any new franchise records and all-time top-10 rows (source 'LEAGUE').
    """
    from db.db_handler import DatabaseHandler
    db = DatabaseHandler(db_conn_info=conn_info)
    if season is None:
        season = db.get_current_league_state()[0]

    with psycopg.connect(db.db_conn_info) as conn:
        with conn.cursor() as cur:
            result = evaluate(cur, season)
            if apply_changes:
                stored = apply(cur, season, result)
                conn.commit()
                result["stored"] = stored
    return result


class RecordsEngine:
    def __init__(self, db_conn_info: str | None = None):
        self.db_conn_info = db_conn_info

    def calculate(self, season: int | None = None, apply_changes: bool = True) -> dict:
        result = run(season=season, apply_changes=apply_changes, conn_info=self.db_conn_info)
        stored_info = ""
        if apply_changes and "stored" in result:
            stored = result["stored"]
            stored_info = f" ({stored['franchise_rows']} franchise rows, {stored['top10_rows']} top-10 rows stored)"
        print(f"Records evaluated for {result['season']}: {len(result['events'])} events{stored_info}")
        return result


def main():
    ap = argparse.ArgumentParser(description="Check a league season against the record book.")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--apply", action="store_true", help="store the season's records (source LEAGUE)")
    ap.add_argument("--json", help="also write the events to this file")
    args = ap.parse_args()

    from db.db_handler import DatabaseHandler
    with psycopg.connect(DatabaseHandler().db_conn_info) as conn:
        with conn.cursor() as cur:
            result = evaluate(cur, args.season)
            print(format_report(result))
            if args.apply:
                print(f"\nStored: {apply(cur, args.season, result)}")
                conn.commit()
    if args.json:
        with open(args.json, "w") as f:
            json.dump(result, f, indent=1, default=str)


if __name__ == "__main__":
    main()
