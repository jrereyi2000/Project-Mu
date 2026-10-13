-- Playoff Clout (engines/playoff_clout_engine.py) on teams and players, and the player popularity components
-- Awards Equity and Exposure (engines/player_popularity_engine.py). All 0–100. NULL until the engines run.
--
-- Idempotent. Apply with:  psql -U postgres -d madden -f db/migrations/2026_10_17_playoff_clout_awards_exposure.sql

BEGIN;
ALTER TABLE team_seasons ADD COLUMN IF NOT EXISTS s_playoff DOUBLE PRECISION;
ALTER TABLE player_seasons ADD COLUMN IF NOT EXISTS s_playoff DOUBLE PRECISION;
ALTER TABLE player_seasons ADD COLUMN IF NOT EXISTS a_awards DOUBLE PRECISION;
ALTER TABLE player_seasons ADD COLUMN IF NOT EXISTS e_exposure DOUBLE PRECISION;
COMMIT;
