-- Postgame press conferences (docs/in_progress/PRESS_CONFERENCE_PLAN.md §2.7; PROJECT_SPEC §3.3r, §7.14).
--   press_conferences  one head coach's postgame presser for one game (both teams of a game get one)
--   press_attendees    the reporters in that room, their counters and their validated appraisal
--   press_hands        who had a hand up each round, and who the coach picked
--   press_turns        the transcript (OPENING / QUESTION / FOLLOW_UP / REACT / ANSWER / NO_COMMENT / CLOSING / NOTE)
-- Idempotent (IF NOT EXISTS); seeds nothing. Also in db/init_db.sql.
--
-- Apply with:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 < db/migrations/2027_01_20_press_conferences.sql

BEGIN;

CREATE TABLE IF NOT EXISTS press_conferences (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT NOT NULL,                                -- the game's week
    game_id INT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    subject_type VARCHAR(10) NOT NULL DEFAULT 'coach' CHECK (subject_type IN ('coach', 'player')),
    subject_id INT,                                   -- coaches.id now; players.id later
    subject_name VARCHAR(255) NOT NULL,
    controller VARCHAR(5) NOT NULL CHECK (controller IN ('USER', 'AGENT')),
    status VARCHAR(10) NOT NULL DEFAULT 'SCHEDULED'
        CHECK (status IN ('SCHEDULED', 'LIVE', 'REVIEW', 'CLOSED', 'DECLINED', 'FAILED')),
    end_reason VARCHAR(16) CHECK (end_reason IN ('RAN_OUT', 'SUBJECT_ENDED', 'PATIENCE', 'CAP', 'DECLINED')),
    interest REAL, tier VARCHAR(10), cast_inputs JSONB,
    facts JSONB NOT NULL,                             -- press_facts.fact_sheet, frozen
    patience JSONB,                                   -- {"budget", "spent", "mood"} plus the session scratch keys
    model VARCHAR(100), calls INT NOT NULL DEFAULT 0, input_tokens INT NOT NULL DEFAULT 0,
    output_tokens INT NOT NULL DEFAULT 0,
    effects_applied BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(), started_at TIMESTAMP, closed_at TIMESTAMP,
    UNIQUE (game_id, team_id, subject_type, subject_id)
);
CREATE INDEX IF NOT EXISTS idx_press_conferences_week ON press_conferences (season, week, status);

CREATE TABLE IF NOT EXISTS press_attendees (
    conference_id INT NOT NULL REFERENCES press_conferences(id) ON DELETE CASCADE,
    agent_id VARCHAR(100) NOT NULL,                   -- agent_memories.agent_id
    person_key VARCHAR(100) NOT NULL,
    role VARCHAR(8) NOT NULL CHECK (role IN ('BEAT', 'NATIONAL')),
    pool_tier VARCHAR(10),
    questions INT NOT NULL DEFAULT 0, passes INT NOT NULL DEFAULT 0, done BOOLEAN NOT NULL DEFAULT FALSE,
    hands INT NOT NULL DEFAULT 0, picked INT NOT NULL DEFAULT 0,       -- rounds with a hand up / times picked
    judgment JSONB,                                   -- validated reporter_judgment output
    PRIMARY KEY (conference_id, agent_id)
);

CREATE TABLE IF NOT EXISTS press_hands (                -- who had a hand up each round, and who was picked
    conference_id INT NOT NULL REFERENCES press_conferences(id) ON DELETE CASCADE,
    round INT NOT NULL,
    agent_id VARCHAR(100) NOT NULL,
    picked BOOLEAN NOT NULL DEFAULT FALSE,
    pick_source VARCHAR(8) CHECK (pick_source IN ('AGENT', 'USER', 'FALLBACK')),   -- set on the picked row
    PRIMARY KEY (conference_id, round, agent_id)
);

CREATE TABLE IF NOT EXISTS press_turns (
    id SERIAL PRIMARY KEY,
    conference_id INT NOT NULL REFERENCES press_conferences(id) ON DELETE CASCADE,
    seq INT NOT NULL,
    speaker VARCHAR(10) NOT NULL CHECK (speaker IN ('REPORTER', 'SUBJECT')),
    agent_id VARCHAR(100),                            -- reporters only
    kind VARCHAR(10) NOT NULL CHECK (kind IN ('OPENING', 'QUESTION', 'FOLLOW_UP', 'REACT', 'ANSWER', 'NO_COMMENT',
                                              'CLOSING', 'NOTE')),
    reply_to INT REFERENCES press_turns(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    about JSONB NOT NULL DEFAULT '[]'::jsonb,
    tone VARCHAR(8),
    source VARCHAR(8) NOT NULL CHECK (source IN ('AGENT', 'USER', 'FALLBACK', 'ENGINE')),
    issues JSONB NOT NULL DEFAULT '[]'::jsonb,        -- check_line output (kept for audit; warnings on USER turns)
    retries SMALLINT NOT NULL DEFAULT 0,
    on_record BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (conference_id, seq)
);

COMMIT;
