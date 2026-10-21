-- Trade negotiations with the GM agents (engines/gm_negotiation.py; PROJECT_SPEC §2.17, §3.3f).
-- JR, running one of his user-controlled teams, offers a trade to another team's GM; the GM accepts, counters or
-- rejects, and walks away when his patience runs out. A deal both sides agree becomes an APPROVED USER proposal
-- (gm_trade_proposals, flag negotiated:<id>) that JR makes in Madden and confirms with Made it.
--
-- Run ./db/backup_db.sh first. Idempotent. Apply with:
--   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_12_21_gm_negotiations.sql

BEGIN;
CREATE TABLE IF NOT EXISTS gm_trade_negotiations (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT NOT NULL,                                -- engine week when opened (offseason 24)
    trade_window VARCHAR(10) NOT NULL CHECK (trade_window IN ('IN_SEASON', 'OFFSEASON')),
    offseason_stage SMALLINT,                         -- the offseason stage it was opened in (expires when it moves)
    user_team_id INT NOT NULL REFERENCES teams(id),   -- JR's team
    gm_team_id INT NOT NULL REFERENCES teams(id),     -- the GM agent's team
    status VARCHAR(11) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'AGREED', 'WALKED_AWAY', 'CLOSED', 'EXPIRED')),
    status_reason TEXT,
    patience SMALLINT NOT NULL,                       -- rounds the GM started with
    patience_left SMALLINT NOT NULL,
    proposal_id INT REFERENCES gm_trade_proposals(id) ON DELETE SET NULL,   -- the agreed deal
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS gm_trade_negotiations_season ON gm_trade_negotiations (season, status);

CREATE TABLE IF NOT EXISTS gm_trade_negotiation_turns (
    id SERIAL PRIMARY KEY,
    negotiation_id INT NOT NULL REFERENCES gm_trade_negotiations(id) ON DELETE CASCADE,
    turn_no SMALLINT NOT NULL,
    actor VARCHAR(2) NOT NULL CHECK (actor IN ('JR', 'GM')),
    action VARCHAR(9) NOT NULL CHECK (action IN ('OFFER', 'COUNTER', 'ACCEPT', 'REJECT', 'WALK_AWAY', 'CLOSE')),
    assets JSONB,                                     -- Front Office JSON assets of the package on the table
    surplus_user BIGINT,                              -- JR's team's surplus by the model
    surplus_gm BIGINT,                                -- the GM's surplus (never shown to JR)
    mood VARCHAR(8),                                  -- the GM's read of an offer: CLOSE | APART | INSULTED
    legality JSONB,
    message TEXT,                                     -- JR's note, or the GM's reply
    message_source VARCHAR(8) CHECK (message_source IN ('JR', 'LLM', 'TEMPLATE')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (negotiation_id, turn_no)
);
COMMIT;
