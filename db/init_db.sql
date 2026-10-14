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
    third_down_attempts INT DEFAULT 0,
    third_down_conversions INT DEFAULT 0,
    fourth_down_attempts INT DEFAULT 0,
    fourth_down_conversions INT DEFAULT 0,
    turnovers INT DEFAULT 0,
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
    UNIQUE (season, player_name, team_id, position)
);

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

CREATE TABLE IF NOT EXISTS awards (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    award_type VARCHAR(50) NOT NULL,
    player_id INT REFERENCES players(id),
    team_id INT REFERENCES teams(id),
    coach_name VARCHAR(255),
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
    IF TG_OP = 'INSERT' OR
       (TG_OP = 'UPDATE' AND (NEW.status != OLD.status OR NEW.home_score != OLD.home_score OR NEW.away_score != OLD.away_score)) THEN

        -- Recalculate Home Team
        INSERT INTO team_seasons (team_id, season, wins, losses)
        VALUES (
            NEW.home_team_id, NEW.season,
            (SELECT COUNT(*) FROM games g WHERE g.season = NEW.season AND g.status = 'COMPLETED' AND ((g.home_team_id = NEW.home_team_id AND g.home_score > g.away_score) OR (g.away_team_id = NEW.home_team_id AND g.away_score > g.home_score))),
            (SELECT COUNT(*) FROM games g WHERE g.season = NEW.season AND g.status = 'COMPLETED' AND ((g.home_team_id = NEW.home_team_id AND g.home_score < g.away_score) OR (g.away_team_id = NEW.home_team_id AND g.away_score < g.home_score)))
        )
        ON CONFLICT (team_id, season) DO UPDATE SET
            wins = EXCLUDED.wins, losses = EXCLUDED.losses;

        -- Recalculate Away Team
        INSERT INTO team_seasons (team_id, season, wins, losses)
        VALUES (
            NEW.away_team_id, NEW.season,
            (SELECT COUNT(*) FROM games g WHERE g.season = NEW.season AND g.status = 'COMPLETED' AND ((g.home_team_id = NEW.away_team_id AND g.home_score > g.away_score) OR (g.away_team_id = NEW.away_team_id AND g.away_score > g.home_score))),
            (SELECT COUNT(*) FROM games g WHERE g.season = NEW.season AND g.status = 'COMPLETED' AND ((g.home_team_id = NEW.away_team_id AND g.home_score < g.away_score) OR (g.away_team_id = NEW.away_team_id AND g.away_score < g.home_score)))
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
