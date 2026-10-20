-- Scouting and draft (docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md §2.10; PROJECT_SPEC §3.3g).
-- Each prospect's hidden true value, the weekly noisy consensus board and the invented college events that explain
-- its moves, 32 private team boards, and the Round 1 picks JR records live in the Draft Room. Rows key on a stable
-- prospect_key (draft season + name + position group) because a Draft Board re-scan replaces draft_prospects ids.
-- Hidden columns (true_value, true_rank, value_inputs, err, impact, team boards) are never shown to writers or on
-- #debug; vw_scout_board_public is the writer-safe view. Needs 2026_12_10_gm_agents.sql (gm_trade_proposals).
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_12_12_scouting.sql

BEGIN;

-- One row per prospect per class. true_value (V) and true_rank are hidden; prospect_id is re-linked after every
-- board load (engines/scouting_engine.relink). status: ACTIVE -> DRAFTED | UDFA, or WITHDRAWN when a re-scan drops him.
CREATE TABLE IF NOT EXISTS scout_prospects (
    id SERIAL PRIMARY KEY,
    draft_season INT NOT NULL,
    prospect_key TEXT NOT NULL,
    prospect_id INT REFERENCES draft_prospects(id) ON DELETE SET NULL,
    true_value NUMERIC(6,2) NOT NULL,
    true_rank INT NOT NULL,
    value_inputs JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {ovr, position, group, age, pv, age_adj}
    class_year TEXT,                                   -- junior, redshirt sophomore, senior ... (from age)
    conference TEXT,                                   -- data/scouting/colleges.json
    first_step SMALLINT NOT NULL,
    status VARCHAR(9) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'WITHDRAWN', 'DRAFTED', 'UDFA')),
    seed TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (draft_season, prospect_key)
);
CREATE INDEX IF NOT EXISTS scout_prospects_prospect ON scout_prospects (prospect_id);

-- One row per stored scouting step (in-season step = league week; offseason step = 23 + stage, 27 = draft).
CREATE TABLE IF NOT EXISTS scout_runs (
    draft_season INT NOT NULL,
    step SMALLINT NOT NULL CHECK (step BETWEEN 1 AND 27),
    season INT NOT NULL,
    week INT NOT NULL,
    stage SMALLINT,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,         -- the constants and code version the step ran with
    n_events INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (draft_season, step)
);

-- The consensus board each step: grade (0-100), rank, round label, change from the previous step. err is hidden.
CREATE TABLE IF NOT EXISTS scout_consensus (
    draft_season INT NOT NULL,
    step SMALLINT NOT NULL,
    prospect_key TEXT NOT NULL,
    season INT NOT NULL,
    week INT NOT NULL,
    stage SMALLINT,
    grade NUMERIC(5,1) NOT NULL,
    rank INT NOT NULL,
    round_label TEXT NOT NULL,
    delta_grade NUMERIC(5,1),
    delta_rank INT,
    err NUMERIC(7,3) NOT NULL,
    event_ids INT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (draft_season, step, prospect_key),
    FOREIGN KEY (draft_season, step) REFERENCES scout_runs (draft_season, step) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS scout_consensus_rank ON scout_consensus (draft_season, step, rank);

-- Invented college events (games, storylines, all-star games, combine, pro days, visits, draft week). Every one is
-- stored so all writers see the same story. impact (the consensus move it explains) is hidden.
CREATE TABLE IF NOT EXISTS scout_events (
    id SERIAL PRIMARY KEY,
    draft_season INT NOT NULL,
    step SMALLINT NOT NULL,
    prospect_key TEXT NOT NULL,
    kind VARCHAR(9) NOT NULL CHECK (kind IN ('GAME', 'STORYLINE', 'DECLARE', 'AWARD', 'ALL_STAR', 'COMBINE',
                                             'PRO_DAY', 'VISIT', 'MEDICAL', 'BUZZ')),
    team_id INT REFERENCES teams(id),                  -- a VISIT's team
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,        -- stat line, opponent, measurables (keys starting _ are hidden)
    impact NUMERIC(7,3),
    headline TEXT,
    blurb TEXT,
    text_source VARCHAR(8) NOT NULL DEFAULT 'TEMPLATE' CHECK (text_source IN ('LLM', 'TEMPLATE')),
    seq SMALLINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (draft_season, step, prospect_key, kind, seq),
    FOREIGN KEY (draft_season, step) REFERENCES scout_runs (draft_season, step) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS scout_events_prospect ON scout_events (draft_season, prospect_key, step);

-- Each team's private board, top 100 by its grade, every step (hidden: shown only in the Draft Room).
CREATE TABLE IF NOT EXISTS scout_team_boards (
    draft_season INT NOT NULL,
    step SMALLINT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    prospect_key TEXT NOT NULL,
    team_grade NUMERIC(5,1) NOT NULL,
    team_rank INT NOT NULL,
    fit NUMERIC(6,2),
    bias NUMERIC(6,2),
    err NUMERIC(7,3),
    PRIMARY KEY (draft_season, step, team_id, prospect_key),
    FOREIGN KEY (draft_season, step) REFERENCES scout_runs (draft_season, step) ON DELETE CASCADE
);

-- The Round 1 picks JR records in the Draft Room as he makes them in Madden; validated against the Draft Recap.
CREATE TABLE IF NOT EXISTS scout_draft_picks (
    draft_season INT NOT NULL,
    overall_pick SMALLINT NOT NULL CHECK (overall_pick BETWEEN 1 AND 32),
    round SMALLINT NOT NULL DEFAULT 1,
    team_id INT NOT NULL REFERENCES teams(id),         -- the owner on the clock
    original_team_id INT NOT NULL REFERENCES teams(id),
    prospect_key TEXT NOT NULL,
    prospect_id INT REFERENCES draft_prospects(id) ON DELETE SET NULL,
    recommended_key TEXT,
    choice_source VARCHAR(10) NOT NULL CHECK (choice_source IN ('GM', 'OFF_SCRIPT', 'JR_TEAM')),
    alternatives JSONB NOT NULL DEFAULT '[]'::jsonb,
    rationale TEXT,
    rationale_source VARCHAR(8) CHECK (rationale_source IN ('LLM', 'TEMPLATE')),
    trade_proposal_id INT REFERENCES gm_trade_proposals(id) ON DELETE SET NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    madden_match VARCHAR(8) CHECK (madden_match IN ('MATCH', 'MISMATCH')),
    madden_prospect_id INT REFERENCES draft_prospects(id) ON DELETE SET NULL,
    PRIMARY KEY (draft_season, overall_pick),
    UNIQUE (draft_season, prospect_key)
);

-- The writer-safe board: consensus rows with the prospect's public fields and the step's top event headline.
-- No true_value, err, impact, team boards or Madden OVR.
CREATE OR REPLACE VIEW vw_scout_board_public AS
SELECT c.draft_season, c.step, c.season, c.week, c.stage, c.prospect_key, c.grade, c.rank, c.round_label,
       c.delta_grade, c.delta_rank,
       d.full_name, d.position, d.college, d.height_in, d.weight, d.age,
       sp.class_year, sp.conference, sp.status,
       (SELECT e.headline FROM scout_events e
        WHERE e.draft_season = c.draft_season AND e.step = c.step AND e.prospect_key = c.prospect_key
          AND e.kind <> 'VISIT'
        ORDER BY abs(e.impact) DESC NULLS LAST, e.id LIMIT 1) AS top_headline
FROM scout_consensus c
JOIN scout_prospects sp ON sp.draft_season = c.draft_season AND sp.prospect_key = c.prospect_key
LEFT JOIN draft_prospects d ON d.id = sp.prospect_id;

COMMIT;
