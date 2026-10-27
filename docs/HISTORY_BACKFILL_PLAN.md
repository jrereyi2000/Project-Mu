# 2026–2034 History Backfill: Implementation Plan

**Status (2026-10-26):** Phase 0 built: Saleh baseline fix applied, console facts K1–K5 answered (§2), and the three input files in `data/history/` (`engines/history_backfill/sources.py`, §3.1–§3.2). Phase 1's code built (2026-10-26): Career Stats capture, extract, load into `player_career_totals` (migration applied) and the remainder report; the console capture itself hasn't run yet and must before this offseason's retirements (§3.1). The console capture ran and loaded 2026-10-26 (§3.1). Phase 2 built 2026-10-26: the team arc solver, staging, `apply` / `revert` (§3.3). JR preferred candidate 5 of the first set but found the records too flat (§3.3 item 2). Candidates 13–15 (`verify_output/history_team_arc.md`) use strength arcs and Bowles' Madden baseline (no winning seasons before 2026). All pass `verify`, and all put the AFC's extra playoff loss on Houston (DeMeco Ryans). **JR picked candidate 14, applied 2026-10-26** after a backup (`madden_backup_20261026_193605.sql`): 9 `league_seasons`, 288 `team_season_results` and 288 `coach_season_lines` rows, all SYNTH, and `verify` is clean on the live rows. It is canon under B15; regenerating needs JR's go-ahead. Phase 3 done 2026-10-26: cast, team totals, player lines, fill-ins and validation; run 6 applied (SYNTH rows in the live player tables). Every reader that spans seasons is held back from SYNTH rows and backfilled players until phase 4 (PROJECT_SPEC §3.3k). Phase 4 built 2026-10-26: the player page's history seasons and career-since-2026 totals, the Team hub History tab, history players in search, and the writer tools `get_player_career` / `get_team_history`, with awards and record-book rows linked to their players (PROJECT_SPEC §3.3l). Run 10 replaced run 6 (stat dependencies enforced) and run 11 replaced run 10 (steeper depth-chart shares: more 4,000-yard passers and 1,000-yard receivers, rising from real-NFL levels in 2026 toward 2035's). The season-picker pages (Standings, Stats) show 2026–2034 from the history, and run 16 (group-scaled free players, rate-based team totals, record holders as starters) fixed the defensive and special-teams ratios those pages exposed. JR's decisions are in §0.
**Scope:** Give the league a complete 2026–2034 history at season level: standings, playoffs and coach lines for every team, and season stat lines for every player who played. Each player's lines add up to his captured Madden career totals. The history starts from the state of the real league at the end of the 2026–27 NFL season, ends at the captured 2035 season, and fits every fact already stored in between.
**Reference specs:** `PROJECT_SPEC.md` §2.9 (Record Book), §3.1–§3.3a (players, seasons, stats, coaches), §3.3h (season archive), §7 (writer tools); `docs/SEASON_CONTINUITY_PLAN.md` (this plan replaces its D4); `docs/RECORD_BOOK_AND_AWARDS_PLAN.md` D5 (All-Pro before 2035).

---

## 0. Decisions

### JR (2026-10-26)

| # | Decision |
|---|---|
| B1 | **2026–2034 is rebuilt.** This replaces continuity plan D4 ("capture forward, don't rebuild the past"). |
| B2 | **Career totals are the only new capture.** They come from Madden's Career Stats list. Opening every player's season-by-season page would take too long. |
| B3 | **Generated history shows the arc from point A to point B.** Point A is the league as of the 2026–27 season (the real NFL season's projected end). Point B is the captured 2035 season. The seasons in between connect them. |
| B4 | **Standings for 2026–2034 are generated**, within the limits in §1. |
| B5 | **Retired award winners get invented lines.** The Career Stats page lists active players only, so these lines are made up freely as long as they agree with the award and with every other fact. |
| B6 | **Veteran career totals include real pre-2026 NFL stats.** Those are subtracted before anything is spread across 2026–2034. |
| B7 | **Washed-out draftees can't be recovered.** Fictional players drafted after 2026 who retired before 2035 have no source. Only fill-ins (§3.6) cover the gap they leave. |
| B8 | **Robert Saleh's 2025 `coach_seasons` row (20-36, his real Jets record) is stale.** Madden started him at 0-0 in 2026. The row becomes all zeros, which makes the Titans' 2026–2034 record 83-70. |
| B9 | **Dave Canales coached the Panthers 2026–2033.** His tenure is already stored (2024–2033). He has no 2034 snapshot, so Carolina's 2026–2030 record is not fixed by any coach total. |

### Defaults (change any before its phase)

| # | Default |
|---|---|
| B10 | **Every generated row is marked `source = 'SYNTH'`.** Captured career totals are `CAREER`. Rows already stored keep their current state and win over generated ones. |
| B11 | **Season level only.** No 2026–2034 `games`, `player_stats` or box scores are generated. The Panthers' stored 2034 games stay the only per-game history. |
| B12 | **Fill-in players are visible**, labelled "historical" on player pages and in leader lists. |
| B13 | **Panthers 2026–2030: 18-67, no playoffs** (replaces "middling years"). The coach totals force it: every other coach's 2025 → 2034 difference is exact, so Canales' 2026–2033 is the league's remainder, 26-110 with no playoff games, and 2031–2033 are 8-43. `Panthers Full History.pdf` agrees: no playoffs since 2025, whose 8-9 "was their high-water mark", so at most 8 wins a season. |
| B14 | **Three candidate histories.** The generator writes three, each from a different random seed. JR picks one, can pin any season or line, and the run is refit around the pins (§3.8). |
| B15 | **Approved history is canon.** Once applied, `SYNTH` rows are data under the hallucination policy, and writers must not contradict them. Regenerating the history needs JR's go-ahead. |
| B16 | **Player teams:** each player spends his 2026–2034 seasons with his 2035 team unless something already stored says otherwise (a canon fact or a pin). Team moves can be added later as texture. |
| B17 | **Coach records stay "since 2035" on screen** (continuity plan D2). The generated 2026–2034 `coach_season_lines` exist for history views and writer tools. Their sums must equal the 2025 → 2034 snapshot differences exactly. |

---

## 1. Survey: what's already fixed (live database, 2026-10-26)

| Fact | Coverage | Use |
|---|---|---|
| Coach snapshot differences, 2025 → 2034 | 31 teams: exact 9-season W-L (153 games, no ties), number of winning seasons, playoff W-L, Super Bowl W-L. Includes the Titans after B8, the Broncos (Gaffney) and the Chiefs (Leonard), who all started 0-0 in 2026. The Buccaneers' Todd Bowles is the one coach whose 2025 row is real history but not Madden's start: Madden began him with one more loss (PROJECT_SPEC §3.3a), so the Bucs' 2026–2034 record is 82-71, not the row difference of 82-72. Madden also lists him with no winning seasons before 2026 (JR checked a 2025 save, 2026-10-26), so all 5 of his 2034 count are 2026–2034, not the row difference's 1. Ron Leonard (KC) started 0-0 and has exactly 153 games by 2034, so he coached all nine seasons: Madden replaced Andy Reid before the 2026 season (coach firing was off; how is unknown). His 77-76 with 1 winning season is Madden's own count, which makes Kansas City 8-9 in most seasons. The AFC differences add up to 64 playoff trips against the league's 63: one AFC coach with a real pre-2026 record started in Madden one playoff loss above his stored row (§3.3). The Panthers have none (B9). | Hard limits on standings |
| Super Bowls | Both teams and the score for every season, 2026–2035 | Hard: champion and runner-up |
| 2034 records | Only Carolina's (13-4). The other 17 `team_seasons` 2034 rows hold just their games against Carolina (e.g. ATL 0-2), so they are lower bounds, not records. | Hard |
| Panthers | 2031: 0-17, 2032: 4-13, 2033: 4-13, 2034: 13-4; 2034's 17 regular-season games and 3 playoff games; a few `season_stats` rows for 2032–2034 (Crawford, Davis, Franklin, Tucker) | Hard |
| Awards | MVP, OPOY, DPOY, OROY, DROY, COTY and SB MVP for each season (7 per season). 12 non-COTY rows have no `player_id`, mostly players who have since retired. | Hard: an award season must justify the award |
| Record book (`BOOK`) | Franchise and NFL single-season and rookie-season marks set 2026–2035, with player, season and value (`2026_10_28_league_record_book.sql`) | Hard: exact for the holder, a ceiling for everyone else |
| 2035 and 2036 | Full standings, games, `player_seasons`, `season_stats`, `season_stat_lines`, team overalls | End point B; subtracted from career totals |
| `years_of_experience` | Every 2036 player. Rookie = 0 (memory), so first season = season − experience. 57 players have 11–14 years and entered the league before 2026. | Which seasons a player can have |
| Panthers canon | `Panthers Full History.pdf` (JR's story, mostly 2032–2035) | Numbers it states are hard facts; events are guides |

`AP All Pro.pdf` is the 2035 All-Pro team, which is already stored. It is not a source for 2026–2034.

---

## 2. Console facts (answered by JR, 2026-10-26)

| # | Question | Answer | Consequence |
|---|---|---|---|
| K1 | Where is the Career Stats page? | Under Stats & Awards, beside Player and Team stats, with the same navigation as Season Stats. | The capture reuses the Season Stats path (`LEAGUE_CROSS_OK` already allows STATS & AWARDS) and only switches the stats type. |
| K2 | Categories and columns | Exactly the same as Season Stats. | `player_career_totals` uses the season columns, and the extractor reuses the season list parser. |
| K3 | Is every active player listed? | Apparently yes, but the bottom of a list may be cut off. | League-wide lists stop at 250 rows (the 2036 Receiving and Defense Season Stats lists hold exactly 250, leaving 66 and 99 players with a season line off the list), so the long categories are also captured team by team (§3.1). The same row-coverage check as the season lists (`prior_category_rows`, `min_pages_for`) guards dropped presses. A player missing from every list has an unknown total, not zero (the `season_defense_values` rule). |
| K4 | Does it include the current season so far? | Yes. | Capture right after a Season Stats capture with no games in between, so subtracting 2036 is exact. |
| K5 | What do veteran totals include? | Real NFL stats through 2025; nothing from the real 2026 season. | Subtract real careers **through 2025** only (`data/history/nfl_careers_through_2025.csv`, from nflverse). |

---

## 3. Design

### 3.1 Career totals capture (phase 1)

- **When:** in the same session as a Season Stats capture with no games in between (K4), and before the offseason processes retirements, since retired players leave the list (B5). During the playoffs the regular-season lists are final, so any playoff week works. Run order, with RUN the capture folder:
  1. `python3 madden_capture.py --only career --out RUN`: Stats & Awards > **Career Stats** (the right-hand tile column: In-Season Awards, NFL Record Book, Career Stats, Coach Stats; `open_career_stats`), every category into `career/NN_<cat>_NNN.png` through the Season Stats code (`run_stats(kind="career")`; `career/done.txt` for `--resume`). The cut-off check takes as its floor an earlier `career_<cat>.csv` or the season list's rows (a career list is at least as long). The list has no title (first run, 2026-10-26: it opens on the user's team and shows career numbers, e.g. Crawford's 16,411 passing yards). Its wide tables end on a GP column (label GAMES PLAYED), not DP, so the career lists judge the right edge with `at_career_edge` (GAMES PLAYED with GP the right-most header, or DOWNS PLAYED). RUN must be the folder of the Season Stats capture (`--out 2026-10-26_1151`, a name under `captures/`, not a path).
  2. `python3 madden_capture.py --only career-teams --out RUN`: the same list team by team for the categories whose league-wide list stops at 250 rows (`CAREER_TEAM_CATEGORIES`: rushing, receiving, defense, blocking; `--category` to change) into `career_scope/<team>_<cat>_NNN.png` (`run_career_by_team`; `career_scope/done.txt` lists finished teams). The list is opened once; each next team is only an L2 scope change and a scroll back to the top, never a trip through the hub.
  3. `python3 extract.py RUN --career`: `career_<cat>.csv` and `career_scope_<team>_<cat>.csv`, parsed like the season lists (a full `extract.py RUN` does them too).
  4. `python3 -m pipeline.load_career_totals RUN [--dry-run] [--allow-missing]`.
- **Load** (`pipeline/load_career_totals.py`, one transaction) writes **`player_career_totals`** for league_state's season, replacing that season's rows per category: `captured_season`, `captured_week`, `capture_folder`, `category`, `player_id` (NULL when unsettled), `list_name`, `position`, `stats` JSONB in `season_stat_lines` column names (defense `def_*`, kicking `kick_*`, punting `punt_*`, returns `kr_*`/`pr_*`, blocking `blk_*`) plus `games_played` from GP, `flags`. Unique per (`captured_season`, `category`, `player_id`).
  - **Matching:** a team list's row by list name key + position + that team (flag `team_list`); then the league-wide rows by name key + position against the season's roster, skipping rows a team list already settled. Several players sharing a name and position are settled by their season lines: no counting stat of a career line may be below the same player's season total (`fits`; yards are left out since they can go down), and exactly one assignment must work (`settle`). Otherwise the row is stored with `player_id` NULL and `unmatched` / `ambiguous`; a single candidate whose season total is higher gets `below_season_total`. Two rows differing in at most two cells are one row read twice (`misread_copy`). Rows still unmatched after every exact match get a second pass (unique, untaken, `fits`): a team-list row whose player is stored on another team (`moved_team`: same name key + position on any team; the career list shows his current team), then one OCR slip in the name (`name_slip`, `one_slip`: same position, and the same team for a team-list row).
  - **Report:** per category the rows, matches, flags and the players with a season line but no career row, with a warning naming the `--only career-teams --category` capture when a category without team lists is cut off. A missing category CSV stops the load unless `--allow-missing`. Tested on 2026-10-26 by loading the 2036 season lists as career lists in a dry run: 1,214 of 1,219 rows matched (the 5 others match no 2036 roster name), and the 250-row cut-off showed in receiving (66) and defense (99). **First real load (2026-10-26, folder 2026-10-26_1151, 2036 week 19):** 2,410 rows, 2,404 matched (10 by the second pass); league-wide lists 50 passing, 34 kicking, 36 punting, 178 kick and 91 punt returns, the other four 250 each, with every team's rushing, receiving, defense and blocking list. Left unmatched: N. Bayless (stored position `OB`, a misread of QB), N. Johnson (LG on the list, LT stored), C. Denman and C. Hixon (not on a stored 2036 roster), and M. Peralta's team rushing row (his league-wide row already matched). 23 players stored on a 2036 roster have no career row. The lists carry players on a Madden roster only (D. McBride, 160+ career catches, is missing although the league-wide receiving list runs down to 21), so these are Madden free agents: 17 have a `released_week`, 5 were released without a captured transaction (C. Smith, T. Hix, D. McBride, E. Fraser, A. Cherry), and N. Bayless is the `OB` row. Their 2026–2034 shares are unconstrained, like retired players'. The two large negatives: C. Alonso's 2035 receiving line (25-73, RAC 85) is a copy of J. Alonso's (same row, DP 750, read as C.Alonso in the 2035 Week 18 capture 2026-10-15_2009), more than his 16 career catches; K. Franklin's seeded 2032–2034 Panthers lines (`2026_11_29_panthers_history_backfill.sql`, 510 carries for 2,564 yards) are 29 carries, 273 rushing and 38 receiving yards above what his Madden career leaves. All three (and the Bayless position) are fixed by `db/migrations/2026_12_27_career_fit_fixes.sql` (Franklin's 2032–2033 lines reshaped so 2032–2034 sum exactly to his career remainder; Alonso back to his pre-Week-18 9-46, the fake Week 18 game delta zeroed and its 16-catch game removed from the league top 10). After the reload: 2,406 of 2,410 rows matched, `negative` 14 (all a few yards or one TD, fumble or deflection), 22 rostered players without a career row. Remainders: 1,696 players, 1,397 with a 2026–2034 share, `negative` 16 (mostly a few yards or one deflection; K. Franklin and C. Alonso are larger and need a look), `no_open_seasons` 215 (empty 2035/2036 season lines, see §3.3i), `untracked_pre2026` 37.
- **Remainder** (`python3 -m engines.history_backfill.remainder [--season S]`; `compute()` is pure; writes `verify_output/history_remainders_<S>.csv` and `.md`, nothing to the database): for each player with career totals and each counting stat (averages, rates and longs aren't summable; games played are left out, since Madden's career GP isn't comparable to season games; only columns the player's career rows carry are subtracted, so an offensive player's 2 tackles below a team's defense list are unknown, not −2):
  - **mu = career − Σ captured seasons 2035..S (`season_stat_lines`) − pre-2026 NFL (B6)**; **generate = mu − Σ stored canon rows 2026–2034 (`season_stats`, the Panthers' 2032–2034 lines)**. `generate` is what phase 3 spreads over the player's open seasons, max(2026, first season)..2034 (first season = season − `years_of_experience`).
  - **Flags:** `negative`, `no_open_seasons` (a remainder but a first season of 2035 or later), `missing_pre2026` (entered before 2026, no real-career row), `untracked_pre2026` (stats nflverse doesn't carry, e.g. broken tackles, YAC, run after catch, sacks allowed, so the remainder still holds his real pre-2026 share; listed per player). Players with a 2035..S line but no career row are listed separately, split into rostered in S and no longer rostered.
  - Checked on 2026-10-26 with careers built from each player's own 2035–2036 lines plus his real pre-2026 line: every remainder was zero except the four canon Panthers (Crawford, Davis, Franklin, Tucker), whose stored 2032–2034 lines show as negative, as they must until their real career totals are captured.
  - **Pre-2026 NFL:** `data/history/nfl_careers_through_2025.csv` (`sources.py nfl-careers`): nflverse regular-season player stats for 2016–2025 summed per player and renamed to the `season_stat_lines` columns (sums only; longs and rates aren't summable). It covers every real 2026 roster player and all 57 players in the 2036 pool who entered the league before 2026 (11+ years of experience). Players are matched by `espn_id` (nflverse `players.csv` maps it to the stats' gsis id), or, for a 2036 veteran with neither an `espn_id` nor a 2026 roster row, by a unique name among nflverse players whose rookie season is 2025 or earlier. A player with fewer than 10 years of experience in 2036 entered after 2026 and never gets a real career, even if his stored `espn_id` points at a real namesake.
  - **Bad remainders:** a negative remainder, or a positive one for a player who couldn't have played 2026–2034, is flagged and held out of generation until it's resolved.

### 3.2 The 2026 starting league

- **Team strength in 2026:** `data/history/start_2026.csv` (`sources.py start-2026`): the real 2026–27 standings from ESPN projected to the season's end. A team's rating is its point differential per game shrunk toward zero (PD / (G + 4)); each unplayed game on its ESPN schedule is won with probability Φ((r − r_opp ± 1.5) / 13.5) (1.5 points of home edge); projected points add the remaining games at a scoring rate shrunk 4 games toward the league's. Built 2026-10-26 after 6–7 games. Rerun it later in the season (`--refresh`), and with the final standings if they arrive before JR approves the history.
- **2026 rosters:** `data/history/rosters_2026.csv` (`sources.py rosters-2026`): ESPN's current rosters (2,557 players incl. practice squad and reserve lists, as of 2026-10-26; rerun near season's end). Each is matched to `players` by `espn_id`, else by a unique full name. A match is kept only if the player has at least 9 more years of experience in 2036 than in 2026 (`plausible_veteran`), so a fictional namesake isn't mistaken for him (`match = espn_id_conflict` when the stored `espn_id` fails this). `in_pool_2035` / `in_pool_2036` say whether he is still in the league. On 2026-10-26: 194 matched, 130 in the 2036 pool. They identify two groups:
  - **Retired real veterans:** on a 2026 roster but not in the 2035/2036 pool. They get `players` rows if missing, real careers through 2025 from `nfl_careers_through_2025.csv`, and Mu-era lines from §3.5 with a decline curve and a generated last season.
  - **The 2026 rookie class:** real players, so their first season is fixed.

### 3.3 Team arc (phase 2): `engines/history_backfill/team_arc.py`

Built 2026-10-26 (PROJECT_SPEC §3.3j has the full model).

1. **Facts** (`derive`): the coach differences (§1) per tenure; Canales takes the league's remainder (B13); `BOWLES` adjusts Todd Bowles by −1 loss and +4 winning seasons (Madden's 2025 baseline has none). The AFC's 64 playoff trips need one AFC coach with a real pre-2026 record to carry a playoff loss his stored 2025 row lacks. The data can't say who, so the solver picks him and the report names him. JR can pin him in `data/history/pins.json` (`"playoff_loss_offset": ["HOU"]`). Stored games: the Super Bowls, Carolina's 2034 run (1 seed; beat NYG in the Divisional round and PHI for the NFC), Carolina 2034's 17 games (13-4, 535-347), and lower bounds for the teams it played. Canon from `Panthers Full History.pdf`: 0-17 in 2031 alone at the bottom; 4-13 in 2032 and 2033 tied for the worst record; Chicago the No. 1 offense 2029–2033 (Whitfield's Bears years).
2. **Default path (strength arcs):** each team's arc is a random AR(1) path with the NFL's spread (SD 3.0 wins, year-to-year correlation 0.65). It starts at the 2026 projection (§3.2), stretched ×1.2 because expected wins are compressed, and is bent to land on the 2035 record. It is then shifted to the coach's total, and each candidate draws different arcs (B14). The first version decayed both ends toward .500, which left the middle seasons flat. Candidates 4–6 had a win SD of 1.8–2.6 against 2035's 2.85, 0–4 twelve-win teams against 5, and up to 18 teams at 8-9. Their report is kept in `verify_output/history_team_arc_v1_candidates_4-6.md`. The objective also charges zig-zags more than steady rises and falls, and asks for about five 12-win and five 5-win teams and at most about 11 teams at 8-9 a season. Candidates 10–12: win SD about 2.6, five teams at 12+ and five at 5 or fewer in almost every season, and 5–11 teams at 8-9.
3. **Model:** OR-Tools CP-SAT, not PuLP. CBC found no feasible point in minutes; CP-SAT proves feasibility or infeasibility in seconds and finds a 1%-gap answer in about 3 minutes. Per team-season it chooses wins, a winning-season flag, a seed and a round flag. The bracket holds by seed: one Wild Card loser in each of 2v7, 3v6 and 4v5, then a reseeded Divisional round. Division winners have their division's most wins, seeds are ordered by wins, and wildcards have at least as many wins as any non-playoff team. The objective covers distance from the path and year-to-year swings (into 2035 too), plus soft story terms: COTY and MVP teams win 11+ and make the playoffs, COTY teams improve by 3+, Super Bowl teams win 11+ (weighted 2 per win short, after a first run gave a 7-10 champion), playoff teams win 9+, and Chicago wins 10+ in 2029–2033.
   - **Infeasible input:** `generate` stops and names the constraint families whose removal makes it feasible. Nothing is relaxed silently.
4. **Rows** (`finish`): division places use `division_tiebreak = 'GENERATED'` when records tie. Points come from wins (differential 31.5 × (W − 8.5) + noise; league mean rising from 383 in 2026 to 416 in 2035), balanced each season. Stored points are kept, and Chicago leads in scoring in 2029–2033. `verify` re-checks every fact on the rows.
5. **Strength path for phases 3–4:** refit from the applied records (phase 3).

**Written:**
- `team_season_results` rows for 2026–2034 (`source = 'SYNTH'`). Head coach comes from `coach_tenures`; `gm_id` is NULL before a GM's `start_season`.
- `league_seasons` rows (champion and runner-up from the Super Bowl games).
- `coach_season_lines` (B17).

`team_seasons` is left as it is.

### 3.4 Team stat totals: `engines/history_backfill/team_totals.py`

For each team-season, totals for passing, rushing, receiving, sacks, interceptions, tackles and kicking are drawn from the strength path and points. They're calibrated to the 2035–2036 team distributions (`team_stats`, `season_stats` sums) and stored in a staging table. These totals are soft targets for §3.5 and the source of fill-ins in §3.6.

### 3.5 Player lines (phase 3): `engines/history_backfill/player_lines.py`

**Who gets lines:**
- Every current player with a remainder
- Retired real veterans (§3.2)
- Retired award winners (B5): the 12 award rows with no `player_id` get `players` rows and are linked

**Active seasons:** from `years_of_experience` (or the 2026 roster). A player whose remainder is too small for every season gets fewer seasons (injuries, being a backup), taken from the end nearest his rookie year.

**Shape:** a position-based career curve (rookie ramp-up, peak, decline). It is weighted by:
- his team's share of the §3.4 totals
- his 2035 overall rating and development trait (higher ratings and better traits peak later and higher)
- his team's strength

**Hard rules:**
- **Exact sums:** each stat's seasons sum exactly to the remainder (integer rounding that keeps the sum exact).
- **Record book:** the `BOOK` holder's season equals the record value. No other generated season beats the franchise or NFL record as it stood in that season, including rookie-season records.
- **Awards:** an MVP or OPOY season is among the league's top three at the player's position in its key stats. A DPOY season leads in sacks, interceptions or tackles, or close to it. OROY and DROY seasons are rookie seasons (experience 0). An SB MVP played for that season's champion.
- **Canon:** stored canon rows (2032–2034 Panthers `season_stats`) are kept exactly.

**Written (all `SYNTH`):**
- `player_seasons`: team, position, age, `years_of_experience`; `overall_rating` NULL
- `season_stats`
- `season_stat_lines`

**As built (2026-10-26, PROJECT_SPEC §3.3k):** `cast.py` decides who played where. Current players' seasons come from years of experience, with the 2035 team except the real 2026 team in 2026 and award or record teams in their season. Real 2026 players outside the pool get a drawn retirement year. A record holder must play a position that can hold the record, otherwise he becomes a named fill-in. `team_totals.py` draws the §3.4 totals. `player_lines.py` splits them by group and rank, spreads each constrained player's remainder exactly, and lets free players fill the rest. Pool players who left after 2035 have no career row, so they are free like veterans. Seeded canon zeros count as unknown, and a remainder with nowhere else to go grows the seeded row (Crawford's 2034 attempts). No non-record season enters today's NFL top 10 or passes a franchise record as it stood, and award seasons qualify. Ka'Von Franklin's 2032 line goes back to the record book's 1,016 yards (`2026_12_29_franklin_2032_record.sql`). Applied 2026-10-26 (`apply-players --run 6`; `2026_12_30_history_player_rows.sql` adds the `source` columns and `players.origin`). The readers that span seasons skip SYNTH rows until phase 4 turns history on with its labels. §3.7's records-engine check is built (`records-check`, 2026-10-26): no record outside the book except four the book itself implies (PROJECT_SPEC §3.3l). The leader-range report is built too (`leaders-report`, 2026-10-26; PROJECT_SPEC §3.3l lists what it found); its five fixes went into run 19 (163 → 131 measures outside the 2035–2036 range).

### 3.6 Fill-ins (phase 3): `engines/history_backfill/fillins.py`

For each team-season and position group, the leftover is the §3.4 total minus the known players' lines.
- **Starter-sized leftover** (e.g. ≥ 60% of a typical starter's season at QB, RB, WR1/2, TE, edge rusher, corner): it becomes a fill-in player.
  - He has a seeded name, position, draft year and round, a career span that ends before 2035, and the teams he played for.
  - He gets `players.origin = 'HISTORY_FILLIN'` (new column; NULL for everyone else) and lines from §3.5's curve.
- **Smaller leftovers** stay unassigned. The team's totals then run a little ahead of its players' sums, as they would with untracked depth players.

### 3.7 Validation: `python3 -m engines.history_backfill validate`

Writes `verify_output/history_backfill_report.md` and checks:
- Every remainder is spread exactly.
- Every §1 fact holds: coach totals, Super Bowls, known records, awards, canon.
- Running the records engine over the generated seasons breaks no record that `BOOK` doesn't already hold.
- Each season's leaders and totals sit inside the 2035–2036 ranges.
- Lists of flagged players (unmatched names, bad remainders) and of fill-ins by team.

### 3.8 Workflow and CLI

```
python3 -m engines.history_backfill generate --seeds 3 [--pins data/history/pins.json]
python3 -m engines.history_backfill validate --candidate N
python3 -m engines.history_backfill apply --candidate N      # one transaction into the live tables
python3 -m engines.history_backfill revert                   # deletes every SYNTH row
```

- **`generate`:** writes only to staging tables (`history_candidates`, `history_team_seasons`, `history_player_lines`, `history_fillins`).
- **JR's review order:** standings and playoffs first, then the Panthers, then award winners, then the league leaders.
- **Pins:** in `data/history/pins.json`, e.g. "Bears 11-6 in 2029", or "Crawford's 2033 line". `generate` treats them as hard facts.
- **`apply` and `revert`:** both are idempotent.

### 3.9 Saleh baseline fix (phase 0, B8; applied 2026-10-26)

`db/migrations/2026_12_25_saleh_2025_baseline.sql` sets coach 8's 2025 `coach_seasons` row to all zeros. Afterwards, the Titans' 2025 → 2034 difference reads 83-70, 6 winning seasons, 10-4 in the playoffs and 1-1 in Super Bowls (a loss in 2026, a win in 2031). His coach profile then shows a Mu-era career only.

---

## 4. Schema changes

| Table | Change |
|---|---|
| `player_career_totals` | New (§3.1; `2026_12_26_player_career_totals.sql`, applied 2026-10-26) |
| `players` | New column `origin` (NULL, or `HISTORY_FILLIN`) |
| `team_season_results`, `league_seasons`, `coach_season_lines` | New column `source` (DEFAULT `'CAPTURE'`; `'SYNTH'` for generated rows); `2026_12_28_history_team_arc.sql`, applied 2026-10-26 |
| `player_seasons`, `season_stats`, `season_stat_lines` | Same `source` column (phase 3) |
| `awards` | New column `source` (only used if generated All-Pro teams are approved, §5) |
| `history_*` staging tables | `history_candidates` and `history_team_seasons` (phase 2, kept so a pick can be re-applied); player staging in phase 3 |
| `coach_seasons` | Data only: Saleh's 2025 row (§3.9) |

---

## 5. Consumers

- **Record Book:** the records engine reads `SYNTH` seasons like any other once they're applied. `BOOK` rows keep priority, and §3.7 proves the two agree.
- **Career layer** (continuity plan §3.5): `player_career_mu` covers 2026 onward. Labels say "since 2026", with a marker on generated seasons where the page shows season rows.
- **Awards Equity, Improvement and other prior-season inputs:** read full prior seasons where they read `team_season_results` or `season_stats`. Inputs computed from `games` still see only 2035 onward (B11).
- **Writer tools:** player career and team history tools return 2026–2034 as ordinary history (B15).
- **All-Pro 2026–2034** (records plan D5, optional): First and Second Teams picked from the generated lines, stored as `awards` rows with `source = 'SYNTH'`. Only with JR's go-ahead after phase 3.
- **Not affected:** Napper and fantasy (no `player_stats`), weekly analytics, power rankings.

---

## 6. Phases (one commit to `main` each, spec updated in the same commit)

| Phase | Delivers | Done when |
|---|---|---|
| **0. Facts and fixes** (built 2026-10-26) | Saleh migration; K1–K5 answered on the console; `start_2026.csv`, `rosters_2026.csv` and `nfl_careers_through_2025.csv` from `engines/history_backfill/sources.py`; `tests/test_history_sources.py` | The Titans' snapshot difference is 83-70 (done); every 2036 veteran has a real career row (57 of 57) |
| **1. Career totals** (code built 2026-10-26; console capture pending) | Capture (`--only career`, `--only career-teams`), extract (`--career`) and load; `player_career_totals`; remainders and their report | Every 2036 player has totals or a flag; no unflagged negative remainder |
| **2. Team arc** (done 2026-10-26: candidate 14 applied) | `team_arc.py`, playoffs, points; staging; `apply` for team rows; coach lines | 31 coach totals match exactly, every Super Bowl and known record holds, JR has picked a candidate |
| **3. Player lines and fill-ins** (done 2026-10-26: run 19 applied) | `team_totals.py`, `player_lines.py`, `fillins.py`, `validate`; `apply` / `revert` for player rows | The §3.7 report is clean; JR approves the Panthers, award winners and leaders |
| **4. Consumers** (built 2026-10-26; All-Pro 2026–2034 not done) | "Since 2026" career labels, history views, writer tools, optional All-Pro | A 2028 MVP's page shows his season; the Titans' history lists 2026–2036 |

---

## 7. Tests

- **`tests/test_history_sources.py`** (built): the 2026 projection, career sums and field-goal ranges, the namesake check.
- **`tests/test_history_career.py`** (built): `fits`, `settle`, name matching with and without team lists, the moved-team and name-slip pass, `games_played`, and the remainder's subtraction, columns without a career row and flags.
- **`tests/test_history_team_arc.py`** (built): the 64 bracket outcomes and reseeding, `bracket_ok`, `derive` on a synthetic league made from a known history (clean, bad totals, an extra playoff trip, an adjustment), a two-season solve that `verify` passes, and an impossible pin reported infeasible.
- **`tests/test_history_player_lines.py`:** exact integer sums, record ceilings and holders, award conditions, canon rows kept, fill-in thresholds.
- **No live writes in tests.** `apply` and `revert` are checked on a restored copy through `DATABASE_URL`. The Napper tests are never run wholesale (memory).

---

## 8. Spec updates (per phase)

- §1.1 Repository Layout: `engines/history_backfill/`, the pipeline loader, `data/history/`, migrations, tests and this plan.
- §3: `player_career_totals`, `players.origin`, the `source` columns, Saleh's baseline row (§3.3a).
- §2.9 Record Book: `SYNTH` seasons and `BOOK` priority.
- §1.2 Running the System: the `history_backfill` CLI.

---

## 9. Risks

- **The Career Stats list is incomplete (K3):** missing players have unknown totals and get no generated lines, only flags.
- **Madden's pre-2026 numbers don't match nflverse (K5):** JR confirmed they include real stats through 2025; phase 1's remainder report still flags any veteran whose remainder goes negative.
- **Infeasible facts:** e.g. a stored 2034 record that conflicts with a coach total. Reported, never relaxed; JR decides.
- **Canon lock-in (B15):** after `apply`, mistakes are fixed by pins and a rerun, not hand edits. `revert` keeps that cheap until writers have used the history.
- **Shared checkout:** the season archive and coaching carousel sessions are editing `init_db.sql`, `PROJECT_SPEC.md` and `engines/coach_*`. Stage only this work's hunks, and agree on migration dates.
