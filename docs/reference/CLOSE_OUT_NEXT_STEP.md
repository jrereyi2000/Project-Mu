# Close-out: the next step, and which model should do it

**Written 2026-11-12 by `deepseek-v4.1-flash (closeout-next-step)`, against `main` at `a252164`.**
**Status: research only.** No code, no PC, no database writes (read-only `SELECT`s and migration counts), no
audit. Nothing here is a CLOSED / PARTIAL / OPEN verdict; anything that needs one is marked
**unverified — needs audit**.

Sources: [`docs/CLOSE_OUT_CHECKLIST.md`](../CLOSE_OUT_CHECKLIST.md) (P0–P9, Appendices A–C),
[`CLOSE_OUT_STATUS.md`](CLOSE_OUT_STATUS.md) (the 2026-11-12 inventory: 49 IMPLEMENTED / 16 PARTIAL / 47 NOT FOUND),
[`FOLLOW_UPS.md`](../FOLLOW_UPS.md), [`MODEL_ROUTING_EVALUATION.md`](MODEL_ROUTING_EVALUATION.md),
[`AUDITOR_MODEL_EVALUATION.md`](AUDITOR_MODEL_EVALUATION.md), and the two plans building right now
(`fact-gate`, board M-0657; `glm-unification`, M-0658).

---

## 1. Answer

**The next item is P3-04's open follow-up: re-run the P3-02 progression fit against the fixed league-shape
engine (three seeds) and settle the two questions the verdict left open (sample vs full roster; whether the
term moved).** It is a measurement, so it needs **no audit verdict** — which is why it can run while the audit
lane is parked.

> **Status, 2026-11-12: unit 1 is done** (and extended at JR's request), results in
> [`PROGRESSION_FIT_2026-11-12.md`](PROGRESSION_FIT_2026-11-12.md). Headline: the old “stable on all three
> seeds” recommendation does **not** survive — `AGE_BAND_OFFSET[0]` is 2 of 3 on the sample calibration and
> 4 of 5 with one dissent in direction on the full roster, and `AGE_BAND_OFFSET[1]` collapses from “largest gain
> on two seeds” to 1 of 5 once the calibration is fixed. The tripwire is not tripped (development term +1.50 at
> the line on the sample, +1.00 on the full roster). **The one clear result is structural:** calibrating on the
> **full roster** beats the 400-player sample on start (0.9337 vs 0.9603), finish (0.8927 vs 0.9184) and — the
> point of the term — the 90+ count it holds (92–95 across five seeds, against 82–102 on the sample). Nothing was
> applied; the calibration change is P3-04b for JR.

- **Checklist ID:** **P3-04** (its own recorded follow-up; it re-runs **P3-02**). Proposed as a new sub-row
  **P3-04a** in §5 below, because P3-04 and P3-02 are both ticked ☑ and the outstanding half has no row.
- **Why it is the true next one, not the next unchecked row:** the first unchecked rows in document order are
  P3-05 (blocked: it needs P3-03, which needs a shadow season) and P4-01 (the Madden PC and nothing else on the
  critical path). This one is different in kind: it is the last **offline input to a decision that must precede
  the first shadow season**. P3-02 fitted a stable recommendation (`AGE_BAND_OFFSET[0]` −0.86 → −0.66 on all
  three seeds) and P3-04 accepted the league term on it, but the code the fit replays **changed after the fit
  ran** (`ded2627`, `e93e417`, both on 2026-11-11 morning; the three reports are dated 2026-11-10 19:39–20:14).
  P5's `development_learning` tuning compares against that fit (`CLOSE_OUT_CHECKLIST.md:356`), and the constants
  must be frozen before 2026 week 1 rather than re-decided mid-shadow-season. So the sequence is: refresh the
  numbers → JR decides → freeze → shadow seasons.
- **Depends on:** the league-shape fix already on `main` (`ded2627` shape-season storage + live projection;
  `e93e417` exits keyed on season and seed) and `madden_mu2026` already carrying it — read-only today:
  `144` migrations, `league_state` = `2026 / 1 / shape_season 2026`. Also the snapshot input
  (`CAREER-MU2026`, readable in the PC saves folder) and `data/history/nfl_careers_through_2025.csv`. Nothing
  else: no plan, no migration, no PC screen, no model quota beyond reading its own report.
- **Size:** **M** — one command per seed (~13 min each at the recorded 0.8 s/season × 5 seasons × 200 replays),
  three seeds, plus one measurement and a one-page write-up.
- **Madden PC?** **No screen, no pilot.** It needs a ~2 s read of `CAREER-MU2026` through
  `node pipeline/save_reader/pc_save_reader.mjs` (it copies to a temp file). Take it while Madden is not
  mid-save; it does not need the `CLAIM · Madden PC`.
- **Human at the screen?** No.
- **Database write?** No. The fit reads a JSON snapshot and patches engine constants **in memory only**
  (`engines/tuning/progression_fit.py:1-12`).
- **JR's decision?** Not to run it. **Yes afterwards:** whether to apply the recommendation. Nothing in
  `engines/progression.py` is edited by this item, and applying a constant stays a `/builder` change JR approves
  (the `/tune` skill's hard rule).
- **Done-when evidence:**
  1. three new `data/tuning/progression_fit_seed{0,1,2}.md|.json` with headers dated after 2026-11-11 05:22 and
     a different before/after score from the 2026-11-10 set (0.9804→0.9399 / 0.9856→0.9234 / 0.9488→0.8887);
  2. the `AGE_BAND_OFFSET[0]` recommendation stated for each seed, and whether it is still the one stable one;
  3. audit-2's open question answered with a number: `calibrate_league_term` on the stratified sample
     (`CALIBRATION_SAMPLE`) vs the full roster, star term each way (the verdict records +1.5 vs +1.0 on p2g);
  4. a **committed** note with those numbers (the fit writes into git-ignored `data/tuning/`, so the record has
     to be a doc — see §5);
  5. explicitly: nothing applied to `engines/progression.py`.

**Honest answer: it is a short sequence, not one item.** Three units, in this order, because each is
unblocked by the one before it:

| # | Unit | ID | Needs | Why here |
|---|---|---|---|---|
| 1 | Re-run the fit on the fixed engine, 3 seeds; sample-vs-roster; committed note | **P3-04a** (new) | save read only | last offline input to the constants freeze; no audit needed. **Done 2026-11-12** |
| 2 | Tuning DB to head (144; `madden_tune_p2g` is at **138**, no `shape_season` column) and prove every feature reaches `shadow` | **P5-01** | a `madden_tune_*` write | S, an hour; the gateway to the shadow seasons, and its precondition is not met today |
| 3 | First shadow season on the PC (`producers` + `relationships`), then the per-feature loop | **P5-02** → P5-14 | Madden PC + a full season per feature | the long pole; it waits on the PC, so it cannot be unit 1 |

Unit 1 is the one to start; unit 2 can run in the same sitting while unit 1's fit is in the background.
**With unit 1 done, unit 2 (P5-01) is next** — it is still the only PC-free step of the shadow-season phase.

---

## 2. Evidence

| Fact | Evidence |
|---|---|
| The fit reports predate the engine change they should measure | `data/tuning/progression_fit_seed{0,1,2}.md` mtimes 2026-11-10 19:39 / 19:55 / 20:14; `ded2627` 2026-11-11 04:27:51, `e93e417` 2026-11-11 05:22:36 |
| The fit replays the changed function | `engines/tuning/progression_fit.py:33` — step 3 exits are "shared with the engine as `progression.shape_exits_and_rookies`"; that function is `engines/progression.py:1834` |
| The fix is in and stamped on the live franchise | read-only: `madden_mu2026` `schema_migrations` 144, head `2027_02_29_stats_counter_season_games`; `league_state` = `2026 / 1 / 2026` |
| The broken calibration is visible in the only tuning league with an offseason | read-only `madden_tune_p2g`, `player_progression_history` season 2027: **2,699** rows at `league_term` 0.00, **148** negative, mean −0.07 (the verdict's figure: development 0, star −1) |
| The tuning path is behind on schema — P5-01 is real work, not a formality | `madden_tune_p2g` 138 migrations, `madden_tune_p2f` 137; `madden_tune_p2g.league_state` has **no** `shape_season` column; `personality_feature_modes` = 0 rows on p2g/p2f/p2h |
| The PC is not needed for the fit | `ls "/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/CAREER-MU2026"` resolves; `pc_save_reader.mjs` reads a temp copy (`.claude/skills/tune/SKILL.md` §“Hard rules”) |
| The fit's output is git-ignored, so the record must be committed | `data/tuning/` is not tracked; `CLOSE_OUT_CHECKLIST.md:225` names `data/tuning/progression_fit.md` as the done-when artifact |
| The audit lane is parked, so nothing here may need a verdict | board M-0642; `AUDITOR_MODEL_EVALUATION.md` §1.3, §4.4 |
| No file collision with the two building plans | fact-gate owns `newsroom/db_tools.py`, `newsroom/fact_gate.py`, `swarm_dispatcher.py`, `db/init_db.sql` (M-0657); glm-unification owns `engines/llm_provider.py`, the writers (M-0658). The fit's files are `engines/tuning/progression_fit.py` (unmodified) and git-ignored `data/tuning/` |

### 2.1 What I rejected, and why

- **P4-01 (baseline the installed game)** — the first unchecked row, and the survey's second-biggest gap. It
  is **PC-bound** (a full season sim on a `CAREER-TUNE-*` clone with `Mu Base`), it is a sibling branch in the
  programme tree (`CLOSE_OUT_CHECKLIST.md:55-57` — P3, P4 and P5 all feed P6), and it closes nothing else
  until P4-02's “signed-off entry in the tuning ledger” exists — and `docs/reference/TUNING_LEDGER.md` does not
  (P6-03). Doing it first would put the only scarce resource (the PC) on the branch that gates least.
- **P5-01 alone** — right place, too small to be “the next step”: it is an hour, and it leads straight into
  P5-02, which needs the PC. Keep it as unit 2 of the same sitting.
- **P7-00e (`--managed`)** — the survey's third gap and a clean offline code change, but it is downstream:
  nothing before P7-06/P9-01 needs it, and its positive test (`sim-season --managed`) needs the PC. Good
  fill-in work for a builder lane when the PC is busy; not the programme's next step.
- **The P8 lane (P8-03/04/06b/07/08–11)** — either needs a browser and JR's eyes, or is the newsroom work the
  two building plans already own, or needs the audit's verdicts. Not next.
- **Anything in the parked audit queue** (stats3 `26d1141`, stats2b `07decd3`, the two `opencode-route-plan`
  reviews) — it needs a CLOSED verdict, which a DeepSeek session may not give and no auditor session is up.

---

## 3. Which model should do it

Quota read at 2026-11-12 11:59 EST (`python3 -m tools.go_usage`: **5h 6% (resets 18:50Z), weekly 38%, month
19%**). Claude and Codex figures are the measured ones on the board and in `AUDITOR_MODEL_EVALUATION.md`.

**Recommendation: a DeepSeek session running `/tune progression`.** The work is one command per seed plus a
report to read; it needs shell, the repo's own tools and almost no tokens, and DeepSeek is the cheap route with
the only real headroom.

| Session type | What it would do | Measured evidence | Quota today | Verdict |
|---|---|---|---|---|
| **`deepseek` (OpenCode Go, `deepseek-v4.1-flash`) + `/tune progression`** | snapshot, three fits, sample-vs-roster, the note | Go charges it **0.60× list** (measured; `AUDITOR_MODEL_EVALUATION.md` §2 sources). The task is CPU/IO-bound, not token-bound | **5h 6% / weekly 38% / month 19%** (model limit $100/mo) | **Recommended** |
| `claude` / `opus` (Opus 5.5 Medium) | same, but on Claude Code | Claude **weekly 98%** on 2026-11-11 (resets Nov 16 03:00) and **92** commit-audit reviews killed by usage limits | no spare capacity | **Worse** — it would starve the audit lane and the post-commit hook for work that needs no Claude quality |
| `codex` (GPT-6.1 Sol) | same | audit-5 measured **137.2M input / 14 batches / 18.5 h**, 5h **99%** at 04:46 EST, weekly 72%, reset 07:00 EST | the 5-hour wall has since reset, but its week is the parked queue's | **Worse** — spend the auditor's week on auditing, not on a measurement |
| `gemini` (Gemini 4 Argon) | same | TB4 57.58% independent; **no Mu audit report exists**, so its audit record is unmeasured rather than good; API price doubles 2026-12-01 | no measured Mu usage | **Worse here, better one step later** — save it for the review (§3.1) |
| `glm` (GLM 5.3) | same | Go charges `glm-5.3` at **4.00× list** on a **$15/month** limit — one audit day prices at **$163 of allowance, 10.9× the limit**; `glm-5.3-flash` is 1.00× | n/a | **Unavailable** — no `glm` launcher/skill exists (a ~100-line, 4–6 h build) and Go would wall it |
| `muse` (Spark 1.3) | same | 1.23 min to week 4 on the pilot driver; trains on request data | Go, headroom | **Not allowed** — a Muse session takes builder and computer-use work only |

### 3.1 The hand-off chain (build then audit)

1. **`deepseek` + `/tune progression`** runs unit 1 and hands JR the numbers. Nothing applied.
2. **JR decides** whether `AGE_BAND_OFFSET[0]` (and anything else that survives three seeds) is applied before
   the freeze. That decision is his, by the checklist's own rule.
3. **If yes: `deepseek` + `/builder`** makes the one-line constant change in `engines/progression.py` with a test
   that fails on the parent. A DeepSeek builder is allowed and is the cheapest lane.
4. **`gemini` (Gemini 4 Argon) + `/audit` reviews it.** Not the builder, and not DeepSeek (a DeepSeek session
   never audits). Claude's week is at 98% and Codex's is the parked queue's, so Argon is the only permitted
   auditor with room — and a one-line constant change is exactly the size of finding it should cut its teeth on.
   **If JR's Codex quota is back and the parked queue is drained first, Codex is the better reviewer** (the
   incumbent, with the measured per-batch cost).
5. The audit's verdict is the auditor's, not mine: what is unverified today is that the fit's new numbers are
   right for a 2026 league, and that the recommendation holds on all three seeds — **unverified — needs audit**.

---

## 4. What this does not settle

- Whether the post-fix engine's live term lands above +1.5 a year in a real season. That is read in the first
  P5 shadow season (`player_progression_history.league_term`), not here — **unverified — needs audit**.
- Whether P5-01 is met on a tuning DB today: I read 138 migrations and zero `personality_feature_modes` rows on
  `madden_tune_p2g`, which says the migrations and the switchboard reachability are still to be done, not that
  the code path is wrong.
- Whether the fit's recommendation should be applied. Mine is not to say.

---

## 5. Proposed checklist change — for JR, not applied

`docs/CLOSE_OUT_CHECKLIST.md` was not edited. Two rows:

**Add, under §5.1 (P3), after P3-04:**

> | ☑ | **P3-04a** | **Re-run the P3-02 fit on the fixed league-shape engine** (P3-04's open follow-up, 2026-11-12). Three seeds against `CAREER-MU2026` after `ded2627`/`e93e417`, answer audit-2's sample-vs-full-roster question, write the numbers into a committed note, apply nothing. | Fresh `progression_fit_seed{0,1,2}` reports plus a committed note; `AGE_BAND_OFFSET[0]` recommendation stated per seed. | M |

**Run 2026-11-12 — tick it when JR agrees.** Evidence in
[`PROGRESSION_FIT_2026-11-12.md`](PROGRESSION_FIT_2026-11-12.md): three sample-calibrated reports, five
full-roster ones, `AGE_BAND_OFFSET[0]` stated per seed, the sample-vs-roster table, and the committed note.

**Add, as the decision that measurement opened:**

> | ☐ | **P3-04b** | **Settle the league-shape calibration: full roster or `CALIBRATION_SAMPLE`.** The fit says full roster on every measure (start, finish, and the 90+ count held at 92–95 against the sample's 82–102); the live engine still calibrates on a 400-player sample (`engines/progression.py:1753`). Change `CALIBRATION_SAMPLE`, or record why the sample stays, before the first shadow season — the term the season runs under depends on it. | A `/builder` change with a test (or a recorded decision), audited, and the term read in the first shadow season. | M |

**Note on P3-02's tick (no status change):** append “the fitted numbers predate the 2026-11-11 league-shape
fix; the re-run is P3-04a”.

Not proposed: any tick for P4-03 or P7-03 (the survey's proposals stand, and they are JR's); any change to
`CLOSE_OUT_STATUS.md`.
