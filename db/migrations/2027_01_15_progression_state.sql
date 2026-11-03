-- Authoritative Mu base ratings and verified progression accounting.
-- Keep player_progression_history as the compatible annual summary.
BEGIN;
CREATE TABLE IF NOT EXISTS progression_player_state (
    player_id INT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    application_season INT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    base_ratings JSONB NOT NULL,
    band JSONB NOT NULL,
    source_fingerprint TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS progression_events (
    event_id TEXT PRIMARY KEY,
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    earned_season INT NOT NULL,
    application_season INT NOT NULL,
    kind TEXT NOT NULL,
    ordinal TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    base_revision BIGINT NOT NULL,
    candidate_base JSONB NOT NULL,
    candidate_band JSONB NOT NULL,
    overlay JSONB NOT NULL DEFAULT '{}'::jsonb,
    annual_deltas JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_purpose TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'PREPARED' CHECK (status IN ('PREPARED', 'APPLIED')),
    output_fingerprint TEXT,
    edits_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_at TIMESTAMPTZ,
    UNIQUE (player_id, earned_season, kind, ordinal, rules_version)
);
CREATE TABLE IF NOT EXISTS progression_annual_accounting (
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    earned_season INT NOT NULL,
    applied JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (player_id, earned_season)
);
CREATE TABLE IF NOT EXISTS progression_evidence_consumption (
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    earned_season INT NOT NULL,
    purpose TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES progression_events(event_id),
    PRIMARY KEY (player_id, earned_season, purpose, evidence_id)
);
CREATE INDEX IF NOT EXISTS progression_events_player_season
    ON progression_events (player_id, earned_season, status);
COMMIT;
