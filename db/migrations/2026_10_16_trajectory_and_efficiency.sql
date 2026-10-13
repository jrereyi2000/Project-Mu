-- Trajectory Multiplier (engines/game_credit_engine.py) and the team efficiency profile
-- (engines/team_efficiency_engine.py) on team_seasons. NULL until the engines run.
--   trajectory_multiplier: 0.80–1.20, 1.00 = steady (PROJECT_SPEC §2.4)
--   pythagorean_wins, pace_production (0–100) (§2.6)
--
-- Idempotent. Apply with:  psql -U postgres -d madden -f db/migrations/2026_10_16_trajectory_and_efficiency.sql

BEGIN;
ALTER TABLE team_seasons ADD COLUMN IF NOT EXISTS trajectory_multiplier DOUBLE PRECISION;
ALTER TABLE team_seasons ADD COLUMN IF NOT EXISTS pythagorean_wins DOUBLE PRECISION;
ALTER TABLE team_seasons ADD COLUMN IF NOT EXISTS pace_production DOUBLE PRECISION;
COMMIT;
