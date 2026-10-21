-- Migration: Name the remaining 2036 stand-in coordinators
-- The 2035 offseason package (newsroom/offseason_service.py) filled nine coordinator jobs with stand-ins
-- ("<Team> Interim OC/DC", "<Coach> Offensive/Defensive Lieutenant"). JR asked Claude to name them all.
--   * Interim jobs (a coordinator left to become a head coach elsewhere): an internal promotion who keeps his
--     predecessor's scheme, playbook and tendencies.
--   * Lieutenant jobs (a new head coach's hire): an assistant who followed the head coach from his old club,
--     keeping the scheme the package gave him.
-- Names were checked against coaches, coordinators and players for collisions.

BEGIN;

-- 1. Internal promotions: take over the predecessor's system (tenure that ended in 2035).
UPDATE coordinators c
SET coordinator_name = v.new_name, origin_notes = v.notes,
    scheme = prev.scheme, playbook = prev.playbook,
    pass_lean = prev.pass_lean, blitz_rate = prev.blitz_rate, man_coverage = prev.man_coverage
FROM (VALUES
    ('Chargers Interim OC', 'Derek Halvorsen', 'Chargers OC under Jim Harbaugh (2036-present); promoted from Chargers quarterbacks coach when Mike McDaniel left for the Jets', 'LAC', 'OC'),
    ('Rams Interim OC',     'Tyler Voss',      'Rams OC under Sean McVay (2036-present); promoted from Rams pass game coordinator when Thomas Brown left for Green Bay', 'LAR', 'OC'),
    ('Vikings Interim OC',  'Jordan Pruitt',   'Vikings OC under Kevin O''Connell (2036-present); promoted from Vikings quarterbacks coach when Wes Phillips left for Miami', 'MIN', 'OC'),
    ('Vikings Interim DC',  'Calvin Okafor',   'Vikings DC under Kevin O''Connell (2036-present); promoted from Vikings linebackers coach when Brian Flores left for Tennessee', 'MIN', 'DC')
) AS v(old_name, new_name, notes, abbr, role)
JOIN teams t ON t.abbreviation = v.abbr
JOIN coordinator_tenures pt ON pt.team_id = t.id AND pt.role = v.role AND pt.end_season = 2035
JOIN coordinators prev ON prev.id = pt.coordinator_id
WHERE c.coordinator_name = v.old_name;

-- 2. Assistants who followed the new head coach.
UPDATE coordinators c
SET coordinator_name = v.new_name, origin_notes = v.notes
FROM (VALUES
    ('Thomas Brown Offensive Lieutenant',  'Nate Ellery',    'Packers OC under Thomas Brown (2036-present); followed Brown from the Rams, where he was the tight ends coach'),
    ('Wes Phillips Offensive Lieutenant',  'Ross Kincaid',   'Dolphins OC under Wes Phillips (2036-present); followed Phillips from the Vikings, where he was the offensive line coach'),
    ('Mike McDaniel Offensive Lieutenant', 'Sam Delacroix',  'Jets OC under Mike McDaniel (2036-present); followed McDaniel from the Chargers, where he was the pass game coordinator'),
    ('Shavon Bell Defensive Lieutenant',   'Greg Pellerin',  'Eagles DC under Shavon Bell (2036-present); followed Bell from the Panthers, where he was the linebackers coach'),
    ('Brian Flores Defensive Lieutenant',  'Andre Szabo', 'Titans DC under Brian Flores (2036-present); followed Flores from the Vikings, where he was the defensive backs coach')
) AS v(old_name, new_name, notes)
WHERE c.coordinator_name = v.old_name;

COMMIT;
