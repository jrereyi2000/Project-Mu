-- Career-counter season games recorded at load time (data/audit_reports/audit-4/stats/report.md suspicion 2).
--
-- 2027_02_28's player_week_counters read the player's season games from a live LEFT JOIN on
-- season_stat_general, so a first career row's BASELINE/PARTIAL status and its games_lost changed
-- retrospectively whenever a later load wrote or grew that season's line. The loader now stores the games the
-- save's own season line gave the player with the row (`season_games`), and the view reads the row instead of
-- the live table: a status is judged by what the load knew.
--
-- Rows that predate these columns have no load-time observation at all, and an upper bound is not one
-- (audit-5 stats2b R1): the bound is kept in its own `season_games_bound` column, the row is marked
-- `LEGACY_UNKNOWN`, and the view reports it as unknown history instead of an exact BASELINE or lost count. A
-- first row with career games 0 is provable zero and stays an exact zero-game BASELINE (`LEGACY_ZERO`).
--
-- Run ./db/backup_db.sh first. Idempotent: the legacy marking below runs once, when this file creates the
-- provenance column, so a replay cannot rewrite a later load's own observation. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_02_29_stats_counter_season_games.sql

BEGIN;

-- 1. The load-time season games. NULL means the load had no season line for the player (the view reads that as
--    zero games, as an absent season_stat_general row did before).
--
--    `season_games` is only ever the loader's own observation. Rows written before these columns have none, and
--    neither the live season line (it keeps growing, so copying it freezes a later, larger total onto an earlier
--    load, audit-5 stats2 R1) nor the row's `LEAST(games_played, week)` upper bound can stand in for one
--    (audit-5 stats2b R1): career games include earlier NFL seasons and elapsed league weeks include byes and
--    DNPs, so the bound proves only a maximum. The legacy rows are therefore marked by provenance:
--      LEGACY_ZERO     a first row whose career games are 0 proves nothing was played; season_games = 0.
--      LEGACY_UNKNOWN  every other legacy row; season_games stays NULL (unknown, not zero), and a first row
--                      inside a playable week with career games keeps its bound in `season_games_bound` alone.
--    A first row outside a playable week has no per-week evidence at all and keeps a NULL bound. Rows written
--    after this migration take the column default `LOAD`, and the loader's own NULL (a save with no season line)
--    is a LOAD observation. The marking runs once, when this file creates the provenance column; a replay is a
--    no-op, so no later observation is ever rewritten.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'player_career_counters'
                     AND column_name = 'season_games_source') THEN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema = 'public' AND table_name = 'player_career_counters'
                         AND column_name = 'season_games') THEN
            ALTER TABLE player_career_counters ADD COLUMN season_games INTEGER;
        END IF;
        ALTER TABLE player_career_counters ADD COLUMN season_games_source TEXT NOT NULL DEFAULT 'LOAD';
        ALTER TABLE player_career_counters ADD COLUMN season_games_bound INTEGER;
        ALTER TABLE player_career_counters
            ADD CONSTRAINT player_career_counters_season_games_source_check
            CHECK (season_games_source IN ('LOAD', 'LEGACY_ZERO', 'LEGACY_UNKNOWN'));
        -- On a database upgraded from 2027_02_28 no row can carry a load-time value: mark them all unknown
        -- first, then give the two provable cases their exact values.
        UPDATE player_career_counters SET season_games = NULL, season_games_source = 'LEGACY_UNKNOWN';
        -- A first row inside a playable week keeps its bound, labelled as a bound and never as observation.
        UPDATE player_career_counters c
        SET season_games_bound = LEAST(c.games_played, c.week)
        WHERE c.week >= 1
          AND c.games_played > 0
          AND NOT EXISTS (SELECT 1 FROM player_career_counters p
                          WHERE p.player_id = c.player_id AND p.load_seq < c.load_seq);
        -- A first row whose career games are exactly 0 proves the season had none: an exact zero. A NULL
        -- games_played is absent evidence, not a zero, and stays unknown.
        UPDATE player_career_counters c
        SET season_games = 0, season_games_source = 'LEGACY_ZERO'
        WHERE c.games_played = 0
          AND NOT EXISTS (SELECT 1 FROM player_career_counters p
                          WHERE p.player_id = c.player_id AND p.load_seq < c.load_seq);
    END IF;
END $$;

-- 2. The same view without the join: `season_games_played` comes from the row. A legacy first row reports
--    UNKNOWN_HISTORY (no exact games, no delta) and carries its bound separately; a replay of this file never
--    changes a row it has already seen.
CREATE OR REPLACE VIEW player_week_counters AS
WITH base AS (
    SELECT c.season, c.week, c.stage, c.player_id, c.madden_table, c.counters, c.games_played, c.load_seq,
           COALESCE(c.season_games, 0) AS season_games_played,
           c.season_games_source, c.season_games_bound,
           LAG(c.season)       OVER (PARTITION BY c.player_id ORDER BY c.load_seq) AS prev_season,
           LAG(c.week)         OVER (PARTITION BY c.player_id ORDER BY c.load_seq) AS prev_week,
           LAG(c.stage)        OVER (PARTITION BY c.player_id ORDER BY c.load_seq) AS prev_stage,
           LAG(c.madden_table) OVER (PARTITION BY c.player_id ORDER BY c.load_seq) AS prev_madden_table,
           LAG(c.counters)     OVER (PARTITION BY c.player_id ORDER BY c.load_seq) AS prev_counters,
           LAG(c.games_played) OVER (PARTITION BY c.player_id ORDER BY c.load_seq) AS prev_games_played
    FROM player_career_counters c
), marked AS (
    SELECT b.*,
           CASE
               WHEN b.prev_season IS NULL AND b.season_games_source = 'LEGACY_UNKNOWN' THEN 'UNKNOWN_HISTORY'
               WHEN b.prev_season IS NULL AND b.season_games_played > 0 THEN 'PARTIAL'
               WHEN b.prev_season IS NULL THEN 'BASELINE'
               WHEN b.madden_table IS DISTINCT FROM b.prev_madden_table THEN 'TABLE_CHANGED'
               WHEN b.games_played - b.prev_games_played < 0 THEN 'RESET'
               WHEN b.games_played - b.prev_games_played = 0 THEN 'NO_GAMES'
               WHEN b.season = b.prev_season THEN 'OK'
               WHEN b.season = b.prev_season + 1 AND b.week <= 0 THEN 'OK'
               ELSE 'CROSS_SEASON'
           END AS status,
           CASE WHEN b.prev_season IS NULL AND b.season_games_source = 'LEGACY_UNKNOWN' THEN 0
                WHEN b.prev_season IS NULL THEN b.season_games_played
                ELSE b.games_played - b.prev_games_played END AS games_covered,
           CASE
               WHEN b.prev_season IS NULL THEN b.season
               WHEN b.season = b.prev_season THEN b.season
               WHEN b.season = b.prev_season + 1 AND b.week <= 0 THEN b.prev_season
               ELSE LEAST(b.season, b.prev_season)
           END AS credited_season
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
       (delta ->> 'DOWNSPLAYED')::int            AS downs_played,
       stage, prev_stage, load_seq, credited_season, season_games_bound
FROM deltas;

-- 3. The season view counts an UNKNOWN_HISTORY first row as an unknown week (never as exact lost games) and
--    carries its labelled bound beside it. `games_lost` stays the exact PARTIAL count only.
CREATE OR REPLACE VIEW player_season_counters AS
WITH delta_rows AS (
    SELECT credited_season AS season, player_id, delta
    FROM player_week_counters
    WHERE status IN ('OK', 'NO_GAMES') AND delta IS NOT NULL
), summed AS (
    SELECT season, player_id, jsonb_object_agg(k, total) AS delta
    FROM (
        SELECT season, player_id, e.key AS k, SUM(e.value::int) AS total
        FROM delta_rows, LATERAL jsonb_each_text(delta_rows.delta) AS e(key, value)
        GROUP BY season, player_id, e.key
    ) parts
    GROUP BY season, player_id
), coverage AS (
    SELECT credited_season AS season, player_id,
           COALESCE(SUM(games_covered) FILTER (WHERE status IN ('OK', 'NO_GAMES')), 0) AS games_covered,
           COUNT(*) FILTER (WHERE status IN ('TABLE_CHANGED', 'RESET', 'CROSS_SEASON',
                                             'UNKNOWN_HISTORY')) AS weeks_unknown,
           COUNT(*) FILTER (WHERE status = 'PARTIAL') AS weeks_partial,
           COALESCE(SUM(games_covered) FILTER (WHERE status = 'PARTIAL'), 0) AS games_lost,
           COUNT(*) FILTER (WHERE status = 'UNKNOWN_HISTORY') AS weeks_history_unknown,
           MAX(season_games_bound) FILTER (WHERE status = 'UNKNOWN_HISTORY') AS games_history_bound,
           COUNT(*) FILTER (WHERE status = 'NO_GAMES') AS weeks_no_games
    FROM player_week_counters
    GROUP BY credited_season, player_id
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
       (s.delta ->> 'DOWNSPLAYED')::int            AS downs_played,
       c.weeks_partial, c.games_lost, c.weeks_no_games,
       c.weeks_history_unknown, c.games_history_bound
FROM coverage c
LEFT JOIN summed s ON s.season = c.season AND s.player_id = c.player_id;

COMMIT;
