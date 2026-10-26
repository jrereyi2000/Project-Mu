"""Record Book and Awards backend (/api/records/*, /api/awards*; docs/RECORD_BOOK_AND_AWARDS_PLAN.md, PROJECT_SPEC §2.9,
§4.7 and §7.6b–§7.6d).

Read-only, public. It serves the record book the records engine keeps (record_categories, league_record_entries,
franchise_records and the league_records view) as raw numbers for the #records page to format. Every mark carries its
season and source: NFL (real history through 2025), BOOK (the game's record book, 2026–2035) or LEAGUE (written by
RecordsEngine from this league's own seasons). BOOK and LEAGUE marks from 2026 on are "set in this league" (mu).

Ranking uses the engine's own rules (records_engine.rank / better): ties share a rank and better = 'LOW' ranks the
smallest value first. A current-season LEAGUE mark is "in progress" while that season has unplayed regular-season
games, because the engine rewrites those rows every week. When the tables don't exist (migrations not applied) every
endpoint returns empty blocks rather than an error.

The awards endpoints read the stored `awards` rows (year-end awards, Super Bowl MVP, All-Pro teams; newsroom/all_pro.py
and newsroom/award_winners.py write them) with each season's Super Bowl from `games` and the champion's head coach
from `coach_tenures`. A row without a player_id is served by name only.
"""
import os
from typing import Optional

import psycopg
from fastapi import APIRouter, HTTPException

from engines.coach_profile import fetch_team_coach
from engines.records_engine import (GAMES_PER_SEASON, better, load_book, rank, season_defense_values,
                                    season_player_values, team_seasons, tidy_name)
from newsroom.all_pro import DEFENSE_QUOTAS, OFFENSE_QUOTAS, SPECIAL_TEAMS_QUOTAS

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
ABBR_ALIASES = {"ARI": "AZ", "CRD": "AZ", "WSH": "WAS", "WTC": "WAS"}
MU_START = 2026          # the league's first season: BOOK and LEAGUE marks from here on were set in this league
MU_SOURCES = ("BOOK", "LEAGUE")
TOP = 10
AWARD_TYPES = ("MVP", "OPOY", "DPOY", "OROY", "DROY", "COTY", "SBMVP")   # the award history table's columns
ALL_PRO_TEAMS = {"First Team All-Pro": "first_team", "Second Team All-Pro": "second_team"}
ALL_PRO_UNITS = (("OFFENSE", OFFENSE_QUOTAS), ("DEFENSE", DEFENSE_QUOTAS), ("SPECIAL_TEAMS", SPECIAL_TEAMS_QUOTAS))
SLOT_ORDER = [slot for _, quotas in ALL_PRO_UNITS for slot in quotas]
NO_PACE = {"season_fg_long"}   # a longest-kick mark doesn't add up over games, so Record Watch shows no 17-game pace

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


def season_in_progress(cur, season: int) -> bool:
    """Some regular-season game of the season isn't COMPLETED yet (D3)."""
    cur.execute("SELECT EXISTS (SELECT 1 FROM games WHERE season = %s AND game_type = 'REGULAR_SEASON' "
                "AND status IS DISTINCT FROM 'COMPLETED');", (season,))
    return bool(cur.fetchone()[0])


def fetch_as_of(cur) -> dict:
    cur.execute("SELECT current_season, current_week FROM league_state LIMIT 1;")
    row = cur.fetchone()
    if not row:
        return {"season": None, "week": None, "in_progress": False}
    season, week = row
    return {"season": season, "week": week, "in_progress": season_in_progress(cur, season)}


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
    """Rows as dicts from a tuple cursor or a dict_row cursor (server/api.py's pool)."""
    cur.execute(sql, params)
    cols = [d.name for d in cur.description]
    return [r if isinstance(r, dict) else dict(zip(cols, r)) for r in cur.fetchall()]


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


def fetch_titles(cur, team_id: int, abbrs: dict) -> list:
    """The team's Super Bowl wins in this league, newest first: [{season, game_id, score, opponent, opponent_score}]."""
    games = _rows(cur, "SELECT id, season, home_team_id, away_team_id, home_score, away_score FROM games "
                       "WHERE game_type = 'SUPER_BOWL' AND status = 'COMPLETED' AND %s IN (home_team_id, away_team_id) "
                       "ORDER BY season DESC;", (team_id,))
    out = []
    for g in games:
        sb = super_bowl(g, abbrs)
        if sb and sb["team_id"] == team_id:
            out.append({"season": g["season"], "game_id": sb["game_id"], "score": sb["score"],
                        "opponent": sb["opponent"], "opponent_score": sb["opponent_score"]})
    return out


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
                   "as_of": as_of, "mu_count": 0, "records": [], "titles": []}
            abbrs = fetch_abbrs(cur)
            out["titles"] = fetch_titles(cur, team_id, abbrs)
            if not _installed(cur):
                return out
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


# ------------------------------------------------------------------------------------------------- watch ---
def watch_category(cat: dict, entries: list, values: list, games: dict, abbrs: dict) -> Optional[dict]:
    """Record Watch for one FULL single-season player category: the season's leader(s) so far against the record
    from before this season (records_engine.load_book's entries). `entries` is [(value, holder, season)], `values`
    is [(team_id, player_id, holder, value)] for this season and `games` is {team_id: games played}. None when
    nobody has a value yet or the category has no record to chase."""
    if not values or not entries:
        return None
    low = cat["better"] == "LOW"
    best = (min if low else max)(v for v, _, _ in entries)
    top = (min if low else max)(v for *_, v in values)
    leaders = sorted(({"holder": h, "player_id": p, "team": abbrs.get(t), "games": games.get(t, 0)}
                      for t, p, h, v in values if v == top), key=lambda l: l["holder"] or "")
    played = max((l["games"] for l in leaders), default=0)
    pace = None if cat["category"] in NO_PACE or not played else round(top / played * GAMES_PER_SEASON)
    return {
        "category": cat["category"], "label": cat["label"], "better": cat["better"],
        "record": {"value": num(best), "holders": sorted(({"holder": tidy_name(h), "season": s}
                                                         for v, h, s in entries if v == best),
                                                        key=lambda h: (-(h["season"] or 0), h["holder"] or ""))},
        "leaders": leaders,
        "value": num(top),
        "pct": round(top / best * 100, 1) if best and not low else None,
        "pace_17": pace,
        "on_pace": pace is not None and not better(best, pace, low),
        "rank": rank(top, entries, low),
    }


@router.get("/api/records/watch")
def record_watch(season: Optional[int] = None):
    """This season's leaders in each FULL single-season player category against the record, while the regular
    season is being played. `season` defaults to the current one; a finished regular season returns no rows."""
    with psycopg.connect(CONN_INFO) as conn:
        with conn.cursor() as cur:
            as_of = fetch_as_of(cur)
            season = season or as_of["season"]
            out = {"season": season, "week": as_of["week"] if season == as_of["season"] else None,
                   "in_progress": False, "categories": []}
            if season is None or not _installed(cur) or not season_in_progress(cur, season):
                return out
            out["in_progress"] = True
            book, abbrs = load_book(cur, season), fetch_abbrs(cur)
            games = {t: r["games"] for t, r in team_seasons(cur, season).items()}
            values = {}
            for c, t, p, h, v in season_player_values(cur, season) + season_defense_values(cur, season):
                values.setdefault(c, []).append((t, p, h, v))
            for cat in fetch_categories(cur):
                if (cat["coverage"], cat["scope"], cat["subject"], cat["season_type"]) != ("FULL", "SEASON", "PLAYER", "REGULAR"):
                    continue
                row = watch_category(cat, book["entries"].get(cat["category"], []), values.get(cat["category"], []),
                                     games, abbrs)
                if row:
                    out["categories"].append(row)
            return out


# ------------------------------------------------------------------------------------------------- awards ---
def shape_award(row: dict, abbrs: dict) -> dict:
    """One awards row: a player (linked when player_id is set) or, for Coach of the Year, a coach."""
    coach = row["award_type"] == "COTY"
    return {
        "award_type": row["award_type"],
        "season": row["season"],
        "name": row["coach_name"] if coach else row["player_name"],
        "player_id": None if coach else row["player_id"],
        "coach_id": row["coach_id"] if coach else None,
        "position": None if coach else row["position"],
        "team": abbrs.get(row["team_id"]),
    }


def super_bowl(row: dict, abbrs: dict) -> Optional[dict]:
    """A Super Bowl game as {team, score, opponent, opponent_score, game_id}, from the winner's side; None when it
    isn't final or ended level."""
    if row["home_score"] is None or row["away_score"] is None or row["home_score"] == row["away_score"]:
        return None
    home_won = row["home_score"] > row["away_score"]
    win, lose = ("home", "away") if home_won else ("away", "home")
    return {"game_id": row["id"], "team": abbrs.get(row[f"{win}_team_id"]), "team_id": row[f"{win}_team_id"],
            "score": row[f"{win}_score"], "opponent": abbrs.get(row[f"{lose}_team_id"]),
            "opponent_score": row[f"{lose}_score"]}


def award_leaders(awards: list) -> list:
    """Repeat winners of the same award (e.g. 2x MVP), most wins first. Players match on player_id, else on name."""
    groups = {}
    for a in awards:
        if not a["name"]:
            continue
        who = ("coach", a["coach_id"]) if a["coach_id"] else ("player", a["player_id"]) if a["player_id"] \
            else ("name", a["name"].lower())
        groups.setdefault((a["award_type"], who), []).append(a)
    out = []
    for (award_type, _), rows in groups.items():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda a: a["season"])
        last = rows[-1]
        out.append({"award_type": award_type, "count": len(rows), "name": last["name"], "player_id": last["player_id"],
                    "coach_id": last["coach_id"], "team": last["team"], "seasons": [a["season"] for a in rows]})
    out.sort(key=lambda x: (-x["count"], AWARD_TYPES.index(x["award_type"]), -x["seasons"][-1]))
    return out


def franchise_counts(seasons: list) -> list:
    """Titles and award wins per franchise since 2026 (award history columns only, not All-Pro), most titles first."""
    counts = {}
    for s in seasons:
        if s["champion"]:
            counts.setdefault(s["champion"]["team"], {"titles": 0, "awards": 0})["titles"] += 1
        for a in s["awards"].values():
            if a and a["team"]:
                counts.setdefault(a["team"], {"titles": 0, "awards": 0})["awards"] += 1
    out = [dict(team=t, **c) for t, c in counts.items() if t]
    out.sort(key=lambda x: (-x["titles"], -x["awards"], x["team"]))
    return out


def all_pro_team(rows: list) -> list:
    """One All-Pro team in slot order (offense, defense, special teams; newsroom/all_pro.py quotas), stored order
    within a slot. A slot the quotas don't know goes last."""
    def key(r):
        slot = (r["position"] or "").upper()
        return (SLOT_ORDER.index(slot) if slot in SLOT_ORDER else len(SLOT_ORDER), r["id"])
    out = []
    for r in sorted(rows, key=key):
        slot = (r["position"] or "").upper()
        unit = next((u for u, quotas in ALL_PRO_UNITS if slot in quotas), "OTHER")
        out.append({"slot": slot, "unit": unit, "name": r["player_name"], "player_id": r["player_id"],
                    "team": r["team"]})
    return out


def fetch_player_awards(cur, player_id: int) -> list:
    """Every stored award for one player (year-end awards, SB MVP, All-Pro), newest first, for the player page's
    Honors row (server/api.py get_player). Works on a tuple or dict_row cursor; empty without the awards table."""
    cur.execute("SELECT to_regclass('awards') IS NOT NULL AS present;")
    row = cur.fetchone()
    if not (row["present"] if isinstance(row, dict) else row[0]):
        return []
    rows = _rows(cur, "SELECT a.season, a.award_type, a.position, t.abbreviation AS team FROM awards a "
                      "LEFT JOIN teams t ON t.id = a.team_id WHERE a.player_id = %s "
                      "ORDER BY a.season DESC, a.id;", (player_id,))
    return [{"season": r["season"], "award_type": r["award_type"], "position": r["position"], "team": r["team"]}
            for r in rows]


@router.get("/api/awards")
def awards():
    """Champions and award history, newest season first: each season's Super Bowl result, the champion's head coach
    and the award winners (award history columns), plus repeat winners and each franchise's titles and awards."""
    with psycopg.connect(CONN_INFO) as conn:
        with conn.cursor() as cur:
            out = {"award_types": list(AWARD_TYPES), "seasons": [], "leaders": [], "franchise_counts": []}
            if not _regclass(cur, "awards"):
                return out
            abbrs = fetch_abbrs(cur)
            rows = [shape_award(r, abbrs) for r in _rows(
                cur, "SELECT season, award_type, player_id, player_name, position, team_id, coach_id, coach_name "
                     "FROM awards WHERE award_type = ANY(%s) ORDER BY season, id;", (list(AWARD_TYPES),))]
            games = {r["season"]: super_bowl(r, abbrs) for r in _rows(
                cur, "SELECT id, season, home_team_id, away_team_id, home_score, away_score FROM games "
                     "WHERE game_type = 'SUPER_BOWL' AND status = 'COMPLETED';")}
            has_tenures = _regclass(cur, "coach_tenures")
            for season in sorted({r["season"] for r in rows} | set(games), reverse=True):
                champ = games.get(season)
                coach = None
                if champ and has_tenures:
                    hc = fetch_team_coach(cur, champ["team_id"], season)
                    coach = {"coach_id": hc["id"], "name": hc["coach_name"]} if hc else None
                won = {}
                for r in rows:
                    if r["season"] == season:
                        won.setdefault(r["award_type"], r)
                out["seasons"].append({"season": season, "champion": champ, "champion_coach": coach,
                                       "sb_mvp": won.get("SBMVP"),
                                       "awards": {t: won.get(t) for t in AWARD_TYPES}})
            out["leaders"] = award_leaders(rows)
            out["franchise_counts"] = franchise_counts(out["seasons"])
            return out


@router.get("/api/awards/all-pro")
def all_pro(season: Optional[int] = None):
    """One season's First and Second Team All-Pro in slot order. `season` defaults to the latest stored."""
    with psycopg.connect(CONN_INFO) as conn:
        with conn.cursor() as cur:
            out = {"seasons_available": [], "season": season, "first_team": [], "second_team": []}
            if not _regclass(cur, "awards"):
                return out
            cur.execute("SELECT DISTINCT season FROM awards WHERE award_type = ANY(%s) ORDER BY season DESC;",
                        (list(ALL_PRO_TEAMS),))
            out["seasons_available"] = [r[0] for r in cur.fetchall()]
            if season is None:
                season = out["season"] = out["seasons_available"][0] if out["seasons_available"] else None
            if season is None:
                return out
            abbrs = fetch_abbrs(cur)
            rows = _rows(cur, "SELECT id, award_type, player_id, player_name, position, team_id FROM awards "
                              "WHERE season = %s AND award_type = ANY(%s);", (season, list(ALL_PRO_TEAMS)))
            for r in rows:
                r["team"] = abbrs.get(r["team_id"])
            for award_type, key in ALL_PRO_TEAMS.items():
                out[key] = all_pro_team([r for r in rows if r["award_type"] == award_type])
            return out
