-- @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history data)
-- @REPLACED-BY: db/seed_clean_db.py + db/init_db.sql (old-universe data only; AUDIT_LEDGER.md §3b)
-- Migration: Backfill coordinator lore for Marcus Whitfield and Chicago Bears
-- Marcus Whitfield was the Bears offensive coordinator from 2028-2033 under Ben Johnson
-- until getting hired as the Panthers head coach in 2034.
-- In Chicago:
--   Thomas Brown OC: 2026-2027
--   Marcus Whitfield OC: 2028-2033
--   Thomas Brown OC: 2034-present
-- In Carolina:
--   Brad Idzik OC: 2026-present (under Canales 2026-2033 and Whitfield 2034-present)
--   (Remove erroneous Carolina OC tenure for Whitfield)

BEGIN;

-- 1. Update Marcus Whitfield coordinator origin notes
UPDATE coordinators
SET origin_notes = 'Bears offensive coordinator under Ben Johnson (2028-2033) until hired as Panthers head coach in 2034'
WHERE coordinator_name = 'Marcus Whitfield' AND side = 'OFFENSIVE';

-- 2. Remove erroneous Carolina Panthers OC tenure for Whitfield
DELETE FROM coordinator_tenures
WHERE coordinator_id IN (SELECT id FROM coordinators WHERE coordinator_name = 'Marcus Whitfield' AND side = 'OFFENSIVE')
  AND team_id IN (SELECT id FROM teams WHERE abbreviation = 'CAR')
  AND role = 'OC';

-- 3. Update Thomas Brown's first Chicago tenure to end in 2027
UPDATE coordinator_tenures
SET end_season = 2027
WHERE coordinator_id IN (SELECT id FROM coordinators WHERE coordinator_name = 'Thomas Brown' AND side = 'OFFENSIVE')
  AND team_id IN (SELECT id FROM teams WHERE abbreviation = 'CHI')
  AND role = 'OC'
  AND start_season = 2026;

-- 4. Insert Whitfield's Chicago tenure (2028-2033)
INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season)
SELECT c.id, t.id, 'OC', 2028, 2033
FROM coordinators c, teams t
WHERE c.coordinator_name = 'Marcus Whitfield' AND c.side = 'OFFENSIVE'
  AND t.abbreviation = 'CHI'
ON CONFLICT (coordinator_id, team_id, role, start_season)
DO UPDATE SET end_season = EXCLUDED.end_season;

-- 5. Insert Thomas Brown's second Chicago tenure (2034-present)
INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season)
SELECT c.id, t.id, 'OC', 2034, NULL
FROM coordinators c, teams t
WHERE c.coordinator_name = 'Thomas Brown' AND c.side = 'OFFENSIVE'
  AND t.abbreviation = 'CHI'
ON CONFLICT (coordinator_id, team_id, role, start_season)
DO UPDATE SET end_season = EXCLUDED.end_season;

UPDATE coordinators
SET origin_notes = 'Bears OC under Ben Johnson (2026-2027, 2034-present)'
WHERE coordinator_name = 'Thomas Brown' AND side = 'OFFENSIVE';

-- 6. Ensure Brad Idzik in Carolina starts in 2026
-- Handle conflict in case (coordinator_id, team_id, role, 2026) already exists or update existing 2034 row
DELETE FROM coordinator_tenures
WHERE coordinator_id IN (SELECT id FROM coordinators WHERE coordinator_name = 'Brad Idzik' AND side = 'OFFENSIVE')
  AND team_id IN (SELECT id FROM teams WHERE abbreviation = 'CAR')
  AND role = 'OC';

INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season)
SELECT c.id, t.id, 'OC', 2026, NULL
FROM coordinators c, teams t
WHERE c.coordinator_name = 'Brad Idzik' AND c.side = 'OFFENSIVE'
  AND t.abbreviation = 'CAR';

UPDATE coordinators
SET origin_notes = 'Panthers OC under Dave Canales (2026-2033) and Marcus Whitfield (2034-present)'
WHERE coordinator_name = 'Brad Idzik' AND side = 'OFFENSIVE';

COMMIT;
