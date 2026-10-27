# Narrative Cohesiveness Review: Re-evaluating Past System Decisions

**Status:** Strategic Review & Design Assessment (2026-10-27).  
**Context:** The media engine was originally constrained to a single season (2035) where all CPU games were mathematically generated through score-proportional distributions (`DISTRIBUTED`), with zero recorded league history prior to 2035. Today, Project Mu possesses:
1. **A full season of real games (2036):** Captured weekly via Remote Play OCR with authentic box scores, play logs, highlights, and exact situational stats (conversions, red zone, penalties).
2. **A season of synthetic games (2035):** Real season totals and final scores, but synthetic intra-game distributions.
3. **Ten years of full-year statlines (2026–2034 history backfill + 2035 + 2036):** High-fidelity season-level ledger with standings, playoff brackets, Super Bowls, coach tenures/records from 2026, award histories, All-Pro teams, record book marks, and complete player season statlines summing to career totals.

This document reviews seven foundational compromises made under earlier data limitations and presents concrete proposals to rethink them for heightened narrative cohesiveness in generated articles.

---

## 1. Historical Grounding: In-Universe Lore vs. Real-World Crutches

### The Original Compromise
In `newsroom/swarm_dispatcher.py` (Rule 4: *Historical Comparisons*), national writers were instructed:
> *"You may compare a team or player to a real NFL team or season from 1990–2023 when the parallel genuinely sharpens your argument... Never invent a historical team or season."*

Similarly, persona directives (e.g., Bill Simmons in `newsroom/config/agent_personas.json`) instructed agents to use the 1990s NBA or real NFL eras as crutches. This guardrail was essential when the database held zero simulation history.

### Narrative Friction
In 2036, reaching for the 2000 Ravens, 2017 Eagles, or 1990s role players breaks the fourth wall and discards a full decade of living canon that occurred in this universe.

### Proposed Rethink
* **Pivot from Real-World 1990–2023 to Simulation Canon (2026–2035):** Update national research guidelines and persona directives to encourage historical parallels within the league’s own history:
  * Marcus Whitfield’s historic 2029–2033 Chicago Bears scoring powerhouse.
  * The Carolina Panthers' traumatic collapse (0–17 in 2031) and 2034 renaissance.
  * Past MVP/OPOY campaigns and infamous playoff upsets.
* **Integrate Tools into Article Definitions:** Formally expose `get_player_career` and `get_team_history` in `newsroom/config/article_types.json` (`data_queried`), making career context an active part of article commissioning.

---

## 2. Restoring Game-to-Game Usage Trends (The Death of "Synthetic Flatness")

### The Original Compromise
In `docs/models/proposed_metric_definitions.md` (§B1), rolling **Usage Delta** ($\text{Touch Share}_{\text{last 3}} - \text{Touch Share}_{\text{season}}$) was explicitly **rejected and dropped**:
> *"Usage delta is dropped, because synthetic per-game splits give every player nearly the same share each game, so the delta is ~0 by construction."*

Because 2035 games split season totals across games proportional to final score, touch shares never varied week-to-week.

### Narrative Friction
In real sports journalism, weekly workload shifts are the engine of beat reporting and fantasy analysis: backfield takeovers, a wideout being phased out, or a player returning from injury with an escalating snap count.

### Proposed Rethink
* **Reintroduce Rolling Usage Deltas for Real Games:** 2036 games are captured live. Week-to-week carry and reception distributions fluctuate authentically.
* Calculate and expose rolling 3-game usage shifts in `get_player_context` and fantasy research tools (`get_fantasy_player_card`), unlocking storylines like "the rookie backfield takeover" or "the declining target share."

---

## 3. Matchup Edges & Turnover Margin

### The Original Compromise
In `docs/models/proposed_metric_definitions.md`:
* **Turnover Margin** was rejected (§A3) because opponent takeaways in 2035 were synthetic allocations.
* **Matchup Edges** (§A6) abandoned opponent pass/rush yards allowed per game and substituted static Madden video game ratings (`coverage_rank`, `def_ovr`).

### Narrative Friction
Game previews currently evaluate matchups using static Madden OVR ranks rather than how defenses actually perform on the field. Furthermore, turnover margin—football’s primary chaotic narrative engine—was stripped from analytical profiles.

### Proposed Rethink
* **Rehabilitate Turnover Margin:** 2036 captures exact team giveaways and takeaways, and the 10-year season ledger maintains exact turnover figures. Expose season turnover margin in `get_team_efficiency_profile` and game matchup previews.
* **Evolve Matchup Edges:** Shift `matchup_edges.py` from purely abstract Madden OVR tiers to blending *real defensive production conceded* (points allowed, passing/rushing yards allowed per game, takeaway rates).

---

## 4. Multi-Year Player Arcs vs. Single-Season Blinders

### The Original Compromise
With only one season in the database, `get_player_context` and `get_player_season_stats` could only present current-year stats alongside static ratings. Articles evaluating player trajectories (`PLAYER_PROGRESS_REPORT`, `LOCAL_PLAYER_SPOTLIGHT`, `AWARDS_TRACKER`) were designed around whether a player is outperforming his baseline Madden rating ($R_{\text{rating}}$) this season.

### Narrative Friction
Real sports journalism evaluates players against their **established career trajectory**, not an abstract video game overall:
* *Is this a career-best breakout or a return to form?*
* *Is this veteran hitting an age cliff?*
* *Is this player surging in a contract year?*

### Proposed Rethink
* **Provide 3-Year Trailing Baselines:** Enrich `get_player_context` with trailing 3-season averages (games, volume, yards, efficiency) drawn from `season_stat_lines`.
* **Update Curatorial Directives:** Guide writers in `PLAYER_PROGRESS_REPORT` and `LOCAL_PLAYER_SPOTLIGHT` to contrast this season’s pace with past seasons rather than solely comparing to Madden ratings.

---

## 5. Fixing the Veteran "Playoff Clout" Gap ($S_{\text{playoff}}$)

### The Original Compromise
In `PROJECT_SPEC.md` §2.7 and `proposed_metric_definitions.md` §A5, player Playoff Clout noted:
> *"Pre-2035 Super Bowl rosters aren't stored, so career clout for veterans is understated. This is a known gap... played in means the player has a player_stats row... only knowable from 2035 onward."*

### Narrative Friction
A 33-year-old veteran who won two Super Bowls and played in ten playoff games between 2026 and 2034 receives **0 Playoff Clout points** unless he won Super Bowl MVP. Meanwhile, a 2035 rookie with one Wild Card appearance received full playoff credit.

### Proposed Rethink
* With the 2026–2034 history backfill applied (`team_season_results`, `player_seasons`, `season_stat_lines`), the system now knows exactly which roster every player was on for every season and playoff outcome.
* Update `engines/playoff_clout_engine.py` to credit players rostered on 2026–2034 playoff and championship teams, closing this known gap and restoring veteran playoff prestige.

---

## 6. Player Scheme Splits & Sample Size Maturity

### The Original Compromise
In `engines/player_splits.py`, `REAL_SOURCES = ("CAPTURED", "WEEKLY")`. Synthetic distributed lines from 2035 are discarded. Because 2035 was synthetic, players entering 2036 had zero qualifying games. Seven article types were instructed: *“if the tool's verdict says there aren't enough real games, leave splits out.”*

### Narrative Friction
Throughout early 2036, writers repeatedly ran into the "insufficient real games" guardrail, causing `get_player_scheme_splits` to be dropped from narrative consideration.

### Proposed Rethink
* Now that a full 18-week season of real 2036 games has concluded, every active starter has 14–17 authentic data points against top/middle/bottom defenses.
* For late-season 2036, playoffs, and future seasons, splits can be graduated (e.g., directional tendencies rather than all-or-nothing suppression), enabling writers to dissect how passers and rushers fare against elite secondaries or heavy pass rushes.

---

## 7. Unlocking Situational & Red-Zone Football

### The Original Compromise
Touchdown Finishing Rate and red-zone metrics were rejected (`proposed_metric_definitions.md` §A8) because synthetic per-game touchdowns did not match final scores in 2035. Prompts were injected with warnings: *“third-down defense has no season total... do not invent red-zone totals.”*

### Narrative Friction
Situational execution (3rd down conversions and Red Zone TD efficiency) decides real football games and defines coaching identity.

### Proposed Rethink
* 2036 captures exact OCR tables for all 32 teams: `team_conversions.csv`, `team_redzoneefficiency.csv`, and `team_penalties.csv`.
* Soften historical defensive warnings in `newsroom/swarm_dispatcher.py` and encourage writers to use the authentic red-zone conversion and defensive stop rates already exposed by `get_team_efficiency_profile`.

---

---

## 8. Multi-Season Franchise & Player Trajectory Context (5-Season Rolling Horizon)

### The Original Compromise
Because earlier versions had no prior history in the database:
* `get_team_context` returns only the team's current-season record (e.g. `9-3`) and single-season culture metrics.
* `season_landscape` in the Weekly Assignment Brief lists only current-season division records.
* `get_player_context` and `get_player_season_stats` return only current-season counting stats.

Writers evaluated teams and players entirely in a single-season vacuum or against static Madden OVR ratings.

### Narrative Friction
Football journalism derives its meaning from multi-year continuity and franchise arcs:
* **For Teams:** A team sitting at 6–3 could be:
  1. A defending Super Bowl finalist holding serve,
  2. A Cinderella team emerging from a 3–14 tank year under a second-year head coach, or
  3. A perpetual 8–9 purgatory team experiencing a brief hot streak.
  Without past-season finishes, writers cannot identify whether a franchise is in an ascending window, defending a title, or clinging to an aging core.
* **For Players:** An offensive or defensive contributor producing 800 yards or 8 sacks through 10 weeks could be:
  1. An established superstar delivering his standard 5-year baseline,
  2. A 3rd-year breakout talent taking a massive statistical leap,
  3. An aging veteran experiencing one last resurgence in a contract year, or
  4. A former All-Pro bouncing back from an injury-plagued down season.
  Without prior-season statlines, agents are forced to evaluate players against abstract video game overalls rather than living career arcs.

### Proposed Architecture (5-Season Window: 2031–2035)

Five seasons represents the natural lifecycle of an NFL regime (a first-round rookie contract with 5th-year option, a head coach/GM tenure, or a superstar's prime). In Project Mu entering 2036, the 5-season window (2031–2035) captures the simulation's entire modern lore: Whitfield's Bears scoring peak, Carolina's 0–17 abyss to Super Bowl ascent, and the maturation of today's veteran stars.

To deliver maximum narrative signal without causing prompt bloat, the 5-season data is formatted as **dense, chronological trendlines** rather than heavy nested JSON objects.

#### A. Team Trajectory Integration
1. **Enrich `get_team_context`:** Inject a compact 5-year ledger and trajectory assessment (from `team_season_results`):
   ```json
   "five_year_record": "48-35 (.578), 3 playoff appearances, 1 Super Bowl trip",
   "five_year_arc": [
     "2031:  0-17 (4th NFC South, missed)",
     "2032:  4-13 (4th NFC South, missed)",
     "2033:  4-13 (4th NFC South, missed)",
     "2034: 13-4  (1st NFC South, NFC Champions / Super Bowl runner-up)",
     "2035: 11-6  (2nd NFC South, Divisional round exit)"
   ],
   "franchise_state": "Sustained Contender (Year 3 of contention window following ground-up rebuild)"
   ```
2. **Weekly Brief Horizon (`season_landscape`):** Annotate division standings with a clean 5-year win string and title count:
   * `Carolina Panthers (ID: 1) 9-3, diff +80 | 5-Yr Wins: 0-4-4-13-11 (1 SB trip, 2 playoff trips)`
   * `Chicago Bears (ID: 6) 4-8, diff -70 | 5-Yr Wins: 10-11-10-8-6 (Whitfield era exit, post-peak decline)`
3. **Calibrate `get_franchise_rebuild_status`:** Root lifecycle stages (Championship Window, Youth Ascent, Roster Purgatory, Ground-Up Rebuild) directly in the team's 5-year win totals and playoff finishes rather than age heuristics alone.

#### B. Player Trajectory Integration
1. **Enrich `get_player_context` & `get_player_season_stats`:** Inject a tight single-line summary per season (clamped to `min(experience, 5)`), plus 5-year weighted baseline averages from `season_stat_lines`:
   ```json
   "five_year_statline_summary": [
     "2031 (CAR): 16 GP | 3,100 yds, 18 TD, 19 INT",
     "2032 (CAR): 17 GP | 3,850 yds, 24 TD, 14 INT",
     "2033 (CAR): 17 GP | 4,020 yds, 29 TD, 12 INT (Pro Bowl)",
     "2034 (CAR): 17 GP | 4,450 yds, 38 TD,  9 INT (MVP, 1st-Team All-Pro, SB)",
     "2035 (CAR): 17 GP | 4,120 yds, 31 TD, 11 INT (Pro Bowl)"
   ],
   "five_year_per_season_averages": {
     "games": 16.8,
     "pass_yards": 3908.0,
     "pass_tds": 28.0,
     "interceptions": 13.0
   },
   "career_arc_assessment": "Elite franchise QB in peak prime; on pace with 5-year Pro Bowl baseline."
   ```
2. **Support All Position Groups:** Apply the same structure to defenders (sacks, TFLs, INTs, pass deflections), specialists (FG%, punting net), and offensive skill players.
3. **Update Curatorial Directives:** Revise directives for `PLAYER_PROGRESS_REPORT`, `LOCAL_PLAYER_SPOTLIGHT`, `LOCAL_SEASON_PROGRESS_REPORT`, and `AWARDS_TRACKER` to mandate comparing the player's current pace against their 5-year career arc.

---

## Summary Matrix

| Domain | Past Limitation / Decision | Cause at the Time | Current Data Reality | Proposed Rethink |
|---|---|---|---|---|
| **Historical Parallels** | Compare to 1990–2023 real NFL teams | Zero league history in DB | 10 full canon seasons (2026–2035) | Anchor comparisons in simulation lore (Bears dynasty, Panthers rebuild, past MVPs) |
| **Usage Trends** | Dropped game-to-game usage deltas | 2035 synthetic games distributed touches flatly | 2036 has authentic, volatile weekly box scores | Reintroduce rolling 3-game usage deltas for skill players |
| **Matchup Edges** | Used Madden ranks (`coverage_rank`, `def_ovr`) | 2035 opponent yards allowed were synthetic | 2036 has real opponent yards allowed and defensive stats | Blend actual defensive production into matchup edges |
| **Turnover Margin** | Excluded from team profiles | Opponent giveaways were synthetically distributed | Exact turnovers captured in 2036 and present in history | Expose true turnover differential as a core narrative driver |
| **Player Progress** | Evaluated only against current Madden OVR | No prior seasons to establish year-over-year deltas | 10-year season statlines for every active player | Feed 5-year trailing baselines to evaluate career years vs. decline |
| **Team Progress & State** | Single-season record with no franchise trajectory | Zero prior team standings or playoff results | 10 years of exact team results, playoff seeds & titles | Expose 5-year team trendlines in `get_team_context` & brief's `season_landscape` |
| **Playoff Clout ($S_{\text{playoff}}$)** | Veterans received 0 points for 2026–2034 playoff runs | 2026–2034 playoff rosters were unknown | Backfill provides exact player-team mapping and playoff records | Credit veterans for 2026–2034 playoff runs and titles |
| **Situational / Red Zone** | Suppressed red-zone and situational stats | Synthetic touchdowns didn't reconcile with final scores | 2036 captures exact 3rd/4th down and red-zone OCR tables | Promote verified red-zone TD% and situational execution in game autopsies |


