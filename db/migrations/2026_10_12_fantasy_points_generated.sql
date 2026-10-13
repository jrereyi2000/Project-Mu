-- Make player_stats.fantasy_points a generated (computed) column so PPR scoring is always
-- derived from the box score and can never be missing or stale.
--
-- Scoring (full PPR): 0.04/pass yd, 4/pass TD, -2/INT, 0.1/rush yd, 6/rush TD,
--                     1/reception, 0.1/rec yd, 6/rec TD, -2/fumble. NULL stats count as 0.
--
-- Self-verifying: snapshots the old values and both dependent views, and aborts (rolling
-- everything back) if any player_stats row or view row differs afterwards.
-- Apply with:  psql -U postgres -d madden -f db/migrations/2026_10_12_fantasy_points_generated.sql

BEGIN;

CREATE TEMP TABLE _fp_before ON COMMIT DROP AS
    SELECT id, fantasy_points FROM player_stats;
CREATE TEMP TABLE _vw_scores_before ON COMMIT DROP AS
    SELECT * FROM vw_fantasy_scores;
CREATE TEMP TABLE _vw_dvp_before ON COMMIT DROP AS
    SELECT * FROM vw_fantasy_defense_vs_position;

-- Views depend on the column, so they must be dropped and recreated around the swap.
DROP VIEW vw_fantasy_scores;
DROP VIEW vw_fantasy_defense_vs_position;

ALTER TABLE player_stats DROP COLUMN fantasy_points;
ALTER TABLE player_stats ADD COLUMN fantasy_points NUMERIC(6, 2) GENERATED ALWAYS AS (
    ROUND(
          COALESCE(pass_yards, 0)    * 0.04
        + COALESCE(pass_tds, 0)      * 4.0
        - COALESCE(interceptions, 0) * 2.0
        + COALESCE(rush_yards, 0)    * 0.1
        + COALESCE(rush_tds, 0)      * 6.0
        + COALESCE(receptions, 0)    * 1.0
        + COALESCE(rec_yards, 0)     * 0.1
        + COALESCE(rec_tds, 0)       * 6.0
        - COALESCE(fumb, 0)          * 2.0
    , 2)
) STORED;

CREATE VIEW vw_fantasy_scores AS
SELECT ps.game_id,
       ps.player_name,
       ps.team_id,
       g.season,
       g.week,
       ps.fantasy_points AS ppr_score
FROM player_stats ps
JOIN games g ON ps.game_id = g.id;

CREATE VIEW vw_fantasy_defense_vs_position AS
WITH game_def AS (
    SELECT CASE WHEN ps.team_id = g.home_team_id THEN g.away_team_id ELSE g.home_team_id END AS def_team_id,
           ps."position",
           g.season,
           ps.fantasy_points AS ppr_score
    FROM player_stats ps
    JOIN games g ON ps.game_id = g.id
    WHERE g.status::text = 'COMPLETED'::text
), team_pos_totals AS (
    SELECT season, def_team_id, "position",
           SUM(ppr_score) AS total_allowed,
           AVG(ppr_score) AS avg_allowed
    FROM game_def
    GROUP BY season, def_team_id, "position"
), league_pos_benchmarks AS (
    SELECT season, "position",
           MIN(total_allowed) AS min_allowed,
           MAX(total_allowed) AS max_allowed,
           AVG(total_allowed) AS league_avg_allowed
    FROM team_pos_totals
    GROUP BY season, "position"
)
SELECT tpt.season,
       tpt.def_team_id,
       t.abbreviation AS def_team_abbr,
       t.name AS def_team_name,
       tpt."position",
       tpt.total_allowed,
       tpt.avg_allowed,
       lpb.min_allowed,
       lpb.max_allowed,
       lpb.league_avg_allowed,
       NTILE(5) OVER (PARTITION BY tpt.season, tpt."position" ORDER BY tpt.total_allowed)::numeric AS matchup_stars
FROM team_pos_totals tpt
JOIN teams t ON tpt.def_team_id = t.id
JOIN league_pos_benchmarks lpb ON tpt.season = lpb.season AND tpt."position"::text = lpb."position"::text;

DO $$
DECLARE
    n_rows_before INT; n_rows_after INT; n_mismatch INT; n_scores_diff INT; n_dvp_diff INT;
BEGIN
    SELECT COUNT(*) INTO n_rows_before FROM _fp_before;
    SELECT COUNT(*) INTO n_rows_after FROM player_stats;
    SELECT COUNT(*) INTO n_mismatch
    FROM _fp_before b JOIN player_stats ps USING (id)
    WHERE b.fantasy_points IS DISTINCT FROM ps.fantasy_points;
    SELECT COUNT(*) INTO n_scores_diff FROM (
        (SELECT * FROM _vw_scores_before EXCEPT ALL SELECT * FROM vw_fantasy_scores)
        UNION ALL
        (SELECT * FROM vw_fantasy_scores EXCEPT ALL SELECT * FROM _vw_scores_before)) d;
    SELECT COUNT(*) INTO n_dvp_diff FROM (
        (SELECT * FROM _vw_dvp_before EXCEPT ALL SELECT * FROM vw_fantasy_defense_vs_position)
        UNION ALL
        (SELECT * FROM vw_fantasy_defense_vs_position EXCEPT ALL SELECT * FROM _vw_dvp_before)) d;
    RAISE NOTICE 'player_stats rows: % -> %, value mismatches: %, vw_fantasy_scores diffs: %, vw_fantasy_defense_vs_position diffs: %',
        n_rows_before, n_rows_after, n_mismatch, n_scores_diff, n_dvp_diff;
    IF n_rows_before <> n_rows_after OR n_mismatch > 0 OR n_scores_diff > 0 OR n_dvp_diff > 0 THEN
        RAISE EXCEPTION 'fantasy_points migration verification failed; rolling back';
    END IF;
END $$;

COMMIT;
