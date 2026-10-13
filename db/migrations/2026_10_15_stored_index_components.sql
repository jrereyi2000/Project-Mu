-- Persist index components that the engines already calculate but used to discard, so writer tools can read them.
--   team_seasons (engines/twi_engine.py): watchability_sigma_top (σ_top, ceiling/chaos volatility), p_draft (Blue-Chip
--     Pedigree, 0–100), y_prod (Under-25 production share, %), purgatory_penalty (points subtracted from TWI).
--   player_seasons (engines/player_popularity_engine.py): spike_index (0–100), r_rating (Baseline Rating & Pedigree,
--     0–100), f_platform (Franchise Platform, 0–100), t_success (Team Success, 0–100).
-- NULL until the engines next run.
--
-- Idempotent. Apply with:  psql -U postgres -d madden -f db/migrations/2026_10_15_stored_index_components.sql

BEGIN;
ALTER TABLE team_seasons ADD COLUMN IF NOT EXISTS watchability_sigma_top DOUBLE PRECISION;
ALTER TABLE team_seasons ADD COLUMN IF NOT EXISTS p_draft DOUBLE PRECISION;
ALTER TABLE team_seasons ADD COLUMN IF NOT EXISTS y_prod DOUBLE PRECISION;
ALTER TABLE team_seasons ADD COLUMN IF NOT EXISTS purgatory_penalty DOUBLE PRECISION;
ALTER TABLE player_seasons ADD COLUMN IF NOT EXISTS spike_index DOUBLE PRECISION;
ALTER TABLE player_seasons ADD COLUMN IF NOT EXISTS r_rating DOUBLE PRECISION;
ALTER TABLE player_seasons ADD COLUMN IF NOT EXISTS f_platform DOUBLE PRECISION;
ALTER TABLE player_seasons ADD COLUMN IF NOT EXISTS t_success DOUBLE PRECISION;
COMMIT;
