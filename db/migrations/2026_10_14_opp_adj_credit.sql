-- Add Opponent-Adjusted Game Credit columns to team_seasons, written by engines/game_credit_engine.py.
-- opp_adj_credit / opp_adj_credit_recent / sos_rating are points per game vs. a league-average team;
-- expected_wins is the sum of per-game win probabilities from those ratings. NULL until the engine runs.
--
-- Idempotent. Apply with:  psql -U postgres -d madden -f db/migrations/2026_10_14_opp_adj_credit.sql

BEGIN;
ALTER TABLE team_seasons ADD COLUMN IF NOT EXISTS opp_adj_credit DOUBLE PRECISION;
ALTER TABLE team_seasons ADD COLUMN IF NOT EXISTS opp_adj_credit_recent DOUBLE PRECISION;
ALTER TABLE team_seasons ADD COLUMN IF NOT EXISTS sos_rating DOUBLE PRECISION;
ALTER TABLE team_seasons ADD COLUMN IF NOT EXISTS expected_wins DOUBLE PRECISION;
COMMIT;
