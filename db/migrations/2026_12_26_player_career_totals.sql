-- Career totals from Madden's Career Stats lists (docs/done/HISTORY_BACKFILL_PLAN.md phase 1; PROJECT_SPEC §3.3i).
-- pipeline/load_career_totals.py writes one row per list row and category: the player's career line as Madden shows
-- it (real NFL stats through 2025 for veterans, every season since, the current season so far), in the
-- season_stat_lines column names. A row whose name can't be settled to one player keeps player_id NULL and a flag.
-- A reload of the same captured season replaces that season's rows.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_12_26_player_career_totals.sql

BEGIN;

CREATE TABLE IF NOT EXISTS player_career_totals (
    id SERIAL PRIMARY KEY,
    captured_season INT NOT NULL,
    captured_week INT,
    capture_folder TEXT NOT NULL,
    category VARCHAR(16) NOT NULL,                 -- passing, rushing, receiving, defense, kicking, punting, ...
    player_id INT REFERENCES players(id) ON DELETE SET NULL,
    list_name TEXT NOT NULL,                       -- the list's NAME cell, e.g. G.Crawford
    position VARCHAR(8),
    stats JSONB NOT NULL,                          -- {season_stat_lines column: value}, plus games_played
    flags TEXT[] NOT NULL DEFAULT '{}',            -- unmatched, ambiguous, misread_copy
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS player_career_totals_player
    ON player_career_totals (captured_season, category, player_id) WHERE player_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS player_career_totals_by_player ON player_career_totals (player_id);

COMMIT;
