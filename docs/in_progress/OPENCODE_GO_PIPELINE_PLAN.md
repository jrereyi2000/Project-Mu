# Project Mu: OpenCode Go Pipeline Plan

**Status:** approved by JR (2026-11-12); building, wave 1 of 2.
**Builds on:** [`OPENCODE_DEEPSEEK_ROUTE_PLAN.md`](../done/OPENCODE_DEEPSEEK_ROUTE_PLAN.md) (agent sessions already run on OpenCode Go), PROJECT_SPEC §7.12 (dual ReAct tool loop), [`V3_DOSSIER_EVALUATION.md`](../in_progress/V3_DOSSIER_EVALUATION.md).

**Goal:** The repo's own DeepSeek calls stop going to `api.deepseek.com` and go through JR's OpenCode Go subscription instead. OpenCode Go becomes a first-class provider, so any later plan (including the model benchmark) can point a writer or a tool loop at any Go model without new plumbing. Every call reports what it cost the monthly allowance, and `tools/go_usage.py` watches that allowance.

**Delivers:**
- Provider `opencode-go` in `newsroom/tool_loop/openai_loop.py`, with the `deepseek` name kept as an alias that resolves to Go.
- `deepseek-flash` → `deepseek-v4.1-flash` model translation, so existing call sites keep working.
- `newsroom/tool_loop/go_allowance.py` — records each call's allowance in `data/usage/go_allowance.jsonl`.
- `tools/go_usage.py` — 5-hour / weekly / monthly allowance watchdog with the same exit-code convention as `tools/usage_check.py`.
- `tools/dossier_pilot.py` priced from Go's own reported cost, and `OPENCODE_GO_API_KEY` / `OPENCODE_GO_BASE_URL` in `.env.example`.

**JR's answers (2026-11-12):**
- **D1 — Reach.** OpenCode Go is a first-class provider usable by every caller; the newsroom's default model stays Gemini.
- **D2 — Naming.** `deepseek` becomes a deprecated alias for `opencode-go`; nothing resolves to `api.deepseek.com` unless `DEEPSEEK_BASE_URL` is set by hand.
- **D3 — Budget.** Keep per-run ceilings and add an allowance watchdog.
- **D4 — Vision.** Excluded: `tools/madden_pilot/vision.py` is being migrated to Muse Ember by `MUSE_EMBER_ROTATION_PLAN` (M-0611), so this plan does not touch it.
- **D5 — Credential (Q2).** A personal Console API key, not a service account. It lives in the git-ignored `.env` as `OPENCODE_GO_API_KEY`.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `newsroom/tool_loop/openai_loop.py:71-77` maps five providers to `(base_url env, default base url, api key env)`; `resolve_endpoint` (`:85-100`) raises for an unknown provider or a hosted one with no key. | One dict entry and one alias rule add the provider; no call site needs a new signature. |
| S2 | `newsroom/tool_loop/router.py:19` lists the OpenAI-compatible providers and `:41-64` resolves and strips `provider:` / `provider/` prefixes. | `opencode-go:` and `opencode:` prefixes must be added, and `deepseek:` kept working through the alias. |
| S3 | `tools/dossier_pilot.py:38-64` carries DeepSeek's list prices and peak/off-peak rule; `:318-321` sets the $1.00 ceiling and `:440-461` the live guard. | Under Go, `usage.opencode.list_cost` already includes peak pricing, so the local table becomes a fallback rather than the source of truth. |
| S4 | `tools/madden_pilot/vision.py:217-233` is the other DeepSeek call site, and `docs/in_progress/MUSE_EMBER_ROTATION_PLAN.md:34,38` (claimed in M-0611) is replacing the pilot's vision path. | Out of scope here (D4). |
| S5 | OpenCode reports `usage.opencode.{billing_source, list_cost, allowance, tokens}` on every inference response; `cost` is 0 for calls covered by Go (Console API › Inference, read 2026-11-12). | The allowance can be metered per call without a price table, and the month's spend is knowable from our own traffic. |
| S6 | Two base paths are documented: `https://opencode.ai/inference/go/openai/v1/chat/completions` and `https://opencode.ai/inference/openai/v1/chat/completions`. Spike G1 (2026-11-12) found the plain path answers **HTTP 402 "Insufficient balance"** for a Go key and the `/go/` path answers normally with `billing_source: "go"`. | The provider's default base URL is the `/go/` path. A caller who wants balance-billed models sets `OPENCODE_GO_BASE_URL` by hand. |
| S6b | `GET https://opencode.ai/inference/go/v1/usage` answers `{"usage": {"rolling": {percent, resetsAt}, "weekly": {...}, "monthly": {...}}}` for the same key (spike G2, 2026-11-12), workspace-wide, independent of OpenCode sessions. | The allowance watchdog reads the server, not our own ledger, so agent-session spend is included. The ledger stays for per-run accounting. |
| S7 | Free-tier chat models are only available from inside OpenCode; other clients get HTTP 403 even with a key (Console API › Authentication). | The pipeline must use Go-covered models only. Plan B's free-model evaluation cannot use this endpoint as-is. |
| S8 | `db/llm_usage` stores tokens only (`db/init_db.sql:434-441`), and Claude's quota already uses a file (`data/usage/rate_limits.json`, `tools/usage_statusline.py`). | Allowance goes to a file ledger, not a migration: no schema change, no `init_db.sql` mirror. |
| S9 | `tools/usage_check.py:1-25` is the existing watchdog shape: thresholds on two windows, exit 0/2, exit 1 when unknown. | `tools/go_usage.py` copies that contract, so `/audit`-style hand-over rules can read either tool the same way. |
| S10 | `.env` holds `GEMINI_API_KEY` and no DeepSeek or OpenCode key (`.env.example:1-36` documents Gemini, PostgreSQL, ntfy and Chiaki only). | A Go key is a new, JR-created secret; the plan never writes one into a tracked file. |

## 1. Scope

**In:**
- `opencode-go` provider + `deepseek` alias + model translation.
- Per-call cost and allowance capture, a JSONL ledger, and the watchdog.
- `tools/dossier_pilot.py` budget priced from Go's numbers.
- `.env.example` documentation of the two new variables.

**Out (non-goals):**
- `tools/madden_pilot/vision.py` (D4; Muse Ember's plan).
- Changing the newsroom's default model away from Gemini (`engines/llm_config.py:15`). That is plan B's decision.
- Benchmarking models. Plan B.
- A board or agent-side change: the agent route is already done.
- Any migration, table or column.
- A Console usage API client. Whether one exists is unknown (see R4).

## 2. Design

### 2.1 The provider

| Piece | Value |
|---|---|
| Provider id | `opencode-go` |
| Base URL env | `OPENCODE_GO_BASE_URL`, default `https://opencode.ai/inference/go/openai/v1` — the `/go/` path is the only one a Go key can spend from (S6) |
| Key env | `OPENCODE_GO_API_KEY` — the API key that ships with the Go subscription (Console › Account › Personal API keys, `oc_sk_…`). It is the credential, not another purchase: calls report `billing_source: "go"` and count against the Go allowance. |
| Request header | `x-opencode-session: mu-pipeline-<pid>-<epoch>`, one per process, overridable by `MU_GO_SESSION` (Go uses it for routing and prompt caching) |
| Models | `deepseek-v4.1-flash` (Go-covered), any other Go model id by name |

`PROVIDER_ENDPOINTS["opencode-go"] = ("OPENCODE_GO_BASE_URL", "https://opencode.ai/inference/openai/v1", "OPENCODE_GO_API_KEY")`.

### 2.2 The `deepseek` alias

`resolve_endpoint("deepseek")` returns the `opencode-go` endpoint unless `DEEPSEEK_BASE_URL` is set, in which case the old endpoint is honoured (an escape hatch JR can use if Go is down). `MODEL_ALIASES = {"deepseek-flash": "deepseek-v4.1-flash"}` is applied in `run_openai_tool_loop` after prefix stripping, only for providers `deepseek` and `opencode-go`. `router.resolve_tool_loop_provider` adds `opencode-go` to `OPENAI_COMPATIBLE_PROVIDERS`, treats a leading `opencode-go:`/`opencode:` as that provider, and leaves `deepseek:` resolving to `deepseek` (which is now Go).

### 2.3 Usage capture

`extract_openai_usage` gains four optional keys beside the token counts, read from `usage.opencode` when the SDK keeps it (dict or `model_extra`):

| Key | Source |
|---|---|
| `cost_usd` | `usage.cost` |
| `list_cost_usd` | `usage.opencode.list_cost` |
| `allowance_usd` | `usage.opencode.allowance` |
| `billing_source` | `usage.opencode.billing_source` |

All four are `None` when absent, so Gemini/ollama paths and any provider that does not report them behave exactly as today. Per-call usage rows (`usage_calls`) carry the same four fields.

Two facts from spike G1 that shape the loop:
- The SDK keeps the extra object: `usage.model_dump()` contains `opencode: {allowance, billing_source, list_cost, tokens}`. `extract_openai_usage` reads it from the dump (dict) or `usage.opencode` when the SDK exposes it as an attribute.
- Go returns **no `reasoning_content`** on tool turns, so the replay at `openai_loop.py:260-267` is inert on this provider (keep it: it is what DeepSeek's own API needs). Thinking tokens arrive in `completion_tokens_details.reasoning_tokens`, which `extract_openai_usage` already reads.
- Thinking shares `max_tokens`: with `max_tokens=8` the answer was empty because all eight tokens went to reasoning. Live dossier calls keep their current caps.

### 2.4 The ledger — `newsroom/tool_loop/go_allowance.py`

```python
record(path, *, at, model, label, allowance_usd, list_cost_usd, billing_source) -> None
entries(path, since=None) -> list[dict]
```

- Appends one JSON object per line to `data/usage/go_allowance.jsonl` (`MU_GO_ALLOWANCE_FILE` overrides; the directory is created if missing).
- Append is best-effort: an `OSError` prints one warning and returns, exactly like `db.llm_usage`: metering never stops a run.
- Called from `run_openai_tool_loop` once per request that reports an allowance. No database write.

### 2.5 The watchdog — `tools/go_usage.py`

```
python3 -m tools.go_usage [--source auto|api|ledger] [--monthly-limit USD]
                          [--five-hour-threshold PCT] [--weekly-threshold PCT] [--monthly-threshold PCT]
                          [--max-age HOURS] [--json]
```

- **`--source api` (the default under `auto`):** `GET {OPENCODE_GO_BASE_URL without the trailing /openai/v1}/v1/usage`, i.e. `https://opencode.ai/inference/go/v1/usage` (S6b), with `OPENCODE_GO_API_KEY` and a `project-mu/…` User-Agent (Cloudflare answers a generic agent with 403 code 1010). Returns `rolling`, `weekly`, `monthly` as `{percent, resetsAt}` — already workspace-wide, so agent sessions are counted. This is the authoritative number.
- **`--source ledger`:** sums `allowance_usd` from `data/usage/go_allowance.jsonl` over the rolling 5 hours, 7 days and the UTC month. That is a **dollar** figure for Mu's own traffic, not a percentage: Go's monthly limit is per model (DeepSeek V4.1 Flash $100, GLM-5.3-Flash $60, Kimi K3 $15, …), so the tool does not guess one. It judges thresholds only when `--monthly-limit` (or `MU_GO_MONTHLY_LIMIT`) is given; without it, ledger mode prints the dollars and exits 1 ("limit unknown"), the same way `tools/usage_check.py` treats a stale file. The Go shares stay fixed: 5-hour 20%, weekly 50% of that limit.
- Thresholds default `85` / `99` / `95` for 5-hour / weekly / monthly. Exit 0 under every one, 2 at or over any, 1 when no source answers (no key, no network, and no ledger).
- Prints one line: `go allowance: 5h 2% (resets 13:04Z) · weekly 1% (resets Nov 16) · month 0% (resets Dec 12) - under 85%/99%/95%`. The ledger source prints dollars instead of percentages.
- `--json` returns the three windows, each `{percent|usd, limit_usd, threshold, over, resets_at}`, plus `source` and `stale`.
- Whether the API percentage is per-model or for the whole workspace is not documented; either way it is a superset of Mu's traffic, so the watchdog errs high, never low. Say so in the docstring.

### 2.6 dossier_pilot

- `cost()` prefers `list_cost_usd` from the response when present; otherwise it keeps the local peak/off-peak table (S3) as the fallback.
- The `--budget-usd` ceiling, reservation and settlement logic are unchanged.
- Each live call also writes the allowance to the ledger through §2.4.

### 2.7 `.env.example`

Adds, with no secret value:

```
OPENCODE_GO_API_KEY=your_opencode_go_key_here   # Console > Account > Personal API keys (oc_sk_...)
OPENCODE_GO_BASE_URL=                           # optional; defaults to the Go OpenAI-compatible path
```

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | No Go key on the machine. | **Resolved 2026-11-12:** JR's key is in the git-ignored `.env` as `OPENCODE_GO_API_KEY`. | — |
| R2 | The SDK may drop `usage.opencode`. | **Resolved:** `usage.model_dump()` contains `opencode.{allowance, billing_source, list_cost, tokens}`; `extract_openai_usage` reads the dump. | — |
| R3 | Which base path bills to Go. | **Resolved:** the `/go/` path answers normally; the plain path returns 402 "Insufficient balance". | — |
| R4 | The ledger only sees Mu's own traffic, so agent sessions would be invisible. | **Resolved:** `GET /inference/go/v1/usage` reports rolling / weekly / monthly percentages workspace-wide (2% / 1% / 0% at the spike). The watchdog defaults to it; the ledger is the fallback. | If the endpoint changes shape, `--source ledger` still works and the tool says which source it used. |
| R5 | Go may reject DeepSeek's `reasoning_content` replay. | **Resolved:** Go returns no `reasoning_content` at all, and a tool call succeeds. The replay stays (inert here, required by the direct API). | — |
| R6 | Free models answer only inside OpenCode (S7), so a future "free model" config would fail here. | Documented in §2.1 and plan B. | Plan B must benchmark them through a different route. |
| R7 | The API percentage may be per-model rather than workspace-wide. | Undocumented; the spike cannot tell with one model in use. | It still over-counts nothing Mu spends, so the watchdog errs high. Note it in the docstring. |

**Spike G1 — done 2026-11-12, 04:05 EST.** One text call and one tool call to each path with `model="deepseek-v4.1-flash"` and `x-opencode-session: mu-spike-g1`:
- `/inference/openai/v1` → **402 Insufficient balance** (it wants balance credit, not Go).
- `/inference/go/openai/v1` → text call ok with `cost: 0`, `billing_source: "go"`, `allowance: 1.206e-05`, `list_cost: 2.01e-05`; tool call returned `get_weather({"city":"Boston"})`, no `reasoning_content`, `completion_tokens_details.reasoning_tokens: 0`.
- `max_tokens=8` produced empty text (all eight tokens spent thinking) — keep real caps.

**Spike G2 — done 2026-11-12, 04:06 EST.** `/inference/go/v1/models` lists the Go models; `/inference/go/v1/usage` answers `{"usage": {"rolling": {percent: 2, resetsAt}, "weekly": {percent: 1}, "monthly": {percent: 0}}}`. `/api/usage`, `/console/api/usage`, `/v1/usage` and `/inference/v1/usage` are 404. A request without a browser-like User-Agent gets Cloudflare 403 code 1010, so the client sends `project-mu/…`.

## 4. Tests (synthetic, no network, no live DB)

- `tests/test_openai_tool_loop.py` (extend) →
  - `opencode-go` is in `PROVIDER_ENDPOINTS`; `resolve_endpoint` reads `OPENCODE_GO_BASE_URL`/`OPENCODE_GO_API_KEY` and raises without the key.
  - `resolve_endpoint("deepseek")` returns the Go base URL by default and the old one when `DEEPSEEK_BASE_URL` is set.
  - `deepseek-flash` becomes `deepseek-v4.1-flash` for both `deepseek` and `opencode-go`; other model ids pass through untouched.
  - `router.resolve_tool_loop_provider` resolves `opencode-go`, `opencode-go:…`, `opencode:…` and keeps `deepseek:…` working; the prefix is stripped.
  - `extract_openai_usage` picks up `cost`/`list_cost`/`allowance`/`billing_source` from a fake usage object (dict and `model_extra` shapes) and returns `None` for each when absent.
  - A loop run with a fake client that reports an allowance appends exactly one ledger line per request; a run whose ledger write fails still returns a successful result.
- `tests/test_go_usage.py` (new) →
  - API source: a stubbed HTTP layer returns a `{rolling, weekly, monthly}` body → the tool prints percentages and exits 0/2 on the thresholds; a 403/404 falls back to the ledger under `auto` and exits 1 under `--source api`;
  - ledger source: window sums from a fixture (entries inside and outside 5h, 7d, month); with `--monthly-limit 100` it turns them into percentages and exits 0/2, without it it prints dollars and exits 1;
  - missing ledger and no API → exit 1; stale ledger with entries in the month → reported with `stale: true` and still 0/2;
  - `--json` shape carries both percent and usd fields; `--monthly-limit` changes only the ledger thresholds;
  - no test touches the network: `urllib`/`httpx` is monkeypatched.
- `tests/test_dossier_pilot.py` (extend) →
  - `cost()` uses `list_cost_usd` when present and the local table when absent;
  - the ceiling still stops a run over budget with the new source.

Run with `pytest -m "not integration" tests/test_openai_tool_loop.py tests/test_go_usage.py tests/test_dossier_pilot.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Go provider, alias, usage capture and watchdog

- **Owns:** `newsroom/tool_loop/openai_loop.py`, `newsroom/tool_loop/router.py`, `newsroom/tool_loop/go_allowance.py` (new), `tools/go_usage.py` (new), `tests/test_openai_tool_loop.py`, `tests/test_go_usage.py` (new)
- **Reads:** `newsroom/tool_loop/openai_loop.py`, `newsroom/tool_loop/router.py`, `tools/usage_check.py`, `db/llm_usage.py`, this plan §2.1–2.5
- **Depends on:** none
- **Done when:** a caller can name `opencode-go` or `deepseek`, a response's allowance reaches `data/usage/go_allowance.jsonl`, and `tools/go_usage.py` reports the three windows with the documented exit codes.
- **Verify:** `pytest -m "not integration" tests/test_openai_tool_loop.py tests/test_go_usage.py` → all pass; `python3 -m tools.go_usage --json` with a fixture `MU_GO_ALLOWANCE_FILE` → three windows and `source` set.

### U2: dossier_pilot budget and env documentation

- **Owns:** `tools/dossier_pilot.py`, `tests/test_dossier_pilot.py`, `.env.example`
- **Reads:** `tools/dossier_pilot.py` (pricing, reserve/settle, live guard), U1's `extract_openai_usage` and ledger, `.env.example`
- **Depends on:** U1
- **Done when:** a live dossier call is priced from Go's `list_cost_usd` when present, falls back to the local table otherwise, and both new env variables are documented.
- **Verify:** `pytest -m "not integration" tests/test_dossier_pilot.py` → all pass; `grep -c 'OPENCODE_GO_API_KEY' .env.example` → 1.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1 | — | The provider, the ledger and the watchdog share one interface and one test file. |
| 2 | U2 | U1 | Prices from U1's captured `list_cost_usd`. |

If JR wants this built in one pass, U1 and U2 can be built inline in a single session; the split exists only because U2 reads U1's fields.

**File ownership check:**

| File | Unit |
|---|---|
| `.env.example` | U2 |
| `newsroom/tool_loop/go_allowance.py` | U1 |
| `newsroom/tool_loop/openai_loop.py` | U1 |
| `newsroom/tool_loop/router.py` | U1 |
| `tests/test_dossier_pilot.py` | U2 |
| `tests/test_go_usage.py` | U1 |
| `tests/test_openai_tool_loop.py` | U1 |
| `tools/dossier_pilot.py` | U2 |
| `tools/go_usage.py` | U1 |

## 7. Spec changes when built

- §7.12: the provider list gains `opencode-go` and the `deepseek` alias; the usage fields become `cost_usd`, `list_cost_usd`, `allowance_usd`, `billing_source`; the model alias rule is stated.
- §1.2: one sentence in "Agent routes" saying the pipeline's DeepSeek calls also go through Go, and that vision does not (Muse plan).
- Repository Layout: new `newsroom/tool_loop/go_allowance.py` and `tools/go_usage.py` lines; the `openai_loop.py`, `router.py`, `dossier_pilot.py` and `.env.example` lines gain the new keys.
- No schema, migration or `init_db.sql` change.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | ~~Is $100/month the right ledger default?~~ Settled by the explanation: there is no default. Ledger mode reports dollars and needs `--monthly-limit` to judge, because Go's limit is per model. The API source reports percentages and never needs it. | No default; `--monthly-limit` / `MU_GO_MONTHLY_LIMIT` when someone wants a dollar verdict offline. |
| Q2 | Personal key or a service account for the pipeline? | Personal now (it is one machine and JR is the only member); a service account when the pipeline ever runs where JR is not signed in. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
