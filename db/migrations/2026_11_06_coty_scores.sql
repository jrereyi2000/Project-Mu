-- Coach of the Year race (PROJECT_SPEC §2.11): one row per head coach per season, rewritten by
-- python3 -m engines.coty_engine [--season N]. Inputs the coach didn't have (no previous regular season,
-- no team_ovr) are NULL. Run once: psql -f db/migrations/2026_11_06_coty_scores.sql
BEGIN;

CREATE TABLE IF NOT EXISTS coty_scores (
    id SERIAL PRIMARY KEY,
    coach_id INT NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    season INT NOT NULL,
    coty_score DOUBLE PRECISION NOT NULL,
    coty_rank INT NOT NULL,
    win_pct DOUBLE PRECISION NOT NULL,
    wins_above_expected DOUBLE PRECISION NOT NULL,
    roster_outperformance DOUBLE PRECISION,
    win_pct_change DOUBLE PRECISION,
    first_year_with_team BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (coach_id, season)
);

COMMIT;
