-- Real NFL history 1999-2025 (docs/done/REAL_NFL_HISTORY_PLAN.md §2.2; PROJECT_SPEC §3.3x).
-- Real players, season lines (regular season and playoffs), games, team and head-coach seasons, awards and the
-- draft from nflverse (awards from Wikipedia), in their own nfl_* tables so they never mix with the league's
-- simulated seasons. nfl_player_links ties this database's Madden players to the real people. Written only by
-- `python3 -m engines.nfl_history load|link|coach-baseline|init --apply`; every row is real (view source = 'NFL').
-- Mirrored at the end of db/init_db.sql.
--
-- Run ./db/backup_db.sh first; apply on madden_test first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d <db> -v ON_ERROR_STOP=1 < db/migrations/2027_02_04_nfl_history.sql

BEGIN;

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

COMMIT;
