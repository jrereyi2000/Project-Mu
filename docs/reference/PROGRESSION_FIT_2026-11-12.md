# P3-04a — the progression fit on the fixed league-shape engine

**Run 2026-11-12 by `deepseek-v4.1-flash (closeout-next-step)`** against `main` at `a252164`, one day after
`ded2627` and `e93e417` changed the code the fit replays. **Measurement only:** the fit patches constants in
memory and restores them; nothing was applied to `engines/progression.py`, no code changed, no database read
beyond the save snapshot.
**Not an audit.** Anything below that needs a correctness judgement is marked **unverified — needs audit**.

Why it ran: [`PROGRESSION_DEVELOPMENT_VERDICT.md`](PROGRESSION_DEVELOPMENT_VERDICT.md) §5 — “fix the target
season and the projected class, then re-run the P3-02 fit against the fixed engine”, and
[`CLOSE_OUT_NEXT_STEP.md`](CLOSE_OUT_NEXT_STEP.md) §1 named it the next close-out unit.

---

## 1. What was run

- **Snapshot:** `CAREER-MU2026` read read-only through `pc_save_reader.mjs` on 2026-11-12 12:10 (2.2 s):
  **2,366 rostered players, 349 rookies, 94 players at 90+, mean OVR 71.79, mean age 26.06, calendar 2026.**
  Identical to the league the 2026-11-10 run used, so every difference below is **code, not population**.
- **Fit:** `python3 -m engines.tuning.progression_fit <snapshot> --seasons 5 --budget 200 --seed {0,1,2}`
  (`.venv`), one process per seed, 200 replays each. Wall time ≈ 35 min for all three.
- **Why the numbers must move:** `ded2627` moved the exit/rookie step into `engines/progression.py`
  (`shape_exits_and_rookies`) so the fit and the live engine cannot drift, and `e93e417` keyed the exit draw on
  `season:seed`. The fit's replay uses that shared step (`engines/tuning/progression_fit.py:33`), so the three
  reports dated **2026-11-10 19:39–20:14** measured a step that no longer exists.

---

## 2. Scores — old vs new

| seed | 2026-11-10 (pre-fix) | 2026-11-12 (fixed engine) | constants recommended | `AGE_BAND_OFFSET[0]` (−0.86)? |
|---|---|---|---|---|
| 0 | 0.9804 → 0.9399 | **0.9589 → 0.9505** | 3 | **not recommended** |
| 1 | 0.9856 → 0.9234 | **0.9494 → 0.8957** | 8 | −0.86 → −0.66 (gain 0.0052) |
| 2 | 0.9488 → 0.8887 | **0.9725 → 0.9090** | 12 | −0.86 → −0.66 (gain 0.0062) |

Means: start **0.9603** new vs 0.9716 old; after **0.9184** new vs 0.9173 old.

**What that says.** The fixed engine starts closer to the target on two seeds and farther on one, and the search
converges to the same mean after-score. So the fix did not make the engine easier or harder to fit — it changed
*which constants carry the gain*, and that is what the decision was waiting for.

**The old headline does not survive.** P3-02 recorded “the only stable recommendation is `AGE_BAND_OFFSET[0]`
(≤ 23) −0.86 → −0.66, on all three seeds” (`CLOSE_OUT_CHECKLIST.md:225`). On the fixed engine only **2 of 3**
seeds recommend it, and seed 0 recommends three constants in total for a gain of 0.0084. The band that now moves
most is **`AGE_BAND_OFFSET[1]` (24–26), −0.76 → −0.56** — gain **0.0395** on seed 1 and **0.0276** on seed 2, the
largest single move on both; seed 0 does not move it.

The only parameter named by all three seeds is `AGE_BAND_OFFSET[5]` (34+), at three different values
(−0.165 / −0.39 / −0.39) for gains under 0.0012 — i.e. **no constant is a stable recommendation after the fix**.
That is a different answer from 2026-11-10 and the reason this re-run mattered.

Worst targets left (mean error over the three seeds, after): `career.games_rel.0` 2.80,
`career.games_rel.3-5` 2.21, `career.years_share.0` 1.89, `shape.QB.p90` 1.83, `career.years_share.6+` 1.73.
6 of 34 targets average above 1.0 (old run: 7). The remaining gap is the **career-arc** half, not the league
term, and it is the same size as before.

---

## 3. The league term across the five replayed seasons (the P3-04 tripwire)

Development / star term per replayed season, after each seed's fitted constants:

| seed | 2027 | 2028 | 2029 | 2030 | 2031 |
|---|---|---|---|---|---|
| 0 | **0.00** / +0.75 | +1.00 / 0.00 | +0.50 / −0.25 | **+1.50** / −1.00 | +0.50 / +1.25 |
| 1 | **0.00** / +0.50 | 0.00 / −1.00 | +1.00 / 0.00 | +1.00 / −0.50 | 0.00 / +0.50 |
| 2 | **0.00** / −0.75 | +0.50 / 0.00 | +0.50 / +0.25 | +1.00 / −0.50 | 0.00 / −0.50 |

The verdict's line is “revisit if the development term rises above +1.5 a year”. The maximum here is **+1.50**
(seed 0, 2030) — at the line, never over it, so **the tripwire is not tripped**. The 2026 league's development
term stays small (0 to +1.5), as the verdict argued, and the star term does the work in the seasons that swing.

---

## 4. Sample vs full roster — audit-2's open question, measured

`calibrate_league_term` fits on a stratified sample of at most `CALIBRATION_SAMPLE = 400`
(`engines/progression.py:1753`); audit-2 measured on `madden_tune_p2g` that the full roster would pick different
terms (star +1.5 vs the sample's +1.0). The same measurement on the canonical 2026 league, first replayed season
(2027, seed 0), one replay each — the second pass makes `progression_fit._stratified` return every row, which is
exactly “calibrate on the full roster” with the target unscaled:

| calibration | development | star | projected 90+ | projected mean |
|---|---|---|---|---|
| **sample (the live path)** | **0.00** | **+0.75** | **99** (start 94) | 71.69 |
| **full roster** | **+0.50** | **+0.25** | **94** (start 94) | 71.89 |

The two disagree by **+0.5 OVR a year on the development term** and −0.5 on the star term, and the sample
overshoots the 90+ count by 5 where the full roster lands on it. So the question audit-2 opened is not academic:
it changes the term the first shadow season will run under, and it is the reason the first projected season
shows dev 0.00 on all three seeds. **Which one the engine should use is a decision, not something this
measurement settles — unverified — needs audit.**

---

## 5. What this does not do

- It applies nothing. No constant in `engines/progression.py` was edited; `V2 core U7` owns that file and JR
  approves a change (the `/tune` hard rule).
- It does not audit `ded2627`/`e93e417`. It measures their output; a review of those commits is still owed.
- It does not settle the sample-vs-roster choice, or whether the career-arc targets (still the worst six) need a
  different measure rather than a constant.

## 6. For JR

1. **Do not apply the old `AGE_BAND_OFFSET[0]` −0.66 on the strength of the 2026-11-10 fit.** Seed 0 no longer
   recommends it; the 2-of-3 split is not the “stable on all three” result the earlier report recorded.
2. **The one move both moving seeds agree on is `AGE_BAND_OFFSET[1]` (24–26): −0.76 → −0.56.** It is the largest
   gain on seeds 1 and 2. Seed 0 does not move it, so it is 2 of 3 rather than 3 of 3.
3. **Decide the sample-vs-roster question before the first shadow season** (§4). It changes the calibration the
   season runs under, so deciding it afterwards costs a season.
4. If a three-of-three answer is wanted, the cheapest next step is two more seeds (0 and 2 more, ~17 min each)
   rather than any code change. Nothing else in P3 is unblocked by this.

## 7. Artifacts

- `data/tuning/progression_fit_seed{0,1,2}.{json,md}` — the new reports (git-ignored, as always).
- The 2026-11-10 set was copied to `/tmp/opencode/p3fit/old/` before being overwritten; its scores and its one
  stable recommendation are also recorded in `CLOSE_OUT_CHECKLIST.md:225` and in this doc, so nothing is lost
  when that scratch copy goes.
- The sample-vs-roster pass: `/tmp/opencode/p3fit/roster_vs_sample.py` (scratch, not kept in the tree).
- Proposed checklist row **P3-04a** (in `CLOSE_OUT_NEXT_STEP.md` §5) is now **done**, awaiting JR's tick.
