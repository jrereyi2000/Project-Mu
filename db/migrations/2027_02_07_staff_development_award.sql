-- Staff development award data path (COMPENSATORY_PICKS_PLAN follow-up U6; PROJECT_SPEC §2.17g/§3.3c).
-- Resolution JC-2A is modelled as *program eligibility* JR supplies from a reviewed source: a person is either
-- recorded as eligible for the development award or is simply absent (absent = not eligible, never guessed). No
-- ethnicity/race/minority column is added to any people table and nobody's status is inferred from anything.
--   staff_development_eligibility  the reviewed registry (person_kind COACH/COORDINATOR/EXECUTIVE + a stable key)
--   staff_development_departures   the journal of hire-aways the comp producer reads (one row per qualifying move)
--   staff_moves.role               widened to hold 'GM' / 'AGM' so an executive hire can be recorded at all
-- Needs the teams and staff_moves tables (db/init_db.sql). Idempotent; seeds nothing; transactional. Also in
-- db/init_db.sql (mirrored by the orchestrator).
--
-- Run ./db/backup_db.sh first. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_02_07_staff_development_award.sql

BEGIN;

DO $$
BEGIN
    IF to_regclass('teams') IS NULL THEN
        RAISE EXCEPTION 'staff development award requires the teams table';
    END IF;
    IF to_regclass('staff_moves') IS NULL THEN
        RAISE EXCEPTION 'staff development award requires the staff_moves table';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS staff_development_eligibility (
    id           SERIAL PRIMARY KEY,
    person_kind  VARCHAR(16) NOT NULL CHECK (person_kind IN ('COACH', 'COORDINATOR', 'EXECUTIVE')),
    person_key   TEXT NOT NULL,
    person_name  TEXT,
    eligible     BOOLEAN NOT NULL DEFAULT true,
    source       TEXT NOT NULL,
    reviewed_by  TEXT,
    as_of        DATE NOT NULL DEFAULT CURRENT_DATE,
    notes        TEXT,
    UNIQUE (person_kind, person_key)
);

CREATE TABLE IF NOT EXISTS staff_development_departures (
    id           SERIAL PRIMARY KEY,
    season       INT NOT NULL,
    from_team_id INT REFERENCES teams(id),
    to_team_id   INT REFERENCES teams(id),
    person_name  TEXT,
    from_role    VARCHAR(16),
    to_role      VARCHAR(16),
    tenure_years INT,
    eligible     BOOLEAN NOT NULL DEFAULT true,
    source       TEXT,
    created_at   TIMESTAMPTZ DEFAULT now()
);

-- staff_moves.role was VARCHAR(2) with a HC/OC/DC CHECK, so a 3-letter 'AGM' executive hire could not be stored.
-- Widen the type and re-add the CHECK (existing HC/OC/DC rows keep working).
ALTER TABLE staff_moves DROP CONSTRAINT IF EXISTS staff_moves_role_check;
ALTER TABLE staff_moves ALTER COLUMN role TYPE VARCHAR(16);
ALTER TABLE staff_moves ADD CONSTRAINT staff_moves_role_check
    CHECK (role IN ('HC', 'OC', 'DC', 'GM', 'AGM'));

COMMIT;
