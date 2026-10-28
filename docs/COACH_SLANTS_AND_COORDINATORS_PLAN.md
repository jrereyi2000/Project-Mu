# Project Mu: Coach Slants and Coordinators Implementation Plan

**Status (2026-10-28):** built. Coordinators, slants and staff profiles landed 2026-10-16 (`engines/coordinator_seeder.py`, `5311ed3`); the full coaching carousel with coordinator poaching 2026-10-26 (`engines/coaching_carousel.py`, `3e4d3d9`). PROJECT_SPEC describes the current system.

## 1. Executive Summary

This plan defines the architecture for tracking **coaching slants** (`OFFENSIVE`, `DEFENSIVE`, `CEO`) and a full **coordinator infrastructure** (Offensive Coordinators and Defensive Coordinators) across the 32 NFL franchises.

Currently, `coaches` tracks only head coaches with static offensive and defensive schemes, and tenures are head-coach-only (`coach_tenures`). In real football and in Madden, head coaches specialize on one side of the ball, coaching carousels frequently hire top coordinators from other franchises (e.g., *“Colts hire ex-Panthers OC Marcus Whitfield”*), and hiring an offensive head coach changes the offense while defensive continuity is preserved under a defensive coordinator (or vice versa).

This plan introduces:
1. **Coach Slants:** Classifying every head coach as `OFFENSIVE`, `DEFENSIVE`, or `CEO`.
2. **Coordinators & Staff Tenures:** Adding `coordinators` and `coordinator_tenures` tables to record active OCs and DCs per team per season.
3. **Madden Coordinator Ingestion:** Seeding actual Madden coordinators from a user-supplied reference list, with procedural generation for unlisted slots matching existing team playbooks.
4. **Hiring Dynamics & Scheme Inheritance:** Modeling how coordinator poaching works, which schemes follow the coach to the new team, and how the opposite side of the ball retains continuity.
5. **Pre-Packaged Context for Writers:** Exposing staff trees and coordinator origin facts to writer tools and the `COACHING_CAROUSEL_AUDIT` article type.

---

## 2. Core Concepts & Taxonomy

### 2.1 Head Coach Slant

Every head coach possesses an overarching operational orientation:

| Slant | Background / Role | Playbook & Scheme Impact | Decision Ownership |
|---|---|---|---|
| `OFFENSIVE` | Former OC or offensive architect (e.g. Shanahan, McVay, Canales). | **Dictates team offensive scheme and playbook.** Imports passing lean. Defensive scheme managed by DC. | Offense play-calling, clock management, 4th-down go-rates. |
| `DEFENSIVE` | Former DC or defensive architect (e.g. Ryans, Saleh, Belichick). | **Dictates team defensive scheme, front (3-4/4-3), and coverage.** Imports blitz/man tendencies. Offensive scheme managed by OC. | Defense play-calling, situational timeouts, 4th-down go-rates. |
| `CEO` | Program leader, special teams background, or broad executive (e.g. Harbaugh, Campbell). | **Delegates unit schemes.** Offense runs through OC; defense runs through DC. | Game management, aggressive 4th-down decisions, cultural identity. |

### 2.2 Coordinator Roles

Each team carries two primary coordinators under the head coach:
- **`OC` (Offensive Coordinator):** Runs the offensive system. Under an `OFFENSIVE` head coach, the OC acts as the top lieutenant executing the HC's playbook. Under a `DEFENSIVE` or `CEO` head coach, the OC owns offensive scheme selection and play-calling.
- **`DC` (Defensive Coordinator):** Runs the defensive system. Under a `DEFENSIVE` head coach, the DC executes the HC's scheme. Under an `OFFENSIVE` or `CEO` head coach, the DC owns defensive scheme selection, fronts, blitz packages, and coverage philosophies.

---

## 3. Database Schema Evolution

### 3.1 Migration: `coaches` Table Enhancement

Add `slant` to `coaches`:

```sql
ALTER TABLE coaches 
ADD COLUMN slant VARCHAR(16) NOT NULL DEFAULT 'OFFENSIVE' 
CHECK (slant IN ('OFFENSIVE', 'DEFENSIVE', 'CEO'));
```

#### Initial Slant Baseline (Seed for 33 Coaches)
- **`OFFENSIVE`:** Marcus Whitfield (CAR), Dave Canales (ex-CAR), Sean McVay (LAR), Kyle Shanahan (SF), Zac Taylor (CIN), Kevin O'Connell (MIN), Matt LaFleur (GB), Mike McDaniel (MIA), Ben Johnson, Shane Steichen (IND), Brian Callahan (TEN), Doug Pederson (JAX), Arthur Smith, etc.
- **`DEFENSIVE`:** DeMeco Ryans (HOU), Robert Saleh (NYJ), Todd Bowles (TB), Dan Quinn (WAS), Sean McDermott (BUF), Raheem Morris (ATL), Jerod Mayo (NE), Jonathan Gannon (ARI), Matt Eberflus (CHI), Dennis Allen (NO), Mike Macdonald (SEA), etc.
- **`CEO`:** John Harbaugh (BAL), Dan Campbell (DET), Mike Tomlin (PIT), etc.

### 3.2 New Table: `coordinators`

Stores coordinator entities, their specialties, schemes, and baseline philosophies:

```sql
CREATE TABLE IF NOT EXISTS coordinators (
    id SERIAL PRIMARY KEY,
    coordinator_name VARCHAR(255) NOT NULL,
    side VARCHAR(16) NOT NULL CHECK (side IN ('OFFENSIVE', 'DEFENSIVE')),
    scheme VARCHAR(64) NOT NULL,
    playbook VARCHAR(64),
    -- Tendencies for their side of the ball (Low / Medium / High)
    pass_lean VARCHAR(6) CHECK (pass_lean IN ('Low', 'Medium', 'High')),
    blitz_rate VARCHAR(6) CHECK (blitz_rate IN ('Low', 'Medium', 'High')),
    man_coverage VARCHAR(6) CHECK (man_coverage IN ('Low', 'Medium', 'High')),
    origin_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.3 New Table: `coordinator_tenures`

Tracks who held which coordinator post for which franchise across seasons:

```sql
CREATE TABLE IF NOT EXISTS coordinator_tenures (
    id SERIAL PRIMARY KEY,
    coordinator_id INT NOT NULL REFERENCES coordinators(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    role VARCHAR(8) NOT NULL CHECK (role IN ('OC', 'DC')),
    start_season INT NOT NULL,
    end_season INT, -- NULL = active
    UNIQUE (coordinator_id, team_id, role, start_season),
    CHECK (end_season IS NULL OR end_season >= start_season)
);

CREATE INDEX idx_coord_tenures_team_season 
ON coordinator_tenures (team_id, start_season, COALESCE(end_season, 9999));
```

#### Integrity Invariants:
1. Every team must have exactly one active `OC` and one active `DC` for any given season.
2. A coordinator cannot hold active tenures on two different teams in the same season.

---

## 4. Coordinator Seeding and Data Ingestion

### 4.1 Ingestion Sources
1. **User-Supplied Madden Coordinator List:**
   - A structured CSV/JSON file listing actual offensive and defensive coordinators present in Madden franchise mode (e.g. `data/reference/madden_coordinators_2026.json`).
   - Fields: `team_abbr`, `role` (`OC`/`DC`), `name`, `scheme`, `playbook`.
2. **Procedural Fallback Generator:**
   - For any team missing an explicit OC or DC in the reference list:
     - The OC inherits the franchise's established offensive scheme (`Wide Zone West Coast`, `Spread`, etc.) and realistic pass-lean tier.
     - The DC inherits the franchise's established base front (`3-4` or `4-3`) and real-world man/blitz philosophy.
     - Generates a plausible name using the project's coaching name generator.

### 4.2 Initial Franchise Baseline (2026–2035)
- Seed initial `coordinator_tenures` starting at 2026 (or tenure start matching historical data).
- For Carolina: Dave Canales (2026–2033), Marcus Whitfield served as Panthers OC prior to taking over as head coach in 2034.

---

## 5. Coaching Hiring Dynamics & Scheme Inheritance

When a team hires a new head coach in the offseason, the system models the promotion path and tactical consequences.

### 5.1 Promotion and Hiring Archetypes

1. **External Coordinator Poach (The Primary Carousel Dynamic):**
   - Example: Team B hires Team A's offensive coordinator as its new Head Coach.
   - The coordinator entity is converted/promoted into a new `coaches` record (if not already present).
   - Origin is recorded: `former_team: Team A`, `former_role: OC`.
2. **Internal Promotion:**
   - A franchise fires its head coach and elevates its existing OC or DC to HC.
   - The coordinator's former coordinator tenure ends, and an HC tenure begins.
3. **Head Coach Lateral/Re-Hire:**
   - An established head coach dismissed elsewhere is hired by a new franchise.
4. **New / Outside Hire:**
   - College ranks or outside candidate with specified slant and playbook.

### 5.2 Tactical Scheme Inheritance Rules

When a new head coach is appointed to a franchise, unit schemes update according to the coach's slant:

```
                  ┌───────────────────────────────┐
                  │    Incoming Head Coach Hire   │
                  └──────────────┬────────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
  [OFFENSIVE SLANT]       [DEFENSIVE SLANT]         [CEO SLANT]
  • Imports Offensive     • Imports Defensive     • Retains/Adopts
    Scheme & Playbook       Scheme & Base Front     OC Offensive Scheme
  • Imports Pass Lean     • Imports Blitz & Man   • Retains/Adopts
  • DEFENSIVE SCHEME      • OFFENSIVE SCHEME        DC Defensive Scheme
    STAYS UNCHANGED         STAYS UNCHANGED       • Focuses on 4th-Down
    (Retained DC system)    (Retained OC system)    & Game Management
```

#### Detailed Rule Set:
1. **Offensive Slant Hire (e.g., Colts hire ex-Panthers OC):**
   - **Offensive Scheme:** Changes to the incoming coach's scheme (e.g., `Spread` or `Wide Zone West Coast`).
   - **Offensive Playbook:** Replaced by incoming coach's playbook.
   - **Pass Lean:** Inherits incoming coach's tendency.
   - **Defensive Scheme & Front:** **Remains 100% unchanged.** The hiring team's existing defensive front (`Base 4-3` or `3-4`), blitz rate, and coverage philosophy are preserved under the retained/new DC.
2. **Defensive Slant Hire (e.g., Falcons hire ex-Ravens DC):**
   - **Defensive Scheme:** Changes to incoming coach's scheme (e.g., `Base 3-4 Under` or `4-3 Over`).
   - **Defensive Tendencies:** Imports blitz rate and man-coverage philosophy.
   - **Offensive Scheme & Playbook:** **Remains 100% unchanged.** The hiring team's offensive identity is preserved under the offensive coordinator.
3. **CEO Slant Hire:**
   - Overall identity focuses on fourth-down aggressiveness and program leadership.
   - Offensive scheme matches incoming/retained OC.
   - Defensive scheme matches incoming/retained DC.

---

## 6. Engine Updates & Writer Context

### 6.1 `engines/coach_profile.py` Enhancements

Extend coach profile queries to reflect staff composition:

```python
def fetch_team_staff(cur, team_id: int, season: int) -> dict:
    """Returns head coach, OC, and DC for team in season, with effective unit schemes."""
    # 1. Fetch Head Coach (coaches + coach_tenures)
    hc = fetch_team_coach(cur, team_id, season)
    
    # 2. Fetch Coordinators (coordinators + coordinator_tenures)
    cur.execute("""
        SELECT role, coordinator_name, scheme, playbook, pass_lean, blitz_rate, man_coverage
        FROM coordinator_tenures ct
        JOIN coordinators c ON c.id = ct.coordinator_id
        WHERE ct.team_id = %s AND ct.start_season <= %s AND COALESCE(ct.end_season, 9999) >= %s
    """, (team_id, season, season))
    coords = {r[0]: dict(zip(("role", "name", "scheme", "playbook", "pass_lean", "blitz_rate", "man_coverage"), r)) 
              for r in cur.fetchall()}
    
    # 3. Resolve Effective Unit Schemes based on HC slant
    slant = hc.get("slant", "OFFENSIVE")
    effective_off_scheme = hc["offensive_scheme"] if slant == "OFFENSIVE" else (coords.get("OC", {}).get("scheme") or hc["offensive_scheme"])
    effective_def_scheme = hc["defensive_scheme"] if slant == "DEFENSIVE" else (coords.get("DC", {}).get("scheme") or hc["defensive_scheme"])
    
    return {
        "head_coach": hc,
        "offensive_coordinator": coords.get("OC"),
        "defensive_coordinator": coords.get("DC"),
        "effective_offense": effective_off_scheme,
        "effective_defense": effective_def_scheme,
        "staff_philosophy_summary": f"HC {hc['coach_name']} ({slant} slant) calling {effective_off_scheme} / DC coordinating {effective_def_scheme}"
    }
```

### 6.2 Writer Tools & Prompt Exposure

In `newsroom/db_tools.py`:
- `get_coach_profile(coach_name)`: Returns head coach's career totals, slant, coaching tree background, and current OC/DC staff.
- `get_team_context(team_abbr)`: Adds `offensive_coordinator` and `defensive_coordinator` fields alongside `head_coach`.
- `get_matchup_edges(...)`: Compares unit schemes (e.g. how the OC's run-blocking scheme fares against the opposing DC's front).

---

## 7. Offseason Manifest Integration

In `newsroom/offseason_service.py`, coaching moves in `manifest.json` specify coordinator details and origin:

```json
{
  "team": "IND",
  "action": "hire",
  "outgoing_coach": "Shane Steichen",
  "incoming_coach": "Marcus Whitfield",
  "incoming_slant": "OFFENSIVE",
  "origin": {
    "type": "COORDINATOR_POACH",
    "former_team": "CAR",
    "former_role": "OC"
  },
  "imported_offensive_scheme": "Spread",
  "retained_defensive_scheme": "Base 4-3",
  "staff_actions": [
    {
      "role": "OC",
      "action": "hire",
      "coordinator_name": "New Offensive Lieutenant",
      "scheme": "Spread"
    },
    {
      "role": "DC",
      "action": "retain",
      "coordinator_name": "Existing DC",
      "scheme": "Base 4-3"
    }
  ],
  "effective_season": 2036,
  "notes": "Colts hire Whitfield after leading the league in offensive pace in Carolina. Retain defensive staff for continuity."
}
```

### Transactional Application:
When applying this move:
1. Close outgoing head coach's tenure (`end_season = 2035`).
2. Close outgoing OC tenure (if replaced).
3. If incoming coach is new to `coaches`, insert row with `slant = 'OFFENSIVE'`, `offensive_scheme = 'Spread'`, and retained defensive scheme.
4. Insert new `coach_tenures` row for IND starting 2036.
5. Insert new `coordinator_tenures` row for IND OC starting 2036.
6. Verify exactly one active HC, one active OC, and one active DC exist for IND in 2036.

---

## 8. Implementation Steps & Work Breakdown

```
┌────────────────────────────────────────────────────────┐
│ Step 1: Database Migration                             │
│ - Add coaches.slant column                             │
│ - Create coordinators and coordinator_tenures tables   │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│ Step 2: Baseline Seeding Script                        │
│ - Seed 33 head coach slants                            │
│ - Ingest Madden coordinator reference list             │
│ - Generate procedural fallback coordinators            │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│ Step 3: Engine & Profile Logic                         │
│ - Update engines/coach_profile.py                      │
│ - Expose staff profile & effective unit schemes        │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│ Step 4: Transactional Service & Carousel Integration   │
│ - Implement coordinator handling in offseason_service  │
│ - Pre-package origin & scheme facts for writers        │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│ Step 5: Unit Tests & Verification                      │
│ - Test scheme inheritance logic                        │
│ - Test tenure constraints (no duplicate active coords) │
│ - Verify writer tool output formatting                 │
└────────────────────────────────────────────────────────┘
```

---

## 9. Acceptance Criteria

1. **Schema & Integrity:**
   - `coaches.slant` is constrained to `OFFENSIVE`, `DEFENSIVE`, `CEO`.
   - `coordinators` and `coordinator_tenures` exist with foreign keys, checks, and unique indexes.
   - Every active franchise in 2035 has exactly one HC, one OC, and one DC.
2. **Scheme Inheritance Accuracy:**
   - An offensive slant hire changes the team's offensive scheme while defensive scheme remains unchanged.
   - A defensive slant hire changes the team's defensive scheme while offensive scheme remains unchanged.
   - CEO hires maintain unit schemes via their respective coordinators.
3. **Carousel Narrative Facts:**
   - The writer facts bundle for `COACHING_CAROUSEL_AUDIT` accurately cites coordinator origins (e.g. *"Colts hire ex-Panthers OC"*), explaining what scheme changes occur and what unit continuity remains.
4. **Idempotency & Rollback:**
   - Manifest processing applies all HC and coordinator updates in a single database transaction, rolling back completely if any coordinator constraint is violated.
5. **No Regressions:**
   - `engines/coty_engine.py`, `coach_records` view, and existing writer tools continue functioning without breakage.
