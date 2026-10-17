-- Migration: 2026_11_29_panthers_history_backfill.sql
-- Description:
--   1) Corrects 2034 Coach of the Year (COTY) in awards to Marcus Whitfield (Carolina Panthers).
--   2) Populates team_seasons historical records for Carolina Panthers (2031: 0-17, 2032: 4-13, 2033: 4-13, 2034: 13-4).
--   3) Backfills 2034 Carolina Panthers regular season (17 games) and playoff games (Divisional vs NYG, NFCCG vs PHI).
--   4) Seeds historical season_stats for George Crawford (2034), Trey'Mane Davis (2032-2034), Ka'Von Franklin (2032-2034), and Raheem Tucker (2033).

BEGIN;

-- 1) Fix 2034 Coach of the Year in awards
UPDATE awards
SET coach_id = 2,
    coach_name = 'Marcus Whitfield',
    team_id = 11
WHERE season = 2034 AND award_type = 'COTY';

-- 2) Populate team_seasons for Carolina Panthers (team_id = 11)
INSERT INTO team_seasons (team_id, season, wins, losses)
VALUES
    (11, 2031, 0, 17),
    (11, 2032, 4, 13),
    (11, 2033, 4, 13)
ON CONFLICT (team_id, season) DO UPDATE
SET wins = EXCLUDED.wins,
    losses = EXCLUDED.losses;

UPDATE team_seasons
SET wins = 13,
    losses = 4
WHERE team_id = 11 AND season = 2034;

-- 3) Backfill 2034 Carolina Panthers games
-- Regular Season Weeks 1-17
INSERT INTO games (season, week, home_team_id, away_team_id, home_score, away_score, status, game_type)
VALUES
    -- Week 1: vs Cardinals W 38-35
    (2034, 1, 11, 23, 38, 35, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 2: vs Bengals W 38-10
    (2034, 2, 11, 24, 38, 10, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 3: vs Ravens W 27-24
    (2034, 3, 11, 16, 27, 24, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 4: @ Seahawks L 9-30
    (2034, 4, 6, 11, 30, 9, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 5: vs Saints W 24-6
    (2034, 5, 11, 29, 24, 6, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 6: @ Steelers L 14-31
    (2034, 6, 15, 11, 31, 14, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 7: @ Browns L 19-27
    (2034, 7, 14, 11, 27, 19, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 8: @ Saints L 28-31
    (2034, 8, 29, 11, 31, 28, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 9: vs 49ers W 34-28
    (2034, 9, 11, 27, 34, 28, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 10: vs Buccaneers W 44-10
    (2034, 10, 11, 9, 44, 10, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 11: @ Chargers W 42-38
    (2034, 11, 36, 11, 38, 42, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 12: @ Buccaneers W 34-30
    (2034, 12, 9, 11, 30, 34, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 13: vs Commanders W 34-0
    (2034, 13, 11, 22, 34, 0, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 14: vs Vikings W 41-17
    (2034, 14, 11, 12, 41, 17, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 15: @ Falcons W 45-20
    (2034, 15, 25, 11, 20, 45, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 16: vs Rams W 30-3
    (2034, 16, 11, 21, 30, 3, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 17: vs Falcons W 34-7
    (2034, 17, 11, 25, 34, 7, 'COMPLETED', 'REGULAR_SEASON'),
    -- Week 20: NFC Divisional vs Giants W 42-35
    (2034, 20, 11, 7, 42, 35, 'COMPLETED', 'DIVISIONAL'),
    -- Week 21: NFC Championship vs Eagles W 38-14
    (2034, 21, 11, 8, 38, 14, 'COMPLETED', 'CONFERENCE')
ON CONFLICT (season, week, home_team_id, away_team_id) DO UPDATE
SET home_score = EXCLUDED.home_score,
    away_score = EXCLUDED.away_score,
    status = EXCLUDED.status,
    game_type = EXCLUDED.game_type;

-- 4) Seed historical season_stats

-- George Crawford (2034)
INSERT INTO season_stats (
    season, player_name, player_id, team_id, position,
    pass_yards, pass_tds, interceptions, completions, pass_attempts,
    pass_comp_pct, pass_rating, pass_ypg,
    rush_yards, rush_tds, rush_ypg,
    rec_yards, rec_tds, receptions,
    total_fantasy_points, ppg_fantasy_points
)
VALUES (
    2034, 'G. Crawford', 325, 11, 'QB',
    5359, 47, 4, 340, 540,
    63.0, 114.4, 315.2,
    851, 7, 50.1,
    0, 0, 0,
    521.46, 30.67
)
ON CONFLICT (season, player_name, team_id, position) DO UPDATE
SET pass_yards = EXCLUDED.pass_yards,
    pass_tds = EXCLUDED.pass_tds,
    interceptions = EXCLUDED.interceptions,
    completions = EXCLUDED.completions,
    pass_attempts = EXCLUDED.pass_attempts,
    pass_comp_pct = EXCLUDED.pass_comp_pct,
    pass_rating = EXCLUDED.pass_rating,
    pass_ypg = EXCLUDED.pass_ypg,
    rush_yards = EXCLUDED.rush_yards,
    rush_tds = EXCLUDED.rush_tds,
    rush_ypg = EXCLUDED.rush_ypg,
    total_fantasy_points = EXCLUDED.total_fantasy_points,
    ppg_fantasy_points = EXCLUDED.ppg_fantasy_points;

-- Trey'Mane Davis (2032, 2033, 2034)
INSERT INTO season_stats (
    season, player_name, player_id, team_id, position,
    receptions, rec_yards, rec_tds, rec_avg, rec_ypg,
    rush_yards, rush_tds, pass_yards, pass_tds,
    total_fantasy_points, ppg_fantasy_points
)
VALUES
    (2032, 'T. Davis', 80, 11, 'WR', 75, 1213, 9, 16.2, 71.4, 0, 0, 0, 0, 250.30, 14.72),
    (2033, 'T. Davis', 80, 11, 'WR', 71, 1076, 9, 15.2, 63.3, 0, 0, 0, 0, 232.60, 13.68),
    (2034, 'T. Davis', 80, 11, 'WR', 129, 2406, 24, 18.7, 141.5, 0, 0, 0, 0, 513.60, 30.21)
ON CONFLICT (season, player_name, team_id, position) DO UPDATE
SET receptions = EXCLUDED.receptions,
    rec_yards = EXCLUDED.rec_yards,
    rec_tds = EXCLUDED.rec_tds,
    rec_avg = EXCLUDED.rec_avg,
    rec_ypg = EXCLUDED.rec_ypg,
    total_fantasy_points = EXCLUDED.total_fantasy_points,
    ppg_fantasy_points = EXCLUDED.ppg_fantasy_points;

-- Ka'Von Franklin (2032, 2033, 2034)
INSERT INTO season_stats (
    season, player_name, player_id, team_id, position,
    rush_attempts, rush_yards, rush_tds, rush_avg, rush_ypg,
    receptions, rec_yards, rec_tds, rec_avg, rec_ypg,
    pass_yards, pass_tds,
    total_fantasy_points, ppg_fantasy_points
)
VALUES
    (2032, 'K. Franklin', 44, 11, 'HB', 190, 1018, 6, 5.4, 59.9, 15, 120, 0, 8.0, 7.1, 0, 0, 149.80, 8.81),
    (2033, 'K. Franklin', 44, 11, 'HB', 120, 650, 10, 5.4, 38.2, 20, 160, 1, 8.0, 9.4, 0, 0, 147.00, 8.65),
    (2034, 'K. Franklin', 44, 11, 'HB', 200, 896, 5, 4.5, 52.7, 58, 440, 4, 7.6, 25.9, 0, 0, 245.60, 14.45)
ON CONFLICT (season, player_name, team_id, position) DO UPDATE
SET rush_attempts = EXCLUDED.rush_attempts,
    rush_yards = EXCLUDED.rush_yards,
    rush_tds = EXCLUDED.rush_tds,
    rush_avg = EXCLUDED.rush_avg,
    rush_ypg = EXCLUDED.rush_ypg,
    receptions = EXCLUDED.receptions,
    rec_yards = EXCLUDED.rec_yards,
    rec_tds = EXCLUDED.rec_tds,
    rec_avg = EXCLUDED.rec_avg,
    rec_ypg = EXCLUDED.rec_ypg,
    total_fantasy_points = EXCLUDED.total_fantasy_points,
    ppg_fantasy_points = EXCLUDED.ppg_fantasy_points;

-- Raheem Tucker (2033)
INSERT INTO season_stats (
    season, player_name, player_id, team_id, position,
    rush_attempts, rush_yards, rush_tds, rush_avg, rush_ypg,
    receptions, rec_yards, rec_tds,
    pass_yards, pass_tds,
    total_fantasy_points, ppg_fantasy_points
)
VALUES
    (2033, 'R. Tucker', 359, 11, 'HB', 245, 1257, 9, 5.1, 73.9, 25, 180, 1, 0, 0, 203.70, 11.98)
ON CONFLICT (season, player_name, team_id, position) DO UPDATE
SET rush_attempts = EXCLUDED.rush_attempts,
    rush_yards = EXCLUDED.rush_yards,
    rush_tds = EXCLUDED.rush_tds,
    rush_avg = EXCLUDED.rush_avg,
    rush_ypg = EXCLUDED.rush_ypg,
    receptions = EXCLUDED.receptions,
    rec_yards = EXCLUDED.rec_yards,
    rec_tds = EXCLUDED.rec_tds,
    total_fantasy_points = EXCLUDED.total_fantasy_points,
    ppg_fantasy_points = EXCLUDED.ppg_fantasy_points;

COMMIT;
