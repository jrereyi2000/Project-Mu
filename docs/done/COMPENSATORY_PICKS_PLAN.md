# Project Mu: Compensatory Draft Picks Engine Plan

**Status:** built and on main (2026-11-08); decisions Q1–Q3 settled as recommended (§8). Units U1–U3 merged in three waves; the open follow-ups under As built are not in any unit's files.
- As built (U1): cancellation is a gained-driven closest-tier pass (each gained QFA cancels the lost QFA closest in round tier, so the most valuable lost QFAs survive); JC-2A is an explicit-input function (`jc2a_eligible`/`jc2a_awards`) because the schema has no demographic or GM/executive field — a follow-up is needed to seed a demographic source and GM/executive-role tracking before the carousel can auto-detect.
- As built (U2): `CBA["DRAFT_PICKS"]` and the rookie scale are 256 wide (values for picks 1–224 unchanged, each base floored at `minimum_salary`); `round_for_overall_pick` takes per-round comp counts (index 0 = round 1) so four Round-3 comps start Round 4 at 101; comp picks are recognised in `trade_value.pick_value` by `pick_suffix`/`comp_slot`/`slot_in_round` and valued at `V(round×32)·e^(−0.015·s)`; `league_office` still signs rookies without a comp schedule (drafted_round static for comp slots) — open follow-up.
- As built (U3): `GET /api/draft/compensatory` and comp rows in `/api/draft/team-needs` (appended after each round's 32 standard picks, later rounds shifted, `total_picks` counted from the table); the War Room gained an additive COMP/JC2A panel; the announcement article is a deterministic module + CLI (`article_type COMPENSATORY_PICKS_ANNOUNCEMENT`, slot `Standalone`, `news_feed`). Scheduling was made stage/season driven: the plan's "Week 21" is a playoff week in Mu (weeks 19–23), so `--week` defaults to the FREE_AGENCY stage (26).
- **Follow-ups closed 2026-11-08** (units U4–U6): the producer (`engines/compensatory_picks_run.py`) now derives QFAs and writes the ledgers, awards and COMP/JC2A `draft_picks`; `engines/draft_order.py`, `engines/draft_room.py` and `pipeline/league_office.py` are comp-aware (and degrade to the pre-comp flow when `draft_picks.pick_suffix` is absent); JC-2A has a reviewed data path (`staff_development_eligibility` / `staff_development_departures`, migration `2027_02_07`) and is **off by default** (JR, 2026-11-08) — `calculate_comp_picks` awards CBA picks only unless `jc2a_enabled=True`.
- **Still open:** the announcement article has no weekly/league-office scheduling hook (filed in `docs/FOLLOW_UPS.md`); nothing calls the producer from a pipeline step yet (it is a CLI run at the offseason FREE_AGENCY stage); there is no live snap-share source, so the Snap Bonus is 0 until one exists; JC-2A's registry needs JR to populate it from a reviewed source before the rule can fire.
**Builds on:** PROJECT_SPEC §2.17, §3.3c, §4.6; [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §2.6, §2.7; [`CAP_RELATIVE_CONTRACTS_PLAN.md`](../done/CAP_RELATIVE_CONTRACTS_PLAN.md); [`SCOUTING_REVAMP_PLAN.md`](../done/SCOUTING_REVAMP_PLAN.md); [`COACH_SLANTS_AND_COORDINATORS_PLAN.md`](../done/COACH_SLANTS_AND_COORDINATORS_PLAN.md).

**Goal:** Implement the official NFL CBA Article 6, Section 2 Compensatory Draft Pick formula and the 2020 Resolution JC-2A minority coach/executive development compensatory picks. The engine tracks Qualifying Free Agents (QFAs) lost versus gained, ranks departing talent by Average Per Year (APY) percentiles and playing time, and allocates up to 32 compensatory picks at the ends of Rounds 3 through 7 (capped at 4 per franchise). Teams whose minority coordinators or executives are hired away as Head Coach or GM are awarded two 3rd-round compensatory picks across consecutive drafts. Picks integrate seamlessly into the Draft War Room, Trade Machine, consensus boards, and rookie contract scales.

**Delivers:**
- `db/migrations/2027_02_06_compensatory_draft_picks.sql`: schema updates to `draft_picks` (expanding the primary key to support multiple comp picks per round, and adding `COMP` and `JC2A` sources), plus ledger tables `qualifying_free_agents` and `compensatory_pick_awards`.
- `engines/compensatory_picks_engine.py`: formula valuation, net cancellation algorithm, 32-pick allocation, and Resolution JC-2A trigger.
- `engines/contracts_engine.py` update: removes the hardcoded `"PICKS_PER_ROUND": 32` limitation, enabling dynamic round calculations and rookie scale contracts for pick numbers 225 through 256.
- `engines/trade_value.py` update: extends surplus value curves ($V_T$) and Rich Hill pick value points to compensatory draft slots.
- `newsroom/compensatory_picks_article.py`: annual early March newsroom report detailing official compensatory pick awards.
- `server/draft_api.py` additions: compensatory pick listings on consensus boards, team capital endpoints, and the War Room.
- Tests in `tests/test_compensatory_picks_engine.py` and `tests/test_compensatory_draft_flow.py`.

**JR's answers (2026-11-07):**
- D1 (Q1): Compensatory picks are legally tradeable, matching the modern post-2017 NFL CBA rule and expanding the Trade Machine.
- D2 (Q2): Resolution JC-2A awards are active during the 2026–2033 all-CPU simulation era, allowing top developmental coaching trees to realistically hoard draft capital.
- D3 (Q3): Round boundaries dynamically expand with compensatory additions (e.g. Round 4 starts at Pick 101+ after four Round 3 comp picks), matching real NFL television draft coverage.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `engines/contracts_engine.py:62` contains: `"PICKS_PER_ROUND": 32, # compensatory picks ignored: round = (pick - 1) // 32 + 1`. | The draft sequence is rigidly assumed to be 32 picks per round (224 picks total). Any pick beyond 224 causes an index error in rookie scale contract generation. |
| S2 | `db/init_db.sql:772-780` defines `draft_picks` with `PRIMARY KEY (draft_season, round, original_team_id)` and `CHECK (source IN ('SEED', 'TRADE', 'JR'))`. | If a team receives two compensatory picks in Round 3 (e.g. San Francisco under JC-2A, or Baltimore losing two tier-3 UFAs), the primary key collides. The table needs a `pick_suffix` or an auto-incrementing ID, and `source` must allow `'COMP'` and `'JC2A'`. |
| S3 | `pipeline/save_reader/draft_tables.mjs:8-16` shows Madden franchise saves contain exactly 672 future pick records (`3 drafts x 7 rounds x 32 teams`), or 224 per draft year. | Madden's native save format does not have extra DraftPick slots. However, in V2 headless Madden, Project Mu runs the draft in Python (`pipeline/league_office.py:76-78`), signs the drafted rookies, and writes the resulting players to the save. Compensatory picks live fully inside Project Mu's database, War Room, and trade engine without needing Madden menus. |
| S4 | `engines/trade_value.py:15-35` maps pick surplus value curves up to pick 224. | Compensatory picks at the ends of Rounds 3 through 7 require calibrated surplus value points on the Jimmy Johnson and Rich Hill curves. |
| S5 | `engines/coaching_carousel.py:112` and `db/init_db.sql:510` track coach hirings and staff moves (`staff_moves`). | When a coordinator or front-office executive is hired away, checking their demographic and tenure profile cleanly triggers Resolution JC-2A awards. |
| S6 | `engines/free_agency_engine.py:180-250` processes free agent signings across off-season waves. | The engine must log the signing timestamp and whether the departing player was an unrestricted free agent whose contract expired naturally, vs a cut player. |

## 1. Scope

**In:**
- **Qualifying Free Agent (QFA) Tracking:**
  - Evaluates true unrestricted free agents (contracts expired naturally) who sign before the post-draft qualifying deadline (Monday following the NFL Draft).
  - Players cut or waived by their former teams do not qualify.
  - Contracts ranked into APY value percentiles (Top 5% = Round 3, Top 10% = Round 4, Top 15% = Round 5, Top 25% = Round 6, Remainder = Round 7).
- **The Net Cancellation Algorithm:**
  - For each franchise, qualifying lost players are paired and cancelled one-to-one against qualifying gained players of equal or adjacent round tiers.
  - Net losses produce compensatory picks awarded at the tail of the corresponding round (Rounds 3 through 7).
  - League-wide ceiling of 32 total compensatory picks; maximum of 4 compensatory picks per franchise per year.
- **Resolution JC-2A Minority Development Awards:**
  - When an organization has a minority offensive coordinator, defensive coordinator, or assistant GM hired away as a Head Coach or GM by another club (with at least two years of tenure), the originating club receives **two 3rd-round compensatory picks** (one in each of the subsequent two drafts).
  - These awards do not count against the 4-pick franchise limit or the 32-pick league-wide ceiling.
- **Draft Integration:**
  - Compensatory picks slots added to `draft_picks` (e.g. Round 3 Pick 33 = Overall Pick 97; subsequent rounds shifted accordingly).
  - Draft War Room and Consensus Board reflect dynamic draft slots.
  - Trade Machine supports trading compensatory picks (per 2017 NFL CBA amendment allowing comp pick trades).
  - Rookie contract scale extended to calculate correct 4-year slotted deals for picks 225 through 256.
- **Annual Media Announcement:**
  - Editorial article published in Week 21 (early March) reporting official compensatory allocations.

**Out (non-goals):**
- Modifying Madden's native offline franchise save tables to add phantom `DraftPick` rows (draft execution happens in Mu; Madden receives the drafted players).
- Awarding compensatory picks for lost coaches who were fired rather than hired away into promotions.

## 2. Design

### 2.1 The Compensatory Valuation Formula (CBA Article 6)
Each departing qualifying unrestricted free agent is assigned a formula point score $P$:
$$P = \text{APY\_Score} + \text{Snap\_Bonus} + \text{Honor\_Bonus}$$

1. **APY Score:**
   - Rank all NFL player contracts by Average Per Year (APY).
   - Top 5% APY ($\ge 95\text{th percentile}$): **Round 3 Tier** (Value = 100).
   - Top 10% APY ($90\text{th}–94.9\text{th}$): **Round 4 Tier** (Value = 80).
   - Top 15% APY ($85\text{th}–89.9\text{th}$): **Round 5 Tier** (Value = 60).
   - Top 25% APY ($75\text{th}–84.9\text{th}$): **Round 6 Tier** (Value = 40).
   - Top 35% APY ($65\text{th}–74.9\text{th}$): **Round 7 Tier** (Value = 20).
   - Below 65th percentile: Non-qualifier (no comp pick awarded).
2. **Snap Bonus:** Players who played $\ge 50\%$ of offensive/defensive snaps in the preceding season receive $+5$ points; $\ge 75\%$ receive $+10$ points.
3. **Honor Bonus:** AP First-Team All-Pro adds $+20$ points; Pro Bowl selection adds $+10$ points.

### 2.2 The Cancellation & Allocation Matrix
* **Cancellation:** A team's lost players and gained players are sorted in descending order of round value. Each lost player is matched with a gained player in the same or nearest available round tier, cancelling each other out.
* **Uncancelled Lost Players:** Yield an unassigned compensatory pick in that player's round tier.
* **Draft Slotting:**
  - Compensatory picks are inserted immediately following the 32nd regular pick of that round.
  - Round 3 picks are slotted 3.33, 3.34, etc.
  - Overall pick numbering shifts down deterministically: if four 3rd-round comp picks are awarded, Round 4 begins at Pick 101 instead of Pick 97.
  - Resolution JC-2A picks are slotted after all CBA Article 6 formula picks in Round 3.

### 2.3 Extended Draft Pick Values (`engines/trade_value.py`)
Compensatory picks are assigned trade surplus value based on an exponential decay curve extending from pick 32 of that round:
$$V_{\text{comp}}(r, s) = V(r, 32) \cdot e^{-0.015 \cdot s}$$
Where $r$ is the round and $s$ is the compensatory slot index (1 to $N$).

### 2.4 Database Schema Updates (`db/migrations/2027_02_06_compensatory_draft_picks.sql`)
```sql
-- Update draft_picks table to support multiple compensatory picks per round
ALTER TABLE draft_picks DROP CONSTRAINT IF EXISTS draft_picks_pkey;
ALTER TABLE draft_picks ADD COLUMN IF NOT EXISTS pick_suffix VARCHAR(4) NOT NULL DEFAULT '';
ALTER TABLE draft_picks ADD PRIMARY KEY (draft_season, round, original_team_id, pick_suffix);

ALTER TABLE draft_picks DROP CONSTRAINT IF EXISTS draft_picks_source_check;
ALTER TABLE draft_picks ADD CONSTRAINT draft_picks_source_check 
    CHECK (source IN ('SEED', 'TRADE', 'JR', 'COMP', 'JC2A'));

CREATE TABLE IF NOT EXISTS qualifying_free_agents (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    player_id INT NOT NULL REFERENCES players(id),
    old_team_id INT NOT NULL REFERENCES teams(id),
    new_team_id INT NOT NULL REFERENCES teams(id),
    signed_date TIMESTAMP WITH TIME ZONE NOT NULL,
    total_value BIGINT NOT NULL,
    years INT NOT NULL,
    apy BIGINT NOT NULL,
    snap_pct NUMERIC(4,3) DEFAULT 0.0,
    honors_points INT DEFAULT 0,
    round_tier SMALLINT CHECK (round_tier BETWEEN 3 AND 7),
    qualifies BOOLEAN NOT NULL DEFAULT true,
    canceled_by_qfa_id INT REFERENCES qualifying_free_agents(id)
);

CREATE TABLE IF NOT EXISTS compensatory_pick_awards (
    id SERIAL PRIMARY KEY,
    draft_season INT NOT NULL,
    team_id INT NOT NULL REFERENCES teams(id),
    round SMALLINT NOT NULL CHECK (round BETWEEN 3 AND 7),
    slot_in_round SMALLINT NOT NULL,
    overall_pick SMALLINT NOT NULL,
    award_kind VARCHAR(6) NOT NULL CHECK (award_kind IN ('CBA', 'JC2A')),
    source_player_id INT REFERENCES players(id),
    source_coach_id INT REFERENCES coaches(id),
    reason TEXT NOT NULL
);
```

### 2.5 Dynamic Rookie Scale (`engines/contracts_engine.py`)
Replace the static `PICKS_PER_ROUND = 32` assumption:
```python
def round_for_overall_pick(overall_pick: int, comp_schedule: list[int] | None = None) -> int:
    """Calculates round for any pick index (1 to 260) given active compensatory allocations."""
    if not comp_schedule:
        return (overall_pick - 1) // 32 + 1
    # Check against dynamic round boundary markers
    ...
```

---

## 3. Risks and Unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Extending pick counts beyond 224 breaks downstream War Room UI or API queries. | Inspect all endpoints in `server/draft_api.py` and React components in `Mu_Network/src/components/draft/`. | Ensure `total_picks` is read dynamically from the database count rather than assuming `7 * 32`. |
| R2 | Madden player injection fails if more than 224 players are drafted. | Review `pipeline/league_office.py:76-78`. Drafted players are signed to regular team rosters. | Total league roster limits (53 active + practice squad) are checked during cut-down; un-rostered drafted rookies become UDFAs. |
| R3 | JC-2A awards disproportionately reward powerhouse franchises. | Review historical 49ers / Rams draft hauls in synthetic 10-year simulation runs. | Verified as authentic to real NFL dynamics (Resolution JC-2A naturally aids top coaching trees). |

**Spike first:** Test a mock draft order containing 248 picks through `engines/contracts_engine.py` and `server/draft_api.py`, verifying that rookie contract proration and War Room displays render with zero errors.

---

## 4. Tests (synthetic, no live DB)

- `tests/test_compensatory_picks_engine.py`:
  - QFA qualification logic (cut players rejected; UFAs before post-draft deadline accepted).
  - APY percentile ranking and point formula scores.
  - Net cancellation matrix correctly pairs equal-tier departures and arrivals.
  - Max 4-pick limit per franchise enforced for CBA Article 6 picks.
  - Resolution JC-2A correctly grants two Round 3 picks across consecutive drafts.
- `tests/test_compensatory_draft_flow.py`:
  - Draft order renumbering correctly shifts subsequent round picks.
  - Extended pick surplus values ($V_T$) match Rich Hill curve decay.
  - Rookie scale contracts compute valid 4-year slotted deals for picks 225 through 256.
  - Trade machine accepts and validates compensatory pick exchanges between teams.

Run with: `pytest -m "not integration" tests/test_compensatory_picks_engine.py tests/test_compensatory_draft_flow.py`

---

## 5. Work Breakdown

Shared files: `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Schema & Compensatory Calculation Engine
- **Owns:**
  - `db/migrations/2027_02_06_compensatory_draft_picks.sql` (new)
  - `engines/compensatory_picks_engine.py` (new)
  - `tests/test_compensatory_picks_engine.py` (new)
- **Reads:** `engines/contracts_engine.py`, `engines/free_agency_engine.py`
- **Depends on:** none
- **Done when:** QFAs are evaluated, cancelled, and up to 32 comp picks + JC-2A picks are calculated for a season.
- **Verify:** `pytest -m "not integration" tests/test_compensatory_picks_engine.py` passes.

### U2: Draft Engine, Pick Value & Contract Integration
- **Owns:**
  - `engines/contracts_engine.py`
  - `engines/trade_value.py`
  - `tests/test_compensatory_draft_flow.py` (new)
- **Reads:** `engines/compensatory_picks_engine.py`, `pipeline/league_office.py`
- **Depends on:** U1
- **Done when:** Draft order supports up to 256 picks, comp pick trade values match curves, and rookie scale contracts generate for comp picks.
- **Verify:** `pytest -m "not integration" tests/test_compensatory_draft_flow.py` passes.

### U3: API, Media Article & War Room Integration
- **Owns:**
  - `server/draft_api.py`
  - `newsroom/compensatory_picks_article.py` (new)
  - `Mu_Network/src/components/draft/DraftWarRoom.tsx`
  - `Mu_Network/src/types/draftPicks.ts` (new)
- **Reads:** `engines/compensatory_picks_engine.py`, `server/api.py`
- **Depends on:** U2
- **Done when:** War Room displays compensatory picks with distinct badges, endpoints expose awards, and Week 21 publishes the announcement article.
- **Verify:** `pytest -m "not integration" tests/test_draft_api.py && npm run build --prefix Mu_Network` passes.

---

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1 | — | Schema updates and core compensatory math engine. |
| 2 | U2 | U1 | Contracts and trade value curves build on U1's pick structures. |
| 3 | U3 | U2 | Draft War Room UI, endpoints, and newsroom article build on U2's dynamic draft sequence. |

**File ownership check:**

| File | Unit |
|---|---|
| `db/migrations/2027_02_06_compensatory_draft_picks.sql` | U1 |
| `engines/compensatory_picks_engine.py` | U1 |
| `engines/contracts_engine.py` | U2 |
| `engines/trade_value.py` | U2 |
| `Mu_Network/src/components/draft/DraftWarRoom.tsx` | U3 |
| `Mu_Network/src/types/draftPicks.ts` | U3 |
| `newsroom/compensatory_picks_article.py` | U3 |
| `server/draft_api.py` | U3 |
| `tests/test_compensatory_draft_flow.py` | U2 |
| `tests/test_contracts_engine.py` | U2 (added by the orchestrator 2026-11-08: the 256-pick extension changes this existing test's expectations, e.g. `RookieScaleTest::test_shape_and_rounds` expecting `ValueError` for pick 225) |
| `tests/test_compensatory_picks_engine.py` | U1 |

## 7. Spec changes when built

- §2 Core Mathematical Indices: Add QFA formula point equations and compensatory pick curve decay.
- §3 Database Schema: Document `draft_picks` primary key expansion, `qualifying_free_agents`, and `compensatory_pick_awards`.
- §4 Backend Architecture: Update `/api/draft/*` endpoints to reflect dynamic round lengths and comp pick metadata.
- §5 Frontend Architecture: Update Draft War Room with compensatory pick visual badges.
- §7 Media Swarm: Document the annual Week 21 Compensatory Picks announcement article.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Should compensatory picks be tradeable? (The real NFL banned trading comp picks until 2017, then legalized it). | **Yes (Recommended).** Legalizing comp pick trades matches modern NFL CBA rules and enriches the Trade Machine. |
| Q2 | Should Resolution JC-2A awards apply during the 2026–2033 all-CPU simulation era? | **Yes (Recommended).** Builds realistic draft capital hoarding for top developmental coaching staffs prior to JR's 2034 takeover. |
| Q3 | When calculating draft order shifts, should round numbers stay strictly tied to pick counts (e.g. Round 4 begins after Round 3 comp picks end)? | **Yes (Recommended).** Matches the real NFL draft broadcast where Round 3 includes picks 65–100+ and Round 4 starts at pick 101+. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | worktree-agent-aefa2bc86d7f1964e | .claude/worktrees/agent-aefa2bc86d7f1964e | merged | 757dff1 |
| U2 | 2 | worktree-agent-a7e07300bad31a384 | .claude/worktrees/agent-a7e07300bad31a384 | merged (retried after an API 402 cut the first run short; also fixed the existing 224-pick expectations in `tests/test_contracts_engine.py` and `tests/test_transactions_engine.py`) | 7c1eb65 |
| U3 | 3 | worktree-agent-aa7037e017bc6fb81 | .claude/worktrees/agent-aa7037e017bc6fb81 | merged | cab476d |
