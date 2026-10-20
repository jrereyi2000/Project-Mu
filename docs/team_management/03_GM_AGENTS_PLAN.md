# Step 3: GM agents (sub-plan)

Written 2026-10-20 for [`TEAM_MANAGEMENT_PLAN.md`](../TEAM_MANAGEMENT_PLAN.md) step 3. This plan owns master §2.4 (`gm_profiles`, `gm_trade_proposals`, the Front Office surface). It reads step 1's contract tables (§2.1) and step 2's `transactions` (§2.2) only through the interfaces the master plan fixes.

**Delivers:** 32 stable GM personas, a per-season team direction and positional needs, a data-fitted trade value model, a weekly proposal engine that paces toward ~17 in-season and 15–20 offseason executed trades a season, draft-pick ownership, a Front Office section on `#debug` where JR approves, rejects and confirms trades, the moves a confirmed trade makes (players, contracts, cap rows, picks, `transactions` rows), and read helpers that steps 4 and 5 consume.

**Build state this plan starts from (checked against the live DB, 2026-10-20):** `league_state` = (2036, 4), and week 4 is the next week to play (`games` week 3 COMPLETED, week 4 SCHEDULED). 1,695 `ACTIVE` 2036 contracts, 7 with `no_trade_clause`, every one with `cut_penalty`. 32 `team_cap_seasons` rows. 181 `transactions` (113 RE_SIGNED, 32 SIGNED, 24 RELEASED, 12 RESTRUCTURED). The transactions load (capture 20) ran **before** the contract scan (capture 21), so there are no `derived_at` cap rows and **no `projected` contract-year rows yet**. Both appear once weekly scans run on top of the contract baseline, and the value model handles both from day one.

**Mismatch with the brief, resolved in favour of the master plan and the code:** the brief says a matching scanned TRADED transaction marks a proposal EXECUTED. Master D5 and §2.4 (JR, 2026-10-19), and the code, say otherwise: `pipeline/load_transactions.py:82` drops trade rows, and `:556` only lists them under `skipped_trades`. So **JR's "Made it" click on `#debug` is what marks a proposal EXECUTED and makes the moves.** The scan's trade rows become *sightings* that corroborate or flag a confirmed trade (§2.6). They never execute anything.

---

## 0. Survey (code and DB facts that set the scope)

| # | Fact (file:line / DB) | Consequence for step 3 |
|---|---|---|
| S1 | `team_seasons` has `wins`, `losses`, `team_ovr`, `off_ovr`, `def_ovr`, `opp_adj_credit`, `opp_adj_credit_recent`, `sos_rating`, `expected_wins`, `trajectory_multiplier`, `pythagorean_wins`. All 32 rows are filled for 2035 and 2036, and none for 2034. There is no ties column. | Direction uses exactly these columns, with 2035 as the prior. Games played = `wins + losses`. |
| S2 | `trajectory_multiplier` (`engines/game_credit_engine.py:71`) is 1.00 until `TRAJECTORY_MIN_GAMES`, so all 32 teams read 1.00 today. `expected_wins` and the rest are written at `:206`. `pythagorean_wins` comes from `engines/team_efficiency_engine.py:30`, written at `:112`. Both run inside the weekly engines step. | The GM step runs **after** `step_engines` (`pipeline/weekly_capture.py:341`, called at Step 7 around `:708`). Trajectory gets a small weight, and the weight is 0 while T = 1.00. |
| S3 | `team_cap_seasons`: `cap`, `adjusted_cap`, `cap_space` (can be negative), `salaries`, `cap_penalty`, `total_spend`, `derived_at`, `derived_transaction_id`. `vw_team_cap_sheet` gives `cap_hit_y0..y4`, `years_remaining`, `contract_end_season`, `cut_penalty`, `cut_savings` and `no_trade_clause` per active contract. CAR shows `cap_space` $161M (adjusted cap $411M, total spend $250M). | Legality and value read the view plus `player_contract_years.projected`. Before the first live run, JR sanity-checks CAR's outlier (§1 C11). |
| S4 | `player_contracts.status` is `ACTIVE`/`SUPERSEDED`/`RELEASED`/`TRADED`. `pipeline/contracts.py:19` `insert_contract(...)` and `:35` `upsert_team_cap(...)` are the write helpers. | Trade execution reuses `insert_contract` for the new-team copy (master §3.5) and writes cap deltas the way step 2 does (`derived_at` stamp). |
| S5 | `pipeline/load_transactions.py`: `plan_effects` (`:210`) is pure and returns ops (`move`, `contract_status`, `cap_delta`, …), and `execute` (`:450`) runs them. Trade rows are dropped at `:82`, and only their text reaches `rep["skipped_trades"]` (`:556`). | Trade moves copy this pure-plan/execute split (`pipeline/trade_moves.py`). Sightings need a small change to the loader (Phase 5). |
| S6 | `pipeline/transactions_parse.py:196` `_parse_trade` already reads teams, player legs and `YYYY <N> ROUND PICK` assets from a trade row. Its wording is **unverified**. | Sightings reuse it. Console fact C8 confirms the wording. |
| S7 | `transactions.gm_proposal_id INT` has no FK ("step 3 adds it", step-2 plan Phase 1). `trade_group TEXT` is indexed. The unique key is `(season, txn_date, row_key, seq)`. | Step 3 adds the FK and writes one `TRADED` row per leg with `row_key = 'gm:<proposal>:<leg>'`, which can't collide with scanned keys. |
| S8 | `player_seasons`: `team_id`, `position`, `age`, `overall_rating`, `dev_trait` (`Normal`/`Star`/`Superstar`/`Superstar X-Factor`, 92 blank in 2036), `years_of_experience` (0 = rookie), `released_week` (a released player keeps his `team_id`). Active roster per team: 53–56 rows, which includes one `position = 'DEF'` pseudo-row. | Roster = `released_week IS NULL AND position <> 'DEF'`. The roster limit check needs console fact C3. |
| S9 | `injuries`: `season`, `week`, `team_id`, `player_id`, `ovr`, `duration`, `weeks_remaining`, `is_ir`, loaded weekly (weeks 2–4 present). | Injury trigger input (§2.5). |
| S10 | `engines/mock_draft_engine.py:30` `POSITION_GROUP_MAP` and `:46` `POSITIONAL_PREMIUM` (QB 1.25 … RB 0.80). | Reuse the group map. Trade position weights start from the premium table and get their own constant, because veteran value isn't draft value. |
| S11 | `engines/napper_trade_engine.py` values players by `fantasy_projections.ros_total_points` (`:3`). Reusable *ideas* only: the value-ratio band 1.05–1.35 (`:662–663`), the duplicate-pending check (`:530`), stochastic acceptance (`:975–990`), and `trades_open`/`expire_pending_trades` (`:39`, `:44`, deadline `:36` = 11, which is Napper's own). | Nothing imported. The Mu engine copies the deadline/expire shape and the duplicate guard. |
| S12 | `draft_prospects` holds the 2036 draft's results (`actual_pick`, `drafted_team_id`, 224 rows; 182 linked to `player_id`, 177 with an active contract). Average 2036 cap hit by round: R1 $7.27M, R2 $2.57M, R3 $1.84M … R7 $1.24M. **There's no `draft_selections` table.** Migration `2026_11_26_draft_selections.sql` dropped it, and master §2.5 still names it. | The pick value model fits the expected OVR and rookie hit by slot from real 2036 data. Step 4's `q_team` seeds from `draft_prospects`, not `draft_selections` (flagged for the master plan, §6). |
| S13 | `engines/draft_order.py:59` `compute_draft_order(teams, games)` assumes every team owns its own picks. | Pick ownership is new (`draft_picks`). Step 4 maps the slot order through it. |
| S14 | `coaches` (scheme and tendency columns) and `coach_tenures(coach_id, team_id, start_season, end_season)`, read through `engines/coach_profile.py:157` `fetch_team_coach`. | GM tenure copies the `coach_tenures` shape. The GM rationale may cite the head coach and scheme from this data. |
| S15 | `newsroom/config/agent_personas.json` = `{archetypes, article_personas}`: system prompts for **writers** (ESPN, THE_ATHLETIC …), loaded by the newsroom. | GMs aren't writers. They're league people that writers cover, so they live in the DB next to coaches (§2.1). |
| S16 | `server/debug_api.py`: `JOB_KINDS` (`:34`), `require_token` (`:55`), `GET /transactions` (`:195`), and `job_command` (`:553`), which already special-cases `publish_articles` to run another module. `Mu_Network/src/DebugPage.tsx`: `TransactionsCard` (`:62`) is rendered in `Control` (`:840`). | The Front Office is one more card plus endpoints in the same style. The `gm_proposals` job uses the same special case. |
| S17 | Gemini: `genai.Client` used directly (`newsroom/agent_memory.py:287` `gemini_text`), metered through `db/llm_usage.py:28` `UsageMeter`. | The rationale writer uses the same client and meter, and falls back to a template without a key. |
| S18 | `tests/test_debug_api.py` patches `JOBS_DIR`/`env` and uses no DB. `test_napper_api`/`test_napper_engines` hit the live DB (memory). | New tests are pure or mock `connect()`. They never open a DB connection. |
| S19 | The last migration is `db/migrations/2026_12_09_transactions_restructured.sql`. | Step 3 takes **`2026_12_10_gm_agents.sql`**. Step 5, written in parallel, should start at `2026_12_11`. |

---

## 1. Console facts to check (JR at the console; don't build on these unverified)

| # | Question | Blocks |
|---|---|---|
| C1 | **Trade deadline week** in this franchise's settings, and whether it's enforced for trades a user team starts. | `TRADE_DEADLINE_WEEK` (a placeholder until confirmed), Phase 4 |
| C2 | With CPU trades off, does Madden's trade screen still **judge a user offer** (approval meter, "decline") when the other team is CPU? If it can refuse, does making **both** teams user-controlled let JR push any trade through? | Execution steps (§2.7), and whether a `madden_acceptable` guard is needed (§2.4) |
| C3 | Roster max and min in season and in the offseason, and whether a trade that breaks them is blocked. | Roster legality |
| C4 | On a trade, does the **sending** team take a cap penalty (the PENALTY column, or the remaining bonus)? Does the receiving team's hit equal the year column? | `TRADE_DEAD_MONEY` mode (`cut_penalty` or `none`) |
| C5 | Which **draft years** and rounds can be traded, and does the trade screen show each team's owned picks? (CPU trades were on before, so some 2037 picks may have moved already.) | `draft_picks` seed check |
| C6 | Does a no-trade clause block a trade outright? | NTC rule (assumed: block) |
| C7 | Can players signed or re-signed this season be traded, and can injured or IR players? | Asset filters |
| C8 | The wording of one trade row in LEAGUE TRANSACTIONS (photo after the first real trade). | Sighting grammar (Phase 5) |
| C9 | Does JR permanently control a team? | Whether that team's GM is advisory only (§6 Q1) |
| C10 | Can a user team trade with a team that's also user-controlled, and how is the other side accepted? | §2.7 step text |
| C11 | CAR shows $161M cap space (adjusted cap $411M). Is that what Team Salary shows? | Phase 2 exit check |

---

### 1.1 JR's answers (2026-10-20)
These replace the matching assumptions and placeholders everywhere in this plan.
- **C1:** The trade deadline is **Week 8**: the last trade happens before simming into Week 9. Madden enforces it for user trades too. `TRADE_DEADLINE_WEEK = 8`.
- **C2:** CPU trades stay off. JR turns them on only to push a trade through, so the CPU never makes a move of its own. **Treat any agreed trade as one JR can complete.** Madden's approval meter is not a constraint.
- **C3:** Roster minimum is **46** in every phase of the season. Maximum is **53** in season and **65** in the offseason.
- **C4:** Yes. The sending team takes a cap penalty on a trade, and the receiving team's cap hit equals the year column.
- **C5:** Picks in the **next three drafts** can be traded (from 2036: 2037–2039).
- **C6:** Yes. A no-trade clause blocks a trade outright.
- **C7:** Players signed or re-signed this season **can** be traded. Injured players **can't**.
- **C8:** Still open. It can't be checked until the first trade goes through.
- **C9:** JR currently controls the **Panthers and the Colts**. JR can **take GM control of any team** at any time, and that team's GM agent then acts as an **advisor** only.
- **C10:** Yes. JR switches to the other team to accept.
- **C11:** Yes. CAR's $161M cap space matches Team Salary.
- **Trade volume:** about **17 trades in the regular season** and **15–20 in the offseason**.

## 2. Design

### 2.1 GM personas (`gm_profiles`): separate from coaches and from newsroom personas
- **Separate table, not `agent_personas.json`.** Newsroom personas are writer system prompts. A GM is a league entity like a coach, with tenure, and step 4 and the writers need to query him. GMs follow the `coach_tenures` pattern, but in their own table because the roles differ. Coaches don't trade and don't draft.
- **Columns:** `id`, `team_id`, `gm_name`, `bio` (short, invented), `voice` (style notes for the rationale writer), `start_season` (invented tenure start, never before 2030), `end_season` NULL. Then the persona: `risk_appetite`, `aggressiveness`, `pick_affinity` (0.6–1.4), `youth_bias` (−1 veterans … +1 youth), and `position_bias` JSONB ({group: multiplier}, each within ±15%, at most 3 groups). The first two are 0–1. `seed TEXT`, `created_at`. Only one open tenure per team (`UNIQUE (team_id) WHERE end_season IS NULL`).
- **Generation:** `engines/gm_personas.py seed`. It is deterministic: `random.Random(sha256("gm:" + abbreviation))`. The numeric traits are stratified across the league, so there are always 6–10 GMs with aggressiveness ≥ 0.65 and 6–10 with ≤ 0.35. Names, bio and voice come from one Gemini call per GM, given the traits, team and head coach (S14), with a seeded name-pool template as the fallback. It's idempotent (`ON CONFLICT DO NOTHING`).
- **Stable across seasons:** rows are never regenerated. The season rollover only adds `gm_team_seasons` rows. Replacing a GM means closing the tenure and seeding a new row (`seed --replace TEAM`, by hand from `#debug`). An automatic hot seat is out of scope (§6 Q4).
- **Hallucination policy:** the tenure and bio are invented, untracked narrative. The rationale validator (§2.8) stops a GM from claiming stored facts that aren't in his fact sheet. Before his `start_season`, a GM has no draft history in the data, so the bio mustn't credit him with specific picks.

### 2.2 Team direction and needs (`gm_team_seasons`)
§2.4 says `gm_profiles` holds the direction and needs "per season". To keep one persona row per team, they go in a child table, `gm_team_seasons(team_id, season)`, and the view **`vw_gm_profiles`** joins the open tenure with the current season. That way consumers still see §2.4's single "GM profile". This extends §2.4 without changing it.

Columns: `direction` (`CONTEND`/`RETOOL`/`REBUILD`), `direction_score`, `direction_week`, `direction_inputs` JSONB, `needs` JSONB (ranked `[{group, slot_gap, injury, expiring, score}]`), `surplus` JSONB, `urgent_need` (injury trigger, §2.5). There are also **step 4's columns**, which step 3 creates but never writes after their initial value: `fo_quality NUMERIC(4,2) DEFAULT 1.00` (`q_team`, CHECK 0.5–1.6), `fo_quality_inputs JSONB`, `fo_quality_at`.

**Direction score** (pure `direction_score(inputs)`, constants in `engines/gm_direction.py`):
- `g = wins + losses`, and `form = (pythagorean_wins + expected_wins) / (2g)`.
- `prior = 0.5 × wins₂₀₃₅/17 + 0.5 × pct_rank(team_ovr)`.
- `p = (g/(g+6)) × form + (6/(g+6)) × prior`, then `proj_wins = wins + p × (17 − g)`.
- `D = z(proj_wins) + 0.5·z(team_ovr) + 0.25·z(opp_adj_credit_recent) + 4·(trajectory_multiplier − 1) − 0.3·max(0, starter_age − 27.5)`.
  - `starter_age` is the mean age of each team's starters by slot (below).
- The persona tilt is `+0.2·(aggressiveness − 0.5)`.
- **Bands by rank of D:** the top 10 are CONTEND, the bottom 8 are REBUILD, and the rest are RETOOL.
- **Overrides:** a team over the cap with `cap_space < 0` can't be REBUILD-buying. A team at `proj_wins ≥ 10.5` is always CONTEND.
- **Hysteresis:** the direction is set preseason, then re-checked only at checkpoint weeks (`{1, 6, TRADE_DEADLINE_WEEK − 1}` and post-season). It changes only when D crosses a band edge by more than 0.3.

**Needs** (pure `positional_needs(roster, injuries, contracts, league)`):
- Starter slots: QB1, RB1, WR3, TE1, OT2, IOL3, EDGE2, DT2, LB2, CB3, S2, K1, P1, grouped through S10's map.
- A team's slot OVRs are its top-n `overall_rating` in the group. A player is left out when he is injured with `weeks_remaining ≥ 3` or `is_ir`.
- `slot_gap = max(0, league 60th-percentile slot OVR − team slot OVR)`.
- `score = Σ gap × TRADE_POS_WEIGHT[group]`, plus `0.5 ×` the same score for slots whose starter is on an expiring deal (`years_remaining = 1`, `extension_years IS NULL`). The expiring part is weighted ×1.5 for REBUILD and RETOOL.
- **Surplus** is a group where the (n+1)th player is within 3 OVR of the starter line, and those depth players are tradeable without creating a need.

### 2.3 Trade value model (`engines/trade_value.py`, pure; fits recomputed each run from live data)
Everything is in **dollars of surplus**, so players and picks compare directly.
1. **Market worth `W(ovr, group)`:** a log-linear fit, `ln(cap_hit) = a_group + b·(ovr − 75)`. It uses 2036 `cap_hit` from `ACTIVE`, `SCAN` contracts with `years_of_experience ≥ 4` (no rookie deals). A group with fewer than 15 rows shrinks to the league intercept.
2. **Aging `Δovr(age, group)`:** the mean change in same-player `overall_rating` from 2035 to 2036, by age band and group, shrunk toward a generic curve where the sample is small. Dev trait adds +1/+2/+2 OVR a year at age ≤ 26 (Star/Superstar/X-Factor). A blank dev trait counts as Normal.
3. **Player value for team T:** `V_T = Σ_{y < horizon} d_T[y] × (W(ovr_y, group) × u_T(group, y) − cap_hit_y)`.
   - `cap_hit_y` comes from `player_contract_years`, including `projected` rows (flagged in the output).
   - After `contract_end_season` the term is 0, because the player re-signs at market.
   - `u_T` = 1.0 for a starter for T, 1 + 0.5·(need score share) for a slot T needs, and 0.3 for a backup. `position_bias` scales it.
   - `d_T` by direction: CONTEND `[1, .55, .35, .25, .2]`, RETOOL `[.8, .75, .6, .5, .4]`, REBUILD `[.35, .8, .9, .9, .85]`.
   - `youth_bias` multiplies by `1 − 0.05·youth_bias·(age − 28)`, clamped to 0.7–1.3.
   - A neutral **league value** `V_L` (RETOOL discounts, `u = 1`) is stored for fairness checks and for step 5.
4. **Pick value:** the expected OVR by overall pick is fitted from the 2036 draft (`draft_prospects.ovr` by `actual_pick`, smoothed). The rookie hit by slot comes from the 2036 rookies' contracts (S12).
   - `V(pick) = Σ_{k<4} d[k] × (W(ovr_k) × u − rookie_hit_k)`, × `pick_affinity`, × 1.2 for REBUILD or × 0.8 for CONTEND.
   - The slot is projected from a team's `proj_wins` rank for next year's draft. Picks a year further out use the middle slot of their round, with one more year of discount.
5. **Excluded assets:** `no_trade_clause`, the `DEF` row, players acquired by trade this season (no flipping), injured players with `weeks_remaining ≥ 4`, and anything C7 rules out.

### 2.4 Proposal generation (`engines/gm_trade_engine.py`)
**When:** after step 7 (engines) in a weekly or playoff run, as a non-fatal step like the Napper lineups (`weekly_capture.py:375`), and on demand from `#debug`. It runs for `week = league_state.current_week` (the week about to be played) while `trades_open(week)` holds: `1 ≤ week ≤ TRADE_DEADLINE_WEEK`. The offseason window comes in Phase 7.

**Order:**
1. Expire stale proposals.
2. Recompute the direction (at checkpoints) and the needs (every week).
3. Raise injury triggers.
4. Work out the budget.
5. Search.
6. Check legality.
7. Check acceptance.
8. Pick.
9. Write the rationale.
10. Store.

**Budget (JR, 2026-10-20):**
- Two targets: `IN_SEASON_TARGET = 17` executed through the Week 8 deadline, and `OFFSEASON_TARGET = 17` (drawn from the range 15–20 each offseason) including draft day. The offseason uses the same pacing over its own stages.
- `TRADE_DEADLINE_WEEK = 8` is the NFL deadline. It is separate from Napper's fantasy `TRADE_DEADLINE_WEEK = 11` in `engines/napper_trade_engine.py`, so don't import that one.
- The in-season weekly shape is flat, with the last two weeks before the deadline weighted 3×.
- This week's expected executions: `e_w = (in-season target − executed so far) × shape_w / Σ remaining shape`.
- `p_exec` = executed ÷ decided proposals, as a Beta(3,2) posterior. It starts at 0.6 and falls if JR rejects a lot.
- New proposals this week: `n_w ~ Poisson(e_w / p_exec)`, using a seeded RNG `random.Random(f"{season}:{week}")`, so a re-run gives the same draw.
- Caps: at most 4 new proposals a week, at most 6 open proposals (PROPOSED or APPROVED), and each team in at most 1 new proposal a week.
- For 2036, the in-season target is scaled by the remaining shape (week 4 onward, up to the Week 8 deadline; §7 Q2).

**Search:**
- Initiators are drawn with weight `need_score × aggressiveness × direction factor` (CONTEND 1.5, RETOOL 1.0, REBUILD 0.6 as buyer; REBUILD also sells).
- For the initiator's top 2 needs, targets are players on other teams who would start for the initiator and whose own team values them below the initiator's value (`V_seller < V_buyer`). Directions make most of this gap: a REBUILD seller discounts this year.
- Packages come from the initiator's surplus players, picks and backups. There are at most 3 assets a side, the search is exhaustive over the top 8 candidates a side, and every deal is two-team.

**Legality** (pure `check_legality(deal, books)` → `{check: pass/fail, detail}`, all stored):
- **Cap:** for each team, `cap_space − Σ incoming 2036 cap_hit + Σ outgoing cap_hit − dead money ≥ CAP_MARGIN`. Dead money is `cut_penalty` when C4 says the sender pays one. `CAP_MARGIN` is $1M, or $3M when the team's cap row is derived (`derived_at > scanned_at`). Under $3M of headroom the proposal is flagged "run Contract scan --teams A,B first" (master D5).
- **No-trade clauses:** any NTC player fails.
- **Roster:** each team's active count after the trade is within the C3 limits, and no team drops below its slot minimum in QB, K or P.
- **Deadline:** `trades_open(week)`.
- **Picks:** the sender owns each pick in `draft_picks`, and the pick isn't already in another open proposal.
- **No double booking:** no asset appears in another PROPOSED or APPROVED proposal.
- **Fairness guard:** `|V_L(give) − V_L(get)| ≤ max(25%, $4M)`. This keeps deals Madden's trade screen would plausibly allow (C2) and stops step 5's "fleeced" angle from being absurd.

**Acceptance:** both GMs must want the deal.
- `Δ_T = Σ V_T(in) − Σ V_T(out) ≥ θ_T`, with `θ_T = $1.5M × (1 − aggressiveness)`.
- A CONTEND team with an urgent need may accept down to `−$2M × risk_appetite`, which models a deadline overpay.
- Among candidates that pass both sides, the engine picks the best joint surplus `min(Δ_A, Δ_B)`. A seeded softmax draw gives some variety.

**Storage and expiry:** see §2.6. A proposal expires when the league moves past `expires_week` (= the week it was made), when the deadline passes, or when a re-check finds it illegal. A re-check runs on each engine run and on approve. JR may still confirm an expired proposal he has already made at the console (§2.7).

### 2.5 Injury trigger
- **Input:** the latest `injuries` week. The trigger fires when the player is a starter by slot on a **CONTEND** team, in a group with `TRADE_POS_WEIGHT ≥ 1.0` or at QB, and he has `is_ir` or `weeks_remaining ≥ 4`.
- **Effect:** it sets `gm_team_seasons.urgent_need = {group, player_id, weeks_remaining}` and makes the team the first initiator with probability `0.35 × (0.5 + aggressiveness) × premium`.
- **Budget:** the trigger stays inside the budget, except that it may add one proposal beyond a week with no budget left (probability 0.5).
- Every other injury is left to the CPU's signings (D1). The trigger clears when the player's `weeks_remaining` falls under 2 or the need is filled.

### 2.6 Schema (`db/migrations/2026_12_10_gm_agents.sql`, folded into `init_db.sql`)
- `gm_profiles`: §2.1.
- `gm_team_seasons`: §2.2, PK `(team_id, season)`.
- `vw_gm_profiles`: the open tenure joined with `gm_team_seasons` for `league_state.current_season`.
- `draft_picks`:
  - Columns: `draft_season`, `round` (1–7), `original_team_id`, `owner_team_id`, `overall_pick` NULL (step 4 fills it once the order is set), `source` (`SEED`/`TRADE`/`JR`), `acquired_proposal_id` NULL, `used` bool. PK `(draft_season, round, original_team_id)`.
  - Seeded with every team owning its own picks for `season+1` and `season+2` (the years C5 allows). JR corrects any CPU-era pick trade with the `#debug` table editor (`PATCH /tables/draft_picks`).
- `gm_trade_proposals`:
  - Ids and timing: `id`, `season`, `week`, `window` (`IN_SEASON`/`OFFSEASON`/`DRAFT_DAY`), `trigger` (`NEED`/`INJURY`/`SELL`/`DRAFT`), `expires_week`.
  - Teams and values: `team_a_id` (initiator), `team_b_id`, `value_a`, `value_b` (each side's Δ), `league_value_a`, `league_value_b`.
  - Cap and roster: `cap_space_before_a/b`, `cap_space_after_a/b`, `dead_money_a/b`, `roster_after_a/b`, `cap_derived_a/b`.
  - Checks and text: `legality` JSONB, `rationale_a`, `rationale_b`, `headline`, `rationale_source` (`LLM`/`TEMPLATE`).
  - Status: `status` (`PROPOSED` → `APPROVED` → `EXECUTED` | `REJECTED` | `EXPIRED`), `status_reason`, `approved_at`, `executed_at`, `executed_as` (`PROPOSED` or `JR_EDITED`).
  - Step 5 and housekeeping: `rumor_ok` bool (step 5; seeded, about 40% of non-executed proposals), `flags` TEXT[], `deal_hash`, `created_at`.
  - Constraints and indexes: `UNIQUE (season, deal_hash)` and an index on `(season, status)`.
- `gm_trade_assets`: `proposal_id` FK CASCADE, `from_team_id`, `to_team_id`, `asset_type` (`PLAYER`/`PICK`), `player_id`, `contract_id`, `pick_draft_season`, `pick_round`, `pick_original_team_id`, `cap_hit_now`, `value_from`, `value_to`, `value_league`.
- `gm_trade_sightings`: `id`, `season`, `txn_date`, `raw_text`, `parsed` JSONB, `capture_id`, `proposal_id` NULL, `match_status` (`MATCHED`/`PARTIAL`/`UNMATCHED`), `UNIQUE (season, txn_date, raw_text)`.
- `ALTER TABLE transactions ADD CONSTRAINT … FOREIGN KEY (gm_proposal_id) REFERENCES gm_trade_proposals(id) ON DELETE SET NULL`.

### 2.7 Execution (`pipeline/trade_moves.py`) and the Front Office
**Steps shown for an APPROVED proposal** (generated text):
1. Options > User Teams: switch **{team A}** to user control. If C2 or C10 require it, switch {team B} too. Skip a team JR already controls.
2. Trade with {team B}. Send: {pos name OVR, "2037 R2 (own)"}. Receive: {…}.
3. Accept or submit. If Madden refuses, click **Couldn't make it** (the proposal goes back to EXPIRED with the reason).
4. Switch the team or teams back to CPU.
5. Click **Made it**.

The card also shows each team's cap after the trade, and a flag when the cap row is derived and headroom is under $3M.

**"Made it"** (`POST …/confirm`): `plan_trade_ops(proposal, state)` is pure, and `execute_trade(cur, ops)` runs everything in **one DB transaction**:
- `player_seasons.team_id` moves to the receiver.
- The `ACTIVE` contract becomes `TRADED`, and a row-for-row copy is inserted on the new team with `source = 'TRANSACTION'`, through `insert_contract`, copying every `player_contract_years` row and its `projected` flag (master §3.5).
- Cap rows for both teams: `salaries` ∓ the 2036 hit, `cap_penalty` + dead money (C4 mode), `total_spend`/`cap_space` moved to match, and `derived_at` stamped.
- Picks: `draft_picks.owner_team_id` changes and `source = 'TRADE'`.
- One `transactions` row per leg: `type = 'TRADED'`, `trade_group = 'gm:<id>'`, `gm_proposal_id`, `row_key = 'gm:<id>:<n>'`, `txn_date = '<season> SEASON - WEEK <week>'`, synthesized `raw_text`, `parse_ok = true`, `applied = true`, `capture_id` NULL.
- The proposal becomes `EXECUTED`.

**"Made a different trade"** (`POST …/record`): JR enters the teams and assets on the card, and the same `plan_trade_ops` runs with `executed_as = 'JR_EDITED'`. This is JR's own input, never inferred from a scan.

**Sightings and mismatch flags** (computed and shown, **never auto-edited**):
- `load_transactions` writes its skipped trade rows into `gm_trade_sightings`, guarded by `to_regclass`, the same way step 2 guards contracts.
- `front_office.match_sightings` links each sighting to an EXECUTED proposal by teams and name keys, and sets only `match_status`/`proposal_id` on the sighting.
- Flags:
  - **Unmatched sighting:** Madden logged a trade nobody confirmed.
  - **Not yet seen:** an EXECUTED proposal with no sighting after the next transactions scan.
  - **Moved since:** a later roster scan puts a traded player on a different team, i.e. `player_seasons.team_id ≠` the receiver, or the contract's team ≠ the roster's team.
  - **Picks:** a pick count that disagrees with C5's screen, when JR records one.

### 2.8 Deterministic engine vs Gemini
- **Computed (deterministic, tested):** personas' traits, direction, needs, values, budget, legality, acceptance, choice, steps, cap effects and flags.
- **Gemini (optional, metered, label `gm_rationale`):**
  1. The persona name, bio and voice, once at seeding.
  2. Each side's 2–3-sentence rationale and a headline, in the GM's voice.
- The rationale prompt gets a **fact sheet**: assets with OVR, age, position, dev, cap hits and years left; both teams' record, direction, needs, cap before and after; the head coach. It is told to invent untracked colour freely (front-office chatter, how long he's liked the player) but never to state a number, player, team or contract fact that isn't on the sheet.
- `validate_rationale(text, sheet)` rejects text whose numbers, `$` amounts, player names or team names aren't on the sheet. On a rejection or with no key it falls back to a template. `rationale_source` records which.
- The LLM never chooses or changes a deal.

### 2.9 Read helpers (`engines/front_office.py`): the hand-off surface
- `get_gm(cur, team_id, season)`, which includes direction, needs and `fo_quality`.
- `team_directions(cur, season)`.
- `draft_pick_owner(cur, draft_season, round, original_team_id)`.
- `trade_value(...)`, re-exported from `trade_value.py`.
- `executed_trades(cur, season, week=None)`.
- `trade_rumors(cur, season, week)`: non-executed proposals with `rumor_ok`, teams and asset *types/positions* only, never marked as done.
- `deadline_summary(cur, season)`: buyers and sellers by direction, executed trades by week.
- `front_office_state(cur, season)`, for `#debug`.

---

## 3. Phases

### Phase 1: Schema, personas, pick seed
**Files:**
- `db/migrations/2026_12_10_gm_agents.sql` (§2.6, idempotent, `BEGIN/COMMIT`, the same header as `2026_12_08`).
- `db/init_db.sql` fold-in (stage only this hunk).
- `engines/gm_personas.py`, with the CLI `python3 -m engines.gm_personas seed [--season S] [--replace TEAM] [--no-llm] [--dry-run]`. It also seeds `draft_picks` for S+1 and S+2 and creates `gm_team_seasons` rows with `fo_quality = 1.00`.

**Tests** (`tests/test_gm_personas.py`, pure):
- The same seed gives the same traits.
- The stratification bounds hold.
- `position_bias` stays within limits.
- The template fallback works without a key.
- The pick seed gives 32×7×2 rows.

**Exit check:** the migration is applied after `./db/backup_db.sh` (JR approves). Seeding gives 32 GMs. JR reviews them on `#debug` Tables.

### Phase 2: Direction and needs
**Files:**
- `engines/gm_direction.py`: the pure `direction_score`, `assign_directions` (bands, overrides, hysteresis), `starter_slots` and `positional_needs`, plus a loader that writes `gm_team_seasons`. CLI: `python3 -m engines.gm_direction --season 2036 [--week W] [--dry-run]`.

**Tests** (`tests/test_gm_direction.py`):
- The band counts are right.
- Hysteresis holds inside the margin.
- The early-season prior dominates at g = 3.
- An injured starter opens a need.
- An expiring starter is weighted more for REBUILD.
- `DEF` rows are ignored.

**Exit check:** `--dry-run` prints the 32 teams with D, direction and their top needs, and JR sanity-checks them. CAR at 3–0 and OVR 93 should be CONTEND. C11 is answered.

### Phase 3: Trade value model
**Files:**
- `engines/trade_value.py`: `fit_market(rows)`, `fit_aging(pairs)`, `fit_pick_curve(draft_rows, rookie_hits)`, `player_value(p, team_ctx, fits)`, `pick_value(pick, team_ctx, fits)`, `league_value(...)`. CLI: `--report` prints the top 40 players by `V_L`, the pick chart and each group's fit.

**Tests** (`tests/test_trade_value.py`, fixture rows only):
- `W` is monotone in OVR.
- Aging shrinks toward the generic curve on small samples.
- Surplus ends at the contract's end.
- Projected years are counted and flagged.
- A REBUILD team values a 31-year-old below what a CONTEND team does.
- The pick curve is monotone and R1 > R2.
- NTC and DEF players are excluded.

**Exit check:** JR reads `--report` and agrees the top of the list and the pick chart look right.

### Phase 4: Proposal engine (dry run first)
**Files:**
- `engines/gm_trade_engine.py`: `trades_open`, `expire_stale`, `injury_triggers`, `weekly_budget`, `candidate_deals`, `check_legality`, `accepts`, `choose`, `steps_text`, `run_week(cur, season, week, dry_run, replace)`. Template rationale only.
- CLI: `python3 -m engines.gm_trade_engine --season 2036 --week 4 [--dry-run] [--replace] [--simulate-season]`.
  - `--simulate-season` replays the budget over weeks 1..deadline with a JR approval rate p, and prints the expected executed count.
- Constants: `TRADE_DEADLINE_WEEK` (C1), `TRADE_DEAD_MONEY` (C4), `ROSTER_MAX/MIN` (C3).

**Tests** (`tests/test_gm_trade_engine.py`):
- Each legality check fails on its own fixture: cap, NTC, roster, deadline, pick ownership, double booking, fairness.
- Acceptance needs both sides.
- An injury trigger fires only for a CONTEND starter.
- The budget's mean over 2,000 seeded seasons with p = 0.6 is 10 ± 1.5 in season.
- A re-run with the same seed doesn't duplicate (`deal_hash`).
- Proposals expire after their week and at the deadline.

**Exit check:** `--dry-run --week 4` on the live DB (a read-only run) prints 0–4 legal proposals, with values, cap effects and steps that JR finds plausible.

### Phase 5: Front Office, execution, sightings
**Files:**
- `pipeline/trade_moves.py`: the pure `plan_trade_ops` and `execute_trade`.
- `engines/front_office.py` (§2.9) plus `match_sightings` and `consistency_flags`.
- `pipeline/load_transactions.py`: store the skipped trade rows in `gm_trade_sightings` when the table exists, then call `match_sightings`. The report key `skipped_trades` stays.
- `server/debug_api.py`:
  - `GET /api/debug/front-office?season` returns the window and deadline, the budget and pace, 32 directions with their needs, proposals with assets, cap effects, legality, steps and status, flags, and unmatched sightings.
  - Token-gated `POST /front-office/proposals/{id}/approve | reject {reason} | confirm | could-not-make {reason}` and `POST /front-office/record` (a JR-entered trade).
  - `confirm` and `record` re-check legality and warn, but they never block, because Madden is the truth once JR has made the trade.
  - `JOB_KINDS["gm_proposals"]`, with `job_command` special-casing it to `-m engines.gm_trade_engine`.
- `Mu_Network/src/DebugPage.tsx`: a `FrontOfficeCard` in `Control`, next to `TransactionsCard`.
  - Header: the budget pace (executed/target, open count, deadline week).
  - Proposal rows: teams, assets, cap before → after, rationale, the steps (collapsible), and the action buttons.
  - Each confirmed trade's status chips: Awaiting confirm / Executed / Seen in Madden log / Not yet seen.
  - A flags list.

**Tests:**
- `tests/test_trade_moves.py`: the contract copy keeps every year and the projected flag; both cap rows move by the same hit; dead money follows the mode; pick ownership moves; one `transactions` row per leg; the row keys can't collide with scan keys.
- `tests/test_front_office.py`: the matcher handles matched, partial and unmatched sightings, and the flags never create write ops.
- `tests/test_debug_api.py`: the front-office endpoints with `connect` mocked, the token required, and the job command for `gm_proposals`.
- `tests/test_load_transactions.py`: trade rows become sighting inserts only when the table exists.

**Exit check:** one real trade end to end with JR. Approve, make it at the console, click Made it, check that the players, contracts and cap rows moved, then run the next transactions scan and check that the sighting is MATCHED (and C8's wording is recorded).

### Phase 6: Weekly integration and rationale writer
**Files:**
- `pipeline/weekly_capture.py`: a `step_gm(season)` after Step 7 and before articles in the weekly and playoff runs. It's non-fatal and logs a warning on failure. It runs `run_week`, then `notify("N new trade proposals for JR", …)` when N > 0.
- `engines/gm_rationale.py`: `fact_sheet`, `prompt`, `validate_rationale`, `write_rationales(…, meter)`. Personas' LLM bios are backfilled for any GM that's still on the template.

**Tests:**
- `tests/test_gm_rationale.py`: the validator rejects an invented number, player or team and accepts invented colour; a mocked client error falls back to the template.
- `tests/test_weekly_capture*`: the step order with `run_cmd` and `step_gm` mocked, and a failure that doesn't stop the run.

**Exit check:** one supervised Scan week. The log shows the GM step with the budget line, the proposals appear on `#debug`, and `llm_usage` has `gm_rationale` rows.

### Phase 7: Offseason, draft day, season rollover (before the 2036 offseason)
**Files:**
- `engines/gm_trade_engine.py`: the `OFFSEASON` window (post-Super Bowl through preseason, using `IN_SEASON_SHARE`'s remainder) and `DRAFT_DAY` pick-for-pick proposals (step 4 calls `run_draft_day(pick)`).
- Rollover hook in the Season scan's preseason engines (`step_preseason_engines`):
  - new `gm_team_seasons` rows, with the preseason direction from the prior season and `team_ovr`;
  - seed `draft_picks` for the new S+2;
  - mark last draft's picks `used`;
  - carry `fo_quality` forward until step 4 rewrites it.

**Tests:** the rollover is idempotent, pick seeding doesn't overwrite traded picks, and the offseason budget is paced.

**Exit check:** a dry run of the rollover on a restored copy (`db/restore_db.sh`).

---

## 4. `PROJECT_SPEC.md` edits (CLAUDE.md rule, made in the same phase)

| Phase | Sections |
|---|---|
| 1 | §1.1 layout (migration, `engines/gm_personas.py`); a new **§3.3f Front Office** (`gm_profiles`, `gm_team_seasons` with the step-4 `fo_quality` columns, `vw_gm_profiles`, `draft_picks`); §3.3c Draft: a pointer to `draft_picks` ownership; §1.2 commands |
| 2 | §1.1 (`engines/gm_direction.py`); a new **§2.16 Team Direction & Needs** (formula, bands, hysteresis, slots) |
| 3 | §1.1 (`engines/trade_value.py`); a new **§2.17 Trade Value** (W, aging, surplus, direction discounts, pick curve) |
| 4 | §1.1, §2.17 (legality, acceptance, budget), §3.3f (`gm_trade_proposals`, `gm_trade_assets`), §1.2 CLI |
| 5 | §1.1 (`pipeline/trade_moves.py`, `engines/front_office.py`); §3.3e (gm `TRADED` rows, the FK, sightings); §3.3f (`gm_trade_sightings`); §4.5 debug endpoints; §5.2 the `#debug` Front Office card |
| 6 | §6.0 weekly run (the GM step after engines); §7 only if step 5 hasn't already noted the rationale writer |
| 7 | §6.0 Season scan (rollover hook); §2.17 offseason and draft-day windows |

---

## 5. Out of scope
- Scouting boards, `q_team` computation and live drafting (step 4). Step 3 only reserves `fo_quality` and owns `draft_picks` and the draft-day trade flow.
- Writer tools, trade rumors, deadline coverage, Agent Wire posts, and lifting the newsroom's trade ban (step 5). Step 5 consumes §2.9.
- Free-agent signings, releases and re-signings by GMs (D1, the CPU keeps them).
- Three-team trades, contract restructures as part of a trade, and automatic GM firing.
- Pressing anything in Madden (D7). The capture never touches the trade screen.

## 6. Hand-offs
- **Step 4 consumes:**
  - `vw_gm_profiles` / `get_gm`: `position_bias`, `youth_bias`, `risk_appetite` for `bias`, plus `direction` and `needs` for `fit`.
  - `gm_team_seasons.fo_quality` (+ `_inputs`, `_at`) to write `q_team`. Seed it from `draft_prospects.actual_pick/drafted_team_id`; there's no `draft_selections`.
  - `draft_picks` ownership for the order.
  - `trade_value.pick_value` and `run_draft_day` for draft-day trades.
- **Step 5 consumes:** `executed_trades` (and the `TRADED` rows in `transactions`), `trade_rumors` (only `rumor_ok` non-executed proposals, never presented as done), `deadline_summary`, `get_gm` (names, voice, tenure), and the stored `rationale_a/b` and `headline`. Step 5 must not leak APPROVED-but-unconfirmed trades as completed.

## 7. Open questions, answered (JR, 2026-10-20)
1. **User-controlled teams:** each user-controlled team's GM agent acts as an advisor and suggests trades to JR. JR can turn the suggestions off for each team. The setting is stored per team.
2. **2036 in-season target:** scaled to the weeks left before the Week 8 deadline.
3. **Rumors:** rejected and expired proposals can be rumor material (`rumor_ok`). They're never reported as done.
4. **GM turnover:** an automatic hot seat, decided at the offseason rollover.
5. **Rationale and GM bios:** Gemini. Bios are written once and stored; rationale is written for each proposal.
6. **`draft_selections`:** fixed in the master plan (ee69a59). Draft history is in `draft_prospects`.
