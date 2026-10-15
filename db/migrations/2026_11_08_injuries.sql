-- Project Mu: Weekly Injury Ledger
--
-- Stores weekly injury reports captured from Madden franchise mode (Manage Roster > Injury Report).
-- Captures are scoped by team and week. Healthy/recovered players show with 0 weeks remaining or are omitted.

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
