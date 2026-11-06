-- Personality contract policy receipts (docs/in_progress/PERSONALITY_CONTRACT_POLICY_PLAN.md §2.5;
-- engines/personality_contract_store.py). Additive and default-off: two tables, no seeds, no backfill.
--   contract_policy_decisions     one immutable receipt per negotiation attempt or CPU decision batch entry
--                                 (permitted view, revision, context/provider versions, components, gates, draws,
--                                 bars, counters, baseline and adjusted outcomes); same id + same hash replays,
--                                 a changed hash conflicts. No FK to the mutable profile, so archive/retirement
--                                 never breaks replay.
--   contract_policy_event_outbox  factual contract events (CONTRACT_OFFER_SUBMITTED, CONTRACT_RESPONSE_RECORDED,
--                                 CONTRACT_EXECUTED, CONTRACT_INSTRUMENT_APPLIED) for the relationships owner to
--                                 classify; consumers dedupe event_id.
-- The mode/version/config come from the shared personality switchboard (activation plan D1); there is no
-- contract_policy_activation table. Transactional and safe to rerun: an existing table is checked against this
-- definition first, and an incompatible one fails loudly instead of being silently accepted.
--
-- Apply with (scratch first; production needs JR's separate go-ahead):
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_01_23_contract_personality_policy.sql

BEGIN;

DO $$
DECLARE
    spec RECORD;
    rel REGCLASS;
    actual_cols TEXT;
    actual_pk TEXT;
BEGIN
    FOR spec IN SELECT * FROM (VALUES
        ('contract_policy_decisions',
         'universe_id text NO, decision_id text NO, season integer NO, week integer NO, stage text NO, '
         'player_id bigint NO, persona_id text YES, revision integer YES, mode text NO, policy_version text NO, '
         'input_hash text NO, snapshot jsonb NO, result jsonb NO, created_at timestamp with time zone NO',
         'universe_id,decision_id'),
        ('contract_policy_event_outbox',
         'universe_id text NO, event_id text NO, decision_id text NO, kind text NO, payload jsonb NO, '
         'delivered_at timestamp with time zone YES',
         'universe_id,event_id')
    ) AS v(tbl, cols, pk)
    LOOP
        rel := to_regclass(format('%I.%I', current_schema(), spec.tbl));
        CONTINUE WHEN rel IS NULL;
        SELECT string_agg(c.column_name || ' ' || c.data_type || ' ' || c.is_nullable, ', '
                          ORDER BY c.ordinal_position)
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

CREATE TABLE IF NOT EXISTS contract_policy_decisions (
    universe_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,          -- the actual negotiation attempt / batch entry, never player-season
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    stage TEXT NOT NULL,
    player_id BIGINT NOT NULL,
    persona_id TEXT,                    -- NULL: no profile (neutral with reason)
    revision INTEGER,
    mode TEXT NOT NULL CHECK (mode IN ('off', 'shadow', 'active')),
    policy_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,           -- sha256 of the canonical snapshot
    snapshot JSONB NOT NULL,
    result JSONB NOT NULL,              -- links existing offer / execution ids
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, decision_id)
);

CREATE TABLE IF NOT EXISTS contract_policy_event_outbox (
    universe_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('CONTRACT_OFFER_SUBMITTED', 'CONTRACT_RESPONSE_RECORDED',
                                       'CONTRACT_EXECUTED', 'CONTRACT_INSTRUMENT_APPLIED')),
    payload JSONB NOT NULL,
    delivered_at TIMESTAMPTZ,
    PRIMARY KEY (universe_id, event_id),
    FOREIGN KEY (universe_id, decision_id) REFERENCES contract_policy_decisions (universe_id, decision_id)
);

COMMIT;
