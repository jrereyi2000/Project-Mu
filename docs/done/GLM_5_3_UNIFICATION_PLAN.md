# Project Mu: GLM-5.3-Flash Unification Plan

**Status:** approved by JR (2026-11-12); built and on main (2026-11-12). `/execute-plan` runs it as label `glm-unification` (DeepSeek session, 2026-11-12).
**Builds on:** [`MODEL_ROUTING_EVALUATION.md`](../reference/MODEL_ROUTING_EVALUATION.md) §8.1 and [`ARTICLE_FACT_GATE_PLAN.md`](../not_started/ARTICLE_FACT_GATE_PLAN.md) (both read the same measured week), PROJECT_SPEC §7.7 (agent memory), §7.8 (Agent Wire), §7.11 (personas), §2.21b (inbox voice), [`AGENT_WIRE_VOICE_PLAN.md`](AGENT_WIRE_VOICE_PLAN.md) (**built 2026-11-09** — the voice rules are settled, so a model swap is a clean comparison), [`OPENCODE_GO_PIPELINE_PLAN.md`](OPENCODE_GO_PIPELINE_PLAN.md) (the Go provider and allowance watchdog).

**Goal:** one text model — `glm-5.3-flash` on OpenCode Go — writes everything Mu says in words: articles (already done, `ac8df19`), Agent Wire posts, season memories, press conferences, scouting prose, GM lines and inbox voice. Gemini stops being a text model for Mu and stays only where Go has no equivalent: **embeddings** and **vision**.

**Delivers:**
- A `go` provider in `engines/llm_provider.py` (`LLM_PROVIDER_<LABEL>=go`), so every short call can name a Go model without a new client per call site.
- `newsroom/social_writer.py` running on Go, keeping its JSON contract, rewrite pass and voice rules.
- `SOCIAL_MODEL`, `MEMORY_MODEL`, `PRESS_MODEL`, `INBOX_MODEL`, `SCOUT_MODEL`, `GM_MODEL` — one env per site, all defaulting to `glm-5.3-flash`, all overridable back to a Gemini id.
- A measurement worksheet (`docs/reference/GLM_UNIFICATION_MEASUREMENTS.md`) with the latency and JSON-parse numbers each site had to pass.

**As built (orchestrator, 2026-11-12):**
- **U2 also owns `newsroom/social_publish.py`.** §1 names it in scope; §5's ownership table omitted it, and without it a Go social model still builds a Gemini client (`client = client or _gemini_client()` in `run_social_week` / `run_team_social`) and needs `GEMINI_API_KEY`. U2 changes only those client constructions to follow the model id.
- **The §3 spikes run right after U1, not before wave 1.** `tools/latency_spike.py` drives `make_llm`/`generate_text`, so it can only reach a Go model once U1's `go:` model prefix exists (posted as M-0658). Nothing in U1–U3 depends on the result: no default flips in any unit (U4 is JR's).
- **`SCOUT_MODEL` and `GM_MODEL` are not added by a unit.** §2.3's seam for scouting prose and GM lines is `gm_llm.make_llm` + the provider, which U1 already unlocks (`model="go:glm-5.3-flash"`, `LLM_PROVIDER_<LABEL>=go`); §5 assigns no unit `engines/gm_llm.py` or the scouting/commissioning callers, so the named envs belong with U4's flip, if JR wants them then.
- **U2 as built:** the client follows the model id, so `SOCIAL_MODEL=glm-5.3-flash` reaches Go with no `GEMINI_API_KEY`; `provider="gemini"` stays the per-call rollback lever, and `social_model_compare` works unedited (a Go id swaps the Google-pointed client for the Go one). A Go post reports `cost_usd: 0.0` like Ollama — the allowance is metered by `tools/go_usage`/`UsageMeter` — and Go posts use the model's default thinking (no `low` cap); R2 measured no thinking cost either way.
- **U3 as built:** one shared rule (`agent_memory.model_provider`, a 5-line sibling of `resolve_provider` in a file U3 owns) is used by memory, `press_room.press_provider` and `inbox.voice.default_llm`; it belongs beside `resolve_provider` if it ever has to be shared outside these sites. **It also restores a silently broken path:** `SwarmDispatcher._record_agent_memory` passes the *article's* model, and articles have been `glm-5.3-flash` since `ac8df19`, so takes extraction had been asking Gemini for a GLM model and failing with `MEMORY NOT RECORDED`; it now routes to Go, and `tools.llm_compare --task memory_takes` parses 3/3 on real prompts. Season summaries keep `AGENT_MEMORY_MODEL` (Gemini) until U4.
- **U1 as built:** `DEFAULT_GO_MODEL` is defined from `MU_GO_TEXT_MODEL` at import as §2.1 says, but `resolve_model("go", ...)` reads `default_go_model()` at call time so a test or a run can set the env after import. `make_llm(provider="go")` returns `None` (its documented template contract) when no Go key exists, while `generate_text(..., provider="go")` raises the transport's `ValueError` — nothing ever answers a Go request with Gemini. `engines/llm_provider.estimate_cost` still prices a Go id at the Gemini rate (only `tools/llm_compare` reads it); the plan's measurements use `tools/go_usage`, so this waits for U4.
- **The §5 U1 Verify named `tests/test_gm_llm.py`, which does not exist.** It now names `tests/test_llm_provider_go.py`, `test_llm_provider.py`, `test_llm_config.py`, `test_gm_personas.py`, `test_weekly_capture.py`, `test_inbox_voice.py` — 156 passed on main after the merge.
- **`MEMORY_MODEL` already exists as `AGENT_MEMORY_MODEL`** (`newsroom/weekly_run.py:32`, default `gemini-3.8-flash`, read by `refresh_season_summaries`). U3 keeps that name and default; `SOCIAL_MODEL` (`weekly_run.py:33`) and `PRESS_MODEL` (`press_room.py:55`) already exist the same way. U3 adds `INBOX_MODEL` (and a provider route) for the inbox voice.

---

## 0. Survey (code facts that set the scope)

| # | Fact | Consequence |
|---|---|---|
| S1 | `engines/llm_provider.resolve_provider` accepts only `gemini` and `ollama` (`:100-125`); `resolve_model` (`:128`) and `generate_text` (`:327`) follow that split. `make_llm` (`engines/gm_llm.py:60`) builds `GeminiText` for everything that is not Ollama. | Every engine short call (GM lines, scouting, inbox voice, press) has **no** Go path today; one provider branch unlocks all of them. |
| S2 | `newsroom/social_writer.py:808` builds its own `genai.Client(api_key=key)` and calls `client.models.generate_content` with `make_content_config`; no explicit cache API is used (the `cache` references are usage fields). | The Agent Wire writer needs a client swap and a model id, not a rewrite. |
| S3 | `newsroom/agent_memory.py:571 gemini_text(api_key, model, prompt, ...)` is a single `generate_content` call with a 0.3 temperature. | The simplest port; it already takes a model. |
| S4 | `newsroom/press_room.py:55 PRESS_MODEL` (env, default None → provider route via `generate_text(source="press")`); `press_agents.py` does the reporter questions through the same seam. | One env flip once the latency gate passes. |
| S5 | Inbox voice budget is `INBOX_LLM_BUDGET_S = 4.0 s`, set by the P1-09 spike (p50 1.99 s, p95 2.46 s on `gemini-3.8-flash` with Madden open — `docs/reference/P1_LATENCY_AND_INBOX_GATE.md`). | **The hard gate.** A model that misses p95 4 s on a warm call cannot take the inbox, whatever its prose. |
| S6 | `newsroom/league_memory.py:27` uses `gemini-embedding-001`, stored as `REAL[]` with brute-force search, and already supports a local embedder hook. | Embeddings **stay on Google** (or move to a local embedder, a separate decision). Out of scope. |
| S7 | `extract.py:50 MODEL` is `gemini-3.8-flash` for **screenshot** extraction (vision), plus flagged-row re-reads. | Vision stays; Go's text models don't see a screenshot table. Out of scope. |
| S8 | `tools/social_model_compare.py` already writes 20 assignments with several models, saves a blind A/B Markdown with an answer key, and has a `--dry-run`. `tools/llm_compare.py` does the same for 9 short tasks. | The evidence gate for tweets and inbox replies is already built; it needs Go models in its list. |
| S9 | `AGENT_WIRE_VOICE_PLAN` is **built and on main (2026-11-09)**, so the prompt, JSON contract and voice rules are fixed. | A model swap can be judged on a stable prompt instead of confounding prompt and model. |
| S10 | Per-model Go limits: GLM-5.3-Flash **$60/month, $12 per 5 hours**. Measured article spend ~$0.32 per six articles; the short calls were ~$45/mo on Gemini. | The allowance is the budget; no cash. The 5-hour window is the burst constraint on a Sunday run. |

## 1. Scope

**In:**
- The `go` provider seam, used by every text site below.
- Agent Wire posts (`newsroom/social_writer.py`, `newsroom/social_publish.py`), season memories (`agent_memory`), press conferences (`press_room`, `press_agents`), scouting prose (`scouting_reports.rewrite`, `scouting_notes`), GM lines (`gm_llm` labels), inbox voice (`engines/inbox/voice.py`), and `custom_commission.classify_prompt`.
- The measurements: latency p50/p95 per site, JSON-parse rate, and JR's blind read on 20 tweets and 8 inbox replies.

**Out (non-goals):**
- `gemini-embedding-001` (`league_memory`, `newsroom/league_memory.py`) — Go serves no embedding model; the local-embedder hook is the escape route and its own decision.
- `extract.py` vision and every screenshot path.
- `tools/madden_pilot/vision.py` (Muse Ember's plan).
- Any image generation, headshots or mod assets.
- Re-deciding the article model: `ac8df19` already put articles on GLM-5.3-Flash.

## 2. Design

### 2.1 The `go` provider (`engines/llm_provider.py`)

- `resolve_provider` gains `go`: explicit `provider="go"`, a `go:`/`go/` model prefix, or `LLM_PROVIDER_<SOURCE|LABEL>=go` (the existing per-label env convention at `:107-118`).
- `resolve_model("go", ...)` defaults to `DEFAULT_GO_MODEL = os.environ.get("MU_GO_TEXT_MODEL") or "glm-5.3-flash"` and strips a `go:` prefix.
- A `GoText` class beside `GeminiText`: builds `newsroom/tool_loop/go_transport.go_client()` once and caches it per process, calls `client.models.generate_content` with `thinking_level` mapped to a `ThinkingConfig` when the model accepts one, and reads usage with the existing `_usage`-style fields. It must expose the same `.model`, `.label` and callable interface `GeminiText` does, so `make_llm`, press and inbox need no signature change.
- `generate_text(..., provider="go")` works, so `tools/llm_compare.py` can list Go models too.
- **No default changes in this unit.** Every site keeps `gemini-3.8-flash` until its gate passes.

### 2.2 Agent Wire (`newsroom/social_writer.py`)

- `SOCIAL_MODEL` (env, default `glm-5.3-flash` once U2 lands; pinned `gemini-3.8-flash` before that) plus a `provider` argument that picks the client: `go_client()` for a Go id, `genai.Client` otherwise.
- The JSON contract, the two-pass rewrite (`finish`, `_rewrite_asks`) and the voice rules are untouched.
- The comparison that decides it: `python3 -m tools.social_model_compare --week <finished week> --models gemini-3.8-flash glm-5.3-flash muse-spark-1.3-contributor --prompts new` → a blind read JR does on 20 assignments. Acceptance: GLM's posts are not worse than Gemini's on voice and hashtag hygiene, and the JSON-parse rate is 100%.
- **Cost note:** Agent Wire is ~100 posts a week at ~170 calls a day; on GLM prices that is roughly $10–15 of its $60 allowance.

### 2.3 Memories, press, scouting, GM lines, inbox

| Site | Seam | Gate before flipping |
|---|---|---|
| Season summaries, takes (`agent_memory`) | `generate_text(source="memory", provider="go")` | JSON/take parse rate 100%, and a memory block still reads like the writer |
| Press conferences (`press_room`, `press_agents`) | `generate_text(source="press", provider="go")` | p95 latency inside the conference budget; a 10-question dry conference read by JR |
| Scouting prose (`scouting_reports.rewrite`) | `gm_llm.make_llm(label="scout_report")` with the `go` provider | 10 reports read by JR; no invented numbers (the gate in `ARTICLE_FACT_GATE_PLAN` is article-only) |
| GM lines (`gm_talks`, `gm_rationale`, `gm_persona`) | `make_llm` + provider | the existing `gm_writer` validator still passes on every line; the `llm_compare` GM tasks show no regression |
| Inbox voice (`engines/inbox/voice.py`) | `generate_text(source="inbox", provider="go")` | **p95 ≤ 4.0 s warm with Madden open** (P1-09's budget); one inbox thread read by JR |

### 2.4 Budget and rollback

- Every site has its own env (`SOCIAL_MODEL`, `MEMORY_MODEL`, `PRESS_MODEL`, `INBOX_MODEL`, `SCOUT_MODEL`, `GM_MODEL`), so one site can go back to `gemini-3.8-flash` without a code change.
- `tools/go_usage.py` stays the watchdog; the 5-hour window is the one to watch on a Sunday (a full slate plus 100 posts plus press).
- A site whose gate fails keeps Gemini and says so in the measurement doc; the plan is not "all or nothing".

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | GLM-5.3-Flash may be too slow for the 4 s inbox budget. | Warm-call p50/p95 with Madden open, the P1-09 method, before the inbox flip. | Inbox keeps Gemini; nothing else is affected. |
| R2 | Go's translation may not honour `thinking_config`, so short calls get slow and expensive. | Compare thinking tokens per call for `low` vs default on a 10-prompt sample. | Accept the default; if cost is material, use a non-thinking Go model (e.g. `gpt-6-luna`) for the short sites. |
| R3 | JSON contracts (posts, commissions, GM rationale) may parse worse on a new model. | `tools/llm_compare` and `social_model_compare` report parse failures per model. | Keep the retry/repair path; fail the gate if parse < 100%. |
| R4 | A shared blind spot between writer and checker (the Carson case: both believed a second-year player was a rookie). | Not a model-selection risk here; the fact gate addresses it for articles. | For tweets there are no facts to check; keep the desk's own number rules. |
| R5 | The 5-hour window is shared across every site; a Sunday run could hit $12 of GLM. | Sum the measured per-site spend from one weekly run against the $12 window. | Stagger the social pass, or split sites across models (social on `gpt-6-luna`, articles on GLM). |
| R6 | Personality drift: 192 voices on a model that has not been tuned against them. | JR's blind read on 20 posts; the voice rules are fixed (S9). | Social stays on Gemini, which is the one site where voice is the product. |

**Spike first:** the inbox/press latency probe (R1) and a thinking-level probe (R2) — both are ~20 cheap calls with `tools/latency_spike.py` and can run before wave 1. Neither needs the PC's Madden except for the real p95, which is the P1-09 method and needs `CLAIM · Madden PC`.

## 4. Tests (synthetic, no live DB)

- `tests/test_llm_provider_go.py` (new) →
  - `resolve_provider` returns `go` for an explicit provider, a `go:` model prefix and `LLM_PROVIDER_PRESS=go`;
  - `resolve_model("go", ...)` defaults to `glm-5.3-flash` and strips the prefix;
  - `generate_text(..., provider="go")` with a fake client calls the model and returns text; a missing key raises the documented error, never a silent Gemini fallback;
  - `GoText` exposes `.model`/`.label` and `make_llm` builds it for a `go` label.
- `tests/test_social_writer_go.py` (new) →
  - a Go model id selects the Go client and a Gemini id selects the Gemini client (both faked);
  - the JSON contract and the two-pass rewrite behave identically with a fake Go response;
  - `SOCIAL_MODEL` unset keeps `gemini-3.8-flash`.
- `tests/test_agent_memory_go.py` (extend an existing memory test) →
  - `gemini_text`'s Go sibling sends the same prompt and temperature through `generate_text(provider="go")`.
- `tests/test_inbox_voice_go.py` (extend) →
  - a Go voice call still falls back to the kind's template on timeout or an invalid reply, at the same 4.0 s budget.

Run with `pytest -m "not integration" tests/test_llm_provider_go.py tests/test_social_writer_go.py tests/test_inbox_voice.py tests/test_agent_memory.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`, `docs/reference/GLM_UNIFICATION_MEASUREMENTS.md`.

### U1: The `go` provider

- **Owns:** `engines/llm_provider.py`, `tests/test_llm_provider_go.py` (new)
- **Reads:** `engines/llm_provider.py:100-360`, `engines/gm_llm.py:60`, `newsroom/tool_loop/go_transport.py`, `docs/reference/P1_LATENCY_AND_INBOX_GATE.md`
- **Depends on:** none
- **Done when:** any `generate_text`/`make_llm` caller can name `go` and get the Go model, with no default flipped.
- **Verify:** `pytest -m "not integration" tests/test_llm_provider_go.py tests/test_llm_provider.py tests/test_llm_config.py tests/test_gm_personas.py tests/test_weekly_capture.py tests/test_inbox_voice.py` → pass (the plan first named a `tests/test_gm_llm.py` that does not exist; these are the provider/GM-callable tests on the base commit), plus `python3 -c "import engines.llm_provider, engines.gm_llm"` with no Go key.

### U2: Agent Wire on Go

- **Owns:** `newsroom/social_writer.py`, `newsroom/social_publish.py` (the two client constructions only), `tests/test_social_writer_go.py` (new)
- **Reads:** `newsroom/social_writer.py:780-860`, `tools/social_model_compare.py`, `AGENT_WIRE_VOICE_PLAN.md` §2.6
- **Depends on:** U1
- **Done when:** `SOCIAL_MODEL=glm-5.3-flash` writes the week's posts through Go and the blind-read artefacts can be produced with `social_model_compare` including a Go model.
- **Verify:** `pytest -m "not integration" tests/test_social_writer_go.py` → pass; `python3 -m tools.social_model_compare --dry-run --models glm-5.3-flash` builds prompts without a call.

### U3: The remaining text sites

- **Owns:** `newsroom/agent_memory.py`, `newsroom/press_room.py`, `newsroom/press_agents.py`, `engines/inbox/voice.py`, `tests/test_agent_memory.py` (extend), `tests/test_inbox_voice.py` (extend), `tests/test_press_room.py` (extend, one test for the model→provider rule)
- **Reads:** each file's model construction, `engines/llm_provider.py`'s new branch, `docs/reference/P1_LATENCY_AND_INBOX_GATE.md`
- **Depends on:** U1
- **Done when:** each site takes its `*_MODEL` env, defaults to Gemini until flipped, and the inbox still degrades to templates on a timeout.
- **Verify:** `pytest -m "not integration" tests/test_agent_memory.py tests/test_inbox_voice.py tests/test_press_room.py` → pass.

### U4 (JR's, after the measurements): flip the defaults

Not a build unit. One line per site in `PROJECT_SPEC.md` and the env block, after the measurement doc shows the gates passed.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1 | — | The provider branch everything else needs; no behaviour change on its own. |
| 2 | U2, U3 | U1 | Different files; neither imports the other. |

**File ownership check:**

| File | Unit |
|---|---|
| `engines/inbox/voice.py` | U3 |
| `engines/llm_provider.py` | U1 |
| `newsroom/agent_memory.py` | U3 |
| `newsroom/press_agents.py` | U3 |
| `newsroom/press_room.py` | U3 |
| `newsroom/social_publish.py` | U2 |
| `newsroom/social_writer.py` | U2 |
| `tests/test_agent_memory.py` | U3 |
| `tests/test_inbox_voice.py` | U3 |
| `tests/test_press_room.py` | U3 |
| `tests/test_llm_provider_go.py` | U1 |
| `tests/test_social_writer_go.py` | U2 |

## 7. Spec changes when built

- §7.7/§7.8/§7.11: the model each site uses and its env override.
- §2.21b (inbox): the voice model, the 4.0 s budget, and the measured p95 that justified it.
- §7.12: the `go` provider beside the OpenAI-compatible and Gemini paths, and that embeddings/vision stay on Google.
- Repository Layout: `docs/reference/GLM_UNIFICATION_MEASUREMENTS.md`, the three test files.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Does Agent Wire go to GLM even though voice is the product? | **Judge it blind on 20 posts first.** If GLM is not worse, move it; the allowance is the same either way. |
| Q2 | What happens to embeddings? | **Stay on `gemini-embedding-001`.** Go has no embedding model; a local embedder is a separate decision and `league_memory` already has the hook. |
| Q3 | Does the inbox move if p95 lands above 4 s? | **No.** Keep Gemini for the inbox; one slow site must not make a conversation feel broken. |
| Q4 | One model everywhere, or a per-site mix? | **Start with GLM-5.3-Flash everywhere it passes**; a site that fails keeps Gemini by env, no code change. If the Sunday 5-hour window gets tight, split social onto a cheaper Go model. |
| Q5 | Do the short-call thinking levels stay capped? | Yes — `low` wherever the model honours it; measure the tokens, since thinking is most of the short-call bill. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | `glm-u1-provider` | `.../worktree/404d9f/glm-u1-provider` | merged | `838a058` |
| U2 | 2 | `glm-u2-wire` | `.../worktree/404d9f/glm-u2-wire` | merged | `80ec761` |
| U3 | 2 | `glm-u3-sites` | `.../worktree/404d9f/glm-u3-sites` | merged | `effd0a7` |
