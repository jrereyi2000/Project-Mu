-- Record book: full names for two short-form holders (JR, Oct 15, 2026; PROJECT_SPEC §2.9).
-- 2026_10_28_league_record_book.sql kept the record book's short form where the roster didn't match a single player.
-- The 2035 roster (after the name-casing fix) has exactly one of each: A. Lowery is Antoine Lowery (Bills QB, a 2035
-- rookie, matching his Bills rookie records) and B. Gore is Ben Gore (running back, now with the Steelers; his Jets
-- rushing-TD records are from 2033 and 2034). B. Ojulari, M. Fields and M. Wilson aren't on the 2035 roster, so they
-- keep the short form. 2026_10_28 now seeds the full names, so re-running it after this matches these rows.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_11_16_record_book_names.sql

BEGIN;

UPDATE franchise_records SET holder = 'Antoine Lowery' WHERE holder = 'A. Lowery' AND source = 'BOOK';
UPDATE franchise_records SET holder = 'Ben Gore' WHERE holder = 'B. Gore' AND source = 'BOOK';
UPDATE league_record_entries SET holder = 'Antoine Lowery' WHERE holder = 'A. Lowery' AND source = 'BOOK';
UPDATE league_record_entries SET holder = 'Ben Gore' WHERE holder = 'B. Gore' AND source = 'BOOK';

COMMIT;
