-- @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history data)
-- @REPLACED-BY: db/seed_clean_db.py + db/init_db.sql (old-universe data only; AUDIT_LEDGER.md §3b)
-- Robert Saleh's 2025 baseline row held his real Jets record (20-36), but Madden started his career at 0-0 in 2026
-- (JR, 2026-10-26: the row is stale). His end-of-2034 row (83-70) is exactly 153 games = 9 x 17 since 2026, so with a
-- zero baseline the Titans' 2026-2034 record is 83-70, as docs/done/HISTORY_BACKFILL_PLAN.md §3.9 needs.
-- Run once after 2026_10_31_coach_baseline.sql: psql -f db/migrations/2026_12_25_saleh_2025_baseline.sql
BEGIN;

UPDATE coach_seasons cs
SET career_wins = 0, career_losses = 0, career_ties = 0, winning_seasons = 0,
    playoff_wins = 0, playoff_losses = 0, super_bowl_wins = 0, super_bowl_losses = 0
FROM coaches c
WHERE c.id = cs.coach_id AND c.coach_name = 'Robert Saleh' AND cs.season = 2025;

COMMIT;
