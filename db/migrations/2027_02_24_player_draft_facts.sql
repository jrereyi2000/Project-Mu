-- Madden's own draft facts on every player (TUNING_ACCEPTANCE_THRESHOLDS.md §7 decision 10, P3-07).
--
-- The tuning gates' "drafted" cohort (A1 career length, A2 retirement age) used to come from
-- draft_prospects.actual_pick / player_season_transitions DRAFTED, which only ever described players Mu drafted
-- itself: loaded veterans had no draft info at all, so both gates read NOT MEASURED until Mu's own draftees
-- retired. Madden's Player record carries the facts for every player, veterans included:
--
--   PLYR_DRAFTROUND  1-7 drafted, 63 undrafted (0 = unset)
--   PLYR_DRAFTPICK   the overall slot, 1-256; 511 = none (undrafted players carry a 257+ UDFA order here)
--   PLYR_DRAFTTEAM   a 1-based TeamIndex (PLYR_DRAFTTEAM - 1 = Team.team_index); 0 = no draft team, which
--                    Madden stores for every real-NFL veteran
--
-- pipeline/save_reader/pc_save_reader.mjs (reader_version 9) normalizes the sentinels to NULL and
-- pipeline/load_save.py stores them here: on insert, and filled into an existing row while it is still NULL so
-- the old rows loaded before the reader change pick the facts up (an already-set value is never overwritten).
ALTER TABLE players ADD COLUMN IF NOT EXISTS draft_round SMALLINT;      -- PLYR_DRAFTROUND 1-7; NULL = undrafted/unknown
ALTER TABLE players ADD COLUMN IF NOT EXISTS draft_pick SMALLINT;       -- PLYR_DRAFTPICK 1-256; NULL = none
ALTER TABLE players ADD COLUMN IF NOT EXISTS draft_team_id INT REFERENCES teams(id) ON DELETE SET NULL;
