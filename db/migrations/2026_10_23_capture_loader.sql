-- Capture loader, part 1 (docs/PIPELINE_MIGRATION.md, phase 3): storage for every stat the
-- madden_capture.py / extract.py capture produces, the captures run log and Gemini token usage.
-- Hybrid layout (JR, Oct 14): offense extras as columns on season_stats / player_stats, every other
-- category in its own season- and game-level table, views joining them. Nothing reads the new
-- columns or tables yet. The player_id keys come in part 2 (2026_10_24_player_id_keys.sql), after
-- the roster load has fixed the player links.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_10_23_capture_loader.sql

BEGIN;

-- 1. Offense extras: the captured passing / rushing / receiving columns not stored until now.
--    Season Stats headers: PASSING YARDS,COMP,ATT,TD,INT,COMP%,YPG,RATING,YPA,LONG,SACKS,GP,DP;
--    RUSHING ATT,YARDS,AVG,TD,AVG G,FUMB,BTK,YAC,LONG; RECEIVING REC,YARDS,AVG,AVG G,TD,RAC,RAC AVG,LONG.
ALTER TABLE season_stats
    ADD COLUMN IF NOT EXISTS pass_comp_pct NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS pass_ypg      NUMERIC(6, 1),
    ADD COLUMN IF NOT EXISTS pass_rating   NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS pass_ypa      NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS pass_long     INT,
    ADD COLUMN IF NOT EXISTS pass_sacks    INT,
    ADD COLUMN IF NOT EXISTS rush_avg      NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS rush_ypg      NUMERIC(6, 1),
    ADD COLUMN IF NOT EXISTS rush_btk      INT,
    ADD COLUMN IF NOT EXISTS rush_yac      INT,
    ADD COLUMN IF NOT EXISTS rush_long     INT,
    ADD COLUMN IF NOT EXISTS rec_avg       NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS rec_ypg       NUMERIC(6, 1),
    ADD COLUMN IF NOT EXISTS rec_rac       INT,
    ADD COLUMN IF NOT EXISTS rec_rac_avg   NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS rec_long      INT;

-- Game player stats headers: PASSING RATING,YARDS,TD,INT,LONG,SACKS,COMP,ATT,COMP%,YPA;
-- RUSHING ATT,YARDS,AVG,TD,FUMB,BTK,YAC,LONG; RECEIVING REC,YARDS,AVG,TD,RAC,RAC AVG,LONG.
ALTER TABLE player_stats
    ADD COLUMN IF NOT EXISTS player_id     INT REFERENCES players(id),   -- keyed in part 2
    ADD COLUMN IF NOT EXISTS pass_comp_pct NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS pass_rating   NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS pass_ypa      NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS pass_long     INT,
    ADD COLUMN IF NOT EXISTS pass_sacks    INT,
    ADD COLUMN IF NOT EXISTS rush_avg      NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS rush_btk      INT,
    ADD COLUMN IF NOT EXISTS rush_yac      INT,
    ADD COLUMN IF NOT EXISTS rush_long     INT,
    ADD COLUMN IF NOT EXISTS rec_avg       NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS rec_rac       INT,
    ADD COLUMN IF NOT EXISTS rec_rac_avg   NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS rec_long      INT;

-- 2. Every other category: one season-level and one game-level table each, keyed on player_id.
--    A row exists only where Madden lists the player; NULL = not listed, 0 = listed as 0.
CREATE TABLE IF NOT EXISTS season_stat_defense (       -- SOLO,ASSISTS,TAK,TFL,SACK,INT,INT YDS,INT AVG,INT LG,DEFL,FFUMB,FMBREC,FMBYDS,BLOCK,SFTY,TD
    season INT NOT NULL, player_id INT NOT NULL REFERENCES players(id), team_id INT REFERENCES teams(id), position VARCHAR(10),
    solo INT, assists INT, tackles INT, tfl INT, sacks NUMERIC(5, 1), ints INT, int_yds INT, int_avg NUMERIC(5, 1),
    int_long INT, deflections INT, forced_fumbles INT, fumble_recoveries INT, fumble_yds INT, blocks INT, safeties INT, tds INT,
    PRIMARY KEY (season, player_id));
CREATE TABLE IF NOT EXISTS game_stat_defense (
    game_id INT NOT NULL REFERENCES games(id) ON DELETE CASCADE, player_id INT NOT NULL REFERENCES players(id), team_id INT REFERENCES teams(id), position VARCHAR(10),
    solo INT, assists INT, tackles INT, tfl INT, sacks NUMERIC(5, 1), ints INT, int_yds INT, int_avg NUMERIC(5, 1),
    int_long INT, deflections INT, forced_fumbles INT, fumble_recoveries INT, fumble_yds INT, blocks INT, safeties INT, tds INT,
    PRIMARY KEY (game_id, player_id));

CREATE TABLE IF NOT EXISTS season_stat_kicking (       -- FGM,FGA,FG%,FG LNG,FG BLK,XPM,XPA,XP%,XPB,FGM29..FGA50,KO,TB
    season INT NOT NULL, player_id INT NOT NULL REFERENCES players(id), team_id INT REFERENCES teams(id), position VARCHAR(10),
    fgm INT, fga INT, fg_pct NUMERIC(5, 1), fg_long INT, fg_blocked INT, xpm INT, xpa INT, xp_pct NUMERIC(5, 1), xp_blocked INT,
    fgm_29 INT, fga_29 INT, fgm_39 INT, fga_39 INT, fgm_49 INT, fga_49 INT, fgm_50 INT, fga_50 INT, kickoffs INT, touchbacks INT,
    PRIMARY KEY (season, player_id));
CREATE TABLE IF NOT EXISTS game_stat_kicking (
    game_id INT NOT NULL REFERENCES games(id) ON DELETE CASCADE, player_id INT NOT NULL REFERENCES players(id), team_id INT REFERENCES teams(id), position VARCHAR(10),
    fgm INT, fga INT, fg_pct NUMERIC(5, 1), fg_long INT, fg_blocked INT, xpm INT, xpa INT, xp_pct NUMERIC(5, 1), xp_blocked INT,
    fgm_29 INT, fga_29 INT, fgm_39 INT, fga_39 INT, fgm_49 INT, fga_49 INT, fgm_50 INT, fga_50 INT, kickoffs INT, touchbacks INT,
    PRIMARY KEY (game_id, player_id));

CREATE TABLE IF NOT EXISTS season_stat_punting (       -- PUNTS,YARDS,AVG,NET YDS,NET AVG,BLOCK,IN20,TB,LONG
    season INT NOT NULL, player_id INT NOT NULL REFERENCES players(id), team_id INT REFERENCES teams(id), position VARCHAR(10),
    punts INT, yards INT, average NUMERIC(5, 1), net_yards INT, net_average NUMERIC(5, 1), blocked INT, inside_20 INT, touchbacks INT, longest INT,
    PRIMARY KEY (season, player_id));
CREATE TABLE IF NOT EXISTS game_stat_punting (
    game_id INT NOT NULL REFERENCES games(id) ON DELETE CASCADE, player_id INT NOT NULL REFERENCES players(id), team_id INT REFERENCES teams(id), position VARCHAR(10),
    punts INT, yards INT, average NUMERIC(5, 1), net_yards INT, net_average NUMERIC(5, 1), blocked INT, inside_20 INT, touchbacks INT, longest INT,
    PRIMARY KEY (game_id, player_id));

CREATE TABLE IF NOT EXISTS season_stat_kickreturn (    -- KR,YARDS,AVG,TD,LONG
    season INT NOT NULL, player_id INT NOT NULL REFERENCES players(id), team_id INT REFERENCES teams(id), position VARCHAR(10),
    returns INT, yards INT, average NUMERIC(5, 1), tds INT, longest INT,
    PRIMARY KEY (season, player_id));
CREATE TABLE IF NOT EXISTS game_stat_kickreturn (
    game_id INT NOT NULL REFERENCES games(id) ON DELETE CASCADE, player_id INT NOT NULL REFERENCES players(id), team_id INT REFERENCES teams(id), position VARCHAR(10),
    returns INT, yards INT, average NUMERIC(5, 1), tds INT, longest INT,
    PRIMARY KEY (game_id, player_id));

CREATE TABLE IF NOT EXISTS season_stat_puntreturn (    -- PR,YARDS,AVG,TD,LONG
    season INT NOT NULL, player_id INT NOT NULL REFERENCES players(id), team_id INT REFERENCES teams(id), position VARCHAR(10),
    returns INT, yards INT, average NUMERIC(5, 1), tds INT, longest INT,
    PRIMARY KEY (season, player_id));
CREATE TABLE IF NOT EXISTS game_stat_puntreturn (
    game_id INT NOT NULL REFERENCES games(id) ON DELETE CASCADE, player_id INT NOT NULL REFERENCES players(id), team_id INT REFERENCES teams(id), position VARCHAR(10),
    returns INT, yards INT, average NUMERIC(5, 1), tds INT, longest INT,
    PRIMARY KEY (game_id, player_id));

CREATE TABLE IF NOT EXISTS season_stat_blocking (      -- SACK (sacks allowed)
    season INT NOT NULL, player_id INT NOT NULL REFERENCES players(id), team_id INT REFERENCES teams(id), position VARCHAR(10),
    sacks_allowed INT,
    PRIMARY KEY (season, player_id));
CREATE TABLE IF NOT EXISTS game_stat_blocking (
    game_id INT NOT NULL REFERENCES games(id) ON DELETE CASCADE, player_id INT NOT NULL REFERENCES players(id), team_id INT REFERENCES teams(id), position VARCHAR(10),
    sacks_allowed INT,
    PRIMARY KEY (game_id, player_id));

-- GP / DP: every Season Stats category repeats them (stored once per player); a game has DP only (General tab).
CREATE TABLE IF NOT EXISTS season_stat_general (
    season INT NOT NULL, player_id INT NOT NULL REFERENCES players(id), team_id INT REFERENCES teams(id), position VARCHAR(10),
    games_played INT, downs_played INT,
    PRIMARY KEY (season, player_id));
CREATE TABLE IF NOT EXISTS game_stat_general (
    game_id INT NOT NULL REFERENCES games(id) ON DELETE CASCADE, player_id INT NOT NULL REFERENCES players(id), team_id INT REFERENCES teams(id), position VARCHAR(10),
    downs_played INT,
    PRIMARY KEY (game_id, player_id));

-- 3. Team Stats tabs (season): CONVERSIONS 3DC,3DA,3D%,4DC,4DA,4D%,2PC,2PA,2P%;
--    RED ZONE ATT,TD,FG,OFF%,DATT,DTD,DFG,DEF%; PENALTIES PEN,YARDS.
ALTER TABLE team_seasons
    ADD COLUMN IF NOT EXISTS third_down_conv INT,  ADD COLUMN IF NOT EXISTS third_down_att INT,  ADD COLUMN IF NOT EXISTS third_down_pct NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS fourth_down_conv INT, ADD COLUMN IF NOT EXISTS fourth_down_att INT, ADD COLUMN IF NOT EXISTS fourth_down_pct NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS two_point_conv INT,   ADD COLUMN IF NOT EXISTS two_point_att INT,   ADD COLUMN IF NOT EXISTS two_point_pct NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS red_zone_att INT, ADD COLUMN IF NOT EXISTS red_zone_td INT, ADD COLUMN IF NOT EXISTS red_zone_fg INT, ADD COLUMN IF NOT EXISTS red_zone_pct NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS red_zone_def_att INT, ADD COLUMN IF NOT EXISTS red_zone_def_td INT, ADD COLUMN IF NOT EXISTS red_zone_def_fg INT, ADD COLUMN IF NOT EXISTS red_zone_def_pct NUMERIC(5, 1),
    ADD COLUMN IF NOT EXISTS penalties INT, ADD COLUMN IF NOT EXISTS penalty_yards INT;

-- 4. Box score (per game and team) beyond what team_stats already has: quarter points, return
--    yards, total yards, 2-point conversions, red zone, penalty yards, time of possession.
ALTER TABLE team_stats
    ADD COLUMN IF NOT EXISTS q1 INT, ADD COLUMN IF NOT EXISTS q2 INT, ADD COLUMN IF NOT EXISTS q3 INT,
    ADD COLUMN IF NOT EXISTS q4 INT, ADD COLUMN IF NOT EXISTS ot INT,      -- NULL when the box score's digits were unreadable
    ADD COLUMN IF NOT EXISTS punt_return_yards INT, ADD COLUMN IF NOT EXISTS kick_return_yards INT,
    ADD COLUMN IF NOT EXISTS total_yards_gained INT, ADD COLUMN IF NOT EXISTS two_point_conversions INT,
    ADD COLUMN IF NOT EXISTS red_zone_pct NUMERIC(5, 1), ADD COLUMN IF NOT EXISTS red_zone_tds INT, ADD COLUMN IF NOT EXISTS red_zone_fgs INT,
    ADD COLUMN IF NOT EXISTS penalty_yards INT, ADD COLUMN IF NOT EXISTS time_of_possession_s INT;

-- 5. Roster card fields (player_seasons.overall_rating = the roster list OVR; the card's base OVR
--    and morale are not stored).
ALTER TABLE player_seasons
    ADD COLUMN IF NOT EXISTS dev_trait VARCHAR(32),
    ADD COLUMN IF NOT EXISTS archetype VARCHAR(64),
    ADD COLUMN IF NOT EXISTS role VARCHAR(64);

-- 6. One row per capture run (duplicate / missed-week / row-count checks, resuming, token totals).
CREATE TABLE IF NOT EXISTS captures (
    id SERIAL PRIMARY KEY,
    folder VARCHAR(255) NOT NULL UNIQUE,              -- captures/<run> name, e.g. 2026-10-14_0146
    mode VARCHAR(32) NOT NULL,                        -- weekly | roster | season_schedule | games
    season INT, week INT,
    status VARCHAR(16) NOT NULL DEFAULT 'captured',   -- captured | extracted | loaded | failed
    row_counts JSONB,                                 -- {csv name: rows}
    input_tokens BIGINT, cached_tokens BIGINT, output_tokens BIGINT, thinking_tokens BIGINT,
    error TEXT,
    captured_at TIMESTAMP, extracted_at TIMESTAMP, loaded_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS captures_mode_season_week ON captures (mode, season, week);

-- 7. One row per Gemini call (extract.py and the newsroom), replacing the cost estimates.
CREATE TABLE IF NOT EXISTS llm_usage (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(16) NOT NULL,                      -- extract | newsroom
    capture_id INT REFERENCES captures(id) ON DELETE SET NULL,
    label VARCHAR(255),                               -- screenshot file, or agent / article type
    model VARCHAR(64),
    input_tokens INT, cached_tokens INT, output_tokens INT, thinking_tokens INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS llm_usage_source_created ON llm_usage (source, created_at);

-- 8. One line per player: season (every category) and game (every category).
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
       pr.returns AS pr_returns, pr.yards AS pr_yards, pr.average AS pr_average, pr.tds AS pr_tds, pr.longest AS pr_longest
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
       pr.returns AS pr_returns, pr.yards AS pr_yards, pr.average AS pr_average, pr.tds AS pr_tds, pr.longest AS pr_longest
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
