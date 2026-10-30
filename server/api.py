import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import datetime
from typing import Optional
import re

# The site shows the active franchise's database when DATABASE_URL isn't set (db/franchises.py); this has to run
# before the imports below, which read DATABASE_URL at import time.
from db.franchises import export_active_dsn
export_active_dsn()

from engines.gotw_engine import display_excitement
from pipeline.commentary_names import player_name_aliases_exist
from server import debug_api, social_api, napper_api, front_office_api, draft_api, history_api, franchise_api

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


class LazyConnectionPool(ConnectionPool):
    """ConnectionPool that defers opening worker threads until first .connection() or lifespan startup."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("open", False)
        super().__init__(*args, **kwargs)

    def connection(self, timeout=None):
        if self.closed:
            self.open()
        return super().connection(timeout=timeout)


db_pool = LazyConnectionPool(
    conninfo=CONN_INFO,
    min_size=4,
    max_size=20,
    kwargs={"row_factory": dict_row},
    open=False,
)


def season_week(season: Optional[int], week: Optional[int] = None) -> tuple[int, int]:
    """Fills a missing season / week from league_state: the current season, and for it the current week
    (another season's default week is 18, its last regular-season week)."""
    if season is not None and week is not None:
        return season, week
    with db_pool.connection() as conn:
        row = conn.execute("SELECT current_season, current_week FROM league_state WHERE id = 1").fetchone()
    cur_season, cur_week = (row["current_season"], row["current_week"]) if row else (2035, 18)
    season = cur_season if season is None else season
    return season, (week if week is not None else (cur_week if season == cur_season else 18))

# --- On-demand local beat wakeups (team news pages) ---
# A team page view starts the local beat swarm in a background thread so the request never waits on Gemini.
# One attempt per (team, season, week) every LOCAL_WAKEUP_RETRY_SECONDS: covers the run in progress and
# stops repeated page views from re-triggering a wakeup that failed. Set NEWSROOM_AUTO_LOCAL_WAKEUP=0 to disable.
import threading
import time

AUTO_LOCAL_WAKEUP = os.environ.get("NEWSROOM_AUTO_LOCAL_WAKEUP", "1") != "0"
LOCAL_WAKEUP_RETRY_SECONDS = 600
_local_wakeup_lock = threading.Lock()
_local_wakeup_attempts = {}  # (team_id, season, week) -> start time


def start_local_wakeup(team_id: int, season: int, week: int, label: str = "") -> bool:
    """Starts a live local beat wakeup in the background; returns False if one was attempted recently."""
    key = (team_id, season, week)
    now = time.time()
    with _local_wakeup_lock:
        last = _local_wakeup_attempts.get(key)
        if last is not None and now - last < LOCAL_WAKEUP_RETRY_SECONDS:
            return False
        _local_wakeup_attempts[key] = now

    def run():
        try:
            from newsroom.swarm_dispatcher import SwarmDispatcher, build_weekly_assignment_brief
            sd = SwarmDispatcher(debug_mode=False)
            brief = build_weekly_assignment_brief(CONN_INFO, season=season, week=week)
            sd.dispatch_team_local(
                team_id=team_id, brief=brief, season=season, week=week,
                live=True, save_to_db=True, max_workers=5,
            )
        except Exception as e:
            print(f"Auto-wakeup failed for team {label} (#{team_id}): {e}")

    threading.Thread(target=run, name=f"local-wakeup-{team_id}-{season}-{week}", daemon=True).start()
    return True


def maybe_start_team_wakeup(cur, team_id: int, season: int, label: str = "") -> bool:
    """Starts the team's local wakeup (beat portfolio + Agent Wire posts) if this is the current season and the team
    has no 'Local Beat N' article for the current week yet. Used by the team news page and the Agent Wire Team Hub."""
    cur.execute("SELECT current_season, current_week FROM league_state LIMIT 1;")
    st = cur.fetchone()
    curr_season = st["current_season"] if st else 2035
    curr_week = st["current_week"] if st else 18
    if season != curr_season or not AUTO_LOCAL_WAKEUP:
        return False
    cur.execute("""
        SELECT COUNT(*) AS cnt
        FROM news_feed n
        WHERE n.season = %s AND n.week = %s AND n.content_type = 'ARTICLE'
          AND n.team_id = %s AND n.slot LIKE 'Local Beat%%';
    """, (season, curr_week, team_id))
    cnt_row = cur.fetchone()
    if cnt_row and cnt_row["cnt"] > 0:
        return False
    # Runs in the background; the new coverage shows up on a later page load.
    return start_local_wakeup(team_id, season, curr_week, label=label)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if db_pool.closed:
        db_pool.open()
    yield
    if not db_pool.closed:
        db_pool.close()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class RevalidatedStaticFiles(StaticFiles):
    # Headshots get regenerated in place; make browsers revalidate (cheap 304 via ETag) instead of serving stale cached images.
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response

app.include_router(debug_api.router)   # /api/debug/*: the #debug page (state, tables, endpoints, docs, run jobs)
app.include_router(napper_api.router)  # /api/napper/*: Napper interactive fantasy leagues platform
app.include_router(front_office_api.router)  # /api/team/{abbr}/front-office: the Team hub's Front Office tab
app.include_router(draft_api.router)   # /api/draft/*: Draft Central (big board, dossiers, honors, needs, mock, war room)
app.include_router(history_api.router)  # /api/records/*: the #records Record Book
app.include_router(franchise_api.router)  # /api/franchise/*: the #franchise page (depth chart, roster, cap, moves, market, launch)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
os.makedirs(_STATIC_DIR, exist_ok=True)
app.mount("/static", RevalidatedStaticFiles(directory=_STATIC_DIR), name="static")

def normalize_team_abbr(abbr: str) -> str:
    clean = (abbr or "").upper().strip()
    if clean in ("ARI", "CRD"):
        return "AZ"
    if clean in ("WSH", "WTC"):
        return "WAS"
    return clean

def display_team_abbr(abbr: str) -> str:
    clean = (abbr or "").upper().strip()
    if clean == "AZ":
        return "ARI"
    if clean == "WAS":
        return "WSH"
    return clean

@app.get("/api/status")
def get_status():
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_season, current_week FROM league_state LIMIT 1;")
            state = cur.fetchone()
            # When stats were last loaded: the latest capture load (the Stats page's "Updated" time)
            cur.execute("SELECT MAX(loaded_at) AS loaded_at FROM captures WHERE status = 'loaded'")
            loaded = cur.fetchone()
            last_updated = loaded["loaded_at"].isoformat() if loaded and loaded["loaded_at"] else None
            if state:
                return {"season": state['current_season'], "week": state['current_week'], "last_updated": last_updated}
            return {"season": 2035, "week": 18, "last_updated": last_updated}

@app.get("/api/schedule")
def get_schedule(season: Optional[int] = None, week: Optional[int] = None):
    season, week = season_week(season, week)
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT g.id, g.week, g.game_time, g.status, g.home_score, g.away_score, g.excitement_index,
                       g.projected_excitement,
                       t_home.name AS home_name, t_home.abbreviation AS home_abbr, 
                       ts_home.wins AS home_wins, ts_home.losses AS home_losses,
                       ts_home.watchability_index AS home_twi,
                       t_away.name AS away_name, t_away.abbreviation AS away_abbr, 
                       ts_away.wins AS away_wins, ts_away.losses AS away_losses,
                       ts_away.watchability_index AS away_twi
                FROM games g
                JOIN teams t_home ON g.home_team_id = t_home.id
                JOIN teams t_away ON g.away_team_id = t_away.id
                LEFT JOIN team_seasons ts_home ON ts_home.team_id = t_home.id AND ts_home.season = g.season
                LEFT JOIN team_seasons ts_away ON ts_away.team_id = t_away.id AND ts_away.season = g.season
                WHERE g.season = %s AND g.week = %s
            """, (season, week))
            games = cur.fetchall()

            day_order = {'Thu': 0, 'Fri': 1, 'Sat': 2, 'Sun': 3, 'Mon': 4}
            def get_sort_key(game):
                time_str = game.get('game_time')
                if not time_str: return 99999
                m = re.match(r'([A-Za-z]+)\s+(\d+):(\d+)\s+(AM|PM)', time_str, re.IGNORECASE)
                if not m: return 99999
                day = day_order.get(m.group(1).capitalize(), 6)
                hour = int(m.group(2))
                ampm = m.group(4).upper()
                if ampm == 'PM' and hour < 12: hour += 12
                if ampm == 'AM' and hour == 12: hour = 0
                return day * 10000 + hour * 100 + int(m.group(3))

            for g in games:
                g['home_abbr'] = display_team_abbr(g['home_abbr'])
                g['away_abbr'] = display_team_abbr(g['away_abbr'])
                h_twi = float(g.get('home_twi') or 50.0)
                a_twi = float(g.get('away_twi') or 50.0)
                g['home_twi'] = h_twi
                g['away_twi'] = a_twi
                watchability = round((h_twi + a_twi) / 2.0, 2)
                g['watchability'] = watchability
                # Excitement Index on the 70–100 display scale: the final index once played, else the projection
                completed = g.get('status') == 'COMPLETED' and g.get('excitement_index') and float(g['excitement_index']) > 0
                projected = g.pop('projected_excitement', None)
                g['excitement_index'] = display_excitement(g['excitement_index'] if completed else projected)
                g['excitement_projected'] = (not completed) and projected is not None
            return sorted(games, key=get_sort_key)

@app.get("/api/team/{abbr}")
def get_team(abbr: str, season: Optional[int] = None):
    season, _ = season_week(season)
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            # Get Team Info
            cur.execute("""
                SELECT t.id, t.name, t.abbreviation, t.team_color,
                       ts.wins, ts.losses, ts.team_ovr, ts.off_ovr, ts.def_ovr
                FROM teams t
                LEFT JOIN team_seasons ts ON ts.team_id = t.id AND ts.season = %s
                WHERE (t.abbreviation = %s OR t.abbreviation = %s)
            """, (season, abbr.upper(), normalize_team_abbr(abbr)))
            team_info = cur.fetchone()
            if not team_info:
                return {"error": "Team not found"}
            team_info["abbreviation"] = display_team_abbr(team_info["abbreviation"])
            
            # Get Roster: a released player keeps team_id with released_week set (pipeline/load_transactions.py), so he
            # leaves the roster from his release on; a signing elsewhere moves him and clears released_week. The fantasy
            # D/ST row (position DEF, e.g. "Bengals D/ST") isn't a player and stays off it too.
            cur.execute("""
                SELECT p.id, p.full_name as name, p.player_number, p.espn_id, ps.position, ps.years_of_experience, ps.overall_rating
                FROM players p
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                WHERE ps.team_id = %s AND ps.released_week IS NULL AND ps.position IS DISTINCT FROM 'DEF'
                ORDER BY ps.overall_rating DESC
            """, (season, team_info['id']))
            roster = cur.fetchall()
            
            return {
                "team": team_info,
                "roster": roster
            }

@app.get("/api/team/{id}/season_stats/{season}")
def get_team_season_stats(id: int, season: int):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(g.id) as games_played,
                    SUM(ts.score) as points_for,
                    SUM(opp_ts.score) as points_against,
                    SUM(ts.total_offense) as total_yards,
                    SUM(ts.passing_yards) as pass_yards,
                    SUM(ts.rushing_yards) as rush_yards
                FROM team_stats ts
                JOIN teams t ON t.name LIKE '%%' || ts.team_name
                JOIN games g ON g.id = ts.game_id
                JOIN team_stats opp_ts ON opp_ts.game_id = g.id AND opp_ts.id != ts.id
                WHERE t.id = %s AND g.season = %s AND g.status = 'COMPLETED'
            """, (id, season))
            
            stats = cur.fetchone()
            if not stats or stats['games_played'] == 0:
                return {
                    "games_played": 0,
                    "points_per_game": 0,
                    "points_allowed_per_game": 0,
                    "total_yards": 0,
                    "pass_yards_per_game": 0,
                    "rush_yards_per_game": 0
                }
            
            gp = stats['games_played']
            return {
                "games_played": gp,
                "points_per_game": round(stats['points_for'] / gp, 1),
                "points_allowed_per_game": round(stats['points_against'] / gp, 1),
                "total_yards": stats['total_yards'],
                "pass_yards_per_game": round(stats['pass_yards'] / gp, 1),
                "rush_yards_per_game": round(stats['rush_yards'] / gp, 1)
            }

@app.get("/api/team/{abbr}/schedule")
def get_team_schedule(abbr: str, season: Optional[int] = None):
    season, _ = season_week(season)
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            # Get Team ID
            cur.execute("SELECT id FROM teams WHERE (abbreviation = %s OR abbreviation = %s)", (abbr.upper(), normalize_team_abbr(abbr)))
            team_res = cur.fetchone()
            if not team_res:
                return {"error": "Team not found"}
            
            team_id = team_res['id']
            
            cur.execute("""
                SELECT g.id, g.week, g.game_time, g.status, g.home_score, g.away_score, g.home_team_id, g.season,
                       t_home.name AS home_name, t_home.name AS home_team, t_home.abbreviation AS home_abbr, 
                       t_away.name AS away_name, t_away.name AS away_team, t_away.abbreviation AS away_abbr
                FROM games g
                JOIN teams t_home ON g.home_team_id = t_home.id
                JOIN teams t_away ON g.away_team_id = t_away.id
                WHERE g.season = %s AND (g.home_team_id = %s OR g.away_team_id = %s)
                ORDER BY g.week ASC;
            """, (season, team_id, team_id))
            schedule_games = cur.fetchall()
            for g in schedule_games:
                g['home_abbr'] = display_team_abbr(g['home_abbr'])
                g['away_abbr'] = display_team_abbr(g['away_abbr'])
            return schedule_games

@app.get("/api/team/{abbr}/history")
def get_team_history(abbr: str):
    """Season-by-season franchise history (PROJECT_SPEC §3.3j; docs/done/HISTORY_BACKFILL_PLAN.md phase 4): every archived
    season in team_season_results (2026-2034 reconstructed league history, 2035 on from the season archive), the
    current season so far when it isn't archived yet, the head coach, GM, playoff result and the team's award
    winners, plus totals since 2026."""
    season, _ = season_week(None)
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM teams WHERE (abbreviation = %s OR abbreviation = %s)",
                        (abbr.upper(), normalize_team_abbr(abbr)))
            team_res = cur.fetchone()
            if not team_res:
                return {"error": "Team not found"}
            team_id = team_res["id"]
            cur.execute("""
                SELECT r.season, r.wins, r.losses, r.ties, r.points_for, r.points_against, r.division_place,
                       r.conference_seed, r.playoff_result, r.playoff_wins, r.playoff_losses, r.source,
                       c.coach_name, g.gm_name
                FROM team_season_results r
                LEFT JOIN coaches c ON c.id = r.head_coach_id
                LEFT JOIN gm_profiles g ON g.id = r.gm_id
                WHERE r.team_id = %s
                ORDER BY r.season DESC
            """, (team_id,))
            seasons = [dict(r) for r in cur.fetchall()]
            if not any(r["season"] == season for r in seasons):
                cur.execute("""
                    SELECT ts.wins, ts.losses, c.coach_name
                    FROM team_seasons ts
                    LEFT JOIN coach_tenures ct ON ct.team_id = ts.team_id AND ct.start_season <= ts.season
                         AND (ct.end_season IS NULL OR ct.end_season >= ts.season)
                    LEFT JOIN coaches c ON c.id = ct.coach_id
                    WHERE ts.team_id = %s AND ts.season = %s LIMIT 1
                """, (team_id, season))
                cur_row = cur.fetchone()
                if cur_row:
                    seasons.insert(0, {"season": season, "wins": cur_row["wins"], "losses": cur_row["losses"],
                                       "ties": 0, "coach_name": cur_row["coach_name"], "in_progress": True})
            cur.execute("""
                SELECT season, award_type, COALESCE(player_name, coach_name) AS name
                FROM awards WHERE team_id = %s ORDER BY season, award_type
            """, (team_id,))
            awards = {}
            for a in cur.fetchall():
                awards.setdefault(a["season"], []).append({"award": a["award_type"], "name": a["name"]})
            for r in seasons:
                r["history"] = r["season"] < HISTORY_CAPTURED_FROM
                r["awards"] = awards.get(r["season"], [])
            done = [r for r in seasons if not r.get("in_progress")]
            summary = {
                "since": min((r["season"] for r in seasons), default=None),
                "wins": sum(r["wins"] or 0 for r in done), "losses": sum(r["losses"] or 0 for r in done),
                "playoffs": sum(1 for r in done if r.get("playoff_result") not in (None, "MISSED")),
                "titles": [r["season"] for r in done if r.get("playoff_result") == "CHAMPION"],
                "super_bowls": [r["season"] for r in done if r.get("playoff_result") in ("CHAMPION", "SUPER_BOWL_LOSS")],
            }
            return {"seasons": seasons, "summary": summary}


@app.get("/api/team/{abbr}/news")
def get_team_news(abbr: str, season: Optional[int] = None, limit: Optional[int] = None):
    """
    Returns news articles and beat coverage specifically tagged for or assigned to a franchise.
    Uses article_teams join or fallback to news_feed.team_id.
    Opposing teams' local beat reporters are excluded so that only the franchise's own beat coverage
    and general/national coverage appear.
    If limit is None, returns all articles written for that team this season.
    """
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            # 1. Resolve Team
            cur.execute("""
                SELECT id, name, city, abbreviation, team_color
                FROM teams 
                WHERE (abbreviation = %s OR abbreviation = %s);
            """, (abbr.upper(), normalize_team_abbr(abbr)))
            team = cur.fetchone()
            if not team:
                return {"error": f"Team '{abbr}' not found.", "articles": []}

            team_id = team["id"]
            cur.execute("SELECT current_season, current_week FROM league_state LIMIT 1;")
            st = cur.fetchone()
            curr_season = st["current_season"] if st else 2035
            curr_week = st["current_week"] if st else 18
            if season is None:
                season = curr_season

            # 2. Wake the team's local beat for the active week if it hasn't been (see maybe_start_team_wakeup).
            # Local wakeups file every article under the team with slot 'Local Beat N' (national primers use 'Slot 6-8').
            maybe_start_team_wakeup(cur, team_id, season, label=abbr)

            # 3. Query articles tagged with this team via article_teams OR news_feed.team_id
            # Exclude opposing teams' beat reporters (LOCAL_BEAT_REPORTER assigned to a different team)
            cur.execute("""
                SELECT DISTINCT ON (n.id)
                       n.id, n.season, n.week, n.team_id, n.content_type, n.persona,
                       n.article_type, n.slot, n.headline, n.excerpt, n.content, n.created_at,
                       am.display_name, am.handle, am.archetype,
                       COALESCE(at.mention_type, 'PRIMARY') AS mention_type,
                       COALESCE(at.mention_count, 1) AS mention_count,
                       t_author.name AS author_team_name
                FROM news_feed n
                LEFT JOIN teams t_author ON t_author.id = n.team_id
                LEFT JOIN article_teams at ON at.article_id = n.id AND at.team_id = %s
                LEFT JOIN agent_memories am ON (
                    (n.team_id IS NOT NULL AND am.assigned_team_id = n.team_id AND UPPER(am.archetype) = UPPER(n.persona))
                    OR (n.team_id IS NULL AND UPPER(am.archetype) = UPPER(n.persona))
                )
                WHERE n.season = %s
                  AND n.content_type = 'ARTICLE'
                  AND (at.team_id = %s OR n.team_id = %s)
                  AND NOT (n.persona = 'LOCAL_BEAT_REPORTER' AND n.team_id IS DISTINCT FROM %s)
                ORDER BY n.id ASC,
                         (CASE WHEN at.mention_type = 'PRIMARY' THEN 1 
                               WHEN at.mention_type = 'FEATURED' THEN 2 
                               ELSE 3 END) ASC,
                         at.mention_count DESC,
                         n.week DESC;
            """, (team_id, season, team_id, team_id, team_id))
            raw_articles = cur.fetchall()

            # Sort by week DESC, primary mentions first
            raw_articles.sort(key=lambda a: (
                a.get("week") or 0,
                1 if a.get("mention_type") == "PRIMARY" else 0,
                a.get("mention_count") or 0
            ), reverse=True)
            if limit is not None and limit > 0:
                raw_articles = raw_articles[:limit]

            # 3. Fetch all tagged teams and players for these articles
            formatted = []
            if raw_articles:
                art_ids = [a["id"] for a in raw_articles]
                cur.execute("""
                    SELECT at_sub.article_id, t.id AS team_id, t.name, t.city, t.abbreviation, 
                           at_sub.mention_type, at_sub.mention_count
                    FROM article_teams at_sub
                    JOIN teams t ON at_sub.team_id = t.id
                    WHERE at_sub.article_id = ANY(%s)
                    ORDER BY at_sub.mention_type = 'PRIMARY' DESC, at_sub.mention_count DESC;
                """, (art_ids,))
                team_rows = cur.fetchall()
                teams_by_art = {}
                for tr in team_rows:
                    teams_by_art.setdefault(tr["article_id"], []).append({
                        "id": tr["team_id"],
                        "name": tr["name"],
                        "city": tr["city"],
                        "abbr": display_team_abbr(tr["abbreviation"]),
                        "mention_type": tr["mention_type"],
                        "mention_count": tr["mention_count"]
                    })

                for a in raw_articles:
                    art_id = a["id"]
                    author_name = a["display_name"] or a["persona"]
                    if a["persona"] == "LOCAL_BEAT_REPORTER":
                        t_name = a.get("author_team_name") or team["name"]
                        author_name = f"{t_name} Beat Reporter"
                    formatted.append({
                        "id": art_id,
                        "season": a["season"],
                        "week": a["week"],
                        "slot": a["slot"] or "Team Coverage",
                        "article_type": a["article_type"],
                        "headline": a["headline"],
                        "excerpt": a["excerpt"],
                        "author": {
                            "display_name": author_name,
                            "handle": a["handle"] or f"@{a['persona']}",
                            "archetype": a["persona"]
                        },
                        "team_id": a["team_id"],
                        "mention_type": a.get("mention_type"),
                        "mention_count": a.get("mention_count"),
                        "created_at": a["created_at"].isoformat() if a["created_at"] else None,
                        "tagged_teams": teams_by_art.get(art_id, [])
                    })

            return {
                "team": {
                    "id": team["id"],
                    "name": team["name"],
                    "city": team["city"],
                    "abbreviation": display_team_abbr(team["abbreviation"]),
                    "team_color": team["team_color"]
                },
                "season": season,
                "total": len(formatted),
                "articles": formatted
            }


def real_team_stats(row: dict) -> dict:
    """A team_stats row with its estimated values blanked: a field the estimator filled (estimated_fields) is shown
    only when it is exact (exact_fields), the same rule as the writers' box score (newsroom/db_tools.py)."""
    est, exact = set(row.pop("estimated_fields", None) or []), set(row.pop("exact_fields", None) or [])
    for field in est - exact:
        row[field] = None
    # Red-zone TDs are written only by a captured box score (or the estimator); without one, first downs and plays
    # are backfill placeholders (0), not values
    row["box_score_captured"] = row.get("red_zone_tds") is not None and "red_zone_tds" not in est
    if not row["box_score_captured"]:
        row["first_downs"] = None
    row["total_plays"] = None   # never captured
    return row


@app.get("/api/game/{game_id}")
def get_game(game_id: int):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT g.id, g.week, g.season, g.game_time, g.status, g.home_score, g.away_score,
                       t_home.name AS home_name, t_home.abbreviation AS home_abbr, t_home.stadium_name AS home_stadium, t_home.team_color AS home_color, ts_home.wins AS home_wins, ts_home.losses AS home_losses,
                       t_away.name AS away_name, t_away.abbreviation AS away_abbr, t_away.team_color AS away_color, ts_away.wins AS away_wins, ts_away.losses AS away_losses
                FROM games g
                JOIN teams t_home ON g.home_team_id = t_home.id
                JOIN teams t_away ON g.away_team_id = t_away.id
                LEFT JOIN team_seasons ts_home ON ts_home.team_id = t_home.id AND ts_home.season = g.season
                LEFT JOIN team_seasons ts_away ON ts_away.team_id = t_away.id AND ts_away.season = g.season
                WHERE g.id = %s
            """, (game_id,))
            game = cur.fetchone()
            if not game:
                return {"error": "Game not found"}
            
            cur.execute("""
                SELECT ps.*, t.abbreviation as team_abbr
                FROM player_stats ps
                JOIN teams t ON ps.team_id = t.id
                WHERE ps.game_id = %s
                ORDER BY ps.id
            """, (game_id,))
            stats = cur.fetchall()

            cur.execute("""
                SELECT ts.*, t.abbreviation as team_abbr
                FROM team_stats ts
                JOIN teams t ON t.name LIKE '%%' || ts.team_name
                WHERE ts.game_id = %s
            """, (game_id,))
            team_stats = [real_team_stats(r) for r in cur.fetchall()]

            # Defenders, kickers and punters from the game's player-stats capture (games it covered)
            cur.execute("""
                SELECT l.player_id, COALESCE(l.full_name, l.player_name) AS player_name,
                       COALESCE(ps.position, '') AS position, t.abbreviation AS team_abbr,
                       l.def_tackles, l.def_sacks, l.def_tfl, l.def_ints, l.def_deflections, l.def_forced_fumbles,
                       l.def_fumble_recoveries, l.def_tds, l.kick_fgm, l.kick_fga, l.kick_fg_long, l.kick_xpm,
                       l.kick_xpa, l.punt_punts, l.punt_average, l.punt_net_average, l.punt_inside_20
                FROM game_stat_lines l
                JOIN teams t ON t.id = l.team_id
                LEFT JOIN player_seasons ps ON ps.player_id = l.player_id AND ps.season = l.season
                WHERE l.game_id = %s
                  AND (l.def_tackles IS NOT NULL OR l.kick_fga > 0 OR l.kick_xpa > 0 OR l.punt_punts > 0)
            """, (game_id,))
            lines = [{k: (_n(v) if k.startswith(("def_", "kick_", "punt_")) else v) for k, v in r.items()}
                     for r in cur.fetchall()]
            defense = sorted((r for r in lines if r["def_tackles"] is not None),
                             key=lambda r: (-((r["def_sacks"] or 0) + (r["def_ints"] or 0) + (r["def_forced_fumbles"] or 0)
                                              + (r["def_tds"] or 0)), -(r["def_tackles"] or 0)))
            kicking = [r for r in lines if (r["kick_fga"] or 0) > 0 or (r["kick_xpa"] or 0) > 0]
            punting = [r for r in lines if (r["punt_punts"] or 0) > 0]

            return {
                "game": game,
                "stats": stats,
                "team_stats": team_stats,
                "defense": defense,
                "kicking": kicking,
                "punting": punting,
            }

@app.get("/api/seasons")
def get_seasons():
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT season FROM (
                    SELECT season FROM games
                    UNION
                    SELECT season FROM team_seasons
                    UNION
                    SELECT season FROM player_seasons WHERE source <> 'SYNTH'   -- 2026-2034 history waits for phase 4
                ) AS all_seasons
                ORDER BY season DESC
            """)
            seasons = [row["season"] for row in cur.fetchall()]
            return seasons


@app.get("/api/power_rankings")
def get_power_rankings(season: Optional[int] = None, week: Optional[int] = None):
    """Stored weekly Power Rankings (parsed from the POWER_RANKINGS article). Defaults to the current season's latest
    ranked week. Returns an empty `rankings` list when the requested week has none."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            if season is None:
                cur.execute("SELECT current_season FROM league_state LIMIT 1;")
                state = cur.fetchone()
                season = state['current_season'] if state else 2035

            cur.execute("SELECT DISTINCT week FROM power_rankings WHERE season = %s ORDER BY week", (season,))
            available_weeks = [r['week'] for r in cur.fetchall()]
            if week is None:
                week = available_weeks[-1] if available_weeks else None
            empty = {"season": season, "week": week, "available_weeks": available_weeks, "article": None, "rankings": []}
            if week is None or week not in available_weeks:
                return empty

            cur.execute("""
                SELECT pr.rank, pr.previous_rank, pr.movement, pr.record, pr.kicker, pr.blurb, pr.persona, pr.article_id,
                       t.id AS team_id, t.name, t.city, t.abbreviation, t.team_color, t.conference, t.division
                FROM power_rankings pr
                JOIN teams t ON t.id = pr.team_id
                WHERE pr.season = %s AND pr.week = %s
                ORDER BY pr.rank
            """, (season, week))
            rows = cur.fetchall()

            article = None
            if rows and rows[0]['article_id']:
                cur.execute("SELECT id, headline, excerpt, persona, created_at FROM news_feed WHERE id = %s",
                            (rows[0]['article_id'],))
                a = cur.fetchone()
                if a:
                    article = {"id": a['id'], "headline": a['headline'], "excerpt": a['excerpt'],
                               "persona": a['persona'],
                               "created_at": a['created_at'].isoformat() if a['created_at'] else None}

            return {
                "season": season,
                "week": week,
                "available_weeks": available_weeks,
                "article": article,
                "rankings": [{
                    "rank": r['rank'],
                    "previous_rank": r['previous_rank'],
                    "movement": r['movement'],
                    "record": r['record'],
                    "kicker": r['kicker'],
                    "blurb": r['blurb'],
                    "team_id": r['team_id'],
                    "abbr": display_team_abbr(r['abbreviation']),
                    "name": r['name'],
                    "city": r['city'],
                    "team_color": r['team_color'],
                    "conference": r['conference'],
                    "division": r['division'],
                } for r in rows],
            }

@app.get("/api/standings/{season}")
def get_standings(season: int):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, abbreviation, conference, division FROM teams")
            teams = cur.fetchall()
            if is_history_season(cur, season):
                return history_standings(cur, season, teams)

            cur.execute("""
                SELECT home_team_id, away_team_id, home_score, away_score
                FROM games
                WHERE season = %s AND status = 'COMPLETED' AND COALESCE(game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
                ORDER BY week ASC
            """, (season,))
            games = cur.fetchall()

            team_stats = {t['id']: {
                'id': t['id'], 'abbr': display_team_abbr(t['abbreviation']), 'name': t['name'],
                'conference': t['conference'], 'division': t['division'],
                'wins': 0, 'losses': 0, 'ties': 0,
                'confWins': 0, 'confLosses': 0, 'confTies': 0,
                'divWins': 0, 'divLosses': 0, 'divTies': 0,
                'pointsFor': 0, 'pointsAgainst': 0,
                'homeWins': 0, 'homeLosses': 0,
                'awayWins': 0, 'awayLosses': 0,
                'history': []
            } for t in teams}

            for g in games:
                h = g['home_team_id']
                a = g['away_team_id']
                hs = g['home_score']
                as_ = g['away_score']
                
                team_stats[h]['pointsFor'] += hs
                team_stats[h]['pointsAgainst'] += as_
                team_stats[a]['pointsFor'] += as_
                team_stats[a]['pointsAgainst'] += hs
                
                h_conf = team_stats[h].get('conference')
                a_conf = team_stats[a].get('conference')
                h_div = team_stats[h].get('division')
                a_div = team_stats[a].get('division')
                
                same_conf = (h_conf == a_conf and h_conf is not None)
                same_div = (h_div == a_div and h_div is not None)
                
                if hs > as_:
                    team_stats[h]['wins'] += 1
                    team_stats[h]['homeWins'] += 1
                    team_stats[h]['history'].append('W')
                    if same_conf: team_stats[h]['confWins'] += 1
                    if same_div: team_stats[h]['divWins'] += 1
                    
                    team_stats[a]['losses'] += 1
                    team_stats[a]['awayLosses'] += 1
                    team_stats[a]['history'].append('L')
                    if same_conf: team_stats[a]['confLosses'] += 1
                    if same_div: team_stats[a]['divLosses'] += 1
                elif as_ > hs:
                    team_stats[a]['wins'] += 1
                    team_stats[a]['awayWins'] += 1
                    team_stats[a]['history'].append('W')
                    if same_conf: team_stats[a]['confWins'] += 1
                    if same_div: team_stats[a]['divWins'] += 1
                    
                    team_stats[h]['losses'] += 1
                    team_stats[h]['homeLosses'] += 1
                    team_stats[h]['history'].append('L')
                    if same_conf: team_stats[h]['confLosses'] += 1
                    if same_div: team_stats[h]['divLosses'] += 1
                else:
                    team_stats[h]['ties'] += 1
                    team_stats[a]['ties'] += 1
                    team_stats[h]['history'].append('T')
                    team_stats[a]['history'].append('T')
                    if same_conf:
                        team_stats[h]['confTies'] += 1
                        team_stats[a]['confTies'] += 1
                    if same_div:
                        team_stats[h]['divTies'] += 1
                        team_stats[a]['divTies'] += 1

            for t in team_stats.values():
                hist = t['history']
                # Streak
                if not hist:
                    t['streak'] = '0'
                else:
                    last = hist[-1]
                    count = 0
                    for res in reversed(hist):
                        if res == last:
                            count += 1
                        else:
                            break
                    t['streak'] = f"{count}{last}"
                
                # Last 5
                l5 = hist[-5:]
                w = l5.count('W')
                l = l5.count('L')
                t_ties = l5.count('T')
                if t_ties > 0:
                    t['lastFive'] = f"{w}-{l}-{t_ties}"
                else:
                    t['lastFive'] = f"{w}-{l}"

            return list(team_stats.values())



@app.get("/api/player/{id}")
def get_player_api(id: int, season: Optional[int] = None):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            if not season:
                cur.execute("SELECT current_season FROM league_state LIMIT 1;")
                st_row = cur.fetchone()
                season = st_row["current_season"] if st_row and st_row.get("current_season") else 2035
            # Query base player
            cur.execute("""
                SELECT p.id, p.full_name as name, p.player_number, p.height, p.weight, p.espn_id
                FROM players p
                WHERE p.id = %s
            """, (id,))
            base_player = cur.fetchone()
            if not base_player:
                return {"error": "Player not found"}

            # Query player season info for requested season
            cur.execute("""
                SELECT ps.position, ps.years_of_experience, ps.overall_rating,
                       t.id as team_id, t.name as team_name, t.abbreviation as team_abbr, t.team_color
                FROM player_seasons ps
                JOIN teams t ON t.id = ps.team_id
                WHERE ps.player_id = %s AND ps.season = %s
            """, (id, season))
            ps_row = cur.fetchone()

            # If not in player_seasons for this season, check season_stats
            if not ps_row:
                cur.execute("""
                    SELECT ss.position, NULL as years_of_experience, NULL as overall_rating,
                           t.id as team_id, t.name as team_name, t.abbreviation as team_abbr, t.team_color
                    FROM season_stats ss
                    JOIN teams t ON t.id = ss.team_id
                    WHERE ss.player_id = %s AND ss.season = %s
                    LIMIT 1
                """, (id, season))
                ps_row = cur.fetchone()

            # If still not found for this season, fallback to player's most recent season
            if not ps_row:
                cur.execute("""
                    SELECT ps.position, ps.years_of_experience, ps.overall_rating,
                           t.id as team_id, t.name as team_name, t.abbreviation as team_abbr, t.team_color
                    FROM player_seasons ps
                    JOIN teams t ON t.id = ps.team_id
                    WHERE ps.player_id = %s
                    ORDER BY ps.season DESC
                    LIMIT 1
                """, (id,))
                ps_row = cur.fetchone()

            player = dict(base_player)
            if ps_row:
                player.update(dict(ps_row))
            else:
                player.update({
                    "position": "ATH",
                    "years_of_experience": 0,
                    "overall_rating": 70,
                    "team_id": None,
                    "team_name": "Free Agent",
                    "team_abbr": "FA",
                    "team_color": "#e31837"
                })

            raw_games = []
            if player.get("team_id"):
                cur.execute("""
                    WITH weekly_ranks AS (
                        SELECT g.season, g.week, st.player_id, ps.position,
                               (ps.position || DENSE_RANK() OVER (PARTITION BY g.season, g.week, ps.position ORDER BY st.fantasy_points DESC)) as weekly_pos_rank
                        FROM games g
                        JOIN player_stats st ON st.game_id = g.id
                        LEFT JOIN player_seasons ps ON ps.season = g.season AND ps.player_id = st.player_id
                        WHERE g.season = %s AND g.status = 'COMPLETED'
                    )
                    SELECT g.week, g.status, g.home_team_id, g.away_team_id, g.home_score, g.away_score,
                           t_home.name as home_name, t_home.abbreviation as home_abbr,
                           t_away.name as away_name, t_away.abbreviation as away_abbr,
                           st.fantasy_points,
                           wr.weekly_pos_rank,
                           st.completions, st.pass_attempts, st.pass_yards, st.pass_tds, st.interceptions,
                           st.rush_attempts, st.rush_yards, st.rush_tds,
                           st.receptions, st.rec_yards, st.rec_tds, st.fumb,
                           gl.def_tackles, gl.def_sacks, gl.def_tfl, gl.def_ints, gl.def_deflections, gl.def_forced_fumbles,
                           gl.def_fumble_recoveries, gl.def_tds, gl.kick_fgm, gl.kick_fga, gl.kick_fg_long, gl.kick_xpm,
                           gl.kick_xpa, gl.punt_punts, gl.punt_average, gl.punt_net_average, gl.punt_inside_20,
                           gl.kr_returns, gl.kr_yards, gl.pr_returns, gl.pr_yards, gl.blk_sacks_allowed,
                           (gl.player_id IS NOT NULL) AS has_capture_line
                    FROM games g
                    LEFT JOIN player_stats st ON st.game_id = g.id AND st.player_id = %s
                    LEFT JOIN game_stat_lines gl ON gl.game_id = g.id AND gl.player_id = %s
                    LEFT JOIN weekly_ranks wr ON wr.season = g.season AND wr.week = g.week AND wr.player_id = st.player_id
                    JOIN teams t_home ON t_home.id = g.home_team_id
                    JOIN teams t_away ON t_away.id = g.away_team_id
                    WHERE g.season = %s AND (g.home_team_id = %s OR g.away_team_id = %s)
                    ORDER BY g.week
                """, (season, id, id, season, player['team_id'], player['team_id']))
                raw_games = cur.fetchall()

            # Build all 18 weeks, inserting BYE week if team had a bye
            game_map = {g['week']: g for g in raw_games}
            games = []
            for w in range(1, 19):
                if w in game_map:
                    games.append(game_map[w])
                else:
                    games.append({
                        "week": w,
                        "status": "BYE",
                        "home_team_id": None,
                        "away_team_id": None,
                        "home_score": None,
                        "away_score": None,
                        "home_name": "BYE",
                        "home_abbr": "BYE",
                        "away_name": "BYE",
                        "away_abbr": "BYE",
                        "fantasy_points": None,
                        "weekly_pos_rank": None,
                        "completions": None,
                        "pass_attempts": None,
                        "pass_yards": None,
                        "pass_tds": None,
                        "interceptions": None,
                        "rush_attempts": None,
                        "rush_yards": None,
                        "rush_tds": None,
                        "receptions": None,
                        "rec_yards": None,
                        "rec_tds": None,
                        "fumb": None
                    })

            cur.execute("""
                WITH season_overall_ranks AS (
                    SELECT ps.season, ps.player_id,
                           COALESCE(SUM(st.fantasy_points), 0) as total_fpts,
                           DENSE_RANK() OVER (PARTITION BY ps.season ORDER BY COALESCE(SUM(st.fantasy_points), 0) DESC) as overall_rank
                    FROM player_seasons ps
                    JOIN players p ON p.id = ps.player_id
                    JOIN games g ON g.season = ps.season AND (g.home_team_id = ps.team_id OR g.away_team_id = ps.team_id)
                    JOIN player_stats st ON st.game_id = g.id AND st.team_id = ps.team_id AND st.player_id = ps.player_id
                    WHERE g.status = 'COMPLETED'
                    GROUP BY ps.season, ps.player_id
                )
                SELECT ps.season, t.name as team_name, t.abbreviation as team_abbr,
                       COUNT(st.id) as gp,
                       COALESCE(sor.total_fpts, 0) as total_fpts,
                       COALESCE(sor.overall_rank, 1) as overall_rank,
                       SUM(st.completions) as completions, SUM(st.pass_attempts) as pass_attempts,
                       SUM(st.pass_yards) as pass_yards, SUM(st.pass_tds) as pass_tds, SUM(st.interceptions) as interceptions,
                       SUM(st.rush_attempts) as rush_attempts, SUM(st.rush_yards) as rush_yards, SUM(st.rush_tds) as rush_tds,
                       SUM(st.receptions) as receptions, SUM(st.rec_yards) as rec_yards, SUM(st.rec_tds) as rec_tds
                FROM player_seasons ps
                JOIN teams t ON t.id = ps.team_id
                LEFT JOIN season_overall_ranks sor ON sor.season = ps.season AND sor.player_id = ps.player_id
                LEFT JOIN games g ON g.season = ps.season AND (g.home_team_id = ps.team_id OR g.away_team_id = ps.team_id)
                LEFT JOIN player_stats st ON st.game_id = g.id AND st.player_id = ps.player_id AND st.team_id = ps.team_id
                WHERE ps.player_id = %s AND ps.source <> 'SYNTH'    -- generated seasons come from season_stat_lines below
                GROUP BY ps.season, t.name, t.abbreviation, sor.total_fpts, sor.overall_rank
                ORDER BY ps.season DESC
            """, (id,))
            career = [dict(r) for r in cur.fetchall()]

            # Merge historical seasons from season_stats (e.g. 2034, 2033, 2032) into career_stats
            cur.execute("""
                SELECT ss.season, t.name as team_name, t.abbreviation as team_abbr,
                       17 as gp,
                       COALESCE(ss.total_fantasy_points, 0) as total_fpts,
                       1 as overall_rank,
                       ss.completions, ss.pass_attempts, ss.pass_yards, ss.pass_tds, ss.interceptions,
                       ss.rush_attempts, ss.rush_yards, ss.rush_tds,
                       ss.receptions, ss.rec_yards, ss.rec_tds
                FROM season_stats ss
                JOIN teams t ON t.id = ss.team_id
                WHERE ss.player_id = %s AND ss.source <> 'SYNTH'
                ORDER BY ss.season DESC
            """, (id,))
            ss_rows = [dict(r) for r in cur.fetchall()]
            career_by_season = {c["season"]: c for c in career}
            for sr in ss_rows:
                s_yr = sr["season"]
                if s_yr not in career_by_season:
                    career.append(sr)
                    career_by_season[s_yr] = sr
                else:
                    target = career_by_season[s_yr]
                    for field in ["completions", "pass_attempts", "pass_yards", "pass_tds", "interceptions",
                                  "rush_attempts", "rush_yards", "rush_tds", "receptions", "rec_yards", "rec_tds"]:
                        if not target.get(field) and sr.get(field):
                            target[field] = sr[field]
                    if not target.get("total_fpts") and sr.get("total_fpts"):
                        target["total_fpts"] = sr["total_fpts"]
            career.sort(key=lambda x: x["season"], reverse=True)

            # Defense, kicking, punting, returns and pass protection from the Season Stats capture (listed players only)
            cur.execute("""
                SELECT season, games_played, def_tackles, def_solo, def_assists, def_sacks, def_tfl, def_ints,
                       def_int_yds, def_deflections, def_forced_fumbles, def_fumble_recoveries, def_tds, def_safeties,
                       kick_fgm, kick_fga, kick_fg_long, kick_xpm, kick_xpa, kick_fgm_50, kick_fga_50,
                       punt_punts, punt_yards, punt_average, punt_net_average, punt_inside_20, punt_longest,
                       kr_returns, kr_yards, kr_average, kr_tds, pr_returns, pr_yards, pr_average, pr_tds,
                       blk_sacks_allowed
                FROM season_stat_lines WHERE player_id = %s AND source <> 'SYNTH'
            """, (id,))
            lines = {r["season"]: r for r in cur.fetchall()}
            # 2026-2034 league history (docs/done/HISTORY_BACKFILL_PLAN.md phase 4): every stat group of a generated season
            # comes from season_stat_lines; a seeded canon season (the Panthers' 2032-2034 lines) keeps its own
            # offense and takes the rest from the generated row. Both read "history" on the page.
            cur.execute("""
                SELECT l.season, t.name AS team_name, t.abbreviation AS team_abbr, l.games_played AS gp,
                       l.completions, l.pass_attempts, l.pass_yards, l.pass_tds, l.interceptions,
                       l.rush_attempts, l.rush_yards, l.rush_tds, l.receptions, l.rec_yards, l.rec_tds,
                       l.def_tackles, l.def_solo, l.def_assists, l.def_sacks, l.def_tfl, l.def_ints, l.def_int_yds,
                       l.def_deflections, l.def_forced_fumbles, l.def_fumble_recoveries, l.def_tds, l.def_safeties,
                       l.kick_fgm, l.kick_fga, l.kick_xpm, l.kick_xpa, l.punt_punts, l.punt_yards, l.punt_average,
                       l.punt_net_average, l.punt_inside_20, l.kr_returns, l.kr_yards, l.pr_returns, l.pr_yards,
                       l.blk_sacks_allowed
                FROM season_stat_lines l JOIN teams t ON t.id = l.team_id
                WHERE l.player_id = %s AND l.source = 'SYNTH'
            """, (id,))
            for h in cur.fetchall():
                h = {k: (_n(v) if k not in ("season", "team_name", "team_abbr", "gp") else v) for k, v in h.items()}
                row = career_by_season.get(h["season"])
                if row is None:
                    row = {"season": h["season"], "total_fpts": 0, "overall_rank": None}
                    career.append(row)
                    career_by_season[h["season"]] = row
                for k, v in h.items():
                    if v is not None and not row.get(k):
                        row[k] = v
                row["history"] = True
                row["has_capture_line"] = True
            for c in career:
                if c["season"] < HISTORY_CAPTURED_FROM:
                    c["history"] = True
            career.sort(key=lambda x: x["season"], reverse=True)
            for c in career:
                line = lines.get(c["season"])
                c["has_capture_line"] = line is not None
                if line:
                    for k, v in line.items():
                        if k != "season":
                            c[k] = _n(v) if k != "games_played" else v
                    if line.get("games_played"):
                        c["gp"] = line["games_played"]
            for g in games:
                for k in [k for k in g if k.startswith(("def_", "kick_", "punt_", "kr_", "pr_", "blk_"))]:
                    g[k] = _n(g[k])

            # Weekly matchup & opponent quality intelligence
            cur.execute("SELECT current_week FROM league_state LIMIT 1;")
            state_row = cur.fetchone()
            curr_week = state_row['current_week'] if state_row else 18

            cur.execute("""
                SELECT g.id, g.week, g.game_time, g.status,
                       t_home.id as home_team_id, t_home.name as home_name, t_home.abbreviation as home_abbr,
                       t_away.id as away_team_id, t_away.name as away_name, t_away.abbreviation as away_abbr
                FROM games g
                JOIN teams t_home ON t_home.id = g.home_team_id
                JOIN teams t_away ON t_away.id = g.away_team_id
                WHERE g.season = %s AND (g.home_team_id = %s OR g.away_team_id = %s) AND g.week = %s
                LIMIT 1;
            """, (season, player['team_id'], player['team_id'], curr_week))
            curr_game = cur.fetchone()

            on_bye = False
            if not curr_game:
                cur.execute("SELECT 1 FROM games WHERE season = %s AND week = %s LIMIT 1;", (season, curr_week))
                # The league plays this week but the player's team doesn't: a bye.
                on_bye = cur.fetchone() is not None
            if not curr_game and not on_bye:
                # The season has no games in the league's current week (e.g. a past season): use its last game
                cur.execute("""
                    SELECT g.id, g.week, g.game_time, g.status,
                           t_home.id as home_team_id, t_home.name as home_name, t_home.abbreviation as home_abbr,
                           t_away.id as away_team_id, t_away.name as away_name, t_away.abbreviation as away_abbr
                    FROM games g
                    JOIN teams t_home ON t_home.id = g.home_team_id
                    JOIN teams t_away ON t_away.id = g.away_team_id
                    WHERE g.season = %s AND (g.home_team_id = %s OR g.away_team_id = %s)
                    ORDER BY g.week DESC
                    LIMIT 1;
                """, (season, player['team_id'], player['team_id']))
                curr_game = cur.fetchone()

            outlook_week = curr_game['week'] if curr_game else curr_week
            # Stored projection (engines/fantasy_projection_engine.py) for the outlook week
            cur.execute("""
                SELECT projected_points, avg_points, matchup_stars, pos_rank, ros_rank, ros_total_points
                FROM fantasy_projections
                WHERE season = %s AND week = %s AND player_id = %s;
            """, (season, outlook_week, id))
            proj_record = cur.fetchone()

            weekly_outlook = None
            if on_bye:
                weekly_outlook = {
                    "week": curr_week,
                    "bye": True,
                    "projected": True,
                    "ecr_rank": None,
                    "pos_rank": None,
                    "ros_rank": proj_record['ros_rank'] if proj_record else None,
                    "projection": "0.0",
                    "stars": None,
                }
            elif curr_game:
                is_home = curr_game['home_team_id'] == player['team_id']
                opp_team_id = curr_game['away_team_id'] if is_home else curr_game['home_team_id']
                opp_name = curr_game['away_name'] if is_home else curr_game['home_name']
                opp_abbr = curr_game['away_abbr'] if is_home else curr_game['home_abbr']

                if proj_record and proj_record.get('projected_points') is not None:
                    projected = True
                    proj_pts = float(proj_record['projected_points'])
                    stars = int(proj_record['matchup_stars']) if proj_record['matchup_stars'] else None
                    pos_rank_label = proj_record['pos_rank']
                    ros_rank_label = proj_record['ros_rank']
                else:
                    # No stored projection (e.g. week 1, or a player the engine doesn't cover): rough estimate, no ranks
                    projected = False
                    cur.execute("""
                        SELECT matchup_stars
                        FROM vw_fantasy_defense_vs_position
                        WHERE season = %s AND def_team_id = %s AND position = %s;
                    """, (season, opp_team_id, player['position']))
                    def_stat = cur.fetchone()
                    stars = int(def_stat['matchup_stars']) if def_stat and def_stat['matchup_stars'] else 3
                    completed_games = [g for g in games if g['status'] == 'COMPLETED']
                    avg_pts = (sum([float(g.get('fantasy_points') or 0.0) for g in completed_games]) / len(completed_games)) if completed_games else 16.5
                    multiplier = 0.85 + (stars - 1) * 0.075
                    proj_pts = round(avg_pts * multiplier, 1)
                    pos_rank_label = None
                    ros_rank_label = None

                weekly_outlook = {
                    "week": curr_game['week'],
                    "bye": False,
                    "projected": projected,
                    "kickoff": curr_game['game_time'] or "SUN 1:00 PM ET",
                    "is_home": is_home,
                    "home_name": curr_game['home_name'],
                    "home_abbr": display_team_abbr(curr_game['home_abbr']),
                    "away_name": curr_game['away_name'],
                    "away_abbr": display_team_abbr(curr_game['away_abbr']),
                    "opp_name": opp_name,
                    "opp_abbr": display_team_abbr(opp_abbr),
                    "ecr_rank": pos_rank_label,
                    "pos_rank": pos_rank_label,
                    "ros_rank": ros_rank_label,
                    "projection": f"{proj_pts:.1f}",
                    "stars": stars
                }

            if player:
                player['team_abbr'] = display_team_abbr(player['team_abbr'])
            for g in games:
                g['home_abbr'] = display_team_abbr(g['home_abbr'])
                g['away_abbr'] = display_team_abbr(g['away_abbr'])
            for c in career:
                c['team_abbr'] = display_team_abbr(c['team_abbr'])
            # The player's coverage: newsroom articles tagged with him (article_players), newest first
            # Exclude opposing teams' beat reporters (only allow player's own team beat or national/general writers)
            player_team_id = player.get('team_id') if player else None
            cur.execute("""
                SELECT DISTINCT ON (nf.id) nf.id, nf.season, nf.week, nf.article_type, nf.headline, nf.excerpt,
                       nf.persona, nf.created_at, ap.mention_type, ap.mention_count, am.display_name, am.handle,
                       t_auth.name AS author_team_name
                FROM article_players ap
                JOIN news_feed nf ON nf.id = ap.article_id AND nf.content_type = 'ARTICLE'
                LEFT JOIN teams t_auth ON t_auth.id = nf.team_id
                LEFT JOIN agent_memories am ON (
                    (nf.team_id IS NOT NULL AND am.assigned_team_id = nf.team_id AND UPPER(am.archetype) = UPPER(nf.persona))
                    OR (nf.team_id IS NULL AND UPPER(am.archetype) = UPPER(nf.persona))
                )
                WHERE ap.player_id = %s
                  AND NOT (nf.persona = 'LOCAL_BEAT_REPORTER' AND nf.team_id IS DISTINCT FROM %s)
                ORDER BY nf.id, am.assigned_team_id NULLS FIRST, am.id
            """, (id, player_team_id))
            arts = sorted(cur.fetchall(), key=lambda a: (a["season"], a["week"], a["mention_type"] == "PRIMARY",
                                                         a["mention_count"] or 0, a["id"]), reverse=True)[:6]
            # Short labels: "The Weekly Slate Lookahead (ESPN ...)" -> "Weekly Slate Lookahead"
            type_names = {k: re.sub(r"^The\s+|\s*\(.*\)\s*$", "", v.get("name", k))
                          for k, v in _load_config("article_types.json", "article_types").items()}
            articles = [{
                "id": a["id"], "season": a["season"], "week": a["week"], "headline": a["headline"],
                "excerpt": a["excerpt"],
                "label": type_names.get(a["article_type"], (a["article_type"] or "Article").replace("_", " ").title()),
                "author": (f"{a['author_team_name']} Beat Reporter" if a["persona"] == "LOCAL_BEAT_REPORTER" and a.get("author_team_name")
                           else a["display_name"] or (a["persona"] or "").replace("_", " ").title()),
                "author_handle": a["handle"] or f"@{a['persona']}",
                "mention": a["mention_type"],
            } for a in arts]

            pos = player["position"]
            stat_group = ("passing" if pos == "QB" else "rushing" if pos in ("HB", "FB") else
                          "receiving" if pos in ("WR", "TE") else "kicking" if pos == "K" else
                          "punting" if pos in ("P", "LS") else "blocking" if pos in ("LT", "LG", "C", "RG", "RT")
                          else "defense")
            return {
                "player": player,
                "stat_group": stat_group,
                "articles": articles,
                "awards": history_api.fetch_player_awards(cur, id),
                "game_log": games,
                "career_stats": career,
                "career_totals": career_totals(career),
                "career_path": _career_path(conn, id),
                "weekly_outlook": weekly_outlook
            }


def calculate_passer_rating(completions, attempts, yards, touchdowns, interceptions):
    if not attempts or attempts == 0:
        return 0.0
    a = ((completions / attempts) - 0.3) * 5
    b = ((yards / attempts) - 3) * 0.25
    c = (touchdowns / attempts) * 20
    d = 2.375 - ((interceptions / attempts) * 25)
    
    a = max(0.0, min(a, 2.375))
    b = max(0.0, min(b, 2.375))
    c = max(0.0, min(c, 2.375))
    d = max(0.0, min(d, 2.375))
    
    rating = ((a + b + c + d) / 6) * 100
    return round(rating, 1)

# ─── Defense & special teams (Season Stats capture: season_stat_lines / game_stat_lines) ───
# Madden lists only its leaders in each category (defenders by tackles), so these tables rank listed players.
HISTORY_CAPTURED_FROM = 2035     # seasons before this are reconstructed league history (B15: canon)
CAREER_SUM = ("gp", "completions", "pass_attempts", "pass_yards", "pass_tds", "interceptions", "rush_attempts",
              "rush_yards", "rush_tds", "receptions", "rec_yards", "rec_tds", "def_tackles", "def_solo", "def_sacks",
              "def_tfl", "def_ints", "def_deflections", "def_forced_fumbles", "def_fumble_recoveries", "def_tds",
              "kick_fgm", "kick_fga", "kick_xpm", "kick_xpa", "punt_punts", "punt_yards", "punt_inside_20",
              "blk_sacks_allowed")


def is_history_season(cur, season) -> bool:
    """A 2026-2034 season: reconstructed league history (team_season_results SYNTH), no regular-season games kept."""
    if season is None or season >= HISTORY_CAPTURED_FROM:
        return False
    cur.execute("SELECT 1 FROM team_season_results WHERE season = %s AND source = 'SYNTH' LIMIT 1", (season,))
    return cur.fetchone() is not None


def history_standings(cur, season, teams):
    """Standings for a history season from team_season_results: record, points, division place, seed and playoff
    result. Splits, streaks and last-five need games, so they're None."""
    cur.execute("""SELECT team_id, wins, losses, ties, points_for, points_against, division_place, conference_seed,
                          playoff_result FROM team_season_results WHERE season = %s""", (season,))
    res = {r["team_id"]: r for r in cur.fetchall()}
    out = []
    for t in teams:
        r = res.get(t["id"])
        if not r:
            continue
        out.append({
            "id": t["id"], "abbr": display_team_abbr(t["abbreviation"]), "name": t["name"],
            "conference": t["conference"], "division": t["division"],
            "wins": r["wins"], "losses": r["losses"], "ties": r["ties"],
            "pointsFor": r["points_for"], "pointsAgainst": r["points_against"],
            "confWins": None, "confLosses": None, "confTies": None, "divWins": None, "divLosses": None,
            "divTies": None, "homeWins": None, "homeLosses": None, "awayWins": None, "awayLosses": None,
            "streak": None, "lastFive": None, "isHistory": True,   # "history" is the W/L list in game standings
            "divisionPlace": r["division_place"], "seed": r["conference_seed"], "playoffResult": r["playoff_result"],
        })
    return out


HISTORY_OFFENSE_SQL = """
    SELECT p.id, p.full_name AS name, p.player_number, l.position, t.name AS team_name, t.abbreviation AS team_abbr,
           COALESCE(l.pass_attempts, 0) AS att, COALESCE(l.completions, 0) AS cmp, COALESCE(l.pass_yards, 0) AS pass_yds,
           COALESCE(l.pass_tds, 0) AS pass_td, COALESCE(l.interceptions, 0) AS ints,
           COALESCE(l.rush_attempts, 0) AS rush_att, COALESCE(l.rush_yards, 0) AS rush_yds,
           COALESCE(l.rush_tds, 0) AS rush_td, COALESCE(l.fumb, 0) AS fum,
           COALESCE(l.receptions, 0) AS rec, COALESCE(l.rec_yards, 0) AS rec_yds, COALESCE(l.rec_tds, 0) AS rec_td
    FROM season_stat_lines l JOIN players p ON p.id = l.player_id JOIN teams t ON t.id = l.team_id
    WHERE l.season = %s
"""


def history_offense_leaders(cur, season, cat):
    """Passing, rushing, receiving and scoring leaders for a history season, from its season lines (no game lines
    exist). Same shape as the game-line leaderboards."""
    cur.execute(HISTORY_OFFENSE_SQL, (season,))
    rows = cur.fetchall()
    out = []
    for r in rows:
        if cat == "passing" and r["att"] > 0:
            att, cmp, yds, td, ints = r["att"], r["cmp"], r["pass_yds"], r["pass_td"], r["ints"]
            stats = {"ATT": att, "CMP": cmp, "CMP %": f"{100 * cmp / att:.1f}", "YDS/ATT": f"{yds / att:.1f}",
                     "PASS YDS": yds, "TD": td, "INT": ints,
                     "RATE": f"{calculate_passer_rating(cmp, att, yds, td, ints):.1f}"}
            key = yds
        elif cat == "rushing" and r["rush_att"] > 0:
            stats = {"ATT": r["rush_att"], "YDS": r["rush_yds"], "AVG": f"{r['rush_yds'] / r['rush_att']:.1f}",
                     "TD": r["rush_td"], "FUM": r["fum"]}
            key = r["rush_yds"]
        elif cat == "receiving" and r["rec"] > 0:
            stats = {"REC": r["rec"], "YDS": r["rec_yds"], "AVG": f"{r['rec_yds'] / r['rec']:.1f}", "TD": r["rec_td"]}
            key = r["rec_yds"]
        elif cat == "scoring" and (r["pass_td"] + r["rush_td"] + r["rec_td"]) > 0:
            total = r["pass_td"] + r["rush_td"] + r["rec_td"]
            stats = {"PASSING TD": r["pass_td"], "RUSHING TD": r["rush_td"], "RECEIVING TD": r["rec_td"],
                     "TOTAL TD": total}
            key = total
        else:
            continue
        out.append({"id": r["id"], "name": r["name"], "player_number": r["player_number"] or 0,
                    "position": r["position"], "team_name": r["team_name"],
                    "team_abbr": display_team_abbr(r["team_abbr"]), "stats": stats, "history": True, "_key": key})
    out.sort(key=lambda x: x["_key"], reverse=True)
    for x in out:
        del x["_key"]
    return out


def history_team_stats(cur, season, cat):
    """Team passing / rushing / receiving / scoring for a history season: its players' season lines summed by team
    (untracked depth players aren't in them), points from team_season_results."""
    cur.execute("""
        SELECT t.id, t.name AS team_name, t.abbreviation AS team_abbr,
               SUM(COALESCE(l.pass_attempts, 0)) AS pass_att, SUM(COALESCE(l.completions, 0)) AS pass_cmp,
               SUM(COALESCE(l.pass_yards, 0)) AS pass_yds, SUM(COALESCE(l.pass_tds, 0)) AS pass_tds,
               SUM(COALESCE(l.interceptions, 0)) AS ints, SUM(COALESCE(l.rush_attempts, 0)) AS rush_att,
               SUM(COALESCE(l.rush_yards, 0)) AS rush_yds, SUM(COALESCE(l.rush_tds, 0)) AS rush_tds,
               SUM(COALESCE(l.fumb, 0)) AS fums, MAX(r.points_for) AS pts
        FROM season_stat_lines l JOIN teams t ON t.id = l.team_id
        JOIN team_season_results r ON r.team_id = t.id AND r.season = l.season
        WHERE l.season = %s GROUP BY t.id, t.name, t.abbreviation
    """, (season,))
    out = []
    for r in cur.fetchall():
        pa, pc, py, pt = r["pass_att"], r["pass_cmp"], r["pass_yds"], r["pass_tds"]
        if cat == "passing":
            stats = {"ATT": pa, "CMP": pc, "CMP %": f"{100 * pc / pa:.1f}" if pa else "0.0",
                     "YDS/ATT": f"{py / pa:.1f}" if pa else "0.0", "PASS YDS": py, "TD": pt, "INT": r["ints"],
                     "RATE": f"{calculate_passer_rating(pc, pa, py, pt, r['ints']):.1f}"}
            key = py
        elif cat == "rushing":
            ra, ry = r["rush_att"], r["rush_yds"]
            stats = {"ATT": ra, "YDS": ry, "AVG": f"{ry / ra:.1f}" if ra else "0.0", "TD": r["rush_tds"],
                     "FUM": r["fums"]}
            key = ry
        elif cat == "receiving":
            stats = {"REC": pc, "YDS": py, "AVG": f"{py / pc:.1f}" if pc else "0.0", "TD": pt}
            key = py
        elif cat == "scoring":
            stats = {"PTS": r["pts"], "TOTAL TD": pt + r["rush_tds"], "PASS TD": pt, "RUSH TD": r["rush_tds"]}
            key = r["pts"] or 0
        else:
            return []
        out.append({"id": r["id"], "team_name": r["team_name"], "team_abbr": display_team_abbr(r["team_abbr"]),
                    "stats": stats, "history": True, "_key": key})
    out.sort(key=lambda x: x["_key"], reverse=True)
    for x in out:
        del x["_key"]
    return out


def _career_path(conn, player_id):
    """The player season ledger's view of a career (engines/season_ledger.career_path): his moves between captured
    seasons (moved, free agent, drafted, new, retired) and where it stands now. None before the ledger migration."""
    from psycopg.rows import tuple_row
    from engines import season_ledger
    with conn.cursor(row_factory=tuple_row) as cur:
        if not season_ledger.installed(cur):
            return None
        path = season_ledger.career_path(cur, player_id)
    return {"moves": path["moves"], "now": path["now"]}


def career_totals(career):
    """The player's league career (since 2026, the first season this league records): counting stats summed over
    every season row, plus the first season and how many are reconstructed history (pure)."""
    if not career:
        return None
    out = {k: 0 for k in CAREER_SUM}
    for c in career:
        for k in CAREER_SUM:
            v = c.get(k)
            if isinstance(v, (int, float)):
                out[k] += v
    out = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in out.items()}
    out["seasons"] = len(career)
    out["since"] = min(c["season"] for c in career)
    out["history_seasons"] = sum(1 for c in career if c.get("history"))
    return out


def _n(v):
    """Display number: whole numbers without .0 (sacks come in halves)."""
    if v is None:
        return None
    f = float(v)
    return int(f) if f.is_integer() else round(f, 1)


def _pct_str(made, att):
    return f"{100.0 * made / att:.1f}" if att else "0.0"


PLAYER_ST_CATEGORIES = {
    # category: (row filter, ORDER BY, stats builder)
    "tackles": ("l.def_tackles IS NOT NULL", "l.def_tackles DESC, l.def_sacks DESC",
                lambda l: {"TOT": _n(l["def_tackles"]), "SOLO": _n(l["def_solo"]), "AST": _n(l["def_assists"]),
                           "TFL": _n(l["def_tfl"]), "SCK": _n(l["def_sacks"]), "FF": _n(l["def_forced_fumbles"]),
                           "FR": _n(l["def_fumble_recoveries"])}),
    "sacks": ("l.def_tackles IS NOT NULL AND l.def_sacks > 0", "l.def_sacks DESC, l.def_tfl DESC",
              lambda l: {"SCK": _n(l["def_sacks"]), "TFL": _n(l["def_tfl"]), "TOT": _n(l["def_tackles"]),
                         "FF": _n(l["def_forced_fumbles"]), "FR": _n(l["def_fumble_recoveries"]),
                         "SFTY": _n(l["def_safeties"])}),
    "interceptions": ("l.def_tackles IS NOT NULL AND (l.def_ints > 0 OR l.def_deflections > 0)",
                      "l.def_ints DESC, l.def_deflections DESC",
                      lambda l: {"INT": _n(l["def_ints"]), "YDS": _n(l["def_int_yds"]), "AVG": _n(l["def_int_avg"]),
                                 "LNG": _n(l["def_int_long"]), "TD": _n(l["def_tds"]), "PD": _n(l["def_deflections"])}),
    "kicking": ("l.kick_fga > 0", "l.kick_fgm DESC, l.kick_fg_pct DESC",
                lambda l: {"FGM": _n(l["kick_fgm"]), "FGA": _n(l["kick_fga"]), "FG%": _pct_str(l["kick_fgm"], l["kick_fga"]),
                           "LNG": _n(l["kick_fg_long"]), "50+": f"{_n(l['kick_fgm_50']) or 0}/{_n(l['kick_fga_50']) or 0}",
                           "XPM": _n(l["kick_xpm"]), "XPA": _n(l["kick_xpa"]),
                           "PTS": _n(3 * (l["kick_fgm"] or 0) + (l["kick_xpm"] or 0))}),
    "punting": ("l.punt_punts > 0", "l.punt_net_average DESC",
                lambda l: {"PUNTS": _n(l["punt_punts"]), "YDS": _n(l["punt_yards"]), "AVG": _n(l["punt_average"]),
                           "NET": _n(l["punt_net_average"]), "LNG": _n(l["punt_longest"]), "IN 20": _n(l["punt_inside_20"]),
                           "TB": _n(l["punt_touchbacks"])}),
    "kick returns": ("l.kr_returns > 0", "l.kr_yards DESC",
                     lambda l: {"RET": _n(l["kr_returns"]), "YDS": _n(l["kr_yards"]), "AVG": _n(l["kr_average"]),
                                "LNG": _n(l["kr_longest"]), "TD": _n(l["kr_tds"])}),
    "punt returns": ("l.pr_returns > 0", "l.pr_yards DESC",
                     lambda l: {"RET": _n(l["pr_returns"]), "YDS": _n(l["pr_yards"]), "AVG": _n(l["pr_average"]),
                                "LNG": _n(l["pr_longest"]), "TD": _n(l["pr_tds"])}),
}


def season_type_filter(season_type: str) -> str:
    """SQL condition on games g: regular season (default) or postseason."""
    if str(season_type or "").lower().startswith("post"):
        return "COALESCE(g.game_type, 'REGULAR_SEASON') <> 'REGULAR_SEASON'"
    return "COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'"


# A season's playoff box-score lines summed per player, with the same columns as season_stat_lines
_SUMS = ("def_solo", "def_assists", "def_tackles", "def_tfl", "def_sacks", "def_ints", "def_int_yds", "def_deflections",
         "def_forced_fumbles", "def_fumble_recoveries", "def_safeties", "def_tds", "kick_fgm", "kick_fga", "kick_xpm",
         "kick_xpa", "kick_fgm_50", "kick_fga_50", "punt_punts", "punt_yards", "punt_net_yards", "punt_inside_20",
         "punt_touchbacks", "kr_returns", "kr_yards", "kr_tds", "pr_returns", "pr_yards", "pr_tds")
_LONGS = ("def_int_long", "kick_fg_long", "punt_longest", "kr_longest", "pr_longest")
PLAYOFF_GAMES = "COALESCE(g.game_type, 'REGULAR_SEASON') <> 'REGULAR_SEASON'"
POSTSEASON_LINES = f"""
    SELECT gl.player_id, MAX(gl.player_name) AS player_name, gl.season,
           (ARRAY_AGG(gl.team_id ORDER BY gl.week DESC))[1] AS team_id, NULL::text AS position,
           {", ".join(f"SUM(gl.{c}) AS {c}" for c in _SUMS)},
           {", ".join(f"MAX(gl.{c}) AS {c}" for c in _LONGS)},
           ROUND(SUM(gl.def_int_yds)::numeric / NULLIF(SUM(gl.def_ints), 0), 1) AS def_int_avg,
           ROUND(SUM(gl.kick_fgm)::numeric * 100 / NULLIF(SUM(gl.kick_fga), 0), 1) AS kick_fg_pct,
           ROUND(SUM(gl.punt_yards)::numeric / NULLIF(SUM(gl.punt_punts), 0), 1) AS punt_average,
           ROUND(SUM(gl.punt_net_average * gl.punt_punts)::numeric / NULLIF(SUM(gl.punt_punts), 0), 1) AS punt_net_average,
           ROUND(SUM(gl.kr_yards)::numeric / NULLIF(SUM(gl.kr_returns), 0), 1) AS kr_average,
           ROUND(SUM(gl.pr_yards)::numeric / NULLIF(SUM(gl.pr_returns), 0), 1) AS pr_average
    FROM game_stat_lines gl JOIN games g ON g.id = gl.game_id
    WHERE gl.season = %s AND g.status = 'COMPLETED' AND {{games}}
    GROUP BY gl.player_id, gl.season"""


def player_st_leaders(cur, season: int, cat: str, postseason: bool = False, games: str = PLAYOFF_GAMES) -> list:
    """Defense and special-teams player leaderboards: the Season Stats capture for the regular season, the
    season's captured playoff box scores for the postseason."""
    row_filter, order_by, build = PLAYER_ST_CATEGORIES[cat]
    source = f"({POSTSEASON_LINES.format(games=games)}) l" if postseason else "season_stat_lines l"
    cur.execute(f"""
        SELECT l.*, p.id AS pid, p.full_name AS display_name, p.player_number,
               COALESCE(ps.position, l.position) AS pos, t.name AS t_name, t.abbreviation AS t_abbr
        FROM {source}
        JOIN players p ON p.id = l.player_id
        LEFT JOIN player_seasons ps ON ps.player_id = l.player_id AND ps.season = l.season
        JOIN teams t ON t.id = COALESCE(ps.team_id, l.team_id)
        WHERE l.season = %s AND {row_filter}
        ORDER BY {order_by} NULLS LAST, p.id
    """, (season, season) if postseason else (season,))
    return [{
        "id": r["pid"], "name": r["display_name"] or r["player_name"], "player_number": r["player_number"] or 0,
        "position": r["pos"], "team_name": r["t_name"], "team_abbr": display_team_abbr(r["t_abbr"]),
        "stats": build(r),
    } for r in cur.fetchall()]


def team_situational_postseason(cur, season: int, cat: str, games: str = PLAYOFF_GAMES) -> list:
    """Postseason 'downs' and 'red zone defense' from the playoff games' captured team box scores. A box score has
    conversions but no attempts, red-zone TDs/FGs (trips from its percentage) and penalty yards but no count,
    so only those columns are returned (null where the box score didn't record a game's value)."""
    real = lambda col: (f"CASE WHEN {{t}}.{col} IS NOT NULL AND (NOT ('{col}' = ANY({{t}}.estimated_fields)) "
                        f"OR '{col}' = ANY({{t}}.exact_fields)) THEN {{t}}.{col} END")
    def total(t, col):   # NULL when any game is missing the value
        expr = real(col).format(t=t)
        return f"CASE WHEN COUNT({expr}) = COUNT(*) THEN SUM({expr}) END"
    cur.execute(f"""
        SELECT tm.id, tm.name AS team_name, tm.abbreviation AS team_abbr, COUNT(*) AS gp, SUM(opp.score) AS pa,
               {total('ts', 'third_down_conversions')} AS c3, {total('ts', 'fourth_down_conversions')} AS c4,
               {total('ts', 'two_point_conversions')} AS c2, {total('ts', 'red_zone_attempts')} AS rza,
               {total('ts', 'red_zone_tds')} AS rztd, {total('ts', 'penalty_yards')} AS peny,
               {total('opp', 'red_zone_attempts')} AS drza, {total('opp', 'red_zone_tds')} AS drztd,
               {total('opp', 'red_zone_fgs')} AS drzfg
        FROM games g
        JOIN teams tm ON tm.id IN (g.home_team_id, g.away_team_id)
        JOIN team_stats ts ON ts.game_id = g.id AND ts.team_name = tm.name
        JOIN team_stats opp ON opp.game_id = g.id AND opp.team_name <> tm.name
        WHERE g.season = %s AND g.status = 'COMPLETED' AND {games}
        GROUP BY tm.id, tm.name, tm.abbreviation
    """, (season,))
    out = []
    for r in cur.fetchall():
        if cat == "downs":
            stats = {"3D CONV": r["c3"], "4D CONV": r["c4"], "2PT": r["c2"], "RZ ATT": r["rza"], "RZ TD": r["rztd"],
                     "RZ TD%": _pct_str(r["rztd"], r["rza"]) if r["rza"] is not None and r["rztd"] is not None else None,
                     "PEN YDS": r["peny"]}
            order = (r["c3"] or 0) / (r["gp"] or 1)
        else:
            stats = {"PA": r["pa"] or 0, "PA/G": f"{(r['pa'] or 0) / r['gp']:.1f}" if r["gp"] else "0.0",
                     "RZ ATT": r["drza"], "RZ TD": r["drztd"], "RZ FG": r["drzfg"],
                     "RZ TD%": _pct_str(r["drztd"], r["drza"]) if r["drza"] is not None and r["drztd"] is not None else None,
                     "RZ SCORE%": (_pct_str((r["drztd"] or 0) + (r["drzfg"] or 0), r["drza"])
                                   if None not in (r["drza"], r["drztd"], r["drzfg"]) else None)}
            order = -((r["pa"] or 0) / (r["gp"] or 1))
        out.append({"id": r["id"], "team_name": r["team_name"], "team_abbr": display_team_abbr(r["team_abbr"]),
                    "stats": stats, "_order": order})
    out.sort(key=lambda x: x["_order"], reverse=True)
    for x in out:
        del x["_order"]
    return out


def team_situational_table(cur, season: int, cat: str) -> list:
    """Team 'downs' (third/fourth-down, two-point, red zone, penalties) and 'red zone defense' tables from the
    Team Stats capture (team_seasons)."""
    cur.execute("""
        SELECT t.id, t.name AS team_name, t.abbreviation AS team_abbr, ts.third_down_conv, ts.third_down_att,
               ts.fourth_down_conv, ts.fourth_down_att, ts.two_point_conv, ts.two_point_att,
               ts.red_zone_att, ts.red_zone_td, ts.red_zone_fg, ts.red_zone_def_att, ts.red_zone_def_td,
               ts.red_zone_def_fg, ts.penalties, ts.penalty_yards,
               (SELECT COUNT(*) FROM games g WHERE g.season = ts.season AND g.status = 'COMPLETED'
                  AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
                  AND t.id IN (g.home_team_id, g.away_team_id)) AS gp,
               (SELECT SUM(CASE WHEN g.home_team_id = t.id THEN g.away_score ELSE g.home_score END) FROM games g
                WHERE g.season = ts.season AND g.status = 'COMPLETED'
                  AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
                  AND t.id IN (g.home_team_id, g.away_team_id)) AS pa
        FROM team_seasons ts JOIN teams t ON t.id = ts.team_id
        WHERE ts.season = %s AND ts.third_down_att IS NOT NULL
    """, (season,))
    out = []
    for r in cur.fetchall():
        gp = r["gp"] or 0
        if cat == "downs":
            stats = {"3D CONV": r["third_down_conv"], "3D ATT": r["third_down_att"],
                     "3D%": _pct_str(r["third_down_conv"], r["third_down_att"]),
                     "4D CONV": r["fourth_down_conv"], "4D ATT": r["fourth_down_att"],
                     "4D%": _pct_str(r["fourth_down_conv"], r["fourth_down_att"]),
                     "2PT": f"{r['two_point_conv']}/{r['two_point_att']}",
                     "RZ ATT": r["red_zone_att"], "RZ TD": r["red_zone_td"],
                     "RZ TD%": _pct_str(r["red_zone_td"], r["red_zone_att"]),
                     "PEN": r["penalties"], "PEN YDS": r["penalty_yards"]}
            order = (r["third_down_conv"] or 0) / (r["third_down_att"] or 1)
        else:
            stats = {"PA": r["pa"] or 0, "PA/G": f"{(r['pa'] or 0) / gp:.1f}" if gp else "0.0",
                     "RZ ATT": r["red_zone_def_att"], "RZ TD": r["red_zone_def_td"], "RZ FG": r["red_zone_def_fg"],
                     "RZ TD%": _pct_str(r["red_zone_def_td"], r["red_zone_def_att"]),
                     "RZ SCORE%": _pct_str((r["red_zone_def_td"] or 0) + (r["red_zone_def_fg"] or 0),
                                           r["red_zone_def_att"])}
            order = -((r["red_zone_def_td"] or 0) / (r["red_zone_def_att"] or 1))
        out.append({"id": r["id"], "team_name": r["team_name"], "team_abbr": display_team_abbr(r["team_abbr"]),
                    "stats": stats, "_order": order})
    out.sort(key=lambda x: x["_order"], reverse=True)
    for x in out:
        del x["_order"]
    return out


@app.get("/api/stats/player")
def get_player_stats_api(season: Optional[int] = None, category: str = "Passing", season_type: str = "regular"):
    season, _ = season_week(season)
    cat = category.lower()
    games_filter = season_type_filter(season_type)
    postseason = games_filter.endswith("<> 'REGULAR_SEASON'")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            if is_history_season(cur, season) and cat in ("passing", "rushing", "receiving", "scoring"):
                # 2026-2034 history: season lines only (no game lines); no postseason stats were kept
                return [] if postseason else history_offense_leaders(cur, season, cat)
            if cat == "passing":
                cur.execute("""
                    SELECT 
                        p.id, p.full_name as name, p.player_number,
                        ps.position,
                        t.name as team_name, t.abbreviation as team_abbr,
                        SUM(COALESCE(st.pass_attempts, 0)) as att,
                        SUM(COALESCE(st.completions, 0)) as cmp,
                        SUM(COALESCE(st.pass_yards, 0)) as pass_yds,
                        SUM(COALESCE(st.pass_tds, 0)) as td,
                        SUM(COALESCE(st.interceptions, 0)) as int
                    FROM players p
                    JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                    JOIN teams t ON t.id = ps.team_id
                    JOIN player_stats st ON st.player_id = p.id
                    JOIN games g ON g.id = st.game_id AND g.season = %s AND """ + games_filter + """
                    GROUP BY p.id, p.full_name, p.player_number, ps.position, t.name, t.abbreviation
                    HAVING SUM(COALESCE(st.pass_attempts, 0)) > 0
                    ORDER BY pass_yds DESC;
                """, (season, season))
                rows = cur.fetchall()
                results = []
                for r in rows:
                    att = r['att'] or 0
                    cmp = r['cmp'] or 0
                    yds = r['pass_yds'] or 0
                    td = r['td'] or 0
                    ints = r['int'] or 0
                    cmp_pct = round((cmp / att * 100), 1) if att > 0 else 0.0
                    yds_att = round((yds / att), 1) if att > 0 else 0.0
                    rate = calculate_passer_rating(cmp, att, yds, td, ints)
                    results.append({
                        "id": r["id"],
                        "name": r["name"],
                        "player_number": r["player_number"] or 0,
                        "position": r["position"],
                        "team_name": r["team_name"],
                        "team_abbr": display_team_abbr(r["team_abbr"]),
                        "stats": {
                            "ATT": att,
                            "CMP": cmp,
                            "CMP %": f"{cmp_pct:.1f}",
                            "YDS/ATT": f"{yds_att:.1f}",
                            "PASS YDS": yds,
                            "TD": td,
                            "INT": ints,
                            "RATE": f"{rate:.1f}"
                        }
                    })
                return results

            elif cat == "rushing":
                cur.execute("""
                    SELECT 
                        p.id, p.full_name as name, p.player_number,
                        ps.position,
                        t.name as team_name, t.abbreviation as team_abbr,
                        SUM(COALESCE(st.rush_attempts, 0)) as att,
                        SUM(COALESCE(st.rush_yards, 0)) as yds,
                        SUM(COALESCE(st.rush_tds, 0)) as td,
                        SUM(COALESCE(st.fumb, 0)) as fum
                    FROM players p
                    JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                    JOIN teams t ON t.id = ps.team_id
                    JOIN player_stats st ON st.player_id = p.id
                    JOIN games g ON g.id = st.game_id AND g.season = %s AND """ + games_filter + """
                    GROUP BY p.id, p.full_name, p.player_number, ps.position, t.name, t.abbreviation
                    HAVING SUM(COALESCE(st.rush_attempts, 0)) > 0
                    ORDER BY yds DESC;
                """, (season, season))
                rows = cur.fetchall()
                results = []
                for r in rows:
                    att = r['att'] or 0
                    yds = r['yds'] or 0
                    td = r['td'] or 0
                    fum = r['fum'] or 0
                    avg = round((yds / att), 1) if att > 0 else 0.0
                    results.append({
                        "id": r["id"],
                        "name": r["name"],
                        "player_number": r["player_number"] or 0,
                        "position": r["position"],
                        "team_name": r["team_name"],
                        "team_abbr": display_team_abbr(r["team_abbr"]),
                        "stats": {
                            "ATT": att,
                            "YDS": yds,
                            "AVG": f"{avg:.1f}",
                            "TD": td,
                            "FUM": fum
                        }
                    })
                return results

            elif cat == "receiving":
                cur.execute("""
                    SELECT 
                        p.id, p.full_name as name, p.player_number,
                        ps.position,
                        t.name as team_name, t.abbreviation as team_abbr,
                        SUM(COALESCE(st.receptions, 0)) as rec,
                        SUM(COALESCE(st.rec_yards, 0)) as yds,
                        SUM(COALESCE(st.rec_tds, 0)) as td
                    FROM players p
                    JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                    JOIN teams t ON t.id = ps.team_id
                    JOIN player_stats st ON st.player_id = p.id
                    JOIN games g ON g.id = st.game_id AND g.season = %s AND """ + games_filter + """
                    GROUP BY p.id, p.full_name, p.player_number, ps.position, t.name, t.abbreviation
                    HAVING SUM(COALESCE(st.receptions, 0)) > 0
                    ORDER BY yds DESC;
                """, (season, season))
                rows = cur.fetchall()
                results = []
                for r in rows:
                    rec = r['rec'] or 0
                    yds = r['yds'] or 0
                    td = r['td'] or 0
                    avg = round((yds / rec), 1) if rec > 0 else 0.0
                    results.append({
                        "id": r["id"],
                        "name": r["name"],
                        "player_number": r["player_number"] or 0,
                        "position": r["position"],
                        "team_name": r["team_name"],
                        "team_abbr": display_team_abbr(r["team_abbr"]),
                        "stats": {
                            "REC": rec,
                            "YDS": yds,
                            "AVG": f"{avg:.1f}",
                            "TD": td
                        }
                    })
                return results

            elif cat == "scoring":
                cur.execute("""
                    SELECT 
                        p.id, p.full_name as name, p.player_number,
                        ps.position,
                        t.name as team_name, t.abbreviation as team_abbr,
                        SUM(COALESCE(st.pass_tds, 0)) as pass_tds,
                        SUM(COALESCE(st.rush_tds, 0)) as rush_tds,
                        SUM(COALESCE(st.rec_tds, 0)) as rec_tds,
                        (SUM(COALESCE(st.pass_tds, 0)) + SUM(COALESCE(st.rush_tds, 0)) + SUM(COALESCE(st.rec_tds, 0))) as total_td
                    FROM players p
                    JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                    JOIN teams t ON t.id = ps.team_id
                    JOIN player_stats st ON st.player_id = p.id
                    JOIN games g ON g.id = st.game_id AND g.season = %s AND """ + games_filter + """
                    GROUP BY p.id, p.full_name, p.player_number, ps.position, t.name, t.abbreviation
                    HAVING (SUM(COALESCE(st.pass_tds, 0)) + SUM(COALESCE(st.rush_tds, 0)) + SUM(COALESCE(st.rec_tds, 0))) > 0
                    ORDER BY total_td DESC;
                """, (season, season))
                rows = cur.fetchall()
                results = []
                for r in rows:
                    pass_td = r['pass_tds'] or 0
                    rush_td = r['rush_tds'] or 0
                    rec_td = r['rec_tds'] or 0
                    total = r['total_td'] or 0
                    results.append({
                        "id": r["id"],
                        "name": r["name"],
                        "player_number": r["player_number"] or 0,
                        "position": r["position"],
                        "team_name": r["team_name"],
                        "team_abbr": display_team_abbr(r["team_abbr"]),
                        "stats": {
                            "PASSING TD": pass_td,
                            "RUSHING TD": rush_td,
                            "RECEIVING TD": rec_td,
                            "TOTAL TD": total
                        }
                    })
                return results

            elif cat in PLAYER_ST_CATEGORIES:
                # Regular season: the Season Stats capture; postseason: the captured playoff box scores
                return player_st_leaders(cur, season, cat, postseason)

            return []

@app.get("/api/stats/team")
def get_team_stats_api(season: Optional[int] = None, category: str = "Passing", season_type: str = "regular"):
    season, _ = season_week(season)
    cat = category.lower()
    games_filter = season_type_filter(season_type)
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            if is_history_season(cur, season):
                # 2026-2034 history: no games, so no situational or postseason team stats
                if cat in ("downs", "red zone defense") or games_filter.endswith("<> 'REGULAR_SEASON'"):
                    return []
                return history_team_stats(cur, season, cat)
            if cat in ("downs", "red zone defense"):
                # Regular season: the Team Stats capture; postseason: the playoff games' captured box scores
                if games_filter.endswith("<> 'REGULAR_SEASON'"):
                    return team_situational_postseason(cur, season, cat)
                return team_situational_table(cur, season, cat)
            cur.execute("""
                SELECT 
                    t.id, t.name as team_name, t.abbreviation as team_abbr,
                    SUM(ts.passing_attempts) as pass_att,
                    SUM(ts.passing_completions) as pass_cmp,
                    SUM(ts.passing_yards) as pass_yds,
                    SUM(ts.passing_tds) as pass_tds,
                    SUM(ts.rushing_attempts) as rush_att,
                    SUM(ts.rushing_yards) as rush_yds,
                    SUM(ts.rushing_tds) as rush_tds,
                    SUM(ts.score) as pts
                FROM team_stats ts
                JOIN teams t ON t.name LIKE '%%' || ts.team_name
                JOIN games g ON g.id = ts.game_id
                WHERE g.season = %s AND g.status = 'COMPLETED' AND """ + games_filter + """
                GROUP BY t.id, t.name, t.abbreviation
            """, (season,))
            team_rows = cur.fetchall()

            cur.execute("""
                SELECT ps.team_id, SUM(ps.interceptions) as ints, SUM(ps.fumb) as fums
                FROM player_stats ps
                JOIN games g ON g.id = ps.game_id
                WHERE g.season = %s AND """ + games_filter + """
                GROUP BY ps.team_id
            """, (season,))
            extra_map = {r['team_id']: r for r in cur.fetchall()}

            results = []
            for r in team_rows:
                tid = r['id']
                ints = extra_map.get(tid, {}).get('ints') or 0
                fums = extra_map.get(tid, {}).get('fums') or 0

                pass_att = r['pass_att'] or 0
                pass_cmp = r['pass_cmp'] or 0
                pass_yds = r['pass_yds'] or 0
                pass_tds = r['pass_tds'] or 0
                rush_att = r['rush_att'] or 0
                rush_yds = r['rush_yds'] or 0
                rush_tds = r['rush_tds'] or 0
                pts = r['pts'] or 0

                if cat == "passing":
                    cmp_pct = round((pass_cmp / pass_att * 100), 1) if pass_att > 0 else 0.0
                    yds_att = round((pass_yds / pass_att), 1) if pass_att > 0 else 0.0
                    rate = calculate_passer_rating(pass_cmp, pass_att, pass_yds, pass_tds, ints)
                    stats = {
                        "ATT": pass_att,
                        "CMP": pass_cmp,
                        "CMP %": f"{cmp_pct:.1f}",
                        "YDS/ATT": f"{yds_att:.1f}",
                        "PASS YDS": pass_yds,
                        "TD": pass_tds,
                        "INT": ints,
                        "RATE": f"{rate:.1f}"
                    }
                    order_val = pass_yds

                elif cat == "rushing":
                    avg = round((rush_yds / rush_att), 1) if rush_att > 0 else 0.0
                    stats = {
                        "ATT": rush_att,
                        "YDS": rush_yds,
                        "AVG": f"{avg:.1f}",
                        "TD": rush_tds,
                        "FUM": fums
                    }
                    order_val = rush_yds

                elif cat == "receiving":
                    avg = round((pass_yds / pass_cmp), 1) if pass_cmp > 0 else 0.0
                    stats = {
                        "REC": pass_cmp,
                        "YDS": pass_yds,
                        "AVG": f"{avg:.1f}",
                        "TD": pass_tds
                    }
                    order_val = pass_yds

                elif cat == "scoring":
                    total_td = pass_tds + rush_tds
                    stats = {
                        "PTS": pts,
                        "TOTAL TD": total_td,
                        "PASS TD": pass_tds,
                        "RUSH TD": rush_tds
                    }
                    order_val = pts

                else:
                    stats = {}
                    order_val = 0

                results.append({
                    "id": tid,
                    "team_name": r["team_name"],
                    "team_abbr": display_team_abbr(r["team_abbr"]),
                    "stats": stats,
                    "_order": order_val
                })

            results.sort(key=lambda x: x["_order"], reverse=True)
            for x in results:
                del x["_order"]
            return results


# ─── Mu Fantasy articles (newsroom/fantasy_desk.py writes them; docs/in_progress/FANTASY_EDITORIAL_MATRIX.md) ───
_NEWSROOM_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "newsroom", "config")


def _load_config(name, key):
    try:
        import json
        with open(os.path.join(_NEWSROOM_CONFIG, name)) as f:
            return json.load(f).get(key, {})
    except Exception:
        return {}


FANTASY_TYPE_NAMES = {k: v.get("name", k) for k, v in _load_config("article_types.json", "article_types").items()
                      if k.startswith("FANTASY_")}
FANTASY_WRITER_NAMES = {k: v.get("display_name", k) for k, v in _load_config("fantasy_writers.json", "writers").items()}
FANTASY_LAST_SLATE_WEEK = 18   # W19–W23 keep showing the Week 18 slate
FANTASY_FRONT_SLOTS = ("Fantasy Hero", "Fantasy 1", "Fantasy 2", "Fantasy 3")


def _fantasy_slot_order(slot: str) -> tuple:
    if slot in FANTASY_FRONT_SLOTS:
        return (0, FANTASY_FRONT_SLOTS.index(slot))
    m = re.match(r"Fantasy Shelf (\d+)$", slot or "")
    return (1, int(m.group(1))) if m else (2, 0)


def fantasy_article_week(cur, season: int, week: int):
    """The fantasy slate shown for `week`: that week's slate (weeks after 18 show Week 18), or the latest earlier
    week that has one. None if the season has no fantasy articles up to then."""
    cur.execute("""
        SELECT MAX(week) AS week FROM news_feed
        WHERE season = %s AND week <= %s AND content_type = 'ARTICLE' AND article_type LIKE 'FANTASY%%'
    """, (season, min(week, FANTASY_LAST_SLATE_WEEK)))
    row = cur.fetchone()
    return row["week"] if row else None


def fantasy_articles(cur, season: int, week: int) -> list:
    """Fantasy articles of one week in display order (front page, then shelf), as card dicts."""
    cur.execute("""
        SELECT DISTINCT ON (n.id) n.id, n.week, n.slot, n.article_type, n.headline, n.excerpt, n.persona,
               n.created_at, array_length(regexp_split_to_array(trim(n.content), '\\s+'), 1) AS words,
               am.display_name,
               (SELECT t.abbreviation FROM article_teams at JOIN teams t ON t.id = at.team_id
                WHERE at.article_id = n.id ORDER BY at.mention_count DESC NULLS LAST, at.team_id LIMIT 1) AS team_abbr
        FROM news_feed n
        LEFT JOIN agent_memories am ON am.assigned_team_id IS NULL AND UPPER(am.archetype) = UPPER(n.persona)
        WHERE n.season = %s AND n.week = %s AND n.content_type = 'ARTICLE' AND n.article_type LIKE 'FANTASY%%'
        ORDER BY n.id, am.id
    """, (season, week))
    rows = sorted(cur.fetchall(), key=lambda r: (_fantasy_slot_order(r["slot"]), r["id"]))
    out = []
    for r in rows:
        author = r["display_name"] or FANTASY_WRITER_NAMES.get((r["persona"] or "").upper()) or r["persona"]
        minutes = max(1, round((r["words"] or 0) / 230))
        out.append({
            "id": r["id"],
            "week": r["week"],
            "slot": r["slot"],
            "front_page": r["slot"] in FANTASY_FRONT_SLOTS,
            "article_type": r["article_type"],
            "label": FANTASY_TYPE_NAMES.get(r["article_type"], (r["article_type"] or "").replace("_", " ").title()),
            "title": r["headline"],
            "excerpt": r["excerpt"],
            "author": author,
            "time": f"{minutes} min",
            "updated": r["created_at"].isoformat() if r["created_at"] else None,
            "team": display_team_abbr(r["team_abbr"]).lower() if r["team_abbr"] else None,
        })
    return out


@app.get("/api/fantasy/articles")
def get_fantasy_articles_api(season: Optional[int] = None, week: Optional[int] = None):
    """Every Mu Fantasy article for a week ("View all analysis"): front page first, then the shelf.
    Weeks after 18 show the Week 18 slate; a week with no slate shows the latest earlier one."""
    season, week = season_week(season, week)
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            shown = fantasy_article_week(cur, season, week)
            articles = fantasy_articles(cur, season, shown) if shown else []
            cur.execute("""
                SELECT DISTINCT week FROM news_feed
                WHERE season = %s AND content_type = 'ARTICLE' AND article_type LIKE 'FANTASY%%' ORDER BY week
            """, (season,))
            weeks = [r["week"] for r in cur.fetchall()]
    return {
        "season": season,
        "requested_week": week,
        "week": shown,
        "available_weeks": weeks,
        "front_page": [a for a in articles if a["front_page"]],
        "more_analysis": [a for a in articles if not a["front_page"]],
    }


@app.get("/api/fantasy/dashboard")
def get_fantasy_dashboard_api(season: Optional[int] = None, week: Optional[int] = None):
    season, week = season_week(season, week)
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            # 1. Marquee games for the requested week: (projected) Excitement Index + editorial stakes
            #    (division games, teams still in the playoff race) — same ranking as matchup-primer picks.
            from newsroom.slot_targets import load_league_snapshot, rank_games_by_interest
            ranked = rank_games_by_interest(load_league_snapshot(CONN_INFO, season, week))[:3]
            cur.execute("""
                SELECT g.id, t_home.name as home_name, t_home.abbreviation as home_abbr,
                       t_away.name as away_name, t_away.abbreviation as away_abbr
                FROM games g
                JOIN teams t_home ON g.home_team_id = t_home.id
                JOIN teams t_away ON g.away_team_id = t_away.id
                WHERE g.id = ANY(%s);
            """, ([g["game_id"] for g in ranked],))
            names = {r['id']: r for r in cur.fetchall()}
            excitement_games = []
            for g in ranked:
                r = names[g["game_id"]]
                tags = ["Projected" if g["projected"] else ("Final" if g["excitement"] is not None else "Not yet projected")]
                if g["division_game"]:
                    tags.append("Division game")
                if g["teams_in_race"] == 2:
                    tags.append("Playoff race")
                excitement_games.append({
                    "id": g["game_id"],
                    "away": display_team_abbr(r['away_abbr']).lower(),
                    "home": display_team_abbr(r['home_abbr']).lower(),
                    "away_name": r['away_name'],
                    "home_name": r['home_name'],
                    "away_abbr": display_team_abbr(r['away_abbr']),
                    "home_abbr": display_team_abbr(r['home_abbr']),
                    "score": display_excitement(g["excitement"]),
                    "projected": g["projected"],
                    "note": f"Week {week} · " + " · ".join(tags),
                })

            # Pre-fetch players lookup
            cur.execute("""
                SELECT p.id, p.full_name, p.espn_id, ps.team_id, ps.position 
                FROM players p 
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s;
            """, (season,))
            players_list = cur.fetchall()
            p_map = {}
            for p in players_list:
                fn = p['full_name']
                parts = fn.split(' ')
                m_name = f"{parts[0][0]}. {' '.join(parts[1:])}" if len(parts) >= 2 else fn
                p_map[(p['team_id'], m_name)] = p
                p_map[m_name] = p

            # 2. Weekly rankings (display pos_rank like WR1, and delta from last week's fantasy finish)
            prev_week = max(1, week - 1)
            cur.execute("""
                WITH prev_finish AS (
                    SELECT p.id as player_id,
                           DENSE_RANK() OVER (PARTITION BY ps.position ORDER BY st.fantasy_points DESC) as prev_pos_rank
                    FROM players p
                    JOIN player_seasons ps ON ps.player_id = p.id
                    JOIN games g ON g.season = ps.season AND (g.home_team_id = ps.team_id OR g.away_team_id = ps.team_id)
                    JOIN player_stats st ON st.game_id = g.id AND st.player_id = p.id
                    WHERE ps.season = %s AND g.week = %s AND g.status = 'COMPLETED'
                )
                SELECT fp.player_id, fp.player_name, fp.position, t.abbreviation as team_abbr,
                       fp.pos_rank, p.espn_id,
                       CAST(SUBSTRING(fp.pos_rank FROM '[0-9]+') AS INTEGER) as curr_num,
                       pf.prev_pos_rank,
                       (pf.prev_pos_rank - CAST(SUBSTRING(fp.pos_rank FROM '[0-9]+') AS INTEGER)) as rank_delta
                FROM fantasy_projections fp
                JOIN teams t ON fp.team_id = t.id
                LEFT JOIN players p ON p.id = fp.player_id
                LEFT JOIN prev_finish pf ON pf.player_id = fp.player_id
                WHERE fp.season = %s AND fp.week = %s
                ORDER BY fp.projected_points DESC
                LIMIT 8;
            """, (season, prev_week, season, week))
            weekly_raw = cur.fetchall()
            weekly_players = []
            for r in weekly_raw:
                delta_val = r['rank_delta']
                if delta_val is None or delta_val == 0:
                    delta_str = "—"
                    trend = "even"
                elif delta_val > 0:
                    delta_str = f"+{delta_val}"
                    trend = "up"
                else:
                    delta_str = f"{delta_val}".replace("-", "−")
                    trend = "down"

                weekly_players.append({
                    "id": r['player_id'],
                    "playerId": r['player_id'],
                    "name": r['player_name'],
                    "position": r['position'],
                    "team": display_team_abbr(r['team_abbr']),
                    "points": r['pos_rank'],
                    "espn_id": r['espn_id'],
                    "trend": trend,
                    "change": delta_str
                })

            # 3. ADP (per year; subtext is delta from last year's ADP if available, else hidden)
            prev_season = season - 1
            cur.execute("""
                WITH prev_adp AS (
                    SELECT player_id, adp_rank as prev_rank
                    FROM fantasy_projections
                    WHERE season = %s AND week = 1 AND adp_rank IS NOT NULL
                )
                SELECT fp.player_id, fp.adp_rank, fp.player_name, fp.team_id, fp.position, t.abbreviation as team_abbr,
                       p.espn_id,
                       pa.prev_rank,
                       (pa.prev_rank - fp.adp_rank) as adp_delta
                FROM fantasy_projections fp
                JOIN teams t ON fp.team_id = t.id
                LEFT JOIN players p ON p.id = fp.player_id
                LEFT JOIN prev_adp pa ON pa.player_id = fp.player_id
                WHERE fp.season = %s AND fp.week = 1 AND fp.adp_rank IS NOT NULL AND fp.player_id IS NOT NULL
                ORDER BY fp.adp_rank ASC
                LIMIT 8;
            """, (prev_season, season))
            adp_raw = cur.fetchall()
            adp_players = []
            for r in adp_raw:
                pl = p_map.get((r['team_id'], r['player_name'])) or p_map.get(r['player_name']) or {}
                delta_val = r['adp_delta']
                if delta_val is not None:
                    if delta_val > 0:
                        delta_str = f"+{delta_val}"
                        trend = "up"
                    elif delta_val < 0:
                        delta_str = f"{delta_val}".replace("-", "−")
                        trend = "down"
                    else:
                        delta_str = "—"
                        trend = "even"
                else:
                    # Incomplete/unavailable prior year ADP: keep subtext invisible
                    delta_str = ""
                    trend = "even"

                pid = r['player_id'] or pl.get('id', 0)
                adp_players.append({
                    "id": pid,
                    "playerId": pid,
                    "name": pl.get('full_name', r['player_name']),
                    "position": r['position'] or pl.get('position', 'FLEX'),
                    "team": display_team_abbr(r['team_abbr']),
                    "points": f"{r['adp_rank']}.0",
                    "espn_id": r['espn_id'] or pl.get('espn_id'),
                    "trend": trend,
                    "change": delta_str
                })

            # 4. ROS (displays rank itself like QB1 or WR1, ordered by ros_total_points, with no subtext)
            cur.execute("""
                SELECT fp.player_id, fp.player_name, fp.position, t.abbreviation as team_abbr,
                       fp.ros_rank, fp.ros_total_points, p.espn_id
                FROM fantasy_projections fp
                JOIN teams t ON fp.team_id = t.id
                LEFT JOIN players p ON p.id = fp.player_id
                WHERE fp.season = %s AND fp.week = %s
                ORDER BY fp.ros_total_points DESC NULLS LAST
                LIMIT 8;
            """, (season, week))
            ros_raw = cur.fetchall()
            ros_players = []
            for r in ros_raw:
                ros_players.append({
                    "id": r['player_id'],
                    "playerId": r['player_id'],
                    "name": r['player_name'],
                    "position": r['position'],
                    "team": display_team_abbr(r['team_abbr']),
                    "points": r['ros_rank'],
                    "espn_id": r['espn_id'],
                    "trend": "even",
                    "change": ""
                })

            # 5. Projections (retrieved directly from persistent fantasy_projections table)
            cur.execute("""
                SELECT fp.player_id, fp.player_name, fp.position, t.abbreviation as team_abbr,
                       fp.avg_points, fp.matchup_stars, fp.projected_points, fp.delta, p.espn_id
                FROM fantasy_projections fp
                JOIN teams t ON fp.team_id = t.id
                LEFT JOIN players p ON p.id = fp.player_id
                WHERE fp.season = %s AND fp.week = %s
                ORDER BY fp.projected_points DESC
                LIMIT 8;
            """, (season, week))
            proj_rows = cur.fetchall()
            projections_players = []
            for r in proj_rows:
                delta_val = float(r['delta'] or 0.0)
                if delta_val > 0:
                    delta_str = f"+{delta_val:.1f}"
                    trend = "up"
                elif delta_val < 0:
                    delta_str = f"{delta_val:.1f}".replace("-", "−")
                    trend = "down"
                else:
                    delta_str = "0.0"
                    trend = "even"

                projections_players.append({
                    "id": r['player_id'],
                    "playerId": r['player_id'],
                    "name": r['player_name'],
                    "position": r['position'],
                    "team": display_team_abbr(r['team_abbr']),
                    "points": f"{float(r['projected_points'] or 0.0):.1f}",
                    "avg_fpts": f"{float(r['avg_points'] or 0.0):.1f}",
                    "stars": int(r['matchup_stars'] or 3),
                    "espn_id": r['espn_id'],
                    "trend": trend,
                    "change": delta_str
                })

            # Mu Fantasy articles: the stored slate for this week (W19+ show W18), else placeholders
            shown_week = fantasy_article_week(cur, season, week)
            stored = fantasy_articles(cur, season, shown_week) if shown_week else []
            front = [a for a in stored if a["front_page"]]
            placeholder_hero = {
                    "label": f"WEEK {week} LINEUP ADVICE",
                    "title": f"Start 'Em, Sit 'Em: Week {week} Matchups to Exploit",
                    "excerpt": f"Six lineup decisions where opportunity, playoff stakes, and defensive tendencies point in the same direction for Week {week}.",
                    "author": "Barnwell Bot",
                    "confidence": "94%",
                    "time": "11 MIN READ",
                    "updated": "TODAY · 9:42 AM ET"
                }
            placeholder_articles = [
                    {
                        "label": "Trade & Roster Lab",
                        "title": f"Week {week} League Winners: High-Ceiling Plays for the Regular Season Finale",
                        "author": "Matthew Berry Bot",
                        "time": "6 min",
                        "team": "car"
                    },
                    {
                        "label": "Matchup Breakdown",
                        "title": f"Pace and Personnel: Breaking Down Week {week} Slate-Breaking Games",
                        "author": "KC Joyner Bot",
                        "time": "9 min",
                        "team": "pit"
                    },
                    {
                        "label": "Waiver Heat",
                        "title": f"Final Stretch Surge: Under-The-Radar Value Entering Week {week}",
                        "author": "Mike Sando Bot",
                        "time": "5 min",
                        "team": "cle"
                    }
                ]
            return {
                "week": week,
                "season": season,
                "articles_week": shown_week,
                "articles_source": "newsroom" if front else "placeholder",
                "hero": front[0] if front else placeholder_hero,
                "articles": front[1:4] if front else placeholder_articles,
                "more_analysis_count": len(stored) - len(front),
                "social_takes": social_api.fantasy_takes(cur, season, week),
                "excitement_games": excitement_games,
                "quick_rankings": {
                    "Weekly": weekly_players,
                    "Projections": projections_players,
                    "ADP": adp_players,
                    "ROS Rankings": ros_players
                }
            }

FANTASY_POSITIONS = ("QB", "HB", "FB", "WR", "TE")
FLEX_POSITIONS = ("HB", "FB", "WR", "TE")
UI_POSITION_MAP = {"QB": ("QB",), "RB": ("HB", "FB"), "WR": ("WR",), "TE": ("TE",)}


def _ui_position(pos: str) -> str:
    return "RB" if pos in ("HB", "FB") else pos


def _weekly_finishes(cur, season: int, week: int) -> dict:
    """Actual PPR finishes for a completed week, keyed by player_id."""
    cur.execute("""
        SELECT p.id AS player_id, p.full_name, p.espn_id, ps.position, ps.team_id,
               t.abbreviation AS team_abbr, st.fantasy_points,
               DENSE_RANK() OVER (
                   PARTITION BY CASE WHEN ps.position IN ('HB','FB') THEN 'RB' ELSE ps.position END
                   ORDER BY st.fantasy_points DESC
               ) AS pos_rank_num
        FROM games g
        JOIN player_stats st ON st.game_id = g.id
        JOIN player_seasons ps ON ps.season = g.season AND ps.player_id = st.player_id
        JOIN players p ON p.id = ps.player_id
        JOIN teams t ON t.id = ps.team_id
        WHERE g.season = %s AND g.week = %s AND g.status = 'COMPLETED'
          AND ps.position IN ('QB','HB','FB','WR','TE')
          AND st.fantasy_points IS NOT NULL
    """, (season, week))
    finishes = {}
    for r in cur.fetchall():
        # Keep best row if a player somehow appears twice
        if r["player_id"] not in finishes or r["fantasy_points"] > finishes[r["player_id"]]["fantasy_points"]:
            finishes[r["player_id"]] = r
    return finishes


@app.get("/api/fantasy/rankings/weekly")
def get_weekly_fantasy_rankings(season: Optional[int] = None, week: Optional[int] = None, position: str = "FLEX", limit: int = 150):
    season, week = season_week(season, week)
    position = position.upper()
    # FLEX = RB/WR/TE only (no QBs)
    allowed = UI_POSITION_MAP.get(position, FLEX_POSITIONS)

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            # Opponent lookup for the requested week
            cur.execute("""
                SELECT g.home_team_id, g.away_team_id, g.status,
                       th.abbreviation AS home_abbr, ta.abbreviation AS away_abbr
                FROM games g
                JOIN teams th ON th.id = g.home_team_id
                JOIN teams ta ON ta.id = g.away_team_id
                WHERE g.season = %s AND g.week = %s
            """, (season, week))
            opponents = {}
            opponent_ids = {}
            week_completed = False   # any game this week is final
            week_all_final = None    # every game this week is final
            for g in cur.fetchall():
                opponents[g["home_team_id"]] = f"vs {display_team_abbr(g['away_abbr'])}"
                opponents[g["away_team_id"]] = f"@ {display_team_abbr(g['home_abbr'])}"
                opponent_ids[g["home_team_id"]] = g["away_team_id"]
                opponent_ids[g["away_team_id"]] = g["home_team_id"]
                week_completed = week_completed or g["status"] == "COMPLETED"
                if g["status"] != "BYE":
                    week_all_final = (week_all_final is not False) and g["status"] == "COMPLETED"

            # Matchup stars by (defense team, position) — same source as player modal
            cur.execute("""
                SELECT def_team_id, position, matchup_stars
                FROM vw_fantasy_defense_vs_position
                WHERE season = %s
            """, (season,))
            stars_map = {(r["def_team_id"], r["position"]): int(r["matchup_stars"]) for r in cur.fetchall() if r["matchup_stars"] is not None}

            def matchup_stars(team_id, pos):
                opp = opponent_ids.get(team_id)
                if opp is None:
                    return None  # bye week
                return stars_map.get((opp, pos), 3)

            prev_finishes = _weekly_finishes(cur, season, week - 1) if week > 1 else {}

            # Stored projections for this week (used unless the week is fully played)
            cur.execute("""
                SELECT fp.player_id, fp.player_name, fp.position, fp.team_id,
                       t.abbreviation AS team_abbr, fp.projected_points, fp.pos_rank,
                       fp.matchup_stars, fp.avg_points, p.espn_id
                FROM fantasy_projections fp
                JOIN teams t ON t.id = fp.team_id
                LEFT JOIN players p ON p.id = fp.player_id
                WHERE fp.season = %s AND fp.week = %s
                  AND fp.pos_rank IS NOT NULL AND fp.player_id IS NOT NULL
                  AND fp.position = ANY(%s)
                ORDER BY fp.projected_points DESC
            """, (season, week, list(allowed)))
            proj_rows = cur.fetchall()

            rows = []
            # A fully played week shows actual finishes; otherwise the stored projections
            if proj_rows and not week_all_final:
                mode = "projected"
                for r in proj_rows:
                    rank_num = int(re.sub(r"\D", "", r["pos_rank"]) or 0)
                    rows.append({
                        "player_id": r["player_id"],
                        "name": r["player_name"],
                        "position": r["position"],
                        "team_id": r["team_id"],
                        "team_abbr": r["team_abbr"],
                        "points": float(r["projected_points"] or 0),
                        "pos_rank": f"{_ui_position(r['position'])}{rank_num}",
                        "pos_rank_num": rank_num,
                        "espn_id": r["espn_id"],
                        "stars": int(r["matchup_stars"]) if r["matchup_stars"] else matchup_stars(r["team_id"], r["position"]),
                    })
            elif week_completed:
                mode = "final"
                for r in _weekly_finishes(cur, season, week).values():
                    if r["position"] not in allowed:
                        continue
                    rows.append({
                        "player_id": r["player_id"],
                        "name": r["full_name"],
                        "position": r["position"],
                        "team_id": r["team_id"],
                        "team_abbr": r["team_abbr"],
                        "points": float(r["fantasy_points"] or 0),
                        "pos_rank": f"{_ui_position(r['position'])}{r['pos_rank_num']}",
                        "pos_rank_num": int(r["pos_rank_num"]),
                        "espn_id": r["espn_id"],
                        "stars": matchup_stars(r["team_id"], r["position"]),
                    })
                rows.sort(key=lambda x: x["points"], reverse=True)
            else:
                mode = "unavailable"

            players = []
            for i, r in enumerate(rows[:limit]):
                prev = prev_finishes.get(r["player_id"])
                # Positive trend = moved up vs last week's positional finish
                trend = int(prev["pos_rank_num"]) - r["pos_rank_num"] if prev else None
                players.append({
                    "rank": i + 1,
                    "id": r["player_id"],
                    "playerId": r["player_id"],
                    "name": r["name"],
                    "position": _ui_position(r["position"]),
                    "team": display_team_abbr(r["team_abbr"]),
                    "opponent": opponents.get(r["team_id"], "BYE"),
                    "value": f"{r['points']:.1f}",
                    "pos_rank": r["pos_rank"],
                    "prev_finish": f"{_ui_position(r['position'])}{prev['pos_rank_num']}" if prev else None,
                    "trend": trend,
                    "stars": r["stars"],
                    "espn_id": r["espn_id"],
                })

            return {
                "season": season,
                "week": week,
                "position": position,
                "mode": mode,
                "total": len(rows),
                "players": players,
            }


@app.get("/api/fantasy/rankings/ros")
def get_ros_fantasy_rankings(season: Optional[int] = None, week: Optional[int] = None, position: str = "FLEX", limit: int = 150):
    season, week = season_week(season, week)
    position = position.upper()
    # FLEX = RB/WR/TE only (no QBs)
    allowed = UI_POSITION_MAP.get(position, FLEX_POSITIONS)

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            # Query Season SOS (all weeks) and Playoff SOS (weeks 15-17) based on opponent defense matchup stars
            cur.execute("""
                WITH team_schedule AS (
                    SELECT g.season, g.week,
                           g.home_team_id AS team_id, g.away_team_id AS opp_team_id
                    FROM games g
                    WHERE g.season = %s
                    UNION ALL
                    SELECT g.season, g.week,
                           g.away_team_id AS team_id, g.home_team_id AS opp_team_id
                    FROM games g
                    WHERE g.season = %s
                ),
                sos_calc AS (
                    SELECT ts.team_id,
                           v.position,
                           NTILE(5) OVER (PARTITION BY v.position ORDER BY AVG(v.matchup_stars) ASC)::int as season_sos_stars,
                           NTILE(5) OVER (PARTITION BY v.position ORDER BY AVG(CASE WHEN ts.week BETWEEN 15 AND 17 THEN v.matchup_stars END) ASC)::int as playoff_sos_stars
                    FROM team_schedule ts
                    JOIN vw_fantasy_defense_vs_position v ON v.season = ts.season AND v.def_team_id = ts.opp_team_id
                    GROUP BY ts.team_id, v.position
                ),
                overall_ranks AS (
                    SELECT fp.player_id,
                           ROW_NUMBER() OVER (ORDER BY fp.ros_total_points DESC NULLS LAST) as overall_ros_rank
                    FROM fantasy_projections fp
                    WHERE fp.season = %s AND fp.week = (
                        SELECT COALESCE(
                            (SELECT %s WHERE EXISTS (SELECT 1 FROM fantasy_projections WHERE season = %s AND week = %s AND ros_rank IS NOT NULL)),
                            (SELECT MAX(week) FROM fantasy_projections WHERE season = %s AND ros_rank IS NOT NULL)
                        )
                    ) AND fp.ros_rank IS NOT NULL
                )
                SELECT fp.player_id, fp.player_name, fp.position, fp.team_id,
                       t.abbreviation AS team_abbr, fp.ros_total_points, fp.ros_rank,
                       fp.adp_rank, o.overall_ros_rank, p.espn_id,
                       COALESCE(sos.season_sos_stars, 3) as season_sos,
                       COALESCE(sos.playoff_sos_stars, 3) as playoff_sos,
                       CASE WHEN fp.adp_rank IS NOT NULL THEN (fp.adp_rank - o.overall_ros_rank) ELSE NULL END as rank_trend
                FROM fantasy_projections fp
                JOIN teams t ON fp.team_id = t.id
                JOIN overall_ranks o ON o.player_id = fp.player_id
                LEFT JOIN players p ON p.id = fp.player_id
                LEFT JOIN sos_calc sos ON sos.team_id = fp.team_id AND sos.position = fp.position
                WHERE fp.season = %s
                  AND fp.week = (
                      SELECT COALESCE(
                          (SELECT %s WHERE EXISTS (SELECT 1 FROM fantasy_projections WHERE season = %s AND week = %s AND ros_rank IS NOT NULL)),
                          (SELECT MAX(week) FROM fantasy_projections WHERE season = %s AND ros_rank IS NOT NULL)
                      )
                  )
                  AND fp.ros_rank IS NOT NULL AND fp.player_id IS NOT NULL
                  AND fp.position = ANY(%s)
                ORDER BY fp.ros_total_points DESC NULLS LAST;
            """, (season, season, season, week, season, week, season, season, week, season, week, season, list(allowed)))
            rows = cur.fetchall()

            players = []
            for i, r in enumerate(rows[:limit]):
                raw_rank = r["ros_rank"] or ""
                rank_num = int(re.sub(r"\D", "", raw_rank) or 0)
                ui_pos = _ui_position(r["position"])
                pos_rank = f"{ui_pos}{rank_num}" if rank_num > 0 else f"{ui_pos}1"
                trend_val = r["rank_trend"]
                players.append({
                    "rank": i + 1,
                    "id": r["player_id"],
                    "playerId": r["player_id"],
                    "name": r["player_name"],
                    "position": ui_pos,
                    "team": display_team_abbr(r["team_abbr"]),
                    "value": f"{float(r['ros_total_points'] or 0):.1f}",
                    "pos_rank": pos_rank,
                    "season_sos": max(1, min(5, int(r["season_sos"] or 3))),
                    "playoff_sos": max(1, min(5, int(r["playoff_sos"] or 3))),
                    "adp_rank": r["adp_rank"],
                    "overall_rank": r["overall_ros_rank"],
                    "trend": int(trend_val) if trend_val is not None else None,
                    "espn_id": r["espn_id"],
                })

            return {
                "season": season,
                "week": week,
                "position": position,
                "mode": "ros",
                "total": len(rows),
                "players": players,
            }


@app.get("/api/fantasy/rankings/adp")
def get_adp_fantasy_rankings(season: Optional[int] = None, position: str = "ALL", limit: int = 500):
    season, _ = season_week(season)
    pos_upper = (position or "ALL").upper()
    if pos_upper in ("ALL", "CONSENSUS", "OVERALL"):
        allowed = ["QB", "HB", "WR", "TE"]
    else:
        allowed = UI_POSITION_MAP.get(pos_upper, FLEX_POSITIONS)

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                WITH all_team_weeks AS (
                    SELECT t.id as team_id, w.week
                    FROM teams t
                    CROSS JOIN generate_series(1, 18) as w(week)
                ),
                played_weeks AS (
                    SELECT home_team_id as team_id, week FROM games WHERE season = %s
                    UNION
                    SELECT away_team_id as team_id, week FROM games WHERE season = %s
                ),
                team_byes AS (
                    SELECT atw.team_id, MIN(atw.week) as bye_week
                    FROM all_team_weeks atw
                    LEFT JOIN played_weeks pw ON pw.team_id = atw.team_id AND pw.week = atw.week
                    WHERE pw.week IS NULL
                    GROUP BY atw.team_id
                ),
                team_schedule AS (
                    SELECT g.season, g.week,
                           g.home_team_id AS team_id, g.away_team_id AS opp_team_id
                    FROM games g
                    WHERE g.season = %s
                    UNION ALL
                    SELECT g.season, g.week,
                           g.away_team_id AS team_id, g.home_team_id AS opp_team_id
                    FROM games g
                    WHERE g.season = %s
                ),
                sos_calc AS (
                    SELECT ts.team_id,
                           v.position,
                           NTILE(5) OVER (PARTITION BY v.position ORDER BY AVG(v.matchup_stars) ASC)::int as season_sos_stars,
                           NTILE(5) OVER (PARTITION BY v.position ORDER BY AVG(CASE WHEN ts.week BETWEEN 15 AND 17 THEN v.matchup_stars END) ASC)::int as playoff_sos_stars
                    FROM team_schedule ts
                    JOIN vw_fantasy_defense_vs_position v ON v.season = ts.season AND v.def_team_id = ts.opp_team_id
                    GROUP BY ts.team_id, v.position
                )
                SELECT fp.player_id, fp.player_name, fp.position, fp.team_id,
                       t.abbreviation AS team_abbr, tb.bye_week,
                       fp.adp_rank, fp.ros_rank, p.espn_id,
                       COALESCE(sos.season_sos_stars, 3) as season_sos,
                       COALESCE(sos.playoff_sos_stars, 3) as playoff_sos
                FROM fantasy_projections fp
                JOIN teams t ON fp.team_id = t.id
                LEFT JOIN players p ON p.id = fp.player_id
                LEFT JOIN team_byes tb ON tb.team_id = fp.team_id
                LEFT JOIN sos_calc sos ON sos.team_id = fp.team_id AND sos.position = fp.position
                WHERE fp.season = %s
                  AND fp.week = (
                      SELECT MAX(week) FROM fantasy_projections 
                      WHERE season = %s AND adp_rank IS NOT NULL AND player_id IS NOT NULL
                  )
                  AND fp.adp_rank IS NOT NULL AND fp.player_id IS NOT NULL
                  AND fp.position = ANY(%s)
                ORDER BY fp.adp_rank ASC;
            """, (season, season, season, season, season, season, list(allowed)))
            rows = cur.fetchall()

            players = []
            pos_counter = {}
            for i, r in enumerate(rows[:limit]):
                ui_pos = _ui_position(r["position"])
                pos_counter[ui_pos] = pos_counter.get(ui_pos, 0) + 1
                pos_rank = f"{ui_pos}{pos_counter[ui_pos]}"
                t_abbr = display_team_abbr(r["team_abbr"])
                bye_str = f" ({r['bye_week']})" if r["bye_week"] else ""
                players.append({
                    "rank": i + 1,
                    "id": r["player_id"],
                    "playerId": r["player_id"],
                    "name": r["player_name"],
                    "position": ui_pos,
                    "pos_rank": pos_rank,
                    "team": t_abbr,
                    "bye_week": r["bye_week"],
                    "team_bye": f"{t_abbr}{bye_str}",
                    "season_sos": max(1, min(5, int(r["season_sos"] or 3))),
                    "playoff_sos": max(1, min(5, int(r["playoff_sos"] or 3))),
                    "espn_id": r["espn_id"],
                })

            return {
                "season": season,
                "position": position,
                "mode": "adp",
                "total": len(rows),
                "players": players,
            }


@app.get("/api/fantasy/projections")
def get_fantasy_projections_api(
    season: Optional[int] = None,
    week: Optional[int] = None,
    position: Optional[str] = "ALL",
    limit: int = 600,
):
    """Returns fantasy projections for the specified season and week."""
    season, default_week = season_week(season)
    wk = week if week is not None else 1
    pos_upper = (position or "ALL").upper()

    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            where_clauses = ["fp.season = %s", "fp.week = %s"]
            params = [season, wk]

            if pos_upper != "ALL":
                if pos_upper in ("RB", "HB"):
                    where_clauses.append("ps.position IN ('RB', 'HB', 'FB')")
                else:
                    where_clauses.append("ps.position = %s")
                    params.append(pos_upper)
            else:
                where_clauses.append("ps.position IN ('QB', 'HB', 'FB', 'RB', 'WR', 'TE', 'K', 'DEF')")

            where_sql = " AND ".join(where_clauses)
            cur.execute(f"""
                SELECT p.id, p.player_name, p.full_name, ps.position, ps.age, ps.team_id,
                       COALESCE(t.abbreviation, 'NFL') as team_abbr,
                       COALESCE(fp.adp_rank, 999) as adp,
                       ss.total_fantasy_points as ss_total_pts,
                       ss.ppg_fantasy_points as ss_ppg_pts,
                       fp.ros_total_points,
                       fp.avg_points as fp_avg_pts,
                       NULLIF(fp.projected_points, 0) as fp_proj_pts,
                       COALESCE(ss.rush_attempts, 0) as rush_att,
                       COALESCE(ss.rush_yards, 0) as rush_yds,
                       COALESCE(ss.rush_tds, 0) as rush_tds,
                       COALESCE(ss.receptions, 0) as rec,
                       COALESCE(ss.rec_yards, 0) as rec_yds,
                       COALESCE(ss.rec_tds, 0) as rec_tds
                FROM players p
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.season = ps.season
                LEFT JOIN teams t ON t.id = ps.team_id
                LEFT JOIN season_stats ss ON ss.player_id = p.id AND ss.season = ps.season
                WHERE {where_sql}
                ORDER BY COALESCE(fp.adp_rank, 999) ASC, ss.total_fantasy_points DESC NULLS LAST, p.id ASC
                LIMIT %s
            """, [season] + params + [limit])
            rows = cur.fetchall()

            players = []
            for r in rows:
                adp_val = int(r["adp"])
                team_id = r["team_id"] or 1
                bye_wk = (team_id % 10) + 5
                ros_pct = round(max(5.0, min(100.0, 100.0 - (float(adp_val) * 0.45))), 1) if adp_val <= 200 else 5.0

                ss_total = r["ss_total_pts"]
                ss_ppg = r["ss_ppg_pts"]
                ros_total = r["ros_total_points"]
                fp_avg = r["fp_avg_pts"]
                fp_proj = r["fp_proj_pts"]

                # PTS must be season total points, and AVG must be ppg
                if ss_total is not None and float(ss_total) > 10.0:
                    tot_pts = round(float(ss_total), 1)
                    avg_ppg = round(float(ss_ppg) if ss_ppg and float(ss_ppg) > 0 else (tot_pts / 17.0), 1)
                elif ros_total is not None and float(ros_total) > 10.0:
                    tot_pts = round(float(ros_total), 1)
                    avg_ppg = round(tot_pts / 17.0, 1)
                elif fp_avg is not None and float(fp_avg) > 0:
                    avg_ppg = round(float(fp_avg), 1)
                    tot_pts = round(avg_ppg * 17.0, 1)
                elif fp_proj is not None and float(fp_proj) > 0:
                    avg_ppg = round(float(fp_proj), 1)
                    tot_pts = round(avg_ppg * 17.0, 1)
                else:
                    # Consensus projection scaled by ADP
                    if adp_val <= 12:
                        avg_ppg = round(18.0 - (adp_val * 0.3), 1)
                    elif adp_val <= 36:
                        avg_ppg = round(14.5 - ((adp_val - 12) * 0.15), 1)
                    elif adp_val <= 75:
                        avg_ppg = round(11.0 - ((adp_val - 36) * 0.08), 1)
                    elif adp_val <= 150:
                        avg_ppg = round(8.0 - ((adp_val - 75) * 0.03), 1)
                    else:
                        avg_ppg = round(max(3.5, 6.0 - ((adp_val - 150) * 0.01)), 1)
                    tot_pts = round(avg_ppg * 17.0, 1)

                players.append({
                    "id": r["id"],
                    "player_id": r["id"],
                    "name": r["full_name"] or r["player_name"],
                    "player_name": r["player_name"],
                    "position": r["position"],
                    "team": r["team_abbr"],
                    "headshot_url": f"/static/players/{r['id']}.webp",
                    "adp": adp_val,
                    "bye": bye_wk,
                    "age": r["age"] or 25,
                    "ros_pct": ros_pct,
                    "projections": {
                        "pts": tot_pts,
                        "avg": avg_ppg,
                    },
                    "rush": {
                        "rush": int(r["rush_att"]),
                        "yds": int(r["rush_yds"]),
                        "td": int(r["rush_tds"]),
                    },
                    "rec": {
                        "rec": int(r["rec"]),
                        "yds": int(r["rec_yds"]),
                        "td": int(r["rec_tds"]),
                    },
                })

            return {
                "season": season,
                "week": wk,
                "count": len(players),
                "players": players,
            }


@app.get("/api/search")
def search_global(q: str = "", season: Optional[int] = None, limit: int = 20):
    """
    Search teams and players for the global site search bar.
    """
    season, _ = season_week(season)
    clean_q = q.strip()
    if not clean_q:
        return {"query": "", "teams": [], "players": []}

    pattern = f"%{clean_q}%"
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            # Search teams
            cur.execute("""
                SELECT id, name, city, abbreviation, conference, division, team_color
                FROM teams
                WHERE name ILIKE %s OR city ILIKE %s OR abbreviation ILIKE %s OR (city || ' ' || name) ILIKE %s
                ORDER BY name
                LIMIT 8;
            """, (pattern, pattern, pattern, pattern))
            team_rows = cur.fetchall()
            teams_res = [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "city": r["city"],
                    "fullName": f"{r['city']} {r['name']}",
                    "abbr": r["abbreviation"].lower(),
                    "displayAbbr": display_team_abbr(r["abbreviation"]),
                    "conference": r["conference"],
                    "division": r["division"],
                    "color": r["team_color"],
                }
                for r in team_rows
            ]

            # Search players
            has_aliases = player_name_aliases_exist(cur)
            alias_clause = "\n                   OR EXISTS (SELECT 1 FROM player_name_aliases pna WHERE pna.player_id = p.id AND pna.alias ILIKE %s)" if has_aliases else ""
            sql_params = [HISTORY_CAPTURED_FROM, season, pattern, pattern]
            if has_aliases:
                sql_params.append(pattern)

            cur.execute(f"""
                SELECT p.id, p.full_name, p.player_number, p.espn_id,
                       COALESCE(ps_curr.position, ps_latest.position, 'ATH') as position,
                       COALESCE(ps_curr.overall_rating, ps_latest.overall_rating, 75) as overall_rating,
                       COALESCE(t_curr.abbreviation, t_latest.abbreviation, 'FA') as team_abbr,
                       COALESCE(t_curr.name, t_latest.name, 'Free Agent') as team_name,
                       ps_curr.player_id IS NULL AND ps_latest.season < %s AS history_only, ps_latest.season AS last_season
                FROM players p
                LEFT JOIN player_seasons ps_curr ON ps_curr.player_id = p.id AND ps_curr.season = %s
                LEFT JOIN teams t_curr ON t_curr.id = ps_curr.team_id
                LEFT JOIN LATERAL (
                    SELECT ps.position, ps.overall_rating, ps.team_id, ps.season
                    FROM player_seasons ps
                    WHERE ps.player_id = p.id
                    ORDER BY ps.season DESC
                    LIMIT 1
                ) ps_latest ON true
                LEFT JOIN teams t_latest ON t_latest.id = ps_latest.team_id
                WHERE p.full_name ILIKE %s
                   OR p.player_name ILIKE %s{alias_clause}
                ORDER BY (p.origin IS NOT NULL), overall_rating DESC NULLS LAST   -- today's players before history
                LIMIT 15;
            """, tuple(sql_params))
            player_rows = cur.fetchall()
            players_res = [
                {
                    "id": r["id"],
                    "name": r["full_name"],
                    "position": _ui_position(r["position"]) if r["position"] else "ATH",
                    "overall": None if r["history_only"] else r["overall_rating"],
                    "number": r["player_number"],
                    "teamAbbr": display_team_abbr(r["team_abbr"]).lower(),
                    "teamDisplayAbbr": display_team_abbr(r["team_abbr"]),
                    "teamName": r["team_name"],
                    "espn_id": r["espn_id"],
                    "history": bool(r["history_only"]),            # played only in 2026-2034 league history
                    "lastSeason": r["last_season"],
                }
                for r in player_rows
            ]

            return {
                "query": clean_q,
                "teams": teams_res,
                "players": players_res,
            }


@app.get("/api/fantasy/players")
def get_fantasy_search_players(season: Optional[int] = None, min_points: Optional[float] = None, q: Optional[str] = None):
    season, _ = season_week(season)
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_week FROM league_state LIMIT 1;")
            st_row = cur.fetchone()
            curr_week = st_row["current_week"] if st_row else 1

            cur.execute("""
                SELECT p.id,
                       p.full_name,
                       p.espn_id,
                       ps.position,
                       t.id AS team_id,
                       t.abbreviation AS team_abbr,
                       COALESCE(ROUND(SUM(st.fantasy_points), 1), 0.0) AS total_fpts,
                       COALESCE(fp.projected_points, 0.0) AS proj_fpts
                FROM player_seasons ps
                JOIN players p ON p.id = ps.player_id
                JOIN teams t ON t.id = ps.team_id
                LEFT JOIN games g ON g.season = ps.season AND g.status = 'COMPLETED' AND (g.home_team_id = t.id OR g.away_team_id = t.id)
                LEFT JOIN player_stats st ON st.game_id = g.id AND st.player_id = p.id
                LEFT JOIN fantasy_projections fp ON fp.season = ps.season AND fp.player_id = p.id AND fp.week = %s
                WHERE ps.season = %s
                  AND ps.position IN ('QB', 'HB', 'FB', 'WR', 'TE', 'K')
                GROUP BY p.id, p.full_name, p.espn_id, ps.position, t.id, t.abbreviation, fp.projected_points, ps.overall_rating
                ORDER BY total_fpts DESC, proj_fpts DESC, ps.overall_rating DESC;
            """, (curr_week, season))
            rows = cur.fetchall()

            players = []
            for r in rows:
                ui_pos = _ui_position(r["position"])
                t_abbr = display_team_abbr(r["team_abbr"])
                fpts = float(r["total_fpts"] or 0.0)
                proj = float(r["proj_fpts"] or 0.0)
                if min_points is not None and fpts < min_points:
                    continue
                players.append({
                    "id": r["id"],
                    "playerId": r["id"],
                    "name": r["full_name"],
                    "position": ui_pos,
                    "team": t_abbr,
                    "fpts": fpts if fpts > 0 else proj,
                    "actual_fpts": fpts,
                    "projected_fpts": proj,
                    "espn_id": r["espn_id"],
                })

            if q:
                q_clean = q.strip().lower()
                players = [p for p in players if q_clean in f"{p['name']} {p['team']} {p['position']}".lower()]

            return {
                "season": season,
                "count": len(players),
                "players": players,
            }


@app.get("/api/news/week/{week}")
def get_weekly_news(week: int, season: Optional[int] = None):
    """
    Returns all commissioned articles for a given week with their assigned editorial slots:
    - Hero Slot (top story)
    - Secondary Slots (Slot 1, Slot 2)
    - Feature Trio (Slot 3, Slot 4, Slot 5)
    - Local Matchup Primers (Slot 6, Slot 7, Slot 8)
    - Standalone (Power Rankings)
    - Other (anything else, e.g. unslotted articles)
    Team-page beat coverage (slot 'Local Beat N') is excluded; it is served by /api/team/{abbr}/news.
    """
    season, week = season_week(season, week)
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (n.id)
                       n.id, n.season, n.week, n.team_id, n.content_type, n.persona,
                       n.article_type, n.slot, n.headline, n.excerpt, n.content, n.created_at,
                       am.display_name, am.handle, am.archetype
                FROM news_feed n
                LEFT JOIN agent_memories am ON (
                    (n.team_id IS NOT NULL AND am.assigned_team_id = n.team_id AND UPPER(am.archetype) = UPPER(n.persona))
                    OR (n.team_id IS NULL AND UPPER(am.archetype) = UPPER(n.persona))
                )
                WHERE n.season = %s AND n.week = %s AND n.content_type = 'ARTICLE'
                  -- Team-page beat coverage ('Local Beat N') belongs on team pages, not the weekly slate
                  AND (n.slot IS NULL OR n.slot NOT LIKE 'Local Beat%%')
                  AND (n.article_type IS NULL OR n.article_type NOT LIKE 'FANTASY%%')  -- Mu Fantasy has its own page
                ORDER BY n.id ASC, am.id ASC;
            """, (season, week))
            articles = cur.fetchall()

            if not articles:
                return {
                    "season": season,
                    "week": week,
                    "total_articles": 0,
                    "slotted": {
                        "hero": None,
                        "secondary": [],
                        "features": [],
                        "local_primers": [],
                        "standalone": None,
                        "other": []
                    },
                    "articles": []
                }

            # Fetch entity tags for these articles
            art_ids = [a["id"] for a in articles]
            cur.execute("""
                SELECT at.article_id, t.id AS team_id, t.name, t.city, t.abbreviation, 
                       at.mention_type, at.mention_count
                FROM article_teams at
                JOIN teams t ON at.team_id = t.id
                WHERE at.article_id = ANY(%s)
                ORDER BY at.mention_type = 'PRIMARY' DESC, at.mention_count DESC;
            """, (art_ids,))
            team_rows = cur.fetchall()
            teams_by_art = {}
            for tr in team_rows:
                teams_by_art.setdefault(tr["article_id"], []).append({
                    "id": tr["team_id"],
                    "name": tr["name"],
                    "city": tr["city"],
                    "abbr": display_team_abbr(tr["abbreviation"]),
                    "mention_type": tr["mention_type"],
                    "mention_count": tr["mention_count"]
                })

            cur.execute("""
                SELECT ap.article_id, p.id AS player_id, p.full_name, ps.position,
                       t.abbreviation AS team_abbr, ap.mention_type, ap.mention_count
                FROM article_players ap
                JOIN players p ON ap.player_id = p.id
                LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                LEFT JOIN teams t ON ap.team_id = t.id
                WHERE ap.article_id = ANY(%s)
                ORDER BY ap.mention_type = 'PRIMARY' DESC, ap.mention_count DESC;
            """, (season, art_ids))
            player_rows = cur.fetchall()
            players_by_art = {}
            for pr in player_rows:
                players_by_art.setdefault(pr["article_id"], []).append({
                    "id": pr["player_id"],
                    "name": pr["full_name"],
                    "position": pr["position"],
                    "team": display_team_abbr(pr["team_abbr"]) if pr["team_abbr"] else None,
                    "mention_type": pr["mention_type"],
                    "mention_count": pr["mention_count"]
                })

            slotted_response = {
                "hero": None,
                "secondary": [],
                "features": [],
                "local_primers": [],
                "standalone": None,
                "historic_events": [],
                "other": []
            }

            formatted_articles = []
            for a in articles:
                art_id = a["id"]
                slot_name = a["slot"] or "Unslotted"
                art_dict = {
                    "id": art_id,
                    "season": a["season"],
                    "week": a["week"],
                    "slot": slot_name,
                    "article_type": a["article_type"],
                    "headline": a["headline"],
                    "excerpt": a["excerpt"],
                    "author": {
                        "display_name": a["display_name"] or a["persona"],
                        "handle": a["handle"] or f"@{a['persona']}",
                        "archetype": a["persona"]
                    },
                    "team_id": a["team_id"],
                    "created_at": a["created_at"].isoformat() if a["created_at"] else None,
                    "tagged_teams": teams_by_art.get(art_id, []),
                    "tagged_players": players_by_art.get(art_id, [])
                }
                formatted_articles.append(art_dict)

                # Categorize into newsroom display slots
                s_lower = slot_name.lower()
                if "hero" in s_lower:
                    slotted_response["hero"] = art_dict
                elif s_lower in ("slot 1", "slot 2") or s_lower.startswith("secondary"):
                    slotted_response["secondary"].append(art_dict)
                elif s_lower in ("slot 3", "slot 4", "slot 5") or s_lower.startswith("feature"):
                    slotted_response["features"].append(art_dict)
                elif s_lower in ("slot 6", "slot 7", "slot 8"):
                    slotted_response["local_primers"].append(art_dict)
                elif "standalone" in s_lower or a["article_type"] == "POWER_RANKINGS":
                    slotted_response["standalone"] = art_dict
                elif "record" in s_lower or a["article_type"] == "HISTORIC_RECORD_BREAK":
                    slotted_response["historic_events"].append(art_dict)
                    slotted_response["features"].append(art_dict)
                else:
                    slotted_response["other"].append(art_dict)

            return {
                "season": season,
                "week": week,
                "total_articles": len(formatted_articles),
                "slotted": slotted_response,
                "articles": formatted_articles
            }

@app.get("/api/news/article/{article_id}")
def get_article_detail(article_id: int):
    """Returns the full text and metadata of a specific article, along with related articles written that week."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT n.id, n.season, n.week, n.team_id, n.content_type, n.persona,
                       n.article_type, n.slot, n.headline, n.excerpt, n.content, n.created_at,
                       am.display_name, am.handle, am.archetype, am.bio,
                       t_auth.name AS author_team_name, t_auth.abbreviation AS author_team_abbr
                FROM news_feed n
                LEFT JOIN teams t_auth ON t_auth.id = n.team_id
                LEFT JOIN agent_memories am ON (
                    (n.team_id IS NOT NULL AND am.assigned_team_id = n.team_id AND UPPER(am.archetype) = UPPER(n.persona))
                    OR (n.team_id IS NULL AND UPPER(am.archetype) = UPPER(n.persona))
                )
                WHERE n.id = %s;
            """, (article_id,))
            article = cur.fetchone()
            if not article:
                return {"error": f"Article #{article_id} not found."}

            cur.execute("""
                SELECT t.id, t.name, t.city, t.abbreviation, at.mention_type, at.mention_count
                FROM article_teams at
                JOIN teams t ON at.team_id = t.id
                WHERE at.article_id = %s
                ORDER BY at.mention_type = 'PRIMARY' DESC, at.mention_count DESC;
            """, (article_id,))
            teams = cur.fetchall()

            cur.execute("""
                SELECT p.id, p.full_name, ps.position, t.abbreviation AS team_abbr,
                       ap.mention_type, ap.mention_count
                FROM article_players ap
                JOIN players p ON ap.player_id = p.id
                LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                LEFT JOIN teams t ON ap.team_id = t.id
                WHERE ap.article_id = %s
                ORDER BY ap.mention_type = 'PRIMARY' DESC, ap.mention_count DESC;
            """, (article["season"], article_id))
            players = cur.fetchall()

            # Query real related articles actually written that week (excluding current article)
            cur.execute("""
                SELECT n.id, n.season, n.week, n.headline, n.excerpt, n.article_type, n.persona, n.slot,
                       n.created_at, am.display_name, am.handle, t_sub.name AS author_team_name
                FROM news_feed n
                LEFT JOIN teams t_sub ON t_sub.id = n.team_id
                LEFT JOIN agent_memories am ON (
                    (n.team_id IS NOT NULL AND am.assigned_team_id = n.team_id AND UPPER(am.archetype) = UPPER(n.persona))
                    OR (n.team_id IS NULL AND UPPER(am.archetype) = UPPER(n.persona))
                )
                WHERE n.season = %s AND n.week = %s AND n.content_type = 'ARTICLE' AND n.id != %s
                ORDER BY n.id ASC
                LIMIT 6;
            """, (article["season"], article["week"], article_id))
            related_rows = cur.fetchall()

            type_names = {k: re.sub(r"^The\s+|\s*\(.*\)\s*$", "", v.get("name", k))
                          for k, v in _load_config("article_types.json", "article_types").items()}

            related_articles = []
            for r in related_rows:
                r_author = r["display_name"] or r["persona"]
                if r["persona"] == "LOCAL_BEAT_REPORTER" and r.get("author_team_name"):
                    r_author = f"{r['author_team_name']} Beat Reporter"
                related_articles.append({
                    "id": r["id"],
                    "season": r["season"],
                    "week": r["week"],
                    "headline": r["headline"],
                    "excerpt": r["excerpt"],
                    "article_type": r["article_type"],
                    "label": type_names.get(r["article_type"], (r["article_type"] or "Article").replace("_", " ").title()),
                    "author": {
                        "display_name": r_author,
                        "handle": r["handle"] or f"@{r['persona']}",
                        "archetype": r["persona"]
                    }
                })

            author_name = article["display_name"] or article["persona"]
            if article["persona"] == "LOCAL_BEAT_REPORTER" and article.get("author_team_name"):
                author_name = f"{article['author_team_name']} Beat Reporter"

            return {
                "id": article["id"],
                "season": article["season"],
                "week": article["week"],
                "slot": article["slot"],
                "article_type": article["article_type"],
                "headline": article["headline"],
                "excerpt": article["excerpt"],
                "content": article["content"],
                "author": {
                    "display_name": author_name,
                    "handle": article["handle"] or f"@{article['persona']}",
                    "archetype": article["persona"],
                    "bio": article["bio"],
                    "team_abbr": display_team_abbr(article["author_team_abbr"]) if article.get("author_team_abbr") else None
                },
                "team_id": article["team_id"],
                "created_at": article["created_at"].isoformat() if article["created_at"] else None,
                "tagged_teams": [{
                    "id": t["id"], "name": t["name"], "city": t["city"],
                    "abbr": display_team_abbr(t["abbreviation"]),
                    "mention_type": t["mention_type"], "mention_count": t["mention_count"]
                } for t in teams],
                "tagged_players": [{
                    "id": p["id"], "name": p["full_name"], "position": p["position"],
                    "team": display_team_abbr(p["team_abbr"]) if p["team_abbr"] else None,
                    "mention_type": p["mention_type"], "mention_count": p["mention_count"]
                } for p in players],
                "related_articles": related_articles
            }

@app.get("/api/author/{author_key}")
def get_author_profile(author_key: str):
    """
    Returns an author's profile and all articles written by them across the season/archive.
    author_key can be a handle (e.g. '@billbarnwell', 'billbarnwell'), an archetype (e.g. 'BARNWELL', 'LOCAL_BEAT_REPORTER'),
    or an agent_id (e.g. 'Bill_Barnwell', 'Cowboys_Beat_1').
    """
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            clean_key = author_key.strip()
            handle_norm = clean_key if clean_key.startswith("@") else f"@{clean_key}"
            handle_raw = clean_key.lstrip("@")

            # 1. Look up agent in agent_memories
            cur.execute("""
                SELECT am.agent_id, am.archetype, am.handle, am.display_name, am.bio, am.assigned_team_id,
                       t.id AS team_id, t.name AS team_name, t.city AS team_city, t.abbreviation AS team_abbr, t.team_color
                FROM agent_memories am
                LEFT JOIN teams t ON t.id = am.assigned_team_id
                WHERE lower(am.handle) = lower(%s)
                   OR lower(am.handle) = lower(%s)
                   OR lower(am.agent_id) = lower(%s)
                   OR lower(am.archetype) = lower(%s)
                ORDER BY am.assigned_team_id NULLS LAST
                LIMIT 1;
            """, (clean_key, handle_norm, clean_key, clean_key))
            agent = cur.fetchone()

            # 2. Query articles written by this author
            if agent:
                assigned_team_id = agent["assigned_team_id"]
                archetype = agent["archetype"]
                agent_id = agent["agent_id"]
                display_name = agent["display_name"] or agent["handle"] or archetype
                if archetype == "LOCAL_BEAT_REPORTER" and agent.get("team_name"):
                    display_name = f"{agent['team_name']} Beat Reporter"

                if assigned_team_id is not None and archetype == "LOCAL_BEAT_REPORTER":
                    cur.execute("""
                        SELECT n.id, n.season, n.week, n.team_id, n.content_type, n.persona,
                               n.article_type, n.slot, n.headline, n.excerpt, n.content, n.created_at
                        FROM news_feed n
                        WHERE n.content_type = 'ARTICLE'
                          AND n.persona = 'LOCAL_BEAT_REPORTER'
                          AND n.team_id = %s
                        ORDER BY n.season DESC, n.week DESC, n.id DESC;
                    """, (assigned_team_id,))
                else:
                    cur.execute("""
                        SELECT n.id, n.season, n.week, n.team_id, n.content_type, n.persona,
                               n.article_type, n.slot, n.headline, n.excerpt, n.content, n.created_at
                        FROM news_feed n
                        WHERE n.content_type = 'ARTICLE'
                          AND (n.agent_id = %s OR UPPER(n.persona) = UPPER(%s) OR UPPER(n.persona) = UPPER(%s))
                        ORDER BY n.season DESC, n.week DESC, n.id DESC;
                    """, (agent_id, archetype, clean_key))
                raw_articles = cur.fetchall()
                author_data = {
                    "handle": agent["handle"] or f"@{archetype.lower()}",
                    "display_name": display_name,
                    "archetype": archetype,
                    "bio": agent["bio"] or f"Covers the NFL for Gridiron Newsroom.",
                    "team": {
                        "id": agent["team_id"],
                        "name": agent["team_name"],
                        "city": agent["team_city"],
                        "abbreviation": display_team_abbr(agent["team_abbr"]),
                        "team_color": agent["team_color"]
                    } if agent["team_id"] else None
                }
            else:
                # Fallback directly to news_feed persona matching
                cur.execute("""
                    SELECT n.id, n.season, n.week, n.team_id, n.content_type, n.persona,
                           n.article_type, n.slot, n.headline, n.excerpt, n.content, n.created_at,
                           t.name AS team_name, t.city AS team_city, t.abbreviation AS team_abbr, t.team_color
                    FROM news_feed n
                    LEFT JOIN teams t ON t.id = n.team_id
                    WHERE n.content_type = 'ARTICLE'
                      AND (UPPER(n.persona) = UPPER(%s) OR UPPER(n.persona) = UPPER(%s))
                    ORDER BY n.season DESC, n.week DESC, n.id DESC;
                """, (clean_key, handle_raw))
                raw_articles = cur.fetchall()
                if not raw_articles:
                    return {"error": f"Author '{author_key}' not found.", "articles": []}

                first_art = raw_articles[0]
                author_data = {
                    "handle": f"@{clean_key.lstrip('@')}",
                    "display_name": clean_key.replace("_", " ").title(),
                    "archetype": first_art["persona"],
                    "bio": "Staff Writer for Gridiron Newsroom.",
                    "team": {
                        "id": first_art["team_id"],
                        "name": first_art["team_name"],
                        "city": first_art["team_city"],
                        "abbreviation": display_team_abbr(first_art["team_abbr"]),
                        "team_color": first_art["team_color"]
                    } if first_art["team_id"] else None
                }

            # Fetch tagged entities for these articles
            formatted = []
            if raw_articles:
                art_ids = [a["id"] for a in raw_articles]
                cur.execute("""
                    SELECT at_sub.article_id, t.id AS team_id, t.name, t.city, t.abbreviation, 
                           at_sub.mention_type, at_sub.mention_count
                    FROM article_teams at_sub
                    JOIN teams t ON at_sub.team_id = t.id
                    WHERE at_sub.article_id = ANY(%s)
                    ORDER BY at_sub.mention_type = 'PRIMARY' DESC, at_sub.mention_count DESC;
                """, (art_ids,))
                team_rows = cur.fetchall()
                teams_by_art = {}
                for tr in team_rows:
                    teams_by_art.setdefault(tr["article_id"], []).append({
                        "id": tr["team_id"],
                        "name": tr["name"],
                        "city": tr["city"],
                        "abbr": display_team_abbr(tr["abbreviation"]),
                        "mention_type": tr["mention_type"],
                        "mention_count": tr["mention_count"]
                    })

                type_names = {k: re.sub(r"^The\s+|\s*\(.*\)\s*$", "", v.get("name", k))
                              for k, v in _load_config("article_types.json", "article_types").items()}

                for a in raw_articles:
                    formatted.append({
                        "id": a["id"],
                        "season": a["season"],
                        "week": a["week"],
                        "slot": a["slot"] or "Coverage",
                        "article_type": a["article_type"],
                        "label": type_names.get(a["article_type"], (a["article_type"] or "Article").replace("_", " ").title()),
                        "headline": a["headline"],
                        "excerpt": a["excerpt"],
                        "created_at": a["created_at"].isoformat() if a["created_at"] else None,
                        "tagged_teams": teams_by_art.get(a["id"], [])
                    })

            return {
                "author": author_data,
                "total_articles": len(formatted),
                "articles": formatted
            }

@app.post("/api/news/team/{team_id}/wakeup")
def trigger_team_wakeup(team_id: int, season: Optional[int] = None, week: Optional[int] = None, live: bool = False, max_workers: int = 5):
    """
    Triggers the on-demand local swarm wakeup for a specific franchise page:
    - Wakes up all beat reporters for the team to write localized articles.
    - Wakes up local die-hard fan personas for reaction tweets.
    - Wakes up national pundits if team popularity warrants attention.
    Runs in parallel across max_workers threads. Only live=true calls Gemini and saves; live=false is a dry run
    (prompts are compiled, nothing is written to news_feed).
    """
    season, week = season_week(season, week)
    from newsroom.swarm_dispatcher import SwarmDispatcher, build_weekly_assignment_brief
    sd = SwarmDispatcher(debug_mode=False)
    brief = build_weekly_assignment_brief(CONN_INFO, season=season, week=week)
    sd.dispatch_team_local(
        team_id=team_id,
        brief=brief,
        season=season,
        week=week,
        live=live,
        save_to_db=live,
        max_workers=max_workers
    )
    return {
        "status": "success",
        "team_id": team_id,
        "season": season,
        "week": week,
        "message": (
            f"Team #{team_id} local media wakeup dispatched with {max_workers} parallel workers."
            if live else f"Team #{team_id} dry run: prompts compiled, nothing saved (pass live=true to generate)."
        ),
    }

# --- Agent Wire (docs/done/SOCIAL_MATRIX.md §7; queries in server/social_api.py) ---
@app.get("/api/social/feed")
def social_feed(season: Optional[int] = None, week: Optional[int] = None, tab: str = "foryou",
                team: Optional[str] = None, trend: Optional[str] = None, limit: int = 30, offset: int = 0):
    """Home feed for a week (default: the latest week with posts): top-level posts, For You (engagement with a mild
    recency weight) or Latest. team=ABBR and trend=KIND:KEY filter it."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            return social_api.feed(cur, season, week, tab, team, trend, limit, offset)


@app.get("/api/social/post/{post_id}")
def social_post(post_id: int):
    """One post with its parent (for a reply) and its replies, oldest first."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            return social_api.post_detail(cur, post_id)


@app.get("/api/social/profile/{handle}")
def social_profile(handle: str, season: Optional[int] = None, limit: int = 40, offset: int = 0):
    """An account (by handle, with or without @) and its posts this season, newest first."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            return social_api.profile(cur, handle, season, limit, offset)


@app.get("/api/social/trends")
def social_trends(season: Optional[int] = None, week: Optional[int] = None, limit: int = 20):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            return social_api.trends(cur, season, week, limit)


@app.get("/api/social/search")
def social_search(q: str = "", season: Optional[int] = None, limit: int = 40):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            return social_api.search(cur, q, season, limit)


@app.get("/api/social/suggestions")
def social_suggestions(season: Optional[int] = None, week: Optional[int] = None, limit: int = 3):
    """Who to follow: the accounts whose posts drew the most likes this week."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            return social_api.suggestions(cur, season, week, limit)


@app.get("/api/social/team/{abbr}")
def social_team(abbr: str, season: Optional[int] = None, week: Optional[int] = None, tab: str = "latest",
                limit: int = 40, offset: int = 0):
    """A Team Hub feed. Viewing it starts the team's local wakeup, like the team page does."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            data = social_api.team_hub(cur, abbr, season, week, tab, limit, offset)
            if "team" in data:
                data["wakeup_started"] = maybe_start_team_wakeup(
                    cur, data["team"]["id"], data["season"], label=data["team"]["abbr"])
            return data

# The built site (Mu_Network/dist, `npm run build`) at /, so a phone on the home network can open
# http://<Mac's IP>:8000/#debug through the API's port (the macOS firewall allows Python, not node).
# Mounted last so it never shadows an /api or /static route.
_SITE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Mu_Network", "dist")
if os.path.isdir(_SITE_DIR):
    app.mount("/", StaticFiles(directory=_SITE_DIR, html=True), name="site")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
