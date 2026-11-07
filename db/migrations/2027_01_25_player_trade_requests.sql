-- Player trade requests (docs/in_progress/PERSONALITY_TRADE_REQUESTS_PLAN.md §2.6; engines/player_trade_request_store.py).
-- Requires 2027_01_16_personality_core.sql (persona_core_profiles) and db/init_db.sql (teams, whose primary key is
-- `id`). Additive feature tables only: no core or legacy schema edits, no backfill, no historical requests; also
-- mirrored in db/init_db.sql.
--   player_trade_request_decisions  one row per (universe, persona, season) annual REQUEST/NO_REQUEST draw; never a
--                                   deferred or ineligible reservation
--   player_trade_requests           one row per request episode: revision, status and the full episode state
--   player_trade_request_actions    append-only receipts: every checkpoint review (including no-op/deferred) and
--                                   every lifecycle action, with before/after state
-- Transactional and safe to rerun: an existing table is checked against this definition first, and an
-- incompatible one fails loudly instead of being silently accepted. Seeds nothing.
--
-- Production application is a separately reviewed step (plan §6 gate G5). Scratch only, e.g.:
--   psql "$TRADE_REQUESTS_TEST_DSN" -v ON_ERROR_STOP=1 < db/migrations/2027_01_25_player_trade_requests.sql

BEGIN;

DO $$
DECLARE
    spec RECORD;
    rel REGCLASS;
    actual_cols TEXT;
    actual_pk TEXT;
BEGIN
    IF to_regclass(format('%I.%I', current_schema(), 'persona_core_profiles')) IS NULL THEN
        RAISE EXCEPTION 'player trade requests need persona_core_profiles (2027_01_16_personality_core.sql) first';
    END IF;
    IF to_regclass(format('%I.%I', current_schema(), 'teams')) IS NULL THEN
        RAISE EXCEPTION 'player trade requests need the teams table (db/init_db.sql) first';
    END IF;
    FOR spec IN SELECT * FROM (VALUES
        ('player_trade_request_decisions',
         'universe_id text NO, persona_id character varying(64) NO, season integer NO, decision_id uuid NO, '
         'outcome text NO, input_hash text NO, policy_version text NO, seed_algorithm text NO, '
         'draw double precision NO, receipt jsonb NO, created_at timestamp with time zone NO',
         'universe_id,persona_id,season'),
        ('player_trade_requests',
         'request_id uuid NO, decision_id uuid NO, universe_id text NO, persona_type character varying(32) NO, '
         'persona_id character varying(64) NO, team_id integer NO, opened_season integer NO, revision integer NO, '
         'status text NO, state jsonb NO, updated_at timestamp with time zone NO',
         'request_id'),
        ('player_trade_request_actions',
         'universe_id text NO, persona_id character varying(64) NO, event_id text NO, source_event_id text NO, '
         'request_id uuid YES, season integer NO, window_id text NO, checkpoint_id text NO, action_kind text NO, '
         'payload_hash text NO, before_state jsonb YES, after_state jsonb YES, receipt jsonb NO, '
         'created_at timestamp with time zone NO',
         'universe_id,persona_id,event_id')
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

CREATE TABLE IF NOT EXISTS player_trade_request_decisions (
    universe_id TEXT NOT NULL,
    persona_id VARCHAR(64) NOT NULL,
    season INTEGER NOT NULL,
    decision_id UUID NOT NULL UNIQUE,
    outcome TEXT NOT NULL CHECK (outcome IN ('REQUEST', 'NO_REQUEST')),
    input_hash TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    seed_algorithm TEXT NOT NULL,
    draw DOUBLE PRECISION NOT NULL CHECK (draw >= 0 AND draw < 1),
    receipt JSONB NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, persona_id, season)
);

CREATE TABLE IF NOT EXISTS player_trade_requests (
    request_id UUID PRIMARY KEY,
    decision_id UUID NOT NULL UNIQUE REFERENCES player_trade_request_decisions (decision_id),
    universe_id TEXT NOT NULL,
    persona_type VARCHAR(32) NOT NULL CHECK (persona_type = 'player'),
    persona_id VARCHAR(64) NOT NULL,
    team_id INTEGER NOT NULL REFERENCES teams (id),
    opened_season INTEGER NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'WITHDRAWN', 'RESOLVED_TRADE', 'CLOSED_EMPLOYMENT',
                                           'VOIDED_CORRECTION')),
    state JSONB NOT NULL CHECK (jsonb_typeof(state) = 'object' AND state->>'status' = status
                                AND (state->>'revision')::int = revision),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (persona_type, persona_id) REFERENCES persona_core_profiles (persona_type, persona_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS player_trade_requests_one_active
    ON player_trade_requests (universe_id, persona_id) WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS player_trade_request_actions (
    universe_id TEXT NOT NULL,
    persona_id VARCHAR(64) NOT NULL,
    event_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    request_id UUID NULL REFERENCES player_trade_requests (request_id),
    season INTEGER NOT NULL,
    window_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    action_kind TEXT NOT NULL CHECK (action_kind IN ('REVIEW', 'ACKNOWLEDGE', 'DECLINE', 'EXPLORE', 'REVIEW_REPAIR',
                                                     'RECORD_WITHDRAWAL', 'RECORD_TRADE', 'CLOSE_EMPLOYMENT',
                                                     'CORRECT', 'DISCLOSE')),
    payload_hash TEXT NOT NULL,
    before_state JSONB NULL,
    after_state JSONB NULL,
    receipt JSONB NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, persona_id, event_id),
    UNIQUE (universe_id, persona_id, source_event_id, action_kind),
    UNIQUE (universe_id, persona_id, season, window_id, checkpoint_id, action_kind)
);

COMMIT;
