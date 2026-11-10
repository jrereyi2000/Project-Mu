-- Two players can share an abbreviated box-score name and the same position — Javonte Williams
-- and Josh Williams are both "J. Williams" at HB — and Madden has them both in a game when their
-- teams meet. The unique key was (game_id, player_name, position) alone, so the second man's
-- line was refused with a duplicate-key error and a whole tuning season failed to ingest
-- (2026-11-10, madden_tune_p2g, game 77).
--
-- The team is part of the key. The loader's own rows are also covered by the partial
-- (game_id, player_id) index, which stays as it is; the OCR capture path upserts on this key.
ALTER TABLE player_stats DROP CONSTRAINT IF EXISTS player_stats_game_id_player_name_position_key;
CREATE UNIQUE INDEX IF NOT EXISTS player_stats_game_name_position_team
    ON player_stats (game_id, player_name, position, team_id);
