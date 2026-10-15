-- 2026 coaching tendencies as Low / Medium / High tiers (PROJECT_SPEC §3.3a). Each tier ranks the source
-- team-season among the 32 teams of its season (top 11 High, next 10 Medium, bottom 11 Low), from nflverse
-- play-by-play and NGS participation data (regular season):
--   pass_lean                  pass rate over expected (pass_oe) on neutral 1st-3rd downs (win prob 20-80%, outside the last 2 min of a half)
--   fourth_down_aggressiveness go rate on 4th-and-3-or-less from the own 30 on (yardline_100 <= 70), win prob 10-90%, outside the last 2 min of a half
--   blitz_rate                 share of dropbacks with 5+ pass rushers
--   man_coverage               share of charted coverages that are man
-- Source: the 2026 play-caller's last play-calling season (offense, defense) and the head coach's last head-coaching
-- season (4th down, decided by the head coach); first-time head coaches get Medium. Run once:
-- psql -f db/migrations/2026_11_05_coach_tendencies.sql
BEGIN;

ALTER TABLE coaches
    ADD COLUMN IF NOT EXISTS pass_lean VARCHAR(6) CHECK (pass_lean IN ('Low', 'Medium', 'High')),
    ADD COLUMN IF NOT EXISTS fourth_down_aggressiveness VARCHAR(6) CHECK (fourth_down_aggressiveness IN ('Low', 'Medium', 'High')),
    ADD COLUMN IF NOT EXISTS blitz_rate VARCHAR(6) CHECK (blitz_rate IN ('Low', 'Medium', 'High')),
    ADD COLUMN IF NOT EXISTS man_coverage VARCHAR(6) CHECK (man_coverage IN ('Low', 'Medium', 'High'));

UPDATE coaches c SET pass_lean = v.pass_lean, fourth_down_aggressiveness = v.fourth_down,
                     blitz_rate = v.blitz, man_coverage = v.man
FROM (VALUES
    ('Mike LaFleur'        , 'High'  , 'Medium', 'Medium', 'Medium'),  -- off LA 2025 PROE +5.3; 4th none (first-time HC); def ARI 2025 blitz 28% man 29%
    ('Kevin Stefanski'     , 'Low'   , 'High'  , 'High'  , 'Medium'),  -- off CLE 2025 PROE -3.9; 4th CLE 2025 go 65%; def ATL 2025 blitz 35% man 33%
    ('Jesse Minter'        , 'Medium', 'Medium', 'Low'   , 'Low'   ),  -- off CHI 2025 PROE -2.6; 4th none (first-time HC); def LAC 2025 blitz 21% man 23%
    ('Joe Brady'           , 'Medium', 'Medium', 'High'  , 'High'  ),  -- off BUF 2025 PROE -3.0; 4th none (first-time HC); def DEN 2025 blitz 32% man 45%
    ('Dave Canales'        , 'Low'   , 'High'  , 'Medium', 'Low'   ),  -- off CAR 2025 PROE -4.1; 4th CAR 2025 go 74%; def CAR 2025 blitz 26% man 22%
    ('Ben Johnson'         , 'Medium', 'Medium', 'Medium', 'High'  ),  -- off CHI 2025 PROE -2.6; 4th CHI 2025 go 60%; def CHI 2025 blitz 28% man 43%
    ('Zac Taylor'          , 'High'  , 'Medium', 'Low'   , 'High'  ),  -- off CIN 2025 PROE +4.4; 4th CIN 2025 go 64%; def CIN 2025 blitz 18% man 37%
    ('Todd Monken'         , 'Low'   , 'Medium', 'High'  , 'Medium'),  -- off BAL 2025 PROE -9.8; 4th none (first-time HC); def ATL 2025 blitz 35% man 33%
    ('Brian Schottenheimer', 'High'  , 'High'  , 'Low'   , 'High'  ),  -- off DAL 2025 PROE -0.1; 4th DAL 2025 go 80%; def PHI 2025 blitz 19% man 43%
    ('Sean Payton'         , 'High'  , 'Medium', 'High'  , 'High'  ),  -- off DEN 2025 PROE +1.4; 4th DEN 2025 go 57%; def DEN 2025 blitz 32% man 45%
    ('Dan Campbell'        , 'High'  , 'High'  , 'Medium', 'High'  ),  -- off ARI 2025 PROE +5.9; 4th DET 2025 go 84%; def DET 2025 blitz 27% man 43%
    ('Matt LaFleur'        , 'Low'   , 'High'  , 'Medium', 'Medium'),  -- off GB 2025 PROE -4.1; 4th GB 2025 go 70%; def ARI 2025 blitz 28% man 29%
    ('DeMeco Ryans'        , 'Medium', 'Low'   , 'Low'   , 'Medium'),  -- off HOU 2025 PROE -0.5; 4th HOU 2025 go 44%; def HOU 2025 blitz 23% man 32%
    ('Shane Steichen'      , 'High'  , 'Medium', 'High'  , 'High'  ),  -- off IND 2025 PROE +0.0; 4th IND 2025 go 64%; def IND 2025 blitz 28% man 37%
    ('Liam Coen'           , 'High'  , 'Medium', 'Medium', 'Low'   ),  -- off JAX 2025 PROE +2.2; 4th JAX 2025 go 58%; def JAX 2025 blitz 26% man 26%
    ('Andy Reid'           , 'High'  , 'High'  , 'High'  , 'High'  ),  -- off KC 2025 PROE +4.6; 4th KC 2025 go 71%; def KC 2025 blitz 34% man 37%
    ('Klint Kubiak'        , 'Low'   , 'Medium', 'Low'   , 'Low'   ),  -- off SEA 2025 PROE -5.5; 4th none (first-time HC); def LV 2025 blitz 22% man 22%
    ('Jim Harbaugh'        , 'Low'   , 'Low'   , 'Low'   , 'Low'   ),  -- off MIA 2025 PROE -3.7; 4th LAC 2025 go 38%; def LAC 2025 blitz 21% man 23%
    ('Sean McVay'          , 'High'  , 'Medium', 'Low'   , 'Low'   ),  -- off LA 2025 PROE +5.3; 4th LA 2025 go 61%; def LA 2025 blitz 20% man 27%
    ('Jeff Hafley'         , 'High'  , 'Medium', 'Low'   , 'Low'   ),  -- off HOU 2024 PROE +0.5; 4th none (first-time HC); def GB 2025 blitz 21% man 21%
    ('Kevin O''Connell'    , 'Medium', 'Low'   , 'High'  , 'Low'   ),  -- off MIN 2025 PROE -1.7; 4th MIN 2025 go 42%; def MIN 2025 blitz 46% man 19%
    ('Mike Vrabel'         , 'High'  , 'Medium', 'High'  , 'Medium'),  -- off NE 2025 PROE +3.7; 4th NE 2025 go 61%; def NE 2025 blitz 28% man 32%
    ('Kellen Moore'        , 'Medium', 'Medium', 'Medium', 'Low'   ),  -- off NO 2025 PROE -2.4; 4th NO 2025 go 63%; def NO 2025 blitz 26% man 22%
    ('John Harbaugh'       , 'High'  , 'Low'   , 'Low'   , 'Low'   ),  -- off KC 2025 PROE +4.6; 4th BAL 2025 go 50%; def TEN 2025 blitz 23% man 27%
    ('Aaron Glenn'         , 'Low'   , 'Low'   , 'Medium', 'High'  ),  -- off NYJ 2025 PROE -11.3; 4th NYJ 2025 go 53%; def NYJ 2025 blitz 28% man 40%
    ('Nick Sirianni'       , 'Medium', 'High'  , 'Low'   , 'High'  ),  -- off PHI 2025 PROE -1.4; 4th PHI 2025 go 64%; def PHI 2025 blitz 19% man 43%
    ('Mike McCarthy'       , 'Medium', 'High'  , 'Low'   , 'Low'   ),  -- off DAL 2024 PROE -0.5; 4th DAL 2024 go 80%; def LV 2025 blitz 22% man 22%
    ('Kyle Shanahan'       , 'High'  , 'High'  , 'Low'   , 'Low'   ),  -- off SF 2025 PROE +0.0; 4th SF 2025 go 65%; def SF 2025 blitz 22% man 26%
    ('Mike Macdonald'      , 'Low'   , 'Low'   , 'Low'   , 'Medium'),  -- off SEA 2025 PROE -5.5; 4th SEA 2025 go 40%; def SEA 2025 blitz 22% man 29%
    ('Todd Bowles'         , 'Low'   , 'Medium', 'High'  , 'Medium'),  -- off ATL 2025 PROE -4.8; 4th TB 2025 go 62%; def TB 2025 blitz 34% man 30%
    ('Robert Saleh'        , 'Low'   , 'Low'   , 'Low'   , 'Low'   ),  -- off NYG 2025 PROE -5.0; 4th NYJ 2023 go 40%; def SF 2025 blitz 22% man 26%
    ('Dan Quinn'           , 'Medium', 'High'  , 'Medium', 'Medium')  -- off WAS 2025 PROE -2.5; 4th WAS 2025 go 65%; def WAS 2025 blitz 27% man 32%
) AS v(coach_name, pass_lean, fourth_down, blitz, man)
WHERE c.coach_name = v.coach_name;

DO $$ BEGIN
    IF (SELECT count(*) FROM coaches WHERE pass_lean IS NOT NULL) <> 32
    THEN RAISE EXCEPTION 'expected tendencies for the 32 coaches of 2026'; END IF;
END $$;

COMMIT;
