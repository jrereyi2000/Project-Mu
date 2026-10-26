-- Coaching carousel (PROJECT_SPEC §2.19): a head-coach hire records how attractive his opening was (rank among the
-- cycle's openings, appeal, and its parts: QB, roster, draft capital, market, front office, cap, churn).
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_12_24_staff_moves_inputs.sql

BEGIN;
ALTER TABLE staff_moves ADD COLUMN IF NOT EXISTS inputs JSONB;
COMMIT;
