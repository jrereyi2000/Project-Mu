-- Migration: Add game_highlights table for in-game postgame highlight play-by-play
-- Applied: 2026-10-16

CREATE TABLE IF NOT EXISTS game_highlights (
    game_id INTEGER PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    highlights JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_game_highlights_season_week ON game_highlights(season, week);
