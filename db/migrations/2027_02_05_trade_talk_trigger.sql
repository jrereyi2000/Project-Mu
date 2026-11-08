-- The trigger a trade talk was opened from (docs/done/TRADE_NEGOTIATIONS_PLAN.md; PROJECT_SPEC §2.17 Talks, §3.3f).
-- A CPU talk can carry over a week, so the deal's trigger has to live on the row: without it, a carried talk's
-- eventual AGREED / STALLED proposal records NEED whatever opened it. Nullable: a JR talk has no engine trigger.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_02_05_trade_talk_trigger.sql

BEGIN;

ALTER TABLE gm_trade_negotiations ADD COLUMN IF NOT EXISTS trigger_kind VARCHAR(8)
  CHECK (trigger_kind IN ('NEED', 'INJURY', 'SELL', 'DRAFT', 'USER'));

COMMIT;
