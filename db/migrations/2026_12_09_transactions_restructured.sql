-- League Transactions: contract restructures (seen on the console 2026-10-19:
-- "BEARS HAVE RESTRUCTURED THE CONTRACT OF (QB) TY SIMPSON GENERATING $11.3M IN ADDITIONAL CAP SPACE").
-- 2026_12_08_transactions.sql is already applied live, so the type check is widened here.
ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_type_check;
ALTER TABLE transactions ADD CONSTRAINT transactions_type_check
    CHECK (type IN ('SIGNED', 'RELEASED', 'TRADED', 'RE_SIGNED', 'EXTENDED', 'RESTRUCTURED', 'OTHER'));
