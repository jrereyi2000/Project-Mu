-- The write-time fact gate's verdict, stored beside the article it judged
-- (docs/in_progress/ARTICLE_FACT_GATE_PLAN.md §2.4, U3).
--
-- Every live ARTICLE is now judged claim by claim against the evidence its writer actually retrieved
-- (newsroom/fact_gate.py, called from `SwarmDispatcher._execute_agent`) before it reaches news_feed. The
-- verdict is one of:
--   clean      the first check found no claim that contradicts the evidence.
--   rewritten  a correction pass ran and the kept version has no contradiction.
--   flagged    the article still contradicts its evidence (or the rewrite returned nothing): it is stored
--              and visible, and this column is how the reading view and editor list find it.
--   skipped    the gate did not judge it: a live run with the gate switched off (MU_NO_LLM or
--              FACT_GATE_ARTICLES=0), no article text, or a checker outage (two failed attempts).
-- A mock run (`live=False`) never runs the gate and stores NULL, which means "not checked", never
-- "clean".
-- `fact_gate` keeps the small JSON payload: the checker model, the verdict counts, the contradicted claims
-- with their evidence quotes, one entry per check, and the kept/discarded contradiction counts. The article
-- text is never stored in it.
--
-- No backfill: rows written before this migration keep NULL, which means "not checked", never "clean".
-- Idempotent, and a replay changes nothing. Run ./db/backup_db.sh first. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2027_02_30_fact_gate.sql

BEGIN;

-- NULL (not checked) passes the check, so old rows need no rewrite.
ALTER TABLE news_feed ADD COLUMN IF NOT EXISTS fact_gate_status VARCHAR(16);
ALTER TABLE news_feed DROP CONSTRAINT IF EXISTS news_feed_fact_gate_status_check;
ALTER TABLE news_feed ADD CONSTRAINT news_feed_fact_gate_status_check
    CHECK (fact_gate_status IN ('clean', 'rewritten', 'flagged', 'skipped'));

ALTER TABLE news_feed ADD COLUMN IF NOT EXISTS fact_gate JSONB;

-- The one query a reader of flagged pieces makes: this week's flagged articles.
CREATE INDEX IF NOT EXISTS news_feed_fact_gate ON news_feed (season, week) WHERE fact_gate_status = 'flagged';

COMMIT;
