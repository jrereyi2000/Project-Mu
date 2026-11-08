-- Staff contracts & full control schema (docs/in_progress/STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN.md §2.4; PROJECT_SPEC §3).
-- U1, built 2026-11-08.
--   staff_contracts           one row per HC/GM/OC/DC contract: the market salary, length, remaining years,
--                             the full-control flag and the buyout owed once a person is fired. person_id is a
--                             polymorphic reference disambiguated by person_kind (HC -> coaches.id, GM ->
--                             gm_profiles.id, OC/DC -> coordinators.id), so it carries no FK.
--   full_control_history      the league-wide Godfather-offer ledger: one row per full-control offer extended,
--                             read by the 5-season cooldown governor (engines/staff_contracts_engine.py).
-- Needs the teams and coaches tables (db/init_db.sql). Idempotent; seeds nothing; transactional. Also in
-- db/init_db.sql (mirrored by the orchestrator).
--
-- Run ./db/backup_db.sh first. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_02_07_staff_contracts_and_full_control.sql

BEGIN;

DO $$
BEGIN
    IF to_regclass('teams') IS NULL THEN
        RAISE EXCEPTION 'staff contracts requires the teams table';
    END IF;
    IF to_regclass('coaches') IS NULL THEN
        RAISE EXCEPTION 'staff contracts requires the coaches table';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS staff_contracts (
    id SERIAL PRIMARY KEY,
    team_id INT NOT NULL REFERENCES teams(id),
    person_id INT NOT NULL,
    person_kind VARCHAR(6) NOT NULL CHECK (person_kind IN ('HC', 'GM', 'OC', 'DC')),
    annual_salary BIGINT NOT NULL,
    total_years SMALLINT NOT NULL,
    remaining_years SMALLINT NOT NULL,
    signed_season INT NOT NULL,
    full_control BOOLEAN NOT NULL DEFAULT false,
    buyout_owed BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS full_control_history (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    coach_id INT NOT NULL REFERENCES coaches(id),
    annual_salary BIGINT NOT NULL,
    contract_years SMALLINT NOT NULL,
    trigger_reason TEXT NOT NULL
);

COMMIT;
