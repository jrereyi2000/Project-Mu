# Record Book & Awards Page: Implementation Plan

**Status (2026-10-26):** Phases 1 (Record Book, `#records`), 2 (Awards & Champions, `#awards`) 3 (player Honors row, Team hub Records & Titles panel on the Stats tab) and 4 (Record Watch on `#records`) built; phase 5 has no source yet (D5). Nothing here changes the database schema. Phase 1 adds a Rookie scope chip (the `rookie_*` categories) and ranks categories without an NFL list from their franchise records; phase 2 applies the `#awards` team and award filters on the page rather than as `/api/awards` parameters (PROJECT_SPEC §4.7); phase 4's watch rows list every tied leader (`leaders`) and round the 17-game pace to a whole number, with no pace for longest field goal.
**Scope:** A public **Record Book** (`#records`) and **Awards & Champions** (`#awards`) page, plus two small hooks: award badges on player pages and a Records panel on the Team hub.
**Reference specs:** `PROJECT_SPEC.md` §2.9 (Record Book), §2.11 (Coach of the Year), §3.3a (coaches), §7.6b–§7.6d (stored All-Pro, award winners, Super Bowl MVP).

---

## 0. Why

The league already keeps a full record book and ten seasons of awards, and the writers cite both. Only the writers can see them, though. Spec §2.9 still says "Not on a screen yet", and no API endpoint serves `record_categories`, `franchise_records`, `league_record_entries` or `awards`. The home page's "Historic NFL Record Breaking" spotlight is built from articles, not from these tables.

---

## 1. Survey: what exists (live database, 2026-10-26)

| Data | Where | What's there |
|---|---|---|
| Categories | `record_categories` (`category, label, scope, subject, better, sort_order, coverage, season_type`) | 80 categories. **Regular season:** 12 player-season and 3 team-season (`FULL`), 9 player-season and 11 player-game (`BOOK`, defense and kicking), 6 player-game (`FULL`). **Postseason:** 17 game, 8 single postseason, 14 career (player, coach and team). |
| All-time top-10 lists | `league_record_entries` | 690 rows. `NFL` 600 (1929–2025, Pro-Football-Reference), `BOOK` 76 (2027–2035, the game's record book), `LEAGUE` 14 (2035–2036, written by `RecordsEngine`). |
| Current NFL record | view `league_records` | One row per holder: ties give several rows (`game_pass_tds` has 10). Also folds in the best franchise record for categories without NFL lists. |
| Franchise records | `franchise_records` and view `franchise_records_current` | 699 rows (`NFL` 544, `BOOK` 119, `LEAGUE` 36). The current view has 605 rows over 534 (team, category) pairs, again because of ties. Regular season only; there are no franchise postseason records. |
| Ranking logic | `engines/records_engine.py` | `_rank` (ties share a rank), `_better` (handles `better = LOW`), `tidy_name`, `ordinal`. `load_book` is the baseline loader. |
| Writer tool | `newsroom/db_tools.get_record_book` | Fuzzy category lookup, holders, top 10 and franchise records. It returns prose JSON, so it isn't reusable as an API. |
| Awards | `awards` (`season, award_type, player_id, player_name, team_id, position, coach_id, coach_name`) | MVP, OPOY, DPOY, OROY, DROY, COTY and SBMVP for 2026–2035 (10 each). First- and Second-Team All-Pro for **2035 only** (31 + 31). 12 non-COTY rows have no `player_id`, so they can show a name but not link. |
| Champions | `games` with `game_type = 'SUPER_BOWL'` | All ten Super Bowls, 2026–2035, with scores. 2026–2033 have **only** the Super Bowl game on record. |
| Season history | `team_seasons` | Full 32-team standings only for 2035–2036 (18 teams in 2034, 2–3 before that). |
| Coach careers | `coach_seasons` (2025 and 2034 snapshots), view `coach_records`, `engines/coach_profile.coach_profile` | Career and per-team records are only reliable from the snapshot on (coach profile returns null records when a tenure isn't fully recorded). |
| League state | `league_state` | 2036, Week 18. `RecordsEngine` writes current-season `LEAGUE` rows **during** the season (e.g. Crawford's 66 passing TDs in 2036), so the current season's marks are not final yet. |

**Implications for the design**
- Ties are normal, so every view must show multiple holders.
- Every mark has a source. NFL history (`NFL`), the game's record book for 2026–2035 (`BOOK`) and Mu's own seasons (`LEAGUE`) should be told apart. "Set in this league" (`BOOK` or `LEAGUE`, season ≥ 2026) is the story the page is for.
- The current season's `LEAGUE` rows must read "in progress" until the regular season is complete (§2, D3).
- All-Pro history before 2035 isn't stored. The page shows what's there and doesn't pretend otherwise (§2, D5).

---

## 2. Decisions

Defaults are marked. Items marked **JR** need your call before the phase that uses them.

| # | Decision | Default |
|---|---|---|
| D1 | Where the pages live | Two routes, `#records` and `#awards`, linked from the **Stats** dropdown (`StatsDropdown`) under a new "History" group, not as new top-level nav links. The phone nav already uses its full width (six links). |
| D2 | Public or operator-only | **Public.** Records and awards are facts the newsroom already publishes; nothing here is hidden scouting or GM-queue data. |
| D3 | Current-season marks | Shown with an "In progress · through Week N" tag while `league_state.current_season` has unplayed regular-season games. After Week 18 they read as final. |
| D4 | Mu marks vs. NFL history | A "Set in this league" badge on any entry with `source IN ('BOOK','LEAGUE')`. A **Mu Era** filter (2026+) on every list, defaulting to *All-time*. |
| D5 | All-Pro before 2035 (**JR**) | Show 2035 only, with a note. *Option:* backfill 2026–2034 from the game's All-Pro history. `AP All Pro.pdf` (since deleted) turned out to be the 2035 team (already stored), so there is no source for 2026–2034. Generated First and Second Teams from the history backfill's season lines are an option there (`docs/HISTORY_BACKFILL_PLAN.md` §5), with JR's go-ahead (phase 5). |
| D6 | Coach history | COTY winners and Super Bowl-winning coaches only (from `awards` and `coach_tenures`). No coach career leaderboard until `coach_seasons` covers more seasons. |
| D7 | Records on the Team hub | A small **Records** panel inside the existing Main or Stats tab, not a sixth tab. It links to `#records?team=ABBR`. |
| D8 | Writer tool | Leave `get_record_book` alone. The new API shares the engine helpers, not the tool. |

---

## 3. Design

### 3.1 Record Book (`#records`)
- **Header:** "Record Book", with a count of the NFL records set or tied in this league since 2026 (Mu entries that hold rank 1 in `league_records`).
- **Controls:** a segmented control for Regular season / Postseason, scope chips (Single season, Single game, Career [postseason only], Super Bowl), a subject filter (Player, Team, Coach), an *All-time / Mu Era* toggle, and a team picker (`?team=CAR` filters to franchise records).
- **Category cards** (grid; one per category in `sort_order`):
  - The label ("Passing TDs, single season"), the record value, and the holder(s) with team logo, season and source badge.
  - A "Top 10" expander showing ranked entries (shared ranks for ties). Mu entries are highlighted. The current season's in-progress entries carry the D3 tag.
  - The previous record holder when a Mu entry broke an NFL record (the best `NFL` entry below it).
- **Franchise view** (`?team=ABBR`): the team's record in every regular-season category, holder, season and source, plus whether it is also the NFL record.
- **Record Watch** (phase 4; only while a regular season is in progress): current-season leaders in each `FULL` single-season player category, with their value, the record, the percentage of the record reached and the pace over 17 games. This uses the same ranking rules as `RecordsEngine.evaluate`.

### 3.2 Awards & Champions (`#awards`)
- **Champions strip:** one card per season, newest first. It shows the Super Bowl winner and score (from `games`), the Super Bowl MVP and the champion's head coach (`coach_tenures` that season).
- **Award history table:** rows are seasons, columns are MVP, OPOY, DPOY, OROY, DROY, COTY and SB MVP. Each cell has the player (linked when `player_id` is set), position and team logo.
- **Leaders panel:** multiple winners (e.g. "Caleb Williams · 2× MVP") and the franchise count of awards and titles.
- **All-Pro teams:** a season picker (only seasons with rows) showing First and Second Team grouped Offense / Defense / Special Teams in slot order. Seasons without rows show "All-Pro teams are recorded from 2035."
- **Filters:** by team (`?team=ABBR`) and by award.

### 3.3 Hooks into existing pages
- **Player page** (`#player-{id}`): an Honors row with chips ("2× MVP", "2035 First Team All-Pro (QB)", "SB MVP 2034"). `get_player` in `server/api.py` gains an `awards` list; the frontend renders chips when it's non-empty.
- **Team hub:** a Records panel in the Main or Stats tab (D7) listing the franchise's records, Mu-era ones first, and titles won since 2026, plus a "Full record book →" link.

### 3.4 Visual language
- The same paper / ink / team-colour system as the Team hub (`.team-section-title`, `.stats-grid` tiles, `.league-stats-table` / `.fo-table` patterns).
- New CSS goes in `src/components/history/history.css`, following `components/team/frontOffice.css`.
- Phone: the cards stack, tables scroll sideways inside their own container, and the page never scrolls sideways (the rule used for the Front Office tab).

---

## 4. Backend: `server/history_api.py` (read-only router, included in `server/api.py`)

Every endpoint is read-only and public. Numbers come back raw (the frontend formats), and every row carries `source` and `season`. Missing tables (migration not applied) give empty blocks, not errors.

| Endpoint | Returns |
|---|---|
| `GET /api/records/categories` | `[{category, label, scope, subject, better, season_type, coverage, sort_order}]` |
| `GET /api/records?season_type=REGULAR&scope=&subject=&era=all\|mu` | `{as_of {season, week, in_progress}, mu_marks (count), categories: [{category, label, …, record: [holders], top10: [{rank, value, holder, player_id, team, season, source, mu, in_progress}], previous_nfl_record}]}` |
| `GET /api/records/category/{category}` | One category in full: top 10, every franchise's record for it (regular season), and Mu entries outside the top 10. |
| `GET /api/records/franchise/{abbr}` | The team's record per regular-season category (ties included), whether it's also the NFL record, and Mu-era flags. Accepts the `ARI`/`WSH` aliases, like the Front Office endpoint. |
| `GET /api/records/watch?season=` (phase 4) | Current-season leaders vs. the record per `FULL` single-season player category: `{leader, value, record, pct, pace_17}`. Empty once the regular season is complete. |
| `GET /api/awards?team=&award=` | `{seasons: [{season, champion {team, score, opponent, opponent_score}, sb_mvp, champion_coach, awards {MVP, OPOY, …}}], leaders, franchise_counts}` |
| `GET /api/awards/all-pro?season=` | `{seasons_available, season, first_team [{slot, player…}], second_team […]}` in slot order. |
| `get_player` (existing, `/api/player/{id}`) | Adds `awards: [{season, award_type, position, team}]`. |

**Implementation notes**
- Ranking reuses `records_engine._rank` / `_better` (ties share ranks; `better = 'LOW'` for fewest points allowed). Expose them as public helpers (`rank`, `better`) rather than importing private names. Nothing else in the engine changes.
- Holder names go through `tidy_name`. A `player_id` gives a `#player-` link; `NFL` holders (real players) don't link.
- "In progress" (D3): `season == league_state.current_season` and some regular-season game for that season isn't `COMPLETED`.
- "Previous NFL record": the best `NFL` entry for the category, shown when a Mu entry outranks it.
- Champion coach: the `coach_tenures` row covering that season for the winning team.
- Phase 5 data only: the All-Pro backfill writes `awards` rows through the existing `newsroom/all_pro.store_all_pro_teams` path, not by hand.

---

## 5. Frontend: `Mu_Network/src/components/history/`

| File | Role |
|---|---|
| `RecordBookPage.tsx` | `#records` route: controls, category grid, query-string state (`?type=&scope=&team=&era=`). |
| `RecordCard.tsx` | One category: record, holders, Top 10 expander, badges. |
| `FranchiseRecords.tsx` | `?team=` view; also reused (compact) by the Team hub panel. |
| `RecordWatch.tsx` | Phase 4 in-season chase panel. |
| `AwardsPage.tsx` | `#awards` route: champions strip, award history table, leaders. |
| `AllProTeams.tsx` | All-Pro season picker and grids. |
| `format.ts` | Value formatting per category (yards, TDs, half sacks, FG length). Reuse `components/team/format.ts` `teamLogo` / `ordinal`. |
| `history.css` | Styles (§3.4). |

`App.tsx` changes stay small: two routes, a "History" group in `StatsDropdown`, the player-page Honors row and the Team hub Records panel import. No new code goes into the 5,900-line file beyond wiring.

---

## 6. Phases (each one commit to `main`, spec updated in the same commit)

| Phase | Delivers | Done when |
|---|---|---|
| **1. Record Book API + page** | `history_api.py` records endpoints (categories, list, category, franchise); `#records` with regular-season and postseason cards, Top 10, Mu badges, in-progress tags, team filter; Stats dropdown entry. | All 80 categories render; ties show every holder (single-game passing TDs: 10 holders at 7, Crawford 2035 and Darnold 2029 badged "Set in this league"); `?team=CAR` lists Carolina's records; phone width has no sideways scroll. |
| **2. Awards & Champions** | `/api/awards`, `/api/awards/all-pro`; `#awards` with champions strip, award table, leaders, All-Pro 2035. | Ten champions with scores, SB MVPs and coaches; every award row present (unlinked names shown plain); All-Pro shows 62 selections in slot order and earlier seasons show the D5 note. |
| **3. Hooks** | `awards` on `/api/player/{id}` and the Honors row; Team hub Records panel. | Crawford's page shows 2× MVP, 2035 First Team All-Pro (QB) and 2035 SB MVP; the Panthers hub lists their Mu-era records and titles. |
| **4. Record Watch** | `/api/records/watch` and the panel on `#records` (in season only). | During a regular season it lists each leader's percentage of the record and 17-game pace; it disappears after Week 18. |
| **5. All-Pro backfill (optional, JR)** | No source yet: `AP All Pro.pdf` (since deleted) was the site's own 2035 All-Pro article (#article-224), whose 62 picks match the stored 2035 rows. Needs a 2026–2034 source JR names, or generated teams from the history backfill (D5); either is stored through the existing All-Pro storage path. | JR approves the source and the parsed rows before anything is written. |

---

## 7. Tests (`tests/test_history_api.py`)

- **Pure:** ranking with ties and `better = LOW`; the in-progress rule; previous-NFL-record selection; All-Pro slot ordering; award-leader counting; value formatting.
- **Live, read-only:** skipped when Postgres is down, the same pattern as `tests/test_front_office_api.py`. Every category returns; `league_records` ties all appear; franchise view for `CAR` matches `franchise_records_current`; ten champions; no endpoint writes.
- No test writes to `madden`. Run this file only, not the whole `tests/` folder (the Napper tests write to the live DB).

---

## 8. Spec updates (per phase)

- §1.1 Repository Layout: `server/history_api.py`, `Mu_Network/src/components/history/`, this plan.
- §2.9: replace "Not on a screen yet" with a pointer to the page.
- §4: a new "History: records and awards" endpoint section.
- §5.1 and §5.2: the `#records` / `#awards` routes, Stats dropdown entry, player Honors row, Team hub Records panel.
- §7.6b–§7.6d: note that stored awards now also appear on `#awards` and player pages.

---

## 9. Risks

- **Mid-season `LEAGUE` rows** can be overtaken or revised by later weeks. D3's tag covers display; nothing here relies on them being final.
- **Ties inflate lists:** `game_pass_tds` alone has 10 record holders. Cards show the first 3 holders plus "+N tied", and the Top 10 expander shows all.
- **Unlinked award rows** (12): shown by name only. Linking them is a separate data fix.
- **Sparse pre-2034 history:** standings and coach careers before 2034 are thin, which is why this plan leaves out season-by-season standings and coach career leaderboards (D6).
- **Shared checkout:** other sessions edit `PROJECT_SPEC.md`, `App.tsx` and `server/api.py`. Stage only this work's hunks.
