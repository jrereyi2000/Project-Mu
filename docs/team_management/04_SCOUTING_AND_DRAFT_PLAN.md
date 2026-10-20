# Step 4: Scouting and draft (sub-plan)

Written 2026-10-20 for [`TEAM_MANAGEMENT_PLAN.md`](../TEAM_MANAGEMENT_PLAN.md) step 4. It builds on JR's starting design (master §2.5: hidden `V`, noisy weekly consensus `C_w`, per-team `T`, `q_team` from track record, 20–30% poor Round 1 picks) and on decision D4: GM agents choose picks 1–32 live, one at a time, JR makes each pick in Madden, and Madden simulates Rounds 2–7. Step 3's interfaces are consumed as they are (`vw_gm_profiles`, `gm_team_seasons.fo_quality*`, `draft_picks`, `run_draft_day`).

**Delivers:** a hidden true value for every prospect; a weekly consensus board that moves with stored, invented college events (games, storylines, all-star games, then the combine, pro days and visits); 32 private team boards; `fo_quality` (`q_team`) from draft track records, bootstrapped from the 2036 draft; a `--calibrate` harness and a test that locks the poor-pick band; weekly risers and fallers for step 5 Phase 9; a `#debug` **Draft Room** where JR records each Round 1 pick as he makes it; post-draft validation against the Draft Recap scan; and feedback into `fo_quality` once the rookies have real ratings.

**Status (2026-10-20): Phases 1–8 built and on main** (PROJECT_SPEC §2.18, §3.3g). The migration `2026_12_12_scouting.sql` is **not applied to live** (validated twice on a throwaway copy, then dropped). As built, deviating from or settling this plan:
- **Consensus seed (Q1):** the first error is `0.75 × gap + σ·z`, where gap = the V at Madden's PICK rank minus the prospect's V (sd 5.3 for 2037). It decays by (1 − λ) a step, λ = 0.05, so about 31% is left on draft day. The noise part's sd runs 3.0 → 1.5 (`SIGMA_0`, `SIGMA_DRAFT`). The quiet-week threshold scales with the step's noise.
- **Team boards:** a Round 1 pick must sit inside the consensus top 80 (`REACH_LIMIT`, K1). Six visits per team at each of steps 25 and 26.
- **Calibration frozen 2026-10-20** (1000 drafts): `SIGMA_BASE` 5.5, `RHO` 0.5, `SIGMA_RES` 2.0, `PHI` 3.0. Poor picks: 24.3% under `live`, 25.2% under `spread` (q ≤ 0.85: 13.0%; ≥ 1.15: 34.5%).
- **Bootstrap:** 2036 q runs 0.93–1.09 (dry run on live). CAR and IND hold at 1.00.
- **Draft-day premiums:** `1 + 0.05 × (the buyer's best grade − the consensus's best)`, within 1.0–1.6. At most 3 proposals a draft.
- **Writers:** `get_scouted_draft_board` and `get_mock_draft` dropped OVR (Q6). The step 5 Phase 9 data seam is built: `get_draft_risers_fallers` and `get_team_draft_record`. The Phase 9 posts and directive lines still wait for JR's text.

**Build state (live DB, read-only SELECTs, 2026-10-20):** `league_state` = (2036, 4). `draft_prospects` holds the **2037 class: 388 rows** (32 a round in Rounds 1–7 except 31 in Round 4, 165 UDFA). 387 have an OVR (one blank, `BEN MULLIGAN`, MIKE, pick 149). Age, college and height/weight are all filled. `archetype` and `notes` are empty on every row. In the top 64, OVR runs 61–80 (mean 69.5, sd 4.2) and age 21–24. The 2036 class (389 rows, 224 with `actual_pick`, 194 linked to players) is the only draft history. `draft_picks` has 224 rows each for 2037–2039, with `overall_pick` NULL. The main session is applying `2026_12_11_draft_pick_owners.sql` (JR's 24 CPU-era pick trades) right now.

---

## 0. Survey (code and DB facts that set the scope)

| # | Fact (file:line / DB) | Consequence for step 4 |
|---|---|---|
| S1 | `pipeline/draft_board.py:23` `write_board` **deletes** the class (`:26`) and re-inserts it, so `draft_prospects.id` changes on every board re-scan, and `mock_draft_picks` cascades away. | Scouting rows can't hang off `draft_prospects.id`. They key on a stable **`prospect_key`** = `draft_season` + `_name_key(full_name)` (`:19`) + position group (`:14`), and keep a soft `prospect_id` that's re-linked after each board load. |
| S2 | The board has only `projected_pick/round/round_pick`, name, position, college, `height_in`, `weight`, `age`, `ovr`, plus `archetype`/`notes` (always NULL). There's no dev trait and no ratings beyond OVR (DB). | `V` uses OVR, position and age only. Dev goes into `V` only if console fact K2 says the board shows it. Invented narrative can't contradict anything beyond these fields. |
| S3 | Madden's own board order (`projected_pick`) **diverges from OVR**: corr(OVR, projected_pick) = −0.56 for 2037 (the top OVR, `JAMAR BODDEN` CB 80, sits at 45). In 2036 the CPU's actual picks tracked that board closely: corr(projected, actual) = 0.93 (DB). | Madden's board encodes something OVR doesn't (likely hidden potential plus Madden's own position values), and the CPU drafts Rounds 2–7 by it. Whether `V` blends it in is open question Q1. |
| S4 | 2036 rookies (`years_of_experience` 0) were rated about +1 to +3 OVR above their board OVR when the 2036 roster scan ran (R1 75.0 vs 71.5 on the linked rows). Their `dev_trait`: 147 Normal, 1 Star, 47 blank (DB). | The board OVR may be a scouted value, or rookies may progress before Week 1 (K1). A hit score must compare a rookie with other rookies at the same age, never with his board OVR. |
| S5 | `draft_prospects.drafted_team_id` follows the draft order (pick 1 = PHI, which went 3–14 in 2035), but only **72 of 182** linked 2036 draftees were on that team in the 2036 roster scan (pick 1 VOGEL: PHI → BUF) (DB). | CPU-era trades, or a recap team column that reads the original slot owner. The `fo_quality` bootstrap has to choose which team gets credit (Q2, K7). |
| S6 | `player_seasons` exists only for 2035 and 2036, so the 2036 draftees have **0 seasons** of development. There is no earlier draft history (DB). | The bootstrap rests on one draft scored at year 0. It's shrunk hard, and q starts in roughly 0.85–1.15. The full 3-draft window fills by 2039. |
| S7 | `engines/mock_draft_engine.py:46` `POSITIONAL_PREMIUM` (QB 1.25 … RB 0.80, specialists 0.20), `:117` `evaluate_fit` (BPA = (OVR − 70)·4 + slot delta, need from `get_team_draft_needs`, a QB rule), `:168` `run_mock`. `newsroom/db_tools.py:1949` `get_mock_draft` saves one mock per week to `mock_draft_picks`, and `:1989` `get_scouted_draft_board` gives writers the board **including OVR**. | The mock stays the **media mock** (the public's guess) with the same API and table. Its BPA term switches from OVR to the consensus grade. Whether writers keep seeing raw OVR is Q6. |
| S8 | `engines/draft_order.py:59` `compute_draft_order(teams, games)` returns `round_1_order` by original team. `draft_picks` (PK `(draft_season, round, original_team_id)`, `owner_team_id`, `overall_pick` NULL, `used`) holds ownership (spec §3.3f). | The Draft Room maps the order through `draft_picks` and fills `overall_pick` for 2037 at order lock. |
| S9 | `engines/gm_trade_engine.py:1047` `run_draft_day(cur, season, pick)` already proposes pick-for-pick trade-ups per pick on the clock. It uses a flat `DRAFT_DAY_PREMIUM = 1.35` (`:63`, whose comment says "step 4's board grade replaces this"). `OFFSEASON_STAGES` (`:58`) = post-Super Bowl, re-signing, free agency, **draft (stage 4)**, preseason. | Draft-day trades are in scope through the existing flow. Step 4 only swaps the premium for the buyer's team-board grade, and calls `run_draft_day` as each pick comes on the clock. |
| S10 | `pipeline/weekly_capture.py:351` `step_gm` runs after `step_engines` in weekly and playoff runs (`:804`) and for each offseason stage (`:758`). `:410` `step_preseason_engines` ends with `step_gm_rollover` (`:443`). | Scouting is **Step 7c**, right after `step_gm` and on the same triggers (needs are fresh, articles come after). The `fo_quality` recompute hangs off the preseason engines, after the rollover. |
| S11 | `engines/gm_rollover.py:136` carries `fo_quality` forward "until step 4 writes it". `gm_team_seasons` has `fo_quality NUMERIC(4,2) DEFAULT 1.00 CHECK 0.5–1.6`, `fo_quality_inputs`, `fo_quality_at`. 32 rows exist for 2036. | Step 4 writes these three columns and nothing else in step 3's tables (apart from `draft_picks.overall_pick`/`used`). |
| S12 | `engines/front_office.py:48` `get_gm` gives `position_bias`, `youth_bias`, `risk_appetite`, `direction`, `needs`, `user_controlled`. `engines/gm_llm.py:46` `make_llm(label)` is the metered Gemini seam with a None fallback. `engines/gm_rationale.py:167` `validate_rationale(text, sheet)`. | `fit` and `bias` come from `get_gm`. Text goes through `make_llm`, with a validator built the same way and a template fallback. |
| S13 | `pipeline/draft_results.py:233` `resolve_and_load_picks` writes `actual_*`/`drafted_team_id` for the scanned recap (resetting earlier actuals at `:348`). `madden_capture.py:2494` captures the recap, run as `--mode draft-results`. | The recap scan stays the **only writer of `actual_*`**. Live picks go into their own table and are validated against it. |
| S14 | `engines/trade_value.py:160` `fit_pick_curve(draft_rows, market)` fits expected OVR by pick from the 2036 draft. | The hit score's "expected OVR for the slot" reuses this fit, refitted as more drafts land. |
| S15 | `newsroom/run_context.py:94` `cutoff_week` / `:106` `is_visible_week` hide `week >= N`, but the injury report reads `week <= cutoff` (step 5 S3). `OFFSEASON_EVENT_ORDER` (`:71`) ends at `draft: 4`. | A board snapshot made in the run for Week N is visible to Week N articles (the injury-report rule). Offseason snapshots carry their stage for the event cascade. |
| S16 | The last migration file is `db/migrations/2026_12_11_draft_pick_owners.sql` (uncommitted, main session). Step 5 adds no schema. | Step 4 takes **`2026_12_12_scouting.sql`**. Re-check the folder at build time. |
| S17 | 156 distinct colleges appear in `draft_prospects` (DB). | A static `data/scouting/colleges.json` (school → conference, nickname) keeps invented schedules and opponents inside the right conference. |

---

## 1. Console facts to check (JR at the console; don't build on these unverified)

| # | Question | Blocks |
|---|---|---|
| K1 | Is the OVR on **EDIT DRAFT CLASS** the prospect's true rating, or a scouted one? Does it (or the PICK column) change during the season, so the board should be re-scanned before the draft? | `V`'s source; whether there's a pre-draft board re-scan (Phase 1, Phase 6) |
| K2 | Does any prospect screen show the **dev trait** (or a hidden-dev icon)? Is a rookie's dev revealed at the draft, or only after games? | Dev in `V` (S2); the hit score's dev term |
| K3 | Does Madden's **draft order screen** list all 32 Round 1 owners after the CPU-era pick trades, and does it update live as each pick is made? Does its order match `compute_draft_order` (tie-breaks)? | Order lock (Phase 7) |
| K4 | Can JR switch user control **between picks** during the draft, or must every Round 1 owner be user-controlled before the draft starts? After pick 32, can he switch them all back and sim Rounds 2–7 so the CPU picks for them? | The Draft Room's step text (Phase 7) |
| K5 | Can picks be **traded during the draft** with CPU trades off (step 3 C2: JR turns them on only to push a trade through)? | Draft-day trades (Phase 7) |
| K6 | In Madden's draft room, can JR find a prospect by name and position quickly, and is there a pick clock? | The Draft Room's pace and search |
| K7 | Does the **Draft Recap** show the team that made the pick, or the slot's original owner? (S5: 110 of 182 2036 draftees on another team.) | `fo_quality` credit (Q2), recap validation |

---

### 1.1 JR's answers (2026-10-20)
- **K1:** EDIT DRAFT CLASS shows the **true** rating. Neither the OVR nor Madden's PICK column changes during the season, so some high-OVR prospects carry low projections.
  - Madden's sim uses its projections when it autodrafts. In the 2036 draft, Bobby Lowell (a top-20 OVR) was projected undrafted and went 221st, and Oscar Espinosa was projected R7P32 and went 196th. So sleepers exist, but they still go earlier than projected.
  - Mu only picks Round 1, and a prospect Madden projects very late shouldn't realistically go in Round 1.
  - **No board re-scan is needed** (Q7 closed).
- **K2:** Prospect screens show a dev trait only if JR clicks into each player's edit screen, which isn't worth it, so `V` has **no dev term** before the draft.
  - The **roster card shows the dev trait** (e.g. "Normal", "Superstar X-Factor | Omaha"), so a rookie's dev is known from the next Season scan's roster capture. The hit score's dev term uses that.
  - Checked 2026-10-20: `player_seasons.dev_trait` is already filled for 1,658 of 1,750 rows in 2036, from the 2026-10-17 roster capture. The gaps: 47 cards with no dev shown, 12 in-season signings, and about 33 players not on the scan.
  - **Madden hides a rookie's dev until he has played 500 snaps** (verified 2026-10-20). Until then the card reads e.g. "Hidden | 184/500 Snaps to reveal". The 47 blank cards above are all 2036 rookies (experience 0, `cards.csv` flag `missing_dev`; 148 of 195 rookies already show a dev). So a rookie's dev may stay unknown for a season or more. Since main `aa7dc14`, the card parser stores this as `dev_trait = 'Hidden'`, and the 47 live rookies are set to it. JR's reason: Star and Superstar rookies often start out hidden. The hit score treats `'Hidden'` as **unrevealed**, not Normal and not missing data: it scores on OVR alone and adds the dev bonus once a later roster scan reveals the trait (§2.4). A NULL dev (45 rows in 2036, players the start-of-season scan never saw, such as in-season signings) also gets no dev term.
- **K3:** The draft order screen lists Round 1 owners after the CPU-era trades. It shows them only where the slot is known. Mid-season, the order isn't set yet.
- **K4:** JR turns on user control for **every Round 1 pick owner before the draft**, makes each of the 32 picks by switching teams, then sims Rounds 2–7.
- **K5:** With every team user-controlled, JR can turn CPU trades on during the draft without any CPU–CPU trades happening. Draft-day trades are possible **if the GM agents propose them** (open question 8).
- **K6:** Prospects can be found quickly, and the pick clock can be turned off.
- **K7:** The Draft Recap shows the **team that made the pick**, so `fo_quality` credits the drafting team (Q2 closed: `drafted`).

## 2. Design

### 2.1 True value `V` (hidden; `engines/scouting_value.py`, pure)
- `V = OVR + PV[group] + AGE[age] (+ DEV[dev] if K2) (+ κ·board_prior, Q1)`, in OVR points.
  - `PV`: QB +5, EDGE +3, LT +3, RT +2, CB +2.5, WR +2, DT +1, TE 0, IOL −1, off-ball LB −2, S −2, RB −3, K/P −12. These are additive versions of `POSITIONAL_PREMIUM` (S7), which stays the source of the ordering.
  - `AGE`: 21 +1.5, 22 +0.5, 23 −0.5, 24 −1.5.
  - `board_prior` = the expected `V` at Madden's `projected_pick` (a smoothed rank-to-value curve), so Madden's view of hidden potential counts for something. κ defaults to 0 until Q1 is answered.
  - A blank OVR is valued at `UNRATED_OVR` = 50, as the mock does.
- Sorting by `V` gives the **true rank**. `V` and true rank are stored, but no view, writer tool or `#debug` panel ever shows them, and that includes JR. Only aggregate calibration numbers are printed.
- **Grade scale:** a fixed per-class linear map from `V` to 0–100: the class's #1 ≈ 92, #32 ≈ 80, #64 ≈ 74, #224 ≈ 60. **Round labels** come from consensus rank (1–32 "1st round", 33–64 "2nd", … "UDFA"), with "Top 10" and "Day 3" tags.

### 2.2 Consensus `C_w = V + e_w` (`engines/scouting_engine.py`)
- **Steps.** A step is one scouting run: in-season, `step = league week` (the week about to be played, as `step_gm` uses it); offseason, `step = 23 + stage` (24 post-Super Bowl … 27 draft). The 2037 board starts at **step 4**. Weeks 1–3 aren't backfilled: real weekly runs make the data real.
- **Error process.** `e_0 ~ N(0, σ_0²)`, seeded by `sha256("scout:" + prospect_key)`. Each step: `e_{s+1} = (1 − λ_s)·e_s + ν_s`, with `ν_s ~ N(0, τ_s²)`. `λ_s` and `τ_s` follow a schedule so that `sd(e)` falls smoothly from `σ_0` at step 4 to `σ_draft ≈ 0.5·σ_0` at step 27, and never below it. Starting guesses: `σ_0 = 4.0`, `σ_draft = 2.0` OVR points. `--calibrate` fixes the final values.
- **Events explain the step; they don't cause it.** The engine draws `Δe` first, then picks an event whose sign and size fit it, from that step's calendar:

| Steps | Calendar | Event kinds |
|---|---|---|
| 4–13 | College regular season (byes seeded per school) | `GAME` (a stat line), `STORYLINE` (breakout, rough outing, scheme change, minor injury, character note, award watch) |
| 14 | Conference championships | `GAME` for title-game teams |
| 15–21 | Bowls and the CFP (only for selected teams), declarations | `GAME`, `DECLARE` (age-21 juniors), `AWARD` |
| 22–23 | All-star games (Shrine, Senior Bowl) | `ALL_STAR` (practice reports, game), mostly seniors |
| 24 | Combine | `COMBINE` (measurables, interviews, medicals) |
| 25–26 | Pro days, top-30 visits | `PRO_DAY`; `VISIT` (team-specific, §2.3) |
| 27 | Draft week | `MEDICAL` re-check, `BUZZ`; final board |

  - Below a threshold (`|Δe| < 0.6`), the prospect gets no event that week: a "quiet week".
  - The top ~120 by consensus get a `GAME` line every playing week regardless, so the board has texture.
- **Risers and fallers** = the change in consensus rank and grade from the previous stored step. Each carries the `scout_events` that drove it.
- **Idempotent and seeded.** A `scout_runs (draft_season, step)` row guards each step. A re-run of a stored step does nothing unless `--replace`, which deletes and redraws only that step with the same seed (`random.Random(f"scout:{draft_season}:{step}")` plus per-prospect sub-seeds), so it produces identical output. A step can't run before its predecessor is stored. A missed week is filled by running the missing steps in order before the current one.

### 2.3 Team boards `T = V + e_team + fit + bias`
- `e_team,p = σ_base · q_team · m_team,p · (ρ·ẑ_C,p + √(1−ρ²)·z_team,p)`:
  - `ẑ_C` = the consensus error now, divided by its sd. Front offices read the same tape and media, so the consensus errors bleed into team boards (ρ ≈ 0.5). This extends master §2.5's `e_team` without changing it.
  - `z_team,p` is the team's own AR(1) misread, seeded by `(team, prospect)`. It drifts weekly with the same shrink schedule as `C`, so team boards move too.
  - `m` = 0.75 when the team logged a `VISIT` with the prospect, else 1.
  - The draft-day residual is one more independent draw per (team, prospect) at step 27, of size `σ_res`.
- **fit** = `φ · need(group)`, where `need` is the team's normalized need score for the group from `gm_team_seasons.needs`. Direction tilts it: CONTEND × 1.2 for a current starter gap, REBUILD favours age 21–22.
- **bias** from the persona (`get_gm`): `position_bias[group]` scales `(T − 60)`; `youth_bias · (22 − age) · 0.75`; `risk_appetite` weights the prospect's volatility (the sd of his consensus steps), so risk-takers chase risers.
- The **QB rule** carries over from the mock: a team whose QB1 slot isn't a need takes a large penalty on QBs.
- Stored for each team: the top 100 by `T`, each step. Private: shown only in the Draft Room's on-the-clock panel (Q3).

### 2.4 Front-office quality `q_team` (`engines/fo_quality.py`, pure core)
- **Hit score** per Round 1–2 pick: `h = (OVR_now − E[OVR | slot, years since draft]) / sd_slot + DEV_BONUS` (Star +0.5, Superstar +1.0, X-Factor +1.5, once the dev is known). A hidden dev (`'Hidden'` until 500 snaps, §1.1 K2; or NULL, never scanned) is unknown, not Normal: there's no dev term, and the pick is re-scored at every recompute, so the bonus applies once a roster scan reveals the dev.
  - `E` comes from `trade_value.fit_pick_curve` (S14), refitted on all drafted players in the data by slot and years.
  - Labels: HIT `h ≥ 0.5`, MISS `h ≤ −0.5`, BUST `h ≤ −1.2`, else OK.
  - A player who left the team still counts for the team that drafted him.
- **Team score:** `H = Σ_d w_d Σ h / Σ_d w_d n_d` over the last 3 drafts, with `w` = 1.0, 0.6, 0.35 (newest first). It's shrunk, `H* = H·n/(n + 4)`, and `q = clamp(1 − 0.25·H*, 0.7, 1.4)`. No picks gives `q = 1`.
- **Bootstrap (2036 draft, year 0):** every 2036 R1–R2 draftee linked to a 2036 `player_seasons` row, scored with `years = 0`. Credit goes to the team chosen in Q2. There are 54 linked R1–R2 rows (26 + 28). Unlinked picks are skipped, not scored as misses.
- `fo_quality_inputs` stores every scored pick (season, slot, player, h, label), so step 5 can cite "first-round miss last year", and later "three straight first-round misses".
- **Writes** go only to `gm_team_seasons.fo_quality`, `fo_quality_inputs` and `fo_quality_at`.
- **When:** once now (Phase 3, for 2036). After that, at every Season scan in `step_preseason_engines`, after `step_gm_rollover`, when the new rookies have real ratings and every earlier class has gained a year.

### 2.5 Calibration (`--calibrate`)
- `python3 -m engines.scouting_engine --calibrate [--drafts 1000] [--class 2037] [--q live|spread] [--seed N]`.
  - Each simulated draft draws a fresh error path from step 4 to 27 and all 32 team boards.
  - The 32 picks run in order, using the real 2037 owners and needs, with `--q live` (the stored q values) or `--q spread` (q spread evenly 0.7–1.4 over the teams).
- **Prints:**
  - The poor-pick rate (true rank > slot + 32) overall and by q band (≤ 0.85, 0.85–1.15, ≥ 1.15).
  - Mean |true rank − slot|.
  - The R1 overlap between consensus and the true top 32.
  - sd(e) by step.
  - How many prospects move ≥ 5 ranks a week.
- **Target:** 20–30% overall under both `live` and `spread`, and under `spread` the ≥ 1.15 band's rate is at least 1.5× the ≤ 0.85 band's. While every q is near 1 (the first year), clustering can't show, and that's expected.
- The tuned values (`σ_0`, `σ_draft`, `σ_base`, `ρ`, `σ_res`, `φ`) are frozen as constants with the calibration run's date.

### 2.6 Hallucination policy (D6) for invented college narrative
Everything invented is **stored** in `scout_events`, so every writer sees the same story and later events can't contradict earlier ones. Generators and templates must respect Madden's data:
- **Position:** stat categories by group (QB passing/rushing, EDGE sacks/TFL/pressures, CB targets/PBU/INT, OL sacks allowed/penalties, K/P kicks). Writers name the group in words ("edge rusher" for LEDG/REDG).
- **College:** opponents and title games come only from the school's conference in `colleges.json`. A school not in the map gets generic "non-conference" opponents.
- **Size:** combine height and weight are exactly `height_in` and `weight`. The 40, vertical, broad jump, 3-cone and bench are drawn from position-and-weight priors and tilted by the event's sign. They're labelled as Mu's invented combine.
- **Age:** class year from age (21 junior or redshirt sophomore, 22 senior or redshirt junior, 23–24 senior, redshirt senior or a sixth year). Never "true freshman". Only age-21 prospects get a `DECLARE` event.
- **OVR truth:** a prospect's season production percentile stays within ±25 points of his `V` percentile. No injury can last past the combine or be described as ongoing at the draft, because Madden's rookie will be healthy. A character flag never says he was charged, suspended at draft time, or anything else Madden doesn't model.
- **What stays hidden from text:** `V`, true rank, `e`, `q` and team boards never appear. Writers see consensus grade and rank, round labels, events, and track-record labels.

### 2.7 Gemini (optional, behind the `gm_llm` seam)
- **Numbers are always deterministic.** Stat lines, measurables, grades and picks are computed. The LLM only writes prose.
- **Uses:**
  1. Up to 8 weekly storyline blurbs for the biggest movers (label `scouting_notes`).
  2. A 2–3-sentence rationale for each Round 1 pick in the GM's voice (label `draft_rationale`).
- **Guarded:** a fact sheet per text, and `validate_scout_text(text, sheet)` in the style of `validate_rationale`: every number, name, school, team and measurable must be on the sheet, plus §2.6's banned claims.
- **Fallback:** on a rejection or with no key, a template is used. `text_source` (`LLM`/`TEMPLATE`) is stored, and a stored text is never regenerated by a re-run.

### 2.8 Live draft day (Draft Room, `engines/draft_room.py`)
1. **Lock the order** (after the Super Bowl load, at offseason stage 4):
   - `compute_draft_order` is mapped through `draft_picks` owners, and `overall_pick` is filled for every 2037 round.
   - The card lists the 32 Round 1 owners for JR to check against Madden's order screen (K3). JR can fix a row before locking.
   - Locking runs the step-27 board (draft day) if it isn't stored yet.
2. **On the clock, pick n:**
   - The card shows the owning team, its GM and the step-text for control (K4: which team to switch to user).
   - It shows the **recommendation**, `argmax T + fit + bias` over prospects still available, with the next 3 alternatives by that team's rank, consensus rank, and the rationale.
   - `run_draft_day(cur, 2036, pick)` is called once per pick (it's idempotent by its `pick:n` flag), with the buyer's value of the pick coming from its team-board `T` for the best available player instead of `DRAFT_DAY_PREMIUM`.
   - A trade-up proposal shows inline. JR approves it, makes it, and confirms it through step 3's endpoints, which move `draft_picks.owner_team_id`, and then the card recomputes for the new owner.
   - **Seeded:** the recommendation for (pick, owner, available set) is deterministic, so a reload shows the same player.
3. **JR makes the pick in Madden and records it:**
   - **Made it** records the recommended player (`GM`).
   - **Different player** opens a search by name or position and records `OFF_SCRIPT`.
   - On a team JR controls (`user_controlled`), the GM only advises, and the pick is recorded as `JR_TEAM`.
   - **Undo** is allowed for the last pick only.
   - Recording writes one `scout_draft_picks` row and marks `draft_picks.used` and the prospect `DRAFTED`, in one transaction.
4. **Off-script re-runs:** each pick is decided only when it's on the clock, from the pool still available, so an off-script pick changes every later recommendation by construction. The card also shows the projected rest of Round 1 (`project_remaining`), recomputed after each recorded pick.
5. **After pick 32:** the card shows the steps to switch teams back to CPU and sim to the end (K4). Then JR runs **Draft results** (`--mode draft-results`).
6. **Validation:** after `resolve_and_load_picks`, `draft_room.validate(cur, 2037)` compares picks 1–32 with the recap (`actual_pick`, `drafted_team_id`) and sets `scout_draft_picks.madden_match` (`MATCH`/`MISMATCH` plus the recap prospect). Mismatches are listed on the card and in the load report. They're never auto-edited: the recap is Madden's truth for `actual_*`. The remaining prospects become `DRAFTED` or `UDFA` from the recap.

### 2.9 What replaces or reuses what
- `engines/mock_draft_engine.py` stays the **media mock**. `evaluate_fit` swaps OVR-BPA for the latest visible consensus grade (`(C − 60)·4`, plus the same slot delta), and keeps its needs, premium and QB rule. `get_mock_draft` and `mock_draft_picks` are unchanged, so writers see a public mock that the GMs' real draft can beat or miss.
- `engines/draft_order.py` is reused as it is, plus a new `draft_order_with_owners(cur, draft_season)`.
- `pipeline/draft_board.py` is reused. After `write_board`, the loader calls `scouting_engine.relink(cur, draft_season)`. It re-links `prospect_id` by `prospect_key`, marks prospects that are now missing `WITHDRAWN`, and initializes new ones at the current step with `e` drawn from `σ` at that step.
- The prospect scan stays in the Season scan. Whether to re-scan the board before the draft depends on K1.
- `pipeline/draft_results.py` is unchanged except for the validation call.

### 2.10 Schema (`db/migrations/2026_12_12_scouting.sql`, folded into `init_db.sql`)
- **`scout_prospects`**: `id`, `draft_season`, `prospect_key`, `prospect_id` (FK `draft_prospects` ON DELETE SET NULL), `true_value` NUMERIC(6,2), `true_rank`, `value_inputs` JSONB, `class_year`, `conference`, `first_step`, `status` (`ACTIVE` | `WITHDRAWN` | `DRAFTED` | `UDFA`), `seed`, `created_at`. `UNIQUE (draft_season, prospect_key)`.
- **`scout_runs`**: PK `(draft_season, step)`, `season`, `week`, `stage`, `params` JSONB (the constants and code version), `n_events`, `created_at`.
- **`scout_consensus`**: PK `(draft_season, step, prospect_key)`, `season`, `week`, `stage`, `grade` NUMERIC(5,1), `rank`, `round_label`, `delta_grade`, `delta_rank`, `err` NUMERIC (hidden), `event_ids` INT[].
- **`scout_events`**: `id`, `draft_season`, `step`, `prospect_key`, `kind` (§2.2), `team_id` NULL (a `VISIT`), `payload` JSONB (stat line, opponent, measurables), `impact` NUMERIC (hidden), `headline`, `blurb`, `text_source`, `seq`, `created_at`. `UNIQUE (draft_season, step, prospect_key, kind, seq)`.
- **`scout_team_boards`**: PK `(draft_season, step, team_id, prospect_key)`, `team_grade`, `team_rank`, `fit`, `bias`, `err` (hidden). Top 100 per team per step: about 32 × 100 × 24 ≈ 77k rows a class.
- **`scout_draft_picks`**: PK `(draft_season, overall_pick)`, `round`, `team_id` (the owner on the clock), `original_team_id`, `prospect_key`, `prospect_id`, `recommended_key`, `choice_source` (`GM` | `OFF_SCRIPT` | `JR_TEAM`), `alternatives` JSONB, `rationale`, `rationale_source`, `trade_proposal_id` NULL (FK `gm_trade_proposals`), `recorded_at`, `madden_match`, `madden_prospect_id`.
- **View `vw_scout_board_public`**: consensus rows joined to the prospect's name, position, college, height, weight and age, plus the step's top event headline. It has **no** `true_value`, `err`, `impact` or team boards. Step 5 reads only this view and the read helpers.

### 2.11 Read helpers for step 5 Phase 9 (`engines/scouting_read.py`, read-only)
- `consensus_board(cur, draft_season, step=None, limit=64)`.
- `risers_fallers(cur, season, week, limit=10)`: the latest step visible at the article's cutoff (`week <= cutoff`, the injury-report rule; offseason steps go by stage), each with its driving events.
- `prospect_story(cur, prospect_key, through_step)`.
- `team_draft_record(cur, team_id)`: labels from `fo_quality_inputs` only (HIT/MISS/BUST per pick, streaks), never q itself.
- `draft_room_picks(cur, draft_season)`: only after the draft event is visible (`OFFSEASON_EVENT_ORDER['draft']`).

This answers step 5's interface request (Phase 9: a weekly consensus board with season, week, prospect, grade and rank, plus hit-rate inputs).

---

## 3. Phases

### Phase 1: Schema, true value, class init
**Files:**
- `db/migrations/2026_12_12_scouting.sql` (§2.10; idempotent, `BEGIN/COMMIT`) and the `init_db.sql` fold-in (stage only this hunk).
- `engines/scouting_value.py`: `prospect_key`, `true_value`, `grade_map`, `class_year`.
- `data/scouting/colleges.json` (the 156 schools).
- `engines/scouting_engine.py init --draft-season 2037 [--dry-run]`.
- `pipeline/load_capture.py`: call `relink` after a board load, guarded by `to_regclass`.

**Tests** (`tests/test_scouting_value.py`, pure):
- `V` orders position and age as specified.
- `prospect_key` is stable across a re-scan with a changed id.
- Class year follows from age.
- The grade map hits its anchors.
- A blank OVR gets 50.
- `relink` marks a missing prospect `WITHDRAWN`.

**Exit check:** the migration is applied after `./db/backup_db.sh` (JR approves). `init` creates 388 rows, and a `--dry-run` prints only counts and the grade distribution, never `V` by name.

### Phase 2: Weekly consensus, events, risers and fallers
**Files:**
- `engines/scouting_events.py` (pure: the calendar, stat-line and storyline generators, the §2.6 constraints).
- `engines/scouting_engine.py run --season 2036 [--week W | --stage S] [--dry-run] [--replace]`, with template text only.
- `engines/scouting_read.py`.
- `pipeline/weekly_capture.py`: `step_scouting(season, stage=None)` as **Step 7c** after `step_gm` in weekly and playoff runs and in each offseason stage. It's optional (a failure only logs), skipped without the migration, and catches up any missing steps in order.

**Tests** (`tests/test_scouting_engine.py`):
- The same seed gives an identical step.
- A re-run without `--replace` writes nothing.
- sd(e) follows the schedule and stays ≥ `σ_draft`.
- Every event respects §2.6: stats by position, conference opponents, exact height/weight, age-legal class year, no injury past step 24, production within ±25 points of the `V` percentile.
- Risers and fallers are computed against the previous step.
- The view exposes no hidden column.

**Exit check:** `run --week 4 --dry-run` on the live DB prints the top 32, the 10 risers and fallers and sample events. JR reads them, then the first live step is stored.

### Phase 3: `fo_quality` bootstrap
**Files:**
- `engines/fo_quality.py`: `hit_score`, `team_q`, `bootstrap`, `write`.
- CLI `python3 -m engines.fo_quality --season 2036 [--credit drafted|roster] [--dry-run]`.

**Tests** (`tests/test_fo_quality.py`):
- The clamp to 0.7–1.4.
- No picks gives q = 1.
- The recency weights.
- Shrinkage at n = 1.
- The dev bonus is applied only when dev is known.
- An unlinked pick is skipped.
- The labels and streaks in the inputs.

**Exit check:** the dry run prints 32 q values (expected about 0.85–1.15) and each team's scored picks. JR answers Q2, then the run writes to `gm_team_seasons`.

### Phase 4: Team boards and calibration
**Files:**
- Team boards in `scouting_engine.run`.
- `--calibrate` (§2.5).
- Constants frozen from the run.

**Tests** (`tests/test_scouting_calibration.py`, pure, using a frozen fixture `tests/fixtures/draft_class_2037.csv` exported read-only from the board, with fixture owners, needs and personas):
- 300 seeded drafts under `--q spread` give a poor-pick rate in **[0.20, 0.30]**.
- The ≥ 1.15 band's rate is ≥ 1.5× the ≤ 0.85 band's.
- `--q live` with all q = 1 still lands in the band.
- A visit lowers that team's error.
- The QB rule holds.

**Exit check:** `--calibrate --drafts 1000` prints in-band numbers under both modes, and JR reads one sample draft against consensus.

### Phase 5: Narrative writer (Gemini seam)
**Files:**
- `engines/scouting_narrative.py`: `fact_sheet`, `templates`, `validate_scout_text`, `write_blurbs(…, llm)` (labels `scouting_notes` and `draft_rationale`, through `gm_llm.make_llm`/`flush`).
- Hooked into `step_scouting`.

**Tests** (`tests/test_scouting_narrative.py`):
- The validator rejects an invented number, school, team, height, an ongoing injury, and any mention of OVR/V.
- It accepts invented colour.
- A mocked client error falls back to the template.
- A stored text isn't rewritten on a re-run.

**Exit check:** one supervised Scan week. `llm_usage` has `scouting_notes` rows, and the blurbs read cleanly.

### Phase 6: Offseason steps and the media mock
**Files:**
- Offseason calendar wiring in `step_scouting` for stages 1–4 (combine, pro days, visits, draft week).
- `engines/mock_draft_engine.py`: `evaluate_fit` reads the consensus.
- `newsroom/db_tools.py`: `get_scouted_draft_board` gains the grade, rank and label (the OVR column follows Q6).

**Tests:**
- `tests/test_mock_draft_engine.py` updated for consensus BPA.
- The offseason steps map to 24–27.
- `VISIT` events are team-scoped.

**Exit check:** a dry run of steps 24–27 on a restored DB copy (`db/restore_db.sh`).

### Phase 7: Draft Room (before the 2036 offseason's draft stage)
**Files:**
- `engines/draft_room.py`: `lock_order`, `on_clock`, `recommend`, `record`, `undo_last`, `project_remaining`, `validate`.
- `engines/gm_trade_engine.py`: the board-based draft-day value replaces `DRAFT_DAY_PREMIUM` when the board exists.
- `server/debug_api.py`: `GET /api/debug/draft-room`, and token-gated `POST /draft-room/lock-order`, `/draft-room/picks/{n}` (prospect_key, source) and `/draft-room/picks/{n}/undo`.
- `Mu_Network/src/DebugPage.tsx`: a `DraftRoomCard` in `Control`, phone-first, next to `FrontOfficeCard`.

**Tests:**
- `tests/test_draft_room.py`: the order maps through owners; a recorded pick leaves the pool; an off-script pick changes later recommendations; undo of the last pick only; seeded recommendations are stable; a `JR_TEAM` pick on a user-controlled team; a pick traded mid-draft moves the clock.
- `tests/test_debug_api.py`: the endpoints with `connect` mocked, token required.

**Exit check:** a full 32-pick rehearsal on a restored DB copy with one off-script pick and one draft-day trade.

### Phase 8: Post-draft validation and feedback
**Files:**
- `pipeline/draft_results.py`: calls `draft_room.validate` after the load.
- `pipeline/weekly_capture.py` `step_preseason_engines`: `fo_quality.recompute(season)` after `step_gm_rollover`, plus `scouting_engine init` for the new class after the prospects load.

**Tests:**
- Validation reports MATCH and MISMATCH and edits nothing else.
- The recompute is idempotent and includes the new rookie class at `years = 0`.
- A new class starts at step 1 of the next season.

**Exit check:** after the real 2037 draft and Draft Results scan, the card shows 32 MATCH (or the listed mismatches). At the 2037 Season scan, q is rewritten from two drafts.

---

## 4. `PROJECT_SPEC.md` edits (CLAUDE.md rule, made in the same phase)

| Phase | Sections |
|---|---|
| 1 | §1.1 layout (migration, `engines/scouting_value.py`, `engines/scouting_engine.py`, `data/scouting/`); a new **§3.3g Scouting** (tables, view, the hidden columns); §3.3c a pointer to `prospect_key`; §2.10 board load (the `relink`) |
| 2 | §1.1 (`scouting_events.py`, `scouting_read.py`); a new **§2.18 Scouting & Draft** (`V`, `C_w`, steps, calendar, the §2.6 policy); §6.0 weekly run (Step 7c); §1.2 CLI |
| 3 | §2.18 (`fo_quality`); §3.3f (step 4 writes `fo_quality*`); §1.1 |
| 4 | §2.18 (`T`, calibration constants and band) |
| 5 | §1.1 (`scouting_narrative.py`); §2.18 (the Gemini seam, labels) |
| 6 | §2.10 (the media mock from consensus); §6.0 (offseason stages); §7 `get_scouted_draft_board` |
| 7 | §1.1 (`draft_room.py`); §2.18 (live draft); §2.17 (draft-day value); §4.5 endpoints; §5.2 Draft Room card |
| 8 | §6.0 Season scan (the `fo_quality` recompute, new-class init); §3.3c (validation) |

---

## 5. Out of scope
- Rounds 2–7, UDFAs and rookie contracts (Madden's CPU, D4).
- Writer tools and posts for risers, fallers and track records (step 5 Phase 9 consumes §2.11).
- Trade logic, legality and the approval flow (step 3). Step 4 only feeds the draft-day pick value.
- Madden's own scouting-points system. Nothing is pressed in Madden (D7).
- Trading down by the GM on the clock beyond what `run_draft_day` proposes.

## 6. Open questions for JR
Closed by JR's console answers (§1.1): **Q2** (credit the drafting team) and **Q7** (no re-scan).

**Answered (JR, 2026-10-20), all as recommended:**
1. **Madden's board:** `V` stays ratings-based (κ = 0). Madden's PICK order seeds the week-4 consensus.
3. **Team boards:** consensus by default, with any team's board selectable on `#debug`.
4. **CAR/IND picks:** JR's own picks don't count. `fo_quality` holds at its last value while JR controls a team.
5. **Gemini:** yes, with the template fallback.
6. **Writers and raw OVR:** writers see consensus grades only, never Madden's OVR.
8. **Draft-day trades:** GM agents may propose **pick swaps** during Round 1. JR approves each one through step 3's proposal flow, and makes it in Madden with CPU trades on, since every Round 1 owner is user-controlled. At most 2–3 a draft.
