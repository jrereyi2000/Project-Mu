-- Weekly Power Rankings parsed from the POWER_RANKINGS article (newsroom/power_rankings.py, PROJECT_SPEC §7.6).
-- One row per team per (season, week); a week is stored only when the article ranks all 32 teams exactly once.
--
-- Idempotent. Apply with:  psql -U postgres -d madden -f db/migrations/2026_10_18_power_rankings.sql

BEGIN;
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
COMMIT;
