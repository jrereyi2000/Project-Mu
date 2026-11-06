-- Retirement policy sidecars (docs/in_progress/PERSONALITY_RETIREMENT_PLAN.md §2.5; engines/retirement_store.py).
-- Requires the V2 schema (db/init_db.sql: players, league_office_runs); not the persona migrations. Additive only:
-- no backfill, no synthetic capture rows, no edit of player_retirements or retirement_decisions (that table stays the
-- league office's compatibility projection). Mirrored in db/init_db.sql.
--   career_decision_states     one row per (universe, person, earned season): the career state machine
--                              (engines/career_decision_state.py), CAS revision and the live reservation
--   retirement_policy_events   append-only receipts: each checkpoint review (input/policy snapshots, draw, breakdown)
--                              and each state event, with before/after state
--   retirement_write_receipts  save-write requests (PREPARED, immutable) and their acknowledgement (VERIFIED,
--                              FAILED or CONFLICT, by CAS from PREPARED)
-- Transactional and safe to rerun: an existing table is checked against this definition first, and an incompatible
-- one fails loudly instead of being silently accepted. Seeds nothing.
--
-- Production application is a separately authorized step (plan §2.6 gate G5). Scratch only, e.g.:
--   psql "$RETIREMENT_TEST_DSN" -v ON_ERROR_STOP=1 < db/migrations/2027_01_26_retirement_policy.sql
-- Rollback before activation: disable consumers, then drop the empty sidecars. After audit rows exist keep the
-- tables and disable the feature; a save write is never undone by a database rollback.

BEGIN;

DO $$
DECLARE
    spec RECORD;
    rel REGCLASS;
    actual_cols TEXT;
    actual_pk TEXT;
BEGIN
    IF to_regclass(format('%I.%I', current_schema(), 'players')) IS NULL THEN
        RAISE EXCEPTION 'retirement policy tables need the players table (db/init_db.sql) first';
    END IF;
    IF to_regclass(format('%I.%I', current_schema(), 'league_office_runs')) IS NULL THEN
        RAISE EXCEPTION 'retirement policy tables need league_office_runs (2027_01_14_v2_franchise_os.sql) first';
    END IF;
    FOR spec IN SELECT * FROM (VALUES
        ('career_decision_states',
         'universe_id text NO, person_key text NO, earned_season integer NO, player_id integer NO, state text NO, '
         'revision integer NO, decision_id uuid NO, reservation jsonb YES, updated_at timestamp with time zone NO',
         'universe_id,person_key,earned_season'),
        ('retirement_policy_events',
         'event_id uuid NO, decision_id uuid NO, checkpoint text NO, event_kind text NO, source_event_id text NO, '
         'request_hash text NO, input_snapshot jsonb NO, policy_snapshot jsonb NO, draw_hex text YES, '
         'breakdown jsonb YES, before_state jsonb NO, after_state jsonb NO, receipt jsonb NO, '
         'created_at timestamp with time zone NO',
         'event_id'),
        ('retirement_write_receipts',
         'operation_id uuid NO, decision_id uuid NO, status text NO, source_save_hash text NO, output_hash text YES, '
         'edits_hash text NO, league_office_run integer YES, before_image jsonb NO, verification jsonb YES, '
         'request_hash text NO, ack_hash text YES, created_at timestamp with time zone NO, '
         'acknowledged_at timestamp with time zone YES',
         'operation_id')
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

CREATE TABLE IF NOT EXISTS career_decision_states (
    universe_id TEXT NOT NULL,
    person_key TEXT NOT NULL,
    earned_season INTEGER NOT NULL,
    player_id INTEGER NOT NULL REFERENCES players (id),
    state TEXT NOT NULL CHECK (state IN ('OPEN', 'AVAILABLE', 'RETIRE_INTENT', 'SIGN_PENDING', 'RETIRE_PENDING',
                                         'RETURNING', 'UNSIGNED_RETURN_ELIGIBLE', 'RETIRED', 'BLOCKED_DATA',
                                         'CONFLICT')),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    decision_id UUID NOT NULL UNIQUE,
    reservation JSONB NULL CHECK (reservation IS NULL OR jsonb_typeof(reservation) = 'object'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, person_key, earned_season)
);

CREATE TABLE IF NOT EXISTS retirement_policy_events (
    event_id UUID PRIMARY KEY,
    decision_id UUID NOT NULL REFERENCES career_decision_states (decision_id),
    checkpoint TEXT NOT NULL CHECK (checkpoint IN ('OPEN', 'MARKET_CLOSE', 'ROLLOVER_REVIEW', 'EVENT')),
    event_kind TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    input_snapshot JSONB NOT NULL,
    policy_snapshot JSONB NOT NULL,
    draw_hex TEXT NULL,
    breakdown JSONB NULL,
    before_state JSONB NOT NULL,
    after_state JSONB NOT NULL,
    receipt JSONB NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (decision_id, source_event_id)
);

CREATE TABLE IF NOT EXISTS retirement_write_receipts (
    operation_id UUID PRIMARY KEY,
    decision_id UUID NOT NULL REFERENCES career_decision_states (decision_id),
    status TEXT NOT NULL CHECK (status IN ('PREPARED', 'VERIFIED', 'FAILED', 'CONFLICT')),
    source_save_hash TEXT NOT NULL,
    output_hash TEXT NULL,
    edits_hash TEXT NOT NULL,
    league_office_run INTEGER NULL REFERENCES league_office_runs (id),
    before_image JSONB NOT NULL,
    verification JSONB NULL,
    request_hash TEXT NOT NULL,
    ack_hash TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged_at TIMESTAMPTZ NULL,
    CHECK ((status = 'PREPARED') = (ack_hash IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS retirement_write_receipts_one_prepared
    ON retirement_write_receipts (decision_id) WHERE status = 'PREPARED';

COMMIT;
