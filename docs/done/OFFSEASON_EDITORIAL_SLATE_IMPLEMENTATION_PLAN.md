# Project Mu: Offseason Editorial Package Implementation Plan

**Status (2026-10-28):** built 2026-10-16 (`4e02c72`): the six offseason article types and the Week 24 package in [EDITORIAL_MATRIX.md](../EDITORIAL_MATRIX.md), which is now the source of truth.

## 1. Objective

Build an offseason editorial package that follows the season from the Super Bowl through the first round of the next NFL Draft. All six articles are published together in the single interseason publication week after Week 23 and before the next season's Week 1 (internal marker: **Week 24**, labeled **“Offseason”** in the UI).

Although published as one unified package, each story is researched and narrated as if written in the week after its own event (`event_date + 1 week`), enforcing an event-relative information cutoff. The publication week (Week 24) and the story's narrative anchor date are separate concepts: Super Bowl stories never preview future coaching hires, the coaching carousel never leaks free-agency signings, and signings never reference draft picks.

The package covers three Super Bowl follow-ups, a coaching-carousel audit powered by a new coordinator/slant data model and user-submitted moves (detailed in [docs/done/COACH_SLANTS_AND_COORDINATORS_PLAN.md](file:/Users/jrereyi/Downloads/Project%20Mu/docs/done/COACH_SLANTS_AND_COORDINATORS_PLAN.md)), marquee free-agency signing evaluations (editorial facts only), and a first-round-only draft recap based on captured or ingested actual draft results. This document is an implementation specification. The article types described here will be added to `article_types.json` once implementation begins.

---

## 2. Agreed Editorial Decisions

### 2.1 Event-Relative Perspective and Publication Cadence

| Article | Narrative Anchor Date | Publication Edition | National Desk Slot |
|---|---|---|---|
| Super Bowl Game Recap | 1 week post-Super Bowl (mid-Feb) | Week 24 (“Offseason”) | **Hero** |
| Champion Retrospective | 1 week post-Super Bowl (mid-Feb) | Week 24 (“Offseason”) | **Secondary 1** |
| Runner-Up Postmortem | 1 week post-Super Bowl (mid-Feb) | Week 24 (“Offseason”) | **Secondary 2** |
| Coaching Carousel Audit | 1 week post-hiring cycle (late Feb) | Week 24 (“Offseason”) | **Feature 3** |
| Marquee Signing Grades | 1 week post-signing wave (mid-March) | Week 24 (“Offseason”) | **Feature 4** |
| First-Round Draft Recap | 1 week post-draft (early May) | Week 24 (“Offseason”) | **Feature 5** |

The offseason publishes as **one package** once all event inputs (coaching moves, signings, and draft results) are finalized. All six articles reside under the concluded season and `week = 24`. In the frontend UI, narrative dates remain internal prompt anchors rather than separate week tabs—all six stories appear under the unified "Offseason" edition.

### 2.2 Editorial Slot Structure (National Desk Grid)

The Project Mu national desk grid expects: **1 Hero, 2 Secondary, 3 Features** (totaling exactly 6 articles). The package slots into this structure seamlessly:
- **Hero:** `SUPER_BOWL_GAME_RECAP` — The definitive breakdown of the title game.
- **Secondary 1:** `SUPER_BOWL_CHAMPION_RETROSPECTIVE` — The full-season championship arc and identity validation.
- **Secondary 2:** `SUPER_BOWL_RUNNER_UP_POSTMORTEM` — Tactical diagnosis of the loss and the franchise's urgent next step.
- **Feature 3:** `COACHING_CAROUSEL_AUDIT` — Comprehensive audit and letter grades for the hiring cycle.
- **Feature 4:** `FREE_AGENCY_SIGNING_GRADES` — Evaluation of the marquee (90+ OVR) veteran additions.
- **Feature 5:** `NFL_DRAFT_CLASS_RECAP` — First-round draft evaluation, value picks, and organizational bets.

### 2.3 Information Visibility Cascade (Anti-Leakage Rules)

To preserve narrative integrity and historical verisimilitude across the package, prompt contexts enforce a strict event-relative visibility cascade:

```
[Super Bowl (Week 23)]
        │
        ▼  (Sees season + SB game; NO coaching, signings, or draft)
   [1. SB Recap, 2. Champion Retro, 3. Runner-Up Postmortem]
        │
        ▼  (Sees SB + coaching moves; NO signings or draft)
   [4. Coaching Carousel Audit]
        │
        ▼  (Sees SB + coaching moves + signings; NO draft)
   [5. Marquee Signing Grades]
        │
        ▼  (Sees full offseason: SB + coaching + signings + Round 1 picks)
   [6. First-Round Draft Recap]
```

1. **Super Bowl Articles (Recap, Champion, Runner-Up):**
   - Access only concluded season data through Week 23.
   - Forbidden from referencing firings, hirings, free-agent departures/arrivals, or draft projections as completed facts.
2. **Coaching Carousel:**
   - Access concluded season records, team situational efficiency, and submitted coaching/coordinator moves.
   - Forbidden from referencing free-agent signings, trades, or draft selections.
3. **Marquee Signings:**
   - Access concluded season records, updated coaching/coordinator schemes, and submitted signing facts.
   - Forbidden from referencing actual draft results or rookie selections.
4. **Draft Class Recap:**
   - Access full offseason context: coaching staff philosophies, veteran signings, and captured actual Round 1 selections.

### 2.4 Proposed Article Specifications

Writer personas in `newsroom/config/agent_personas.json` provide voice. Type directives set the analytical lens and format.

#### `SUPER_BOWL_GAME_RECAP`
- **Name:** The Super Bowl: How the Game Was Won
- **Writers:** `BARNWELL` (efficiency/regression), `ROSENTHAL` (game flow/watchability), `CHADIHA` (stakes/performance under pressure)
- **Granularity / focus:** One completed game; explain the result through its pivotal turning points and recorded performances.
- **Evidence:** Final and quarter scores; team/player box-score lines; game script when available; team efficiency and situational baselines; Excitement Index as optional context for game drama.
- **Autonomy:** Lead with the most consequential swing. Explain how recorded production and each team's regular-season identity manifested in the title game. Do not invent film observations, quotes, injuries, or play details absent from supplied data.
- **Structure:** Defining turn; how each side performed; consequential individual performances; what decided the championship.
- **Hook:** “Explain how the Super Bowl was decided, identifying the pivotal stretch and the performances that changed the game.”

#### `SUPER_BOWL_CHAMPION_RETROSPECTIVE`
- **Name:** The Champion's Road to the Lombardi
- **Writers:** `CHADIHA` (legacy/narrative), `SANDO` (roster construction), `ROSENTHAL` (season identity)
- **Granularity / focus:** Full-season and postseason arc; demonstrate how regular-season strengths traveled through January.
- **Evidence:** Regular-season and playoff results; player production; game scripts and box scores; coach profile; team efficiency; opponent-adjusted quality; age, under-25 production, draft pedigree, and Playoff Clout where they support specific arguments.
- **Autonomy:** Trace the actual path, not a generic championship narrative. Identify strengths that translated to the playoffs, who carried the run, and whether the title validates a durable foundation or a narrow set of circumstances. Metrics serve as evidence, not required name-drops.
- **Structure:** Regular-season identity; defining playoff tests; players/units that carried the run; what the title changes for the franchise.
- **Hook:** “Trace the champion's path from its regular-season identity to the Super Bowl, and explain what the title validates.”

#### `SUPER_BOWL_RUNNER_UP_POSTMORTEM`
- **Name:** The Super Bowl Loss: What Comes Next
- **Writers:** `SANDO` (front-office accountability), `BARNWELL` (tactical diagnosis/sustainability)
- **Granularity / focus:** Championship exit and offseason horizon; diagnose why the runner-up lost and identify its primary offseason priority.
- **Evidence:** Super Bowl box score and game script; prior postseason games; regular-season efficiency and situational rates; coach profile; roster age/youth context and team needs where relevant.
- **Autonomy:** Diagnose this team's loss, separating game-specific execution from recurring season-long flaws. Ground both in recorded data. Conclude with one clear roster, coaching, or scheme priority. Do not assume unentered personnel moves.
- **Structure:** What the loss exposed; whether the weakness was recurring; championship-window outlook; top offseason mandate.
- **Hook:** “Explain what kept the runner-up from winning the championship and identify the most urgent step toward another chance.”

#### `COACHING_CAROUSEL_AUDIT`
- **Name:** The Coaching Carousel: Hires, Firings, and Grades
- **Writers:** `SANDO` (executive decisions/team windows), `BARNWELL` (performance context/sustainability)
- **Granularity / focus:** Offseason coaching cycle; grade each entered team decision in context.
- **Evidence:** Submitted move facts (outgoing coach, incoming coach, coaching slant, former team/coordinator origin); team record and wins relative to expectation; team efficiency and roster context; coach career profile; inherited vs. imported offensive/defensive schemes (governed by [docs/done/COACH_SLANTS_AND_COORDINATORS_PLAN.md](file:/Users/jrereyi/Downloads/Project%20Mu/docs/done/COACH_SLANTS_AND_COORDINATORS_PLAN.md)).
- **Autonomy:** Cover only submitted moves. Explain the team's need, what the hire addresses, and the tactical/philosophical risks. Assign letter grades supported by concrete rationale. Apply validated tenure/coordinator updates before dispatch so upcoming-season context reflects the correct staff.
- **Structure:** Main carousel pattern; consequential entered moves; strongest decision; highest-risk decision; brief note on material unknowns.
- **Hook:** “Grade the coaching changes entered for this offseason, explaining which teams addressed their needs and which accepted the greatest risk.”

#### `FREE_AGENCY_SIGNING_GRADES`
- **Name:** The Biggest Signings: Fit, Value, and Risk
- **Writers:** `YATES` (role/roster fit), `BARNWELL` (value/downside), `SANDO` (team-building rationale)
- **Granularity / focus:** Curated marquee signings only (short manual list of 2–3 players rated 90+ OVR). Sub-90 signings are not evaluated.
- **Evidence:** Submitted signing facts (player, former team, destination team, user-supplied contract/role notes); player age, experience, position, recorded career production, destination roster composition, and team needs.
- **Strict OVR Rule:** **The article must never state the OVR, quote the numeric rating, or cite OVR as evidence.** Numerical ratings are converted to qualitative tiers prior to model exposure:
  - **95–99 OVR:** *“Tier 1 superstar / elite franchise cornerstone”*
  - **90–94 OVR:** *“High-impact marquee starter / Pro Bowl caliber talent”*
  - *All-Pro Descriptor Rule:* The term "All-Pro" must **never** be used as a generic description based on OVR. It may only be cited if the player actually earned an Official AP All-Pro selection in the database (`awards` table).
- **Autonomy:** Analyze only submitted entries. Grade fit, expected role, and risk. Never infer unstated contract figures or modify database player/roster records.
- **Structure:** Themes across the marquee signings; evaluation and letter grade for each; strongest team fit; greatest downside risk.
- **Hook:** “Grade the marquee signings entered for this offseason, weighing player fit and team need against the risks supported by available information.”

#### `NFL_DRAFT_CLASS_RECAP`
- **Name:** The Draft Is In: First-Round Classes and Biggest Bets
- **Writers:** `MEL_KIPER` (prospect traits/draft-room conviction), `YATES` (team needs/value), `BARNWELL` (capital/opportunity cost)
- **Granularity / focus:** Completed NFL Draft, **Round 1 only** (actual selections, never mock projections).
- **Evidence:** Actual overall pick (1–32), selecting team, selected player; scouted prospect board data; positional need and roster depth; prospect position, college, age, measurements, and pre-draft consensus ranking. Compare actual pick with projected board position only when prospect matching is unambiguous.
- **Autonomy:** Evaluate first-round picks exclusively; never grade or imply coverage of Rounds 2–7, UDFAs, or full draft classes. Focus on a curated set of 4–6 teams/picks illustrating great value, major reaches, scheme fit, or aggressive organizational bets. Actual picks must originate from captured/loaded results, never `mock_draft_picks`.
- **Structure:** First-round macro trends; spotlighted team pick evaluations with letter grades; best value pick; biggest risk/reach; which team most transformed its outlook with its Round 1 selection.
- **Hook:** “Review the captured first-round results, grade the most consequential selections, and identify the picks that most changed a team's outlook.”

---

## 3. Subplan: Coordinator Data Model and Coaching Slants

Before implementing the coaching carousel article, the database and engine architecture must be upgraded to support coordinators and head coaching backgrounds. For the complete, standalone technical specification covering database schemas, baseline seeding, tenure constraints, and scheme inheritance rules, see **[docs/done/COACH_SLANTS_AND_COORDINATORS_PLAN.md](file:/Users/jrereyi/Downloads/Project%20Mu/docs/done/COACH_SLANTS_AND_COORDINATORS_PLAN.md)**. A summary of core tenets is provided below:

### 3.1 Head Coach Slant Classification
Every head coach in the `coaches` table receives a primary orientation:
- `OFFENSIVE`: Play-caller or offensive architect background (e.g. Shanahan, McVay, Canales).
- `DEFENSIVE`: Defensive schemer background (e.g. Ryans, Saleh, Belichick).
- `CEO`: Program manager/culture leader who delegates play-calling and specific schemes to coordinators (e.g. Harbaugh, Campbell).

### 3.2 Coordinator Roster and Database Model
Add support for coordinators (`coordinators` and `coordinator_tenures` tables):
1. **Initial Seed:**
   - Seed actual Madden offensive and defensive coordinators from a user-supplied reference list.
   - For teams without an explicit coordinator named in the list, procedurally generate realistic coordinator profiles matching the team's existing offensive/defensive playbook and scheme.
2. **Schema:**
   - `coordinators`: `id`, `name`, `side` (`OFFENSIVE` | `DEFENSIVE`), `scheme`, `playbook`, `pass_lean` (optional), `blitz_rate` (optional), `man_coverage` (optional).
   - `coordinator_tenures`: `coordinator_id`, `team_id`, `role` (`OC` | `DC`), `start_season`, `end_season` (NULL = current).

### 3.3 Coaching Hiring & Scheme Inheritance Logic
When a head coaching hire occurs:
1. **Origin Tracking:**
   - The hire can be an internal promotion, an external coordinator hire (e.g. *"Colts hire ex-Panthers OC Marcus Whitfield"*), an existing head coach changing teams, or an outside hire.
2. **Scheme Inheritance Rules:**
   - **Offensive Slant Hire:** The incoming coach imports his offensive scheme, playbook, and pass-lean tendency to the new team. The hiring team's **defensive scheme remains unchanged** (retaining existing defensive continuity or coordinator scheme).
   - **Defensive Slant Hire:** The incoming coach imports his defensive scheme, playbook, blitz rate, and man-coverage philosophy. The hiring team's **offensive scheme remains unchanged**.
   - **CEO Slant Hire:** The incoming head coach maintains overall leadership and fourth-down aggressiveness; the offensive and defensive schemes reflect the respective retained or newly hired OC and DC.
3. **Pre-Packaged Facts Packet:**
   - Because an incoming coach begins tenure at `season + 1` while the article runs under the concluded `season`, the carousel runner does not rely on ambiguous single-season DB queries.
   - Instead, the event service pre-packages a structured **Coaching Carousel Facts Packet** that explicitly embeds:
     - Outgoing coach: career record, tenure record with team, concluded season performance, reason/notes.
     - Incoming coach: identity, coaching slant (`OFFENSIVE`/`DEFENSIVE`/`CEO`), previous post (e.g. former team's OC), imported schemes vs. retained team schemes, and historical tendency profile.

---

## 4. Architecture Proposal

### 4.1 Event Runner and Package Manifest

An explicit runner (`newsroom/offseason_run.py`) manages the package. It reads an all-in-one manifest, executes database updates, validates inputs, and dispatches the swarm.

```bash
# Dry-run validation of the entire package
python3 -m newsroom.offseason_run --season 2035 --input captures/offseason/2035/manifest.json --dry-run

# Run a specific sub-event for isolated testing / drafting
python3 -m newsroom.offseason_run --season 2035 --input captures/offseason/2035/manifest.json --only super_bowl
python3 -m newsroom.offseason_run --season 2035 --input captures/offseason/2035/manifest.json --only coaching

# Full package execution and publication
python3 -m newsroom.offseason_run --season 2035 --input captures/offseason/2035/manifest.json
```

### 4.2 All-in-One Manifest Schema (`manifest.json`)

The manifest resides at `captures/offseason/<season>/manifest.json` (gitignored runtime path):

```json
{
  "schema_version": 1,
  "season": 2035,
  "event_id": "offseason-2035-package",
  "publication_week": 24,
  "event_dates": {
    "super_bowl": "2036-02-10",
    "coaching": "2036-02-22",
    "signings": "2036-03-16",
    "draft": "2036-04-26"
  },
  "coaching_moves": [
    {
      "team": "IND",
      "action": "hire",
      "outgoing_coach": "Shane Steichen",
      "incoming_coach": "Marcus Whitfield",
      "incoming_slant": "OFFENSIVE",
      "origin": {
        "team": "CAR",
        "role": "OC"
      },
      "imported_offensive_scheme": "Spread",
      "retained_defensive_scheme": "Base 4-3",
      "effective_season": 2036,
      "notes": "Colts target Whitfield to develop young franchise quarterback after stellar offensive showing in Carolina."
    }
  ],
  "signings": [
    {
      "player_name": "Patrick Surtain II",
      "position": "CB",
      "former_team": "DEN",
      "new_team": "DET",
      "ovr": 96,
      "contract_notes": "4 years, top-of-market guaranteed deal",
      "role_notes": "Immediate shutdown CB1 anchor for secondary"
    }
  ],
  "draft_results": {
    "source_file": "captures/offseason/2035/draft_results.csv"
  }
}
```

### 4.3 Marquee Signings: Editorial Persistence Only

- Manual signing entries **never** alter the `players`, `player_seasons`, or `team_seasons` tables.
- Roster membership changes are owned strictly by the next regular season's Madden roster scan (`madden_capture.py --only roster`).
- Numerical OVR is stripped during prompt compilation; the writer brief receives only the qualitative tier and confirmed database awards (if any).

### 4.4 Draft Results: Ingestion and Fallback

Rather than creating a separate table, actual draft results enrich the existing `draft_prospects` table directly:
- **Columns Added to `draft_prospects` (Mirroring `projected_round`, `round_pick`, `projected_pick`):**
  - `actual_round VARCHAR(8)`: Actual round selected (e.g. '1').
  - `actual_round_pick SMALLINT`: Pick number within that round (1–32 for Round 1).
  - `actual_pick SMALLINT CHECK (actual_pick BETWEEN 1 AND 32)`: Recorded overall selection.
  - `drafted_team_id INT REFERENCES teams(id) ON DELETE SET NULL`: Franchise that made the selection (including trades).
  - `selection_notes TEXT`: Trade details or selection commentary.
  - Partial unique index on `(draft_season, actual_pick) WHERE actual_pick IS NOT NULL` guarantees picks 1–32 are unique per season.
- **Benefits:** Keeps all prospect traits (scouted OVR, measurables, projected pick, college) in one place. Enables instant comparison between projection (`projected_round`, `round_pick`, `projected_pick`) and actual selection (`actual_round`, `actual_round_pick`, `actual_pick`) to highlight risers, fallers, and reaches without table joins, while automatically preserving the downstream rookie `player_id` link.
- **Ingestion Logic:**
  - Match picks 1–32 to `draft_prospects` by normalized `full_name` + `position`. Update `actual_pick`, `drafted_team_id`, and `selection_notes`.
  - If a player selected in Round 1 was unlisted on the scouted board, insert a new `draft_prospects` row with `actual_pick` set and `projected_pick = NULL`.
- **Remote Play Capture:** `python3 -m pipeline.weekly_capture --mode draft-results` navigates to Madden Draft Results screens and extracts `draft_results.csv`.
- **Offline / Manual Fallback:** `python3 -m pipeline.draft_results --file captures/offseason/<season>/draft_results.csv --season <season>` validates and transactionally loads the 32 picks from CSV/JSON without requiring an active Remote Play stream.
- **Validation:** Exactly 32 picks, picks 1–32 present without gaps, valid team IDs, and non-empty player names.

### 4.5 Dispatch and Context Isolation

- `newsroom/run_context.py` is extended to support `offseason_event_cutoff`:
  - When writing Super Bowl articles, tools hide all post-Week 23 database records and all offseason manifest data.
  - When writing Coaching Carousel, tools hide signings and draft selections.
  - When writing Signings, tools hide draft selections.
  - When writing Draft Recap, full offseason data is accessible.
- Articles are saved to `news_feed` with:
  - `season`: Concluded season (e.g. 2035).
  - `week`: 24.
  - `slot`: `Hero`, `Secondary 1`, `Secondary 2`, `Feature 3`, `Feature 4`, `Feature 5`.
  - `article_type`: The respective offseason type.

---

## 5. Implementation Phases

```
┌──────────────────────────────────────────────────────────────┐
│ Phase 0: Contract Audit & Schema Freezing                    │
└──────────────────────────────┬───────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────┐
│ Phase 0.5: Coordinator Model & Coach Slant System (Subplan)  │
└──────────────────────────────┬───────────────────────────────┘
                               │
            ┌──────────────────┴──────────────────┐
            ▼                                     ▼
┌──────────────────────────────┐    ┌──────────────────────────────┐
│ Stream A: Swarm & Catalog    │    │ Stream B: Manifest Service   │
│ - 6 Article Types            │    │ - JSON Manifest Parser       │
│ - Temporal Cutoffs & Slots   │    │ - Coach/Tenure Transactions  │
│ - Editorial Matrix Docs      │    │ - Signing Facts Ingestion    │
└──────────────┬───────────────┘    └──────────────┬───────────────┘
               │                                   │
               │   ┌───────────────────────────────┤
               │   ▼                               ▼
┌──────────────┴───────────────┐    ┌──────────────────────────────┐
│ Stream C: Draft Ingestion    │    │ Stream D: Debug UI / API     │
│ - draft_selections Table     │    │ - Offseason Manifest Form    │
│ - Madden Screen Capture      │    │ - Dry-Run Preview Modal      │
│ - Offline CSV/JSON Loader    │    │ - Execution Job Triggers     │
└──────────────┬───────────────┘    └──────────────┬───────────────┘
               │                                   │
               └──────────────────┬────────────────┘
                                  ▼
┌──────────────────────────────────────────────────────────────┐
│ Phase 2: Serial Integration & Offseason Runner               │
│ - newsroom/offseason_run.py                                  │
│ - Frontend Week 24 ("Offseason") Edition Display             │
│ - PROJECT_SPEC.md Updates                                    │
└──────────────────────────────┬───────────────────────────────┘
                               │
┌──────────────────────────────────────────────────────────────┐
│ Phase 3: End-to-End Verification & Rollout                   │
└──────────────────────────────────────────────────────────────┘
```

### Phase 0: Contract Audit & Schema Freezing
1. Audit week bounds in backend endpoints and frontend week selector.
2. Freeze `draft_selections` table schema and `manifest.json` schema.
3. Establish shared file ownership to prevent merge collisions.

### Phase 0.5: Coordinator Model & Coach Slant System (Subplan)
*Executed per [docs/done/COACH_SLANTS_AND_COORDINATORS_PLAN.md](file:/Users/jrereyi/Downloads/Project%20Mu/docs/done/COACH_SLANTS_AND_COORDINATORS_PLAN.md):*
1. Add migration for `coaches.slant` (`OFFENSIVE`, `DEFENSIVE`, `CEO`).
2. Add `coordinators` and `coordinator_tenures` tables.
3. Seed Madden coordinator baseline list and procedurally fill gaps.
4. Update `engines/coach_profile.py` to expose coordinator context and inherited schemes.

### Phase 1: Parallel Workstreams
* **Stream A (Newsroom & Types):** Add the 6 types to `newsroom/config/article_types.json`; update `newsroom/swarm_dispatcher.py` to handle the offseason event visibility cascade and slot assignments.
* **Stream B (Manifest & Coach Transaction Service):** Implement manifest validation, dry-run preview, and atomic coach tenure/coordinator update logic in `newsroom/offseason_service.py`.
* **Stream C (Draft Capture & Ingestion):** Implement `draft_prospects` actual-pick columns migration, Madden Remote Play capture pass, and offline `draft_results.csv` loader.
* **Stream D (#debug UX & API):** Add `/api/debug/offseason/*` endpoints to preview manifests and trigger package runs; wire up the #debug control interface.

### Phase 2: Serial Integration
1. Implement `newsroom/offseason_run.py` supporting full runs and `--only <event>` partial drafting.
2. Update frontend `Mu_Network/src/App.tsx` so the edition selector recognizes Week 24 as “OFFSEASON” and renders the 1 Hero / 2 Secondary / 3 Feature grid.
3. Update `PROJECT_SPEC.md` and `docs/EDITORIAL_MATRIX.md` to reflect the complete offseason architecture.

### Phase 3: End-to-End Verification
1. Run end-to-end dry-run with a complete test manifest.
2. Verify that Super Bowl stories contain zero post-game leakage.
3. Verify that the coaching article reflects imported coordinator schemes.
4. Verify that marquee signing articles contain zero numeric OVR citations and only use "All-Pro" when substantiated by database records.
5. Verify that draft picks 1–32 match actual results and cover Round 1 only.

---

## 6. Acceptance Criteria

- **Unified Publication:** All six articles publish together under concluded season $N$, `week = 24`, labeled “Offseason” in the newsroom edition selector.
- **Grid Compliance:** Articles map exactly to the National Desk grid: 1 Hero (`SUPER_BOWL_GAME_RECAP`), 2 Secondary (Champion Retro, Runner-Up Postmortem), and 3 Features (Coaching Carousel, Signings, Draft Recap).
- **Anti-Leakage Enforcement:** Temporal context strictly prevents chronological leakage between offseason stages.
- **Coordinator Logic:** Database supports coach slants, OC/DC coordinators, and scheme inheritance upon hiring.
- **Roster Safety:** Manual free-agent signing inputs are strictly editorial and produce no mutations on player, roster, or team season records.
- **Strict OVR Compliance:** No signing article states, quotes, or cites numeric OVR. "All-Pro" is never used as an OVR proxy and only appears when verified in the `awards` table.
- **Draft Integration:** Actual Round 1 selections enrich `draft_prospects` directly via `actual_pick` (1–32) and `drafted_team_id` (distinct from mock draft predictions in `mock_draft_picks`); offline CSV/JSON loading is fully supported.
- **Resumability & Idempotency:** Reruns with the same manifest do not duplicate articles or corrupt coach tenures. Partial runs via `--only <event>` operate cleanly for testing.
- **Spec Currency:** `PROJECT_SPEC.md` matches the shipped architecture before the feature branch merges to main.

---

## 7. Non-Goals

- No database ledger for free-agent signings and no roster change from manual signing input.
- No full-league free agency tracking; only a curated marquee batch (2–3 players, 90+ OVR).
- No draft coverage beyond Round 1.
- No weekly cadence or Agent Wire/Fantasy slates during the offseason; publication occurs as one package under Week 24.
- No altering in-season game-driven pipelines.
