-- League Memory narrative search index (docs/PRE_PC_WORK.md §9, docs/PC_MIGRATION_PLAN.md V2.8).
-- Stores historical universe events (articles, writer takes, franchise records, league records)
-- with text chunks and vector embeddings (REAL[]) for fast cosine similarity in Python/numpy.

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
