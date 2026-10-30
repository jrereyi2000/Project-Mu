-- V2 franchise OS core (docs/in_progress/V2_CORE_BUILD_PLAN.md §2.1, unit U1; PROJECT_SPEC §3.3b, §3.3d, §3.3n):
-- depth charts, contract instruments and offers, waivers, JR's queued moves, league office runs, weekly form,
-- retirement overrides, durability and mileage, plus the progression C2 storage columns
-- (docs/in_progress/PROGRESSION_PLAN.md §5.6).
--
-- Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_01_14_v2_franchise_os.sql

BEGIN;

-- Depth charts as Madden holds them (MADDEN), as Project Mu's coaches set them (MU_COACH) or as JR set them (JR).
CREATE TABLE IF NOT EXISTS depth_charts (
    team_id INT NOT NULL REFERENCES teams(id),
    season INT NOT NULL,
    week INT NOT NULL,
    position_slot VARCHAR(8) NOT NULL,      -- Madden's DepthChart field name: QB, HB, WR, ..., KR, PR, 3DRB, ...
    depth SMALLINT NOT NULL CHECK (depth BETWEEN 1 AND 6),
    player_id INT REFERENCES players(id),
    source VARCHAR(8) NOT NULL CHECK (source IN ('MADDEN','MU_COACH','JR')),
    PRIMARY KEY (team_id, season, week, source, position_slot, depth)
);

-- Contract instruments beyond Madden's plain deal.
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS contract_type VARCHAR(16) NOT NULL DEFAULT 'STANDARD'
    CHECK (contract_type IN ('STANDARD','ROOKIE','UDFA','FIFTH_YEAR_OPTION','FRANCHISE_EXCL','FRANCHISE_NONEXCL',
                             'TRANSITION','RFA_TENDER','ERFA_TENDER','MINIMUM'));
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS guaranteed BIGINT NOT NULL DEFAULT 0;
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS signed_season INT;
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS signed_week INT;
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS last_restructure_season INT;
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS drafted_round SMALLINT;   -- NULL = undrafted

-- Every offer or proposal: free agency, re-signing, extension, tender, JR's or a CPU team's.
CREATE TABLE IF NOT EXISTS contract_offers (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    stage VARCHAR(16) NOT NULL,             -- league_office stage name (2.6)
    wave SMALLINT,                          -- free-agency wave 1..3, NULL otherwise
    kind VARCHAR(12) NOT NULL CHECK (kind IN ('FREE_AGENT','RESIGN','EXTENSION','TENDER','UDFA')),
    player_id INT NOT NULL REFERENCES players(id),
    team_id INT NOT NULL REFERENCES teams(id),
    from_user BOOLEAN NOT NULL DEFAULT false,
    years SMALLINT NOT NULL,
    total_value BIGINT NOT NULL,
    bonus BIGINT NOT NULL DEFAULT 0,
    guaranteed BIGINT NOT NULL DEFAULT 0,
    utility NUMERIC(6,4),                   -- free_agency_engine.offer_utility, NULL until scored
    status VARCHAR(10) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN','ACCEPTED','REJECTED','COUNTERED','WITHDRAWN','EXPIRED')),
    counter JSONB,                          -- {years, total_value, bonus, guaranteed} when COUNTERED
    seed TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS contract_offers_player ON contract_offers (player_id, season, status);

CREATE TABLE IF NOT EXISTS waiver_claims (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL, week INT NOT NULL,
    player_id INT NOT NULL REFERENCES players(id),
    team_id INT NOT NULL REFERENCES teams(id),
    priority SMALLINT NOT NULL,             -- 1 = worst record
    awarded BOOLEAN NOT NULL DEFAULT false
);

-- JR's queued moves from Mu_Network, applied at the next league office write.
CREATE TABLE IF NOT EXISTS franchise_pending_moves (
    id SERIAL PRIMARY KEY,
    team_id INT NOT NULL REFERENCES teams(id),
    kind VARCHAR(16) NOT NULL CHECK (kind IN ('DEPTH_CHART','RELEASE','SIGN','TRADE','PS_PROMOTE','PS_DEMOTE',
                                              'IR_PLACE','IR_ACTIVATE','RESTRUCTURE','EXTENSION','TAG','OPTION')),
    payload JSONB NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','WRITTEN','REFUSED','CANCELLED')),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    written_run INT
);

-- One row per league office run, and per save write inside it.
CREATE TABLE IF NOT EXISTS league_office_runs (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL, week INT, stage VARCHAR(16) NOT NULL,
    save_name TEXT NOT NULL,
    status VARCHAR(10) NOT NULL CHECK (status IN ('PLANNED','WRITTEN','FAILED','DRY_RUN')),
    edits JSONB NOT NULL,                   -- the exact save_writer edits document
    summary JSONB,                          -- counts per move kind
    drift JSONB,                            -- drift_check report before the write
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS player_form (
    player_id INT NOT NULL REFERENCES players(id),
    season INT NOT NULL, week INT NOT NULL,
    form NUMERIC(4,2) NOT NULL CHECK (form BETWEEN -3 AND 3),   -- OVR points
    overlay JSONB NOT NULL,                 -- {RatingName: delta} written on top of the base ratings
    PRIMARY KEY (player_id, season, week)
);

CREATE TABLE IF NOT EXISTS retirement_decisions (
    season INT NOT NULL,
    player_id INT NOT NULL REFERENCES players(id),
    madden_retired BOOLEAN NOT NULL,
    mu_keep BOOLEAN NOT NULL,               -- Project Mu un-retires him
    reason TEXT,
    PRIMARY KEY (season, player_id)
);

ALTER TABLE injuries ADD COLUMN IF NOT EXISTS mu_weeks_out SMALLINT;   -- Project Mu's duration, written back

-- Progression C2 storage (PROGRESSION_PLAN §5.6).
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS checkpoint VARCHAR(12) NOT NULL DEFAULT 'OFFSEASON';
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS dev_before VARCHAR(16);
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS dev_after VARCHAR(16);
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS group_changes JSONB;
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS wear_term NUMERIC(6,3);
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS upkeep_term NUMERIC(6,3);
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS continuity_term NUMERIC(6,3);
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS mentor_term NUMERIC(6,3);
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS persona_term NUMERIC(6,3) NOT NULL DEFAULT 0;
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS coach_term NUMERIC(6,3) NOT NULL DEFAULT 0;
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS behaviour_changes JSONB;
ALTER TABLE player_ceilings ADD COLUMN IF NOT EXISTS floor NUMERIC(5,2);
ALTER TABLE player_ceilings ADD COLUMN IF NOT EXISTS spread NUMERIC(5,2);
ALTER TABLE player_ceilings ADD COLUMN IF NOT EXISTS group_ceilings JSONB;   -- {physical, technical, mental}
CREATE TABLE IF NOT EXISTS player_durability (
    player_id INT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    durability NUMERIC(4,3) NOT NULL CHECK (durability BETWEEN 0 AND 1),
    source VARCHAR(16) NOT NULL, updated_season INT NOT NULL
);
CREATE TABLE IF NOT EXISTS player_mileage (
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    season INT NOT NULL,
    touches INT NOT NULL DEFAULT 0, hits INT NOT NULL DEFAULT 0, snaps INT NOT NULL DEFAULT 0,
    wear NUMERIC(6,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, season)
);

COMMIT;
