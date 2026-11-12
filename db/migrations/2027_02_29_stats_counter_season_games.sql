-- Career-counter season games recorded at load time (data/audit_reports/audit-4/stats/report.md suspicion 2).
--
-- 2027_02_28's player_week_counters read the player's season games from a live LEFT JOIN on
-- season_stat_general, so a first career row's BASELINE/PARTIAL status and its games_lost changed
-- retrospectively whenever a later load wrote or grew that season's line. The loader now stores the games the
-- save's own season line gave the player with the row (`season_games`), and the view reads the row instead of
-- the live table: a status is judged by what the load knew.
--
-- Run ./db/backup_db.sh first. Idempotent: the backfill below runs once, when this file creates the column, so a
-- replay cannot rewrite a later load's own observation. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_02_29_stats_counter_season_games.sql

BEGIN;

-- 1. The load-time season games. NULL means the load had no season line for the player (the view reads that as
--    zero games, as an absent season_stat_general row did before).
--
--    Rows written before this migration have no load-time value, and the live season line is not one: it keeps
--    growing, so copying it would freeze a later, larger total onto an earlier load and invent lost games
--    (audit-5 stats2 R1). The backfill therefore runs only when this migration creates the column, and it uses
--    only what the row itself proves: `games_played` is the player's career games at that load and `week` is
--    the last league week the row covers, so a player's first row can have lost at most LEAST(games_played,
--    week) games of its season. A first row outside a playable week (PRESEASON week 0, OFFSEASON week -1,
--    PROBOWL week -2) has no per-week evidence that any game was played and stays a zero-game BASELINE, and a
--    row that is not a player's first row is never read for it, so both are left NULL rather than given a
--    number the row cannot support. NULLs written after the migration are the loader's own observations (a save
--    with no season line for the player) and are never rewritten, so replaying this file changes nothing.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'player_career_counters'
                     AND column_name = 'season_games') THEN
        ALTER TABLE player_career_counters ADD COLUMN season_games INTEGER;
        UPDATE player_career_counters c
        SET season_games = LEAST(c.games_played, c.week)
        WHERE c.season_games IS NULL
          AND c.week >= 1
          AND c.games_played > 0
          AND NOT EXISTS (SELECT 1 FROM player_career_counters p
                          WHERE p.player_id = c.player_id AND p.load_seq < c.load_seq);
    END IF;
END $$;

-- 2. The same view without the join: `season_games_played` comes from the row.
CREATE OR REPLACE VIEW player_week_counters AS
WITH base AS (
    SELECT c.season, c.week, c.stage, c.player_id, c.madden_table, c.counters, c.games_played, c.load_seq,
           COALESCE(c.season_games, 0) AS season_games_played,
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
               WHEN b.prev_season IS NULL AND b.season_games_played > 0 THEN 'PARTIAL'
               WHEN b.prev_season IS NULL THEN 'BASELINE'
               WHEN b.madden_table IS DISTINCT FROM b.prev_madden_table THEN 'TABLE_CHANGED'
               WHEN b.games_played - b.prev_games_played < 0 THEN 'RESET'
               WHEN b.games_played - b.prev_games_played = 0 THEN 'NO_GAMES'
               WHEN b.season = b.prev_season THEN 'OK'
               WHEN b.season = b.prev_season + 1 AND b.week <= 0 THEN 'OK'
               ELSE 'CROSS_SEASON'
           END AS status,
           CASE WHEN b.prev_season IS NULL THEN b.season_games_played
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
       stage, prev_stage, load_seq, credited_season
FROM deltas;

COMMIT;
