# Project Mu: Player Progression Plan

**Status:** Phase C (engines/progression.py offline, backtested) complete (2026-10-28); Phase D/E/F/G/H planned for PC arrival.
**Comes after:** the PC migration ([`PC_MIGRATION_PLAN.md`](PC_MIGRATION_PLAN.md)). It needs the save reader, the save writer and the OVR weights from §4–§5 there.
**Companions:** [`MMC_MOD_SURVEY.md`](MMC_MOD_SURVEY.md) (the community progression tools), V2.13 (personalities) and V2.14 (this plan's summary) of the migration plan. [`LEAGUE_OFFICE_PLAN.md`](LEAGUE_OFFICE_PLAN.md) uses the same personality traits for free agency, contracts and depth charts, and its coach-set depth charts feed the playing-time term here.

Turn off Madden's own player progression on the PC. Project Mu then decides how every player's ratings change and writes the result into the save. Progression becomes something the universe can explain: every rating change has causes the newsroom can report. Later, player and coach personalities become some of those causes.

Timings and counts below are estimates until checked on the PC.

---

## 1. Why

Today Madden decides progression and Project Mu only records it. `engines/dev_quality.py` and the aging fit in `engines/trade_value.py` measure changes after the fact, and writers can say *that* a player improved but never *why*. On the PC, Project Mu can write ratings (the teleporter already does), so it can own progression:

* **Stories with causes.** A breakout has a reason (a new position coach, a full season of snaps, a player who lives in the film room), and so does a decline (age, a knee injury, a big contract and no hunger left). Writers, the Agent Wire, press conferences (V2.2) and storylines (V2.12) draw on the same causes.
* **Scouting means something over a career.** Each prospect's hidden true value `V` (spec §2.18) can set his ceiling, so a scouting miss plays out over years instead of only on draft night.
* **Front offices and coaches matter causally.** `dev_quality` and `fo_quality` stop being only measurements of Madden's dice. Hiring a coach known for developing quarterbacks becomes a real decision for the GM agents and the coaching carousel.
* **Personalities have consequences.** Personas (V2.13) do more than set a tone of voice: they move the numbers, within limits the engine sets.

The ground rule from V2.0 holds: **numbers stay deterministic.** The engine decides every rating change from stored inputs and a seed. Language models write about the changes and the personalities and never decide them.

---

## 2. What the save offers (checked on `CAREER-GIANTS-AUTOSAVE`, 2026-10-28)

| Where | What | Use |
|---|---|---|
| `ProgressionXPSlider` (2 rows) | XP percentage per position. One row is all 0 except LS 100; the other is 70 (K and P 50, LS 100) | Set every position to 0 in both rows to stop XP progression for all teams. Which row is user teams and which is CPU teams is a PC check |
| `Player.ExperiencePoints`, `SkillPoints` | XP banked and unspent skill points (almost all 0) | Clear both in the writer step so no player can spend points |
| `Player.RegressionPoints` | 0 for all 3,086 players in the sample (mid-season) | Probably filled at the season rollover. Shows when Madden regresses players |
| `Player.TraitDevelopment` | Normal / Star / Superstar / XFactor (prospects: `College_*`, the same stored values) | Dev trait. Project Mu changes it as part of progression |
| `Player.Focus` | `LoveOfTheGame` 1,374, `Winning` 650, `Financial` 638, `PersonalAccolades` 424 | Madden's own motivation field. A starting value for one personality trait (§6) |
| `HistoryEntry` and the `HistoryManager_*Regression*` / `*DevelopmentTrait*` reactions | Madden's log of progression, regression and dev changes | Read once on the PC to see what Madden changes and when, then use as the check that it changed nothing |
| `FranchiseServer_PlayerProgressionFlow`, `ProgressionPackagesFlow`, `RetirementFlow`, `FocusTraining` | Madden's progression, package, retirement and weekly-training systems | Turned off or overwritten (§3) |

EA's help pages say regression can't be fully turned off, only offset by earning XP, and that it mostly happens in the offseason. So setting XP to 0 isn't enough: Madden's offseason regression has to be overwritten.

---

## 3. Turning off Madden's progression

Two layers. The second makes the first a safety net.

1. **Stop XP.** The writer sets every `ProgressionXPSlider` value to 0 (the in-game XP sliders do the same) and clears `ExperiencePoints` and `SkillPoints`. *Sabos Franchise Mod* (XP disabled, regression reworked, fewer retirements) is the community route to the same result and is worth installing anyway. Its regression rework doesn't matter once step 2 runs.
2. **Overwrite what Madden still changes.** Before the stage where Madden ages and regresses players, read and keep a snapshot. After that stage, write every player's ratings as Project Mu decides them, starting from the snapshot and not from Madden's regressed values. The same comparison with the snapshot, run each week, catches anything else Madden changes (weekly training, abilities).

**Editing needs the franchise closed.** Every franchise tool says to save and quit to the main menu before editing. In-season writes (§5.3) therefore happen while Madden is closed, and they fit the nightly GPU queue's window (migration plan §7.8).

### Why not a community progression tool

| Tool | What it does | Why not as the long-term answer |
|---|---|---|
| **MFT Progression Tool** (Sinthros) | Progression from MyFranchise Tool; needs XP off | A player's path is tied to the save file's name, and a Presentation ID change re-rolls it, so it clashes with the teleporter and the commentary-ID fix. A closed box: Project Mu sees only the results |
| **Madden Trainer** (Buhbooh) | Weekly-training progression in place of XP (XP sliders at 0) | Also a closed box. Runs alongside Madden's own systems, which are being turned off |
| **Sabos Franchise Mod** | XP off, regression reworked, fewer retirements | Keep for XP off and retirements. Its regression is overwritten by §5 |

A tool remains the fallback for the first PC season (§8, phase B) if the engine isn't ready by the 2037 offseason.

---

## 4. Inputs Project Mu already has

| Input | Source | Role |
|---|---|---|
| OVR, age, position, archetype, dev trait | `player_seasons`; on the PC `pipeline/load_save.py` | The state being changed |
| Age curve by position group | `trade_value.py` aging fit (age bands ≤23 … 34+, shrunk toward a generic +1.5 … −3.0) | Starting age term |
| Expected change by age, OVR band, dev, rookie | `dev_quality.py` ridge fit | Calibration target for the whole league (§5.5) |
| Prospect ceiling | `scout_prospects.true_value` (hidden) | Starting ceiling for each draftee |
| Snaps, game grade, starts | `GameOffensiveStats` and the other per-game tables: `DOWNSPLAYED`, `GAMERATING`, `GAMESSTARTED` (migration plan §5.3) | Playing-time and performance term |
| Injuries | the save's `Injury` table, `injuries`, IR flags | Missed time and setback risk |
| Head coach, coordinators, schemes | `coaches`, `coordinators`, `coach_tenures`, `coordinator_tenures` | Coaching term (§7) |
| Contract | `player_contracts` | Contract-year and just-paid effects through personality (§6) |
| Team direction | `gm_team_seasons` (CONTEND / RETOOL / REBUILD) | Playing time for young players on rebuilding teams |
| OVR formulas | `engines/ovr_weights.py`, `adjust_ratings`, `predict_ovr` | Turning an OVR change into rating edits |

---

## 5. The engine (`engines/progression.py`)

### 5.1 Two layers: OVR change, then ratings

1. **Decide the OVR change** for each player (§5.2).
2. **Spread it over the ratings** in a way that fits his age and the cause (§5.4), then check with `predict_ovr` that the result is the target OVR and that his archetype is still on top. This is the teleporter's Step 2 and its archetype settling, run every season.

### 5.2 The OVR change

For player *i* in season *s*:

```
ΔOVR_i = age(group, age)                    curve by position group
       + dev(dev_trait, age)                young players with a better trait grow faster
       + pull(ceiling_i − OVR_i)            room to grow; shrinks toward 0 near the ceiling
       + play(snaps share, game grade)      playing time and how well he played
       + coach(staff, group)                §7; 0 until coach personalities exist
       + persona(traits_i, situation)       §6; 0 until player personalities exist
       + injury(games missed, severity)     missed time, and lasting damage for major injuries
       + ε_i                                seeded noise: random.Random(f"prog:{season}:{player_id}")
```

clamped to a band per age and OVR (a 99 can't go up; a single season moves at most about ±8 OVR outside major injuries). The terms are additive, so each one is stored separately and becomes a reason a writer can cite (§5.6).

**Ceiling** (hidden, like `true_value`; never in a view, writer tool or `#debug`):
* Draftees: from the prospect's `true_value`, so a player the consensus underrated can outgrow his draft slot and an overrated one can stall.
* Players already in the league at teleport time: drawn from OVR, age and dev trait, seeded per player, so an 84-OVR 23-year-old Superstar has more room than an 84-OVR 29-year-old.
* Ceilings can move: a dev upgrade or an elite personality (§6) raises it; a major injury lowers it.

### 5.3 When it runs

| Point | What | Share of a year's change |
|---|---|---|
| **Offseason pass** (after Madden's rollover, before the draft) | The main change: age, ceiling pull, last season's playing time and grade, injuries. Dev trait changes. Rookie camp changes for the new draft class | ~70% |
| **In-season checkpoints** (proposed: after weeks 4, 8, 12 and 16) | Small changes from playing time and form: a rookie who earns snaps grows; a vet who loses his job slips. A breakout is noticed during the season, not only in the next one | ~30% |
| **Major injury** (when it happens) | Ceiling and physical ratings (speed, acceleration) lowered for the worst injuries (torn ACL, Achilles) | — |

The in-season split is a decision (§9, D3): offseason-only is simpler and never edits the save during the season.

### 5.4 Which ratings move

* **Physical ratings** (SPD, ACC, AGI, JMP, STA) follow age: they rise into the mid-20s and fall first and fastest after 29, earlier for HB, WR and CB.
* **Skill and mental ratings** (AWR, play recognition, route running, blocking technique, coverage) keep growing into the late 20s and fall slowly. A veteran can lose OVR in his physical ratings and partly make it back in awareness.
* **Cause shapes the mix:** a coaching gain goes to the ratings the coach teaches (§7); an injury hits physical ratings; a film-room personality (§6) adds to awareness and play recognition.
* Only each archetype's `skills` move (the `adjust_ratings` rule), so markers like QB long snap stay as they are.

### 5.5 Keeping the league stable

Madden's league has a shape (count of 90+ players, mean starter OVR by position, age mix). A progression system with even a small upward bias inflates it within a few seasons. Each run:

* Compares the league's mean ΔOVR by age band and OVR band with the `dev_quality` fit from Madden's own seasons (2035→2036 and, once the PS5 finishes, 2036→2037). These are the only seasons where Madden decided progression, so keep them as the reference.
* Checks the counts by OVR band and position against the 2036 roster. A season where they drift past a set limit is reported, and the age term is adjusted for next season, never mid-run.
* `--simulate N` runs the engine N seasons ahead on a copy (with seeded rookie classes and retirements) to show the drift before the first real offseason.

### 5.6 What it stores

* **`player_progression`**: `season`, `checkpoint` (`OFFSEASON` | `W4` | …), `player_id`, `ovr_before`, `ovr_after`, `terms` (JSONB: each term of §5.2), `ratings_changed` (JSONB), `dev_before`, `dev_after`, `cause` (main cause tag: `AGE`, `BREAKOUT`, `PLAYING_TIME`, `COACHING`, `INJURY`, `PERSONALITY`, `CEILING`, …).
* **`player_ceilings`** (hidden): `player_id`, `ceiling`, `source`, `updated_season`.
* The save writer's edits for that run, as the teleporter writes them.

Writer tools read `player_progression` with the hidden parts left out: the ceiling never, the terms only as cause tags and plain amounts ("+3 OVR, mostly playing time").

### 5.7 Dev traits

Dev trait changes become part of the offseason pass instead of Madden's XP thresholds: a player well above his expected change for two seasons can move up a level, one well below or past 30 can move down. The chances depend on age, ceiling room and (later) personality. `dev_quality` already scores dev steps; it would keep doing that.

---

## 6. Player personalities (V2.13) and progression

The traits below are entries in the shared trait catalogue of [`PERSONALITY_PLAN.md`](PERSONALITY_PLAN.md) §4.2, not a separate system.

V2.13 plans personas for players (identity line, takes log, season summaries). This plan adds a small set of **numeric traits** to each persona, decided by the engine and stored, so personality can affect the numbers without a language model deciding anything:

| Trait (0–1) | Effect on progression | Other uses |
|---|---|---|
| **Work ethic** | Larger ceiling pull; slower decline after 30 | Press quotes, training-camp stories |
| **Football IQ / film study** | Growth weighted to awareness and play recognition; extends late-career value | Scouting reports, broadcast notes |
| **Coachability** | Multiplies the coaching term (§7); a stubborn star gains less from a good coach | Coach–player friction stories |
| **Drive** (from Madden's `Focus`: `Winning` and `PersonalAccolades` high; `LoveOfTheGame` steady; `Financial` drops after a big deal) | Contract-year bump; a dip after a big extension for low-drive players | Holdouts and contract-year stories in the inbox (V2.9) |
| **Resilience** | Size of the injury term; recovery of physical ratings after major injuries | Comeback stories |
| **Ego / temperament** | No direct effect on ratings; affects how he reacts to losing snaps (which then affects playing time through the depth chart) | Agent Wire tone, locker-room stories |

* **Seeding:** deterministic per player (`sha256("persona:" + player_id)`), stratified like the GM traits, with Madden's `Focus` as a starting point for drive. Draftees get theirs from scouting: character flags and interview answers (V2.10) reveal traits the engine already set, never change them.
* **Limits:** the personality term is capped (about ±1.5 OVR a season), so personality colours careers without overriding age and talent.
* **Can change slowly:** a player's traits may shift after life events the engine records (a title, a major injury, a big contract), a small seeded step at most once a season. Identity stays fixed, like `agent_memories.persistent_memory`.
* **Prose:** the persona's identity line and voice come from the local model once (V2.13), written from the stored traits, never the other way round.

---

## 7. Coach personalities and progression

Coaches have `slant`, schemes and tenure records today. This plan adds development traits to head coaches and coordinators (and later position coaches, if a staff table is built):

| Trait | Effect |
|---|---|
| **Teacher** (0–1) | Size of the coaching term for the groups he coaches (head coach: all players a little; OC: offense; DC: defense) |
| **Specialty** (position groups) | Extra effect on one or two groups, e.g. a QB whisperer or a pass-rush coach. Gains go to that group's skill ratings |
| **Youth vs veteran trust** | Shifts playing time toward rookies or veterans, which feeds the playing-time term |
| **Demands** (0–1) | Works with player coachability: a demanding coach gets more from high-coachability players and less from low-coachability ones |
| **Scheme fit** | A player whose archetype fits the scheme progresses a little faster (Madden's scheme-fit idea, decided here) |

* **Effects of hiring.** The coaching carousel (spec §2.19) and the hot seat can weigh development: a REBUILD team's GM agent prefers a teacher, and a coach whose groups regress loses standing. `dev_quality` gains the coaching term as an explained part of each team's score.
* **Staff memory.** A coordinator who developed a QB gets credit in writers' tools and in his next job's introduction stories.
* **Seeding:** existing coaches' traits are seeded deterministically and can be nudged by JR before the first PC season; real-world coaches get traits consistent with their known reputations (the hallucination policy allows invented narrative, never contradicting data).

---

## 8. Phases

| Phase | What | Needs |
|---|---|---|
| **A. PC checks** | (1) XP sliders at 0 stop all in-season changes, weekly training included; which slider row is user and CPU. (2) At which offseason stage Madden ages and regresses players, and what `HistoryEntry` logs. (3) Madden keeps written ratings and recomputes OVR from them, or needs `OverallRating` written (migration plan §5.4 item 7). (4) How abilities (Superstar, X-Factor) are assigned with XP off. (5) Whether retirement chances depend on the ratings Project Mu writes | The PC, a test franchise |
| **B. First PC season** | Sabos with XP off; Madden's offseason regression left alone if the engine isn't ready, or a tool (§3) as a stopgap. Keep the `HistoryEntry` log of what Madden did | Phase A |
| **C. Engine, offline** | Complete (2026-10-28): `engines/progression.py` built with age, dev, ceiling, playing-time, injury, and seeded noise terms. 2035→2036 backtest shows predicted delta (-0.095) aligned within 0.05 of actual (-0.147); `--simulate 5` proves multi-year stability bounds. Tested in `tests/test_progression.py`. | `dev_quality`, aging fit, scouting |
| **D. Engine in the save** | The offseason pass writes through `save_writer.mjs` (snapshot before Madden's rollover, write after it), stores `player_progression`, and runs a daily check that Madden changed nothing between passes. Writer tool `get_player_progression` | Phases A and C, migration §5 |
| **E. In-season checkpoints** | If D3 says yes | D |
| **F. Player personality traits** | Traits table, seeding, the persona term, Focus mapping. Personas' voices (V2.13) | D, V2.13 |
| **G. Coach traits** | Coach and coordinator traits, the coaching term, carousel and GM weighting | D, F (coachability) |
| **H. Dev traits and ceilings fully owned** | Dev changes from §5.7; ceilings move with events | D |

Phases F and G can start before D as data (traits seeded, shown to writers) with the terms at 0, so personalities appear in stories first and in the numbers once the engine writes.

---

## 9. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| D1 | Who owns ratings after the move: Project Mu (this plan) or Madden? | Project Mu, from the 2038 offseason at the latest |
| D2 | First PC season (2037): Madden's own progression, a community tool, or this engine? | Madden's own with XP off via Sabos, unless phase C is ready by the 2037 offseason |
| D3 | In-season checkpoints, or offseason only? | Start offseason only; add checkpoints in phase E once the save-writing routine is smooth |
| D4 | How much weight performance gets against age and talent | Playing time and grade together about a quarter of a typical season's change |
| D5 | Can JR see ceilings or traits? | Ceilings never (like `true_value`); personality traits visible on player pages once seeded |
| D6 | Does Project Mu also take over retirements? | Not in this plan; Sabos's lower retirement chance for now, revisit after phase D |
| D7 | Can JR's own team's coaching choices affect his players? | Yes, the same rules as every team (JR's team is scored in `dev_quality` today) |

---

## 10. Spec changes when built

`player_progression`, `player_ceilings`, player and coach trait tables in spec §3; the engine in §2 and the run order (offseason pass after the rollover, before the draft board loads); the writer tool in §7; `dev_quality`'s coaching term; the new steps in the save watcher (migration plan §5.2).
