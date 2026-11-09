-- Team-private scouting report rows (OWNERS_SCOUTS_VOTERS U6 follow-up = CLOSE_OUT_CHECKLIST P1-11;
-- PROJECT_SPEC §2.18). A team's scouting director voices its reports: the department row (team_id NULL) stays the
-- public consensus and never changes, and each team gets its own row with grade = consensus + the director's
-- bounded bias, the director's scout_id and voice. The old PRIMARY KEY (prospect_uid, edition) cannot hold one row
-- per team, so it moves to two partial unique indexes (a NULL team_id is the department row).
--
-- Apply with:
--   python3 -m db.migrate apply --file 2027_02_11_scout_report_team_rows.sql

BEGIN;

ALTER TABLE IF EXISTS scout_reports ADD COLUMN IF NOT EXISTS team_id INT REFERENCES teams(id);
ALTER TABLE IF EXISTS scout_reports DROP CONSTRAINT IF EXISTS scout_reports_pkey;
CREATE UNIQUE INDEX IF NOT EXISTS scout_reports_dept_once
    ON scout_reports (prospect_uid, edition) WHERE team_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS scout_reports_team_once
    ON scout_reports (prospect_uid, edition, team_id) WHERE team_id IS NOT NULL;

COMMIT;
