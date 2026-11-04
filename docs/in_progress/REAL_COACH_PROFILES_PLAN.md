# Project Mu: Real-Coach Profiles Plan

**Status:** approved by JR (2026-11-03), Q1–Q3 settled as recommended (§8); building, wave 1 of 3. The other plans' pointers were updated at approval (§7).
**Builds on:** [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §4.3–§4.5, §5.1 (coach scope), D5, D11, D13; [`PERSONALITY_PANTHERS_SAMPLE.md`](../in_progress/PERSONALITY_PANTHERS_SAMPLE.md) (the reviewed-evidence method); [`PERSONALITY_CORE_EXTENSIONS_PLAN.md`](../done/PERSONALITY_CORE_EXTENSIONS_PLAN.md) D1 (full reads of reviewed real profiles); [`PERSONALITY_DRAFT_INTEGRATION_PLAN.md`](../done/PERSONALITY_DRAFT_INTEGRATION_PLAN.md) §2.1, §2.5 (identity registration and reviewed manifests); [`PERSONALITY_RELATIONSHIPS_PLAN.md`](../done/PERSONALITY_RELATIONSHIPS_PLAN.md) §2.2–§2.3 (`reviewed_evidence` starting ties); PROJECT_SPEC §3.3a, §3.3p, §7.11.

**Goal:** Give every real head coach and coordinator in the 2026 save a personality shaped to his real counterpart, as a fictional character inspired by him (D13). Many of them will still be in the league in 2034, and today they have no persona at all, so pressers and inbox talks give every coach the same neutral voice. Each profile is a full character like the writer personas: sliders, traits, football views, a want and a fear, a voice, signature habits and a few reviewed starting relationships. Every claim cites a dated public source, and JR reviews each one before it counts.

**Delivers:**
- One reviewed profile file per coach, in `newsroom/config/coach_personas/<TEAM>_<ROLE>.json`: 96 files for the 32 HCs and 64 OCs and DCs in `db/seed_clean_db.py`.
- `personality/coach_profiles.py`: load, validate, and convert to core initial values, lifecycle manifest entries, a voice persona for prompts and relationship tie events.
- `tools/coach_profile_review.py`: `validate`, `report` (a review page per batch with every claim and its source) and `apply-review` (records JR's decisions in the files).
- A template `newsroom/config/coach_personas/_TEMPLATE.json`, and tests.

**JR's answers (2026-11-03):**
- D1: Research agents draft the profiles during the build. Claude Code agents with web search write one file per coach from dated public sources (quotes, press, coaching tree), each claim cited. JR reviews them in batches on a generated review page and approves, edits or rejects each. No new model-API code.
- D2: Head coaches and coordinators: all 32 HCs and their 64 OCs and DCs in the 2026 save.
- D3: A full character: sliders shaped by the evidence (within 24–36), 3–5 traits, 6–10 football views with triggers, one want and one fear, voice notes, and 2–5 signature habits.
- D4: Only reviewed, well-documented ties among people in the league (coaching-tree mentors, a coach's past with a player or GM) are seeded at the start. There are none with reporters; everything else forms from the simulation.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `db/seed_clean_db.py:69` `COACHES_DATA` (32 rows: team, name, slant, schemes, tendencies, start season) and `:105` `COORDINATORS_DATA` (64 rows: team, OC/DC, name, side, scheme, playbook, tendencies, start). `:267` `seed_coaches_and_coordinators` writes `coaches` (`coach_name` UNIQUE, `db/init_db.sql:637`) and `coordinators` (name not unique, `:661`). | These are the 96 people. A file is keyed by its 2026 team and role (`KC_HC`, `KC_OC`, `KC_DC`), and records a `seed_ref` to its row, which is the reviewed mapping to the database. |
| S2 | The press conference plan, S6 (`PRESS_CONFERENCE_PLAN.md:36`): no coach has a persona; `persona_sliders` allows `coach` and the core supports role `coach`. Its R9 and D5 leave AI coaches neutral until profiles exist; `:225` reads `temperament` once a coach has a personality. | Pressers and the inbox are the consumers. They need a render-ready persona (`persona_for`) plus the core profile. |
| S3 | `newsroom/config/agent_personas.json` writers hold `voice`, `sliders`, `traits`, `views` (`[{text, when: [tags]}]`), `signatures`, `want`, `fear`. `newsroom/personas.py:746` `render_persona` builds the prompt block from that shape; `:39–48` `TOPIC_TAGS`, `SIGNAL_TAGS`, `ALL_TAGS` are the tags a view's `when` may name; `:156` rejects unknown tags. | Coach files use the same shape, so `render_persona` works unchanged, and views are validated against `ALL_TAGS`. |
| S4 | `docs/in_progress/PERSONALITY_PANTHERS_SAMPLE.md`: dated primary sources, evidence versus inference, a confidence on each mapping, no exact-quote imitation, history through 2025 (its review item 3). It kept every slider at 5 only because the old exact-30 budget forced a compensating move. | The method carries over. With the 24–36 band, sliders can move on evidence alone; a slider with no evidence stays 5. |
| S5 | `personality/core.py:120` `initialize(identity, 'real', season, sliders=, traits=, evidence=)` takes reviewed values. Evidence entries need `source`, `date`, `confidence`, `reviewer`, `reason`, `features` (`:99–114`). After the core extensions plan (D1), `features` is provenance only and `real` profiles read in full. | `core_initial_values` turns a file into exactly that shape. `features: ['all']`. |
| S6 | The lifecycle plan registers existing coaches' identities and aliases (`db-coach`, `db-coordinator` on role `coach`) and initializes them "only from that workstream's reviewed manifest" (§2.1, §2.5). | This plan defines that manifest entry (`lifecycle_entries`). The lifecycle plan applies it; this plan writes nothing to a database. |
| S7 | The relationships plan's envelope allows `basis: 'reviewed_evidence'`, and §2.3 lets a reviewed real-coach profile "supply a narrow starting tie … never invented past simulation events". The `meaningful_interaction` payload carries `relationship_kind` (mentorship, support, conflict, collaboration), `outcome` and `significance`. | Ties become `meaningful_interaction` events with `basis 'reviewed_evidence'` at the league's first tick, marked `prehistory`. They are appended once identities exist. |
| S8 | `newsroom/config/persona_traits.json` gives each trait per-role odds; `personality/core.py:93` rejects a trait the role can't draw. The Teaching category (Teacher, QB Whisperer, Disciplinarian, Players' Coach, Youth Developer) is coach and scout only (PERSONALITY_PLAN §4.2). | The validator checks coach eligibility, conflicts and the two-per-category rule, using the same core validation. |
| S9 | GM personas are made by `engines/gm_personas.py` (`:133` template, `:189` LLM name and bio), not taken from a real-GM list. | A tie to a GM resolves only if that GM persona is the same person. Otherwise the tie is reported unresolved and dropped (§8 Q3). |

## 1. Scope

**In:**
- The profile file schema and template.
- A validator with the method's rules (cited evidence on every non-neutral slider and every trait, a source cutoff, no long verbatim quotes, coach-eligible traits, known view tags).
- Conversions to core initial values, lifecycle manifest entries, a render-ready voice persona and relationship tie events.
- The review report and the recording of JR's decisions.
- Research of all 96 coaches, in four batches.

**Out (non-goals):**
- Writing profiles into a database, and registering identities: the lifecycle plan applies the reviewed manifest.
- Appending ties: the relationships store, once built; this plan only builds the events.
- Using the persona in pressers or the inbox: those plans call `persona_for`.
- Coaches created later by the carousel: they get generated profiles at creation (lifecycle U4).
- Coach effects on development or depth charts: the development/coaching plan.
- Players' and GMs' profiles: generated, and adopted by the core extensions plan.
- Real-world events after 2025 as history: the simulation writes 2026 on.

## 2. Design

### 2.1 Profile file (`coach-profile-v1`)

`newsroom/config/coach_personas/<TEAM>_<ROLE>.json`, for example `KC_HC.json`:

```json
{
  "schema_version": "coach-profile-v1",
  "key": "coach:KC:HC", "name": "Andy Reid", "role": "HC", "team": "KC",
  "seed_ref": {"table": "COACHES_DATA", "team": "KC", "role": "HC", "name": "Andy Reid"},
  "sliders": {"temperament": 3, "sociability": 6, "confidence": 6, "openness": 7, "warmth": 8, "drive": 8},
  "slider_evidence": {"warmth": ["E2"], "openness": ["E3"]},
  "traits": ["TEACHER", "QB_WHISPERER", "SCHEME_NERD"],
  "trait_evidence": {"TEACHER": ["E1", "E4"], "QB_WHISPERER": ["E1"], "SCHEME_NERD": ["E3"]},
  "views": [{"text": "...", "when": ["QB", "YOUTH"]}],
  "signatures": ["..."],
  "want": "...", "fear": "...",
  "voice": "Two to four sentences on how he talks: register, pace, what he deflects, what he praises. Style, never his exact phrases.",
  "ties": [{"target": {"kind": "coach", "seed_ref": {"table": "COACHES_DATA", "team": "PHI", "role": "HC"}},
            "relationship_kind": "mentorship", "outcome": "positive", "evidence": ["E5"]}],
  "evidence": [{"id": "E1", "url": "https://...", "publisher": "...", "published": "2024-09-19",
                "accessed": "2027-01-10", "summary": "A paraphrase of what the source shows, and what it does not.",
                "confidence": "high"}],
  "review": {"status": "draft", "reviewer": null, "reviewed_at": null, "notes": null}
}
```

**Rules** (`validate_profile_file`; each failure is a message, not an exception):
- **Identity:** `key`, `team` and `role` match the file name. `seed_ref` matches exactly one `COACHES_DATA` / `COORDINATORS_DATA` row.
- **Sliders:** six sliders, 0–10, total 24–36. Every slider that isn't 5 cites at least one evidence ID.
- **Traits:** 3–5. Each cites evidence, is eligible for role `coach`, and passes the conflict and two-per-category rules (through `personality.core.validate_profile`).
- **Views:** 6–10, each with 1–3 `when` tags from `newsroom.personas.ALL_TAGS`. `signatures` 2–5. `want`, `fear` and `voice` are non-empty.
- **Evidence:**
  - `published` ≤ `SOURCE_CUTOFF = 2025-12-31` (§8 Q1), with a URL and publisher;
  - confidence `low`, `medium` or `high`;
  - a `low` source can support only views, voice or signatures, never a slider or trait;
  - no field quotes more than `MAX_QUOTE_WORDS = 15` consecutive words from a source `summary`, as a check against copying.
- **Ties:** `kind` is `coach`, `player` or `gm`; each tie cites evidence. A coach target uses `seed_ref`; a player or GM target uses `{name, team_2026}`. No reporters.
- **Review:** `status` is `draft`, `approved`, `edited` or `rejected`. Only `approved` and `edited` files leave this plan's tools.

### 2.2 Module (`personality/coach_profiles.py`, pure apart from reading files)

```python
SCHEMA_VERSION = 'coach-profile-v1'; SOURCE_CUTOFF = '2025-12-31'; MAX_QUOTE_WORDS = 15
PROFILE_DIR = 'newsroom/config/coach_personas'
def seed_index() -> dict                                   # {(table, team, role): row} from db.seed_clean_db
def load_profiles(directory=PROFILE_DIR, *, teams=None) -> list[dict]
def validate_profile_file(doc, *, catalogue=None, index=None) -> list[str]
def core_initial_values(doc) -> dict                       # {sliders, traits, evidence} for core.initialize(..., 'real', ...)
def lifecycle_entries(docs) -> list[dict]                  # approved/edited only
def persona_for(doc, view=None) -> dict                    # render_persona-ready
def tie_events(doc, resolve) -> tuple[list[dict], list[dict]]   # (envelopes, unresolved)
```

- **`core_initial_values`:** each cited slider or trait becomes a core evidence entry keyed `slider:<name>` / `trait:<id>`:
  - `source` = the first evidence URL, `date` = its `published`;
  - `confidence` = the lowest of the cited sources;
  - `reviewer` = `review.reviewer`, `reason` = the cited summaries joined;
  - `features: ['all']`.
- **`lifecycle_entries`:**
  - `role`: `'coach'`; `origin`: `'real'`;
  - `birth_key`: `'curated-coach:<TEAM>:<ROLE>:2026'`;
  - `alias`: `{namespace: 'db-coach' | 'db-coordinator', seed_ref}`;
  - `initial_values` and `review_receipt` (`{reviewer, reviewed_at, file_sha256, schema_version}`).

  The lifecycle plan resolves `seed_ref` to `coaches.id` (by the unique name) or `coordinators.id` (name, team and role through `coordinator_tenures`), and binds the alias in its reviewed apply.
- **`persona_for(doc, view)`:** `{voice, sliders, traits, views, signatures, want, fear}`. When a core feature view is passed, its sliders and traits replace the file's, so overrides and later evolution show up. The file keeps the voice, views and want/fear.
- **`tie_events(doc, resolve)`:**
  - `resolve(target)` returns a PersonRef or None;
  - one `meaningful_interaction` per resolved tie: `basis 'reviewed_evidence'`, `occurred_at {season: 2026, tick: 1}`;
  - `payload {relationship_kind, outcome, significance: 'meaningful', opportunity_ref: null, prehistory: true}`, visibility `private`;
  - `source_event_id` = `mu:coach-tie:<key>:<n>`.

  Unresolved ties come back with a reason.

### 2.3 Review tool (`tools/coach_profile_review.py`)

```text
python -m tools.coach_profile_review validate [--dir DIR] [--teams BUF,MIA,...]
python -m tools.coach_profile_review report --teams BUF,MIA,... --out verify_output/coach_review/<batch>.md
python -m tools.coach_profile_review apply-review --decisions PATH [--dir DIR]
```

- **`validate`:** prints each file's errors. Exit 0 when all pass, 1 otherwise.
- **`report`:** writes one review page per batch. Per coach:
  - his sliders with the reasons;
  - each trait with its sources (linked);
  - his views, want/fear, voice and signatures;
  - his ties.

  Each claim carries its confidence, and each coach gets a check box line for JR's decision.
- **`apply-review`:**
  - reads `{"<key>": {"status": "approved|edited|rejected", "notes": "...", "edits": {field: value}}}`;
  - applies the edits, re-validates, and stamps `review` with reviewer JR and today's date;
  - refuses an edit that fails validation.

  It is offline throughout: no database, no model calls.

### 2.4 Research batches (build-time work, D1)

Each research agent handles one batch of 8 teams (24 coaches). For every coach it:
1. finds 3–8 dated public sources through 2025 (team sites, major outlets, coaching-tree records, long interviews);
2. paraphrases what each shows, and what it doesn't;
3. maps the evidence to sliders, traits, views, want/fear, voice, signatures and ties under §2.1;
4. leaves a slider at 5 when nothing supports moving it.

Rules from the Panthers method:
- Narrow inferences only; evidence and inference kept apart.
- No imitation of exact phrases, and no private motives beyond what sources show. A want and fear may be a reasoned reading of his public career, marked medium confidence.
- No later real-world events.
- The agent runs `validate` on its batch before committing.

Batches: AFC East+North, AFC South+West, NFC East+North, NFC South+West.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Thin sources for some coordinators. | `report` shows each coach's evidence count; the validator flags traits without sources. | Fewer traits isn't allowed (3 minimum), so a thin coach gets 3 traits from what is documented, more sliders left at 5, and medium confidence; JR sees that on the page. |
| R2 | Research cost: 96 coaches × several searches and pages (estimate: a few hours of agent time across four batches). | Track agent time per batch. | Batches run one at a time. JR can stop after any batch. |
| R3 | Inferences read as claims about the real person. | The method: paraphrases, confidence on each mapping, a voice that describes style, review before use. | JR rejects or edits on review. Profiles are fictional characters (D13) and stay private context unless a consumer's visibility rule says otherwise. |
| R4 | A coordinator in `COORDINATORS_DATA` is out of date for the actual 2026 save. | The lifecycle plan matches `seed_ref` against the save-seeded tables at apply. | An unmatched file is reported and not applied; refresh the seed data first. |
| R5 | Coach traits get read as effects (for example TEACHER boosting development) before the development plan's caps exist. | The development plan's off/zero defaults. | Profiles change voice and context only until each owning plan activates effects. |

**Spike first:** before wave 2, the orchestrator writes one profile by hand (Dave Canales, `CAR_HC.json`, reusing the Panthers sample's sources) and runs `validate` and `report` on it. JR looks at that one page and confirms the format before the 95 others are researched.

## 4. Tests (synthetic, no live DB)

- `tests/test_coach_profiles.py`:
  - a valid fixture passes;
  - each rule fails with its message: file name and key mismatch; unknown `seed_ref`; slider total outside 24–36; a moved slider without evidence; an ineligible or conflicting trait; an unknown view tag; a source after the cutoff; a low-confidence source behind a trait; a 16-word copied quote; a reporter tie; bad review status;
  - `core_initial_values` produces a profile `core.initialize(..., 'real', ...)` accepts;
  - `lifecycle_entries` skips drafts and rejected files and fills birth keys and aliases;
  - `persona_for` renders through `newsroom.personas.render_persona` and takes a view's sliders and traits over the file's;
  - `tie_events` builds valid envelopes and reports unresolved targets.
- `tests/test_coach_profile_review.py`:
  - `validate` exit codes;
  - `report` output has every claim, source link and decision line, and is deterministic;
  - `apply-review` approves, edits (re-validated), refuses an invalid edit and rejects;
  - no database or network imports.

Run with `pytest -m "not integration" tests/test_coach_profiles.py tests/test_coach_profile_review.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`. The spike's `CAR_HC.json` is written by the orchestrator and then belongs to U5.

### U1: Schema, module and review tool

- **Owns:** `personality/coach_profiles.py` (new), `tools/coach_profile_review.py` (new), `newsroom/config/coach_personas/_TEMPLATE.json` (new), `tests/test_coach_profiles.py` (new), `tests/test_coach_profile_review.py` (new)
- **Reads:** this plan §2; `db/seed_clean_db.py:60–140`; `newsroom/personas.py:20–160`, `:746–800`; `personality/core.py`; `PERSONALITY_RELATIONSHIPS_PLAN.md` §2.2
- **Depends on:** none. It is written against the core extensions plan's D1 `feature_view`, and uses today's `core.validate_profile` for trait rules.
- **Done when:** the §2.2 and §2.3 interfaces exist exactly, every §4 case passes, and `validate` and `report` work on the template.
- **Verify:** `pytest -m "not integration" tests/test_coach_profiles.py tests/test_coach_profile_review.py -q` passes; `python -m tools.coach_profile_review --help` exits 0.

### U2: Research batch AFC East + North

- **Owns:** `newsroom/config/coach_personas/{BUF,MIA,NE,NYJ,BAL,CIN,CLE,PIT}_{HC,OC,DC}.json` (24 new files)
- **Reads:** this plan §2.1 and §2.4; `docs/in_progress/PERSONALITY_PANTHERS_SAMPLE.md`; the template; `newsroom/config/persona_traits.json`
- **Depends on:** U1
- **Done when:** 24 draft files exist, each with cited evidence, and pass `validate`.
- **Verify:** `python -m tools.coach_profile_review validate --teams BUF,MIA,NE,NYJ,BAL,CIN,CLE,PIT` exits 0.

### U3: Research batch AFC South + West

- **Owns:** `newsroom/config/coach_personas/{HOU,IND,JAX,TEN,DEN,KC,LV,LAC}_{HC,OC,DC}.json` (24 new files)
- **Reads, Depends on, Done when:** as U2.
- **Verify:** `python -m tools.coach_profile_review validate --teams HOU,IND,JAX,TEN,DEN,KC,LV,LAC` exits 0.

### U4: Research batch NFC East + North

- **Owns:** `newsroom/config/coach_personas/{DAL,NYG,PHI,WAS,CHI,DET,GB,MIN}_{HC,OC,DC}.json` (24 new files)
- **Reads, Depends on, Done when:** as U2.
- **Verify:** `python -m tools.coach_profile_review validate --teams DAL,NYG,PHI,WAS,CHI,DET,GB,MIN` exits 0.

### U5: Research batch NFC South + West

- **Owns:** `newsroom/config/coach_personas/{ATL,CAR,NO,TB,AZ,LAR,SF,SEA}_{HC,OC,DC}.json` (24 files; `CAR_HC.json` starts from the spike)
- **Reads, Depends on, Done when:** as U2.
- **Verify:** `python -m tools.coach_profile_review validate --teams ATL,CAR,NO,TB,AZ,LAR,SF,SEA` exits 0.

Team codes follow `COACHES_DATA` (checked 2026-11-03: 32 codes, Arizona is `AZ`, and every team has exactly one OC and one DC in `COORDINATORS_DATA`).

## 6. Waves

| Wave | Units | Needs merged first | Why |
|---|---|---|---|
| 1 | U1 | — | Research needs the validator and template. |
| — | Spike: `CAR_HC.json` and JR's look at one review page | U1 | Confirms the format before 95 more profiles. |
| 2 | U2, U3, U4 | U1, spike | Disjoint files; each runs `validate` on its batch. |
| 3 | U5 | U1, spike | The fourth batch (three per wave). |

After the build, a gate: the orchestrator runs `report` per batch; JR reviews and approves, edits or rejects; `apply-review` records it. Only reviewed files reach the lifecycle manifest, and only after the lifecycle plan is built. Building waits until every personality sub-plan is approved (JR, 2026-11-03).

**File ownership check:**

| File | Unit |
|---|---|
| `newsroom/config/coach_personas/_TEMPLATE.json` | U1 |
| `newsroom/config/coach_personas/{BAL,BUF,CIN,CLE,MIA,NE,NYJ,PIT}_{HC,OC,DC}.json` | U2 |
| `newsroom/config/coach_personas/{DEN,HOU,IND,JAX,KC,LAC,LV,TEN}_{HC,OC,DC}.json` | U3 |
| `newsroom/config/coach_personas/{CHI,DAL,DET,GB,MIN,NYG,PHI,WAS}_{HC,OC,DC}.json` | U4 |
| `newsroom/config/coach_personas/{ATL,AZ,CAR,LAR,NO,SEA,SF,TB}_{HC,OC,DC}.json` | U5 |
| `personality/coach_profiles.py` | U1 |
| `tests/test_coach_profile_review.py` | U1 |
| `tests/test_coach_profiles.py` | U1 |
| `tools/coach_profile_review.py` | U1 |

The four team lists don't overlap, and together they cover all 32 teams once.

## 7. Spec changes when built

- §1.1 Repository Layout: `newsroom/config/coach_personas/` (96 files and the template), `personality/coach_profiles.py`, `tools/coach_profile_review.py`, the two tests and this plan's entry.
- §1.2: the three review commands.
- §3.3a: coaches and coordinators now have reviewed profile files keyed by 2026 team and role, mapped by `seed_ref`.
- §7.11: the coach-profile schema, the evidence rules (cutoff, confidence, quote check), review statuses, `persona_for` for prompts, starting ties as `reviewed_evidence` events, and that profiles reach the core only through the lifecycle plan's reviewed apply.
- Other plans, at approval:
  - `PERSONALITY_PLAN.md` §5.3, "Real-coach profiles and coach views": point at this plan.
  - The press conference plan's R9 and D5, and the inbox's coach voice: name `persona_for` as their source.
  - The lifecycle plan §2.5: name `lifecycle_entries` as the reviewed coach manifest.

## 8. Decisions for JR

JR approved all three recommendations on 2026-11-03.

| # | Question | Decision (JR, 2026-11-03) |
|---|---|---|
| Q1 | How recent can sources be? | Through the end of 2025 (`SOURCE_CUTOFF`), matching the clean database's history and the Panthers sample. From 2026 the simulation is the history. |
| Q2 | How do you review? | One page per batch of 24 coaches, with every claim and its linked source. You reply approve, edit or reject per coach, and I record it with `apply-review`. Start with the single spike page to settle the format. |
| Q3 | What happens to a tie whose other person isn't in the 2026 league as the same person (for example a GM persona with a different name)? | Drop it and list it in the report. A tie only counts between people who are both actually in the league. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
