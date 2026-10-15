-- coaches.archetype was never filled or read (PROJECT_SPEC §3.3a).
-- Run once: psql -f db/migrations/2026_11_02_drop_coach_archetype.sql
ALTER TABLE coaches DROP COLUMN IF EXISTS archetype;
