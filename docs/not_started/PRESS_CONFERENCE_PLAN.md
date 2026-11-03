# Project Mu: Interactive Press Conferences Plan (V2.2)

**Status:** proposal, awaiting JR's approval (2026-11-03).
**Builds on:** [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §5 (post-advance ingest decision), §5.3.2 (interactive latency boundary), §6 V2.0 ground rules and V2.2; [`V3_STUDIO_PLAN.md`](../in_progress/V3_STUDIO_PLAN.md) §7 and its ownership table (V3 consumes approved quotes); [`AGENT_WIRE_VOICE_PLAN.md`](../in_progress/AGENT_WIRE_VOICE_PLAN.md) U1 (person keys, writer relationships); [`FRONT_OFFICE_INBOX_PLAN.md`](./FRONT_OFFICE_INBOX_PLAN.md) (V2.9, not built yet: the `engines/inbox/` kind registry, `inbox_threads`, `jr_statements`; it builds first); [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) phases 1–3; PROJECT_SPEC §2.14 (game scripts), §4.6b (franchise API), §7.2 (beat reporting), §7.7 (agent memory), §7.11 (personas).

**Goal:** After JR plays his game, advances the week and the watcher ingests the save, a postgame press conference waits for him in the Franchise Command Center. The team's beat reporters and one national writer each ask a question built from what really happened in the game: the result, a scoring swing (a comeback, a blown lead), turnovers, a star who barely touched the ball, a breakout, an injury, a record. JR types his answers as the head coach. Each reporter may push once with a follow-up. JR reviews the transcript, keeps or pulls answers off the record, and approves. Approved answers become the only head-coach quotes in that week's beat coverage of his team, reporters remember the exchange, and an evasive answer costs a little goodwill with the reporter who asked. Numbers stay deterministic: questions are built from engine facts and a model may only reword them; a phrased question with a number the facts don't hold is thrown away for the template.

**Delivers:**
- `newsroom/press_questions.py`: game facts, question hooks, reporter selection, question and follow-up templates, model phrasing with a number guard, and the answer number check. Pure functions plus one fact reader.
- `newsroom/press_conference.py`: the press conference lifecycle (prepare, answer, edit, approve, skip, expire), reporter memory (`agent_memory_entries.kind = 'PRESS'`), relationship moves toward JR's coach, the beat brief block, the wakeup hold, the `get_press_conference` writer tool and `approved_quotes` for V3.
- `db/migrations/2027_01_20_press_conferences.sql` (folded into `db/init_db.sql`): tables `press_conferences` and `press_questions`.
- `server/press_api.py`: `/api/franchise/{abbr}/press*` endpoints on its own router (same prefix as the franchise API, so the frontend reuses `franchiseFetch`).
- `Mu_Network/src/types/press.ts` and `Mu_Network/src/components/franchise/PressConferencePanel.tsx`, rendered at the top of the Game Center.
- `engines/inbox/kinds/press.py`: a `PRESS` inbox kind (V2.9's registry) that posts a SYSTEM notice "Postgame press conference ready" in JR's inbox and closes it when the presser is approved, skipped or expired.
- Wiring: the router mounted in `server/api.py`; the user team's local beat wakeup held while the presser is open; the press block in beat briefs; a PRESS section in the memory block; the writer tool registered.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | The watcher runs load → engines → league office → national articles → mod pack in one ingest (`pipeline/watch_save.py:632` `ingest_snapshot`, articles at `:690–694`), on the save made after Advance Week (V2_MASTER_PLAN §5, decision 2026-10-30). | The presser can only happen after the national slate for the new week is written. National articles that week carry no quotes. The plan does not hold or re-order the watcher, and does not edit `watch_save.py` (it also has someone else's uncommitted changes). |
| S2 | The team's beat portfolio (Postgame Report Card, Matchup Primer, Player Spotlight, Season Progress Report) is not written by the watcher. It is an on-demand wakeup started when a team page or Agent Wire Team Hub is viewed: `server/api.py:97` `maybe_start_team_wakeup` → `:71` `start_local_wakeup` → `newsroom/swarm_dispatcher.py:2263` `dispatch_team_local`, which also runs the team's Agent Wire posts. | This is where the quotes land in the same week: hold the user team's wakeup while the presser is open, and start it when JR approves or skips. One small edit in `maybe_start_team_wakeup`. |
| S3 | Beat briefs already get per-team blocks (`swarm_dispatcher.py:1387` `brief_for_agent["team_transactions"] = team_block(...)`), each in its own try/except. | The press block goes in the same way: `brief_for_agent["press_conference"]`. |
| S4 | The beat directive invites invented quotes: "The Press Conference / Quote Hook: Open with a candid quote from a coordinator or team captain" (`swarm_dispatcher.py:1166`) and "Quote coaching staff sentiment" (`:1162`). | With a presser on record, the head coach's words must come only from the transcript. Coordinators and captains stay as they are (Q5). |
| S5 | `agent_memory_entries` has a `kind` column, default `'TAKE'` (`db/init_db.sql:2112`), and every memory read filters `kind = 'TAKE'` (`newsroom/agent_memory.py:279`, `:285`, `:334`, `:350`). | Press exchanges can be stored as `kind = 'PRESS'` rows with no schema change and without leaking into takes or summaries. `build_memory_block` (`:268`) gets one new section. |
| S6 | Writer relationships live in `persona_relationships` keyed by person key, `target_type` player / coach / team (`newsroom/relationships.py:1–30`). `_adjust` (`:386`) moves one row and appends evidence; `_already_applied` (`:449`) checks an evidence `ref`. | Reporter → JR's coach trust can move on answers, idempotently, with `ref = "press:<question_id>"`. `relationships.py` is not edited; the press module calls these helpers. |
| S7 | `games.completed_at` is never written by the save loader (`grep completed_at pipeline/load_save.py`: no hits); the load stamps `captures.loaded_at` with `mode = 'save'` (`pipeline/load_save.py:963`). | The wakeup hold window is timed from the newest `captures.loaded_at` (mode `save`), not from the game. |
| S8 | `engines/game_script_engine.py` stores one deterministic story per game (`game_scripts`): shape (`comeback`, `rout`, `late_winner` …), quarter scores, lead changes, largest lead and winner's deficit, scoring plays, key moments (`ensure_script` `:1209`, `script_summary` `:1044`). `engines/records_engine.py:294` `find_records_broken_in_week`. | Scoring swings and records come from these, not from a model. Player usage comes from `player_stats` / `game_stat_lines` against the player's earlier games. |
| S9 | `server/franchise_api.py:148` router prefix `/api/franchise`; `:213` `user_teams()` and `:223` `require_user_team()` give the 403 rule; `Mu_Network/src/components/franchise/RosterCapModule.tsx:27` `franchiseFetch(abbr, path, …)` always calls `/api/franchise/{abbr}{path}`. | A separate `server/press_api.py` router with the same prefix keeps `franchise_api.py` untouched (sibling plans are likely to edit it) and lets the panel use `franchiseFetch`. |
| S10 | `GameCenterModule.tsx` (324 lines) shows the next game and the Play Game button; `FranchisePage.tsx` holds the eight-module tab list. | The presser is a panel above the next-game card (Q1). Only `GameCenterModule.tsx` changes (one import, one element); `FranchisePage.tsx` is not touched. |
| S11 | Models: `engines/llm_provider.generate_text(prompt, source, label, model, provider, thinking_level, response_mime_type, …)` (`:314`); provider by `LLM_PROVIDER_<SOURCE>` / `<LABEL>` env, else `LLM_PROVIDER`, else Gemini (`:71`). Usage logs to `llm_usage`. | Source `press`, labels `press_questions` and `press_follow_up`; `LLM_PROVIDER_PRESS=ollama` runs it locally. Madden is closed by the time of the presser (JR quits after saving), so a local model has the GPU. |
| S12 | Persona rendering: `newsroom/personas.load_personas(cur=…)` (`:517`) and `render_persona(persona, fmt, tags, seed)` (`:746`). Beat reporters are `agent_memories` rows with `archetype = 'LOCAL_BEAT_REPORTER'` and `assigned_team_id` (`swarm_dispatcher.py:2282`). | Question phrasing uses the asking writer's persona. Fans, panels and label accounts are excluded from the podium. |
| S13 | The latest migration is `db/migrations/2027_01_18_writer_voice.sql`. | Next free name: `2027_01_20_press_conferences.sql` (see R6: sibling plans may want the same date). |
| S15 | The V2.9 inbox plan (`FRONT_OFFICE_INBOX_PLAN.md`, not built) claims migration `2027_01_19_front_office_inbox.sql`, owns `engines/inbox/` with a kind registry that auto-discovers any module in `engines/inbox/kinds/` defining `KIND` (its §2.2 `Kind` protocol: `scan`, `options`, `act`, `template`, `start`; `Trigger`, `ActResult`), reserves `KIND = "PRESS"` with counterpart `system` for this plan (its §2.8 table), stores JR's answers to his beat writer's comment requests and exposes them through `engines/inbox/store.jr_statements(cur, team_id, season, week=None)` (ON_RECORD / OFF_RECORD), and leaves their newsroom wiring and trust effects to V2.2 (its Q4). It edits `server/api.py`, `db/init_db.sql`, `pipeline/weekly_capture.py` and `FranchisePage.tsx`, and builds first. | Migration date `2027_01_20`. The presser notice is a new file in `engines/inbox/kinds/`, no edit to inbox code. This plan wires `jr_statements` into beat briefs and memory beside the presser quotes. Every edit to `db/init_db.sql` and `server/api.py` waits for the inbox units that own them to be on main (§6). This plan never edits `pipeline/weekly_capture.py` or `FranchisePage.tsx`. |
| S14 | `tests/test_franchise_api.py` tests every endpoint on fake data with a recording `FakeCursor` and FastAPI `TestClient`. `tests/test_agent_memory.py`, `tests/test_relationships.py`, `tests/test_local_portfolio.py` exist. | The new tests follow the same fake-cursor pattern; no live database. |

## 1. Scope

**In:**
- One presser per user team per completed game (regular season and playoffs), for the team's most recent completed game, offered from the moment the post-advance save is ingested until the team's next game is completed.
- Questions from deterministic hooks over the game's facts, asked by the team's beat reporters plus one national writer (two in the playoffs), five questions by default, at most two follow-ups.
- Free-text answers, "No comment", edit before approval, an on-record toggle per answer, approve or skip the whole presser.
- A number check on JR's answers against the box score: warnings only.
- Approved, on-record answers quoted verbatim in the user team's beat coverage for that week; the head coach is quoted from nothing else.
- Reporter memory of each exchange (`kind = 'PRESS'`) and small trust moves toward JR's coach.
- The `PRESS` inbox kind: a notice per presser, closed with it.
- JR's on-record answers to his beat writer from the V2.9 inbox (`jr_statements`) quoted in beat briefs and remembered by that writer, the newsroom half V2.9 left to this plan.
- A writer tool `get_press_conference(team_identifier, season, week)` so later national pieces can cite the transcript.
- `press_conference.approved_quotes(cur, season, week, team_id=None)`: the stable read V3 builds on.
- The Game Center panel.

**Out (non-goals):**
- Holding or re-ordering the watcher's national slate (S1). National writers can quote the presser from the following week through the tool.
- Agent Wire reactions to quotes. `newsroom/social_publish.py` and `social_writer.py` belong to the in-progress Agent Wire voice plan; a follow-up can feed `approved_quotes` into its game facts (Q7).
- Voice, audio, video or images of the presser, and any downstream media: V3 (V3_STUDIO_PLAN §6–§7).
- Player pressers, coordinator pressers, opponent-coach pressers, offseason pressers (draft night, signings, hiring).
- Answers changing any number: morale, player personality, progression, contracts, popularity. Only writer trust moves, and that only touches prompts.
- Anything inside Madden. JR never uses Madden's menus.

## 2. Design

### 2.1 When a presser exists

- **Game:** `press_questions.presser_game(cur, team_id, season, current_week)` returns the team's latest `games` row with `status = 'COMPLETED'` and `week < current_week` of the season, or `None`. Byes have no presser.
- **Who can hold one:** teams in the active franchise's `user_teams` (`franchise_api.user_teams()`); when that list is empty, any team the UI asks for (same rule as the franchise write endpoints).
- **Expiry:** a presser still `OPEN` is set to `EXPIRED` the first time it is read after a later game of the team is `COMPLETED` (`press_conference.expire_stale(cur, team_id, season)`).
- **Timing in practice:** JR plays, advances, saves, quits. The watcher ingests (S1). JR opens `#franchise`; the Game Center shows "Postgame press conference: Week N vs OPP". Nothing is prepared until JR presses **Start press conference** (or the panel calls prepare on open; see §2.6), so a week JR ignores costs no model calls.

### 2.2 Game facts (`newsroom/press_questions.py`)

`game_facts(cur, game_id: int, team_id: int) -> dict`, read once at prepare time and frozen in `press_conferences.facts`:

```
{"game_id", "season", "week", "game_type",
 "team": {"id", "abbr", "name"}, "opponent": {"id", "abbr", "name"}, "home": bool,
 "score": {"team": int, "opponent": int}, "result": "W"|"L"|"T",
 "record_after": {"wins", "losses", "ties"}, "streak": int,              # +3 = won 3 straight, -2 = lost 2
 "coach": {"id": int|null, "name": str|null},                            # head coach from coach_tenures for the season
 "script": {"shape": [str], "storyline": str, "quarter_scores": {...}, "lead_changes": int,
            "largest_lead": int, "winner_largest_deficit": int, "key_moments": [str]},   # game_script_engine
 "team_stats": {"total_offense", "rushing_yards", "passing_yards", "turnovers", "takeaways",
                "third_down": [conv, att] | null, "sacks_allowed": int|null},        # null = not recorded
 "players": [{"player_id", "name", "position", "line": {...recorded game counters...},
              "season_avg": {...per-game averages over the team's earlier games this season...},
              "touches": int, "touches_avg": float|null, "yards": int, "yards_avg": float|null}],
 "injuries": [{"player_id", "name", "position", "injury", "weeks_out"}],  # new this week, starters first
 "records": [{"text", "player_id"|null}],                                 # find_records_broken_in_week, this team
 "next_game": {"week", "opponent_abbr", "opponent_name", "home"} | null}
```

Unknown stays `null`, never 0 (house rule from §7.2). Players: the team's top 12 by touches + receptions + defensive plays in the game, plus any player in the team's season top 5 by touches who is not in that list.

### 2.3 Hooks

`select_hooks(facts: dict, max_n: int = PRESS_QUESTIONS) -> list[dict]` returns `[{"hook", "severity", "facts", "subject": {"type": "player"|"team"|"coach", "id"}|null}]`, highest severity first, ties broken by a `random.Random(f"press:{game_id}")` shuffle. `RESULT` is always first; `NEXT_GAME` is last when there is room.

| Hook | Trigger (all from `facts`) | Severity |
|---|---|---|
| `RESULT` | always | opener |
| `COMEBACK` | shape has `comeback`, or a win with `winner_largest_deficit >= 10` | 3 |
| `BLOWN_LEAD` | a loss after leading by 10+ (script) | 3 |
| `ROUT_LOSS` / `ROUT_WIN` | margin ≥ 21 | 3 / 1 |
| `LATE_FINISH` | shape `late_winner` or overtime | 2 |
| `TURNOVERS` | team turnovers ≥ 3 | 3 |
| `QB_STRUGGLE` | the team's QB (most pass attempts) threw 2+ interceptions, or passer rating < 70 where recorded | 2 |
| `STAR_QUIET` | a season-top-3 player by touches with touches ≤ 0.5 × `touches_avg` (≥ 3 earlier games) | 2 |
| `BREAKOUT` | a player with yards ≥ 100 and ≥ 2 × `yards_avg` (or ≥ 2 sacks, or 2+ takeaways) | 2 |
| `RUN_PASS` | rush share of plays ≥ 0.6 or ≤ 0.25 (both recorded) | 1 |
| `INJURY` | a starter injured this week, out 1+ weeks | 2 |
| `RECORD` | a record broken or tied by the team or a player | 3 |
| `STREAK` | `abs(streak) >= 3` | 1 |
| `RELATIONSHIP` | the asking reporter holds GRUDGE / DOUBTER / FAVOURITE / BELIEVER on a player in `facts.players` (§2.4) | 2 |
| `NEXT_GAME` | `next_game` not null | closer |

Constants at the top of the module: `PRESS_QUESTIONS = 5`, `PRESS_MAX_FOLLOW_UPS = 2`, `PLAYOFF_NATIONALS = 2`, `REGULAR_NATIONALS = 1`.

### 2.4 Reporters

`select_reporters(cur, facts: dict) -> list[dict]` → `[{"agent_id", "person_key", "archetype", "display_name", "handle", "kind": "BEAT"|"NATIONAL"}]`:
- every `LOCAL_BEAT_REPORTER` with `assigned_team_id = team_id`, by `agent_id`;
- then `REGULAR_NATIONALS` (or `PLAYOFF_NATIONALS` for `game_type <> 'REGULAR_SEASON'`) national writers: those whose person key holds a `persona_relationships` row on the team or a player in `facts.players` with `|trust| >= relationships.SHOWN_MIN`, strongest first; filled from the `allowed_writers` of `SINGLE_GAME_SCHEMATIC_AUTOPSY` in `newsroom/config/article_types.json`, seeded by `game_id`;
- excluded: `DIE_HARD_FAN`, `BANDWAGON_FAN`, `FORMER_PLAYER`, `relationships.NO_SEED_ARCHETYPES`, `*_TWEET` accounts; one account per person key.

`assign_questions(hooks, reporters) -> list[dict]`: the first beat reporter asks `RESULT`; the national writer(s) get the highest-severity hook left, preferring a `RELATIONSHIP` hook built for them; the rest go round-robin over the beats.

### 2.5 Question text

- `template_question(hook: dict, facts: dict, reporter: dict) -> str`: one or two fixed templates per hook, picked by seed, with the numbers filled from `hook["facts"]`. Always available; no model.
- `phrase_questions(items: list[dict], facts: dict, personas: dict, model: str | None = None) -> list[dict]`: one `generate_text(..., source="press", label="press_questions", thinking_level="low", response_mime_type="application/json")` call for the whole presser. The prompt gives each item its reporter's persona (`render_persona(persona, "POST", tags={"GAME_REVIEW"}, seed=f"press:{game_id}:{agent_id}")`), the hook's facts and the template, and asks for `[{"i": int, "question": str}]`: a reworded question, at most 45 words, no new facts. Each result passes `numbers_ok(text, allowed: set[str]) -> bool` (every digit run in the text must appear in the hook's facts, the score, the record or the week) and a name check (every capitalised two-word name must be a name in `facts`); a failure, a parse error or a model error keeps the template and sets `source = 'TEMPLATE'`.
- **Follow-ups:** `needs_follow_up(question: dict, answer: str, no_comment: bool, flags: list, asked: int) -> bool`: true when `asked < PRESS_MAX_FOLLOW_UPS` and the question was not itself a follow-up, and (no comment on a hook of severity ≥ 2, or fewer than `PRESS_SHORT_ANSWER_WORDS = 8` words on severity ≥ 2, or the answer has a number flag). `follow_up_question(question, answer, facts, persona, model=None) -> (text, source)`: a template ("Coach, to follow up: …") phrased by one `press_follow_up` model call under the same guards, with a `PRESS_FOLLOW_UP_TIMEOUT_S = 10` limit (run in a worker thread; on timeout the template is used). This is the only call made while JR waits (§5.3.2).

### 2.6 Answer number check

`check_answer(answer: str, facts: dict) -> list[dict]`, each `{"span", "claim", "recorded", "stat"}`:
- a score `N-M` / `N to M` that is not the final in either order;
- `N <stat>` for yards, touchdowns/TDs, turnovers/giveaways, takeaways, sacks, interceptions/picks, catches/receptions, carries; with a player's name (or last name) within the 6 words before, checked against that player's line, else against the team line;
- nothing is flagged when the recorded value is `null` or the phrase can't be tied to one stat.

Flags are warnings. JR can edit or approve anyway (Q4). They are stored on the question and, when the answer is approved with a flag, the press block tells writers the recorded figure (§2.9).

### 2.7 Tables (`db/migrations/2027_01_20_press_conferences.sql`)

```sql
CREATE TABLE IF NOT EXISTS press_conferences (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    week INT NOT NULL,                       -- the game's week
    game_id INT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    team_id INT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    coach_id INT REFERENCES coaches(id) ON DELETE SET NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'APPROVED', 'SKIPPED', 'EXPIRED')),
    facts JSONB NOT NULL,                    -- press_questions.game_facts, frozen at prepare
    model VARCHAR(100),                      -- model that phrased the questions, NULL = templates only
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMP,                     -- approved / skipped / expired
    UNIQUE (game_id, team_id)
);
CREATE INDEX IF NOT EXISTS idx_press_conferences_team ON press_conferences (team_id, season, week);

CREATE TABLE IF NOT EXISTS press_questions (
    id SERIAL PRIMARY KEY,
    conference_id INT NOT NULL REFERENCES press_conferences(id) ON DELETE CASCADE,
    seq INT NOT NULL,                        -- order asked; a follow-up takes its parent's seq + 0.5 order via parent_id
    parent_id INT REFERENCES press_questions(id) ON DELETE CASCADE,   -- set on follow-ups
    agent_id VARCHAR(100) NOT NULL,          -- agent_memories.agent_id of the asking reporter
    hook VARCHAR(16) NOT NULL,
    subject_type VARCHAR(10),                -- player | team | coach
    subject_id INT,
    hook_facts JSONB NOT NULL DEFAULT '{}'::jsonb,
    question TEXT NOT NULL,
    source VARCHAR(8) NOT NULL DEFAULT 'TEMPLATE' CHECK (source IN ('MODEL', 'TEMPLATE')),
    answer TEXT,
    answer_status VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (answer_status IN ('PENDING', 'ANSWERED', 'NO_COMMENT')),
    on_record BOOLEAN NOT NULL DEFAULT TRUE,
    flags JSONB NOT NULL DEFAULT '[]'::jsonb,   -- check_answer output
    answered_at TIMESTAMP,
    UNIQUE (conference_id, seq, parent_id)
);
CREATE INDEX IF NOT EXISTS idx_press_questions_conf ON press_questions (conference_id);
```

Idempotent, wrapped in `BEGIN/COMMIT`, applied by hand to `madden_test` first; the same DDL appended to `db/init_db.sql`. Every reader checks `to_regclass('press_conferences')` and returns empty when the table is missing.

### 2.8 Lifecycle (`newsroom/press_conference.py`)

Every function takes a cursor and never commits; the API commits.

- `prepare(cur, team_id: int, season: int, current_week: int, model: str | None = None, phrase: bool = True) -> dict | None`: `expire_stale`, then the existing row for `presser_game`'s game, else `game_facts` → `select_reporters` → `select_hooks` → `assign_questions` → templates → `phrase_questions` (when `phrase`) → insert. Returns `conference_dict`. `None` when there is no game. Concurrent prepares are safe through `UNIQUE (game_id, team_id)` (`ON CONFLICT DO NOTHING`, then re-read).
- `answer(cur, conference_id, question_id, text: str | None, no_comment: bool = False, model=None) -> dict`: refuses (`PressError`, API 409) unless the conference is `OPEN` and the question `PENDING`. Stores the answer, `check_answer` flags, and when `needs_follow_up` inserts the follow-up. Returns `{"question": …, "follow_up": … | null}`.
- `edit_answer(cur, conference_id, question_id, text: str | None = None, on_record: bool | None = None) -> dict`: only while `OPEN` and the question is answered; re-runs `check_answer`.
- `approve(cur, conference_id) -> dict`: needs every question `ANSWERED` or `NO_COMMENT` (unanswered ones become `NO_COMMENT`). Sets `APPROVED`, `closed_at`; then `record_memory` and `apply_relationships`. Returns `{"conference", "memory_rows", "relationship_rows"}`.
- `skip(cur, conference_id) -> dict`: `SKIPPED`; no memory, no trust change (Q6).
- `record_memory(cur, conf) -> int`: per question, one `agent_memory_entries` row: `kind = 'PRESS'`, `agent_id` = asker, `season`, `week` = the game's week (so prompts from the next publication week on see it, matching `week < publication_week`), `team_id`, `player_id` = subject when a player, `article_id` NULL, text `"Week 5 presser: you asked Coach Whitfield about Tetairoa McMillan's two catches. He said: \"…\""` (answer cut to 300 characters; "He declined to comment." for `NO_COMMENT`; "(off the record)" answers are remembered as "He answered off the record."). Skips rows already present for the conference (text prefix check by `agent_id`, season, week, kind).
- `apply_relationships(cur, conf) -> int`: for each asker's person key toward `("coach", coach_id)`: `PRESS_ANSWERED = +0.02` for an answered question, `PRESS_NO_COMMENT = -0.04` for a declined one, through `relationships._adjust` with event `{season, week, kind: "PRESS", delta, text, ref: f"press:{question_id}"}`, skipped when `_already_applied`. Nothing when `coach_id` is null.
- `current(cur, team_id, season, current_week) -> dict | None`: the presser for `presser_game` (no prepare), after `expire_stale`.
- `conference_dict(cur, conference_id) -> dict`: the API shape (§2.10).
- `holds_wakeup(cur, team_id: int, season: int, week: int, user_team_ids: list[int], now=None) -> bool`: true when `team_id` is in `user_team_ids` (non-empty), the team played a game with `week < week` that has no `APPROVED`/`SKIPPED`/`EXPIRED` presser, and the newest `captures` row with `mode = 'save'` was loaded less than `PRESS_HOLD_HOURS = 24` hours ago (S7). After that the wakeup runs without quotes.
- `brief_block(cur, team_id: int, season: int, week: int) -> dict | None`: the newest `APPROVED` presser of the team with game week `< week` and no later completed game of the team; `None` otherwise. Shape:
  ```
  {"coach": "Marcus Whitfield", "game": "Week 5 vs Atlanta, lost 17-24",
   "quotes": [{"reporter": "@handle", "question": str, "answer": str}],          # on-record, ANSWERED only
   "declined": [str],                                                             # questions he declined
   "comments": [{"reporter": "@handle", "subject": str, "answer": str}],        # inbox jr_statements, ON_RECORD only, this week

   "corrections": [str],                                                          # "He said 3 turnovers; the box score has 2."
   "reporting_guidance": "Quote the head coach only from these answers, word for word. Do not invent other words for him. Use the box score for your own figures; where he misstated one, you may say so."}
  ```
- `inbox_comments(cur, team_id, season, week) -> list[dict]`: `engines.inbox.store.jr_statements(cur, team_id, season, week - 1)` ON_RECORD rows reshaped for `brief_block["comments"]`; `[]` when `engines.inbox` is missing or its tables don't exist (import and `to_regclass` guarded). `brief_block` returns a block when there is either an approved presser or a comment. `record_comment_memory(cur, team_id, season, week) -> int` writes one `kind = 'PRESS'` memory row per on-record comment for the writer who asked ("You asked Coach … for comment on …; he said: …"), idempotent on the text prefix; U4 calls it in `maybe_start_team_wakeup` right before a wakeup starts, so it runs once per team-week and never while a prompt is being built.
- `skipped_note(cur, team_id, season, week) -> str | None`: for a `SKIPPED` or `EXPIRED` presser, the line "The head coach did not take questions after the Week N game; do not quote him on it." (Q5).
- `approved_quotes(cur, season: int, week: int, team_id: int | None = None) -> list[dict]`: `[{conference_id, game_id, team_id, coach_id, coach_name, week, agent_id, hook, question, answer, flags}]`, on-record and answered only. The interface V3 reads; documented as stable.
- `get_press_conference(team_identifier: str, season: int = 0, week: int = 0) -> str`: the writer tool (in this module, re-exported by `newsroom/db_tools.py`), the approved transcript as plain text with the same quoting rule, week-cut like the other tools (only pressers whose game week is before the run's publication week).

### 2.9 Prompt wiring

- **Beat briefs** (`swarm_dispatcher.py`, next to `team_transactions` at `:1387`): `brief_for_agent["press_conference"] = press_conference.brief_block(tcur, assigned_team_id, season, week)` when not `None`, else `brief_for_agent["press_conference_note"] = skipped_note(...)` when not `None`; its own try/except like the neighbours.
- **Beat directive** (`:1162–1166`): one added line: "If your brief has `press_conference`, every word you attribute to the head coach comes from it, verbatim; coordinators and players may still be quoted in your usual way." The Quote Hook keeps working for coordinators and captains.
- **Tool:** `get_press_conference` added to the tool list (`swarm_dispatcher.py:846` block) and to the national and beat research directives: "Call `get_press_conference` for a user team's postgame presser; quote the coach only from it."
- **Memory block** (`agent_memory.build_memory_block`): reads `kind = 'PRESS'` rows of the person (`week < publication week`), newest `PRESS_EXCHANGES_SHOWN = 3`, and `format_memory_block` adds "YOUR PRESS ROOM EXCHANGES WITH THE COACH (you were there; you may refer back to them):" after the takes. The TAKE queries are unchanged.
- **Wakeup hold** (`server/api.py:97` `maybe_start_team_wakeup`): before starting, `if press_conference.holds_wakeup(cur, team_id, season, curr_week, user_team_ids): return False`, inside a try/except that falls through to the old behaviour. `server/press_api.py` calls `server.api.start_local_wakeup` (imported lazily) after approve and skip.

### 2.10 API (`server/press_api.py`, `router = APIRouter(prefix="/api/franchise")`)

Shapes (all mirrored in `Mu_Network/src/types/press.ts`):

```
PressReporter   {agent_id, display_name, handle, kind: "BEAT"|"NATIONAL", avatar_color: str|null}
PressQuestion   {id, seq, parent_id: int|null, reporter: PressReporter, hook, question, source: "MODEL"|"TEMPLATE",
                 answer: str|null, answer_status: "PENDING"|"ANSWERED"|"NO_COMMENT", on_record: bool,
                 flags: [{span, claim, recorded, stat}]}
PressConference {id, season, week, game_id, status: "OPEN"|"APPROVED"|"SKIPPED"|"EXPIRED",
                 team: {id, abbr, name}, opponent: {id, abbr, name}, home: bool,
                 score: {team, opponent}, result: "W"|"L"|"T", coach_name: str|null,
                 created_at: ISO, closed_at: ISO|null, questions: [PressQuestion]}   # follow-ups after their parent
```

| Method and path | Body | Returns | Errors |
|---|---|---|---|
| `GET /{abbr}/press` | — | `{conference: PressConference\|null, game: {game_id, week, opponent_abbr, result, score}\|null}` | 404 unknown team |
| `POST /{abbr}/press/prepare` | `{phrase?: bool}` (default true) | `PressConference` | 403 not a user team, 404 no completed game |
| `POST /{abbr}/press/{conference_id}/answer` | `{question_id, answer?: str, no_comment?: bool}` | `{question, follow_up}` | 400 empty answer without no_comment, 403, 404, 409 not OPEN / already answered |
| `PUT /{abbr}/press/{conference_id}/questions/{question_id}` | `{answer?, on_record?}` | `PressQuestion` | 403, 404, 409 |
| `POST /{abbr}/press/{conference_id}/approve` | `{}` | `{conference, memory_rows, relationship_rows, wakeup_started}` | 403, 404, 409 not OPEN |
| `POST /{abbr}/press/{conference_id}/skip` | `{}` | `{conference, wakeup_started}` | 403, 404, 409 |
| `GET /{abbr}/press/history?season=` | — | `{conferences: [PressConference]}` approved and skipped, newest first | 404 |

The conference id must belong to `{abbr}`'s team (else 404). 403 follows `franchise_api.require_user_team`.

### 2.11 Inbox kind (`engines/inbox/kinds/press.py`)

Written against V2.9's §2.2 protocol; found by its auto-discovery, so no inbox file changes.

- `KIND = "PRESS"`, `COUNTERPART_TYPE = "system"`, `PERSONA_ROLE = None`.
- `scan(cur, season, week, team)`: when `press_questions.presser_game` gives a game whose presser is missing or `OPEN`, one `Trigger(kind="PRESS", counterpart_type="system", counterpart_id="press", counterpart_name="Media relations", subject=f"Postgame press conference: Week {w} vs {opp}", source_ref=f"press:{game_id}", facts={game_id, week, opponent_abbr, result, score}, intent="PRESSER_READY", link_table="games", link_id=game_id, expires_season=season, expires_week=<the team's next game week, else None>)`. Nothing for a closed presser.
- `options`: `OPEN_PRESSER` (label "Go to the press conference"; the UI opens `#franchise?team=ABBR`, the Game Center) and `SKIP_PRESSER`. `act`: `OPEN_PRESSER` → intent `NOTED`, nothing else; `SKIP_PRESSER` → `press_conference.skip` (preparing a templates-only presser first when none exists), `close=True`. A thread whose presser was approved or skipped in the panel is closed by `scan` the next time it runs (`act` is not needed for that).
- `template(intent, facts)`: plain lines ("Reporters are waiting after the Week 5 loss to Atlanta. Answer them in the Game Center.").
- `start`: raises `InboxError(400)`; pressers are never started from the inbox.

The presser itself stays in the Game Center panel (Q8): one presser has several reporters and a review step, which an inbox thread (one counterpart, one reply at a time) does not fit.

### 2.12 Panel (`PressConferencePanel.tsx`)

Props `{ teamAbbr: string }`. Rendered by `GameCenterModule` above the next-game card. States:
- **No game / expired / approved:** hidden, except a one-line link "Week N presser: approved, 4 quotes" for approved.
- **Ready:** a card "Postgame press conference · Week 5 vs ATL · L 17-24" with **Start press conference** and **Skip** buttons. Start calls `prepare` (spinner "Reporters are taking their seats"; templates come back without a model if it fails).
- **Open:** one question at a time: reporter name, handle and kind badge, the question, a textarea, **Answer** and **No comment**. A follow-up appears straight after its parent. Number flags show under the answer as "You said 3 turnovers; the box score has 2" with **Edit**.
- **Review:** the full transcript, each answer editable with an **On the record** toggle, then **Approve and publish** (shows "Your beat writers will file with these quotes") or **Skip presser**.
- Plain `useState` + `franchiseFetch`, no new libraries; the CSS block style of the other modules.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | A phrased question invents a fact the number and name guards miss (a wrong player role, "your third straight loss" when it's the second). | U1 tests with adversarial fake model output; JR reads the first live pressers. | Set `phrase=False` (templates only) by default with a `PRESS_PHRASE=0` env switch already in the design. |
| R2 | Model latency for the follow-up while JR waits (§5.3.2). | U3's live smoke run logs the time per call in `llm_usage` / console. | The 10 s timeout falls back to the template; `LLM_PROVIDER_PRESS=ollama` with Madden closed. |
| R3 | JR views his team page before starting the presser, starting the wakeup without quotes. | `holds_wakeup` covers the first 24 h after ingest; tested in U4. | The hold is a single check; the window is a constant JR can change. |
| R4 | The beat reporters invent head-coach quotes anyway (old habit from the Quote Hook, S4). | U4 test checks the directive and the brief carry the rule; JR reads the first Postgame Report Card. | Tighten the directive wording; add a post-check that strips quotes attributed to the coach that aren't in the transcript (follow-up). |
| R5 | **Shared files with the sibling plans** (V2.9 inbox, V2.10 scouting, V2.11 trade negotiations, written in parallel): `server/api.py` (router mount), `db/init_db.sql` and the next migration date, `newsroom/agent_memory.py` and `newsroom/swarm_dispatcher.py` (if the inbox or negotiations feed writer memory or briefs), `Mu_Network/src/components/franchise/GameCenterModule.tsx` / `FranchisePage.tsx` (an inbox badge or tab), and possibly `server/franchise_api.py`. | Compare the four plans' ownership tables before running any of them. | This plan keeps its edits to those files tiny and in one wiring unit (U4) in the last wave, adds its own router instead of editing `franchise_api.py`, and does not touch `FranchisePage.tsx`. Build the plans one after another, or renumber migrations at merge. |
| R6 | Migration name clash: V2.9 takes `2027_01_19`; V2.10 / V2.11 may also pick `2027_01_20`. | Check `ls db/migrations` when U3 starts. | Take the next free date; the name is only an order. |
| R10 | V2.9's interfaces (`Kind`, `Trigger`, `jr_statements`'s row shape) change before it is built. | U5 and U4 start only after V2.9's U1 is on main and read the built code first. | Adjust `press.py` and `inbox_comments` to the built shapes; both are small and guarded. |
| R7 | `newsroom/swarm_dispatcher.py` and `agent_memory.py` are also edited by the in-progress Agent Wire voice plan. | `git log` on those files before U4. | U4's edits are a few lines each; rebase or merge them by hand (adjacent-line conflicts are combined; same-line ones go to JR). |
| R8 | `pipeline/weekly_freeze.py` (untracked, someone's work in progress) may freeze weekly context for V3 before the presser is approved. | Ask JR / read it when it lands. | V3 reads `approved_quotes` at its own batch time; nothing in this plan depends on the freeze. |
| R9 | Season-average usage is thin early in the season (Week 1–3). | `STAR_QUIET` and `BREAKOUT` need ≥ 3 earlier games; tested. | Those hooks simply don't fire. |

**Spike first:** none. The design works with templates only; the model is an optional layer with a fallback, and U3's live smoke run (against `madden_test`) measures phrasing time.

## 4. Tests (synthetic, no live DB)

- `tests/test_press_questions.py`:
  - hook selection: a comeback win, a blown-lead loss, a 3-turnover game, a rout, a quiet star (touches ≤ half his average after 3+ games), a breakout, an injury, a record; `RESULT` first, `NEXT_GAME` last, at most `PRESS_QUESTIONS`; same seed → same order;
  - early season: no `STAR_QUIET` / `BREAKOUT` with fewer than 3 earlier games; `null` stats never trigger a hook;
  - reporters: beats first, one national (two in a playoff game), fans / panels / tweet accounts excluded, one account per person key, relationship-holders preferred;
  - templates contain only numbers from the hook's facts;
  - `phrase_questions` with a fake model: a clean rewording is kept (`MODEL`), one with a new number or an unknown name falls back (`TEMPLATE`), bad JSON and a raised error fall back for all;
  - `check_answer`: wrong score, wrong team turnovers, wrong player yards with his last name, right numbers unflagged, unknown (`null`) stats unflagged;
  - `needs_follow_up`: no comment on severity 2 → yes; short answer on severity 1 → no; cap of 2; never on a follow-up; timeout falls back to the template;
  - `game_facts` on a fake cursor: shape, `null` for unrecorded stats, coach from `coach_tenures`.
- `tests/test_press_conference.py`:
  - `prepare` is idempotent per game and team; returns `None` on a bye-only week; `expire_stale` after a later completed game;
  - `answer` / `edit_answer` / `approve` / `skip` state rules and `PressError` cases;
  - `approve` writes one `PRESS` memory row per question with `week` = the game's week, declined and off-record wording, and is idempotent;
  - relationship moves: +0.02 answered, −0.04 declined, once per question (`ref`), none without a coach;
  - `holds_wakeup`: open presser within 24 h → true; approved / skipped / expired → false; after 24 h → false; not a user team or empty list → false;
  - `brief_block`: on-record answered quotes only, verbatim, corrections from flags, `None` once a later game is completed; `skipped_note`;
  - `approved_quotes` shape; `get_press_conference` week cut;
  - the migration and `init_db.sql` both define both tables with the CHECKs and the unique key.
- `tests/test_press_api.py`: every endpoint on fake data (the `test_franchise_api.py` pattern): JSON shapes, 403 for a non-user team, 404 for a conference of another team, 409 answering an approved presser, 400 empty answer, `wakeup_started` on approve and skip (with `start_local_wakeup` patched).
- `tests/test_agent_memory.py` (added cases): the PRESS section appears with the newest 3 exchanges, only before the publication week, and PRESS rows never appear among takes or summary inputs.
- `tests/test_press_wiring.py`: `maybe_start_team_wakeup` returns False while held and starts otherwise (DB and thread patched); the beat brief gets `press_conference` (with inbox `comments` from a patched `jr_statements`) / `press_conference_note`; `record_comment_memory` runs once before a wakeup; `get_press_conference` is in the dispatcher's tool list; the beat directive carries the head-coach rule; the router is mounted (`/api/franchise/{abbr}/press` in `app.routes`).

- `tests/test_inbox_kinds_press.py`: one notice per game (rerun creates nothing, by `source_ref`), none for a closed presser, `SKIP_PRESSER` skips the presser and closes the thread, `start` raises 400, `kinds()` discovers `PRESS`.

Run with `pytest -m "not integration" tests/test_inbox_kinds_press.py tests/test_press_questions.py tests/test_press_conference.py tests/test_press_api.py tests/test_agent_memory.py tests/test_press_wiring.py`. The frontend is checked with `npm run build` in `Mu_Network/`. One manual live smoke run against `madden_test` (U3) is optional.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, `Mu_Network/PROJECT_SPEC.md`, this plan, `CLAUDE.md`, `docs/in_progress/V2_MASTER_PLAN.md` (V2.2 status line).

### U1: Facts, hooks and questions

- **Owns:** `newsroom/press_questions.py` (new), `tests/test_press_questions.py` (new)
- **Reads:** this plan §2.1–§2.6; `engines/game_script_engine.py` (`ensure_script`, `script_summary`); `engines/records_engine.py:294`; `newsroom/db_tools.py` `get_game_box_score` (how a game's lines are read); `newsroom/personas.py` (`load_personas`, `render_persona`); `newsroom/relationships.py` (`SHOWN_MIN`, `NO_SEED_ARCHETYPES`, table layout); `engines/llm_provider.py:314`; `newsroom/config/article_types.json` (`SINGLE_GAME_SCHEMATIC_AUTOPSY`)
- **Depends on:** none
- **Done when:** every §2.2–§2.6 function exists with the exact signature, works on fake cursors and a fake model, and never calls a model when `model` is falsy and `PRESS_PHRASE=0`.
- **Verify:** `pytest -m "not integration" tests/test_press_questions.py` all pass.

### U2: Game Center panel

- **Owns:** `Mu_Network/src/types/press.ts` (new), `Mu_Network/src/components/franchise/PressConferencePanel.tsx` (new), `Mu_Network/src/components/franchise/GameCenterModule.tsx`
- **Reads:** this plan §2.10 and §2.12; `Mu_Network/src/components/franchise/RosterCapModule.tsx` (`franchiseFetch`, `useFranchiseData`, `SharedStyles`, `StateNote`); `GameCenterModule.tsx`; `Mu_Network/src/types/franchiseGame.ts` (style of the type file)
- **Depends on:** none (builds against the §2.10 shapes)
- **Done when:** the panel renders each §2.12 state from the typed shapes, and `GameCenterModule` renders it above the next-game card with one import and one element.
- **Verify:** `cd Mu_Network && npm run build` succeeds with no type errors.

### U3: Lifecycle, tables and API

- **Owns:** `db/migrations/2027_01_20_press_conferences.sql` (new), `db/init_db.sql`, `newsroom/press_conference.py` (new), `server/press_api.py` (new), `tests/test_press_conference.py` (new), `tests/test_press_api.py` (new)
- **Reads:** this plan §2.7–§2.10; U1's `newsroom/press_questions.py`; `server/franchise_api.py` (module docstring, `user_teams`, `require_user_team`, `team_row`, `league_state`, `_connect`); `tests/test_franchise_api.py` (fake-cursor pattern); `newsroom/relationships.py:386–460`; `newsroom/agent_memory.py:297–320` (memory inserts); `db/migrations/2027_01_18_writer_voice.sql` (migration style)
- **Depends on:** U1
- **Done when:** the migration applies cleanly twice to a scratch schema in `madden_test` (manual, optional), all lifecycle functions and endpoints match §2.8 and §2.10, and the router works when mounted on a bare `FastAPI()` in the tests. `server/api.py` is not touched.
- **Verify:** `pytest -m "not integration" tests/test_press_conference.py tests/test_press_api.py tests/test_press_questions.py` all pass.

### U4: Newsroom and server wiring

- **Owns:** `server/api.py`, `newsroom/swarm_dispatcher.py`, `newsroom/agent_memory.py`, `newsroom/db_tools.py`, `tests/test_agent_memory.py`, `tests/test_press_wiring.py` (new)
- **Reads:** this plan §2.8–§2.9; U3's `newsroom/press_conference.py` and `server/press_api.py`; V2.9's built `engines/inbox/store.py` (`jr_statements`) and its `server/api.py` edit; `server/api.py:60–120`, `:140–150`; `newsroom/swarm_dispatcher.py:840–860`, `:1100–1170`, `:1360–1400`; `newsroom/agent_memory.py:135–296`
- **Depends on:** U3
- **Done when:** the router is mounted; the user team's wakeup is held while its presser is open; beat briefs carry the press block or the skipped note; the head-coach rule is in the beat directive; the tool is registered; the memory block shows press exchanges; old behaviour is unchanged when the press tables don't exist.
- **Verify:** `pytest -m "not integration" tests/test_press_wiring.py tests/test_agent_memory.py tests/test_local_portfolio.py tests/test_relationships.py tests/test_press_conference.py` all pass.

### U5: Press inbox kind

- **Owns:** `engines/inbox/kinds/press.py` (new), `tests/test_inbox_kinds_press.py` (new)
- **Reads:** this plan §2.11; V2.9's built `engines/inbox/registry.py`, `store.py`, `kinds/reporter.py` (a sibling kind as the pattern); U3's `newsroom/press_conference.py`
- **Depends on:** U3, and V2.9's U1 on main
- **Done when:** `engines.inbox.registry.kinds()` lists `PRESS`; the notice appears once per presser and closes with it; no file under `engines/inbox/` other than `kinds/press.py` changes.
- **Verify:** `pytest -m "not integration" tests/test_inbox_kinds_press.py tests/test_inbox_registry.py` all pass.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2 | — | No shared files; U2 builds against the §2.10 shapes, not U1's code. Can run before or alongside V2.9 |
| 2 | U3 | U1, and V2.9's U1 (owner of `db/init_db.sql` and `2027_01_19`) | Imports `press_questions`; edits `init_db.sql` after the inbox tables are in it |
| 3 | U4, U5 | U3, and V2.9's U1 and U5 (owner of the inbox's `server/api.py` edit) | U4 edits the shared files (`server/api.py`, `swarm_dispatcher.py`, `agent_memory.py`, `db_tools.py`) and reads `jr_statements`; U5 adds one file to `engines/inbox/kinds/`. No file in common |

**Cross-plan order:** V2.9 builds first (its Q1). This plan's wave 1 needs nothing from it; waves 2 and 3 wait for the V2.9 units named above to be on `main`. This plan never edits `pipeline/weekly_capture.py`, `FranchisePage.tsx` or any existing file under `engines/inbox/`.

**File ownership check:**

| File | Unit |
|---|---|
| `Mu_Network/src/components/franchise/GameCenterModule.tsx` | U2 |
| `Mu_Network/src/components/franchise/PressConferencePanel.tsx` | U2 |
| `Mu_Network/src/types/press.ts` | U2 |
| `db/init_db.sql` | U3 |
| `engines/inbox/kinds/press.py` | U5 |
| `db/migrations/2027_01_20_press_conferences.sql` | U3 |
| `newsroom/agent_memory.py` | U4 |
| `newsroom/db_tools.py` | U4 |
| `newsroom/press_conference.py` | U3 |
| `newsroom/press_questions.py` | U1 |
| `newsroom/swarm_dispatcher.py` | U4 |
| `server/api.py` | U4 |
| `server/press_api.py` | U3 |
| `tests/test_agent_memory.py` | U4 |
| `tests/test_inbox_kinds_press.py` | U5 |
| `tests/test_press_api.py` | U3 |
| `tests/test_press_conference.py` | U3 |
| `tests/test_press_questions.py` | U1 |
| `tests/test_press_wiring.py` | U4 |

No path appears twice.

## 7. Spec changes when built

- §1.1 Repository Layout: `engines/inbox/kinds/press.py`, `newsroom/press_questions.py`, `newsroom/press_conference.py`, `server/press_api.py`, `db/migrations/2027_01_20_press_conferences.sql`, `Mu_Network/src/types/press.ts`, `Mu_Network/src/components/franchise/PressConferencePanel.tsx`, the six test files.
- §3 (new §3.3r Press Conferences): `press_conferences`, `press_questions`; `agent_memory_entries.kind` gains `'PRESS'`.
- §4.6b (or a new §4.6c Press Endpoints): the seven `/api/franchise/{abbr}/press*` endpoints and their shapes.
- §6.0b: no watcher change; the presser for JR's game happens after the post-advance ingest and before the user team's beat wakeup, which is held up to `PRESS_HOLD_HOURS`.
- §7.2: beat briefs carry `press_conference` / `press_conference_note`; head-coach quotes only from the transcript.
- §7.7: the PRESS memory section and the trust moves (`PRESS_ANSWERED`, `PRESS_NO_COMMENT`, `ref = press:<id>`).
- §7 tools list: `get_press_conference`.
- New §7.14 Interactive Press Conferences: hooks table, reporter selection, phrasing guards, follow-ups, number check, lifecycle, `approved_quotes` as the V3 interface, LLM source/labels and env switches (`PRESS_PHRASE`, `LLM_PROVIDER_PRESS`).
- The V2.9 inbox section (wherever its build puts it): the `PRESS` kind and that `jr_statements` now feed beat briefs and writer memory.
- `Mu_Network/PROJECT_SPEC.md`: the Game Center's press conference panel.
- `docs/in_progress/V2_MASTER_PLAN.md` V2.2: status and a link to this plan.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Where does the presser live: a panel at the top of the Game Center, or its own "Press Room" tab? | A Game Center panel. It's the first thing you see after a game, and it keeps `FranchisePage.tsx` (which the inbox plan will likely touch) out of this plan. If V2.9's inbox lands, it can also list "Presser waiting". |
| Q2 | Should anything wait for the presser? | Only your team's beat coverage: the Postgame Report Card and the rest of the beat portfolio wait up to 24 h after ingest, then run without quotes. The national slate is written at ingest as today; national writers can quote the presser from the next week through `get_press_conference`. |
| Q3 | Follow-up questions: phrased live by a model (template if it takes over 10 s), or templates only? | Model with the template fallback, at most 2 per presser. It's the only call made while you wait, and it can be turned off with `PRESS_PHRASE=0`. |
| Q4 | If your answer gets a number wrong against the box score: warn, or block approval? | Warn and let you edit or approve anyway. It's your coach's quote, so it stands, but writers are given the recorded figure and use it for their own statements. |
| Q5 | Once pressers exist, may writers ever invent words for your head coach (for example when you skip)? | No. Your coach is quoted only from an approved presser; a skipped or expired presser tells writers he didn't take questions. Coordinators and players keep today's behaviour. |
| Q6 | Consequences: should "No comment" or skipping cost goodwill? | "No comment" on a question: −0.04 trust with the reporter who asked; an answer: +0.02. Skipping the whole presser: no change. Nothing touches players, morale or any number in the sim. |
| Q7 | Size and cast: how many questions, and who asks? | 5 questions plus up to 2 follow-ups; all your beat reporters, plus 1 national writer (2 in the playoffs), favouring writers who already have a bias toward your team or players. Agent Wire reactions to your quotes come later, after the Agent Wire voice plan, as a follow-up. |
| Q8 | Run the presser inside the V2.9 inbox as a thread, or in its own Game Center panel with an inbox notice? | Own panel plus a `PRESS` inbox notice. A presser has several reporters, follow-ups and a review step; an inbox thread has one counterpart and one reply at a time. The notice makes sure you don't miss it. |
| Q9 | Should your on-record answers to your beat writer in the inbox (V2.9 `REPORTER` threads) be quoted in his articles and remembered by him? | Yes, wired here with the presser quotes (V2.9 left this to V2.2). No trust change for inbox answers yet; that can follow once pressers show how the ±0.02 / −0.04 moves feel. |
