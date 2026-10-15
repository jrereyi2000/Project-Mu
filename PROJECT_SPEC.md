# Project Mu: Automated Media Engine & Simulation Network

## 1. System Overview
An automated, event-driven platform and media ecosystem for a simulated NFL universe. The architecture combines:
1. **Canonical Temporal PostgreSQL Database**: Real-time snapshot ledgers storing game states, team standings, player stats, cultural metrics, and fantasy projections.
2. **FastAPI Backend (`server/api.py`)**: High-performance RESTful API powering schedules, box scores, team hubs, player profiles, and fantasy analytics.
3. **Advanced Quantitative Analytics**:
   - **Excitement Index ($E$)**: Scores games (0–100) based on pace, margin, and star performances.
   - **Team Watchability Index ($TWI$)**: Measures continuous consumer entertainment value and chaos volatility.
   - **Franchise Popularity Index ($POP$)** & **Player Popularity ($Pop_{\text{player}}$)**.
   - **Fantasy Defensive Strength vs. Position**: Quantile-balanced 5-star matchup and Strength of Schedule (SOS) ratings.
4. **Agent Swarm Architecture (192+ Agents)**: Autonomous journalists, beat reporters, and pundits generating contextual articles and social posts.
5. **Interactive UI (`Mu_Network`)**: A React 19 + TypeScript + Vite frontend featuring Gridiron Newsroom, Agent Wire Social, and Mu Fantasy Analytics.

### 1.1 Repository Layout
The project root is the Python import root. Every backend folder is a package, and modules import each other by absolute package path (e.g. `from db.db_handler import DatabaseHandler`, `from newsroom.swarm_dispatcher import SwarmDispatcher`).

```
Project Mu/
├── PROJECT_SPEC.md            # This document (system-level spec)
├── CLAUDE.md                  # Claude Code session rules (read this spec first; keep it updated)
├── madden_capture.py          # Remote Play capture (macOS + chiaki-ng): drives Madden franchise menus with held keypresses,
│                              #   checks every screen via Apple Vision OCR, screenshots Season Stats (NFL scope, all categories;
│                              #   Madden lists the top ~270 per category; Passing/Defense/Kicking: each page also captured scrolled right at the same row position, *_right_NNN.png)
│                              #   Team Stats tabs Conversions / Red Zone Efficiency / Penalties (NFL scope, 32 teams),
│                              #   and the previous week's League Schedule (week picked via the held-L2 dropdown) → captures/<YYYY-MM-DD_HHMM>/{stats,team_stats,schedule}/
│                              #   Cross on the League Schedule list only opens a played game's box score (week before the current one, BOX SCORE button, no ACTIONS, FORCE WIN only as the column header, highlighted row has a final score)
│                              #   `--only games [--week ...] [--games N]`: every game of a week (default the previous one): League Schedule → guarded Cross → box score
│                              #   (box_top.png, box_bottom.png: 17 team-stat rows) → R1 player stats (TEAM column, 10 categories via R2, 6-row page steps; Kicking/Defense
│                              #   captured at both edges, end judged on the named half) → Back → next game; games/<NN>_<away>_at_<home>/{box_*.png,stats/}, games/games.txt. ~7 min a game (wide tables crossed by holding Left/Right; lists stop at all-zero pages), so it's only for recovering a skipped week
│                              #   Every mode survives a frozen chiaki-ng stream: on a failure, a harmless test press (Down/Up on lists, Right/Left on tiles, R1/L1 on the hub) that doesn't
│                              #   change the picture means frozen → force-quit + relaunch (8 restarts per run, shared by all modes) and the mode is redone: stats/team/schedule/season-schedule
│                              #   from scratch (their folders cleared), games resuming per game (games/<NN>/DONE), rosters resuming per team
│                              #   `--only season-schedule`: League Schedule weeks 1-18 via the week dropdown (~3 pages a week) → season_schedule/
│                              #   `--only roster`: Manage Roster > Roster for all 32 teams (team via the held-L2 dropdown, starting at the user's team);
│                              #   per row the player card is read on the Mac (Apple Vision on an in-process window grab, settled = two agreeing reads,
│                              #   each step checked against the list, by name or by the neighbouring line's position and age: a skipped row is stepped back, an unverifiable step stops the run; a card that stops changing anywhere but the last row stops the team, since a frozen chiaki-ng stream looks like the end of the list; when a team or team switch fails and a harmless test press doesn't change the screen, the stream is force-quit and relaunched (CHIAKI_HOST / CHIAKI_HOST_IP from env or .env, up to 8 times per run) and the team redone; the team chip must read as one of the 32 teams; a team's rows reach cards.csv only once it is complete) → roster/cards.csv (team,row,first,last,number,
│                              #   position,archetype,role,dev,age,height,weight,experience,flags,card_png; experience 0 = ROOKIE; flags name missing or
│                              #   implausible values, e.g. Vision drops the digit 1) + roster/<NN>_<team>/card_NNN.png (card crop) and list_NN.png (one full
│                              #   screenshot per 14-row list page, for Gemini). ~80 s per team
├── extract.py                 # Gemini vision pass (gemini-3.8-flash, JSON schema) over a captures/ run (a row read two ways on overlapping pages - same NAME+POS differing in 1-2 cells, or equal numbers under names one letter apart - keeps the copy seen highest above a page's bottom edge, where rows are faded or mid-scroll; a tie is re-read twice and voted) → stats_<category>.csv (wide tables: left/right pages paired by number, rows joined by position since NAME/POS scroll away; shared columns cross-checked), team_conversions/team_redzoneefficiency/team_penalties.csv, schedule.csv, season_schedule.csv (all 18 weeks, same columns); games runs → games/<NN>/box_score.csv (LABEL, away, home) and stats_<category>.csv, plus week-level game_scores.csv (quarters + totals; QUARTERS_OK = n when the quarters don't add up to the total, e.g. digits washed out by a light team colour) and game_stats_<category>.csv (GAME column; wide pages read through each page's own headers, cut-off edge columns mapped to their full name, all-empty columns dropped); roster runs → roster_list.csv (TEAM + list columns from the list pages; CONV/DEV icons blank; a page with fewer than 14 rows is re-read; rows de-duplicated on NAME+POS, differing repeats printed as CONFLICT; per-team counts compared with cards.csv) and roster_cards.csv (cards.csv with AGE/HT/WT/EXP re-read by Gemini from the card crops, 20 numbered strips per request, since Apple Vision drops the digit 1 and misreads heights; vision_* columns keep Vision's values, vision_disagrees names the fields that differ) (GEMINI_API_KEY, env or root .env)
├── README.md                  # Setup & weekly usage for madden_capture.py / extract.py (permissions, App Nap, safety rules)
├── captures/                  # madden_capture.py output (screenshots, scope_debug/ + week_debug/ L2-dropdown frames, stop_debug/ frame that caused a stop, extracted CSVs; roster/ from --only roster; team_debug/ team-dropdown frames; <date>_explore/ holds --step mapping frames); gitignored
├── server/                    # FastAPI backend
│   ├── api.py                 #   REST API + /static mount (serves ../static) + the built site (Mu_Network/dist) at /, mounted last
│   ├── social_api.py          #   Agent Wire read queries behind /api/social/* (feed, post, profile, trends, search, Team Hubs)
│   └── debug_api.py           #   /api/debug/*: the #debug page (league state, table browser + cell edits, endpoints, docs, run jobs; §4.5)
├── pipeline/                  # Video ingestion & OCR extraction (Section 6, step 1)
│   ├── main.py                #   End-to-end pipeline entrypoint (--file / --stream, --db, --workers)
│   ├── weekly_capture.py      #   Weekly run, started from #debug or by hand: connect, capture, extract, load, backfill + engines, articles, shrink;
│   │                          #   --no-articles, --mode games (last week's games, loaded with --replace); stops + notifies on a failed step; --resume
│   ├── notify.py              #   ntfy phone notices (NTFY_TOPIC / NTFY_SERVER from env or .env); env() reads .env settings
│   ├── ocr_extractor.py       #   Gemini box-score extraction + Pydantic stat models
│   ├── load_capture.py        #   Loads an extracted captures/<run> into the DB, one transaction, checks first (mode from the folder; roster built so far)
│   ├── capture_matching.py    #   Name keys, roster matching in passes (list name first; rows whose full_name belongs to another player don't count),
│   │                          #   Season Stats history assignment for shared names
│   ├── template_classifier.py #   Screen-state classifier (TEMPLATE_DIR = pipeline/templates/)
│   ├── templates/             #   Reference screenshots for each Madden UI state
│   ├── ingest_stream.py, sort_video.py, batch_historical.py, batch_menus.py
│   └── roster_scanner.py, schedule_scanner.py, heal_roster.py
├── engines/                   # Deterministic analytics (Sections 2 & 6, steps 2–3)
│   ├── game_stat_generator.py #   CPU-sim backfill → culture engines (no swarm; see newsroom/weekly_run.py)
│   ├── backfill_team_stats.py
│   ├── team_box_estimator.py  #   Per-game team conversions, red zone, penalties from team_seasons totals → team_stats (§2.6)
│   ├── gotw_engine.py         #   Game Excitement Index (E) → games.excitement_index; E components, player matching, 70–100 display scale
│   ├── excitement_projection.py #  Projected E for scheduled games → games.projected_excitement (+ --backtest)
│   ├── twi_engine.py, popularity_engine.py, player_popularity_engine.py
│   ├── market_constants.py    #   B / L / D / N / Legacy Floor per franchise + SB hardware bumps derived from games
│   ├── game_credit_engine.py  #   Opponent-Adjusted Game Credit (SRS) + Trajectory Multiplier → team_seasons.opp_adj_credit, sos_rating, expected_wins, trajectory_multiplier
│   ├── team_efficiency_engine.py # Pythagorean wins, team pace/production → team_seasons
│   ├── playoff_clout_engine.py #  Team & player Playoff Clout → team_seasons.s_playoff, player_seasons.s_playoff
│   ├── matchup_edges.py       #   Matchup Edge / Trench Edge math for writer tools (computed live, not stored)
│   ├── fantasy_adp_engine.py  #   Preseason value board (draft_score) + runs the simulated drafts → fantasy_projections week 1 adp_rank
│   ├── fantasy_league_sim.py  #   100 simulated 12-team PPR leagues: strategy drafts → ADP; weekly rostered/start %, standings, 6-team playoffs → fantasy_sim_*
│   ├── records_engine.py      #   Record book: checks a season against franchise records and the NFL top 10s (§2.9)
│   └── fantasy_projection_engine.py # Weekly PPR projections, matchup stars, pos/ROS ranks → fantasy_projections (+ --backtest)
├── newsroom/                  # Agent swarm (Section 7)
│   ├── swarm_dispatcher.py    #   Editorial desk, commissioning, parallel dispatch
│   ├── slot_targets.py        #   Editorial Matrix slot targeting (primer games, exits, eliminations)
│   ├── playoff_overrides.py   #   Playoff override lookup, temporal-mode normalization, config validation
│   ├── run_context.py         #   Season/publication week of the article being written (tool season + week cutoff)
│   ├── entity_patterns.py     #   Case-sensitive team-mention patterns for article tagging
│   ├── local_portfolio.py     #   Plans a team's weekly local beat portfolio across its reporters
│   ├── social_desk.py         #   Agent Wire planner: a week's ~100 post assignments with facts packets, replies, team wakeups
│   ├── social_writer.py       #   Agent Wire post prompts (per post kind), one tool-free Gemini call, cleanup, token cost
│   ├── social_publish.py      #   Agent Wire run: writes, times, scores and saves posts, reply pass, weekly trends
│   ├── season_landscape.py    #   Division standings for the assignment brief's season_landscape
│   ├── agent_memory.py        #   Evolving agent memory: takes log, prediction track record, weekly summaries (§7.7)
│   ├── power_rankings.py      #   Parse/validate/store the weekly POWER_RANKINGS article → power_rankings table
│   ├── weekly_run.py          #   Weekly national run: picks the publication week, skips published weeks, locks
│   ├── db_tools.py            #   ReAct research tools exposed to writer agents
│   ├── fantasy_tools.py       #   Fantasy research tools (FANTASY_TOOLS), given only to FANTASY_* article types
│   ├── fantasy_desk.py        #   Fantasy Editorial Matrix: weekly fantasy slots, Card 03 schedule, fantasy calendar + desk rules
│   └── config/                #   agent_personas.json, article_types.json, article_structure_directives.json,
│                              #   curatorial_autonomy_directives.json, playoff_overrides.json,
│                              #   fantasy_writers.json (fantasy article voices + selection filters)
├── db/                        # PostgreSQL access & maintenance
│   ├── db_handler.py          #   Shared connection / UPSERT layer (DATABASE_URL)
│   ├── llm_usage.py           #   UsageMeter: counts every Gemini call on a client (tool loops included) → llm_usage (+ the capture's totals)
│   ├── init_db.sql, run_sql.py
│   ├── backup_db.sh, restore_db.sh   # pg_dump / restore via the madden-db Docker container
│   ├── clear_news_feed.sh     #   Back up then delete generated news_feed content (--articles-only keeps tweets)
│   ├── migrations/            #   migrate_*.py, 2026_10_12_fantasy_points_generated.sql, 2026_10_13_projected_excitement.sql, 2026_10_14_opp_adj_credit.sql,
│   │                          #   2026_10_15_stored_index_components.sql, 2026_10_16_trajectory_and_efficiency.sql,
│   │                          #   2026_10_17_playoff_clout_awards_exposure.sql, 2026_10_18_power_rankings.sql,
│   │                          #   2026_10_19_roundtable_panel_agent.sql, 2026_10_20_agent_memory.sql,
│   │                          #   2026_10_21_fantasy_league_sim.sql, 2026_10_22_agent_wire_social.sql,
│   │                          #   2026_10_23_capture_loader.sql, 2026_10_24_player_id_keys.py (python3 -m db.migrations.2026_10_24_player_id_keys, after a roster load),
│   │                          #   2026_10_25_former_players.sql, 2026_10_26_nfl_records.sql, 2026_10_27_name_casing.py (re-cases all-caps full names
│   │                          #   from the loaded roster capture's cards; python3 -m db.migrations.2026_10_27_name_casing),
│   │                          #   2026_10_28_league_record_book.sql, 2026_10_29_team_box_estimates.sql, 2026_10_30_coaches.sql,
│   │                          #   2026_10_31_team_box_exact_fields.sql,
│   │                          #   2026_10_31_coach_baseline.sql, 2026_11_01_coach_schemes.sql,
│   │                          #   2026_11_02_drop_coach_archetype.sql, 2026_11_03_whitfield_2034.sql,
│   │                          #   2026_11_05_coach_tendencies.sql (apply SQL ones via
│   │                          #   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < <file>)
│   └── backups/               #   madden_backup_*.sql dumps
├── headshots/                 # AI player headshot generation
│   ├── headshot_appearance_generator.py, make_headshot.py
│   ├── gen.py                 #   Gemini batch job submit / download
│   └── process_headshots.py   #   Background removal → static/players/{player_id}.webp
├── dashboards/                # Streamlit tools: admin_dashboard.py, espn_dashboard.py
├── tests/                     # test_*.py scripts
├── tools/
│   ├── debug/                 #   One-off frame/OCR debugging scripts
│   ├── verify_session.sh      #   One-shot newsroom check: roundtable migration, unit tests, verify_newsroom.py, weekly dry run → verify_output/
│   ├── verify_newsroom.py     #   Checks of the newsroom against the live DB (tool week cutoffs, brief, prompts, local portfolio, weekly news endpoint, agent memory); its one write test runs in a rolled-back transaction
│   ├── check_capture_load.py  #   Phase 3 exit check: backfill against a DB (analytics blocked) + per-player game lines vs season_stats
│   ├── rebuild_backfill.py    #   One-time: clear a season's backfilled lines (every game but the kept team's, default Panthers = real) and re-run the backfill
│   ├── vision_dump.py         #   Apple Vision OCR of a capture folder or frame folder → vision_ocr.json (read-only; used for the Oct 13 OCR tests)
│   └── vision_cells.py        #   Per-cell Apple Vision OCR experiment from cell_plan.json → vision_cells.json (read-only)
├── static/players/            # Served headshots ({player_id}.webp) at /static/players/
├── docs/
│   ├── EDITORIAL_MATRIX.md
│   ├── FANTASY_EDITORIAL_MATRIX.md # Agreed fantasy slate: fantasy calendar (W1 positional draft tiers, W2–14 season, W15–17 playoffs, W18 review, W19–23 frozen), 100 simulated leagues (strategy drafts → ADP, rostered/start %, 6-team playoffs), K/DST after migration phase 3; built: league sim, writer tools, fantasy desk, API and page
│   ├── SOCIAL_MATRIX.md       #   Agreed Agent Wire plan (steps 1–7 built: accounts, schema, planner, writer, publishing, API, frontend): social desk planner (~100 posts/week, player-first: die-hard reactions, performances, bad games, player comparisons, beat notes, standings, article shares, replies, previews, memes), ex-player accounts, computed engagement, tag-based trends, simulated post times, accounts/handles, schema/API, build steps
│   ├── PIPELINE_MIGRATION.md  #   Agreed plan for moving ingestion to madden_capture.py over chiaki-ng (phases, decisions, verified facts)
│   ├── models/                #   Index specifications (TWI, popularity, market gravity, legacy floor, GOTW, seasons played);
│                              #   proposed_metric_definitions.md = decision record for metrics writers were told to use (built / rejected)
│   ├── reference/             #   Source PDFs
│   └── sample_articles/       #   Example generated articles
├── data/                      # Large local inputs/outputs (not code)
│   ├── video/                 #   Madden screen recordings (*.mp4)
│   ├── frames/                #   Extracted / sorted debug frames
│   ├── images/                #   Misc. screenshots and mockups
│   └── headshots/             #   batch_headshot_requests.jsonl, batch_results.jsonl, players_backup/
└── Mu_Network/                # React frontend (has its own PROJECT_SPEC.md for UI screens)
```

### 1.2 Running the System
All Python commands run **from the project root** (relative data paths such as `data/...` and `static/...` resolve against it). Configuration comes from the environment: `DATABASE_URL` (defaults to the local `madden` database; the backfill, every engine, the newsroom, the capture loader and the API all read it, so pointing it at a restored test copy runs everything there) and `GEMINI_API_KEY` (the swarm also reads a root-level `.env`).

| Task | Command |
|---|---|
| API server | `python3 -m uvicorn server.api:app --host 0.0.0.0 --port 8000 --reload` |
| Frontend | `cd Mu_Network && npm run dev` (Vite on port 8443) |
| Video pipeline | `python3 -m pipeline.main --file data/video/<clip>.mp4 --db` |
| Stat backfill + culture engines | `python3 -m engines.game_stat_generator` (season from `league_state.current_season`; `--season N` overrides) |
| Publish the weekly newsroom slate | `python3 -m newsroom.weekly_run` (next week; skips if already published). `--week N` for a specific week (Week 1 preseason and Week 23 Super Bowl are manual), `--force` to publish again, `--dry-run` to print prompts with no Gemini calls or writes. The Mu Fantasy slate runs in the same call after the national slate (W1–18, own already-published check); `--fantasy-only` / `--skip-fantasy` |
| Clear generated articles/tweets | `bash db/clear_news_feed.sh` (backs up `news_feed`, tags, `power_rankings` and the agent-memory tables to `db/backups/` first, asks to confirm; deleting articles cascades to their takes and predictions, and agent season summaries are cleared; `--articles-only` keeps tweets) |
| Re-score Excitement Index (E) | `python3 -m engines.gotw_engine --season 2035` (then refresh the culture indices below). Every engine's season (CLI `--season` and the Python `season` argument) defaults to `league_state.current_season`, as does `newsroom.weekly_run` |
| Refresh culture indices (in this order) | `python3 -m engines.twi_engine` → `python3 -m engines.popularity_engine` → `python3 -m engines.player_popularity_engine` |
| Opponent-Adjusted Game Credit + Trajectory | `python3 -m engines.game_credit_engine --season 2035` (independent of the culture indices) |
| Team efficiency profile | `python3 -m engines.team_efficiency_engine --season 2035` |
| Playoff Clout | `python3 -m engines.playoff_clout_engine --season 2035` |
| Project E for scheduled games | `python3 -m engines.excitement_projection --season 2035` (`--backtest` scores accuracy, no writes) |
| Preseason ADP (runs the 100 simulated drafts once per season) | `python3 -m engines.fantasy_adp_engine --season 2035` (`--redraft` replaces a stored draft) |
| Simulated fantasy leagues (rostered %, start %, standings) | `python3 -m engines.fantasy_league_sim --season 2035` (re-simulates from the stored draft through the next unplayed fantasy week; `--summary` prints results, no writes) |
| Weekly fantasy projections | `python3 -m engines.fantasy_projection_engine --season 2035 --week 18` (no `--week`: league_state current week; `--all-weeks` backfills 2–18; `--backtest` scores accuracy, no writes) |
| Debug page (league state, tables, endpoints, docs, run buttons) | Open the site's LIVE badge (`#debug`); from a phone on the home network `http://<Mac's IP>:8000/#debug`: the API also serves the built site (`Mu_Network/dist`, mounted at `/` after every route; rebuild with `cd Mu_Network && npm run build` after frontend changes), because the macOS firewall allows Python but not `node` (Vite's 8443). Edits and runs need `DEBUG_TOKEN` from `.env`, typed once on the page. See §4.5 |
| Weekly run by hand / resume a stopped week | `python3 -m pipeline.weekly_capture` (connect → capture → extract → load → backfill + engines → articles → shrink); `--resume <run>` continues `captures/<run>` from the first step not done; `--no-articles` (Backfill previous week); `--mode games` (Game scan: `madden_capture.py --only games`, extract, load with `--replace`, engines, shrink; never articles); `--no-connect` |
| Phone notice test | `python3 -m pipeline.notify "message"` (`NTFY_TOPIC`, `NTFY_SERVER` in `.env`) |
| Capture Season Stats, Team Stats (conversions, red zone, penalties) + last week's schedule over Remote Play | `python3 madden_capture.py` (chiaki-ng streaming, anywhere in franchise menus). `--connect`: start the chiaki-ng stream (`CHIAKI_HOST` / `CHIAKI_HOST_IP`) when it isn't running, or force-quit and relaunch it once when it shows no franchise screen within 30 s; presses nothing (`chiaki stream` lands straight on the franchise screen; the PlayStation menu only shows on a manual connect); stops after 2 minutes without a franchise screen. `--out <run>` writes into `captures/<run>`. `--only stats\|team\|schedule\|roster\|season-schedule\|games` (`--games N` stops after N games for testing) (roster, season-schedule and games are not in the default run yet; season-schedule = League Schedule weeks 1-18 → season_schedule/season_week_NN_NNN.png; `--teams N` stops after N teams for testing; `--resume <run>` continues a stopped roster or games run in captures/<run>, skipping the teams in roster/done.txt or the games with a games/<NN>/DONE marker), `--week "SEASON WEEK 12"` (the schedule step writes `week.json` with the week the schedule opened on and the week captured, for the loader), `--test` (read screen only), `--scope-only`, `--probe-l2` (records the held-L2 dropdown from Player Stats or the Manage Roster list), `--all-rows`, `--foreground`. Team-scoped Season Stats: `--only stats --scope <TEAM> --category <CAT> [--category ...] --resume <weekly run>` → stats_scope/ in that run (settles shared names the weekly load stops on). Mapping tool: `--step <key>\|none` presses one key and saves the screenshot, OCR text (with boxes and brightness) and `classify()` result to `captures/<date>_explore/explore/NN_<key>.png/.txt`; `--wait` sets the settle time; Cross needs `--allow-cross` and is always refused on a screen showing ADVANCE WEEK or FORCE WIN |
| Screenshots → CSVs | `python3 extract.py captures/<run>` (Gemini, 8 parallel requests on table-cropped half-size images; `GEMINI_API_KEY` from env or root `.env`; `MADDEN_EXTRACT_MODEL` / `MADDEN_EXTRACT_WORKERS` override model and parallelism) |
| Load a capture | `python3 -m pipeline.load_capture captures/<run>` (`--dry-run`: checks + report, writes nothing). `DATABASE_URL` picks the database (default the live `madden`; point it at a restored test copy to try a load first). Roster: matches every card to a player (list name + team + position first), stops with nothing written on any unreadable or ambiguous card, updates `players` (full name, height, weight, number; a stored name spelling with the same letters is kept unless it is all caps) and `player_seasons` (team, position, age, list OVR, experience, dev, archetype, role), adds new players; on a full 32-team capture, players it didn't find are deleted (with their generated fantasy rows) when they hold no real data (no stats, awards, article tags, agent memories), and otherwise keep their team and are listed Season schedule: inserts SCHEDULED games only, never touches existing ones; checks 272 games, 17 per team. Weekly (Season Stats + Team Stats + last week's schedule): the week from schedule.csv (`Week 17` → 17; postseason labels map to 19 Wild Card … 23 Super Bowl; a week already loaded is refused, a gap is flagged); `league_state.current_week` is set in the same transaction from `week.json` (the week the League Schedule opened on; the load stops if its captured week isn't schedule.csv's or its current week doesn't follow it; a folder without one gets the loaded week + 1; never moves back), scores → `games` COMPLETED, team tabs → `team_seasons`, Season Stats resolved by name + position through the roster (shared names settled by earlier counting-stat totals, then by team-scoped lists `stats_scope_<team>_<cat>.csv` where one team's list settles a pair; else it stops and prints the `--scope` commands to run; a drop in a total settled by a scoped list is reported as a correction, other drops stop the load; CSV headers lose Madden's sort mark, and a mapped column missing from a CSV stops the load; afterwards it removes stale copies, i.e. season_stats rows it didn't write that repeat a written row's list name, position and offense numbers under another player), offense → `season_stats`, other categories → `season_stat_*`, GP/DP → `season_stat_general`; stops on unmatched passing/rushing/receiving rows, a row read two ways, a counting stat going down, a row count more than 10% off the last weekly capture. Games (recovery): box scores → `team_stats`, lines → `player_stats` / `game_stat_*` (team + list name; teammates sharing a name settled by category), scores → `games`; refused while the backfill's rows exist unless `--replace` |
| Headshot post-processing | `python3 -m headshots.process_headshots` |
| Dashboards | `streamlit run dashboards/admin_dashboard.py` |
| DB backup / restore | `db/backup_db.sh` / `db/restore_db.sh db/backups/<file>.sql` |

---

## 2. Core Mathematical Indices & Formulations

### 2.1 Game Excitement Index ($E$)
Evaluates single-game entertainment value ($0 \text{ to } 100$):
$$E = 0.35(C) + 0.25(P) + 0.20(S) + 0.20(U)$$
- **$C$ (Competitiveness)**: Final score margin and overtime drama: $\min(100, \max(0, 100 - 4 \cdot |\text{Score}_A - \text{Score}_B|) + \text{OT Bonus})$.
- **$P$ (Production & Pace)**: Combined points and offensive yardage volume.
- **$S$ (Star Impact & Outliers)**: Top-3 player OVR averages and standout single-game milestone box scores (e.g. 400+ pass yards, 4+ TDs).
  - **Defensive & special-teams outliers** (`defensive_outlier_points`, JR Oct 14): from a game's captured lines in `game_stat_lines` (`fetch_game_defense_rows`; captured games only, Week 17 onward in 2035), per player: 3+ sacks +12, 2+ interceptions +12, +10 per defensive TD, +10 per kick or punt return TD, a safety +6, 2+ forced fumbles +6, a made field goal of 55+ yards +6. They add to the offensive milestones (400+ pass yards +15, 4+ pass TDs +15, 150+ scrimmage yards +12, 2+ rush/rec TDs +8) before the 100 cap, so a 3-sack game counts like a 150-yard day. Games without captured lines keep offense-only outliers. Week 17 of 2035 was given only the defensive increment (six games, +0.6 to +1.6 raw; Steelers–Vikings, already the week's top game, 83.65 → 85.25), not a full re-score: re-scoring those games with today's inputs would also have moved them up to ±5.6 from rating and box-score drift since they were scored.
  - Each stat line's player is its stored `player_stats.player_id` (abbreviated names like `A. Wright` collide across players), and OVR is that player's rating in the game's season; lines with no rating that season are skipped (`fetch_game_player_rows` in `engines/gotw_engine.py`). TWI's $Y_{\text{prod}}$ ages, Playoff Clout's "played in", the record book's single games and the fantasy projections' lines join the same way.
- **$U$ (Upset & Narrative Leverage)**: Pre-game talent differential (`team_seasons.team_ovr` for the game's season) and heavyweight clashes.
- Computed by `engines/gotw_engine.py` and stored in `games.excitement_index`. A game's $E$ depends only on that game's data, so once scored it is **not** re-scored automatically (re-scoring would let it drift as player/team ratings change during the season, which would in turn shift TWI and popularity).
  - **Pipeline (Section 6, step 3):** `batch_evaluate(season, only_missing=True, game_ids=<games just backfilled>)` scores only completed games with no $E$ yet plus the games whose box scores were inserted in that run.
  - **Manual re-scoring** (e.g. after a box-score correction): `python3 -m engines.gotw_engine --game-ids 805 832`; `--season 2035 --missing-only` fills gaps; no flags re-scores every completed game in all seasons (`--season` limits it to one).
- **Projected Excitement (scheduled games, `engines/excitement_projection.py`)**: the expected value of each component before kickoff, on the same raw scale as $E$, stored in `games.projected_excitement` (migration `db/migrations/2026_10_13_projected_excitement.sql`) and refreshed for every scheduled game by the pipeline (§6):
  - $C$: $\mathbb{E}[\max(0, 100 - 4|m|)]$ with margin $m \sim N(\mu, \sigma)$, $\mu = k \cdot (\text{OVR}_\text{home} - \text{OVR}_\text{away})$. The slope $k$ (≈2.3 points per OVR) and the residual spread $\sigma$ are fitted from the season's completed games.
  - $P$: the league-average game. Team scoring and yardage barely persist week to week in this league, so team-specific $P$ only added noise.
  - $S$: the teams' average top-3 OVR and milestone-outlier points per game (offensive plus captured defensive and special-teams outliers), shrunk toward the league average (4 pseudo-games).
  - $U$: the OVR favourite's win probability, blended over the chalk and upset outcomes.
  - **Accuracy (2035 backtest, weeks 3+, earlier weeks only):** correlation ≈ 0.23 with actual $E$, and mean error 9.25 vs. 9.38 for always guessing the league average. Pre-game excitement is only weakly predictable, so projections are always labelled as projected. Check with `python3 -m engines.excitement_projection --season 2035 --backtest`.
  - This replaces the former display-only formula $\min(98, \max(85, \lfloor \text{Matchup TWI} \times 1.45 \rfloor))$. That put 207 of 224 backtested games at exactly 85, and correlated −0.25 with actual $E$.
- **Display scale (70–100)**: stored and projected $E$ stay raw (TWI and popularity use raw $E$). Everything shown to readers and writers (API, assignment brief, writer DB tools) uses `display_excitement()` in `engines/gotw_engine.py`: $\text{Display} = \min(100, \max(70,\ 70 + 30 \cdot (E - 40) / 60))$. A dull game (raw 40, about the 5th percentile) shows as 70, a typical game (≈58) about 79, the 2035 best (83.7) about 92, and a perfect 100 shows as 100.

### 2.2 Team Watchability Index ($TWI$)
Rolling, opponent-adjusted metric defining how entertaining a franchise is to watch over time:
$$TWI = \min\left(100, 0.40(\mu_{\text{adj}}) + 0.20(\sigma_{\text{top}}) + 0.25(P_{\text{draft}}) + 0.15(Y_{\text{prod}}) - \text{Penalty}_{\text{purgatory}}\right)$$
- Attributed through production share $\alpha$ and opponent baseline correction $\delta_{\text{opp}}$.
- Exponential recency decay with $\lambda = 0.15$.
- Ceiling & chaos volatility bonus ($\sigma_{\text{top}}$) from high-entertainment games.
- Stored permanently in `team_seasons.watchability_index`, along with its components `watchability_sigma_top` ($\sigma_{\text{top}}$), `p_draft`, `y_prod` and `purgatory_penalty` so writer tools read the same values TWI used.

### 2.3 Fantasy Defensive Matchup Stars & Strength of Schedule (SOS)
- **Even 5-Tier Quantile Distribution (`NTILE(5)`)**: To eliminate artificial clustering, defensive strength against each offensive position (QB, RB/HB, WR, TE) is ranked across all 32 NFL teams:
  $$\text{Matchup Stars} = \text{NTILE}(5) \text{ OVER } (\text{PARTITION BY } \text{season}, \text{position} \text{ ORDER BY } \text{total\_allowed} \text{ ASC})$$
  - **1 Star (Top 6–7 Defenses)**: Toughest matchups (allow fewest fantasy points).
  - **2 Stars (6–7 Defenses)**: Above-average defenses.
  - **3 Stars (6 Defenses)**: Average defenses.
  - **4 Stars (6 Defenses)**: Below-average defenses.
  - **5 Stars (Bottom 6–7 Defenses)**: Easiest matchups (allow most fantasy points).
- **Season SOS & Playoff SOS**:
  - **Season SOS**: 18-week schedule difficulty bucketed into 1 to 5 stars via `NTILE(5)`.
  - **Playoff SOS**: Fantasy playoff weeks (Weeks 15–17) opponent schedule difficulty bucketed into 1 to 5 stars via `NTILE(5)`.

### 2.4 Opponent-Adjusted Game Credit (`engines/game_credit_engine.py`)
Schedule-adjusted team strength in **points per game relative to a league-average team** (0 = average), fitted as a Simple Rating System over the season's completed games:
- **Capped margin** $m$: each game's margin clamped to ±24 (`MARGIN_CAP`).
- **Home field** $H$: the league's mean capped home margin this season, clamped to $[0, 4]$. `SUPER_BOWL` games are neutral (site sign $s = 0$; home $s = +1$, away $s = -1$).
- **Game credit** for a team in one game: $c = m - sH + R_{\text{opp}}$, the margin an average team would need at that site against that opponent to match the result.
- **Rating** (`opp_adj_credit`): $R_i = \dfrac{\sum_{g} c_g}{n_i + 2}$ (shrunk toward 0 by 2 pseudo-games), solved iteratively and re-centred so ratings average 0.
- **Recent form** (`opp_adj_credit_recent`): recency-weighted mean of $c$ with TWI's decay $w_t = e^{-0.15(T - t)}$.
- **Strength of schedule** (`sos_rating`): mean $R_{\text{opp}}$ faced; positive means a harder schedule.
- **Trajectory Multiplier** (`trajectory_multiplier`, $T$): $\Delta = \bar c_{\text{last 4}} - R$; $T = \text{clamp}(1 + 0.5\,\Delta / 20,\ 0.80,\ 1.20)$, the 0.5 shrink damping the noisy 4-game mean. $T = 1.00$ before 6 games. Labels: ≥ 1.08 Surging, ≥ 1.03 Rising, > 0.97 Steady, ≥ 0.92 Fading, < 0.92 Sliding.
- **Expected wins** (`expected_wins`): $\sum_g \Phi\!\left((R_i - R_{\text{opp}} + sH)/\sigma\right)$, where $\sigma$ is the RMS residual margin (default 14 with fewer than 64 games). `wins − expected_wins` ("win luck") flags paper tigers (positive) and unlucky teams (negative); ties count as half a win.
- Written to `team_seasons` by `GameCreditEngine().calculate(season)`. Writer agents read it live (recomputed from `games`, so it is never stale) through `get_opponent_adjusted_credit`.
- This is distinct from TWI's opponent baseline correction $\delta_{\text{opp}}$, which adjusts *excitement* for how watchable an opponent is, not team strength.

### 2.5 Popularity Indices (`engines/popularity_engine.py`, `engines/player_popularity_engine.py`)
- **Franchise POP** $= 0.35B + 0.30H + 0.35\,TWI$, with $H = 0.85\,S_{\text{recent}} + 0.15\,S_{\text{all-time}}$ and $S_{\text{recent}} = \min(100,\ 100\cdot\text{Win\%} + \text{hardware bump})$. Written to `team_seasons.popularity_index`.
  - The hardware bump is +25 per Super Bowl win and +5 per runner-up over the 5 seasons before the current one, derived from `games.game_type = 'SUPER_BOWL'` (`market_constants.recent_hardware_bumps`).
  - $B$ (Base Market Gravity) and $S_{\text{all-time}}$ (Legacy Floor) are constants in `engines/market_constants.py`, transcribed from `docs/models/base_market_gravity_specification.md` and `docs/models/legacy_floor.md`. The $L$/$D$/$N$ breakdown of $B$ is only published for eight franchises.
  - `docs/models/legacy_floor.md` lists the Giants as 2031 and 2033 runner-ups; the recorded results (and the Season Recap video) have the Lions and Falcons. The Legacy Floor values were not recomputed.
- **Player Pop** $= (0.15F + 0.20E + 0.20P + 0.15R + 0.15A + 0.15T)\cdot M_{\text{pos}}$ (`player_seasons.popularity_index`). Stored components: SpikeIndex, $R_{\text{rating}}$, $F_{\text{platform}}$, $T_{\text{success}}$, $A_{\text{awards}}$, $E_{\text{exposure}}$ (`spike_index`, `r_rating`, `f_platform`, `t_success`, `a_awards`, `e_exposure`).
  - **Awards Equity** $A = \min\left(100,\ \sum_{k=1}^{3} e^{-0.35(k-1)} A_{\text{pts}}(Y-k) + 0.25\,A_{\text{career}}\right)$, where $A_{\text{pts}}$ is MVP 50, OPOY/DPOY 35, SBMVP 25, OROY/DROY 18 (COTY 0; All-Pro isn't recorded) and $A_{\text{career}}$ is all award points before season $Y$. Matched on `awards.player_id`. Before 2026-10-12 it compared full award names with abbreviated `players.player_name`, so $A$ was 0 for everyone.
  - **Exposure** $E = \sum_k w_k E_k (1 + 0.25\,\mathbb{I}_{\text{GOTW},k}) / \sum_k w_k$ over games the player has a stat line in, $w_k = e^{-0.12(T-k)}$, GOTW = the week's highest raw $E$. The appearance flag stands in for the spec's SnapShare. Players with no stat lines (all defenders) get 50. Previously a flat 60.
  - **Team Success** $T = 0.60\cdot 100\,\text{Win\%} + 0.40\,S_{\text{post}}$, where $S_{\text{post}}$ is 30 for last season's Super Bowl winner and 15 for the runner-up, from `games`.
  - Data note: the 2033 SB MVP is recorded as "Cade Klubnik" but `player_id` 1477 is `players.full_name` "Cade Loop".


### 2.6 Team Efficiency Profile (`engines/team_efficiency_engine.py`)
- **Pythagorean wins** (`pythagorean_wins`): $G \cdot PF^{2.37} / (PF^{2.37} + PA^{2.37})$. "Pyth luck" $=$ wins $-$ Pythagorean wins. It ignores schedule; compare with `expected_wins` (2.4).
- **Team pace & production** (`pace_production`): $\min\left(100,\ \frac{PF/G}{35}\cdot 60 + \frac{\text{Yds}/G}{425}\cdot 40\right)$, the game-level $P$ benchmarks halved to one team. True tempo needs `team_stats.total_plays`, which is not captured (all 0).
- Also returned live by `get_team_efficiency_profile`: pass and rush yards per game and giveaways per game (the team's own offense).
- **Box-score caveat:** for CPU-simmed games, `game_stat_generator.py` splits season totals across games in proportion to each game's combined score (see Section 6, step 2). A team's own season totals are real, but per-game box scores, and anything attributed to opponents game by game (takeaways, yards allowed, TDs vs. points in one game, game-to-game usage changes), are artifacts of that split. Metrics are built only on season totals, scores and ratings for that reason; turnover margin and a TD-finishing-rate stand-in for red-zone efficiency were considered and rejected.
- **Per-game team conversions, red zone and penalties** (`engines/team_box_estimator.py`, `python3 -m engines.team_box_estimator [--season N] [--dry-run]`; JR, Oct 14): every NULL `team_stats` cell of third/fourth-down and two-point conversions and attempts, red-zone trips/TDs/FGs/percentage, penalties and penalty yards gets the team's season remainder (`team_seasons` total from the Team Stats capture minus the cells already stored), split across the games that need it. A captured box score supplies conversions, red-zone TDs/FGs/percentage and penalty yards (red-zone trips follow from the percentage); attempts and penalty counts are always estimated. Stored cells (captured or estimated earlier) never change, so in a weekly run each new game gets exactly that week's change; only the first run (Weeks 1–16 of 2035, plus Week 17's attempts and counts) spread a backlog. The split: each game's score is decomposed into TDs and FGs (about one extra point per TD, closest to the team's season red-zone TD share) and caps red-zone TDs and FGs; red-zone trips are balanced against the offense's remainder and the opponent's red-zone defense remainder by iterative proportional fitting, then rounded to the offense exactly (defense lands within a few trips; 98 off in total across all teams and the three stats on the 2035 backlog); third-down conversions follow points scored and failures the opposite, both scaled by the game's combined score; fourth-down attempts rise with the deficit; two-point tries follow TDs and the deficit and never exceed the game's TDs; penalties follow captured penalty yards or the combined score, and yards follow the count. Filled columns are listed in `team_stats.estimated_fields`; a cell that was its team's only missing cell for the column (normal weekly runs from Week 18 on) is the real value and is also listed in `exact_fields` (`red_zone_pct` when its TDs and trips are captured or exact). A clamped remainder (stored cells already over the season total) is never marked exact. A team's conversions allowed in a game are the opponent's row for that game: real where that row is captured or exact, an estimate in the backlog (Madden's Conversions screen has no defense side, so nothing anchors third-down defense the way red-zone defense is anchored). Regular season only, and only weeks the latest loaded weekly capture covers. These per-game values are estimates under the same caveat: nothing reads them yet, and season-level metrics should keep using `team_seasons`. `tests/test_team_box_estimator.py` covers the split.


### 2.7 Playoff Clout (`engines/playoff_clout_engine.py`)
- **Team** (`team_seasons.s_playoff`): $\min\left(100,\ \sum_{y \le Y} e^{-0.15(Y-y)}\left[100\cdot\text{SB win} + 50\cdot\text{SB loss} + 20\cdot\text{playoff wins}\right]\right)$. Super Bowls are `games.game_type = 'SUPER_BOWL'`; playoff wins are completed wins in any other non-`REGULAR_SEASON` game.
- **Player** (`player_seasons.s_playoff`): $\min(100,\ 40\cdot\text{SB MVPs} + 25\cdot\text{SB wins played in} + 8\cdot\text{other playoff wins played in})$. "Played in" = a `player_stats` row for the winning team in that game, which only exists from 2035. Before then only SB MVPs (by `awards.player_id`) count, so veterans' clout is understated.


### 2.8 Matchup Edge & Trench Edge (`engines/matchup_edges.py`)
Computed live by `get_matchup_edges`; nothing is stored. For team A's offense against team B's defense, with $z$ = z-score across the 32 teams:
- **Pass edge** $= z(A\ \text{pass yds/g}) + z(B\ \text{coverage\_rank})$. Madden ranks run 1 = best, so a high rank is a weak secondary.
- **Rush edge** $= z(A\ \text{rush yds/g}) - z(B\ \text{def\_ovr})$.
- **Ball-security edge** $= z(B\ \text{giveaways/g}) - z(A\ \text{giveaways/g})$.
- **Trench edge** = mean OVR of A's top 5 offensive linemen (LT/LG/C/RG/RT) − mean OVR of B's top 4 pass rushers (LEDG/REDG/LE/RE/DT), in OVR points, shown with B's `pass_rush_rank`.
- The largest positive edge is A's best edge and the most negative is its biggest vulnerability. Defense uses Madden ratings, not yards allowed or takeaways (see the box-score caveat in 2.6).
- **Season touch share** (in `get_player_season_stats`, RB/FB/WR/TE only): the player's share of the team's season carries + receptions and scrimmage yards, from `season_stats`. Receptions stand in for targets, which aren't recorded.

### 2.9 Record Book (`engines/records_engine.py`, `db/migrations/2026_10_26_nfl_records.sql`, `2026_10_28_league_record_book.sql`)
JR, Oct 14, 2026: a record book the league can be measured against. Not on a screen yet.
- **What's in it:** 17 categories (`record_categories`). Single season: passing yards and TDs, rushing yards and TDs, receptions, receiving yards and TDs, rushing + receiving TDs (no return TDs), team wins, team points scored and fewest points allowed (seasons of 16+ games). Single game: passing yards and TDs, rushing yards, receiving yards, receptions, rushing + receiving TDs. Offense and team only until defensive stats are loaded; no career records (the database has no careers before 2035).
- **Seed (real NFL history through 2025, from Pro-Football-Reference, pulled Oct 14, 2026):** every franchise's single-season record holders in all 11 season categories (400 rows, ties included; franchise histories follow Pro-Football-Reference, so the Rams include St. Louis and Cleveland, the Titans the Houston Oilers, the Raiders Oakland and Los Angeles, the Cardinals Chicago and St. Louis, and the Ravens start in 1996), and the NFL's all-time top 10 in the 14 player categories (160 rows). Single-game records exist only as NFL top 10s: Pro-Football-Reference has no franchise single-game lists. Known limits: Pro-Football-Reference's rushing + receiving TD single-game list has a data error at the top and leaves out Ernie Nevers (1929), so that list was set by hand from the NFL record book (6: Nevers, Dub Jones, Alvin Kamara); 38 games are tied at 6 passing TDs, of which the six most recent are kept; and for the oldest franchises the source pages were cut off before their earliest seasons, which were checked by hand only where they could hold a record (the 1963 Giants' 448 points, the 1961 Oilers' 513).
- **The league's own record book, 2026–2035 (JR, Oct 14; `2026_10_28_league_record_book.sql`, source `BOOK`):** the records set in this league between 2026 and 2035, from the game's record book: franchise records for single seasons, single games, rookie seasons and rookie games (119 rows, including Crawford's 2034 rookie season, which the NFL-wide rookie list implies was also a Panthers record), and the same seasons and games on the NFL lists (75 rows; the views rank them, so Ty Simpson's 592 passing yards (2034) and Jordyn Tyson's 399 receiving yards (2032) are the single-game records, and Trey'Mane Davis's 2,406 receiving yards (2034) the season record). Short names were matched to full names where the roster had exactly one player with that initial and surname, or the player is a well-known current NFL player; A. Lowery, B. Gore, B. Ojulari, M. Fields and M. Wilson keep the short form. Isaiah Carson's rookie receiving-yards game was listed as 2036 and is stored as 2035. New categories: sacks in a season, rushing TDs, receiving TDs and interceptions in a game, and rookie seasons and games; they are `coverage = 'BOOK'` (only what the record book lists, no real NFL history), so `league_records` gives them a league-wide record only where the record book named one (the rookie passing, receiving-TD and receptions records). The record book only lists records broken, not ties, and has no team or combined-TD records. Known gap: seasons from 2026–2034 that made an all-time top 10 without breaking a franchise record aren't in it.
- **Updating:** `python3 -m engines.records_engine --season 2035` reports the season's records; `--apply` stores them. Season values come from `season_stats` (players) and completed regular-season games (teams); single games from `player_stats` (regular season, the line's `player_id`). A season is measured against every earlier season (NFL, record book and league rows) and against its own other entries: only a team's best of the season can hold its franchise record, and top-10 ranks count the season's other entries. Fewest points allowed waits for a full 17-game season. `--apply` deletes and rewrites that season's `LEAGUE` rows, so it can run every week. The record book is the authority for its seasons: where it already has the season's row for a team and category, `--apply` adds nothing, and the report ends with where the record book and the stats disagree (a record-book entry the engine could have found but didn't, or a broken record in a category the record book tracks that it doesn't list). Old holders are never deleted: the views pick the best value, so the history of who held each record stays.
- **League-wide records** are computed (JR): the best franchise record across the 32 teams (`league_records`; single-game categories, which have no franchise records, take the top of the NFL list).
- **2035 check (Oct 14, through Week 17, on a copy of the live data, with the record book loaded):** the record book's 2035 entries and the stats agree. NFL records: the Panthers' 654 points (2013 Broncos, 606) and 16-0 (ties the 2007 Patriots with a game left), Crawford's 67 passing TDs (Manning, 55) and 7 in Week 6 (ties the single-game record), Davis's 27 receiving TDs (Moss, 23). Top 10: Davis's 1,949 receiving yards (3rd) and 27 combined TDs (3rd), Hansen's 19 receiving TDs (5th), Carson's 18 (8th), Carson's 16 catches in a game (9th). Franchise: Panthers 6 (passing TDs, Franklin's rushing yards, Davis's receiving and combined TDs, wins, points), Steelers 4, Texans 2 ties, Bears 1 (points). Without the record book the check had wrongly credited Crawford's 5,121 yards, Davis's 1,949 yards, and Bears, Browns and Lions records that 2026–2034 seasons had already passed.
- **Tests:** `tests/test_records_engine.py` (franchise and NFL records, ties, the season's best only, top-10 ranks with same-season entries, the full-season rule, single games, the report, the record book confirming the stats, disagreements both ways, `--apply` leaving record-book rows alone, the seed covering every team and category).

---

## 3. Database Schema & Architecture (PostgreSQL)

The database utilizes a relational temporal model in PostgreSQL (`dbname=madden`). `db/init_db.sql` reproduces the full live schema (tables, constraints, generated columns, views, functions, triggers) on an empty database and is safe to re-run; it does not add columns to existing tables, so schema changes to the live DB ship as migrations under `db/migrations/` and must also be folded into `init_db.sql`.

### 3.1 Core Entity & State Tables
- **`teams`**:
  - `id` (PK, int), `name` (varchar), `city` (varchar), `abbreviation` (varchar, e.g. 'CAR', 'TB', 'KC'), `conference` (varchar), `division` (varchar), `team_color` (varchar hex), `stadium_name` (varchar). `UNIQUE(name)`.
- **`players`**:
  - `id` (PK, int), `full_name` (varchar), `player_name` (varchar, e.g. 'G. Crawford'), `player_number` (int - jersey number, default 0), `height` (varchar), `weight` (int lbs), `espn_id` (int, nullable).
- **`league_state`**:
  - `id` (PK, int, default 1 — single row), `current_season` (int), `current_week` (int). Column defaults are 2024 / 1; the live league row is currently season 2035, week 18. `current_week` is Madden's current (next unplayed) week in `games.week` numbering, set by each weekly capture load from the captured week label (`pipeline/load_capture.py`); `current_season` is set by hand at a new season. The backfill, `pipeline/main.py`'s trigger and `DatabaseHandler.get_current_league_state()` read both and never infer or write them.

### 3.2 Temporal Snapshot Ledgers
- **`team_seasons`**:
  - `id` (PK), `team_id` (FK), `season` (int), `wins` (int), `losses` (int), `team_ovr` (int), `off_ovr` (int), `def_ovr` (int), `pass_off_rank` (int), `rush_off_rank` (int), `pass_rush_rank` (int), `coverage_rank` (int), `watchability_index` (float8), `popularity_index` (float8), `opp_adj_credit`, `opp_adj_credit_recent`, `sos_rating`, `expected_wins` (float8, nullable; see 2.4, added by `db/migrations/2026_10_14_opp_adj_credit.sql`), `watchability_sigma_top`, `p_draft`, `y_prod`, `purgatory_penalty` (float8, nullable; TWI components, see 2.2, added by `db/migrations/2026_10_15_stored_index_components.sql`), `trajectory_multiplier` (2.4), `pythagorean_wins`, `pace_production` (2.6) (float8, nullable; added by `db/migrations/2026_10_16_trajectory_and_efficiency.sql`), `s_playoff` (2.7; added by `db/migrations/2026_10_17_playoff_clout_awards_exposure.sql`); Team Stats tabs from the capture (nullable, `2026_10_23_capture_loader.sql`, not read yet): `third_down_conv`/`_att`/`_pct`, `fourth_down_conv`/`_att`/`_pct`, `two_point_conv`/`_att`/`_pct`, `red_zone_att`/`_td`/`_fg`/`_pct`, `red_zone_def_att`/`_td`/`_fg`/`_pct`, `penalties`, `penalty_yards`. `UNIQUE(team_id, season)`; `wins`/`losses` are maintained by `trigger_update_team_records` (see 3.6).
- **`player_seasons`**:
  - `id` (PK), `player_id` (FK), `team_id` (FK), `season` (int), `position` (varchar), `age` (int), `overall_rating` (int), `years_of_experience` (int; 0 = rookie, 1 = second-year), `popularity_index` (float8), `spike_index`, `r_rating`, `f_platform`, `t_success`, `a_awards`, `e_exposure` (float8, nullable; player Pop components, see 2.5), `s_playoff` (float8, nullable; 2.7); roster card fields `dev_trait`, `archetype`, `role` (varchar, nullable; `2026_10_23_capture_loader.sql`; `overall_rating` is the roster list OVR, the card's base OVR and morale aren't stored). `UNIQUE(player_id, season)`.

### 3.3 Games & Statistics Tables
- **`games`**:
  - `id` (PK), `season` (int), `week` (int), `home_team_id` (FK), `away_team_id` (FK), `home_score` (int), `away_score` (int), `status` (varchar: 'COMPLETED' | 'SCHEDULED' | 'BYE'), `game_time` (varchar, e.g. 'Sun 1:00 PM'), `game_type` (varchar, default 'REGULAR_SEASON'), `excitement_index` (float8), `projected_excitement` (float8, nullable; pre-game projected $E$), `completed_at` (timestamp). `UNIQUE(season, week, home_team_id, away_team_id)` (constraint `games_season_week_home_away_key`).
- **`player_stats`**:
  - `id` (PK), `game_id` (FK), `team_id` (FK), `player_name` (varchar), `position` (varchar), `completions`, `pass_attempts`, `pass_yards`, `pass_tds`, `interceptions`, `rush_attempts`, `rush_yards`, `rush_tds`, `receptions`, `rec_yards`, `rec_tds`, `fumb`, `fantasy_points` (numeric(6,2), **generated column**); from `2026_10_23_capture_loader.sql` (nullable, written only by the capture loader's games mode, not read yet): `player_id` (FK; keyed in a later migration), `pass_comp_pct`, `pass_rating`, `pass_ypa`, `pass_long`, `pass_sacks`, `rush_avg`, `rush_btk`, `rush_yac`, `rush_long`, `rec_avg`, `rec_rac`, `rec_rac_avg`, `rec_long`. `UNIQUE(game_id, player_name, position)` and, from `2026_10_24_player_id_keys.py`, unique `(game_id, player_id)` where `player_id` is set; trigger `player_stats_fill_player_id` fills `player_id` on inserts that only name the player (the backfill), from players + player_seasons (same team and position, list name).
  - `fantasy_points` is a PostgreSQL `GENERATED ALWAYS ... STORED` column computed from the row's box score with full-PPR scoring: 0.04/pass yd (1 per 25), 4/pass TD, −2/INT, 0.1/rush & rec yd (1 per 10), 6/rush & rec TD, 1/reception, −2/fumble; NULL stats count as 0. It recomputes automatically on every insert or stat correction and is read-only: ingestion and backfill code must **not** insert or update it (Postgres rejects writes). All fantasy surfaces (`vw_fantasy_scores`, `vw_fantasy_defense_vs_position`, game logs, rankings, player popularity) read this column.
  - Introduced by `db/migrations/2026_10_12_fantasy_points_generated.sql` (self-verifying; rolls back on any value change). Restoring a database dump taken before 2026-10-12 brings back the old plain column; re-apply the migration afterwards. Fresh databases built from `db/init_db.sql` already have the generated column.
- **`team_stats`**:
  - `id` (PK), `game_id` (FK), `team_name` (varchar), `score`, `first_downs`, `total_offense`, `total_plays`, `rushing_attempts`, `rushing_yards`, `rushing_tds`, `passing_completions`, `passing_attempts`, `passing_yards`, `passing_tds`, `third_down_attempts`, `third_down_conversions`, `fourth_down_attempts`, `fourth_down_conversions` (int, no default: NULL = unknown until the estimator fills them, 2026_10_29), `turnovers` (all int); box-score fields from `2026_10_23_capture_loader.sql` (nullable): `q1`–`q4`, `ot` (NULL when the digits were unreadable), `punt_return_yards`, `kick_return_yards`, `total_yards_gained`, `two_point_conversions`, `red_zone_pct`, `red_zone_tds`, `red_zone_fgs`, `penalty_yards`, `time_of_possession_s`; from `2026_10_29_team_box_estimates.sql`: `red_zone_attempts`, `two_point_attempts`, `penalties` (int, nullable) and `estimated_fields` (text[], default `{}`: the columns `engines/team_box_estimator.py` filled rather than a capture); from `2026_10_31_team_box_exact_fields.sql`: `exact_fields` (text[], default `{}`: the estimated columns that are exact, i.e. a team's only missing cell for that column; the 2035 Weeks 1–17 backlog has none). `UNIQUE(game_id, team_name)`.
- **`season_stats`**:
  - `id` (PK), `season` (int), `player_id` (FK), `player_name` (varchar), `team_id` (FK), `position` (varchar), the same box-score counters as `player_stats`, `total_fantasy_points` (numeric(7,2)), `ppg_fantasy_points` (numeric(5,2)); offense extras from `2026_10_23_capture_loader.sql` (nullable, not read yet): `pass_comp_pct`, `pass_ypg`, `pass_rating`, `pass_ypa`, `pass_long`, `pass_sacks`, `rush_avg`, `rush_ypg`, `rush_btk`, `rush_yac`, `rush_long`, `rec_avg`, `rec_ypg`, `rec_rac`, `rec_rac_avg`, `rec_long`. `UNIQUE(season, player_name, team_id, position)` and unique `(season, player_id)` where `player_id` is set (`2026_10_24_player_id_keys.py`, which re-linked the existing rows from the refreshed roster and merged a player's duplicate rows, larger value per stat); trigger `season_stats_fill_player_id` as for `player_stats`.
- **Capture stat categories** (`2026_10_23_capture_loader.sql`; hybrid layout, docs/PIPELINE_MIGRATION.md phase 3): every category the Season Stats / game player-stats capture has beyond offense, in a season table (`season`, `player_id`, PK both) and a game table (`game_id` FK ON DELETE CASCADE, `player_id`, PK both), each with `team_id`, `position`: `season_stat_defense` / `game_stat_defense` (`solo`, `assists`, `tackles`, `tfl`, `sacks`, `ints`, `int_yds`, `int_avg`, `int_long`, `deflections`, `forced_fumbles`, `fumble_recoveries`, `fumble_yds`, `blocks`, `safeties`, `tds`), `_kicking` (`fgm`, `fga`, `fg_pct`, `fg_long`, `fg_blocked`, `xpm`, `xpa`, `xp_pct`, `xp_blocked`, `fgm_29` … `fga_50`, `kickoffs`, `touchbacks`), `_punting` (`punts`, `yards`, `average`, `net_yards`, `net_average`, `blocked`, `inside_20`, `touchbacks`, `longest`), `_kickreturn` / `_puntreturn` (`returns`, `yards`, `average`, `tds`, `longest`), `_blocking` (`sacks_allowed`), `_general` (`games_played`, `downs_played`; game level `downs_played` only). A row exists only where Madden lists the player (defense is listed by tackles, about the top 250 defenders; a missing row is unknown, not zero). The writer tools read them through the `season_stat_lines` / `game_stat_lines` views (§7: `get_player_season_stats`, `get_game_box_score`, `get_top_rated_players`, `get_defense_special_teams_leaders`).
- **Views** `season_stat_lines` (one row per `player_seasons` row: offense from `season_stats` + every category, prefixed `blk_`, `def_`, `kick_`, `punt_`, `kr_`, `pr_`) and `game_stat_lines` (one row per player and game that has any game-level row).
- **`captures`**: one row per capture run: `folder` (unique), `mode` (weekly | roster | season_schedule | games), `season`, `week`, `status` (captured | extracted | loaded | failed), `row_counts` (jsonb), Gemini token totals, `error`, `captured_at` / `extracted_at` / `loaded_at`.
- **`llm_usage`**: one row per Gemini call: `source` (extract | newsroom), `capture_id` (FK, nullable), `label`, `model`, `input_tokens`, `cached_tokens`, `output_tokens`, `thinking_tokens`, `created_at`.
- **`awards`**:
  - `id` (PK), `season` (int), `award_type` (varchar), `player_id` (FK), `player_name` (varchar), `team_id` (FK), `coach_name` (varchar), `coach_id` (FK `coaches`, COTY rows; `2026_10_30_coaches.sql`), `position` (varchar). `UNIQUE(season, award_type)`. The historical import (`pipeline/batch_menus.py`) writes COTY with both `coach_name` and `coach_id`, creating the `coaches` row if the name is new.

### 3.3a Coaches (`db/migrations/2026_10_30_coaches.sql`, `2026_10_31_coach_baseline.sql`, `2026_11_01_coach_schemes.sql`, `2026_11_02_drop_coach_archetype.sql`, `2026_11_03_whitfield_2034.sql`, `2026_11_05_coach_tendencies.sql`)
Head coaches only. Nothing reads these tables yet.
- **`coaches`**: `id` (PK), `coach_name` (varchar, unique), `offensive_scheme`, `defensive_scheme` (varchar, nullable). The schemes are the real-life 2026 schemes of the 32 coaches of 2026 (`2026_11_01_coach_schemes.sql`), set from the head coach and the 2026 coordinators (Wikipedia coordinator lists, 2026-07-12) by coaching tree. They are classifications, not official labels. Offense is one of `Wide Zone West Coast` (Shanahan/McVay tree), `West Coast`, `Spread`, `Erhardt-Perkins`; defense is the base front, `3-4` or `4-3`. Whitfield has none. (`archetype` was dropped by `2026_11_02_drop_coach_archetype.sql`; it was never filled.)
  - **Tendencies** (`2026_11_05_coach_tendencies.sql`): `pass_lean`, `fourth_down_aggressiveness`, `blitz_rate`, `man_coverage`, each `Low` / `Medium` / `High` (CHECK), for the 32 coaches of 2026. Playbooks haven't changed since 2026, so these hold for the whole franchise. They come from nflverse play-by-play and NGS participation data (regular season). Each source team-season is ranked against the 32 teams of its season: top 11 High, next 10 Medium, bottom 11 Low. Pass lean is pass rate over expected on neutral 1st–3rd downs (win probability 20–80%, outside the last two minutes of a half). Fourth-down aggressiveness is the go rate on 4th-and-3-or-less from the own 30 on (win probability 10–90%). Blitz rate is the share of dropbacks with 5+ rushers. Man coverage is the share of charted coverages that are man. The source is the 2026 play-caller's last play-calling season (offense from the offensive play-caller, defense from the defensive one). Fourth down comes from the head coach's last head-coaching season, since the head coach makes that decision. The six first-time head coaches (M. LaFleur, Minter, Brady, Monken, Kubiak, Hafley) get Medium. The migration's comments list each coach's source and raw rates. These are real-world tendencies, and Madden's play-calling follows its playbooks, so they are a proxy to check against franchise results. Nothing reads them yet.
- **`coach_tenures`**: `coach_id` (FK), `team_id` (FK), `start_season`, `end_season` (NULL = current). `UNIQUE(coach_id, team_id, start_season)`. A coach's per-season record in the franchise comes from joining this to `team_seasons`. Every coach except Marcus Whitfield has coached the same team since 2026 (the franchise's first season). Carolina: Dave Canales 2026–2033, Marcus Whitfield from 2034. All 33 coaches have rows: the 32 head coaches of 2026 (tenure from 2026; Canales's ends 2033) and Whitfield. `2026_10_30_coaches.sql` seeded Carolina and the Coach of the Year winners, and `2026_10_31_coach_baseline.sql` added the rest. That migration fails unless every team has exactly one 2026 head coach.
- **`coach_seasons`**: `coach_id` (FK), `team_id` (FK), `season`, and the Madden coach screen's career counters as of that season: `career_wins`, `career_losses`, `career_ties`, `winning_seasons`, `playoff_wins`, `playoff_losses`, `super_bowl_wins`, `super_bowl_losses`. `UNIQUE(coach_id, season)`. These totals include pre-franchise NFL careers, so they are stored as shown rather than rebuilt from `games`. **Baseline row:** season 2025 with `team_id` NULL holds each 2026 coach's real NFL head-coaching career through 2025, the starting point of the franchise (`2026_10_31_coach_baseline.sql`; source: Wikipedia's current-coaches list as of 2026-07-12, plus each coach's year-by-year record for winning seasons (W > L, partial and interim seasons included) and Super Bowl results). Whitfield had no head-coaching career before 2034, so he has no baseline row. His one franchise-season row is 2034 (Carolina): 13-4, a #1-seed bye, 3-0 in the playoffs and a Super Bowl win (`2026_11_03_whitfield_2034.sql`, per JR; the 2034 regular season and early playoff rounds aren't in `games`). No other franchise-season rows (2026–2035) yet.

### 3.4 Fantasy Analytics & Projections
- **`fantasy_projections`**:
  - `id` (PK), `season` (int), `week` (int), `player_id` (int, players.id — no FK constraint), `player_name` (varchar), `position` (varchar), `team_id` (FK, int), `projected_points` (numeric), `avg_points` (numeric), `matchup_stars` (int 1–5), `pos_rank` (varchar e.g. 'QB1', 'WR1'), `adp_rank` (int), `ros_rank` (varchar), `ros_total_points` (numeric), `delta` (numeric). `UNIQUE(season, week, player_id)`.
  - **Week 1 = preseason ADP board**, written by `engines/fantasy_adp_engine.py`: the top 300 players by simulated ADP (`fantasy_sim_adp.adp`, ties by `draft_score`), `adp_rank` = that order, upserted on `(season, week, player_id)`. Writing it also refreshes `adp_rank` on the season's weeks 2–18 rows. Some older week 1 rows have no `player_id`; every reader ignores those.
    - `draft_score` (not stored) is the consensus value board: OVR × positional scarcity (HB 1.35, WR 1.30, TE 1.15, QB 0.9–1.10 by OVR tier), + team pass/rush offense rank bump, + last season's MVP/OPOY/SBMVP/OROY bumps, + up to 3 for popularity, × 0.7 under OVR 80.
  - **Weeks 2–18 = weekly projections**, written by `engines/fantasy_projection_engine.py`: one row per rostered QB/HB/FB/WR/TE (≈456 in 2035), using only games completed before that week, so past weeks show what was projected before kickoff. Re-running a week replaces its rows (rows for players no longer projected are deleted). Week 1 is never projected (no earlier games; there is no prior-season data yet).
    - `projected_points` = baseline × matchup factor. Baseline is the player's PPR per team game to date (team games with no stat line count as 0), shrunk toward a depth-chart prior by 0.25 pseudo-games; the prior is the league's PPR per team game for that depth slot (rank by OVR within team and group, capped at QB2/RB3/WR4/TE2). Matchup factor = (opponent's PPR allowed to the group, shrunk toward the league average by 16 pseudo-games, ÷ league average)^0.5.
    - `avg_points` = PPR per appearance to date; `delta` = `projected_points − avg_points`.
    - `matchup_stars` = the opponent's PPR allowed per game to the group, split into five even groups (Postgres-style `NTILE(5)`) across the 32 defenses, 1 = toughest, 5 = easiest. Point-in-time and per game, so it can differ from `vw_fantasy_defense_vs_position` (season totals).
    - `pos_rank` ranks players with a game that week within QB/RB/WR/TE (RB = HB + FB), e.g. `RB12`. Players on bye get `projected_points` 0, NULL `pos_rank`/`matchup_stars`/`delta`, so they stay on the ROS board but drop out of that week's rankings.
    - `ros_total_points` = sum of the player's projections for that week through week 18 against each remaining opponent; `ros_rank` ranks it within the group.
    - `adp_rank` is copied from the season's week 1 row so ROS `TREND` (`adp_rank − overall_ros_rank`) works.
    - **Accuracy (2035 backtest, weeks 2–17, depth-chart starters QB1/RB1–2/WR1–3/TE1):** MAE 3.29 PPR vs. 6.18 for guessing the league average; correlation 0.79. Almost all of it comes from the per-game average. Production here is very stable week to week; the matchup factor adds little, and recency weighting was tested and dropped. Check with `--backtest`.
  - When a player has no stored row for the week, the player outlook (`GET /api/player/{id}`, §4.3) falls back to a rough estimate with no ranks.
- **Simulated fantasy leagues** (`engines/fantasy_league_sim.py`, `db/migrations/2026_10_21_fantasy_league_sim.sql`; design in `docs/FANTASY_EDITORIAL_MATRIX.md` §5). 100 leagues × 12 teams, full PPR, 15 rounds, lineup QB/2 RB/2 WR/TE/FLEX:
  - **Draft** (once per season, from `fantasy_adp_engine`; stored, so in-season OVR changes don't redraft): snake order. Each manager reads `draft_score` with personal noise (sd 1.2%) and values players over replacement (the 12th QB/TE, 30th RB/WR; QB value × 0.4, TE × 0.8), minus backup penalties (6 per QB, 10 per TE already rostered; RB/WR past five fade) and caps (QB/TE 2, RB/WR 7); late picks are forced to fill an empty starting slot. Strategy adjustments: `BPA` none; `HERO_RB` one RB in rounds 1–2 then none through round 5; `ZERO_RB` no RB before round 5; `RB_HEAVY` 2–3 RBs in rounds 1–4; `EARLY_QB` / `EARLY_TE` one in rounds 1–3. Seats draw strategies independently from the season mix.
  - **Strategy mix:** first season `DEFAULT_MIX` (BPA 35%, Hero RB 15%, Zero RB 15%, RB Heavy 15%, Early QB 10%, Early TE 10%). Later seasons: lift = title share ÷ manager share; new ∝ old × lift^0.5, blended 50/50 with the old mix, floor 5%.
  - **Season** (re-simulated from the stored draft on every run, seeded per season/league/week): lineups start the highest `projected_points` (week 1: best ADP); scores are actual PPR (`player_stats.fantasy_points`); 12-team round robin weeks 1–14; seeds by wins then points for; 6-team playoffs (seeds 1–2 bye in week 15, semifinals week 16, final week 17; ties to the higher seed). Before each week, managers in reverse-standings order make up to 2 add/drops (each with probability = `activity`, 0.2–1.0) when a free agent's value beats the drop's by 15% + 1 point: value = `ros_total_points` scaled to the fantasy weeks left (playoff weeks: next game × weeks left), × (1 + `reactivity` (0–0.5) × last week's PPR vs. his average, clamped −0.5..1). They also stream a QB/RB/WR/TE when no rostered player at a starting position plays that week.
  - **`fantasy_sim_picks`**: `season`, `league`, `pick` (1–180), `round`, `team` (1–12 = draft slot), `player_id`. PK `(season, league, pick)`.
  - **`fantasy_sim_adp`**: `season`, `player_id`, `adp` (mean pick over 100 drafts; undrafted = pick 181), `min_pick`/`max_pick` (drafted leagues only; NULL if never drafted), `draft_pct`. Every board player (QB/HB/WR/TE) has a row.
  - **`fantasy_sim_player_weeks`**: `season`, `week` (rosters/lineups going into that week), `player_id`, `rostered_pct`, `started_pct` (NULL for a week without projections). Only players on at least one roster. Rewritten on every run.
  - **`fantasy_sim_teams`**: per league seat: `strategy`, `activity`, `reactivity`, `wins`/`losses`/`ties`, `points_for`/`points_against`, `seed` (after week 14), `playoff_finish` (`CHAMPION`, `RUNNER_UP`, `SEMIFINAL`, `QUARTERFINAL`, `MISSED`), `final_rank` (1–12 once week 17 is final).
  - **`fantasy_sim_strategy_mix`**: per season and strategy: `share` (target mix), `managers` (seats drawn), and once week 17 is final `titles`, `playoff_teams`, `avg_points_for` (regular season). Feeds the next season's mix; not published in articles.
  - 2035 (first run, drafted from end-of-season ratings): ADP 1 Trey'Mane Davis 1.8 (range 1–6); titles BPA 33, Zero RB 24, Early QB 20, Hero RB 14, RB Heavy 8, Early TE 1. A full season simulates in about 3 seconds.
- **`vw_fantasy_scores` (View)**:
  - `game_id`, `player_name`, `team_id`, `season`, `week`, `ppr_score` (= `player_stats.fantasy_points`).
- **`vw_fantasy_defense_vs_position` (View)**:
  - Aggregates PPR fantasy points conceded by each defensive team per position across completed games.
  - Dynamically calculates:
    - `total_allowed`, `avg_allowed`
    - `min_allowed`, `max_allowed`, `league_avg_allowed`
    - `matchup_stars`: `NTILE(5) OVER (PARTITION BY season, position ORDER BY total_allowed ASC)::numeric`.

### 3.5 Media & Content Tables
- **`agent_memories`**:
  - `id`, `agent_id` (unique), `archetype`, `assigned_team_id`, `persistent_memory` (the agent's fixed identity line), `handle`, `display_name`, `updated_at`, `bio` (Agent Wire profile line), `followers` (simulated, set once by the social desk; NULL until it runs), `avatar_color` (unused until avatars are decided).
  - `db/migrations/2026_10_22_agent_wire_social.sql` gave every agent that posts a non-generic handle, display name and bio (approved by JR), renamed beat reporters whose templated surnames repeated (43) and same-team die-hards / bandwagon fans who shared a first name, and added the social accounts `The_Flex_Spot` (`FANTASY_GURU`, @TheFlexSpot), `Gridiron_Memes` (`NFL_MEMES`, @GridironMemes) and `Mock_Draft_Marty` (`DRAFT_SICKO`, @MockDraftMarty). `Skip_Bayless_National` (article byline) and `Skip_Bayless` (`SKIP_TWEET`) share @RealSkipBayless; Agent Wire shows them as one profile. `db/migrations/2026_10_25_former_players.sql` added the 20 `FORMER_PLAYER` accounts (agent ids `Former_<Name>`, §7.8).
- **`agent_memory_entries`** (§7.7): `id`, `agent_id`, `season`, `week`, `article_id` (FK `news_feed` ON DELETE CASCADE), `kind` ('TAKE'), `text`, `team_id`, `player_id`, `created_at`.
- **`agent_predictions`** (§7.7): `id`, `agent_id`, `season`, `week` (the predicted game's week), `article_id` (FK `news_feed` ON DELETE CASCADE), `game_id`, `label` (roundtable panellist or ''), `team_a_id`, `score_a`, `team_b_id`, `score_b`, `created_at`; unique `(article_id, game_id, label)`.
- **`agent_season_summaries`** (§7.7): `agent_id`, `season`, `through_week`, `summary`, `updated_at`; primary key `(agent_id, season, through_week)`.
- **`media_outputs`**:
  - `id`, `game_id` (FK to `games(id)` ON DELETE CASCADE), `content_type`, `payload_json` (jsonb), `created_at`.
- **`news_feed`**:
  - `id`, `season`, `week`, `team_id`, `content_type` ('ARTICLE' | 'TWEET'), `persona`, `article_type`, `slot`, `headline`, `excerpt`, `content`, `created_at`.
  - Agent Wire post columns (`db/migrations/2026_10_22_agent_wire_social.sql`; filled by the social desk, `docs/SOCIAL_MATRIX.md`): `agent_id` (author), `post_kind` (REACTION, PERFORMANCE, BAD_GAME, COMPARISON, TEAM_STORY, PLAYER, BEAT, STANDINGS, SHARE, REPLY, PREVIEW, MEME), `posted_at` (simulated in-world time; `created_at` stays the real generation time), `reply_to_id` (FK `news_feed` ON DELETE CASCADE), `linked_article_id` (FK `news_feed` ON DELETE SET NULL; the article a SHARE links), `subject_game_id`, `subject_player_id`, `likes`, `reposts`, `reply_count`, `views`, `hashtags` (text[]).
- **Record book** (`db/migrations/2026_10_26_nfl_records.sql`, §2.9): `record_categories` (`category` PK, `label`, `scope` SEASON / GAME, `subject` PLAYER / TEAM, `better` HIGH / LOW, `sort_order`, `coverage` FULL / BOOK); `franchise_records` (`team_id`, `category`, `value`, `holder` (player name, or "2007 New England Patriots"), `player_id`, `season`, `source` NFL / BOOK / LEAGUE; unique `(team_id, category, holder, season)`): every holder a franchise record has had; `league_record_entries` (`category`, `value`, `holder`, `team_id` (the franchise under its current name), `player_id`, `season`, `game_date` (NFL single games), `game_id` (league single games), `source`): entries on the NFL's all-time top-10 lists. Views: `franchise_records_current` (current holders, ties included), `league_top10` (`rank` computed, ties share it) and `league_records` (per category, the best of the complete franchise records (season categories with FULL coverage) and the top of `league_top10`).
- **`social_trends`** (`docs/SOCIAL_MATRIX.md` §4): `id`, `season`, `week`, `rank`, `kind` (TEAM / PLAYER / GAME / HASHTAG), `key`, `title`, `category`, `description`, `volume` (simulated post count), `score`; unique `(season, week, kind, key)`. Empty until the social desk is built.
- **`article_teams`**:
  - `id` (PK, int), `article_id` (FK to `news_feed(id)` ON DELETE CASCADE), `team_id` (FK to `teams(id)` ON DELETE CASCADE), `mention_type` (varchar: 'PRIMARY' | 'FEATURED' | 'MENTIONED'), `mention_count` (int), `created_at` (timestamp), `UNIQUE(article_id, team_id)`.
- **`power_rankings`**:
  - `id` (PK), `season`, `week` (the week published for, i.e. entering that week), `rank` (1–32), `team_id` (FK to `teams(id)` ON DELETE CASCADE), `previous_rank` (rank in the latest earlier stored week of the season; NULL if none), `movement` (`previous_rank − rank`, positive = up; NULL if none), `record` (W-L snapshot from `team_seasons` at store time), `kicker`, `blurb`, `article_id` (FK to `news_feed(id)` ON DELETE CASCADE), `persona`, `created_at`. `UNIQUE(season, week, team_id)` and `UNIQUE(season, week, rank)`. Written only by `newsroom/power_rankings.store_power_rankings` (see 7.6).
- **`article_players`**:
  - `id` (PK, int), `article_id` (FK to `news_feed(id)` ON DELETE CASCADE), `player_id` (FK to `players(id)` ON DELETE CASCADE), `team_id` (FK to `teams(id)` ON DELETE SET NULL), `mention_type` (varchar: 'PRIMARY' | 'FEATURED' | 'MENTIONED'), `mention_count` (int), `created_at` (timestamp), `UNIQUE(article_id, player_id)`.

### 3.6 Triggers
- **`trigger_update_team_records`** (`AFTER INSERT OR UPDATE ON games`, function `update_team_records()`): on insert, or when `status`/scores change, recomputes both teams' `wins`/`losses` for that season from completed games and upserts them into `team_seasons`.
- **`trigger_sync_season_stats_team`** (`AFTER UPDATE OF team_id ON player_seasons`, function `sync_team_to_season_stats()`): when a player's team changes, moves their `season_stats` rows for that season (team NULL or the old team) to the new team, merging counters into an existing new-team row where one exists.

---

## 4. Backend Architecture (`server/api.py` - FastAPI)

The backend operates on Python 3.11+ using FastAPI, `uvicorn`, and `psycopg` (connection pooling with `dict_row`).

### 4.1 System & Schedules
- `GET /api/status`: Returns current league season and week from `league_state`.
- `GET /api/schedule?season={season}&week={week}`:
  - Returns matchups for the week, records, kickoff times, home/away TWI (`home_twi`, `away_twi`), combined matchup `watchability`, and `excitement_index` on the 70–100 display scale. That is the final $E$ for completed games and the projection otherwise; `excitement_projected` is true for projections, and the value is null if a game hasn't been projected yet.
- `GET /api/standings/{season}`:
  - Division and conference standings, wins, losses, win percentage, streak, points for/against, and last 5 games.
- `GET /api/power_rankings?season={season}&week={week}`:
  - Stored weekly Power Rankings (7.6): `season`, `week`, `available_weeks`, the source `article` (`id`, `headline`, `excerpt`, `persona`, `created_at`), and `rankings` ordered 1–32 (`rank`, `previous_rank`, `movement`, `record`, `kicker`, `blurb`, `team_id`, `abbr`, `name`, `city`, `team_color`, `conference`, `division`). `season` defaults to the current season and `week` to its latest stored week; a week with no stored ranking returns an empty `rankings` list. The same article is also served by `GET /api/news/week/{week}` in `slotted.standalone`.

### 4.2 Team & Roster Hubs
- `GET /api/team/{abbr}?season={season}`: Team metadata (record, overall/offensive/defensive ratings, team color) and the season's `roster` (id, full name, jersey number, ESPN id, position, experience, OVR; best OVR first). `season` defaults to `league_state.current_season`.
- `GET /api/team/{abbr}/schedule?season={season}`: Full 18-week schedule with completed scores and upcoming dates.
- `GET /api/team/{abbr}/news?season={season}&limit={limit}`: Returns news articles and beat coverage specifically tagged for or assigned to a franchise (`article_teams` join or `news_feed.team_id`), prioritized by `mention_type` (`PRIMARY` > `FEATURED` > `MENTIONED`) and week recency. If the team's local beat hasn't been woken for the current week (no article filed under the team with a `Local Beat N` slot that week; national primers use `Slot 6–8` and don't count), it starts the on-demand local swarm wakeup (`SwarmDispatcher.dispatch_team_local`, `live=True`, `save_to_db=True`) in a background thread via `start_local_wakeup` (the check is `maybe_start_team_wakeup`, shared with the Agent Wire Team Hub) and returns the existing feed immediately; the new beat reports and the team's Agent Wire posts appear on a later load. Each (team, season, week) is attempted at most once every `LOCAL_WAKEUP_RETRY_SECONDS` (600s), which covers a run in progress and keeps repeat views from re-triggering a failed one. `NEWSROOM_AUTO_LOCAL_WAKEUP=0` disables it.
- `GET /api/team/{id}/season_stats/{season}`: Aggregated season totals (passing yards, rushing yards, points per game, points allowed per game).

### 4.3 Player Analytics & Modal Overlays
- `GET /api/player/{id}?season={season}`:
  - Player personal information and ratings.
  - Full 18-week `game_log` (accounting for bye weeks and completed game fantasy statistics).
  - Multi-season `career_stats` with games played, yards, touchdowns, and overall fantasy rank.
  - `weekly_outlook` for the league's current week (`league_state.current_week`): opponent, home/away, kickoff, `projection`, `pos_rank`/`ecr_rank`, `ros_rank` and 1–5 `stars`, all from that week's `fantasy_projections` row (`projected: true`).
    - **Bye:** if the league plays that week but the player's team doesn't, it returns `bye: true`, `projection` "0.0", the stored `ros_rank`, and null ranks/stars (no opponent fields). The modal shows a "BYE WEEK" card.
    - **No stored row** (e.g. week 1): `projection` is a rough estimate (season PPR average × 0.85–1.15 by matchup stars), `projected: false`, and the ranks are null. The modal shows "—" rather than a rank.
    - If the season has no games in the current week (e.g. viewing a past season), it uses the team's last game of that season and that week's projection row.
- `GET /api/stats/player?season={season}&category={category}`:
  - Passing, Rushing, Receiving and Scoring player leaderboards (no defense), summed from the player's `player_stats` game lines by `player_id` (a traded player's lines for every team), shown under the season's team.
- **Season and week defaults:** every endpoint whose `season` / `week` is omitted uses `league_state` (`season_week()` in `server/api.py`): the current season, and for it the current week; another season's default week is 18. Players on the game page (`GET /api/game/{id}`), the player page's game log and career, and the fantasy rankings, search and dashboard finishes are joined on `player_stats.player_id`, never by name.
- `GET /api/stats/team?season={season}&category={category}`:
  - Team leaderboards for the same four categories, summed from `team_stats` over completed games.

### 4.4 Fantasy Analytics Endpoints
- `GET /api/fantasy/dashboard?season={season}&week={week}`:
  - **Articles:** `hero` and `articles` (the 3 front-page cards) come from the stored Mu Fantasy slate (`news_feed`, `article_type` `FANTASY_*`) for the week shown: the requested week, weeks after 18 show Week 18, and a week without a slate shows the latest earlier one (`articles_week`). Each card: `id`, `slot`, `label` (article type name), `title`, `excerpt`, `author` (agent display name, else `fantasy_writers.json`), `time` (~230 words/min), `updated`, `team` (most-tagged team, lower-case, or null). `more_analysis_count` = shelf articles behind "View all". With no stored slate, `articles_source` is `placeholder` and the old placeholder hero/cards are returned.
  - Top 3 marquee games (`excitement_games`). These are ranked by editorial interest: raw (projected) $E$, +3 for a division game, and +2 per team not yet mathematically eliminated (regular season). This is the same `rank_games_by_interest` used for matchup-primer picks. Each game has `score` (display scale), `projected`, and a `note` such as "Week 18 · Projected · Division game · Playoff race".
  - Waiver wire trends, weekly projections, preseason ADP movers, and rest-of-season rank snapshots. The ADP card reads only week 1 rows with a `player_id` (later weeks carry a copy of `adp_rank`).
  - **Social takes** (`social_takes`): the requested week's 3 most-liked Agent Wire posts by the fantasy accounts (The Flex Spot, Berry, Yates), no replies or shares (`social_api.fantasy_takes`): `{id, quote, author, handle, tag}`, where `tag` is the subject player's team and position ("MIN • WR") or the post type ("START / SIT"). Empty when the week has none.
- `GET /api/fantasy/articles?season={season}&week={week}`: every Mu Fantasy article for the week shown (same week rule as the dashboard): `front_page` (Hero, Fantasy 1–3) and `more_analysis` (shelf, in order), cards as above, plus `available_weeks` (weeks with a fantasy slate) and `week` (null when none). Article text comes from `GET /api/news/article/{id}`.
- `GET /api/fantasy/rankings/weekly?season={season}&week={week}&position={pos}&limit={limit}`:
  - `mode`: `final` when every game that week is completed (ranked by actual PPR points), otherwise `projected` from that week's `fantasy_projections` rows (players on bye are excluded), and `unavailable` if neither exists.
  - Dynamic positional ranking (`pos_rank` e.g. `QB1`, `RB1`).
  - Prior-week positional finish comparison (`trend`).
  - Exact 1–5 matchup stars.
  - FLEX view strictly excludes QBs and presents a dedicated `POS` column.
- `GET /api/fantasy/rankings/ros?season={season}&position={pos}&limit={limit}`:
  - Rest-of-season consensus rankings ordered by `ros_total_points DESC`.
  - Columns: `RANK`, `PLAYER`, `ROS RANK` (on FLEX), `SEASON SOS` (1–5 stars), `PLAYOFF SOS` (Weeks 15–17, 1–5 stars), `PROJECTED ROS FPTS`, and `TREND` (`adp_rank - overall_ros_rank`).
- `GET /api/fantasy/rankings/adp?season={season}&position={pos}&limit={limit}`:
  - Consensus preseason draft board ordered by `adp_rank ASC`.
  - Columns: `RANK`, `PLAYER`, `POSITION` (positional rank ordered by ADP, e.g. first RB is `RB1`, first WR is `WR1`), `TEAM (BYE)` (e.g. `CAR (12)`), `SEASON SOS`, and `PLAYOFF SOS`.
  - Position filter and week selectors omitted for an unobstructed full-draft view.
- `GET /api/fantasy/players?season={season}&min_points=1.0&q={query}`:
  - Returns all players who have accumulated at least 1.0 fantasy point on the season (342 players).
  - Powers zero-latency client search in `FantasyHeader`.
- **Agent Wire** (`server/social_api.py`, §7.8). Only posts written by the social desk (with an `agent_id`) are served. `season` defaults to `league_state.current_season`; `week` to the latest week with posts. A post is `{id, season, week, kind, kind_label, text, posted_at, likes, reposts, replies (simulated + real), real_replies, views, hashtags, team_abbr, local, subject_game_id, subject_player_id, author {agent_id, handle, display_name, archetype, label, team_abbr, team_color, followers}, reply_to {id, handle, display_name} | null, article {id, headline, excerpt, article_type} | null}`.
  - `GET /api/social/feed?season&week&tab=foryou|latest&team={ABBR}&trend={KIND:KEY}&limit=30&offset=0`: the week's top-level posts (no replies). For You = likes + 2 × reposts + 3 × replies, times a recency weight from 1.0 (the week's newest post) to 0.5; Latest = `posted_at` descending. Returns `weeks` (weeks with posts), `as_of` (the newest post's time, the feed's "now" for relative times), `total` and `next_offset`.
  - `GET /api/social/post/{id}`: `{post, parent, replies}` (replies oldest first).
  - `GET /api/social/profile/{handle}?season&limit&offset`: `{account {handle, display_name, bio, label, archetype, team_abbr, team_color, followers, following, agent_ids}, post_count, posts}`; accounts that share a handle (Skip's byline and tweet account) are one profile.
  - `GET /api/social/trends?season&week&limit=20`: the week's `social_trends` rows, each with `id` = `KIND:KEY` for the feed's `trend` filter.
  - `GET /api/social/search?q&season&limit=40`: posts whose text, author or tagged team matches, newest first, plus up to 8 matching accounts (one per handle).
  - `GET /api/social/suggestions?season&week&limit=3`: Who to follow, the accounts whose posts drew the most likes that week.
  - `GET /api/social/team/{abbr}?season&week&tab=latest`: a Team Hub (the feed filtered to the team, plus `team {id, abbr, name, city, team_color, division, record}`). Viewing it starts the team's local wakeup through `maybe_start_team_wakeup`, the same check as the team news page; `wakeup_started` says whether it did.

### 4.5 Debug Endpoints (`server/debug_api.py`, the `#debug` page)
Reads are open like the rest of the API; every write needs header `X-Debug-Token` = `DEBUG_TOKEN` (env or `.env`; 403 when unset, 401 when wrong). The module opens its own connections (`DATABASE_URL`).
- `GET /api/debug/state`: `league_state`, the database name, the last 10 `captures` rows, `writes_enabled`, `running_job`, and `resumable`: up to 15 run folders with a `weekly_run.log` that aren't loaded (`folder`, `mode` weekly | games, `stage` capture incomplete | captured | extracted), newest first. `PUT /api/debug/state` `{current_season, current_week}` (week 1–23).
- `GET /api/debug/tables`: every table and view in `public` (`name`, `kind`, estimated `rows`, `columns`, `pk`).
- `GET /api/debug/tables/{name}?page&page_size (≤200)&sort&dir&q&filters`: rows plus `columns` (`column`, `type`, `nullable`, `generated`, `pk`), `pk`, `total`. `q` searches every column as text; `filters` is a JSON object `{column: value}`: plain text = case-insensitive contains, `=`, `!=`, `>`, `<`, `>=`, `<=` compare (value cast to the column's type), `null` / `!null`. Default order is the primary key.
- `PATCH /api/debug/tables/{name}` `{key: {pk columns}, changes: {column: value | null}}`: base tables with a primary key only; generated and key columns refused; values cast to the column type; table triggers fire as usual (e.g. a `games` score edit updates records). Returns the updated row.
- `GET /api/debug/endpoints`: every route (`path`, `methods`, `name`, docstring line, path and query params). `GET /api/debug/docs/{spec|migration|frontend}`: that Markdown file.
- Jobs (records, logs and exit codes in `captures/jobs/<id>.json|.log|.exit`): `POST /api/debug/jobs` `{kind: scan_week | backfill_week | game_scan, resume?: <captures folder>}` starts `pipeline.weekly_capture` (`--no-articles` / `--mode games`, `--resume`) in its own process session (survives an API reload; `sh` writes the exit code); 409 while another job runs. `GET /api/debug/jobs` (last 20, status `running` / `done` / `failed` / `stopped` / `lost`, log tail), `GET /api/debug/jobs/{id}/log?lines=`, `POST /api/debug/jobs/{id}/stop` (SIGINT to the job's process group: madden_capture releases every key and stops).
- **Tests:** `tests/test_debug_api.py` (token, start, log, one at a time, failure, stop, resume name; a dummy command stands in for the run).

---

## 5. Frontend & UI Architecture (`Mu_Network`)

Built on **React 19**, **TypeScript 5.7**, and **Vite 8**, featuring responsive layouts styled in `src/index.css`.

### 5.1 Shared Navigation & Hash Routing
Navigation uses a lightweight hash router (`window.location.hash`):
- `#`: Gridiron Newsroom Home (featured stories, analysis, trending topics).
- `#team-{abbr}`: Team hub with dynamic team colors, division rank, upcoming schedule, roster, and team stats.
- `#schedule`: Full interactive 18-week league schedule.
- `#game-{id}`: Comprehensive game view with box scores, drive summaries, and scoring logs.
- `#stats-team` & `#stats-player`: League statistical leaderboards.
- `#standings` & `#rankings`: Division standings and power rankings. `PowerRankingsPage` reads `GET /api/power_rankings`: one button per stored week, each team's rank, logo, record, last-week rank and movement ("NEW" when there is no previous week), kicker and blurb, with an empty state when nothing is stored.
- `#fantasy`: Mu Fantasy Analytics Command Center.
- `#fantasy-rankings`: Weekly Fantasy Rankings board.
- `#fantasy-ros`: Rest of Season Rankings board.
- `#fantasy-adp`: Preseason ADP Consensus Board.
- `#fantasy-all`: All Mu Fantasy analysis for the week (front page, then the "View all" shelf; week buttons from `available_weeks`), from `/api/fantasy/articles`.
- `#article-fantasy-{id}`: a stored fantasy article (`/api/news/article/{id}`), its markdown rendered in-app (headings, lists, tables, quotes) with a rail of the week's other fantasy articles. The dashboard hero and cards link here when a slate exists; `start-sit` / `p0`–`p2` are the placeholder pages.
- `#social`: Agent Wire Home (For You / Latest tabs, `/api/social/feed`, "Show more posts" paging).
- `#social-explore`: trends (`/api/social/trends`) and search (`/api/social/search`, posts plus matching accounts, 300 ms debounce).
- `#social-trend-{KIND}-{key}` (e.g. `#social-trend-GAME-853`, `#social-trend-HASHTAG-skol`): the posts behind one trend.
- `#social-post-{id}`: a post with its parent (for a reply) and replies (`/api/social/post/{id}`).
- `#social-profile-{handle}` (no @): an account's profile and posts (`/api/social/profile/{handle}`).
- `#social-teams`, `#social-team-{abbr}`: Team Hubs; a hub's feed comes from `/api/social/team/{ABBR}`, and viewing it starts the team's wakeup (a note says when it did).

### 5.2 Key UI Components & Interactions
1. **Interactive Fantasy Player Modal (`FantasyPlayerModal`)**:
   - Accessible by clicking any player across weekly rankings, ROS rankings, ADP board, dashboard, or search bar.
   - Four integrated tabs:
     - **Game Logs**: Full 18-week breakdown with position-adaptive primary stat highlight (Blue column: Passing Yards for QBs, Rushing Yards for RBs, Receiving Yards for WRs/TEs).
     - **Season Stats**: Multi-season statistical and fantasy scoring trends.
     - **Outlook**: Next opponent breakdown, kickoff time, projected fantasy points, and 19px 1–5 defensive matchup stars.
     - **News**: Analytical blurbs and beat commentary.
2. **Global Player Search (`FantasyHeader`)**:
   - Accessible from any fantasy view with global `⌘ K` / `Ctrl+K` shortcut support.
   - Queries `/api/fantasy/players`, displaying official ESPN headshots, team, position, and season fantasy points.
   - Selecting a player opens their modal instantly.
3. **Preseason ADP Board**:
   - Clean draft board view with `RANK`, `PLAYER`, `POSITION` (e.g. `RB1`, `WR1`), `TEAM (BYE)`, `SEASON SOS`, and `PLAYOFF SOS`.
4. **Agent Wire (`SocialFeedPage`, `SocialPost`, `SocialPostList`, `SocialRightRail`, … in `App.tsx`)**:
   - All posts, trends, profiles and Who to follow come from `/api/social/*` (§4); the hardcoded mock posts and topics are gone. The fantasy page's "Signal stream" shows `/api/fantasy/dashboard` `social_takes` (empty state when none), each linked to its post.
   - **Post card:** initials avatar on the author's team colour (or a colour per kind of account; photos are parked), name, handle, relative time, team logo, a label badge (fan / beat reporter / insider / meme page / analyst), "Replying to @…", the text with #hashtags linked to their trend and @handles to their profile, a card linking the shared article (`#article-{id}` or `#article-fantasy-{id}`), and reply / repost / like counts (1.2K, 3.4M).
   - **Times** are the simulated `posted_at`: relative ("12m", "5h") against the feed's newest post (`as_of`), else the date ("Dec 31"); the post page shows the full time, date and views.
   - Loading, error ("Agent Wire couldn't load…") and empty states on every view.
5. **Social NFL Container (`SocialNflWidget`)**:
   - Embedded in the Agent Wire right rail.
   - Replaced generic CSS text shield with the official ESPN NFL shield logo.
   - Displays the top 3 most exciting matchups of the active week ranked by Excitement Index ($E$).
6. **Team Hub Beat Coverage & Article Feed (`TeamPage`)**:
   - Queries `GET /api/team/{abbr}/news?season={season}` to populate the team spotlight hero card and 4-story beat feed with real articles tagged for the franchise (`article_teams`), with seamless fallback to positional template beats.
7. **Live Newsroom Front Page (`App.tsx`)**:
   - Fetches `GET /api/news/week/{week}` to bind the Hero Slot story, secondary analysis cards, feature trios, and trending local coverage primers directly to autonomous newsroom outputs. The sidebar Power Rankings card shows the top 3 from `GET /api/power_rankings` (latest stored week), or a placeholder when none is stored.
8. **Team & League Logos**:
   - Pulls high-resolution official SVG/PNG logos from ESPN's global CDN (`https://a.espncdn.com/i/teamlogos/nfl/500/${abbr}.png`), normalized across all 32 franchises.

## 6. The Automated Pipeline Workflow (End-to-End)

### 6.0 Weekly run (capture pipeline, `docs/PIPELINE_MIGRATION.md` phase 5)
The capture pipeline runs alongside the old stream ingestion below until phase 6 retires that.
1. **Start** (JR, Oct 14: on demand; the broadcast trigger that watched for Advance Week was too brittle and was removed): after Advance Week, JR taps a button on the `#debug` page (phone or Mac). JR knows the run is going because his controller disconnects when Remote Play takes the console. Buttons: **Scan week** (the full run below), **Backfill previous week** (the same, `--no-articles`), **Game scan** (`--mode games`: every game of last week, box scores + player stats loaded with `--replace` over the backfill's lines, ~7 min a game, never articles); each can resume a stopped run folder.
2. **Weekly run** (`pipeline/weekly_capture.py`, a job of `server/debug_api.py` or run by hand; one at a time via `captures/.weekly_run.lock`, log in `captures/<run>/weekly_run.log`): notice → `madden_capture.py --connect` → `madden_capture.py --out <run>` (Season Stats, Team Stats, last week's schedule) → notice to turn the controller back on and restart the broadcast → `extract.py` → `pipeline.load_capture` → `generate_team_and_player_stats(season, strict=True)` (an engine error raises) → `newsroom.weekly_run.run_weekly_swarm` → shrink (PNG → WebP q90 full resolution, `stop_debug` / `scope_debug` / `week_debug` / `team_debug` / `probe` / `explore` deleted, CSVs kept; ~1.7 GB → ~46 MB) → notice. Any failed step stops the week (nothing after it runs, so a failed load means no backfill, engines or articles) and sends an urgent notice with the `--resume` command. Resume starts at the first step not done: capture is done when the run wrote `.capture_done` (older weekly runs: `week.json`), extract when the mode's CSVs exist (weekly: `schedule.csv` + `stats_*.csv`; games: `game_scores.csv`), load when the `captures` row is `loaded`; the backfill and articles are safe to rerun.

### 6.1 Stream ingestion (retired in phase 6)
The entire intelligence apparatus is triggered autonomously via an optimized, concurrent pipeline designed to ingest streaming video and output localized AI content in real time.

1. **Ingestion (Live Stream & Worker Pool Concurrency):**
   - `pipeline/ocr_extractor.py` and `TemplateClassifier` monitor the Madden video stream at 1 FPS.
   - Dynamic batch jobs and single-frame stats are queued to an asynchronous **`ApiWorkerPool`** (default 3 concurrent workers, scalable via `--workers`).
   - Multiple extraction jobs run simultaneously against Gemini (`gemini-3.8-flash`), with thread-safe synchronization of game IDs, session state, and real-time database UPSERTs.
   - When the user scrolls Post-Game stats, box scores are instantly UPSERTED into `player_stats`.
   - When the user scrolls the Weekly Schedule, it logs final CPU-simmed scores and win/loss records.
   - When the user scrolls Season Stats, it overwrites the `season_stats` ledger.
2. **Mathematical Backfill:**
   - `engines/game_stat_generator.py` is invoked for `league_state.current_season` (no season is hardcoded in the backfill or the pipeline's calls). It subtracts the player's lines already stored this season (matched by `player_id`, on any team, so a traded player's old-team games count; a `season_stats` row without one falls back to name + team + position) from the `season_stats` and distributes the remaining stats into the missing CPU-simmed games, so season totals match `season_stats`. Each player's remainder is split across those games in proportion to each game's combined score (weight $\max(1, \text{total points}/40)$), so per-game box scores for CPU-simmed games are synthetic: only season totals and final scores are real (see the caveat in 2.6). It inserts with the line's `player_id` and skips a row that hits either unique key. Team rows are the sums of those player lines, with third/fourth-down fields left NULL; right after the insert it runs `engines/team_box_estimator.run(season)` (§2.6), which fills every still-NULL per-game conversion, red-zone and penalty cell from the `team_seasons` remainder (an estimator failure stops a strict run).
3. **Culture Engines (Dependency-Ordered Execution & Batch Updates):**
   - Appended to the end of the backfill generator, `engines/game_stat_generator.py` first runs `gotw_engine.batch_evaluate(season, only_missing=True, game_ids=...)` to score the Game Excitement Index ($E$) for newly completed games (those with no `excitement_index`) and any games it just backfilled. Previously scored games are left unchanged. This must precede TWI, which only counts games with a non-null `excitement_index`.
   - It then runs `TWIEngine().backfill_twi()` followed by `PopularityEngine().calculate_popularity()`, sequentially in dependency order, because franchise popularity reads the `watchability_index` that TWI writes. These two used to run in parallel, so popularity could read stale or partially updated TWI.
   - Between TWI and franchise popularity it runs `GameCreditEngine().calculate(season)` (Section 2.4) `TeamEfficiencyEngine().calculate(season)` (Section 2.6) and `PlayoffCloutEngine().calculate(season)` (Section 2.7). They read only `games` / `team_stats` / `player_stats` / `awards`, so their position is not a dependency.
   - Once franchise metrics are computed, `PlayerPopularityEngine().calculate_popularity()` executes vectorized batch updates using PostgreSQL `executemany` (reducing DB execution time from ~1s to ~0.16s).
   - Then `project_scheduled_games(season)` refreshes `games.projected_excitement` for every still-scheduled game, so the next assignment brief and primer picks see current projections.
   - Then `project_upcoming_week(season)` (`engines/fantasy_projection_engine.py`) writes fantasy projections for the first week that still has scheduled games. It reads only `games`, `player_seasons` and `player_stats`, so it has no dependency on the culture engines.
   - Finally, `simulate_season(season)` (`engines/fantasy_league_sim.py`) re-simulates the 100 fantasy leagues through that week (rostered %, start %, standings). It needs the season's stored draft (from `fantasy_adp_engine`) and skips with a message if there is none.
4. **The Swarm Trigger & Editorial Commissioning:**
   - The swarm is a separate step from the stats sync. `game_stat_generator.py` only syncs stats and runs the engines; the video pipeline (`pipeline/main.py`, both live and file modes) calls `newsroom.weekly_run.run_weekly_swarm(season)` right after it on the same thread. The admin dashboard's `season_stats` edits and a direct `python3 -m engines.game_stat_generator` run no longer publish anything.
   - **Publication week:** a Week N slate is published before Week N kicks off (recaps Week N−1, previews Week N). `run_weekly_swarm` picks N = the latest *fully completed* week + 1, where fully completed means every non-`BYE` game that week is `COMPLETED`; Week 1 if none. A partly played week doesn't count, so a mid-week Season Stats capture maps to an already-published slate. After Week 21 it publishes Week 22; Week 23 (Super Bowl) and the Week 1 preseason slate must be run by hand with `--week`. Once the Super Bowl week is finished it publishes nothing.
   - **Once per week:** it skips the week if `news_feed` already has a national-slot article for it (`slot` = `Hero Slot`, `Standalone`, or `Slot N`; local wakeups use `Local Beat N` and don't count), unless `--force`. It holds a PostgreSQL advisory lock on (season, week) for the run, so two triggers can't generate the same week at once.
   - **Memory refresh:** in a live run, before the brief, `agent_memory.refresh_season_summaries` rewrites the "season so far" summary of every agent with at least 3 new takes since its last summary (§7.7).
   - `run_weekly_swarm` compiles a dual-horizon Weekly Slate Digest:
     - **`season` / `calendar_week` / `recap_week`**: labels for the publication week and the week being recapped.
     - **`recap_slate`**: Completed scoreboard from `week - 1` (score, margin, excitement, team IDs), most exciting first, for retrospective recaps.
     - **`standout_performances`**: The top 5 individual stat lines from `week - 1`, ranked by passing, rushing and receiving yards and touchdowns.
     - **`preview_slate`**: Upcoming scheduled matchups for `week`, ordered by projected excitement. `projected_excitement` comes from `games.projected_excitement` on the 70–100 display scale, for prospective previews.
     - **`excitement_scale_note`**: Tells writers that Excitement Index values are on the 70–100 scale (≈70 dull, ≈80 typical, 90+ classic) and that projections are rough pre-game estimates. `recap_slate` values and the writer DB tools (`get_weekly_slate`, `get_team_season_schedule`, box score and advanced analytics) use the same display scale.
     - **`season_landscape`**: The league as it stood entering the week. `division_standings` gives every division's teams, best record first, as one line each (`City Team (ID: n) W-L[-T], PF, PA, diff`), counted from completed **regular-season** games before the publication week by `newsroom/season_landscape.division_standings` (playoff games excluded; `standings_through_week` is `week - 1`, capped at 18; `tests/test_season_landscape.py`). `most_watchable_teams` and `most_popular_teams` are the top 5 by `team_seasons` watchability and popularity, labelled with the same standings records.
     - **`curatorial_guidance`**: Tells writers they may choose their own games, teams and players, except that an `EDITORIAL DESK SUBJECT ASSIGNMENT` in the prompt fixes the subject; then the freedom applies only to angles within it.
     - The brief is identical for every article in a run (Agent Wire posts get their own facts packets, §7.8). (The former `slate_manifest`, a duplicate of `recap_slate`, and `league_leaders`, now inside `season_landscape`, were removed.)
   - It then fires `SwarmDispatcher().dispatch_weekly_national(live=True, save_to_db=True, max_workers=5)`, which commissions the full 10-slot Editorial Matrix slate (`docs/EDITORIAL_MATRIX.md`) via the autonomous Editorial Assignment Desk. The optional `article_count` argument truncates the slate and is for tests/debugging only.
   - Local on-demand team wakeups (`POST /api/news/team/{team_id}/wakeup`; only `live=true` calls Gemini and saves, `live=false` is a dry run) also utilize `ThreadPoolExecutor(max_workers=5)` to generate the team's local beat portfolio, then (live, saved wakeups only) the team's Agent Wire posts through `newsroom/social_publish.run_team_social`, which also shares each new local article from its reporter (§7.8). The portfolio is planned by `newsroom/local_portfolio.plan_local_portfolio` (see §7.2).
   - The Editorial Desk commissions a structured 10-article matrix (Hero, Slot 1, Slot 2, Slots 3–5 Feature Trio, Slots 6–8 Local Matchup Primers, and Standalone Power Rankings), matches each format to an eligible national writer or beat reporter, and dispatches them with custom curatorial directives and explicit temporal framing (`RETROSPECTIVE`, `PROSPECTIVE`, or `CUMULATIVE`). Other local beat formats (`LOCAL_POSTGAME_REPORT_CARD`, `LOCAL_PLAYER_SPOTLIGHT`, `LOCAL_SEASON_PROGRESS_REPORT`) are not part of the weekly national run; they are produced only by on-demand team wakeups.
   - **Only real output is saved:** `_execute_agent` writes to `news_feed` only when `live=True` and the model returned text. A missing API key, a Gemini exception, an empty response, or offline mock text (`live=False`) is logged as `[NOT SAVED]` and returned with an `error` field instead. A weekly run where every article failed therefore leaves the week unpublished, and the next trigger retries it (`tests/test_article_saving.py`).
   - **Agent Wire posts:** the old reaction tweets (random pundits and fans prompted with the full brief) are gone. After the national and fantasy slates, `run_weekly_swarm` calls `newsroom.social_publish.run_social_week`, which plans and publishes the week's ~100 Agent Wire posts; team wakeups post through `run_team_social` (§7.8). `--social-only` / `--skip-social` run or skip that step; `--fantasy-only` skips it; `--skip-fantasy` keeps it. The result dict gains a `social` status.
   - **Parallel Multithreaded Execution:** Dispatcher leverages Python `concurrent.futures.ThreadPoolExecutor` (default 5 workers), collapsing total weekly generation time from ~90s sequential down to ~12–15s parallel.
   - **API Connection Pooling:** FastAPI backend (`server/api.py`) utilizes `psycopg_pool.ConnectionPool` (min 4, max 20 connections) with sub-3ms query latency across all REST endpoints.
   - **Persistence & Entity Tagging:** Articles are parsed for creative headlines (`# Headline`) and teaser excerpts (`> **EXCERPT:** ...`), then saved to `news_feed` with explicit `article_type` and `slot` identifiers alongside relational tags in `article_teams` and `article_players`.

## 7. Media Swarm Prompting, Curatorial Autonomy & Historical Anchoring
To eliminate editorial homogenization and ground long-form journalism in authentic football culture, prompts for the National Writers chain strict analytical constraints:
- **Core Model Standard:** All autonomous writer and reaction agents utilize Google's **`gemini-3.8-flash`** with autonomous ReAct database function calling (`automatic_function_calling` with a 50-call cap).
- **Mandatory Headline & Excerpt Directives:** Prompts enforce strict extraction markers:
  ```markdown
  # [Your Creative, Engaging Headline]
  > **EXCERPT:** [A sharp, 1-to-2 sentence summary / teaser for the front page]
  ```
  Both fields are parsed upon generation and stored directly in `news_feed(headline, excerpt)`.
- **Temporal Publishing Alignment (`TEMPORAL_ARTICLE_MODES`):**
  - **`RETROSPECTIVE`** (`MACRO_SLATE_RECAP`, `SINGLE_GAME_SCHEMATIC_AUTOPSY`, `LOCAL_POSTGAME_REPORT_CARD`, `PLAYOFF_AUTOPSY`): Publishes during calendar `week` evaluating the concluded action from `week - 1`.
  - **`PROSPECTIVE`** (`SLATE_LOOKAHEAD_PREDICTIONS`, `LOCAL_MATCHUP_PRIMER`, Super Bowl Previews): Previews upcoming scheduled matchups for `week`.
  - **`CUMULATIVE`** (`POWER_RANKINGS`, `AWARDS_TRACKER`, `PLAYER_DEEP_PROFILE`, `FRANCHISE_STATE_OF_THE_UNION`, etc.): Evaluates hierarchy entering `week` based on cumulative results through `week - 1`.
- **Curatorial Autonomy & Slate Digest (`newsroom/config/curatorial_autonomy_directives.json`):** Writers are fed both full scoreboards (last week's results and this week's schedule) and the division standings in the brief's `season_landscape`, rather than a narrow pre-filtered Top 5 list. Subject selection is layered: the article type's own curatorial autonomy block (`article_types.json`, or a playoff override's) comes first, and the writer's directive from `curatorial_autonomy_directives.json` is appended as "YOUR SPECIFIC WRITER SELECTION FILTER". If only one exists, it is used alone. An Editorial Desk subject assignment (§7.3) overrides both. Thirteen writers have a directive:
  - *Barnwell:* regression candidates: lucky one-score winners, poor point differentials, favorites exposed by upsets.
  - *Sando:* front-office pressure: aging, mediocre rosters (GM job security) or young blue-chip rosters (coaching development).
  - *Joyner:* schematic complexity and play-level efficiency: extreme tempo, boom-or-bust performances, tactical mismatches, or a defense that did the breaking (sacks, tackles for loss, takeaways from the box score's defensive lines and `get_defense_special_teams_leaders`; his data table charts a defense's recorded numbers, never pressure rates, snaps or targets).
  - *Rosenthal:* entertainment and vibes: high-tempo, high-excitement chaos and electric young quarterbacks.
  - *Chadiha:* the human element and historical stakes: aging stars, adversity, historic franchises under pressure.
  - *Yates:* statistical anomalies, milestones and fantasy volume that support factual lists.
  - *Simmons:* the "wonkiest" game: upsets, bad beats, historic franchises over- or under-achieving the eye test.
  - *Acho:* exposed flaws and public overreactions: popular favorites that lost, high-tempo offenses shut down (shown in the box score's defensive lines and the defenders' season numbers; his column format names those plus `get_team_efficiency_profile` as the defensive and situational evidence).
  - *The Athletic:* process, scheme and roster trajectory; young-core surges and schedule-adjusted winners; avoids garbage-time inflation.
  - *Skip Bayless:* superstar drama and the "clutch gene": prime-time chokes or game-winning drives by marquee players.
  - *Kimes:* games where efficiency tells a different story than the score, and smart defensive schemes, evidenced by the box score's defensive lines and `get_defense_special_teams_leaders` (coverage rotations and pressures are her read of them; her column format credits unheralded defenders with their recorded numbers).
  - *Orlovsky:* the most extreme quarterback performances only, good or bad.
  - *Clark:* locker-room dynamics, player sacrifice and leadership under pressure.
- **Historical Comparisons (optional, 1990–2023):** National writers may compare a team or player to a real NFL team or season from 1990–2023 when the parallel genuinely sharpens the argument. It isn't required; the prompt says most articles need none, allows at most one, forbids inventing a historical team, and gives no canned examples (the old ones, e.g. the 2000 Ravens, got reused everywhere). Beat reporters don't receive this instruction.
- **Invented detail vs. data:** Scores, records, stat lines and rankings must come from the brief or the tools. Writers may invent what happens inside games (specific plays, drives, game script) consistent with the final score and box score, and anonymous sources such as Sando's "NFC executive" quotes. Injuries, betting lines and contract details are never used: the league has no data for them, and the prompts either don't ask for them or forbid them. Madden ratings are never cited: writers may use OVR and team ratings from their tools to judge talent, but the shared `natural_language_rule` block (appended to every article and tweet prompt) forbids rating numbers, "OVR"/"overall" wording and any mention of Madden or the video game in published text.
- **Team situational rule:** the national `DEFENSIVE DATA` block and the beat research block also say season third-down, red-zone and penalty rates come from `get_team_efficiency_profile` and a game's only from its `team_situational` box-score line (none = don't invent the game's totals), that third-down defense has no season total, and that down-and-distance splits aren't recorded. The directives that ask for situational football point at those tools: the regular-season and postseason lookahead Stat to Know, the matchup primer (its Defensive Mandate may be a red-zone target against the opponent's red-zone TD rate), the schematic autopsy and its Weeks 20–22 override, the postgame report card, the season progress report, the contender blueprint, the playoff autopsy, the QB duel (its blitz-conversion line is replaced by team rates plus the QBs' interceptions and sacks taken), the Super Bowl comprehensive preview, masterclass and Tale of the Tape. `tests/test_writer_config.py` checks them.
- **Defensive data rule:** the national writers' research block (`DEFENSIVE DATA`, between the tool list and the optional historical comparisons) and the beat reporters' research block tell writers that defense and special teams have real season numbers (and game lines where captured) and to back defensive claims with named players, with three limits: a player Madden doesn't list has unknown numbers, not zeros; listed defenders never sum to team sacks or takeaways; pressures, blitzes, coverage percentages, snap counts and targets aren't recorded and are never cited (scheme talk is the writer's read of the numbers). `tests/test_writer_config.py` checks the tool is registered and the Week 19 directives point at it; `tests/test_defense_tools.py` covers the line formatting.
- **Database Context & Research Tools:** Agents have autonomous access to query live simulation context via ReAct AFC tools:
  - `get_weekly_slate`: Returns the complete scoreboard for all games in a given week with final scores, victory margins, excitement indices, and team IDs.
  - `get_season_standings`: Returns full NFL division standings with wins, losses, win %, points scored (PF), points allowed (PA), and point differential.
  - `get_top_popular_players`: Returns the top 20 most popular players across the league with Madden OVR, age, experience, and popularity indices.
  - `get_top_rated_players`: The highest-rated players by Madden OVR at every position (default 200, capped at 300; long snappers excluded), one compact line each with team, OVR, age, experience and season totals: passing/rushing/receiving, tackles/sacks/INT/forced fumbles for listed defenders, FG for kickers, net average for punters. This is the pool for league-wide player rankings such as the Top 100. Ratings run high (this season the 100th-best player is OVR 92 and 733 players are 80+), and the popularity list holds only 41 of the 108 highest-rated players, so it can't serve as the pool.
  - `get_all_team_popularities`: Returns all 32 teams ranked by popularity index, watchability rating, and overall roster quality.
  - `get_player_context` / `get_player_season_stats`: Resolves player by name or ID; returns age, years of experience, physical measurements (height/weight), career awards list, Madden OVR, cumulative season stats, stored popularity components (SpikeIndex, $R_{\text{rating}}$, $F_{\text{platform}}$, $T_{\text{success}}$, $A_{\text{awards}}$, $E_{\text{exposure}}$), player Playoff Clout, and season touch/yard share for RB/FB/WR/TE. Season stats add sacks taken for passers and, from `season_stat_lines`, `games_played` plus defense (tackles with solo/assisted, sacks, TFL, INT and return yards, pass deflections, forced fumbles, recoveries, defensive TDs, safeties), kicking (FG with long and 50+, XP), punting (average, net, inside the 20), kick/punt returns and sacks allowed, only the categories the player is listed in. Defenders, kickers and punters drop the all-zero offensive lines; a defender (or K/P) with no listed line gets a note that the numbers are unknown, not zero.
  - `get_team_context`: Resolves team by name, abbreviation, or ID; returns win-loss record, watchability, popularity, Madden team ratings, and top 3 players.
  - `get_team_season_schedule`: Returns a team's complete season schedule and game log: week-by-week opponents, scores, win/loss outcomes, victory margins, one-score game breakdown, excitement ratings, and game IDs.
  - `get_game_box_score`: Returns team box score and top individual game contributions, plus `team_situational` per team with only real values (captured, or listed in `exact_fields`; estimated backlog values are never shown): third/fourth-down and two-point conversions (with attempts when known), red-zone TDs and FGs (with trips when known), penalties or penalty yards, quarter-by-quarter scoring, first downs and time of possession (captured box scores); plus, from `game_stat_lines`, `defensive_performances` (defenders with a sack, INT, forced fumble, recovery, defensive TD, safety, 7+ tackles or 2+ TFL; biggest playmakers first) and `kicking` lines. Games the capture didn't cover get a `defense_note` telling the writer not to cite individual defensive stats.
  - `get_defense_special_teams_leaders(category, team_identifier, limit, season)`: season leaders from `season_stat_lines`, league-wide or for one team (limit capped at 50). Categories (`LEADER_CATEGORIES`): sacks, interceptions, tackles, tackles_for_loss, pass_deflections, forced_fumbles, takeaways (INT + fumble recoveries), defensive_tds, field_goals, punting (net average), kick_returns, punt_returns, sacks_allowed (fewest, linemen). Each line carries the player's full defensive or special-teams line, and the note warns that listed players don't sum to team totals and that pressures, snaps, targets and coverage aren't recorded.
  - `get_advanced_team_analytics`: Reads the stored TWI components (σ_top, P_draft, Y_prod, Purgatory Penalty), TWI, franchise POP, team Playoff Clout, starter age (top 22 by OVR), and $B$ / $L$ / $D$ / $N$ / Legacy Floor from `market_constants`.
  - `get_advanced_game_analytics`: Completed games return pace/production ($P$ from `gotw_engine.production_score`), Upset Leverage ($U$), excitement, and both teams' Opponent-Adjusted Game Credit. Scheduled games return projected $U$ (`excitement_projection.expected_upset`) and the underdog's win probability.
  - `get_opponent_adjusted_credit`: Opponent-Adjusted Game Credit (Section 2.4). No team → all 32 ranked with credit, recent-form credit, Trajectory Multiplier and label, strength of schedule, raw point differential per game, expected wins and win luck; with a team → that summary plus a game-by-game credit log.
  - `get_matchup_edges`: Section 2.8. Pass, rush and ball-security edges plus Trench Edge for both offenses in a matchup; with no opponent, uses the team's next scheduled game.
  - `get_team_efficiency_profile`: Section 2.6. Pythagorean wins and pyth luck, pace & production, pass/rush yards and giveaways per game; all 32 sorted by pyth luck, or one team. Each team also carries `situational` (`db_tools.situational_profiles`) from the `team_seasons` Team Stats totals through the latest loaded weekly capture (not cut off by the article's week): third- and fourth-down conversions and rate, two-point tries, red-zone trips (and per game), TD and scoring rate, red-zone trips and TD rate allowed, penalties and penalty yards per game, and league ranks (`third_down_rank`, `fourth_down_rank`, `red_zone_rank`, `red_zone_defense_rank`, `discipline_rank`; 1 = best). Third downs allowed appear only from games where the opponent's `team_stats` row is real (captured, or listed in `exact_fields`), with the game count; there is no season third-down defense total.
  - `get_league_leaderboards`: Returns top 5 teams by watchability and popularity.
  - `get_draft_order`: Round 1 order (Picks 1–32) by reverse record with point differential as the tiebreaker; once the Super Bowl is played, its loser picks 31 and its winner 32.
  - `get_team_draft_needs`: A team's roster needs ranked by urgency, from starter rating deficits, positional depth, age cliffs and statistical phase rankings.
  - `get_team_roster_depth_chart`: A team's full depth chart by position group (QB, RB, WR, TE, OT, IOL, EDGE, DT, LB, CB, S) with ratings, age tiers and roster demographics.
  - `get_draft_trade_value`: Jimmy Johnson trade-chart points and typical trade packages for any pick (1–260).
  - `get_franchise_rebuild_status`: A franchise's lifecycle stage (Championship Window, Youth Ascent, Roster Purgatory, Ground-Up Rebuild) and recommended draft posture.
  - `get_team_articles`: Returns recent editorial articles and analysis referencing a specific team (resolves by name, city, abbreviation, or ID).
  - `get_player_articles`: Returns recent editorial articles and profiles referencing a specific player (resolves by name or ID).
- **Fantasy research tools** (`newsroom/fantasy_tools.py`, `FANTASY_TOOLS`): given **only to `FANTASY_*` article types** (`SwarmDispatcher.tools_for(article_type)`), so other prompts don't carry their descriptions. Full PPR, RB = HB + FB. Same week rule as the other tools: a Week N article sees box scores from weeks before N only; projections (`fantasy_projections`) and simulated-league rosters (`fantasy_sim_player_weeks`) up to and including week N are visible (a later week is refused). Depth slots are OVR order within the team's position group.
  - `get_fantasy_rankings(position, week, sort, limit)`: a week's (default: the article's) projections with opponent, matchup stars, PPR/g, ROS rank and points, ADP rank, rostered/started %, and teams on bye; `sort='ros'` for rest of season. Weeks 2–18 only.
  - `get_fantasy_week_results(week, position, limit)`: a completed week's (default: latest visible) top scorers, biggest beats and biggest misses (projected 8+) vs. the pre-game projection, with stat line, positional finish and rostered %.
  - `get_fantasy_usage(team_identifier, recent_weeks, limit)`: carries, receptions and touches per game with team shares, last N games vs. the rest of the season; no team = league-wide touch risers and fallers (3+ games). Receptions stand in for targets (not tracked).
  - `get_fantasy_waiver_wire(week, position, max_rostered_pct, limit)`: players rostered in ≤ X% of simulated leagues ordered by ROS projection, with rostered % change, recent PPR and touches, plus most added / most dropped.
  - `get_fantasy_defense_vs_position(position, view)`: PPR allowed per game to each position, season and last 4 games, rank 1 = most generous; `view='playoff_schedule'` = each offense's Weeks 15–17 opponents and average opponent rank.
  - `get_fantasy_player_profile(player_identifier)`: weekly log (opponent, PPR, projection, stat line, rostered/started %), scoring range (mean, best, worst, SD, 20+ boom and sub-8 bust weeks), touches/g, ADP with range, current ROS rank.
  - `get_fantasy_draft_board(position, limit, rookies_only, strategy)`: simulated ADP board with range and draft %, OVR, age, experience, depth slot and team offense ranks; optional ADP among one strategy's managers; once games are visible, season PPR and positional finish so far.
  - `get_fantasy_mock_draft(league, rounds)`: one simulated league's draft pick by pick with each team's strategy.
  - `get_fantasy_season_leaders(position, first_week, last_week, limit)`: PPR leaders over a week range (1–17 season, 15–17 playoffs) with games, per-game, positional finish, ADP and draft %.
  - `get_fantasy_projection_accuracy()`: projection MAE, correlation and guess-the-average MAE for projected starters (weekly top 12 QB/TE, 30 RB/WR), by position, plus the biggest over- and under-projections.
- **Season & week scope of the research tools (`newsroom/run_context.py`):** `_execute_agent` sets the article's season and publication week for its thread before calling Gemini (automatic function calling runs the tools on that thread) and clears it afterwards.
  - **Season:** tools take `season: int = 0`, meaning "current": the article's season inside a run, otherwise `league_state.current_season`, otherwise the latest season in `games` (`db_tools.resolve_season`). No tool hardcodes a season.
  - **Week cutoff:** a Week N article can't see results from Week N onward. `get_game_box_score` refuses those games; `get_advanced_game_analytics`, `get_team_season_schedule` and `get_weekly_slate` show them as scheduled with no score; `get_season_standings` counts the record and points from earlier games only; `get_opponent_adjusted_credit`, `get_team_efficiency_profile`, `get_matchup_edges` and the credit inside game analytics use earlier games only (`fetch_team_game_rows(..., before_week=)`); `get_team_articles` / `get_player_articles` return only articles from earlier weeks, which also hides the article's own week. Earlier seasons are fully visible.
  - **Not cut off:** stored season totals with no per-week history (`season_stats`, `team_seasons` records and indices, player popularity components, ratings, rosters, draft tools). In normal forward runs these already stop at the previous week; when an old week is regenerated they reflect the latest data. `tests/test_run_context.py` covers the context.
- **Prohibited Internal Symbols:** Strict prompt directive banning internal mathematical symbols (`$P$`, `Y_prod`, `(88.17 W)`, `(64.84 POP)`) in favor of natural sports journalism prose.

### 7.1 Article Taxonomy & Writer Archetype Alignments (`newsroom/config/article_types.json`)
`article_types.json` defines 31 article types: the 27 national types below and the 4 local beat types in §7.2. Each maps to its allowed writer archetypes and a writing category. A type either carries its own structure directive or, where noted, uses the writer's column format from `article_structure_directives.json`:

1. **The Macro Slate Recap** (`MACRO_SLATE_RECAP`)
   - **Allowed Writers / Archetypes:** Barnwell, Rosenthal (`BARNWELL`, `ROSENTHAL`, `Weekly Recaps`).
   - **Granularity:** Week-level.
   - **Focus:** Cross-league trends, regression candidates, aesthetic watchability power rankings.
   - **Data Queried:** $E_{\text{game}}$, $P_{\text{pace}}$, league-wide point differentials, Pythagorean win expectations.
   - **Structure Source:** Writer format (Barnwell's column or Rosenthal's Weekend Notebook; `article_structure_directives.json`).

2. **The Player Deep Profile** (`PLAYER_DEEP_PROFILE`)
   - **Allowed Writers / Archetypes:** Jeffri Chadiha (Narrative/Legacy), KC Joyner (Scientific Efficiency), Gregg Rosenthal (Aesthetic/Vibes) (`CHADIHA`, `JOYNER`, `ROSENTHAL`, `Player Season Overview`).
   - **Granularity:** In-Season Trajectory / Career Arc.
   - **Focus:** Prestige, longform player profile evaluating their season-to-date trajectory, breakout status, MVP credentials, or legacy implications.
   - **Data Queried:** Player Popularity Index ($Pop_{\text{player}}$), Awards Equity ($A_{\text{awards}}$), Playoff Clout ($S_{\text{playoff}}$), SpikeIndex, Franchise Platform ($F_{\text{platform}}$).
   - **Curatorial Autonomy:** Evaluates season-to-date leaderboards, cumulative stats, and popularity to select one player whose current campaign defines the narrative of the league—an aging legend defending legacy, an MVP frontrunner with massive cultural footprint, or a volatile breakout star, regardless of whether it is Week 3 or Week 17.
   - **Structure Directive:** 4-part profile (`# 1. The Defining Moment / The Hook`, `## 2. The Anatomy of Their Season`, `## 3. The Cultural Footprint & Legacy`, `### 4. The Horizon`).
   - **Prompt Hook:** Select a marquee player whose campaign currently defines the season. Deliver a definitive longform profile on their offensive engine, their cultural footprint, and their career horizon based on their season-to-date tape.

3. **The Franchise State of the Union** (`FRANCHISE_STATE_OF_THE_UNION`)
   - **Allowed Writers / Archetypes:** Sando (`SANDO`, `Front-Office Columnists`).
   - **Granularity:** 3-to-5 year franchise trajectory.
   - **Focus:** General Manager job security, roster demographic distributions, draft hit rates, rebuilding vs. purgatory.
   - **Data Queried:** $P_{\text{draft}}$, $Y_{\text{prod}}$, Purgatory Penalty, Base Market Gravity ($B$), Roster Age.
   - **Curatorial Autonomy:** Evaluates multi-year franchise data, demographic distributions, and standing trends. Identifies a franchise at an existential fork in the road: aging roster in purgatory, high-gravity giant collapsing, or young rebuilding core on the verge of contention.
   - **Structure Source:** Writer format (Mike Sando's Pick Six with anonymous executive quotes).
   - **Prompt Hook:** Analyze a franchise in transition. Ignore the single-game box score. Evaluate their front office's 3-to-5 year trajectory using the Purgatory Penalty, aging roster constraints, and draft equity.

4. **The Single-Game Schematic Autopsy** (`SINGLE_GAME_SCHEMATIC_AUTOPSY`)
   - **Allowed Writers / Archetypes:** KC Joyner (Scientific Efficiency), Mina Kimes (Metric-Driven Schematic Breakdown), Dan Orlovsky (QB Film Room / Telestrator) (`JOYNER`, `KIMES`, `ORLOVSKY`, `Film Analysts`).
   - **Granularity:** Single-game play-by-play.
   - **Focus:** Point-of-attack wins, run-pass splits, specific receiver coverage isolations, quarterback processing mechanics, and the defensive playmakers who swung the game.
   - **Data Queried:** Advanced Game Analytics (Offensive Pace, Single-Game Excitement), Game Box Scores, Touch Share & Scrimmage Yard Share (season), game defensive lines and kickers where captured.
   - **Curatorial Autonomy:** the most extreme tactical anomaly on the slate; a game decided by defense qualifies only if its box score has defensive lines, and scheme is the writer's read of the box score (no pressure, blitz or coverage rates, snaps or targets).
   - **Structure Source:** Writer format (Joyner's tape-charting breakdown, Kimes's analytical breakdown or Orlovsky's film-room telestrator; `article_structure_directives.json`).

5. **The Power Rankings** (`POWER_RANKINGS`)
   - **Allowed Writers / Archetypes:** Mike Sando (Executive Tiers & Front-Office Context), Gregg Rosenthal (Vibe & Watchability Rankings) (`SANDO`, `ROSENTHAL`, `32-Team Thematic Consensus`).
   - **Granularity:** Week-level / Full League 1-32 Hierarchy.
   - **Focus:** Comprehensive publication-ready 32-team Power Ranking evaluating every team 1-32 through an explicit weekly editorial theme, underlying metrics, and movement indicators.
   - **Data Queried:** Opponent-Adjusted Game Credit, Team Watchability Index ($W$), Trajectory Multiplier ($T$), Purgatory Penalty, Base Market Gravity ($B$).
   - **Curatorial Autonomy:** Evaluates full 32-team standings and metrics to separate paper tigers from true contenders using Opponent-Adjusted Game Credit, rewards trajectory surging teams, penalizes middle-tier stagnators, and establishes a unifying Weekly Editorial Theme.
   - **Structure Directive:** 4-part 32-Team Power Ranking (`# 1. The Macro Overview & Weekly Theme`, `## 2. The 32-Team Enumeration 1 to 32`, one header per team formatted `## [Rank]. [City] [Team] ([W-L]) (Previous: N | Change: ±N)`, the same format as the continuity block in §7.6, `3. The Thematic Blurbs with bold kickers`, `### 4. The Walk-Off / The Drop-Off`).
   - **Prompt Hook:** Rank all 32 NFL franchises from 1 to 32 under an explicit weekly editorial theme. Separate paper tigers from true contenders using opponent-adjusted credit and trajectory, provide a 3-to-4 sentence analytical blurb for every team with movement indicators, and diagnose the widening gap between tiers.

6. **The Awards Tracker** (`AWARDS_TRACKER`)
   - **Allowed Writers / Archetypes:** Field Yates (Objective/Factual), Bill Simmons (Narrative/Historical Debate) (`YATES`, `SIMMONS`, `The Hardware Race`).
   - **Granularity:** Season-level / Hardware Campaign.
   - **Focus:** Active MVP, OPOY, and OROY award races, evaluating true contenders vs. empty-calorie stat accumulators, narrative momentum, and rookie learning curves.
   - **Data Queried:** Awards Equity ($A_{\text{awards}}$), Player Popularity ($Pop_{\text{player}}$), Team Success ($T_{\text{success}}$), SpikeIndex.
   - **Curatorial Autonomy:** Aggressively applies the "Empty Calories" Suppressor on sub-.300 teams, pinpointing the intersection of elite explosive production ($SpikeIndex$) and contender team success ($T_{\text{success}}$).
   - **Structure Directive:** 3-part Awards Watch (The MVP Frontrunner / The Heavy Crown `#` / `##`, The Contenders / The Chasers `##`, The Offensive Rookie Race `##`).
   - **Postseason:** Week 21 runs the `AWARDS_FINALISTS` override and Week 22 `AWARDS_WINNERS` (§7.4).
   - **Prompt Hook:** Analyze the active MVP, OPOY and OROY races across the league. Apply the empty-calories suppressor to eliminate meaningless stats, examine the frontrunner vs. the chasers, and break down the rookie race.

7. **The Weekly Slate Lookahead** (`SLATE_LOOKAHEAD_PREDICTIONS`)
   - **Allowed Writers / Archetypes:** Bill Barnwell (Efficiency & Statistical Modeling), Emmanuel Acho (Contrarian Matchups & Narratives), Gregg Rosenthal (Aesthetic Matchups & Watchability) (`BARNWELL`, `ACHO`, `ROSENTHAL`, `ESPN Multi-Angle Matchup Preview`).
   - **Granularity:** Week-ahead / Matchup Preview Guide.
   - **Focus:** Comprehensive, modular weekly preview guide modeled after ESPN's multi-perspective game capsules covering team context, key stats, watchability, tactical predictions, fantasy spotlights, and projected final scores.
   - **Data Queried:** Upcoming Matchup Schedule, Single-Game Excitement Index ($E$), Offensive Pace ($P$), Upset Leverage ($U_{\text{projected}}$), Team Watchability ($W$), SpikeIndex, Base Market Gravity ($B$).
   - **Curatorial Autonomy:** Evaluates the full upcoming schedule across high-tempo clashes ($P$, $W$), upset leverage shockers ($U$), and skill-player mismatch volatility (SpikeIndex) using an integrated multi-analyst format.
   - **Structure Directive:** 4-part Modular Lookahead (`# 1. The Slate Overview & Complete Matchup Jump List`, `2. The Full Slate Enumeration for every single game on schedule`, `## 3. Modular Matchup Capsules with Team Context, Stat to Know, Why Watch, Tactical Angle, Fantasy Spotlight, and Official Projected Final Score`, `4. Data Integration Rule`).
   - **Prompt Hook:** Preview the upcoming weekend slate using ESPN-style modular game capsules. For each featured matchup, break down locker-room buzz, a key statistical split, why fans should tune in, a bold tactical forecast, fantasy spike spotlight, and an official final score prediction.

8. **The NFL Top 100 & Positional Value Hierarchy** (`NFL_TOP_100_HIERARCHY`)
    - **Allowed Writers / Archetypes:** The Athletic Staff (Consensus Roster Building), Field Yates (Factual List-Maker) (`THE_ATHLETIC`, `YATES`, `The Preseason / Offseason Hierarchy`).
    - **Granularity:** Preseason / League-Wide Player Hierarchy.
    - **Focus:** Prestige top 100 player countdown from No. 100 to No. 1: grouped tiers for 100-11, an individual top 10 countdown, positional value and legacy vs. projection.
    - **Data Queried:** Baseline Rating ($R_{\text{rating}}$), Positional Multiplier ($M_{\text{pos}}$), Career Awards Equity ($A_{\text{career}}$), Explosive Ceiling ($SpikeIndex$).
    - **Curatorial Autonomy:** Builds the pool with `get_top_rated_players` (top 200 by OVR, every position, with stats; `get_player_season_stats` only for close calls) and ranks on baseline talent, production and positional value, not popularity, including linemen and defenders. Weighs aging veterans with championship pedigree against young players with elite traits. Every player must be a real player in the league database, listed once with his current team and position.
    - **Structure Directive:** Countdown from No. 100 to No. 1, modelled on ESPN's NFL Rank and NFL Network's player-voted Top 100, which both count down and end on No. 1 with no snubs section (`# headline` + 2-paragraph methodology with team and position counts; 3–4 `##` tiers in countdown order, e.g. `## The Elite Specialists (100-61)`, each with 1–2 paragraphs of analysis and a numbered `**[Rank]. [Player]**, [Pos], [City] [Team]` line per player from highest number to lowest, no individual blurbs; `## The Top 10` with one `### [Rank]. [Player], [Pos], [City] [Team]` per player from 10 to 1, 3–4 sentences each; the article ends on No. 1 with no snubs, honorable mentions or wrap-up). Exactly 100 different players, no gaps or repeats.
    - **Prompt Hook:** Count down the top 100 players in the league from No. 100 to No. 1. Explain your methodology, group Nos. 100-11 into named tiers listed in countdown order, break down the top 10 individually from No. 10 to No. 1, and end on No. 1.

9. **The Playoff Predictor** (`PLAYOFF_PREDICTOR`)
    - **Allowed Writers / Archetypes:** Gregg Rosenthal (Chaos/Wild Card brackets), Bill Barnwell (Efficiency-based pathing) (`ROSENTHAL`, `BARNWELL`, `Bracketology & Postseason Tiers`).
    - **Granularity:** Late-Season / Playoff Race.
    - **Focus:** Projected postseason bracket mapping, identifying fraudulent division leaders ripe for first-round upsets, and dangerous Wild Card gatecrashers.
    - **Data Queried:** Trajectory Multiplier ($T$), Watchability Volatility ($\sigma_{\text{top}}$), Opponent-Adjusted Game Credit, Base Market Gravity ($B$).
    - **Curatorial Autonomy:** Maps the projected postseason bracket; exposes fraudulent division leaders with inflated records ripe for first-round upsets; highlights terrifying surging Wild Card teams.
    - **Structure Directive:** 4-part Playoff Picture (`# 1. The Postseason Landscape`, `## 2. The Bracket Locks`, `## 3. The Dangerous Gatecrashers`, `### 4. The First-Round Exit Warning`).
    - **Prompt Hook:** Map out the projected postseason bracket. Expose a fraudulent division leader on upset alert, identify the terrifying Wild Card gatecrashers, and establish the true conference heavyweights.

10. **The Super Bowl Contender Blueprint** (`SUPER_BOWL_CONTENDER_BLUEPRINT`)
    - **Allowed Writers / Archetypes:** Mike Sando (Front-Office Windows), KC Joyner (Statistical Profiling), Jeffri Chadiha (Legacy & Clout) (`SANDO`, `JOYNER`, `CHADIHA`, `The Championship Litmus Test`).
    - **Granularity:** Championship / Postseason Arc.
    - **Focus:** Championship contender autopsy evaluating whether top-tier teams possess the statistical profile, quarterback clout, and roster balance to win three straight playoff games.
    - **Data Queried:** Playoff Clout ($S_{\text{playoff}}$), Awards Equity ($A_{\text{awards}}$), Legacy Floor ($S_{\text{all-time}}$), Offensive Pace ($P$), Youth Production Share ($Y_{\text{prod}}$).
    - **Curatorial Autonomy:** Applies a rigid championship litmus test to the top tier; separates regular-season 11-win paper tigers from true Lombardi contenders with playoff clout, pedigree, and scheme balance.
    - **Structure Directive:** 4-part Championship Autopsy (`# 1. The Championship Standard`, `## 2. The Blue-Chip Heavyweights`, `## 3. The Paper Tigers`, `### 4. The Missing Piece`).
    - **Prompt Hook:** Apply the championship litmus test to the league's top tier. Crown the inner-circle heavyweights, dissect an 11-win paper tiger with a fatal playoff flaw, and identify the contender that is one piece away.
    - **Postseason:** Week 20 runs the `DIVISIONAL_ROUND_CONTENDER_AUDIT` override and Week 21 `FINAL_FOUR_CONTENDER_AUDIT` (§7.4). The base version above runs in the regular season (Phase 4).

11. **The Season Progress Audit** (`SEASON_PROGRESS_AUDIT`)
    - **Allowed Writers / Archetypes:** Bill Barnwell (Macro Regression & Sustainability), Gregg Rosenthal (Cumulative Vibes & Entertainment Hierarchy), Mike Sando (Executive Temperature Check) (`BARNWELL`, `ROSENTHAL`, `SANDO`, `League-Wide Season-to-Date Review`).
    - **Granularity:** Flexible Cumulative / Season-to-Date.
    - **Focus:** Macro regression & sustainability, cumulative vibes & entertainment hierarchy, executive temperature checks, real vs fugazi contenders, youth core engines.
    - **Data Queried:** Cumulative Opponent-Adjusted Game Credit, Team Watchability Index ($W$), Trajectory Multipliers ($T$), Under-25 Production Share ($Y_{\text{prod}}$), Purgatory Penalty, Base Market Gravity ($B$).
    - **Curatorial Autonomy:** Evaluates cumulative performance from Week 1 through the current week without isolating single Sundays. Curates 3 to 4 systemic storylines across the season to date: Record Mirages (inflated win totals), Structural Engines (underlying per-play dominance), and Identity Shifts.
    - **Structure Directive:** 5-part Progress Assessment (`# 1. The Macro State of Play (The Season to Date)`, `## 2. Real or Mirage? The Contender Litmus Test`, `## 3. The Under-the-Hood Ascenders`, `## 4. The Panic Index`, `### 5. The Next Four Weeks`).
    - **Prompt Hook:** Curate the defining macro storylines of the season through the current week. Contrast a legitimate contender against a fugazi winner, spotlight the youth cores driving underlying surges, and audit veteran rosters sliding into purgatory.

12. **The Player Progress Report** (`PLAYER_PROGRESS_REPORT`)
    - **Allowed Writers / Archetypes:** Bill Barnwell (Efficiency & Regression), Jeffri Chadiha (Narrative & Legacy), Field Yates (Fantasy Volume & Milestones) (`BARNWELL`, `CHADIHA`, `YATES`, `Player Progress Overview`).
    - **Granularity:** Flexible In-Season / Season-to-Date Arc.
    - **Focus:** Mid-campaign player valuation shifts, usage and snap trajectory, underlying efficiency vs. box-score luck, and rest-of-season sustainability.
    - **Data Queried:** Player Popularity Index ($Pop_{\text{player}}$), Awards Equity ($A_{\text{awards}}$), Baseline Rating ($R_{\text{rating}}$), SpikeIndex, Touch Share & Scrimmage Yard Share (season), Offensive Pace ($P$), defense & special-teams lines.
    - **Defense:** Defenders and specialists are eligible: the curatorial block points at `get_defense_special_teams_leaders` and `get_player_season_stats`, with the unknown-not-zero, no-team-sums and no pressure/snap/target rules (JR, Oct 14). A defender whose sacks, tackles for loss or takeaways are rewriting his standing qualifies, and the efficiency section covers how a defender wins.
    - **Curatorial Autonomy:** Evaluates the cumulative season-to-date trajectory of a single player. Identifies players whose performance is forcing an immediate recalibration: aging veterans with slipping roles, rising talents drastically outpacing baseline ratings, or high-volume contributors due for regression.
    - **Structure Directive:** 4-part Player Progress Evaluation (`# 1. The Progress Thesis (The Hook)`, `## 2. The Tape vs. The Numbers (Underlying Efficiency)`, `## 3. Roster Gravity & Market Footprint`, `### 4. The Rest-of-Season Outlook`).
    - **Prompt Hook:** Evaluate a player whose season-to-date campaign has radically shifted their trajectory. Contrast their preseason baseline against operational efficiency, diagnose their offensive burden, and project their rest-of-season sustainability.

13. **The Pre-Season Roster & Tier Grades** (`PRE_SEASON_ROSTER_TIERS`)
    - **Allowed Writers / Archetypes:** Mike Sando (Executive Tiers & Roster Baselines), Bill Barnwell (Structural Efficiency), Gregg Rosenthal (Vibes & Watchability Tiers) (`SANDO`, `BARNWELL`, `ROSENTHAL`, `Preseason Tiers & Roster Evaluation`).
    - **Granularity:** Preseason / 32-Team Overview.
    - **Focus:** Grouping all 32 NFL franchises into 5 to 6 distinct structural tiers based on baseline roster talent, blue-chip pedigree, purgatory status, and championship equity.
    - **Data Queried:** Baseline Roster Talent, Blue-Chip Pedigree ($P_{\text{draft}}$), Under-25 Production ($Y_{\text{prod}}$), Purgatory Penalty, Base Market Gravity ($B$).
    - **Curatorial Autonomy:** Evaluates preseason baseline metrics, demographics, and draft pedigrees across all 32 teams. Categorizes the entire league into 5–6 distinct unified tiers without omitting any team.
    - **Structure Directive:** 4-part 32-Team Tier Column (`# 1. The Preseason Thesis (The Hook)`, `## 2. The Tier Categories (Strict 32-Team Rule)`, `3. The Team Blurbs with 2-3 sentence justifications`, `### 4. The Basement (The Drop-Off)`).
    - **Prompt Hook:** Group all 32 NFL franchises into 5 to 6 structured preseason tiers based on baseline roster talent and draft pedigree. Define the criteria for each tier and provide a tight analytical justification for every team's placement, ensuring no team is left out.

14. **The Positional Tiers Breakdown** (`POSITIONAL_PLAYER_TIERS`)
    - **Allowed Writers / Archetypes:** Mike Sando (Executive/Scout Consensus), Field Yates (Factual & Fantasy Volume), Mina Kimes (Schematic Efficiency) (`SANDO`, `YATES`, `KIMES`, `Positional Rankings`).
    - **Granularity:** Midseason / Position Group Audit.
    - **Focus:** Grouping all starting players at a specific premium position into strictly defined analytical and structural tiers.
    - **Data Queried:** Baseline Rating ($R_{\text{rating}}$), SpikeIndex, Opponent-Adjusted Game Credit, Under-25 Production ($Y_{\text{prod}}$).
    - **Curatorial Autonomy:** Evaluates the entire league-wide landscape of a single position (e.g., all 32 starting Quarterbacks or top 40 Wide Receivers). Groups players into 4–5 distinct thematic tiers based on underlying metrics, tape, and current trajectory, differentiating high-variance gunslingers from system elevators.
    - **Structure Directive:** 3-part Positional Tier Column (`# 1. The Positional Thesis (The Hook)`, `## 2. The Tier Breakdowns with defining traits and bolded player blurbs`, `3. The Replacement Level covering veterans holding offenses back`).
    - **Prompt Hook:** Evaluate a single premium position across the NFL. Group the league's starters into 4 to 5 thematic tiers based on their underlying efficiency and explosive ceiling, providing a tight justification for each player's placement.

15. **The Positional Top 10 Hierarchy** (`POSITIONAL_TOP_10_HIERARCHY`)
    - **Allowed Writers / Archetypes:** Bill Barnwell (Efficiency & Regression), Jeffri Chadiha (Legacy & Narrative), Skip Bayless (Clutch Gene & Superstars) (`BARNWELL`, `CHADIHA`, `SKIP_BAYLESS`, `Positional Rankings`).
    - **Granularity:** In-Season / Elite Positional Audit.
    - **Focus:** A rigid, prestige 1-through-10 ranking of the absolute best players at a single premium position right now, debating legacy vs. current film.
    - **Data Queried:** Awards Equity ($A_{\text{awards}}$), Explosive Ceiling ($SpikeIndex$), Playoff Clout ($S_{\text{playoff}}$), Player Popularity ($Pop_{\text{player}}$).
    - **Curatorial Autonomy:** Ranks the absolute top 10 players at one specific premium position (e.g., Edge Rushers, Cornerbacks, Left Tackles). Weighs past hardware and playoff clout against explosive metrics ($SpikeIndex$), strictly debating the elite between aging legends and young phenoms.
    - **Structure Directive:** 3-part Positional Countdown (`# 1. The Positional Landscape (The Hook)`, `## 2. The Top 10 Countdown with numbered player headers and tight justifications`, `### 3. The Controversial Snub examining high-profile omissions`).
    - **Prompt Hook:** Rank the absolute top 10 players at a premium position right now. Balance past hardware against current explosive tape, provide a numbered countdown, and justify the most controversial snub left outside the top 10.

16. **The National Opinion Column** (`NATIONAL_OPINION_COLUMN`)
    - **Allowed Writers / Archetypes:** Bill Simmons (Historical / Pop Culture / Narrative), Emmanuel Acho (Uncomfortable Truths / Contrarian), Field Yates (Lists / Milestones / Volume), Skip Bayless (Clutch Gene / Fourth Quarter Drama), Mina Kimes (Metric-Driven Schematic Perspective) (`SIMMONS`, `ACHO`, `YATES`, `SKIP_BAYLESS`, `KIMES`, `National Perspective`).
    - **Granularity:** Week-level / Macro Narrative.
    - **Focus:** Singular viewpoint editorial focusing on league narratives, historical debates, contrarian reality checks, milestone valuations, or fourth-quarter clutch execution.
    - **Data Queried:** Player Popularity Index ($Pop_{\text{player}}$), Awards Equity ($A_{\text{awards}}$), Base Market Gravity ($B$), SpikeIndex, Offensive Pace ($P$).
    - **Curatorial Autonomy:** Isolates a single high-profile topic carrying an outsized national cultural footprint ($Pop_{\text{player}}$) or massive market gravity ($B$). Filters topic through writer archetype: historical parallels/wonky trends (Simmons), contrarian counter-arguments (Acho), milestone anomalies/share surges (Yates), or clutch execution/choke narratives (Bayless).
    - **Structure Directive:** None on the article type; each columnist's format comes from `newsroom/config/article_structure_directives.json`:
      - *Simmons:* cold open built on one comparison → `## The Case` → `## The Levels` (4–6 `###` tiers) → `### The Bad Beat` (one game from the recap week) → walk-off prediction with a stated confidence level.
      - *Acho:* "Uncomfortable Truths": direct address naming the consensus → `## What Everyone Believes` (stated fairly) → three numbered `##` truths → `## What the Public Is Missing` → `### The Verdict` (one bolded call).
      - *Yates:* one-paragraph setup → `## The Leaderboard` (markdown table from tool results) → `## Pace & Milestones` (5–8 factual bullets with paces computed from games played) → `## Three Things to Know` → one-line close ("That's the list.").
      - *Skip Bayless:* one-sentence take → `## The Indictment`/`The Coronation` (one-score games, late-season games vs. winning teams, playoff history, awards) → `### Exhibit A–C` (one game each, from the subject's schedule) → `## Don't Tell Me About the Numbers` → `### The Final Word`.
      - *Kimes:* pop-culture thesis → `## The Metric Disconnect` → `## The Schematic Chess Match` → `### The Walk-Off`.
      - The four new formats (Simmons, Acho, Yates, Bayless) end with a grounding rule: every number must come from the brief or tool results; plays, in-game moments and quotes may be invented if they fit the final score and box score; no injuries, betting lines, contract details or broadcast details. `tests/test_writer_config.py` fails if any writer allowed on a type without its own structure lacks a column format.
    - **Prompt Hook:** Isolate the defining cultural narrative or superstar controversy of the week. Challenge mainstream consensus, run a historical parallel, or interrogate fourth-quarter execution.

17. **The Playoff Autopsy** (`PLAYOFF_AUTOPSY`)
    - **Allowed Writers / Archetypes:** Mike Sando (Front-Office Windows & Executive Accountability), Bill Barnwell (Tactical Autopsy & Regression/Sustainability) (`SANDO`, `BARNWELL`, `Postseason Exit Diagnostics`).
    - **Granularity:** Postseason Exit / Offseason Horizon.
    - **Focus:** Forensic breakdown of a postseason elimination, identifying the schematic failure points that got exposed, evaluating championship window sustainability, and defining the immediate draft and roster mandate.
    - **Data Queried:** Opponent-Adjusted Game Credit, Under-25 Production ($Y_{\text{prod}}$), Blue-Chip Pedigree ($P_{\text{draft}}$), Purgatory Penalty, Base Market Gravity ($B$), Legacy Floor ($S_{\text{all-time}}$), Offensive Pace ($P$).
    - **Curatorial Autonomy:** Evaluates recent playoff elimination results and multi-year franchise analytics. Selects an eliminated team representing a significant crossroads: aging heavyweight with slamming window, high-market-gravity giant facing intense scrutiny, or ascending young core learning from defeat.
    - **Structure Directive:** 5-part Postseason Autopsy (`# 1. The Scene of the Crash (The Exit)`, `## 2. The Schematic Fracture (What Broke on Film)`, `## 3. The Window Audit (The Championship Window Litmus Test)`, `## 4. Front Office & Coaching Seat Temperature`, `### 5. The Spring Mandate`).
    - **Prompt Hook:** Conduct a definitive playoff autopsy on an eliminated contender. Pinpoint where the game plan fractured, evaluate whether their championship window is closing or just opening, and establish the front office's primary offseason directive.

18. **The X-Factor Volatility Spotlight** (`X_FACTOR_VOLATILITY_SPOTLIGHT`)
    - **Allowed Writers / Archetypes:** Bill Barnwell (Variance & Efficiency Analytics), KC Joyner (Matchup Exploitation), Field Yates (Volume Spikes & Fantasy/Prop Context) (`BARNWELL`, `JOYNER`, `YATES`, `Super Bowl Matchup Spotlight`).
    - **Granularity:** Single-Game Predictive / Super Bowl Arc.
    - **Focus:** Identifying the highly volatile, non-quarterback skill players whose Boom-or-Bust profiles (SpikeIndex) could single-handedly wreck the Super Bowl game script.
    - **Data Queried:** SpikeIndex, Touch Share & Scrimmage Yard Share (season), Matchup Edge (pass, rush, ball security), Offensive Pace ($P$), Under-25 Production ($Y_{\text{prod}}$).
    - **Curatorial Autonomy:** Selects the ultimate non-QB X-factor for the Super Bowl matchup: boom-or-bust deep threat with high $SpikeIndex$, a pass rusher or ball hawk whose sack, tackle-for-loss, interception or forced-fumble totals make a game-wrecking play likely (the untracked "pressure rate" is gone), a returner or kicker who can flip the field, or dynamic rookie with spiked postseason usage. Defenders and specialists are eligible: the curatorial block points at `get_defense_special_teams_leaders` and `get_player_season_stats`, with the unknown-not-zero, no-team-sums and no pressure/snap/target rules (JR, Oct 14). The volatility section cites recorded numbers (no targets, routes or snap counts).
    - **Structure Directive:** 4-part Predictive X-Factor Breakdown (`# 1. The Game-Breaker Thesis (The Hook)`, `## 2. The Anatomy of the Spike (The Volatility Audit)`, `## 3. The Schematic Mismatch (The Film Room)`, `### 4. The Script Prediction`).
    - **Prompt Hook:** Identify the ultimate Super Bowl X-Factor. Analyze a volatile skill player with a massive SpikeIndex, break down the specific schematic mismatch they can exploit, and predict how they could break the game script.

19. **The Official AP All-Pro Roster** (`OFFICIAL_AP_ALL_PRO_ROSTER`)
    - **Allowed Writers / Archetypes:** Field Yates (Definitive Roster & Stats), Bill Barnwell (Efficiency & Positional Value) (`YATES`, `BARNWELL`, `Year-End Hardware`).
    - **Granularity:** Season-Level / Year-End Roster.
    - **Focus:** The definitive First and Second-Team AP All-Pro selections, statistical justifications, positional dominance, and voting anomalies.
    - **Data Queried:** Awards Equity ($A_{\text{awards}}$), Baseline Rating ($R_{\text{rating}}$), SpikeIndex, Player Popularity Index ($Pop_{\text{player}}$), Empty Calories Suppressor, defense & special-teams season leaders, player season stats.
    - **Curatorial Autonomy:** Constructs both the complete First Team and Second Team. Weighs full-season efficiency, raw volume, and high-leverage execution. Aggressively applies the "Empty Calories" suppressor to eliminate players racking up stats on sub-.300 teams; focuses on milestone volume including sacks, interceptions and field goals (Yates) or down-to-down efficiency and which defensive totals overstate or understate impact (Barnwell). A "Defense & Special Teams Evidence" block maps each defensive and special-teams slot to its `get_defense_special_teams_leaders` categories, says an unlisted defender may be picked on talent but gets no stat line, and has the linemen, fullback, special teamer and long snapper justified without numbers.
    - **Structure Directive:** 5-part Roster Reveal, approx. 1,400–1,800 words (`# 1. The Voting Overview (The Hook)`, `## 2. The First-Team Offense (11 slots with skill justifications; linemen may carry sacks allowed)`, `## 3. The First-Team Defense & Special Teams (11 defense + 6 special teams slots; a 1-sentence stat justification for each listed defender and specialist, a role note for the special teamer and long snapper)`, `## 4. The Second-Team Offense`, `## 5. The Second-Team Defense & Special Teams`).
    - **Prompt Hook:** Reveal the official AP All-Pro First and Second Teams. Construct both complete 29-player units across Offense, Defense, and Special Teams, provide statistical justifications for the skill players, defenders and specialists, and evaluate positional dominance without speculative snub commentary.

20. **The Super Bowl Comprehensive Game Preview** (`SUPER_BOWL_COMPREHENSIVE_PREVIEW`)
    - **Allowed Writers / Archetypes:** Bill Barnwell (Macro Analytics & Efficiency Models), Gregg Rosenthal (Matchup Aesthetics & Game Flow), Jeffri Chadiha (Championship Clout & Legacy Stakes) (`BARNWELL`, `ROSENTHAL`, `CHADIHA`, `Championship Mega-Preview`).
    - **Granularity:** Single-Game Pinnacle / Full-Season Culmination.
    - **Focus:** Definitive multi-angle Super Bowl preview covering tactical schemes, efficiency baselines, line-of-scrimmage trench warfare, quarterback legacy stakes, and final score projections.
    - **Data Queried:** Opponent-Adjusted Game Credit, Single-Game Excitement Index ($E$), Offensive Pace ($P$), Playoff Clout ($S_{\text{playoff}}$), Awards Equity ($A_{\text{awards}}$), Legacy Floor ($S_{\text{all-time}}$), Base Market Gravity ($B$), SpikeIndex, Youth Production Share ($Y_{\text{prod}}$).
    - **Curatorial Autonomy:** Synthesizes the entire season of data and film between the two conference champions. Contrasts offensive pace ($P$) against opponent-adjusted efficiency, tests explosive passing ($SpikeIndex$) against disciplined pass rush, and weighs quarterback playoff clout ($S_{\text{playoff}}$) under maximum pressure.
    - **Structure Directive:** 6-part Championship Mega-Preview (`# 1. The Grand Stage (The Overture)`, `## 2. The Quarterback Legacy & The Stakes`, `## 3. Trench Warfare: The Line of Scrimmage`, `## 4. The Chess Match: Coordinators and Scheme Exploitation`, `## 5. The Volatility Factor (The X-Factor Unit)`, `## 6. The Game Script & Official Prediction (Projected Final)`).
    - **Prompt Hook:** Deliver the definitive Super Bowl preview. Break down the clash of offensive pace against defensive efficiency, evaluate the quarterback legacy stakes, diagnose the trench battles, and issue an official final score prediction.

21. **The Market Gravity & Legacy Floor Audit** (`MARKET_GRAVITY_LEGACY_AUDIT`)
    - **Allowed Writers / Archetypes:** Bill Simmons (Historical / Pop Culture / Narrative), Jeffri Chadiha (Championship Clout & Legacy Stakes) (`SIMMONS`, `CHADIHA`, `Championship Legacy Audit`).
    - **Granularity:** Franchise Historical / Macro Narrative.
    - **Focus:** Macro-level column evaluating what a Super Bowl championship would mean for the historical prestige, legacy floor, and market gravity of the two participating franchises.
    - **Data Queried:** Base Market Gravity ($B$), Legacy Floor ($S_{\text{all-time}}$), Historical Franchise Prestige, National Reach ($N$; published for 8 franchises).
    - **Curatorial Autonomy:** Evaluates the two Super Bowl finalists through a strictly historical and organizational lens without detailing on-field positional matchups. Weighs dynasty solidification vs. market darling elevation, evaluating whether a Lombardi permanently lifts Base Market Gravity ($B$) or protects a historic Legacy Floor ($S_{\text{all-time}}$).
    - **Structure Directive:** 4-part Historical Prestige Column (`# 1. The Historical Weight (The Hook)`, `## 2. The Legacy Floor Analysis (Team A - AFC Champion)`, `## 3. The Market Gravity Analysis (Team B - NFC Champion)`, `### 4. The Existential Verdict`).
    - **Prompt Hook:** Evaluate the historical stakes of the Super Bowl matchup. Analyze what a Lombardi trophy would do for the Legacy Floor of the winning franchise and how it would shift their Base Market Gravity.

22. **The Bold Predictions & Panel Consensus** (`SUPER_BOWL_ROUNDTABLE_CONSENSUS`)
    - **Allowed Writers / Archetypes:** The Super Bowl Roundtable panel only (`ROUNDTABLE_PANEL`, `The Super Bowl Roundtable`): one AI call writing as a moderator who voices Kimes, Acho, Simmons and Yates. Its `agent_memories` row (`Super_Bowl_Roundtable`, @MuRoundtable) comes from `db/migrations/2026_10_19_roundtable_panel_agent.sql`. The panel never tweets.
    - **Granularity:** Single-Game Pinnacle / Super Bowl Consensus.
    - **Focus:** A multi-voice feature designed to highlight conflicting models, narrative disagreements, extreme bold predictions, and final scoreboard consensus before the Super Bowl.
    - **Data Queried:** Upset Leverage ($U_{\text{projected}}$), Single-Game Excitement Index ($E$), SpikeIndex, Player Popularity Index ($Pop_{\text{player}}$).
    - **Curatorial Autonomy:** Acts as a panel of four distinct analysts with contrasting philosophies (Tape/Metrics: Kimes, Contrarian: Acho, Narrativist: Simmons, Fantasy/Props: Yates). Each analyst delivers a distinct bold prediction bordering on the extreme and an official final scoreline, moderated by an Editor.
    - **Structure Directive:** 4-part Expert Roundtable (`# 1. The Introduction (The Stakes)`, `## 2. The Bold Predictions (### sub-sections for each analyst's bold claim)`, `## 3. The X-Factor / MVP Picks (bulleted list tying back to specific metric lenses)`, `## 4. The Final Consensus (The Scoreboard with individual picks and bolded Panel Consensus)`).
    - **Prompt Hook:** Convene the expert roundtable for the Super Bowl. Feature conflicting bold predictions across tape analysis, contrarian logic, historical narrative, and fantasy props, name under-the-radar MVP candidates, and issue the panel's official score consensus.

23. **The Quarterback Duel Tape Breakdown** (`QUARTERBACK_DUEL_TAPE_BREAKDOWN`)
    - **Allowed Writers / Archetypes:** Dan Orlovsky (QB Mechanics & Telestrator Breakdown), Mina Kimes (Metric-Driven Schematic Film Study), KC Joyner (Scientific Efficiency & Matchup Splits) (`ORLOVSKY`, `KIMES`, `JOYNER`, `Film Room Tactical Breakdown`).
    - **Granularity:** Single-Game Marquee Matchup / Pre-Kickoff Film Study.
    - **Focus:** Comprehensive quarterback tape evaluation dissecting pocket mechanics, processing speeds against simulated pressure, middle-of-the-field coverage reads, and high-leverage situational creation.
    - **Data Queried:** Baseline Rating ($R_{\text{rating}}$), Awards Equity ($A_{\text{awards}}$), SpikeIndex, Offensive Pace ($P$), Playoff Clout ($S_{\text{playoff}}$), Matchup Edge (pass, rush, ball security).
    - **Curatorial Autonomy:** Previews marquee matchup strictly through the tape of the two starting signal-callers. Contrasts timing inside structure against off-platform creation, decodes SpikeIndex variance on film, and tests efficiency against two-high shell rotations.
    - **Structure Directive:** 5-part Coaching Tape Breakdown (`# 1. The Film Room Overture`, `## 2. Film Study: [Quarterback A] vs. Opposing Defensive Disguises`, `## 3. Film Study: [Quarterback B] vs. Opposing Defensive Disguises`, `## 4. The High-Leverage Flashpoint: Third-and-Long & The Red Zone`, `### 5. The Tape Room Verdict`).
    - **Prompt Hook:** Deconstruct the Super Bowl quarterback duel through the coaching tape. Contrast how both signal-callers process split-safety looks, navigate collapsing pockets, and handle simulated pressures on third down.

24. **The Super Bowl Media & Interview Audit** (`SUPER_BOWL_MEDIA_INTERVIEW_AUDIT`)
    - **Allowed Writers / Archetypes:** Jeffri Chadiha (Narrative & Leadership), Bill Simmons (Body Language & Historical Pressure), Ryan Clark (Locker Room Dynamics) (`CHADIHA`, `SIMMONS`, `CLARK`, `Super Bowl Media & Narrative Audit`).
    - **Granularity:** Super Bowl Week / Media Availability & Narrative Arc.
    - **Focus:** Evaluating pre-game press conferences, Opening Night transcripts, locker room temperature, psychological pressure, and narrative control leading into the Super Bowl.
    - **Data Queried:** Provided Press Conference Quotes, Quarterback Experience (years), Legacy Floor ($S_{\text{all-time}}$), Awards Equity (MVP Pressure), Player Popularity Index ($Pop_{\text{player}}$), Base Market Gravity ($B$).
    - **Curatorial Autonomy:** Evaluates psychological pressure, locker room temperature, and narrative control rather than X's and O's. Analyzes quotes for bulletin-board material, deflection of pressure, subtle signs of anxiety, and coach podium protection.
    - **Structure Directive:** 5-part Psychological & Narrative Audit (`# 1. The Temperature Check (The Hook)`, `## 2. The Defining Quote (The Pivot Point with blockquote)`, `## 3. Quarterback Demeanor & The Weight of the Moment`, `## 4. Bulletin-Board Material & Unforced Errors`, `### 5. The Final Read (The Verdict)`).
    - **Prompt Hook:** Conduct a psychological press audit of Super Bowl week. Dissect the subtext of podium quotes, analyze quarterback body language under the microscope, isolate unforced errors for bulletin-board material, and determine which team won the media week.

25. **The Franchise Architecture Blueprint** (`FRANCHISE_ARCHITECTURE_BLUEPRINT`)
    - **Allowed Writers / Archetypes:** Mike Sando (Front-Office Executive Blueprint), Bill Barnwell (Resource Allocation & Positional Value) (`SANDO`, `BARNWELL`, `Front-Office Roster Construction`).
    - **Granularity:** Multi-Year / Foundational Roster Core.
    - **Focus:** Forensic breakdown of how the two Super Bowl rosters were constructed, emphasizing positional resource allocation, draft pedigree yield, and youth production integration.
    - **Data Queried:** Blue-Chip Pedigree ($P_{\text{draft}}$), Under-25 Production ($Y_{\text{prod}}$), Positional Multiplier ($M_{\text{pos}}$), Baseline Rating ($R_{\text{rating}}$), Opponent-Adjusted Game Credit.
    - **Curatorial Autonomy:** Analyzes the two finalists exclusively through an executive lens. Contrasts draft pedigree against undervalued efficiency on the margins, assesses Under-25 Production Share ($Y_{\text{prod}}$), and isolates foundational positional investments without mentioning raw salary cap figures.
    - **Structure Directive:** 5-part Front-Office Deep Dive (`# 1. The Executive Summary (The Hook)`, `## 2. The Core Architecture: [AFC Champion Name]`, `## 3. The Core Architecture: [NFC Champion Name]`, `## 4. The Positional Value Disconnect`, `### 5. The Industry Takeaway`).
    - **Prompt Hook:** Deconstruct the roster architecture of the two Super Bowl finalists. Compare how both front offices allocated positional resources, maximized blue-chip draft capital, and integrated youth production to build a championship core.

26. **The Draft Blueprint & Roster Core Audit** (`DRAFT_BLUEPRINT_ROSTER_AUDIT`)
    - **Allowed Writers / Archetypes:** Mike Sando (Front-Office Mandates), Bill Barnwell (Roster Core Diagnostics) (`SANDO`, `BARNWELL`, `Draft & Offseason Horizon`).
    - **Granularity:** Offseason / Multi-Year Core Audit.
    - **Focus:** Diagnostic audit of eliminated teams with locked draft picks (1–28), analyzing roster age curves, foundational core needs, and draft-capital mandates.
    - **Data Queried:** Under-25 Production ($Y_{\text{prod}}$), Blue-Chip Pedigree ($P_{\text{draft}}$), Purgatory Penalty, Base Market Gravity ($B$).
    - **Curatorial Autonomy:** Evaluates the 28 eliminated franchises holding locked first-round draft capital. Groups franchises by structural health (high youth production needing single cornerstone vs. aging rosters in purgatory penalty requiring hard reset). Highlights 3 to 4 urgent franchise blueprints in depth.
    - **Structure Directive:** 4-part Executive Roster Blueprint (`# 1. The Offseason Landscape`, `## 2. The Core Audits (3–4 Featured Franchises)`, `## 3. Macro Positional Scarcity`, `### 4. The Mandate`).
    - **Prompt Hook:** Audit the roster cores of eliminated playoff teams and lottery franchises. Define their primary draft mandate and diagnose which units require total overhauls.

27. **Offseason Mock Draft 1.0** (`OFFSEASON_MOCK_DRAFT`)
    - **Allowed Writers / Archetypes:** Field Yates (Needs & Value Board), Bill Barnwell (Analytics & Positional Capital) (`YATES`, `BARNWELL`, `Draft & Offseason Horizon`).
    - **Granularity:** Offseason / Round 1 Projection.
    - **Focus:** First-round mock draft projecting selections for locked draft slots based on team needs, positional value, and blue-chip player traits.
    - **Data Queried:** Blue-Chip Pedigree ($P_{\text{draft}}$), Under-25 Production ($Y_{\text{prod}}$), Positional Multiplier ($M_{\text{pos}}$), Team Draft Needs (roster deficiencies).
    - **Curatorial Autonomy:** Projects the first 24 picks of the NFL Draft for eliminated teams following the Divisional round. Prioritizes premium positional value (QB, OT, EDGE, CB) over low-impact positions. Evaluates team rosters using Under-25 Production Share ($Y_{\text{prod}}$) to balance immediate foundational needs against developmental high-ceiling prospects without projecting speculative trades.
    - **Structure Directive:** 3-part Authoritative Mock Draft (`# 1. The Board Overview`, `## 2. The First-Round Projections (Picks 1–24 with ## [Pick Number]. [Team Name]: [Prospect Name], [Position], [School])`, `### 3. The Draft Floor Watch`).
    - **Prompt Hook:** Project the first 24 picks of the NFL Draft. Balance premium positional value against immediate roster holes for eliminated franchises.

### 7.2 Local Beat Reporting Taxonomy (`LOCAL_BEAT_REPORTER`)
Local Beat Reporters cover their assigned franchise exclusively. Their research directive keeps them on their own team's games, and they don't receive the national writers' historical-comparison instruction. The beat research directive also points them at `get_defense_special_teams_leaders` for their team's and the opponent's defensive and special-teams leaders. There is no injury data in the league, so the beat directive, the shared preview framing and the matchup preview templates tell writers not to mention injuries.

**Weekly portfolio (`newsroom/local_portfolio.py`):** every team wakeup plans the whole portfolio, however many beat reporters the team has: the Postgame Report Card if the team played last week, the Matchup Primer if it plays this week (aimed at that game via an Editorial Desk subject assignment), the Player Spotlight always, and the Season Progress Report from Week 6. Types the team already has that week (e.g. a national Slot 6–8 primer) are skipped. Articles are dealt round-robin across the team's reporters, starting at a different reporter each week, so one reporter writes all of them and no reporter is stuck on the postgame report. Each is saved under the team with slot `Local Beat N` (`tests/test_local_portfolio.py`).


1. **The Postgame Report Card** (`LOCAL_POSTGAME_REPORT_CARD`)
   - **Beat Archetype:** Local Beat Writer / Postgame Columnist (2–4 hours post-whistle).
   - **Focus:** Immediate unit execution versus expectations, unit letter grades (A+ through F), offensive line protection breakdowns, third-down stalls, and coaching decisions.
   - **Data Queried:** Unit Offensive Pace ($P$), Opponent-Adjusted Game Credit, Giveaways per Game (ball security), Pass Rush Rating (Madden rank).

2. **The Local Matchup Primer & Game-Plan Blueprint** (`LOCAL_MATCHUP_PRIMER`)
   - **Beat Archetype:** Senior Beat Reporter (Friday / Saturday morning preview).
   - **Focus:** Home-team perspective matchup reality, 3 hyper-specific micro battles, game-plan victory blueprint, and final score prediction.
   - **Data Queried:** Matchup Edge (pass, rush, ball security), Upset Leverage ($U_{\text{projected}}$), Trench Edge (O-line vs. pass rush ratings), both teams' defense & special-teams leaders.
   - **Defense:** each of the three micro-battles is anchored in both players' season numbers where they exist (sacks and TFL vs. sacks allowed, catches and yards vs. INT and pass deflections), with no invented pressure rates, snaps or targets; the Defensive Mandate is a sack or takeaway target tied to named defenders (it was a third-down stop percentage, which isn't recorded).

3. **The Local Player Spotlight** (`LOCAL_PLAYER_SPOTLIGHT`)
   - **Beat Archetype:** Franchise Beat Reporter / Film Analyst.
   - **Focus:** Single local player's role evolution, developmental leap, tape & assignment execution, coaching trust, and roster implications.
   - **Data Queried:** SpikeIndex, Touch Share & Scrimmage Yard Share (season), Positional Multiplier ($M_{\text{pos}}$), Blue-Chip Pedigree ($P_{\text{draft}}$), Under-25 Production Share ($Y_{\text{prod}}$), defense & special-teams lines.
   - **Defense:** Defenders and specialists are eligible: the curatorial block points at `get_defense_special_teams_leaders` and `get_player_season_stats`, with the unknown-not-zero, no-team-sums and no pressure/snap/target rules (JR, Oct 14). The whole roster is in play (a pass rusher piling up sacks, a ball-hawking corner, a kicker or returner), and usage is described from recorded numbers rather than target volume or snap rate.

4. **The Local Season-to-Date Progress Report** (`LOCAL_SEASON_PROGRESS_REPORT`)
   - **Beat Archetype:** Senior Beat Columnist (Midseason / Season Audit).
   - **Focus:** Comprehensive evaluation of the home team's campaign, offensive/defensive identity check, structural strengths, lingering flaws, coaching staff seat temperature, and second-half win benchmark.
   - **Data Queried:** Trajectory Multiplier ($T$), Opponent-Adjusted Game Credit, Youth Production Share ($Y_{\text{prod}}$), Purgatory Penalty, Base Market Gravity ($B$).

### 7.3 The Editorial Assignment Desk & Article-First Commissioning Architecture
Rather than waking up a fixed list of writer agents who generate generic articles, the Swarm operates under an **Article-First Editorial Commissioning Architecture** (`commission_weekly_editorial_slate`):

1. **Weekly Default Distribution (Full 10-Slot Editorial Matrix):**
   - `docs/EDITORIAL_MATRIX.md` is the source of truth for the weekly slate. Every weekly run (`dispatch_weekly_national`) commissions all 10 matrix slots for the week: Hero, Slots 1–8, and Standalone.
   - `article_count` / `target_articles` truncate the slate and are reserved for tests and debugging (`tests/test_wakeups.py --article-count`, `tests/test_editorial_flow.py --count`); production never passes them.
   - The weekly national run does not wake up any extra beat reporters. Slots 6–8 come only from the matrix. Local beat portfolios (`LOCAL_POSTGAME_REPORT_CARD`, `LOCAL_PLAYER_SPOTLIGHT`, `LOCAL_SEASON_PROGRESS_REPORT`, plus team-specific `LOCAL_MATCHUP_PRIMER`) come from on-demand team wakeups (`dispatch_team_local`).
   - **Slot Targeting (`newsroom/slot_targets.py`):** Before writers are assigned, `commission_weekly_editorial_slate` resolves the subject of every targeted slot per the matrix's *Slot Targeting Rules*. Matchup primers go to the week's most interesting scheduled games, ranked by `rank_games_by_interest`: projected $E$ (§2.1) plus division-game and playoff-race stakes (byes excluded, 2-week team rotation in the regular season, covering the side with fewer primers this season), and in Super Bowl week to the AFC and NFC champions. Phase 4 `FRANCHISE_STATE_OF_THE_UNION` goes to a mathematically eliminated team. Week 19 Exit slots go to teams that missed the playoffs, and W20–22 `PLAYOFF_AUTOPSY` slots go to the prior round's losers. All of these are distinct teams, most popular first.
   - Each primer is written by a beat reporter for its subject team. Every targeted article gets an `EDITORIAL DESK SUBJECT ASSIGNMENT` block in its prompt, which overrides subject-selection instructions, and is saved under the subject's `team_id`. If league data can't be loaded, slots fall back to writer-chosen subjects.
   - **Postseason week numbering:** 19 = Wild Card, 20 = Divisional, 21 = Conference Championships, 22 = Super Bowl bye, 23 = Super Bowl. Historical imports (`pipeline/batch_menus.py`) store the Super Bowl at week 23. `db/migrations/migrate_super_bowl_week.py` moves older week-22 Super Bowl rows to week 23.
2. **Matrix-Driven Editorial Slate Planning (`docs/EDITORIAL_MATRIX.md`):**
   - Implements the definitive **18-Week Regular Season & 5-Week Postseason Editorial Matrix** to commission a structured portfolio of article types per week:
     - **Regular Season (Weeks 1–18):** Fixed Hero Slot (`PRE_SEASON_ROSTER_TIERS` in W1, `MACRO_SLATE_RECAP` in W2–18), Slot 1 (`SLATE_LOOKAHEAD_PREDICTIONS`), Slot 2 (`NFL_TOP_100_HIERARCHY` in W1, `SINGLE_GAME_SCHEMATIC_AUTOPSY` in W2–18), Slots 3–5 (Feature Trio selected dynamically from 4 distinct seasonal phase pools), Slots 6–8 (3x `LOCAL_MATCHUP_PRIMER`), and Standalone (`POWER_RANKINGS`).
     - **Postseason (Weeks 19–23):** Custom 10-slot boards tailored for Wild Card Eve (W19), Divisional Eve (W20), Championship Eve (W21), Super Bowl Bye (W22), and Super Bowl Week (W23).
   - Dynamically rotates national columnists (`SIMMONS`, `KIMES`, `ACHO`, `SKIP_BAYLESS`, `YATES`) into `NATIONAL_OPINION_COLUMN` slots during regular-season feature trios.
3. **Qualified Writer Matching & Load Balancing:**
   - For each commissioned article type, the Editorial Desk queries `allowed_writers` from the taxonomy (`newsroom/config/article_types.json`).
   - Picks each writer at random from the least-used allowed writers so far this week (`writer_assignment_counts`). This spreads work but doesn't prevent repeats: a writer can get more than one article in a week when the allowed lists overlap. Rotating-columnist slots name their writer directly and skip the balancing.
   - **Writer records:** every writer name in `allowed_writers` and the columnist pools is an archetype key in `agent_personas.json`. `compile_agent_prompt` uses the archetype's `archetypes` persona, except for articles by writers listed under `article_personas` in `agent_personas.json` (long-form voices for writers whose persona is written for tweets: YATES, SIMMONS, KIMES, ACHO, CLARK, ORLOVSKY; `tests/test_writer_config.py` fails if a national writer has only a tweet persona), and `FANTASY_*` articles, which use `fantasy_writers.json`. `get_agent_record` loads the `agent_memories` row whose `archetype` matches exactly (oldest first); old shorthand names are mapped first by `WRITER_ARCHETYPE_ALIASES` (`BAYLESS` → `SKIP_BAYLESS`). Tweet-only accounts such as `SKIP_TWEET` are separate agents and are never used for articles. `tests/test_writer_config.py` checks that every article writer has a persona, every opinion columnist has a curatorial directive, and no columnist pool or article type uses an alias.
   - Binds each writer to their specific commissioned `article_type`, ensuring prompt injection of custom curatorial lenses, structure directives, and analytical parameters.

### 7.3.1 The Mu Fantasy Desk (`newsroom/fantasy_desk.py`, `docs/FANTASY_EDITORIAL_MATRIX.md`)
- **When:** `weekly_run` calls `dispatch_weekly_fantasy(season, week)` after the national slate, Weeks 1–18 (W19–23: no slate; the page keeps W18). A week is skipped when it already has `Fantasy …` slots (unless `--force`).
- **Slots** (`fantasy_slate(week)`): `Fantasy Hero`, `Fantasy 1`–`3` (front page) and `Fantasy Shelf N` ("View all analysis").
  - W1: `FANTASY_DRAFT_TIERS` ×4 (WR, RB, QB, TE, position given as a desk note) + Sleepers & Busts, Mock Draft, Rookie Rankings.
  - W2–14: Start/Sit, Waiver Wire, Weekly Reactions, Card 03 from `CARD3_SCHEDULE` (W2 Overreaction, W3 DvP, W4 Overreaction, W5 Overreaction, W6 Buy/Sell, W7 ADP Checkpoint, W8 ROS Tiers, W9 DvP, W10 Buy/Sell, W11 ROS Tiers, W12 Trade Deadline Special, W13 Playoff Schedule Planner, W14 Must-Win Week); shelf: Rankings & Tiers, Box Score & Usage, 10 Things, Matchup Exploits (W3+).
  - W15–17: Start/Sit (playoff/championship editions), Streamers, Reactions, Playoff Schedule Planner (W15) / Ceiling Plays; W18: Season in Review, Awards (with All-Fantasy Team), Draft Retro, Projection Report Card, Dynasty Early Look.
  - K/D/ST (`FANTASY_STREAMING_DST_K`) is gated by `KDST_ENABLED = False` until migration phase 3.
- **Writers:** 26 `FANTASY_*` types in `article_types.json`, written by BERRY, YATES, BARNWELL and JOYNER (spread by least-used, like the national desk). Yates writes all four W1 Draft Tiers; Joyner writes 10 Things. Length targets are minimums ("at least N words; longer is fine"). For `FANTASY_*` articles `compile_agent_prompt` swaps in the writer's fantasy voice and selection filter from `newsroom/config/fantasy_writers.json` (tweet personas and national directives are untouched), uses `FANTASY_DESK_GUIDELINES` instead of the national research-tools block (full PPR, all numbers from tools, "rostered in X% of leagues" with no mention of simulations, no injuries, clear verdicts, never invent past calls), a fantasy-calendar temporal block (`temporal_mode = "FANTASY"`), the fantasy tools, and the desk note as an `EDITORIAL DESK ASSIGNMENT (MANDATORY)` block. Fantasy articles are saved with `team_id` NULL.
- **Brief:** a small fantasy brief (season, publication week, fantasy phase, week label, results-through week, scoring), not the national assignment brief.

### 7.4 Playoff-Specific Dynamic Overrides Architecture (`newsroom/config/playoff_overrides.json`)
During the single-elimination tournament and Super Bowl run (Weeks 19–23), standard regular-season directives are dynamically superseded by targeted postseason overrides. The orchestrator inspects `target_weeks` at compile time and overrides the baseline article directive, curatorial autonomy filter, and structure specification without mutating baseline schemas:

- **Lookup & validation (`newsroom/playoff_overrides.py`):** `find_active_override(overrides, article_type, week)` picks the override; `normalize_temporal_mode` reads `temporal_mode` case-insensitively and accepts only `RETROSPECTIVE`, `PROSPECTIVE` or `CUMULATIVE` (anything else falls back to the article type's mode in `TEMPORAL_ARTICLE_MODES`). `SwarmDispatcher` runs `validate_playoff_overrides` on load and prints a warning per problem: unknown article type or temporal mode, target weeks outside 19–23, a week claimed by two overrides of the same type, or a misused `preview_week`. `tests/test_playoff_overrides.py` also validates the shipped config.
- **`preview_week` (optional, `PROSPECTIVE` only):** the week whose games the override previews, when it isn't the publication week. The prompt then names that week and tells the writer to fetch it with `get_weekly_slate`, since the brief's `preview_slate` only holds the publication week.

1. **`SINGLE_GAME_SCHEMATIC_AUTOPSY` Playoff Overrides:**
   - **Week 19:** no override. It looks back at regular-season Week 18, so the base article type applies.
   - **Weeks 20–22 (`PLAYOFF_ELIMINATION_ROUND_AUTOPSY` | `RETROSPECTIVE`):**
     - **Focus:** High-leverage situational football, tightened personnel packages, blitz counters, and scheme-exposure points from the elimination round just completed.
     - **Curatorial Filter:** Isolates the single clash with highest tactical leverage, third-down aggression, or where fourth-down game management swung win probability; a defense exposing an offense counts when the box score shows it (sacks, takeaways, a defensive score).
     - **Defense grounding:** the schematic collision is built from the box score (sacks, TFL, takeaways, pass deflections and the offense's lines), and the high-leverage moments are ones the box score supports (strip-sack, interception, stalled red-zone trip, long or missed field goal), with no invented down-and-distance, pressure rates, coverage percentages, snaps or targets.
     - **Structure:** 4-part Tape Autopsy (`# 1. The Decisive Blueprint`, `## 2. The Core Schematic Collision`, `## 3. The High-Leverage Breakdown`, `### 4. The Elimination Takeaway`).
   - **Week 23 (`SUPER_BOWL_SCHEMATIC_MASTERCLASS` | `PROSPECTIVE`):**
     - **Focus:** Exhaustive pre-game tactical blueprint previewing the Super Bowl clash through an X's and O's coaching lens (disregarding celebrity/media week storylines).
     - **Curatorial Filter:** Trench warfare leverage (pass rush vs. slide protection), safety disguise rotations against primary passing concepts.
     - **Structure:** 5-part Masterclass (`# 1. The Grand Chess Match`, `## 2. Trench Warfare & Point-of-Attack Leverage`, `## 3. Coverage Shells vs. Passing Architecture`, `## 4. The Critical Situational Battle`, `### 5. The Coordinator Verdict`).

2. **`SLATE_LOOKAHEAD_PREDICTIONS` Playoff Overrides:**
   - **Weeks 19–21 (`POSTSEASON_ROUND_LOOKAHEAD` | `PROSPECTIVE`):**
     - **Focus:** High-stakes single-elimination preview capsules for every game in the upcoming playoff round (Wild Card, Divisional, Conference Championship). Strict ban on betting lines/spreads.
     - **Curatorial Filter:** Mandatory full enumeration covering every scheduled tournament matchup. Mismatches use both sides of the ball: each team's defensive leaders from `get_defense_special_teams_leaders` against the quarterback's interceptions and sacks taken, never summed into team totals.
     - **Capsules:** Stat to Know may be offensive, defensive or special teams; Tactical Flashpoint names the players on both sides with their season numbers (no pressure rates, coverage percentages, snaps or targets); the X-Factor can be a defender or specialist.
     - **Structure:** Modular Postseason Capsules (`# 1. The Round Overview`, `## 2. Matchup Enumeration with kickoff time from the slate (no invented broadcast network)`, `3. Structured Sub-Sections: What We're Hearing, Stat to Know, Tactical Flashpoint, The X-Factor Player, Official Prediction`).
   - **Week 22 (`SUPER_BOWL_EARLY_TALE_OF_THE_TAPE` | `PROSPECTIVE`, `preview_week: 23`):**
     - **Focus:** Comprehensive bye-week Tale of the Tape comparing both Super Bowl finalists from top to bottom.
     - **Curatorial Filter:** Contrasts cumulative efficiency profiles across every unit and neutral-site dome variables.
     - **Structure:** 6-part Tale of the Tape (`# 1. The Championship Tale of the Tape`, `## 2. The Macro Profile & Efficiency Baselines`, `## 3. Unit Clash 1: Team A Offense vs. Team B Defense`, `## 4. Unit Clash 2: Team B Offense vs. Team A Defense`, `## 5. The Volatility & Spike Audit`, `### 6. The Bye-Week Installation Mandate`).

3. **`AWARDS_TRACKER` Playoff Overrides** (both `CUMULATIVE`; award voting covers the regular season only, so writers judge Weeks 1–18 and don't credit playoff games):
   - **Week 21 (`AWARDS_FINALISTS`):** three finalists each for MVP, OPOY and OROY, with the projected winner. Structure: `# The Ballots Are In`, one `##` per award with three bolded finalists, `## The Projected Winners`, `### The Snub`.
   - **Week 22 (`AWARDS_WINNERS`):** the publication's winner and runner-up for each award. Structure: `#` headline naming the MVP, one `##` per award (`## MVP: [Player], [Position], [Team]`) with the case and the runner-up, `### The Season in One Line`.

4. **`SUPER_BOWL_CONTENDER_BLUEPRINT` Playoff Overrides** (both `CUMULATIVE`; only teams playing that week's playoff games are in scope, judged on the regular season plus their playoff games so far):
   - **Week 20 (`DIVISIONAL_ROUND_CONTENDER_AUDIT`):** the eight divisional-round teams, three wins from a title. Structure: `# The Championship Standard`, `##` heavyweights (2–3 teams), `## The Vulnerable Favorite`, `### The Dangerous Underdog`.
   - **Week 21 (`FINAL_FOUR_CONTENDER_AUDIT`):** the four conference finalists ranked 1–4 by title chances. Structure: `#` hook, four numbered `## [Rank]. [City] [Team] ([W-L])` sections (path, case, fatal flaw), `### The Verdict` naming the champion.

### 7.5 Database Schema & Newsroom REST API Specifications
To support seamless frontend newsroom consumption, longform editorial content is persisted with full metadata and relational tagging:

1. **`news_feed` Database Schema:**
   ```sql
   CREATE TABLE IF NOT EXISTS news_feed (
       id SERIAL PRIMARY KEY,
       season INT,
       week INT,
       team_id INT,                -- teams.id (no FK constraint in live schema)
       content_type VARCHAR(50),   -- 'ARTICLE' | 'TWEET'
       persona VARCHAR(50),        -- e.g. 'BARNWELL', 'SANDO', 'LOCAL_BEAT_REPORTER'
       headline VARCHAR(255),
       content TEXT,
       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
       excerpt TEXT,
       article_type VARCHAR(64),   -- e.g. 'MACRO_SLATE_RECAP', 'POWER_RANKINGS'
       slot VARCHAR(32)            -- 'Hero Slot', 'Slot 1'..'Slot 8', 'Standalone', or 'Local Beat N'
   );
   ```

2. **Relational Entity Tagging:**
   - **`article_teams`**: Maps mentioned franchises (`PRIMARY`, `FEATURED`, `MENTIONED`) with mention frequencies. Team mentions come from `newsroom/entity_patterns.team_mention_pattern`: full name, nickname, city (except shared New York / Los Angeles) and uppercase abbreviation, all **case-sensitive**, so ordinary words ("was", "no", "ten", "bills") are not team mentions. Abbreviations that are English words (`NO`, `WAS`, `TEN`, `MIN`, `CAR`, `SEA`, `DEN`) are never matched, even in all caps; those teams are found by nickname or city (`tests/test_entity_patterns.py`). A team is `PRIMARY` if it's the article's assigned team, appears in the headline or a header, or is mentioned 5+ times; `FEATURED` at 3+; otherwise `MENTIONED`.
   - **`article_players`**: Maps mentioned players with their active franchise, position, mention tier, and mention frequencies.

3. **Newsroom REST API Endpoints (`server/api.py`):**
   - **`GET /api/news/week/{week}?season={season}`**:
     - Returns the complete weekly news slate partitioned into canonical display slots:
       - **`hero`**: Hero Slot article (top banner).
       - **`secondary`**: Slots 1 and 2 (e.g. Schematic Autopsy, Slate Lookahead).
       - **`features`**: Slots 3, 4, and 5 (seasonal feature trio).
       - **`local_primers`**: Slots 6, 7, and 8 only (the national desk's local matchup primers).
       - **`standalone`**: Standalone Power Rankings 32-team hierarchy.
       - **`other`**: Any other article that week (e.g. unslotted); the frontend shows these with the main stories.
     - Team-page beat coverage (slot `Local Beat N`, from on-demand team wakeups) is excluded; it appears only via `GET /api/team/{abbr}/news`.
     - Mu Fantasy articles (`article_type` `FANTASY_*`, slots `Fantasy …`) are excluded; they belong to the fantasy page.
     - Includes resolved author objects (`display_name`, `handle`, `archetype`) and attached entity tags (`tagged_teams`, `tagged_players`).
   - **`GET /api/news/article/{article_id}`**:
     - Returns full article text, headline, teaser excerpt, assigned slot, author metadata, and all tagged entities for reading view rendering.

### 7.6 Stored Weekly Power Rankings (`newsroom/power_rankings.py`)
The Standalone `POWER_RANKINGS` article is the source of the stored weekly ranking:
1. **Continuity in the prompt:** `compile_agent_prompt` adds a "POWER RANKINGS CONTINUITY" block, which lists the latest earlier stored week's order (or says none has been published, so every team is "Previous: — | New") and restates the header format `## [Rank]. [City] [Team] ([W-L]) (Previous: N | Change: ±N)`. This means movement indicators come from real prior rankings.
2. **Parsing:** each numbered header that names a team (resolved by its unique nickname, or abbreviation) or carries a W-L record is a team entry. Other numbered headers (e.g. "## 2. The Drop-Off") are treated as section breaks. The bold lead-in is the `kicker`; the first paragraph after it is the `blurb` (a horizontal rule or blank line ends it, so the walk-off isn't included).
3. **Validation:** the article must rank all 32 teams exactly once with ranks 1–32. In a live run, a failing article gets one rewrite in the same chat, with the specific errors (missing/duplicate teams or ranks, unmatched headers).
4. **Storage:** on save to `news_feed`, a valid article replaces that week's `power_rankings` rows. Only the order, kicker and blurb come from the text. `record` is snapshotted from `team_seasons`, and `previous_rank` / `movement` come from the stored previous week, not the writer's indicators. An article that still fails validation is saved as an article but no ranking is stored (logged as `POWER RANKINGS NOT STORED`).

### 7.7 Evolving Agent Memory (`newsroom/agent_memory.py`)
Every agent (national writers, beat reporters, pundits and fans) keeps its hand-written identity line in `agent_memories.persistent_memory`, which never changes (the one exception: `2026_10_22_agent_wire_social.sql` swapped the name inside the line for agents it renamed). Three evolving layers sit on top of it, written by `db/migrations/2026_10_20_agent_memory.sql`'s tables:
1. **Takes log (`agent_memory_entries`).** After a live post is saved (`SwarmDispatcher._record_agent_memory`): a tweet is stored as a take as-is (no extra call); an article gets one extra model call (`takes_extraction_prompt`) that returns up to 3 first-person stances the writer committed to, each tagged with a team (resolved by nickname) and/or player.
2. **Prediction track record (`agent_predictions`).** For `PROSPECTIVE` articles, `parse_score_predictions` reads `Projected Final: Team N, Team N` lines (and, for the Super Bowl Roundtable, `Panellist: Team N, Team N` lines, labelled by panellist) and stores each pick against the matching game in the article's preview week (`preview_week` for overrides). Picks are graded when a prompt is built: correct, wrong, push (a predicted or actual tie) or pending.
3. **Season summaries (`agent_season_summaries`).** At the start of each live weekly run, agents with at least 3 new takes since their last summary get it rewritten through last week (one call each, ≤120 words, first person). Each rewrite is a new row per `through_week`, so older weeks keep the summary they saw.

**In the prompt:** under "PERSISTENT WORLDVIEW & MEMORIES", after the identity line, `build_memory_block` adds the latest summary, the 6 most recent takes ("stay consistent, or say plainly that you've changed your mind"), up to 3 earlier takes about the article's subject team, and the track record: the season's winner-picking record plus the 5 most recent graded picks with the final score, telling the writer to own misses in their own voice. Only memories from before the article's publication week are used, and graded results only count games from earlier weeks, so regenerating an old week can't see the future. If the memory tables don't exist yet, prompts simply have no memory block.

**Failure handling:** memory recording runs after the article is committed and never affects it; failures are logged as `MEMORY NOT RECORDED`. Deleting `news_feed` rows cascades to their takes and predictions; `db/clear_news_feed.sh` also clears the summaries. Tests: `tests/test_agent_memory.py`; `tools/verify_newsroom.py` builds memory blocks for each kind of agent against the live database, and exercises the whole write path (parse and store a prediction, store takes and a tweet take, check the week cutoff, gather summary inputs, store a summary, read the graded block back) inside a transaction it rolls back.

### 7.8 Agent Wire Social Desk (`newsroom/social_desk.py`, `docs/SOCIAL_MATRIX.md`)

**Status:** build steps 3–6 are built: planning, the post writer (JR chose Gemini 3.8 Flash for every post kind after the side-by-side test), publishing from the weekly run and team wakeups, and the `/api/social/*` endpoints (§4). Step 7, the Agent Wire frontend on live data, is built (§5). First live week: 2035 Week 18 (Oct 14), 100 posts and 20 trends. After it, JR asked for a player-first mix and 20 ex-player accounts (Oct 14; `db/migrations/2026_10_25_former_players.sql`), described below.

- **What it does:** `load_week_context(cur, season, week)` reads one publication week; `plan_social_week(ctx)` returns about 100 assignments, each an account, a post kind, a subject and a **facts packet** (no model call, nothing written). Week N looks back at Week N-1's completed games and ahead to Week N's games; standings and season totals count only games before Week N, and a game in Week N or later never shows a score, so replanning an old week can't leak results.
- **Buckets (player-first mix, JR Oct 14):** shares (one per article stored for the week, from the writer's account; Skip's column shares from `SKIP_TWEET`, the roundtable panel never shares; local beat articles from one of that team's reporters); **reactions only from die-hards, one per team that played, no extras** (only die-hards post about a team as a team); **performances 14** (top full-PPR lines; offense only until the capture loads defense and kicking), written by fantasy accounts, pundits, ex-players and bandwagon fans in rotation, never die-hards; **bad games 8** (`BAD_GAME`: players who averaged 12+ full-PPR points over 3+ earlier games and scored at most 65% of that, biggest drop first, then QBs with 3+ interceptions; players with no line are skipped, since they may have been inactive), mostly bandwagon fans clowning the flop, plus pundits, an ex-player and @GridironMemes; **comparisons 10** (`COMPARISON`): 5 die-hards argue their team's best player beats last week's opponent's best at the same position, 3 ex-players rank the league's top five at their old position with their old team's best player alongside, pundits fill the rest with top-five debates; beat notes 5; standings 2 (from Week 3: a pundit and a bandwagon fan); previews 6 (fans of the top projected games, plus 2 fantasy start/sit posts in Weeks 2–17); memes 2; replies 10–15 (placeholders; parents are picked after writing). Replies flex to land the week near 100; if a full slate with a big article week is still over, performances shrink to no fewer than 10. Player names stored in capitals ("JERRY RAYFORD") are posted in normal case, and season ranks say RB, not HB.
- **What the league is talking about (JR, Oct 14; `Week.player_buzz`, `Week.team_stories`; frequencies checked against the 2035 season):**
  - **Player games**, tier 1 ("the league is talking"): 400+ passing yards, 5+ passing TDs, 150+ rushing or receiving yards (TE 90+), 3+ total TDs or 40+ full-PPR points. Tier 2 ("historic"): 6+ passing TDs, 200+ rushing or receiving yards (TE 150+), 4+ total TDs, 50+ PPR, or a tier-1 game that is the season's best by full-PPR points at its position (from Week 4). Season context in the regular season: round numbers crossed that week (5,000 passing yards, 50 passing TDs, 1,500/2,000 rushing yards, 1,500 receiving yards and 20 total TDs make news alone; 4,000 passing yards, 40 TDs, 1,000 yards and 15 TDs are only mentioned when the game is already news), NFL single-season records as of 2025 (`SEASON_RECORDS`: breaking one is tier 2; being on pace for or already past one is tier 1 from Week 12, context before that). Tier 1 gets three performance posts (pundit, ex-player preferring the player's team and position, bandwagon fan), tier 2 four (plus a fantasy account) and a comparison on where the game ranks among the season's best at the position. Facts carry `why_people_are_talking` (up to 3 reasons, with "the 2nd-most in a game this season" ranks) and `how_big`; each post gets its own angle. These posts sit at the front of their buckets; ordinary performances fill the rest. QBs with 2+ interceptions in a loss count as bad games (no QB threw 3 in a 2035 game).
  - **Team storylines (`TEAM_STORY`, regular season, teams that played last week; up to 8 posts):** unbeaten or winless from 5 games (tier 2 from 10, with real NFL history in `nfl_history_through_2025`: the 1972 Dolphins and 2007 Patriots, the 2008 Lions and 2017 Browns), winning or losing streaks of 6+ (tier 2 from 10), a 6+ game winning streak ending (tier 2 for the first loss after an 8-0 or better start), a first win after 0-5 or worse, wins by 35+, 50+ points, upsets (a 0.35+ gap in winning percentage before the game, both teams with 5+ games) and the points record (606, 2013 Broncos: on pace from 6 games, past it, or broken that week as tier 2). One story per team with up to 3 reasons. Good news: tier 1 a pundit and a bandwagon fan, tier 2 two pundits (the second takes the skeptic's case), the team's ex-player if it has one, and a bandwagon fan. Bad news: the meme page and pundits, plus bandwagon fans hopping off and an ex-player at tier 2. Filed under the team, so they show in its Team Hub. A team wakeup for a team with a storyline always gets at least one national voice, and that post is the storyline. Agent Wire labels them "Storyline".
  - **Busy weeks:** to stay near 100, the planner trims the last-added performances (to 10), comparisons (to 7), bad games (to 5) and storylines (to 4), in that order. In 2035 Weeks 2–18 that lands every week at 100 or 101.
  - **Engagement:** `buzz_heat` in `social_desk.json` multiplies a buzz post's subject heat (tier 1 ×1.5, tier 2 ×2.2), and `TEAM_STORY` has kind factor 1.3, so these posts draw the replies and lead the trends.
- **Ex-players (`FORMER_PLAYER`, 20 accounts):** real retired stars plus 2026 NFL Top 50 players who aren't on a 2035 roster (treated as retired before 2035), written "in the style of" like the pundits: Mahomes, Josh Allen, Justin Jefferson, Ja'Marr Chase, McCaffrey, Saquon, Micah Parsons, T.J. Watt, Derrick Henry, Dak, Maxx Crosby, Herbert, Brady, Marshawn Lynch, Cam Newton, Vick, Brees, Strahan, Calvin Johnson, Larry Fitzgerald. `assigned_team_id` is the old team and the identity line says "I played <POS> for the <team>"; the planner reads the position from it. Their posts are league-wide and never filed under the old team (outside its team wakeup). Every assignment by an ex-player, and by the ex-player pundits (`FORMER_TEAM_SLANT`: Ochocinco CIN, Shannon DEN, J.J. Watt HOU, RG3 WAS, Clark PIT, McAfee IND), carries `slant_team`, which the writer turns into a "soft spot for your old team" line. Followers 0.8–6M, like rate 0.004. Agent Wire labels them "Former <team>" with an orange badge.
- **Postseason (Week 19+):** a few extra reactions, then the room goes to `PLAYER` posts by fans of eliminated teams (their own team's top players, alternating with players still alive). Alive teams are this week's scheduled teams, or last week's playoff winners in the Super Bowl bye week. **Preseason** (no completed games): every team's fans preview their opener, then pundits.
- **Accounts:** least-used first under weekly caps (pundit 3, fantasy 4, beat 2, fan 2, bandwagon 3, ex-player 3), with position affinities for pundits (e.g. QB → RG3, Cowherd, Stephen A). The pundit pool mirrors `TWEET_EXCLUDED_ARCHETYPES` and leaves out the fantasy, meme and draft accounts, which the desk assigns on purpose. Deterministic for a season and week.
- **Dry run:** `python3 -m newsroom.social_desk --season 2035 --week 18 [--json plan.json]` prints the plan; when the week's slates aren't stored yet it plans placeholder shares, one per national and fantasy slot.
- **Writer (`newsroom/social_writer.py`, build step 4):** `build_post_prompt` puts the account's tweet persona (`agent_personas.json` `archetypes`), its identity line and memory block, and `SOCIAL_RULES` (one post of at most 280 characters, numbers only from the facts packet, no tools and no metric names even if the persona mentions them, no ratings or Madden, at most 2 hashtags) in the system instruction, and a per-kind task plus the facts packet in the prompt (replies also get the parent post). `write_post` makes one Gemini call with no tools and asks once for a shorter version if the post runs over 280 characters; `clean_post` strips labels, quotes, markdown and links, keeps 2 hashtags and flags problems (length, ratings). Facts packets describe a game's excitement in words ("an entertaining game") and never carry the index. Usage and cost come from `usage_metadata` (`PRICES`: 3.8 Flash $0.75/$3.75 per 1M until Dec 31, 2026; 3.5 Flash-Lite $0.30/$2.50).
- **Model comparison:** `python3 tools/social_model_compare.py [--week 9] [--models gemini-3.8-flash gemini-3.5-flash-lite]` writes the same 20 planned posts (6 reactions, 3 performances, 2 beat notes, 1 standings, 2 previews incl. a fantasy start/sit, 2 memes, 2 shares when the week has stored articles, and 2 replies from rival fans) with each model, prints cost per post, and saves posts, prompts and usage to `captures/social_compare/`. It reads the database and writes nothing to it.
- **Publishing (`newsroom/social_publish.py`, build step 5):**
  - `run_social_week(conn_info, season, week)` (called by `weekly_run` after the slates): skips a week that already has posts with slot `Agent Wire` (unless `force`), plans, writes the non-reply posts in parallel (5 workers), then plans and writes the reply pass, then refreshes trends. `dry_run` prints the plan only. Posts with an API error, no text, over 280 characters after the one rewrite, or a ratings/Madden mention are logged `[NOT SAVED]` and skipped.
  - `run_team_social(conn_info, season, week, team_id, local_articles)` (called by `dispatch_team_local` on live, saved wakeups): `plan_team_wakeup` gives each of the team's beat reporters a note, each die-hard a reaction to last week's game or a preview of this week's (alternating), 3 pundits if the team's popularity is above 70 or 1 above 55 (each posts about one of the team's top lines last week, or a top-five debate at its best player's position), the team's ex-player, if it has one, on a top-five debate at his old position, and a share of each local article written in that wakeup by its reporter; agents who already posted in an earlier wakeup for the team and week are skipped. Saved with slot `Agent Wire Local`.
  - **Saving:** each post is a `news_feed` row (`content_type` 'TWEET', `persona` = archetype, `agent_id`, `post_kind`, `posted_at`, `reply_to_id`, `linked_article_id` for shares, `subject_game_id`, `subject_player_id`, `likes`, `reposts`, `reply_count`, `views`, `hashtags`). Teams are tagged by `EntityTaggingService` from the text plus both teams of the post's game; a player subject is tagged `PRIMARY`. Every post except a share is stored as the author's take (`agent_memory.store_tweet_take`).
  - **Replies (`social_desk.plan_replies`):** the 20 replies go to the 8 most-liked posts (3, 3, 3, 3, 2, 2, 2, 2). A fan's post gets the other team's fans first, then its own team's fans and bandwagon fans; a pundit's or writer's gets a set foil (`CONTRARIAN`, e.g. Shannon ↔ Skip, Stephen A ↔ McAfee, Barnwell → Skip, Sando → Cowherd), then fans of the teams involved; a meme gets the roasted team's fans; a beat note gets that team's fans. Replies count against the weekly caps and carry the parent's facts and text.
  - **Variety:** every fan post (die-hard or bandwagon, not shares or replies) gets one `angle` from `FAN_ANGLES` (a player, the turning point, the season, trash talk, the coaching, the atmosphere, one number, next week), seeded by account and subject, and the rules discourage stock fan phrases ("casuals", "real fans", "plastic franchise", "bleed … forever"). Replies to the same post take different stances in turn: push back, agree and add, joke. The first live week (Oct 14) showed nearly every fan post leaning on the same phrases and four near-identical bandwagon replies to one Barnwell share, which these rules fix.
  - **Simulated time (`posted_at`):** Week 1's Thursday is the first Thursday after Labor Day of the season year, each week 7 days later; kickoffs come from `games.game_time` (default Sunday 1:00 PM) and games last 3h10m. Reactions land 5–90 minutes after their game ends, game memes 30 minutes–10 hours after, performances and bad games between the game's end and Monday night, storylines Monday 9:00–Wednesday 20:00, comparisons Tuesday 9:00–Thursday 20:00, beat notes, shares and draft posts Tuesday morning–Wednesday evening, standings Tuesday, postseason player posts Tuesday–Saturday, previews from Thursday (at most two days before kickoff) until an hour before kickoff, fantasy start/sit Friday–Sunday morning, replies 3–240 minutes after their parent. Planned posts (everything except reactions, game memes and replies) that land between 1:00 and 6:59 move to 7:00–9:59 that morning. Seeded per post, so a rerun gives the same times.
  - **Engagement:** `followers` is set once per account (`agent_memories.followers`, seeded by `agent_id`): insiders (Schefter, Rapoport) 1–5M, other national voices 0.5–3M, The Flex Spot 250–600K, @GridironMemes 600K–1M, @MockDraftMarty 50–150K, beat reporters 20–150K and fans 200–25K scaled by their team's popularity, bandwagon fans 500–8K. Likes = followers × the role's like rate × subject heat (game excitement and team popularity, player popularity, or the shared article's slot) × a kind factor (memes 1.6, replies 0.25) × log-normal noise; reposts, reply counts and views are ratios of likes. Stored on the row, so they never change between loads.
  - **Trends (`refresh_trends`):** after every weekly run and team wakeup, the week's teams, players, games and hashtags are scored by summed engagement (likes + 2 × reposts + 3 × replies) with a recency weight, weighted by kind (players 1.3, games 1.2, teams 0.8, hashtags 0.5), and need a minimum number of posts (games 3, others 2). Generic hashtags (#NFL, #Week17 and the like) never trend. The top 20 replace the week's `social_trends` rows; titles are templates ("Bills–Chiefs", the player's name, "#BillsMafia") and the description is the top post's text.
  - **Settings:** `newsroom/config/social_desk.json` holds the model (and per-kind overrides), workers, follower ranges, like rates, kind factors, share-slot heat and the trend weights and stoplist. `db/clear_news_feed.sh` also backs up and clears `social_trends` when it deletes posts.
- **Tests:** `tests/test_social_api.py` (post shape, labels, For You order), `tests/test_social_publish.py` (calendar, post windows, followers, engagement, publishable posts, trends, replies, team wakeups, pundit pool vs the dispatcher), `tests/test_social_writer.py` (cleanup, prompts, old-team slant, cost) and `tests/test_social_desk.py` (synthetic league: 100 posts, one die-hard reaction per team and no one else reacting, the player-first mix, bad games, comparisons, ex-players not filed under their old team, name tidying, buzz tiers and their extra posts, common milestones not being news, a record broken, team storylines (perfect and winless runs, a first loss after a perfect start, team wakeups), no storylines before games are played, share accounts, no score leaks, caps, fantasy weeks, determinism, preseason, postseason and the Super Bowl bye).

