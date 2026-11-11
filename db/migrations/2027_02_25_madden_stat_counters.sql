-- Madden stat counters (docs/done/MADDEN_STATS_INGEST_PLAN.md U2, §2.2): the six counters the save reader
-- already copies and the loader used to drop, plus Madden's career totals stored at every weekly load. Only
-- differences between career rows mean anything (the totals start from real-NFL history), so the views turn them
-- into per-week and per-season deltas with a status where the delta is unknown (a career-table change or a reset).
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_02_25_madden_stat_counters.sql

BEGIN;

-- 1. The six dropped Game / Season counters.
ALTER TABLE player_stats         ADD COLUMN IF NOT EXISTS rec_drops INT, ADD COLUMN IF NOT EXISTS rush_20plus INT;
ALTER TABLE season_stats         ADD COLUMN IF NOT EXISTS rec_drops INT, ADD COLUMN IF NOT EXISTS rush_20plus INT;
ALTER TABLE game_stat_defense    ADD COLUMN IF NOT EXISTS big_hits INT, ADD COLUMN IF NOT EXISTS catches_allowed INT;
ALTER TABLE season_stat_defense  ADD COLUMN IF NOT EXISTS big_hits INT, ADD COLUMN IF NOT EXISTS catches_allowed INT;
ALTER TABLE game_stat_blocking   ADD COLUMN IF NOT EXISTS pancakes INT;
ALTER TABLE season_stat_blocking ADD COLUMN IF NOT EXISTS pancakes INT;
ALTER TABLE game_stat_general    ADD COLUMN IF NOT EXISTS games_started INT;
ALTER TABLE season_stat_general  ADD COLUMN IF NOT EXISTS games_started INT;

-- 2. Madden's career totals at each weekly load (raw Career*Stats fields). Only differences between rows mean anything.
CREATE TABLE IF NOT EXISTS player_career_counters (
    season       INT NOT NULL,
    week         INT NOT NULL,              -- current_week(snapshot); 0 = any load outside the regular season/playoffs
    player_id    INT NOT NULL REFERENCES players(id),
    madden_table VARCHAR(40) NOT NULL,      -- CareerOffensiveStats, CareerDefensiveKPReturnStats, ...
    counters     JSONB NOT NULL,            -- {Madden field: int}, every numeric field on the row
    games_played INT,                       -- counters->GAMESPLAYED, pulled out for the views
    capture_id   INT REFERENCES captures(id) ON DELETE SET NULL,
    read_at      TIMESTAMPTZ,               -- snapshot meta.read_at
    PRIMARY KEY (season, week, player_id));
CREATE INDEX IF NOT EXISTS player_career_counters_player ON player_career_counters (player_id, season, week);

-- 3. Per-key difference: every key in cur minus the previous row's value (0 when the previous row lacks the key).
--    Negative values are kept: a legitimately shrinking counter is not a reset (the views decide that from the
--    career table and GAMESPLAYED, not from the counters themselves).
CREATE OR REPLACE FUNCTION counter_delta(cur jsonb, prev jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $$
    SELECT COALESCE(jsonb_object_agg(k, (cur ->> k)::int - COALESCE((prev ->> k)::int, 0)), '{}'::jsonb)
    FROM jsonb_object_keys(cur) AS k
$$;

-- 4. One row per career-counter row, with the previous row's position and a status:
--    BASELINE (no previous row), TABLE_CHANGED (the career row moved table, so the counter set restarted),
--    RESET (GAMESPLAYED went down), NO_GAMES (no game between the two loads) or OK. `delta` exists only for OK
--    and NO_GAMES; typed columns are NULL when the field is absent from the row's career table.
CREATE OR REPLACE VIEW player_week_counters AS
WITH base AS (
    SELECT c.season, c.week, c.player_id, c.madden_table, c.counters, c.games_played,
           LAG(c.season)       OVER (PARTITION BY c.player_id ORDER BY c.season, c.week) AS prev_season,
           LAG(c.week)         OVER (PARTITION BY c.player_id ORDER BY c.season, c.week) AS prev_week,
           LAG(c.madden_table) OVER (PARTITION BY c.player_id ORDER BY c.season, c.week) AS prev_madden_table,
           LAG(c.counters)     OVER (PARTITION BY c.player_id ORDER BY c.season, c.week) AS prev_counters,
           LAG(c.games_played) OVER (PARTITION BY c.player_id ORDER BY c.season, c.week) AS prev_games_played
    FROM player_career_counters c
), marked AS (
    SELECT b.*,
           CASE
               WHEN b.prev_season IS NULL THEN 'BASELINE'
               WHEN b.madden_table IS DISTINCT FROM b.prev_madden_table THEN 'TABLE_CHANGED'
               WHEN b.games_played - b.prev_games_played < 0 THEN 'RESET'
               WHEN b.games_played - b.prev_games_played = 0 THEN 'NO_GAMES'
               ELSE 'OK'
           END AS status,
           b.games_played - b.prev_games_played AS games_covered
    FROM base b
), deltas AS (
    SELECT m.*,
           CASE WHEN m.status IN ('OK', 'NO_GAMES') THEN counter_delta(m.counters, m.prev_counters) END AS delta
    FROM marked m
)
SELECT season, week, player_id, madden_table, prev_season, prev_week, games_covered, status, delta,
       (delta ->> 'TARGETS')::int                AS targets,
       (delta ->> 'ROUTESRUN')::int              AS madden_routes,
       (delta ->> 'AIRYARDS')::int               AS air_yards,
       (delta ->> 'PRESSURES')::int              AS pressures_faced,
       (delta ->> 'SEPARATION_TIGHT')::int       AS sep_tight,
       (delta ->> 'SEPARATION_NORMAL')::int      AS sep_normal,
       (delta ->> 'SEPARATION_OPEN')::int        AS sep_open,
       (delta ->> 'SEPARATION_WIDEOPEN')::int    AS sep_wide_open,
       (delta ->> 'SHORTPASS_ATTEMPTS')::int     AS short_att,
       (delta ->> 'SHORTPASS_COMPLETIONS')::int  AS short_cmp,
       (delta ->> 'SHORTPASS_YARDS')::int        AS short_yds,
       (delta ->> 'SHORTPASS_TD')::int           AS short_td,
       (delta ->> 'SHORTPASS_INTERCEPTIONS')::int AS short_int,
       (delta ->> 'MEDIUMPASS_ATTEMPTS')::int    AS medium_att,
       (delta ->> 'MEDIUMPASS_COMPLETIONS')::int AS medium_cmp,
       (delta ->> 'MEDIUMPASS_YARDS')::int       AS medium_yds,
       (delta ->> 'MEDIUMPASS_TD')::int          AS medium_td,
       (delta ->> 'MEDIUMPASS_INTERCEPTIONS')::int AS medium_int,
       (delta ->> 'DEEPPASS_ATTEMPTS')::int      AS deep_att,
       (delta ->> 'DEEPPASS_COMPLETIONS')::int   AS deep_cmp,
       (delta ->> 'DEEPPASS_YARDS')::int         AS deep_yds,
       (delta ->> 'DEEPPASS_TD')::int            AS deep_td,
       (delta ->> 'DEEPPASS_INTERCEPTIONS')::int AS deep_int,
       (delta ->> 'INSIDEPOCKETTHROW_ATTEMPTS')::int    AS pocket_in_att,
       (delta ->> 'INSIDEPOCKETTHROW_COMPLETIONS')::int AS pocket_in_cmp,
       (delta ->> 'INSIDEPOCKETTHROW_YARDS')::int       AS pocket_in_yds,
       (delta ->> 'INSIDEPOCKETTHROW_TD')::int          AS pocket_in_td,
       (delta ->> 'INSIDEPOCKETTHROW_INTERCEPTIONS')::int AS pocket_in_int,
       (delta ->> 'OUTSIDEPOCKETTHROW_ATTEMPTS')::int    AS pocket_out_att,
       (delta ->> 'OUTSIDEPOCKETTHROW_COMPLETIONS')::int AS pocket_out_cmp,
       (delta ->> 'OUTSIDEPOCKETTHROW_YARDS')::int       AS pocket_out_yds,
       (delta ->> 'OUTSIDEPOCKETTHROW_TD')::int          AS pocket_out_td,
       (delta ->> 'OUTSIDEPOCKETTHROW_INTERCEPTIONS')::int AS pocket_out_int,
       (delta ->> 'BLITZ_PLAYS')::int            AS blitz_plays,
       (delta ->> 'BLITZ_COMPLETIONS')::int      AS blitz_cmp,
       (delta ->> 'BLITZ_YARDS')::int            AS blitz_yds,
       (delta ->> 'BLITZ_TD')::int               AS blitz_td,
       (delta ->> 'BLITZ_INTERCEPTIONS')::int    AS blitz_int,
       (delta ->> 'QBSCRAMBLE_CARRIES')::int     AS scramble_att,
       (delta ->> 'QBSCRAMBLE_YARDS')::int       AS scramble_yds,
       (delta ->> 'QBSCRAMBLE_TD')::int          AS scramble_td,
       (delta ->> 'THROWLOCATION_LEFT')::int     AS throw_left,
       (delta ->> 'THROWLOCATION_MIDDLE')::int   AS throw_middle,
       (delta ->> 'THROWLOCATION_RIGHT')::int    AS throw_right,
       (delta ->> 'RUSHSUCCESS')::int            AS rush_success,
       (delta ->> 'RUSH10YARDRUNS')::int         AS rush_10plus,
       (delta ->> 'LIGHTBOX')::int               AS light_box,
       (delta ->> 'LOADEDBOX')::int              AS loaded_box,
       (delta ->> 'DEFTARGETS')::int             AS def_targets,
       (delta ->> 'CTHALLOWED')::int             AS def_catches_allowed,
       (delta ->> 'DEFPRESSURES')::int           AS def_pressures,
       (delta ->> 'STOPS')::int                  AS stops,
       (delta ->> 'STUFFS')::int                 AS stuffs,
       (delta ->> 'DOWNSPLAYED')::int            AS downs_played
FROM deltas;

-- 5. One row per (season, player): the OK week deltas summed key by key, the games those weeks covered, and how
--    many weeks of the season had an unknown delta (a table change or a reset).
CREATE OR REPLACE VIEW player_season_counters AS
WITH ok_deltas AS (
    SELECT season, player_id, delta
    FROM player_week_counters
    WHERE status = 'OK' AND delta IS NOT NULL
), summed AS (
    SELECT season, player_id, jsonb_object_agg(k, total) AS delta
    FROM (
        SELECT season, player_id, e.key AS k, SUM(e.value::int) AS total
        FROM ok_deltas, LATERAL jsonb_each_text(ok_deltas.delta) AS e(key, value)
        GROUP BY season, player_id, e.key
    ) parts
    GROUP BY season, player_id
), coverage AS (
    SELECT season, player_id,
           COALESCE(SUM(games_covered) FILTER (WHERE status = 'OK'), 0) AS games_covered,
           COUNT(*) FILTER (WHERE status IN ('TABLE_CHANGED', 'RESET')) AS weeks_unknown
    FROM player_week_counters
    GROUP BY season, player_id
)
SELECT c.season, c.player_id, s.delta, c.games_covered, c.weeks_unknown,
       (s.delta ->> 'TARGETS')::int                AS targets,
       (s.delta ->> 'ROUTESRUN')::int              AS madden_routes,
       (s.delta ->> 'AIRYARDS')::int               AS air_yards,
       (s.delta ->> 'PRESSURES')::int              AS pressures_faced,
       (s.delta ->> 'SEPARATION_TIGHT')::int       AS sep_tight,
       (s.delta ->> 'SEPARATION_NORMAL')::int      AS sep_normal,
       (s.delta ->> 'SEPARATION_OPEN')::int        AS sep_open,
       (s.delta ->> 'SEPARATION_WIDEOPEN')::int    AS sep_wide_open,
       (s.delta ->> 'SHORTPASS_ATTEMPTS')::int     AS short_att,
       (s.delta ->> 'SHORTPASS_COMPLETIONS')::int  AS short_cmp,
       (s.delta ->> 'SHORTPASS_YARDS')::int        AS short_yds,
       (s.delta ->> 'SHORTPASS_TD')::int           AS short_td,
       (s.delta ->> 'SHORTPASS_INTERCEPTIONS')::int AS short_int,
       (s.delta ->> 'MEDIUMPASS_ATTEMPTS')::int    AS medium_att,
       (s.delta ->> 'MEDIUMPASS_COMPLETIONS')::int AS medium_cmp,
       (s.delta ->> 'MEDIUMPASS_YARDS')::int       AS medium_yds,
       (s.delta ->> 'MEDIUMPASS_TD')::int          AS medium_td,
       (s.delta ->> 'MEDIUMPASS_INTERCEPTIONS')::int AS medium_int,
       (s.delta ->> 'DEEPPASS_ATTEMPTS')::int      AS deep_att,
       (s.delta ->> 'DEEPPASS_COMPLETIONS')::int   AS deep_cmp,
       (s.delta ->> 'DEEPPASS_YARDS')::int         AS deep_yds,
       (s.delta ->> 'DEEPPASS_TD')::int            AS deep_td,
       (s.delta ->> 'DEEPPASS_INTERCEPTIONS')::int AS deep_int,
       (s.delta ->> 'INSIDEPOCKETTHROW_ATTEMPTS')::int    AS pocket_in_att,
       (s.delta ->> 'INSIDEPOCKETTHROW_COMPLETIONS')::int AS pocket_in_cmp,
       (s.delta ->> 'INSIDEPOCKETTHROW_YARDS')::int       AS pocket_in_yds,
       (s.delta ->> 'INSIDEPOCKETTHROW_TD')::int          AS pocket_in_td,
       (s.delta ->> 'INSIDEPOCKETTHROW_INTERCEPTIONS')::int AS pocket_in_int,
       (s.delta ->> 'OUTSIDEPOCKETTHROW_ATTEMPTS')::int    AS pocket_out_att,
       (s.delta ->> 'OUTSIDEPOCKETTHROW_COMPLETIONS')::int AS pocket_out_cmp,
       (s.delta ->> 'OUTSIDEPOCKETTHROW_YARDS')::int       AS pocket_out_yds,
       (s.delta ->> 'OUTSIDEPOCKETTHROW_TD')::int          AS pocket_out_td,
       (s.delta ->> 'OUTSIDEPOCKETTHROW_INTERCEPTIONS')::int AS pocket_out_int,
       (s.delta ->> 'BLITZ_PLAYS')::int            AS blitz_plays,
       (s.delta ->> 'BLITZ_COMPLETIONS')::int      AS blitz_cmp,
       (s.delta ->> 'BLITZ_YARDS')::int            AS blitz_yds,
       (s.delta ->> 'BLITZ_TD')::int               AS blitz_td,
       (s.delta ->> 'BLITZ_INTERCEPTIONS')::int    AS blitz_int,
       (s.delta ->> 'QBSCRAMBLE_CARRIES')::int     AS scramble_att,
       (s.delta ->> 'QBSCRAMBLE_YARDS')::int       AS scramble_yds,
       (s.delta ->> 'QBSCRAMBLE_TD')::int          AS scramble_td,
       (s.delta ->> 'THROWLOCATION_LEFT')::int     AS throw_left,
       (s.delta ->> 'THROWLOCATION_MIDDLE')::int   AS throw_middle,
       (s.delta ->> 'THROWLOCATION_RIGHT')::int    AS throw_right,
       (s.delta ->> 'RUSHSUCCESS')::int            AS rush_success,
       (s.delta ->> 'RUSH10YARDRUNS')::int         AS rush_10plus,
       (s.delta ->> 'LIGHTBOX')::int               AS light_box,
       (s.delta ->> 'LOADEDBOX')::int              AS loaded_box,
       (s.delta ->> 'DEFTARGETS')::int             AS def_targets,
       (s.delta ->> 'CTHALLOWED')::int             AS def_catches_allowed,
       (s.delta ->> 'DEFPRESSURES')::int           AS def_pressures,
       (s.delta ->> 'STOPS')::int                  AS stops,
       (s.delta ->> 'STUFFS')::int                 AS stuffs,
       (s.delta ->> 'DOWNSPLAYED')::int            AS downs_played
FROM coverage c
LEFT JOIN summed s ON s.season = c.season AND s.player_id = c.player_id;

-- 6. The new counters appended to the line views (existing readers keep their columns; the six go at the end).
CREATE OR REPLACE VIEW season_stat_lines AS
SELECT ps.season, ps.player_id, pl.player_name, pl.full_name, ps.team_id, ps.position,
       g.games_played, g.downs_played,
       ss.completions, ss.pass_attempts, ss.pass_yards, ss.pass_tds, ss.interceptions, ss.pass_comp_pct, ss.pass_ypg,
       ss.pass_rating, ss.pass_ypa, ss.pass_long, ss.pass_sacks,
       ss.rush_attempts, ss.rush_yards, ss.rush_tds, ss.fumb, ss.rush_avg, ss.rush_ypg, ss.rush_btk, ss.rush_yac, ss.rush_long,
       ss.receptions, ss.rec_yards, ss.rec_tds, ss.rec_avg, ss.rec_ypg, ss.rec_rac, ss.rec_rac_avg, ss.rec_long,
       b.sacks_allowed AS blk_sacks_allowed,
       d.solo AS def_solo, d.assists AS def_assists, d.tackles AS def_tackles, d.tfl AS def_tfl, d.sacks AS def_sacks,
       d.ints AS def_ints, d.int_yds AS def_int_yds, d.int_avg AS def_int_avg, d.int_long AS def_int_long,
       d.deflections AS def_deflections, d.forced_fumbles AS def_forced_fumbles, d.fumble_recoveries AS def_fumble_recoveries,
       d.fumble_yds AS def_fumble_yds, d.blocks AS def_blocks, d.safeties AS def_safeties, d.tds AS def_tds,
       k.fgm AS kick_fgm, k.fga AS kick_fga, k.fg_pct AS kick_fg_pct, k.fg_long AS kick_fg_long, k.fg_blocked AS kick_fg_blocked,
       k.xpm AS kick_xpm, k.xpa AS kick_xpa, k.xp_pct AS kick_xp_pct, k.xp_blocked AS kick_xp_blocked,
       k.fgm_29 AS kick_fgm_29, k.fga_29 AS kick_fga_29, k.fgm_39 AS kick_fgm_39, k.fga_39 AS kick_fga_39,
       k.fgm_49 AS kick_fgm_49, k.fga_49 AS kick_fga_49, k.fgm_50 AS kick_fgm_50, k.fga_50 AS kick_fga_50,
       k.kickoffs AS kick_kickoffs, k.touchbacks AS kick_touchbacks,
       p.punts AS punt_punts, p.yards AS punt_yards, p.average AS punt_average, p.net_yards AS punt_net_yards,
       p.net_average AS punt_net_average, p.blocked AS punt_blocked, p.inside_20 AS punt_inside_20,
       p.touchbacks AS punt_touchbacks, p.longest AS punt_longest,
       kr.returns AS kr_returns, kr.yards AS kr_yards, kr.average AS kr_average, kr.tds AS kr_tds, kr.longest AS kr_longest,
       pr.returns AS pr_returns, pr.yards AS pr_yards, pr.average AS pr_average, pr.tds AS pr_tds, pr.longest AS pr_longest,
       ps.source,
       ss.rec_drops, ss.rush_20plus, d.big_hits AS def_big_hits, d.catches_allowed AS def_catches_allowed,
       b.pancakes AS blk_pancakes, g.games_started
FROM player_seasons ps
JOIN players pl ON pl.id = ps.player_id
LEFT JOIN season_stats ss            ON ss.season = ps.season AND ss.player_id = ps.player_id
LEFT JOIN season_stat_general g      ON g.season = ps.season AND g.player_id = ps.player_id
LEFT JOIN season_stat_blocking b     ON b.season = ps.season AND b.player_id = ps.player_id
LEFT JOIN season_stat_defense d      ON d.season = ps.season AND d.player_id = ps.player_id
LEFT JOIN season_stat_kicking k      ON k.season = ps.season AND k.player_id = ps.player_id
LEFT JOIN season_stat_punting p      ON p.season = ps.season AND p.player_id = ps.player_id
LEFT JOIN season_stat_kickreturn kr  ON kr.season = ps.season AND kr.player_id = ps.player_id
LEFT JOIN season_stat_puntreturn pr  ON pr.season = ps.season AND pr.player_id = ps.player_id;

CREATE OR REPLACE VIEW game_stat_lines AS
WITH ids AS (
    SELECT game_id, player_id FROM player_stats WHERE player_id IS NOT NULL
    UNION SELECT game_id, player_id FROM game_stat_general
    UNION SELECT game_id, player_id FROM game_stat_blocking
    UNION SELECT game_id, player_id FROM game_stat_defense
    UNION SELECT game_id, player_id FROM game_stat_kicking
    UNION SELECT game_id, player_id FROM game_stat_punting
    UNION SELECT game_id, player_id FROM game_stat_kickreturn
    UNION SELECT game_id, player_id FROM game_stat_puntreturn)
SELECT ids.game_id, gm.season, gm.week, ids.player_id, pl.player_name, pl.full_name,
       COALESCE(s.team_id, g.team_id, d.team_id, k.team_id, p.team_id, kr.team_id, pr.team_id, b.team_id) AS team_id,
       g.downs_played,
       s.completions, s.pass_attempts, s.pass_yards, s.pass_tds, s.interceptions, s.pass_comp_pct, s.pass_rating,
       s.pass_ypa, s.pass_long, s.pass_sacks,
       s.rush_attempts, s.rush_yards, s.rush_tds, s.fumb, s.rush_avg, s.rush_btk, s.rush_yac, s.rush_long,
       s.receptions, s.rec_yards, s.rec_tds, s.rec_avg, s.rec_rac, s.rec_rac_avg, s.rec_long, s.fantasy_points,
       b.sacks_allowed AS blk_sacks_allowed,
       d.solo AS def_solo, d.assists AS def_assists, d.tackles AS def_tackles, d.tfl AS def_tfl, d.sacks AS def_sacks,
       d.ints AS def_ints, d.int_yds AS def_int_yds, d.int_avg AS def_int_avg, d.int_long AS def_int_long,
       d.deflections AS def_deflections, d.forced_fumbles AS def_forced_fumbles, d.fumble_recoveries AS def_fumble_recoveries,
       d.fumble_yds AS def_fumble_yds, d.blocks AS def_blocks, d.safeties AS def_safeties, d.tds AS def_tds,
       k.fgm AS kick_fgm, k.fga AS kick_fga, k.fg_pct AS kick_fg_pct, k.fg_long AS kick_fg_long, k.fg_blocked AS kick_fg_blocked,
       k.xpm AS kick_xpm, k.xpa AS kick_xpa, k.xp_pct AS kick_xp_pct, k.xp_blocked AS kick_xp_blocked,
       k.fgm_29 AS kick_fgm_29, k.fga_29 AS kick_fga_29, k.fgm_39 AS kick_fgm_39, k.fga_39 AS kick_fga_39,
       k.fgm_49 AS kick_fgm_49, k.fga_49 AS kick_fga_49, k.fgm_50 AS kick_fgm_50, k.fga_50 AS kick_fga_50,
       k.kickoffs AS kick_kickoffs, k.touchbacks AS kick_touchbacks,
       p.punts AS punt_punts, p.yards AS punt_yards, p.average AS punt_average, p.net_yards AS punt_net_yards,
       p.net_average AS punt_net_average, p.blocked AS punt_blocked, p.inside_20 AS punt_inside_20,
       p.touchbacks AS punt_touchbacks, p.longest AS punt_longest,
       kr.returns AS kr_returns, kr.yards AS kr_yards, kr.average AS kr_average, kr.tds AS kr_tds, kr.longest AS kr_longest,
       pr.returns AS pr_returns, pr.yards AS pr_yards, pr.average AS pr_average, pr.tds AS pr_tds, pr.longest AS pr_longest,
       s.rec_drops, s.rush_20plus, d.big_hits AS def_big_hits, d.catches_allowed AS def_catches_allowed,
       b.pancakes AS blk_pancakes, g.games_started
FROM ids
JOIN games gm ON gm.id = ids.game_id
JOIN players pl ON pl.id = ids.player_id
LEFT JOIN player_stats s           ON s.game_id = ids.game_id AND s.player_id = ids.player_id
LEFT JOIN game_stat_general g      ON g.game_id = ids.game_id AND g.player_id = ids.player_id
LEFT JOIN game_stat_blocking b     ON b.game_id = ids.game_id AND b.player_id = ids.player_id
LEFT JOIN game_stat_defense d      ON d.game_id = ids.game_id AND d.player_id = ids.player_id
LEFT JOIN game_stat_kicking k      ON k.game_id = ids.game_id AND k.player_id = ids.player_id
LEFT JOIN game_stat_punting p      ON p.game_id = ids.game_id AND p.player_id = ids.player_id
LEFT JOIN game_stat_kickreturn kr  ON kr.game_id = ids.game_id AND kr.player_id = ids.player_id
LEFT JOIN game_stat_puntreturn pr  ON pr.game_id = ids.game_id AND pr.player_id = ids.player_id;

COMMIT;
