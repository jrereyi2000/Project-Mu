# Team Management: Implementation Plan (master)

Written 2026-10-19 from a design talk with JR. This plan ties the work together. Each step gets its own sub-plan under [`docs/team_management/`](team_management/), and a sub-agent writes that sub-plan before building the step. This document holds the decisions, the order of the steps, and the **interfaces between them**: tables, functions, file layouts. With those fixed, each sub-agent can plan and build its step without reading the others.

**Goal:** Mu knows every contract and every roster move in the league, and Mu (not Madden's CPU) decides the trades and the Round 1 picks. The CPU still handles routine signings and releases.

---

## 0. Decisions (JR, 2026-10-19)

| # | Decision | Why |
|---|---|---|
| D1 | **Madden's CPU keeps signings and releases.** Mu only scans them. | Injury replacements and depth fills are low-stakes. Real teams answer most injuries with a signing, not a trade. Keeping the CPU in charge also keeps auto re-signing and depth charts working. |
| D2 | **Mu decides trades.** GM agents propose them, JR approves them and makes them by hand in Madden. CPU trades get turned off if Madden has a setting for it. | Madden's trade logic isn't realistic and the CPU trades too often. The NFL averages about 17 trades a season. |
| D3 | **JR does not control all 32 teams permanently.** To make an approved trade, he switches one of the teams to user control (Options > User Teams), makes the trade, then switches it back. | User-controlled teams lose the CPU's signings, re-signings and depth-chart upkeep. |
| D4 | **Draft: GM agents choose the first 32 picks, and JR makes each pick in Madden by controlling that team. Madden simulates the rest.** The choices are made one pick at a time, live, from a scouting board (step 4), not from the mock draft's top-32-by-OVR. | 224 picks made by hand is too many. If picks were fixed in advance, they'd break as soon as a pick went off-script. Ranking by overall isn't how real boards look. |
| D5 | **Contracts are scanned once a season (Season scan), then kept current from transactions.** When a transaction scan sees a team change, that team's Team Salary screen is captured again. Teams are never patched by guessing. | One full scan takes ~40 min, and a single team takes ~1–1.5 min. A CPU signing's contract terms show up only on the Team Salary screen. |
| D6 | **Agents are informed by data.** They make up no rationale that contradicts stored data. They may invent untracked narrative, such as front-office chatter, the way the newsroom does. | Same hallucination policy as the newsroom. |
| D7 | **The automated capture never presses Cross on TRADE, FREE AGENCY or CONTRACTS, and never on a trade or sign/release confirmation.** It may open only the read-only TEAM SALARY and LEAGUE TRANSACTIONS screens. | The same safety rule as the existing Cross guard (`madden_capture.cross`). |

---

## 1. Steps and sub-plans

| Step | Sub-plan | Depends on | Delivers |
|---|---|---|---|
| 1 | [`team_management/01_CONTRACT_SCAN_PLAN.md`](team_management/01_CONTRACT_SCAN_PLAN.md) | (none) | Team Salary capture, extraction, contract and cap tables, Season scan phase, `--mode contracts` |
| 2 | [`team_management/02_LEAGUE_TRANSACTIONS_PLAN.md`](team_management/02_LEAGUE_TRANSACTIONS_PLAN.md) | Step 1's schema (§2.1). Can be built in parallel against that schema. | A weekly transactions scan, the `transactions` table, roster and contract moves, per-team contract re-scans |
| 3 | [`team_management/03_GM_AGENTS_PLAN.md`](team_management/03_GM_AGENTS_PLAN.md) | Steps 1 and 2 loaded | GM agent personas, team direction, trade proposals → approval queue → confirmed by step 2 |
| 4 | [`team_management/04_SCOUTING_AND_DRAFT_PLAN.md`](team_management/04_SCOUTING_AND_DRAFT_PLAN.md) (**needs a design pass first, §2.5**) | Step 3 (GM personas and team direction) | A scouting architecture shared by GM agents and JR (a living board, college storylines and performances, risers and fallers), plus live pick-by-pick GM decisions for picks 1–32 |
| 5 | [`team_management/05_NEWSROOM_INTEGRATION_PLAN.md`](team_management/05_NEWSROOM_INTEGRATION_PLAN.md) | Step 1 for cap tools, step 2 for transaction stories | Writer tools (cap sheet, contract, transactions), trade/signing coverage in the slate and Agent Wire, and lifting the "no trades/signings" ban where it applies |

Steps 1 and 2 can start right away, in parallel, because §2 fixes their shared contract. Step 5 can start once step 1 lands. Steps 3 and 4 come after.

### What every sub-plan contains
1. **Survey:** facts read from the code that change the scope, as a table like the one in `NAPPER_WEEKLY_IMPLEMENTATION_PLAN.md` §0.
2. **Console facts to check:** anything that needs JR at the console (screen names, columns, settings). List them, and don't build on them unverified.
3. **Phases:** each phase is a mergeable chunk with its files, tests and exit check.
4. **`PROJECT_SPEC.md` edits:** which sections each phase changes (CLAUDE.md rule).
5. **Out of scope:** anything that belongs to another step.

---

## 2. Shared interfaces (fixed here; sub-plans may extend but not change)

### 2.1 Contract schema (owned by step 1, migration `db/migrations/2026_12_07_contracts.sql`, folded into `init_db.sql`)

Money is stored as `bigint` whole dollars. `$16.8M` → 16800000, `$200K` → 200000. The parser goes in `pipeline/money.py` (`parse_money(str) -> int | None`) and every step uses it.

- **`team_cap_seasons`**: `team_id` FK, `season`, `cap`, `rollover`, `adjusted_cap`, `cap_space` (can be negative), `salaries`, `cap_penalty`, `total_spend`, `next_rookie_reserve`, `scanned_at` timestamptz, `capture_id` (the `captures` row). PK `(team_id, season)`. The latest scan wins.
- **`player_contracts`**: `id` PK, `player_id` FK, `team_id` FK, `season` (the league season it was scanned in), `length`, `years_remaining`, `total_value`, `bonus`, `cut_savings`, `cut_penalty`, `no_trade_clause` bool, `incentive` text (`NONE` → NULL), `status` (`ACTIVE` | `SUPERSEDED` | `RELEASED` | `TRADED`), `source` (`SCAN` | `TRANSACTION`), `scanned_at`, `capture_id`. Unique `(player_id, season) WHERE status = 'ACTIVE'`.
- **`player_contract_years`**: `contract_id` FK ON DELETE CASCADE, `year` (calendar year, e.g. 2036), `cap_hit` bigint. PK `(contract_id, year)`. There is one row per year column on screen (five years), so a $0 year is stored as 0. The contract's end is `season + years_remaining − 1`, not where the first $0 falls.
- **View `vw_team_cap_sheet`**: active contracts with each player's yearly cap hits next to the team's cap row. Steps 3 and 5 read this.

What happens on a re-scan: a team's new scan marks that team's earlier `ACTIVE` rows for the season as `SUPERSEDED` and inserts fresh ones. Those historical rows are never updated in place, so step 2 can see what changed.

### 2.2 Transactions schema (owned by step 2, migration `2026_12_08_transactions.sql`)

- **`transactions`**: `id` PK, `season`, `week` (games.week numbering), `txn_date` text (as Madden shows it), `type` (`SIGNED` | `RELEASED` | `TRADED` | `RE_SIGNED` | `EXTENDED` | `OTHER`), `player_id` FK nullable, `player_name`, `position`, `from_team_id`, `to_team_id`, `raw_text`, `trade_group` (links the legs of one trade), `gm_proposal_id` nullable (set when it matches a step-3 proposal), `capture_id`. A unique key on its natural fields (exact columns set by the step-2 sub-plan) makes a re-scan idempotent.
- Loading a transaction moves `player_seasons.team_id`. It creates a `player_seasons` row (and a player) for a signing it doesn't recognize, marks the player's active contract `RELEASED`/`TRADED`, and records the affected teams in a re-scan list for D5.

### 2.3 Capture conventions (steps 1 and 2)
- New modes in `madden_capture.py`: `--only contracts` and `--only transactions`. New folders `captures/<run>/contracts/<NN>_<team>/` and `captures/<run>/transactions/`. Each resumes with `done.txt` like the roster and injuries do.
- New `ROSTER_MENU`/`LEAGUE_MENU` targets get added to the `cross()` guard **by name** (TEAM SALARY, LEAGUE TRANSACTIONS) per D7, each with its own screen classifier.
- `extract.py` writes `contracts.csv`, `team_cap.csv` and `transactions.csv`. `pipeline/load_capture.py` loads them, picking the mode from the folder, as one transaction.
- `pipeline/weekly_capture.py`:
  - `--mode contracts` (all teams, or `--teams CAR,NO` for a re-scan).
  - The Season scan gets a contracts phase **after rosters**, so every row matches a player.
  - The weekly run gets a transactions phase, plus a contracts re-scan of only the teams step 2 flagged.
  - Matching `#debug` buttons.

### 2.4 GM interfaces (owned by step 3; step 4 and step 5 consume)
- **`gm_profiles`**: one per team. It holds a persona (agent name, risk appetite, how much they value youth vs veterans, and how aggressive they are), the team's `direction` per season (`CONTEND` | `RETOOL` | `REBUILD`, computed from records, `team_ovr`, trajectory and Pythagorean wins, cap space and roster age), and their positional needs.
- **`gm_trade_proposals`**: the teams, the assets each side gives (players and picks), cap effects for each team taken from the contract tables, rationale, `status` (`PROPOSED` → `APPROVED` → `EXECUTED` | `REJECTED` | `EXPIRED`), `season`, `week`. A proposal becomes `EXECUTED` only when a step-2 `TRADED` transaction matches it.
- Proposals have to be legal: the cap after the trade, no-trade clauses, the trade deadline week, and roster limits. A season-wide budget aims for about 17 executed trades. Injuries to key players on `CONTEND` teams raise the chance of a trade, and the rest is left to the CPU's signings (D1).
- JR's surface: a **Front Office** section on `#debug` that lists proposals with approve/reject, steps for making each trade in Madden (which team to switch to user control and which players go where), and the status of the transactions-scan confirmation.

### 2.5 Scouting and draft (step 4, design pass, JR 2026-10-19)
The mock draft's board is the top 32 by OVR, and that isn't realistic. Step 4 starts with a design pass, not a build. Questions for that pass:
- **A living board.** Each prospect gets a scouting grade that is separate from Madden's OVR and moves during the season. It moves with simulated college performances, storylines (injuries, character flags, breakout games, combine and pro-day results) and visits. Mu invents all of it (D6), and none of it may contradict the Madden prospect data (`draft_prospects`).
- **Who sees what.** The GM agents and JR use the same scouting layer. Each team gets its own view (scout accuracy, and each GM's needs and biases from `gm_profiles`), so boards differ from team to team. JR's view is the `#debug`/frontend board.
- **Risers and fallers.** Published weekly, and fed to the newsroom (draft coverage, Agent Wire) through step 5.
- **Live drafting.** For each of the first 32 picks, the on-the-clock GM chooses from the players still available. An off-script pick (JR's own, or a correction) re-runs every pick after it. Step 4 needs a way to record each actual pick as it happens, so it can't rely on reading the Draft Recap afterwards. The existing draft-results scan then checks the result.
- **Draft-day trades** fall under step 3's proposal flow, if they're in scope at all.
- What it replaces or reuses: `engines/mock_draft_engine.py`, `engines/draft_order.py`, `pipeline/draft_board.py`, and the prospect scan.

**Scouting grade, the starting design (JR, 2026-10-19).** The step 4 sub-plan builds on this.
- **True value `V` (hidden).** Comes only from Madden's prospect data: OVR × position value (QB, EDGE, LT, CB, WR high; RB, S, off-ball LB, K/P low), plus an age adjustment and the dev trait if the board shows it. Sorting by `V` gives each prospect's *true rank*. Nobody sees `V`, not JR and not the agents. It's the yardstick for every grade.
- **Consensus grade `C_w = V + e_w`, published weekly.** The board is **noisy** by design. The week-1 error `e` is large and seeded. Each week it takes a step driven by simulated events (college games, storylines, then the combine and pro days). The steps lean toward `V`, so the error shrinks but **stays sizeable on draft day**. Risers and fallers are the weekly change in `C`. Grades are 0–100 with round labels.
- **Each team's grade `T = V + e_team + fit + bias`.** Every front office has its own error, scaled by **front office quality**: `σ_team = σ_base × q_team`.
  - `q_team` comes from the team's **recent draft track record**. Each Round 1–2 pick in the last 3 drafts gets a hit score: the player's real OVR and dev after 1–3 seasons compared with what's expected for that pick slot. More recent drafts weigh more.
  - Recent hits push `q` down (less noise), and recent misses push it up. `q` is clamped to about 0.7–1.4, so even the best front office stays noisy on draft day.
  - Starting values: whatever `draft_selections` + `player_seasons` history exists, and neutral (`q = 1`) where there's none.
  - After each draft, the rookie class's real Madden ratings feed back into `q` for the next year.
  - The same hit rates are writer facts, e.g. "three straight first-round misses" (step 5).
  - `fit` comes from needs and scheme, and `bias` from the GM persona (both step 3).
- **Calibration target: about 25% poor first-round picks.** JR's read of the 2025 NFL draft is that about a quarter of teams made a poor pick.
  - A *poor pick* is a Round 1 pick whose true rank is more than 32 spots worse than the slot, i.e. a Day 2 player taken in Round 1.
  - `σ_base` and the draft-day residual are tuned so that simulated drafts land at **20–30% poor picks**, with poor picks clustered among high-`q` (noisy) front offices.
  - The engine gets a `--calibrate` mode (e.g. 1,000 simulated drafts → the poor-pick rate overall and by `q` band), and a test locks in the band.

---

## 3. Console facts (JR confirmed 2026-10-19)

1. **TEAM SALARY:** the columns across both edges are NAME, CONV, POS, AGE, OVR, LEN, REM, TOTAL, BONUS | BONUS, SAVINGS, PENALTY, 2036–2040, INCENTIVE, NO TRADE CLAUSE. **NAME scrolls away** when the table moves right, so rows are matched by position (`capture_wide_list`). **11 rows a page.** **L2 cycles teams** the same way the Injury Report does. The player card on the right repeats the selected row (name, number, archetype, dev, salary for each year) and isn't needed. The header strip (36 CAP … 37 ROOKIE RES) appears on every team.
2. **The "36" in "36 CAP" means 2036, the current season.** It's the league-wide salary cap for this year. The year columns 2036–2040 are the current season plus the next four. Labels are mapped from the header's year, never from `league_state` arithmetic alone. The loader checks that the two agree and stops if they don't.
3. **LEAGUE TRANSACTIONS** is under the League tab (`LEAGUE_MENU`). It's a list, with an L2 chip (NFL) and an R2 filter by transaction type (ALL by default), and **no week filter**. Each row has a team logo and headshot, a heading `2036 SEASON - WEEK 4`, and a line of text, for example:
   - `JAGUARS SIGNED FREE AGENT (WR) DEVONTA TAYLOR TO 1 YEAR, $3.93M CONTRACT`
   - `JAGUARS RELEASE (WR) JULIUS SLATON`

   The list is newest first. The capture scrolls down until it reaches a row older than the last week loaded, which keeps a weekly scan short. **The CPU only signs free agents to 1-year deals.** Contract re-signings also show up in the list.
4. **CPU trades are now turned off.** Switching team control in the middle of a season is allowed, and the team keeps its CPU settings when switched back.
5. **The receiving team takes on the same contract** in a trade. Step 2 moves the active `player_contracts` row (`TRADED` → a new `ACTIVE` row with `source = TRANSACTION` on the new team, same years). The D5 re-scan confirms it.
6. **The draft:** JR can't pick for a team he doesn't control. He can control teams for the first 32 picks and then simulate the rest (see D4 and step 4).

**Re-signings (decision D8): included.** Re-signings and extensions are stored as `RE_SIGNED`/`EXTENDED` transactions. They don't change the current season's contract row, but they flag the team for the D5 contracts re-scan. Why include them: they change future cap hits, which the GM agents' trade legality and future-cap reasoning depend on (step 3). They're also news (step 5). Leaving them out would mean the GM agents reason from stale future years until the next Season scan.

---

## 4. Risks
- **Matching names after transactions:** a CPU signing brings in players the roster scan never saw. Step 2 creates them the same way the roster loader does (`capture_matching`), and the next roster scan reconciles them.
- **Cap drift:** CPU re-signings and extensions change contracts without a trade. D5's per-team re-scan catches every team that shows up in that week's transactions. Teams with no moves can drift until the next season scan. That's acceptable for now.
- **Making trades by hand:** JR may change a trade or skip it. The transaction scan is the source of truth, so a mismatch leaves the proposal `APPROVED` and flags it, without editing anything.
- **Shared checkout:** other sessions edit `PROJECT_SPEC.md` and `init_db.sql` at the same time. Sub-agents stage only their own hunks, and migration dates are taken from §2.1/§2.2 so they don't collide.

## 5. Status
| Step | Sub-plan | Build |
|---|---|---|
| 1 Contract scan | written | not started |
| 2 League transactions | written | not started |
| 3 GM agents | not written | not started |
| 4 Scouting and draft | design pass needed (§2.5) | not started |
| 5 Newsroom integration | not written | not started |
