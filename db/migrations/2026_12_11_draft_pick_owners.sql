-- Traded draft picks as of 2036 week 4, from JR's list (2026-10-20). Every pick not listed is still owned
-- by its original team (the SEED rows from engines.gm_personas seed). Data fix: run after 2026_12_10_gm_agents.sql
-- and the GM seed. Idempotent; stops if any listed pick is missing.
BEGIN;

CREATE TEMP TABLE jr_pick_owners (draft_season int, round int, owner text, original text) ON COMMIT DROP;
INSERT INTO jr_pick_owners VALUES
  (2037, 1, 'Broncos',    'Browns'),
  (2037, 1, 'Packers',    'Titans'),
  (2037, 1, 'Steelers',   'Chiefs'),
  (2037, 2, 'Cowboys',    'Jets'),
  (2037, 2, 'Dolphins',   'Chiefs'),
  (2037, 3, 'Broncos',    'Browns'),
  (2037, 3, 'Rams',       'Chiefs'),
  (2037, 4, 'Cowboys',    'Chiefs'),
  (2037, 4, 'Dolphins',   'Buccaneers'),
  (2037, 5, 'Chargers',   'Falcons'),
  (2037, 5, 'Jaguars',    'Buccaneers'),
  (2037, 6, 'Chiefs',     '49ers'),
  (2037, 6, 'Commanders', 'Falcons'),
  (2037, 6, 'Steelers',   'Seahawks'),
  (2037, 7, 'Bears',      'Lions'),
  (2037, 7, 'Bengals',    'Falcons'),
  (2037, 7, 'Bengals',    'Jaguars'),
  (2037, 7, 'Buccaneers', 'Seahawks'),
  (2037, 7, 'Cardinals',  'Rams'),
  (2037, 7, 'Commanders', 'Raiders'),
  (2037, 7, 'Commanders', 'Giants'),
  (2037, 7, 'Jaguars',    'Saints'),
  (2037, 7, 'Panthers',   'Browns'),
  (2038, 7, 'Broncos',    'Lions');

DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n
  FROM jr_pick_owners j
  JOIN teams o ON o.name = j.owner
  JOIN teams t ON t.name = j.original
  JOIN draft_picks p ON p.draft_season = j.draft_season AND p.round = j.round AND p.original_team_id = t.id;
  IF n <> (SELECT count(*) FROM jr_pick_owners) THEN
    RAISE EXCEPTION 'draft_pick_owners: matched % of % listed picks', n, (SELECT count(*) FROM jr_pick_owners);
  END IF;
END $$;

UPDATE draft_picks p
SET owner_team_id = o.id, source = 'JR'
FROM jr_pick_owners j
JOIN teams o ON o.name = j.owner
JOIN teams t ON t.name = j.original
WHERE p.draft_season = j.draft_season AND p.round = j.round AND p.original_team_id = t.id
  AND (p.owner_team_id <> o.id OR p.source <> 'JR');

COMMIT;
