-- Draft-day trades in Rounds 2-7 (docs/team_management/06_DRAFT_ROUND_TRADES_PLAN.md §5; PROJECT_SPEC §2.18, §3.3g).
-- One row per round JR started in the Draft Room (engines/draft_room.start_round), so a round that drew no trade
-- proposals still counts as started and a second click doesn't propose again.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_01_02_draft_round_starts.sql

BEGIN;
CREATE TABLE IF NOT EXISTS draft_round_starts (
    draft_season INT NOT NULL,
    round SMALLINT NOT NULL CHECK (round BETWEEN 2 AND 7),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    proposals SMALLINT NOT NULL DEFAULT 0,             -- trade proposals stored when the round started
    PRIMARY KEY (draft_season, round)
);
COMMIT;
