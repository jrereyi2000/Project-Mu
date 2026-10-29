-- @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history data)
-- @REPLACED-BY: db/seed_clean_db.py + db/init_db.sql (old-universe data only; AUDIT_LEDGER.md §3b)
-- Correct head coach tenures: update active team tenures to actual hiring years,
-- update 2025 baseline-season coaches, backfill prior historical stints for veteran coaches,
-- and link coach_seasons 2025 baseline rows to team_id (PROJECT_SPEC §3.3a).
BEGIN;

-- 1. Correct start_season for established head coaches with their team
UPDATE coach_tenures ct
SET start_season = v.actual_start
FROM (VALUES
    ('Kyle Shanahan',        'San Francisco', '49ers',      2017),
    ('Sean McVay',          'Los Angeles',   'Rams',       2017),
    ('Matt LaFleur',        'Green Bay',     'Packers',    2019),
    ('Zac Taylor',          'Cincinnati',    'Bengals',    2019),
    ('Dan Campbell',        'Detroit',       'Lions',      2021),
    ('Nick Sirianni',       'Philadelphia',  'Eagles',     2021),
    ('Kevin O''Connell',    'Minnesota',     'Vikings',    2022),
    ('Todd Bowles',         'Tampa Bay',     'Buccaneers', 2022),
    ('DeMeco Ryans',        'Houston',       'Texans',     2023),
    ('Shane Steichen',      'Indianapolis',  'Colts',      2023),
    ('Dave Canales',        'Carolina',      'Panthers',   2024),
    ('Jim Harbaugh',        'Los Angeles',   'Chargers',   2024),
    ('Mike Macdonald',      'Seattle',       'Seahawks',   2024),
    ('Dan Quinn',           'Washington',    'Commanders', 2024)
) AS v(coach_name, city, team_name, actual_start)
JOIN coaches c ON c.coach_name = v.coach_name
JOIN teams t ON t.city = v.city AND t.name = v.team_name
WHERE ct.coach_id = c.id 
  AND ct.team_id = t.id 
  AND ct.start_season = 2026;

-- 2. Correct start_season for coaches whose 17-game baseline was 2025
UPDATE coach_tenures ct
SET start_season = 2025
FROM (VALUES
    ('Ben Johnson',          'Chicago',      'Bears'),
    ('Brian Schottenheimer', 'Dallas',       'Cowboys'),
    ('Liam Coen',            'Jacksonville', 'Jaguars'),
    ('Kellen Moore',         'New Orleans',  'Saints'),
    ('Aaron Glenn',          'New York',     'Jets')
) AS v(coach_name, city, team_name)
JOIN coaches c ON c.coach_name = v.coach_name
JOIN teams t ON t.city = v.city AND t.name = v.team_name
WHERE ct.coach_id = c.id 
  AND ct.team_id = t.id 
  AND ct.start_season = 2026;

-- 3. Backfill prior historical tenures for veteran head coaches
INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season)
SELECT c.id, t.id, v.start_s, v.end_s
FROM (VALUES
    ('John Harbaugh',   'Baltimore',     'Ravens',   2008, 2025),
    ('Mike McCarthy',   'Green Bay',     'Packers',  2006, 2018),
    ('Mike McCarthy',   'Dallas',        'Cowboys',  2020, 2024),
    ('Kevin Stefanski', 'Cleveland',     'Browns',   2020, 2025),
    ('Mike Vrabel',     'Tennessee',     'Titans',   2018, 2023),
    ('Robert Saleh',    'New York',      'Jets',     2021, 2024),
    ('Todd Bowles',     'New York',      'Jets',     2015, 2018),
    ('Dan Quinn',       'Atlanta',       'Falcons',  2015, 2020),
    ('Jim Harbaugh',    'San Francisco', '49ers',    2011, 2014)
) AS v(coach_name, city, team_name, start_s, end_s)
JOIN coaches c ON c.coach_name = v.coach_name
JOIN teams t ON t.city = v.city AND t.name = v.team_name
ON CONFLICT (coach_id, team_id, start_season) DO NOTHING;

-- 4. Link 2025 coach_seasons baseline rows to their team_id where active in 2025
UPDATE coach_seasons cs
SET team_id = ct.team_id
FROM coach_tenures ct
WHERE cs.coach_id = ct.coach_id
  AND cs.season = 2025
  AND cs.team_id IS NULL
  AND ct.start_season <= 2025 
  AND (ct.end_season IS NULL OR ct.end_season >= 2025);

COMMIT;
