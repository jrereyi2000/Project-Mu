-- Project Mu: PostgreSQL schema bootstrap (dbname=madden).
--
-- Reproduces the live schema (tables, constraints, generated columns, views,
-- functions, triggers) on an empty database. Schema only, no data.
-- Safe to re-run: IF NOT EXISTS / CREATE OR REPLACE / DROP TRIGGER IF EXISTS.
-- Note: IF NOT EXISTS will not add columns to a table that already exists;
-- columns added later must ship as migrations under db/migrations/.
--
-- Column order mirrors the live database so `pg_dump --schema-only` of a fresh
-- database matches the live one. Apply with:
--   docker exec -i madden-db psql -U postgres -d <db> -v ON_ERROR_STOP=1 < db/init_db.sql

-- ---------------------------------------------------------------------------
-- Core entities & league state
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS league_state (
    id INT NOT NULL DEFAULT 1 PRIMARY KEY,
    current_season INT NOT NULL DEFAULT 2024,
    current_week INT NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS teams (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) UNIQUE NOT NULL,
    city VARCHAR(255),
    abbreviation VARCHAR(10),
    team_color VARCHAR(10),
    stadium_name VARCHAR(100),
    conference VARCHAR(50),
    division VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS players (
    id SERIAL PRIMARY KEY,
    player_name VARCHAR(255) NOT NULL,
    full_name VARCHAR(255),
    height VARCHAR(10),
    weight INT,
    player_number INT DEFAULT 0,
    espn_id INT
);

-- ---------------------------------------------------------------------------
-- Temporal snapshot ledgers
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS team_seasons (
    id SERIAL PRIMARY KEY,
    team_id INT REFERENCES teams(id) ON DELETE CASCADE,
    season INT NOT NULL,
    wins INT DEFAULT 0,
    losses INT DEFAULT 0,
    team_ovr INT,
    off_ovr INT,
    def_ovr INT,
    pass_off_rank INT,
    rush_off_rank INT,
    pass_rush_rank INT,
    coverage_rank INT,
    watchability_index DOUBLE PRECISION DEFAULT 0.0,
    popularity_index DOUBLE PRECISION DEFAULT 0.0,
    opp_adj_credit DOUBLE PRECISION,
    opp_adj_credit_recent DOUBLE PRECISION,
    sos_rating DOUBLE PRECISION,
    expected_wins DOUBLE PRECISION,
    watchability_sigma_top DOUBLE PRECISION,
    p_draft DOUBLE PRECISION,
    y_prod DOUBLE PRECISION,
    purgatory_penalty DOUBLE PRECISION,
    trajectory_multiplier DOUBLE PRECISION,
    pythagorean_wins DOUBLE PRECISION,
    pace_production DOUBLE PRECISION,
    s_playoff DOUBLE PRECISION,
    -- Team Stats tabs (capture loader, 2026_10_23): conversions, red zone, penalties
    third_down_conv INT, third_down_att INT, third_down_pct NUMERIC(5, 1),
    fourth_down_conv INT, fourth_down_att INT, fourth_down_pct NUMERIC(5, 1),
    two_point_conv INT, two_point_att INT, two_point_pct NUMERIC(5, 1),
    red_zone_att INT, red_zone_td INT, red_zone_fg INT, red_zone_pct NUMERIC(5, 1),
    red_zone_def_att INT, red_zone_def_td INT, red_zone_def_fg INT, red_zone_def_pct NUMERIC(5, 1),
    penalties INT, penalty_yards INT,
    UNIQUE (team_id, season)
);

CREATE TABLE IF NOT EXISTS player_seasons (
    id SERIAL PRIMARY KEY,
    player_id INT REFERENCES players(id) ON DELETE CASCADE,
    team_id INT REFERENCES teams(id) ON DELETE CASCADE,
    season INT NOT NULL,
    position VARCHAR(10),
    age INT,
    overall_rating INT,
    popularity_index DOUBLE PRECISION DEFAULT 0.0,
    years_of_experience INT,  -- 0 = rookie, 1 = second-year, ...
    spike_index DOUBLE PRECISION,
    r_rating DOUBLE PRECISION,
    f_platform DOUBLE PRECISION,
    t_success DOUBLE PRECISION,
    s_playoff DOUBLE PRECISION,
    a_awards DOUBLE PRECISION,
    e_exposure DOUBLE PRECISION,
    -- Roster card (capture loader, 2026_10_23); overall_rating = the roster list OVR
    dev_trait VARCHAR(32),
    archetype VARCHAR(64),
    role VARCHAR(64),
    UNIQUE (player_id, season)
);

-- ---------------------------------------------------------------------------
-- Games & statistics
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS games (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT NOT NULL,
    home_team_id INT REFERENCES teams(id),
    away_team_id INT REFERENCES teams(id),
    home_score INT DEFAULT 0,
    away_score INT DEFAULT 0,
    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) DEFAULT 'COMPLETED',  -- 'COMPLETED' | 'SCHEDULED' | 'BYE'
    game_time VARCHAR(50),
    game_type VARCHAR(20) DEFAULT 'REGULAR_SEASON',
    excitement_index DOUBLE PRECISION DEFAULT 0.0,
    projected_excitement DOUBLE PRECISION,    -- pre-game projected E (raw scale); engines/excitement_projection.py
    CONSTRAINT games_season_week_home_away_key UNIQUE (season, week, home_team_id, away_team_id)
);

CREATE TABLE IF NOT EXISTS team_stats (
    id SERIAL PRIMARY KEY,
    game_id INT REFERENCES games(id) ON DELETE CASCADE,
    team_name VARCHAR(255) NOT NULL,
    score INT DEFAULT 0,
    first_downs INT DEFAULT 0,
    total_offense INT DEFAULT 0,
    total_plays INT DEFAULT 0,
    rushing_attempts INT DEFAULT 0,
    rushing_yards INT DEFAULT 0,
    rushing_tds INT DEFAULT 0,
    passing_completions INT DEFAULT 0,
    passing_attempts INT DEFAULT 0,
    passing_yards INT DEFAULT 0,
    passing_tds INT DEFAULT 0,
    third_down_attempts INT,          -- NULL = unknown; engines/team_box_estimator.py fills them (2026_10_29)
    third_down_conversions INT,
    fourth_down_attempts INT,
    fourth_down_conversions INT,
    turnovers INT DEFAULT 0,
    -- Box score (capture loader, 2026_10_23); quarters NULL when unreadable
    q1 INT, q2 INT, q3 INT, q4 INT, ot INT,
    punt_return_yards INT, kick_return_yards INT, total_yards_gained INT, two_point_conversions INT,
    red_zone_pct NUMERIC(5, 1), red_zone_tds INT, red_zone_fgs INT, penalty_yards INT, time_of_possession_s INT,
    -- 2026_10_29: estimated per game from season totals where no box score was captured
    red_zone_attempts INT, two_point_attempts INT, penalties INT,
    estimated_fields TEXT[] NOT NULL DEFAULT '{}',   -- the columns engines/team_box_estimator.py filled
    exact_fields TEXT[] NOT NULL DEFAULT '{}',       -- 2026_10_31: of those, the ones that are exact (a single-game remainder)
    UNIQUE (game_id, team_name)
);

CREATE TABLE IF NOT EXISTS player_stats (
    id SERIAL PRIMARY KEY,
    game_id INT REFERENCES games(id) ON DELETE CASCADE,
    player_name VARCHAR(255) NOT NULL,
    completions INT DEFAULT 0,
    pass_attempts INT DEFAULT 0,
    pass_yards INT DEFAULT 0,
    pass_tds INT DEFAULT 0,
    interceptions INT DEFAULT 0,
    rush_attempts INT DEFAULT 0,
    rush_yards INT DEFAULT 0,
    rush_tds INT DEFAULT 0,
    receptions INT DEFAULT 0,
    rec_yards INT DEFAULT 0,
    rec_tds INT DEFAULT 0,
    team_id INT REFERENCES teams(id),
    fumb INT DEFAULT 0,
    position VARCHAR(10),
    -- Capture loader (2026_10_23): player id (keyed in 2026_10_24) and offense extras
    player_id INT REFERENCES players(id),
    pass_comp_pct NUMERIC(5, 1), pass_rating NUMERIC(5, 1), pass_ypa NUMERIC(5, 1), pass_long INT, pass_sacks INT,
    rush_avg NUMERIC(5, 1), rush_btk INT, rush_yac INT, rush_long INT,
    rec_avg NUMERIC(5, 1), rec_rac INT, rec_rac_avg NUMERIC(5, 1), rec_long INT,
    -- Full-PPR fantasy points, always derived from the box score above (read-only; never insert/update it).
    fantasy_points NUMERIC(6, 2) GENERATED ALWAYS AS (
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
    ) STORED,
    UNIQUE (game_id, player_name, position)
);

CREATE TABLE IF NOT EXISTS season_stats (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    player_name VARCHAR(255) NOT NULL,
    pass_yards INT DEFAULT 0,
    pass_tds INT DEFAULT 0,
    interceptions INT DEFAULT 0,
    rush_yards INT DEFAULT 0,
    rush_tds INT DEFAULT 0,
    rec_yards INT DEFAULT 0,
    rec_tds INT DEFAULT 0,
    team_id INT REFERENCES teams(id),
    completions INT DEFAULT 0,
    pass_attempts INT DEFAULT 0,
    rush_attempts INT DEFAULT 0,
    receptions INT DEFAULT 0,
    fumb INT DEFAULT 0,
    position VARCHAR(10),
    player_id INT REFERENCES players(id),
    total_fantasy_points NUMERIC(7, 2),
    ppg_fantasy_points NUMERIC(5, 2),
    -- Capture loader (2026_10_23): offense extras
    pass_comp_pct NUMERIC(5, 1), pass_ypg NUMERIC(6, 1), pass_rating NUMERIC(5, 1), pass_ypa NUMERIC(5, 1), pass_long INT, pass_sacks INT,
    rush_avg NUMERIC(5, 1), rush_ypg NUMERIC(6, 1), rush_btk INT, rush_yac INT, rush_long INT,
    rec_avg NUMERIC(5, 1), rec_ypg NUMERIC(6, 1), rec_rac INT, rec_rac_avg NUMERIC(5, 1), rec_long INT,
    UNIQUE (season, player_name, team_id, position)
);


-- ---------------------------------------------------------------------------
-- Capture loader (db/migrations/2026_10_23_capture_loader.sql): every other stat
-- category, the captures run log and Gemini token usage
-- ---------------------------------------------------------------------------

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

-- 5b. Weekly injuries ledger (Manage Roster > Injury Report, 32 teams via Apple Vision OCR).
CREATE TABLE IF NOT EXISTS injuries (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    player_id INT REFERENCES players(id) ON DELETE SET NULL,
    player_name VARCHAR(255) NOT NULL,
    position VARCHAR(10),
    ovr INT,
    injury_type VARCHAR(100),
    duration VARCHAR(50),
    weeks_remaining INT DEFAULT 0,
    is_ir BOOLEAN DEFAULT FALSE,
    captured_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT uq_injuries_season_week_team_name UNIQUE(season, week, team_id, player_name)
);
CREATE INDEX IF NOT EXISTS idx_injuries_season_week ON injuries(season, week);
CREATE INDEX IF NOT EXISTS idx_injuries_team ON injuries(team_id);
CREATE INDEX IF NOT EXISTS idx_injuries_player ON injuries(player_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_injuries_unique_player ON injuries(season, week, player_id) WHERE player_id IS NOT NULL;

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


-- ---------------------------------------------------------------------------
-- Fantasy analytics & projections
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fantasy_projections (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT NOT NULL,
    player_name VARCHAR(255) NOT NULL,
    team_id INT REFERENCES teams(id),
    projected_points NUMERIC(5, 2) DEFAULT 0.00,
    adp_rank INT,
    position VARCHAR(10),
    avg_points NUMERIC(5, 2),
    matchup_stars INT,          -- 1-5
    pos_rank VARCHAR(10),       -- e.g. 'QB1', 'WR12'
    delta NUMERIC(5, 2),
    player_id INT,              -- players.id (no FK constraint in live schema)
    ros_rank VARCHAR(10),
    ros_total_points NUMERIC(7, 2),
    UNIQUE (season, week, player_id)
);

-- Simulated fantasy leagues (engines/fantasy_league_sim.py): drafts → ADP, rostered/start %, standings, strategy mix
CREATE TABLE IF NOT EXISTS fantasy_sim_strategy_mix (
    season INT NOT NULL,
    strategy VARCHAR(16) NOT NULL,       -- BPA, HERO_RB, ZERO_RB, RB_HEAVY, EARLY_QB, EARLY_TE
    share NUMERIC(6, 4) NOT NULL,        -- the season's target mix (seats are drawn from it)
    managers INT,                        -- seats that actually drew this strategy (of 1,200)
    titles INT,                          -- filled once the fantasy season (week 17) is final
    playoff_teams INT,
    avg_points_for NUMERIC(7, 2),        -- regular season (weeks 1–14)
    PRIMARY KEY (season, strategy)
);

CREATE TABLE IF NOT EXISTS fantasy_sim_teams (
    season INT NOT NULL,
    league SMALLINT NOT NULL,            -- 1..100
    team SMALLINT NOT NULL,              -- 1..12 = draft slot
    strategy VARCHAR(16) NOT NULL,
    activity NUMERIC(4, 3) NOT NULL,     -- chance of each weekly waiver move (0.2–1.0)
    reactivity NUMERIC(4, 3) NOT NULL,   -- how much last week's box score sways him (0–0.5)
    wins INT, losses INT, ties INT,
    points_for NUMERIC(8, 2), points_against NUMERIC(8, 2),
    seed SMALLINT,                       -- after week 14
    playoff_finish VARCHAR(16),          -- CHAMPION, RUNNER_UP, SEMIFINAL, QUARTERFINAL, MISSED
    final_rank SMALLINT,                 -- 1..12 once the playoffs are over
    PRIMARY KEY (season, league, team)
);

CREATE TABLE IF NOT EXISTS fantasy_sim_picks (
    season INT NOT NULL,
    league SMALLINT NOT NULL,
    pick SMALLINT NOT NULL,              -- overall pick 1..180
    round SMALLINT NOT NULL,
    team SMALLINT NOT NULL,
    player_id INT NOT NULL,              -- players.id
    PRIMARY KEY (season, league, pick)
);
CREATE INDEX IF NOT EXISTS idx_fantasy_sim_picks_player ON fantasy_sim_picks (season, player_id);

CREATE TABLE IF NOT EXISTS fantasy_sim_adp (
    season INT NOT NULL,
    player_id INT NOT NULL,
    adp NUMERIC(5, 1) NOT NULL,          -- mean pick over the 100 drafts; undrafted counts as pick 181
    min_pick SMALLINT,                   -- earliest pick (NULL if never drafted)
    max_pick SMALLINT,                   -- latest pick (NULL if never drafted)
    draft_pct NUMERIC(5, 1) NOT NULL,    -- share of leagues that drafted him
    PRIMARY KEY (season, player_id)
);

CREATE TABLE IF NOT EXISTS fantasy_sim_player_weeks (
    season INT NOT NULL,
    week INT NOT NULL,                   -- rosters/lineups going into this week
    player_id INT NOT NULL,
    rostered_pct NUMERIC(5, 1) NOT NULL,
    started_pct NUMERIC(5, 1),           -- NULL when the week had no projections to set lineups from
    PRIMARY KEY (season, week, player_id)
);

CREATE OR REPLACE VIEW vw_fantasy_scores AS
SELECT
    ps.game_id,
    ps.player_name,
    ps.team_id,
    g.season,
    g.week,
    ps.fantasy_points AS ppr_score  -- generated column on player_stats
FROM player_stats ps
JOIN games g ON ps.game_id = g.id;

-- PPR points conceded by each defense per position over completed games,
-- with league benchmarks and a 1-5 matchup star rating (5 = most generous).
CREATE OR REPLACE VIEW vw_fantasy_defense_vs_position AS
WITH game_def AS (
    SELECT
        CASE WHEN ps.team_id = g.home_team_id THEN g.away_team_id ELSE g.home_team_id END AS def_team_id,
        ps.position,
        g.season,
        ps.fantasy_points AS ppr_score
    FROM player_stats ps
    JOIN games g ON ps.game_id = g.id
    WHERE g.status = 'COMPLETED'
), team_pos_totals AS (
    SELECT
        season,
        def_team_id,
        position,
        SUM(ppr_score) AS total_allowed,
        AVG(ppr_score) AS avg_allowed
    FROM game_def
    GROUP BY season, def_team_id, position
), league_pos_benchmarks AS (
    SELECT
        season,
        position,
        MIN(total_allowed) AS min_allowed,
        MAX(total_allowed) AS max_allowed,
        AVG(total_allowed) AS league_avg_allowed
    FROM team_pos_totals
    GROUP BY season, position
)
SELECT
    tpt.season,
    tpt.def_team_id,
    t.abbreviation AS def_team_abbr,
    t.name AS def_team_name,
    tpt.position,
    tpt.total_allowed,
    tpt.avg_allowed,
    lpb.min_allowed,
    lpb.max_allowed,
    lpb.league_avg_allowed,
    NTILE(5) OVER (PARTITION BY tpt.season, tpt.position ORDER BY tpt.total_allowed)::NUMERIC AS matchup_stars
FROM team_pos_totals tpt
JOIN teams t ON tpt.def_team_id = t.id
JOIN league_pos_benchmarks lpb ON tpt.season = lpb.season AND tpt.position = lpb.position;

-- ---------------------------------------------------------------------------
-- Awards
-- ---------------------------------------------------------------------------

-- Head coaches (2026_10_30_coaches.sql)
CREATE TABLE IF NOT EXISTS coaches (
    id SERIAL PRIMARY KEY,
    coach_name VARCHAR(255) UNIQUE NOT NULL,
    offensive_scheme VARCHAR(64),
    defensive_scheme VARCHAR(64),
    -- 2026 tendencies, Low / Medium / High (2026_11_05_coach_tendencies.sql)
    pass_lean VARCHAR(6) CHECK (pass_lean IN ('Low', 'Medium', 'High')),
    fourth_down_aggressiveness VARCHAR(6) CHECK (fourth_down_aggressiveness IN ('Low', 'Medium', 'High')),
    blitz_rate VARCHAR(6) CHECK (blitz_rate IN ('Low', 'Medium', 'High')),
    man_coverage VARCHAR(6) CHECK (man_coverage IN ('Low', 'Medium', 'High'))
);

-- Who coached which team, and when. end_season NULL = still there.
CREATE TABLE IF NOT EXISTS coach_tenures (
    id SERIAL PRIMARY KEY,
    coach_id INT NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    start_season INT NOT NULL,
    end_season INT,
    UNIQUE (coach_id, team_id, start_season),
    CHECK (end_season IS NULL OR end_season >= start_season)
);

-- Career counters as the coach screen shows them at the end of each season (they include
-- pre-franchise NFL careers, so they are stored, not rebuilt from games).
CREATE TABLE IF NOT EXISTS coach_seasons (
    id SERIAL PRIMARY KEY,
    coach_id INT NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
    team_id INT REFERENCES teams(id) ON DELETE CASCADE,
    season INT NOT NULL,
    career_wins INT,
    career_losses INT,
    career_ties INT,
    winning_seasons INT,
    playoff_wins INT,
    playoff_losses INT,
    super_bowl_wins INT,
    super_bowl_losses INT,
    UNIQUE (coach_id, season)
);

-- Each coach's current career totals: the latest stored snapshot (coach_seasons, totals at the end of that season)
-- plus every completed game his team played in later seasons while he held the job (coach_tenures; one head coach
-- per team per season). A winning season counts once the team's 17-game regular season is complete.
CREATE OR REPLACE VIEW coach_records AS
WITH snap AS (
    SELECT DISTINCT ON (coach_id) * FROM coach_seasons ORDER BY coach_id, season DESC
), team_games AS (
    SELECT g.season, g.game_type, t.team_id,
           CASE WHEN t.score > t.opp THEN 1 ELSE 0 END AS win,
           CASE WHEN t.score < t.opp THEN 1 ELSE 0 END AS loss,
           CASE WHEN t.score = t.opp THEN 1 ELSE 0 END AS tie
    FROM games g
    CROSS JOIN LATERAL (VALUES (g.home_team_id, g.home_score, g.away_score),
                               (g.away_team_id, g.away_score, g.home_score)) AS t(team_id, score, opp)
    WHERE g.status = 'COMPLETED'
), coached AS (
    SELECT s.coach_id, tg.*
    FROM snap s
    JOIN coach_tenures ct ON ct.coach_id = s.coach_id
    JOIN team_games tg ON tg.team_id = ct.team_id AND tg.season > s.season
                      AND tg.season >= ct.start_season AND tg.season <= coalesce(ct.end_season, 9999)
), reg AS (
    SELECT coach_id, season, sum(win) AS w, sum(loss) AS l, sum(tie) AS t, count(*) AS games
    FROM coached WHERE game_type = 'REGULAR_SEASON' GROUP BY coach_id, season
), add AS (
    SELECT c.coach_id,
           sum(win) FILTER (WHERE game_type = 'REGULAR_SEASON') AS w,
           sum(loss) FILTER (WHERE game_type = 'REGULAR_SEASON') AS l,
           sum(tie) FILTER (WHERE game_type = 'REGULAR_SEASON') AS t,
           sum(win) FILTER (WHERE game_type <> 'REGULAR_SEASON') AS pw,
           sum(loss) FILTER (WHERE game_type <> 'REGULAR_SEASON') AS pl,
           sum(win) FILTER (WHERE game_type = 'SUPER_BOWL') AS sbw,
           sum(loss) FILTER (WHERE game_type = 'SUPER_BOWL') AS sbl
    FROM coached c GROUP BY c.coach_id
)
SELECT c.id AS coach_id, c.coach_name, s.season AS snapshot_season,
       s.career_wins + coalesce(a.w, 0) AS career_wins,
       s.career_losses + coalesce(a.l, 0) AS career_losses,
       s.career_ties + coalesce(a.t, 0) AS career_ties,
       s.winning_seasons + (SELECT count(*) FROM reg r WHERE r.coach_id = c.id AND r.games >= 17 AND r.w > r.l)
           AS winning_seasons,
       s.playoff_wins + coalesce(a.pw, 0) AS playoff_wins,
       s.playoff_losses + coalesce(a.pl, 0) AS playoff_losses,
       s.super_bowl_wins + coalesce(a.sbw, 0) AS super_bowl_wins,
       s.super_bowl_losses + coalesce(a.sbl, 0) AS super_bowl_losses
FROM coaches c
JOIN snap s ON s.coach_id = c.id
LEFT JOIN add a ON a.coach_id = c.id;

-- Coach of the Year race, rewritten per season by engines/coty_engine.py (2026_11_06_coty_scores.sql)
CREATE TABLE IF NOT EXISTS coty_scores (
    id SERIAL PRIMARY KEY,
    coach_id INT NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    season INT NOT NULL,
    coty_score DOUBLE PRECISION NOT NULL,
    coty_rank INT NOT NULL,
    win_pct DOUBLE PRECISION NOT NULL,
    wins_above_expected DOUBLE PRECISION NOT NULL,
    roster_outperformance DOUBLE PRECISION,
    win_pct_change DOUBLE PRECISION,
    first_year_with_team BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (coach_id, season)
);

CREATE TABLE IF NOT EXISTS awards (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    award_type VARCHAR(50) NOT NULL,
    player_id INT REFERENCES players(id),
    team_id INT REFERENCES teams(id),
    coach_name VARCHAR(255),
    coach_id INT REFERENCES coaches(id),
    player_name VARCHAR(255),
    position VARCHAR(10),
    UNIQUE (season, award_type)
);

-- ---------------------------------------------------------------------------
-- Media & content
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS media_outputs (
    id SERIAL PRIMARY KEY,
    game_id INT REFERENCES games(id) ON DELETE CASCADE,
    content_type VARCHAR(50) NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_memories (
    id SERIAL PRIMARY KEY,
    agent_id VARCHAR(100) UNIQUE,
    archetype VARCHAR(50),
    assigned_team_id INT,       -- teams.id (no FK constraint in live schema)
    persistent_memory TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    handle VARCHAR(100),
    display_name VARCHAR(100),
    bio VARCHAR(200),           -- Agent Wire profile line (db/migrations/2026_10_22_agent_wire_social.sql)
    followers INT,              -- simulated follower count, set once by the social desk
    avatar_color VARCHAR(10)
);

CREATE TABLE IF NOT EXISTS news_feed (
    id SERIAL PRIMARY KEY,
    season INT,
    week INT,
    team_id INT,                -- teams.id (no FK constraint in live schema)
    content_type VARCHAR(50),   -- 'ARTICLE' | 'TWEET'
    persona VARCHAR(50),
    headline VARCHAR(255),
    content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    excerpt TEXT,
    article_type VARCHAR(64),
    slot VARCHAR(32),
    -- Agent Wire posts (db/migrations/2026_10_22_agent_wire_social.sql; docs/SOCIAL_MATRIX.md)
    agent_id VARCHAR(100),      -- agent_memories.agent_id of the author
    post_kind VARCHAR(32),      -- REACTION | PERFORMANCE | BEAT | STANDINGS | SHARE | REPLY | PREVIEW | MEME
    posted_at TIMESTAMP,        -- simulated in-world posting time (created_at = real generation time)
    reply_to_id INT REFERENCES news_feed(id) ON DELETE CASCADE,
    linked_article_id INT REFERENCES news_feed(id) ON DELETE SET NULL,
    subject_game_id INT,
    subject_player_id INT,
    likes INT,
    reposts INT,
    reply_count INT,
    views INT,
    hashtags TEXT[]
);
CREATE INDEX IF NOT EXISTS idx_news_feed_social_week ON news_feed (season, week, content_type, posted_at);
CREATE INDEX IF NOT EXISTS idx_news_feed_agent ON news_feed (agent_id);
CREATE INDEX IF NOT EXISTS idx_news_feed_reply_to ON news_feed (reply_to_id);

-- Agent Wire weekly trends, computed from post tags and hashtags (docs/SOCIAL_MATRIX.md §4)
CREATE TABLE IF NOT EXISTS social_trends (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT NOT NULL,
    rank INT NOT NULL,
    kind VARCHAR(16) NOT NULL,          -- TEAM | PLAYER | GAME | HASHTAG
    key VARCHAR(64) NOT NULL,
    title VARCHAR(120) NOT NULL,
    category VARCHAR(64),
    description TEXT,
    volume INT,
    score DOUBLE PRECISION,
    UNIQUE (season, week, kind, key)
);
CREATE INDEX IF NOT EXISTS idx_social_trends_week ON social_trends (season, week, rank);

-- NFL record book (db/migrations/2026_10_26_nfl_records.sql holds the categories and the real-NFL seed; PROJECT_SPEC §2.9).
CREATE TABLE IF NOT EXISTS record_categories (
    category VARCHAR(40) PRIMARY KEY,
    label TEXT NOT NULL,
    scope VARCHAR(6) NOT NULL CHECK (scope IN ('SEASON', 'GAME')),
    subject VARCHAR(6) NOT NULL CHECK (subject IN ('PLAYER', 'TEAM')),
    better VARCHAR(4) NOT NULL DEFAULT 'HIGH' CHECK (better IN ('HIGH', 'LOW')),
    sort_order INT NOT NULL,
    -- FULL: every franchise's season record and the NFL top 10 through 2025 are seeded; BOOK: only what the league's
    -- record book lists (2026 on). 2026_10_28_league_record_book.sql.
    coverage VARCHAR(4) NOT NULL DEFAULT 'FULL' CHECK (coverage IN ('FULL', 'BOOK'))
);

-- Every franchise record holder there has been, real (source 'NFL', through 2025) or set in this league ('LEAGUE').
CREATE TABLE IF NOT EXISTS franchise_records (
    id SERIAL PRIMARY KEY,
    team_id INT NOT NULL REFERENCES teams(id),
    category VARCHAR(40) NOT NULL REFERENCES record_categories(category),
    value NUMERIC NOT NULL,
    holder TEXT NOT NULL,                         -- player name, or "2007 New England Patriots" for team records
    player_id INT REFERENCES players(id) ON DELETE SET NULL,
    season INT NOT NULL,
    source VARCHAR(6) NOT NULL CHECK (source IN ('NFL', 'BOOK', 'LEAGUE')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (team_id, category, holder, season)
);
CREATE INDEX IF NOT EXISTS idx_franchise_records_team ON franchise_records (team_id, category);

-- Entries on the NFL's all-time top-10 lists (players), real or set in this league; ranks are computed (league_top10).
CREATE TABLE IF NOT EXISTS league_record_entries (
    id SERIAL PRIMARY KEY,
    category VARCHAR(40) NOT NULL REFERENCES record_categories(category),
    value NUMERIC NOT NULL,
    holder TEXT NOT NULL,
    team_id INT REFERENCES teams(id),             -- the franchise, under its current name
    player_id INT REFERENCES players(id) ON DELETE SET NULL,
    season INT NOT NULL,
    game_date DATE,                               -- single-game records (real NFL history)
    game_id INT REFERENCES games(id) ON DELETE CASCADE,   -- single-game records set in this league
    source VARCHAR(6) NOT NULL CHECK (source IN ('NFL', 'BOOK', 'LEAGUE')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_league_record_entries
    ON league_record_entries (category, holder, season, COALESCE(game_date, DATE '1900-01-01'), COALESCE(game_id, 0));

-- The current holder(s) of each franchise record (ties included).
CREATE OR REPLACE VIEW franchise_records_current AS
SELECT fr.*, c.label, c.scope, c.subject, c.better
FROM franchise_records fr
JOIN record_categories c ON c.category = fr.category
WHERE fr.value = (SELECT CASE WHEN c.better = 'LOW' THEN MIN(x.value) ELSE MAX(x.value) END
                  FROM franchise_records x WHERE x.team_id = fr.team_id AND x.category = fr.category);

-- The NFL's all-time top 10 per category (ties included).
CREATE OR REPLACE VIEW league_top10 AS
SELECT * FROM (
    SELECT e.*, c.label, c.scope, c.better,
           RANK() OVER (PARTITION BY e.category
                        ORDER BY CASE WHEN c.better = 'LOW' THEN e.value ELSE -e.value END) AS rank
    FROM league_record_entries e JOIN record_categories c ON c.category = e.category
) ranked WHERE rank <= 10;

CREATE VIEW league_records AS
WITH cands AS (
    SELECT fr.category, fr.value, fr.holder, fr.team_id, fr.player_id, fr.season, NULL::date AS game_date, NULL::int AS game_id, fr.source
    FROM franchise_records_current fr JOIN record_categories c ON c.category = fr.category
    WHERE c.scope = 'SEASON' AND c.coverage = 'FULL'
    UNION
    SELECT t.category, t.value, t.holder, t.team_id, t.player_id, t.season, t.game_date, t.game_id, t.source
    FROM league_top10 t WHERE t.rank = 1
), best AS (
    SELECT c.category, CASE WHEN rc.better = 'LOW' THEN MIN(c.value) ELSE MAX(c.value) END AS value
    FROM cands c JOIN record_categories rc ON rc.category = c.category GROUP BY c.category, rc.better
)
SELECT DISTINCT ON (c.category, c.holder, c.season)
       c.category, rc.label, rc.scope, rc.subject, c.value, c.holder, c.team_id, c.player_id, c.season,
       c.game_date, c.game_id, c.source
FROM cands c
JOIN best b ON b.category = c.category AND b.value = c.value
JOIN record_categories rc ON rc.category = c.category
ORDER BY c.category, c.holder, c.season, c.game_date NULLS LAST;

-- Weekly Power Rankings parsed from the POWER_RANKINGS article (newsroom/power_rankings.py)
CREATE TABLE IF NOT EXISTS power_rankings (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT NOT NULL,                -- the week the ranking was published for (entering that week)
    rank INT NOT NULL CHECK (rank BETWEEN 1 AND 32),
    team_id INT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    previous_rank INT,                -- rank in the latest earlier stored week of the season; NULL if none
    movement INT,                     -- previous_rank - rank (positive = moved up); NULL if no previous
    record VARCHAR(10),               -- W-L snapshot from team_seasons when stored
    kicker TEXT,                      -- the bold thematic lead-in
    blurb TEXT,
    article_id INT REFERENCES news_feed(id) ON DELETE CASCADE,
    persona VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (season, week, team_id),
    UNIQUE (season, week, rank)
);
CREATE INDEX IF NOT EXISTS idx_power_rankings_season_week ON power_rankings (season, week);

-- Relational entity tagging for articles
CREATE TABLE IF NOT EXISTS article_teams (
    id SERIAL PRIMARY KEY,
    article_id INT NOT NULL REFERENCES news_feed(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    mention_type VARCHAR(20) DEFAULT 'MENTIONED',  -- 'PRIMARY' | 'FEATURED' | 'MENTIONED'
    mention_count INT DEFAULT 1,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (article_id, team_id)
);

CREATE TABLE IF NOT EXISTS article_players (
    id SERIAL PRIMARY KEY,
    article_id INT NOT NULL REFERENCES news_feed(id) ON DELETE CASCADE,
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    team_id INT REFERENCES teams(id) ON DELETE SET NULL,
    mention_type VARCHAR(20) DEFAULT 'MENTIONED',  -- 'PRIMARY' | 'FEATURED' | 'MENTIONED'
    mention_count INT DEFAULT 1,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (article_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_article_teams_article_id ON article_teams(article_id);
CREATE INDEX IF NOT EXISTS idx_article_teams_team_id ON article_teams(team_id);
CREATE INDEX IF NOT EXISTS idx_article_players_article_id ON article_players(article_id);
CREATE INDEX IF NOT EXISTS idx_article_players_player_id ON article_players(player_id);

-- Evolving agent memory (newsroom/agent_memory.py): takes, prediction track record, season summaries
CREATE TABLE IF NOT EXISTS agent_memory_entries (
    id SERIAL PRIMARY KEY,
    agent_id VARCHAR(100) NOT NULL,          -- agent_memories.agent_id
    season INT NOT NULL,
    week INT NOT NULL,                       -- publication week of the source article/tweet
    article_id INT REFERENCES news_feed(id) ON DELETE CASCADE,
    kind VARCHAR(20) NOT NULL DEFAULT 'TAKE',
    text TEXT NOT NULL,
    team_id INT REFERENCES teams(id) ON DELETE SET NULL,
    player_id INT REFERENCES players(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_memory_entries_agent ON agent_memory_entries (agent_id, season, week);
CREATE INDEX IF NOT EXISTS idx_agent_memory_entries_team ON agent_memory_entries (team_id);

CREATE TABLE IF NOT EXISTS agent_predictions (
    id SERIAL PRIMARY KEY,
    agent_id VARCHAR(100) NOT NULL,
    season INT NOT NULL,
    week INT NOT NULL,                       -- week of the predicted game
    article_id INT REFERENCES news_feed(id) ON DELETE CASCADE,
    game_id INT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    label VARCHAR(60) NOT NULL DEFAULT '',  -- roundtable panellist name; '' otherwise
    team_a_id INT NOT NULL,
    score_a INT NOT NULL,
    team_b_id INT NOT NULL,
    score_b INT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (article_id, game_id, label)
);
CREATE INDEX IF NOT EXISTS idx_agent_predictions_agent ON agent_predictions (agent_id, season, week);

CREATE TABLE IF NOT EXISTS agent_season_summaries (
    agent_id VARCHAR(100) NOT NULL,
    season INT NOT NULL,
    through_week INT NOT NULL,               -- covers takes and results through this week
    summary TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (agent_id, season, through_week)
);
CREATE INDEX IF NOT EXISTS idx_article_players_team_id ON article_players(team_id);

-- ---------------------------------------------------------------------------
-- Triggers
-- ---------------------------------------------------------------------------

-- Recompute both teams' W-L in team_seasons whenever a game is inserted or its
-- status/score changes.
CREATE OR REPLACE FUNCTION update_team_records()
RETURNS TRIGGER AS $$
BEGIN
    -- team_seasons wins/losses are the regular-season record: playoff games don't count (2026_11_04)
    IF TG_OP = 'INSERT' OR
       (TG_OP = 'UPDATE' AND (NEW.status != OLD.status OR NEW.home_score != OLD.home_score OR NEW.away_score != OLD.away_score)) THEN

        -- Recalculate Home Team
        INSERT INTO team_seasons (team_id, season, wins, losses)
        VALUES (
            NEW.home_team_id, NEW.season,
            (SELECT COUNT(*) FROM games g WHERE g.season = NEW.season AND g.status = 'COMPLETED' AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON' AND ((g.home_team_id = NEW.home_team_id AND g.home_score > g.away_score) OR (g.away_team_id = NEW.home_team_id AND g.away_score > g.home_score))),
            (SELECT COUNT(*) FROM games g WHERE g.season = NEW.season AND g.status = 'COMPLETED' AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON' AND ((g.home_team_id = NEW.home_team_id AND g.home_score < g.away_score) OR (g.away_team_id = NEW.home_team_id AND g.away_score < g.home_score)))
        )
        ON CONFLICT (team_id, season) DO UPDATE SET
            wins = EXCLUDED.wins, losses = EXCLUDED.losses;

        -- Recalculate Away Team
        INSERT INTO team_seasons (team_id, season, wins, losses)
        VALUES (
            NEW.away_team_id, NEW.season,
            (SELECT COUNT(*) FROM games g WHERE g.season = NEW.season AND g.status = 'COMPLETED' AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON' AND ((g.home_team_id = NEW.away_team_id AND g.home_score > g.away_score) OR (g.away_team_id = NEW.away_team_id AND g.away_score > g.home_score))),
            (SELECT COUNT(*) FROM games g WHERE g.season = NEW.season AND g.status = 'COMPLETED' AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON' AND ((g.home_team_id = NEW.away_team_id AND g.home_score < g.away_score) OR (g.away_team_id = NEW.away_team_id AND g.away_score < g.home_score)))
        )
        ON CONFLICT (team_id, season) DO UPDATE SET
            wins = EXCLUDED.wins, losses = EXCLUDED.losses;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_team_records ON games;
CREATE TRIGGER trigger_update_team_records
AFTER INSERT OR UPDATE ON games
FOR EACH ROW
EXECUTE FUNCTION update_team_records();

-- When a player's team changes for a season, move their season_stats rows
-- (team_id NULL or the old team) to the new team, merging into any existing
-- row for the new team so the season_stats UNIQUE key is never violated.
CREATE OR REPLACE FUNCTION sync_team_to_season_stats()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.team_id IS DISTINCT FROM OLD.team_id THEN

        -- 1. For any NULL team_id rows where the target team ALREADY exists, we must MERGE the stats.
        UPDATE season_stats target
        SET
            completions = COALESCE(target.completions, 0) + COALESCE(source.completions, 0),
            pass_attempts = COALESCE(target.pass_attempts, 0) + COALESCE(source.pass_attempts, 0),
            pass_yards = COALESCE(target.pass_yards, 0) + COALESCE(source.pass_yards, 0),
            pass_tds = COALESCE(target.pass_tds, 0) + COALESCE(source.pass_tds, 0),
            interceptions = COALESCE(target.interceptions, 0) + COALESCE(source.interceptions, 0),
            rush_attempts = COALESCE(target.rush_attempts, 0) + COALESCE(source.rush_attempts, 0),
            rush_yards = COALESCE(target.rush_yards, 0) + COALESCE(source.rush_yards, 0),
            rush_tds = COALESCE(target.rush_tds, 0) + COALESCE(source.rush_tds, 0),
            receptions = COALESCE(target.receptions, 0) + COALESCE(source.receptions, 0),
            rec_yards = COALESCE(target.rec_yards, 0) + COALESCE(source.rec_yards, 0),
            rec_tds = COALESCE(target.rec_tds, 0) + COALESCE(source.rec_tds, 0),
            fumb = COALESCE(target.fumb, 0) + COALESCE(source.fumb, 0)
        FROM season_stats source, players p
        WHERE source.player_name = p.player_name AND p.id = NEW.player_id
          AND source.season = NEW.season AND (source.team_id IS NULL OR source.team_id = OLD.team_id)
          AND target.player_name = source.player_name AND target.season = source.season
          AND target.team_id = NEW.team_id AND target.position = source.position;

        -- 2. Delete the source rows that were just merged
        DELETE FROM season_stats source
        USING players p
        WHERE source.player_name = p.player_name AND p.id = NEW.player_id
          AND source.season = NEW.season AND (source.team_id IS NULL OR source.team_id = OLD.team_id)
          AND EXISTS (
              SELECT 1 FROM season_stats target
              WHERE target.player_name = source.player_name
                AND target.season = source.season
                AND target.team_id = NEW.team_id
                AND target.position = source.position
          );

        -- 3. Now safe to update the remaining rows that had no collision
        UPDATE season_stats
        SET team_id = NEW.team_id
        FROM players p
        WHERE season_stats.player_name = p.player_name
          AND p.id = NEW.player_id
          AND season_stats.season = NEW.season
          AND (season_stats.team_id IS NULL OR season_stats.team_id = OLD.team_id);

    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_sync_season_stats_team ON player_seasons;
CREATE TRIGGER trigger_sync_season_stats_team
AFTER UPDATE OF team_id ON player_seasons
FOR EACH ROW
EXECUTE FUNCTION sync_team_to_season_stats();

-- player_id keys and the trigger that fills player_id on name-only inserts (old ingestion,
-- backfill); added on existing databases by db/migrations/2026_10_24_player_id_keys.py after the roster load

CREATE OR REPLACE FUNCTION fill_player_id_from_roster() RETURNS trigger AS $$
DECLARE
    yr INT;
BEGIN
    IF NEW.player_id IS NOT NULL THEN
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'player_stats' THEN
        SELECT season INTO yr FROM games WHERE id = NEW.game_id;
    ELSE
        yr := NEW.season;
    END IF;
    -- the player on that team and position whose list name matches; one match only
    SELECT p.id INTO NEW.player_id
    FROM players p JOIN player_seasons s ON s.player_id = p.id AND s.season = yr
    WHERE s.team_id = NEW.team_id AND s.position = NEW.position
      AND regexp_replace(upper(p.player_name), '[^A-Z]', '', 'g') = regexp_replace(upper(NEW.player_name), '[^A-Z]', '', 'g')
    GROUP BY p.id HAVING count(*) = 1 LIMIT 1;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS season_stats_fill_player_id ON season_stats;
CREATE TRIGGER season_stats_fill_player_id BEFORE INSERT ON season_stats
    FOR EACH ROW EXECUTE FUNCTION fill_player_id_from_roster();
DROP TRIGGER IF EXISTS player_stats_fill_player_id ON player_stats;
CREATE TRIGGER player_stats_fill_player_id BEFORE INSERT ON player_stats
    FOR EACH ROW EXECUTE FUNCTION fill_player_id_from_roster();

CREATE UNIQUE INDEX IF NOT EXISTS season_stats_season_player_id ON season_stats (season, player_id) WHERE player_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS player_stats_game_player_id ON player_stats (game_id, player_id) WHERE player_id IS NOT NULL;

-- Views over the capture loader tables (2026_10_23)
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

