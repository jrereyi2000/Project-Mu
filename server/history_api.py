"""Record Book backend (/api/records/*; docs/RECORD_BOOK_AND_AWARDS_PLAN.md, PROJECT_SPEC §2.9 and §4.7).

Read-only, public. It serves the record book the records engine keeps (record_categories, league_record_entries,
franchise_records and the league_records view) as raw numbers for the #records page to format. Every mark carries its
season and source: NFL (real history through 2025), BOOK (the game's record book, 2026–2035) or LEAGUE (written by
RecordsEngine from this league's own seasons). BOOK and LEAGUE marks from 2026 on are "set in this league" (mu).

Ranking uses the engine's own rules (records_engine.rank / better): ties share a rank and better = 'LOW' ranks the
smallest value first. A current-season LEAGUE mark is "in progress" while that season has unplayed regular-season
games, because the engine rewrites those rows every week. When the tables don't exist (migrations not applied) every
endpoint returns empty blocks rather than an error.
"""
import os
from typing import Optional

import psycopg
from fastapi import APIRouter, HTTPException

from engines.records_engine import better, rank, tidy_name

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
ABBR_ALIASES = {"ARI": "AZ", "CRD": "AZ", "WSH": "WAS", "WTC": "WAS"}
MU_START = 2026          # the league's first season: BOOK and LEAGUE marks from here on were set in this league
MU_SOURCES = ("BOOK", "LEAGUE")
TOP = 10

router = APIRouter()


# --------------------------------------------------------------------------------------------- pure helpers ---
def is_mu(source: str, season: Optional[int]) -> bool:
    """A mark set in this league (the game's record book or the league's own seasons), not NFL history."""
    return source in MU_SOURCES and (season or 0) >= MU_START


def mark_in_progress(source: str, season: Optional[int], as_of: dict) -> bool:
    """A LEAGUE mark from the current season while that regular season still has games to play (D3)."""
    return bool(as_of.get("in_progress")) and source == "LEAGUE" and season == as_of.get("season")


def category_group(cat: dict) -> str:
    """The page's scope chip: SEASON / GAME / ROOKIE for the regular season, GAME / SEASON / SUPER_BOWL / CAREER
    for the postseason (Super Bowl games and Super Bowl wins share the Super Bowl chip)."""
    name = cat["category"]
    if cat["season_type"] == "POSTSEASON":
        if name.startswith("super_bowl_") or name.startswith("career_super_bowl_"):
            return "SUPER_BOWL"
        return cat["scope"]
    if name.startswith("rookie_"):
        return "ROOKIE"
    return cat["scope"]


def num(v):
    """Decimal/float as int when whole (sacks keep their half)."""
    if v is None:
        return None
    f = float(v)
    return int(f) if f.is_integer() else f


def ranked(marks: list, low: bool, limit: Optional[int] = TOP) -> list:
    """Marks with their rank (ties share it, records_engine.rank), best first, cut at rank <= limit."""
    values = [(m["value"], None, None) for m in marks]
    out = [dict(m, rank=rank(m["value"], values, low)) for m in marks]
    out.sort(key=lambda m: (m["rank"], -(m["season"] or 0), m["holder"] or ""))
    return [m for m in out if limit is None or m["rank"] <= limit]


def dedupe(marks: list) -> list:
    """One mark per (holder, season, value): the same season can sit on the NFL list and the franchise list."""
    seen, out = set(), []
    for m in marks:
        key = ((m["holder"] or "").lower(), m["season"], m["value"])
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


def previous_nfl_record(top: list, low: bool) -> Optional[dict]:
    """The best NFL mark when a mark set in this league beats it outright (not a tie)."""
    nfl = [m for m in top if m["source"] == "NFL"]
    mu = [m for m in top if m["mu"]]
    if not nfl or not mu:
        return None
    best_nfl = min(nfl, key=lambda m: m["value"]) if low else max(nfl, key=lambda m: m["value"])
    best_mu = min(mu, key=lambda m: m["value"]) if low else max(mu, key=lambda m: m["value"])
    if not better(best_mu["value"], best_nfl["value"], low):
        return None
    return {k: best_nfl[k] for k in ("value", "holder", "team", "season", "source")}


def record_result(holders: list, top: list, low: bool) -> Optional[str]:
    """'broke' when a league mark holds the record alone above NFL history, 'tied' when it shares it with an NFL
    mark, None when the record is NFL history only (or there is no NFL history to measure against)."""
    if not holders or not any(h["mu"] for h in holders):
        return None
    nfl = [m["value"] for m in top if m["source"] == "NFL"]
    if not nfl:
        return None
    best_nfl = min(nfl) if low else max(nfl)
    value = holders[0]["value"]
    return "tied" if value == best_nfl else ("broke" if better(value, best_nfl, low) else None)


# ------------------------------------------------------------------------------------------------- reading ---
def _regclass(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL;", (name,))
    return bool(cur.fetchone()[0])


def _installed(cur) -> bool:
    return all(_regclass(cur, t) for t in ("record_categories", "league_record_entries", "franchise_records",
                                             "franchise_records_current", "league_records"))


def fetch_as_of(cur) -> dict:
    cur.execute("SELECT current_season, current_week FROM league_state LIMIT 1;")
    row = cur.fetchone()
    if not row:
        return {"season": None, "week": None, "in_progress": False}
    season, week = row
    cur.execute("SELECT EXISTS (SELECT 1 FROM games WHERE season = %s AND game_type = 'REGULAR_SEASON' "
                "AND status IS DISTINCT FROM 'COMPLETED');", (season,))
    return {"season": season, "week": week, "in_progress": bool(cur.fetchone()[0])}


def fetch_categories(cur) -> list:
    cur.execute("SELECT category, label, scope, subject, better, sort_order, coverage, "
                "COALESCE(season_type, 'REGULAR') FROM record_categories ORDER BY sort_order;")
    cats = [{"category": r[0], "label": r[1], "scope": r[2], "subject": r[3], "better": r[4], "sort_order": r[5],
             "coverage": r[6], "season_type": r[7]} for r in cur.fetchall()]
    for c in cats:
        c["group"] = category_group(c)
    return cats


def fetch_abbrs(cur) -> dict:
    cur.execute("SELECT id, abbreviation FROM teams;")
    return dict(cur.fetchall())


def shape_mark(row: dict, abbrs: dict, as_of: dict) -> dict:
    """One record row (entry, franchise record or league_records holder) as the page's mark."""
    return {
        "value": num(row["value"]),
        "holder": tidy_name(row["holder"]),
        "player_id": row.get("player_id"),
        "team": abbrs.get(row.get("team_id")),
        "season": row["season"],
        "game_date": row["game_date"].isoformat() if row.get("game_date") else None,
        "source": row["source"],
        "mu": is_mu(row["source"], row["season"]),
        "in_progress": mark_in_progress(row["source"], row["season"], as_of),
    }


def _rows(cur, sql: str, params=()) -> list:
    cur.execute(sql, params)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def fetch_marks(cur, abbrs: dict, as_of: dict) -> tuple:
    """(entries by category, franchise rows by category, current franchise rows by category, record holders by
    category), each as shaped marks."""
    def by_cat(rows):
        out = {}
        for r in rows:
            out.setdefault(r["category"], []).append(shape_mark(r, abbrs, as_of))
        return out
    entries = by_cat(_rows(cur, "SELECT category, value, holder, player_id, team_id, season, game_date, source "
                                "FROM league_record_entries;"))
    franchise = by_cat(_rows(cur, "SELECT category, value, holder, player_id, team_id, season, NULL::date AS game_date, "
                                  "source FROM franchise_records;"))
    current = by_cat(_rows(cur, "SELECT category, value, holder, player_id, team_id, season, NULL::date AS game_date, "
                                "source FROM franchise_records_current;"))
    holders = by_cat(_rows(cur, "SELECT category, value, holder, player_id, team_id, season, game_date, source "
                                "FROM league_records;"))
    return entries, franchise, current, holders


def build_category(cat: dict, entries: list, franchise: list, holders: list, era: str,
                   limit: Optional[int] = TOP) -> Optional[dict]:
    """One category card: the record holder(s), the top 10 and, when a league mark broke it, the NFL record it
    replaced. All-time ranks the NFL list (league_record_entries, the league_top10 view's pool); a category with no
    list (team seasons, most BOOK categories) ranks its franchise records instead (basis 'franchise'). The Mu Era
    view ranks only marks set in this league, from both lists. All-time keeps a category with no marks yet (an empty
    card: tackles and defensive TDs wait for a league season); the Mu Era view leaves it out (None)."""
    low = cat["better"] == "LOW"
    if era == "mu":
        pool = dedupe([m for m in entries + franchise if m["mu"]])
        basis = "mu"
    elif entries:
        pool, basis = list(entries), "nfl_list"
    else:
        pool, basis = dedupe(franchise), "franchise"
    if not pool:
        return None if era == "mu" else dict(cat, basis="none", record=[], top10=[], result=None,
                                              previous_nfl_record=None)
    top = ranked(pool, low, limit)
    if era == "mu" or not holders:
        record = [m for m in top if m["rank"] == 1]
    else:
        record = sorted(holders, key=lambda m: (-(m["season"] or 0), m["holder"] or ""))
    return dict(cat, basis=basis, record=record, top10=top,
                result=None if era == "mu" else record_result(record, top, low),
                previous_nfl_record=None if era == "mu" else previous_nfl_record(top, low))


def mu_mark_count(holders_by_cat: dict) -> int:
    """NFL records set or tied in this league: league_records holders set since 2026 (one per category, holder and
    season)."""
    return len({(cat, m["holder"], m["season"]) for cat, ms in holders_by_cat.items() for m in ms if m["mu"]})


def resolve_team(cur, abbr: str) -> tuple:
    clean = (abbr or "").upper().strip()
    cur.execute("SELECT id, abbreviation, city, name, team_color FROM teams WHERE abbreviation IN (%s, %s);",
                (clean, ABBR_ALIASES.get(clean, clean)))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"Team '{abbr}' not found")
    return row


# ----------------------------------------------------------------------------------------------- endpoints ---
@router.get("/api/records/categories")
def record_categories():
    with psycopg.connect(CONN_INFO) as conn:
        with conn.cursor() as cur:
            if not _regclass(cur, "record_categories"):
                return []
            return fetch_categories(cur)


@router.get("/api/records")
def records(season_type: str = "REGULAR", scope: Optional[str] = None, subject: Optional[str] = None,
            era: str = "all"):
    """Every category card for one season type, optionally narrowed to one scope chip (group) and subject."""
    season_type, era = season_type.upper(), era.lower()
    with psycopg.connect(CONN_INFO) as conn:
        with conn.cursor() as cur:
            as_of = fetch_as_of(cur)
            out = {"as_of": as_of, "season_type": season_type, "era": era, "mu_marks": 0, "categories": []}
            if not _installed(cur):
                return out
            abbrs = fetch_abbrs(cur)
            entries, franchise, _, holders = fetch_marks(cur, abbrs, as_of)
            out["mu_marks"] = mu_mark_count(holders)
            for cat in fetch_categories(cur):
                if cat["season_type"] != season_type:
                    continue
                if scope and cat["group"] != scope.upper():
                    continue
                if subject and cat["subject"] != subject.upper():
                    continue
                card = build_category(cat, entries.get(cat["category"], []), franchise.get(cat["category"], []),
                                      holders.get(cat["category"], []), era)
                if card:
                    out["categories"].append(card)
            return out


@router.get("/api/records/category/{category}")
def record_category(category: str):
    """One category in full: the whole ranked list (not cut at 10), every franchise's current record for it, and the
    league's marks outside the top 10."""
    with psycopg.connect(CONN_INFO) as conn:
        with conn.cursor() as cur:
            if not _installed(cur):
                raise HTTPException(404, "Record book not installed")
            cat = next((c for c in fetch_categories(cur) if c["category"] == category), None)
            if not cat:
                raise HTTPException(404, f"Category '{category}' not found")
            as_of = fetch_as_of(cur)
            abbrs = fetch_abbrs(cur)
            entries, franchise, current, holders = fetch_marks(cur, abbrs, as_of)
            card = build_category(cat, entries.get(category, []), franchise.get(category, []),
                                  holders.get(category, []), "all", limit=None) or dict(cat, record=[], top10=[])
            full = card.pop("top10", [])
            card["top10"] = [m for m in full if m["rank"] <= TOP]
            card["mu_outside_top10"] = [m for m in full if m["rank"] > TOP and m["mu"]]
            low = cat["better"] == "LOW"
            card["franchise_records"] = sorted(current.get(category, []),
                                               key=lambda m: (m["value"] if low else -m["value"], m["team"] or ""))
            card["as_of"] = as_of
            return card


@router.get("/api/records/franchise/{abbr}")
def franchise_records(abbr: str):
    """The team's current record in every regular-season category it holds one in (ties: every holder), whether
    it is also the league-wide record, and which were set in this league."""
    with psycopg.connect(CONN_INFO) as conn:
        with conn.cursor() as cur:
            team_id, team_abbr, city, name, color = resolve_team(cur, abbr)
            as_of = fetch_as_of(cur)
            out = {"team": {"id": team_id, "abbreviation": team_abbr, "name": f"{city} {name}", "color": color},
                   "as_of": as_of, "mu_count": 0, "records": []}
            if not _installed(cur):
                return out
            abbrs = fetch_abbrs(cur)
            _, _, current, holders = fetch_marks(cur, abbrs, as_of)
            for cat in fetch_categories(cur):
                if cat["season_type"] != "REGULAR":
                    continue
                mine = [m for m in current.get(cat["category"], []) if m["team"] == team_abbr]
                if not mine:
                    continue
                league = holders.get(cat["category"], [])
                mine.sort(key=lambda m: (-(m["season"] or 0), m["holder"] or ""))
                out["records"].append(dict(
                    cat, record=mine,
                    league_record=bool(league) and mine[0]["value"] == league[0]["value"],
                    mu=any(m["mu"] for m in mine)))
            out["mu_count"] = sum(1 for r in out["records"] if r["mu"])
            return out
