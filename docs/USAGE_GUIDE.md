# Project Mu — Comprehensive Usage Guide

This guide covers daily operations, weekly cadence workflows, analytics engines, newsroom generation, and debugging tools for **Project Mu**, an autonomous media ecosystem and simulation network for Madden NFL.

---

## Table of Contents
1. [Prerequisites & Environment Configuration](#1-prerequisites--environment-configuration)
2. [Starting Core Services](#2-starting-core-services)
3. [The Weekly Game Cadence (Primary Workflow)](#3-the-weekly-game-cadence-primary-workflow)
   - [Option A: One-Tap via the Web Debug UI](#option-a-one-tap-via-the-web-debug-ui)
   - [Option B: Running the Weekly Pipeline from CLI](#option-b-running-the-weekly-pipeline-from-cli)
   - [The ntfy Phone Notification Loop](#the-ntfy-phone-notification-loop)
4. [Special Cadence Runs & Seasonal Scans](#4-special-cadence-runs--seasonal-scans)
   - [Start-of-Season Full Scan (`--mode season`)](#start-of-season-full-scan---mode-season)
   - [Roster Cadence Scan (`--mode roster`)](#roster-cadence-scan---mode-roster)
   - [Draft Prospect Board Scan (`--mode prospects`)](#draft-prospect-board-scan---mode-prospects)
   - [Game Box-Score Scan / Week Recovery (`--mode games`)](#game-box-score-scan--week-recovery---mode-games)
   - [Playoff Scan (`--mode playoff`)](#playoff-scan---mode-playoff)
5. [Analytics Engines CLI Reference](#5-analytics-engines-cli-reference)
6. [Newsroom Swarm & Social Agent Wire Operations](#6-newsroom-swarm--social-agent-wire-operations)
7. [Debugging, Resuming & Failure Recovery](#7-debugging-resuming--failure-recovery)
8. [Database Maintenance, Backups & Verification](#8-database-maintenance-backups--verification)

---

## 1. Prerequisites & Environment Configuration

### macOS System Permissions
Remote Play automation uses Apple Vision OCR and synthetic keyboard events to control Madden via `chiaki-ng`:
- Go to **System Settings → Privacy & Security**:
  - Grant **Terminal** (or your terminal emulator / IDE) **Screen Recording** access.
  - Grant **Terminal** **Accessibility** access.
- Disable macOS App Nap for `chiaki-ng` to prevent background throttling:
  ```bash
  defaults write org.streetpea.chiaking NSAppSleepDisabled -bool YES
  ```

### Environment Configuration (`.env`)
Create or edit `.env` at the project root:
```ini
# PostgreSQL connection (default local database)
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/madden

# Google Gemini API key for OCR extraction & newsroom swarm
GEMINI_API_KEY=your_gemini_api_key_here

# Chiaki-ng Remote Play connection
CHIAKI_HOST=192.168.1.xxx
CHIAKI_HOST_IP=192.168.1.xxx

# Phone push notifications via ntfy
NTFY_SERVER=https://ntfy.sh
NTFY_TOPIC=project_mu_alerts

# Debug dashboard authentication token
DEBUG_TOKEN=your_secure_debug_token
```

To test phone notifications:
```bash
python3 -m pipeline.notify "Project Mu notification test"
```

---

## 2. Starting Core Services

Run all commands from the **project root** directory.

### 1. Launch FastAPI Backend
```bash
python3 -m uvicorn server.api:app --host 0.0.0.0 --port 8000 --reload
```
- REST API available at: `http://localhost:8000`
- API docs available at: `http://localhost:8000/docs`
- Static headshots served at: `http://localhost:8000/static/players/{player_id}.webp`

### 2. Launch Vite/React Frontend (`Mu_Network`)
```bash
cd Mu_Network && npm run dev
```
- Development UI available at: `http://localhost:8443` (or port assigned by Vite).

### 3. Production / Phone Access to the Web UI
The macOS firewall allows incoming traffic to Python (`server.api:8000`) but may block Vite (`node`). The backend serves the built frontend at `/`:
```bash
cd Mu_Network && npm run build
```
Access the application from any device on your local Wi-Fi:
- Main App: `http://<Mac-IP>:8000/`
- Debug Dashboard: `http://<Mac-IP>:8000/#debug`

---

## 3. The Weekly Game Cadence (Primary Workflow)

After advancing the week in your Madden Franchise:

### Option A: One-Tap via the Web Debug UI
1. Open `http://<Mac-IP>:8000/#debug` (or click the **LIVE** badge on the web app).
2. Enter your `DEBUG_TOKEN` once.
3. Keep `chiaki-ng` streaming Madden and ensure you are anywhere in the franchise menus (not in a game). Do not minimize or full-screen the window.
4. Under **Run Pipeline Job**, click **Scan week**.
5. Remote Play will connect and take control of the console. Your controller will disconnect.
6. The debug console streams live logs in real time.

### Option B: Running the Weekly Pipeline from CLI
```bash
python3 -m pipeline.weekly_capture
```

Flags:
- `--no-articles`: Ingest stats, team situational data, and run analytics engines without triggering writer agents.
- `--resume <folder>`: Resume a stopped run from `captures/<folder>` at the first unfinished step.
- `--no-connect`: Skip auto-relaunching `chiaki-ng`.

### The ntfy Phone Notification Loop
1. **Console Takeover**: When capture starts, Remote Play connects and disconnects your physical controller.
2. **"Your Turn" Notice (~20 minutes)**: Once menu screen-captures finish, you receive an alert on your phone. You can immediately turn your controller back on and resume playing or simulating in Madden.
3. **Background Processing**: Gemini extraction, database loading, mathematical backfills, culture engines, and swarm publishing proceed autonomously on your Mac.
4. **"Week Done" Notice**: You receive a final notification when all articles, rankings, fantasy updates, and social posts are committed to the database.

---

## 4. Special Cadence Runs & Seasonal Scans

### Start-of-Season Full Scan (`--mode season`)
Performs the full start-of-season calibration in strict sequential order (and automatically rolls over `league_state` to `(current_season + 1, 1)` if `current_week != 1`):
1. Full 18-week schedule (~3 pages per week) → `season_schedule/`
2. All 32 team rosters (~80 seconds per team) → `roster/`
3. 32-team Injury Report (offseason / preseason injuries for Week 1) → `injuries/`
4. Scouting College Draft Board row-by-row → `prospects/`

```bash
python3 -m pipeline.weekly_capture --mode season --no-articles
```

### Roster Cadence Scan (`--mode roster`)
Scans all 32 team rosters via Manage Roster > Roster, extracting card crops and list screenshots:
```bash
python3 -m pipeline.weekly_capture --mode roster --no-articles
```
Or directly with the capture script:
```bash
python3 madden_capture.py --only roster
```

### Draft Prospect Board Scan (`--mode prospects`)
Navigates through Scouting > Edit Draft Class to capture the full scouted board row-by-row with on-device Apple Vision OCR:
```bash
python3 -m pipeline.weekly_capture --mode prospects --no-articles
```
Or directly:
```bash
python3 madden_capture.py --only prospects
```

### Game Box-Score Scan / Week Recovery (`--mode games`)
Used to recover skipped weeks or capture detailed per-game player stats and team situational stats:
```bash
python3 -m pipeline.weekly_capture --mode games
```
Or capture a specific week:
```bash
python3 madden_capture.py --only games --week "SEASON WEEK 12"
```

### Playoff Scan (`--mode playoff`)
Automatically engaged from Week 19 onward. Captures the playoff schedule, coming round matchups, and box scores for every played game in the round:
```bash
python3 -m pipeline.weekly_capture --mode playoff
```

---

## 5. Analytics Engines CLI Reference

All engines default to the current season stored in `league_state.current_season`. Override using `--season N`.

| Engine | Command | Description |
|---|---|---|
| **Excitement Index ($E$)** | `python3 -m engines.gotw_engine` | Scores game pace, margin, and milestones ($0\text{--}100$). Add `--missing-only` to fill unscored games. |
| **Projected Excitement** | `python3 -m engines.excitement_projection` | Generates pre-kickoff excitement predictions for scheduled games. `--backtest` evaluates error. |
| **Game Scripts** | `python3 -m engines.game_script_engine` | Seeds and renders canonical, deterministic play-by-play game narratives into `game_scripts`. |
| **Watchability (TWI)** | `python3 -m engines.twi_engine` | Recency-weighted team entertainment index including chaos volatility ($\sigma_{\text{top}}$). |
| **Franchise Popularity** | `python3 -m engines.popularity_engine` | Market gravity, legacy floor, and win-rate trajectory. |
| **Player Popularity** | `python3 -m engines.player_popularity_engine` | Spike index, rating clout, market exposure, and hardware prestige. |
| **Game Credit (SRS)** | `python3 -m engines.game_credit_engine` | Opponent-adjusted point differential and momentum trajectory multiplier. |
| **Team Efficiency** | `python3 -m engines.team_efficiency_engine` | Pythagorean win expectations, pace, production, and situational conversion rates. |
| **Coach of the Year** | `python3 -m engines.coty_engine` | Roster talent outperformance and season-over-season improvement rankings. |
| **Playoff Clout** | `python3 -m engines.playoff_clout_engine` | Postseason win equity and milestone leverage for franchises and players. |
| **Record Book** | `python3 -m engines.records_engine` | Checks for newly broken franchise records and all-time NFL top-10 entries. |
| **Round 1 Mock Draft** | `python3 -m engines.mock_draft_engine --save` | Simulates Round 1 selections from draft order, team needs, and prospect board. |
| **Preseason Fantasy ADP** | `python3 -m engines.fantasy_adp_engine` | Simulates 100 12-team PPR drafts to generate consensus ADP boards. |
| **Fantasy League Sim** | `python3 -m engines.fantasy_league_sim` | Simulates 100 competitive leagues week-by-week (standings, rostered %, started %). |
| **Fantasy Projections** | `python3 -m engines.fantasy_projection_engine` | Generates weekly PPR point projections, positional rankings, and 1–5 matchup stars. |

---

## 6. Newsroom Swarm & Social Agent Wire Operations

### Autonomous Weekly Slate Generation
Publishes the national 10-article editorial matrix and weekly fantasy magazine:
```bash
python3 -m newsroom.weekly_run
```

Key CLI Flags:
- `--week N`: Manually target publication week (e.g., `--week 1` preseason or `--week 23` Super Bowl).
- `--force`: Re-publish a slate even if articles already exist for that week.
- `--dry-run`: Format briefs and prompt chains without making Gemini calls or saving records.
- `--skip-fantasy`: Generate only national articles.
- `--fantasy-only`: Generate only the Mu Fantasy magazine slate.
- `--skip-social`: Skip Agent Wire social post generation.
- `--social-only`: Run only the Agent Wire social desk.

### On-Demand Team Local Beat Wakeup
Visiting a franchise's Team Hub (`#team-{abbr}` or `#social-team-{abbr}`) automatically triggers background local coverage generation if inactive for the week.

To trigger manually via REST API:
```bash
curl -X POST "http://localhost:8000/api/news/team/1/wakeup?live=true&save_to_db=true"
```

### Clearing Generated Media
To wipe generated articles and tweets while keeping database schemas and player records intact:
```bash
bash db/clear_news_feed.sh
```
*(Creates an automatic backup in `db/backups/` before clearing; add `--articles-only` to preserve social wire posts).*

---

## 7. Debugging, Resuming & Failure Recovery

### Resuming an Interrupted Capture or Processing Run
If a weekly capture is interrupted (e.g., Remote Play timeout or Gemini API rate limit), the pipeline preserves intermediate outputs in `captures/<folder>/`.

Resume directly via CLI:
```bash
python3 -m pipeline.weekly_capture --resume <folder_name>
```
Or via the `#debug` Web UI under **Resume a stopped run**. The pipeline resumes at the first incomplete stage:
1. Capture (checks `.capture_done`)
2. Extraction (checks `schedule.csv` and `stats_*.csv`)
3. Database Load (checks `captures.loaded_at`)
4. Analytics Backfill & Engines
5. Swarm Generation
6. Media Compression (shrinks PNGs to WebP)

### Chiaki-ng Remote Play Troubleshooting
- **Stream Freeze Detection**: `madden_capture.py` sends harmless test inputs when screens fail to advance. If unresponsive, it automatically force-quits and relaunches `chiaki-ng` (up to 8 attempts per run) before resuming the current category.
- **Verify Screen Classifier**: Check whether OCR accurately reads the current Madden screen without sending inputs:
  ```bash
  python3 madden_capture.py --test
  ```
- **L2 Dropdown Diagnostics**:
  ```bash
  python3 madden_capture.py --probe-l2
  ```

### Newsroom & Session Verification
Before running a production week or after updating prompt directives:
```bash
bash tools/verify_session.sh
```
Runs:
1. Unit tests (`pytest -q tests`)
2. Live DB newsroom verifications (`tools/verify_newsroom.py`)
3. Dry-run swarm validation across brief compiling, ReAct DB tools, and schema constraints.

---

## 8. Database Maintenance, Backups & Verification

### Database Backups & Restores
Backup script dumps the running Docker PostgreSQL database (`madden-db`):
```bash
bash db/backup_db.sh
```
Dumps are timestamped in `db/backups/madden_backup_<timestamp>.sql`.

To restore from a previous backup:
```bash
bash db/restore_db.sh db/backups/<backup_file>.sql
```

### Running Database Migrations
SQL migrations are stored in `db/migrations/`:
```bash
docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/<migration_file>.sql
```
Python data migrations:
```bash
python3 -m db.migrations.<script_name>
```

### Checking Capture Load Integrity
Verify that loaded counting statistics match season leaderboards and player allocations:
```bash
python3 -m tools.check_capture_load
```
