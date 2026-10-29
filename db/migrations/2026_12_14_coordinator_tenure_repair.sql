-- @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history data)
-- @REPLACED-BY: db/seed_clean_db.py + db/init_db.sql (old-universe data only; AUDIT_LEDGER.md §3b)
-- Migration: Repair coordinator tenures and head coach slants after the 2036 coaching carousel
--
-- tests/test_coordinators.py::test_seeder_idempotency re-ran engines/coordinator_seeder.py against the live
-- database and committed. The seeder's upsert sets end_season back to NULL and its slant pass defaults unknown
-- coaches to OFFENSIVE, so it undid parts of the 2035 offseason package (captures/offseason/2035/manifest.json):
--   * Coordinators promoted to head coach (Grant, Bell, Phillips, McDaniel, Brown, Flores) were still listed at
--     their old clubs, and the coordinators replaced by the new head coaches' hires were still listed too, so
--     ten teams had two active OCs or DCs for 2036.
--   * Shavon Bell (PHI) and Brian Flores (TEN), hired as DEFENSIVE head coaches, were reset to OFFENSIVE.
-- Separately, five baseline tenures were cross-seeded onto the wrong club (BAL/NO OCs, IND/NO DCs, KC DC); the
-- seeder's BASELINE_STAFF_REFERENCE gives each coordinator's real club.

BEGIN;

-- 1. A tenure that a later-starting tenure for the same club and role supersedes ends the season before it.
UPDATE coordinator_tenures old
SET end_season = sup.start_season - 1
FROM (
    SELECT team_id, role, MIN(start_season) AS start_season
    FROM coordinator_tenures n
    WHERE n.start_season = 2036
    GROUP BY team_id, role
) sup
WHERE old.team_id = sup.team_id AND old.role = sup.role
  AND old.start_season < sup.start_season
  AND (old.end_season IS NULL OR old.end_season >= sup.start_season);

-- 2. Coordinators hired as head coaches for 2036 leave their coordinator jobs after 2035.
UPDATE coordinator_tenures ct
SET end_season = 2035
FROM coordinators c, coach_tenures ht, coaches hc
WHERE c.id = ct.coordinator_id AND hc.id = ht.coach_id
  AND hc.coach_name = c.coordinator_name AND ht.start_season = 2036
  AND ct.start_season < 2036 AND (ct.end_season IS NULL OR ct.end_season >= 2036);

-- 3. Cross-seeded baseline tenures: each coordinator stays only with the club the seeder assigns him.
DELETE FROM coordinator_tenures ct
USING coordinators c, teams t
WHERE c.id = ct.coordinator_id AND t.id = ct.team_id AND ct.start_season = 2026
  AND (c.coordinator_name, ct.role, t.abbreviation) IN (
      ('Ronald Curry', 'OC', 'BAL'),
      ('Declan Doyle', 'OC', 'NO'),
      ('Brandon Staley', 'DC', 'IND'),
      ('Lou Anarumo', 'DC', 'NO'),
      ('Gus Bradley', 'DC', 'KC')
  );

-- 4. Defensive-slant head coaches from the 2035 offseason package.
UPDATE coaches SET slant = 'DEFENSIVE' WHERE coach_name IN ('Shavon Bell', 'Brian Flores');

-- 5. Carolina's OC after Charles Grant left for Indianapolis is David Wallace, former Stanford OC (per JR).
-- He takes over the stand-in the offseason package created; Whitfield's offense stays Wide Zone West Coast.
UPDATE coordinators
SET coordinator_name = 'David Wallace', scheme = 'Wide Zone West Coast', playbook = 'Carolina Offense',
    origin_notes = 'Panthers OC under Marcus Whitfield (2036-present); former Stanford offensive coordinator'
WHERE coordinator_name = 'Panthers Interim OC' AND side = 'OFFENSIVE';

-- 6. Carolina's DC after Shavon Bell left for Philadelphia is Phil Taylor, promoted from Panthers defensive line
-- coach (per JR). He keeps Bell's 3-4 Carolina Defense and its blitz and coverage tendencies.
UPDATE coordinators c
SET coordinator_name = 'Phil Taylor', scheme = bell.scheme, playbook = bell.playbook,
    blitz_rate = bell.blitz_rate, man_coverage = bell.man_coverage,
    origin_notes = 'Panthers DC under Marcus Whitfield (2036-present); promoted from Panthers defensive line coach'
FROM coordinators bell
WHERE bell.coordinator_name = 'Shavon Bell' AND bell.side = 'DEFENSIVE'
  AND c.coordinator_name = 'Panthers Interim DC' AND c.side = 'DEFENSIVE';

COMMIT;
