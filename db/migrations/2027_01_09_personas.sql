-- Personality plan phases 2 and 3 (docs/in_progress/PERSONALITY_PLAN.md §4, §9; PROJECT_SPEC §3, §7.11; PRE_PC_WORK.md §10).
-- Persona tables keyed by (persona_type, persona_id) across writers, GMs, players, coaches, owners, and scouts.
--
-- Apply with:
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_01_09_personas.sql

BEGIN;

CREATE TABLE IF NOT EXISTS persona_sliders (
    persona_type VARCHAR(32) NOT NULL CHECK (persona_type IN ('writer', 'gm', 'player', 'coach', 'owner', 'scout')),
    persona_id VARCHAR(64) NOT NULL,
    temperament INT NOT NULL CHECK (temperament BETWEEN 0 AND 10),
    sociability INT NOT NULL CHECK (sociability BETWEEN 0 AND 10),
    confidence INT NOT NULL CHECK (confidence BETWEEN 0 AND 10),
    openness INT NOT NULL CHECK (openness BETWEEN 0 AND 10),
    warmth INT NOT NULL CHECK (warmth BETWEEN 0 AND 10),
    drive INT NOT NULL CHECK (drive BETWEEN 0 AND 10),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (persona_type, persona_id),
    CONSTRAINT chk_persona_sliders_budget CHECK (temperament + sociability + confidence + openness + warmth + drive = 30)
);

CREATE INDEX IF NOT EXISTS idx_persona_sliders_type ON persona_sliders (persona_type);

CREATE TABLE IF NOT EXISTS persona_traits (
    id SERIAL PRIMARY KEY,
    persona_type VARCHAR(32) NOT NULL CHECK (persona_type IN ('writer', 'gm', 'player', 'coach', 'owner', 'scout')),
    persona_id VARCHAR(64) NOT NULL,
    trait_id VARCHAR(64) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (persona_type, persona_id, trait_id)
);

CREATE INDEX IF NOT EXISTS idx_persona_traits_lookup ON persona_traits (persona_type, persona_id);
CREATE INDEX IF NOT EXISTS idx_persona_traits_trait ON persona_traits (trait_id);

CREATE TABLE IF NOT EXISTS persona_views (
    id SERIAL PRIMARY KEY,
    persona_type VARCHAR(32) NOT NULL CHECK (persona_type IN ('writer', 'gm', 'player', 'coach', 'owner', 'scout')),
    persona_id VARCHAR(64) NOT NULL,
    view_text TEXT NOT NULL,
    trigger_condition VARCHAR(128) DEFAULT '',
    subject_tags TEXT[] DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_persona_views_lookup ON persona_views (persona_type, persona_id);

CREATE TABLE IF NOT EXISTS persona_relationships (
    id SERIAL PRIMARY KEY,
    persona_type VARCHAR(32) NOT NULL CHECK (persona_type IN ('writer', 'gm', 'player', 'coach', 'owner', 'scout')),
    persona_id VARCHAR(64) NOT NULL,
    target_type VARCHAR(32) NOT NULL,
    target_id VARCHAR(64) NOT NULL,
    trust NUMERIC(3, 2) NOT NULL DEFAULT 0.00 CHECK (trust BETWEEN -1.00 AND 1.00),
    note TEXT,
    tags TEXT[] DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (persona_type, persona_id, target_type, target_id)
);

CREATE INDEX IF NOT EXISTS idx_persona_rel_lookup ON persona_relationships (persona_type, persona_id);

CREATE TABLE IF NOT EXISTS persona_trait_history (
    id SERIAL PRIMARY KEY,
    persona_type VARCHAR(32) NOT NULL CHECK (persona_type IN ('writer', 'gm', 'player', 'coach', 'owner', 'scout')),
    persona_id VARCHAR(64) NOT NULL,
    trait_id VARCHAR(64) NOT NULL,
    added_season INT NOT NULL,
    removed_season INT,
    reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_persona_trait_history_lookup ON persona_trait_history (persona_type, persona_id);

COMMIT;
