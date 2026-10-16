-- Migration: Support historical rarity bonus and team records broken in coty_scores
-- Applied to madden database

ALTER TABLE coty_scores ADD COLUMN IF NOT EXISTS historical_bonus DOUBLE PRECISION DEFAULT 0.0;
ALTER TABLE coty_scores ADD COLUMN IF NOT EXISTS historical_notes TEXT[];
