-- Dead buyouts reach the owner's cash (chunk-9 audit B4; PROJECT_SPEC §2.17h/§3.3z).
-- STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN §2.2 defines the owner's available CapEx as pool − payroll − OpEx − dead
-- buyouts, and the spec says the ledger deducts them — but nothing read `staff_contracts.buyout_owed`, so an owner
-- who had just fired a coach spent the same facility cash as one who had not. The ledger records the charge now,
-- and a fired row remembers the season its buyout was charged in so it is charged once, not every season.
--
-- Apply with:
--   python3 -m db.migrate apply --file 2027_02_19_owner_cash_buyouts.sql

BEGIN;

ALTER TABLE owner_cash_ledgers
  ADD COLUMN IF NOT EXISTS dead_buyouts BIGINT NOT NULL DEFAULT 0;   -- the buyouts deducted from this season's cash

ALTER TABLE staff_contracts
  ADD COLUMN IF NOT EXISTS buyout_charged_season INT;                -- the season the buyout was deducted in; NULL = not yet

COMMIT;
