-- Personality core extensions (docs/in_progress/PERSONALITY_CORE_EXTENSIONS_PLAN.md §2.2; PROJECT_SPEC §3.3p, §7.11).
-- Requires 2027_01_16_personality_core.sql. Not folded into db/init_db.sql; applied deliberately (JR's call).
--
-- persona_core_events gains kind / effective_tick / actor / seq / recorded_at; existing rows become 'evolution'.
-- The one-change-a-season rule (the old UNIQUE (persona_type, persona_id, season)) now covers evolution only,
-- so JR's overrides, adoptions and role links can share a season. New table persona_core_links (role changes).
-- One transaction; the season constraint is found by its columns and the migration fails if it is missing
-- (unless the partial index from an earlier run already replaced it). Idempotent.
--
-- Apply with:
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_01_29_personality_core_extensions.sql

BEGIN;

DO $$
BEGIN
    IF to_regclass('persona_core_events') IS NULL OR to_regclass('persona_core_profiles') IS NULL THEN
        RAISE EXCEPTION 'personality core extensions require 2027_01_16_personality_core.sql';
    END IF;
END $$;

ALTER TABLE persona_core_events
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'evolution'
        CHECK (kind IN ('evolution', 'override', 'adoption', 'role_link')),
    ADD COLUMN IF NOT EXISTS effective_tick SMALLINT CHECK (effective_tick BETWEEN 1 AND 32),
    ADD COLUMN IF NOT EXISTS actor TEXT,
    ADD COLUMN IF NOT EXISTS seq BIGINT GENERATED ALWAYS AS IDENTITY,
    ADD COLUMN IF NOT EXISTS recorded_at TIMESTAMPTZ NOT NULL DEFAULT now();

DO $$
DECLARE
    season_key TEXT;
BEGIN
    -- Find the season unique constraint by its columns, whatever name the database gave it.
    SELECT con.conname INTO season_key
    FROM pg_constraint con
    WHERE con.conrelid = 'persona_core_events'::regclass
      AND con.contype = 'u'
      AND (SELECT array_agg(att.attname::text ORDER BY att.attname::text)
           FROM unnest(con.conkey) AS k(attnum)
           JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum)
          = ARRAY['persona_id', 'persona_type', 'season'];
    IF season_key IS NOT NULL THEN
        EXECUTE format('ALTER TABLE persona_core_events DROP CONSTRAINT %I', season_key);
    ELSIF to_regclass('persona_core_events_one_evolution_per_season') IS NULL THEN
        RAISE EXCEPTION 'persona_core_events season unique constraint not found; schema does not match 2027_01_16';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS persona_core_events_one_evolution_per_season
    ON persona_core_events (persona_type, persona_id, season) WHERE kind = 'evolution';

CREATE TABLE IF NOT EXISTS persona_core_links (
    universe_id TEXT NOT NULL,
    from_type VARCHAR(32) NOT NULL,
    from_id VARCHAR(64) NOT NULL,
    to_type VARCHAR(32) NOT NULL,
    to_id VARCHAR(64) NOT NULL,
    link_kind TEXT NOT NULL CHECK (link_kind = 'role_change'),
    effective_season INT NOT NULL,
    effective_tick SMALLINT CHECK (effective_tick BETWEEN 1 AND 32),
    receipt JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (to_type, to_id),
    UNIQUE (from_type, from_id, to_type),
    FOREIGN KEY (from_type, from_id) REFERENCES persona_core_profiles(persona_type, persona_id),
    FOREIGN KEY (to_type, to_id) REFERENCES persona_core_profiles(persona_type, persona_id)
);

CREATE INDEX IF NOT EXISTS idx_persona_core_links_from ON persona_core_links (from_type, from_id);

COMMIT;
