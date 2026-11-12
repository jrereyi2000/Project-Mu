# Project Mu: Muse Ember evaluation

**Status:** step 1 not run. §1 (spike S-M1) is **done** — 2026-11-12, by `deepseek-v4.1-flash (muse-ember-plan-2)`. §2–§5 fill in when JR gives the word and the Madden PC for U4.
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

## 3. The gate (G-S)

| # | Item | Result |
|---|---|---|
| 1 | Job A completes; Job B reaches the target week | not run |
| 2 | Wall-clock per simmed week ≤ 3× the catalog's | not run |
| 3 | Zero hard-rule violations (roster card, Space, non-TUNE save, mouse) | not run |
| 4 | The withheld-screen recovery is documented well enough to write a catalog entry | not run |
| 5 | Cost stays inside Go usage; nothing spent on Zen | held so far (§1.2) |

**Verdict: not yet decided.**

## 4. Step 2 — Ember

Not run: B2 (no Zen balance) and no G-S pass. Nothing has been spent on Zen.

## 5. Recommendation, as of 2026-11-12

1. **Proceed to U4 step 1** on JR's word and PC. The route is verified; what is unmeasured is whether the model is good and fast enough, which only the live job answers.
2. **Do not top up Zen for Ember yet** (B2): D10 makes it conditional on G-S, and Spark has not run.
3. **Keep the launcher fix** regardless of the benchmark's outcome — it repairs the shipped Muse route, and the same session-pin trick is the answer for any future non-DeepSeek model class in OpenCode.
4. **Keep Spark to computer-use frames** until the result justifies more (R7): every Spark request trains Meta's model, and this repo's source and plan text are in its context.
