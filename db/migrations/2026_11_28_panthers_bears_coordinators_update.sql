-- @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history data)
-- @REPLACED-BY: db/seed_clean_db.py + db/init_db.sql (old-universe data only; AUDIT_LEDGER.md §3b)
-- Migration: Update Carolina and Chicago OC tenures
-- Carolina Panthers:
--   Brad Idzik OC under Dave Canales (2026-2033; tenure ended when Whitfield was hired)
--   Charles Grant OC under Marcus Whitfield (2034-present; past two seasons)
-- Chicago Bears:
--   Thomas Brown OC (2026-2027)
--   Marcus Whitfield OC (2028-2033)
--   Brett Vance OC (2034-present; former college offensive coordinator / quarterbacks coach)

BEGIN;

-- 1. Carolina Panthers: End Brad Idzik's tenure in 2033
UPDATE coordinator_tenures
SET end_season = 2033
WHERE coordinator_id IN (SELECT id FROM coordinators WHERE coordinator_name = 'Brad Idzik' AND side = 'OFFENSIVE')
  AND team_id IN (SELECT id FROM teams WHERE abbreviation = 'CAR')
  AND role = 'OC';

UPDATE coordinators
SET origin_notes = 'Panthers OC under Dave Canales (2026-2033)'
WHERE coordinator_name = 'Brad Idzik' AND side = 'OFFENSIVE';

-- 2. Carolina Panthers: Insert Charles Grant (2034-present)
INSERT INTO coordinators (coordinator_name, side, scheme, playbook, pass_lean, origin_notes)
VALUES ('Charles Grant', 'OFFENSIVE', 'Wide Zone West Coast', 'Carolina Offense', 'Low', 'Panthers OC under Marcus Whitfield (2034-present)')
ON CONFLICT DO NOTHING;

INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season)
SELECT c.id, t.id, 'OC', 2034, NULL
FROM coordinators c, teams t
WHERE c.coordinator_name = 'Charles Grant' AND c.side = 'OFFENSIVE'
  AND t.abbreviation = 'CAR'
ON CONFLICT (coordinator_id, team_id, role, start_season)
DO UPDATE SET end_season = EXCLUDED.end_season;

-- 3. Chicago Bears: Remove Thomas Brown 2034 tenure
DELETE FROM coordinator_tenures
WHERE coordinator_id IN (SELECT id FROM coordinators WHERE coordinator_name = 'Thomas Brown' AND side = 'OFFENSIVE')
  AND team_id IN (SELECT id FROM teams WHERE abbreviation = 'CHI')
  AND role = 'OC'
  AND start_season = 2034;

UPDATE coordinators
SET origin_notes = 'Bears OC under Ben Johnson (2026-2027)'
WHERE coordinator_name = 'Thomas Brown' AND side = 'OFFENSIVE';

-- 4. Chicago Bears: Insert Brett Vance (2034-present)
INSERT INTO coordinators (coordinator_name, side, scheme, playbook, pass_lean, origin_notes)
VALUES ('Brett Vance', 'OFFENSIVE', 'Wide Zone West Coast', 'Chicago Offense', 'Medium', 'Bears OC under Ben Johnson (2034-present); former college offensive coordinator and quarterbacks coach')
ON CONFLICT DO NOTHING;

INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season)
SELECT c.id, t.id, 'OC', 2034, NULL
FROM coordinators c, teams t
WHERE c.coordinator_name = 'Brett Vance' AND c.side = 'OFFENSIVE'
  AND t.abbreviation = 'CHI'
ON CONFLICT (coordinator_id, team_id, role, start_season)
DO UPDATE SET end_season = EXCLUDED.end_season;

COMMIT;
