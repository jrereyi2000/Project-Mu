# Project Mu: Owners, Scouts and Media Voters Plan

**Status:** approved by JR (2026-11-03), Q1, Q2 and Q4 settled as recommended and Q3 changed by JR (D5: All-Pro by ballot too) (§8); built on main 2026-11-06 (U1–U4, U7) and 2026-11-07 (U5, on JR's instruction "Build U5 first") except one held unit: **U6** (scouting directors, `engines/scout_voice.py`, the `2027_02_02` migration), held per PERSONALITY_PLAN §5.3 for scouting U2's `engines/scout_voice.py` (not built). The six switchboard features are already in `personality/features.py` (the activation plan added them); they can't leave `off` until U6's migration file exists (`features.REQUIREMENTS`). The 32 owner profiles are `owner-profile-v2` and approved by JR (2026-11-07). No migration applied; every owner feature is off.
- As built (U5, 2026-11-07): the shared reads live in a new `engines/owner_context.py` (not in the plan's file list): the owner_* switchboard modes (savepointed; any error reads off), each team's owner view from his reviewed profile (`owner_profiles.core_initial_values`, effective sliders and traits; `team_owners` is U6's), JR's teams (`gm_team_settings.user_controlled`, plus the registry's `user_teams` in the league office) left out, and `owner_decisions` read/write that does nothing until U6's table exists. Patience: a hot-seat row may carry `owner` terms; `coach_hot_seat` (Black Monday and playoff exits) and `gm_rollover` use line 1.0 × scale and minimum tenure + delta (at least 1); rows without terms are decided byte for byte as before. Hiring: carousel head-coach openings only (appeal term on the opening, hiring key before the seeded pick, composed with the coaching-tree terms). Spending: `plan_offers`/`cpu_offers(..., spending=)` through `PersonalityDeps.cpu_offers`; the re-sign stage keeps its 2% buffer. Meddling: head-coach hires in the carousel (VETO re-picks without the candidate, PUSH hires the candidate with the best positive hiring key) and top-50 CPU free-agent signings in `PersonalityDeps.signings_from_decisions` (VETO drops the signing; no PUSH for signings, as no legal alternative contract is built); CPU-to-CPU trades have no league-office path yet, so trade meddling has no caller; the fan-favourite term isn't passed (no popularity rank at hand); the agent-loop `decide` row and the model's line for a seen decision are not written (`owner_policy.meddle_choice` only; `loop_decision_id` stays NULL). Shadow equals today for every feature and reports what the owner would have changed (`owner_shadow` in the hot-seat, rollover and carousel reports; league office receipts). The `OWNER` inbox kind sends only while `owner_meddling` is active (off and shadow: nothing), and records the reply as a `meaningful_interaction` itself (judge False), which waits in the outbox until the owners' `db-owner` identities exist (U6). Checked on a scratch copy of madden_test (2035 offseason, rolled back): off and shadow hire the same staffs, active ran with one owner veto.
- As built (U1): the six ownership traits sit in two existing catalogue categories (`MEDDLER`/`HANDS_OFF` and `DEEP_POCKETS`/`BUDGET_CONSCIOUS` in `work`, `HEADLINE_HUNTER`/`PATIENT_BUILDER` in `ambition`) and the five scout traits in `football_mind`, each tagged `group: ownership|scouting`. A new Ownership/Scouting category would have needed `newsroom/personas.py` (`VALID_CATEGORIES`) and a three-per-category exception in `personality/core.py`, neither owned by a unit; this way an owner holds one trait from each pair within the core's two-per-category rule (Q2 kept).
- As built (U1): no existing trait was owner-eligible, so 14 existing traits got small owner weights (`LOYAL`, `GRUDGE_HOLDER`, `PEACEMAKER`, `LEGACY_BUILDER`, `RING_CHASER`, `HOMETOWN_GUY`, `SPOTLIGHT_SEEKER`, `QUOTABLE`, `GUARDED`, `MEASURED`, `ANALYTICS_BELIEVER`, `OLD_SCHOOL`, `FAMILY_MAN`, `HISTORIAN`), keeping owners at the coach rule of 3–5 traits; other roles' seeded draws are unchanged (tested).
- As built (U1): `coach_profiles.py` is not U1's, so `personality/owner_profiles.validate_profile_file` reuses the coach module's helpers (evidence, citations, quotes) instead of a `kind='owner'` argument on the coach function; the review tool's `--kind owner` routes to it. `OWNERS_DATA` holds the owners as of the eve of the 2026 season (JR's start-data decision), not 2025: Seattle is Neeru Khosla (the Khosla family's purchase was approved 2026-08-26), Indianapolis Carlie Irsay-Gordon, Chicago George McCaskey, Green Bay team president Ed Policy (publicly owned), Tampa Bay Joel Glazer (co-owner; JR to confirm which Glazer).
- As built (U4): the plan's temperament/drive line terms are read with the catalogue's polarity (temperament high = volatile): each point of temperament or drive above 5 lowers the hot-seat line by 0.01. `PUSH` weight is 0.15 (an estimate; the plan gave none) and `meddle_choice` returns the seeded policy key without calling `agent_loops.base.decide` (the loop row is U5's wiring).
- As built (U7): `award_ballots.run_awards` / `run_all_pro` and the stored-result checks (`award_winners.check_against_stored`, `all_pro.check_against_stored`) exist but nothing calls them: `newsroom/swarm_dispatcher.py` (not a unit's file) still parses and stores from the articles. Switching the Week 19/22 articles to report stored ballots waits for the `media_voters` feature (activation plan) and the `award_ballots` table (U6's migration, held). Since 2026-11-07 the dispatcher's Week 19/22 storage step reads `media_voters` from the switchboard (`award_ballots.feature_mode`): off and shadow keep the parse-and-store path; active runs `decide_awards`/`decide_all_pro` and checks the article against the stored results (writer facts not fed yet). Shadow stays unreachable until U6's migration exists.
- **TODO (JR, 2026-11-07, open; U7 follow-up, not built):** "fix the writer ballots for All-Pro and end-of-season awards to make sure the articles are aligned". With `media_voters` active the stored winners and All-Pro teams come from the writer ballots (`newsroom/award_ballots.py`, `engines/award_candidates.py`, `newsroom/award_winners.py`, `newsroom/all_pro.py`), but the article writer isn't given the ballot results, so the Week 22 awards and Week 19 All-Pro articles can name different winners than the stored ones. Needed: pass the ballot results (`results_facts`, `all_pro_facts`) into the award and All-Pro article generation, check each article against the stored results (`check_against_stored`), and rewrite on a miss (`stored_winners_fix_prompt`).
- As built (U7): COTY candidates read `coty_scores` (there is no `coty_scores()` function). A player without a `season_stat_general` row counts as meeting the 10-game minimum.
- As built (U2–U3): all 32 owner drafts were written from 2–3 dated web sources each (searched 2026-11-06, sources through 2026-08-27), three traits each and sliders mostly at 5; every file's `review.notes` flags the thin coverage and that a few publication dates come from the news cycle the article covers. Notes for JR: Tampa Bay uses Joel Glazer (co-chairman who speaks publicly) and Seattle uses Neeru Khosla (controlling owner per the NFL; Vinod is the chair and public voice; their 2026-09-09 introductory press conference is after the cutoff and unused); Green Bay's file is team president Ed Policy (publicly owned club). Later 2026 events (for example the Colts firing Chris Ballard in October 2026) are excluded by the cutoff. Ties name each owner's 2026 head coach where a source supports it.
**Builds on:** [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) phase 6, §12 (owner and scout categories); [`REAL_COACH_PROFILES_PLAN.md`](../done/REAL_COACH_PROFILES_PLAN.md) (the reviewed-profile files, research batches and review tool); [`SCOUTING_REVAMP_PLAN.md`](../in_progress/SCOUTING_REVAMP_PLAN.md) (the `scout_voice` / `regional_bias` hooks); [`PERSONALITY_DEVELOPMENT_COACHING_PLAN.md`](../done/PERSONALITY_DEVELOPMENT_COACHING_PLAN.md) §2.6 (hiring and owner patience left as notes); [`PERSONALITY_ACTIVATION_PLAN.md`](../done/PERSONALITY_ACTIVATION_PLAN.md) (switchboard); [`COACH_PLAYER_AGENT_LOOPS_PLAN.md`](../in_progress/COACH_PLAYER_AGENT_LOOPS_PLAN.md) (the loop base); [`FRONT_OFFICE_INBOX_PLAN.md`](../in_progress/FRONT_OFFICE_INBOX_PLAN.md); PROJECT_SPEC §2.11, §2.19, §2.17d, §7.6b, §7.6c.

**Goal:** Fill in the league's last faceless decision-makers.
- **Owners:** the 32 real-inspired owners become reviewed characters whose personalities shape four things:
  - **patience:** how long coaches and GMs get;
  - **hiring:** what kind of coach they hire;
  - **spending:** how much of the cap their GM may use and how hard they chase free agents;
  - **meddling:** whether they overrule their GM on big moves, and talk to JR's GM in the inbox.
- **Scouting directors:** each team gets one, generated, who leans its draft board and voices its reports.
- **Media voters:** the season's major awards and the All-Pro teams are decided by a panel of writer personas casting ballots, instead of by whoever writes those articles.

**Delivers:**
- **Owners:**
  - `newsroom/config/owner_personas/<TEAM>.json`: 32 reviewed, cited owner profiles, using the coach-profile method and review tool;
  - `personality/owner_profiles.py`;
  - `engines/owner_policy.py`: pure patience, hiring, spending and meddling policies;
  - wiring into `coach_hot_seat`, `gm_rollover`, `coaching_carousel`, `transactions_engine` and the league office's personality layer;
  - an inbox `OWNER` kind.
- **Scouts:** `engines/scout_directors.py` (one generated director per team, created through the lifecycle plan), and their traits in the scouting plan's `scout_voice` and `regional_bias` hooks.
- **Voters:** `engines/award_candidates.py` (stat-based candidate scores, per award and per All-Pro slot), `newsroom/award_ballots.py` (writer ballots and tallies), and `newsroom/award_winners.py` and `newsroom/all_pro.py` storing winners and teams from ballots.
- **Catalogue:** new owner and scout traits in `newsroom/config/persona_traits.json`.
- **Migration:** `db/migrations/2027_02_02_owners_scouts_voters.sql`, with tables `team_owners`, `owner_decisions`, `scout_directors`, `award_ballots`.
- **Switchboard features:** `owner_patience`, `owner_hiring`, `owner_spending`, `owner_meddling`, `scout_directors`, `media_voters`.

**JR's answers (2026-11-03):**
- D1: The owners are the real-inspired NFL owners, as fictional characters (D13), researched and cited like the coaches: 32 more files through the same review tool, reviewed by JR.
- D2: Owner personalities change all four things in v1: patience, hiring, spending and meddling.
- D3: One generated scouting director per team. His traits (Traits Chaser, Tape Grinder, Character Hawk, Floor Seeker / Ceiling Gambler) lean that team's board and voice its reports.
- D4: Writer ballots decide the awards. A panel of about 20 writer personas each casts a ballot from the season's stats, leaned by his views. Points decide the winners, and the awards article reports the stored result and tallies. No model calls for the votes.
- D5 (JR, 2026-11-03, changing the plan's Q3): the All-Pro teams are decided by the same panel's ballots, not by the All-Pro article.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | No owner table exists (`db/init_db.sql` has `owner_team_id` only on draft picks, `:776`). The core supports role `owner` (`personality/core.py:14`). | `team_owners` holds the 32 owners, seeded from the reviewed files; identities go through the lifecycle plan like coaches. |
| S2 | `engines/coach_hot_seat.py:21–33`: `HOT_SEAT_LINE = 1.0`, `MIN_TENURE = 2`, `MIN_FIRES = 5`, `MAX_FIRES = 7`, `SUPER_BOWL_YEARS = 3`, `EXIT_MIN_TENURE = 3`. `engines/gm_rollover.py:24` `HOT_SEAT_LINE = 1.0`, and `:29–56` `hot_seat` fires a GM above the line with odds `min(0.9, 0.35 + 0.3·(score − 1))`, never on JR's teams (`:47`). | Patience scales each team's line and minimum tenure within bounds. The league-wide fire counts (`MIN_FIRES`, `MAX_FIRES`) stay as they are. JR's GMs are never fired. |
| S3 | `engines/coaching_carousel.py:204` `opening_appeal(parts)` scores a job for candidates; `:265` `pick(candidates, key)` chooses a hire; `:321` `hiring_cycle`. | Hiring preference is a bounded term in the team's `key` (for example a Headline Hunter favours a known name or a retread; a Patient Builder favours a young coordinator). Appeal gains a small owner term (a Meddler scares candidates off). |
| S4 | `engines/transactions_engine.py:50` `CAP_BUFFER_SHARE = 0.02`, `:59` `OFFERS_PER_WAVE = 4`, `:60` `OFFER_NEED_PREMIUM = 0.10`, used by `cpu_offers` and signings. | Spending sets each team's buffer, offers per wave and need premium within bounds. Pricing and legality stay the engines'. |
| S5 | The trade talks plan executes agreed CPU-to-CPU trades through the league office. The league office plans free-agency signings (`pipeline/league_office.py:1549`) and the carousel makes hires. | Meddling reviews a CPU team's big moves (a trade sending a top player, a top-50 signing, a head-coach hire) with approve, veto or push, through the agent-loop base. It never touches JR's moves. |
| S6 | The scouting plan ships neutral scouts and leaves hooks: `scout_voice.ScoutVoice` and `regional_bias()` (its §2, `:54`); the core supports role `scout`. | Directors plug into those hooks. Board leaning stays inside the scouting plan's noise model, as a bounded bias. |
| S7a | `newsroom/all_pro.py` (spec §7.6b) parses 62 selections from the Week 19 `OFFICIAL_AP_ALL_PRO_ROSTER` article: First-Team Offense 13 slots, First-Team Defense and Special Teams 18, and the same for the Second Team; it stores them in `awards` and orders them by its slot quotas. | D5: the same slot quotas become the All-Pro ballot. The article reports the stored teams. |
| S7 | `newsroom/award_winners.py` (spec §7.6c) parses six winners from the Week 22 awards article; `engines/coty_engine.py` scores Coach of the Year; no stats-based MVP/OPOY/DPOY/OROY/DROY race exists. | `award_candidates.py` builds the candidate scores (COTY reuses `coty_scores`). Ballots decide; the article reports. |
| S8 | `newsroom/config/agent_personas.json` holds 40 writer archetypes, including fan, meme, aggregator and stats accounts. | The panel is a fixed list of about 20 national-writer archetypes (`VOTER_ARCHETYPES`), not the fan or joke accounts. |
| S9 | Latest reserved migration date is `2027_02_01` (agent loops). | This plan takes `2027_02_02`. |

## 1. Scope

**In:**
- Owner profiles (D1) and their four effects (D2): patience, hiring, spending, meddling.
- An inbox `OWNER` kind for JR's teams.
- 32 generated scouting directors and their bias and voice hooks (D3).
- Award candidates, writer ballots, tallies and storage for the six major awards (D4) and the All-Pro teams (D5).
- The new traits, the migration and the switchboard features.

**Out (non-goals):**
- Owners firing or overruling JR's personas: never; on JR's teams the owner only talks (§8 Q1).
- Owner sales and new owners.
- Regional scouts (D3).
- Changing pricing, valuation, legality or the league-wide firing counts.
- Model-written ballots (D4).

## 2. Design

### 2.1 Traits (`newsroom/config/persona_traits.json`)

New `Ownership` category, role `owner` only:
- `MEDDLER` ✕ `HANDS_OFF`;
- `DEEP_POCKETS` ✕ `BUDGET_CONSCIOUS`;
- `HEADLINE_HUNTER` ✕ `PATIENT_BUILDER`.

New `Scouting` category, role `scout` only:
- `TRAITS_CHASER`, `TAPE_GRINDER`, `CHARACTER_HAWK`;
- `FLOOR_SEEKER` ✕ `CEILING_GAMBLER`.

Each has prompt text and empty effects; the policies below own the numbers. An owner may hold up to three from Ownership, as an exception to the two-per-category rule, because the three pairs are separate axes (§8 Q2).

### 2.2 Owner profiles (`personality/owner_profiles.py`, `newsroom/config/owner_personas/`)

`owner-profile-v1` is the coach schema (`REAL_COACH_PROFILES_PLAN` §2.1) with these differences:
- `role: "OWNER"`, `key: "owner:<TEAM>"`, `seed_ref: {table: "OWNERS_DATA", team}`;
- traits are drawn from all categories eligible for `owner`;
- views use the same tags;
- ties may name his GM or head coach.

`OWNERS_DATA` is a new list in `personality/owner_profiles.py`: 32 rows of team, owner name and start year, as of 2025. The research agents fill and cite it, and the owner's name is checked by JR on review.

The module reuses `coach_profiles.validate_profile_file` with `kind='owner'`. The coach plan's review tool gains `--kind owner`.

Lifecycle entries use `role 'owner'`, `birth_key 'curated-owner:<TEAM>:2026'` and alias `db-owner` / `team_owners.id`.

### 2.3 Owner policy (`engines/owner_policy.py`, pure; all constants are review-only estimates)

```python
def patience(owner_view) -> dict        # {hot_seat_line_scale: 0.85..1.15, min_tenure_delta: -1..+1}
def hiring_key(owner_view, candidate) -> float   # added to coaching_carousel.pick's key, |x| <= 0.15
def appeal_term(owner_view) -> float    # added to opening_appeal, |x| <= 0.05
def spending(owner_view) -> dict        # {cap_buffer_share: 0.0..0.05, offers_per_wave: 3..6, need_premium: 0.05..0.15}
def meddle_options(move, owner_view) -> list[Option]   # APPROVE | VETO | PUSH, through agent_loops.base
```

**Patience:**
- `PATIENT_BUILDER` raises the hot-seat line by 0.10 and adds a season of tenure;
- `HEADLINE_HUNTER` lowers the line by 0.10;
- temperament moves the line by ±0.01 a point from 5, and drive by ±0.01 the other way;
- the result is clamped.

It applies to his head coach (`coach_hot_seat`) and his GM (`gm_rollover`), never on JR's teams.

**Hiring:**
- `HEADLINE_HUNTER` adds 0.15 for a former head coach or a retread, and openness +0.01 a point for a first-time coordinator;
- `PATIENT_BUILDER` adds 0.10 for a coordinator under 45;
- `MEDDLER` costs 0.05 of appeal.

**Spending:**
- `DEEP_POCKETS` sets buffer 0.0, 6 offers per wave and premium 0.15;
- `BUDGET_CONSCIOUS` sets buffer 0.05, 3 offers and premium 0.05;
- otherwise today's constants apply.

Legality and the cap still bind.

**Meddling:**
- **Scope:** CPU teams only, for big moves: a trade sending a top-10 player at his position, a top-50 free-agent signing, a head-coach hire.
- **Policy:** `p_veto` = 0.05, plus 0.20 for `MEDDLER`, minus 0.04 for `HANDS_OFF`, plus 0.05 if the player is a fan favourite (a top-20 `player_popularity_engine` score). `PUSH` is a signing or hire he wants above the GM's pick, and only `HEADLINE_HUNTER` + `MEDDLER` owners push.
- **Effects:** a veto drops the move from this write; a push swaps the signing or hire for the owner's legal alternative.
- **Seen cases:** a veto or push is a "seen" decision, so the model writes the line, and it is recorded in `owner_decisions` and visible to the newsroom.

**JR's teams:** the owner never overrules or fires. The inbox `OWNER` kind (owner → JR's GM) sends:
- a season-start expectation, from his traits and the team's direction;
- a pressure note after three straight losses or a losing season;
- a reaction to a big move.

JR can reply, and the exchange is a relationships `meaningful_interaction`.

### 2.4 Scouting directors (`engines/scout_directors.py`)

```python
def ensure_directors(cur, season, *, lifecycle) -> list[dict]   # one per team; name from staff_names; generated role 'scout' through the lifecycle apply
def board_bias(director_view, prospect) -> float                # |x| <= 0.5 grade points, inside scouting's noise
def voice(director_view) -> dict                                # ScoutVoice fields for the scouting plan's reports
```

The bias works by trait:
- `TRAITS_CHASER` raises athletic outliers;
- `TAPE_GRINDER` raises production;
- `CHARACTER_HAWK` lowers prospects with negative interview flags (from the scouting plan);
- `FLOOR_SEEKER` and `CEILING_GAMBLER` shift high-variance prospects.

The bias feeds `regional_bias()` per the scouting plan's hook and never changes a prospect's true ratings. Directors are kept across seasons; when a GM is fired, his director stays (§8 Q4).

### 2.5 Media voters (`engines/award_candidates.py`, `newsroom/award_ballots.py`, `newsroom/award_winners.py`)

```python
AWARDS = ('MVP','OPOY','DPOY','OROY','DROY','COTY'); POINTS = (10, 5, 3, 2, 1)
VOTER_ARCHETYPES = (...20 national writers...)
def candidates(cur, season, award) -> list[dict]       # {id, metrics: {...}, base_score}
def ballot(voter, cands, award, seed) -> list[int]     # top 5 ids
def tally(ballots) -> dict                             # {award: [{id, points, first_place}]}
def store_results(cur, season, tallies) -> None        # awards rows (winners) + award_ballots rows
def store_all_pro(cur, season, teams) -> None          # 62 awards rows (First/Second Team All-Pro) + award_ballots rows (award 'ALL_PRO:<slot>')
```

**Metrics:**
- **MVP and OPOY:** a blend of position-adjusted production z-scores, team wins and efficiency (EPA-like proxies from existing stats).
- **DPOY:** sacks, interceptions, tackles for loss and passes defended.
- **OROY and DROY:** the same metrics, rookies only.
- **COTY:** `coty_scores`.

**Each voter's lean on the metric weights:**
- `ANALYTICS_BELIEVER` up-weights efficiency;
- `OLD_SCHOOL` up-weights wins and volume;
- `HISTORIAN` adds a bonus for record pace;
- a view tagged `AWARDS` adds its own lean.

Seeded noise (`sha256(['ballot-v1', season, voter, award])`), then the top 5.

**Results:** the most points win, and ties go to more first-place votes. The Week 22 awards article gets the stored winners and tallies as facts and must name them. `award_winners` validation checks the article against the stored results instead of parsing winners from it.

**All-Pro (D5):**
- **Ballot:** each voter fills every First-Team slot in `all_pro.py`'s quotas (QB, RB, FB, three WRs, TE, AP, five OL, the defense and the special teams), choosing from that position's candidates with the same metric leans and seeded noise.
- **Teams:** per slot, the players with the most votes up to the quota make the First Team, and the next ones up to the quota make the Second Team. A player is placed once, at his best-voted slot. Ties go to the higher candidate base score.
- **Eligibility:** the minimum games rule applies.
- **Article:** the Week 19 All-Pro article gets both teams and their vote counts as facts and must name them, and `all_pro.py` checks the article against the stored teams.

```python
def all_pro_ballot(voter, cands_by_slot, seed) -> dict   # {slot: [player ids up to the quota]}
def all_pro_tally(ballots) -> dict                         # {slot: [{id, votes}]} -> first and second teams
```

### 2.6 Tables (`2027_02_02_owners_scouts_voters.sql`, mirrored in `db/init_db.sql`)

```sql
CREATE TABLE team_owners (id SERIAL PRIMARY KEY, team_id INT NOT NULL REFERENCES teams(id), owner_name TEXT NOT NULL,
  profile_key TEXT NOT NULL, start_season INT NOT NULL, end_season INT, UNIQUE (team_id, start_season));
CREATE TABLE owner_decisions (id BIGSERIAL PRIMARY KEY, season INT NOT NULL, tick SMALLINT NOT NULL, team_id INT NOT NULL REFERENCES teams(id),
  move_kind TEXT NOT NULL CHECK (move_kind IN ('TRADE','SIGNING','HIRE')), move_ref JSONB NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('APPROVE','VETO','PUSH')), alternative JSONB, loop_decision_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (season, tick, team_id, move_kind, move_ref));
CREATE TABLE scout_directors (id SERIAL PRIMARY KEY, team_id INT NOT NULL REFERENCES teams(id), name TEXT NOT NULL,
  persona_id VARCHAR(64), start_season INT NOT NULL, end_season INT, UNIQUE (team_id, start_season));
CREATE TABLE award_ballots (season INT NOT NULL, award TEXT NOT NULL, voter TEXT NOT NULL, ranking JSONB NOT NULL,
  ballot_version TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (season, award, voter));
```

The migration is transactional and refuses an incompatible table.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Owner effects swing firings or spending too far. | Tuning-franchise shadow season (activation plan): fires, tenure and cap-use distributions against today's. | Narrow the bounds; the league-wide fire counts already cap totals. |
| R2 | Meddling vetoes too many CPU trades. | Shadow report: veto rate. | Lower `p_veto`; vetoes are capped at one per team per season (estimate). |
| R3 | Ballots crown odd winners (a backup with great efficiency). | Synthetic seasons with known stat leaders; a minimum games and snaps rule per award. | Tune the metric blends; JR sees tallies. |
| R4 | Owner research is thinner than coach research for some owners. | `report` evidence counts. | Fewer moved sliders; JR reviews. |
| R5 | Many shared files with other plans (the hot seat, carousel and GM rollover; `transactions_engine`; the league office personality layer; the inbox; `scout_voice`; `award_winners`; the catalogue; `init_db.sql`). | Build order: this plan builds after the plans that own those files first. | Each unit re-reads its files after those land. |

**Spike first:** none. The award metric blends are tuned from synthetic seasons in U6's tests.

## 4. Tests (synthetic, no live DB)

- `tests/test_owner_profiles.py`:
  - owner files validate with the coach rules plus owner traits;
  - `OWNERS_DATA` has 32 teams;
  - lifecycle entries use role `owner`;
  - the review tool's `--kind owner` works.
- `tests/test_owner_policy.py`:
  - every policy stays in its bounds;
  - opposing traits move opposite ways;
  - meddling only on big CPU moves, never on JR's teams;
  - veto and push options through the loop base;
  - the veto cap.
- `tests/test_owner_wiring.py`:
  - hot seat and GM rollover use the scaled line, with unchanged league-wide counts and JR's GMs never fired;
  - `pick` and appeal terms;
  - spending knobs in `cpu_offers`;
  - the league office drops a vetoed move and swaps a pushed one;
  - the `OWNER` inbox kind triggers and records a relationships event;
  - shadow equals today's results.
- `tests/test_scout_directors.py`:
  - one director per team;
  - generated through a fake lifecycle;
  - bias bounds and trait directions;
  - true ratings untouched;
  - voice fields.
- `tests/test_award_ballots.py`:
  - candidate metrics on a synthetic season;
  - ballots deterministic and leaned by voter traits;
  - points and the tie rule;
  - the minimum games rule;
  - storage writes winners and ballots;
  - article validation checks the stored winners;
  - All-Pro: every voter fills every slot quota; first and second teams by votes; a player placed once; ties by base score; the minimum games rule; 62 rows stored; the All-Pro article checked against the stored teams.

Run with `pytest -m "not integration" tests/test_owner_profiles.py tests/test_owner_policy.py tests/test_owner_wiring.py tests/test_scout_directors.py tests/test_award_ballots.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Owner profile schema, traits and review

- **Owns:** `personality/owner_profiles.py` (new), `newsroom/config/owner_personas/_TEMPLATE.json` (new), `newsroom/config/persona_traits.json`, `tools/coach_profile_review.py`, `tests/test_owner_profiles.py` (new)
- **Reads:** this plan §2.1–§2.2; the coach plan §2; `personality/coach_profiles.py`
- **Depends on:** none in this plan. Cross-plan: the coach plan's U1 and the core extensions plan's U2 (catalogue) are merged.
- **Done when:** owner files validate; the new traits pass the catalogue checks; `report --kind owner` works on the template.
- **Verify:** `pytest -m "not integration" tests/test_owner_profiles.py tests/test_coach_profile_review.py -q` passes.

### U2: Owner research, AFC

- **Owns:** `newsroom/config/owner_personas/{BUF,MIA,NE,NYJ,BAL,CIN,CLE,PIT,HOU,IND,JAX,TEN,DEN,KC,LV,LAC}.json` (16 new files)
- **Reads:** §2.2; the coach plan §2.4 (the research method); the template
- **Depends on:** U1
- **Done when:** 16 cited drafts pass `validate --kind owner`.
- **Verify:** `python -m tools.coach_profile_review validate --kind owner --teams BUF,MIA,NE,NYJ,BAL,CIN,CLE,PIT,HOU,IND,JAX,TEN,DEN,KC,LV,LAC` exits 0.

### U3: Owner research, NFC

- **Owns:** `newsroom/config/owner_personas/{DAL,NYG,PHI,WAS,CHI,DET,GB,MIN,ATL,CAR,NO,TB,AZ,LAR,SF,SEA}.json` (16 new files)
- **Reads, Depends on, Done when:** as U2.
- **Verify:** as U2 with the NFC list.

### U4: Owner policy

- **Owns:** `engines/owner_policy.py` (new), `tests/test_owner_policy.py` (new)
- **Reads:** §2.3; `engines/agent_loops/base.py`; `engines/coach_hot_seat.py:20–60`; `engines/gm_rollover.py:20–60`; `engines/coaching_carousel.py:200–330`; `engines/transactions_engine.py:39–70`
- **Depends on:** none in this plan. Cross-plan: the agent loops plan's U1.
- **Done when:** §2.3 functions exist with their bounds.
- **Verify:** `pytest -m "not integration" tests/test_owner_policy.py -q` passes.

### U5: Owner wiring and the OWNER inbox kind

- **Owns:** `engines/coach_hot_seat.py`, `engines/gm_rollover.py`, `engines/coaching_carousel.py`, `engines/transactions_engine.py`, `pipeline/league_office_personality.py`, `engines/inbox/kinds/owner.py` (new), `tests/test_owner_wiring.py` (new)
- **Reads:** U4; §2.3; the activation plan §2.3; the inbox plan's kind registry
- **Depends on:** U4. Cross-plan: the activation, inbox, contract (U3, `transactions_engine`) and lifecycle (U4, `coaching_carousel`) plans are merged.
- **Done when:** patience, hiring, spending and meddling apply by switchboard mode, with shadow equal to today; the `OWNER` kind works.
- **Verify:** `pytest -m "not integration" tests/test_owner_wiring.py tests/test_coach_hot_seat.py tests/test_gm_rollover.py tests/test_transactions_engine.py -q` passes (the carousel has only `tests/test_coaching_carousel_db.py`, an integration file; U5 adds its pure `pick` and appeal cases to `tests/test_owner_wiring.py`).

### U6: Scouting directors, tables and features

- **Owns:** `engines/scout_directors.py` (new), `engines/scout_voice.py`, `db/migrations/2027_02_02_owners_scouts_voters.sql` (new), `db/init_db.sql`, `personality/features.py`, `tests/test_scout_directors.py` (new)
- **Reads:** §2.4, §2.6; the scouting plan's hooks; the lifecycle plan §2.5
- **Depends on:** none in this plan. Cross-plan: the scouting plan (`scout_voice.py`) and the activation plan (`features.py`) are merged.
- **Done when:** §2.4 and §2.6 work; the switchboard gains the six features.
- **Verify:** `pytest -m "not integration" tests/test_scout_directors.py tests/test_personality_features.py -q` passes.

### U7: Media voters (awards and All-Pro)

- **Owns:** `engines/award_candidates.py` (new), `newsroom/award_ballots.py` (new), `newsroom/award_winners.py`, `newsroom/all_pro.py`, `tests/test_award_ballots.py` (new)
- **Reads:** §2.5–§2.6 (the `award_ballots` table shape); `engines/coty_engine.py`; `newsroom/award_winners.py`; `newsroom/all_pro.py`; `newsroom/config/agent_personas.json`
- **Depends on:** none in this plan. It builds against §2.6's table shape with fake cursors; U6's migration creates it.
- **Done when:** §2.5 works for the six awards and the All-Pro teams, and both articles validate against the stored results.
- **Verify:** `pytest -m "not integration" tests/test_award_ballots.py -q` passes, and the existing award and All-Pro tests (`tests/test_award_winners.py`, `tests/test_all_pro.py`) still pass.

## 6. Waves

| Wave | Units | Needs merged first | Why |
|---|---|---|---|
| 1 | U1, U4, U7 | cross-plan files | Disjoint files. |
| 2 | U2, U3, U6 | U1 (U2, U3) | Research needs the validator; U6 is independent. |
| 3 | U5 | U4, U6 | Wiring needs the policy and the switchboard features. |

Building waits until every personality sub-plan is approved (JR, 2026-11-03). After the build:
- JR reviews the owner batches like the coach batches;
- the six features go through the activation plan's tuning-franchise shadow season and clearance.

**File ownership check:**

| File | Unit |
|---|---|
| `db/init_db.sql` | U6 |
| `db/migrations/2027_02_02_owners_scouts_voters.sql` | U6 |
| `engines/award_candidates.py` | U7 |
| `engines/coach_hot_seat.py` | U5 |
| `engines/coaching_carousel.py` | U5 |
| `engines/gm_rollover.py` | U5 |
| `engines/inbox/kinds/owner.py` | U5 |
| `engines/owner_policy.py` | U4 |
| `engines/scout_directors.py` | U6 |
| `engines/scout_voice.py` | U6 |
| `engines/transactions_engine.py` | U5 |
| `newsroom/all_pro.py` | U7 |
| `newsroom/award_ballots.py` | U7 |
| `newsroom/award_winners.py` | U7 |
| `newsroom/config/owner_personas/_TEMPLATE.json` | U1 |
| `newsroom/config/owner_personas/{AFC 16}.json` | U2 |
| `newsroom/config/owner_personas/{NFC 16}.json` | U3 |
| `newsroom/config/persona_traits.json` | U1 |
| `personality/features.py` | U6 |
| `personality/owner_profiles.py` | U1 |
| `pipeline/league_office_personality.py` | U5 |
| `tests/test_award_ballots.py` | U7 |
| `tests/test_owner_policy.py` | U4 |
| `tests/test_owner_profiles.py` | U1 |
| `tests/test_owner_wiring.py` | U5 |
| `tests/test_scout_directors.py` | U6 |
| `tools/coach_profile_review.py` | U1 |

No path appears twice. Existing tests that U5's verify runs (`test_coach_hot_seat.py`, `test_gm_rollover.py`, `test_transactions_engine.py`) are read, not changed.

## 7. Spec changes when built

- §1.1 Repository Layout: the new modules, `owner_personas/`, the migration, the tests, and this plan's entry.
- §2.11, §7.6b and §7.6c: award winners and All-Pro teams from ballots (candidates, voters, points and votes), with the articles reporting them.
- §2.17d: owner spending knobs.
- §2.18: scouting directors and the board bias.
- §2.19: owner patience and hiring terms; meddling on hires.
- §2.21a: meddling reviews in the league office.
- §3: the four new tables.
- §7.11: owner and scout traits and profiles.
- Other plans, at approval:
  - PERSONALITY_PLAN phase 6, §12 and the tracker point here;
  - the development plan §2.6 hiring/patience notes point here;
  - the scouting plan's neutral-scout notes point here;
  - the activation plan's `FEATURES` gains the six features;
  - the inbox plan lists the `OWNER` kind.

## 8. Decisions for JR

JR decided all four on 2026-11-03: Q1, Q2 and Q4 as recommended, and Q3 changed (D5).

| # | Question | Decision (JR, 2026-11-03) |
|---|---|---|
| Q1 | On your own teams, what can the owner do? | Talk only: a season expectation, pressure after losing, reactions to big moves, in your inbox. He never vetoes your moves or fires your personas. That keeps your control intact while giving the job a boss. |
| Q2 | Can an owner carry all three ownership pairs (for example Meddler, Deep Pockets and Headline Hunter)? | Yes. The pairs are separate axes, so the ownership category allows three traits for owners instead of the usual two-per-category limit. |
| Q3 | Should All-Pro teams also go to ballots? | **JR (2026-11-03): yes (D5).** The same panel votes on every First-Team slot; the vote counts make the First and Second Teams. |
| Q4 | Does a scouting director leave when his GM is fired? | No, he stays; a new GM can replace him at the next season's start (a later carousel rule). |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | build/osv-u1 | .claude/worktrees/osv-u1 | merged | 41fd01d |
| U4 | 1 | build/osv-u4 | .claude/worktrees/osv-u4 | merged | fbaf1b9 |
| U7 | 1 | build/osv-u7 | .claude/worktrees/osv-u7 | merged | 3fa9db7 |
| U2 | 2 | build/osv-u2 | .claude/worktrees/osv-u2 | merged (16 AFC drafts) | 9eb82f7 |
| U3 | 2 | build/osv-u3 | .claude/worktrees/osv-u3 | merged (16 NFC drafts) | 1940de3 |
| U6 | 2 | — | — | held (needs scouting U2's `scout_voice.py`) | — |
| U5 | 3 | build/osv-u5 | .claude/worktrees/osv-u5 | merged 2026-11-07 (JR: "Build U5 first"; inbox registry and switchboard on main, the six features already in `features.py`) | build/osv-u5 merged into main |
