# Which model should write the newsroom? An OpenCode Go fit check

**Date:** 2026-11-12 · **Asked by:** JR · **Status:** research only, nothing built
**Question:** the newsroom writes articles on `gemini-3.8-flash`, which is weaker on creative writing than several models on the OpenCode Go subscription. Could the newsroom — and the engines' short "decision" calls — move to a better Go model and still fit that model's monthly Go allowance?

---

## 1. Bottom line

1. **No. The better models do not fit the newsroom's allowance — by 20× to 33×.** Kimi K3, Kimi K3.1 Pro, GLM-5.3, GLM-5.2, Grok 4.7, Qwen3.8 Max and Gemini 4 Argon all score far above Gemini 3.8 Flash on EQ-Bench, and all have a **$15–$30 monthly Go limit**. At Mu's current newsroom volume that allowance buys **2–8 articles a month**, not a week's slate. Go Plus ($40) does not close the gap either (Kimi K3 $60 buys ~10).
2. **The models that do fit are mostly not better — except one, and JR has cleared its condition.** Only three Go models cover the whole desk at Go's $10 tier: **GLM-5.3-Flash** (~$29/mo of a $60 limit), **Muse Spark 1.3 Contributor** (~$10/mo of $60) and **DeepSeek V4.1 Flash** (~$18–36/mo of $100). DeepSeek V4.1 Flash scores *worse* than Gemini 3.8 Flash on both creative benchmarks and GLM-5.3-Flash is **unscored**. **Muse Spark 1.3 scores above Gemini on both** (longform 82.8 vs 76.8, Elo 1906.9 vs 1744.4) at 17% of its limit; its discounted price is paid for by training on prompts and completions, **which JR accepted on 2026-11-12** (D2). It is therefore the leading candidate, not a caveat.
3. **Cash, not the allowance, is the real constraint.** Allowing Go's Extra Usage (list-priced, from Console credit) would let GLM-5.3 write the whole desk for ~**$260/mo** or Kimi K3 for ~**$500/mo**, against **~$125/mo** for Gemini 3.8 Flash today. That is a 2.1× or 4× bill for +5.0 or +2.8 longform points. Whether that is worth it is JR's call, not a capacity question.
4. **The "decision models" are not reachable from here.** `gemini-decision-1` and `jev-1.13-free` are **not in any catalogue this repo can read** — not the 30-model Go list, not the 41-model Zen list, not OpenCode's 40-entry Go registry in `models.dev`, and not served by the Go endpoint (they answer `400 Model is unavailable`, the same as any unknown id). What *is* real: the free Go models **Beam Free, Iceman Contributor Free and LongCat 2.5 Free** (unlimited allowance, callable from our own client, tool calls working), and a class of **TUI-only gated ids** that OpenCode lists but the API refuses (§7.4). In Mu the model does not decide anyway: the policy decides and the model speaks (§2.21b), so a "decision model" would be a voice/judging model, not a decision-maker.
5. **The switch is cheap technically, and one spike made it cheaper.** Go translates every text model through a **Google-compatible endpoint**, and all 12 models I probed returned a correct `functionCall` with arguments through it. The newsroom's Gemini AFC loop could point at Go with a base URL and a model id instead of the hand-written OpenAI port `docs/done/OPENCODE_GO_PIPELINE_PLAN.md` assumed.

**Recommendation:** run one judged side-by-side week with **Muse Spark 1.3 Contributor** as the candidate, since it is the only model that both fits the allowance and scores above Gemini 3.8 Flash, and JR has cleared the training condition. Keep **GLM-5.3-Flash** in the same test as the no-training fallback. If Muse wins, the desk moves to ~$10/mo inside its own allowance and the Gemini bill disappears; GLM-5.3 or Kimi K3 stay as a flagship-slice option only. §8 has the shape and the costs.

---

## 2. What is measured and what is extrapolated

| Figure | Source | Confidence |
| :--- | :--- | :--- |
| Go model ids, prices, capability flags | live `GET /inference/go/v1/models` and the Console Go page (2026-11-12) | measured |
| Go monthly limits, 5-hour/weekly shares, plan tiers | Console Go page, plan tables (2026-11-12) | measured |
| Training/retention per model | Console Go page, Privacy table (2026-11-12) | measured |
| Tool calling through Go's Google path | 12-model probe, this session (§7.3) | measured |
| EQ-Bench Creative Writing v3 and Longform scores | eqbench.com embedded leaderboards (2026-11-12) | measured |
| Mu's newsroom token volume | `docs/OPEN_MODEL_RESEARCH.md` §2, `llm_usage` 2026-10-22 – 10-27 | **6 days old, pre-V3** |
| Articles per month (~80) | 10 national + ~9 fantasy slots a week × 4.33 (`docs/EDITORIAL_MATRIX.md`, `newsroom/fantasy_desk.py`) | derived |
| Everything in §5's $ columns | the two rows above | **extrapolated** |

The live `madden_*` database is on the PC and is not reachable from this session, so no fresh `llm_usage` query was run. **Before any decision, re-measure** — the number to refresh is one query:

```sql
SELECT source,
       count(*) AS calls,
       sum(input_tokens - cached_tokens) AS uncached_in,
       sum(cached_tokens)                AS cached_in,
       sum(output_tokens + thinking_tokens)
           FILTER (WHERE source = 'newsroom') AS out_thinking
FROM llm_usage
WHERE created_at >= now() - interval '30 days'
GROUP BY source ORDER BY 1;
```

If the current month's newsroom tokens differ from the 10-22 baseline by more than ~20%, §5's verdicts move with them (the "does it fit" column scales linearly with volume).

---

## 3. How the Go allowance actually works

- Three plans: **Go $10/mo**, **Go Plus $40/mo**, **Go Teams $20/member/mo**. The plan changes the *dollar* limit, not the token prices.
- **Each model has its own monthly limit in dollars** ($15, $20, $30, $60, $100, $180, $240), and within it: **5-hour window = 20%**, **weekly = 50%**, **monthly = 100%**. So a model with a $15 limit gives $3 of usage per 5 hours.
- Usage is priced at that model's list rate and the response reports `opencode.allowance` per call — the pipeline's existing `tools/go_usage.py` watchdog reads this.
- **Extra Usage** (Console credit) can be enabled to continue past any limit at pay-as-you-go prices. Go Teams starts with it on. This is the difference between "fits the allowance" ($0 extra) and "is affordable" (metered cash).
- Free models are a separate axis: **unlimited** allowance, zero list cost. They report `allowance: 0`, `billing_source: "go"` and can be called from our own client.
- Go is a coding-agent service: traffic is monitored for abuse "that degrades the experience for other users". A 50-call, 150K-token article loop repeated by the hundreds each week is heavier than the usage pattern the page describes, and its 5-hour window is the mechanism that would throttle it.

---

## 4. Where the money goes today

From the 6-day `llm_usage` sample (all `gemini-3.8-flash`), monthly at current list prices:

| Source | Calls/mo | $/mo now | Note |
| :--- | ---: | ---: | :--- |
| `newsroom` (articles) | ~10,200 | **~$125** | 73% of the total; ~80 articles → **~$1.55/article** |
| `agent_wire` (posts) | ~5,100 | ~$26 | ~100 posts a week, 94% of output was thinking |
| `agent_memory` (takes) | ~2,500 | ~$14 | |
| `scouting_notes` / `scout_report` | ~500 | ~$2.5 | |
| `gm_*`, inbox, press, classify | <300 | ~$2 | short calls; the engines decide by policy, the model only speaks |

The newsroom is the whole question. Everything else together is ~$45/mo and would fit almost any Go tier — **except** the $15 models, where `agent_wire` alone (~$50/mo at GLM-5.3 prices) would blow the limit.

---

## 5. Does it fit? (newsroom only, ~$125/mo of Gemini tokens today)

$ = monthly list cost of the same token mix (73.0M uncached in, 456M cached in, 9.7M out+thinking); **fit %** = share of that model's Go monthly limit; **arts** = articles per month the limit buys at ~80 articles/mo; **burst** = one week's slate ($/4.33) against the 5-hour window (20% of the limit).

| Model | $/mo | Go limit | fit % | arts/mo | 1 week | 5h cap | burst | Plus limit |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- | ---: |
| **GLM-5.3-Flash** | 29.49 | 60 | 49% | 163 | 6.81 | 12.00 | OK | 180 |
| **Muse Spark 1.3 Contributor** | 10.15 | 60 | 17% | 473 | 2.34 | 12.00 | OK | 120 |
| **DeepSeek V4.1 Flash** (off-peak) | 18.15 | 100 | 18% | 441 | 4.19 | 20.00 | OK | 200 |
| **DeepSeek V4.1 Flash** (peak) | 36.30 | 100 | 36% | 220 | 8.38 | 20.00 | OK | 200 |
| MiMo-V2.6-Flash | 14.22 | 60 | 24% | 338 | 3.28 | 12.00 | OK | 120 |
| LongCat-2.0 | 36.30 | 60 | 60% | 132 | 8.38 | 12.00 | OK | 240 |
| Qwen3.8 Flash | 22.81 | 30 | 76% | 105 | 5.27 | 6.00 | OK (tight) | 90 |
| MiniMax M3.1 Flash | 23.62 | 20 | 118% | 68 | 5.46 | 4.00 | NO | 80 |
| GPT-6 Luna / Claude Haiku 5.5 | 16.72 | 15 | 111% | 72 | 3.86 | 3.00 | NO | 60 |
| MiMo-V2.6-Pro | 41.85 | 15 | 279% | 29 | 9.67 | 3.00 | NO | 60 |
| Hy4 | 104.33 | 60 | 174% | 46 | 24.09 | 12.00 | NO | 240 |
| **Gemini 4 Argon** (50% off) | 288.80 | 30 | 963% | 8 | 66.70 | 6.00 | NO | 120 |
| **GLM-5.3** | 263.51 | 15 | 1757% | 5 | 60.86 | 3.00 | NO | 120 |
| **Qwen3.8 Max** | 318.29 | 15 | 2122% | 4 | 73.51 | 3.00 | NO | 60 |
| **Kimi K3.1 Pro** | 334.40 | 15 | 2229% | 4 | 77.23 | 3.00 | NO | 60 |
| **Grok 4.7** | 432.29 | 15 | 2882% | 3 | 99.84 | 3.00 | NO | 60 |
| **Kimi K3** | 501.60 | 15 | 3344% | 2 | 115.84 | 3.00 | NO | 60 |
| *(Gemini 3.8 Flash, today)* | *125.40* | — | — | *80* | *28.96* | — | — | — |

Two conclusions the table already settles:

- **The weekly run is bursty, so the 5-hour window binds before the monthly one.** The desk writes a week's slate in one evening: that is ~23% of a month's tokens in a single 5-hour window, against a 20% cap. MiniMax M3.1 Flash, GPT-6 Luna, Claude Haiku 5.5 and MiMo-V2.6-Pro fail on the *monthly* number; Qwen3.8 Flash passes it by 24% and would fail a long Sunday.
- **Tier upgrades don't rescue the premium models.** Go Plus triples most limits, but GLM-5.3 needs 2.2× the Plus limit and Kimi K3 needs 8.4×.

---

## 6. Quality: what the benchmarks actually say

EQ-Bench is the right benchmark for this desk (LLM-judged prose quality, and it publishes a slop score, which matters because the newsroom already bans filler). Two tables matter: **Creative Writing v3** (short-form, Elo) and **Longform** (multi-chapter, 0–100; closer to a 1,500-word column).

| Model | CW v3 Elo | Longform | Slop (v3) | Fits Go? | Privacy |
| :--- | ---: | ---: | ---: | :--- | :--- |
| Grok 4.7 | 2012.2 | **83.0** | 8.47 | no ($15) | not trained, 30-day |
| **Muse Spark 1.3** | 1906.9 | **82.8** | 10.73 | **yes** | **trains on data** |
| GLM-5.3 | 2076.4 | 81.8 | **8.42** | no ($15) | not trained, 0-day |
| Qwen3.8 Max (=2.4T-A95B) | 1843.8 | — | 12.26 | no ($15) | not trained, 0-day |
| Gemini 4 Argon | 1757.8 | 80.9 | 22.70 | no ($30) | not trained, 0-day |
| Kimi K3.1 Pro | **2172.6** | 79.9 | 9.64 | no ($15) | not trained, 0-day |
| Kimi K3 | 2083.3 | 79.6 | 9.70 | no ($15) | not trained, 0-day |
| GLM-5.2 | 1759.2 | 77.9 | 13.11 | no ($60) | not trained, 0-day |
| **Gemini 3.8 Flash (today)** | **1744.4** | **76.8** | **22.60** | — | — |
| DeepSeek V4 Pro | 1552.6 | 75.6 | 19.66 | no ($15) | not trained, 0-day |
| GPT-5.6 Luna | 1831.5 | 75.5 | 11.80 | n/a (6 Luna unscored) | — |
| **DeepSeek V4.1 Flash** | **1539.9** | **74.0** | 22.42 | **yes** | not trained, 0-day* |
| GLM-5.3-Flash | — | — | — | **yes** | not trained, 0-day |
| MiniMax M3.1 Flash | — | — | — | no | not trained, 0-day |
| MiMo-V2.6-Flash / Qwen3.8 Flash / Hy4 / LongCat-2.0 | — | — | — | partly | not trained, 0-day |

\* DeepSeek's ZDR agreement runs to 2026-11-30 and is renewed monthly.

What this says, plainly:

- **Gemini 3.8 Flash is genuinely mid-table, and its slop score is bad.** 1744 Elo / 76.8 longform, slop 22.6 — the worst slop of any current model in the top 20. Kimi, GLM-5.3 and Muse all write with roughly 60% less slop. So JR's instinct that a better model exists is right.
- **But the models that are better are exactly the ones the allowance can't carry**, and the cheap ones are either unscored or worse. The only cheap model with evidence *above* Gemini is Muse Spark 1.3, and its discount is payment in training data. This is not a coincidence: Meta, Moonshot and Z.ai price their strong models where they price them because the tokens are worth that.
- **GLM-5.3-Flash is the one open question.** Its family is the strongest cheap line (GLM-5.2 77.9, GLM-5.3 81.8 longform) and it fits with 2× headroom, but it has **no EQ-Bench entry at all**. Its predecessor at the flash tier, GLM-4.7-Flash, scored **47.8** — a bad omen that only a real test settles.
- **DeepSeek V4.1 Flash would be a downgrade for prose.** It is the cheapest sensible model on Go and the right choice for structured/decision calls, not for a 192-voice newsroom.
- **Two things benchmarks don't measure here:** whether a model can hold a persona across a 50-call tool loop without inventing numbers (Mu's own bar), and whether it follows the article's structure directive. Both are exactly what a side-by-side week tests.

**Privacy, separately:** Muse Spark 1.3/1.2 Contributor and Iceman Free are the only Go models that train on prompts; Muse also needs "Allow paid endpoints that train on request data" enabled and is limited to Meta's permitted regions. **JR accepted the Muse training term on 2026-11-12 (D2), and the workspace already answers on `muse-spark-1.3-contributor` (HTTP 200, `x-opencode-session` set), so nothing blocks it.** LongCat 2.5 Free is China-hosted and needs Global regions. Everything else in the table is "not used, 0-day retention". DeepSeek V4.1 Flash's ZDR agreement expires 2026-11-30 and is renewed monthly.

---

## 7. The other half of the question

### 7.1 "Decision models" — what is reachable from here

JR reports seeing **Gemini Decision 1** and **jev-1.13-free** in OpenCode. I could not reach either, and the way they are unreachable matters:

| Probe (2026-11-12) | Result |
| :--- | :--- |
| Go model list `GET /inference/go/v1/models` | 30 ids, neither present |
| Zen model list `GET /inference/v1/models` | 41 ids, neither present |
| OpenCode's own registry (`models.dev`, 235 providers) | 40 Go ids, neither present; the only "decision engine" text in the registry is Model Oracle AI's *router* |
| `POST /chat/completions` with `model: "gemini-decision-1"` / `"jev-1.13-free"` | `400 Model is unavailable` |

That 400 is worth reading carefully: it is the **generic unknown/unserved-id error**, not a deprecation. `minimax-m2.7` answers a precise `410 ModelDeprecated … Use minimax-m3.1-flash` through the same endpoint, so Go distinguishes "retired" from "not served here". Both of JR's ids fall on the "not served to this key" side.

- **The likely class is the TUI-only gated ids.** OpenCode's registry lists 40 Go models but the API serves 30; the extras — `gemini-4-argon-contributor`, `space-bunny`, `muse-spark-1.3-contributor-free`, `step-5-preview-free`, `minimax-m3.1-flash-free`, `hy3-free`, `cat-jam-free`, `crouton-alpha-free`, `ll-alpha-free` — all answer `400 Model is unavailable` to our `oc_sk_…` key, while their non-free siblings answer 200. If JR sees his two models in the OpenCode model picker, they are almost certainly in this class: **visible in the TUI, not callable from a pipeline client**. That is decisive for the newsroom — Mu could not use them even if they were the best models in the world.
- **If they are callable another way, the API id is what settles it.** What I need from JR is where he sees them (TUI picker, Console page, or a specific workspace setting) and whether a chat in the TUI actually answers on them. If one does, the id and a screenshot are enough for me to re-probe; until then this doc cannot price or benchmark them.
- The real "decision model" family is **Liquid LFM3's d1-2B** (~2B, returns calibrated answers rather than text), which `docs/OPEN_MODEL_RESEARCH.md` §3.1 already flagged for beside-Madden work. It is a local model, not a Go one.
- **Free Go models are real and callable.** I called `beam-free`, `iceman-contributor-free` and `longcat-2.5-free` from our own client through the Go endpoint: HTTP 200, `billing_source: "go"`, `allowance: 0`, `list_cost: 0`. This corrects the pipeline plan's S7 note (that was the *Zen* free tier, not Go). All three returned a correct tool call in §7.3's probe.
- **But "free" is not "better".** None of the three has a creative-writing score; LongCat's family scored 35–47 longform, Iceman trains on data, LongCat 2.5 is China-hosted, and all three are "limited time". They are candidates for *cheap* work (classification, a first-pass summary), not for a persona's voice.
- **In Mu, the model is not the decider.** `docs/OPEN_MODEL_RESEARCH.md` and §2.21b are explicit: the policy picks the move and the model only says it, validated against a fact sheet. So "decision quality" here means *judging* (inbox appraisal, `meaningful_interaction` detection, media voters) and *classification* (`custom_commission`, `draft_interview_classify`), and those calls are ~$2/mo in total. Optimising them is not a budget question; it is a quality question, and none of the free models has evidence for it.

### 7.2 The engines cannot use Go yet

`engines/llm_provider.resolve_provider` accepts only `gemini` and `ollama`; `gm_llm.make_llm` builds `GeminiText` for everything else. So the short calls (agent_wire, agent_memory, gm_*, inbox, press) have **no Go path today** — only the newsroom's OpenAI-compatible tool loop knows `opencode-go`, and the newsroom's own writer does not use it.

### 7.3 The newsroom switch is cheaper than the plan assumed

`newsroom/swarm_dispatcher.py:1568` builds a `google-genai` client and runs Gemini's automatic function calling over up to 50 tools. `docs/OPEN_MODEL_RESEARCH.md` §4 and `OPENCODE_GO_PIPELINE_PLAN.md` both assumed that meant porting to an OpenAI loop. Two spikes this session say otherwise:

- **Go translates every text model through a Google-compatible endpoint.** `POST /inference/go/google/v1beta/models/<model>:generateContent` answered for `glm-5.3-flash` and `gemini-4-argon` on the same `oc_sk_…` key, with `usageMetadata.opencode.allowance` present, and the Google-style `x-goog-api-key` and `Bearer` headers both work. A session header (`x-opencode-session`) is now **required** — without it Go answers `400 MissingSessionID`.
- **Tool schemas survive the translation.** All 12 models probed (`glm-5.3-flash`, `glm-5.3`, `deepseek-v4.1-flash`, `qwen3.8-flash`, `minimax-m3.1-flash`, `mimo-v2.6-flash`, `muse-spark-1.3-contributor`, `gemini-4-argon`, `gpt-6-luna`, `claude-haiku-5-5`, `beam-free`, `longcat-2.5-free`) returned `functionCall {name: "lookup_player", args: {name: "Marcus Vance", season: 2027}}` — arguments intact, required fields respected.
- **Caveat:** through the SDK's own AFC loop, Muse Spark and Gemini 4 Argon executed the tool and answered correctly, while `glm-5.3-flash` came back claiming the tool "defines no city argument". The raw API call is fine, so this is an SDK/translation detail — but the newsroom's real tool set (complex schemas, 50 calls) needs its own test before anyone trusts it.

Consequence: a newsroom switch is **`base_url` + model id + a session header** in one client construction, not a rewrite of the loop. That makes a side-by-side week cheap enough to actually run.

---

## 8. Recommendation

Muse Spark 1.3 Contributor is now the target: it is the only Go model that both fits the desk's allowance and scores above Gemini 3.8 Flash, and D2 (the training term) is settled. Do this in order:

1. **Re-measure** (one SQL query, §2). Everything below is priced off a 6-day October sample.
2. **One judged side-by-side week, no production change** — the existing `tools/social_model_compare.py` / `tools/llm_compare.py` pattern: take a finished week's slate, re-run the same prompts and the same tool set through `gemini-3.8-flash`, `muse-spark-1.3-contributor` and `glm-5.3-flash` (via Go's Google endpoint), and judge on Mu's own bar — voice fidelity to the 192 personas, no invented numbers against `fact_sheet`, structure-directive compliance, tool-loop completion, slop. **Approved by JR 2026-11-12 and building** as [`NEWSROOM_MODEL_SIDE_BY_SIDE_PLAN.md`](../in_progress/NEWSROOM_MODEL_SIDE_BY_SIDE_PLAN.md), the article-level track of CLOSE_OUT_CHECKLIST P8-05. Billing: the Go arms draw on their own monthly allowances, the Gemini control on JR's existing API credit, so the pass costs no cash.
3. **If Muse wins, switch the desk to it** (§8.1). That is a ~**$115/mo saving** against today's Gemini bill, inside a $60 allowance the desk would use at ~17%.
4. **Keep a flagship slice as the stretch option, not the plan.** With **Extra Usage on**, send the Hero slot and 2–3 marquee slots (≈16–24 articles/mo) to **Kimi K3.1 Pro** (~$79–120/mo) or **GLM-5.3** (~$53–79/mo) and keep the bulk on Muse. Only worth it if the side-by-side shows Muse is the limiting factor on the big pieces.
5. **For the short calls**, the cheapest real change is the same base-URL trick in `engines/llm_provider.py`: point `GeminiText` at Go with `deepseek-v4.1-flash` (or keep Gemini). It would cut ~$45/mo to ~$10 and needs thinking caps kept on. Do it only after step 2, because those calls are persona voices too.

**Do not** move the desk to DeepSeek V4.1 Flash to "use the subscription": it is ~2.8 longform points and ~205 Elo *below* the model it replaces, on the one benchmark that measures this job.

**Do not** plan around the free models for prose. They are unlimited and reachable, but unscored, one trains on data, one is China-hosted, and all three are temporary.

### 8.1 First measured result — 2036 Week 18, six slots, three models (2026-11-12)

Run with `tools/article_model_compare.py` on the `madden_2036` archive, read-only, no `news_feed` or `llm_usage` writes. Six slots: `Hero Slot`, `Slot 1`, `Slot 4` (it resolved to `AWARDS_TRACKER`), `Slot 7`, `Standalone` (`POWER_RANKINGS`) and `Fantasy 1`. Same compiled prompts and the same ~40 tools for every model; only the client and the model id changed. Artifacts: `verify_output/model_compare/20261112_053537/`.

| Model | Articles | Words | Tool calls | Wall time | List cost | Cost/article |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| `gemini-3.8-flash` | 6 | 14,978 | 121 | 911.7 s | **$2.560** | $0.427 |
| `muse-spark-1.3-contributor` | 6 | 13,126 | 202 | 1,054.1 s | **$0.177** | $0.030 |
| `glm-5.3-flash` | 6 | 11,999 | 129 | 583.3 s | **$0.317** | $0.053 |

What the run already settles:

1. **The Go path works on the real desk.** No failures, no validation rewrites fired, no tool-schema errors, and no writes to the archive — the AFC loop drove the same tool set for all three models. The `missing GEMINI_API_KEY` branch was never hit, so the injected client really did replace the Gemini one.
2. **The cost gap is 8–14×.** Muse Spark wrote the same six slots for $0.18 against Gemini's $2.56, and GLM-5.3-Flash for $0.32. At the desk's ~80 articles a month, that is ~$2.40 (Muse) or ~$4.20 (GLM-5.3-Flash) against ~$34 for Gemini — the whole "$125/mo newsroom" in the June numbers prices out far lower here because this archive's prompts cache heavily.
3. **Muse researches hardest, GLM-5.3-Flash researches least.** 202 tool calls (Muse) vs 129 (Gemini) vs 129 (GLM-5.3-Flash); GLM-5.3-Flash's `Hero Slot` used **5** calls and produced 1,127 words where the others used 20–25 and wrote 1,423–1,504. Whether that is efficiency or thin grounding is exactly what the blind read decides — the grounding sheet flags numbers absent from the model's own trace, but it over-reports because tool results are clipped, so it is a screening aid, not a verdict.
4. **Wall time:** GLM-5.3-Flash is the fastest (583 s for six), Gemini 912 s, Muse 1,054 s. Nothing near the 50-call ceiling.
5. **The judge did not produce verdicts.** Two attempts at the pairwise GLM-5.3 judge returned no parsable answer: on prompts this size its thinking consumed the output cap, so the text came back empty. The tool now reads the non-thought parts, parses nested JSON, raises the cap to 8,192, clips the inputs, and writes each pair to `judge.jsonl` so a re-run resumes. The judging leg needs one more run after the Go 5-hour window resets (13:04Z on 2026-11-12); the failed attempts, not the articles, are what took the window to 89% — the 18 article runs together cost ~$0.5 of Go allowance.
6. **The archive has no agent-memory block** (`nfl_mirror_picks.pick_type` predates the column), so all three models saw the same prompt without it. Fair between them, not identical to production.

**Still to do:** JR's blinded read (`blind_read.md`, answer key at the end), and the judge re-run. Only then does this become P8-05 evidence.

### 8.2 What the Muse switch would look like (not built)

- `newsroom/swarm_dispatcher.py`'s `genai.Client(api_key=…)` gains `base_url="https://opencode.ai/inference/go/google"` and a per-process `x-opencode-session` header; the model string becomes `muse-spark-1.3-contributor`.
- The AFC tool loop stays as it is, except that its usage meter must read `usageMetadata.opencode.allowance` (the Go field) rather than assuming Gemini's pricing — `db/llm_usage` stores tokens only, so allowance goes to the existing `data/usage/go_allowance.jsonl` ledger that `newsroom/tool_loop/go_allowance.py` already writes.
- Local `llm_usage.model` should keep the Go id so `tools/go_usage.py` and later audits can tell which model wrote which article.
- The 50-call ceiling, the persona prompts and the review pass are unchanged. The one real risk is tool-loop reliability over the newsroom's ~40 real tools, which step 2 measures.
- Muse Spark's vocabulary is narrower than Gemini's (EQ-Bench vocab complexity 12.42 vs 30.97) — it writes in plainer diction. That is a style decision for JR to hear in the side-by-side, not a defect to fix in code.

---

## 9. Decisions for JR

| # | Question | Status |
| :--- | :--- | :--- |
| D1 | Is a one-week judged side-by-side worth ~$15 and a day of work? | **open** — recommended |
| D2 | May newsroom prompts and article text go to a model that trains on them (Muse Spark 1.3)? | **answered yes, 2026-11-12** — Muse is the leading candidate; GLM-5.3-Flash stays as the no-training fallback in the same test |
| D3 | Is a 2.1× newsroom bill (~$260/mo) acceptable for GLM-5.3 quality, or a 4× bill (~$500/mo) for Kimi K3? | **open** — only matters as the flagship slice now that Muse fits |
| D4 | Should the short calls (agent_wire, memory, gm_*) move to Go independently of the desk? | **open** |
| D5 | Is "unlimited but temporary and unscored" acceptable for any production path? | **open** |
| D6 | Where does JR see `gemini-decision-1` and `jev-1.13-free` (TUI picker, Console, a workspace setting)? A screenshot or the exact id would let me re-probe; both currently answer `400 Model is unavailable` to the pipeline key. | **open — needs JR** |

---

## 10. Sources

- Console Go plan, limits, 5-hour/weekly shares, privacy table: `https://opencode.ai/docs/go` (read 2026-11-12)
- Live model catalogue and prices: `GET https://opencode.ai/inference/go/v1/models` (30 ids) and `GET https://opencode.ai/inference/v1/models` (41 Zen ids) with the repo's `OPENCODE_GO_API_KEY`
- OpenCode's own model registry: `https://models.dev/api.json` (235 providers, 40 ids for `opencode-go`), read 2026-11-12
- Availability probes: `POST /inference/go/openai/v1/chat/completions` with `gemini-decision-1`, `jev-1.13-free`, `gemini-4-argon-contributor`, `muse-spark-1.3-contributor-free` (all `400 Model is unavailable`) against `muse-spark-1.3-contributor` (200) and `minimax-m2.7` (`410 ModelDeprecated`)
- Live allowance: `GET https://opencode.ai/inference/go/v1/usage` (5h 5%, weekly 2%, month 1% at the time of writing)
- EQ-Bench Creative Writing v3 leaderboard: `https://eqbench.com/creative_writing_chartdata.js` / `creative_writing.js` (embedded CSV, 146 models)
- EQ-Bench Longform leaderboard: `https://eqbench.com/creative_writing_longform.js` (embedded CSV, 143 models)
- Mu cost baseline: `docs/OPEN_MODEL_RESEARCH.md` §2; provider seams: `newsroom/tool_loop/openai_loop.py`, `engines/llm_provider.py`; spend watchdog: `tools/go_usage.py`
- Prize for the switch: `docs/done/OPENCODE_GO_PIPELINE_PLAN.md` (Go provider, allowance ledger, watchdog), `docs/done/PC_MIGRATION_PLAN.md` §7
