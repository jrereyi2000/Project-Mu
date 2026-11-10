-- The Madden coach record Mu builds for every coach or coordinator it invents
-- (COACH_SAVE_WRITES_PLAN §2.1; PROJECT_SPEC §2.19/§3.3a). One row per Mu person, holding every Madden
-- Coach field Mu writes plus the link to the save record. The contract itself stays in staff_contracts.
--
-- Apply with:
--   python3 -m db.migrate apply --file 2027_02_21_coach_madden_profiles.sql

BEGIN;

CREATE TABLE IF NOT EXISTS coach_madden_profiles (
    id SERIAL PRIMARY KEY,
    person_kind VARCHAR(5) NOT NULL CHECK (person_kind IN ('COACH', 'COORD')),   -- coaches.id or coordinators.id
    person_id INT NOT NULL,
    source VARCHAR(8) NOT NULL CHECK (source IN ('SAVE', 'CREATED')),            -- read from the save, or built by Mu
    presentation_id INT,          -- Coach.PresentationId of the linked save record (NULL until written or linked)
    first_name VARCHAR(64) NOT NULL,
    last_name VARCHAR(64) NOT NULL,
    age INT NOT NULL,
    position VARCHAR(24) NOT NULL,            -- StaffPosition: HeadCoach | OffensiveCoordinator | DefensiveCoordinator
    archetype VARCHAR(32) NOT NULL,           -- StaffArchetypeEnum
    specialty VARCHAR(16),                    -- CoachSpecialty
    level INT NOT NULL DEFAULT 1,
    years_coaching INT,
    offensive_playbook VARCHAR(64), defensive_playbook VARCHAR(64),
    offensive_scheme VARCHAR(64), defensive_scheme VARCHAR(64),
    team_philosophy VARCHAR(32),
    personality VARCHAR(32), demeanor VARCHAR(32),
    body_type VARCHAR(24), height INT, weight INT, face_shape VARCHAR(32), generic_head VARCHAR(64),
    talents JSONB NOT NULL DEFAULT '[]',      -- [{"slot": "Gameday"|"Playsheet", "talent": <name>, "tier": n}]
    ratings JSONB NOT NULL DEFAULT '{}',      -- {"COACH_QB": n, ..., "COACH_RATING": n}
    tendencies JSONB NOT NULL DEFAULT '{}',   -- the COACH_*TENDENCY* fields
    visuals_template INT,                     -- PresentationId of the save coach whose CharacterVisuals/loadouts were cloned
    portrait_id INT,                          -- Coach.Portrait (0 = silhouette until the pack is installed)
    portrait_status VARCHAR(10) NOT NULL DEFAULT 'NONE' CHECK (portrait_status IN ('NONE', 'GENERATED', 'PACKED', 'INSTALLED')),
    headshot_path TEXT,                       -- static/coaches/<coach_key>.webp
    seed TEXT NOT NULL,                       -- the draw's seed (same seed, same profile)
    written_at TIMESTAMP,                     -- last time staff.mjs wrote or verified this record
    UNIQUE (person_kind, person_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS coach_madden_profiles_pid ON coach_madden_profiles (presentation_id)
    WHERE presentation_id IS NOT NULL;

COMMIT;
