-- @AUDIT-STATUS: ACTIVE_V2 (native save ratings, 54 Madden ratings per player per season)
-- @REPLACED-BY: pipeline/load_save.py (native PC save reader extracts Player.<field>Rating)
-- 54 ratings per player per season, loaded by pipeline/load_save.py from native Madden PC save snapshots.
-- Read by engines/progression.py (fetch_player_attributes_map) for ratings-driven progression and attribute auditing.
-- One row per player per capture folder (or f"save_{season}"), populated directly without OCR.
--   ratings      {"Speed": 99, "Acceleration": 97, ...}: Madden 27 Player.<field>Rating names, 0-99
--   overall_rating the player's overall rating in the save
--   confirmed    count of ratings stored (54 for native PC save rows)
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_01_06_player_attributes.sql

BEGIN;

CREATE TABLE IF NOT EXISTS player_attributes (
    id SERIAL PRIMARY KEY,
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    season INT NOT NULL,
    team_id INT REFERENCES teams(id),
    capture_folder VARCHAR(255) NOT NULL,
    position VARCHAR(10),
    age INT,
    overall_rating INT,
    predicted_ovr INT,
    ovr_check VARCHAR(16),
    confirmed SMALLINT,
    ratings JSONB NOT NULL,
    flags TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (player_id, capture_folder));
CREATE INDEX IF NOT EXISTS player_attributes_season ON player_attributes (season, player_id);

COMMIT;
