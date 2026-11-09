-- Bring a database that predates these tightenings to the state db/init_db.sql builds (CLOSE_OUT_CHECKLIST
-- P1-03). Both differences were found by diffing a fully-migrated database against a fresh db/init_db.sql
-- build; db/init_db.sql already carries the final state, so this file mirrors it for existing databases.
--
--  1. napper_leagues.season / napper_drafts.season: 2026_12_02 created both NOT NULL (and 2026_12_03 dropped
--     only the DEFAULT). A table created before 2026_12_02 kept the nullable column because the migration's
--     guarded create was a no-op on it.
--  2. gm_profiles.start_season: 2026_12_10 created CHECK (start_season >= 2030); db/init_db.sql now allows a
--     tenure to start in 1970, so a real GM's tenure in the fresh-save era is not rejected.
--
-- Idempotent; seeds nothing; transactional. Also in db/init_db.sql (the ledger block mirrors every migration).

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM napper_leagues WHERE season IS NULL)
       OR EXISTS (SELECT 1 FROM napper_drafts WHERE season IS NULL) THEN
        RAISE EXCEPTION 'a napper league or draft has no season; set it from league_state.current_season first';
    END IF;
END $$;

ALTER TABLE IF EXISTS napper_leagues ALTER COLUMN season SET NOT NULL;
ALTER TABLE IF EXISTS napper_drafts ALTER COLUMN season SET NOT NULL;

ALTER TABLE IF EXISTS gm_profiles DROP CONSTRAINT IF EXISTS gm_profiles_start_season_check;
ALTER TABLE IF EXISTS gm_profiles
    ADD CONSTRAINT gm_profiles_start_season_check CHECK (start_season >= 1970);

COMMIT;
