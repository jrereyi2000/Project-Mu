-- NFLPA team report cards schema (docs/in_progress/NFLPA_REPORT_CARD_PLAN.md §2.4; PROJECT_SPEC §2.4/§3).
-- U1, built 2026-11-08.
--   owner_cash_ledgers         one row per team and season: the owner's annual cash pool outside the salary cap
--                              (base budget by wealth band plus the success dividend) and how it was spent.
--   team_report_cards          one row per team and season: the eight category letter grades and the composite.
--   facility_upgrades          one row per CapEx renovation: ordered in one season, completing the next.
--   team_operating_policies    one row per team: the recurring OpEx policy toggles (travel, daycare, chef).
-- Needs the teams table (db/init_db.sql). Idempotent; seeds nothing; transactional. Also in db/init_db.sql
-- (mirrored by the orchestrator).
--
-- Run ./db/backup_db.sh first. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_02_08_nflpa_report_cards.sql

BEGIN;

DO $$
BEGIN
    IF to_regclass('teams') IS NULL THEN
        RAISE EXCEPTION 'nflpa report cards requires the teams table';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS owner_cash_ledgers (
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    wealth_band VARCHAR(12) NOT NULL CHECK (wealth_band IN ('TIER_1', 'TIER_2', 'TIER_3', 'TIER_4')),
    base_budget BIGINT NOT NULL,
    success_dividend BIGINT NOT NULL DEFAULT 0,
    total_cash_budget BIGINT NOT NULL,
    staff_payroll BIGINT NOT NULL DEFAULT 0,
    opex_committed BIGINT NOT NULL DEFAULT 0,
    capex_spent BIGINT NOT NULL DEFAULT 0,
    accumulated_reserve BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (season, team_id)
);

CREATE TABLE IF NOT EXISTS team_report_cards (
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    weight_room VARCHAR(2) NOT NULL,
    training_room VARCHAR(2) NOT NULL,
    locker_room VARCHAR(2) NOT NULL,
    food_nutrition VARCHAR(2) NOT NULL,
    team_travel VARCHAR(2) NOT NULL,
    family_treatment VARCHAR(2) NOT NULL,
    head_coach VARCHAR(2) NOT NULL,
    ownership VARCHAR(2) NOT NULL,
    overall_score NUMERIC(4,3) NOT NULL,
    overall_grade VARCHAR(2) NOT NULL,
    summary_text TEXT,
    last_renovated_season INT,
    PRIMARY KEY (season, team_id)
);

CREATE TABLE IF NOT EXISTS facility_upgrades (
    id SERIAL PRIMARY KEY,
    team_id INT NOT NULL REFERENCES teams(id),
    season_ordered INT NOT NULL,
    category VARCHAR(20) NOT NULL,
    cost_dollars BIGINT NOT NULL,
    target_grade VARCHAR(2) NOT NULL,
    completion_season INT NOT NULL,
    status VARCHAR(12) NOT NULL DEFAULT 'IN_PROGRESS' CHECK (status IN ('IN_PROGRESS', 'COMPLETED', 'CANCELLED'))
);

CREATE TABLE IF NOT EXISTS team_operating_policies (
    team_id INT PRIMARY KEY REFERENCES teams(id),
    luxury_travel_enabled BOOLEAN NOT NULL DEFAULT false,
    no_roommate_policy BOOLEAN NOT NULL DEFAULT false,
    family_daycare_enabled BOOLEAN NOT NULL DEFAULT false,
    private_chef_program BOOLEAN NOT NULL DEFAULT false,
    annual_opex_cost BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

COMMIT;
