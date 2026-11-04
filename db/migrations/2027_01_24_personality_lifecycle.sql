-- Person lifecycle ledgers (docs/in_progress/PERSONALITY_DRAFT_INTEGRATION_PLAN.md §2.2; PROJECT_SPEC §3.3p, §7.11).
-- Requires 2027_01_09_personas.sql and 2027_01_16_personality_core.sql. Seeds nothing; not folded into
-- db/init_db.sql; applying it to a franchise database is JR's decision.
--
--   persona_lifecycle_universes  one immutable registration per universe (league ID, starting snapshot hash)
--   persona_lifecycle_people     one row per (universe, source person, role): classification receipt, state
--   persona_lifecycle_bindings   scoped aliases (save-pgid:<lifetime>, db-player, draft:<class>:<sha>, ...),
--                                time-scoped; conflicts never overwrite
--   persona_lifecycle_batches    applied manifests with before/after state and review receipts (replay, revert)
--
-- A rerun verifies the column fingerprint instead of hiding an incompatible earlier schema behind IF NOT EXISTS.
--
-- Apply with:
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_01_24_personality_lifecycle.sql

BEGIN;

DO $$
BEGIN
    IF to_regclass('persona_sliders') IS NULL OR to_regclass('persona_core_profiles') IS NULL THEN
        RAISE EXCEPTION 'personality lifecycle requires 2027_01_09_personas.sql and 2027_01_16_personality_core.sql';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS persona_lifecycle_universes (
    universe_id TEXT PRIMARY KEY,
    league_id TEXT NOT NULL UNIQUE,
    initial_snapshot_sha256 TEXT NOT NULL,
    start_season INTEGER NOT NULL,
    start_cutoff JSONB NOT NULL,
    manifest_version TEXT NOT NULL,
    registration JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS persona_lifecycle_people (
    universe_id TEXT NOT NULL REFERENCES persona_lifecycle_universes(universe_id),
    source_person_id UUID NOT NULL,
    role TEXT NOT NULL,
    birth_key TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('generated', 'real', 'unknown')),
    classification_receipt JSONB NOT NULL,
    first_seen JSONB NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'ready', 'quarantined', 'archived')),
    persona_id VARCHAR(64),
    revision INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (universe_id, source_person_id, role),
    UNIQUE (universe_id, role, birth_key),
    FOREIGN KEY (role, persona_id) REFERENCES persona_core_profiles(persona_type, persona_id)
);

CREATE TABLE IF NOT EXISTS persona_lifecycle_bindings (
    universe_id TEXT NOT NULL REFERENCES persona_lifecycle_universes(universe_id),
    role TEXT NOT NULL,
    namespace TEXT NOT NULL,
    external_id TEXT NOT NULL,
    source_person_id UUID NOT NULL,
    valid_from JSONB NOT NULL,
    valid_to JSONB,
    receipt JSONB NOT NULL,
    PRIMARY KEY (universe_id, role, namespace, external_id),
    FOREIGN KEY (universe_id, source_person_id, role)
        REFERENCES persona_lifecycle_people(universe_id, source_person_id, role)
);

CREATE INDEX IF NOT EXISTS persona_lifecycle_bindings_person
    ON persona_lifecycle_bindings (universe_id, source_person_id, role);

CREATE TABLE IF NOT EXISTS persona_lifecycle_batches (
    batch_id TEXT PRIMARY KEY,
    universe_id TEXT NOT NULL REFERENCES persona_lifecycle_universes(universe_id),
    manifest JSONB NOT NULL,
    before_state JSONB NOT NULL,
    after_state JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('applied', 'reverted')),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    review_receipt JSONB NOT NULL,
    revert_receipt JSONB
);

DO $$
DECLARE
    got TEXT;
BEGIN
    SELECT string_agg(table_name || '.' || column_name, ',' ORDER BY table_name, column_name) INTO got
    FROM information_schema.columns
    WHERE table_schema = current_schema() AND table_name LIKE 'persona\_lifecycle\_%';
    IF got IS DISTINCT FROM 'persona_lifecycle_batches.after_state,persona_lifecycle_batches.applied_at,persona_lifecycle_batches.batch_id,persona_lifecycle_batches.before_state,persona_lifecycle_batches.manifest,persona_lifecycle_batches.revert_receipt,persona_lifecycle_batches.review_receipt,persona_lifecycle_batches.status,persona_lifecycle_batches.universe_id,persona_lifecycle_bindings.external_id,persona_lifecycle_bindings.namespace,persona_lifecycle_bindings.receipt,persona_lifecycle_bindings.role,persona_lifecycle_bindings.source_person_id,persona_lifecycle_bindings.universe_id,persona_lifecycle_bindings.valid_from,persona_lifecycle_bindings.valid_to,persona_lifecycle_people.birth_key,persona_lifecycle_people.classification,persona_lifecycle_people.classification_receipt,persona_lifecycle_people.first_seen,persona_lifecycle_people.persona_id,persona_lifecycle_people.revision,persona_lifecycle_people.role,persona_lifecycle_people.source_person_id,persona_lifecycle_people.state,persona_lifecycle_people.universe_id,persona_lifecycle_universes.initial_snapshot_sha256,persona_lifecycle_universes.league_id,persona_lifecycle_universes.manifest_version,persona_lifecycle_universes.registration,persona_lifecycle_universes.start_cutoff,persona_lifecycle_universes.start_season,persona_lifecycle_universes.universe_id' THEN
        RAISE EXCEPTION 'persona_lifecycle schema fingerprint mismatch: %', got;
    END IF;
END $$;

COMMIT;
