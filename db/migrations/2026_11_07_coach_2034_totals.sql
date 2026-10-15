-- Coach career totals at the end of 2034, from Madden's coach screen (all 32 active coaches, captured at
-- 2035 Week 18 before the Week 18 games except Carolina's) minus each team's 2035 regular-season record at that point.
-- Winning seasons only count finished seasons and there were no 2035 playoff games yet, so those columns are taken
-- as shown. From here on `coach_records` adds every later game to these rows (PROJECT_SPEC §3.3a).
-- Checks that held when this was written: every 2026 coach still with his team has exactly 153 games (9 x 17) between
-- the 2025 baseline and the end of 2034 (except Saleh and Bowles, whose Madden careers start from different numbers
-- than the real ones), and every coach's Super Bowl record matches the Super Bowls in `games`.
-- Also: the Broncos' and Chiefs' coaches are P. Gaffney and R. Leonard (169 games each = every game since 2026, no
-- earlier career), not Sean Payton and Andy Reid, who are removed. Their schemes and pass/blitz/man tendencies stay
-- with the team (playbooks haven't changed since 2026); fourth-down aggressiveness, a head coach's own call, is Medium.
-- Run once: psql -f db/migrations/2026_11_07_coach_2034_totals.sql
BEGIN;

INSERT INTO coaches (coach_name, offensive_scheme, defensive_scheme, pass_lean, fourth_down_aggressiveness, blitz_rate, man_coverage)
SELECT v.new_name, c.offensive_scheme, c.defensive_scheme, c.pass_lean, 'Medium', c.blitz_rate, c.man_coverage
FROM (VALUES ('P. Gaffney', 'Sean Payton'), ('R. Leonard', 'Andy Reid')) AS v(new_name, old_name)
JOIN coaches c ON c.coach_name = v.old_name
ON CONFLICT (coach_name) DO NOTHING;

UPDATE coach_tenures ct SET coach_id = n.id
FROM (VALUES ('P. Gaffney', 'Sean Payton'), ('R. Leonard', 'Andy Reid')) AS v(new_name, old_name)
JOIN coaches o ON o.coach_name = v.old_name JOIN coaches n ON n.coach_name = v.new_name
WHERE ct.coach_id = o.id;

-- Payton and Reid never coached in this franchise (cascades to their 2025 baseline rows and COTY scores; the COTY
-- engine rewrites the season on its next run).
DELETE FROM coaches WHERE coach_name IN ('Sean Payton', 'Andy Reid');

CREATE TEMP TABLE screen (team TEXT, cw INT, cl INT, ct INT, w35 INT, l35 INT, t35 INT, ws INT, pw INT, pl INT,
                          sbw INT, sbl INT) ON COMMIT DROP;
INSERT INTO screen VALUES
    ('Giants', 285, 177, 0, 11, 5, 0, 20, 25, 18, 1, 2),  -- J. Harbaugh
    ('Steelers', 257, 198, 2, 9, 7, 0, 15, 14, 13, 1, 0),  -- M. McCarthy
    ('Rams', 188, 130, 0, 9, 7, 0, 16, 15, 12, 2, 1),  -- S. McVay
    ('Patriots', 165, 120, 0, 10, 6, 0, 11, 7, 9, 0, 1),  -- M. Vrabel
    ('Eagles', 160, 94, 0, 3, 13, 0, 13, 14, 10, 2, 1),  -- N. Sirianni
    ('49ers', 159, 159, 0, 8, 8, 0, 8, 12, 8, 0, 2),  -- K. Shanahan
    ('Lions', 155, 110, 1, 8, 8, 0, 10, 13, 8, 1, 1),  -- D. Campbell
    ('Chargers', 151, 115, 1, 7, 9, 0, 9, 7, 8, 0, 1),  -- J. Harbaugh
    ('Buccaneers', 151, 154, 0, 8, 8, 0, 5, 5, 8, 0, 0),  -- T. Bowles
    ('Packers', 141, 144, 1, 5, 11, 0, 8, 3, 8, 0, 0),  -- M. LaFleur
    ('Bengals', 137, 147, 1, 5, 11, 0, 10, 8, 6, 0, 2),  -- Z. Taylor
    ('Falcons', 131, 139, 0, 6, 10, 0, 7, 4, 6, 0, 1),  -- K. Stefanski
    ('Commanders', 123, 165, 0, 7, 9, 0, 4, 5, 3, 0, 1),  -- D. Quinn
    ('Colts', 121, 99, 0, 5, 11, 0, 9, 6, 7, 0, 0),  -- S. Steichen
    ('Vikings', 116, 121, 0, 11, 5, 0, 5, 0, 3, 0, 0),  -- K. O''Connell
    ('Texans', 113, 107, 0, 12, 4, 0, 5, 5, 7, 0, 0),  -- D. Ryans
    ('Seahawks', 112, 91, 0, 9, 7, 0, 6, 6, 4, 1, 0),  -- M. Macdonald
    ('Broncos', 102, 67, 0, 13, 3, 0, 7, 7, 6, 1, 0),  -- P. Gaffney
    ('Bears', 98, 88, 0, 7, 9, 0, 6, 2, 5, 0, 0),  -- B. Johnson
    ('Cowboys', 98, 87, 1, 9, 7, 0, 6, 0, 3, 0, 0),  -- B. Schottenheimer
    ('Raiders', 95, 74, 0, 9, 7, 0, 7, 1, 5, 0, 0),  -- K. Kubiak
    ('Titans', 91, 78, 0, 8, 8, 0, 6, 10, 4, 1, 1),  -- R. Saleh
    ('Cardinals', 90, 79, 0, 8, 8, 0, 5, 5, 4, 1, 0),  -- M. LaFleur
    ('Dolphins', 90, 79, 0, 8, 8, 0, 7, 6, 5, 0, 2),  -- J. Hafley
    ('Browns', 85, 84, 0, 4, 12, 0, 5, 1, 5, 0, 0),  -- T. Monken
    ('Chiefs', 84, 85, 0, 7, 9, 0, 1, 1, 3, 0, 0),  -- R. Leonard
    ('Saints', 83, 103, 0, 4, 12, 0, 4, 1, 3, 0, 0),  -- K. Moore
    ('Ravens', 82, 87, 0, 7, 9, 0, 5, 8, 1, 2, 0),  -- J. Minter
    ('Bills', 77, 92, 0, 8, 8, 0, 5, 3, 4, 0, 0),  -- J. Brady
    ('Jets', 72, 114, 0, 7, 9, 0, 2, 3, 2, 0, 1),  -- A. Glenn
    ('Jaguars', 69, 117, 0, 8, 8, 0, 1, 0, 1, 0, 0),  -- L. Coen
    ('Panthers', 30, 4, 0, 17, 0, 0, 1, 3, 0, 1, 0);  -- M. Whitfield

INSERT INTO coach_seasons (coach_id, team_id, season, career_wins, career_losses, career_ties, winning_seasons,
                           playoff_wins, playoff_losses, super_bowl_wins, super_bowl_losses)
SELECT ct.coach_id, t.id, 2034, s.cw - s.w35, s.cl - s.l35, s.ct - s.t35, s.ws, s.pw, s.pl, s.sbw, s.sbl
FROM screen s
JOIN teams t ON t.name = s.team
JOIN coach_tenures ct ON ct.team_id = t.id AND ct.start_season <= 2035 AND coalesce(ct.end_season, 9999) >= 2035
ON CONFLICT (coach_id, season) DO UPDATE SET
    team_id = EXCLUDED.team_id, career_wins = EXCLUDED.career_wins, career_losses = EXCLUDED.career_losses,
    career_ties = EXCLUDED.career_ties, winning_seasons = EXCLUDED.winning_seasons,
    playoff_wins = EXCLUDED.playoff_wins, playoff_losses = EXCLUDED.playoff_losses,
    super_bowl_wins = EXCLUDED.super_bowl_wins, super_bowl_losses = EXCLUDED.super_bowl_losses;

DO $$ BEGIN
    IF (SELECT count(*) FROM coach_seasons WHERE season = 2034) <> 32
    THEN RAISE EXCEPTION 'expected end-of-2034 totals for 32 coaches'; END IF;
END $$;

-- Each coach's current career totals: the latest stored snapshot (coach_seasons, totals at the end of that season)
-- plus every completed game his team played in later seasons while he held the job (coach_tenures; one head coach
-- per team per season). A winning season counts once the team's 17-game regular season is complete.
CREATE OR REPLACE VIEW coach_records AS
WITH snap AS (
    SELECT DISTINCT ON (coach_id) * FROM coach_seasons ORDER BY coach_id, season DESC
), team_games AS (
    SELECT g.season, g.game_type, t.team_id,
           CASE WHEN t.score > t.opp THEN 1 ELSE 0 END AS win,
           CASE WHEN t.score < t.opp THEN 1 ELSE 0 END AS loss,
           CASE WHEN t.score = t.opp THEN 1 ELSE 0 END AS tie
    FROM games g
    CROSS JOIN LATERAL (VALUES (g.home_team_id, g.home_score, g.away_score),
                               (g.away_team_id, g.away_score, g.home_score)) AS t(team_id, score, opp)
    WHERE g.status = 'COMPLETED'
), coached AS (
    SELECT s.coach_id, tg.*
    FROM snap s
    JOIN coach_tenures ct ON ct.coach_id = s.coach_id
    JOIN team_games tg ON tg.team_id = ct.team_id AND tg.season > s.season
                      AND tg.season >= ct.start_season AND tg.season <= coalesce(ct.end_season, 9999)
), reg AS (
    SELECT coach_id, season, sum(win) AS w, sum(loss) AS l, sum(tie) AS t, count(*) AS games
    FROM coached WHERE game_type = 'REGULAR_SEASON' GROUP BY coach_id, season
), add AS (
    SELECT c.coach_id,
           sum(win) FILTER (WHERE game_type = 'REGULAR_SEASON') AS w,
           sum(loss) FILTER (WHERE game_type = 'REGULAR_SEASON') AS l,
           sum(tie) FILTER (WHERE game_type = 'REGULAR_SEASON') AS t,
           sum(win) FILTER (WHERE game_type <> 'REGULAR_SEASON') AS pw,
           sum(loss) FILTER (WHERE game_type <> 'REGULAR_SEASON') AS pl,
           sum(win) FILTER (WHERE game_type = 'SUPER_BOWL') AS sbw,
           sum(loss) FILTER (WHERE game_type = 'SUPER_BOWL') AS sbl
    FROM coached c GROUP BY c.coach_id
)
SELECT c.id AS coach_id, c.coach_name, s.season AS snapshot_season,
       s.career_wins + coalesce(a.w, 0) AS career_wins,
       s.career_losses + coalesce(a.l, 0) AS career_losses,
       s.career_ties + coalesce(a.t, 0) AS career_ties,
       s.winning_seasons + (SELECT count(*) FROM reg r WHERE r.coach_id = c.id AND r.games >= 17 AND r.w > r.l)
           AS winning_seasons,
       s.playoff_wins + coalesce(a.pw, 0) AS playoff_wins,
       s.playoff_losses + coalesce(a.pl, 0) AS playoff_losses,
       s.super_bowl_wins + coalesce(a.sbw, 0) AS super_bowl_wins,
       s.super_bowl_losses + coalesce(a.sbl, 0) AS super_bowl_losses
FROM coaches c
JOIN snap s ON s.coach_id = c.id
LEFT JOIN add a ON a.coach_id = c.id;

COMMIT;
