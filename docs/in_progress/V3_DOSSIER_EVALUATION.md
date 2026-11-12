# V3 dossier evaluation — 2026-10-30

Original assessment (superseded in part by the pilot addendum below): retain ReAct as production baseline. Dossiers are promising evidence packaging, but this prototype cannot yet establish factual/editorial parity or savings. No model calls, publication, migrations, or archive writes were performed.

## Checks and artifacts

Canonical WSL checkout `/home/jrereyi/Project-Mu`. Archive `madden_2036`; every probe connection overrides libpq options to `default_transaction_read_only=on`. `tools/dossier_offline_eval.py` requires an explicit DSN before importing newsroom modules, so dispatcher, tools, and brief use the same database. It does not call generation/publication/memory-write routines. It exports three real-writer diagnostic packets plus SHA-256 manifest in ignored `verify_output/v3_dossier_offline/`. Preserve that directory to retain this exact evidence version; rerunning overwrites it with current inputs.

Verified archive SQL counts: 2036 ARTICLE=320, TWEET=1,876; each spans 18 distinct weeks. Two seasons at that volume means 640 articles and 3,752 tweets, not a measured compute budget. No archive token-total claim was verified.

Original dry run: 2036 W12 CAR/MIN, 165.51 ms dossier compilation, 13,439 dossier characters, 17,257 macro-brief characters, 26,984 system characters, 54,096 B task characters. These are characters, not tokenizer counts or GPU measurements. The displayed 40 tools is hardcoded; actual list has 46.

Focused tests: `tests/test_run_context.py tests/test_agent_memory.py tests/test_league_memory.py`: **30 passed**, 13 dependency deprecation warnings. No live factual/editorial A/B verdict follows from these tests.

Reproduce from WSL (DSN shown contains only the existing local development credentials):

```bash
cd /home/jrereyi/Project-Mu
DATABASE_URL='host=localhost dbname=madden_2036 user=postgres password=postgres' .venv/bin/python -m tools.dossier_offline_eval
.venv/bin/python -m pytest tests/test_run_context.py tests/test_agent_memory.py tests/test_league_memory.py -q
```

## Evidence and continuity findings

- `run_dossier_ab_test` builds the shared brief and copies the full compiled system prompt to B. Macro context is retained, but so are research/tool instructions that contradict B's no-tools instruction. Make availability-specific instructions explicit while preserving persona/editorial rules.
- Harness never enters `article_run_context`; the production helper contract uses it to hide publication-week/future games. The probe found CAR efficiency changes with context at W2 and W12, demonstrating a material leakage risk; W21 was unchanged. Merely passing season/week to compiler is insufficient.
- `run_context.py` explicitly warns that season totals, popularity, ratings and team-season records lack weekly history. Even correct context does not recreate an old database snapshot. Historical roster/contract/coach/transition evidence requires source-by-source cutoff review. Use an actual as-of snapshot, or label/remove latest-only fields equally in both arms; do not present these diagnostic packets as historically clean.
- `compile_agent_prompt` already supplies evolving memory, graded predictions, real staff directory, persona signals, recent-work/signature constraints. Production agent IDs matter. Original harness uses `beat_car`, invented persistent memory and generic display name rather than archive `Panthers_Beat_1`; both arms can match each other while missing actual continuity.
- Six dossier helpers cover edges, efficiency, staff and injuries; game helper covers box score and analytics. They do not exhaust the 46-tool research space: player/career/contract history, league memory, previous articles/predictions, transitions and family-specific evidence need an explicit coverage map. Preserve unknown-versus-zero distinctions and observed-versus-inferred game scripts.
- Helper errors are strings and can be embedded as evidence; reject/flag missing sections rather than declaring a complete dossier. No immutable franchise identity, source-level versions, schema validation, or missing-data contract exists in original harness.
- Audit checks H1/excerpt, clichés, internal symbols and counts. It cannot detect wrong records/scores, misidentified players, invented quotes/reporting access, unsupported tactics, chronology or award errors. Its `pass` means structural checks only.

## Same-model experiment protocol (ready to implement after blockers)

Do not invoke the original live CLI as the acceptance experiment. It strips A's `:low`, whereas B uses provider thinking configuration; A uses a raw client without the usual usage meter, with potentially incomplete AFC usage. It ignores A error status and may still spend on B after A fails. B provider may log usage; do not run either live arm against the archive. Cost estimates omit thinking/cached billing distinctions and failures. No current model availability/pricing was verified.

1. Restore a disposable database from a frozen appropriate snapshot. Record franchise identity, snapshot hash, commit, config hashes, writer/persona/memory versions, publication horizon and permitted source visibility. Pin all helper/dispatcher connections to it before imports. Disable publication and memory writes; isolate usage logging. Freeze fetched evidence/tool responses or guarantee unchanged snapshot across both arms.
2. Correct harness: same normalized available model ID, identical explicit thinking level, temperature 0.7, output limit and retry policy in both arms; instrument all calls using the same meter. Enter article context around brief, prompt, dossier and tool execution. Fail paired trial on provider error/empty output; retain failures in cost/yield denominator. Capture tool arguments/results and all usage. Separate B compiler wall time from inference and report both.
3. Use the frozen real writer prompt for both arms. A gets current research access; B gets validated dossier and an explicit no-tools variant of research instructions. Keep assignment, publication horizon, persona, continuity and editorial directives identical. Freeze and hash both complete prompts; any subsequent evidence addition starts a new version/pair.
4. First wave: three paired assignments, three repetitions each = **18 successful generation runs** before retries, with A potentially issuing many billable AFC requests. Alternate arm order; randomize blinded review IDs. Repeats sample variability; they are not proof across all families.

| Case | Assignment / evidence | Purpose |
| --- | --- | --- |
| 2036 W2 CAR/PIT, game 3845 | LOCAL_MATCHUP_PRIMER; Panthers_Beat_1 | Sparse early-season evidence, cutoff |
| 2036 W12 CAR/MIN, game 3995 | LOCAL_MATCHUP_PRIMER; same writer | Midseason context/continuity |
| 2036 W21 CAR/AZ, game 4131 | LOCAL_MATCHUP_PRIMER; same writer | Conference playoff override |
| 2036 publication W21, game 4123 CAR/SF (W20) | Existing postgame/report-card configuration and eligible writer | Exact score/player lines; recap horizon |
| Selected real roster transition + injury + missing-stat fixture | Spotlight/availability families | Former-team attribution, unknown values; selection still required |
| Rankings/awards/full slate | Eligible configured writer, complete candidate set | Curatorial expansion only after assigned-family pass |

Only the first three have frozen prompt/dossier packets. Other rows are expansion assignments requiring compiler/fixture selection, not claimed completed trials. A multi-season fixture needs trustworthy transition and historical evidence before inclusion.

## Acceptance and review sheet

For each arm, record every checkable claim, text span, canonical source/table/key, cutoff, expected value, contradiction/unsupported/verified status and reviewer decision. Numeric checks must respect units, rounding and ties; do not reject analysis merely because it is interpretive. Quotes, play details and reporting access require actual source evidence. Check persona continuity separately from factual league history.

Hard gate: **zero known factual contradictions or fabricated quotes/access/play details in accepted outputs**; missing evidence remains unknown; no future facts; correct writer/team/franchise; all required structural/family validators pass. Human review remains required; regex format audit cannot certify this gate.

Blindly score voice, angle, tactical substance, readability, continuity and repetition on 1–5. Proposed pilot acceptance: dossier median overall score >= ReAct, no dimension median lower by more than 0.5, and no case with a serious unexplained regression. JR must approve these proposed thresholds after seeing samples. Expand per family, never universally from three primers.

Report accepted-output yield, raw/accepted cost, all prompt/cached/output/thinking tokens, retries, error counts, compiler/inference/total wall time and reviewer work. Price only against verified provider billing and actual complete usage. No hard media-latency gate is assumed; prioritize quality and price, report observed wait.

## Next decision and missing inputs

First fix experiment isolation/settings/audit gaps above. Then obtain JR's explicit provider/model and **total spend ceiling** for the paired trial; no paid calls are authorized by this report. A tiny one-pair smoke test can precede the proposed 18-run pilot. Exact cost cannot be responsibly quoted before model availability, complete prompts, per-call accounting and provider rates are checked. Colab account allocation, actual GPU/VRAM/burn rate, weight access and cold/warm inference remain unmeasured; they are independent of this architecture comparison.


## Authorized pilot implementation (2026-10-30)

JR authorized closing the gaps and running a pilot, with a **$10 total API ceiling**. The earlier recommendation to obtain authorization is satisfied. Historical latest-only values are a shared limitation: ReAct cannot recover values that were never stored either. They are not evidence that dossier architecture loses facts. We evaluate the same source snapshot and publication horizon in both arms; historically stale values must not be misrepresented as current.

`tools/dossier_pilot.py` is the replacement evaluation entry point. It requires an explicitly named `madden_v3_eval_*` scratch database, sets read-only libpq connection options before importing newsroom modules, uses the actual archive reporter, enters article context around compilation and research, and prepares typed provenance-bearing evidence sections. Available/missing/error statuses are explicit; detected helper failures stop preparation. New pilots do not use the original synthetic writer. The legacy paid path now rejects live calls.

The scratch database `madden_v3_eval_20261030` was restored from a consistent archive dump, retained locally at `verify_output/v3_pilot/archive.dump`, SHA-256 `58eaab608f9535bbfc7d5dcd78c01a42b282358fa58a9ad086b1bc14b11e4ce6`. The archive itself was only read. Both arms connect to the same unchanged scratch copy. Latest-only data remains latest-only; no invented historical reconstruction is claimed.

Matched settings: `gemini-3.8-flash:low`, temperature 0.7, 8,192 output-token cap, same retry policy (SDK attempts=1), same writer/directives/cutoff. ReAct retains the baseline SDK automatic-function-calling behavior (25 remote calls, synthesis follow-up if empty); dossier writing has zero exposed tools. The evaluation wraps each underlying generate request, retaining full usage including cache and thinking. No publication, memory writes, or database usage logging occur. Tool arguments/results, final response, model metadata, packet hash, code fingerprint and review artifacts are written locally. Any uncertain failed request retains its conservative cost reservation; it cannot be treated as a free failure. Unmetered embedding calls are blocked; this archive's retrieved league-memory search was empty.

Pricing checked against [Google's standard Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) on 2026-10-30: $0.75/M uncached input, $0.075/M cached input, $3.75/M output including thinking, through 2026-12-31. These are estimated list-rate costs, not reconciled account invoices. The model metadata endpoint confirmed availability and a 1,048,576-token input limit. Each request reserves the full model input limit plus twice the capped output allowance before execution, then settles against returned usage; failures without usage retain the reservation. No paid external tools are enabled. Later pilot invocations use the remaining allowance, not a fresh $10 each.

```bash
# Offline preparation (no model calls)
DATABASE_URL='host=localhost dbname=madden_v3_eval_20261030 user=postgres password=postgres' .venv/bin/python -m tools.dossier_pilot --week 21
# Live smoke pair: use remaining authorized budget across invocations
DATABASE_URL='host=localhost dbname=madden_v3_eval_20261030 user=postgres password=postgres' .venv/bin/python -m tools.dossier_pilot --live --budget-usd REMAINING_ALLOWANCE --week 21
```

`factual_review.csv` contains every paragraph, with columns for atomic claims, source reference, expected value, verdict and reviewer notes. Reviewers must split multi-claim paragraphs into additional rows. Anonymous sample files and a separate review key support human comparison; automated format checks remain explicitly distinct from factual approval. Operational isolation/settings/accounting are implemented; an exhaustive automatic natural-language fact checker is not claimed.

### First smoke pair: Week 12

Artifacts: `verify_output/v3_pilot/20261030_195438_462732/`.

- ReAct: 39.71 seconds, 23 tool calls, estimated $0.07611465.
- Dossier: 17.12 seconds, zero tool calls, estimated $0.06881850 (excludes compiler time; compiler time is recorded in the packet).
- Combined estimated spend: $0.14493315. One pair is insufficient evidence of statistical savings or editorial parity.
- Both arms used Week-18 situational/season aggregates in a Week-12 story. Example: 49.3% red-zone TD rate is explicitly sourced from 17 games through Week 18 in `get_team_efficiency_profile`, yet both leads present it as entering Week 12. Correct 11-0/8-2 records do not make those totals contemporaneous. This is a shared source/interpretation failure, not unique to dossiers. Do not accept these outputs as historically accurate.
- Both leads report a private Tuesday film session without recorded evidence. Inspection found the production task actually demands this opening and explicitly permits invented scenes/quotes/texture. The pilot system policy conflicted with that task contract. This is an inherited editorial instruction, not proof that changing to dossiers caused invention.
- ReAct researched depth charts. Version 2 adds them to the fixed dossier, increasing coverage from 66 to 68 sections. This revision requires a new paired trial; the old result is retained, never silently compared to a revised dossier.
- Pilot-only prompt rendering now replaces the invented-film-session requirement and texture permission with evidence-led analysis in **both** arms. Production configurations are unchanged. The next pair uses W21, when completed regular-season totals are valid for the publication horizon; this avoids pretending the archive can supply unavailable earlier aggregates.

Do not scale to 18 runs merely because generation succeeds. Inspect the corrected smoke pair first; factual or editorial failures trigger a targeted revision or a retained-baseline decision. Full acceptance still requires representative repeated pairs, human editorial review, missing-data/transition tests, and separate family gates. Colab benchmarking and durable batch/import/publication integration follow architecture acceptance.


### Corrected smoke pair: Week 21 and decision

Artifacts: `verify_output/v3_pilot/20261030_195619_499948/`; cross-run ledger summary: `verify_output/v3_pilot/summary.json`.

| Measure | ReAct | Dossier |
| --- | --- | --- |
| Generation wall time | 59.47 s | 20.17 s |
| Research calls | 19 | 0 |
| Estimated API cost | $0.06362520 | $0.07849500 |
| Article words | 1,260 | 1,227 |

Both passed structural checks. Dossier generation was about 3x faster but about 23% more expensive in this pair: A benefited from substantial implicit caching, while B prefills a large evidence packet. This reverses the small cost advantage in the W12 pair. Compiler time is recorded separately in each packet. No general cost/parity conclusion follows from two pairs with different prompt versions/horizons.

Targeted review is saved in `review_findings.json` / `.csv`, with extra canonical evidence in `review_evidence.json`. It verified scores, the exact winning play, lead changes, relevant season totals, injury identity, physical attributes and selected awards/records. It also found:

- ReAct incorrectly calls Darby the team tackle leader (132); canonical leader Galloway has 153 and DuVall 138.
- ReAct changes Mendoza's +47% **interceptions per game versus strong scoring defenses** into an interception **rate when hurried**. The source has no measured pressure split. This is a substantive metric/causality error despite using an actual percentage.
- ReAct still invents private staff-week reporting despite the pilot policy.
- Dossier's broad claim that Davis “shattered records” around both 29 TD and 2,209 yards needs narrowing: the receiving-yard record is 2,406 in 2034. Sources also disagree about Davis's 2035 TD total (career context 29 vs record-book previous-holder metadata 27); do not silently resolve contradictory canonical sources in favor of a stronger claim. His 2036 sack/scoring teammate records were independently confirmed.

**Pilot outcome:** runnable, isolated architecture comparison and useful failure detection achieved; factual/editorial acceptance **not achieved**. No output published. Hold further repetitions while these known defects are unresolved; spending up to the ceiling is not the objective. Total estimated model spend across four generated articles is **$0.28705335**, leaving more than $9.71 of the authorized allowance unused. This is list-rate usage accounting, not an invoice.

The factual-review gap is addressed with preserved sources, explicit checks and a review gate, not an assertion of a comprehensive automatic claim verifier. Representative repeated trials, blind editorial scoring and further article-family coverage remain necessary before migration. Next useful work is source conflict/metric labeling and an explicit editorial decision on permitted fictional texture, followed by another narrowly revised paired test. Colab/hardware trials and durable batch/import work remain subsequent stages.

Validation after implementation: 40 focused tests passed (10 harness tests plus 30 existing context/memory tests), including matched live settings with a fake SDK, budget enforcement/uncertain failures, cost/caching/thinking arithmetic and nonpilot-database rejection. No production routing or engine changes were made by this work.


## JR correction and restored editorial rule (2026-10-30; supersedes stricter pilot judgments)

JR checked the save and confirmed Davis had **29 receiving TD in 2035**, not 27. `season_stats` and the career-summary view were correct. The stale `BOOK` entries, explicitly seeded as 27 in `2026_10_28_league_record_book.sql`, caused the erroneous broken-record conclusion. On JR's instruction, only `madden_v3_eval_20261030` was corrected: `franchise_records.id=442` and `league_record_entries.id=201` changed 27 to 29. No archive write or migration edit. Transactional correction provenance is retained in `verify_output/v3_pilot/evaluation_corrections.json`; this is a post-restore fixture correction, so the original dump hash identifies the original snapshot, not the corrected fixture by itself.

Read-only verification through the actual `get_record_book` tool now reports Davis's 2036 receiving-TD mark as **tied, 29; previous record 29, Davis (2035)**.

JR's editorial rule: invented quotes, private reporting access, scenes, dialogue and unrecorded plays are welcome wherever canonical data leaves room. They must fit recorded scores, scoring sequences, stats, participation, identities, injuries, transactions, awards, records and chronology. Missing canonical numbers cannot be invented. Creative texture alone is not a factual failure. The stricter pilot-only no-invention policy and film-session rewrites have been removed; original creative-texture directives remain in both arms. Production newsroom configuration is untouched.

Consequently, prior findings about invented private reporting alone are withdrawn as acceptance failures. The dossier followed an explicit (incorrect) record-book conclusion, so its TD-record claim is a source-quality issue, not a demonstrated independent hallucination. Broad receiving-yard record language still needs precise review. Wrong tackle-leader and misdescribed statistical measures remain genuine factual issues. Earlier outputs and original review artifacts are preserved as historical experiment records; apply this correction when interpreting them.

Fresh schema-v3 offline packet: `verify_output/v3_pilot/20261030_211356_359810/packet.json`, with source correction manifest, hash and `correction_verification.json`. Both arms were checked for matching creative-freedom policy and retained original texture directives; the dossier's record evidence was rebuilt. No new model calls or spend. Colab/GPU setup remains a separate exploration step; this change does not claim it is configured or validated.


## Dossier v2 iteration after the first Colab result

Pure compiler `tools/dossier_cards.py` transforms the corrected frozen packet into source-indexed subject cards. Assignment heading explicitly states Week 21, Conference Championship, winner advances to Super Bowl, game not yet played; it is repeated beside the final accuracy checklist. Every metric carries its own label, value, unit, league rank and regular-season scope. Roster identities are deduplicated, conflicting positions fail compilation, and numerical OVR is excluded from writer-facing evidence because the inherited article directive already forbids publishing it. Records retain scope and tied/broken status; QB split cards distinguish per-game interceptions from rates and pressure splits. Full prior box scores/game scripts, key-player awards and season/postseason lines, writer system/persona/memory and the league standings/slate remain.

This is not a claim of exhaustive research parity. Optional contracts/history helpers, duplicate analytics, non-QB splits, schedule excitement/margin metadata, long clutch game lists and the old tool-selection catalog are deferred. Detailed game_story duplicated in box scores is removed from macro recap entries; voice/opening/article-structure directives remain. Export records omissions and source indexes. The v2 input contains 33 selected source entries and **27,160 Qwen tokens**, down from 35,402 (23.3%). No truncation. Same Qwen AWQ revision `0499c3ac83fdef8810b907a23894ba91e95eddd8`, temperature/top-p/top-k and seed remain fixed to isolate this dossier iteration; comparison with a different Gemini model is still an end-to-end evaluation, not architecture-only proof.

The next Colab return is named `project_mu_colab_result_v2.zip`, preserving the first ZIP. Local tests: 47 passed, including stage selection, metric/rank binding, record ties, rating omission, persona preservation, missing-evidence failure and conflicting-position rejection. Actual revised generation and factual/editorial review are still pending. No new model calls or database writes were needed for v2 preparation.


### Second returned ZIP: original input repeated

`project_mu_colab_result (1).zip` used the exact first-run input hash (`80a4e3c289be890a23c6718fec67807c2858a506307afd81b161de843d7c6a16`), 35,402 input tokens, and produced a byte-identical article with the same model revision/seed. It is not evidence about dossier v2. Its reported generation time was 80.85 s; loading 102.65 s. Root ZIP preserved; comparison recorded in `verify_output/v3_colab/repeat_run_check.json`.

To prevent another ambiguous handoff, delivery artifacts are explicitly `newsroom_dossier_pilot_v2.ipynb` and `colab_matchup_input_v2.json`. Notebook requires dossier_version=2 and prints the stage header before token preflight/weight loading. Expected revised count remains 27,160 tokens; the old 35,402-token input is rejected by version before model loading. No new generation was run during this check.


## DeepSeek arm of the paired pilot (2026-11-11, CLOSE_OUT_CHECKLIST P8-04a)

`tools/dossier_pilot.py --provider gemini|deepseek` (default `gemini`, unchanged). DeepSeek runs `deepseek-flash` in both arms: the ReAct arm through `newsroom/tool_loop/openai_loop.run_openai_tool_loop(provider="deepseek")` with the same tools, writer prompt, cutoff and packet as the Gemini ReAct arm, and the dossier arm as one chat call with no tools. Both request temperature 0.7 and an 8,192-token output cap. DeepSeek's docs say `deepseek-flash` runs in thinking mode by default and that temperature has no effect there, so the packet and each result record the value for parity while noting the provider may ignore it.

Thinking mode plus tools requires the previous turns' `reasoning_content` to be sent back on assistant messages; omitting it returns HTTP 400. The OpenAI loop now captures it from the response and replays it on tool turns; providers that do not return the field get no extra key. Per-request usage is exposed as `ToolLoopResult.usage_calls` (uncached, cached, output and reasoning tokens plus latency), and the loop accepts `max_tokens`, forwards it to every request including fallback synthesis, and builds its default client with an explicit retry count. The pilot's own client wrapper reserves and settles the budget per request.

Pricing, checked 2026-11-11 against the DeepSeek Models & Pricing page (`https://api-docs.deepseek.com/quick_start/pricing`): `deepseek-flash` list rates per 1M tokens are $0.30 uncached input, $0.006 cached input and $1.20 output at peak, halved off-peak. Peak is Monday–Friday 01:00–04:00 and 06:00–10:00 UTC. Chinese public holidays are off-peak all day; no holiday calendar is kept, so those weekdays are conservatively priced as peak. Each call is priced by its own UTC time. Reasoning tokens are reported separately but are part of DeepSeek's `completion_tokens`, so they are billed once at the output rate (unlike Gemini, whose thoughts are added to the output count).

The budget keeps the Gemini reservation/settlement protocol with a $1.00 default ceiling for DeepSeek: each request reserves the 1M-token input context at the call's rate plus twice the output cap, then settles against returned usage; a request with no usage retains its reservation. Live DeepSeek calls require `--live`, a positive budget, the pinned `deepseek-flash` model and `DEEPSEEK_API_KEY`. No live DeepSeek call was made by the build; tests use a fake client only.

Truncation is detected rather than accepted as a complete article. Thinking tokens are part of `completion_tokens`, so a long think can consume the 8,192 cap and return `finish_reason == "length"` with a cut-off article that still passes a non-empty check. The loop records `finish_reason` on each `usage_calls` row, the pilot records it on each `usage.json` row and in `{arm}_result.json`, and when the article-producing call — the dossier call, the final ReAct turn or its synthesis follow-up — ends in `length`, the arm stops with `status="truncated"` and raises, keeping the settled cost. Tests cover a `length` response in each arm with a fake client (audit F1, M-0474).


## P8-04: the three dossier factual defects closed at the source (2026-11-12)

The corrected week-21 pair (above) left three named defects. They were reproduced offline from
`verify_output/v3_pilot/20261030_195619_499948/packet.json` before any code changed, and fixed in the
packet's source path (`newsroom/db_tools.py` + `tools/dossier_pilot.py`); neither article writer's
routing, model choice nor settings changed.

1. **Wrong tackle leader.** The packet's per-team leaderboards asked for sacks, interceptions and field
   goals only, and `get_defense_special_teams_leaders` printed each player's full line without naming
   the metric that ranked the rows. A writer read row 1 of the CAR interceptions list (Darby, 132
   tackles) as the team tackle leader; the packet's own list had Galloway at 153 (row 2) and DuVall at
   138. The payload now carries a `ranked_by` block and each line a `ranked by <category>: <value>
   (list #n)` marker plus a note that row 1 is not automatically the tackle leader, and the pilot
   requests the `tackles` leaderboard explicitly (`PACKET_DEFENSE_CATEGORIES`). Regression:
   `tests/test_db_tools_dossier_facts.py` (Galloway first at 153 in the tackles listing; Darby's 132 is
   labelled an interceptions-list row) and `tests/test_dossier_pilot.py`.
2. **Interception-rate misdescription.** The QB split showed `interceptions: 0.6` and `+47%` for
   `scoring_defense` (the opponent's points-allowed grade). The writer turned that per-game count
   against strong scoring defenses into an "interception rate when hurried". The payload now labels
   every figure (`metric_labels`, `metric_scope`: a per-game count is not a rate), scopes each split to
   the opponent's season defensive grade (`dimension_scope`), and lists the splits that do not exist
   (`not_recorded`: pressure, hurry, blitz, hit and time-to-throw; interception rate). Regression:
   `test_split_metric_labels_say_interceptions_are_per_game_not_a_rate` and
   `test_scheme_split_payload_carries_labels_and_denies_a_pressure_split`.
3. **Record claim built on a stale `BOOK` row.** The 2035 receiving-TD seed says 27 while the recorded
   2035 season line says 29, so the 2036 29 was reported `broke`; only `madden_v3_eval_20261030` had
   been hand-corrected. `get_record_book` now cross-checks every previous value against the holder's
   recorded season line and, on a real disagreement, emits `source_conflict` with both values and the
   resolved result, sets `previous_record` from the recorded line and recomputes broke / tied / ranks.
   The Davis event therefore reports **tied, 29** with `book_previous_record: 27` on a fresh database,
   and `record_scope_note` tells every writer never to upgrade a tie or a flagged conflict to broken or
   "shattered". Regression: `test_stale_book_row_is_re_resolved_from_the_recorded_season_line`,
   `test_agreeing_book_row_is_left_alone` and `test_record_book_tool_flags_the_stale_row` (fake cursor;
   no database written).

The packet's `shared_limitations` and the shared `POLICY` carry the same three rules, so both arms see
them. Offline only: no database write, no scratch database, no live model call, no PC. The first
corrected paired run is still P8-05.
