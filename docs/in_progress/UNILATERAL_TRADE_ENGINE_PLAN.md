# Project Mu: Unilateral Trade Engine & Rivalry Contention Plan

**Status:** approved by JR (2026-11-09); building, wave 1 of 2.
**Builds on:** `PROJECT_SPEC.md` §2.17 (Trade Value, Proposal Engine, Negotiation, Talks), [`03_GM_AGENTS_PLAN.md`](../done/team_management/03_GM_AGENTS_PLAN.md) §2.4–2.5, [`TRADE_NEGOTIATIONS_PLAN.md`](../done/TRADE_NEGOTIATIONS_PLAN.md) (V2.11), [`CLOSE_OUT_CHECKLIST.md`](../CLOSE_OUT_CHECKLIST.md) (`P1-25`, `P3-07`, `P5-07`, `P5-14`, `P8-10`).

**Goal:** Stop the trade engine from pre-screening offers through both GMs' private valuations before a talk starts. Each initiating GM searches for targets and builds an opening offer using only their own valuation, their own personality (aggressiveness, risk appetite, sliders, traits), and public league facts; the receiving GM then evaluates that offer against their own internal standards—including a rivalry-and-contention tax that makes trading with a division rival or fellow conference contender much costlier than trading with a rebuilding team in the other conference—and chooses whether to accept, counter, reject, or walk away.

**Delivers:**
- Unilateral target and package search in `engines/gm_trade_engine.py`: `public_seller_value`, `persona_target_weight`, `opening_posture`, `initiator_accepts`, `evaluate_initiator`, `choose_opening`, `choose_shaped_opening`.
- Rivalry $\times$ contention tax and division-contender rules in `engines/gm_trade_engine.py`: `contender_status`, `rival_tax(b, sender, receiver)` (0% inter-conference baseline up to 40% same-conference contenders and 75% same-division contenders), `division_check` (blocks `OVR >= 84` starters between contending division rivals in season), and `ladder(p, season, rival_step=0)` (allows a 1-round overpay on the in-season price ladder when the seller faces a $\ge 25\%$ rival/contender tax).
- Real opening anchors and bilateral counter flow in `engines/gm_talks.py` and `engines/gm_negotiation.py`: `_options_for_opening` (adds outgoing pick-downgrade swaps and carries the search's `FIRM` and `ANCHOR` candidates), `open_talk` (opens with the initiator's personality-chosen offer rather than a pre-agreed package), and `recheck_open` (keeps unilateral CPU offers to user teams open as long as the offering CPU GM still stands behind them).
- Updated `--simulate-season` and `--replay-week` diagnostics reporting opening posture shares (`FIRM` vs `ANCHOR`), multi-turn counter rates, agreed vs stalled talks, and rivalry tax effects.

**JR's answers (2026-11-09):**
- **D1:** A GM's opening trade offer aggressiveness is driven by the GM's personality (`aggressiveness`, `risk_appetite`, persona sliders, and traits such as `CAP_MIND`, `DRAFTNIK`, `OLD_SCHOOL`, `ANALYTICS_BELIEVER`, and `MEASURED`), so sharks lowball/anchor while measured GMs or contenders with urgent needs open near fair value.
- **D2:** Team B evaluates Team A's identity and contention status, not just geography: trading to a division rival or a fellow contender in the same conference carries a much steeper tax (and allows a 1-round higher price on the in-season ladder) than trading to a non-contending team in the other conference.
- **D3 (Q1):** In season, when both division rivals are in the playoff race (`CONTEND` or `.500+` `RETOOL`), starters at `OVR >= 84` (`DIV_CONTEND_MAX_OVR = 84`) are blocked from moving inside the division (`88+` remains blocked inside the division for all teams year-round, and 75% `DIV_CONTEND_TAX` applies below 84).
- **D4 (Q2):** CPU offers targeting a user-controlled team also use the CPU GM's unilateral opening offer (stored `PROPOSED` and surfaced in the `GM_TALK` Inbox thread), and `recheck_open` checks only that the initiating CPU GM still accepts the offer.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `engines/gm_trade_engine.py:1119` (`shaped_buy_deals`), `:1161` (`shaped_sell_deals`), `:1323` (`buy_deals`), `:1348` (`sell_deals`), and `:1606` (`request_deals`) filter targets by `gain = valuer.value(x, buyer) - valuer.value(x, seller) > 0`, and `packages` (`:1365`, `:1369`) sorts candidate assets by `valuer.value(x, other_team)`. | The initiating GM peeks at the other GM's private `youth_bias`, `pick_affinity`, `position_bias`, `DEV_PREMIUM` (100% owner vs 60% league), and pick scarcity before making an offer. Target and package selection must use only the initiator's valuation `valuer.value(x, initiator)` and a public estimate `public_seller_value`. |
| S2 | `engines/gm_trade_engine.py:1388-1403` `evaluate` filters every candidate through `accepts(d, valuer, b)` (`:1234-1247`), which requires **both** $\Delta_A \ge \theta_A$ and $\Delta_B \ge \theta_B$; `choose` (`:1250-1265`) then ranks by `joint = min(da, db)`. | Every deal that survives `run_week`'s search is already accepted by both GMs before talks open. In the P1-25 replay on `madden_2036` (`PROJECT_SPEC.md:1015`), 7 of 7 CPU talks agreed in 2 turns with 0 stalls, 0 carries, and 0 leaks. Search must filter by `initiator_accepts` ($\Delta_A \ge \theta_A$ and legality) and choose by the initiator's personality posture (`choose_opening`). |
| S3 | `engines/gm_talks.py:101-136` `_options_for_opening` builds `ANCHOR` variants from the opening offer only by dropping a non-core outgoing asset (`:112-114`) or adding an asset from `other` (`:115-116`). | In a 1-player-for-1-pick in-season `RENTAL`, dropping the only outgoing pick leaves an empty side, and adding a player from `other` fails `classify_shape` (`"more than one player a side"`). `_options_for_opening` never tries swapping the initiator's outgoing pick for a cheaper round pick the initiator owns, so `anchors` is often empty even when the ladder spans two rounds (e.g. R4–R5). |
| S4 | `engines/gm_trade_engine.py:58-60` and `:644-645` `rival_tax(b, sender, receiver)` returns a flat `DIVISION_TAX = 0.50` for same division, `CONFERENCE_TAX = 0.10` for same conference, and `0.0` for different conference, ignoring `b["ctx"][t]["direction"]` and `b["wl"][t]`. | A contending team trading a starter to another contender in the same conference pays only 10%—the exact same as a 1–7 rebuilding team trading a veteran to a 2–6 team. `rival_tax` must scale by both alignment and the two teams' contention status (`CONTEND` / `HUNT` / `OUT`). |
| S5 | `engines/gm_trade_engine.py:876-892` `division_check` blocks only `OVR >= ELITE_OVR` (88) inside the division. | Two contending division rivals can trade an 84–87 OVR starter to each other in season if the pick math clears the 50% tax. When both division rivals are in the race (`CONTEND` or `HUNT`), `division_check` should block `OVR >= 84` starters in season. |
| S6 | `engines/gm_trade_engine.py:901-918` `ladder(p, season)` returns `(lo, hi)` from player OVR, age, and contract years left alone, and `classify_shape` (`:994-998`) fails any `RENTAL` where `main < lad[0]` (`"price off the ladder"`). | When a seller faces a heavy rivalry or conference-contender tax ($\ge 25\%$), the seller's `counter_options` in `gm_negotiation.py:112` cannot legally ask for a pick 1 round earlier than `lad[0]` because `check_legality` marks `shape` false. `classify_shape` must pass `rival_step=1` into `ladder` when `rival_tax(b, seller, buyer) >= CONF_HUNT_TAX` (0.25). |
| S7 | `engines/gm_trade_engine.py:2043-2071` `recheck_open` expires any open `ENGINE` proposal where `not accepts(deal, valuer, b)` (`:2060`). | If a CPU GM sends a unilateral `PROPOSED` offer to a user-controlled team (which `engines/inbox/kinds/gm_talk.py:145` `scan` surfaces as an inbound `TRADE_INTEREST` thread), `recheck_open` would expire it if JR's side is below $\theta_{\text{user}}$. For `PROPOSED` engine offers, `recheck_open` should require only that the initiating GM (`team_a_id`) still accepts; `APPROVED` engine deals still require both sides. |
| S8 | `engines/gm_trade_engine.py:1980-2040` `find_packages` (#debug Trade Finder) uses `accepts(d, valuer, b)` to split packages into `viable` (both GMs accept right now) vs `misses`. | `#debug` Trade Finder is a diagnostic inspection tool and should keep showing whether both GMs would accept a package outright (`accepts`), while using the upgraded `rival_tax` and `ladder` rules. |

## 1. Scope

**In:**
- Unilateral candidate generation in `engines/gm_trade_engine.py` (`shaped_buy_deals`, `shaped_sell_deals`, `buy_deals`, `sell_deals`, `request_deals`, `packages`) using only the initiator's valuation `valuer.value(x, initiator)`, neutral league value `valuer.league(x)`, and `public_seller_value`.
- Personality-weighted target selection (`persona_target_weight`) and opening offer posture (`opening_posture`, `choose_opening`, `choose_shaped_opening`) driven by `aggressiveness`, `risk_appetite`, `pick_affinity`, `youth_bias`, `position_bias`, `sliders`, and `traits`.
- Rivalry $\times$ contention tax matrix (`contender_status`, `rival_tax`) and division-contender starter block (`division_check`, `DIV_CONTEND_MAX_OVR = 84`).
- Price ladder overpay step (`ladder(p, season, rival_step=0)`) when a seller trades with a division rival or same-conference contender/in-the-hunt team (`rival_tax >= 0.25`).
- Multi-turn negotiation integration in `engines/gm_talks.py` and `engines/gm_negotiation.py`: outgoing pick-swap anchors in `_options_for_opening`, unilateral opening offers in `open_talk`, and responder evaluation on Turn 2 via `judge` and `counter_options`.
- `recheck_open` update so unilateral `PROPOSED` offers to a user team check only the initiator's threshold.
- Synthetic unit tests covering unilateral offers, personality anchor vs firm choices, rivalry $\times$ contention tax tiers, ladder overpay legality, and multi-turn counter/stall/agree outcomes.

**Out (non-goals):**
- Draft-day and Rounds 2–7 pick-for-pick trades (`run_draft_day`, `run_draft_round`): kept one-shot on the clock, as in V2.11.
- Database schema migrations: `gm_trade_proposals`, `gm_trade_negotiations`, `gm_trade_negotiation_turns`, and `gm_trade_leaks` already have all required columns (`posture`, `options`, `choice`, `flags`, `STALLED`).
- Changes to base player or pick worth formulas in `engines/trade_value.py` (`fit_market`, `player_value`, `pick_value`).
- Frontend UI changes (`Mu_Network`): the Command Center Trade Machine and Front Office Inbox already render `GM_TALK` threads, postures, counters, and published CPU talk transcripts.

## 2. Design

### 2.1 Rivalry $\times$ Contention Tax Matrix & Division Rules (`engines/gm_trade_engine.py`)

A team's competitive standing in trade evaluation is classified by `contender_status(b, tid) -> str`:
- `"CONTEND"`: `b["ctx"][tid]["direction"] == "CONTEND"`.
- `"HUNT"`: `b["ctx"][tid]["direction"] == "RETOOL"` and `not losing(b, tid)` (in season: `wins >= losses`; offseason: `(b["ts"].get(tid, {}).get("proj_wins") or 8.5) >= 8.5`).
- `"OUT"`: `REBUILD`, or a `RETOOL` team with `losing(b, tid)`.

New constants in `engines/gm_trade_engine.py`:
```python
ELITE_OVR = 88                           # hard division block for any division teams
DIV_CONTEND_MAX_OVR = 84                 # hard in-season division block when both teams are CONTEND/HUNT
DIVISION_TAX = 0.50                      # division baseline (when at least one side is OUT)
DIV_HUNT_TAX = 0.65                      # same division, both in the race (at least one HUNT, neither OUT)
DIV_CONTEND_TAX = 0.75                   # same division, both CONTEND
CONFERENCE_TAX = 0.10                    # same conference baseline (seller is OUT, buyer is CONTEND/HUNT)
CONF_OUT_TAX = 0.05                      # same conference, receiver is OUT (not a playoff threat)
CONF_HUNT_TAX = 0.25                     # same conference, both in the race (at least one HUNT)
CONF_CONTEND_TAX = 0.40                  # same conference, both CONTEND
INTERCONF_CONTEND_TAX = 0.05             # different conference, both CONTEND (0.0 otherwise)
```

`rival_tax(b, sender, receiver) -> float`:
- Looks up `rel = rivalry(b, sender, receiver)` (`"division"` | `"conference"` | `None`), `s_stat = contender_status(b, sender)`, and `r_stat = contender_status(b, receiver)`:
  - If `rel == "division"`:
    - `s_stat == "CONTEND" and r_stat == "CONTEND"` $\rightarrow$ `DIV_CONTEND_TAX` (`0.75`)
    - `s_stat != "OUT" and r_stat != "OUT"` $\rightarrow$ `DIV_HUNT_TAX` (`0.65`)
    - otherwise $\rightarrow$ `DIVISION_TAX` (`0.50`)
  - If `rel == "conference"`:
    - `r_stat == "OUT"` $\rightarrow$ `CONF_OUT_TAX` (`0.05`)
    - `s_stat == "CONTEND" and r_stat == "CONTEND"` $\rightarrow$ `CONF_CONTEND_TAX` (`0.40`)
    - `s_stat != "OUT" and r_stat != "OUT"` $\rightarrow$ `CONF_HUNT_TAX` (`0.25`)
    - otherwise (`s_stat == "OUT"`, receiver is `CONTEND` or `HUNT`) $\rightarrow$ `CONFERENCE_TAX` (`0.10`, preserving exact parity with existing tests where Team 1 is `CONTEND` and Team 2 is `REBUILD`).
  - If `rel is None` (different conference):
    - `s_stat == "CONTEND" and r_stat == "CONTEND"` $\rightarrow$ `INTERCONF_CONTEND_TAX` (`0.05`)
    - otherwise $\rightarrow$ `0.0` (trading to a non-contender or from a seller across conferences is completely untaxed).

`division_check(deal, b) -> dict`:
- Keeps the hard block when `rel == "division"` and any moved player has `ovr >= ELITE_OVR` (88).
- Adds a contending-division block in season (`b.get("window") == "IN_SEASON"`): when `rel == "division"` and both teams have `contender_status(b, t) != "OUT"`, any moved player with `ovr >= DIV_CONTEND_MAX_OVR` (84) fails with detail:
  `f"same division ({div}): {names} is a starter (OVR {DIV_CONTEND_MAX_OVR}+) and both teams are in the race"`.
- Reports the actual `rival_tax(b, sender, receiver)` percentage in `detail` when allowed.

### 2.2 Price Ladder Overpay Room (`ladder` and `classify_shape`)

In `engines/gm_trade_engine.py`:
- Signature becomes `ladder(p, season, rival_step=0) -> tuple[int, int] | None`.
  - Computes `(lo, hi)` exactly as today when `rival_step == 0`.
  - When `rival_step > 0`: `lo = max(2 if (p.get("ovr") or 0) >= 80 else 3, lo - rival_step)` (never allowing Round 1 outside a blockbuster, and never moving `hi`, so standard-round opening offers remain legal while 1-round higher counters are also legal).
- Helper `rival_ladder_step(b, seller, buyer) -> int`:
  - Returns `1` if `rival_tax(b, seller, buyer) >= CONF_HUNT_TAX` (`0.25`), else `0`.
- In `classify_shape(deal, b)` and `rental_packages(b, buyer, seller, target, include_overpay=False)`:
  - `classify_shape` calls `lad = ladder(star, season, rival_ladder_step(b, seller, buyer))` and allows an `R2` main pick when `lad[0] == 2` (`if any(rnd(x) <= 2 for x in bp) and lad[0] > 2:`).
  - `rental_packages` uses `rival_step = rival_ladder_step(b, seller, buyer)` when `include_overpay=True` (or when building packages for a buyer willing to pay the rival premium) so both standard and rival-overpay packages are generated.

### 2.3 Unilateral Target Valuation & Personality Weights (`engines/gm_trade_engine.py`)

To prevent Team $A$ from reading Team $B$'s private `valuer.value(x, b)`:

1. **`public_seller_value(b, valuer, asset, seller) -> int`:**
   - What an outside GM estimates `asset` is worth to `seller` using only public facts:
   - For a `PICK`: `valuer.league(asset) * (1.2 if s_dir == "REBUILD" else (0.8 if s_dir == "CONTEND" else 1.0))`.
   - For a `PLAYER`: starts from `valuer.league(asset)` ($V_L$, which uses 60% `DEV_VISIBILITY` and no private GM biases). Multiplies by public role on `seller` (`1.0` if `pid in b["ctx"][seller]["starter_ids"]`, `0.65` if in `rotation_ids`, else `0.35`) and by `0.65` if `seller` is publicly selling (`b["ctx"][seller]["selling"]` in season, or `s_dir == "REBUILD"` in the offseason on a player aged $\ge 27$ or with $\le 1$ contract year left).
   - Also adds `valuer.dead(asset, seller)` (since contract `cut_penalty` is public on the cap sheet) and `round(rival_tax(b, seller, buyer) * max(0, valuer.league(asset)))` (since division/conference and records are public).

2. **`persona_target_weight(b, tid, player, season, mode="BUY") -> float`:**
   - Shapes which players and partners GM `tid` pursues based on `b["gm"][tid]`:
   - Starts with `target_weight(player, season)` (`1.5` for veterans with $\le 2$ years left in season, else `1.0`).
   - Multiplies by `float((gm.get("position_bias") or {}).get(player["group"], 1.0))`.
   - Trait multipliers from `set(gm.get("traits") or ())`:
     - `OLD_SCHOOL`: $\times 1.15$ when `player["group"] in ("OT", "IOL", "DT", "EDGE", "RB", "LB")`.
     - `ANALYTICS_BELIEVER`: $\times 1.15$ when `player["group"] in ("QB", "WR", "CB", "EDGE", "OT")`.
     - `CAP_MIND`: $\times 1.15$ when `mode == "BUY"` and `cap_hit_now(player, season) < 0.85 * tv.worth(player["ovr"], player["group"], fits["market"])`, or when `mode == "SELL"` and `cap_hit_now(player, season) > tv.worth(...)`.
     - `DRAFTNIK`: $\times 1.20$ when `mode == "SELL"` (eager to acquire picks).

3. **Unilateral Search Functions (`shaped_buy_deals`, `shaped_sell_deals`, `buy_deals`, `sell_deals`, `request_deals`, `packages`):**
   - In `shaped_buy_deals` and `buy_deals`: `gain = valuer.value(x, buyer) - public_seller_value(b, valuer, x, seller)` (never `valuer.value(x, seller)`).
   - In `shaped_sell_deals`, `sell_deals`, and `request_deals`: `gain = public_buyer_value(b, valuer, x, buyer) - (valuer.value(x, seller) + valuer.dead(x, seller) + valuer.rival(x, seller, buyer))`, where `public_buyer_value` is `valuer.league(x) * (1.25 if group in buyer_top_needs else 1.0)`.
   - In `packages(b, valuer, buyer, seller, core, trigger, extras=True)`:
     - The initiator (`buyer` when buying, `seller` when selling) sorts the other team's non-starters/picks by the **initiator's** value (`-valuer.value(x, initiator)`) and sorts its own outgoing candidate assets by **neutral league value** (`-valuer.league(x)`) rather than `valuer.value(x, other_team)`.

### 2.4 Personality-Driven Opening Offer Selection (`engines/gm_trade_engine.py`)

1. **`initiator_accepts(deal, valuer, b) -> tuple[bool, int, int, int, int]`:**
   - Returns `(ok, da, db, la, lb)` where `da, db, la, lb = deltas(deal, valuer)`.
   - Checks **only** the initiating team `a = deal["a"]`:
     `urgent = ctx_a.get("urgent_need") and any(x["type"] == "PLAYER" and x["to"] == a and b["players"][x["player_id"]]["group"] == ctx_a["urgent_need"].get("group") for x in deal["assets"])`
     `ok = da >= threshold(ctx_a, bool(urgent), b.get("window", "IN_SEASON"))`
   - `accepts(deal, valuer, b)` remains intact for bilateral callers (`find_packages`, `recheck_open` on `APPROVED` proposals, and existing unit tests).

2. **`evaluate_initiator(deals, b, valuer) -> list[dict]`:**
   - Like `evaluate`, but calls `initiator_accepts(d, valuer, b)` instead of `accepts(d, valuer, b)`:
   - Keeps every candidate that is not in `b["hashes"]`, has no `rejected_players(d, b)`, passes `check_legality(d, b, valuer)`, and satisfies `initiator_accepts`.
   - Each returned dict carries `hash`, `da`, `db`, `la`, `lb`, `joint = min(da, db)`, `legality`, and `shape`.

3. **`opening_posture(persona, urgent=False) -> float`:**
   - Computes the GM's opening anchor propensity $\alpha \in [0.05, 0.90]$ from `persona` (`b["gm"][tid]`):
     ```python
     agg = float(persona.get("aggressiveness", 0.5) or 0.5)
     sl = persona.get("sliders") or {}
     conf = float(sl.get("confidence", 5) or 5)
     drive = float(sl.get("drive", 5) or 5)
     warmth = float(sl.get("warmth", 5) or 5)
     traits = set(persona.get("traits") or ())
     shift = (0.12 if "CAP_MIND" in traits else 0.0) + (0.08 if "DRAFTNIK" in traits else 0.0)
     if "MEASURED" in traits or "PEACEMAKER" in traits:
         shift -= 0.12
     if urgent:
         shift -= 0.20
     return max(0.05, min(0.90, 0.15 + 0.45 * agg + 0.04 * (conf - 5) + 0.03 * (drive - 5) - 0.03 * (warmth - 5) + shift))
     ```

4. **`choose_opening(cands, persona, rng, urgent=False) -> dict | None` and `choose_shaped_opening(cands, persona, rng, urgent=False) -> dict | None`:**
   - Draws `want_anchor = rng.random() < opening_posture(persona, urgent)`.
   - Defines two views over `cands`:
     - **Firm pool (`firm_cands`):** smallest asset count among candidates that are near neutral league-value parity (`la - lb` closest to `0`, or where the buyer pays the top/fair round of the ladder), ranked by `-abs(c["la"] - c["lb"]) + 0.25 * c["da"]`.
     - **Anchor pool (`anchor_cands`):** candidates that maximize the initiator's own surplus `c["da"]` (e.g., paying the bottom round of the ladder, or asking for a late pick back), ranked by `c["da"] - 250_000 * len(c["assets"])`.
   - Samples the winning candidate from `anchor_cands[:5]` if `want_anchor` else `firm_cands[:5]` via seeded softmax (`SOFTMAX_TEMP`), and attaches:
     - `pick["opening_posture"] = "ANCHOR" if want_anchor else "FIRM"`
     - `pick["opening_options"]`: up to `OPTION_K` (3) distinct candidates from `cands` (`FIRM` first, then up to 2 `ANCHOR` candidates with higher `da`) so `gm_talks.open_talk` has legal, shape-valid opening options ready.

### 2.5 Multi-Turn Talks & Counter Flow (`engines/gm_talks.py`, `engines/gm_negotiation.py`)

1. **`_options_for_opening(b, valuer, talk, actor, offer)` in `engines/gm_talks.py`:**
   - In addition to dropping a non-core outgoing asset or adding an asset from `user_pool`, `_options_for_opening` also generates **pick-downgrade swaps**: for each outgoing `PICK` sent by `initiator`, swap it for a later-round free pick owned by `initiator` in the same draft (e.g., swapping an R4 for an R5 on a two-round ladder).
   - Every candidate is checked with `check_legality(d, b, valuer)` (including `shape` in season and `fairness` in the offseason) so an `ANCHOR` option is always a legal offer.
   - If `talk.get("opening_options")` was passed from the unilateral search, those pre-validated options seed the list.

2. **`open_talk(cur, b, valuer, deal, rng)` in `engines/gm_talks.py`:**
   - Opens the `CPU` negotiation row and evaluates `options_for(b, valuer, opener, a, deal["assets"])`.
   - Because `deal["assets"]` is now the initiator's unilateral offer (not pre-screened by `accepts`), Turn 1 records the initiator's chosen opening option (`FIRM` or `ANCHOR`), and `advance_talk` immediately lets Responder `other` judge it on Turn 2 using `gm_negotiation.judge(b, valuer, ..., reserve)`.
   - On Turn 2:
     - If the offer clears `other`'s floor (`j["ok"]`), `other` sees `ACCEPT` (and `SQUEEZE` when `patience > 1`).
     - If the offer sits below `other`'s floor (`CLOSE`, `APART`, or `INSULTED`), `other` deducts patience (`2` if `INSULTED`, `1` otherwise) and sees up to `OPTION_K` (3) counters (`MEET`, `HOLD2`, `HOLD`) from `gm_negotiation.counter_options` plus `REJECT` (or `WALK_AWAY` if patience hits 0).
     - In `gm_negotiation.counter_options`, `check_legality(c["deal"], b, valuer)` now uses the rivalry-aware `ladder` (§2.2), so a responder taxed for trading to a division rival or conference contender can legally counter for a pick 1 round higher!

3. **`recheck_open(cur, b, dry_run=False, log=None, valuer=None)` in `engines/gm_trade_engine.py`:**
   - When `p["status"] == "PROPOSED"` and `p.get("origin", "ENGINE") == "ENGINE"`, checks `initiator_accepts(deal, valuer, b)` rather than `accepts(deal, valuer, b)` so a unilateral CPU offer waiting in a user team's Inbox isn't expired just because it was a lowball to the user.
   - When `p["status"] == "APPROVED"` and `p.get("origin", "ENGINE") == "ENGINE"`, continues to check `accepts(deal, valuer, b)`.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | With unilateral opening offers and higher rivalry/contender taxes, a larger share of CPU talks will stall (`STALLED` / `WALKED_AWAY`), which could lower executed in-season trades below the NFL target band (5–21 in `decision_targets.py`, target ~15–20). | `weekly_budget` already divides `e_w` by `p_exec = (3 + executed) / (5 + decided)`, where `decided` counts `EXECUTED + REJECTED + STALLED`. We verify with `simulate_season` and a multi-week replay in tests. | Adjust `MAX_NEW_PER_WEEK` (currently 4 in weeks 1–6, 8 in week 7, 14 in week 8) by +1 in weeks 1–6 if the per-week cap clips the `p_exec`-inflated Poisson draw. |
| R2 | Existing unit tests in `tests/test_gm_trade_engine.py` and `tests/test_gm_trade_shapes.py` call `accepts`, `choose`, `ladder(p, season)`, and `deltas` on synthetic two-team books (`AAA` CONTEND, `BBB` REBUILD). | Run `pytest -m "not integration" tests/test_gm_trade_engine.py tests/test_gm_trade_shapes.py tests/test_gm_talks.py tests/test_gm_negotiation.py`. | Keep `accepts(deal, valuer, b)`, `choose(cands, rng)`, `ladder(p, season, rival_step=0)` default signature, and `CONFERENCE_TAX = 0.10` / `DIVISION_TAX = 0.50` for `CONTEND` + `REBUILD` pairs backward-compatible. |

**Spike first:** none (all affected modules and tests are pure Python over in-memory dicts).

## 4. Tests (synthetic, no live DB)

- `tests/test_gm_trade_engine.py`:
  - `test_rivalry_and_contention_tax_matrix`: verifies `rival_tax(b, sender, receiver)` across inter-conference (0% when one side is not `CONTEND`, 5% when both `CONTEND`), same-conference (5% to `OUT`, 10% `OUT` seller to `CONTEND`, 25% `HUNT` vs `CONTEND`, 40% `CONTEND` vs `CONTEND`), and same-division (50% with an `OUT` team, 65% `HUNT`, 75% `CONTEND` vs `CONTEND`).
  - `test_division_contenders_block_starter_trades_in_season`: verifies that an 85 OVR starter is blocked by `division_check` in season when both division rivals are contending (`CONTEND` or winning `RETOOL`), while still allowed when the seller is `REBUILD` (with the 50% division tax).
  - `test_unilateral_search_does_not_require_receiver_acceptance`: verifies that when Team 2 privately overvalues its own player (e.g., high `position_bias` or `DEV_PREMIUM` on a Star/Superstar), Team 1 still proposes a legal ladder offer based on Team 1's valuation and `public_seller_value`, rather than returning `[]`.
  - `test_personality_drives_opening_posture_and_package`: verifies that a high-aggressiveness / high-confidence / low-warmth `CAP_MIND` GM chooses a cheaper `ANCHOR` package more often than a low-aggressiveness / high-warmth `MEASURED` GM on the same candidate set.
  - `test_recheck_keeps_unilateral_proposed_offer_when_initiator_still_accepts`: verifies `recheck_open` does not expire a `PROPOSED` engine offer merely because `db < theta_b`, while still expiring it if `da < theta_a`.
- `tests/test_gm_trade_shapes.py`:
  - `test_ladder_allows_one_round_overpay_for_rival_or_conference_contender`: verifies `ladder(p(82), SEASON)` is `(4, 5)` by default, becomes `(3, 5)` when `rival_step=1`, and `classify_shape` accepts an R3 rental for an 82 OVR player when `rival_tax(b, seller, buyer) >= 0.25` while rejecting R3 between untaxed non-rivals.
- `tests/test_gm_talks.py`:
  - `test_opening_options_include_pick_downgrade_anchor_for_single_pick_rental`: verifies `_options_for_opening` produces a legal `ANCHOR` option (e.g., R5 instead of R4) on a 1-for-1 rental when the ladder spans two rounds.
  - `test_unilateral_lowball_triggers_counter_or_stall_instead_of_auto_accept`: verifies that when Team 1 opens with an `ANCHOR` offer below Team 2's floor (or into a rivalry/contender tax), Turn 2 does not `ACCEPT`—Team 2 counters (`MEET` / `HOLD`) or rejects, leading to either a multi-turn compromise (`AGREED` on a higher counter) or a `STALLED` / `WALKED_AWAY` talk.
- `tests/test_gm_negotiation.py`:
  - `test_rival_contender_counter_demands_higher_package`: verifies `counter_options` asks for a bigger package (including a 1-round higher pick via the rival ladder step) when the offering team is a division rival or fellow conference contender than when the offering team is an out-of-conference rebuilder.

Run with `pytest -m "not integration" tests/test_gm_trade_engine.py tests/test_gm_trade_shapes.py tests/test_gm_talks.py tests/test_gm_negotiation.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Unilateral Search, Personality Opening Posture & Rivalry-Contention Tax

- **Owns:** `engines/gm_trade_engine.py`, `tests/test_gm_trade_engine.py`, `tests/test_gm_trade_shapes.py`
- **Reads:** `engines/gm_trade_engine.py`, `engines/trade_value.py`, `tests/test_gm_trade_engine.py`, `tests/test_gm_trade_shapes.py`
- **Depends on:** none
- **Done when:** `contender_status`, the rivalry $\times$ contention `rival_tax` matrix, the contending-division starter check in `division_check`, `rival_ladder_step` and `ladder(p, season, rival_step=0)`, `public_seller_value`, `persona_target_weight`, `initiator_accepts`, `evaluate_initiator`, `opening_posture`, `choose_opening`, `choose_shaped_opening`, and the unilateral `run_week` / `recheck_open` search flow are implemented in `engines/gm_trade_engine.py`, and all existing and new tests in `tests/test_gm_trade_engine.py` and `tests/test_gm_trade_shapes.py` pass.
- **Verify:** `pytest -m "not integration" tests/test_gm_trade_engine.py tests/test_gm_trade_shapes.py` (all pass).

### U2: Multi-Turn Talk Opening Anchors & Rivalry Counter Flow

- **Owns:** `engines/gm_talks.py`, `engines/gm_negotiation.py`, `tests/test_gm_talks.py`, `tests/test_gm_negotiation.py`
- **Reads:** `engines/gm_talks.py`, `engines/gm_negotiation.py`, `engines/gm_trade_engine.py`, `tests/test_gm_talks.py`, `tests/test_gm_negotiation.py`
- **Depends on:** U1
- **Done when:** `_options_for_opening` in `engines/gm_talks.py` supports outgoing pick-downgrade swaps and `deal.get("opening_options")` from U1's unilateral search, `open_talk` opens with the initiator's personality-selected offer, `gm_negotiation.counter_options` and `judge` evaluate incoming offers with U1's rivalry $\times$ contention tax and ladder step, and all existing and new tests in `tests/test_gm_talks.py` and `tests/test_gm_negotiation.py` pass.
- **Verify:** `pytest -m "not integration" tests/test_gm_talks.py tests/test_gm_negotiation.py` (all pass).

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1 | — | Builds the unilateral search, personality opening posture, rivalry $\times$ contention tax matrix, and ladder step in `engines/gm_trade_engine.py`. |
| 2 | U2 | U1 | Wires `engines/gm_talks.py` and `engines/gm_negotiation.py` against U1's unilateral offers and rivalry-aware ladder. |

**File ownership check:**

| File | Unit |
|---|---|
| `engines/gm_negotiation.py` | U2 |
| `engines/gm_talks.py` | U2 |
| `engines/gm_trade_engine.py` | U1 |
| `tests/test_gm_negotiation.py` | U2 |
| `tests/test_gm_talks.py` | U2 |
| `tests/test_gm_trade_engine.py` | U1 |
| `tests/test_gm_trade_shapes.py` | U1 |

## 7. Spec changes when built

- `PROJECT_SPEC.md` §1.1 Repository Layout (`engines/gm_trade_engine.py`, `engines/gm_talks.py`): note unilateral search, personality-driven opening posture, and rivalry $\times$ contention tax.
- `PROJECT_SPEC.md` §2.17 (`Proposal engine`, `In-season trade shapes`, `Search`, `Legality`, `Acceptance`, `Negotiation`, `Talks`, `Tests`): document `contender_status`, the `rival_tax` contention matrix, the in-season contending-division 84+ OVR rule, `ladder(..., rival_step)`, `public_seller_value`, `persona_target_weight`, `initiator_accepts`, `opening_posture` / `choose_opening`, and pick-downgrade anchors in `_options_for_opening`.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | When two teams in the **same division** are both in the playoff race (`CONTEND` or `.500+` `RETOOL`) during the season, where should the hard OVR cutoff sit for refusing to trade a starter inside the division? | **84 OVR (`DIV_CONTEND_MAX_OVR = 84`)** in season (88+ remains blocked in the division for all teams year-round, and 75% `DIV_CONTEND_TAX` applies to any depth/lower starter below 84). |
| Q2 | When a CPU GM initiates a trade targeting a **user-controlled team**, should it also use the CPU GM's unilateral opening offer (so shark GMs sometimes send you lowballs in the Inbox that you can counter or reject)? | **Yes** — store the CPU initiator's unilateral offer as `PROPOSED` (surfaced in your `GM_TALK` Inbox thread), and have `recheck_open` only check that the offering CPU GM still stands behind it. |

---

## 9. Impact on `CLOSE_OUT_CHECKLIST.md` & Hand-Off to Checklist Agent

This plan was created after the initial **`P1-25`** replay on `madden_tt_replay` (`docs/CLOSE_OUT_CHECKLIST.md` §3.4) exposed that 7 of 7 CPU talks in weeks 4–8 resolved as `AGREED` in exactly 2 turns (`0%` stall share, `0` carried talks, `0` leaks) because `gm_trade_engine` only sent pre-accepted bilateral deals into `gm_talks`.

### 9.1 Checklist items affected

| Checklist ID | Section | How this plan affects it | Action required when checklist agent resumes |
|---|---|---|---|
| **`P1-25`** | §3.4 (`docs/CLOSE_OUT_CHECKLIST.md:148, 185-186`) | **Blocked on this plan for final sign-off.** The existing `verify_output/p1_25/transcripts/` sample reflects the pre-agreed 2-turn behavior (`7 AGREED, 0 STALLED/WALKED_AWAY, 0 carried, 0 leaks`). Once U1 and U2 land, `--replay-week` will produce `ANCHOR` and `FIRM` openings, multi-turn `COUNTER` exchanges, real `STALLED` / `WALKED_AWAY` / `CARRIED` outcomes, and insider leaks. | Re-run weeks 4–8 on `madden_tt_replay` (after resetting `gm_trade_negotiations` / `gm_trade_proposals` on the scratch copy), regenerate `verify_output/p1_25/transcripts/` with 3 multi-turn transcripts (including a counter/anchor and a stall or leak where present), record the new convergence/stall rate in `P1-25` and `PROJECT_SPEC.md` §2.17, and present the 3 transcripts to JR for sign-off. |
| **`P3-07` & §5.3** | §5.2–5.3 (`docs/CLOSE_OUT_CHECKLIST.md:239, 246-247, 264`) | `gm_trade_engine` weekly pacing (`p_exec`) and `--talk-stall` (`TALK_STALL = 0.25`) feed the P0-2 / P3-06 trade-volume gate (`5–21` in-season trades, `45–84` offseason trades, `<= 3` blockbusters/yr). With unilateral offers, live `gm_talks` runs experience organic stalls while `--simulate-season` uses `--talk-stall` aligned to the measured P1-25 stall share. | When running `P3-07` (`compare_decisions`) and checking §5.3 constants, verify that `TALK_STALL` matches the post-U2 measured stall share from `P1-25` and that in-season trade volume stays inside `5–21`. |
| **`P5-07` & `P5-14`** | §7.2 (`docs/CLOSE_OUT_CHECKLIST.md:356, 363`) | `request_deals` (for `trade_requests`) and the cumulative shadow run with all 20 personality features active now shop players unilaterally and evaluate division/conference contender taxes through multi-turn talks. | No extra setup needed; `P5-07` and `P5-14` shadow runs will naturally exercise unilateral shopping and rivalry-taxed counters. |
| **`P8-10`** | §10.2 (`docs/CLOSE_OUT_CHECKLIST.md:423`) | Trade Machine + League Talks visual check (`PUBLISHED` transcripts and `GM_TALK` Inbox threads). CPU inbound offers to user teams (`D4`) and published CPU-CPU transcripts will show personality-driven `ANCHOR` / `FIRM` openings and multi-turn counters. | Verify during `P8-10` that both `AGREED` and `STALLED`/`WALKED_AWAY` transcripts render cleanly in League Talks. |

### 9.2 Resumption checklist for the Close-Out Checklist Agent

After `/execute-plan docs/not_started/UNILATERAL_TRADE_ENGINE_PLAN.md` finishes and moves this plan to `docs/done/`:
1. **Reset & re-run `P1-25` on `madden_tt_replay`:**
   - Truncate `gm_trade_negotiations`, `gm_trade_proposals`, and `agent_loop_decisions` (or re-clone `madden_tt_replay` from `madden_2036` and apply migrations).
   - Run `python3 -m engines.gm_talks --dsn postgresql://.../madden_tt_replay --season 2036 --week <w> --replay-week` across weeks 4–8.
   - Save 3 representative transcripts to `verify_output/p1_25/transcripts/` and update the `P1-25` row in `docs/CLOSE_OUT_CHECKLIST.md` (and `PROJECT_SPEC.md` §2.17 `Talks` note) with the new agreed / stalled / carried / leak counts.
2. **Hand the 3 updated transcripts to JR** to close `P1-25` (`◐` → `☑`) and continue the remaining `CLOSE_OUT_CHECKLIST.md` phases.

---

## 10. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
