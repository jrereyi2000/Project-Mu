# Project Mu: Newsroom Model Side-by-Side Plan

**Status:** approved by JR (2026-11-12); built and first run done (2026-11-12). U1 merged (`9278242`, `bae1055`); the six-slot comparison ran on `madden_2036` Week 18 with no failures, and its measured result is in [`MODEL_ROUTING_EVALUATION.md`](../reference/MODEL_ROUTING_EVALUATION.md) §8.1. Two legs remain and are not build work: JR's blinded read, and the pairwise judge, which returned no verdicts twice (thinking consumed the output cap; fixed, re-run after the Go 5-hour window resets at 13:04Z 2026-11-12).
**Builds on:** [`MODEL_ROUTING_EVALUATION.md`](../reference/MODEL_ROUTING_EVALUATION.md) (the fit check this answers), PROJECT_SPEC §7.3 (editorial slate) and §7.12 (dual ReAct tool loop), [`OPENCODE_GO_PIPELINE_PLAN.md`](../done/OPENCODE_GO_PIPELINE_PLAN.md) (the Go provider, allowance ledger and watchdog this reuses). This is the fleshed-out execution of **CLOSE_OUT_CHECKLIST P8-05**; its result feeds P8-06's per-job table.

**Goal:** answer one question with evidence instead of benchmarks: can a Go model write Mu's articles as well as `gemini-3.8-flash`, holding the persona voice and inventing no numbers over the newsroom's real 40-tool loop? The comparison must run the **production writer path** unchanged — same compiled prompts, same tools, same validation rewrites — and differ only in the client and model id. JR reads a blinded A/B/C sheet and the judge's pairwise verdict; then the switch (or the decision not to switch) is made on that, not on Elo.

**Delivers:**
- `newsroom/tool_loop/go_transport.py` — a `google-genai` client pointed at Go's Google-compatible endpoint.
- Two optional parameters on `SwarmDispatcher._execute_agent` (`client`, `capture`) so a comparison run can supply a client and receive the text, tool trace and usage without touching the database.
- `tools/article_model_compare.py` — compiles one week's real prompts, writes each with every named model, saves a JSON result set, a blinded A/B/C Markdown sheet for JR, and an optional judge verdict.
- `tests/test_go_transport.py`, `tests/test_article_model_compare.py` — offline, fake clients.

**JR's answers (2026-11-12):**
- **D1 — Slots.** The six recommended slots (Q1) are approved.
- **D2 — Judge.** `glm-5.3` pairwise plus JR's blinded read is approved.
- **D3 — Billing.** The first pass runs **entirely on OpenCode Go** where a Go model can serve it, so it costs **no cash**: Muse Spark 1.3, GLM-5.3-Flash and the judge bill to their Go monthly allowances. The one non-Go cell is the Gemini 3.8 Flash control, which runs on JR's **existing `GEMINI_API_KEY` credit** (verified working 2026-11-12 with a one-token call). Nothing is bought for this run.
- **D4 — Budget.** The guard is **allowance headroom, not dollars**: no pass may take a Go model past 50% of its 5-hour window or 25% of its month, and the Gemini arm is skipped (not bought) when the key has no credit.
- **D5 — Madden PC.** No claim; the run reads a database and calls APIs.
- **D6 — Training.** Muse Spark 1.3 trains on prompts; acceptable.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `newsroom/swarm_dispatcher.py:1568` builds `genai.Client(api_key=self.api_key)`; `:1601` creates the chat with the persona system instruction, `tools_for_gemini`, temperature 0.7 and AFC `maximum_remote_calls=50`. | The writer is Gemini-shaped, but only in the client construction. A Go client with the same interface needs no loop rewrite. |
| S2 | `newsroom/swarm_dispatcher.py:1612-1700` is the production post-processing: a synthesis follow-up when AFC returns no text, and one rewrite each for POWER_RANKINGS, ALL_PRO, AWARDS_TRACKER, stored ballots and SUPER_BOWL_GAME_RECAP. | A reimplementation would silently drop these. The comparison must call the production method, not a copy. |
| S3 | `newsroom/swarm_dispatcher.py:1506` `_execute_agent(...)` returns nothing: it prints, and writes only when `save_to_db=True` (`:1860+`). | The comparison needs the text, so `_execute_agent` needs an out-param (`capture`) — the only production edit in this plan. |
| S4 | `newsroom/swarm_dispatcher.py:1605-1625` wraps each tool in `make_wrapper` (counts calls, prints a preview, stringifies the result). | The same wrapper is where the comparison's tool trace is recorded (call name, args, result). |
| S5 | `newsroom/swarm_dispatcher.py:2228` `dispatch_weekly_national(brief, season, week, article_count=None, live=..., model=..., save_to_db=..., max_workers=5)` commissions the 10-slot slate and calls `compile_agent_prompt` per article (`:2270`). | The comparison can reuse commissioning and compilation with `live=False`, gaining real prompts for a real week. |
| S6 | `newsroom/swarm_dispatcher.py:895` `tools_for(at_id)` returns the base tool set plus `FANTASY_TOOLS` for `FANTASY_*` types. `:931` `compile_agent_prompt(...)` returns a dict with `system_instruction`, `task_instruction`, `display_name`, `handle`, `archetype`, `slot`, `article_type`. | Prompts are capturable without a model call. The article's tools are known per type. |
| S7 | `db/llm_usage.py:107` `UsageMeter` patches `client.models._generate_content`, so it counts **one HTTP request per call** (the AFC loop included), and `:118` records tokens per call. It writes only when the caller calls `flush()`. | The comparison can meter a Go client unchanged and keep the rows in memory, so nothing touches the live `llm_usage` table. |
| S8 | `newsroom/tool_loop/openai_loop.py:138` already has the `opencode-go` provider and `:296` `extract_openai_usage` reads `usage.opencode.{allowance,…}`. `newsroom/tool_loop/go_allowance.py` already appends to `data/usage/go_allowance.jsonl`. | The OpenAI-compatible Go path exists, but it is a **different loop**; using it for this test would confound model quality with loop behaviour. Not used here. |
| S9 | `tools/go_usage.py` reads `GET https://opencode.ai/inference/go/v1/usage` (workspace percentages, 5-hour/weekly/monthly) with `OPENCODE_GO_API_KEY`. | Authoritative dollar-accounting for a run, without parsing SDK internals: read it before and after each model's pass. |
| S10 | `tools/social_model_compare.py` is the existing side-by-side shape: `--models`, `--db`, `--dry-run`, `--out`, read-only connection, blind A/B Markdown with an answer key (`:131`), results JSON. `tools/llm_compare.py` does the same for short single-call tasks (no tools, skips `llm_usage` writes). | The new tool copies this CLI and the blind-read shape; it does not invent a new reporting convention. |
| S11 | `tools/dossier_pilot.py:475` writes `factual_review.csv` (one row per paragraph, columns `sample, paragraph, text, claim, source_reference, expected_value, verdict, reviewer_note`) and `review_key.json` mapping anonymised sample hashes to their arm. | The factual worksheet and the blinding scheme are already JR-reviewed; reuse both. |
| S12 | There is no test file for `SwarmDispatcher` today (`ls tests | grep -i swarm` is empty). `tests/test_weekly_run.py` imports `newsroom.weekly_run`, which imports the dispatcher lazily. | The seam needs its own small test with a fake client; it cannot lean on an existing dispatcher suite. |
| S13 | Spike, this session: `POST /inference/go/google/v1beta/models/<id>:generateContent` answers for Go models with `usageMetadata.opencode`; **all 12 probed models** returned `functionCall` with intact arguments; `x-opencode-session` is mandatory (400 `MissingSessionID` without it). Through the SDK's own AFC, Muse Spark 1.3 and Gemini 4 Argon executed a tool correctly while `glm-5.3-flash` once misread a schema. | The design is viable, but AFC over the newsroom's real tool set is unproven — that is the spike in §3, before wave 1. |
| S14 | `engines/llm_provider.resolve_provider` accepts only `gemini` and `ollama`, so the short calls (agent_wire, memory, gm_\*) have no Go path. | Out of scope: a separate plan if JR wants it (evaluation doc D4). |

## 1. Scope

**In:**
- A Go transport for the `google-genai` SDK (Go's Google-compatible endpoint) with the required session header.
- Two optional parameters on `_execute_agent` (`client`, `capture`) — no behaviour change when they are absent.
- `tools/article_model_compare.py`: one week, N named article slots, M models, real prompts and real tools, artifacts for JR and for the judge.
- A blinded A/B/C Markdown read for JR, with the answer key at the end.
- A pairwise judge (default `glm-5.3`, overridable) with both presentation orders, so position bias is measured rather than assumed.
- Tool-trace capture and a "numbers not in the tool output" grounding check per article.
- Go allowance accounting before/after each model pass, from `tools/go_usage.py`.

**Out (non-goals):**
- Switching production to a Go model. That is the follow-on plan this one decides, and it also needs `PROJECT_SPEC` §7.3 changes, an `llm_usage` model convention, and a rollback.
- The short-call path (§0 S14). Separate plan.
- Changing any prompt, persona, tool, article type or the 50-call ceiling. The comparison measures models, not prompt work.
- Agent Wire / social posts, press conferences, fantasy-specific prompt work.
- Any write to `news_feed`, `articles`, `llm_usage`, captures or the Madden PC.

## 2. Design

### 2.1 `newsroom/tool_loop/go_transport.py` (new)

```python
GO_GOOGLE_BASE_URL = "https://opencode.ai/inference/go/google"

def go_session_id(prefix: str = "mu-compare") -> str      # f"{prefix}-{os.getpid()}-{int(time.time())}"
def go_api_key() -> str | None                            # OPENCODE_GO_API_KEY from env, else .env via pipeline.notify.env
def go_client(api_key: str | None = None, session: str | None = None) -> genai.Client
def is_go_model(model: str) -> bool                       # True for a Go id, False for a Zen/Gemini id
```

- `go_client` returns `genai.Client(api_key=key, http_options=types.HttpOptions(base_url=GO_GOOGLE_BASE_URL, headers={"x-opencode-session": session or go_session_id()}))`, and raises `ValueError("OPENCODE_GO_API_KEY is not set")` when there is no key.
- `is_go_model` reads the id prefix only (`gemini-` → False; every other id in the comparison list → True). It exists so the harness and a later production switch share one rule.
- The module imports `google.genai` lazily inside `go_client`, so importing it never needs the SDK.

### 2.2 The `_execute_agent` seam (`newsroom/swarm_dispatcher.py`)

```python
def _execute_agent(self, compiled_prompt, season, week, team_id, live=False,
                   model="gemini-3.8-flash", save_to_db=False, slot=None,
                   article_type=None, client=None, capture=None):
```

- `client`: when not None, used instead of `genai.Client(api_key=self.api_key)` at `:1568`. `UsageMeter` still wraps it, exactly as today.
- `capture`: a dict, updated in place:
  - `capture["text"]` — the final `generated_text` (after synthesis and every validation rewrite).
  - `capture["failure_reason"]` — the existing value.
  - `capture["tool_calls"]` — list of `{"name", "args", "result", "error"}` appended inside `make_wrapper` (`:1605`).
  - `capture["usage_calls"]` — the meter's in-memory `calls` list, copied on exit.
  - `capture["rewrites"]` — the names of the validation rewrites that fired.
- Both default to None; every existing caller is unchanged. `capture["text"]` is set even when `save_to_db` is False.

### 2.3 `tools/article_model_compare.py` (new)

```
python3 -m tools.article_model_compare --season 2027 --week 8 \
    --models gemini-3.8-flash muse-spark-1.3-contributor glm-5.3-flash \
    --slots Hero Slot 1 Slot 4 Standalone "Fantasy 1" \
    [--db DSN] [--repeats 1] [--dry-run] [--no-judge] [--judge glm-5.3] \
    [--budget-usd 25] [--out verify_output/model_compare/<ts>/]
```

Flow:
1. **Read-only connection** (`conn.read_only = True`, as `social_model_compare.py` does), `load_personas`, the week's brief. Default target is the frozen archive `--db madden_2036 --season 2036 --week 18` (a complete 20-article slate with 595 completed games behind it); `--db` takes any franchise DSN, and `madden_mu2026` is the live one but currently sits at 2026 Week 1 with no slate.
2. **Compile the prompts once.** `commission_weekly_editorial_slate(season, week, target_articles=None)`, filtered to `--slots`; then `compile_agent_prompt(...)` per kept slot. Write `prompts.json` (system + task per article, writer, slot, article type, persona). `--dry-run` stops here.
3. **Per model, per article, per repeat:** build the client — `go_client()` when `is_go_model(model)`, else `genai.Client(api_key=gemini_key)` — then call the **production** `_execute_agent(..., live=True, save_to_db=False, client=client, capture=capture)` with `set_article_context(season, week)` set, one worker per model so the runs cannot interleave their Go allowance.
4. **Record** per run: model, article, repeat, wall seconds, `text`, word count, `tool_calls` (count and trace), `rewrites`, `failure_reason`, `usage_calls` (tokens per request, thinking included), and the Go allowance delta from `tools.go_usage --json` taken immediately before and after that model's whole pass.
5. **Write artifacts** to `verify_output/model_compare/<timestamp>/`:
   - `results.json` — everything above.
   - `blind_read.md` — per article, the three texts as **A/B/C in a seeded shuffle**, model names stripped, one `###` per label, and the answer key (`A = muse-spark-1.3-contributor`, …) at the very end. Same shape as `social_model_compare.save_blind_markdown`.
   - `factual_review.csv` — the `review_sheet` columns from S11, one row per paragraph, sample hash per (article, model), blinded.
   - `grounding.csv` — per article: every numeric token in the text that does **not** appear in that run's tool trace or the brief's fact sheet, with its sentence. A review aid, not a verdict: a number can legitimately come from the model's own arithmetic on tool values.
   - `summary.md` — one table per article: model, words, tool calls, rewrites, seconds, tokens, cost, grounding flags; then the aggregate.
6. **Budget guard (allowance, not cash):** before a pass, read `tools/go_usage --json` and refuse to start a Go model whose 5-hour window would pass 50% or whose month would pass 25% (a six-slot pass is ~1% of Muse Spark's month and ~2% of its 5-hour window, so this is a safety net, not a throttle). The Gemini control runs on the existing `GEMINI_API_KEY`; a quota or credit error marks that arm `skipped_no_credit` and the run continues with the Go models, which is the outcome JR asked for — nothing is bought.

### 2.4 The judge

- Default `--judge glm-5.3`: a different family from both contestants that matters (Gemini, Meta), on Go, Judgemark v4 0.726, and cheap enough that judging does not distort the budget.
- **Pairwise, not absolute:** for each article and each pair of models, two calls with the orders swapped; a win needs the same winner in both orders, otherwise `tie/order-dependent` (which is reported, not hidden).
- The judge prompt carries: the article's task brief (what the slot asked for), the persona's one-line description, and both texts, with explicit instructions to ignore length, penalise any number not supported by the supplied tool trace excerpt, and score **voice fidelity, factual grounding, structure compliance, cliché-free prose, specificity** on 1–5 before choosing.
- Output `judge.json` (per-pair winner, scores, one-line reason) and a `judge.md` table. `--no-judge` skips it for the first smoke run.

### 2.5 Cost: $0 cash, a sliver of allowance

Per-article list cost at the evaluation doc's token mix: Gemini 3.8 Flash ~$1.55, GLM-5.3-Flash ~$0.37, Muse Spark 1.3 ~$0.13. Where it lands:

| Arm | Six slots | Billed to | Cash |
|---|---:|---|---:|
| `muse-spark-1.3-contributor` | ~$0.80 | Go allowance ($60/mo, 5-hour window $12) | $0 |
| `glm-5.3-flash` | ~$2.20 | Go allowance ($60/mo, 5-hour window $12) | $0 |
| `glm-5.3` judge (36 calls) | ~$1.00 | Go allowance ($15/mo, 5-hour window $3) | $0 |
| `gemini-3.8-flash` control | ~$9.30 | existing `GEMINI_API_KEY` credit | $0 |

Variance is real (thinking tokens, tool-call depth), so the tool reports measured tokens and the allowance delta beside these estimates. The full nineteen-article slate is ~$2.5 / ~$7 / ~$29 across the same three arms.

### 2.6 Running it

The database and `newsroom/db_tools.py` live on the PC, so the run happens there, in the repo root, against `--db madden_2036` read-only. It holds **no** `CLAIM · Madden PC` (nothing touches Madden) and buys nothing, so it runs on JR's word. Order: `--dry-run` (prompts only, no calls) → one-article smoke on Muse → the six-slot comparison → JR's blind read → judge.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | The Gemini AFC loop may not survive Go's tool-schema translation over the newsroom's ~40 real tools (S13 saw one schema misread on `glm-5.3-flash` through the SDK). | **Spike S-A before wave 1:** compile one real article prompt on the PC and run it through `go_client()` with the real `tools_for(at_id)`; confirm tool calls execute, arguments are right, and the loop returns text. ~10 minutes, ~$0.10. | Fall back to `run_openai_tool_loop` for Go models and record that the comparison used two loops; or drop the Go candidates and report why. |
| R2 | Go models think by default and `thinking` shares `maxTokens`; the production path sets none, so a Go model may spend its budget thinking. Observed: 248–314 reasoning tokens for a two-word reply. | The token columns in `results.json` (thinking is already in `usage_metadata.thoughts_token_count`). | Add an explicit thinking level to the comparison only, and report both; do not tune prompts. |
| R3 | The 5-hour Go window may throttle a pass (6 articles × up to 50 tool calls on Muse is ~$1, well inside $12; on GLM-5.3-Flash ~$2.2, inside $12). | Read `go_usage --json` before/after; the tool prints the window percentages. | Stagger the models or reduce `--slots`. |
| R4 | Another agent session spending against the same Go key during a pass inflates the allowance delta. | The delta is compared with the summed token cost; a large gap is reported as `allowance_noise: true` rather than silently believed. | Use the token-derived cost as the number of record; the delta is corroboration. |
| R5 | Recompiling a past week's prompts today may not reproduce the facts the stored article saw (the DB moved on). That is fine — all models get the same prompt today — but it must not be mistaken for "re-judging last week's article". | `prompts.json` records the compiled brief and the season/week. | Say so in the summary; use the stored article only as a style reference for JR. |
| R6 | The judge may prefer its own family or length. | Both presentation orders per pair; the judge is told to ignore length; the judge is not a Gemini or Meta model. | Report order-dependent pairs separately and let JR's blind read decide those. |
| R7 | The Muse/Qwen-class models may refuse or truncate a persona's voice (e.g. an aggressive columnist). | `failure_reason` and word count per run; the blind sheet shows it. | Report as a per-model risk, not a prompt bug; a refusal is disqualifying for that slot. |
| R8 | Editing `_execute_agent` (2,400-line production file) could change production behaviour. | The seam is two default-None parameters and the capture writes; the existing path is unchanged. `tests/test_swarm_capture.py` runs the method with a fake client and asserts the no-capture path still returns and prints. | Revert the two parameters; the comparison tool then needs its own loop copy (accept the drift). |

**Spike first:** S-A (R1) — one real article prompt through Go's Google endpoint with the real tool set, run by the builder on the PC before wave 1. It answers the one question that could change the design.

## 4. Tests (synthetic, no live DB)

- `tests/test_go_transport.py` (new) →
  - `go_client()` raises without `OPENCODE_GO_API_KEY`; with a key it builds a client whose `base_url` is the Go Google path and whose headers carry `x-opencode-session`.
  - the session id is stable within a call and unique across processes (patched pid and clock).
  - `is_go_model` splits `gemini-3.8-flash` from `muse-spark-1.3-contributor`, `glm-5.3-flash`, `deepseek-v4.1-flash`.
  - the module imports with `google.genai` absent (patched import).
- `tests/test_swarm_capture.py` (new) →
  - `_execute_agent(..., client=fake, capture=d)` with a scripted fake chat: `capture["text"]` holds the final text, `tool_calls` records one entry per wrapper call with args and result, `usage_calls` carries the meter rows, and nothing is written when `save_to_db=False`.
  - a tool raising inside the wrapper records `error` and still returns a string to the model (today's behaviour).
  - a validation rewrite (POWER_RANKINGS) appends to `capture["rewrites"]` and replaces `text`.
  - `_execute_agent` without `capture` behaves exactly as before (the fake chat is called the same number of times).
- `tests/test_article_model_compare.py` (new) →
  - `--dry-run` compiles prompts, writes `prompts.json`, makes no client call and opens no connection beyond a fake.
  - a fake runner writes `results.json` with one entry per (model, article, repeat) and a `summary.md` whose cost column matches the token rows.
  - the blind sheet shuffles A/B/C with a seeded `random.Random(seed)` and ends with an answer key that maps every label back to a model.
  - `grounding.csv` flags a fabricated number ("4,812 yards" absent from the trace) and does not flag one present in the trace.
  - the budget guard refuses a pass whose estimate exceeds `--budget-usd` before any call.
  - the judge runs both orders, and a pair split across orders is reported `order-dependent`, not a win.
  - `--no-judge` writes no `judge.json`.

Run with `pytest -m "not integration" tests/test_go_transport.py tests/test_swarm_capture.py tests/test_article_model_compare.py`. No test opens a database or the network; the Go and Gemini clients are fakes.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Go transport, the `_execute_agent` seam, and the comparison tool

- **Owns:** `newsroom/tool_loop/go_transport.py` (new), `newsroom/swarm_dispatcher.py` (the two optional parameters only), `tools/article_model_compare.py` (new), `tests/test_go_transport.py` (new), `tests/test_swarm_capture.py` (new), `tests/test_article_model_compare.py` (new)
- **Reads:** `newsroom/swarm_dispatcher.py:1506-1720` and `:2228-2290`, `db/llm_usage.py:107`, `newsroom/tool_loop/openai_loop.py:138`, `tools/go_usage.py`, `tools/social_model_compare.py`, `tools/dossier_pilot.py:475`, this plan §2
- **Depends on:** none (spike S-A runs before it)
- **Done when:** `--dry-run` compiles a week's prompts without a model call; a comparison run with a fake client produces `results.json`, `blind_read.md` with a correct answer key, `grounding.csv` and `summary.md`; and the production `_execute_agent` path is unchanged when `client` and `capture` are None.
- **Verify:** `pytest -m "not integration" tests/test_go_transport.py tests/test_swarm_capture.py tests/test_article_model_compare.py` → all pass; `python3 -m tools.article_model_compare --help` → the documented flags.

One unit: the transport, the seam and the tool are one interface, and splitting them costs two cold starts for no parallelism.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1 | — | The whole feature is one unit; it can be built inline without `/execute-plan`. |

**File ownership check:**

| File | Unit |
|---|---|
| `newsroom/swarm_dispatcher.py` | U1 |
| `newsroom/tool_loop/go_transport.py` | U1 |
| `tests/test_article_model_compare.py` | U1 |
| `tests/test_go_transport.py` | U1 |
| `tests/test_swarm_capture.py` | U1 |
| `tools/article_model_compare.py` | U1 |

After the build, the run itself (§2.6) is JR's call and happens on the PC; the findings go into `MODEL_ROUTING_EVALUATION.md` §8 as a new "result" subsection, not into this plan.

## 7. Spec changes when built

- §7.12 (dual ReAct tool loop): add the Go Google-compatible transport as a third client shape, the mandatory `x-opencode-session` header, and `tools/article_model_compare.py` as the article-level comparison tool beside `tools/llm_compare.py` and `tools/social_model_compare.py`.
- §7.3 (editorial slate): one sentence that the writer path accepts an injected client for model comparison and does no database write in that mode.
- Repository Layout: `newsroom/tool_loop/go_transport.py`, `tools/article_model_compare.py`, and the three test files.

## 8. Decisions for JR

| # | Question | JR's answer |
|---|---|---|
| Q1 | Which slots make the comparison? | **Approved as recommended:** six — `Hero`, `Slot 1`, `Slot 4`, `Standalone`, `Fantasy 1`, one beat/local slot. |
| Q2 | Who judges? | **Approved:** `glm-5.3` pairwise plus JR's blinded read. |
| Q3 | How many models? | **Three:** `gemini-3.8-flash` (control, on existing Gemini credit), `muse-spark-1.3-contributor`, `glm-5.3-flash`. Extra models only if the three disagree. |
| Q4 | Billing and budget | **Everything on Go where it can be, no cash.** Go models bill to their allowances; Gemini uses the existing key's credit; the guard is allowance headroom (50% of a 5-hour window, 25% of a month). |
| Q5 | Does this run need a `CLAIM · Madden PC`? | **No.** It reads `madden_2036` read-only and calls APIs. |
