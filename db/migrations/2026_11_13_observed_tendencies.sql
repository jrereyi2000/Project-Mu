-- Observed offensive tendencies per team-season, measured from this league's own results by
-- engines/tendency_engine.py (PROJECT_SPEC §2.12). The real-world 2026 tiers on `coaches` didn't show up in the
-- 2035 results (pass lean: none; fourth down: weak), so writers get these instead.
-- Run once: psql -f db/migrations/2026_11_13_observed_tendencies.sql
ALTER TABLE team_seasons
    ADD COLUMN IF NOT EXISTS pass_rate_oe DOUBLE PRECISION,    -- pass share minus the share point differential predicts
    ADD COLUMN IF NOT EXISTS fourth_down_oe DOUBLE PRECISION,  -- 4th-down attempts/game minus what point differential predicts
    ADD COLUMN IF NOT EXISTS observed_pass_lean VARCHAR(6) CHECK (observed_pass_lean IN ('Low', 'Medium', 'High')),
    ADD COLUMN IF NOT EXISTS observed_fourth_down VARCHAR(6) CHECK (observed_fourth_down IN ('Low', 'Medium', 'High')),
    ADD COLUMN IF NOT EXISTS tendency_games INT;               -- regular-season games the tiers are based on
