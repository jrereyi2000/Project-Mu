# Development curve and the league term: P3-04 verdict

**Written 2026-11-11 by p3-lead-2 (claude-opus-5-5).** This is CLOSE_OUT_CHECKLIST P3-04. It is a recommendation for
JR. Nothing in `engines/progression.py` was changed.

## Question

The progression engine adds a **league-shape term** (`engines/progression.py`, `calibrate_league_term`). It gives
young players below star level extra OVR, and young stars a separate star term, so that the league keeps its 90+
count and mean OVR from year to year (JR, 2026-10-28). The 2036 simulation in PROGRESSION_PLAN found the
development term at +4.5 to +5.5 OVR a year at full weight. That is where "a 68-OVR rookie gains ~5–6 a year" comes
from, against Madden's own ~+1. P3-04 asks whether, from Mu's real starting league (2026), the term does work
that real careers don't justify.

## Short answer

1. **In the 2026 league, as the fit replays it, the term is small and justified.** It sits at **+0.5 a year**
   (+1.0 to +1.5 in a few seasons). A rookie who stays three seasons gains about **+2.7 OVR**, and **+1.1 of
   that comes from the term**. A 68–72 rookie gains **+2.3 over three years (+0.8 a year)**, against Madden's
   ~+1 a year. Without the term he would gain +0.4 a year, and rookies at 67 or below would *lose* about 1 OVR over
   three years. The "+5–6 a year" figure belonged to the 2036 league, where Madden's rookie classes came in about
   10 OVR below the veterans they replaced. It does not describe 2026. **Verdict: accept the term's design.**
2. **But the live engine does not run the term in a 2026 league. That is a bug** (§3). `league_shape_target`
   reads `player_seasons` for **season 2036** (`LEAGUE_SHAPE_SEASON`), and a 2026-era database has no rows
   there. So the target is 0 players, a 90+ count of 0 and a mean OVR of 0. The calibration then picks
   **development 0 and star −1**. In `madden_tune_p2g`'s 2027 offseason, every player under 86 got a league term of
   exactly 0. The 138 players at 86+ and ≤ 32 averaged **−1.21** (star −1 plus upkeep), and 132 of them were
   non-zero. The design is accepted, but **a follow-up is open** to give the term the right target. JR needs to
   decide what shape a 2026 league holds (§4).

## 1. What the replay shows (2026 league)

Method: `engines.tuning.progression_fit`'s own replay (`_replay`), run over a fresh `pc_save_reader` snapshot of
`CAREER-MU2026` (2,366 rostered players, 349 rookies; the save's sha256 was unchanged). It ran 5 seasons with the
engine's current constants, on seeds 0, 1 and 2. The league term holds the **snapshot's own** shape, as the fit
always does. For each player, each year's OVR change was computed twice, with and without the fitted terms, and
the difference is that year's league contribution. The cohort is the 349 rookies of 2026. Of those, 238–252
stayed three seasons. The script and its output are scratch files and are not kept.

Fitted terms per season (development, star):

| seed | 2027 | 2028 | 2029 | 2030 | 2031 |
|---|---|---|---|---|---|
| 0 | +0.5, 0 | +0.5, +1.0 | +0.5, +1.25 | +1.5, −0.75 | +0.5, 0 |
| 1 | +0.5, 0 | +0.5, +0.5 | +0.5, +1.25 | +1.0, −0.75 | +0.5, −0.75 |
| 2 | +0.5, 0 | +0.5, −1.0 | +0.5, +1.25 | +0.5, +0.5 | +1.0, −1.0 |

Three-year arcs of the 2026 rookies who stayed three seasons. The figures are means over the three seeds, which
agree within ±0.15:

| entry OVR | n (per seed) | gain over 3 yrs | from the league term | without it | year-1 gain | started all 3 yrs |
|---|---:|---:|---:|---:|---:|---:|
| ≤ 62 | 15–17 | +0.6 | +1.1 | −0.5 | +0.75 | 0 |
| 63–67 | 74–81 | +0.15 | +1.1 | −0.95 | +0.4 | 0 |
| 68–72 | 86–92 | +2.3 | +1.15 | +1.2 | +1.15 | 3–5 |
| 73+ | 63–66 | +6.9 | +1.2 | +5.7 | +2.6 | 40–42 |
| all | 238–252 | +2.7 | +1.1 | +1.6 | +1.3 | 43–47 |

How to read it:

- **The term is a modest, flat lift.** It adds about +0.4 a year to every young player below 80. It does not
  pick winners. Who develops is still decided by ceilings, playing time and performance: the 73+ rookies who
  start gain about 5.7 on their own.
- **It mostly cancels the young age-band offset.** `AGE_BAND_OFFSET[0]` (≤ 23) is −0.86, fitted to Madden's
  2035→2036 offseason. Without the term, low-OVR rookies decline. The P3-02 fit's one stable recommendation points
  the same way: raise `AGE_BAND_OFFSET[0]` to −0.66 on all three seeds. If JR applies that, the term should get
  smaller still, because young players develop more on their own merit.
- **The knob is coarse at this size.** `LEAGUE_TERM_STEPS` runs 0 to 6 in steps of 0.5 and can't go below 0. At
  2026 the term sits on its smallest non-zero step, so the true value may lie between 0 and 0.5. This is noted,
  not a follow-up.

## 2. Against real careers

The repository has **no real OVR history** (no year-by-year Madden ratings of real players). So "real
rookie-to-year-3 arcs" can't be compared in OVR. The real-career evidence there is usage:

- `data/history/nfl_careers_through_2025.csv` (the fit's career target): real players' games rise from **0.56**
  of the best band in year 0 to **0.76** in years 1–2. Mu's replay gives **0.84 / 0.88**, so its rookies play too
  much compared with veterans. That comes from how the replay assigns roles (starters by OVR rank on the team,
  backups at the backup mean). It is not caused by the league term, which moves OVR by about +0.4 a year, too
  little to change many ranks.
- THRESHOLDS **A6** (share of each round with 2+ started seasons, 85% in round 1 down to 11% in round 7) and **A7**
  (rookie usage) are the real-career checks that development is right. They become measurable in the P5 shadow
  seasons, once builder-7's R2b (the chart in force per slot and week) lands and a season produces started
  seasons.

Nothing in the real-career evidence argues that the term at its 2026 size is too large. Measured in OVR, it keeps
a 68–72 rookie a little below Madden's own development rate.

## 3. The bug: the live engine's target is season 2036

- `engines/progression.py:1342`: `run_progression` calls `league_shape_target(cur)`, which defaults to
  `LEAGUE_SHAPE_SEASON = 2036` (`:102`, `:1718`). The draft class it projects is
  `rookie_class(league_turnover(cur, LEAGUE_SHAPE_SEASON), …)` (`:1344`), and that is empty for the same reason.
- `engines/progression_checkpoint.py:524` (`prepare_offseason_pass`) runs it with `hold_shape` at its default,
  True. So every offseason progression in a 2026-era league calibrates against an empty target: the tuning leagues
  now, and the live league once it starts in 2026. `madden_2036` has its 2036 rows and is not affected.
- With target `{players: 0, count_90_plus: 0, avg_ovr: 0}`, `calibrate_league_term` minimises the mean and the
  90+ count. It picks the development term 0 (the smallest step) and the star term −1 (the smallest step).
- **Observed, `madden_tune_p2g`, `player_progression_history` season 2027:** 2,678 players under 86 have league
  term 0.00. Of the 138 players at 86+ and ≤ 32, 132 have a negative term, with mean −1.21 and minimum −3.57 (the
  term includes upkeep above 90). The 90+ count went from 71 (2026) to 67 (2027). `p2g` is the only tuning league
  that has reached an offseason so far.
- The P3-02 fit does **not** reproduce this. Its replay holds the snapshot's own shape. So the fit's numbers,
  including the `AGE_BAND_OFFSET[0]` recommendation, describe the engine *as designed* rather than as it runs
  today. They become the right numbers once the target is fixed.

**Confirmed by audit-2 (HIGH, board M-0396).** The proof, every caller and a fix brief are in
`data/audit_reports/league_shape/report.md`, which is git-ignored and lives in the main checkout. Its counts are
by age at progression: on p2g 2027 all 126 players at 86+ and ≤ 32 are negative, with a mean of −1.29, and the 90+
count falls from 94 to 65 in one pass. Two more paths reach the bug, `progression_save.pass_edits` (`:291`) and
the CLI. **`madden_mu2026` (the real franchise) is set up for managed progression, so it would be hit at its first
OFFSEASON_START. It must not be advanced into an offseason until this is fixed.**
**A second, older finding (MEDIUM):** `class_size = max(0, target − roster)` (`:1343`) is always 0, because both
numbers come from the same season and filter. So the live engine never models leavers or the coming rookie class,
which the spec and the offline fit both assume it does. `madden_2036` is affected by this one. This document
doesn't fix either finding.

## 4. Decision for JR

**Ruled by JR, 2026-11-11:** a 2026-era league holds **the shape of its own start season** (reading (a)), and the
live engine's projection **accounts for the players leaving and the coming draft class**, as the fit does. The
fix is built by builder-8, from audit-2's brief. **Each league stores its own shape season, so `madden_2036` keeps
the 2036 shape** (JR, relayed in board M-0413). The options JR chose from follow.

**After the fix (builder-8, M-0436), JR ruled on two more points, 2026-11-11 (M-0441):**
- **The projection's leaver draw is keyed on the season and the seed.** The live engine uses seed 0, and the fit
  uses its `--seed` per replayed season. Different seeds then vary departures, and the 3-seed majority in
  P3-02 means something again.
- **`madden_2036` is archival only for now, and the shape-season migration isn't applied to it.** With the
  projection drafting, its 2037 terms would be development +4.0 and star +1.5. That's the pattern §5 says to
  revisit (above +1.5 a year), and it stays a P3 question.

**What shape does a 2026-era league hold?** JR's 2026-10-28 rule ("the league keeps its 2036 shape, same 90+
count and mean OVR") was written while the live league was 2036. audit-2 measured each reading on the p2g copy at
2027 (report above):

| reading | development / star term | 90+ after | mean OVR after |
|---|---|---:|---:|
| **(a) the league's own start season** (recommended; what the fit and §1 use) | +1.0 / +1.0 | 74 | 70.28 |
| (b) the current season S−1 (same as (a) at 2027, but it drifts year by year) | +1.0 / +1.0 | 74 | 70.28 |
| (c1) 2036 read literally (the term is off) | — | 61 | — |
| (c2) 2036's proportions (both terms hit their caps) | capped | 95 | — |

**A separate question:** should the live projection model leavers and the coming draft (the MEDIUM finding),
as the fit does? audit-2 has put both questions to JR.

## 5. Verdict

- **Accept** the league term's design for the 2026 league. At its fitted size, +0.5 a year and about +1.1 over a
  rookie's first three years, it lifts the development of young players below 80 to roughly Madden's own rate.
  The real-career evidence does not argue against that.
- **Follow-up (open, confirmed HIGH; JR ruled in §4, so the fix is ready to build):** fix the target season and the projected class (§3), then re-run the
  P3-02 fit against the fixed engine. In the first P5 shadow season, check the logged
  `player_progression_history.league_term`. **If the development term rises above +1.5 a year in any season,
  revisit the term.** That would be the 2036 pattern, where rookie classes fall far behind the departing
  veterans.
