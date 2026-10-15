-- Defensive and kicking season records (PROJECT_SPEC §2.9). engines/records_engine.py reads them from the Season Stats
-- capture (season_stat_lines): sacks, interceptions, tackles, defensive TDs, field goals made and longest field goal.
-- Madden lists only each category's leaders, so a player without a listed line has unknown numbers, never a zero.
-- `season_sacks` already exists (2026_10_28_league_record_book.sql). Like it, the new categories are
-- `coverage = 'BOOK'`: no real NFL history is seeded, so the engine measures a season only against what the record
-- book and earlier league seasons hold.
--
-- Run ./db/backup_db.sh first, after 2026_10_28_league_record_book.sql. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_11_12_defense_kicking_records.sql

BEGIN;

INSERT INTO record_categories (category, label, scope, subject, better, sort_order, coverage) VALUES
    ('season_def_interceptions', 'Interceptions in a season', 'SEASON', 'PLAYER', 'HIGH', 37, 'BOOK'),
    ('season_tackles', 'Tackles in a season', 'SEASON', 'PLAYER', 'HIGH', 38, 'BOOK'),
    ('season_def_tds', 'Defensive TDs in a season', 'SEASON', 'PLAYER', 'HIGH', 39, 'BOOK'),
    ('season_fgm', 'Field goals made in a season', 'SEASON', 'PLAYER', 'HIGH', 40, 'BOOK'),
    ('season_fg_long', 'Longest field goal in a season', 'SEASON', 'PLAYER', 'HIGH', 41, 'BOOK')
ON CONFLICT (category) DO UPDATE SET label = EXCLUDED.label, scope = EXCLUDED.scope, subject = EXCLUDED.subject,
    better = EXCLUDED.better, sort_order = EXCLUDED.sort_order, coverage = EXCLUDED.coverage;

COMMIT;
