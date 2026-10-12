import psycopg

with psycopg.connect("host=localhost dbname=madden user=postgres password=postgres") as conn:
    with conn.cursor() as cur:
        # 1. Create team_seasons
        cur.execute("""
            CREATE TABLE IF NOT EXISTS team_seasons (
                id SERIAL PRIMARY KEY,
                team_id INT REFERENCES teams(id) ON DELETE CASCADE,
                season INT NOT NULL,
                wins INT DEFAULT 0,
                losses INT DEFAULT 0,
                UNIQUE(team_id, season)
            );
        """)
        
        # 2. Migrate existing records to team_seasons
        cur.execute("SELECT current_season FROM league_state LIMIT 1;")
        current_season = cur.fetchone()[0]
        
        cur.execute("""
            INSERT INTO team_seasons (team_id, season, wins, losses)
            SELECT id, %s, wins, losses FROM teams
            ON CONFLICT DO NOTHING;
        """, (current_season,))
        
        # 3. Create player_seasons
        cur.execute("""
            CREATE TABLE IF NOT EXISTS player_seasons (
                id SERIAL PRIMARY KEY,
                player_id INT REFERENCES players(id) ON DELETE CASCADE,
                team_id INT REFERENCES teams(id) ON DELETE CASCADE,
                season INT NOT NULL,
                position VARCHAR(10),
                age INT,
                overall_rating INT,
                UNIQUE(player_id, season)
            );
        """)
        
        # 4. Migrate existing players to player_seasons
        cur.execute("""
            INSERT INTO player_seasons (player_id, team_id, season, position, age, overall_rating)
            SELECT p.id, t.id, %s, p.position, p.age, p.overall_rating
            FROM players p
            JOIN teams t ON LOWER(p.team_name) = LOWER(t.name)
            ON CONFLICT DO NOTHING;
        """, (current_season,))
        
        # 5. Update Trigger for team_seasons
        cur.execute("""
            CREATE OR REPLACE FUNCTION update_team_records()
            RETURNS TRIGGER AS $$
            BEGIN
                IF TG_OP = 'INSERT' OR 
                   (TG_OP = 'UPDATE' AND (NEW.status != OLD.status OR NEW.home_score != OLD.home_score OR NEW.away_score != OLD.away_score)) THEN
                   
                    -- Recalculate Home Team
                    INSERT INTO team_seasons (team_id, season, wins, losses)
                    VALUES (
                        NEW.home_team_id, NEW.season,
                        (SELECT COUNT(*) FROM games g WHERE g.season = NEW.season AND g.status = 'COMPLETED' AND ((g.home_team_id = NEW.home_team_id AND g.home_score > g.away_score) OR (g.away_team_id = NEW.home_team_id AND g.away_score > g.home_score))),
                        (SELECT COUNT(*) FROM games g WHERE g.season = NEW.season AND g.status = 'COMPLETED' AND ((g.home_team_id = NEW.home_team_id AND g.home_score < g.away_score) OR (g.away_team_id = NEW.home_team_id AND g.away_score < g.home_score)))
                    )
                    ON CONFLICT (team_id, season) DO UPDATE SET 
                        wins = EXCLUDED.wins, losses = EXCLUDED.losses;

                    -- Recalculate Away Team
                    INSERT INTO team_seasons (team_id, season, wins, losses)
                    VALUES (
                        NEW.away_team_id, NEW.season,
                        (SELECT COUNT(*) FROM games g WHERE g.season = NEW.season AND g.status = 'COMPLETED' AND ((g.home_team_id = NEW.away_team_id AND g.home_score > g.away_score) OR (g.away_team_id = NEW.away_team_id AND g.away_score > g.home_score))),
                        (SELECT COUNT(*) FROM games g WHERE g.season = NEW.season AND g.status = 'COMPLETED' AND ((g.home_team_id = NEW.away_team_id AND g.home_score < g.away_score) OR (g.away_team_id = NEW.away_team_id AND g.away_score < g.home_score)))
                    )
                    ON CONFLICT (team_id, season) DO UPDATE SET 
                        wins = EXCLUDED.wins, losses = EXCLUDED.losses;

                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        
        # 6. Drop the old columns
        cur.execute("ALTER TABLE players DROP CONSTRAINT IF EXISTS players_team_name_player_name_key;")
        cur.execute("ALTER TABLE players DROP COLUMN IF EXISTS team_name;")
        cur.execute("ALTER TABLE players DROP COLUMN IF EXISTS position;")
        cur.execute("ALTER TABLE players DROP COLUMN IF EXISTS age;")
        cur.execute("ALTER TABLE players DROP COLUMN IF EXISTS overall_rating;")
        
        cur.execute("ALTER TABLE teams DROP COLUMN IF EXISTS wins;")
        cur.execute("ALTER TABLE teams DROP COLUMN IF EXISTS losses;")
        
    conn.commit()
print("Migration successful!")
