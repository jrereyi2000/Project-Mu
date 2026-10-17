-- Migration: Add coach slants and coordinator infrastructure
-- (COACH_SLANTS_AND_COORDINATORS_PLAN.md §3)
BEGIN;

-- 1. Add slant column to coaches
ALTER TABLE coaches 
ADD COLUMN IF NOT EXISTS slant VARCHAR(16) NOT NULL DEFAULT 'OFFENSIVE' 
CHECK (slant IN ('OFFENSIVE', 'DEFENSIVE', 'CEO'));

-- 2. Create coordinators table
CREATE TABLE IF NOT EXISTS coordinators (
    id SERIAL PRIMARY KEY,
    coordinator_name VARCHAR(255) NOT NULL,
    side VARCHAR(16) NOT NULL CHECK (side IN ('OFFENSIVE', 'DEFENSIVE')),
    scheme VARCHAR(64) NOT NULL,
    playbook VARCHAR(64),
    pass_lean VARCHAR(6) CHECK (pass_lean IN ('Low', 'Medium', 'High')),
    blitz_rate VARCHAR(6) CHECK (blitz_rate IN ('Low', 'Medium', 'High')),
    man_coverage VARCHAR(6) CHECK (man_coverage IN ('Low', 'Medium', 'High')),
    origin_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Create coordinator_tenures table
CREATE TABLE IF NOT EXISTS coordinator_tenures (
    id SERIAL PRIMARY KEY,
    coordinator_id INT NOT NULL REFERENCES coordinators(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    role VARCHAR(8) NOT NULL CHECK (role IN ('OC', 'DC')),
    start_season INT NOT NULL,
    end_season INT,
    UNIQUE (coordinator_id, team_id, role, start_season),
    CHECK (end_season IS NULL OR end_season >= start_season)
);

CREATE INDEX IF NOT EXISTS idx_coord_tenures_team_season 
ON coordinator_tenures (team_id, start_season, COALESCE(end_season, 9999));

COMMIT;
