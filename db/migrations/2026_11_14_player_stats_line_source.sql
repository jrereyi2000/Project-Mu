-- Where each per-game player line came from (PROJECT_SPEC §3.3): CAPTURED = a captured box score
-- (pipeline/load_capture.py games mode); WEEKLY = the backfill filled the team's only missing game, so the line is that
-- week's exact change in the captured season totals; DISTRIBUTED = the backfill spread the remainder over several
-- missing games, so the line is synthetic. Player splits (engines/player_splits.py) use CAPTURED and WEEKLY only.
-- Existing rows: games with captured category lines are CAPTURED (2035 Week 17), the rest DISTRIBUTED (the backlog
-- was spread across many games before weekly captures began).
-- Run once: psql -f db/migrations/2026_11_14_player_stats_line_source.sql
BEGIN;
ALTER TABLE player_stats ADD COLUMN IF NOT EXISTS line_source VARCHAR(12)
    CHECK (line_source IN ('CAPTURED', 'WEEKLY', 'DISTRIBUTED'));
UPDATE player_stats ps SET line_source = CASE
        WHEN EXISTS (SELECT 1 FROM game_stat_general x WHERE x.game_id = ps.game_id)
          OR EXISTS (SELECT 1 FROM game_stat_defense x WHERE x.game_id = ps.game_id) THEN 'CAPTURED'
        ELSE 'DISTRIBUTED' END
WHERE line_source IS NULL;
COMMIT;
