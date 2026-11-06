-- Personality evolution (docs/in_progress/PERSONALITY_EVOLUTION_PLAN.md §2.4; personality/evolution_store.py).
-- Requires the personality core (2027_01_16_personality_core.sql) and core extensions
-- (2027_01_29_personality_core_extensions.sql) migrations: the applied changes themselves are persona_core_events
-- receipts (kind 'evolution'); these two tables only record each season's run and every candidate it considered.
--   persona_evolution_runs        one row per (universe, season, rules_version) run: input hash, counts, status
--   persona_evolution_candidates  every rule and external candidate, with its draw and what became of it
-- Transactional and safe to rerun: an existing table or index is checked against this definition first, and an
-- incompatible one fails loudly instead of being silently accepted. Seeds nothing.
--
-- Apply with:
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_01_30_personality_evolution.sql

BEGIN;

DO $$
DECLARE
    spec RECORD;
    rel REGCLASS;
    actual_cols TEXT;
    actual_pk TEXT;
    idx REGCLASS;
BEGIN
    FOR spec IN SELECT * FROM (VALUES
        ('persona_evolution_runs',
         'universe_id text NO, season integer NO, rules_version text NO, input_hash text NO, counts jsonb NO, '
         'status character varying(10) NO, error text YES, created_at timestamp with time zone NO',
         'universe_id,season,rules_version'),
        ('persona_evolution_candidates',
         'candidate_id text NO, universe_id text NO, persona_type character varying(32) NO, '
         'persona_id character varying(64) NO, season integer NO, source character varying(16) NO, rule_id text NO, '
         'rules_version text NO, change jsonb NO, chance numeric YES, draw double precision YES, '
         'source_event_ids ARRAY NO, note text YES, status character varying(12) NO, reason text YES, '
         'core_event_id text YES, created_at timestamp with time zone NO',
         'candidate_id')
    ) AS v(tbl, cols, pk)
    LOOP
        rel := to_regclass(format('%I.%I', current_schema(), spec.tbl));
        CONTINUE WHEN rel IS NULL;
        SELECT string_agg(c.column_name || ' ' || c.data_type
                          || coalesce('(' || c.character_maximum_length || ')', '') || ' ' || c.is_nullable,
                          ', ' ORDER BY c.ordinal_position)
          INTO actual_cols
          FROM information_schema.columns c
         WHERE c.table_schema = current_schema() AND c.table_name = spec.tbl;
        SELECT string_agg(a.attname, ',' ORDER BY k.ord)
          INTO actual_pk
          FROM pg_constraint pc
          CROSS JOIN LATERAL unnest(pc.conkey) WITH ORDINALITY AS k(attnum, ord)
          JOIN pg_attribute a ON a.attrelid = pc.conrelid AND a.attnum = k.attnum
         WHERE pc.conrelid = rel AND pc.contype = 'p';
        IF actual_cols IS DISTINCT FROM spec.cols OR actual_pk IS DISTINCT FROM spec.pk THEN
            RAISE EXCEPTION '% already exists with an incompatible definition: columns [%], primary key [%]; expected columns [%], primary key [%]',
                spec.tbl, actual_cols, actual_pk, spec.cols, spec.pk;
        END IF;
    END LOOP;

    idx := to_regclass(format('%I.%I', current_schema(), 'persona_evolution_candidates_season'));
    IF idx IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_index i
         WHERE i.indexrelid = idx
           AND i.indrelid = to_regclass(format('%I.%I', current_schema(), 'persona_evolution_candidates'))
           AND pg_get_indexdef(i.indexrelid) LIKE '%USING btree (universe_id, season, status)')
    THEN
        RAISE EXCEPTION 'persona_evolution_candidates_season already exists but is not a btree index on persona_evolution_candidates (universe_id, season, status): %',
            pg_get_indexdef(idx);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS persona_evolution_runs (
    universe_id TEXT NOT NULL,
    season INT NOT NULL,
    rules_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,          -- sha256 of the season facts and external candidates the run read
    counts JSONB NOT NULL,
    status VARCHAR(10) NOT NULL CHECK (status IN ('APPLIED','PREVIEW','FAILED')),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, season, rules_version)
);

CREATE TABLE IF NOT EXISTS persona_evolution_candidates (
    candidate_id TEXT PRIMARY KEY,     -- '<universe>:<persona>:<season>:<rule_id | external:<event_id>>'
    universe_id TEXT NOT NULL,
    persona_type VARCHAR(32) NOT NULL,
    persona_id VARCHAR(64) NOT NULL,
    season INT NOT NULL,
    source VARCHAR(16) NOT NULL CHECK (source IN ('RULE','RELATIONSHIPS','DEVELOPMENT','OTHER')),
    rule_id TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    change JSONB NOT NULL,             -- {kind, slider/step | remove/add}
    chance NUMERIC(5,4),               -- NULL for external candidates (reviewed, not drawn)
    draw DOUBLE PRECISION,
    source_event_ids TEXT[] NOT NULL,
    note TEXT,
    status VARCHAR(12) NOT NULL CHECK (status IN ('drawn_out','blocked','superseded','applied','reverted')),
    reason TEXT,
    core_event_id TEXT,                -- the persona_core_events event_id of an applied change
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (universe_id, persona_type, persona_id, season, rule_id)
);

CREATE INDEX IF NOT EXISTS persona_evolution_candidates_season
    ON persona_evolution_candidates (universe_id, season, status);

COMMIT;
