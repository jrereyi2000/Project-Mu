-- Personality feature switchboard (docs/in_progress/PERSONALITY_ACTIVATION_PLAN.md §2.1-§2.2; personality/features.py).
--   personality_feature_modes         one row per (league, feature): mode off|shadow|active, version, config, review
--   personality_feature_mode_changes  every mode change, with the franchise time and any force reason (append-only)
--   personality_feature_clearances    shadow seasons imported from tuning franchises (D4), per feature and version
-- Keyed by league_id (league_state.madden_league_id), like the relationship producers' tables. Seeds nothing: every
-- feature starts 'off' (a missing row reads 'off'). Transactional and safe to rerun: an existing table is checked
-- against this definition first, and an incompatible one fails loudly instead of being silently accepted.
--
-- Apply with (JR's call, after the build):
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_01_31_personality_feature_modes.sql

BEGIN;

DO $$
DECLARE
    spec RECORD;
    rel REGCLASS;
    actual_cols TEXT;
    actual_pk TEXT;
BEGIN
    FOR spec IN SELECT * FROM (VALUES
        ('personality_feature_modes',
         'league_id bigint NO, feature text NO, mode text NO, version text NO, config jsonb NO, review_ref text YES, '
         'shadow_since_season integer YES, shadow_since_tick smallint YES, set_by text NO, '
         'set_at timestamp with time zone NO',
         'league_id,feature'),
        ('personality_feature_mode_changes',
         'id bigint NO, league_id bigint NO, feature text NO, from_mode text YES, to_mode text NO, version text NO, '
         'review_ref text YES, force_reason text YES, at_season integer NO, at_tick smallint NO, set_by text NO, '
         'created_at timestamp with time zone NO',
         'id'),
        ('personality_feature_clearances',
         'league_id bigint NO, feature text NO, version text NO, source_db text NO, source_label text NO, '
         'shadow_from_season integer NO, shadow_from_tick smallint NO, shadow_to_season integer NO, '
         'shadow_to_tick smallint NO, report_path text NO, report_sha256 text NO, config jsonb NO, cleared_by text NO, '
         'cleared_at timestamp with time zone NO',
         'league_id,feature,version')
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

CREATE TABLE IF NOT EXISTS personality_feature_modes (
    league_id BIGINT NOT NULL,
    feature TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('off','shadow','active')),
    version TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}',
    review_ref TEXT,                       -- JR's review note or report reference
    shadow_since_season INT,               -- when the current version entered shadow (D3)
    shadow_since_tick SMALLINT,
    set_by TEXT NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_id, feature)
);

CREATE TABLE IF NOT EXISTS personality_feature_mode_changes (
    id BIGSERIAL PRIMARY KEY,
    league_id BIGINT NOT NULL,
    feature TEXT NOT NULL,
    from_mode TEXT CHECK (from_mode IN ('off','shadow','active')),
    to_mode TEXT NOT NULL CHECK (to_mode IN ('off','shadow','active')),
    version TEXT NOT NULL,
    review_ref TEXT,
    force_reason TEXT,                     -- set when JR skipped the order or shadow-season rule
    at_season INT NOT NULL,
    at_tick SMALLINT NOT NULL,
    set_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS personality_feature_clearances (
    league_id BIGINT NOT NULL,
    feature TEXT NOT NULL,
    version TEXT NOT NULL,
    source_db TEXT NOT NULL,               -- e.g. madden_tune_2026a
    source_label TEXT NOT NULL,            -- e.g. CAREER-TUNE-2026a
    shadow_from_season INT NOT NULL,
    shadow_from_tick SMALLINT NOT NULL,
    shadow_to_season INT NOT NULL,
    shadow_to_tick SMALLINT NOT NULL,
    report_path TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
    config JSONB NOT NULL,                 -- the config tuned on the tuning franchise
    cleared_by TEXT NOT NULL,
    cleared_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_id, feature, version)
);

COMMIT;
