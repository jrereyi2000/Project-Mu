-- Migration: 2026_12_27_career_fit_fixes.sql
-- Description: three stored-data fixes found by the first Career Stats load (docs/HISTORY_BACKFILL_PLAN.md phase 1,
-- verify_output/history_remainders_2036.md). Every UPDATE matches the old values, so a second run changes nothing.
--
--   1) Carl Alonso (1127), 2035: the Week 18 capture (2026-10-15_2009) read Justin Alonso's unchanged receiving row
--      (25-73, RAC 85, DP 750; J. Alonso already had it at Week 17, 2026-10-13_1413) as "C.Alonso", so the Week 18
--      load gave Carl a one-game +16 catches, +27 yards (WEEKLY line) and a 2035 line of 25-73. His career list row
--      has 16 catches in all. Restore his season line to what it was before Week 18 (9-46: the distributed lines plus
--      his captured Week 17 line), zero the fake Week 18 delta, and remove the 16-catch game from the league's
--      receptions-in-a-game top 10. RAC, long and the averages built on J. Alonso's row become unknown / recomputed.
--   2) Ka'Von Franklin (44), 2032-2033: the seeded Panthers lines (2026_11_29_panthers_history_backfill.sql) were
--      29 carries, 273 rushing yards, 38 receiving yards and 1 receiving TD above his Madden career, and 2 rushing TDs
--      and 3 catches below it. 2032 and 2033 now sum, with the untouched 2034 line (it has captured game lines), to
--      exactly his career minus 2035-2036: 481-2,291-23 rushing, 96-682-4 receiving. Averages and fantasy points
--      (0.1 a yard, 6 a TD, as seeded) follow.
--   3) Nathan Bayless (601): 2036 position stored as 'OB', an OCR misread of QB (his 2035 row and the career lists).

BEGIN;

-- 1) Carl Alonso, 2035
UPDATE season_stats
SET receptions = 9, rec_yards = 46, rec_tds = 0, rec_avg = 5.1, rec_ypg = 2.9,
    rec_rac = NULL, rec_rac_avg = NULL, rec_long = NULL,
    total_fantasy_points = 61.20, ppg_fantasy_points = 4.08
WHERE id = 1142 AND player_id = 1127 AND season = 2035 AND receptions = 25 AND rec_yards = 73;

UPDATE player_stats
SET receptions = 0, rec_yards = 0       -- fantasy_points is a generated column
WHERE id = 16605 AND player_id = 1127 AND game_id = 883 AND receptions = 16 AND rec_yards = 27;

DELETE FROM league_record_entries
WHERE id = 724 AND player_id = 1127 AND category = 'game_receptions' AND game_id = 883;

-- 2) Ka'Von Franklin, 2032-2033
UPDATE season_stats
SET rush_attempts = 170, rush_yards = 800, rush_tds = 8, rush_avg = 4.7, rush_ypg = 47.1,
    receptions = 15, rec_yards = 92, rec_tds = 0, rec_avg = 6.1, rec_ypg = 5.4,
    total_fantasy_points = 137.20, ppg_fantasy_points = 8.07
WHERE id = 4133 AND player_id = 44 AND season = 2032 AND rush_attempts = 190 AND rush_yards = 1018;

UPDATE season_stats
SET rush_attempts = 111, rush_yards = 595, rush_tds = 10, rush_avg = 5.4, rush_ypg = 35.0,
    receptions = 23, rec_yards = 150, rec_tds = 0, rec_avg = 6.5, rec_ypg = 8.8,
    total_fantasy_points = 134.50, ppg_fantasy_points = 7.91
WHERE id = 4134 AND player_id = 44 AND season = 2033 AND rush_attempts = 120 AND rush_yards = 650;

-- 3) Nathan Bayless
UPDATE player_seasons SET position = 'QB' WHERE player_id = 601 AND position = 'OB';
UPDATE season_stats SET position = 'QB' WHERE player_id = 601 AND position = 'OB';
UPDATE player_stats SET position = 'QB' WHERE player_id = 601 AND position = 'OB';

COMMIT;
