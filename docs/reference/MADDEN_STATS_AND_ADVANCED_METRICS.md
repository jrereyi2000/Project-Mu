# Madden 27 Player Stats & Advanced Analytics Reference

## 1. Overview & Architectural Context
Madden NFL (Madden 27 PC franchise save format, Frostbite schema `M27_620_0.gz`) maintains two distinct layers of statistical data:
1. **The Legacy Presentation Layer (Surfaced)**: The traditional box-score metrics surfaced in-game, on postgame recap menus, and across the franchise UI (dating back to legacy console architectures).
2. **The Telemetry & Next-Gen Engine Layer (Tracked Under the Hood)**: An extensive set of per-play tracking data recorded inside the franchise database, almost all of it on the `Career*Stats` rows rather than the `Game*Stats` / `Season*Stats` rows (see §2.3 and §3). This underlying layer tracks individual receiver targets, route participation, air yards, separation windows, defensive coverage targets/catches allowed, quarterback pressure events, pocket posture, throw depth splits, and defensive stops.

This reference provides a comprehensive catalog of what Madden tracks vs. surfaces, and defines the complete suite of advanced metrics derivable for Project Mu's database, analytics engines, fantasy platform (Napper), and newsroom.

---

## 2. Telemetry Architecture & Save Tables

Madden organizes player statistics across three primary temporal horizons in the franchise save file:

### 2.1 Per-Game Tables (`Game*Stats`)
Linked to individual `SeasonGame` records and the opposing `Team` reference. Maintained in the save file for current and recent weeks:
* `GameOffensiveStats` (inherits `GameStats`)
* `GameDefensiveStats` (inherits `GameStats`)
* `GameOLineStats` (inherits `GameStats`)
* `GameKickingStats` (inherits `GameStats`)
* `GameOffensiveKPReturnStats` (inherits `GameOffensiveStats`)
* `GameDefensiveKPReturnStats` (inherits `GameDefensiveStats`)

### 2.2 Season Totals (`Season*Stats`)
Cumulative seasonal totals per player, including franchise records, milestones, and seasonal context:
* `SeasonOffensiveStats`, `SeasonDefensiveStats`, `SeasonOLineStats`, `SeasonKickingStats`, `SeasonOffensiveKPReturnStats`, `SeasonDefensiveKPReturnStats`.
* Introduces situational counters such as `FIRSTDOWNS`, `4QCOMEBACKS`, `GAMEWINFGATTEMPTS`, and `GAMEWINFGSMADE`. All four are zero in every CPU-simmed season in the 2026 spike (§2.3); the `†` marker in §3.

### 2.3 Career & Next-Gen Telemetry (`Career*Stats`)
The permanent player historical ledger where Madden persists its Next Gen tracking metrics:
* `CareerOffensiveStats` (86 attributes): Tracks route-level, depth-level, pocket, and separation metrics.
* `CareerDefensiveStats` (58 attributes): Tracks coverage targets, catches allowed, pressure counts, alignments, and run stuffs.
* `CareerOLineStats`, `CareerKickingStats`, and the `*KPReturn` subclasses (`CareerOffensiveKPReturnStats`, `CareerDefensiveKPReturnStats`). A player's career row moves to a KPReturn subclass once he returns kicks; the tracking fields reset when it does.

**The tracking fields live only here.** Targets, routes, air yards, separation, passing depth splits, pocket and throw-location splits, blitz and scramble counters, coverage targets, catches allowed, pressures, stops, stuffs and downs played are not fields on `Game*Stats` or `Season*Stats`; the Game/Season rows hold the box-score counters, and the Career row holds those counters plus the tracking fields. The earlier version of this document put the tracking data on `Game*Stats`; that was wrong (§3 now marks where each field actually lives).

**Only differences mean anything.** The Career row carries real-NFL history for its box-score counters, but the tracking fields start near zero. In the 2026 spike, Josh Allen's depth splits summed to 539 attempts against 4,576 career attempts, and `DOWNSPLAYED` was 0 for every player at the franchise start. A career total on its own is therefore not a Project Mu season. Project Mu stores the raw Career row at every weekly load in `player_career_counters`; `player_week_counters` and `player_season_counters` subtract consecutive **loads** (ordered by `load_seq`) to give the week's and the season's counters. A row is labelled with the last played week its difference covers, not the save's current week, so a reload of the same week replaces it. A label is not proof of which games a difference covers: a bye or a skipped save leaves a difference that spans several games, so the engine places each difference against the team's stored games and a week query uses only a delta whose covered games are exactly that week (audit-5 S3). Loads outside a playable week use `stage` PRESEASON/OFFSEASON/PROBOWL with sentinel weeks 0/-1/-2. A difference is unknown (NULL, not negative) when the row changed Career table, `GAMESPLAYED` went down, or it spans a season boundary it cannot split (`CROSS_SEASON`); the season view counts those rows as `weeks_unknown`. A first career row whose season says games were played is `PARTIAL` and the season view reports its `games_lost`; a `NO_GAMES` row whose tracked counters still grew keeps that growth in the season total and is counted by `weeks_no_games`.

**Never filled in CPU-simmed games** (2026 spike; `†` in §3): `FIRSTDOWNS`, `4QCOMEBACKS`, `GAMERATING`, `GAMEWINFG*`, `INSIDERUN_*` / `OUTSIDERUN_*`, every `COVERAGEDEPTH_*`, every `DEFSHORT` / `DEFMEDIUM` / `DEFDEEP` pass split and every `DEFINSIDE` / `DEFOUTSIDE` run split, `DEFBLITZ_*`, and the passing depth (`SHORT` / `MEDIUM` / `DEEPPASS_*`), blitz (`BLITZ_*`) and scramble (`QBSCRAMBLE_*`) counters, which hold static real-NFL legacy totals. The separation buckets are aliased: `SEPARATION_TIGHT`, `_NORMAL` and `_OPEN` hold the same number in the save (all four buckets are equal for one QB), so they are not a usable per-bucket distribution. These fields are still stored raw, so they appear without a code change if Madden ever fills them.

---

## 3. Data Dictionary: Surfaced vs. Tracked Under the Hood

**Table legend** (2026 spike, save schema `M27_620_0`):
- `[G/Season]` — a box-score counter present on the Game and Season rows; Project Mu reads it through `game_stat_lines` / `season_stat_lines`.
- `[Season]` — Season rows only.
- `[Career]` — on the player's Career row only. Project Mu stores the raw Career row in `player_career_counters` and reads differences through `player_week_counters` / `player_season_counters` (§2.3).
- `†` — never filled in CPU-simmed games; stored raw, no metric uses it.
- `‡` — present on the Career row but static: holds real-NFL legacy totals and does not grow in the sim.
- `•` — grows, but does not mean what the name suggests (`ROUTESRUN` grows about 0.72 per target, not routes run).

### 3.1 Wide Receivers & Tight Ends
| Metric Category | Surfaced in In-Game Box Score | Tracked in Underlying Engine | Schema Attributes |
| :--- | :--- | :--- | :--- |
| **Volume** | Receptions (`REC`), Receiving Yards (`YDS`), Receiving Touchdowns (`TD`), Long (`LNG`) | **Targets**: Exact targets per player.<br>**Routes Run**: Routes executed on passing downs.<br>**First Downs**: Receiving first downs earned. | `RECEIVECATCHES`, `RECEIVEYARDS`, `RECEIVETDS`, `RECEIVELONGEST` `[G/Season]`; `TARGETS` `[Career]`; `ROUTESRUN` `[Career] •`; `FIRSTDOWNS` `[Season] †` |
| **Efficiency** | Yards Per Catch (`AVG` / `YPC`), Yards After Catch (`RAC`), Drops (`DROP`) | **Air Yards**: Throw distance prior to catch.<br>**Drop Count**: Incompleteness attributed to dropped passes. | `RECEIVEYARDSAFTER` `[G/Season]`; `RECEIVEDROPS` `[G/Season]`; `AIRYARDS` `[Career]` (passer only: not a per-receiver air-yards counter) |
| **Separation** | *None* | **Separation Windows**: Frequency of target separation:<br>• Tight Window / Contested<br>• Normal Separation<br>• Open Separation<br>• Wide Open Separation | `SEPARATION_TIGHT`<br>`SEPARATION_NORMAL`<br>`SEPARATION_OPEN`<br>`SEPARATION_WIDEOPEN` — all `[Career]`, passer-side; `TIGHT` / `NORMAL` / `OPEN` are aliased in the save |

### 3.2 Quarterbacks & Passing
| Metric Category | Surfaced in In-Game Box Score | Tracked in Underlying Engine | Schema Attributes |
| :--- | :--- | :--- | :--- |
| **Traditional Passing** | Attempts (`ATT`), Completions (`COMP`), Yards (`YDS`), TDs (`TD`), Interceptions (`INT`), Sacks Taken (`SACK`), Long (`LNG`), Completion %, YPA, Passer Rating | **Pressures Absorbed**: Pass rushes disrupting the pocket.<br>**4th Quarter Comebacks**: Comeback game drives.<br>**First Downs**: Passing chains moved. | `PASSATTEMPTS`, `PASSCOMPLETED`, `PASSYARDS`, `PASSTDS`, `PASSINTS`, `PASSSACKED`, `PASSLONGEST` `[G/Season]`; `PRESSURES` `[Career]`; `4QCOMEBACKS` `[Season] †`; `FIRSTDOWNS` `[Season] †` |
| **Passing Depth Splits** | *None* | Stratified by depth tier (Attempts, Completions, Yards, TDs, INTs):<br>• **Short**: 0–10 air yards<br>• **Medium**: 11–19 air yards<br>• **Deep**: 20+ air yards | `SHORTPASS_*`<br>`MEDIUMPASS_*`<br>`DEEPPASS_*` — all `[Career] ‡` |
| **Pocket & Platform** | *None* | Stratified by quarterback posture:<br>• **Inside Pocket**: On-platform throws from pocket<br>• **Outside Pocket**: Bootlegs, rollouts, scrambles | `INSIDEPOCKETTHROW_*`<br>`OUTSIDEPOCKETTHROW_*` — all `[Career]`, computable |
| **Directional Spray** | *None* | Target placement across field thirds: Left, Middle, Right | `THROWLOCATION_LEFT`<br>`THROWLOCATION_MIDDLE`<br>`THROWLOCATION_RIGHT` — all `[Career]`, computable |
| **Blitz Performance** | *None* | Plays, completions, yards, TDs, and INTs against extra rushers | `BLITZ_PLAYS`, `BLITZ_COMPLETIONS`, `BLITZ_YARDS`, `BLITZ_TD`, `BLITZ_INTERCEPTIONS` — all `[Career] ‡` |
| **Rushing vs Scramble** | Total Rushing Yards & Carries | Dedicated scramble tracking separating designed runs from tuck-and-run plays | `QBSCRAMBLE_CARRIES`<br>`QBSCRAMBLE_YARDS`<br>`QBSCRAMBLE_TD` — all `[Career] ‡` |

### 3.3 Running Backs & Rushing
| Metric Category | Surfaced in In-Game Box Score | Tracked in Underlying Engine | Schema Attributes |
| :--- | :--- | :--- | :--- |
| **Volume & Creation** | Carries (`ATT`), Yards (`YDS`), Touchdowns (`TD`), Long (`LNG`), Broken Tackles (`BTK`), Yards After First Hit (`YAC`), Fumbles (`FUM`), 20+ Yard Runs | **Explosive Runs**: 10+ yard runs.<br>**Rushing Success**: Positive-EPA rushing plays.<br>**First Downs**: Chains moved on the ground. | `RUSHATTEMPTS`, `RUSHYARDS`, `RUSHTDS`, `RUSHLONGEST`, `RUSHBROKENTACKLES`, `RUSHYARDSAFTER1STHIT`, `RUSHFUMBLES`, `RUSH10YARDRUNS`, `RUSH20YARDRUNS` `[G/Season]`; `RUSHSUCCESS` `[Career]`; `FIRSTDOWNS` `[Season] †` |
| **Run Concepts & Direction** | *None* | Concept breakdown & directional path:<br>• **Inside Run**: Carries, Yards, TDs, Run Left vs Run Right<br>• **Outside Run**: Carries, Yards, TDs, Run Left vs Run Right | `INSIDERUN_CARRIES`, `_YARDS`, `_TD`, `_RUNLEFT`, `_RUNRIGHT`<br>`OUTSIDERUN_CARRIES`, `_YARDS`, `_TD`, `_RUNLEFT`, `_RUNRIGHT` — all `[Career] †` |
| **Defensive Front** | *None* | Box density on carry:<br>• **Light Box** (≤6 in the box)<br>• **Loaded Box** (≥8 in the box) | `LIGHTBOX`<br>`LOADEDBOX` — `[Career]` |

### 3.4 Defense & Pass Rush
| Metric Category | Surfaced in In-Game Box Score | Tracked in Underlying Engine | Schema Attributes |
| :--- | :--- | :--- | :--- |
| **Tackles & Havoc** | Total Tackles (`TAK`), Solo (`SOLO`), Assists (`AST`), Tackles For Loss (`TFL`), Sacks (`SACK`), INTs, Return Yds/TD, Pass Deflections (`PD`), Forced Fumbles (`FF`), Fumble Recoveries (`FR`), Return Yds/TD, Blocks (`BLK`), Safeties | **Big Hits**: Hit-stick and momentum collision tackles.<br>**Defensive Stops**: Tackles resulting in offensive failure.<br>**Run Stuffs**: Tackles at or behind the line on run plays. | `DEFTACKLES`, `ASSDEFTACKLES`, `DEFTACKLESFORLOSS`, `DLINESACKS`, `DLINEHALFSACK`, `DSECINTS`, `DSECINTRETURNYARDS`, `DSECINTTDS`, `DEFPASSDEFLECTIONS`, `DLINEFORCEDFUMBLES`, `DLINEFUMBLERECOVERIES`, `DLINEBLOCKS`, `DLINESAFETIES`, `BIGHITS` `[G/Season]`; `STOPS`, `STUFFS` `[Career]` |
| **Coverage Allowed** | *None* | Per-defender coverage accountability:<br>• **Catches Allowed**: Receptions conceded in coverage<br>• **Targets Allowed**: Times targeted as primary defender<br>• **Passes Defended / INTs**: On targeted balls | `CTHALLOWED` `[G/Season] [Career]`; `DEFTARGETS` `[Career]`; `DEFPASSDEFLECTIONS`, `DSECINTS` `[G/Season]` |
| **Pass Rush Pressure** | Sacks | Total quarterback disruptions and pressures generated | `DEFPRESSURES` `[Career]`; `DLINESACKS` `[G/Season]` |
| **Coverage Alignment** | *None* | Snaps deployed in Press, Normal, or Off coverage depth | `COVERAGEDEPTH_PRESS`<br>`COVERAGEDEPTH_NORMAL`<br>`COVERAGEDEPTH_OFF` — all `[Career] †` |
| **Coverage Depth Allowed** | *None* | Stratified by depth tier allowed (Short, Medium, Deep) | `DEFSHORTPASS_*`<br>`DEFMEDIUMPASS_*`<br>`DEFDEEPPASS_*` — all `[Career] †` |

### 3.5 Offensive Line & Blocking
| Metric Category | Surfaced in In-Game Box Score | Tracked in Underlying Engine | Schema Attributes |
| :--- | :--- | :--- | :--- |
| **Protection & Blocking** | Pancake Blocks (`PANCAKE`), Sacks Allowed (`SACKS ALLOWED`) | Individual offensive line snap counts and starting assignment | `OLINEPANCAKES`, `OLINESACKSALLOWED` `[G/Season]`; `DOWNSPLAYED`, `GAMESSTARTED` `[G/Season] [Career]` |

### 3.6 Special Teams & General Telemetry
| Metric Category | Surfaced in In-Game Box Score | Tracked in Underlying Engine | Schema Attributes |
| :--- | :--- | :--- | :--- |
| **Kicking & Punting** | FGM/FGA, Long, Blocked, Distance splits (1–29, 30–39, 40–49, 50+), XPM/XPA/Blocked, Punts, Punt Yds/Net/In20/TB/Long/Blocked, Kick/Punt Return Att/Yds/TD/Long | **Kickoff Volume**: Total kickoffs booted.<br>**Touchback Volume**: Kickoff touchbacks achieved.<br>**Game-Winning Field Goals**: Attempts and conversions. | The kicking, punting and return counters `[G/Season]`; `KICKNUMKICKOFFS`, `KICKTOUCHBACKS` `[G/Season]`; `GAMEWINFGATTEMPTS`, `GAMEWINFGSMADE` `[Season] †` |
| **General Tracking** | *None* | **Downs Played**: Exact snap counts for all 22 positions.<br>**Game Rating**: Internal 0–100 performance grade.<br>**Starter Status**: Credit for starting drive. | `DOWNSPLAYED` `[G/Season] [Career]`; `GAMERATING` `[G/Season] [Career] †`; `GAMESSTARTED` `[G/Season] [Career]` |

**What Project Mu stores.** The six counters the loader used to drop — `RECEIVEDROPS`, `RUSH20YARDRUNS`, `BIGHITS`, `CTHALLOWED`, `OLINEPANCAKES`, `GAMESSTARTED` — are now stored on both the game and season rows (`rec_drops`, `rush_20plus`, `big_hits`, `catches_allowed`, `pancakes`, `games_started`), and `DOWNSPLAYED` is kept per game as well as per season. Everything marked `[Career]` is ingested raw into `player_career_counters` at every weekly load; the typed columns and differences come from `player_week_counters` / `player_season_counters`. Nothing marked `[Career]` is written to `player_stats` or `game_stat_*`, because the Game and Season rows do not have those fields.

---

## 4. Derivable Advanced Analytics Suite

Combining Madden's surfaced counters with its hidden engine telemetry enables the computation of industry-standard analytics. Each metric carries a **status**, matching the ingest plan §2.5 and `engines/advanced_metrics.py`:

- **OK** — computable now from the stored counters. `engines/advanced_metrics.py` computes it.
- **BLOCKED** — waiting on what a counter actually means (`ROUTESRUN`), or the measured counters contradict the metric's premise (audit M1).
- **IMPOSSIBLE** — Madden does not record the inputs.

Rates below are fractions (0–1), not percentages. A missing input or a zero denominator yields `None`, never 0.

Both sides of every rate are built from the same games (audit H3): the engine sums the `delta` JSONB on `player_week_counters`, so the full box counters (`RECEIVECATCHES`, `TARGETS`, `PASSATTEMPTS`, `PASSSACKED`, `RUSHATTEMPTS`, `DLINESACKS`, `DSECINTS`, `RECEIVEDROPS`, ...) stay on one side over the same tracked weeks. BASELINE, PARTIAL, TABLE_CHANGED, RESET and CROSS_SEASON rows are never used, and a week whose team inside/outside-pocket attempts do not account for its `PASSATTEMPTS` growth is treated as untracked and excluded, counted in `weeks_unknown` like an unknown week. A week query keeps only a delta whose covered games are exactly its game; a multi-game delta that cannot be split leaves the week to its box score, with the tracked inputs `None` and a reason in `coverage.reasons` (audit-5 S3). The stat line supplies only a field the Career row does not carry (kicking and special teams); a rate that would mix the two sources over different game counts is `None`, and `player_metrics`' `coverage` reports why. Team denominators are summed from `team_stats` over the same game weeks as the numerator, and used only when the team's stored games and boxes cover exactly the games the numerator covers; otherwise the denominator inputs are `None` and `coverage.team_denominator` names the missing games or boxes (audit-5 S1). A delta whose receptions exceed its targets is named in `coverage.invalid_inputs`, and target-based rates are `None` — never clamped to 1 (audit-5 S4).

| Status | Metrics |
| :--- | :--- |
| **OK** | 4.1.3 Target share; 4.1.5 QB aDOT (unverified); 4.1.7 Catch rate, Drop rate; 4.2.1 P2S; 4.2.2 Pressure rate; 4.2.5 Pocket split; 4.3.1 Rush success; 4.3.2 YAC/Att; 4.3.3 BTK rate; 4.3.5 Loaded/light box rate; 4.4.5 Stop rate and `stuff_rate_all_snaps`; 4.5.4 TB % |
| **BLOCKED** | 4.1.1 TPRR; 4.1.2 YPRR; 4.1.4 Route participation — `ROUTESRUN` is not routes run (spike S22). 4.3.4 Explosive rate (`RUSH10YARDRUNS` is lower than `RUSH20YARDRUNS`); 4.4.1 `coverage_rating_lite`, 4.4.2 Burn % (`DEFTARGETS` is more than triple the league's targets); 4.4.4 Sack conversion (`DLINESACKS` exceeds `DEFPRESSURES`); 4.5.3 Snap share (`DOWNSPLAYED` exceeds the team's plays) — audit M1 |
| **IMPOSSIBLE** | 4.1.5 receiver aDOT and 4.1.6 RACR (air yards is a passer stat); 4.1.8 Separation index (passer-side, aliased buckets); 4.2.3 Deep rate; 4.2.4 Depth-stratified efficiency; 4.2.6 Blitz rating; 4.2.7 Scramble rate; 4.3.6 Inside/outside concept split; 4.4.1 full coverage rating; 4.4.3 PRate; 4.4.5 Stuff rate per run-defense snap; 4.4.6 Press frequency; 4.5.1 Pass-block sack rate; 4.5.2 Pancake rate |

### 4.1 Wide Receiver & Tight End Metrics
1. **Targets Per Route Run (TPRR)**:
   $$\text{TPRR} = \frac{\text{TARGETS}}{\text{ROUTESRUN}}$$
   *Significance*: Evaluates target demand independent of offensive run/pass ratio. League elite thresholds sit above $25\%$.
   *Status: BLOCKED — `ROUTESRUN` grows about 0.72 per target and about 5% of `DOWNSPLAYED`; it is not routes run (spike S22 / plan R2).*
2. **Yards Per Route Run (YPRR)**:
   $$\text{YPRR} = \frac{\text{RECEIVEYARDS}}{\text{ROUTESRUN}}$$
   *Significance*: The premier per-snap receiving efficiency metric. A value $> 2.20$ denotes WR1 efficiency.
   *Status: BLOCKED — same reason: no real routes-run count.*
3. **Target Share**:
   $$\text{Target Share} = \frac{\text{TARGETS}}{\text{Team Pass Attempts}}$$
   *Status: OK — `targets / team_pass_attempts`, with the team total summed from `team_stats` over the same game weeks as the Career `TARGETS`. The share is `None` when the stored team games and boxes do not cover exactly the numerator's games (audit-5 S1).*
4. **Route Participation Rate**:
   $$\text{Route Participation} = \frac{\text{ROUTESRUN}}{\text{Team Dropbacks}}$$
   *Status: BLOCKED — same `ROUTESRUN` problem (spike S22).*
5. **Average Depth of Target (aDOT), quarterback**:
   $$\text{aDOT}_{QB} = \frac{\text{AIRYARDS}}{\text{PASSATTEMPTS}}$$
   *Significance*: Measures how far downfield a quarterback's attempts travel, independent of completion.
   *Status: OK, unverified — `qb_adot`, air yards and pass attempts both from the same Career delta. `AIRYARDS` averages about 5.5 per attempt over the 2026 season against a real intended aDOT near 8, so what the counter means is still a suspicion (audit).*
   The receiver version, $\text{aDOT} = \text{AIRYARDS} / \text{TARGETS}$, is **IMPOSSIBLE**: Madden records `AIRYARDS` only on the passer's Career row, never per receiver (spike S9). It stays in the catalog as `rec_adot` marked IMPOSSIBLE.
6. **Receiver Air Conversion Ratio (RACR)**:
   $$\text{RACR} = \frac{\text{RECEIVEYARDS}}{\text{AIRYARDS}}$$
   *Significance*: Measures how effectively intended downfield air yards are converted into net yardage.
   *Status: IMPOSSIBLE — air yards is a passer counter, so there is no per-receiver air-yards denominator (spike S9).*
7. **True Catch Rate & True Drop Rate**:
   $$\text{Catch Rate} = \frac{\text{RECEIVECATCHES}}{\text{TARGETS}}, \quad \text{Drop Rate} = \frac{\text{RECEIVEDROPS}}{\text{RECEIVECATCHES} + \text{RECEIVEDROPS}}$$
   *Status: OK — catches, targets and drops all from the same Career delta (`RECEIVECATCHES`, `TARGETS`, `RECEIVEDROPS`), so no game is counted on one side only. A delta with more receptions than targets publishes no catch rate (`None`, never clamped), and `coverage.invalid_inputs` names it (audit-5 S4).*
8. **Separation Index**:
   $$\text{Open Rate} = \frac{\text{SEPARATION\_OPEN} + \text{SEPARATION\_WIDEOPEN}}{\text{TARGETS}}, \quad \text{Contested Rate} = \frac{\text{SEPARATION\_TIGHT}}{\text{TARGETS}}$$
   *Status: IMPOSSIBLE — separation is a passer-side count, and the `TIGHT` / `NORMAL` / `OPEN` buckets are aliased in the save (spike S9, S21).*

### 4.2 Quarterback Metrics
1. **Pressure-to-Sack Rate (P2S)**:
   $$\text{P2S} = \frac{\text{PASSSACKED}}{\text{PRESSURES}}$$
   *Significance*: Disentangles offensive line fault from quarterback pocket presence. High P2S ($> 20\%$) indicates pocket holding and inability to navigate pressure.
   *Status: OK — `PASSSACKED` and `PRESSURES` both from the same Career delta.*
2. **Pressure Rate Faced**:
   $$\text{Pressure Rate} = \frac{\text{PRESSURES}}{\text{PASSATTEMPTS} + \text{PASSSACKED}}$$
   *Status: OK — all three from the same Career delta.*
3. **Deep Ball Attempt Rate**:
   $$\text{Deep Rate} = \frac{\text{DEEPPASS\_ATTEMPTS}}{\text{PASSATTEMPTS}}$$
   *Status: IMPOSSIBLE — `SHORT` / `MEDIUM` / `DEEPPASS_*` never grow; they hold real-NFL legacy totals (spike S19).*
4. **Depth-Stratified Efficiency**:
   Separate Passer Rating, Completion %, and Yards Per Attempt calculated across:
   * **Short** ($0–10\text{ yds}$): Screen and rhythm execution.
   * **Medium** ($11–19\text{ yds}$): NFL intermediate window passing.
   * **Deep** ($20+\text{ yds}$): Downfield explosive ball-placement.
   *Status: IMPOSSIBLE — the depth-split counters are static legacy totals (spike S19).*
5. **Platform Disparity (In-Pocket vs. Off-Platform)**:
   Comparison of efficiency metrics between `INSIDEPOCKETTHROW_*` and `OUTSIDEPOCKETTHROW_*`.
   $$\text{Pocket Split} = \text{Passer Rating}_{\text{in pocket}} - \text{Passer Rating}_{\text{outside pocket}}$$
   *Status: OK — both partitions come from the same Career delta, so the two ratings and their difference are real. Ratio the counters against themselves; their sum is about `PASSATTEMPTS`, not exactly it (spike S20). A week whose team pocket attempts do not account for its `PASSATTEMPTS` growth is treated as untracked and excluded.*
6. **Blitz Resilience Rating**:
   Passer rating and turnover rate specifically under blitz looks (`BLITZ_*`).
   *Status: IMPOSSIBLE — `BLITZ_*` never grows; it holds real-NFL legacy totals (spike S19).*
7. **True Emergency Scramble Rate**:
   $$\text{Scramble Rate} = \frac{\text{QBSCRAMBLE\_CARRIES}}{\text{Total Dropbacks}}$$
   *Status: IMPOSSIBLE — `QBSCRAMBLE_*` never grows; it holds real-NFL legacy totals (spike S19).*

### 4.3 Running Back Metrics
1. **Rushing Success Rate**:
   $$\text{Success Rate} = \frac{\text{RUSHSUCCESS}}{\text{RUSHATTEMPTS}}$$
   *Significance*: Measures situational down-to-down consistency rather than aggregate volume.
   *Status: OK — `RUSHSUCCESS` and `RUSHATTEMPTS` both from the same Career delta.*
2. **Yards After Contact Per Attempt (YAC/Att)**:
   $$\text{YAC/Att} = \frac{\text{RUSHYARDSAFTER1STHIT}}{\text{RUSHATTEMPTS}}$$
   *Significance*: Isolates individual creation ability from offensive line push.
   *Status: OK.*
3. **Elusiveness / Broken Tackle Rate**:
   $$\text{BTK Rate} = \frac{\text{RUSHBROKENTACKLES}}{\text{RUSHATTEMPTS}}$$
   *Status: OK.*
4. **Explosive Run Rate**:
   $$\text{Explosive Rate} = \frac{\text{RUSH10YARDRUNS} + \text{RUSH20YARDRUNS}}{\text{RUSHATTEMPTS}}$$
   *Status: BLOCKED — the counters contradict the metric: `RUSH10YARDRUNS` (179) is lower than `RUSH20YARDRUNS` (306) over the 2026 season, and 34 of the 90 backs with a 20+ run have no 10+ run, so the buckets do not nest (audit M1).*
5. **Loaded Box Rate & Front Adaptation**:
   $$\text{Loaded Box Rate} = \frac{\text{LOADEDBOX}}{\text{RUSHATTEMPTS}}, \quad \text{Light Box Rate} = \frac{\text{LIGHTBOX}}{\text{RUSHATTEMPTS}}$$
   Examines whether production is sustained against stacked 8+ man fronts or aided by light fronts.
   *Status: OK — `loaded_box` and `light_box` from the Career delta.*
6. **Inside vs Outside Concept Efficiency**:
   Compares YPC and success between interior runs (`INSIDERUN_*`) and perimeter sweeps (`OUTSIDERUN_*`).
   *Status: IMPOSSIBLE — `INSIDE` / `OUTSIDERUN_*` never fill (spike S10).*

### 4.4 Defensive & Secondary Metrics
1. **Passer Rating Allowed in Coverage**:
   Calculated directly for individual cornerbacks and safeties using `DEFTARGETS`, `CTHALLOWED`, depth completions/yards/TDs, and `DSECINTS`.
   *Status: IMPOSSIBLE as specified — coverage yards and coverage TDs are not recorded. The shipped approximation is `coverage_rating_lite`, now **BLOCKED**: `DEFTARGETS` (48,001) is more than triple the league's targets (14,117) and receptions (10,124) while `CTHALLOWED` is 10,003, so the pair cannot be a catch rate (audit M1).*
2. **Catch Rate Allowed (Burn %)**:
   $$\text{Burn \%} = \frac{\text{CTHALLOWED}}{\text{DEFTARGETS}}$$
   *Significance*: Primary benchmark for lockdown cornerback performance.
   *Status: BLOCKED — same counter contradiction as `coverage_rating_lite`: `DEFTARGETS` is more than triple the league's targets, so the ratio is not a catch rate (audit M1).*
3. **Pass Rush Pressure Rate (PRate)**:
   $$\text{PRate} = \frac{\text{DEFPRESSURES}}{\text{Pass Rush Snaps}}$$
   *Significance*: $3\times$ more predictive of future defensive disruption than raw sack totals.
   *Status: IMPOSSIBLE — Madden does not split snaps by type, so there is no pass-rush snap count.*
4. **Sack Conversion Rate**:
   $$\text{Conversion Rate} = \frac{\text{DLINESACKS} + 0.5 \times \text{DLINEHALFSACK}}{\text{DEFPRESSURES}}$$
   *Status: BLOCKED — the counters contradict the premise: `DLINESACKS` (1,305, or 1,363 with the half sacks) exceeds `DEFPRESSURES` (732) over the 2026 season, so the pair cannot be a sack-per-pressure rate (audit M1).*
5. **Defensive Stop Rate & Stuff Rate**:
   $$\text{Stop Rate} = \frac{\text{STOPS}}{\text{DOWNSPLAYED}}, \quad \text{Stuff Rate} = \frac{\text{STUFFS}}{\text{Run Defense Snaps}}$$
   *Status: Stop Rate **OK** — `STOPS` and `DOWNSPLAYED` from the same Career delta. Stuff Rate **IMPOSSIBLE** per run-defense snap (Madden does not split snaps by type); the computable approximation ships as `stuff_rate_all_snaps` = `STUFFS / DOWNSPLAYED` (**OK**, but it is not the doc's per-run-snap rate).*
6. **Press Technique Frequency & Success**:
   $$\text{Press Frequency} = \frac{\text{COVERAGEDEPTH\_PRESS}}{\text{Total Coverage Snaps}}$$
   *Status: IMPOSSIBLE — `COVERAGEDEPTH_*` never fills (spike S10), and there is no coverage-snap count.*

### 4.5 Offensive Line & Playing Time Metrics
1. **Pass Blocking Sack Rate**:
   $$\text{Sack Rate Allowed} = \frac{\text{OLINESACKSALLOWED}}{\text{Pass Block Snaps}}$$
   *Status: IMPOSSIBLE — Madden does not split snaps by type, so there is no pass-block snap count.*
2. **Pancake Block Rate**:
   $$\text{Pancake Rate} = \frac{\text{OLINEPANCAKES}}{\text{Run Block Snaps}}$$
   *Status: IMPOSSIBLE — same reason: no run-block snap count.*
3. **True Snap Share**:
   $$\text{Snap Share} = \frac{\text{DOWNSPLAYED}}{\text{Total Team Snaps}}$$
   *Significance*: Universal playing-time share across all 22 offensive, defensive, and special teams positions.
   *Status: BLOCKED — the counters contradict the premise: `DOWNSPLAYED` over the team's total plays exceeds 1 for real players (J. Horn 193/172 = 1.12), so they do not share a denominator (audit M1).*
4. **Touchback Percentage**:
   $$\text{TB \%} = \frac{\text{KICKTOUCHBACKS}}{\text{KICKNUMKICKOFFS}}$$
   *Status: OK — both counters from the kicking Career row, or from the stat line when the player has no Career kicking row; the same games either way.*

---

## 5. Integration with Project Mu Systems

| Project Mu Engine / Feature | Relevant Tracked Metrics | System Enhancement |
| :--- | :--- | :--- |
| **Pipeline Loader (`pipeline/load_save.py`)** | Box-score counters from the Game/Season rows; every numeric Career field | **Game/Season rows → per-game and per-season columns:** `RECEIVEDROPS` → `rec_drops`, `RUSH20YARDRUNS` → `rush_20plus`, `BIGHITS` → `big_hits` (defense), `CTHALLOWED` → `catches_allowed` (defense), `OLINEPANCAKES` → `pancakes` (blocking), `GAMESSTARTED` → `games_started` (general), plus `DOWNSPLAYED` → `downs_played`. **Career row → `player_career_counters`:** one raw JSONB row per player per weekly load, keyed by the last played week it covers plus a `stage` (WEEK / PRESEASON / OFFSEASON / PROBOWL); `player_week_counters` / `player_season_counters` derive the differences and typed counters, credited to the season the games belong to. Targets, routes, air yards, separation, depth/pocket splits and coverage fields are **not** ingested into `player_stats` / `game_stat_*`: Madden does not put them on those rows. |
| **Excitement Index ($E$)** | `4QCOMEBACKS`, `BIGHITS`, `QBSCRAMBLE_*`, `GAMEWINFGSMADE` | Granular momentum, play violence, and comeback scoring weights. |
| **Napper Fantasy Platform** | `TARGETS`, `ROUTESRUN`, `TPRR`, `YAC/Att`, `Snap Share` | Upgrades projections from raw touches to underlying opportunity volume and target-earning metrics. |
| **Agent Wire & Newsroom** | `CTHALLOWED` (CB coverage), `P2S` (QB pocket collapse), `SEPARATION_*` | Contextual story prompts: calling out blown coverage assignments, QB holding the ball, or lockdown shadow corners. |
| **Command Center Front Office** | `PRate`, `YPRR`, `P2S`, `OLINESACKSALLOWED` | Evaluates true market value in contract extensions independent of raw box-score inflation. |

**Status note:** the tracked-metric column lists what each system could use once the metrics exist. Today §4 marks `ROUTESRUN` / `TPRR` / `YPRR` BLOCKED, the five audit-M1 counters (explosive rate, `coverage_rating_lite`, burn %, sack conversion, snap share) BLOCKED, and `4QCOMEBACKS`, `GAMEWINFG*`, `QBSCRAMBLE_*`, `SEPARATION_*` and `PRate` IMPOSSIBLE, so those entries are not usable yet. Usable now: `TARGETS` (through the Career delta), `CTHALLOWED`, `BIGHITS`, `P2S`, `YAC/Att` and the rest of the OK metrics in §4.
