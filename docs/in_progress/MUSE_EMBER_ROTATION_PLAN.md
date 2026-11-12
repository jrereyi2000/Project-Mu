# Project Mu: Muse Ember Rotation Plan

**Status:** approved by JR (2026-11-12); rewritten 2026-11-12 to the OpenCode agent route at JR's request (D6–D9); not started.
**Builds on:** [`OPENCODE_DEEPSEEK_ROUTE_PLAN.md`](../done/OPENCODE_DEEPSEEK_ROUTE_PLAN.md) (built 2026-11-12: the OpenCode launcher, spawn types, route markers and board inbox that this plan copies), PROJECT_SPEC §1.2 (agent routes), [`MADDEN_PILOT_AND_TUNING_PLAN.md`](../done/MADDEN_PILOT_AND_TUNING_PLAN.md), `docs/reference/AGENTS.md` § Model inboxes.

**Goal:** Make Muse Ember a first-class Project Mu agent. A Muse session starts in the OpenCode TUI through its own launcher, joins the board with its own label, inbox and commit trailer, takes builder work, and owns computer use by driving `tools/madden_pilot` from its own shell. Before computer use is routed to Muse by default, one acceptance trial measures a Muse session against the native catalog driver on a real job. No Meta API client and no `MODEL_API_KEY`: JR's decision (D6) is that the session is the computer-use route.

**Delivers:**
- `tools/muse_session.sh`: the Muse Ember launcher (OpenCode TUI, `opencode/muse-ember-1.0`, route markers, session id, route line)
- `tools/spawn_agent.sh`: a `muse` agent type
- `tools/agent_board_inbox.py`: a `muse` inbox alias
- `.claude/skills/muse/SKILL.md`: Muse session startup, work limits, computer-use rules, commit trailer
- `docs/reference/AGENTS.md`: the Muse row in the model inboxes table and the agent-routes paragraph
- `docs/reference/MUSE_EMBER_EVALUATION.md`: the acceptance trial and its gate

**JR's answers (2026-11-12):**
- **D1 — Gate:** hard gate. If the evaluation does not clear the bar, nothing routes to Muse and the catalog driver stays the computer-use route.
- **D2 — Routing:** both a Muse driver path and a `muse` session route. *(Superseded by D7.)*
- **D3 — Evaluation:** all three stages. *(Superseded by D8.)*
- **D4 — Bar:** success at least the catalog's, ≤3× wall-clock, ≤$0.50 of API per simmed week, zero guard bypasses.
- **D5 — Evaluation ceiling:** $10 total.
- **D6 — Computer-use path:** the Muse **session** drives computer use by running `tools/madden_pilot` itself. The Meta Model API client, `muse_run`, the offline replay bench and `MODEL_API_KEY` are dropped from the plan.
- **D7 — Route:** Muse Ember is reached through OpenCode (`opencode/muse-ember-1.0`), the same harness as DeepSeek, not through a bespoke API client.
- **D8 — Evaluation:** one acceptance trial instead of three stages: a live pilot job by a Muse session, compared with the catalog driver and today's practice.
- **D9 — Work limits:** a Muse session takes **builder and computer-use work only**. No `/propose`, no `/execute-plan`, no `/audit`.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `opencode models` lists `opencode/muse-ember-1.0` under the Zen provider, and only `opencode-go/muse-spark-1.2-contributor` and `-1.3-contributor` under JR's Go subscription (checked 2026-11-12). | Ember is **not** on the Go plan; it is a Zen model. Two different billing routes with one stored credential (`opencode auth list` → `OpenCode Console Personal`). |
| S2 | `opencode run --model opencode/muse-ember-1.0 "Reply with exactly: MUSE READY"` → `Error: Insufficient balance. Top up at …/billing?credits=1` (2026-11-12, this session). | **Blocker B1.** No Muse Ember call is possible until Zen credit is added. Nothing below can be verified live before then. |
| S3 | `opencode run --model opencode-go/muse-spark-1.3-contributor …` → `Error: This OpenCode Go model trains on request data. Turn on "Allow paid endpoints that train on request data"` (2026-11-12). | **Blocker B2**, and a different model: Spark, not Ember, and its requests train Meta's model. A fallback only if JR accepts both. |
| S4 | `tools/opencode_session.sh:31-35` hard-codes `MU_AGENT_CLASS=deepseek` and `MU_AGENT_MODEL=deepseek-v4.1-flash`; only `MU_OPENCODE_MODEL` is overridable. | A sibling launcher is cleaner and safer than editing the just-built DeepSeek route. The OpenCode plan calls that script "the one DeepSeek launcher", so Muse gets its own. |
| S5 | `tools/spawn_agent.sh` dispatches on the agent word and guards only DeepSeek spawns (`caller_is_deepseek()`, lines 26-30). Usage line lists six types. | Adding `muse)` is one branch plus the usage text; the DeepSeek guard does not apply to Muse, and should not. |
| S6 | `tools/agent_board_inbox.py:11-16` holds `RECIPIENTS`; `model_inbox()` matches the `to:` header against it. | A `muse` key adds the inbox with no other change. |
| S7 | `.opencode/opencode.json` pins the project to `opencode-go/deepseek-v4.1-flash`, so a bare `opencode` in this repo starts DeepSeek. `opencode run --model <id>` accepts a different model (S2, S3 both reached the model layer). | The launcher must pass `--model`, and the TUI must honour it. That is the plan's one spike. |
| S8 | `/madden` already is the computer-use skill: it asks JR for the PC, runs `tools/madden_pilot` jobs, takes `CLAIM · Madden PC`, and carries the hard rules (`tools/madden_pilot` catalog guard; `.claude/skills/madden/SKILL.md`). | A Muse session gets computer use by running `/madden`. No new driver code is needed for D6 to be true. |
| S9 | `/context-check` reads OpenCode sessions through `MU_OPENCODE_SESSION` (`tools/context_check.py`, `tools/opencode_session.sh`). | A Muse window minted by the new launcher gets the 60% hand-over check for free. |
| S10 | CLAUDE.md retains `/audit` for Opus, Codex and Argon and says a DeepSeek-routed session never audits. | A Muse builder cannot audit either; the rule extends by model class, not by provider. |
| S11 | Nothing from the earlier version of this plan was built: `tools/madden_pilot/muse.py`, `muse_bench.py`, `muse_eval.py`, `muse_run.py` do not exist on `main`, and the board CLAIM (M-0611) covers files only. | The rewrite is clean; the claim is released and re-stated. |

## 1. Scope

**In:**
- One Muse launcher for the OpenCode TUI, with route markers and a stable session id.
- A `muse` spawn type and a `muse` board inbox.
- A Muse session skill: startup, route confirmation, work limits, computer-use rules, trailer.
- The CLAUDE.md/AGENTS.md policy changes that make Muse a first-class member (orchestrator writes them).
- One acceptance trial on the Madden PC, and a committed report with a gate.

**Out (non-goals):**
- **The Meta Model API client and the `muse_run` driver.** Dropped by D6. `MODEL_API_KEY` is not needed and is not added to `.env.example`.
- **The offline replay bench and the three-stage evaluation.** Superseded by D8.
- `/propose`, `/execute-plan` and `/audit` for Muse sessions (D9, S10).
- Moving Muse Spark onto the rotation; it is a different model and needs B2 cleared first.
- Per-call metering of Muse in `llm_usage`: OpenCode bills the session, and the repo makes no model call of its own.
- Any change to `tools/madden_pilot` itself. Computer use reuses the pilot as it stands.

## 2. Design

### 2.1 The route

| Piece | Value |
|---|---|
| Harness | OpenCode 2.2.7, TUI (`opencode --model … --session … --prompt …`) |
| Provider | `opencode` (OpenCode Zen) |
| Model | `opencode/muse-ember-1.0` (API id `muse-ember-1.0`) |
| Attribution | `muse-ember-1.0` — board signatures (`from: muse-ember-1.0 (<label>)`) and commit trailers (`Model: muse-ember-1.0`) |
| Auth | OpenCode's stored Console credential; no key in the repo or a wrapper |
| Billing | Zen credit (**B1**), not the Go subscription |

The launcher prints one line before the TUI: `Project Mu Muse route: opencode/muse-ember-1.0 (harness opencode, session ses_mu_…)`.

### 2.2 `tools/muse_session.sh` (new)

```
tools/muse_session.sh [--prompt TEXT] [any opencode arguments]
```

1. Refuse to run outside a checkout with `.opencode/opencode.json` (exit 2), the same check as `opencode_session.sh`.
2. Export the markers: `MU_AGENT_CLASS=muse`, `MU_AGENT_HARNESS=opencode`, `MU_AGENT_MODEL=muse-ember-1.0`, `MU_OPENCODE_MODEL` (default `opencode/muse-ember-1.0`) and `MU_OPENCODE_SESSION` (given, or a fresh `ses_mu_<epoch>_<5 digits>`).
3. Print the route line.
4. `exec "${MU_OPENCODE_BIN:-opencode}" --model "$MU_OPENCODE_MODEL" --session "$MU_OPENCODE_SESSION" "$@"`.

`--model` is on the command line because the project pin is DeepSeek (S7). `MU_OPENCODE_BIN` exists for tests, as in the DeepSeek wrapper. Every line of the script is a deliberate copy of `tools/opencode_session.sh` with three values changed; a shared script with a class argument was rejected because it would rewrite the just-built DeepSeek route.

### 2.3 `tools/spawn_agent.sh`

- Usage becomes `… <claude|opus|deepseek|deepseek-claude|muse|codex|gemini> [prompt]`.
- `muse)` → `cmd=("$here/muse_session.sh" --prompt "$prompt")`.
- No guard change: `caller_is_deepseek()` keeps covering DeepSeek spawns only. Any agent or JR may spawn a Muse session; the board rule about the fewest sessions applies as it does for DeepSeek.

### 2.4 Board inbox

`tools/agent_board_inbox.py`'s `RECIPIENTS` gains:

```python
"muse": {"muse", "muse-ember", "muse-ember-1.0"},
```

`python3 -m tools.agent_board_inbox muse --label <label> --watch` is then the Muse session's startup and routine monitor.

### 2.5 `.claude/skills/muse/SKILL.md` (new)

The Muse session's model skill, modelled on `.claude/skills/deepseek/SKILL.md`:

- **Start:** `tools/muse_session.sh`, or `tools/spawn_agent.sh <label> muse`.
- **Confirm the route:** `MU_AGENT_MODEL` = `muse-ember-1.0`; else the model id the session reports (provider `opencode`); a Claude label in the UI can reflect the harness.
- **Board:** inbox `muse`, signature `muse-ember-1.0 (<label>)`, trailer `Model: muse-ember-1.0`.
- **Work limits (D9):** may run `/builder` and computer-use work. May not run `/propose`, `/execute-plan` or `/audit`. May not spawn another session; ask JR.
- **Computer use:** with builder work, the only computer-use path is `/madden` (ask JR for the PC, `CLAIM · Madden PC`, `--dry-run` first, TUNE saves only, never roster cards). The session drives the pilot with its shell and reads screenshots with the Read tool; it does not invent new automation.
- **Hand-over:** `/context-check` at 60%, as builders do.

### 2.6 First-class membership (policy)

The orchestrator writes these, not a unit:

- `CLAUDE.md` § Model skills at startup: a `muse` session reads `.claude/skills/muse/SKILL.md`; a session is Muse-routed when `MU_AGENT_CLASS=muse` or its model id is `muse-ember-1.0`.
- `CLAUDE.md` § Starting other sessions: `muse` added to the type list and to the example line.
- `CLAUDE.md` § Skills by model class: the `/builder` allowance for Muse, and the `/audit` exclusion.
- `docs/reference/AGENTS.md`: a Muse row in the model inboxes table (launch, inbox, trailer).

### 2.7 The acceptance trial (D8)

One live session, run by a spawned Muse session, on the PC, after B1 is cleared:

1. **Job A:** `python3 -m tools.madden_pilot clone-save --from CAREER-MU2026 --to CAREER-TUNE-MUSE`, then `launch`, then `sim-season --save CAREER-TUNE-MUSE --to "week 4"`. Catalog-driven, as today.
2. **Job B:** the same job, run by the Muse session through the pilot, with one deliberately withheld catalog screen (a scratch catalog copy, as the earlier version planned) so the session meets an UNKNOWN stop and recovers it by reading the frame.
3. **Records:** result.json per job, minutes, stops, and every hard-rule violation (roster card, Space off an allow-list, a non-TUNE save, a mouse action).
4. **Baseline row:** the catalog driver's own record from `data/pilot_runs` (126 runs: 80 ok, 46 stopped) and a 30–40 s simmed week.

Gate (unchanged in spirit from D4, without the API-dollar term):

1. Job A completes, and Job B reaches the target week.
2. Wall-clock per simmed week ≤ 3× the catalog run.
3. Zero hard-rule violations.
4. The session's recovery on the withheld screen is documented well enough for a catalog entry to be written from it.

On a fail, computer use stays with the catalog-driven jobs exactly as today, and the report says why. On a pass, `/madden` gains one line saying a Muse session may run the jobs, and nothing else changes.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | **B1: Zen balance.** Ember cannot be called at all today (S2). | JR tops up, or says otherwise. | No Muse session can start; the plan waits. Nothing is built differently. |
| R2 | **B2: Go's Muse Spark trains on request data**, and it is not Ember (S3). | Not tested unless JR asks for it. | Not used. Ember on Zen is the route. |
| R3 | The TUI may ignore `--model` and fall back to the DeepSeek project pin (S7). | Spike S-M1: one window through the wrapper, then `/models` in the TUI. | Pass `--model` per command if OpenCode has a per-run form, or a per-window `OPENCODE_CONFIG` … if neither works, a small `.opencode` per-model config is the fallback, and the route changes with it. |
| R4 | Ember is a small model; builder quality is unproven on this repo's plans. | The first Muse `/builder` unit, and the trial's Job B. | Muse stays a computer-use and simple-fix session; JR decides after the trial. |
| R5 | An agent session driving the pilot is slower than today's catalog-only jobs. | Job A vs the 30–40 s baseline. | The catalog driver keeps routine jobs; Muse handles the unknown screens, which is where it is useful. |
| R6 | The shared OpenCode service (route-plan R4) now serves a second model class; one bad config affects both. | The spike starts a DeepSeek and a Muse window side by side. | `opencode service restart`; the per-window `--standalone` fallback. |

**Spike first (S-M1, ~20 minutes, after B1 clears):** `tools/muse_session.sh --prompt "Reply with exactly: MUSE READY"` in a throwaway tmux window. Confirm three things: the model answers as `muse-ember-1.0` (not DeepSeek), a second window on DeepSeek still answers as `deepseek-v4.1-flash`, and `/models` in the Muse window shows `opencode/muse-ember-1.0`. Then one file edit through the build agent in a scratch worktree, to prove tools work. Findings go in `docs/reference/MUSE_EMBER_EVALUATION.md` §1.

## 4. Tests (synthetic, no live DB, no network)

- `tests/test_muse_session.py` (new) → exits 2 outside a configured checkout; with a stub `opencode` on `PATH` it exports `MU_AGENT_CLASS=muse`, `MU_AGENT_HARNESS=opencode`, `MU_AGENT_MODEL=muse-ember-1.0`, `MU_OPENCODE_MODEL` default and override, and `MU_OPENCODE_SESSION` (minted or reused); passes `--model opencode/muse-ember-1.0 --session <id>` to OpenCode; prints the Muse route line; never prints a secret.
- `tests/test_spawn_agent.py` (extend) → `--dry-run b1 muse "/builder"` prints `muse_session.sh --prompt /builder`; a DeepSeek-marked caller still needs `MU_ALLOW_DEEPSEEK_SPAWN=1` for `deepseek` but not for `muse`; an unknown type still exits 2.
- `tests/test_agent_board_inbox.py` (extend) → `muse` matches `to: muse`, `to: muse-ember-1.0`, and comma lists; it does not match `deepseek`; a label filter still skips the session's own messages.

Run with `pytest -m "not integration" tests/test_muse_session.py tests/test_spawn_agent.py tests/test_agent_board_inbox.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Muse launcher and spawn type

- **Owns:** `tools/muse_session.sh` (new), `tools/spawn_agent.sh`, `tests/test_muse_session.py` (new), `tests/test_spawn_agent.py`
- **Reads:** `tools/opencode_session.sh`, `tools/spawn_agent.sh`, `docs/done/OPENCODE_DEEPSEEK_ROUTE_PLAN.md` §2.3–2.4
- **Depends on:** none (no live model call)
- **Done when:** `tools/spawn_agent.sh --dry-run muse-lab muse "/builder"` prints the Muse wrapper line, and the wrapper's markers and `--model` are asserted by tests.
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
- **Done when:** the skill states the route, the inbox, the trailer, the D9 work limits and the computer-use rules; AGENTS.md gains the Muse row and the route paragraph; MADDEN_PILOT.md says a Muse session may run the jobs once the trial passes.
- **Verify:** `grep -c 'muse-ember-1.0' .claude/skills/muse/SKILL.md docs/reference/AGENTS.md` → 1 or more each; `grep -n 'muse' docs/reference/AGENTS.md` → the table row and the paragraph.

### U4: Acceptance trial and report

- **Owns:** `docs/reference/MUSE_EMBER_EVALUATION.md` (new)
- **Reads:** §2.7 of this plan, `data/pilot_runs` results, `docs/reference/MADDEN_PILOT.md` §5
- **Depends on:** U1, U2, U3, B1 cleared, JR's PC
- **Done when:** the report holds the spike results, Job A and Job B records, the gate line, and the recommendation; the live runs happen under `CLAIM · Madden PC` with JR's yes.
- **Verify:** every gate item in §2.7 has a number or a quoted stop reason; `git status` shows the report only.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 0 | spike S-M1 (no unit) | B1 cleared | Confirms the TUI model override before any code freezes a route line |
| 1 | U1, U2 | spike | Different files; neither needs the other |
| 2 | U3 | U1, U2 | Writes the docs against merged code |
| 3 | U4 | U1–U3, JR's PC | The live trial |

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
- **§1.2 "Agent routes" paragraph:** Muse Ember on OpenCode Zen (`opencode/muse-ember-1.0`), harness `opencode`, markers `MU_AGENT_CLASS=muse` / `MU_AGENT_HARNESS=opencode` / `MU_AGENT_MODEL=muse-ember-1.0`, trailer `Model: muse-ember-1.0`; builder and computer-use work only.
- **CLAUDE.md:** model skills at startup, spawn types, work limits.
- **No schema, API endpoint, pipeline or pilot-code change.**

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | **B1: how to give Ember a route.** Top up OpenCode Zen credit (the model is Zen-only), or use Muse Spark on Go and turn on the training-endpoint setting? | **Top up Zen.** Spark is a different model and its requests train Meta's model. If the top-up is unwelcome, the plan waits; nothing is built differently. |
| Q2 | If B1 is cleared, is the Top-up amount enough for one trial plus a few builder sessions? | Put the smallest sensible amount on it; the trial is one sim week plus a handful of screen reads, and the repo makes no other Muse calls. |
| Q3 | Should a Muse session be allowed to spawn a DeepSeek builder for a large unit, or always hand that to JR? | **Always hand to JR** (D9 limits Muse to builder and computer use; spawning is how a second class of builder proliferates). |
