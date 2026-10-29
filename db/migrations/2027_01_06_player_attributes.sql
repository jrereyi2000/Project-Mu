-- @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (v2-teleport schema, PS5 attribute scan)
-- @REPLACED-BY: NONE (feature dropped with the 2026-2036 history; drop the tables from db/init_db.sql at prune time; AUDIT_LEDGER.md §3b)
-- PS5 attribute scan (docs/in_progress/PC_MIGRATION_PLAN.md §2.3; PROJECT_SPEC §3.1): every player's 54 ratings read off the
-- wide Manage Roster > Roster list by madden_capture.py --only attributes and pipeline/attribute_reader.py, loaded
-- by pipeline/load_attributes.py. One row per player per scan (capture folder), so scans can be repeated and
-- compared; the teleporter takes a player's latest scan of the season.
--   ratings      {"Speed": 99, "Acceleration": 97, ...}: Madden 27 Player.<field>Rating names, 0-99
--   overall_rating the OVR the list showed; predicted_ovr / ovr_check the engines/ovr_weights check
--   confirmed    ratings read alike in two sweeps (of 54)
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
