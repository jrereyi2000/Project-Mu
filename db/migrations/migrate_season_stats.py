# @AUDIT-STATUS: PRUNE_FILE (v0)
# @REPLACED-BY: db/init_db.sql (one-off schema script for the original database)
import psycopg

with psycopg.connect("host=localhost dbname=madden user=postgres password=postgres") as conn:
    with conn.cursor() as cur:
        # 1. Rename columns to match the new schema
        cur.execute("ALTER TABLE season_stats RENAME COLUMN passing_yards TO pass_yards;")
        cur.execute("ALTER TABLE season_stats RENAME COLUMN passing_touchdowns TO pass_tds;")
        cur.execute("ALTER TABLE season_stats RENAME COLUMN rushing_yards TO rush_yards;")
        cur.execute("ALTER TABLE season_stats RENAME COLUMN rushing_touchdowns TO rush_tds;")
        cur.execute("ALTER TABLE season_stats RENAME COLUMN receiving_yards TO rec_yards;")
        cur.execute("ALTER TABLE season_stats RENAME COLUMN receiving_tds TO rec_tds;")
        cur.execute("ALTER TABLE season_stats RENAME COLUMN last_updated_week TO week;")
        
        # 2. Add team_id column
        cur.execute("ALTER TABLE season_stats ADD COLUMN team_id INT REFERENCES teams(id);")
        
        # 3. Migrate data: Link team string to team_id (or fallback to player_seasons)
        cur.execute("""
            UPDATE season_stats ss
            SET team_id = COALESCE(
                (SELECT id FROM teams t WHERE LOWER(t.name) = LOWER(ss.team) LIMIT 1),
                (SELECT ps.team_id FROM player_seasons ps 
                 JOIN players p ON ps.player_id = p.id 
                 WHERE p.player_name = ss.player_name AND ps.season = ss.season LIMIT 1)
            );
        """)
        
        # 4. Drop old columns
        cur.execute("ALTER TABLE season_stats DROP CONSTRAINT IF EXISTS season_stats_season_player_name_key;")
        cur.execute("ALTER TABLE season_stats DROP COLUMN team;")
        cur.execute("ALTER TABLE season_stats DROP COLUMN completions;")
        cur.execute("ALTER TABLE season_stats DROP COLUMN attempts;")
        cur.execute("ALTER TABLE season_stats DROP COLUMN carries;")
        cur.execute("ALTER TABLE season_stats DROP COLUMN fumbles;")
        cur.execute("ALTER TABLE season_stats DROP COLUMN receptions;")
        
        # 5. Add new unique constraint
        cur.execute("ALTER TABLE season_stats ADD UNIQUE (season, week, player_name, team_id);")
        
    conn.commit()
print("Successfully migrated season_stats schema!")
