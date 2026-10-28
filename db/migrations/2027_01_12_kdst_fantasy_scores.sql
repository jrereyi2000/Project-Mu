-- 2027_01_12_kdst_fantasy_scores.sql
-- Unify vw_fantasy_scores to include skill players (player_stats),
-- kickers (game_stat_kicking with yardage-based fractional scoring: min 3 for <30 yds, 3.x, 4.x, 5.x),
-- and team defenses (player_seasons DEF with sacks, turnovers, safeties, blocks, TDs, and points allowed tiers).

DROP VIEW IF EXISTS vw_fantasy_scores CASCADE;

CREATE VIEW vw_fantasy_scores AS
-- 1. Offensive Skill Players
SELECT 
    ps.game_id,
    ps.player_name,
    ps.team_id,
    g.season,
    g.week,
    ps.fantasy_points AS ppr_score,
    ps.player_id
FROM player_stats ps
JOIN games g ON ps.game_id = g.id

UNION ALL

-- 2. Kickers (game_stat_kicking)
-- Yardage-based: <30 yds = 3.0 pts, 30-39 = 3.5, 40-49 = 4.5, 50+ = 5.5,
-- with fg_long exact decimal yardage adjustment (fg_long * 0.1) for the longest kick;
-- +1.0 per extra point made, -1.0 per missed FG, -1.0 per missed XP.
SELECT 
    k.game_id,
    p.player_name,
    k.team_id,
    g.season,
    g.week,
    ROUND((
        COALESCE(k.fgm_29, 0) * 3.0
        + COALESCE(k.fgm_39, 0) * 3.5
        + COALESCE(k.fgm_49, 0) * 4.5
        + COALESCE(k.fgm_50, 0) * 5.5
        + CASE 
            WHEN COALESCE(k.fgm, 0) > 0 AND COALESCE(k.fg_long, 0) >= 30 THEN
                (k.fg_long::numeric * 0.1) - (
                    CASE 
                        WHEN k.fg_long >= 50 THEN 5.5
                        WHEN k.fg_long >= 40 THEN 4.5
                        ELSE 3.5
                    END
                )
            ELSE 0.0
        END
        + COALESCE(k.xpm, 0) * 1.0
        - (COALESCE(k.fga, 0) - COALESCE(k.fgm, 0)) * 1.0
        - (COALESCE(k.xpa, 0) - COALESCE(k.xpm, 0)) * 1.0
    )::numeric, 2) AS ppr_score,
    k.player_id
FROM game_stat_kicking k
JOIN games g ON k.game_id = g.id
JOIN players p ON p.id = k.player_id

UNION ALL

-- 3. Team Defenses / Special Teams (D/ST)
-- Sacks (1), INTs (2), Fumble recoveries (2), Safeties (2), Blocked kicks (2),
-- Defensive TDs (6), Return TDs (6), and Points Allowed tiers:
-- 0 pts = 10, 1-6 = 7, 7-13 = 4, 14-20 = 1, 21-27 = 0, 28-34 = -1, 35+ = -4.
SELECT 
    g.id AS game_id,
    p.player_name,
    dst.team_id,
    g.season,
    g.week,
    ROUND((
        COALESCE(def.sacks, 0)
        + 2 * COALESCE(def.ints, 0)
        + 2 * COALESCE(def.fumble_recoveries, 0)
        + 6 * (COALESCE(def.tds, 0) + COALESCE(ret.tds, 0))
        + 2 * COALESCE(def.safeties, 0)
        + 2 * COALESCE(def.blocks, 0)
        + CASE 
            WHEN (CASE WHEN g.home_team_id = dst.team_id THEN g.away_score ELSE g.home_score END) = 0 THEN 10
            WHEN (CASE WHEN g.home_team_id = dst.team_id THEN g.away_score ELSE g.home_score END) <= 6 THEN 7
            WHEN (CASE WHEN g.home_team_id = dst.team_id THEN g.away_score ELSE g.home_score END) <= 13 THEN 4
            WHEN (CASE WHEN g.home_team_id = dst.team_id THEN g.away_score ELSE g.home_score END) <= 20 THEN 1
            WHEN (CASE WHEN g.home_team_id = dst.team_id THEN g.away_score ELSE g.home_score END) <= 27 THEN 0
            WHEN (CASE WHEN g.home_team_id = dst.team_id THEN g.away_score ELSE g.home_score END) <= 34 THEN -1
            ELSE -4
        END
    )::numeric, 2) AS ppr_score,
    dst.player_id
FROM player_seasons dst
JOIN players p ON p.id = dst.player_id
JOIN games g ON g.season = dst.season AND g.status = 'COMPLETED'
    AND (g.home_team_id = dst.team_id OR g.away_team_id = dst.team_id)
LEFT JOIN (
    SELECT game_id, team_id, SUM(sacks) AS sacks, SUM(ints) AS ints,
           SUM(fumble_recoveries) AS fumble_recoveries, SUM(tds) AS tds,
           SUM(safeties) AS safeties, SUM(blocks) AS blocks
    FROM game_stat_defense GROUP BY game_id, team_id
) def ON def.game_id = g.id AND def.team_id = dst.team_id
LEFT JOIN (
    SELECT game_id, team_id, SUM(tds) AS tds FROM (
        SELECT game_id, team_id, tds FROM game_stat_kickreturn
        UNION ALL
        SELECT game_id, team_id, tds FROM game_stat_puntreturn
    ) r GROUP BY game_id, team_id
) ret ON ret.game_id = g.id AND ret.team_id = dst.team_id
WHERE dst.position = 'DEF';
