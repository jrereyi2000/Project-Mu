# Tuning ledger

The frozen constants, their values, versions and the receipt that justifies each (CLOSE_OUT_CHECKLIST **P6-03**).
Nothing in here is retuned mid-run: a change after 2026 week 1 needs a version bump, a reason and a note that the
live run's history breaks (CLOSE_OUT_CHECKLIST §0).

P6-03 still owns the full ledger. The section below is the first entry, written by the work that changed the value
(**P3-08**, 2026-11-12, re-measured for the `audit-p3-trade` recheck); the other constants are added at P6-03.

## Trade flow — `TALK_STALL` (`engines/gm_trade_engine.py`)

| Version | Value | Date | Source | State |
|---|---:|---|---|---|
| v1 | 0.25 | 2026-10-20 | assumed ("share of CPU-to-CPU talks that break down", an estimate) | superseded |
| **v2** | **0.64** | **2026-11-12** | P3-08 replay, 2036 weeks 4–8 on a fresh clone of `madden_2036` | **current** |

**Why v2.** `UNILATERAL_TRADE_ENGINE_PLAN` (§3 R1, §9.1) and `GM_TALK_VOICE_PLAN` changed how CPU talks are played,
so the 0.25 assumption was void. P1-25 had already measured 62.5% (pre-voice) and 65.4% (post-voice) breakdowns but
left the constant alone; P3-08 re-measured on the post-build code, with the agreed deals actually executed, and the
share came out at **64.0%**.

**Scope of the constant.** `TALK_STALL` is a planner input only: `simulate_season` and the `--simulate-season`
default read it, and nothing in the live engine does. Retuning it brings the offline planner into line with the
replay; the live band comes from the engine, the REJECT fix and the talk mechanics, not from this number.

### The measurement (P3-08, 2026-11-12; re-measured after audit-p3-trade F1)

- **Database:** `mu_scratch_p3_trade_remeasure_test`, a fresh `CREATE DATABASE … TEMPLATE madden_2036` clone with
  migrations `2027_01_22`, `2027_02_05`, `2027_02_06`, `2027_02_12`, `2027_02_13`, `2027_02_14`; all teams CPU;
  proposals, negotiations, turns, leaks and sightings cleared. `madden_2036`, `madden_mu2026`, `madden_test` and
  every `madden_tune_*` are never a destination (the tool refuses a database not named `mu_scratch_*`).
- **Run:** `python3 -m tools.trade_flow_measure --dsn "host=localhost dbname=mu_scratch_p3_trade_remeasure_test
  user=postgres password=postgres" --season 2036 --first-week 4 --last-week 8 --reset --json OUT`
  (the engine's own seeds; Gemini writer on). **Executions are fed back:** each week's auto-approved CPU deals are
  executed in Mu's books through `front_office._execute` — the league office's DB half after a save write — so the
  weekly pacing sees them; a refused deal is expired with `could_not_make`. The P1-25 replay did not do this
  (`executed` stayed 0), so its budget kept spending a full target.
- **Code:** `e4ed3d85` plus P3-08's fix in `engines/gm_talks.py`: a REJECT ends the talk STALLED (before it, the
  package's author could Accept its own rejected offer and execute it), and that terminal turn draws its
  STALLED-stage leak like the walk-away and run-limit stalls. Fixture: `tests/fixtures/trade_flow_2036_weeks4_8.json`
  is the tool's **raw `--json` output**.

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
| Leaks (OPENED 4 / CLOSE 1 / STALLED 1) | **6** | 2 at the first P3-08 run (no STALLED draw); 3 on the parent; 9 at P1-25 |
| Executed in-season trades, weeks 4–8 | **9** | 9 at P1-25 |
| Executed share (executed/decided) | 36.0% | — |
| Proposals by shape | **8 RENTAL / 17 SWAP** | — |
| Executed by shape | **4 RENTAL / 5 SWAP** | — |
| Writer lines | 57 LLM / 5 TEMPLATE (8.1% fallback) | 26.5% before `bade682`, 6.0% after, 10% target |

**Before the REJECT fix, recorded correctly.** Re-running the tool with only `engines/gm_talks.py` reverted gives
**19 talks, 13 AGREED / 3 STALLED / 3 WALKED_AWAY and 6 self-accepts** (the self-accepts are six of the 13
agreements), not the "17 talks / 15 executed" first reported. The fixed run is the 25/9/16 above; the drop in
agreements is the six rejected deals that no longer execute, plus the search and pacing consequences of executing
fewer deals.

### The band

`TUNING_ACCEPTANCE_THRESHOLDS.md` A4/A5: in-season trades (Week 1 → season end) sit in **5–21** per season, median
13, 3-season mean inside 5–21 and each season inside 3–25. Weeks 4–8 carry 12 of the season's 15 shape-weight units,
so **9 in the window ≈ 11 a season** — inside the band, and the 9 itself is inside it too. The `--no-llm` twin of
the replay gave the same 25/9/16 mix, so the count is not a writer artefact. Three of the 16 stalls re-try a core an
earlier week already stalled (`STALLED` never bars its players, by design); each repeat counts as decided, so the
64.0% includes them.

### Planner agreement

`simulate_season` at the measured share predicts **10.1** executed for weeks 4–8 (measured 9) and **13.3** for a
full season; the old 0.25 predicted 16.0 for the window. The tests pin the constant to the fixture, the ±3 window
agreement and the 5–21 season band (`tests/test_gm_trade_engine.py`,
`test_talk_stall_is_the_measured_2036_breakdown_share`,
`test_pacing_model_lands_in_the_real_band_at_the_measured_breakdown`), the writer's measured share
(`test_the_measured_writer_fallback_share_is_under_the_plan_target`) and the leak total
(`test_the_measured_leak_draw_includes_the_stalled_stage`).

### Not retuned (and why)

- `IN_SEASON_TARGET = 20`, `MAX_NEW_PER_WEEK = 4`, `IN_SEASON_MAX_OPEN = 16`: weeks 4–7 sourced exactly what the
  budget asked for (1, 2, 4, 8 talks), so they were budget-bound; only week 8 fell short (14 requested, 10
  sourced). The result is inside the band, so raising the caps would add failed talks without adding trades.
- `SHAPE_MIX` (80 RENTAL / 15 SWAP / 5 BLOCKBUSTER): not retuned on one window. **Residual:** the replay's owned
  deals were **8 RENTAL and 17 SWAP** (4 and 5 executed), so rentals are 32% of proposals and 44% of executions
  against the planner's 80% — the mix is still an assumption, and a full-season replay should measure it.
- `TALK_MAX_TURNS`, `MAX_CARRY`, the patience values: unchanged; no talk reached 8 turns or carried in this sample.

## Open follow-ups from this entry

- **Full-season trade-flow proof.** P3-08 replays weeks 4–8 of the archive (12 of the 15 in-season shape units); the
  season figure is the shape projection (~11). A full-season replay (weeks 1–8, or a season driven through the
  league office) is still owed, and P3-07's `compare_decisions` is the natural home.
- **RENTAL share.** The measured mix (8 RENTAL / 17 SWAP) is far below `SHAPE_MIX`'s 80/15/5. Measure a second
  archive season before changing the mix.
