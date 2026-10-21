-- Migration: Front-office scores follow the GM, not the team (engines/fo_quality.py, engines/dev_quality.py,
-- engines/fo_standing.py; PROJECT_SPEC §2.18, §3.3f).
-- first_draft_season = the first draft class a GM picks. A GM's draft score counts only classes from it on, and his
-- development score only windows starting at his start_season, so a newly hired GM starts at the baseline
-- (q 1.00, D* 0). The hot seat fires at the Season scan, after that year's draft, so a replacement's first class is
-- season + 1 (engines/gm_personas.seed). The GMs seeded at launch were hired before their first season's draft:
-- first_draft_season = start_season.

BEGIN;

ALTER TABLE gm_profiles ADD COLUMN IF NOT EXISTS first_draft_season INT;
UPDATE gm_profiles SET first_draft_season = start_season WHERE first_draft_season IS NULL;

COMMIT;
