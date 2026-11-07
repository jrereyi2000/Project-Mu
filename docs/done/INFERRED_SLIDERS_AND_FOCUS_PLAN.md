# Project Mu: Inferred Sliders and Refreshing Focus Plan

**Status:** approved by JR (2026-11-07, his own message: "Approve both.", with the RETIREMENT_WINDOW_PLAN; Q1–Q9 as recommended); built and on main (2026-11-07), waves 1–3; off by default, migration `2027_02_03` applied to no database (scratch test only). JR's coach and owner profile approvals were recorded on main (619a9f3, 2eb056e) before wave 3, as Q7 asks; the conversion sent all 128 files back to draft (Q8), so JR needs to re-approve the converted fields (motivation, focus, inferred sliders).
- As built (spike, before U1): the §2.2 leans with balancing over the 128 profiles give 431 inferred sliders (values 2:4, 3:20, 4:89, 5:179, 6:92, 7:36, 8:11); 71 reach 3 or 7 and show in the voice; 21 profiles balanced, none left outside 24–36; distinct slider vectors 93 → 119; owner `patience` hot-seat line scale moves at most 0.03 (R1 is small). Leans kept as listed.
- As built (U1): band balancing moves only inferred values on the side that fixes the total (above 5 when over 36, below 5 when under 24); `basis[s]['balanced']` is the step count; a locked slider's basis is `{'locked': True}`. `inferred_evidence` uses `FALLBACK_DATE = '2026-09-08'` when a file has no `reviewed_at`.
- As built (U2): 48 templates (one per seat × slot × direction, plus hot-seat/playoff, people-led and generic ones) rather than three per direction. `focus_store` also has `read_row` and `next_seq`; `read_focus(as_of=)` takes the highest-seq row whose valid_from is at or before `as_of`. The opt-in scratch test (`FOCUS_TEST_DSN`) passed on a throwaway `mu_scratch_test` database.
- As built (U3): `coach_profiles` gained `is_v2`, `check_schema`, `check_sliders`, `check_motives` (shared with `owner_profiles`), `with_inference` and `convert_v2`; a v2 file checks the effective slider total, not the file total. `convert-v2 --edits PATH` ({key: {motivation, focus, slider_inference}}) supplies the hand-written parts before validation, since `--write` writes only valid results (dry run over the owners: 32 conversions, 8 with motivation over 14 words, nothing written).
- As built (U4): `focus_facts.facts` takes `bindings` ({persona_id: [subject, ...]}) so subject status is read only for active rows' subjects; a coach's seat is the tenure covering season + 1, else an open tenure covering the season; coach and GM subjects carry a `seat` {team, role?} for the `not_in_seat` check; GM bindings use namespace `db-gm` (`gm_profiles.id`). Seeding finds an HC by `coaches.coach_name`, a coordinator by name plus his 2026 tenure, an owner by birth key. CLI refuses `madden_2036` and `postgres` (as §2.6; the evolution CLI also refuses `madden`); exit 3 covers not found and not migrated.
- As built (U5): the panel's "inferred" tag is styled inline (U5 does not own `personality.css`).
- As built (U6–U8): converted with `convert-v2 --edits` (hand-written motivation and focus per file), then `infer --write` (no changes). 431 inferred sliders across the 128 files (unchanged from the spike); 78 focus slots set, naming 44 subjects; the rest are null where the old want/fear was already durable. Judgment calls for JR: focus subjects name the 2026 starters the old text implied but did not always name (e.g. Joe Burrow, Jaxson Dart, Bo Nix, Cam Ward, J.J. McCarthy, Tyler Shough, Jayden Daniels, Baker Mayfield, Trevor Lawrence, Aaron Rodgers on PIT, Patrick Mahomes); the DET owner fear names Dan Campbell and the SF owner want names Kyle Shanahan as coach subjects; SEA HC/OC and SEA owner focus assume the defending-champion framing in the existing drafts; owner `(medium)` tags dropped. A converted reviewed file's `review` is `{status: draft, notes}` (no reviewer/reviewed_at keys), which validates; `apply-review` writes the full block on re-review.
**Builds on:** PROJECT_SPEC §7.11 (real-coach profiles, owner profiles, personality core, evolution, switchboard), §3.3p, §6.0b, §4.8; [`REAL_COACH_PROFILES_PLAN.md`](../in_progress/REAL_COACH_PROFILES_PLAN.md) §2.1–§2.3, [`OWNERS_SCOUTS_VOTERS_PLAN.md`](../in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md) §2.1–§2.2, [`PERSONALITY_EVOLUTION_PLAN.md`](../done/PERSONALITY_EVOLUTION_PLAN.md), [`PERSONALITY_ACTIVATION_PLAN.md`](../done/PERSONALITY_ACTIVATION_PLAN.md), [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §5.3 (migration reservations).

**Goal:** Two fixes JR asked for after reading the 96 coach and 32 owner drafts (2026-11-07). (A) A slider with no evidence no longer sits at a flat 5: it gets a value inferred from the person's evidenced traits plus a small seeded nudge, fixed in the file at review time, marked as inferred everywhere it shows, and never touching an evidenced slider. (B) Want and fear split into a durable **motivation** ("to prove he can build a contender") and a **current focus** that may name people ("a second title with Lamar Jackson"). When a person a focus names retires, leaves the team or leaves his seat, the offseason step drafts a new focus from the new situation, applies it with a change note, and JR can revert or set it by hand.

**Delivers:**
- `personality/slider_inference.py` and `newsroom/config/slider_leans.json`: the pure inference (`infer_sliders`, `check_inference`, `effective_sliders`, `inferred_evidence`, `is_inferred`)
- Profile schema `coach-profile-v2` / `owner-profile-v2`: `slider_inference`, `motivation`, `focus` (v1 still read)
- `tools.coach_profile_review` subcommands `infer` and `convert-v2`; review pages show inferred sliders and the focus apart from evidence
- `personality/focus.py` and `newsroom/config/focus_templates.json`: focus shapes, staleness, template drafting (pure)
- `db/migrations/2027_02_03_persona_focus.sql`: `persona_focus`, `persona_focus_runs`; `personality/focus_store.py`
- `personality/focus_facts.py`, `personality/focus_run.py` (CLI `python3 -m personality.focus_run season|report|show|set|revert`)
- Switchboard feature `focus_refresh`; watcher step `step_focus_refresh` at OFFSEASON_START
- `render_persona` background line with motivation plus current focus, inside today's word budget
- `/api/personality` slider rows carry `inferred`; the Personality panel labels them
- All 128 profile files converted to v2 (motivation/focus split, subjects, inferred sliders), as drafts for JR's review

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | The coach validator requires evidence only for a slider that is not 5 (`personality/coach_profiles.py:263`); 257 of 576 coach slider values are 5 and 18 of 32 owners have all six at 5 (counted 2026-11-07). | "5 = no evidence" is the current convention. Inference must keep evidenced values and the meaning of `sliders` untouched, so it lives in a separate block. |
| S2 | `core_initial_values` copies `doc['sliders']` and emits `slider:<s>` evidence only for cited sliders (`coach_profiles.py:386`); the lifecycle builds the real profile from that (`personality/lifecycle.py:480-481`). | Overlaying inferred values in `core_initial_values` carries them into the core through the existing reviewed lifecycle apply, with no lifecycle or core change. |
| S3 | Core evidence entries need `source`, ISO `date`, `confidence`, `reviewer`, `reason`, `features`; a low-confidence entry may only list `voice` (`personality/core.py:147-162`). Under views-v2 evidence is provenance only (`core.py:209-216`). | An inferred slider can carry an honest provenance entry `{source 'inferred:slider-inference-v1', confidence 'low', features ['voice']}` that passes validation and changes no feature read. |
| S4 | `apply_override` replaces a slider's evidence with `override:<id>` when the slider already has an entry (`core.py:334-335`). | JR's later override of an inferred slider automatically stops it reading as inferred. |
| S5 | Policies read sliders as small linear terms around 5, e.g. owner patience `SLIDER_LINE = 0.01` per point for temperament and drive, against ±0.10 for PATIENT_BUILDER / HEADLINE_HUNTER (`engines/owner_policy.py:28-31, 71-80`); coach and player loops do the same (`engines/agent_loops/coach.py:147, 245`, `player.py:70, 203`). | Traits already make most of the behavioural difference; inferred sliders add a smaller second layer. Leans that echo a trait slightly double its effect (R1). |
| S6 | `slider_sentence` only voices a slider at 0–3 or 7–10 (`newsroom/personas.py:773-786`). | Inferred 4s and 6s change decisions but not the prompt voice; only 3s and 7s show. |
| S7 | Seeded generation already gives generated people (players, scouting directors) full slider spreads (`newsroom/personas.py:211-223`). | Inference is for real-inspired coaches and owners only. |
| S8 | want/fear are plain strings, validated nonempty (`coach_profiles.py:327`), passed through `persona_for` (`coach_profiles.py:419-430`) and rendered as one IN THE BACKGROUND line (`newsroom/personas.py:865-867`). No engine reads them, and nothing calls `persona_for` outside tests yet. | The split changes prompts only. No decision code moves. |
| S9 | Coach want/fear average 18 words (max 27); owner 11.5 (max 18). Many name a player, a former team or a seat goal ("earn another head coaching job"). | Focus staleness needs subject refs and the holder's own seat (`HOLDER_MOVED`); the word caps must keep the line near today's length. |
| S10 | Ties name `{kind player|gm, name, team_2026}` or a coach `seed_ref`, resolved later by a caller-supplied `resolve` (`coach_profiles.py:441-474`); no live resolver exists. | Focus subjects reuse the tie target shape; the focus run resolves them itself against the DB. |
| S11 | Evolution runs at OFFSEASON_START (tick 24) in `step_personality_evolution` (`pipeline/watch_save.py:631-708`), gated by `feature_gate` (`watch_save.py:544`), with runs/candidates tables and a revert by JR (spec §7.11). Season facts read tenures, `player_seasons`, `player_retirements`, `staff_moves` (`personality/evolution_facts.py:35-36, 216-249`). | The focus refresh copies that pattern: a step right after evolution, its own run table, revert, report, and a facts reader over the same tables. |
| S12 | `FEATURES` is the D2 go-live order and `REQUIREMENTS` maps each feature to a module and migration (`personality/features.py:51-86`). Every feature is off on every franchise. | Adding `focus_refresh` is one tuple entry plus one requirement; inserting it changes no live mode. |
| S13 | Migrations on disk end at `2027_02_01_agent_loops.sql`; `2027_02_02_owners_scouts_voters.sql` is reserved and unwritten (`PERSONALITY_PLAN.md:190`). | This plan takes `2027_02_03_persona_focus.sql`. |
| S14 | The core alias table is keyed `(universe_id, persona_type, namespace, external_id)` (`db/migrations/2027_01_16_personality_core.sql:27`); owner aliases are `db-owner` on `team_owners`, which the owners plan's held U6 creates (spec §7.11). | Coach holders resolve today; owner holders resolve only after owners U6. Owner focus refresh is inert until then (R7). |
| S15 | The review tool's `EDITABLE` lists `want`/`fear` (`tools/coach_profile_review.py:32`), and the report prints them under "Want and fear" (`:187`). | The tool gains the v2 fields and the inferred/evidenced split in the page. |
| S16 | `/api/personality` slider rows are `{name, value, sentence, low, high}` (`server/personality_api.py:79`). | One boolean `inferred` per row, no new numbers (D3 rule). |
| S17 | All 128 profile files are drafts except `CAR_HC.json` (`edited`, approved by JR 2026-11-04). JR is reviewing the drafts now. | Converting the files touches what JR is editing. Wave 3 waits for his in-flight edits (R3, Q7). |

**Scratch estimate (2026-11-07, the §2.2 leans over the 128 current drafts, before band balancing):** 431 sliders inferred; values 2:4, 3:20, 4:89, 5:161, 6:96, 7:47, 8:14; 85 reach 3 or 7 and so show in the voice; 21 profiles land outside 24–36 and need balancing; distinct slider vectors go from 93 to 123 of 128.

## 1. Scope

**In:**
- Inferred values for every no-evidence slider of every coach and owner profile, deterministic per person, recorded in the file, reviewable, lockable by JR, inside 24–36.
- Inferred values reach the core (and so every policy that reads sliders) only through the existing reviewed lifecycle apply.
- Inferred sliders shown as inferred in the review page, the core provenance, the API and the Personality panel.
- The v2 profile shape: durable `motivation`, a `focus` per slot with subject refs, optional `until_season`.
- A focus store with history, an OFFSEASON_START refresh behind the switchboard, template drafting, change notes, JR set and revert.
- The prompt line showing motivation plus current focus within a word budget.
- One reviewed conversion of all 128 files to v2.

**Out (non-goals):**
- Inference for players, scouting directors, writers or GMs (generated or authored already).
- Model-drafted focus text. The store reserves nothing for it; a later plan can add a `model` source (Q6).
- Want/fear in decision code. Focus stays prompt-only.
- Evolution changing motivation, or focus refresh changing sliders or traits.
- Ownership changes (an owner selling the team). Owners keep their team for the life of the universe here.
- Wiring a live prompt caller for coaches or owners (nothing calls `persona_for` yet; the agent loops and owners U5 do that).
- Showing the focus on the site panel (a later display change).

## 2. Design

### 2.1 Profile schema v2 (coach and owner)

`schema_version` becomes `coach-profile-v2` / `owner-profile-v2`. v1 files still validate and still convert to core values and personas, so files can move one at a time; after wave 3 every file is v2. v2 removes top-level `want`/`fear` and adds:

```json
"slider_inference": {"version": "slider-inference-v1",
                     "values": {"temperament": 3, "openness": 6},
                     "locked": []},
"motivation": {"want": "to prove he can build a lasting contender",
               "fear": "being judged against the coach he replaced"},
"focus": {"want": {"text": "a title with Lamar Jackson in his prime",
                   "subjects": [{"kind": "player", "name": "Lamar Jackson", "team_2026": "BAL"}],
                   "until_season": null},
          "fear": null}
```

Rules (added to `validate_profile_file` in both modules):
- `sliders` and `slider_evidence` keep their v1 meaning: a value that is not 5 cites evidence.
- `slider_inference.values` has exactly the **no-evidence sliders**: `sliders[s] == 5` and `s` not in `slider_evidence`. A slider evidenced at 5 is not inferred.
- An unlocked value must equal `slider_inference.infer_sliders(doc)['values'][s]` (so editing traits means re-running `infer`). A slider in `locked` is JR's value, any integer 0–10, and must still be a no-evidence slider.
- The effective sliders (file sliders with inferred values over them) total 24–36.
- `motivation.want` and `motivation.fear`: nonempty, at most `MAX_MOTIVE_WORDS = 14` words, and they contain no focus subject's full name or surname and no tie target's name.
- `focus.want` / `focus.fear`: `null` or `{text (1–MAX_FOCUS_WORDS = 14 words), subjects (0–MAX_SUBJECTS = 2), until_season (null or int >= 2026)}`. A subject is `{kind: 'player'|'gm', name, team_2026}` or `{kind: 'coach', seed_ref}` (a seed seat, not the holder himself).
- The quote check covers motivation and focus text.

`persona_for(doc, view=None, change_notes=None, focus=None)`: for v2, `want`/`fear` are the motivation and `focus_want`/`focus_fear` are the file focus texts, or the stored rows' texts when `focus` (`{'want': row | None, 'fear': row | None}` from `focus_store.read_focus`) is given. For v1, `want`/`fear` as today and no focus keys.

`core_initial_values(doc)` returns `effective_sliders(doc)` and adds `slider:<s>` evidence for each inferred slider from `inferred_evidence(doc, s)`.

### 2.2 Slider inference (`personality/slider_inference.py`, pure)

Constants: `INFERENCE_VERSION = 'slider-inference-v1'`, `INFERRED_SOURCE = 'inferred:slider-inference-v1'`, `JR_SET_SOURCE_PREFIX = 'jr-set:'`, `LEANS_FILE = 'newsroom/config/slider_leans.json'`, `LEAN_CAP = 2`, `INFERRED_RANGE = (2, 8)`, `JITTER_CUTS = (0.25, 0.75)`.

For each no-evidence slider `s` of a profile with key `k` and traits `T`:
1. `lean = clamp(sum(leans[t].get(s, 0) for t in T), -LEAN_CAP, LEAN_CAP)`.
2. `u = inference_draw(k, s)` = first 8 bytes of sha256(json `['slider-inference-v1', k, s]`) / 2^64; `jitter = -1 if u < 0.25 else +1 if u >= 0.75 else 0`.
3. `value = clamp(5 + lean + jitter, 2, 8)`. Inferred values never reach an extreme.
4. **Band balancing:** while the effective total is above 36 (below 24), move the unlocked inferred value farthest from 5 one step toward 5 (ties in `SLIDERS` order). Evidenced and locked values never move. Recorded per slider as `balanced`.

Same key, traits and leans give the same values; the person's key is stable across seats only within a file (a moved coach keeps his file key history in git).

Functions:
- `load_leans(path=LEANS_FILE, catalogue=None) -> {trait: {slider: int}}`: validates known traits, known sliders, −2..+2, at most two sliders per trait.
- `neutral_sliders(doc) -> list[str]`
- `inference_draw(key, slider) -> float`
- `infer_sliders(doc, leans=None, *, locked=None) -> {'version', 'values': {s: int}, 'basis': {s: {'traits': {t: lean}, 'lean', 'jitter', 'balanced'}}}` (`locked` = `{s: value}` kept as given)
- `check_inference(doc, leans=None) -> list[str]` (the §2.1 inference rules)
- `effective_sliders(doc) -> dict`
- `inferred_evidence(doc, slider) -> dict`: unlocked → `{source INFERRED_SOURCE, date review.reviewed_at, confidence 'low', reviewer, reason 'inferred from <traits with leans>; no direct evidence', features ['voice']}`; locked → `{source 'jr-set:<reviewed_at>', confidence 'high', reviewer 'JR', reason 'set by JR at review', features ['all']}`.
- `is_inferred(evidence_entry) -> bool` (`source` starts with `inferred:`)

Proposed leans (`slider_leans.json`, review-only starting points, Q1): FILM_JUNKIE drive +1; ANALYTICS_BELIEVER openness +2; OLD_SCHOOL openness −2; SCHEME_NERD openness +1, drive +1; HISTORIAN openness −1; LOYAL warmth +2; GRUDGE_HOLDER temperament +1, warmth −1; PEACEMAKER temperament −1, warmth +1; MENTOR warmth +1, sociability +1; RING_CHASER drive +2; LEGACY_BUILDER drive +1, confidence +1; HOMETOWN_GUY warmth +1; SPOTLIGHT_SEEKER sociability +2, confidence +1; QUOTABLE sociability +1, confidence +1; GUARDED sociability −2; CLICHE_MACHINE openness −1; MEASURED temperament −1, confidence −1; WORKOUT_FANATIC drive +1; FILM_ROOM_RAT drive +2; PERFECTIONIST drive +1, temperament +1; TEACHER warmth +1, temperament −1; QB_WHISPERER openness +1; DISCIPLINARIAN warmth −1, temperament +1; PLAYERS_COACH warmth +2; YOUTH_DEVELOPER openness +1, warmth +1; SUPERSTITIOUS openness −1; FAMILY_MAN warmth +1; MEDDLER confidence +1, temperament +1; HANDS_OFF temperament −1; DEEP_POCKETS drive +1; BUDGET_CONSCIOUS openness −1; HEADLINE_HUNTER sociability +1, temperament +1; PATIENT_BUILDER temperament −2; CAR_GUY none.

**Where and when:** inference runs at review time in the review tool (`infer --write`, and inside `convert-v2`), so the stored value is fixed, visible in the file and in the review page, and covered by JR's approval. It is not a switchboard feature: it is data that reaches the core only through the `profiles`-gated lifecycle apply, and each policy's own feature still decides whether sliders matter. A new leans version means re-running `infer` and re-review.

### 2.3 Review tool (`tools/coach_profile_review.py`)

- `infer [--kind coach|owner] [--teams ...] [--file ...] [--write]`: prints per file each inferred slider with its basis (traits, lean, seed step, balancing). `--write` stores `slider_inference` (keeps `locked` values and the review status; v2 files only). Exit 0, or 1 when a file fails other validation (that file is not written).
- `convert-v2 [--kind] [--teams] [--file] [--write] [--today YYYY-MM-DD]`: mechanical: schema v2, `want`/`fear` → `motivation`, `focus` `{want: null, fear: null}`, fills `slider_inference`. A reviewed file goes back to `draft` with the note `converted to v2 on <date>: check motivation, focus and inferred sliders`. The judgment part (shortening motivation, writing focus and subjects) is done by hand in wave 3.
- `report`: the slider table has a **source** column: `evidence (e2, e3)`, `inferred (LOYAL +2, seed −1)`, `inferred, balanced`, `set by JR`. New sections **Motivation** and **Current focus** (text, subjects, "refreshes when Lamar Jackson leaves Baltimore, retires or leaves the league").
- `apply-review`: `EDITABLE` adds `slider_inference`, `motivation`, `focus` (keeps `want`, `fear` for v1). JR locks a slider by editing `slider_inference.values` and listing it in `locked`.

### 2.4 Focus model (`personality/focus.py`, pure) and templates

Constants: `FOCUS_RULES_VERSION = 'focus-v1'`, `SLOTS = ('want', 'fear')`, `SUBJECT_KINDS = ('player', 'coach', 'gm')`, `MAX_MOTIVE_WORDS = 14`, `MAX_FOCUS_WORDS = 14`, `MAX_SUBJECTS = 2`, `TEMPLATES_FILE = 'newsroom/config/focus_templates.json'`, `STALE_CAUSES = ('SUBJECT_RETIRED', 'SUBJECT_LEFT_TEAM', 'SUBJECT_LEFT_ROLE', 'SUBJECT_LEFT_LEAGUE', 'SUBJECT_UNRESOLVED', 'HOLDER_MOVED', 'EXPIRED')`.

**Facts shape** (built by `focus_facts`, U4; consumed here):

```python
{'persona_id': str, 'role': 'coach' | 'owner', 'seat': 'HC' | 'OC' | 'DC' | 'OWNER' | None, 'team': 'BAL' | None,
 'team_name': 'Ravens' | None, 'direction': 'CONTEND' | 'RETOOL' | 'REBUILD' | None,
 'made_playoffs': bool | None, 'won_super_bowl': bool | None, 'hot_seat': bool | None,
 'people': {'qb1' | 'young_star' | 'top_defender' | 'top_weapon' | 'head_coach' | 'gm':
            {'kind', 'name', 'binding': {'namespace', 'external_id'}} | None},
 'subject_status': {'<namespace>:<external_id>': {'status': 'with_team' | 'elsewhere' | 'retired' | 'gone'
                                                   | 'not_in_seat' | 'unknown', 'team': str | None}},
 'source_ids': [str], 'missing': {table: reason}}
```

Functions:
- `validate_file_focus(doc) -> list[str]`: the §2.1 motivation and focus rules (the profile validators call it).
- `stale_causes(row, facts, season) -> list[{'cause', 'subject'?}]`: per subject, `retired` → SUBJECT_RETIRED, `elsewhere` → SUBJECT_LEFT_TEAM, `not_in_seat` → SUBJECT_LEFT_ROLE, `gone` → SUBJECT_LEFT_LEAGUE, an unresolved subject → SUBJECT_UNRESOLVED; holder's `team` or `seat` differs from the row's `anchor` → HOLDER_MOVED; `until_season < season` → EXPIRED. `unknown` or a None fact is never stale.
- `load_templates(path=TEMPLATES_FILE) -> list[dict]`: each `{template_id, seats [..], slot, when {direction?, hot_seat?, made_playoffs?, won_super_bowl?}, needs [people keys], traits_any [..] (optional), text}`; text uses `{qb1}`, `{young_star}`, `{top_defender}`, `{top_weapon}`, `{head_coach}`, `{gm}`, `{team_name}`; filled text must fit `MAX_FOCUS_WORDS`.
- `focus_draw(universe_id, persona_id, season, slot) -> float` (sha256 of `['focus-v1', ...]`).
- `draft_focus(*, universe_id, persona_id, season, slot, facts, traits, templates) -> {'text', 'subjects', 'template_id', 'anchor'}`: eligible = seat matches, every `when` key equals the fact (a None fact fails), every `needs` person present; a template whose `traits_any` meets the traits counts twice; pick by the draw over the eligible list sorted by `template_id`. Every (seat, slot) has a `GENERIC_<SEAT>_<SLOT>` template with no `when` and no `needs`, so it never returns nothing. Subjects are the filled people (with bindings).
- `change_note(old_row, new, causes, season) -> str`: e.g. `2031: with Lamar Jackson retired, he now wants a title with Tyler Shough in his prime.` (≤ 30 words).

Templates (U2 writes at least 3 per seat × slot × direction plus hot seat and the generic ones, about 40; examples): owner want CONTEND "a championship while {qb1} is in his prime"; owner want REBUILD "a young core around {young_star} that contends for years" (traits_any PATIENT_BUILDER); owner fear hot_seat "having to fire {head_coach} and start over"; HC want "to win big with {qb1} running his offense"; HC fear hot_seat "that one more losing season costs him the job"; OC want "to build the offense around {top_weapon}"; DC want "a defense built around {top_defender}"; GENERIC_OWNER_WANT "a {team_name} team that wins in January".

### 2.5 Focus store (`db/migrations/2027_02_03_persona_focus.sql`, `personality/focus_store.py`)

Requires `2027_01_16` (core) and `2027_01_24` (lifecycle). Not folded into `db/init_db.sql`; applying it is JR's call. Seeds nothing; a rerun checks the columns (a DO block), like `2027_01_30`.

`persona_focus`:
- `focus_id` TEXT PK (`focus:<universe_id>:<persona_id>:<slot>:<seq>`), `universe_id` TEXT, `persona_type` TEXT CHECK in (`coach`, `owner`), `persona_id` TEXT, FK `(persona_type, persona_id)` → `persona_core_profiles`, `slot` TEXT CHECK in (`want`, `fear`), `seq` INT (0 = from the profile, +1 per later row), `text` TEXT NOT NULL, `subjects` JSONB (`[{kind, name, binding {namespace, external_id} | null}]`), `anchor` JSONB (`{team, seat}`), `until_season` INT NULL, `source` TEXT CHECK in (`profile`, `template`, `jr`), `template_id` TEXT NULL, `rules_version` TEXT, `status` TEXT CHECK in (`active`, `superseded`, `reverted`), `valid_from_season` INT, `valid_from_tick` SMALLINT CHECK 1–32, `supersedes` TEXT NULL FK → `persona_focus(focus_id)`, `stale_causes` JSONB default `[]`, `reason` TEXT NULL (the change note), `actor` TEXT default `system` (`JR` for set and revert), `created_at` timestamptz.
- UNIQUE `(universe_id, persona_id, slot, seq)`; partial UNIQUE `(universe_id, persona_id, slot) WHERE status = 'active'`.

`persona_focus_runs`: PK `(universe_id, season, rules_version)`, `input_hash` TEXT, `counts` JSONB, `status` CHECK in (`APPLIED`, `PREVIEW`, `FAILED`), `error` TEXT, `created_at`.

Store (caller owns the transaction, savepoints, never commits): `tables_ready(cur)`, `insert_focus(cur, row)` (insert once; same id and content → `replayed`; different content → `FocusConflict`), `supersede(cur, old_focus_id, new_row)` (old → `superseded`, new active, one savepoint), `mark_reverted(cur, focus_id)`, `read_focus(cur, universe_id, persona_id, *, as_of=None) -> {'want': row | None, 'fear': row | None}` (`as_of` `(season, tick)`: the row valid then, by `valid_from` and its successor's `valid_from`), `history(cur, universe_id, persona_id)`, `record_run(cur, run)`, `read_run(cur, universe_id, season, rules_version)`.

### 2.6 Refresh run (`personality/focus_facts.py`, `personality/focus_run.py`)

- `focus_facts.facts(cur, universe_id, season) -> {persona_id: facts}` (read-only) for core coaches and owners in the universe. Holder: a coach's seat and team from `coach_tenures` / `coordinator_tenures` covering the season (via his `db-coach` / `db-coordinator` alias); an owner's team from his birth key `curated-owner:<TEAM>:2026`. Team facts: `gm_team_seasons.direction`, playoff and Super Bowl from `games`, `coach_hot_seat` / `coordinator_hot_seat`. People: `qb1` (highest `player_attributes.overall_rating` QB on the active roster, `player_seasons.released_week IS NULL`), `young_star` (age ≤ 25, highest overall), `top_defender`, `top_weapon` (WR/TE/RB), `head_coach`, `gm`. Subject status: a player on the holder's team → `with_team`, another team → `elsewhere`, a `player_retirements` row → `retired`, no roster row → `gone`; a coach still in that seat → `with_team`, elsewhere → `not_in_seat`. A missing table leaves its facts None and is listed in `missing`.
- `seed(cur, universe_id, season, tick, docs)`: for each reviewed v2 file whose person is in the universe (found through the core alias the lifecycle apply wrote: `db-coach` / `db-coordinator` with the seed row's id; owners `db-owner`), inserts seq-0 rows for non-null focus slots, resolving subjects (player by `players.full_name` on the `team_2026` roster in season 2026; coach by `seed_ref`; GM by `gm_profiles` team). An unresolved subject keeps `binding: null`. Idempotent.
- `run_season(cur, universe_id, season, *, dry_run=False, docs=None, templates=None) -> report`: seed, read facts, and at `(season, tick 24)` for every active row: no causes → keep; causes and `source` in (`profile`, `template`) → `draft_focus` and `supersede` with `stale_causes`, `reason = change_note(...)`, `source 'template'`; causes and `source 'jr'` → reported as `jr_stale`, never replaced. A null focus slot stays null unless the holder moved (then one is drafted). `noop` when an APPLIED run has the same `input_hash` (sha256 of the facts and active rows). `dry_run` writes nothing. Report: `{status, counts {people, rows, kept, replaced | would_replace, jr_stale, by_cause, missing_sources}, changes [{persona_id, slot, old, new, causes, note}]}`.
- `render_report(report)` (deterministic markdown), `report_path(universe_id, season)` = `verify_output/personality_focus/focus_<universe>_<season>.md`, `record_failure(cur, universe_id, season, error)`.
- `set_focus(cur, universe_id, persona_id, slot, *, text, subjects, reason, season, tick)`: a JR row (`source 'jr'`, `actor 'JR'`), supersedes the active one. Validated by the same text and subject rules.
- `revert(cur, focus_id, reason)`: the reverted row → `reverted`; a new JR row copies the row it superseded (text, subjects, anchor), `reason 'revert:<focus_id>: <reason>'`. Append-only.
- CLI `python3 -m personality.focus_run`: `season --season S [--dry-run] [--universe U] [--dsn DSN]`, `report --season S [--out PATH]`, `show --persona ID [--as-of S:T]`, `set --persona ID --slot want|fear --text TEXT [--subject kind:namespace:external_id ...] --reason TEXT [--season S --tick T]`, `revert --focus-id ID --reason TEXT`. `DATABASE_URL` or `--dsn`, refusing `madden_2036` and `postgres`; JSON out; commits unless `--dry-run`; exit 0 / 2 invalid / 3 not found / 4 conflict.

### 2.7 Switchboard and watcher

- `personality/features.py`: `FEATURES` gains `focus_refresh` right after `evolution` (Q4); `REQUIREMENTS['focus_refresh'] = ('personality.focus_run', '2027_02_03_persona_focus.sql')`.
- `pipeline/watch_save.py`: `FOCUS_ENV = 'MU_FOCUS_REFRESH'`; `focus_refresh_on(modes=None)`; `step_focus_refresh(snapshot_path, save_season, dsn=None, log_fn=log, connect=None, modes=None)` right after `step_personality_evolution`, on the OFFSEASON_START ingest (tick 24) only, before the league office and trade-request steps. Shadow → `run_season(dry_run=True)` and the report; active → applies. Own connection and transaction; skipped with a logged reason when the tables aren't migrated or no universe is registered; never fails the ingest (logged, notified, FAILED run recorded). Notifies "Mu: N focus refreshes in <season>". The ingest result carries `focus_refresh`.

### 2.8 Prompt line (`newsroom/personas.py` `render_persona`)

With `focus_want` or `focus_fear` present: `IN THE BACKGROUND (motivation only; do not state it): you want {want}, and right now {focus_want}; you fear {fear}, and right now {focus_fear}.` (a missing focus drops its "and right now" clause). Without focus keys the line is byte for byte today's, so writers and v1 personas don't change. With the word caps the two slots add up to at most 56 words of content, against today's 54-word maximum (27 + 27).

### 2.9 Site display (`server/personality_api.py`, Personality panel)

`slider_rows(sliders, evidence=None)` adds `inferred: bool` per row (`slider_inference.is_inferred(evidence.get('slider:' + name))`); `build_profile` passes the profile's evidence. `types/personality.ts` gets `inferred?: boolean`; `PersonalityPanel.tsx` shows a small "inferred" tag after the slider bar with the tooltip "No direct evidence; estimated from his traits." No other number or field.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Leans that echo a trait double its policy effect (PATIENT_BUILDER +0.10 line and, via temperament −2, another +0.02). | The spike prints owner `patience` for all 32 owners before and after. | Drop the echoing lean from `slider_leans.json` (new version); the effect is small either way (0.01 a point). |
| R2 | An inferred value reads as fact. | Review page, core provenance, API and panel all mark it. | — |
| R3 | Wave 3 rewrites the files JR is reviewing now. | Ask JR before wave 3 (Q7). | Hold wave 3 until his in-flight edits are committed; conversion keeps his edits since it runs on main. |
| R4 | Subjects named in 2026 don't match the save's player names. | `seed` lists unresolved subjects; they come up SUBJECT_UNRESOLVED at the first run. | The first run replaces that focus from a template; JR can `set` it. |
| R5 | Template text gets repetitive across 128 people. | The first shadow report lists every new focus. | Add templates (a new rules version); JR sets any he dislikes. |
| R6 | Inserting `focus_refresh` into the D2 order makes later features wait for it. | Nothing is active anywhere (S12); `test_personality_features.py` is updated. | Move it (Q4). |
| R7 | Owner holders can't be found until owners U6 creates `team_owners` and `db-owner`. | `seed` reports owners skipped. | Owner focus refresh starts once owners U6 lands; nothing else waits. |
| R8 | Prompt length grows. | U3 test: the rendered background line stays ≤ 60 words for the longest allowed texts. | Lower the caps. |
| R9 | Evolution moves an inferred slider without new evidence, and it still reads "inferred". | Known. | The panel's tag means "no direct evidence", which stays true. |
| R10 | Changing a profile's traits silently moves its inferred values. | The validator fails until `infer --write` is re-run, so the change shows in the diff and the review page. | — |

**Spike first:** the orchestrator, before wave 1 (about 30 minutes, offline): apply the §2.2 leans to the 128 current drafts with balancing, and print the value histogram, how many show in the voice, distinct slider vectors, and owner `patience` / coach loop outputs before and after. A first pass without balancing is in §0. If JR wants different leans, they change in `slider_leans.json` before U1 is built.

## 4. Tests (synthetic, no live DB)

- `tests/test_slider_inference.py` → leans load and reject bad entries; only no-evidence sliders inferred (a slider evidenced at 5 stays); same key gives same values, different keys differ; LEAN_CAP and 2–8 range; band balancing order and that evidenced/locked never move; `check_inference` catches stale values after a trait edit, accepts locked values; `inferred_evidence` passes `core.validate_profile`; `is_inferred`.
- `tests/test_focus.py` → `validate_file_focus` (word caps, subject shapes, motivation naming a subject, until_season); every stale cause, and that `unknown`/None is never stale; HOLDER_MOVED; template loading and word caps; `draft_focus` deterministic, honours `when`/`needs`/`traits_any`, falls back to GENERIC; `change_note` text.
- `tests/test_focus_store.py` → migration text (tables, checks, partial unique, requires lines); insert replay and conflict; supersede; `read_focus` as-of across two rows; history; runs (fake cursor; one opt-in test needs `FOCUS_TEST_DSN` on a scratch DB).
- `tests/test_coach_profiles.py`, `tests/test_owner_profiles.py` → v2 validation (inference block keys, unlocked mismatch, locked, effective band, motivation/focus rules); v1 still valid; `core_initial_values` overlays inferred values with provenance; `persona_for` v1/v2 and with stored focus rows.
- `tests/test_coach_profile_review.py` → `infer` output and `--write`; `convert-v2` (fields moved, reviewed file back to draft with note, `--write` only when valid); the report's source column and focus section; `apply-review` of the new fields.
- `tests/test_personas.py` → background line with focus; unchanged byte for byte without; ≤ 60 words at the caps.
- `tests/test_focus_run.py` → facts over a fake cursor (holder seat, people, subject status, missing tables); seed (resolved and unresolved subjects, drafts skipped, idempotent); `run_season` keep/replace/jr_stale/noop/dry_run; report; `set_focus`; `revert`; CLI exit codes and DSN guard.
- `tests/test_focus_refresh_wiring.py` → the watcher step runs only at tick 24, honours the kill switch and modes, shadow writes nothing, skips unmigrated tables, never fails the ingest.
- `tests/test_personality_features.py` → `focus_refresh` in the D2 order with its requirement.
- `tests/test_personality_api.py` → `inferred` true only for `inferred:` provenance; no new numbers in the body.

Run with `pytest -m "not integration" <files>`. The opt-in store test runs against a scratch `madden_test` schema only.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Slider inference

- **Owns:** `personality/slider_inference.py` (new), `newsroom/config/slider_leans.json` (new), `tests/test_slider_inference.py` (new)
- **Reads:** this plan §2.1–§2.2, `personality/coach_profiles.py`, `personality/core.py` (validate_profile evidence rules), `newsroom/personas.py` (SLIDERS, band), `newsroom/config/persona_traits.json`
- **Depends on:** none
- **Done when:** every §2.2 function exists with that signature; leans are the §2.2 table (or JR's edit); tests pass.
- **Verify:** `pytest -m "not integration" tests/test_slider_inference.py` passes.

### U2: Focus model, templates and store

- **Owns:** `personality/focus.py` (new), `newsroom/config/focus_templates.json` (new), `personality/focus_store.py` (new), `db/migrations/2027_02_03_persona_focus.sql` (new), `tests/test_focus.py` (new), `tests/test_focus_store.py` (new)
- **Reads:** this plan §2.1, §2.4–§2.5, `personality/evolution_store.py`, `db/migrations/2027_01_30_personality_evolution.sql`, `db/migrations/2027_01_16_personality_core.sql`, `personality/coach_profiles.py` (tie target shapes)
- **Depends on:** none
- **Done when:** functions and tables as in §2.4–§2.5; about 40 templates with a GENERIC per (seat, slot); tests pass.
- **Verify:** `pytest -m "not integration" tests/test_focus.py tests/test_focus_store.py` passes.

### U3: Profile schema v2, review tool and prompt line

- **Owns:** `personality/coach_profiles.py`, `personality/owner_profiles.py`, `tools/coach_profile_review.py`, `newsroom/personas.py`, `newsroom/config/coach_personas/_TEMPLATE.json`, `newsroom/config/owner_personas/_TEMPLATE.json`, `tests/test_coach_profiles.py`, `tests/test_owner_profiles.py`, `tests/test_coach_profile_review.py`, `tests/test_personas.py`
- **Reads:** this plan §2.1–§2.3, §2.8; `personality/slider_inference.py`, `personality/focus.py`
- **Depends on:** U1, U2
- **Done when:** v1 and v2 files validate by §2.1; `infer` and `convert-v2` work (dry run over the real directories prints without writing); the report shows the source column and focus; the background line as §2.8; templates are v2.
- **Verify:** `pytest -m "not integration" tests/test_coach_profiles.py tests/test_owner_profiles.py tests/test_coach_profile_review.py tests/test_personas.py` passes; `python3 -m tools.coach_profile_review convert-v2 --kind owner` prints 32 conversions and writes nothing.

### U4: Focus refresh run, switchboard and watcher step

- **Owns:** `personality/focus_facts.py` (new), `personality/focus_run.py` (new), `personality/features.py`, `pipeline/watch_save.py`, `personality/README.md`, `tests/test_focus_run.py` (new), `tests/test_focus_refresh_wiring.py` (new), `tests/test_personality_features.py`
- **Reads:** this plan §2.4–§2.7; `personality/focus.py`, `personality/focus_store.py`, `personality/evolution_facts.py`, `personality/evolution_run.py`, `personality/lifecycle_store.py`, `personality/store.py` (aliases), `tests/test_personality_evolution_wiring.py`
- **Depends on:** U2
- **Done when:** §2.6–§2.7 built; `focus_refresh` in `FEATURES`; README documents slider_inference, focus, focus_store, focus_facts and focus_run; tests pass.
- **Verify:** `pytest -m "not integration" tests/test_focus_run.py tests/test_focus_refresh_wiring.py tests/test_personality_features.py tests/test_personality_evolution_wiring.py` passes; `python3 -m personality.focus_run --help` lists the five subcommands.

### U5: Inferred flag on the site

- **Owns:** `server/personality_api.py`, `Mu_Network/src/types/personality.ts`, `Mu_Network/src/components/personality/PersonalityPanel.tsx`, `tests/test_personality_api.py`
- **Reads:** this plan §2.9, `personality/slider_inference.py`
- **Depends on:** U1
- **Done when:** slider rows carry `inferred`; the panel labels inferred sliders; tests and build pass.
- **Verify:** `pytest -m "not integration" tests/test_personality_api.py` passes; `npm run build` in `Mu_Network/` succeeds.

### U6: Convert AFC coach profiles to v2

- **Owns:** `newsroom/config/coach_personas/{BUF,MIA,NE,NYJ,BAL,CIN,CLE,PIT,HOU,IND,JAX,TEN,DEN,KC,LV,LAC}_{HC,OC,DC}.json` (48 files)
- **Reads:** this plan §2.1, §2.4; `tools/coach_profile_review.py`
- **Depends on:** U3
- **Done when:** each file is v2: `convert-v2 --write`, then by hand a durable motivation (no names, ≤ 14 words) and a focus (≤ 14 words, subjects for any named person, or null when the old want/fear was already durable), then `infer --write`; status stays draft; `validate` passes for all 48; a short list of judgment calls for JR in the unit report.
- **Verify:** `python3 -m tools.coach_profile_review validate --teams BUF,MIA,NE,NYJ,BAL,CIN,CLE,PIT,HOU,IND,JAX,TEN,DEN,KC,LV,LAC` exits 0.

### U7: Convert NFC coach profiles to v2

- **Owns:** `newsroom/config/coach_personas/{DAL,NYG,PHI,WAS,CHI,DET,GB,MIN,ATL,CAR,NO,TB,AZ,LAR,SF,SEA}_{HC,OC,DC}.json` (48 files)
- **Reads:** as U6
- **Depends on:** U3
- **Done when:** as U6 for the NFC; `CAR_HC.json` goes back to draft with the conversion note.
- **Verify:** `python3 -m tools.coach_profile_review validate --teams DAL,NYG,PHI,WAS,CHI,DET,GB,MIN,ATL,CAR,NO,TB,AZ,LAR,SF,SEA` exits 0.

### U8: Convert owner profiles to v2

- **Owns:** `newsroom/config/owner_personas/<TEAM>.json` (the 32 team files, not `_TEMPLATE.json`)
- **Reads:** as U6
- **Depends on:** U3
- **Done when:** as U6 for owners (the `(medium)` confidence tags drop out of motivation and focus text).
- **Verify:** `python3 -m tools.coach_profile_review validate --kind owner` exits 0.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2 | — | new files only; no shared file, no dependency |
| 2 | U3, U4, U5 | U1, U2 | U3 needs both; U4 needs U2; U5 needs U1; their files don't overlap |
| 3 | U6, U7, U8 | U3 (and JR's go-ahead, Q7) | disjoint file sets; each runs U3's tools |

**File ownership check:**

| File | Unit |
|---|---|
| `Mu_Network/src/components/personality/PersonalityPanel.tsx` | U5 |
| `Mu_Network/src/types/personality.ts` | U5 |
| `db/migrations/2027_02_03_persona_focus.sql` | U2 |
| `newsroom/config/coach_personas/_TEMPLATE.json` | U3 |
| `newsroom/config/coach_personas/{AFC 16 teams}_{HC,OC,DC}.json` | U6 |
| `newsroom/config/coach_personas/{NFC 16 teams}_{HC,OC,DC}.json` | U7 |
| `newsroom/config/focus_templates.json` | U2 |
| `newsroom/config/owner_personas/_TEMPLATE.json` | U3 |
| `newsroom/config/owner_personas/<TEAM>.json` (32) | U8 |
| `newsroom/config/slider_leans.json` | U1 |
| `newsroom/personas.py` | U3 |
| `personality/README.md` | U4 |
| `personality/coach_profiles.py` | U3 |
| `personality/features.py` | U4 |
| `personality/focus.py` | U2 |
| `personality/focus_facts.py` | U4 |
| `personality/focus_run.py` | U4 |
| `personality/focus_store.py` | U2 |
| `personality/owner_profiles.py` | U3 |
| `personality/slider_inference.py` | U1 |
| `pipeline/watch_save.py` | U4 |
| `server/personality_api.py` | U5 |
| `tests/test_coach_profile_review.py` | U3 |
| `tests/test_coach_profiles.py` | U3 |
| `tests/test_focus.py` | U2 |
| `tests/test_focus_refresh_wiring.py` | U4 |
| `tests/test_focus_run.py` | U4 |
| `tests/test_focus_store.py` | U2 |
| `tests/test_owner_profiles.py` | U3 |
| `tests/test_personality_api.py` | U5 |
| `tests/test_personality_features.py` | U4 |
| `tests/test_personas.py` | U3 |
| `tests/test_slider_inference.py` | U1 |

No path appears twice. The AFC and NFC coach sets are disjoint team lists.

## 7. Spec changes when built

- §7.11 Real-coach profiles and Owner profiles: v2 schema (`slider_inference`, `motivation`, `focus`), v1 still read, the inference rules and leans, `core_initial_values` overlay and `inferred:` provenance, `persona_for(..., focus=)`, the conversion status of the 128 files.
- §7.11: new paragraphs **Inferred sliders** (§2.2) and **Refreshing focus** (§2.4–§2.6: facts, staleness, templates, store, run, revert, set, change notes).
- §7.11 Feature switchboard: `focus_refresh` in `FEATURES` after `evolution`, its requirement.
- §7.11 prompt block description (want/fear item and "Prompt block" order): motivation plus "and right now" focus.
- §3.3p: `persona_focus`, `persona_focus_runs` (`2027_02_03`, requirements, not in `init_db.sql`).
- §6.0b: the `step_focus_refresh` step after evolution.
- §4.8: slider rows' `inferred`; §5.2 item 14: the panel's inferred tag.
- §1.2: `coach_profile_review infer` and `convert-v2`; the `personality.focus_run` CLI row.
- `PERSONALITY_PLAN.md` §5.3 migration reservations: `2027_02_03_persona_focus.sql`.
- Repository Layout: `personality/` line (slider_inference.py, focus.py, focus_store.py, focus_facts.py, focus_run.py), `newsroom/config/` (slider_leans.json, focus_templates.json), `db/migrations/` (2027_02_03), `tests/` (test_slider_inference.py, test_focus.py, test_focus_store.py, test_focus_run.py, test_focus_refresh_wiring.py).

## 8. Decisions for JR

The /propose workflow was run by an agent that could not ask JR directly; it took the recommended answer for each question below and kept going. Each one is still open.

| # | Question | Recommendation |
|---|---|---|
| Q1 | Are the §2.2 trait leans right (which slider each trait pushes, by 1 or 2)? | Approve as listed; they are review-only and a later version can tune them. Look hardest at the owner traits, since 18 owners have all six sliders inferred. |
| Q2 | Should two people with the same traits still get different inferred values? | Yes: a seeded ±1 nudge on about half the inferred sliders (§2.2 step 2). Without it, same traits means same sliders. |
| Q3 | Should inferred values feed decisions, or the prompt voice only? | Both, through the core like any slider. That answers "won't they all behave the same?". The effect is small (owner patience moves 0.01 a point), and traits still matter most. |
| Q4 | Where does `focus_refresh` go in the go-live order? | Right after `evolution`. Nothing is active yet, so this changes no live mode. |
| Q5 | Is a refreshed focus applied automatically, or does it wait for your review? | Automatically, with a change note in the season report. You can `revert` or `set` it. A focus you set yourself is never replaced automatically; the run only reports it when it goes stale. |
| Q6 | Should a model draft new focus text? | Not now. Templates only; model drafting can come in a later plan. |
| Q7 | When should the 128 files be converted, given you are reviewing the drafts now? | Commit your in-flight review edits, then run wave 3, then review each profile once in its final v2 shape. Waves 1–2 don't touch the profile files and can run now. |
| Q8 | `CAR_HC.json` is already approved. Does it go back to draft after conversion? | Yes, with the conversion note. It is the only reviewed file, and its new motivation, focus and inferred sliders need your look. |
| Q9 | Should inference also cover scouting directors and players? | No. They are generated and already have full slider spreads. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | build/isf-u1 | .claude/worktrees/isf-u1 | merged | c5c7852 |
| U2 | 1 | build/isf-u2 | .claude/worktrees/isf-u2 | merged | 01020d0 |
| U3 | 2 | build/isf-u3 | .claude/worktrees/isf-u3 | merged | 0fe2da1 |
| U4 | 2 | build/isf-u4 | .claude/worktrees/isf-u4 | merged | 38b3084 |
| U5 | 2 | build/isf-u5 | .claude/worktrees/isf-u5 | merged | 6d81d3d |
| U6 | 3 | build/isf-u6 | .claude/worktrees/isf-u6 | merged | b72e263 |
| U7 | 3 | build/isf-u7 | .claude/worktrees/isf-u7 | merged | 3890fe3 |
| U8 | 3 | build/isf-u8 | .claude/worktrees/isf-u8 | merged | 64e922f |
