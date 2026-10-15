-- Record book: full names for the last three short-form holders (JR, Oct 15, 2026; PROJECT_SPEC §2.9).
-- None of them is in the league in 2035; JR named them:
--   B. Ojulari -> Bayo Ojulari (Cardinals rookie single-game sacks, 2 in 2028; retired before 2035)
--   M. Fields  -> Malachi Fields (Giants receiving TDs in a season, 15 in 2029)
--   M. Wilson  -> Michael Wilson (Giants receiving yards in a game, 293 in 2031)
-- 2026_10_28_league_record_book.sql now seeds these names, so re-running it after this matches these rows.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_11_17_record_book_names_2.sql

BEGIN;

UPDATE franchise_records SET holder = 'Bayo Ojulari' WHERE holder = 'B. Ojulari' AND source = 'BOOK';
UPDATE franchise_records SET holder = 'Malachi Fields' WHERE holder = 'M. Fields' AND source = 'BOOK';
UPDATE franchise_records SET holder = 'Michael Wilson' WHERE holder = 'M. Wilson' AND source = 'BOOK';
UPDATE league_record_entries SET holder = 'Bayo Ojulari' WHERE holder = 'B. Ojulari' AND source = 'BOOK';
UPDATE league_record_entries SET holder = 'Malachi Fields' WHERE holder = 'M. Fields' AND source = 'BOOK';
UPDATE league_record_entries SET holder = 'Michael Wilson' WHERE holder = 'M. Wilson' AND source = 'BOOK';

COMMIT;
