# P3-04a — the progression fit on the fixed league-shape engine

**Run 2026-11-12 by `deepseek-v4.1-flash (closeout-next-step)`** against `main` at `a252164`, one day after
`ded2627` and `e93e417` changed the code the fit replays. Extended the same afternoon at JR's request with five
seeds on a **full-roster calibration**.
**Measurement only:** the fit patches constants in memory and restores them; nothing was applied to
`engines/progression.py`, no file in the engine or the fit was edited, no database was read beyond the save
snapshot. **Not an audit** — anything needing a correctness judgement is marked **unverified — needs audit**.

Why it ran: [`PROGRESSION_DEVELOPMENT_VERDICT.md`](PROGRESSION_DEVELOPMENT_VERDICT.md) §5 — “fix the target
season and the projected class, then re-run the P3-02 fit against the fixed engine” — and
[`CLOSE_OUT_NEXT_STEP.md`](CLOSE_OUT_NEXT_STEP.md) §1 named it the next close-out unit.

---

## 1. What was run

- **Snapshot:** `CAREER-MU2026` read read-only through `pc_save_reader.mjs` on 2026-11-12 12:10 (2.2 s):
  **2,366 rostered players, 349 rookies, 94 players at 90+, mean OVR 71.79, mean age 26.06, calendar 2026.**
  Identical to the league the 2026-11-10 run used, so every difference below is **code, not population**.
- **Fit A — sample calibration (the engine's live path):** seeds 0–2,
  `progression_fit … --seasons 5 --budget 200 --seed {0,1,2}`, 200 replays each, ≈ 5 s a replay.
- **Fit B — full-roster calibration:** seeds 0–4, same flags, with `calibrate_league_term` fed **every** staying
  player and every rookie instead of the 400-player stratified sample. Done by a scratch runner
  (`/tmp/opencode/p3fit/fit_fullroster.py`) that sets `progression_fit._stratified` to identity, which makes the
  fit's own `frac` 1.0. **No repo file changed**; the report header of each B file says so. ≈ 28.5 s a replay,
  ≈ 118 min for the five (run concurrently).
- **Why the numbers had to move:** `ded2627` moved the exit/rookie step into `engines/progression.py`
  (`shape_exits_and_rookies`) so the fit and the live engine cannot drift, and `e93e417` keyed the exit draw on
  `season:seed`. The fit's replay uses that shared step (`engines/tuning/progression_fit.py:33`), so the three
  reports dated **2026-11-10 19:39–20:14** measured a step that no longer exists.

---

## 2. Scores

| set | seeds | calibration | mean start | mean after |
|---|---|---|---|---|
| 2026-11-10 (pre-fix) | 0–2 | sample | 0.9716 | 0.9173 |
| **A** (2026-11-12) | 0–2 | sample | **0.9603** | **0.9184** |
| **B** (2026-11-12) | 0–4 | **full roster** | **0.9337** | **0.8927** |

Per seed, B:

| seed | start → after | constants |
|---|---|---|
| 0 | 0.9544 → 0.9295 | 10 |
| 1 | 0.9268 → 0.8688 | 13 |
| 2 | 0.9596 → 0.9043 | 8 |
| 3 | 0.9284 → 0.8826 | 10 |
| 4 | 0.8991 → 0.8781 | 10 |

**Reading.** The fixed engine starts closer to the target than the pre-fix one, and the full-roster calibration
starts and finishes best of the three sets — better by 0.027 at the start and 0.026 after. The search's own gain
is about the same in every set (0.041–0.042), so the difference between A and B is the **starting point**, i.e.
the calibration, not the search.

---

## 3. Stability across seeds — the constant question

- **A (sample, 3 seeds):** the only parameter named by all three is `AGE_BAND_OFFSET[5]` (34+), at three
  different values (−0.165 / −0.39 / −0.39) for gains under 0.0012. `AGE_BAND_OFFSET[0]` (≤ 23) is **2 of 3**;
  seed 0 drops it and recommends three constants in total.
- **B (full roster, 5 seeds):** three parameters are named by all five — `AGE_BAND_OFFSET[5]`
  (−0.59 / +0.21 / +0.01 / −0.14 / +0.21), `LEAGUE_TERM_OVR_WEIGHT[1][1]` and `STAR_OVR` (84 / 83 / 84 / 85 / 87)
  — **none at the same value**. `AGE_BAND_OFFSET[0]` is **4 of 5**, but seed 0 wants it **more negative**
  (−0.86 → −1.06) where seeds 1, 3 and 4 want −0.66: the same direction on four seeds, opposite on the fifth.
  `AGE_BAND_OFFSET[1]` (24–26), which carried the largest gain under A, is named by **1 of 5** here.

**Conclusion.** Under either calibration no constant is recommended at one value by all seeds, so the fit is not
an instrument that can settle a single constant. The old claim that `AGE_BAND_OFFSET[0]` −0.86 → −0.66 held on
all three seeds does not survive the fix in either set — it is 2 of 3 on the sample and 4 of 5 with one dissent
in direction on the full roster.

---

## 4. Sample vs full roster — audit-2's question

One replayed season (2027, seed 0), same snapshot, one calibration each:

| calibration | development | star | projected 90+ | projected mean |
|---|---|---|---|---|
| **sample (A, the live path today)** | **0.00** | **+0.75** | **99** (start 94) | 71.69 |
| **full roster (B)** | **+0.50** | **+0.25** | **94** (start 94) | 71.89 |

The five-seed run shows the same thing at league scale. Projected 90+ count per replayed season, target 94:

| set | 2027 | 2028 | 2029 | 2030 | 2031 |
|---|---|---|---|---|---|
| A seed 0 | 99 | 94 | 95 | 84 | 94 |
| A seed 1 | 102 | 90 | 90 | 93 | 97 |
| A seed 2 | 82 | 86 | 90 | 95 | 93 |
| **B, all five** | **94–95** | **93–94** | **94–95** | **92–94** | **94–95** |

The sample path swings the 90+ count between 82 and 102 across seeds — the very count the term exists to hold —
while the full roster holds it inside 92–95 on every seed and every season. That is the strongest evidence in
this document, and it points one way: **calibrate on the full roster**.

Cost, for the live engine: the calibration is the only part that grows — one replayed season costs 5.7 s on the
full roster against 1.1 s on the sample for this league (2,366 players), so an offseason pass would spend a few
seconds more of CPU. It is a cheap change in principle; it is still a change to `engines/progression.py`
(`CALIBRATION_SAMPLE`), which `V2 core U7` owns and JR approves — **unverified — needs audit**.

---

## 5. The league term across the replayed seasons (the P3-04 tripwire)

Development term per replayed season, after each seed's fitted constants:

| set / seed | 2027 | 2028 | 2029 | 2030 | 2031 |
|---|---|---|---|---|---|
| A 0 | 0.00 | 1.00 | 0.50 | **1.50** | 0.50 |
| A 1 | 0.00 | 0.00 | 1.00 | 1.00 | 0.00 |
| A 2 | 0.00 | 0.50 | 0.50 | 1.00 | 0.00 |
| B 0 | 0.50 | 0.50 | 0.50 | 1.00 | 0.50 |
| B 1 | 0.00 | 0.50 | 1.00 | 0.50 | 0.50 |
| B 2 | 0.50 | 0.50 | 0.50 | 0.50 | 1.00 |
| B 3 | 0.50 | 0.00 | 1.00 | 0.50 | 0.50 |
| B 4 | 0.50 | 0.50 | 0.50 | 1.00 | 0.00 |

The verdict's line is “revisit if the development term rises above +1.5 a year”. **A peaks at +1.50, exactly on
the line; B peaks at +1.00.** The tripwire is not tripped in either set.

The earlier “development 0.00 in every first projected season” was the **sample** artefact: with the full roster
the first season is **+0.50 on 4 of 5 seeds** (0.00 on seed 1). The 2026 league's term is small, as the verdict
argued, and the star term does the swinging.

---

## 6. What this does not do

- It applies nothing: no constant in `engines/progression.py` was edited, and no calibration change was made in
  the tree.
- It does not audit `ded2627`/`e93e417`; it measures their output. A review of those commits is still owed.
- It does not decide the calibration question, only measures it — **unverified — needs audit**.
- It leaves the career-arc targets as the worst six in both sets (`career.games_rel.0` 2.80, `career.games_rel.3-5`
  2.21, `career.years_share.0` 1.89, `shape.QB.p90` 1.83, `career.years_share.6+` 1.73; 6 of 34 targets average
  above 1.0, against 7 before the fix). The remaining gap is not the league term.

## 7. For JR

1. **Calibrate on the full roster.** It is better on every measure here: the start (0.9337 vs 0.9603), the finish
   (0.8927 vs 0.9184), and — decisively — the 90+ count the term exists to hold (92–95 across five seeds against
   82–102 on the sample). The live change is `CALIBRATION_SAMPLE` in `engines/progression.py`; it needs a
   `/builder` change and an audit before it means anything in production.
2. **Do not apply a constant on this instrument.** Nothing is stable at one value across seeds, in either set —
   `AGE_BAND_OFFSET[0]` is 2 of 3 (sample) and 4 of 5 with one dissent (full roster), and
   `AGE_BAND_OFFSET[1]` −0.76 → −0.56 collapses from “largest gain on two seeds” to 1 of 5 once the calibration
   is fixed. The constants question belongs to the first shadow season's reports (P5-06), not to this fit.
3. **The freeze input that is now settled:** the tripwire is not tripped (A +1.50 at the line, B +1.00), so P3-04
   needs no revisit on those grounds.

## 8. Artifacts

- `data/tuning/progression_fit_seed{0,1,2}.{json,md}` — set A (sample).
- `data/tuning/progression_fit_full_seed{0..4}.{json,md}` — set B (full roster), each report's footer naming the
  calibration.
- The 2026-11-10 set was copied to `/tmp/opencode/p3fit/old/` before being overwritten; its scores and its one
  stable recommendation are also in `CLOSE_OUT_CHECKLIST.md:225` and in this doc, so nothing is lost when that
  scratch copy goes.
- Scratch, not kept in the tree: `/tmp/opencode/p3fit/{roster_vs_sample.py,fit_fullroster.py,report.py}`.
- Proposed checklist rows: **P3-04a** (done — this document) and **P3-04b** (the calibration decision), both
  listed in `CLOSE_OUT_NEXT_STEP.md` §5 for JR to paste or drop.
