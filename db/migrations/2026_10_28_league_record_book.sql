-- The league's own record book, 2026–2035 (JR, Oct 14, 2026; PROJECT_SPEC §2.9).
-- The records set in this league between 2026 and 2035, from the game's record book (franchise, NFL-wide, single
-- season, single game, rookie season and rookie game), stored with source 'BOOK' on top of the real NFL history
-- through 2025 (2026_10_26_nfl_records.sql). Names are matched to full names where the roster allows it; the rest keep
-- the record book's short form. Defensive and rookie categories, and single-game rushing and receiving TDs, are new:
-- they only hold what the record book lists, so `coverage` = 'BOOK' marks them (no real NFL history, no complete
-- top 10). The engine treats BOOK rows as the authority for their season.
--
-- Run ./db/backup_db.sh first, after 2026_10_26_nfl_records.sql. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_10_28_league_record_book.sql

BEGIN;

ALTER TABLE record_categories ADD COLUMN IF NOT EXISTS coverage VARCHAR(4) NOT NULL DEFAULT 'FULL'
    CHECK (coverage IN ('FULL', 'BOOK'));
COMMENT ON COLUMN record_categories.coverage IS
    'FULL: every franchise''s season record and the NFL top 10 through 2025 are seeded. BOOK: only what the league''s record book lists (2026 on).';

ALTER TABLE franchise_records DROP CONSTRAINT IF EXISTS franchise_records_source_check;
ALTER TABLE franchise_records ADD CONSTRAINT franchise_records_source_check CHECK (source IN ('NFL', 'BOOK', 'LEAGUE'));
ALTER TABLE league_record_entries DROP CONSTRAINT IF EXISTS league_record_entries_source_check;
ALTER TABLE league_record_entries ADD CONSTRAINT league_record_entries_source_check CHECK (source IN ('NFL', 'BOOK', 'LEAGUE'));

-- League-wide records: the best of each category's complete franchise records (season categories with full coverage)
-- and the top of its all-time list. A category with neither (a BOOK category the record book has no NFL-wide entry
-- for) has no league-wide record rather than a partial one.
DROP VIEW IF EXISTS league_records;
CREATE VIEW league_records AS
WITH cands AS (
    SELECT fr.category, fr.value, fr.holder, fr.team_id, fr.player_id, fr.season, NULL::date AS game_date, NULL::int AS game_id, fr.source
    FROM franchise_records_current fr JOIN record_categories c ON c.category = fr.category
    WHERE c.scope = 'SEASON' AND c.coverage = 'FULL'
    UNION
    SELECT t.category, t.value, t.holder, t.team_id, t.player_id, t.season, t.game_date, t.game_id, t.source
    FROM league_top10 t WHERE t.rank = 1
), best AS (
    SELECT c.category, CASE WHEN rc.better = 'LOW' THEN MIN(c.value) ELSE MAX(c.value) END AS value
    FROM cands c JOIN record_categories rc ON rc.category = c.category GROUP BY c.category, rc.better
)
SELECT DISTINCT ON (c.category, c.holder, c.season)
       c.category, rc.label, rc.scope, rc.subject, c.value, c.holder, c.team_id, c.player_id, c.season,
       c.game_date, c.game_id, c.source
FROM cands c
JOIN best b ON b.category = c.category AND b.value = c.value
JOIN record_categories rc ON rc.category = c.category
ORDER BY c.category, c.holder, c.season, c.game_date NULLS LAST;

INSERT INTO record_categories (category, label, scope, subject, better, sort_order, coverage) VALUES
    ('season_sacks', 'Sacks in a season', 'SEASON', 'PLAYER', 'HIGH', 18, 'BOOK'),
    ('game_rush_tds', 'Rushing TDs in a game', 'GAME', 'PLAYER', 'HIGH', 19, 'BOOK'),
    ('game_rec_tds', 'Receiving TDs in a game', 'GAME', 'PLAYER', 'HIGH', 20, 'BOOK'),
    ('game_def_interceptions', 'Interceptions in a game', 'GAME', 'PLAYER', 'HIGH', 21, 'BOOK'),
    ('rookie_season_pass_yards', 'Passing yards in a rookie season', 'SEASON', 'PLAYER', 'HIGH', 22, 'BOOK'),
    ('rookie_season_pass_tds', 'Passing TDs in a rookie season', 'SEASON', 'PLAYER', 'HIGH', 23, 'BOOK'),
    ('rookie_season_rush_yards', 'Rushing yards in a rookie season', 'SEASON', 'PLAYER', 'HIGH', 24, 'BOOK'),
    ('rookie_season_rush_tds', 'Rushing TDs in a rookie season', 'SEASON', 'PLAYER', 'HIGH', 25, 'BOOK'),
    ('rookie_season_receptions', 'Receptions in a rookie season', 'SEASON', 'PLAYER', 'HIGH', 26, 'BOOK'),
    ('rookie_season_rec_yards', 'Receiving yards in a rookie season', 'SEASON', 'PLAYER', 'HIGH', 27, 'BOOK'),
    ('rookie_season_rec_tds', 'Receiving TDs in a rookie season', 'SEASON', 'PLAYER', 'HIGH', 28, 'BOOK'),
    ('rookie_game_pass_yards', 'Passing yards in a game by a rookie', 'GAME', 'PLAYER', 'HIGH', 29, 'BOOK'),
    ('rookie_game_pass_tds', 'Passing TDs in a game by a rookie', 'GAME', 'PLAYER', 'HIGH', 30, 'BOOK'),
    ('rookie_game_rush_yards', 'Rushing yards in a game by a rookie', 'GAME', 'PLAYER', 'HIGH', 31, 'BOOK'),
    ('rookie_game_rush_tds', 'Rushing TDs in a game by a rookie', 'GAME', 'PLAYER', 'HIGH', 32, 'BOOK'),
    ('rookie_game_receptions', 'Receptions in a game by a rookie', 'GAME', 'PLAYER', 'HIGH', 33, 'BOOK'),
    ('rookie_game_rec_yards', 'Receiving yards in a game by a rookie', 'GAME', 'PLAYER', 'HIGH', 34, 'BOOK'),
    ('rookie_game_rec_tds', 'Receiving TDs in a game by a rookie', 'GAME', 'PLAYER', 'HIGH', 35, 'BOOK'),
    ('rookie_game_sacks', 'Sacks in a game by a rookie', 'GAME', 'PLAYER', 'HIGH', 36, 'BOOK')
ON CONFLICT (category) DO UPDATE SET label = EXCLUDED.label, scope = EXCLUDED.scope, subject = EXCLUDED.subject,
    sort_order = EXCLUDED.sort_order, coverage = EXCLUDED.coverage;

-- Franchise records set in this league, 2026–2035 (119 rows).
INSERT INTO franchise_records (team_id, category, value, holder, season, source)
SELECT t.id, v.category, v.value, v.holder, v.season, 'BOOK'
FROM (VALUES
    ('CHI', 'season_pass_yards', 5033, 'Caleb Williams', 2029),
    ('CHI', 'season_pass_tds', 38, 'Caleb Williams', 2032),
    ('CHI', 'season_rec_yards', 1705, 'Luther Burden III', 2029),
    ('CHI', 'season_rec_tds', 19, 'Luther Burden III', 2030),
    ('CIN', 'season_pass_yards', 5189, 'Joe Burrow', 2028),
    ('BUF', 'season_rec_tds', 14, 'Khalil Shakir', 2029),
    ('DEN', 'season_rec_tds', 15, 'Zavion Thomas', 2034),
    ('DEN', 'season_receptions', 119, 'Rashee Rice', 2032),
    ('CLE', 'season_pass_yards', 5020, 'Shedeur Sanders', 2029),
    ('CLE', 'season_pass_tds', 38, 'Shedeur Sanders', 2031),
    ('CLE', 'season_receptions', 123, 'Jerry Jeudy', 2029),
    ('TB', 'season_receptions', 118, 'Chris Godwin Jr.', 2028),
    ('AZ', 'season_pass_yards', 5206, 'Fernando Mendoza', 2033),
    ('AZ', 'season_pass_tds', 36, 'Fernando Mendoza', 2034),
    ('AZ', 'season_rush_tds', 18, 'Bobby Smith', 2032),
    ('AZ', 'season_rec_yards', 1817, 'DeAngelo Hargrave', 2033),
    ('LAC', 'season_pass_yards', 5018, 'Justin Herbert', 2027),
    ('LAC', 'season_rec_yards', 1809, 'Ladd McConkey', 2031),
    ('LAC', 'season_rec_tds', 18, 'Ladd McConkey', 2029),
    ('LAC', 'season_receptions', 120, 'Quentin Johnston', 2028),
    ('KC', 'season_pass_yards', 5361, 'J.J. McCarthy', 2030),
    ('KC', 'season_rec_yards', 1713, 'Rashee Rice', 2030),
    ('DAL', 'season_pass_yards', 5304, 'Dak Prescott', 2027),
    ('DAL', 'season_pass_tds', 47, 'Dak Prescott', 2027),
    ('DAL', 'season_rec_tds', 20, 'CeeDee Lamb', 2027),
    ('PHI', 'season_pass_yards', 4551, 'Jalen Hurts', 2028),
    ('PHI', 'season_pass_tds', 41, 'Jalen Hurts', 2028),
    ('PHI', 'season_rush_tds', 18, 'Saquon Barkley', 2027),
    ('PHI', 'season_rec_yards', 1827, 'Makai Lemon', 2028),
    ('PHI', 'season_rec_tds', 15, 'Makai Lemon', 2028),
    ('NYG', 'season_rush_yards', 1905, 'Cam Skattebo', 2028),
    ('NYG', 'season_rec_tds', 15, 'Malachi Fields', 2029),
    ('NYG', 'season_sacks', 23, 'Arvell Reese', 2029),
    ('NYJ', 'season_pass_yards', 4356, 'Cade Klubnik', 2029),
    ('NYJ', 'season_rush_tds', 15, 'Ben Gore', 2034),
    ('NYJ', 'season_receptions', 111, 'Sam Walton', 2034),
    ('DET', 'season_rec_tds', 18, 'Jameson Williams', 2032),
    ('CAR', 'season_pass_yards', 5359, 'George Crawford', 2034),
    ('CAR', 'season_pass_tds', 67, 'George Crawford', 2035),
    ('CAR', 'season_rush_yards', 1733, 'Ka''Von Franklin', 2035),
    ('CAR', 'season_rec_yards', 2406, 'Trey''Mane Davis', 2034),
    ('CAR', 'season_rec_tds', 27, 'Trey''Mane Davis', 2035),
    ('CAR', 'season_receptions', 129, 'Trey''Mane Davis', 2034),
    ('LV', 'season_rush_tds', 18, 'Ashton Jeanty', 2032),
    ('LV', 'season_rec_yards', 1587, 'Rome Odunze', 2028),
    ('LAR', 'season_pass_yards', 5175, 'Ty Simpson', 2034),
    ('NO', 'season_rec_tds', 19, 'Jordyn Tyson', 2032),
    ('SEA', 'season_rec_tds', 18, 'Eddie Castro', 2031),
    ('PIT', 'season_pass_tds', 38, 'Ryan Nichols', 2035),
    ('PIT', 'season_rec_tds', 19, 'Cody Hansen', 2035),
    ('TEN', 'season_pass_yards', 4944, 'Emmett Carroll', 2033),
    ('TEN', 'season_pass_tds', 40, 'Cam Ward', 2031),
    ('TEN', 'season_receptions', 104, 'Max Stockton', 2034),
    ('CHI', 'game_rec_yards', 338, 'Luther Burden III', 2028),
    ('TB', 'game_rec_yards', 277, 'Emeka Egbuka', 2031),
    ('KC', 'game_receptions', 16, 'Eddie Santiago', 2034),
    ('DAL', 'game_def_interceptions', 4, 'Caleb Downs', 2032),
    ('ATL', 'game_def_interceptions', 3, 'A.J. Terrell Jr.', 2028),
    ('NYG', 'game_rec_yards', 293, 'Michael Wilson', 2031),
    ('NYJ', 'game_rush_tds', 5, 'Ben Gore', 2033),
    ('CAR', 'game_pass_yards', 506, 'George Crawford', 2035),
    ('CAR', 'game_pass_tds', 7, 'George Crawford', 2035),
    ('CAR', 'game_rush_yards', 217, 'Ka''Von Franklin', 2035),
    ('CAR', 'game_rec_yards', 247, 'Trey''Mane Davis', 2034),
    ('CAR', 'game_rec_tds', 4, 'Trey''Mane Davis', 2035),
    ('CAR', 'game_receptions', 16, 'Isaiah Carson', 2035),
    ('NE', 'game_rec_yards', 226, 'Kayshon Boutte', 2032),
    ('LV', 'game_rush_tds', 4, 'Ashton Jeanty', 2027),
    ('LAR', 'game_pass_yards', 592, 'Ty Simpson', 2034),
    ('LAR', 'game_pass_tds', 6, 'Ty Simpson', 2031),
    ('NO', 'game_pass_yards', 538, 'Jared Phillips', 2032),
    ('NO', 'game_rec_yards', 399, 'Jordyn Tyson', 2032),
    ('SEA', 'game_pass_yards', 498, 'Sam Darnold', 2029),
    ('SEA', 'game_pass_tds', 7, 'Sam Darnold', 2029),
    ('SEA', 'game_rec_yards', 269, 'Jaxon Smith-Njigba', 2029),
    ('SEA', 'game_rec_tds', 5, 'Jaylen Waddle', 2029),
    ('CHI', 'rookie_season_receptions', 71, 'Fernando Guerrero', 2028),
    ('BUF', 'rookie_season_pass_yards', 3105, 'Antoine Lowery', 2035),
    ('BUF', 'rookie_season_pass_tds', 12, 'Antoine Lowery', 2035),
    ('BUF', 'rookie_season_rec_yards', 1047, 'Aaron Wright', 2034),
    ('DEN', 'rookie_season_rec_yards', 1175, 'Cody Hansen', 2030),
    ('DEN', 'rookie_season_rec_tds', 10, 'Cody Hansen', 2030),
    ('AZ', 'rookie_season_rush_tds', 11, 'Bobby Smith', 2030),
    ('WAS', 'rookie_season_rec_yards', 985, 'Evan Smith', 2029),
    ('WAS', 'rookie_season_rec_tds', 18, 'Evan Smith', 2029),
    ('WAS', 'rookie_season_receptions', 79, 'Evan Smith', 2029),
    ('PHI', 'rookie_season_rush_tds', 9, 'Corey Winters', 2032),
    ('NYG', 'rookie_season_rush_tds', 12, 'Chauncey Britton', 2031),
    ('NYJ', 'rookie_season_rush_tds', 8, 'Brandon Brackins', 2032),
    ('GB', 'rookie_season_pass_yards', 3956, 'Tommy Williams', 2034),
    ('GB', 'rookie_season_pass_tds', 23, 'Tommy Williams', 2034),
    ('CAR', 'rookie_season_rush_yards', 1016, 'Ka''Von Franklin', 2032),
    ('CAR', 'rookie_season_rec_yards', 1484, 'Isaiah Carson', 2035),
    ('CAR', 'rookie_season_rec_tds', 18, 'Isaiah Carson', 2035),
    ('CAR', 'rookie_season_receptions', 99, 'Isaiah Carson', 2035),
    ('NO', 'rookie_season_pass_yards', 2411, 'Jared Phillips', 2029),
    ('MIN', 'rookie_season_pass_yards', 3027, 'Joey Mayes', 2028),
    ('MIN', 'rookie_season_pass_tds', 25, 'Joey Mayes', 2028),
    ('DEN', 'rookie_game_rec_yards', 169, 'Cody Hansen', 2030),
    ('TB', 'rookie_game_receptions', 10, 'Hayden Mann', 2028),
    ('AZ', 'rookie_game_rec_yards', 251, 'Billy Thompson', 2030),
    ('AZ', 'rookie_game_receptions', 12, 'Joshua Randolph', 2034),
    ('AZ', 'rookie_game_sacks', 2, 'Bayo Ojulari', 2028),
    ('LAC', 'rookie_game_pass_yards', 406, 'Will Whitlock', 2032),
    ('DAL', 'rookie_game_receptions', 10, 'Josh Perez', 2033),
    ('MIA', 'rookie_game_pass_tds', 4, 'Arch Manning', 2027),
    ('MIA', 'rookie_game_rush_yards', 203, 'De''Von Achane', 2028),
    ('MIA', 'rookie_game_rec_tds', 2, 'De''Von Achane', 2028),
    ('GB', 'rookie_game_pass_yards', 381, 'Tommy Williams', 2034),
    ('CAR', 'rookie_game_pass_tds', 6, 'George Crawford', 2034),
    ('CAR', 'rookie_game_rec_yards', 198, 'Isaiah Carson', 2035),
    ('CAR', 'rookie_game_rec_tds', 3, 'Isaiah Carson', 2035),
    ('CAR', 'rookie_game_receptions', 16, 'Isaiah Carson', 2035),
    ('BAL', 'rookie_game_rush_tds', 3, 'Jeremiah Gettis', 2035),
    ('SEA', 'rookie_game_pass_yards', 392, 'Bryce Mobley', 2031),
    ('SEA', 'rookie_game_pass_tds', 5, 'Bryce Mobley', 2031),
    ('CAR', 'rookie_season_pass_yards', 5359, 'George Crawford', 2034),
    ('CAR', 'rookie_season_pass_tds', 47, 'George Crawford', 2034),
    ('CAR', 'season_pass_tds', 47, 'George Crawford', 2034)
) AS v (abbr, category, value, holder, season)
JOIN teams t ON t.abbreviation = v.abbr
ON CONFLICT (team_id, category, holder, season) DO UPDATE SET value = EXCLUDED.value, source = 'BOOK';

-- The same seasons and games on the all-time lists (the views rank them; 75 rows), plus the record book's
-- NFL-wide rookie records.
INSERT INTO league_record_entries (category, value, holder, team_id, season, source)
SELECT v.category, v.value, v.holder, t.id, v.season, 'BOOK'
FROM (VALUES
    ('season_pass_yards', 5033, 'Caleb Williams', 'CHI', 2029),
    ('season_pass_tds', 38, 'Caleb Williams', 'CHI', 2032),
    ('season_rec_yards', 1705, 'Luther Burden III', 'CHI', 2029),
    ('season_rec_tds', 19, 'Luther Burden III', 'CHI', 2030),
    ('season_pass_yards', 5189, 'Joe Burrow', 'CIN', 2028),
    ('season_rec_tds', 14, 'Khalil Shakir', 'BUF', 2029),
    ('season_rec_tds', 15, 'Zavion Thomas', 'DEN', 2034),
    ('season_receptions', 119, 'Rashee Rice', 'DEN', 2032),
    ('season_pass_yards', 5020, 'Shedeur Sanders', 'CLE', 2029),
    ('season_pass_tds', 38, 'Shedeur Sanders', 'CLE', 2031),
    ('season_receptions', 123, 'Jerry Jeudy', 'CLE', 2029),
    ('season_receptions', 118, 'Chris Godwin Jr.', 'TB', 2028),
    ('season_pass_yards', 5206, 'Fernando Mendoza', 'AZ', 2033),
    ('season_pass_tds', 36, 'Fernando Mendoza', 'AZ', 2034),
    ('season_rush_tds', 18, 'Bobby Smith', 'AZ', 2032),
    ('season_rec_yards', 1817, 'DeAngelo Hargrave', 'AZ', 2033),
    ('season_pass_yards', 5018, 'Justin Herbert', 'LAC', 2027),
    ('season_rec_yards', 1809, 'Ladd McConkey', 'LAC', 2031),
    ('season_rec_tds', 18, 'Ladd McConkey', 'LAC', 2029),
    ('season_receptions', 120, 'Quentin Johnston', 'LAC', 2028),
    ('season_pass_yards', 5361, 'J.J. McCarthy', 'KC', 2030),
    ('season_rec_yards', 1713, 'Rashee Rice', 'KC', 2030),
    ('season_pass_yards', 5304, 'Dak Prescott', 'DAL', 2027),
    ('season_pass_tds', 47, 'Dak Prescott', 'DAL', 2027),
    ('season_rec_tds', 20, 'CeeDee Lamb', 'DAL', 2027),
    ('season_pass_yards', 4551, 'Jalen Hurts', 'PHI', 2028),
    ('season_pass_tds', 41, 'Jalen Hurts', 'PHI', 2028),
    ('season_rush_tds', 18, 'Saquon Barkley', 'PHI', 2027),
    ('season_rec_yards', 1827, 'Makai Lemon', 'PHI', 2028),
    ('season_rec_tds', 15, 'Makai Lemon', 'PHI', 2028),
    ('season_rush_yards', 1905, 'Cam Skattebo', 'NYG', 2028),
    ('season_rec_tds', 15, 'Malachi Fields', 'NYG', 2029),
    ('season_pass_yards', 4356, 'Cade Klubnik', 'NYJ', 2029),
    ('season_rush_tds', 15, 'Ben Gore', 'NYJ', 2034),
    ('season_receptions', 111, 'Sam Walton', 'NYJ', 2034),
    ('season_rec_tds', 18, 'Jameson Williams', 'DET', 2032),
    ('season_pass_yards', 5359, 'George Crawford', 'CAR', 2034),
    ('season_pass_tds', 67, 'George Crawford', 'CAR', 2035),
    ('season_rush_yards', 1733, 'Ka''Von Franklin', 'CAR', 2035),
    ('season_rec_yards', 2406, 'Trey''Mane Davis', 'CAR', 2034),
    ('season_rec_tds', 27, 'Trey''Mane Davis', 'CAR', 2035),
    ('season_receptions', 129, 'Trey''Mane Davis', 'CAR', 2034),
    ('season_rush_tds', 18, 'Ashton Jeanty', 'LV', 2032),
    ('season_rec_yards', 1587, 'Rome Odunze', 'LV', 2028),
    ('season_pass_yards', 5175, 'Ty Simpson', 'LAR', 2034),
    ('season_rec_tds', 19, 'Jordyn Tyson', 'NO', 2032),
    ('season_rec_tds', 18, 'Eddie Castro', 'SEA', 2031),
    ('season_pass_tds', 38, 'Ryan Nichols', 'PIT', 2035),
    ('season_rec_tds', 19, 'Cody Hansen', 'PIT', 2035),
    ('season_pass_yards', 4944, 'Emmett Carroll', 'TEN', 2033),
    ('season_pass_tds', 40, 'Cam Ward', 'TEN', 2031),
    ('season_receptions', 104, 'Max Stockton', 'TEN', 2034),
    ('game_rec_yards', 338, 'Luther Burden III', 'CHI', 2028),
    ('game_rec_yards', 277, 'Emeka Egbuka', 'TB', 2031),
    ('game_receptions', 16, 'Eddie Santiago', 'KC', 2034),
    ('game_rec_yards', 293, 'Michael Wilson', 'NYG', 2031),
    ('game_pass_yards', 506, 'George Crawford', 'CAR', 2035),
    ('game_pass_tds', 7, 'George Crawford', 'CAR', 2035),
    ('game_rush_yards', 217, 'Ka''Von Franklin', 'CAR', 2035),
    ('game_rec_yards', 247, 'Trey''Mane Davis', 'CAR', 2034),
    ('game_receptions', 16, 'Isaiah Carson', 'CAR', 2035),
    ('game_rec_yards', 226, 'Kayshon Boutte', 'NE', 2032),
    ('game_pass_yards', 592, 'Ty Simpson', 'LAR', 2034),
    ('game_pass_tds', 6, 'Ty Simpson', 'LAR', 2031),
    ('game_pass_yards', 538, 'Jared Phillips', 'NO', 2032),
    ('game_rec_yards', 399, 'Jordyn Tyson', 'NO', 2032),
    ('game_pass_yards', 498, 'Sam Darnold', 'SEA', 2029),
    ('game_pass_tds', 7, 'Sam Darnold', 'SEA', 2029),
    ('game_rec_yards', 269, 'Jaxon Smith-Njigba', 'SEA', 2029),
    ('season_pass_tds', 47, 'George Crawford', 'CAR', 2034),
    ('rookie_season_pass_yards', 5359, 'George Crawford', 'CAR', 2034),
    ('rookie_season_pass_tds', 47, 'George Crawford', 'CAR', 2034),
    ('rookie_season_rec_tds', 18, 'Isaiah Carson', 'CAR', 2035),
    ('rookie_game_pass_tds', 6, 'George Crawford', 'CAR', 2034),
    ('rookie_game_receptions', 16, 'Isaiah Carson', 'CAR', 2035)
) AS v (category, value, holder, abbr, season)
JOIN teams t ON t.abbreviation = v.abbr
ON CONFLICT DO NOTHING;

COMMIT;
