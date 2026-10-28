-- Player progression engine (docs/PROGRESSION_PLAN.md §5.6; PROJECT_SPEC §2, §3; PRE_PC_WORK.md §11).
-- Stores per-player yearly progression breakdown (OVR delta terms and ratings diffs).
--
-- Apply with:
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_01_08_player_progression.sql

BEGIN;

CREATE TABLE IF NOT EXISTS player_progression_history (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    old_ovr INT NOT NULL,
    new_ovr INT NOT NULL,
    delta_ovr INT NOT NULL,
    age_term NUMERIC(4, 2) NOT NULL DEFAULT 0,
    dev_term NUMERIC(4, 2) NOT NULL DEFAULT 0,
    ceiling_term NUMERIC(4, 2) NOT NULL DEFAULT 0,
    play_term NUMERIC(4, 2) NOT NULL DEFAULT 0,
    injury_term NUMERIC(4, 2) NOT NULL DEFAULT 0,
    noise_term NUMERIC(4, 2) NOT NULL DEFAULT 0,
    ratings_diff JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (season, player_id)
);

CREATE INDEX IF NOT EXISTS idx_player_progression_season ON player_progression_history (season);
CREATE INDEX IF NOT EXISTS idx_player_progression_player ON player_progression_history (player_id);

CREATE TABLE IF NOT EXISTS player_ceilings (
    player_id INT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    ceiling NUMERIC(5, 2) NOT NULL,
    source VARCHAR(32) NOT NULL,
    updated_season INT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_player_ceilings_season ON player_ceilings (updated_season);

COMMIT;
