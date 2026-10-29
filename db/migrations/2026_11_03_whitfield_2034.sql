-- @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history data)
-- @REPLACED-BY: db/seed_clean_db.py + db/init_db.sql (old-universe data only; AUDIT_LEDGER.md §3b)
-- Marcus Whitfield's career counters after his first season (2034, Carolina): 13-4, #1 seed with a bye,
-- 3-0 in the playoffs and the Super Bowl win over the Jets (48-27). No head-coaching career before 2034.
-- Per JR (Oct 14); the 2034 regular-season and earlier playoff games aren't in `games`. PROJECT_SPEC §3.3a.
-- Run once: psql -f db/migrations/2026_11_03_whitfield_2034.sql
INSERT INTO coach_seasons (coach_id, team_id, season, career_wins, career_losses, career_ties, winning_seasons,
                           playoff_wins, playoff_losses, super_bowl_wins, super_bowl_losses)
SELECT c.id, t.id, 2034, 13, 4, 0, 1, 3, 0, 1, 0
FROM coaches c JOIN teams t ON t.city = 'Carolina' AND t.name = 'Panthers'
WHERE c.coach_name = 'Marcus Whitfield'
ON CONFLICT (coach_id, season) DO UPDATE SET
    team_id = EXCLUDED.team_id, career_wins = EXCLUDED.career_wins, career_losses = EXCLUDED.career_losses,
    career_ties = EXCLUDED.career_ties, winning_seasons = EXCLUDED.winning_seasons,
    playoff_wins = EXCLUDED.playoff_wins, playoff_losses = EXCLUDED.playoff_losses,
    super_bowl_wins = EXCLUDED.super_bowl_wins, super_bowl_losses = EXCLUDED.super_bowl_losses;
