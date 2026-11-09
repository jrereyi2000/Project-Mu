-- Persist a CPU trade talk's stored-path flags (CLOSE_OUT_CHECKLIST P1-16; TRADE_NEGOTIATIONS_PLAN U8 follow-up).
-- A talk that carries over a week kept its shape:/stage:/release: flags in the caller's memory only, so the
-- proposal its later agreement stored carried `talks:` alone: a carried blockbuster escaped MAX_BLOCKBUSTERS and
-- an offseason carry escaped its stage expiry. The flags now live on the row.
--
-- Apply with:
--   python3 -m db.migrate apply --file 2027_02_12_trade_talk_flags.sql

BEGIN;

ALTER TABLE gm_trade_negotiations ADD COLUMN IF NOT EXISTS flags TEXT[] NOT NULL DEFAULT '{}';

COMMIT;
