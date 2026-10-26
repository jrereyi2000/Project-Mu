# 2026–2034 History Backfill: Implementation Plan

**Status (2026-10-26):** Written, not built. JR's decisions are in §0.
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
| Coach snapshot differences, 2025 → 2034 | 31 teams: exact 9-season W-L (153 games, no ties), number of winning seasons, playoff W-L, Super Bowl W-L. Includes the Titans after B8, the Broncos (Gaffney) and the Chiefs (Leonard), who all started 0-0 in 2026. The Panthers have none (B9). | Hard limits on standings |
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

## 2. Console facts to confirm (before phase 1's capture)

| # | Question | Why it matters |
|---|---|---|
| K1 | Where is the Career Stats page, and can every step to it be read without pressing Cross on anything that changes state? | It must be a read-only screen added by name to the capture allow-list (the `LEAGUE_CROSS_OK` pattern). |
| K2 | Which categories and columns does it show, and are they the same as Season Stats? Regular season only, or playoffs too? | Decides the `player_career_totals` columns and what gets subtracted. |
| K3 | Does each list show every active player, or only leaders or players above a minimum? | Players missing from a list have an unknown total, not zero (the same rule as `season_defense_values`). |
| K4 | Does the career total include the current season so far? | The capture must run in the same session as a Season Stats capture, with no games in between, so the subtraction is exact. |
| K5 | For two or three veterans, does Madden's pre-2026 total match Pro-Football-Reference? | Checks the B6 subtraction. A consistent gap (e.g. Madden leaves out some stat) gets corrected for every veteran. |

---

## 3. Design

### 3.1 Career totals capture (phase 1)

- **Capture:** `madden_capture.py --only career`, which saves `career/{category}_NNN.png` pages through the existing list capture (`capture_list` / `capture_wide_list`). It runs right after a Season Stats capture (K4).
- **Extract:** `extract.py` parses the pages the same way as season lists into `career_stats.csv`.
- **Load:** `pipeline/load_career_totals.py` writes **`player_career_totals`**:
  - Key: (`player_id`, `captured_season`, `category`). Other columns: `captured_week`, `capture_id`, `stats` JSONB (the category's columns), `flags`.
  - Names are matched with `pipeline/capture_matching`. An unmatched or ambiguous name is stored with `player_id` NULL and a flag, never guessed.
- **Remainder** (`engines/history_backfill/remainder.py`, pure): for each player and stat, the 2026–2034 share is:
  - **remainder = career − Σ captured seasons (2035, 2036 to date) − Σ stored canon rows (2032–2034 Panthers) − pre-2026 NFL (B6)**
  - **Pre-2026 NFL:** a one-time Pro-Football-Reference pull, `data/history/pfr_careers_through_2025.csv`, covering every player whose first season is 2025 or earlier.
  - **Bad remainders:** a negative remainder, or a positive one for a player who couldn't have played 2026–2034, is flagged and held out of generation until it's resolved.

### 3.2 The 2026 starting league

- **Team strength in 2026:** each team's 2026 strength comes from the projected final 2026–27 NFL standings and point differentials. The projection is made at build time from current records plus the remaining schedule, and stored as `data/history/start_2026.csv`. If the real final standings arrive before JR approves the history, they replace the projection.
- **2026 rosters:** real end-of-season rosters, keyed by `players.espn_id` where possible, stored as `data/history/rosters_2026.csv`. They identify two groups:
  - **Retired real veterans:** on a 2026 roster but not in the 2035/2036 pool. They get `players` rows if missing, real careers through 2025 from Pro-Football-Reference, and Mu-era lines from §3.5 with a decline curve and a generated last season.
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

### 3.9 Saleh baseline fix (phase 0, B8)

A migration (next free date, `_saleh_2025_baseline.sql`) sets coach 8's 2025 `coach_seasons` row to all zeros. Afterwards, the Titans' 2025 → 2034 difference reads 83-70, 6 winning seasons, 10-4 in the playoffs and 1-1 in Super Bowls (a loss in 2026, a win in 2031). His coach profile then shows a Mu-era career only.

---

## 4. Schema changes

| Table | Change |
|---|---|
| `player_career_totals` | New (§3.1) |
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
| **0. Facts and fixes** | Saleh migration; K1–K5 answered on the console; `start_2026.csv`, `rosters_2026.csv` and the Pro-Football-Reference career file | The Titans' snapshot difference is 83-70; every 2026 roster player is matched or flagged |
| **1. Career totals** | Capture, extract and load; `player_career_totals`; remainders and their report | Every 2036 player has totals or a flag; no unflagged negative remainder |
| **2. Team arc** | `team_arc.py`, playoffs, points; staging; `apply` for team rows; coach lines | 31 coach totals match exactly, every Super Bowl and known record holds, JR has picked a candidate |
| **3. Player lines and fill-ins** | `team_totals.py`, `player_lines.py`, `fillins.py`, `validate`; `apply` / `revert` for player rows | The §3.7 report is clean; JR approves the Panthers, award winners and leaders |
| **4. Consumers** | "Since 2026" career labels, history views, writer tools, optional All-Pro | A 2028 MVP's page shows his season; the Titans' history lists 2026–2036 |

---

## 7. Tests

- **`tests/test_history_remainder.py`:** subtraction, flags and the pre-2026 split, on fixtures.
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
- **Madden's pre-2026 numbers don't match Pro-Football-Reference (K5):** checked on a few veterans before phase 1 relies on them.
- **Infeasible facts:** e.g. a stored 2034 record that conflicts with a coach total. Reported, never relaxed; JR decides.
- **Canon lock-in (B15):** after `apply`, mistakes are fixed by pins and a rerun, not hand edits. `revert` keeps that cheap until writers have used the history.
- **Shared checkout:** the season archive and coaching carousel sessions are editing `init_db.sql`, `PROJECT_SPEC.md` and `engines/coach_*`. Stage only this work's hunks, and agree on migration dates.
