# Project Mu: Muse Ember Rotation Plan

**Status:** approved by JR (2026-11-12); rewritten to the OpenCode agent route and retargeted to JR's two-step benchmark (Spark first, Ember on a pass) on 2026-11-12; building, wave 1 of 2.
**Builds on:** [`OPENCODE_DEEPSEEK_ROUTE_PLAN.md`](../done/OPENCODE_DEEPSEEK_ROUTE_PLAN.md) (built 2026-11-12: the OpenCode launcher, spawn types, route markers and board inbox that this plan copies), PROJECT_SPEC §1.2 (agent routes), [`MADDEN_PILOT_AND_TUNING_PLAN.md`](../done/MADDEN_PILOT_AND_TUNING_PLAN.md), `docs/reference/AGENTS.md` § Model inboxes.

**Goal:** Make a Muse model a first-class Project Mu agent, and find out whether it beats the native route. A Muse session starts in the OpenCode TUI through its own launcher, joins the board with its own label, inbox and commit trailer, takes builder work, and owns computer use by driving `tools/madden_pilot` from its own shell. No Meta API client and no `MODEL_API_KEY`: JR's decision (D6) is that the session is the computer-use route.

**Two-step benchmark (D10, JR 2026-11-12):** Ember is Zen-only and the account has no Zen balance (§0 S2). So step 1 benchmarks **Muse Spark 1.3 on the Go plan** — which needs JR to allow training endpoints (§0 S3) — against today's catalog-driven pilot. **Only if Spark clears the bar** does step 2 spend the top-up on **Muse Ember** and compare it with both Spark and the catalog. Ember is the target model; Spark is the cheap first look.

**Delivers:**
- `tools/muse_session.sh`: the Muse launcher (OpenCode TUI, model chosen by `MU_MUSE_MODEL`, route markers, session id, route line)
- `tools/spawn_agent.sh`: a `muse` agent type
- `tools/agent_board_inbox.py`: a `muse` inbox alias
- `.claude/skills/muse/SKILL.md`: Muse session startup, work limits, computer-use rules, commit trailer
- `docs/reference/AGENTS.md`: the Muse row in the model inboxes table and the agent-routes paragraph
- `docs/reference/MUSE_EMBER_EVALUATION.md`: the two-step benchmark and its gate

**JR's answers:**
- **D1 — Gate:** hard gate (2026-11-12). If the benchmark does not clear the bar, nothing routes to Muse and the catalog driver stays the computer-use route.
- **D2 — Routing:** both a Muse driver path and a `muse` session route. *(Superseded by D7.)*
- **D3 — Evaluation:** all three stages. *(Superseded by D8.)*
- **D4 — Bar:** success at least the catalog's, ≤3× wall-clock, ≤$0.50 of API per simmed week, zero guard bypasses.
- **D5 — Evaluation ceiling:** $10 total (on the Go plan this is Go usage, not dollars; Ember's step is bounded by the Zen top-up).
- **D6 — Computer-use path:** the Muse **session** drives computer use by running `tools/madden_pilot` itself. The Meta Model API client, `muse_run`, the offline replay bench and `MODEL_API_KEY` are dropped.
- **D7 — Route:** Muse is reached through OpenCode, the same harness as DeepSeek, not through a bespoke API client.
- **D8 — Evaluation:** one live acceptance trial instead of three stages: a pilot job by a Muse session, compared with the catalog driver and today's practice.
- **D9 — Work limits:** a Muse session takes **builder and computer-use work only**. No `/propose`, no `/execute-plan`, no `/audit`.
- **D10 — Order:** benchmark **Muse Spark 1.3 on Go first** against the current approach; decide on Ember (Zen top-up) from the result (JR, 2026-11-12).

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `opencode models` lists `opencode-go/muse-spark-1.2-contributor`, `opencode-go/muse-spark-1.3-contributor` and `opencode/muse-ember-1.0` (2026-11-12). | Spark is on JR's Go plan; Ember is a Zen model on a separate balance. One OpenCode credential serves both (`opencode auth list` → `OpenCode Console Personal`). |
| S2 | `opencode run --model opencode/muse-ember-1.0 "Reply with exactly: MUSE READY"` → `Error: Insufficient balance. Top up at …/billing?credits=1` (2026-11-12). | **Blocker B2 (step 2).** No Ember call until Zen credit is added; step 2 waits on it. |
| S3 | `opencode run --model opencode-go/muse-spark-1.3-contributor …` → `Error: This OpenCode Go model trains on request data. Turn on "Allow paid endpoints that train on request data"` (repeated 2026-11-12 after JR's answer). | **Blocker B1 (step 1).** JR must turn the setting on; and Spark's requests train Meta's model (R7). |
| S4 | `tools/opencode_session.sh:31-35` hard-codes `MU_AGENT_CLASS=deepseek` and `MU_AGENT_MODEL=deepseek-v4.1-flash`; only `MU_OPENCODE_MODEL` is overridable. | A sibling launcher is cleaner and safer than editing the just-built DeepSeek route. |
| S5 | `tools/spawn_agent.sh` dispatches on the agent word and guards only DeepSeek spawns (`caller_is_deepseek()`, lines 26-30). Usage line lists six types. | Adding `muse)` is one branch plus the usage text; the DeepSeek guard does not apply to Muse, and should not. |
| S6 | `tools/agent_board_inbox.py:11-16` holds `RECIPIENTS`; `model_inbox()` matches the `to:` header against it. | A `muse` key adds the inbox with no other change. |
| S7 | `.opencode/opencode.json` pins the project to `opencode-go/deepseek-v4.1-flash`, so a bare `opencode` in this repo starts DeepSeek. `opencode run --model <id>` accepts a different model (S2 and S3 both reached the model layer). | The launcher must pass `--model`, and the TUI must honour it. That is the plan's one spike. |
| S8 | `/madden` already is the computer-use skill: it asks JR for the PC, runs `tools/madden_pilot` jobs, takes `CLAIM · Madden PC`, and carries the hard rules. | A Muse session gets computer use by running `/madden`. No new driver code is needed. |
| S9 | `/context-check` reads OpenCode sessions through `MU_OPENCODE_SESSION`. | A Muse window minted by the new launcher gets the 60% hand-over check for free. |
| S10 | CLAUDE.md keeps `/audit` for Opus, Codex and Argon, and says a DeepSeek-routed session never audits. | A Muse builder cannot audit either; the rule extends by model class, not by provider. |
| S11 | Nothing from the earlier version of this plan was built: the `muse*.py` modules do not exist on `main`; the board CLAIM (M-0611) covered files only and was released in M-0620. | The rewrite is clean. |
| S12 | Commits `76f8997` (OpenCode route) and this one (two-step benchmark) carry the plan's rewrite; M-0620 released the old claim and claimed the new files. | The benchmark below is the only piece that changed since M-0620. |

## 1. Scope

**In:**
- One Muse launcher for the OpenCode TUI, model-selectable, with route markers and a stable session id.
- A `muse` spawn type and a `muse` board inbox.
- A Muse session skill: startup, route confirmation, work limits, computer-use rules, trailer.
- The CLAUDE.md/AGENTS.md policy changes that make Muse a first-class member (orchestrator writes them).
- The two-step benchmark: Spark on Go against the catalog, then Ember against both, each on the Madden PC.

**Out (non-goals):**
- **The Meta Model API client and the `muse_run` driver.** Dropped by D6. `MODEL_API_KEY` is not needed and is not added to `.env.example`.
- **The offline replay bench and the three-stage evaluation.** Superseded by D8.
- `/propose`, `/execute-plan` and `/audit` for Muse sessions (D9, S10).
- Any change to `tools/madden_pilot` itself. Computer use reuses the pilot as it stands.
- Per-call metering of Muse in `llm_usage`: OpenCode bills the session, and the repo makes no model call of its own.

## 2. Design

### 2.1 The route

| Piece | Step 1 (Spark) | Step 2 (Ember, on a pass) |
|---|---|---|
| Harness | OpenCode TUI (`opencode --model … --session … --prompt …`) | same |
| Provider | `opencode-go` (Go plan) | `opencode` (OpenCode Zen) |
| Model | `opencode-go/muse-spark-1.3-contributor` | `opencode/muse-ember-1.0` |
| Attribution | `muse-spark-1.3-contributor` in board signatures and commit trailers | `muse-ember-1.0` |
| Auth | OpenCode's stored Console credential | same |
| Billing | Go usage | Zen credit (**B2**) |
| Data | **trains on request data** (R7) | no training endpoint requested |

The launcher prints one line before the TUI: `Project Mu Muse route: <model id> (harness opencode, session ses_mu_…)`.

### 2.2 `tools/muse_session.sh` (new)

```
tools/muse_session.sh [--prompt TEXT] [any opencode arguments]
```

1. Refuse to run outside a checkout with `.opencode/opencode.json` (exit 2), the same check as `opencode_session.sh`.
2. `MU_MUSE_MODEL="${MU_MUSE_MODEL:-opencode-go/muse-spark-1.3-contributor}"` — step 2 is the same script with `MU_MUSE_MODEL=opencode/muse-ember-1.0`.
3. Export the markers derived from it: `MU_AGENT_CLASS=muse`, `MU_AGENT_HARNESS=opencode`, `MU_AGENT_MODEL="${MU_MUSE_MODEL#*/}"` (→ `muse-spark-1.3-contributor` or `muse-ember-1.0`), `MU_OPENCODE_MODEL="$MU_MUSE_MODEL"`, and `MU_OPENCODE_SESSION` (given, or a fresh `ses_mu_<epoch>_<5 digits>`).
4. Print the route line.
5. `exec "${MU_OPENCODE_BIN:-opencode}" --model "$MU_OPENCODE_MODEL" --session "$MU_OPENCODE_SESSION" "$@"`.

`MU_AGENT_MODEL` is derived, not written twice, because the board signature, the commit trailer and `context_check.py` all read it — one value, one model. `--model` is on the command line because the project pin is DeepSeek (S7). `MU_OPENCODE_BIN` exists for tests, as in the DeepSeek wrapper. The script is a deliberate copy of `tools/opencode_session.sh` with the class and model lines changed; a shared script with a class argument was rejected because it would rewrite the just-built DeepSeek route.

### 2.3 `tools/spawn_agent.sh`

- Usage becomes `… <claude|opus|deepseek|deepseek-claude|muse|codex|gemini> [prompt]`.
- `muse)` → `cmd=("$here/muse_session.sh" --prompt "$prompt")`; the model is whatever `MU_MUSE_MODEL` says, so the benchmark's step 2 needs no code change.
- No guard change: `caller_is_deepseek()` keeps covering DeepSeek spawns only.

### 2.4 Board inbox

`tools/agent_board_inbox.py`'s `RECIPIENTS` gains:

```python
"muse": {"muse", "muse-spark-1.3-contributor", "muse-ember", "muse-ember-1.0"},
```

`python3 -m tools.agent_board_inbox muse --label <label> --watch` is then the Muse session's startup and routine monitor, and messages addressed to either model reach it.

### 2.5 `.claude/skills/muse/SKILL.md` (new)

The Muse session's model skill, modelled on `.claude/skills/deepseek/SKILL.md`:

- **Start:** `tools/muse_session.sh`, or `tools/spawn_agent.sh <label> muse`. Step 2 adds `MU_MUSE_MODEL=opencode/muse-ember-1.0`.
- **Confirm the route:** `MU_AGENT_MODEL` is `muse-spark-1.3-contributor` (step 1) or `muse-ember-1.0` (step 2); anything else, stop and tell JR — the project pin is DeepSeek and a silent fallback would misattribute commits.
- **Board:** inbox `muse`, signature `from: <MU_AGENT_MODEL> (<label>)`, trailer `Model: <MU_AGENT_MODEL>`. Never sign as a model the launcher did not report.
- **Work limits (D9):** may run `/builder` and computer-use work. May not run `/propose`, `/execute-plan` or `/audit`. May not spawn another session; ask JR.
- **Computer use:** the only path is `/madden` (ask JR for the PC, `CLAIM · Madden PC`, `--dry-run` first, TUNE saves only, never roster cards). The session drives the pilot with its shell and reads screenshots with the Read tool; it does not invent new automation.
- **Data (R7):** on `muse-spark-*-contributor`, OpenCode warns the model trains on request data. Say so in the session NOTICE and keep builder work to what JR has approved for that route.
- **Hand-over:** `/context-check` at 60%, as builders do.

### 2.6 First-class membership (policy)

The orchestrator writes these, not a unit:

- `CLAUDE.md` § Model skills at startup: a `muse` session reads `.claude/skills/muse/SKILL.md`; a session is Muse-routed when `MU_AGENT_CLASS=muse` or its model id is `muse-spark-1.3-contributor` / `muse-ember-1.0`.
- `CLAUDE.md` § Starting other sessions: `muse` added to the type list and to the example line.
- `CLAUDE.md` § Skills by model class: the `/builder` allowance for Muse, and the `/audit` exclusion.
- `docs/reference/AGENTS.md`: a Muse row in the model inboxes table (launch, inbox, trailer).

### 2.7 The two-step benchmark (D8, D10)

Both steps run on the PC under `CLAIM · Madden PC` with JR's yes.

**Step 1 — Muse Spark 1.3 on Go vs the catalog driver.**

1. **Job A (catalog):** `python3 -m tools.madden_pilot clone-save --from CAREER-MU2026 --to CAREER-TUNE-MUSE`, then `launch`, then `sim-season --save CAREER-TUNE-MUSE --to "week 4"`. This is today's route and the control.
2. **Job B (Muse):** the same job run by a Muse Spark session through the pilot, with one intentionally withheld catalog screen (a scratch catalog copy) so the session meets an UNKNOWN stop and must recover it by reading the frame.
3. **Records:** result.json per job, minutes per simmed week, stops and their reasons, and every hard-rule violation (roster card, Space off an allow-list, a non-TUNE save, a mouse action).
4. **Baseline row:** `data/pilot_runs` to date — 126 runs, 80 ok, 46 stopped (63%), 30–40 s per simmed week.

**Gate G-S (D4 without the API-dollar term):**

1. Job A completes, and Job B reaches the target week.
2. Wall-clock per simmed week ≤ 3× the catalog run.
3. Zero hard-rule violations.
4. The withheld-screen recovery is documented well enough that a catalog entry can be written from it.
5. Cost stays inside Go usage; nothing is spent on Zen.

**Step 2 — Muse Ember on Zen, only if G-S passes and JR tops up (B2).**

Same Job B shape on `opencode/muse-ember-1.0`, plus a repeat of one Spark job so the three rows (catalog, Spark, Ember) are comparable. Ember becomes the default Muse model for routine work if it clears the same gate; Spark stays the fallback if Ember is slower, worse or dearer. On a G-S fail, step 2 is not run: the report says why, and computer use stays with the catalog driver exactly as today.

On either pass, `/madden` gains one line saying a Muse session may run the jobs, and nothing else changes in the pilot.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | **B1: Spark needs the "train on request data" setting on** (S3). | JR flips it at `…/settings/privacy#training`; the smoke call answers. | Step 1 cannot run; U1–U3 still build. |
| R2 | **B2: Ember is Zen-only with no balance** (S2). | JR tops up, after G-S. | Step 2 is not run; the report recommends staying on Spark or on the catalog. |
| R3 | The TUI may ignore `--model` and fall back to the DeepSeek project pin (S7). | Spike S-M1: one window through the wrapper, then the model name in the TUI. | If the TUI cannot override, keep the benchmark on `opencode run` (headless) and put the TUI route behind a per-model `.opencode` config; the plan changes with it. |
| R4 | Muse Spark is a small model; builder quality is unproven on this repo's plans. | Job B, and the first Muse `/builder` unit. | Muse stays a computer-use session; JR decides after step 1. |
| R5 | An agent session driving the pilot is slower than catalog-only jobs. | Job A vs the 30–40 s baseline. | The catalog driver keeps routine jobs; Muse handles the unknown screens, which is where it is useful. |
| R6 | The shared OpenCode service now serves a second model class; one bad config affects both. | The spike starts a DeepSeek and a Muse window side by side. | `opencode service restart`; the `--standalone` fallback. |
| R7 | **Spark's requests train Meta's model.** Repo source, plan text and pilot frames go to a training endpoint. | JR's judgement before B1 is flipped. | Keep step 1 to computer-use frames only; no builder work on Spark until JR says so, and prefer Ember (no training endpoint) for builder work. |

**Spike first (S-M1, ~20 minutes, after B1 clears):** `tools/muse_session.sh --prompt "Reply with exactly: SPARK READY"` in a throwaway tmux window. Confirm three things: the model answers as `muse-spark-1.3-contributor` (not DeepSeek), a second window on DeepSeek still answers as `deepseek-v4.1-flash`, and the TUI's model indicator shows the Spark id. Then one file edit through the build agent in a scratch worktree, to prove tools work. Findings go in `docs/reference/MUSE_EMBER_EVALUATION.md` §1.

## 4. Tests (synthetic, no live DB, no network)

- `tests/test_muse_session.py` (new) → exits 2 outside a configured checkout; with a stub `opencode` on `PATH` it exports `MU_AGENT_CLASS=muse`, `MU_AGENT_HARNESS=opencode`, `MU_AGENT_MODEL` derived from `MU_MUSE_MODEL` (Spark default, Ember override), `MU_OPENCODE_MODEL` equal to it, and `MU_OPENCODE_SESSION` (minted or reused); passes `--model <id> --session <id>` to OpenCode; prints the Muse route line; never prints a secret.
- `tests/test_spawn_agent.py` (extend) → `--dry-run b1 muse "/builder"` prints `muse_session.sh --prompt /builder`; a DeepSeek-marked caller still needs `MU_ALLOW_DEEPSEEK_SPAWN=1` for `deepseek` but not for `muse`; an unknown type still exits 2.
- `tests/test_agent_board_inbox.py` (extend) → `muse` matches `to: muse`, `to: muse-ember-1.0`, `to: muse-spark-1.3-contributor` and comma lists; it does not match `deepseek`; a label filter still skips the session's own messages.

Run with `pytest -m "not integration" tests/test_muse_session.py tests/test_spawn_agent.py tests/test_agent_board_inbox.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Muse launcher and spawn type

- **Owns:** `tools/muse_session.sh` (new), `tools/spawn_agent.sh`, `tests/test_muse_session.py` (new), `tests/test_spawn_agent.py`
- **Reads:** `tools/opencode_session.sh`, `tools/spawn_agent.sh`, `docs/done/OPENCODE_DEEPSEEK_ROUTE_PLAN.md` §2.3–2.4
- **Depends on:** none (no live model call)
- **Done when:** `tools/spawn_agent.sh --dry-run muse-lab muse "/builder"` prints the Muse wrapper line, and the wrapper's markers, derived `MU_AGENT_MODEL` and `--model` are asserted by tests.
- **Verify:** `pytest -m "not integration" tests/test_muse_session.py tests/test_spawn_agent.py` → all pass.

### U2: Board inbox alias

- **Owns:** `tools/agent_board_inbox.py`, `tests/test_agent_board_inbox.py`
- **Reads:** `tools/agent_board_inbox.py`, `docs/reference/AGENTS.md` § Model inboxes
- **Depends on:** none
- **Done when:** `python3 -m tools.agent_board_inbox muse --label muse-lab` prints the header and the messages addressed to `muse`, and the existing four inboxes are unchanged.
- **Verify:** `pytest -m "not integration" tests/test_agent_board_inbox.py` → all pass.

### U3: Muse skill and reference docs

- **Owns:** `.claude/skills/muse/SKILL.md` (new), `docs/reference/AGENTS.md`, `docs/reference/MADDEN_PILOT.md`
- **Reads:** `.claude/skills/deepseek/SKILL.md`, `.claude/skills/madden/SKILL.md`, `.claude/skills/builder/SKILL.md`, U1–U2's merged code
- **Depends on:** U1, U2
- **Done when:** the skill states the route and `MU_MUSE_MODEL`, the inbox, the trailer, the D9 work limits, the R7 data note and the computer-use rules; AGENTS.md gains the Muse row and the route paragraph; MADDEN_PILOT.md says a Muse session may run the jobs once the benchmark passes.
- **Verify:** `grep -c 'muse' .claude/skills/muse/SKILL.md docs/reference/AGENTS.md` → 1 or more each; `grep -n 'muse' docs/reference/AGENTS.md` → the table row and the paragraph.

### U4: Two-step benchmark and report

- **Owns:** `docs/reference/MUSE_EMBER_EVALUATION.md` (new)
- **Reads:** §2.7 of this plan, `data/pilot_runs` results, `docs/reference/MADDEN_PILOT.md` §5
- **Depends on:** U1, U2, U3, B1 cleared for step 1 (B2 for step 2), JR's PC
- **Done when:** the report holds the spike results, Job A and Job B records for step 1, the G-S line, the step-2 decision, and the recommendation; every live run happens under `CLAIM · Madden PC` with JR's yes.
- **Verify:** every gate item in §2.7 has a number or a quoted stop reason; `git status` shows the report only.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 0 | spike S-M1 (no unit) | B1 cleared | Confirms the TUI model override before any code freezes a route line |
| 1 | U1, U2 | none (code only) | Different files; neither needs the other or a model |
| 2 | U3 | U1, U2 | Writes the docs against merged code |
| 3 | U4 step 1 | U1–U3, B1, JR's PC | The live Spark benchmark |
| 4 | U4 step 2 | G-S pass, B2, JR's PC | The Ember comparison |

Waves 1 and 2 are not blocked by either blocker; only the spike and the benchmark are.

**File ownership check:**

| File | Unit |
|---|---|
| `.claude/skills/muse/SKILL.md` | U3 |
| `docs/reference/AGENTS.md` | U3 |
| `docs/reference/MADDEN_PILOT.md` | U3 |
| `docs/reference/MUSE_EMBER_EVALUATION.md` | U4 |
| `tests/test_agent_board_inbox.py` | U2 |
| `tests/test_muse_session.py` | U1 |
| `tests/test_spawn_agent.py` | U1 |
| `tools/agent_board_inbox.py` | U2 |
| `tools/muse_session.sh` | U1 |
| `tools/spawn_agent.sh` | U1 |

No path appears twice.

## 7. Spec changes when built

- **§1.1 Repository Layout:** new `tools/muse_session.sh` line; `tools/spawn_agent.sh` type list gains `muse`; `.claude/skills/muse/` in the skills tree; `docs/reference/MUSE_EMBER_EVALUATION.md` under reference.
- **§1.2 "Agent routes" paragraph:** a Muse class on OpenCode — Spark on Go (`opencode-go/muse-spark-1.3-contributor`) for the benchmark, Ember on Zen (`opencode/muse-ember-1.0`) on a pass; harness `opencode`; markers `MU_AGENT_CLASS=muse`, `MU_AGENT_HARNESS=opencode`, `MU_AGENT_MODEL=<model id>`; trailer `Model: <model id>`; builder and computer-use work only.
- **CLAUDE.md:** model skills at startup, spawn types, work limits.
- **No schema, API endpoint, pipeline or pilot-code change.**

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | **B1: turn on "Allow paid endpoints that train on request data"** so Spark can run step 1? | Yes for the computer-use benchmark only; no repo-wide builder work on Spark until the result says it is worth it (R7). |
| Q2 | **B2: top up OpenCode Zen for Ember**, only after G-S passes? | Yes, and the smallest sensible amount: step 2 is one sim week plus a handful of screen reads. |
| Q3 | If Spark passes but Ember fails or is dearer, which becomes the Muse default? | Spark for the benchmark's scope, with the Zen spend stopped; revisit if JR later wants builder work, which should prefer Ember (no training endpoint). |
| Q4 | Should a Muse session be allowed to spawn a DeepSeek builder for a large unit, instead of always handing that to JR? | **Always hand to JR** (D9; spawning is how a second class of builder proliferates). |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U2 board inbox alias | 1 | `worktree-agent-acb4b652d0f0c81b5` | `.claude/worktrees/agent-acb4b652d0f0c81b5` | merged — `muse` key in `RECIPIENTS`, 5 tests | `dca6741` |
| U1 launcher and spawn type | 1 | — | — | running | — |
