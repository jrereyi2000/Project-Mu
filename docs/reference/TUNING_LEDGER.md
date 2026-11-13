# Tuning ledger

The frozen constants, their values, versions and the receipt that justifies each (CLOSE_OUT_CHECKLIST **P6-03**).
Nothing in here is retuned mid-run: a change after 2026 week 1 needs a version bump, a reason and a note that the
live run's history breaks (CLOSE_OUT_CHECKLIST §0).

P6-03 still owns the full ledger. The section below is the first entry, written by the work that changed the value
(**P3-08**, 2026-11-12); the other constants are added at P6-03.

## Trade flow — `TALK_STALL` (`engines/gm_trade_engine.py`)

| Version | Value | Date | Source | State |
|---|---:|---|---|---|
| v1 | 0.25 | 2026-10-20 | assumed ("share of CPU-to-CPU talks that break down", an estimate) | superseded |
| **v2** | **0.64** | **2026-11-12** | P3-08 replay, 2036 weeks 4–8 on a fresh clone of `madden_2036` | **current** |

**Why v2.** `UNILATERAL_TRADE_ENGINE_PLAN` (§3 R1, §9.1) and `GM_TALK_VOICE_PLAN` changed how CPU talks are played,
so the 0.25 assumption was void. P1-25 had already measured 62.5% (pre-voice) and 65.4% (post-voice) breakdowns but
left the constant alone; P3-08 re-measured on the post-build code, with the agreed deals actually executed, and the
share came out at **64.0%**.

### The measurement (P3-08, 2026-11-12)

- **Database:** `mu_scratch_p3_trade_remeasure_test`, a fresh `CREATE DATABASE … TEMPLATE madden_2036` clone with
  migrations `2027_01_22`, `2027_02_05`, `2027_02_06`, `2027_02_12`, `2027_02_13`, `2027_02_14`; all teams CPU;
  proposals, negotiations, turns, leaks and sightings cleared. `madden_2036`, `madden_mu2026`, `madden_test` and
  every `madden_tune_*` are never a destination (the tool refuses a database not named `mu_scratch_*`).
- **Run:** `python3 -m tools.trade_flow_measure --dsn <scratch> --season 2036 --first-week 4 --last-week 8 --json OUT`
  (the engine's own seeds; Gemini writer on). **Executions are fed back:** each week's auto-approved CPU deals are
  executed in Mu's books through `front_office._execute` — the league office's DB half after a save write — so the
  weekly pacing sees them; a refused deal is expired with `could_not_make`. The P1-25 replay did not do this
  (`executed` stayed 0), so its budget kept spending a full target.
- **Code:** `e2ef6ab` plus P3-08's REJECT fix in `engines/gm_talks.py` (a REJECT ends the talk STALLED; before it,
  the package's author could Accept its own rejected offer and execute it). Fixture:
  `tests/fixtures/trade_flow_2036_weeks4_8.json`.

| Week | Talks | AGREED (executed) | STALLED | WALKED_AWAY |
|---|---:|---:|---:|---:|
| 4 | 1 | 1 | 0 | 0 |
| 5 | 2 | 0 | 2 | 0 |
| 6 | 4 | 2 | 2 | 0 |
| 7 | 8 | 2 | 6 | 0 |
| 8 | 10 | 4 | 6 | 0 |
| **Total** | **25** | **9** | **16** | **0** |

| Measure | Measured | Old assumption / prior receipt |
|---|---:|---:|
| Breakdown share (`(talks − agreed)/talks`) | **64.0%** | 25% assumed; 65.4% at P1-25 |
| Openings | 5 ANCHOR / 20 FIRM | — |
| Counters | 13 (0.52 a talk) | — |
| Leaks | 2 | 9 at P1-25 (before the chunk-4 leak fixes) |
| Executed in-season trades, weeks 4–8 | **9** | 9 at P1-25 |
| Executed share (executed/decided) | 36.0% | — |
| Writer lines | 57 LLM / 5 TEMPLATE (8.1% fallback) | 26.5% before `bade682`, 6.0% after, 10% target |

### The band

`TUNING_ACCEPTANCE_THRESHOLDS.md` A4/A5: in-season trades (Week 1 → season end) sit in **5–21** per season, median
13, 3-season mean inside 5–21 and each season inside 3–25. Weeks 4–8 carry 12 of the season's 15 shape-weight units,
so **9 in the window ≈ 11 a season** — inside the band, and the 9 itself is inside it too. The `--no-llm` twin of
the replay gave the same 25/9/16 mix, so the count is not a writer artefact.

### Planner agreement

`simulate_season` at the measured share predicts **10.1** executed for weeks 4–8 (measured 9) and **13.3** for a
full season; the old 0.25 predicted 16.0 for the window. The tests pin the constant to the fixture, the ±3 window
agreement and the 5–21 season band (`tests/test_gm_trade_engine.py`,
`test_talk_stall_is_the_measured_2036_breakdown_share`,
`test_pacing_model_lands_in_the_real_band_at_the_measured_breakdown`).

### Not retuned (and why)

- `IN_SEASON_TARGET = 20`, `MAX_NEW_PER_WEEK = 4`, `IN_SEASON_MAX_OPEN = 16`: the window requested 14 talks in week
  8 and could only source 10, so volume is limited by acceptable candidates, not by the budget. Raising them would
  add failed talks without adding trades.
- `SHAPE_MIX` (80 RENTAL / 15 SWAP / 5 BLOCKBUSTER): not retuned on one window. **Residual:** every deal the replay
  stored carried `shape:SWAP` (player for player, some with a late pick); no RENTAL was proposed in the archive
  league. The planner's rental-heavy mix is therefore still an assumption. Worth a follow-up with P3-07's
  `compare_decisions` and a second archive season.
- `TALK_MAX_TURNS`, `MAX_CARRY`, the patience values: unchanged; no talk reached 8 turns or carried in this sample.
