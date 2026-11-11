-- 2027_02_26_league_shape_season.sql
-- The league keeps the shape of a reference season, stored once per league (JR 2026-11-11, board M-0414).
--
-- engines/progression.run_progression used to calibrate the league-shape terms against the module constant 2036
-- (LEAGUE_SHAPE_SEASON). A 2026-era league has no 2036 rows, so the target was empty (0 players / 0 at 90+ / mean
-- 0) and every offseason picked development 0.0 and star -1.00. The reference season is now read from
-- league_state.shape_season: the league's own start season for a league bootstrapped into 2026, and still 2036 for
-- the frozen 2036 archive.
ALTER TABLE league_state ADD COLUMN IF NOT EXISTS shape_season INT;

-- Backfill: a league holds its first season's shape unless it is the 2036 archive (below).
UPDATE league_state
SET shape_season = COALESCE((SELECT min(season) FROM player_seasons), current_season)
WHERE shape_season IS NULL;

-- madden_2036 keeps the 2036 shape (JR, audit-2 chat 2026-11-11), and so do its clones: madden_test,
-- madden_v3_eval_20261030, madden_tt_replay and madden_tt_voice. Their league_state.current_season is 2036, so the
-- rule is stated by state, never by database name; without it the backfill above would pick madden_2036's 2035.
UPDATE league_state SET shape_season = 2036 WHERE current_season = 2036;
