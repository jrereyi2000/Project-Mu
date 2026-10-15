-- Marcus Whitfield takes Carolina's schemes and pass/blitz/man tendencies from Dave Canales: the playbooks haven't
-- changed since 2026, so the team keeps them. Fourth-down aggressiveness is the head coach's own call and Whitfield
-- has no earlier head-coaching record, so it is Medium (PROJECT_SPEC §3.3a).
-- Run once: psql -f db/migrations/2026_11_10_whitfield_tendencies.sql
UPDATE coaches w SET offensive_scheme = c.offensive_scheme, defensive_scheme = c.defensive_scheme,
                     pass_lean = c.pass_lean, fourth_down_aggressiveness = 'Medium',
                     blitz_rate = c.blitz_rate, man_coverage = c.man_coverage
FROM coaches c
WHERE w.coach_name = 'Marcus Whitfield' AND c.coach_name = 'Dave Canales';
