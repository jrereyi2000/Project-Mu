-- Marcus Whitfield's own schemes and tendencies (per JR, Oct 15), replacing the ones inherited from Dave Canales
-- in 2026_11_10_whitfield_tendencies.sql (PROJECT_SPEC §3.3a).
-- Run once: psql -f db/migrations/2026_11_11_whitfield_ratings.sql
UPDATE coaches SET offensive_scheme = 'Wide Zone West Coast', defensive_scheme = '4-3', pass_lean = 'Medium',
                   fourth_down_aggressiveness = 'High', blitz_rate = 'High', man_coverage = 'Low'
WHERE coach_name = 'Marcus Whitfield';
