-- Migration: Front-office player development score (engines/dev_quality.py, PROJECT_SPEC §2.18, §3.3f)
-- Each team's players are scored on how their OVR moved from one season's roster card to the next, against the
-- league-wide expectation for their age, OVR, dev trait and rookie status. Separate from fo_quality (the draft
-- score that drives board noise): dev_quality is narrative only for now.
--   dev_quality        shrunk team score D* (0 = league expectation, positive = players beat it); NULL = not scored
--   dev_quality_inputs every scored player with label, D, D*, n, rank, windows
--   dev_quality_at     when engines/dev_quality.recompute last wrote it

BEGIN;

ALTER TABLE gm_team_seasons
    ADD COLUMN IF NOT EXISTS dev_quality NUMERIC(5,3),
    ADD COLUMN IF NOT EXISTS dev_quality_inputs JSONB,
    ADD COLUMN IF NOT EXISTS dev_quality_at TIMESTAMPTZ;

COMMIT;
