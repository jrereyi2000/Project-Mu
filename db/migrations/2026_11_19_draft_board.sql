-- Draft board in the database (JR, Oct 15, 2026; PROJECT_SPEC §2.10): the scouted board from the Draft Board scan
-- (draft_prospects) and each week's Round 1 mock draft (mock_draft_picks). Until now the board lived only in
-- git-ignored CSVs under data/draft/.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_11_19_draft_board.sql

BEGIN;

-- Scouted draft board (pipeline/draft_board.py, loaded from the Draft Board scan by pipeline/load_capture.py).
-- One row per prospect per draft class; a re-scan replaces the class. Only the pick and name are required:
-- the scan can leave a cell blank (JR, Oct 15), and the rookie's real values arrive with next season's
-- roster scan, which also sets player_id.
CREATE TABLE IF NOT EXISTS draft_prospects (
    id SERIAL PRIMARY KEY,
    draft_season INT NOT NULL,              -- the class: league season + 1
    projected_pick INT NOT NULL,            -- overall: 1-224 drafted, then the undrafted in board order
    projected_round VARCHAR(4),             -- '1'..'7' or 'UDFA'
    round_pick SMALLINT,
    full_name VARCHAR(255) NOT NULL,
    position VARCHAR(10),
    college VARCHAR(100),
    height_in SMALLINT,
    weight SMALLINT,
    age SMALLINT,
    ovr SMALLINT,                           -- scouted OVR on the board
    archetype VARCHAR(64),
    notes TEXT,
    source_capture VARCHAR(255),            -- captures/<run> the board came from
    player_id INT REFERENCES players(id) ON DELETE SET NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (draft_season, projected_pick)
);
CREATE INDEX IF NOT EXISTS idx_draft_prospects_player ON draft_prospects(player_id);

-- Round 1 mock draft per (season, week), saved by engines/mock_draft_engine.py so every article that week
-- cites the same picks. Rows go with the board they were drawn from (a re-scan replaces both).
CREATE TABLE IF NOT EXISTS mock_draft_picks (
    season INT NOT NULL,                    -- league season whose draft order and needs were used
    week INT NOT NULL,
    pick SMALLINT NOT NULL,
    team_id INT REFERENCES teams(id),
    prospect_id INT NOT NULL REFERENCES draft_prospects(id) ON DELETE CASCADE,
    locked BOOLEAN NOT NULL DEFAULT false,  -- the team's season is over, so its pick is final
    selection_status VARCHAR(40),
    fit_score NUMERIC(7, 1),
    rationale TEXT,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (season, week, pick)
);

COMMIT;
