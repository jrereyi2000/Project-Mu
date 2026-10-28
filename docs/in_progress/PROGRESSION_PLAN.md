# Project Mu: Player Progression Plan

**Status:** Phase C (engines/progression.py offline, backtested) complete (2026-10-28). Revised 2026-10-28 with the contextual design (three rating groups, durability and mileage, ceiling bands and breakthroughs, peak upkeep, form, Madden's behaviour traits, scheme continuity and mentors, and how player and coach traits drive each): not built; phase C2 builds it offline, phases D–H on the PC. JR decided D8–D12 (2026-10-28); D13 (how form reaches the game, through Madden's morale or a ratings overlay) waits on a PC test.
**Comes after:** the PC migration ([`PC_MIGRATION_PLAN.md`](PC_MIGRATION_PLAN.md)). It needs the save reader, the save writer and the OVR weights from §4–§5 there.
**Companions:** [`MMC_MOD_SURVEY.md`](../MMC_MOD_SURVEY.md) (the community progression tools), V2.13 (personalities) and V2.14 (this plan's summary) of the migration plan. [`LEAGUE_OFFICE_PLAN.md`](../not_started/LEAGUE_OFFICE_PLAN.md) uses the same personality traits for free agency, contracts and depth charts, and its coach-set depth charts feed the playing-time term here.

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
| `Player.Original*Rating` (56 fields) | Each player's ratings when he entered the league: current minus original grows with `YearsPro` (+1.2 average for rookies, +10 at 5–7 years) | Calibration of the rating groups (§5.4). Not a second rating layer, so it can't hold form (§5.11) |
| `Player.PT_*` (94 flags, 59 in use) | Madden's behaviour traits: `PT_HAPPYFEET` 32, `PT_SEEINGGHOSTS` 35, `PT_PARANOID` 14, `PT_PANICBUTTON` 31, `PT_DISCIPLINED` 805, `PT_UNDISCIPLINED` 386, `PT_SHOWBOAT` 413, `PT_COVERBALL` 296, `PT_BIGHITTER` 120, … | Gained and lost through progression (§5.12); starting points for some personality traits (§6) |
| `Player.ConfidenceRating` and the morale tables (`MoraleManager`, `MoraleUpdateEvent` with `PlayerMoraleAward`/`TeamMoraleAward`, `MoraleManager_PreSeasonStart_Reaction`, `PlayerGoalsEval_MoraleUpdateReaction`) | Madden's morale, most likely stored per player as `ConfidenceRating`: 26–99; 1,898 players at 50 (the default), 209 at 90+ (mean OVR 84 against 72 for the league). Updated by Madden from events and player goals; the tuning (`MoraleManager.MoraleTuning`) points outside the save, so it can't be edited | Madden's morale logic is replaced by Project Mu's form (§5.11) |
| `Player.InjuryRating`, `ToughnessRating`, `InjuryType`, `InjurySeverity`, `WasPreviouslyInjured` | Injury proneness, toughness, current injury (e.g. `KneeACLCompleteTear` 10), history flag | Starting point for durability (§5.8); which injuries can do lasting damage |

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
| Snaps, starts, per-game lines | `GameOffensiveStats` and the other per-game tables: `DOWNSPLAYED`, `GAMESSTARTED`, `CTHALLOWED`, `OLINEPANCAKES`, `OLINESACKSALLOWED`, … (migration plan §5.3). `GAMERATING` is too sparse to use (§5.15) | Playing-time and performance term |
| Injuries | the save's `Injury` table, `injuries`, IR flags | Missed time and setback risk |
| Head coach, coordinators, schemes | `coaches`, `coordinators`, `coach_tenures`, `coordinator_tenures` | Coaching term (§7) |
| Contract | `player_contracts` | Contract-year and just-paid effects through personality (§6) |
| Team direction | `gm_team_seasons` (CONTEND / RETOOL / REBUILD) | Playing time for young players on rebuilding teams |
| OVR formulas | `engines/ovr_weights.py`, `adjust_ratings`, `predict_ovr` | Turning an OVR change into rating edits |
| Career usage | season stat tables from the 2026–2034 backfill on (carries, targets, receptions, tackles, starts); on the PC also `DOWNSPLAYED` per game | Mileage (§5.8) |
| Scheme continuity | `coordinator_tenures`, `coach_tenures` (years with the same OC, DC, head coach) | Learning penalty after staff changes (§5.13) |
| Position room | rosters; on the PC the save's depth chart | Mentors (§5.13), supporting cast (offensive line for QBs) |

---

## 5. The engine (`engines/progression.py`)

### 5.1 Three rating groups, then OVR

Ratings fall into three groups that age on their own timelines (§5.4): **physical** (set mostly on draft day, worn down by age and mileage), **technical** (learned through snaps and coaching over 2–4 seasons, then a plateau) and **mental** (grows with snaps, film study and mentors, and declines last and slowest). Veterans stay good when mental and technical gains offset what their bodies lose, and how long that holds is a player's prime.

1. **Decide the change for each group** (§5.2), from its own terms.
2. **Add the groups up into an OVR change** with the position's OVR weights, then apply the league-shape term and the clamp (§5.5). Built today as one ΔOVR; the revision (phase C2) gives each term a group, so the league term and the clamp scale the groups' changes rather than replacing them.
3. **Spread each group's change over its ratings** in a way that fits the cause, then check with `predict_ovr` that the result is the target OVR and that his archetype is still on top. This is the teleporter's Step 2 and its archetype settling, run every season.

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

clamped to a band per age and OVR (a 99 can't go up; a single season moves at most about ±8 OVR outside major injuries).

**By group (revision, phase C2).** Each term above lands in one group, and the revision adds the terms of §5.8–§5.13:

```
Δphysical  = strength program (first 2 seasons, +1…+2)  − wear(age, mileage, durability)  − lasting injury damage
Δtechnical = growth(snaps, efficiency, room to ceiling) × coaching × continuity  − erosion once physical loss is large
Δmental    = growth(snaps, film study, mentor, room to ceiling)  − slow decline late in a career
ΔOVR       = Σ OVR weights(position) · group change  + upkeep (above 90) + league term + persona + ε
```

`age` splits into the physical wear curve and the technical and mental growth curves; `dev` and `pull` become growth rates toward each group's ceiling (§5.9); `play` and `performance` feed technical and mental growth; `injury` is physical. Every term stays stored on its own, and the backtest against Madden (§5.5) must still pass.

**How well he played (built 2026-10-28, JR):** the `play` term is two parts: playing time (games and snaps, about ±0.3) and a **performance term** from Project Mu's season stats (`engines/progression.py` `performance_scores`). Each player's production per game (QB: passing and rushing yards with touchdowns and interceptions; RB, WR/TE: scrimmage yards and touchdowns; OL: sacks allowed; front seven and DBs: tackles, TFL, sacks, picks, deflections, forced fumbles, touchdowns; K: FG% with long makes; P: net average) is ranked within his position group among players with 6+ games, giving −1 (worst) to +1 (best) OVR a year, plus award bonuses (First Team All-Pro +1, Second Team or MVP/OPOY/DPOY/OROY/DROY +0.5, at most +1.5); the term is clamped to −1 … +2 and stored as `performance_term`. Madden's own 2035→2036 changes barely follow production (correlation 0.03), so this is Project Mu's choice, not a fit: the Madden backtest goes from MAE 1.13 to 1.19 with it (`--backtest --with-performance`). On the 2037 dry run, award winners average +1.0 OVR, the top quarter of producers +0.25, the middle −0.16, the bottom quarter −0.72. The simulation draws each season's production from OVR plus noise, with 62 All-Pros. With it the star term of the league shape falls to about 0: standout seasons and All-Pros keep the 90+ count instead. The terms are additive, so each one is stored separately and becomes a reason a writer can cite (§5.6). Positions whose box scores say little (linemen, defensive backs) get per-down rates, unit credit and a reliability weight (§5.15).

**Ceiling** (hidden, like `true_value`; never in a view, writer tool or `#debug`):
* Draftees: from the prospect's `true_value`, so a player the consensus underrated can outgrow his draft slot and an overrated one can stall.
* Players already in the league at teleport time: drawn from OVR, age and dev trait, seeded per player, so an 84-OVR 23-year-old Superstar has more room than an 84-OVR 29-year-old.
* Ceilings can move: a dev upgrade or an elite personality (§6) raises it; a major injury lowers it.
* The revision replaces the single ceiling with a band per group, a floor and a spread, and adds breakthroughs (§5.9).

### 5.3 When it runs

| Point | What | Share of a year's change |
|---|---|---|
| **Offseason pass** (after Madden's rollover, before the draft) | The main change: age, ceiling pull, last season's playing time and grade, injuries. Dev trait changes. Rookie camp changes for the new draft class | ~70% |
| **In-season checkpoints** (proposed: after weeks 4, 8, 12 and 16) | Small changes from playing time and form: a rookie who earns snaps grows; a vet who loses his job slips. A breakout is noticed during the season, not only in the next one | ~30% |
| **Major injury** (when it happens) | Ceiling and physical ratings (speed, acceleration) lowered for the worst injuries (torn ACL, Achilles); the roll is in §5.8 | — |
| **Form** (weekly, D9) | Form from the last few games, written to the save with Madden's morale overridden, and reset to neutral each offseason (§5.11). Not part of the year's change | — |

The in-season split is a decision (§9, D3). Weekly save writes happen anyway in season (`LEAGUE_OFFICE_PLAN.md`: trades, waivers, depth charts, injury durations, with Madden closed), so checkpoints and form add rating edits to an existing weekly write, not a new routine. Mileage (§5.8) is counted weekly either way, since it only reads.

### 5.4 Which ratings move

* **Physical** (Speed, Acceleration, Agility, ChangeOfDirection, Strength, Jumping, Stamina, ThrowPower, Toughness): about fixed on draft day. A strength program adds +1 or +2 (mostly Strength and Stamina) in the first two seasons. Decline starts in the late 20s, earlier for HB, WR and CB, and its speed depends on mileage and durability as much as on age (§5.8).
* **Technical** (route running, release, catching in traffic, pass and run block technique, pass rush moves, man and zone coverage, press, tackling, ball-carrier vision, throw accuracy, kick accuracy): grows with snaps, coaching and scheme continuity over 2–4 seasons, then holds through the late 20s and early 30s. It erodes slowly, and only once physical decline is large.
* **Mental** (Awareness, PlayRecognition): grows with snaps, film study and mentors, and declines last and slowest (D10, JR). It is how a veteran wins once his burst is gone, and the ceiling caps it so veterans don't all drift to 99. **Decline is set by the peak** (JR): the engine keeps each player's career-high mental ratings, and once his physical decline is under way he loses a share each season of what he gained between draft day (`Original*Rating`) and that peak, rising slowly with the years past it. A player who built a big mental edge loses it in proportion, never all at once, and never falls below his draft-day level from age alone.
* **Madden's own league already looks like this.** Current minus draft-day ratings (`Original*Rating`) in the 2036 sample: physical +0.0 (0–1 years pro), +0.7 (2–3), +2.1 (4–6), +2.3 (7–9), +1.7 (10+); awareness and play recognition +0.2, +15.3, +32.6, +37.0, +19.6. The engine is fitted to these shapes. The 10+ group probably mixes in players whose entry ratings date from before the franchise began, so it is checked, not fitted.
* **Cause shapes the mix:** a coaching gain goes to the technical ratings the coach teaches (§7); an injury hits physical ratings; a film-room personality (§6) adds to awareness and play recognition.
* Only each archetype's `skills` move (the `adjust_ratings` rule), so markers like QB long snap stay as they are.

### 5.5 Keeping the league stable

Madden's league has a shape (count of 90+ players, mean starter OVR by position, age mix). A progression system with even a small upward bias inflates it within a few seasons. Each run:

* Compares the league's mean ΔOVR by age band and OVR band with the `dev_quality` fit from Madden's own seasons (2035→2036 and, once the PS5 finishes, 2036→2037). These are the only seasons where Madden decided progression, so keep them as the reference.
* Checks the counts by OVR band and position against the 2036 roster. A season where they drift past a set limit is reported, and the age term is adjusted for next season, never mid-run.
* `--simulate N [--follow-madden]` runs the engine N seasons ahead on a copy, with departures at Madden's own 2035→2036 rates and rookie classes shaped like the real 2036 class, and checks every season against limits relative to 2036 (`STABILITY_BOUNDS_2036`). First result (2026-10-28, PRE_PC_WORK.md R6): the league shrinks as Madden's does (90+ players 144 → 46 in five seasons), mostly from weak rookie classes replacing better veterans; the correction (±0.4 OVR a year at most) can't hold it. **JR decided (2026-10-28): year over year the league keeps roughly its 2036 shape, the same number of 90+ players and the same mean OVR.** The engine does it in two layers. (1) Each player's change is calibrated to Madden: a per-age-band offset (`AGE_BAND_OFFSET`) fitted on the 2035→2036 backtest, every band within ~0.1 of Madden (34+ has 5 players). (2) A league-shape term, fitted each offseason before it runs (`calibrate_league_term`): a development term for young players below star level (full weight to 23 and OVR ≤ 79, tapering to 0 at 30 and OVR 88) holds the mean, and a star term for players at 86+ and 32 or younger holds the 90+ count. The fit projects the league after the coming draft, with the draft class shaped like the real 2036 class. Stored per player as `league_term` so writers can cite it. `--follow-madden` turns it off. Five simulated seasons from 2036 (2026-10-28 run, with the performance term): 90+ count 143–148 (2036: 144), mean OVR 78.05–78.21 (78.13), starters 85.8–86.4 (85.51). The cost: the development term runs +4.5 to +5.5 OVR a year at full weight, so a 68-OVR rookie gains ~5–6 a year where Madden's offseason gave ~1; the star term sits at −0.25 to 0, since standout seasons and All-Pros now keep the 90+ count (it was +0.75 to +1 before the performance term).

**With the revision**, the league term keeps the *count* of 90+ players and peak upkeep (§5.10) decides *who* they are: players who stop performing or wear down drop out, and the star term goes to the players who produce. The league term's development part goes into technical and mental ratings, never physical ones.

### 5.6 What it stores

* **`player_progression_history`** (built, `db/migrations/2027_01_08_player_progression.sql`): `season`, `player_id`, `old_ovr`, `new_ovr`, `delta_ovr`, one column per term (`age_term`, `dev_term`, `ceiling_term`, `play_term`, `injury_term`, `noise_term`, `league_term`, `performance_term`), `ratings_diff` (JSONB). Still to add: `checkpoint` (`OFFSEASON` | `W4` | …), `dev_before`, `dev_after`, `cause` (main cause tag: `AGE`, `BREAKOUT`, `BREAKTHROUGH`, `PLAYING_TIME`, `COACHING`, `INJURY`, `WEAR`, `UPKEEP`, `PERSONALITY`, `CEILING`, …), and for the revision `group_changes` (JSONB: physical, technical, mental), `wear_term`, `upkeep_term`, `continuity_term`, `mentor_term`, `persona_term`, `coach_term`, `behaviour_changes` (JSONB: `PT_*` flags gained or lost).
* **`player_ceilings`** (hidden, built): `player_id`, `ceiling`, `source`, `updated_season`. The revision adds `floor`, `spread`, per-group ceilings (§5.9) and `peak_mental` (JSONB: career-high Awareness and PlayRecognition, for the mental decline of §5.4).
* **`player_durability`** (hidden, revision): `player_id`, `durability` (0–1), `source`, `updated_season`.
* **`player_mileage`** (revision): per player and season, touches, hits, snaps and the wear they add (§5.8). Readable by writers ("1,600 career touches by 26").
* The save writer's edits for that run, as the teleporter writes them.

Writer tools read `player_progression_history` with the hidden parts left out: the ceiling and durability never, the terms only as cause tags and plain amounts ("+3 OVR, mostly playing time").

### 5.7 Dev traits

Dev trait changes become part of the offseason pass instead of Madden's XP thresholds: a player well above his expected change for two seasons can move up a level, one well below or past 30 can move down. The chances depend on age, ceiling room and (later) personality. `dev_quality` already scores dev steps; it would keep doing that.

**JR decided (2026-10-28):** with ceiling bands (§5.9), the dev trait stays and becomes the visible summary of the hidden band: Madden still needs `TraitDevelopment` in the game (abilities, X-Factor slots), and it tells JR and writers roughly how much room a player has, without the number.

### 5.8 Durability and mileage

* **Durability** (hidden, 0–1): how well a body resists wear per snap. Seeded per player (`sha256("durability:" + player_id)`) around a starting point from Madden's `InjuryRating` and `ToughnessRating`; draftees from scouting's medical notes. Like the ceiling, it never shows in a view or writer tool.
* **Mileage:** career touches, hits and snaps, weighted by position (an RB carry and a WR catch over the middle count more than a clean target; trench snaps for linemen). Counted from the season stat tables now and from `DOWNSPLAYED` per game on the PC.
* **Wear:** the physical decline of §5.4 grows with mileage above what is normal for his age and position, and shrinks with durability. A 26-year-old RB with 1,600 career touches declines faster than a 28-year-old committee back with 650.
* **Lasting injury damage:** a major lower-body or structural injury (`KneeACLCompleteTear`, Achilles, Lisfranc, patellar tendon) rolls, seeded, for a permanent −1 … −3 to Speed, Acceleration or Agility on his return, and lowers his physical ceiling. Resilience (§6) and later team medical staff (§7) change the odds. He then has to win with technical and mental growth, which is a story.
* **Playing hurt:** players who play through minor injuries (by personality, §6) add extra wear for those weeks.

### 5.9 Ceiling bands and breakthroughs

* **Band:** each player has a floor, a ceiling and a spread (how widely his path can land between them), with a ceiling per group: physical about at his draft-day ratings plus the strength program, technical and mental from `true_value` and his profile. A polished four-year starter has a narrow band near the top of his range and reaches it fast; a raw elite athlete has a wide band that needs coaching and snaps; a late-round pick sits low with a small tail. These profiles come out of the prospect's entry ratings and scouting's uncertainty about him; they are not fixed classes.
* **Growth toward the ceiling** replaces the dev multiplier: room to grow sets the rate, and the spread sets how far a seeded draw can move him each season.
* **Breakthrough:** a player 28 or younger with two straight seasons in his position group's top quarter (`performance_scores`) can have his technical and mental ceilings raised by a seeded 1–4. Personality and coaching (§6, §7) set the odds, so every breakthrough has a cause tag (`BREAKTHROUGH`) and a reason a writer can give.

### 5.10 Peaks and upkeep

Peaks should be short for most players and long for a few. Above 90 OVR, an **upkeep term** pulls a player down unless he keeps producing: roughly `−k · (OVR − 90)` scaled down by his performance rank and up by missed games and wear, so a 98 who has a middling season or misses six games loses 2–3. The league term (§5.5) keeps the number of 90+ players, so upkeep changes who holds the spots rather than how many there are (D8, JR decided 2026-10-28: keep the count; no hard cap on underlying ratings). Upkeep reads the reliability-weighted performance score (§5.15), so stars at positions with thin stats aren't taxed for noise.

Prime length then comes out of the groups and not from a label: a player who peaks on physical ratings with low durability and average mental ceilings falls fast once his speed goes (a short peak); a player with high durability and high technical and mental ceilings offsets his physical decline into his 30s (a long one).

### 5.11 Form (in-season; D9)

Form is how a player is playing this month, separate from his underlying ratings: hot streaks, slumps, nagging minor injuries, a better or worse supporting cast. It moves weekly from the last few games, within about ±3 OVR, and **resets to neutral every offseason**, so a player coming off an MVP season starts camp at his underlying level and has to earn it again.

It rides on the weekly in-season write the league office already needs (§5.3) (D9, JR decided 2026-10-28: build it, in phase E).

**Madden's morale is the same idea, decided badly.** Madden already moves a per-player morale (most likely `ConfidenceRating`, §2) from game events and player goals, and in JR's experience it doesn't behave sensibly. Its tuning sits outside the save, so it can't be fixed, only overridden. Project Mu takes it over: every weekly write sets `ConfidenceRating` for all players, so Madden's own morale changes last at most until the next write (one game). Two ways to carry form (D13):

1. **Morale fixed, form as a ratings overlay** (JR's proposal). Every player's `ConfidenceRating` is set to one value, so morale no longer differs between players; form is a small overlay on ratings, written each week and removed at the offseason pass. Project Mu stores the underlying ratings; the save holds underlying plus form, and the scan-OVR rule and the save loader (migration plan §4.3, §5) have to know which layer they read. The fixed value should be the one at which Madden applies no effect: 50 is Madden's default and probably neutral; 99 for everyone would give every player Madden's high-morale effect, so the sim would play above the OVRs Project Mu fits and shows, and a form overlay would start from a boosted base.
2. **Morale as the carrier.** Project Mu writes each player's form into `ConfidenceRating` weekly (neutral form = the neutral value). Ratings and Madden's displayed OVR stay untouched, and Madden's own in-sim morale effect plays it out. Better if the PC test shows morale's effect is of a sensible size and direction; if its effect is opaque or too large, option 1.

PC test (phase A (6)): with all players at 50 and then at 99, does the sim change, and what value is neutral; and does a player's morale visibly change how he plays.

Form isn't stored in `player_ceilings` or used by the yearly change; the yearly change reads the season's production instead.

### 5.12 Madden's behaviour traits (`PT_*`)

Madden's `PT_*` flags change how a player plays in the sim (Happy Feet, Seeing Ghosts, Paranoid, Panic Button, Disciplined, Undisciplined, Covers Ball, Big Hitter, …). Project Mu gains and removes them in the offseason pass:
* **Gaining a bad one** depends on circumstances: a young QB behind a weak line who takes many sacks can get `PT_HAPPYFEET` or `PT_SEEINGGHOSTS`; a defender with many penalties can get `PT_UNDISCIPLINED`. Temperament and confidence (§6) raise or lower the odds; a mentor or a good coach lowers them.
* **Losing it** depends on coaching, experience and a better situation.
* At most one gained and one lost per player per season, seeded. Stored in `behaviour_changes` with the cause, so a writer can report that the sacks got into his head.
* PC checks (phase A): Madden keeps written flags; with XP off, nothing else assigns them; which flags are mutually exclusive.

### 5.13 Context: efficiency, supporting cast, continuity, mentors

* **Efficiency over volume:** the performance term (§5.2) uses per-play rates where the stats allow (yards per attempt and per target, interception and sack rates, per-down rates for linemen and defenders, §5.15), so a rookie force-fed 180 inefficient targets doesn't grow from volume alone. Playing time still counts, as reps.
* **Supporting cast:** a QB's offensive line, a receiver's QB. A bad environment raises the odds of bad behaviour traits (§5.12) and slows growth a little; it doesn't touch physical ratings.
* **Scheme continuity:** the first season under a new coordinator (from `coordinator_tenures`) cuts technical and mental growth for his side of the ball by a learning penalty; the size depends on the coach's openness (§7). Teams that keep their staff are rewarded.
* **Mentors:** a veteran at the same position group with awareness 85+ on the roster speeds mental growth for young players there. The Mentor trait (§6) multiplies it, a Loner gives nothing. Stored as a relationship (`MENTOR`, PERSONALITY_PLAN §4.7) so stories can say who taught whom.

### 5.14 Dropped: hidden ratings for young players

The design proposed showing young players' ratings as ranges until they had logged snaps. Madden's own screens show JR every rating, and Project Mu can't hide them there; ceilings, durability and spreads are hidden already (D5). JR dropped it (D11, 2026-10-28).

### 5.15 Performance for positions with thin stats

Box scores describe QBs, backs, receivers and pass rushers well, and linemen and defensive backs poorly: a corner who is never thrown at has an empty line, and a guard's line is downs, pancakes and sacks allowed. `GAMERATING` doesn't fill the gap. In the sample save it is 0 on all 1,347 `GameOLineStats` rows, nonzero on only 704 of 3,661 defensive rows (2,339 rows with 20+ downs have 0) and 717 of 1,831 offensive rows, so it isn't a grade for every player. The performance term therefore works like this:

1. **Per-down rates.** `DOWNSPLAYED` is on every per-game row, so counts become rates per down: sacks allowed and pancakes for linemen; sacks, half sacks, tackles for loss, forced fumbles and deflections for the front seven; catches allowed (`CTHALLOWED`), interceptions and deflections for defensive backs. Few catches allowed over many downs scores as good coverage, not as missing data. Until the PC's per-game tables arrive, season totals per game stand in.
2. **Misleading stats left out.** Tackles by corners and safeties are neutral (many often means he is being thrown at, or the front is leaking); big hits don't count.
3. **Unit credit.** For linemen and the secondary, part of the score comes from the unit, shared by downs played and judged against the league that season: the team's sack rate allowed and yards per carry for the offensive line; opponents' completion rate, yards per attempt and passing touchdowns for the secondary; rushing yards allowed for the front. Proposed own/unit split: OL 50/50, DB 60/40, front seven 80/20, skill positions all own.
4. **Reliability weight per group.** Each group's score is multiplied by how well it predicts itself: the year-to-year correlation of a player's score over the stat seasons since 2026, and on the PC split halves of a season. A group whose score barely repeats (likely OL and safeties) gets a small performance term and leans on playing time and health; QBs and pass rushers get the full range. Players with few downs are shrunk toward 0 as well.
5. **Awards stay.** All-Pro and the defensive awards are chosen by position, so they reach linemen and DBs whatever the box score says.
6. **Checked, not trusted.** The backtest reports for each group how the score relates to OVR, to awards and to itself the next season. A group that fails gets its weight cut toward 0; no formula is invented to fill the gap.

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

### 6.1 Where traits act on the revised engine

The numeric traits above and the named catalogue traits (PERSONALITY_PLAN §4.2) multiply the terms of §5.8–§5.13. Talent, durability and the ceiling band set the limits; personality sets the rate and the shape inside them.

| Mechanism | Raised by | Lowered by |
|---|---|---|
| Physical wear (§5.8) | Coasting; Money First in the two seasons after a big extension; playing hurt (Ring Chaser, Spotlight Seeker play through minor injuries more often) | Workout Fanatic; work ethic |
| Lasting injury damage (§5.8) | — | Resilience (odds of permanent loss, and how much physical rating comes back) |
| Technical growth | Perfectionist; coachability × the coach's demands (§7) | Coasting; low coachability under a demanding coach |
| Mental growth and the mental ceiling | Film Room Rat; football IQ | Coasting |
| Mentoring young players (§5.13, as the veteran) | Mentor (×1.5); Loyal to the team | Loner (none); Diva (none, and a rookie's form dips) |
| Breakthrough odds (§5.9) | Film Room Rat, Perfectionist, Workout Fanatic | Coasting |
| Peak upkeep (§5.10) | Money First after getting paid; low drive | Legacy Builder, Ring Chaser; high drive |
| Form swings (§5.11) | Confidence slider (brash: bigger swings both ways); Temperament (volatile) | Calm temperament; a Players' Coach (§7) |
| Bad behaviour traits (§5.12) | Low confidence (Happy Feet, Seeing Ghosts, Panic Button); volatile temperament (Undisciplined) | Mentor in the room; Disciplinarian or QB Whisperer coach |
| Scheme learning penalty (§5.13) | — | Football IQ; Film Room Rat |

* **The cap covers the total:** the personality effect is the season's change with his traits minus the change with neutral traits, capped at about ±1.5 OVR, so stacking traits can't exceed it. Over a career the effect is larger, because wear and ceilings compound; that is the point (a Workout Fanatic with high durability stays in his prime longer), and the league term (§5.5) still holds the league's shape.
* **Madden's fields as starting points:** `Focus` for drive; `PT_DISCIPLINED` and `PT_UNDISCIPLINED` for temperament; `PT_SHOWBOAT` (413 players) raises the odds of Spotlight Seeker or Diva; `ToughnessRating` for resilience. Once seeded, personality isn't re-read from the save: the flags Project Mu changes later (§5.12) are outcomes, not traits.

### 6.2 Seeding, limits and change

* **Seeding:** deterministic per player (`sha256("persona:" + player_id)`), stratified like the GM traits, with Madden's `Focus` as a starting point for drive. Draftees get theirs from scouting: character flags and interview answers (V2.10) reveal traits the engine already set, never change them.
* **Limits:** the personality term is capped (about ±1.5 OVR a season, measured as in §6.1), so personality colours careers without overriding age and talent.
* **Can change slowly:** a player's traits may shift after life events the engine records (a title, a major injury, a big contract), a small seeded step at most once a season. Identity stays fixed, like `agent_memories.persistent_memory`.
* **Prose:** the persona's identity line and voice come from the local model once (V2.13), written from the stored traits, never the other way round.

---

## 7. Coach personalities and progression

Coaches have `slant`, schemes and tenure records today. This plan adds development traits to head coaches and coordinators (and later position coaches, if a staff table is built):

| Trait | Effect |
|---|---|
| **Teacher** (0–1) | Size of the coaching term for the groups he coaches (head coach: all players a little; OC: offense; DC: defense). Coaching gains go to technical ratings; a coordinator's scheme installation also adds to mental ratings in his second season and later |
| **Specialty** (position groups) | Extra effect on one or two groups, e.g. a QB whisperer or a pass-rush coach. Gains go to that group's technical ratings |
| **Youth vs veteran trust** | Shifts playing time toward rookies or veterans, which feeds the playing-time term and mileage (§5.8) |
| **Demands** (0–1) | Works with player coachability: a demanding coach gets more from high-coachability players and less from low-coachability ones |
| **Scheme fit** | A player whose archetype fits the scheme progresses a little faster (Madden's scheme-fit idea, decided here) |
| **Openness** (persona slider, traditional ↔ experimental) | Size of the scheme learning penalty when he arrives (§5.13): an experimental coach overhauls the playbook. A Scheme Nerd costs more in year one and raises his players' technical ceilings a little |
| **Workload** (0–1) | How hard he leans on his best players: an Old School coach with a workhorse back adds mileage fast (§5.8), so coaching choices can shorten careers |
| **Discipline style** (Disciplinarian ✕ Players' Coach) | Disciplinarian: removes Undisciplined and similar flags (§5.12), clashes with volatile players (form dips). Players' Coach: steadies form for low-confidence players (§5.11) |

**Staff traits at team level** (until a staff table exists): **strength and conditioning** sets the size of the first-two-seasons physical gain and slows wear a little; **medical** changes the odds and size of lasting injury damage (§5.8) and, with `LEAGUE_OFFICE_PLAN.md`, injury durations. Seeded per team, nudged by JR, and visible to GM agents as a hiring and spending factor.

* **Effects of hiring.** The coaching carousel (spec §2.19) and the hot seat can weigh development: a REBUILD team's GM agent prefers a teacher, and a coach whose groups regress loses standing. `dev_quality` gains the coaching term as an explained part of each team's score.
* **Careers as a coaching outcome.** With wear, continuity and mentors in the engine, a GM agent weighing a coach also weighs how long his stars will last: a workload-heavy coach wins now and wears out backs; frequent coordinator changes slow every young player.
* **Staff memory.** A coordinator who developed a QB gets credit in writers' tools and in his next job's introduction stories.
* **Seeding:** existing coaches' traits are seeded deterministically and can be nudged by JR before the first PC season; real-world coaches get traits consistent with their known reputations (the hallucination policy allows invented narrative, never contradicting data).

---

## 8. Phases

| Phase | What | Needs |
|---|---|---|
| **A. PC checks** | (1) XP sliders at 0 stop all in-season changes, weekly training included; which slider row is user and CPU. (2) At which offseason stage Madden ages and regresses players, and what `HistoryEntry` logs. (3) Madden keeps written ratings and recomputes OVR from them, or needs `OverallRating` written (migration plan §5.4 item 7). (4) How abilities (Superstar, X-Factor) are assigned with XP off. (5) Whether retirement chances depend on the ratings Project Mu writes. (6) Madden's morale (§5.11): that `ConfidenceRating` is morale, which value is neutral (all players at 50, then at 99), and how much morale changes play. (7) Madden keeps written `PT_*` flags and nothing else assigns them with XP off; which flags exclude each other (§5.12) | The PC, a test franchise |
| **B. First PC season** | Sabos with XP off; Madden's offseason regression left alone if the engine isn't ready, or a tool (§3) as a stopgap. Keep the `HistoryEntry` log of what Madden did | Phase A |
| **C. Engine, offline** | Complete (2026-10-28): `engines/progression.py` built with age, dev, ceiling, playing-time, injury and seeded noise terms, the per-age-band calibration, the league-shape term and the performance term. 2035→2036 backtest: mean ΔOVR −0.159 vs actual −0.147, MAE 1.134, every age band within 0.12 (34+ aside); `--simulate 5` holds the 2036 shape (§5.5). 15 tests in `tests/test_progression.py`. Tables on `madden_test` only. | `dev_quality`, aging fit, scouting |
| **C2. Revision, offline** | Before the PC: the three rating groups fitted to Madden's draft-day-to-now shapes (§5.4), durability and mileage (§5.8), ceiling bands and breakthroughs (§5.9), peak upkeep inside the league term (§5.10), scheme continuity and mentors (§5.13), performance for thin-stat positions with per-group reliability (§5.15), the `PT_*` gain-and-loss model as stored outcomes not yet written (§5.12), and the new columns and tables (§5.6). Backtest and `--simulate` must still pass. Trait and coach terms built at 0 | C, the sample save's `Original*Rating` and `PT_*` |
| **D. Engine in the save** | The offseason pass writes through `save_writer.mjs` (snapshot before Madden's rollover, write after it), stores `player_progression_history`, and runs a daily check that Madden changed nothing between passes. Writer tool `get_player_progression` | Phases A and C, migration §5 |
| **E. In-season checkpoints and form** | Checkpoints if D3 says yes; form (§5.11, D9 decided) with Madden's morale overridden every week, carried by a ratings overlay or by morale itself (D13, after phase A (6)) | D |
| **F. Player personality traits** | Traits table, seeding (Focus, `PT_*` and toughness as starting points), the trait multipliers of §6.1. Personas' voices (V2.13) | D (C2 for the terms offline), V2.13 |
| **G. Coach traits** | Coach and coordinator traits (including openness, workload, discipline style), team strength and medical staff, the coaching term, carousel and GM weighting | D, F (coachability) |
| **H. Dev traits, ceilings and behaviour traits fully owned** | Dev changes from §5.7; bands and breakthroughs move with events; `PT_*` changes written to the save | D, phase A (7) |

Phases F and G can start before D as data (traits seeded, shown to writers) with the terms at 0, so personalities appear in stories first and in the numbers once the engine writes.

---

## 9. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| D1 | Who owns ratings after the move: Project Mu (this plan) or Madden? | Project Mu, from the 2038 offseason at the latest |
| D2 | First PC season (2037): Madden's own progression, a community tool, or this engine? | Madden's own with XP off via Sabos, unless phase C is ready by the 2037 offseason |
| D3 | In-season checkpoints, or offseason only? | Start offseason only; add checkpoints in phase E. The weekly in-season write exists anyway for the league office, so the extra cost is small |
| D4 | How much weight performance gets against age and talent | Playing time and grade together about a quarter of a typical season's change |
| D5 | Can JR see ceilings or traits? | Ceilings never (like `true_value`); personality traits visible on player pages once seeded |
| D6 | Does Project Mu also take over retirements? | Not in this plan; Sabos's lower retirement chance for now, revisit after phase D |
| D7 | Can JR's own team's coaching choices affect his players? | Yes, the same rules as every team (JR's team is scored in `dev_quality` today) |
| D8 | Peaks: keep the 90+ count (league term) and let upkeep decide who holds the spots, or hard-cap underlying ratings at 93–95 with form for the rest? | **JR decided (2026-10-28): keep the count**, upkeep decides who (§5.10) |
| D9 | Form: build it at all? It rides on the weekly in-season write (§5.3) | **JR decided (2026-10-28): yes**, in phase E, replacing Madden's morale logic (§5.11) |
| D10 | Mental ratings: never decline, or decline last and slowest? | **JR decided (2026-10-28): last and slowest, at a rate set by the peak** (§5.4) |
| D11 | Rating ranges instead of numbers for young players on Project Mu's pages? | **JR decided (2026-10-28): dropped** (§5.14) |
| D13 | How form reaches the game: morale fixed for everyone (50 or 99) with a ratings overlay, or form written into morale itself? | **Open until the PC test.** JR's proposal is to fix morale and overlay; fix it at Madden's neutral value (probably 50, not 99). If morale's own effect tests sensible, writing form into morale is simpler (no second rating layer) |
| D12 | Dev trait under ceiling bands | **JR decided (2026-10-28): kept, as the visible summary of the hidden band** (§5.7) |

---

## 10. Spec changes when built

`player_progression_history` (built) and its new columns, `player_ceilings` (built) with floors, spreads and group ceilings, `player_durability`, `player_mileage`, player, coach and team-staff trait tables in spec §3; the engine in §2 and the run order (offseason pass after the rollover, before the draft board loads); the writer tool in §7; `dev_quality`'s coaching term; the new steps in the save watcher (migration plan §5.2).
