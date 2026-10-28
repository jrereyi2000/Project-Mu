-- Commentary rename player name aliases (docs/PRE_PC_WORK.md §2; docs/PC_MIGRATION_PLAN.md §4.3)
-- Records historical/original names when a player's surname is renamed to match Madden commentary.
-- Dry-run by default; apply on madden_test first.

BEGIN;

CREATE TABLE IF NOT EXISTS player_name_aliases (
    player_id INT REFERENCES players(id) ON DELETE CASCADE,
    alias VARCHAR(128) NOT NULL,
    kind VARCHAR(32) NOT NULL DEFAULT 'commentary_rename',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (player_id, alias)
);

CREATE INDEX IF NOT EXISTS idx_player_name_aliases_alias ON player_name_aliases(alias);

COMMIT;
