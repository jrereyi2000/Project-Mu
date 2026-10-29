# @AUDIT-STATUS: PRUNE_FILE (v0)
# @REPLACED-BY: db/init_db.sql (one-off schema script for the original database)
import psycopg

with psycopg.connect("host=localhost dbname=madden user=postgres password=postgres") as conn:
    with conn.cursor() as cur:
        # 1. Rename columns to match the new schema
        cur.execute("ALTER TABLE player_stats RENAME COLUMN passing_yards TO pass_yards;")
        cur.execute("ALTER TABLE player_stats RENAME COLUMN passing_touchdowns TO pass_tds;")
        cur.execute("ALTER TABLE player_stats RENAME COLUMN rushing_yards TO rush_yards;")
        cur.execute("ALTER TABLE player_stats RENAME COLUMN rushing_touchdowns TO rush_tds;")
        cur.execute("ALTER TABLE player_stats RENAME COLUMN receiving_yards TO rec_yards;")
        cur.execute("ALTER TABLE player_stats RENAME COLUMN receiving_tds TO rec_tds;")
        cur.execute("ALTER TABLE player_stats RENAME COLUMN attempts TO pass_attempts;")
        cur.execute("ALTER TABLE player_stats RENAME COLUMN carries TO rush_attempts;")
        
        # 2. Add team_id column
        cur.execute("ALTER TABLE player_stats ADD COLUMN team_id INT REFERENCES teams(id);")
        
        # 3. Migrate data: Link team string to team_id (or fallback to player_seasons via game's season)
        cur.execute("""
            UPDATE player_stats ps
            SET team_id = COALESCE(
                (SELECT id FROM teams t WHERE LOWER(t.name) = LOWER(ps.team) LIMIT 1),
                (SELECT pls.team_id FROM player_seasons pls 
                 JOIN players p ON pls.player_id = p.id
                 JOIN games g ON ps.game_id = g.id
                 WHERE p.player_name = ps.player_name AND pls.season = g.season LIMIT 1)
            );
        """)
        
        # 4. Drop old columns
        cur.execute("ALTER TABLE player_stats DROP COLUMN team;")
        cur.execute("ALTER TABLE player_stats DROP COLUMN fumbles;")
        
    conn.commit()
print("Successfully migrated player_stats schema!")
