# Project Mu: Personality and Agents Plan

**Status:** phases 1, 2, and 3 built (2026-10-28, spec §3.3f, §7.11); phase 4 feature-independent core built (2026-10-31, §5.2), evidence samples and feature policies remain for review; no production profiles seeded or effects activated; phases 5–6 not started. See [Panthers review sample](PERSONALITY_PANTHERS_SAMPLE.md) and [generated-player effect matrix and examples](PERSONALITY_GENERATED_EFFECTS_DESIGN.md) (design for review; no activation).
**Expands:** V2.13 of [`PC_MIGRATION_PLAN.md`](PC_MIGRATION_PLAN.md). Feeds [`PROGRESSION_PLAN.md`](PROGRESSION_PLAN.md) §6–§7 and [`LEAGUE_OFFICE_PLAN.md`](../done/LEAGUE_OFFICE_PLAN.md) (free agency, retirements).
**Writer personas** can start before the PC (§9, phase 1).

One personality system for everyone in the universe (writers, GMs, players, coaches, owners, scouts), modelled on The Sims: a few sliders with a budget, a handful of traits drawn from a catalogue, and a mood that comes from the week's data. Traits are nudges that mix, so no single belief takes over a character.

---

## 1. What an agent is

The language model has no memory or personality of its own; every call starts from nothing. An agent is:

1. **A record** in the database: who they are, what they believe, what they have done, who they know.
2. **A prompt built fresh each time** from that record plus the current data.
3. **A loop**: a trigger fires → the record and the relevant data are loaded → the model says or chooses something → the output is checked → what happened is written back to the record.

The personality lives in the database. The model is the voice that reads it.

**Agentic** means the loop also has **tools** and a **trigger**. The model is still prompted, by the program rather than a person: a scheduler or an event wakes the agent ("Week 7 started", "an offer arrived"), and it chooses its own steps from the tools it is given (look up a roster, price a deal, message a GM) until it is done or hits a step limit. The newsroom already works this way: each writer gets an assignment and the writer tools (`newsroom/db_tools.py`) and decides what to look up across dozens of calls. GMs don't yet; the trade engine decides everything and the model writes only the rationale.

**Ground rule (V2.0):** engines decide every number. Traits act on numbers only through engine formulas; the model chooses among options the engines allow and does the talking.

---

## 2. What makes agents feel alive

In rough order of effect:

1. **Memory with consequences, especially between people.** A GM who was fleeced by a team is colder with it two seasons later; a reporter JR brushed off writes a sharper piece. History with specific people matters more than a long biography.
2. **A specific situation.** Contract year, hot seat, losing streak, a feud with a coordinator. Project Mu's data holds most of this already; it has to reach the prompt.
3. **Wants and fears that pull against each other.** They give the agent choices to make, and stay in the background rather than being said aloud.
4. **Consistency with room to change.** The same voice and grudges over seasons, shifted by big events.
5. **Agents reacting to each other.** A player's quote leads to a writer's take, which leads to the GM's answer.

---

## 3. The problem with today's writer personas

Writer personas in `newsroom/config/agent_personas.json` are written as orders, not as character. Barnwell's says he "constantly" looks for regression to the mean, must "End with a 'Regression Warning' section", and must use a fixed lexicon. The model obeys, so every Barnwell piece is a regression piece. 10 of the 38 archetype personas and 3 of the 8 article personas use "always / constantly / never / must / End with" wording of this kind.

A belief written as a rule for every piece becomes a catchphrase. Adding a want and a fear the same way would add two more. The fix is the structure below: broad traits as the base, many views each with when it applies, signature moves as a pool, and memory that pushes back on repetition.

---

## 4. The personality model

### 4.1 Sliders (everyone; numbers with a budget)

Six sliders from 0 to 10. The total must stay between 24 and 36 (of 60), and seeding deals exactly 30, so nobody is high on everything. A high setting in one place still costs something somewhere else, which is what makes characters distinct (The Sims 1–2: 25 points over five sliders). Why a band and not an exact 30 (JR, 2026-11-02): an exact total makes some characters impossible and ties unrelated sliders together, because one slider can't move unless another moves the other way.

| Slider | 0 ↔ 10 |
|---|---|
| **Temperament** | calm ↔ volatile |
| **Sociability** | private ↔ media-friendly |
| **Confidence** | doubting ↔ brash |
| **Openness** | traditional ↔ experimental |
| **Warmth** | cold ↔ loyal |
| **Drive** | content ↔ obsessive |

### 4.2 Traits (three to five each, from a catalogue)

Traits come in categories. Each agent gets at most two from one category, some pairs can't go together, and each role draws with its own odds (writers lean toward football-mind and media traits, players toward ambition and social traits, coaches toward teaching and scheme).

| Category | Examples | Conflicts |
|---|---|---|
| **Football mind** | Film Junkie, Analytics Believer, Old School, Scheme Nerd, Historian | Analytics Believer ✕ Old School |
| **Social** | Loyal, Grudge Holder, Mentor, Diva, Peacemaker, Loner | Peacemaker ✕ Grudge Holder; Mentor ✕ Loner |
| **Ambition** | Ring Chaser, Money First, Legacy Builder, Hometown Guy, Spotlight Seeker | Ring Chaser ✕ Money First |
| **Media** | Quotable, Cliché Machine, Troll, Guarded, Hot Taker | Guarded ✕ Quotable |
| **Work** | Workout Fanatic, Film Room Rat, Coasting, Perfectionist | Workout Fanatic ✕ Coasting |
| **Teaching** (coaches, scouts) | Teacher, QB Whisperer, Disciplinarian, Players' Coach, Youth Developer | Disciplinarian ✕ Players' Coach |
| **Quirks** | Superstitious, Prankster, Foodie, Car Guy, Family Man | — |

Each catalogue entry defines:
* **Roles** that can draw it, and the odds per role.
* **Engine effects** (optional): which formula term it multiplies and by how much (§6).
* **Prompt text**: a tendency phrased as "tends to …", plus **when it applies** (subjects, situations).
* **Conflicts** and **category**.

The catalogue lives in one data file (`newsroom/config/persona_traits.json`, started in phase 1 with the writer traits and the plan's examples; `data/` is git-ignored) so it can grow without code changes. Player work ethic, coachability and drive (progression plan §6) and coach teacher and specialty (§7) are entries in it, not separate systems.

### 4.3 Views (writers, GMs, coaches; many, each with when it applies)

Eight to twelve opinions about football, each with a trigger: "rookie QBs need two years (when a young QB struggles)", "records that outrun point differential don't last (when record and differential disagree by 2+ wins)". A prompt includes only the views whose trigger matches the piece's subject or the data. Regression shows up when the data supports it, not in every column.

### 4.4 Wants and fears

One want and one fear, kept in the background as motivation ("wants front offices to respect his work; fears being remembered as a hype man"). Prompts state them as context for the model, never as something to say.

### 4.5 Signature moves (a pool, not a requirement)

Two to five habits per writer or speaker (a historical comparison, a closing one-liner, a section title). The prompt offers them as optional, and the takes log tracks when each was last used, so a signature appears now and then.

### 4.6 Mood (from this week's data)

Worked out at prompt time, never stored as identity:

| Situation (from data) | Mood |
|---|---|
| Contract year, hot seat | Pressure |
| Losing streak, missed prediction, benched | Frustrated / defensive |
| Win streak, award, correct call | Confident |
| New team, new job | Eager / uncertain |
| Injury | Anxious / determined (by traits) |

When more than one situation applies, they are read in this order: own role or health first, then contract or job security, then team results. The first match is the main mood and the next one is an undertone. Example: a benched starter in a contract year on a 10–0 team is Frustrated, with an undertone of Pressure.

Mood shifts tone for the week without changing who the agent is.

### 4.7 Relationships

One-way rows (A → B), as `persona_relationships` stores them (`persona_type/persona_id → target_type/target_id`). A's view of B and B's view of A are separate rows, so one-sided grudges and unreturned loyalty are allowed. Each row holds history: `trust` (−1 to 1), `last_interaction` (what and when), a short note, and tags (`RIVAL`, `MENTOR`, `FEUD`, `FORMER_TEAMMATE`). Updated after interactions the engines record (trades, negotiations, articles naming someone, press conferences, firings). Prompts include the relationship only when that person is involved.

### 4.8 Memory

The newsroom's model, extended to every agent: an episodic log (takes, quotes, deals, decisions), a season summary rewritten every few entries, and, once V2.8 exists, search over the full log so old history can come back. Prompts see only memories from before the moment being written, as `newsroom/agent_memory.py` does today.

---

## 5. How a persona is formed

1. **Draw the numbers.** Sliders and traits are dealt from a seed (`sha256("persona:" + role + ":" + id)`) and spread across the league so the mix is realistic: a few divas, some mentors, most in between. Stratified like GM traits in `engines/gm_personas.py`; checked so no team or desk gets an odd cluster.
2. **Start from what is known.** Madden's `Player.Focus` (`LoveOfTheGame`, `Winning`, `Financial`, `PersonalAccolades`) sets ambition odds for players. Real-world writers and coaches get traits that fit their known public style (hallucination policy: invented narrative, never contradicting data). JR can adjust any persona before it is first used.
3. **Write the identity once.** The model writes name (where needed), a short bio and voice notes from the numbers. Validated and stored. Bio facts are fixed and never regenerated. Voice notes are rewritten after any logged trait or slider change, so the voice follows the character.
4. **Let it grow.** Memory and relationships fill as the agent acts; summaries are rewritten as entries pile up.

### 5.1 Approved rollout direction (JR, 2026-10-30)

The current plan is to control the simulation from its starting season through 2034, rather than first importing a league already in 2034. Prioritize consequential personalities for generated players: seed each generated draft class when prospects enter the universe, before personality-dependent decisions, and preserve identity, traits and logged evolution throughout the run. Any generated players already present at the starting checkpoint need one-time initialization; do not redraw returning players each season. Confirm the actual starting season/save before binding identities.

Personality is intended to influence contracts, development, trades and career decisions through bounded formulas in the owning engines, without duplicating ratings or scheme fit or overriding legality. Dialogue and visible badges are optional presentation, not the reason to build the system. Existing catalogue effects or prompt support do not establish that these integrations are implemented or approved coefficients.

Stop expanding manual research of today's real-player population. The Panthers sample is a limited evidence-method reference, not a league-wide seeding template or a prerequisite to generated-player work. For real people participating during the run, use conservative supported observations; unsupported private motivations remain neutral. Generated people can receive plausible seeded motivations. Both populations subsequently evolve from logged events in the simulated universe. Initialize participating coaches and decision-makers as well, with separate treatment of observed football tendencies and uncertain private motives.

Design deliverable drafted: [generated-player effect matrix and examples](PERSONALITY_GENERATED_EFFECTS_DESIGN.md), including deterministic identity/seed handling, persistence across seasons, bounded effects, event evolution and offline validation. It recommends safe identity/resolution first, contract preferences next, then coordination with development/coaching owners and later requests/retirement. Proposed trial coefficients remain for review; no engine changes are implemented. Offline demonstration now available: `python -m tools.personality_contract_demo` produces seven synthetic situations and 525 parameter combinations; see the design document for bounds, threshold and noise findings. This direction does not authorize production seeding or activate engine effects. JR refinement: strong ring chasing is situational (established career, achieved financial security and usable contender role), not universal or inferred from elite ratings. Earned loyalty reflects simulated history and treatment, separately from a smaller verified-hometown preference; no relationship-count bonuses. Equal-strength loyalty examples and trial tiers are in the generated-player design. The existing six sliders and trait catalogue remain the foundation.

### 5.2 Feature-independent core (built 2026-10-31)

`personality/README.md` documents the implemented API and ownership boundaries. The core supplies stable universe identity, explicit one-time initialization, verified aliases, versioned catalogue/profile snapshots, provenance-scoped feature views and audited one-change-per-season evolution candidates. Persistence reuses existing slider/trait/history tables with sidecars in `2027_01_16_personality_core.sql`; no production migration or seeding has run. Existing writer/GM paths are unchanged. Generated initialization still seeds four traits and a total of 30; real/unknown defaults are neutral, with sparse evidence permissions for reviewed real people. Missing reads never generate profiles and storage failures are explicit.

Contracts, progression, relationships/mood, trade requests and retirement remain separate feature policies. The core checks and records a candidate; it does not decide that a title, payday or benching should cause one. Verified pipeline identity binding, classification/adoption workflows and feature activation remain integration work. Unknown identities must not be guessed as generated. The slider band (2026-11-02) moved both the Python validator and the SQL constraint (`2027_01_17_persona_slider_band.sql`) from an exact 30 to 24–36, so single-slider evolution is possible once that migration is applied. Core trait evolution already works, and future slider evolution adopts the active validated band without reseeding.

Parallel work can proceed on cap history/career earnings and contract pricing independently; relationship/reaction policy can now target the core identity/event interface; progression/depth-chart owners can develop pure consumers of feature views. Trade-request/retirement baseline design is independent, with execution integration later. Shared core schemas and public API changes require coordination; feature policies must not edit core state or introduce duplicate personality schemas.

### Change over time

Like Sims aspirations: at most once a season, a major event can add or drop a trait or move a slider a step (seeded). A title can turn Ring Chaser into Legacy Builder; a firing can raise Temperament; a mentor's retirement can make a player a Mentor. A slider move that keeps the total inside the 24–36 band needs no paired move on another slider. Each change is logged with its cause, so writers can tell the story.

---

## 6. Where personalities act

**Design qualification (2026-10-30):** the generated-player design reviews the older examples below. Financial preference must not imply low effort after payment, and public style must not imply confidence or ability. Do not activate those older causal shortcuts; use the scoped effect matrix and owning-engine review in PERSONALITY_GENERATED_EFFECTS_DESIGN.md. Engine integrations remain inactive.

| Where | How |
|---|---|
| **Engines** | Traits multiply formula terms within caps. Progression (full table in `PROGRESSION_PLAN.md` §6.1, coach side §7): Workout Fanatic slows physical wear, Coasting speeds it and lowers ceiling pull, Film Room Rat grows mental ratings and the mental ceiling, Perfectionist grows technical ratings, Mentor speeds young teammates' mental growth (Loner gives none), Legacy Builder and Ring Chaser resist peak upkeep while Money First after a big deal doesn't, the Confidence and Temperament sliders set form swings and the odds of Madden behaviour flags like Happy Feet, coachability scales the coaching term; coaches' Disciplinarian, Players' Coach, QB Whisperer, Youth Developer, Old School (workload) and Scheme Nerd and the openness slider act on the same terms. These go into each catalogue entry's `effects` (empty today in `persona_traits.json`). Free agency: Ring Chaser takes less from a contender, Hometown Guy favours his old team, Money First follows the top offer. Money First and Ring Chaser are weight shifts with a written bound (coefficient below 0.20 for Money First and below 0.45 for Ring Chaser, design §2), so they lean a choice but never reverse it. Strong ring chasing needs the career-earnings gate, counted in cap shares: `engines/career_earnings.py` `is_secure` is True at 0.15 of a cap earned, and unknown history leaves it inactive. Trades: Grudge Holder adds a discount toward a team that wronged him. Retirements: Legacy Builder chases records, Coasting retires earlier |
| **Prompts** | Sliders as a short description; only traits and views whose trigger matches; mood; relationship with whoever is involved; signature moves as options; recent angles to avoid |
| **Agent loops** | Traits pick which tools an agent reaches for first and how long it keeps going: an aggressive GM makes more calls before the deadline, a Guarded player declines interviews |
| **Site** | Traits shown on player, coach and writer pages once seeded (decision D3); hidden values (ceilings, reserves) stay hidden |

Caps keep personality a colouring: no trait moves a number further than the limit the owning engine sets (progression: about ±1.5 OVR a season).

---

## 7. Prompt assembly

```
[identity]       name, role, bio                            (fixed)
[voice notes]    how this person talks                      (rewritten after a logged trait or slider change)
[personality]    sliders as one sentence                    (fixed, slow change)
[active traits]  traits whose "when" matches, as tendencies
[active views]   views whose trigger matches the subject/data
[mood]           from this week's data
[stakes]         contract, job security, team direction     (from data)
[relationship]   with each person involved
[memory]         recent entries + summary (+ search hits, V2.8)
[avoid]          last N angles and signatures used
[task + tools]
```

Every line is chosen by code from stored data. Nothing in it says "always".

---

## 8. Agentic loops

For agents that act rather than only write (GMs, players, coaches, owners). A player's own AI loop does his negotiating; no hired agent sits between him and the GM:

* **Triggers:** schedule (each week, each offseason stage) and events (injury to a starter, offer received, benched, fired).
* **Tools per role, kept narrow.** GM: roster, needs, cap, `price_deal`, `message_gm`, `make_offer`, inbox. Player (negotiating): market value, offers, `counter`, `leak`. Coach: depth chart, practice reports, `set_depth`. No web access; the league is the world.
* **Guardrails:** step and budget limits; every action that changes the league goes through the owning engine's legality and acceptance checks; outputs validated against the data.
* **Record, don't replay:** model choices aren't reproducible, so every action is stored and the stored record is the truth.
* **Cost:** many calls per loop; runs on the local model (migration plan §7) where it passes the §7.4 quality gate.

---

## 9. Phases

| Phase | What | Needs |
|---|---|---|
| **1. Writer personas, now** (built) | `agent_personas.json` rewritten from rules into sliders, traits, views with triggers, signature pools and a want/fear, with the long-form voices folded in as `formats.ARTICLE`; no "always / must / end with". `newsroom/personas.py` picks active views from the piece's tags and the subject team's results; recent headlines and recently used signatures form the avoid list (from `news_feed`, no new table). The 13 writer slate-selection directives rewritten as leanings with alternatives. Still to do: the side-by-side test against current output on a week's slate (costs model calls) | Nothing new |
| **2. Catalogue and tables** (built 2026-10-28) | Full 50-trait catalogue in `persona_traits.json` with categories, role odds, prompt tendencies, and optional effects; tables for persona sliders (`persona_sliders`), traits (`persona_traits`), views (`persona_views`), relationships (`persona_relationships`), and trait history (`persona_trait_history`); deterministic seeding and budget validation; review endpoints in `#debug` (`GET/POST /api/debug/personas`) | — |
| **3. GMs** (built 2026-10-28) | Sliders and traits on top of `gm_profiles` (and `engines/gm_personas.py`), GM prompt formatting and negotiation dialogue reflection in `engines/gm_negotiation.py` without altering deterministic trade values | 2 |
| **4. Players and coaches** | Generated draft classes first during the controlled run to 2034 (§5.1), plus generated people already at the starting checkpoint; persistent event evolution; participating coaches and decision-makers; limited evidence-backed real-person initialization; bounded contract, development, trade and career effects through owning engines (not yet activated) | 2, PC |
| **5. Agentic GMs** | Trigger + tools + loop for GM talks and trades (V2.11), inside the trade engine's checks | 3, league office save moves |
| **6. Owners, scouts, media voters** | Inbox (V2.9), free agency (league office plan), scouting (V2.10), award ballots | 2–5 |

---

## 10. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| D1 | Slider budget and count | Six sliders, 0–10 each, total 24–36, seeded at 30 (JR, 2026-11-02) |
| D2 | Traits per agent | 3–5 (writers 4, stars 5, depth players 3) |
| D3 | Are traits visible on the site? | Yes for traits and sliders; mood shown as a tag; engine effects never shown as numbers |
| D4 | Can JR edit personas? | Yes, before first use; afterwards only through events |
| D5 | Real-world writers and coaches | Traits that fit their public style; reviewed by JR |
| D6 | How often traits change | At most one change per agent per season, after a logged event |
| D7 | Start phase 1 before the PC? | Yes; it fixes the repetitive writer columns now |

---

## 11. Spec changes when built

Persona tables (sliders, traits, views, relationships, trait history) in spec §3; prompt assembly in the newsroom section (§7) and `agent_personas.json` structure; engine trait effects in each engine's section; writer tools gaining relationships and traits.

---

## 12. Deferred

Agreed on 2026-10-31 and left for later work:

* **Lowball zone in re-sign and extension talks.** An offer far below his value costs trust with the GM and may leak to the press.
* **Cap casualty after a discount.** A player who took less and is then cut for cap reasons loses trust toward that GM (one-way). Any trait change waits for the yearly review.
* **Elite positional-rank demand.** A top player asks to be paid among the top few at his position.
* **Two or three views for players who speak.** Players who give interviews get views like writers do.
* **Owner and scout categories (phase 6).** Owners: Meddler / Hands-Off, Deep Pockets / Budget Conscious, Headline Hunter / Patient Builder. Scouts: Traits Chaser, Tape Grinder, Character Hawk, Floor Seeker / Ceiling Gambler.

---

## 13. Considered and rejected (2026-10-31)

| Idea | Why not |
|---|---|
| "Bag Drop": Money First plus low Drive gains Coasting | Wanting money must not mean working less after payday (§6 design qualification). |
| Warmth as a switching cost | Warmth is manner, not attachment. Staying comes from earned attachment (LOYAL tiers). |
| Confidence changes guarantee demands | Public style must not set pay terms; the engine already scores guarantees. |
| Scheme Nerd contract multiplier | Scheme fit is already a utility term; a multiplier counts it twice. |
| Sliders on 0–100 | False precision. Prompts and yearly steps work in whole points on 0–10. |
| Separate hired agents for players | One more actor with no new decisions. The player's own loop negotiates. |
