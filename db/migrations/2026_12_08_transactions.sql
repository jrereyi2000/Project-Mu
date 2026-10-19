-- League transactions (docs/team_management/02_LEAGUE_TRANSACTIONS_PLAN.md, master plan §2.2).
-- The weekly League > LEAGUE TRANSACTIONS scan (madden_capture.py --only transactions -> transactions.csv ->
-- pipeline/load_transactions.py). Madden's CPU signs and releases (D1); Mu's trades are made by hand (D2/D3).

-- One row per list row. The list gives no date finer than its heading ("2036 SEASON - WEEK 4") and identical
-- rows are legitimate, so the key is (season, heading, normalized row, seq): seq counts equal rows within a
-- heading group from the oldest up, so it stays stable when newer rows appear at the top of the list.
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT,                                          -- games.week; NULL for preseason / offseason headings
    txn_date TEXT NOT NULL,                            -- the heading after OCR clean-up, as Madden shows it
    type VARCHAR(12) NOT NULL CHECK (type IN ('SIGNED', 'RELEASED', 'TRADED', 'RE_SIGNED', 'EXTENDED', 'OTHER')),
    player_id INT REFERENCES players(id) ON DELETE SET NULL,
    player_name TEXT,                                  -- as the row spells it ("DEVONTA TAYLOR")
    position VARCHAR(6),
    from_team_id INT REFERENCES teams(id),             -- RELEASED / TRADED: the team losing the player
    to_team_id INT REFERENCES teams(id),               -- SIGNED / TRADED: the team gaining him; RE_SIGNED / EXTENDED: his team
    contract_years SMALLINT,
    contract_value BIGINT,                             -- whole dollars ($3.93M -> 3930000)
    raw_text TEXT NOT NULL,
    row_key TEXT NOT NULL,                             -- normalized key (parsed fields, or raw: + folded text)
    seq SMALLINT NOT NULL DEFAULT 1,
    parse_ok BOOLEAN NOT NULL,
    flags TEXT,                                        -- extraction / load flags, comma-separated
    applied BOOLEAN NOT NULL DEFAULT TRUE,             -- false: stored as history only (before the effects cutoff)
    trade_group TEXT,                                  -- links the legs of one trade
    gm_proposal_id INT,                                -- step 3 (GM agents) sets it and adds the FK
    capture_id INT REFERENCES captures(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (season, txn_date, row_key, seq)
);
CREATE INDEX IF NOT EXISTS idx_transactions_season_week ON transactions (season, week);
CREATE INDEX IF NOT EXISTS idx_transactions_player ON transactions (player_id);
CREATE INDEX IF NOT EXISTS idx_transactions_trade_group ON transactions (trade_group);

-- Teams whose Team Salary screen must be captured again (D5): every team in a newly applied transaction.
-- The weekly run re-scans at most 8 a week (traded teams always, the rest oldest first) and marks them satisfied.
CREATE TABLE IF NOT EXISTS contract_rescan_requests (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    transaction_id INT REFERENCES transactions(id) ON DELETE CASCADE,
    requested_at TIMESTAMPTZ DEFAULT now(),
    satisfied_capture_id INT REFERENCES captures(id),
    satisfied_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_contract_rescan_pending ON contract_rescan_requests (season, team_id)
    WHERE satisfied_at IS NULL;

-- A released player keeps his last team (the engines assume every player_seasons row has one, JR Oct 14);
-- released_week marks him a free agent since that week. NULL = on the team's roster.
ALTER TABLE player_seasons ADD COLUMN IF NOT EXISTS released_week INT;
