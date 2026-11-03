-- Personality core sidecars. Requires 2027_01_09_personas.sql.
-- Does not seed profiles, change legacy constraints, or activate feature effects.
BEGIN;
CREATE TABLE IF NOT EXISTS persona_core_catalogues (
    version TEXT PRIMARY KEY,
    catalogue JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS persona_core_profiles (
    persona_type VARCHAR(32) NOT NULL,
    persona_id VARCHAR(64) NOT NULL,
    universe_id TEXT NOT NULL,
    birth_key TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('generated', 'real', 'unknown')),
    catalogue_version TEXT NOT NULL REFERENCES persona_core_catalogues(version),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    metadata JSONB NOT NULL,
    PRIMARY KEY (persona_type, persona_id),
    UNIQUE (universe_id, persona_type, birth_key),
    FOREIGN KEY (persona_type, persona_id) REFERENCES persona_sliders(persona_type, persona_id)
);
CREATE TABLE IF NOT EXISTS persona_core_aliases (
    universe_id TEXT NOT NULL,
    persona_type VARCHAR(32) NOT NULL,
    namespace TEXT NOT NULL,
    external_id TEXT NOT NULL,
    persona_id VARCHAR(64) NOT NULL,
    PRIMARY KEY (universe_id, persona_type, namespace, external_id),
    FOREIGN KEY (persona_type, persona_id) REFERENCES persona_core_profiles(persona_type, persona_id)
);
CREATE TABLE IF NOT EXISTS persona_core_events (
    persona_type VARCHAR(32) NOT NULL,
    persona_id VARCHAR(64) NOT NULL,
    event_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    request JSONB NOT NULL,
    before_state JSONB NOT NULL,
    after_state JSONB NOT NULL,
    PRIMARY KEY (persona_type, persona_id, event_id),
    UNIQUE (persona_type, persona_id, season),
    UNIQUE (persona_type, persona_id, source_event_id),
    FOREIGN KEY (persona_type, persona_id) REFERENCES persona_core_profiles(persona_type, persona_id)
);
COMMIT;
