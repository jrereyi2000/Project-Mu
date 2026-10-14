# Pipeline migration: handoff for Claude Code

Plan agreed with JR on Oct 13, 2026 in a claude.ai planning session. The full plan also lives in the claude.ai doc "Project Mu Pipeline Migration Plan"; this file is the copy Claude Code works from. If this file and the code disagree, trust the code and say so. If this file seems wrong or underspecified, ask JR before improvising.

## How to work through it

- Do **one phase at a time**, in order. Stop when the phase's exit criteria are met and report back; do not start the next phase unprompted.
- Every phase that touches the database starts with `./db/backup_db.sh`, and is tried against a restored copy before the live database.
- `CLAUDE.md` rules apply: update `PROJECT_SPEC.md` with every change; commit into `main`; leave other sessions' uncommitted changes alone.
- Engines (`engines/*` except the season fix in phase 4), the newsroom and the API are **out of scope**. They must keep reading the same columns they read today.

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
| Capture **every game's box score and both teams' player stats every week** | CPU-simmed games have box scores. This replaces the score-weighted backfill split with real lines; the backfill stays only as a fallback for games the capture missed |
| Season Stats is still captured weekly, as a **cross-check** | Each player's game lines summed over the season must equal his Season Stats total |
| Every stat the capture finds gets a **database column**, ignored by everything for now | JR wants the data kept; consumers come later |
| A failed load **stops the pipeline for that week**: nothing written, no backfill, no engines, no articles | Bad data must not reach articles |
| chiaki-ng is **already open**; the script connects to it. **Exception (JR, Oct 13):** when the stream freezes (no key reaches the console), the script force-quits chiaki-ng and relaunches the stream itself (up to 3 times per run) before stopping | JR's setup. The stream froze several times on Oct 13 over Wi-Fi; JR's fix was always force quit + reopen. A polite quit + relaunch, plus an Escape press, once left the console ignoring input |
| Remote Play **ends the broadcast and disconnects the controller**. JR turns the controller back on (which ends Remote Play) and restarts the broadcast himself | Expected, accepted |
| Notices (capture started, done, week stopped) go to JR's **phone via ntfy** | JR's choice |
| **Stats tables stay on Gemini** (`extract.py`). Roster: the **list pages go to Gemini**, the **player card is read with Apple Vision on the Mac**, except **AGE/HT/WT/EXP, which Gemini reads from the card crops** (20 per request) | Tested Oct 13 (see "OCR test results"): Apple Vision skips short values (1–2 characters) in tables, so it can't read tables alone; it reads card text well. On console screenshots it drops or misreads the digit 1 and misreads heights silently (Hahn 6'3" read as 6'8"), so JR moved those four values to Gemini (Oct 13, phase 2) |
| `player_seasons.overall_rating` = the roster **list OVR** (morale-boosted, how players perform). The card's base OVR and morale are **not stored** | JR's rule. The card OVR is the base rating; the list OVR includes the morale boost and is how players play. A once-a-season scan would make base OVR and morale stale |
| After a successful load, screenshots convert to **WebP quality 90, full resolution**; debug folders are deleted; CSVs kept. Failed runs keep their PNGs | Phase 1 folder was 1.3 GB/week; this brings it to ~45 MB |
| A `captures` table records every run | Duplicate protection, missed-week check, fallback trigger, row-count checks, resuming, token cost |

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
- **chiaki-ng**: the executable is `/Applications/chiaki-ng.app/Contents/MacOS/chiaki` (not `chiaki-ng`; `--help` prints nothing). JR confirmed `.../chiaki stream "PS5-263" 192.168.1.181` connects to the console; after connecting, **one Escape press closes the PlayStation menu**, and nothing else needs pressing. Other built-in options: `list`, `--passcode`, `--profile`, `--exit-app-on-stream-exit` (do not use; the app stays open), `--fullscreen/--zoom/--stretch`. Put the nickname and IP in `.env`.
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
2. **Connect**: `pipeline/weekly_capture.py` runs the chiaki `stream` command, presses Escape once the PlayStation menu is recognized by OCR (retry once) to close it, then waits up to 2 minutes for `madden_capture.py --test` to see a franchise menu. Otherwise: notice, press nothing else.
3. **Capture**: Season Stats (NFL, every category), Team Stats (3 tabs), the previous week's League Schedule, and every game of that week (box score + both teams' player stats).
4. **Hand back**: notice to JR's phone; the script does not disconnect.
5. **Extract**: `extract.py` → CSVs.
6. **Validate and load**: `pipeline/load_capture.py`, one transaction. Any failed check: nothing written, the week stops, notice sent. Re-running by hand resumes.
7. **Backfill and engines**: `generate_team_and_player_stats(season)` with the season from `league_state`; it only fills games the capture missed.
8. **Articles**: `newsroom/weekly_run` (already skips published weeks).
9. **Shrink** the capture folder (WebP, delete debug folders) after a successful load.

## Capture modes to add to `madden_capture.py`

| Mode | When | What | Writes |
| --- | --- | --- | --- |
| `--only games [--week "SEASON WEEK 9"]` | Every week (default previous week); by hand to recover a skipped week | Per game: League Schedule → highlight the game → Cross; box score screenshot at the top and at the bottom; R1 → player stats, captured like Season Stats (every category, every page); both teams are shown by default | `games` scores, `team_stats`, `player_stats` |
| `--only roster` | Start of season, after trades/signings, when a load reports unmatched players; draft classes | Manage Roster → Roster tile; per team via the L2 dropdown: press down one row at a time and screenshot each row once the card has settled (card read on the Mac: full name, number, archetype, dev trait, age, height, weight, experience); also one screenshot per list page for Gemini (POS, AGE, OVR, DEV) (~1,700 players, ~40–45 min) | `players`, `player_seasons` |
| `--only season-schedule` | Start of season | League Schedule weeks 1–18 via the week dropdown (~50 screenshots) | `games` as SCHEDULED (272 games) |

Keep the existing safety rules: check the screen before every press, stop on anything unexpected, never press Cross on Advance Week or Force Win.

## Loader rules (`pipeline/load_capture.py`)

- Column names for new stats come **from the captured headers above**, prefixed by category for non-offense (`blk_`, `def_`, `kick_`, `punt_`, `kr_`, `pr_`). New columns are nullable. Team tabs go on `team_seasons`. Do not invent columns that aren't on a screen.
- Matching: resolve through `player_seasons` on normalized name + position (after the roster capture, also full names). Settle duplicates by the candidate whose previous totals are closest to, and not above, the new ones, checked against GP. Box-score rows carry their team.
- Passing, rushing and receiving must match **100%**; other categories load with unmatched rows listed in the report.
- Checks before writing: row counts within 10% of last week; no season total goes down; every value parses; the schedule covers all 32 teams minus byes; every game has a box score and two teams' player stats; game lines summed over the season equal Season Stats totals; the week is exactly one later than the last capture (a gap loads but is flagged); a week already loaded is refused.
- `fantasy_points` on `player_stats` is a generated column: never insert or update it.
- Roster: `player_seasons.overall_rating` takes the **list OVR**; add nullable columns for dev trait, archetype and experience. Do not store the card's base OVR or morale. Heights stored as `6'1"` like today.

## Phases

1. **Prove the capture.** Done.
2. **Capture extensions.** Add the three modes above; on-device OCR for player cards; Escape in the key map. Run each by hand once with JR at the console. *No database writes in this phase*: output is screenshots and CSVs only.
   - Exit: roster covers all 32 teams including linemen; season schedule has 272 games; one week's game capture has a box score and two teams' player stats for every game.
3. **Schema and loader.** Backup first. Migration in `db/migrations/` plus `db/init_db.sql`: new `season_stats` and `team_seasons` columns, `captures` table. Build `pipeline/load_capture.py` for all modes. Token logging in `extract.py` and `newsroom/swarm_dispatcher.py` `_execute_agent` (input, cached, output, thinking tokens). Test on a restored copy, then live.
   - Exit: one week's loaded `player_stats` sum to Season Stats totals for every player; a deliberately broken CSV is refused with nothing written; the backfill afterwards fills no games for that week.
4. **Season from `league_state`.** Remove hardcoded 2035 from the backfill and pipeline calls; update `league_state.current_week` from the captured week label.
   - Exit: tests pass; a dry run picks up the right season and week.
5. **Trigger, connect, notices.** Trigger in `pipeline/main.py`; `pipeline/weekly_capture.py`; ntfy notices; stream-offline retry; fallback on any `MAIN_MENU` comparing the week label with the last capture.
   - Exit: three weeks in a row run end to end from Advance Week with JR's only step being the controller and broadcast restart, including a bye week and a forced failure (bad CSV) that stops the week, publishes nothing and reaches JR's phone.
6. **Retire the stream extraction.** Remove post-game parsing and the worker pool from `pipeline/main.py`; move `pipeline/ocr_extractor.py` and unused templates to `archive/`; stop calling `upsert_player_stats`, `update_game_meta`, `upsert_team_stats`; rewrite `PROJECT_SPEC.md` Section 6. Until this phase, the old post-game flow keeps working.
   - Exit: no imports of retired code; the spec describes only the new flow.

## Cost reference

`gemini-3.8-flash` introductory pricing through Dec 31, 2026: $0.75 / 1M input, $3.75 / 1M output (thinking included), $0.135 / 1M cached. Estimated capture cost ~$0.50–2/week; the weekly article run is an estimated $1.50–2.50 and is unchanged. Token logging (phase 3) replaces these estimates.
