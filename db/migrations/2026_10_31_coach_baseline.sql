-- 2026 head coaches (all 32 teams; Carolina's is Dave Canales, who coached through 2033) and their real NFL
-- head-coaching careers through 2025, stored as coach_seasons season 2025 with team_id NULL: the baseline the
-- franchise started from (PROJECT_SPEC §3.3a). Sources: Wikipedia "List of current NFL head coaches" (revision of
-- 2026-07-12, records as of the end of 2025) and each coach's year-by-year record (winning seasons, Super Bowls).
-- Run once after 2026_10_30_coaches.sql: psql -f db/migrations/2026_10_31_coach_baseline.sql
BEGIN;

CREATE TEMP TABLE baseline (city TEXT, team TEXT, coach_name TEXT, w INT, l INT, t INT, winning INT,
                            pw INT, pl INT, sbw INT, sbl INT) ON COMMIT DROP;
INSERT INTO baseline VALUES
    ('Arizona', 'Cardinals', 'Mike LaFleur', 0, 0, 0, 0, 0, 0, 0, 0),
    ('Atlanta', 'Falcons', 'Kevin Stefanski', 45, 56, 0, 2, 1, 2, 0, 0),
    ('Baltimore', 'Ravens', 'Jesse Minter', 0, 0, 0, 0, 0, 0, 0, 0),
    ('Buffalo', 'Bills', 'Joe Brady', 0, 0, 0, 0, 0, 0, 0, 0),
    ('Carolina', 'Panthers', 'Dave Canales', 13, 21, 0, 0, 0, 1, 0, 0),
    ('Chicago', 'Bears', 'Ben Johnson', 11, 6, 0, 1, 1, 1, 0, 0),
    ('Cincinnati', 'Bengals', 'Zac Taylor', 52, 63, 1, 4, 5, 2, 0, 1),
    ('Cleveland', 'Browns', 'Todd Monken', 0, 0, 0, 0, 0, 0, 0, 0),
    ('Dallas', 'Cowboys', 'Brian Schottenheimer', 7, 9, 1, 0, 0, 0, 0, 0),
    ('Denver', 'Broncos', 'Sean Payton', 184, 108, 0, 12, 10, 10, 1, 0),
    ('Detroit', 'Lions', 'Dan Campbell', 53, 43, 1, 4, 2, 2, 0, 0),
    ('Green Bay', 'Packers', 'Matt LaFleur', 76, 40, 1, 6, 3, 6, 0, 0),
    ('Houston', 'Texans', 'DeMeco Ryans', 32, 19, 0, 3, 3, 3, 0, 0),
    ('Indianapolis', 'Colts', 'Shane Steichen', 25, 26, 0, 1, 0, 0, 0, 0),
    ('Jacksonville', 'Jaguars', 'Liam Coen', 13, 4, 0, 1, 0, 1, 0, 0),
    ('Kansas City', 'Chiefs', 'Andy Reid', 279, 157, 1, 21, 28, 17, 3, 3),
    ('Las Vegas', 'Raiders', 'Klint Kubiak', 0, 0, 0, 0, 0, 0, 0, 0),
    ('Los Angeles', 'Chargers', 'Jim Harbaugh', 66, 31, 1, 5, 5, 5, 0, 1),
    ('Los Angeles', 'Rams', 'Sean McVay', 92, 57, 0, 8, 10, 6, 1, 1),
    ('Miami', 'Dolphins', 'Jeff Hafley', 0, 0, 0, 0, 0, 0, 0, 0),
    ('Minnesota', 'Vikings', 'Kevin O''Connell', 43, 25, 0, 3, 0, 2, 0, 0),
    ('New England', 'Patriots', 'Mike Vrabel', 68, 48, 0, 5, 5, 4, 0, 1),
    ('New Orleans', 'Saints', 'Kellen Moore', 6, 11, 0, 0, 0, 0, 0, 0),
    ('New York', 'Giants', 'John Harbaugh', 180, 113, 0, 13, 13, 11, 1, 0),
    ('New York', 'Jets', 'Aaron Glenn', 3, 14, 0, 0, 0, 0, 0, 0),
    ('Philadelphia', 'Eagles', 'Nick Sirianni', 59, 26, 0, 5, 6, 4, 1, 1),
    ('Pittsburgh', 'Steelers', 'Mike McCarthy', 174, 112, 2, 12, 11, 11, 1, 0),
    ('San Francisco', '49ers', 'Kyle Shanahan', 82, 67, 0, 5, 9, 5, 0, 2),
    ('Seattle', 'Seahawks', 'Mike Macdonald', 24, 10, 0, 2, 3, 0, 1, 0),
    ('Tampa Bay', 'Buccaneers', 'Todd Bowles', 61, 74, 0, 4, 1, 3, 0, 0),
    ('Tennessee', 'Titans', 'Robert Saleh', 20, 36, 0, 0, 0, 0, 0, 0),
    ('Washington', 'Commanders', 'Dan Quinn', 60, 59, 0, 3, 5, 3, 0, 1);

INSERT INTO coaches (coach_name) SELECT coach_name FROM baseline ON CONFLICT (coach_name) DO NOTHING;

-- Every coach except Whitfield has had the same team since 2026 (Canales's 2026–2033 tenure already exists).
INSERT INTO coach_tenures (coach_id, team_id, start_season)
SELECT c.id, tm.id, 2026
FROM baseline b JOIN coaches c ON c.coach_name = b.coach_name JOIN teams tm ON tm.city = b.city AND tm.name = b.team
WHERE NOT EXISTS (SELECT 1 FROM coach_tenures ct WHERE ct.coach_id = c.id)
ON CONFLICT DO NOTHING;

INSERT INTO coach_seasons (coach_id, team_id, season, career_wins, career_losses, career_ties, winning_seasons,
                           playoff_wins, playoff_losses, super_bowl_wins, super_bowl_losses)
SELECT c.id, NULL, 2025, b.w, b.l, b.t, b.winning, b.pw, b.pl, b.sbw, b.sbl
FROM baseline b JOIN coaches c ON c.coach_name = b.coach_name
ON CONFLICT (coach_id, season) DO UPDATE SET
    career_wins = EXCLUDED.career_wins, career_losses = EXCLUDED.career_losses, career_ties = EXCLUDED.career_ties,
    winning_seasons = EXCLUDED.winning_seasons, playoff_wins = EXCLUDED.playoff_wins,
    playoff_losses = EXCLUDED.playoff_losses, super_bowl_wins = EXCLUDED.super_bowl_wins,
    super_bowl_losses = EXCLUDED.super_bowl_losses;

-- Every team has exactly one 2026 head coach.
DO $$ BEGIN
    IF (SELECT count(DISTINCT team_id) FROM coach_tenures WHERE start_season <= 2026 AND coalesce(end_season, 9999) >= 2026) <> 32
       OR EXISTS (SELECT team_id FROM coach_tenures WHERE start_season <= 2026 AND coalesce(end_season, 9999) >= 2026
                  GROUP BY team_id HAVING count(*) > 1)
    THEN RAISE EXCEPTION 'expected one 2026 head coach per team'; END IF;
END $$;

COMMIT;
