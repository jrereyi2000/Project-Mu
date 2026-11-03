# Project Mu: Scouting Revamp & Combine Interviews Plan (V2.10)

**Status:** approved by JR (2026-11-03), all §8 recommendations accepted; not built. Builds after the inbox plan's U1 (V2.9) is on main.
**Builds on:** PROJECT_SPEC §2.18, §3.3c, §3.3g, §4.6, §5.2; [`FRONT_OFFICE_INBOX_PLAN.md`](./FRONT_OFFICE_INBOX_PLAN.md) (V2.9, unapproved; its U1 must be on main before this plan's wave 1); [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §5.3.2, §6 (V2.0, V2.10); [`04_SCOUTING_AND_DRAFT_PLAN.md`](../done/team_management/04_SCOUTING_AND_DRAFT_PLAN.md); [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) phase 6; [`PERSONALITY_DRAFT_INTEGRATION_PLAN.md`](./PERSONALITY_DRAFT_INTEGRATION_PLAN.md) (unapproved).

**Goal:** Every prospect in the class gets a full written scouting report (traits, strengths, concerns, production, athletic profile, projection, bottom line) in three editions a year, and JR can sit down with prospects himself: formal combine interviews and top-30 visits, run as conversations in the V2.9 front-office inbox (a `PROSPECT` inbox kind), started from the Command Center's Draft War Room. A neutral `SCOUT` inbox kind tells JR when interview windows open and new report editions land. The engines decide every grade, trait score, answer quality and board effect; the LLM only writes the words and, for a typed question, picks which engine-permitted topic it is. Scouts speak as one neutral "Mu Scouting Department" until personality phase 6 gives them names, voices and regional biases; prospects answer from neutral, seeded dispositions until the personality draft integration plan is built. Both have hooks so they plug in later without a schema change.

**Delivers:**
- `db/migrations/2027_01_21_scouting_reports.sql` (mirrored in `db/init_db.sql`): tables `scout_prospect_ids` (permanent prospect IDs), `scout_prospect_ratings` (hidden), `scout_reports`, `scout_interviews`.
- `engines/prospect_ids.py`: `ensure_prospect_ids`, `uid_for`, `person_ref` (permanent prospect ID until the personality lifecycle plan's `source_person_id` takes over, §2.1b).
- `pipeline/prospect_ratings.py`: `store_prospect_ratings(cur, snapshot, draft_season, capture_folder)`, called from `pipeline/load_save.py`.
- `engines/scouting_reports.py`: trait observation model, report builder, templates, LLM rewrite, CLI `python3 -m engines.scouting_reports`.
- `engines/scout_voice.py`: `ScoutVoice`, `department_voice()`, `regional_bias()` (0 until phase 6).
- `engines/scouting_interviews.py`: interview windows, budgets, topics, answer grades, classifier, fact sheets, templates, summary, `prospect_disposition()` hook, `jr_visit_keys()`.
- `engines/inbox/kinds/prospect.py` (`KIND = "PROSPECT"`) and `engines/inbox/kinds/scout.py` (`KIND = "SCOUT"`): the inbox plug-ins V2.9 §2.9 reserves for V2.10.
- `engines/scouting_engine.py`: `default_extras` stores report editions and swaps JR's team's seeded visits for his own.
- `pipeline/weekly_capture.py`: Step 7c rewrites a new edition's top 64 reports with the LLM.
- `engines/scouting_read.py`: `scout_report(cur, prospect_key, through_step=None)` for writers.
- `server/draft_api.py`: `GET /api/draft/prospect/{key}/report`, `GET /api/draft/interviews?team=` (window, budget, eligible prospects, past interviews with their inbox thread ids). Interview writes go through the inbox API (`POST /api/franchise/{abbr}/inbox`, `/reply`, `/close`).
- `Mu_Network`: a Scouting Report section in `ProspectModal.tsx`; an Interviews card in `DraftWarRoomModule.tsx` that starts inbox threads.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | The combine event already stores an interview verdict: `payload.interviews` = `"strong"` if the consensus move is positive, else `"mixed"` (`engines/scouting_events.py:349-354`), and the dossier shows it (`engines/college_stats.py:270`). | JR's own combine interviews must not contradict it. The stored verdict sets the interview's base tone (§2.6). |
| S2 | Top-30 visits are seeded for every team with a GM at steps 25–26, `per_team=6` a step, weighted to needs (`engines/scouting_events.py:381-401`, called from `engines/scouting_engine.py:509-534`). A logged `VISIT` cuts that team's board error to `VISIT_M = 0.75` (`engines/scouting_boards.py:31`, `:105-107`). | JR's visits can reuse this exact effect. For JR's teams, his chosen visits replace that many seeded ones, so his team gets the same 6 a step as every CPU team and the frozen calibration (§2.18) still holds. |
| S3 | `default_extras(cur, out, step, season)` is the only place VISITs and team boards are made, and `_write_step` stores `out` in one transaction; `--replace` redraws a step with the same seeds (`engines/scouting_engine.py:374-437`). | Report editions are written from `default_extras` too, so they are stored, guarded and replaced with their step. JR's visits must be re-read the same way on a `--replace`. |
| S4 | The only callers that store steps are Step 7c `step_scouting` (`pipeline/weekly_capture.py:440-475`, with `llm = make_llm("scouting_notes")`) and the Draft Room lock (`engines/draft_room.py:492`, no llm). | The LLM pass for reports goes in `step_scouting` after `catch_up`, gated to edition steps. The lock path (step 27) has no edition, so nothing is missed. |
| S5 | `draft_prospects` has `ovr` but no per-rating data; `player_attributes` keys on `players(id)` (`db/migrations/2027_01_06_player_attributes.sql:17-30`), and prospects have no `players` row until drafted. `load_save.load_draft_class` reads `snapshot["players"]` with status `Draft` (`pipeline/load_save.py:749-781`), and `store_player_attributes` reads `p["ratings"]` from the same list (`pipeline/load_save.py:448-453`). | Trait grades need a new hidden table keyed by `prospect_key`. The snapshot most likely carries prospect ratings already (spike X2 confirms). PS5-scanned classes have none: reports fall back to traits-free mode. |
| S6 | `validate_scout_text(text, sheet, league)` rejects numbers, schools, NFL teams and heights not on a fact sheet, ongoing injuries, OVR/Madden/ratings words, charges and "freshman" (`engines/scouting_narrative.py:76-125`). | Reports and interview answers reuse it unchanged, with their own fact sheets. No edit to `scouting_narrative.py`. |
| S7 | `gm_llm.make_llm(label)` returns a prompt→text callable or None; `call(llm, prompt)` swallows errors (`engines/gm_llm.py:63-102`). No timeout parameter. Provider per label comes from `engines/llm_provider.resolve_provider` (`LLM_PROVIDER_<LABEL>`). | The typed-question classifier wraps `gm_llm.call` in a thread with a timeout so a slow model falls back to `OFF_TOPIC` (§5.3.2). Interview answers are written by the inbox voice (its own budget). New labels: `scout_report`, `draft_interview_classify`. |
| S7b | The V2.9 inbox plan (`docs/not_started/FRONT_OFFICE_INBOX_PLAN.md` §2.1–§2.9) fixes `inbox_threads` / `inbox_messages`, a `Kind` protocol (`scan`, `options`, `act`, `template`, `start`) auto-discovered from `engines/inbox/kinds/`, `counterpart_type` values `prospect` and `scout`, `link_table`/`link_id`, and reserves `prospect.py` and `scout.py` for V2.10. Its `voice.write` validates LLM text with `gm_rationale.validate_rationale` and a budget `INBOX_LLM_BUDGET_S`, LLM off unless `MU_INBOX_LLM=1`. It claims migration `2027_01_19` and owns `db/init_db.sql`, `pipeline/weekly_capture.py`, `server/api.py`, `FranchisePage.tsx` in its U1/U2/U5. | Interviews are an inbox kind: no own conversation tables, API or chat UI. Our migration is `2027_01_21` (`2027_01_20` is the V2.2 press conference plan's). Edits to `weekly_capture.py` and `init_db.sql` wait for inbox U1 on main. Scouting needs its own validator (heights, schools, ongoing injuries), so the PROSPECT kind needs an optional `validate` hook in `voice.write` (§2.8). |
| S8 | `GET /api/draft/prospect/{key}` returns `college_stats.prospect_dossier` (`server/draft_api.py:259-268`); `ProspectModal.tsx` fetches it (`Mu_Network/src/components/draft/ProspectModal.tsx:76`) and is shared by Draft Central and the War Room module (`Mu_Network/src/components/franchise/DraftWarRoomModule.tsx:10`). | One new section in `ProspectModal` shows the report in both places. The report has its own endpoint so `college_stats.py` stays untouched. |
| S9 | Franchise writes are gated by `require_user_team` (403 for a team outside the active franchise's `user_teams`, an empty list allows all; `server/franchise_api.py:213-226`). War Room writes use `X-Debug-Token` (`server/draft_api.py:636-680`). | Interviews are JR acting as his team; the inbox API already applies `require_user_team`. The read endpoint `GET /api/draft/interviews` applies it too. |
| S10 | Personality core supports role `scout` (`personality/__main__.py:9`); scout categories (Traits Chaser, Tape Grinder, Character Hawk, Floor Seeker / Ceiling Gambler) arrive in phase 6 (`docs/in_progress/PERSONALITY_PLAN.md:232`, `:264`). No scout profiles exist. | Reports carry a nullable `scout_id` and `voice` now; phase 6 fills them. No scout names are invented here. |
| S11 | The personality draft plan says current interview text "is not personality evidence" and later interview features must "observe pre-existing truth" (`docs/not_started/PERSONALITY_DRAFT_INTEGRATION_PLAN.md:24`, `:100`); progression says interview answers "reveal traits the engine already set, never change them" (`docs/in_progress/PROGRESSION_PLAN.md:397`). That plan also owns `pipeline/load_save.py` and `engines/scouting_engine.py` in its U3 (`:178`). | Interviews never write personality. The hook reads a scoped view or returns None. A class's disposition source is fixed by its first interview so it never switches mid-class. File overlap is a merge risk (R6). |
| S12 | The GPU queue has no production handlers registered anywhere (`pipeline/gpu_queue/runner.py:89`; `grep register_handler` finds only the runner). | The report LLM pass runs in Step 7c like `scouting_notes`, capped at 64 a edition, plus a CLI for more. Moving it to the queue is V3 work. |
| S11b | `prospect_key` = `draft_season:NAMEKEY:GROUP` (`engines/scouting_value.py:55-57`) breaks on a rename, a position-group change and same-name collisions (`docs/not_started/PERSONALITY_DRAFT_INTEGRATION_PLAN.md` S7). That plan allocates an immutable `source_person_id` UUID at verified first entry (`:51`), may derive it deterministically from first-seen class and pgid once continuity is verified (`:51`), binds aliases in `persona_lifecycle_bindings` (`:67`, `:70`), and builds its store in U2 with migration `2027_01_24_personality_lifecycle.sql` (`:173`). It is unapproved and gated by reviewed manifests. | JR wants a permanent ID. This plan keys its own rows and the PROSPECT thread on a permanent UUID, not on `prospect_key` (§2.1b). |
| S13 | Latest migration is `db/migrations/2027_01_18_writer_voice.sql`; V2.9 claims `2027_01_19`. Migrations are mirrored in `db/init_db.sql` (`scout_runs` at `db/init_db.sql:1039`). | Reserved: inbox `2027_01_19`, press conferences `2027_01_20`, personality relationships `2027_01_18`, contracts `2027_01_23`, lifecycle `2027_01_24`, trade requests `2027_01_25`, retirement `2027_01_26`. This plan takes `2027_01_21_scouting_reports.sql` and mirrors it in `init_db.sql` (R5). |

## 1. Scope

**In:**
- Hidden per-prospect ratings stored from the save at board load.
- A trait observation model (noisy, shrinking with the step, class-relative 1–9 scores and words).
- Three report editions per class (`FALL`, `BOWLS`, `FINAL`) for every non-withdrawn prospect: template prose for all, LLM prose for the consensus top 64 of each edition, CLI for more.
- Public report read API, writer read helper, report section in the prospect modal.
- JR's formal combine interviews (window: step 24 stored, step 25 not yet) and top-30 visits (windows: steps 24 and 25 stored), with budgets, a topic menu, typed questions classified to topics, engine answer grades, answers in the prospect's voice (inbox voice, template fallback), and an engine-graded scout summary, all as `PROSPECT` inbox threads.
- A neutral `SCOUT` inbox kind: window-open notices, report-edition notices, and a shortlist on request.
- JR's visits replacing his team's seeded visits, with the existing `VISIT_M` board effect.
- Hooks: `scout_voice.ScoutVoice` / `regional_bias()` for phase 6 scouts; `scouting_interviews.prospect_disposition()` for the personality draft plan.
- An Interviews card in the Command Center's Draft War Room module (budget, window, eligible prospects, "Interview" buttons that start inbox threads).

**Out (non-goals):**
- Named scouts, scout personalities, regional bias numbers: personality phase 6, then a follow-up to this plan.
- Prospect personality traits and any write to personality profiles: `PERSONALITY_DRAFT_INTEGRATION_PLAN.md`. Interview text and grades are never personality evidence (§2.6).
- Allocating `source_person_id` or writing `persona_lifecycle_*` rows: the lifecycle plan (§2.1b says how it adopts this plan's IDs).
- Re-keying the existing scouting tables (`scout_prospects`, `scout_consensus`, `scout_events`, …) off `prospect_key`: a separate change; this plan only stops new tables depending on it.
- CPU teams' combine interviews (they keep their seeded visits only).
- Pro-player comparisons in reports (Q7).
- Writers quoting JR's interviews or leaking them: V2.2 / V3 media.
- The inbox itself (tables, registry, voice, API, Inbox module): V2.9.
- Changing consensus, team-board constants, `true_value` or any existing event text.
- Moving the LLM pass onto the GPU queue: V3.
- Anything in Madden's menus; nothing here writes the save.

## 2. Design

### 2.1 Schema (`db/migrations/2027_01_21_scouting_reports.sql`)

File `db/migrations/2027_01_21_scouting_reports.sql`, mirrored in `db/init_db.sql` after the scouting tables. Idempotent (`CREATE TABLE IF NOT EXISTS`), one `BEGIN … COMMIT`, header like `2026_12_12_scouting.sql`. Needs V2.9's `inbox_threads` (FK below).

```sql
-- Permanent prospect IDs (§2.1b). prospect_key is only the current alias into the older scouting tables.
CREATE TABLE IF NOT EXISTS scout_prospect_ids (
    prospect_uid     UUID PRIMARY KEY,                 -- uuid5(MU_PROSPECT_NS, birth_key); never changes
    birth_key        TEXT NOT NULL UNIQUE,             -- 'save-pgid:<draft_season>:<pgid>' or 'board:<draft_season>:<first prospect_key>'
    draft_season     INT NOT NULL,
    pgid             BIGINT,                           -- NULL for a scanned (PS5) class
    prospect_key     VARCHAR(80) NOT NULL,             -- current alias
    key_history      JSONB NOT NULL DEFAULT '[]',      -- [{"prospect_key","until_capture"}]
    status           VARCHAR(12) NOT NULL DEFAULT 'OK' CHECK (status IN ('OK','KEY_COLLISION')),
    source_person_id UUID,                             -- set by the lifecycle plan when it adopts this person
    first_seen       VARCHAR(255) NOT NULL,            -- capture folder
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS scout_prospect_ids_key ON scout_prospect_ids (draft_season, prospect_key);

-- Hidden: never in a view, endpoint, writer tool or #debug panel.
CREATE TABLE IF NOT EXISTS scout_prospect_ratings (
    prospect_uid   UUID PRIMARY KEY REFERENCES scout_prospect_ids(prospect_uid),
    draft_season   INT NOT NULL,
    prospect_key   VARCHAR(80) NOT NULL,            -- alias at load time
    pgid           BIGINT,
    ratings        JSONB NOT NULL,          -- Madden Player.<Name>Rating names without "Rating", 0-99
    capture_folder VARCHAR(255) NOT NULL,
    loaded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scout_reports (
    draft_season   INT NOT NULL,
    step           SMALLINT NOT NULL,
    prospect_uid   UUID NOT NULL REFERENCES scout_prospect_ids(prospect_uid),
    prospect_key   VARCHAR(80) NOT NULL,            -- alias at the step, for joins with scout_consensus/scout_events
    edition        VARCHAR(8) NOT NULL CHECK (edition IN ('FALL','BOWLS','FINAL')),
    grade          NUMERIC(5,1) NOT NULL,   -- the step's consensus grade (copied, public)
    rank           INT NOT NULL,            -- the step's consensus rank (public)
    round_label    VARCHAR(16) NOT NULL,
    role           VARCHAR(24) NOT NULL,    -- ROLE_BANDS key (§2.3)
    traits         JSONB NOT NULL,          -- [{"trait","label","score","word"}], [] without ratings
    strengths      JSONB NOT NULL,          -- ["trait", ...] 0-3
    concerns       JSONB NOT NULL,          -- ["trait" or flag, ...] 0-3
    flags          JSONB NOT NULL,          -- ["INJURY_HISTORY", ...] (§2.3)
    body           JSONB NOT NULL,          -- {"summary","strengths","concerns","production","athletic","projection","bottom_line"}
    text_source    VARCHAR(8) NOT NULL DEFAULT 'TEMPLATE' CHECK (text_source IN ('TEMPLATE','LLM')),
    scout_id       INT,                     -- NULL = the Mu Scouting Department (phase 6 hook)
    voice          JSONB,                   -- NULL = department voice (phase 6 hook)
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (prospect_uid, edition),
    FOREIGN KEY (draft_season, step) REFERENCES scout_runs (draft_season, step) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scout_interviews (
    id               SERIAL PRIMARY KEY,
    draft_season     INT NOT NULL,
    team_id          INT NOT NULL REFERENCES teams(id),
    prospect_uid     UUID NOT NULL REFERENCES scout_prospect_ids(prospect_uid),
    prospect_key     VARCHAR(80) NOT NULL,          -- alias when opened (VISIT events are written under the current alias)
    kind             VARCHAR(8) NOT NULL CHECK (kind IN ('COMBINE','VISIT')),
    thread_id        INT REFERENCES inbox_threads(id) ON DELETE SET NULL,   -- the PROSPECT inbox thread
    window_step      SMALLINT NOT NULL,     -- latest stored step when opened (24 or 25)
    logged_step      SMALLINT,              -- VISIT: the step whose VISIT event it became (25 or 26); NULL until then
    status           VARCHAR(8) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED')),
    questions_max    SMALLINT NOT NULL,
    tone             VARCHAR(8) NOT NULL CHECK (tone IN ('STRONG','STEADY','MIXED')),
    disposition_src  VARCHAR(12) NOT NULL DEFAULT 'NEUTRAL' CHECK (disposition_src IN ('NEUTRAL','PERSONALITY')),
    turns            JSONB NOT NULL DEFAULT '[]',   -- [{"seq","topic","grade","classified_by"}]; words live in inbox_messages
    summary_grade    VARCHAR(12),           -- ANSWER_GRADES value, the interview's overall read (set on close)
    opened_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at        TIMESTAMPTZ,
    UNIQUE (draft_season, team_id, prospect_uid, kind)
);
```

`scout_reports` cascades with its step, so `scouting_engine --replace` redraws an edition exactly (templates are seeded; `_write_step`'s `keep_llm` pattern keeps LLM text, §2.5). Interviews do not cascade: they are JR's actions, not a step's output. The words of an interview (JR's questions, the prospect's answers, the scout's summary) are `inbox_messages` of its thread, each answer with `engine_ref = {"interview_id", "topic", "grade", "classified_by"}`; `scout_interviews.turns` keeps the engine's record so grades never depend on the inbox.

### 2.1b Permanent prospect ID (`engines/prospect_ids.py`, U1)

JR wants one ID per prospect that never changes. The target is the lifecycle plan's immutable `source_person_id`. That plan is unapproved and gated by reviewed manifests, so this plan cannot wait for it. **Recommended (and planned here): allocate a permanent `prospect_uid` now, derived the same way the lifecycle plan allows a native `source_person_id` to be derived, and have the lifecycle plan adopt the same value.** The alternative, making V2.10 depend on the lifecycle plan's U1–U2 (and in practice its U3 load hooks, since U1–U2 alone allocate nothing for new classes), would block reports and interviews behind an unapproved plan and its review gates.

- **Allocation.** `birth_key` = `save-pgid:<draft_season>:<pgid>` for a PC class (pgid is unique within a class in one save), else `board:<draft_season>:<prospect_key at first sight>` for a scanned class. `prospect_uid = uuid5(MU_PROSPECT_NS, birth_key)`, with `MU_PROSPECT_NS` a fixed UUID constant in `engines/prospect_ids.py`. It is written once and never recomputed: a later rename, position-group change or pgid move does not change it.
- **`ensure_prospect_ids(cur, draft_season, rows, capture_folder) -> dict`.** `rows` = `[{"prospect_key","pgid"}]` from the board load. A PC row matches its existing person by pgid and updates the `prospect_key` alias if it changed (the old one goes to `key_history`); a scanned row matches by `prospect_key`; anything unmatched is allocated. Two rows with the same `prospect_key` in one class are both marked `KEY_COLLISION`: they get no report and can't be interviewed until the old scouting tables stop keying on names (S11b); the load report counts them. Returns `{"allocated","renamed","collisions"}`. Called from `pipeline/load_save.py` before `store_prospect_ratings`.
- **`uid_for(cur, draft_season, prospect_key) -> UUID | None`** (the current alias) and **`person_ref(cur, prospect_uid) -> str`**: `str(source_person_id)` once the lifecycle plan has set it, else `str(prospect_uid)`.
- **Adoption by the lifecycle plan (its work, not built here).** When it verifies a prospect, it should use `prospect_uid` as that person's `source_person_id` (the derivation is from first-seen class and pgid, which its §2 allows once continuity is verified), set `scout_prospect_ids.source_person_id`, and bind the alias `scout-uid`/`prospect_uid`. Then `source_person_id == prospect_uid`, and no thread, report or rating ever changes its key. If its verification instead allocates a different UUID, it sets `source_person_id` to that value and binds `scout-uid`/`prospect_uid` → it; `person_ref` returns the new ID for new threads; old threads keep `prospect_uid` and resolve through the binding. The orchestrator should add this adoption step to the lifecycle plan's §2 (identity rules) when it is next revised.
- **Where it is used.** `scout_prospect_ratings`, `scout_reports` and `scout_interviews` key on `prospect_uid`; the PROSPECT inbox thread's `counterpart_id` is `person_ref(prospect_uid)` (S7b: the parent updates V2.9's column comment). `prospect_key` stays only as the alias for joins with the older scouting tables at that step, and in public URLs (`/api/draft/prospect/{key}`, which resolves through `uid_for`; responses also carry `prospect_uid`).
- U2 and U3 never import this module (same wave). They read `scout_prospect_ids` with SQL and take `prospect_uid` as a parameter; U4 and U5 call `engines/prospect_ids.py`.

### 2.2 Prospect ratings (`pipeline/prospect_ratings.py`, U1)

- `prospect_ratings(snapshot, draft_season) -> list[dict]`: pure. For each `snapshot["players"]` row with `status == "Draft"` and a non-empty `ratings`, `{"prospect_key": scouting_value.prospect_key(draft_season, full_name, position), "pgid", "ratings"}`. Name and position are built exactly as `load_save.draft_board_prospects` builds them (`f"{first_name} {last_name}".strip()`, Mu position), so keys match `scout_prospects`.
- `store_prospect_ratings(cur, snapshot, draft_season, capture_folder) -> int`: calls `prospect_ids.ensure_prospect_ids` for the class, then upserts every row by `prospect_uid` (`ON CONFLICT (prospect_uid) DO UPDATE` ratings, prospect_key, pgid, capture_folder), skipping `KEY_COLLISION` people; returns the count. Skips silently (returns 0) when the table doesn't exist (`to_regclass`). Runs on every load, not only when the board is new: prospect ratings don't change before the draft, and an upsert is idempotent.
- `pipeline/load_save.py`: one call in the main load, right after `load_draft_class(...)` (`pipeline/load_save.py:923`), `report["prospect_ratings"] = store_prospect_ratings(cur, snapshot, season + 1, capture_folder)`, and the key added to the report summary list (`:932`). A scanned (PS5) class gets its IDs from `pipeline/load_capture.load_prospects` (no ratings): U4 adds `ensure_prospect_ids` there next to the existing `scouting_engine.relink` call.

### 2.3 Reports (`engines/scouting_reports.py`, U2)

**Constants.**
```python
EDITIONS = {"FALL": None, "BOWLS": 21, "FINAL": 26}   # FALL = the class's first stored step
SIGMA_T0, SIGMA_T_DRAFT, RHO_T = 8.0, 4.0, 0.5         # rating points; estimates, checked by spike X3
LLM_TOP = 64                                           # consensus top N rewritten by the LLM per edition
ROLE_BANDS = ((15, "IMPACT_STARTER"), (48, "DAY1_STARTER"), (100, "EARLY_CONTRIBUTOR"),
              (180, "DEVELOPMENTAL"), (260, "DEPTH"), (None, "PRIORITY_FA"))
SCORE_WORDS = ((8.5, "elite"), (7, "plus"), (5, "solid"), (3.5, "below average"), (0, "a concern"))
```
`ROLE_WORDS` maps each role to a phrase ("Day 1 starter", "developmental with starter upside", …).

**Traits.** `TRAITS[group]` lists 4–6 traits per `scouting_events.stat_group`, each `(trait, label, [rating names], [weights])`. Draft list (exact Madden keys confirmed by spike X1):
- QB: `arm` (ThrowPower), `accuracy` (ThrowAccuracyShort/Mid/Deep), `processing` (Awareness, PlayAction), `pocket` (ThrowUnderPressure, BreakSack), `mobility` (Speed, ThrowOnTheRun).
- RB: `burst` (Acceleration, Speed), `elusiveness` (JukeMove, SpinMove, ChangeOfDirection), `power` (Trucking, BreakTackle), `hands` (Catching, CarryingAbility), `vision` (BallCarrierVision).
- WR/TE: `speed`, `routes` (ShortRouteRunning, MediumRouteRunning, DeepRouteRunning), `hands` (Catching, CatchInTraffic), `release`, `contested` (SpectacularCatch, Jumping); TE adds `blocking` (RunBlock, PassBlock).
- OT/IOL: `pass_pro` (PassBlock, PassBlockPower, PassBlockFinesse), `run_block` (RunBlock, RunBlockPower, RunBlockFinesse, ImpactBlocking), `strength`, `feet` (Agility, Awareness).
- EDGE/DT: `get_off` (Acceleration, Speed), `power` (PowerMoves, Strength), `finesse` (FinesseMoves), `shed` (BlockShedding), `instincts` (PlayRecognition, Pursuit).
- LB: `range` (Speed, Pursuit), `instincts` (PlayRecognition, Awareness), `tackling` (Tackle, HitPower), `coverage` (ZoneCoverage, ManCoverage), `shed` (BlockShedding).
- CB/S: `speed`, `man` (ManCoverage, Press), `zone` (ZoneCoverage, PlayRecognition), `ball_skills` (Catching, Jumping), `tackling` (Tackle, Pursuit).
- K/P: `power` (KickPower), `accuracy` (KickAccuracy). LS: `snapping` (Awareness).

**Observation.** For prospect `p`, trait `t`, step `s`: `true_t = Σ w·rating / Σ w`; `obs_t = true_t + σ_T(s) · (RHO_T · ẑ_C + √(1 − RHO_T²) · z_t)`, where `σ_T` falls linearly from `SIGMA_T0` at the class's first step to `SIGMA_T_DRAFT` at step 27 (never below), `ẑ_C` = the prospect's consensus error over its class sd at step `s` (the same input `scouting_boards.board` uses, so a prospect the consensus overrates also looks better trait by trait), and `z_t` = a unit normal seeded by `(prospect_uid, trait)` and **fixed across editions**, so reports sharpen rather than flip. `score` = the prospect's percentile of `obs_t` among the class's same-group prospects mapped to 1.0–9.0 (one decimal); `word` from `SCORE_WORDS`. Class-relative scores never expose a rating.

**Report fields** (`build_report(p, cons_row, events, obs, dossier_bits, edition, step) -> dict`, pure):
- `grade`, `rank`, `round_label`: the step's consensus row.
- `role`: first `ROLE_BANDS` entry with `rank ≤ limit`.
- `strengths`: up to 3 traits with `score ≥ 7`, highest first; `concerns`: up to 2 traits with `score ≤ 3.5`, lowest first, then flags.
- `flags` (public facts only): `INJURY_HISTORY` (a stored `STORYLINE` subtype `injury` or a `MEDICAL` event), `CONSISTENCY` (`STORYLINE` subtype `character` or `rough_outing`), `INTERVIEWS_MIXED` (`COMBINE` payload `interviews == "mixed"`, `FINAL` only), `SMALL_SCHOOL` (school `level` not FBS), `AGE` (age ≥ 24), `VOLATILE` (consensus-move sd in the class's top quartile; `scouting_engine.volatility`).
- `body`: template sentences per section from the fields above, `college_stats.aggregate_games(...)["summary_line"]` (production) and `college_stats.combine_profile(...)` (`FINAL` and only if the prospect has a `COMBINE` event: forty, RAS word). No section mentions a number the fact sheet doesn't hold.
- Without stored ratings (PS5 classes): `traits = []`, strengths/concerns from flags and production only.

**Functions.**
- `edition_for(step, first_step) -> str | None`: `"FALL"` when `step == first_step`, `"BOWLS"` at 21, `"FINAL"` at 26, else None. A class first initialized after step 21 gets no `FALL`/`BOWLS`.
- `load_ratings(cur, draft_season) -> {prospect_key: (prospect_uid, ratings)}` (current aliases, joined through `scout_prospect_ids`; collisions left out); `{}` without the table.
- `build_edition(cls, cons, events_by_key, ratings, uids, step, first_step, draft_season, vols) -> list[dict]`: pure; one row per non-`WITHDRAWN` prospect that has a `prospect_uid` in `uids` (`{prospect_key: uid}`, `OK` status only). `cls`/`cons`/`events` are the shapes in `scouting_engine.compute`'s `out`. `vols` = `scouting_engine.volatility`'s dict.
- `report_rows(reports) -> list[tuple]` in `scout_reports` column order (for `_write_step`).
- `fact_sheet(report, prospect) -> dict`: extends `scouting_narrative.fact_sheet` with the report's public fields (trait words and scores, production line, combine marks, role words).
- `rewrite(cur, draft_season, edition, llm, limit=LLM_TOP, keys=None) -> int`: for the edition's consensus top `limit` (or `keys`) still `TEMPLATE`, asks the LLM (label `scout_report`) to rewrite `body.summary` and `body.bottom_line` in `voice` (department voice when NULL), validates each with `validate_scout_text`, updates `body` and `text_source='LLM'`; returns the count rewritten. Never touches any other field.
- `report_for(cur, prospect_uid, edition=None) -> dict | None`: the latest (or named) edition, public fields only.
- CLI: `python3 -m engines.scouting_reports [--class 2037] [--edition FINAL] [--rewrite] [--limit N] [--dry-run]`. Without `--rewrite` prints counts by edition, role and flag (never a rating). `--rewrite` runs `rewrite` with `make_llm("scout_report")`.

### 2.4 Scout voice hook (`engines/scout_voice.py`, U2)

```python
@dataclass(frozen=True)
class ScoutVoice:
    scout_id: int | None = None      # None = department
    name: str | None = None
    region: str | None = None        # a REGIONS key, phase 6
    lens: str | None = None          # "TRAITS" | "TAPE" | "CHARACTER" | "FLOOR" | "CEILING", phase 6

def department_voice() -> ScoutVoice
def voice_prompt(voice: ScoutVoice) -> str           # department: "a neutral national scouting service"
def regional_bias(voice: ScoutVoice, prospect: dict) -> float   # 0.0 now; phase 6: a grade shift for that scout's own report only
REGIONS = {}                                          # conference -> region, filled in phase 6
```
Phase 6 wiring (not built here): area scouts per team, each with a region and lens; a team-private report row per scout (`scout_id` set) whose grade is `consensus grade + regional_bias(...)` and whose prose uses `voice_prompt`. The consensus and the department report never change.

### 2.5 Wiring into the step (`engines/scouting_engine.py`, `pipeline/weekly_capture.py`, U4)

- `default_extras`, after the team boards: `ed = scouting_reports.edition_for(step, out["first_step"])`; if `ed`, `out["reports"] = scouting_reports.build_edition(cls, cons, out["events"], scouting_reports.load_ratings(cur, draft_season), uids, step, out["first_step"], draft_season, vols)` with `uids` = `{prospect_key: prospect_uid}` for the class's `OK` people (`prospect_ids`).
- `_write_step`: inserts `out.get("reports")` into `scout_reports`; with `keep_llm` (the `--replace` path) it carries forward each prospect's `body`/`text_source` when the old row was `LLM` and its `grade`/`rank` are unchanged (they are, same seeds).
- `default_extras` visits: for a team in `user_teams()` (via `db.franchises.active_entry`, same as `franchise_api.user_teams`), `jr = scouting_interviews.jr_visit_keys(cur, draft_season, step, tid)`; the team's visits for the step = `jr` + the first `VISITS_PER_STEP − len(jr)` of its seeded draw that aren't in `jr`. `jr_visit_keys` also stamps `logged_step = step` on the interviews it returns, so a `--replace` of that step reads the same set. CPU teams are unchanged. `VISITS_PER_STEP = 6` (moved from the `per_team` default to a named constant in `scouting_events`? No: `scouting_events.py` stays untouched; `scouting_interviews.VISITS_PER_STEP = 6` mirrors it and a test asserts they're equal).
- `pipeline/weekly_capture.step_scouting`: after `catch_up`, for each stored step with an edition, `scouting_reports.rewrite(cur, s + 1, ed, make_llm("scout_report"))`, then commit; a failure only logs a warning. About 64 calls, three times a season (estimate).

### 2.6 Interviews (`engines/scouting_interviews.py`, U3)

**Windows and budgets.**
```python
COMBINE_STEP, VISIT_STEPS = 24, (24, 25)       # the latest stored step when an interview can be opened
COMBINE_BUDGET = 15                            # formal combine interviews per team per class
VISITS_PER_STEP = 6                            # = scouting_events.visits per_team
QUESTIONS = {"COMBINE": 4, "VISIT": 6}
ANSWER_GRADES = ("IMPRESSIVE", "SOLID", "GUARDED", "CONCERNING")
```
- `window(cur, draft_season) -> {"latest_step", "combine_open": bool, "visit_open": bool, "visit_for_step": 25 | 26 | None}`. A visit opened while step 24 is the latest becomes a step-25 VISIT; while 25 is latest, a step-26 VISIT.
- `budget(cur, draft_season, team_id) -> {"combine_used", "combine_left", "visits_used", "visits_left"}`; visits counted per `visit_for_step`.
- Eligibility: COMBINE needs a stored `COMBINE` event (invited, consensus top 320). VISIT needs the prospect `ACTIVE` and in the consensus top 150 at the latest step. One interview per (team, prospect, kind).

**Tone (engine).** `tone_for(prospect, events, disposition)`: with a `COMBINE` event, `"STRONG"` if `payload.interviews == "strong"` else `"MIXED"` (S1, never contradicted); without one, seeded by `(prospect_uid, "tone")`: 40% `STRONG`, 40% `STEADY`, 20% `MIXED`. When `disposition` is not None, the personality mapping replaces the seeded draw but still never contradicts a stored combine verdict.

**Topics (engine-permitted options).** `topics(prospect, events, kind) -> [{"topic","question","fact"}]`. Always: `FOOTBALL_IQ` ("Put your favourite play on the whiteboard."), `SCHEME` ("How would you fit what we do?"), `WORK_HABITS`, `LEADERSHIP`, `PRESSURE` ("Walk me through your worst game", with the worst game from `college_stats.aggregate_games` game log as `fact`). Only when a fact exists: `INJURY` (injury storyline or MEDICAL; the answer may only say it's healed), `CONSISTENCY` (character or rough-outing storyline), `DECLARE` (a `DECLARE` event), `COMPETITION` (non-FBS school or Shrine Bowl), `CAPTAIN` (captain storyline). `OFF_TOPIC` is never in the menu; it is the classifier's fallback.

**Answer grade (engine).** `answer_grade(tone, topic, prospect, seq, draft_season, disposition) -> str`: base score `{STRONG: 0.6, STEADY: 0.2, MIXED: −0.3}` + topic tilt (`CAPTAIN` +0.4, `LEADERSHIP` +0.2 with a captain storyline, `CONSISTENCY` −0.4, `INJURY` 0, `OFF_TOPIC` −0.2) + `0.5 · z` seeded by `(prospect_uid, topic)` (so the same question gets the same grade from any team and on any retry) ; cut points `> 0.6` IMPRESSIVE, `> 0.0` SOLID, `> −0.5` GUARDED, else CONCERNING. The disposition hook adds a per-topic tilt when present. Constants are estimates; test T-I4 checks the grade mix per tone.

**Classifier (typed questions).** `classify(text, menu, llm=None) -> (topic, classified_by)`: keyword rules first (`RULE`; e.g. "injur|ankle|knee|medical" → `INJURY` if in the menu); else, when an llm is given, it gets the menu's topic ids and answers one id (label `draft_interview_classify`, run through `timed_call(llm, prompt, CLASSIFY_TIMEOUT_S = 4.0)`, a worker thread around `gm_llm.call`); an id not in the menu → `OFF_TOPIC` (`LLM`); no llm or a timeout → `OFF_TOPIC` (`FALLBACK`). The LLM chooses only among engine-permitted ids.

**Facts and templates (no LLM here).** The words are written by the inbox voice (§2.8); this module supplies what it needs:
- `answer_facts(prospect, topic_row, grade, team) -> dict`: `scouting_narrative.fact_sheet(...)` + `topic`, the topic's `fact`, `grade`, JR's team `(abbr, name)`, plus the `gm_rationale` sheet keys the inbox validator expects (`numbers` = every number on the scouting sheet, `assets` = the prospect's name, `money` = []).
- `TEMPLATES[topic][grade]` and `answer_template(facts) -> str`: 2–3 first-person sentences per topic and grade, formatted only from the facts.
- `validate_answer(text, facts) -> (ok, why)`: `scouting_narrative.validate_scout_text(text, facts)` plus a grade check (an `IMPRESSIVE`/`SOLID` answer must not contain the `CONCERN_WORDS` list, a `CONCERNING` one must not be all praise: a keyword check like the inbox's intent check).
- `summary_facts(interview, prospect)` / `summary_template(facts)`: the department scout's 2–3 sentence read from `summary_grade` and the best and worst topics.

**Flow (DB, called by the PROSPECT kind).**
- `open_interview(cur, draft_season, team_id, prospect_uid, kind) -> dict`: checks window, budget, eligibility (raises `InterviewError(code, message)`: `WINDOW_CLOSED`, `NO_BUDGET`, `NOT_ELIGIBLE`, `DUPLICATE`); fixes `tone`, `disposition_src`, `questions_max`; inserts the `scout_interviews` row; returns it with the topic menu.
- `ask(cur, interview_id, topic=None, text=None, llm=None) -> dict`: one of `topic` (menu, `MENU`) or `text` (classified); refuses a closed interview or one with no questions left (`NO_QUESTIONS`); appends `{seq, topic, grade, classified_by}` to `turns`; returns that plus `answer_facts` and questions left. The same topic twice gets the same grade (seeded) and an "as I said" template lead.
- `close(cur, interview_id) -> dict`: `summary_grade` = the mean of the turn grades (IMPRESSIVE 3 … CONCERNING 0, rounded), status `CLOSED`; returns `summary_facts`. An interview still `OPEN` when its window closes is closed by the next `window()`/`open_interview` call for that team.
- `link_thread(cur, interview_id, thread_id)`: sets `thread_id`.
- `jr_visit_keys(cur, draft_season, step, team_id) -> list[str]`: VISIT interviews of the team with `logged_step = step`, plus unlogged ones whose `window_step = step − 1`, which it stamps `logged_step = step`; at most `VISITS_PER_STEP`, oldest first. Returns each prospect's **current** `prospect_key` (from `scout_prospect_ids`), because VISIT events still key on the alias.
- `eligible(cur, draft_season, team_id, limit=150) -> list[dict]`: prospects JR can still interview, with the kinds open to each.
- `prospect_disposition(cur, prospect_uid) -> dict | None`: returns None now. The personality draft plan replaces the body with a read of the prospect's scoped `scouting` view (`personality.core`, looked up by `person_ref`), never a write, returning `{"tone": ..., "tilts": {topic: float}}`. Rule: a class whose first interview stored `NEUTRAL` stays `NEUTRAL` (the `disposition_src` of the class's earliest interview wins).

**Interviews are never personality evidence.** Interview text, answer grades, summaries, tone and `turns` are never read as evidence by the personality core or the lifecycle plan, and never create, initialize, seed or change a prospect's profile (the draft integration plan's S9 and its step 4: interviews observe pre-existing truth). Nothing in this plan writes to `personality/*` or `persona_*` tables. Once prospect personalities exist, they only shape an interview: its tone and per-topic tilts, through `prospect_disposition`. No trait name, slider value or personality label is ever put on a fact sheet, in a prompt, in a template, in an API response or in the UI; the prompt gets only the engine's tone and grade. A test checks that `answer_facts` and the prompt hold no key from `persona_traits.json`.

**Board effect.** Only VISITs touch numbers, and only through §2.5: a JR visit is a stored `VISIT` event like any other, so his team's board error for that prospect uses `VISIT_M` from the step it's logged. Combine interviews and answer grades change nothing numeric (Q2).

### 2.7 API (`server/draft_api.py`, U4)

Reads only; no hidden columns. Interview writes go through the inbox API.

- `GET /api/draft/prospect/{key}/report?draft_season=&edition=` (key = the current `prospect_key`, resolved with `uid_for`) → `{"prospect_uid","prospect_key","draft_season","edition","step","step_label","grade","rank","round_label","role","role_words","traits":[{"trait","label","score","word"}],"strengths":[label],"concerns":[label],"flags":[{"flag","words"}],"body":{...},"text_source","scout":{"name": "Mu Scouting Department","scout_id": null},"editions":["FALL","BOWLS","FINAL"]}`; 404 when none.
- `GET /api/draft/interviews?team=CAR&draft_season=` (`franchise_api.require_user_team`) → `{"team","draft_season","window":{...},"budget":{...},"eligible":[{"prospect_uid","prospect_key","full_name","position","college","rank","kinds":["COMBINE","VISIT"]}],"interviews":[{"id","prospect_uid","prospect_key","full_name","kind","status","questions_left","summary_grade","thread_id"}]}`.

### 2.8 Inbox kinds (`engines/inbox/kinds/prospect.py`, `scout.py`, U5)

**Who JR speaks as (JR, 2026-11-03):** both kinds set `ACTOR_ROLES = ('GM',)`. Interviews, visits and scout notices belong to the GM persona JR controls on that team (V2.9 `controlled_personas`; JR controls no one by default). If JR doesn't control the team's GM, there are no prospect interviews or scout notices for him, and his team keeps its seeded visits like a CPU team.

Both implement V2.9's `Kind` protocol and are found by auto-discovery; no shared inbox file changes, except the validator hook below.

**`PROSPECT`** (`COUNTERPART_TYPE = "prospect"`, `PERSONA_ROLE = None` until the personality draft plan; counterpart id = `person_ref(prospect_uid)` (§2.1b: the permanent prospect ID, which becomes the lifecycle plan's `source_person_id`), never `prospect_key` or `draft_prospects.id`, which renames and re-scans change; name = the prospect's name; `link_table = "scout_interviews"`).
- `start(cur, team, body)`: body `{"kind": "PROSPECT", "prospect_uid", "interview": "COMBINE" | "VISIT"}` (a `prospect_key` is accepted and resolved with `uid_for`) → `scouting_interviews.open_interview` (an `InterviewError` becomes `InboxError(409, message)`) → a `Trigger` (subject "Combine interview: John Smith, QB, Oregon", `source_ref = "interview:<id>"`, intent `GREETING`, facts = the prospect's sheet). After the thread exists, `link_thread`.
- `scan(...)`: returns [] (prospects never write first).
- `options(cur, thread, last)`: one option per menu topic (`{"id": "ASK_<TOPIC>", "label": <question>, "form": null, "needs_note": false}`) while questions are left, plus `END_INTERVIEW`. A note with no option is a typed question (`needs_note` style; V2.9 calls `act(option_id=None, note=...)` "talk only", and for this kind talk is a question).
- `act(cur, thread, option_id, payload, note)`: `ASK_<TOPIC>` → `ask(topic=...)`; `None` + note → `ask(text=note, llm=make_llm("draft_interview_classify") if MU_INBOX_LLM=1 else None)`; returns `ActResult(facts=answer_facts, intent="ANSWER_<GRADE>", engine_ref={"interview_id","topic","grade","classified_by"})`. `END_INTERVIEW`, or the last question, → `close` and `ActResult(..., intent="SUMMARY", close=True)`; the summary is written as the closing message with the department scout's facts.
- `template(intent, facts)`: `answer_template` / `summary_template`.
- `validate(text, facts)`: `scouting_interviews.validate_answer`.

**`SCOUT`** (`COUNTERPART_TYPE = "scout"`, `PERSONA_ROLE = "scout"`, which V2.9's `persona_view` maps to the neutral view; counterpart id `"department"`, name "Mu Scouting Department").
- `scan(cur, season, week, team)`: triggers `COMBINE_OPEN` (step 24 stored; facts: budget, window), `VISITS_OPEN` (step 24 and step 25 stored), `REPORTS_READY` (a report edition stored; facts: edition, the 5 biggest risers among JR's needs, names and ranks only). `source_ref` = `"scout:<draft_season>:<trigger>:<step>"`, so each fires once. Reads only scouting tables, so it works in the offseason when the inbox scans on page open (V2.9 §2.6).
- `options`: `SHORTLIST` (engine: up to 8 eligible prospects in JR's team-board order at the latest step, names and positions only; never a grade or team rank), `ACKNOWLEDGE`.
- `act`: `SHORTLIST` → facts with the list, intent `SHORTLIST`; `ACKNOWLEDGE` → close.
- `template`, `validate` (`validate_scout_text`), `start`: raises `InboxError(400)` (the scout writes first).

**Voice and latency.** V2.9's `voice.write` writes every reply within `INBOX_LLM_BUDGET_S` and falls back to `Kind.template`; the engine's grade is stored before the words, so a slow model never changes an answer (§5.3.2). The prompt's voice is the prospect's (neutral: "a polite, nervous-but-prepared college prospect") or the department's. **Hook needed in V2.9:** `voice.write` must also call `kind.validate(text, facts)` when the kind defines it (`getattr(kind, "validate", None)`), because `validate_rationale` doesn't know heights, schools or injuries. The coordinator should add this line to V2.9's §2.4 before it is built; if V2.9 is built without it, U5 adds it (R7).

### 2.9 Writers (`engines/scouting_read.py`, U2)

`scout_report(cur, prospect_key, through_step=None)`: resolves the alias to `prospect_uid` with SQL, then the latest edition whose `step ≤ through_step` (use `visible_step` for article cutoffs), public fields only. Interviews are team-private and are never readable here.

### 2.10 Frontend (U6)

- `Mu_Network/src/types/scoutingReports.ts`: `ScoutReport`, `InterviewWindow`, `InterviewBudget`, `InterviewRow`, `InterviewsResponse` matching §2.7.
- `ProspectModal.tsx`: a "Scouting Report" section (fetches `/report`): edition tabs, role and grade line, trait bars (1–9 with the word), strengths/concerns chips, flag chips, the body paragraphs, "Mu Scouting Department · FINAL edition". Hidden when the endpoint 404s.
- `DraftWarRoomModule.tsx`: an "Interviews" card: budget meters, the window ("Combine interviews open until the re-signing stage"), eligible prospects with "Combine interview" / "Visit" buttons (`POST /api/franchise/{abbr}/inbox` with the PROSPECT body), and past interviews with their summary grade and a link to the thread in the Inbox module. The conversation itself is V2.9's `InboxModule`; no chat UI here and no edit to `FranchisePage.tsx` (the link uses whatever deep link V2.9 ships, else switches to the Inbox tab).

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Madden rating key names differ from the `TRAITS` lists. | Spike X1: list the keys in one `player_attributes.ratings` row (read-only). | U2 corrects the names; the lists are constants. |
| R2 | The PC snapshot doesn't carry ratings for Draft-status players. | Spike X2: count Draft players with non-empty `ratings` in a CAREER-WEEK1 snapshot JSON. | Reports run traits-free (already a supported mode); a reader change becomes a separate task. |
| R3 | Trait noise too small (FINAL scores give away true ratings) or too big (FALL is noise). | Spike X3 (inside U2, offline): correlation of `obs` with truth at steps 4, 21, 26. Target about 0.6 / 0.75 / 0.85 (estimate). | Tune `SIGMA_T0` / `SIGMA_T_DRAFT`; U4 stores them in each edition step's `scout_runs.params`. |
| R4 | Interactive LLM latency beside Madden (§5.3.2) is unmeasured. | V2.9's latency spike sets `INBOX_LLM_BUDGET_S`; the classifier has its own `CLASSIFY_TIMEOUT_S`. | Templates; the grade is stored first, so nothing waits on words. `MU_INBOX_LLM` off keeps it template-only. |
| R5 | Shared files with sibling plans. **V2.9 inbox:** `db/init_db.sql` (its U1), `pipeline/weekly_capture.py` (its U1), `engines/inbox/voice.py` (validator hook, R7); this plan never touches `server/api.py` or `FranchisePage.tsx`. **V2.2 press conferences** (`PRESS_CONFERENCE_PLAN.md`): `db/init_db.sql` in its later waves, migration date `2027_01_20` (ours is `2027_01_21`); no overlap with its `server/api.py`, `newsroom/swarm_dispatcher.py`, `newsroom/agent_memory.py`. **V2.11 trade negotiations:** none known (it edits `engines/inbox/kinds/gm_talk.py`). **Personality lifecycle** (`2027_01_24`): also owns `pipeline/load_save.py`, `pipeline/load_capture.py` and `engines/scouting_engine.py` in its U3 (R6), and must adopt `scout_prospect_ids` (R11). Also `engines/gm_llm.py` / `llm_provider.py` are read, not edited; three new `llm_usage` labels. | The orchestrator compares the four plans' Owns lists before each wave. | Build after V2.9's U1 is on main (prerequisite); U1 here and the press plan's `init_db.sql` edits go in sequence, never in parallel; conflicts go to JR. |
| R6 | `PERSONALITY_DRAFT_INTEGRATION_PLAN.md` U3 also owns `pipeline/load_save.py`, `pipeline/load_capture.py` and `engines/scouting_engine.py`. | Check its status before wave 1. | This plan's edits are small and additive (one call; a few lines in `default_extras`/`_write_step`). Whichever lands second rebases; conflicts go to JR. |
| R7 | V2.9's `voice.write` validates only with `validate_rationale`, which doesn't catch a wrong height, school or an ongoing injury. | Read V2.9's built `voice.py` before wave 2. | Ask the coordinator to add the optional `kind.validate` hook to V2.9 §2.4 now; otherwise U5 adds the 3-line hook to `engines/inbox/voice.py` (listed in U5's Owns for that case). |
| R8 | An LLM answer contradicts a fact or its grade (e.g. "I missed three games", praise on a CONCERNING grade). | `validate_answer` (numbers, injuries, grade keywords); tests T-I6. | Template fallback; tighten the lists. |
| R9 | JR's visits break the frozen calibration. | JR's count equals the CPU count (6 a step), test T-W2. | None expected; `--calibrate` is unchanged (it simulates planned visits). |
| R11 | The lifecycle plan doesn't adopt `prospect_uid` as `source_person_id`, or its verification disagrees with a pgid match (e.g. a pgid reused within a class). | When the lifecycle plan is revised or built; its tests should cover adoption. | `source_person_id` column plus the `scout-uid` binding keep every old row resolvable (§2.1b); `person_ref` switches new threads to the lifecycle ID. A pgid clash shows up as two rows wanting one birth key: `ensure_prospect_ids` marks both `KEY_COLLISION` instead of merging. |
| R12 | The older scouting tables still key on `prospect_key`, so a rename or collision still splits a prospect's consensus history. | Existing behaviour (S11b). | Out of scope; this plan's rows stay correct because they key on `prospect_uid`, and collisions are excluded rather than merged. |
| R10 | V2.9 is unapproved; if JR rejects or delays it, interviews have no surface. | JR's answer on V2.9. | Reports (U1, U2, U4's report parts, U6's modal section) still build alone; interviews wait for the inbox (Q4). |

**Spike first:** X1 and X2 (orchestrator, read-only, about 10 minutes) before wave 1: rating key names and whether Draft-status players carry ratings. X3 runs inside U2 and can only change constants. **Prerequisite:** V2.9's U1 on main before wave 1 (the migration's FK to `inbox_threads`, `db/init_db.sql`, `pipeline/weekly_capture.py`, and the `Kind` registry).

## 4. Tests (synthetic, no live DB)

- `tests/test_prospect_ids.py` (U1) → the same birth key always gives the same `prospect_uid`; a rename (new `prospect_key`, same pgid) keeps the UID and appends `key_history`; a position-group change likewise; two rows with one `prospect_key` are both `KEY_COLLISION`; a scanned class matches by key; `person_ref` returns `source_person_id` once set, else the UID; re-running changes nothing.
- `tests/test_prospect_ratings.py` (U1) → rows are keyed by `prospect_uid`; collisions skipped; keys match `draft_board_prospects`' keys for the same snapshot; non-Draft and rating-less players skipped; upsert idempotent (fake cursor); missing table returns 0.
- `tests/test_scouting_reports.py` (U2) → T-R1 `edition_for` (first step, 21, 26, late-initialized class); T-R2 noise sd falls with the step and `z_t` is fixed across editions (FINAL closer to truth than FALL on 500 synthetic prospects); T-R3 scores are class-relative 1–9 and no stored field equals a raw rating or OVR; T-R4 roles by rank band; T-R5 flags only from stored events/payloads; T-R6 traits-free mode; T-R7 `rewrite` keeps the template on a validator rejection, an error or no llm, rewrites only the top `limit`, never changes non-prose fields; T-R8 `report_for` / `scouting_read.scout_report` return public fields only and respect `through_step`; T-R9 `scout_voice.regional_bias` is 0 and `department_voice()` has no id.
- `tests/test_scouting_interviews.py` (U3) → T-I1 windows by latest step (23, 24, 25, 26); T-I2 budgets and the errors `WINDOW_CLOSED` / `NO_BUDGET` / `NOT_ELIGIBLE` / `DUPLICATE` / `NO_QUESTIONS`; T-I3 tone never contradicts a stored combine verdict; T-I4 grade mix per tone over 1,000 seeded prospects, same topic → same grade; T-I5 the menu has `INJURY`/`CONSISTENCY`/`DECLARE` only with the backing event; T-I6 `validate_answer` rejects an invented number, an ongoing injury, OVR, another school, praise on CONCERNING; templates pass their own validator; T-I7 classifier: rule hit, LLM id in the menu, outside the menu → `OFF_TOPIC`, no llm or a timeout → `FALLBACK`; T-I8 `jr_visit_keys` stamps `logged_step` and returns the same set on a second call; T-I9 `prospect_disposition` is None and the class stays `NEUTRAL`; T-I10 no `answer_facts`, prompt or template contains a `persona_traits.json` trait or slider name, and no interview function writes outside `scout_interviews`.
- `tests/test_scouting_v210_wiring.py` (U4) → T-W1 `default_extras` stores a report edition only on edition steps and `_write_step` writes the rows; `--replace` keeps LLM prose; T-W2 a user team's visits = JR's + seeded fill to 6, CPU teams unchanged, `VISITS_PER_STEP` equals `scouting_events.visits`' default; T-W3 `step_scouting` calls `rewrite` only for edition steps and survives a raised error; T-W4 `load_capture.load_prospects` allocates IDs for a scanned class; a collision prospect gets no report.
- `tests/test_draft_api.py` (U4, existing) → report endpoint 200/404 and no hidden key anywhere in the payload (`true_value`, `ratings`, `err`, `impact`, `ovr`); interviews endpoint 403 for a non-user team and §2.7's shape.
- `tests/test_inbox_kinds_prospect_scout.py` (U5) → PROSPECT `start` opens an interview and maps each `InterviewError` to 409; `options` lists the menu while questions remain; `act` with a menu option and with a note, the last question closes with a summary intent; `engine_ref` carries the grade; `template` and `validate` work on a fake sheet; the thread's `counterpart_id` is `person_ref(prospect_uid)`; SCOUT `scan` fires each trigger once per step and nothing outside the windows; `SHORTLIST` never shows a grade or team rank; both kinds are found by `registry.kinds()`.
- Frontend: `npm run build` in `Mu_Network/` (U6).

Run with `pytest -m "not integration" tests/test_prospect_ids.py tests/test_prospect_ratings.py tests/test_scouting_reports.py tests/test_scouting_interviews.py tests/test_scouting_v210_wiring.py tests/test_draft_api.py tests/test_inbox_kinds_prospect_scout.py tests/test_scouting_engine.py tests/test_scouting_narrative.py tests/test_draft_room.py`. No integration tests.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Schema and prospect ratings

- **Owns:** `db/migrations/2027_01_21_scouting_reports.sql` (new), `db/init_db.sql`, `engines/prospect_ids.py` (new), `pipeline/prospect_ratings.py` (new), `pipeline/load_save.py`, `tests/test_prospect_ids.py` (new), `tests/test_prospect_ratings.py` (new)
- **Reads:** `db/migrations/2026_12_12_scouting.sql`, V2.9's `2027_01_19_front_office_inbox.sql`, `pipeline/load_save.py:740-935`, `engines/scouting_value.py` (`prospect_key`), this plan §2.1–§2.2; `docs/not_started/PERSONALITY_DRAFT_INTEGRATION_PLAN.md` lines 49–70 (identity rules it must stay adoptable by)
- **Depends on:** none in this plan (prerequisite: V2.9 U1 on main)
- **Done when:** the migration matches §2.1 and is idempotent and mirrored in `init_db.sql`; a load allocates permanent prospect IDs (§2.1b) and stores ratings by them, reporting allocated / renamed / collisions; tests pass.
- **Verify:** `pytest -m "not integration" tests/test_prospect_ids.py tests/test_prospect_ratings.py` passes; `grep -c 'CREATE TABLE IF NOT EXISTS' db/migrations/2027_01_21_scouting_reports.sql` prints 4.

### U2: Report engine, scout voice hook, writer read

- **Owns:** `engines/scouting_reports.py` (new), `engines/scout_voice.py` (new), `engines/scouting_read.py`, `tests/test_scouting_reports.py` (new)
- **Reads:** `engines/scouting_engine.py` (`compute`, `volatility`, `out` shapes), `engines/scouting_boards.py:105-145`, `engines/scouting_events.py`, `engines/scouting_narrative.py`, `engines/college_stats.py:105-280`, `engines/gm_llm.py`, this plan §2.1, §2.3–§2.4, §2.9; spike X1/X2 results
- **Depends on:** none (builds against §2.1)
- **Done when:** `build_edition`, `rewrite`, `report_for`, the CLI and `scout_report` work as §2.3/§2.9; spike X3's correlations are in the unit's summary.
- **Verify:** `pytest -m "not integration" tests/test_scouting_reports.py tests/test_scouting_narrative.py` passes; `python3 -m engines.scouting_reports --help` exits 0.

### U3: Interview engine

- **Owns:** `engines/scouting_interviews.py` (new), `tests/test_scouting_interviews.py` (new)
- **Reads:** `engines/scouting_events.py:295-422`, `engines/scouting_narrative.py`, `engines/scouting_read.py`, `engines/college_stats.py:105-200`, `engines/gm_llm.py`, `engines/gm_rationale.py` (sheet shape), this plan §2.1, §2.6
- **Depends on:** none (builds against §2.1)
- **Done when:** windows, budgets, topics, grades, classifier, facts, templates, validator, summary, `jr_visit_keys`, `eligible` and `prospect_disposition` behave as §2.6 with fake cursors and fake llms.
- **Verify:** `pytest -m "not integration" tests/test_scouting_interviews.py` passes.

### U4: Step, pipeline and API wiring

- **Owns:** `engines/scouting_engine.py`, `pipeline/load_capture.py`, `pipeline/weekly_capture.py`, `server/draft_api.py`, `tests/test_scouting_v210_wiring.py` (new), `tests/test_draft_api.py`
- **Reads:** U1–U3's merged code, `server/franchise_api.py:213-226`, `db/franchises.py`, V2.9's merged `step_inbox` placement in `weekly_capture.py`, this plan §2.5, §2.7
- **Depends on:** U1, U2, U3
- **Done when:** a class's first step, step 21 and step 26 store a report edition; Step 7c rewrites the top 64; user teams' visits come from JR's interviews; a scanned class gets permanent IDs at load; both endpoints return §2.7's shapes with no hidden field.
- **Verify:** `pytest -m "not integration" tests/test_scouting_v210_wiring.py tests/test_draft_api.py tests/test_scouting_engine.py tests/test_draft_room.py tests/test_scouting_calibration.py` passes.

### U5: PROSPECT and SCOUT inbox kinds

- **Owns:** `engines/inbox/kinds/prospect.py` (new), `engines/inbox/kinds/scout.py` (new), `tests/test_inbox_kinds_prospect_scout.py` (new); `engines/inbox/voice.py` only if V2.9 shipped without the `kind.validate` hook (R7)
- **Reads:** V2.9's merged `engines/inbox/registry.py`, `voice.py`, `store.py` and one built kind (`owner.py`); U2/U3's merged code; this plan §2.6, §2.8
- **Depends on:** U1, U2, U3 (and V2.9's U1)
- **Done when:** both kinds are discovered, a PROSPECT thread runs open → ask (menu and typed) → close on fakes, and SCOUT notices fire once per window and edition.
- **Verify:** `pytest -m "not integration" tests/test_inbox_kinds_prospect_scout.py tests/test_inbox_registry.py` passes.

### U6: Report section and Interviews card

- **Owns:** `Mu_Network/src/types/scoutingReports.ts` (new), `Mu_Network/src/components/draft/ProspectModal.tsx`, `Mu_Network/src/components/franchise/DraftWarRoomModule.tsx`, `Mu_Network/src/components/draft/draft.css`
- **Reads:** this plan §2.7, §2.8, §2.10; V2.9's `Mu_Network/src/types/franchiseInbox.ts` and Inbox deep-link (if built); `Mu_Network/src/components/franchise/RosterCapModule.tsx`, `Mu_Network/src/api.ts`
- **Depends on:** none for the build (types fixed by §2.7); runs in wave 2 so it can be checked against U4's endpoints.
- **Done when:** the modal shows the report (hidden on 404); the War Room module's Interviews card starts a PROSPECT thread and links to it; the build passes.
- **Verify:** `cd Mu_Network && npm run build` succeeds with no type errors.

## 6. Waves

Prerequisite: V2.9 (`FRONT_OFFICE_INBOX_PLAN.md`) U1 merged into main. U5 also needs V2.9's kinds pattern; if V2.9's wave 2 is still running, U5 can wait for it without holding U4/U6.

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2, U3 | V2.9 U1 | No shared files; U2 and U3 build against §2.1 and need nothing from each other. |
| 2 | U4, U5, U6 | U1, U2, U3 | U4 and U5 import U2/U3 and need U1's tables; U6 builds against §2.7. No file in common. |

**File ownership check:**

| File | Unit |
|---|---|
| `Mu_Network/src/components/draft/ProspectModal.tsx` | U6 |
| `Mu_Network/src/components/draft/draft.css` | U6 |
| `Mu_Network/src/components/franchise/DraftWarRoomModule.tsx` | U6 |
| `Mu_Network/src/types/scoutingReports.ts` | U6 |
| `db/init_db.sql` | U1 |
| `db/migrations/2027_01_21_scouting_reports.sql` | U1 |
| `engines/inbox/kinds/prospect.py` | U5 |
| `engines/inbox/kinds/scout.py` | U5 |
| `engines/inbox/voice.py` (conditional, R7) | U5 |
| `engines/prospect_ids.py` | U1 |
| `engines/scout_voice.py` | U2 |
| `engines/scouting_engine.py` | U4 |
| `engines/scouting_interviews.py` | U3 |
| `engines/scouting_read.py` | U2 |
| `engines/scouting_reports.py` | U2 |
| `pipeline/load_capture.py` | U4 |
| `pipeline/load_save.py` | U1 |
| `pipeline/prospect_ratings.py` | U1 |
| `pipeline/weekly_capture.py` | U4 |
| `server/draft_api.py` | U4 |
| `tests/test_draft_api.py` | U4 |
| `tests/test_inbox_kinds_prospect_scout.py` | U5 |
| `tests/test_prospect_ids.py` | U1 |
| `tests/test_prospect_ratings.py` | U1 |
| `tests/test_scouting_interviews.py` | U3 |
| `tests/test_scouting_reports.py` | U2 |
| `tests/test_scouting_v210_wiring.py` | U4 |

No path appears twice.

## 7. Spec changes when built

- §2.18: new bullets for reports (editions, trait observation model and constants, roles, flags, LLM top 64, label `scout_report`), JR's interviews (windows, budgets, topics, grades, classifier, label `draft_interview_classify`, the PROSPECT/SCOUT inbox kinds) and the user-team visit swap in `default_extras`; the scout-voice and prospect-disposition hooks.
- §3.3g: tables `scout_prospect_ids`, `scout_prospect_ratings` (hidden), `scout_reports`, `scout_interviews`; add `scout_prospect_ratings` to the hidden-columns list; the permanent prospect ID rule (§2.1b) and its adoption by the lifecycle plan; interviews are never personality evidence.
- §3.3c: `pipeline/load_save.py` allocates permanent prospect IDs and stores prospect ratings on every load; `pipeline/load_capture.load_prospects` allocates IDs for a scanned class; `prospect_key` described as an alias.
- §3.1 `llm_usage` sources: the new labels.
- §4.6: the two new endpoints; tests.
- The inbox section V2.9 adds: the PROSPECT and SCOUT kinds (and the `validate` hook if U5 added it).
- §5.2: Scouting Report section in `ProspectModal`; Interviews card in the Draft War Room module.
- §6.0 / Step 7c: report rewrite after `catch_up` on edition steps.
- Repository Layout: `engines/prospect_ids.py`, `engines/scouting_reports.py`, `engines/scout_voice.py`, `engines/scouting_interviews.py`, `engines/inbox/kinds/prospect.py`, `engines/inbox/kinds/scout.py`, `pipeline/prospect_ratings.py`, `db/migrations/2027_01_21_scouting_reports.sql`, `Mu_Network/src/types/scoutingReports.ts`, the six new test files.
- `V2_MASTER_PLAN.md` §6 V2.10 status line (orchestrator).

## 8. Decisions for JR (all decided 2026-11-03: JR accepted every recommendation)

| # | Question | Recommendation (accepted) |
|---|---|---|
| Q1 | Trait grades: from the save's real prospect ratings seen through noise (new hidden table, small loader hook), or only from what Mu already stores (stats, combine, events)? | Real ratings through noise. Reports then agree with how the player turns out after the draft; facts-only reports could call a slow receiver fast. |
| Q2 | What do JR's interviews change? Recommended: VISITs replace his team's seeded visits one for one (6 a step, same as CPU teams) and get the existing `VISIT_M` board effect; combine interviews are information only. | As recommended. Keeps the frozen calibration fair; more visits or a combine effect would make JR's board sharper than every CPU team's. |
| Q3 | Budgets: 15 combine interviews of 4 questions; 6 visits a step (12 total) of 6 questions? | Yes. The NFL allows 45 combine interviews and 30 visits, but that would be hours of clicking. |
| Q4 | Interviews live in the V2.9 inbox as a PROSPECT kind (started from the War Room's Interviews card), so V2.10's interviews wait for V2.9's U1. Reports don't wait. | Yes. One conversation surface; building a second chat UI now would be thrown away. |
| Q5 | Can JR type his own questions (the engine or LLM maps them to an allowed topic, otherwise "off topic"), or menu only? | Both. The LLM still only picks among engine-permitted topics. |
| Q6 | Report editions and LLM use: three editions (first step, step 21 after the bowls, step 26 after pro days); LLM prose for the consensus top 64 of each, templates for the rest, a CLI for more. | Yes. About 190 LLM calls a season (estimate). |
| Q7 | Pro comparisons ("plays like …") in reports? | Not now. An engine-picked comparison is possible but needs its own no-leak rule; propose it with phase 6 scouts. |
| Q8 | Scout names before phase 6? | No. One "Mu Scouting Department" voice until phase 6 creates scouts through the personality core, so no invented people need adopting later. |
| Q9 (added 2026-11-03 after approval, from JR's cross-plan decision; mechanism needs JR's confirmation) | Permanent prospect ID before the lifecycle plan exists: allocate `prospect_uid` here (deterministic from class + pgid) for the lifecycle plan to adopt as `source_person_id`, or make V2.10 wait for the lifecycle plan's U1–U2? | Allocate here (§2.1b). JR asked for a permanent ID on 2026-11-03; waiting would block V2.10 behind an unapproved plan, and the derivation is one the lifecycle plan already allows. |
