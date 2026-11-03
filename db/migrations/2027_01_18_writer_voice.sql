-- Writer voice: person keys, relationship evidence, winner picks and season calls
-- (docs/in_progress/AGENT_WIRE_VOICE_PLAN.md §2.1, §2.2, §2.5; PROJECT_SPEC §3.3p, §7.7).
--   agent_memories.person_key        one person behind several accounts (Skip Bayless + his tweet account);
--                                    memory and writer relationships are read per person. Backfill = agent_id.
--   persona_relationships.evidence   the last events that moved trust ({season, week, kind, delta, text}).
--   agent_predictions.pick_type      'SCORE' (a projected final) or 'WINNER' (winner only: scores NULL,
--                                    winner_team_id set).
--   agent_season_calls               season-long calls (MVP, Super Bowl winner, playoff team, coach fired ...),
--                                    graded by newsroom/agent_memory.grade_season_calls.
-- Idempotent; tables that don't exist yet are skipped.
--
-- Apply with:
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_01_18_writer_voice.sql

BEGIN;

ALTER TABLE IF EXISTS agent_memories ADD COLUMN IF NOT EXISTS person_key VARCHAR(100);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'agent_memories') THEN
        UPDATE agent_memories SET person_key = agent_id WHERE person_key IS NULL;
        UPDATE agent_memories SET person_key = 'Skip_Bayless'
         WHERE archetype IN ('SKIP_BAYLESS', 'SKIP_TWEET') AND person_key IS DISTINCT FROM 'Skip_Bayless';
        CREATE INDEX IF NOT EXISTS idx_agent_memories_person_key ON agent_memories (person_key);
    END IF;
END $$;

ALTER TABLE IF EXISTS persona_relationships ADD COLUMN IF NOT EXISTS evidence JSONB DEFAULT '[]'::jsonb;

ALTER TABLE IF EXISTS agent_predictions ADD COLUMN IF NOT EXISTS pick_type VARCHAR(8) NOT NULL DEFAULT 'SCORE';
ALTER TABLE IF EXISTS agent_predictions ADD COLUMN IF NOT EXISTS winner_team_id INT;
ALTER TABLE IF EXISTS agent_predictions ALTER COLUMN score_a DROP NOT NULL;
ALTER TABLE IF EXISTS agent_predictions ALTER COLUMN score_b DROP NOT NULL;

CREATE TABLE IF NOT EXISTS agent_season_calls (
    id SERIAL PRIMARY KEY,
    agent_id VARCHAR(100) NOT NULL,          -- agent_memories.agent_id of the account that posted it
    person_key VARCHAR(100) NOT NULL,        -- agent_memories.person_key
    season INT NOT NULL,
    made_week INT NOT NULL,
    article_id INT REFERENCES news_feed(id) ON DELETE CASCADE,
    call_type VARCHAR(24) NOT NULL,          -- MVP | OPOY | DPOY | ROY | SUPER_BOWL | PLAYOFF_TEAM | MISSES_PLAYOFFS | DIVISION_WINNER | COACH_FIRED
    subject_type VARCHAR(10),                -- player | coach | team
    subject_id INT,
    detail TEXT,
    result VARCHAR(8) NOT NULL DEFAULT 'PENDING' CHECK (result IN ('PENDING', 'RIGHT', 'WRONG', 'VOID')),
    graded_week INT
);
CREATE INDEX IF NOT EXISTS idx_agent_season_calls_person ON agent_season_calls (person_key, season);

COMMIT;
