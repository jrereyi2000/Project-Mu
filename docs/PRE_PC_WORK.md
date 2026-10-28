# Project Mu: Pre-PC Work Briefs

**Status:** 7 of 11 items complete (Item 1 QB OVR refit, Item 2 surname renames/commentary IDs, Item 3 body skin tone, Item 4 watch_save.py, Item 5 thinking caps, Item 6 provider switch, Item 8 fold research into §7, Item 11 progression phase C done 2026-10-28). The checklist is [`PC_MIGRATION_PLAN.md`](PC_MIGRATION_PLAN.md) §2.4. Each brief below is written so an agent can take it on its own: goal, what exists, where to work, steps, and when it's done.

Nothing here needs the PC or a real 2037 save. On hold, not briefed: `madden_pgid` on the live database (after the PC move), the CFB 27 bridge (§6), the pregame show (§9.1) and the rest of Part 2.

## Rules for every item

* **Read `PROJECT_SPEC.md` first** (CLAUDE.md). Update it in the same change: Repository Layout tree, tables, CLI flags, behaviour. Tick the item in `PC_MIGRATION_PLAN.md` §2.4 and update the plan section the item points at.
* **Live database is read-only for agents** unless the brief says a write step needs JR's OK. Tests can't reach `madden` (`tests/conftest.py` blocks it); DB tests are `@pytest.mark.integration` against `madden_test`. New migrations go in `db/migrations/` (dated name, `IF NOT EXISTS`), applied to `madden_test` first.
* **Shared checkout:** other sessions edit `PROJECT_SPEC.md`, `db/init_db` and plan docs at the same time. Stage only your own hunks (`git add -p`); commit on `main` (or merge your branch into `main` right away); don't push; stop on a merge conflict in code.
* **Sample PC save:** `CAREER-GIANTS-AUTOSAVE` at the repo root (Madden 27, franchise year 2027). Snapshots aren't kept; make one into your scratch space with `node pipeline/save_reader/pc_save_reader.mjs CAREER-GIANTS-AUTOSAVE --out <scratch>/snapshot.json`. Never write the save itself; `save_writer.mjs` writes copies only.
* **Model calls cost money.** Unit tests use fake callables, never Gemini. A brief that needs real calls says how many.
* **Hallucination policy:** prose may invent untracked narrative, never contradict data. Engines decide numbers; models write words.

---

## 1. QB OVR refit [DONE 2026-10-28]

**Goal:** the Madden 27 OVR formulas in `engines/ovr_weights_m27.json` predict PS5-scanned QBs within ±1, without getting worse on the sample save.

**What was wrong:** each QB archetype (`QB_FieldGeneral`, `QB_Improviser`, `QB_Scrambler`, `QB_StrongArm`) leaned heavily on `LongSnap` (e.g. `QB_StrongArm`: squared weight 1.51 per LongSnap²/100). This was root-caused to a single anomalous converted receiver in `CAREER-GIANTS-AUTOSAVE` (Carter Landry, pgid 24437), who had flat 83 archetype grades despite an overall rating of 91 and LongSnap of 59 (while other QBs had LongSnap 0 or 10). Because NNLS tried to fit this anomaly, it assigned ~1.5 to LongSnap², which exploded on real PS5-scanned QBs with LongSnap 15–58.

**Solution & Results (2026-10-28):**
* Excluded anomalous QB records where `max(archetype_grades) != overall` in `usable_players()`.
* Excluded `LongSnap` from QB candidate features in `fit_weights()`, refitting all 4 QB archetypes with true passing/awareness attributes.
* Clamped `LongSnap` to 10 for QBs in `predict_grade()` as a backward-compatibility guard.
* **Scanned QBs:** 78 of 78 (100.0%) within ±1 in live `player_attributes` (73 exact / 93.6%, error distribution: `-1`: 3, `0`: 73, `+1`: 2).
* **Sample-save cross-validation:** all 4 QB archetypes improved to 1.000 within ±1 (was 0.931 / 0.959 / 0.883 / 0.979; cv_exact 90.3%–94.4%).
* **Non-QB archetypes:** all 42 other archetypes 100% unchanged.
* **Skills:** LongSnap is neither in terms nor skills for any QB archetype; `adjust_ratings` never moves LongSnap for QBs.
* All tests pass (`test_attribute_reader.py`, `test_ovr_weights.py`, `test_teleport.py`), and teleport dry run puts all 1,706 players on target.

---

## 2. Surname rename tool (commentary IDs) [DONE 2026-10-28]

**Goal:** every Project Mu player has a surname Madden's commentators can say. Players with a recorded surname get its ID; the rest get a ranked list of recorded surnames that JR reviews, and the approved renames are written to the database.

**Completed Implementation (2026-10-28):**
* **Source Lookup:** Fetched `data/commentary_m27.json` (7,865 surnames → 7,666 IDs, git-ignored). Clear `FileNotFoundError` instructions if missing.
* **Module `pipeline/commentary_names.py`:**
  - `normalize(surname)`: drops suffixes (Jr, Sr, II, III, IV, V), strips accents and punctuation, casefolds, matches with and without hyphens.
  - `commentary_id(full_name)`: extracts candidate surnames, checks exact, normalized, unhyphenated, and punctuation-free matches; returns commentary ID int or `None`.
  - `suggest_surnames()`: ranks top 5 recorded surnames using Double Metaphone phonetic similarity tier + Levenshtein edit distance, preferring same first letter and strictly filtering out surnames already on the player's team roster.
  - `report` CLI: computes coverage on active players (skipping `DEF` rows) and outputs `captures/commentary_unmatched.csv` with player info and top 5 ranked candidates, leaving `chosen` for JR review. Active players: 1,665 / 1,797 matched (92.7%), 132 unmatched.
  - `apply` CLI: reads reviewed CSV, dry-run by default, validates chosen surnames against commentary lookup, updates `players.full_name` and `player_name`, and records historical aliases in `player_name_aliases`.
* **Database Migration & Alias Search:**
  - Added migration `db/migrations/2027_01_07_player_name_aliases.sql` creating `player_name_aliases` table and index; added table to `db/init_db.sql`. Applied to `madden_test`.
  - Integrated `player_name_aliases` into player search in `server/api.py` (`/api/search`) and `server/napper_api.py`.
* **Teleporter Integration:**
  - `pipeline/teleport.py` and `pipeline/teleport_draft.py` set `PLYR_COMMENT` from `commentary_id()` (0 when unrecorded).
  - Teleport reports include `commentary_matched` and `commentary_unmatched` counts.
* **Testing:** 14 tests in `tests/test_commentary_names.py` covering normalization, lookup, ranking, dry-run rollback, committed renames, and alias search in `madden_test`. All teleport unit and integration tests passing (`tests/test_teleport.py`, `tests/test_teleport_draft.py`).

---

## 3. Body skin tone (`CharacterVisuals.RawData`) — COMPLETED (2026-10-28)

**Goal:** the teleporter can set the body's skin tone to match the generic head it picks, so head and body agree.

**Background:** plan §4.3 "Appearance" and the `pipeline/teleport_appearance.py` docstring. The head's tone is the first digit of `GenericHeadAssetName` (`gen_<tone 1–7>_<hair>_<facial hair>_<n>`). The body's tint is `skinTone` (1–8) inside `CharacterVisuals.RawData`, a zstd blob compressed with a dictionary and holding an ISON document (gear loadouts plus `skinTone`). `madden-franchise` can't decode it on Node 23; Python `zstandard` plus a small ISON decoder reads it, but that decoder isn't in the repo (rebuild it). After a teleport the body tone matches the head for only 325 of 1,797 players.

**Completed implementation:**
1. **Dictionary & ISON structure:** Found zstd dictionary `madden-franchise/data/27/dict.bin` (dictionary ID `0x1823777C`). CharacterVisuals Table3 field `RawData` format: `[2 bytes LE uint16 compressed length] [zstd compressed stream with dict 0x1823777C] [0-padding to maxLength + 2 (377 bytes)]`. Payload is ISON format; root field `skinTone` is stored as `[0x10, 0x0a, 0x20, 0x00, 0x03, <tone: 1-8>]`.
2. **Head to body mapping:** Analysed all 2,985 players in `CAREER-GIANTS-AUTOSAVE`. The modal mapping from generic head tone digit 1–7 to body skinTone 1–8 is an exact 1-to-1 match: `{1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7}`.
3. **Encoder / Decoder module:** Implemented `pipeline/character_visuals.py` supporting ISON parse/serialize and zstd dictionary re-compression with `write_content_size=True`. Validated 100% round-trip fidelity (2,987/2,987 records in the sample save decoded and re-compressed with 100% byte-for-byte and decompression match). Provided CLI `batch` and `read-batch` IPC commands.
4. **Save writer integration:** Implemented `pipeline/save_reader/visual_edits.mjs` handling `visuals` block (`{"skinTone": 1..8}`), copy-on-write allocation if a `CharacterVisuals` record is shared by multiple players, Table3 record slot sizing and overflow record handling (`populateOverflowRecord` / `clearOverflowRecordIfExists`), and strict read-back verification. Integrated into `pipeline/save_reader/save_writer.mjs`.
5. **Teleporter integration:** Updated `pipeline/teleport_appearance.py` and `pipeline/teleport.py` so `--appearance` sets `visuals: {"skinTone": look["body_skin_tone"]}` alongside generic head and body type.
6. **Tests:** Full test suite passing in `pipeline/save_reader/test/visual_edits.test.mjs` (`npm test`, 31 passing) and `tests/test_teleport_appearance.py` (`pytest`, 16 passing including `SampleAppearanceTest` verifying all 1,796 players in `CAREER-GIANTS-AUTOSAVE`).

Whether Madden draws the edited body skin tones on PC is added to plan §5.4.

---

## 4. `pipeline/watch_save.py` (Done)
*Completed 2026-10-28.* Built `pipeline/watch_save.py`, extracted `weekly_capture.run_weekly_engines` for deterministic engine reuse, and covered with 21 unit and integration tests in `tests/test_watch_save.py`. Verified end-to-end dry-run against `CAREER-GIANTS-AUTOSAVE` and `madden_test`.

**Goal:** a daemon that notices when Madden saves a new week and runs the weekly pipeline on the save (plan §5.2).

**Pieces that exist:**
* `node pipeline/save_reader/pc_save_reader.mjs <save> --out <json>`: reads a copy, never the save.
* `python3 -m pipeline.load_save <json> [--season N] [--commit]`: one transaction; also moves `league_state.current_week` and writes a `captures` row (mode `save`).
* `league_state` (`current_season`, `current_week`).
* Engines and articles: see steps 7–8 in `pipeline/weekly_capture.py`'s docstring (the backfill + analytics engines, then `newsroom.weekly_run`). Reuse those functions rather than copying them; refactor them out if needed.
* `pipeline/notify.py` `notify(message, title, priority, tags)` sends JR's phone notices.

**Behaviour:**
1. Watch a folder for `CAREER-*` files (path from an argument or `.env`; on the PC it will be `/mnt/c/Users/<Name>/Documents/Madden NFL 27/saves/` seen from WSL2). Polling mtime every few seconds is fine and works across `/mnt/c`; don't depend on inotify there.
2. Debounce: act only when the file hasn't changed for N seconds (default 10) and its size is stable.
3. Read the snapshot into `captures/<run>/save.json` and compare its season and week with `league_state`. Same week: do nothing. New week: load (`--commit`), then engines, then articles. Older week or another franchise: notify and do nothing.
4. One run at a time (lock file); a failure stops that run and notifies, as `weekly_capture` does.
5. Flags: `--once` (check now and exit), `--dry-run` (load without commit, no articles), `--no-articles`.
6. Local-model jobs go on the nightly queue (plan §7.5); until that exists, just leave a hook point.

**Done when:** tests drive it with a temp folder and a fake reader/loader (touch the file, change week, partial writes) and show: no run during a burst, exactly one run per new week, no run for the same week, lock respected. A manual `--once --dry-run` against `CAREER-GIANTS-AUTOSAVE` and `madden_test` works end to end. Spec layout, CLI and plan §5.2 updated.

---

## 5. Thinking caps on the short Gemini calls (Done, 2026-10-28)

**Goal:** stop paying for ~1,000 thinking tokens per short call (plan §7.1, §7.9).

**Completed implementation:**
* `engines/llm_config.py` provides `parse_model_thinking`, `resolve_thinking_level`, and `make_content_config` supporting `model:level` (e.g. `gemini-3.8-flash:low`).
* Callers updated:
  - `newsroom/agent_memory.py` `gemini_text()`: defaults to `low` thinking. Configurable via `AGENT_MEMORY_THINKING` env var or `model:level`.
  - `engines/gm_llm.py` `GeminiText` and `make_llm()`: defaults to `low` thinking across `gm_persona`, `gm_rationale`, `gm_negotiation`, `scouting_notes`, and `draft_rationale`. Configurable via `GM_LLM_THINKING`, `GM_THINKING_<LABEL>`, or `model:level`.
  - `newsroom/social_writer.py` `write_post()`: unified on `make_content_config()`.
* Unit tests in `tests/test_llm_config.py` verify that `ThinkingConfig` is constructed and passed with expected thinking levels using mock clients.
* Comparison numbers and token savings documented in `docs/PC_MIGRATION_PLAN.md` §7.9 and `PROJECT_SPEC.md`.

---

## 6. Gemini-or-local provider switch (Done, 2026-10-28)

**Goal:** each short-task caller can use Gemini or a local Ollama model, falls back to Gemini when the local one is down, and logs both to `llm_usage` (plan §7.3).

**Completed implementation:**
* `engines/llm_provider.py` provides provider resolution (`resolve_provider`, `resolve_model`, `resolve_fallback_model`), Ollama HTTP API calling (`call_ollama` supporting `/api/generate`, `/api/chat`, and JSON schema format), Gemini execution (`call_gemini`), unified text generation with automatic fallback (`generate_text`), and a provider-neutral callable with flushing (`ProviderText`).
* Provider choice configured via model prefix (`ollama:<model>` / `gemini:<model>`), per-label/source environment variables (`LLM_PROVIDER_<LABEL>`, e.g. `LLM_PROVIDER_AGENT_WIRE`, `LLM_PROVIDER_AGENT_MEMORY`), global `LLM_PROVIDER` env var, or explicit arguments. Default provider remains `gemini`.
* Fallback to Gemini triggers strictly on connection error or timeout (`requests.exceptions.ConnectionError`, `Timeout`, socket/connection refused), never on bad answers or validation failures.
* Callers updated:
  - `engines/gm_llm.py` `make_llm()`: returns `ProviderText` for Ollama and `GeminiText` for Gemini, preserving `llm(prompt)` signature and `flush()`.
  - `newsroom/agent_memory.py`: introduces `llm_text()` as provider-neutral helper while retaining backwards-compatible `gemini_text()`.
  - `newsroom/social_writer.py`: `write_post()` supports Ollama with automatic Gemini fallback; `cost_usd()` returns $0.00 for local Ollama models.
* `db/llm_usage.py`: supports Ollama token mapping (`prompt_eval_count` -> `input_tokens`, `eval_count` -> `output_tokens`, `thinking_tokens` -> 0), model column normalization (`ollama/<model_name>`), `log_usage()` direct helper, and `UsageMeter.add_call()` for recording local calls.
* Unit tests in `tests/test_llm_provider.py` (25 tests) cover provider selection, Ollama calling and schema formatting, automatic fallback on connection error and timeout, token logging, and caller integration.

---

## 7. Quality test harness

**Goal:** one tool that runs a week of short-task calls through two or more providers on the same inputs and lays the outputs side by side, as plan §7.4 requires before any task moves.

**Start from:** `tools/social_model_compare.py` (Agent Wire: same 20 assignments per model, JSON output with tokens and cost; already accepts `model:thinking`), and `tools/persona_side_by_side.py` (articles). Generalize, don't duplicate.

**Steps:**
1. `tools/llm_compare.py --task {agent_wire,memory_takes,memory_summary,scouting_notes,gm_persona,gm_rationale,gm_negotiation} --season S --week W --models A B …`: builds the real inputs from the database (read-only), sends each through every model via item 6's seam, and saves nothing to the newsroom tables.
2. Output: one JSON per run plus an HTML page in `verify_output/` with inputs and outputs per row, and per-model columns for tokens, thinking, seconds and cost.
3. Automatic checks that exist in code already: the callers' validators (`social_writer`'s length, money and rumor guards; `scouting_narrative.validate_scout_text`; GM message validation), JSON/schema validity for GM calls, and numbers in the output that aren't in the input facts. Count failures per model.
4. Leave voice and persona fit to JR: a column for a manual verdict.

**Done when:** a run on the Mac with Gemini at two thinking levels (and Ollama if item 6 is done) produces the page for Agent Wire and one other task; the number of real calls is printed before it starts and kept small by default (`--limit`); tests cover input building and the checks with fakes; plan §7.4 says how to run it.

---

## 8. Fold `OPEN_MODEL_RESEARCH.md` into plan §7 [DONE 2026-10-28]

**Goal:** plan §7 says what the research found, so nobody has to reconcile the two.

**Folded into `PC_MIGRATION_PLAN.md` §7:**
* **Top note removed:** removed out-of-line correction note; §7 is now self-contained and authoritative.
* **§7.2 & Table:** added named candidates (Gemma 4 26B-A4B, Qwen3.8-27B, LFM2.5-24B-A2B, Qwen3.6-35B-A3B, Nemotron 3 Nano, gpt-oss-20b, Gemma 4 E2B/E4B, LFM3-1.7B). Detailed MoE expert offload (`--n-cpu-moe` in llama.cpp) where ~2–4B active params stream over PCIe while attention and shared weights stay in 16GB VRAM, achieving viable speeds on 32GB DDR5 system RAM.
* **§7.3:** added hosted open-weight models (DeepSeek V4.1 Flash, GLM-5.3-Flash) as tested alternatives for newsroom articles due to the 2027-01-01 Gemini price doubling ($0.75 → $1.50 in, $3.75 → $7.50 out). Detailed OpenAI-compatible tool loop port requirements.
* **§7.4:** added the 5-step evaluation order from `OPEN_MODEL_RESEARCH.md` §5 alongside quality gates (voice, facts, structure, thinking).
* **§7.5:** updated beside-Madden execution to reflect that resident ~1–2GB models (LFM3-1.7B, Gemma 4 E2B/E4B) stay resident in VRAM alongside Madden for interactive structured calls (GM negotiations, custom commission classification) without waiting for nightly queue or cloud fallback.
* **Doc alignment:** updated `OPEN_MODEL_RESEARCH.md` §6, `PC_MIGRATION_PLAN.md` §2.4 checklist, and `PROJECT_SPEC.md` doc descriptions.

---

## 9. League memory (V2.8)

**Goal:** a search index over what the universe has said and done, so writers (and later players, press and broadcast) can pull real callbacks: "the last time Carolina came back from 17 down was 2031".

**Corpus (live counts 2026-10-28):** `news_feed` (2,814 articles; check where full article text lives), `agent_memory_entries` (3,247 writer takes), `franchise_records` (711) and `league_record_entries` (692), plus Agent Wire posts, season archive and game scripts where they're stored. Facts with exact numbers (records, stats) stay queries on the stat tables; the index is for narrative recall.

**Design:**
* **Storage:** the `madden-db` container is PostgreSQL 14 without `pgvector` (only `pg_trgm` is available). Default: a `league_memory` table (source table, source id, season, week, team ids, player ids, text chunk, embedding as `REAL[]`) and brute-force cosine in numpy, which is fast at tens of thousands of chunks. Switching the image to `pgvector/pgvector:pg14` is cleaner but changes infrastructure for every session: **ask JR before doing it.**
* **Embeddings:** Gemini embedding API now (metered into `llm_usage`, source `memory_index`), behind a seam so a local embedding model can replace it on the PC. Record the model name per row; re-embed when it changes.
* **Updates:** an incremental `python3 -m newsroom.league_memory index` that embeds only new or changed rows, run after the weekly articles.
* **Use:** a writer tool (`search_league_memory(query, team?, player?, seasons?)`) registered with the newsroom's tool loop like the existing writer tools, returning short cited snippets (headline, date, author). Facts in an answer must still come from the data tools.

**Done when:** the full backfill runs once on `madden_test` (report chunk count and embedding cost first; cost estimate before running on live), queries return sensible callbacks for a handful of test questions in the docstring, the writer tool is wired in and covered by tests with a fake embedder, and the spec documents the table, CLI and tool. Plan V2.8 updated.

---

## 10. Personality plan phases 2 and 3 (V2.13)

**Goal:** personas become data (tables, a full trait catalogue, seeding), and GMs get them.

**Read first:** `docs/PERSONALITY_PLAN.md` in full, especially §4 (sliders, traits, views, wants and fears, mood, relationships, memory), §9 phases and §10 decisions. Phase 1 is built: `newsroom/config/agent_personas.json` (writer personas as sliders, traits, views with triggers, signature pools, want/fear), `newsroom/config/persona_traits.json` (started catalogue), `newsroom/personas.py` (active views, avoid list). Phase 1's side-by-side check exists as `tools/persona_side_by_side.py`.

**Phase 2: catalogue and tables.**
1. Finish `persona_traits.json`: every category and example in §4.2, each with roles and odds per role, conflicts, category, prompt text with when it applies, and optional engine effects (progression plan §6–§7 entries included, effects at 0 for now). Check the role odds give sensible mixes.
2. Tables (one migration): persona sliders, persona traits, views, relationships and trait history, keyed by an agent id that works for writers, GMs, players, coaches, owners and scouts (persona type + id).
3. Seeding: deterministic (seeded per agent), honours the slider budget (D1: six sliders, 30 of 60), traits per role (D2: 3–5), at most two per category, no conflicts, stratified so the league has a spread. Writers are seeded from their existing JSON, not re-rolled.
4. A JR review screen in `#debug` (server/debug_api.py and the debug page) to view and edit personas before first use (D4).
5. `newsroom/personas.py` reads from the tables, with the JSON as the seed source.

**Phase 3: GMs.** Sliders and traits on top of `gm_profiles` (and `engines/gm_personas.py`), GM memory of deals and talks, relationships between GMs and with JR. Traits shape GM text and, where §6 of the plan says so, choices inside what the trade engine allows; the trade engine's numbers don't change.

**Decisions:** §10 lists D1–D7 with recommendations. Use the recommendations; ask JR before showing traits on the public site (D3) and before applying real-world writers' and coaches' traits (D5).

**Done when:** migration applied to `madden_test`, seeding is reproducible, the catalogue passes a validator (budgets, conflicts, odds), writers' prompts are unchanged in content when read from the tables (test against the phase-1 output), GMs have personas used in their prompts, tests pass, and the personality plan's status, PROJECT_SPEC (spec §7.11 and tables) and plan V2.13 are updated. Applying to live `madden` needs JR's OK.

---

## 11. Progression plan phase C (V2.14) [DONE 2026-10-28]

**Goal:** `engines/progression.py` decides each player's yearly OVR change and spreads it over ratings, offline, backtested against what Madden did.

**Completed implementation (2026-10-28):**
1. **OVR Change Layer (§5.2):**
   - Formula: $\Delta\text{OVR}_i = \text{age}(\text{group}, \text{age}) + \text{dev}(\text{dev\_trait}, \text{age}) + \text{pull}(\text{ceiling}_i - \text{OVR}_i) + \text{play}(\text{snaps/starts}) + \text{injury}(\text{games missed, IR}) + \varepsilon_i$.
   - Seeded noise: `random.Random(f"prog:{season}:{player_id}").gauss(0, 1.0)`.
   - Age aging fit from `engines/trade_value.py`, dev trait bonuses and veteran decline resistance, hidden ceilings from `scout_prospects.true_value` or deterministic seeding, playing time proxy from `season_stat_general`, and injury penalties from `injuries`.
   - Clamped to age and OVR bounds (99 capped, max ±8 OVR per season).
   - Stored in full breakdown dict (`age`, `dev`, `ceiling`, `play`, `injury`, `noise`) and cause tags (`AGE`, `BREAKOUT`, `PLAYING_TIME`, `INJURY`, `CEILING`, `DEVELOPED`, `REGRESSED`, `ON_TRACK`, `STALLED`).
2. **Ratings Layer (§5.4):**
   - Physical ratings (`Speed`, `Acceleration`, `Agility`, `Strength`, `Jumping`, `Stamina`, `ChangeOfDirection`) follow age decline curves and injury severity.
   - Skill ratings adjusted via `engines/ovr_weights.adjust_ratings` to reach target OVR, verified with `settle_archetype` and `predict_ovr` so the primary archetype remains on top.
3. **CLI & Engine Interface:**
   - `python3 -m engines.progression --season S [--commit] [--dry-run]`
   - `--backtest`: 2035→2036 backtest across 1,385 matched players: predicted mean ΔOVR is -0.095 vs actual -0.147 (difference 0.052, within the 0.15 limit); MAE 1.119; RMSE 1.491; within ±1 accuracy 73.0%.
   - `--simulate 5`: 5-season forward simulation verifies league stability bounds (§5.5): 90+ player count stays in [45, 180] (Season 5: 54), starter average OVR stays in [80.5, 88.0] (Season 5: 81.46), average league OVR stays in [74.0, 80.5] (Season 5: 75.19), average age in [25.5, 28.5] (Season 5: 27.41).
4. **Database Storage:**
   - Migration `db/migrations/2027_01_08_player_progression.sql` created `player_progression_history` and `player_ceilings`, tested and verified on `madden_test`.
5. **Testing:**
   - `tests/test_progression.py`: 8 tests covering terms, clamping, reproducible seeding, ratings adjustments, simulation stability, backtest accuracy, and database persistence.

