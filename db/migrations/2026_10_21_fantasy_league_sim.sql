-- Simulated fantasy leagues (engines/fantasy_league_sim.py, docs/in_progress/FANTASY_EDITORIAL_MATRIX.md §5, PROJECT_SPEC §3.4).
-- 100 simulated 12-team PPR leagues per season: drafts → ADP, weekly rostered % / start %, standings, and the
-- draft strategy mix (which adapts each season from the previous season's title results).
--
-- Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_10_21_fantasy_league_sim.sql

BEGIN;

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

COMMIT;
