-- Personality relationships ledger (docs/done/PERSONALITY_RELATIONSHIPS_PLAN.md §2.6;
-- personality/relationship_store.py). Requires 2027_01_16_personality_core.sql (persona_core_profiles, read by
-- the store to validate every PersonRef). Additive feature tables only: no core schema edits, no legacy
-- persona_relationships dual-write, no backfill, not folded into db/init_db.sql.
--   persona_relationship_batches  one row per appended batch: the universe's serialized head (starts at 1),
--                                 the caller's expected head, the batch hash and the full receipt
--   persona_relationship_events   one row per (occurrence, facet): canonical envelope, payload hash, head
--   persona_relationship_sources  external source refs -> the occurrence they name (many refs, one occurrence)
-- Transactional and safe to rerun: an existing table or index is checked against this definition first, and
-- an incompatible one fails loudly instead of being silently accepted. Seeds nothing.
--
-- Production application is a separately reviewed step (plan §6 gate G4). Scratch only, e.g.:
--   psql "$RELATIONSHIPS_TEST_DSN" -v ON_ERROR_STOP=1 < db/migrations/2027_01_27_personality_relationships.sql

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
        ('persona_relationship_batches',
         'universe_id text NO, head bigint NO, expected_head bigint NO, batch_hash text NO, receipt jsonb NO, '
         'created_at timestamp with time zone NO',
         'universe_id,head'),
        ('persona_relationship_events',
         'universe_id text NO, source_event_id text NO, facet_key text NO, event_id text NO, head bigint NO, '
         'occurred_season integer NO, occurred_tick integer NO, schema_version text NO, payload_hash text NO, '
         'envelope jsonb NO, created_at timestamp with time zone NO',
         'universe_id,source_event_id,facet_key'),
        ('persona_relationship_sources',
         'universe_id text NO, namespace text NO, external_key text NO, source_event_id text NO',
         'universe_id,namespace,external_key')
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

    idx := to_regclass(format('%I.%I', current_schema(), 'persona_relationship_events_time'));
    IF idx IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_index i
         WHERE i.indexrelid = idx
           AND i.indrelid = to_regclass(format('%I.%I', current_schema(), 'persona_relationship_events'))
           AND pg_get_indexdef(i.indexrelid) LIKE '%USING btree (universe_id, occurred_season, occurred_tick)')
    THEN
        RAISE EXCEPTION 'persona_relationship_events_time already exists but is not a btree index on persona_relationship_events (universe_id, occurred_season, occurred_tick): %',
            pg_get_indexdef(idx);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS persona_relationship_batches (
    universe_id TEXT NOT NULL,
    head BIGINT NOT NULL CHECK (head > 0),
    expected_head BIGINT NOT NULL CHECK (expected_head >= 0 AND expected_head = head - 1),
    batch_hash TEXT NOT NULL,
    receipt JSONB NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, head),
    UNIQUE (universe_id, batch_hash)
);

CREATE TABLE IF NOT EXISTS persona_relationship_events (
    universe_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    facet_key TEXT NOT NULL,           -- relationship_events.facet_key: hash of subject / target / kind
    event_id TEXT NOT NULL,            -- transport ID, checked separately for content conflicts
    head BIGINT NOT NULL CHECK (head > 0),
    occurred_season INT NOT NULL,
    occurred_tick INT NOT NULL CHECK (occurred_tick >= 0),
    schema_version TEXT NOT NULL CHECK (schema_version IN ('relationships-v1')),
    payload_hash TEXT NOT NULL,
    envelope JSONB NOT NULL CHECK (jsonb_typeof(envelope) = 'object'
                                   AND envelope->>'schema_version' = schema_version
                                   AND envelope->>'source_event_id' = source_event_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, source_event_id, facet_key),
    UNIQUE (universe_id, event_id, facet_key),
    FOREIGN KEY (universe_id, head) REFERENCES persona_relationship_batches (universe_id, head)
);

CREATE INDEX IF NOT EXISTS persona_relationship_events_time
    ON persona_relationship_events (universe_id, occurred_season, occurred_tick);

CREATE TABLE IF NOT EXISTS persona_relationship_sources (
    universe_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    external_key TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    PRIMARY KEY (universe_id, namespace, external_key)
);

COMMIT;
