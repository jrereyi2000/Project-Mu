-- Project Mu: PostgreSQL schema bootstrap (dbname=madden).
--
-- Reproduces the live schema (tables, constraints, generated columns, views,
-- functions, triggers) on an empty database. Schema only, no data.
-- Safe to re-run: IF NOT EXISTS / CREATE OR REPLACE / DROP TRIGGER IF EXISTS.
-- Note: IF NOT EXISTS will not add columns to a table that already exists;
-- columns added later must ship as migrations under db/migrations/.
--
-- Column order matches the live franchise database `madden_mu2026` column for column, so
-- `pg_dump --schema-only` of a fresh database matches it (checked 2026-11-09). Columns a
-- migration appends go last, where the migration put them. `python3 -m db.migrate diff` keys
-- columns by name, so it does not check order and will not catch a column placed mid-table.
-- Apply with:
--   docker exec -i madden-db psql -U postgres -d <db> -v ON_ERROR_STOP=1 < db/init_db.sql
--
-- The file ends with a generated `schema_migrations` block (db/migrate.py): because this file mirrors every
-- migration, a database built from it is at the head of db/migrations/, reports nothing pending from
-- `python3 -m db.migrate status`, and treats `apply` as a no-op. Regenerate that block with
-- `python3 -m db.migrate sync-init-db` after adding a migration.

-- ---------------------------------------------------------------------------
-- Core entities & league state
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS league_state (
    id INT NOT NULL DEFAULT 1 PRIMARY KEY,
    current_season INT NOT NULL DEFAULT 2024,
    current_week INT NOT NULL DEFAULT 1,
    madden_league_id BIGINT                -- the save's Franchise.LeagueID (db/franchises.py)
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
    espn_id INT,
    madden_pgid INT    -- Player.PresentationId on the PC save (2027_01_05_madden_pgid.sql)
);
CREATE UNIQUE INDEX IF NOT EXISTS players_madden_pgid ON players (madden_pgid) WHERE madden_pgid IS NOT NULL;

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
    -- Observed offensive tendencies (engines/tendency_engine.py, 2026_11_13_observed_tendencies.sql)
    pass_rate_oe DOUBLE PRECISION, fourth_down_oe DOUBLE PRECISION,
    observed_pass_lean VARCHAR(6) CHECK (observed_pass_lean IN ('Low', 'Medium', 'High')),
    observed_fourth_down VARCHAR(6) CHECK (observed_fourth_down IN ('Low', 'Medium', 'High')),
    tendency_games INT,
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
    -- League transactions (2026_12_08_transactions.sql): set = released (free agent) since that week; team_id kept
    released_week INT,
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
    -- CAPTURED box score | WEEKLY = exact one-game backfill | DISTRIBUTED = synthetic (2026_11_14_player_stats_line_source.sql)
    line_source VARCHAR(12) CHECK (line_source IN ('CAPTURED', 'WEEKLY', 'DISTRIBUTED')),
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
    ) STORED
);
-- The team is in the key: two players can share an abbreviated name and position (J. Williams, HB, on two
-- teams) and Madden has them in the same game (2027_02_23_player_stats_team_key.sql, whose named index
-- this mirrors — a fresh database must carry the migrated object name, not a table-level UNIQUE).
CREATE UNIQUE INDEX IF NOT EXISTS player_stats_game_name_position_team
    ON player_stats (game_id, player_name, position, team_id);

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
    mu_weeks_out SMALLINT,                             -- Project Mu's duration, written back (2027_01_14_v2_franchise_os.sql)
    CONSTRAINT uq_injuries_season_week_team_name UNIQUE(season, week, team_id, player_name)
);
CREATE INDEX IF NOT EXISTS idx_injuries_season_week ON injuries(season, week);
CREATE INDEX IF NOT EXISTS idx_injuries_team ON injuries(team_id);
CREATE INDEX IF NOT EXISTS idx_injuries_player ON injuries(player_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_injuries_unique_player ON injuries(season, week, player_id) WHERE player_id IS NOT NULL;

-- 5c. Game scripts (engines/game_script_engine.py, 2026_11_18_game_scripts.sql): one canonical story per completed game.
CREATE TABLE IF NOT EXISTS game_scripts (
    game_id INTEGER PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    version INTEGER NOT NULL,
    facts_hash TEXT NOT NULL,
    script JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_game_scripts_season_week ON game_scripts(season, week);

-- 5d. Game highlights (scanned from in-game postgame View Highlights screen).
CREATE TABLE IF NOT EXISTS game_highlights (
    game_id INTEGER PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    highlights JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_game_highlights_season_week ON game_highlights(season, week);

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

-- Native player ratings (2027_01_06_player_attributes.sql): 54 Madden 27 ratings per player, a season baseline row plus a row per in-season change
CREATE TABLE IF NOT EXISTS player_attributes (
    id SERIAL PRIMARY KEY,
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    season INT NOT NULL,
    team_id INT REFERENCES teams(id),
    capture_folder VARCHAR(255) NOT NULL,             -- captures/<run> or save_<season> from pipeline/load_save.py
    position VARCHAR(10),
    age INT,
    overall_rating INT,                               -- OVR from the save
    predicted_ovr INT,                                -- engines/ovr_weights from the ratings
    ovr_check VARCHAR(16),                            -- ok | off by N | n/a
    confirmed SMALLINT,                               -- count of ratings stored (54 for native PC save rows)
    ratings JSONB NOT NULL,                           -- {"Speed": 99, ...} Player.<field>Rating names
    flags TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (player_id, capture_folder));
CREATE INDEX IF NOT EXISTS player_attributes_season ON player_attributes (season, player_id);

-- Player name aliases (2027_01_07_player_name_aliases.sql): tracks former names on commentary renames
CREATE TABLE IF NOT EXISTS player_name_aliases (
    player_id INT REFERENCES players(id) ON DELETE CASCADE,
    alias VARCHAR(128) NOT NULL,
    kind VARCHAR(32) NOT NULL DEFAULT 'commentary_rename',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (player_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_player_name_aliases_alias ON player_name_aliases(alias);

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

-- 8. Contracts and cap from Manage Roster > TEAM SALARY (db/migrations/2026_12_07_contracts.sql,
-- docs/done/TEAM_MANAGEMENT_PLAN.md §2.1). Money is whole dollars (pipeline/money.parse_money).
-- The TEAM SALARY header strip, one row per team and season. The latest scan wins.
CREATE TABLE IF NOT EXISTS team_cap_seasons (
    team_id INT NOT NULL REFERENCES teams(id),
    season INT NOT NULL,
    cap BIGINT,
    rollover BIGINT,
    adjusted_cap BIGINT,
    cap_space BIGINT,                                 -- can be negative
    salaries BIGINT,
    cap_penalty BIGINT,
    total_spend BIGINT,
    next_rookie_reserve BIGINT,
    scanned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    capture_id INT REFERENCES captures(id) ON DELETE SET NULL,
    PRIMARY KEY (team_id, season)
);

-- One row per contract as scanned. A team's re-scan marks its earlier ACTIVE rows SUPERSEDED and inserts fresh
-- ones; rows are never updated in place otherwise, so step 2 can see what changed. Step 2 marks RELEASED/TRADED
-- and inserts source = 'TRANSACTION' rows. season = the league season the scan was taken in.
CREATE TABLE IF NOT EXISTS player_contracts (
    id SERIAL PRIMARY KEY,
    player_id INT NOT NULL REFERENCES players(id),
    team_id INT NOT NULL REFERENCES teams(id),
    season INT NOT NULL,
    length INT,
    years_remaining INT,
    total_value BIGINT,
    bonus BIGINT,
    cut_savings BIGINT,
    cut_penalty BIGINT,
    no_trade_clause BOOLEAN NOT NULL DEFAULT false,
    incentive TEXT,                                   -- NONE on screen -> NULL
    status VARCHAR(12) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','SUPERSEDED','RELEASED','TRADED')),
    source VARCHAR(12) NOT NULL DEFAULT 'SCAN' CHECK (source IN ('SCAN','TRANSACTION')),
    scanned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    capture_id INT REFERENCES captures(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS player_contracts_one_active ON player_contracts (player_id, season)
    WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS player_contracts_team_season ON player_contracts (team_id, season, status);

-- One row per year column on screen (five: season .. season+4); a $0 year is stored as 0. The contract ends
-- in season + years_remaining - 1, not where the first $0 falls.
CREATE TABLE IF NOT EXISTS player_contract_years (
    contract_id INT NOT NULL REFERENCES player_contracts(id) ON DELETE CASCADE,
    year INT NOT NULL,                                -- calendar year, e.g. 2036
    cap_hit BIGINT NOT NULL,
    PRIMARY KEY (contract_id, year)
);

-- Active contracts with each player's yearly cap hits (relative years y0 = season .. y4 = season+4) next to the
-- team's cap row. Steps 3 (GM agents) and 5 (newsroom) read this view.
CREATE OR REPLACE VIEW vw_team_cap_sheet AS
SELECT c.team_id,
       t.abbreviation,
       c.season,
       c.id AS contract_id,
       c.player_id,
       p.player_name,
       ps.position,
       c.length,
       c.years_remaining,
       c.season + c.years_remaining - 1 AS contract_end_season,
       c.total_value,
       c.bonus,
       c.cut_savings,
       c.cut_penalty,
       c.no_trade_clause,
       c.incentive,
       (SELECT y.cap_hit FROM player_contract_years y WHERE y.contract_id = c.id AND y.year = c.season)     AS cap_hit_y0,
       (SELECT y.cap_hit FROM player_contract_years y WHERE y.contract_id = c.id AND y.year = c.season + 1) AS cap_hit_y1,
       (SELECT y.cap_hit FROM player_contract_years y WHERE y.contract_id = c.id AND y.year = c.season + 2) AS cap_hit_y2,
       (SELECT y.cap_hit FROM player_contract_years y WHERE y.contract_id = c.id AND y.year = c.season + 3) AS cap_hit_y3,
       (SELECT y.cap_hit FROM player_contract_years y WHERE y.contract_id = c.id AND y.year = c.season + 4) AS cap_hit_y4,
       tc.cap,
       tc.adjusted_cap,
       tc.cap_space,
       tc.total_spend,
       tc.next_rookie_reserve
FROM player_contracts c
JOIN teams t ON t.id = c.team_id
JOIN players p ON p.id = c.player_id
LEFT JOIN player_seasons ps ON ps.player_id = c.player_id AND ps.season = c.season
LEFT JOIN team_cap_seasons tc ON tc.team_id = c.team_id AND tc.season = c.season
WHERE c.status = 'ACTIVE';

-- 9. League transactions (2026_12_08_transactions.sql, docs/done/team_management/02_LEAGUE_TRANSACTIONS_PLAN.md).
-- One row per list row. The list gives no date finer than its heading ("2036 SEASON - WEEK 4") and identical
-- rows are legitimate, so the key is (season, heading, normalized row, seq): seq counts equal rows within a
-- heading group from the oldest up, so it stays stable when newer rows appear at the top of the list.
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT,                                          -- games.week; NULL for preseason / offseason headings
    txn_date TEXT NOT NULL,                            -- the heading after OCR clean-up, as Madden shows it
    type VARCHAR(12) NOT NULL CHECK (type IN ('SIGNED', 'RELEASED', 'TRADED', 'RE_SIGNED', 'EXTENDED', 'RESTRUCTURED', 'RETIRED', 'OTHER')),
    player_id INT REFERENCES players(id) ON DELETE SET NULL,
    player_name TEXT,                                  -- as the row spells it ("DEVONTA TAYLOR")
    position VARCHAR(6),
    from_team_id INT REFERENCES teams(id),             -- RELEASED / TRADED: the team losing the player
    to_team_id INT REFERENCES teams(id),               -- SIGNED / TRADED: the team gaining him; RE_SIGNED / EXTENDED: his team
    contract_years SMALLINT,
    contract_value BIGINT,                             -- whole dollars ($3.93M -> 3930000)
    raw_text TEXT NOT NULL,
    row_key TEXT NOT NULL,                             -- normalized key (parsed fields, or raw: + folded text)
    seq SMALLINT NOT NULL DEFAULT 1,
    parse_ok BOOLEAN NOT NULL,
    flags TEXT,                                        -- extraction / load flags, comma-separated
    applied BOOLEAN NOT NULL DEFAULT TRUE,             -- false: stored as history only (before the effects cutoff)
    trade_group TEXT,                                  -- links the legs of one trade
    gm_proposal_id INT,                                -- step 3 (GM agents) sets it and adds the FK
    capture_id INT REFERENCES captures(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (season, txn_date, row_key, seq)
);
CREATE INDEX IF NOT EXISTS idx_transactions_season_week ON transactions (season, week);
CREATE INDEX IF NOT EXISTS idx_transactions_player ON transactions (player_id);
CREATE INDEX IF NOT EXISTS idx_transactions_trade_group ON transactions (trade_group);

-- Contract and cap bookkeeping from transactions (no automatic Team Salary re-scans, JR 2026-10-19; the next
-- Season scan replaces all of it). Needs step 1's 2026_12_07_contracts.sql.
-- A re-signing / extension keeps the current contract and records the new deal's terms on it; its projected
-- cap hits are player_contract_years rows marked projected (the years after the current deal ends).
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS extension_years SMALLINT;
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS extension_value BIGINT;
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS extended_transaction_id INT
    REFERENCES transactions(id) ON DELETE SET NULL;
ALTER TABLE player_contract_years ADD COLUMN IF NOT EXISTS projected BOOLEAN NOT NULL DEFAULT false;
-- Cap rows kept current from transaction deltas (signing, release dead money, trade) between scans:
-- derived_at after scanned_at means the row is derived, not read off the screen.
ALTER TABLE team_cap_seasons ADD COLUMN IF NOT EXISTS derived_at TIMESTAMPTZ;
ALTER TABLE team_cap_seasons ADD COLUMN IF NOT EXISTS derived_transaction_id INT
    REFERENCES transactions(id) ON DELETE SET NULL;

-- V2 franchise OS core (2027_01_14_v2_franchise_os.sql, docs/done/V2_CORE_BUILD_PLAN.md §2.1).
-- Contract instruments beyond Madden's plain deal.
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS contract_type VARCHAR(16) NOT NULL DEFAULT 'STANDARD'
    CHECK (contract_type IN ('STANDARD','ROOKIE','UDFA','FIFTH_YEAR_OPTION','FRANCHISE_EXCL','FRANCHISE_NONEXCL',
                             'TRANSITION','RFA_TENDER','ERFA_TENDER','MINIMUM'));
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS guaranteed BIGINT NOT NULL DEFAULT 0;
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS signed_season INT;
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS signed_week INT;
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS last_restructure_season INT;
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS drafted_round SMALLINT;   -- NULL = undrafted

-- Depth charts as Madden holds them (MADDEN), as Project Mu's coaches set them (MU_COACH) or as JR set them (JR).
CREATE TABLE IF NOT EXISTS depth_charts (
    team_id INT NOT NULL REFERENCES teams(id),
    season INT NOT NULL,
    week INT NOT NULL,
    position_slot VARCHAR(8) NOT NULL,      -- Madden's DepthChart field name: QB, HB, WR, ..., KR, PR, 3DRB, ...
    depth SMALLINT NOT NULL CHECK (depth BETWEEN 1 AND 6),
    player_id INT REFERENCES players(id),
    source VARCHAR(8) NOT NULL CHECK (source IN ('MADDEN','MU_COACH','JR')),
    PRIMARY KEY (team_id, season, week, source, position_slot, depth)
);

-- Every offer or proposal: free agency, re-signing, extension, tender, JR's or a CPU team's.
CREATE TABLE IF NOT EXISTS contract_offers (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    stage VARCHAR(16) NOT NULL,             -- league_office stage name
    wave SMALLINT,                          -- free-agency wave 1..3, NULL otherwise
    kind VARCHAR(12) NOT NULL CHECK (kind IN ('FREE_AGENT','RESIGN','EXTENSION','TENDER','UDFA')),
    player_id INT NOT NULL REFERENCES players(id),
    team_id INT NOT NULL REFERENCES teams(id),
    from_user BOOLEAN NOT NULL DEFAULT false,
    years SMALLINT NOT NULL,
    total_value BIGINT NOT NULL,
    bonus BIGINT NOT NULL DEFAULT 0,
    guaranteed BIGINT NOT NULL DEFAULT 0,
    utility NUMERIC(6,4),                   -- free_agency_engine.offer_utility, NULL until scored
    status VARCHAR(10) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN','ACCEPTED','REJECTED','COUNTERED','WITHDRAWN','EXPIRED')),
    counter JSONB,                          -- {years, total_value, bonus, guaranteed} when COUNTERED
    seed TEXT NOT NULL,
    week SMALLINT,                          -- engine week the offer was made (2027_02_18); NULL = undated
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS contract_offers_player ON contract_offers (player_id, season, status);

CREATE TABLE IF NOT EXISTS waiver_claims (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL, week INT NOT NULL,
    player_id INT NOT NULL REFERENCES players(id),
    team_id INT NOT NULL REFERENCES teams(id),
    priority SMALLINT NOT NULL,             -- 1 = worst record
    awarded BOOLEAN NOT NULL DEFAULT false
);

-- JR's queued moves from Mu_Network, applied at the next league office write.
CREATE TABLE IF NOT EXISTS franchise_pending_moves (
    id SERIAL PRIMARY KEY,
    team_id INT NOT NULL REFERENCES teams(id),
    kind VARCHAR(16) NOT NULL CHECK (kind IN ('DEPTH_CHART','RELEASE','SIGN','TRADE','PS_PROMOTE','PS_DEMOTE',
                                              'IR_PLACE','IR_ACTIVATE','RESTRUCTURE','EXTENSION','TAG','OPTION')),
    payload JSONB NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','WRITTEN','REFUSED','CANCELLED')),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    written_run INT
);

-- One row per league office run, and per save write inside it.
CREATE TABLE IF NOT EXISTS league_office_runs (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL, week INT, stage VARCHAR(16) NOT NULL,
    save_name TEXT NOT NULL,
    status VARCHAR(10) NOT NULL CHECK (status IN ('PLANNED','WRITTEN','FAILED','DRY_RUN')),
    edits JSONB NOT NULL,                   -- the exact save_writer edits document
    summary JSONB,                          -- counts per move kind
    drift JSONB,                            -- drift_check report before the write
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

-- Head coaches & coordinators (coaches, coach_tenures, coordinators, coordinator_tenures, coach_seasons)
CREATE TABLE IF NOT EXISTS coaches (
    id SERIAL PRIMARY KEY,
    coach_name VARCHAR(255) UNIQUE NOT NULL,
    slant VARCHAR(16) NOT NULL DEFAULT 'OFFENSIVE' CHECK (slant IN ('OFFENSIVE', 'DEFENSIVE', 'CEO')),
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

-- Coordinators and staff tenures (2026_11_25_coordinators_and_slants.sql, docs/done/COACH_SLANTS_AND_COORDINATORS_PLAN.md)
CREATE TABLE IF NOT EXISTS coordinators (
    id SERIAL PRIMARY KEY,
    coordinator_name VARCHAR(255) NOT NULL,
    side VARCHAR(16) NOT NULL CHECK (side IN ('OFFENSIVE', 'DEFENSIVE')),
    scheme VARCHAR(64) NOT NULL,
    playbook VARCHAR(64),
    pass_lean VARCHAR(6) CHECK (pass_lean IN ('Low', 'Medium', 'High')),
    blitz_rate VARCHAR(6) CHECK (blitz_rate IN ('Low', 'Medium', 'High')),
    man_coverage VARCHAR(6) CHECK (man_coverage IN ('Low', 'Medium', 'High')),
    origin_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS coordinator_tenures (
    id SERIAL PRIMARY KEY,
    coordinator_id INT NOT NULL REFERENCES coordinators(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    role VARCHAR(8) NOT NULL CHECK (role IN ('OC', 'DC')),
    start_season INT NOT NULL,
    end_season INT,
    UNIQUE (coordinator_id, team_id, role, start_season),
    CHECK (end_season IS NULL OR end_season >= start_season)
);
CREATE INDEX IF NOT EXISTS idx_coord_tenures_team_season ON coordinator_tenures(team_id, start_season, coalesce(end_season, 9999));

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

-- The Madden coach record Mu builds for every coach or coordinator it invents
-- (2027_02_21_coach_madden_profiles.sql; COACH_SAVE_WRITES_PLAN §2.1; PROJECT_SPEC §2.19/§3.3a).
-- One row per Mu person, holding every Madden Coach field Mu writes plus the link to the save record.
CREATE TABLE IF NOT EXISTS coach_madden_profiles (
    id SERIAL PRIMARY KEY,
    person_kind VARCHAR(5) NOT NULL CHECK (person_kind IN ('COACH', 'COORD')),   -- coaches.id or coordinators.id
    person_id INT NOT NULL,
    source VARCHAR(8) NOT NULL CHECK (source IN ('SAVE', 'CREATED')),            -- read from the save, or built by Mu
    presentation_id INT,          -- Coach.PresentationId of the linked save record (NULL until written or linked)
    first_name VARCHAR(64) NOT NULL,
    last_name VARCHAR(64) NOT NULL,
    age INT NOT NULL,
    position VARCHAR(24) NOT NULL,            -- StaffPosition: HeadCoach | OffensiveCoordinator | DefensiveCoordinator
    archetype VARCHAR(32) NOT NULL,           -- StaffArchetypeEnum
    specialty VARCHAR(16),                    -- CoachSpecialty
    level INT NOT NULL DEFAULT 1,
    years_coaching INT,
    offensive_playbook VARCHAR(64), defensive_playbook VARCHAR(64),
    offensive_scheme VARCHAR(64), defensive_scheme VARCHAR(64),
    team_philosophy VARCHAR(32),
    personality VARCHAR(32), demeanor VARCHAR(32),
    body_type VARCHAR(24), height INT, weight INT, face_shape VARCHAR(32), generic_head VARCHAR(64),
    talents JSONB NOT NULL DEFAULT '[]',      -- [{"slot": "Gameday"|"Playsheet", "talent": <name>, "tier": n}]
    ratings JSONB NOT NULL DEFAULT '{}',      -- {"COACH_QB": n, ..., "COACH_RATING": n}
    tendencies JSONB NOT NULL DEFAULT '{}',   -- the COACH_*TENDENCY* fields
    visuals_template INT,                     -- PresentationId of the save coach whose CharacterVisuals/loadouts were cloned
    portrait_id INT,                          -- Coach.Portrait (0 = silhouette until the pack is installed)
    portrait_status VARCHAR(10) NOT NULL DEFAULT 'NONE' CHECK (portrait_status IN ('NONE', 'GENERATED', 'PACKED', 'INSTALLED')),
    headshot_path TEXT,                       -- static/coaches/<coach_key>.webp
    seed TEXT NOT NULL,                       -- the draw's seed (same seed, same profile)
    written_at TIMESTAMP,                     -- last time staff.mjs wrote or verified this record
    UNIQUE (person_kind, person_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS coach_madden_profiles_pid ON coach_madden_profiles (presentation_id)
    WHERE presentation_id IS NOT NULL;

-- 10. GM agents (2026_12_10_gm_agents.sql, docs/done/team_management/03_GM_AGENTS_PLAN.md; PROJECT_SPEC §3.3f).
-- Personas, team direction and needs, draft-pick ownership, trade proposals, assets and sightings.
-- One row per GM tenure (the coach_tenures shape). Traits are 0-1 except pick_affinity (0.6-1.4) and youth_bias
-- (-1 veterans .. +1 youth); position_bias = {group: multiplier}, at most 3 groups, each within +-15%.
-- Rows are never regenerated: replacing a GM closes the tenure (end_season) and seeds a new row.
CREATE TABLE IF NOT EXISTS gm_profiles (
    id SERIAL PRIMARY KEY,
    team_id INT NOT NULL REFERENCES teams(id),
    gm_name TEXT NOT NULL,
    bio TEXT,                                         -- short, invented (untracked narrative)
    voice TEXT,                                       -- style notes for the rationale writer
    bio_source VARCHAR(8) NOT NULL DEFAULT 'TEMPLATE' CHECK (bio_source IN ('LLM', 'TEMPLATE')),
    start_season INT NOT NULL CHECK (start_season >= 1970),   -- tenure start
    first_draft_season INT,                           -- first class he picks (a hot-seat hire: start + 1)
    coach_id INT REFERENCES coaches(id),              -- a head coach who also serves as GM (full control)
    end_season INT,
    end_reason TEXT,                                  -- why the tenure closed (hot seat, by hand)
    risk_appetite NUMERIC(3,2) NOT NULL CHECK (risk_appetite BETWEEN 0 AND 1),
    aggressiveness NUMERIC(3,2) NOT NULL CHECK (aggressiveness BETWEEN 0 AND 1),
    pick_affinity NUMERIC(3,2) NOT NULL CHECK (pick_affinity BETWEEN 0.6 AND 1.4),
    youth_bias NUMERIC(3,2) NOT NULL CHECK (youth_bias BETWEEN -1 AND 1),
    position_bias JSONB NOT NULL DEFAULT '{}'::jsonb,
    seed TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    voice_card JSONB,                                 -- the GM's speech card (2027_02_13_gm_voice_cards.sql)
    voice_card_source TEXT CHECK (voice_card_source IN ('AUTHORED', 'LLM', 'TEMPLATE'))  -- how the card was made
);
CREATE UNIQUE INDEX IF NOT EXISTS gm_profiles_open_tenure ON gm_profiles (team_id) WHERE end_season IS NULL;

-- Who runs each team (sub-plan §1.1 C9, §7 Q1): a user-controlled team's GM only advises JR, and JR can turn
-- its suggestions off per team. A team with no row is CPU-run with suggestions on.
CREATE TABLE IF NOT EXISTS gm_team_settings (
    team_id INT PRIMARY KEY REFERENCES teams(id),
    user_controlled BOOLEAN NOT NULL DEFAULT false,
    suggestions_on BOOLEAN NOT NULL DEFAULT true,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO gm_team_settings (team_id, user_controlled, suggestions_on)
SELECT id, true, true FROM teams WHERE abbreviation IN ('CAR', 'IND')
ON CONFLICT (team_id) DO NOTHING;

-- Direction and needs per team and season (engines/gm_direction.py), plus step 4's front-office quality columns
-- (q_team): step 3 creates them at 1.00 and never writes them after that.
CREATE TABLE IF NOT EXISTS gm_team_seasons (
    team_id INT NOT NULL REFERENCES teams(id),
    season INT NOT NULL,
    gm_id INT REFERENCES gm_profiles(id) ON DELETE SET NULL,
    direction VARCHAR(8) CHECK (direction IN ('CONTEND', 'RETOOL', 'REBUILD')),
    direction_score NUMERIC(6,3),
    direction_week INT,                               -- the checkpoint week the direction was last set
    direction_inputs JSONB,
    needs JSONB NOT NULL DEFAULT '[]'::jsonb,         -- ranked [{group, slot_gap, injury, expiring, score}]
    needs_week INT,
    surplus JSONB NOT NULL DEFAULT '[]'::jsonb,       -- [{group, player_ids}]
    urgent_need JSONB,                                -- injury trigger {group, player_id, weeks_remaining}
    fo_quality NUMERIC(4,2) NOT NULL DEFAULT 1.00 CHECK (fo_quality BETWEEN 0.5 AND 1.6),
    fo_quality_inputs JSONB,
    fo_quality_at TIMESTAMPTZ,
    dev_quality NUMERIC(5,3),                         -- player development D* (engines/dev_quality, §2.18)
    dev_quality_inputs JSONB,
    dev_quality_at TIMESTAMPTZ,
    hot_seat_score NUMERIC(6,3),                      -- offseason rollover (sub-plan §7 Q4)
    hot_seat_inputs JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, season)
);

-- Draft-pick ownership: every team's own picks for the next three drafts (sub-plan §1.1 C5), then trades.
-- overall_pick is filled by step 4 once the order is set.
CREATE TABLE IF NOT EXISTS draft_picks (
    draft_season INT NOT NULL,
    round SMALLINT NOT NULL CHECK (round BETWEEN 1 AND 7),
    original_team_id INT NOT NULL REFERENCES teams(id),
    owner_team_id INT NOT NULL REFERENCES teams(id),
    overall_pick SMALLINT,
    source VARCHAR(5) NOT NULL DEFAULT 'SEED' CHECK (source IN ('SEED', 'TRADE', 'JR', 'COMP', 'JC2A')),
    acquired_proposal_id INT,                         -- FK added below, after gm_trade_proposals
    used BOOLEAN NOT NULL DEFAULT false,
    pick_suffix VARCHAR(4) NOT NULL DEFAULT '',        -- COMP/JC2A comp picks (2027_02_06); '' for every SEED/TRADE/JR row
    PRIMARY KEY (draft_season, round, original_team_id, pick_suffix)
);
CREATE INDEX IF NOT EXISTS draft_picks_owner ON draft_picks (owner_team_id, draft_season);

-- Compensatory draft picks (2027_02_06_compensatory_draft_picks.sql, COMPENSATORY_PICKS_PLAN §2.4/§3.3c): QFA ledger
-- and the awarded CBA / JC-2A picks.
CREATE TABLE IF NOT EXISTS qualifying_free_agents (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    player_id INT NOT NULL REFERENCES players(id),
    old_team_id INT NOT NULL REFERENCES teams(id),
    new_team_id INT NOT NULL REFERENCES teams(id),
    signed_date TIMESTAMP WITH TIME ZONE NOT NULL,
    total_value BIGINT NOT NULL,
    years INT NOT NULL,
    apy BIGINT NOT NULL,
    snap_pct NUMERIC(4,3) DEFAULT 0.0,
    honors_points INT DEFAULT 0,
    round_tier SMALLINT CHECK (round_tier BETWEEN 3 AND 7),
    qualifies BOOLEAN NOT NULL DEFAULT true,
    canceled_by_qfa_id INT REFERENCES qualifying_free_agents(id)
);

CREATE TABLE IF NOT EXISTS compensatory_pick_awards (
    id SERIAL PRIMARY KEY,
    draft_season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    round SMALLINT NOT NULL CHECK (round BETWEEN 3 AND 7),
    slot_in_round SMALLINT NOT NULL,
    overall_pick SMALLINT NOT NULL,
    award_kind VARCHAR(6) NOT NULL CHECK (award_kind IN ('CBA', 'JC2A')),
    source_player_id INT REFERENCES players(id),
    source_coach_id INT REFERENCES coaches(id),
    reason TEXT NOT NULL
);

-- Trade proposals (engines/gm_trade_engine.py) and trades JR recorded himself (origin USER).
-- PROPOSED -> APPROVED -> EXECUTED | REJECTED | EXPIRED. EXECUTED only when JR confirms on #debug.
CREATE TABLE IF NOT EXISTS gm_trade_proposals (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT NOT NULL,                                -- league_state.current_week when made (offseason 24, draft 25)
    trade_window VARCHAR(10) NOT NULL DEFAULT 'IN_SEASON' CHECK (trade_window IN ('IN_SEASON', 'OFFSEASON', 'DRAFT_DAY')),
    trigger_kind VARCHAR(8) NOT NULL DEFAULT 'NEED' CHECK (trigger_kind IN ('NEED', 'INJURY', 'SELL', 'DRAFT', 'USER')),
    origin VARCHAR(6) NOT NULL DEFAULT 'ENGINE' CHECK (origin IN ('ENGINE', 'USER')),
    expires_week INT,
    team_a_id INT NOT NULL REFERENCES teams(id),      -- initiator
    team_b_id INT NOT NULL REFERENCES teams(id),
    value_a BIGINT,                                   -- each side's surplus (dollars)
    value_b BIGINT,
    league_value_a BIGINT,                            -- neutral value of what each side receives
    league_value_b BIGINT,
    cap_space_before_a BIGINT,
    cap_space_before_b BIGINT,
    cap_space_after_a BIGINT,
    cap_space_after_b BIGINT,
    dead_money_a BIGINT,
    dead_money_b BIGINT,
    roster_after_a INT,
    roster_after_b INT,
    cap_derived_a BOOLEAN,
    cap_derived_b BOOLEAN,
    legality JSONB NOT NULL DEFAULT '{}'::jsonb,      -- {check: {ok, detail}}
    rationale_a TEXT,
    rationale_b TEXT,
    headline TEXT,
    rationale_source VARCHAR(8) CHECK (rationale_source IN ('LLM', 'TEMPLATE')),
    status VARCHAR(9) NOT NULL DEFAULT 'PROPOSED'
        CHECK (status IN ('PROPOSED', 'APPROVED', 'EXECUTED', 'REJECTED', 'EXPIRED', 'STALLED')),
    status_reason TEXT,
    approved_at TIMESTAMPTZ,
    executed_at TIMESTAMPTZ,
    executed_as VARCHAR(9) CHECK (executed_as IN ('PROPOSED', 'JR_EDITED', 'SIGHTING')),
    rumor_ok BOOLEAN NOT NULL DEFAULT false,          -- step 5 may use a non-executed proposal as a rumor
    counts_for_pacing BOOLEAN NOT NULL DEFAULT true,  -- false: left out of the budget's pacing (2026_12_13_gm_pacing.sql)
    flags TEXT[] NOT NULL DEFAULT '{}',
    deal_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (season, deal_hash)
);
CREATE INDEX IF NOT EXISTS gm_trade_proposals_status ON gm_trade_proposals (season, status);

CREATE TABLE IF NOT EXISTS gm_trade_assets (
    id SERIAL PRIMARY KEY,
    proposal_id INT NOT NULL REFERENCES gm_trade_proposals(id) ON DELETE CASCADE,
    from_team_id INT NOT NULL REFERENCES teams(id),
    to_team_id INT NOT NULL REFERENCES teams(id),
    asset_type VARCHAR(6) NOT NULL CHECK (asset_type IN ('PLAYER', 'PICK')),
    player_id INT REFERENCES players(id) ON DELETE SET NULL,
    contract_id INT REFERENCES player_contracts(id) ON DELETE SET NULL,
    pick_draft_season INT,
    pick_round SMALLINT,
    pick_original_team_id INT REFERENCES teams(id),
    cap_hit_now BIGINT,
    value_from BIGINT,
    value_to BIGINT,
    value_league BIGINT
);
CREATE INDEX IF NOT EXISTS gm_trade_assets_proposal ON gm_trade_assets (proposal_id);
CREATE INDEX IF NOT EXISTS gm_trade_assets_player ON gm_trade_assets (player_id);

-- Trade rows the League Transactions scan read (pipeline/load_transactions.py skips them as moves). They are
-- matched to EXECUTED proposals (match_status / proposal_id only); "Apply this trade" on #debug records an
-- unmatched one as a USER proposal. Nothing here is ever applied automatically.
CREATE TABLE IF NOT EXISTS gm_trade_sightings (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    txn_date TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    parsed JSONB,
    capture_id INT REFERENCES captures(id) ON DELETE SET NULL,
    proposal_id INT REFERENCES gm_trade_proposals(id) ON DELETE SET NULL,
    match_status VARCHAR(9) NOT NULL DEFAULT 'UNMATCHED' CHECK (match_status IN ('MATCHED', 'PARTIAL', 'UNMATCHED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (season, txn_date, raw_text)
);

-- The offseason stage the GM engine last ran (engines/gm_trade_engine.OFFSEASON_STAGES 1-5), per season just
-- finished: stage 1 runs on its own after the Super Bowl scan, JR runs the rest from #debug (Next offseason stage).
CREATE TABLE IF NOT EXISTS gm_offseason_state (
    season INT PRIMARY KEY,
    stage SMALLINT NOT NULL CHECK (stage BETWEEN 1 AND 5),
    stage_run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    proposals INT NOT NULL DEFAULT 0
);

-- Trade negotiations with the GM agents (engines/gm_negotiation.py; 2026_12_21_gm_negotiations.sql, extended by
-- 2027_01_22_trade_talks.sql for engines/gm_talks.py): JR offers from a user-controlled team, the GM accepts,
-- counters, rejects or walks away; an agreed deal becomes an APPROVED USER proposal (flag negotiated:<id>). A CPU
-- talk (kind CPU) is two GMs: user_team_id / initiator_team_id is the initiator, gm_team_id the other GM.
CREATE TABLE IF NOT EXISTS gm_trade_negotiations (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT NOT NULL,                                -- engine week when opened (offseason 24)
    trade_window VARCHAR(10) NOT NULL CHECK (trade_window IN ('IN_SEASON', 'OFFSEASON')),
    offseason_stage SMALLINT,                         -- the offseason stage it was opened in (expires when it moves)
    user_team_id INT NOT NULL REFERENCES teams(id),   -- JR's team (CPU: the initiator's)
    gm_team_id INT NOT NULL REFERENCES teams(id),     -- the GM agent's team (CPU: the other GM)
    kind VARCHAR(4) NOT NULL DEFAULT 'USER' CHECK (kind IN ('USER', 'CPU')),
    initiator_team_id INT REFERENCES teams(id),       -- CPU: the GM who called; USER: NULL
    source_proposal_id INT REFERENCES gm_trade_proposals(id) ON DELETE SET NULL,
    carried_weeks SMALLINT NOT NULL DEFAULT 0,        -- weeks (or offseason stages) a CPU talk carried over
    published_at TIMESTAMPTZ,                         -- a completed CPU deal's transcript is published
    status VARCHAR(11) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'AGREED', 'WALKED_AWAY', 'CLOSED', 'EXPIRED', 'STALLED')),
    status_reason TEXT,
    patience SMALLINT NOT NULL,                       -- rounds the GM started with (CPU: the initiator's)
    patience_left SMALLINT NOT NULL,                  -- (CPU: the other GM's)
    proposal_id INT REFERENCES gm_trade_proposals(id) ON DELETE SET NULL,   -- the agreed / stalled deal
    trigger_kind VARCHAR(8)                           -- the engine trigger a talk was opened from (CPU talks)
        CHECK (trigger_kind IN ('NEED', 'INJURY', 'SELL', 'DRAFT', 'USER')),
    flags TEXT[] NOT NULL DEFAULT '{}',               -- the stored-path flags (shape:/stage:/release:), 2027_02_12
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS gm_trade_negotiations_season ON gm_trade_negotiations (season, status);

CREATE TABLE IF NOT EXISTS gm_trade_negotiation_turns (
    id SERIAL PRIMARY KEY,
    negotiation_id INT NOT NULL REFERENCES gm_trade_negotiations(id) ON DELETE CASCADE,
    turn_no SMALLINT NOT NULL,
    actor VARCHAR(4) NOT NULL CHECK (actor IN ('JR', 'GM', 'GM_A')),   -- GM_A: CPU talk's initiating GM
    action VARCHAR(9) NOT NULL CHECK (action IN ('OFFER', 'COUNTER', 'ACCEPT', 'REJECT', 'WALK_AWAY', 'CLOSE')),
    actor_team_id INT REFERENCES teams(id),
    assets JSONB,                                     -- Front Office JSON assets of the package on the table
    options JSONB,                                    -- [{key, action, assets, su, sg}] the chooser saw (never public)
    choice TEXT,                                      -- the chosen option key
    choice_source VARCHAR(6) CHECK (choice_source IN ('POLICY', 'JR')),   -- never a model (2027_02_16)
    posture VARCHAR(8) CHECK (posture IN ('FIRM', 'ANCHOR', 'SQUEEZE', 'MEET', 'HOLD')),
    surplus_user BIGINT,                              -- JR's team's surplus by the model
    surplus_gm BIGINT,                                -- the GM's surplus (never shown to JR)
    mood VARCHAR(8),                                  -- the GM's read of an offer: CLOSE | APART | INSULTED
    legality JSONB,
    message TEXT,                                     -- JR's note, or the GM's reply
    message_source VARCHAR(8) CHECK (message_source IN ('JR', 'LLM', 'TEMPLATE')),
    week SMALLINT,                                    -- engine week the turn was played at (2027_02_14)
    stage SMALLINT,                                   -- offseason stage it was played at, NULL in season
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (negotiation_id, turn_no)
);

-- Leaks from trade talks (engines/gm_talks.py; 2027_01_22_trade_talks.sql): a rumor from one talk, positions only
-- unless the leaker names the players he receives.
CREATE TABLE IF NOT EXISTS gm_trade_leaks (
    id SERIAL PRIMARY KEY,
    negotiation_id INT NOT NULL REFERENCES gm_trade_negotiations(id) ON DELETE CASCADE,
    turn_no INT NOT NULL,
    season INT NOT NULL,
    week INT NOT NULL,                                -- engine week of the leak
    leaker_team_id INT NOT NULL REFERENCES teams(id),
    stage VARCHAR(7) NOT NULL CHECK (stage IN ('OPENED', 'CLOSE', 'STALLED')),
    named_player_ids INT[] NOT NULL DEFAULT '{}',     -- empty: positions only
    detail JSONB NOT NULL,                            -- {teams:[abbr,abbr], initiator, positions:[{pos, from, to}], picks:int}
    insider_key TEXT,                                 -- agent_personas.json key of the writer who got it first
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (negotiation_id, turn_no)
);
CREATE INDEX IF NOT EXISTS gm_trade_leaks_week ON gm_trade_leaks (season, week);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'draft_picks_acquired_proposal_fk') THEN
        ALTER TABLE draft_picks ADD CONSTRAINT draft_picks_acquired_proposal_fk
            FOREIGN KEY (acquired_proposal_id) REFERENCES gm_trade_proposals(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'transactions_gm_proposal_fk') THEN
        ALTER TABLE transactions ADD CONSTRAINT transactions_gm_proposal_fk
            FOREIGN KEY (gm_proposal_id) REFERENCES gm_trade_proposals(id) ON DELETE SET NULL;
    END IF;
END $$;

-- Master §2.4's single "GM profile": the open tenure, this season's direction and needs, and who runs the team.
CREATE OR REPLACE VIEW vw_gm_profiles AS
SELECT g.id AS gm_id,
       g.team_id,
       t.abbreviation,
       t.city,
       t.name AS team_name,
       g.gm_name,
       g.bio,
       g.voice,
       g.bio_source,
       g.start_season,
       g.risk_appetite,
       g.aggressiveness,
       g.pick_affinity,
       g.youth_bias,
       g.position_bias,
       ls.current_season AS season,
       s.direction,
       s.direction_score,
       s.direction_week,
       s.needs,
       s.surplus,
       s.urgent_need,
       s.fo_quality,
       s.fo_quality_inputs,
       s.fo_quality_at,
       COALESCE(st.user_controlled, false) AS user_controlled,
       COALESCE(st.suggestions_on, true) AS suggestions_on
FROM gm_profiles g
JOIN teams t ON t.id = g.team_id
CROSS JOIN league_state ls
LEFT JOIN gm_team_seasons s ON s.team_id = g.team_id AND s.season = ls.current_season
LEFT JOIN gm_team_settings st ON st.team_id = g.team_id
WHERE g.end_season IS NULL AND ls.id = 1;

-- ---------------------------------------------------------------------------
-- Draft (2026_11_19_draft_board.sql)
-- ---------------------------------------------------------------------------

-- Scouted draft board (pipeline/draft_board.py, loaded from the Draft Board scan by pipeline/load_capture.py).
-- One row per prospect per draft class; a re-scan replaces the class. Only the pick and name are required:
-- the scan can leave a cell blank (JR, Oct 15), and the rookie's real values arrive with next season's
-- roster scan, which also sets player_id.
CREATE TABLE IF NOT EXISTS draft_prospects (
    id SERIAL PRIMARY KEY,
    draft_season INT NOT NULL,              -- the class: league season + 1
    projected_pick INT,                     -- overall: 1-224 drafted, then the undrafted in board order (nullable for unprojected picks)
    projected_round VARCHAR(4),             -- '1'..'7' or 'UDFA'
    round_pick SMALLINT,
    full_name VARCHAR(255) NOT NULL,
    position VARCHAR(10),
    college VARCHAR(100),
    height_in SMALLINT,
    weight SMALLINT,
    age SMALLINT,
    ovr SMALLINT,                           -- scouted OVR on the board
    archetype VARCHAR(64),
    notes TEXT,
    source_capture VARCHAR(255),            -- captures/<run> the board came from
    player_id INT REFERENCES players(id) ON DELETE SET NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Actual draft selection columns (2026_11_26_draft_selections.sql)
    actual_round VARCHAR(8),
    actual_round_pick SMALLINT,
    actual_pick SMALLINT CHECK (actual_pick BETWEEN 1 AND 300),
    drafted_team_id INT REFERENCES teams(id) ON DELETE SET NULL,
    selection_notes TEXT,
    UNIQUE (draft_season, projected_pick)
);
CREATE INDEX IF NOT EXISTS idx_draft_prospects_player ON draft_prospects(player_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_draft_prospects_actual_pick ON draft_prospects(draft_season, actual_pick) WHERE actual_pick IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_draft_prospects_drafted_team ON draft_prospects(draft_season, drafted_team_id) WHERE drafted_team_id IS NOT NULL;

-- Round 1 mock draft per (season, week), saved by engines/mock_draft_engine.py so every article that week
-- cites the same picks. Rows go with the board they were drawn from (a re-scan replaces both).
CREATE TABLE IF NOT EXISTS mock_draft_picks (
    season INT NOT NULL,                    -- league season whose draft order and needs were used
    week INT NOT NULL,
    pick SMALLINT NOT NULL,
    team_id INT REFERENCES teams(id),
    prospect_id INT NOT NULL REFERENCES draft_prospects(id) ON DELETE CASCADE,
    locked BOOLEAN NOT NULL DEFAULT false,  -- the team's season is over, so its pick is final
    selection_status VARCHAR(40),
    fit_score NUMERIC(7, 1),
    rationale TEXT,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (season, week, pick)
);

-- 11. Scouting and draft (2026_12_12_scouting.sql, docs/done/team_management/04_SCOUTING_AND_DRAFT_PLAN.md;
-- PROJECT_SPEC §3.3g). Hidden columns: true_value, true_rank, value_inputs, err, impact, scout_team_boards.
-- One row per prospect per class. true_value (V) and true_rank are hidden; prospect_id is re-linked after every
-- board load (engines/scouting_engine.relink). status: ACTIVE -> DRAFTED | UDFA, or WITHDRAWN when a re-scan drops him.
CREATE TABLE IF NOT EXISTS scout_prospects (
    id SERIAL PRIMARY KEY,
    draft_season INT NOT NULL,
    prospect_key TEXT NOT NULL,
    prospect_id INT REFERENCES draft_prospects(id) ON DELETE SET NULL,
    true_value NUMERIC(6,2) NOT NULL,
    true_rank INT NOT NULL,
    value_inputs JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {ovr, position, group, age, pv, age_adj}
    class_year TEXT,                                   -- junior, redshirt sophomore, senior ... (from age)
    conference TEXT,                                   -- data/scouting/colleges.json
    first_step SMALLINT NOT NULL,
    status VARCHAR(9) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'WITHDRAWN', 'DRAFTED', 'UDFA')),
    seed TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (draft_season, prospect_key)
);
CREATE INDEX IF NOT EXISTS scout_prospects_prospect ON scout_prospects (prospect_id);

-- One row per stored scouting step (in-season step = league week; offseason step = 23 + stage, 27 = draft).
CREATE TABLE IF NOT EXISTS scout_runs (
    draft_season INT NOT NULL,
    step SMALLINT NOT NULL CHECK (step BETWEEN 1 AND 27),
    season INT NOT NULL,
    week INT NOT NULL,
    stage SMALLINT,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,         -- the constants and code version the step ran with
    n_events INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (draft_season, step)
);

-- The consensus board each step: grade (0-100), rank, round label, change from the previous step. err is hidden.
CREATE TABLE IF NOT EXISTS scout_consensus (
    draft_season INT NOT NULL,
    step SMALLINT NOT NULL,
    prospect_key TEXT NOT NULL,
    season INT NOT NULL,
    week INT NOT NULL,
    stage SMALLINT,
    grade NUMERIC(5,1) NOT NULL,
    rank INT NOT NULL,
    round_label TEXT NOT NULL,
    delta_grade NUMERIC(5,1),
    delta_rank INT,
    err NUMERIC(7,3) NOT NULL,
    event_ids INT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (draft_season, step, prospect_key),
    FOREIGN KEY (draft_season, step) REFERENCES scout_runs (draft_season, step) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS scout_consensus_rank ON scout_consensus (draft_season, step, rank);

-- Invented college events (games, storylines, all-star games, combine, pro days, visits, draft week). Every one is
-- stored so all writers see the same story. impact (the consensus move it explains) is hidden.
CREATE TABLE IF NOT EXISTS scout_events (
    id SERIAL PRIMARY KEY,
    draft_season INT NOT NULL,
    step SMALLINT NOT NULL,
    prospect_key TEXT NOT NULL,
    kind VARCHAR(9) NOT NULL CHECK (kind IN ('GAME', 'STORYLINE', 'DECLARE', 'AWARD', 'ALL_STAR', 'COMBINE',
                                             'PRO_DAY', 'VISIT', 'MEDICAL', 'BUZZ')),
    team_id INT REFERENCES teams(id),                  -- a VISIT's team
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,        -- stat line, opponent, measurables (keys starting _ are hidden)
    impact NUMERIC(7,3),
    headline TEXT,
    blurb TEXT,
    text_source VARCHAR(8) NOT NULL DEFAULT 'TEMPLATE' CHECK (text_source IN ('LLM', 'TEMPLATE')),
    seq SMALLINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (draft_season, step, prospect_key, kind, seq),
    FOREIGN KEY (draft_season, step) REFERENCES scout_runs (draft_season, step) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS scout_events_prospect ON scout_events (draft_season, prospect_key, step);

-- Each team's private board, top 100 by its grade, every step (hidden: shown only in the Draft Room).
CREATE TABLE IF NOT EXISTS scout_team_boards (
    draft_season INT NOT NULL,
    step SMALLINT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    prospect_key TEXT NOT NULL,
    team_grade NUMERIC(5,1) NOT NULL,
    team_rank INT NOT NULL,
    fit NUMERIC(6,2),
    bias NUMERIC(6,2),
    err NUMERIC(7,3),
    PRIMARY KEY (draft_season, step, team_id, prospect_key),
    FOREIGN KEY (draft_season, step) REFERENCES scout_runs (draft_season, step) ON DELETE CASCADE
);

-- The Round 1 picks JR records in the Draft Room as he makes them in Madden; validated against the Draft Recap.
CREATE TABLE IF NOT EXISTS scout_draft_picks (
    draft_season INT NOT NULL,
    overall_pick SMALLINT NOT NULL CHECK (overall_pick BETWEEN 1 AND 32),
    round SMALLINT NOT NULL DEFAULT 1,
    team_id INT NOT NULL REFERENCES teams(id),         -- the owner on the clock
    original_team_id INT NOT NULL REFERENCES teams(id),
    prospect_key TEXT NOT NULL,
    prospect_id INT REFERENCES draft_prospects(id) ON DELETE SET NULL,
    recommended_key TEXT,
    choice_source VARCHAR(10) NOT NULL CHECK (choice_source IN ('GM', 'OFF_SCRIPT', 'JR_TEAM')),
    alternatives JSONB NOT NULL DEFAULT '[]'::jsonb,
    rationale TEXT,
    rationale_source VARCHAR(8) CHECK (rationale_source IN ('LLM', 'TEMPLATE')),
    trade_proposal_id INT REFERENCES gm_trade_proposals(id) ON DELETE SET NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    madden_match VARCHAR(8) CHECK (madden_match IN ('MATCH', 'MISMATCH')),
    madden_prospect_id INT REFERENCES draft_prospects(id) ON DELETE SET NULL,
    PRIMARY KEY (draft_season, overall_pick),
    UNIQUE (draft_season, prospect_key)
);

-- Rounds 2-7 JR started in the Draft Room (draft-day trades proposed at the start of each round;
-- 2027_01_02_draft_round_starts.sql).
CREATE TABLE IF NOT EXISTS draft_round_starts (
    draft_season INT NOT NULL,
    round SMALLINT NOT NULL CHECK (round BETWEEN 2 AND 7),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    proposals SMALLINT NOT NULL DEFAULT 0,             -- trade proposals stored when the round started
    PRIMARY KEY (draft_season, round)
);

-- The writer-safe board: consensus rows with the prospect's public fields and the step's top event headline.
-- No true_value, err, impact, team boards or Madden OVR.
CREATE OR REPLACE VIEW vw_scout_board_public AS
SELECT c.draft_season, c.step, c.season, c.week, c.stage, c.prospect_key, c.grade, c.rank, c.round_label,
       c.delta_grade, c.delta_rank,
       d.full_name, d.position, d.college, d.height_in, d.weight, d.age,
       sp.class_year, sp.conference, sp.status,
       (SELECT e.headline FROM scout_events e
        WHERE e.draft_season = c.draft_season AND e.step = c.step AND e.prospect_key = c.prospect_key
          AND e.kind <> 'VISIT'
        ORDER BY abs(e.impact) DESC NULLS LAST, e.id LIMIT 1) AS top_headline
FROM scout_consensus c
JOIN scout_prospects sp ON sp.draft_season = c.draft_season AND sp.prospect_key = c.prospect_key
LEFT JOIN draft_prospects d ON d.id = sp.prospect_id;


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

-- Fantasy points per game for skill players, kickers and team defenses (2027_01_12_kdst_fantasy_scores.sql)
CREATE OR REPLACE VIEW vw_fantasy_scores AS
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
-- Awards & Coaching Records
-- ---------------------------------------------------------------------------

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
    source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE'
);

CREATE UNIQUE INDEX IF NOT EXISTS awards_season_player_type_idx 
    ON awards (season, award_type, position, player_id) 
    WHERE player_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS awards_single_winner_player_idx 
    ON awards (season, award_type) 
    WHERE award_type IN ('MVP', 'OPOY', 'DPOY', 'OROY', 'DROY', 'SBMVP');

CREATE UNIQUE INDEX IF NOT EXISTS awards_coach_season_type_idx 
    ON awards (season, award_type) 
    WHERE award_type = 'COTY';


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

-- Season-end archive and head-coach records from 2035 (2026_12_24_season_archive.sql, engines/season_archive.py,
-- docs/done/SEASON_CONTINUITY_PLAN.md phase 1).
-- One row per finished season.
CREATE TABLE IF NOT EXISTS league_seasons (
    season INT PRIMARY KEY,
    champion_team_id INT REFERENCES teams(id),
    runner_up_team_id INT REFERENCES teams(id),
    super_bowl_game_id INT REFERENCES games(id) ON DELETE SET NULL,
    regular_season_games INT NOT NULL,
    postseason_games INT NOT NULL,
    notes TEXT,
    finalized_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every team's final line for a finished season.
CREATE TABLE IF NOT EXISTS team_season_results (
    season INT NOT NULL REFERENCES league_seasons(season) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id),
    wins INT NOT NULL,
    losses INT NOT NULL,
    ties INT NOT NULL,
    points_for INT NOT NULL,
    points_against INT NOT NULL,
    division_place SMALLINT NOT NULL CHECK (division_place BETWEEN 1 AND 4),
    division_tiebreak TEXT,                        -- the rule that placed it above a team with the same record, or UNBROKEN
    conference_seed SMALLINT CHECK (conference_seed BETWEEN 1 AND 7),   -- NULL: missed the playoffs
    playoff_result VARCHAR(16) NOT NULL CHECK (playoff_result IN
        ('MISSED', 'WILD_CARD', 'DIVISIONAL', 'CONFERENCE', 'SUPER_BOWL_LOSS', 'CHAMPION')),
    playoff_wins INT NOT NULL DEFAULT 0,
    playoff_losses INT NOT NULL DEFAULT 0,
    head_coach_id INT REFERENCES coaches(id) ON DELETE SET NULL,
    gm_id INT,                                     -- gm_profiles(id) when the GM agents migration is applied
    finalized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (season, team_id)
);

-- Each head coach's line for a finished season (one coach per team per season), from 2035.
CREATE TABLE IF NOT EXISTS coach_season_lines (
    coach_id INT NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id),
    season INT NOT NULL,
    wins INT NOT NULL,
    losses INT NOT NULL,
    ties INT NOT NULL,
    games INT NOT NULL,                            -- regular-season games coached
    playoff_wins INT NOT NULL DEFAULT 0,
    playoff_losses INT NOT NULL DEFAULT 0,
    playoff_result VARCHAR(16) NOT NULL,
    PRIMARY KEY (coach_id, team_id, season)
);
CREATE INDEX IF NOT EXISTS coach_season_lines_team ON coach_season_lines (team_id, season);

-- A coach with no record before he joined this league (a carousel hire, plan D6): his snapshot counts only his
-- seasons here; his earlier career isn't recorded and is never filled in.
ALTER TABLE coach_seasons ADD COLUMN IF NOT EXISTS mu_era_only BOOLEAN NOT NULL DEFAULT false;

-- Player season ledger and retirements (2027_01_03_player_season_ledger.sql, engines/season_ledger.py,
-- docs/done/SEASON_CONTINUITY_PLAN.md phase 2a).
-- The season a player retired after (a captured retirement; the 2026-2034 history's players are retired by their
-- players.origin instead, plan D11). A later signing or roster scan that finds him clears it (plan D8).
ALTER TABLE players ADD COLUMN IF NOT EXISTS retired_season INT;

-- One row per retirement read off League Transactions.
CREATE TABLE IF NOT EXISTS player_retirements (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,                               -- the season he retired after
    player_id INT REFERENCES players(id) ON DELETE CASCADE,   -- NULL: the name couldn't be settled (see flags)
    player_name TEXT NOT NULL,
    position VARCHAR(6),
    team_id INT REFERENCES teams(id),                  -- his last team, when the row names one
    transaction_id INT REFERENCES transactions(id) ON DELETE SET NULL,
    capture_id INT REFERENCES captures(id) ON DELETE SET NULL,
    flags TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS player_retirements_one ON player_retirements (season, player_id)
    WHERE player_id IS NOT NULL;

-- Madden's retirement call next to Project Mu's (2027_01_14_v2_franchise_os.sql): mu_keep = Project Mu un-retires him.
CREATE TABLE IF NOT EXISTS retirement_decisions (
    season INT NOT NULL,
    player_id INT NOT NULL REFERENCES players(id),
    madden_retired BOOLEAN NOT NULL,
    mu_keep BOOLEAN NOT NULL,               -- Project Mu un-retires him
    reason TEXT,
    PRIMARY KEY (season, player_id)
);

-- Retirement policy sidecars (2027_01_26_retirement_policy.sql, engines/retirement_store.py,
-- PERSONALITY_RETIREMENT_PLAN §2.5): career state machine rows, append-only review/event receipts and save-write
-- receipts. Policy intent and execution only; retirement_decisions above stays the league office's projection.
CREATE TABLE IF NOT EXISTS career_decision_states (
    universe_id TEXT NOT NULL,
    person_key TEXT NOT NULL,
    earned_season INTEGER NOT NULL,
    player_id INTEGER NOT NULL REFERENCES players (id),
    state TEXT NOT NULL CHECK (state IN ('OPEN', 'AVAILABLE', 'RETIRE_INTENT', 'SIGN_PENDING', 'RETIRE_PENDING',
                                         'RETURNING', 'UNSIGNED_RETURN_ELIGIBLE', 'RETIRED', 'BLOCKED_DATA',
                                         'CONFLICT')),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    decision_id UUID NOT NULL UNIQUE,
    reservation JSONB NULL CHECK (reservation IS NULL OR jsonb_typeof(reservation) = 'object'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, person_key, earned_season)
);

CREATE TABLE IF NOT EXISTS retirement_policy_events (
    event_id UUID PRIMARY KEY,
    decision_id UUID NOT NULL REFERENCES career_decision_states (decision_id),
    checkpoint TEXT NOT NULL CHECK (checkpoint IN ('OPEN', 'MARKET_CLOSE', 'ROLLOVER_REVIEW', 'EVENT')),
    event_kind TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    input_snapshot JSONB NOT NULL,
    policy_snapshot JSONB NOT NULL,
    draw_hex TEXT NULL,
    breakdown JSONB NULL,
    before_state JSONB NOT NULL,
    after_state JSONB NOT NULL,
    receipt JSONB NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (decision_id, source_event_id)
);

CREATE TABLE IF NOT EXISTS retirement_write_receipts (
    operation_id UUID PRIMARY KEY,
    decision_id UUID NOT NULL REFERENCES career_decision_states (decision_id),
    status TEXT NOT NULL CHECK (status IN ('PREPARED', 'VERIFIED', 'FAILED', 'CONFLICT')),
    source_save_hash TEXT NOT NULL,
    output_hash TEXT NULL,
    edits_hash TEXT NOT NULL,
    league_office_run INTEGER NULL REFERENCES league_office_runs (id),
    before_image JSONB NOT NULL,
    verification JSONB NULL,
    request_hash TEXT NOT NULL,
    ack_hash TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged_at TIMESTAMPTZ NULL,
    CHECK ((status = 'PREPARED') = (ack_hash IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS retirement_write_receipts_one_prepared
    ON retirement_write_receipts (decision_id) WHERE status = 'PREPARED';

-- Each player's fate between two captured seasons (plan D10: captured seasons only, 2035 -> 2036 on).
CREATE TABLE IF NOT EXISTS player_season_transitions (
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    to_season INT NOT NULL,
    from_season INT NOT NULL,
    from_team_id INT REFERENCES teams(id),             -- his team at the end of from_season (NULL: not on a roster)
    to_team_id INT REFERENCES teams(id),               -- his team at the start of to_season (NULL: none)
    status VARCHAR(12) NOT NULL CHECK (status IN ('RETAINED', 'MOVED', 'RETIRED', 'FREE_AGENT', 'DRAFTED', 'NEW')),
    source VARCHAR(12),                                -- how a move or arrival happened: TRADE, TRANSACTION, ROSTER_SCAN, DRAFT
    ovr_before INT, ovr_after INT,
    dev_before VARCHAR(32), dev_after VARCHAR(32),
    age_before INT, age_after INT,
    exp_before INT, exp_after INT,
    flags TEXT,                                        -- e.g. retirement_scan_missing, released_in_season, returned
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (player_id, to_season)
);
CREATE INDEX IF NOT EXISTS player_season_transitions_status ON player_season_transitions (to_season, status);

-- Player progression engine (2027_01_08_player_progression.sql, engines/progression.py, docs/in_progress/PROGRESSION_PLAN.md).
CREATE TABLE IF NOT EXISTS player_progression_history (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    old_ovr INT NOT NULL,
    new_ovr INT NOT NULL,
    delta_ovr INT NOT NULL,
    age_term NUMERIC(4, 2) NOT NULL DEFAULT 0,
    dev_term NUMERIC(4, 2) NOT NULL DEFAULT 0,
    ceiling_term NUMERIC(4, 2) NOT NULL DEFAULT 0,
    play_term NUMERIC(4, 2) NOT NULL DEFAULT 0,
    injury_term NUMERIC(4, 2) NOT NULL DEFAULT 0,
    noise_term NUMERIC(4, 2) NOT NULL DEFAULT 0,
    league_term NUMERIC(4, 2) NOT NULL DEFAULT 0,       -- league-shape term (development + star)
    performance_term NUMERIC(4, 2) NOT NULL DEFAULT 0,  -- last season's production rank + awards
    ratings_diff JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    cause VARCHAR(32),                                  -- main cause tag (2027_01_11_progression_rating_groups.sql)
    -- Progression C2 storage (2027_01_14_v2_franchise_os.sql, PROGRESSION_PLAN §5.6)
    checkpoint VARCHAR(12) NOT NULL DEFAULT 'OFFSEASON',
    dev_before VARCHAR(16),
    dev_after VARCHAR(16),
    group_changes JSONB,
    wear_term NUMERIC(6,3),
    upkeep_term NUMERIC(6,3),
    continuity_term NUMERIC(6,3),
    mentor_term NUMERIC(6,3),
    persona_term NUMERIC(6,3) NOT NULL DEFAULT 0,
    coach_term NUMERIC(6,3) NOT NULL DEFAULT 0,
    behaviour_changes JSONB,
    UNIQUE (season, player_id)
);
CREATE INDEX IF NOT EXISTS idx_player_progression_season ON player_progression_history (season);
CREATE INDEX IF NOT EXISTS idx_player_progression_player ON player_progression_history (player_id);

CREATE TABLE IF NOT EXISTS player_ceilings (
    player_id INT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    ceiling NUMERIC(5, 2) NOT NULL,
    source VARCHAR(32) NOT NULL,
    updated_season INT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    peak_mental JSONB,                                  -- career-high Awareness, PlayRecognition
    entry_mental JSONB,                                 -- draft-day Awareness, PlayRecognition
    entry_source VARCHAR(16),                           -- ESTIMATED (Madden's mean gain) | SAVE
    floor NUMERIC(5,2),                                 -- 2027_01_14_v2_franchise_os.sql
    spread NUMERIC(5,2),
    group_ceilings JSONB                                -- {physical, technical, mental}
);
CREATE INDEX IF NOT EXISTS idx_player_ceilings_season ON player_ceilings (updated_season);

-- Weekly form overlay (2027_01_14_v2_franchise_os.sql, progression phase E).
CREATE TABLE IF NOT EXISTS player_form (
    player_id INT NOT NULL REFERENCES players(id),
    season INT NOT NULL, week INT NOT NULL,
    form NUMERIC(4,2) NOT NULL CHECK (form BETWEEN -3 AND 3),   -- OVR points
    overlay JSONB NOT NULL,                 -- {RatingName: delta} written on top of the base ratings
    PRIMARY KEY (player_id, season, week)
);

-- Durability and workload (2027_01_14_v2_franchise_os.sql, progression C2).
CREATE TABLE IF NOT EXISTS player_durability (
    player_id INT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    durability NUMERIC(4,3) NOT NULL CHECK (durability BETWEEN 0 AND 1),
    source VARCHAR(16) NOT NULL, updated_season INT NOT NULL
);
CREATE TABLE IF NOT EXISTS player_mileage (
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    season INT NOT NULL,
    touches INT NOT NULL DEFAULT 0, hits INT NOT NULL DEFAULT 0, snaps INT NOT NULL DEFAULT 0,
    wear NUMERIC(6,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, season)
);

-- League Memory narrative search index (2027_01_10_league_memory.sql, newsroom/league_memory.py, docs/done/PRE_PC_WORK.md §9, docs/in_progress/V2_MASTER_PLAN.md V2.8).
CREATE TABLE IF NOT EXISTS league_memory (
    id SERIAL PRIMARY KEY,
    source_table VARCHAR(32) NOT NULL,
    source_id INT NOT NULL,
    season INT,
    week INT,
    team_ids VARCHAR(8)[],
    player_ids INT[],
    text_chunk TEXT NOT NULL,
    embedding REAL[] NOT NULL,
    model VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source_table, source_id)
);
CREATE INDEX IF NOT EXISTS idx_league_memory_source ON league_memory(source_table, source_id);
CREATE INDEX IF NOT EXISTS idx_league_memory_season_week ON league_memory(season, week);

-- Personality plan tables (2027_01_09_personas.sql, docs/in_progress/PERSONALITY_PLAN.md §4, §9).
CREATE TABLE IF NOT EXISTS persona_sliders (
    persona_type VARCHAR(32) NOT NULL CHECK (persona_type IN ('writer', 'gm', 'player', 'coach', 'owner', 'scout')),
    persona_id VARCHAR(64) NOT NULL,
    temperament INT NOT NULL CHECK (temperament BETWEEN 0 AND 10),
    sociability INT NOT NULL CHECK (sociability BETWEEN 0 AND 10),
    confidence INT NOT NULL CHECK (confidence BETWEEN 0 AND 10),
    openness INT NOT NULL CHECK (openness BETWEEN 0 AND 10),
    warmth INT NOT NULL CHECK (warmth BETWEEN 0 AND 10),
    drive INT NOT NULL CHECK (drive BETWEEN 0 AND 10),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (persona_type, persona_id),
    CONSTRAINT chk_persona_sliders_budget CHECK (temperament + sociability + confidence + openness + warmth + drive BETWEEN 24 AND 36)
);
CREATE INDEX IF NOT EXISTS idx_persona_sliders_type ON persona_sliders (persona_type);

CREATE TABLE IF NOT EXISTS persona_traits (
    id SERIAL PRIMARY KEY,
    persona_type VARCHAR(32) NOT NULL CHECK (persona_type IN ('writer', 'gm', 'player', 'coach', 'owner', 'scout')),
    persona_id VARCHAR(64) NOT NULL,
    trait_id VARCHAR(64) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (persona_type, persona_id, trait_id)
);
CREATE INDEX IF NOT EXISTS idx_persona_traits_lookup ON persona_traits (persona_type, persona_id);
CREATE INDEX IF NOT EXISTS idx_persona_traits_trait ON persona_traits (trait_id);

CREATE TABLE IF NOT EXISTS persona_views (
    id SERIAL PRIMARY KEY,
    persona_type VARCHAR(32) NOT NULL CHECK (persona_type IN ('writer', 'gm', 'player', 'coach', 'owner', 'scout')),
    persona_id VARCHAR(64) NOT NULL,
    view_text TEXT NOT NULL,
    trigger_condition VARCHAR(128) DEFAULT '',
    subject_tags TEXT[] DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_persona_views_lookup ON persona_views (persona_type, persona_id);

CREATE TABLE IF NOT EXISTS persona_relationships (
    id SERIAL PRIMARY KEY,
    persona_type VARCHAR(32) NOT NULL CHECK (persona_type IN ('writer', 'gm', 'player', 'coach', 'owner', 'scout')),
    persona_id VARCHAR(64) NOT NULL,
    target_type VARCHAR(32) NOT NULL,
    target_id VARCHAR(64) NOT NULL,
    trust NUMERIC(3, 2) NOT NULL DEFAULT 0.00 CHECK (trust BETWEEN -1.00 AND 1.00),
    note TEXT,
    tags TEXT[] DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    evidence JSONB DEFAULT '[]'::jsonb,     -- last events that moved trust {season, week, kind, delta, text} (2027_01_18_writer_voice.sql)
    UNIQUE (persona_type, persona_id, target_type, target_id)
);
CREATE INDEX IF NOT EXISTS idx_persona_rel_lookup ON persona_relationships (persona_type, persona_id);

-- Every graded pick and call a writer's relationship row has already been charged for (2027_02_22; M-0167): the
-- evidence window holds five events, so the applied ref is recorded on its own row rather than inferred from it.
CREATE TABLE IF NOT EXISTS persona_relationship_event_refs (
    persona_type VARCHAR(32) NOT NULL,
    persona_id VARCHAR(64) NOT NULL,
    target_type VARCHAR(32) NOT NULL,
    target_id VARCHAR(64) NOT NULL,
    ref VARCHAR(64) NOT NULL,               -- 'pick:<agent_predictions.id>' or 'call:<agent_season_calls.id>'
    season INT,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (persona_type, persona_id, target_type, target_id, ref)
);

CREATE TABLE IF NOT EXISTS persona_trait_history (
    id SERIAL PRIMARY KEY,
    persona_type VARCHAR(32) NOT NULL CHECK (persona_type IN ('writer', 'gm', 'player', 'coach', 'owner', 'scout')),
    persona_id VARCHAR(64) NOT NULL,
    trait_id VARCHAR(64) NOT NULL,
    added_season INT NOT NULL,
    removed_season INT,
    reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_persona_trait_history_lookup ON persona_trait_history (persona_type, persona_id);

-- Career totals from Madden's Career Stats lists (2026_12_26_player_career_totals.sql, pipeline/load_career_totals.py,
-- docs/done/HISTORY_BACKFILL_PLAN.md phase 1): one row per list row and category, in season_stat_lines column names.
CREATE TABLE IF NOT EXISTS player_career_totals (
    id SERIAL PRIMARY KEY,
    captured_season INT NOT NULL,
    captured_week INT,
    capture_folder TEXT NOT NULL,
    category VARCHAR(16) NOT NULL,
    player_id INT REFERENCES players(id) ON DELETE SET NULL,
    list_name TEXT NOT NULL,
    position VARCHAR(8),
    stats JSONB NOT NULL,
    flags TEXT[] NOT NULL DEFAULT '{}',
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS player_career_totals_player
    ON player_career_totals (captured_season, category, player_id) WHERE player_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS player_career_totals_by_player ON player_career_totals (player_id);

-- Generated 2026-2034 team arc (2026_12_28_history_team_arc.sql, engines/history_backfill/team_arc.py,
-- docs/done/HISTORY_BACKFILL_PLAN.md phase 2): `apply` writes source = 'SYNTH'; the season archive's rows stay 'CAPTURE'.
ALTER TABLE league_seasons ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';
ALTER TABLE team_season_results ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';
ALTER TABLE coach_season_lines ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';
CREATE TABLE IF NOT EXISTS history_candidates (
    id SERIAL PRIMARY KEY,
    seed INT NOT NULL,
    objective DOUBLE PRECISION,
    solver_status TEXT,
    pins JSONB,
    report JSONB,                                  -- verify() problems and the AFC playoff-loss offset
    applied_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS history_team_seasons (
    candidate_id INT NOT NULL REFERENCES history_candidates(id) ON DELETE CASCADE,
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    wins INT NOT NULL,
    losses INT NOT NULL,
    ties INT NOT NULL DEFAULT 0,
    points_for INT NOT NULL,
    points_against INT NOT NULL,
    division_place SMALLINT NOT NULL,
    division_tiebreak TEXT,
    conference_seed SMALLINT,
    playoff_result VARCHAR(16) NOT NULL,
    playoff_wins INT NOT NULL DEFAULT 0,
    playoff_losses INT NOT NULL DEFAULT 0,
    head_coach_id INT REFERENCES coaches(id) ON DELETE SET NULL,
    gm_id INT,
    PRIMARY KEY (candidate_id, season, team_id)
);

-- Generated 2026-2034 player rows (2026_12_30_history_player_rows.sql, phase 3 apply): source = 'SYNTH'; the
-- players the backfill creates carry origin HISTORY (real 2026 players retired before 2035) or HISTORY_FILLIN.
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
-- generated 2026-2034 All-Pro teams (2026_12_31_awards_source.sql, engines/history_backfill/all_pro.py)
ALTER TABLE awards ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'CAPTURE';

-- Staged 2026-2034 player lines (2026_12_29_history_player_lines.sql, engines/history_backfill/players.py, phase 3):
-- nothing reaches the live player tables before JR approves a run.
CREATE TABLE IF NOT EXISTS history_player_runs (
    id SERIAL PRIMARY KEY,
    seed INT NOT NULL,
    team_arc_candidate INT,
    report JSONB,
    applied_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS history_players (
    run_id INT NOT NULL REFERENCES history_player_runs(id) ON DELETE CASCADE,
    key TEXT NOT NULL,                             -- p<player id> | e<espn id> | f<n>
    kind VARCHAR(8) NOT NULL,                      -- pool | vet | fillin
    player_id INT REFERENCES players(id) ON DELETE SET NULL,
    espn_id TEXT,
    full_name TEXT NOT NULL,
    player_name TEXT NOT NULL,
    position VARCHAR(8) NOT NULL,
    first_season INT,
    PRIMARY KEY (run_id, key)
);
CREATE TABLE IF NOT EXISTS history_player_lines (
    run_id INT NOT NULL REFERENCES history_player_runs(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    position VARCHAR(8) NOT NULL,
    age INT,
    years_of_experience INT,
    stats JSONB NOT NULL,
    PRIMARY KEY (run_id, key, season)
);

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
    historical_bonus DOUBLE PRECISION DEFAULT 0.0,
    historical_notes TEXT[],
    UNIQUE (coach_id, season)
);

-- Coach hot seat, decided once per season on Black Monday by engines/coach_hot_seat.py (2026_12_22_coach_hot_seat.sql)
CREATE TABLE IF NOT EXISTS coach_hot_seat (
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    coach_id INT NOT NULL REFERENCES coaches(id),
    score NUMERIC(6,3) NOT NULL,
    fired BOOLEAN NOT NULL DEFAULT false,
    inputs JSONB NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    phase VARCHAR(12) NOT NULL DEFAULT 'BLACK_MONDAY' CHECK (phase IN ('BLACK_MONDAY', 'PLAYOFF_EXIT')),
    PRIMARY KEY (season, team_id)
);

-- Coaching carousel (engines/coaching_carousel.py; 2026_12_23_coaching_carousel.sql): coordinator firings and the
-- hiring cycle after the Super Bowl
CREATE TABLE IF NOT EXISTS coordinator_hot_seat (
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    role VARCHAR(2) NOT NULL CHECK (role IN ('OC', 'DC')),
    coordinator_id INT NOT NULL REFERENCES coordinators(id),
    score NUMERIC(6,3) NOT NULL,
    fired BOOLEAN NOT NULL DEFAULT false,
    phase VARCHAR(12) NOT NULL CHECK (phase IN ('BLACK_MONDAY', 'PLAYOFF_EXIT')),
    inputs JSONB NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (season, team_id, role)
);

CREATE TABLE IF NOT EXISTS staff_moves (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    role VARCHAR(16) NOT NULL CHECK (role IN ('HC', 'OC', 'DC', 'GM', 'AGM')),
    action VARCHAR(8) NOT NULL CHECK (action IN ('HIRED', 'LEFT', 'REPLACED')),
    person TEXT NOT NULL,
    coach_id INT REFERENCES coaches(id),
    coordinator_id INT REFERENCES coordinators(id),
    source VARCHAR(8) CHECK (source IN ('POACHED', 'PROMOTED', 'RETREAD', 'REHIRED', 'HC_STAFF', 'OUTSIDE')),
    from_team_id INT REFERENCES teams(id),
    from_role VARCHAR(2),
    slant VARCHAR(10),
    score NUMERIC(6,3),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    inputs JSONB                                      -- HC hires: opening_rank, openings, appeal, parts
);
CREATE INDEX IF NOT EXISTS staff_moves_season ON staff_moves (season);

-- Staff development award data path (2027_02_07_staff_development_award.sql, COMPENSATORY_PICKS_PLAN follow-up U6):
-- Resolution JC-2A is modelled as reviewed program eligibility JR supplies; a person absent from the registry is
-- not eligible (never guessed). No ethnicity/race/minority column exists on any people table.
CREATE TABLE IF NOT EXISTS staff_development_eligibility (
    id           SERIAL PRIMARY KEY,
    person_kind  VARCHAR(16) NOT NULL CHECK (person_kind IN ('COACH', 'COORDINATOR', 'EXECUTIVE')),
    person_key   TEXT NOT NULL,
    person_name  TEXT,
    eligible     BOOLEAN NOT NULL DEFAULT true,
    source       TEXT NOT NULL,
    reviewed_by  TEXT,
    as_of        DATE NOT NULL DEFAULT CURRENT_DATE,
    notes        TEXT,
    UNIQUE (person_kind, person_key)
);

CREATE TABLE IF NOT EXISTS staff_development_departures (
    id           SERIAL PRIMARY KEY,
    season       INT NOT NULL,
    from_team_id INT REFERENCES teams(id),
    to_team_id   INT REFERENCES teams(id),
    person_name  TEXT,
    from_role    VARCHAR(16),
    to_role      VARCHAR(16),
    tenure_years INT,
    eligible     BOOLEAN NOT NULL DEFAULT true,
    source       TEXT,
    person_kind  TEXT,                               -- the reviewed registry's kind (2027_02_15)
    person_key   TEXT,                               -- its key: a db row id, or JR's reviewed key
    hire_coach_id INT REFERENCES coaches(id),        -- the head-coach row the hire created: a JC-2A award's source coach
    created_at   TIMESTAMPTZ DEFAULT now()
);

-- NFLPA team report cards (2027_02_08_nflpa_report_cards.sql, NFLPA_REPORT_CARD_PLAN §2.4/§2.17h/§3.3z):
-- annual player-evaluated report cards, owner facility investment and the owner's off-cap cash pool.
CREATE TABLE IF NOT EXISTS owner_cash_ledgers (
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    wealth_band VARCHAR(12) NOT NULL CHECK (wealth_band IN ('TIER_1', 'TIER_2', 'TIER_3', 'TIER_4')),
    base_budget BIGINT NOT NULL,
    success_dividend BIGINT NOT NULL DEFAULT 0,
    total_cash_budget BIGINT NOT NULL,
    staff_payroll BIGINT NOT NULL DEFAULT 0,
    opex_committed BIGINT NOT NULL DEFAULT 0,
    capex_spent BIGINT NOT NULL DEFAULT 0,
    dead_buyouts BIGINT NOT NULL DEFAULT 0,           -- the season's staff buyouts, deducted (2027_02_19)
    accumulated_reserve BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (season, team_id)
);

CREATE TABLE IF NOT EXISTS team_report_cards (
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    weight_room VARCHAR(2) NOT NULL,
    training_room VARCHAR(2) NOT NULL,
    locker_room VARCHAR(2) NOT NULL,
    food_nutrition VARCHAR(2) NOT NULL,
    team_travel VARCHAR(2) NOT NULL,
    family_treatment VARCHAR(2) NOT NULL,
    head_coach VARCHAR(2) NOT NULL,
    ownership VARCHAR(2) NOT NULL,
    overall_score NUMERIC(4,3) NOT NULL,
    overall_grade VARCHAR(2) NOT NULL,
    summary_text TEXT,
    last_renovated_season INT,
    PRIMARY KEY (season, team_id)
);

CREATE TABLE IF NOT EXISTS facility_upgrades (
    id SERIAL PRIMARY KEY,
    team_id INT NOT NULL REFERENCES teams(id),
    season_ordered INT NOT NULL,
    category VARCHAR(20) NOT NULL,
    cost_dollars BIGINT NOT NULL,
    target_grade VARCHAR(2) NOT NULL,
    completion_season INT NOT NULL,
    status VARCHAR(12) NOT NULL DEFAULT 'IN_PROGRESS' CHECK (status IN ('IN_PROGRESS', 'COMPLETED', 'CANCELLED'))
);

CREATE TABLE IF NOT EXISTS team_operating_policies (
    team_id INT PRIMARY KEY REFERENCES teams(id),
    luxury_travel_enabled BOOLEAN NOT NULL DEFAULT false,
    no_roommate_policy BOOLEAN NOT NULL DEFAULT false,
    family_daycare_enabled BOOLEAN NOT NULL DEFAULT false,
    private_chef_program BOOLEAN NOT NULL DEFAULT false,
    annual_opex_cost BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Staff contracts & full control (2027_02_07_staff_contracts_and_full_control.sql, STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN §2.4):
-- uncapped multi-year HC/GM/OC/DC contracts funded from the owner cash pool and the league-wide Godfather-offer ledger.
CREATE TABLE IF NOT EXISTS staff_contracts (
    id SERIAL PRIMARY KEY,
    team_id INT NOT NULL REFERENCES teams(id),
    person_id INT NOT NULL,
    person_kind VARCHAR(6) NOT NULL CHECK (person_kind IN ('HC', 'GM', 'OC', 'DC')),
    annual_salary BIGINT NOT NULL,
    total_years SMALLINT NOT NULL,
    remaining_years SMALLINT NOT NULL,
    signed_season INT NOT NULL,
    full_control BOOLEAN NOT NULL DEFAULT false,
    buyout_owed BIGINT NOT NULL DEFAULT 0,
    buyout_charged_season INT,                        -- superseded by the schedule below (2027_02_19)
    buyout_total BIGINT,                              -- the whole buyout, paid in instalments (2027_02_20)
    buyout_fired_season INT,                          -- the first league year charged
    buyout_years SMALLINT,                            -- how many league years it is spread over
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
-- One active contract per person, team and role (2027_02_17_staff_contract_active_once.sql).
CREATE UNIQUE INDEX IF NOT EXISTS staff_contracts_active_once
    ON staff_contracts (team_id, person_kind, person_id) WHERE remaining_years > 0;

CREATE TABLE IF NOT EXISTS full_control_history (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    coach_id INT NOT NULL REFERENCES coaches(id),
    annual_salary BIGINT NOT NULL,
    contract_years SMALLINT NOT NULL,
    trigger_reason TEXT NOT NULL
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
    avatar_color VARCHAR(10),
    person_key VARCHAR(100)     -- the person behind the account (= agent_id; Skip's two accounts share 'Skip_Bayless'); 2027_01_18_writer_voice.sql
);
CREATE INDEX IF NOT EXISTS idx_agent_memories_person_key ON agent_memories (person_key);

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
    -- Agent Wire posts (db/migrations/2026_10_22_agent_wire_social.sql; docs/done/SOCIAL_MATRIX.md)
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
    hashtags TEXT[],
    post_shape VARCHAR(32)      -- the sampled social_writer.POST_SHAPES label (posts only; 2027_02_10)
);
CREATE INDEX IF NOT EXISTS idx_news_feed_social_week ON news_feed (season, week, content_type, posted_at);
CREATE INDEX IF NOT EXISTS idx_news_feed_agent ON news_feed (agent_id);
CREATE INDEX IF NOT EXISTS idx_news_feed_reply_to ON news_feed (reply_to_id);

-- Agent Wire weekly trends, computed from post tags and hashtags (docs/done/SOCIAL_MATRIX.md §4)
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
    scope VARCHAR(10) NOT NULL CHECK (scope IN ('SEASON', 'GAME', 'CAREER')),
    subject VARCHAR(6) NOT NULL CHECK (subject IN ('PLAYER', 'TEAM', 'COACH')),
    better VARCHAR(4) NOT NULL DEFAULT 'HIGH' CHECK (better IN ('HIGH', 'LOW')),
    sort_order INT NOT NULL,
    -- FULL: every franchise's season record and the NFL top 10 through 2025 are seeded; BOOK: only what the league's
    -- record book lists (2026 on). 2026_10_28_league_record_book.sql.
    coverage VARCHAR(4) NOT NULL DEFAULT 'FULL' CHECK (coverage IN ('FULL', 'BOOK')),
    season_type VARCHAR(16) NOT NULL DEFAULT 'REGULAR' CHECK (season_type IN ('REGULAR', 'POSTSEASON'))
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
SELECT fr.*, c.label, c.scope, c.subject, c.better, c.season_type
FROM franchise_records fr
JOIN record_categories c ON c.category = fr.category
WHERE fr.value = (SELECT CASE WHEN c.better = 'LOW' THEN MIN(x.value) ELSE MAX(x.value) END
                  FROM franchise_records x WHERE x.team_id = fr.team_id AND x.category = fr.category);

-- The NFL's all-time top 10 per category (ties included).
CREATE OR REPLACE VIEW league_top10 AS
SELECT * FROM (
    SELECT e.*, c.label, c.scope, c.subject, c.better, c.season_type,
           RANK() OVER (PARTITION BY e.category
                        ORDER BY CASE WHEN c.better = 'LOW' THEN e.value ELSE -e.value END) AS rank
    FROM league_record_entries e JOIN record_categories c ON c.category = e.category
) ranked WHERE rank <= 10;

CREATE OR REPLACE VIEW league_records AS
WITH cands AS (
    SELECT fr.category, fr.value, fr.holder, fr.team_id, fr.player_id, fr.season, NULL::date AS game_date, NULL::int AS game_id, fr.source
    FROM franchise_records_current fr JOIN record_categories c ON c.category = fr.category
    WHERE c.scope = 'SEASON' AND c.coverage = 'FULL' AND c.season_type = 'REGULAR'
    UNION
    SELECT t.category, t.value, t.holder, t.team_id, t.player_id, t.season, t.game_date, t.game_id, t.source
    FROM league_top10 t WHERE t.rank = 1
), best AS (
    SELECT c.category, CASE WHEN rc.better = 'LOW' THEN MIN(c.value) ELSE MAX(c.value) END AS value
    FROM cands c JOIN record_categories rc ON rc.category = c.category GROUP BY c.category, rc.better
)
SELECT DISTINCT ON (c.category, c.holder, c.season)
       c.category, rc.label, rc.scope, rc.subject, rc.season_type, c.value, c.holder, c.team_id, c.player_id, c.season,
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
    score_a INT,                             -- NULL for WINNER picks
    team_b_id INT NOT NULL,
    score_b INT,
    created_at TIMESTAMP DEFAULT NOW(),
    pick_type VARCHAR(8) NOT NULL DEFAULT 'SCORE',   -- SCORE | WINNER (2027_01_18_writer_voice.sql)
    winner_team_id INT,                      -- the picked winner of a WINNER pick
    UNIQUE (article_id, game_id, label)
);
CREATE INDEX IF NOT EXISTS idx_agent_predictions_agent ON agent_predictions (agent_id, season, week);

-- Season-long calls from posts, graded by agent_memory.grade_season_calls (2027_01_18_writer_voice.sql)
CREATE TABLE IF NOT EXISTS agent_season_calls (
    id SERIAL PRIMARY KEY,
    agent_id VARCHAR(100) NOT NULL,
    person_key VARCHAR(100) NOT NULL,
    season INT NOT NULL,
    made_week INT NOT NULL,
    article_id INT REFERENCES news_feed(id) ON DELETE CASCADE,
    call_type VARCHAR(24) NOT NULL,          -- MVP | OPOY | DPOY | ROY | SUPER_BOWL | PLAYOFF_TEAM | MISSES_PLAYOFFS | DIVISION_WINNER | COACH_FIRED
    subject_type VARCHAR(10),                -- player | coach | team
    subject_id INT,
    detail TEXT,
    result VARCHAR(8) NOT NULL DEFAULT 'PENDING' CHECK (result IN ('PENDING', 'RIGHT', 'WRONG', 'VOID')),
    graded_week INT
);
CREATE INDEX IF NOT EXISTS idx_agent_season_calls_person ON agent_season_calls (person_key, season);

CREATE TABLE IF NOT EXISTS agent_season_summaries (
    agent_id VARCHAR(100) NOT NULL,
    season INT NOT NULL,
    through_week INT NOT NULL,               -- covers takes and results through this week
    summary TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (agent_id, season, through_week)
);
CREATE INDEX IF NOT EXISTS idx_article_players_team_id ON article_players(team_id);

-- Postgame press conferences (2027_01_20_press_conferences.sql, newsroom/press_room.py; PROJECT_SPEC §3.3y, §7.14).
CREATE TABLE IF NOT EXISTS press_conferences (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT NOT NULL,                                -- the game's week
    game_id INT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    subject_type VARCHAR(10) NOT NULL DEFAULT 'coach' CHECK (subject_type IN ('coach', 'player')),
    subject_id INT,                                   -- coaches.id now; players.id later
    subject_name VARCHAR(255) NOT NULL,
    controller VARCHAR(5) NOT NULL CHECK (controller IN ('USER', 'AGENT')),
    status VARCHAR(10) NOT NULL DEFAULT 'SCHEDULED'
        CHECK (status IN ('SCHEDULED', 'LIVE', 'REVIEW', 'CLOSED', 'DECLINED', 'FAILED')),
    end_reason VARCHAR(16) CHECK (end_reason IN ('RAN_OUT', 'SUBJECT_ENDED', 'PATIENCE', 'CAP', 'DECLINED')),
    interest REAL, tier VARCHAR(10), cast_inputs JSONB,
    facts JSONB NOT NULL,                             -- press_facts.fact_sheet, frozen
    patience JSONB,                                   -- {"budget", "spent", "mood"} plus the session scratch keys
    model VARCHAR(100), calls INT NOT NULL DEFAULT 0, input_tokens INT NOT NULL DEFAULT 0,
    output_tokens INT NOT NULL DEFAULT 0,
    effects_applied BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(), started_at TIMESTAMP, closed_at TIMESTAMP,
    UNIQUE (game_id, team_id, subject_type, subject_id)
);
CREATE INDEX IF NOT EXISTS idx_press_conferences_week ON press_conferences (season, week, status);

CREATE TABLE IF NOT EXISTS press_attendees (
    conference_id INT NOT NULL REFERENCES press_conferences(id) ON DELETE CASCADE,
    agent_id VARCHAR(100) NOT NULL,                   -- agent_memories.agent_id
    person_key VARCHAR(100) NOT NULL,
    role VARCHAR(8) NOT NULL CHECK (role IN ('BEAT', 'NATIONAL')),
    pool_tier VARCHAR(10),
    questions INT NOT NULL DEFAULT 0, passes INT NOT NULL DEFAULT 0, done BOOLEAN NOT NULL DEFAULT FALSE,
    hands INT NOT NULL DEFAULT 0, picked INT NOT NULL DEFAULT 0,       -- rounds with a hand up / times picked
    judgment JSONB,                                   -- validated reporter_judgment output
    PRIMARY KEY (conference_id, agent_id)
);

CREATE TABLE IF NOT EXISTS press_hands (                -- who had a hand up each round, and who was picked
    conference_id INT NOT NULL REFERENCES press_conferences(id) ON DELETE CASCADE,
    round INT NOT NULL,
    agent_id VARCHAR(100) NOT NULL,
    picked BOOLEAN NOT NULL DEFAULT FALSE,
    pick_source VARCHAR(8) CHECK (pick_source IN ('AGENT', 'USER', 'FALLBACK')),   -- set on the picked row
    PRIMARY KEY (conference_id, round, agent_id)
);

CREATE TABLE IF NOT EXISTS press_turns (
    id SERIAL PRIMARY KEY,
    conference_id INT NOT NULL REFERENCES press_conferences(id) ON DELETE CASCADE,
    seq INT NOT NULL,
    speaker VARCHAR(10) NOT NULL CHECK (speaker IN ('REPORTER', 'SUBJECT')),
    agent_id VARCHAR(100),                            -- reporters only
    kind VARCHAR(10) NOT NULL CHECK (kind IN ('OPENING', 'QUESTION', 'FOLLOW_UP', 'REACT', 'ANSWER', 'NO_COMMENT',
                                              'CLOSING', 'NOTE')),
    reply_to INT REFERENCES press_turns(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    about JSONB NOT NULL DEFAULT '[]'::jsonb,
    tone VARCHAR(8),
    source VARCHAR(8) NOT NULL CHECK (source IN ('AGENT', 'USER', 'FALLBACK', 'ENGINE')),
    issues JSONB NOT NULL DEFAULT '[]'::jsonb,        -- check_line output (kept for audit; warnings on USER turns)
    retries SMALLINT NOT NULL DEFAULT 0,
    on_record BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (conference_id, seq)
);

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

-- ---------------------------------------------------------------------------
-- Napper Fantasy Platform (2026_12_02_napper_fantasy.sql, NAPPER_FANTASY_PLAN.md)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS napper_user_profiles (
    id SERIAL PRIMARY KEY,
    handle VARCHAR(32) UNIQUE NOT NULL,
    display_name VARCHAR(64) NOT NULL,
    avatar_url TEXT,
    trophies INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS napper_leagues (
    id SERIAL PRIMARY KEY,
    name VARCHAR(64) NOT NULL,
    season INT NOT NULL,
    teams_count INT NOT NULL,
    scoring_type VARCHAR(16) DEFAULT 'PPR',
    roster_format JSONB NOT NULL DEFAULT '{"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 0, "K": 1, "DEF": 1, "BN": 6, "IR": 1}'::jsonb,
    status VARCHAR(16) DEFAULT 'IN_SEASON',
    avatar_url TEXT,
    current_week INT DEFAULT 1,
    is_seed BOOLEAN DEFAULT FALSE,
    previous_league_id INT REFERENCES napper_leagues(id) ON DELETE SET NULL,
    lineage_id INT,
    completed_at TIMESTAMPTZ,
    champion_team_id INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS napper_leagues_lineage ON napper_leagues (lineage_id, season);
CREATE UNIQUE INDEX IF NOT EXISTS napper_leagues_one_successor ON napper_leagues (previous_league_id)
    WHERE previous_league_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS napper_teams (
    id SERIAL PRIMARY KEY,
    league_id INT REFERENCES napper_leagues(id) ON DELETE CASCADE,
    user_id INT REFERENCES napper_user_profiles(id) ON DELETE SET NULL,
    team_name VARCHAR(64) NOT NULL,
    manager_handle VARCHAR(32) NOT NULL,
    avatar_url TEXT,
    is_ai BOOLEAN DEFAULT FALSE,
    persona VARCHAR(32),
    wins INT DEFAULT 0,
    losses INT DEFAULT 0,
    ties INT DEFAULT 0,
    points_for NUMERIC(8,2) DEFAULT 0.00,
    points_against NUMERIC(8,2) DEFAULT 0.00,
    seed SMALLINT,
    playoff_finish VARCHAR(12),
    final_rank SMALLINT
);
CREATE INDEX IF NOT EXISTS idx_napper_teams_league ON napper_teams(league_id);

ALTER TABLE napper_leagues DROP CONSTRAINT IF EXISTS fk_napper_leagues_champion;
ALTER TABLE napper_leagues DROP CONSTRAINT IF EXISTS napper_leagues_champion_team_id_fkey;
ALTER TABLE napper_leagues ADD CONSTRAINT napper_leagues_champion_team_id_fkey FOREIGN KEY (champion_team_id) REFERENCES napper_teams(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS napper_rosters (
    id SERIAL PRIMARY KEY,
    league_id INT REFERENCES napper_leagues(id) ON DELETE CASCADE,
    team_id INT REFERENCES napper_teams(id) ON DELETE CASCADE,
    week INT NOT NULL,
    player_id INT REFERENCES players(id) ON DELETE CASCADE,
    slot VARCHAR(16) NOT NULL,
    UNIQUE(team_id, week, player_id)
);
CREATE INDEX IF NOT EXISTS idx_napper_rosters_team_week ON napper_rosters(team_id, week);
CREATE INDEX IF NOT EXISTS idx_napper_rosters_league_week ON napper_rosters(league_id, week);

CREATE TABLE IF NOT EXISTS napper_matchups (
    id SERIAL PRIMARY KEY,
    league_id INT REFERENCES napper_leagues(id) ON DELETE CASCADE,
    week INT NOT NULL,
    home_team_id INT REFERENCES napper_teams(id) ON DELETE CASCADE,
    away_team_id INT REFERENCES napper_teams(id) ON DELETE CASCADE,
    home_score NUMERIC(6,2) DEFAULT 0.00,
    away_score NUMERIC(6,2) DEFAULT 0.00,
    home_proj NUMERIC(6,2) DEFAULT 0.00,
    away_proj NUMERIC(6,2) DEFAULT 0.00,
    win_prob_home NUMERIC(4,3) DEFAULT 0.500,
    is_final BOOLEAN DEFAULT FALSE,
    playoff_round VARCHAR(16)
);
CREATE INDEX IF NOT EXISTS idx_napper_matchups_league_week ON napper_matchups(league_id, week);

CREATE TABLE IF NOT EXISTS napper_trades (
    id SERIAL PRIMARY KEY,
    league_id INT REFERENCES napper_leagues(id) ON DELETE CASCADE,
    week INT NOT NULL,
    proposer_team_id INT REFERENCES napper_teams(id) ON DELETE CASCADE,
    target_team_id INT REFERENCES napper_teams(id) ON DELETE CASCADE,
    offered_player_ids INT[] NOT NULL DEFAULT '{}',
    requested_player_ids INT[] NOT NULL DEFAULT '{}',
    status VARCHAR(16) DEFAULT 'PENDING',
    initiated_by_ai BOOLEAN DEFAULT FALSE,
    ai_rationale TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_napper_trades_league ON napper_trades(league_id);

CREATE TABLE IF NOT EXISTS napper_chat (
    id SERIAL PRIMARY KEY,
    league_id INT REFERENCES napper_leagues(id) ON DELETE CASCADE,
    sender_type VARCHAR(16) DEFAULT 'USER',
    sender_name VARCHAR(64) NOT NULL,
    sender_handle VARCHAR(32) NOT NULL,
    avatar_url TEXT,
    message_text TEXT,
    card_payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_napper_chat_league ON napper_chat(league_id, created_at);

CREATE TABLE IF NOT EXISTS napper_drafts (
    id SERIAL PRIMARY KEY,
    league_id INT REFERENCES napper_leagues(id) ON DELETE CASCADE,
    season INT NOT NULL,
    rounds INT DEFAULT 15,
    teams_count INT DEFAULT 10,
    scoring_type VARCHAR(16) DEFAULT 'PPR',
    status VARCHAR(16) DEFAULT 'PENDING',
    current_round INT DEFAULT 1,
    current_pick INT DEFAULT 1,
    is_mock BOOLEAN DEFAULT FALSE,
    user_slot INT DEFAULT 1,
    settings JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_napper_drafts_league ON napper_drafts(league_id);

CREATE TABLE IF NOT EXISTS napper_draft_picks (
    id SERIAL PRIMARY KEY,
    draft_id INT REFERENCES napper_drafts(id) ON DELETE CASCADE,
    round_num INT NOT NULL,
    pick_num INT NOT NULL,
    overall_pick INT NOT NULL,
    team_id INT REFERENCES napper_teams(id) ON DELETE CASCADE,
    team_name VARCHAR(64),
    slot_num INT NOT NULL,
    player_id INT REFERENCES players(id) ON DELETE CASCADE,
    picked_by VARCHAR(16) DEFAULT 'AI',
    pick_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(draft_id, overall_pick)
);
CREATE INDEX IF NOT EXISTS idx_napper_draft_picks_draft ON napper_draft_picks(draft_id);
CREATE INDEX IF NOT EXISTS idx_napper_draft_picks_draft_overall ON napper_draft_picks(draft_id, overall_pick);
CREATE INDEX IF NOT EXISTS idx_napper_draft_picks_player ON napper_draft_picks(player_id);

CREATE TABLE IF NOT EXISTS napper_waiver_claims (
    id SERIAL PRIMARY KEY,
    league_id INT NOT NULL REFERENCES napper_leagues(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES napper_teams(id) ON DELETE CASCADE,
    add_player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    drop_player_id INT REFERENCES players(id) ON DELETE SET NULL,
    rank SMALLINT NOT NULL DEFAULT 1,
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    reason VARCHAR(16),
    note TEXT,
    week INT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_napper_waiver_claims_pending ON napper_waiver_claims (league_id, status);

CREATE TABLE IF NOT EXISTS napper_waiver_runs (
    league_id INT NOT NULL REFERENCES napper_leagues(id) ON DELETE CASCADE,
    week INT NOT NULL,
    priority INT[] NOT NULL,
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (league_id, week)
);

CREATE TABLE IF NOT EXISTS napper_waiver_wire (
    league_id INT NOT NULL REFERENCES napper_leagues(id) ON DELETE CASCADE,
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    dropped_by_team_id INT REFERENCES napper_teams(id) ON DELETE SET NULL,
    clears_week INT NOT NULL,
    dropped_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (league_id, player_id)
);


-- Verified progression event ledger and authoritative permanent ratings.
-- Authoritative Mu base ratings and verified progression accounting.
-- Keep player_progression_history as the compatible annual summary.
CREATE TABLE IF NOT EXISTS progression_player_state (
    player_id INT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    application_season INT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    base_ratings JSONB NOT NULL,
    band JSONB NOT NULL,
    source_fingerprint TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS progression_events (
    event_id TEXT PRIMARY KEY,
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    earned_season INT NOT NULL,
    application_season INT NOT NULL,
    kind TEXT NOT NULL,
    ordinal TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    base_revision BIGINT NOT NULL,
    candidate_base JSONB NOT NULL,
    candidate_band JSONB NOT NULL,
    overlay JSONB NOT NULL DEFAULT '{}'::jsonb,
    annual_deltas JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_purpose TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'PREPARED' CHECK (status IN ('PREPARED', 'APPLIED')),
    output_fingerprint TEXT,
    edits_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_at TIMESTAMPTZ,
    UNIQUE (player_id, earned_season, kind, ordinal, rules_version)
);
CREATE TABLE IF NOT EXISTS progression_annual_accounting (
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    earned_season INT NOT NULL,
    applied JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (player_id, earned_season)
);
CREATE TABLE IF NOT EXISTS progression_evidence_consumption (
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    earned_season INT NOT NULL,
    purpose TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES progression_events(event_id),
    PRIMARY KEY (player_id, earned_season, purpose, evidence_id)
);
CREATE INDEX IF NOT EXISTS progression_events_player_season
    ON progression_events (player_id, earned_season, status);

-- Relationship event producers (2027_01_28_relationship_producers.sql, engines/relationship_producers/store.py,
-- docs/done/RELATIONSHIP_EVENT_PRODUCERS_PLAN.md §2.3). Keyed by league_id so held events can exist
-- before the personality lifecycle registers the universe.
CREATE TABLE IF NOT EXISTS relationship_producer_runs (
    league_id BIGINT NOT NULL,
    season INT NOT NULL,
    tick SMALLINT NOT NULL CHECK (tick BETWEEN 1 AND 32),
    rules_version TEXT NOT NULL,
    source VARCHAR(8) NOT NULL CHECK (source IN ('SAVE','CATCHUP')),
    input_hash TEXT NOT NULL,          -- sha256 of the canonical inputs read for this tick
    registry_hash TEXT NOT NULL,
    event_hashes JSONB NOT NULL,       -- {source_event_id|facet: payload_hash} appended or held
    counts JSONB NOT NULL,
    status VARCHAR(10) NOT NULL CHECK (status IN ('APPENDED','HELD','NOOP','FAILED')),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_id, season, tick, rules_version)
);
CREATE TABLE IF NOT EXISTS relationship_roster_observations (
    league_id BIGINT NOT NULL,
    season INT NOT NULL,
    tick SMALLINT NOT NULL,
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    team_id INT REFERENCES teams(id),  -- NULL: free agent
    source VARCHAR(8) NOT NULL CHECK (source IN ('SAVE','CATCHUP')),
    PRIMARY KEY (league_id, season, tick, player_id)
);
CREATE TABLE IF NOT EXISTS relationship_pending_events (
    league_id BIGINT NOT NULL,
    source_event_id TEXT NOT NULL,
    facet_key TEXT NOT NULL,
    subject_alias JSONB NOT NULL,      -- {role, namespace, external_id}
    envelope JSONB NOT NULL,           -- complete except subject.persona_id
    status VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPENDED','DROPPED')),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    appended_at TIMESTAMPTZ,
    PRIMARY KEY (league_id, source_event_id, facet_key)
);
CREATE INDEX IF NOT EXISTS relationship_pending_open ON relationship_pending_events (league_id, status);

-- Front-office inbox (2027_01_19_front_office_inbox.sql, engines/inbox/, docs/done/FRONT_OFFICE_INBOX_PLAN.md §2.6)
CREATE TABLE IF NOT EXISTS controlled_personas (
    team_id     INT NOT NULL REFERENCES teams(id),
    role        VARCHAR(8) NOT NULL CHECK (role IN ('GM','COACH','PLAYER')),
    persona_id  VARCHAR(40) NOT NULL,              -- gm_profiles.id | coaches.id | players.id
    active      BOOLEAN NOT NULL DEFAULT true,
    taken_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ,
    PRIMARY KEY (team_id, role, persona_id)
);   -- JR controls no one by default (D8); rows of teams outside the registry's user_teams are ignored

CREATE TABLE IF NOT EXISTS inbox_threads (
    id                SERIAL PRIMARY KEY,
    team_id           INT NOT NULL REFERENCES teams(id),
    kind              VARCHAR(24) NOT NULL,                    -- registry key; no CHECK (sibling plans add kinds)
    actor_role        VARCHAR(8) NOT NULL CHECK (actor_role IN ('GM','COACH','PLAYER','TEAM')),
    actor_id          VARCHAR(40) NOT NULL,                    -- the controlled persona; 'TEAM' + abbr for notices
    counterpart_type  VARCHAR(16) NOT NULL
        CHECK (counterpart_type IN ('gm','coach','player','writer','prospect','scout','owner','system')),
    counterpart_id    VARCHAR(100) NOT NULL,                   -- gm_profiles.id, coaches.id, players.id, agent_memories.agent_id, prospect_uid
    counterpart_name  VARCHAR(255) NOT NULL,
    subject           VARCHAR(255) NOT NULL,
    status            VARCHAR(12) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','WAITING_JR','CLOSED','EXPIRED')),
    started_by        VARCHAR(12) NOT NULL CHECK (started_by IN ('JR','COUNTERPART','SYSTEM')),
    source_ref        VARCHAR(120) NOT NULL,
    link_table        VARCHAR(48),
    link_id           INT,
    state             JSONB NOT NULL DEFAULT '{}',             -- kind-owned: {"questions_asked": 4}, {"promises": [...]}
    season            INT NOT NULL,
    week              INT,
    offseason_stage   VARCHAR(16),
    expires_season    INT,
    expires_week      INT,
    unread            BOOLEAN NOT NULL DEFAULT true,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (team_id, kind, source_ref)
);
CREATE INDEX IF NOT EXISTS inbox_threads_team_status ON inbox_threads (team_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS inbox_messages (
    id           SERIAL PRIMARY KEY,
    thread_id    INT NOT NULL REFERENCES inbox_threads(id) ON DELETE CASCADE,
    seq          SMALLINT NOT NULL,
    author       VARCHAR(12) NOT NULL CHECK (author IN ('JR','COUNTERPART','SYSTEM')),
    body         TEXT NOT NULL,
    body_source  VARCHAR(10) NOT NULL CHECK (body_source IN ('JR','LLM','TEMPLATE','ENGINE')),
    option_id    VARCHAR(40),
    options      JSONB NOT NULL DEFAULT '[]',
    facts        JSONB,                       -- never sent to the UI
    engine_ref   JSONB,
    appraisal    JSONB,                       -- the counterpart's appraisal of the JR message this replies to (NULL = none)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (thread_id, seq)
);

CREATE TABLE IF NOT EXISTS inbox_relationship_events (     -- outbox for PERSONALITY_RELATIONSHIPS_PLAN's store
    id               SERIAL PRIMARY KEY,
    source_event_id  VARCHAR(120) NOT NULL UNIQUE,         -- 'inbox:msg:812', 'inbox:promise:44:1:made'
    kind             VARCHAR(32) NOT NULL CHECK (kind IN ('meaningful_interaction','treatment','promise_made','promise_resolved')),
    thread_id        INT REFERENCES inbox_threads(id) ON DELETE SET NULL,
    message_id       INT REFERENCES inbox_messages(id) ON DELETE SET NULL,
    event            JSONB NOT NULL,                       -- the full §2.2 envelope (tick and PersonRefs filled at flush)
    sim_time         JSONB NOT NULL,                       -- {season, week, offseason_stage}
    status           VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPENDED','REJECTED')),
    status_reason    TEXT,
    appended_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS team_vision (
    team_id       INT NOT NULL REFERENCES teams(id),
    season        INT NOT NULL,
    direction     VARCHAR(8) CHECK (direction IN ('CONTEND','RETOOL','REBUILD')),   -- NULL = no pin
    emphasis      VARCHAR(8) NOT NULL DEFAULT 'BALANCED' CHECK (emphasis IN ('OFFENSE','DEFENSE','BALANCED')),
    playing_time  VARCHAR(8) NOT NULL DEFAULT 'MERIT' CHECK (playing_time IN ('YOUTH','VETERANS','MERIT')),
    thread_id     INT REFERENCES inbox_threads(id) ON DELETE SET NULL,
    agreed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, season)
);

-- Scouting reports and combine interviews (2027_01_21_scouting_reports.sql, engines/prospect_ids.py,
-- pipeline/prospect_ratings.py; docs/done/SCOUTING_REVAMP_PLAN.md §2.1). Placed after the inbox tables because
-- scout_interviews references inbox_threads. scout_prospect_ratings is hidden (never in a view, endpoint or #debug).
-- Permanent prospect IDs (§2.1b). prospect_key is only the current alias into the older scouting tables.
CREATE TABLE IF NOT EXISTS scout_prospect_ids (
    prospect_uid     UUID PRIMARY KEY,                 -- uuid5(MU_PROSPECT_NS, birth_key); never changes
    birth_key        TEXT NOT NULL UNIQUE,             -- 'save-pgid:<draft_season>:<pgid>' or 'board:<draft_season>:<first prospect_key>'
    draft_season     INT NOT NULL,
    pgid             BIGINT,                           -- NULL for a scanned (PS5) class
    prospect_key     VARCHAR(80) NOT NULL,             -- current alias
    key_history      JSONB NOT NULL DEFAULT '[]',      -- [{"prospect_key","until_capture"}]
    status           VARCHAR(12) NOT NULL DEFAULT 'OK' CHECK (status IN ('OK','KEY_COLLISION')),
    source_person_id UUID,                             -- set by the lifecycle plan when it adopts this person
    first_seen       VARCHAR(255) NOT NULL,            -- capture folder
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS scout_prospect_ids_key ON scout_prospect_ids (draft_season, prospect_key);

-- Hidden: never in a view, endpoint, writer tool or #debug panel.
CREATE TABLE IF NOT EXISTS scout_prospect_ratings (
    prospect_uid   UUID PRIMARY KEY REFERENCES scout_prospect_ids(prospect_uid),
    draft_season   INT NOT NULL,
    prospect_key   VARCHAR(80) NOT NULL,            -- alias at load time
    pgid           BIGINT,
    ratings        JSONB NOT NULL,                  -- Madden Player.<Name>Rating names without "Rating", 0-99
    capture_folder VARCHAR(255) NOT NULL,
    loaded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scout_reports (
    draft_season   INT NOT NULL,
    step           SMALLINT NOT NULL,
    prospect_uid   UUID NOT NULL REFERENCES scout_prospect_ids(prospect_uid),
    prospect_key   VARCHAR(80) NOT NULL,            -- alias at the step, for joins with scout_consensus/scout_events
    edition        VARCHAR(8) NOT NULL CHECK (edition IN ('FALL','BOWLS','FINAL')),
    grade          NUMERIC(5,1) NOT NULL,           -- the step's consensus grade (copied, public)
    rank           INT NOT NULL,                    -- the step's consensus rank (public)
    round_label    VARCHAR(16) NOT NULL,
    role           VARCHAR(24) NOT NULL,            -- ROLE_BANDS key (§2.3)
    traits         JSONB NOT NULL,                  -- [{"trait","label","score","word"}], [] without ratings
    strengths      JSONB NOT NULL,                  -- ["trait", ...] 0-3
    concerns       JSONB NOT NULL,                  -- ["trait" or flag, ...] 0-3
    flags          JSONB NOT NULL,                  -- ["INJURY_HISTORY", ...] (§2.3)
    body           JSONB NOT NULL,                  -- {"summary","strengths","concerns","production","athletic","projection","bottom_line"}
    text_source    VARCHAR(8) NOT NULL DEFAULT 'TEMPLATE' CHECK (text_source IN ('TEMPLATE','LLM')),
    scout_id       INT,                             -- NULL = the Mu Scouting Department (phase 6 hook)
    voice          JSONB,                           -- NULL = department voice (phase 6 hook)
    team_id        INT REFERENCES teams(id),        -- NULL = the department (public) row; 2027_02_11_scout_report_team_rows.sql
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (draft_season, step) REFERENCES scout_runs (draft_season, step) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS scout_reports_dept_once
    ON scout_reports (prospect_uid, edition) WHERE team_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS scout_reports_team_once
    ON scout_reports (prospect_uid, edition, team_id) WHERE team_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS scout_interviews (
    id               SERIAL PRIMARY KEY,
    draft_season     INT NOT NULL,
    team_id          INT NOT NULL REFERENCES teams(id),
    prospect_uid     UUID NOT NULL REFERENCES scout_prospect_ids(prospect_uid),
    prospect_key     VARCHAR(80) NOT NULL,          -- alias when opened (VISIT events are written under the current alias)
    kind             VARCHAR(8) NOT NULL CHECK (kind IN ('COMBINE','VISIT')),
    thread_id        INT REFERENCES inbox_threads(id) ON DELETE SET NULL,   -- the PROSPECT inbox thread
    window_step      SMALLINT NOT NULL,             -- latest stored step when opened (24 or 25)
    logged_step      SMALLINT,                      -- VISIT: the step whose VISIT event it became (25 or 26); NULL until then
    status           VARCHAR(8) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')),
    questions_max    SMALLINT NOT NULL,
    tone             VARCHAR(8) NOT NULL CHECK (tone IN ('STRONG','STEADY','MIXED')),
    disposition_src  VARCHAR(12) NOT NULL DEFAULT 'NEUTRAL' CHECK (disposition_src IN ('NEUTRAL','PERSONALITY')),
    turns            JSONB NOT NULL DEFAULT '[]',   -- [{"seq","topic","grade","classified_by"}]; words live in inbox_messages
    summary_grade    VARCHAR(12),                   -- ANSWER_GRADES value, the interview's overall read (set on close)
    opened_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at        TIMESTAMPTZ,
    UNIQUE (draft_season, team_id, prospect_uid, kind)
);

-- ---------------------------------------------------------------------------
-- Personality tables (folded from the 2027_01_16 .. 2027_02_03 migrations; their guard DO blocks, which refuse an
-- incompatible existing table, stay in the migrations). Each migration is a no-op on a database built from this file.
-- ---------------------------------------------------------------------------

-- Personality core sidecars (2027_01_16_personality_core.sql + 2027_01_29_personality_core_extensions.sql,
-- personality/; PROJECT_SPEC §3.3p, §7.11). The catalogue row is written by the personality code, not seeded here.
CREATE TABLE IF NOT EXISTS persona_core_catalogues (
    version TEXT PRIMARY KEY,
    catalogue JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS persona_core_profiles (
    persona_type VARCHAR(32) NOT NULL,
    persona_id VARCHAR(64) NOT NULL,
    universe_id TEXT NOT NULL,
    birth_key TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('generated', 'real', 'unknown')),
    catalogue_version TEXT NOT NULL REFERENCES persona_core_catalogues(version),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    metadata JSONB NOT NULL,
    PRIMARY KEY (persona_type, persona_id),
    UNIQUE (universe_id, persona_type, birth_key),
    FOREIGN KEY (persona_type, persona_id) REFERENCES persona_sliders(persona_type, persona_id)
);
CREATE TABLE IF NOT EXISTS persona_core_aliases (
    universe_id TEXT NOT NULL,
    persona_type VARCHAR(32) NOT NULL,
    namespace TEXT NOT NULL,
    external_id TEXT NOT NULL,
    persona_id VARCHAR(64) NOT NULL,
    PRIMARY KEY (universe_id, persona_type, namespace, external_id),
    FOREIGN KEY (persona_type, persona_id) REFERENCES persona_core_profiles(persona_type, persona_id)
);
-- Columns kind .. recorded_at are 2027_01_29's (kept last, in its order); its partial index replaces
-- 2027_01_16's UNIQUE (persona_type, persona_id, season), so only evolution is one-a-season.
CREATE TABLE IF NOT EXISTS persona_core_events (
    persona_type VARCHAR(32) NOT NULL,
    persona_id VARCHAR(64) NOT NULL,
    event_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    request JSONB NOT NULL,
    before_state JSONB NOT NULL,
    after_state JSONB NOT NULL,
    kind TEXT NOT NULL DEFAULT 'evolution' CHECK (kind IN ('evolution', 'override', 'adoption', 'role_link')),
    effective_tick SMALLINT CHECK (effective_tick BETWEEN 1 AND 32),
    actor TEXT,
    seq BIGINT GENERATED ALWAYS AS IDENTITY,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (persona_type, persona_id, event_id),
    UNIQUE (persona_type, persona_id, source_event_id),
    FOREIGN KEY (persona_type, persona_id) REFERENCES persona_core_profiles(persona_type, persona_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS persona_core_events_one_evolution_per_season
    ON persona_core_events (persona_type, persona_id, season) WHERE kind = 'evolution';

CREATE TABLE IF NOT EXISTS persona_core_links (
    universe_id TEXT NOT NULL,
    from_type VARCHAR(32) NOT NULL,
    from_id VARCHAR(64) NOT NULL,
    to_type VARCHAR(32) NOT NULL,
    to_id VARCHAR(64) NOT NULL,
    link_kind TEXT NOT NULL CHECK (link_kind = 'role_change'),
    effective_season INT NOT NULL,
    effective_tick SMALLINT CHECK (effective_tick BETWEEN 1 AND 32),
    receipt JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (to_type, to_id),
    UNIQUE (from_type, from_id, to_type),
    FOREIGN KEY (from_type, from_id) REFERENCES persona_core_profiles(persona_type, persona_id),
    FOREIGN KEY (to_type, to_id) REFERENCES persona_core_profiles(persona_type, persona_id)
);

CREATE INDEX IF NOT EXISTS idx_persona_core_links_from ON persona_core_links (from_type, from_id);

-- Person lifecycle ledgers (2027_01_24_personality_lifecycle.sql; PROJECT_SPEC §3.3p).
CREATE TABLE IF NOT EXISTS persona_lifecycle_universes (
    universe_id TEXT PRIMARY KEY,
    league_id TEXT NOT NULL UNIQUE,
    initial_snapshot_sha256 TEXT NOT NULL,
    start_season INTEGER NOT NULL,
    start_cutoff JSONB NOT NULL,
    manifest_version TEXT NOT NULL,
    registration JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS persona_lifecycle_people (
    universe_id TEXT NOT NULL REFERENCES persona_lifecycle_universes(universe_id),
    source_person_id UUID NOT NULL,
    role TEXT NOT NULL,
    birth_key TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('generated', 'real', 'unknown')),
    classification_receipt JSONB NOT NULL,
    first_seen JSONB NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'ready', 'quarantined', 'archived')),
    persona_id VARCHAR(64),
    revision INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (universe_id, source_person_id, role),
    UNIQUE (universe_id, role, birth_key),
    FOREIGN KEY (role, persona_id) REFERENCES persona_core_profiles(persona_type, persona_id)
);

CREATE TABLE IF NOT EXISTS persona_lifecycle_bindings (
    universe_id TEXT NOT NULL REFERENCES persona_lifecycle_universes(universe_id),
    role TEXT NOT NULL,
    namespace TEXT NOT NULL,
    external_id TEXT NOT NULL,
    source_person_id UUID NOT NULL,
    valid_from JSONB NOT NULL,
    valid_to JSONB,
    receipt JSONB NOT NULL,
    PRIMARY KEY (universe_id, role, namespace, external_id),
    FOREIGN KEY (universe_id, source_person_id, role)
        REFERENCES persona_lifecycle_people(universe_id, source_person_id, role)
);

CREATE INDEX IF NOT EXISTS persona_lifecycle_bindings_person
    ON persona_lifecycle_bindings (universe_id, source_person_id, role);

CREATE TABLE IF NOT EXISTS persona_lifecycle_batches (
    batch_id TEXT PRIMARY KEY,
    universe_id TEXT NOT NULL REFERENCES persona_lifecycle_universes(universe_id),
    manifest JSONB NOT NULL,
    before_state JSONB NOT NULL,
    after_state JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('applied', 'reverted')),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    review_receipt JSONB NOT NULL,
    revert_receipt JSONB
);

-- Contract policy receipts (2027_01_23_contract_personality_policy.sql, engines/personality_contract_store.py; §3.3u).
CREATE TABLE IF NOT EXISTS contract_policy_decisions (
    universe_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,          -- the actual negotiation attempt / batch entry, never player-season
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    stage TEXT NOT NULL,
    player_id BIGINT NOT NULL,
    persona_id TEXT,                    -- NULL: no profile (neutral with reason)
    revision INTEGER,
    mode TEXT NOT NULL CHECK (mode IN ('off', 'shadow', 'active')),
    policy_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,           -- sha256 of the canonical snapshot
    snapshot JSONB NOT NULL,
    result JSONB NOT NULL,              -- links existing offer / execution ids
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, decision_id)
);

CREATE TABLE IF NOT EXISTS contract_policy_event_outbox (
    universe_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('CONTRACT_OFFER_SUBMITTED', 'CONTRACT_RESPONSE_RECORDED',
                                       'CONTRACT_EXECUTED', 'CONTRACT_INSTRUMENT_APPLIED')),
    payload JSONB NOT NULL,
    delivered_at TIMESTAMPTZ,
    PRIMARY KEY (universe_id, event_id),
    FOREIGN KEY (universe_id, decision_id) REFERENCES contract_policy_decisions (universe_id, decision_id)
);

-- Player trade requests (2027_01_25_player_trade_requests.sql, engines/player_trade_request_store.py; §3.3v).
CREATE TABLE IF NOT EXISTS player_trade_request_decisions (
    universe_id TEXT NOT NULL,
    persona_id VARCHAR(64) NOT NULL,
    season INTEGER NOT NULL,
    decision_id UUID NOT NULL UNIQUE,
    outcome TEXT NOT NULL CHECK (outcome IN ('REQUEST', 'NO_REQUEST')),
    input_hash TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    seed_algorithm TEXT NOT NULL,
    draw DOUBLE PRECISION NOT NULL CHECK (draw >= 0 AND draw < 1),
    receipt JSONB NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, persona_id, season)
);

CREATE TABLE IF NOT EXISTS player_trade_requests (
    request_id UUID PRIMARY KEY,
    decision_id UUID NOT NULL UNIQUE REFERENCES player_trade_request_decisions (decision_id),
    universe_id TEXT NOT NULL,
    persona_type VARCHAR(32) NOT NULL CHECK (persona_type = 'player'),
    persona_id VARCHAR(64) NOT NULL,
    team_id INTEGER NOT NULL REFERENCES teams (id),
    opened_season INTEGER NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'WITHDRAWN', 'RESOLVED_TRADE', 'CLOSED_EMPLOYMENT',
                                           'VOIDED_CORRECTION')),
    state JSONB NOT NULL CHECK (jsonb_typeof(state) = 'object' AND state->>'status' = status
                                AND (state->>'revision')::int = revision),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (persona_type, persona_id) REFERENCES persona_core_profiles (persona_type, persona_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS player_trade_requests_one_active
    ON player_trade_requests (universe_id, persona_id) WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS player_trade_request_actions (
    universe_id TEXT NOT NULL,
    persona_id VARCHAR(64) NOT NULL,
    event_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    request_id UUID NULL REFERENCES player_trade_requests (request_id),
    season INTEGER NOT NULL,
    window_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    action_kind TEXT NOT NULL CHECK (action_kind IN ('REVIEW', 'ACKNOWLEDGE', 'DECLINE', 'EXPLORE', 'REVIEW_REPAIR',
                                                     'RECORD_WITHDRAWAL', 'RECORD_TRADE', 'CLOSE_EMPLOYMENT',
                                                     'CORRECT', 'DISCLOSE')),
    payload_hash TEXT NOT NULL,
    before_state JSONB NULL,
    after_state JSONB NULL,
    receipt JSONB NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, persona_id, event_id),
    UNIQUE (universe_id, persona_id, source_event_id, action_kind),
    UNIQUE (universe_id, persona_id, season, window_id, checkpoint_id, action_kind)
);

-- Relationship ledger (2027_01_27_personality_relationships.sql, personality/relationship_store.py; §3.3t).
CREATE TABLE IF NOT EXISTS persona_relationship_batches (
    universe_id TEXT NOT NULL,
    head BIGINT NOT NULL CHECK (head > 0),
    expected_head BIGINT NOT NULL CHECK (expected_head >= 0 AND expected_head = head - 1),
    batch_hash TEXT NOT NULL,
    receipt JSONB NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, head),
    UNIQUE (universe_id, batch_hash)
);

CREATE TABLE IF NOT EXISTS persona_relationship_events (
    universe_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    facet_key TEXT NOT NULL,           -- relationship_events.facet_key: hash of subject / target / kind
    event_id TEXT NOT NULL,            -- transport ID, checked separately for content conflicts
    head BIGINT NOT NULL CHECK (head > 0),
    occurred_season INT NOT NULL,
    occurred_tick INT NOT NULL CHECK (occurred_tick >= 0),
    schema_version TEXT NOT NULL CHECK (schema_version IN ('relationships-v1')),
    payload_hash TEXT NOT NULL,
    envelope JSONB NOT NULL CHECK (jsonb_typeof(envelope) = 'object'
                                   AND envelope->>'schema_version' = schema_version
                                   AND envelope->>'source_event_id' = source_event_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, source_event_id, facet_key),
    UNIQUE (universe_id, event_id, facet_key),
    FOREIGN KEY (universe_id, head) REFERENCES persona_relationship_batches (universe_id, head)
);

CREATE INDEX IF NOT EXISTS persona_relationship_events_time
    ON persona_relationship_events (universe_id, occurred_season, occurred_tick);

CREATE TABLE IF NOT EXISTS persona_relationship_sources (
    universe_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    external_key TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    PRIMARY KEY (universe_id, namespace, external_key)
);

-- Personality evolution records (2027_01_30_personality_evolution.sql, personality/evolution_store.py; §3.3p).
CREATE TABLE IF NOT EXISTS persona_evolution_runs (
    universe_id TEXT NOT NULL,
    season INT NOT NULL,
    rules_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,          -- sha256 of the season facts and external candidates the run read
    counts JSONB NOT NULL,
    status VARCHAR(10) NOT NULL CHECK (status IN ('APPLIED','PREVIEW','FAILED')),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, season, rules_version)
);

CREATE TABLE IF NOT EXISTS persona_evolution_candidates (
    candidate_id TEXT PRIMARY KEY,     -- '<universe>:<persona>:<season>:<rule_id | external:<event_id>>'
    universe_id TEXT NOT NULL,
    persona_type VARCHAR(32) NOT NULL,
    persona_id VARCHAR(64) NOT NULL,
    season INT NOT NULL,
    source VARCHAR(16) NOT NULL CHECK (source IN ('RULE','RELATIONSHIPS','DEVELOPMENT','OTHER')),
    rule_id TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    change JSONB NOT NULL,             -- {kind, slider/step | remove/add}
    chance NUMERIC(5,4),               -- NULL for external candidates (reviewed, not drawn)
    draw DOUBLE PRECISION,
    source_event_ids TEXT[] NOT NULL,
    note TEXT,
    status VARCHAR(12) NOT NULL CHECK (status IN ('drawn_out','blocked','superseded','applied','reverted')),
    reason TEXT,
    core_event_id TEXT,                -- the persona_core_events event_id of an applied change
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (universe_id, persona_type, persona_id, season, rule_id)
);

CREATE INDEX IF NOT EXISTS persona_evolution_candidates_season
    ON persona_evolution_candidates (universe_id, season, status);

-- Personality feature switchboard (2027_01_31_personality_feature_modes.sql, personality/features.py; §3.3p).
-- Seeds nothing: a missing row reads 'off'.
CREATE TABLE IF NOT EXISTS personality_feature_modes (
    league_id BIGINT NOT NULL,
    feature TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('off','shadow','active')),
    version TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}',
    review_ref TEXT,                       -- JR's review note or report reference
    shadow_since_season INT,               -- when the current version entered shadow (D3)
    shadow_since_tick SMALLINT,
    set_by TEXT NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_id, feature)
);

CREATE TABLE IF NOT EXISTS personality_feature_mode_changes (
    id BIGSERIAL PRIMARY KEY,
    league_id BIGINT NOT NULL,
    feature TEXT NOT NULL,
    from_mode TEXT CHECK (from_mode IN ('off','shadow','active')),
    to_mode TEXT NOT NULL CHECK (to_mode IN ('off','shadow','active')),
    version TEXT NOT NULL,
    review_ref TEXT,
    force_reason TEXT,                     -- set when JR skipped the order or shadow-season rule
    at_season INT NOT NULL,
    at_tick SMALLINT NOT NULL,
    set_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS personality_feature_clearances (
    league_id BIGINT NOT NULL,
    feature TEXT NOT NULL,
    version TEXT NOT NULL,
    source_db TEXT NOT NULL,               -- e.g. madden_tune_2026a
    source_label TEXT NOT NULL,            -- e.g. CAREER-TUNE-2026a
    shadow_from_season INT NOT NULL,
    shadow_from_tick SMALLINT NOT NULL,
    shadow_to_season INT NOT NULL,
    shadow_to_tick SMALLINT NOT NULL,
    report_path TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
    config JSONB NOT NULL,                 -- the config tuned on the tuning franchise
    cleared_by TEXT NOT NULL,
    cleared_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_id, feature, version)
);

-- Agent loop decisions (2027_02_01_agent_loops.sql, engines/agent_loops/base.py).
CREATE TABLE IF NOT EXISTS agent_loop_decisions (
    id BIGSERIAL PRIMARY KEY,
    league_id BIGINT NOT NULL,
    season INT NOT NULL,
    tick SMALLINT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('CHART','PROMISE','PROMISE_REVIEW','CONTRACT','INBOX_REPLY','LEAK')),
    subject_key TEXT NOT NULL,              -- e.g. 'team:12:chart', 'player:4411:promise', 'player:4411:offer:88'
    persona_type VARCHAR(32),
    persona_id VARCHAR(64),
    options JSONB NOT NULL,                 -- [{key, action, detail, label}]: labels only, never a hidden score
    policy_key TEXT NOT NULL,               -- the seeded personality policy's choice (always computed)
    chosen_key TEXT NOT NULL,
    choice_source TEXT NOT NULL CHECK (choice_source IN ('POLICY','MODEL','MODEL_REJECTED')),
    text TEXT,                              -- the model's words, kept only when grounded in facts
    visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','public')),
    facts JSONB NOT NULL,
    cause_refs JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (league_id, season, tick, kind, subject_key)
);

CREATE INDEX IF NOT EXISTS agent_loop_decisions_public
    ON agent_loop_decisions (league_id, season, tick) WHERE visibility = 'public';

-- Persona focus (2027_02_03_persona_focus.sql, personality/focus_store.py; §3.3p).
CREATE TABLE IF NOT EXISTS persona_focus (
    focus_id TEXT PRIMARY KEY,                 -- 'focus:<universe_id>:<persona_id>:<slot>:<seq>'
    universe_id TEXT NOT NULL,
    persona_type VARCHAR(32) NOT NULL CHECK (persona_type IN ('coach','owner')),
    persona_id VARCHAR(64) NOT NULL,
    slot VARCHAR(4) NOT NULL CHECK (slot IN ('want','fear')),
    seq INT NOT NULL CHECK (seq >= 0),         -- 0 = from the profile, +1 per later row
    text TEXT NOT NULL,
    subjects JSONB NOT NULL DEFAULT '[]',      -- [{kind, name, binding {namespace, external_id} | null}]
    anchor JSONB NOT NULL DEFAULT '{}',        -- {team, seat} of the holder when the row was written
    until_season INT,
    source VARCHAR(8) NOT NULL CHECK (source IN ('profile','template','jr')),
    template_id TEXT,
    rules_version TEXT NOT NULL,
    status VARCHAR(10) NOT NULL CHECK (status IN ('active','superseded','reverted')),
    valid_from_season INT NOT NULL,
    valid_from_tick SMALLINT NOT NULL CHECK (valid_from_tick BETWEEN 1 AND 32),
    supersedes TEXT REFERENCES persona_focus(focus_id),
    stale_causes JSONB NOT NULL DEFAULT '[]',
    reason TEXT,                               -- the change note
    actor TEXT NOT NULL DEFAULT 'system',      -- 'JR' for set and revert
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (universe_id, persona_id, slot, seq),
    FOREIGN KEY (persona_type, persona_id) REFERENCES persona_core_profiles(persona_type, persona_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS persona_focus_one_active
    ON persona_focus (universe_id, persona_id, slot) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS persona_focus_runs (
    universe_id TEXT NOT NULL,
    season INT NOT NULL,
    rules_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,                  -- sha256 of the season facts and active rows the run read
    counts JSONB NOT NULL,
    status VARCHAR(10) NOT NULL CHECK (status IN ('APPLIED','PREVIEW','FAILED')),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (universe_id, season, rules_version)
);

-- Real NFL history 1999-2025 (2027_02_04_nfl_history.sql, docs/done/REAL_NFL_HISTORY_PLAN.md §2.2;
-- PROJECT_SPEC §3.3x). Own nfl_* tables, never the league's; written only by `python3 -m engines.nfl_history`.

CREATE TABLE IF NOT EXISTS nfl_players (            -- one real person (nflverse players.csv), upserted, never deleted
    gsis_id        TEXT PRIMARY KEY,
    espn_id        TEXT,
    pfr_id         TEXT,
    display_name   TEXT NOT NULL,
    first_name     TEXT,
    last_name      TEXT,
    position       VARCHAR(8),
    birth_date     DATE,
    college        TEXT,
    height_in      SMALLINT,
    weight         SMALLINT,
    rookie_season  INT,
    last_season    INT,
    draft_year     INT,
    draft_round    SMALLINT,
    draft_pick     SMALLINT,
    draft_team_abbr VARCHAR(4)
);
CREATE INDEX IF NOT EXISTS nfl_players_espn ON nfl_players (espn_id);
CREATE INDEX IF NOT EXISTS nfl_players_pfr  ON nfl_players (pfr_id);

CREATE TABLE IF NOT EXISTS nfl_player_seasons (     -- nflverse stats_player_{reg,post}_<season>.csv
    gsis_id      TEXT NOT NULL REFERENCES nfl_players(gsis_id),
    season       INT NOT NULL,
    season_type  VARCHAR(4) NOT NULL CHECK (season_type IN ('REG', 'POST')),
    team_id      INT REFERENCES teams(id),           -- current franchise of team_abbr
    team_abbr    VARCHAR(4) NOT NULL,                -- nflverse's code that season (OAK, SD, STL, LA, ...)
    position     VARCHAR(8),
    games_played INT,
    -- engines.nfl_history.store.STAT_COLUMNS; NULL = nflverse has no value
    completions INT, pass_attempts INT, pass_yards INT, pass_tds INT, interceptions INT, pass_sacks INT,
    rush_attempts INT, rush_yards INT, rush_tds INT, rush_fumbles_lost INT, fumb INT,
    rec_targets INT, receptions INT, rec_yards INT, rec_tds INT,
    def_tackles INT, def_solo INT, def_assists INT, def_tfl INT, def_sacks NUMERIC(4,1), def_ints INT,
    def_int_yds INT, def_deflections INT, def_forced_fumbles INT, def_fumble_recoveries INT, def_fumble_yds INT,
    def_safeties INT, def_tds INT,
    kick_fgm INT, kick_fga INT, kick_fg_blocked INT, kick_fg_long INT,
    kick_fgm_29 INT, kick_fga_29 INT, kick_fgm_39 INT, kick_fga_39 INT,
    kick_fgm_49 INT, kick_fga_49 INT, kick_fgm_50 INT, kick_fga_50 INT,
    kick_xpm INT, kick_xpa INT, kick_xp_blocked INT,
    punt_punts INT, punt_yards INT, punt_net_yards INT, punt_inside_20 INT, punt_touchbacks INT, punt_blocked INT,
    kr_returns INT, kr_yards INT, kr_tds INT, pr_returns INT, pr_yards INT, pr_tds INT, st_tds INT,
    PRIMARY KEY (gsis_id, season, season_type)
);
CREATE INDEX IF NOT EXISTS nfl_player_seasons_season ON nfl_player_seasons (season, season_type);

CREATE TABLE IF NOT EXISTS nfl_games (              -- nflverse schedules/games.csv
    game_id      TEXT PRIMARY KEY,                   -- nflverse id, e.g. 2019_21_TEN_KC
    season       INT NOT NULL,
    week         INT NOT NULL,
    game_type    VARCHAR(4) NOT NULL CHECK (game_type IN ('REG', 'WC', 'DIV', 'CON', 'SB')),
    gameday      DATE,
    home_team_id INT REFERENCES teams(id),
    away_team_id INT REFERENCES teams(id),
    home_abbr    VARCHAR(4) NOT NULL,
    away_abbr    VARCHAR(4) NOT NULL,
    home_score   INT,
    away_score   INT,
    overtime     BOOLEAN,
    home_coach   TEXT,
    away_coach   TEXT
);
CREATE INDEX IF NOT EXISTS nfl_games_season ON nfl_games (season, game_type);

CREATE TABLE IF NOT EXISTS nfl_team_seasons (
    season          INT NOT NULL,
    team_id         INT NOT NULL REFERENCES teams(id),
    team_abbr       VARCHAR(4) NOT NULL,
    conference      VARCHAR(3),
    division        TEXT,                            -- the division that season
    wins            INT NOT NULL,
    losses          INT NOT NULL,
    ties            INT NOT NULL,
    points_for      INT NOT NULL,
    points_against  INT NOT NULL,
    division_place  SMALLINT CHECK (division_place >= 1),                  -- up to 6 before 2002
    conference_seed SMALLINT CHECK (conference_seed BETWEEN 1 AND 7),     -- NULL = missed
    playoff_result  VARCHAR(16) NOT NULL CHECK (playoff_result IN
        ('MISSED', 'WILD_CARD', 'DIVISIONAL', 'CONFERENCE', 'SUPER_BOWL_LOSS', 'CHAMPION')),
    playoff_wins    INT NOT NULL DEFAULT 0,
    playoff_losses  INT NOT NULL DEFAULT 0,
    head_coach      TEXT,                            -- coached most of the regular season
    head_coach_id   INT REFERENCES coaches(id) ON DELETE SET NULL,         -- only when he is in this database's coaches
    standings_source VARCHAR(8) NOT NULL CHECK (standings_source IN ('NFLDATA', 'COMPUTED')),
    PRIMARY KEY (season, team_id)
);

CREATE TABLE IF NOT EXISTS nfl_coach_seasons (      -- from nfl_games' per-game coach
    coach_name      TEXT NOT NULL,
    season          INT NOT NULL,
    team_id         INT NOT NULL REFERENCES teams(id),
    coach_id        INT REFERENCES coaches(id) ON DELETE SET NULL,
    wins            INT NOT NULL,
    losses          INT NOT NULL,
    ties            INT NOT NULL,
    games           INT NOT NULL,
    full_season     BOOLEAN NOT NULL,                -- coached every regular-season game of the team
    playoff_wins    INT NOT NULL DEFAULT 0,
    playoff_losses  INT NOT NULL DEFAULT 0,
    playoff_result  VARCHAR(16) NOT NULL,
    super_bowl_wins INT NOT NULL DEFAULT 0,
    super_bowl_losses INT NOT NULL DEFAULT 0,
    PRIMARY KEY (coach_name, season, team_id)
);

CREATE TABLE IF NOT EXISTS nfl_awards (
    id          SERIAL PRIMARY KEY,
    season      INT NOT NULL,
    award_type  VARCHAR(32) NOT NULL,                -- engines.nfl_history.awards.NFL_AWARD_TYPES
    gsis_id     TEXT REFERENCES nfl_players(gsis_id),
    pfr_id      TEXT,
    player_name TEXT,
    coach_name  TEXT,
    team_id     INT REFERENCES teams(id),
    team_abbr   VARCHAR(4),
    position    VARCHAR(8)
);
CREATE UNIQUE INDEX IF NOT EXISTS nfl_awards_key
    ON nfl_awards (season, award_type, coalesce(position, ''), coalesce(gsis_id, pfr_id, coach_name, player_name));

CREATE TABLE IF NOT EXISTS nfl_draft_picks (        -- nflverse draft_picks/draft_picks.csv
    season      INT NOT NULL,
    overall     SMALLINT NOT NULL,
    round       SMALLINT NOT NULL,
    pick        SMALLINT,
    team_id     INT REFERENCES teams(id),
    team_abbr   VARCHAR(4) NOT NULL,
    gsis_id     TEXT REFERENCES nfl_players(gsis_id),
    pfr_id      TEXT,
    player_name TEXT NOT NULL,
    position    VARCHAR(8),
    college     TEXT,
    PRIMARY KEY (season, overall)
);

CREATE TABLE IF NOT EXISTS nfl_player_links (       -- this database's Madden player <-> the real person
    player_id INT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    gsis_id   TEXT NOT NULL UNIQUE REFERENCES nfl_players(gsis_id),
    method    VARCHAR(10) NOT NULL CHECK (method IN ('EXACT', 'ESPN', 'ALIAS', 'FUZZY', 'OVERRIDE')),
    score     NUMERIC(5, 2),
    evidence  JSONB NOT NULL DEFAULT '{}',
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS nfl_history_loads (      -- one row per applied command
    id           SERIAL PRIMARY KEY,
    kind         VARCHAR(16) NOT NULL CHECK (kind IN ('players', 'stats', 'games', 'teams', 'awards', 'draft',
                                                      'link', 'coach_baseline')),
    first_season INT,
    last_season  INT,
    files        JSONB NOT NULL DEFAULT '[]',        -- [{"url", "cache_name", "sha256", "bytes"}]
    counts       JSONB NOT NULL DEFAULT '{}',
    report       JSONB NOT NULL DEFAULT '{}',
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);

CREATE OR REPLACE VIEW nfl_season_lines AS
SELECT s.*, p.display_name, l.player_id, 'NFL'::varchar(8) AS source
FROM nfl_player_seasons s
JOIN nfl_players p ON p.gsis_id = s.gsis_id
LEFT JOIN nfl_player_links l ON l.gsis_id = s.gsis_id;

-- ---------------------------------------------------------------------------
-- Owners, scouting directors and media-voter ballots
-- (db/migrations/2027_02_02_owners_scouts_voters.sql; OWNERS_SCOUTS_VOTERS_PLAN §2.6)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS team_owners (
    id           SERIAL PRIMARY KEY,
    team_id      INT NOT NULL REFERENCES teams(id),
    owner_name   TEXT NOT NULL,
    profile_key  TEXT NOT NULL,
    start_season INT NOT NULL,
    end_season   INT,
    UNIQUE (team_id, start_season)
);

-- move_ref is JSONB, which Postgres can't index in a plain B-tree UNIQUE constraint, so the idempotency key is a
-- unique index on its canonical text form (jsonb text is deterministic for one value).
CREATE TABLE IF NOT EXISTS owner_decisions (
    id               BIGSERIAL PRIMARY KEY,
    season           INT NOT NULL,
    tick             SMALLINT NOT NULL,
    team_id          INT NOT NULL REFERENCES teams(id),
    move_kind        TEXT NOT NULL CHECK (move_kind IN ('TRADE', 'SIGNING', 'HIRE')),
    move_ref         JSONB NOT NULL,
    decision         TEXT NOT NULL CHECK (decision IN ('APPROVE', 'VETO', 'PUSH')),
    alternative      JSONB,
    loop_decision_id BIGINT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS owner_decisions_once
    ON owner_decisions (season, tick, team_id, move_kind, (move_ref::text));

CREATE TABLE IF NOT EXISTS scout_directors (
    id           SERIAL PRIMARY KEY,
    team_id      INT NOT NULL REFERENCES teams(id),
    name         TEXT NOT NULL,
    persona_id   VARCHAR(64),
    start_season INT NOT NULL,
    end_season   INT,
    UNIQUE (team_id, start_season)
);

CREATE TABLE IF NOT EXISTS award_ballots (
    season         INT NOT NULL,
    award          TEXT NOT NULL,
    voter          TEXT NOT NULL,
    ranking        JSONB NOT NULL,
    ballot_version TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (season, award, voter)
);

-- ---------------------------------------------------------------------------
-- Migration ledger (schema_migrations; db/migrate.py)
-- ---------------------------------------------------------------------------

-- >>> MIGRATION LEDGER
-- Generated by `python3 -m db.migrate sync-init-db`; do not edit by hand.
-- db/init_db.sql mirrors every file under db/migrations/, so a database built from this file is at
-- the head of the list: `python3 -m db.migrate status` reports nothing pending and apply is a no-op.
-- <<< MIGRATION LEDGER

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    filename   TEXT NOT NULL UNIQUE,
    checksum   TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source     TEXT NOT NULL DEFAULT 'applied'
);

INSERT INTO schema_migrations (version, filename, checksum, source) VALUES
    ('2026_10_12_fantasy_points_generated', '2026_10_12_fantasy_points_generated.sql', '3e43b69526ed2f563bc6c2cfc8ee9d5e94b46641b7c3779be15e91e5de56e597', 'init_db.sql'),
    ('2026_10_13_projected_excitement', '2026_10_13_projected_excitement.sql', '91bdd64b60356dddce83f376c6b658881ef98a444c2510f4e7836d2314fdbb95', 'init_db.sql'),
    ('2026_10_14_opp_adj_credit', '2026_10_14_opp_adj_credit.sql', '44f3311050aec1311c7da0e87e63aca05e672a57364edb2cac1d90efd61ed2be', 'init_db.sql'),
    ('2026_10_15_stored_index_components', '2026_10_15_stored_index_components.sql', 'b9a1f44139b7966aa9b15fc61abc4aa3ecb240f7c97c29ae08cdfa98303726b1', 'init_db.sql'),
    ('2026_10_16_trajectory_and_efficiency', '2026_10_16_trajectory_and_efficiency.sql', '0bcb4b6725a0e453e6d5d6bdb42cf6d4c9f558d958a098de0d8b4e1492223455', 'init_db.sql'),
    ('2026_10_17_playoff_clout_awards_exposure', '2026_10_17_playoff_clout_awards_exposure.sql', '90dc70c4757d250795777370c448d415203d124e66370150560548aab91a8e45', 'init_db.sql'),
    ('2026_10_18_power_rankings', '2026_10_18_power_rankings.sql', 'a318f1f468874b2a73a98ac7a197e805662d947e050353cfbe5dd111e0eb9c93', 'init_db.sql'),
    ('2026_10_19_roundtable_panel_agent', '2026_10_19_roundtable_panel_agent.sql', '15b405aab073fe236390eb18d18de70e8128bf613f757da1af19e689f671148e', 'init_db.sql'),
    ('2026_10_20_agent_memory', '2026_10_20_agent_memory.sql', 'aa388040dd1f90a4bcc301999f6d0d75dc7c7002ac49660958d8d25598fd1f9e', 'init_db.sql'),
    ('2026_10_21_fantasy_league_sim', '2026_10_21_fantasy_league_sim.sql', '54e8eb6aee6a254fdb29a23a51f8c2cc65f9a71f497b8c3a40cac4bd4c2c03f0', 'init_db.sql'),
    ('2026_10_22_agent_wire_social', '2026_10_22_agent_wire_social.sql', 'bd23cfb88a5ff5c3f51e87fdff8382c5c41b3c2dd3fced11f5c1bf48adc146fe', 'init_db.sql'),
    ('2026_10_23_capture_loader', '2026_10_23_capture_loader.sql', 'd4ef10d068f022a4fedf0e5d95e83bfbca50581e39f97ad3d33459d145bf88ab', 'init_db.sql'),
    ('2026_10_25_former_players', '2026_10_25_former_players.sql', 'd1e1b2c876511dea8ee4bf0e83624e51b861c79f27c4958c3109d50a3e052eb8', 'init_db.sql'),
    ('2026_10_26_nfl_records', '2026_10_26_nfl_records.sql', 'bf2dbe99683ea2a5201b457a4f46af17a242ec80e3866aa83c6fd0dac2c5e911', 'init_db.sql'),
    ('2026_10_28_league_record_book', '2026_10_28_league_record_book.sql', '15545cb1cf541bc55ffe893a17e52a7f45ac27f77483481ad0fd0a22eb0ac22a', 'init_db.sql'),
    ('2026_10_29_team_box_estimates', '2026_10_29_team_box_estimates.sql', '60bd9770c5987376c63d2bcac0c2f1b9fffe546bc1eed9e856e08ee213c30848', 'init_db.sql'),
    ('2026_10_30_coaches', '2026_10_30_coaches.sql', 'a58ff1b6cfe6aa50af9bd8332cc4c6c20bf2764193a819a436ca01fa44247d0b', 'init_db.sql'),
    ('2026_10_31_coach_baseline', '2026_10_31_coach_baseline.sql', '26479691c5f6000d86344a57970cb3062e4899f8cad351ad8b4b88abaa85e951', 'init_db.sql'),
    ('2026_10_31_team_box_exact_fields', '2026_10_31_team_box_exact_fields.sql', '3cff239d94658a6f39277a8ad8dcdea021337229b1be80e3ab78ab87905f77ea', 'init_db.sql'),
    ('2026_11_01_coach_schemes', '2026_11_01_coach_schemes.sql', 'b5920f0b39445f5b827291e02e1e5f5faf00ec1eea987096044a3f3ff4fd60af', 'init_db.sql'),
    ('2026_11_02_drop_coach_archetype', '2026_11_02_drop_coach_archetype.sql', 'a6d14bcddaaf734d3b4c88fb961d961afad7c884a4f32d978ea4fb4be0b83895', 'init_db.sql'),
    ('2026_11_03_whitfield_2034', '2026_11_03_whitfield_2034.sql', '8728b650527d3b9010614d60a80848a3c598572c28c2712b09f2f465d1ca756c', 'init_db.sql'),
    ('2026_11_04_playoff_games', '2026_11_04_playoff_games.sql', '8c5659b0d26b0a2acfd8891c1be445a7e0512f22eac95f6499c6452184acadcf', 'init_db.sql'),
    ('2026_11_05_coach_tendencies', '2026_11_05_coach_tendencies.sql', '1d961dcd7a030de2cc3a0b5624c1a8da8aacc76b482d265eed9c4500d06cf760', 'init_db.sql'),
    ('2026_11_06_coty_scores', '2026_11_06_coty_scores.sql', '6a50a0435ea06c800629d1badecd55703ed5f8ca888ab190883d298da99b98dd', 'init_db.sql'),
    ('2026_11_07_coach_2034_totals', '2026_11_07_coach_2034_totals.sql', '1994d53122dcd1ef231fd1b4cc7bdea0c011d9af34cc20bd050952599cb7f4f8', 'init_db.sql'),
    ('2026_11_08_injuries', '2026_11_08_injuries.sql', 'e647a893b096bd31ff861b1856516f2b44802e3df9b794abe8702b5f2ee30c58', 'init_db.sql'),
    ('2026_11_09_coach_full_names', '2026_11_09_coach_full_names.sql', 'ba816f4387ad4809b29810b218c1dd4a8607bc9b0ce6f26fc640b3de0945b32f', 'init_db.sql'),
    ('2026_11_10_whitfield_tendencies', '2026_11_10_whitfield_tendencies.sql', '30d56545a1ed7581196a245842592bd58300c860a2a6418ea2d14287c9b8ad4b', 'init_db.sql'),
    ('2026_11_11_whitfield_ratings', '2026_11_11_whitfield_ratings.sql', 'd0e173c60aa410b0912a8221cd2bf33da6b7f19ec62d196aa42b14869a6c592d', 'init_db.sql'),
    ('2026_11_12_defense_kicking_records', '2026_11_12_defense_kicking_records.sql', '3c6e5aef6e583819cd4fd60fbd2e3857124579de648e0ef2bb2ff05ba231fc3f', 'init_db.sql'),
    ('2026_11_13_observed_tendencies', '2026_11_13_observed_tendencies.sql', 'd4d8a6e3701dcc4f2b7cbd55ab68f997a482c83234a128d08fe287254853f79c', 'init_db.sql'),
    ('2026_11_14_player_stats_line_source', '2026_11_14_player_stats_line_source.sql', '5232cc6696c8828df3dbd43c3e72b8236695f5ec70f956098770c6628f5895f6', 'init_db.sql'),
    ('2026_11_15_defense_kicking_history', '2026_11_15_defense_kicking_history.sql', 'a4191fe0bd85911c157ca7b8a0a1e21f9171beee600c00677f8e9190fa79ee54', 'init_db.sql'),
    ('2026_11_16_record_book_names', '2026_11_16_record_book_names.sql', '66c67226cba62553f1c31710c7d9e498b85a7921088cad05bff9e0b2c4bbc602', 'init_db.sql'),
    ('2026_11_17_record_book_names_2', '2026_11_17_record_book_names_2.sql', '789cc13d6089cece50e1ad9a7f46b144a880d5210f9538d55e2b1e91076a11c1', 'init_db.sql'),
    ('2026_11_18_game_scripts', '2026_11_18_game_scripts.sql', '08769fa152f0c692c2f222efa2be4c24592630beeedc26c9b884488428f43a79', 'init_db.sql'),
    ('2026_11_19_draft_board', '2026_11_19_draft_board.sql', 'c57d3420c9695e4bfbb70ff4ad31c435ad528a26e2c8f6d3105fc835c287677b', 'init_db.sql'),
    ('2026_11_20_all_pro_awards', '2026_11_20_all_pro_awards.sql', 'e61fe1c485f98724ea26f3c7770370fe826435d1f04cc04efd0145af118f06bc', 'init_db.sql'),
    ('2026_11_23_game_highlights', '2026_11_23_game_highlights.sql', 'c60f8137553db713c4e6ea6418a265b43fc30bd8ce3d1b1a4dc1bcca2d25f351', 'init_db.sql'),
    ('2026_11_23_postseason_records', '2026_11_23_postseason_records.sql', 'fc4a2210de1332d848e9bb2290f0652feb32deed2379995814b5e07e68b7edcd', 'init_db.sql'),
    ('2026_11_24_coty_historical_records', '2026_11_24_coty_historical_records.sql', '1c68aa53f90e15ce52767ff63ab3d275c3bdc54f67bdaeb2eb41b64a512c7a1b', 'init_db.sql'),
    ('2026_11_25_coordinators_and_slants', '2026_11_25_coordinators_and_slants.sql', 'eaaac5ffda916909f2f0fedb44210cfa7b5e8b29c739228ade32ee18142a6105', 'init_db.sql'),
    ('2026_11_26_draft_selections', '2026_11_26_draft_selections.sql', 'cb1dd1b7712ad4e9c3f0c32150449df078cf6059c60c637b28979b6fd20aed01', 'init_db.sql'),
    ('2026_11_27_whitfield_bears_coordinator_lore', '2026_11_27_whitfield_bears_coordinator_lore.sql', '6d6d6a7ba8453198f76738feebc06a6ce7e23b0bfd60efb2adb5fc80dc16057e', 'init_db.sql'),
    ('2026_11_28_panthers_bears_coordinators_update', '2026_11_28_panthers_bears_coordinators_update.sql', '622e42d916514050ca634c6514f1cc03327d961eaa118937f14aa48f5840bd61', 'init_db.sql'),
    ('2026_11_29_panthers_history_backfill', '2026_11_29_panthers_history_backfill.sql', 'd7703e9d94d719c1a1b18a391a9e415e87bc3b7c455e9abb35d9dfb789332278', 'init_db.sql'),
    ('2026_11_29_panthers_shavon_bell_dc', '2026_11_29_panthers_shavon_bell_dc.sql', 'c45ceaee3ebd8ac0e56a1178497b943980cac976017f03a468022ab67d044c89', 'init_db.sql'),
    ('2026_11_30_panthers_2034_playoff_statlines', '2026_11_30_panthers_2034_playoff_statlines.sql', 'e1d09bd68315ce981f91d00a4a64aed80f06ae13550fa9106178df7a086e649b', 'init_db.sql'),
    ('2026_12_01_coach_tenures_correction', '2026_12_01_coach_tenures_correction.sql', 'b28ff5777fc025f8bfc211717960aee1a87bd49bf963b8570226afc8a60ba099', 'init_db.sql'),
    ('2026_12_02_napper_fantasy', '2026_12_02_napper_fantasy.sql', '7cc00bed69fb9c1bef83effdd57bc933bc61e1127853abfca3e01b798d5d5c60', 'init_db.sql'),
    ('2026_12_03_napper_active_season', '2026_12_03_napper_active_season.sql', 'a04279ecdd98ec30379c4257131bac607b1c9bd9855b8ce6efdca558e91e8680', 'init_db.sql'),
    ('2026_12_04_napper_ir_slot', '2026_12_04_napper_ir_slot.sql', '3a76d367586243ddeaf47e6d203a5895edf87da8733df8cdf5fee850f16b2a8a', 'init_db.sql'),
    ('2026_12_05_napper_waivers', '2026_12_05_napper_waivers.sql', 'ace4dd97e85d1bd2f7d9fdeab5193267d90b53e8894d11fcd09d88c26c763e30', 'init_db.sql'),
    ('2026_12_06_napper_playoffs', '2026_12_06_napper_playoffs.sql', '33729ca1da53ca36be264fcb2ae8cf0f158a7aec47d36a7e32a2076d61e90e67', 'init_db.sql'),
    ('2026_12_07_contracts', '2026_12_07_contracts.sql', 'fdb3e546b486856a374001c1ac72068e3d4d5abb8f9abac474b8e5f161104af8', 'init_db.sql'),
    ('2026_12_08_transactions', '2026_12_08_transactions.sql', '85bbc5d86bf51426e64bfb3ebe77eec11a0714b62d2c54dcbb096e770a00dd81', 'init_db.sql'),
    ('2026_12_09_transactions_restructured', '2026_12_09_transactions_restructured.sql', '44f9582d83eddce15918d1d19f4e8c432a8e29e3d9073745645063f251a07cb7', 'init_db.sql'),
    ('2026_12_10_gm_agents', '2026_12_10_gm_agents.sql', 'dd3b3738ca2fac2a5387679cbeafea14a96979045d6e6e14e359f54b977f5732', 'init_db.sql'),
    ('2026_12_11_draft_pick_owners', '2026_12_11_draft_pick_owners.sql', 'f6c41b76df04147ce437b6b74ee980fc31519565c5136badec79e753a75b0dbf', 'init_db.sql'),
    ('2026_12_12_scouting', '2026_12_12_scouting.sql', 'd5487240a09dcca720bfec0674bc11a44b2b7a290d79035ffe7aeb321f7709f1', 'init_db.sql'),
    ('2026_12_13_gm_pacing', '2026_12_13_gm_pacing.sql', '05cce86f1d6013f87d8dd314330a9ce9c35d5db8071edfa8e3f5e9de963adc53', 'init_db.sql'),
    ('2026_12_14_coordinator_tenure_repair', '2026_12_14_coordinator_tenure_repair.sql', '061c6ca25d3884f864afc257145ae7c070c8944dff7550642ed14d6079d7e2dd', 'init_db.sql'),
    ('2026_12_15_colts_oc_evan_marsh', '2026_12_15_colts_oc_evan_marsh.sql', '54cca0b09f2e448040261fa33e6f2135d9088662ae26f4ef08fa255d4d743de4', 'init_db.sql'),
    ('2026_12_16_name_2036_coordinators', '2026_12_16_name_2036_coordinators.sql', '80dac2e8999e67c379e11de268235cd68260c2781d6b7178295ce97517ae333a', 'init_db.sql'),
    ('2026_12_17_dev_quality', '2026_12_17_dev_quality.sql', 'd2057d1abe29bc2dd4718c7e42901dc18f94e5ab131b1f5fa50b1c59b8bd65dc', 'init_db.sql'),
    ('2026_12_18_gm_first_draft', '2026_12_18_gm_first_draft.sql', 'db17d17963f2608acb9aaebb4e0ef82dda1c54430574b32bf1c1eedffd76d9f8', 'init_db.sql'),
    ('2026_12_19_charles_grant_full_control', '2026_12_19_charles_grant_full_control.sql', '50573a679334d59b32f591dd47d12c441dfe8015a5c53486c1a965225c7e74a7', 'init_db.sql'),
    ('2026_12_20_marcus_whitfield_full_control', '2026_12_20_marcus_whitfield_full_control.sql', '8c96b4a73b60c30c15dae236374f438ef23222a7ccd2da60f8b5cef5b8ed0668', 'init_db.sql'),
    ('2026_12_21_gm_negotiations', '2026_12_21_gm_negotiations.sql', 'e673c9a2391ea84259a5919e19415e0927e41eb7cb8707d697050b9d8751406f', 'init_db.sql'),
    ('2026_12_22_coach_hot_seat', '2026_12_22_coach_hot_seat.sql', 'd2bbe37541dbed8ab818c7c07775b5a254fa76be2c9a4baa2c2346737a2ce8b0', 'init_db.sql'),
    ('2026_12_23_coaching_carousel', '2026_12_23_coaching_carousel.sql', 'a8ebc01e0555976e2be4e803fdf2a09e241a211bfb8d51de686bc20993449ced', 'init_db.sql'),
    ('2026_12_24_season_archive', '2026_12_24_season_archive.sql', '1becefb2a088edf2a25669c07bfe0c8fa31fdf1931fcf6120085a330cb2c3d47', 'init_db.sql'),
    ('2026_12_24_staff_moves_inputs', '2026_12_24_staff_moves_inputs.sql', 'ecacdaf87043093b9b778c130b7a7538864fa364c2408af22b15605346134d37', 'init_db.sql'),
    ('2026_12_25_saleh_2025_baseline', '2026_12_25_saleh_2025_baseline.sql', '4518f9275b44380b6ad22214101c76dfcc516a097db991cbd0d747e21362e6a8', 'init_db.sql'),
    ('2026_12_26_player_career_totals', '2026_12_26_player_career_totals.sql', '587808b1fff04db1af7f98bfa72a97e85da6f896d8e2b01a18981b300cd5412d', 'init_db.sql'),
    ('2026_12_27_career_fit_fixes', '2026_12_27_career_fit_fixes.sql', 'e5825c2dc0ee02a78c8343aeb3a8e8f6d707eedbe600ddc8e430c6b8db4d3aa9', 'init_db.sql'),
    ('2026_12_28_history_team_arc', '2026_12_28_history_team_arc.sql', '5b3b1bfb8b7e22672a51b13f864aefa39fc91ac3127dffc18e2874ea214e281a', 'init_db.sql'),
    ('2026_12_29_franklin_2032_record', '2026_12_29_franklin_2032_record.sql', '116fad3b3eeb0e1653008e2f57487c0f68b059ff53c0aeb7c101d9a90c5f4113', 'init_db.sql'),
    ('2026_12_29_history_player_lines', '2026_12_29_history_player_lines.sql', '2b91a8172e8db3363715345acc9f0936d236a4f8021ab75e6c6da0530272dde0', 'init_db.sql'),
    ('2026_12_30_history_player_rows', '2026_12_30_history_player_rows.sql', '5b199a6b84afbf429ea4aa7580bbba4f11fd4f759019e2b99c57103b59eb7a2a', 'init_db.sql'),
    ('2026_12_31_awards_source', '2026_12_31_awards_source.sql', 'ad1c720111edf9b4fe4d1661d7f5342ca81a2d3bdd3d9d79444ba97a8b3ba56a', 'init_db.sql'),
    ('2027_01_01_record_book_implied', '2027_01_01_record_book_implied.sql', '1de7c0d669fc042f2c16d8ba63965f82b703a9c8432cd99c12a17991fb636e49', 'init_db.sql'),
    ('2027_01_02_draft_round_starts', '2027_01_02_draft_round_starts.sql', '9f5c11371eb6deac63dd9e896c1fd7e428109d3b0b859e3df39822072a444c0b', 'init_db.sql'),
    ('2027_01_03_player_season_ledger', '2027_01_03_player_season_ledger.sql', 'a83c6f03fe73e1609c1d1f0873d5afeb4e3e797ee96035227f4dd7f93bf53a7a', 'init_db.sql'),
    ('2027_01_04_napper_lineage', '2027_01_04_napper_lineage.sql', 'f9cc436e3670a87fc8cd4b8f63310d5ccdd606d9b9afa53610681e9a6db8b986', 'init_db.sql'),
    ('2027_01_05_madden_pgid', '2027_01_05_madden_pgid.sql', 'ecacef126af93436b1ba12f937297330c67f92e3de9d3fc1e11ef544a8e5b250', 'init_db.sql'),
    ('2027_01_06_player_attributes', '2027_01_06_player_attributes.sql', '2ee8ccd990d6132309514c5afda32f09f95930ad1fd311720238859354b730c8', 'init_db.sql'),
    ('2027_01_07_player_name_aliases', '2027_01_07_player_name_aliases.sql', '6e4a577760046db9d37983e9d968cc7a20b91a65905dd7cf0f5678024ce75d5a', 'init_db.sql'),
    ('2027_01_07_writer_identity_lines', '2027_01_07_writer_identity_lines.sql', '23ba74869c6ea4d683a882efd49fe19d0d3b57b970d049264284297bf3a8c010', 'init_db.sql'),
    ('2027_01_08_player_progression', '2027_01_08_player_progression.sql', 'aea9bc4a0d81faa11c0ef58b548f01a3a8d2abaf20f3ec4b61cfa2b24a1fa675', 'init_db.sql'),
    ('2027_01_09_personas', '2027_01_09_personas.sql', '69ce33a8704f4134027006810f2ca8416ae3499c24a5f6a403ded5ad224c61c7', 'init_db.sql'),
    ('2027_01_10_league_memory', '2027_01_10_league_memory.sql', 'fc487a0658775b2bacc75629608473eb39aa6a5a3273e48586df0376ea17bbf8', 'init_db.sql'),
    ('2027_01_11_progression_rating_groups', '2027_01_11_progression_rating_groups.sql', '183101880f7db7bda59a4203bb766cd1e951bfad3d0fbb307dbecdd2e72fe28e', 'init_db.sql'),
    ('2027_01_12_kdst_fantasy_scores', '2027_01_12_kdst_fantasy_scores.sql', '4804913e0c47331bfaab39335c65bce7bb14d91fba3380bdf92dd4747ba605fc', 'init_db.sql'),
    ('2027_01_13_franchise_identity', '2027_01_13_franchise_identity.sql', '3fa54731476824683f9621b7de1c7c8c16b46769913b8cfde669d796dc605e85', 'init_db.sql'),
    ('2027_01_14_v2_franchise_os', '2027_01_14_v2_franchise_os.sql', '103284bee12f2c80ec47578c618f4da77f84017a96b138f159d0d8ca9b50031b', 'init_db.sql'),
    ('2027_01_15_progression_state', '2027_01_15_progression_state.sql', 'fb1a595f1d6ad2ee49f490579089ea870cfb658e7a7fce590b3ee19bdec60f80', 'init_db.sql'),
    ('2027_01_16_personality_core', '2027_01_16_personality_core.sql', '5221e258ae6154e8033302c14bfcd9f42f1b6b4eec6ffa51256d2a343d0abfac', 'init_db.sql'),
    ('2027_01_17_persona_slider_band', '2027_01_17_persona_slider_band.sql', 'f02d03cf8cb07e8950f881c6cbdd300fb8347ac2fc0549f9646210a51f5ab321', 'init_db.sql'),
    ('2027_01_18_writer_voice', '2027_01_18_writer_voice.sql', '5e045bc7a8e97109ea6a6586c4c411bc7804bae974e1afa67b2ceab393b42d20', 'init_db.sql'),
    ('2027_01_19_front_office_inbox', '2027_01_19_front_office_inbox.sql', '0200651fb1aa40a76d4a26ad94877d5591db8a185f9303722e057e504095ecf3', 'init_db.sql'),
    ('2027_01_20_press_conferences', '2027_01_20_press_conferences.sql', '3e92c6d609a06c485ae806c825ca598918ed4c8efe7aec91afcd19ece569b92d', 'init_db.sql'),
    ('2027_01_21_scouting_reports', '2027_01_21_scouting_reports.sql', '967ef0aaad4bd7a9461ab6404f336fd2193ebd7d736373e3af705ce867916301', 'init_db.sql'),
    ('2027_01_22_trade_talks', '2027_01_22_trade_talks.sql', '20a5672116db64585141462c233b129f95edf07b85fbc8b47aae4c11326799d3', 'init_db.sql'),
    ('2027_01_23_contract_personality_policy', '2027_01_23_contract_personality_policy.sql', '36521d4aa3cf4a490b0a6b5dc371757f690ea99cf878837f89f42f958cd7cc1a', 'init_db.sql'),
    ('2027_01_24_personality_lifecycle', '2027_01_24_personality_lifecycle.sql', 'c77a75b00f804e3958cdb63aa4012f69a624eb4f528b5b57ab63a7a5c8bdb89d', 'init_db.sql'),
    ('2027_01_25_player_trade_requests', '2027_01_25_player_trade_requests.sql', '7ee1f86b2f123a811d480ec5262d2016d1baa981ec8d30b006456abdd57d7d7a', 'init_db.sql'),
    ('2027_01_26_retirement_policy', '2027_01_26_retirement_policy.sql', '9361a8ee71fec2083a5e2e6e564e0321e41ec45cb2ec979324958f20ba8fc669', 'init_db.sql'),
    ('2027_01_27_personality_relationships', '2027_01_27_personality_relationships.sql', 'd23aa3aea648ba3aaa55bc8c40e4036fa656b06d02b96b078ec39a0cf579aa53', 'init_db.sql'),
    ('2027_01_28_relationship_producers', '2027_01_28_relationship_producers.sql', '17df1676c5e1158e127d5456575e7c2fbb66ac6a43e7cea5bdfe86f29a85ac70', 'init_db.sql'),
    ('2027_01_29_personality_core_extensions', '2027_01_29_personality_core_extensions.sql', 'e86fe5dbb9c6766b27bc91b6e03dd5b96bab5e375882154b8d6525efafddc530', 'init_db.sql'),
    ('2027_01_30_personality_evolution', '2027_01_30_personality_evolution.sql', '0f59683f426571cf28c2c4c570f11f21cb6ba5afec058e3c8751b5e43c2a8def', 'init_db.sql'),
    ('2027_01_31_personality_feature_modes', '2027_01_31_personality_feature_modes.sql', 'fe97fb811f83947ba3ee2dc09996da8d27d2afbc1387f1d3470a011f2872dcdc', 'init_db.sql'),
    ('2027_02_01_agent_loops', '2027_02_01_agent_loops.sql', '02cfcaa2235fc2e517598ddbeaf21d66dbac02e3fe1f89a9ebebf90d70c77c8b', 'init_db.sql'),
    ('2027_02_02_owners_scouts_voters', '2027_02_02_owners_scouts_voters.sql', '132185af5359d8a230488fbc9f9c220d993c422453353897609310cb7be2ef3f', 'init_db.sql'),
    ('2027_02_03_persona_focus', '2027_02_03_persona_focus.sql', 'e1f494a2e9fda63866ab269fddf71c5c2eb86ab3b86095a5b6701d2112a9d333', 'init_db.sql'),
    ('2027_02_04_nfl_history', '2027_02_04_nfl_history.sql', '7a616c41811ba94829749902915c1f94016f6381a5d6c4f4357c8223039436c5', 'init_db.sql'),
    ('2027_02_05_trade_talk_trigger', '2027_02_05_trade_talk_trigger.sql', '36034ee10af58873002c86e61c28a68621219a62d8365e268bda1cc5989e7ec3', 'init_db.sql'),
    ('2027_02_06_compensatory_draft_picks', '2027_02_06_compensatory_draft_picks.sql', '54883c7fd325d624717397d411cfa0bfe62b6a3473ab1fd18b2d709d2f336cce', 'init_db.sql'),
    ('2027_02_07_staff_contracts_and_full_control', '2027_02_07_staff_contracts_and_full_control.sql', '33ab9fb7edbf269944dba103da8685970c1dc76c69cfb82258a4d7fe1b1e46c1', 'init_db.sql'),
    ('2027_02_07_staff_development_award', '2027_02_07_staff_development_award.sql', '7ff467816bf01c9680cfd6999e10120317181ec288e6cbee1f196b53247542dd', 'init_db.sql'),
    ('2027_02_08_nflpa_report_cards', '2027_02_08_nflpa_report_cards.sql', 'aedd087a2367f031bb6aecb2ed883d4ee896611af2fbb4ddbf3a59a09c858166', 'init_db.sql'),
    ('2027_02_09_init_db_parity', '2027_02_09_init_db_parity.sql', 'f3d728858dd69f738514a575d055ed9d8831c22ed89927db530cce2df3be5aa0', 'init_db.sql'),
    ('2027_02_10_news_feed_post_shape', '2027_02_10_news_feed_post_shape.sql', '85f1d2db9cd238dc9e889010dcab8d0311e09d5b27ad271fddaceb56f58cc5ba', 'init_db.sql'),
    ('2027_02_11_scout_report_team_rows', '2027_02_11_scout_report_team_rows.sql', '83cd8b36d397e35d117a1ff67c36ac2011121d37e1bd65f33035bb4f6e5bf561', 'init_db.sql'),
    ('2027_02_12_trade_talk_flags', '2027_02_12_trade_talk_flags.sql', '5083e4799542b73b665b4ca68e117dd54af9de0c8ade61e7430785e843c233eb', 'init_db.sql'),
    ('2027_02_13_gm_voice_cards', '2027_02_13_gm_voice_cards.sql', '73ece2378bf0f178bc693dde1d3f19ba2396bcf80cb56353804658f9db7134d7', 'init_db.sql'),
    ('2027_02_14_trade_turn_stop', '2027_02_14_trade_turn_stop.sql', '8e0cd2c0e60e310a1bcc930b43388edb40aa6218f83913a0df343bc509304912', 'init_db.sql'),
    ('2027_02_15_jc2a_source_ids', '2027_02_15_jc2a_source_ids.sql', '9134260f512316714b66f917ea06bf76b1459cda967c04c18e94dda09a848d0a', 'init_db.sql'),
    ('2027_02_16_choice_source_no_llm', '2027_02_16_choice_source_no_llm.sql', '462bd5af1d778d100be44e3a419969957a5a9bcdd1493c50214cd0ea632774ca', 'init_db.sql'),
    ('2027_02_17_staff_contract_active_once', '2027_02_17_staff_contract_active_once.sql', '1b432423aea3d5bf4083c84ee1d83618b2ee976102cd508ece4401324ea94ed9', 'init_db.sql'),
    ('2027_02_18_contract_offer_week', '2027_02_18_contract_offer_week.sql', 'd7ad6551f3037ba512d0f22c992ca653634ed7ab8d703a9718c5ef119f798041', 'init_db.sql'),
    ('2027_02_19_owner_cash_buyouts', '2027_02_19_owner_cash_buyouts.sql', 'b7395abe8796d1772bfba9d9bc0322f4fc8b3638d1688ebf6dcf5e9b9c2674d8', 'init_db.sql'),
    ('2027_02_20_buyout_schedule', '2027_02_20_buyout_schedule.sql', 'ce290330016361b6a851d0919cd4596d58e24e51feb3703cd36ed590ca84be1d', 'init_db.sql'),
    ('2027_02_21_coach_madden_profiles', '2027_02_21_coach_madden_profiles.sql', '57c19dbbd71734bbf240a9831018a4447e6985c037f5b55240c5bd21c3f5b8f7', 'init_db.sql'),
    ('2027_02_22_relationship_applied_refs', '2027_02_22_relationship_applied_refs.sql', '6d24a303ad4affdca5645e264d82220864fd357916c874e97849d3bfc08d9e39', 'init_db.sql'),
    ('2027_02_23_player_stats_team_key', '2027_02_23_player_stats_team_key.sql', '7f5e8241f0e83d35385a272e5c8d7f34ddd3f0f7a68d99f0d51ad635a84fdeab', 'init_db.sql')
ON CONFLICT (version) DO NOTHING;
