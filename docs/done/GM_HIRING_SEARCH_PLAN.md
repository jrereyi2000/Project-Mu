# Project Mu: Competitive GM Hiring Search Plan

**Status:** built and on main (2026-11-09); approved by JR 2026-11-09.
**Builds on:** PROJECT_SPEC §2.19, §3.3f, §3.3p; [`STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN.md`](../done/STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN.md) §2.1–§2.2; [`OWNERS_SCOUTS_VOTERS_PLAN.md`](../done/OWNERS_SCOUTS_VOTERS_PLAN.md) §2.3.

**Goal:** Fix the three Chunk 7 B8 bugs (`M-0044` / `M-0064`) in the GM hiring search so that destination `opening_appeal`, candidate `stature`, owner cash `budget`, and `owner_policy` hiring preferences each play a distinct, deterministic role. First-time `PROMOTION` GM candidates get their own salary band below `STANDARD`, `ELITE` and `STANDARD` candidates refuse unattractive openings below their tier appeal floors (recording their refusals in the search report), and the `owner_hiring` feature weights candidate selection and opening appeal for CPU owners.

**Delivers:**
- `STAFF_SALARY_SCALES["GM"]["PROMOTION"]` (`$2.0M–$3.5M`, midpoint `$2,750,000`) and `GM_STANDARD_EXPERIENCE = 3` in `engines/staff_contracts_engine.py`, plus tenure-based GM experience in `engines/staff_contracts_run.py`.
- `gm_hiring_key(owner_view, candidate)` (`|x| <= 0.25`) in `engines/owner_policy.py` translating owner traits (`HEADLINE_HUNTER`, `DEEP_POCKETS`, `BUDGET_CONSCIOUS`, `MEDDLER`) and `openness` into GM tier preferences.
- `GM_MIN_APPEAL`, `GM_APPEAL_WEIGHT`, `gm_candidate_score()`, and an overhauled `gm_search(candidates, *, appeal, budget, owner_view=None)` in `engines/gm_rollover.py` with candidate acceptance gates, `declined` tracking, and `owner_hiring` (`off` / `shadow` / `active`) wiring in `_hire_gm()`.
- Synthetic offline unit tests in `tests/test_staff_contracts.py`, `tests/test_staff_contracts_run.py`, `tests/test_owner_policy.py`, `tests/test_gm_rollover.py`, and `tests/test_carousel_appeal_enhanced.py`.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `engines/gm_rollover.py:106-115` `gm_search(candidates, *, appeal, budget)` ranks affordable candidates by `(round((c.get("stature") or 0) + appeal, 3), -(c.get("salary") or 0), c["key"])`. | Because `appeal` is added equally to every candidate in the pool for the same opening, it is a constant offset and never changes which candidate wins (`test_chunk7_confirm.py:36-41` `test_G1`). |
| S2 | `engines/staff_contracts_engine.py:49-52` `STAFF_SALARY_SCALES["GM"]` defines only `"ELITE": (8_000_000, 12_000_000)` and `"STANDARD": (4_000_000, 7_500_000)`. `salary_tier("GM", ...)` (`:86-87`) returns `"ELITE"` if `championships >= 1`, else `"STANDARD"`, ignoring `experience`. | In `gm_candidate_pool` (`engines/gm_rollover.py:97-103`), the `"PROMOTION"` candidate (`experience=1, championships=0`) gets `"STANDARD"`'s `$5,750,000` salary with lower stature (`0.30 ± 0.08` vs `0.55 ± 0.08`), so `"PROMOTION"` can never win under any budget or appeal (`test_chunk7_confirm.py:44-50` `test_G2`). |
| S3 | `engines/gm_rollover.py:97-103` seeds stature around `0.85` (`ELITE`), `0.55` (`STANDARD`), and `0.30` (`PROMOTION`) with `±0.08` uniform noise. | `ELITE` stature (`0.77..0.93`) always exceeds `STANDARD` (`0.47..0.63`), which always exceeds `PROMOTION` (`0.22..0.38`). Without an acceptance gate or owner preference term, `gm_search` degenerates into `budget >= $10M ? ELITE : STANDARD`. |
| S4 | `engines/staff_contracts_run.py:77` computes a current GM's `experience` via `SELECT count(*) FROM gm_profiles WHERE id = %s`. | Because `id` is the primary key of `gm_profiles`, that query always returns `1`. Once `salary_tier("GM", ...)` tiers `experience < 3` as `"PROMOTION"`, `staff_contracts_run.annual` would misprice veteran GMs (`start_season = 2030`) as `"PROMOTION"` unless experience is computed from seasons on the job (`max(1, season - start_season + 1)`). |
| S5 | `engines/owner_policy.py:87-105` defines `hiring_key(owner_view, candidate)` for coaching candidates (`kind` in `RETREAD`/`POACHED`) and `appeal_term(owner_view)` (`MEDDLER` `-0.05`), and `engines/coaching_carousel.py:869-878, 946-1001` wires them under the `owner_hiring` switchboard feature. `engines/gm_rollover.py:274-292` wires only `owner_patience`, not `owner_hiring`. | GM searches ignore `owner_hiring` completely: a `MEDDLER` owner suffers no destination appeal penalty when recruiting an `ELITE` GM, and a `BUDGET_CONSCIOUS` / `MEDDLER` owner never favors a controllable first-time `PROMOTION` GM. |
| S6 | `tests/test_staff_contracts.py:46, 61` asserts `salary_tier("GM", experience=0, championships=0) == "STANDARD"`, and `tests/test_carousel_appeal_enhanced.py:132-134` asserts `poor = gm_search(pool, appeal=0.3, budget=1_000_000)` picks `"STANDARD"`. | Both tests encoded the pre-fix absence of a `"PROMOTION"` GM band and must be updated to reflect `"PROMOTION"` for `experience < 3` and as the lowest-cost fallback. |

## 1. Scope

**In:**
- Add `"PROMOTION": (2_000_000, 3_500_000)` (`$2,750,000` midpoint) to `STAFF_SALARY_SCALES["GM"]` and `GM_STANDARD_EXPERIENCE = 3` to `engines/staff_contracts_engine.py`.
- Fix `engines/staff_contracts_run.py` GM experience calculation to measure tenure seasons (`max(1, season - start_season + 1)`).
- Add `gm_hiring_key(owner_view, candidate)` in `engines/owner_policy.py` for GM candidate tiers (`ELITE`, `STANDARD`, `PROMOTION`).
- Replace the constant-offset `stature + appeal` rule in `engines/gm_rollover.py` (`gm_search`) with:
  1. Tier acceptance floors `GM_MIN_APPEAL = {"ELITE": 0.55, "STANDARD": 0.35, "PROMOTION": 0.0}` evaluated against owner-adjusted `effective_appeal`.
  2. Tier-weighted appeal scoring (`GM_APPEAL_WEIGHT`) plus `owner_policy.gm_hiring_key`.
  3. Refusal tracking (`declined: [{key, tier, reason, ...}]`) on the search result and `_hire_gm` report.
  4. `owner_hiring` feature switchboard integration (`off`, `shadow`, `active`) in `_hire_gm`, skipping user-controlled teams.
- Unit tests covering all new and changed behavior.

**Out (non-goals):**
- Changing head-coach or coordinator salary bands or carousel candidate scoring (`engines/coaching_carousel.py`).
- Changing the Godfather Full Control offer gates (`STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN.md` §2.3).
- Adding database tables, columns, or migrations (all changes operate on existing tables and pure functions).
- Changing how `gm_personas.py` seeds replacement GM traits or voice cards after `gm_search` selects the winning tier.

## 2. Design

### 2.1 GM Salary Bands & Experience Tiering (`engines/staff_contracts_engine.py`, `engines/staff_contracts_run.py`)

Update `STAFF_SALARY_SCALES["GM"]` and the tiering cutover in `engines/staff_contracts_engine.py`:

```python
STAFF_SALARY_SCALES = {
    "HC": {
        "LEGENDARY": (16_000_000, 22_000_000),   # Super Bowl champion / Tier 1
        "ESTABLISHED": (8_000_000, 14_000_000),  # Multi-year winning HC
        "PROMOTION": (4_500_000, 7_500_000),    # Rising coordinator first-timer
    },
    "GM": {
        "ELITE": (8_000_000, 12_000_000),       # Proven championship architect ($10.0M midpoint)
        "STANDARD": (4_000_000, 7_500_000),     # Established personnel director ($5.75M midpoint)
        "PROMOTION": (2_000_000, 3_500_000),    # First-time assistant GM / scouting director ($2.75M midpoint)
    },
    "COORD": {
        "TOP_TIER": (2_500_000, 4_000_000),     # Highly sought playcaller
        "STANDARD": (1_500_000, 2_400_000),     # Position coach promotion
    },
}

GM_ELITE_CHAMPIONSHIPS = 1
GM_STANDARD_EXPERIENCE = 3
```

- `salary_tier("GM", experience=0, championships=0)`:
  - Returns `"ELITE"` when `championships >= GM_ELITE_CHAMPIONSHIPS` (`1`).
  - Returns `"STANDARD"` when `experience >= GM_STANDARD_EXPERIENCE` (`3`).
  - Returns `"PROMOTION"` otherwise (`experience < 3` and `championships == 0`).
- `market_salary("GM", experience, championships)` continues to return `(lo + hi) // 2`:
  - `"ELITE"` (`experience=6, championships=1`): `$10,000,000`
  - `"STANDARD"` (`experience=4, championships=0`): `$5,750,000`
  - `"PROMOTION"` (`experience=1, championships=0`): `$2,750,000`
- In `engines/staff_contracts_run.py` (`current_staff`), replace the `SELECT count(*) FROM gm_profiles WHERE id = %s` query for `s["person_kind"] == "GM"` with `s["experience"] = max(1, int(season) - int(s["start_season"] or season) + 1)` so an incumbent multi-year GM (`start_season <= season - 2`) has `experience >= 3` (`"STANDARD"`) while a newly hired first-year GM has `experience == 1` (`"PROMOTION"`).

### 2.2 Owner GM Hiring Policy (`engines/owner_policy.py`)

Add `GM_HIRING_CAP = 0.25` and `gm_hiring_key(owner_view, candidate) -> float` to `engines/owner_policy.py`:

```python
GM_HIRING_CAP = 0.25
GM_HEADLINE_ELITE = 0.15
GM_HEADLINE_PROMOTION = -0.05
GM_DEEP_POCKETS_ELITE = 0.10
GM_BUDGET_PROMOTION = 0.15
GM_BUDGET_ELITE = -0.15
GM_MEDDLER_PROMOTION = 0.15
GM_MEDDLER_ELITE = -0.10
```

```python
def gm_hiring_key(owner_view, candidate) -> float:
    """candidate: a gm_rollover candidate ({key, tier ELITE|STANDARD|PROMOTION, stature, salary, ...}).
    Returns a bounded modifier in [-GM_HIRING_CAP, GM_HIRING_CAP]; 0.0 for a neutral owner."""
    traits = _traits(owner_view)
    tier = (candidate or {}).get("tier")
    x = 0.0
    if "HEADLINE_HUNTER" in traits:
        if tier == "ELITE":
            x += GM_HEADLINE_ELITE
        elif tier == "PROMOTION":
            x += GM_HEADLINE_PROMOTION
    if "DEEP_POCKETS" in traits and tier == "ELITE":
        x += GM_DEEP_POCKETS_ELITE
    if "BUDGET_CONSCIOUS" in traits:
        if tier == "PROMOTION":
            x += GM_BUDGET_PROMOTION
        elif tier == "ELITE":
            x += GM_BUDGET_ELITE
    if "MEDDLER" in traits:
        if tier == "PROMOTION":
            x += GM_MEDDLER_PROMOTION
        elif tier == "ELITE":
            x += GM_MEDDLER_ELITE
    if tier == "PROMOTION":
        x += OPENNESS_FIRST_TIMER * (_slider(owner_view, "openness") - 5)
    return round(base.clamp(x, -GM_HIRING_CAP, GM_HIRING_CAP), 4)
```

- A neutral owner (`None` or `{}`) returns `0.0` for all tiers.
- `appeal_term(owner_view)` (`-0.05` for `"MEDDLER"`, `0.0` otherwise, already in `engines/owner_policy.py:103-105`) is reused to adjust the opening's destination appeal before checking candidate acceptance floors.

### 2.3 Candidate Acceptance, Refusal & Scoring Model (`engines/gm_rollover.py`)

#### Constants & Candidate Pool
```python
GM_MIN_APPEAL = {"ELITE": 0.55, "STANDARD": 0.35, "PROMOTION": 0.0}
GM_APPEAL_WEIGHT = {"ELITE": 0.40, "STANDARD": 0.20, "PROMOTION": 0.05}
```

- `gm_candidate_pool(abbr, season)` attaches `"min_appeal": GM_MIN_APPEAL[tier]` to each candidate dict:
  - `"ELITE"`: `experience=6, championships=1, base=0.85, salary=10_000_000, min_appeal=0.55`
  - `"STANDARD"`: `experience=4, championships=0, base=0.55, salary=5_750_000, min_appeal=0.35`
  - `"PROMOTION"`: `experience=1, championships=0, base=0.30, salary=2_750_000, min_appeal=0.0`

#### Scoring Function (`gm_candidate_score`)
```python
def gm_candidate_score(candidate, appeal, owner_view=None) -> float:
    """Pure. Candidate stature plus tier-weighted surplus appeal above the tier floor and the owner's GM hiring
    preference term."""
    from engines import owner_policy as op
    tier = candidate.get("tier") or "STANDARD"
    stature = float(candidate.get("stature") or 0.0)
    floor = float(candidate.get("min_appeal", GM_MIN_APPEAL.get(tier, 0.0)))
    weight = GM_APPEAL_WEIGHT.get(tier, 0.20)
    surplus_appeal = max(0.0, float(appeal) - floor)
    return round(stature + weight * surplus_appeal + op.gm_hiring_key(owner_view, candidate), 4)
```

Why this resolves the three Chunk 7 B8 defects:
1. **Appeal gates candidate willingness:**
   - Effective opening appeal is `eff_appeal = round(max(0.0, min(1.0, float(appeal) + op.appeal_term(owner_view))), 3)`.
   - When `eff_appeal < 0.55`, the `ELITE` architect refuses the opening (`reason="appeal"`), even if the owner has `$50M` in available cash.
   - When `eff_appeal < 0.35`, both `ELITE` and `STANDARD` refuse (`reason="appeal"`), leaving the first-time `PROMOTION` candidate as the only willing executive.
   - When `eff_appeal >= 0.55`, all three tiers are willing to accept if the owner can afford their market salary.
2. **Appeal weights candidate scores differently by tier:**
   - `GM_APPEAL_WEIGHT` scales surplus appeal above `min_appeal` by `0.40` (`ELITE`), `0.20` (`STANDARD`), and `0.05` (`PROMOTION`), rather than adding a constant offset across tiers.
3. **Owner posture can tip close searches:**
   - A neutral owner hires the highest-stature willing & affordable candidate (`ELITE` if `eff_appeal >= 0.55` and `budget >= $10.0M`; `STANDARD` if `eff_appeal >= 0.35` and `budget >= $5.75M`; else `PROMOTION`).
   - A `BUDGET_CONSCIOUS` + `MEDDLER` owner (`+0.25` on `PROMOTION`, `-0.25` on `ELITE`, plus `-0.05` on `eff_appeal`) pushes borderline openings out of `ELITE` reach and boosts `PROMOTION` when `STANDARD` stature is low and `PROMOTION` stature is high.

#### Search Function (`gm_search`)
```python
def gm_search(candidates, *, appeal, budget, owner_view=None):
    """Pure. Evaluates candidate willingness (effective_appeal >= min_appeal) and affordability (salary <= budget),
    recording refusals in `declined`. Returns a copy of the winning candidate dict enriched with
    {score, effective_appeal, declined}, or None if `candidates` is empty."""
```
- **Evaluation order:**
  1. Compute `eff_appeal = round(max(0.0, min(1.0, float(appeal) + op.appeal_term(owner_view))), 3)`.
  2. Walk `candidates` in order:
     - Let `floor = float(c.get("min_appeal", GM_MIN_APPEAL.get(c.get("tier"), 0.0)))` and `sal = int(c.get("salary") or 0)`.
     - If `eff_appeal < floor`: append `{"key": c["key"], "tier": c.get("tier"), "reason": "appeal", "min_appeal": floor, "appeal": eff_appeal}` to `declined`.
     - Else (`willing`): if `sal > budget`, append `{"key": c["key"], "tier": c.get("tier"), "reason": "budget", "salary": sal, "budget": budget}` to `declined`; otherwise append `c` to `eligible`.
  3. **Winner selection:**
     - If `eligible` is non-empty: pick `max(eligible, key=lambda c: (gm_candidate_score(c, eff_appeal, owner_view), -(c.get("salary") or 0), c["key"]))`.
     - Else if `willing` (`eff_appeal >= floor`) is non-empty (e.g., `budget < $2,750,000`): pick the cheapest willing candidate `min(willing, key=lambda c: ((c.get("salary") or 0), -gm_candidate_score(c, eff_appeal, owner_view), c["key"]))`.
     - Else (all candidates declined on appeal, e.g., a synthetic caller passed only `ELITE` candidates on a low-appeal opening): pick the cheapest candidate overall `min(candidates, key=lambda c: ((c.get("salary") or 0), -round(c.get("stature") or 0, 3), c["key"]))`.
  4. Return `dict(winner, score=gm_candidate_score(winner, eff_appeal, owner_view), effective_appeal=eff_appeal, declined=declined)`.

### 2.4 Wiring `owner_hiring` in `_hire_gm` (`engines/gm_rollover.py`)

In `_hire_gm(cur, season, prior, abbr, team_id, llm, *, why, log=print, vacate_only=False, first_draft=None)`:
- Read `hmode = octx.feature_mode(cur, "owner_hiring")` (via `engines.owner_context`).
- If `hmode != "off"` and `team_id not in octx.user_team_ids(cur)`:
  - Look up `view = octx.team_owner_views(cur).get(team_id)`.
- When `hmode == "active"` and `view` is present:
  - Call `winner = gm_search(pool, appeal=appeal, budget=budget, owner_view=view)`.
- When `hmode == "shadow"` and `view` is present:
  - Call `winner = gm_search(pool, appeal=appeal, budget=budget)` (baseline) and `alt = gm_search(pool, appeal=appeal, budget=budget, owner_view=view)`.
  - If `alt["key"] != winner["key"]` or `alt["effective_appeal"] != winner["effective_appeal"]`, attach `"owner_shadow": {"picked": winner["tier"], "owner_pick": alt["tier"], "appeal": round(appeal, 3), "owner_appeal": alt["effective_appeal"]}` to the returned hire dict.
- Include `"declined": list(winner.get("declined") or ())` in `_hire_gm`'s returned dict:
  `{"gm": p["gm_name"], "tier": winner["tier"], "salary": winner["salary"], "appeal": round(appeal, 3), "effective_appeal": winner.get("effective_appeal", round(appeal, 3)), "budget": budget, "declined": list(winner.get("declined") or ())}`.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Existing tests in `tests/test_staff_contracts.py` and `tests/test_carousel_appeal_enhanced.py` assert pre-fix `"STANDARD"` fallback for `experience=0` and `budget=1_000_000`. | `pytest -m "not integration" tests/test_staff_contracts.py tests/test_carousel_appeal_enhanced.py`. | Update those assertions in U1 and U2 to expect `"PROMOTION"`, and keep `experience >= 3` tested as `"STANDARD"`. |
| R2 | `staff_contracts_run.annual` files contracts for all 32 current GMs at `FREE_AGENCY`; if `experience` stays `1`, veteran GMs drop from `$5.75M` to `$2.75M`. | `tests/test_staff_contracts_run.py` with both a 2030 veteran GM and a 2036 rookie GM. | U1 changes `staff_contracts_run.py:77` to compute `max(1, season - start_season + 1)`, keeping 3+-season GMs in `"STANDARD"`. |
| R3 | A low-appeal team with a large budget gets a `PROMOTION` or `STANDARD` GM instead of `ELITE`. | Synthetic matrix across `appeal` in `[0.1, 0.9]` and `budget` in `[$1M, $50M]`. | Intentional per `M-0064`; `GM_MIN_APPEAL` constants (`0.55` / `0.35` / `0.0`) live at module top in `engines/gm_rollover.py` for easy tuning. |

**Spike first:** none. All affected functions are pure or already covered by offline SQLite/cursor fakes.

## 4. Tests (synthetic, no live DB)

- `tests/test_staff_contracts.py` →
  - `STAFF_SALARY_SCALES["GM"]` has `"ELITE": (8_000_000, 12_000_000)`, `"STANDARD": (4_000_000, 7_500_000)`, and `"PROMOTION": (2_000_000, 3_500_000)`.
  - `salary_tier("GM", experience, championships)` places `experience in (0, 1, 2), championships=0` into `"PROMOTION"`, `experience >= 3, championships=0` into `"STANDARD"`, and `championships >= 1` into `"ELITE"`.
  - `market_salary("GM", ...)` returns `$2,750,000` (`PROMOTION`), `$5,750,000` (`STANDARD`), and `$10,000,000` (`ELITE`).
- `tests/test_staff_contracts_run.py` →
  - `annual(cur, 2036)` files a veteran GM (`start_season = 2030`) at `"STANDARD"` (`$5,750,000`) and a first-year GM (`start_season = 2036`) at `"PROMOTION"` (`$2,750,000`).
- `tests/test_owner_policy.py` →
  - `gm_hiring_key(owner_view, candidate)` returns `0.0` for neutral owners, stays within `[-0.25, 0.25]` across all trait/slider combinations in `all_views()`, favors `"ELITE"` for `HEADLINE_HUNTER` / `DEEP_POCKETS`, and favors `"PROMOTION"` (while penalizing `"ELITE"`) for `BUDGET_CONSCIOUS` / `MEDDLER` and high `openness`.
- `tests/test_gm_rollover.py` →
  - `gm_candidate_pool` generates strictly ordered salaries (`PROMOTION $2.75M < STANDARD $5.75M < ELITE $10.0M`) and attaches `min_appeal` (`0.0`, `0.35`, `0.55`).
  - Chunk 7 `G1` & `G2` confirmation: across `appeal in (0.0, 0.5, 1.0)` at `budget = 30_000_000`, `gm_search` picks `{"PROMOTION", "STANDARD", "ELITE"}` (appeal changes the pick); across budgets `(0, 1_000_000, 5_750_000, 10_000_000)` at `appeal = 0.6`, all three tiers (`PROMOTION`, `STANDARD`, `ELITE`) are reachable.
  - `declined` records `reason="appeal"` when `ELITE` refuses a `0.40`-appeal opening (even with `$50M` budget) and `reason="budget"` when `ELITE` is willing (`appeal = 0.70`) but `budget = 6_000_000`.
  - `owner_view` interaction: a `MEDDLER` owner (`appeal_term = -0.05`) pushes a `0.56`-appeal opening to `effective_appeal = 0.51`, causing `ELITE` to decline on appeal; a `BUDGET_CONSCIOUS` + `MEDDLER` owner with high openness can select `PROMOTION` over a low-stature `STANDARD` candidate on a borderline opening.
  - `_hire_gm` wires `owner_hiring` in `off`, `shadow` (populating `owner_shadow` without changing the hire), and `active` modes, and never applies owner terms to JR's teams.
- `tests/test_carousel_appeal_enhanced.py` →
  - `test_gm_search_picks_the_best_affordable_candidate` verifies `ELITE` wins at `appeal=0.6, budget=50_000_000`, `STANDARD` wins when `ELITE` declines at `appeal=0.4, budget=50_000_000`, and `PROMOTION` (`$2,750,000`) wins at `appeal=0.3, budget=1_000_000`.

Run with `pytest -m "not integration" tests/test_staff_contracts.py tests/test_staff_contracts_run.py tests/test_owner_policy.py tests/test_gm_rollover.py tests/test_carousel_appeal_enhanced.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: GM Salary Bands, Experience Tiering & Owner GM Policy

- **Owns:** `engines/staff_contracts_engine.py`, `engines/staff_contracts_run.py`, `engines/owner_policy.py`, `tests/test_staff_contracts.py`, `tests/test_staff_contracts_run.py`, `tests/test_owner_policy.py`
- **Reads:** `docs/done/STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN.md` §2.2, `docs/done/OWNERS_SCOUTS_VOTERS_PLAN.md` §2.3
- **Depends on:** none
- **Done when:** `STAFF_SALARY_SCALES["GM"]["PROMOTION"]` is `(2_000_000, 3_500_000)`, `salary_tier("GM", ...)` tiers `< 3` seasons without a championship as `"PROMOTION"` and `>= 3` seasons as `"STANDARD"`, `staff_contracts_run.current_staff` computes GM experience from `start_season`, and `owner_policy.gm_hiring_key` is implemented and tested.
- **Verify:** `pytest -m "not integration" tests/test_staff_contracts.py tests/test_staff_contracts_run.py tests/test_owner_policy.py -q` passes.

### U2: Competitive GM Search Acceptance, Refusal & Owner Wiring

- **Owns:** `engines/gm_rollover.py`, `tests/test_gm_rollover.py`, `tests/test_carousel_appeal_enhanced.py`
- **Reads:** `engines/staff_contracts_engine.py`, `engines/owner_policy.py`, `engines/owner_context.py`, `engines/coaching_carousel.py`
- **Depends on:** U1
- **Done when:** `gm_candidate_pool`, `gm_candidate_score`, `gm_search`, and `_hire_gm` enforce tier appeal floors (`GM_MIN_APPEAL`), tier-weighted appeal scoring (`GM_APPEAL_WEIGHT`), refusal reporting (`declined`), and `owner_hiring` (`off`/`shadow`/`active`) integration, and all synthetic tests (including Chunk 7 `G1`/`G2` checks) pass.
- **Verify:** `pytest -m "not integration" tests/test_gm_rollover.py tests/test_carousel_appeal_enhanced.py tests/test_owner_wiring.py -q` passes.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1 | — | Defines the `PROMOTION` salary band, GM experience tiering, and `owner_policy.gm_hiring_key` interface. |
| 2 | U2 | U1 | Uses U1's `market_salary("GM", 1, 0)` and `owner_policy.gm_hiring_key` in `gm_rollover.py`. |

*(Note: Because the entire plan spans 9 files across two small tightly coupled steps, JR may also authorize building U1 and U2 inline in a single pass without `/execute-plan` worktrees.)*

**File ownership check:**

| File | Unit |
|---|---|
| `engines/gm_rollover.py` | U2 |
| `engines/owner_policy.py` | U1 |
| `engines/staff_contracts_engine.py` | U1 |
| `engines/staff_contracts_run.py` | U1 |
| `tests/test_carousel_appeal_enhanced.py` | U2 |
| `tests/test_gm_rollover.py` | U2 |
| `tests/test_owner_policy.py` | U1 |
| `tests/test_staff_contracts.py` | U1 |
| `tests/test_staff_contracts_run.py` | U1 |

## 7. Spec changes when built

- **§2.19 (`Coaching Carousel` / `Owner terms` & `Owner wiring`):** document `owner_policy.gm_hiring_key(owner_view, candidate)` (`|x| <= 0.25`) and note that `owner_hiring` also applies `appeal_term` and `gm_hiring_key` to CPU GM searches in `gm_rollover._hire_gm` (with `owner_shadow` recorded in shadow mode).
- **§3.3a (`staff_contracts`):** note that `STAFF_SALARY_SCALES["GM"]` includes `"PROMOTION"` (`$2.0M–$3.5M`, midpoint `$2.75M`) below `"STANDARD"` (`$4.0M–$7.5M`, `$5.75M`, `experience >= 3`) and `"ELITE"` (`$8.0M–$12.0M`, `$10.0M`, `championships >= 1`), and that `staff_contracts_run` measures a GM's experience from `season - start_season + 1`.
- **§3.3f (`Front Office: GM agents` → `Season rollover`):** update the `gm_candidate_pool` / `gm_search` description to state the three GM salary tiers (`ELITE` `$10.0M`, `STANDARD` `$5.75M`, `PROMOTION` `$2.75M`), the `GM_MIN_APPEAL` acceptance floors (`0.55` / `0.35` / `0.0`), the tier-weighted appeal score (`GM_APPEAL_WEIGHT` + `gm_hiring_key`), the `declined` refusal list on the search report, and the `PROMOTION` fallback when higher tiers decline or exceed budget.
- **Repository Layout:** no new or moved files.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | What salary band should `STAFF_SALARY_SCALES["GM"]["PROMOTION"]` use? | **`(2_000_000, 3_500_000)` (`$2,750,000` midpoint)**, sitting just above `COORD["STANDARD"]` (`$1.95M`) and below `GM["STANDARD"]` (`$4.0M–$7.5M`, `$5.75M` midpoint), with `GM_STANDARD_EXPERIENCE = 3` seasons. (Alternative: `(1_500_000, 3_000_000)` / `$2.25M` midpoint.) |
| Q2 | What opening appeal floors (`GM_MIN_APPEAL`) should gate candidate acceptance? | **`ELITE`: `0.55`, `STANDARD`: `0.35`, `PROMOTION`: `0.0`**, evaluated against owner-adjusted `effective_appeal` (`appeal + appeal_term(owner_view)`). An `ELITE` architect refuses below-average openings (`< 0.55`) even if the owner has `$10M+` cash; `STANDARD` refuses bottom-tier situations (`< 0.35`); `PROMOTION` always accepts. |
| Q3 | Should `owner_hiring` (`owner_policy.appeal_term` + `gm_hiring_key`) wire into `_hire_gm` using the same `off` / `shadow` / `active` switchboard pattern as `coaching_carousel`? | **Yes.** Off (today's default) uses neutral owner terms; shadow hires as off and logs `owner_shadow` when the owner's terms would change the GM tier or effective appeal; active applies both terms to CPU teams (never JR's teams). |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | gm-hiring-u1 | .claude/worktrees/gm-hiring-u1 | merged | 80b9667 |
| U2 | 2 | gm-hiring-u2 | .claude/worktrees/gm-hiring-u2 | merged | 7f06382 |

**As built:** both units landed exactly as designed. U2 added two robustness guards inside `_hire_gm` that the
plan did not spell out: the shadow comparison uses `.get` on the winner key/appeal so an empty-pool fallback dict
cannot raise, and the owner-view lookup is wrapped so a failing owner read can never fail a hire (matching
`owner_context`'s rule that the switchboard never fails an engine). Both are covered by the `_hire_gm` mode tests.
The plan's predicted red test (`test_gm_search_picks_the_best_affordable_candidate`) was corrected in U2 to expect
PROMOTION at `budget=1_000_000`. No deviations in U1; no blockers in either unit.

**Audit (2026-11-10, `claude-opus-5-5` at `145d6cb`):** built as written — §2.1–§2.4 all present, B8 closed (appeal
changes the pick, all three tiers reachable, PROMOTION pays $2.75M, shadow never changes a hire), the one active
GM-contract index breaks no hire path, 103 plan tests pass. Five defects were found in the surrounding paths and
handed to the builder: **B1** the fallback winner also appears in its own `declined` list; **B2** the
`team_owner_views` guard has no savepoint; **B3** a Godfather coach-GM gets a second, GM contract; **B4** seeded GMs
start in 2030, so every seeded incumbent prices as PROMOTION; **B5** a re-filed ELITE GM drops to STANDARD because
`championships` is hard-coded. Design note for JR: the ELITE-favouring owner terms (`HEADLINE_HUNTER`,
`DEEP_POCKETS`) never change a pick, because every owner-driven flip is downward and mostly `MEDDLER`'s, so
`owner_hiring` needs tuning before it goes active.
