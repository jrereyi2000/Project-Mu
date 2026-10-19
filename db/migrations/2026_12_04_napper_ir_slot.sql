-- Napper injured-reserve slot (docs/NAPPER_WEEKLY_IMPLEMENTATION_PLAN.md, Phase 2; decision D4: one per league).
-- Players in the IR slot don't start or score and are left out of trades.
ALTER TABLE napper_leagues ALTER COLUMN roster_format
    SET DEFAULT '{"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 0, "K": 1, "DEF": 1, "BN": 6, "IR": 1}'::jsonb;
UPDATE napper_leagues SET roster_format = roster_format || '{"IR": 1}'::jsonb
WHERE roster_format IS NOT NULL AND NOT (roster_format ? 'IR');
