-- Migration: Charles Grant runs Indianapolis as head coach and GM (full control), from 2036
-- Per JR: when Indianapolis hired Charles Grant (2035 offseason package), it gave him full control of football
-- operations. He is the Colts' head coach (coach_tenures, 2036-present) and their GM.
--   * gm_profiles.coach_id (new): the coach who also serves as the GM; NULL for a GM who is only a GM.
--   * Russell Finch's tenure closes at 2035 (he ran the front office 2031-2035).
--   * Grant's GM tenure starts 2036; the 2036 draft came after his hire, so his first class is 2036. His traits are
--     the seeded replacement draw for IND in 2036 (gm_personas.replacement_traits, seed 'gm:IND:2036'); the bio and
--     voice are invented at JR's request (bio_source 'LLM' so the template backfill never rewrites them).
--   * IND's 2036 gm_team_seasons row points at Grant.

BEGIN;

ALTER TABLE gm_profiles ADD COLUMN IF NOT EXISTS coach_id INT REFERENCES coaches(id);

UPDATE gm_profiles g
SET end_season = 2035,
    end_reason = 'Charles Grant hired as head coach with full control of football operations'
FROM teams t
WHERE t.id = g.team_id AND t.abbreviation = 'IND' AND g.gm_name = 'Russell Finch' AND g.end_season IS NULL;

INSERT INTO gm_profiles (team_id, gm_name, bio, voice, bio_source, start_season, first_draft_season, coach_id,
                         risk_appetite, aggressiveness, pick_affinity, youth_bias, position_bias, seed)
SELECT t.id, 'Charles Grant',
       'Charles Grant took over Indianapolis in 2036 as both head coach and general manager, the rare hire handed the '
       'headset and the final say on the roster. The former Panthers offensive coordinator wanted a roster built for '
       'his Wide Zone West Coast attack and the authority to shape it himself, and he guards draft capital closely '
       'while weighing youth and experience case by case.',
       'A play-caller''s directness: talks about scheme fit and winning now, measured in negotiations but quick to '
       'move once the price is right.',
       'LLM', 2036, 2036, c.id, 0.25, 0.58, 1.18, -0.05, '{"DT": 0.88}'::jsonb, 'gm:IND:2036'
FROM teams t
JOIN coaches c ON c.coach_name = 'Charles Grant'
WHERE t.abbreviation = 'IND'
  AND NOT EXISTS (SELECT 1 FROM gm_profiles g WHERE g.team_id = t.id AND g.end_season IS NULL);

UPDATE gm_team_seasons s
SET gm_id = g.id
FROM gm_profiles g JOIN teams t ON t.id = g.team_id
WHERE t.abbreviation = 'IND' AND g.gm_name = 'Charles Grant' AND g.end_season IS NULL
  AND s.team_id = t.id AND s.season = 2036;

COMMIT;
