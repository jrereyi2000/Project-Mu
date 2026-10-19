-- Napper waivers (engines/napper_waiver_engine.py). The weekly step runs one waiver run per league and week,
-- before that week's games: queued claims (the user's) and AI pickups are resolved in reverse-standings order.

-- Claims. The user's claims on players on waivers wait as PENDING for the next run; AI pickups are logged as
-- WON when the run makes them. rank = the team's preference order among its pending claims (1 = first).
CREATE TABLE IF NOT EXISTS napper_waiver_claims (
    id SERIAL PRIMARY KEY,
    league_id INT NOT NULL REFERENCES napper_leagues(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES napper_teams(id) ON DELETE CASCADE,
    add_player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    drop_player_id INT REFERENCES players(id) ON DELETE SET NULL,
    rank SMALLINT NOT NULL DEFAULT 1,
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING',   -- PENDING, WON, LOST, INVALID, CANCELLED
    reason VARCHAR(16),                              -- AI pickups: NEED, STREAM, UPGRADE; user claims: CLAIM
    note TEXT,                                       -- why a claim was lost or invalid
    week INT,                                        -- the waiver run that decided it
    created_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_napper_waiver_claims_pending ON napper_waiver_claims (league_id, status);

-- Players on waivers: dropped since the last run. They can only be claimed, and clear to free agency at the
-- run for clears_week (or are awarded to a claim in it).
CREATE TABLE IF NOT EXISTS napper_waiver_wire (
    league_id INT NOT NULL REFERENCES napper_leagues(id) ON DELETE CASCADE,
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    dropped_by_team_id INT REFERENCES napper_teams(id) ON DELETE SET NULL,
    clears_week INT NOT NULL,
    dropped_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (league_id, player_id)
);

-- One row per completed run, so a re-run of the weekly step doesn't process waivers twice.
-- priority = the team ids in claim order for that run.
CREATE TABLE IF NOT EXISTS napper_waiver_runs (
    league_id INT NOT NULL REFERENCES napper_leagues(id) ON DELETE CASCADE,
    week INT NOT NULL,
    priority INT[] NOT NULL,
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (league_id, week)
);
