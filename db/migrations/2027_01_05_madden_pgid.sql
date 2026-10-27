-- Madden's player ID on the PC franchise save (docs/PC_MIGRATION_PLAN.md §4.5; PROJECT_SPEC §3.1).
-- players.madden_pgid = Player.PresentationId from pipeline/save_reader, unique per save. Written by
-- pipeline/load_save.py for every player it loads (and later by the teleporter); NULL for players never seen
-- in a PC save. Project Mu's own players.id stays the primary key.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_01_05_madden_pgid.sql

BEGIN;

ALTER TABLE players ADD COLUMN IF NOT EXISTS madden_pgid INT;
CREATE UNIQUE INDEX IF NOT EXISTS players_madden_pgid ON players (madden_pgid) WHERE madden_pgid IS NOT NULL;

COMMIT;
