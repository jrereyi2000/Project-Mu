-- 2027_01_13_franchise_identity.sql
-- Each Madden franchise has its own database (db/franchises.py). The franchise's Franchise.LeagueID from the save is
-- stamped here at seed time, so pipeline/watch_save.py can refuse to load a save into another franchise's database.
ALTER TABLE league_state ADD COLUMN IF NOT EXISTS madden_league_id BIGINT;
