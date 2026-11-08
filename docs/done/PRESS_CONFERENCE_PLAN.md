# Project Mu: Press Conferences Plan (V2.2)

**Status:** approved by JR (2026-11-03), with his changes to the redesign recorded in §8 (decisions taken); built and on main (2026-11-08), all eight units in four waves.
- As built (U7): the watcher derives the press week from the database (`MAX(week)` of COMPLETED games that season) rather than `save_week - 1`, because the save is made after Advance Week. `game_launch` has no CLI verb that runs judgments for declined pressers, so it spawns one detached `python3 -c` process (a constant code string; the ids travel as argv) that runs `close_effects` for the declined ids and then the team's beat wakeup directly. The press hook never stops Play Game.
- As built (U8): `ACTOR_ROLES` is `('COACH',)` (the presser is the head coach's), so a PRESS notice needs JR to control the coach persona, as the rest of the inbox does. Closing is done inside `scan` (the built protocol has no separate close hook), and it also closes on `FAILED` or a deleted conference row.
- As built (U6, deviation from §2.7): the drafted hand questions are persisted in `press_conferences.patience` JSONB (keys `drafts`, `round`, `picks`, `hand_counts`, `done`, `passes_in_a_row`, `questions_asked`, `questions_by_agent`, `follow_ups_in_a_row`, `wants_to_end`, `seq`), because the fixed schema has no draft column. That is what makes `pick_user` instant and lets a reopened LIVE presser keep its raised hands; `budget`/`spent`/`mood` are unchanged. The migration was applied twice cleanly to a throwaway `mu_scratch_press_u6` database (all CHECKs verified, then dropped); no `madden_*` database was touched.
- As built (U4): `state` and `conference` are plain dicts documented in `newsroom/press_agents.py`'s docstring; U6 builds them. The appraisal's declined-presser rule applies only when a treatment is supplied (the neutral fallback carries none), so a declined presser can fall back neutrally. `judgment_events` envelopes validate against `personality.relationship_events.validate_event`; U6 appends them through `flush_relationship_events`.
- As built (U5): `Conference` interprets `team`/`opponent` as abbreviations, `score` as a string from the subject's side ("24-17") and `result` as W/L/T, and adds an optional `hands` field so a reopened LIVE presser keeps its raised hands (the GET body in §2.11 has none). U6 should return those shapes.
- As built (U1): the corpus is 175 real head-coach pressers from the ASAP Sports team media-conference archives (2023-09-10 to 2026-01-12; 30 of 32 teams, 161 regular season and 14 postseason, 2,092 questions — not the 40 the plan asked for as a minimum). The cloud half of the latency spike was **not** measured: the worktree has no `.env`/`GEMINI_API_KEY`, and no numbers were invented. The local half was measured on Ollama `gemma4:12b`: one warm five-call round 5.3 s, ~74 s and 64 calls per presser extrapolated.
- As built (U1, deviation from §2.5): the corpus found that pressers after losses and blowout losses are *longer* than after wins (13.1 and 13.5 questions vs 10.9), with fewer follow-ups after a blowout (5.8% vs 14.6%), so the first-guess penalties would have shortened bad-loss pressers below what real rooms did. U4's constants are therefore: `BASE_PATIENCE` 14, win +1, tie 0, loss 0, blowout loss −1, losing streak beyond 2 −1/game, hot seat −2, temperament `(t − 5) × 0.5`, clamped `[6, 20]`. `MAX_QUESTIONS` **stays 25** (D1 names it as a safety rule) although the corpus proposed 28 (observed maximum 27); the cap binds on about the longest 1% of pressers. `MAX_FOLLOW_UPS_IN_A_ROW` stays 2.
- As built (U3): the team line also carries `pass_yards`/`rush_yards`, and transaction player names are seeded into `names` so the checker sees them. The checker additionally validates bare records ("we're 6-2"), division records, "held them to N points", "lost by N points" and averaging claims against the season averages. U6 must add the cast's attendee display names to `sheet["names"]` before freezing the sheet.
- As built (U2): playoff interest is `max(formula, round floor)`, read literally as a floor.
**Builds on:** [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §5 (ingest runs on the save made after Advance Week), §5.3.2 (interactive latency boundary), §6 V2.0 ground rules and V2.2; [`V3_STUDIO_PLAN.md`](../in_progress/V3_STUDIO_PLAN.md) §7 and its ownership table (V3 reads approved quotes); [`AGENT_WIRE_VOICE_PLAN.md`](../in_progress/AGENT_WIRE_VOICE_PLAN.md) U1 (person keys, writer relationships); [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) phases 1–3, the personality core, §4.7 and §5.1 (D8–D12); [`PERSONALITY_RELATIONSHIPS_PLAN.md`](../done/PERSONALITY_RELATIONSHIPS_PLAN.md) §0b (consumer decisions), §2.1 (`relationship_context`, `append_events`) and §2.2 (event envelope), which owns trust; [`FRONT_OFFICE_INBOX_PLAN.md`](../in_progress/FRONT_OFFICE_INBOX_PLAN.md) (V2.9, being revised in parallel; builds first); PROJECT_SPEC §2.1 (excitement and its projection), §2.14 (game scripts), §2.19 (hot seat), §4.6b (franchise API), §7.2 (beat reporting), §7.7 (agent memory), §7.11 (personas).

**Words used here.** An *agent* is a persona with an agentic interface: a writer, a coach, later a player, each with its own prompt, memory and personality, taking turns. The *subject* is the person at the podium (a head coach in this plan). A subject is either *user-controlled* (a persona JR controls; today that means the head coach of a team in the franchise registry's `user_teams`) or an *AI agent*.

**Goal:** Every head coach holds a postgame press conference every week, the way the real league does. Writer agents are each told who they are, given the box score, the game's story and the league's state, and told they are in a postgame press conference with Coach X. They raise their hands; the coach picks who asks; reporters ask, follow up and pick up on each other's questions. The coach answers: an AI coach as his own agent, JR's coach as JR, typing in the Game Center. The presser ends the way real ones do: the reporters run out of questions, or the coach has had enough (sooner after a bad loss, or for a short-tempered coach). Who shows up depends on how big the game looked beforehand: every team's beat reporters cover their own coach, and national reporters go to the week's big games and cover both coaches. The 31 AI pressers run in the background after the week is ingested; JR's own presser waits for him behind a button. Transcripts become the only source of head-coach quotes in that week's beat coverage. Every reporter remembers what was said, and judges for itself how the coach treated it: a thoughtful answer, a snippy one, being passed over again and again, or a presser he never showed up for. That judgment is recorded as relationship events for the relationships plan, which owns trust; it never touches a sim number.

**Delivers:**
- **Research:** a reference corpus of real recent NFL postgame press conferences. The derived question taxonomy and statistics are committed; the raw transcripts stay local. It is reference and test material only; no real transcript text goes into prompts (U1, D8).
- **Casting:** `newsroom/config/press_pool.json` (the national press pool with tiers) and `newsroom/press_cast.py` (pre-game interest and who attends which game).
- **Facts and checking:** `newsroom/press_facts.py` (the fact sheet every agent sees, and the line checker that guards numbers and names).
- **Agents:** `newsroom/press_agents.py` (reporter turns, the coach's turns including picking the next reporter, coach patience, and each reporter's end-of-presser appraisal, turned into relationship events).
- **Running pressers:** `newsroom/press_room.py` (the session engine, storage, approval, memory and relationships, the week runner and CLI). It comes with the migration `db/migrations/2027_01_20_press_conferences.sql` (tables `press_conferences`, `press_attendees`, `press_turns`, `press_hands`) and `server/press_api.py` (the live presser API under `/api/franchise/{abbr}/press*`).
- **Screen:** `Mu_Network/src/components/franchise/PressConferencePanel.tsx` and its types file, opened from a button on the Game Center.
- **Wiring:**
  - the watcher starts the week's pressers in the background;
  - Play Game counts an unstarted presser as declined;
  - beat briefs carry the transcript, with no invented head-coach quotes;
  - a PRESS section in the writers' memory;
  - the `get_press_conference` tool for beat writers;
  - a `PRESS` notice kind in the V2.9 inbox.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | The watcher runs load → engines → league office → national articles → mod pack in one ingest (`pipeline/watch_save.py:632` `ingest_snapshot`, articles `:690–694`), on the save made after Advance Week (V2_MASTER_PLAN §5). Every game of the week, JR's included, is complete in that save. | All 32 pressers for week N can start right after the ingest of week N. "Right after the game" for JR means once he has played, advanced, saved and quit, and the watcher has ingested. The national slate is written during the ingest, before any presser exists, and stays that way (D3). |
| S2 | The beat portfolio (Postgame Report Card, Matchup Primer, Player Spotlight, Season Progress Report) is written by an on-demand wakeup per team (`server/api.py:97` `maybe_start_team_wakeup` → `:71` `start_local_wakeup` → `newsroom/swarm_dispatcher.py:2263` `dispatch_team_local`), started when a team page or Team Hub is viewed. | The transcript reaches each team's beat coverage if the wakeup runs after that team's presser. For an AI team, the wakeup makes sure the presser has run first. For JR's team, it waits until his presser closes or is declined. |
| S3 | Beat briefs already get per-team blocks (`swarm_dispatcher.py:1387` `brief_for_agent["team_transactions"]`). The beat directive invites invented quotes ("Quote coaching staff sentiment", `:1162`; "The Press Conference / Quote Hook: Open with a candid quote from a coordinator or team captain", `:1166`). The writers' shared rule allows invented quotes as texture (`:1257`). | A `press_conference` block goes in the same way. The quoting rules change for every head coach: he is quoted only from his transcript. |
| S4 | Pre-game interest signals that don't know the result: `games.projected_excitement`, raw E projected before kickoff and kept for completed games (`engines/excitement_projection.py:1–20`); `display_excitement` (`engines/gotw_engine.py:77`); `rank_games_by_interest` = projected E + division bonus + playoff-race bonus (`newsroom/slot_targets.py:160`); `crowd_score` = popularity, form, opponent, division and primetime (`pipeline/mod_packs.py:106`), with the broadcast window from `broadcast_window` (`:90`). `team_seasons.popularity_index` is one row per season that the weekly engines rewrite, so after the ingest it already includes the result. | Casting uses only pre-game inputs: projected E, the broadcast window, division, the playoff round, the market constants, and the records from games before this week. It never uses `popularity_index` from the current season (R3). |
| S5 | Writer personas: 40 archetypes in `newsroom/config/agent_personas.json`. National candidates are ACHO, BARNWELL, BERRY, CHADIHA, CLARK, COWHERD, EISEN, ESPN, HERBSTREIT, JJ_WATT, JOYNER, KIMES, MCAFEE, MEL_KIPER, OCHOCINCO, ORLOVSKY, RAPOPORT, RG3, ROSENTHAL, SANDO, SCHEFTER, SHANNON, SIMMONS, SKIP_BAYLESS, STEPHEN_A, THE_ATHLETIC, WINGO and YATES; the rest are fans, label accounts, panels, fantasy and draft desks. Beat reporters are `agent_memories` rows with `archetype = 'LOCAL_BEAT_REPORTER'` and `assigned_team_id` (`swarm_dispatcher.py:2282`). | The pool is a curated config with tiers (U2), not every archetype. |
| S6 | No coach has a persona today. `persona_sliders.persona_type` allows `'coach'` (`db/init_db.sql:1663`), and the personality core supports role `coach` (`personality/core.py:14`), but neither is seeded or switched on in production (PROJECT_SPEC §7.11). Coaches have profiles: tenure, schemes, tendencies, hardware (`get_coach_profile`), and the hot seat (`engines/coach_hot_seat.py:47`). | An AI coach speaks from his profile and the situation, with a neutral temperament, until coach personalities are switched on (D5). His patience depends on the result, the streak and the hot seat now, and on temperament later. |
| S7 | `agent_memory_entries.kind` defaults to `'TAKE'` (`db/init_db.sql:2112`), and every memory read filters on `kind = 'TAKE'` (`newsroom/agent_memory.py:279`, `:285`, `:334`, `:350`). Relationships: `relationships._adjust` (`:386`) and `_already_applied` (`:449`) on `persona_relationships` with target types player, coach and team. | Press memories are `kind = 'PRESS'` rows, so the schema doesn't change and they don't leak into takes. This plan does not write `persona_relationships` or call `_adjust` (S15). |
| S15 | The relationships plan (`PERSONALITY_RELATIONSHIPS_PLAN.md`, not built) owns trust (§0b, JR 2026-11-03). Consumers record events in its envelope (§2.2: `source_event_id`, `kind`, `subject`, `target`, `actor`, `basis`, `confidence`, `visibility`, `attribution`, `payload`), append them through `personality/relationship_store.append_events(cur, events, *, expected_head, team_registry)` and read trust only through `relationship_context(state, *, target)`, which is neutral while its policy is off. A presser answer is a `meaningful_interaction` (`relationship_kind`, `outcome`, `significance`, `opportunity_ref`); being skipped, cut off or stood up is a `treatment` (`team_key`, `treatment_kind`, `outcome`, `significance`, `outcome_refs`). Trust is one-way: nothing reads it as mutual or adopts legacy tags. It reserves migration `2027_01_18_personality_relationships.sql`. | The reporters' appraisals become events in that envelope, kept in this plan's own tables until `append_events` exists, then appended. Prompts and casting read trust only through `relationship_context` (neutral before). This plan's migration stays `2027_01_20`. |
| S8 | Play Game: `pipeline/game_launch.py:115` `launch(team, season, week, dry_run)` is called by `POST /api/franchise/{abbr}/game/launch`; it refuses while moves are pending. | This is where an unstarted presser for JR's last game becomes DECLINED (JR's rule), and where the beat wakeup for his team gets started. |
| S9 | Models: `engines/llm_provider.generate_text(..., source, label, model, provider, thinking_level, response_mime_type, return_usage)` (`:314`), routed by `LLM_PROVIDER_<SOURCE>`/`<LABEL>` (`:71`) and logged to `llm_usage`. The repo's price table has `gemini-3.8-flash` at $0.15 per million input tokens and $0.60 per million output tokens (`estimate_cost`, `:422`). The nightly GPU queue (`pipeline/gpu_queue/`, §7.13) keeps local models off the GPU while Madden runs. | Compute sizing (§2.10) uses that table. Background pressers run while JR may be playing, so they use a cloud model (D4). |
| S10 | `games.completed_at` is not written by the save loader (no hits in `pipeline/load_save.py`). | Nothing here is timed from the game. Times come from presser rows and Play Game. |
| S11 | `pipeline/watch_save.py` has someone else's uncommitted changes, and `pipeline/weekly_freeze.py` is untracked work in progress. | The watcher edit (one detached spawn) is scheduled last and starts only once those changes are committed (R7). |
| S12 | The V2.9 inbox plan claims migration `2027_01_19`, owns `engines/inbox/`, auto-discovers kinds in `engines/inbox/kinds/`, reserves `KIND = "PRESS"` (counterpart `system`) for this plan, and edits `server/api.py`, `db/init_db.sql`, `pipeline/weekly_capture.py` and `FranchisePage.tsx`. Its reporter threads are one-on-one interviews; pressers are this plan's own multi-agent panel. | Migration `2027_01_20`. The inbox only gets a PRESS notice (one new file). Edits to `db/init_db.sql` and `server/api.py` wait for the inbox units that own them (§6). This plan never edits `weekly_capture.py` or `FranchisePage.tsx`. |
| S13 | User teams: `db/franchises.py` registry, active entry `user_teams`; `server/franchise_api.py:213` `user_teams()`, `:223` `require_user_team()`. | `user_teams` is the source of truth for which coaches JR controls. The presser code asks one function, `subject_controller`, so other personas JR controls (GMs, players) plug in later. |
| S14 | Tests use fake cursors and FastAPI `TestClient` (`tests/test_franchise_api.py`); `tests/test_agent_memory.py`, `tests/test_relationships.py` and `tests/test_local_portfolio.py` exist. | All new tests are synthetic with a fake model; nothing touches a live database. |

## 1. Scope

**In:**
- **Who and when:** a postgame presser for every head coach after every completed game: regular season and playoffs, all 32 teams, every week.
- **AI coaches:** their pressers run unattended in the background after the ingest, one week at a time, in parallel.
- **JR's coach:** his presser opens from a button on the Game Center. Reporters ask live, JR types his answers, then reviews and approves the transcript. If it hasn't been started by Play Game for the next game (or by the next ingest, if Play Game was never pressed), it is recorded as declined.
- **Agent turns:** reporter agents (raise a hand with a question, follow up, react to another reporter's question, pass) and the coach (opening statement, picking the next reporter from the raised hands, answers, closing). An AI coach picks as an agent; JR picks himself. Coach patience ends the presser. The only fixed rules are safety rules: the 25-question cap, at most 2 follow-ups in a row, and a fallback order when the coach agent fails.
- **Casting:** by pre-game interest. Each team's beat reporters cover their own coach; national reporters attend at most one game a week and cover both coaches there. The national pool and its tiers are a config file.
- **The fact sheet and the line checker:**
  - every agent gets the facts;
  - lines from reporters and AI coaches are checked, and a bad line is retried once, then dropped;
  - JR's answers only get warnings.
- **After the presser:**
  - transcripts are quoted in the team's beat coverage;
  - no invented head-coach quotes anywhere;
  - a PRESS memory for every reporter who attended;
  - each reporter's own appraisal of how it was treated (answers, being skipped or cut off, a declined presser), stored with its reasons in its memory and as relationship events for the relationships plan;
  - a tool for beat writers;
  - `approved_quotes` for V3.
- **The research corpus** of real postgame pressers, as reference and test material only.
- **Player pressers later:** the tables, cast and agent turn are written for any subject type (`coach` now; `player` later).
- **The inbox:** a `PRESS` notice when JR's presser is waiting.

**Out (non-goals):**
- **Player pressers.** The design leaves room for them (§2.12); building them is a later plan.
- **Any effect on the sim:**
  - answers never change ratings, morale, progression, contracts or popularity;
  - only relationship events are recorded, and the relationships plan decides what they do to trust, which only touches prompts.
- **Pressers in national articles** (D3). The national slate is written at ingest as today, before the week's pressers, and national writers don't quote pressers or call the press tool. No national article type fits: every one of them is written in that same ingest. Nationals who attended still remember the presser, and it can colour their opinions.
- **Agent Wire reactions to quotes.** That belongs to the Agent Wire voice plan, which owns the social files; a follow-up can feed `approved_quotes` into it.
- **Audio, video and images.** These are V3.
- **Anything in Madden's menus.**

## 2. Design

### 2.1 What "facts stay fixed" means, and how it works with agents

The engines decide every number: the score, the stats, the records, the standings, the injuries. A model writes words around those numbers and must never make up a new one. Here is what goes wrong without a guard. Your quarterback threw 2 interceptions, but a reporter agent asks "Coach, you threw 4 picks today, is he still your starter?" Or an AI coach says "we ran for 200 yards" when the box score has 87. Once that line is in a transcript, writers quote it, readers see it, and the league's story contradicts its own box score.

In the earlier draft, questions came from fixed templates and a model could only reword them. That doesn't fit agents that ask what they want. The new rule:
1. **Every agent gets the facts:** the fact sheet (§2.3) is in every prompt, with the instruction to use only numbers from it.
2. **Every line is checked** before it enters the transcript: `press_facts.check_line` looks at every number tied to a stat, every score, record and streak, and every person's name against the fact sheet.
3. **A failed line goes back once**, with the problem spelled out: "You said 4 interceptions; the box score has 2. Ask your question again." A line that fails twice is dropped. A reporter's turn passes to the next hand; an AI coach's answer falls back to its sentences that passed, or to a short non-answer ("We'll look at the tape on that one."), marked `source = 'FALLBACK'`.
4. **JR's own answers are his words:** they are checked and warned on ("You said 3 turnovers; the box score has 2"), never blocked. Writers are told the recorded figure.

Opinions, tone, and anything the data doesn't record are free: "Was that the worst half of football you've coached here?" passes the checker.

### 2.2 Timeline of a week

| When | What happens |
|---|---|
| JR plays week N, advances, saves, quits | Nothing yet. |
| Watcher ingests the post-advance save | As today: load, engines, league office, national slate (no presser quotes; D3), mod pack. Then the watcher spawns `python3 -m newsroom.press_room run-week --season S --week N` as a detached process and does not wait for it. |
| Background runner (estimated 5–10 min with a cloud model, §2.10) | Casts every game of week N. Runs every AI coach's presser to CLOSED, using `PRESS_WORKERS = 6` pressers at a time. For each user-controlled coach it creates a SCHEDULED presser (cast, facts, and the first round of hands drafted in advance, so JR can pick the first reporter at once). |
| JR opens `#franchise` | The Game Center shows **Postgame press conference: Week N vs OPP** with a **Go to the podium** button, and the inbox shows a PRESS notice. |
| JR takes the podium | A LIVE presser: hands go up, JR picks a reporter, the reporter asks, JR answers, and so on, until the reporters run out of questions or JR ends it. Then REVIEW (edit, on/off record), then **Approve**. The presser is CLOSED and JR's team's beat wakeup starts. |
| JR presses Play Game for week N+1 without having taken the podium | `game_launch` sets the presser to DECLINED and starts, in one detached process, the reporters' judgments of the decline (§2.8) and then the team's beat wakeup. A presser left in REVIEW is approved as it stands, since JR answered the questions (D7). |
| Fallback: the next ingest arrives with JR's presser still SCHEDULED | `press_room.expire_user_pressers` declines it the same way. |
| Any beat wakeup | `dispatch_team_local` calls `press_room.ensure_team_presser(team_id, season, week - 1)` first. For an AI team whose presser is missing (the runner failed or hasn't reached it), it runs that presser inline. For a user team still SCHEDULED, LIVE or REVIEW, `maybe_start_team_wakeup` doesn't start the wakeup at all. |

### 2.3 The fact sheet (`newsroom/press_facts.py`)

`fact_sheet(cur, game_id: int) -> dict` is built once per game and frozen in `press_conferences.facts` (both pressers of a game share it). It holds the following parts:
- `game`: the final, quarter scores, the game type, the week, home and away, and the broadcast window.
- `box`: the text of `newsroom/db_tools.get_game_box_score(game_id)` (it ends with the game script).
- `script`: `game_script_engine.script_summary`: shape, storyline, lead changes, largest lead, the winner's deficit, key moments.
- `teams[2]`, one for each team, with:
  - record before and after, streak, division standing and conference seed after the game;
  - the head coach (id, name, tenure, schemes, tendencies, and the hot seat line from `coach_hot_seat` when present);
  - the team line (yards, turnovers, takeaways, sacks; null when not recorded);
  - player lines for everyone with a recorded line, each with his season per-game averages from earlier games;
  - injuries new this week;
  - transactions this week (`roster_moves.new_this_week`);
  - records broken (`records_engine.find_records_broken_in_week`, this team).
- `league`: this week's other results (score lines), the top of the standings, and the playoff picture after the week (week ≥ 10).
- `names`: every person name the sheet contains (players, coaches, the reporters in the room), for the name check.
- `numbers`: a flat index of `(stat, subject, value)` triples built from all of the above, for the number check.

Unknown stays `null`, never 0.

The checker is `check_line(text: str, sheet: dict, speaker: str) -> list[dict]`. Each issue it returns is `{"kind": "NUMBER"|"NAME"|"EVENT", "span", "claim", "recorded"}`. It checks:
- scores (`N-M`, `N to M`);
- `N <stat>` phrases (yards, touchdowns/TDs, interceptions/picks, sacks, turnovers/giveaways, takeaways, catches/receptions, carries, field goals, points, wins, losses, straight wins/losses), tied to the nearest preceding name within 6 words, else to the team. Nothing is flagged when the recorded value is null;
- records (`N-M` after "record" or "start"), "first place" / "in the playoffs" against the standings;
- capitalised two- and three-word names not in `names` (exception: names in a short list of real public figures in the press pool, so a reporter can name himself);
- event attributions: "<name>'s touchdown / interception / fumble / sack" requires that line in his box score.

Ages, contract figures and historical claims are out of the checker's scope: the sheet doesn't hold them, and the prompt forbids numbers it doesn't hold.

### 2.4 Casting (`newsroom/press_cast.py`, `newsroom/config/press_pool.json`)

**Pre-game interest** is `pregame_interest(cur, game: dict) -> dict` and returns `{"interest": float, "inputs": {...}}`, using only what was known before kickoff. On a 0–100 scale:
- `0.45 × display_excitement(projected_excitement)` rescaled from 70–100 to 0–100, or 50 when missing;
- `+ 0.35 × market`: the two teams' Base Market Gravity B (`engines/market_constants.py`), as a 0–100 mean;
- `+ 0.20 × form`: the two teams' win share in games before this week, blended with last season's for the first 4 games, as in `mod_packs.form`;
- `+ PRIMETIME_BONUS` (8) for SNF, MNF or TNF (`mod_packs.broadcast_window`), `+ DIVISION_BONUS` (5), `+ RACE_BONUS` (5) from week 15 when both teams are at .500 or better before the game;
- for playoffs, the round's floor instead: Wild Card 70, Divisional 80, Conference 90, Super Bowl 100.

The weights are first guesses; U1's corpus and JR's eye set them. The pool and the tier counts below are approved (D6).

**Tiers and the national count:**

| Interest | Tier | Nationals at the game | Who |
|---|---|---|---|
| < 45 | `QUIET` | 0–1 | an `INSIDER`, 1 in 3 games (seeded) |
| 45–65 | `NOTABLE` | 1–2 | `INSIDER`, `REPORTER` |
| 65–80 | `BIG` | 2–4 | + `COLUMNIST` |
| ≥ 80, or playoffs | `MARQUEE` | 4–6 (Super Bowl: the whole pool that isn't excluded, up to 8) | + `TV` |

**The pool** is `press_pool.json`, compiled in U2 and reviewed by JR:

```json
{"tiers": {"INSIDER": ["SCHEFTER", "RAPOPORT"],
           "REPORTER": ["ROSENTHAL", "SANDO", "CHADIHA", "THE_ATHLETIC", "ESPN", "YATES"],
           "COLUMNIST": ["BARNWELL", "KIMES", "SIMMONS", "JOYNER", "BERRY"],
           "TV": ["STEPHEN_A", "SKIP_BAYLESS", "COWHERD", "EISEN", "WINGO", "MCAFEE", "ORLOVSKY", "SHANNON",
                  "CLARK", "RG3", "JJ_WATT", "OCHOCINCO", "HERBSTREIT", "ACHO"]},
 "excluded": ["AGGREGATOR", "STATS_DESK", "ROUNDTABLE_PANEL", "NFL_MEMES", "FANTASY_GURU", "DRAFT_SICKO",
              "MEL_KIPER", "DIE_HARD_FAN", "BANDWAGON_FAN", "FORMER_PLAYER", "SCHEFTER_TWEET", "SKIP_TWEET"],
 "weights": {"former_team_bonus": 2.0, "relationship_bonus": 0.0}}    // relationship_bonus stays 0 until relationship_context exists
```

The tier lists above are a starting proposal; U2 reads each persona's `voice` and records why each archetype sits where it does.

**Assignment:** `cast_week(cur, season: int, week: int) -> dict[game_id, Cast]`. Games are ranked by interest, highest first. Each game draws its national count from the tiers it allows: within a tier, people with a former-team slant toward either team are weighted up (and, once `relationship_context` exists, people whose context names a tie to either team, with `relationship_bonus`; neutral before), and the rest is a seeded draw (`random.Random(f"press:{season}:{week}:{game_id}")`). Each national attends at most one game a week (one person key, one game), as real reporters travel to one game. Beat reporters are every `LOCAL_BEAT_REPORTER` of each team; they attend only their own coach's presser and ask only about their own team. Nationals attend both coaches' pressers at their game. The result never enters casting.

`Cast = {"game_id", "interest", "tier", "inputs", "nationals": [Attendee], "beats": {team_id: [Attendee]}}`, where `Attendee = {"agent_id", "person_key", "archetype", "display_name", "handle", "role": "BEAT"|"NATIONAL", "pool_tier"}`.

### 2.5 The agents (`newsroom/press_agents.py`)

**The reporter's turn** is `reporter_turn(attendee, state, llm) -> dict`. Its prompt holds:
- the persona: `personas.render_persona(persona, "POST", tags={"GAME_REVIEW"}, seed=f"press:{conference_id}:{agent_id}")`;
- the memory block (`agent_memory.build_memory_block`);
- its relationship to the coach, only through `relationship_context` once the relationships plan is built (omitted, which is neutral, before);
- "You are at Coach X's postgame press conference after <the game>. You are a beat reporter for <team> and ask only about <team>" (or "a national reporter covering this game");
- the fact sheet, then the transcript so far;
- how many times it has raised its hand and been picked so far in this presser;
- the instruction to answer in JSON.

No real transcript text is in any prompt (D8). The instructions are written by hand; U1's corpus only tests them.

The reply is one action:
- `{"action": "ASK" | "FOLLOW_UP" | "REACT" | "PASS", "text": str, "about": [{"type": "player"|"coach"|"team"|"game", "id": int|null}], "tone": "neutral"|"pointed"|"light", "reply_to": turn_id|null}`
- `FOLLOW_UP`: only on the last answer, and only by the reporter who asked it.
- `REACT`: picks up another reporter's question or the coach's answer to it ("To Adam's point…").
- `PASS`: nothing left to ask.

The text must be at most 60 words, use only the sheet's numbers, and not repeat a question already asked.

**The coach agent's turn** is `coach_turn(subject, state, llm, kind) -> dict`, with `kind` one of `OPENING`, `ANSWER`, `CLOSING`. Its prompt holds:
- the coach's profile (name, tenure, schemes, tendencies, record, hot seat);
- the personality view when one exists (`personality.store` + `core.feature_view(profile, 'press')`), else a neutral line: "plain, professional, guarded about injuries and the game plan";
- the result and his mood, then the fact sheet and the transcript;
- hand-written guidance on how coaches talk at the podium (deflect, credit players, take blame, refuse injury details).

The reply is `{"text": str, "mood": 1..5, "wants_to_end": bool}`; `mood` 1 means irritated. Answers are at most 120 words.

**Who asks next: the coach picks** (D1). There is no moderator.
- **AI coach:** `coach_pick(subject, hands: list[dict], state, llm, coach_views: dict[str, dict] | None = None) -> {"agent_id": str, "reason": str}`. The coach sees:
  - the raised hands as names, outlets and roles, with a follow-up marked;
  - his own pick and skip history with each reporter this season (from `press_hands`: hands up, picked);
  - neutral guidance: a coach usually lets the local beat open, takes a fair spread of the room, and allows a follow-up.
  
  He does not see the drafted questions, as a real coach doesn't, and he gets no relationship input today. Trust is one-way: a reporter's view of the coach is not the coach's view of the reporter, and legacy tags are not read (D11). `coach_views` is the slot for the coach's own views of reporters. Once real coaches have reviewed profiles (personality plan D11) and the relationships plan serves coach → reporter context, `press_agents.coach_views(cur, coach, reporters)` fills it from `relationship_context`; today it returns `None`, and the prompt leaves that block out. The pick must be one of the raised hands; anything else is retried once, then the fallback order is used.
- **JR:** the panel shows the raised hands (names and roles, not the questions). JR clicks one; that reporter's question appears.
- **Safety rules (code, not choices):**
  - `MAX_QUESTIONS = 25`;
  - `MAX_FOLLOW_UPS_IN_A_ROW = 2`: a third follow-up from the same reporter isn't offered as a hand;
  - a reporter who passes twice in a row is done;
  - `fallback_pick(hands, state)` when the coach agent fails twice: a follow-up from the last asker first, then the reporter picked least so far, beats before nationals, ties seeded. It is used only on failure, and such a pick is marked `source = 'FALLBACK'` on the turn.

Every round records who had a hand up and who was picked (`press_hands`, §2.7), so being skipped is a fact the reporters' judgments can use.

**Patience** (AI coaches) starts as `patience_budget(subject, sheet) -> int`:
- `BASE_PATIENCE = 12` questions;
- `+3` for a win, `+1` for a tie, `−2` for a loss, `−4` for a loss by 17 or more;
- `−1` for each game of a losing streak beyond 2;
- `−2` on the hot seat;
- `±` the temperament slider (`(temperament − 5) × 0.6`, rounded) when the coach has a personality;
- clamped to `[MIN_PATIENCE = 5, MAX_PATIENCE = 20]`.

Each answered question costs 1, a `pointed` one 2, and a repeated topic 2. When patience reaches 0 or the coach wants out, the coach agent also decides how to end: one last question, or "That's all, thanks". The presser ends:
- when patience reaches 0: the coach agent gives a CLOSING line;
- when `wants_to_end` is true and patience is ≤ 3;
- when every reporter is done;
- or at `MAX_QUESTIONS = 25`.

A user-controlled subject has no budget: JR ends whenever he presses **That's all**.

The constants are first guesses; U1's corpus statistics (questions per presser, follow-up rate, length after wins and losses) set them before U4 is built.

**The reporter's appraisal** is `reporter_judgment(attendee, conference, llm) -> dict` (D2, D10). It is one call per reporter after the presser closes or is declined. The reporter agent sees:
- its persona and its memory, plus its relationship to the coach through `relationship_context` once that exists (neutral before);
- its own exchanges (question, answer, NO_COMMENT, follow-ups);
- how many times it raised its hand and was picked, compared with the others in the room, and whether it was left with a hand up when the coach walked off;
- its pick and skip history with this coach this season (`skip_history(cur, person_key, coach_id, season)` from `press_hands`);
- or, for a DECLINED presser, only the fact that the coach didn't show.

It answers in structured JSON, in the relationships plan's terms (§2.2), with no trust numbers:
```
{"exchanges": [{"turn_id": int,
                "relationship_kind": "support"|"collaboration"|"conflict",
                "outcome": "positive"|"negative"|"mixed",
                "significance": "routine"|"meaningful",
                "reason": str}],                                   // one per answer it got
 "treatment": {"treatment_kind": "disrespect"|"dismissal"|"recognition",
               "outcome": "positive"|"negative"|"mixed",
               "significance": "routine"|"meaningful",
               "reason": str} | null,                              // skipped, cut off, picked first, or the decline
 "memory": str}                                                    // first person, at most 60 words
```

Validation (`validate_judgment(raw, attendee, conference) -> dict`):
- the JSON parses against that shape, with the enums above. Otherwise it is retried once, then becomes the neutral appraisal (`exchanges: []`, `treatment: null`, memory built from the transcript);
- `turn_id`s must be this reporter's own exchanges; others are dropped;
- a NO_COMMENT exchange can't be `positive`;
- `treatment` must match what happened:
  - `recognition` needs the reporter picked at least once and no hand left up;
  - `disrespect` or `dismissal` needs a skip, a cut-off or a decline;
  - a declined presser allows only `dismissal` or `disrespect`;
- each reason must be non-empty and at most 30 words;
- the memory must pass `press_facts.check_line` (no invented numbers or names); a failing memory is replaced by the built one.

No trust number is computed here (D10). The bounds this plan proposed (±0.03 per answer, `[−0.03, 0]` for a skip or cut-off, `[−0.05, 0]` for a declined presser, ±0.08 per presser) are suggested starting values for the relationships plan's reviewed policy, recorded in its §0b.

**Events.** `judgment_events(conference, attendee, appraisal, universe_id) -> list[dict]` builds envelopes (relationships plan §2.2):
- `source_kind: "press_conference"`;
- `source_event_id`: `f"press:{conference_id}:turn:{turn_id}"` for each exchange, `f"press:{conference_id}:treatment:{agent_id}"` for a treatment, `f"press:{conference_id}:declined"` for a decline (one occurrence, faceted per reporter);
- `kind`: `meaningful_interaction` for an exchange, `treatment` for a treatment;
- `subject`: the reporter (person key); `target` and `actor`: the coach; `attribution.responsible`: the coach;
- `occurred_at`: the season and the game's week; `basis: "verified_simulation"`, `confidence: "medium"`, `visibility: "public"`;
- `source_refs: [{"namespace": "press_turns", "key": turn_id}]` or `press_conferences`;
- `payload`:
  - for an exchange: the appraisal fields, `opportunity_ref` (the turn) and `reason`;
  - for a treatment: `team_key` (the coach's team), `treatment_kind`, `outcome`, `significance`, `outcome_refs` (the `press_hands` rounds) and `reason`.

Person and team refs use the relationships plan's `PersonRef` / `TeamRef` as built.

### 2.6 The session engine (`newsroom/press_room.py`)

`run_session(cur_factory, conference_id, llm, user_answer=None) -> dict` drives one presser as a state machine and persists every turn.

The loop:
1. **Open:** the coach's OPENING statement (an AI coach; JR's opening statement is optional).
2. **Round:** in parallel, `reporter_turn` for every attendee who isn't done (`concurrent.futures`, one call each); validate each hand (§2.1: one retry, then drop); record the hands in `press_hands`. The coach picks: `coach_pick` for an AI coach, or JR through the API (a user session returns `{"awaiting_pick": [hands]}`). Store the QUESTION turn.
3. **Answer:** an AI coach answers via `coach_turn` (validated, one retry, fallback). A user subject's session returns `{"awaiting_answer": turn_id}` and resumes when JR's answer arrives through the API.
4. Update patience and the done flags, and check the end conditions. Repeat from step 2, or CLOSING.

**Speed for JR:** hands drafted in the round before stay valid when the answer didn't touch their `about` subject, so only the affected reporters and the asker are polled again (`PRESS_REPOLL = "changed"`; `"all"` polls everyone). With a cloud model the wait between JR's answer and the next raised hands is about one parallel call, an estimated 3–6 s (§5.3.2; R2). Picking is instant: the picked reporter's question was drafted with its hand.

Lifecycle functions (each takes a cursor and never commits; the CLI and API commit):
- `schedule_week(cur, season, week, llm) -> dict`: casts the week and inserts the conferences: AI subjects as SCHEDULED for the runner, user subjects as SCHEDULED with the first round of hands drafted in advance.
- `run_week(conn_info, season, week, workers=PRESS_WORKERS, llm=None, teams=None) -> dict`: idempotent; skips CLOSED conferences and runs the rest; one failing presser is logged as FAILED (it gets one retry on the next call) and doesn't stop the others. Returns `{"closed", "failed", "scheduled_user", "calls", "input_tokens", "output_tokens", "seconds"}`.
- `ensure_team_presser(conn_info, team_id, season, week) -> str`: the team's status, running an AI presser inline if missing or FAILED.
- `start_user(cur, conference_id)`, `pick_user(cur, conference_id, agent_id)`, `answer_user(cur, conference_id, turn_id, text: str | None, no_comment: bool)`, `end_user(cur, conference_id)` (to REVIEW), `edit_user(cur, conference_id, turn_id, text=None, on_record=None)`, `approve(cur, conference_id)` (to CLOSED).
- `decline(cur, conference_id, reason: "PLAY_GAME"|"NEXT_INGEST")`: SCHEDULED or LIVE becomes DECLINED; REVIEW is approved as it stands.
- `decline_open_for_launch(cur, team_id, season) -> list[int]`: called by `game_launch`.
- `expire_user_pressers(cur, season, week)`: called by `run_week` for older weeks.
- `close_effects(conn_info, conference_id, llm) -> dict`: runs once on CLOSED or DECLINED (§2.8). It makes model calls (the judgments), so it runs in the runner, in the API's background thread after approval, or in the detached process started by Play Game. It never runs inside a request JR is waiting on.
- `subject_controller(cur, team_id, subject_type="coach", subject_id=None) -> "USER"|"AGENT"`: USER when the team is in the registry's `user_teams` (an empty list means none; the press room never treats every team as JR's). This is the one place to extend when JR controls other personas.
- `approved_quotes(cur, season, week, team_id=None) -> list[dict]`: `[{conference_id, game_id, team_id, subject_type, subject_id, subject_name, controller, week, agent_id, question, answer, flags}]`. It returns ANSWER turns of CLOSED conferences only, on the record, joined to their question. This is the stable V3 interface.
- `brief_block(cur, team_id, season, week) -> dict | None` and `declined_note(...)` (§2.9).
- `get_press_conference(team_identifier, season=0, week=0) -> str`: the beat writers' tool (for example, for a Season Progress Report looking back over several pressers), cut by week like the other tools. It is not offered to national writers (D3).

CLI: `python3 -m newsroom.press_room run-week --season S --week W [--workers 6] [--team ABBR] [--model M] [--dry-run]` (a dry run prints the casts and the prompts of the first round only, and makes no model calls), `show --conference ID`, `wakeup --team ABBR --season S --week W` (the beat wakeup, used by `game_launch`), and `cast --season S --week W` (casts only).

### 2.7 Tables (`db/migrations/2027_01_20_press_conferences.sql`, mirrored in `db/init_db.sql`)

```sql
CREATE TABLE IF NOT EXISTS press_conferences (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT NOT NULL,                                -- the game's week
    game_id INT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    subject_type VARCHAR(10) NOT NULL DEFAULT 'coach' CHECK (subject_type IN ('coach', 'player')),
    subject_id INT,                                   -- coaches.id now; players.id later
    subject_name VARCHAR(255) NOT NULL,
    controller VARCHAR(5) NOT NULL CHECK (controller IN ('USER', 'AGENT')),
    status VARCHAR(10) NOT NULL DEFAULT 'SCHEDULED'
        CHECK (status IN ('SCHEDULED', 'LIVE', 'REVIEW', 'CLOSED', 'DECLINED', 'FAILED')),
    end_reason VARCHAR(16) CHECK (end_reason IN ('RAN_OUT', 'SUBJECT_ENDED', 'PATIENCE', 'CAP', 'DECLINED')),
    interest REAL, tier VARCHAR(10), cast_inputs JSONB,
    facts JSONB NOT NULL,                             -- press_facts.fact_sheet, frozen
    patience JSONB,                                   -- {"budget", "spent", "mood"}
    model VARCHAR(100), calls INT NOT NULL DEFAULT 0, input_tokens INT NOT NULL DEFAULT 0, output_tokens INT NOT NULL DEFAULT 0,
    effects_applied BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(), started_at TIMESTAMP, closed_at TIMESTAMP,
    UNIQUE (game_id, team_id, subject_type, subject_id)
);
CREATE INDEX IF NOT EXISTS idx_press_conferences_week ON press_conferences (season, week, status);

CREATE TABLE IF NOT EXISTS press_attendees (
    conference_id INT NOT NULL REFERENCES press_conferences(id) ON DELETE CASCADE,
    agent_id VARCHAR(100) NOT NULL,                   -- agent_memories.agent_id
    person_key VARCHAR(100) NOT NULL,
    role VARCHAR(8) NOT NULL CHECK (role IN ('BEAT', 'NATIONAL')),
    pool_tier VARCHAR(10),
    questions INT NOT NULL DEFAULT 0, passes INT NOT NULL DEFAULT 0, done BOOLEAN NOT NULL DEFAULT FALSE,
    hands INT NOT NULL DEFAULT 0, picked INT NOT NULL DEFAULT 0,       -- rounds with a hand up / times picked
    judgment JSONB,                                   -- validated reporter_judgment output
    PRIMARY KEY (conference_id, agent_id)
);

CREATE TABLE IF NOT EXISTS press_hands (                -- who had a hand up each round, and who was picked
    conference_id INT NOT NULL REFERENCES press_conferences(id) ON DELETE CASCADE,
    round INT NOT NULL,
    agent_id VARCHAR(100) NOT NULL,
    picked BOOLEAN NOT NULL DEFAULT FALSE,
    pick_source VARCHAR(8) CHECK (pick_source IN ('AGENT', 'USER', 'FALLBACK')),   -- set on the picked row
    PRIMARY KEY (conference_id, round, agent_id)
);

CREATE TABLE IF NOT EXISTS press_turns (
    id SERIAL PRIMARY KEY,
    conference_id INT NOT NULL REFERENCES press_conferences(id) ON DELETE CASCADE,
    seq INT NOT NULL,
    speaker VARCHAR(10) NOT NULL CHECK (speaker IN ('REPORTER', 'SUBJECT')),
    agent_id VARCHAR(100),                            -- reporters only
    kind VARCHAR(10) NOT NULL CHECK (kind IN ('OPENING', 'QUESTION', 'FOLLOW_UP', 'REACT', 'ANSWER', 'NO_COMMENT', 'CLOSING', 'NOTE')),
    reply_to INT REFERENCES press_turns(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    about JSONB NOT NULL DEFAULT '[]'::jsonb,
    tone VARCHAR(8),
    source VARCHAR(8) NOT NULL CHECK (source IN ('AGENT', 'USER', 'FALLBACK', 'ENGINE')),
    issues JSONB NOT NULL DEFAULT '[]'::jsonb,        -- check_line output (kept for audit; warnings on USER turns)
    retries SMALLINT NOT NULL DEFAULT 0,
    on_record BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (conference_id, seq)
);
```

Every reader guards with `to_regclass('press_conferences')`. Dropped lines are not stored, apart from a count in `patience`.

### 2.8 Memory and relationships (`close_effects`)

`close_effects` runs once per conference (`effects_applied`). For a CLOSED presser, each attendee:
1. **Judges the presser** with `reporter_judgment` (§2.5), validated and bounded. The result is stored in `press_attendees.judgment`.
2. **Records relationship events** (D10): `judgment_events` builds the envelopes, which are stored with the appraisal as `press_attendees.judgment = {"appraisal": {...}, "events": [...], "appended": false}`. `flush_relationship_events(cur, conference_id) -> int` imports `personality.relationship_store`. When that module doesn't exist yet (the relationships plan isn't built), it returns 0 and the events wait. When it does, it calls `append_events(cur, pending, expected_head=read_head(cur, universe_id), team_registry=...)` and marks them `appended`. `run_week` calls it for every waiting conference, so events recorded before the relationships plan lands are appended later, in order. Nothing here writes `persona_relationships`.
3. **Gets one memory row** in `agent_memory_entries`, `kind = 'PRESS'`:
   - `week` is the game's week, so it is visible from the next publication week;
   - `team_id` is the conference's team;
   - `player_id` is the subject of its first question when that is a player;
   - the text (at most 600 characters) is the facts of its exchanges plus its judgment's memory and reasons. For example: "Week 5, Coach Whitfield's presser after the 24-17 loss to Atlanta. You asked about McMillan's two catches; he said '…'. You had your hand up six times and he called on you once; he took four from the Charlotte Observer. You came away feeling frozen out."

For a DECLINED presser, every reporter in the cast is still appraised (a `treatment` only) and gets a PRESS memory row ("Coach Whitfield didn't take questions after the Week 5 loss." plus its appraisal); the event's `source_event_id` is `f"press:{conference_id}:declined"`.

JR's pressers are judged the same way: JR's picks, skips, answers and "no comment"s are what the reporters react to. Nothing here moves any sim number (D2), and trust is decided only by the relationships plan's policy (D10).

The memory block (`agent_memory.build_memory_block`) gets "YOUR RECENT PRESS CONFERENCES (you were in the room):" with the person's newest `PRESS_SHOWN = 3` PRESS rows before the publication week. The TAKE queries don't change.

### 2.9 Quotes in coverage

- **Beat briefs** (`swarm_dispatcher.py`, beside `team_transactions`): `brief_for_agent["press_conference"] = press_room.brief_block(tcur, assigned_team_id, season, week)` for the team's CLOSED presser of its last game. The block looks like this:
  ```
  {"subject": "Marcus Whitfield", "game": "Week 5 vs Atlanta, lost 17-24", "ended": "PATIENCE",
   "exchanges": [{"reporter": "@handle", "question": str, "answer": str}],     # on record, in order
   "no_comment": [str], "corrections": ["He said 3 turnovers; the box score has 2."],
   "reporting_guidance": "Quote the head coach only from these exchanges, word for word. ..."}
  ```
  `brief_for_agent["press_conference_note"] = declined_note(...)` covers a DECLINED presser: "Coach X did not take questions after the game; do not quote him on it."
- **Quoting rules** (the beat directive at `:1162–1166` and the shared rule at `:1257`): "A head coach is quoted only from his press conference transcript, word for word (`press_conference` in your brief, or `get_press_conference`). If there is none, do not put words in his mouth. Coordinators, players and team captains may be quoted as before." This applies to every head coach, AI or JR.
- **National slate** (D3): it is written at ingest as today, before the week's pressers. National writers don't quote pressers and don't get the tool. The one change for them is the shared rule's head-coach line: they don't invent words for a head coach. Nationals who attended a presser carry it in memory (the PRESS section), which may colour their opinions.
- **The tool:** `get_press_conference` is registered in the dispatcher's tool list and named only in the beat research directive.

### 2.10 Compute (estimates, measured in the spike)

Per AI presser: about 4 reporters (2 beats and 0–6 nationals, averaging about 2), about 12 questions. Each round polls the reporters with hands up (about 3 calls), makes 1 pick call (a short prompt of about 1,500 tokens) and 1 answer, plus about 10% retries. One judgment call per reporter comes at the end. That is about 65–80 calls. Each reporter or answer call is about 4,000 input tokens (persona and memory 1,000, fact sheet 2,000, transcript 1,000) and about 150 output tokens.

| | Per presser | Per week (32) | Per season (≈ 18 regular-season weeks × 32 + 12 playoff pressers) |
|---|---|---|---|
| Calls | ~75 | ~2,400 | ~44,000 |
| Input tokens | ~280k | ~9M | ~165M |
| Output tokens (+ low thinking) | ~12–25k | ~0.4–0.8M | ~7–14M |
| Cost, `gemini-3.8-flash` at the repo's table | ~$0.05–0.06 | ~$1.5–2 | ~$30–35 |
| Wall time, cloud, 6 pressers at a time | ~1.5–2 min | ~8–12 min | — |
| Wall time, local 14B on the 5070 Ti (estimate) | ~5–8 min | ~3–4 h, only with Madden closed | — |

Batching:
- the runner works one week at a time, `PRESS_WORKERS = 6` pressers in parallel and up to 6 calls in parallel inside a round;
- the fact sheet is the first, identical block of every prompt in a presser, so provider prompt caching can apply (not relied on);
- dry runs make no calls.

A local model fits only the nightly GPU queue (§7.13), since background pressers may run while JR is in Madden. So the pressers use a cloud model (D4): `LLM_PROVIDER_PRESS` unset (Gemini), with `PRESS_MODEL` to override it.

### 2.11 API (`server/press_api.py`, `APIRouter(prefix="/api/franchise")`) and the panel

| Method and path | Body | Returns | Errors |
|---|---|---|---|
| `GET /{abbr}/press` | — | `{conference: Conference\|null, opponent_presser: {conference_id, status}\|null}`: the team's presser for its last game | 404 |
| `POST /{abbr}/press/{id}/start` | `{opening?: str}` | `{conference, hands: [Hand]}`: the first raised hands (drafted in advance) | 403 not user-controlled, 409 not SCHEDULED |
| `POST /{abbr}/press/{id}/pick` | `{agent_id}` | `{question: Turn}` | 400 not a raised hand, 403, 409 |
| `POST /{abbr}/press/{id}/answer` | `{turn_id, answer?: str, no_comment?: bool}` | `{answer: Turn, hands: [Hand], ended: bool, end_reason}` (the next raised hands, or the end) | 400, 403, 404, 409 |
| `POST /{abbr}/press/{id}/end` | `{}` | `Conference` (REVIEW) | 403, 409 |
| `PUT /{abbr}/press/{id}/turns/{turn_id}` | `{answer?, on_record?}` | `Turn` | 403, 409 not REVIEW or not his turn |
| `POST /{abbr}/press/{id}/approve` | `{}` | `{conference, effects_started, wakeup_started}` (judgments run in a background thread, then the wakeup) | 403, 409 |
| `GET /{abbr}/press/history?season=&week=` | — | `{conferences: [Conference]}` (any team's closed pressers; to read AI coaches' too) | 404 |

The shapes are fixed for U5 (`Mu_Network/src/types/press.ts`):
- `Hand {agent_id, display_name, handle, role, pool_tier, picked_so_far: int, follow_up: bool}` (no question text)
- `Turn {id, seq, speaker, kind, reporter: {agent_id, display_name, handle, role, pool_tier}|null, text, reply_to, source, issues, on_record}`
- `Conference {id, season, week, game_id, team, opponent, result, score, subject_name, controller, status, end_reason, tier, attendees: [...], turns: [Turn]}`

The answer endpoint runs the next round inside the request: one parallel round of reporter calls, which is the latency JR feels. Picks are instant.

**The panel** (`PressConferencePanel.tsx`, opened from a **Go to the podium** button the Game Center shows while JR's presser is SCHEDULED, LIVE or REVIEW):
- the room: attendees as a row of name cards, with a tier badge on each national;
- the transcript as it grows;
- the raised hands as clickable name cards (a follow-up hand is marked), with a count of how often each reporter has been picked;
- the picked reporter's question;
- a textarea with **Answer**, **No comment** and **That's all, thanks**;
- a "hands going up…" state while the next round runs;
- a review screen with editable answers, on-record toggles and checker warnings, then **Approve and publish**;
- links to the other team's presser and the week's other pressers (read-only, from `/history`).

### 2.12 Room for player pressers

Nothing in the design is specific to coaches:
- `subject_type` and `subject_id` run through the tables, the cast, `subject_controller` and `coach_turn` (to be generalised as `subject_turn` with a `PlayerProfile`);
- beats would attend the players' pressers of their own team, nationals those of their game.

Player pressers would add `select_player_subjects(sheet, team_id, n=3)` (the game's top performers and anyone the story centres on) and a player voice from the personality core. They would multiply compute about 3 times (§2.10), which is why they wait for their own plan.

### 2.13 Inbox notice (`engines/inbox/kinds/press.py`)

`KIND = "PRESS"`, `COUNTERPART_TYPE = "system"`. `scan` creates one notice per user-controlled presser that is SCHEDULED, LIVE or REVIEW (`source_ref = f"press:{conference_id}"`, `link_table = 'press_conferences'`), and closes it once the presser is CLOSED or DECLINED. Its option is `OPEN_PRESSER` (the UI goes to the Game Center); `start` raises 400. The kind is written against V2.9's built `Kind` protocol and is a single new file.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Agent pressers read as generic ("Talk about the win, Coach") or all alike. | Compare with U1's corpus statistics (question types, length, follow-up rate, share naming a player) on a dry week of 32 pressers (`tools/llm_compare.py` for the V2.0 quality gate). | Tune the hand-written instructions and constants. Give each reporter's persona a "what you care about" line from its views. |
| R2 | Latency for JR between answer and next question. | The spike measures one parallel round (cloud and local). | Use the hands drafted in advance (`PRESS_REPOLL = "changed"`) and a shorter context (the fact sheet without other games). |
| R3 | Result leakage into casting (a post-game popularity or E). | U2 tests feed a finished game and assert that the cast is the same as for the unplayed game. | Only the inputs listed in §2.4. |
| R12 | Appraisals drift: agents call everything negative, or everything positive and meaningful. | U4 tests with scripted good and bad answers; after the first live weeks, a per-week count of outcomes and significance by reporter and coach from `press_attendees.judgment`. | Tighten the appraisal prompt and validation here; how much an appraisal moves trust is the relationships plan's policy (D10). |
| R14 | The relationships plan's envelope or `append_events` changes before it is built, or isn't built for a long time. | U6 reads its current §2.2 when it starts; `flush_relationship_events` is guarded. | Events wait in `press_attendees.judgment` in the agreed shape; a small mapping change at append time. Prompts stay neutral meanwhile. |
| R13 | An AI coach picks the same favourite every time, so pressers become two-person shows. | Read a dry week; count picks per reporter. | The fallback rules stay safety-only (D1). If it reads badly, give the coach agent its own pick counts in the prompt (already there), and JR can decide whether to add a soft rule. |
| R4 | The checker misses an invented fact (a wrong role, "your third straight loss"), or flags a fine line. | U3's adversarial tests; a sample of transcripts read by JR in the first live week. | Add patterns; record dropped-line counts per presser and watch them. |
| R5 | Compute or cost grows (more reporters, retries). | `run_week` returns calls and tokens; `llm_usage` holds them per label. | `PRESS_MAX_NATIONALS`, `MAX_QUESTIONS` and `PRESS_WORKERS` are constants; the cap per AI presser can be lowered. |
| R6 | **Shared files with sibling plans** (V2.9 inbox, V2.10 scouting, V2.11 trades): `server/api.py`, `db/init_db.sql`, `newsroom/swarm_dispatcher.py`, `newsroom/agent_memory.py`, `GameCenterModule.tsx`, `pipeline/game_launch.py`; the Agent Wire voice plan also edits `swarm_dispatcher.py` and `agent_memory.py`. | Compare ownership tables before each wave. | All edits to shared files sit in U7, in the last wave, after V2.9's U1 and U5 are on main. Small edits; adjacent-line conflicts combined, same-line ones to JR. |
| R7 | `pipeline/watch_save.py` has someone else's uncommitted changes (S11). | `git status` before U7. | U7 waits for them to be committed. Until then, `run-week` can be started by hand or by `ensure_team_presser` on the first beat wakeup. |
| R8 | Copyright of real transcripts. | — | Raw transcripts stay in `data/press_corpus/` (gitignored by `data/*`). Only derived statistics and the taxonomy are committed. No transcript text goes into prompts (D8). |
| R9 | Coach personality is neutral until coach profiles are switched on, so AI coaches sound alike. | Read a dry week. | The profile (schemes, tendencies, tenure, hot seat) and the result already set different situations. Real personality comes when coach profiles are switched on (D5): the [real-coach profiles plan](../done/REAL_COACH_PROFILES_PLAN.md) (approved 2026-11-03) supplies `personality.coach_profiles.persona_for`. |
| R10 | V2.9's `Kind` protocol changes in its revision. | U8 reads the built code first. | `press.py` is one small file. |
| R11 | Migration date: V2.10/V2.11 may also choose `2027_01_20`; the relationships plan reserves `2027_01_27_personality_relationships.sql`. | `ls db/migrations` at U6. | This plan keeps `2027_01_20` (coordinator, D12); a sibling takes the next. |

**Spike first (before wave 2; part of U1, about half a day):**
1. Collect at least 40 real postgame coach pressers (§4 U1 sources) and measure questions per presser, follow-up rate, question length, and share by type, split by win, loss and blowout loss.
2. Run one agent presser on fake data with a cloud model and the local 14B, and record calls, tokens, the latency of one round, and the time per presser.

The results set the patience constants, `MAX_QUESTIONS` and the national counts, become the test targets for U4 and U6, and confirm or replace the §2.10 estimates.

## 4. Tests (synthetic, no live DB, fake model)

- `tests/test_press_cast.py`:
  - interest uses only pre-game inputs (the same cast for a game before and after it is played);
  - tier bands, playoff floors, and national counts per tier;
  - one game per national per week;
  - excluded archetypes never cast;
  - former-team weighting; no read of `persona_relationships`; relationship weighting stays neutral without `relationship_context`;
  - beats only at their own team's presser;
  - deterministic for a seed;
  - `press_pool.json` validates (known archetypes, no overlap, every non-excluded national archetype placed).
- `tests/test_press_facts.py`:
  - fact sheet shape, with null for unrecorded stats;
  - `check_line` flags a wrong interception count, a wrong player yardage by last name, a wrong score or record, an unknown name, and a touchdown credited to a player without one;
  - it passes correct lines, opinions and unknown stats;
  - reporter names are allowed.
- `tests/test_press_agents.py`:
  - reporter and coach prompts contain the persona, the memory, the fact sheet and the transcript, and a beat prompt says "only about <team>";
  - JSON parsing of each action;
  - `coach_pick`: the prompt holds the hands (no question text) and this season's pick and skip history, and has no trust, no tags and no reporter-side relationship; `coach_views=None` leaves the views block out, and a given `coach_views` dict appears in it; an invalid pick is retried once, then `fallback_pick` (marked FALLBACK);
  - safety rules: the 25-question cap, no third follow-up in a row, done after two passes;
  - `validate_judgment`: enums, NO_COMMENT never positive, treatment kind consistent with what happened (recognition, skip, cut-off, decline), foreign `turn_id`s dropped, bad JSON retried then neutral, a memory with an invented number replaced;
  - `judgment_events`: envelopes match the relationships plan §2.2 (kinds, required payload fields, stable `source_event_id`s, one-way subject → target);
  - patience by result, streak and hot seat, with temperament only when present, and the clamps;
  - end conditions.
- `tests/test_press_room.py`:
  - a full AI session with a scripted fake model ends on RAN_OUT, PATIENCE or CAP;
  - a bad line is retried once then dropped, and a coach fallback is used;
  - a user session waits and resumes;
  - SCHEDULED to LIVE to REVIEW to CLOSED, with edits allowed only in REVIEW;
  - `decline` from SCHEDULED, and REVIEW auto-approved;
  - `close_effects` runs one appraisal per attendee, stores the appraisal and its events, and writes PRESS rows once (idempotent), including for a declined presser; it never writes `persona_relationships`;
  - `flush_relationship_events` returns 0 when `personality.relationship_store` is missing, and appends once and marks `appended` with a fake store;
  - `press_hands` records every round, and `skip_history` counts hands and picks per reporter and coach;
  - a user session waits for a pick, then for an answer;
  - `run_week` is idempotent and one failure doesn't stop the rest;
  - `subject_controller` reads `user_teams` (an empty list means none);
  - `approved_quotes`, `brief_block` and `declined_note`;
  - the migration and `init_db.sql` define the four tables and CHECKs;
  - the CLI `--help` and `--dry-run` make no model calls.
- `tests/test_press_api.py`:
  - each endpoint's shape on fake data;
  - 403 for an AI-controlled team, 409 on state errors;
  - `pick` refuses a reporter without a raised hand;
  - `answer` returns the next hands;
  - `approve` reports `effects_started` and `wakeup_started` (patched).
- `tests/test_agent_memory.py` (added cases): the PRESS section appears with the newest 3, only before the publication week, and never among takes or summaries.
- `tests/test_press_wiring.py`:
  - `maybe_start_team_wakeup` is held for a user presser that is SCHEDULED, LIVE or REVIEW, and starts once it is CLOSED or DECLINED;
  - `dispatch_team_local` calls `ensure_team_presser`;
  - the beat brief carries the block or the note;
  - the head-coach quoting rule is in the beat directive and the shared rule;
  - the tool is registered and named only in the beat directive;
  - the router is mounted;
  - `game_launch.launch` (not on a dry run) declines and spawns the judgments and the wakeup (Popen patched);
  - `watch_save` spawns `run-week` after the articles (Popen patched) and not on `--dry-run`.
- `tests/test_inbox_kinds_press.py`: one notice per user presser, closed with it, never for AI pressers, and `start` raises 400.

Run with `pytest -m "not integration" tests/test_press_*.py tests/test_agent_memory.py tests/test_inbox_kinds_press.py`. Frontend: `npm run build` in `Mu_Network/`. U1's corpus tool is checked with its own fixtures.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, `Mu_Network/PROJECT_SPEC.md`, this plan, `CLAUDE.md`, `docs/in_progress/V2_MASTER_PLAN.md`.

### U1: Press conference corpus (research spike)

- **Owns:** `tools/press_corpus.py` (new: reads transcripts from `data/press_corpus/*.txt`, splits them into question and answer turns, writes the statistics), `newsroom/config/press_corpus_stats.json` (new: the question and answer taxonomy and the statistics by result, used by tests and the eval, never by prompts), `docs/reference/PRESS_CONFERENCE_CORPUS.md` (new: sources, method, statistics, and how they test the agents), `tests/test_press_corpus.py` (new)
- **Sources to check** (each recorded with its terms of use in the reference doc):
  - team websites' published postgame transcripts (many clubs post "Head Coach X postgame press conference transcript" pages);
  - the NFL's league communications site (game-day and playoff transcripts);
  - ASAP Sports' football section (playoff and Super Bowl media);
  - official team YouTube postgame videos with captions, as a fallback for clubs that don't publish text.
  
  The target is at least 40 head-coach pressers from the 2023–2025 seasons, spread over wins, losses, blowouts, overtime and playoff games, and at least 16 teams.
- **Reads:** this plan §2.1, §2.5; `newsroom/config/agent_personas.json` (to map question types to reporter archetypes)
- **Depends on:** none
- **Done when:**
  - the raw transcripts are in `data/press_corpus/` (not committed);
  - `python3 -m tools.press_corpus stats` prints the statistics and writes `press_corpus_stats.json`;
  - `python3 -m tools.press_corpus eval --transcripts <dir>` compares a set of generated pressers with the corpus statistics (questions per presser, follow-up rate, question length, type mix);
  - the reference doc lists every source with its URL;
  - the spike's latency and token measurement (fake data, cloud and local) is recorded in the reference doc, with proposed values for the §2.5 constants.
- **Verify:** `pytest -m "not integration" tests/test_press_corpus.py` passes on fixture transcripts; `python3 -m tools.press_corpus --help` runs.

### U2: Casting and the national pool

- **Owns:** `newsroom/press_cast.py` (new), `newsroom/config/press_pool.json` (new), `tests/test_press_cast.py` (new)
- **Reads:** this plan §2.4; `engines/excitement_projection.py`, `engines/gotw_engine.py:77`, `newsroom/slot_targets.py:160`, `pipeline/mod_packs.py:90–120`, `:263–285`, `engines/market_constants.py`, `newsroom/config/agent_personas.json`, `PERSONALITY_RELATIONSHIPS_PLAN.md` §0b and §2.1
- **Depends on:** none
- **Done when:** `cast_week` and `pregame_interest` match §2.4 on fake data, and every pool placement has a one-line reason in a `notes` field of the JSON.
- **Verify:** `pytest -m "not integration" tests/test_press_cast.py` all pass.

### U3: Fact sheet and line checker

- **Owns:** `newsroom/press_facts.py` (new), `tests/test_press_facts.py` (new)
- **Reads:** this plan §2.1, §2.3; `newsroom/db_tools.py` (`get_game_box_score`), `engines/game_script_engine.py`, `engines/records_engine.py:294`, `engines/coach_hot_seat.py`, `newsroom/roster_moves.py`
- **Depends on:** none
- **Done when:** `fact_sheet` and `check_line` match §2.3 on fake cursors and adversarial lines.
- **Verify:** `pytest -m "not integration" tests/test_press_facts.py` all pass.

### U4: Reporter and coach agents

- **Owns:** `newsroom/press_agents.py` (new), `tests/test_press_agents.py` (new)
- **Reads:** this plan §2.5; U1's `press_corpus_stats.json` (for test targets); U2's `press_cast.py` (the `Attendee` shape); U3's `press_facts.py`; `newsroom/personas.py`, `newsroom/agent_memory.py:268`, `PERSONALITY_RELATIONSHIPS_PLAN.md` §0b, §2.1, §2.2, `personality/core.py` (`feature_view`), `engines/llm_provider.py:314`
- **Depends on:** U1, U2, U3
- **Done when:** the reporter turns, the coach's turns and picks (with the empty `coach_views` slot), patience, the fallback and safety rules, and the appraisal with its validation and event envelopes work on a scripted fake model, and the constants are set from U1's statistics.
- **Verify:** `pytest -m "not integration" tests/test_press_agents.py` all pass.

### U5: Podium panel

- **Owns:** `Mu_Network/src/types/press.ts` (new), `Mu_Network/src/components/franchise/PressConferencePanel.tsx` (new), `Mu_Network/src/components/franchise/GameCenterModule.tsx`
- **Reads:** this plan §2.11; `RosterCapModule.tsx` (`franchiseFetch`, `SharedStyles`, `StateNote`), `GameCenterModule.tsx`, `types/franchiseGame.ts`
- **Depends on:** none (builds against §2.11's shapes)
- **Done when:** the button and panel render every state from the typed shapes; `GameCenterModule` gains one import, the button and the panel.
- **Verify:** `cd Mu_Network && npm run build` succeeds.

### U6: Session engine, tables, runner and API

- **Owns:** `db/migrations/2027_01_20_press_conferences.sql` (new), `db/init_db.sql`, `newsroom/press_room.py` (new), `server/press_api.py` (new), `tests/test_press_room.py` (new), `tests/test_press_api.py` (new)
- **Reads:** this plan §2.2, §2.6–§2.8, §2.11; U2–U4's modules; `server/franchise_api.py` (`user_teams`, `require_user_team`, `team_row`, `league_state`), `db/franchises.py`, `tests/test_franchise_api.py`, `PERSONALITY_RELATIONSHIPS_PLAN.md` §2.1–§2.2 (and `personality/relationship_store.py` if built), `db/migrations/2027_01_18_writer_voice.sql`
- **Depends on:** U4, and V2.9's U1 on main (it owns `db/init_db.sql` and `2027_01_19`)
- **Done when:**
  - the migration applies twice cleanly to a scratch schema in `madden_test` (manual);
  - `run-week --dry-run` prints casts and first-round prompts;
  - a fake-model week closes 30 AI pressers and schedules 2 user ones;
  - the API works on a bare `FastAPI()`;
  - `server/api.py` is not touched.
- **Verify:** `pytest -m "not integration" tests/test_press_room.py tests/test_press_api.py tests/test_press_agents.py` all pass.

### U7: Pipeline, newsroom and server wiring

- **Owns:** `server/api.py`, `newsroom/swarm_dispatcher.py`, `newsroom/agent_memory.py`, `newsroom/db_tools.py`, `pipeline/game_launch.py`, `pipeline/watch_save.py`, `tests/test_agent_memory.py`, `tests/test_press_wiring.py` (new)
- **Reads:** this plan §2.2, §2.8, §2.9; U6's `press_room.py` and `press_api.py`; `server/api.py:60–120`, `:140–150`; `swarm_dispatcher.py:840–860`, `:1100–1170`, `:1250–1260`, `:1360–1400`, `:2263–2300`; `agent_memory.py:135–296`; `pipeline/game_launch.py`; `pipeline/watch_save.py:632–712`
- **Depends on:** U6; V2.9's U1 and U5 on main (`server/api.py`); the uncommitted `watch_save.py` changes committed (R7)
- **Done when:**
  - the router is mounted;
  - the user wakeup is held, and AI wakeups ensure their presser;
  - beat briefs carry the block or the note;
  - the quoting rule is in the beat directive and the shared rule;
  - the tool is registered for beat writers only;
  - the PRESS memory section shows;
  - Play Game declines and starts the judgments and the wakeup;
  - the watcher spawns `run-week` after the articles (not on `--dry-run` / `--no-articles`);
  - old behaviour holds when the press tables don't exist.
- **Verify:** `pytest -m "not integration" tests/test_press_wiring.py tests/test_agent_memory.py tests/test_local_portfolio.py tests/test_relationships.py tests/test_game_launch.py` all pass.

### U8: Press inbox notice

- **Owns:** `engines/inbox/kinds/press.py` (new), `tests/test_inbox_kinds_press.py` (new)
- **Reads:** this plan §2.13; V2.9's built `engines/inbox/registry.py`, `store.py`, one sibling kind; U6's `press_room.py`
- **Depends on:** U6, and V2.9's U1 on main
- **Done when:** `registry.kinds()` lists `PRESS`, and no other file under `engines/inbox/` changes.
- **Verify:** `pytest -m "not integration" tests/test_inbox_kinds_press.py` passes.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2, U3 | — | Independent new files; U1 includes the spike, whose numbers feed U4 |
| 2 | U4, U5 | U1, U2, U3 (U4); none (U5) | U4 imports U2/U3 and takes its constants and test targets from U1; U5 builds against §2.11's shapes |
| 3 | U6 | U4; V2.9 U1 | Imports the agents; edits `init_db.sql` after the inbox tables |
| 4 | U7, U8 | U6; V2.9 U1 and U5; `watch_save.py` changes committed | U7 holds every edit to shared files; U8 is one new file in `engines/inbox/kinds/`. No file in common |

**Cross-plan order:** waves 1–2 need nothing from other plans and can run alongside V2.9. Waves 3–4 wait for the named V2.9 units. This plan never edits `pipeline/weekly_capture.py`, `FranchisePage.tsx` or an existing file under `engines/inbox/`.

**File ownership check:**

| File | Unit |
|---|---|
| `Mu_Network/src/components/franchise/GameCenterModule.tsx` | U5 |
| `Mu_Network/src/components/franchise/PressConferencePanel.tsx` | U5 |
| `Mu_Network/src/types/press.ts` | U5 |
| `db/init_db.sql` | U6 |
| `db/migrations/2027_01_20_press_conferences.sql` | U6 |
| `docs/reference/PRESS_CONFERENCE_CORPUS.md` | U1 |
| `engines/inbox/kinds/press.py` | U8 |
| `newsroom/agent_memory.py` | U7 |
| `newsroom/config/press_pool.json` | U2 |
| `newsroom/config/press_corpus_stats.json` | U1 |
| `newsroom/db_tools.py` | U7 |
| `newsroom/press_agents.py` | U4 |
| `newsroom/press_cast.py` | U2 |
| `newsroom/press_facts.py` | U3 |
| `newsroom/press_room.py` | U6 |
| `newsroom/swarm_dispatcher.py` | U7 |
| `pipeline/game_launch.py` | U7 |
| `pipeline/watch_save.py` | U7 |
| `server/api.py` | U7 |
| `server/press_api.py` | U6 |
| `tests/test_agent_memory.py` | U7 |
| `tests/test_inbox_kinds_press.py` | U8 |
| `tests/test_press_agents.py` | U4 |
| `tests/test_press_api.py` | U6 |
| `tests/test_press_cast.py` | U2 |
| `tests/test_press_corpus.py` | U1 |
| `tests/test_press_facts.py` | U3 |
| `tests/test_press_room.py` | U6 |
| `tests/test_press_wiring.py` | U7 |
| `tools/press_corpus.py` | U1 |

No path appears twice.

## 7. Spec changes when built

- **§1.1 Repository Layout:** the new modules, configs, tool, reference doc, migration, router, panel, types and tests listed in §6. `data/press_corpus/` is local and not committed.
- **§2 (new §2.22 Press Conference Casting):** the pre-game interest formula, tiers, the pool and the one-game-a-week rule.
- **§3 (new §3.3r Press Conferences):** the four tables; `agent_memory_entries.kind` gains `'PRESS'`.
- **§4 (new §4.6c Press Endpoints):** the eight endpoints and shapes.
- **§6.0b:** the watcher spawns `newsroom.press_room run-week` after the articles. The `game_launch` entry in §4.6b gains the decline behaviour.
- **§7.2 and §7.3:** the beat brief block and note; the head-coach quoting rule for all writers; national writers don't use pressers.
- **§7.7:** the PRESS memory section; reporters' appraisals recorded as relationship events (`reporter_judgment`, `judgment_events`, `flush_relationship_events`), with trust owned by the relationships plan.
- **§7 tools:** `get_press_conference` (beat writers).
- **New §7.14 Press Conferences:** agents, the coach's picks and the safety rules, patience, reporters' judgments, the line checker and retries, the runner, the CLI, compute and model routing (`LLM_PROVIDER_PRESS`, `PRESS_MODEL`, `PRESS_WORKERS`, `PRESS_REPOLL`), `approved_quotes` as the V3 interface, and room for player pressers.
- **The V2.9 inbox section:** the `PRESS` kind.
- **`Mu_Network/PROJECT_SPEC.md`:** the Game Center's podium button and panel.
- **`docs/in_progress/V2_MASTER_PLAN.md` V2.2:** status and link.

## 8. Decisions taken

All decided by JR, 2026-11-03.

| # | Decision |
|---|---|
| D0 | The redesign stands: every head coach holds a postgame presser every week. The pressers are fully agentic, with no fixed triggers or question count. AI coaches answer as their own agents, in background pressers after the ingest. JR's presser opens from a Game Center button once the post-advance save is ingested. A presser not started by Play Game (or by the next ingest) counts as declined, and the 24-hour hold is gone. Casting uses only pre-game interest: beats cover their own coach, nationals cover both coaches at one game a week. Coaches only for now, with room for about 3 players per team later. No invented head-coach quotes for any coach. Quotes go to V3 through `approved_quotes`. |
| D1 | The coach decides who asks next. An AI coach agent picks from the raised hands (its relationship input is per D11); JR picks for his own coach. Picks and skips are recorded and feed the reporters' judgments. There is no moderator; only safety rules remain (25-question cap, at most 2 follow-ups in a row, a fallback order if the coach agent fails). |
| D2 | Trust isn't a fixed table. Each reporter agent appraises the answers it got, being skipped or cut off, or a declined presser. The appraisal is structured output with reasons, validated, and stored with its reasons in the reporter's memory. Declined pressers are appraised too. Trust never affects a sim number. (How appraisals become trust: D10.) |
| D3 | National articles are written at ingest as today. National writers don't quote pressers or use the press tool; no national article type fits, since all are written before the week's pressers. Beat coverage quotes the transcripts. |
| D4 | A cloud model runs the pressers for now (background and JR's live presser). |
| D5 | AI coaches have a neutral voice (profile and situation) until coach personalities are switched on. Their voice then comes from `personality.coach_profiles.persona_for` ([real-coach profiles plan](../done/REAL_COACH_PROFILES_PLAN.md)). |
| D6 | The national pool and tier counts are approved as proposed in §2.4. |
| D7 | A presser left in REVIEW at Play Game is approved as it stands. |
| D8 | Real transcripts are reference and test material only; none of their text goes into prompts. |
| D9 | Wrong numbers in JR's answers get a warning only; his quote stands and writers get the recorded figure. |
| D10 | Cross-plan (personality plan §4.7, §5.1; relationships plan §0b). The relationships plan owns trust. Each appraised answer is a `meaningful_interaction` event; being skipped, cut off or stood up is a `treatment` event, in that plan's §2.2 envelope, appended through `personality/relationship_store.append_events`. Until that plan is built, the events wait in `press_attendees.judgment` in the same shape. This plan computes no trust; its bounds are suggested starting values for that plan's policy. Prompts read trust only through `relationship_context` once it exists, and are neutral before. Nothing writes `persona_relationships`. |
| D11 | Cross-plan. Trust is one-way: nothing reads a reporter's trust as the coach's view, and legacy tags (GRUDGE, FAVOURITE, …) are not read. Until coaches have profiles and their own views, an AI coach's pick has no relationship input (only his pick and skip history and neutral guidance). Real coaches will get reviewed profiles of their real counterparts (personality plan D11), and coach → reporter views arrive through the relationships plan into `coach_pick`'s `coach_views` slot. Coach voice stays neutral until those profiles exist (D5). |
| D12 | Cross-plan. The relationships plan reserves migration `2027_01_27_personality_relationships.sql`; this plan's stays `2027_01_20_press_conferences.sql`. |
| D13 | Cross-plan (JR, 2026-11-03, PERSONALITY_PLAN D12–D13). Reporters named after real writers (Schefter, Barnwell, …) are fictional characters inspired by them, so their appraisals and views are allowed like any persona's. Real coaches appraise once their reviewed profiles exist. |

**Still open** (each has a working value in the plan; none blocks wave 1):
- How appraisals become trust (weights, caps, decay) is decided in the relationships plan's reviewed policy, starting from this plan's suggested bounds. Not decided here.
- The patience and cast constants. U1's corpus sets them before U4 is built.

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | worktree-agent-aac57f2006f11e079 | .claude/worktrees/agent-aac57f2006f11e079 | merged | 1e20392 |
| U2 | 1 | worktree-agent-aa384368d1f408353 | .claude/worktrees/agent-aa384368d1f408353 | merged | 8ea6d84 |
| U3 | 1 | worktree-agent-a4920c59d5fd33383 | .claude/worktrees/agent-a4920c59d5fd33383 | merged | 2f0042d |
| U4 | 2 | worktree-agent-a42407b539e4ee843 | .claude/worktrees/agent-a42407b539e4ee843 | merged | 0ad861f |
| U5 | 2 | worktree-agent-a05cf03f87acf9f8a | .claude/worktrees/agent-a05cf03f87acf9f8a | merged | 2e42190 |
| U6 | 3 | worktree-agent-a9c75b0608ae107ed | .claude/worktrees/agent-a9c75b0608ae107ed | merged | 1e12761 |
| U7 | 4 | worktree-agent-a31a84d6959cb2e12 | .claude/worktrees/agent-a31a84d6959cb2e12 | merged | 57946cc |
| U8 | 4 | worktree-agent-a9d81c4cd0f3c397b | .claude/worktrees/agent-a9d81c4cd0f3c397b | merged | a7ed796 |
