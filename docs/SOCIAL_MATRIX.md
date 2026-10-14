# Agent Wire Social Matrix (agreed with JR, Oct 14, 2026)

> **Status: plan agreed; steps 1–7 built.** Steps 1–2: `db/migrations/2026_10_22_agent_wire_social.sql` (applied to the live database Oct 14): junk tweets removed, social schema, approved handles, names and bios, the three new accounts. Step 3: the planner, `newsroom/social_desk.py` (PROJECT_SPEC §7.8), reviewed by JR. Step 4: the post writer (`newsroom/social_writer.py`) and `tools/social_model_compare.py`; JR picked Gemini 3.8 Flash for every post kind after the side-by-side test (5 errors in 20 Flash-Lite posts, none in Flash). Step 5: `newsroom/social_publish.py`, wired into `newsroom/weekly_run.py` and team wakeups; the old reaction tweets are removed. Step 6: the `/api/social/*` endpoints (`server/social_api.py`); the fantasy page's social takes are real. JR published the first live week (2035 Week 18) on Oct 14: 100 posts, 20 trends; its review added fan angles, reply stances, foils for the national writers and waking-hours timing. Step 7: the Agent Wire pages run on `/api/social/*` (Home, Explore and trends, posts, profiles, Team Hubs) and the mock data is removed; checked in a browser against a copy of the live Week 18 data. After reviewing the pages, JR asked for 20 ex-player accounts and a player-first mix (see "Player-first mix and ex-players" below; `db/migrations/2026_10_25_former_players.sql`). Step 8 (JR's review of the live page) is next. This file is the source of truth for the weekly social run, alongside `docs/EDITORIAL_MATRIX.md` (newsroom) and `docs/FANTASY_EDITORIAL_MATRIX.md` (fantasy). It merges a draft another session wrote earlier the same night with the decisions JR made in the claude.ai planning session. JR has since reviewed and settled the draft's extra proposals, too.

## Review status

**Decided (JR)**
- **Who posts:** the current pundits and fans, plus the **66 local beat reporters**, which are no longer excluded. New accounts: **The Flex Spot (`@TheFlexSpot`)**, the renamed `FANTASY_GURU`; **`@GridironMemes`** (`NFL_MEMES`); **`@MockDraftMarty`** (`DRAFT_SICKO`; this name is the default, since JR didn't pick one). `ESPN` and `SCHEFTER` are dropped, because they duplicate existing voices.
- **Handles:** every account that posts gets a real-looking, non-generic handle and display name (§6). Die-hard fans of the same team who share a first name get renamed.
- **Post kinds:** takes, **article shares**, and **replies**.
- **Volume:** **about 100 posts per weekly run** (§2). Team wakeups add to that.
- **Topic split:** a **social desk planner** builds every assignment from the week's data, so posts and trends follow the biggest games and performances (§2, §4).
- **Engagement** (likes, reposts, reply counts, followers) is **computed**, not random (§5).
- **Trends** come from team and player tags plus hashtags, counted per week, with no extra model calls. They should line up with the week's biggest games and player performances (§4).
- **Timestamps:** made up but realistic, fitted to the real game slots and the week's calendar (§3).
- **Navigation:** Home gets **For You** and **Latest** tabs. The NFL link stays as it is: it goes to the home page by design. **Opening a Team Hub starts that team's wakeup**, the same as the team page.
- **Fantasy page:** its "social takes" come from real posts by the fantasy accounts (The Flex Spot, Berry, Yates).
- **Models (decided after the test):** **Gemini 3.8 Flash for every post kind**; 3.5 Flash-Lite made 5 factual or voice errors in 20 posts, 3.8 Flash none. Cost ≈ $0.41 per 100 posts now, ≈ $0.82 from Jan 1, 2027.
- **How posts are written:** each post's facts go into its assignment, and tools are **off by default**. The model is set per post kind in config. Before choosing, JR reviews **the same assignments written by Gemini 3.8 Flash and by Gemini 3.5 Flash-Lite**, side by side (build step 4). Google has no text model called 3.8 Flash-Lite; 3.5 Flash-Lite is its newest Lite text model.

**Player-first mix and ex-players (JR, Oct 14, after step 7)**
- **20 ex-player accounts (`FORMER_PLAYER`):** real retired stars plus 2026 NFL Top 50 players who aren't players in the 2035 database (treated as retired before 2035), same "in the style of" convention as the pundits. They work like bandwagon fans, posting about players league-wide, with a soft spot for their old team.
- **Only die-hards post about a team as a team.** Bandwagon fans and ex-players post about players; bandwagon fans clown players who play badly.
- **New weekly mix:** die-hard reactions one per team that played, no extras; performances 14; bad games 8; comparisons 10 (who's the best at a position; is our best player better than theirs); shares ≈15 (one per article); replies 10–15; previews 6; beat notes 5; standings 2; memes 2. This replaces the table in §2 (PROJECT_SPEC §7.8 has the details).
- **What the league is talking about:** big player games (tier 1: 400+ passing yards, 5+ passing TDs, 150+ rushing or receiving yards, 3+ TDs, 40+ PPR; tier 2: 200+ yards, 6+ TDs, 50+ PPR, the season's best game at a position), season milestones and NFL record chases, and team storylines (the Panthers' 16-0 start, winless runs, streaks and how they end, blowouts, upsets, the points record) get several posts from different voices, take slots from ordinary performances, and draw more engagement. Thresholds and numbers: PROJECT_SPEC §7.8.

**Parked**
- **Avatars:** JR will come back to this. Until then, avatars are monograms in team or brand colours, with no photos of real or stock people.

**Also decided (from the other session's draft, reviewed by JR)**
- **Preview wave (Thu–Sat), counted inside the 100.** Every post is written in one run, after last week's games are final and before this week's games are played. So previews only use what exists then (records, last week's results, the matchup), and they are accurate whenever they're written. `posted_at` spreads the week across a believable timeline: reactions Sun–Mon, beat notes, standings and shares Tue–Wed, previews Thu–Sat. Each preview is dated before its own game's kickoff. The facts packets include data only through last week, so no post can know how this week's games end. The site shows all of a week's posts at once; the times set the order and the time labels.
- **Every team that played gets at least one die-hard post a week**, so every Team Hub has new content.
- **The postseason keeps 100 posts a week.** Fans of eliminated teams post about **players**: their own team's players' seasons and what's next for them, and players still alive in the playoffs. Those posts fill the slots freed up by fewer games and by fantasy posts ending after W18.
- **Bios:** one short line per agent, written once from its persona in one batched call.
- **Beat-reporter names:** regenerate the duplicated templated surnames.

---

## 0. Where things stand

- **Posts are generated but never shown.** `dispatch_weekly_national` posts about 16 tweets a week: 4 random pundits, 2 bandwagon fans, and 1 die-hard per team in the 5 most exciting recap games. `dispatch_team_local` posts the team's die-hards plus 0–3 pundits. All are saved to `news_feed` with `content_type = 'TWEET'`. No endpoint returns them, and `/api/team/{abbr}/news` filters to `ARTICLE`.
- **Posts aren't linked to their author.** `news_feed.persona` stores the archetype only. With 81 `DIE_HARD_FAN`s, the specific fan can only be recovered through `agent_memory_entries.article_id`, and only when memory recording succeeded.
- **No real tweets are stored.** All 37 `TWEET` rows in the last backup are junk from before the "only real output is saved" fix: Gemini error strings and offline mock text. They get deleted, with a backup first.
- **Posts have no social shape:** no replies, kinds, engagement or posting time. Every post in a run shares one `created_at`.
- **Account gaps:** the 17 tweet-only pundits (Schefter, Stephen A, McAfee, etc.) have no `handle` or `display_name`. The 66 beat reporters have neither. `FANTASY_GURU`, `NFL_MEMES` and `DRAFT_SICKO` have personas but no `agent_memories` row.
- **Posts are expensive.** Each gets the full assignment brief and the article toolset (up to 50 tool calls). Every tool call resends the whole prompt.
- **The fantasy page's `social_takes`** are hardcoded in `/api/fantasy/dashboard`.

## 1. What the page needs

| UI element | Today | Source |
| :--- | :--- | :--- |
| Home feed (`#social`) | 8 mock posts | The week's posts. **For You** = engagement with a recency decay; **Latest** = `posted_at` descending. Paginated. |
| Post card: name, handle, archetype, team, time, counts | mock | `agent_memories` + the post row (§5, §6) |
| Post detail (`#social-post-{id}`) | recycled mock authors as replies | The real reply thread (`reply_to_id`) |
| Profile (`#social-profile-{agent_id}`) | index-based, invented counts | Bio, followers, the agent's posts |
| Explore / "What's happening" | 4 mock topics | `social_trends` for the week (§4) |
| Explore search | over the mock array | Server search over post text, author and team |
| Team Hubs / team feed (`#social-team-{abbr}`) | mock filter | Posts filed under the team or tagged with it. Opening it starts the team's wakeup. |
| Who to follow | 3 hardcoded people | Top accounts by followers among those posting about this week's trending teams and players |
| Article share card | none | Linked article headline and excerpt, opening `#article-{id}` |
| NFL widget, NFL link | live / home | unchanged |
| Fantasy page social takes | hardcoded | This week's top 3 fantasy posts by engagement; empty state if none |

## 2. The weekly social run

A new **Social Desk** (`newsroom/social_desk.py`) runs inside `weekly_run` **after** the national and fantasy slates, under the same advisory lock, so it can share and react to the week's articles. It has its own already-published check (`social_slate_exists`: any post with a social slot for the week), plus a `social` flag on `run_weekly_swarm` and `--social-only` / `--skip-social` on the CLI. The reaction tweets now in `dispatch_weekly_national` move into it.

**The planner builds a list of assignments first.** Each assignment names an account, a post kind, a subject (game, player, team or article) and a **facts packet**. Writing comes after. `--dry-run` prints the full list without calling the model. Accounts are spread least-used first, with weekly caps: pundits 3, beat reporters 2, fans 2.

| # | Kind | Simulated time | ~Posts | Subjects | Who posts |
| :-: | :--- | :--- | :-: | :--- | :--- |
| 1 | Game reactions (`REACTION`) | Minutes to hours after each game's slot ends (Thu / Sun 1:00 / 4:25 / SNF / Mon) | 34 | One die-hard post for each team that played. Extra posts go to the top games by excitement index. | That team's die-hards; pundits and bandwagon fans on the top 3–5 games |
| 2 | Player performances (`PERFORMANCE`) | Sun night – Mon | 12 | The week's top stat lines and fantasy scores (all positions, including big defensive lines) | Pundits, that team's fans, fantasy accounts |
| 3 | Beat notes (`BEAT`) | Mon – Wed | 6 | Teams in the biggest games, plus the biggest movers in the standings | That team's beat reporters |
| 4 | Standings and races (`STANDINGS`) | Tue | 4 | Division leaders, the playoff bubble, the race for the 1 seed (`season_landscape`) | Pundits, bandwagon fans |
| 5 | Article shares (`SHARE`) | When the slate publishes (Tue – Wed) | ~14 | One per published article that week (national, local and fantasy) | The article's writer, including long-form-only writers. They still aren't drawn as random pundits. |
| 6 | Replies (`REPLY`) | Minutes to hours after the parent post | 20 | The most engaged posts from waves 1–5, 2–3 replies each | Accounts with a reason to answer: rival-team fans, the subject team's beat reporter, a contrarian pundit (e.g. Shannon → Skip), a same-team fan piling on |
| 7 | Previews (`PREVIEW`) | Thu – Sat | 8 | The top 3 projected games (`rank_games_by_interest`); fantasy start/sit (W2–17) | Pundits, both fan bases, The Flex Spot / Berry / Yates |
| 8 | Memes and draft (`MEME`) | Any time | 2 | Losing teams; teams sliding toward the top of the draft order | @GridironMemes, @MockDraftMarty |

**Total: 100.** The number of article shares varies from week to week, so the planner trims or tops up the extra reaction posts (wave 1) to land on 100. Replies are planned **after** waves 1–5 are written and their engagement is computed, so replies gather on the posts that look biggest.

**Team wakeups** (team page or Team Hub view) use the same planner in team mode: each of the team's beat reporters posts one note, each of its die-hards posts one reaction to the last game or a preview of the next, and 0–3 pundits post, using today's popularity rule. These posts are on top of the 100.

**Postseason (W19–23):** still about 100 posts a week. Reactions cover only the games played. The fantasy posts stop after W18. The freed slots go to a **player-post bucket** (`PERFORMANCE` subjects chosen for the offseason): fans of eliminated teams post about their own team's players (how their season went, who should be back) and about players still in the playoffs. @MockDraftMarty posts more draft-order takes.

## 3. Post prompt, facts and time

- **Facts packet in place of the article brief.** A reaction gets the final score, both records after the game, the game's top stat lines (`player_stats`) and its excitement tier. A performance gets the player's line and his season rank. Standings posts get the relevant `season_landscape` slice. A share gets the article's headline and excerpt. A reply gets the parent post, its author and the parent's facts. **Tools are off by default.** If the model comparison shows takes need them, they get a cap of 6 calls.
- **One short instruction per post kind**, on top of the account's tweet persona (the `archetypes` entry in `agent_personas.json`, not an article persona).
- **The grounding rules don't change:** `natural_language_rule` (no ratings, OVR or Madden), no injuries, betting or contracts, and numbers only from the facts packet.
- **Cleanup on save:** strip wrapping quotes and markdown, enforce 280 characters, allow at most 2 hashtags (stored in `hashtags`). Share links are stored in `linked_article_id`, not written as URLs in the text.
- **Agent memory:** takes and replies are stored as takes, as tweets are today. Shares aren't, because the article already records the take.
- **Simulated time (`posted_at`):** Week 1's Thursday is the first Thursday after Labor Day in the season year. Each week is 7 days later; the postseason follows the playoff schedule. Game slots come from `games.game_time`. Each post gets a time inside its wave's window, with seeded jitter. `created_at` keeps the real generation time.

## 4. Trends

These are computed **after** the run from tags, with no model call, and stored in `social_trends` for the week.
- **Tagging:** posts are tagged with teams and players by `EntityTaggingService`, the same tagger articles use (`article_teams` / `article_players`).
- **Candidates:** teams, players, games (when both teams appear in a post, or the post's subject is a game) and hashtags.
- **Score:** the summed engagement of a candidate's posts, weighted toward recent posts. Because the planner aims volume at the top games and performances, the trends follow them.
- **Display:** the title comes from a template ("Ravens–Chiefs", the player's name, "#BillsMafia"). The category is the division, "Fantasy" or "Draft". The description is an excerpt from the candidate's top post. The displayed volume ("18.4K posts") is simulated from engagement.
- The top 6 trends appear in "What's happening"; the full list is on Explore. A trend opens a feed filtered to its posts.

## 5. Engagement and followers (computed, deterministic)

- **Followers**, set once per agent and seeded from `agent_id` so they stay stable:
  - national insiders: 1–5M
  - other national voices: 0.5–3M, by tier
  - beat reporters: 20–150K, scaled by team popularity
  - fans: 200–25K, scaled by team popularity
  - The Flex Spot, @GridironMemes and @MockDraftMarty: 50K–1M
- **Likes** = followers × base rate × subject heat × kind factor × lognormal noise (seeded by post id). Subject heat comes from the game's excitement index, the player's or team's popularity index, or the shared article's slot. Replies get a lower kind factor.
- **Reposts and reply counts** are ratios of likes. The reply count shown is the simulated count plus the real replies.
- Values are computed when a post is saved and stored on the row, so they never change between page loads. All constants live in `newsroom/config/social_desk.json`.

## 6. Accounts

- **Handles and names for every account that posts:**
  - The 17 tweet-only pundits follow the convention the other pundits already use (e.g. `@billbarnwell`).
  - Beat reporters get personal handles built from their name and team (e.g. `@EmilyWilliamsBAL`, display name "Emily Williams"), not "Ravens Beat 2". Duplicated templated surnames are regenerated first.
  - Die-hard fans of the same team who share a first name get new names and handles.
  - Bandwagon handles stay as they are.
- **New agent rows:** `FANTASY_GURU` → The Flex Spot (`@TheFlexSpot`), `NFL_MEMES` → `@GridironMemes`, `DRAFT_SICKO` → `@MockDraftMarty`. In `TWEET_EXCLUDED_ARCHETYPES`, `LOCAL_BEAT_REPORTER` is no longer excluded from the social desk; random-pundit draws still skip beats and fans.
- The full list of handles is generated and **shown to JR for approval** before the migration is applied.
- **Bio:** one short line per agent, written once from its persona (one batched call) and included in the list JR approves. **Avatar:** monogram, and `avatar_color` from the team colour or brand (parked).

## 7. Schema and API

- **`news_feed`** (posts stay here, so agent memory, tagging and the `clear_news_feed.sh` cascades keep working). New columns:
  - `agent_id`, `post_kind`, `posted_at`
  - `reply_to_id` (FK `news_feed` ON DELETE CASCADE), `linked_article_id` (FK `news_feed` ON DELETE SET NULL)
  - `subject_game_id`, `subject_player_id`
  - `likes`, `reposts`, `reply_count`, `views`, `hashtags TEXT[]`
- **`agent_memories`:** add `bio`, `followers`, `avatar_color`.
- **New `social_trends`:** `season`, `week`, `rank`, `kind` (TEAM / PLAYER / GAME / HASHTAG), `key`, `title`, `category`, `description`, `volume`.
- **API:**
  - `GET /api/social/feed?season&week&tab=foryou|latest&team&trend&cursor`
  - `/api/social/post/{id}` (with its thread)
  - `/api/social/profile/{agent_id}`
  - `/api/social/trends`
  - `/api/social/search?q`
  - `/api/social/suggestions`
  - `/api/social/team/{abbr}` (starts the wakeup through `start_local_wakeup`)
  - `/api/fantasy/dashboard` serves real `social_takes`
- **Frontend:**
  - Replace the mock arrays.
  - Routes key on ids (`#social-post-{id}`, `#social-profile-{agent_id}`), not array indexes.
  - Add the For You / Latest tabs, share cards and empty states.

## 8. Build steps

Each step updates `PROJECT_SPEC.md`, gets tests in `tests/`, and is committed to `main`. Steps that touch the database start with `./db/backup_db.sh`.

1. **Cleanup and accounts.** Delete the 37 junk tweets (back them up first). Generate the handle, name and bio list and **show it to JR**. Then write it as a migration with the three new agents.
2. **Schema migration** (and `init_db.sql`).
3. **Planner.** `social_desk.py` builds the assignment list and facts packets. **Exit:** a `--dry-run` for a real week prints about 100 sensible assignments, and JR looks them over.
4. **Generation and model comparison.** Write the post kinds' prompts, cleanup and token logging (`usage_metadata`). Write **the same 20 assignments** (fans, pundits, beat reporters, a share and replies) with **Gemini 3.8 Flash and Gemini 3.5 Flash-Lite**, and put them side by side, with cost per post, for **JR's review**. **Exit:** JR picks a model for each post kind; the choice goes into `social_desk.json`.
5. **The run.** Engagement, posting times, the reply pass and trends. Wire it into `weekly_run` after the slates and into team wakeups (team page and Team Hub). Remove the old tweet code from the national and local dispatch.
6. **API.**
7. **Frontend**, including the fantasy page's social takes.
8. **Publish a real week** (2035 W18) and review the page with JR.

## 9. Cost

The figures are estimated; step 4's token logging replaces them.
- **Gemini 3.8 Flash:** $0.75 / $3.75 per 1M input / output tokens through Dec 31, 2026, **doubling to $1.50 / $7.50 on Jan 1, 2027**. The article runs are affected too.
- **Gemini 3.5 Flash-Lite:** $0.30 / $2.50.
- **The old tweet setup** (full brief plus tools) costs about 1–3¢ a post: 100 posts ≈ $1–3 a week on Flash now, $2–6 from January, $0.45–1.30 on Flash-Lite.
- **With facts packets and no tools,** posts should cost well under that on either model.
