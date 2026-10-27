# Step 6: Draft-day trades in Rounds 2–7 (sub-plan)

Written 2026-10-27 for JR. This plan extends step 4's Draft Room (`04_SCOUTING_AND_DRAFT_PLAN.md`, PROJECT_SPEC §2.18) and step 3's draft-day trades (`run_draft_day`, PROJECT_SPEC §2.17). Today, trade proposals stop at pick 32, and Madden simulates Rounds 2–7 with no trades at all. Real drafts have roughly 30 trades after Round 1, so this plan adds Mu-generated trades at the start of each later round.

**Status:** plan only, nothing built yet.

**Delivers:**
- A **Start Round N** step in the Draft Room for N = 2..7. It proposes the round's pick trades: teams moving up for a player they don't expect to last to their own pick.
- Madden steps for each round: give the trading teams user control, make the trades, confirm them, hand the teams back to the CPU, and sim to the end of the round.
- Trade volume of about 5 a round (about 30 in Rounds 2–7), with its own budget separate from the offseason's 15–20 trades.
- Proposals that never name the player a team is moving up for. The story is told after the draft, from the real Draft Recap.

**JR's decisions (2026-10-27):**
- **D1:** JR sims one round at a time in Madden. He can trade between CPU teams by putting them under user control first.
- **D2:** Between rounds, Mu doesn't capture who was taken. It estimates the pool internally and never names a target in a proposal or its rationale.
- **D3:** The trades happen at the start of each round, not pick by pick.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `engines/gm_trade_engine.py:1607` `run_draft_day` proposes at most one pick-for-pick trade-up for the pick on the clock. It returns early when `overall > DRAFT_DAY_PICKS` (32, `:91`). | The deal search, the `packages(..., "DRAFT", extras=False)` filter to picks only, `evaluate`/`choose` and `store(trigger="DRAFT")` all carry over. The new code is a round-level entry point next to it, not a rewrite. |
| S2 | `engines/draft_room.py:287` `clock_trades` calls it once per Round 1 pick. Proposals are capped at `MAX_DRAFT_DAY_PROPOSALS = 3` a draft (`:28`). Buyer premiums come from `premiums(boards, cons, available, owner)` (`:77`): `1 + 0.05 ×` (the buyer's board grade for its best available player − the consensus's best), within 1.0–1.6. | Rounds 2–7 reuse `premiums`. They need their own cap, and the Round 1 cap stays as it is unless JR raises it (Q1). |
| S3 | `draft_day_p` (`:1599`) draws from `offseason_budget` (`:259`), whose target is 15–20 trades for the **whole offseason**, draft-day trades included. `MAX_OPEN = 6` (`:31`) caps open proposals league-wide. | About 30 later-round trades can't come out of that budget, and a batch of 5–8 a round would hit `MAX_OPEN`. Rounds 2–7 get their own target and cap, and don't count toward the offseason target. |
| S4 | `expire_stale` (`:175`) expires a `DRAFT_DAY` proposal once a later pick is on the clock (flag `pick:<n>`). | Round proposals carry `round:<N>` as well as `pick:<overall>`. They expire when Round N+1 starts, or when the draft results are loaded. |
| S5 | `draft_picks` holds owners by `(draft_season, round, original_team_id)`, with `overall_pick` filled when the order is locked. `pipeline/trade_moves.py` moves `owner_team_id` when JR confirms a trade. The draft-results extract reads owners by overall pick when it runs (`pipeline/extract_draft_results.py`, commit 2551bea). | Confirmed later-round trades already reach the drafted-team credit. Nothing changes downstream. |
| S6 | In 2036, Madden's CPU picks tracked its own board closely: corr(`draft_prospects.projected_pick`, actual pick) = 0.93 (04 plan, S3). | Mu's estimate of who's gone after a simmed round uses Madden's `projected_pick` order, not the consensus. That's the best available guess at what the CPU did. |
| S7 | Round 1 picks are recorded exactly in `scout_draft_picks` as JR makes them. | The pool estimate is exact for Round 1 and only estimated from Round 2 on. |
| S8 | `engines/draft_room.py` `AFTER_DRAFT` tells JR to hand every team back to the CPU and "sim the rest of the draft". | That text changes: sim to the end of Round 2, then come back for Start Round 3. |

---

## 1. The round flow (Draft Room)

After pick 32 is recorded, the Draft Room shows **Start Round 2** in place of "sim the rest". For each N in 2..7:

1. **Start Round N** (`draft_room.start_round(cur, draft_season, N)`):
   - It expires every open round N−1 proposal ("the round was simmed"). For N = 2, those are the Round 1 draft-day proposals.
   - It estimates the pool (§2), finds buyers and sellers (§3), and stores up to the round's proposals (§4).
   - It records that the round started (§5), so a second click doesn't propose again. The same rule `clock_trades` uses: one run per unit.
2. **JR reviews** the round's proposals in Front Office: approve, reject, or edit as usual.
3. **Madden steps** shown on the round card, for each approved trade:
   1. Options > User Teams: turn on user control for both teams.
   2. Make the trade in Madden.
   3. Confirm it in Front Office ("Made it"). The picks move in `draft_picks`.
   4. Options > User Teams: hand both teams back to the CPU, except CAR and IND.
4. **Sim to the end of Round N** in Madden, then Start Round N+1.

After Round 7, the card shows the existing "Draft results" step (`--mode draft-results`), then validation.

A trade JR makes in Madden that Mu didn't propose still goes through "Record a trade" before the extract runs. That's unchanged.

---

## 2. The pool estimate (internal, never shown to writers)

At the start of Round N, the players still available are estimated as:
- the class,
- minus the 32 Round 1 picks JR recorded (exact),
- minus the next 32 × (N − 2) prospects in `projected_pick` order (S6).

The estimate drives two things only: each team's **target** (its board's best player in the estimated pool) and that target's **projected slot** (its rank by `projected_pick` within the pool, mapped onto the round's order). Nothing shown to JR or writers names a prospect from this estimate (D2).

---

## 3. Who trades

`round_trade_candidates(slots, boards, pool, projected)` is pure. A team that owns a pick in Round N is a **buyer** when:
- its target's projected slot is at least `FALL_GAP` (3) picks before the buyer's own Round N slot, and
- it has a pick to pay with: its own Round N pick, plus picks from later rounds of this draft or Day-3 picks next year.

The **seller** is the owner of the slot just ahead of the target's projected slot. The seller has to value that pick less than the buyer does, with the buyer's premium from `premiums` (S2). The deal search is `run_draft_day`'s, with `b["on_clock"]` set to the seller's slot, keeping only pick-for-pick packages (S1).

Exclusions, all as today: teams not eligible to trade (`eligible_team`), and a pick already in an open proposal. A trade involving CAR or IND gets the `advisory:<ABBR>` flag, and JR decides whether to make it.

---

## 4. Volume and pacing

- **Target:** `ROUND_TRADE_TARGET` = 5 executed trades a round, Rounds 2–7, about 30 a draft. This budget is separate from the offseason's 15–20 (S3).
- **Proposals per round:** a Poisson draw with mean 5 ÷ JR's draft-day approval rate (`p_exec` over draft-day proposals, default 0.6 until there's history), capped at `ROUND_MAX_PROPOSALS` = 8. Seeded per `(season, round)`, so a re-run proposes the same trades.
- **One proposal per buyer per round.** Candidates are ranked by how far ahead of the buyer's pick the target is projected to go, then by the trade's surplus value. `choose` picks among them as it does today.
- **League-wide cap:** round proposals don't count against `MAX_OPEN`, and expire at the end of their round.

---

## 5. Data and API

- **Schema:** one small table, `draft_round_starts` (`draft_season`, `round`, `started_at`, `proposals` int, PK `(draft_season, round)`), so a round with no proposals still counts as started. Migration `db/migrations/<next date>_draft_round_starts.sql` (check the folder for the next free name at build time; the last is `2027_01_01_record_book_implied.sql`). No other schema changes: proposals use `gm_trade_proposals` with `trade_window = 'DRAFT_DAY'`, `trigger_kind = 'DRAFT'` and flags `round:<N>` and `pick:<overall>`.
- **Engine:**
  - `gm_trade_engine.run_draft_round(cur, season, draft_season, round, candidates, premiums, dry_run, llm)`, next to `run_draft_day`.
  - `expire_stale` learns the `round:` flag.
  - CLI: `python3 -m engines.gm_trade_engine --draft-round N [--dry-run]`.
- **Draft Room:**
  - `start_round` and the round card in `state` (current round, its proposals, the Madden steps).
  - `AFTER_DRAFT` becomes the per-round text (S8).
  - CLI `python3 -m engines.draft_room start-round N`.
- **API:**
  - `POST /api/debug/draft-room/rounds/{n}/start`.
  - `GET /api/debug/draft-room` gains `round` and `round_proposals`.
- **Frontend:** the Draft Room card after pick 32 shows Start Round N, the round's proposals with links into Front Office, and the steps.

---

## 6. Rationale text

Proposals never name the target (D2). The rationale's fact sheet for a round trade has:
- both teams, the picks moving, the slots, and the value on each side;
- the buyer's position need from its GM's needs.

It has no prospect. Template example: "The Bengals move up eleven spots in Round 3, worried the edge rusher they want won't last to 87." That works only when the position comes from team needs, not from a prospect. The LLM validator rejects any text that contains a name from the class (`draft_prospects.full_name` for the class), and the template stands in.

After the draft-results load, the newsroom can tell the real story: the player each traded pick was used on, read from the recap. That's a follow-up for the Trade Grades and draft coverage (05 plan, Phase 8), not part of this build.

---

## 7. Tests (synthetic, no live DB)

- `round_trade_candidates`:
  - a buyer whose target goes 3+ picks ahead is found;
  - one whose target lasts to its pick isn't;
  - a seller who values the pick more than the buyer is skipped.
- The pool estimate: Round 1 recorded picks are removed exactly, later rounds by `projected_pick`.
- Pacing:
  - a seeded round gives the same proposals on a re-run;
  - never more than `ROUND_MAX_PROPOSALS`;
  - one proposal per buyer.
- Expiry: round N proposals expire when round N+1 starts; Round 1 proposals expire at Start Round 2.
- `start_round` twice proposes once.
- The no-names validator rejects a rationale containing a class name.
- Over 1,000 seeded drafts, executed trades (at the default approval rate) land at 25–35 for Rounds 2–7, like the scouting calibration's band test.

The Draft Room tests write nothing to `madden`. Never run the napper tests wholesale.

---

## 8. Spec updates when built

- §2.17: draft-day trades in Rounds 2–7, the separate budget, the `round:` flag.
- §2.18: the round flow, `start_round`, the per-round Madden steps.
- §3.3f: `gm_trade_proposals` flags.
- §3.3g: `draft_round_starts`.
- The API table.
- The Repository Layout entry for this plan.

---

## 9. Open questions for JR

- **Q1:** Should Round 1's cap of 3 proposals a draft go up so the whole draft feels like the real one? Roughly 5–8 Round 1 trades a year is common.
- **Q2:** May round trades include next year's picks as payment, or only picks in this draft? This plan says both, with next year limited to Rounds 4–7.
- **Q3:** Trade-downs: should a team with no target worth its slot be able to shop the pick on its own, or do trades only start from a buyer moving up, as planned?
- **Q4:** Should an approved trade that JR can't make in Madden (for example, the CPU won't accept it even under user control) be marked rejected, or just left to expire with the round?
