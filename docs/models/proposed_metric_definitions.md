# Metric Definitions: Referenced-but-Missing Metrics (Decision Record)

**Status:** approved and implemented 2026-10-12. Each heading says whether it was **Implemented** or **Rejected**. Implemented metrics are specified in `PROJECT_SPEC.md` §2, which wins if the two disagree.

Every metric below is named in `newsroom/config/article_types.json` ("Data Queried", which is injected into writer prompts), in persona/curatorial directives, or in `PROJECT_SPEC.md`, but is either not computed anywhere or computed without being visible to writers. Definitions are constrained to data that actually exists:

- Game-level detail exists only for **2035** (`games`, `player_stats`, `team_stats`).
- For 2026–2034 we have each **Super Bowl result** (`games.game_type = 'SUPER_BOWL'`) and the full **end-of-season awards**: MVP, OPOY, DPOY, OROY, DROY, SBMVP and COTY. That's 63 rows in `awards`, each with the winner's team that season. They were extracted from `data/video/awards.MP4` (the in-game Season Recap screens) by `pipeline/batch_menus.py --mode awards` and checked against those screens; every SBMVP's team matches the SB winner.
  - The recap screens show nothing beyond these 7 awards (no All-Pro teams).
  - The screens also show franchise totals ("3X SUPER BOWL CHAMPION", "6X NFC CHAMPION"), which aren't stored.
  - Regular-season records, rosters and playoff brackets for 2026–2034 aren't in the database.
- `team_stats`: `score`, `total_offense`, rushing/passing attempts and yards and TDs, `turnovers` are populated; `total_plays`, `first_downs`, third/fourth-down columns are **all 0**.
- `player_stats` is offensive only (no tackles, sacks, targets, snaps). Madden ratings (`player_seasons.overall_rating`, `team_seasons` OVRs and `pass_off_rank` / `rush_off_rank` / `pass_rush_rank` / `coverage_rank`) are populated for 2035.
- No data at all for injuries, pressures, snaps, targets, red-zone trips, or coaching/QB tenure.
- **Per-game box scores for CPU-simmed games are synthetic.** `engines/game_stat_generator.py` splits each player's season totals across games in proportion to each game's combined score. Season totals and final scores are real. Anything that depends on how stats fell game by game (takeaways, yards allowed, TDs vs. points in a game, usage changes between games) is an artifact of that split. Several definitions below were rejected or narrowed for this reason.

Notation: $C_g$ = a team's Opponent-Adjusted Game Credit in game $g$, $R$ = its season credit (PROJECT_SPEC §2.4). "z" = z-score across the 32 teams.

---

## A. New team metrics (computable now)

### A1. Trajectory Multiplier ($T$) — Implemented (§2.4)
Referenced by: `POWER_RANKINGS`, `PLAYOFF_PREDICTOR`, `SEASON_PROGRESS_AUDIT`, `LOCAL_SEASON_PROGRESS_REPORT`. No formula exists anywhere.

Is the team better or worse lately than its season-long level?

$$\Delta = \bar C_{\text{last 4}} - R \qquad \Delta_{\text{shrunk}} = \Delta \cdot \frac{4}{4 + 4} \qquad T = \text{clamp}\!\left(1 + \frac{\Delta_{\text{shrunk}}}{20},\ 0.80,\ 1.20\right)$$

- Last-4-games window (≈ "the last month") rather than exponential decay, so it can be described in prose. The shrink halves the swing because a 4-game mean of game credit is noisy (game-credit σ ≈ 11 pts).
- $T = 1.00$ until a team has played 6 games.
- Labels: ≥ 1.08 **Surging**, 1.03–1.08 **Rising**, 0.97–1.03 **Steady**, 0.92–0.97 **Fading**, < 0.92 **Sliding**.
- It's called a "multiplier" in the prompts. Nothing multiplies by it yet; if Power Rankings ever get a numeric score, $R \cdot T$ (for $R > 0$) is the intended use.

### A2. Pythagorean Win Expectation — Implemented (§2.6)
Referenced by: `MACRO_SLATE_RECAP`.

$$\text{PythW} = G \cdot \frac{PF^{2.37}}{PF^{2.37} + PA^{2.37}} \qquad \text{Pyth luck} = W - \text{PythW}$$

- 2.37 is the standard NFL exponent. Uses raw points; complements `expected_wins` (schedule-adjusted) from §2.4. When they disagree, the gap is schedule.

### A3. Turnover Margin — Rejected
Referenced by: `LOCAL_POSTGAME_REPORT_CARD`.

**Rejected:** takeaways come from opponents' turnovers allocated game by game, which is synthetic (see data notes). Giveaways per game (the team's own, a real season total) are returned by `get_team_efficiency_profile` instead. The original proposal was:

$$\text{TOM} = \sum_g (\text{opp turnovers}_g - \text{own turnovers}_g)$$

Reported as season total and per game, from `team_stats.turnovers` (own giveaways; takeaways are the opponent's row).

### A4. Team Offensive Pace ($P_{\text{team}}$) — Implemented (§2.6; writer tool uses `production_score`)
Referenced by ~10 article types. Today the excitement engine's $P$ uses points + yards, while `get_advanced_game_analytics` uses points ÷ 70 only.

- **Game level:** use `gotw_engine.production_score(total_points, combined_yards)` everywhere, including the writer tool.
- **Team level (new):** $P_{\text{team}} = \min\!\left(100,\ \frac{PF/G}{35}\cdot 60 + \frac{\text{Yds}/G}{425}\cdot 40\right)$ — the same benchmarks halved to one team.
- True tempo (plays per game) isn't possible until `total_plays` is captured.

### A5. Playoff Clout ($S_{\text{playoff}}$) — Implemented (§2.7)
Referenced by: `PLAYER_DEEP_PROFILE`, `SUPER_BOWL_CONTENDER_BLUEPRINT`, `POSITIONAL_TOP_10_HIERARCHY`, `SUPER_BOWL_COMPREHENSIVE_PREVIEW`, `QUARTERBACK_DUEL_TAPE_BREAKDOWN`. Not defined anywhere.

**Team:**
$$S_{\text{playoff}}^{\text{team}} = \min\!\left(100,\ \sum_{y} e^{-0.15\,(Y - y)}\left[100\cdot\text{SBwin}_y + 50\cdot\text{SBloss}_y + 20\cdot\text{PlayoffWins}_y\right]\right)$$
- $y$ ranges over 2026–current; SB results from `games.game_type = 'SUPER_BOWL'`, and playoff wins from non-regular-season games once they exist (none before 2035).

**Player:**
$$S_{\text{playoff}}^{\text{player}} = \min\!\left(100,\ 40\cdot\text{SBMVPs} + 25\cdot\text{SB wins played in} + 8\cdot\text{playoff wins played in}\right)$$
- "Played in" means the player has a `player_stats` row in that game, so it's only knowable from 2035 onward; before that only SB MVPs count.
- Pre-2035 Super Bowl rosters aren't stored, so career clout for veterans is understated. This is a known gap.

### A6. Matchup Edge (replaces "Opponent Matchup Deficiencies") — Implemented, revised (§2.8)

**Revised:** "yards allowed" and takeaways are synthetic per-game allocations, so the defensive side uses Madden ratings instead: Pass edge $= z(A\text{ pass yds/g}) + z(B\text{ coverage\_rank})$; Rush edge $= z(A\text{ rush yds/g}) - z(B\text{ def\_ovr})$; Ball-security edge $= z(B\text{ giveaways/g}) - z(A\text{ giveaways/g})$. Ranks run 1 = best. The original proposal was:
Referenced by: `LOCAL_MATCHUP_PRIMER`, `X_FACTOR_VOLATILITY_SPOTLIGHT`, `QUARTERBACK_DUEL_TAPE_BREAKDOWN`.

For team $A$ facing $B$, per phase, from 2035 `team_stats`:

| Edge | Formula |
|---|---|
| Pass | $z(A\text{ pass yds/g}) + z(B\text{ pass yds allowed/g})$ |
| Rush | $z(A\text{ rush yds/g}) + z(B\text{ rush yds allowed/g})$ |
| Takeaway | $z(A\text{ takeaways/g}) + z(B\text{ giveaways/g})$ |

- Positive = A's strength meets B's weakness. The largest positive edge is "the deficiency to exploit," and the most negative is A's own vulnerability.
- Computed both directions for each game.

### A7. Trench Edge (replaces "Defensive Front vs. O-Line Matchup") — Implemented (§2.8)
Referenced by: `LOCAL_MATCHUP_PRIMER`. There's no pressure or sack data, so this is ratings-based.

- $OL_A$ = mean OVR of A's top 5 among LT/LG/C/RG/RT.
- $DF_B$ = mean OVR of B's top 4 among LEDG/REDG/DT.
- $\text{Trench Edge}_{A\,\text{off}} = OL_A - DF_B$ (OVR points), shown with B's `pass_rush_rank` for context.

### A8. Touchdown Finishing Rate — Rejected

**Rejected:** per-game TDs don't line up with per-game points (e.g. half the Chargers' games show more TDs × 7 than points scored), so the rate measured the stat split, not finishing. Red-zone metrics will be dropped from the configs. The original proposal was:
Referenced by: `SINGLE_GAME_SCHEMATIC_AUTOPSY`, `LOCAL_POSTGAME_REPORT_CARD`. Red-zone trips aren't recorded.

$$\text{TD}_{\text{off}} = \sum(\text{rush\_tds} + \text{rec\_tds}) \qquad \widehat{\text{FG}} = \max\!\left(0, \frac{PF - 7\cdot\text{TD}_{\text{off}}}{3}\right) \qquad \text{Finish\%} = \frac{\text{TD}_{\text{off}}}{\text{TD}_{\text{off}} + \widehat{\text{FG}}}$$

- Share of scoring trips ending in touchdowns. Defensive and special-teams TDs and missed PATs add noise.
- Label it "finishing drives," never "red zone."

---

## B. New player metrics (computable now)

### B1. Touch Share (replaces "Target/Snap Share Deltas", "Player Snap & Efficiency Shares") — Implemented, narrowed (§2.8)

**Narrowed:** season touch share and yard share only. Usage delta is dropped, because synthetic per-game splits give every player nearly the same share each game, so the delta is ~0 by construction. The original proposal was:
Referenced by: `LOCAL_PLAYER_SPOTLIGHT`, `X_FACTOR_VOLATILITY_SPOTLIGHT`, `PLAYER_PROGRESS_REPORT` (as "Target/Usage Deltas"), `SINGLE_GAME_SCHEMATIC_AUTOPSY`. There are no snaps or targets.

$$\text{Touch share}_{i,g} = \frac{\text{rush\_att}_i + \text{rec}_i}{\sum_{\text{team}}(\text{rush\_att} + \text{rec})} \qquad \text{Yard share}_{i,g} = \frac{\text{rush\_yds}_i + \text{rec\_yds}_i}{\text{team scrimmage yds}}$$

- **Usage Delta** = mean touch share over the last 3 games − season mean (percentage points). Skill positions only.
- Receptions, not targets, so it understates high-volume, low-catch receivers.

### B2. Exposure ($E_{\text{exposure}}$) — Implemented (§2.5)
The spec exists (`nfl_player_popularity_specification.md` §2.2) but needs SnapShare, which we don't have.

$$E_{\text{exposure}} = \frac{\sum_k w_k\, E_k\, a_{i,k}\,(1 + 0.25\cdot\mathbb{I}_{\text{GOTW},k})}{\sum_k w_k\, a_{i,k}} \qquad w_k = e^{-0.12(T-k)}$$

- $a_{i,k} = 1$ if the player has a stat line in game $k$, which stands in for SnapShare.
- GOTW = the highest-E game of that week.
- $E_k$ is raw E. Defaults to 50 for players with no appearances (defensive players included, since no defensive stats exist).

### B3. Awards Equity ($A_{\text{awards}}$, $A_{\text{career}}$) — Implemented (§2.5)
The spec exists (§2.5). Code counts only last season, with no decay and no career term.

- Points per award: MVP 50, OPOY/DPOY 35, SBMVP 25, OROY/DROY 18. All-Pro isn't tracked.
- $A_{\text{career}} = \sum_{\text{all seasons}} A_{\text{points}}$.
- $A_{\text{awards}} = \min\!\left(100, \sum_{y=1}^{3} e^{-0.35(y-1)}A_{\text{points}}(Y-y) + 0.25\cdot A_{\text{career}}\right)$.
- Match on `awards.player_id`, falling back to name (21 rows have no `player_id`).

---

## C. Already exist — just expose (definition unchanged) — Implemented (§2.2, §2.5), including Team Roster Deficiencies (config now says "Team Draft Needs")

| Metric | Where it's computed now | Change |
|---|---|---|
| Upset Leverage ($U_{\text{projected}}$) | `excitement_projection.expected_upset` | Writer tool returns the real $U$ plus the underdog's win probability, replacing the "High Variance" (E > 80) label. |
| Watchability Volatility ($\sigma_{\text{top}}$) | `twi_engine` (discarded) | Store on `team_seasons` and expose. |
| Blue-Chip Pedigree ($P_{\text{draft}}$) | `twi_engine` (discarded) | Store and expose. `get_advanced_team_analytics` stops deriving "draft pedigree" from Y_prod. |
| Purgatory Penalty | `twi_engine` (discarded) | Store and expose. The tool stops using the "roster avg age > 27.5" approximation. |
| Under-25 Production ($Y_{\text{prod}}$) | `twi_engine` and `get_advanced_team_analytics` (two different calculations) | One calculation (TWI's), stored and read by the tool. |
| SpikeIndex | `player_popularity_engine` (discarded) | Store on `player_seasons`; definition stays $\min(100, 25\cdot\#\text{games} \ge 24\text{ PPR})$. |
| Baseline Rating ($R_{\text{rating}}$), Franchise Platform ($F$), Team Success ($T_{\text{success}}$) | `player_popularity_engine` | Expose via the player context tool. |
| Base Market Gravity ($B$), National Reach ($N$), Legacy Floor ($S_{\text{all-time}}$, a.k.a. "Historical Franchise Prestige") | Hardcoded dicts in `popularity_engine` and `player_popularity_engine` (B duplicated) | One constants module seeded from `base_market_gravity_specification.md` (L, D, N, B) and `legacy_floor.md`; expose via a team tool. |
| Recent hardware bumps (Franchise POP) | `popularity_engine.RECENT_SUCCESS_BUMPS` | Derive from `games` SUPER_BOWL rows. The hardcoded table credits the Giants with 2031 and 2033 runner-ups, but the DB has the Lions (2031) and Falcons (2033) losing. |
| Team Roster Deficiencies | `get_team_draft_needs` | Already exists; point the config wording at it. |

---

## D. No data — dropped or relabeled in `article_types.json` — Implemented

| Referenced metric | Recommendation |
|---|---|
| Pressure Rates | Replace with "Pass Rush Rating (Madden rank)" from `team_seasons.pass_rush_rank`. |
| Injury Report Availability | Drop (no injury data). |
| Head Coach / Quarterback Tenure | Relabel to "Quarterback experience" (`years_of_experience`). Coach tenure isn't stored; only COTY winners' names exist. |
| Red Zone / Snap / Target metrics | Red-zone metrics dropped (A8 was rejected); snap/target metrics replaced by season touch share (B1). |

**Applied relabels** (`data_queried` lists in `article_types.json`, mirrored in PROJECT_SPEC §7): Target/Snap Share Deltas, Target/Usage Deltas and Player Snap & Efficiency Shares → Touch Share & Scrimmage Yard Share (season). Turnover Margin → Giveaways per Game. Pressure Rates → Pass Rush Rating (Madden rank). Opponent Matchup Deficiencies → Matchup Edge. Defensive Front vs. O-Line Matchup → Trench Edge. National Penetration → National Reach (published for 8 franchises). Head Coach / QB Tenure → Quarterback Experience. Team Roster Deficiencies → Team Draft Needs. Red Zone Conversion Rates, Red Zone Efficiency and Injury Report Availability were removed.

**Not changed:** editorial prose in structure/curatorial directives and `playoff_overrides.json` still asks for red-zone execution, turnover margins and pressure rates in places (e.g. the Super Bowl preview's "Red Zone" section). Writers have no data for those and may improvise.
