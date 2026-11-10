-- Staff buyouts are paid over the remaining contract years (JR, 2026-11-10, via the chunk-9 audit).
-- A firing moves the remaining guaranteed salary into `buyout_owed`; the owner's cash should carry it one league year
-- at a time, over the years the deal had left, not in a single season. The schedule is stored on the row so a rerun
-- of any season computes the same instalment: `buyout_total` is the whole buyout, `buyout_fired_season` the league
-- year it started, and `buyout_years` how many instalments it is split into. `buyout_charged_season` (2027_02_19)
-- is superseded and no longer written; it stays for the databases that already have it.
--
-- Apply with:
--   python3 -m db.migrate apply --file 2027_02_20_buyout_schedule.sql

BEGIN;

ALTER TABLE staff_contracts
  ADD COLUMN IF NOT EXISTS buyout_total BIGINT,       -- the whole buyout being paid off
  ADD COLUMN IF NOT EXISTS buyout_fired_season INT,   -- the first league year charged
  ADD COLUMN IF NOT EXISTS buyout_years SMALLINT;     -- how many league years it is spread over

-- Rows fired before this migration have no schedule: they keep their buyout_owed for the record and are not charged
-- again (a NULL schedule reads as "already paid"). No migrated database held such a row on 2026-11-10.

COMMIT;
