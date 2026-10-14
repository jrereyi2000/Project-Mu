import os
import json
import psycopg
import redis
from typing import List, Optional, Any
from pydantic import BaseModel
from pipeline.ocr_extractor import PlayerStat, TeamStat, GameMeta, RosterPlayer

class DatabaseHandler:
    def __init__(self, db_conn_info: str = None, redis_url: str = None):
        self.db_conn_info = db_conn_info or os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
        self.redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self.redis_client = redis.Redis.from_url(self.redis_url)

    def _get_or_create_team(self, cur, team_name: str) -> int:
        # First check exact name
        cur.execute("SELECT id FROM teams WHERE name ILIKE %s", (team_name,))
        row = cur.fetchone()
        if row: return row[0]
        
        # Then check abbreviation
        cur.execute("SELECT id FROM teams WHERE abbreviation ILIKE %s", (team_name,))
        row = cur.fetchone()
        if row: return row[0]
        
        # Then check substring (e.g. if Gemini extracts "San Francisco 49ers" and the DB is just "49ers")
        cur.execute("SELECT id FROM teams WHERE name ILIKE %s OR %s ILIKE '%%' || name || '%%'", (f"%{team_name}%", team_name))
        row = cur.fetchone()
        if row: return row[0]
        
        cur.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id", (team_name,))
        return cur.fetchone()[0]

    def get_current_league_state(self) -> tuple[int, int]:
        """(current_season, current_week) from league_state. The capture loader keeps both current
        (pipeline/load_capture.py sets current_week from the captured week label); nothing here
        infers or writes them."""
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1;")
                res = cur.fetchone()
                if not res:
                    raise RuntimeError("league_state has no row: set current_season and current_week first")
                return res[0], res[1]

    def create_active_game(self) -> int:
        season, week = self.get_current_league_state()
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO games (season, week) VALUES (%s, %s) RETURNING id;", (season, week))
                return cur.fetchone()[0]

    def update_game_meta(self, temp_game_id: int, game_meta: GameMeta) -> int:
        season, week = self.get_current_league_state()
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                home_team_id = self._get_or_create_team(cur, game_meta.home_team)
                away_team_id = self._get_or_create_team(cur, game_meta.away_team)
                
                # Find the real scheduled game
                cur.execute("""
                    SELECT id FROM games 
                    WHERE season = %s AND week = %s AND home_team_id = %s AND away_team_id = %s
                """, (season, week, home_team_id, away_team_id))
                
                row = cur.fetchone()
                
                if row:
                    real_game_id = row[0]
                    # The game was pre-scheduled! Migrate all stats from the temporary game to the real one
                    cur.execute("UPDATE player_stats SET game_id = %s WHERE game_id = %s", (real_game_id, temp_game_id))
                    cur.execute("UPDATE team_stats SET game_id = %s WHERE game_id = %s", (real_game_id, temp_game_id))
                    
                    # Update the real scheduled game with final scores and set to COMPLETED
                    cur.execute("""
                        UPDATE games 
                        SET home_score = %s, away_score = %s, status = 'COMPLETED'
                        WHERE id = %s;
                    """, (game_meta.home_score, game_meta.away_score, real_game_id))
                    
                    # Delete the temporary placeholder game
                    if real_game_id != temp_game_id:
                        cur.execute("DELETE FROM games WHERE id = %s;", (temp_game_id,))
                        
                    conn.commit()
                    return real_game_id
                else:
                    # No scheduled game found (maybe a playoff game?). Just update the temporary game
                    cur.execute("""
                        UPDATE games 
                        SET home_team_id = %s, away_team_id = %s, home_score = %s, away_score = %s, status = 'COMPLETED'
                        WHERE id = %s;
                    """, (home_team_id, away_team_id, game_meta.home_score, game_meta.away_score, temp_game_id))
                    
                    conn.commit()
                    return temp_game_id


    def upsert_schedule(self, extraction: Any):
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                for game in extraction.games:
                    cur.execute("SELECT id FROM teams WHERE name ILIKE %s OR abbreviation ILIKE %s LIMIT 1", (f"%{game.home_team}%", f"%{game.home_team}%"))
                    home_res = cur.fetchone()
                    cur.execute("SELECT id FROM teams WHERE name ILIKE %s OR abbreviation ILIKE %s LIMIT 1", (f"%{game.away_team}%", f"%{game.away_team}%"))
                    away_res = cur.fetchone()
                    
                    if home_res and away_res:
                        # UPDATE existing scheduled game
                        cur.execute("""
                            UPDATE games 
                            SET away_score = %s, home_score = %s, status = %s
                            WHERE season = %s AND week = %s AND home_team_id = %s AND away_team_id = %s
                        """, (game.away_score, game.home_score, game.status, extraction.season, extraction.week, home_res[0], away_res[0]))
            conn.commit()

    def upsert_roster(self, roster_list, update_only: bool = False):
        if not roster_list: return
        season, _ = self.get_current_league_state()
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                for p in roster_list:
                    team_id = self._get_or_create_team(cur, p.team_name)
                    safe_full_name = p.full_name if p.full_name and p.full_name.strip() != "" else None
                    
                    # Find if this exact player is already logged for this team this season
                    # Added position matching to differentiate name collisions (e.g., two J. Smiths on the same team)
                    cur.execute("""
                        SELECT p.id FROM players p
                        JOIN player_seasons ps ON p.id = ps.player_id
                        WHERE p.player_name = %s AND ps.team_id = %s AND ps.season = %s
                        AND (ps.position = %s OR ps.position IS NULL OR ps.position = '')
                    """, (p.player_name, team_id, season, p.position))
                    
                    row = cur.fetchone()
                    # Try to fetch height/weight/experience safely if they exist on the model
                    p_height = getattr(p, 'height', None)
                    p_weight = getattr(p, 'weight', None)
                    p_exp = getattr(p, 'years_of_experience', None)

                    if row:
                        player_id = row[0]
                        cur.execute("UPDATE players SET full_name = COALESCE(NULLIF(%s, ''), full_name), height = COALESCE(%s, height), weight = COALESCE(%s, weight) WHERE id = %s", (safe_full_name, p_height, p_weight, player_id))
                        
                        cur.execute("""
                            INSERT INTO player_seasons (player_id, team_id, season, position, age, overall_rating, years_of_experience)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (player_id, season) DO UPDATE SET
                                team_id = EXCLUDED.team_id,
                                position = EXCLUDED.position,
                                age = EXCLUDED.age,
                                overall_rating = EXCLUDED.overall_rating,
                                years_of_experience = EXCLUDED.years_of_experience;
                        """, (player_id, team_id, season, p.position, p.age, p.overall_rating, p_exp))
                    else:
                        if update_only:
                            continue # SKIP inserting new players
                        else:
                            # New player or traded player
                            cur.execute("INSERT INTO players (player_name, full_name, height, weight) VALUES (%s, %s, %s, %s) RETURNING id", (p.player_name, safe_full_name, p_height, p_weight))
                            player_id = cur.fetchone()[0]
                            
                            cur.execute("""
                                INSERT INTO player_seasons (player_id, team_id, season, position, age, overall_rating, years_of_experience)
                                VALUES (%s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (player_id, season) DO UPDATE SET
                                    team_id = EXCLUDED.team_id,
                                    position = EXCLUDED.position,
                                    age = EXCLUDED.age,
                                    overall_rating = EXCLUDED.overall_rating,
                                    years_of_experience = EXCLUDED.years_of_experience;
                            """, (player_id, team_id, season, p.position, p.age, p.overall_rating, p_exp))
            conn.commit()

    def upsert_team_stats(self, game_id: int, team_stats_list: List[TeamStat]):
        if not team_stats_list: return
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                ts_to_insert = []
                for ts in team_stats_list:
                    ts_to_insert.append((
                        game_id, ts.team_name, ts.score, ts.first_downs, ts.total_offense,
                        ts.total_plays, ts.rushing_attempts, ts.rushing_yards, ts.rushing_tds,
                        ts.passing_completions, ts.passing_attempts, ts.passing_yards, ts.passing_tds,
                        ts.third_down_attempts, ts.third_down_conversions,
                        ts.fourth_down_attempts, ts.fourth_down_conversions, ts.turnovers
                    ))
                cur.executemany(
                    """
                    INSERT INTO team_stats (
                        game_id, team_name, score, first_downs, total_offense, total_plays,
                        rushing_attempts, rushing_yards, rushing_tds, passing_completions,
                        passing_attempts, passing_yards, passing_tds, third_down_attempts,
                        third_down_conversions, fourth_down_attempts, fourth_down_conversions, turnovers
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (game_id, team_name) 
                    DO UPDATE SET
                        score = GREATEST(team_stats.score, EXCLUDED.score),
                        first_downs = GREATEST(team_stats.first_downs, EXCLUDED.first_downs),
                        total_offense = GREATEST(team_stats.total_offense, EXCLUDED.total_offense),
                        total_plays = GREATEST(team_stats.total_plays, EXCLUDED.total_plays),
                        rushing_attempts = GREATEST(team_stats.rushing_attempts, EXCLUDED.rushing_attempts),
                        rushing_yards = GREATEST(team_stats.rushing_yards, EXCLUDED.rushing_yards),
                        rushing_tds = GREATEST(team_stats.rushing_tds, EXCLUDED.rushing_tds),
                        passing_completions = GREATEST(team_stats.passing_completions, EXCLUDED.passing_completions),
                        passing_attempts = GREATEST(team_stats.passing_attempts, EXCLUDED.passing_attempts),
                        passing_yards = GREATEST(team_stats.passing_yards, EXCLUDED.passing_yards),
                        passing_tds = GREATEST(team_stats.passing_tds, EXCLUDED.passing_tds),
                        third_down_attempts = GREATEST(team_stats.third_down_attempts, EXCLUDED.third_down_attempts),
                        third_down_conversions = GREATEST(team_stats.third_down_conversions, EXCLUDED.third_down_conversions),
                        fourth_down_attempts = GREATEST(team_stats.fourth_down_attempts, EXCLUDED.fourth_down_attempts),
                        fourth_down_conversions = GREATEST(team_stats.fourth_down_conversions, EXCLUDED.fourth_down_conversions),
                        turnovers = GREATEST(team_stats.turnovers, EXCLUDED.turnovers);
                    """,
                    ts_to_insert
                )

    def upsert_player_stats(self, game_id: int, player_stats_list: List[PlayerStat]):
        if not player_stats_list: return
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT season FROM games WHERE id = %s", (game_id,))
                row = cur.fetchone()
                season = row[0] if row else self.get_current_league_state()[0]
                
                for p in player_stats_list:
                    # Ignore empty stat rows
                    if p.attempts == 0 and p.carries == 0 and p.receptions == 0 and p.fumbles == 0 and p.interceptions == 0:
                        continue

                    # Smart Team Resolution
                    team_id = None
                    gemini_team_id = self._get_or_create_team(cur, p.team) if p.team and p.team.strip() != "" else None
                    
                    # 1. Try to resolve by Name + Position from the roster index
                    if p.position and str(p.position).strip() != "":
                        cur.execute("""
                            SELECT ps.team_id FROM player_seasons ps
                            JOIN players pl ON ps.player_id = pl.id
                            WHERE pl.player_name = %s AND ps.position = %s AND ps.season = %s
                        """, (p.player_name, p.position, season))
                        rows = cur.fetchall()
                        if len(rows) == 1:
                            team_id = rows[0][0]
                            
                    # 2. Try to resolve by Name + Gemini's extracted Team
                    if team_id is None and gemini_team_id is not None:
                        cur.execute("""
                            SELECT ps.team_id FROM player_seasons ps
                            JOIN players pl ON ps.player_id = pl.id
                            WHERE pl.player_name = %s AND ps.team_id = %s AND ps.season = %s
                        """, (p.player_name, gemini_team_id, season))
                        rows = cur.fetchall()
                        if len(rows) >= 1:
                            team_id = gemini_team_id
                            
                    # 3. Fallback: Just trust Gemini's team extraction
                    if team_id is None and gemini_team_id is not None:
                        team_id = gemini_team_id

                    # Auto-create missing player in central roster index if they don't exist
                    cur.execute("INSERT INTO players (player_name) SELECT %s WHERE NOT EXISTS (SELECT 1 FROM players WHERE player_name = %s) RETURNING id;", (p.player_name, p.player_name))
                    p_row = cur.fetchone()
                    if p_row:
                        player_id = p_row[0]
                    else:
                        cur.execute("SELECT id FROM players WHERE player_name = %s LIMIT 1", (p.player_name,))
                        player_id = cur.fetchone()[0]
                    cur.execute("INSERT INTO player_seasons (player_id, season, team_id, position) VALUES (%s, %s, %s, %s) ON CONFLICT (player_id, season) DO NOTHING;", (player_id, season, team_id, p.position))


                    
                    # Smart Position Resolution
                    resolved_position = p.position
                    if not resolved_position or str(resolved_position).strip() == "" or str(resolved_position).strip() in ["QB", "HB", "WR", "TE"]:
                        # Always trust the global roster index over Gemini's guess
                        cur.execute("SELECT ps.position FROM player_seasons ps JOIN players pl ON ps.player_id = pl.id WHERE pl.player_name = %s AND ps.team_id = %s AND ps.season = %s AND ps.position IS NOT NULL AND ps.position != ''", (p.player_name, team_id, season))
                        pos_rows = cur.fetchall()
                        if len(pos_rows) == 1:
                            resolved_position = pos_rows[0][0]
                        elif len(pos_rows) > 1:
                            # Name collision! (e.g. two R. Keatons)
                            if p.passing_yards > 0: resolved_position = "QB"
                            elif p.carries > 0: resolved_position = "HB"
                            elif p.receptions > 0: resolved_position = "WR"
                            else: resolved_position = pos_rows[0][0]
                            
                    cur.execute(
                        """
                        INSERT INTO player_stats (
                            game_id, player_name, team_id, position,
                            completions, pass_attempts, pass_yards, pass_tds, interceptions,
                            rush_attempts, rush_yards, rush_tds,
                            receptions, rec_yards, rec_tds, fumb
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (game_id, player_name, position) 
                        DO UPDATE SET 
                            team_id = COALESCE(EXCLUDED.team_id, player_stats.team_id),
                            completions = GREATEST(player_stats.completions, EXCLUDED.completions),
                            pass_attempts = GREATEST(player_stats.pass_attempts, EXCLUDED.pass_attempts),
                            pass_yards = GREATEST(player_stats.pass_yards, EXCLUDED.pass_yards),
                            pass_tds = GREATEST(player_stats.pass_tds, EXCLUDED.pass_tds),
                            interceptions = GREATEST(player_stats.interceptions, EXCLUDED.interceptions),
                            rush_attempts = GREATEST(player_stats.rush_attempts, EXCLUDED.rush_attempts),
                            rush_yards = GREATEST(player_stats.rush_yards, EXCLUDED.rush_yards),
                            rush_tds = GREATEST(player_stats.rush_tds, EXCLUDED.rush_tds),
                            receptions = GREATEST(player_stats.receptions, EXCLUDED.receptions),
                            rec_yards = GREATEST(player_stats.rec_yards, EXCLUDED.rec_yards),
                            rec_tds = GREATEST(player_stats.rec_tds, EXCLUDED.rec_tds),
                            fumb = GREATEST(player_stats.fumb, EXCLUDED.fumb);
                        """,
                        (
                            game_id, p.player_name, team_id, resolved_position,
                            p.completions, p.attempts, p.passing_yards, p.passing_touchdowns, p.interceptions,
                            p.carries, p.rushing_yards, p.rushing_touchdowns,
                            p.receptions, p.receiving_yards, p.receiving_tds, p.fumbles
                        )
                    )
                conn.commit()

    def upsert_season_stats(self, player_stats_list: List[PlayerStat]):
        if not player_stats_list: return
        season, week = self.get_current_league_state()
        with psycopg.connect(self.db_conn_info) as conn:
            with conn.cursor() as cur:
                for p in player_stats_list:
                    # Ignore empty stat rows
                    if p.attempts == 0 and p.carries == 0 and p.receptions == 0 and p.fumbles == 0 and p.interceptions == 0:
                        continue

                    # Smart Team Resolution
                    team_id = None
                    gemini_team_id = self._get_or_create_team(cur, p.team) if p.team and p.team.strip() != "" else None
                    
                    # 1. Try to resolve by Name + Position from the roster index
                    if p.position and str(p.position).strip() != "":
                        cur.execute("""
                            SELECT ps.team_id FROM player_seasons ps
                            JOIN players pl ON ps.player_id = pl.id
                            WHERE pl.player_name = %s AND ps.position = %s AND ps.season = %s
                        """, (p.player_name, p.position, season))
                        rows = cur.fetchall()
                        if len(rows) == 1:
                            team_id = rows[0][0]
                            
                    # 2. Try to resolve by Name + Gemini's extracted Team
                    if team_id is None and gemini_team_id is not None:
                        cur.execute("""
                            SELECT ps.team_id FROM player_seasons ps
                            JOIN players pl ON ps.player_id = pl.id
                            WHERE pl.player_name = %s AND ps.team_id = %s AND ps.season = %s
                        """, (p.player_name, gemini_team_id, season))
                        rows = cur.fetchall()
                        if len(rows) >= 1:
                            team_id = gemini_team_id
                            
                    # 3. Fallback: Just trust Gemini's team extraction
                    if team_id is None and gemini_team_id is not None:
                        team_id = gemini_team_id

                    # Auto-create missing player in central roster index if they don't exist
                    cur.execute("INSERT INTO players (player_name) SELECT %s WHERE NOT EXISTS (SELECT 1 FROM players WHERE player_name = %s) RETURNING id;", (p.player_name, p.player_name))
                    p_row = cur.fetchone()
                    if p_row:
                        player_id = p_row[0]
                    else:
                        cur.execute("SELECT id FROM players WHERE player_name = %s LIMIT 1", (p.player_name,))
                        player_id = cur.fetchone()[0]
                    cur.execute("INSERT INTO player_seasons (player_id, season, team_id, position) VALUES (%s, %s, %s, %s) ON CONFLICT (player_id, season) DO NOTHING;", (player_id, season, team_id, p.position))


                    
                    # Smart Position Resolution
                    resolved_position = p.position
                    if not resolved_position or str(resolved_position).strip() == "":
                        cur.execute("SELECT ps.position FROM player_seasons ps JOIN players pl ON ps.player_id = pl.id WHERE pl.player_name = %s AND ps.team_id = %s AND ps.season = %s AND ps.position IS NOT NULL AND ps.position != ''", (p.player_name, team_id, season))
                        pos_rows = cur.fetchall()
                        if len(pos_rows) == 1:
                            resolved_position = pos_rows[0][0]
                        elif len(pos_rows) > 1:
                            # Name collision! (e.g. two R. Keatons)
                            if p.passing_yards > 0: resolved_position = "QB"
                            elif p.carries > 0: resolved_position = "HB"
                            elif p.receptions > 0: resolved_position = "WR"
                            else: resolved_position = pos_rows[0][0]
                            
                    cur.execute(
                        """
                        INSERT INTO season_stats (
                            season, player_name, team_id, position,
                            completions, pass_attempts, pass_yards, pass_tds, interceptions,
                            rush_attempts, rush_yards, rush_tds, 
                            receptions, rec_yards, rec_tds, fumb
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (season, player_name, team_id, position) 
                        DO UPDATE SET 
                            completions = GREATEST(season_stats.completions, EXCLUDED.completions),
                            pass_attempts = GREATEST(season_stats.pass_attempts, EXCLUDED.pass_attempts),
                            pass_yards = GREATEST(season_stats.pass_yards, EXCLUDED.pass_yards),
                            pass_tds = GREATEST(season_stats.pass_tds, EXCLUDED.pass_tds),
                            interceptions = GREATEST(season_stats.interceptions, EXCLUDED.interceptions),
                            rush_attempts = GREATEST(season_stats.rush_attempts, EXCLUDED.rush_attempts),
                            rush_yards = GREATEST(season_stats.rush_yards, EXCLUDED.rush_yards),
                            rush_tds = GREATEST(season_stats.rush_tds, EXCLUDED.rush_tds),
                            receptions = GREATEST(season_stats.receptions, EXCLUDED.receptions),
                            rec_yards = GREATEST(season_stats.rec_yards, EXCLUDED.rec_yards),
                            rec_tds = GREATEST(season_stats.rec_tds, EXCLUDED.rec_tds),
                            fumb = GREATEST(season_stats.fumb, EXCLUDED.fumb);
                        """,
                        (
                            season, p.player_name, team_id, resolved_position,
                            p.completions, p.attempts, p.passing_yards, p.passing_touchdowns, p.interceptions,
                            p.carries, p.rushing_yards, p.rushing_touchdowns, 
                            p.receptions, p.receiving_yards, p.receiving_tds, p.fumbles
                        )
                    )
                conn.commit()

    def emit_game_completed(self, game_id: int):
        event_payload = json.dumps({"event": "GAME_COMPLETED", "game_id": game_id})
        self.redis_client.publish("madden_events", event_payload)
        print(f"[DB_Handler] Event Emitted: GAME_COMPLETED for game_id {game_id}")
