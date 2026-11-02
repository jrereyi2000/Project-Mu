-- Persona slider total becomes a band (docs/in_progress/CAP_RELATIVE_CONTRACTS_PLAN.md §2.5;
-- PERSONALITY_PLAN.md §4.1; PROJECT_SPEC §3.3p, §7.11). Requires 2027_01_09_personas.sql.
-- chk_persona_sliders_budget was "total = 30"; it becomes "total BETWEEN 24 AND 36". Seeding still deals exactly 30,
-- so every stored row already passes. No-op where the persona tables don't exist. Idempotent.
--
-- Apply with:
--   docker exec -i madden-db psql -U postgres -d madden_test -v ON_ERROR_STOP=1 < db/migrations/2027_01_17_persona_slider_band.sql

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'persona_sliders') THEN
        ALTER TABLE persona_sliders DROP CONSTRAINT IF EXISTS chk_persona_sliders_budget;
        ALTER TABLE persona_sliders ADD CONSTRAINT chk_persona_sliders_budget
            CHECK (temperament + sociability + confidence + openness + warmth + drive BETWEEN 24 AND 36);
    END IF;
END $$;

COMMIT;
