-- Player season ledger and retirements (docs/in_progress/SEASON_CONTINUITY_PLAN.md phase 2a; PROJECT_SPEC §3.3m).
-- engines/season_ledger.py writes player_season_transitions at each Season scan (and by hand for a backfill): one row
-- per player per captured season boundary, with his fate (RETAINED, MOVED, RETIRED, FREE_AGENT, DRAFTED, NEW) and his
-- rating, development trait, age and experience on both sides. Retirements come from League Transactions rows
-- (JR 2026-10-26: Madden lists them there); phase 2b parses them into player_retirements and players.retired_season.
-- Until then a departed player with no retirement on record is a free agent (plan D1).
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_01_03_player_season_ledger.sql

BEGIN;

-- League Transactions can carry retirements (parsed in phase 2b).
ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_type_check;
ALTER TABLE transactions ADD CONSTRAINT transactions_type_check CHECK (type IN
    ('SIGNED', 'RELEASED', 'TRADED', 'RE_SIGNED', 'EXTENDED', 'RESTRUCTURED', 'RETIRED', 'OTHER'));

-- The season a player retired after (a captured retirement; the 2026-2034 history's players are retired by their
-- players.origin instead, plan D11). A later signing or roster scan that finds him clears it (plan D8).
ALTER TABLE players ADD COLUMN IF NOT EXISTS retired_season INT;

-- One row per retirement read off League Transactions.
CREATE TABLE IF NOT EXISTS player_retirements (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,                               -- the season he retired after
    player_id INT REFERENCES players(id) ON DELETE CASCADE,   -- NULL: the name couldn't be settled (see flags)
    player_name TEXT NOT NULL,
    position VARCHAR(6),
    team_id INT REFERENCES teams(id),                  -- his last team, when the row names one
    transaction_id INT REFERENCES transactions(id) ON DELETE SET NULL,
    capture_id INT REFERENCES captures(id) ON DELETE SET NULL,
    flags TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS player_retirements_one ON player_retirements (season, player_id)
    WHERE player_id IS NOT NULL;

-- Each player's fate between two captured seasons (plan D10: captured seasons only, 2035 -> 2036 on).
CREATE TABLE IF NOT EXISTS player_season_transitions (
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    to_season INT NOT NULL,
    from_season INT NOT NULL,
    from_team_id INT REFERENCES teams(id),             -- his team at the end of from_season (NULL: not on a roster)
    to_team_id INT REFERENCES teams(id),               -- his team at the start of to_season (NULL: none)
    status VARCHAR(12) NOT NULL CHECK (status IN ('RETAINED', 'MOVED', 'RETIRED', 'FREE_AGENT', 'DRAFTED', 'NEW')),
    source VARCHAR(12),                                -- how a move or arrival happened: TRADE, TRANSACTION, ROSTER_SCAN, DRAFT
    ovr_before INT, ovr_after INT,
    dev_before VARCHAR(32), dev_after VARCHAR(32),
    age_before INT, age_after INT,
    exp_before INT, exp_after INT,
    flags TEXT,                                        -- e.g. retirement_scan_missing, released_in_season, returned
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (player_id, to_season)
);
CREATE INDEX IF NOT EXISTS player_season_transitions_status ON player_season_transitions (to_season, status);

COMMIT;
