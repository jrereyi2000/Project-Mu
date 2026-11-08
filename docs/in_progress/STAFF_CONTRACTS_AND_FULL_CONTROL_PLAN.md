# Project Mu: Staff Contracts, Young Talent Appeal & Full Control Plan

**Status:** approved by JR (2026-11-07); building, wave 1 of 2 done.
**Builds on:** PROJECT_SPEC §2.19, §3.3f, §4.2; [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §2.3; [`OWNERS_SCOUTS_VOTERS_PLAN.md`](../in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md) §2.1 (owner spending policies); [`COACH_SLANTS_AND_COORDINATORS_PLAN.md`](../done/COACH_SLANTS_AND_COORDINATORS_PLAN.md); [`NFLPA_REPORT_CARD_PLAN.md`](../done/NFLPA_REPORT_CARD_PLAN.md) (Owner Cash Budget pool).

**Goal:** Establish uncapped multi-year contracts and market salaries for Head Coaches, Coordinators, and General Managers paid directly out of the Owner Cash Budget ($10M–$50M/year base plus up to +$10M on-field Success Dividend), upgrade destination `opening_appeal()` for both HC and GM candidates to heavily factor in star/promising young QBs and under-25 blue-chip roster talent, and implement an authentic, strictly governed "Godfather Offer" mechanic allowing rare desperate owners to hire elite dual-hat Head Coach + GM leaders with full 53-man roster control (reflecting the historical rarity of Marcus Whitfield and Charles Grant).

**Delivers:**
- `db/migrations/2027_02_07_staff_contracts_and_full_control.sql`: table `staff_contracts` (salary, length, remaining years, full control flag) and league-wide godfather cooldown ledger `full_control_history`.
- `engines/staff_contracts_engine.py`: staff salary curves, market value calculations, owner cash payroll deductions, and contract expiry/rollover tracking.
- `engines/coaching_carousel.py` enhancement: overhauled `opening_appeal()` (under-25 talent core, young QB ceiling, draft capital weighting) and candidate offer scoring (salary, guaranteed length, and control bonuses).
- `engines/gm_rollover.py` enhancement: upgrades GM replacement from blind profile seeding into a competitive hiring search evaluating opening appeal and staff budget.
- `server/front_office_api.py` additions: staff contract and salary transparency on `GET /api/team/{abbr}/front-office`.
- `Mu_Network/src/components/franchise/StaffContractCard.tsx`: staff salary, contract years, and control badges on the Team Hub and Franchise Command Center.
- Tests in `tests/test_staff_contracts.py` and `tests/test_carousel_appeal_enhanced.py`.

**JR's answers (2026-11-07):**
- D1: Option A (Annual Owner Cash Pool of $10M–$50M across 4 wealth bands, augmented by a capped Success Dividend of up to +$10M for playoff runs/Super Bowls) funds all coaching and front-office payroll outside the player salary cap.
- D2: Godfather offers (Full Control / Dual-Hat HC+GM) must reflect real NFL extreme rarity (once every 5–10 years across the entire league; capped at at most 2 active league-wide with a mandatory 5-year cooldown).
- D3: Destination `opening_appeal` must evaluate roster talent, under-25 young foundation, and star or promising young franchise QBs on rookie deals, applying equally to both Head Coach and General Manager openings.
- D4 (Q1): Firing a coach with Full Control automatically vacates the GM seat as well (simultaneous dual vacancy).
- D5 (Q2): Fired staff buyouts feature an offset clause if the coach/GM is hired by another club during the contract term.
- D6 (Q3): User-controlled franchises retain interactive roster authority; user GMs do not surrender control to an autonomous CPU head coach.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `db/init_db.sql:637-670` defines `coaches`, `coach_tenures`, `coordinators`, and `coordinator_tenures` with only `start_season` and `end_season`. | Zero contract years, salaries, or financial terms are currently tracked for coaches; coaching staffs are completely free to owners in the database. |
| S2 | `engines/coaching_carousel.py:208-232` computes `opening_appeal()` using `APPEAL_WEIGHTS = {"qb": 0.30, "roster": 0.20, "draft": 0.15, "market": 0.15, "front_office": 0.10, "cap": 0.10}`. `roster` is raw `team_ovr` percentile, and `qb` is `scouting_boards.qb_security`. | A team with an aging, declining 80 OVR roster has the exact same roster appeal as a young, ascending team loaded with under-25 Pro Bowlers; promising rookie QBs with high dev traits are not differentiated from veteran game-managers. |
| S3 | `engines/gm_rollover.py:75-80` fires GMs when hot-seat score $\ge 1.0$, and immediately seeds a new GM profile via `gm_personas.py` (`seed 'gm:<team>:<season>'`). | There is no competitive GM hiring market or candidate choice; CPU teams blindly generate a new GM with no salary, no contract length, and no destination appeal evaluation. |
| S4 | `db/init_db.sql:715` contains `gm_profiles.coach_id INT REFERENCES coaches(id)`. Migrations `2026_12_19_charles_grant_full_control.sql` and `2026_12_20_marcus_whitfield_full_control.sql` show Charles Grant (IND) and Marcus Whitfield (CAR) holding full dual-hat control. | The database structure for dual-hat GM/Coach authority already exists, but it was applied via static migration lore rather than autonomous dynamic carousel hiring logic. |
| S5 | `engines/owner_policy.py:87-95` models owner spending as cap buffer shares (`DEEP_POCKETS` 0%, `BUDGET_CONSCIOUS` 5%). | Linking the Owner Cash Budget pool ($12M–$40M) from `NFLPA_REPORT_CARD_PLAN.md` directly limits what owners can bid on elite coaching staffs without starving facility CapEx. |

## 1. Scope

**In:**
- **Uncapped Staff Contracts:**
  - Contracts for Head Coaches, Offensive Coordinators, Defensive Coordinators, and General Managers.
  - Stored in `staff_contracts`: `(person_id, person_kind, team_id, annual_salary, total_years, remaining_years, full_control, signed_season)`.
  - Funded directly from the Owner Cash Pool (Option A).
  - Four market salary tiers: Marquee/Championship HC (\$14M–\$20M/yr), Established Solid HC (\$8M–\$12M/yr), First-Time Coordinator Promotion (\$4.5M–\$7M/yr), Coordinators (\$1.5M–\$3.5M/yr), General Managers (\$5M–\$10M/yr).
- **Overhauled Destination Opening Appeal (Shared by HC & GM Searches):**
  - **Promising / Star QB Score ($S_{\text{qb}}$):** Evaluates QB quality, contract runway, and ceiling. An elite Tier-1 QB (Crawford, Mahomes) or an under-25 Star/Superstar/X-Factor rookie QB on a cheap deal provides maximum destination appeal (+0.35); an aging, expensive bridge QB imposes a heavy drag.
  - **Young Talent Core ($S_{\text{youth}}$):** Evaluates the percentage of roster snaps and starters under age 25, plus under-25 players with Superstar/X-Factor dev traits. Replaces raw `team_ovr` with a composite:
    $$S_{\text{roster\_appeal}} = 0.50 \cdot S_{\text{team\_ovr}} + 0.50 \cdot S_{\text{youth\_core}}$$
  - **Draft Capital Runway ($S_{\text{draft}}$):** Multiple Round 1 picks in the upcoming two drafts heavily boost appeal for incoming GMs and HCs.
- **The "Godfather Offer" & Full Control Architecture:**
  - Strict Rarity Governor: At most **two active Full-Control GM/Coaches** league-wide at any time; mandatory **5-year league-wide cooldown** between new Godfather offers.
  - Strict Owner Trigger: Owner must have `DEEP_POCKETS` and `HEADLINE_HUNTER` traits (`owner_policy.py`), owner cash pool $\ge \$35\text{M}$, and high impatience/desperation.
  - Vacancy Requirement: Both Head Coach and General Manager positions must be vacant simultaneously (clean-sweep Black Monday purge).
  - Candidate Prerequisite: Candidate must have won a Super Bowl as HC/coordinator, or be an A+ rated Tier-1 candidate with `drive` $\ge 8$ or `ego` $\ge 8$.
  - Contract terms: 5–6 years fully guaranteed at top-of-market salary (\$16M–\$22M/year), accompanied by final 53-man roster say (`gm_profiles.coach_id = coach_id`).
- **Competitive GM Hiring Market:**
  - When a GM is fired, the team opens a formal search among top personnel directors and college scouting directors, evaluating candidates via the enhanced opening appeal and owner salary budget.

**Out (non-goals):**
- Positional coaches (QBs, OL, DL coaches) beyond HC, OC, DC, and GM.
- College head coaching buyouts or NCAA poaching fees.
- Head coach buyout litigation in court (fired coaches simply collect remaining guaranteed salary from owner cash).

## 2. Design

### 2.1 Enhanced Destination Opening Appeal (`engines/coaching_carousel.py`)
Both prospective Head Coaches and General Managers evaluate vacant openings using an expanded 6-component appeal formulation:

$$\text{Appeal} = 0.30 \cdot S_{\text{qb}} + 0.25 \cdot S_{\text{roster\_appeal}} + 0.15 \cdot S_{\text{draft}} + 0.10 \cdot S_{\text{cap}} + 0.10 \cdot S_{\text{market}} + 0.10 \cdot S_{\text{facilities}} - 0.05 \cdot \text{churn}$$

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                       ENHANCED OPENING APPEAL COMPONENTS                        │
├─────────────────────────┬───────────────────────────────────────────────────────┤
│  1. QB Security & Youth │ Elite franchise QB OR under-25 Star/X-Factor starter  │
│  2. Roster & Youth Core │ 50% Team OVR + 50% Under-25 Pro Bowl / starter share │
│  3. Draft Capital       │ Weighted Round 1 & 2 pick capital over next 2 drafts  │
│  4. Cap Flexibility     │ Available effective cap space entering next season    │
│  5. Market & Prestige   │ Historical franchise clout and market tier            │
│  6. Facility Quality    │ NFLPA Report Card score (from NFLPA_REPORT_CARD_PLAN) │
│  - Penalty: Churn       │ -0.05 per extra head coach fired in past 5 seasons     │
└─────────────────────────┴───────────────────────────────────────────────────────┘
```

#### Under-25 Youth Core Calculation ($S_{\text{youth\_core}}$):
$$S_{\text{youth\_core}} = \frac{N_{\text{under25\_starters}}}{22} + 0.05 \cdot N_{\text{under25\_xfactor}}$$
Clamped to $[0.0, 1.0]$. Franchises like the 2035–2036 Carolina Panthers (25 players under 25, 4 blue-chip All-Pros on rookie deals) max out this score, making them the premier destination in professional football.

#### Promising / Franchise QB Calculation ($S_{\text{qb}}$):
* **Tier 1 (Elite Franchise QB, OVR $\ge 88$, Age $\le 31$):** Score = 1.00.
* **Tier 2 (Promising Young QB, Age $\le 25$, Dev Trait Star/Superstar/X-Factor, OVR $\ge 76$):** Score = 0.90. (High developmental ceiling on a cheap rookie contract).
* **Tier 3 (Average Established Starter, OVR 78–84, Age 26–31):** Score = 0.60.
* **Tier 4 (Aging Bridge QB, Age $\ge 34$, OVR $\le 78$, Cap $\ge \$30\text{M}$):** Score = 0.25 (Financial handicap).
* **Tier 5 (QB Wasteland / No Starter):** Score = 0.10.

---

### 2.2 Staff Salary Curves & Owner Cash Deductions (`engines/staff_contracts_engine.py`)

#### Annual Market Salary Scales:
```python
STAFF_SALARY_SCALES = {
    "HC": {
        "LEGENDARY": (16_000_000, 22_000_000),   # Super Bowl champion / Tier 1
        "ESTABLISHED": (8_000_000, 14_000_000),  # Multi-year winning HC
        "PROMOTION": (4_500_000, 7_500_000),    # Rising coordinator first-timer
    },
    "GM": {
        "ELITE": (8_000_000, 12_000_000),       # Proven championship architect
        "STANDARD": (4_000_000, 7_500_000),     # Established personnel director
    },
    "COORD": {
        "TOP_TIER": (2_500_000, 4_000_000),     # Highly sought playcaller
        "STANDARD": (1_500_000, 2_400_000),     # Position coach promotion
    }
}
```

#### Cash Pool Accounting & The Wealth Bands:
* Each franchise possesses an Annual Owner Cash Budget ($B_{\text{cash}}$) governed by their wealth band plus on-field success dividends:
$$\text{Annual Cash Pool} = B_{\text{base}} + \Delta_{\text{success}}$$
  - **Tier 1 (Megabucks Conglomerates - DEN, CAR, LAR):** $B_{\text{base}} = \$40\text{M}–\$50\text{M}/\text{yr}$.
  - **Tier 2 (Wealthy Billionaires - DAL, NE, MIA, PHI):** $B_{\text{base}} = \$28\text{M}–\$38\text{M}/\text{yr}$.
  - **Tier 3 (Mid-Market Industrialists - KC, IND, ATL, CLE):** $B_{\text{base}} = \$18\text{M}–\$26\text{M}/\text{yr}$.
  - **Tier 4 (Cash-Poor Family Trusts - CIN, ARI, LV, PIT):** $B_{\text{base}} = \$10\text{M}–\$16\text{M}/\text{yr}$.
* **The Success Dividend ($\Delta_{\text{success}}$, capped at +$10.0M/yr):** Adds +$300k per regular season win over 8, +$2.5M per hosted home playoff game, +$2.5M for Super Bowl appearance, +$5.0M for Super Bowl victory, and up to +$2.0M for top popularity ($POP$).
* Total staff payroll is deducted annually:
$$\text{Staff\_Payroll} = \text{Salary}_{\text{HC}} + \text{Salary}_{\text{GM}} + \text{Salary}_{\text{OC}} + \text{Salary}_{\text{DC}}$$
* Dead buyouts from fired coaches/GMs (offset by any new contract salary with another club per D5) are deducted:
$$\text{Available CapEx} = \text{Annual Cash Pool} - \text{Staff\_Payroll} - \text{Active OpEx Perks} - \text{Dead Buyouts}$$
* Remaining cash is available for Facility CapEx renovations and OpEx luxury perks (travel, daycare, chefs) under `NFLPA_REPORT_CARD_PLAN.md`.
* If an owner exhausts cash reserves modernizing facilities or paying for private jets, they lack the liquidity to enter an $18M/year bidding war for a marquee coach. Conversely, signing an expensive staff squeezes available CapEx, causing unmaintained facilities to slowly degrade.

---

### 2.3 The "Godfather Offer" & Full Control Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                THE GODFATHER OFFER ALGORITHM                                     │
├────────────────────────────────┬────────────────────────────────┬────────────────────────────────┤
│      STRICT RARITY GATES       │      ORGANIZATIONAL CRISIS     │       THE REWARD PACKAGE       │
│  - League-wide cap: MAX 2      │  - HC & GM vacant (clean sweep)│  - Final 53-man roster say     │
│  - 5-year league cooldown      │  - Owner: DEEP_POCKETS +       │  - gm_profiles.coach_id link   │
│  - Candidate: Tier 1 Champion  │    HEADLINE_HUNTER             │  - $16M–$22M/yr for 5–6 years  │
│    or A+ mastermind with drive │  - Impatience score < 0.90     │  - Unified scheme & draft sync │
└────────────────────────────────┴────────────────────────────────┴────────────────────────────────┘
```

#### The Rarity Governor (Hard Gates):
A team is permitted to construct and extend a "Godfather Offer" (Full Control) **only if ALL of the following conditions pass**:
1. **League Ceiling:** There are currently **fewer than 2 active Full-Control GM/Coaches** in the entire 32-team NFL.
2. **Cooldown Timer:** At least **5 regular seasons** have elapsed since the last Godfather offer was extended anywhere in the league.
3. **Owner Archetype:** The hiring owner has `DEEP_POCKETS` and `HEADLINE_HUNTER` personality traits, and cash budget $\ge \$35\text{M}$.
4. **Dual Vacancy:** Both the Head Coach and General Manager positions were vacated in the same off-season cycle.
5. **Candidate Stature:** The candidate is either a Super Bowl-winning head coach (e.g. Whitfield, Belichick, Carroll) or an A+ playcalling coordinator with `drive` $\ge 8$ and `ego` $\ge 8$ who refuses standard head-coaching offers without roster control.

#### Execution Effect:
When accepted:
* `staff_contracts.full_control` is set to `TRUE`.
* `gm_profiles.coach_id` is set to `coaches.id`.
* The head coach holds final authority over trades, draft boards, and roster cuts; owner meddling in trades is disabled.

---

### 2.4 Database Schema (`db/migrations/2027_02_07_staff_contracts_and_full_control.sql`)

```sql
CREATE TABLE IF NOT EXISTS staff_contracts (
    id SERIAL PRIMARY KEY,
    team_id INT NOT NULL REFERENCES teams(id),
    person_id INT NOT NULL,
    person_kind VARCHAR(6) NOT NULL CHECK (person_kind IN ('HC', 'GM', 'OC', 'DC')),
    annual_salary BIGINT NOT NULL,
    total_years SMALLINT NOT NULL,
    remaining_years SMALLINT NOT NULL,
    signed_season INT NOT NULL,
    full_control BOOLEAN NOT NULL DEFAULT false,
    buyout_owed BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS full_control_history (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    coach_id INT NOT NULL REFERENCES coaches(id),
    annual_salary BIGINT NOT NULL,
    contract_years SMALLINT NOT NULL,
    trigger_reason TEXT NOT NULL
);
```

### 2.5 API Endpoints (`server/front_office_api.py`)
* `GET /api/team/{abbr}/front-office`: Adds a `staff_contracts` block detailing HC, GM, OC, DC salaries, years remaining, and whether the head coach holds Full Control.
* `POST /api/team/{abbr}/staff/offer`: Allows JR (as owner) to construct custom contract offers (salary, years, control permissions) during coaching carousel phases.

---

## 3. Risks and Unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | High coaching salaries bankrupt small-market owner cash pools. | Simulate 10-year coaching carousel cycles across all 32 teams; monitor remaining cash reserves. | Minimum baseline cash pool is set to \$12M, ensuring even frugal owners can cover standard \$4.5M HC + \$1.5M OC + \$1.5M DC staffs with \$4.5M left over. |
| R2 | Godfather offers trigger too frequently. | Synthetic 50-year headless league run checking total Full Control occurrences. | Hardcode strict 5-year cooldown and max-2 league-wide ceiling in `engines/staff_contracts_engine.py`. |
| R3 | Promising rookie QB weighting skews opening appeal too high for flawed rosters. | Test appeal scores across teams with top-10 rookie QBs vs 12-win veteran teams with average QBs. | Balance weights: QB is capped at 0.30; roster talent and youth core carry 0.25 combined. |

**Spike first:** Run an offline simulation comparing opening appeal scores across 32 teams after a completed season, verifying that a 6-win team with a dynamic 23-year-old superstar QB and five under-25 starters outranks an 8-win team with an aging 36-year-old game manager and zero young talent.

---

## 4. Tests (synthetic, no live DB)

- `tests/test_staff_contracts.py`:
  - Staff salary scales correctly tier candidates by experience and championships.
  - Annual staff payroll cleanly deducts from Owner Cash Budget pool.
  - Fired staff members transition to `buyout_owed` and decrement remaining years each rollover.
- `tests/test_carousel_appeal_enhanced.py`:
  - `opening_appeal()` correctly evaluates `qb_security`, young QB dev traits, and under-25 starter share.
  - Team with top under-25 talent outscores an older roster of identical team OVR.
  - Godfather offer rarity governor rejects offers when active count $\ge 2$ or cooldown $< 5$ years.
  - Full Control acceptance correctly populates `gm_profiles.coach_id` and synchronizes front-office slants.

Run with: `pytest -m "not integration" tests/test_staff_contracts.py tests/test_carousel_appeal_enhanced.py`

---

## 5. Work Breakdown

Shared files: `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Schema & Staff Contracts Engine
- **Owns:**
  - `db/migrations/2027_02_07_staff_contracts_and_full_control.sql` (new)
  - `engines/staff_contracts_engine.py` (new)
  - `tests/test_staff_contracts.py` (new)
- **Reads:** `engines/owner_policy.py`, `engines/coaching_carousel.py`
- **Depends on:** none
- **Done when:** Multi-year staff contracts generate market salaries, enforce buyouts upon firing, and deduct from owner cash.
- **Verify:** `pytest -m "not integration" tests/test_staff_contracts.py` passes.

### U2: Enhanced Opening Appeal & Carousel Hiring Logic
- **Owns:**
  - `engines/coaching_carousel.py`
  - `engines/gm_rollover.py`
  - `tests/test_carousel_appeal_enhanced.py` (new)
- **Reads:** `engines/staff_contracts_engine.py`, `engines/scouting_boards.py`
- **Depends on:** U1
- **Done when:** Opening appeal evaluates youth core and young QBs, and the carousel enforces the strict Godfather rarity governor.
- **Verify:** `pytest -m "not integration" tests/test_carousel_appeal_enhanced.py` passes.

### U3: API & Front Office UI Integration
- **Owns:**
  - `server/front_office_api.py`
  - `Mu_Network/src/components/franchise/StaffContractCard.tsx` (new)
  - `Mu_Network/src/types/staffContracts.ts` (new)
  - `tests/test_staff_contracts_api.py` (new)
- **Reads:** `engines/staff_contracts_engine.py`, `server/api.py`
- **Depends on:** U1
- **Done when:** Front-office endpoints return staff contract details, and the React frontend renders coach salary, years, and Full Control badges.
- **Verify:** `pytest -m "not integration" tests/test_staff_contracts_api.py && npm run build --prefix Mu_Network` passes.

---

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1 | — | Core schema, contract math, and cash pool deductions. |
| 2 | U2, U3 | U1 | U2 (carousel & GM hiring algorithms) and U3 (API & UI cards) build independently on U1's contract interface. |

**File ownership check:**

| File | Unit |
|---|---|
| `db/migrations/2027_02_07_staff_contracts_and_full_control.sql` | U1 |
| `engines/coaching_carousel.py` | U2 |
| `engines/gm_rollover.py` | U2 |
| `engines/staff_contracts_engine.py` | U1 |
| `Mu_Network/src/components/franchise/StaffContractCard.tsx` | U3 |
| `Mu_Network/src/types/staffContracts.ts` | U3 |
| `server/front_office_api.py` | U3 |
| `tests/test_carousel_appeal_enhanced.py` | U2 |
| `tests/test_staff_contracts.py` | U1 |
| `tests/test_staff_contracts_api.py` | U3 |

## 7. Spec changes when built

- §2 Core Mathematical Indices: Add $S_{\text{youth\_core}}$ formula and enhanced `opening_appeal()` equation.
- §3 Database Schema: Document `staff_contracts` and `full_control_history` tables.
- §4 Backend Architecture: Document `/api/team/{abbr}/front-office` staff salary additions and `/api/team/{abbr}/staff/offer`.
- §5 Frontend Architecture: Document `StaffContractCard` in the Franchise Command Center.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | If a coach with Full Control is fired, should the team automatically fire the GM position as well (complete clean sweep)? | **Yes (Recommended).** Because the coach *was* the GM, firing him naturally vacates both seats simultaneously. |
| Q2 | Should coach buyouts for fired staff offset if they are hired elsewhere (the real NFL offset clause)? | **Yes (Recommended).** If a fired coach owed \$10M gets an \$8M job with another team, the original owner only pays the \$2M difference. |
| Q3 | Can JR as user GM offer a head coach Full Control and willingly surrender final 53-man roster say? | **No (Recommended).** JR's user team is user-controlled; user GMs keep interactive roster authority unless explicitly running an autonomous spectator league. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | worktree-agent-a1572c54687db3f01 | agent-a1572c54687db3f01 | merged | dd6b606 |
