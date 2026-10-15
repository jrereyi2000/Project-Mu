-- Which estimated team_stats cells are exact: a team's only missing cell for a column gets the season total's
-- change since the stored cells, so it is the real value (engines/team_box_estimator.py, PROJECT_SPEC §2.6).
-- Every cell estimated before this (the 2035 Weeks 1-17 backlog) was a multi-game split, so none starts exact.
ALTER TABLE team_stats ADD COLUMN IF NOT EXISTS exact_fields TEXT[] NOT NULL DEFAULT '{}';
