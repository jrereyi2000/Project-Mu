-- Season-end archive and head-coach records from 2035 (docs/SEASON_CONTINUITY_PLAN.md phase 1; PROJECT_SPEC §3.3h).
-- engines/season_archive.py writes these once a season's Super Bowl is COMPLETED (the weekly run's postgame step, or
-- `python3 -m engines.season_archive --season S`): the season's champion, every team's final line (computed from
-- games: ties, points, division place with the tiebreaker that decided it, conference seed read off the bracket,
-- playoff result, head coach and GM), each head coach's season line, and his career snapshot in coach_seasons
-- (the previous snapshot + that season; JR 2026-10-26: coach records start in 2035).
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_12_24_season_archive.sql

BEGIN;

-- One row per finished season.
CREATE TABLE IF NOT EXISTS league_seasons (
    season INT PRIMARY KEY,
    champion_team_id INT REFERENCES teams(id),
    runner_up_team_id INT REFERENCES teams(id),
    super_bowl_game_id INT REFERENCES games(id) ON DELETE SET NULL,
    regular_season_games INT NOT NULL,
    postseason_games INT NOT NULL,
    notes TEXT,
    finalized_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every team's final line for a finished season.
CREATE TABLE IF NOT EXISTS team_season_results (
    season INT NOT NULL REFERENCES league_seasons(season) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id),
    wins INT NOT NULL,
    losses INT NOT NULL,
    ties INT NOT NULL,
    points_for INT NOT NULL,
    points_against INT NOT NULL,
    division_place SMALLINT NOT NULL CHECK (division_place BETWEEN 1 AND 4),
    division_tiebreak TEXT,                        -- the rule that placed it above a team with the same record, or UNBROKEN
    conference_seed SMALLINT CHECK (conference_seed BETWEEN 1 AND 7),   -- NULL: missed the playoffs
    playoff_result VARCHAR(16) NOT NULL CHECK (playoff_result IN
        ('MISSED', 'WILD_CARD', 'DIVISIONAL', 'CONFERENCE', 'SUPER_BOWL_LOSS', 'CHAMPION')),
    playoff_wins INT NOT NULL DEFAULT 0,
    playoff_losses INT NOT NULL DEFAULT 0,
    head_coach_id INT REFERENCES coaches(id) ON DELETE SET NULL,
    gm_id INT,                                     -- gm_profiles(id) when the GM agents migration is applied
    finalized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (season, team_id)
);

-- Each head coach's line for a finished season (one coach per team per season), from 2035.
CREATE TABLE IF NOT EXISTS coach_season_lines (
    coach_id INT NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id),
    season INT NOT NULL,
    wins INT NOT NULL,
    losses INT NOT NULL,
    ties INT NOT NULL,
    games INT NOT NULL,                            -- regular-season games coached
    playoff_wins INT NOT NULL DEFAULT 0,
    playoff_losses INT NOT NULL DEFAULT 0,
    playoff_result VARCHAR(16) NOT NULL,
    PRIMARY KEY (coach_id, team_id, season)
);
CREATE INDEX IF NOT EXISTS coach_season_lines_team ON coach_season_lines (team_id, season);

-- A coach with no record before he joined this league (a carousel hire, plan D6): his snapshot counts only his
-- seasons here; his earlier career isn't recorded and is never filled in.
ALTER TABLE coach_seasons ADD COLUMN IF NOT EXISTS mu_era_only BOOLEAN NOT NULL DEFAULT false;

COMMIT;
