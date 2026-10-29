-- @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history schema)
-- @REPLACED-BY: NONE (feature dropped with the 2026-2036 history; drop the tables from db/init_db.sql at prune time; AUDIT_LEDGER.md §3b)
-- 2026-2034 player lines, staged (docs/done/HISTORY_BACKFILL_PLAN.md phase 3; PROJECT_SPEC §3.3k).
-- `python3 -m engines.history_backfill players` writes one run: the players who get lines (current players, real
-- 2026 veterans who retired before 2035, fill-ins) and their season lines in season_stat_lines column names. Nothing
-- reaches players / player_seasons / season_stats before JR approves a run (a later apply step).
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_12_29_history_player_lines.sql

BEGIN;

CREATE TABLE IF NOT EXISTS history_player_runs (
    id SERIAL PRIMARY KEY,
    seed INT NOT NULL,
    team_arc_candidate INT,                        -- the applied team arc the lines were drawn for
    report JSONB,                                  -- validate() problems and counts
    applied_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS history_players (
    run_id INT NOT NULL REFERENCES history_player_runs(id) ON DELETE CASCADE,
    key TEXT NOT NULL,                             -- p<player id> | e<espn id> (real veteran) | f<n> (fill-in)
    kind VARCHAR(8) NOT NULL,                      -- pool | vet | fillin
    player_id INT REFERENCES players(id) ON DELETE SET NULL,
    espn_id TEXT,
    full_name TEXT NOT NULL,
    player_name TEXT NOT NULL,
    position VARCHAR(8) NOT NULL,
    first_season INT,
    PRIMARY KEY (run_id, key)
);

CREATE TABLE IF NOT EXISTS history_player_lines (
    run_id INT NOT NULL REFERENCES history_player_runs(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    position VARCHAR(8) NOT NULL,
    age INT,
    years_of_experience INT,
    stats JSONB NOT NULL,                          -- season_stat_lines column names, plus games_played
    PRIMARY KEY (run_id, key, season)
);

COMMIT;
