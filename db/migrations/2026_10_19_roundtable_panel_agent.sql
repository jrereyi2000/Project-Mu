-- Writer record for the Super Bowl Roundtable panel (ROUNDTABLE_PANEL), the only allowed writer of
-- SUPER_BOWL_ROUNDTABLE_CONSENSUS (Week 23, Slot 3). Without it the dispatcher used a stand-in identity.
-- The panel never tweets: newsroom/swarm_dispatcher.py excludes it from the reaction-tweet pool.
--
-- Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_10_19_roundtable_panel_agent.sql

BEGIN;
INSERT INTO agent_memories (agent_id, archetype, assigned_team_id, persistent_memory, handle, display_name)
VALUES (
    'Super_Bowl_Roundtable',
    'ROUNDTABLE_PANEL',
    NULL,
    'I am the Super Bowl Roundtable, a moderated panel of Mina Kimes, Emmanuel Acho, Bill Simmons and Field Yates. '
    'We disagree on purpose: tape and scheme, contrarian reads, legacy and narrative, and fantasy and prop angles each get a voice.',
    '@MuRoundtable',
    'Super Bowl Roundtable (Kimes, Acho, Simmons, Yates)'
)
ON CONFLICT (agent_id) DO NOTHING;
COMMIT;
