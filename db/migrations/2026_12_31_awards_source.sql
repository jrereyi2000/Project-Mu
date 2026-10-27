-- 2026-2034 All-Pro teams (docs/HISTORY_BACKFILL_PLAN.md §5; PROJECT_SPEC §3.3l).
-- engines/history_backfill/all_pro.py stores generated First and Second Teams with source = 'SYNTH'; every other
-- award row stays 'CAPTURE'. `revert-players` deletes the SYNTH rows first (awards.player_id blocks deleting a player).
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_12_31_awards_source.sql

ALTER TABLE awards ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';
