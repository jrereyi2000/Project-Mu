# 2026–2034 History Backfill: Implementation Plan

**Status (2026-10-26):** Phase 0 built: Saleh baseline fix applied, console facts K1–K5 answered (§2), and the three input files in `data/history/` (`engines/history_backfill/sources.py`, §3.1–§3.2). Phase 1's code built (2026-10-26): Career Stats capture, extract, load into `player_career_totals` (migration applied) and the remainder report; the console capture itself hasn't run yet and must before this offseason's retirements (§3.1). Phases 2–4 not built. JR's decisions are in §0.
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
| B13 | **Panthers 2026–2030:** middling years that kept Canales in the job (about .450–.550), with no Super Bowl trip. The 0-17 in 2031 starts the decline. |
| B14 | **Three candidate histories.** The generator writes three, each from a different random seed. JR picks one, can pin any season or line, and the run is refit around the pins (§3.8). |
| B15 | **Approved history is canon.** Once applied, `SYNTH` rows are data under the hallucination policy, and writers must not contradict them. Regenerating the history needs JR's go-ahead. |
| B16 | **Player teams:** each player spends his 2026–2034 seasons with his 2035 team unless something already stored says otherwise (a canon fact or a pin). Team moves can be added later as texture. |
| B17 | **Coach records stay "since 2035" on screen** (continuity plan D2). The generated 2026–2034 `coach_season_lines` exist for history views and writer tools. Their sums must equal the 2025 → 2034 snapshot differences exactly. |

---

## 1. Survey: what's already fixed (live database, 2026-10-26)

| Fact | Coverage | Use |
|---|---|---|
| Coach snapshot differences, 2025 → 2034 | 31 teams: exact 9-season W-L (153 games, no ties), number of winning seasons, playoff W-L, Super Bowl W-L. Includes the Titans after B8, the Broncos (Gaffney) and the Chiefs (Leonard), who all started 0-0 in 2026. The Buccaneers' Todd Bowles is the one coach whose 2025 row is real history but not Madden's start: Madden began him with one more loss (PROJECT_SPEC §3.3a), so the Bucs' 2026–2034 record is 82-71, not the row difference of 82-72. The Panthers have none (B9). | Hard limits on standings |
| Super Bowls | Both teams and the score for every season, 2026–2035 | Hard: champion and runner-up |
| 2034 records | 18 teams in `team_seasons` | Hard |
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
  1. `python3 madden_capture.py --only career --out RUN`: Stats & Awards > **Career Stats** (the right-hand tile column: In-Season Awards, NFL Record Book, Career Stats, Coach Stats; `open_career_stats`), every category into `career/NN_<cat>_NNN.png` through the Season Stats code (`run_stats(kind="career")`; `career/done.txt` for `--resume`). The cut-off check takes as its floor an earlier `career_<cat>.csv` or the season list's rows (a career list is at least as long). The first run should be watched: if the opened list shows no CAREER label, a frame is saved to `stop_debug/` to confirm it isn't the Season Stats list.
  2. `python3 madden_capture.py --only career-teams --out RUN`: the same list team by team for the categories whose league-wide list stops at 250 rows (`CAREER_TEAM_CATEGORIES`: rushing, receiving, defense, blocking; `--category` to change) into `career_scope/<team>_<cat>_NNN.png` (`run_career_by_team`; `career_scope/done.txt` lists finished teams).
  3. `python3 extract.py RUN --career`: `career_<cat>.csv` and `career_scope_<team>_<cat>.csv`, parsed like the season lists (a full `extract.py RUN` does them too).
  4. `python3 -m pipeline.load_career_totals RUN [--dry-run] [--allow-missing]`.
- **Load** (`pipeline/load_career_totals.py`, one transaction) writes **`player_career_totals`** for league_state's season, replacing that season's rows per category: `captured_season`, `captured_week`, `capture_folder`, `category`, `player_id` (NULL when unsettled), `list_name`, `position`, `stats` JSONB in `season_stat_lines` column names (defense `def_*`, kicking `kick_*`, punting `punt_*`, returns `kr_*`/`pr_*`, blocking `blk_*`) plus `games_played` from GP, `flags`. Unique per (`captured_season`, `category`, `player_id`).
  - **Matching:** a team list's row by list name key + position + that team (flag `team_list`); then the league-wide rows by name key + position against the season's roster, skipping rows a team list already settled. Several players sharing a name and position are settled by their season lines: no counting stat of a career line may be below the same player's season total (`fits`; yards are left out since they can go down), and exactly one assignment must work (`settle`). Otherwise the row is stored with `player_id` NULL and `unmatched` / `ambiguous`; a single candidate whose season total is higher gets `below_season_total`. Two rows differing in at most two cells are one row read twice (`misread_copy`).
  - **Report:** per category the rows, matches, flags and the players with a season line but no career row, with a warning naming the `--only career-teams --category` capture when a category without team lists is cut off. A missing category CSV stops the load unless `--allow-missing`. Tested on 2026-10-26 by loading the 2036 season lists as career lists in a dry run: 1,214 of 1,219 rows matched (the 5 others match no 2036 roster name), and the 250-row cut-off showed in receiving (66) and defense (99).
- **Remainder** (`python3 -m engines.history_backfill.remainder [--season S]`; `compute()` is pure; writes `verify_output/history_remainders_<S>.csv` and `.md`, nothing to the database): for each player with career totals and each counting stat (averages, rates and longs aren't summable; `games_played` is):
  - **mu = career − Σ captured seasons 2035..S (`season_stat_lines`) − pre-2026 NFL (B6)**; **generate = mu − Σ stored canon rows 2026–2034 (`season_stats`, the Panthers' 2032–2034 lines)**. `generate` is what phase 3 spreads over the player's open seasons, max(2026, first season)..2034 (first season = season − `years_of_experience`).
  - **Flags:** `negative`, `no_open_seasons` (a remainder but a first season of 2035 or later), `missing_pre2026` (entered before 2026, no real-career row), `untracked_pre2026` (stats nflverse doesn't carry, e.g. broken tackles, YAC, run after catch, sacks allowed, so the remainder still holds his real pre-2026 share; listed per player). Players with a 2035..S line but no career row are listed separately.
  - Checked on 2026-10-26 with careers built from each player's own 2035–2036 lines plus his real pre-2026 line: every remainder was zero except the four canon Panthers (Crawford, Davis, Franklin, Tucker), whose stored 2032–2034 lines show as negative, as they must until their real career totals are captured.
  - **Pre-2026 NFL:** `data/history/nfl_careers_through_2025.csv` (`sources.py nfl-careers`): nflverse regular-season player stats for 2016–2025 summed per player and renamed to the `season_stat_lines` columns (sums only; longs and rates aren't summable). It covers every real 2026 roster player and all 57 players in the 2036 pool who entered the league before 2026 (11+ years of experience). Players are matched by `espn_id` (nflverse `players.csv` maps it to the stats' gsis id), or, for a 2036 veteran with neither an `espn_id` nor a 2026 roster row, by a unique name among nflverse players whose rookie season is 2025 or earlier. A player with fewer than 10 years of experience in 2036 entered after 2026 and never gets a real career, even if his stored `espn_id` points at a real namesake.
  - **Bad remainders:** a negative remainder, or a positive one for a player who couldn't have played 2026–2034, is flagged and held out of generation until it's resolved.

### 3.2 The 2026 starting league

- **Team strength in 2026:** `data/history/start_2026.csv` (`sources.py start-2026`): the real 2026–27 standings from ESPN projected to the season's end. A team's rating is its point differential per game shrunk toward zero (PD / (G + 4)); each unplayed game on its ESPN schedule is won with probability Φ((r − r_opp ± 1.5) / 13.5) (1.5 points of home edge); projected points add the remaining games at a scoring rate shrunk 4 games toward the league's. Built 2026-10-26 after 6–7 games. Rerun it later in the season (`--refresh`), and with the final standings if they arrive before JR approves the history.
- **2026 rosters:** `data/history/rosters_2026.csv` (`sources.py rosters-2026`): ESPN's current rosters (2,557 players incl. practice squad and reserve lists, as of 2026-10-26; rerun near season's end). Each is matched to `players` by `espn_id`, else by a unique full name. A match is kept only if the player has at least 9 more years of experience in 2036 than in 2026 (`plausible_veteran`), so a fictional namesake isn't mistaken for him (`match = espn_id_conflict` when the stored `espn_id` fails this). `in_pool_2035` / `in_pool_2036` say whether he is still in the league. On 2026-10-26: 194 matched, 130 in the 2036 pool. They identify two groups:
  - **Retired real veterans:** on a 2026 roster but not in the 2035/2036 pool. They get `players` rows if missing, real careers through 2025 from `nfl_careers_through_2025.csv`, and Mu-era lines from §3.5 with a decline curve and a generated last season.
  - **The 2026 rookie class:** real players, so their first season is fixed.

### 3.3 Team arc (phase 2): `engines/history_backfill/team_arc.py`

1. **Default path:** each team's strength runs from 2026 (§3.2) to 2035 (record, point differential and team overall, all captured), with a yearly pull back toward the league average (parity). That gives the default curve.
2. **Standings:** an integer program in PuLP (installed) chooses wins for every team-season, 32 × 9 values:
   - **Hard:** every season sums to 272 wins with no ties; the §1 totals; the known records (2034 and the Panthers); a winning season means 9 or more wins.
   - **Objective:** stay close to the default curve, penalize large year-to-year swings, and add the story preferences (B13; MVP and COTY seasons; a long low stretch before a rise; a young, highly rated 2035 core implies earlier high draft picks).
   - **Infeasible input:** if the facts can't all be met, the report names the constraints in conflict. Nothing is relaxed silently.
3. **Playoffs:** each season gets 7 seeds per conference and a valid bracket.
   - Seeds and division winners follow that season's records.
   - Each team's playoff W-L sums to its coach total, and the stored Super Bowl matchup comes out of the bracket.
   - Division places use `division_tiebreak = 'GENERATED'` when records tie, because without games the NFL tiebreakers can't be computed.
4. **Points:** `points_for` and `points_against` are drawn from the team's strength and record, calibrated to the 2035–2036 scoring environment.
5. **Strength path:** the final strength path is refit from the chosen records, so it always agrees with them. It drives §3.4–§3.6.

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
| `player_seasons`, `season_stats`, `season_stat_lines`, `team_season_results`, `league_seasons`, `coach_season_lines` | New column `source` (DEFAULT `'CAPTURE'`; `'SYNTH'` for generated rows) |
| `awards` | New column `source` (only used if generated All-Pro teams are approved, §5) |
| `history_*` staging tables | New; dropped or emptied after `apply` |
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
| **2. Team arc** | `team_arc.py`, playoffs, points; staging; `apply` for team rows; coach lines | 31 coach totals match exactly, every Super Bowl and known record holds, JR has picked a candidate |
| **3. Player lines and fill-ins** | `team_totals.py`, `player_lines.py`, `fillins.py`, `validate`; `apply` / `revert` for player rows | The §3.7 report is clean; JR approves the Panthers, award winners and leaders |
| **4. Consumers** | "Since 2026" career labels, history views, writer tools, optional All-Pro | A 2028 MVP's page shows his season; the Titans' history lists 2026–2036 |

---

## 7. Tests

- **`tests/test_history_sources.py`** (built): the 2026 projection, career sums and field-goal ranges, the namesake check.
- **`tests/test_history_career.py`** (built): `fits`, `settle`, name matching with and without team lists, `games_played`, and the remainder's subtraction and flags.
- **`tests/test_history_team_arc.py`:** the integer program on a small synthetic league (exact totals, Super Bowl facts, winning-season counts, a reported infeasibility), and bracket validity.
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
