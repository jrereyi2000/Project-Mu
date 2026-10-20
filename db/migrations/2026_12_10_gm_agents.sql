-- GM agents (docs/TEAM_MANAGEMENT_PLAN.md §2.4, docs/team_management/03_GM_AGENTS_PLAN.md §2.6; PROJECT_SPEC §3.3f).
-- One invented GM per team (persona traits, stable across seasons), the team's direction and needs per season,
-- draft-pick ownership, trade proposals and their assets (Mu's GM agents decide trades, JR makes them by hand in
-- Madden, D2/D3), and the trade rows the League Transactions scan sees (sightings; they never execute anything).
-- Needs steps 1 and 2 (2026_12_07_contracts.sql, 2026_12_08_transactions.sql).
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_12_10_gm_agents.sql

BEGIN;

-- One row per GM tenure (the coach_tenures shape). Traits are 0-1 except pick_affinity (0.6-1.4) and youth_bias
-- (-1 veterans .. +1 youth); position_bias = {group: multiplier}, at most 3 groups, each within +-15%.
-- Rows are never regenerated: replacing a GM closes the tenure (end_season) and seeds a new row.
CREATE TABLE IF NOT EXISTS gm_profiles (
    id SERIAL PRIMARY KEY,
    team_id INT NOT NULL REFERENCES teams(id),
    gm_name TEXT NOT NULL,
    bio TEXT,                                         -- short, invented (untracked narrative)
    voice TEXT,                                       -- style notes for the rationale writer
    bio_source VARCHAR(8) NOT NULL DEFAULT 'TEMPLATE' CHECK (bio_source IN ('LLM', 'TEMPLATE')),
    start_season INT NOT NULL CHECK (start_season >= 2030),   -- invented tenure start
    end_season INT,
    end_reason TEXT,                                  -- why the tenure closed (hot seat, by hand)
    risk_appetite NUMERIC(3,2) NOT NULL CHECK (risk_appetite BETWEEN 0 AND 1),
    aggressiveness NUMERIC(3,2) NOT NULL CHECK (aggressiveness BETWEEN 0 AND 1),
    pick_affinity NUMERIC(3,2) NOT NULL CHECK (pick_affinity BETWEEN 0.6 AND 1.4),
    youth_bias NUMERIC(3,2) NOT NULL CHECK (youth_bias BETWEEN -1 AND 1),
    position_bias JSONB NOT NULL DEFAULT '{}'::jsonb,
    seed TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS gm_profiles_open_tenure ON gm_profiles (team_id) WHERE end_season IS NULL;

-- Who runs each team (sub-plan §1.1 C9, §7 Q1): a user-controlled team's GM only advises JR, and JR can turn
-- its suggestions off per team. A team with no row is CPU-run with suggestions on.
CREATE TABLE IF NOT EXISTS gm_team_settings (
    team_id INT PRIMARY KEY REFERENCES teams(id),
    user_controlled BOOLEAN NOT NULL DEFAULT false,
    suggestions_on BOOLEAN NOT NULL DEFAULT true,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO gm_team_settings (team_id, user_controlled, suggestions_on)
SELECT id, true, true FROM teams WHERE abbreviation IN ('CAR', 'IND')
ON CONFLICT (team_id) DO NOTHING;

-- Direction and needs per team and season (engines/gm_direction.py), plus step 4's front-office quality columns
-- (q_team): step 3 creates them at 1.00 and never writes them after that.
CREATE TABLE IF NOT EXISTS gm_team_seasons (
    team_id INT NOT NULL REFERENCES teams(id),
    season INT NOT NULL,
    gm_id INT REFERENCES gm_profiles(id) ON DELETE SET NULL,
    direction VARCHAR(8) CHECK (direction IN ('CONTEND', 'RETOOL', 'REBUILD')),
    direction_score NUMERIC(6,3),
    direction_week INT,                               -- the checkpoint week the direction was last set
    direction_inputs JSONB,
    needs JSONB NOT NULL DEFAULT '[]'::jsonb,         -- ranked [{group, slot_gap, injury, expiring, score}]
    needs_week INT,
    surplus JSONB NOT NULL DEFAULT '[]'::jsonb,       -- [{group, player_ids}]
    urgent_need JSONB,                                -- injury trigger {group, player_id, weeks_remaining}
    fo_quality NUMERIC(4,2) NOT NULL DEFAULT 1.00 CHECK (fo_quality BETWEEN 0.5 AND 1.6),
    fo_quality_inputs JSONB,
    fo_quality_at TIMESTAMPTZ,
    hot_seat_score NUMERIC(6,3),                      -- offseason rollover (sub-plan §7 Q4)
    hot_seat_inputs JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, season)
);

-- Draft-pick ownership: every team's own picks for the next three drafts (sub-plan §1.1 C5), then trades.
-- overall_pick is filled by step 4 once the order is set.
CREATE TABLE IF NOT EXISTS draft_picks (
    draft_season INT NOT NULL,
    round SMALLINT NOT NULL CHECK (round BETWEEN 1 AND 7),
    original_team_id INT NOT NULL REFERENCES teams(id),
    owner_team_id INT NOT NULL REFERENCES teams(id),
    overall_pick SMALLINT,
    source VARCHAR(5) NOT NULL DEFAULT 'SEED' CHECK (source IN ('SEED', 'TRADE', 'JR')),
    acquired_proposal_id INT,                         -- FK added below, after gm_trade_proposals
    used BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (draft_season, round, original_team_id)
);
CREATE INDEX IF NOT EXISTS draft_picks_owner ON draft_picks (owner_team_id, draft_season);

-- Trade proposals (engines/gm_trade_engine.py) and trades JR recorded himself (origin USER).
-- PROPOSED -> APPROVED -> EXECUTED | REJECTED | EXPIRED. EXECUTED only when JR confirms on #debug.
CREATE TABLE IF NOT EXISTS gm_trade_proposals (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT NOT NULL,                                -- league_state.current_week when made (offseason 24, draft 25)
    trade_window VARCHAR(10) NOT NULL DEFAULT 'IN_SEASON' CHECK (trade_window IN ('IN_SEASON', 'OFFSEASON', 'DRAFT_DAY')),
    trigger_kind VARCHAR(8) NOT NULL DEFAULT 'NEED' CHECK (trigger_kind IN ('NEED', 'INJURY', 'SELL', 'DRAFT', 'USER')),
    origin VARCHAR(6) NOT NULL DEFAULT 'ENGINE' CHECK (origin IN ('ENGINE', 'USER')),
    expires_week INT,
    team_a_id INT NOT NULL REFERENCES teams(id),      -- initiator
    team_b_id INT NOT NULL REFERENCES teams(id),
    value_a BIGINT,                                   -- each side's surplus (dollars)
    value_b BIGINT,
    league_value_a BIGINT,                            -- neutral value of what each side receives
    league_value_b BIGINT,
    cap_space_before_a BIGINT,
    cap_space_before_b BIGINT,
    cap_space_after_a BIGINT,
    cap_space_after_b BIGINT,
    dead_money_a BIGINT,
    dead_money_b BIGINT,
    roster_after_a INT,
    roster_after_b INT,
    cap_derived_a BOOLEAN,
    cap_derived_b BOOLEAN,
    legality JSONB NOT NULL DEFAULT '{}'::jsonb,      -- {check: {ok, detail}}
    rationale_a TEXT,
    rationale_b TEXT,
    headline TEXT,
    rationale_source VARCHAR(8) CHECK (rationale_source IN ('LLM', 'TEMPLATE')),
    status VARCHAR(9) NOT NULL DEFAULT 'PROPOSED'
        CHECK (status IN ('PROPOSED', 'APPROVED', 'EXECUTED', 'REJECTED', 'EXPIRED')),
    status_reason TEXT,
    approved_at TIMESTAMPTZ,
    executed_at TIMESTAMPTZ,
    executed_as VARCHAR(9) CHECK (executed_as IN ('PROPOSED', 'JR_EDITED', 'SIGHTING')),
    rumor_ok BOOLEAN NOT NULL DEFAULT false,          -- step 5 may use a non-executed proposal as a rumor
    flags TEXT[] NOT NULL DEFAULT '{}',
    deal_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (season, deal_hash)
);
CREATE INDEX IF NOT EXISTS gm_trade_proposals_status ON gm_trade_proposals (season, status);

CREATE TABLE IF NOT EXISTS gm_trade_assets (
    id SERIAL PRIMARY KEY,
    proposal_id INT NOT NULL REFERENCES gm_trade_proposals(id) ON DELETE CASCADE,
    from_team_id INT NOT NULL REFERENCES teams(id),
    to_team_id INT NOT NULL REFERENCES teams(id),
    asset_type VARCHAR(6) NOT NULL CHECK (asset_type IN ('PLAYER', 'PICK')),
    player_id INT REFERENCES players(id) ON DELETE SET NULL,
    contract_id INT REFERENCES player_contracts(id) ON DELETE SET NULL,
    pick_draft_season INT,
    pick_round SMALLINT,
    pick_original_team_id INT REFERENCES teams(id),
    cap_hit_now BIGINT,
    value_from BIGINT,
    value_to BIGINT,
    value_league BIGINT
);
CREATE INDEX IF NOT EXISTS gm_trade_assets_proposal ON gm_trade_assets (proposal_id);
CREATE INDEX IF NOT EXISTS gm_trade_assets_player ON gm_trade_assets (player_id);

-- Trade rows the League Transactions scan read (pipeline/load_transactions.py skips them as moves). They are
-- matched to EXECUTED proposals (match_status / proposal_id only); "Apply this trade" on #debug records an
-- unmatched one as a USER proposal. Nothing here is ever applied automatically.
CREATE TABLE IF NOT EXISTS gm_trade_sightings (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    txn_date TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    parsed JSONB,
    capture_id INT REFERENCES captures(id) ON DELETE SET NULL,
    proposal_id INT REFERENCES gm_trade_proposals(id) ON DELETE SET NULL,
    match_status VARCHAR(9) NOT NULL DEFAULT 'UNMATCHED' CHECK (match_status IN ('MATCHED', 'PARTIAL', 'UNMATCHED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (season, txn_date, raw_text)
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'draft_picks_acquired_proposal_fk') THEN
        ALTER TABLE draft_picks ADD CONSTRAINT draft_picks_acquired_proposal_fk
            FOREIGN KEY (acquired_proposal_id) REFERENCES gm_trade_proposals(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'transactions_gm_proposal_fk') THEN
        ALTER TABLE transactions ADD CONSTRAINT transactions_gm_proposal_fk
            FOREIGN KEY (gm_proposal_id) REFERENCES gm_trade_proposals(id) ON DELETE SET NULL;
    END IF;
END $$;

-- Master §2.4's single "GM profile": the open tenure, this season's direction and needs, and who runs the team.
CREATE OR REPLACE VIEW vw_gm_profiles AS
SELECT g.id AS gm_id,
       g.team_id,
       t.abbreviation,
       t.city,
       t.name AS team_name,
       g.gm_name,
       g.bio,
       g.voice,
       g.bio_source,
       g.start_season,
       g.risk_appetite,
       g.aggressiveness,
       g.pick_affinity,
       g.youth_bias,
       g.position_bias,
       ls.current_season AS season,
       s.direction,
       s.direction_score,
       s.direction_week,
       s.needs,
       s.surplus,
       s.urgent_need,
       s.fo_quality,
       s.fo_quality_inputs,
       s.fo_quality_at,
       COALESCE(st.user_controlled, false) AS user_controlled,
       COALESCE(st.suggestions_on, true) AS suggestions_on
FROM gm_profiles g
JOIN teams t ON t.id = g.team_id
CROSS JOIN league_state ls
LEFT JOIN gm_team_seasons s ON s.team_id = g.team_id AND s.season = ls.current_season
LEFT JOIN gm_team_settings st ON st.team_id = g.team_id
WHERE g.end_season IS NULL AND ls.id = 1;

COMMIT;
