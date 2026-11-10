-- Every graded pick and call a writer's relationship row has already been charged for (M-0167; PROJECT_SPEC §7.7).
-- The evidence window holds five events, so a ref could be trimmed out and a forced rerun of an old season applied
-- it again, raising trust a second time. The ref is now recorded as applied once, per (writer, subject), in its own
-- row: an exact fact the guard reads instead of inferring anything from the order of the evidence. Rows written
-- before this table are backfilled once by `db/migrations/2027_02_22_backfill_applied_refs.py`.
--
-- Apply with:
--   python3 -m db.migrate apply --file 2027_02_22_relationship_applied_refs.sql

BEGIN;

CREATE TABLE IF NOT EXISTS persona_relationship_event_refs (
    persona_type VARCHAR(32) NOT NULL,
    persona_id VARCHAR(64) NOT NULL,
    target_type VARCHAR(32) NOT NULL,
    target_id VARCHAR(64) NOT NULL,
    ref VARCHAR(64) NOT NULL,               -- 'pick:<agent_predictions.id>' or 'call:<agent_season_calls.id>'
    season INT,                             -- the season the graded event belongs to
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (persona_type, persona_id, target_type, target_id, ref)
);

COMMIT;
