-- Postseason records: national all-time NFL postseason leaderboards through 2025-26.
-- Augments the record book with season_type ('REGULAR', 'POSTSEASON') on record_categories,
-- expands scope to allow 'CAREER' and subject to allow 'COACH'.
-- Covers:
--   1. Single-game postseason records (Game)
--   2. Single-postseason tournament runs (Season)
--   3. Super Bowl game records (Game)
--   4. Super Bowl wins (Career: Player, Coach, Franchise)
--   5. Playoff career records (Career: Passing, Rushing, Receiving, Sacks, INTs, QB wins, Coach wins)
--
-- Apply:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_11_23_postseason_records.sql

BEGIN;

-- 1. Drop dependent views first so column types can be altered
DROP VIEW IF EXISTS league_records;
DROP VIEW IF EXISTS league_top10;
DROP VIEW IF EXISTS franchise_records_current;

-- 2. Schema adjustments on record_categories
ALTER TABLE record_categories ADD COLUMN IF NOT EXISTS season_type VARCHAR(16) NOT NULL DEFAULT 'REGULAR'
    CHECK (season_type IN ('REGULAR', 'POSTSEASON'));

ALTER TABLE record_categories ALTER COLUMN scope TYPE VARCHAR(10);
ALTER TABLE record_categories DROP CONSTRAINT IF EXISTS record_categories_scope_check;
ALTER TABLE record_categories ADD CONSTRAINT record_categories_scope_check CHECK (scope IN ('SEASON', 'GAME', 'CAREER'));

ALTER TABLE record_categories DROP CONSTRAINT IF EXISTS record_categories_subject_check;
ALTER TABLE record_categories ADD CONSTRAINT record_categories_subject_check CHECK (subject IN ('PLAYER', 'TEAM', 'COACH'));

-- 3. Recreate views exposing season_type
CREATE VIEW franchise_records_current AS
SELECT fr.*, c.label, c.scope, c.subject, c.better, c.season_type
FROM franchise_records fr
JOIN record_categories c ON c.category = fr.category
WHERE fr.value = (SELECT CASE WHEN c.better = 'LOW' THEN MIN(x.value) ELSE MAX(x.value) END
                  FROM franchise_records x WHERE x.team_id = fr.team_id AND x.category = fr.category);

CREATE VIEW league_top10 AS
SELECT * FROM (
    SELECT e.*, c.label, c.scope, c.subject, c.better, c.season_type,
           RANK() OVER (PARTITION BY e.category
                        ORDER BY CASE WHEN c.better = 'LOW' THEN e.value ELSE -e.value END) AS rank
    FROM league_record_entries e JOIN record_categories c ON c.category = e.category
) ranked WHERE rank <= 10;

CREATE VIEW league_records AS
WITH cands AS (
    SELECT fr.category, fr.value, fr.holder, fr.team_id, fr.player_id, fr.season, NULL::date AS game_date, NULL::int AS game_id, fr.source
    FROM franchise_records_current fr JOIN record_categories c ON c.category = fr.category
    WHERE c.scope = 'SEASON' AND c.coverage = 'FULL' AND c.season_type = 'REGULAR'
    UNION
    SELECT t.category, t.value, t.holder, t.team_id, t.player_id, t.season, t.game_date, t.game_id, t.source
    FROM league_top10 t WHERE t.rank = 1
), best AS (
    SELECT c.category, CASE WHEN rc.better = 'LOW' THEN MIN(c.value) ELSE MAX(c.value) END AS value
    FROM cands c JOIN record_categories rc ON rc.category = c.category GROUP BY c.category, rc.better
)
SELECT DISTINCT ON (c.category, c.holder, c.season)
       c.category, rc.label, rc.scope, rc.subject, rc.season_type, c.value, c.holder, c.team_id, c.player_id, c.season,
       c.game_date, c.game_id, c.source
FROM cands c
JOIN best b ON b.category = c.category AND b.value = c.value
JOIN record_categories rc ON rc.category = c.category
ORDER BY c.category, c.holder, c.season, c.game_date NULLS LAST;

-- 4. Insert Postseason Categories
INSERT INTO record_categories (category, label, scope, subject, better, sort_order, coverage, season_type) VALUES
    -- Single-game postseason records
    ('postseason_game_pass_yards', 'Passing yards in a postseason game', 'GAME', 'PLAYER', 'HIGH', 50, 'FULL', 'POSTSEASON'),
    ('postseason_game_pass_tds', 'Passing TDs in a postseason game', 'GAME', 'PLAYER', 'HIGH', 51, 'FULL', 'POSTSEASON'),
    ('postseason_game_rush_yards', 'Rushing yards in a postseason game', 'GAME', 'PLAYER', 'HIGH', 52, 'FULL', 'POSTSEASON'),
    ('postseason_game_rush_tds', 'Rushing TDs in a postseason game', 'GAME', 'PLAYER', 'HIGH', 53, 'FULL', 'POSTSEASON'),
    ('postseason_game_rec_yards', 'Receiving yards in a postseason game', 'GAME', 'PLAYER', 'HIGH', 54, 'FULL', 'POSTSEASON'),
    ('postseason_game_receptions', 'Receptions in a postseason game', 'GAME', 'PLAYER', 'HIGH', 55, 'FULL', 'POSTSEASON'),
    ('postseason_game_rec_tds', 'Receiving TDs in a postseason game', 'GAME', 'PLAYER', 'HIGH', 56, 'FULL', 'POSTSEASON'),
    ('postseason_game_sacks', 'Sacks in a postseason game', 'GAME', 'PLAYER', 'HIGH', 57, 'FULL', 'POSTSEASON'),
    ('postseason_game_def_ints', 'Interceptions in a postseason game', 'GAME', 'PLAYER', 'HIGH', 58, 'FULL', 'POSTSEASON'),

    -- Single-season postseason runs
    ('postseason_run_pass_yards', 'Passing yards in a single postseason', 'SEASON', 'PLAYER', 'HIGH', 60, 'FULL', 'POSTSEASON'),
    ('postseason_run_pass_tds', 'Passing TDs in a single postseason', 'SEASON', 'PLAYER', 'HIGH', 61, 'FULL', 'POSTSEASON'),
    ('postseason_run_rush_yards', 'Rushing yards in a single postseason', 'SEASON', 'PLAYER', 'HIGH', 62, 'FULL', 'POSTSEASON'),
    ('postseason_run_rush_tds', 'Rushing TDs in a single postseason', 'SEASON', 'PLAYER', 'HIGH', 63, 'FULL', 'POSTSEASON'),
    ('postseason_run_rec_yards', 'Receiving yards in a single postseason', 'SEASON', 'PLAYER', 'HIGH', 64, 'FULL', 'POSTSEASON'),
    ('postseason_run_receptions', 'Receptions in a single postseason', 'SEASON', 'PLAYER', 'HIGH', 65, 'FULL', 'POSTSEASON'),
    ('postseason_run_rec_tds', 'Receiving TDs in a single postseason', 'SEASON', 'PLAYER', 'HIGH', 66, 'FULL', 'POSTSEASON'),
    ('postseason_run_sacks', 'Sacks in a single postseason', 'SEASON', 'PLAYER', 'HIGH', 67, 'FULL', 'POSTSEASON'),

    -- Super Bowl single-game records
    ('super_bowl_pass_yards', 'Passing yards in a Super Bowl', 'GAME', 'PLAYER', 'HIGH', 70, 'FULL', 'POSTSEASON'),
    ('super_bowl_pass_tds', 'Passing TDs in a Super Bowl', 'GAME', 'PLAYER', 'HIGH', 71, 'FULL', 'POSTSEASON'),
    ('super_bowl_rush_yards', 'Rushing yards in a Super Bowl', 'GAME', 'PLAYER', 'HIGH', 72, 'FULL', 'POSTSEASON'),
    ('super_bowl_rush_tds', 'Rushing TDs in a Super Bowl', 'GAME', 'PLAYER', 'HIGH', 73, 'FULL', 'POSTSEASON'),
    ('super_bowl_rec_yards', 'Receiving yards in a Super Bowl', 'GAME', 'PLAYER', 'HIGH', 74, 'FULL', 'POSTSEASON'),
    ('super_bowl_receptions', 'Receptions in a Super Bowl', 'GAME', 'PLAYER', 'HIGH', 75, 'FULL', 'POSTSEASON'),
    ('super_bowl_rec_tds', 'Receiving TDs in a Super Bowl', 'GAME', 'PLAYER', 'HIGH', 76, 'FULL', 'POSTSEASON'),
    ('super_bowl_sacks', 'Sacks in a Super Bowl', 'GAME', 'PLAYER', 'HIGH', 77, 'FULL', 'POSTSEASON'),

    -- Super Bowl wins
    ('career_super_bowl_wins_player', 'Super Bowl wins by a player', 'CAREER', 'PLAYER', 'HIGH', 80, 'FULL', 'POSTSEASON'),
    ('career_super_bowl_wins_coach', 'Super Bowl wins by a head coach', 'CAREER', 'COACH', 'HIGH', 81, 'FULL', 'POSTSEASON'),
    ('career_super_bowl_wins_franchise', 'Super Bowl wins by a franchise', 'CAREER', 'TEAM', 'HIGH', 82, 'FULL', 'POSTSEASON'),

    -- Playoff career records
    ('career_postseason_pass_yards', 'Career postseason passing yards', 'CAREER', 'PLAYER', 'HIGH', 90, 'FULL', 'POSTSEASON'),
    ('career_postseason_pass_tds', 'Career postseason passing TDs', 'CAREER', 'PLAYER', 'HIGH', 91, 'FULL', 'POSTSEASON'),
    ('career_postseason_rush_yards', 'Career postseason rushing yards', 'CAREER', 'PLAYER', 'HIGH', 92, 'FULL', 'POSTSEASON'),
    ('career_postseason_rush_tds', 'Career postseason rushing TDs', 'CAREER', 'PLAYER', 'HIGH', 93, 'FULL', 'POSTSEASON'),
    ('career_postseason_rec_yards', 'Career postseason receiving yards', 'CAREER', 'PLAYER', 'HIGH', 94, 'FULL', 'POSTSEASON'),
    ('career_postseason_receptions', 'Career postseason receptions', 'CAREER', 'PLAYER', 'HIGH', 95, 'FULL', 'POSTSEASON'),
    ('career_postseason_rec_tds', 'Career postseason receiving TDs', 'CAREER', 'PLAYER', 'HIGH', 96, 'FULL', 'POSTSEASON'),
    ('career_postseason_sacks', 'Career postseason sacks', 'CAREER', 'PLAYER', 'HIGH', 97, 'FULL', 'POSTSEASON'),
    ('career_postseason_def_ints', 'Career postseason interceptions', 'CAREER', 'PLAYER', 'HIGH', 98, 'FULL', 'POSTSEASON'),
    ('career_postseason_wins_qb', 'Career postseason quarterback wins', 'CAREER', 'PLAYER', 'HIGH', 99, 'FULL', 'POSTSEASON'),
    ('career_postseason_wins_coach', 'Career postseason head coach wins', 'CAREER', 'COACH', 'HIGH', 100, 'FULL', 'POSTSEASON')
ON CONFLICT (category) DO UPDATE SET
    label = EXCLUDED.label, scope = EXCLUDED.scope, subject = EXCLUDED.subject,
    sort_order = EXCLUDED.sort_order, coverage = EXCLUDED.coverage, season_type = EXCLUDED.season_type;

-- 5. Seed all-time NFL postseason history into league_record_entries
INSERT INTO league_record_entries (category, value, holder, team_id, season, game_date, source)
SELECT v.category, v.value, v.holder, t.id, v.season, v.game_date::date, 'NFL'
FROM (VALUES
    -- Single-game postseason passing yards
    ('postseason_game_pass_yards', 505, 'Tom Brady', 'NE', 2017, '2018-02-04'),
    ('postseason_game_pass_yards', 501, 'Ben Roethlisberger', 'PIT', 2020, '2021-01-10'),
    ('postseason_game_pass_yards', 489, 'Bernie Kosar', 'CLE', 1986, '1987-01-03'),
    ('postseason_game_pass_yards', 469, 'Ben Roethlisberger', 'PIT', 2017, '2018-01-14'),
    ('postseason_game_pass_yards', 466, 'Tom Brady', 'NE', 2016, '2017-02-05'),
    ('postseason_game_pass_yards', 466, 'Drew Brees', 'NO', 2011, '2012-01-14'),
    ('postseason_game_pass_yards', 433, 'Dan Fouts', 'LAC', 1981, '1982-01-02'),
    ('postseason_game_pass_yards', 429, 'Josh Allen', 'BUF', 2021, '2022-01-23'),
    ('postseason_game_pass_yards', 421, 'Dan Marino', 'MIA', 1984, '1985-01-06'),
    ('postseason_game_pass_yards', 414, 'Kurt Warner', 'LAR', 1999, '2000-01-30'),

    -- Single-game postseason passing TDs
    ('postseason_game_pass_tds', 6, 'Tom Brady', 'NE', 2011, '2012-01-14'),
    ('postseason_game_pass_tds', 6, 'Steve Young', 'SF', 1994, '1995-01-29'),
    ('postseason_game_pass_tds', 6, 'Daryle Lamonica', 'LV', 1969, '1969-12-21'),
    ('postseason_game_pass_tds', 5, 'Patrick Mahomes', 'KC', 2019, '2020-01-12'),
    ('postseason_game_pass_tds', 5, 'Josh Allen', 'BUF', 2021, '2022-01-15'),
    ('postseason_game_pass_tds', 5, 'Kurt Warner', 'AZ', 2009, '2010-01-10'),
    ('postseason_game_pass_tds', 5, 'Kurt Warner', 'LAR', 1999, '2000-01-16'),
    ('postseason_game_pass_tds', 5, 'Joe Montana', 'SF', 1989, '1990-01-28'),
    ('postseason_game_pass_tds', 5, 'Aaron Rodgers', 'GB', 2014, '2015-01-11'),
    ('postseason_game_pass_tds', 5, 'Drew Brees', 'NO', 2011, '2012-01-07'),

    -- Single-game postseason rushing yards
    ('postseason_game_rush_yards', 248, 'Eric Dickerson', 'LAR', 1985, '1986-01-04'),
    ('postseason_game_rush_yards', 220, 'Raheem Mostert', 'SF', 2019, '2020-01-19'),
    ('postseason_game_rush_yards', 209, 'Lamar Smith', 'MIA', 2000, '2000-12-30'),
    ('postseason_game_rush_yards', 206, 'Keith Lincoln', 'LAC', 1963, '1964-01-05'),
    ('postseason_game_rush_yards', 205, 'Saquon Barkley', 'PHI', 2024, '2025-01-12'),
    ('postseason_game_rush_yards', 204, 'Timmy Smith', 'WAS', 1987, '1988-01-31'),
    ('postseason_game_rush_yards', 202, 'Lawrence McCutcheon', 'LAR', 1975, '1975-12-27'),
    ('postseason_game_rush_yards', 202, 'Freeman McNeil', 'NYJ', 1982, '1983-01-09'),
    ('postseason_game_rush_yards', 199, 'Terrell Davis', 'DEN', 1998, '1999-01-09'),
    ('postseason_game_rush_yards', 181, 'Colin Kaepernick', 'SF', 2012, '2013-01-12'),

    -- Single-game postseason rushing TDs
    ('postseason_game_rush_tds', 5, 'Ricky Watters', 'SF', 1993, '1994-01-15'),
    ('postseason_game_rush_tds', 4, 'Raheem Mostert', 'SF', 2019, '2020-01-19'),
    ('postseason_game_rush_tds', 4, 'LeGarrette Blount', 'NE', 2013, '2014-01-11'),
    ('postseason_game_rush_tds', 3, 'Jalen Hurts', 'PHI', 2022, '2023-02-12'),
    ('postseason_game_rush_tds', 3, 'Terrell Davis', 'DEN', 1997, '1998-01-25'),
    ('postseason_game_rush_tds', 3, 'Sony Michel', 'NE', 2018, '2019-01-13'),
    ('postseason_game_rush_tds', 3, 'Arian Foster', 'HOU', 2011, '2012-01-07'),
    ('postseason_game_rush_tds', 3, 'Shaun Alexander', 'SEA', 2005, '2006-01-22'),
    ('postseason_game_rush_tds', 3, 'Craig James', 'NE', 1985, '1986-01-12'),
    ('postseason_game_rush_tds', 3, 'Larry Csonka', 'MIA', 1973, '1974-01-13'),

    -- Single-game postseason receiving yards
    ('postseason_game_rec_yards', 240, 'Eric Moulds', 'BUF', 1998, '1999-01-02'),
    ('postseason_game_rec_yards', 227, 'Anthony Carter', 'MIN', 1987, '1988-01-09'),
    ('postseason_game_rec_yards', 224, 'T.Y. Hilton', 'IND', 2013, '2014-01-04'),
    ('postseason_game_rec_yards', 221, 'Reggie Wayne', 'IND', 2004, '2005-01-09'),
    ('postseason_game_rec_yards', 218, 'Steve Smith Sr.', 'CAR', 2005, '2006-01-15'),
    ('postseason_game_rec_yards', 215, 'Jerry Rice', 'SF', 1988, '1989-01-22'),
    ('postseason_game_rec_yards', 211, 'Calvin Johnson', 'DET', 2011, '2012-01-07'),
    ('postseason_game_rec_yards', 204, 'Demaryius Thomas', 'DEN', 2011, '2012-01-08'),
    ('postseason_game_rec_yards', 201, 'Gabe Davis', 'BUF', 2021, '2022-01-23'),
    ('postseason_game_rec_yards', 180, 'Julio Jones', 'ATL', 2016, '2017-01-22'),

    -- Single-game postseason receptions
    ('postseason_game_receptions', 15, 'Darren Sproles', 'LAC', 2008, '2009-01-03'),
    ('postseason_game_receptions', 15, 'James White', 'NE', 2018, '2019-01-13'),
    ('postseason_game_receptions', 14, 'James White', 'NE', 2016, '2017-02-05'),
    ('postseason_game_receptions', 14, 'Travis Kelce', 'KC', 2022, '2023-01-21'),
    ('postseason_game_receptions', 13, 'Demaryius Thomas', 'DEN', 2013, '2014-02-02'),
    ('postseason_game_receptions', 13, 'Shannon Sharpe', 'DEN', 1993, '1994-01-09'),
    ('postseason_game_receptions', 13, 'Travis Kelce', 'KC', 2019, '2020-01-12'),
    ('postseason_game_receptions', 12, 'Keenan Allen', 'LAC', 2020, '2021-01-03'),
    ('postseason_game_receptions', 12, 'Julian Edelman', 'NE', 2018, '2019-01-20'),
    ('postseason_game_receptions', 12, 'Michael Irvin', 'DAL', 1994, '1995-01-15'),

    -- Single-game postseason receiving TDs
    ('postseason_game_rec_tds', 4, 'Gabe Davis', 'BUF', 2021, '2022-01-23'),
    ('postseason_game_rec_tds', 3, 'Jerry Rice', 'SF', 1989, '1990-01-28'),
    ('postseason_game_rec_tds', 3, 'Jerry Rice', 'SF', 1994, '1995-01-29'),
    ('postseason_game_rec_tds', 3, 'Larry Fitzgerald', 'AZ', 2008, '2009-01-18'),
    ('postseason_game_rec_tds', 3, 'Rob Gronkowski', 'NE', 2011, '2012-01-14'),
    ('postseason_game_rec_tds', 3, 'Travis Kelce', 'KC', 2019, '2020-01-12'),
    ('postseason_game_rec_tds', 3, 'Randall Cobb', 'GB', 2016, '2017-01-08'),
    ('postseason_game_rec_tds', 3, 'Jake Ferguson', 'DAL', 2023, '2024-01-14'),
    ('postseason_game_rec_tds', 3, 'Harold Carmichael', 'PHI', 1979, '1979-12-23'),
    ('postseason_game_rec_tds', 3, 'Dave Logan', 'CLE', 1980, '1981-01-04'),

    -- Single-game postseason sacks
    ('postseason_game_sacks', 4.5, 'Willie McGinest', 'NE', 2005, '2006-01-07'),
    ('postseason_game_sacks', 3.0, 'Jeffrey Simmons', 'TEN', 2021, '2022-01-22'),
    ('postseason_game_sacks', 3.0, 'Von Miller', 'DEN', 2015, '2016-01-24'),
    ('postseason_game_sacks', 3.0, 'Reggie White', 'GB', 1996, '1997-01-26'),
    ('postseason_game_sacks', 3.0, 'Darnell Dockett', 'AZ', 2008, '2009-02-01'),
    ('postseason_game_sacks', 3.0, 'Kony Ealy', 'CAR', 2015, '2016-02-07'),
    ('postseason_game_sacks', 3.0, 'Grady Jarrett', 'ATL', 2016, '2017-02-05'),
    ('postseason_game_sacks', 3.0, 'Richard Dent', 'CHI', 1984, '1984-12-30'),
    ('postseason_game_sacks', 3.0, 'Clay Matthews', 'GB', 2011, '2012-01-15'),
    ('postseason_game_sacks', 3.0, 'Terrell Suggs', 'BAL', 2010, '2011-01-09'),

    -- Single-game postseason defensive INTs
    ('postseason_game_def_ints', 4, 'Vernon Perry', 'TEN', 1979, '1979-12-29'),
    ('postseason_game_def_ints', 3, 'Ty Law', 'NE', 2003, '2004-01-18'),
    ('postseason_game_def_ints', 3, 'Rodney Harrison', 'NE', 2004, '2005-01-16'),
    ('postseason_game_def_ints', 3, 'Craig Dahl', 'NYG', 2007, '2008-01-20'),
    ('postseason_game_def_ints', 3, 'A.J. Duhe', 'MIA', 1982, '1983-01-23'),
    ('postseason_game_def_ints', 3, 'Johnny Robinson', 'KC', 1969, '1969-12-20'),
    ('postseason_game_def_ints', 3, 'Charlie Waters', 'DAL', 1977, '1978-01-01'),
    ('postseason_game_def_ints', 3, 'Dennis Thurman', 'DAL', 1982, '1983-01-09'),
    ('postseason_game_def_ints', 3, 'Barry Wilburn', 'WAS', 1987, '1988-01-10'),
    ('postseason_game_def_ints', 2, 'Richard Sherman', 'SEA', 2013, '2014-01-19'),

    -- Single-season postseason run: passing yards
    ('postseason_run_pass_yards', 1219, 'Eli Manning', 'NYG', 2011, NULL),
    ('postseason_run_pass_yards', 1188, 'Matthew Stafford', 'LAR', 2021, NULL),
    ('postseason_run_pass_yards', 1147, 'Kurt Warner', 'AZ', 2008, NULL),
    ('postseason_run_pass_yards', 1140, 'Joe Flacco', 'BAL', 2012, NULL),
    ('postseason_run_pass_yards', 1137, 'Tom Brady', 'NE', 2016, NULL),
    ('postseason_run_pass_yards', 1132, 'Tom Brady', 'NE', 2017, NULL),
    ('postseason_run_pass_yards', 1057, 'Patrick Mahomes', 'KC', 2021, NULL),
    ('postseason_run_pass_yards', 1034, 'Peyton Manning', 'IND', 2006, NULL),
    ('postseason_run_pass_yards', 1022, 'Tom Brady', 'NE', 2003, NULL),
    ('postseason_run_pass_yards', 1003, 'Joe Montana', 'SF', 1988, NULL),

    -- Single-season postseason run: passing TDs
    ('postseason_run_pass_tds', 11, 'Joe Montana', 'SF', 1989, NULL),
    ('postseason_run_pass_tds', 11, 'Kurt Warner', 'AZ', 2008, NULL),
    ('postseason_run_pass_tds', 11, 'Joe Flacco', 'BAL', 2012, NULL),
    ('postseason_run_pass_tds', 11, 'Patrick Mahomes', 'KC', 2021, NULL),
    ('postseason_run_pass_tds', 10, 'Tom Brady', 'TB', 2020, NULL),
    ('postseason_run_pass_tds', 10, 'Patrick Mahomes', 'KC', 2019, NULL),
    ('postseason_run_pass_tds', 9, 'Matthew Stafford', 'LAR', 2021, NULL),
    ('postseason_run_pass_tds', 9, 'Eli Manning', 'NYG', 2011, NULL),
    ('postseason_run_pass_tds', 9, 'Josh Allen', 'BUF', 2021, NULL),
    ('postseason_run_pass_tds', 9, 'Aaron Rodgers', 'GB', 2010, NULL),

    -- Single-season postseason run: rushing yards
    ('postseason_run_rush_yards', 610, 'John Riggins', 'WAS', 1982, NULL),
    ('postseason_run_rush_yards', 581, 'Terrell Davis', 'DEN', 1997, NULL),
    ('postseason_run_rush_yards', 499, 'Saquon Barkley', 'PHI', 2024, NULL),
    ('postseason_run_rush_yards', 468, 'Terrell Davis', 'DEN', 1998, NULL),
    ('postseason_run_rush_yards', 466, 'Marcus Allen', 'LV', 1983, NULL),
    ('postseason_run_rush_yards', 449, 'Eddie George', 'TEN', 1999, NULL),
    ('postseason_run_rush_yards', 427, 'Franco Harris', 'PIT', 1974, NULL),
    ('postseason_run_rush_yards', 396, 'Thurman Thomas', 'BUF', 1990, NULL),
    ('postseason_run_rush_yards', 339, 'Corey Dillon', 'NE', 2004, NULL),
    ('postseason_run_rush_yards', 318, 'Marshawn Lynch', 'SEA', 2014, NULL),

    -- Single-season postseason run: rushing TDs
    ('postseason_run_rush_tds', 8, 'Terrell Davis', 'DEN', 1997, NULL),
    ('postseason_run_rush_tds', 6, 'John Riggins', 'WAS', 1983, NULL),
    ('postseason_run_rush_tds', 6, 'Sony Michel', 'NE', 2018, NULL),
    ('postseason_run_rush_tds', 6, 'Larry Csonka', 'MIA', 1973, NULL),
    ('postseason_run_rush_tds', 5, 'Raheem Mostert', 'SF', 2019, NULL),
    ('postseason_run_rush_tds', 5, 'Ricky Watters', 'SF', 1993, NULL),
    ('postseason_run_rush_tds', 5, 'Emmitt Smith', 'DAL', 1995, NULL),
    ('postseason_run_rush_tds', 5, 'Jalen Hurts', 'PHI', 2022, NULL),
    ('postseason_run_rush_tds', 4, 'LeGarrette Blount', 'NE', 2013, NULL),
    ('postseason_run_rush_tds', 4, 'Franco Harris', 'PIT', 1975, NULL),

    -- Single-season postseason run: receiving yards
    ('postseason_run_rec_yards', 546, 'Larry Fitzgerald', 'AZ', 2008, NULL),
    ('postseason_run_rec_yards', 478, 'Cooper Kupp', 'LAR', 2021, NULL),
    ('postseason_run_rec_yards', 444, 'Hakeem Nicks', 'NYG', 2011, NULL),
    ('postseason_run_rec_yards', 409, 'Jerry Rice', 'SF', 1988, NULL),
    ('postseason_run_rec_yards', 404, 'Steve Smith Sr.', 'CAR', 2003, NULL),
    ('postseason_run_rec_yards', 398, 'Travis Kelce', 'KC', 2023, NULL),
    ('postseason_run_rec_yards', 388, 'Julian Edelman', 'NE', 2018, NULL),
    ('postseason_run_rec_yards', 371, 'Charlie Brown', 'WAS', 1982, NULL),
    ('postseason_run_rec_yards', 360, 'Travis Kelce', 'KC', 2020, NULL),
    ('postseason_run_rec_yards', 353, 'Randy Moss', 'MIN', 2000, NULL),

    -- Single-season postseason run: receptions
    ('postseason_run_receptions', 33, 'Cooper Kupp', 'LAR', 2021, NULL),
    ('postseason_run_receptions', 32, 'Travis Kelce', 'KC', 2023, NULL),
    ('postseason_run_receptions', 31, 'Travis Kelce', 'KC', 2020, NULL),
    ('postseason_run_receptions', 30, 'Larry Fitzgerald', 'AZ', 2008, NULL),
    ('postseason_run_receptions', 28, 'Hakeem Nicks', 'NYG', 2011, NULL),
    ('postseason_run_receptions', 27, 'Wes Welker', 'NE', 2007, NULL),
    ('postseason_run_receptions', 27, 'Jerry Rice', 'SF', 1988, NULL),
    ('postseason_run_receptions', 27, 'Steve Smith Sr.', 'CAR', 2003, NULL),
    ('postseason_run_receptions', 26, 'Julian Edelman', 'NE', 2018, NULL),
    ('postseason_run_receptions', 26, 'Julian Edelman', 'NE', 2014, NULL),

    -- Single-season postseason run: receiving TDs
    ('postseason_run_rec_tds', 7, 'Larry Fitzgerald', 'AZ', 2008, NULL),
    ('postseason_run_rec_tds', 6, 'Cooper Kupp', 'LAR', 2021, NULL),
    ('postseason_run_rec_tds', 6, 'Jerry Rice', 'SF', 1988, NULL),
    ('postseason_run_rec_tds', 5, 'Dave Casper', 'LV', 1977, NULL),
    ('postseason_run_rec_tds', 5, 'Gabe Davis', 'BUF', 2021, NULL),
    ('postseason_run_rec_tds', 5, 'Rob Gronkowski', 'NE', 2011, NULL),
    ('postseason_run_rec_tds', 4, 'Travis Kelce', 'KC', 2022, NULL),
    ('postseason_run_rec_tds', 4, 'Hakeem Nicks', 'NYG', 2011, NULL),
    ('postseason_run_rec_tds', 4, 'John Stallworth', 'PIT', 1978, NULL),
    ('postseason_run_rec_tds', 3, 'Lynn Swann', 'PIT', 1975, NULL),

    -- Single-season postseason run: sacks
    ('postseason_run_sacks', 6.0, 'Richard Dent', 'CHI', 1985, NULL),
    ('postseason_run_sacks', 6.0, 'LaMarr Woodley', 'PIT', 2008, NULL),
    ('postseason_run_sacks', 5.0, 'Von Miller', 'DEN', 2015, NULL),
    ('postseason_run_sacks', 5.0, 'Terrell Suggs', 'BAL', 2010, NULL),
    ('postseason_run_sacks', 5.0, 'Frank Clark', 'KC', 2019, NULL),
    ('postseason_run_sacks', 4.5, 'Willie McGinest', 'NE', 2005, NULL),
    ('postseason_run_sacks', 4.5, 'Reggie White', 'GB', 1996, NULL),
    ('postseason_run_sacks', 4.5, 'Trey Hendrickson', 'CIN', 2021, NULL),
    ('postseason_run_sacks', 4.5, 'Michael Strahan', 'NYG', 2007, NULL),
    ('postseason_run_sacks', 4.5, 'Dwight Freeney', 'IND', 2006, NULL),

    -- Super Bowl single-game passing yards
    ('super_bowl_pass_yards', 505, 'Tom Brady', 'NE', 2017, '2018-02-04'),
    ('super_bowl_pass_yards', 466, 'Tom Brady', 'NE', 2016, '2017-02-05'),
    ('super_bowl_pass_yards', 414, 'Kurt Warner', 'LAR', 1999, '2000-01-30'),
    ('super_bowl_pass_yards', 377, 'Kurt Warner', 'AZ', 2008, '2009-02-01'),
    ('super_bowl_pass_yards', 373, 'Nick Foles', 'PHI', 2017, '2018-02-04'),
    ('super_bowl_pass_yards', 365, 'Kurt Warner', 'LAR', 2001, '2002-02-03'),
    ('super_bowl_pass_yards', 357, 'Donovan McNabb', 'PHI', 2004, '2005-02-06'),
    ('super_bowl_pass_yards', 357, 'Joe Montana', 'SF', 1988, '1989-01-22'),
    ('super_bowl_pass_yards', 354, 'Tom Brady', 'NE', 2003, '2004-02-01'),
    ('super_bowl_pass_yards', 340, 'Doug Williams', 'WAS', 1987, '1988-01-31'),

    -- Super Bowl single-game passing TDs
    ('super_bowl_pass_tds', 6, 'Steve Young', 'SF', 1994, '1995-01-29'),
    ('super_bowl_pass_tds', 5, 'Joe Montana', 'SF', 1989, '1990-01-28'),
    ('super_bowl_pass_tds', 4, 'Terry Bradshaw', 'PIT', 1978, '1979-01-21'),
    ('super_bowl_pass_tds', 4, 'Doug Williams', 'WAS', 1987, '1988-01-31'),
    ('super_bowl_pass_tds', 4, 'Troy Aikman', 'DAL', 1992, '1993-01-31'),
    ('super_bowl_pass_tds', 4, 'Tom Brady', 'NE', 2014, '2015-02-01'),
    ('super_bowl_pass_tds', 3, 'Jake Delhomme', 'CAR', 2003, '2004-02-01'),
    ('super_bowl_pass_tds', 3, 'Brett Favre', 'GB', 1997, '1998-01-25'),
    ('super_bowl_pass_tds', 3, 'Kurt Warner', 'AZ', 2008, '2009-02-01'),
    ('super_bowl_pass_tds', 3, 'Nick Foles', 'PHI', 2017, '2018-02-04'),

    -- Super Bowl single-game rushing yards
    ('super_bowl_rush_yards', 204, 'Timmy Smith', 'WAS', 1987, '1988-01-31'),
    ('super_bowl_rush_yards', 191, 'Marcus Allen', 'LV', 1983, '1984-01-22'),
    ('super_bowl_rush_yards', 166, 'John Riggins', 'WAS', 1982, '1983-01-30'),
    ('super_bowl_rush_yards', 158, 'Franco Harris', 'PIT', 1974, '1975-01-12'),
    ('super_bowl_rush_yards', 157, 'Terrell Davis', 'DEN', 1997, '1998-01-25'),
    ('super_bowl_rush_yards', 145, 'Larry Csonka', 'MIA', 1973, '1974-01-13'),
    ('super_bowl_rush_yards', 137, 'Clarence Davis', 'LV', 1976, '1977-01-09'),
    ('super_bowl_rush_yards', 132, 'Emmitt Smith', 'DAL', 1993, '1994-01-30'),
    ('super_bowl_rush_yards', 124, 'Michael Pittman', 'TB', 2002, '2003-01-26'),
    ('super_bowl_rush_yards', 112, 'Thomas Jones', 'CHI', 2006, '2007-02-04'),

    -- Super Bowl single-game rushing TDs
    ('super_bowl_rush_tds', 3, 'Terrell Davis', 'DEN', 1997, '1998-01-25'),
    ('super_bowl_rush_tds', 3, 'Jalen Hurts', 'PHI', 2022, '2023-02-12'),
    ('super_bowl_rush_tds', 2, 'Larry Csonka', 'MIA', 1973, '1974-01-13'),
    ('super_bowl_rush_tds', 2, 'Marcus Allen', 'LV', 1983, '1984-01-22'),
    ('super_bowl_rush_tds', 2, 'Timmy Smith', 'WAS', 1987, '1988-01-31'),
    ('super_bowl_rush_tds', 2, 'Emmitt Smith', 'DAL', 1993, '1994-01-30'),
    ('super_bowl_rush_tds', 2, 'Emmitt Smith', 'DAL', 1995, '1996-01-28'),
    ('super_bowl_rush_tds', 2, 'Franco Harris', 'PIT', 1978, '1979-01-21'),
    ('super_bowl_rush_tds', 2, 'Eddie George', 'TEN', 1999, '2000-01-30'),
    ('super_bowl_rush_tds', 2, 'James White', 'NE', 2016, '2017-02-05'),

    -- Super Bowl single-game receiving yards
    ('super_bowl_rec_yards', 215, 'Jerry Rice', 'SF', 1988, '1989-01-22'),
    ('super_bowl_rec_yards', 193, 'Ricky Sanders', 'WAS', 1987, '1988-01-31'),
    ('super_bowl_rec_yards', 162, 'Isaac Bruce', 'LAR', 1999, '2000-01-30'),
    ('super_bowl_rec_yards', 161, 'Lynn Swann', 'PIT', 1975, '1976-01-18'),
    ('super_bowl_rec_yards', 152, 'Danny Amendola', 'NE', 2017, '2018-02-04'),
    ('super_bowl_rec_yards', 152, 'Rod Smith', 'DEN', 1998, '1999-01-31'),
    ('super_bowl_rec_yards', 149, 'Jerry Rice', 'SF', 1994, '1995-01-29'),
    ('super_bowl_rec_yards', 148, 'Jerry Rice', 'SF', 1989, '1990-01-28'),
    ('super_bowl_rec_yards', 143, 'Deion Branch', 'NE', 2003, '2004-02-01'),
    ('super_bowl_rec_yards', 140, 'Muhsin Muhammad', 'CAR', 2003, '2004-02-01'),

    -- Super Bowl single-game receptions
    ('super_bowl_receptions', 14, 'James White', 'NE', 2016, '2017-02-05'),
    ('super_bowl_receptions', 13, 'Demaryius Thomas', 'DEN', 2013, '2014-02-02'),
    ('super_bowl_receptions', 11, 'Dan Ross', 'CIN', 1981, '1982-01-24'),
    ('super_bowl_receptions', 11, 'Jerry Rice', 'SF', 1988, '1989-01-22'),
    ('super_bowl_receptions', 11, 'Deion Branch', 'NE', 2004, '2005-02-06'),
    ('super_bowl_receptions', 11, 'Wes Welker', 'NE', 2007, '2008-02-03'),
    ('super_bowl_receptions', 11, 'Shane Vereen', 'NE', 2014, '2015-02-01'),
    ('super_bowl_receptions', 10, 'Cooper Kupp', 'LAR', 2021, '2022-02-13'),
    ('super_bowl_receptions', 10, 'Jerry Rice', 'SF', 1994, '1995-01-29'),
    ('super_bowl_receptions', 10, 'Travis Kelce', 'KC', 2019, '2020-02-02'),

    -- Super Bowl single-game receiving TDs
    ('super_bowl_rec_tds', 3, 'Jerry Rice', 'SF', 1989, '1990-01-28'),
    ('super_bowl_rec_tds', 3, 'Jerry Rice', 'SF', 1994, '1995-01-29'),
    ('super_bowl_rec_tds', 2, 'Max McGee', 'GB', 1966, '1967-01-15'),
    ('super_bowl_rec_tds', 2, 'Bill Miller', 'LV', 1967, '1968-01-14'),
    ('super_bowl_rec_tds', 2, 'John Stallworth', 'PIT', 1978, '1979-01-21'),
    ('super_bowl_rec_tds', 2, 'Cliff Branch', 'LV', 1980, '1981-01-25'),
    ('super_bowl_rec_tds', 2, 'Dan Ross', 'CIN', 1981, '1982-01-24'),
    ('super_bowl_rec_tds', 2, 'Roger Craig', 'SF', 1984, '1985-01-20'),
    ('super_bowl_rec_tds', 2, 'Ricky Sanders', 'WAS', 1987, '1988-01-31'),
    ('super_bowl_rec_tds', 2, 'Rob Gronkowski', 'TB', 2020, '2021-02-07'),

    -- Super Bowl single-game sacks
    ('super_bowl_sacks', 4.0, 'L.C. Greenwood', 'PIT', 1975, '1976-01-18'),
    ('super_bowl_sacks', 3.0, 'Reggie White', 'GB', 1996, '1997-01-26'),
    ('super_bowl_sacks', 3.0, 'Darnell Dockett', 'AZ', 2008, '2009-02-01'),
    ('super_bowl_sacks', 3.0, 'Kony Ealy', 'CAR', 2015, '2016-02-07'),
    ('super_bowl_sacks', 3.0, 'Grady Jarrett', 'ATL', 2016, '2017-02-05'),
    ('super_bowl_sacks', 2.5, 'Willie McGinest', 'NE', 2004, '2005-02-06'),
    ('super_bowl_sacks', 2.5, 'Von Miller', 'DEN', 2015, '2016-02-07'),
    ('super_bowl_sacks', 2.0, 'Richard Dent', 'CHI', 1985, '1986-01-26'),
    ('super_bowl_sacks', 2.0, 'Justin Tuck', 'NYG', 2007, '2008-02-03'),
    ('super_bowl_sacks', 2.0, 'Justin Tuck', 'NYG', 2011, '2012-02-05'),

    -- Career Super Bowl wins (Player)
    ('career_super_bowl_wins_player', 7, 'Tom Brady', 'NE', 2020, NULL),
    ('career_super_bowl_wins_player', 5, 'Charles Haley', 'SF', 1995, NULL),
    ('career_super_bowl_wins_player', 4, 'Marv Fleming', 'GB', 1973, NULL),
    ('career_super_bowl_wins_player', 4, 'Joe Montana', 'SF', 1989, NULL),
    ('career_super_bowl_wins_player', 4, 'Terry Bradshaw', 'PIT', 1979, NULL),
    ('career_super_bowl_wins_player', 4, 'Franco Harris', 'PIT', 1979, NULL),
    ('career_super_bowl_wins_player', 4, 'Lynn Swann', 'PIT', 1979, NULL),
    ('career_super_bowl_wins_player', 4, 'John Stallworth', 'PIT', 1979, NULL),
    ('career_super_bowl_wins_player', 4, 'Mel Blount', 'PIT', 1979, NULL),
    ('career_super_bowl_wins_player', 4, 'Ronnie Lott', 'SF', 1989, NULL),

    -- Career Super Bowl wins (Head Coach)
    ('career_super_bowl_wins_coach', 6, 'Bill Belichick', 'NE', 2018, NULL),
    ('career_super_bowl_wins_coach', 4, 'Chuck Noll', 'PIT', 1979, NULL),
    ('career_super_bowl_wins_coach', 3, 'Andy Reid', 'KC', 2023, NULL),
    ('career_super_bowl_wins_coach', 3, 'Bill Walsh', 'SF', 1988, NULL),
    ('career_super_bowl_wins_coach', 3, 'Joe Gibbs', 'WAS', 1991, NULL),
    ('career_super_bowl_wins_coach', 2, 'Vince Lombardi', 'GB', 1967, NULL),
    ('career_super_bowl_wins_coach', 2, 'Don Shula', 'MIA', 1973, NULL),
    ('career_super_bowl_wins_coach', 2, 'Tom Landry', 'DAL', 1977, NULL),
    ('career_super_bowl_wins_coach', 2, 'Jimmy Johnson', 'DAL', 1993, NULL),
    ('career_super_bowl_wins_coach', 2, 'Tom Coughlin', 'NYG', 2011, NULL),

    -- Career Super Bowl wins (Franchise)
    ('career_super_bowl_wins_franchise', 6, 'New England Patriots', 'NE', 2018, NULL),
    ('career_super_bowl_wins_franchise', 6, 'Pittsburgh Steelers', 'PIT', 2008, NULL),
    ('career_super_bowl_wins_franchise', 5, 'San Francisco 49ers', 'SF', 1994, NULL),
    ('career_super_bowl_wins_franchise', 5, 'Dallas Cowboys', 'DAL', 1995, NULL),
    ('career_super_bowl_wins_franchise', 4, 'Kansas City Chiefs', 'KC', 2023, NULL),
    ('career_super_bowl_wins_franchise', 4, 'Green Bay Packers', 'GB', 2010, NULL),
    ('career_super_bowl_wins_franchise', 4, 'New York Giants', 'NYG', 2011, NULL),
    ('career_super_bowl_wins_franchise', 3, 'Denver Broncos', 'DEN', 2015, NULL),
    ('career_super_bowl_wins_franchise', 3, 'Las Vegas Raiders', 'LV', 1983, NULL),
    ('career_super_bowl_wins_franchise', 3, 'Washington Commanders', 'WAS', 1991, NULL),

    -- Career postseason passing yards
    ('career_postseason_pass_yards', 13049, 'Tom Brady', 'NE', 2022, NULL),
    ('career_postseason_pass_yards', 7339, 'Peyton Manning', 'IND', 2015, NULL),
    ('career_postseason_pass_yards', 5972, 'Ben Roethlisberger', 'PIT', 2021, NULL),
    ('career_postseason_pass_yards', 5894, 'Aaron Rodgers', 'GB', 2021, NULL),
    ('career_postseason_pass_yards', 5855, 'Brett Favre', 'GB', 2009, NULL),
    ('career_postseason_pass_yards', 5772, 'Joe Montana', 'SF', 1994, NULL),
    ('career_postseason_pass_yards', 5366, 'Drew Brees', 'NO', 2020, NULL),
    ('career_postseason_pass_yards', 5135, 'Patrick Mahomes', 'KC', 2024, NULL),
    ('career_postseason_pass_yards', 4964, 'John Elway', 'DEN', 1998, NULL),
    ('career_postseason_pass_yards', 4510, 'Dan Marino', 'MIA', 1999, NULL),

    -- Career postseason passing TDs
    ('career_postseason_pass_tds', 88, 'Tom Brady', 'NE', 2022, NULL),
    ('career_postseason_pass_tds', 45, 'Joe Montana', 'SF', 1994, NULL),
    ('career_postseason_pass_tds', 45, 'Aaron Rodgers', 'GB', 2021, NULL),
    ('career_postseason_pass_tds', 44, 'Brett Favre', 'GB', 2009, NULL),
    ('career_postseason_pass_tds', 41, 'Patrick Mahomes', 'KC', 2024, NULL),
    ('career_postseason_pass_tds', 40, 'Peyton Manning', 'IND', 2015, NULL),
    ('career_postseason_pass_tds', 37, 'Drew Brees', 'NO', 2020, NULL),
    ('career_postseason_pass_tds', 36, 'Ben Roethlisberger', 'PIT', 2021, NULL),
    ('career_postseason_pass_tds', 32, 'Dan Marino', 'MIA', 1999, NULL),
    ('career_postseason_pass_tds', 31, 'Kurt Warner', 'AZ', 2009, NULL),

    -- Career postseason rushing yards
    ('career_postseason_rush_yards', 1586, 'Emmitt Smith', 'DAL', 1999, NULL),
    ('career_postseason_rush_yards', 1556, 'Franco Harris', 'PIT', 1983, NULL),
    ('career_postseason_rush_yards', 1448, 'Thurman Thomas', 'BUF', 1998, NULL),
    ('career_postseason_rush_yards', 1383, 'Tony Dorsett', 'DAL', 1985, NULL),
    ('career_postseason_rush_yards', 1347, 'Marcus Allen', 'LV', 1997, NULL),
    ('career_postseason_rush_yards', 1224, 'John Riggins', 'WAS', 1984, NULL),
    ('career_postseason_rush_yards', 1140, 'Terrell Davis', 'DEN', 1998, NULL),
    ('career_postseason_rush_yards', 970, 'Marshawn Lynch', 'SEA', 2019, NULL),
    ('career_postseason_rush_yards', 841, 'Roger Craig', 'SF', 1993, NULL),
    ('career_postseason_rush_yards', 786, 'Chuck Muncie', 'LAC', 1982, NULL),

    -- Career postseason rushing TDs
    ('career_postseason_rush_tds', 19, 'Emmitt Smith', 'DAL', 1999, NULL),
    ('career_postseason_rush_tds', 16, 'Thurman Thomas', 'BUF', 1998, NULL),
    ('career_postseason_rush_tds', 16, 'Franco Harris', 'PIT', 1983, NULL),
    ('career_postseason_rush_tds', 12, 'Terrell Davis', 'DEN', 1998, NULL),
    ('career_postseason_rush_tds', 12, 'John Riggins', 'WAS', 1984, NULL),
    ('career_postseason_rush_tds', 12, 'Marshawn Lynch', 'SEA', 2019, NULL),
    ('career_postseason_rush_tds', 11, 'Marcus Allen', 'LV', 1997, NULL),
    ('career_postseason_rush_tds', 9, 'Roger Craig', 'SF', 1993, NULL),
    ('career_postseason_rush_tds', 9, 'Jerome Bettis', 'PIT', 2005, NULL),
    ('career_postseason_rush_tds', 8, 'Shaun Alexander', 'SEA', 2007, NULL),

    -- Career postseason receiving yards
    ('career_postseason_rec_yards', 2245, 'Jerry Rice', 'SF', 2002, NULL),
    ('career_postseason_rec_yards', 1903, 'Travis Kelce', 'KC', 2024, NULL),
    ('career_postseason_rec_yards', 1442, 'Julian Edelman', 'NE', 2018, NULL),
    ('career_postseason_rec_yards', 1315, 'Michael Irvin', 'DAL', 1996, NULL),
    ('career_postseason_rec_yards', 1289, 'Cliff Branch', 'LV', 1983, NULL),
    ('career_postseason_rec_yards', 1254, 'Reggie Wayne', 'IND', 2014, NULL),
    ('career_postseason_rec_yards', 1229, 'Andre Reed', 'BUF', 1999, NULL),
    ('career_postseason_rec_yards', 1181, 'Hines Ward', 'PIT', 2011, NULL),
    ('career_postseason_rec_yards', 1167, 'Fred Biletnikoff', 'LV', 1977, NULL),
    ('career_postseason_rec_yards', 1131, 'Drew Pearson', 'DAL', 1982, NULL),

    -- Career postseason receptions
    ('career_postseason_receptions', 165, 'Travis Kelce', 'KC', 2024, NULL),
    ('career_postseason_receptions', 151, 'Jerry Rice', 'SF', 2002, NULL),
    ('career_postseason_receptions', 118, 'Julian Edelman', 'NE', 2018, NULL),
    ('career_postseason_receptions', 98, 'Rob Gronkowski', 'TB', 2021, NULL),
    ('career_postseason_receptions', 96, 'Tyreek Hill', 'MIA', 2023, NULL),
    ('career_postseason_receptions', 93, 'Reggie Wayne', 'IND', 2014, NULL),
    ('career_postseason_receptions', 88, 'Hines Ward', 'PIT', 2011, NULL),
    ('career_postseason_receptions', 88, 'Wes Welker', 'DEN', 2014, NULL),
    ('career_postseason_receptions', 87, 'Michael Irvin', 'DAL', 1996, NULL),
    ('career_postseason_receptions', 85, 'Andre Reed', 'BUF', 1999, NULL),

    -- Career postseason receiving TDs
    ('career_postseason_rec_tds', 22, 'Jerry Rice', 'SF', 2002, NULL),
    ('career_postseason_rec_tds', 19, 'Travis Kelce', 'KC', 2024, NULL),
    ('career_postseason_rec_tds', 15, 'Rob Gronkowski', 'TB', 2021, NULL),
    ('career_postseason_rec_tds', 12, 'John Stallworth', 'PIT', 1984, NULL),
    ('career_postseason_rec_tds', 10, 'Fred Biletnikoff', 'LV', 1977, NULL),
    ('career_postseason_rec_tds', 10, 'Larry Fitzgerald', 'AZ', 2015, NULL),
    ('career_postseason_rec_tds', 10, 'Antonio Freeman', 'GB', 2001, NULL),
    ('career_postseason_rec_tds', 10, 'Randy Moss', 'SF', 2012, NULL),
    ('career_postseason_rec_tds', 10, 'Hines Ward', 'PIT', 2011, NULL),
    ('career_postseason_rec_tds', 9, 'Lynn Swann', 'PIT', 1979, NULL),

    -- Career postseason sacks
    ('career_postseason_sacks', 16.0, 'Willie McGinest', 'NE', 2005, NULL),
    ('career_postseason_sacks', 14.5, 'Bruce Smith', 'BUF', 1998, NULL),
    ('career_postseason_sacks', 13.5, 'Frank Clark', 'KC', 2022, NULL),
    ('career_postseason_sacks', 12.5, 'Terrell Suggs', 'BAL', 2019, NULL),
    ('career_postseason_sacks', 12.0, 'Reggie White', 'GB', 1998, NULL),
    ('career_postseason_sacks', 11.0, 'James Harrison', 'PIT', 2017, NULL),
    ('career_postseason_sacks', 11.0, 'Charles Haley', 'DAL', 1996, NULL),
    ('career_postseason_sacks', 11.0, 'Clay Matthews', 'GB', 2016, NULL),
    ('career_postseason_sacks', 10.5, 'Von Miller', 'BUF', 2024, NULL),
    ('career_postseason_sacks', 10.5, 'Richard Dent', 'CHI', 1995, NULL),

    -- Career postseason interceptions
    ('career_postseason_def_ints', 9, 'Ronnie Lott', 'SF', 1993, NULL),
    ('career_postseason_def_ints', 9, 'Bill Simpson', 'LAR', 1979, NULL),
    ('career_postseason_def_ints', 9, 'Ed Reed', 'BAL', 2012, NULL),
    ('career_postseason_def_ints', 9, 'Charlie Waters', 'DAL', 1981, NULL),
    ('career_postseason_def_ints', 8, 'Lester Hayes', 'LV', 1985, NULL),
    ('career_postseason_def_ints', 7, 'Rodney Harrison', 'NE', 2007, NULL),
    ('career_postseason_def_ints', 7, 'Dennis Thurman', 'DAL', 1985, NULL),
    ('career_postseason_def_ints', 6, 'Aeneas Williams', 'LAR', 2004, NULL),
    ('career_postseason_def_ints', 6, 'Ty Law', 'NE', 2006, NULL),
    ('career_postseason_def_ints', 6, 'Mel Blount', 'PIT', 1983, NULL),

    -- Career postseason QB wins
    ('career_postseason_wins_qb', 35, 'Tom Brady', 'NE', 2022, NULL),
    ('career_postseason_wins_qb', 16, 'Joe Montana', 'SF', 1994, NULL),
    ('career_postseason_wins_qb', 15, 'Patrick Mahomes', 'KC', 2024, NULL),
    ('career_postseason_wins_qb', 14, 'Terry Bradshaw', 'PIT', 1982, NULL),
    ('career_postseason_wins_qb', 14, 'John Elway', 'DEN', 1998, NULL),
    ('career_postseason_wins_qb', 14, 'Peyton Manning', 'DEN', 2015, NULL),
    ('career_postseason_wins_qb', 13, 'Ben Roethlisberger', 'PIT', 2021, NULL),
    ('career_postseason_wins_qb', 13, 'Brett Favre', 'GB', 2009, NULL),
    ('career_postseason_wins_qb', 12, 'Aaron Rodgers', 'GB', 2021, NULL),
    ('career_postseason_wins_qb', 11, 'Troy Aikman', 'DAL', 1999, NULL),

    -- Career postseason head coach wins
    ('career_postseason_wins_coach', 31, 'Bill Belichick', 'NE', 2021, NULL),
    ('career_postseason_wins_coach', 26, 'Andy Reid', 'KC', 2024, NULL),
    ('career_postseason_wins_coach', 20, 'Tom Landry', 'DAL', 1985, NULL),
    ('career_postseason_wins_coach', 19, 'Don Shula', 'MIA', 1995, NULL),
    ('career_postseason_wins_coach', 17, 'Joe Gibbs', 'WAS', 2007, NULL),
    ('career_postseason_wins_coach', 16, 'Chuck Noll', 'PIT', 1989, NULL),
    ('career_postseason_wins_coach', 13, 'Mike Holmgren', 'SEA', 2007, NULL),
    ('career_postseason_wins_coach', 12, 'John Harbaugh', 'BAL', 2024, NULL),
    ('career_postseason_wins_coach', 12, 'Bill Cowher', 'PIT', 2005, NULL),
    ('career_postseason_wins_coach', 11, 'Marv Levy', 'BUF', 1997, NULL)
) AS v (category, value, holder, abbr, season, game_date)
LEFT JOIN teams t ON t.abbreviation = v.abbr
ON CONFLICT (category, holder, season, COALESCE(game_date, DATE '1900-01-01'), COALESCE(game_id, 0))
DO UPDATE SET value = EXCLUDED.value, team_id = EXCLUDED.team_id, source = EXCLUDED.source;

COMMIT;
