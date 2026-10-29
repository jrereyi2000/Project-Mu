-- @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history data)
-- @REPLACED-BY: db/seed_clean_db.py + db/init_db.sql (old-universe data only; AUDIT_LEDGER.md §3b)
-- Real-life 2026 schemes for the 32 head coaches of 2026 (PROJECT_SPEC §3.3a). The offense is the family the
-- staff runs (head coach and 2026 offensive coordinator); the defense is the base front (head coach and 2026
-- defensive coordinator). These are classifications from coaching trees, not official team labels. Coordinators:
-- Wikipedia "List of current NFL offensive/defensive coordinators" (revisions of 2026-07-12).
-- Run once after 2026_10_31_coach_baseline.sql: psql -f db/migrations/2026_11_01_coach_schemes.sql
BEGIN;

UPDATE coaches c SET offensive_scheme = v.off, defensive_scheme = v.def
FROM (VALUES
    ('Mike LaFleur',         'Wide Zone West Coast', '3-4'),  -- DC Nick Rallis
    ('Kevin Stefanski',      'Wide Zone West Coast', '4-3'),  -- DC Jeff Ulbrich
    ('Jesse Minter',         'Wide Zone West Coast', '3-4'),  -- OC Declan Doyle (Ben Johnson tree), DC Anthony Weaver
    ('Joe Brady',            'Spread',               '3-4'),  -- OC Pete Carmichael Jr., DC Jim Leonhard (3-4 switch)
    ('Dave Canales',         'Wide Zone West Coast', '3-4'),  -- DC Ejiro Evero (Fangio tree)
    ('Ben Johnson',          'Wide Zone West Coast', '4-3'),  -- DC Dennis Allen
    ('Zac Taylor',           'Spread',               '4-3'),  -- DC Al Golden
    ('Todd Monken',          'Spread',               '4-3'),  -- DC Mike Rutenberg
    ('Brian Schottenheimer', 'West Coast',           '3-4'),  -- DC Christian Parker (3-4 switch)
    ('Sean Payton',          'West Coast',           '3-4'),  -- DC Vance Joseph
    ('Dan Campbell',         'West Coast',           '4-3'),  -- OC Drew Petzing, DC Kelvin Sheppard
    ('Matt LaFleur',         'Wide Zone West Coast', '3-4'),  -- DC Jonathan Gannon
    ('DeMeco Ryans',         'Wide Zone West Coast', '4-3'),  -- OC Nick Caley (McVay tree), DC Matt Burke
    ('Shane Steichen',       'Spread',               '4-3'),  -- DC Lou Anarumo
    ('Liam Coen',            'Wide Zone West Coast', '4-3'),  -- DC Anthony Campanile
    ('Andy Reid',            'West Coast',           '4-3'),  -- DC Steve Spagnuolo
    ('Klint Kubiak',         'Wide Zone West Coast', '4-3'),  -- DC Rob Leonard
    ('Jim Harbaugh',         'Wide Zone West Coast', '3-4'),  -- OC Mike McDaniel, DC Chris O'Leary (Minter tree)
    ('Sean McVay',           'Wide Zone West Coast', '3-4'),  -- DC Chris Shula
    ('Jeff Hafley',          'Wide Zone West Coast', '4-3'),  -- OC Bobby Slowik (Shanahan tree), DC Sean Duggan
    ('Kevin O''Connell',     'Wide Zone West Coast', '3-4'),  -- DC Brian Flores
    ('Mike Vrabel',          'Erhardt-Perkins',      '3-4'),  -- OC Josh McDaniels, DC Zak Kuhr
    ('Kellen Moore',         'Spread',               '3-4'),  -- DC Brandon Staley
    ('John Harbaugh',        'West Coast',           '3-4'),  -- OC Matt Nagy, DC Dennard Wilson
    ('Aaron Glenn',          'West Coast',           '4-3'),  -- OC Frank Reich, DC Brian Duker
    ('Nick Sirianni',        'Spread',               '3-4'),  -- DC Vic Fangio
    ('Mike McCarthy',        'West Coast',           '3-4'),  -- DC Patrick Graham
    ('Kyle Shanahan',        'Wide Zone West Coast', '4-3'),  -- DC Raheem Morris
    ('Mike Macdonald',       'Wide Zone West Coast', '3-4'),  -- OC Brian Fleury (49ers tree), DC Aden Durde
    ('Todd Bowles',          'Wide Zone West Coast', '3-4'),  -- OC Zac Robinson (McVay tree)
    ('Robert Saleh',         'Erhardt-Perkins',      '4-3'),  -- OC Brian Daboll, DC Gus Bradley
    ('Dan Quinn',            'Spread',               '4-3')   -- OC David Blough
) AS v(coach_name, off, def)
WHERE c.coach_name = v.coach_name;

DO $$ BEGIN
    IF (SELECT count(*) FROM coaches WHERE offensive_scheme IS NOT NULL AND defensive_scheme IS NOT NULL) <> 32
    THEN RAISE EXCEPTION 'expected schemes for the 32 coaches of 2026'; END IF;
END $$;

COMMIT;
