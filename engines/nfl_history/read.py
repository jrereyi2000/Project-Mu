"""Reads of real NFL history for the site, writers and engines (docs/done/REAL_NFL_HISTORY_PLAN.md §2.8).

Every function returns empty ({} / [] / None) when the nfl_* tables aren't installed (installed(cur) is false), so
callers need no migration check. Cursors may return tuples or dicts; rows come back as dicts.

- installed(cur) -> bool
- player_nfl_career(cur, player_id) -> dict | None: {gsis_id, display_name, first_season, last_season, seasons
  (REG, newest first), postseason, totals, postseason_totals, awards [{season, award_type}], draft}
- nfl_career_before(cur, player_ids, stats) -> {player_id: {stat: total}} (regular season, linked players)
- nfl_first_seasons(cur, player_ids) -> {player_id: first real season}
- team_nfl_seasons(cur, team_id) -> [team season dict with "awards"], newest first
- nfl_awards_by_season(cur) -> {season: [award rows]}; nfl_champions(cur) -> {season: team row}
- nfl_player_awards(cur) -> [(player_id, season, award_type)] for linked players
"""
from engines.nfl_history.store import NON_SUMMABLE, STAT_COLUMNS

_SUMMABLE = tuple(c for c in STAT_COLUMNS if c not in NON_SUMMABLE)


def _val(row, key, i=0):
    return row[key] if isinstance(row, dict) else row[i]


def _dicts(cur):
    rows = cur.fetchall()
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return [dict(r) for r in rows]
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in rows]


def installed(cur):
    cur.execute("SELECT to_regclass('public.nfl_player_seasons') IS NOT NULL AS ok")
    row = cur.fetchone()
    return bool(row and _val(row, "ok"))


def _num(v):
    if v is None:
        return None
    f = float(v)
    return int(f) if f == int(f) else f


def _totals(rows):
    out = {"games_played": sum(r.get("games_played") or 0 for r in rows)}
    for c in _SUMMABLE:
        vals = [r.get(c) for r in rows if r.get(c) is not None]
        if vals:
            out[c] = _num(sum(vals))
    longs = [r.get("kick_fg_long") for r in rows if r.get("kick_fg_long") is not None]
    if longs:
        out["kick_fg_long"] = max(longs)
    return out


def player_nfl_career(cur, player_id):
    if not installed(cur):
        return None
    cur.execute("""
        SELECT p.gsis_id, p.display_name, p.draft_year, p.draft_round, p.draft_pick, p.draft_team_abbr
        FROM nfl_player_links l JOIN nfl_players p ON p.gsis_id = l.gsis_id
        WHERE l.player_id = %s""", (player_id,))
    people = _dicts(cur)
    if not people:
        return None
    person = people[0]
    gsis = person["gsis_id"]
    cur.execute("""
        SELECT s.*, t.abbreviation AS mu_team
        FROM nfl_player_seasons s LEFT JOIN teams t ON t.id = s.team_id
        WHERE s.gsis_id = %s ORDER BY s.season DESC""", (gsis,))
    rows = [{k: (_num(v) if k == "def_sacks" else v) for k, v in r.items()} for r in _dicts(cur)]
    reg = [r for r in rows if r["season_type"] == "REG"]
    post = [r for r in rows if r["season_type"] == "POST"]
    cur.execute("SELECT season, award_type FROM nfl_awards WHERE gsis_id = %s ORDER BY season, award_type", (gsis,))
    awards = _dicts(cur)
    cur.execute("""
        SELECT d.season, d.round, d.pick, d.overall, d.team_abbr, t.abbreviation AS mu_team
        FROM nfl_draft_picks d LEFT JOIN teams t ON t.id = d.team_id
        WHERE d.gsis_id = %s ORDER BY d.season LIMIT 1""", (gsis,))
    picks = _dicts(cur)
    draft = None
    if picks:
        d = picks[0]
        draft = {"season": d["season"], "round": d["round"], "pick": d["overall"],
                 "team": d["mu_team"] or d["team_abbr"]}
    elif person.get("draft_year"):
        draft = {"season": person["draft_year"], "round": person.get("draft_round"),
                 "pick": person.get("draft_pick"), "team": person.get("draft_team_abbr")}
    seasons = [r["season"] for r in reg] or [r["season"] for r in post]
    return {
        "gsis_id": gsis,
        "display_name": person["display_name"],
        "first_season": min(seasons) if seasons else None,
        "last_season": max(seasons) if seasons else None,
        "seasons": reg,
        "postseason": post,
        "totals": _totals(reg),
        "postseason_totals": _totals(post),
        "awards": awards,
        "draft": draft,
    }


def nfl_career_before(cur, player_ids, stats):
    """{player_id: {stat: regular-season total}} over every real season of each linked player."""
    player_ids = list(player_ids or [])
    stats = [s for s in stats if s in _SUMMABLE or s == "games_played"]
    if not player_ids or not stats or not installed(cur):
        return {}
    sums = ", ".join(f"SUM(s.{c}) AS {c}" for c in stats)
    cur.execute(f"""
        SELECT l.player_id, {sums}
        FROM nfl_player_links l JOIN nfl_player_seasons s ON s.gsis_id = l.gsis_id AND s.season_type = 'REG'
        WHERE l.player_id = ANY(%s) GROUP BY l.player_id""", (player_ids,))
    out = {}
    for r in _dicts(cur):
        out[r["player_id"]] = {c: (_num(r[c]) or 0) for c in stats}
    return out


def nfl_first_seasons(cur, player_ids):
    player_ids = list(player_ids or [])
    if not player_ids or not installed(cur):
        return {}
    cur.execute("""
        SELECT l.player_id, MIN(s.season) AS first_season
        FROM nfl_player_links l JOIN nfl_player_seasons s ON s.gsis_id = l.gsis_id AND s.season_type = 'REG'
        WHERE l.player_id = ANY(%s) GROUP BY l.player_id""", (player_ids,))
    return {r["player_id"]: r["first_season"] for r in _dicts(cur)}


def team_nfl_seasons(cur, team_id):
    if not installed(cur):
        return []
    cur.execute("""
        SELECT season, team_abbr, conference, division, wins, losses, ties, points_for, points_against,
               division_place, conference_seed, playoff_result, playoff_wins, playoff_losses, head_coach,
               head_coach_id, standings_source
        FROM nfl_team_seasons WHERE team_id = %s ORDER BY season DESC""", (team_id,))
    seasons = _dicts(cur)
    cur.execute("""
        SELECT season, award_type, player_name, coach_name, position, gsis_id
        FROM nfl_awards WHERE team_id = %s ORDER BY season, award_type, player_name""", (team_id,))
    by_season = {}
    for a in _dicts(cur):
        by_season.setdefault(a["season"], []).append(a)
    for s in seasons:
        s["awards"] = by_season.get(s["season"], [])
    return seasons


def nfl_awards_by_season(cur):
    if not installed(cur):
        return {}
    cur.execute("""
        SELECT a.season, a.award_type, a.player_name, a.coach_name, a.position, a.team_abbr, a.gsis_id,
               t.abbreviation AS mu_team, l.player_id
        FROM nfl_awards a
        LEFT JOIN teams t ON t.id = a.team_id
        LEFT JOIN nfl_player_links l ON l.gsis_id = a.gsis_id
        ORDER BY a.season DESC, a.award_type, a.player_name""")
    out = {}
    for r in _dicts(cur):
        out.setdefault(r["season"], []).append(r)
    return out


def nfl_champions(cur):
    """{season: {team_abbr, mu_team, team_id, wins, losses, ties, head_coach}} for each real champion."""
    if not installed(cur):
        return {}
    cur.execute("""
        SELECT s.season, s.team_abbr, t.abbreviation AS mu_team, s.team_id, s.wins, s.losses, s.ties, s.head_coach
        FROM nfl_team_seasons s JOIN teams t ON t.id = s.team_id
        WHERE s.playoff_result = 'CHAMPION' ORDER BY s.season DESC""")
    return {r["season"]: r for r in _dicts(cur)}


def nfl_player_awards(cur):
    if not installed(cur):
        return []
    cur.execute("""
        SELECT l.player_id, a.season, a.award_type
        FROM nfl_awards a JOIN nfl_player_links l ON l.gsis_id = a.gsis_id
        ORDER BY l.player_id, a.season""")
    return [(r["player_id"], r["season"], r["award_type"]) for r in _dicts(cur)]
