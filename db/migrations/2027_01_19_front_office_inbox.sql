-- Front-office inbox (docs/in_progress/FRONT_OFFICE_INBOX_PLAN.md §2.6, V2.9 U1; PROJECT_SPEC §3.3s).
--   controlled_personas        personas JR has explicitly taken control of (none by default, D8)
--   inbox_threads              one conversation: a controlled persona <-> a counterpart agent
--   inbox_messages             the thread's lines, numbered by seq
--   inbox_relationship_events  outbox of relationship events (PERSONALITY_RELATIONSHIPS_PLAN §2.2 envelope),
--                              appended to personality.relationship_store once it exists; the inbox never
--                              writes persona_relationships (D15)
--   team_vision                the coach and GM's agreed vision per team and season (consumed by gm_direction, U6)
-- Idempotent (IF NOT EXISTS); seeds nothing. Also in db/init_db.sql.
--
-- Apply with:
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_01_19_front_office_inbox.sql

BEGIN;

CREATE TABLE IF NOT EXISTS controlled_personas (
    team_id     INT NOT NULL REFERENCES teams(id),
    role        VARCHAR(8) NOT NULL CHECK (role IN ('GM','COACH','PLAYER')),
    persona_id  VARCHAR(40) NOT NULL,              -- gm_profiles.id | coaches.id | players.id
    active      BOOLEAN NOT NULL DEFAULT true,
    taken_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ,
    PRIMARY KEY (team_id, role, persona_id)
);   -- JR controls no one by default (D8); rows of teams outside the registry's user_teams are ignored

CREATE TABLE IF NOT EXISTS inbox_threads (
    id                SERIAL PRIMARY KEY,
    team_id           INT NOT NULL REFERENCES teams(id),
    kind              VARCHAR(24) NOT NULL,                    -- registry key; no CHECK (sibling plans add kinds)
    actor_role        VARCHAR(8) NOT NULL CHECK (actor_role IN ('GM','COACH','PLAYER','TEAM')),
    actor_id          VARCHAR(40) NOT NULL,                    -- the controlled persona; 'TEAM' + abbr for notices
    counterpart_type  VARCHAR(16) NOT NULL
        CHECK (counterpart_type IN ('gm','coach','player','writer','prospect','scout','owner','system')),
    counterpart_id    VARCHAR(100) NOT NULL,                   -- gm_profiles.id, coaches.id, players.id, agent_memories.agent_id, prospect_key
    counterpart_name  VARCHAR(255) NOT NULL,
    subject           VARCHAR(255) NOT NULL,
    status            VARCHAR(12) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','WAITING_JR','CLOSED','EXPIRED')),
    started_by        VARCHAR(12) NOT NULL CHECK (started_by IN ('JR','COUNTERPART','SYSTEM')),
    source_ref        VARCHAR(120) NOT NULL,
    link_table        VARCHAR(48),
    link_id           INT,
    state             JSONB NOT NULL DEFAULT '{}',             -- kind-owned: {"questions_asked": 4}, {"promises": [...]}
    season            INT NOT NULL,
    week              INT,
    offseason_stage   VARCHAR(16),
    expires_season    INT,
    expires_week      INT,
    unread            BOOLEAN NOT NULL DEFAULT true,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (team_id, kind, source_ref)
);
CREATE INDEX IF NOT EXISTS inbox_threads_team_status ON inbox_threads (team_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS inbox_messages (
    id           SERIAL PRIMARY KEY,
    thread_id    INT NOT NULL REFERENCES inbox_threads(id) ON DELETE CASCADE,
    seq          SMALLINT NOT NULL,
    author       VARCHAR(12) NOT NULL CHECK (author IN ('JR','COUNTERPART','SYSTEM')),
    body         TEXT NOT NULL,
    body_source  VARCHAR(10) NOT NULL CHECK (body_source IN ('JR','LLM','TEMPLATE','ENGINE')),
    option_id    VARCHAR(40),
    options      JSONB NOT NULL DEFAULT '[]',
    facts        JSONB,                       -- never sent to the UI
    engine_ref   JSONB,
    appraisal    JSONB,                       -- the counterpart's appraisal of the JR message this replies to (NULL = none)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (thread_id, seq)
);

CREATE TABLE IF NOT EXISTS inbox_relationship_events (     -- outbox for PERSONALITY_RELATIONSHIPS_PLAN's store
    id               SERIAL PRIMARY KEY,
    source_event_id  VARCHAR(120) NOT NULL UNIQUE,         -- 'inbox:msg:812', 'inbox:promise:44:1:made'
    kind             VARCHAR(32) NOT NULL CHECK (kind IN ('meaningful_interaction','treatment','promise_made','promise_resolved')),
    thread_id        INT REFERENCES inbox_threads(id) ON DELETE SET NULL,
    message_id       INT REFERENCES inbox_messages(id) ON DELETE SET NULL,
    event            JSONB NOT NULL,                       -- the full §2.2 envelope (tick and PersonRefs filled at flush)
    sim_time         JSONB NOT NULL,                       -- {season, week, offseason_stage}
    status           VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPENDED','REJECTED')),
    status_reason    TEXT,
    appended_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS team_vision (
    team_id       INT NOT NULL REFERENCES teams(id),
    season        INT NOT NULL,
    direction     VARCHAR(8) CHECK (direction IN ('CONTEND','RETOOL','REBUILD')),   -- NULL = no pin
    emphasis      VARCHAR(8) NOT NULL DEFAULT 'BALANCED' CHECK (emphasis IN ('OFFENSE','DEFENSE','BALANCED')),
    playing_time  VARCHAR(8) NOT NULL DEFAULT 'MERIT' CHECK (playing_time IN ('YOUTH','VETERANS','MERIT')),
    thread_id     INT REFERENCES inbox_threads(id) ON DELETE SET NULL,
    agreed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, season)
);

COMMIT;
