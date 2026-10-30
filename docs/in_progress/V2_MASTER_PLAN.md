# Project Mu: V2 Master Plan — The Autonomous Gridiron Universe

**Status (2026-10-29):** Approved architecture. Split from [`PC_MIGRATION_PLAN.md`](./PC_MIGRATION_PLAN.md) to serve as the master specification for all post-migration features, universe agency, front-office control, and the headless Madden architecture.
**Prerequisite:** The PC environment, save reader/writer, and Docker Postgres are operational ([`PC_MIGRATION_PLAN.md`](./PC_MIGRATION_PLAN.md) §3, §5).
**Strategic Decision (JR, 2026-10-29):** The live **2034 canonical franchise run is held until V2 systems are fleshed out and tested** on scratch/sample saves (`CAREER-WEEK1/2/3` and `CAREER-GIANTS-AUTOSAVE`). Testing V2 first ensures that when the permanent 2034 league begins, progression, coach-set depth charts, and CBA contract mechanics are fully autonomous and controlled by Project Mu rather than Madden's native CPU algorithms.

---

## 1. Executive Summary: Headless Madden & Project Mu as the NFL OS

In the PS5 era (v1), Project Mu was an observational chronicler: Madden ran the simulation and made all front-office decisions, while Project Mu passively parsed screens via OCR to tell stories.

In **V2**, the relationship is completely inverted:
* **Project Mu is the Operating System of the NFL Universe:** It owns all league state, player development, deterministic analytics, trades, contract negotiations, free agency, the college draft, weekly game planning, and depth charts across all 32 teams.
* **Madden 27 is a Headless 60-Minute Physics Engine:** Madden is never used for menus, franchise management, or CPU transactions. Its sole function is to load a pre-configured save, simulate or play the 60 minutes of on-field football physics, write the resulting stats to disk, and exit.

| Dimension | Legacy PS5 Setup (v1) | V2 Architecture |
| :--- | :--- | :--- |
| **Primary System Authority** | Madden Franchise Engine | **Project Mu Platform** |
| **Front Office & Roster Moves** | Madden CPU / Manual controller clicks | **Project Mu Autonomous GM Agents & Engines** |
| **Depth Charts** | Madden auto-sorting | **Project Mu Coach AI (32 Teams) + User Command Center** |
| **Player Development** | Madden XP sliders | **Deterministic Mu Progression Engine** ([`engines/progression.py`](../../engines/progression.py)) |
| **Contract Rules & Economics** | Madden arcade contracts | **Strict Real-Life NFL CBA Mechanics** (Tags, 5th-Year Options, Dead Money) |
| **Free Agency** | One-click instant accept | **Multi-Dimensional Agent Negotiation & Utility Scoring** |
| **Game Day Experience** | Navigating Madden menus | **One-click "Play Game" from `Mu_Network` Command Center** |

---

## 2. The Franchise Command Center (`Mu_Network`)

Rather than relying on Madden's franchise screens, the React 19 + Vite frontend (`Mu_Network`) features a dedicated, comprehensive **Franchise Command Center** (e.g. `/franchise`). This serves as the user's personal war room as Head Coach and General Manager.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          FRANCHISE COMMAND CENTER                               │
│  [Game Center]  [Depth Chart]  [Roster & Cap]  [Free Agency]  [Contract Room]   │
│                 [Trade Machine]  [Draft & Scouting]  [Training Room]            │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Module 1: Game Center (Weekly Opponent Prep & One-Click Launch)
* **Opponent Scouting Dossier:** Record, offensive/defensive rankings (EPA, pass/rush splits), personnel groupings, weather conditions, and the newsroom’s matchup primer.
* **Matchup Breakdown:** Head-to-head positional battles (e.g. your Pass Rush vs their LT, your Slot WR vs their Nickel CB).
* **Game Strategy & Focus:** Set weekly offensive/defensive focuses (e.g. Blitz-heavy vs Cover-3; Inside Zone vs Deep Shot; stamina/fatigue management).
* **The "Play Game" Trigger:**
  1. Pre-flight check: validates and writes pending depth charts, roster moves, and injuries to the save.
  2. Compiles and activates the game's tailored mod pack via [`pipeline.mod_packs`](../../pipeline/mod_packs.py) (CBS/FOX/Primetime presentation and scorebugs, crowd density tier, mudded or BCA pink ball).
  3. Triggers `MMCModManager.exe -launch <pack>`.

### 2.2 Module 2: Depth Chart & Situational Roles
* **Visual Interactive Grid for All 35 Positions:**
  - **Base Offense/Defense:** QB, HB, FB, WR (WR1, WR2, WR3), TE, LT, LG, C, RG, RT, LE, RE, DT, LOLB (SAM), MLB (MIKE), ROLB (WILL), CB, FS, SS.
  - **Situational / Specialists:** 3rd Down RB (3DRB), Slot WR (3DWR), Slot CB, Sub LB, Rush LE, Rush RE, Rush DT, Kick/Punt Returners, Long Snapper.
* **Drag-and-Drop / Click-to-Swap:** Real-time visual re-ordering.
* **Coach AI Auto-Sort:** "Let Staff Set" button runs deterministic coaching logic based on scheme fit, stamina, and coach philosophy (from [`PERSONALITY_PLAN.md`](./PERSONALITY_PLAN.md)).
* **Direct Save Sync:** Writes directly to `Team.DepthChart` (each of the 35 positions points to a 6-slot `Player[]` record in the save).

### 2.3 Module 3: Roster Construction & Cap Sheet
* **Roster Sections:** Active 53/65 Roster, Practice Squad (12–16 slots), and Injured Reserve (IR).
* **Financial Cap Sheet (Live & 5-Year Outlook):** Real-time breakdown of Total Cap, Cap Room, Salaries, Dead Money, and Rookie Reserve ([`server/front_office_api.py`](../../server/front_office_api.py)).
* **Roster Moves with Financial Safety Warnings:**
  - **Cut Player:** Displays exact **Cap Savings** vs **Dead Money Penalty** before confirmation. Warns if dead money acceleration results in negative cap savings.
  - **Practice Squad:** Promote to active roster or demote to practice squad (with eligibility validation).
  - **Injured Reserve:** Place eligible injured players on IR to open active roster spots.

### 2.4 Module 4: Free Agency & Waivers
* **Searchable Market Browser:** Filter all unassigned players across the league by position, OVR, age, scheme fit, and dev trait.
* **Interactive Negotiations:** Not a one-click transaction. Free agents negotiate based on financial offers, depth chart opportunity, and team competitiveness (see §3 below).
* **Waiver Wire:** Weekly waiver claims processed in inverse standings order before players clear to unrestricted free agency.

### 2.5 Module 5: Contract Room (In-Season Extensions & Cap Management)
* **Expiring Contracts Watchlist:** Highlights players in their contract year eligible for extension under NFL CBA rules.
* **Interactive Agent Negotiation:** Propose years, total money, and signing bonus. Player agents evaluate offers in real time and return acceptances, rejections, or counter-demands.
* **Restructure Tool:** Convert non-guaranteed base salary into signing bonus to create immediate cap space, showing the exact future dead money risk.

### 2.6 Module 6: Trade Machine & Trade Block
* **Two-Way Trade Machine:** Add players and future draft picks (next 3 drafts) from both sides.
* **Real-Time Surplus Value Bar:** Powered by [`engines/trade_value.py`](../../engines/trade_value.py), evaluating player surplus value ($V_T$) and pick curves.
* **Cap Verification:** Confirms both teams can legally absorb the incoming contracts and charges dead money to the trading team.
* **AI GM Dialogue:** AI GMs review proposals using personality traits, team direction (Contend / Retool / Rebuild), and positional needs ([`engines/gm_negotiation.py`](../../engines/gm_negotiation.py)).
* **Trade Block:** Place user players on the block and view CPU trade targets and rumors.

### 2.7 Module 7: College Scouting & Draft War Room
* **Consensus & Team Scouting Boards:** Track scouted prospects, archetype grades, combine metrics, and scout notes ([`engines/scouting_boards.py`](../../engines/scouting_boards.py), [`server/draft_api.py`](../../server/draft_api.py)).
* **Draft Capital Ledger:** Track all owned picks across 7 rounds for the next 3 draft classes.
* **Live War Room (Offseason):** Interactive draft board to make user picks while AI GMs execute deterministic picks based on their team boards.

### 2.8 Module 8: Training Room & Medical
* **Detailed Medical Ledger:** Track injury types, recovery timelines (weeks remaining), and game status (Out, Doubtful, Questionable).
* **Return-to-Play Management:** Clear healed players or manage snap counts.

---

## 3. Real-World NFL CBA Contract & Economic Mechanics

Project Mu abandons simplistic video-game contract models in favor of canonical NFL Collective Bargaining Agreement (CBA) rules:

### 3.1 Strict Extension Windows (Eligibility)

| Player Category | Real-Life NFL CBA Rule | Project Mu Rule |
| :--- | :--- | :--- |
| **Drafted Rookies** | Cannot renegotiate or extend until after the regular season of their **3rd NFL season** (CBA Article 7, Section 3(k)). | Extensions blocked until the offseason entering Year 4. 5th-year option decision occurs after Year 3. |
| **Undrafted Rookies** | Cannot renegotiate or extend until after their **2nd NFL season**. | Extensions blocked until after Year 2 regular season. |
| **Veterans (Multi-Year Deals)** | Cannot renegotiate until at least **12 months** have passed since signing or last restructure, OR when entering their **final contract year**. | Extensions allowed only in the final year of the deal or once 12 months have elapsed. |
| **Players on 1-Year Contracts** | Cannot be extended mid-season until the final week of the regular season / offseason. | Re-signing negotiations unlock in Week 18. |
| **Tagged Players** | Multi-year extension window closes in mid-July; otherwise locked into the 1-year tag tender. | If no deal is reached before training camp, player plays on tag for that season. |

### 3.2 Contract Types & Instruments

1. **Standard Multi-Year Deal:**
   - **Length:** 1 to 5 years (matching save capabilities).
   - **Base Salary ($P$):** Paid weekly across the 18-week season.
   - **Signing Bonus ($B$):** Cash paid upfront, prorated evenly against the cap over the lesser of contract length or **5 years**.
   - **Incentives:** Likely to Be Earned (LTBE, counts on current cap) vs Not Likely to Be Earned (NLTBE, counts against next year's cap if achieved).
2. **5th-Year Option (1st Round Picks Only):**
   - Exercised between end of Year 3 and May 2nd of the 4th offseason. 100% fully guaranteed.
   - **Salary Tiers:**
     - *Tier 1 (Multiple Pro Bowls):* Franchise Tag value at the position.
     - *Tier 2 (1 Pro Bowl):* Transition Tag value.
     - *Tier 3 (Playtime criteria: 75%+ snaps in 2 of 3 years):* Average of 3rd–20th highest salaries.
     - *Tier 4 (Basic):* Average of 3rd–25th highest salaries.
3. **Franchise & Transition Tags:**
   - Applied in the late February / early March window.
   - *Exclusive Franchise Tag:* Top 5 average salary in current year, or 120% of previous salary. Player cannot negotiate with other clubs.
   - *Non-Exclusive Franchise Tag:* Top 5 average over past 5 years as % of cap, or 120% of prior salary. Other teams can sign to an offer sheet; original team gets two 1st-round picks if they decline to match.
   - *Transition Tag:* Top 10 average salary; right of first refusal, no draft pick compensation.
4. **RFA & ERFA Tenders:**
   - *Exclusive Rights (ERFA, $\le 2$ accrued seasons):* 1-year league minimum tender; player must sign or sit out.
   - *Restricted Free Agent (RFA, 3 accrued seasons):* 1st-round, 2nd-round, or Right-of-First-Refusal tenders.
5. **Restructure Mechanics:**
   - Converts up to 90% of base salary into signing bonus, spreading the cap charge into future years to create immediate cap space, while increasing future dead money liability.

### 3.3 Cap Mathematics & Dead Money Acceleration

Madden saves store money in units of **$10,000** (e.g. $15,000,000 = `1500`). The cap ledger strictly enforces:

$$\text{Cap Hit}_t = \text{Base Salary}_t + \text{Prorated Signing Bonus}_t + \text{LTBE Incentives}_t$$

$$\text{Dead Money (Pre-June 1)} = \sum_{k=t}^{\text{end}} \text{Prorated Signing Bonus}_k + \text{Guaranteed Base Salary}_t$$

$$\text{Cut Savings}_t = \text{Cap Hit}_t - \text{Dead Money}_t$$

If $\text{Cut Savings} < 0$, cutting or trading the player reduces available cap space immediately.

---

## 4. Free Agency Negotiation Engine

Free agency is **never a one-click sign**. Both in-season free agents and offseason free agents evaluate offers through a multi-dimensional utility function:

$$\text{Offer Score } (U) = w_{\text{money}} \cdot S_{\text{financial}} + w_{\text{role}} \cdot S_{\text{opportunity}} + w_{\text{team}} \cdot S_{\text{contention}} + w_{\text{scheme}} \cdot S_{\text{scheme}} + \epsilon_{\text{persona}}$$

### 4.1 Evaluation Dimensions

1. **Financial Value ($S_{\text{financial}}$):**
   - Evaluated against market worth $W(\text{ovr}, \text{group})$ from [`engines/trade_value.py`](../../engines/trade_value.py).
   - **Guaranteed Money Ratio:** Share of total contract value guaranteed. Veterans heavily penalize non-guaranteed backloaded deals.
   - **Term Fit:** Aging veterans (30+) demand multi-year security; prime players (24–27) prefer shorter deals to test free agency again.
2. **Role & Depth Chart Opportunity ($S_{\text{opportunity}}$):**
   - The engine checks where the player would sit on the acquiring team's depth chart:
     - Clear Starter ($u = 1.0$) $\rightarrow$ Full score.
     - Situational / 3rd Down Specialist ($u = 0.65$) $\rightarrow$ Minor penalty.
     - Deep Bench / Backup ($u = 0.3$) $\rightarrow$ Severe penalty.
   - *Rule:* A starting-caliber player (e.g. 80+ OVR) will reject an offer to be a backup unless paid a massive financial premium.
3. **Team Contention & Prestige ($S_{\text{contention}}$):**
   - Based on team win percentage, playoff odds, and franchise popularity (`team_seasons.popularity_index`).
   - Contenders receive a "contender discount"; struggling franchises must pay a "rebuilder tax".
4. **Player Persona Traits ($\epsilon_{\text{persona}}$):**
   - Integrated with [`newsroom/config/persona_traits.json`](../../newsroom/config/persona_traits.json) and [`PERSONALITY_PLAN.md`](./PERSONALITY_PLAN.md):
     - **`Ring Chaser`:** $w_{\text{team}}$ tripled; gladly accepts below-market 1-year deals on top contenders.
     - **`Bag Chaser / Mercenary`:** $w_{\text{money}}$ dominates completely; guarantees are prioritized above all else.
     - **`Scheme Purist`:** Requires high scheme fit ($\ge 85\%$).
     - **`Ego / Prideful`:** Rejects backup designations regardless of financial compensation.

### 4.2 In-Season Free Agents vs. Offseason Waves
* **In-Season "Street" Free Agents:** The bar to accept is lower (players want to play and stay in the league), but they still evaluate respect, minimum financial floors, and role expectations.
* **Offseason Bidding Waves:** CPU teams actively evaluate needs and place competing bids across Wave 1 (superstars), Wave 2 (quality starters), and Wave 3 (depth), creating genuine bidding wars.

---

## 5. The Automated Game Lifecycle Orchestrator

```mermaid
flowchart TD
    subgraph Browser ["Mu_Network (Frontend)"]
        A["Command Center: Review Matchup & Adjust Depth Chart"] --> B["Click 'Play Game'"]
    end

    subgraph Pre_Flight ["Pre-Flight Orchestration"]
        B --> C["POST /api/franchise/game/launch"]
        C --> D["Inject Depth Charts (32 teams) & Roster Moves into Save"]
        D --> E["Compile Tailored Mod Pack (pipeline/mod_packs.py)"]
        E --> F["Launch: MMCModManager.exe -launch <pack>"]
    end

    subgraph Madden_Game ["Madden NFL 27 (PC)"]
        F --> G["Play User Game (or CPU vs CPU Spectate)"]
        G --> H["Save & Quit to Desktop"]
    end

    subgraph Post_Game ["Automated Post-Game Pipeline"]
        H --> I["Process Monitor detects Madden27.exe termination"]
        I --> J["Save Watcher detects debounced CAREER-* file update"]
        J --> K["Ingest Game Box Scores, Snap Counts & Team Stats"]
        K --> L["Execute Deterministic Analytics (Excitement, TWI, POP)"]
        L --> M["Dispatch Newsroom Swarm & Agent Wire Social Desk"]
        M --> N["Update Database & Refresh Mu_Network Dashboard"]
    end
```

1. **User Action:** You finalize depth charts and game strategy in `Mu_Network`, then click **Play Game**.
2. **Pre-Flight Save Injection:** The backend writes all 32 team depth charts, contracts, and roster moves directly to the save file.
3. **Mod Pack Activation & Launch:** [`pipeline.mod_packs`](../../pipeline/mod_packs.py) configures the active broadcast presentation (CBS/FOX/TNF/SNF/MNF), crowd tier, and ball type, then executes `MMCModManager.exe -launch <pack>`.
4. **Gameplay:** You play the game in Madden. On completion, simply save and exit the game to desktop.
5. **Autonomous Ingestion & Newsroom Dispatch:**
   - The process monitor ([`pipeline/gpu_queue/process_check.py`](../../pipeline/gpu_queue/process_check.py)) detects `Madden27.exe` has closed.
   - [`pipeline/watch_save.py`](../../pipeline/watch_save.py) ingests the save, executes deterministic engines, queues nightly batch jobs, and triggers the newsroom journalists.
   - `Mu_Network` refreshes with completed box scores, postgame quotes, articles, and updated standings.

**Not yet true: player lines can't be read straight after the game (§7.0, B1).** In `CAREER-WEEK1/2/3` ([`PC_MIGRATION_PLAN.md`](./PC_MIGRATION_PLAN.md) §5.4, check 3), each save was made after the week's 15 CPU games were simmed and before CAR's game. The final score, quarter scores and team box score (`SeasonGame` and its `TeamStatCache`) were readable for every game already played, this week's included. The player lines (`Game*Stats`) for this week's games were in their tables too, but had a null `SeasonGame` and no `Player.GameStats` link. The reader reaches player lines only through that link, so it can't credit them. The next week's save had them linked. So `CAREER-WEEK3` holds week 1 and week 2 player lines but not week 3's. The saves can't show *what* links them: Advance Week, or the week's last game finishing. Until a PC test answers that, the ingest just after "Save & Quit" can load the score and team stats but not the player lines, and the newsroom would write recaps without player stats. Possible fixes, depending on the test: ingest after the week advances (Project Mu triggers the advance, or the user does), or attribute the unlinked rows directly if they carry enough to find their player and game (§7.0, O1).

---

## 6. The V2 Feature Suite (Detailed Catalog)

### V2.0 Ground Rules
* **Numbers Stay Deterministic:** Engines decide values, ratings, deals, draft boards, and outcomes. Language models write dialogue, rationale, and choose among engine-permitted options.
* **Strict Hallucination Policy:** Narrative color is encouraged, but factual contradictions (stats, scores, names, contracts) are strictly banned.
* **Resource Separation:** Batch jobs run on the nightly GPU queue ([`pipeline/gpu_queue/`](../../pipeline/gpu_queue/)); interactive tasks use ultra-compact models or cloud fallback.
* **Quality Gate:** Every feature must pass side-by-side evaluation ([`tools/llm_compare.py`](../../tools/llm_compare.py)) before deployment.

### V2.1 Project Mu News Inside Madden
Inject top Project Mu headlines directly into Madden's `NewsCenterArticle` and `NewsCenterArticleData` save tables, displaying the universe's headlines inside Madden's in-game menus.

### V2.2 Interactive Press Conferences
Postgame press conferences where newsroom personas question JR based on real box-score events, scoring swings, and player usage. JR's answers become quotes in weekly articles and update reporter memories ([`newsroom/agent_memory.py`](../../newsroom/agent_memory.py)).

### V2.3 Authentic Player Voices
* Postgame quotes generated for all 16 weekly games from real box-score stat lines.
* Player accounts on Agent Wire expressing celebration, frustration, contract-year focus, or injury updates in distinct voices.

### V2.4 Photography with Consistent Faces
Use local image generation with IP-Adapter / face-ID adapters to generate photojournalism featuring consistent player faces across draft night, award ceremonies, press conferences, and feature articles.

### V2.5 Fan Ecosystem & Team Sentiment
Agent fans reacting to game outcomes, generating article comments, team forum threads, and driving a dynamic team sentiment index that impacts coach hot seats and ticket demand.

### V2.6 Radio Shows & Podcasts
Automated weekly audio recaps and podcasts voiced with local TTS models, produced overnight on the nightly GPU queue and published to `Mu_Network`.

### V2.7 Complete League Game Recaps
Detailed narrative recaps for all 16 games every week generated from native box scores and scoring summaries.

### V2.8 League Memory (Built)
PostgreSQL-backed narrative search index ([`newsroom/league_memory.py`](../../newsroom/league_memory.py)) enabling journalists and agents to pull authentic historical callbacks ("last time Carolina won 5 straight was 2031").

### V2.9 Front-Office Inbox & Direct Conversations
Interactive messaging system allowing JR to communicate with team owners, player agents, rival GMs, beat reporters, and college prospects.

### V2.10 Scouting Revamp & Combine Interviews
Comprehensive narrative reports for every draft prospect, individual scout voices with regional biases, and interactive combine/visit interviews.

### V2.11 Live Multi-Turn Trade Negotiations
Interactive negotiation loops with AI GMs featuring counter-offers, trade posturing, leaks to media insiders, and published negotiation transcripts for CPU-to-CPU deals.

### V2.12 Long-Term Team and League Narratives
Persistent narrative arc tracker following season-long storylines (rookie QB development, dynasty defense decline, coach hot seats, contract holdouts).

### V2.13 Personalities Across the NFL Universe (Built, Phases 1–3)
50-trait validated personality system ([`newsroom/config/persona_traits.json`](../../newsroom/config/persona_traits.json)) extending numeric behavioral sliders to players, coaches, GMs, and scouts.

### V2.14 Project Mu Owns Progression
Madden XP progression sliders set to 0. All player attribute development, stagnation, and regression calculated deterministically by [`engines/progression.py`](../../engines/progression.py) and written straight to the save file.

### V2.15 Project Mu Runs the League (Headless League Office)
Full front-office automation for all 32 teams across trades, signings, cuts, draft picks, and coach hirings, executing moves via the save writer ([`LEAGUE_OFFICE_PLAN.md`](../not_started/LEAGUE_OFFICE_PLAN.md)).

---

## 7. Implementation & Testing Roadmap

### 7.0 Dependencies on Other In-Progress Work (checked 2026-10-30)

Phases 1–3 below can start now. These items depend on unfinished work elsewhere:

**Blockers**

| # | What it blocks | Waiting on |
| :--- | :--- | :--- |
| B1 | §5 post-game ingest (step K) and Phase 4 | (a) What links a week's player lines (§5 note above): sim or play one game, save, read; then finish the week and advance, and read again. (b) Whether Madden writes the save during a game or only on Save & Quit: run [`tools/save_write_logger.py`](../../tools/save_write_logger.py) on the PC through one played game. |
| B2 | V2.14 (Project Mu owns progression) | [`PROGRESSION_PLAN.md`](./PROGRESSION_PLAN.md): phase A (PC checks: XP sliders at 0 stop all changes, Madden keeps written ratings), the rest of phase C2, phase D (engine writes the save), decision D13 (how form reaches the game). Plus the OVR-recompute-on-load check ([`PC_MIGRATION_PLAN.md`](./PC_MIGRATION_PLAN.md) §5.4, check 7), and moving the helpers `engines/progression.py` imports out of the obsolete `pipeline/teleport.py` ([`AUDIT_LEDGER.md`](../../AUDIT_LEDGER.md) §3b; PC plan §10, item 1). |

**Partial blockers** (can ship first with the dependent term at 0, or later)

| # | What it affects | Waiting on |
| :--- | :--- | :--- |
| P1 | §2.2 "Let Staff Set" (coach philosophy) and §4.1 persona weights | [`PERSONALITY_PLAN.md`](./PERSONALITY_PLAN.md) phases 4–6 and progression plan phases F–G (player and coach traits). |
| P2 | V2.15 (headless league office) | [`LEAGUE_OFFICE_PLAN.md`](../not_started/LEAGUE_OFFICE_PLAN.md): not started. |
| P3 | §2.1 / §5 "Play Game" pre-flight mod pack | Wiring `pipeline.mod_packs.write` into `watch_save.py` (PC plan §10, item 3). |
| P4 | V2.4 portraits in-game | The one-portrait spike (PC plan §9.4, §10 item 2). |
| P5 | Trust in all scratch-save results | Every §5.4 check was on unmodded `CAREER-WEEK1/2/3`; sim-stat and roster mods change the numbers, so the checks are re-run on a modded save before the 2034 start. |
| P6 | Phase 1 `depth_chart.mjs` | The "35 positions × 6 slots" `Team.DepthChart` layout is from this plan, not yet read from a save; check it on `CAREER-WEEK3` first. |

**Open items** (not blocking; ideas that depend on B1)

| # | Item | Notes |
| :--- | :--- | :--- |
| O1 | Link this week's unlinked player lines ourselves | The game is easy to find: each unlinked row carries `OpposingTeam`, and a team plays once a week, so opponent + week names the game (its score and team box score are already readable). The player is the open question: the reader reaches stat rows only through `Player.GameStats`, so no one has checked whether an unlinked row holds its own player reference. Check on `CAREER-WEEK3` on the PC. If it has none, look for another route (row order following the roster, or lines whose totals add up to the team box score). If O1 works, B1's player-line half goes away: the postgame ingest can credit your own game's lines straight after Save & Quit. |
| O2 | "Around the league" by kickoff window | Madden sims all CPU games of a week together, before the user's game, whatever their scheduled kickoff. Project Mu could reveal only the games whose kickoff is earlier than the user's (Thursday night, then the early and late Sunday windows before a Sunday night game) and hold the rest until after it: their scores and team box scores in the Game Center (§2.1) and newsroom, in kickoff order. Works today with scores and team stats; player stat lines need O1. |

### 7.1 Phases

All development and testing will occur on the PC using existing scratch saves (`CAREER-WEEK3` on `pc-wsl`):

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Phase 1: Save Writer Primitives (depth_chart.mjs, sign.mjs, trade.mjs)          │
│                                      │                                          │
│                                      ▼                                          │
│ Phase 2: Engine & CBA Logic (engines/contracts_engine.py, free_agency_engine.py)│
│                                      │                                          │
│                                      ▼                                          │
│ Phase 3: Backend API Endpoints (server/franchise_api.py)                        │
│                                      │                                          │
│                                      ▼                                          │
│ Phase 4: Lifecycle Automation (pipeline/watch_save.py game monitor)             │
│                                      │                                          │
│                                      ▼                                          │
│ Phase 5: Frontend Franchise Command Center (Mu_Network React UI)                │
└─────────────────────────────────────────────────────────────────────────────────┘
```

1. **Phase 1: Save Writer Primitives (`pipeline/save_reader/`):**
   - Build `depth_chart.mjs` (read/write 35 position depth charts, 6 slots each).
   - Build `sign.mjs` and `trade.mjs` (completing roster moves alongside `release.mjs`).
   - Verify on `CAREER-WEEK3` with full read-back integrity.
2. **Phase 2: Economic & CBA Engines:**
   - Implement `engines/contracts_engine.py` (extension eligibility, 5th-year option tiers, franchise tags, dead money math).
   - Implement `engines/free_agency_engine.py` (multi-dimensional player offer evaluation utility).
3. **Phase 3: Backend Franchise API (`server/franchise_api.py`):**
   - Endpoints for depth chart management, roster cuts/promotions, free agency offers, contract negotiations, trade evaluations, and the game launch trigger.
4. **Phase 4: Game Lifecycle Automation:**
   - Enhance [`pipeline/watch_save.py`](../../pipeline/watch_save.py) and [`pipeline/gpu_queue/process_check.py`](../../pipeline/gpu_queue/process_check.py) to automatically ingest upon `Madden27.exe` process termination.
5. **Phase 5: Frontend Franchise Command Center:**
   - Build the complete `/franchise` dashboard in `Mu_Network`, integrating all 8 modules.
