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
-- docs/TEAM_MANAGEMENT_PLAN.md §2.1). Money is whole dollars (pipeline/money.parse_money).
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

-- 9. League transactions (2026_12_08_transactions.sql, docs/team_management/02_LEAGUE_TRANSACTIONS_PLAN.md).
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

-- 10. GM agents (2026_12_10_gm_agents.sql, docs/team_management/03_GM_AGENTS_PLAN.md; PROJECT_SPEC §3.3f).
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
    start_season INT NOT NULL CHECK (start_season >= 2030),   -- invented tenure start
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
    source VARCHAR(5) NOT NULL DEFAULT 'SEED' CHECK (source IN ('SEED', 'TRADE', 'JR')),
    acquired_proposal_id INT,                         -- FK added below, after gm_trade_proposals
    used BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (draft_season, round, original_team_id)
);
CREATE INDEX IF NOT EXISTS draft_picks_owner ON draft_picks (owner_team_id, draft_season);

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
        CHECK (status IN ('PROPOSED', 'APPROVED', 'EXECUTED', 'REJECTED', 'EXPIRED')),
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

-- Trade negotiations with the GM agents (engines/gm_negotiation.py; 2026_12_21_gm_negotiations.sql): JR offers
-- from a user-controlled team, the GM accepts, counters, rejects or walks away; an agreed deal becomes an
-- APPROVED USER proposal (flag negotiated:<id>).
CREATE TABLE IF NOT EXISTS gm_trade_negotiations (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT NOT NULL,                                -- engine week when opened (offseason 24)
    trade_window VARCHAR(10) NOT NULL CHECK (trade_window IN ('IN_SEASON', 'OFFSEASON')),
    offseason_stage SMALLINT,                         -- the offseason stage it was opened in (expires when it moves)
    user_team_id INT NOT NULL REFERENCES teams(id),   -- JR's team
    gm_team_id INT NOT NULL REFERENCES teams(id),     -- the GM agent's team
    status VARCHAR(11) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'AGREED', 'WALKED_AWAY', 'CLOSED', 'EXPIRED')),
    status_reason TEXT,
    patience SMALLINT NOT NULL,                       -- rounds the GM started with
    patience_left SMALLINT NOT NULL,
    proposal_id INT REFERENCES gm_trade_proposals(id) ON DELETE SET NULL,   -- the agreed deal
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS gm_trade_negotiations_season ON gm_trade_negotiations (season, status);

CREATE TABLE IF NOT EXISTS gm_trade_negotiation_turns (
    id SERIAL PRIMARY KEY,
    negotiation_id INT NOT NULL REFERENCES gm_trade_negotiations(id) ON DELETE CASCADE,
    turn_no SMALLINT NOT NULL,
    actor VARCHAR(2) NOT NULL CHECK (actor IN ('JR', 'GM')),
    action VARCHAR(9) NOT NULL CHECK (action IN ('OFFER', 'COUNTER', 'ACCEPT', 'REJECT', 'WALK_AWAY', 'CLOSE')),
    assets JSONB,                                     -- Front Office JSON assets of the package on the table
    surplus_user BIGINT,                              -- JR's team's surplus by the model
    surplus_gm BIGINT,                                -- the GM's surplus (never shown to JR)
    mood VARCHAR(8),                                  -- the GM's read of an offer: CLOSE | APART | INSULTED
    legality JSONB,
    message TEXT,                                     -- JR's note, or the GM's reply
    message_source VARCHAR(8) CHECK (message_source IN ('JR', 'LLM', 'TEMPLATE')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (negotiation_id, turn_no)
);

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

-- 11. Scouting and draft (2026_12_12_scouting.sql, docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md;
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

-- Coordinators and staff tenures (2026_11_25_coordinators_and_slants.sql, docs/COACH_SLANTS_AND_COORDINATORS_PLAN.md)
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
-- docs/SEASON_CONTINUITY_PLAN.md phase 1).
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
-- docs/SEASON_CONTINUITY_PLAN.md phase 2a).
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

-- Career totals from Madden's Career Stats lists (2026_12_26_player_career_totals.sql, pipeline/load_career_totals.py,
-- docs/HISTORY_BACKFILL_PLAN.md phase 1): one row per list row and category, in season_stat_lines column names.
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
-- docs/HISTORY_BACKFILL_PLAN.md phase 2): `apply` writes source = 'SYNTH'; the season archive's rows stay 'CAPTURE'.
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
    role VARCHAR(2) NOT NULL CHECK (role IN ('HC', 'OC', 'DC')),
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

CREATE TABLE IF NOT EXISTS awards (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    award_type VARCHAR(50) NOT NULL,
    player_id INT REFERENCES players(id),
    team_id INT REFERENCES teams(id),
    coach_name VARCHAR(255),
    coach_id INT REFERENCES coaches(id),
    player_name VARCHAR(255),
    position VARCHAR(10)
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
    actual_pick SMALLINT CHECK (actual_pick BETWEEN 1 AND 32),
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

CREATE OR REPLACE VIEW league_records AS
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

