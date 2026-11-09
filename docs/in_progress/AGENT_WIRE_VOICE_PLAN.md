# Project Mu: Agent Wire Voice & Writer Biases Plan

**Status:** approved by JR (2026-10-31); building, wave 1 of 2 done (2026-11-09). U1, U2 and U3 are merged on main; U3 was reworked from its original branch (`7e4adae`) against the current `social_desk.py` so the trade-talk leak behaviour from commit 8376564 is preserved, not merged over. **Wave 2 is not built** — verified 2026-11-09: U4 has no relationship block in `social_publish.py`/`personas.py`, no `apply_graded`/`decay` in `weekly_run.py`, no `seed_writer_relationships` in the seed and no `person_key` in `agent_accounts.json`; U5's `tools/social_quality_report.py` does not exist. **Unheld by JR (2026-11-08)** — the hold "until the end of the V2 plan queue" is lifted and U3 is now CLOSE_OUT_CHECKLIST P1-06, a prerequisite for the 2034 quality-of-life checkpoint.
- *Spike (2026-10-31, 2036 Week 12 on `madden_2036`, 62 Gemini calls, JR read blind):* of 21 decided posts JR preferred a new-instruction version 15 times (plain text 8, structured output 7) and the old prompt 6 times (mostly reactions, the beat note and the standings post). Structured output parsed 100% and was not judged stiffer, so R1 is settled: keep the JSON output contract. The new instructions are better but not enough: JR found the posts "have more personality but still don't read like tweets", and fan reactions still shared stock phrases across accounts. Before wave 1, a real-tweet audit (§2.7) sets the style rules U2 builds.
**Builds on:** PROJECT_SPEC §7.7 (agent memory), §7.8 (Agent Wire social desk), §7.11 (structured personas); [`SOCIAL_MATRIX.md`](../done/SOCIAL_MATRIX.md); [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §4.7 (relationships, designed, not built); [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §7.2 (the 2026–2033 all-CPU era); [`V3_STUDIO_PLAN.md`](../in_progress/V3_STUDIO_PLAN.md) §2 (social posts family), §10 (local models).

**Goal:** Agent Wire posts that read like real NFL Twitter instead of captioned box scores: still reacting to the week's games, but also arguing about the league, making picks and predictions that get graded, and showing each writer's own favourites and grudges. Those biases are written down per person, grow from what each writer says and from being right or wrong, and colour both posts and articles. Weekly picks double as the memory source for the CPU years, when few articles are written.

**Delivers:**
- Rewritten post instructions (`social_writer.KIND_INSTRUCTIONS`) that state the situation and intent instead of a menu of moods, plus a sampled post shape per post (`social_writer.POST_SHAPES`, `pick_shape`).
- New post kinds: `PICKS`, `HOT_TAKE`, `AWARD_RACE`, `POWER_TAKE`, `QUESTION`, `SEASON_CALL`.
- A weekly mix that varies with the news (`social_desk.week_mix`) in place of the fixed `BUCKET_SIZES` and `TRIM_ORDER`.
- League topics for general posts (`social_desk.load_league_topics`).
- Structured post output (`{text, about, picks, calls}`) so picks, season calls and the post's subjects are stored without parsing tweets.
- Writer biases: `newsroom/relationships.py` (seed, grow, earn, render) writing `persona_relationships`, keyed by person (`agent_memories.person_key`).
- Graded winner picks and season calls: `agent_predictions.pick_type`, nullable scores; new table `agent_season_calls`.
- Memory shared across one person's accounts (Skip's column and Skip's tweets).
- `tools/social_quality_report.py` (repetition and mix metrics) and a prompt A/B in `tools/social_model_compare.py`.

**JR's answers (2026-10-31):**
- D1. Biases form three ways: seeded at creation, grown from the writer's own takes, earned by being right or wrong.
- D2. Biases belong to each account, except that accounts of the same person share them (Skip Bayless's article account and tweet account are one person). Implemented as a person key.
- D3. Weekly picks are public posts.
- D4. The weekly volume and mix vary with the week instead of fixed bucket sizes.
- D5. Re-write the per-kind instructions so posts stop reading canned.
- D6 (2026-11-02, Q5). Mild swearing for fan archetypes only, at a low rate, never slurs; national writers and team-adjacent accounts stay clean.
- D7 (Q6). Add two national accounts with original names: a label-first aggregator and a stats account.
- D8 (Q7). `LIVE_MOMENT` and `QUARTER_UPDATE` posts are written after the save is read, timestamped during the game, and use only plays the save records.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `newsroom/social_desk.py:27-42`: `TARGET_POSTS = 100`, fixed `BUCKET_SIZES` (PERFORMANCE 14, BAD_GAME 8, COMPARISON 10, BEAT 5, STANDINGS 2, PREVIEW 6, MEME 2, REPLY 10–15); `:155` `TRIM_ORDER` trims busy weeks back to 100. | Every week has the same shape. D4 replaces both with `week_mix`. |
| S2 | `social_desk.py:1499-1538`: `FAN_ANGLES`, `BANDWAGON_ANGLES`, `BEAT_ANGLES`, `PREVIEW_ANGLES`, `REPLY_STANCES` are short fixed lists picked by a character-sum seed (`_assignment`, `:1541`). | The same handful of angles recur all season. They go; angle becomes an optional hint from topics or relationships. |
| S3 | `newsroom/social_writer.py:43-64` `KIND_INSTRUCTIONS`: each kind lists the moves to make, e.g. REACTION "elation, disbelief, heartbreak, receipts against doubters, or sheer relief", PERFORMANCE "impact, swagger, legacy standing…". `SOCIAL_RULES` (`:23`) then lists more options. | The model picks from the menu every time. D5 rewrites them as situation + intent, with no menu. |
| S4 | `social_writer.py:74-133` `build_post_prompt`: persona + identity + memory + rules + instruction + full FACTS JSON; one call, temperature 0.9 (`write_post`, `:213`), plain-text output. | Full stat packets push box-score captions. The planner pre-selects 1–3 `focus` facts; output becomes JSON so picks and subjects are captured. |
| S5 | 2036 archive (`madden_2036.news_feed`, slot `Agent Wire%`, measured 2026-10-31): 2,467 posts. REACTION 553 posts with 317 distinct two-word openers; PLAYER 160 with 72; BEAT_INJURY 88 with 40. A rough forward-looking word match (will, next week, pick, calling it…) hits 465 of 2,467 (19%). Reactions sampled almost all recite the score and the record, then an emotion. | Baseline for `social_quality_report`; the target is measured against these numbers. |
| S6 | Post kinds in `newsroom/config/social_desk.json` `kind_factor`: REACTION, PERFORMANCE, PLAYER, BEAT, BEAT_INJURY, INJURY_SHOCK, STANDINGS, SHARE, PREVIEW, MEME, REPLY, BAD_GAME, COMPARISON, TEAM_STORY, MOVE_NEWS, BEAT_MOVE, MOVE_REACTION, CAP_TAKE, TRADE_NEWS, TRADE_REACTION, TRADE_RUMOR, DRAFT_BOARD. Only PREVIEW looks ahead, and it is told not to predict a score. | Nothing produces picks or league-wide takes. New kinds needed. |
| S7 | `newsroom/agent_memory.py:35` `parse_score_predictions` reads "Projected Final" lines from articles only; `agent_predictions` (`db/init_db.sql`) needs `score_a`, `score_b` NOT NULL and `article_id` → `news_feed`. Tweets become takes as-is (`store_tweet_take`, `:226`; called from `social_publish.py:428`) with a team id only, no player, no sentiment. | Picks from posts need winner-only picks (nullable scores, `pick_type`) and must be stored from structured output, not parsed. Takes need the player and a lean for bias growth. |
| S8 | `persona_relationships` exists (`db/init_db.sql`, migration `2027_01_09_personas.sql`): `persona_type`, `persona_id`, `target_type`, `target_id`, `trust` −1..1, `note`, `tags`. `newsroom/personas.py:329` loads rows into the persona dict; `render_persona` (`:734`) never uses them; nothing in the repo inserts a row. | The table is ready; the writing, updating and prompt rendering are new. |
| S9 | Writer personas are keyed by archetype; `personas.SHARED_ARCHETYPES` (`:97`) = LOCAL_BEAT_REPORTER, DIE_HARD_FAN, BANDWAGON_FAN, FORMER_PLAYER, so 66 beat reporters share one persona. Memory (`agent_memory_entries.agent_id`) is per account. `social_desk.SHARE_ACCOUNT_ARCHETYPE = {"SKIP_BAYLESS": "SKIP_TWEET"}` (`:199`) is the only place the code knows two accounts are one person. | Biases keyed by archetype would give every beat reporter the same grudges (D2). A `person_key` on `agent_memories` fixes both relationships and split memory. |
| S10 | `personas.build_persona_block` (`:790`) is called for posts (`social_publish.py:474`) and articles (`swarm_dispatcher.py:952`). | A relationship block added there reaches both without touching `swarm_dispatcher.py`. |
| S11 | `newsroom/weekly_run.py:154` refreshes season summaries at the start of each run. | The natural place to grade the previous week's picks into relationship updates. |
| S12 | The clean seed writes 199 accounts (`db/seed_clean_db.py:245` `seed_agent_accounts`, `newsroom/config/agent_accounts.json`); FORMER_PLAYER accounts are no longer seeded. | Relationship seeding runs right after accounts in the clean seed; `madden_2036` is never touched. |
| S13 | Latest migration (2026-11-02): `db/migrations/2027_01_17_persona_slider_band.sql`; `2027_01_16_personality_core.sql` added the `personality/` core (identity, aliases, audited profile changes), which says relationships will be a separate consumer/producer of verified events. | This plan's migration is `2027_01_18_writer_voice.sql` (recheck the next free date at build time). Writer relationships stay in `newsroom/relationships.py` and don't touch `personality/`. |
| S14 | Tests: `tests/test_social_desk.py`, `test_social_writer.py`, `test_social_publish.py`, `test_agent_memory.py`, `test_personas.py`, `test_personas_db.py`. `tools/social_model_compare.py` writes 20 sampled posts per model to `captures/social_compare/` (no test file). | Each unit extends its own test file. |

## 1. Scope

**In:**
- Rewritten instructions for every existing post kind (situation + intent, no menus), sampled post shapes, focus facts, a week-level repetition check with one rewrite.
- Six new kinds: weekly `PICKS`, league-wide `HOT_TAKE`, `AWARD_RACE`, `POWER_TAKE`, `QUESTION`, and preseason/midseason/deadline `SEASON_CALL`.
- `week_mix`: a news-driven, seeded weekly mix and total (80–130 posts), replacing fixed buckets and trims; die-hard reactions, beat notes and news kinds stay event-driven.
- League topics from data the league already stores (award leaders, hot seat, power rankings, playoff picture, record chases, contract years, rookies, injuries).
- Structured post output; winner and score picks and season calls stored and graded.
- `person_key` for accounts; memory reads across a person's accounts.
- Writer relationships: seeding, growth from takes, earning from graded picks and calls, decay, and a relationship block in post and article prompts.
- Quality report and prompt A/B, measured against the 2036 baseline (S5).

**Out (non-goals):**
- Relationships for GMs, players, coaches and owners, and writer↔writer feuds driven by replies (PERSONALITY_PLAN §4.7 owns them; this plan builds the writer→player/coach/team part and the table code they will reuse).
- Model choice and moving posts to a local model (V3 §10); prompts stay model-neutral and the plan keeps Gemini 3.8 Flash as today.
- Frontend changes beyond what renders today (posts are text; picks are posts). Quote-post cards, threads and pick leaderboards on the site are later UI work.
- Article prompt rewrites (`swarm_dispatcher.py`); articles only gain the relationship block through `build_persona_block`.
- The V3 dossier/Colab pipeline.

## 2. Design

### 2.1 Person key (memory and biases per person)

Migration `2027_01_18_writer_voice.sql` (also folded into `db/init_db.sql`):
- `ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS person_key VARCHAR(100)`; backfill `person_key = agent_id`, then `UPDATE … SET person_key = 'Skip_Bayless'` for both Skip accounts (`archetype IN ('SKIP_BAYLESS','SKIP_TWEET')`). Index on `person_key`.
- `newsroom/config/agent_accounts.json` entries gain `"person_key"` (seed writes it; default = `agent_id`).

`newsroom/relationships.py`:
- `person_key(cur, agent_id) -> str` and `person_agent_ids(cur, agent_id) -> list[str]` (all accounts sharing the key; just `[agent_id]` when the column is missing).

`newsroom/agent_memory.py`: `build_memory_block`, `fetch_predictions`, `agents_due_for_summary`, `summary_inputs` read with `agent_id = ANY(person_agent_ids)`; summaries are stored once per person under the person's first account (lowest `agent_memories.id`). Writes stay per account.

### 2.2 Writer relationships

Rows in `persona_relationships` with `persona_type = 'writer'`, `persona_id = person_key`, `target_type IN ('player','coach','team')`, `target_id` = the id as text. Tags used: `FAVOURITE`, `GRUDGE`, `OWN_TEAM`, `RIVAL`, `FORMER_TEAM`, `BELIEVER`, `DOUBTER`. One new column in the migration: `persona_relationships.evidence JSONB DEFAULT '[]'` (the last 5 events that moved trust: `{season, week, kind, delta, text}`), which the prompt block quotes.

`relationships.py` functions (all take a cursor, never commit):
- `seed_writer_relationships(cur, season: int, seed: str = "rel") -> int`. Deterministic per person (`sha256(seed + person_key)`):
  - Die-hard fans: own team +0.8 `OWN_TEAM`; one rival −0.6 `RIVAL` (same division, the rival with the most recent playoff or head-to-head loss if stored, else seeded pick); 1–2 own-team stars +0.5 `FAVOURITE`.
  - Bandwagon fans: the current best team by last season's record +0.6; nothing negative.
  - Beat reporters: own team +0.3 `OWN_TEAM` (professional distance); 2–3 of that team's top players and the head coach between −0.3 and +0.6 (some reporters have a doubter take on a local star).
  - National writers and pundits: 2–4 `FAVOURITE` (+0.4..+0.7) and 1–2 `GRUDGE` (−0.3..−0.6) among the top 60 players by OVR and the 32 head coaches, drawn with weights from the persona's traits (`CONTRARIAN`/`HOT_TAKER` draw more grudges and stars; `FRONT_RUNNER` favours winning teams; `ANALYTICS_BELIEVER` favours efficient over famous); `FORMER_TEAM_SLANT` (`social_desk.py:49`) adds a `FORMER_TEAM` +0.5.
  - Returns rows written. Idempotent: existing rows are left alone.
- `apply_take(cur, person_key, season, week, about: list[dict]) -> int`. `about` items are `{"type": "player"|"coach"|"team", "id": int, "lean": -1|0|1}` from the post's structured output or the article take extraction. `trust += GROW_STEP * lean` (`GROW_STEP = 0.04`), creating a row at 0 when missing; `|trust| ≥ 0.5` adds `FAVOURITE`/`GRUDGE`.
- `apply_graded(cur, season, week) -> int`, run once per weekly run for the previous week: for each graded pick or season call, the subject team or player of the pick gets `EARN_RIGHT = +0.03` when the writer backed it and it delivered, `EARN_BURNED = -0.06` when it let them down; a call against a subject that comes true adds `DOUBTER` +0 trust but evidence ("I said he'd fade"). Players' calls grade against `player_buzz` tiers (§2.4).
- `decay(cur, season) -> int` at season rollover: `trust *= DECAY = 0.8` for rows not touched that season, never crossing the seeded sign of `OWN_TEAM`/`RIVAL` rows.
- `relationship_block(cur, person_key, subjects: list[dict], limit: int = 4) -> str`: rows for the subjects of this post or article first, then the strongest |trust| rows, rendered as plain sentences ("You've been a Jalen Hurts believer since Week 3 and he keeps proving you right." / "You can't stand the Cowboys' front office."), with one evidence line each. Empty string when no rows or no table.

### 2.3 Prompt changes (posts)

`social_writer.py`:
- `KIND_INSTRUCTIONS` rewritten for every kind as two parts: **situation** (what happened, from whose point of view) and **intent** (what this post is for: break news, argue, react, call a shot), with no list of moods or moves. Facts limits stay in `SOCIAL_RULES`, which loses its own list of example moves.
- `POST_SHAPES = ("one_liner", "question", "fragmented_live", "contrast", "list_of_three", "callback", "prediction", "observation", "sarcasm", "plain_statement")`; `pick_shape(account, kind, recent_shapes: list[str], seed: str) -> str` samples by persona weights (`agent_personas.json` gains an optional `post_shapes` weight map per archetype), never repeating the account's last 3 shapes. The shape is one line in the prompt.
- `build_post_prompt(..., relationship_block="", shape=None)`: adds `YOUR BIASES` (the relationship block) and `SHAPE`; FACTS becomes `assignment["focus"]` (1–3 facts the planner chose) plus `assignment["facts"]` labelled "background, use at most one".
- Output contract: the model returns JSON `{"text": str, "about": [{"type", "name", "lean"}], "picks": [{"game": "AWAY @ HOME", "winner": "TEAM", "score": "27-24" | null, "confidence": "lock"|"lean"|"upset"}], "calls": [{"call_type", "subject", "detail"}]}`; `text` first. `parse_post_json(raw, assignment) -> dict` resolves names to ids against the assignment's facts (unknown names dropped), validates picks against `assignment["pick_games"]`, falls back to plain text when JSON fails. `clean_post` runs on `text` unchanged.
- `too_similar(text: str, corpus: list[str], threshold: float = 0.55) -> bool`: word-trigram Jaccard against the week's already written posts and the account's last 15 posts, plus an exact first-three-words match against posts of the same kind this week. `write_post` adds it to the conditions for its one rewrite.
- New kind instructions: `PICKS` (call this week's games you care about, any number from 1 to 4, at least one with a reason; lean/lock/upset), `HOT_TAKE` (an opinion about the league topic in `focus`, not a recap), `AWARD_RACE`, `POWER_TAKE` (who's real, who's a fraud, a top-five), `QUESTION` (ask the timeline something you'd argue about), `SEASON_CALL` (a prediction graded at season's end: MVP, OPOY, DPOY, ROY, Super Bowl winner, a playoff team, a coach fired).

### 2.4 Planner changes

`social_desk.py`:
- `week_mix(ctx, seed) -> dict[str, int]`: total = clamp(80 + 4·tier-1 buzz + 6·tier-2 buzz + 3·trades + 2·one-score games + 5·(playoff week), 80, 130) then kinds drawn from base weights (`social_desk.json` new `"mix_weights"`) multiplied by news signals (no buzz → fewer PERFORMANCE; a big trade week → more TRADE_*; late season → more AWARD_RACE and POWER_TAKE; postseason → more PICKS and SEASON_CALL) with seeded jitter (±25%) and per-kind floor/ceiling in config. Die-hard reactions (one per team that played), beat notes, shares and news kinds stay event-driven and count inside the total. `BUCKET_SIZES`, `TRIM_ORDER`, `TARGET_POSTS` and the angle lists (S2) are removed.
- `load_league_topics(cur, season, week) -> list[dict]`: each `{topic_id, kind_hint, subjects: [{type,id}], focus: [facts], heat}` from stored data only, before Week N: award leaders (season leaders and `coty_scores`), `coach_hot_seat`, stored power rankings (`newsroom/power_rankings.py` tables) and their movement, playoff picture and division races (`season_landscape`), record chases (existing `SEASON_RECORDS` logic), contract-year starters (`player_contracts`), rookie leaders, the week's injuries. Missing tables → no topics of that type (savepoint, as `load_moves` does).
- `plan_picks(ctx, acc, n_writers) -> list`: about 10–16 writers a week each get `pick_games` (this week's games, the slate's top excitement games first) and post one `PICKS`. Accounts least-used this season first; die-hard fans pick their own game.
- `SEASON_CALL`: preseason (Week 1 publication), midseason (Week 9) and the trade-deadline week, 6–10 writers each.
- Assignment gains `"focus"`, `"pick_games"`, `"subjects"` (for relationship lookup) and `"topic_id"`. Relationship-driven angle: when a writer has |trust| ≥ 0.5 with a subject of the post, the assignment's `angle` says so in one plain line.

### 2.5 Publishing, storage and grading

`social_publish.py`:
- Builds the relationship block (`relationships.relationship_block(cur, person_key, assignment["subjects"])`) and passes it with `pick_shape(...)` into `build_post_prompt`.
- After saving a post: `store_tweet_take` as now plus `relationships.apply_take(... about ...)`; picks into `agent_predictions` with `pick_type 'WINNER'` or `'SCORE'`, `article_id` = the post's `news_feed.id`, `label` = confidence; calls into `agent_season_calls`.

Migration `2027_01_18_writer_voice.sql` also:
- `agent_predictions`: `ADD COLUMN pick_type VARCHAR(8) NOT NULL DEFAULT 'SCORE'`, `ALTER score_a, score_b DROP NOT NULL`, `ADD COLUMN winner_team_id INT`. `agent_memory.grade_prediction` grades WINNER picks by winner only.
- `CREATE TABLE agent_season_calls (id SERIAL PK, agent_id VARCHAR(100) NOT NULL, person_key VARCHAR(100) NOT NULL, season INT NOT NULL, made_week INT NOT NULL, article_id INT REFERENCES news_feed(id) ON DELETE CASCADE, call_type VARCHAR(24) NOT NULL, subject_type VARCHAR(10), subject_id INT, detail TEXT, result VARCHAR(8) NOT NULL DEFAULT 'PENDING' CHECK (result IN ('PENDING','RIGHT','WRONG','VOID')), graded_week INT)`.
- `agent_memory.grade_season_calls(cur, season) -> int` grades from the `awards` table (MVP, OPOY, DPOY, ROY), the Super Bowl result, final standings and `coach_hot_seat` firings; run by `weekly_run` once the awards are stored (week 22/23) and for playoff-team calls after week 18.
- `build_memory_block` adds open season calls and the season's call record next to the pick record.
- `weekly_run.py`: after `refresh_season_summaries`, call `relationships.apply_graded(cur, season, week - 1)`; at week 1 of a new season `relationships.decay`.
- `db/seed_clean_db.py`: after `seed_agent_accounts`, `relationships.seed_writer_relationships(cur, season)`; report `writer_relationships`.
- `personas.build_persona_block(..., subjects=None)`: when `agent_id` and a cursor are given, appends `relationship_block` for articles too (S10).

### 2.6 Measuring quality

- `tools/social_quality_report.py --db <dsn> --season S [--weeks a-b] [--json out]`: per kind and overall, distinct two-word openers / posts, mean pairwise trigram overlap within a week, share of forward-looking posts (PICKS, SEASON_CALL, PREVIEW, HOT_TAKE plus a word match), share of posts citing 3+ numbers, kind mix, posts per account. Read-only connection. Baseline = `madden_2036` (S5).
- `tools/social_model_compare.py --prompts old|new|both`: the same 20 sampled assignments written with the old and new prompts side by side, for JR's blind read in `captures/social_compare/`.
- Targets (estimates to confirm with JR after the first run): distinct openers ≥ 85% for REACTION, forward-looking ≥ 30% of the week, posts citing 3+ numbers ≤ 15%.

### 2.7 Real-tweet audit (2026-11-02) and the style rules it sets

Read on X (JR's signed-in session, read only) during Monday Night Football Week 8 (Bears at Seahawks) and from Sunday's top posts on the Cowboys, Bengals, Jets and Chiefs: 371 posts, plus about 5 recent posts each from Schefter, Rapoport, Stephen A, Cowherd, Skip, Barnwell, Kimes, Yates, McAfee, Cimini, Ben Baby, J.J. Watt, Ochocinco and two team accounts. Measured over the 371: median 22 words (quartiles 12 and 39), 75% with no closing punctuation, 51% multi-line, 49% with any number but fan posts far fewer, 30% with a hashtag (mostly media accounts), 14% with an all-caps word, 7% questions, 5% profanity (fans only). Emoji are rendered as images and weren't counted. No real post is reproduced here; patterns are described.

What real posts do that ours don't:
- **Fans post about one moment, not the game.** Live: one play, one hit, one missed tackle, one call, one player ("can't tackle", "is cooked", "stands for Screens"). After the game: one feeling or one target (the owner, the coordinator, a player) in a few words. They almost never recite the final score and record; ours always do.
- **Short and unfinished.** Fragments, no end punctuation, lowercase or ALL CAPS on a name, slang and the odd swear, a one-word follow-up correcting their own typo.
- **Demands and fixes.** "Trade for a corner", "bench him", a made-up trade ("X receive: …, Y receive: …, I'd do that"), draft-position gallows humour after a loss.
- **Looking ahead is normal.** A game "brewing" in two weeks, someone "hurtling toward MVP", a team that "should be 7-0", playoff odds, score-prediction prompts.
- **Rivalry trolling.** Fans of one team rooting against another together; "how bad are the [rival]?"; joke fake news about a rival owner.
- **Label-first media accounts** (an aggregator style we don't have): a one-word label and colon ("Savage:", "Heartbreaking:", "BREAKING"), then the quote or claim; hype accounts in ALL CAPS with three or four team hashtags.
- **Stat accounts:** one striking stat with its source and almost no opinion; lists ("highest sack rate", "highest-graded players").
- **Beat writers:** quarter-by-quarter updates during games ("End 1Q: …" plus two lines of context), injury and IR news, one-line judgments ("brutal in the red zone, should have 45 points"), quote-posts of the insider with one local line.
- **Insiders:** flat sentences, "source says", `#Team` hashtags, often quoting a beat writer with five words on top.
- **Pundits and ex-players:** much of what they post is not football analysis (show plugs, other sports, their own lives, emoji-only); the football takes are very short ("Geno still rules", "WHAT A KICK").
- **Team accounts:** 2–5 word captions, holiday and gameday posts, no analysis.

Rules this sets (U2 builds them into `KIND_INSTRUCTIONS`, `POST_SHAPES` and a per-archetype `post_style`; U3 into the planner):
1. `agent_personas.json` gains `post_style` per archetype: `length` (words: fans 3–20, insiders 10–35, analysts 15–45), `numbers` (fans 0–1, analysts 1–2, stat accounts 1–3), `case` (fans may go lowercase or ALL CAPS on a name), `punctuation` ("often none"), `hashtags` (insiders and media 1–3 team tags; fans rarely), `profanity` (see Q5), `off_topic_share` (pundits and ex-players).
2. Fan reactions lose the facts packet's final score and record by default; the planner gives one `focus` (a key moment from the game script, a player's line, a coaching call) and the score only as background.
3. New planner kinds from the audit: `LIVE_MOMENT` (a fan or pundit on one play from the game's scoring summary or script, timestamped during the game), `QUARTER_UPDATE` (beat writers, games of their team), `TRADE_IDEA` (fans and pundits proposing a trade from team needs), `DRAFT_POSITION` (fans of losing teams). Folded into `week_mix`.
4. Quote-posts: `REPLY` gains a `quote` form (comment on top of another account's post, shown with the parent).
5. `SOCIAL_RULES` loses "speak like a real human sports fan" boilerplate and states the measured shape instead (most posts under 25 words, no closing punctuation needed, at most one number unless you are a stats account).
6. `social_quality_report` adds these metrics (median words per archetype, share without closing punctuation, numbers per post) and reports them next to the audit's figures.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Asking for JSON makes tweets stiffer. | Spike (below): 20 assignments written as plain text and as JSON with `text` first; JR reads both blind. | Two calls for kinds that carry picks or calls (text, then a tiny extraction call on the local model), plain text elsewhere. |
| R2 | Biases snowball (a writer ends up hating everyone or worshipping one player). | Unit tests on `apply_take` / `apply_graded` over a simulated season; report trust spread per person. | Lower `GROW_STEP`, cap rows per person (8 strongest), stronger decay. |
| R3 | League topics are thin on a fresh franchise (no power rankings, no awards history yet). | Run `load_league_topics` on `madden_test` week 3 and on `madden_2036` week 12. | Topic types degrade to the ones with data; the mix shifts to picks and reactions early in a season. |
| R4 | Picks and calls name teams or players the parser can't resolve. | `parse_post_json` tests with nicknames, abbreviations and typos. | Unresolved picks are dropped from grading; the post still publishes. |
| R5 | Repetition check rejects good posts in busy weeks. | Report how often `too_similar` triggers on the A/B run. | Raise the threshold or limit the check to same-kind posts. |
| R6 | Another migration takes `2027_01_15` first (V2 build). | Check `ls db/migrations` at build time. | Use the next free date. |

**Spike first (before wave 1, ~30 minutes, the orchestrator with JR):** write 20 posts from one 2036 week (`madden_2036`, read-only) with a hand-edited new instruction set, once as plain text and once as JSON output; JR picks blind. Settles R1 and whether the instruction style works before units build on it.

## 4. Tests (synthetic, no live DB)

- `tests/test_relationships.py` → seeding is deterministic and covers each archetype rule; shared-person accounts get one row set; `apply_take` moves trust and sets tags at ±0.5; `apply_graded` uses the right deltas; `decay` keeps `OWN_TEAM`/`RIVAL` signs; `relationship_block` puts subject rows first and quotes evidence; empty string without the table.
- `tests/test_agent_memory.py` → memory reads across a person's accounts; WINNER picks graded by winner; `grade_season_calls` against synthetic awards and standings; open calls appear in the memory block.
- `tests/test_social_writer.py` → no instruction contains a list of three or more alternative moods (regex on "or" chains); `pick_shape` never repeats the last 3; `parse_post_json` resolves picks and subjects and falls back to text; `too_similar` thresholds.
- `tests/test_social_desk.py` → `week_mix` total in 80–130, deterministic per seed, reacts to buzz and trade counts, respects floors and ceilings; `load_league_topics` uses only data before Week N; `plan_picks` assigns valid `pick_games`; old buckets and angle lists are gone.
- `tests/test_social_publish.py` → picks, calls and `about` stored from a fake writer's JSON; relationship block passed into the prompt.
- `tests/test_personas.py` → `build_persona_block(..., subjects=)` appends the relationship block.
- `tests/test_seed_clean_db.py` → the seed calls `seed_writer_relationships`; `person_key` present in `agent_accounts.json` and shared by Skip's accounts.
- `tests/test_social_quality_report.py` → metrics on a synthetic post list.

Run with `pytest -m "not integration" tests/test_relationships.py tests/test_agent_memory.py tests/test_social_writer.py tests/test_social_desk.py tests/test_social_publish.py tests/test_personas.py tests/test_seed_clean_db.py tests/test_social_quality_report.py`. No napper tests.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Person key, relationships and memory

- **Owns:** `db/migrations/2027_01_18_writer_voice.sql` (new), `db/init_db.sql`, `newsroom/relationships.py` (new), `newsroom/agent_memory.py`, `tests/test_relationships.py` (new), `tests/test_agent_memory.py`
- **Reads:** §2.1, §2.2, §2.5 (tables and grading); `newsroom/personas.py:275-350`; `newsroom/social_desk.py:49` (`FORMER_TEAM_SLANT`); `engines/gm_personas.py` (seeded stratification style)
- **Depends on:** none
- **Done when:** migration applies twice cleanly to `madden_test`; `init_db.sql` builds the same schema; relationship and memory functions exist with the §2.2/§2.5 signatures; tests pass.
- **Verify:** `pytest -m "not integration" tests/test_relationships.py tests/test_agent_memory.py` all pass.

### U2: Post writer: instructions, shapes, structured output

- **Owns:** `newsroom/social_writer.py`, `newsroom/config/agent_personas.json` (`post_shapes`, `post_style` per archetype, and two new archetypes `AGGREGATOR` and `STATS_DESK` for D7), `tests/test_social_writer.py`
- **Reads:** §2.3; the spike's chosen instruction style and output format; `newsroom/personas.py:734` (`render_persona`)
- **Depends on:** none
- **Done when:** every kind (old and new, including §2.7's `LIVE_MOMENT`, `QUARTER_UPDATE`, `TRADE_IDEA`, `DRAFT_POSITION` and the quote form of `REPLY`) has a situation+intent instruction with no mood menu; `post_style` from §2.7 rule 1 (with D6's swearing rule) is in every archetype and rendered into the prompt; `SOCIAL_RULES` follows §2.7 rule 5; `pick_shape`, `parse_post_json`, `too_similar` exist; `build_post_prompt` takes `relationship_block` and `shape`; tests pass.
- **Verify:** `pytest -m "not integration" tests/test_social_writer.py` all pass.

### U3: Planner: weekly mix, league topics, picks and calls

- **Owns:** `newsroom/social_desk.py`, `newsroom/config/social_desk.json`, `tests/test_social_desk.py`
- **Reads:** §2.4; `newsroom/season_landscape.py`; `newsroom/power_rankings.py`; `engines/coach_hot_seat.py` output tables
- **Depends on:** none
- **Done when:** `week_mix`, `load_league_topics`, `plan_picks`, season-call planning and §2.7's planner kinds (`LIVE_MOMENT` from the game script and scoring summaries, `QUARTER_UPDATE`, `TRADE_IDEA`, `DRAFT_POSITION`, quote replies, fan reactions with one `focus` and the score only as background) exist; the two D7 archetypes are drawn into the mix; assignments carry `focus`, `pick_games`, `subjects`, `topic_id`; fixed buckets and angle lists are removed; `python3 -m newsroom.social_desk --season 2035 --week 12` prints a plan on `madden_test`.
- **Verify:** `pytest -m "not integration" tests/test_social_desk.py` all pass.

### U4: Wiring: publish, prompts, weekly run, seed

- **Owns:** `newsroom/social_publish.py`, `newsroom/personas.py`, `newsroom/weekly_run.py`, `db/seed_clean_db.py`, `newsroom/config/agent_accounts.json`, `tests/test_social_publish.py`, `tests/test_personas.py`, `tests/test_seed_clean_db.py`
- **Reads:** §2.5; U1–U3's merged interfaces
- **Depends on:** U1, U2, U3
- **Done when:** `agent_accounts.json` has the two D7 accounts (original names and handles, `AGGREGATOR` and `STATS_DESK`, national) and `person_key` on every account; a dry-run week (`python3 -m newsroom.weekly_run --social-only --dry-run` on a scratch franchise database built from `init_db.sql` and the clean seed) plans and writes posts with picks and relationship blocks; picks, calls, takes and trust changes are stored in a non-dry run on the scratch database; the clean seed writes relationships; tests pass.
- **Verify:** `pytest -m "not integration" tests/test_social_publish.py tests/test_personas.py tests/test_seed_clean_db.py` all pass.

### U5: Quality report and prompt A/B

- **Owns:** `tools/social_quality_report.py` (new), `tools/social_model_compare.py`, `tests/test_social_quality_report.py` (new)
- **Reads:** §2.6; S5 baseline numbers
- **Depends on:** U2, U3
- **Done when:** the report runs read-only on `madden_2036` and reproduces the S5 baseline; `social_model_compare --prompts both` writes old/new pairs.
- **Verify:** `pytest -m "not integration" tests/test_social_quality_report.py` passes; `python3 -m tools.social_quality_report --db "host=localhost dbname=madden_2036 user=postgres password=postgres" --season 2036` prints REACTION 553 posts / 317 openers.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2, U3 | — (spike done) | Separate files; U2 and U3 build against the §2.3/§2.4 interfaces, U1 against §2.1/§2.2/§2.5 |
| 2 | U4, U5 | U1, U2, U3 | U4 wires all three; U5 needs the new prompts and planner. No shared files between U4 and U5 |

**File ownership check:**

| File | Unit |
|---|---|
| `db/init_db.sql` | U1 |
| `db/migrations/2027_01_18_writer_voice.sql` | U1 |
| `db/seed_clean_db.py` | U4 |
| `newsroom/agent_memory.py` | U1 |
| `newsroom/config/agent_accounts.json` | U4 |
| `newsroom/config/agent_personas.json` | U2 |
| `newsroom/config/social_desk.json` | U3 |
| `newsroom/personas.py` | U4 |
| `newsroom/relationships.py` | U1 |
| `newsroom/social_desk.py` | U3 |
| `newsroom/social_publish.py` | U4 |
| `newsroom/social_writer.py` | U2 |
| `newsroom/weekly_run.py` | U4 |
| `tests/test_agent_memory.py` | U1 |
| `tests/test_personas.py` | U4 |
| `tests/test_relationships.py` | U1 |
| `tests/test_seed_clean_db.py` | U4 |
| `tests/test_social_desk.py` | U3 |
| `tests/test_social_publish.py` | U4 |
| `tests/test_social_quality_report.py` | U5 |
| `tests/test_social_writer.py` | U2 |
| `tools/social_model_compare.py` | U5 |
| `tools/social_quality_report.py` | U5 |

No path appears twice.

## 7. Spec changes when built

- §3 (schema): `agent_memories.person_key`; `persona_relationships.evidence`; `agent_predictions.pick_type`, `winner_team_id`, nullable scores; new `agent_season_calls`.
- §3.3p: writer relationships are now written (seeded, grown, earned, decayed), keyed by person.
- §7.7: memory per person; winner picks; season calls and their record in the memory block; relationship updates in the weekly run.
- §7.8: replace the buckets and trims description with `week_mix`; new kinds; league topics; picks and calls; structured output; repetition check; angle lists removed.
- §7.11: post shapes; the relationship block in post and article prompts.
- §1.1 Repository Layout: `newsroom/relationships.py`, `tools/social_quality_report.py`, the migration; updated lines for `social_desk.py`, `social_writer.py`, `social_publish.py`, `agent_memory.py`, `personas.py`, `seed_clean_db.py`.
- Pointers for the owning plans, added when those files are free: PERSONALITY_PLAN §4.7 (writer relationships built here), V3 §2 (social family) and V2 §7.2 (picks as the CPU-era memory source).

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Weekly total range | 80–130 posts, centred on today's 100. |
| Q2 | How many writers post `PICKS` each week | 10–16, rotating so every national writer picks about every other week; die-hards pick their own game. |
| Q3 | Should writers' biases show in articles too, or only posts? | Both (through `build_persona_block`), since the point is a writer who is recognisably the same person everywhere. |
| Q4 | Run the spike before wave 1? | Done 2026-10-31 (see Status). |
| Q5 | Profanity: real fans swear (5% of the audited posts, fans only). Allow it? | Allow mild swearing for fan archetypes only, at a low rate; never slurs; national writers and team-adjacent accounts stay clean. |
| Q6 | Add the account types the audit found and we don't have: a label-first aggregator ("Savage:", "BREAKING") and a stats account? | Yes, two national accounts (one aggregator, one stats), seeded in `agent_accounts.json` with original names. |
| Q7 | `LIVE_MOMENT` and `QUARTER_UPDATE` posts are timestamped during the game but written after the save is read (the save has scoring summaries and the game script, not every play). OK? | Yes; they only use plays the save records, so nothing is invented. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U2 | 1 | worktree-agent-aa7acd0039e44ddbc | .claude/worktrees/agent-aa7acd0039e44ddbc | merged | 7bd180d |
| U1 | 1 | worktree-agent-a2bb0ffc77e49b047 | .claude/worktrees/agent-a2bb0ffc77e49b047 | merged (retry; first agent stopped at the base check) | fc804ca |
| U3 | 1 | worktree-agent-a795f4e0b460714a2 | .claude/worktrees/agent-a795f4e0b460714a2 | merged (reworked from `7e4adae`; trade-talk regressions preserved; 62 tests) | 108fd00 |

**As built (wave 1):**
- U3 re-applied all four trade-talk hunks by hand (`load_trades` fresh/older leaks, `Week.rumor_facts` named variant, `Accounts.insider(key)`, `plan_social_week` insider preference) and added three leak tests.
- U3 added the relationship-driven angle (`load_strong_relationships`, `ctx["strong_relationships"]`, one-line `angle` on assignments with a |trust| ≥ 0.5 subject).
- U3 added `--dsn` to the planner CLI; the plan's verify command now works verbatim. `BUZZ_ANGLES` is retained (not one of S2's removed lists).
- Integration fix on main (`b67495a`): `tests/test_draft_board_posts.py` and `tests/test_newsroom_trades.py` imported the removed `TARGET_POSTS`; they now compare the plan's counts to its own `mix`.
