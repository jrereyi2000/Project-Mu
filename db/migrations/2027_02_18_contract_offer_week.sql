-- The week a contract offer was made (P5, chunk-1 audit; PROJECT_SPEC §3.3d/§2.21b).
-- A REPORTER move interview is offered for a move made this week or the week before, and a signing had nothing to
-- measure that against: `contract_offers` stored the season and the stage but no week, so every signing of the season
-- could still be asked about in week 18. `week` is the engine week the offer was written (24+ in the offseason, the
-- same clock `league_office_runs.week` uses for a trade).
--
-- No backfill: every migrated database held zero contract_offers rows on 2026-11-10 (madden_test and madden_mu2026).
-- A row written before this migration — if one ever exists in an older copy — keeps NULL, and NULL is out of window:
-- an undated signing gets no interview rather than a stale one.
--
-- Apply with:
--   python3 -m db.migrate apply --file 2027_02_18_contract_offer_week.sql

BEGIN;

ALTER TABLE contract_offers
  ADD COLUMN IF NOT EXISTS week SMALLINT;            -- engine week the offer was made; NULL = before this migration

COMMIT;
