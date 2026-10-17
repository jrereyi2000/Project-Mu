-- Migration: 2026_12_02_napper_fantasy.sql
-- Description: Core tables for Napper fantasy platform

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
    season INT DEFAULT 2035,
    teams_count INT NOT NULL,
    scoring_type VARCHAR(16) DEFAULT 'PPR',
    roster_format JSONB NOT NULL DEFAULT '{"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 1, "K": 1, "DEF": 1, "BN": 6}'::jsonb,
    status VARCHAR(16) DEFAULT 'IN_SEASON',
    avatar_url TEXT,
    current_week INT DEFAULT 1,
    is_seed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

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
    seed SMALLINT
);

CREATE TABLE IF NOT EXISTS napper_rosters (
    id SERIAL PRIMARY KEY,
    league_id INT REFERENCES napper_leagues(id) ON DELETE CASCADE,
    team_id INT REFERENCES napper_teams(id) ON DELETE CASCADE,
    week INT NOT NULL,
    player_id INT REFERENCES players(id) ON DELETE CASCADE,
    slot VARCHAR(16) NOT NULL,
    UNIQUE(team_id, week, player_id)
);

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
    is_final BOOLEAN DEFAULT FALSE
);

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

CREATE INDEX IF NOT EXISTS idx_napper_teams_league ON napper_teams(league_id);
CREATE INDEX IF NOT EXISTS idx_napper_rosters_team_week ON napper_rosters(team_id, week);
CREATE INDEX IF NOT EXISTS idx_napper_rosters_league_week ON napper_rosters(league_id, week);
CREATE INDEX IF NOT EXISTS idx_napper_matchups_league_week ON napper_matchups(league_id, week);
CREATE INDEX IF NOT EXISTS idx_napper_trades_league ON napper_trades(league_id);
CREATE INDEX IF NOT EXISTS idx_napper_chat_league ON napper_chat(league_id, created_at);

CREATE TABLE IF NOT EXISTS napper_drafts (
    id SERIAL PRIMARY KEY,
    league_id INT REFERENCES napper_leagues(id) ON DELETE CASCADE,
    season INT DEFAULT 2035,
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

CREATE INDEX IF NOT EXISTS idx_napper_drafts_league ON napper_drafts(league_id);
CREATE INDEX IF NOT EXISTS idx_napper_draft_picks_draft ON napper_draft_picks(draft_id);
CREATE INDEX IF NOT EXISTS idx_napper_draft_picks_draft_overall ON napper_draft_picks(draft_id, overall_pick);
CREATE INDEX IF NOT EXISTS idx_napper_draft_picks_player ON napper_draft_picks(player_id);
