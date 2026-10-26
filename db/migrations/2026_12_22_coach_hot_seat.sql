-- Coach hot seat (engines/coach_hot_seat.py; PROJECT_SPEC §3.3a, §2.19). On Black Monday (the Week 18 run, once the
-- regular season is complete and the Wild Card matchups are loaded) Mu scores every head coach and fires up to a
-- handful of CPU-run teams' coaches; JR makes the same change in Madden, and the replacement comes in through the
-- offseason manifest's coaching_moves. One row per team and season; fired rows close the coach's tenure at that season.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_12_22_coach_hot_seat.sql

BEGIN;

CREATE TABLE IF NOT EXISTS coach_hot_seat (
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    coach_id INT NOT NULL REFERENCES coaches(id),
    score NUMERIC(6,3) NOT NULL,
    fired BOOLEAN NOT NULL DEFAULT false,
    inputs JSONB NOT NULL,                            -- the score's terms, eligibility, p and the seeded draw
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (season, team_id)
);

COMMIT;
