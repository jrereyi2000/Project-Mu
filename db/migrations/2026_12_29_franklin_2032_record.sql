-- Migration: 2026_12_29_franklin_2032_record.sql
-- Ka'Von Franklin's 2032 line back to the record book (docs/done/HISTORY_BACKFILL_PLAN.md phase 3).
--
-- 2026_12_27_career_fit_fixes.sql trimmed his seeded 2032 line to 800 rushing yards to fit his Madden career, but
-- franchise_records holds his 2032 Carolina rookie record at 1,016 yards and Panthers Full History.pdf says "rushing
-- for 1016 yards and 8 touchdowns". 2032 goes to 190-1,016-8; 2033 stays 111-595-10 (the PDF: he led the
-- backfield with 10 TDs); 2034 (no regular-season game lines) takes the rest of his 2032-2034 career share,
-- 481-2,291-23: 180-680-5. Receiving unchanged. Averages and fantasy points (0.1 a yard, 6 a TD) follow. Every
-- UPDATE matches the old values, so a second run changes nothing.
--
-- Run ./db/backup_db.sh first. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_12_29_franklin_2032_record.sql

BEGIN;

UPDATE season_stats
SET rush_attempts = 190, rush_yards = 1016, rush_tds = 8, rush_avg = 5.3, rush_ypg = 59.8,
    total_fantasy_points = 158.80, ppg_fantasy_points = 9.34
WHERE id = 4133 AND player_id = 44 AND season = 2032 AND rush_attempts = 170 AND rush_yards = 800;

UPDATE season_stats
SET rush_attempts = 180, rush_yards = 680, rush_tds = 5, rush_avg = 3.8, rush_ypg = 40.0,
    total_fantasy_points = 166.00, ppg_fantasy_points = 9.76
WHERE player_id = 44 AND season = 2034 AND rush_attempts = 200 AND rush_yards = 896;

COMMIT;
