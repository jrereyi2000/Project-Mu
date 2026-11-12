# Project Mu: Article Fact Gate Plan

**Status:** approved by JR (2026-11-12); building, wave 3 of 3 (R1 fix); `/execute-plan` runs it as label `fact-gate`.

**As built:**
- U3 also lands a test-infrastructure fix the unit flagged as outside its files: `tests/conftest.py` sets `FACT_GATE_ARTICLES=0` for the whole non-integration tier (a test driving the writer path would otherwise build a checker client and spend a paid call), and `tests/test_swarm_fact_gate.py` turns it back on for its own cases. Commit `cd00131`.
- The gate rewrites at most once per article and keeps whichever version has fewer contradictions (R4), storing the discarded count in `fact_gate` rather than dropping the good text.
- `news_feed.fact_gate_status` is written only when the column exists; a live database without `2027_02_30` still saves, logs `[FACT GATE COLUMN MISSING]` once and prints no notice line. The migration is JR's to apply (R6).
- The reading-view badge in §2.5 is **not** in this plan's units (no unit owns `server/api.py` or the frontend) and is not built; `/api/news/week` does not yet select the new column. Open for JR.

**Builds on:** [`MODEL_ROUTING_EVALUATION.md`](../reference/MODEL_ROUTING_EVALUATION.md) §8.1 (the measured comparison and the Carson fabrication it exposed), PROJECT_SPEC §7.3 (editorial slate) and §2.5/§2.26 (award candidates, media panel), [`NEWSROOM_MODEL_SIDE_BY_SIDE_PLAN.md`](../done/NEWSROOM_MODEL_SIDE_BY_SIDE_PLAN.md) (the comparison tool and the `capture` seam this reuses).

**Goal:** an article cannot reach `news_feed` with a claim that contradicts the evidence its writer had. Every article is checked claim by claim after it is written, a contradiction earns **one** correction pass that shows the writer the evidence, and an article that still contradicts it is stored *flagged* rather than published silently (or blocked — JR's call, §8).

**Delivers:**
- `newsroom/fact_gate.py` — `check(text, evidence, model)` → `{claims, counts, contradicted}` and `rewrite_prompt(...)`, sharing the rubric `tools/article_factcheck.py` already uses, extended to attribute and event claims.
- `_execute_agent` runs the gate on every `ARTICLE` before saving, with the existing capture as its evidence.
- `news_feed.fact_gate` (JSONB) + a `fact_gate_status` column, so the frontend and the audit can see what was checked and what was flagged.
- `get_award_race` in `newsroom/db_tools.py`, wrapping `engines/award_candidates.candidates()` — the tool the Awards article asked for and never had.

---

## 0. Survey (code facts that set the scope)

| # | Fact | Consequence |
|---|---|---|
| S1 | `newsroom/swarm_dispatcher.py` `_execute_agent` already captures `text`, `tool_calls` (name, args, result, error) and `rewrites` when a `capture` dict is passed (`NEWSROOM_MODEL_SIDE_BY_SIDE_PLAN` U1). | The evidence for a gate is already in hand at write time; no re-querying needed. |
| S2 | `tools/article_factcheck.py` (2026-11-12) judges claims against evidence with SUPPORTED / CONTRADICTED / UNSUPPORTED and quotes, resumably, at ~$0.006 per article on `deepseek-v4.1-flash`. | The check exists as a CLI; the plan promotes its logic to a module and calls it inline. |
| S3 | The AWARDS_TRACKER brief tells the writer to cover **MVP, OPOY, OROY, DPOY, DROY and COTY** (the string appears 4× in the compiled prompt). | The writer is asked for races it must be able to look up. |
| S4 | `engines/award_candidates.py:33` `AWARDS` and `:145 candidates_from_lines(lines, award, team_wins)`; `:149` treats OROY/DROY as `years_of_experience == 0`; `candidates(cur, season, award)` is the ready entry point. | The correct data exists and is not exposed to writers. That gap is why GLM invented Isaiah Carson. |
| S5 | `newsroom/db_tools.py:1937` `get_defense_special_teams_leaders(..., rookies_only=False)` is the only rookies filter, and it covers defense/special teams only. `get_top_rated_players` returns no experience column. | No offensive-rookie source; a writer cannot name an OROY candidate honestly. |
| S6 | The Carson fabrication: `player_seasons` gives him YOE 0 in 2035 and **1** in 2036; "Carson" appears 0 times in that run's 82,642-character evidence; the article claims 121 catches / 1,755 yards / 20 TD and "as a rookie". | The failure class the gate must catch: a whole section with entity, status and statistics invented. |
| S7 | The same run called `get_player_season_stats()` with **no arguments** twice; the tool returned `missing 1 required positional argument` and the writer continued. | A malformed tool call is not fatal today. The gate is the backstop; a retry hint is a cheap extra. |
| S8 | `_execute_agent` already requests one rewrite for POWER_RANKINGS, ALL_PRO, AWARDS_TRACKER and the Super Bowl MVP, from a parse/validation error list. | The correction pass has an established shape to copy; the gate becomes another validator, not a new mechanism. |
| S9 | `news_feed` has no place to record a check; articles are inserted at `swarm_dispatcher.py` `INSERT INTO news_feed`. | A migration is needed if the result is to be visible and auditable. |
| S10 | Fact-check cost measured: ~$0.006 and 60–150 s per article on `deepseek-v4.1-flash`; a 19-article slate ≈ $0.12 and, at 5 workers, no meaningful wall-clock increase. | Affordable on the DeepSeek Go allowance; the latency is the writer's, not the slate's. |

## 1. Scope

**In:**
- A writer tool for the six award races, `get_award_race(award, season)`, correct by construction (rookies are YOE 0).
- A claim checker as a module, with the rubric extended to **attribute claims** ("X is a rookie", "Y coaches Z") and **event claims** ("A beat B 24-21") — the two classes the current CLI under-weights.
- One correction pass on a CONTRADICTED verdict, with the claim and the evidence quoted.
- A stored record per article: how many claims, how many contradicted, what was done (`clean`, `rewritten`, `flagged`).
- A gate that can be switched off per label (`FACT_GATE_ARTICLES=0`) and an env override for the checker model (`FACT_GATE_MODEL`, default `deepseek-v4.1-flash`).

**Out (non-goals):**
- Replacing the media voters or the award engine. The tool reads `engines/award_candidates`; it does not decide the awards.
- Checking Agent Wire posts, press conferences or inbox replies. Same technique, different plan; they have no `news_feed` row and a different cost profile.
- Re-checking already published articles: the gate runs at write time, on the evidence in hand.
- Vision/OCR extraction, which is not text evidence.

## 2. Design

### 2.1 `newsroom/fact_gate.py`

```python
FACT_GATE_MODEL = os.environ.get("FACT_GATE_MODEL") or "deepseek-v4.1-flash"
MAX_CLAIMS = 60            # raised from the CLI's 40; the Carson section sat past the cut
def check(text: str, evidence: str, model: str = None, client=None) -> dict
def rewrite_prompt(contradicted: list) -> str
```

- `check` returns `{"claims": [...], "counts": {"SUPPORTED","CONTRADICTED","UNSUPPORTED"}, "ok": bool, "model": ...}`, one claim per fact with a 12-word evidence quote and a `derived` flag.
- **Claim coverage is the fix, not the model.** The rubric gains three explicit passes before numbers: (1) every named person's team and role, (2) every status word (`rookie`, `second-year`, `veteran`, `undrafted`) checked against `years_of_experience`, (3) every game result as a (winner, loser, score) triple. A claim that names a person absent from the evidence is `CONTRADICTED` with the quote `"not in evidence"`, not `UNSUPPORTED` — silence about an invented person is the signal.
- `client=None` builds a Go client for the checker model through `newsroom/tool_loop/go_transport.py`; a `gemini-*` model uses the Gemini client. Thinking is asked low and the output cap is 32,768 (thinking shares it — the failure from 2026-11-12).
- `rewrite_prompt` is one message: each contradicted claim, the evidence line, and "correct or delete these; do not add new claims".

### 2.2 The tool: `get_award_race` (`newsroom/db_tools.py`)

```python
@tool
def get_award_race(award: str, season: int = 0) -> str
```
- Accepts `MVP|OPOY|DPOY|OROY|DROY|COTY` (case-insensitive), defaulting the season to the article's context like every other tool.
- Calls `engines/award_candidates.candidates(cur, season, award)` (COTY through `coty_candidates_from_rows`), returning the top 10 with name, team, position, **years_of_experience**, games and the metric z-scores the voters weigh.
- For OROY/DROY every row is YOE 0 by construction; the tool says so in one header line, so a writer cannot call a second-year player a rookie without contradicting its own tool.
- Registered in the same module list as the other tools, so `tools_for()` picks it up for the award article types (`AWARDS_TRACKER`, `OFFICIAL_AP_ALL_PRO_ROSTER`) and the fantasy awards card.

### 2.3 Wiring (`_execute_agent`)

After the existing validation rewrites and before the `save_to_db` block:

1. `gate = check(generated_text, evidence)` where `evidence` is the same string the comparison tool records (task instruction + tool results, now 12k per result).
2. If `counts["CONTRADICTED"]` and no rewrite has run yet: `chat.send_message(rewrite_prompt(...))`, replace `generated_text`, `rewrites.append("FACT_GATE")`, and re-check once.
3. If still contradicted: `fact_gate_status = "flagged"`, keep the article, record the claims and quotes.
4. `clean` / `rewritten` otherwise.
5. `MU_NO_LLM=1` (tuning ingests) skips the gate entirely, like every other model call.

### 2.4 Storage

`db/migrations/<next>_fact_gate.sql` (next free number; the builder confirms it at build time) adds to `news_feed`:
- `fact_gate_status VARCHAR(16)` CHECK `('clean','rewritten','flagged','skipped')`
- `fact_gate JSONB` — counts, the contradicted claims with quotes, the checker model, the cost
- index `news_feed_fact_gate (season, week) WHERE fact_gate_status = 'flagged'`

`db/init_db.sql` mirrors the migration (the standing rule for every schema change).

### 2.5 What JR sees

- A `flagged` article is stored and visible, with a badge in the reading view and the editor's list; nothing is hidden.
- The week's notice gains one line: `N articles written, M fact-gated (K flagged)`.
- `python3 -m tools.article_factcheck --dir …` keeps working for off-line audits and now shares the module.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | The checker flags true statements as CONTRADICTED (e.g. a fact from the brief, or arithmetic). **Found on 2026-11-12, first R1 run with U2's rubric: it does, badly** — see §3.1. | Re-run it over all 18 comparison articles with the new rubric and read every CONTRADICTED claim by hand; the target is zero false contradictions on those 18 before the gate blocks anything. | Keep the gate in `flag` mode (record, never block) for a week and tune the rubric. |
| R2 | The gate adds 60–150 s and ~$0.006 per article. | Measured already (S10); re-measure on one weekly run. | Run it on the flagged article types only, or on the first N articles. |
| R3 | The checker model may share a blind spot with the writer (both may believe a second-year player is a rookie). | The rubric forces the comparison against `years_of_experience` in the evidence, not against world knowledge. | Use two checkers from different families and require agreement on CONTRADICTED. |
| R4 | The correction pass can make an article worse (the writer deletes a good section). | The gate re-checks after the rewrite and keeps whichever version has fewer contradictions; store both? No — store the better one and note the discarded count. | Disable the rewrite (`FACT_GATE_REWRITE=0`) and keep flagging. |
| R5 | `get_award_race` changes what the awards article can say (it may disagree with the media panel's eventual ballot). | The tool returns the *candidate race*; the panel still decides. Where the stored ballot exists, the article already follows it (S8). | Label the tool output "candidate race, not the result". |
| R6 | A migration on `news_feed` needs care on the live franchise DB. | Standard: migration + `init_db.sql` mirror + `tests/test_migrate.py`; apply is JR's call. | Ship the gate in log-only mode with no column until JR applies it. |

**Spike first:** none blocking. The one pre-build check is R1: re-run the checker over the 18 articles with the new rubric and confirm the Carson section is caught and true claims are not. That is a 20-minute run on DeepSeek (~$0.12). **Run after U2 merges** (the new rubric does not exist before it): the Carson fixture is U2's unit test, and the 18-article re-run is `python3 -m tools.article_factcheck --dir verify_output/model_compare/20261112_053537 --max-claims 60` with the shared rubric, read by hand for false contradictions.

### 3.1 R1, first run (2026-11-12): failed, and why

Run with U2's rubric at `--max-claims 60` over the same 18-article directory (copied to `/tmp/opencode/factcheck-r1`, original files untouched), 8 of 18 articles judged before the finding was clear: **CONTRADICTED is 20–62% of claims, and almost all of it is false.**

Two causes, both structural, neither a model problem:

1. **The evidence window drops most of the evidence.** `newsroom/fact_gate.py` bounds the evidence at 24,000 characters and the brief at 6,000, copied from the CLI. The 18 articles' evidence is 56,590–205,579 characters (median 97,004) and the *assignment alone* is 46,484–48,496 characters. So the checker sees under half the brief and **none of the tool results** in a production run — every name it cannot find is then, under the new rule, CONTRADICTED.
2. **The absent-person rule is unqualified.** "A person absent from the evidence is CONTRADICTED with the quote `not in evidence`" was aimed at the invented Isaiah Carson. Against a truncated window it fires on every real coach and player the article mentions in passing: `Mike LaFleur is the Cardinals' head coach`, `Kyle Shanahan is the 49ers' head coach`, 37 such lines in one power-rankings article.

The real contradictions the same run finds are exactly the class the gate is for and survive both fixes: the archive's Davis 29-vs-27 record claim, `the Jets' 469 points are second in the AFC` (evidence: Bengals 460, and 469 is first), `Indianapolis surrendered 27 second-half points` (halftime IND 20-6, final 33-27), and a swapped winner/score in the Muse Hero. **Fix, as U4 below: a truncation-aware evidence window, a scoped absent-person rule, and `FACT_GATE_REWRITE=0` (R4's switch) until the re-run is clean.**

## 4. Tests (synthetic, no live DB)

- `tests/test_fact_gate.py` →
  - a fabricated person with statistics is CONTRADICTED (the Carson fixture: article text + evidence without him);
  - a status word is checked against `years_of_experience` (a YOE 1 player called a rookie is CONTRADICTED, a YOE 0 player is SUPPORTED);
  - a game result is a (winner, loser, score) triple and a swapped score is contradicted;
  - a derived number (a per-game average of two evidence values) is `derived: true` and not contradicted;
  - a clean article returns `ok` and no rewrite prompt;
  - the rewrite prompt names every contradicted claim with its evidence quote and forbids new claims;
  - no key / no model returns `{"ok": True, "skipped": "no checker"}` so a gate outage never fails a write.
- `tests/test_db_tools_award_race.py` →
  - each award code resolves case-insensitively; an unknown code lists the valid ones;
  - OROY/DROY rows are all YOE 0, and the header says so;
  - the top 10 are ordered by base score and carry team, position and games;
  - a season with no lines returns a readable "no candidates" string, not an exception.
- `tests/test_swarm_fact_gate.py` →
  - a capture run with a contradicting fake checker triggers exactly one rewrite, records `FACT_GATE` in `rewrites`, and re-checks once;
  - a still-contradicted article is saved with `fact_gate_status = "flagged"` and its claims in `fact_gate`;
  - `MU_NO_LLM=1` skips the gate and records `skipped`;
  - with `FACT_GATE_ARTICLES=0` the writer path is byte-identical to today.

Run with `pytest -m "not integration" tests/test_fact_gate.py tests/test_db_tools_award_race.py tests/test_swarm_fact_gate.py`. No test opens a database or the network.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: The award-race tool

- **Owns:** `newsroom/db_tools.py`, `tests/test_db_tools_award_race.py` (new)
- **Reads:** `engines/award_candidates.py:33-330`, `newsroom/db_tools.py:1487` (the COTY tool), `newsroom/db_tools.py:1937` (the rookies-only precedent)
- **Depends on:** none
- **Done when:** `get_award_race("OROY", 2036)` returns the ranked rookies with YOE 0 and the header says so, every award code works, and the tests cover the "no candidates" path. **U3 registers it** in `SwarmDispatcher.tools` (that file is U3's; the base set is what every writer, fantasy included, already gets), so U1 must not touch `newsroom/swarm_dispatcher.py`.
- **Verify:** `pytest -m "not integration" tests/test_db_tools_award_race.py` → all pass.

### U2: The fact gate module

- **Owns:** `newsroom/fact_gate.py` (new), `tests/test_fact_gate.py` (new), `tools/article_factcheck.py` (the rubric and parser this promotes move into the module, and the CLI imports them back)
- **Reads:** `tools/article_factcheck.py` (the rubric and parser this promotes), `newsroom/tool_loop/go_transport.py`
- **Depends on:** none
- **Done when:** the Carson fixture is caught, true claims are not, and a missing checker never raises.
- **Verify:** `pytest -m "not integration" tests/test_fact_gate.py` → all pass.

### U3: Wiring, storage and the notice

- **Owns:** `newsroom/swarm_dispatcher.py`, `db/migrations/<next>_fact_gate.sql` (new), `db/init_db.sql`, `tests/test_swarm_fact_gate.py` (new), `newsroom/weekly_run.py` (the notice line)
- **Reads:** `_execute_agent`'s validation block and save block, `db/migrations/` for the next free number, `PROJECT_SPEC` §3.3 `news_feed`
- **Depends on:** U1, U2
- **Done when:** a simulated article with a fabricated claim is rewritten once and, if still wrong, stored `flagged` with its claims; `get_award_race` is in `SwarmDispatcher.tools` so every writer (fantasy included) gets it; `MU_NO_LLM` and `FACT_GATE_ARTICLES=0` both leave today's path untouched.
- **Verify:** `pytest -m "not integration" tests/test_swarm_fact_gate.py tests/test_swarm_capture.py` → all pass; `python3 -m db.migrate diff` on a scratch DB → no drift.

**Implementation note (orchestrator, 2026-11-12):** in a live run there is no `capture` dict, so `_execute_agent` must collect the tool results it already prints (`entry["result"][:12000]`) into a local evidence list whenever the gate is on, and build `evidence` as the task instruction + those results — the same string shape `tools/article_model_compare.py` records. When a `capture` dict *is* passed, reuse its `tool_calls`. The gate runs only for `content_type == "ARTICLE"`, `live`, real text, no `failure_reason`, `MU_NO_LLM` unset and `FACT_GATE_ARTICLES != "0"`. Return the status in `_execute_agent`'s result dict (`"fact_gate_status"`, `"fact_gate"`) so `nightly`/weekly callers can count it without a second query; `newsroom/weekly_run.py` prints the one notice line from a count query over the week's `ARTICLE` rows.

### U4: Rubric and evidence-window calibration (R1 follow-up)

- **Owns:** `newsroom/fact_gate.py`, `tools/article_factcheck.py`, `newsroom/swarm_dispatcher.py` (the rewrite switch only), `tests/test_fact_gate.py`, `tests/test_swarm_fact_gate.py`
- **Reads:** §3.1 above, the R1 output at `/tmp/opencode/factcheck-r1/veracity.jsonl` if it is still there, `tools/article_model_compare.py`'s evidence building
- **Depends on:** U1, U2, U3
- **Done when:** the checker sees the whole assignment and every tool result (up to a large, documented bound), absence is only a contradiction when the article attaches a statistic, role or status to the person **and** the evidence was not truncated, a truncation marker is in the prompt when evidence is cut, `FACT_GATE_REWRITE=0` skips the correction pass and still stores the verdict, and the tests pin each of those.
- **Verify:** `pytest -m "not integration" tests/test_fact_gate.py tests/test_swarm_fact_gate.py` → all pass; then the R1 re-run over the 18 articles is read by hand for false contradictions.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2 | — | Different files, no shared state: the tool and the checker are independent. |
| 2 | U3 | U1, U2 | Wires both into the writer path and adds the storage. |
| 3 | U4 | U1, U2, U3 | R1's fix: one agent, the checker's own files. |

**File ownership check:**

| File | Unit |
|---|---|
| `db/init_db.sql` | U3 |
| `db/migrations/<next>_fact_gate.sql` | U3 |
| `newsroom/db_tools.py` | U1 |
| `newsroom/fact_gate.py` | U2 |
| `newsroom/swarm_dispatcher.py` | U3 |
| `newsroom/weekly_run.py` | U3 |
| `tests/test_db_tools_award_race.py` | U1 |
| `tests/test_fact_gate.py` | U2 |
| `tests/test_swarm_fact_gate.py` | U3 |
| `tools/article_factcheck.py` | U2 |
| `newsroom/fact_gate.py` (U4 revision) | U4 |
| `tools/article_factcheck.py` (U4 revision) | U4 |
| `newsroom/swarm_dispatcher.py` (U4: the rewrite switch) | U4 |
| `tests/test_fact_gate.py` (U4 revision) | U4 |
| `tests/test_swarm_fact_gate.py` (U4 revision) | U4 |

## 7. Spec changes when built

- §3.3 `news_feed`: two columns (`fact_gate_status`, `fact_gate`), the index, and the meanings of the four statuses.
- §7.3: the writer path checks every article before saving, one correction pass, and what a flagged article is.
- §7.5 / §2.5: `get_award_race` in the writer tool list and the award-candidate engine as its source.
- Repository Layout: `newsroom/fact_gate.py`, the migration, the three test files.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Does a still-contradicted article get published (flagged) or held back? | **Publish flagged** for the first two weeks, so a bad gate cannot empty the desk; then hold back only for the award article types, where the damage is worst. |
| Q2 | Which article types are gated? | All `ARTICLE` rows at first (cost is ~$0.006 each); Agent Wire, press and inbox are out. |
| Q3 | Is one correction pass enough? | Yes. A second pass costs double and the evidence shows the first pass either fixes it or the model has no source. |
| Q4 | Does UNSUPPORTED count against an article? | No. It is reported (and visible in the reading view only for flagged pieces) but never triggers a rewrite. |
| Q5 | Which checker model? | `deepseek-v4.1-flash` at ~$0.006 an article; if R3 bites, add `gpt-6-luna` as a second opinion on CONTRADICTED only. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | `fact-gate-u1` | `~/.local/share/opencode/worktree/404d9f/fact-gate-u1` | merged | 7075613 |
| U2 | 1 | `fact-gate-u2` | `~/.local/share/opencode/worktree/404d9f/fact-gate-u2` | merged | a252164 |
| U3 | 2 | `fact-gate-u3` | `~/.local/share/opencode/worktree/404d9f/fact-gate-u3` | merged | 8cfa931 (review fix `cd00131`) |
| U4 | 3 | `fact-gate-u4` | `~/.local/share/opencode/worktree/404d9f/fact-gate-u4` | merged | 26d0e55 |
