-- Stats loader weeks and load order (data/audit_reports/stats_ingest/report.md H1/H2/L2/L4).
--
-- H1: a career-counter row's `week` is now the last played week whose games the delta covers (the current
--     week minus one in the regular season, the previous played round in the playoffs), not the save's current
--     week. Off-season and Pro Bowl loads keep a stage marker and a sentinel week (see below) so they never
--     collide with a real week.
-- H2: deltas are ordered by load sequence (`load_seq`, from a database sequence) instead of (season, week), and
--     a delta that spans a season boundary is credited to the earlier season (or flagged CROSS_SEASON), never
--     silently to the later one. `stage` keeps the preseason baseline, a regular-season week 0 and the
--     offseason/Pro Bowl loads at distinct keys.
-- L2: a first career row now reports how many games it lost: PARTIAL when the player's season row says games
--     were played, BASELINE when it says none. The season view exposes `weeks_partial` and `games_lost`.
-- L4: NO_GAMES deltas (career counters that grew while GAMESPLAYED did not) stay in the season total and are
--     counted by `weeks_no_games`.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_02_28_stats_loader_weeks.sql

BEGIN;

-- 1. Load order and stage. `stage` values: WEEK (regular season and playoffs), PRESEASON (week 0 baseline),
--    OFFSEASON (week -1), PROBOWL (week -2). Negative weeks are sentinels: no stored game has that week, so a
--    stage row can never collide with a real week. `load_seq` orders a player's rows: every load takes one
--    value, and a reload of an existing key moves that row to the end (it is a new observation).
ALTER TABLE player_career_counters ADD COLUMN IF NOT EXISTS stage TEXT;
ALTER TABLE player_career_counters ADD COLUMN IF NOT EXISTS load_seq BIGINT;
CREATE SEQUENCE IF NOT EXISTS player_career_counters_load_seq;

-- Backfill rows written before this migration. They cannot be told apart (the old loader used week 0 for both
-- the preseason and every off-season load), so week 0 is marked PRESEASON and every other week WEEK. The old
-- rows remain recomputable from `counters`; a real database has no 2027_02_25 rows yet.
UPDATE player_career_counters SET stage = 'PRESEASON' WHERE stage IS NULL AND week = 0;
UPDATE player_career_counters SET stage = 'WEEK' WHERE stage IS NULL;
WITH ordered AS (
    SELECT ctid, row_number() OVER (ORDER BY read_at NULLS FIRST, capture_id NULLS FIRST,
                                    season, week, player_id, madden_table) AS rn
    FROM player_career_counters
)
UPDATE player_career_counters c SET load_seq = o.rn FROM ordered o WHERE c.ctid = o.ctid;
SELECT setval('player_career_counters_load_seq',
              GREATEST(COALESCE((SELECT MAX(load_seq) FROM player_career_counters), 0),
                       (SELECT last_value FROM player_career_counters_load_seq)), true);
ALTER TABLE player_career_counters ALTER COLUMN stage SET DEFAULT 'WEEK';
ALTER TABLE player_career_counters ALTER COLUMN stage SET NOT NULL;
ALTER TABLE player_career_counters ALTER COLUMN load_seq SET DEFAULT nextval('player_career_counters_load_seq');
ALTER TABLE player_career_counters ALTER COLUMN load_seq SET NOT NULL;
ALTER TABLE player_career_counters DROP CONSTRAINT IF EXISTS player_career_counters_pkey;
ALTER TABLE player_career_counters ADD PRIMARY KEY (season, week, player_id, stage);
CREATE INDEX IF NOT EXISTS player_career_counters_load ON player_career_counters (player_id, load_seq);

-- 2. Per-key difference. Unchanged: every key in cur minus the previous row's value (0 when absent).
CREATE OR REPLACE FUNCTION counter_delta(cur jsonb, prev jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $$
    SELECT COALESCE(jsonb_object_agg(k, (cur ->> k)::int - COALESCE((prev ->> k)::int, 0)), '{}'::jsonb)
    FROM jsonb_object_keys(cur) AS k
$$;

-- 3. One row per career-counter row. The previous row is the player's previous load (`load_seq`). Status:
--    BASELINE (first row, no games in the season yet), PARTIAL (first row but the season says games were
--    played: that load's games are lost and reported), TABLE_CHANGED, RESET, NO_GAMES, OK (the delta's games
--    belong to this row's season) or CROSS_SEASON (the previous load was in an earlier season and this load
--    already has games of its own, so the delta covers both and cannot be split; it is flagged under the
--    earlier season). `credited_season` is the season the delta belongs to: the row's own season, or the
--    previous season when a start-of-season load (week <= 0) covers the tail of the season just ended.
CREATE OR REPLACE VIEW player_week_counters AS
WITH base AS (
    SELECT c.season, c.week, c.stage, c.player_id, c.madden_table, c.counters, c.games_played, c.load_seq,
           COALESCE(g.games_played, 0) AS season_games_played,
           LAG(c.season)       OVER (PARTITION BY c.player_id ORDER BY c.load_seq) AS prev_season,
           LAG(c.week)         OVER (PARTITION BY c.player_id ORDER BY c.load_seq) AS prev_week,
           LAG(c.stage)        OVER (PARTITION BY c.player_id ORDER BY c.load_seq) AS prev_stage,
           LAG(c.madden_table) OVER (PARTITION BY c.player_id ORDER BY c.load_seq) AS prev_madden_table,
           LAG(c.counters)     OVER (PARTITION BY c.player_id ORDER BY c.load_seq) AS prev_counters,
           LAG(c.games_played) OVER (PARTITION BY c.player_id ORDER BY c.load_seq) AS prev_games_played
    FROM player_career_counters c
    LEFT JOIN season_stat_general g ON g.season = c.season AND g.player_id = c.player_id
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

-- 4. One row per (credited season, player): the OK and NO_GAMES deltas summed key by key, the games those
--    rows covered, and the coverage gaps: weeks_unknown (TABLE_CHANGED, RESET or CROSS_SEASON), weeks_partial /
--    games_lost (a first career row whose games are lost, L2), and weeks_no_games (a NO_GAMES row whose
--    counters still grew, L4; its growth is kept in the sum).
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
           COUNT(*) FILTER (WHERE status IN ('TABLE_CHANGED', 'RESET', 'CROSS_SEASON')) AS weeks_unknown,
           COUNT(*) FILTER (WHERE status = 'PARTIAL') AS weeks_partial,
           COALESCE(SUM(games_covered) FILTER (WHERE status = 'PARTIAL'), 0) AS games_lost,
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
       c.weeks_partial, c.games_lost, c.weeks_no_games
FROM coverage c
LEFT JOIN summed s ON s.season = c.season AND s.player_id = c.player_id;

COMMIT;
