"""Writes for real NFL history (docs/done/REAL_NFL_HISTORY_PLAN.md §2.8; PROJECT_SPEC §3.3x).

Every function takes a cursor and runs in the caller's transaction; nothing here commits. Rows are plain dicts in
the column shapes of db/migrations/2027_02_04_nfl_history.sql. Column names are checked against the table's known
columns before they reach SQL.

- connect(db): a DSN or a database name; refuses REFUSED_DBS by name.
- upsert_players(cur, rows): nfl_players, never deletes (nfl_player_links points at it).
- replace_kind(cur, kind, first, last, rows): deletes that kind's rows for the season range and inserts the new ones.
  kind 'teams' takes {"team_seasons": [...], "coach_seasons": [...]}; the others take a list.
- record_load(cur, kind, first, last, files, counts, report) -> id of the nfl_history_loads row.
- write_links(cur, links): replaces every nfl_player_links row.
- write_coach_baseline(cur, rows): the 2025 coach_seasons baseline (team_id NULL), upserted on (coach_id, season).
"""
import json

REFUSED_DBS = {"madden", "madden_2036"}

# nfl_player_seasons stat columns (§2.3), in table order; def_sacks is NUMERIC(4,1), the rest INT.
STAT_COLUMNS = (
    "completions", "pass_attempts", "pass_yards", "pass_tds", "interceptions", "pass_sacks",
    "rush_attempts", "rush_yards", "rush_tds", "rush_fumbles_lost", "fumb",
    "rec_targets", "receptions", "rec_yards", "rec_tds",
    "def_tackles", "def_solo", "def_assists", "def_tfl", "def_sacks", "def_ints",
    "def_int_yds", "def_deflections", "def_forced_fumbles", "def_fumble_recoveries", "def_fumble_yds",
    "def_safeties", "def_tds",
    "kick_fgm", "kick_fga", "kick_fg_blocked", "kick_fg_long",
    "kick_fgm_29", "kick_fga_29", "kick_fgm_39", "kick_fga_39",
    "kick_fgm_49", "kick_fga_49", "kick_fgm_50", "kick_fga_50",
    "kick_xpm", "kick_xpa", "kick_xp_blocked",
    "punt_punts", "punt_yards", "punt_net_yards", "punt_inside_20", "punt_touchbacks", "punt_blocked",
    "kr_returns", "kr_yards", "kr_tds", "pr_returns", "pr_yards", "pr_tds", "st_tds",
)
# not summable across seasons (a career "total" of these would be wrong)
NON_SUMMABLE = {"kick_fg_long"}

COLUMNS = {
    "nfl_players": ("gsis_id", "espn_id", "pfr_id", "display_name", "first_name", "last_name", "position",
                    "birth_date", "college", "height_in", "weight", "rookie_season", "last_season", "draft_year",
                    "draft_round", "draft_pick", "draft_team_abbr"),
    "nfl_player_seasons": ("gsis_id", "season", "season_type", "team_id", "team_abbr", "position",
                           "games_played") + STAT_COLUMNS,
    "nfl_games": ("game_id", "season", "week", "game_type", "gameday", "home_team_id", "away_team_id", "home_abbr",
                  "away_abbr", "home_score", "away_score", "overtime", "home_coach", "away_coach"),
    "nfl_team_seasons": ("season", "team_id", "team_abbr", "conference", "division", "wins", "losses", "ties",
                         "points_for", "points_against", "division_place", "conference_seed", "playoff_result",
                         "playoff_wins", "playoff_losses", "head_coach", "head_coach_id", "standings_source"),
    "nfl_coach_seasons": ("coach_name", "season", "team_id", "coach_id", "wins", "losses", "ties", "games",
                          "full_season", "playoff_wins", "playoff_losses", "playoff_result", "super_bowl_wins",
                          "super_bowl_losses"),
    "nfl_awards": ("season", "award_type", "gsis_id", "pfr_id", "player_name", "coach_name", "team_id",
                   "team_abbr", "position"),
    "nfl_draft_picks": ("season", "overall", "round", "pick", "team_id", "team_abbr", "gsis_id", "pfr_id",
                        "player_name", "position", "college"),
    "nfl_player_links": ("player_id", "gsis_id", "method", "score", "evidence"),
}
# kind -> the tables it replaces (all keyed by a `season` column)
KIND_TABLES = {
    "stats": ("nfl_player_seasons",),
    "games": ("nfl_games",),
    "teams": ("nfl_team_seasons", "nfl_coach_seasons"),
    "awards": ("nfl_awards",),
    "draft": ("nfl_draft_picks",),
}
LOAD_KINDS = ("players", "stats", "games", "teams", "awards", "draft", "link", "coach_baseline")
BASELINE_SEASON = 2025


class RefusedDatabase(ValueError):
    pass


def db_name(db):
    """The database name of a DSN (`dbname=x ...` or a URL) or a bare name."""
    s = str(db).strip()
    for part in s.split():
        if part.startswith("dbname="):
            return part.split("=", 1)[1]
    if "://" in s:
        return s.rsplit("/", 1)[-1].split("?", 1)[0]
    return s


def dsn_for(db):
    if "=" in db or "://" in db:
        return db
    return f"host=localhost dbname={db} user=postgres password=postgres"


def connect(db):
    """A psycopg connection to `db` (DSN or name); refuses madden and madden_2036 by name."""
    name = db_name(db)
    if name in REFUSED_DBS:
        raise RefusedDatabase(f"engines.nfl_history never writes to {name!r}")
    import psycopg
    return psycopg.connect(dsn_for(db))


def _insert(cur, table, rows, conflict=""):
    rows = list(rows)
    if not rows:
        return 0
    allowed = COLUMNS[table]
    cols = [c for c in allowed if c in rows[0]]
    unknown = set(rows[0]) - set(allowed)
    if unknown:
        raise ValueError(f"{table}: unknown columns {sorted(unknown)}")
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
           + (f" {conflict}" if conflict else ""))
    cur.executemany(sql, [tuple(_adapt(r.get(c)) for c in cols) for r in rows])
    return len(rows)


def _adapt(v):
    return json.dumps(v) if isinstance(v, (dict, list)) else v


def upsert_players(cur, rows):
    cols = [c for c in COLUMNS["nfl_players"] if c != "gsis_id"]
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
    return _insert(cur, "nfl_players", rows, f"ON CONFLICT (gsis_id) DO UPDATE SET {sets}")


def replace_kind(cur, kind, first, last, rows):
    """Delete `kind`'s rows for seasons first..last, insert `rows`. Returns {table: inserted}."""
    if kind not in KIND_TABLES:
        raise ValueError(f"replace_kind: unknown kind {kind!r}")
    tables = KIND_TABLES[kind]
    if kind == "teams":
        by_table = {"nfl_team_seasons": rows.get("team_seasons", []),
                    "nfl_coach_seasons": rows.get("coach_seasons", [])}
    else:
        by_table = {tables[0]: rows}
    counts = {}
    for table in tables:
        cur.execute(f"DELETE FROM {table} WHERE season BETWEEN %s AND %s", (first, last))
        for r in by_table[table]:
            if not first <= r["season"] <= last:
                raise ValueError(f"{table}: season {r['season']} outside {first}-{last}")
        counts[table] = _insert(cur, table, by_table[table])
    return counts


def record_load(cur, kind, first, last, files, counts, report):
    if kind not in LOAD_KINDS:
        raise ValueError(f"record_load: unknown kind {kind!r}")
    files = [f if isinstance(f, dict) else {"url": f.url, "cache_name": f.cache_name, "sha256": f.sha256,
                                            "bytes": f.bytes} for f in files]
    cur.execute("""
        INSERT INTO nfl_history_loads (kind, first_season, last_season, files, counts, report, finished_at)
        VALUES (%s, %s, %s, %s, %s, %s, now()) RETURNING id""",
                (kind, first, last, json.dumps(files), json.dumps(counts), json.dumps(report, default=str)))
    return cur.fetchone()[0]


def write_links(cur, links):
    """Replace every nfl_player_links row with `links` ({player_id, gsis_id, method, score, evidence})."""
    cur.execute("DELETE FROM nfl_player_links")
    return _insert(cur, "nfl_player_links", links)


def write_coach_baseline(cur, rows):
    """Upsert one coach_seasons row per coach for BASELINE_SEASON with team_id NULL (the real career snapshot)."""
    n = 0
    for r in rows:
        cur.execute("""
            INSERT INTO coach_seasons (coach_id, team_id, season, career_wins, career_losses, career_ties,
                                       winning_seasons, playoff_wins, playoff_losses, super_bowl_wins,
                                       super_bowl_losses)
            VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (coach_id, season) DO UPDATE SET
                team_id = NULL, career_wins = EXCLUDED.career_wins, career_losses = EXCLUDED.career_losses,
                career_ties = EXCLUDED.career_ties, winning_seasons = EXCLUDED.winning_seasons,
                playoff_wins = EXCLUDED.playoff_wins, playoff_losses = EXCLUDED.playoff_losses,
                super_bowl_wins = EXCLUDED.super_bowl_wins, super_bowl_losses = EXCLUDED.super_bowl_losses""",
                    (r["coach_id"], BASELINE_SEASON, r["career_wins"], r["career_losses"], r["career_ties"],
                     r["winning_seasons"], r["playoff_wins"], r["playoff_losses"], r["super_bowl_wins"],
                     r["super_bowl_losses"]))
        n += 1
    return n
