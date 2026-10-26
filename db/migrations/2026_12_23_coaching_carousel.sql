-- Coaching carousel (engines/coaching_carousel.py; PROJECT_SPEC §2.19). Extends the coach hot seat with playoff-exit
-- firings (a contender that went out early), adds coordinator firings, and logs Mu's hiring cycle after the Super
-- Bowl: head coaches (JR hires any coach in Madden and Mu's choice stands for him), coordinators (Mu's own), poaching.
-- Needs 2026_12_22_coach_hot_seat.sql.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_12_23_coaching_carousel.sql

BEGIN;

-- BLACK_MONDAY: teams that missed the playoffs (Week 18 run); PLAYOFF_EXIT: a playoff team once it is eliminated or
-- wins the Super Bowl.
ALTER TABLE coach_hot_seat ADD COLUMN IF NOT EXISTS phase VARCHAR(12) NOT NULL DEFAULT 'BLACK_MONDAY'
    CHECK (phase IN ('BLACK_MONDAY', 'PLAYOFF_EXIT'));

-- One row per coordinator reviewed: the teams whose head coach kept his job, decided with his row.
CREATE TABLE IF NOT EXISTS coordinator_hot_seat (
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    role VARCHAR(2) NOT NULL CHECK (role IN ('OC', 'DC')),
    coordinator_id INT NOT NULL REFERENCES coordinators(id),
    score NUMERIC(6,3) NOT NULL,
    fired BOOLEAN NOT NULL DEFAULT false,
    phase VARCHAR(12) NOT NULL CHECK (phase IN ('BLACK_MONDAY', 'PLAYOFF_EXIT')),
    inputs JSONB NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (season, team_id, role)
);

-- The hiring cycle after `season`'s Super Bowl, one row per staff change it made (for season + 1).
-- action HIRED: the new HC/OC/DC; LEFT: a coordinator who left for a head-coach job; REPLACED: a coordinator the new
-- head coach replaced with his own. source (HIRED): POACHED (another team's coordinator), PROMOTED (internal), RETREAD
-- (a former head coach), REHIRED (a coordinator out of a job), HC_STAFF (the new head coach's own coordinator),
-- OUTSIDE (invented: college or assistant ranks).
CREATE TABLE IF NOT EXISTS staff_moves (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    role VARCHAR(2) NOT NULL CHECK (role IN ('HC', 'OC', 'DC')),
    action VARCHAR(8) NOT NULL CHECK (action IN ('HIRED', 'LEFT', 'REPLACED')),
    person TEXT NOT NULL,
    coach_id INT REFERENCES coaches(id),
    coordinator_id INT REFERENCES coordinators(id),
    source VARCHAR(8) CHECK (source IN ('POACHED', 'PROMOTED', 'RETREAD', 'REHIRED', 'HC_STAFF', 'OUTSIDE')),
    from_team_id INT REFERENCES teams(id),
    from_role VARCHAR(2),
    slant VARCHAR(10),
    score NUMERIC(6,3),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS staff_moves_season ON staff_moves (season);

COMMIT;
