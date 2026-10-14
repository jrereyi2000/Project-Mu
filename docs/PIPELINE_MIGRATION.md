# Pipeline migration: handoff for Claude Code

Plan agreed with JR on Oct 13, 2026 in a claude.ai planning session. The full plan also lives in the claude.ai doc "Project Mu Pipeline Migration Plan"; this file is the copy Claude Code works from. If this file and the code disagree, trust the code and say so. If this file seems wrong or underspecified, ask JR before improvising.

## Current state (Oct 14, 2026)

- **Phases 1–4 are done**; phase 3 ran live on Oct 14 (backup `db/backups/madden_backup_20261014_093457.sql`), phase 4 on Oct 14 (code only, no live writes; backup `madden_backup_20261014_095107.sql`). **Phase 5 started Oct 14 (JR): code built and unit-tested; the console and end-to-end exit runs are still to do** (see "Phase 5 status" below).
- Phase 4: the backfill (`generate_team_and_player_stats()`, `python3 -m engines.game_stat_generator [--season N]`), `pipeline/main.py`'s trigger, `db/db_handler.py` and the old `batch_historical` / `batch_menus` tools take the season from `league_state.current_season`. `madden_capture.py`'s schedule step writes `week.json` (`current` = the week the schedule opened on, `captured`); the weekly load sets `league_state.current_week` from it in the same transaction (postseason: 19 Wild Card, 20 Divisional, 21 Conference, 22 Super Bowl bye, 23 Super Bowl; no `week.json` = loaded week + 1; never moves back; stops if the labels don't fit schedule.csv). `get_current_league_state()` no longer infers the week from `games` or writes it. `current_season` is still set by hand at a new season. Left as is: `pipeline/ocr_extractor.py`'s 2035 schema default (retired in phase 6). The engines', `newsroom/weekly_run.py`'s and the API's 2035 defaults were changed afterwards (see the engines and API follow-up below).
- Phase 4 test (copy `madden_p4test`, dropped afterwards): the Week 17 weekly capture with `week.json` (`SEASON WEEK 18`) dry-ran with season 2035 / week 17 / current week 17 → 18 and nothing written; loaded for real it set 18; a `week.json` naming week 16 stopped the load with nothing written. Console run (Oct 14, `captures/2026-10-14_1000`, `--only schedule`, not loaded since Week 17 already is): the schedule opened on SEASON WEEK 18, captured Week 17 in 3 pages, wrote `week.json` `{"current": "SEASON WEEK 18", "captured": "SEASON WEEK 17"}`; all 3 screenshots match the extracted CSV and its 16 scores match `games`.
- **Open (JR, Oct 14):** the off-season / new-season architecture (when and how `current_season` changes) is JR's to decide; until then it is set by hand.
- **Engines and API follow-up (JR, Oct 14, after phase 4):** JR approved an exception to "engines and the API are out of scope" to fix bugs the phase 3 data exposed (backup `madden_backup_20261014_125531.sql`; tried on the copy `madden_p4fix`, dropped afterwards, then live).
  - Backfill: reads `DATABASE_URL` (was hard-coded to live), only this season's stored lines, deducts them by `player_id` on any team (teammates sharing a list name, e.g. CAR T. Davis WR / CB, deducted each other's lines), inserts with `player_id`. On the copy, Weeks 15–16's 677 backfilled lines were cleared and refilled; the exit check stayed at the known 18 + 4.
  - Engines (GOTW, TWI, Playoff Clout, records, fantasy projections) and the API join stat lines on `player_stats.player_id` instead of names: TWI's Y_prod and Playoff Clout counted same-name teammates' lines twice (CAR Y_prod 48.9 → 61.5), and the API's leaderboards and player game logs missed 11 players with stats.
  - Engines, `newsroom/weekly_run.py` and the API take their default season (API: also week) from `league_state`; the API reads `DATABASE_URL`.
  - 57 `players.full_name` values were all caps from the old video scanner ("JERRY RAYFORD"); the roster load now prefers the card's casing over an all-caps stored one, and `db/migrations/2026_10_27_name_casing.py` re-cased them on live from the loaded roster capture.
  - Still to do in their phases: **phase 5**, `game_stat_generator` catches every engine error and `pipeline/main.py` publishes anyway, so an engine failure must stop the week like a failed load; **phase 6**, `db/db_handler.py` imports its models from `pipeline/ocr_extractor.py` at module level and nearly everything uses `DatabaseHandler`, so move those models (or drop the stream-only methods) before archiving it.
- Live database: 1,719 players (1,698 with a number; the other 21 wear #0), all on a team; 362 `season_stats` rows for 2035 keyed on `player_id`; 256 games COMPLETED through Week 17, Week 18's 16 SCHEDULED; `league_state` 2035 / 18.
- `captures` holds 4 loaded runs: roster, season schedule, Week 17 weekly, Week 17 games (`--replace`). Every other 2035 game was refilled by the backfill from the corrected totals (`tools/rebuild_backfill.py`); the Panthers' games and the Week 17 games capture are real lines and were kept.
- Live check (`tools/check_capture_load.py --db madden --season 2035`): 340 of 362 players' game lines add up to `season_stats`. The 18 that don't are known: real game lines for players outside the Season Stats top lists, old stream lines in Panthers games (G. Crawford, K. Franklin, R. Tucker), J. Randolph. 4 more are negative season totals the backfill can't write. Team-games with lines: 512 of 512.
- `llm_usage` is still empty on live: token logging was added after these captures were extracted, so the first live numbers come with the next weekly extract (on the test copy, thinking was about 40% of Gemini's extraction output).
- The phase 3 test copy `madden_p3test` was dropped on Oct 14. A later phase that needs one restores a fresh backup into a new test database (never with `db/restore_db.sh`, which drops the live `madden` database).
- Known limits carried forward: the backfill never writes negatives; Season Stats only lists each category's top players, so lines for the rest come from game captures only; a shared Season Stats name that history can't settle needs a team-scoped capture (`--scope <TEAM> --category <CAT>`) before the weekly load.

## How to work through it

- Do **one phase at a time**, in order. Stop when the phase's exit criteria are met and report back; do not start the next phase unprompted.
- Every phase that touches the database starts with `./db/backup_db.sh`, and is tried against a restored copy before the live database.
- `CLAUDE.md` rules apply: update `PROJECT_SPEC.md` with every change; commit into `main`; leave other sessions' uncommitted changes alone.
- Engines (`engines/*` except the season fix in phase 4 and the Oct 14 follow-up above), the newsroom and the API are **out of scope**. They must keep reading the same columns they read today.

## Testing on the console

Claude Code runs on JR's Mac and is expected to **test each phase on the real console**, not only with unit tests.

- **Before any run that presses keys**, tell JR and confirm: chiaki-ng is open and connected, Madden is in the franchise menus, and nobody is using the controller. Always run `madden_capture.py --test` first; it presses nothing.
- **Spot-check OCR by looking at the screenshots yourself.** After each capture, open a sample of screenshots (at least 3 per mode, including the first and last page) and compare them with what was read:
  - roster: for 10 players across 3+ teams, check full name, number, position, OVR, dev trait, age, height, weight and experience on the card screenshot against the parsed values;
  - games: for 2 games, check the box score and 5 player lines per team against the CSVs;
  - season schedule: check 3 weeks against the screenshots, including week 1 and week 18.
  Report any mismatch, with the screenshot path, before moving on.
- **Every new or changed capture mode is run end to end on the console** before its phase counts as done, and re-run after any fix to it.
- **Never** press Cross on Advance Week, Force Win, or anything that changes the franchise. If the script stops on an unexpected screen, leave the console as it is and tell JR.
- Unit tests go in `tests/` for anything that doesn't need the console (CSV parsing, name matching, loader checks), and `python3 -m pytest tests` must pass before each commit.

## Mapping screens the script doesn't know yet

`madden_capture.py` navigates by reading the screen, not by fixed key sequences: `classify()` identifies the screen from its OCR text, `Frame.highlighted()` finds the selected label by brightness, and `move_to()` presses toward a target label and re-reads after every press. `MANAGE ROSTER` is already in `HUB_TABS`, so reaching that hub tab works like `to_league_tab()`. Everything inside it is **unmapped**: the roster list, its team dropdown and the player card. So are a game's box score and per-team player stats from the League Schedule. Map them before writing capture code:

1. **Ask JR for the route first.** He knows the menus. The roster route is already known (see Verified facts: Manage Roster → Roster tile → L2 team dropdown; card shown for the highlighted player). The box-score route is known too (League Schedule → Cross on a completed game → top and bottom screenshots → R1 for player stats). Map the screens along both routes with `--step` before writing capture code.
2. **Add a single-step explorer**, e.g. `madden_capture.py --step <key>`: press one key, wait, save a screenshot plus its OCR text to `captures/<run>/explore/NN_<key>.png/.txt`, and print what `classify()` makes of it. `--step none` only captures. Like `--probe-l2`, this is a tool for building the flow, not part of it.
3. **Walk the route one press at a time with `--step`**, opening every screenshot to see what happened. Up, down, left, right, L1/R1 and Circle are safe to explore. **Cross (select) needs JR's OK** on any screen not yet mapped, because it can trigger actions (trades, cuts, Force Win, Advance Week). Never Cross on Advance Week or Force Win.
4. **Record each new screen** in `classify()` with labels that only that screen has (from the OCR text), and add its menu labels as lists like `LEAGUE_MENU`. Note anything odd, such as dropped presses, held-button dropdowns like L2, or highlight colors.
5. **Then write the `open_roster()` / `open_box_score()` functions** the same way `open_player_stats()` is written. Run them on the console with `--test` checks in between.

## Commits

- Commit after **every working, tested change**, not once per phase: e.g. "add Escape to key map", "add --only season-schedule", "roster card OCR", "migration: captures table", "loader: schedule results". Each commit leaves the repo working, with `PROJECT_SPEC.md` updated in the same commit.
- Follow `CLAUDE.md`: bring each commit into `main` right away; stop on a merge conflict; don't push; don't commit other sessions' uncommitted changes (several files were already modified before this work started; leave them alone unless JR says otherwise).
- At the end of each phase, report what was committed (one line per commit) and how the exit criteria were checked.

## Decisions already made (do not reopen without asking)

| Decision | Why |
| --- | --- |
| Keep the live stream (`pipeline/main.py`) only as a **trigger**: see `ADVANCE_WEEK`, then fire on the next `MAIN_MENU` | Games (CPU and user) are only final after Advance Week. Polling chiaki-ng would disconnect the player |
| All stats come from `madden_capture.py` + `extract.py` over chiaki-ng, not from stream frames | The stream only records what the user scrolls; the capture gets every page for the whole league |
| ~~Capture every game's box score and both teams' player stats every week~~ **Changed by JR (Oct 14):** the weekly capture stays Season Stats + Team Stats + the previous week's schedule (~20 min). `--only games` (box score + both teams' player stats) is used **only to recover a skipped week** | Measured Oct 14: ~7 min a game, ~1 h 50 min a week, against ~20 min for the season/team/schedule capture. The score-weighted backfill stays the normal way per-game lines are filled |
| Season Stats is still captured weekly, as a **cross-check** | Each player's game lines summed over the season must equal his Season Stats total |
| Every stat the capture finds gets a **database column**, ignored by everything for now | JR wants the data kept; consumers come later |
| A failed load **stops the pipeline for that week**: nothing written, no backfill, no engines, no articles | Bad data must not reach articles |
| chiaki-ng is **already open**; the script connects to it. **Exception (JR, Oct 13):** when the stream freezes (no key reaches the console), the script force-quits chiaki-ng and relaunches the stream itself (up to 3 times per run) before stopping | JR's setup. The stream froze several times on Oct 13 over Wi-Fi; JR's fix was always force quit + reopen. A polite quit + relaunch, plus an Escape press, once left the console ignoring input |
| Remote Play **ends the broadcast and disconnects the controller**. JR turns the controller back on (which ends Remote Play) and restarts the broadcast himself | Expected, accepted |
| Notices (capture started, done, week stopped) go to JR's **phone via ntfy** | JR's choice |
| **Stats tables stay on Gemini** (`extract.py`). Roster: the **list pages go to Gemini**, the **player card is read with Apple Vision on the Mac**, except **AGE/HT/WT/EXP, which Gemini reads from the card crops** (20 per request) | Tested Oct 13 (see "OCR test results"): Apple Vision skips short values (1–2 characters) in tables, so it can't read tables alone; it reads card text well. On console screenshots it drops or misreads the digit 1 and misreads heights silently (Hahn 6'3" read as 6'8"), so JR moved those four values to Gemini (Oct 13, phase 2) |
| `player_seasons.overall_rating` = the roster **list OVR** (morale-boosted, how players perform). The card's base OVR and morale are **not stored** | JR's rule. The card OVR is the base rating; the list OVR includes the morale boost and is how players play. A once-a-season scan would make base OVR and morale stale |
| After a successful load, screenshots convert to **WebP quality 90, full resolution**; debug folders are deleted; CSVs kept. Failed runs keep their PNGs | Phase 1 folder was 1.3 GB/week; this brings it to ~45 MB |
| Phase 5 settings (JR, Oct 14): the trigger watches **`https://twitch.tv/jrereyi`** (`BROADCAST_URL` in `.env`); it fires **30 seconds** after the Weekly Hub settles following Advance Week; notices go to a generated private ntfy.sh topic (`NTFY_TOPIC` in `.env`); the **shrink** step is part of phase 5 | JR's answers when phase 5 started |
| A `captures` table records every run | Duplicate protection, missed-week check, fallback trigger, row-count checks, resuming, token cost |
| **Hybrid storage** (JR, Oct 14): offense extras as columns on `season_stats` / `player_stats`; every other category in its own season- and game-level tables; views join them | Keeps every reader and the backfill unchanged (no zero rows for defenders), one table per capture category, new data on player ids |
| **`player_id` keys everywhere** (JR, Oct 14); `player_name` kept as a column and the old keys kept for the backfill, with a trigger filling `player_id` | Stored rows are never ambiguous; readers that join on names keep working |
| **An ambiguous Season Stats name stops the load** (JR, Oct 14) and asks for a team-scoped capture | No guessing about whose stats a row is |
| **Gemini token usage goes to a `llm_usage` table** (JR, Oct 14), one row per call, from `extract.py` and the newsroom | Replaces the cost estimates |
| **Rebuild 2035's backfilled lines once, keeping every Panthers game** (JR, Oct 14): `tools/rebuild_backfill.py` after the first weekly load | The old ingestion had put lines under the wrong players and the backfill had shared those totals over weeks 1-16; Panthers games' lines are real (JR's games) |

## Verified facts

- **Phase 1 is done.** `captures/2026-10-13_1413/` holds 9 stats categories, 3 team tabs and the schedule, extracted with no manual fixes.
- **CSV headers** (from that folder):
  - `stats_passing`: NAME,POS,YARDS,COMP,ATT,TD,INT,COMP%,YPG,RATING,YPA,LONG,SACKS,GP,DP
  - `stats_rushing`: NAME,POS,ATT,YARDS,AVG,TD,AVG G,FUMB,BTK,YAC,LONG,GP,DP
  - `stats_receiving`: NAME,POS,REC,YARDS,AVG,AVG G,TD,RAC,RAC AVG,LONG,GP,DP
  - `stats_blocking`: NAME,POS,SACK,GP,DP
  - `stats_defense`: NAME,POS,SOLO,ASSISTS,TAK,TFL,SACK,INT,INT YDS,INT AVG,INT LG,DEFL,FFUMB,FMBREC,FMBYDS,BLOCK,SFTY,TD,GP,DP
  - `stats_kicking`: NAME,POS,FGM,FGA,FG%,FG LNG,FG BLK,XPM,XPA,XP%,XPB,FGM29,FGA29,FGM39,FGA39,FGM49,FGA49,FGM50,FGA50,KO,TB,GP,DP
  - `stats_punting`: NAME,POS,PUNTS,YARDS,AVG,NET YDS,NET AVG,BLOCK,IN20,TB,LONG,GP,DP
  - `stats_kickreturn` / `stats_puntreturn`: NAME,POS,KR|PR,YARDS,AVG,TD,LONG,GP,DP
  - `team_conversions`: TEAM,3DC,3DA,3D%,4DC,4DA,4D%,2PC,2PA,2P%
  - `team_redzoneefficiency`: TEAM,ATT,TD,FG,OFF%,DATT,DTD,DFG,DEF%
  - `team_penalties`: TEAM,PEN,YARDS
  - `schedule`: WEEK,GAME TIME,MATCHUP,RESULT,PLAYED,FORCE WIN, e.g. `Week 17,Sun 1:00 PM,Lions @ Saints,DET 38 - 24 NO,0,None`
  - Numbers can contain commas (`"5,121"`) and `%`. What `DP` means is unconfirmed; store it as-is.
- **Player stats CSVs have no team column**, and names are `G.Crawford` while the DB stores `G. Crawford`. Madden position labels (MIKE, REDG, LEDG, HB…) match `player_seasons.position` exactly.
- **Match rate against the Oct 12 backup** (name with spaces/periods stripped + position, `player_seasons` for 2035): 1,085 of 1,214 rows unique, 15 ambiguous (e.g. B.Thompson WR, J.Alonso HB, J.Delgado K), 114 unmatched. 50 unmatched are linemen in Blocking: `player_seasons` has ~1,499 players (~47/team) and is missing many linemen. Of 16 rushing/receiving misses, 13 exist in `season_stats` by name.
- **chiaki-ng**: the executable is `/Applications/chiaki-ng.app/Contents/MacOS/chiaki` (not `chiaki-ng`; `--help` prints nothing). JR confirmed `.../chiaki stream "PS5-263" 192.168.1.181` connects to the console; after a **manual** connect, one Escape press closes the PlayStation menu; `chiaki stream` from the command line connects straight to the franchise page, so the scripts press nothing (JR, Oct 14). Other built-in options: `list`, `--passcode`, `--profile`, `--exit-app-on-stream-exit` (do not use; the app stays open), `--fullscreen/--zoom/--stretch`. Put the nickname and IP in `.env`.
- **Box-score route (from JR)**: League Schedule → highlight the game → **Cross** opens it. Take a screenshot at the top of the box score, scroll to the bottom, take a screenshot at the bottom. Then **R1** switches to player stats, which has category pages like Season Stats: capture them the same way `run_stats()` does (every category, every page). Both teams' players are shown by default, so no team switching is needed; the team for each row has to be read from the screen (column, logo or grouping) when mapping it.
- **Cross on the schedule list is currently blocked on purpose.** `cross()` only allows `HUB_LEAGUE`, `SCHED_TILES` and `STATS_TILES`, because on an unplayed week the schedule shows ACTIONS and FORCE WIN. Opening a box score needs a narrow exception: allow Cross on `SCHED_LIST` only when the week is earlier than the current week, the screen shows no ACTIONS or FORCE WIN, and the highlighted row has a final score. Otherwise refuse, as today.
- `madden_capture.py`'s key map has no Escape yet. chiaki keyboard map: Cross=S Circle=D L1=2 R1=3 L2=1 R2=4 D-pad=arrows.
- `pipeline/templates/ADVANCE_WEEK.png` exists (added by JR) alongside `MAIN_MENU.png`.
- **Roster route (from JR)**: Manage Roster hub tab → Roster tile → **L2 opens the team dropdown**. The roster list columns are NAME, CONV, POS, AGE, OVR, DEV, MOR, SAVINGS, PENALTY. The **player card is shown on the same screen for the currently highlighted player**. Card layout (from `data/frames/test_output_frames/`): base OVR badge; first name (small) over last name (large); a line `#64 DT | Nose Tackle | Elite Player` (number, position, archetype, role); dev trait as a word (Normal, Star, Superstar, Superstar X-Factor | ...); a grid with labels AGE, HT, WT, EXP, MORALE above their values; mindset and traits below. No button opens the card; moving the highlight down one row changes it, so one screenshot per row captures that player. The team dropdown needs **L2 held down**, like the stats scope dropdown (`--probe-l2` documents that behaviour: the list opens after ~0.7 s, L2 stays held while moving, releasing selects).
- **Box scores**: CPU-simmed games have a box score and per-team player stats in the schedule.
- Screenshots are 3024×1888 PNG, ~6 MB each. WebP q90 is ~0.21 MB.
- `league_state` holds `current_season`, `current_week` (2035, 18 in the backup). The DB runs in Docker container `madden-db` (`docker exec madden-db psql -U postgres -d madden`).

## OCR test results (Oct 13, in the claude.ai session)

Scripts: `tools/vision_dump.py` (Apple Vision OCR of a folder of screenshots, writes `vision_ocr.json`) and `tools/vision_cells.py` (per-cell experiment). Both are read-only.

- **Stats tables, Apple Vision vs Gemini CSVs** (197 phase 1 screenshots, ~14,000 cells): whole-page OCR agreed on 81% of cells, but left **18% blank**; 95% of blanks are 1–2 character values (QB, 0, 7, 38). Reading each cell separately was worse (27% blank). Where Vision did read a value it was ~99.5% right. Tesseract misread the condensed font (4,769→4163). **Decision: tables stay on Gemini.** Gemini is not perfect either (one confirmed misread: 1651 read as 1051), so loader checks stay mandatory.
- **Roster cards, Apple Vision** (1,626 video frames of roster screens, 1080p JPEG): on settled frames, name, number, position, dev trait, age, weight and morale were read ~100% and agree with the DB 98–99%. Known problems:
  1. **Unsettled cards**: ~25% of frames caught the card mid-animation (new name, previous player's numbers). After each Down press, read the card until two reads a moment apart agree **and** the card's position and age match the highlighted list row.
  2. **6'1" is misread as 6'7"** (apostrophe + 1). Treat 6'7"+ on a player under ~280 lbs as suspect and re-read.
  3. **EXP missing 14%** (single short values). Re-read that box, or send that one card to Gemini. The card's OVR badge and MORALE are not needed (see Decisions), so skip them.
  4. The list OVR was read in only 89% of rows by Vision, which is why list pages go to Gemini.
- Never identify a player by name alone: names repeat (two Eric Castros, FB and RG). Use team + full name + number + position.

## Target weekly flow

1. **Detect**: `pipeline/main.py` reads the stream at 1 FPS; `TemplateClassifier` matches only `ADVANCE_WEEK` and `MAIN_MENU`. Advance Week arms; the next main menu fires once. When the broadcast is offline, keep retrying the stream URL and resume.
2. **Connect**: `pipeline/weekly_capture.py` runs the chiaki `stream` command, which lands straight on the franchise page (the PlayStation menu only shows on a manual connect; JR, Oct 14), then waits up to 2 minutes for `madden_capture.py --test` to see a franchise menu. Otherwise: notice, press nothing else.
3. **Capture**: Season Stats (NFL, every category), Team Stats (3 tabs) and the previous week's League Schedule. (`--only games` for a week's box scores + player stats is run by hand only when a week was skipped; JR, Oct 14.)
4. **Hand back**: notice to JR's phone; the script does not disconnect.
5. **Extract**: `extract.py` → CSVs.
6. **Validate and load**: `pipeline/load_capture.py`, one transaction. Any failed check: nothing written, the week stops, notice sent. Re-running by hand resumes.
7. **Backfill and engines**: `generate_team_and_player_stats(season)` with the season from `league_state`; it only fills games the capture missed.
8. **Articles**: `newsroom/weekly_run` (already skips published weeks).
9. **Shrink** the capture folder (WebP, delete debug folders) after a successful load.

## Capture modes to add to `madden_capture.py`

| Mode | When | What | Writes |
| --- | --- | --- | --- |
| `--only games [--week "SEASON WEEK 9"]` | Only by hand, to recover a skipped week (JR, Oct 14; ~7 min a game) | Per game: League Schedule → highlight the game → Cross; box score screenshot at the top and at the bottom; R1 → player stats, captured like Season Stats (every category, every page); both teams are shown by default | `games` scores, `team_stats`, `player_stats` |
| `--only roster` | Start of season, after trades/signings, when a load reports unmatched players; draft classes | Manage Roster → Roster tile; per team via the L2 dropdown: press down one row at a time and screenshot each row once the card has settled (card read on the Mac: full name, number, archetype, dev trait, age, height, weight, experience); also one screenshot per list page for Gemini (POS, AGE, OVR, DEV) (~1,700 players, ~40–45 min) | `players`, `player_seasons` |
| `--only season-schedule` | Start of season | League Schedule weeks 1–18 via the week dropdown (~50 screenshots) | `games` as SCHEDULED (272 games) |

Keep the existing safety rules: check the screen before every press, stop on anything unexpected, never press Cross on Advance Week or Force Win.

## Phase 3 in detail (agreed with JR, Oct 14)

### What existed before phase 3 (checked Oct 14; see "Current state" for now)
- `season_stats` holds offense only (passing, rushing, receiving, fumbles), unique on (season, player_name, team_id, position); it has a `player_id` column that isn't part of the key. 359 rows for 2035.
- `player_stats` (per game) is the same offense set, unique on (game_id, player_name, position), no `player_id`; `fantasy_points` is generated.
- The backfill (`engines/game_stat_generator.py`) fills every game that has **no `player_stats` rows for a team** with each player's season total minus what is already stored, inserting with `ON CONFLICT (game_id, player_name, position) DO NOTHING`. So a weekly load only has to write season totals and scores; the backfill fills the new week.
- `players`: 1,499 rows, `player_number` is 0 for all of them; the roster capture has 1,719 players (~220 new, mostly linemen).
- `games`: 256 COMPLETED, 16 SCHEDULED for 2035; `league_state` 2035 / 18.

### Storage layout (hybrid)
- **Offense extras become columns** on `season_stats` and `player_stats` (the captured passing / rushing / receiving columns not stored today: rating, long, sacks, YPA, AVG, broken tackles, YAC, RAC, ...). These rows already exist, so nothing changes for the backfill.
- **Every other category gets its own tables**, at season and game level: `season_stat_defense` / `game_stat_defense`, `_kicking`, `_punting`, `_kickreturn`, `_puntreturn`, `_blocking`, `_general` (GP / DP). Columns are named after the captured headers. Rows exist only where Madden lists the player; NULL means not listed, 0 means listed as 0.
- **Views** `season_stat_lines` and `game_stat_lines` join everything into one line per player (season or game).
- **Team tabs** (conversions, red zone, penalties) become nullable columns on `team_seasons`; the box score's team stats go on `team_stats` (new nullable columns where the box score has more than the table).
- **Roster**: `player_seasons.overall_rating` takes the **list OVR**; new nullable `dev_trait`, `archetype`, `role` on `player_seasons`; `players` gets full name, height, weight and `player_number` from the cards. The card's base OVR and morale are not stored.
- Nothing reads the new columns or tables yet (decision above); every existing reader keeps working on the columns it uses today.

### Keys: `player_id` everywhere
- Every new table is keyed on `player_id` (+ season or game_id).
- `player_stats` gets a `player_id` column, filled for existing rows from name + team + position (unambiguous there, since those rows carry `team_id`). New unique keys (season, player_id) on `season_stats` and (game_id, player_id) on `player_stats`.
- `player_name` stays a plain column, because the newsroom tools and the API join on it. The old unique keys stay too, because the backfill's `ON CONFLICT` uses them; a trigger fills `player_id` on rows the backfill inserts (from the matching `season_stats` row), so `game_stat_generator` is not changed.
- The key keeps stored data unambiguous; working out *who* a captured row is still happens once, at load time:
  - **Roster load** is the identity source: team + full name + number + position + list name ("J.Delgado") per `players.id`.
  - **Game player stats** carry TEAM, so team + list name is nearly always unique; a same-team pair (Seahawks S.Lewis LT / HB) is settled by category (a Rushing row is the HB, Blocking the LT).
  - **Season Stats** has name + position but **no team** (15 of 1,214 phase 1 rows were ambiguous across teams, e.g. two J.Delgado kickers). From the second load on, previous totals settle it (totals only go up; assign rows to the ids whose earlier totals fit, checked against GP). **If history can't settle a name, the load stops and lists them (JR, Oct 14)**: nothing is written and the notice says which names and categories need a team-scoped Season Stats capture (`madden_capture.py --only stats --scope <TEAM> --category <CAT>`, to add in this phase; the scope dropdown already lists every team). That capture is loaded first, its ids are stored, and the week load runs again.

### `captures` and `llm_usage` tables
- `captures`: one row per run: mode, season, week, folder, status (`captured` / `extracted` / `loaded` / `failed`), row counts per CSV, Gemini token totals, error, timestamps. Used for the duplicate check, the missed-week check, resuming and the row-count check.
- `llm_usage`: one row per Gemini call: source (`extract` / `newsroom`), agent or file, model, input / cached / output / thinking tokens, time. Written by `extract.py` and `newsroom/swarm_dispatcher.py` `_execute_agent`; the `captures` row keeps the run's total.

### `pipeline/load_capture.py <capture folder>`
- One transaction per run; any failed check writes nothing, marks the `captures` row `failed` and (phase 5) sends the notice. A run already loaded is refused. `fantasy_points` is never written.
- **Weekly run** (Season Stats + Team Stats + previous week's schedule): upsert `season_stats` (offense + extras) and the `season_stat_*` tables; set that week's game scores and status COMPLETED; team tabs to `team_seasons`.
- **Roster run**: update `player_seasons` (team, list OVR, age, experience, position, dev, archetype, role) and `players` (full name, height, weight, number); insert new players. On a full 32-team capture, players it didn't find are deleted when they hold no real data (stats, awards, article tags, agent memories), else kept on their team and listed (JR, Oct 14).
- **Season schedule**: insert SCHEDULED games only; COMPLETED games are never touched.
- **Games run (recovery only)**: real lines into `player_stats`, `game_stat_*` and `team_stats`, plus scores. Refused if those games already have backfilled rows, unless `--replace` (which deletes that week's backfilled rows in the same transaction).
- **Checks before writing**: passing / rushing / receiving rows 100% matched (other categories list unmatched rows); row counts within 10% of the last capture of that mode; no season total goes down; every value parses; the schedule covers all 32 teams minus byes; the week is the last loaded week + 1 (a gap loads but is flagged); a games run has a box score and both teams' player stats for every game, and its scores match the schedule (`QUARTERS_OK = n` games load totals only).

### Order of work (a commit after each)
1. `./db/backup_db.sh`; restore it into `madden_p3test` in the `madden-db` container; the loader and tests use `DATABASE_URL` pointing there until the live load. (Dropped after the live run.)
2. Migration `db/migrations/2026_10_23_capture_loader.sql` + `db/init_db.sql`: new columns, category tables, views, `captures`, `llm_usage` (no keys). The `player_id` keys and trigger come in `2026_10_24_player_id_keys.sql`, **after the roster load** (JR, Oct 14): existing rows had two different M. Santos on one id, 7 `season_stats` rows linked to the wrong player and 364 `player_stats` rows (25 names) that don't resolve to one player, mostly because ~220 players were missing from `player_seasons`. That migration re-links `player_id` from the refreshed roster, lists anything still unresolved, then adds the keys. Built as `db/migrations/2026_10_24_player_id_keys.py` (Python: it needs the name keys). JR's rules (Oct 14): a player's duplicate stats rows are merged (larger value per stat; Brian Thomas Jr. had "B. Thomas Jr" and "B. Thomas Jr." rows), and players a full roster capture didn't find are deleted when they hold no real data, else kept on their team and listed (JR, Oct 14; clearing their team first broke the TWI engine, which assumes every player_seasons row has a team; the 9 found on Oct 14 were the old video scanner's misreads and duplicates and were deleted on live). Rows without a team are matched on name + position across the league when one player fits, and get that team.
3. Matching module (identity from the roster, Season Stats history assignment, stop-and-list for unsettled names) with unit tests.
4. Loader per mode: roster, season schedule, weekly, games.
5. `--scope <TEAM> --category <CAT>` for Season Stats in `madden_capture.py`.
6. Token logging in `extract.py` and `_execute_agent`.
7. Run everything on `madden_p3test`, then live (after another backup). Done Oct 14: both passed exits (a)–(e); see "Current state".

## Phase 5 status (Oct 14)

Built (PROJECT_SPEC.md §6.0): `pipeline/trigger.py` + `python3 -m pipeline.main --watch`; `pipeline/weekly_capture.py` (steps 2–9, stop + notice on any failed step, `--resume`, shrink); `pipeline/notify.py` (ntfy; a test notice was sent Oct 14); `madden_capture.py --connect` / `--out`; `generate_team_and_player_stats(strict=True)` so an engine error stops the week. Unit tests: `tests/test_trigger.py`, `tests/test_weekly_capture.py`.

Checked offline: the MAIN_MENU template matches JR's recorded broadcast hub frames (score 1.0) and the hub label reads `2026: WEEK 2` → (2026, 2); shrink took a copy of `2026-10-13_1413` from 1,696 MB to 46 MB; streamlink resolves the channel (offline at the time).

Still to do, with JR at the console:
1. **Test `--connect`** from chiaki-ng closed (no mapping needed: JR, Oct 14, `chiaki stream` connects straight to the franchise page; the PlayStation menu only shows on a manual connect, so nothing is pressed).
2. **Check the ADVANCE_WEEK template on the live broadcast** (no recorded frame of that screen exists yet), and the hub label in the 2035 franchise.
3. **Exit runs**: three weeks in a row from Advance Week, including a bye week and a forced failure (bad CSV) that stops the week, publishes nothing and reaches JR's phone.

## Phases

1. **Prove the capture.** Done.
2. **Capture extensions.** Done (Oct 14). Add the three modes above; on-device OCR for player cards; Escape in the key map. Run each by hand once with JR at the console. *No database writes in this phase*: output is screenshots and CSVs only.
   - Exit: roster covers all 32 teams including linemen; season schedule has 272 games; one week's game capture has a box score and two teams' player stats for every game.
3. **Schema and loader.** Done (Oct 14, live). See "Phase 3 in detail" above: backup and restored copy, migration (hybrid layout, `player_id` keys, `captures`, `llm_usage`), matching, `pipeline/load_capture.py` for every mode, team-scoped Season Stats capture, token logging. Test on the restored copy, then live.
   - Exit (revised by JR, Oct 14, since games are captured only to recover a week): on the restored copy, (a) the roster loads (1,719 players, numbers filled) and the season schedule loads with no change to completed games; (b) a weekly capture loads, the backfill runs, and every player's `player_stats` summed over the season equals `season_stats`; (c) the Week 17 games capture loads with `--replace` and its lines match the box scores; (d) a deliberately broken CSV, and an ambiguous Season Stats name, each stop the load with nothing written; (e) the backfill afterwards fills no game already loaded. Then the same loads live.
4. **Season from `league_state`.** Done (Oct 14). Remove hardcoded 2035 from the backfill and pipeline calls; update `league_state.current_week` from the captured week label.
   - Exit: tests pass; a dry run picks up the right season and week.
5. **Trigger, connect, notices.** In progress (built; console and exit runs to do). Trigger in `pipeline/main.py`; `pipeline/weekly_capture.py`; ntfy notices; stream-offline retry; fallback on any `MAIN_MENU` comparing the week label with the last capture.
   - Exit: three weeks in a row run end to end from Advance Week with JR's only step being the controller and broadcast restart, including a bye week and a forced failure (bad CSV) that stops the week, publishes nothing and reaches JR's phone.
6. **Retire the stream extraction.** Remove post-game parsing and the worker pool from `pipeline/main.py`; move `pipeline/ocr_extractor.py` and unused templates to `archive/`; stop calling `upsert_player_stats`, `update_game_meta`, `upsert_team_stats`; rewrite `PROJECT_SPEC.md` Section 6. Until this phase, the old post-game flow keeps working.
   - Exit: no imports of retired code; the spec describes only the new flow.

## Cost reference

`gemini-3.8-flash` introductory pricing through Dec 31, 2026: $0.75 / 1M input, $3.75 / 1M output (thinking included), $0.135 / 1M cached. Estimated capture cost ~$0.50–2/week; the weekly article run is an estimated $1.50–2.50 and is unchanged. Token logging (phase 3) replaces these estimates once live weekly runs fill `llm_usage` (empty as of Oct 14).
