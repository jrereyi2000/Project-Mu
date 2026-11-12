# Audit queue inventory — what is stacked, how big it is, and what actually blocks P7

**Written 2026-11-12 by `deepseek-v4.1-flash (audit-queue-inventory)`**, against `main` at **`fe0c44e`**
(2026-11-12 15:54 EST). Read-only: no audit, no CLOSED / PARTIAL / OPEN verdict of my own, no code, no
database write, no Madden PC. See [board](../../AGENT_CONVERSATION.md) M-0684.

**Why this exists.** JR has Codex quota again and wants to know the size of the audit queue before spending it,
and whether Gemini 4 Hydrogen or a later Claude session should take part of it. This measures the queue; it does
not review it.

**Verdicts are quoted, never given.** Where an "open findings" cell names a verdict, it is another session's,
with the report path beside it (mostly `data/audit_reports/audit-5/*`). A DeepSeek-routed session never audits
(`/audit` is restricted to Opus 5.5, Codex and Argon).

---

## 0. Bottom line

| | Batches (audit-5 units) | Input tokens | Wall clock | Codex-Plus weekly limit |
|---|---:|---:|---:|---:|
| **The whole queue, all ten rows** | **33.3** | **~326M** | **~44 h** | **~171%** |
| Never reviewed and not started | 21.3 | ~208M | ~28 h | ~110% |
| Audited today, fixes now landing (R7) | 7.8 | ~77M | ~10 h | ~40% |
| Already reported at the pin (R1, R2, R9) | 4.2 | ~42M | ~5.6 h | ~22% |
| **The chain that actually gates P7 / checkpoint 1** | **~3.2** | **~31M** | **~4.2 h** | **~16%** |

1. **The queue does not fit in one Codex week.** At audit-5's measured burn (14 batches took the Plus plan to
   99% of a 5-hour window and 72% of a week), 33 batches is about **1.7 weeks** of Codex Plus, and the
   never-started remainder alone is about **1.1 weeks**.
2. **Only three rows gate checkpoint 1, and they are one chain** (`ded2627` → `e93e417` → `P3-04b`), about
   **3.2 batches / 4.2 h**. Everything else is P8 (articles, decision routing, mod checks), tooling
   (the two OpenCode plans) or analytics (stats). See §3.1.
3. **Two "unverified" rows are already reported, and were never delivered.** `26d1141` (stats3) and
   `07decd3` (stats2b) each have a report written at that exact pin on 2026-11-12 04:46 that no builder has
   seen. `ded2627` was reviewed on 2026-11-11 and *was* delivered: `e93e417` is the fix built from it.
   Re-auditing R1/R2 buys nothing — they need a builder fix and a 15% re-read. See §4.
4. **The three `/execute-plan` ranges overlap each other.** The fact-gate range handed over at M-0677 is
   **50 commits** and contains glm-unification's and visual-mod-check's builds. Read naively, those three rows
   cost **~50 batches** instead of ~18. Reviewing each plan's own commits only is the difference between
   ~326M and ~496M input tokens. See §3.4.
5. **Codex's current auditor spent its day on the most expensive row, not the blocking one.** It audited
   **visual-mod-check** (7.8 batches, 37% of what is left) and delivered six findings at M-0689, then took
   priority PC control for live acceptance of a checkpoint-2 feature (M-0692), while the P7 chain is still
   untaken. That is a defensible call for the CL JR assigned it — it is simply not the CL that gates checkpoint
   1, and at audit-5's rate it consumed a week's allowance before the blocking chain started.

---

## 1. The queue, one row per range

All ten builders are **`deepseek-v4.1-flash`** — verified commit by commit, not assumed: every commit in every
range below carries that trailer (`git log <range> --format=%B | grep '^Model:'`), so **Codex, Opus 5.5 and
Argon are all eligible auditors under the "auditor is not the builder's model" rule**, and so is any new route
(GLM 5.3, Gemini 4 Hydrogen) once it is trialled.

Legend: **R** = review units, defined in §2.2. "Own" = the plan's own files where a range carries other
sessions' commits. Every figure is `main` today.

| # | Range — what it is | Builder | Commits | `git diff --shortstat` | Files | Test files touched | `db/init_db.sql`? Migration? | JR sign-off | Findings already open on it | State |
|---|---|---|---:|---|---:|---|---|---|---|---|
| **R1** | **stats3** — per-game club identity, one-game isolation proof (audit-5 stats3 R1/R2). `26d1141` | deepseek | 1 | 6 files, +592 −112 | 6 | `test_advanced_metrics_coverage.py` (new, 253), `test_advanced_metrics.py` | No / No | Not on the fix. JR authorized the stats queue (M-0610/M-0617) | **A report exists at this exact pin**: `data/audit_reports/audit-5/stats4/report.md` — its own title reads *"Stats4 recheck — PARTIAL at 26d1141"*: S3/R1 named closed, R2a + R2b MEDIUM residuals. Never posted, never handed to a builder | Reported, undelivered |
| **R2** | **stats2b** — the legacy season-games bound is unknown history, not an observation (audit-5 stats2b R1). `07decd3`, merged `e8a89f2` | deepseek | 1 | 8 files, +322 −69 | 8 | `test_stat_counter_views.py` (80), `test_advanced_metrics.py` (41) | **Yes** `db/init_db.sql` (+27/−14) / **Yes** `2027_02_29_stats_counter_season_games.sql` (new, 139) — both applied | Not on the fix | **A report exists at this exact pin**: `data/audit_reports/audit-5/provenance/report.md` — *"Final migration provenance recheck — PARTIAL at 07decd3"*: the requested matrix passes, one residual (loader provenance on reload over an upgraded row). Never posted | Reported, undelivered |
| **R3** | **OPENCODE_DEEPSEEK_ROUTE_PLAN** — DeepSeek on OpenCode Go: launcher, model pin, `/context-check` source, skills. `dbdac5f^..8a972c0` (M-0618) | deepseek | 14 | 21 files, +1652 −64 | 21 | `test_context_check.py` (222), `test_opencode_session.py` (192), `test_spawn_agent.py` (167), `test_opencode_config.py` (150) | No / No | Plan approved by JR; M-0618 records §8 Q1/Q2 "settled as recommended". JR still owes one line outside the repo (`~/bin/mu`) | None — **never reviewed.** M-0629 ruled it outside audit-5's assigned queue; M-0626 says blocked on quota alone | Never reviewed |
| **R4** | **OPENCODE_GO_PIPELINE_PLAN** — the repo's own DeepSeek calls through Go (`tool_loop/`, dossier pilot) + allowance watchdog. `dc96309^..fff897f` (M-0628) | deepseek | 8 | 13 files, +1946 −44 | 13 | `test_openai_tool_loop.py` (329), `test_go_usage.py` (297), `test_dossier_pilot.py` (95) | No / No (`.env.example` only) | Plan approved by JR; JR's D1/D4 answers recorded in M-0628 | None — **never reviewed.** One flag from the builder: a `deepseek:<model>` vision backend now resolves to Go | Never reviewed |
| **R5** | **ARTICLE_FACT_GATE_PLAN** — award-race tool, `newsroom/fact_gate.py`, gate wired into `_execute_agent`, storage, API + newsroom view. `4abd2c9^..546b9fa` (M-0677) **+ `45e58d1`** (M-0679) | deepseek | 50 in range (22 are this plan's) + 1 | whole range: 44 files, +6986 −153. **Own: 20 files, +2081 −90**; `45e58d1`: 11 files, +107 −41 | 44 in range / 20 own | `test_fact_gate.py` (491), `test_swarm_fact_gate.py` (389), `test_db_tools_award_race.py` (184), `conftest.py` (13) | **Yes** `db/init_db.sql` (+9/−2) / **Yes** `2027_02_30_fact_gate.sql` (new, 33) — applied to `madden_mu2026` and `madden_test` at `45e58d1` on JR's word | JR approved the plan, kept `FACT_GATE_REWRITE` on (his decision), ordered the migration applied and the reading view built | None — **never reviewed.** Builder's own two questions in M-0677 (rewrite default; capture runs) are both answered/implemented by `45e58d1` | Never reviewed |
| **R6** | **GLM_5_3_UNIFICATION_PLAN** — `go` provider in `engines/llm_provider.py`, Agent Wire on Go, memory/press/inbox voice behind envs. `c70c714^..ec85c78` (M-0666) | deepseek | 23 in range (10 are this plan's) | whole range: 30 files, +3045 −148. **Own: 15 files, +1301 −41** | 30 in range / 15 own | `test_llm_provider_go.py` (384), `test_social_writer_go.py` (253), `test_agent_memory.py` (92), `test_inbox_voice.py` (88), `test_press_room.py` (28) | No own / No own (the `init_db.sql` + `2027_02_30` in this range are fact-gate's) | JR approved the plan; **deferred the model flip** to P8-06b (M-0671). No default flipped | None — **never reviewed.** Builder flags deviation (5) as *"worth an auditor's read"*: takes extraction was asking Gemini for a GLM id (behaviour, live before this build) | Never reviewed |
| **R7** | **VISUAL_MOD_CHECK_PLAN** — `tools/mod_check/`, `watch-game`, ten in-game catalog screens, the guide. `12485a6^..2087aae` (M-0677, same number, different plan) | deepseek | 33 in range (21 are this plan's) | whole range: 35 files, +5646 −176. **Own: 18 files, +4588 −20** | 35 in range / 18 own | `test_mod_check.py` (675), `test_mod_check_capture.py` (194), `test_madden_pilot_jobs.py` (444), `test_madden_pilot_nav.py` (148), `test_madden_pilot_spike.py` (57) | No / No | JR approved the plan, spike S-1, the `allow_space` guard and `watch-game`'s QUIT GAME policy (M-0674, M-0676) | **Audit delivered** by `gpt-6.1-sol (audit-visual)`: `data/audit_reports/visual_mod_check/report.md` — its HANDOFF (M-0689) says *"Visual-mod-check CL OPEN, six findings"*: F1–F3 HIGH (halftime always stops; static supersim can certify an arbitrary screen; stale kickoff evidence accepted), F4–F6 MEDIUM. 302 passed / 1 skipped at the pin. Builder signed off (M-0678), so fixes went to a fresh `visual-check-fixes` session (M-0690) | Audited; fixes starting |
| **R8** | **P3-04b** — calibrate the league-shape term on the full roster (JR's decision 2, M-0672). `2087aae..HEAD` = `46d1716` + merge `c82f13b` + spec `fe0c44e` | deepseek (builder-10) | 3 | 5 files, **+81 −54** | 5 | `test_progression_shape.py` (+33/−13), `test_progression.py` (+13) | No / No | **Yes** — JR's two decisions recorded at `7183151`: no constant applied; the league-shape calibration runs on the full roster | None yet. Builder-10's NOTICE is M-0684 (both new tests fail on parent `e1d36ca`; 66 + 12 passed; full offline 6,629 passed / 7 failed, 2 of them failing on `main` too) and it signed off in M-0685. The verdict is owed | Fix landed, awaiting verdict |
| **R9** | **league-shape fix** — stored `shape_season`, live projection drafts and exits. `ded2627` | deepseek | 1 | 5 files, +485 −83 | 5 | `test_progression_shape.py` (new, 266) | No / **Yes** `2027_02_26_league_shape_season.sql` (new, 19) — applied | JR ruling M-0441 folded into the fix brief (seed keying is a required change) | **A report exists**: `data/audit_reports/league_shape_fix/report.md` (2026-11-11, `claude-opus-5-5`) — its verdict table reads PARTIAL on both HIGH and MEDIUM, with F1–F6 findings. F1 is what `e93e417` fixed | Reported; residuals partly fixed |
| **R10** | **league-shape exit-draw fix** — key `shape_exits_and_rookies` on the projected season and the seed (F1). `e93e417`, merged `82966ca` | deepseek | 1 | 7 files, +265 −80 | 7 | `test_progression_shape.py` (170), `test_decision_compare.py` (12) | No / No (touches `db/seed_clean_db.py`, no schema) | JR's M-0441 ruling is the spec of the change | **No report anywhere**: `grep -rl e93e417 data/audit_reports/` is empty; the only mention is the archive note that it was built *"per report"* | Never reviewed |

### 1.1 File lists

**R1** `26d1141` — `engines/advanced_metrics.py` (+186/−81), `tests/test_advanced_metrics_coverage.py` (+253),
`tests/test_advanced_metrics.py` (+150/−29), `docs/reference/MADDEN_STATS_AND_ADVANCED_METRICS.md` (1/1),
`PROJECT_SPEC.md` (1/1), `docs/done/MADDEN_STATS_INGEST_PLAN.md` (1/0).

**R2** `07decd3` — `db/migrations/2027_02_29_stats_counter_season_games.sql` (+139/−20), `tests/test_stat_counter_views.py`
(+80/−20), `tests/test_advanced_metrics.py` (+41/−3), `engines/advanced_metrics.py` (+27/−5), `db/init_db.sql`
(+27/−14), `PROJECT_SPEC.md` (5/5), `docs/reference/MADDEN_STATS_AND_ADVANCED_METRICS.md` (2/2),
`docs/done/MADDEN_STATS_INGEST_PLAN.md` (1/0).

**R3** `dbdac5f^..8a972c0` — `docs/{in_progress,done}/OPENCODE_DEEPSEEK_ROUTE_PLAN.md` (+297/+300),
`tests/test_context_check.py` (+222/−1), `tests/test_opencode_session.py` (+192), `tools/context_check.py`
(+175/−18), `tests/test_spawn_agent.py` (+167), `tests/test_opencode_config.py` (+150), `tools/opencode_session.sh`
(+41), `.opencode/agents/repo-qa.md` (+31), `tools/spawn_agent.sh` (+28/−16), `PROJECT_SPEC.md` (+13/−5),
`.claude/skills/offline/SKILL.md` (8/5), `docs/reference/MADDEN_SPIKE_AGENT.md` (6/5),
`.claude/skills/context-check/SKILL.md` (6/4), `.opencode/opencode.json` (4), `.claude/skills/deepseek/SKILL.md`
(3/3), `docs/reference/AGENTS.md` (3/1), `CLAUDE.md` (2/2), `.claude/skills/{builder,execute-plan,audit}/SKILL.md`
(1/1 each). Its ten commits: `dbdac5f 4734688 35cbb9a c636b1d ed8e577 6d955e4 446ae14 5982ea3 c83e4e6 9906d8d
0906bb2 a58aad9 af089a1 8a972c0`.

**R4** `dc96309^..fff897f` — `tools/go_usage.py` (+366), `tests/test_openai_tool_loop.py` (+329/−3),
`tests/test_go_usage.py` (+297), `docs/{in_progress,done}/OPENCODE_GO_PIPELINE_PLAN.md` (+234/+244),
`newsroom/tool_loop/openai_loop.py` (+148/−12), `newsroom/tool_loop/go_allowance.py` (+118),
`tests/test_dossier_pilot.py` (+95/−2), `tools/dossier_pilot.py` (+63/−10), `newsroom/tool_loop/router.py`
(+30/−6), `PROJECT_SPEC.md` (+12/−8), `.env.example` (+6), `newsroom/tool_loop/README.md` (4/3). Its eight
commits: `dc96309 adac0cf 1976591 5840ef4 5df7d37 65bf2c4 e238c4e fff897f`.

**R5** own files — `newsroom/fact_gate.py` (+501), `tests/test_fact_gate.py` (+491), `tests/test_swarm_fact_gate.py`
(+389), `tests/test_db_tools_award_race.py` (+184), `newsroom/swarm_dispatcher.py` (+193/−21),
`newsroom/db_tools.py` (+125), `db/migrations/2027_02_30_fact_gate.sql` (+33), `newsroom/weekly_run.py` (+27),
`tools/article_factcheck.py` (+26/−67), `tests/conftest.py` (+13), `db/init_db.sql` (+9/−2), `PROJECT_SPEC.md`,
`docs/done/ARTICLE_FACT_GATE_PLAN.md`. Plus `45e58d1`: `Mu_Network/src/components/newsroom/ArticlePage.tsx`
(+28), `newsroom/swarm_dispatcher.py` (12/12), `server/api.py` (+10), `server/debug_api.py` (+6/−2),
`tests/test_swarm_fact_gate.py` (+31/−16), `Mu_Network/src/index.css` (+6), `Mu_Network/src/DebugPage.tsx`,
`NewsroomHomePage.tsx`, `Mu_Network/PROJECT_SPEC.md`, `PROJECT_SPEC.md`, the plan doc.
The range also carries R6's and R7's commits (see §3.4).

**R6** own files — `engines/llm_provider.py` (+194/−15), `tests/test_llm_provider_go.py` (+384),
`tests/test_social_writer_go.py` (+253), `newsroom/social_writer.py` (+37/−8), `tests/test_agent_memory.py`
(+92/−2), `tests/test_inbox_voice.py` (+88), `newsroom/agent_memory.py` (+65/−5), `engines/inbox/voice.py`
(+16/−3), `newsroom/social_publish.py` (+15/−2), `newsroom/press_room.py` (+15/−1), `newsroom/press_agents.py`
(5/4), `engines/gm_llm.py` (+12/−1), `tests/test_press_room.py` (+28),
`docs/reference/GLM_UNIFICATION_MEASUREMENTS.md` (+97), `docs/done/GLM_5_3_UNIFICATION_PLAN.md`, `PROJECT_SPEC.md`.
Ten own commits: `c70c714 4adc8ef 838a058 5a43ff0 6f7abe8 629bf47 80ec761 effd0a7 f946d20 ec85c78`.

**R7** own files — `tools/mod_check/runner.py` (+901), `tests/test_mod_check.py` (+675), `docs/reference/MOD_CHECK.md`
(+465), `tools/madden_pilot/jobs.py` (+462/−2), `tests/test_madden_pilot_jobs.py` (+444), `tools/mod_check/segments.py`
(+322), `tools/madden_pilot/catalog.json` (+265), `tools/mod_check/__main__.py` (+197), `tests/test_mod_check_capture.py`
(+194), `tools/mod_check/report.py` (+168), `tests/test_madden_pilot_nav.py` (+148), `tools/madden_pilot/__main__.py`
(+138/−15), `tools/madden_pilot/spike.py` (+93/−3), `tests/test_madden_pilot_spike.py` (+57), `tools/mod_check/__init__.py`
(+42), `docs/reference/{MOD_CHECK,MADDEN_PILOT,MADDEN_SPIKE_AGENT}.md`, `docs/done/VISUAL_MOD_CHECK_PLAN.md`,
`PROJECT_SPEC.md`.

**R8** `2087aae..HEAD` — `engines/progression.py` (+20/−25), `engines/tuning/progression_fit.py` (+11/−12),
`tests/test_progression_shape.py` (+33/−13), `tests/test_progression.py` (+13), `PROJECT_SPEC.md` (+4/−4).

**R9** `ded2627` — `tests/test_progression_shape.py` (+266), `engines/progression.py` (+178/−18),
`db/migrations/2027_02_26_league_shape_season.sql` (+19), `engines/tuning/progression_fit.py` (+18/−61),
`PROJECT_SPEC.md` (4/4).

**R10** `e93e417` — `tests/test_progression_shape.py` (+170/−15), `engines/progression.py` (+53/−43),
`engines/tuning/progression_fit.py` (+17/−8), `tests/test_decision_compare.py` (+12/−5), `db/seed_clean_db.py`
(+7/−5), `PROJECT_SPEC.md` (4/3), `tools/offseason_check/timelines.py` (2/1).

### 1.2 What the ranges touch, safety-wise

- **Batches a wrong verdict would cost the most** (save writer, staff contracts, coach save-writes,
  `league_office`): **none of the ten rows**. The only DB-schema changes are R2's and R5's, both already
  applied, and both are additive (a migration row, provenance columns, two `news_feed` columns).
- **Live-by-default behaviour:** R5's gate is **on** (`FACT_GATE_REWRITE` on, JR's decision) and runs on every
  article; R6 flipped **no** default, but its takes-extraction reroute is live; R9/R10/R8 are the engine that
  the first offseason runs.
- **Applied migrations:** `db/migrations/` now holds **145** files (R5's `2027_02_30` is the head). M-0679
  records **145 applied / 0 pending** on `madden_mu2026` and `madden_test`; `CLOSE_OUT_STATUS.md` §Appendix B
  still says 144, written before that.

---

## 2. How the estimate was made — and that it is an extrapolation

**This is an extrapolation, not a measurement of these ten ranges.** It takes one measured day (audit-5) and
scales it by the size of each range. It cannot know how many bugs a range actually contains: a clean 5,000-line
plan can cost less than a dirty 300-line engine change, and audit-5's own day contained both.

### 2.1 The measured anchor

From Codex's own session logs for **audit-5**, measured by `auditor-model-eval` (M-0656,
[`AUDITOR_MODEL_EVALUATION.md`](AUDITOR_MODEL_EVALUATION.md) §4.1):

| | audit-5, 2026-11-11 10:15 → 2026-11-12 04:46 EST |
|---|---|
| Batches | **14** (1 parent, 8 batch subagents, plus guardians) |
| Input tokens | **137.2M**, of which **134.0M cached** (2.3% uncached) |
| Output tokens | 261K + 93K reasoning |
| Wall clock | **18.5 h** |
| Per batch | **9.8M input** (9.57M cached) + ~25K output, **1.32 h** |
| Plan cost | 99% of the 5-hour window, **72% of the weekly Plus limit** |
| Evidence style per batch | pinned worktree + own `mu_scratch_*` DB, real PostgreSQL repros, a parent-failure proof, targeted suites (25–176 tests) |

### 2.2 The scaling unit

A batch's size is measured in **review units**: `R = changed lines + 120 × (files − 1)`. The 120 is the cost of
opening, tracing and closing a file that is not the one the diff points at. The reference batch was taken from
the four audit-5 batches whose fix I could size exactly:

| audit-5 batch | pin | files | changed lines | R |
|---|---|---:|---:|---:|
| stats3 | `c9d8ccb` | 5 | 587 | 1,067 |
| stats2b | `8fe576b` | 5 | 184 | 664 |
| league-shape fix | `ded2627` | 5 | 568 | 1,048 |
| mirror N1 residual | `7d5b451`+`be04a8d` | 5 | 153 | 633 |
| **reference** | | | | **≈850** |

So **1 batch ≈ 850 review units ≈ 9.8M input tokens ≈ 1.32 h**. Per row: `batches = R / 850`,
`tokens = batches × 9.8M`, `hours = batches × 1.32 h`.

### 2.3 What it assumes — stated plainly

1. **One pass.** The auditor reads the diff, traces each changed symbol to its callers (**≈3 files read per
   changed hunk**), runs the plan's own targeted tests once, and writes the report. No second reading of the
   whole range.
2. **One repro per finding, not per hunk**, written the way audit-5 wrote them (a real-PostgreSQL fixture plus a
   parent-failure proof). A repro suite per hunk would add roughly 20–30%.
3. **One re-read after fixes** is **not** in the per-row figure. Budget **+15% per row** for the pass that
   verifies a builder's fix — that is the "one re-read" JR asked about. R1, R2 and R9 in particular are *only*
   a re-read now.
4. **Caching like audit-5's** (98% cached). These are agent-loop input tokens — the same file read dozens of
   times — not unique text. A route with a different cache policy will not match, which is exactly the
   11× uncertainty band `AUDITOR_MODEL_EVALUATION.md` §4.4 flags.
5. **Plan-own scope.** For R5, R6 and R7 the figure counts only the plan's own files. Reading each handed-over
   range wholesale is costed separately in §3.4.
6. **One auditor session, one machine**, batches mostly serial with ≤3 subagents, like audit-5.
7. **Linear scaling.** Cost per unit actually grows a little with session length (the transcript is re-sent each
   step). If growth is superlinear, multiply the four big rows (R3–R7) by up to **1.5×**; the total then reaches
   **~45 batches / ~440M / ~60 h**. Treat that as the upper end of the band, not the plan.

### 2.4 Per-row estimate

| # | Row | R | Batches | Input tokens | Wall clock |
|---|---|---:|---:|---:|---:|
| R1 | stats3 `26d1141` | 1,304 | 1.5 | 15M | 2.0 h |
| R2 | stats2b `07decd3` | 1,231 | 1.5 | 14M | 1.9 h |
| R3 | route plan `dbdac5f^..8a972c0` | 4,116 | 4.8 | 48M | 6.4 h |
| R4 | go pipeline `dc96309^..fff897f` | 3,430 | 4.0 | 40M | 5.3 h |
| R5a | fact-gate own `4abd2c9^..546b9fa` | 4,451 | 5.2 | 51M | 6.9 h |
| R5b | fact-gate follow-up `45e58d1` | 1,348 | 1.6 | 16M | 2.1 h |
| R6 | glm-unification own `c70c714^..ec85c78` | 3,022 | 3.6 | 35M | 4.7 h |
| R7 | visual-mod-check own `12485a6^..2087aae` | 6,648 | 7.8 | 77M | 10.3 h |
| R8 | builder-10 P3-04b `2087aae..HEAD` | 615 | 0.7 | 7M | 1.0 h |
| R9 | `ded2627` | 1,048 | 1.2 | 12M | 1.6 h |
| R10 | `e93e417` | 1,065 | 1.3 | 12M | 1.7 h |
| | **Total** | **28,278** | **33.3** | **~326M** | **~44 h** |

Converted to Codex Plus at audit-5's rate (14 batches ≈ 72% of a week): **171% of a week**, i.e. about two
calendar weeks of Codex Plus at one auditor. The queue does not fit in the week JR just got back.

### 2.5 Where the estimate could be badly wrong

- **R7** is measured: it came back with **six findings, three HIGH** (M-0689), so a noisy row costs the top of
  the band, not the middle. The 10 h figure was right for the review; it does not include the fix round.
- **R5** was handed over *after its own pre-build check (R1) failed, was fixed, and then needed a coverage
  fix* (plan §3.1/§3.2, waves U4/U5). A reviewer re-running that check is closer to two batches than one.
- **R3/R4** are mostly documentation, shell and skill files. They may audit far cheaper than their line count —
  the 120-per-file factor may overcharge 21 files of prose. If so, that is ~4 batches refunded, not more.
- **R1/R2/R9** are already reported. Their 4.2 batches are in the total for completeness; in practice they are
  **+15% re-reads**, or zero until a builder touches them.

---

## 3. Reading the queue as JR asked

### 3.1 Rank: what blocks P7 / checkpoint 1, and what can wait

**Blocks checkpoint 1 (P7's first franchise start): one chain, three rows, ~3.2 batches / ~4.2 h.**

Why these and nothing else: P7's rows are about *starting* the franchise (`P7-04` first `league_office setup`,
`P7-05` seed verification including every feature `active`, `P7-06` week 1 end to end, `P7-07` tuning closed).
The only queued code in that path is the progression / league-shape engine that the first offseason runs:

1. **R8 — builder-10's P3-04b (`46d1716`).** It changes what the **live** engine does (the p2g copy moves
   from `(1.0, 1.5)` to `(1.0, 1.25)`), makes the fit ~8× slower, and rewrites a shared constant so live and fit
   cannot drift. Nothing has read it. It is also the smallest row in the queue (+81/−54): one test can be read
   in an hour.
2. **R10 — `e93e417`.** The exit/rookie draw now keys on the projected season and the seed. Every projective
   number in `PROGRESSION_FIT_2026-11-12.md` was produced on this code, and no report exists at this pin.
   A wrong verdict here invalidates P3-04a's five-seed result, which P5-06 will tune against.
3. **R9 — the `ded2627` residuals.** A report exists (PARTIAL, F1–F6); what is open is only whether the fixed
   tree answers it. That is a re-read, not a fresh audit.

**Needs a verdict before the first live media slate, but not before the franchise start: R5 (fact-gate).**
The gate is **on by default** and rewrites articles that it judges contradicted. `P7-06` runs week 1 through
the media step, so the first live slate runs this code. It is also the highest-stakes fresh read in the queue
(writer path + a DB migration + frontend), which is why it is listed for Codex below rather than for a cheap
lane.

**Can wait — nothing in checkpoint 1 depends on it:**

| Row | Why it waits | Checklist rows it serves |
|---|---|---|
| R6 glm-unification | No default flipped; every site still Gemini until JR's P8-06b decision | P8-06, P8-06b |
| R3 + R4 route plans | Launcher/skills/tooling. No pipeline path and no database. If they are wrong, sessions misbehave, not the franchise | P8-06 plumbing, P0-4 |
| R7 visual-mod-check | PC-bound mod testing, checkpoint 2. Its audit is delivered (six findings, M-0689); fixes and a live acceptance run follow | P8-12, P8-13, P8-14 |
| R1 + R2 stats | Analytics and newsroom numbers; already reported at the pin | — (feeds §2.23 metrics, no P7 row) |
| R9's F2–F6 | Only if P5's shadow seasons need the fit's exits to be provably right; they do not gate the start | P3-04, P5-06 |

**No P1 row depends on the queue at all.** All 27 P1 rows are ticked except `P1-11` (scouting directors), and
that one is a JR review action on a `staff:scout` batch, not a code audit. The queue's product surface is
P3/P5/P7 (progression, league shape) and P8 (articles, decision routing, mod checks). So "close the code before
the programme starts" is **not** waiting on an auditor, with the single exception of the league-shape chain.

### 3.2 Cheap rows vs expensive plans

**Cheap — one file, one commit, or one line of behaviour (~6.2 batches together, 19% of the queue, and
4.2 of those are the two rows that are already reported):**

| Row | Size | Why it is cheap |
|---|---|---|
| **R8** P3-04b | +81/−54, 5 files, 3 commits | Two engine files and two test files; the change is one constant's call path |
| **R9** `ded2627` | +485/−83 | Previewed by an existing report; the work is "does the fixed tree answer F1–F6" |
| **R10** `e93e417` | +265/−80 | One function's keying, plus re-derived pinned test values |
| **R1** `26d1141` | +592/−112 | Already reported at the pin; a builder fix + short re-read |
| **R2** `07decd3` | +322/−69 | Same; one residual (loader provenance) |

**Expensive — multi-thousand-line plans (~27.1 batches, 81%):** R7 (4,588 own lines, +1,518 lines of tests),
R5 (2,081 + 1,348 own lines, 4 build waves), R3 (1,652 over 21 files, 731 lines of tests),
R4 (1,946, 721 lines of tests), R6 (1,301 own lines, 845 of tests). Two rules of thumb the numbers show:

- **A plan costs ~3–4× its own diffstat** once tests and docs are read and the call sites traced.
- **Test volume is half the row.** R7 and R5 are each ~1,000 lines of tests; an auditor who skips them is not
  auditing the thing that proves the change.

### 3.3 Who can take what (the auditor must not be the builder's model)

Every builder here is `deepseek-v4.1-flash`, so **the rule excludes only DeepSeek and Muse** (Muse is
builder/computer-use only and never audits). All four of the following are eligible:

| Model | Can take it in practice? | Best fit |
|---|---|---|
| **GPT-6.1 Sol (Codex)** | **Yes — it is what is running now** (`audit-visual`, M-0685). Highest per-batch burn, so spend it on the rows where a wrong verdict is expensive | **R8 + R10 + R9** (the P7 chain, ~3.2 batches) and **R5** fact-gate. Nothing else |
| **Gemini 4 Argon** | Yes as a role, **but no Argon audit report exists yet** (`AUDITOR_MODEL_EVALUATION.md` §4.3) — its first job should be a row whose answer is checkable | **R3 + R4** (~8.8 batches): shell, skills, docs, and the 46 + 105 tests the builders already ran — mechanical to check, low product risk. Then the **R1/R2 re-checks**, where audit-5's repros are a free reference answer |
| **Claude Opus 5.5** | **Not now.** Weekly window read 98% on 2026-11-11 20:30, resets **Nov 16 03:00**; `data/commit_audits/` holds 92 reviews killed by Claude's usage limits | Nothing before the reset. After it: the rows Argon did not take, or the P5 shadow-season reviews |
| **Gemini 4 Hydrogen** | **Measured today and not adopted as auditor of record**: [`GEMINI_4_HYDROGEN_EVALUATION.md`](GEMINI_4_HYDROGEN_EVALUATION.md) (`fe8eeff`) puts it mid-pack on Terminal-Bench 4.0 (54.0%, #11/64), **cheapest of the candidates to run** (~$1.25 a batch / ~$17.50 a session, inferred, and $0 marginal on JR's Antigravity plan), **absent from the Go allowance**, with **no local quota probe** and no EQ-Bench entry | **Not a queue row until one blind trial passes.** The trial is written down (that doc §3.4): H1 re-audit of the mirror N1 defect at `26d1141` = `a641efa^`, H2 the fact-gate evidence-window defect at `8cfa931`, H3 `feb0c29` as a clean control — ~$1.25, and both known answers exist. If it passes, it takes **R3/R4 and the R1/R2 re-checks**, which is the same block Argon would take |

**Concretely, the split this inventory supports:** Codex takes the 3.2-batch P7 chain **now**, before it spends
another ten hours on visual-mod-check. Argon (or Hydrogen, if its one blind trial passes first — today's
evaluation prices that trial at ~$1.25) takes the two route plans and the stats re-checks. Codex, Argon or a
post-reset Opus takes fact-gate and glm-unification afterwards. That
puts checkpoint 1 clear inside **one Codex 5-hour window** (~4.2 h of work, ~16% of the weekly limit, since
14 batches was 99% of a window), and leaves the ~28-batch remainder to lanes that are not the scarce one.

### 3.4 Two traps in the ranges as handed over

**Trap 1 — the ranges overlap.** `4abd2c9^..546b9fa` is 50 commits and contains **glm-unification's and
visual-mod-check's** builds (M-0677's handoff lists the foreign shas itself: `838a058`, `ec85c78`, `593ddf2`,
`17416f2`). `c70c714^..ec85c78` and `12485a6^..2087aae` likewise carry fact-gate's and each other's. A reviewer
who reads each range wholesale pays for the same commits up to three times:

| Row | Plan-own | Range wholesale | Overhead |
|---|---:|---:|---:|
| R5 fact-gate (+`45e58d1`) | 6.8 batches | 16.1 | **+9.2** |
| R6 glm-unification | 3.6 | 7.9 | **+4.3** |
| R7 visual-mod-check | 7.8 | 11.6 | **+3.8** |
| | 18.2 | **35.6** | **+17.4 batches ≈ +170M tokens** |

So: review each plan's own commits, and have **one** reviewer confirm that the foreign commits inside a range
were already covered elsewhere. That single instruction is worth ~17 batches — more than the entire P7 chain.

**Trap 2 — three rows are re-reads dressed as audits.** R1 and R2 have reports at their exact pins, written
four minutes apart at 04:46 on 2026-11-12 and never delivered; R9 has a report too. If JR sends an auditor at
those shas, it will pay 4.2 batches to reproduce verdicts that are already sitting in `data/audit_reports/`.
The correct next step for them is a builder and a 15% re-read.

---

## 4. Corrections to the close-out record (facts, not verdicts)

These are places where the documents and the files on disk disagree today. I changed none of them (the
checklist and `CLOSE_OUT_STATUS.md` are not mine to edit).

1. **`CLOSE_OUT_STATUS.md` §"Known-unverified: the blocked audit queue"** lists stats3 `26d1141` and stats2b
   `07decd3` as having "No audit verdict". **Both have a report at that exact pin**:
   `data/audit_reports/audit-5/stats4/report.md` (11,254 bytes, mtime 2026-11-12 04:46) and
   `data/audit_reports/audit-5/provenance/report.md` (10,883 bytes, mtime 2026-11-12 04:46). audit-5's last
   board post is M-0632 and its `STATUS.md` still describes the earlier pins, so the reports were written and
   the session ran out before it could hand them over. Nothing in them has been given to a builder.
2. **The same section, and M-0671/M-0629/M-0672, call `ded2627`/`e93e417` "un-audited".** `ded2627` **was**
   reviewed: `data/audit_reports/league_shape_fix/report.md` (2026-11-11, `claude-opus-5-5`), whose verdict
   table is PARTIAL on both findings with F1–F6. What has no report is **`e93e417`** — the fix built from that
   report. `grep -rl e93e417 data/audit_reports/` returns nothing. So the chain is audited at its first step
   and unverified at its second, which is the opposite of what a reader of the docs would conclude.
3. **`CLOSE_OUT_STATUS.md` Appendix B says "144 migrations".** `db/migrations/` holds **145** today (the head is
   `2027_02_30_fact_gate.sql`), and M-0679 records 145 applied / 0 pending on `madden_mu2026` and
   `madden_test`. The status doc predates that commit.
4. **M-0672 says "no auditor session is up".** One is (`gpt-6.1-sol (audit-visual)`, M-0685/M-0686), and it has
   taken R7 first rather than the P7 chain.
5. **`PROGRESSION_FIT_2026-11-12.md:148`** says a review of `ded2627`/`e93e417` "is still owed". Same split as
   (2): owed for `e93e417`, already delivered for `ded2627`.

Not corrected here, flagged for the owner of each doc: `CLOSE_OUT_NEXT_STEP.md`'s proposed rows P3-04a/P3-04b
predate builder-10's `46d1716` landing, so its status line is now behind the tree.

---

## 5. Reproduce any number here

```bash
# size, commits, files and trailer for one row
git log --oneline <range> | wc -l
git diff --shortstat <range>
git diff --numstat <range> | sort -k1 -nr
git log <range> --format='%B' | grep '^Model:' | sort | uniq -c

# plan-own size for a mixed range (R5/R6/R7): pass only the plan's paths after `--`
git diff --numstat c70c714^..ec85c78 -- engines/llm_provider.py newsroom/social_writer.py ...

# the scaling unit
python3 -c "R=lambda i,d,f: i+d+120*(f-1); print(R(2081,90,20)/850*9.8)"   # 5.24 batches
```

---

## 6. Wall clock if JR parallelises it hard

**Subagents do not create quota. They only shorten the wall clock of a fixed token budget**, so the answer has two
parts: how fast the *work* goes, and how fast the *week's allowance* drains.

### 6.1 The work side

One batch is one pinned worktree, one scratch DB and one subagent reading a range — independent of every other
batch. The measured wall clock per batch is **under an hour for a capable agent on a big row**: `audit-visual`
reviewed the largest range in this queue (R7, 6,648 review units, 302 tests) between its M-0685 notice at 15:55
and the report at `data/audit_reports/visual_mod_check/report.md` 15:58, with the tests themselves taking 10 s.
The 1.32 h/batch in §2.1 is therefore a **token** measure, not a reading-speed measure — audit-5's day was
quota-throttled, not attention-throttled.

At **8 concurrent batch subagents** (audit-5's own peak shape: 8 `thread_spawn` children plus the parent), the
ten rows run as **one wave**, and the wall clock is set by the slowest row (fact-gate, R5) rather than by the sum:
**~2–3 h of agent time for all 33.3 batches**, against 44 h one-deep. Nothing in the audit skill caps concurrency;
the binding practical caps are the Postgres cluster and the parent session.

| Non-scaling constraint | Why it caps | What it means |
|---|---|---|
| Postgres cluster | Each DB-backed batch clones a template (`madden_tune_p2g` alone dumps at 120 MB; 4 of 11 GB RAM in use) | **≤3–4 DB-backed batches at once.** Only R8, R9, R10 and the R1/R2 re-checks need a DB; the four plan reviews do not |
| The parent session | It reads every report and writes one fix brief per batch, serially (~10–15 min each) | ~2 h of parent time across ten reports; overlaps the subagents, but it, not the agents, is the tail |
| The fix → re-check loop | Findings go to a builder, come back as a fix, and the auditor re-reads at the new sha | One extra round, +15% per row; the builders are DeepSeek on Go (8% of a 5-hour window, 44% weekly), so **the fix side costs no Claude quota** |
| The skill's own 85% rule | At 85% of the 5-hour window the auditor posts a hand-off and stops | Max parallelism reaches that line in **~1.5–2 h**, not 5 |

### 6.2 The quota side — the only number nobody has

Claude's per-session token cost has never been measured on this box (`AUDITOR_MODEL_EVALUATION.md` §4.2: "only
the plan percentage"). The queue needs **~326M input tokens**, which is **2.4× audit-5's whole day** (137.2M, which
was 99% of a 5-hour Codex window and 72% of a Codex week). So the calendar answer is a function of what a Claude
week holds, and only JR's console knows:

| If a Claude week carries | The queue takes | First useful result |
|---|---|---|
| **≥ ~33 batches (≥ ~326M)** — the whole queue fits | **All ten audited in one ~3 h wave**; fixes the same evening on Go; re-check next window. **~1 day end to end** | P7 chain verdict in the first hour |
| **~14 batches (~137M, one audit-5 day)** | **~2.4 windows ≈ 10–12 h of clock time**, two or three waves across a day and a half | P7 chain in wave 1 (~2 h) |
| **~7 batches** | **~5 windows ≈ 2–2.5 days**; the P7 chain is one window on its own | P7 chain only, rest waits |

### 6.3 The order to launch in (8 slots, first wave)

Quota is fixed, so the order *is* the decision — the first wave gets the whole window:

1. **R8** P3-04b `2087aae..HEAD` — the live engine change; smallest row, highest leverage.
2. **R10** `e93e417` — the exit-draw fix nothing has read.
3. **R9** the `ded2627` residual re-read — a re-read, ~30 min.
4. **R5a** fact-gate own commits (highest-stakes fresh read: writer path + migration + frontend).
5. **R5b** `45e58d1` (API/view + the capture exclusion) as its own batch.
6. **R3** the DeepSeek route plan (~8.8 with R4: the safest block for a first non-Codex auditor).
7. **R4** the Go pipeline plan.
8. **R6** glm-unification.

**Hold back:** R1 and R2 (re-reads against reports already on disk — the parent can do both inside the same
window for ~10% of their estimate), and R7 (already audited; its six fixes are landing now).

**Second wave, next window:** the re-checks of whatever the builders fixed, then whatever is left of R3/R4/R6.

### 6.4 What to do in the three hours before the quota lands

The prep is the part that makes a 3 h wave possible instead of a 6 h one, and none of it spends quota:

1. Ten pinned worktrees, one per row, at the exact shas in §1 — `git worktree add ... <sha>`.
2. Ten briefs written from §1's table (range, plan path, what the builder claimed, the known open questions,
   the tests to run, the report path). This is the parent's serial work; doing it in advance removes it from the tail.
3. Three scratch DBs pre-cloned (`p2g` template, `2036`, fresh `init_db.sql`) for R8/R9/R10.
4. Pause or accept the `commit_audit.py` hook: its headless Claude reviews have already failed 92 times on Claude
   usage limits, and a burst will starve it again.

### 6.5 Honest caveats

- **Parallelism multiplies the burn, not the output.** Eight subagents burn a 5-hour window in about a fifth of
  the time one does. If the week's allowance is one audit-5 day, max parallelism finishes the same 14 batches in
  ~2 h and then sits idle for 3 h — it does not add work.
- **The 2–3 h wave figure assumes no re-reads.** With the fix round, count a second, shorter wave.
- **One observation, not a benchmark:** "R7 in under an hour" is one agent on one range today. A model with a
  sloppier tool loop will be slower, and the estimate band in §2.5 still applies to the tokens.

---

## 7. What I did not do

No audit, no CLOSED / PARTIAL / OPEN verdict of my own, no commit-range review, no correctness claim about any
row, no test run beyond reading the counters the builders reported, no database write, no `madden_*` query, no
Madden PC, and no edit to `PROJECT_SPEC.md`, `docs/CLOSE_OUT_CHECKLIST.md`, `docs/reference/CLOSE_OUT_STATUS.md`
or any range I describe. The board is read; my messages are M-0684 and M-0694.
