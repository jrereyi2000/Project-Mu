# Draft Central & Immersive College Scouting Plan

**Status (2026-10-28):** built 2026-10-24 (`81e1b29`, `Mu_Network/src/DraftPage.tsx`); linked from the Draft Capital grid (`b668894`).  
**Access Model:** Accessible exclusively via the `#debug` control view (not a top-level navbar tab); navigating to `#draft` enters the Draft Central home page.  
**Reference Specs:** `PROJECT_SPEC.md` §2.10, §2.18, §3.3c, §3.3g; `docs/done/team_management/04_SCOUTING_AND_DRAFT_PLAN.md`.

---

## 1. Executive Summary & Objectives

Project Mu simulates a living NFL and college football scouting ecosystem:
- The backend currently generates weekly college `GAME` statlines for top prospects ([`engines/scouting_events.py`](file:/Users/jrereyi/Downloads/Project%20Mu/engines/scouting_events.py)), stochastic consensus movement across 27 steps ([`engines/scouting_engine.py`](file:/Users/jrereyi/Downloads/Project%20Mu/engines/scouting_engine.py)), private team GM boards ([`engines/scouting_boards.py`](file:/Users/jrereyi/Downloads/Project%20Mu/engines/scouting_boards.py)), and an analytical media mock draft ([`engines/mock_draft_engine.py`](file:/Users/jrereyi/Downloads/Project%20Mu/engines/mock_draft_engine.py)).
- However, draft functionality has historically been isolated within backend test suites, newsroom tool queries, and an operational `#debug` card.

This plan builds **Draft Central (`#draft`)**: a broadcast-grade, multi-tab scouting and draft simulation destination. It unifies college game-by-game logs, cumulative season statlines, NFL Combine athletic profiles, college awards races, team needs, media mock drafts, and a live broadcast-style Draft Day War Room into one immersive interface.

---

## 2. Navigation & Access Model

To preserve the public media brand of Gridiron Newsroom and avoid cluttering the primary navigation:
1. **No Top-Level Navbar Tab:** The primary navigation header (`Teams`, `Stats`, `Standings`, `Schedule`, `Fantasy`, `Social`) remains unchanged.
2. **Access via `#debug`:** In the `#debug` Control tab, a dedicated **Draft Central** hero banner provides league status (current draft class, active scouting step, calendar phase) and a primary call-to-action button:
   ```
   [ Enter Draft Central → ]
   ```
3. **Route Destination (`#draft`):** Clicking the button navigates to `#draft`, opening the **Draft Central Home Page** (defaulting to the **Consensus Big Board**).
4. **Header Utilities:** Draft Central features a top utility bar with a breadcrumb `← Return to Debug` and `Gridiron Home`, letting the operator seamlessly transition between system management and the scouting experience.

---

## 3. Draft Central Core Architecture & Tabs

Draft Central is organized into five cohesive views:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│  GRIDIRON DRAFT CENTRAL · 2037 CLASS · STEP 4 (COLLEGE WEEK 5)         [← Back to Debug]│
│  [ 📊 Big Board ] [ 🏆 Honors & Movers ] [ 📋 Team Needs ] [ 🔮 Media Mock ] [ 🔴 War Room ]│
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Tab 1: The Consensus Big Board (Home View)
* **The Board Matrix:** 150+ prospects ordered by public consensus grade (0–100) and consensus rank.
* **Mover Momentum:** Visual rank change badges ($\Delta\text{rank}$, $\Delta\text{grade}$) indicating stock rising ($\blacktriangle$ green) or falling ($\blacktriangledown$ red) relative to the prior scouting step.
* **Filter & Search Controls:**
  * **Position Groups:** `ALL`, `QB`, `RB`, `WR`, `TE`, `OT`, `IOL`, `EDGE`, `DT`, `LB`, `CB`, `S`, `K/P`.
  * **Conferences:** `ALL`, `SEC`, `Big Ten`, `ACC`, `Big 12`, `P4`, `FBS`, `FCS`.
  * **Round Projection:** `Round 1` (1–32), `Day 2` (33–100), `Day 3` (101–224), `UDFA`.
  * **Real-time Name & School Search.**
* **Headline Ticker:** Displays the latest news events generated during the current step (e.g. *"Amir Vogel shines vs Georgia"*, *"Donnie Reynolds runs 4.41s at NFL Combine"*).

---

### Tab 2: Prospect Dossier & Career Profile (Modal / Deep-Dive Drawer)
Clicking any prospect across any tab opens their full **Scouting Dossier**:
1. **Cumulative College Season Stats:** Aggregates all weekly `GAME` payloads from `scout_events` into real season totals:
   * **QB:** Games, comp/att, comp %, pass yards, pass TDs, INTs, passer rating, rush yards.
   * **RB:** Carries, rush yards, YPC, rush TDs, receptions, rec yards, total TDs.
   * **WR/TE:** Targets, catches, rec yards, YPR, rec TDs, catch %.
   * **OT/IOL:** Snaps, sacks allowed, pressures allowed, penalties.
   * **EDGE/DT/LB:** Tackles, TFL, sacks, QB pressures, pass breakups.
   * **CB/S:** Targets, catches allowed, catch %, pass breakups, INTs, tackles.
   * **Specialists:** FGM/FGA, FG %, long, XP, punts, avg, inside 20.
2. **College Game Log:** Chronological table of weekly games with:
   * College week & game type (Regular season, Conference Championship, Bowl, CFP).
   * Opponent school with conference indicator.
   * Full box score statline.
   * Scout's verdict badge (*Shines*, *Struggles*, or *Steady*).
3. **Combine & Athleticism Card:** Visualizes the Step 24 combine drills (40-yard dash, vertical jump, broad jump, 3-cone drill, 225-lb bench press) with percentiles compared to NFL standards.
4. **Scouting Storyline Timeline:** Chronological narrative history of events (e.g., *Week 3 Breakout $\to$ Week 8 Minor Injury $\to$ Biletnikoff Finalist $\to$ Citrus Bowl MVP $\to$ Combine Standout $\to$ Top-30 Visit*).
5. **Scout's Take & Measurables:** True height/weight (from the board's glyph OCR), age, class year, consensus grade tier, and risk/upside traits.

---

### Tab 3: College Honors, Awards & Risers/Fallers
* **College Awards Showcase:** Visual award podiums tracking finalists and winners generated in `scouting_events.py`:
  * Davey O'Brien (QB), Doak Walker (RB), Biletnikoff (WR), John Mackey (TE), Outland Trophy (OL), Chuck Bednarik (DL/EDGE), Butkus (LB), Jim Thorpe (DB), Lou Groza (K), Ray Guy (P).
* **Risers & Fallers Radar:** Top 10 biggest momentum gainers and fallers over the last 4 weeks with the exact college storylines explaining their move.

---

### Tab 4: Team Needs & Draft Capital Matrix
* **32-Franchise Capital Sheet:** Real-time view of all 7 rounds of draft picks owned by each franchise for the upcoming draft (from `draft_picks`).
* **Roster Deficiencies:** Ranked position needs (NeedScore 1–5) calculated live by `get_team_draft_needs`, factoring in sub-75 bridge starters, age cliffs (veterans 30+ without developmental backups), and roster depth.
* **Draft Capital Index:** Highlights teams with surplus capital (e.g., multiple Round 1 picks from trades) vs. pick-depleted teams.

---

### Tab 5: Media Mock Draft (Mel Kiper & Todd McShay)
* **Interactive Round 1 Mock:** Surfaces the media mock draft engine (`engines/mock_draft_engine.py`).
* Displays picks 1–32 with team logos, player selection, fit scores, locked vs. provisional status, and Mel Kiper's signature expert rationale.

---

### Tab 6: Live Draft Day War Room (Broadcast Experience)
The live draft execution engine moved from `#debug` and given a full ESPN/NFL Network broadcast aesthetic:
* **Draft Header & Status:** Current round & pick number, team on the clock, countdown timer, and "THE PICK IS IN" broadcast flashers.
* **On-the-Clock Decision Console:**
  * GM recommendation from their private team board ($T = V + e_{\text{team}} + \text{fit} + \text{bias}$) inside the consensus top 80.
  * Next 3 alternatives with GM rationale.
  * Madden Console Step-by-Step Instructions (Remote Play guidance for user-controlled teams).
* **Pick Execution:**
  * **"Make GM Selection"** (records recommendation).
  * **"Draft Off-Script"** (instant search & select any available prospect from the board).
  * **"Undo Pick"** (single-step safety rollback).
* **Live Draft-Day Trade Alerts:** Pops up when a CPU team proposes a trade-up via `run_draft_day`.
* **Board Switcher:** Ability to toggle the active board view between Consensus, JR's Board, and any of the 32 private team GM boards.
* **Instant Pick Grade:** Displays an immediate grade/verdict (Steal, Value, Reach, Cornerstone) once a pick is locked.

---

## 4. Backend & API Architecture

### A. Dedicated Router: `server/draft_api.py`
Mounted at `/api/draft/*`:
* `GET /api/draft/state`: Returns current draft season, active scouting step, calendar phase, and live draft status.
* `GET /api/draft/board`: Consensus board with positional filters, pagination, mover status, and latest headlines (backed by `scouting_read.consensus_board`).
* `GET /api/draft/prospect/{prospect_key}`: Returns the full player dossier (bio, aggregated college stats, game logs, combine drills, event history, team fit).
* `GET /api/draft/movers`: Returns weekly risers, fallers, and storylines.
* `GET /api/draft/awards`: Returns college award finalists and watchlists.
* `GET /api/draft/team-needs`: Returns all 32 teams' needs, draft capital, and pick inventory.
* `GET /api/draft/mock`: Returns the current week's saved or generated Round 1 mock draft.
* `GET /api/draft/war-room`: Live Draft Room state (slots, on-the-clock pick, team boards, trade proposals).
* `POST /api/draft/war-room/lock-order`: Locks the 224-pick order with owner overrides (admin token required).
* `POST /api/draft/war-room/pick`: Records pick 1–32 (admin token required).
* `POST /api/draft/war-room/undo`: Reverts the last pick (admin token required).

### B. College Stat Aggregator: `engines/college_stats.py`
A pure, testable analytics module that processes stored `scout_events`:
* Aggregates step 1–20 `GAME` event payloads into cumulative collegiate season stats.
* Computes derived metrics (passer rating, completion percentage, yards per carry, target share).
* Formats chronological game logs with opponents, box score lines, and performance labels.
* Extracts combine drill percentiles from step 24 `COMBINE` events against baseline athletic models.

---

## 5. Frontend Component Architecture (`Mu_Network/src/`)

```
Mu_Network/src/
├── DraftPage.tsx                           # Master Draft Central container & tab navigation
├── components/draft/
│   ├── DraftHeader.tsx                     # Top banner, class selector, ticker, return-to-debug
│   ├── ConsensusBigBoard.tsx               # Filterable matrix with movers & trend badges
│   ├── ProspectModal.tsx                   # In-depth dossier: season stats, game log, combine, timeline
│   ├── CombineCard.tsx                     # Spider / percentile visualization of drills
│   ├── CollegeGameLog.tsx                  # Box score table per game
│   ├── CollegeAwardsView.tsx               # Award podiums & watchlists
│   ├── TeamNeedsMatrix.tsx                 # 32-team draft capital & positional needs
│   ├── MockDraftView.tsx                   # Round 1 media mock draft display
│   ├── DraftWarRoom.tsx                    # Broadcast-style live draft room
│   └── draft.css                           # Dedicated styling for Draft Central
```

---

## 6. Phased Implementation Roadmap

1. **Phase 1: Backend Stats Aggregator & Draft API (`server/draft_api.py`, `engines/college_stats.py`)**
   * Implement college stats aggregation and game log compilation.
   * Implement all `/api/draft/*` endpoints.
   * Write test suite in `tests/test_draft_api.py`.

2. **Phase 2: Frontend Draft Central Shell & Big Board (`DraftPage.tsx`, `ConsensusBigBoard.tsx`)**
   * Add `#draft` route in `App.tsx` (without adding a top-level navbar link).
   * Update `DebugPage.tsx` with the "Enter Draft Central →" entry point.
   * Build the Big Board with positional/conference filters, search, and mover badges.

3. **Phase 3: Prospect Dossier & Career Profile (`ProspectModal.tsx`)**
   * Build the modal with cumulative college season totals, weekly game log table, athletic combine radar card, and chronological event timeline.

4. **Phase 4: Honors, Team Needs & Media Mock Draft**
   * Build `CollegeAwardsView.tsx` (Heisman & positional awards).
   * Build `TeamNeedsMatrix.tsx` (roster needs & 3-year pick capital).
   * Build `MockDraftView.tsx` (interactive Round 1 media mock).

5. **Phase 5: Broadcast Draft War Room & Debug Migration**
   * Move live pick execution into `DraftWarRoom.tsx` with broadcast polish, GM advice, pick actions, trade alerts, and instant draft grades.
   * Streamline the draft section in `DebugPage.tsx`.

6. **Phase 6: Verification, Test Coverage & Spec Sync**
   * Run full test suite (`pytest`, Vite build).
   * Update `PROJECT_SPEC.md` repository layout and endpoint documentation.
