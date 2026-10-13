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

from engines.gotw_engine import display_excitement

CONN_INFO = "host=localhost dbname=madden user=postgres password=postgres"

db_pool = ConnectionPool(
    conninfo=CONN_INFO,
    min_size=4,
    max_size=20,
    kwargs={"row_factory": dict_row},
    open=True
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
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

app.mount("/static", RevalidatedStaticFiles(directory=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")), name="static")

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
            if state:
                return {"season": state['current_season'], "week": state['current_week']}
            return {"season": 2035, "week": 18}

@app.get("/api/schedule")
def get_schedule(season: int = 2035, week: int = 18):
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
def get_team(abbr: str, season: int = datetime.datetime.now().year):
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
            
            # Get Roster
            cur.execute("""
                SELECT p.id, p.full_name as name, p.player_number, p.espn_id, ps.position, ps.years_of_experience, ps.overall_rating
                FROM players p
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                WHERE ps.team_id = %s
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
def get_team_schedule(abbr: str, season: int = datetime.datetime.now().year):
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

@app.get("/api/team/{abbr}/news")
def get_team_news(abbr: str, season: Optional[int] = None, limit: int = 10):
    """
    Returns news articles and beat coverage specifically tagged for or assigned to a franchise.
    Uses article_teams join or fallback to news_feed.team_id.
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

            # 2. Check if team-specific beat coverage has already been written for the active week.
            # Matchup primers may have been commissioned as national/slate slots, but local beat
            # reports (LOCAL_POSTGAME_REPORT_CARD) are only written when the team's local beat is awakened.
            target_week = curr_week if season == curr_season else None
            if target_week is not None:
                cur.execute("""
                    SELECT COUNT(*) AS cnt 
                    FROM news_feed n
                    LEFT JOIN article_teams at ON at.article_id = n.id AND at.team_id = %s
                    WHERE n.season = %s AND n.week = %s AND n.content_type = 'ARTICLE'
                      AND n.article_type = 'LOCAL_POSTGAME_REPORT_CARD'
                      AND (at.team_id = %s OR n.team_id = %s);
                """, (team_id, season, target_week, team_id, team_id))
                cnt_row = cur.fetchone()
                if not cnt_row or cnt_row["cnt"] == 0:
                    try:
                        from newsroom.swarm_dispatcher import SwarmDispatcher, build_weekly_assignment_brief
                        sd = SwarmDispatcher(debug_mode=False)
                        brief = build_weekly_assignment_brief(CONN_INFO, season=season, week=target_week)
                        sd.dispatch_team_local(
                            team_id=team_id,
                            brief=brief,
                            season=season,
                            week=target_week,
                            live=False,
                            save_to_db=True,
                            max_workers=5
                        )
                    except Exception as e:
                        print(f"Auto-wakeup failed for team {abbr} (#{team_id}): {e}")

            # 3. Query articles tagged with this team via article_teams OR news_feed.team_id
            cur.execute("""
                SELECT DISTINCT ON (n.id)
                       n.id, n.season, n.week, n.team_id, n.content_type, n.persona,
                       n.article_type, n.slot, n.headline, n.excerpt, n.content, n.created_at,
                       am.display_name, am.handle, am.archetype,
                       COALESCE(at.mention_type, 'PRIMARY') AS mention_type,
                       COALESCE(at.mention_count, 1) AS mention_count
                FROM news_feed n
                LEFT JOIN article_teams at ON at.article_id = n.id AND at.team_id = %s
                LEFT JOIN agent_memories am ON (
                    (n.team_id IS NOT NULL AND am.assigned_team_id = n.team_id AND UPPER(am.archetype) = UPPER(n.persona))
                    OR (n.team_id IS NULL AND UPPER(am.archetype) = UPPER(n.persona))
                )
                WHERE n.season = %s
                  AND n.content_type = 'ARTICLE'
                  AND (at.team_id = %s OR n.team_id = %s)
                ORDER BY n.id ASC,
                         (CASE WHEN at.mention_type = 'PRIMARY' THEN 1 
                               WHEN at.mention_type = 'FEATURED' THEN 2 
                               ELSE 3 END) ASC,
                         at.mention_count DESC,
                         n.week DESC;
            """, (team_id, season, team_id, team_id))
            raw_articles = cur.fetchall()

            # Sort by week DESC, primary mentions first
            raw_articles.sort(key=lambda a: (
                a.get("week") or 0,
                1 if a.get("mention_type") == "PRIMARY" else 0,
                a.get("mention_count") or 0
            ), reverse=True)
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
                        author_name = f"{team['name']} Beat Reporter"
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
                SELECT DISTINCT ON (ps.id) ps.*, t.abbreviation as team_abbr, p.id as player_id
                FROM player_stats ps
                JOIN teams t ON ps.team_id = t.id
                LEFT JOIN players p ON ps.player_name = p.player_name OR ps.player_name = p.full_name
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
            team_stats = cur.fetchall()
            
            return {
                "game": game,
                "stats": stats,
                "team_stats": team_stats
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
                    SELECT season FROM player_seasons
                ) AS all_seasons
                ORDER BY season DESC
            """)
            seasons = [row["season"] for row in cur.fetchall()]
            return seasons


@app.get("/api/standings/{season}")
def get_standings(season: int):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, abbreviation, conference, division FROM teams")
            teams = cur.fetchall()
            
            cur.execute("""
                SELECT home_team_id, away_team_id, home_score, away_score 
                FROM games 
                WHERE season = %s AND status = 'COMPLETED' 
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
            cur.execute("""
                SELECT p.id, p.full_name as name, p.player_number, p.height, p.weight, p.espn_id,
                       ps.position, ps.years_of_experience, ps.overall_rating,
                       t.id as team_id, t.name as team_name, t.abbreviation as team_abbr, t.team_color
                FROM players p
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                JOIN teams t ON t.id = ps.team_id
                WHERE p.id = %s
            """, (season, id))
            player = cur.fetchone()

            if not player:
                return {"error": "Player not found"}

            full_name = player['name']
            parts = full_name.split(" ")
            if len(parts) >= 2:
                madden_name = f"{parts[0][0]}. {' '.join(parts[1:])}"
            else:
                madden_name = full_name

            cur.execute("""
                WITH weekly_ranks AS (
                    SELECT g.season, g.week, st.team_id, st.player_name, ps.position,
                           (ps.position || DENSE_RANK() OVER (PARTITION BY g.season, g.week, ps.position ORDER BY st.fantasy_points DESC)) as weekly_pos_rank
                    FROM games g
                    JOIN player_stats st ON st.game_id = g.id
                    JOIN player_seasons ps ON ps.season = g.season AND ps.team_id = st.team_id AND ps.position = st.position
                    JOIN players p ON p.id = ps.player_id AND (st.player_name = p.player_name OR st.player_name = p.full_name)
                    WHERE g.season = %s AND g.status = 'COMPLETED'
                )
                SELECT g.week, g.status, g.home_team_id, g.away_team_id, g.home_score, g.away_score,
                       t_home.name as home_name, t_home.abbreviation as home_abbr,
                       t_away.name as away_name, t_away.abbreviation as away_abbr,
                       st.fantasy_points,
                       wr.weekly_pos_rank,
                       st.completions, st.pass_attempts, st.pass_yards, st.pass_tds, st.interceptions,
                       st.rush_attempts, st.rush_yards, st.rush_tds,
                       st.receptions, st.rec_yards, st.rec_tds, st.fumb
                FROM games g
                LEFT JOIN player_stats st ON st.game_id = g.id AND st.player_name = %s AND st.team_id = %s
                LEFT JOIN weekly_ranks wr ON wr.season = g.season AND wr.week = g.week AND wr.team_id = st.team_id AND wr.player_name = st.player_name
                JOIN teams t_home ON t_home.id = g.home_team_id
                JOIN teams t_away ON t_away.id = g.away_team_id
                WHERE g.season = %s AND (g.home_team_id = %s OR g.away_team_id = %s)
                ORDER BY g.week
            """, (season, madden_name, player['team_id'], season, player['team_id'], player['team_id']))
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
                    JOIN player_stats st ON st.game_id = g.id AND st.team_id = ps.team_id AND st.position = ps.position AND (st.player_name = p.player_name OR st.player_name = p.full_name)
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
                LEFT JOIN player_stats st ON st.game_id = g.id AND st.player_name = %s AND st.team_id = ps.team_id
                WHERE ps.player_id = %s
                GROUP BY ps.season, t.name, t.abbreviation, sor.total_fpts, sor.overall_rank
                ORDER BY ps.season DESC
            """, (madden_name, id))
            career = cur.fetchall()

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

            if not curr_game:
                # If no game found for current week, get the latest scheduled or last game
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

            weekly_outlook = None
            if curr_game:
                is_home = curr_game['home_team_id'] == player['team_id']
                opp_team_id = curr_game['away_team_id'] if is_home else curr_game['home_team_id']
                opp_name = curr_game['away_name'] if is_home else curr_game['home_name']
                opp_abbr = curr_game['away_abbr'] if is_home else curr_game['home_abbr']

                # Fast lookup from fantasy_projections table
                cur.execute("""
                    SELECT projected_points, avg_points, matchup_stars, pos_rank, ros_rank, ros_total_points
                    FROM fantasy_projections
                    WHERE season = %s AND week = %s AND player_id = %s;
                """, (season, curr_week, id))
                proj_record = cur.fetchone()

                if proj_record and proj_record.get('projected_points') is not None:
                    proj_pts = float(proj_record['projected_points'])
                    stars = int(proj_record['matchup_stars'] or 3)
                    pos_rank_label = proj_record['pos_rank'] or f"{player['position']}1"
                    ros_rank_label = proj_record['ros_rank'] or f"{player['position']}1"
                else:
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
                    pos_rank_label = f"{player['position']}1"
                    ros_rank_label = f"{player['position']}1"

                weekly_outlook = {
                    "week": curr_game['week'],
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
            return {
                "player": player,
                "game_log": games,
                "career_stats": career,
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

@app.get("/api/stats/player")
def get_player_stats_api(season: int = 2035, category: str = "Passing"):
    cat = category.lower()
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
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
                    JOIN player_stats st ON st.team_id = ps.team_id AND st.player_name = (
                        CASE 
                            WHEN position(' ' IN p.full_name) > 0 
                            THEN substring(p.full_name from 1 for 1) || '. ' || substring(p.full_name from position(' ' IN p.full_name) + 1)
                            ELSE p.full_name
                        END
                    )
                    JOIN games g ON g.id = st.game_id AND g.season = %s
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
                    JOIN player_stats st ON st.team_id = ps.team_id AND st.player_name = (
                        CASE 
                            WHEN position(' ' IN p.full_name) > 0 
                            THEN substring(p.full_name from 1 for 1) || '. ' || substring(p.full_name from position(' ' IN p.full_name) + 1)
                            ELSE p.full_name
                        END
                    )
                    JOIN games g ON g.id = st.game_id AND g.season = %s
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
                    JOIN player_stats st ON st.team_id = ps.team_id AND st.player_name = (
                        CASE 
                            WHEN position(' ' IN p.full_name) > 0 
                            THEN substring(p.full_name from 1 for 1) || '. ' || substring(p.full_name from position(' ' IN p.full_name) + 1)
                            ELSE p.full_name
                        END
                    )
                    JOIN games g ON g.id = st.game_id AND g.season = %s
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
                    JOIN player_stats st ON st.team_id = ps.team_id AND st.player_name = (
                        CASE 
                            WHEN position(' ' IN p.full_name) > 0 
                            THEN substring(p.full_name from 1 for 1) || '. ' || substring(p.full_name from position(' ' IN p.full_name) + 1)
                            ELSE p.full_name
                        END
                    )
                    JOIN games g ON g.id = st.game_id AND g.season = %s
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

            return []

@app.get("/api/stats/team")
def get_team_stats_api(season: int = 2035, category: str = "Passing"):
    cat = category.lower()
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
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
                WHERE g.season = %s AND g.status = 'COMPLETED'
                GROUP BY t.id, t.name, t.abbreviation
            """, (season,))
            team_rows = cur.fetchall()

            cur.execute("""
                SELECT ps.team_id, SUM(ps.interceptions) as ints, SUM(ps.fumb) as fums
                FROM player_stats ps
                JOIN games g ON g.id = ps.game_id
                WHERE g.season = %s
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


@app.get("/api/fantasy/dashboard")
def get_fantasy_dashboard_api(season: int = 2035, week: int = 18):
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
                    JOIN player_stats st ON st.game_id = g.id AND st.team_id = ps.team_id AND st.position = ps.position AND (st.player_name = p.player_name OR st.player_name = p.full_name)
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
                    SELECT player_name, team_id, adp_rank as prev_rank
                    FROM fantasy_projections
                    WHERE season = %s AND adp_rank IS NOT NULL
                )
                SELECT fp.player_id, fp.adp_rank, fp.player_name, fp.team_id, fp.position, t.abbreviation as team_abbr,
                       p.espn_id,
                       pa.prev_rank,
                       (pa.prev_rank - fp.adp_rank) as adp_delta
                FROM fantasy_projections fp
                JOIN teams t ON fp.team_id = t.id
                LEFT JOIN players p ON p.id = fp.player_id
                LEFT JOIN prev_adp pa ON pa.player_name = fp.player_name AND pa.team_id = fp.team_id
                WHERE fp.season = %s AND fp.adp_rank IS NOT NULL
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

            # Articles and takes referencing current week
            return {
                "week": week,
                "season": season,
                "hero": {
                    "label": f"WEEK {week} LINEUP ADVICE",
                    "title": f"Start 'Em, Sit 'Em: Week {week} Matchups to Exploit",
                    "excerpt": f"Six lineup decisions where opportunity, playoff stakes, and defensive tendencies point in the same direction for Week {week}.",
                    "author": "Barnwell Bot",
                    "confidence": "94%",
                    "time": "11 MIN READ",
                    "updated": "TODAY · 9:42 AM ET"
                },
                "articles": [
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
                ],
                "social_takes": [
                    {
                        "quote": f"George Crawford and Trey'Mane Davis enter Week {week} as the #1 scoring stack in the simulated league.",
                        "author": "THE VOLUME BOT",
                        "tag": "CAR • QB/WR"
                    },
                    {
                        "quote": f"Week {week} Steelers–Browns has the highest defensive volatility on the board. Start skill players with confidence.",
                        "author": "TAPE + DATA",
                        "tag": "GAME ENVIRONMENT"
                    },
                    {
                        "quote": f"Ka'Von Franklin is dominating red-zone touches with 16 rushing TDs. Must-start in Week {week}.",
                        "author": "PRIME TIME AGENT",
                        "tag": "TOUCHDOWN SIGNAL"
                    }
                ],
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
        JOIN player_seasons ps ON ps.season = g.season AND ps.team_id = st.team_id AND ps.position = st.position
        JOIN players p ON p.id = ps.player_id AND (st.player_name = p.player_name OR st.player_name = p.full_name)
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
def get_weekly_fantasy_rankings(season: int = 2035, week: int = 18, position: str = "FLEX", limit: int = 150):
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
            week_completed = False
            for g in cur.fetchall():
                opponents[g["home_team_id"]] = f"vs {display_team_abbr(g['away_abbr'])}"
                opponents[g["away_team_id"]] = f"@ {display_team_abbr(g['home_abbr'])}"
                opponent_ids[g["home_team_id"]] = g["away_team_id"]
                opponent_ids[g["away_team_id"]] = g["home_team_id"]
                week_completed = week_completed or g["status"] == "COMPLETED"

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

            # Prefer stored projections for this week
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
            if proj_rows:
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
def get_ros_fantasy_rankings(season: int = 2035, week: int = 18, position: str = "FLEX", limit: int = 150):
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
def get_adp_fantasy_rankings(season: int = 2035, position: str = "ALL", limit: int = 500):
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


@app.get("/api/fantasy/players")
def get_fantasy_search_players(season: int = 2035, min_points: float = 1.0, q: Optional[str] = None):
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.id,
                       p.full_name,
                       p.espn_id,
                       ps.position,
                       t.id AS team_id,
                       t.abbreviation AS team_abbr,
                       ROUND(SUM(st.fantasy_points), 1) AS total_fpts
                FROM games g
                JOIN player_stats st ON st.game_id = g.id
                JOIN player_seasons ps ON ps.season = g.season AND ps.team_id = st.team_id AND ps.position = st.position
                JOIN players p ON p.id = ps.player_id AND (st.player_name = p.player_name OR st.player_name = p.full_name)
                JOIN teams t ON t.id = ps.team_id
                WHERE g.season = %s AND g.status = 'COMPLETED'
                  AND ps.position IN ('QB', 'HB', 'FB', 'WR', 'TE')
                GROUP BY p.id, p.full_name, p.espn_id, ps.position, t.id, t.abbreviation
                HAVING SUM(st.fantasy_points) >= %s
                ORDER BY SUM(st.fantasy_points) DESC;
            """, (season, min_points))
            rows = cur.fetchall()

            players = []
            for r in rows:
                ui_pos = _ui_position(r["position"])
                t_abbr = display_team_abbr(r["team_abbr"])
                players.append({
                    "id": r["id"],
                    "playerId": r["id"],
                    "name": r["full_name"],
                    "position": ui_pos,
                    "team": t_abbr,
                    "fpts": float(r["total_fpts"] or 0.0),
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
def get_weekly_news(week: int, season: int = 2035):
    """
    Returns all commissioned articles for a given week with their assigned editorial slots:
    - Hero Slot (top story)
    - Secondary Slots (Slot 1, Slot 2)
    - Feature Trio (Slot 3, Slot 4, Slot 5)
    - Local Matchup Primers (Slot 6, Slot 7, Slot 8)
    - Standalone (Power Rankings)
    """
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
                elif s_lower in ("slot 1", "slot 2"):
                    slotted_response["secondary"].append(art_dict)
                elif s_lower in ("slot 3", "slot 4", "slot 5"):
                    slotted_response["features"].append(art_dict)
                elif s_lower in ("slot 6", "slot 7", "slot 8") or "local" in (a["article_type"] or "").lower():
                    slotted_response["local_primers"].append(art_dict)
                elif "standalone" in s_lower or a["article_type"] == "POWER_RANKINGS":
                    slotted_response["standalone"] = art_dict
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
    """Returns the full text and metadata of a specific article."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT n.id, n.season, n.week, n.team_id, n.content_type, n.persona,
                       n.article_type, n.slot, n.headline, n.excerpt, n.content, n.created_at,
                       am.display_name, am.handle, am.archetype
                FROM news_feed n
                LEFT JOIN agent_memories am ON UPPER(am.archetype) = UPPER(n.persona)
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
                    "display_name": article["display_name"] or article["persona"],
                    "handle": article["handle"] or f"@{article['persona']}",
                    "archetype": article["persona"]
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
                } for p in players]
            }

@app.post("/api/news/team/{team_id}/wakeup")
def trigger_team_wakeup(team_id: int, season: int = 2035, week: int = 18, live: bool = False, max_workers: int = 5):
    """
    Triggers the on-demand local swarm wakeup for a specific franchise page:
    - Wakes up all beat reporters for the team to write localized articles.
    - Wakes up local die-hard fan personas for reaction tweets.
    - Wakes up national pundits if team popularity warrants attention.
    Runs in parallel across max_workers threads.
    """
    from newsroom.swarm_dispatcher import SwarmDispatcher, build_weekly_assignment_brief
    sd = SwarmDispatcher(debug_mode=False)
    brief = build_weekly_assignment_brief(CONN_INFO, season=season, week=week)
    sd.dispatch_team_local(
        team_id=team_id,
        brief=brief,
        season=season,
        week=week,
        live=live,
        save_to_db=True,
        max_workers=max_workers
    )
    return {
        "status": "success",
        "team_id": team_id,
        "season": season,
        "week": week,
        "message": f"Team #{team_id} local media wakeup dispatched with {max_workers} parallel workers."
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)



