-- @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history data)
-- @REPLACED-BY: db/seed_clean_db.py + db/init_db.sql (old-universe data only; AUDIT_LEDGER.md §3b)
-- Migration: 2026_11_30_panthers_2034_playoff_statlines.sql
-- Description:
--   Seeds exact canonical player_stats and team_stats for Carolina's 2034 playoff run:
--   1) 2034 NFC Divisional Round vs NY Giants (Game 3826, 42-35)
--   2) 2034 NFC Championship Game vs Philadelphia Eagles (Game 3827, 38-14)
--   3) Super Bowl LXIX vs New York Jets (Game 3720, 48-27)

BEGIN;

-- 1. Ensure Patrick Groves and Cole Payton exist in players
INSERT INTO players (player_name, full_name, player_number)
SELECT 'P. Groves', 'Patrick Groves', 81
WHERE NOT EXISTS (SELECT 1 FROM players WHERE full_name = 'Patrick Groves');

INSERT INTO players (player_name, full_name, player_number)
SELECT 'C. Payton', 'Cole Payton', 12
WHERE NOT EXISTS (SELECT 1 FROM players WHERE full_name = 'Cole Payton');

-- 2. Team Stats for the three 2034 playoff games
-- Game 3826: 2034 NFC Divisional Round (CAR 42, NYG 35)
INSERT INTO team_stats (game_id, team_name, score, passing_yards, passing_tds, passing_completions, passing_attempts, rushing_yards, rushing_tds, rushing_attempts, total_offense, estimated_fields, exact_fields)
VALUES
    (3826, 'Panthers', 42, 364, 5, 27, 40, 137, 0, 19, 501, '{}', '{}'),
    (3826, 'Giants', 35, 261, 2, 17, 26, 41, 2, 4, 302, '{}', '{}')
ON CONFLICT (game_id, team_name) DO UPDATE
SET score = EXCLUDED.score,
    passing_yards = EXCLUDED.passing_yards,
    passing_tds = EXCLUDED.passing_tds,
    passing_completions = EXCLUDED.passing_completions,
    passing_attempts = EXCLUDED.passing_attempts,
    rushing_yards = EXCLUDED.rushing_yards,
    rushing_tds = EXCLUDED.rushing_tds,
    rushing_attempts = EXCLUDED.rushing_attempts,
    total_offense = EXCLUDED.total_offense;

-- Game 3827: 2034 NFC Championship (CAR 38, PHI 14)
INSERT INTO team_stats (game_id, team_name, score, passing_yards, passing_tds, passing_completions, passing_attempts, rushing_yards, rushing_tds, rushing_attempts, total_offense, estimated_fields, exact_fields)
VALUES
    (3827, 'Panthers', 38, 237, 4, 21, 32, 251, 1, 28, 488, '{}', '{}'),
    (3827, 'Eagles', 14, 165, 2, 16, 28, 35, 0, 12, 200, '{}', '{}')
ON CONFLICT (game_id, team_name) DO UPDATE
SET score = EXCLUDED.score,
    passing_yards = EXCLUDED.passing_yards,
    passing_tds = EXCLUDED.passing_tds,
    passing_completions = EXCLUDED.passing_completions,
    passing_attempts = EXCLUDED.passing_attempts,
    rushing_yards = EXCLUDED.rushing_yards,
    rushing_tds = EXCLUDED.rushing_tds,
    rushing_attempts = EXCLUDED.rushing_attempts,
    total_offense = EXCLUDED.total_offense;

-- Game 3720: Super Bowl LXIX (CAR 48, NYJ 27)
INSERT INTO team_stats (game_id, team_name, score, passing_yards, passing_tds, passing_completions, passing_attempts, rushing_yards, rushing_tds, rushing_attempts, total_offense, estimated_fields, exact_fields)
VALUES
    (3720, 'Panthers', 48, 354, 3, 29, 37, 161, 3, 25, 515, '{}', '{}'),
    (3720, 'Jets', 27, 247, 1, 24, 34, 87, 1, 12, 334, '{}', '{}')
ON CONFLICT (game_id, team_name) DO UPDATE
SET score = EXCLUDED.score,
    passing_yards = EXCLUDED.passing_yards,
    passing_tds = EXCLUDED.passing_tds,
    passing_completions = EXCLUDED.passing_completions,
    passing_attempts = EXCLUDED.passing_attempts,
    rushing_yards = EXCLUDED.rushing_yards,
    rushing_tds = EXCLUDED.rushing_tds,
    rushing_attempts = EXCLUDED.rushing_attempts,
    total_offense = EXCLUDED.total_offense;

-- 3. Player Stats for the three 2034 playoff games
-- Game 3826: Divisional Round (Panthers vs Giants)
INSERT INTO player_stats (
    game_id, player_id, player_name, position, team_id,
    completions, pass_attempts, pass_yards, pass_tds, interceptions, pass_comp_pct, pass_ypa, pass_rating,
    rush_attempts, rush_yards, rush_tds, rush_avg,
    receptions, rec_yards, rec_tds, rec_avg,
    line_source
)
VALUES
    -- George Crawford
    (3826, 325, 'G. Crawford', 'QB', 11, 27, 40, 364, 5, 0, 67.5, 9.1, 135.8, 7, 65, 0, 9.3, 0, 0, 0, 0, 'CAPTURED'),
    -- Trey'Mane Davis
    (3826, 80, 'T. Davis', 'WR', 11, 0, 0, 0, 0, 0, NULL, NULL, NULL, 0, 0, 0, NULL, 10, 221, 3, 22.1, 'CAPTURED'),
    -- Ka'Von Franklin
    (3826, 44, 'K. Franklin', 'HB', 11, 0, 0, 0, 0, 0, NULL, NULL, NULL, 12, 72, 0, 6.0, 5, 29, 0, 5.8, 'CAPTURED'),
    -- Bryce Mobley
    (3826, 608, 'B. Mobley', 'QB', 7, 17, 26, 261, 2, 0, 65.4, 10.0, 124.0, 4, 41, 2, 10.3, 0, 0, 0, 0, 'CAPTURED'),
    -- Patrick Groves
    (3826, (SELECT id FROM players WHERE full_name = 'Patrick Groves' LIMIT 1), 'P. Groves', 'WR', 7, 0, 0, 0, 0, 0, NULL, NULL, NULL, 0, 0, 0, NULL, 5, 100, 2, 20.0, 'CAPTURED')
ON CONFLICT (game_id, player_name, position) DO UPDATE
SET completions = EXCLUDED.completions,
    pass_attempts = EXCLUDED.pass_attempts,
    pass_yards = EXCLUDED.pass_yards,
    pass_tds = EXCLUDED.pass_tds,
    rush_attempts = EXCLUDED.rush_attempts,
    rush_yards = EXCLUDED.rush_yards,
    rush_tds = EXCLUDED.rush_tds,
    receptions = EXCLUDED.receptions,
    rec_yards = EXCLUDED.rec_yards,
    rec_tds = EXCLUDED.rec_tds,
    line_source = EXCLUDED.line_source;

-- Game 3827: NFC Championship Game (Panthers vs Eagles)
INSERT INTO player_stats (
    game_id, player_id, player_name, position, team_id,
    completions, pass_attempts, pass_yards, pass_tds, interceptions, pass_comp_pct, pass_ypa, pass_rating,
    rush_attempts, rush_yards, rush_tds, rush_avg,
    receptions, rec_yards, rec_tds, rec_avg,
    pass_sacks, line_source
)
VALUES
    -- George Crawford
    (3827, 325, 'G. Crawford', 'QB', 11, 21, 32, 237, 4, 0, 65.6, 7.4, 127.2, 6, 47, 0, 7.8, 0, 0, 0, 0, 0, 'CAPTURED'),
    -- Ka'Von Franklin
    (3827, 44, 'K. Franklin', 'HB', 11, 0, 0, 0, 0, 0, NULL, NULL, NULL, 22, 204, 1, 9.3, 6, 20, 0, 3.3, 0, 'CAPTURED'),
    -- Trey'Mane Davis
    (3827, 80, 'T. Davis', 'WR', 11, 0, 0, 0, 0, 0, NULL, NULL, NULL, 0, 0, 0, NULL, 7, 131, 3, 18.7, 0, 'CAPTURED'),
    -- Cole Payton
    (3827, (SELECT id FROM players WHERE full_name = 'Cole Payton' LIMIT 1), 'C. Payton', 'QB', 8, 16, 28, 165, 2, 1, 57.1, 5.9, 77.2, 2, 5, 0, 2.5, 0, 0, 0, 0, 5, 'CAPTURED')
ON CONFLICT (game_id, player_name, position) DO UPDATE
SET completions = EXCLUDED.completions,
    pass_attempts = EXCLUDED.pass_attempts,
    pass_yards = EXCLUDED.pass_yards,
    pass_tds = EXCLUDED.pass_tds,
    rush_attempts = EXCLUDED.rush_attempts,
    rush_yards = EXCLUDED.rush_yards,
    rush_tds = EXCLUDED.rush_tds,
    receptions = EXCLUDED.receptions,
    rec_yards = EXCLUDED.rec_yards,
    rec_tds = EXCLUDED.rec_tds,
    pass_sacks = EXCLUDED.pass_sacks,
    line_source = EXCLUDED.line_source;

-- Game 3720: Super Bowl LXIX (Panthers vs Jets)
INSERT INTO player_stats (
    game_id, player_id, player_name, position, team_id,
    completions, pass_attempts, pass_yards, pass_tds, interceptions, pass_comp_pct, pass_ypa, pass_rating,
    rush_attempts, rush_yards, rush_tds, rush_avg,
    receptions, rec_yards, rec_tds, rec_avg,
    line_source
)
VALUES
    -- George Crawford
    (3720, 325, 'G. Crawford', 'QB', 11, 29, 37, 354, 3, 0, 78.4, 9.6, 135.2, 3, 44, 1, 14.7, 0, 0, 0, 0, 'CAPTURED'),
    -- Ka'Von Franklin
    (3720, 44, 'K. Franklin', 'HB', 11, 0, 0, 0, 0, 0, NULL, NULL, NULL, 22, 117, 2, 5.3, 0, 0, 0, 0, 'CAPTURED'),
    -- Trey'Mane Davis
    (3720, 80, 'T. Davis', 'WR', 11, 0, 0, 0, 0, 0, NULL, NULL, NULL, 0, 0, 0, NULL, 9, 151, 2, 16.8, 'CAPTURED'),
    -- Elliott Harden
    (3720, 45, 'E. Harden', 'TE', 11, 0, 0, 0, 0, 0, NULL, NULL, NULL, 0, 0, 0, NULL, 1, 5, 1, 5.0, 'CAPTURED'),
    -- Ty Ainsworth
    (3720, 520, 'T. Ainsworth', 'QB', 18, 24, 34, 247, 1, 0, 70.6, 7.3, 98.5, 4, 48, 0, 12.0, 0, 0, 0, 0, 'CAPTURED'),
    -- Ben Gore
    (3720, 1355, 'B. Gore', 'HB', 18, 0, 0, 0, 0, 0, NULL, NULL, NULL, 8, 39, 1, 4.9, 0, 0, 0, 0, 'CAPTURED'),
    -- Layne Russo
    (3720, 95, 'L. Russo', 'WR', 18, 0, 0, 0, 0, 0, NULL, NULL, NULL, 0, 0, 0, NULL, 6, 116, 0, 19.3, 'CAPTURED')
ON CONFLICT (game_id, player_name, position) DO UPDATE
SET completions = EXCLUDED.completions,
    pass_attempts = EXCLUDED.pass_attempts,
    pass_yards = EXCLUDED.pass_yards,
    pass_tds = EXCLUDED.pass_tds,
    rush_attempts = EXCLUDED.rush_attempts,
    rush_yards = EXCLUDED.rush_yards,
    rush_tds = EXCLUDED.rush_tds,
    receptions = EXCLUDED.receptions,
    rec_yards = EXCLUDED.rec_yards,
    rec_tds = EXCLUDED.rec_tds,
    line_source = EXCLUDED.line_source;

COMMIT;
