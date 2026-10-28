-- GM trade pacing reset (docs/done/team_management/03_GM_AGENTS_PLAN.md §2.4; PROJECT_SPEC §3.3f).
-- counts_for_pacing = false keeps a proposal out of the budget's executed / decided totals (p_exec) and out of
-- "already made this week", without deleting it. JR, 2026-10-20: the week 4 and 5 proposals were made under the
-- old open-combination search and don't count; the in-season engine now uses the 2025 trade shapes.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_12_13_gm_pacing.sql

BEGIN;
ALTER TABLE gm_trade_proposals ADD COLUMN IF NOT EXISTS counts_for_pacing BOOLEAN NOT NULL DEFAULT true;
COMMIT;
