-- The ids behind a JC-2A hire-away (chunk-6 #13; PROJECT_SPEC §2.17g / §3.3c).
-- `compensatory_pick_awards.source_coach_id` could never be written: the journal recorded only the departing
-- person's name, so the producer had no id to store and every JC-2A award carried NULL. The carousel knows all
-- three ids at the moment it journals the departure (the reviewed registry's own kind/key, and the head-coach row
-- the hire created), so the journal keeps them and the producer can name the coach without inferring anything.
--
-- Apply with:
--   python3 -m db.migrate apply --file 2027_02_15_jc2a_source_ids.sql

BEGIN;

ALTER TABLE staff_development_departures
  ADD COLUMN IF NOT EXISTS person_kind TEXT,               -- the reviewed registry's kind: COACH | COORDINATOR | EXECUTIVE
  ADD COLUMN IF NOT EXISTS person_key TEXT,                -- its key (a db row id, or JR's reviewed key)
  ADD COLUMN IF NOT EXISTS hire_coach_id INT REFERENCES coaches(id);  -- the head-coach row the hire created

-- Rows journaled before this migration keep NULLs: the person is only known by name, and a name cannot be turned
-- into an id without guessing.

COMMIT;
