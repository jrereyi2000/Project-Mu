-- Actual Round 1 draft results added to draft_prospects.
-- Adds actual_round, actual_round_pick, actual_pick, drafted_team_id, selection_notes.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_11_26_draft_selections.sql

BEGIN;

DROP TABLE IF EXISTS draft_selections CASCADE;

ALTER TABLE draft_prospects ALTER COLUMN projected_pick DROP NOT NULL;

ALTER TABLE draft_prospects 
  ADD COLUMN IF NOT EXISTS actual_round VARCHAR(8),
  ADD COLUMN IF NOT EXISTS actual_round_pick SMALLINT,
  ADD COLUMN IF NOT EXISTS actual_pick SMALLINT,
  ADD COLUMN IF NOT EXISTS drafted_team_id INT REFERENCES teams(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS selection_notes TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_draft_prospects_actual_pick 
  ON draft_prospects (draft_season, actual_pick) 
  WHERE actual_pick IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_draft_prospects_drafted_team 
  ON draft_prospects (draft_season, drafted_team_id) 
  WHERE drafted_team_id IS NOT NULL;

COMMIT;
