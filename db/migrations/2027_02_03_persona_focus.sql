-- Current focus of real coaches and owners (docs/in_progress/INFERRED_SLIDERS_AND_FOCUS_PLAN.md §2.5;
-- personality/focus_store.py, personality/focus_run.py).
-- Requires the personality core (2027_01_16_personality_core.sql) and lifecycle (2027_01_24_personality_lifecycle.sql)
-- migrations: persona_focus rows reference persona_core_profiles, and holders are found through the core aliases the
-- reviewed lifecycle apply writes.
--   persona_focus       append-only focus history per (universe, persona, slot): seq 0 from the profile, later rows
--                       drafted from templates by the offseason refresh or set/reverted by JR; one active row a slot
--   persona_focus_runs  one row per (universe, season, rules_version) refresh run: input hash, counts, status
-- Transactional and safe to rerun: an existing table is checked against this definition first, and an
-- incompatible one fails loudly. Seeds nothing. Mirrored in db/init_db.sql; applying it is JR's call.
--
-- Apply with (JR's call):
--   docker exec -i madden-db psql -U postgres -d <db> -v ON_ERROR_STOP=1 < db/migrations/2027_02_03_persona_focus.sql

BEGIN;

DO $$
DECLARE
    spec RECORD;
    rel REGCLASS;
    actual_cols TEXT;
    actual_pk TEXT;
BEGIN
    FOR spec IN SELECT * FROM (VALUES
        ('persona_focus',
         'focus_id text NO, universe_id text NO, persona_type character varying(32) NO, '
         'persona_id character varying(64) NO, slot character varying(4) NO, seq integer NO, text text NO, '
         'subjects jsonb NO, anchor jsonb NO, until_season integer YES, source character varying(8) NO, '
         'template_id text YES, rules_version text NO, status character varying(10) NO, '
         'valid_from_season integer NO, valid_from_tick smallint NO, supersedes text YES, stale_causes jsonb NO, '
         'reason text YES, actor text NO, created_at timestamp with time zone NO',
         'focus_id'),
        ('persona_focus_runs',
         'universe_id text NO, season integer NO, rules_version text NO, input_hash text NO, counts jsonb NO, '
         'status character varying(10) NO, error text YES, created_at timestamp with time zone NO',
         'universe_id,season,rules_version')
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
END $$;

CREATE TABLE IF NOT EXISTS persona_focus (
    focus_id TEXT PRIMARY KEY,                 -- 'focus:<universe_id>:<persona_id>:<slot>:<seq>'
    universe_id TEXT NOT NULL,
    persona_type VARCHAR(32) NOT NULL CHECK (persona_type IN ('coach','owner')),
    persona_id VARCHAR(64) NOT NULL,
    slot VARCHAR(4) NOT NULL CHECK (slot IN ('want','fear')),
    seq INT NOT NULL CHECK (seq >= 0),         -- 0 = from the profile, +1 per later row
    text TEXT NOT NULL,
    subjects JSONB NOT NULL DEFAULT '[]',      -- [{kind, name, binding {namespace, external_id} | null}]
    anchor JSONB NOT NULL DEFAULT '{}',        -- {team, seat} of the holder when the row was written
    until_season INT,
    source VARCHAR(8) NOT NULL CHECK (source IN ('profile','template','jr')),
    template_id TEXT,
    rules_version TEXT NOT NULL,
    status VARCHAR(10) NOT NULL CHECK (status IN ('active','superseded','reverted')),
    valid_from_season INT NOT NULL,
    valid_from_tick SMALLINT NOT NULL CHECK (valid_from_tick BETWEEN 1 AND 32),
    supersedes TEXT REFERENCES persona_focus(focus_id),
    stale_causes JSONB NOT NULL DEFAULT '[]',
    reason TEXT,                               -- the change note
    actor TEXT NOT NULL DEFAULT 'system',      -- 'JR' for set and revert
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (universe_id, persona_id, slot, seq),
    FOREIGN KEY (persona_type, persona_id) REFERENCES persona_core_profiles(persona_type, persona_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS persona_focus_one_active
    ON persona_focus (universe_id, persona_id, slot) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS persona_focus_runs (
    universe_id TEXT NOT NULL,
    season INT NOT NULL,
    rules_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,                  -- sha256 of the season facts and active rows the run read
    counts JSONB NOT NULL,
    status VARCHAR(10) NOT NULL CHECK (status IN ('APPLIED','PREVIEW','FAILED')),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, season, rules_version)
);

COMMIT;
