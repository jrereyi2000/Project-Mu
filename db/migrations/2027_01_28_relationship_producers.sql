-- Relationship event producers (docs/in_progress/RELATIONSHIP_EVENT_PRODUCERS_PLAN.md §2.3;
-- engines/relationship_producers/store.py). Requires the players and teams tables (db/init_db.sql).
-- Three tables keyed by league_id (not universe_id), so held events can exist before the lifecycle plan
-- registers the universe:
--   relationship_producer_runs        one row per (league, season, tick, rules_version) run, its input and
--                                     registry hashes and the event hashes it appended or held
--   relationship_roster_observations  which team each player was on at each tick (NULL = free agent)
--   relationship_pending_events       envelopes whose subject has no core identity yet, flushed later
-- Transactional and safe to rerun: an existing table or index is checked against this definition first,
-- and an incompatible one fails loudly instead of being silently accepted. Seeds nothing.
--
-- Apply with:
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_01_28_relationship_producers.sql

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
        ('relationship_producer_runs',
         'league_id bigint NO, season integer NO, tick smallint NO, rules_version text NO, '
         'source character varying(8) NO, input_hash text NO, registry_hash text NO, event_hashes jsonb NO, '
         'counts jsonb NO, status character varying(10) NO, error text YES, created_at timestamp with time zone NO',
         'league_id,season,tick,rules_version'),
        ('relationship_roster_observations',
         'league_id bigint NO, season integer NO, tick smallint NO, player_id integer NO, team_id integer YES, '
         'source character varying(8) NO',
         'league_id,season,tick,player_id'),
        ('relationship_pending_events',
         'league_id bigint NO, source_event_id text NO, facet_key text NO, subject_alias jsonb NO, envelope jsonb NO, '
         'status character varying(10) NO, reason text YES, created_at timestamp with time zone NO, '
         'appended_at timestamp with time zone YES',
         'league_id,source_event_id,facet_key')
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

    idx := to_regclass(format('%I.%I', current_schema(), 'relationship_pending_open'));
    IF idx IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_index i
         WHERE i.indexrelid = idx
           AND i.indrelid = to_regclass(format('%I.%I', current_schema(), 'relationship_pending_events'))
           AND pg_get_indexdef(i.indexrelid) LIKE '%USING btree (league_id, status)')
    THEN
        RAISE EXCEPTION 'relationship_pending_open already exists but is not a btree index on relationship_pending_events (league_id, status): %',
            pg_get_indexdef(idx);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS relationship_producer_runs (
    league_id BIGINT NOT NULL,
    season INT NOT NULL,
    tick SMALLINT NOT NULL CHECK (tick BETWEEN 1 AND 32),
    rules_version TEXT NOT NULL,
    source VARCHAR(8) NOT NULL CHECK (source IN ('SAVE','CATCHUP')),
    input_hash TEXT NOT NULL,          -- sha256 of the canonical inputs read for this tick
    registry_hash TEXT NOT NULL,
    event_hashes JSONB NOT NULL,       -- {source_event_id|facet: payload_hash} appended or held
    counts JSONB NOT NULL,
    status VARCHAR(10) NOT NULL CHECK (status IN ('APPENDED','HELD','NOOP','FAILED')),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_id, season, tick, rules_version)
);

CREATE TABLE IF NOT EXISTS relationship_roster_observations (
    league_id BIGINT NOT NULL,
    season INT NOT NULL,
    tick SMALLINT NOT NULL,
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    team_id INT REFERENCES teams(id),  -- NULL: free agent
    source VARCHAR(8) NOT NULL CHECK (source IN ('SAVE','CATCHUP')),
    PRIMARY KEY (league_id, season, tick, player_id)
);

CREATE TABLE IF NOT EXISTS relationship_pending_events (
    league_id BIGINT NOT NULL,
    source_event_id TEXT NOT NULL,
    facet_key TEXT NOT NULL,
    subject_alias JSONB NOT NULL,      -- {role, namespace, external_id}
    envelope JSONB NOT NULL,           -- complete except subject.persona_id
    status VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPENDED','DROPPED')),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    appended_at TIMESTAMPTZ,
    PRIMARY KEY (league_id, source_event_id, facet_key)
);

CREATE INDEX IF NOT EXISTS relationship_pending_open ON relationship_pending_events (league_id, status);

COMMIT;
