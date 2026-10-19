-- Napper fantasy playoffs (engines/napper_playoffs.py): 6-team bracket in weeks 15–17, seeds 1–2 on bye.

-- QUARTERFINAL (week 15), SEMIFINAL (week 16), FINAL (week 17); NULL = regular season.
ALTER TABLE napper_matchups ADD COLUMN IF NOT EXISTS playoff_round VARCHAR(16);

-- Set once the week 17 final is decided.
ALTER TABLE napper_leagues ADD COLUMN IF NOT EXISTS champion_team_id INT REFERENCES napper_teams(id) ON DELETE SET NULL;

-- Weeks 15–17 used to get placeholder pairings when someone opened them; the bracket replaces them.
DELETE FROM napper_matchups WHERE week >= 15 AND playoff_round IS NULL;
