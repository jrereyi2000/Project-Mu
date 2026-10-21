-- Migration: Marcus Whitfield runs Carolina as head coach and GM (full control), since 2034
-- Per JR: Whitfield (Panthers head coach, coach_tenures 2034-present) has also been Carolina's GM since 2034.
-- The seeded persona Bennett Thorne (GM since 2036) never held the job, so his row becomes Whitfield's in place
-- (same id, so CAR's gm_team_seasons rows keep pointing at it; same seeded traits 'gm:CAR'); the bio and voice are
-- invented at JR's request (bio_source 'LLM' so the template backfill never rewrites them). His first draft as GM is
-- the 2034 class.

BEGIN;

UPDATE gm_profiles g
SET gm_name = 'Marcus Whitfield',
    bio = 'Marcus Whitfield has run Carolina as both head coach and general manager since 2034, holding the final '
          'say on the roster as well as the headset. He builds around young, traits-rich players and perimeter '
          'speed, accepts calculated volatility for a higher ceiling, and is measured in negotiations but quick to '
          'move once the price is right.',
    voice = 'A head coach''s conviction: pragmatic yet ceiling-obsessed, framing moves around athleticism, '
            'developmental runway and youthful speed.',
    bio_source = 'LLM',
    start_season = 2034,
    first_draft_season = 2034,
    coach_id = c.id
FROM teams t, coaches c
WHERE t.id = g.team_id AND t.abbreviation = 'CAR' AND g.gm_name = 'Bennett Thorne' AND g.end_season IS NULL
  AND c.coach_name = 'Marcus Whitfield';

COMMIT;
