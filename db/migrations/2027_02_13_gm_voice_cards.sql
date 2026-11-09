-- GM speech cards (GM_TALK_VOICE_PLAN §2.1, U1; PROJECT_SPEC §3.3f).
-- Every GM tenure gets a voice card: the register he talks in, sample lines, verbal tics, words he never says and
-- what he wants from a call. Authored for the 2026 cast (data/gm_voices_2026.json), generated at hire afterwards,
-- with a deterministic trait template as the fallback, so no GM is ever voiceless.
--
-- Apply with:
--   python3 -m db.migrate apply --file 2027_02_13_gm_voice_cards.sql

BEGIN;

ALTER TABLE gm_profiles
  ADD COLUMN IF NOT EXISTS voice_card JSONB,
  ADD COLUMN IF NOT EXISTS voice_card_source TEXT
    CHECK (voice_card_source IN ('AUTHORED', 'LLM', 'TEMPLATE'));

COMMIT;
