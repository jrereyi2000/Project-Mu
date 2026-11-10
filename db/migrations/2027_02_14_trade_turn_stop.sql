-- The stop each trade-talk turn was played at (chunk-4 B8; PROJECT_SPEC §3.3f).
-- A turn recorded no time of its own: talk_json stamped every turn with the talk's opening week, and resume_talks
-- had nothing to compare against, so re-running a week (`--replay-week`, a retried run) advanced every carried
-- CPU talk a second time. Each turn now records the (week, offseason stage) it was played at; resume_talks skips
-- a talk whose newest turn was already played at the run's own stop. stage is NULL in season, and the offseason
-- keeps week 24 for every stage, so the stage is what moves a carried talk across an offseason stop.
--
-- Apply with:
--   python3 -m db.migrate apply --file 2027_02_14_trade_turn_stop.sql

BEGIN;

ALTER TABLE gm_trade_negotiation_turns
  ADD COLUMN IF NOT EXISTS week SMALLINT,           -- engine week the turn was played at (offseason 24)
  ADD COLUMN IF NOT EXISTS stage SMALLINT;          -- the offseason stage it was played at, NULL in season

-- Turns written before this migration have no stop of their own; the talk's own week/stage is the closest record.
UPDATE gm_trade_negotiation_turns t SET week = n.week, stage = n.offseason_stage
  FROM gm_trade_negotiations n WHERE n.id = t.negotiation_id AND t.week IS NULL;

COMMIT;
