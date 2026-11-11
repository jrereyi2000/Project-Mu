-- NFL weekly mirror (docs/in_progress/NFL_WEEKLY_MIRROR_PLAN.md §2.1, §2.4; PROJECT_SPEC §3.3a).
-- The league office can make the 2026 save follow the real NFL week by week: rosters, injuries, depth charts and
-- coaching changes. nfl_mirror_settings is the one switch (and Spike A's Injuries on/off field), nfl_mirror_weeks
-- holds one row per mirrored week (its sources, counts, league office run and status), and nfl_mirror_moves every
-- planned or written move with its reason. Real players the save lacks are created by the mirror and linked to their
-- real person with the new nfl_player_links method 'CREATED'. Written only by engines/nfl_mirror (store.py).
-- Mirrored at the end of db/init_db.sql.
--
-- Run ./db/backup_db.sh first; apply on madden_test first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d <db> -v ON_ERROR_STOP=1 < db/migrations/2027_02_26_nfl_mirror.sql

BEGIN;

CREATE TABLE IF NOT EXISTS nfl_mirror_settings (    -- one row: is the mirror on, for which season, through which week
    id                    SMALLINT PRIMARY KEY CHECK (id = 1),
    enabled               BOOLEAN NOT NULL DEFAULT false,
    season                INT NOT NULL DEFAULT 2026,
    last_week             INT,                       -- NULL = through the Super Bowl (Mu week numbers)
    madden_injuries_field JSONB,                     -- Spike A: {"table", "tableId", "row", "field", "off", "on"}
    injuries_state        VARCHAR(8) NOT NULL DEFAULT 'ON' CHECK (injuries_state IN ('ON', 'OFF')),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO nfl_mirror_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS nfl_mirror_weeks (       -- one row per mirrored Mu week
    season     INT NOT NULL,
    week       INT NOT NULL,                         -- Mu week (playoffs 19/20/21/23)
    real_week  INT,                                  -- nflverse week (playoffs 19-22)
    mode       VARCHAR(8) CHECK (mode IN ('CATCH_UP', 'LIVE')),
    sources    JSONB NOT NULL DEFAULT '{}',          -- {file name: sha256}
    counts     JSONB NOT NULL DEFAULT '{}',          -- moves per kind
    run_id     INT REFERENCES league_office_runs(id) ON DELETE SET NULL,
    status     VARCHAR(10) NOT NULL DEFAULT 'PLANNED' CHECK (status IN ('PLANNED', 'WRITTEN', 'FAILED')),
    report     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (season, week)
);

CREATE TABLE IF NOT EXISTS nfl_mirror_moves (       -- every move the mirror planned for a week, and what became of it
    id           SERIAL PRIMARY KEY,
    season       INT NOT NULL,
    week         INT NOT NULL,
    kind         VARCHAR(10) NOT NULL CHECK (kind IN ('SIGN', 'RELEASE', 'TRADE', 'PS', 'IR', 'ACTIVATE', 'INJURY',
                                                      'HEAL', 'CREATE', 'CHART', 'STAFF', 'SETTING')),
    gsis_id      TEXT,
    player_id    INT REFERENCES players(id) ON DELETE SET NULL,
    pgid         INT,
    from_team_id INT REFERENCES teams(id),
    to_team_id   INT REFERENCES teams(id),
    detail       JSONB NOT NULL DEFAULT '{}',
    status       VARCHAR(10) NOT NULL DEFAULT 'PLANNED' CHECK (status IN ('PLANNED', 'WRITTEN', 'SKIPPED', 'REFUSED')),
    reason       TEXT
);
CREATE INDEX IF NOT EXISTS nfl_mirror_moves_week ON nfl_mirror_moves (season, week);

-- A player the mirror created in the save is linked to his real person as 'CREATED'.
ALTER TABLE nfl_player_links DROP CONSTRAINT IF EXISTS nfl_player_links_method_check;
ALTER TABLE nfl_player_links ADD CONSTRAINT nfl_player_links_method_check
    CHECK (method IN ('EXACT', 'ESPN', 'ALIAS', 'FUZZY', 'OVERRIDE', 'CREATED'));

COMMIT;
