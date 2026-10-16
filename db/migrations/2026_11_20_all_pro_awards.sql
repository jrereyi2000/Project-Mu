-- Migration: Support All-Pro teams and multiple award winners per season in awards table
-- Replaces UNIQUE (season, award_type) with granular indexes.

-- 1. Drop existing single-winner unique constraint
ALTER TABLE awards DROP CONSTRAINT IF EXISTS awards_season_award_type_key;
DROP INDEX IF EXISTS awards_season_player_type_idx;

-- 2. Prevent duplicate award of the same type and position to the same player in the same season
-- Allows multi-position All-Pros (e.g. CB and PR, or RB and AP) while preventing duplicate selections
CREATE UNIQUE INDEX IF NOT EXISTS awards_season_player_type_idx 
    ON awards (season, award_type, position, player_id) 
    WHERE player_id IS NOT NULL;

-- 3. Maintain single-winner constraint for major player awards
CREATE UNIQUE INDEX IF NOT EXISTS awards_single_winner_player_idx 
    ON awards (season, award_type) 
    WHERE award_type IN ('MVP', 'OPOY', 'DPOY', 'OROY', 'DROY', 'SBMVP');

-- 4. Maintain single-winner constraint for Coach of the Year
CREATE UNIQUE INDEX IF NOT EXISTS awards_coach_season_type_idx 
    ON awards (season, award_type) 
    WHERE award_type = 'COTY';
