-- Compensatory draft picks schema (docs/in_progress/COMPENSATORY_PICKS_PLAN.md §2.4; PROJECT_SPEC §3.3c/§4.6).
-- U1, built 2026-11-08.
--   draft_picks               primary key expands to (draft_season, round, original_team_id, pick_suffix) so a team
--                             can hold multiple comp picks in one round; source CHECK adds 'COMP' and 'JC2A'.
--   qualifying_free_agents     one row per QFA signing tracked for the cancellation ledger.
--   compensatory_pick_awards   the awarded CBA and JC-2A picks for a draft.
-- Needs the teams, players, coaches and draft_picks tables (db/init_db.sql). Idempotent; seeds nothing;
-- transactional. Also in db/init_db.sql (mirrored by the orchestrator).
--
-- Run ./db/backup_db.sh first. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_02_06_compensatory_draft_picks.sql

BEGIN;

DO $$
BEGIN
    IF to_regclass('draft_picks') IS NULL THEN
        RAISE EXCEPTION 'compensatory picks requires the draft_picks table';
    END IF;
END $$;

-- Multiple comp picks per round: the primary key gains pick_suffix ('' for every existing SEED/TRADE/JR row).
ALTER TABLE draft_picks DROP CONSTRAINT IF EXISTS draft_picks_pkey;
ALTER TABLE draft_picks ADD COLUMN IF NOT EXISTS pick_suffix VARCHAR(4) NOT NULL DEFAULT '';
ALTER TABLE draft_picks ADD PRIMARY KEY (draft_season, round, original_team_id, pick_suffix);

ALTER TABLE draft_picks DROP CONSTRAINT IF EXISTS draft_picks_source_check;
ALTER TABLE draft_picks ADD CONSTRAINT draft_picks_source_check
    CHECK (source IN ('SEED', 'TRADE', 'JR', 'COMP', 'JC2A'));

CREATE TABLE IF NOT EXISTS qualifying_free_agents (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    player_id INT NOT NULL REFERENCES players(id),
    old_team_id INT NOT NULL REFERENCES teams(id),
    new_team_id INT NOT NULL REFERENCES teams(id),
    signed_date TIMESTAMP WITH TIME ZONE NOT NULL,
    total_value BIGINT NOT NULL,
    years INT NOT NULL,
    apy BIGINT NOT NULL,
    snap_pct NUMERIC(4,3) DEFAULT 0.0,
    honors_points INT DEFAULT 0,
    round_tier SMALLINT CHECK (round_tier BETWEEN 3 AND 7),
    qualifies BOOLEAN NOT NULL DEFAULT true,
    canceled_by_qfa_id INT REFERENCES qualifying_free_agents(id)
);

CREATE TABLE IF NOT EXISTS compensatory_pick_awards (
    id SERIAL PRIMARY KEY,
    draft_season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    round SMALLINT NOT NULL CHECK (round BETWEEN 3 AND 7),
    slot_in_round SMALLINT NOT NULL,
    overall_pick SMALLINT NOT NULL,
    award_kind VARCHAR(6) NOT NULL CHECK (award_kind IN ('CBA', 'JC2A')),
    source_player_id INT REFERENCES players(id),
    source_coach_id INT REFERENCES coaches(id),
    reason TEXT NOT NULL
);

COMMIT;
