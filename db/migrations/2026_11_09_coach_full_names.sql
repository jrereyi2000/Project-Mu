-- @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history data)
-- @REPLACED-BY: db/seed_clean_db.py + db/init_db.sql (old-universe data only; AUDIT_LEDGER.md §3b)
-- Full names for the Broncos' and Chiefs' coaches (per JR, Oct 15; PROJECT_SPEC §3.3a).
-- Run once: psql -f db/migrations/2026_11_09_coach_full_names.sql
UPDATE coaches SET coach_name = 'Peter Gaffney' WHERE coach_name = 'P. Gaffney';
UPDATE coaches SET coach_name = 'Ron Leonard' WHERE coach_name = 'R. Leonard';
