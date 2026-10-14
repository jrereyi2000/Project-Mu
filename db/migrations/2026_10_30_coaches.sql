-- Head coaches: identity, team tenures, and the Madden coach screen's career counters per season
-- (PROJECT_SPEC §3). Run once: psql -f db/migrations/2026_10_30_coaches.sql
BEGIN;

CREATE TABLE IF NOT EXISTS coaches (
    id SERIAL PRIMARY KEY,
    coach_name VARCHAR(255) UNIQUE NOT NULL,
    archetype VARCHAR(64),
    offensive_scheme VARCHAR(64),
    defensive_scheme VARCHAR(64)
);

-- Who coached which team, and when. end_season NULL = still there.
CREATE TABLE IF NOT EXISTS coach_tenures (
    id SERIAL PRIMARY KEY,
    coach_id INT NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    start_season INT NOT NULL,
    end_season INT,
    UNIQUE (coach_id, team_id, start_season),
    CHECK (end_season IS NULL OR end_season >= start_season)
);

-- Career counters as the coach screen shows them at the end of each season (they include
-- pre-franchise NFL careers, so they are stored, not rebuilt from games).
CREATE TABLE IF NOT EXISTS coach_seasons (
    id SERIAL PRIMARY KEY,
    coach_id INT NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
    team_id INT REFERENCES teams(id) ON DELETE CASCADE,
    season INT NOT NULL,
    career_wins INT,
    career_losses INT,
    career_ties INT,
    winning_seasons INT,
    playoff_wins INT,
    playoff_losses INT,
    super_bowl_wins INT,
    super_bowl_losses INT,
    UNIQUE (coach_id, season)
);

ALTER TABLE awards ADD COLUMN IF NOT EXISTS coach_id INT REFERENCES coaches(id);

-- Carolina: Dave Canales 2026–2033, Marcus Whitfield from 2034.
INSERT INTO coaches (coach_name) VALUES ('Dave Canales'), ('Marcus Whitfield')
ON CONFLICT (coach_name) DO NOTHING;
INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season)
SELECT c.id, t.id, v.start_season, v.end_season
FROM (VALUES ('Dave Canales', 2026, 2033), ('Marcus Whitfield', 2034, NULL::INT)) AS v(coach_name, start_season, end_season)
JOIN coaches c ON c.coach_name = v.coach_name
JOIN teams t ON t.city = 'Carolina' AND t.name = 'Panthers'
ON CONFLICT DO NOTHING;

-- Coach of the Year winners: every coach other than Whitfield has had the same team since 2026.
INSERT INTO coaches (coach_name)
SELECT DISTINCT coach_name FROM awards WHERE award_type = 'COTY' AND coach_name IS NOT NULL
ON CONFLICT (coach_name) DO NOTHING;
INSERT INTO coach_tenures (coach_id, team_id, start_season)
SELECT DISTINCT c.id, a.team_id, 2026
FROM awards a JOIN coaches c ON c.coach_name = a.coach_name
WHERE a.award_type = 'COTY' AND a.team_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM coach_tenures ct WHERE ct.coach_id = c.id)
ON CONFLICT DO NOTHING;
UPDATE awards a SET coach_id = c.id
FROM coaches c
WHERE a.award_type = 'COTY' AND a.coach_name = c.coach_name AND a.coach_id IS NULL;

COMMIT;
