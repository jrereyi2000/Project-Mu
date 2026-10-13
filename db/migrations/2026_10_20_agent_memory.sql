-- Evolving agent memory (newsroom/agent_memory.py, PROJECT_SPEC §7.7): takes log, prediction track record and
-- weekly season summaries. agent_memories.persistent_memory stays as each agent's fixed identity line.
-- Deleting a news_feed row removes the takes and predictions it produced (ON DELETE CASCADE).
--
-- Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_10_20_agent_memory.sql

BEGIN;

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

COMMIT;
