# Project Mu: Cap-Relative Contracts and Personality Contract Fixes Plan

**Status:** built and on main (2026-11-02); approved by JR the same day. Wave 0: the finished personality work was committed unchanged as 786ab35 (Q1).
As built:
- **W level (U1, read-only on `madden_2036` 2036):** league W(75) $4.24M → $4.17M, W(80) $6.89M → ~$6.92M, W(85) $11.19M → $11.47M, W(95) $29.54M → $31.57M; `b` 0.0970 → 0.1012; W(95)/W(75) 7.0 → 7.6 (R3).
- **`FALLBACK_W75` (U1):** the no-data stub market keeps W(75) at $3.9M (its old dollar level) whatever the premium; at 1.20 the old stub dropped below the RFA second-round tender and broke `test_resign_tenders_young_players_and_tags_at_most_one`.
- **Drift dedupe (U1):** `--drift` counts one deal per player and season (`DISTINCT ON`, latest row), because a re-scan supersedes old rows and inserts the same deal again.
- **Writer loader (U3):** `seed_writers_to_db` now runs the full `validate_slider_budget` check (via `_sliders_ok`), so it also rejects non-integer or out-of-range sliders, not just totals.
- **Extra edits (U3):** PERSONALITY_PLAN §5.2's stale exact-30 sentence rewritten; the `#debug` API budget-message test updated.
- **Career earnings (U2):** deals are grouped on `(player_id, team_id, signed, length)` only (R4), so restructures and a TRANSACTION row followed by the next season's SCAN of the same deal stay one deal; a TRADED row doesn't cut a deal. On `madden_2036` 2036: 1,777 players, 86 secure, 521 not secure, 1,170 unknown, 522 complete. A deal first seen mid-term is back-dated at its full APY, so big recent extensions can overstate early seasons. The spec section is §2.17e, as planned.
- **Base (all units):** worktrees start from `origin/main` (`140a7a9`, not on local main); each unit reset its fresh branch to `1d95a9d` first. The first attempts stopped at the base check without changes.
**Builds on:** PROJECT_SPEC §2.17 (trade value, market worth), §2.17a (contracts engine), §2.17b (free agency), §7.11 (personas); [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §4–§6 and §10; [`PERSONALITY_GENERATED_EFFECTS_DESIGN.md`](../reference/PERSONALITY_GENERATED_EFFECTS_DESIGN.md) §2 and §5. Comes out of JR's review of a Gemini conversation (`Gemini_NFL-Finances-and-Contender-Dynamics_20261031-1900.pdf`, repo root) on 2026-10-31.

**Goal:** Contract prices should keep up with the salary cap over a long simulated run. Today the market worth `W` is fitted on cap hits of deals signed one to five years earlier, so asking prices trail the cap and nothing anchors them. This plan prices every veteran deal as a share of the cap in the season it was signed and converts that share to this season's cap. It adds a drift report so JR can watch the market over seasons. It adds a career-earnings ledger, measured in cap shares, as the input for the "financially secure" gate on strong ring chasing. It relaxes the persona slider total from exactly 30 to a 24–36 band. It updates the personality plan and design with what JR agreed after the Gemini review. Personality engine effects stay inactive.

**Delivers:**
- `engines/cap_history.py` (new): `league_caps(cur)`, `cap_in(season, caps)`.
- `engines/trade_value.py`: the market fit on APY ÷ cap in the signed season, scaled to this season's cap (`cap_adjust`); `market["cap"]` and `market["basis"]`; a `--drift` report.
- `engines/career_earnings.py` (new): `deal_spans`, `earned_shares`, `is_secure`, `FINANCIAL_SECURITY_SHARE`, and a `--report` CLI.
- `newsroom/personas.py`: `SLIDER_BUDGET_MIN = 24`, `SLIDER_BUDGET_MAX = 36`. Validation accepts any total in that band; seeding still deals exactly 30.
- `db/migrations/2027_01_17_persona_slider_band.sql` (new) and `db/init_db.sql`: `chk_persona_sliders_budget` becomes a 24–36 band.
- `PERSONALITY_PLAN.md` and `PERSONALITY_GENERATED_EFFECTS_DESIGN.md` edits (§2.6), and a guard test that no trial coefficient inverts a preference.

**JR's answers (2026-11-02):**
- **D1 (Q1):** JR considers the personality work finished. Rechecked 2026-11-02: it is built and its tests pass (35 passed: `test_personality_core.py`, `test_personality_core_store.py`, `test_personality_contract_demo.py`, `test_personas.py`), but none of it is committed (S11). How it gets committed is the one open question (§8).
- **D2 (Q2):** `MARKET_PREMIUM` = 1.20.
- **D3 (Q3):** `FINANCIAL_SECURITY_SHARE` = 0.15.
- **D4 (Q4):** `seed_sliders` keeps dealing exactly 30; the band covers validation, hand edits and event moves.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `engines/trade_value.py:327` `read_fit_rows` fits the market on `y.cap_hit` for the scan season of `ACTIVE`, `SCAN` contracts, players with 4+ years. `fit_market` (`:63`) fits `ln(dollars)`; `worth` (`:89`) is `MARKET_PREMIUM (1.3) × exp(a + b(ovr − 75))`. | The fix belongs in `read_fit_rows`: hand `fit_market` rows already converted to this season's cap. `fit_market` and `worth` keep their signatures, so every caller is untouched. |
| S2 | `fits_from_db` is called by `gm_trade_engine` (`:1569`, `:1689`, `:1787`, `:2017`), `gm_negotiation:337`, `front_office:94/220/248`, `transactions_engine:801`, `server/franchise_api:411`. `progression.py:1270/1527/1846` uses only the aging part of `read_fit_rows`. | A new `W` level changes trade values, re-sign asking prices and free-agency value scores league-wide. `read_fit_rows` must keep returning `(market, aging, draft)`. |
| S3 | `madden_2036` (read-only check, 2026-10-31): `team_cap_seasons` has one season, 2036, cap $367.0M for all 32 teams. There are 1,618 `ACTIVE` `SCAN` contracts and 121 `TRANSACTION` ones; the V2 migration (`signed_season` and the other columns) is not applied there. Vets with 4+ years: deal age (`length − years_remaining`) 0: 303, 1: 309, 2: 192, 3: 52, 4: 29, 5: 4. | Caps before 2036 must be estimated. The signed season has to come from `season − (length − years_remaining)`, not the `signed_season` column. The average vet deal is ~1.2 seasons old, about 8% behind the cap at 6.5% growth. |
| S4 | Same check: this year's cap hit ÷ APY averages 0.95 for deals 0–1 years old, 1.05 at 2, 1.10 at 3, 1.34 at 4 and 1.67 at 5. | Cap hits are backloaded, which partly hides the lag and mixes deal structure into the price. APY (`total_value / length`) is the cleaner measure. |
| S5 | Spike (2026-10-31, read-only on `madden_2036`): refitting on APY × 1.065^age gives league `W(75)` $4.24M → $4.52M (+7%), `W(85)` $11.19M → $12.43M (+11%), `W(95)` $29.54M → $34.21M (+16%); slope `b` 0.0970 → 0.1012. Every position group rises by a similar amount. | The change raises prices, most of all for stars. `MARKET_PREMIUM` was set against cap-hit data, so whether to keep the level or the premium is JR's call (Q2). |
| S6 | `engines/contracts_engine.py:84` `CAP_GROWTH` mean 6.5%, `CAP_ROUND` $100k (`:85`); `minimum_salary` (`:99`), the rookie scale and the tender table already scale with the cap. `transactions_engine.league_cap` (`:704`) takes the median team cap. `franchise_api.cap_years` (`:640`) projects future caps with `next_cap`. | The market is the one price that doesn't scale with the cap. `cap_in` estimates missing seasons from the growth mean with no random draw, so a refit always gives the same answer. |
| S7 | TRANSACTION contracts have `length = years_remaining = N` (spec §3.3e), so they read as signed this season. The fit excludes them (`source = 'SCAN'`). | Keep the fit on SCAN rows, so the engine's own new deals don't feed straight back into next run's price. The drift report reads all new deals, TRANSACTION rows included, because those are what can drift. |
| S8 | `tools/personality_contract_demo.py:65`: `preference = coefficient × (financial − contention) × direction`. Utility weights (`free_agency_engine` `W_DEFAULT`): 0.45 financial, 0.20 team. | **Correction to the 2026-10-31 review:** the term is a weight shift, not a perverse bonus. Money First moves the team weight from 0.20 to 0.20 − k, and Ring Chaser moves the financial weight from 0.45 to 0.45 − k. With k = 0.10, a Money First player still prefers the better team when money is equal, and a Ring Chaser still prefers more money when contention is equal. Inversion only happens past k = 0.20 or 0.45; the sweep's top value, 0.20, makes Money First indifferent to contention. So the formula stays as it is, and this plan adds a guard test and a written bound. |
| S9 | `newsroom/personas.py:23` `SLIDER_BUDGET = 30`. `validate_slider_budget` (`:159`) requires exactly 30; `seed_writers_to_db` (`:428`) skips writers whose total isn't 30. `seed_sliders` (`:206`) deals 30 by paired moves. `persona_sliders` is empty in `madden_2036`, so GM personas come from `seed_sliders` on every call (`personas.py:476`, `gm_personas.py:240`). | Changing `seed_sliders` would change all 32 GMs' sliders today. The band goes into validation and the writer loader only; seeding stays at 30 (D4). `tests/test_personas_db.py:84` checks the "does not match budget 30" message and needs updating. |
| S10 | **Corrected 2026-11-02** (the first draft missed it; the contract-policy, draft-integration and development plans flagged it): besides `0..10` per slider (`2027_01_09_personas.sql:12`), `chk_persona_sliders_budget` requires a total of exactly 30 (`2027_01_09_personas.sql:21`, and the same in `db/init_db.sql:1674`). `persona_relationships` is one-way: `persona_type/persona_id → target_type/target_id`, unique on the ordered pair (`:52–:64`). | The band needs a migration (`2027_01_17_persona_slider_band.sql`, the next free name; no other plan claims it) and the same change in `db/init_db.sql`. The plan's §4.7 wording ("a row per pair") is wrong about the schema; fix the wording. |
| S11 | `git status` (rechecked 2026-11-02): `PERSONALITY_PLAN.md` has uncommitted edits. Untracked: `PERSONALITY_GENERATED_EFFECTS_DESIGN.md`, `PERSONALITY_PANTHERS_SAMPLE.md`, `tools/personality_contract_demo.py`, `tests/test_personality_contract_demo.py`, and the personality core: `personality/` (`core.py`, `store.py`, `README.md`, `__init__.py`, `__main__.py`), `db/migrations/2027_01_16_personality_core.sql`, `tests/test_personality_core.py`, `tests/test_personality_core_store.py`. `personality/core.py:136` already reads `SLIDER_BUDGET_MIN`/`MAX` with `getattr` fallbacks. | Worktree agents only see committed files, so these must be committed before wave 1 (§8 Q1). The core is ready for the band and needs no change. |
| S13 | Six new plans in `docs/not_started/` (2026-10-31: `PERSONALITY_CONTRACT_POLICY_PLAN.md`, `_DEVELOPMENT_COACHING_`, `_DRAFT_INTEGRATION_`, `_RELATIONSHIPS_`, `_RETIREMENT_`, `_TRADE_REQUESTS_`) treat this plan as the owner of pricing, the earnings ledger, the security threshold and the slider band. The contract-policy plan consumes `career_earnings.is_secure` as a three-state result, with unknown meaning inactive (its §2, line 86), and lists this plan as a wave-2 dependency (line 221). | Keep the §2.4 interface exactly as written; those plans build against it. This plan changes none of their files. |
| S12 | No code reads career earnings. `player_contracts` holds one row per contract per scan season, with `SUPERSEDED`, `RELEASED` and `TRADED` history. `player_seasons.years_of_experience` gives experience per season. | The ledger can be derived from existing tables. No new table is needed while personality effects are inactive. Earnings before a player's earliest known deal are unknown, so the ledger is a lower bound. |

## 1. Scope

**In:**
- Market worth fitted on APY ÷ cap in the signed season, scaled to this season's cap (§2.1, §2.2).
- `MARKET_PREMIUM` lowered to 1.20 (D2).
- `trade_value --report` shows the basis and the cap; `trade_value --drift` shows new-deal APY ÷ cap by group and season (§2.3).
- The career-earnings ledger and the `is_secure` gate, in cap shares (§2.4). Read-only, with a CLI report.
- The slider total band 24–36 in validation and the writer loader (§2.5).
- Personality doc edits and the coefficient guard test (§2.6).

**Out (non-goals):**
- Turning on any personality engine effect (free agency, progression, trades). That stays in the design's increments A–E.
- A real-NFL cap-share anchor. Add one only if `--drift` shows the market drifting.
- Changing `seed_sliders`, or a v2 seeder for generated players. A v2 seeder belongs with increment A's versioned seeding.
- A `career_earnings` table. Add one when increment B needs stored decision breakdowns.
- Moving sliders to 0–100 (rejected).
- The deferred personality items in §2.6, which are recorded in the plan for later work.
- Projecting future cap growth inside `player_value`. Future years keep using today's `W` in dollars, as they do now.

## 2. Design

### 2.1 Cap history (`engines/cap_history.py`, new)

```python
def league_caps(cur) -> dict[int, int]:
    """{season: median team `cap` (not adjusted_cap)} from team_cap_seasons, all seasons. Read-only."""

def cap_in(season: int, caps: dict[int, int]) -> int:
    """The league cap in `season`: caps[season] if known; else the nearest known season's cap moved by
    contracts_engine.CBA['CAP_GROWTH']['mean'] a year (divide going back, multiply going forward), rounded to
    CBA['CAP_ROUND']; with no known caps, CBA['REFERENCE_CAP']. Deterministic: no seeded draw."""
```

The nearest known season wins; on a tie, the earlier season. Known seasons are never estimated.

### 2.2 Market fit (`engines/trade_value.py`)

```python
def cap_adjust(rows, caps, season):
    """rows: [(group, ovr, total_value, length, years_remaining)] -> [(group, ovr, dollars)]:
    APY = total_value / length; signed = season - (length - years_remaining);
    dollars = APY * cap_in(season, caps) / cap_in(signed, caps). Drops rows with no length, no value, or
    length < years_remaining."""
```

- `read_fit_rows(cur, season)`: the market query keeps its filters (`c.season = %s`, `ACTIVE`, `SCAN`, 4+ years, `NOT y.projected`). It selects `ps.position, ps.overall_rating, c.total_value, c.length, c.years_remaining` and passes them through `cap_adjust(rows, league_caps(cur), season)`. It still returns `(market, aging, draft)`, with market rows as `(group, ovr, dollars)`.
- `fit_market` and `worth` are unchanged. `fits_from_db` sets `fits["market"]["cap"] = cap_in(season, caps)` and `fits["market"]["basis"] = "apy_cap_share"`. Hand-built markets (tests, the demo, `build_fits([], [], [])`) have neither key, and nothing requires them.
- `MARKET_PREMIUM = 1.20` (D2; was 1.3). U1 reports `W(75/85/95)` before and after on `madden_2036` 2036 (read-only). From the spike, league `W(80)` should land within a few percent of today's.
- `report()` gets a first line: `Market basis: APY / cap at signing, scaled to the 2036 cap $367.0M (caps known: 2036; earlier seasons estimated at +6.5%/yr)`.

### 2.3 Drift report (`python3 -m engines.trade_value --drift [--season S]`)

```python
def drift_rows(cur, through_season) -> list[dict]:
    """New deals by season: ACTIVE or SUPERSEDED contracts with length = years_remaining (signed in their scan
    season), players with 4+ years, either source. One dict per (season, group):
    {season, group, n, median_share, p75_share} where share = (total_value / length) / cap_in(season, caps)."""
```

The CLI prints one table: rows are groups, columns are seasons, cells are `median% (n)`. A league row comes first. It is read-only. Today it has one column (2036); it becomes useful as the run adds seasons.

### 2.4 Career earnings (`engines/career_earnings.py`, new)

All money is in cap shares: each season's pay ÷ that season's cap (`cap_history.cap_in`).

```python
FINANCIAL_SECURITY_SHARE = 0.15   # D3, a trial value: about $38M at the 2024 cap, $55M at the 2036 cap

def deal_spans(contract_rows) -> list[dict]:
    """contract_rows: [{player_id, team_id, season, length, years_remaining, total_value, status, yoe}]
    (one per player_contracts row, yoe = years_of_experience in that scan season). Groups rows into deals keyed
    (player_id, team_id, signed, length, total_value), signed = season - (length - years_remaining). Each deal ->
    {player_id, signed, first_season, last_season, apy, rookie_start}: first_season = signed, last_season = the
    deal's end (signed + length - 1), cut to the season before the player's next deal starts, and to the scan
    season of a RELEASED row. rookie_start = the yoe at signing (yoe - (season - signed)) is 0."""

def earned_shares(spans, caps, through_season) -> dict[int, dict]:
    """{player_id: {"share": sum over covered seasons <= through_season of apy / cap_in(s), "first_known": min
    first_season, "complete": True when the earliest deal is rookie_start}}. Each season counts once per
    player; if two deals cover a season, the later-signed deal wins."""

def is_secure(entry, threshold=FINANCIAL_SECURITY_SHARE):
    """True if share >= threshold; False if share < threshold and the history is complete; otherwise None
    (unknown). None keeps the ring-chaser gate inactive (design §2: unknown means inactive)."""

def read_contract_rows(cur, through_season) -> list[dict]:   # read-only
```

CLI: `python3 -m engines.career_earnings --report [--season S] [--top N]`. It prints counts of secure, not secure and unknown, the top N by share, and how many players have complete history. Nothing calls it from an engine yet.

### 2.5 Slider band (`newsroom/personas.py`)

- Keep `SLIDER_BUDGET = 30` as the seeding total. Add `SLIDER_BUDGET_MIN = 24` and `SLIDER_BUDGET_MAX = 36`.
- `validate_slider_budget`: the per-slider rules are unchanged; the total must be within `[SLIDER_BUDGET_MIN, SLIDER_BUDGET_MAX]`. Error message: `Slider total {total} outside budget band 24-36`.
- `seed_writers_to_db` (`:428`): accept the band, using the same check as validation.
- `seed_sliders` is unchanged, so every GM and every seeded test output stays identical.
- `db/migrations/2027_01_17_persona_slider_band.sql` (new): `ALTER TABLE persona_sliders DROP CONSTRAINT IF EXISTS chk_persona_sliders_budget; ALTER TABLE persona_sliders ADD CONSTRAINT chk_persona_sliders_budget CHECK (temperament + sociability + confidence + openness + warmth + drive BETWEEN 24 AND 36);`. It is guarded with `IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'persona_sliders')` in a `DO` block, so it is a no-op where the persona tables don't exist. All stored rows sum to 30, so it can't fail on existing data.
- `db/init_db.sql:1674`: the same constraint, written as the band.
- `render_persona` is unchanged.

### 2.6 Personality docs and guard test

`PERSONALITY_PLAN.md` (built on JR's committed edits):
- Header, §8, §9: drop "agents" as a role. A player's own AI loop negotiates; the §8 "Player agent" tools become "Player (negotiating)". Phase 6 becomes "Owners, scouts, media voters".
- §4.1: total 24–36 (seeded at 30), 0–10 kept. One line on why: an exact total makes some characters impossible and links unrelated sliders. §10 D1 is updated to match.
- §4.6: a mood order: own role or health, then contract or job security, then team results. The output is a main mood plus an undertone (example: benched starter in a contract year on a 10–0 team → Frustrated, undertone Pressure).
- §4.7: relationships are one-way rows (A → B), as stored; one-sided grudges and unreturned loyalty are allowed.
- §5 step 3 and §7: bio facts fixed; voice notes rewritten after any logged trait or slider change.
- §5 Change over time: slider moves inside the 24–36 band no longer need a paired move.
- §6: Free agency points to the career-earnings gate (cap shares) and the coefficient bound.
- New §12 **Deferred**: a lowball zone in re-sign and extension talks (trust hit, possible leak); a cap-casualty-after-discount event (one-way trust drop toward the GM; trait change only through the yearly review); an elite positional-rank demand; 2–3 views for players who speak; owner categories (Meddler / Hands-Off, Deep Pockets / Budget Conscious, Headline Hunter / Patient Builder) and scout categories (Traits Chaser, Tape Grinder, Character Hawk, Floor Seeker / Ceiling Gambler) for phase 6.
- New §13 **Considered and rejected (2026-10-31)**: "Bag Drop" (Money First plus low Drive gains Coasting), Warmth as switching cost, a Confidence effect on guarantees, a Scheme Nerd contract multiplier, 0–100 sliders, separate hired player agents. One line of reason each.

`PERSONALITY_GENERATED_EFFECTS_DESIGN.md`:
- §2: under the trait table, state that MONEY_FIRST and RING_CHASER are weight shifts (S8) with the bound k < 0.20 (Money First) and k < 0.45 (Ring Chaser), and that the sweep's top value of 0.20 is the indifference point for Money First.
- §Reviewed refinement: "financial security already achieved" = `career_earnings.is_secure` is True; unknown history is inactive.

`tests/test_personality_contract_demo.py`, new test `test_preferences_never_invert_base_weights`. For k in (0.05, 0.10, 0.15) and each cap in the sweep: with equal money, Money First ranks the higher-contention offer at or above the lower one (strictly above when the cap isn't binding on both). With equal contention, an eligible Ring Chaser ranks the higher-paying offer at or above. It builds its fixtures with the demo's own `fixtures()` scoring path. The demo code is unchanged.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Caps before 2036 are estimated at 6.5% a year; Madden's real cap path in this save is unknown. | Once 2037 is scanned, compare its real cap with `cap_in(2037)` (the drift report header shows known vs estimated seasons). | Each later scan adds a known season, and older deals roll out of the fit within a few seasons. |
| R2 | A higher `W` (S5) shifts trade surplus, GM proposals and re-sign prices; trade-engine thresholds were tuned against the old `W`. | U1 reports `W(75/85/95)` before and after. JR reruns `python3 -m engines.trade_value --report` on `madden_2036`. | D2 (1.20) keeps the 2036 level close to today's and fixes mainly the lag going forward. |
| R3 | The slope `b` rises (0.097 → 0.101) even after re-levelling, so stars get pricier relative to role players. | Same report: the `W(95)/W(75)` ratio, 7.0 before and 7.6 after. | Accept it (it is the cap-correct shape), or JR asks for a follow-up. |
| R4 | The deal grouping in `deal_spans` may split one deal into two if `total_value` changes between scans, for example after a restructure. | Synthetic test with a restructured deal; the CLI count of players with more than one deal starting in a season. | Group on `(player_id, team_id, signed, length)` only. |
| R5 | Few players have complete history for years, so `is_secure` mostly returns None. | `--report` counts. | That is the intended safe default; the gate stays inactive until history accrues. |

**Spike first:** none left. The S5 spike already ran (2026-10-31, read-only). U1's before and after report confirms the 1.20 level.

## 4. Tests (synthetic, no live DB)

- `tests/test_cap_history.py` (new): a known season is returned exactly; going back one season divides by 1.065 and rounds to $100k; going forward multiplies; the nearest known season wins, the earlier one on a tie; empty caps give `REFERENCE_CAP`; `league_caps` takes the median on a fake cursor.
- `tests/test_trade_value.py`: `cap_adjust` leaves a deal signed this season at its APY; a 2-year-old deal is scaled up by the cap ratio; a backloaded deal prices on APY, not cap hit; bad rows are dropped. `fit_market` on adjusted rows recovers a known share curve. `drift_rows` medians on a fake cursor. The existing W, aging and pick tests still pass unchanged.
- `tests/test_career_earnings.py` (new): one deal across three scans counts once; a release cuts the span; a new deal ends the old one's span; shares are summed per season's cap; rookie-start history is complete; a deal first seen mid-career is incomplete; `is_secure` returns True, False or None; `through_season` excludes later seasons.
- `tests/test_personas.py`: seeded sliders still sum to exactly 30 (the existing check) and are unchanged for a fixed ID list (snapshot of five IDs).
- `tests/test_personas_db.py`: totals 24 and 36 pass; 23 and 37 raise "outside budget band 24-36"; the old exact-30 message test is replaced. A text check confirms that `2027_01_17_persona_slider_band.sql` and `db/init_db.sql` both state `BETWEEN 24 AND 36`, and that neither still says `= 30` for the constraint.
- Regression: `tests/test_personality_core.py tests/test_personality_core_store.py` still pass (the core reads the new bounds).
- `tests/test_personality_contract_demo.py`: `test_preferences_never_invert_base_weights` (§2.6); the existing six tests are unchanged.

Run with `pytest -m "not integration" tests/test_cap_history.py tests/test_trade_value.py tests/test_career_earnings.py tests/test_personas.py tests/test_personas_db.py tests/test_personality_contract_demo.py`. Regression: `tests/test_free_agency_engine.py tests/test_transactions_engine.py tests/test_gm_trade_engine.py` must still pass.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

**Before wave 1 (orchestrator, per §8 Q1):** commit the finished personality work as it stands, in one commit with no content changes: `personality/` (without `__pycache__/`), `db/migrations/2027_01_16_personality_core.sql`, `tests/test_personality_core.py`, `tests/test_personality_core_store.py`, `tools/personality_contract_demo.py`, `tests/test_personality_contract_demo.py`, `docs/in_progress/PERSONALITY_PLAN.md`, `docs/reference/PERSONALITY_GENERATED_EFFECTS_DESIGN.md` and `docs/reference/PERSONALITY_PANTHERS_SAMPLE.md`. The six new `docs/not_started/PERSONALITY_*_PLAN.md` files, and every other untracked or modified file, are left alone.

### U1: Cap-share market and drift report

- **Owns:** `engines/cap_history.py` (new), `engines/trade_value.py`, `tests/test_cap_history.py` (new), `tests/test_trade_value.py`
- **Reads:** `engines/contracts_engine.py` (CBA, `next_cap`), `engines/transactions_engine.py:704` (`league_cap`), `engines/progression.py:1270` (aging use of `read_fit_rows`), this plan §0 S1–S7 and §2.1–§2.3
- **Depends on:** none
- **Done when:** `fits_from_db` prices on APY ÷ cap at signing; `--report` shows the basis line; `--drift` prints the table; `MARKET_PREMIUM` is 1.20, with before and after `W` in the unit's report; the spec changes for §2.17 and the layout are reported back.
- **Verify:** `pytest -m "not integration" tests/test_cap_history.py tests/test_trade_value.py tests/test_free_agency_engine.py tests/test_transactions_engine.py tests/test_gm_trade_engine.py` all pass. `DATABASE_URL="host=localhost dbname=madden_2036 user=postgres password=postgres" python3 -m engines.trade_value --report --season 2036` (read-only) prints the basis line.

### U2: Career-earnings ledger

- **Owns:** `engines/career_earnings.py` (new), `tests/test_career_earnings.py` (new)
- **Reads:** `engines/cap_history.py` (from U1), PROJECT_SPEC §3.3d (contract status history), this plan §2.4
- **Depends on:** U1 (imports `cap_history`)
- **Done when:** `earned_shares` and `is_secure` behave as in §2.4; `--report` runs read-only and prints secure, not secure and unknown counts.
- **Verify:** `pytest -m "not integration" tests/test_career_earnings.py` passes. `DATABASE_URL="host=localhost dbname=madden_2036 user=postgres password=postgres" python3 -m engines.career_earnings --report --season 2036` prints counts.

### U3: Slider band, personality docs, coefficient guard

- **Owns:** `newsroom/personas.py`, `db/migrations/2027_01_17_persona_slider_band.sql` (new), `db/init_db.sql`, `tests/test_personas.py`, `tests/test_personas_db.py`, `tests/test_personality_contract_demo.py`, `docs/in_progress/PERSONALITY_PLAN.md`, `docs/reference/PERSONALITY_GENERATED_EFFECTS_DESIGN.md`
- **Reads:** `tools/personality_contract_demo.py`, `engines/free_agency_engine.py` (`W_DEFAULT`), `server/debug_api.py:1197` (calls validation; no change), this plan §0 S8–S11 and §2.5–§2.6
- **Depends on:** none (the pre-wave commit must exist)
- **Done when:** validation, the migration and `init_db.sql` all accept totals 24–36; seeding is unchanged; both docs carry the §2.6 edits; the guard test passes.
- **Verify:** `pytest -m "not integration" tests/test_personas.py tests/test_personas_db.py tests/test_personality_contract_demo.py tests/test_gm_personas.py tests/test_personality_core.py tests/test_personality_core_store.py` all pass. The orchestrator applies the migration to `madden_test` only; applying it to `madden_2036` is JR's call. `grep -n "agents, scouts\|player agents" docs/in_progress/PERSONALITY_PLAN.md` finds nothing.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 0 | orchestrator commit of personality WIP | — | U3's files must exist in the worktree |
| 1 | U1, U3 | wave 0 | no shared files, no imports between them |
| 2 | U2 | U1 | imports `engines/cap_history.py` |

**File ownership check:**

| File | Unit |
|---|---|
| `db/init_db.sql` | U3 |
| `db/migrations/2027_01_17_persona_slider_band.sql` | U3 |
| `docs/reference/PERSONALITY_GENERATED_EFFECTS_DESIGN.md` | U3 |
| `docs/in_progress/PERSONALITY_PLAN.md` | U3 |
| `engines/cap_history.py` | U1 |
| `engines/career_earnings.py` | U2 |
| `engines/trade_value.py` | U1 |
| `newsroom/personas.py` | U3 |
| `tests/test_cap_history.py` | U1 |
| `tests/test_career_earnings.py` | U2 |
| `tests/test_personality_contract_demo.py` | U3 |
| `tests/test_personas.py` | U3 |
| `tests/test_personas_db.py` | U3 |
| `tests/test_trade_value.py` | U1 |

No path appears twice.

## 7. Spec changes when built

- §2.17 Trade Value: the market-worth bullet becomes the APY ÷ cap-at-signing fit scaled to this season's cap, with `market["cap"]`/`["basis"]`, the `MARKET_PREMIUM` value and `--drift`; the test list grows.
- §2.17a Contracts Engine: one line saying that `cap_history.cap_in` estimates past caps from the growth mean (deterministic) and is separate from the seeded `next_cap`.
- New §2.17e Career Earnings (`engines/career_earnings.py`): spans, shares, `is_secure`, `FINANCIAL_SECURITY_SHARE`, CLI, "inactive: no engine reads it yet".
- §7.11 Personas: "enforces budget 30" becomes "seeds a total of 30; validation accepts 24–36".
- §3.3p Persona tables: `chk_persona_sliders_budget` is a 24–36 band (`2027_01_17_persona_slider_band.sql`).
- Repository Layout: add `engines/cap_history.py`, `engines/career_earnings.py`, `tests/test_cap_history.py`, `tests/test_career_earnings.py`, `db/migrations/2027_01_17_persona_slider_band.sql`; update the `trade_value.py` line (`--report`, `--drift`); add `docs/done/CAP_RELATIVE_CONTRACTS_PLAN.md`.
- §1.2 Running the System: the two read-only report commands.

## 8. Decisions for JR

Q2–Q4 are answered (D2–D4 above). One question is left.

| # | Question | Recommendation |
|---|---|---|
| Q1 | The personality work is finished and its tests pass, but none of it is committed (S11). May the orchestrator commit it as-is before wave 1, with the file list in §5? | Yes. Worktree agents can't see uncommitted files, and `CLAUDE.md` says not to commit changes that aren't ours without asking. The six new `not_started` personality plans stay uncommitted until they're approved. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U3 | 1 | worktree-agent-a1ca098db05b03e62 | .claude/worktrees/agent-a1ca098db05b03e62 | merged (first try blocked at the base check: worktrees start from origin/main; retried with a reset to 1d95a9d) | 5b3d9da |
| U1 | 1 | worktree-agent-a2913a514934d3831 | .claude/worktrees/agent-a2913a514934d3831 | merged (first try blocked at the base check; retried with the reset) | cde251d |
| U2 | 2 | worktree-agent-a36ea960cc4824ee8 | .claude/worktrees/agent-a36ea960cc4824ee8 | merged (paused once by the session usage limit; resumed in the same worktree) | 99cd58b |
