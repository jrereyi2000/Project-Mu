-- @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history data)
-- @REPLACED-BY: db/seed_clean_db.py + db/init_db.sql (old-universe data only; AUDIT_LEDGER.md §3b)
-- Migration: End Ejiro Evero's tenure as Panthers DC in 2033; add Shavon Bell as Panthers DC (2034-present)
BEGIN;

-- 1. End Evero's tenure in 2033
UPDATE coordinator_tenures
SET end_season = 2033
WHERE coordinator_id IN (SELECT id FROM coordinators WHERE coordinator_name = 'Ejiro Evero' AND side = 'DEFENSIVE')
  AND team_id IN (SELECT id FROM teams WHERE abbreviation = 'CAR')
  AND role = 'DC';

UPDATE coordinators
SET origin_notes = 'Panthers DC under Dave Canales (2026-2033); Fangio defensive tree'
WHERE coordinator_name = 'Ejiro Evero' AND side = 'DEFENSIVE';

-- 2. Insert Shavon Bell as DC (2034-present)
INSERT INTO coordinators (coordinator_name, side, scheme, playbook, blitz_rate, man_coverage, origin_notes)
VALUES ('Shavon Bell', 'DEFENSIVE', '3-4', 'Carolina Defense', 'Medium', 'Low', 'Panthers DC under Marcus Whitfield (2034-present)')
ON CONFLICT DO NOTHING;

INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season)
SELECT c.id, t.id, 'DC', 2034, NULL
FROM coordinators c, teams t
WHERE c.coordinator_name = 'Shavon Bell' AND c.side = 'DEFENSIVE'
  AND t.abbreviation = 'CAR'
ON CONFLICT (coordinator_id, team_id, role, start_season)
DO UPDATE SET end_season = EXCLUDED.end_season;

COMMIT;
