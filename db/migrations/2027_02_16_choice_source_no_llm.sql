-- The turn's chooser is the policy or JR, never a model (GM_TALK_VOICE_PLAN U4; chunk-4 cleanup).
-- `message_source` is the writer's (LLM | TEMPLATE | JR); `choice_source` is who picked the move, and the writer-only
-- path means that can only be POLICY or JR. The CHECK still listed 'LLM' from before the talk path dropped
-- llm_choose, so a future change could store a model-chosen move without anyone noticing. No row in madden_test or
-- madden_mu2026 carries 'LLM' (the tables are empty there), so the narrowing is safe.
--
-- Apply with:
--   python3 -m db.migrate apply --file 2027_02_16_choice_source_no_llm.sql

BEGIN;

ALTER TABLE gm_trade_negotiation_turns
  DROP CONSTRAINT IF EXISTS gm_trade_negotiation_turns_choice_source_check,
  ADD CONSTRAINT gm_trade_negotiation_turns_choice_source_check
    CHECK (choice_source IN ('POLICY', 'JR'));

COMMIT;
