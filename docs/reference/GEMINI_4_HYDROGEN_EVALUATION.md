# Gemini 4 Hydrogen: which tiers it can take in Project Mu, and whether it can audit

**Date:** 2026-11-12 · **Asked by:** JR (via `closeout-status`, board M-0682) · **Session:** `gemini-hydrogen-eval` (`deepseek-v4.1-flash`)
**Status:** research only. Nothing built, no default flipped, no audit verdict, no database or save touched, one reachability probe spent.
**Question:** Gemini 4 Hydrogen launched today and JR has quota. Which tiers of implementation could it take here, and is it good enough to be the auditor?

**Labels, used on every claim below:**
`[M]` measured here today (a command, an API answer, a document in this repo) · `[P]` published by Google, Vals AI, BenchLM or OpenCode (read 2026-11-12) · `[U]` unverified — no evidence, or evidence I could not get from this box.
**Inferred** = arithmetic on one `[M]` and one `[P]` figure, with both inputs shown. It is not a billed number.

---

## 0. Bottom line

1. **It is served here on two routes and not on a third, and the third is the one JR's subscription pays for.** `[M]` Gemini 4 Hydrogen answers on **JR's own Gemini API key** (`models.list` lists `gemini-4-hydrogen`, 1,048,576 in / 65,536 out) and on **Antigravity's CLI** (`agy models` lists `gemini-4-hydrogen-low|medium|high|xhigh`, and a one-line probe on `medium` answered). `[M]` It is **absent from OpenCode Go's 30-model catalogue**, which carries only `gemini-4-argon` for Gemini. So the Go allowance that runs every builder session cannot buy it. `[M]` It *is* in **OpenCode Zen's** catalogue at list price.
2. **The one tier where it is genuinely interesting is audit, and the reason is quota, not quality.** `[P]` Its independent agentic score is mid-pack (Terminal-Bench 4.0 **54.04%**, #11/64 at Vals; 4.1 **57.0%** vendor, against Claude Sonnet 5.5's 63.9%), but repricing audit-5's measured footprint (137.2M input / 134.0M cached / 354K output `[M]`) at Hydrogen's list rates gives **~$17.50 a full audit session, ~$1.25 a batch** (inferred) — the cheapest paid auditor candidate of the shortlist, and the only one whose marginal cost on JR's existing Antigravity plan is **$0** `[M]` (probe answered on that plan).
3. **It satisfies the different-model rule cleanly.** `[M]` The rule (`AUDITOR_MODEL_EVALUATION.md` D5, `audit/SKILL.md:10`) is that the auditor must not be the builders' family: the builders are DeepSeek, Hydrogen is Google, and Argon (same Gemini 4 family) is already a sanctioned auditor. It never audits its own work because it would write nothing.
4. **But the two things that stopped audit-5 are not fixed by it, and one of them is worse.** `[M]` audit-5 died at **5-hour 99% / weekly 72%** with no warning; an Antigravity auditor has **no local quota probe** (`agy` has no `usage` subcommand, `tools/usage_check.py` reads Claude's file, `tools/go_usage.py` reads Go's) — the same "runs until the API refuses" failure, exactly as `AUDITOR_MODEL_EVALUATION.md` §2.4 found for GLM. And there is **no EQ-Bench entry** for it `[M]`, so nothing published speaks to prose or to Mu's citation discipline.
5. **For every writer tier it is the wrong model twice over.** It would cost **~$216/mo** at Mu's measured newsroom mix (inferred) against **~$29/mo** for the `glm-5.3-flash` that writes the desk today `[M]`, with no published voice score at all `[M]`. It is a Carbon-derived "everyday coding and agentic workflows" model `[P]`, not a voice model.
6. **Direct answer on audit fitness:** on today's evidence it is **not auditor of record**, and it is a credible **second auditor** — after one blind re-audit (**run the same day: the known defect found first time, 24/24 citations resolved**, §3.5) and a quota watcher, which does not exist yet. That is a measurement recommendation, not a verdict — a DeepSeek-routed session never audits, and nothing below authorizes one. **JR decides.**

---

## 1. The model, as measured and as published

### 1.1 Facts

| Fact | Value | Label |
|---|---|---|
| Release | 2026-11-12 (same day as this evaluation) | `[M]` Zen catalogue `release_date`; `[P]` model card published 12 November 2026 |
| Lineage | **Adapted from Gemini 4 Carbon** — Google's own description of it as the smaller, faster sibling | `[P]` model card, "Model Information" |
| Positioning | "speed and efficiency for everyday coding and agentic workflows", "fast, disciplined execution on its own or **as sub-agents** orchestrated alongside frontier models", "ready-to-go writing, research, and everyday work" | `[P]` model card, "Benefit and Intended Usage" |
| Context / output | **1M unified context**; **64K default synchronous output**, up to **1M via background long decode** (`models.list` reports `outputTokenLimit` 65,536) | `[P]` model card; `[M]` API `models.list` |
| Thinking levels | four: low, medium, **high (default)**, and a new **xhigh** | `[P]` Antigravity launch post; `[M]` `agy models` exposes all four |
| Modalities in | text, images, audio, video, PDF (the catalogue lists 28 MIME types incl. `image/heic`, `video/mp4`, `audio/wav`, `application/pdf`); **text out only** | `[M]` Zen model object; `[P]` model card |
| Knowledge cutoff | March 2026 | `[P]` model card |
| Training on *your* data | **Paid Gemini API: no. Free Gemini API tier: yes** ("Content used to improve our products" = Yes). Zen: no (zero-retention, US/EU). Antigravity: not established here | `[P]` Gemini API pricing page, "Used to improve our products" row; `[P]` OpenCode Zen privacy table; `[U]` Antigravity |
| Price, Gemini API / Zen | **$1.50 in / $0.075 cached / $7.50 out** per 1M; Batch and Flex halve it ($0.75 / $0.0375 / $3.75); Priority is $2.70 / $0.135 / $13.50 | `[P]` Gemini API pricing; `[M]` Zen catalogue `cost` |
| Free tier | input, output and cache **free of charge**, Search grounding unavailable, content used to improve products; per-model RPM/TPM/RPD for Hydrogen are not on the public rate-limit page | `[P]` pricing page; `[U]` free-tier quotas |
| Paid-tier notes | implicit caching only (no `createCachedContent` in `supportedGenerationMethods`) | `[M]` `models.list` |
| Antigravity | "rolling out across **paid Google AI Pro and Ultra** subscribers"; **Orchestrator** (Carbon planning + Hydrogen executing) is **Ultra-only**, and Google reports it reaches near-Carbon task success on TB 4.1 for **24% less quota and 19% faster** | `[P]` Antigravity launch post, 2026-11-12 |

### 1.2 Published benchmarks, and what they do and do not say

| Benchmark | Hydrogen | Nearest comparison | Who ran it | Label |
|---|---|---|---|---|
| Terminal-Bench 4.0 | **54.04%** (#11 of 64) | Opus 5.5 64.8 `[P]`(CodingFleet board), Argon 57.58, GPT-6.1 Sol 55.05 | Vals AI, independent | `[P]` |
| Terminal-Bench 4.1 | **57.0%** | Sonnet 5.5 **63.9%**, GPT-6.1 Terra — | Google (cheating-adjusted, own harness) | `[P]` |
| Vals Index v2.2 | **68.49% ±0.99**, #6 of 37 | Claude Fable 5.2 71.31% leads; $9.84 per test; **32 min 5 s** latency | Vals AI | `[P]` |
| Vibe Code Bench v1.1 | 87.78% (#13 of 128) | — | Vals AI | `[P]` |
| SWE-sweep | 13.5% | Sonnet 5.5 3.1%, GPT-6.1 Terra 5.8% | Google (Antigravity CLI 1.4.8) | `[P]` |
| Agents' Last Exam v1.1 | **71.6%** | Sonnet 5.5 60.4%, Terra 69.8% — *five-hour agentic window, Antigravity CLI* | Google | `[P]` |
| OSWorld-2.1 (computer use) | 77.6% | Sonnet 5.5 83.9% | Google | `[P]` |
| HLE-Diamond | 58.8% | Sonnet 5.5 53.3% | Google | `[P]` |
| AA Intelligence Index | **51** (Argon 50; **54 at its highest setting**) | — | Artificial Analysis, cited by Google | `[P]` |
| FrontierCode 2.0 | 38.2% | — | Cognition | `[P]` |
| **EQ-Bench Creative Writing v3** | **no entry** (0 occurrences of "hydrogen" in the leaderboard's embedded data; `gemini-4-argon` is present) | — | — | `[M]` |
| **EQ-Bench Longform** | **no entry** (same check, 1.2 MB of chart data) | — | — | `[M]` |
| Mu audit record | **one measured trial** (§3.5: known defect found first time, 24/24 citations, 8 min) — **no audit verdict**, and no report in `data/audit_reports/` | — | `[M]` |

Two readings that matter. **Its agentic evidence is real but second-tier:** it beats Sonnet 5.5 on knowledge-work and computer-use-style tasks, loses to it on both terminal benchmarks, and sits between Sol and Argon on the independent TB 4.0. `[P]` **Its prose is simply unmeasured:** the model card sells "ready-to-go writing", but neither EQ-Bench table has a row, and the only writer-ish evidence in the repo is that Gemini-family models score mid-table on those benches (`gemini-4-argon` 1757.8 Elo / 80.9 longform; `gemini-3.8-flash` 1744.4 / 76.8) `[M]`.

### 1.3 Which route actually serves it, today

| Route | Serves Hydrogen? | What it costs | Label |
|---|---|---|---|
| **Gemini API, JR's existing key** | **Yes** — `gemini-4-hydrogen` is in the key's own `models.list`, with `generateContent`, `countTokens`, `batchGenerateContent` | Cash, metered: $1.50 / $0.075 / $7.50 per 1M. No monthly allowance. | `[M]` list; `[P]` price |
| **Antigravity (`agy` 1.5.5)** | **Yes** — listed at four efforts; the probe answered | Marginal $0 on JR's Google AI Pro/Ultra plan; plan limits and which tier he holds are not exposed by `agy` | `[M]` `agy models` + probe; `[U]` limits/plan |
| **OpenCode Go** ($100/mo DeepSeek allowance, per-model $15–$180 limits) | **No** — 30 ids, only `gemini-4-argon` for Gemini | — | `[M]` `GET /inference/go/v1/models` |
| **OpenCode Zen** (pay-as-you-go Console credit) | **Yes** — catalogued `gemini-4-hydrogen`, released 2026-11-12, 1M/65,536 | Cash at list price ($1.50 / $0.075 / $7.50) | `[M]` catalogue; `[U]` whether this account holds Zen credit (the stored credential is one OpenCode Console personal key, and it lists Zen) |
| Go's Google-compatible passthrough | **Untested** — Go translates Gemini-family models through `/inference/go/google/...`, so the passthrough *might* answer even though the catalogue omits it | One small call would settle it | `[U]` |

**Go allowance at the time of writing:** `[M]` 5-hour 7% (resets 23:50Z), weekly 44%, month 22% — irrelevant to Hydrogen unless the passthrough works, because the catalogue is what Go bills.

**The one probe spent**, so the record is complete `[M]`:

```
agy --model gemini-4-hydrogen-medium --mode plan -p "Answer in one line, no tools, no file reads: ..."
→ Model family: Gemini (Gemini 4 Hydrogen); yes, I can follow a strict "report only, never edit" instruction.
```

No API generation call was made; the `models.list` calls and the two catalogue reads are free.

---

## 2. The tier table

Cost figures marked *inferred* are arithmetic on the inputs in §2.1 — nothing was billed.

| Tier | What taking it would mean | Evidence for it | Cost | What must be measured first |
|---|---|---|---|---|
| **Audit** (auditor session) | A Hydrogen session running `/audit` on the Argon route (`agy --model gemini-4-hydrogen-high`) — report-only, pinned worktrees, `file:line` briefs | `[P]` TB 4.0 54.04%, TB 4.1 57.0%, ALE 71.6%; `[M]` serves on `agy`; `[M]` different family from the DeepSeek builders; `[M]` audit-5's load reprices to **~$17.50/session, ~$1.25/batch** (inferred); `[M]` **no quota probe exists**; `[M]` no Mu audit record | Antigravity plan: **$0 marginal** `[M]`, limits `[U]`. Gemini API: **~$17.50 a 14-batch session** (inferred; $8.75 via Batch, which is asynchronous and wrong-shaped for review). Zen: same list price, cash. Go: **unavailable** `[M]` | The blind re-audit in §3.4; a quota readout; whether `agy` can hold a 6–18 h session and dispatch batch subagents the way the skill's §4 assumes |
| **Article writer** | `NEWSROOM_MODEL=gemini-4-hydrogen` on the existing Gemini client (`newsroom/model_default.py` accepts any Gemini id) | `[M]` **no EQ-Bench row**; `[P]` Carbon-derived, "everyday work"; `[M]` the desk already runs `glm-5.3-flash` at ~$29/mo list for the same mix | **~$216/mo** at Mu's measured newsroom mix, **~$2.71/article** (inferred) — 7.4× today's article bill, 1.7× the retired Gemini 3.8 Flash bill | One judged side-by-side week through `tools/article_model_compare.py` on the `madden_2036` Week 18 archive: voice (JR's blind read), **factual contradictions** via `tools/article_factcheck.py`, tool-loop completion, cost |
| **Decision-call sites** (press casting/answers, inbox replies, Agent Wire, memory takes/summaries) | `PRESS_MODEL`, `INBOX_MODEL`, `SOCIAL_MODEL`, `AGENT_MEMORY_MODEL` — all four are one env var and the plumbing exists for any Gemini id | `[M]` none of the four sites has been read on Hydrogen; `[M]` the closest evidence is GLM-5.3-Flash's **failing** Agent Wire read (35 pts vs Gemini's 46) and a 5–4 inbox wash; `[P]` Hydrogen is 2× 3.8 Flash's input/output price at the cache rate it shares | Cash: roughly **2× today's ~$45/mo** on these sites (inferred, and less where the mix is cache-heavy — the cache read price is identical). Or $0 marginal on the Antigravity plan, which is not how the pipeline calls a model | All of GLM's open reads, on Hydrogen: inbox p95 against the 4.0 s budget **with Madden open**, Agent Wire blind read, one dry presser, a memory block, `tools/llm_compare` GM tasks |
| **Coding / builder sessions** | Either a JR-side coding session (`agy --model gemini-4-hydrogen-high`, or Antigravity's **Orchestrator** which needs Ultra) or a Project Mu builder lane | `[P]` TB 4.0 54.04% is **below Argon 57.58 and Opus 64.8**; Vibe Code 87.78%; `[M]` builders are DeepSeek by rule (P0-4), so a Hydrogen builder is a rule change, not a routing choice; `[M]` `spawn_agent.sh`, `/builder`, the audit skill and the board inbox know nothing about it | Antigravity plan: $0 marginal `[M]`; Zen: cash at list; Go: unavailable | Whether it beats the sanctioned coder on Mu's own units — and a rule decision, because the builder lane's model *is* the independence guarantee for audit |
| **Pilot screen-reader / vision** (can it replace the Mac vision host?) | Not as the live layer. Possibly as the **judge**: offline screen labelling, `vision_bench` scoring, or `MADDEN_EXTRACT_MODEL=gemini-4-hydrogen` for `extract.py`'s table reads | `[M]` the Mac host is **measured but not adopted**: PC local is 2.26 s warm with Madden running vs the Mac's 6.49 s, and its 202-frame accuracy is 0.49 screen-kind / **0.17 highlight**, which is why pixel checks and the catalog own the verdict; `[M]` `tools/madden_pilot/vision.py` supports only `ollama:` and `deepseek:` backends, so a `gemini:` backend is a code change; `[P]` Hydrogen is multimodal with a 1M window | Per-frame API cost is trivial but **unmeasured**; no plan route from the pilot today | A `vision_bench` A/B on the **same 202 labelled frames** (needs the new backend), latency included, and a check that it never authorizes a key — the pilot's rule is that a model describes and the catalog decides |
| **Research** | Board research passes like this one: read the repo, read the web, measure, write a report | `[P]` Vals Index 68.49% (#6/37), ALE 71.6%, HLE-Diamond 58.8%, legal/finance indexes top-10, 1M context, and Google's own "as sub-agents" positioning; `[M]` it followed a strict report-only instruction in the one probe | API cash; plan; **free tier $0** but content feeds product improvement `[P]` — the free tier is the right trial route only for material JR is willing to send | A known-answer run (one of §3.4's batches is exactly that), plus a citation check: every path and line it names must resolve |

### 2.1 The two cost calculations, inputs shown

**Audit.** audit-5's measured footprint `[M]` (`AUDITOR_MODEL_EVALUATION.md` §4.1: 137.2M in, of which 134.0M cached; 354K out; 14 batches; 18.5 h) repriced at published rates `[P]`:

| Model | $/batch (9.8M in / 9.57M cached / 25.3K out) | $/session | $ at 4 sessions/mo |
|---|---:|---:|---:|
| **Gemini 4 Hydrogen** | **1.25** | **17.50** | 70.02 |
| Gemini 4 Hydrogen, Batch API | 0.63 | 8.75 | 35.01 |
| Gemini 3.8 Flash (never an auditor, for scale) | 0.99 | 13.78 | 55.11 |
| GPT-6.1 Sol | 1.67 | 23.34 | 93.36 |
| Gemini 4 Argon | 1.67 | 23.34 | 93.36 |
| GLM-5.3 | 2.92 | 40.88 | 163.51 |
| Claude Opus 5.5 | 3.34 | 46.68 | 186.72 |
| DeepSeek V4.1 Flash (excluded: builder family) | 0.08 | 1.09 | 4.38 |

These are **list-cost inferences**, and `AUDITOR_MODEL_EVALUATION.md` §4.4's warning stands: a different harness caches differently, and Vals' $9.84 per test with a 32-minute latency hints at a token-hungry style. Only a measured batch settles it.

**Newsroom.** Mu's June token mix `[M]` (73.0M uncached in, 456M cached in, 9.7M out+thinking per month, ~80 articles) — the same inputs that reproduce `MODEL_ROUTING_EVALUATION.md` §5's table exactly:

| Writer | $/mo | $/article |
|---|---:|---:|
| `glm-5.3-flash` (today's desk) | 29.48 | 0.37 |
| `gemini-3.8-flash` (the model it replaced) | 125.33 | 1.57 |
| **`gemini-4-hydrogen`** | **216.45** | **2.71** |
| `gemini-4-argon` | 288.60 | 3.61 |

---

## 3. Audit fitness, directly

### 3.1 The independence rule

`[M]` The rule is that the auditor comes from a different model family from the builders: the builders run DeepSeek (`CLAUDE.md`, "Skills by model class"; `audit/SKILL.md:10`, which refuses a DeepSeek-routed session), and a session never audits its own work. Hydrogen is Google, so a Hydrogen auditor **passes** the rule on the same basis Argon does — and `AUDITOR_MODEL_EVALUATION.md` D5 already settled that "different family" is about the model, while "never audit your own work" is about the session. Hydrogen writing nothing in the audited range makes the second half trivially true.

What does **not** follow from that: the audit skill's allowed list is Opus 5.5, Codex and Argon `[M]`, so a Hydrogen auditor is a **rule change plus a route**, not a config line. It needs, at minimum, a launcher (`spawn_agent.sh` case + session script), an inbox alias, a skill, four lines in `audit/SKILL.md`, and a `Model:` trailer — the same ~100-line smallest route `AUDITOR_MODEL_EVALUATION.md` §2.3 sized for GLM, plus tests.

### 3.2 The context a batch needs, against its window

`[M]` A batch's **cumulative** input is 9.8M tokens (9.57M cached across many calls) — not one context. The largest single contexts an auditor builds are a 2,400-line production file, the ~200-table schema, a plan and test output: tens to low hundreds of thousands of tokens.

`[M]` Hydrogen's window is **1M** with **64K default output** (up to 1M via long decode). That equals Argon's 65,536 output and equals Opus's 1M context, at half Opus's 128K output. **The window is not the constraint.** The fix-brief report standard is far under 64K.

### 3.3 Quota against the measured audit load

`[M]` The anchor: one heavy audit day = **137.2M tokens over 14 batches in 18.5 h**, which took **all of one Codex 5-hour window (99%) and 72% of a week**. Every candidate must carry that shape, not an average.

| Route | Carries a 14-batch day? | Warning before the wall? | Cash |
|---|---|---|---|
| **Antigravity (Pro/Ultra)** | `[U]` — "more rate limits" and a "flexible AI credit pool" `[P]`, no per-token figure published, and one 5-hour block of Hydrogen auditing is ~137M tokens in a 5-hour shape | **No.** `agy` has no `usage` subcommand `[M]`; nothing writes an Antigravity usage file | **$0 marginal** `[M]` |
| **Gemini API, paid** | `[P]` Tier 1 batch enqueue is 3M tokens and Standard rate limits are per-minute/per-day, not a 5-hour plan; `[U]` whether interactive TPM/RPD throttles a batch-heavy day | **Partly.** Spend is visible in Cloud billing; there is no repo watcher | **~$17.50/session**, no allowance |
| **Gemini API, free** | `[U]` — free-tier quotas for Hydrogen are not on the public rate-limit page | No | $0, **and content is used to improve Google's products** `[P]` |
| **Zen** | `[U]` — pay-as-you-go credits, no plan shape | Budgets exist on Console `[P]` | List price, cash |
| **Go** | **No** `[M]` | — | — |

That is the honest answer to "is its quota good enough": **on the one no-cash route there is no instrument to know**, which is the exact failure that parked audit-5 mid-queue. A Hydrogen auditor without a watcher is not equivalent to the incumbent — it is worse at the one thing that failed. `AUDITOR_MODEL_EVALUATION.md` §2.4 reached the same conclusion for GLM; Hydrogen inherits it unchanged.

### 3.4 The cheap trial: one blind re-audit against a written answer

One batch, report-only, no build, no repo change. Pick **either** range; both have the answer already written down, and both must be given without naming the file, the function or the verdict — otherwise the trial measures reading comprehension.

| Batch | Pin | Brief | The known answer | What it tests |
|---|---|---|---|---|
| **H1 — mirror trade chart** (default) | `26d1141` = `a641efa^` `[M]` | the `build-nfl-mirror-3` R3/R4 review request and the mirror plan's acceptance, as the builder posted them | `data/audit_reports/audit-5/residual/report.md` N1: a promoting trade writes **virtual `Roster`** instead of canonical **`Signed`**, so the new active starter is missing from the same write's chart (`pipeline/league_office.py:776-777` vs `pipeline/league_office_mirror.py:199`); fixed at `a641efa`, rechecked CLOSED in `audit-5/n1/report.md` | does it find a real defect at a real `file:line`, with a repro |
| **H2 — fact-gate R1** | `8cfa931` (the U3 merge) `[M]` | "review this merge of the article fact gate against its plan" | `ARTICLE_FACT_GATE_PLAN.md` §3.1: the evidence window is bounded at **24,000 characters** while the assignment alone is 46–48k and the evidence 56–206k, and the absent-person rule is unqualified — so real names come back CONTRADICTED and 20–62% of claims are flagged, almost all falsely; fixed by U4 at `7b87a4e` | does it find a **numeric/structural** defect from the code, not just a plausible claim |
| **H3 — false-positive control** (optional, free) | `feb0c29` (docs + one eval line) | "audit this commit as you would a builder's fix" | **no findings** — "nothing to fix", with what was checked | does it invent findings or paths when there is nothing to find |

Score it on `AUDITOR_MODEL_EVALUATION.md` §5's gate, unchanged:
1. the known defect found, with a repro;
2. **100% of cited `file:line` resolve** (`grep -n` — mechanical; one invented path fails the gate);
3. ≤1 non-reproducing finding per batch, and H3 clean;
4. a report a builder could fix from in one pass;
5. credit burn within **2×** the ~$1.25/batch (inferred) estimate;
6. six hours without losing the pin or repeating itself (H1+H2 in one session is the cheapest version);
7. a quota readout exists before it is trusted unattended.

**Cost:** one batch is ~$1.25 at Gemini API list, ~9.8M tokens of Antigravity plan quota, or $0 on the free tier if JR accepts the training term. No repo change, no launcher, no skill.

**What the trial cannot tell you:** whether it writes a good *fix brief* — that is the builder's judgement, and it is condition 4.

### 3.5 The trial, run (2026-11-12, `gemini-4-hydrogen-high`)

JR started a Hydrogen session on the Antigravity CLI at 16:10 EST; it answered on the board at 16:19 (M-0702) and left two reports. Everything below is **measured**, and none of it is an audit verdict: this session does not audit, and the range it reviewed is still unaudited for Project Mu's purposes.

**Setup.** Two clean snapshots made with `git archive` (no `.git`, no `data/`, no board) in `/tmp/opencode/hydrogen-trial/`: **H1** at `26d1141` (`a641efa^`), briefed as the mirror's U18 R3/R4 trade-and-list work audited against `NFL_WEEKLY_MIRROR_PLAN.md`; **H3** at `feb0c29`, briefed as "audit this change cold", where *nothing to fix* is the correct outcome. Both briefs withheld the answer, named no file, function or verdict, and forbade network, model calls, database, Madden, save writes and any read of the main checkout. 75-minute cap.

| Check (`AUDITOR_MODEL_EVALUATION.md` §5) | Result | Label |
|---|---|---|
| Known defect found, with a repro | **Yes, exactly.** H1: `Plan.trade_player` writes `"Roster"` into `self.view` at `pipeline/league_office.py:776`, so `MirrorAdapter.chart_override` (`league_office_mirror.py:199`) drops the promoted man from the same write's chart. It named **eight** further consumers (`Plan.rosters`, `step_coach_charts`, `Plan._charts`, `step_legality`, `step_injuries`, `step_drift`, `set_contract`, `retirement_window._is_active`), the two tests that missed it, and an offline repro with observed vs expected values (`('Roster', 0)` vs `('Signed', 0)`, `chart_override(0) == {}` vs `{'WR': [6]}`) | `[M]` |
| 100% of cited `file:line` resolve | **24 of 24** in H1, **23 of 23** in H3, against the pinned trees (mechanical `score.py`) | `[M]` |
| ≤1 non-reproducing finding; the control clean | 0 in H1. **H3 did not return the null:** it audited the whole snapshot rather than the one-line docs change and reported 3 findings — all three citation-accurate at the pin, so not invented, but the control did not run as designed (**my brief's flaw**, not its error) | `[M]` |
| A report a builder can fix from in one pass | Yes on inspection: root cause, every affected caller, the tests to add with their assertions, the one-line fix, expected values, and what it checked and found fine | `[M]` |
| Credit burn within 2× | **Unmeasurable** — no Antigravity usage instrument; wall time **8 min** for both batches (H1 in **3 m 44 s**) | `[M]` / `[U]` burn |
| Six hours without losing the pin | Not tested (8 minutes); it did hold two pins and compared the trees | `[M]` |
| A quota watcher | **Still absent** — the one condition it cannot meet today | `[M]` |
| Blindness | **No contamination found**: zero answer-only tokens (the pgid it used is the tree's own fixture, not the audit's), phrasing differs from the board's, and every citation is in-tree | `[M]` |

**What it found outside the brief.** H3's three findings are about the article-writer default, not the docs commit: (1) `SwarmDispatcher.dispatch_team_local` (`swarm_dispatcher.py:2441`) defaults `model` to `DEFAULT_WRITER_MODEL` (`glm-5.3-flash`) and forwards it to `run_team_social`, which at that pin built Google's `genai.Client` — fixed later the same day by `6f7abe8`/`80ec761`, which added `is_go_model` → `go_client()`; (2) `custom_commission.classify_prompt` still defaults to `DEFAULT_WRITER_MODEL` while constructing `genai.Client` (`custom_commission.py:76`, `:108`) — **the same pattern is still on `main` at `fe8eeff`**, with `server/debug_api.py:932` and the CLI as its live callers `[M]`; (3) a LOW about `.rsplit("/", 1)[0]` on a drive-root `MMC_MOD_MANAGER_EXE` in `game_launch.py:88`. **None of these is a verdict.** (1) and (3) are facts about a pinned tree and its later fix; (2) is a fact about `main`, and whether it is a defect is for an Opus/Codex/Argon auditor to decide — it is now the most concrete item a real audit queue could pick up.

**What the trial changes.** One batch is not the §5 gate ("≥2 of 3 known defects"), but the one known answer available was found first time, at the right line, with a repro, in under four minutes, and every citation in both reports resolves. On this evidence Hydrogen is a **credible second auditor and a plausible first-pass sweeper**, and it is still **not** auditor of record: the gate is unmet at 1 of 3 known answers, the burn is unmeasured, and the quota watcher does not exist. The next step is H2 (the fact-gate R1 range) on a clean tree — the board now carries that range's verdict, so it needs the same sealed setup — plus one more false-positive control that is scoped to a *single* change rather than a whole tree.

---

## 4. What it must not be used for

1. **Not as auditor of record on this evidence.** No Mu report exists, no citation discipline has been measured, and the quota instrument is missing. Also, independently: **a DeepSeek-routed session never audits**, and no session audits its own work — this doc is not a verdict and does not stand in for one.
2. **Not on the Antigravity route for save-writer, staff-contract or `league_office` verdicts** even after a trial passes, without JR's explicit word: `AUDITOR_MODEL_EVALUATION.md` D4 keeps the incumbent there because a wrong CLOSED on a save write is expensive. Start it on docs, stats, mirror and single-file fixes.
3. **Not as a live game controller, and it never authorizes a key.** `LOCAL_VISION_LATENCY.md` already settled that vision describes and the catalog decides; cloud latency per frame is unmeasured, and a network-dependent model in the press loop is a new failure mode. It may *judge* frames offline; it may not press.
4. **Not on the newsroom as a "free upgrade".** $216/mo, no EQ-Bench row, and it replaces a model that costs $29/mo and was chosen on JR's blind read. If it is ever tried, it is a side-by-side, not a default.
5. **Not through the Go allowance.** It is not in the catalogue `[M]`; do not size a plan assuming the $100/month buys it. The Google-compatible passthrough is untested `[U]`.
6. **Not on the Gemini free tier with private material.** Free-tier content is used to improve Google's products `[P]`. That is a JR decision of the same kind as D2 for Muse Spark, and it has not been taken for this repo's code, saves or franchise data.
7. **Not as a fact-gate checker or a progression/tuning calculator.** The gate's rubric (verbatim quotes, numeric triples, status words) is untested on it, and tuning constants come from code, never from a model's arithmetic. It also must not run the gate on `capture` comparison runs — that rule already exists.
8. **Not as an embeddings model.** `gemini-embedding-001` stays; nothing here changes `newsroom/league_memory.py`.

---

## 5. What would change this

In rough order of leverage:

1. **~~The blind re-audit (§3.4).~~ Done 2026-11-12 (§3.5): H1 found first time, 24/24 citations.** Next: **H2** (the fact-gate R1 range) on the same sealed setup, plus a false-positive control scoped to a single change, to complete §5's "≥2 of 3" and "clean control" conditions.
2. **A quota number.** Either find Antigravity's usage API (a Console/plan endpoint, or a documented `agy` flag) or accept that the trial's own token count, taken by hand, is the sizing evidence. Without it, auditing on the plan route cannot be run unattended.
3. **A `gemini:` backend in `tools/madden_pilot/vision.py`** and one `vision_bench` run on the same 202 frames — that is the only thing that can settle the screen-reader question, and it also gives the pilot an accuracy ceiling for a cloud model.
4. **One judged article week** on the Week 18 archive (`tools/article_model_compare.py` + `article_factcheck.py`): if it outscores `glm-5.3-flash` on JR's read *and* on contradictions, the price question becomes real; if not, it is closed for the desk.
5. **The four unread short-text sites** on Hydrogen, once the writer question is settled — the same reads `GLM_UNIFICATION_MEASUREMENTS.md` §6–§8 still owe.
6. **Overlap with the GLM decision.** `CLOSE_OUT_CHECKLIST` P8-06b is deferred pending a prompt/voice pass; if JR runs that pass, adding Hydrogen as a third arm costs one more env var and no new plumbing.

---

## 6. Sources

**Measured here (2026-11-12):**
- `GET https://generativelanguage.googleapis.com/v1beta/models` with the repo's `GEMINI_API_KEY`: 64 models, `gemini-4-hydrogen` present, 1,048,576 in / 65,536 out, methods `generateContent`, `countTokens`, `batchGenerateContent` (no `createCachedContent`).
- `GET https://opencode.ai/inference/go/v1/models`: 30 ids; Gemini ids = `gemini-4-argon` only. `GET https://opencode.ai/inference/v1/models` (Zen): 42 ids, `gemini-4-hydrogen` with `release_date` 2026-11-12, 1,048,576 / 65,536, cost $1.50 / $0.075 / $7.50, modalities incl. image/video/audio/PDF.
- `agy models` (1.5.5): `gemini-4-hydrogen-low|medium|high|xhigh`. The probe (§1.3) answered in one line and refused nothing. `agy --help` has no `usage` subcommand (`agy usage` → "unexpected argument").
- `python3 -m tools.go_usage`: 5h 7% (resets 23:50Z), weekly 44%, month 22%.
- `https://eqbench.com/creative_writing_chartdata.js` and `creative_writing_longform.js`: 0 occurrences of "hydrogen"; `gemini-4-argon` present.
- `data/audit_reports/audit-5/residual/report.md` (N1), `audit-5/n1/report.md` (CLOSED), `data/audit_reports/` has no Hydrogen report; `git log` confirms `a641efa^` = `26d1141`, and `8cfa931` is the fact-gate U3 merge.
- `docs/reference/AUDITOR_MODEL_EVALUATION.md` §1–§6 (audit-5's 137.2M / 14 / 18.5 h, the gate, D4/D5); `docs/reference/MODEL_ROUTING_EVALUATION.md` §2–§8 (Mu's token mix, prices, the desk's history); `docs/reference/GLM_UNIFICATION_MEASUREMENTS.md`; `docs/CLOSE_OUT_CHECKLIST.md` P8-05/P8-06/P8-06b; `.claude/skills/audit/SKILL.md`; `docs/reference/LOCAL_VISION_LATENCY.md`; `tools/madden_pilot/vision.py` (`PROVIDERS = ("ollama", "deepseek")`); `newsroom/model_default.py`; `extract.py` (`MADDEN_EXTRACT_MODEL`).

**Published (read 2026-11-12):**
- Google DeepMind, [Gemini 4 Hydrogen model card](https://deepmind.google/models/model-cards/gemini-4-hydrogen/) — 1M unified context, 64K default / 1M long-decode output, adapted from Carbon, knowledge cutoff March 2026, the evaluation table (TB 4.1 57.0, SWE-sweep 13.5, ALE 71.6, AutomationBench 76.1, Vending-Bench 2 $14,549, OSWorld 77.6, MKA 53.4, Chartography 66.6, TB-Science 51.4, HLE-Diamond 58.8), "may exhibit hallucinations", distribution channels incl. Antigravity.
- [Gemini Developer API pricing](https://aistudio.google.com/docs/pricing) — Hydrogen free tier free-of-charge but used to improve products; paid $1.50 / $7.50 / $0.075; Batch/Flex half; Priority $2.70 / $13.50; Gemini 3.8 Flash $0.75/$3.75 through 2026-12-31 then $1.50/$7.50.
- [Gemini API rate limits](https://aistudio.google.com/docs/rate-limits) — Hydrogen batch enqueued tokens 3M (Tier 1) / 400M (Tier 2) / 1B (Tier 3); no per-model interactive table published.
- [Google Antigravity, "Gemini 4 Hydrogen and Orchestrator"](https://antigravity.google/blog/gemini-4-hydrogen-in-google-antigravity) (2026-11-12) — paid AI Pro/Ultra rollout, four thinking levels with `xhigh`, AA Intelligence Index 51 (Argon 50, 54 at highest), TB 4.1 57% vs 3.8 Flash 18.2%, Orchestrator Ultra-only at near-Carbon success for 24% less quota and 19% faster.
- [Antigravity pricing](https://antigravity.google/pricing/) — Individual $0 (basic weekly rate limits), AI Pro, AI Ultra, flexible AI credit pool, Business via Google Cloud.
- [Vals AI, Gemini 4 Hydrogen](https://www.vals.ai/models/google_gemini-4-hydrogen) — Vals Index 68.49% #6/37, Finance 66.14% #2/40, Legal 73.63% #9/38, EMB 75.80%, Finance Agent 65.12%, Legal Research 53.85%, Tax Agent 52.30% #1/85, Vibe Code 87.78% #13/128, HLAB 11.25%, **TB 4.0 54.04% #11/64**, $9.838/test, 32m05s.
- [BenchLM, Gemini 4 Hydrogen](https://benchlm.ai/models/gemini-4-hydrogen) — unranked, 6 rows: AutomationBench 76.1, OSWorld 77.6, MKA 53.4, TB-Science 51.4, FrontierCode 2.0 38.2, Chartography 66.6; lab-reported TB 4.1 57.0, ALE 71.6, SWE-sweep 13.5.
- [OpenCode Zen docs](https://opencode.ai/docs/zen) — pay-as-you-go credits, zero-retention privacy except the named free models; Hydrogen is in the catalogue endpoint but **not yet in the docs pricing table** (released today).

**Not established, on purpose:** Antigravity's per-plan limits and JR's exact tier; whether Zen credit is funded on this account; free-tier Hydrogen quotas; the Go Google-passthrough answer for it; per-frame vision latency and accuracy; whether it can hold an 18-hour session with batch subagents. Nothing here was billed except the single Antigravity probe, and no article, audit or pipeline run was executed.
