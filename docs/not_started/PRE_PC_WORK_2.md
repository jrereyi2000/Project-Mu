# Project Mu: Pre-PC Work, Round 2 (sorted by what each item needs)

**Status:** audit only, nothing built (2026-10-28). Round 1 (the 11 items in [`PC_MIGRATION_PLAN.md`](../in_progress/PC_MIGRATION_PLAN.md) §2.4, briefs in [`PRE_PC_WORK.md`](../done/PRE_PC_WORK.md)) is done. This is what's still left that doesn't need the PC, sorted so some of it can be built on another device.

**The sort:**
* **A. Standalone:** needs neither the repo, the database nor Madden. Build it anywhere as a small package with the interface given here, then bring it in.
* **B. Repo + sample save, no database:** clone from GitHub, copy over `CAREER-GIANTS-AUTOSAVE` (git-ignored) and run Node + Python. Postgres isn't needed.
* **C. Needs the database:** live `madden`, or `madden_test` restored from a dump. Some items also need the Gemini key, the running weekly pipeline or JR's decisions.
* **D. Mac/PS5 only:** can't be handed off.

---

## Working on another device

1. **Push first.** Local `main` has commits GitHub doesn't (7 as of 2026-10-28, including progression C2 and the fantasy K/DST work). Another device clones whatever is on GitHub.
2. **Context comes from the repo:** `CLAUDE.md` tells the agent to read `PROJECT_SPEC.md` first, and this doc plus the plan sections it links give each item's background. Point the agent at one item here.
3. **Git-ignored files to copy by hand, only for the tiers that need them:**

| File | Needed for |
| :--- | :--- |
| `CAREER-GIANTS-AUTOSAVE` (repo root) | B |
| `.env` (Gemini key, DB URL) | C, and A7/A9 if they run real calls |
| `db/backups/madden_backup_*.sql` → `./db/restore_db.sh`, then `pytest --reclone` for `madden_test` | C |
| `static/players/*.webp` (headshots) | A1, A2, A5 test inputs (a handful is enough) |
| `data/commentary_m27.json`, `data/commentary_save_m27.json` | only for teleport dry runs |

4. **Branch, don't commit to `main`, on the other device.** Push the branch; merging it into `main` happens here (CLAUDE.md commit rule). A and B items touch new files almost entirely, so they merge cleanly. C items edit `PROJECT_SPEC.md`, `db/init_db.sql` and plan docs, which other sessions edit here too, so expect text conflicts.
5. **Standalone packages land as their own folder** (`headshots/local/`, `broadcast/`, …) with a README stating the interface. Wiring one into the pipeline and the spec is a small B/C job afterwards.

---

## A. Standalone (build anywhere, import later)

Each one is a function boundary: Project Mu calls in with plain data and gets plain data back. Nothing inside needs Project Mu.

| # | Item | Interface | Where it plugs in | Notes |
| :--- | :--- | :--- | :--- | :--- |
| A1 | **Local headshot generator** (§7.7) | `generate_headshot(prompt: str, ref_image: bytes \| None, seed: int, size: int = 1024) -> bytes` (PNG) | Replaces the `generate_content` call in `headshots/make_headshot.py` and `headshots/rookie_headshots.py`. Prompts, `rembg` cutout and WebP output stay | Needs a GPU with ~12–16GB VRAM (ComfyUI or `diffusers`). Add a pose/depth ControlNet so every head has the same size, angle and crop (§9.4 needs that). Quality gate: one rookie class rendered both ways (§7.4). The prompt template is `IMAGE_PROMPT` in `make_headshot.py`; copy a few filled-in prompts out as test inputs |
| A2 | **Photography with consistent faces** (V2.4) | `render_scene(face_ref: bytes, scene_prompt: str, colors: dict, seed: int) -> bytes` | Later: draft-night, award, retirement and feature-lead photos | Builds on A1 with IP-Adapter / face-ID. Test with a few headshots as references |
| A3 | **TTS and audio production** (§9, V2.6) | `synthesize(text, voice_id, style=None) -> wav`; `produce(segments: list[{speaker, text, pause_ms}], bed: path \| None, out: path)`; a voice registry (id → engine settings) | Pregame show (§9.1), radio and podcasts (V2.6), live color layer (§9.2) | Original synthetic voices only, never a clone of a real person (§9.3). Measure seconds of audio per second of compute locally (it runs on the nightly queue) and latency in the cloud (the live layer allows 2–4 s) |
| A4 | **Scorebug reader** (§9.2) | `read_scorebug(frame: ndarray) -> {home, away, quarter, clock, down, distance, possession, timeouts}`; `events(prev, cur) -> list[event]` (score change, turnover, quarter end, two-minute warning, 3rd/4th-and-long conversion, red-zone stand) | Live color layer | Same game on PS5 and PC, so the scorebug is the same. Sample frames can be grabbed over Remote Play now (D2); work in coordinates relative to frame size because the PC resolution will differ. No LLM: templates, as `pipeline/attribute_reader.py` does |
| A5 | **Portrait converter** (§9.4 step 3) | `to_portrait(webp: bytes, width: int, height: int, fmt: str) -> bytes` | Mod build for in-game portraits | Size and texture format (likely DDS, BC3/BC7) come from the one-portrait spike on the PC, so keep both as parameters |
| A6 | **OpenAI-compatible tool loop** (§7.3, §7.4 step 2) | `run_tool_loop(client, model, system: str, messages: list, tools: dict[name, callable], tool_schemas: list, max_steps: int) -> (text, usage)` with `usage` = {input, cached, output, thinking} | `newsroom/swarm_dispatcher.py` (the Gemini loop is near line 1494), so national and beat articles can run on DeepSeek V4.1 Flash or GLM-5.3-Flash | Test against the hosted APIs with toy tools (a fake stats lookup) over 50-step loops: tool-call reliability, parallel calls, malformed arguments, retries. Wiring it into the dispatcher and the newsroom week test is C6 |
| A7 | **Nightly GPU queue** (§7.8) | `enqueue(Job(kind, model, payload))`; `run_queue(store, is_madden_running, load_model, unload_model)`: one model at a time, in the §7.8 order, and only while Madden isn't running or after an idle timeout | `pipeline/watch_save.py` (hook point already there) | Keep the store pluggable (SQLite or JSON to start; a Postgres table when it's wired in). `is_madden_running` on WSL2 can call `tasklist.exe`; take it as a parameter |
| A8 | **Local model bench** (§2.5, §7.2) | A script giving tokens/s, VRAM and time-to-first-token per model, quant and context size, with thinking on and off | Picks the models for §7.4 | Raw speed only; the quality test needs the database (C7). Results are most useful from a 16GB NVIDIA card |
| A9 | *On hold (JR):* CFB → Madden rating curve (§6) | `translate(cfb_ratings: dict, class_rank: int, position: str) -> (madden_ratings, dev_trait)` | CFB 27 bridge | Only if a dynasty gets planned. Compare with the MMC *Pipeline* tool first |

---

## B. Repo + sample save, no database

Build and check these against `CAREER-GIANTS-AUTOSAVE` with read-back, the way `release.mjs` and the teleporter were built. Whether Madden accepts the result stays a PC check.

| # | Item | What to build | Done when |
| :--- | :--- | :--- | :--- |
| B1 | **Writer `sign` and `trade` moves** (League Office §3.1, the offline part of phase B) | `pipeline/save_reader/sign.mjs` (the reverse of `release.mjs`: team, ContractStatus, a new or reused `PlayerContract` row, into `Roster`/`PracticeSquad` and the depth chart, out of `League.FreeAgents`, roster counts) and `trade.mjs` (release from one team + sign to another with the contract kept; picks through `record_edits.mjs`), as edit kinds in `save_writer.mjs` | On a copy of the sample save: sign a released player, trade a player and a pick, read back; every team list, depth chart and count is consistent and nothing else changed. Tests in `pipeline/save_reader/test/` |
| B2 | **Project Mu news in Madden's news tables** (V2.1) | `news_edits.mjs` writing `NewsCenterArticle` / `NewsCenterArticleData` rows from JSON `{headline, author, player_pgids, team, week, year, breaking}`; the edit kind in `save_writer.mjs` | Rows written and read back on a copy. (The feed from `news_feed` is a small C job; whether Madden shows them and keeps them across Advance Week is a PC check) |
| B3 | **Season-year and XP-slider edits** (§4.2 option 1, progression §3) | Edits for `CurrentSeasonYear`, `BaseCalendarYear`, `BaseSuperBowlNumber` (compare with the M27 `seasonYearSetup.exe` in MMC_MOD_SURVEY §2) and `ProgressionXPSlider` rows at 0 | Fields written and read back on a copy, nothing else changed. Side effects (draft class year, contract labels, schedule) are PC checks |
| B4 | **OVR weights cross-check** (§4.4 prior art) | Compare `engines/ovr_weights_m27.json` with the Madden Roster Editor's `config/archetype_breakdown.xlsx`, mainly for the weak groups (FB, K, P, LS) | A short note in §4.4 and, if the community weights fit the sample save better for a group, a fallback for that group |

---

## C. Needs the database

Do these here, or on the other device after restoring a dump into `madden_test`. Everything stays offline (no save writes) until the PC.

**Engines (offline, like progression phase C):**

| # | Item | Plan | Notes |
| :--- | :--- | :--- | :--- |
| C1 | Progression C2, the rest: durability and mileage, ceiling bands and breakthroughs, peak upkeep, scheme continuity and mentors, the `PT_*` model as stored outcomes, new tables | [`PROGRESSION_PLAN.md`](../in_progress/PROGRESSION_PLAN.md) §5.6–§5.13, phase C2 | Backtest and `--simulate` must still pass. Uses the sample save's `Original*Rating` and `PT_*` too |
| C2 | Player and coach personas as data (personality phase 4; progression F and G as data, terms at 0) | [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §9, progression §6–§7 | Stars and captains first, then coaches and coordinators. Writers can use them right away; V2.2, V2.3 and the League Office build on it |
| C3 | Coach-set depth charts and media award ballots, offline (League Office phases E and G) | [`LEAGUE_OFFICE_PLAN.md`](LEAGUE_OFFICE_PLAN.md) §4.7, §4.10 | Can run on 2036 now and be compared with Madden's depth charts and the real 2036 awards |
| C4 | Transactions engine, offline (League Office phase D) | League Office §4.4–§4.6, §4.11 | Largest item. Decisions L1–L7 should be settled first |

**Newsroom and site features (V2), all of which read the database and add to `newsroom/` and `Mu_Network`:**

| # | Item | Notes |
| :--- | :--- | :--- |
| C5 | V2.7 a recap for every game | Possible now on today's box scores; better once the save gives real per-game lines and scoring plays |
| C6 | Wire A6 into the newsroom and run one week of articles on hosted open models side by side with Gemini (§7.4 step 2) | Costs model calls; JR's go-ahead |
| C7 | Quality runs JR planned: thinking caps (§7.9, ~40 calls), personality phase 1 side by side, local models through `tools/llm_compare.py` once A8 picks them | JR runs or approves |
| C8 | V2.12 narratives (arc state across weeks and seasons) | Inputs exist: game scripts, records, hot seat, archive. Feeds V2.2, V2.3, V2.9 |
| C9 | V2.2 press conferences, V2.3 player voices | After C2 |
| C10 | V2.9 inbox, V2.11 free-form negotiation talk | The talk can be built now inside the trade engine's checks; carrying out the trades waits on B1 and the PC |
| C11 | V2.10 scouting revamp, V2.5 fans | Lower in the V2.16 order |

**Small teleport fixes (do before the renames are committed, D3):**

| # | Item |
| :--- | :--- |
| C12 | `teleport_appearance.py` seeds from the original name in `player_name_aliases`, so the 116 renamed players keep the head that matches their headshot (§4.3 Commentary IDs, step 5) |
| C13 | A matching cost in `teleport.py` that steers contracted players to Signed donors, recovering most of the 203 contracts that land on practice-squad donors (§4.3 Contracts) |

---

## D. Mac/PS5 only

| # | Item |
| :--- | :--- |
| D1 | Finish 2036 through the Week 23 slate (§2.1); then the full draft order for `PickNumber` (§4.3) |
| D2 | Final PS5 attribute scan, as late as possible (§2.3). Grab scorebug frames for A4 while Madden is up |
| D3 | `commentary_names apply captures/commentary_reviewed.csv --commit` after the last capture and before the teleport (after C12) |
| D4 | `./db/backup_db.sh` as the last step on the Mac (§2.2) |

---

## Still waiting on the PC

All of §5.4, progression phase A, League Office phase A, the one-portrait spike (§9.4), applying `madden_pgid` to live `madden`, `team_cap_seasons` units, the first-year contract check, 2036 → 2037 retirements (continuity C5), and the decisions that need a PC test (progression D13, League Office L1).
