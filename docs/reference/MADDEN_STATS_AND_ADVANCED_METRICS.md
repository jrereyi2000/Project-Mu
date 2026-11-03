# Madden 27 Player Stats & Advanced Analytics Reference

## 1. Overview & Architectural Context
Madden NFL (Madden 27 PC franchise save format, Frostbite schema `M27_620_0.gz`) maintains two distinct layers of statistical data:
1. **The Legacy Presentation Layer (Surfaced)**: The traditional box-score metrics surfaced in-game, on postgame recap menus, and across the franchise UI (dating back to legacy console architectures).
2. **The Telemetry & Next-Gen Engine Layer (Tracked Under the Hood)**: An extensive set of per-play tracking data recorded inside the franchise database (`Game*Stats`, `Season*Stats`, and `Career*Stats` tables). This underlying layer tracks individual receiver targets, route participation, air yards, separation windows, defensive coverage targets/catches allowed, quarterback pressure events, pocket posture, throw depth splits, and defensive stops.

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
* Introduces situational counters such as `FIRSTDOWNS`, `4QCOMEBACKS`, `GAMEWINFGATTEMPTS`, and `GAMEWINFGSMADE`.

### 2.3 Career & Next-Gen Telemetry (`Career*Stats`)
The permanent player historical ledger where Madden persists its Next Gen tracking metrics:
* `CareerOffensiveStats` (86 attributes): Tracks route-level, depth-level, pocket, and separation metrics.
* `CareerDefensiveStats` (58 attributes): Tracks coverage targets, catches allowed, pressure counts, alignments, and run stuffs.
* `CareerOLineStats`, `CareerKickingStats`.

---

## 3. Data Dictionary: Surfaced vs. Tracked Under the Hood

### 3.1 Wide Receivers & Tight Ends
| Metric Category | Surfaced in In-Game Box Score | Tracked in Underlying Engine | Schema Attributes |
| :--- | :--- | :--- | :--- |
| **Volume** | Receptions (`REC`), Receiving Yards (`YDS`), Receiving Touchdowns (`TD`), Long (`LNG`) | **Targets**: Exact targets per player.<br>**Routes Run**: Routes executed on passing downs.<br>**First Downs**: Receiving first downs earned. | `RECEIVECATCHES`, `RECEIVEYARDS`, `RECEIVETDS`, `RECEIVELONGEST`, `TARGETS`, `ROUTESRUN`, `FIRSTDOWNS` |
| **Efficiency** | Yards Per Catch (`AVG` / `YPC`), Yards After Catch (`RAC`), Drops (`DROP`) | **Air Yards**: Throw distance prior to catch.<br>**Drop Count**: Incompleteness attributed to dropped passes. | `RECEIVEYARDSAFTER`, `RECEIVEDROPS`, `AIRYARDS` |
| **Separation** | *None* | **Separation Windows**: Frequency of target separation:<br>• Tight Window / Contested<br>• Normal Separation<br>• Open Separation<br>• Wide Open Separation | `SEPARATION_TIGHT`<br>`SEPARATION_NORMAL`<br>`SEPARATION_OPEN`<br>`SEPARATION_WIDEOPEN` |

### 3.2 Quarterbacks & Passing
| Metric Category | Surfaced in In-Game Box Score | Tracked in Underlying Engine | Schema Attributes |
| :--- | :--- | :--- | :--- |
| **Traditional Passing** | Attempts (`ATT`), Completions (`COMP`), Yards (`YDS`), TDs (`TD`), Interceptions (`INT`), Sacks Taken (`SACK`), Long (`LNG`), Completion %, YPA, Passer Rating | **Pressures Absorbed**: Pass rushes disrupting the pocket.<br>**4th Quarter Comebacks**: Comeback game drives.<br>**First Downs**: Passing chains moved. | `PASSATTEMPTS`, `PASSCOMPLETED`, `PASSYARDS`, `PASSTDS`, `PASSINTS`, `PASSSACKED`, `PASSLONGEST`, `PRESSURES`, `4QCOMEBACKS`, `FIRSTDOWNS` |
| **Passing Depth Splits** | *None* | Stratified by depth tier (Attempts, Completions, Yards, TDs, INTs):<br>• **Short**: 0–10 air yards<br>• **Medium**: 11–19 air yards<br>• **Deep**: 20+ air yards | `SHORTPASS_*`<br>`MEDIUMPASS_*`<br>`DEEPPASS_*` |
| **Pocket & Platform** | *None* | Stratified by quarterback posture:<br>• **Inside Pocket**: On-platform throws from pocket<br>• **Outside Pocket**: Bootlegs, rollouts, scrambles | `INSIDEPOCKETTHROW_*`<br>`OUTSIDEPOCKETTHROW_*` |
| **Directional Spray** | *None* | Target placement across field thirds: Left, Middle, Right | `THROWLOCATION_LEFT`<br>`THROWLOCATION_MIDDLE`<br>`THROWLOCATION_RIGHT` |
| **Blitz Performance** | *None* | Plays, completions, yards, TDs, and INTs against extra rushers | `BLITZ_PLAYS`, `BLITZ_COMPLETIONS`, `BLITZ_YARDS`, `BLITZ_TD`, `BLITZ_INTERCEPTIONS` |
| **Rushing vs Scramble** | Total Rushing Yards & Carries | Dedicated scramble tracking separating designed runs from tuck-and-run plays | `QBSCRAMBLE_CARRIES`<br>`QBSCRAMBLE_YARDS`<br>`QBSCRAMBLE_TD` |

### 3.3 Running Backs & Rushing
| Metric Category | Surfaced in In-Game Box Score | Tracked in Underlying Engine | Schema Attributes |
| :--- | :--- | :--- | :--- |
| **Volume & Creation** | Carries (`ATT`), Yards (`YDS`), Touchdowns (`TD`), Long (`LNG`), Broken Tackles (`BTK`), Yards After First Hit (`YAC`), Fumbles (`FUM`), 20+ Yard Runs | **Explosive Runs**: 10+ yard runs.<br>**Rushing Success**: Positive-EPA rushing plays.<br>**First Downs**: Chains moved on the ground. | `RUSHATTEMPTS`, `RUSHYARDS`, `RUSHTDS`, `RUSHLONGEST`, `RUSHBROKENTACKLES`, `RUSHYARDSAFTER1STHIT`, `RUSHFUMBLES`, `RUSH10YARDRUNS`, `RUSH20YARDRUNS`, `RUSHSUCCESS`, `FIRSTDOWNS` |
| **Run Concepts & Direction** | *None* | Concept breakdown & directional path:<br>• **Inside Run**: Carries, Yards, TDs, Run Left vs Run Right<br>• **Outside Run**: Carries, Yards, TDs, Run Left vs Run Right | `INSIDERUN_CARRIES`, `_YARDS`, `_TD`, `_RUNLEFT`, `_RUNRIGHT`<br>`OUTSIDERUN_CARRIES`, `_YARDS`, `_TD`, `_RUNLEFT`, `_RUNRIGHT` |
| **Defensive Front** | *None* | Box density on carry:<br>• **Light Box** (≤6 in the box)<br>• **Loaded Box** (≥8 in the box) | `LIGHTBOX`<br>`LOADEDBOX` |

### 3.4 Defense & Pass Rush
| Metric Category | Surfaced in In-Game Box Score | Tracked in Underlying Engine | Schema Attributes |
| :--- | :--- | :--- | :--- |
| **Tackles & Havoc** | Total Tackles (`TAK`), Solo (`SOLO`), Assists (`AST`), Tackles For Loss (`TFL`), Sacks (`SACK`), INTs, Return Yds/TD, Pass Deflections (`PD`), Forced Fumbles (`FF`), Fumble Recoveries (`FR`), Return Yds/TD, Blocks (`BLK`), Safeties | **Big Hits**: Hit-stick and momentum collision tackles.<br>**Defensive Stops**: Tackles resulting in offensive failure.<br>**Run Stuffs**: Tackles at or behind the line on run plays. | `DEFTACKLES`, `ASSDEFTACKLES`, `DEFTACKLESFORLOSS`, `DLINESACKS`, `DLINEHALFSACK`, `DSECINTS`, `DSECINTRETURNYARDS`, `DSECINTTDS`, `DEFPASSDEFLECTIONS`, `DLINEFORCEDFUMBLES`, `DLINEFUMBLERECOVERIES`, `DLINEBLOCKS`, `DLINESAFETIES`, `BIGHITS`, `STOPS`, `STUFFS` |
| **Coverage Allowed** | *None* | Per-defender coverage accountability:<br>• **Catches Allowed**: Receptions conceded in coverage<br>• **Targets Allowed**: Times targeted as primary defender<br>• **Passes Defended / INTs**: On targeted balls | `CTHALLOWED`<br>`DEFTARGETS`<br>`DEFPASSDEFLECTIONS`<br>`DSECINTS` |
| **Pass Rush Pressure** | Sacks | Total quarterback disruptions and pressures generated | `DEFPRESSURES`<br>`DLINESACKS` |
| **Coverage Alignment** | *None* | Snaps deployed in Press, Normal, or Off coverage depth | `COVERAGEDEPTH_PRESS`<br>`COVERAGEDEPTH_NORMAL`<br>`COVERAGEDEPTH_OFF` |
| **Coverage Depth Allowed** | *None* | Stratified by depth tier allowed (Short, Medium, Deep) | `DEFSHORTPASS_*`<br>`DEFMEDIUMPASS_*`<br>`DEFDEEPPASS_*` |

### 3.5 Offensive Line & Blocking
| Metric Category | Surfaced in In-Game Box Score | Tracked in Underlying Engine | Schema Attributes |
| :--- | :--- | :--- | :--- |
| **Protection & Blocking** | Pancake Blocks (`PANCAKE`), Sacks Allowed (`SACKS ALLOWED`) | Individual offensive line snap counts and starting assignment | `OLINEPANCAKES`<br>`OLINESACKSALLOWED`<br>`DOWNSPLAYED`<br>`GAMESSTARTED` |

### 3.6 Special Teams & General Telemetry
| Metric Category | Surfaced in In-Game Box Score | Tracked in Underlying Engine | Schema Attributes |
| :--- | :--- | :--- | :--- |
| **Kicking & Punting** | FGM/FGA, Long, Blocked, Distance splits (1–29, 30–39, 40–49, 50+), XPM/XPA/Blocked, Punts, Punt Yds/Net/In20/TB/Long/Blocked, Kick/Punt Return Att/Yds/TD/Long | **Kickoff Volume**: Total kickoffs booted.<br>**Touchback Volume**: Kickoff touchbacks achieved.<br>**Game-Winning Field Goals**: Attempts and conversions. | `KICKNUMKICKOFFS`<br>`KICKTOUCHBACKS`<br>`GAMEWINFGATTEMPTS`<br>`GAMEWINFGSMADE` |
| **General Tracking** | *None* | **Downs Played**: Exact snap counts for all 22 positions.<br>**Game Rating**: Internal 0–100 performance grade.<br>**Starter Status**: Credit for starting drive. | `DOWNSPLAYED`<br>`GAMERATING`<br>`GAMESSTARTED` |

---

## 4. Derivable Advanced Analytics Suite

Combining Madden's surfaced counters with its hidden engine telemetry enables the computation of industry-standard analytics:

### 4.1 Wide Receiver & Tight End Metrics
1. **Targets Per Route Run (TPRR)**:
   $$\text{TPRR} = \frac{\text{TARGETS}}{\text{ROUTESRUN}}$$
   *Significance*: Evaluates target demand independent of offensive run/pass ratio. League elite thresholds sit above $25\%$.
2. **Yards Per Route Run (YPRR)**:
   $$\text{YPRR} = \frac{\text{RECEIVEYARDS}}{\text{ROUTESRUN}}$$
   *Significance*: The premier per-snap receiving efficiency metric. A value $> 2.20$ denotes WR1 efficiency.
3. **Target Share**:
   $$\text{Target Share} = \frac{\text{TARGETS}}{\text{Team Pass Attempts}}$$
4. **Route Participation Rate**:
   $$\text{Route Participation} = \frac{\text{ROUTESRUN}}{\text{Team Dropbacks}}$$
5. **Average Depth of Target (aDOT)**:
   $$\text{aDOT} = \frac{\text{AIRYARDS}}{\text{TARGETS}}$$
6. **Receiver Air Conversion Ratio (RACR)**:
   $$\text{RACR} = \frac{\text{RECEIVEYARDS}}{\text{AIRYARDS}}$$
   *Significance*: Measures how effectively intended downfield air yards are converted into net yardage.
7. **True Catch Rate & True Drop Rate**:
   $$\text{Catch Rate} = \frac{\text{RECEIVECATCHES}}{\text{TARGETS}}, \quad \text{Drop Rate} = \frac{\text{RECEIVEDROPS}}{\text{RECEIVECATCHES} + \text{RECEIVEDROPS}}$$
8. **Separation Index**:
   $$\text{Open Rate} = \frac{\text{SEPARATION\_OPEN} + \text{SEPARATION\_WIDEOPEN}}{\text{TARGETS}}, \quad \text{Contested Rate} = \frac{\text{SEPARATION\_TIGHT}}{\text{TARGETS}}$$

### 4.2 Quarterback Metrics
1. **Pressure-to-Sack Rate (P2S)**:
   $$\text{P2S} = \frac{\text{PASSSACKED}}{\text{PRESSURES}}$$
   *Significance*: Disentangles offensive line fault from quarterback pocket presence. High P2S ($> 20\%$) indicates pocket holding and inability to navigate pressure.
2. **Pressure Rate Faced**:
   $$\text{Pressure Rate} = \frac{\text{PRESSURES}}{\text{PASSATTEMPTS} + \text{PASSSACKED}}$$
3. **Deep Ball Attempt Rate**:
   $$\text{Deep Rate} = \frac{\text{DEEPPASS\_ATTEMPTS}}{\text{PASSATTEMPTS}}$$
4. **Depth-Stratified Efficiency**:
   Separate Passer Rating, Completion %, and Yards Per Attempt calculated across:
   * **Short** ($0–10\text{ yds}$): Screen and rhythm execution.
   * **Medium** ($11–19\text{ yds}$): NFL intermediate window passing.
   * **Deep** ($20+\text{ yds}$): Downfield explosive ball-placement.
5. **Platform Disparity (In-Pocket vs. Off-Platform)**:
   Comparison of efficiency metrics between `INSIDEPOCKETTHROW_*` and `OUTSIDEPOCKETTHROW_*`.
6. **Blitz Resilience Rating**:
   Passer rating and turnover rate specifically under blitz looks (`BLITZ_*`).
7. **True Emergency Scramble Rate**:
   $$\text{Scramble Rate} = \frac{\text{QBSCRAMBLE\_CARRIES}}{\text{Total Dropbacks}}$$

### 4.3 Running Back Metrics
1. **Rushing Success Rate**:
   $$\text{Success Rate} = \frac{\text{RUSHSUCCESS}}{\text{RUSHATTEMPTS}}$$
   *Significance*: Measures situational down-to-down consistency rather than aggregate volume.
2. **Yards After Contact Per Attempt (YAC/Att)**:
   $$\text{YAC/Att} = \frac{\text{RUSHYARDSAFTER1STHIT}}{\text{RUSHATTEMPTS}}$$
   *Significance*: Isolates individual creation ability from offensive line push.
3. **Elusiveness / Broken Tackle Rate**:
   $$\text{BTK Rate} = \frac{\text{RUSHBROKENTACKLES}}{\text{RUSHATTEMPTS}}$$
4. **Explosive Run Rate**:
   $$\text{Explosive Rate} = \frac{\text{RUSH10YARDRUNS} + \text{RUSH20YARDRUNS}}{\text{RUSHATTEMPTS}}$$
5. **Loaded Box Rate & Front Adaptation**:
   $$\text{Loaded Box Rate} = \frac{\text{LOADEDBOX}}{\text{RUSHATTEMPTS}}$$
   Examines whether production is sustained against stacked 8+ man fronts or aided by light fronts.
6. **Inside vs Outside Concept Efficiency**:
   Compares YPC and success between interior runs (`INSIDERUN_*`) and perimeter sweeps (`OUTSIDERUN_*`).

### 4.4 Defensive & Secondary Metrics
1. **Passer Rating Allowed in Coverage**:
   Calculated directly for individual cornerbacks and safeties using `DEFTARGETS`, `CTHALLOWED`, depth completions/yards/TDs, and `DSECINTS`.
2. **Catch Rate Allowed (Burn %)**:
   $$\text{Burn \%} = \frac{\text{CTHALLOWED}}{\text{DEFTARGETS}}$$
   *Significance*: Primary benchmark for lockdown cornerback performance.
3. **Pass Rush Pressure Rate (PRate)**:
   $$\text{PRate} = \frac{\text{DEFPRESSURES}}{\text{Pass Rush Snaps}}$$
   *Significance*: $3\times$ more predictive of future defensive disruption than raw sack totals.
4. **Sack Conversion Rate**:
   $$\text{Conversion Rate} = \frac{\text{DLINESACKS} + 0.5 \times \text{DLINEHALFSACK}}{\text{DEFPRESSURES}}$$
5. **Defensive Stop Rate & Stuff Rate**:
   $$\text{Stop Rate} = \frac{\text{STOPS}}{\text{DOWNSPLAYED}}, \quad \text{Stuff Rate} = \frac{\text{STUFFS}}{\text{Run Defense Snaps}}$$
6. **Press Technique Frequency & Success**:
   $$\text{Press Frequency} = \frac{\text{COVERAGEDEPTH\_PRESS}}{\text{Total Coverage Snaps}}$$

### 4.5 Offensive Line & Playing Time Metrics
1. **Pass Blocking Sack Rate**:
   $$\text{Sack Rate Allowed} = \frac{\text{OLINESACKSALLOWED}}{\text{Pass Block Snaps}}$$
2. **Pancake Block Rate**:
   $$\text{Pancake Rate} = \frac{\text{OLINEPANCAKES}}{\text{Run Block Snaps}}$$
3. **True Snap Share**:
   $$\text{Snap Share} = \frac{\text{DOWNSPLAYED}}{\text{Total Team Snaps}}$$
   *Significance*: Universal playing-time share across all 22 offensive, defensive, and special teams positions.
4. **Touchback Percentage**:
   $$\text{TB \%} = \frac{\text{KICKTOUCHBACKS}}{\text{KICKNUMKICKOFFS}}$$

---

## 5. Integration with Project Mu Systems

| Project Mu Engine / Feature | Relevant Tracked Metrics | System Enhancement |
| :--- | :--- | :--- |
| **Pipeline Loader (`pipeline/load_save.py`)** | `DOWNSPLAYED`, `CTHALLOWED`, `BIGHITS`, `RUSHBROKENTACKLES`, `RUSHYARDSAFTER1STHIT` | Direct ingestion from `Game*Stats` into `player_stats` and `game_stat_*`. |
| **Excitement Index ($E$)** | `4QCOMEBACKS`, `BIGHITS`, `QBSCRAMBLE_*`, `GAMEWINFGSMADE` | Granular momentum, play violence, and comeback scoring weights. |
| **Napper Fantasy Platform** | `TARGETS`, `ROUTESRUN`, `TPRR`, `YAC/Att`, `Snap Share` | Upgrades projections from raw touches to underlying opportunity volume and target-earning metrics. |
| **Agent Wire & Newsroom** | `CTHALLOWED` (CB coverage), `P2S` (QB pocket collapse), `SEPARATION_*` | Contextual story prompts: calling out blown coverage assignments, QB holding the ball, or lockdown shadow corners. |
| **Command Center Front Office** | `PRate`, `YPRR`, `P2S`, `OLINESACKSALLOWED` | Evaluates true market value in contract extensions independent of raw box-score inflation. |
