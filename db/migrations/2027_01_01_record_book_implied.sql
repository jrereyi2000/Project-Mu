-- Record-book rows the 2026-2034 history implies (docs/done/HISTORY_BACKFILL_PLAN.md §3.7; PROJECT_SPEC §3.3l).
-- `python3 -m engines.history_backfill records-check` found four franchise records the book's own facts create but
-- don't list (JR 2026-10-26: add them; Jeanty and Gore tie rather than break):
--   - Evan Smith's book rookie record of 18 receiving TDs (WAS 2029) also beats Washington's season record of 12.
--   - Ashton Jeanty's book 18 rushing TDs (LV 2032) tie the Raiders' 18 scrimmage TDs (Marcus Allen 1984).
--   - Ben Gore's book 15 rushing TDs (NYJ 2034) tie the Jets' 15 scrimmage TDs (Thomas Jones 2008).
--   - The stored 2034 Panthers games score 535 points, above the franchise's 500 (2015).
-- player_id stays NULL here: `python3 -m engines.history_backfill link-facts` links the players (Jeanty is a history
-- player whose id changes on a re-apply, and apply-players relinks).
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_01_01_record_book_implied.sql

BEGIN;

INSERT INTO franchise_records (team_id, category, value, holder, player_id, season, source)
SELECT t.id, v.category, v.value, v.holder, NULL, v.season, 'BOOK'
FROM (VALUES
    ('WAS', 'season_rec_tds', 18, 'Evan Smith', 2029),
    ('LV', 'season_scrimmage_tds', 18, 'Ashton Jeanty', 2032),
    ('NYJ', 'season_scrimmage_tds', 15, 'Ben Gore', 2034),
    ('CAR', 'season_team_points', 535, '2034 Carolina Panthers', 2034)
) AS v(abbr, category, value, holder, season)
JOIN teams t ON t.abbreviation = v.abbr
ON CONFLICT (team_id, category, holder, season) DO NOTHING;

COMMIT;
