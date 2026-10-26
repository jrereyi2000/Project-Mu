-- 2026-2034 team arc (docs/HISTORY_BACKFILL_PLAN.md phase 2; PROJECT_SPEC §3.3j).
-- engines/history_backfill/team_arc.py generates candidate histories into the staging tables below; `apply` copies
-- the one JR picks into league_seasons, team_season_results and coach_season_lines with source = 'SYNTH', and
-- `revert` deletes every SYNTH row. Rows the season archive writes (2035 on) stay source = 'CAPTURE'.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_12_28_history_team_arc.sql

BEGIN;

ALTER TABLE league_seasons ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';
ALTER TABLE team_season_results ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';
ALTER TABLE coach_season_lines ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';

-- One generated history (a solver run with one random seed).
CREATE TABLE IF NOT EXISTS history_candidates (
    id SERIAL PRIMARY KEY,
    seed INT NOT NULL,
    objective DOUBLE PRECISION,
    solver_status TEXT,
    pins JSONB,                                    -- data/history/pins.json as it was for this run
    report JSONB,                                  -- verify() problems (empty when every fact holds) and notes
    applied_at TIMESTAMPTZ,                        -- set by `apply`; at most one candidate applied at a time
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A candidate's team-seasons, in team_season_results' shape.
CREATE TABLE IF NOT EXISTS history_team_seasons (
    candidate_id INT NOT NULL REFERENCES history_candidates(id) ON DELETE CASCADE,
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    wins INT NOT NULL,
    losses INT NOT NULL,
    ties INT NOT NULL DEFAULT 0,
    points_for INT NOT NULL,
    points_against INT NOT NULL,
    division_place SMALLINT NOT NULL,
    division_tiebreak TEXT,                        -- GENERATED: tied on record, no games to break it
    conference_seed SMALLINT,
    playoff_result VARCHAR(16) NOT NULL,
    playoff_wins INT NOT NULL DEFAULT 0,
    playoff_losses INT NOT NULL DEFAULT 0,
    head_coach_id INT REFERENCES coaches(id) ON DELETE SET NULL,
    gm_id INT,
    PRIMARY KEY (candidate_id, season, team_id)
);

COMMIT;
