# Project Mu: Pre-PC Work Briefs

**Status:** 11 items, 1 completed (2026-10-28). The checklist is [`PC_MIGRATION_PLAN.md`](PC_MIGRATION_PLAN.md) §2.4. Each brief below is written so an agent can take it on its own: goal, what exists, where to work, steps, and when it's done.

Nothing here needs the PC or a real 2037 save. On hold, not briefed: `madden_pgid` on the live database (after the PC move), the CFB 27 bridge (§6), the pregame show (§9.1) and the rest of Part 2.

## Rules for every item

* **Read `PROJECT_SPEC.md` first** (CLAUDE.md). Update it in the same change: Repository Layout tree, tables, CLI flags, behaviour. Tick the item in `PC_MIGRATION_PLAN.md` §2.4 and update the plan section the item points at.
* **Live database is read-only for agents** unless the brief says a write step needs JR's OK. Tests can't reach `madden` (`tests/conftest.py` blocks it); DB tests are `@pytest.mark.integration` against `madden_test`. New migrations go in `db/migrations/` (dated name, `IF NOT EXISTS`), applied to `madden_test` first.
* **Shared checkout:** other sessions edit `PROJECT_SPEC.md`, `db/init_db` and plan docs at the same time. Stage only your own hunks (`git add -p`); commit on `main` (or merge your branch into `main` right away); don't push; stop on a merge conflict in code.
* **Sample PC save:** `CAREER-GIANTS-AUTOSAVE` at the repo root (Madden 27, franchise year 2027). Snapshots aren't kept; make one into your scratch space with `node pipeline/save_reader/pc_save_reader.mjs CAREER-GIANTS-AUTOSAVE --out <scratch>/snapshot.json`. Never write the save itself; `save_writer.mjs` writes copies only.
* **Model calls cost money.** Unit tests use fake callables, never Gemini. A brief that needs real calls says how many.
* **Hallucination policy:** prose may invent untracked narrative, never contradict data. Engines decide numbers; models write words.

---

## 1. QB OVR refit

**Goal:** the Madden 27 OVR formulas in `engines/ovr_weights_m27.json` predict PS5-scanned QBs within ±1, without getting worse on the sample save.

**What's wrong:** each QB archetype (`QB_FieldGeneral`, `QB_Improviser`, `QB_Scrambler`, `QB_StrongArm`) is a quadratic fit on 145 sample-save QBs × 4 grades, and leans on `LongSnap` (e.g. `QB_StrongArm`: squared weight 1.51 per LongSnap²/100). On the sample save LongSnap is ~10 for most QBs (0–6 for some, 91 for one legend), so it works as a group marker; the `engines/ovr_weights.py` docstring notes dropping it costs 93% → 62% within ±1 there. The PS5 QBs have LongSnap 15–58, so the squared term explodes: 3 of 78 scanned QBs pass, predictions 0–50 too high. With LongSnap pinned at 10, 58 of 78 are within ±1 (range −4 to +3).

**Data:**
* Training: the sample-save snapshot (above), `python3 -m engines.ovr_weights fit <snapshot> --out <scratch>/weights.json`; `show` prints accuracy.
* Held-out check: `player_attributes` in live `madden`, 78 rows with `position = 'QB'`; `ratings` JSONB uses the same keys as the formulas (`LongSnap`, `ThrowPower`, …), `overall_rating` is the OVR Madden showed. Read-only. A shown 99 accepts any prediction ≥ 99.

**Approaches to compare** (pick by both checks, simplest that passes):
1. Clamp the marker in prediction: feed `min(LongSnap, cap)` to QB formulas (cap ~10–15), keeping the fit as is.
2. Refit QBs with LongSnap linear-only, or as an indicator (LongSnap below ~8), not squared.
3. Refit without LongSnap and add other terms (e.g. squared Awareness) to recover the sample accuracy.

Apply the same check to the other misses in the scan (LS 0 of 39 pass, K 18/35, P 20/34, FB 24/36), but only fix them if it's cheap; the brief is QBs.

**Done when:**
* Scanned QBs ≥ 90% within ±1 (report the distribution).
* Sample-save QB cross-validation within ±1 no worse than now (0.931 / 0.959 / 0.883 / 0.979 for the four archetypes) and every other archetype unchanged.
* `adjust_ratings` still never moves LongSnap for QBs (it's not in `skills`).
* `pytest tests/test_attribute_reader.py` and the ovr_weights/teleport tests pass; the teleporter dry run (`python3 -m pipeline.teleport <snapshot> --report …`) still puts all 1,706 players with an OVR on target.
* The docstring of `engines/ovr_weights.py`, plan §2.3 and §4.4 and the spec say what changed.
* Optional, **JR's OK first:** refresh the stored checks with `python3 -m pipeline.attribute_reader 2026-10-28_attributes` and `python3 -m pipeline.load_attributes 2026-10-28_attributes --commit` (replaces that folder's rows in live `player_attributes`).

---

## 2. Surname rename tool (commentary IDs)

**Goal:** every Project Mu player has a surname Madden's commentators can say. Players with a recorded surname get its ID; the rest get a ranked list of recorded surnames that JR reviews, and the approved renames are written to the database.

**Background:** plan §4.3 "Commentary IDs" and its 5-step surname rule. `Player.PLYR_COMMENT` holds one ID per surname (Smith 4600, Johnson 2506, Anderson and Andersen 103); 0 means no recording and the commentators avoid the name. Today `pipeline/teleport.py:204` keeps the donor's ID only when the donor has the same surname, else 0.

**Source list:** [madden-franchise-utils](https://github.com/Sinthros/madden-franchise-utils) `Utils/JsonLookups/27/playerLookups/commentary.json` (7,865 surnames → 7,666 IDs, `{"Abdullah": 4, …}`). The repo has no license, so don't commit the file: fetch it into the git-ignored `data/` (e.g. `data/commentary_m27.json`) with a documented `curl` from `raw.githubusercontent.com`, and fail clearly when it's missing. Their `commentaryIdFix/commentaryIdFix.js` shows the matching they use: lowercase, strip suffixes, else 0.

**Coverage today:** active players = latest `player_seasons` season, `players.retired_season IS NULL`, name `COALESCE(full_name, player_name)` (`player_name` is the short 'A. Manning' form). After dropping Jr/Sr/II/III/IV/V and folding case and accents, 1,663 of 1,829 match (91%); ~165 don't. Skip the 32 team-defense rows (position `DEF`, name like 'D/ST').

**Build** `pipeline/commentary_names.py`:
1. `normalize(surname)`: drop suffixes, casefold, strip accents; try the lookup with the punctuation as written (it has 'Abdul-Quddus') and without.
2. `commentary_id(full_name) -> int | None`.
3. `report` subcommand: coverage numbers, and a CSV for the unmatched with player_id, name, team, position, OVR and the top 5 recorded surnames, ranked by sound first (Double Metaphone; add `metaphone` or `jellyfish` to requirements) then spelling (edit distance). Prefer same first letter and similar length, and avoid surnames already on the same team. Leave a `chosen` column for JR.
4. `apply <reviewed.csv> [--commit]`: writes the new surname to `players.full_name` and the matching `player_name`, and keeps the old name as an alias. There's no alias table yet: add one (`player_name_aliases`: player_id, alias, kind, created_at) via a migration, and make player search use it if `server/` has a name search. Dry run by default; **`--commit` on live needs JR's OK.**
5. `pipeline/teleport.py` and `pipeline/teleport_draft.py` set `PLYR_COMMENT` from `commentary_id()` (0 when unmatched), and the teleport report counts matched / unmatched.

Draft prospects (`scout_prospects`) go through the same `report` so 2037 rookies can be renamed before they arrive.

**Not in scope:** rewriting names in past articles (plan §4.3 leaves that for migration time).

**Done when:** `report` reproduces ~1,663 / 1,829 and writes the review CSV; unit tests cover normalization, suffixes, accents, punctuation and ranking; `apply` dry run on `madden_test` changes only the listed players and records aliases; the teleporter writes looked-up IDs; spec and plan §4.3 updated.

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

## 4. `pipeline/watch_save.py`

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

## 5. Thinking caps on the short Gemini calls

**Goal:** stop paying for ~1,000 thinking tokens per short call (plan §7.1, §7.9).

**Where the calls are:**
* `newsroom/agent_memory.py` `gemini_text()` (takes extraction from `newsroom/swarm_dispatcher.py:1800`, season summaries in `refresh_season_summaries`): no thinking config.
* `engines/gm_llm.py` `GeminiText` (labels `gm_persona`, `gm_rationale`, `gm_negotiation`, and `scouting_notes` from `engines/scouting_narrative.py`): no thinking config.
* Agent Wire already supports it: `newsroom/social_writer.py` `write_post` reads a `model:level` string (`gemini-3.8-flash:low`) into `ThinkingConfig(thinking_level=…)`, and `newsroom/config/social_desk.json` can set `model_by_kind`. Check what Agent Wire runs with today and leave its setting to JR.

**Steps:**
1. Use the same `model:level` convention in `gemini_text` and `GeminiText` (one small helper both import, e.g. in `db/llm_usage.py` or a new `engines/llm_config.py`), so a level can be set per label.
2. Defaults: `low` for agent memory and scouting notes; for GM calls pick `low` or keep the current default after the check below (negotiation messages must stay valid for `gm_negotiation`'s validators).
3. Check quality cheaply: about 10 calls per label at the old and new level on real inputs (≈ 80 calls), compare text and `llm_usage` thinking tokens, and write the numbers into plan §7.9.

**Done when:** the levels are configurable, the defaults are set, unit tests (fake clients) show the config reaches the request, the comparison numbers are in §7.9, and the spec notes the setting.

---

## 6. Gemini-or-local provider switch

**Goal:** each short-task caller can use Gemini or a local Ollama model, falls back to Gemini when the local one is down, and logs both to `llm_usage` (plan §7.3).

**Callers:** the ones in item 5 plus Agent Wire (`newsroom/social_writer.py` `write_post`, called from `newsroom/social_publish.py`). Newsroom articles stay on Gemini.

**Design:**
* One provider seam returning text plus token usage: `gemini` (as today) and `ollama` (HTTP `POST http://<host>:11434/api/generate` or `/api/chat`, `"stream": false`; JSON-schema `format` for the GM calls; host from `.env`, default `localhost`; on WSL2 it's the Windows host).
* Choose per label in one config (env or a small JSON), e.g. `agent_wire=ollama:qwen3.8-27b` with fallback `gemini-3.8-flash:low`. Fallback on connection error or timeout; never on a bad answer (the callers' validators already handle those).
* `llm_usage.source` is `VARCHAR(16)`; keep sources as they are and put the local model's name in `model` (e.g. `ollama/qwen3.8-27b`). Ollama returns `prompt_eval_count` and `eval_count`; map them to input and output tokens, thinking 0 unless the model reports it. Log the same as Gemini calls (`log_response` / `UsageMeter` style).
* Keep the existing call sites' signatures: `gm_llm.make_llm(label)` still returns a prompt → text callable; `agent_memory.gemini_text` gets a provider-neutral sibling.

**Done when:** unit tests cover provider choice, fallback on a dead Ollama and usage rows for both (fake HTTP); a manual run on the Mac with Ollama (`brew install ollama`, any small instruct model) writes a few local rows to `llm_usage` in `madden_test`; spec and plan §7.3 updated. No caller switches to local by default: that waits for item 7's test.

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

## 8. Fold `OPEN_MODEL_RESEARCH.md` into plan §7

**Goal:** plan §7 says what the research found, so nobody has to reconcile the two.

**Do:** apply `docs/OPEN_MODEL_RESEARCH.md` §6 "Corrections" to `PC_MIGRATION_PLAN.md` §7.2 (MoE models with ~3B active make CPU offload useful for 30–35B models), §7.3 (hosted open-weight models as a tested alternative for articles, because of the 2027 Gemini price change), §7.5 (a 1–2GB model may stay loaded beside Madden for structured calls). Add the named candidates from research §3 to §7.2's table and the evaluation order from research §5 to §7.4. Edit the sections in place (no changelog); remove the "which also corrects §7.2, §7.3 and §7.5" note at the top of §7 once done. Check other docs for references (`grep -rn "OPEN_MODEL_RESEARCH\|§7\." docs`).

**Done when:** §7 reads as one consistent plan with the research's numbers and candidates, the research doc's §6 says it has been applied, and the spec's line for both docs is current. Docs only, no code.

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

## 11. Progression plan phase C (V2.14)

**Goal:** `engines/progression.py` decides each player's yearly OVR change and spreads it over ratings, offline, backtested against what Madden did.

**Read first:** `docs/PROGRESSION_PLAN.md`, especially §4 (inputs), §5 (engine: the ΔOVR terms, ceiling, when it runs, which ratings move, league stability, what it stores, dev traits) and §8 phase C. Personality and coach terms stay at 0.

**Inputs that exist:** `player_seasons` (OVR, age, position, archetype, dev trait by season); `engines/dev_quality.py` (ridge fit of expected change by age, OVR band, dev and rookie: the calibration target); `engines/trade_value.py` aging fit by position group; `scout_prospects.true_value` (hidden ceiling for draftees: never expose it in views, writer tools or `#debug`); `injuries`; per-game snaps and grades only on the PC, so use games played / starts from today's stats as the playing-time proxy and note it; `engines/ovr_weights.py` `adjust_ratings` / `predict_ovr` for the ratings layer; `player_attributes` (the PS5 scan) as each player's starting ratings.

**Build:**
1. ΔOVR per player from the §5.2 terms, clamped per age and OVR band, seeded noise `random.Random(f"prog:{season}:{player_id}")`, each term stored separately so writers can cite it.
2. Ratings layer: physical ratings follow age, skill and mental ratings grow later (§5.4); only each archetype's `skills` move; the archetype stays on top.
3. `--simulate N`: N seasons ahead on a copy with seeded rookie classes and retirements, reporting league shape (counts of 90+, mean starter OVR by position, age mix) against 2036 (§5.5).
4. Backtest on the 2035→2036 offseason (and 2036→2037 if the PS5 franchise is advanced past progression): predicted vs actual ΔOVR per player, error by age band, OVR band and position, and the league-mean comparison with `dev_quality`.
5. Storage as §5.6 describes, in `madden_test` only; nothing writes a save in this phase.

**Done when:** the backtest report (in the docstring or `verify_output/`) shows league means within the stability limits and per-player error, `--simulate 5` shows no drift past the limits, tests cover the terms, clamps, seeding and the ratings layer, and the progression plan's status, PROJECT_SPEC and plan V2.14 are updated. Decisions D1–D7 in progression §9 that this phase touches (D3, D4) use the recommendations unless JR has decided otherwise.
