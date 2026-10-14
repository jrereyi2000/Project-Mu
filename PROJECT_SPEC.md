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
├── extract.py                 # Gemini vision pass (gemini-3.8-flash, JSON schema) over a captures/ run → stats_<category>.csv (wide tables: left/right pages paired by number, rows joined by position since NAME/POS scroll away; shared columns cross-checked), team_conversions/team_redzoneefficiency/team_penalties.csv, schedule.csv, season_schedule.csv (all 18 weeks, same columns); games runs → games/<NN>/box_score.csv (LABEL, away, home) and stats_<category>.csv, plus week-level game_scores.csv (quarters + totals) and game_stats_<category>.csv (GAME column; wide pages read through each page's own headers, cut-off edge columns mapped to their full name, all-empty columns dropped); roster runs → roster_list.csv (TEAM + list columns from the list pages; CONV/DEV icons blank; a page with fewer than 14 rows is re-read; rows de-duplicated on NAME+POS, differing repeats printed as CONFLICT; per-team counts compared with cards.csv) and roster_cards.csv (cards.csv with AGE/HT/WT/EXP re-read by Gemini from the card crops, 20 numbered strips per request, since Apple Vision drops the digit 1 and misreads heights; vision_* columns keep Vision's values, vision_disagrees names the fields that differ) (GEMINI_API_KEY, env or root .env)
├── README.md                  # Setup & weekly usage for madden_capture.py / extract.py (permissions, App Nap, safety rules)
├── captures/                  # madden_capture.py output (screenshots, scope_debug/ + week_debug/ L2-dropdown frames, stop_debug/ frame that caused a stop, extracted CSVs; roster/ from --only roster; team_debug/ team-dropdown frames; <date>_explore/ holds --step mapping frames); gitignored
├── server/                    # FastAPI backend
│   └── api.py                 #   REST API + /static mount (serves ../static)
├── pipeline/                  # Video ingestion & OCR extraction (Section 6, step 1)
│   ├── main.py                #   End-to-end pipeline entrypoint (--file / --stream, --db, --workers)
│   ├── ocr_extractor.py       #   Gemini box-score extraction + Pydantic stat models
│   ├── template_classifier.py #   Screen-state classifier (TEMPLATE_DIR = pipeline/templates/)
│   ├── templates/             #   Reference screenshots for each Madden UI state
│   ├── ingest_stream.py, sort_video.py, batch_historical.py, batch_menus.py
│   └── roster_scanner.py, schedule_scanner.py, heal_roster.py
├── engines/                   # Deterministic analytics (Sections 2 & 6, steps 2–3)
│   ├── game_stat_generator.py #   CPU-sim backfill → culture engines (no swarm; see newsroom/weekly_run.py)
│   ├── backfill_team_stats.py
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
│   ├── init_db.sql, run_sql.py
│   ├── backup_db.sh, restore_db.sh   # pg_dump / restore via the madden-db Docker container
│   ├── clear_news_feed.sh     #   Back up then delete generated news_feed content (--articles-only keeps tweets)
│   ├── migrations/            #   migrate_*.py, 2026_10_12_fantasy_points_generated.sql, 2026_10_13_projected_excitement.sql, 2026_10_14_opp_adj_credit.sql,
│   │                          #   2026_10_15_stored_index_components.sql, 2026_10_16_trajectory_and_efficiency.sql,
│   │                          #   2026_10_17_playoff_clout_awards_exposure.sql, 2026_10_18_power_rankings.sql,
│   │                          #   2026_10_19_roundtable_panel_agent.sql, 2026_10_20_agent_memory.sql,
│   │                          #   2026_10_21_fantasy_league_sim.sql, 2026_10_22_agent_wire_social.sql (apply SQL ones via
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
│   ├── vision_dump.py         #   Apple Vision OCR of a capture folder or frame folder → vision_ocr.json (read-only; used for the Oct 13 OCR tests)
│   └── vision_cells.py        #   Per-cell Apple Vision OCR experiment from cell_plan.json → vision_cells.json (read-only)
├── static/players/            # Served headshots ({player_id}.webp) at /static/players/
├── docs/
│   ├── EDITORIAL_MATRIX.md
│   ├── FANTASY_EDITORIAL_MATRIX.md # Agreed fantasy slate: fantasy calendar (W1 positional draft tiers, W2–14 season, W15–17 playoffs, W18 review, W19–23 frozen), 100 simulated leagues (strategy drafts → ADP, rostered/start %, 6-team playoffs), K/DST after migration phase 3; built: league sim, writer tools, fantasy desk, API and page
│   ├── SOCIAL_MATRIX.md       #   Agreed Agent Wire plan (steps 1–5 built: accounts, schema, planner, writer, publishing): social desk planner (~100 posts/week: reactions, performances, beat notes, standings, article shares, replies, previews, memes), computed engagement, tag-based trends, simulated post times, accounts/handles, schema/API, build steps; not built, Agent Wire still renders mock posts
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
All Python commands run **from the project root** (relative data paths such as `data/...` and `static/...` resolve against it). Configuration comes from the environment: `DATABASE_URL` (defaults to the local `madden` database) and `GEMINI_API_KEY` (the swarm also reads a root-level `.env`).

| Task | Command |
|---|---|
| API server | `python3 -m uvicorn server.api:app --host 0.0.0.0 --port 8000 --reload` |
| Frontend | `cd Mu_Network && npm run dev` (Vite on port 8443) |
| Video pipeline | `python3 -m pipeline.main --file data/video/<clip>.mp4 --db` |
| Stat backfill + culture engines | `python3 -m engines.game_stat_generator` |
| Publish the weekly newsroom slate | `python3 -m newsroom.weekly_run` (next week; skips if already published). `--week N` for a specific week (Week 1 preseason and Week 23 Super Bowl are manual), `--force` to publish again, `--dry-run` to print prompts with no Gemini calls or writes. The Mu Fantasy slate runs in the same call after the national slate (W1–18, own already-published check); `--fantasy-only` / `--skip-fantasy` |
| Clear generated articles/tweets | `bash db/clear_news_feed.sh` (backs up `news_feed`, tags, `power_rankings` and the agent-memory tables to `db/backups/` first, asks to confirm; deleting articles cascades to their takes and predictions, and agent season summaries are cleared; `--articles-only` keeps tweets) |
| Re-score Excitement Index (E) | `python3 -m engines.gotw_engine --season 2035` (then refresh the culture indices below) |
| Refresh culture indices (in this order) | `python3 -m engines.twi_engine` → `python3 -m engines.popularity_engine` → `python3 -m engines.player_popularity_engine` |
| Opponent-Adjusted Game Credit + Trajectory | `python3 -m engines.game_credit_engine --season 2035` (independent of the culture indices) |
| Team efficiency profile | `python3 -m engines.team_efficiency_engine --season 2035` |
| Playoff Clout | `python3 -m engines.playoff_clout_engine --season 2035` |
| Project E for scheduled games | `python3 -m engines.excitement_projection --season 2035` (`--backtest` scores accuracy, no writes) |
| Preseason ADP (runs the 100 simulated drafts once per season) | `python3 -m engines.fantasy_adp_engine --season 2035` (`--redraft` replaces a stored draft) |
| Simulated fantasy leagues (rostered %, start %, standings) | `python3 -m engines.fantasy_league_sim --season 2035` (re-simulates from the stored draft through the next unplayed fantasy week; `--summary` prints results, no writes) |
| Weekly fantasy projections | `python3 -m engines.fantasy_projection_engine --season 2035 --week 18` (no `--week`: league_state current week; `--all-weeks` backfills 2–18; `--backtest` scores accuracy, no writes) |
| Capture Season Stats, Team Stats (conversions, red zone, penalties) + last week's schedule over Remote Play | `python3 madden_capture.py` (chiaki-ng streaming, anywhere in franchise menus). `--only stats\|team\|schedule\|roster\|season-schedule\|games` (`--games N` stops after N games for testing) (roster, season-schedule and games are not in the default run yet; season-schedule = League Schedule weeks 1-18 → season_schedule/season_week_NN_NNN.png; `--teams N` stops after N teams for testing; `--resume <run>` continues a stopped roster run in captures/<run>, skipping the teams in roster/done.txt), `--week "SEASON WEEK 12"`, `--test` (read screen only), `--scope-only`, `--probe-l2` (records the held-L2 dropdown from Player Stats or the Manage Roster list), `--all-rows`, `--foreground`. Mapping tool: `--step <key>\|none` presses one key and saves the screenshot, OCR text (with boxes and brightness) and `classify()` result to `captures/<date>_explore/explore/NN_<key>.png/.txt`; `--wait` sets the settle time; Cross needs `--allow-cross` and is always refused on a screen showing ADVANCE WEEK or FORCE WIN |
| Screenshots → CSVs | `python3 extract.py captures/<run>` (Gemini, 8 parallel requests on table-cropped half-size images; `GEMINI_API_KEY` from env or root `.env`; `MADDEN_EXTRACT_MODEL` / `MADDEN_EXTRACT_WORKERS` override model and parallelism) |
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
  - `player_stats` stores only abbreviated names (e.g. `A. Wright`), which collide across players. Each stat line is matched to the player with that name **on the same team in the game's season**; position breaks the rare same-team tie. Lines with no such player are skipped. Earlier scoring joined on name alone, which counted colliding players' milestone bonuses more than once and could use another player's OVR (`fetch_game_player_rows` in `engines/gotw_engine.py`).
- **$U$ (Upset & Narrative Leverage)**: Pre-game talent differential (`team_seasons.team_ovr` for the game's season) and heavyweight clashes.
- Computed by `engines/gotw_engine.py` and stored in `games.excitement_index`. A game's $E$ depends only on that game's data, so once scored it is **not** re-scored automatically (re-scoring would let it drift as player/team ratings change during the season, which would in turn shift TWI and popularity).
  - **Pipeline (Section 6, step 3):** `batch_evaluate(season, only_missing=True, game_ids=<games just backfilled>)` scores only completed games with no $E$ yet plus the games whose box scores were inserted in that run.
  - **Manual re-scoring** (e.g. after a box-score correction): `python3 -m engines.gotw_engine --game-ids 805 832`; `--season 2035 --missing-only` fills gaps; no flags re-scores every completed game in all seasons (`--season` limits it to one).
- **Projected Excitement (scheduled games, `engines/excitement_projection.py`)**: the expected value of each component before kickoff, on the same raw scale as $E$, stored in `games.projected_excitement` (migration `db/migrations/2026_10_13_projected_excitement.sql`) and refreshed for every scheduled game by the pipeline (§6):
  - $C$: $\mathbb{E}[\max(0, 100 - 4|m|)]$ with margin $m \sim N(\mu, \sigma)$, $\mu = k \cdot (\text{OVR}_\text{home} - \text{OVR}_\text{away})$. The slope $k$ (≈2.3 points per OVR) and the residual spread $\sigma$ are fitted from the season's completed games.
  - $P$: the league-average game. Team scoring and yardage barely persist week to week in this league, so team-specific $P$ only added noise.
  - $S$: the teams' average top-3 OVR and milestone-outlier points per game, shrunk toward the league average (4 pseudo-games).
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

---

## 3. Database Schema & Architecture (PostgreSQL)

The database utilizes a relational temporal model in PostgreSQL (`dbname=madden`). `db/init_db.sql` reproduces the full live schema (tables, constraints, generated columns, views, functions, triggers) on an empty database and is safe to re-run; it does not add columns to existing tables, so schema changes to the live DB ship as migrations under `db/migrations/` and must also be folded into `init_db.sql`.

### 3.1 Core Entity & State Tables
- **`teams`**:
  - `id` (PK, int), `name` (varchar), `city` (varchar), `abbreviation` (varchar, e.g. 'CAR', 'TB', 'KC'), `conference` (varchar), `division` (varchar), `team_color` (varchar hex), `stadium_name` (varchar). `UNIQUE(name)`.
- **`players`**:
  - `id` (PK, int), `full_name` (varchar), `player_name` (varchar, e.g. 'G. Crawford'), `player_number` (int - jersey number, default 0), `height` (varchar), `weight` (int lbs), `espn_id` (int, nullable).
- **`league_state`**:
  - `id` (PK, int, default 1 — single row), `current_season` (int), `current_week` (int). Column defaults are 2024 / 1; the live league row is currently season 2035, week 18.

### 3.2 Temporal Snapshot Ledgers
- **`team_seasons`**:
  - `id` (PK), `team_id` (FK), `season` (int), `wins` (int), `losses` (int), `team_ovr` (int), `off_ovr` (int), `def_ovr` (int), `pass_off_rank` (int), `rush_off_rank` (int), `pass_rush_rank` (int), `coverage_rank` (int), `watchability_index` (float8), `popularity_index` (float8), `opp_adj_credit`, `opp_adj_credit_recent`, `sos_rating`, `expected_wins` (float8, nullable; see 2.4, added by `db/migrations/2026_10_14_opp_adj_credit.sql`), `watchability_sigma_top`, `p_draft`, `y_prod`, `purgatory_penalty` (float8, nullable; TWI components, see 2.2, added by `db/migrations/2026_10_15_stored_index_components.sql`), `trajectory_multiplier` (2.4), `pythagorean_wins`, `pace_production` (2.6) (float8, nullable; added by `db/migrations/2026_10_16_trajectory_and_efficiency.sql`), `s_playoff` (2.7; added by `db/migrations/2026_10_17_playoff_clout_awards_exposure.sql`). `UNIQUE(team_id, season)`; `wins`/`losses` are maintained by `trigger_update_team_records` (see 3.6).
- **`player_seasons`**:
  - `id` (PK), `player_id` (FK), `team_id` (FK), `season` (int), `position` (varchar), `age` (int), `overall_rating` (int), `years_of_experience` (int; 0 = rookie, 1 = second-year), `popularity_index` (float8), `spike_index`, `r_rating`, `f_platform`, `t_success`, `a_awards`, `e_exposure` (float8, nullable; player Pop components, see 2.5), `s_playoff` (float8, nullable; 2.7). `UNIQUE(player_id, season)`.

### 3.3 Games & Statistics Tables
- **`games`**:
  - `id` (PK), `season` (int), `week` (int), `home_team_id` (FK), `away_team_id` (FK), `home_score` (int), `away_score` (int), `status` (varchar: 'COMPLETED' | 'SCHEDULED' | 'BYE'), `game_time` (varchar, e.g. 'Sun 1:00 PM'), `game_type` (varchar, default 'REGULAR_SEASON'), `excitement_index` (float8), `projected_excitement` (float8, nullable; pre-game projected $E$), `completed_at` (timestamp). `UNIQUE(season, week, home_team_id, away_team_id)` (constraint `games_season_week_home_away_key`).
- **`player_stats`**:
  - `id` (PK), `game_id` (FK), `team_id` (FK), `player_name` (varchar), `position` (varchar), `completions`, `pass_attempts`, `pass_yards`, `pass_tds`, `interceptions`, `rush_attempts`, `rush_yards`, `rush_tds`, `receptions`, `rec_yards`, `rec_tds`, `fumb`, `fantasy_points` (numeric(6,2), **generated column**). `UNIQUE(game_id, player_name, position)`.
  - `fantasy_points` is a PostgreSQL `GENERATED ALWAYS ... STORED` column computed from the row's box score with full-PPR scoring: 0.04/pass yd (1 per 25), 4/pass TD, −2/INT, 0.1/rush & rec yd (1 per 10), 6/rush & rec TD, 1/reception, −2/fumble; NULL stats count as 0. It recomputes automatically on every insert or stat correction and is read-only: ingestion and backfill code must **not** insert or update it (Postgres rejects writes). All fantasy surfaces (`vw_fantasy_scores`, `vw_fantasy_defense_vs_position`, game logs, rankings, player popularity) read this column.
  - Introduced by `db/migrations/2026_10_12_fantasy_points_generated.sql` (self-verifying; rolls back on any value change). Restoring a database dump taken before 2026-10-12 brings back the old plain column; re-apply the migration afterwards. Fresh databases built from `db/init_db.sql` already have the generated column.
- **`team_stats`**:
  - `id` (PK), `game_id` (FK), `team_name` (varchar), `score`, `first_downs`, `total_offense`, `total_plays`, `rushing_attempts`, `rushing_yards`, `rushing_tds`, `passing_completions`, `passing_attempts`, `passing_yards`, `passing_tds`, `third_down_attempts`, `third_down_conversions`, `fourth_down_attempts`, `fourth_down_conversions`, `turnovers` (all int). `UNIQUE(game_id, team_name)`.
- **`season_stats`**:
  - `id` (PK), `season` (int), `player_id` (FK), `player_name` (varchar), `team_id` (FK), `position` (varchar), the same box-score counters as `player_stats`, `total_fantasy_points` (numeric(7,2)), `ppg_fantasy_points` (numeric(5,2)). `UNIQUE(season, player_name, team_id, position)`.
- **`awards`**:
  - `id` (PK), `season` (int), `award_type` (varchar), `player_id` (FK), `player_name` (varchar), `team_id` (FK), `coach_name` (varchar), `position` (varchar). `UNIQUE(season, award_type)`.

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
  - `db/migrations/2026_10_22_agent_wire_social.sql` gave every agent that posts a non-generic handle, display name and bio (approved by JR), renamed beat reporters whose templated surnames repeated (43) and same-team die-hards / bandwagon fans who shared a first name, and added the social accounts `The_Flex_Spot` (`FANTASY_GURU`, @TheFlexSpot), `Gridiron_Memes` (`NFL_MEMES`, @GridironMemes) and `Mock_Draft_Marty` (`DRAFT_SICKO`, @MockDraftMarty). `Skip_Bayless_National` (article byline) and `Skip_Bayless` (`SKIP_TWEET`) share @RealSkipBayless; Agent Wire shows them as one profile.
- **`agent_memory_entries`** (§7.7): `id`, `agent_id`, `season`, `week`, `article_id` (FK `news_feed` ON DELETE CASCADE), `kind` ('TAKE'), `text`, `team_id`, `player_id`, `created_at`.
- **`agent_predictions`** (§7.7): `id`, `agent_id`, `season`, `week` (the predicted game's week), `article_id` (FK `news_feed` ON DELETE CASCADE), `game_id`, `label` (roundtable panellist or ''), `team_a_id`, `score_a`, `team_b_id`, `score_b`, `created_at`; unique `(article_id, game_id, label)`.
- **`agent_season_summaries`** (§7.7): `agent_id`, `season`, `through_week`, `summary`, `updated_at`; primary key `(agent_id, season, through_week)`.
- **`media_outputs`**:
  - `id`, `game_id` (FK to `games(id)` ON DELETE CASCADE), `content_type`, `payload_json` (jsonb), `created_at`.
- **`news_feed`**:
  - `id`, `season`, `week`, `team_id`, `content_type` ('ARTICLE' | 'TWEET'), `persona`, `article_type`, `slot`, `headline`, `excerpt`, `content`, `created_at`.
  - Agent Wire post columns (`db/migrations/2026_10_22_agent_wire_social.sql`; filled by the social desk, `docs/SOCIAL_MATRIX.md`, not built yet): `agent_id` (author), `post_kind` (REACTION, PERFORMANCE, BEAT, STANDINGS, SHARE, REPLY, PREVIEW, MEME), `posted_at` (simulated in-world time; `created_at` stays the real generation time), `reply_to_id` (FK `news_feed` ON DELETE CASCADE), `linked_article_id` (FK `news_feed` ON DELETE SET NULL; the article a SHARE links), `subject_game_id`, `subject_player_id`, `likes`, `reposts`, `reply_count`, `views`, `hashtags` (text[]).
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
- `GET /api/team/{abbr}`: Team metadata, overall/offensive/defensive ratings, division rank, and team color.
- `GET /api/team/{abbr}/schedule?season={season}`: Full 18-week schedule with completed scores and upcoming dates.
- `GET /api/team/{abbr}/news?season={season}&limit={limit}`: Returns news articles and beat coverage specifically tagged for or assigned to a franchise (`article_teams` join or `news_feed.team_id`), prioritized by `mention_type` (`PRIMARY` > `FEATURED` > `MENTIONED`) and week recency. If the team's local beat hasn't been woken for the current week (no article filed under the team with a `Local Beat N` slot that week; national primers use `Slot 6–8` and don't count), it starts the on-demand local swarm wakeup (`SwarmDispatcher.dispatch_team_local`, `live=True`, `save_to_db=True`) in a background thread via `start_local_wakeup` and returns the existing feed immediately; the new beat reports and fan tweets appear on a later load. Each (team, season, week) is attempted at most once every `LOCAL_WAKEUP_RETRY_SECONDS` (600s), which covers a run in progress and keeps repeat views from re-triggering a failed one. `NEWSROOM_AUTO_LOCAL_WAKEUP=0` disables it.
- `GET /api/team/{id}/roster/{season}`: Complete active roster with jersey number (`player_number`), ratings, experience, height, and weight.
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
- `GET /api/player_stats?season={season}&category={category}`:
  - Passing, rushing, receiving, and defensive player leaderboards.

### 4.4 Fantasy Analytics Endpoints
- `GET /api/fantasy/dashboard?season={season}&week={week}`:
  - **Articles:** `hero` and `articles` (the 3 front-page cards) come from the stored Mu Fantasy slate (`news_feed`, `article_type` `FANTASY_*`) for the week shown: the requested week, weeks after 18 show Week 18, and a week without a slate shows the latest earlier one (`articles_week`). Each card: `id`, `slot`, `label` (article type name), `title`, `excerpt`, `author` (agent display name, else `fantasy_writers.json`), `time` (~230 words/min), `updated`, `team` (most-tagged team, lower-case, or null). `more_analysis_count` = shelf articles behind "View all". With no stored slate, `articles_source` is `placeholder` and the old placeholder hero/cards are returned.
  - Top 3 marquee games (`excitement_games`). These are ranked by editorial interest: raw (projected) $E$, +3 for a division game, and +2 per team not yet mathematically eliminated (regular season). This is the same `rank_games_by_interest` used for matchup-primer picks. Each game has `score` (display scale), `projected`, and a `note` such as "Week 18 · Projected · Division game · Playoff race".
  - Waiver wire trends, weekly projections, preseason ADP movers, and rest-of-season rank snapshots. The ADP card reads only week 1 rows with a `player_id` (later weeks carry a copy of `adp_rank`).
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
- `#social`, `#social-explore`, `#social-teams`: Agent Wire social feeds.

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
4. **Social NFL Container (`SocialNflWidget`)**:
   - Embedded in the Agent Wire right rail.
   - Replaced generic CSS text shield with the official ESPN NFL shield logo.
   - Displays the top 3 most exciting matchups of the active week ranked by Excitement Index ($E$).
5. **Team Hub Beat Coverage & Article Feed (`TeamPage`)**:
   - Queries `GET /api/team/{abbr}/news?season={season}` to populate the team spotlight hero card and 4-story beat feed with real articles tagged for the franchise (`article_teams`), with seamless fallback to positional template beats.
6. **Live Newsroom Front Page (`App.tsx`)**:
   - Fetches `GET /api/news/week/{week}` to bind the Hero Slot story, secondary analysis cards, feature trios, and trending local coverage primers directly to autonomous newsroom outputs. The sidebar Power Rankings card shows the top 3 from `GET /api/power_rankings` (latest stored week), or a placeholder when none is stored.
7. **Team & League Logos**:
   - Pulls high-resolution official SVG/PNG logos from ESPN's global CDN (`https://a.espncdn.com/i/teamlogos/nfl/500/${abbr}.png`), normalized across all 32 franchises.

## 6. The Automated Pipeline Workflow (End-to-End)
The entire intelligence apparatus is triggered autonomously via an optimized, concurrent pipeline designed to ingest streaming video and output localized AI content in real time.

1. **Ingestion (Live Stream & Worker Pool Concurrency):**
   - `pipeline/ocr_extractor.py` and `TemplateClassifier` monitor the Madden video stream at 1 FPS.
   - Dynamic batch jobs and single-frame stats are queued to an asynchronous **`ApiWorkerPool`** (default 3 concurrent workers, scalable via `--workers`).
   - Multiple extraction jobs run simultaneously against Gemini (`gemini-3.8-flash`), with thread-safe synchronization of game IDs, session state, and real-time database UPSERTs.
   - When the user scrolls Post-Game stats, box scores are instantly UPSERTED into `player_stats`.
   - When the user scrolls the Weekly Schedule, it logs final CPU-simmed scores and win/loss records.
   - When the user scrolls Season Stats, it overwrites the `season_stats` ledger.
2. **Mathematical Backfill:**
   - `engines/game_stat_generator.py` is invoked. It subtracts the real `player_stats` from the `season_stats` and distributes the remaining stats into the missing CPU-simmed games, so season totals match `season_stats`. Each player's remainder is split across those games in proportion to each game's combined score (weight $\max(1, \text{total points}/40)$), so per-game box scores for CPU-simmed games are synthetic: only season totals and final scores are real (see the caveat in 2.6).
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
  - *Joyner:* schematic complexity and play-level efficiency: extreme tempo, boom-or-bust performances, tactical mismatches.
  - *Rosenthal:* entertainment and vibes: high-tempo, high-excitement chaos and electric young quarterbacks.
  - *Chadiha:* the human element and historical stakes: aging stars, adversity, historic franchises under pressure.
  - *Yates:* statistical anomalies, milestones and fantasy volume that support factual lists.
  - *Simmons:* the "wonkiest" game: upsets, bad beats, historic franchises over- or under-achieving the eye test.
  - *Acho:* exposed flaws and public overreactions: popular favorites that lost, high-tempo offenses shut down.
  - *The Athletic:* process, scheme and roster trajectory; young-core surges and schedule-adjusted winners; avoids garbage-time inflation.
  - *Skip Bayless:* superstar drama and the "clutch gene": prime-time chokes or game-winning drives by marquee players.
  - *Kimes:* games where efficiency tells a different story than the score, and smart defensive schemes.
  - *Orlovsky:* the most extreme quarterback performances only, good or bad.
  - *Clark:* locker-room dynamics, player sacrifice and leadership under pressure.
- **Historical Comparisons (optional, 1990–2023):** National writers may compare a team or player to a real NFL team or season from 1990–2023 when the parallel genuinely sharpens the argument. It isn't required; the prompt says most articles need none, allows at most one, forbids inventing a historical team, and gives no canned examples (the old ones, e.g. the 2000 Ravens, got reused everywhere). Beat reporters don't receive this instruction.
- **Invented detail vs. data:** Scores, records, stat lines and rankings must come from the brief or the tools. Writers may invent what happens inside games (specific plays, drives, game script) consistent with the final score and box score, and anonymous sources such as Sando's "NFC executive" quotes. Injuries, betting lines and contract details are never used: the league has no data for them, and the prompts either don't ask for them or forbid them. Madden ratings are never cited: writers may use OVR and team ratings from their tools to judge talent, but the shared `natural_language_rule` block (appended to every article and tweet prompt) forbids rating numbers, "OVR"/"overall" wording and any mention of Madden or the video game in published text.
- **Database Context & Research Tools:** Agents have autonomous access to query live simulation context via ReAct AFC tools:
  - `get_weekly_slate`: Returns the complete scoreboard for all games in a given week with final scores, victory margins, excitement indices, and team IDs.
  - `get_season_standings`: Returns full NFL division standings with wins, losses, win %, points scored (PF), points allowed (PA), and point differential.
  - `get_top_popular_players`: Returns the top 20 most popular players across the league with Madden OVR, age, experience, and popularity indices.
  - `get_top_rated_players`: The highest-rated players by Madden OVR at every position (default 200, capped at 300; long snappers excluded), one compact line each with team, OVR, age, experience and season passing/rushing/receiving totals. This is the pool for league-wide player rankings such as the Top 100. Ratings run high (this season the 100th-best player is OVR 92 and 733 players are 80+), and the popularity list holds only 41 of the 108 highest-rated players, so it can't serve as the pool.
  - `get_all_team_popularities`: Returns all 32 teams ranked by popularity index, watchability rating, and overall roster quality.
  - `get_player_context` / `get_player_season_stats`: Resolves player by name or ID; returns age, years of experience, physical measurements (height/weight), career awards list, Madden OVR, cumulative season stats, stored popularity components (SpikeIndex, $R_{\text{rating}}$, $F_{\text{platform}}$, $T_{\text{success}}$, $A_{\text{awards}}$, $E_{\text{exposure}}$), player Playoff Clout, and season touch/yard share for RB/FB/WR/TE.
  - `get_team_context`: Resolves team by name, abbreviation, or ID; returns win-loss record, watchability, popularity, Madden team ratings, and top 3 players.
  - `get_team_season_schedule`: Returns a team's complete season schedule and game log: week-by-week opponents, scores, win/loss outcomes, victory margins, one-score game breakdown, excitement ratings, and game IDs.
  - `get_game_box_score`: Returns team box score and top individual game contributions.
  - `get_advanced_team_analytics`: Reads the stored TWI components (σ_top, P_draft, Y_prod, Purgatory Penalty), TWI, franchise POP, team Playoff Clout, starter age (top 22 by OVR), and $B$ / $L$ / $D$ / $N$ / Legacy Floor from `market_constants`.
  - `get_advanced_game_analytics`: Completed games return pace/production ($P$ from `gotw_engine.production_score`), Upset Leverage ($U$), excitement, and both teams' Opponent-Adjusted Game Credit. Scheduled games return projected $U$ (`excitement_projection.expected_upset`) and the underdog's win probability.
  - `get_opponent_adjusted_credit`: Opponent-Adjusted Game Credit (Section 2.4). No team → all 32 ranked with credit, recent-form credit, Trajectory Multiplier and label, strength of schedule, raw point differential per game, expected wins and win luck; with a team → that summary plus a game-by-game credit log.
  - `get_matchup_edges`: Section 2.8. Pass, rush and ball-security edges plus Trench Edge for both offenses in a matchup; with no opponent, uses the team's next scheduled game.
  - `get_team_efficiency_profile`: Section 2.6. Pythagorean wins and pyth luck, pace & production, pass/rush yards and giveaways per game; all 32 sorted by pyth luck, or one team.
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
   - **Focus:** Point-of-attack wins, run-pass splits, specific receiver coverage isolations, quarterback processing mechanics.
   - **Data Queried:** Advanced Game Analytics (Offensive Pace, Single-Game Excitement), Game Box Scores, Touch Share & Scrimmage Yard Share (season).
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
    - **Data Queried:** Player Popularity Index ($Pop_{\text{player}}$), Awards Equity ($A_{\text{awards}}$), Baseline Rating ($R_{\text{rating}}$), SpikeIndex, Touch Share & Scrimmage Yard Share (season), Offensive Pace ($P$).
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
    - **Curatorial Autonomy:** Selects the ultimate non-QB X-factor for the Super Bowl matchup: boom-or-bust deep threat with high $SpikeIndex$, situational pass rusher creating upset leverage, or dynamic rookie with spiked postseason usage.
    - **Structure Directive:** 4-part Predictive X-Factor Breakdown (`# 1. The Game-Breaker Thesis (The Hook)`, `## 2. The Anatomy of the Spike (The Volatility Audit)`, `## 3. The Schematic Mismatch (The Film Room)`, `### 4. The Script Prediction`).
    - **Prompt Hook:** Identify the ultimate Super Bowl X-Factor. Analyze a volatile skill player with a massive SpikeIndex, break down the specific schematic mismatch they can exploit, and predict how they could break the game script.

19. **The Official AP All-Pro Roster** (`OFFICIAL_AP_ALL_PRO_ROSTER`)
    - **Allowed Writers / Archetypes:** Field Yates (Definitive Roster & Stats), Bill Barnwell (Efficiency & Positional Value) (`YATES`, `BARNWELL`, `Year-End Hardware`).
    - **Granularity:** Season-Level / Year-End Roster.
    - **Focus:** The definitive First and Second-Team AP All-Pro selections, statistical justifications, positional dominance, and voting anomalies.
    - **Data Queried:** Awards Equity ($A_{\text{awards}}$), Baseline Rating ($R_{\text{rating}}$), SpikeIndex, Player Popularity Index ($Pop_{\text{player}}$), Empty Calories Suppressor.
    - **Curatorial Autonomy:** Constructs both the complete First Team and Second Team. Weighs full-season efficiency, raw volume, and high-leverage execution. Aggressively applies the "Empty Calories" suppressor to eliminate players racking up stats on sub-.300 teams; focuses on milestone volume (Yates) or down-to-down efficiency and defensive scheme inflation (Barnwell).
    - **Structure Directive:** 5-part Roster Reveal (`# 1. The Voting Overview (The Hook)`, `## 2. The First-Team Offense (11 slots with skill justifications)`, `## 3. The First-Team Defense & Special Teams (11 defense + 6 special teams slots)`, `## 4. The Second-Team Offense`, `## 5. The Second-Team Defense & Special Teams`).
    - **Prompt Hook:** Reveal the official AP All-Pro First and Second Teams. Construct both complete 29-player units across Offense, Defense, and Special Teams, provide statistical justifications for the top skill talent, and evaluate positional dominance without speculative snub commentary.

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
Local Beat Reporters cover their assigned franchise exclusively. Their research directive keeps them on their own team's games, and they don't receive the national writers' historical-comparison instruction. There is no injury data in the league, so the beat directive, the shared preview framing and the matchup preview templates tell writers not to mention injuries.

**Weekly portfolio (`newsroom/local_portfolio.py`):** every team wakeup plans the whole portfolio, however many beat reporters the team has: the Postgame Report Card if the team played last week, the Matchup Primer if it plays this week (aimed at that game via an Editorial Desk subject assignment), the Player Spotlight always, and the Season Progress Report from Week 6. Types the team already has that week (e.g. a national Slot 6–8 primer) are skipped. Articles are dealt round-robin across the team's reporters, starting at a different reporter each week, so one reporter writes all of them and no reporter is stuck on the postgame report. Each is saved under the team with slot `Local Beat N` (`tests/test_local_portfolio.py`).


1. **The Postgame Report Card** (`LOCAL_POSTGAME_REPORT_CARD`)
   - **Beat Archetype:** Local Beat Writer / Postgame Columnist (2–4 hours post-whistle).
   - **Focus:** Immediate unit execution versus expectations, unit letter grades (A+ through F), offensive line protection breakdowns, third-down stalls, and coaching decisions.
   - **Data Queried:** Unit Offensive Pace ($P$), Opponent-Adjusted Game Credit, Giveaways per Game (ball security), Pass Rush Rating (Madden rank).

2. **The Local Matchup Primer & Game-Plan Blueprint** (`LOCAL_MATCHUP_PRIMER`)
   - **Beat Archetype:** Senior Beat Reporter (Friday / Saturday morning preview).
   - **Focus:** Home-team perspective matchup reality, 3 hyper-specific micro battles, game-plan victory blueprint, and final score prediction.
   - **Data Queried:** Matchup Edge (pass, rush, ball security), Upset Leverage ($U_{\text{projected}}$), Trench Edge (O-line vs. pass rush ratings).

3. **The Local Player Spotlight** (`LOCAL_PLAYER_SPOTLIGHT`)
   - **Beat Archetype:** Franchise Beat Reporter / Film Analyst.
   - **Focus:** Single local player's role evolution, developmental leap, tape & assignment execution, coaching trust, and roster implications.
   - **Data Queried:** SpikeIndex, Touch Share & Scrimmage Yard Share (season), Positional Multiplier ($M_{\text{pos}}$), Blue-Chip Pedigree ($P_{\text{draft}}$), Under-25 Production Share ($Y_{\text{prod}}$).

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
     - **Curatorial Filter:** Isolates the single clash with highest tactical leverage, third-down aggression, or where fourth-down game management swung win probability.
     - **Structure:** 4-part Tape Autopsy (`# 1. The Decisive Blueprint`, `## 2. The Core Schematic Collision`, `## 3. The High-Leverage Breakdown`, `### 4. The Elimination Takeaway`).
   - **Week 23 (`SUPER_BOWL_SCHEMATIC_MASTERCLASS` | `PROSPECTIVE`):**
     - **Focus:** Exhaustive pre-game tactical blueprint previewing the Super Bowl clash through an X's and O's coaching lens (disregarding celebrity/media week storylines).
     - **Curatorial Filter:** Trench warfare leverage (pass rush vs. slide protection), safety disguise rotations against primary passing concepts.
     - **Structure:** 5-part Masterclass (`# 1. The Grand Chess Match`, `## 2. Trench Warfare & Point-of-Attack Leverage`, `## 3. Coverage Shells vs. Passing Architecture`, `## 4. The Critical Situational Battle`, `### 5. The Coordinator Verdict`).

2. **`SLATE_LOOKAHEAD_PREDICTIONS` Playoff Overrides:**
   - **Weeks 19–21 (`POSTSEASON_ROUND_LOOKAHEAD` | `PROSPECTIVE`):**
     - **Focus:** High-stakes single-elimination preview capsules for every game in the upcoming playoff round (Wild Card, Divisional, Conference Championship). Strict ban on betting lines/spreads.
     - **Curatorial Filter:** Mandatory full enumeration covering every scheduled tournament matchup.
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

**Status:** build steps 3–5 are built: planning, the post writer (JR chose Gemini 3.8 Flash for every post kind after the side-by-side test), and publishing from the weekly run and team wakeups. The API (step 6) and the Agent Wire frontend (step 7) are not built, so the page still shows mock posts.

- **What it does:** `load_week_context(cur, season, week)` reads one publication week; `plan_social_week(ctx)` returns about 100 assignments, each an account, a post kind, a subject and a **facts packet** (no model call, nothing written). Week N looks back at Week N-1's completed games and ahead to Week N's games; standings and season totals count only games before Week N, and a game in Week N or later never shows a score, so replanning an old week can't leak results.
- **Buckets:** shares (one per article stored for the week, from the writer's account; Skip's column shares from `SKIP_TWEET`, the roundtable panel never shares; local beat articles from one of that team's reporters), performances 12 (top full-PPR lines; offense only until the capture loads defense and kicking), beat notes 6 (teams in the most exciting games first), standings 4 (from Week 3 in the regular season: division races, 1-seed races, by record without tiebreakers), previews 8 (both fan bases on the top 3 projected games, plus 2 start/sit posts by the fantasy accounts in Weeks 2–17), memes 2 (@GridironMemes on the worst loss; @MockDraftMarty on the bottom five from Week 6), replies 20 (placeholders; parents are picked after writing). Reactions fill the rest: one die-hard post per team that played, then extras rotating pundit / bandwagon / winner's fan / loser's fan over the 5 most exciting games.
- **Postseason (Week 19+):** a few extra reactions, then the room goes to `PLAYER` posts by fans of eliminated teams (their own team's top players, alternating with players still alive). Alive teams are this week's scheduled teams, or last week's playoff winners in the Super Bowl bye week. **Preseason** (no completed games): every team's fans preview their opener, then pundits.
- **Accounts:** least-used first under weekly caps (pundit 3, fantasy 4, beat 2, fan 2, bandwagon 2), with position affinities for pundits (e.g. QB → RG3, Cowherd, Stephen A). The pundit pool mirrors `TWEET_EXCLUDED_ARCHETYPES` and leaves out the fantasy, meme and draft accounts, which the desk assigns on purpose. Deterministic for a season and week.
- **Dry run:** `python3 -m newsroom.social_desk --season 2035 --week 18 [--json plan.json]` prints the plan; when the week's slates aren't stored yet it plans placeholder shares, one per national and fantasy slot.
- **Writer (`newsroom/social_writer.py`, build step 4):** `build_post_prompt` puts the account's tweet persona (`agent_personas.json` `archetypes`), its identity line and memory block, and `SOCIAL_RULES` (one post of at most 280 characters, numbers only from the facts packet, no tools and no metric names even if the persona mentions them, no ratings or Madden, at most 2 hashtags) in the system instruction, and a per-kind task plus the facts packet in the prompt (replies also get the parent post). `write_post` makes one Gemini call with no tools and asks once for a shorter version if the post runs over 280 characters; `clean_post` strips labels, quotes, markdown and links, keeps 2 hashtags and flags problems (length, ratings). Facts packets describe a game's excitement in words ("an entertaining game") and never carry the index. Usage and cost come from `usage_metadata` (`PRICES`: 3.8 Flash $0.75/$3.75 per 1M until Dec 31, 2026; 3.5 Flash-Lite $0.30/$2.50).
- **Model comparison:** `python3 tools/social_model_compare.py [--week 9] [--models gemini-3.8-flash gemini-3.5-flash-lite]` writes the same 20 planned posts (6 reactions, 3 performances, 2 beat notes, 1 standings, 2 previews incl. a fantasy start/sit, 2 memes, 2 shares when the week has stored articles, and 2 replies from rival fans) with each model, prints cost per post, and saves posts, prompts and usage to `captures/social_compare/`. It reads the database and writes nothing to it.
- **Publishing (`newsroom/social_publish.py`, build step 5):**
  - `run_social_week(conn_info, season, week)` (called by `weekly_run` after the slates): skips a week that already has posts with slot `Agent Wire` (unless `force`), plans, writes the non-reply posts in parallel (5 workers), then plans and writes the reply pass, then refreshes trends. `dry_run` prints the plan only. Posts with an API error, no text, over 280 characters after the one rewrite, or a ratings/Madden mention are logged `[NOT SAVED]` and skipped.
  - `run_team_social(conn_info, season, week, team_id, local_articles)` (called by `dispatch_team_local` on live, saved wakeups): `plan_team_wakeup` gives each of the team's beat reporters a note, each die-hard a reaction to last week's game or a preview of this week's (alternating), 3 pundits if the team's popularity is above 70 or 1 above 55, and a share of each local article written in that wakeup by its reporter; agents who already posted in an earlier wakeup for the team and week are skipped. Saved with slot `Agent Wire Local`.
  - **Saving:** each post is a `news_feed` row (`content_type` 'TWEET', `persona` = archetype, `agent_id`, `post_kind`, `posted_at`, `reply_to_id`, `linked_article_id` for shares, `subject_game_id`, `subject_player_id`, `likes`, `reposts`, `reply_count`, `views`, `hashtags`). Teams are tagged by `EntityTaggingService` from the text plus both teams of the post's game; a player subject is tagged `PRIMARY`. Every post except a share is stored as the author's take (`agent_memory.store_tweet_take`).
  - **Replies (`social_desk.plan_replies`):** the 20 replies go to the 8 most-liked posts (3, 3, 3, 3, 2, 2, 2, 2). A fan's post gets the other team's fans first, then its own team's fans and bandwagon fans; a pundit's gets a set foil (`CONTRARIAN`, e.g. Shannon ↔ Skip, Stephen A ↔ McAfee), then fans of the teams involved; a meme gets the roasted team's fans; a beat note gets that team's fans. Replies count against the weekly caps and carry the parent's facts and text.
  - **Simulated time (`posted_at`):** Week 1's Thursday is the first Thursday after Labor Day of the season year, each week 7 days later; kickoffs come from `games.game_time` (default Sunday 1:00 PM) and games last 3h10m. Reactions land 5–90 minutes after their game ends, game memes 30 minutes–10 hours after, performances between the game's end and Monday night, beat notes, shares and draft posts Tuesday morning–Wednesday evening, standings Tuesday, postseason player posts Tuesday–Saturday, previews from Thursday (at most two days before kickoff) until an hour before kickoff, fantasy start/sit Friday–Sunday morning, replies 3–240 minutes after their parent. Seeded per post, so a rerun gives the same times.
  - **Engagement:** `followers` is set once per account (`agent_memories.followers`, seeded by `agent_id`): insiders (Schefter, Rapoport) 1–5M, other national voices 0.5–3M, The Flex Spot 250–600K, @GridironMemes 600K–1M, @MockDraftMarty 50–150K, beat reporters 20–150K and fans 200–25K scaled by their team's popularity, bandwagon fans 500–8K. Likes = followers × the role's like rate × subject heat (game excitement and team popularity, player popularity, or the shared article's slot) × a kind factor (memes 1.6, replies 0.25) × log-normal noise; reposts, reply counts and views are ratios of likes. Stored on the row, so they never change between loads.
  - **Trends (`refresh_trends`):** after every weekly run and team wakeup, the week's teams, players, games and hashtags are scored by summed engagement (likes + 2 × reposts + 3 × replies) with a recency weight, weighted by kind (players 1.3, games 1.2, teams 0.8, hashtags 0.5), and need a minimum number of posts (games 3, others 2). Generic hashtags (#NFL, #Week17 and the like) never trend. The top 20 replace the week's `social_trends` rows; titles are templates ("Bills–Chiefs", the player's name, "#BillsMafia") and the description is the top post's text.
  - **Settings:** `newsroom/config/social_desk.json` holds the model (and per-kind overrides), workers, follower ranges, like rates, kind factors, share-slot heat and the trend weights and stoplist. `db/clear_news_feed.sh` also backs up and clears `social_trends` when it deletes posts.
- **Tests:** `tests/test_social_publish.py` (calendar, post windows, followers, engagement, publishable posts, trends, replies, team wakeups, pundit pool vs the dispatcher), `tests/test_social_writer.py` (cleanup, prompts, cost) and `tests/test_social_desk.py` (synthetic league: 100 posts, one reaction per team, extras on the top game, share accounts, no score leaks, caps, fantasy weeks, determinism, preseason, postseason and the Super Bowl bye).

