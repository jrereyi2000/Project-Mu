# Project Mu: "Play Fantasy" — Sleeper-Style Interactive League Hub
## Architectural Specification & Implementation Plan

![Sleeper Desktop Interface Reference](../data/images/sleeper_desktop_reference.png)

---

## 1. Executive Summary & Architectural Decision

### Tab vs. Dedicated Page
> **Recommendation: Dedicated Application Page (`#play-fantasy`) with Global Header Bridge**
> 
> - **Mu Fantasy (`#fantasy`)** is an editorial and analytical command center (news articles, consensus weekly rankings, rest-of-season boards, ADP draft charts, defensive SOS stars). It uses a clean, light newspaper-slate aesthetic.
> - **Play Fantasy (`#play-fantasy`)** is an interactive, multi-view operational fantasy software application (managing rosters, setting weekly lineups, live head-to-head score tracking, waiver wire shopping, proposing trades, and real-time trash talk). It demands an immersive, dark 3-panel command center identical to the Sleeper web experience shown in the reference screenshot.
> 
> **The Integration Model:**
> 1. In `FantasyHeader` (`Mu_Network/src/App.tsx`), we add a prominent **"Play Fantasy"** tab with a dropdown preview of your active leagues (`"Solo"`, `"The Justice League"`, `"The Avengers"`, `"+ Join/Create League"`).
> 2. Clicking any league or "Play Fantasy" transitions seamlessly to `#play-fantasy` (or `#play-fantasy-{leagueId}`), rendering the full Sleeper-styled 3-panel desktop app.
> 3. An NFL / Mu Fantasy switcher in the top navigation allows fluid movement back and forth between the newsroom, analytics, and your interactive fantasy leagues.

---

## 2. UI/UX Architecture: The 3-Panel Sleeper Experience

Following the reference interface, the Play Fantasy page is structured into three responsive, synchronized columns:

```
┌─────────────────┬─────────────────────────────────────────────────┬───────────────────────────┐
│ 1. LEFT RAIL    │ 2. CENTER STAGE (League Hub)                    │ 3. RIGHT RAIL             │
│                 │                                                 │                           │
│ • Sleeper / Mu  │ • Header: [Icon] Solo 2026 8-Team SF PPR  [⚙]    │ • Header: League Chat 📌🔔│
│   Brand Mark    │ • Nav Tabs: MATCHUP | TEAM | LEAGUE | PLAYERS   │                           │
│ • Direct Msgs   │             TREND | TRADES | SCORES             │ • Bot Cards:              │
│ • Inbox (Alerts)│─────────────────────────────────────────────────│   - RB Benchwarmer of Week│
│ • LEAGUES (+)   │ [MATCHUP VIEW ACTIVE]                           │     (Aaron Jones card)    │
│   - Solo (Active│ • Vs Header:                                    │   - WR Benchwarmer of Week│
│     Matchup)    │   @jrereyi2000 (Straw Hats) vs Team 3           │     (@jrereyi2000 / Puka) │
│   - Justice Lg  │   Win Prob (76% vs 24%) | Proj (152.1 vs 113.9) │   - TE Benchwarmer of Week│
│   - Avengers    │ • Week Selector: [ Wk. 6 ▾ ]                    │                           │
│                 │ • Starters Comparison Table:                    │ • AI Manager Banter       │
│ • User Profile  │   [Player L] [Pts] [SLOT] [Pts] [Player R]      │ • Chat Message Feed       │
│   @jrereyi2000  │   Allen  24.95     QB    18.45  Prescott        │                           │
│   [Avatar] [⚙]  │   Love    0.00     RB    22.76  Walker          │ • Input: [+] [GIF] [Text] │
└─────────────────┴─────────────────────────────────────────────────┴───────────────────────────┘
```

### Panel 1: Left Navigation Rail
- **Brand & Direct Comms**: Sleeper/Mu Fantasy lightning icon, direct messages, inbox for trade notifications and waiver claim confirmations.
- **Leagues Directory**: 
  - List of joined leagues with custom league badges and live score teasers (`"The Straw... vs Team 3 — 152.14 vs 113.95"`).
  - Quick status chips: `IN PROGRESS`, `• PRE DRAFT`, `FINAL`.
  - **`(+)` Action Button**: Opens a modal to **Create a New League** (choose size 8/10/12, PPR/Half/Standard, Superflex, roster spots) or **Join an AI Simulation League** (instantly claim one of the 100 simulated leagues from `fantasy_league_sim.py`).
- **User Profile Footer**:
  - Avatar, handle (`@jrereyi2000`), trophy count, and profile settings modal.

### Panel 2: Center League Hub (The 7 Core Tabs)
1. **`MATCHUP` (Primary Screen in Screenshot)**:
   - **Matchup Banner**: Team avatars, records (e.g., `5-0` vs `4-1`), live scores, projected points (`152.14` vs `113.95`), and dynamic win probability bar (`76%` vs `24%`).
   - "Yet to play" counters for unplayed roster spots.
   - **Week Selector Dropdown** (`Wk. 1` through `Wk. 17`).
   - **Starters Split-Column Table**:
     - Left Team: Headshot, Name, Position, Team, Game kickoff/status (`Sun 4:25 PM @ LV`, `Final L 14-17 @ DEN`), live/projected fantasy points.
     - Center Slot Badges: `QB`, `RB`, `RB`, `WR`, `WR`, `TE`, `FLEX`, `SUPERFLEX`, `K`, `DEF`.
     - Right Team: Opponent player details mirroring the left side.
   - **Bench Comparison Accordion**: Full bench matchup comparison below the starters.
2. **`TEAM` (Roster Management)**:
   - Your complete roster grouped by Starters, Bench, and IR/Taxi.
   - Interactive Drag-and-Drop or Tap-to-Swap lineup controls (move bench WR to Starter or FLEX; live verification of roster eligibility).
   - Instant fantasy player modal integration on clicking any player card.
3. **`LEAGUE` (Standings & Playoff Bracket)**:
   - Standings table: Rank, Team Name, Manager Handle, Record (W-L-T), Points For (PF), Points Against (PA), Streak, Playoff Seed.
   - Interactive 6-team single-elimination playoff bracket (Weeks 15–17).
   - League settings summary & member directory.
4. **`PLAYERS` (Free Agent & Waiver Market)**:
   - Complete unrostered player pool.
   - Search by name, filter by position (`ALL`, `QB`, `RB`, `WR`, `TE`, `K`, `DEF`), and sort by `PROJ`, `AVG`, `ROS`, or `% ROSTERED`.
   - One-click **"Claim / Add"** button triggering the drop-selection modal.
5. **`TREND` (Market Movers)**:
   - Most added and most dropped players across simulated leagues over the past 48 hours.
   - ADP risers and fallers.
6. **`TRADES` (Trade Machine & Proposals)**:
   - Propose trades to any AI manager in the league.
   - AI evaluation logic: Evaluates trades based on `ros_total_points` delta, team positional surplus/deficits, and manager reactivity.
   - Pending offers inbox with Accept/Reject/Counter actions.
7. **`SCORES` (League Scoreboard)**:
   - Live grid of all 4–6 head-to-head matchups across the league for the active week.

### Panel 3: Right Sidebar (Autonomous League Chat & Automated Awards)
- **Automated Media Cards (As seen in the screenshot)**:
  - **"RB / WR / TE Benchwarmer of the Week"**: Identifies the manager who left the highest-scoring player on their bench and creates a visual highlight card.
  - **"High Scorer of the Week"** and **"Heartbreaker of the Week"** (lost by < 1.5 points).
  - **"Waiver Wire Summary"** (automated weekly transaction log).
- **AI Manager Chat & Trash Talk**:
  - The AI managers in the league post reactions to blowouts, close finishes, and trade proposals using the newsroom's agent persona engine.
- **Interactive User Chat**:
  - Send messages, react with emojis, post GIFs, and banter with the simulated league.

---

## 3. Data Model & Database Architecture

To enable persistence, multi-league management, and stateful rosters without disrupting the existing 100-league statistical engine, we add dedicated user fantasy tables via migration:

```
┌────────────────────────────────────────────────────────┐
│                   fantasy_user_profiles                │
│ id (PK) | handle | display_name | avatar_url | created_at │
└───────────────────────────┬────────────────────────────┘
                            │ 1:N
┌───────────────────────────▼────────────────────────────┐
│                    fantasy_user_leagues                │
│ id (PK) | name | season | teams_count | scoring_type    │
│ roster_format (jsonb) | current_week | status | avatar │
└───────────────────────────┬────────────────────────────┘
                            │ 1:N
┌───────────────────────────▼────────────────────────────┐
│                     fantasy_user_teams                 │
│ id (PK) | league_id | user_id (NULL for AI) | team_name│
│ manager_handle | avatar_url | is_ai | strategy | wins  │
│ losses | ties | points_for | points_against | seed     │
└───────────────────────────┬────────────────────────────┘
         ┌──────────────────┴──────────────────┐
         │ 1:N                                 │ 1:N
┌────────▼─────────────────────┐     ┌─────────▼─────────────────────┐
│    fantasy_user_rosters      │     │    fantasy_user_matchups      │
│ id (PK) | league_id | team_id│     │ id (PK) | league_id | week    │
│ week | player_id | slot      │     │ home_team_id | away_team_id   │
│ (QB, RB1, WR1, FLEX, BN, IR) │     │ home_score | away_score       │
└──────────────────────────────┘     │ home_proj | away_proj         │
                                     │ win_prob_home | is_final      │
                                     └───────────────────────────────┘
```

### Detailed Table Specifications

1. **`fantasy_user_profiles`**:
   - `id SERIAL PRIMARY KEY`, `handle VARCHAR(32) UNIQUE` (e.g. `'jrereyi2000'`), `display_name VARCHAR(64)`, `avatar_url TEXT`, `created_at TIMESTAMPTZ DEFAULT NOW()`.
   - Seeded with default profile `@jrereyi2000` ("The Straw Hat Pirates").
2. **`fantasy_user_leagues`**:
   - `id SERIAL PRIMARY KEY`, `name VARCHAR(64)` (e.g. `'Solo 2026 8-Team SF PPR'`), `season INT DEFAULT 2035`, `teams_count SMALLINT` (8, 10, or 12).
   - `scoring_type VARCHAR(16)` (`'PPR'`, `'HALF_PPR'`, `'STANDARD'`).
   - `roster_format JSONB`: E.g. `{"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 1, "BN": 6, "IR": 2}`.
   - `status VARCHAR(16)` (`'PRE_DRAFT'`, `'IN_SEASON'`, `'COMPLETED'`), `avatar_url TEXT`.
3. **`fantasy_user_teams`**:
   - `id SERIAL PRIMARY KEY`, `league_id INT REFERENCES fantasy_user_leagues(id) ON DELETE CASCADE`.
   - `user_id INT REFERENCES fantasy_user_profiles(id) ON DELETE SET NULL` (NULL for AI opponents).
   - `team_name VARCHAR(64)`, `manager_handle VARCHAR(32)`, `avatar_url TEXT`, `is_ai BOOLEAN DEFAULT FALSE`.
   - `strategy VARCHAR(16)` (for AI managers: `'BPA'`, `'HERO_RB'`, `'ZERO_RB'`, `'EARLY_QB'`).
   - `wins INT DEFAULT 0`, `losses INT DEFAULT 0`, `ties INT DEFAULT 0`, `points_for NUMERIC(8,2) DEFAULT 0`, `points_against NUMERIC(8,2) DEFAULT 0`, `seed SMALLINT`.
4. **`fantasy_user_rosters`**:
   - `id SERIAL PRIMARY KEY`, `league_id INT`, `team_id INT`, `week INT`, `player_id INT REFERENCES players(id)`, `slot VARCHAR(16)`.
   - `slot`: `'QB'`, `'RB1'`, `'RB2'`, `'WR1'`, `'WR2'`, `'TE'`, `'FLEX'`, `'SUPERFLEX'`, `'BN'`, `'IR'`.
   - Unique on `(team_id, week, player_id)`.
5. **`fantasy_user_matchups`**:
   - `id SERIAL PRIMARY KEY`, `league_id INT`, `week INT`, `home_team_id INT`, `away_team_id INT`.
   - `home_score NUMERIC(6,2)`, `away_score NUMERIC(6,2)`, `home_proj NUMERIC(6,2)`, `away_proj NUMERIC(6,2)`, `win_prob_home NUMERIC(4,3)`, `is_final BOOLEAN DEFAULT FALSE`.
6. **`fantasy_user_transactions`**:
   - `id SERIAL PRIMARY KEY`, `league_id INT`, `week INT`, `team_id INT`, `type VARCHAR(16)` (`'ADD'`, `'DROP'`, `'TRADE'`, `'DRAFT'`).
   - `player_added_id INT`, `player_dropped_id INT`, `created_at TIMESTAMPTZ DEFAULT NOW()`.
7. **`fantasy_user_chat`**:
   - `id SERIAL PRIMARY KEY`, `league_id INT`, `sender_type VARCHAR(16)` (`'USER'`, `'AI_MANAGER'`, `'SYSTEM_BOT'`).
   - `sender_name VARCHAR(64)`, `sender_handle VARCHAR(32)`, `avatar_url TEXT`, `message_text TEXT`, `card_payload JSONB`, `created_at TIMESTAMPTZ DEFAULT NOW()`.

---

## 4. Backend Engine Integration & Simulation Logic

### Leveraging Existing Engines
We do **not** re-invent fantasy math; we leverage Project Mu's deterministic engines:
1. **Scoring & Projections**:
   - Starter points come directly from `player_stats.fantasy_points` (for completed games) and `fantasy_projections.projected_points` (for upcoming games).
   - Win probabilities are computed live using a normal CDF over the projection differential:
     $$\Phi\left(\frac{\text{Proj}_{\text{home}} - \text{Proj}_{\text{away}}}{\sigma_{\text{matchup}}}\right)$$
2. **AI Manager Behavior**:
   - AI draft picks, waiver wire adds/drops, and weekly lineup decisions adapt directly from `engines/fantasy_league_sim.py` (`set_lineup`, `player_value`, `run_waivers`).
   - AI managers automatically start their highest projected eligible players every week.
3. **Automated League Bot & Awards Generator (`engines/fantasy_league_bot.py`)**:
   - After each simulated week's games complete, the bot analyzes every roster and generates:
     - **Benchwarmer of the Week**: Scans all `BN` slots for the highest scorer across the entire league.
     - **Matchup of the Week**: Closest margin of victory or highest combined score.
     - **Blowout & Upset Alerts**.
   - These are formatted as rich card payloads and inserted into `fantasy_user_chat`.

---

## 5. API Endpoints (`server/fantasy_user_api.py`)

A clean set of REST endpoints mounted under `/api/fantasy/user/*`:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/fantasy/user/profile` | Current user profile, active leagues, unread alerts |
| `GET` | `/api/fantasy/user/leagues` | All leagues the user belongs to with preview scores |
| `POST` | `/api/fantasy/user/leagues/create` | Create a custom league (name, teams, format, rules) |
| `POST` | `/api/fantasy/user/leagues/join-sim/{league_num}` | Instantly clone or join a league from the 100 sim pool |
| `GET` | `/api/fantasy/user/league/{id}` | League details, standings, and settings |
| `GET` | `/api/fantasy/user/league/{id}/matchup?week={week}` | Head-to-head matchup, starter vs starter breakdown |
| `POST` | `/api/fantasy/user/league/{id}/lineup` | Move players between starters and bench (`{team_id, swaps}`) |
| `GET` | `/api/fantasy/user/league/{id}/waivers` | Available free agents with projections & stats |
| `POST` | `/api/fantasy/user/league/{id}/waivers/claim` | Add a player and select droppable roster player |
| `GET` | `/api/fantasy/user/league/{id}/trades` | Trade market, trade block, and pending proposals |
| `POST` | `/api/fantasy/user/league/{id}/trades/propose` | Propose a trade to another manager |
| `GET` | `/api/fantasy/user/league/{id}/chat` | League chat feed including automated bot award cards |
| `POST` | `/api/fantasy/user/league/{id}/chat/send` | Send a message to the league chat |

---

## 6. Implementation Plan & Phased Roadmap

### Phase 1: Database Schema & Default Seeding
1. Create migration `db/migrations/2026_12_02_fantasy_user_leagues.sql` containing all 7 user fantasy tables.
2. Build `engines/fantasy_user_seeder.py`:
   - Seeds the `@jrereyi2000` user profile.
   - Seeds 3 starter leagues matching your screenshot:
     1. **`Solo 2026 8-Team SF PPR`** (Your team: "The Straw Hat Pirates", Week 6 vs Team 3, Starters matching your exact screenshot: Josh Allen, Jordan Love, D'Andre Swift, Nico Collins, Puka Nacua, etc.).
     2. **`The Justice League`** (12-team PPR).
     3. **`The Avengers`** (8-team Dynasty PPR, Pre-Draft status).
   - Generates the full 14-week round-robin schedule and sets rosters.

### Phase 2: Backend API & Service Layer
1. Implement `server/fantasy_user_api.py` with all CRUD and matchup calculation logic.
2. Implement `engines/fantasy_league_bot.py` to calculate Benchwarmer of the Week and weekly recap cards.
3. Mount the new router in `server/api.py`.
4. Write unit tests in `tests/test_fantasy_user_api.py`.

### Phase 3: Frontend Sleeper Theme & 3-Panel Layout
1. Create `Mu_Network/src/PlayFantasyPage.tsx`:
   - Left Panel: League list, avatar chips, active match score teasers, User Profile bar.
   - Right Panel: League Chat container with message stream and input box.
   - Center Hub: League header, settings gear, and the 7-tab navigation bar.
2. Add custom Sleeper dark theme variables and styling in `Mu_Network/src/sleeper.css`:
   - Dark palette (`#131a27`, `#1a2332`, `#00ceb8` Sleeper cyan accent).
   - Center split matchup layout styling with custom slot pill badges (`QB`, `RB`, `WR`, `TE`, `FLEX`, `SF`).

### Phase 4: Matchup & Lineup Management
1. Build the interactive `MATCHUP` tab:
   - Live win probability gauge, projected totals, starter vs starter player rows.
   - Player headshots (`/static/players/{id}.webp`), game status, kickoff time, points.
   - Week selector dropdown (`Wk. 1`–`17`).
2. Build the `TEAM` tab:
   - Lineup editor allowing the user to swap benched players into starting slots.
   - Validation ensuring position eligibility (e.g., only RB/WR/TE in FLEX, QB in SF).

### Phase 5: Autonomous Chat, Free Agency & Navigation Bridge
1. Build the Benchwarmer of the Week card renderer in the League Chat feed.
2. Build the `PLAYERS` tab (Free Agency / Waiver claim modal).
3. Connect `FantasyHeader` in `Mu_Network/src/App.tsx`:
   - Add "Play Fantasy" navigation tab and league quick-switcher dropdown.
   - Register hash routes `#play-fantasy` and `#play-fantasy-{id}`.
4. Update `PROJECT_SPEC.md` and `Mu_Network/PROJECT_SPEC.md` to reflect the new subsystem.

---

## 7. Verification & Success Criteria

1. **Visual Fidelity**: The `#play-fantasy` screen matches the uploaded Sleeper desktop layout, typography, cyan accents, and 3-panel division.
2. **Stateful Interaction**: Changing starters in the `TEAM` tab immediately updates the `MATCHUP` view, total projected points, and win probabilities.
3. **Multi-League Switching**: Clicking between "Solo", "The Justice League", and "The Avengers" in the left rail instantly transitions league state without reloading.
4. **Rich League Feed**: Weekly benchwarmer cards and AI chat messages appear dynamically in the right rail.
5. **No Regressions**: Existing `Mu Fantasy Analytics` (`#fantasy`, rankings, ADP board) and newsroom operations remain completely intact.
