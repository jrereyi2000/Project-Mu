-- Napper season values are always supplied from league_state.current_season.
-- Removing defaults prevents new leagues and drafts from inheriting a stale season.
ALTER TABLE napper_leagues ALTER COLUMN season DROP DEFAULT;
ALTER TABLE napper_drafts ALTER COLUMN season DROP DEFAULT;
