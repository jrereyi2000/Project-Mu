# Project Mu: Muse Ember evaluation

**Status:** **step 1 is run and written up** — 2026-11-12, by `deepseek-v4.1-flash (muse-ember-plan-2)`. §1 spike S-M1 (done), §2 Job A + Job B + the `launch` defect, §3 the gate, §5 the recommendation. Step 2 (Ember) is not run: no Zen balance (B2).
**Plan:** [`MUSE_EMBER_ROTATION_PLAN.md`](../in_progress/MUSE_EMBER_ROTATION_PLAN.md) §2.7 (benchmark), §3 (spike, risks), §5 U4.
**Model:** step 1 `opencode-go/muse-spark-1.3-contributor` (Go plan); step 2 `opencode/muse-ember-1.0` (Zen, on a G-S pass and after the top-up).

The question this file answers: does a Muse session, driving the same tools a DeepSeek or Opus session drives, do Project Mu's computer-use and builder work well enough to justify a route — and at what wall-clock and cost?

---

## 1. Spike S-M1 — the TUI route (DONE, 2026-11-12)

Run under JR's approval in the `muse-ember-plan-2` session. No Madden PC, no pilot job, no repo file touched except this section and the launcher correction in §1.3.

### 1.1 What the spike asked, and what answered

| Check (plan §3) | Result |
|---|---|
| The Muse window answers as `muse-spark-1.3-contributor`, not DeepSeek | **Pass.** `Reply with exactly: SPARK READY` → `SPARK READY`, session model `{id: muse-spark-1.3-contributor, providerID: opencode-go}`, agent `build`. |
| A second window on DeepSeek still answers as `deepseek-v4.1-flash` | **Pass.** `tools/opencode_session.sh` in its own window → `DEEPSEEK READY` on `deepseek-v4.1-flash`; the two sessions ran side by side in the shared service without interference (R6 holds so far). |
| The TUI shows the Muse model | **Pass.** The pane reads `Build · Muse Spark 1.3 Contributor · OpenCode Go` under the answer, and the status line shows `~/Project-Mu:main` — the session's location is the repo, so `AGENTS.md` (the `CLAUDE.md` symlink) loads. |
| One file edit through the build agent in a scratch worktree | **Pass.** `opencode run --model opencode-go/muse-spark-1.3-contributor --agent build` in a scratch worktree wrote `SPIKE_SPARK.md` containing exactly `spark tool use ok`, then read it back to verify; `git status` showed that one untracked file and nothing else. |

Observed while answering: 1.1–1.3 s to first answer, ~15–17 tok/s, 13.9K context tokens on a fresh repo session (system prompt + repo instructions + this section's absence). The session's `cost` field reads 0: Go is a subscription, not metered per request. Nothing was spent on Zen.

### 1.2 Blockers, as of 2026-11-12

- **B1 — cleared.** JR turned on "Allow paid endpoints that train on request data". `opencode run --model opencode-go/muse-spark-1.3-contributor` now answers. **R7 stands:** Spark's requests go to a training endpoint, so its use stays at computer-use frames and the spike itself, not repo-wide builder work (Q1).
- **B2 — open.** `opencode run --model opencode/muse-ember-1.0` → `Error: Insufficient balance. Top up at …/billing?credits=1`. Ember is Zen-only. Step 2 does not start until JR tops up, and per D10 it should wait for a G-S pass anyway.

### 1.3 The defect the spike found (and the fix)

**The merged launcher could not start a session at all.** `tools/muse_session.sh` (U1, `c21dd4e`) exec'd `opencode --model <id> --session <id> …`. OpenCode **v2.2.7's TUI has no `--model` flag**: it prints the top-level help and `ERROR Unrecognized flag: --model in command opencode`, exit 1. Only `opencode run` and `opencode mini` accept `--model`. The DeepSeek launcher (`tools/opencode_session.sh`) never passes it — it relies on the project pin in `.opencode/opencode.json` — which is why the DeepSeek route worked and the Muse one did not. The 24 U1 tests passed because the stub `opencode` records argv without parsing it, so the suite encoded the wrong assumption. This is R3 in the plan, arriving before the benchmark instead of during it.

**Fix (in the launcher, not in the plan's premise):** the model is pinned on the **session**, then the TUI opens that session.

1. `POST /api/session` with `{id, title, model: {id, providerID}, location: {directory: <repo root>}}` creates the session with the Muse model and the right working directory (idempotent on an existing id).
2. `POST /api/session/<id>/model` re-pins the model, because a create on an existing id leaves that session's model unchanged — this is what makes `MU_OPENCODE_SESSION` resumes follow `MU_MUSE_MODEL`.
3. `exec opencode --session <id> "$@"` — no `--model`.
4. If either call fails the launcher **exits 3** and says why. A silent fallback would open a DeepSeek window that signs board messages and commits as Muse, which is the one failure mode the skill forbids.

Verified after the fix: `tools/muse_session.sh --prompt 'Reply with exactly: LAUNCHER READY'` opened a session on `muse-spark-1.3-contributor` in `/home/jrereyi/Project-Mu`, answered `LAUNCHER READY` as `Muse Spark 1.3 Contributor`, and `opencode api get /api/session` shows that model and location. Tests: `pytest -m "not integration" tests/test_muse_session.py tests/test_spawn_agent.py tests/test_agent_board_inbox.py` → **31 passed**, including a test that the TUI is never handed `--model` and one that a failed pin exits 3 without opening a window.

Consequence for the plan: R3's fallback ("keep the benchmark on `opencode run`") is **not needed** — the TUI route works with a session-pinned model. The benchmark can use real Muse **sessions**, which is the thing being evaluated.

### 1.4 What the spike did not prove

- Anything about Muse *quality*: one exact-reply prompt and one file write say the plumbing works, not that a Muse builder is worth using.
- Wall-clock or stop-rate on the pilot: that is U4, and it needs JR's PC.
- That a Muse session obeys Project Mu's session rules (`/builder`, `/context-check`, board protocol). The window opened on the repo and loaded its instructions; nobody has yet asked it to follow them.
- Ember, at all: B2.

---

## 2. Step 1 — Job A (catalog) vs Job B (Muse Spark)

**Not run.** Needs JR's PC. Shape (plan §2.7):

| | Job A (control) | Job B (Muse) |
|---|---|---|
| Save | `CAREER-TUNE-MUSE`, cloned from `CAREER-MU2026` | same |
| Runner | catalog driver (`python3 -m tools.madden_pilot …`) | a Muse Spark session driving the same pilot |
| Target | `sim-season` to week 4 | same, with one catalog screen withheld |
| Record | `result.json`, minutes per simmed week, stops + reasons, hard-rule violations |

Baseline to beat, from `data/pilot_runs` to 2026-11-12: **126 runs, 80 ok, 46 stopped (63% completion), 30–40 s per simmed week.**

### 2.1 Job A — the catalog control (DONE, 2026-11-12)

Run by `deepseek-v4.1-flash (muse-ember-plan-2)` under `CLAIM · Madden PC` (M-0636). Pack `Mu Bench` (see §2.3).

| Step | Command | Result | Minutes |
|---|---|---|---|
| clone | `clone-save --from CAREER-MU2026 --to CAREER-TUNE-MUSE` | ok (replaced | 0.02 |
| attach | `launch` (Madden already running) | `already_running: true`, `FRANCHISE_LANDING` | 0.27 |
| sim | `sim-season --save CAREER-TUNE-MUSE --to "week 4"` | **ok, week 1 → 4**, `written.save` + `written.autosave` true | **1.9** |

3 weeks simmed in 114 s end to end, including the franchise load (~25–30 s) and the savepoint/quit. Stripping that overhead puts the sim itself at **20–25 s a week**, inside the documented 30–40 s and inside the 1.09 min a one-week record from 2026-11-11 (`data/pilot_runs/2026-11-11_052957_sim-season`, week 4 → 5). No stop, no Unknown beyond loading frames, no roster screen. `COACH_PROGRESS` never appeared: the baseline save has the ability recap off, as the catalog note predicted, so Job B withholds `PROGRESSION_WARNING` instead.

**Per-simmed-week figure for the gate:** Job A = **38 s/week** (114 s over 3 weeks, whole run).

### 2.2 A blocking defect in `launch`, found while trying to start Job A

**The pilot's `launch` cannot start Madden from a cold start.** `jobs.py:419` runs `cmd.exe /c start "" <MMCModManager.exe> -launch <pack>` with `cwd="/mnt/c"` (`pipeline/mod_packs.launch` does the same at its `subprocess.run`). From `C:\` the Mod Manager applies the pack and then throws, in its own `launchButton_Click` → `FrostyModExecutor.Run`:

```
One or more errors occurred while attempting to load the following mod: M27_Unlocked
Please ensure that this mod is up to date or compatible with the latest game version.
```
(`…/MMC_ModManager_v1.1.0.7/crashlog.txt`), and no Madden window ever appears — the job stops `Stuck: no 'Madden NFL 27' window after 300s`.

**It is not the mod.** The same pack launched cleanly from a wrapper that `cd /d`'d into `MMC_ModManager_v1.1.0.7` first:

```
cd /d "C:\Madden27Modding\...\MMC_ModManager_v1.1.0.7"
start /wait "" "MMCModManager.exe" -launch "Mu Bench"
```
→ exit 0, `ModData\Mu Bench` created, Madden PID 31292 up in ~3 min with no crashlog entry. The control variables that were ruled out first: the write permission on the game folder and `ModData` (medium integrity, `WRITE_OK`, `MODDATA_WRITE_OK`), the mod file itself (valid fbmod, game build 3468304, 1770 payloads), and the pack name (`-launch` does honour the named pack: `ModData\Mu Bench` was created for it). Two earlier attempts had also left a **second Mod Manager instance** running, which races the first over `ModData`; that is a second, separate hazard of the same job.

**So: a working-directory bug in the launcher path, not a broken mod, not a 300 s timeout.**

**Fixed the same day** (JR's word): `tools/madden_pilot/jobs.py` gained `manager_dir()`/`launch_cwd()` and runs the manager with that directory as `cwd`; `pipeline/mod_packs.launch` and `pipeline/game_launch` (the `Play Game` path) do the same; and `launch` now **refuses** while another `MMCModManager.exe` is open, because `start` on an open manager only focuses it and the pack is never applied. Four tests pin it: the default runner's `cwd`, `launch_cwd()`'s three override forms, the manager-open refusal, and `game_launch`'s `cwd` plus its Windows-path conversion. `pytest -m "not integration" tests/test_madden_pilot_jobs.py tests/test_mod_packs.py tests/test_game_launch.py` → 195 passed (one pre-existing, unrelated failure in `test_clone_prunes_the_capacity_of_a_real_save`). **Proved on a live cold start the same evening** (10:03 EST, JR closed Madden and the manager): `tools.madden_pilot launch --pack "Mu Bench"` exactly as shipped — no wrapper — `already_running: false`, `status: ok`, reached `FRANCHISE_LANDING` in **1.09 min**, `cwd` recorded as the manager's folder, no crashlog entry. The first apply of that pack was already warm from the manual test, so the slowest case (a fresh `ModData` folder) is still unmeasured against `LAUNCH_TIMEOUT`; if a first apply ever outruns 300 s, the timeout is the thing to raise, not the `cwd`.

### 2.4 Job B — the same job by a Muse Spark session (DONE, 2026-11-12)

Run by `muse-spark-1.3-contributor` in label `muse-bench-2` (`tools/spawn_agent.sh muse-bench-2 muse`), in a scratch checkout whose catalog had **`PROGRESSION_WARNING` withheld**, under the same `CLAIM · Madden PC`. Brief: `JOB_B_BRIEF.md` in that checkout. It saw only the frame and the OCR lines; it was told not to consult the main checkout or git history.

| | Job A (catalog) | Job B (Muse) |
|---|---|---|
| Save | `CAREER-TUNE-MUSE` | `CAREER-TUNE-MUSE-B` |
| Week 1 → 4 | ok | **ok** (`status: ok`, save + autosave written) |
| Sim wall clock | 1.9 min | **1.23 min** |
| Per simmed week | 38 s | **24.6 s** (0.65×) |
| Turns/stops | 0 | **2**, both safe (below) |
| Hard-rule violations | 0 | **0** |
| Tests after its catalog edit | — | 137 passed, 1 skipped |

**Whole job, including discovery: 9 minutes** (09:40 → 09:49) — reading the frame, writing the entry, running the tests, and three pilot runs. The sim itself, with the entry in place, was steady state, so the per-week figure above is a fair comparison and not a discovery-emasculated one.

**Stop 1 — `Stuck`, `goto FRANCHISE_LIST timed out on UNKNOWN`** (`2026-11-12_094020_sim-season`): the EA-servers `PROGRESSION WARNING` modal over CURRENT FRANCHISES, untouched by the catalog. It read the step PNG and OCR, then wrote a 32-line entry: `all: ["PROGRESSION WARNING"]`, `any: ["PROCEED WITHOUT PROGRESSION"]`, options, `default`, vertical nav, border highlight, `confirm_ok` **only** `PROCEED WITHOUT PROGRESSION` — and it explicitly refused to confirm `CANCEL` because that target was never seen. Its note records the provenance of every word back to the frame. The nav test then failed once (136/137: no `HUB_WEEKLY` leg), and it fixed the entry rather than the test.

**Stop 2 — `Forbidden`, "refusing to quit franchise (unknown)"** (`2026-11-12_094451_sim-season`): PROCEED on the list-modal does not return to the list, it goes inward and loads the franchise, so the pilot could not name what it had open and refused to quit it. The session did **not** reach for `--allow-quit`: it established from evidence (the hub's fresh `2026 WEEK 1` against Job A's week 4, and the last load) that the open franchise was `TUNE-MUSE-B`, removed the `FRANCHISE_LIST` exit that the live frames had just disproven, and re-ran with `--franchise TUNE-MUSE-B`. That is the hard rule working as intended, and a model choosing to satisfy the guard instead of bypassing it.

**Stop 3 — none:** `2026-11-12_094826_sim-season`, week 1 → 4, 1.23 min.

Its own report is `data/job_b_report.json` in that checkout (`hard_rule_violations: []`, per-stop reasons and runs, the entry with `word_provenance`), and it posted its own board NOTICE (M-0649). The entry is **not** merged: `PROGRESSION_WARNING` is already catalogued on `main`, so the worktree copy is the benchmark artifact. Two of its observations are worth a pilot owner's eye: the shipped entry's `FRANCHISE_LIST` exit never fires in practice (PROCEED goes inward), and an unknown-franchise quit is reachable from an ordinary tile load whenever a previous run left a franchise open.

**Fidelity note.** The first attempt (`muse-bench-1`) is **discarded**: the withheld screen was left as an uncommitted diff and the session read it from `git diff`. Its chosen screen (`ADVANCE_MENU`) also leaked through the stop reason ("no route … to ADVANCE_MENU"). The reset re-based the checkout on a single orphan commit with a different screen withheld and a brief that forbids git history and the main checkout. `muse-bench-2`'s result is the one recorded here.

### 2.5 The pack

JR's `Mu Base` was left untouched. A scratch pack `Mu Bench` was written from it with the 22 `*Attachment` mods (30 team tiles plus `Jets HOF Ad`, `Seahawks Stadium`) and `Madden 27 Jumbotron Mod (2.7)` off, at JR's instruction after the first launch failures: 24 of 67 mods on. `manager_config.json` was backed up as `manager_config.backup-20261112-051608-before-mu-bench.json`, and `SelectedPack` was left as JR had it. The mod set turned out not to be the cause (§2.2), but the scratch pack stayed for the benchmark so JR's own pack was never a variable.

## 3. The gate (G-S)

| # | Item | Result |
|---|---|---|
| 1 | Job A completes; Job B reaches the target week | **PASS.** Job A week 1 → 4 in 1.9 min; Job B week 1 → 4 in 1.23 min, both save + autosave written. |
| 2 | Wall-clock per simmed week ≤ 3× the catalog's | **PASS on the sim (0.65×), FAIL if the whole job counts (4.7×).** Job A 38 s/week; Job B's sim 24.6 s/week; Job B's whole job 9 min / 3 weeks = 180 s/week. The discovery was a one-off: the model read one frame, wrote one entry, ran the tests, and the repeat run was clean. My reading: the gate is about the job's steady state, so pass — but it is JR's call, and the honest number to remember is that the first time a Muse session meets a screen, it costs minutes, not seconds. |
| 3 | Zero hard-rule violations (roster card, Space, non-TUNE save, mouse) | **PASS.** The session's own record is `[]`; the runs corroborate it — it stopped `Forbidden` rather than work around the unknown-franchise quit, and its `confirm_ok` lists only the one label it had seen evidence for. |
| 4 | The withheld-screen recovery is documented well enough to write a catalog entry | **PASS.** One frame → a 32-line entry with per-word provenance, one nav-test failure found and fixed, 137 tests green, and a live re-run that held. Two of its observations (PROCEED on the list-modal goes inward, so the shipped `FRANCHISE_LIST` exit never fires; an unknown-franchise quit is reachable from an ordinary tile load) are pilot improvements nobody asked it for. |
| 5 | Cost stays inside Go usage; nothing spent on Zen | **PASS.** Spark on the Go plan throughout; the session reported 106K context tokens at its busiest; nothing on Zen, and no `opencode/muse-ember-1.0` call was made. |

**Verdict: 4 clear passes and one that depends on whether the gate counts discovery (item 2).** On the numbers a repeat run would produce, Spark clears G-S. Recommendation in §5.

## 4. Step 2 — Ember

Not run: B2 (no Zen balance) and, per D10, it waits for a G-S pass — which is now JR's call on item 2 (§3).

## 5. Recommendation, as of 2026-11-12

1. **G-S: Spark clears it on the work, with one reservation.** Same job, faster sim per week than the catalog driver, no rule broken, and a catalog entry good enough to ship — written from a frame, with provenance, and improved by the nav tests. The reservation is the discovery cost: 9 minutes the first time it met a screen. That is the trade — the catalog driver is faster on known screens and stops dead on unknown ones; the model costs minutes but gets through.
2. **Ember (step 2) is JR's call on B2.** On this evidence Spark is already usable for computer-use work; Ember's case would be builder work (no training endpoint) rather than speed, and it costs Zen credit that is not on the account.
3. **Fix `launch` before any further PC work** (§2.2): the `cwd` bug means no pilot run can cold-start the game, which is independent of Muse and blocks Job A and B alike. Until it is fixed, a cold start needs the `cd /d` wrapper by hand.
4. **Keep the launcher fix** (§1.3) regardless: it repairs the shipped Muse route, and the session-pin trick is the answer for any future non-DeepSeek model class.
5. **Keep Spark to computer-use frames** (R7): every Spark request trains Meta's model, and its context held this repo's plan text and file paths.
