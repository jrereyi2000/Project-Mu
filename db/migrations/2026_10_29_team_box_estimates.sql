-- Per-game team conversions, red zone and penalties: estimated where no box score was captured
-- (engines/team_box_estimator.py, PROJECT_SPEC §2.6). Run once: psql -f db/migrations/2026_10_29_team_box_estimates.sql
BEGIN;

ALTER TABLE team_stats
    ADD COLUMN IF NOT EXISTS red_zone_attempts INT,
    ADD COLUMN IF NOT EXISTS two_point_attempts INT,
    ADD COLUMN IF NOT EXISTS penalties INT,
    ADD COLUMN IF NOT EXISTS estimated_fields TEXT[] NOT NULL DEFAULT '{}';  -- columns the estimator filled

-- Unknown is NULL, not 0: the backfill used to write 0 for these, and a captured box score has no attempts.
ALTER TABLE team_stats
    ALTER COLUMN third_down_attempts DROP DEFAULT, ALTER COLUMN third_down_conversions DROP DEFAULT,
    ALTER COLUMN fourth_down_attempts DROP DEFAULT, ALTER COLUMN fourth_down_conversions DROP DEFAULT;

-- Rows with no captured box score (red_zone_tds is set only by a box-score capture): nothing here is known.
UPDATE team_stats SET third_down_attempts = NULL, third_down_conversions = NULL,
                      fourth_down_attempts = NULL, fourth_down_conversions = NULL
WHERE red_zone_tds IS NULL AND estimated_fields = '{}';

-- Captured box scores give conversions but not attempts; red-zone trips follow from the percentage.
UPDATE team_stats SET third_down_attempts = NULL, fourth_down_attempts = NULL,
       red_zone_attempts = CASE WHEN red_zone_pct > 0 AND red_zone_tds + red_zone_fgs > 0
                                THEN ROUND((red_zone_tds + red_zone_fgs) * 100.0 / red_zone_pct) END
WHERE red_zone_tds IS NOT NULL AND estimated_fields = '{}';

COMMIT;
