-- Live multi-turn CPU trade talks (docs/in_progress/TRADE_NEGOTIATIONS_PLAN.md §2.2, U1; PROJECT_SPEC §2.17, §3.3f).
-- Extends the JR-vs-GM negotiation tables for two-GM CPU talks (engines/gm_talks.py): a talk's kind, who called,
-- the proposal a talk came from / produced, the week it carried over and when its transcript was published; on
-- each turn, the acting team, the options the chooser saw, the chosen option and its source, and the posture.
-- New status STALLED on talks and proposals (a broken-down talk must not be REJECTED: REJECTED bars its players
-- from the week's engine proposals, STALLED counts as decided for pacing without barring anyone). New table
-- gm_trade_leaks (rumors from a talk, positions only unless the leaker names players).
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_01_22_trade_talks.sql

BEGIN;

ALTER TABLE gm_trade_negotiations
  ADD COLUMN IF NOT EXISTS kind VARCHAR(4) NOT NULL DEFAULT 'USER' CHECK (kind IN ('USER', 'CPU')),
  ADD COLUMN IF NOT EXISTS initiator_team_id INT REFERENCES teams(id),          -- CPU: the GM who called; USER: NULL
  ADD COLUMN IF NOT EXISTS source_proposal_id INT REFERENCES gm_trade_proposals(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS carried_weeks SMALLINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;
-- status gains STALLED; for kind CPU, user_team_id holds the initiator's team (same as initiator_team_id)
ALTER TABLE gm_trade_negotiations DROP CONSTRAINT IF EXISTS gm_trade_negotiations_status_check;
ALTER TABLE gm_trade_negotiations ADD CONSTRAINT gm_trade_negotiations_status_check
  CHECK (status IN ('OPEN', 'AGREED', 'WALKED_AWAY', 'CLOSED', 'EXPIRED', 'STALLED'));

ALTER TABLE gm_trade_negotiation_turns
  ADD COLUMN IF NOT EXISTS actor_team_id INT REFERENCES teams(id),
  ADD COLUMN IF NOT EXISTS options JSONB,            -- [{key, action, assets, su, sg}] the chooser saw (never public)
  ADD COLUMN IF NOT EXISTS choice TEXT,              -- the chosen option key
  ADD COLUMN IF NOT EXISTS choice_source VARCHAR(6) CHECK (choice_source IN ('LLM', 'POLICY', 'JR')),
  ADD COLUMN IF NOT EXISTS posture VARCHAR(8) CHECK (posture IN ('FIRM', 'ANCHOR', 'SQUEEZE', 'MEET', 'HOLD'));
-- actor widens for GM_A (the initiating GM in a CPU talk; GM is the other GM)
ALTER TABLE gm_trade_negotiation_turns ALTER COLUMN actor TYPE VARCHAR(4);
ALTER TABLE gm_trade_negotiation_turns DROP CONSTRAINT IF EXISTS gm_trade_negotiation_turns_actor_check;
ALTER TABLE gm_trade_negotiation_turns ADD CONSTRAINT gm_trade_negotiation_turns_actor_check
  CHECK (actor IN ('JR', 'GM', 'GM_A'));

ALTER TABLE gm_trade_proposals DROP CONSTRAINT IF EXISTS gm_trade_proposals_status_check;
ALTER TABLE gm_trade_proposals ADD CONSTRAINT gm_trade_proposals_status_check
  CHECK (status IN ('PROPOSED', 'APPROVED', 'EXECUTED', 'REJECTED', 'EXPIRED', 'STALLED'));

CREATE TABLE IF NOT EXISTS gm_trade_leaks (
  id SERIAL PRIMARY KEY,
  negotiation_id INT NOT NULL REFERENCES gm_trade_negotiations(id) ON DELETE CASCADE,
  turn_no INT NOT NULL,
  season INT NOT NULL,
  week INT NOT NULL,                                 -- engine week of the leak
  leaker_team_id INT NOT NULL REFERENCES teams(id),
  stage VARCHAR(7) NOT NULL CHECK (stage IN ('OPENED', 'CLOSE', 'STALLED')),
  named_player_ids INT[] NOT NULL DEFAULT '{}',      -- empty: positions only
  detail JSONB NOT NULL,                             -- {teams:[abbr,abbr], initiator, positions:[{pos, from, to}], picks:int}
  insider_key TEXT,                                  -- agent_personas.json key of the writer who got it first
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (negotiation_id, turn_no)
);
CREATE INDEX IF NOT EXISTS gm_trade_leaks_week ON gm_trade_leaks (season, week);

COMMIT;
