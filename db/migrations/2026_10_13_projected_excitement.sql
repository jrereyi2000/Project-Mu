-- Add games.projected_excitement: the pre-game projected Excitement Index (raw 0–100 scale, same as
-- excitement_index) for scheduled games, written by engines/excitement_projection.py. NULL until projected.
-- Completed games keep their last pre-game projection for later comparison with the actual index.
--
-- Idempotent. Apply with:  psql -U postgres -d madden -f db/migrations/2026_10_13_projected_excitement.sql

BEGIN;
ALTER TABLE games ADD COLUMN IF NOT EXISTS projected_excitement DOUBLE PRECISION;
COMMIT;
