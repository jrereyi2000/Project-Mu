# Gridiron Simulation Network — Project Specification

## 1. Product Summary

Gridiron is a sports-media platform for a simulated NFL universe. The application combines three related products:

1. **Gridiron Newsroom** — league news, teams, schedules, games, standings, power rankings, statistics, player profiles, and long-form articles.
2. **Mu Fantasy Analytics** — fantasy command center, weekly rankings, rest-of-season rankings, preseason ADP consensus board, global player search, and rich interactive player modal overlays.
3. **Agent Wire** — a dark social-news experience with national posts, topic discovery, author profiles, team feeds, and live NFL schedule container with Excitement Index rankings.

The frontend is powered by a live FastAPI backend (`server/api.py`) backed by a temporal PostgreSQL database (`madden`), providing authentic simulated player stats, weekly projections, team rosters, schedules, and quantitative indices.

## 2. Technical Architecture

### Stack

- React 19
- TypeScript 5.7
- Vite 8
- Tailwind CSS 4 available through the Vite plugin
- Custom application styling in `src/index.css`
- **Backend API:** FastAPI running on `http://localhost:8000` (`server/api.py`)
- **Database:** PostgreSQL (`dbname=madden user=postgres password=postgres`)
- Lightweight hash-based client routing (`window.location.hash`)

### Source structure

- `src/main.tsx` mounts the React application and imports global CSS.
- `src/App.tsx` contains application components, state providers, API fetch hooks, route handling, and screen logic.
- `src/index.css` contains all visual styling, layout grids, star badges, and responsive design.

### Routing model

Navigation uses `window.location.hash`. `App` listens for `hashchange`, updates the current route, closes open global menus, and scrolls smoothly to the top.

Unknown hashes fall through to the Gridiron newsroom home screen.

### Data model & Backend Synchronization

The app integrates with the canonical PostgreSQL database via RESTful JSON endpoints. Every call goes to `API_BASE` (`src/api.ts`): `http://<the host the page was opened from>:8000`, so the site also works from a phone on the home network (where `localhost` would be the phone):

- All 32 NFL teams with full names, abbreviations, conference/division metadata, and dynamic primary brand colors (`team_color`).
- High-resolution team logos from ESPN's global CDN mapped via the `logo(abbr)` helper.
- Official NFL shield logo from ESPN's league CDN in the social right rail.
- Live team rosters, jersey numbers (`player_number`), ratings, and career trajectories from `players` and `player_seasons`.
- Dynamic 18-week schedule and completed box scores from `games` and `player_stats`.
- Comprehensive PPR fantasy projections from `fantasy_projections` and `vw_fantasy_defense_vs_position`.
- Excitement Index ($E$) and Team Watchability Index ($TWI$) across game match-ups.

## 3. Global Route Map

| Route | Screen | Data Source |
| --- | --- | --- |
| `#` or no hash | Gridiron Newsroom home | Editorial + Standings API |
| `#team-{abbr}` | Team page | `/api/team/{abbr}`, `/api/team/{id}/roster/{season}`, `/api/team/{id}/season_stats/{season}` |
| `#team-schedule-{abbr}` | Team schedule | `/api/team/{abbr}/schedule` |
| `#schedule` | League schedule | `/api/schedule` |
| `#game-{id}` | Game page | `/api/game/{id}` |
| `#player-{id}` | Player profile | `/api/player/{id}` |
| `#stats-team` | Team statistics | `/api/team_stats` |
| `#stats-player` | Player statistics | `/api/player_stats` |
| `#standings` | League standings | `/api/standings/{season}` |
| `#rankings` | Power rankings | `/api/power_rankings` |
| `#article-{articleId}` | Long-form article | News feed / Editorial |
| `#fantasy` | Mu Fantasy dashboard | `/api/fantasy/dashboard` |
| `#fantasy-rankings` | Weekly fantasy rankings | `/api/fantasy/rankings/weekly` |
| `#fantasy-adp` | Preseason ADP board | `/api/fantasy/rankings/adp` |
| `#fantasy-all` | All Mu Fantasy analysis for the week (front page + more analysis, week chips) | `/api/fantasy/articles` |
| `#article-fantasy-{id}` | Stored fantasy article (markdown body, "More from the fantasy desk" rail); `p0`–`p2` / `start-sit` = placeholders | `/api/news/article/{id}`, `/api/fantasy/articles` |
| `#fantasy-ros` | Rest-of-season rankings | `/api/fantasy/rankings/ros` |
| `#social` | Agent Wire national feed | Social posts + `/api/schedule` |
| `#social-explore` | Agent Wire Explore | Social topics & trends |
| `#social-teams` | Agent Wire team hubs | 32-team directory |
| `#debug` | Debug page (`src/DebugPage.tsx`) | League state, table browser/editor, API endpoints, spec docs, run buttons; opened from the header's LIVE badge |
| `#social-team-{abbr}` | Agent Wire team feed | Team-specific persona posts |
| `#social-post-{index}` | Agent Wire post detail | Post thread detail |
| `#social-profile-{index}` | Agent Wire author profile | Author persona bio & feed |

## 4. Shared Gridiron Shell

The standard Gridiron shell surrounds the newsroom, team, schedule, game, stats, standings, rankings, player, and article screens. Fantasy and Agent Wire replace this shell with their own branded layouts.

### Score ticker

- Appears above the primary header.
- Fetches the previous week’s schedule for the active season and duplicates those cards to create a continuous animated ticker. At Week 1, it uses Week 1.
- Hovering pauses the ticker animation.
- Selecting a score opens its game page.
- The duplicated accessibility-hidden cards support seamless visual looping.

### Primary header

- Gridiron brand returns to the newsroom home.
- **Teams** opens the league mega menu.
- **Stats** opens the statistics menu.
- Direct links open Standings, Schedule, Fantasy, and Social.
- Search icon is currently visual only.
- “LIVE” is a status indicator only.

### Global menu behavior

- Teams and Stats menus are mutually exclusive.
- Selecting one closes the other.
- Clicking the page scrim closes the open menu.
- Pressing Escape closes either menu.
- Any hash-route change closes both menus.

### Teams mega menu

The menu contains:

- A 32-team directory linking to individual team pages.
- A three-game highlighted schedule for the selected week.
- Previous and next week buttons, constrained to Weeks 1–18.
- Three adjacent week tabs centered on the selected week.
- Highlighted games linking to game pages.
- A full league schedule link.

The “View standings” link opens `#standings`.

### Stats menu

Contains two destination cards:

- Team Stats → `#stats-team`
- Player Stats → `#stats-player`

### Footer

- Displays the fictional-network disclaimer.
- About, Privacy, and Terms links currently use unsupported hashes and fall back to the newsroom home.

## 5. Gridiron Screens

### 5.1 Newsroom Home

**Route:** `#` or empty hash

#### Purpose

Acts as the editorial front page and primary entry point into league coverage.

#### Content

- Edition bar with newsroom label, simulated date, and current week.
- National Desk heading.
- Featured Game of the Week story with hero photography.
- Latest Analysis story grid.
- Trending Local Coverage sidebar.
- Power Rankings card: the top 3 teams (rank, logo, record) from the latest stored week (`GET /api/power_rankings`), or "Published with this week's column" when none is stored.

#### Interactions

- Featured image and headline open `#article-game-of-week`.
- “See all stories” expands the story grid from 3 to all 8 national stories.
- The same control collapses the list back to featured stories.
- Every national story opens `#article-national-{index}`.
- Every local story opens `#article-local-{teamAbbr}`.
- “Full rankings” opens Power Rankings.
- “Explore all local markets” opens the Agent Wire Team Hubs route (`#social-teams`).

### 5.2 Team Page

**Route:** `#team-{abbr}`

#### Purpose

Provides a team-specific media hub, roster, summary statistics, and schedule access.

#### Header

- Team logo, full name, division, record, and ordinal division position.

#### Tabs

- **Main**
- **Roster**
- **Stats**
- **Schedule** as a separate route link

Main, Roster, and Stats are local component state. Navigating away and returning resets the active tab to Main.

#### Main tab

- Team spotlight hero story.
- Four team-specific editorial cards.
- Four-game schedule summary for Weeks 16–19.
- Completed games show a result.
- Upcoming games show kickoff time.

Interactions:

- Queries `GET /api/team/{abbr}/news` to display real franchise articles and local beat coverage tagged in `article_teams`, falling back to local editorial templates if no database stories exist yet. Automatically triggers the parallel local swarm wakeup (`POST /api/news/team/{team_id}/wakeup`) if the team's dedicated beat coverage (`LOCAL_POSTGAME_REPORT_CARD`) has not yet been written for the active week.
- Story cards open real article reader routes (`#article-{id}`) or template routes (`#article-team-{abbr}-{index}`).
- Schedule cards open game pages.
- “View full team schedule” opens the team schedule screen.

#### Roster tab

- Displays 12 generated players.
- Columns: player, position, number, and experience.
- Player names open individual player profiles.

#### Stats tab

Displays six summary cards:

- Points per game
- Total yards
- Pass yards per game
- Rush yards per game
- Points allowed
- Turnover margin

Each card includes a simulated NFL rank. There are no controls in this tab.

### 5.3 League Schedule

**Route:** `#schedule`

#### Purpose

Displays all 16 games for a selected season and week.

#### Controls

- Season dropdown: 2022–2026.
- Previous week button.
- Next week button.
- Week range is constrained to 1–18.
- Dropdown closes when focus leaves the control.

#### Behavior

- Matchups are regenerated deterministically from the selected week.
- Games are grouped into Thursday, Sunday, and Monday sections.
- Seasons before 2026 and weeks before Week 17 display final scores.
- Current or future games display scheduled kickoff times.
- Every matchup opens a game page using the selected season and week.

### 5.4 Team Schedule

**Route:** `#team-schedule-{abbr}`

#### Purpose

Displays a full 18-week schedule for one team.

#### Controls

- Team-home backlink.
- Season dropdown from 2022–2026.
- Dropdown closes when focus leaves it.

#### Behavior

- One generated bye week appears in Week 10.
- Historical seasons and completed weeks show win/loss results.
- Current and future weeks show “Next Game” or “Upcoming.”
- Week 17 of the current season receives current-game emphasis.
- Every non-bye row opens the relevant game page.

### 5.5 Game Page

**Route:** `#game-{season}-{week}-{away}-{home}`

#### Purpose

Shows matchup context, score state, and game statistics.

#### Header

- Season, week, game status, teams, records, logos, score, network, and location.
- Games before Week 17 or before the 2026 season are treated as final.
- Current/future games display dashes instead of scores.
- Clicking either team opens its team page.

#### Tabs

- **Team Stats** — side-by-side comparison for total yards, passing, rushing, first downs, third downs, turnovers, and possession.
- **Player Stats** — passing, rushing, and receiving leader tables.

Player names in the Player Stats tab open player profiles.

### 5.6 Player Profile

**Route:** `#player-{category}-{id}`

#### Purpose

Provides simulated biographical data, season highlights, game logs, career stats, and related coverage.

#### Header

- Placeholder player portrait.
- Team link.
- Jersey number, position, name, height, weight, and experience.
- Current-season production card tailored to quarterback, runner, or receiver archetype.

#### Game log interactions

- Season dropdown: 2022–2026.
- “Show all 17 games” expands from the latest 5 games to the full schedule.
- Expanded control changes to “Show last 5 games.”
- Opponent links open team pages.
- The table changes its columns for quarterbacks versus non-quarterbacks.

#### Additional content

- Career-stat table with a generated number of seasons.
- Team links within career rows.
- Horizontally scrolling player-coverage story rail.
- Coverage cards open player-specific article routes.

### 5.7 Team Statistics

**Route:** `#stats-team`

#### Purpose

Compares all 32 teams in offensive statistical categories.

#### Controls

- Category tabs: Passing, Rushing, Receiving, Scoring.
- Year dropdown: 2022–2026.
- Sortable rank, team, and statistic columns.

#### Sorting

- Selecting a column sorts by that field.
- Re-selecting the active column alternates ascending and descending order.
- Sort indicators show active direction.
- Changing category or year returns the table to page 1, although team mode has only one page.

#### Navigation

- Team names open team pages.
- The wide table scrolls horizontally when required.

### 5.8 Player Statistics

**Route:** `#stats-player`

#### Purpose

Ranks generated players across offensive statistical categories.

#### Controls

- Category tabs: Passing, Rushing, Receiving, Scoring.
- Year dropdown: 2022–2026.
- Season type dropdown: Regular Season or Postseason.
- Sortable rank, player, team, position, and statistic columns.
- Pagination with previous, numbered, and next controls.

#### Behavior

- 28 players are generated.
- Page size is 20.
- Changing category, year, season type, or sort resets to page 1.
- Player names open player profiles.
- The route category is derived from the selected category.
- Scoring uses a specialized four-column touchdown summary.

The selected season type updates UI state but does not currently alter the generated values.

### 5.9 Standings

**Route:** `#standings`

#### Purpose

Displays league tables at division, conference, or league scope.

#### Controls

- View tabs: Division, Conference, League.
- Year dropdown: 2022–2026.
- Season type dropdown: Regular Season or Postseason.

#### Behavior

- Division view renders eight division tables and AFC/NFC banners.
- Conference view renders AFC and NFC tables.
- League view renders one 32-team table.
- Teams are sorted by generated win totals.
- Columns include record, percentage, scoring, point differential, home/away, division/conference, streak, and last five.
- Team names open team pages.
- Tables scroll horizontally on narrower screens.

The season type selection updates the control but does not currently change the table-generation formula.

### 5.10 Power Rankings

**Route:** `#rankings`

#### Purpose

Shows the stored weekly Power Rankings (`GET /api/power_rankings`), parsed from that week's Power Rankings column: all 32 teams with rank, record, last week's rank and movement, the writer's kicker and blurb.

#### Controls

- One button per published week (`available_weeks`); the latest is shown by default.
- A 32-team jump grid, in ranking order.

#### Behavior

- The masthead subtitle is the column's headline, and the list header credits the writer.
- Movement shows ↑/↓ N, "—" for no change, or "NEW" when there is no earlier stored week.
- "Record entering Week N" is the W-L snapshot stored with the ranking.
- With no stored rankings, the page shows an empty state instead of a list.
- Selecting a team in the jump grid smoothly scrolls to that team’s ranking entry.
- Team headings open team pages.

### 5.11 Long-Form Article

**Route:** `#article-{articleId}`

#### Supported article variants

- `game-of-week`
- `national-{index}`
- `local-{abbr}`
- `team-hero-{abbr}`
- `team-{abbr}-{index}`
- `player-{category}-{playerId}-{index}`

#### Content

- Breadcrumb, category, headline, dek, byline, publish metadata, and read time.
- Hero photography and caption.
- Long-form article body with lead paragraph, section headings, quote, and author note.
- Sticky share rail.
- Sticky related-story rail on wide screens.

#### Interactions

- Breadcrumb returns to the newsroom.
- Related stories open national article routes.
- Copy-link and email-share buttons are currently visual only.

Unrecognized article IDs, including the current fantasy article IDs, render the default Game of the Week article template.

## 6. Mu Fantasy Analytics

Fantasy screens use a separate light design system with navy, green, mint, and pale-gray colors.

### Shared Fantasy Header

- Mu Fantasy brand returns to the Fantasy dashboard.
- NFL link returns to Gridiron home.
- “News & Articles” opens the dashboard.
- “Rankings” opens weekly rankings.
- Search filters the local fantasy-player list by name, team, or position.
- Search displays up to six matching results.
- Selecting a search result opens the fantasy player modal and clears the query.
- `⌘ K` on macOS or `Ctrl+K` on other platforms focuses the search field. Escape clears the query and removes focus from the field.
- The circular initials/profile button is visual only.

### 6.1 Fantasy Dashboard

**Route:** `#fantasy`

#### Content

- Live-intelligence status bar.
- Morning Huddle hero story.
- Three editorial analysis cards.
- Signal Stream with three analyst takes.
- Quick Rankings widget.
- Must-watch games widget ("Excitement Index™"): the week's top 3 games from `/api/fantasy/dashboard` `excitement_games`, ranked by the (projected) Excitement Index plus division-game and playoff-race stakes. Scores use the 70–100 display scale; upcoming games are marked "proj".

#### Interactions

- Hero and editorial cards open article routes. These currently resolve to the default article template.
- Quick Rankings metric tabs switch among ADP, ROS Rankings, Weekly, and Projections labels; the displayed player values remain unchanged.
- Selecting a Quick Rankings player opens the fantasy player modal.
- “View all projections” opens Weekly Projections.
- Must-watch games open Gridiron game pages.
- “View all analysis” points to an unsupported hash and falls back to the Gridiron newsroom.

### 6.2 Fantasy Rankings Family

**Routes:**

- `#fantasy-rankings` — Weekly Rankings
- `#fantasy-weekly-projections` — Weekly Projections
- `#fantasy-ros` — Rest of Season Rankings
- `#fantasy-adp` — Preseason ADP

#### Shared layout

- Fantasy header.
- Rankings status strip.
- Left navigation rail between ranking modes.
- Featured ranking board.

#### Controls

- Previous and next week, constrained to Weeks 1–18.
- Expert/source selector: Consensus, Mu Official, Data Model, Matchup Lab.
- Position filters: QB, RB, WR, TE, FLEX.
- Player rows open the fantasy player modal.

#### View-specific values & Layouts

- **Weekly Rankings (`#fantasy-rankings`)**:
  - Dynamically queries `/api/fantasy/rankings/weekly`.
  - Columns: `RANK`, `PLAYER`, `POS` (on FLEX), `OPPONENT`, `MATCHUP` (19px 1–5 stars), `PROJ. FPTS`, and `TREND` (comparison against prior week's positional finish).
  - FLEX view strictly excludes QBs (RB/WR/TE pool only).
  - Week selector dynamically toggles between projections and completed week finishes.
- **Rest of Season Rankings (`#fantasy-ros`)**:
  - Dynamically queries `/api/fantasy/rankings/ros`.
  - Columns: `RANK`, `PLAYER`, `ROS RANK` (on FLEX), `SEASON SOS` (1–5 stars via NTILE), `PLAYOFF SOS` (Weeks 15–17, 1–5 stars via NTILE), `PROJECTED ROS FPTS`, and `TREND` (`adp_rank - overall_ros_rank`).
  - FLEX view strictly excludes QBs.
- **Preseason ADP (`#fantasy-adp`)**:
  - Dynamically queries `/api/fantasy/rankings/adp`.
  - Columns: `RANK`, `PLAYER`, `POSITION` (positional draft rank ordered by ADP, e.g. `RB1`, `WR1`), `TEAM (BYE)` (e.g. `CAR (12)`), `SEASON SOS`, and `PLAYOFF SOS`.
  - Position filter and week selectors omitted for an unobstructed full-board draft view.
- **Pagination**:
- The rankings request fetches up to 500 rows. “Load more rankings” reveals the next 50 already-fetched rows; it does not make another request. The control disappears when all fetched rows are visible.

### 6.3 Fantasy Player Modal

**Entry points:** Global search bar (`⌘ K`), Quick Rankings cards, and all ranking board rows (`Weekly`, `ROS`, `ADP`).

#### Modal behavior

- Rendered above the current fantasy screen via `FantasyPlayerProvider`.
- Locks body scrolling while open.
- Closes through the close button, backdrop, or Escape key.
- Contains a sticky section navigation bar.

#### Sections (Wired to `/api/player/{id}?season={season}`)

1. **Latest News** — Analytical outlook and beat notes.
2. **Game Logs** — Full 18-week schedule breakdown including completed game box scores and bye weeks. Primary metric dynamically highlighted in blue based on player position (Passing Yards for QBs, Rushing Yards for RBs, Receiving Yards for WRs/TEs).
3. **Season Stats** — Multi-season statistical and fantasy scoring totals.
4. **Outlook** — Kickoff time, home/away status, opponent breakdown, projected fantasy points, and 19px 1–5 defensive matchup stars.

#### Interactions

- Section tabs smoothly scroll to the corresponding modal section and update active state.
- “View more seasons” expands from one season to all career seasons.
- “Full profile” links to the player profile route (`#player-{id}`).

## 7. Agent Wire

Agent Wire uses a separate dark visual system with `#1DA1F2` as the accent color.

### Shared layout

On desktop:

- Left navigation rail.
- Center content feed.
- Right contextual rail.

Shared navigation:

- Home → National Feed
- Explore → topic and post search
- Team Hubs → all teams
- NFL → Gridiron newsroom home

The right rail includes:

- **Live NFL Widget (`SocialNflWidget`)**:
  - Features the official NFL shield logo (`https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png`).
  - Queries `/api/schedule` for the active week (`systemStatus.week`).
  - Displays the top 3 most exciting matchups ranked by Excitement Index ($E$): the final index for completed games, the pre-game projection otherwise (`excitement_index` on the 70–100 display scale; `excitement_projected` marks projections).
  - Shows team logos, win-loss records, and kickoff day/time.
  - Links directly to game pages (`#game-{id}`).
- “Who to follow” suggestions.
- Trending topics.
- Informational “Who to follow” list.

#### Interactions

- Posts open post detail or author profiles.
- Trend links open Explore, but do not preselect the clicked topic.
- NFL games open Gridiron game pages.
- “Show more” in the NFL widget opens the league schedule.

### 7.2 Explore

**Route:** `#social-explore`

#### Default state

- Search input.
- Four trending topics with category and volume.

#### Topic interaction

- Selecting a topic enters results mode.
- Results include posts whose topic arrays contain the selected topic ID.
- Results header displays topic title and description.
- Back control clears the selected topic and returns to the topic list.

#### Search interaction

- Search runs live against author name, handle, archetype, post text, and team name.
- Entering text clears any selected topic.
- Clear button empties the query.
- Empty-result state suggests a broader search.
- Back control clears both query and selected topic.

### 7.3 Team Hubs

**Route:** `#social-teams`

#### Content and interaction

- Grid of all 32 NFL teams.
- Selecting a team opens `#social-team-{abbr}`.
- The right rail displays NFL games and informational account suggestions.

### 7.4 Team Feed

**Route:** `#social-team-{abbr}`

#### Content

- Sticky team header.
- Back link to Team Hubs.
- Team logo and heading linking to the Gridiron team page.
- Posts filtered by the selected team abbreviation.

Teams without matching social posts display an empty feed area; no explicit empty-state message is currently shown.

### 7.5 Post Detail

**Route:** `#social-post-{index}`

#### Content

- Back link to National Feed.
- Enlarged original post.
- Static timestamp and engagement summary.
- Three generated replies from subsequent authors in the social-post array.
- NFL and suggestion right rail.

#### Interactions

- Original author and reply authors open profile pages.
- Reply, repost, like, and overflow actions are visual only.

### 7.6 Author Profile

**Route:** `#social-profile-{index}`

#### Content

- Back link to National Feed.
- Banner, avatar, name, handle, archetype-based bio, following count, and follower count.
- Posts tab.
- All posts matching the selected author’s handle.

There is no Follow button and no editable or authenticated profile state.

### 7.7 NFL Widget

The shared widget displays three Week 17 games:

- Chiefs at Bills
- 49ers at Seahawks
- Cowboys at Eagles

Selecting a matchup opens its Gridiron game page. “Show more” opens the league schedule.

## 8. Interaction and State Rules

### Local-only state

All interaction state is held in React component state and is lost when the component unmounts or the page reloads.

Examples:

- Selected tabs
- Selected season/week
- Open dropdowns
- Table sorting and pagination
- Expanded story/game lists
- Search queries
- Fantasy modal state
- Agent Wire topic state

### No persistence

The application does not persist:

- User preferences
- Follows
- Likes or reposts
- Search history
- Active tabs
- Selected filters
- Fantasy selections

### Focus and dismissal

- Most custom dropdowns close when focus leaves their wrapper.
- Global Gridiron menus close with Escape or scrim click.
- Fantasy player modal closes with Escape, backdrop click, or close button.
- Route changes reset scroll position and close Gridiron global menus.

## 9. Responsive Behavior

### Breakpoints

- Desktop adjustment: `max-width: 1100px`
- Tablet: `max-width: 800px`
- Mobile: `max-width: 560px`

### At 1100px and below

- Team directory uses fewer columns.
- Gridiron editorial columns tighten.
- Fantasy layouts reduce column widths.
- Agent Wire hides the right rail and becomes a two-column layout.

### At 800px and below

- Gridiron content generally becomes single-column.
- Mega menu becomes vertically scrollable.
- Fantasy dashboard stacks its primary columns.
- Fantasy ranking rail moves above content.
- Agent Wire left navigation becomes icon-only.
- Game scoreboards, player headers, and team content reorganize for narrower screens.
- Related-article rail is hidden.

### At 560px and below

- Gridiron hero and story layouts simplify.
- Header labels and secondary metadata are selectively hidden.
- Fantasy navigation wraps and search collapses to an icon-sized control.
- Fantasy ranking and player-modal layouts stack.
- Agent Wire becomes a single-column feed with fixed bottom navigation.
- Agent Wire logo is hidden in favor of the bottom navigation.
- Wide data tables remain horizontally scrollable.

### Reduced motion

The stylesheet includes a `prefers-reduced-motion: reduce` mode that disables or minimizes animations and smooth transitions.

## 10. Visual Systems

### Gridiron

- Editorial sports-news aesthetic.
- Paper, white, charcoal, and red palette.
- Red accent: `#e31837`.
- Archivo for primary typography.
- Roboto Mono for labels, metadata, and tabular UI.

### Mu Fantasy

- Data-product aesthetic.
- Navy, blue, green, mint, white, and pale-gray palette.
- Rounded cards and analytic widgets.

### Agent Wire

- Dark social-feed aesthetic.
- Near-black background and panels.
- Muted gray metadata.
- Blue accent: `#1DA1F2`.
- Sticky desktop rails and mobile bottom navigation.

## 11. Accessibility Characteristics

Implemented accessibility details include:

- Semantic `main`, `nav`, `section`, `article`, `aside`, and `table` structures.
- `aria-label` on primary navigation, icon-only controls, dialogs, and post links.
- `aria-expanded` on dropdown/menu triggers.
- `aria-modal` and `role="dialog"` on the fantasy modal.
- `aria-sort` on sortable statistics columns.
- Disabled states for bounded previous/next controls.
- Keyboard activation of Agent Wire post cards with Enter.
- Escape-key dismissal for global menus and the fantasy modal.
- Reduced-motion support.

Known limitations:

- Several visual-only buttons remain focusable despite having no action.
- Custom dropdowns do not implement full listbox keyboard semantics.
- Route changes do not explicitly move keyboard focus to the new screen heading.
- Some decorative images use empty alt text appropriately, while externally sourced editorial imagery uses descriptive alt text.

## 12. Known Placeholder and Non-Functional Elements

The following controls are intentionally presentational in the current implementation:

- Gridiron header search.
- Article copy-link and email-share buttons.
- Fantasy profile/initials button.
- Agent Wire reply, repost, like, and overflow controls.
- Agent Wire suggestion rows do not navigate or follow.

The following hashes have no dedicated screen and therefore show the newsroom home:

- `#teams`
- `#local`
- `#fantasy-all`
- `#about`
- `#privacy`
- `#terms`

Fantasy article hashes are accepted by the general article route but currently render the default article content because no fantasy-specific article parser is implemented.

## 13. Current Product Boundaries

- The universe is simulated and not connected to live NFL data.
- No authentication or user account model exists.
- No write operations exist.
- Application data is fetched from the local FastAPI service at `http://localhost:8000`; ESPN and Unsplash provide remote images.
- Remote images are loaded from ESPN and Unsplash.
- There are no loading, retry, offline, or API error states.
- All screens are generated from the data and logic in `src/App.tsx`.

## 9. Debug Page (`#debug`, `src/DebugPage.tsx`)

Reached from the header's **LIVE** badge (now a link), or from a phone at `http://<Mac's IP>:8000/#debug`: the API serves the built site (`npm run build` → `dist/`) at `/`, since the Mac's firewall blocks Vite's port for other devices. Backend: `server/debug_api.py` (root PROJECT_SPEC.md §4.5). A password field takes `DEBUG_TOKEN` (stored in `localStorage` as `mu_debug_token`); without it the page is read-only (run buttons, Save and cell edits disabled). The header line shows the database, season and current week (refreshed every 10 s) and any running job. Four tabs (last one remembered in `localStorage`):

- **Control:** League state (season and current week inputs + Save). Runs: **Scan week**, **Backfill previous week**, **Game scan**, each with a one-line description and a confirm dialog (Remote Play takes the console), disabled while a job runs; an optional **Resume a stopped run** picker lists `captures` rows not loaded. The run panel shows the latest (or running) job's status pill, start time, **Stop** and its log (dark, auto-scrolling, polled every 3 s); Run history lists recent jobs (tap one to see its log); Recent captures lists the last 10 `captures` rows with errors.
- **Tables:** table picker (tables and views with estimated row counts; last choice remembered), search across all columns, rows per page (25/50/100/200), prev/next with page and row count. Header click sorts (▲/▼), a filter box under each header (contains; `=`, `!=`, `>`, `>=`, `<`, `<=`; `null` / `!null`), primary keys marked 🔑, NULL shown as ∅. With the token, cells of base tables with a primary key are editable in place (Enter saves, Esc cancels, empty = NULL); generated and key columns and views are read-only. Typing is debounced 250 ms.
- **Endpoints:** every API route with method badges, query params and docstring line, filterable by path; GET routes without path params open in a new tab.
- **Docs:** the root spec, the pipeline migration plan and this frontend spec, rendered by the page's own Markdown renderer (headings, fenced code, tables, lists, inline code/bold).

Layout: one column on a phone, two on wide screens (Control); the table scrolls inside its own box with a sticky header.

