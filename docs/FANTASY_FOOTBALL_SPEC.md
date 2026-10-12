# Fantasy Football Agent Swarm - Architecture Spec

## 1. Overview
The Fantasy Football Swarm extends the core Madden Stream Automation pipeline to calculate, project, and generate content around player fantasy performance. It leverages the existing temporal PostgreSQL schema (player ratings, weekly game stats, cumulative season stats) to create a robust, autonomous fantasy football ecosystem.

## 2. Core Components & Data Flow

### A. The Fantasy Scoring Engine (Deterministic Data Layer)
Before agents can reason about fantasy football, raw box scores need to be translated into fantasy points.
- **Function:** A deterministic script/SQL view (rather than an LLM) that calculates standard PPR/Half-PPR scores from the `player_stats` table.
- **Rules:** e.g., 1 pt per 10 rush/rec yards, 1 pt per 25 pass yards, 6 pts per rush/rec TD, 4 pts per pass TD, -2 per interception/fumble, 1 pt per reception.
- **Outputs:** Writes to a new `fantasy_scores` table linked to `game_id` and `player_id`.

### B. The Projection Agent (Analytical)
- **Role:** The analytical brain that determines forward-looking weekly projections and rest-of-season (ROS) outlooks.
- **Inputs:**
  - `player_seasons`: Base attributes (`overall_rating`, `age`, `position`).
  - `season_stats` / `fantasy_scores`: Rolling averages, consistency metrics, and historical baseline.
  - `team_stats` (Opponent): Analyzes the upcoming opponent's defensive metrics (e.g., points allowed to WRs) via the schedule in the `games` table.
- **Process:** Uses a mix of algorithmic weighting and LLM reasoning to output numerical projections and a short confidence summary.
- **Outputs:** Populates a `fantasy_projections` table (`week`, `player_id`, `projected_points`, `confidence_interval`).

## 3. The Consumer Agents (Content Swarm)

These agents sit downstream from the Projection Agent and Scoring Engine, listening for week completions to generate highly contextual fantasy media.

### A. Fantasy Outlook Writer (The "Start/Sit" Expert)
- **Trigger:** Runs at the start of a new in-game week.
- **Context Received:** Top projected players, players with high discrepancies between their `overall_rating` and recent performance, and upcoming favorable match-ups.
- **Output:** Generates a "Start 'Em, Sit 'Em" or "Waiver Wire Adds" article, formatted in Markdown or HTML for the Next.js frontend.

### B. Fantasy Social Media Agent (The "Reactionary" Tweeter)
- **Trigger:** Runs after a `GAME_COMPLETED` event or at the end of a week.
- **Context Received:** `fantasy_scores` for the completed week. Identifies "Boom" (e.g., 30+ points) and "Bust" (e.g., 1st round pick scoring < 5 points) performances.
- **Output:** Generates 5-10 reactionary tweets.
  - *Example:* "If you started P. Mahomes this week, you're feasting 😤 34.5 PPR points! #FantasyFootball"
  - *Example:* "Another dud week for the Cowboys backfield. Time to bench them until further notice 📉"

### C. The ADP & Dynasty Evaluator (The "GM" Agent)
- **Trigger:** Runs periodically (e.g., every 4 weeks) or at the end of a season.
- **Context Received:** `player_seasons.age`, `player_seasons.overall_rating`, total fantasy points scored.
- **Output:** Determines "Buy Low / Sell High" candidates and calculates a simulated Average Draft Position (ADP) for dynasty or redraft leagues. Useful for long-term roster building content.

## 4. Required Schema Updates
To support this new swarm, the following tables/views should be added to the PostgreSQL schema:

- `fantasy_scores`:
  - `id`, `game_id`, `player_id`, `season`, `week`, `ppr_score`, `half_ppr_score`, `standard_score`.
- `fantasy_projections`:
  - `id`, `season`, `week`, `player_id`, `projected_ppr_score`, `notes`.

## 5. Integration with Existing Stack
- **LangGraph Orchestrator:** The main orchestrator node will be updated to include the `Fantasy Scoring Engine` step. Once `game_stat_generator.py` backfills the stats, the orchestrator computes the `fantasy_scores` and broadcasts a `FANTASY_STATS_READY` event.
- **Agent Payload:** The downstream writer agents will receive the newly ranked fantasy context via PostgreSQL window functions (e.g., `RANK() OVER (PARTITION BY position ORDER BY ppr_score DESC)`).
