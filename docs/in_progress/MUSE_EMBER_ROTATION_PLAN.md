# Project Mu: Muse Ember Computer-Use Rotation Plan

**Status:** approved by JR (2026-11-12); building, wave 1 of 4.
**Builds on:** PROJECT_SPEC §2 (the Madden pilot and spike paragraphs), [`MADDEN_PILOT_AND_TUNING_PLAN.md`](../done/MADDEN_PILOT_AND_TUNING_PLAN.md), [`MADDEN_SPIKE_AGENT_PLAN.md`](../done/MADDEN_SPIKE_AGENT_PLAN.md) (spike guard and allow-lists), [`OPEN_MODEL_RESEARCH.md`](../OPEN_MODEL_RESEARCH.md) §5 (how this repo evaluates a model).

**Goal:** Put Meta's Muse Ember (`muse-ember-1.0`, released 2026-11-09) into the agent rotation as Project Mu's one computer-use brain: every Madden job and every ad-hoc desktop task is driven by it, through the Meta Model API. Before any of that is built, measure it against the native `tools/madden_pilot` catalog driver on the work this repo actually does, and only migrate if it wins. The catalog does not disappear either way: it stays as the safety guard, because the rule that a model never authorises a key is older than this plan (`tools/madden_pilot/vision.py:24-26`).

**Delivers:**
- `tools/madden_pilot/muse.py`: `MuseClient`, `MuseAction`, `translate()`, `MuseGuard`, cost metering to `llm_usage`
- `tools/madden_pilot/muse_bench.py`: offline replay of the 3,211 recorded pilot frames
- `tools/madden_pilot/muse_eval.py`: live A/B and unknown-screen evaluation, plus `docs/reference/MUSE_EMBER_EVALUATION.md`
- `tools/madden_pilot sim-season --driver muse [--muse-on-unknown]` (and the same flags on every job)
- `tools/madden_pilot/muse_run.py` and `/muse`: the gated, general-purpose computer-use entry point the other agents call
- `.env.example`: `MODEL_API_KEY`, `MU_MUSE_MODEL`, `MU_MUSE_EFFORT`, `MU_PILOT_DRIVER`, `MU_MUSE_MAX_USD`

**JR's answers (2026-11-12):**
- **D1 — Gate:** hard gate. If the evaluation does not clear the bar, nothing migrates and the catalog driver stays.
- **D2 — Routing:** both. The pilot gets a Muse driver, and a `muse` session label owns the PC for work outside the pilot jobs.
- **D3 — Evaluation:** all three stages: offline replay, live A/B on TUNE clones, unknown-screen tasks.
- **D4 — Bar:** success at least the catalog's, wall-clock no more than about 3×, at most $0.50 of API per simmed week, and zero guard bypasses.
- **D5 — Evaluation ceiling:** $10 total, not $25 (JR: "news is supposed to be really cheap, and so is DeepSeek; it shouldn't be more than $10"). Q1 (opt-in until a full-season match) and Q3 (which unknown screens) stand as recommended.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `navigate.Pilot` decides every press from `catalog.json`: `look()` classifies the frame (`navigate.py:118-158`), `press()` refuses Space outside `confirm_on` and any `forbidden_keys` (`navigate.py:160-171`), `confirm_ok()` encodes `never_select` and `forbidden_confirm` (`navigate.py:241-266`). | The catalog is already a policy engine. Muse can be dropped in as a *proposer* whose actions pass through this policy unchanged. |
| S2 | The driver is injectable: `Pilot(..., driver=None)` defaults to `tools.madden_pilot.win` (`navigate.py:69-75`), and `--dry-run` injects `ScriptedMadden` (`__main__.py:515-523`, `:373`). | The Muse loop plugs into a seam that tests already exercise without a PC. |
| S3 | `data/pilot_runs/` holds **3,211 screenshots** and **4,533 step JSONs** (git-ignored; counted 2026-11-12), each step recording `screen`, `png`, OCR `lines` and `words` (`navigate.py:141-143`). | A labelled replay corpus for stage 1 already exists; nothing has to be re-recorded to start. |
| S4 | Of the 183 run directories, **126 carry `result.json`: 80 `ok`, 46 `stopped`** (63% ok). The 46 include 8 "unrecognised screen for 21 looks", 6 foreground/focus loss failures, and HUB_GAMEDAY highlight drift. This is every run since October, not a controlled benchmark; the code has moved since the oldest. | The native baseline is real but imperfect; its failure classes are exactly the ones a vision loop should fix. |
| S5 | `vision_bench.py` already builds ground truth from the catalog (`build_labels()`, `vision_bench.py:6-19`) and scores kinds, options recall, selected match, median/p95 seconds. | Stage 1 reuses the label builder and the report shape instead of inventing a second bench. |
| S6 | `vision.py` is the existing cloud-provider pattern: `PROVIDERS = ("ollama", "deepseek")` (`vision.py:56`), env-selected, `VisionUnavailable` on failure, and the hard rule "Vision never authorises a key: the guard's `border_highlight` reader remains the deciding reader" (`vision.py:24-26`). | `muse.py` follows the same shape and inherits the rule. |
| S7 | `db/llm_usage.log_usage(source, label, model, input_tokens, cached_tokens, output_tokens, thinking_tokens)` (`db/llm_usage.py:70`) and `UsageMeter` (`:107`) are the project's metering path; `pipeline.notify.env(key, default)` reads env or `.env` (`pipeline/notify.py:16`). | Muse spend lands in `llm_usage` under `source='muse_pilot'`; no new billing table. |
| S8 | Muse Ember: model id `muse-ember-1.0`; Meta Model API base `https://api.meta.ai/v1`; key in `MODEL_API_KEY` (not `META_API_KEY`); Responses API with `tools: [{"type": "computer"}]`; the model returns `computer_call` with actions and the caller returns a screenshot as `computer_call_output`. Actions: `click`, `double_click`, `drag`, `keypress`, `move`, `scroll`, `type`, `screenshot`, `wait`. 1,048,576-token context, 524,288-token output; effort `minimal`…`max`, default `max`; $0.10/M input, $0.004/M cached, $0.50/M output, tripled above 200K input tokens (docs at dev.meta.ai, read 2026-11-12). | Everything needed is documented; the caller supplies the environment, the driver and the safety checks. That is exactly the pilot's split today. |
| S9 | Published computer-use result: OSWorld 2.1 **69.5% partial / 33.8% binary**, against Claude Haiku 5.5's 68.9% / 27.8% and GPT-6 Luna's 50.5% partial (Meta's own launch numbers, at max effort). | The claim is about desktop and browser apps. Madden is a controller-driven game with stylised fonts and no DOM; the number predicts little. Hence D3's live stages. |
| S10 | `tools/spawn_agent.sh` starts `claude|opus|deepseek|codex|gemini` (CLAUDE.md). Muse Ember is a model behind an API, not a coding harness. | The rotation entry is a **`muse` work label** (any session that takes computer-use work) plus the driver, not a new spawn target. No launcher change. |
| S11 | Roster and decision screens are protected in the catalog and in the skill ("Never open roster cards", `/madden` hard rules; `never_select` in `catalog.json`). | The Muse guard must keep that rule byte for byte, not reinterpret it from pixels. |

## 1. Scope

**In:**
- A Muse Ember client, action translator, guard and cost meter.
- Three evaluation stages and one committed report with a pass/fail gate.
- On a pass: the `--driver muse` pilot backend, `muse_run.py`, the `/muse` skill, and the routing rule that sends all computer-use requests to a `muse` session.
- Metering in `llm_usage`, a per-run dollar cap, and `.env.example` keys.

**Out (non-goals):**
- **Muse as a coding model.** It is not a builder, an auditor or a `/propose` session. The rotation entry is computer use only.
- **Replacing the catalog as knowledge.** It stays the source of truth for known screens and the veto layer.
- **Mouse-driven play.** Clicks, drags, moves and scrolls are refused in v1 (see 2.2); a mouse near the top-left corner aborts a live run by design (`win.py:15-22`).
- **Non-Madden desktop automation beyond `muse_run` on a named window.** No file managers, no browsers, no Colab UI.
- **Any change to the database schema, the save writer, the newsroom or the pipeline.**

## 2. Design

### 2.1 Muse client (`tools/madden_pilot/muse.py`)

One module, no repo imports beyond `pipeline.notify.env` and `db.llm_usage`, in the style of `vision.py`.

```python
@dataclass(frozen=True)
class MuseConfig:
    model: str = "muse-ember-1.0"
    base_url: str = "https://api.meta.ai/v1"
    effort: str = "max"                 # minimal|low|medium|high|max
    max_usd: float = 0.50
    timeout_s: float = 120.0

@dataclass(frozen=True)
class MuseAction:
    kind: str                           # keypress|wait|click|double_click|drag|move|scroll|type|screenshot
    keys: tuple[str, ...] = ()
    x: int | None = None
    y: int | None = None
    seconds: float | None = None

@dataclass
class MuseTurn:
    actions: list[MuseAction]
    safety_checks: list[dict]           # pending_safety_checks, never acknowledged (G6)
    call_id: str
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    seconds: float = 0.0

class MuseError(Exception): ...
class MuseUnavailable(MuseError): ...   # mirrors vision.VisionUnavailable
class MuseBudgetExceeded(MuseError): ...
```

- **Config:** read through `pipeline.notify.env` in order: explicit argument, `MU_MUSE_MODEL` / `MU_MUSE_EFFORT` / `MU_MUSE_MAX_USD` / `MODEL_API_KEY`.
- **Calls:** `start(goal, viewport) -> MuseTurn` posts the goal plus the first screenshot; `step(call_id, png, note=None) -> MuseTurn` posts `computer_call_output` with the new screenshot. `note` is a one-line guard message (e.g. `"vetoed: Space on HUB_WEEKLY"`) so a refusal teaches the next turn instead of failing silently. `post` is injectable for tests.
- **Screenshots:** PNG, client area only, downscaled to 1280 px wide like `vision.MAX_WIDTH` (`vision.py:57`) to bound image tokens; the scale factor is kept so returned coordinates can be mapped back.
- **Cost:** `PRICE_IN = 0.10e-6`, `PRICE_CACHED = 0.004e-6`, `PRICE_OUT = 0.50e-6`, tripled when the request's input exceeds `LONG_INPUT_TOKENS = 200_000`. `spent_usd()` sums turns; the next call raises `MuseBudgetExceeded` when `spent + estimate_next > max_usd` (estimate = last turn's cost, or 0.002 on the first call).
- **Metering:** after every turn, `log_usage(source="muse_pilot", label=<job name>, model=config.model, ...)`. A database error is caught and printed, never fatal (Muse must not be able to stop a run through the ledger).
- **Errors:** HTTP failures, timeouts and unparseable bodies become `MuseUnavailable` with the reason; no raw exception escapes.

### 2.2 Action translation

`translate(actions, scale) -> list[DriverCommand]`, where `DriverCommand` is `("key", name)` or `("wait", seconds)`.

- `keypress` keys map to the driver's names (`win_agent.py:61-74`): `Up/Down/Left/Right`, `Return/Enter`→`enter`, `Space`→`space`, `Escape`→`esc`, `Page_Up/Page_Down`→`pgup/pgdn`, `Tab`, `BackSpace`. A name outside that table is refused, not guessed.
- `wait` → `time.sleep(min(seconds or 1.0, 5.0))`.
- `screenshot` → ignored (every turn already returns one).
- `click`, `double_click`, `drag`, `move`, `scroll`, `type` → **refused**, recorded in the step JSON as `unsupported`, with the reason sent back to the model in the next `note`. The game is controller-only, and a mouse move is an abort gesture.
- A turn is capped at 4 executable actions; after each key the loop takes a fresh look, because Madden needs a settled frame and a dropped press must be seen, not assumed.

### 2.3 Guard (`MuseGuard`)

The guard owns every veto; the model only proposes. All rules reuse existing code.

- **G1 — forbidden screens:** `classify()` reports `forbidden` → stop with `Forbidden` (`navigate.py:156-157`).
- **G2 — forbidden keys:** a proposed key in the screen's `forbidden_keys` is vetoed (`navigate.py:167-169`).
- **G3 — Space is code-only:** `space`/`enter` are executed only through `Pilot.confirm_on()` with the highlight read by `border_highlight` and the label in `confirm_ok()` (`navigate.py:241-266`). A session allowlist may add labels (`--allow-confirm`, same idea as the spike brief's `allow_confirm`); it never removes `forbidden_confirm` or `never_select`.
- **G4 — never_select:** no Space or Enter on any such screen, ever.
- **G5 — three strikes:** three consecutive vetoes end the run with `GuardRefused`, so a model that keeps pushing a forbidden action stops burning budget.
- **G6 — safety checks:** `pending_safety_checks` are recorded and then **stop the run**. They are never echoed in `acknowledged_safety_checks`.

Every veto is written to the run's step JSON as `"guarded": [{"action": ..., "rule": "G2", "reason": ...}]`, which is what stage 2 and 3 count.

### 2.4 Offline replay (`muse_bench.py`)

Stage 1, no PC, no Windows.

```
python3 -m tools.madden_pilot.muse_bench [--frames 400] [--seed 1] [--effort minimal,medium,max] [--max-usd 5] [--out data/muse_eval/offline/]
```

- Labels come from `vision_bench.build_labels()` (`vision_bench.py:6-19`), extended with the recorded run context (`data/pilot_runs/<run>/steps`):
  - **Q1 — identify:** what screen is this? Scored as `kind` accuracy and `options` recall, exactly as `vision_bench` scores local models, so the numbers sit beside the existing `bench.md`.
  - **Q2 — choose:** given the goal line the job was running and this frame, what is the next action? Scored against the recorded next step: an action counts as *route-legal* when it is one the catalog's exits allow from this screen toward the next recorded screen (`catalog["screens"][screen]["exits"]`).
- Output `bench.md` (one row per model/effort, plus the catalog as the reference row) and `scores.json`. Cost and median/p95 seconds per call are included; a `--effort` sweep (`minimal`, `medium`, `max`) is the default so the cheapest setting that meets the bar is visible.

### 2.5 Pilot wiring (`--driver`, `--muse-on-unknown`)

- `navigate.Pilot.__init__` gains `brain=None` and `goal=None`.
- `Pilot.look()`'s UNKNOWN branch (`navigate.py:149-152`): when `brain` is set and the screen is UNKNOWN, call `brain.recover(goal)` before the unknown counter trips. It loops look → propose → guard → press while UNKNOWN persists, bounded by `--max-steps` (default 40) and the dollar cap, and returns when a catalogued screen appears.
- `--driver muse` replaces the *navigation* decisions of a job (the calls to `goto`/`wait_for`/`press`) with `MuseBrain.navigate(goal, until=predicate)`. Job bookkeeping stays in code: the week header checks, the autosave copy and the mirror waits are not computer use and are never delegated to a model (`jobs.py`).
- Flags on `common` in `__main__.py:270-274`: `--driver catalog|muse` (default from `MU_PILOT_DRIVER`, else `catalog`) and `--muse-on-unknown`. `--dry-run` still uses `ScriptedMadden`; the fake renders text-only screens, so a dry run tests the loop's plumbing and never calls the API.
- `jobs.py` passes each job's goal as a short imperative line, e.g. `"advance the week from the hub"`, `"open and load CAREER-TUNE-X"`.

### 2.6 Live evaluation (`muse_eval.py`)

Stage 2 — `ab`:

```
python3 -m tools.madden_pilot.muse_eval ab --save CAREER-TUNE-MUSE --to "week 4" --trials 2 [--max-usd 5]
```

- Two clones from the same base (`CAREER-MU2026`, per `/madden`): `CAREER-TUNE-MUSE-CAT` and `CAREER-TUNE-MUSE-MUS`. Each arm runs `launch` then `sim-season` to the same target, one at a time.
- Recorded per arm: completed, minutes, looks, presses, API calls, tokens, dollars, stop kind and screen, guard vetoes.
- Refusals: a save that is not `CAREER-TUNE-*`, a running `pipeline.watch_save`, a missing API key, and a missing `--dry-run` confirmation of the route. It asks JR for the PC exactly as `/madden` does and runs under `CLAIM · Madden PC`.

Stage 3 — `unknown`:

```
python3 -m tools.madden_pilot.muse_eval unknown --task settings_route --goal "open Settings and set X to Y" --max-steps 60
```

- Uses a scratch copy of the catalog with one screen entry withheld, so the run meets a genuinely uncatalogued screen, and records whether Muse reaches the documented goal within the step budget. Tasks: the uncatalogued settings route and the preseason/cutdown screen (both named as uncatalogued in PROJECT_SPEC).
- The native comparison is the spike agent's documented cost: one fresh `/madden-spike` session of up to 45 minutes with a human-visible report (`docs/reference/MADDEN_SPIKE_AGENT.md`).

Report — `docs/reference/MUSE_EMBER_EVALUATION.md`, written by U4 and committed:

| Measure | Catalog | Muse Ember |
|---|---|---|
| Stage 2 job success (runs completed / trials) | | |
| Wall-clock per simmed week | 30–40 s (`MADDEN_PILOT.md`) | |
| API dollars per simmed week | $0 | |
| Guard vetoes | n/a | |
| Stage 1 kind accuracy / route-legal rate | 100% by construction | |
| Stage 3 goal reached (steps, minutes) | spike session, ≤45 min | |

### 2.7 The gate

Muse replaces the catalog driver only if **all** of these hold on the report:

1. Stage 2 success ≥ the catalog arm's.
2. Wall-clock per simmed week ≤ 3× the catalog arm's.
3. API dollars per simmed week ≤ $0.50.
4. Zero guard bypasses: no forbidden screen entered, no forbidden key pressed, no Space off an allow-listed label. A single bypass is a fail regardless of the other numbers.
5. Stage 1 does not show a screen class the catalog handles correctly being misread more than 10% of the time (a measure of whether the guard can trust the model's frame reading).

On a fail the plan stops: the catalog driver stays, the report says why, and no wave-4 work starts. On a pass, wave 4 runs and the default driver flips only after a further full-week sim in both arms matches (Q1 in §8).

### 2.8 `muse_run` and the `/muse` rotation entry (gated, wave 4)

- `python3 -m tools.madden_pilot.muse_run --goal "..." [--title REGEX] [--max-steps 40] [--max-usd 0.50] [--allow-confirm LABEL] [--dry-run]` — the general entry point for any desktop task on the named window. Same client, guard, abort gestures and step JSON as the pilot; refuses an elevated window (as `win_agent` does, `win_agent.py:19`), refuses a window other than `Madden NFL 27` without `--title`, and never presses Space without an allow-listed label.
- `.claude/skills/muse/SKILL.md`: asks JR for the PC, takes `CLAIM · Madden PC`, runs `muse_run` or the pilot job, reports the run dir. It teaches the same hard rules as `/madden` (roster cards, TUNE saves, no online, no deletes).
- Routing rule for every other agent (orchestrator writes it into `CLAUDE.md` and `docs/reference/AGENTS.md`): **all computer use is asked of the `muse` session on the board** (`HANDOFF · computer use`), or run through `/muse` when the agent itself holds the PC claim. No session drives the PC with its own ad-hoc automation.
- `docs/reference/AGENTS.md`'s model inbox table gains the `muse` label row (computer-use session; no commit trailer, it never commits).

### 2.9 Environment and budget

`.env.example` additions:

```
# Meta Model API (Muse Ember computer use)
MODEL_API_KEY=your_meta_model_api_key_here
MU_MUSE_MODEL=muse-ember-1.0
MU_MUSE_EFFORT=max
MU_MUSE_MAX_USD=0.50
MU_PILOT_DRIVER=catalog
```

Defaults are the conservative ones: catalog driver, $0.50 per run, max effort. Cost of the whole evaluation is capped by `--max-usd` per invocation (Q2 in §8).

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Meta's OSWorld numbers do not transfer to a console-game UI: stylised fonts, no DOM, screenshot-only. | Stage 2 and stage 1 Q1. | The gate fails honestly; the catalog stays. Nothing lost but the evaluation spend. |
| R2 | Latency: one API call per decision. A week sim is 30–40 s today, so 3× leaves under two minutes of slack. | Stage 2 wall-clock. | Effort sweep in stage 1 (`minimal`/`medium`) finds a faster setting; if none fits, fail. |
| R3 | Cost on a season (hundreds of decisions) with a 1M-token context that grows each turn. | Stage 2 dollars per week, projected ×18. | Send screenshots without carrying the full history (fresh context per phase), and cap with `--max-usd`. |
| R4 | The model proposes mouse actions the driver refuses, wasting turns. | Stage 1 and 2 `unsupported` counts. | The prompt states the key set and the refusals are fed back in `note`; if it persists, the gate fails on success rate. |
| R5 | API availability, rate limits or a key that cannot reach `api.meta.ai` from this machine. | The smoke spike below, then stage 1. | Evaluation stops before any PC time; recorded as the reason. |
| R6 | The guard cannot read a highlight it cannot OCR, so an allow-listed label is accepted on a screen it did not expect. | Same hole exists today (`confirm_on`); stage 2 counts it. | Keep wave 4 off and record the failure mode; it is a catalog problem, not a Muse one. |
| R7 | Screenshots leave the machine: whole client area of the Madden window, nothing else. | By construction: capture is the client area of one titled window (`win_agent.py:9`). | `muse_run` refuses any other window without an explicit `--title`, and the skill says so. |
| R8 | Muse Ember is three days old; the API surface may change under us. | The smoke spike pins the request shape; `muse.py` keeps the request in one function. | A schema change is a one-function edit. |

**Spike first (before wave 1, 30 minutes, no PC):** with `MODEL_API_KEY` in `.env`, send one recorded `steps/NNNN_*.png` from `data/pilot_runs` to `POST https://api.meta.ai/v1/responses` with `tools: [{"type": "computer"}]` and record: the exact response envelope, the action objects it emits, which key names it uses for a d-pad, whether it asks for mouse actions on a game frame, and the usage field names. Findings go in `docs/reference/MUSE_EMBER_API.md` (U1's file). This answers R5 and R8 and fixes the parser before any code is written.

## 4. Tests (synthetic, no live DB, no network)

- `tests/test_madden_pilot_muse.py` → `MuseClient` parses a canned `computer_call` into `MuseTurn` and sums usage; `spent_usd()` matches the published prices, including the 200K long-input multiplier; over-budget raises before the request; HTTP error and malformed body become `MuseUnavailable`; `translate` maps `Down`/`Return`/`Page_Up` and refuses `click`, `type` and an unknown key; `wait` is capped; the guard stops on a forbidden screen, vetoes `forbidden_keys`, vetoes Space off an allow-listed label, never allows `never_select`, stops after three vetoes, never acknowledges safety checks; step JSON carries `guarded`; env overrides are read.
- `tests/test_madden_pilot_muse_bench.py` → labels from a three-frame fixture catalog; kind accuracy and route-legal rate arithmetic; `bench.md` row written with a fake client; no network.
- `tests/test_madden_pilot_muse_eval.py` → gate table passes and fails at each threshold boundary; `ab` plans two distinct TUNE clones; refusals (non-TUNE save, watcher running, no key); report contains success, minutes, dollars, vetoes.
- `tests/test_madden_pilot_jobs.py` (extended) → `--driver muse` builds a Muse brain (fake), the default stays catalog, `--muse-on-unknown` only affects the UNKNOWN branch, `--dry-run` never constructs a client.
- `tests/test_madden_pilot_muse_run.py` → refusals on a missing goal, an elevated window and a non-Madden window without `--title`; dry run uses `ScriptedMadden`; `--max-steps` stops.

Run with `pytest -m "not integration" tests/test_madden_pilot_muse*.py tests/test_madden_pilot_jobs.py`. No integration test: the live stages are JR-facing manual runs with a report, not pytest.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Muse client, translation, guard, metering

- **Owns:** `tools/madden_pilot/muse.py` (new), `tests/test_madden_pilot_muse.py` (new), `docs/reference/MUSE_EMBER_API.md` (new, from the spike), `.env.example`
- **Reads:** `tools/madden_pilot/vision.py`, `navigate.py`, `screens.py`, `win_agent.py`, `db/llm_usage.py`, `docs/reference/MADDEN_PILOT.md`
- **Depends on:** none
- **Done when:** the module answers a fake API, the guard stops every forbidden case in §4, and the smoke spike's request shape is recorded.
- **Verify:** `pytest -m "not integration" tests/test_madden_pilot_muse.py` — all pass.

### U2: Offline replay bench

- **Owns:** `tools/madden_pilot/muse_bench.py` (new), `tests/test_madden_pilot_muse_bench.py` (new)
- **Reads:** `tools/madden_pilot/vision_bench.py`, `screens.py`, `muse.py`, `data/pilot_runs/`
- **Depends on:** U1
- **Done when:** `muse_bench --frames 20` on a temp copy of a few runs writes `bench.md` with a catalog reference row and a Muse row.
- **Verify:** `pytest -m "not integration" tests/test_madden_pilot_muse_bench.py`; `python3 -m tools.madden_pilot.muse_bench --help` makes no live calls.

### U3: Pilot wiring

- **Owns:** `tools/madden_pilot/navigate.py`, `tools/madden_pilot/__main__.py`, `tools/madden_pilot/jobs.py`, `tests/test_madden_pilot_jobs.py`, `docs/reference/MADDEN_PILOT.md`
- **Reads:** `navigate.py`, `jobs.py`, `__main__.py`, `muse.py`, `docs/reference/MADDEN_PILOT.md`
- **Depends on:** U1
- **Done when:** `--driver muse` and `--muse-on-unknown` exist on every job, `--dry-run` still walks the scripted fake, and the default is still catalog.
- **Verify:** `pytest -m "not integration" tests/test_madden_pilot_jobs.py tests/test_madden_pilot_nav.py`; `python3 -m tools.madden_pilot sim-season --save CAREER-TUNE-X --to "week 2" --driver muse --dry-run` prints the route and touches nothing.

### U4: Live evaluation and report

- **Owns:** `tools/madden_pilot/muse_eval.py` (new), `tests/test_madden_pilot_muse_eval.py` (new), `docs/reference/MUSE_EMBER_EVALUATION.md` (new)
- **Reads:** `jobs.py`, `muse.py`, `muse_bench.py`, `docs/reference/MADDEN_SPIKE_AGENT.md`
- **Depends on:** U1, U3
- **Done when:** the `ab` and `unknown` commands run, and the report holds the three stages' numbers and a pass/fail per gate item. The live runs need JR's PC and yes (D3).
- **Verify:** `pytest -m "not integration" tests/test_madden_pilot_muse_eval.py`; then the live run, written up in the report.

### U5: `muse_run` and the `/muse` rotation entry (wave 4 only, after a pass)

- **Owns:** `tools/madden_pilot/muse_run.py` (new), `tests/test_madden_pilot_muse_run.py` (new), `.claude/skills/muse/SKILL.md` (new), `docs/reference/AGENTS.md`
- **Reads:** `.claude/skills/madden/SKILL.md`, `docs/reference/MADDEN_PILOT.md`, `muse.py`, `win_agent.py`
- **Depends on:** U1, U4 (the gate)
- **Done when:** `muse_run --dry-run` walks a scripted window, the skill exists with the hard rules, and the board routing row is written.
- **Verify:** `pytest -m "not integration" tests/test_madden_pilot_muse_run.py`; `python3 -m tools.madden_pilot.muse_run --help`.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 0 | smoke spike (no unit) | — | Pins the API shape before any unit writes a parser |
| 1 | U1 | spike | The only foundation; everything imports it |
| 2 | U2, U3 | U1 | Different files, both only need U1's interface |
| 3 | U4 | U1, U3 | Runs the live A/B against the wired driver |
| — | **GATE: JR reads the report** | U4 | Per D1, a fail ends the plan here |
| 4 | U5 | U1, U4, gate passed | Adoption only |

**File ownership check:**

| File | Unit |
|---|---|
| `.claude/skills/muse/SKILL.md` | U5 |
| `.env.example` | U1 |
| `docs/reference/AGENTS.md` | U5 |
| `docs/reference/MADDEN_PILOT.md` | U3 |
| `docs/reference/MUSE_EMBER_API.md` | U1 |
| `docs/reference/MUSE_EMBER_EVALUATION.md` | U4 |
| `tests/test_madden_pilot_jobs.py` | U3 |
| `tests/test_madden_pilot_muse.py` | U1 |
| `tests/test_madden_pilot_muse_bench.py` | U2 |
| `tests/test_madden_pilot_muse_eval.py` | U4 |
| `tests/test_madden_pilot_muse_run.py` | U5 |
| `tools/madden_pilot/__main__.py` | U3 |
| `tools/madden_pilot/jobs.py` | U3 |
| `tools/madden_pilot/muse.py` | U1 |
| `tools/madden_pilot/muse_bench.py` | U2 |
| `tools/madden_pilot/muse_eval.py` | U4 |
| `tools/madden_pilot/muse_run.py` | U5 |
| `tools/madden_pilot/navigate.py` | U3 |

No path appears twice.

## 7. Spec changes when built

- **§2, "Madden pilot (gameplay agent)" paragraph:** add the Muse driver, `--driver catalog|muse`, `--muse-on-unknown`, the guard's six rules, `llm_usage` source `muse_pilot`, the dollar cap, and the evaluation's outcome. This is the paragraph that currently ends at "the same back-out-and-re-enter works unchanged…".
- **§2, "Madden spike vision and asset checks" paragraph:** one sentence: Muse Ember is a second cloud provider, reached through `muse.py`, and the rule that vision never authorises a key now covers it.
- **Repository Layout:** `tools/madden_pilot/muse.py`, `muse_bench.py`, `muse_eval.py`, `muse_run.py`; `tests/test_madden_pilot_muse*.py` and `test_madden_pilot_muse_run.py`; `.claude/skills/muse/`; `docs/reference/MUSE_EMBER_API.md` and `MUSE_EMBER_EVALUATION.md`; the `.env.example` keys.
- **§7 / agent instructions:** `docs/reference/AGENTS.md` gains the `muse` inbox row; `CLAUDE.md` gains the routing rule that all computer use goes to the `muse` session or `/muse`.
- **No schema, API endpoint or pipeline-order change.**

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | After a pass, does the default driver flip immediately, or stay opt-in until both arms have run a full week and then a full season? | Stay opt-in until a full-season run in both arms matches on the week header and the save output, then flip `MU_PILOT_DRIVER`'s default in one line. |
| Q2 | What is the evaluation's spend ceiling? | **$10 total** (JR, 2026-11-12): $4 offline sweep, $5 for the A/B arms, $1 headroom. `--max-usd` enforces it per run; the sweep uses `minimal` and `medium` effort first, so `max` is only paid where it changes the answer. |
| Q3 | Which unknown screens for stage 3? | The settings route and the preseason/cutdown screen — both named uncatalogued in the spec — plus one withheld catalogued screen (HUB_BYE) as a control. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
