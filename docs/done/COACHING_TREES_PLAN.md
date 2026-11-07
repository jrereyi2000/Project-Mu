# Project Mu: Coaching Trees Plan

**Status:** approved by JR (2026-11-07, his own message: "Approve as written", Q1–Q9 as recommended); built and on main (2026-11-07, waves 1–3). JR approved all researched drafts on 2026-11-07 (his message: "The coaching trees are approved"): the registry's 19 trees, the proposed `reich` and `arians` trees, the shanahan → walsh parent link, batches A–C's 139 edges and 67 memberships, recorded by `apply-review` (reviewer JR). Moved to `docs/done/`. `coaching_tree_hiring` is off.
- As built (U1): the projection tests live in `tests/test_personality_relationships.py` (the file that tests `personality/relationships.py`); `tests/test_relationships.py` tests `newsroom/relationships.py` and was left untouched. U1 owned the former instead.
- As built (U2): `lineage` also returns `root_of` (trees the person roots: a registry root or a computed `<Surname> tree`), and `lineage_line` says "root of the ..." and "mentor to ..."; `coaching_tree_facts` adds `graph_from` / `context_from` (the same reads from rows already in hand).
- As built (U3–U6): research by four agents on 2026-11-07. Registry: 19 trees (the 13 plus kubiak, lafleur, holmgren, coughlin, gruden, ryan; spagnuolo dropped, no source), 39 sources. Batches: A 51 edges / 19 memberships, B 37 / 26 (+ proposed `reich`), C 51 / 22 (+ proposed `arians`); every entry a draft. Review pages: `verify_output/coaching_tree_review/{registry,batch_A,batch_B,batch_C}.md`.
- As built (U7): each simulated edge is produced as of its crossing season (`sim_edges(..., through=crossed_season)`), so `seasons` and `outcome` are frozen there and later ticks rebuild byte-identical facets (otherwise the store would see a facet conflict as the count grew). The facets' `event_id` follows the producers' `<source_event_id>|<facet_key>` rule (not `|mentor`/`|mentee`), and `facet_key` now uses `target_alias` when present. R3 did not bite: producer appends don't check the calendar.
- As built (U8): `engines/coach_profile.coaching_tree_block(cur, name, season, season_over)` builds the block (`season_over` is the existing `awards_visible`). `mu_coaching_packet` runs on a dict_row cursor, so the tree reads use a tuple cursor on the same connection (`_tuple_cursor`); a hire with nothing to say carries `coaching_tree: null`. Checked once on a throwaway `mu_scratch_test` database (init_db.sql, a few tenures): `staff_seasons`, `tree_context`, `tree_mode`, `mentor_records` and the tuple-cursor path all ran; the database was dropped.
- As built (U9): the follow pick is its own seeded draw (`<season>:<TEAM>:<ROLE>:follow`) between the stand-in (0) and the followers (0.30); a coordinator opening whose next-season head coach is unknown gets no reunion term. `tests/test_coaching_carousel_db.py` is an integration test against the live database, so it was not run (the offline run skips it).
**Builds on:** PROJECT_SPEC §2.19 (coaching carousel), §3.3a (coaches and staff), §3.3r (relationship event producers), §3.3t (relationship ledger), §7.11 (real-coach profiles, switchboard); [`REAL_COACH_PROFILES_PLAN.md`](../done/REAL_COACH_PROFILES_PLAN.md) (reviewed-evidence method, Q3); [`PERSONALITY_RELATIONSHIPS_PLAN.md`](../done/PERSONALITY_RELATIONSHIPS_PLAN.md) §2.2–§2.3; [`RELATIONSHIP_EVENT_PRODUCERS_PLAN.md`](../done/RELATIONSHIP_EVENT_PRODUCERS_PLAN.md); [`PERSONALITY_DISPLAY_PLAN.md`](../done/PERSONALITY_DISPLAY_PLAN.md) (mentorship labels need a `direction`); [`PERSONALITY_ACTIVATION_PLAN.md`](../done/PERSONALITY_ACTIVATION_PLAN.md) (switchboard).

**Goal:** JR asked (2026-11-07) whether coaching trees, like the Shanahan tree, should be recorded as relationships or are already in coach tenures. They are not in tenures: `coach_tenures` and `coordinator_tenures` say who held which seat on which team each season, from the 2026 start, but not who learned under whom, and nothing before 2026. This plan records real coaching trees for the 2026 staffs as reviewed, cited, directed mentor → protégé ties, gives each coach a named-tree label for writing ("a Shanahan-tree play caller"), and grows the trees inside the simulation: a coordinator who serves two seasons under a head coach becomes his protégé. Writers and coach voices get the lineage as plain facts. A separate, off-by-default switch lets the carousel prefer tree members and lets coordinators follow their mentors.

**Delivers:**
- An optional `direction` (`mentor` | `mentee`) on `relationships-v1` `meaningful_interaction` events with `relationship_kind 'mentorship'`, carried through the relationships projection to `tie.direction` (the display already turns it into "mentor to" / "mentored by").
- `personality/coaching_trees.py`: the `coaching-trees-v1` files, their validator, reviewed edges as directed tie events, the in-sim protégé rule (`sim_edges`) and the lineage / named-tree label (`lineage`, `lineage_line`).
- `engines/coaching_tree_facts.py`: `staff_seasons` and `tree_context` (database reads for writers, producers and the carousel).
- `tools/coaching_tree_review.py`: `validate`, `report`, `apply-review`, `events` (offline), like `tools/coach_profile_review.py`.
- `newsroom/config/coaching_trees/`: `trees.json` (the named-tree registry), `batch_A.json`, `batch_B.json`, `batch_C.json` (researched edges and tree memberships, drafts for JR's review), `_TEMPLATE.json`.
- Producer rule `coaching_tree` (`engines/relationship_producers`, `PRODUCER_RULES_VERSION` → `producers-v2`).
- Writer context: `coaching_tree` in `engines/coach_profile.coach_profile` (the `get_coach_profile` tool) and on each hire in `newsroom/offseason_service.mu_coaching_packet`; `lineage` in `coach_profiles.persona_for` and one line in `newsroom.personas.render_persona`.
- Switchboard feature `coaching_tree_hiring` (off by default) and `engines/coaching_tree_hiring.py`, wired into `engines/coaching_carousel.hiring_cycle`.
- No migration.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `db/init_db.sql:651` `coach_tenures (coach_id, team_id, start_season, end_season)` and `:675` `coordinator_tenures (coordinator_id, team_id, role, start_season, end_season)`. No column links a coordinator to a head coach. | "Who served under whom" in the simulation is derivable: same team, same season, HC row plus OC/DC row. Nothing earlier than the seed exists. |
| S2 | `db/seed_clean_db.py:69` `COACHES_DATA` and `:107` `COORDINATORS_DATA` are the staffs on the eve of the 2026 season, with real `start_season` values (Andy Reid KC from 2013, `:73`). | The seed's start seasons say how long a current pair has overlapped, but not before that seat, and not on other staffs. Real trees need research. The in-sim rule counts simulated seasons only (2026 on), so seeded overlap isn't double counted. |
| S3 | `personality/relationship_events.py:42` `KINDS`, `:54` `RELATIONSHIP_KINDS = ('mentorship', 'support', 'conflict', 'collaboration')`, `:328` the `meaningful_interaction` payload checks `relationship_kind`, `outcome`, `significance`, `opportunity_ref` and admits extra keys (the profiles' `prehistory: true`, `personality/coach_profiles.py:467`). There is no direction field. | A mentorship tie says nothing about who taught whom. Add an optional, validated `direction`. Additive, so existing envelopes and hashes don't change. |
| S4 | `server/personality_api.py:63` `MENTOR_LABELS = {"mentor": "mentor to", "mentee": "mentored by"}`; `:133` `tie_label` reads `tie["direction"]`. `personality/relationships.py:445` `relationship_context` passes `meaningful_ties` through as `ties`, but `:249` `_ties` and `:260` `_tie_view` drop everything except `person`, `kind`, `source_ids`, `relevant_to_team`, and the first event's kind wins when one person has several ties. | The display needs no change; the projection must carry `direction` and prefer a directed mentorship over an undirected tie to the same person. |
| S5 | `personality/coach_profiles.py:436` `tie_events` builds one `meaningful_interaction` per profile tie (`basis 'reviewed_evidence'`, `occurred_at {2026, 1}`, private, `source_event_id mu:coach-tie:<key>:<n>`). Nothing calls it outside tests; no code appends starting ties to the ledger. | Tree ties follow the same envelope recipe. Appending reviewed starting ties (profile ties and tree edges alike) stays with whatever applies the lifecycle manifest (Q3). |
| S6 | The 96 approved profiles carry 305 ties; 203 point at other coaches, 57 of them `mentorship` with no direction (e.g. `KC_OC` → `KC_HC`, `DET_DC` → `DET_HC`). | Tree research checks every existing coach mentorship tie on its teams and either confirms it as a directed edge or leaves it alone. Profile files stay untouched (they're approved, and the inferred-sliders plan owns them now). |
| S7 | `engines/relationship_producers/rules.py:651` `staff_move_events` turns `staff_moves` into treatment/situation events toward teams; `:197` `envelope` supports a person target through `target_alias` (`store.py:98`, `:211` resolves it). `run.py:198` `_coach_events` and `sources.py:445` feed `staff_moves` and hot-seat rows per season. Rules are versioned (`__init__.py:16` `producers-v1`). | A tree rule fits the producers: person → person mentorship events from tenures, held until both identities exist. No producer run has touched a franchise (the `2027_01_28` migration isn't applied), so bumping the version is free. |
| S8 | `engines/relationship_producers/store.py` (via `personality/relationship_store.py:160`) dedupes by `(source_event_id, facet)`, and an external source ref maps to one `source_event_id`. | One edge = one occurrence with two facets (mentor's view, protégé's view) sharing a `source_event_id`; `event_id` gets a `|mentor` / `|mentee` suffix. |
| S9 | `engines/coaching_carousel.py:500` a poached coordinator becomes a head coach by name (`lower(coach_name)` match or a new `coaches` row) and the lifecycle event `promoted` adds the `db-coach` alias to his identity (`:503`). League names are unique (`taken_names`). | One person can have a `coordinators` row and a `coaches` row. Tree code keys people by normalized name (`person_key`), the carousel's own rule; ledger events use the alias of the seat held. |
| S10 | `engines/coaching_carousel.py:427` HC candidates: POACHED coordinators, RETREAD former HCs, one OUTSIDE; `:265` `pick` adds seeded Gumbel noise to `score`. `:541` a new HC's own coordinator is always invented (`HC_STAFF`). `:598` coordinator openings: PROMOTED, OUTSIDE, REHIRED (out-of-work coordinators). `staff_moves.source` is checked against six values (`db/migrations/2026_12_23_coaching_carousel.sql:44`). | Tree hiring is a score term on these pools plus one new path: the new HC brings an out-of-work protégé instead of an invented one, recorded as source `REHIRED` (no schema change). |
| S11 | `personality/features.py:51` `FEATURES` (go-live order), `:67` `REQUIREMENTS` (module, migration or None). Every feature is off unless JR sets it. `engines/owner_policy.py:87` `hiring_key` is the owner's ±0.15 hiring term. | `coaching_tree_hiring` is one more feature with migration `None`. It adds its own term, separate from the owner's. |
| S12 | `engines/coach_profile.py:370` `coach_profile(cur, team_id, season, visible, awards_visible)` feeds `newsroom/db_tools.py:847` `get_coach_profile`; `newsroom/offseason_service.py:304` `mu_coaching_packet` lists each hire. `personality/coach_profiles.py:419` `persona_for` returns the `render_persona` shape (`newsroom/personas.py:~820`). | These are the writer and voice surfaces. |
| S13 | Concurrent work: [`INFERRED_SLIDERS_AND_FOCUS_PLAN.md`](../done/INFERRED_SLIDERS_AND_FOCUS_PLAN.md) (built 2026-11-07, now in `docs/done/`) owned `personality/coach_profiles.py`, `newsroom/personas.py`, `personality/features.py`, `server/personality_api.py` and their tests; `OWNERS_SCOUTS_VOTERS_PLAN.md` U5/U6 (held) own `engines/coaching_carousel.py` and `personality/features.py`. | Units U8 and U9 start only after the inferred-sliders plan is in `docs/done/`. A later owners U5/U6 build merges against U9's carousel changes. |
| S14 | Migrations on disk end at `2027_02_03_persona_focus.sql`; `2027_02_02` is reserved (`PERSONALITY_PLAN.md:190`). | This plan needs no migration: reviewed data lives in files, in-sim edges are computed from tenures, events use the existing ledger, the feature mode uses the switchboard tables. |
| S15 | Seasons 2026–2034 are reconstructed league history with tenures (`newsroom/offseason_service.py:301` `HISTORY_CAPTURED_FROM = 2035`). | The in-sim rule already has nine seasons of trees to find; writers see them as soon as U8 lands. |

## 1. Scope

**In:**
- The direction contract (relationships-v1, additive) and the projection change.
- The `coaching-trees-v1` file format, validator, review tool and the named-tree registry.
- Researched, cited, directed mentor → protégé edges among the 96 people in the 2026 seed (HCs and coordinators, NFL and college staffs), plus each person's named-tree memberships, as drafts for JR's review in three batches.
- The in-sim rule: a coordinator with `TREE_MIN_SEASONS` (2) simulated seasons under a head coach becomes his protégé; produced as relationship events, and computed directly for writers.
- Named-tree labels, including trees that form in the simulation (a coach with 2 protégés who became head coaches gets his own tree).
- Writer context, the coach voice line, and the display label via `direction`.
- `coaching_tree_hiring`, off by default.

**Out (non-goals):**
- Appending reviewed starting ties (profile ties and tree edges) to a franchise's ledger: the lifecycle apply does that for both, later (Q3). This plan builds and previews the events.
- Ties to people not in the 2026 seed (Mike Shanahan, Bill Belichick, retired coaches): dropped as edges, per the profile plan's Q3. They can still be a named tree's root.
- Position coaches and other assistants: the league tracks only HC, OC and DC.
- Editing the 96 approved profile files.
- A tree page on the site; the existing personality panel shows the mentorship labels once the relationships feature is on.
- Turning anything on for a franchise.

## 2. Design

### 2.1 Direction on mentorship ties (`personality/relationship_events.py`, `personality/relationships.py`)

- `DIRECTIONS = ('mentor', 'mentee')`. In `_payload` for `meaningful_interaction`: if `direction` is present it must be one of `DIRECTIONS`, and only when `relationship_kind == 'mentorship'` (else `invalid_field`). Absent is valid. `direction` is the subject's role toward the target: `mentor` = the subject taught the target.
- No schema version bump: an envelope without `direction` hashes as before.
- `relationships._ties`: each tie carries `direction` (`None` when absent). When one person has several qualifying events, the tie's `kind` and `direction` come from the first directed mentorship event in replay order if any, otherwise from the first event (today's rule). `_tie_view` adds `direction`. `relationship_context` passes it through unchanged (it copies the tie dict).

### 2.2 Files (`newsroom/config/coaching_trees/`)

`trees.json`, the named-tree registry:

```json
{"schema_version": "coaching-trees-v1", "kind": "registry",
 "trees": [{"key": "shanahan", "label": "Shanahan tree", "root": "Mike Shanahan",
            "parent": null, "summary": "...", "evidence": ["T1", "T2"],
            "review": {"status": "draft", "reviewer": null, "reviewed_at": null, "notes": ""}}],
 "evidence": [{"id": "T1", "url": "https://...", "publisher": "...", "published": "YYYY-MM-DD",
               "accessed": "YYYY-MM-DD", "summary": "...", "confidence": "high"}]}
```

`key` matches `^[a-z][a-z0-9_]*$`; `parent` is another tree's key or null (McVay → `shanahan`); no cycles. The starting set to research: `walsh`, `parcells`, `belichick`, `shanahan`, `mcvay`, `reid`, `harbaugh` (John), `dungy`, `tomlin`, `carroll`, `payton`, `saban`, `fangio`; U3 may add others with evidence and drop any it can't source.

`batch_<A|B|C>.json`, one per research batch (protégé's 2026 team decides the batch):

```json
{"schema_version": "coaching-trees-v1", "kind": "batch", "batch": "A",
 "members": [{"person": {"table": "COACHES_DATA", "team": "SF", "role": "HC"}, "trees": ["shanahan"],
              "evidence": ["E1"], "review": {...}}],
 "edges": [{"key": "coach:SF:HC>coach:SEA:OC",
            "mentor": {"table": "COACHES_DATA", "team": "SF", "role": "HC"},
            "protege": {"table": "COORDINATORS_DATA", "team": "SEA", "role": "OC"},
            "stints": [{"team": "SF", "first_season": 2021, "last_season": 2023,
                        "mentor_role": "HC", "protege_role": "pass game coordinator"}],
            "outcome": "positive", "evidence": ["E2", "E3"], "notes": "", "review": {...}}],
 "proposed_trees": [],
 "evidence": [...]}
```

- Batches: A = BUF MIA NE NYJ BAL CIN CLE PIT HOU IND JAX; B = TEN DEN KC LV LAC DAL NYG PHI WAS CHI DET; C = GB MIN ATL CAR NO TB AZ LAR SF SEA (`BATCHES` in the module).
- An edge's `key` is `<mentor profile key>><protégé profile key>` (profile keys `coach:<TEAM>:<ROLE>`). Persons are `seed_ref`s without names; the name comes from `coach_profiles.seed_index`.
- **Edge rule (research):** the mentor was the protégé's boss on one staff (head coach over any assistant, or coordinator over a position coach on his side of the ball), NFL or college, for at least 2 seasons summed over stints; or 1 season plus a source that calls it a mentorship. Playing careers don't count. Peers on one staff are not a tree. At least 2 sources per edge, at least one `medium` or `high`; sources dated on or before `SOURCE_CUTOFF` (2026-09-08, the profiles' cutoff).
- **Membership rule:** a person is in a named tree if a source places him there, or if he has an edge (any staff, any era, including people outside the seed) to the tree's root or a member, cited. `proposed_trees` holds trees a batch wants added to the registry (same shape as registry trees); the orchestrator moves accepted ones into `trees.json` after JR's review.
- `outcome` is `positive` or `mixed` (a documented falling-out). Review statuses are the profiles' (`draft`, `approved`, `edited`, `rejected`); only `approved` and `edited` leave the module.

### 2.3 `personality/coaching_trees.py` (pure apart from reading files)

```python
SCHEMA_VERSION = 'coaching-trees-v1'
TREE_DIR = 'newsroom/config/coaching_trees'
SOURCE_CUTOFF = coach_profiles.SOURCE_CUTOFF        # '2026-09-08'
BATCHES = {'A': (...), 'B': (...), 'C': (...)}
DIRECTIONS = relationship_events.DIRECTIONS
TREE_MIN_SEASONS = 2          # simulated seasons under one head coach (estimate; Q2)
FIRST_SEASON = 2026
NAMED_TREE_MIN_HCS = 2        # protégés who became head coaches before a coach gets his own tree (Q7)
LINEAGE_DEPTH = 3             # mentor hops walked for a label
TIE_TIME = {'season': 2026, 'tick': 1}

person_key(name) -> str                    # 'p:' + lowercase, punctuation dropped, single spaces
load_registry(directory=TREE_DIR) -> dict
load_batches(directory=TREE_DIR, *, batches=None) -> list[dict]
validate_registry(doc) -> list[str]
validate_batch(doc, *, registry, index=None) -> list[str]
reviewed_edges(batches, *, index=None) -> list[dict]     # approved/edited only, as Edge dicts
reviewed_members(batches, *, index=None) -> dict        # {person_key: [tree keys]}, approved/edited only
reviewed_pairs(edges) -> frozenset                      # {(mentor_pk, protege_pk)}
edge_events(edge, resolve) -> tuple[list[dict], list[dict]]   # (envelopes, unresolved)
sim_edges(staff_seasons, *, through, reviewed_pairs=frozenset(), min_seasons=TREE_MIN_SEASONS,
          first_season=FIRST_SEASON) -> list[dict]
lineage(pk, *, edges, registry, members, head_coach_pks=frozenset()) -> dict
lineage_line(lin) -> str | None
```

- **Edge dict** (reviewed and simulated alike): `{key, mentor_pk, mentor_name, protege_pk, protege_name, seasons, span: [first, last], teams: [...], outcome, basis: 'reviewed'|'simulated'}`; reviewed adds `evidence_urls`, `confidence` (lowest cited), `reviewer`; simulated adds `mentor_ref {namespace 'db-coach', external_id}`, `protege_ref {namespace 'db-coordinator', external_id}`, `crossed_season` (the season his count reached `min_seasons`).
- **Validation** reuses the profile rules: the batch's persons match a seed row (`seed_index`), mentor ≠ protégé, the protégé's team is in the batch, each edge's stints sum to ≥ 2 seasons or one cited source is marked `"mentorship": true`, ≥ 2 cited sources with one medium/high, `published` ≤ cutoff with an http(s) URL, no free text sharing more than `coach_profiles.MAX_QUOTE_WORDS` words with a source summary, tree keys exist in the registry or the batch's `proposed_trees`, duplicate edge keys across batches fail, a reviewed entry names reviewer and date.
- **`edge_events`:** for a reviewed edge, two validated `meaningful_interaction` envelopes: subject protégé → target mentor with `direction 'mentee'`, subject mentor → target protégé with `direction 'mentor'`. Shared: `source_event_id` `mu:coach-tree:<edge key>`, `event_id` `<source_event_id>|<direction>`, `source_kind 'coaching_tree'`, `source_refs [{namespace 'coaching-tree', key <edge key>}]`, `occurred_at TIE_TIME`, `basis 'reviewed_evidence'`, `confidence` lowest cited, `reviewer`, `evidence_scope ['coaching_tree']`, `visibility 'public'` (documented history), payload `{relationship_kind 'mentorship', outcome, significance 'meaningful', opportunity_ref 'coaching-tree:<key>', prehistory: true, direction, seasons}`. `resolve(seed_ref) -> PersonRef | None`, as in `coach_profiles.tie_events`; an unresolved person returns the edge in `unresolved` with a reason.
- **`sim_edges`:** `staff_seasons` rows `{season, team_id, role ('HC'|'OC'|'DC'), namespace, external_id, name, fired}`. For each season `first_season ≤ s ≤ through`, pair the team's HC with its OC and DC. Count seasons per `(mentor_pk, protege_pk)` across teams (a coordinator who follows his head coach keeps counting). A pair becomes an edge in the season its count reaches `min_seasons`. Skip pairs in `reviewed_pairs` and same-person pairs. `outcome` is `mixed` when the protégé's last stint under the mentor ended with `fired` true while the mentor stayed, else `positive`. Deterministic order: `(crossed_season, mentor_pk, protege_pk)`.
- **`lineage`:** the person's own reviewed memberships first; then walk mentors (reviewed and simulated edges, most seasons first) up to `LINEAGE_DEPTH` hops, collecting the nearest named trees: a mentor who is a registry root gives that tree, a mentor with memberships gives those, and any coach with ≥ `NAMED_TREE_MIN_HCS` protégés in `head_coach_pks` gives a computed tree `{key: 'sim:<pk>', label: '<Surname> tree', root: <name>}`. Registry parents extend the chain. Returns `{trees: [{key, label, root}], label, mentors: [{name, seasons, span, basis}], proteges: [{name, seasons, span, basis}]}`; `label` is e.g. `"McVay tree (Shanahan lineage)"` or `None`.
- **`lineage_line`:** one sentence for prompts, e.g. `"Coaching tree: McVay tree (Shanahan lineage); served 5 seasons under Sean McVay."`; `None` when there is nothing to say.

### 2.4 `engines/coaching_tree_facts.py` (database reads)

```python
staff_seasons(cur, *, through, first_season=FIRST_SEASON) -> list[dict]
tree_context(cur, name, *, through, directory=TREE_DIR) -> dict | None
tree_graph(cur, *, through, directory=TREE_DIR) -> dict   # {edges, registry, members, head_coach_pks}
```

- `staff_seasons` expands `coach_tenures` and `coordinator_tenures` (inclusive `end_season`, open = `through`) joined to `coaches.coach_name` / `coordinators.coordinator_name`, clipped to `[first_season, through]`; `fired` from `coordinator_hot_seat.fired` in the stint's last season.
- `tree_graph` = reviewed edges and members from the files, `sim_edges` from `staff_seasons`, `head_coach_pks` from everyone with a `coach_tenures` row through `through`.
- `tree_context` = `lineage` + `lineage_line` for one name, or `None`. Reads run in a savepoint; a missing table returns `None` without aborting the caller.
- These reads are factual (documented history and the league's own tenures), so they don't wait for the personality switchboard (Q8).

### 2.5 Review tool (`tools/coaching_tree_review.py`, offline)

```
python3 -m tools.coaching_tree_review validate [--dir DIR] [--batch A|B|C|registry]   exit 0 all pass, 1 otherwise
python3 -m tools.coaching_tree_review report --batch A --out verify_output/coaching_tree_review/batch_A.md [--dir DIR]
python3 -m tools.coaching_tree_review apply-review --decisions PATH [--dir DIR] [--reviewer JR] [--today YYYY-MM-DD]
python3 -m tools.coaching_tree_review events [--dir DIR] --out PATH      reviewed edges as envelopes, preview only
```

`report` writes one page per batch: each edge as "mentor → protégé, N seasons (teams, years)", outcome, sources by ID with link and confidence, and a decision line; then memberships; then a Sources table (each summary once). `apply-review` takes `{"<edge key>" | "member:<profile key>" | "tree:<key>": {"status", "notes", "edits"}}`, writes nothing unless every decision validates. `events` resolves seed refs to placeholder PersonRefs (`universe_id 'preview'`, `persona_id` = profile key) so JR can see what would be appended.

### 2.6 Producer rule (`engines/relationship_producers`)

- `PRODUCER_RULES_VERSION = "producers-v2"` (Q5).
- `rules.py`: `TREE_TICK = FIRST_OFFSEASON_TICK` (24); `tree_source_id(coach_id, coordinator_id) -> "mu:tree:<coach_id>:<coordinator_id>"`; `coaching_tree_events(edge, *, source="SAVE") -> [envelope, envelope]` for one simulated edge: kind `meaningful_interaction`, `occurred_at (crossed_season, TREE_TICK)`, `subject_alias` the protégé's `db-coordinator` alias with `target_alias` the mentor's `db-coach` alias and `direction 'mentee'`, and the mirror facet with `direction 'mentor'`; payload `{relationship_kind 'mentorship', outcome, significance 'meaningful', opportunity_ref 'coach_tenures:<coach_id>|coordinator_tenures:<coordinator_id>', direction, seasons, rule 'coaching_tree@producers-v2'}`; public; `basis verified_simulation`; `evidence_scope ['coach_tenures', 'coordinator_tenures']`.
- `sources.read_tick` adds `tree_staff` (`coaching_tree_facts.staff_seasons(cur, through=season)`) and `tree_reviewed_pairs` (from the files).
- `run._coach_events`: when `tick >= TREE_TICK`, every `sim_edges(..., through=season)` edge with `crossed_season <= season` yields its events; the store's dedupe makes repeats free, and a later first run emits past crossings with their historical `occurred_at`. Held until both aliases resolve (store `target_alias` path).

### 2.7 Writer and voice context

- `engines/coach_profile.coach_profile` adds `"coaching_tree": {"label", "line", "mentors", "proteges"}` (omitted when `tree_context` is `None`), with `through = season - 1` in season and `season` once the season is over (the existing `visible` rule decides).
- `newsroom/offseason_service.mu_coaching_packet`: each hire gains `coaching_tree` (`label`, `line`) for the hired coach.
- `personality/coach_profiles.persona_for(doc, view=None, change_notes=None, lineage=None)`: a non-empty `lineage` string is passed through as `out['lineage']`; `newsroom/personas.render_persona` adds it as one line after the voice. The caller (coach voice users, when they exist) passes `lineage_line(...)`.

### 2.8 Tree hiring (`coaching_tree_hiring`, off by default)

- `personality/features.py`: `FEATURES` gains `'coaching_tree_hiring'` right after `'owner_hiring'`; `REQUIREMENTS['coaching_tree_hiring'] = ('engines.coaching_tree_hiring', None)`.
- `engines/coaching_tree_hiring.py` (pure apart from `tree_mode`):

```python
TREE_PEDIGREE = 0.10   # HC candidate whose mentor has a winning career record or a Super Bowl win (estimate)
TREE_REUNION = 0.20    # coordinator opening: an out-of-work protégé of the hiring team's head coach (estimate)
FOLLOW_SCORE = 0.30    # a new head coach's out-of-work protégé, against the invented HC_STAFF stand-in at 0 (estimate)
TREE_CAP = 0.20
tree_mode(cur) -> 'off' | 'shadow' | 'active'      # features.mode; missing tables -> 'off'
candidate_term(candidate, *, graph, hiring_hc_pk=None, mentor_records) -> float   # |x| <= TREE_CAP
follow_candidates(hc_pk, out_of_work, *, graph, role) -> list[dict]   # carousel pool entries, kind 'FOLLOWED'
```

- `hiring_cycle(cur, season, log=print, personality=None)` reads `tree_mode` once. `active`: adds `candidate_term` to HC and coordinator candidates' `score` before `pick`, and when a new HC brings his own coordinator, the pool is the invented stand-in (score 0) plus `follow_candidates`; a picked protégé is hired with `staff_moves.source 'REHIRED'`, `from_team_id`/`from_role` set and notes "follows his mentor <name>". `shadow`: picks are made as today, and `report['tree_shadow']` lists each opening where the tree terms would have picked someone else. `off`: today's behavior, byte for byte.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Research yield and accuracy: staff histories are spread over team sites, Wikipedia and press; titles like "pass game coordinator" are fuzzy. | Each batch's `validate` plus JR's review page. Estimate: 60–120 edges among 96 people. | JR rejects or edits per edge; thin edges stay drafts and never load. |
| R2 | Projection change alters the `kind` shown for a person who has both an undirected profile tie and a tree edge. | U1 tests both orders. | Only the label changes ("mentored by" instead of a trust word), which is the point. |
| R3 | Producer events with historical `occurred_at` (2026–2034) need the relationships calendar to cover those seasons (`CalendarGapError`). | U7 test with a calendar covering only recent seasons. | Emit at the first covered season instead, keeping `crossed_season` in the payload; recorded in the spec. |
| R4 | Name keying breaks if two different people share a normalized name. | `taken_names` keeps league names unique; `sim_edges` test with a near-duplicate. | Fall back to `(namespace, external_id)` for the protégé side. |
| R5 | U8/U9 touch files the inferred-sliders plan is editing; carousel also belongs to owners U5/U6 (held). | Check `docs/done/` before wave 3. | Wait, or merge against the newer file; conflicts go to JR per CLAUDE.md. |
| R6 | Tree hiring changes carousel outcomes for JR's league. | Off by default; `shadow` reports differences for a season first. | Leave it off. |

**Spike first:** none blocking. U3's registry work shows whether roots and parents can be sourced; if JR wants a feel for edge quality before all three batches, run U4 alone first (Q9).

## 4. Tests (synthetic, no live DB)

- `tests/test_relationship_events.py` → `direction` accepted on mentorship, rejected on other kinds and bad values; envelopes without it hash as before.
- `tests/test_relationships.py` → ties carry `direction`; a directed mentorship beats an undirected tie to the same person in either replay order; `relationship_context` + `server.personality_api.tie_label` give "mentored by" / "mentor to".
- `tests/test_coaching_trees.py` → registry and batch validation (each rule fails with its message), `reviewed_edges` drops drafts, `edge_events` builds two valid facets with shared source id, `sim_edges` (2 seasons → edge, 1 → none, follows across teams, reviewed pairs skipped, fired → mixed, deterministic order), `lineage` (own membership, mentor walk, registry parent, computed tree at 2 HC protégés, depth limit), `lineage_line`.
- `tests/test_coaching_tree_facts.py` → `staff_seasons` expansion and clipping with a fake cursor; `tree_context` returns `None` on a missing table.
- `tests/test_coaching_tree_review.py` → `validate` exit codes, `report` page content, `apply-review` all-or-nothing, `events` output.
- `tests/test_relationship_producer_rules.py` → `coaching_tree_events` facets, aliases, payload, version string.
- `tests/test_relationship_producer_run.py` → tree events appear from tick 24, not before; repeated ticks give the same events; `tree_staff` fixture.
- `tests/test_coach_profile.py` → `coaching_tree` present / omitted.
- `tests/test_offseason_service.py` → hires carry `coaching_tree`.
- `tests/test_coach_profiles.py` → `persona_for(..., lineage=...)`.
- `tests/test_personas.py` → `render_persona` prints the lineage line.
- `tests/test_coaching_tree_hiring.py` → `candidate_term` cap and cases, `follow_candidates`, shadow leaves picks unchanged, active can pick a follower (pure, with a stubbed pool).
- `tests/test_personality_features.py` → the new feature is listed, off by default, installed check.

Run with `pytest -m "not integration" <files>`. No integration tests.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`, `docs/in_progress/PERSONALITY_PLAN.md`.

### U1: Direction on mentorship ties

- **Owns:** `personality/relationship_events.py`, `personality/relationships.py`, `tests/test_relationship_events.py`, `tests/test_relationships.py`
- **Reads:** §2.1; `server/personality_api.py:63–135`; `docs/done/PERSONALITY_RELATIONSHIPS_PLAN.md` §2.2
- **Depends on:** none
- **Done when:** `direction` validates as §2.1 and reaches `relationship_context` ties; existing tests unchanged and passing.
- **Verify:** `pytest -m "not integration" tests/test_relationship_events.py tests/test_relationships.py tests/test_personality_api.py tests/test_relationship_store.py` all pass.

### U2: Tree module, facts and review tool

- **Owns:** `personality/coaching_trees.py` (new), `engines/coaching_tree_facts.py` (new), `tools/coaching_tree_review.py` (new), `newsroom/config/coaching_trees/_TEMPLATE.json` (new), `tests/test_coaching_trees.py` (new), `tests/test_coaching_tree_facts.py` (new), `tests/test_coaching_tree_review.py` (new)
- **Reads:** §2.2–§2.5; `personality/coach_profiles.py` (evidence and quote rules, `seed_index`, `tie_events`); `tools/coach_profile_review.py`
- **Depends on:** none (envelopes validate with or without U1's check)
- **Done when:** the module, facts and tool behave as §2.3–§2.5 on synthetic files.
- **Verify:** `pytest -m "not integration" tests/test_coaching_trees.py tests/test_coaching_tree_facts.py tests/test_coaching_tree_review.py` pass; `python3 -m tools.coaching_tree_review --help` prints the four subcommands.

### U3: Named-tree registry (research)

- **Owns:** `newsroom/config/coaching_trees/trees.json` (new)
- **Reads:** §2.2 (registry shape, starting set); one approved profile for the evidence style
- **Depends on:** none
- **Done when:** each tree has a root, optional parent, summary and ≥ 2 dated sources (≤ 2026-09-08), all `draft`; trees that can't be sourced are dropped and listed in the report.
- **Verify:** `python3 -c "import json; d=json.load(open('newsroom/config/coaching_trees/trees.json')); assert d['kind']=='registry' and d['trees']"` exits 0. The orchestrator runs `python3 -m tools.coaching_tree_review validate --batch registry` after wave 1 merges.

### U4: Tree research, batch A (BUF MIA NE NYJ BAL CIN CLE PIT HOU IND JAX)

- **Owns:** `newsroom/config/coaching_trees/batch_A.json` (new)
- **Reads:** §2.2 rules; the batch's profiles in `newsroom/config/coach_personas/` (existing coach ties); `trees.json`
- **Depends on:** U2, U3
- **Done when:** every seed person on these teams has been checked; edges to seed people who were his boss and memberships are written as drafts; every existing coach `mentorship` tie on these teams is confirmed as an edge or noted as not a tree in `notes`.
- **Verify:** `python3 -m tools.coaching_tree_review validate --batch A` exits 0; `report --batch A --out verify_output/coaching_tree_review/batch_A.md` writes the page.

### U5: Tree research, batch B (TEN DEN KC LV LAC DAL NYG PHI WAS CHI DET)

- **Owns:** `newsroom/config/coaching_trees/batch_B.json` (new)
- **Reads, Depends on, Done when, Verify:** as U4, batch B.

### U6: Tree research, batch C (GB MIN ATL CAR NO TB AZ LAR SF SEA)

- **Owns:** `newsroom/config/coaching_trees/batch_C.json` (new)
- **Reads, Depends on, Done when, Verify:** as U4, batch C.

### U7: Producer rule `coaching_tree`

- **Owns:** `engines/relationship_producers/__init__.py`, `engines/relationship_producers/rules.py`, `engines/relationship_producers/run.py`, `engines/relationship_producers/sources.py`, `tests/test_relationship_producer_rules.py`, `tests/test_relationship_producer_run.py`
- **Reads:** §2.6; `engines/relationship_producers/store.py` (target_alias); `personality/coaching_trees.py`, `engines/coaching_tree_facts.py`
- **Depends on:** U1, U2
- **Done when:** tree events build as §2.6 and the version is `producers-v2`.
- **Verify:** `pytest -m "not integration" tests/test_relationship_producer_rules.py tests/test_relationship_producer_run.py tests/test_relationship_producer_store.py` pass; `python3 -m engines.relationship_producers --help` runs.

### U8: Writer and voice context

- **Owns:** `engines/coach_profile.py`, `newsroom/offseason_service.py`, `personality/coach_profiles.py`, `newsroom/personas.py`, `tests/test_coach_profile.py`, `tests/test_offseason_service.py`, `tests/test_coach_profiles.py`, `tests/test_personas.py`
- **Reads:** §2.7; `engines/coaching_tree_facts.py`
- **Depends on:** U2; and `INFERRED_SLIDERS_AND_FOCUS_PLAN.md` in `docs/done/`
- **Done when:** the writer tool, carousel packet, `persona_for` and `render_persona` carry the lineage as §2.7.
- **Verify:** `pytest -m "not integration" tests/test_coach_profile.py tests/test_offseason_service.py tests/test_coach_profiles.py tests/test_personas.py` pass.

### U9: Tree hiring (off by default)

- **Owns:** `engines/coaching_tree_hiring.py` (new), `engines/coaching_carousel.py`, `personality/features.py`, `tests/test_coaching_tree_hiring.py` (new), `tests/test_personality_features.py`
- **Reads:** §2.8; `engines/owner_policy.py:87`; `engines/coaching_tree_facts.tree_graph`
- **Depends on:** U2; and `INFERRED_SLIDERS_AND_FOCUS_PLAN.md` in `docs/done/`
- **Done when:** `off` leaves the carousel unchanged; `shadow` reports; `active` applies the terms and the follow path.
- **Verify:** `pytest -m "not integration" tests/test_coaching_tree_hiring.py tests/test_personality_features.py tests/test_coaching_carousel_db.py` pass (the last skips without a test database); `python3 -m personality.features --help` runs.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2, U3 | — | Contract, code and registry; no shared files; U2 builds against §2.1 |
| 2 | U4, U5, U6 | U2, U3 | Three research batches, one file each; JR reviews the pages while wave 3 builds |
| 3 | U7, U8, U9 | U1, U2 (U8, U9 also the inferred-sliders plan done) | Each uses U2's interfaces; no shared files |

After wave 2: JR reviews `verify_output/coaching_tree_review/batch_{A,B,C}.md` and the registry, and the orchestrator runs `apply-review` with his decisions (only approved/edited data loads).

**File ownership check:**

| File | Unit |
|---|---|
| `engines/coach_profile.py` | U8 |
| `engines/coaching_carousel.py` | U9 |
| `engines/coaching_tree_facts.py` | U2 |
| `engines/coaching_tree_hiring.py` | U9 |
| `engines/relationship_producers/__init__.py` | U7 |
| `engines/relationship_producers/rules.py` | U7 |
| `engines/relationship_producers/run.py` | U7 |
| `engines/relationship_producers/sources.py` | U7 |
| `newsroom/config/coaching_trees/_TEMPLATE.json` | U2 |
| `newsroom/config/coaching_trees/batch_A.json` | U4 |
| `newsroom/config/coaching_trees/batch_B.json` | U5 |
| `newsroom/config/coaching_trees/batch_C.json` | U6 |
| `newsroom/config/coaching_trees/trees.json` | U3 |
| `newsroom/offseason_service.py` | U8 |
| `newsroom/personas.py` | U8 |
| `personality/coach_profiles.py` | U8 |
| `personality/coaching_trees.py` | U2 |
| `personality/features.py` | U9 |
| `personality/relationship_events.py` | U1 |
| `personality/relationships.py` | U1 |
| `tests/test_coach_profile.py` | U8 |
| `tests/test_coach_profiles.py` | U8 |
| `tests/test_coaching_tree_facts.py` | U2 |
| `tests/test_coaching_tree_hiring.py` | U9 |
| `tests/test_coaching_tree_review.py` | U2 |
| `tests/test_coaching_trees.py` | U2 |
| `tests/test_offseason_service.py` | U8 |
| `tests/test_personality_features.py` | U9 |
| `tests/test_personas.py` | U8 |
| `tests/test_relationship_events.py` | U1 |
| `tests/test_relationship_producer_rules.py` | U7 |
| `tests/test_relationship_producer_run.py` | U7 |
| `tests/test_relationships.py` | U1 |

No path appears twice.

## 7. Spec changes when built

- §2.19: tree hiring (`coaching_tree_hiring`, the three terms, the follow path recorded as `REHIRED`, shadow report).
- §3.3a: coaching trees (no table): the files, reviewed vs simulated edges, the in-sim rule, `staff_seasons`.
- §3.3r: the `coaching_tree` rule, `TREE_TICK`, `producers-v2`.
- §3.3t / §7.11: `direction` on mentorship ties; projection preference; `persona_for(lineage=)` and the `render_persona` line; switchboard feature list.
- §1.2: the `tools.coaching_tree_review` command row.
- §4.3 or §7: `get_coach_profile` returns `coaching_tree`; the carousel packet's hires carry it.
- Repository Layout: `personality/coaching_trees.py`, `engines/coaching_tree_facts.py`, `engines/coaching_tree_hiring.py`, `tools/coaching_tree_review.py`, `newsroom/config/coaching_trees/` (trees.json, batch_A–C.json, _TEMPLATE.json), the new tests, this plan's docs line.
- `PERSONALITY_PLAN.md` §5.3: add this plan to the tracker; note no migration.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Add `direction` (`mentor`/`mentee`) to the existing `relationships-v1` contract as an optional field, or bump the schema version? | Optional field, no bump. Old events stay valid and hash the same. |
| Q2 | In-sim rule: how many seasons, and what counts? | 2 simulated seasons (2026 on) as OC or DC under one head coach, summed across teams; `mixed` if he fired the coordinator. Seed overlap before 2026 counts only through reviewed edges. |
| Q3 | Should this plan also append the starting ties (tree edges and the profiles' ties) to a franchise's ledger? | No. Keep one apply path: the lifecycle's reviewed apply appends both later. This plan builds the events and an offline `events` preview. |
| Q4 | Build tree hiring now (off by default) or leave it to its own plan? | Build it now in wave 3, off. Turning it on stays your separate call, after a shadow season. |
| Q5 | Bump producers to `producers-v2` for the new rule? | Yes. No franchise has stored producer events, so it costs nothing and keeps replays honest. |
| Q6 | Leave the 57 undirected coach-to-coach mentorship ties in the profiles as they are? | Yes. Profiles stay approved; a directed tree edge to the same person wins in the projection. |
| Q7 | Let new named trees form in the sim? | Yes: a coach with 2 protégés who became head coaches gets "<Surname> tree", computed, not stored. |
| Q8 | Show tree facts to writers before the personality switchboard is on? | Yes. They're documented history and the league's own tenures, not personality effects. |
| Q9 | Count college staffs, exclude playing careers, and run all three research batches at once? | Yes to all three; if you'd rather check quality first, run batch A alone and review it before B and C. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | build/ct-u1 | .claude/worktrees/ct-u1 | merged | a3fe52e |
| U2 | 1 | build/ct-u2 | .claude/worktrees/ct-u2 | merged | c54df6d |
| U3 | 1 | build/ct-u3 | .claude/worktrees/ct-u3 | merged | 04a5fde |
| U4 | 2 | build/ct-u4 | .claude/worktrees/ct-u4 | merged | 012aa1b |
| U5 | 2 | build/ct-u5 | .claude/worktrees/ct-u5 | merged | b59a3cc |
| U6 | 2 | build/ct-u6 | .claude/worktrees/ct-u6 | merged | 02406ac |
| U7 | 3 | build/ct-u7 | .claude/worktrees/ct-u7 | merged | d0b396e |
| U8 | 3 | build/ct-u8 | .claude/worktrees/ct-u8 | merged | 883b613 |
| U9 | 3 | build/ct-u9 | .claude/worktrees/ct-u9 | merged | 638ecfc |
