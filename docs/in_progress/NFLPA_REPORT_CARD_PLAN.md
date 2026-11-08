# Project Mu: NFLPA Team Report Cards & Facility Effects Plan

**Status:** approved by JR (2026-11-07); building, wave 1 of 2 done.

**As built (orchestrator pre-build fixes):** baseline data committed at `engines/report_card_baseline.json` (the plan's `data/initial_team_report_cards.json` is git-ignored); migration dated `2027_02_08` (2027_02_05–07 already exist); §2.4's duplicated trailing block for `team_operating_policies` implemented once; the Delivers list now names all four tables. §2.3e CPU Owner Upgrade AI (`engines/owner_policy.py`) is not owned by any unit, so it is out of scope for U1–U3 (left unbuilt).
**Builds on:** PROJECT_SPEC §2.4, §2.17, §3.3f, §4.2; [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §2.3, §2.4; [`OWNERS_SCOUTS_VOTERS_PLAN.md`](../in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md) §2.1 (owner spending policies); [`PERSONALITY_CONTRACT_POLICY_PLAN.md`](../done/PERSONALITY_CONTRACT_POLICY_PLAN.md); [`FRONT_OFFICE_INBOX_PLAN.md`](../done/FRONT_OFFICE_INBOX_PLAN.md).

**Goal:** Give every franchise an annual player-evaluated NFLPA Team Report Card grading eight operational and facility categories (Weight Room, Training Room, Locker Room, Nutrition & Food, Travel, Family Treatment, Head Coach, Ownership Investment). Grades produce an organizational quality score ($S_{\text{facility}}$) that integrates directly into free agency utility (`engines/free_agency_engine.py`): elite facilities provide a "facility discount" that saves cap dollars, while bottom-tier organizations suffer a "misery tax" or outright refusal from family-oriented or high-pride players. Published annually in early March as a marquee newsroom event, and visible in the Franchise Command Center where JR and AI GMs can fund facility renovations and operational upgrades.

**Delivers:**
- `db/migrations/2027_02_08_nflpa_report_cards.sql`: tables `owner_cash_ledgers`, `team_report_cards`, `facility_upgrades`, and `team_operating_policies`.
- `engines/report_card_baseline.json`: 32 reviewed baseline report cards for the 2026 season based on real-world NFLPA survey publications (committed under `engines/` — `data/` is git-ignored).
- `engines/report_card_engine.py`: grade evaluation, dynamic improvement/degradation lifecycle, utility modifier calculation, and facility upgrade purchasing.
- `newsroom/report_card_article.py`: annual March newsroom exposé ranking all 32 franchises.
- `server/front_office_api.py` additions: `report_card` object in `GET /api/team/{abbr}/front-office`, `POST /api/team/{abbr}/facilities/upgrade`, and `POST /api/team/{abbr}/facilities/policy`.
- `Mu_Network/src/components/franchise/ReportCardPanel.tsx`: interactive report card UI tab with facility investment actions.
- Tests in `tests/test_report_card_engine.py` and `tests/test_report_card_api.py`.

**JR's answers (2026-11-07):**
- D1 (Q1): Facility grades slowly degrade over time if unmaintained (drop 1 sub-tier every 3 years) to simulate league-wide standards rising and capital wear-and-tear.
- D2 (Q2): Players with extreme Pride/Ego (score $\ge 85$) outright refuse to sign with an "F" graded facility team, regardless of financial offer.
- D3 (Q3): JR retains full debug override authority on `#debug` to inspect, set, or adjust any team's report card grades and policies.
- D4 (Q4): The Owner Cash Pool uses 4 realistic wealth bands ($10M to $50M base) plus an on-field Success Dividend (up to +$10M max for playoff runs/Super Bowls), forcing owners to weigh coaching salaries, facility renovations, and luxury perks.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `engines/free_agency_engine.py:8-25` calculates utility as `U = w_money S_financial + w_role S_opportunity + w_team S_contention + w_scheme S_scheme + e_persona`. `S_contention` is `0.45 win_pct + 0.35 playoff_odds + 0.20 popularity_index / 100` plus a direction bump. | Player interest in an organization is currently driven only by winning and team popularity; facility conditions, travel accommodations, and player treatment have zero mathematical weight in signing decisions. |
| S2 | `engines/free_agency_engine.py:26-30` acceptance logic: `bar = 0.68 + 0.005 (ovr - 75) clamped to [0.55, 0.80]`. | A facility modifier can cleanly adjust either $S_{\text{team}}$ or the acceptance threshold `bar` without disrupting existing financial or scheme components. |
| S3 | `engines/owner_policy.py:45` and `docs/in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md:28` establish owner spending tiers (`AGGRESSIVE`, `BALANCED`, `FRUGAL`). | Frugal owners refuse capital expenditures on stadium renovations; aggressive owners willingly invest cash reserves into state-of-the-art facilities. |
| S4 | `server/front_office_api.py:30-80` returns team front-office state (`staff`, `cap_sheet`, `contracts`, `moves`, `draft_picks`). | Adding a `report_card` block to the front-office endpoint integrates cleanly into the existing Team Hub and Franchise Command Center. |
| S5 | `personality/core.py:120-144` provides the contracts feature view (`greed`, `loyalty`, `ego`). | Players with high `loyalty` or family traits amplify the weight of `family_treatment` and `team_travel`; players with high `ego` amplify `locker_room` and `nutrition`. |
| S6 | `newsroom/editorial_desk.py:140` runs off-season wakeups for Free Agency Preview (Week 22) and Draft Central (Week 24). | Week 21 (early March, post-Combine) is the natural slot for the annual NFLPA Report Card release article. |

## 1. Scope

**In:**
- **Eight Graded Categories:** Weight Room, Training Room, Locker Room Quality, Food & Cafeteria, Team Travel / Planes, Treatment of Families, Head Coach, and Ownership Investment.
- **Letter Grade Scale:** A+, A, A-, B+, B, B-, C+, C, C-, D+, D, D-, F, F- mapped to numerical ratings (0.0 to 1.0).
- **Free Agency Integration:** Facility grades modify free-agent utility in `engines/free_agency_engine.py`:
  - Top-5 facility score provides up to a +0.06 utility boost (facility discount, lowering required APY by 5–10%).
  - Bottom-5 facility score imposes up to a -0.08 utility penalty (misery tax, requiring a 10–15% raise to sign, or causing outright rejection from players with high Pride/Ego).
- **Personality Weighting:** Veterans with high Security/Family weight Family Treatment and Travel double; younger players weight Weight Room and Nutrition.
- **Owner Renovations & Upgrades:** Teams can spend owner cash reserves in the off-season to renovate facilities, bumping letter grades over a 1-to-2 year construction period.
- **Media Coverage:** Annual national exposé article published in Week 21 detailing the best and worst franchises.
- **Frontend Panel:** Dedicated Report Card view in the Franchise Command Center.

**Out (non-goals):**
- Modifying Madden on-field gameplay or stadium models (Madden handles 3D rendering; facilities are an organizational front-office mechanic).
- Legal union grievance lawsuits against owners.
- Stadium municipal bond and public tax referendum votes (kept in the municipal politics domain).

## 2. Design

### 2.1 Grade Categories & Numerical Mapping
The eight survey categories are stored as letter grades and mapped to floating-point scores $g \in [0.0, 1.0]$:

| Letter Grade | Numerical Score ($g$) | Descriptive Tier | Free Agency Impact |
| :--- | :--- | :--- | :--- |
| **A+ / A / A-** | 0.90 – 1.00 | World-Class Destination | +0.04 to +0.06 Utility (Discount) |
| **B+ / B / B-** | 0.70 – 0.89 | Solid / Above Average | +0.01 to +0.03 Utility |
| **C+ / C / C-** | 0.50 – 0.69 | Adequate / Neutral | 0.00 Utility |
| **D+ / D / D-** | 0.30 – 0.49 | Below Standard | -0.02 to -0.04 Utility (Tax) |
| **F / F-** | 0.00 – 0.29 | Toxic / Substandard | -0.05 to -0.08 Utility (Severe Tax / Refusal) |

### 2.2 Facility Utility Integration (`engines/free_agency_engine.py`)
In `engines/free_agency_engine.py`, the team quality score $S_{\text{team}}$ currently reflects only contention:
$$S_{\text{contention}} = 0.45 \cdot \text{win\_pct} + 0.35 \cdot \text{playoff\_odds} + 0.20 \cdot \frac{\text{popularity}}{100}$$

This plan redefines the organizational score $S_{\text{org}}$ to unify competitive and workplace quality:
$$S_{\text{org}} = 0.70 \cdot S_{\text{contention}} + 0.30 \cdot S_{\text{facility}}$$

Where $S_{\text{facility}}$ is the weighted composite of the team's report card:
$$S_{\text{facility}} = \sum_{k=1}^{8} w_k \cdot g_k$$
Baseline weights: Weight Room (0.15), Training Room (0.15), Locker Room (0.15), Nutrition (0.10), Travel (0.15), Family (0.10), Head Coach (0.10), Ownership (0.10).

**Personality Sensitivities:**
* **Veterans (Age $\ge$ 30) or High Loyalty:** Weight for Family Treatment and Team Travel increases to 0.25 each.
* **High Ego / Pride:** If Locker Room or Weight Room is grade D or below, player applies an additional flat -0.05 utility penalty.

### 2.3 The Owner Cash Budget & Improvement Mechanics

#### 1. The Owner Cash Pool ($B_{\text{base}}$) & The 4 Wealth Bands
Rather than flat arbitrary tiers, each franchise possesses an annual cash pool outside the salary cap anchored in their reviewed profile (`newsroom/config/owner_personas/<TEAM>.json`), reflecting the real-life disparity between global corporate conglomerates and cash-strapped family trusts:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 THE 4 OWNER WEALTH BANDS                                         │
├────────────────────┬─────────────────┬───────────────────────────────────────────────────────────┤
│    WEALTH BAND     │ BASE CASH POOL  │ REAL-LIFE EXAMPLES & FINANCIAL CONTEXT                    │
├────────────────────┼─────────────────┼───────────────────────────────────────────────────────────┤
│ Tier 1: Megabucks  │ $40M – $50M/yr  │ Walton-Penner (DEN, $70B+), Tepper (CAR, $20B+),          │
│    Conglomerates   │                 │ Kroenke (LAR, $15B+). Cash liquidity is limitless.        │
├────────────────────┼─────────────────┼───────────────────────────────────────────────────────────┤
│ Tier 2: Wealthy    │ $28M – $38M/yr  │ Jerry Jones (DAL), Robert Kraft (NE), Stephen Ross (MIA),  │
│    Billionaires    │                 │ Jeffrey Lurie (PHI). Massive independent commercial wealth│
├────────────────────┼─────────────────┼───────────────────────────────────────────────────────────┤
│ Tier 3: Mid-Market │ $18M – $26M/yr  │ Hunt Family (KC), Irsay (IND), Blank (ATL), Haslam (CLE). │
│    Industrialists  │                 │ Strong regional businesses; balanced capital spend.       │
├────────────────────┼─────────────────┼───────────────────────────────────────────────────────────┤
│ Tier 4: Cash-Poor  │ $10M – $16M/yr  │ Mike Brown (CIN), Bidwill (ARI), Mark Davis (LV),         │
│    Family Trusts   │                 │ Rooney Family (PIT). Team is sole asset; cash-strapped.   │
└────────────────────┴─────────────────┴───────────────────────────────────────────────────────────┘
```

#### 2. The Success Dividend ($\Delta_{\text{success}}$) & Parity Ceiling
While national media rights (~$400M/team) are shared equally, local gate receipts, playoff suite sales, and merchandise royalties surge with on-field success. Winning franchises earn an annual **Success Dividend** added to their base budget:
$$\text{Annual Cash Pool} = B_{\text{base}} + \Delta_{\text{success}}$$

* **Regular Season Wins:** +$300,000 for every regular season win above 8 wins *(e.g. 12 wins = +$1.2M)*.
* **Home Playoff Games:** +$2,500,000 in pure gate/suite receipts per hosted home playoff game.
* **Super Bowl Appearance:** +$2,500,000; **Super Bowl Championship:** +$5,000,000 (licensing/merchandise surge).
* **Franchise Popularity ($POP$):** Top-5 popularity index franchises receive up to +$2,000,000 in commercial royalties.
* **Parity Guardrail:** The annual Success Dividend is **hard-capped at +$10,000,000** in any single year, preventing runaway rich-get-richer dynasties while guaranteeing that small-market Cinderella champions earn actionable capital to modernize facilities.

#### 3. How Report Card Grades Are Improved (The Three Mechanisms)
Grades do not merely decay over time; owners and front offices actively improve them through three distinct operational channels:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                             REPORT CARD IMPROVEMENT MECHANICS                                    │
├────────────────────────────────┬────────────────────────────────┬────────────────────────────────┤
│    MECHANISM 1: PHYSICAL CAPEX │     MECHANISM 2: ANNUAL OPEX   │   MECHANISM 3: CULTURE/STAFF   │
│  - Locker Room, Weight Room,   │  - Team Travel, Nutrition,     │  - Head Coach respect,         │
│    Training Room, Cafeteria    │    Treatment of Families       │    Ownership willingness       │
│  - $5M–$25M capital reserve    │  - $500k–$3.5M annual policy   │  - Coaching hires / firings,   │
│  - 1-season construction delay │  - Takes effect IMMEDIATELY    │    reinvestment track record   │
└────────────────────────────────┴────────────────────────────────┴────────────────────────────────┘
```

##### a. Physical Capital Expenditures (CapEx Renovations)
* **Categories:** Locker Room, Weight Room, Training Room, Nutrition/Cafeteria Infrastructure.
* **Process:** The owner/GM authorizes a renovation project during the off-season window (Weeks 20–25) drawn from owner cash reserves (independent of the player salary cap).
* **Cost Tiers:**
  * *Minor Refresh ($5M, 1 off-season):* Repairs wear-and-tear; bumps a category by +1 sub-tier (e.g. C- to C).
  * *Standard Renovation ($12M, 1 season):* Modernizes hydrotherapy pools, strength racks, or locker stalls; bumps grade by +1 full tier (e.g. C to B, or D to C).
  * *State-of-the-Art Overhaul ($25M, 1 season):* Complete architectural rebuild (e.g. Miami/Minnesota level); sets the category immediately to A / A+.
* **Timeline:** Projects are ordered in March/April and complete for the following league year's training camp.

##### b. Operational Expenditures & Team Policies (OpEx Budget Toggles)
* **Categories:** Team Travel, Food Quality, Treatment of Families.
* **Process:** These represent operational choices rather than physical brick-and-mortar construction. An owner can change them **immediately** by toggling annual policy line items:
  * *Team Travel ($3.5M/year):* Toggles from regional economy commercial charters to dedicated luxury wide-body jets (e.g., Patriots AirKraft 767s) and eliminates rookie room-sharing on road trips. Jumps travel grade from D/F directly to A/A-.
  * *Treatment of Families ($1.0M/year):* Adds certified on-site gameday daycare rooms, post-game family lounges, and family road-trip travel stipends. Jumps family treatment from C/D to A.
  * *Nutrition & Food ($2.0M/year):* Contracts full-time executive private chefs offering three organic, customized meals per day year-round. Jumps food grade to A.
* **Budget Rule:** These recurring costs are charged annually. If a frugal owner slashes the operational budget, the grade drops in the subsequent season's survey.

##### c. Leadership & Culture Dynamics
* **Categories:** Head Coach, Ownership Investment.
* **Process:**
  * *Head Coach Grade:* Derived from coach communication, player respect, workload management (not running punitive padded practices), and locker room harmony. Improves immediately when hiring a player-centric coach with high Leadership ratings, or through positive relationship events in `COACH_PLAYER_AGENT_LOOPS_PLAN.md`.
  * *Ownership Investment Grade:* Directly calculated from the owner's `spending` trait (`owner_policy.py`) and their track record over the preceding three years. An owner who routinely approves CapEx renovations and maintains top OpEx policies receives an A; an owner who vetoes renovation requests or cuts travel receives an F.

##### d. Natural Wear-and-Tear (Degradation)
* As established in D1, physical facilities naturally degrade by **1 sub-tier every 3 years** without capital maintenance. This reflects both physical wear and the competitive reality of rival NFL franchises constantly opening newer, more modern training facilities.

##### e. CPU Owner Upgrade AI (`engines/owner_policy.py`)
AI owners manage their facilities based on their personality profile and available cash pool:
* **AGGRESSIVE Owners (e.g. Tepper, Jones, Kroenke):** Automatically authorize CapEx renovations whenever any physical category drops to C- or below, and maintain all premium OpEx policies (Luxury Travel, Daycare, Private Chefs).
* **BALANCED Owners:** Fund renovations when a category drops to D+ or when entering a `CONTEND` window to attract blue-chip free agents.
* **FRUGAL Owners (e.g. Brown, Bidwill):** Resist renovations until a category reaches F/F- and the team suffers severe free-agent rejections or intense media embarrassment in the annual report card article.

### 2.4 Database Schema (`db/migrations/2027_02_08_nflpa_report_cards.sql`)
```sql
CREATE TABLE IF NOT EXISTS owner_cash_ledgers (
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    wealth_band VARCHAR(12) NOT NULL CHECK (wealth_band IN ('TIER_1', 'TIER_2', 'TIER_3', 'TIER_4')),
    base_budget BIGINT NOT NULL,
    success_dividend BIGINT NOT NULL DEFAULT 0,
    total_cash_budget BIGINT NOT NULL,
    staff_payroll BIGINT NOT NULL DEFAULT 0,
    opex_committed BIGINT NOT NULL DEFAULT 0,
    capex_spent BIGINT NOT NULL DEFAULT 0,
    accumulated_reserve BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (season, team_id)
);

CREATE TABLE IF NOT EXISTS team_report_cards (
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    weight_room VARCHAR(2) NOT NULL,
    training_room VARCHAR(2) NOT NULL,
    locker_room VARCHAR(2) NOT NULL,
    food_nutrition VARCHAR(2) NOT NULL,
    team_travel VARCHAR(2) NOT NULL,
    family_treatment VARCHAR(2) NOT NULL,
    head_coach VARCHAR(2) NOT NULL,
    ownership VARCHAR(2) NOT NULL,
    overall_score NUMERIC(4,3) NOT NULL,
    overall_grade VARCHAR(2) NOT NULL,
    summary_text TEXT,
    last_renovated_season INT,
    PRIMARY KEY (season, team_id)
);

CREATE TABLE IF NOT EXISTS facility_upgrades (
    id SERIAL PRIMARY KEY,
    team_id INT NOT NULL REFERENCES teams(id),
    season_ordered INT NOT NULL,
    category VARCHAR(20) NOT NULL,
    cost_dollars BIGINT NOT NULL,
    target_grade VARCHAR(2) NOT NULL,
    completion_season INT NOT NULL,
    status VARCHAR(12) NOT NULL DEFAULT 'IN_PROGRESS' CHECK (status IN ('IN_PROGRESS', 'COMPLETED', 'CANCELLED'))
);

CREATE TABLE IF NOT EXISTS team_operating_policies (
    team_id INT PRIMARY KEY REFERENCES teams(id),
    luxury_travel_enabled BOOLEAN NOT NULL DEFAULT false,
    no_roommate_policy BOOLEAN NOT NULL DEFAULT false,
    family_daycare_enabled BOOLEAN NOT NULL DEFAULT false,
    private_chef_program BOOLEAN NOT NULL DEFAULT false,
    annual_opex_cost BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```
    luxury_travel_enabled BOOLEAN NOT NULL DEFAULT false,
    no_roommate_policy BOOLEAN NOT NULL DEFAULT false,
    family_daycare_enabled BOOLEAN NOT NULL DEFAULT false,
    private_chef_program BOOLEAN NOT NULL DEFAULT false,
    annual_opex_cost BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### 2.5 API Endpoints (`server/front_office_api.py`)
* `GET /api/team/{abbr}/report-card`: Returns current and historical report cards, category breakdowns, and league ranks.
* `POST /api/team/{abbr}/facilities/upgrade`: Orders a CapEx physical renovation project.
* `POST /api/team/{abbr}/facilities/policy`: Toggles an OpEx operational policy (e.g. luxury travel, daycare).

---

## 3. Risks and Unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Facility tax unbalances small-market teams in free agency. | Run synthetic multi-wave free agency simulations with 100 players across various team facility grades. | Clamp max utility delta to $[-0.05, +0.04]$ so financial value remains primary. |
| R2 | Initial grades cause user dissatisfaction with favorite team. | Baseline grades derived strictly from published 2024–2026 NFLPA survey releases, cited in config. | Provide debug override token on `#debug` to adjust grades. |

**Spike first:** Offline utility sensitivity test verifying that an elite free agent (88+ OVR) still signs with an "F" facility team if paid 20% above market, but picks a "B+" facility team when monetary offers are within 5%. **Spike run 2026-11-08, passes:** an 88-OVR QB still accepts an "F" facility team at 1.20× market (U 0.892 ≥ bar 0.745), and a "B+" facility team beats an "F" team even when the F team pays 5% more (the F→B+ facility gap is worth ≈12.7% of market AAV). No design change.

---

## 4. Tests (synthetic, no live DB)

- `tests/test_report_card_engine.py`:
  - Grade-to-numeric translation roundtrips.
  - Composite $S_{\text{facility}}$ calculation across baseline cards.
  - Free agency utility delta verifies facility discounts and misery taxes.
  - Personality sensitivity modifiers correctly scale weights for veterans and high-ego players.
  - Facility upgrade state machine advances completion status across season rollovers.
- `tests/test_report_card_api.py`:
  - `GET /api/team/{abbr}/report-card` returns formatted grades and percentiles.
  - `POST /api/team/{abbr}/facilities/upgrade` checks cash availability and returns 200.

Run with: `pytest -m "not integration" tests/test_report_card_engine.py tests/test_report_card_api.py`

---

## 5. Work Breakdown

Shared files: `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Schema, Config & Report Card Engine
- **Owns:**
  - `db/migrations/2027_02_08_nflpa_report_cards.sql` (new)
  - `engines/report_card_baseline.json` (new)
  - `engines/report_card_engine.py` (new)
  - `tests/test_report_card_engine.py` (new)
- **Reads:** `engines/free_agency_engine.py`, `engines/owner_policy.py`
- **Depends on:** none
- **Done when:** 32 teams load baseline report cards, evaluate $S_{\text{facility}}$, and calculate upgrade costs.
- **Verify:** `pytest -m "not integration" tests/test_report_card_engine.py` passes.

### U2: Free Agency & Editorial Integration
- **Owns:**
  - `engines/free_agency_engine.py`
  - `newsroom/report_card_article.py` (new)
  - `tests/test_free_agency_report_card.py` (new)
- **Reads:** `engines/report_card_engine.py`, `newsroom/editorial_desk.py`
- **Depends on:** U1
- **Done when:** Free agent utility reflects facility modifiers, and Week 21 publishes the annual ranking article.
- **Verify:** `pytest -m "not integration" tests/test_free_agency_report_card.py` passes.

### U3: API & Franchise Command Center Panel
- **Owns:**
  - `server/front_office_api.py`
  - `Mu_Network/src/components/franchise/ReportCardPanel.tsx` (new)
  - `Mu_Network/src/types/reportCard.ts` (new)
  - `tests/test_report_card_api.py` (new)
- **Reads:** `engines/report_card_engine.py`, `server/api.py`
- **Depends on:** U1
- **Done when:** Front-office endpoints return report cards, and the React UI renders the grade card with upgrade buttons.
- **Verify:** `pytest -m "not integration" tests/test_report_card_api.py && npm run build --prefix Mu_Network` passes.

---

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1 | — | Core engine, schema, and baseline data. |
| 2 | U2, U3 | U1 | U2 (free agency / media) and U3 (API / UI) build against U1's interface independently. |

**File ownership check:**

| File | Unit |
|---|---|
| `engines/report_card_baseline.json` | U1 |
| `db/migrations/2027_02_08_nflpa_report_cards.sql` | U1 |
| `engines/free_agency_engine.py` | U2 |
| `engines/report_card_engine.py` | U1 |
| `Mu_Network/src/components/franchise/ReportCardPanel.tsx` | U3 |
| `Mu_Network/src/types/reportCard.ts` | U3 |
| `newsroom/report_card_article.py` | U2 |
| `server/front_office_api.py` | U3 |
| `tests/test_free_agency_report_card.py` | U2 |
| `tests/test_report_card_api.py` | U3 |
| `tests/test_report_card_engine.py` | U1 |

## 7. Spec changes when built

- §2 Core Mathematical Indices: Add $S_{\text{facility}}$ and updated $S_{\text{org}}$ formulations.
- §3 Database Schema: Add `team_report_cards` and `facility_upgrades` tables.
- §4 Backend Architecture: Document `/api/team/{abbr}/report-card` and `/api/team/{abbr}/facilities/upgrade`.
- §5 Frontend Architecture: Add `ReportCardPanel` component to the Franchise Command Center.
- §7 Media Swarm: Document the annual Week 21 NFLPA Report Card article.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Should facility grades slowly degrade over time if unmaintained (e.g. drop 1 sub-tier every 3 years)? | **Yes (Recommended).** Adds realistic capital maintenance demands so rich teams cannot buy an A+ once and ignore it forever. |
| Q2 | Can a player with extreme Pride/Ego refuse to sign with an "F" graded team regardless of offered money? | **Yes (Recommended).** Mimics real elite stars who refuse to play for toxic or dilapidated franchises. |
| Q3 | Should JR have unilateral override ability to edit report card grades on `#debug`? | **Yes (Recommended).** Standard debug token access preserves user narrative control. |
| Q4 | Should owner cash pools be structured into 4 distinct wealth bands ($10M–$50M) rather than flat static numbers? | **Yes (Recommended).** Reflects the real-world wealth gap between global corporate titans and cash-strapped family trusts. |
| Q5 | Should on-field franchise success (wins, playoff gates, Super Bowls) award an annual Success Dividend capped at +$10M? | **Yes (Recommended).** Rewards winning with tangible liquidity for renovations while maintaining strict league parity. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | worktree-agent-af05b4ae41b8faa33 | .claude/worktrees/agent-af05b4ae41b8faa33 | merged | 964a3d3 |
