-- Agent Wire posts store the sampled post shape, so social_writer.pick_shape can avoid an account's last three
-- shapes exactly instead of inferring them from the post text (AGENT_WIRE_VOICE_PLAN U4 deviation; PROJECT_SPEC
-- §3.5, §7.8). Idempotent; null for rows written before this migration and for articles.
--
-- Apply with:
--   python3 -m db.migrate apply --file 2027_02_10_news_feed_post_shape.sql

BEGIN;

ALTER TABLE IF EXISTS news_feed ADD COLUMN IF NOT EXISTS post_shape VARCHAR(32);

COMMIT;
