# Project Mu: Coach and Player Agent Loops Plan

**Status:** approved by JR (2026-11-03), Q1–Q3 settled as recommended (§8); not built. Queued: JR wants every personality sub-plan written and approved before more building (2026-11-03). The other plans were updated at approval (§7).
**Builds on:**
- [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §8: agentic loops; the player negotiates for himself, with no hired agent.
- [`TRADE_NEGOTIATIONS_PLAN.md`](TRADE_NEGOTIATIONS_PLAN.md) §2.1, §2.3: the GM loop pattern (legal options, persona policy, model choice with fallback, record don't replay).
- [`PERSONALITY_CONTRACT_POLICY_PLAN.md`](PERSONALITY_CONTRACT_POLICY_PLAN.md) §2.3 (shared scoring and counters).
- [`PERSONALITY_DEVELOPMENT_COACHING_PLAN.md`](PERSONALITY_DEVELOPMENT_COACHING_PLAN.md) §2.5 (close-call opportunity preferences).
- [`PERSONALITY_RELATIONSHIPS_PLAN.md`](../done/PERSONALITY_RELATIONSHIPS_PLAN.md) §2.2 (`promise_made` / `promise_resolved`).
- [`FRONT_OFFICE_INBOX_PLAN.md`](../in_progress/FRONT_OFFICE_INBOX_PLAN.md) (the `CONTRACT` and `ROLE_TALK` kinds).
- [`PERSONALITY_ACTIVATION_PLAN.md`](PERSONALITY_ACTIVATION_PLAN.md) (the switchboard and `PersonalityDeps`).
- [`REAL_COACH_PROFILES_PLAN.md`](../in_progress/REAL_COACH_PROFILES_PLAN.md) (`persona_for`).

**Goal:** Let CPU coaches and players make their own choices and speak for themselves, inside what the engines allow. A coach decides his close depth-chart calls and can promise an unhappy player a role by a given week. That promise is recorded, kept or broken, and trust and trade requests react to it. A player in contract talks picks how hard to push among the counters the engine says he would accept, and can go public with it. Every decision is a seeded personality choice for free. The model is asked, and writes the words, only where JR or the press will see it.

**Delivers:**
- Package `engines/agent_loops/`:
  - `base.py`: options, policy and model choice with fallback, the marquee budget, recording;
  - `coach.py`: chart close calls, promises, and promise reviews;
  - `player.py`: contract responses and leaks.
- `free_agency_engine.counter_options(...)`: the engine's clearing counters at a few ask levels.
- Migration `db/migrations/2027_02_01_agent_loops.sql`: table `agent_loop_decisions`.
- Wiring:
  - league office (`PersonalityDeps`) for CPU charts and CPU contract responses;
  - the inbox `CONTRACT` and `ROLE_TALK` kinds for player replies;
  - a `PLAYER_STATEMENT` post kind in the social desk for leaks;
  - switchboard features `coach_loop` and `player_loop`.

**JR's answers (2026-11-03):**
- D1: Every decision runs the seeded personality policy, at no cost. The model is asked, to choose among the legal options and give the words, only where JR or the press will see it: JR's inbox talks, pressers, a leak, and a capped number of marquee CPU cases a week.
- D2: A CPU coach's loop decides close calls among the staff algorithm's legal charts (inside the development plan's close-score window). It can also promise an unhappy player of his a slot by a week, recorded as `promise_made` so trust and trade requests react.
- D3: Players can go public during contract talks, capped. A statement is one of the player loop's options. It becomes a factual public event the newsroom can report, capped per player per season, and never states figures he wasn't offered.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | The trade talks plan §2.1–§2.3: every option is legal before a chooser sees it; `policy_choice` is seeded from the persona's sliders and traits; `llm_choose` makes one call per turn and falls back to the policy on a bad key; record, don't replay. | `base.py` generalizes this for coaches and players. Same rules. |
| S2 | `engines/free_agency_engine.py:210` `_counter` returns only the smallest raise (in `COUNTER_STEP` steps up to `COUNTER_MAX_RAISE`) at his ideal term that clears his bar, or None. `:230` `acceptance` decides. | The player loop needs a few clearing asks, not one. `counter_options(offer, player, team, market, bar, k=3)` returns the first clearing step and the next `k − 1` steps that still clear. Acceptance stays the engine's. |
| S3 | `pipeline/league_office.py:1330` `step_coach_charts` builds CPU charts. The development plan's `opportunity_adjustments` (its §2.5) works only inside `close_score_window`. The activation plan's `PersonalityDeps.chart_adjustments` is the seam. | The coach loop chooses among the neutral chart and a few one-swap variants inside that window, and the activation seam passes its choice. No new scoring. |
| S4 | The inbox plan's `ROLE_TALK` (JR's coach ↔ his player, with promises: slot, depth and review week) and `CONTRACT` (JR's GM ↔ his player) give the player a neutral voice today (its §2.3 table, `:152`). | `player.respond_inbox` supplies the player's reply and stance in those threads. JR's own coach never runs a CPU loop. |
| S5 | `newsroom/social_desk.py:165–171` posts trade rumors (`TRADE_RUMOR_MAX = 2`, grounded: "A RUMOR, not a done deal"); `:848` `rumor_facts`. | Leaks follow the same pattern: a `PLAYER_STATEMENT` post kind with grounding from the recorded statement only. |
| S6 | `engines/gm_llm.py:60` `make_llm(label, ...)` and `:100` `flush` are the house model wrapper, used for GM rationale. | Loops call `make_llm('agent_loop')`. Cloud model for now (PERSONALITY_PLAN §8). |
| S7 | Latest reserved migration date is `2027_01_31` (activation). | This plan takes `2027_02_01`. |

## 1. Scope

**In:**
- The shared loop base, with the marquee model budget (D1).
- The CPU coach loop: close-call chart choices, promises to unhappy players, and promise reviews at the due week (D2).
- The player loop: CPU contract responses (choice among clearing counters, accept, reject) and capped leaks (D3); replies in JR's inbox contract and role talks.
- A decisions table, and wiring into the league office, the inbox kinds, the social desk and the switchboard.

**Out (non-goals):**
- JR's own coaches and GMs: JR makes those choices.
- GM loops: the trade talks plan.
- Rest and snap plans (D2).
- Changing acceptance, valuation, legality or the contract formulas: owning engines.
- A hired agent role (PERSONALITY_PLAN §13).
- Player-to-player talk, and "views for players who speak" (deferred, PERSONALITY_PLAN §12).

## 2. Design

### 2.1 Base (`engines/agent_loops/base.py`)

```python
@dataclass(frozen=True)
class Option: key: str; action: str; detail: dict; label: str         # label is what the model sees; never a hidden score
MARQUEE_PER_WEEK = 4          # model calls for CPU cases per league week (estimate, §8 Q1)
def policy_choice(options, persona, weights, rng) -> str              # seeded random.Random(f"loop:{season}:{tick}:{kind}:{subject}")
def model_choice(llm, persona_block, facts, options, history) -> tuple[str, str, str]   # (key, text, source POLICY|MODEL|MODEL_REJECTED)
def decide(cur, *, league_id, season, tick, kind, subject, options, persona, weights, facts, seen: bool, llm) -> dict
```

**`decide`** returns the stored decision if `(league_id, season, tick, kind, subject_key)` was already decided (record, don't replay). Otherwise:
1. Run `policy_choice`.
2. If `seen` is true (D1: JR's thread, a presser answer, a leak, or a marquee case within `MARQUEE_PER_WEEK`), call `model_choice`. A key outside `options` falls back to the policy key with `MODEL_REJECTED`. Its text is kept only if a grounding check passes: names, slots and figures must appear in `facts`.
3. Store the decision.

`persona` is the core feature view plus `persona_for` (coaches) or the player's view. `weights` are each loop's own slider and trait coefficients, listed below as review-only estimates.

### 2.2 Coach loop (`engines/agent_loops/coach.py`)

```python
CHART_VARIANTS = 3; PROMISE_REVIEW_WEEKS = 3; PROMISE_MIN_UNHAPPY_WEEKS = 2
def chart_options(neutral_chart, scored_candidates, close_window) -> list[Option]   # NEUTRAL + up to 3 one-swap variants inside the window
def choose_chart(cur, team_id, season, tick, coach_view, options, *, llm) -> dict   # the chosen chart and the decision row
def promise_options(player, role_history, slot) -> list[Option]                     # NONE | PROMISE {slot, depth, review_tick}
def review_promises(cur, season, tick) -> list[dict]                                 # promise_resolved envelopes at their review tick
```

**Chart options:**
- `NEUTRAL` is the staff algorithm's chart.
- Each variant swaps two players at one base slot whose neutral scores are within `close_window`.
- The policy uses the development plan's `opportunity_adjustments` as its weights: YOUTH_DEVELOPER leans to the younger player, and a reviewed veteran preference leans the other way.
- Marquee: a starting quarterback, or a starter benched while healthy.

**Promises:**
- When a player's role events show at least `PROMISE_MIN_UNHAPPY_WEEKS` unsatisfied weeks, his coach's loop sees `NONE` or `PROMISE`. The promise is the slot he lost, at depth 1, by `tick + PROMISE_REVIEW_WEEKS`.
- `p_promise` = 0.25 + 0.05·(warmth − 5) + 0.1 if PLAYERS_COACH − 0.1 if DISCIPLINARIAN, clamped 0.05–0.6 (estimates).
- A made promise is a `promise_made` envelope (issuer = the coach, beneficiary = the player, criterion = `{slot, depth}`, due = review tick), appended through the relationships store.
- At the review tick, `review_promises` reads the MADDEN chart and emits `promise_resolved` (`kept` or `broken`). If he's injured at review, the resolution is `unknown` and the promise is reviewed again a week later, once.
- The next chart choice treats an open promise as a soft preference: the kept variant is weighted up by the coach's `drive` and `warmth`. It is never forced past the close window.

### 2.3 Player loop (`engines/agent_loops/player.py`)

```python
LEAK_MAX_PER_PLAYER_SEASON = 1; LEAK_MAX_PER_WEEK = 3
def contract_options(offer, player, team, market, bar, *, accepts: bool) -> list[Option]
def respond_contract(cur, player_view, offer, ctx, *, llm, seen) -> dict              # CPU re-sign / FA / UDFA response
def respond_inbox(cur, thread, player_view, facts, *, llm) -> dict                  # JR's CONTRACT / ROLE_TALK threads
```

**Options:**
- `ACCEPT` (when the engine's acceptance clears).
- `COUNTER_MIN`, `COUNTER_ASK` and `COUNTER_HARD`, from `free_agency_engine.counter_options(..., k=3)`; only those that exist.
- `REJECT`.
- `LEAK_AND_COUNTER`: `COUNTER_ASK` plus a public statement. Offered only when there is no leak by him this season and the week's count is under `LEAK_MAX_PER_WEEK`.

**Policy weights** (estimates): MONEY_FIRST and confidence push toward ASK and HARD; earned attachment to the offering team, from the relationships context, pulls toward ACCEPT and MIN; SPOTLIGHT_SEEKER and QUOTABLE raise the leak odds; GUARDED lowers them.

**The choice changes only which clearing counter is put to the team.** The team's response stays with the contract engine and the league office's checks. A HARD counter the team won't meet just fails as it would today.

**The statement** carries only the recorded facts: his position, team and "wants a deal" wording, with no figures beyond what was offered and asked. It is stored with `visibility 'public'`, and the social desk turns it into a `PLAYER_STATEMENT` post.

**Marquee:** a contract whose ask is in the top 50 average salaries of the season (estimate).

### 2.4 Table (`2027_02_01_agent_loops.sql`)

```sql
CREATE TABLE agent_loop_decisions (
  id BIGSERIAL PRIMARY KEY, league_id BIGINT NOT NULL, season INT NOT NULL, tick SMALLINT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('CHART','PROMISE','PROMISE_REVIEW','CONTRACT','INBOX_REPLY','LEAK')),
  subject_key TEXT NOT NULL,              -- e.g. 'team:12:WR' or 'player:4411:offer:88'
  persona_type VARCHAR(32), persona_id VARCHAR(64),
  options JSONB NOT NULL, policy_key TEXT NOT NULL, chosen_key TEXT NOT NULL,
  choice_source TEXT NOT NULL CHECK (choice_source IN ('POLICY','MODEL','MODEL_REJECTED')),
  text TEXT, visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','public')),
  facts JSONB NOT NULL, cause_refs JSONB NOT NULL DEFAULT '[]', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (league_id, season, tick, kind, subject_key));
CREATE INDEX agent_loop_decisions_public ON agent_loop_decisions (league_id, season, tick) WHERE visibility = 'public';
```

The migration is transactional and refuses an incompatible table.

### 2.5 Wiring

**League office:** `PersonalityDeps.chart_adjustments` calls `coach.choose_chart` when `coach_loop` is shadow or active. In shadow it records the choice and returns the neutral chart. `PersonalityDeps.resign_decisions`, `run_wave` and `udfa_market` call `player.respond_contract` per counter case when `player_loop` is shadow or active. In shadow, the engine's first clearing counter is used as today.

**Watcher:** `coach.review_promises` runs in the producers step's tick, after the role events.

**Inbox:** the `CONTRACT` and `ROLE_TALK` kinds call `player.respond_inbox` for the player's reply when `player_loop` isn't off. These are always "seen", so they use the model.

**Social desk:** a `PLAYER_STATEMENT` post kind reads public `LEAK` decisions for the week, at most `LEAK_MAX_PER_WEEK`, with grounding "a statement, not a signing".

**Switchboard:** `coach_loop` and `player_loop` are added to `personality.features.FEATURES` after `development_opportunity`.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Model cost from marquee cases and inbox replies. | Count model calls per week in a shadow season on a tuning franchise. | Lower `MARQUEE_PER_WEEK`; inbox replies stay. |
| R2 | HARD counters make re-signs fail too often. | Shadow-season report: counter level against outcome. | Tune the weights; acceptance itself never changes. |
| R3 | CPU promises pile up and break, souring every locker room. | A shadow report of promises made, kept and broken per team. | Lower `p_promise`; the promise only fires after two unhappy weeks. |
| R4 | Leaks repeat or overstate. | The grounding check, and the per-player and per-week caps. | The post uses only recorded facts; a failed check drops the post. |
| R5 | Shared files with other plans: `engines/free_agency_engine.py` (contract plan U3), `pipeline/league_office_personality.py` and `personality/features.py` (activation plan), `engines/inbox/kinds/*.py` (inbox plan), `newsroom/social_desk.py` (trade talks U6). | Build order. | This plan builds after those and re-reads each file. |

**Spike first:** none.

## 4. Tests (synthetic, no live DB)

- `tests/test_agent_loops_base.py`:
  - the seeded policy is deterministic;
  - `seen` false never calls the model;
  - a model key outside the options falls back with `MODEL_REJECTED`;
  - ungrounded text is dropped;
  - replay returns the stored decision without a model call;
  - the marquee budget stops at `MARQUEE_PER_WEEK`.
- `tests/test_agent_loops_coach.py`:
  - chart variants only inside the close window and always legal;
  - YOUTH_DEVELOPER leans young;
  - the promise appears only after two unhappy weeks, with `p_promise` bounds;
  - `promise_made` and `promise_resolved` envelopes (kept, broken, and unknown when injured, re-reviewed once);
  - an open promise weights the choice without leaving the window.
- `tests/test_agent_loops_player.py`:
  - `counter_options` returns only clearing steps, in order;
  - the options appear when they exist;
  - MONEY_FIRST pushes up, attachment pulls down;
  - leak caps per player and per week;
  - the statement holds no figures beyond offered and asked;
  - `respond_inbox` uses the model and stores the reply.
- `tests/test_free_agency_engine.py` (existing; add `counter_options` cases): the first entry equals `_counter`'s, and existing results are unchanged.
- `tests/test_agent_loops_wiring.py`:
  - in shadow, charts and counters equal the neutral ones, with choices recorded;
  - active uses them;
  - inbox kinds call `respond_inbox` unless `player_loop` is off;
  - the social desk posts public leaks within the cap;
  - the switchboard order includes the two new features.

Run with `pytest -m "not integration" tests/test_agent_loops_base.py tests/test_agent_loops_coach.py tests/test_agent_loops_player.py tests/test_free_agency_engine.py tests/test_agent_loops_wiring.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Base, coach loop and table

- **Owns:** `engines/agent_loops/__init__.py` (new), `engines/agent_loops/base.py` (new), `engines/agent_loops/coach.py` (new), `db/migrations/2027_02_01_agent_loops.sql` (new), `tests/test_agent_loops_base.py` (new), `tests/test_agent_loops_coach.py` (new)
- **Reads:** this plan §2.1, §2.2, §2.4; the trade talks plan §2.1–§2.3; the development plan §2.5; the relationships plan §2.2; `engines/gm_llm.py`; `engines/depth_chart_engine.py:305–400`
- **Depends on:** none. It builds against the documented development and relationships interfaces.
- **Done when:** §2.1–§2.2 work against fakes; the migration text checks the definition.
- **Verify:** `pytest -m "not integration" tests/test_agent_loops_base.py tests/test_agent_loops_coach.py -q` passes.

### U2: Player loop and counter options

- **Owns:** `engines/agent_loops/player.py` (new), `engines/free_agency_engine.py`, `tests/test_agent_loops_player.py` (new), `tests/test_free_agency_engine.py`
- **Reads:** U1; this plan §2.3; `engines/free_agency_engine.py:150–260`; the contract plan §2.3
- **Depends on:** U1. Cross-plan: the contract plan's U3 is merged (it owns `free_agency_engine.py` first).
- **Done when:** `counter_options` and the player loop work as §2.3; existing free agency results are unchanged.
- **Verify:** `pytest -m "not integration" tests/test_agent_loops_player.py tests/test_free_agency_engine.py -q` passes.

### U3: Wiring

- **Owns:** `pipeline/league_office_personality.py`, `personality/features.py`, `engines/inbox/kinds/contract.py`, `engines/inbox/kinds/role_talk.py`, `newsroom/social_desk.py`, `tests/test_agent_loops_wiring.py` (new)
- **Reads:** U1, U2; this plan §2.5; those files as merged by their plans
- **Depends on:** U2. Cross-plan: the activation, inbox and trade talks plans are merged.
- **Done when:** §2.5 works in shadow and active, and the existing tests of those files pass.
- **Verify:** `pytest -m "not integration" tests/test_agent_loops_wiring.py tests/test_league_office_personality.py tests/test_personality_features.py -q` passes.

## 6. Waves

| Wave | Units | Needs merged first | Why |
|---|---|---|---|
| 1 | U1 | — | The base every loop uses. |
| 2 | U2 | U1, contract U3 | The player loop imports the base. |
| 3 | U3 | U2 and the cross-plan files (R5) | Touches files other plans own first. |

Building waits until every personality sub-plan is approved (JR, 2026-11-03). `coach_loop` and `player_loop` then go through the activation plan's tuning-franchise shadow season and clearance like any feature.

**File ownership check:**

| File | Unit |
|---|---|
| `db/migrations/2027_02_01_agent_loops.sql` | U1 |
| `engines/agent_loops/__init__.py` | U1 |
| `engines/agent_loops/base.py` | U1 |
| `engines/agent_loops/coach.py` | U1 |
| `engines/agent_loops/player.py` | U2 |
| `engines/free_agency_engine.py` | U2 |
| `engines/inbox/kinds/contract.py` | U3 |
| `engines/inbox/kinds/role_talk.py` | U3 |
| `newsroom/social_desk.py` | U3 |
| `personality/features.py` | U3 |
| `pipeline/league_office_personality.py` | U3 |
| `tests/test_agent_loops_base.py` | U1 |
| `tests/test_agent_loops_coach.py` | U1 |
| `tests/test_agent_loops_player.py` | U2 |
| `tests/test_agent_loops_wiring.py` | U3 |
| `tests/test_free_agency_engine.py` | U2 |

No path appears twice.

## 7. Spec changes when built

- §1.1 Repository Layout: `engines/agent_loops/`, the migration, the five tests, and this plan's entry.
- §2.17b: `counter_options`.
- §2.21a: CPU charts and contract responses through the loops, by mode.
- §3.3p: `agent_loop_decisions`.
- §7.8 social desk: the `PLAYER_STATEMENT` post kind and its caps.
- §7.11: the loop pattern, the marquee model budget, coach promises and reviews, player counters and leaks.
- Other plans, at approval:
  - PERSONALITY_PLAN §8 and the §5.3 tracker point here;
  - the activation plan's `FEATURES` gains `coach_loop` and `player_loop`;
  - the inbox plan's `CONTRACT` and `ROLE_TALK` note the player's voice comes from `respond_inbox`.

## 8. Decisions for JR

JR approved all three recommendations on 2026-11-03.

| # | Question | Decision (JR, 2026-11-03) |
|---|---|---|
| Q1 | How many model-backed CPU cases a week? | 4 (`MARQUEE_PER_WEEK`), for the cases most likely to make news: a starting QB decision, a healthy starter benched, a top-50 contract. Tune from the shadow season's call count. |
| Q2 | How long until a promise is judged? | 3 weeks (`PROMISE_REVIEW_WEEKS`). If he's hurt at the review, judge it a week later, once. |
| Q3 | How many leaks? | At most one per player per season and three league-wide a week. |
