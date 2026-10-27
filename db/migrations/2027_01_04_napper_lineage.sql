-- Napper league history and restart (docs/SEASON_CONTINUITY_PLAN.md phase 3; PROJECT_SPEC §3.4b, §4.4b).
-- Napper stays one-season redraft (JR 2026-10-26). A league becomes COMPLETE when its fantasy final is decided
-- (engines/napper_playoffs.sync_playoffs), which freezes every team's final record, playoff finish and rank, so an
-- old season reads the same after the NFL season rolls over. A COMPLETE league can be restarted for the new NFL
-- season (POST /api/napper/league/{id}/restart): a new league row with the same name, settings and managers,
-- linked to the old one (previous_league_id) and sharing its lineage_id, so a league's history is one query.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_01_04_napper_lineage.sql

BEGIN;

ALTER TABLE napper_leagues ADD COLUMN IF NOT EXISTS previous_league_id INT
    REFERENCES napper_leagues(id) ON DELETE SET NULL;
ALTER TABLE napper_leagues ADD COLUMN IF NOT EXISTS lineage_id INT;
ALTER TABLE napper_leagues ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
UPDATE napper_leagues SET lineage_id = id WHERE lineage_id IS NULL;
CREATE INDEX IF NOT EXISTS napper_leagues_lineage ON napper_leagues (lineage_id, season);
-- At most one successor per league.
CREATE UNIQUE INDEX IF NOT EXISTS napper_leagues_one_successor ON napper_leagues (previous_league_id)
    WHERE previous_league_id IS NOT NULL;

-- Frozen at completion: CHAMPION, RUNNER_UP, SEMIFINAL, QUARTERFINAL or MISSED, and the final rank (1 = champion).
ALTER TABLE napper_teams ADD COLUMN IF NOT EXISTS playoff_finish VARCHAR(12);
ALTER TABLE napper_teams ADD COLUMN IF NOT EXISTS final_rank SMALLINT;

COMMIT;
