-- 2026-2034 player rows (docs/done/HISTORY_BACKFILL_PLAN.md phase 3; PROJECT_SPEC §3.3k).
-- `python3 -m engines.history_backfill apply-players --run N` writes a staged run into the live player tables with
-- source = 'SYNTH'; the players it creates carry players.origin ('HISTORY': a real 2026 player who retired before
-- 2035; 'HISTORY_FILLIN': a fill-in). Every other row stays source = 'CAPTURE' / origin NULL. season_stat_lines
-- gains the player_seasons row's source.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_12_30_history_player_rows.sql

BEGIN;

ALTER TABLE player_seasons ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';
ALTER TABLE season_stats ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';
ALTER TABLE season_stat_defense ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';
ALTER TABLE season_stat_kicking ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';
ALTER TABLE season_stat_punting ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';
ALTER TABLE season_stat_kickreturn ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';
ALTER TABLE season_stat_puntreturn ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';
ALTER TABLE season_stat_blocking ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';
ALTER TABLE season_stat_general ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';
ALTER TABLE players ADD COLUMN IF NOT EXISTS origin VARCHAR(16);

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
       ps.source
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

COMMIT;
