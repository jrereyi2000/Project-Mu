-- Coach and player agent loops (docs/in_progress/COACH_PLAYER_AGENT_LOOPS_PLAN.md §2.4; engines/agent_loops/).
--   agent_loop_decisions   one row per (league, season, tick, kind, subject): the options a loop saw, the seeded
--                          policy's key, the chosen key and its source (POLICY | MODEL | MODEL_REJECTED), the
--                          model's grounded text, its visibility and the facts it was grounded in. Record, don't
--                          replay: a decided case is read back, never re-asked.
-- Transactional and safe to rerun: an existing table or index is checked against this definition first, and an
-- incompatible one fails loudly instead of being silently accepted. Seeds nothing.
--
-- Apply with:
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_02_01_agent_loops.sql

BEGIN;

DO $$
DECLARE
    rel REGCLASS;
    idx REGCLASS;
    expected_cols TEXT := 'id bigint NO, league_id bigint NO, season integer NO, tick smallint NO, kind text NO, '
        'subject_key text NO, persona_type character varying(32) YES, persona_id character varying(64) YES, '
        'options jsonb NO, policy_key text NO, chosen_key text NO, choice_source text NO, text text YES, '
        'visibility text NO, facts jsonb NO, cause_refs jsonb NO, created_at timestamp with time zone NO';
    actual_cols TEXT;
    actual_pk TEXT;
BEGIN
    rel := to_regclass(format('%I.%I', current_schema(), 'agent_loop_decisions'));
    IF rel IS NOT NULL THEN
        SELECT string_agg(c.column_name || ' ' || c.data_type
                          || coalesce('(' || c.character_maximum_length || ')', '') || ' ' || c.is_nullable,
                          ', ' ORDER BY c.ordinal_position)
          INTO actual_cols
          FROM information_schema.columns c
         WHERE c.table_schema = current_schema() AND c.table_name = 'agent_loop_decisions';
        SELECT string_agg(a.attname, ',' ORDER BY k.ord)
          INTO actual_pk
          FROM pg_constraint pc
          CROSS JOIN LATERAL unnest(pc.conkey) WITH ORDINALITY AS k(attnum, ord)
          JOIN pg_attribute a ON a.attrelid = pc.conrelid AND a.attnum = k.attnum
         WHERE pc.conrelid = rel AND pc.contype = 'p';
        IF actual_cols IS DISTINCT FROM expected_cols OR actual_pk IS DISTINCT FROM 'id' THEN
            RAISE EXCEPTION 'agent_loop_decisions already exists with an incompatible definition: columns [%], primary key [%]; expected columns [%], primary key [id]',
                actual_cols, actual_pk, expected_cols;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint pc
             WHERE pc.conrelid = rel AND pc.contype = 'u'
               AND pg_get_constraintdef(pc.oid) = 'UNIQUE (league_id, season, tick, kind, subject_key)')
        THEN
            RAISE EXCEPTION 'agent_loop_decisions already exists with an incompatible definition: no UNIQUE (league_id, season, tick, kind, subject_key)';
        END IF;
    END IF;

    idx := to_regclass(format('%I.%I', current_schema(), 'agent_loop_decisions_public'));
    IF idx IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_index i
         WHERE i.indexrelid = idx
           AND i.indrelid = to_regclass(format('%I.%I', current_schema(), 'agent_loop_decisions'))
           AND pg_get_indexdef(i.indexrelid) LIKE '%USING btree (league_id, season, tick) WHERE (visibility = ''public''::text)')
    THEN
        RAISE EXCEPTION 'agent_loop_decisions_public already exists with an incompatible definition: %',
            pg_get_indexdef(idx);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS agent_loop_decisions (
    id BIGSERIAL PRIMARY KEY,
    league_id BIGINT NOT NULL,
    season INT NOT NULL,
    tick SMALLINT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('CHART','PROMISE','PROMISE_REVIEW','CONTRACT','INBOX_REPLY','LEAK')),
    subject_key TEXT NOT NULL,              -- e.g. 'team:12:chart', 'player:4411:promise', 'player:4411:offer:88'
    persona_type VARCHAR(32),
    persona_id VARCHAR(64),
    options JSONB NOT NULL,                 -- [{key, action, detail, label}]: labels only, never a hidden score
    policy_key TEXT NOT NULL,               -- the seeded personality policy's choice (always computed)
    chosen_key TEXT NOT NULL,
    choice_source TEXT NOT NULL CHECK (choice_source IN ('POLICY','MODEL','MODEL_REJECTED')),
    text TEXT,                              -- the model's words, kept only when grounded in facts
    visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','public')),
    facts JSONB NOT NULL,
    cause_refs JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (league_id, season, tick, kind, subject_key)
);

CREATE INDEX IF NOT EXISTS agent_loop_decisions_public
    ON agent_loop_decisions (league_id, season, tick) WHERE visibility = 'public';

COMMIT;
