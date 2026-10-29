-- @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history data)
-- @REPLACED-BY: db/seed_clean_db.py + db/init_db.sql (old-universe data only; AUDIT_LEDGER.md §3b)
-- Migration: Name the Colts' 2036 offensive coordinator
-- When Indianapolis hired Charles Grant (2035 offseason package), the package filled the OC job with the stand-in
-- "Charles Grant Offensive Lieutenant". Per JR he is Grant's former Panthers quarterbacks coach; JR asked Claude to
-- name him: Evan Marsh. He keeps Grant's Wide Zone West Coast scheme and the Colts Offense playbook.

BEGIN;

UPDATE coordinators
SET coordinator_name = 'Evan Marsh',
    origin_notes = 'Colts OC under Charles Grant (2036-present); followed Grant from Carolina, where he was the Panthers quarterbacks coach'
WHERE coordinator_name = 'Charles Grant Offensive Lieutenant' AND side = 'OFFENSIVE';

COMMIT;
