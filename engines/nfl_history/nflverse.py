"""nflverse parsing and derivations (docs/in_progress/REAL_NFL_HISTORY_PLAN.md §2.3-§2.4). Pure: parsed CSV rows
(dicts of strings) in, plain dicts in the db/migrations/2027_02_04_nfl_history.sql column shapes out.

Team columns (team_id, home_team_id, away_team_id) come out holding the Mu abbreviation (teams.abbreviation of the
current franchise); resolve_team_ids(rows, ids_by_abbr) swaps them for this database's teams.id before a write.

- parse_players(rows)                   players.csv -> nfl_players
- players_from_stats(rows)              minimal nfl_players rows for stat-file players missing from players.csv
- parse_player_seasons(rows, season, season_type, skipped=None)  stats_player_{reg,post}_<season>.csv -> nfl_player_seasons
- parse_games(rows, seasons)            schedules/games.csv -> nfl_games (played games of `seasons`)
- team_seasons(games, standings_rows)   nfl_games rows -> nfl_team_seasons (standings_source NFLDATA or COMPUTED)
- place_and_seed(games, season)         computed division place and conference seed for one season
- coach_seasons(games)                  nfl_games rows -> nfl_coach_seasons
- parse_draft(rows, seasons)            draft_picks.csv -> nfl_draft_picks
- coverage(season_rows)                 {season: players with any tackle} (thin defensive stats before ~2001)
"""
import collections
import datetime as dt

from engines.nfl_history.teams import division_of, mu_abbr

# nflverse stats_player column -> nfl_player_seasons column (direct copies)
NFL_STAT_COLUMNS = {
    "completions": "completions", "attempts": "pass_attempts", "passing_yards": "pass_yards",
    "passing_tds": "pass_tds", "passing_interceptions": "interceptions", "sacks_suffered": "pass_sacks",
    "carries": "rush_attempts", "rushing_yards": "rush_yards", "rushing_tds": "rush_tds",
    "rushing_fumbles_lost": "rush_fumbles_lost", "fumbles_total": "fumb",
    "targets": "rec_targets", "receptions": "receptions", "receiving_yards": "rec_yards",
    "receiving_tds": "rec_tds",
    "def_tackles_solo": "def_solo", "def_tackle_assists": "def_assists", "def_tackles_for_loss": "def_tfl",
    "def_sacks": "def_sacks", "def_interceptions": "def_ints", "def_interception_yards": "def_int_yds",
    "def_pass_defended": "def_deflections", "def_fumbles_forced": "def_forced_fumbles",
    "fumble_recovery_opp": "def_fumble_recoveries", "fumble_recovery_yards_opp": "def_fumble_yds",
    "def_safeties": "def_safeties", "def_tds": "def_tds",
    "fg_made": "kick_fgm", "fg_att": "kick_fga", "fg_blocked": "kick_fg_blocked", "fg_long": "kick_fg_long",
    "pat_made": "kick_xpm", "pat_att": "kick_xpa", "pat_blocked": "kick_xp_blocked",
    "pt_att": "punt_punts", "pt_yards": "punt_yards", "pt_net_yards": "punt_net_yards",
    "pt_inside_20": "punt_inside_20", "pt_touchback": "punt_touchbacks", "pt_blocked": "punt_blocked",
    "kickoff_returns": "kr_returns", "kickoff_return_yards": "kr_yards",
    "punt_returns": "pr_returns", "punt_return_yards": "pr_yards",
    "special_teams_tds": "st_tds",
}
# Madden's field-goal range columns: range -> (made columns, missed columns)
FG_RANGES = {
    "29": (("fg_made_0_19", "fg_made_20_29"), ("fg_missed_0_19", "fg_missed_20_29")),
    "39": (("fg_made_30_39",), ("fg_missed_30_39",)),
    "49": (("fg_made_40_49",), ("fg_missed_40_49",)),
    "50": (("fg_made_50_59", "fg_made_60_"), ("fg_missed_50_59", "fg_missed_60_")),
}
# columns nflverse doesn't split (special_teams_tds is kept whole in st_tds)
ALWAYS_NULL = ("kr_tds", "pr_tds")
GAME_TYPES = {"REG": "REG", "WC": "WC", "DIV": "DIV", "CON": "CON", "SB": "SB"}
PLAYOFF_ROUNDS = ("WC", "DIV", "CON", "SB")
ROUND_LOSS = {"WC": "WILD_CARD", "DIV": "DIVISIONAL", "CON": "CONFERENCE", "SB": "SUPER_BOWL_LOSS"}
TEAM_ID_KEYS = ("team_id", "home_team_id", "away_team_id")
SEVEN_SEED_FIRST = 2020


class ParseError(ValueError):
    pass


# ---------- helpers ----------

def _blank(v):
    return v is None or str(v).strip() in ("", "NA")


def _int(v):
    if _blank(v):
        return None
    return int(round(float(v)))


def _float(v):
    if _blank(v):
        return None
    return round(float(v), 1)


def _text(v):
    return None if _blank(v) else str(v).strip()


def _date(v):
    if _blank(v):
        return None
    try:
        return dt.date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        return None


def height_inches(v):
    """'6-2' -> 74, '74' -> 74, blank -> None."""
    if _blank(v):
        return None
    s = str(v).strip()
    if "-" in s:
        ft, inch = s.split("-", 1)
        return int(ft) * 12 + int(inch)
    return int(round(float(s)))


def resolve_team_ids(rows, ids_by_abbr):
    """Swap Mu abbreviations in TEAM_ID_KEYS for teams.id (in place); returns rows."""
    for r in rows:
        for k in TEAM_ID_KEYS:
            if k in r and isinstance(r[k], str):
                r[k] = ids_by_abbr[r[k]]
    return rows


# ---------- players ----------

def parse_players(rows):
    out = []
    for r in rows:
        gsis = _text(r.get("gsis_id"))
        name = _text(r.get("display_name"))
        if not gsis or not name:
            continue
        out.append({
            "gsis_id": gsis, "espn_id": _text(r.get("espn_id")), "pfr_id": _text(r.get("pfr_id")),
            "display_name": name, "first_name": _text(r.get("first_name")), "last_name": _text(r.get("last_name")),
            "position": _text(r.get("position")), "birth_date": _date(r.get("birth_date")),
            "college": _text(r.get("college_name")), "height_in": height_inches(r.get("height")),
            "weight": _int(r.get("weight")), "rookie_season": _int(r.get("rookie_season")),
            "last_season": _int(r.get("last_season")), "draft_year": _int(r.get("draft_year")),
            "draft_round": _int(r.get("draft_round")), "draft_pick": _int(r.get("draft_pick")),
            "draft_team_abbr": _text(r.get("draft_team")),
        })
    return out


def players_from_stats(rows):
    """Minimal nfl_players rows (gsis_id, display_name, position) from stat-file rows, one per player."""
    seen = {}
    for r in rows:
        gsis = _text(r.get("player_id"))
        if gsis and gsis not in seen:
            seen[gsis] = {"gsis_id": gsis,
                          "display_name": _text(r.get("player_display_name")) or _text(r.get("player_name")) or gsis,
                          "position": _text(r.get("position"))}
    return list(seen.values())


# ---------- player seasons ----------

def parse_player_seasons(rows, season, season_type, skipped=None):
    """A row with no team code raises ParseError, or, when `skipped` is a list, is appended to it and left out
    (nflverse 1999 REG has one: Steve Bono, 1 game)."""
    if season_type not in ("REG", "POST"):
        raise ParseError(f"season_type {season_type!r}")
    out = []
    for r in rows:
        gsis = _text(r.get("player_id"))
        if not gsis:
            continue
        code = _text(r.get("recent_team"))
        if not code:
            if skipped is not None:
                skipped.append({"gsis_id": gsis, "season": int(season), "season_type": season_type,
                                "name": _text(r.get("player_display_name"))})
                continue
            raise ParseError(f"{gsis} {season} {season_type}: no team code")
        row = {"gsis_id": gsis, "season": int(season), "season_type": season_type,
               "team_id": mu_abbr(code, season), "team_abbr": code, "position": _text(r.get("position")),
               "games_played": _int(r.get("games"))}
        for src, col in NFL_STAT_COLUMNS.items():
            row[col] = _float(r.get(src)) if col == "def_sacks" else _int(r.get(src))
        if row["def_solo"] is None and row["def_assists"] is None:
            row["def_tackles"] = None
        else:
            row["def_tackles"] = (row["def_solo"] or 0) + (row["def_assists"] or 0)
        for rng, (made, missed) in FG_RANGES.items():
            m = [_int(r.get(c)) for c in made]
            x = [_int(r.get(c)) for c in missed]
            if all(v is None for v in m + x):
                row[f"kick_fgm_{rng}"] = row[f"kick_fga_{rng}"] = None
            else:
                fgm = sum(v or 0 for v in m)
                row[f"kick_fgm_{rng}"] = fgm
                row[f"kick_fga_{rng}"] = fgm + sum(v or 0 for v in x)
        for col in ALWAYS_NULL:
            row[col] = None
        out.append(row)
    return out


def coverage(season_rows):
    """{season: number of players with any recorded tackle} for the load report (§2.4, R6)."""
    out = collections.Counter()
    for r in season_rows:
        if r.get("season_type", "REG") == "REG" and (r.get("def_tackles") or 0) > 0:
            out[r["season"]] += 1
    return dict(sorted(out.items()))


# ---------- games ----------

def parse_games(rows, seasons):
    seasons = {int(s) for s in seasons}
    out = []
    for r in rows:
        season = _int(r.get("season"))
        if season not in seasons or _blank(r.get("home_score")) or _blank(r.get("away_score")):
            continue
        gt = (r.get("game_type") or "").strip()
        if gt not in GAME_TYPES:
            raise ParseError(f"{r.get('game_id')}: game_type {gt!r}")
        ot = _text(r.get("overtime"))
        out.append({
            "game_id": r["game_id"], "season": season, "week": _int(r.get("week")), "game_type": GAME_TYPES[gt],
            "gameday": _date(r.get("gameday")),
            "home_team_id": mu_abbr(r["home_team"], season), "away_team_id": mu_abbr(r["away_team"], season),
            "home_abbr": r["home_team"], "away_abbr": r["away_team"],
            "home_score": _int(r["home_score"]), "away_score": _int(r["away_score"]),
            "overtime": None if ot is None else ot in ("1", "TRUE", "True", "true"),
            "home_coach": _text(r.get("home_coach")), "away_coach": _text(r.get("away_coach")),
        })
    return out


def _sides(g):
    """[(team, opp, pf, pa, coach, abbr)] for both sides of a game (team = Mu abbreviation)."""
    h, a = g["home_team_id"], g["away_team_id"]
    return [(h, a, g["home_score"], g["away_score"], g.get("home_coach"), g["home_abbr"]),
            (a, h, g["away_score"], g["home_score"], g.get("away_coach"), g["away_abbr"])]


def _record(games):
    """{(season, team): {...}} regular-season and playoff tallies from nfl_games rows."""
    rec = {}
    for g in games:
        for team, opp, pf, pa, coach, abbr in _sides(g):
            r = rec.setdefault((g["season"], team), {
                "w": 0, "l": 0, "t": 0, "pf": 0, "pa": 0, "abbr": abbr, "coaches": collections.Counter(),
                "pw": 0, "pl": 0, "rounds": [], "vs": collections.defaultdict(lambda: [0, 0, 0])})
            if g["game_type"] == "REG":
                r["abbr"] = abbr
                r["pf"] += pf
                r["pa"] += pa
                res = 0 if pf > pa else (1 if pf < pa else 2)
                r[("w", "l", "t")[res]] += 1
                r["vs"][opp][res] += 1
                if coach:
                    r["coaches"][coach] += 1
            else:
                won = pf > pa
                r["pw" if won else "pl"] += 1
                r["rounds"].append((PLAYOFF_ROUNDS.index(g["game_type"]), won))
    return rec


def playoff_result(rounds):
    """MISSED / WILD_CARD / DIVISIONAL / CONFERENCE / SUPER_BOWL_LOSS / CHAMPION from [(round index, won)]."""
    if not rounds:
        return "MISSED"
    idx, won = max(rounds)
    if idx == PLAYOFF_ROUNDS.index("SB") and won:
        return "CHAMPION"
    return ROUND_LOSS[PLAYOFF_ROUNDS[idx]]


def _pct(r):
    g = r["w"] + r["l"] + r["t"]
    return (r["w"] + 0.5 * r["t"]) / g if g else 0.0


def place_and_seed(games, season):
    """Computed {team: (division_place, conference_seed or None)} for one season: division place by win percentage,
    then head-to-head among the tied teams, then net points; seeds from the bracket (teams with a bye first, then
    division winners, then win percentage and net points; six seeds before 2020, seven from 2020)."""
    sg = [g for g in games if g["season"] == season]
    rec = {t: r for (s, t), r in _record(sg).items() if s == season}
    out = {}
    divisions = collections.defaultdict(list)
    for t in rec:
        divisions[division_of(t, season)].append(t)

    def h2h(t, group):
        w = l = ti = 0
        for o in group:
            if o != t:
                vw, vl, vt = rec[t]["vs"].get(o, [0, 0, 0])
                w, l, ti = w + vw, l + vl, ti + vt
        n = w + l + ti
        return (w + 0.5 * ti) / n if n else 0.5

    for div, teams in divisions.items():
        by_pct = collections.defaultdict(list)
        for t in teams:
            by_pct[round(_pct(rec[t]), 6)].append(t)
        ordered = []
        for p in sorted(by_pct, reverse=True):
            group = by_pct[p]
            ordered += sorted(group, key=lambda t: (-h2h(t, group), -(rec[t]["pf"] - rec[t]["pa"]), t))
        for i, t in enumerate(ordered, 1):
            out[t] = [i, None]
    for conf in ("AFC", "NFC"):
        playoff = [t for t in rec if rec[t]["rounds"] and division_of(t, season)[0] == conf]
        bye = {t for t in playoff if min(i for i, _ in rec[t]["rounds"]) > 0}
        playoff.sort(key=lambda t: (t not in bye, out[t][0] != 1, -_pct(rec[t]),
                                    -(rec[t]["pf"] - rec[t]["pa"]), t))
        for seed, t in enumerate(playoff, 1):
            out[t][1] = seed
    return {t: tuple(v) for t, v in out.items()}


def team_seasons(games, standings_rows=None):
    """nfl_team_seasons rows from nfl_games rows. Place and seed come from nfldata standings rows (season, team,
    div_rank, seed) for seasons they cover (standings_source NFLDATA), else from place_and_seed (COMPUTED)."""
    rec = _record(games)
    standings = {}
    for r in standings_rows or ():
        season = _int(r.get("season"))
        try:
            team = mu_abbr(r.get("team"), season)
        except ValueError:
            continue
        standings[(season, team)] = (_int(r.get("div_rank")), _int(r.get("seed")))
    std_seasons = {s for s, _ in standings}
    computed = {}
    out = []
    for (season, team), r in sorted(rec.items()):
        if r["w"] + r["l"] + r["t"] == 0:
            continue
        if season in std_seasons and (season, team) in standings:
            place, seed = standings[(season, team)]
            source = "NFLDATA"
        else:
            if season not in computed:
                computed[season] = place_and_seed(games, season)
            place, seed = computed[season].get(team, (None, None))
            source = "COMPUTED"
        conf, div = division_of(team, season)
        coach = r["coaches"].most_common(1)[0][0] if r["coaches"] else None
        out.append({
            "season": season, "team_id": team, "team_abbr": r["abbr"], "conference": conf, "division": div,
            "wins": r["w"], "losses": r["l"], "ties": r["t"], "points_for": r["pf"], "points_against": r["pa"],
            "division_place": place, "conference_seed": seed, "playoff_result": playoff_result(r["rounds"]),
            "playoff_wins": r["pw"], "playoff_losses": r["pl"], "head_coach": coach, "head_coach_id": None,
            "standings_source": source,
        })
    return out


def coach_seasons(games):
    """nfl_coach_seasons rows: one per (coach, season, team) from the per-game head coach. full_season = he coached
    every regular-season game of the team; playoff columns count the playoff games he coached."""
    team_reg = collections.Counter()
    team_rounds = collections.defaultdict(list)
    rows = {}
    for g in games:
        for team, _opp, pf, pa, coach, _abbr in _sides(g):
            if g["game_type"] == "REG":
                team_reg[(g["season"], team)] += 1
            if not coach:
                continue
            r = rows.setdefault((coach, g["season"], team), {
                "w": 0, "l": 0, "t": 0, "g": 0, "pw": 0, "pl": 0, "rounds": [], "sbw": 0, "sbl": 0})
            if g["game_type"] == "REG":
                r["g"] += 1
                r[("w", "l", "t")[0 if pf > pa else (1 if pf < pa else 2)]] += 1
            else:
                won = pf > pa
                r["pw" if won else "pl"] += 1
                r["rounds"].append((PLAYOFF_ROUNDS.index(g["game_type"]), won))
                if g["game_type"] == "SB":
                    r["sbw" if won else "sbl"] += 1
    out = []
    for (coach, season, team), r in sorted(rows.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0])):
        out.append({
            "coach_name": coach, "season": season, "team_id": team, "coach_id": None,
            "wins": r["w"], "losses": r["l"], "ties": r["t"], "games": r["g"],
            "full_season": r["g"] > 0 and r["g"] == team_reg[(season, team)],
            "playoff_wins": r["pw"], "playoff_losses": r["pl"], "playoff_result": playoff_result(r["rounds"]),
            "super_bowl_wins": r["sbw"], "super_bowl_losses": r["sbl"],
        })
    return out


# ---------- draft ----------

def parse_draft(rows, seasons):
    """nfl_draft_picks rows; nflverse `pick` is the overall pick, `pick` here is the pick within the round."""
    seasons = {int(s) for s in seasons}
    picked = []
    for r in rows:
        season = _int(r.get("season"))
        if season not in seasons:
            continue
        picked.append({
            "season": season, "overall": _int(r.get("pick")), "round": _int(r.get("round")), "pick": None,
            "team_id": mu_abbr(r.get("team"), season), "team_abbr": r.get("team"),
            "gsis_id": _text(r.get("gsis_id")), "pfr_id": _text(r.get("pfr_player_id")),
            "player_name": _text(r.get("pfr_player_name")) or "", "position": _text(r.get("position")),
            "college": _text(r.get("college")),
        })
    first = {}
    for p in picked:
        k = (p["season"], p["round"])
        first[k] = min(first.get(k, p["overall"]), p["overall"])
    for p in picked:
        p["pick"] = p["overall"] - first[(p["season"], p["round"])] + 1
    return sorted(picked, key=lambda p: (p["season"], p["overall"]))
