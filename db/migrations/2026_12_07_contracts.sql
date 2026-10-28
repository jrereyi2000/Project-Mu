-- Contracts and cap (docs/done/TEAM_MANAGEMENT_PLAN.md §2.1, docs/done/team_management/01_CONTRACT_SCAN_PLAN.md Phase 1;
-- PROJECT_SPEC §3.3d): every team's cap strip and every player's contract from Manage Roster > TEAM SALARY,
-- scanned once a season (the Season scan, after rosters) and re-scanned per team when League Transactions
-- show a move (step 2). Money is whole dollars (bigint, pipeline/money.parse_money).
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_12_07_contracts.sql

BEGIN;

-- The TEAM SALARY header strip, one row per team and season. The latest scan wins.
CREATE TABLE IF NOT EXISTS team_cap_seasons (
    team_id INT NOT NULL REFERENCES teams(id),
    season INT NOT NULL,
    cap BIGINT,
    rollover BIGINT,
    adjusted_cap BIGINT,
    cap_space BIGINT,                                 -- can be negative
    salaries BIGINT,
    cap_penalty BIGINT,
    total_spend BIGINT,
    next_rookie_reserve BIGINT,
    scanned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    capture_id INT REFERENCES captures(id) ON DELETE SET NULL,
    PRIMARY KEY (team_id, season)
);

-- One row per contract as scanned. A team's re-scan marks its earlier ACTIVE rows SUPERSEDED and inserts fresh
-- ones; rows are never updated in place otherwise, so step 2 can see what changed. Step 2 marks RELEASED/TRADED
-- and inserts source = 'TRANSACTION' rows. season = the league season the scan was taken in.
CREATE TABLE IF NOT EXISTS player_contracts (
    id SERIAL PRIMARY KEY,
    player_id INT NOT NULL REFERENCES players(id),
    team_id INT NOT NULL REFERENCES teams(id),
    season INT NOT NULL,
    length INT,
    years_remaining INT,
    total_value BIGINT,
    bonus BIGINT,
    cut_savings BIGINT,
    cut_penalty BIGINT,
    no_trade_clause BOOLEAN NOT NULL DEFAULT false,
    incentive TEXT,                                   -- NONE on screen -> NULL
    status VARCHAR(12) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','SUPERSEDED','RELEASED','TRADED')),
    source VARCHAR(12) NOT NULL DEFAULT 'SCAN' CHECK (source IN ('SCAN','TRANSACTION')),
    scanned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    capture_id INT REFERENCES captures(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS player_contracts_one_active ON player_contracts (player_id, season)
    WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS player_contracts_team_season ON player_contracts (team_id, season, status);

-- One row per year column on screen (five: season .. season+4); a $0 year is stored as 0. The contract ends
-- in season + years_remaining - 1, not where the first $0 falls.
CREATE TABLE IF NOT EXISTS player_contract_years (
    contract_id INT NOT NULL REFERENCES player_contracts(id) ON DELETE CASCADE,
    year INT NOT NULL,                                -- calendar year, e.g. 2036
    cap_hit BIGINT NOT NULL,
    PRIMARY KEY (contract_id, year)
);

-- Active contracts with each player's yearly cap hits (relative years y0 = season .. y4 = season+4) next to the
-- team's cap row. Steps 3 (GM agents) and 5 (newsroom) read this view.
CREATE OR REPLACE VIEW vw_team_cap_sheet AS
SELECT c.team_id,
       t.abbreviation,
       c.season,
       c.id AS contract_id,
       c.player_id,
       p.player_name,
       ps.position,
       c.length,
       c.years_remaining,
       c.season + c.years_remaining - 1 AS contract_end_season,
       c.total_value,
       c.bonus,
       c.cut_savings,
       c.cut_penalty,
       c.no_trade_clause,
       c.incentive,
       (SELECT y.cap_hit FROM player_contract_years y WHERE y.contract_id = c.id AND y.year = c.season)     AS cap_hit_y0,
       (SELECT y.cap_hit FROM player_contract_years y WHERE y.contract_id = c.id AND y.year = c.season + 1) AS cap_hit_y1,
       (SELECT y.cap_hit FROM player_contract_years y WHERE y.contract_id = c.id AND y.year = c.season + 2) AS cap_hit_y2,
       (SELECT y.cap_hit FROM player_contract_years y WHERE y.contract_id = c.id AND y.year = c.season + 3) AS cap_hit_y3,
       (SELECT y.cap_hit FROM player_contract_years y WHERE y.contract_id = c.id AND y.year = c.season + 4) AS cap_hit_y4,
       tc.cap,
       tc.adjusted_cap,
       tc.cap_space,
       tc.total_spend,
       tc.next_rookie_reserve
FROM player_contracts c
JOIN teams t ON t.id = c.team_id
JOIN players p ON p.id = c.player_id
LEFT JOIN player_seasons ps ON ps.player_id = c.player_id AND ps.season = c.season
LEFT JOIN team_cap_seasons tc ON tc.team_id = c.team_id AND tc.season = c.season
WHERE c.status = 'ACTIVE';

COMMIT;
