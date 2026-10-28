-- Progression phase C2, first part (docs/in_progress/PROGRESSION_PLAN.md §5.4, §5.6; PROJECT_SPEC §2.21, §3.3n):
-- the stored cause tag, and the career-peak and draft-day mental ratings the peak-based mental decline needs.
--
-- Apply with:
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_01_11_progression_rating_groups.sql

BEGIN;

ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS cause VARCHAR(32);  -- main cause tag (§5.6)

ALTER TABLE player_ceilings ADD COLUMN IF NOT EXISTS peak_mental JSONB;          -- career-high Awareness, PlayRecognition
ALTER TABLE player_ceilings ADD COLUMN IF NOT EXISTS entry_mental JSONB;         -- draft-day Awareness, PlayRecognition
ALTER TABLE player_ceilings ADD COLUMN IF NOT EXISTS entry_source VARCHAR(16);   -- ESTIMATED (Madden's mean gain) | SAVE

COMMIT;
