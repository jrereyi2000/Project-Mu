-- One active contract per person, per team, per role (chunk-7 audit, M-0044's migration note).
-- `staff_contracts` had no uniqueness beyond its id, so a re-signed coach or a double-submitted offer could leave a
-- person on two active rows and draw two salaries. Both databases hold no duplicate today (checked 2026-11-09), so
-- the index is a guard, not a repair. It is partial: a settled row keeps `remaining_years = 0` and may coexist with
-- a newer one, which is how the GM lifecycle and `_payroll` already read the table.
--
-- Apply with:
--   python3 -m db.migrate apply --file 2027_02_17_staff_contract_active_once.sql

BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS staff_contracts_active_once
    ON staff_contracts (team_id, person_kind, person_id) WHERE remaining_years > 0;

COMMIT;
