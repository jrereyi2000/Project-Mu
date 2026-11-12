# Could a GLM 5.3 session be Project Mu's auditor?

**Date:** 2026-11-12 · **Asked by:** JR (via `standby-2`, board M-0654) · **Status:** research only, nothing built
**Question:** can the auditor role move to (or add) a model outside Claude and Codex — specifically a GLM 5.3 session in this repo — and what would it take, what would it be worth, and what would it cost against tonight's Codex wall and the Claude plan?

---

## 1. Bottom line

1. **Build it — as a supplement, not a replacement.** A `glm` auditor route is **~500 lines across 9–10 files, mostly copy work**, and the smallest version that actually audits is **~100 lines / 4–6 hours**. Nothing in the route is research-grade: the launcher is a copy of `tools/muse_session.sh`, Z.ai's **GLM Coding Plan officially lists OpenCode as a supported tool**, and OpenCode already carries a `zai` provider with `glm-5.3` at list price.
2. **The hard part is not the plumbing, it is trust and quota, and both are measurable in one day.** GLM 5.3 is genuinely strong where an auditor spends most of its time — **95.4% on SWE-bench Verified** in an independent bash-only harness, rank 6 of 88 — but its independent long-horizon agentic score is poor (**38.9% on Terminal-Bench 4.0** against Opus 5.5's 64.8, Sol's 55.1 and Argon's 57.6), and it is **very token-hungry** in that harness (**31.5M tokens per task, 11× Sol's 2.9M**). An auditor is exactly a long-horizon agent, and an audit's product is *citations*, so the two risks are the same two things: invented `file:line`, and credit burn.
3. **Today's auditor is not short of skill, it is short of quota.** audit-5 measured, from Codex's own session logs: **137.2M input tokens (134.0M cached) + 354K output across 14 batches in 18.5 hours**, which took the Plus plan to **99% of the 5-hour window and 72% of the weekly one**. It stopped at 04:46 EST with "try again at 7:00 AM". Claude is in the same state for a different reason: its **weekly window read 98%** on 2026-11-11 20:30 (resets Nov 16 03:00), and the commit-audit hook's headless Claude review has **failed 92 times on Claude's usage limits** (81 on the week ending Nov 9, 10 on this week, 10 on 5-hour windows). So the case for a third auditor is **capacity**, not model quality.
4. **Go is the wrong pipe for GLM 5.3, and the reason is measured here.** Go's allowance for `glm-5.3` is charged at **exactly 4.00× the list cost** (two probes, 27 and 410 tokens, 2026-11-12), against a **$15/month** per-model Go limit. audit-5's token mix prices at **$40.78 of list** — **$163 of Go allowance**, 10.9× the monthly limit. (`glm-5.3-flash` measured **1.00×** and `deepseek-v4.1-flash` **0.60×**, so this is a GLM-5.3 pricing fact, not a Go-wide one.)
5. **Recommended route and price:** **Z.ai GLM Coding Plan Pro, $80/month** (12,000 credits / 5 h, 60,000 / week) — this is the only route that carries a 14-batch day without metering, and OpenCode is a supported tool. Direct API ($1.40 / $0.26 cached / $4.40 per million) is the cheaper way to **run the trial** (~$9 for three known-answer batches) and the cheaper steady state only below ~2 audit sessions a month.
6. **Recommendation:** run the one-day known-answer trial in §4 before building anything. If GLM 5.3 finds the known defect, cites only real `file:line`, and burns ≤2× the assumed credits, build the `glm` route and run it as a **second auditor for a second builder lane** — keeping Opus/Codex as auditor of record for the save-writer, staff-contract and `league_office` batches, where a wrong CLOSED verdict is expensive. Do not retire Codex on this evidence.

---

## 2. §1 Feasibility — what a `glm` auditor session concretely is

### 2.1 What the audit skill demands of any model

`.claude/skills/audit/SKILL.md` (read end to end, 126 lines) requires, in the skill's own terms:

| Requirement | Where the skill says it | What the route must provide |
|---|---|---|
| Agentic shell + git | §4.1 pin a worktree at a sha; §4.3 run repros and `tools.offseason_check` | bash/edit-free tool set in the harness |
| Pinned worktree per batch | §4.1, §4.5 own `worktree` path | unchanged, harness-independent |
| Report-only writing | §4 preamble, §4.5 report in a scratch folder | a report path convention per harness (Codex wrote `data/audit_reports/<label>/`) |
| Run pytest / real Postgres scratch DB | §4.3–4.4, ACCEPTANCE_CHECKS | shell + `docker exec` to `madden-db` |
| Live board read + listener + post | §2.1 `tools/agent_board_listen.sh`, §2.2 inbox, §2.3 NOTICE | unchanged (label-based, not model-based) |
| Background batch subagents | §4 "one `Agent` with `run_in_background: true`" | the harness's own subagent mechanism |
| Multi-hour session | §4.5 full fix brief per round trip; §6 archive | 1M context + checkpoint discipline |
| Quota watching + hand-off | §3 `tools/usage_check.py`, 85% / 99% | **a GLM equivalent that does not exist yet** |
| Model id as board signature | §1.1, §7 | `glm-5.3` in `from:` and the commit trailer |
| **Never DeepSeek-routed** | §1.1 | satisfied: GLM is Z.ai, a different family from DeepSeek |

The one new constraint the route introduces: the skill's §3 is written for the Claude status line (`data/usage/rate_limits.json` → `tools/usage_check.py`). A GLM session's credits live on the Z.ai console; there is no status-line file. **That is the genuinely hard piece of the build** (§2.3).

### 2.2 How the existing routes are built (and what `glm` copies)

| Piece | Existing shape | `glm` version |
|---|---|---|
| `tools/spawn_agent.sh:63-72` | `case "$agent"` maps `claude\|opus\|deepseek\|deepseek-claude\|muse\|codex\|gemini` to a command; unknown agent exits 2 | **+1 case line** `glm) cmd=("$here/glm_session.sh" --prompt "$prompt") ;;` and the usage string in 3 places (lines 6, 43, 71) |
| `tools/muse_session.sh` (72 lines) | refuses outside a checkout with `.opencode/opencode.json` (exit 2); exports `MU_AGENT_CLASS`, `MU_AGENT_HARNESS=opencode`, `MU_AGENT_MODEL`, `MU_OPENCODE_MODEL`, `MU_OPENCODE_SESSION`; **creates the session with the model then re-pins it** through `opencode api` (the TUI has no `--model` in v2.2.7, spike S-M1, `MUSE_EMBER_EVALUATION.md` §1), exit 3 if the pin fails; prints a route line; `exec opencode --session` | **copy, ~75 lines.** Change: `MU_GLM_MODEL` default `zai/glm-5.3` (OpenCode's provider is `zai`, base `https://api.z.ai/api/paas/v4`, `glm-5.3` list $1.40/$4.40 — confirmed in `models.dev` 2026-11-12), `MU_AGENT_CLASS=glm`, title "Project Mu GLM session". No new logic. |
| `tools/opencode_session.sh` (41 lines) | the DeepSeek launcher: project pin `opencode-go/deepseek-v4.1-flash`, sets `MU_AGENT_CLASS=deepseek` — the marker `/audit` refuses on | **not touched**; `glm_session.sh` must set `MU_AGENT_CLASS=glm` so the audit skill's DeepSeek refusal doesn't trip and the model string signs correctly |
| `tools/agent_board_inbox.py:11-17` | `RECIPIENTS` maps an inbox alias to a set of model names; `--watch` filters by `to:` | **+1 line** `"glm": {"glm", "glm-5.3", "zai-glm-5.3"}`. Optionally skippable at first: the audit skill listens through `tools/agent_board_listen.sh <label>`, which is model-agnostic, so the label listener works without it. |
| `.opencode/opencode.json` | one line: `"model": "opencode-go/deepseek-v4.1-flash"` | **no change** — pin per session exactly as Muse does, because the project pin must stay DeepSeek for the builders |
| model pin | `MU_OPENCODE_MODEL` + the two `opencode api` posts | same, plus `MU_GLM_MODEL` override for a `glm-5.3-flash` cheap pass |
| Skill | `.claude/skills/deepseek/SKILL.md` (12 lines), `.claude/skills/muse/SKILL.md`; OpenCode discovers `.claude/skills` (AGENTS.md) | **new `.claude/skills/glm/SKILL.md`, ~16 lines**: route, `MU_AGENT_MODEL=glm-5.3`, inbox alias, `Model: glm-5.3` trailer, "may audit but never DeepSeek work" note |
| Audit skill model line | `audit/SKILL.md:3` (description), `:5` (`model: claude-opus-5-5`), `:10` (agent list), `:16` (the model check) | **4 edits** to add GLM: description "Opus 5.5, Codex, Argon or GLM 5.3", the DeepSeek refusal stays, and §3's quota section gains a GLM branch |
| Docs | `docs/reference/AGENTS.md` "Model inboxes" table (5 rows) + a paragraph per harness; `CLAUDE.md` "Skills by model class" | **+1 table row, +1 paragraph, +1 clause** (`glm` may run `/audit` but not `/builder`? — see D5 in §6) |
| Tests | `tests/test_muse_session.py` (252 lines, 11 tests), `tests/test_opencode_session.py` (192, 9), `tests/test_spawn_agent.py` (204, 15), `tests/test_agent_board_inbox.py` (136, 5) | `tests/test_glm_session.py` **~200–230 lines / 10–12 tests** (adapt the Muse suite: stub `opencode`, throwaway checkout, pin calls recorded, exit 3 on failed pin, no `--model` on the TUI command line); `test_spawn_agent.py` **+2 cases ~30 lines**; `test_agent_board_inbox.py` **+2 cases ~25 lines** |
| Quota watcher | `tools/usage_check.py` reads `data/usage/rate_limits.json`, written by `tools/usage_statusline.py` (the Claude Code status line) | **new, uncertain**: `tools/glm_usage.py` (~60–90 lines + ~50 test lines) *if* Z.ai exposes credit usage over an API; otherwise the skill says "read the console at each batch boundary", which is not the same guarantee |

### 2.3 The sized build

| Item | Files | Lines | Effort | Kind |
|---|---|---|---|---|
| `tools/glm_session.sh` | 1 new | ~75 | 2 h | copy of Muse |
| `tools/spawn_agent.sh` | 1 edit | +5 | 15 min | copy |
| `tools/agent_board_inbox.py` | 1 edit | +1 | 10 min | copy |
| `.claude/skills/glm/SKILL.md` | 1 new | ~16 | 30 min | copy |
| `audit/SKILL.md` model + queue + report-path lines | 1 edit | ~8 | 1–2 h | **judgement** |
| `docs/reference/AGENTS.md`, `CLAUDE.md`, `PROJECT_SPEC.md` | 3 edits | ~8 | 45 min | judgement |
| Tests | 1 new + 2 edits | ~280 | 4–6 h | copy-with-care |
| Quota watcher (if an endpoint exists) | 1 new + tests | ~140 | 3–4 h | **uncertain** |
| **Total** | **9–10 files** | **~480–530** | **1–2 focused days** | |

**Smallest route that actually audits** (no inbox alias, no quota watcher, no new test file): `glm_session.sh` + the spawn case + `glm/SKILL.md` + 4 lines in the audit skill = **~100 lines, 4–6 hours**. It works because everything the auditor needs — worktree, tests, scratch Postgres, board listener, reports — is harness-neutral shell already in the repo.

### 2.4 What is copy work and what is genuinely hard

- **Copy work (80% of the lines):** the launcher, the spawn case, the inbox alias, the skill stub, the tests. Muse proved this shape on 2026-11-12, including the non-obvious part (the TUI has no `--model`, so the model is pinned through `opencode api`).
- **Genuinely hard #1 — the quota hand-off.** The Claude auditor stops at 85% of the 5-hour window because `usage_statusline.py` writes the numbers every turn. Z.ai publishes no such local figure; the plan's credits are on `z.ai/manage-apikey/billing`. Without an endpoint, a GLM auditor either watches a web page (a human in the loop, which defeats the point) or runs until the API starts refusing — the exact failure mode that parked audit-5 tonight. **A GLM auditor without a quota watcher is not equivalent to the incumbent; it is strictly worse at the one thing that failed.**
- **Genuinely hard #2 — the batch-subagent shape in OpenCode.** The skill's §4 assumes Claude Code's `Agent` with `run_in_background`; Codex and Argon use their own. OpenCode has subagents, but the report-path convention and the "main session keeps the board, subagent never posts" rule need one sentence each.
- **Genuinely hard #3 — trust.** Not code at all; §3 and §4.

---

## 3. §2 Functionality — would a GLM-class model audit well?

### 3.1 What the job needs, and what is actually measured

The audit job's demands, in the order audit-5's reports exercise them: repo-scale reading (grep/read over a 2,400-line production file and 200-table schema), reliable tool calls under a large tool set, running pytest and real-Postgres fixtures, **citing `file:line` that a builder can act on**, not inventing paths or values, and staying coherent for hours.

**Measured (independent benchmark, or in this repo):**

| Evidence | Value | Source |
|---|---|---|
| GLM-5.3 SWE-bench Verified, bash-only mini-SWE-agent | **95.4%**, rank 6/88, $0.34/task, 958K tokens, 14m01s | Vals AI leaderboard, updated 2026-09-01 |
| GLM-5.3 Terminal-Bench 4.0, same harness | **38.89%**, $9.37/task, **31.54M tokens**, 1h23m | Vals AI TB 4.0 |
| GLM-5.3 Terminal-Bench 2.1 | Vals **71.5%** vs Z.ai's own **88.2%** vs AA **83.9%** | BenchLM row set (three runners) |
| GLM-5.3 Terminal-Bench 4.1 | 41.2% | tbench leaderboard via BenchLM |
| GLM-5.3 DeepSWE v1.1 / FrontierSWE / SWE-Marathon | 66.9 / 78.1 / 42.5 (vendor) | Z.ai model card |
| GLM-5.3 AA-Omniscience **hallucination rate** | **38.9%** | BenchLM carried from AA |
| GLM-5.3 AA agentic / coding index | 53.4 / 74.8 | BenchLM carried |
| GLM-5.3-Flash SWE-bench Verified | **92.0%**, $0.02/task, 793K tokens, **1h04m** | Vals AI |
| GLM-5.3-Flash Terminal-Bench 2.1 | 84.3 (vendor) | Z.ai / Qubrid |
| Tool calling through Go's Google path, 12 models | 12/12 returned correct `functionCall` args, `glm-5.3` included | `MODEL_ROUTING_EVALUATION.md` §7.3 (measured in-repo) |
| GLM-5.3-Flash under the SDK's AFC loop | misread a schema ("defines no city argument") while the raw call was correct | same, §7.3 caveat |
| GLM-5.3-Flash on Mu's real article loop | 6 articles, 129 tool calls, 583 s, $0.317; **5.4%** contradicted claims (Gemini 2.5%) | same, §8.1 |
| ★ **Actually an auditor, tonight's Codex** | 14 batches, 18.5 h, 137.2M in / 354K out, 5h 99% / weekly 72% | `~/.codex/sessions/...` rollout of audit-5 (measured here) |
| ★ **Actually an auditor, Claude** | weekly window **98%**; 92 commit-audit reviews failed on usage limits | `data/usage/rate_limits.json`; `data/commit_audits/*.md` |

**Inferred (no direct measurement exists):**

- whether GLM 5.3 cites Mu's own files and line numbers without inventing them;
- how it holds a 6–18-hour session in OpenCode (compaction, re-reading the pin, repeating a finding);
- what one Mu audit batch costs it in tokens — the 31.5M-vs-2.86M spread on TB4 is an 11× uncertainty band;
- whether Z.ai's plan terms tolerate a 14-batch autonomous queue (the Coding Plan says **"use limited to supported tools"**, and OpenCode *is* listed — that part is confirmed — but throughput policy is not published);
- Argon's audit record in this repo: **none exists** (`data/audit_reports/` has no Argon report), so "already an allowed auditor" is a rule, not evidence.

### 3.2 Model by model

**GLM 5.3 (Z.ai)** — the strongest open-weights coder by SWE-bench Verified (95.4%, ahead of Kimi K3's 93.4 and GLM 5.3 Flash's 92.0), 1M context / 131K output, reasoning always on (low/high/max, max default). Two red flags for *auditing*: the independent long-horizon score collapses (38.9% TB4) where the auditor lives, and its AA hallucination rate is the highest of the shortlist (38.9%). A model that scores 95% fixing a known issue but 39% driving a terminal for an hour is a plausible **fixer** and an unproven **auditor**.

**GLM 5.3 Flash** — 92% SWE-bench at **$0.02/task**, but **1h04m per task** and unscored on creative benchmarks; in this repo it wrote 5.4% contradicted claims and misread one tool schema through a SDK. Best use is a **cheap first sweep or second verifier**, not the auditor of record.

**Kimi K3 / K3.1 Pro (Moonshot)** — K3 is 93.4% SWE-bench; K3.1 Pro claims ~65.7% on TB 4.0, "within a point of Opus 5.5". That claim is vendor-run, and its predecessor scored **17.17%** on Vals' independent TB4 with **27.6M tokens/task and 3h09m**. Same shape of risk as GLM, with a larger vendor-vs-independent gap and a heavier token bill. Route exists (`kimi-for-coding` is a recognised OpenCode provider), plans $19–$199 with an unpublished monthly pool.

**Qwen3.8 Max (Alibaba)** — 85.6% SWE-bench (Vals), but **2.74M tokens and 41m42s per task**, and 67.7% SWE-bench Pro (vendor) against Fable 5's 80.0. The Alibaba Coding Plan is **request-metered** (6,000/5h, 45,000/week, 90,000/month) and **"interactive use only"** — a policy clause that sits badly with an unattended auditor. Not recommended.

**Grok 4.7 (xAI)** — Terminal-Bench 4.0 **37.6%** vendor (4.6 measured 17.17% independently), DeepSWE 71.0 vendor, ~2.5× the token use of predecessors, and prompts ≥200K bill at double rate, which an audit context will hit. Vals index #18 of 36. Not recommended.

**DeepSeek V4.1 Flash — excluded.** It is the **builder** model: an auditor must come from a different family to check DeepSeek work (the rule at `audit/SKILL.md:10`). It is also the cheapest and weakest reviewer of the shortlist. It appears in §3 only to price the exclusion ($0.08/batch).

**Gemini 4 Argon (already allowed)** — TB4 57.58% independent (Vals), DeepSWE 77.9% vendor, AA index 52.6 against Opus 5.5's 57.6; 1M context; **price doubles on 2026-12-01** ($2/$10 → $4/$20). It is the natural *third* auditor today, and it is already sanctioned — but nobody has run it as one, so its Mu record is unmeasured rather than good.

**Incumbents to beat.** **Claude Opus 5.5**: TB4 64.8% on the public board (66.4 vendor), BenchLM coding score 82.4 (#2 of 158), 1M context / 128K output, $4/$20 with $0.20 cache reads. **GPT-6.1 Sol**: TB4 55.05% (Vals) / 58.2% public, DeepSWE 75.2% vendor at ~$1.72/task — the cheapest strong agent per task in the set, and the one that ran out tonight. **No published GLM-5.3 figure beats either on long-horizon agency; one published figure (SWE-bench) beats both.**

---

## 4. §3 Quota and cost, against the incumbents

### 4.1 What an audit batch actually costs — the measured anchor

From Codex's own rollout logs for audit-5 (2026-11-11 10:15 EST → 2026-11-12 04:46 EST, 15 rollouts: 1 parent, 8 `thread_spawn` batch subagents, 6 small guardians):

| Measured | Value |
|---|---|
| Batches audited | **14** (14 `report.md` in `data/audit_reports/audit-5/`, 969 lines; 32 reports in 21 folders repo-wide) |
| Wall clock | **18.5 h** |
| Input tokens | **137.2M**, of which **134.0M cached** (2.3% uncached) |
| Output tokens | 261K + 93K reasoning |
| Per batch | **9.8M input** (9.57M cached) + ~25K output |
| Plan state at the wall | **5-hour 99%**, **weekly 72%** (Plus, $20) |
| Reset | 2026-11-12 07:00:39 EST — the "try again at 7:00 AM" in M-0642 |
| Test volume per batch (examples) | stats3: 25 offline PASS, 4 real-PG PASS in 143 s, 3 parent FAILs in 158 s; u18: 176 PASS; wave5: 109 PASS; residual: 146 Python + 6 PG + e2e 41 PASS/4 INFO |

So **one heavy audit day ≈ 137M tokens ≈ 72% of a Codex Plus week and all of one 5-hour window.** That is the unit every row below is priced against. Because a different harness caches differently, every figure other than Codex's own is **inferred from this measured mix**, and §4.4 says so again.

### 4.2 Prices and limits, as measured on 2026-11-12

| Model | Route | 5-hour | Weekly | Monthly / other | List price per 1M (in / cached / out) |
|---|---|---|---|---|---|
| **GPT-6.1 Sol** | Codex Plus $20 | 15–160 local msgs; **measured 99% after 14 batches** | applies on top; **measured 72% after one heavy day** | — | $2 / $0.10 / $10 (API) |
| **Claude Opus 5.5** | Claude Max 20x $200 (Pro $20, Max $100, Ultra $200) | 5h window; Ultra has none for verified accounts | **measured 98%, resets Nov 16 03:00** | — | $4 / $0.20 / $20 |
| **GLM-5.3** | **Z.ai GLM Coding Plan Pro $80** (Lite $18, Max $168) | **12,000 credits** (Lite 2,000 / Max 28,000) | **60,000 credits** (Lite 10,000 / Max 140,000) | — | credits = (in×6.9 + cached×1.7 + out×24)/10,000 |
| **GLM-5.3** | Z.ai direct API | tiered, unpublished | — | pay-as-you-go | $1.40 / $0.26 / $4.40 |
| **GLM-5.3** | **OpenCode Go $10** | $3 (20% of $15) | $7.50 | **$15/month** | **charged at 4.00× list — measured** |
| **GLM-5.3-Flash** | Go $10 / Z.ai | $12 of $60 | $30 | $60 | $0.15 / $0.03 / $0.50 (Go charges 1.00×) |
| **Kimi K3.1 Pro** | Kimi API, or Kimi Code Max $99 / Ultra $199 | rolling 5h | none on new plans (monthly pool) | monthly pool **unpublished** | $2 / $0.20 / $10 |
| **Qwen3.8 Max** | Alibaba API, or Coding Plan Pro $50 | 6,000 requests | 45,000 requests | 90,000 requests; **interactive use only** | $2 / $0.25 / $6 |
| **Grok 4.7** | xAI API; SuperGrok $30 / Plus $100 | account-relative, unpublished | unpublished | — | $2 / $0.50 / $6; **$4 / $1 / $12 at ≥200K** |
| **Gemini 4 Argon** | Google AI Pro $19.99 / Ultra $99.99–$199.99; API | 5h refresh | weekly cap | — | $2 / $0.10 / $10 → **$4 / $0.20 / $20 from Dec 1** |
| **DeepSeek V4.1 Flash** | Go $10 | $20 of $100 | $50 | $100 | $0.15 / $0.003 / $0.60 (Go charges 0.60×) — **excluded** |

### 4.3 The comparison table

$ per batch = the measured Codex audit footprint (9.8M in / 9.57M cached / 25K out) repriced at each model's list rate; "session" = all 14 batches (audit-5). **All $ in this table are inferred from a measured token mix**; nothing here was billed.

| Model | Route | 5h | Weekly | Monthly | $/audit batch (inferred) | $/audit-5 session (inferred) | Can it audit? |
|---|---|---|---|---|---|---|---|
| **GPT-6.1 Sol** | Codex Plus $20 | **99% reached** (measured) | **72% per day** (measured) | — | $1.66 | $23 | **Yes** — incumbent; walled mid-queue tonight |
| **Claude Opus 5.5** | Claude Max 20x $200 | 5h window | **98% used** (measured) | — | $3.31 | $46 | **Yes** — strongest coder; plan already saturated (92 hook failures) |
| **GLM-5.3** | **Z.ai Plan Pro $80** | 12,000 cr ≈ 6.5 batches | 60,000 cr ≈ **2.3 sessions** | $80 flat | $2.91 (1,842 cr) | $40.78 (25,790 cr) | **Yes, with caveats** — best route |
| **GLM-5.3** | Z.ai API | unpublished | — | pay-as-you-go | $2.91 | $40.78 | Yes, with the same caveats; cheapest below ~2 sessions/mo |
| **GLM-5.3** | **OpenCode Go $10** | **$3 of $15** | $7.50 | $15 | **$163 of allowance** (4× measured) | **10.9× the monthly limit** | **No** |
| **GLM-5.3-Flash** | Go $60 allowance / Z.ai | $12 | $30 | $60 | $0.33 | $4.67 | **With caveats** — cheap second pass, slow, 5.4% contradictions |
| **Kimi K3.1 Pro** | Kimi Code Max $99 | rolling | none | pool unpublished | $2.61 | $36.59 | With caveats — vendor/independent gap |
| **Qwen3.8 Max** | Alibaba Plan Pro $50 | 6,000 req | 45,000 req | 90,000 req | $2.99 | $41.87 | With caveats — request-metered, "interactive only" |
| **Grok 4.7** | xAI API; SuperGrok $30 | unpublished | unpublished | — | **$10.77** at ≥200K | $150.77 | **No** — TB4 37.6%, 2.5× tokens |
| **Gemini 4 Argon** | AI Pro $19.99 / Ultra $199.99 | 5h refresh | weekly | — | $1.66 now / $2.45 from Dec 1 | $23.19 / $34.3 | **Yes** — already allowed, unmeasured in Mu |
| **DeepSeek V4.1 Flash** | Go $100/model | $20 | $50 | $100 | $0.08 | $1.08 | **No — excluded**: builder model, same family |

### 4.4 What the table means, honestly

1. **The plan is the product, not the tokens.** One audit-5 session is **$40.78 of GLM-5.3 list tokens** — under $180/month at one session a week. The **$80 Pro plan** carries that in **25,790 of 60,000 weekly credits (43%)** and **6,900 of 12,000 credits in a 5-hour block (58%)**, i.e. continuous auditing at Codex's token rate. **Lite ($18) cannot**: one batch (1,842 cr) nearly fills its 2,000/5h and 10,000/week buys 5.4 batches, a third of a queue. **Max ($168)** is only needed at ~5 sessions/week.
2. **The 11× uncertainty is the whole argument for the trial.** If GLM burns TB4's 31.5M tokens/task instead of Codex's ~9.8M/batch, the credit figures scale to **~19,600 credits/week for one session** — Pro then carries **3 sessions/month, not 2.3/week**, and Max becomes the real price. Nothing published settles this; one measured batch does.
3. **Codex's wall is the 5-hour window, not the week.** At 99% of a 300-minute window with 72% of the week left, the plan's shape — not its size — is what stopped audit-5. A GLM auditor on Pro has **1.7× headroom in the same 5-hour shape**, which is the direct fix.
4. **Claude is not spare capacity right now.** Weekly 98% on Nov 11 with a Nov 16 reset, and 92 commit-audit reviews killed by the limit, mean the Claude plan is already committed to the post-commit hook. Adding auditor work there would starve the hook, not the builder lane.
5. **What I could not measure:** OpenCode's token economy on Mu-style audit work; Z.ai's credit burn for one real batch; Claude's per-session token cost (no transcript accounting on this box; only the plan percentage); Codex's per-batch split inside the 137M (divided evenly across 14); Argon's audit behaviour (no report exists); whether Z.ai exposes a usage API for a watcher.

---

## 5. §4 The trial — one cheap, decisive measurement

**Trial T-A: three known-answer batches, blind, one day, ~$9.**

| Batch | Pin | The brief | The known answer | What it tests |
|---|---|---|---|---|
| **A1 — find a known defect** | the parent of `a641efa` | the audit-5 N1 brief (a promoting trade in `pipeline/league_office_mirror.py`) | audit-5 found that the promotion writes **virtual Roster** instead of canonical **Signed**, so the new active starter is missing from the same write's chart — CLOSED at `a641efa` per M-0632 | does it find the real bug at a real `file:line` |
| **A2 — find a known defect, different shape** | the parent of `26d1141` | the audit-5 stats3 R1/R2 brief (`engines/advanced_metrics.py`) | a truncated schedule must not read as a one-game week (2 games/10 catches/20 targets must not publish as 10/20/0.4), and a trade must not borrow the latest club's week (110 attempts / 0.1818, never 160 / 0.125) | does it produce a **numeric** known answer, not just a plausible one |
| **A3 — false-positive control** | `feb0c29` (docs + one eval line) | "audit this commit as you would a builder's fix" | **no findings** — the correct output is "nothing to fix", with what it checked and found fine | does it invent findings or `file:line` when there is nothing to find |

Score each batch on: (a) the known defect found, yes/no; (b) **every cited `file:line` resolves** (`grep -n` the path, check the line exists — mechanical); (c) findings that do not reproduce, count; (d) a runnable repro produced, yes/no; (e) could a builder fix everything in one pass from the report alone (JR/builder judgement); (f) measured wall time, tokens and **Z.ai credits**.

- **Cost:** 3 batches ≈ **$8.7 of GLM-5.3 list tokens** (~5,500 plan credits, 9% of a Pro week) on the API or an existing plan — no repo change, no launcher, no skill, no config. Two controls are free: A1 and A2 have **audit-5's actual reports as the reference answer** (`data/audit_reports/audit-5/n1/report.md`, `stats3/report.md`), and A3 has a null answer anyone can check.
- **Run it after the trial session has been told nothing about the answer.** The brief must not name the file, the function or the verdict — otherwise the trial measures reading comprehension, not audit.

**What must be true to trust the model as an auditor** (a gate, not a wish list):

1. **≥2 of 3** known defects found, with a repro for each.
2. **100% of cited `file:line` resolve.** One invented path or line fails the gate — the auditor's whole product is a citation a builder can act on.
3. **≤1 non-reproducing finding per batch** (A3 must be clean or say plainly what it checked and found fine).
4. **A report a builder can fix from in one pass** — the skill's own bar (§4.5), judged by the builder who receives it.
5. **Credit burn within 2×** the §4.3 estimate, so a plan can be sized before it is bought.
6. **Six hours without losing the pin or repeating itself** — A1+A2+A3 in one session is the cheapest version of that test.
7. **A quota watcher exists** before it is trusted unattended (§2.4).

---

## 6. §5 Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| **D1** | Run trial T-A (~$9, one day, three batches, no repo change)? | **Yes** — it is the only thing that turns §3 from inferred to measured, and it is cheaper than one Codex hour |
| **D2** | Which GLM route: Z.ai API for the trial, then Z.ai Coding Plan Pro $80 for steady state? | **Yes** — API is cheaper below ~2 sessions/month; Pro is the only plan that carries a 14-batch day inside its 5-hour window |
| **D3** | Is a GLM auditor allowed to read `CAREER-MUBASE` copies and open `mu_scratch_*` Postgres databases like the Claude/Codex auditor? | Needs your word before the trial; without it the trial can only read the tree |
| **D4** | Who is auditor of record for the save-writer, staff-contract and `league_office` batches? | **Keep Opus/Codex.** GLM first for docs, stats, mirror and single-file fixes |
| **D5** | Does a GLM session count as independent (it must never audit DeepSeek's *fixes* it wrote itself)? | It is a different family from DeepSeek, so **yes on the independence rule**; keep the existing "never audit your own work" rule, which is about the session, not the model |
| **D6** | Build the `glm` route (launcher, spawn case, skill, audit-skill lines, tests) after the trial passes? | **Yes, ~500 lines / 1–2 days**, smallest version first (~100 lines) if you want it this week |
| **D7** | Adjacent finding, not mine to change: the commit-audit hook has burned 92 Claude reviews on usage limits and is holding the weekly window at 98% | Worth a decision separately — moving the hook to a cheaper model would free the Claude plan for the auditor it was meant for |

**Not done, on purpose:** no launcher, no skill, no config, no `spawn_agent.sh` edit, no model pinned or switched, no behaviour commit, no CLOSED/OPEN verdict, no parked audit queue taken.

---

## 7. §6 Sources

**Measured in this repo / this session (2026-11-12):**
- `.claude/skills/audit/SKILL.md` (126 lines, read end to end); `tools/spawn_agent.sh`, `tools/opencode_session.sh`, `tools/muse_session.sh`, `tools/agent_board_inbox.py`, `tools/go_usage.py`, `tools/usage_check.py`; `.opencode/opencode.json`; `docs/reference/AGENTS.md`.
- Codex audit-5 rollouts, `~/.codex/sessions/2026/11/11/rollout-2026-11-11T10-15-37-01a1cb2d-c95f-7732-8424-6b0aa6688372.jsonl` and its 8 `thread_spawn` children: 137,160,607 input / 134,041,984 cached / 261,360 + 93,080 output; `rate_limits.primary.used_percent` 99 at 09:46:15Z with `secondary` 72; the usage-wall error and the 07:00:39 EST reset.
- `data/audit_reports/audit-5/` (14 reports, 969 lines, `STATUS.md`, `session.md`, per-batch logs and PASS counts); 32 `report.md` across 21 folders; `data/usage/rate_limits.json` (`seven_day.used_percentage: 98`, seen 2026-11-11 20:30 EST, resets 1794816000); `data/commit_audits/` 523 reports with 92 usage-limit failures.
- `python3 -m tools.go_usage` at 10:14 EST: 5h 3% (resets 18:50Z), weekly 37%, month 18%.
- Go model catalogue `GET /inference/go/v1/models` (30 ids): `glm-5.3` $1.40/$0.26/$4.40, 1M ctx / 131K out; `glm-5.3-flash` $0.15/$0.03/$0.50; `kimi-k3.1-pro` $2/$0.20/$10; `qwen3.8-max` $2/$0.25/$6; `grok-4.7` $2/$0.50/$6 (doubles ≥200K); `gemini-4-argon` $2/$0.10/$10 (×2 from 2026-12-01); `claude-haiku-5-5`, `gpt-6-luna`, `deepseek-v4.1-flash` $0.15/$0.003/$0.60 with a 2× peak multiplier.
- **Three short availability probes** (the only spend, $0.0017 total): `POST /inference/go/openai/v1/chat/completions` on `glm-5.3` (19 tokens then 410 tokens — 200 OK both, `billing_source: go`), `glm-5.3-flash` (200 OK) and `deepseek-v4.1-flash` (200 OK). The probes found **allowance ÷ list_cost = 4.00 for `glm-5.3`**, **1.00 for `glm-5.3-flash`**, **0.60 for `deepseek-v4.1-flash`**. No benchmark, no heavy Go pass, no Z.ai account (the repo holds only the OpenCode Console credential; `opencode auth list` shows one provider).
- `models.dev/api.json` (235 providers): provider `zai` — base `https://api.z.ai/api/paas/v4`, `glm-5.3` at $1.40/$0.26/$4.40; `moonshotai`, `alibaba`, `zhipuai` likewise.

**Published (read 2026-11-12):**
- Vals AI SWE-bench Verified leaderboard (updated 2026-09-01): GLM 5.3 95.40% / $0.34 / 958.22k tokens / 14m01s (rank 6); Kimi K3 93.40; GLM 5.3 Flash 92.00; Qwen 3.8 Max 85.60 / 2.74m / 41m42s; Grok 4.6 95.60; Gemini 3.8 Flash 80.00.
- Vals AI Terminal-Bench 4.0: Claude Opus 5 53.53, GPT-6.1 Sol 55.05 / $1.72 / 2.86m / 45m12s, Gemini 4 Argon 57.58 / $17.64 / 16.66m, GLM 5.3 38.89 / $9.37 / **31.54m** / 1h23m, Kimi K3 17.17 / 27.56m / 3h09m, Grok 4.6 17.17, Gemini 3.8 Flash 19.70.
- CodingFleet TB 4.0 public board: Opus 5.5 64.8% ±3.1, Sonnet 5.5 61.8, GPT-6.1 Sol 58.2 (statistical tie with Astra at $0.6k vs $3.3k run cost), GLM 5.3 41.8% inside Claude Code.
- BenchLM: GLM-5.3 rows (TB 2.1 88.2 vendor vs 71.5 Vals vs 83.9 AA; TB 4.1 41.2; DeepSWE 66.9; FrontierSWE 78.1; AA agentic index 53.4; coding index 74.8; **AA-Omniscience hallucination 38.9%**); GLM-5.3-Flash TB 2.1 84.3; Qwen3.8 Max SWE-bench 85.6, SWE-bench Pro 67.7, TB 2.1 86.6 (vendor); [coding plans compared](https://benchlm.ai/coding-plans) (24 plans, read 2026-10-16…11-11) for every plan price and meter in §4.2.
- Z.ai: [GLM Coding Plan overview](https://docs.z.ai/devpack/overview) — supported tools **include OpenCode**, 5-hour + weekly credits (Lite 2,000/10,000, Pro 12,000/60,000, Max 28,000/140,000), credit formula and multipliers (GLM-5.3: 6.9 / 1.7 / 24), estimated token allowance at 95% cache 48–97M (Lite) to 676–1,352M (Max) tokens/week; [usage policy](https://docs.z.ai/devpack/usage-policy) — "use limited to supported tools", no sharing, risk control. Z.ai pricing $1.40 / $0.26 / $4.40; Flash $0.15 / $0.03 / $0.50.
- OpenAI Codex pricing: Plus $20, GPT-6.1 Sol 15–160 local messages per 5 hours, weekly limits on top; API $2 / $0.10 cached / $10.
- Anthropic: Opus 5.5 $4 / $20, cache read $0.20, cache write $5 (5 m) / $8 (1 h), 1M context / 128K output; plans Pro $20 / Max $100 / Max 20x $200 / Ultra $200; SemiAnalysis (2026-10-05) values Max 20x at ~$11,726/month of Opus 5.5 agentic usage.
- Moonshot: Kimi K3.1 Pro $2 / $0.20 / $10 (Highspeed $4/$20), TB 4.0 ~65.7% vendor; Kimi plans Plus $19 / Pro $39 / Max $99 / Ultra $199, rolling 5-hour limit with no weekly cap on new plans, monthly pool unpublished; recognised in OpenCode as `kimi-for-coding`.
- Alibaba: Qwen3.8 Max $2 / $6, 1M context / 131K output / 262K reasoning budget; Model Studio Coding Plan Pro $50 with 6,000 / 45,000 / 90,000 requests and "interactive use only".
- xAI: Grok 4.7 $2 / $0.50 / $6 (<200K), $4 / $1 / $12 (≥200K), 500K context, TB 4.0 37.6% vendor, DeepSWE 71.0 vendor, Vals index #18/36.
- Google: Gemini 4 Argon $2 / $10 intro (doubles 2026-12-01), cache 95% off, 1M context / 65,536 output; AI Pro $19.99, AI Ultra $99.99 (5×) / $199.99 (20×), Antigravity rates; DeepSWE 77.9% vendor, TB 4.0 57.4–57.58, AA index 52.6.
