-- Scouting reports and combine interviews (docs/in_progress/SCOUTING_REVAMP_PLAN.md §2.1, V2.10 U1; PROJECT_SPEC §3.3g).
--   scout_prospect_ids      one permanent prospect_uid per prospect (uuid5 of a birth key, never recomputed); the
--                           older scouting tables' prospect_key is only its current alias (engines/prospect_ids.py).
--                           The personality lifecycle plan adopts prospect_uid as source_person_id (§2.1b)
--   scout_prospect_ratings  the save's native ratings for each prospect, stored at every load
--                           (pipeline/prospect_ratings.py). HIDDEN: never in a view, endpoint, writer tool or #debug
--   scout_reports           a prospect's written report per edition (FALL, BOWLS, FINAL); cascades with its step
--   scout_interviews        JR's combine interviews and top-30 visits, each a PROSPECT inbox thread; never
--                           personality evidence
-- Needs 2026_12_12_scouting.sql (scout_runs) and 2027_01_19_front_office_inbox.sql (inbox_threads).
--
-- Run ./db/backup_db.sh first. Idempotent; seeds nothing. Also in db/init_db.sql. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_01_21_scouting_reports.sql

BEGIN;

-- Permanent prospect IDs (§2.1b). prospect_key is only the current alias into the older scouting tables.
CREATE TABLE IF NOT EXISTS scout_prospect_ids (
    prospect_uid     UUID PRIMARY KEY,                 -- uuid5(MU_PROSPECT_NS, birth_key); never changes
    birth_key        TEXT NOT NULL UNIQUE,             -- 'save-pgid:<draft_season>:<pgid>' or 'board:<draft_season>:<first prospect_key>'
    draft_season     INT NOT NULL,
    pgid             BIGINT,                           -- NULL for a scanned (PS5) class
    prospect_key     VARCHAR(80) NOT NULL,             -- current alias
    key_history      JSONB NOT NULL DEFAULT '[]',      -- [{"prospect_key","until_capture"}]
    status           VARCHAR(12) NOT NULL DEFAULT 'OK' CHECK (status IN ('OK','KEY_COLLISION')),
    source_person_id UUID,                             -- set by the lifecycle plan when it adopts this person
    first_seen       VARCHAR(255) NOT NULL,            -- capture folder
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS scout_prospect_ids_key ON scout_prospect_ids (draft_season, prospect_key);

-- Hidden: never in a view, endpoint, writer tool or #debug panel.
CREATE TABLE IF NOT EXISTS scout_prospect_ratings (
    prospect_uid   UUID PRIMARY KEY REFERENCES scout_prospect_ids(prospect_uid),
    draft_season   INT NOT NULL,
    prospect_key   VARCHAR(80) NOT NULL,            -- alias at load time
    pgid           BIGINT,
    ratings        JSONB NOT NULL,                  -- Madden Player.<Name>Rating names without "Rating", 0-99
    capture_folder VARCHAR(255) NOT NULL,
    loaded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scout_reports (
    draft_season   INT NOT NULL,
    step           SMALLINT NOT NULL,
    prospect_uid   UUID NOT NULL REFERENCES scout_prospect_ids(prospect_uid),
    prospect_key   VARCHAR(80) NOT NULL,            -- alias at the step, for joins with scout_consensus/scout_events
    edition        VARCHAR(8) NOT NULL CHECK (edition IN ('FALL','BOWLS','FINAL')),
    grade          NUMERIC(5,1) NOT NULL,           -- the step's consensus grade (copied, public)
    rank           INT NOT NULL,                    -- the step's consensus rank (public)
    round_label    VARCHAR(16) NOT NULL,
    role           VARCHAR(24) NOT NULL,            -- ROLE_BANDS key (§2.3)
    traits         JSONB NOT NULL,                  -- [{"trait","label","score","word"}], [] without ratings
    strengths      JSONB NOT NULL,                  -- ["trait", ...] 0-3
    concerns       JSONB NOT NULL,                  -- ["trait" or flag, ...] 0-3
    flags          JSONB NOT NULL,                  -- ["INJURY_HISTORY", ...] (§2.3)
    body           JSONB NOT NULL,                  -- {"summary","strengths","concerns","production","athletic","projection","bottom_line"}
    text_source    VARCHAR(8) NOT NULL DEFAULT 'TEMPLATE' CHECK (text_source IN ('TEMPLATE','LLM')),
    scout_id       INT,                             -- NULL = the Mu Scouting Department (phase 6 hook)
    voice          JSONB,                           -- NULL = department voice (phase 6 hook)
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (prospect_uid, edition),
    FOREIGN KEY (draft_season, step) REFERENCES scout_runs (draft_season, step) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scout_interviews (
    id               SERIAL PRIMARY KEY,
    draft_season     INT NOT NULL,
    team_id          INT NOT NULL REFERENCES teams(id),
    prospect_uid     UUID NOT NULL REFERENCES scout_prospect_ids(prospect_uid),
    prospect_key     VARCHAR(80) NOT NULL,          -- alias when opened (VISIT events are written under the current alias)
    kind             VARCHAR(8) NOT NULL CHECK (kind IN ('COMBINE','VISIT')),
    thread_id        INT REFERENCES inbox_threads(id) ON DELETE SET NULL,   -- the PROSPECT inbox thread
    window_step      SMALLINT NOT NULL,             -- latest stored step when opened (24 or 25)
    logged_step      SMALLINT,                      -- VISIT: the step whose VISIT event it became (25 or 26); NULL until then
    status           VARCHAR(8) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')),
    questions_max    SMALLINT NOT NULL,
    tone             VARCHAR(8) NOT NULL CHECK (tone IN ('STRONG','STEADY','MIXED')),
    disposition_src  VARCHAR(12) NOT NULL DEFAULT 'NEUTRAL' CHECK (disposition_src IN ('NEUTRAL','PERSONALITY')),
    turns            JSONB NOT NULL DEFAULT '[]',   -- [{"seq","topic","grade","classified_by"}]; words live in inbox_messages
    summary_grade    VARCHAR(12),                   -- ANSWER_GRADES value, the interview's overall read (set on close)
    opened_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at        TIMESTAMPTZ,
    UNIQUE (draft_season, team_id, prospect_uid, kind)
);

COMMIT;
