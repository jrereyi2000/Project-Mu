-- Owners, scouting directors and media-voter ballots (docs/in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md §2.6;
-- PROJECT_SPEC §3). U6, built 2026-11-07.
--   team_owners      one reviewed owner per team tenure (role 'owner'; team_owners.id is the db-owner alias)
--   owner_decisions  each vetoed / pushed / approved big move, written by the owner wiring (idempotent on its
--                    unique key; loop_decision_id is the agent-loop decision when one was seen)
--   scout_directors  one generated scouting director per team tenure (role 'scout'; scout_directors.id is the
--                    db-scout alias); he stays when a GM is fired (plan Q4)
--   award_ballots    one ranking per (season, award, voter) for the six awards and the All-Pro slots
-- Needs the teams table (db/init_db.sql). Idempotent; seeds nothing; transactional; a rerun verifies the column
-- fingerprint and refuses an incompatible earlier table. Also in db/init_db.sql.
--
-- Run ./db/backup_db.sh first. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_02_02_owners_scouts_voters.sql

BEGIN;

DO $$
BEGIN
    IF to_regclass('teams') IS NULL THEN
        RAISE EXCEPTION 'owners/scouts/voters requires the teams table';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS team_owners (
    id           SERIAL PRIMARY KEY,
    team_id      INT NOT NULL REFERENCES teams(id),
    owner_name   TEXT NOT NULL,
    profile_key  TEXT NOT NULL,
    start_season INT NOT NULL,
    end_season   INT,
    UNIQUE (team_id, start_season)
);

-- move_ref is JSONB, which Postgres can't index in a plain B-tree UNIQUE constraint, so the idempotency key is a
-- unique index on its canonical text form (jsonb text is deterministic for one value).
CREATE TABLE IF NOT EXISTS owner_decisions (
    id               BIGSERIAL PRIMARY KEY,
    season           INT NOT NULL,
    tick             SMALLINT NOT NULL,
    team_id          INT NOT NULL REFERENCES teams(id),
    move_kind        TEXT NOT NULL CHECK (move_kind IN ('TRADE', 'SIGNING', 'HIRE')),
    move_ref         JSONB NOT NULL,
    decision         TEXT NOT NULL CHECK (decision IN ('APPROVE', 'VETO', 'PUSH')),
    alternative      JSONB,
    loop_decision_id BIGINT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS owner_decisions_once
    ON owner_decisions (season, tick, team_id, move_kind, (move_ref::text));

CREATE TABLE IF NOT EXISTS scout_directors (
    id           SERIAL PRIMARY KEY,
    team_id      INT NOT NULL REFERENCES teams(id),
    name         TEXT NOT NULL,
    persona_id   VARCHAR(64),
    start_season INT NOT NULL,
    end_season   INT,
    UNIQUE (team_id, start_season)
);

CREATE TABLE IF NOT EXISTS award_ballots (
    season         INT NOT NULL,
    award          TEXT NOT NULL,
    voter          TEXT NOT NULL,
    ranking        JSONB NOT NULL,
    ballot_version TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (season, award, voter)
);

DO $$
DECLARE
    got TEXT;
BEGIN
    SELECT string_agg(table_name || '.' || column_name, ',' ORDER BY table_name, column_name) INTO got
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name IN ('team_owners', 'owner_decisions', 'scout_directors', 'award_ballots');
    IF got IS DISTINCT FROM 'award_ballots.award,award_ballots.ballot_version,award_ballots.created_at,award_ballots.ranking,award_ballots.season,award_ballots.voter,owner_decisions.alternative,owner_decisions.created_at,owner_decisions.decision,owner_decisions.id,owner_decisions.loop_decision_id,owner_decisions.move_kind,owner_decisions.move_ref,owner_decisions.season,owner_decisions.team_id,owner_decisions.tick,scout_directors.end_season,scout_directors.id,scout_directors.name,scout_directors.persona_id,scout_directors.start_season,scout_directors.team_id,team_owners.end_season,team_owners.id,team_owners.owner_name,team_owners.profile_key,team_owners.start_season,team_owners.team_id' THEN
        RAISE EXCEPTION 'owners/scouts/voters schema fingerprint mismatch: %', got;
    END IF;
END $$;

COMMIT;
