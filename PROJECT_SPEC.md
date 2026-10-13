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
│   ├── game_stat_generator.py #   CPU-sim backfill → culture engines → swarm trigger
│   ├── backfill_team_stats.py
│   ├── gotw_engine.py         #   Game Excitement Index (E) → games.excitement_index
│   ├── twi_engine.py, popularity_engine.py, player_popularity_engine.py
│   └── fantasy_adp_engine.py  #   Preseason ADP board
├── newsroom/                  # Agent swarm (Section 7)
│   ├── swarm_dispatcher.py    #   Editorial desk, commissioning, parallel dispatch
│   ├── slot_targets.py        #   Editorial Matrix slot targeting (primer games, exits, eliminations)
│   ├── db_tools.py            #   ReAct research tools exposed to writer agents
│   └── config/                #   agent_personas.json, article_types.json, article_structure_directives.json,
│                              #   curatorial_autonomy_directives.json, playoff_overrides.json
├── db/                        # PostgreSQL access & maintenance
│   ├── db_handler.py          #   Shared connection / UPSERT layer (DATABASE_URL)
│   ├── init_db.sql, run_sql.py
│   ├── backup_db.sh, restore_db.sh   # pg_dump / restore via the madden-db Docker container
│   ├── migrations/            #   migrate_*.py, 2026_10_12_fantasy_points_generated.sql (apply SQL ones via
│   │                          #   docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < <file>)
│   └── backups/               #   madden_backup_*.sql dumps
├── headshots/                 # AI player headshot generation
│   ├── headshot_appearance_generator.py, make_headshot.py
│   ├── gen.py                 #   Gemini batch job submit / download
│   └── process_headshots.py   #   Background removal → static/players/{player_id}.webp
├── dashboards/                # Streamlit tools: admin_dashboard.py, espn_dashboard.py
├── tests/                     # test_*.py scripts
├── tools/debug/               # One-off frame/OCR debugging scripts
├── static/players/            # Served headshots ({player_id}.webp) at /static/players/
├── docs/
│   ├── EDITORIAL_MATRIX.md, FANTASY_FOOTBALL_SPEC.md, WAKEUPS.md
│   ├── models/                #   Index specifications (TWI, popularity, market gravity, legacy floor, GOTW, seasons played)
│   ├── writers/               #   Writer style references (Barnwell, Chadiha, Joyner, Rosenthal, Sando)
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
| Stat backfill + culture engines + swarm | `python3 -m engines.game_stat_generator` |
| Preseason ADP | `python3 -m engines.fantasy_adp_engine` |
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
- Stored permanently in `team_seasons.watchability_index`.

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
  - `id` (PK), `team_id` (FK), `season` (int), `wins` (int), `losses` (int), `team_ovr` (int), `off_ovr` (int), `def_ovr` (int), `pass_off_rank` (int), `rush_off_rank` (int), `pass_rush_rank` (int), `coverage_rank` (int), `watchability_index` (float8), `popularity_index` (float8). `UNIQUE(team_id, season)`; `wins`/`losses` are maintained by `trigger_update_team_records` (see 3.6).
- **`player_seasons`**:
  - `id` (PK), `player_id` (FK), `team_id` (FK), `season` (int), `position` (varchar), `age` (int), `overall_rating` (int), `years_of_experience` (int; 0 = rookie, 1 = second-year), `popularity_index` (float8). `UNIQUE(player_id, season)`.

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
  - `id`, `agent_id` (unique), `archetype`, `assigned_team_id`, `persistent_memory`, `handle`, `display_name`, `updated_at`.
- **`media_outputs`**:
  - `id`, `game_id` (FK to `games(id)` ON DELETE CASCADE), `content_type`, `payload_json` (jsonb), `created_at`.
- **`news_feed`**:
  - `id`, `season`, `week`, `team_id`, `content_type` ('ARTICLE' | 'TWEET'), `persona`, `article_type`, `slot`, `headline`, `excerpt`, `content`, `created_at`.
- **`article_teams`**:
  - `id` (PK, int), `article_id` (FK to `news_feed(id)` ON DELETE CASCADE), `team_id` (FK to `teams(id)` ON DELETE CASCADE), `mention_type` (varchar: 'PRIMARY' | 'FEATURED' | 'MENTIONED'), `mention_count` (int), `created_at` (timestamp), `UNIQUE(article_id, team_id)`.
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
- `GET /api/power_rankings`:
  - 32-team power rankings ordered by composite rating, win records, and movement trends.

### 4.2 Team & Roster Hubs
- `GET /api/team/{abbr}`: Team metadata, overall/offensive/defensive ratings, division rank, and team color.
- `GET /api/team/{abbr}/schedule?season={season}`: Full 18-week schedule with completed scores and upcoming dates.
- `GET /api/team/{abbr}/news?season={season}&limit={limit}`: Returns news articles and beat coverage specifically tagged for or assigned to a franchise (`article_teams` join or `news_feed.team_id`), prioritized by `mention_type` (`PRIMARY` > `FEATURED` > `MENTIONED`) and week recency. If the team's dedicated beat coverage has not yet been written for the current week (determined by checking for existing `LOCAL_POSTGAME_REPORT_CARD` articles, since matchup primers may be pre-commissioned nationally), it automatically triggers the parallel on-demand local swarm wakeup (`SwarmDispatcher.dispatch_team_local`) to generate localized beat reports and fan reaction tweets before returning the feed.
- `GET /api/team/{id}/roster/{season}`: Complete active roster with jersey number (`player_number`), ratings, experience, height, and weight.
- `GET /api/team/{id}/season_stats/{season}`: Aggregated season totals (passing yards, rushing yards, points per game, points allowed per game).

### 4.3 Player Analytics & Modal Overlays
- `GET /api/player/{id}?season={season}`:
  - Player personal information and ratings.
  - Full 18-week `game_log` (accounting for bye weeks and completed game fantasy statistics).
  - Multi-season `career_stats` with games played, yards, touchdowns, and overall fantasy rank.
  - Dynamic `weekly_outlook` containing upcoming opponent, home/away status, kickoff time, positional projection, and 1–5 defensive matchup stars.
- `GET /api/player_stats?season={season}&category={category}`:
  - Passing, rushing, receiving, and defensive player leaderboards.

### 4.4 Fantasy Analytics Endpoints
- `GET /api/fantasy/dashboard?season={season}&week={week}`:
  - Hero editorial take and top 3 marquee games (`excitement_games`). These are ranked by editorial interest: raw (projected) $E$, +3 for a division game, and +2 per team not yet mathematically eliminated (regular season). This is the same `rank_games_by_interest` used for matchup-primer picks. Each game has `score` (display scale), `projected`, and a `note` such as "Week 18 · Projected · Division game · Playoff race".
  - Waiver wire trends, weekly projections, preseason ADP movers, and rest-of-season rank snapshots.
- `GET /api/fantasy/rankings/weekly?season={season}&week={week}&position={pos}&limit={limit}`:
  - Weekly player projections or final results.
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
- `#standings` & `#rankings`: Division standings and power rankings.
- `#fantasy`: Mu Fantasy Analytics Command Center.
- `#fantasy-rankings`: Weekly Fantasy Rankings board.
- `#fantasy-ros`: Rest of Season Rankings board.
- `#fantasy-adp`: Preseason ADP Consensus Board.
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
   - Fetches `GET /api/news/week/{week}` to bind the Hero Slot story, secondary analysis cards, feature trios, trending local coverage primers, and power rankings directly to autonomous newsroom outputs.
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
   - `engines/game_stat_generator.py` is invoked. It subtracts the real `player_stats` from the `season_stats` and mathematically distributes the remaining stats into the missing CPU-simmed games, perfectly synchronizing the database.
3. **Culture Engines (Dependency-Ordered Execution & Batch Updates):**
   - Appended to the end of the backfill generator, `engines/game_stat_generator.py` first runs `gotw_engine.batch_evaluate(season, only_missing=True, game_ids=...)` to score the Game Excitement Index ($E$) for newly completed games (those with no `excitement_index`) and any games it just backfilled. Previously scored games are left unchanged. This must precede TWI, which only counts games with a non-null `excitement_index`.
   - It then runs `TWIEngine().backfill_twi()` followed by `PopularityEngine().calculate_popularity()`, sequentially in dependency order, because franchise popularity reads the `watchability_index` that TWI writes. These two used to run in parallel, so popularity could read stale or partially updated TWI.
   - Once franchise metrics are computed, `PlayerPopularityEngine().calculate_popularity()` executes vectorized batch updates using PostgreSQL `executemany` (reducing DB execution time from ~1s to ~0.16s).
   - Finally, `project_scheduled_games(season)` refreshes `games.projected_excitement` for every still-scheduled game before the assignment brief is compiled.
4. **The Swarm Trigger & Editorial Commissioning:**
   - The generator dynamically compiles a dual-horizon Weekly Slate Digest:
     - **`recap_slate`**: Completed scoreboard and standout individual performances from `week - 1` for retrospective recaps.
     - **`preview_slate`**: Upcoming scheduled matchups and projected excitement for `week` for prospective previews.
     - **`season_landscape`**: Cumulative division standings, records, and popularity/watchability leaders through `week - 1`.
   - It fires `SwarmDispatcher().dispatch_weekly_national(max_workers=5)`, which commissions the full 10-slot Editorial Matrix slate (`docs/EDITORIAL_MATRIX.md`) via the autonomous Editorial Assignment Desk. The optional `article_count` argument truncates the slate and is for tests/debugging only.
   - Local on-demand team wakeups (`POST /api/news/team/{team_id}/wakeup`) also utilize `ThreadPoolExecutor(max_workers=5)` to generate a balanced local beat portfolio (`LOCAL_POSTGAME_REPORT_CARD`, `LOCAL_MATCHUP_PRIMER`, `LOCAL_PLAYER_SPOTLIGHT`, and `LOCAL_SEASON_PROGRESS_REPORT`) alongside local fan/pundit reaction tweets concurrently in ~5–8s.
   - The Editorial Desk commissions a structured 10-article matrix (Hero, Slot 1, Slot 2, Slots 3–5 Feature Trio, Slots 6–8 Local Matchup Primers, and Standalone Power Rankings), matches each format to an eligible national writer or beat reporter, and dispatches them with custom curatorial directives and explicit temporal framing (`RETROSPECTIVE`, `PROSPECTIVE`, or `CUMULATIVE`) alongside viral fan/pundit reactions. Other local beat formats (`LOCAL_POSTGAME_REPORT_CARD`, `LOCAL_PLAYER_SPOTLIGHT`, `LOCAL_SEASON_PROGRESS_REPORT`) are not part of the weekly national run; they are produced only by on-demand team wakeups.
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
- **Curatorial Autonomy & Slate Digest (`newsroom/config/curatorial_autonomy_directives.json`):** Writers are fed the full 16-game scoreboard and standings context rather than a narrow pre-filtered Top 5 list. Each writer is governed by an explicit curatorial autonomy directive defining their slate selection criteria:
  - *Bill Barnwell:* Selects subjects based on regression principles, seeking out lucky one-score winners with negative point differentials or fraudulent records exposed by massive upsets.
  - *Mike Sando:* Ignores final scores/excitement; selects subjects based on front-office pressure, evaluating GM job security for aging rosters under the Purgatory Penalty or assessing coaching development of elite Blue-Chip Pedigree ($P_{\text{draft}}$).
  - *KC Joyner:* Ignores narrative/market size; selects subjects based on schematic complexity, extreme single-game offensive tempo, boom-or-bust SpikeIndex explosions, or tactical mismatches (e.g. young core exploiting aging coverage).
  - *Gregg Rosenthal:* Ignores playoff implications/legacy; selects subjects purely on aesthetic entertainment and vibes, prioritizing high-variance "Must-Watch Sandbox" shootouts, weird chaos, and electric rookie QBs.
  - *Jeffri Chadiha:* Ignores tempo and fantasy stats; selects subjects based on human emotion and historical stakes—aging superstars fighting closing windows, adversity redemption, or historic franchises facing massive market pressure.
- **Historical Analogy Mapping (1990–2023):** Writers must independently match the statistical profile of a simulation team to real-life historical NFL teams:
  - *Winning ugly with low Pace & high Win%:* Compare to 2000 Ravens, 2015 Broncos, 2011 Giants.
  - *Explosive Pace but losing shootouts:* Compare to 2018 Chiefs, 2020 Cowboys.
  - *Suffering from Purgatory Penalty:* Compare to late-era Drew Brees Saints or 2022 Colts.
  - All comparisons must be explicitly stated to validate the analytical argument without inventing historical franchises.
- **Database Context & Research Tools:** Agents have autonomous access to query live simulation context via ReAct AFC tools:
  - `get_weekly_slate`: Returns the complete scoreboard for all games in a given week with final scores, victory margins, excitement indices, and team IDs.
  - `get_season_standings`: Returns full NFL division standings with wins, losses, win %, points scored (PF), points allowed (PA), and point differential.
  - `get_top_popular_players`: Returns the top 20 most popular players across the league with Madden OVR, age, experience, and popularity indices.
  - `get_all_team_popularities`: Returns all 32 teams ranked by popularity index, watchability rating, and overall roster quality.
  - `get_player_context` / `get_player_season_stats`: Resolves player by name or ID; returns age, years of experience, physical measurements (height/weight), career awards list, Madden OVR, and cumulative season stats.
  - `get_team_context`: Resolves team by name, abbreviation, or ID; returns win-loss record, watchability, popularity, Madden team ratings, and top 3 players.
  - `get_team_season_schedule`: Returns a team's complete season schedule and game log: week-by-week opponents, scores, win/loss outcomes, victory margins, one-score game breakdown, excitement ratings, and game IDs.
  - `get_game_box_score`: Returns team box score and top individual game contributions.
  - `get_advanced_team_analytics`: Computes youth production share, average age, and draft pedigree.
  - `get_advanced_game_analytics`: Computes offensive pace rating and single-game excitement index.
  - `get_league_leaderboards`: Returns top 5 teams by watchability and popularity.
  - `get_team_articles`: Returns recent editorial articles and analysis referencing a specific team (resolves by name, city, abbreviation, or ID).
  - `get_player_articles`: Returns recent editorial articles and profiles referencing a specific player (resolves by name or ID).
- **Prohibited Internal Symbols:** Strict prompt directive banning internal mathematical symbols (`$P$`, `Y_prod`, `(88.17 W)`, `(64.84 POP)`) in favor of natural sports journalism prose.

### 7.1 Article Taxonomy & Writer Archetype Alignments (`newsroom/config/article_types.json`)
The sports publication categorizes longform output into four distinct article types, mapping specific writer personas and archetype categories:

1. **The Macro Slate Recap** (`MACRO_SLATE_RECAP`)
   - **Allowed Writers / Archetypes:** Barnwell, Rosenthal (`BARNWELL`, `ROSENTHAL`, `Weekly Recaps`).
   - **Granularity:** Week-level.
   - **Focus:** Cross-league trends, regression candidates, aesthetic watchability power rankings.
   - **Data Queried:** $E_{\text{game}}$, $P_{\text{pace}}$, league-wide point differentials, Pythagorean win expectations.

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
   - **Data Queried:** Advanced game analytics, player snap shares, red zone conversion rates, offensive pace ratings, SpikeIndex passing ceiling.

5. **The Power Rankings** (`POWER_RANKINGS`)
   - **Allowed Writers / Archetypes:** Mike Sando (Executive Tiers & Front-Office Context), Gregg Rosenthal (Vibe & Watchability Rankings) (`SANDO`, `ROSENTHAL`, `32-Team Thematic Consensus`).
   - **Granularity:** Week-level / Full League 1-32 Hierarchy.
   - **Focus:** Comprehensive publication-ready 32-team Power Ranking evaluating every team 1-32 through an explicit weekly editorial theme, underlying metrics, and movement indicators.
   - **Data Queried:** Opponent-Adjusted Game Credit, Team Watchability Index ($W$), Trajectory Multiplier ($T$), Purgatory Penalty, Base Market Gravity ($B$).
   - **Curatorial Autonomy:** Evaluates full 32-team standings and metrics to separate paper tigers from true contenders using Opponent-Adjusted Game Credit, rewards trajectory surging teams, penalizes middle-tier stagnators, and establishes a unifying Weekly Editorial Theme.
   - **Structure Directive:** 4-part 32-Team Power Ranking (`# 1. The Macro Overview & Weekly Theme`, `## 2. The 32-Team Enumeration 1 to 32 with movement indicators`, `3. The Thematic Blurbs with bold kickers`, `### 4. The Walk-Off / The Drop-Off`).
   - **Prompt Hook:** Rank all 32 NFL franchises from 1 to 32 under an explicit weekly editorial theme. Separate paper tigers from true contenders using opponent-adjusted credit and trajectory, provide a 3-to-4 sentence analytical blurb for every team with movement indicators, and diagnose the widening gap between tiers.

6. **The Awards Tracker** (`AWARDS_TRACKER`)
   - **Allowed Writers / Archetypes:** Field Yates (Objective/Factual), Bill Simmons (Narrative/Historical Debate) (`YATES`, `SIMMONS`, `The Hardware Race`).
   - **Granularity:** Season-level / Hardware Campaign.
   - **Focus:** Active MVP, OPOY, and DPOY award races, evaluating true contenders vs. empty-calorie stat accumulators, narrative momentum, and trench warfare.
   - **Data Queried:** Awards Equity ($A_{\text{awards}}$), Player Popularity ($Pop_{\text{player}}$), Team Success ($T_{\text{success}}$), SpikeIndex.
   - **Curatorial Autonomy:** Aggressively applies the "Empty Calories" Suppressor on sub-.300 teams, pinpointing the intersection of elite explosive production ($SpikeIndex$) and contender team success ($T_{\text{success}}$).
   - **Structure Directive:** 3-part Awards Watch (The MVP Frontrunner / The Heavy Crown `#` / `##`, The Contenders / The Chasers `##`, The Defensive Race / The Trench Warfare `##`).
   - **Prompt Hook:** Analyze the active MVP and DPOY races across the league. Apply the empty-calories suppressor to eliminate meaningless stats, examine the frontrunner vs. the chasers, and break down the defensive trench war.

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
    - **Focus:** Prestige top 100 player hierarchy evaluating elite talent, positional value, legacy hardware vs. projection, and grouped tier analysis.
    - **Data Queried:** Baseline Rating ($R_{\text{rating}}$), Positional Multiplier ($M_{\text{pos}}$), Career Awards Equity ($A_{\text{career}}$), Explosive Ceiling ($SpikeIndex$).
    - **Curatorial Autonomy:** Balances baseline roster talent against positional value; weighs aging veterans clinging to championship pedigree against young phenoms; identifies the true inner-circle top 10 for individual breakdown and strategically groups the remaining 90 players into analytical tiers.
    - **Structure Directive:** 4-part Top 100 Column (`# 1. The Evaluation Philosophy & Positional Value Hierarchy`, `## 2. The Inner Circle: The Definitive Top 10 Countdown (10 to 1)`, `## 3. The 11–100 Positional & Impact Tiers`, `### 4. The Toughest Omissions & The Just-Missed Tier`).
    - **Prompt Hook:** Establish the definitive NFL Top 100 hierarchy. Detail your positional value philosophy, provide deep analytical breakdowns for each player in the top 10 countdown (10 down to 1), categorize ranks 11 through 100 into analytical tiers, and examine the hardest omissions.

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
    - **Data Queried:** Player Popularity Index ($Pop_{\text{player}}$), Awards Equity ($A_{\text{awards}}$), Baseline Rating ($R_{\text{rating}}$), SpikeIndex, Target/Usage Deltas, Offensive Pace ($P$).
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
    - **Prompt Hook:** Group all 32 NFL franchises into structured preseason tiers based on baseline roster talent and draft pedigree. Define the criteria for each tier and provide a tight analytical justification for every team's placement, ensuring no team is left out.

14. **The Positional Tiers Breakdown** (`POSITIONAL_PLAYER_TIERS`)
    - **Allowed Writers / Archetypes:** Mike Sando (Executive/Scout Consensus), Field Yates (Factual & Fantasy Volume), Mina Kimes (Schematic Efficiency) (`SANDO`, `YATES`, `KIMES`, `Positional Rankings`).
    - **Granularity:** Midseason / Position Group Audit.
    - **Focus:** Grouping all starting players at a specific premium position into strictly defined analytical and structural tiers based on efficiency, ceiling, and scheme fit.
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
    - **Allowed Writers / Archetypes:** Bill Simmons (Historical / Pop Culture / Narrative), Emmanuel Acho (Uncomfortable Truths / Contrarian), Field Yates (Lists / Milestones / Volume), Skip Bayless (Clutch Gene / Fourth Quarter Drama) (`SIMMONS`, `ACHO`, `YATES`, `BAYLESS`, `National Perspective`).
    - **Granularity:** Week-level / Macro Narrative.
    - **Focus:** Singular viewpoint editorial focusing on league narratives, historical debates, contrarian reality checks, milestone valuations, or fourth-quarter clutch execution.
    - **Data Queried:** Player Popularity Index ($Pop_{\text{player}}$), Awards Equity ($A_{\text{awards}}$), Base Market Gravity ($B$), SpikeIndex, Offensive Pace ($P$).
    - **Curatorial Autonomy:** Isolates a single high-profile topic carrying an outsized national cultural footprint ($Pop_{\text{player}}$) or massive market gravity ($B$). Filters topic through writer archetype: historical parallels/wonky trends (Simmons), contrarian counter-arguments (Acho), milestone anomalies/share surges (Yates), or clutch execution/choke narratives (Bayless).
    - **Structure Directive:** Signature columnist format (governed by writer's signature column directive).
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
    - **Focus:** Identifying volatile, non-quarterback skill players whose boom-or-bust profiles ($SpikeIndex$) could single-handedly disrupt the Super Bowl game script.
    - **Data Queried:** SpikeIndex, Target/Snap Share Deltas, Opponent Matchup Deficiencies, Offensive Pace ($P$), Under-25 Production ($Y_{\text{prod}}$).
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
    - **Data Queried:** Base Market Gravity ($B$), Legacy Floor ($S_{\text{all-time}}$), Historical Franchise Prestige, National Penetration ($N$).
    - **Curatorial Autonomy:** Evaluates the two Super Bowl finalists through a strictly historical and organizational lens without detailing on-field positional matchups. Weighs dynasty solidification vs. market darling elevation, evaluating whether a Lombardi permanently lifts Base Market Gravity ($B$) or protects a historic Legacy Floor ($S_{\text{all-time}}$).
    - **Structure Directive:** 4-part Historical Prestige Column (`# 1. The Historical Weight (The Hook)`, `## 2. The Legacy Floor Analysis (Team A - AFC Champion)`, `## 3. The Market Gravity Analysis (Team B - NFC Champion)`, `### 4. The Existential Verdict`).
    - **Prompt Hook:** Evaluate the historical stakes of the Super Bowl matchup. Analyze what a Lombardi trophy would do for the Legacy Floor of the winning franchise and how it would shift their Base Market Gravity.

22. **The Bold Predictions & Panel Consensus** (`SUPER_BOWL_ROUNDTABLE_CONSENSUS`)
    - **Allowed Writers / Archetypes:** Multi-author byline / Senior Editor (`ROUNDTABLE_PANEL`), The Athletic Staff (`THE_ATHLETIC`), Mina Kimes (`KIMES`), Bill Simmons (`SIMMONS`), Emmanuel Acho (`ACHO`), Field Yates (`YATES`) (`The Super Bowl Roundtable`).
    - **Granularity:** Single-Game Pinnacle / Super Bowl Consensus.
    - **Focus:** Multi-voice feature highlighting conflicting models, narrative disagreements, extreme bold predictions, under-the-radar MVP picks, and final scoreboard consensus.
    - **Data Queried:** Upset Leverage ($U_{\text{projected}}$), Single-Game Excitement Index ($E$), SpikeIndex, Player Popularity Index ($Pop_{\text{player}}$).
    - **Curatorial Autonomy:** Acts as a panel of four distinct analysts with contrasting philosophies (Tape/Metrics: Kimes, Contrarian: Acho, Narrativist: Simmons, Fantasy/Props: Yates). Each analyst delivers a distinct bold prediction bordering on the extreme and an official final scoreline, moderated by an Editor.
    - **Structure Directive:** 4-part Expert Roundtable (`# 1. The Introduction (The Stakes)`, `## 2. The Bold Predictions (### sub-sections for each analyst's bold claim)`, `## 3. The X-Factor / MVP Picks (bulleted list tying back to specific metric lenses)`, `## 4. The Final Consensus (The Scoreboard with individual picks and bolded Panel Consensus)`).
    - **Prompt Hook:** Convene the expert roundtable for the Super Bowl. Feature conflicting bold predictions across tape analysis, contrarian logic, historical narrative, and fantasy props, name under-the-radar MVP candidates, and issue the panel's official score consensus.

23. **The Quarterback Duel Tape Breakdown** (`QUARTERBACK_DUEL_TAPE_BREAKDOWN`)
    - **Allowed Writers / Archetypes:** Dan Orlovsky (QB Mechanics & Telestrator Breakdown), Mina Kimes (Metric-Driven Schematic Film Study), KC Joyner (Scientific Efficiency & Matchup Splits) (`ORLOVSKY`, `KIMES`, `JOYNER`, `Film Room Tactical Breakdown`).
    - **Granularity:** Single-Game Marquee Matchup / Pre-Kickoff Film Study.
    - **Focus:** Comprehensive quarterback tape evaluation dissecting pocket mechanics, processing speeds against simulated pressure, middle-of-the-field coverage reads, and high-leverage situational creation.
    - **Data Queried:** Baseline Rating ($R_{\text{rating}}$), Awards Equity ($A_{\text{awards}}$), SpikeIndex, Offensive Pace ($P$), Playoff Clout ($S_{\text{playoff}}$), Opponent Matchup Deficiencies.
    - **Curatorial Autonomy:** Previews marquee matchup strictly through the tape of the two starting signal-callers. Contrasts timing inside structure against off-platform creation, decodes SpikeIndex variance on film, and tests efficiency against two-high shell rotations.
    - **Structure Directive:** 5-part Coaching Tape Breakdown (`# 1. The Film Room Overture`, `## 2. Film Study: [Quarterback A] vs. Opposing Defensive Disguises`, `## 3. Film Study: [Quarterback B] vs. Opposing Defensive Disguises`, `## 4. The High-Leverage Flashpoint: Third-and-Long & The Red Zone`, `### 5. The Tape Room Verdict`).
    - **Prompt Hook:** Deconstruct the Super Bowl quarterback duel through the coaching tape. Contrast how both signal-callers process split-safety looks, navigate collapsing pockets, and handle simulated pressures on third down.

24. **The Super Bowl Media & Interview Audit** (`SUPER_BOWL_MEDIA_INTERVIEW_AUDIT`)
    - **Allowed Writers / Archetypes:** Jeffri Chadiha (Narrative & Leadership), Bill Simmons (Body Language & Historical Pressure), Ryan Clark (Locker Room Dynamics) (`CHADIHA`, `SIMMONS`, `CLARK`, `Super Bowl Media & Narrative Audit`).
    - **Granularity:** Super Bowl Week / Media Availability & Narrative Arc.
    - **Focus:** Evaluating pre-game press conferences, Opening Night transcripts, locker room temperature, psychological pressure, and narrative control leading into the Super Bowl.
    - **Data Queried:** Provided Press Conference Quotes, Head Coach / Quarterback Tenure, Legacy Floor ($S_{\text{all-time}}$), Awards Equity (MVP Pressure), Player Popularity Index ($Pop_{\text{player}}$), Base Market Gravity ($B$).
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
    - **Focus:** First-round mock draft projecting selections for locked draft slots (Picks 1–24) based on team needs, positional value, and blue-chip player traits.
    - **Data Queried:** Blue-Chip Pedigree ($P_{\text{draft}}$), Under-25 Production ($Y_{\text{prod}}$), Positional Multiplier ($M_{\text{pos}}$), Team Roster Deficiencies.
    - **Curatorial Autonomy:** Projects the first 24 picks of the NFL Draft for eliminated teams following the Divisional round. Prioritizes premium positional value (QB, OT, EDGE, CB) over low-impact positions. Evaluates team rosters using Under-25 Production Share ($Y_{\text{prod}}$) to balance immediate foundational needs against developmental high-ceiling prospects without projecting speculative trades.
    - **Structure Directive:** 3-part Authoritative Mock Draft (`# 1. The Board Overview`, `## 2. The First-Round Projections (Picks 1–24 with ## [Pick Number]. [Team Name]: [Prospect Name], [Position], [School])`, `### 3. The Draft Floor Watch`).
    - **Prompt Hook:** Project the first 24 picks of the NFL Draft. Balance premium positional value against immediate roster holes for eliminated franchises.

### 7.2 Local Beat Reporting Taxonomy (`LOCAL_BEAT_REPORTER`)
Local Beat Reporters cover their assigned franchise exclusively. Prompts strictly isolate them from unrelated national games and ban artificial 1990–2023 national historical comparisons in favor of granular line play, injury designations, and locker-room accountability:

1. **The Postgame Report Card** (`LOCAL_POSTGAME_REPORT_CARD`)
   - **Beat Archetype:** Local Beat Writer / Postgame Columnist (2–4 hours post-whistle).
   - **Focus:** Instant execution vs. expectations, unit-by-unit letter grades (Passing Offense, Rushing Attack, Front Seven & Run Defense, Secondary & Pass Rush, Special Teams & Coaching Decisions), and Composite Team Grade.
   - **Data Queried:** Unit Offensive Pace ($P$), Opponent-Adjusted Game Credit, Turnover Margin, Red Zone Efficiency, Pressure Rates.

2. **The Local Matchup Primer & Game-Plan Blueprint** (`LOCAL_MATCHUP_PRIMER`)
   - **Beat Archetype:** Senior Beat Reporter (Friday / Saturday morning preview).
   - **Focus:** Matchup reality through home team's eyes, 3 hyper-specific player/unit micro battles (technique, slide protection, chips), victory blueprint checklist, and beat final score prediction.
   - **Data Queried:** Opponent Matchup Deficiencies, Upset Leverage ($U_{\text{projected}}$), Injury Report Availability, Defensive Front vs. O-Line Matchup.

3. **The Local Player Spotlight** (`LOCAL_PLAYER_SPOTLIGHT`)
   - **Beat Archetype:** Franchise Beat Reporter / Film Analyst.
   - **Focus:** Single local player's role evolution, developmental leap, contract-year status, locker-room standing, film/assignment technique, front-office roster equation, and Sunday matchup test.
   - **Data Queried:** SpikeIndex, Target/Snap Share Deltas, Positional Multiplier ($M_{\text{pos}}$), Blue-Chip Pedigree ($P_{\text{draft}}$), Under-25 Production Share ($Y_{\text{prod}}$).

4. **The Local Season-to-Date Progress Report** (`LOCAL_SEASON_PROGRESS_REPORT`)
   - **Beat Archetype:** Senior Beat Columnist (Midseason / Season Audit).
   - **Focus:** Identity check vs. offseason promises, structural strengths, unresolved flaws, coaching/front-office seat temperature, and second-half win benchmark.
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
   - Dynamically rotates national columnists (`SIMMONS`, `KIMES`, `ACHO`, `BAYLESS`, `YATES`) into `NATIONAL_OPINION_COLUMN` slots during regular-season feature trios.
3. **Qualified Writer Matching & Load Balancing:**
   - For each commissioned article type, the Editorial Desk queries `allowed_writers` from the taxonomy (`newsroom/config/article_types.json`).
   - Dispatches writers while balancing assignment counts (`writer_assignment_counts`) to ensure authorial diversity across the publication without duplicate writer assignments in the same week.
   - Binds each writer to their specific commissioned `article_type`, ensuring prompt injection of custom curatorial lenses, structure directives, and analytical parameters.

### 7.4 Playoff-Specific Dynamic Overrides Architecture (`newsroom/config/playoff_overrides.json`)
During the single-elimination tournament and Super Bowl run (Weeks 19–23), standard regular-season directives are dynamically superseded by targeted postseason overrides. The orchestrator inspects `target_weeks` at compile time and overrides the baseline article directive, curatorial autonomy filter, and structure specification without mutating baseline schemas:

1. **`SINGLE_GAME_SCHEMATIC_AUTOPSY` Playoff Overrides:**
   - **Weeks 19–22 (`PLAYOFF_ELIMINATION_ROUND_AUTOPSY` | Retrospective):**
     - **Focus:** High-leverage situational football, tightened personnel packages, blitz counters, and scheme-exposure points from the elimination round just completed.
     - **Curatorial Filter:** Isolates the single clash with highest tactical leverage, third-down aggression, or where fourth-down game management swung win probability.
     - **Structure:** 4-part Tape Autopsy (`# 1. The Decisive Blueprint`, `## 2. The Core Schematic Collision`, `## 3. The High-Leverage Breakdown`, `### 4. The Elimination Takeaway`).
   - **Week 23 (`SUPER_BOWL_SCHEMATIC_MASTERCLASS` | Anticipatory):**
     - **Focus:** Exhaustive pre-game tactical blueprint previewing the Super Bowl clash through an X's and O's coaching lens (disregarding celebrity/media week storylines).
     - **Curatorial Filter:** Trench warfare leverage (pass rush vs. slide protection), safety disguise rotations against primary passing concepts.
     - **Structure:** 5-part Masterclass (`# 1. The Grand Chess Match`, `## 2. Trench Warfare & Point-of-Attack Leverage`, `## 3. Coverage Shells vs. Passing Architecture`, `## 4. The Critical Situational Battle`, `### 5. The Coordinator Verdict`).

2. **`SLATE_LOOKAHEAD_PREDICTIONS` Playoff Overrides:**
   - **Weeks 19–21 (`POSTSEASON_ROUND_LOOKAHEAD` | Anticipatory):**
     - **Focus:** High-stakes single-elimination preview capsules for every game in the upcoming playoff round (Wild Card, Divisional, Conference Championship). Strict ban on betting lines/spreads.
     - **Curatorial Filter:** Mandatory full enumeration covering every scheduled tournament matchup.
     - **Structure:** Modular Postseason Capsules (`# 1. The Round Overview`, `## 2. Matchup Enumeration with kickoff/broadcast`, `3. Structured Sub-Sections: What We're Hearing, Stat to Know, Tactical Flashpoint, The X-Factor Player, Official Prediction`).
   - **Week 22 (`SUPER_BOWL_EARLY_TALE_OF_THE_TAPE` | Anticipatory):**
     - **Focus:** Comprehensive bye-week Tale of the Tape comparing both Super Bowl finalists from top to bottom.
     - **Curatorial Filter:** Contrasts cumulative efficiency profiles across every unit and neutral-site dome variables.
     - **Structure:** 6-part Tale of the Tape (`# 1. The Championship Tale of the Tape`, `## 2. The Macro Profile & Efficiency Baselines`, `## 3. Unit Clash 1: Team A Offense vs. Team B Defense`, `## 4. Unit Clash 2: Team B Offense vs. Team A Defense`, `## 5. The Volatility & Spike Audit`, `### 6. The Bye-Week Installation Mandate`).
### 7.5 Database Schema & Newsroom REST API Specifications
To support seamless frontend newsroom consumption, longform editorial content is persisted with full metadata and relational tagging:

1. **`news_feed` Database Schema:**
   ```sql
   CREATE TABLE IF NOT EXISTS news_feed (
       id SERIAL PRIMARY KEY,
       season INT NOT NULL,
       week INT NOT NULL,
       team_id INT NULL,
       content_type VARCHAR(50) NOT NULL,    -- 'ARTICLE' or 'TWEET'
       persona VARCHAR(50) NOT NULL,         -- e.g. 'BARNWELL', 'SANDO', 'LOCAL_BEAT_REPORTER'
       article_type VARCHAR(64),             -- e.g. 'MACRO_SLATE_RECAP', 'POWER_RANKINGS'
       slot VARCHAR(32),                     -- e.g. 'Hero Slot', 'Slot 1', ..., 'Standalone'
       headline VARCHAR(255),
       excerpt TEXT,
       content TEXT NOT NULL,
       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
   );
   ```

2. **Relational Entity Tagging:**
   - **`article_teams`**: Maps mentioned franchises (`PRIMARY`, `FEATURED`, `MENTIONED`) with mention frequencies.
   - **`article_players`**: Maps mentioned players with their active franchise, position, mention tier, and mention frequencies.

3. **Newsroom REST API Endpoints (`server/api.py`):**
   - **`GET /api/news/week/{week}?season={season}`**:
     - Returns the complete weekly news slate partitioned into canonical display slots:
       - **`hero`**: Hero Slot article (top banner).
       - **`secondary`**: Slots 1 and 2 (e.g. Schematic Autopsy, Slate Lookahead).
       - **`features`**: Slots 3, 4, and 5 (seasonal feature trio).
       - **`local_primers`**: Slots 6, 7, and 8 (local beat matchup breakdowns).
       - **`standalone`**: Standalone Power Rankings 32-team hierarchy.
     - Includes resolved author objects (`display_name`, `handle`, `archetype`) and attached entity tags (`tagged_teams`, `tagged_players`).
   - **`GET /api/news/article/{article_id}`**:
     - Returns full article text, headline, teaser excerpt, assigned slot, author metadata, and all tagged entities for reading view rendering.
