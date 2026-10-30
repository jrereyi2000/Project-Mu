# Project Mu: League Office Plan

**Status:** superseded (2026-10-30) by [`V2_CORE_BUILD_PLAN.md`](../in_progress/V2_CORE_BUILD_PLAN.md), which absorbed its areas (JR, D2). Nothing was built from this plan directly; its open phases G and H and the coach writes are on that plan's §9 roadmap. Written 2026-10-28.
**Comes after:** the PC migration ([`PC_MIGRATION_PLAN.md`](../in_progress/PC_MIGRATION_PLAN.md)): the save reader, writer and loader (§4–§5 there). Runs alongside [`PROGRESSION_PLAN.md`](../in_progress/PROGRESSION_PLAN.md), which this plan leans on for player and coach personalities.
**Replaces on the PC:** decision D3 of [`TEAM_MANAGEMENT_PLAN.md`](../done/TEAM_MANAGEMENT_PLAN.md) (JR switches one team to user control per trade so the CPU keeps doing signings, re-signings and depth charts). Here losing the CPU's front office is the point.

**Goal:** Madden plays the games. Project Mu runs the league: every front-office decision for all 32 teams is made by Project Mu's engines and agents and written into the save. JR runs his own team from `Mu_Network` and goes into Madden to play.

Timings and counts below are estimates until checked on the PC.

---

## 1. Who decides what

| Area | Today (PS5) | On the PC (this plan) |
|---|---|---|
| Trades | GM agents propose and price (spec §2.17); JR approves and makes each one in Madden by switching user control | GM agents negotiate; the writer makes the trade (§4.1) |
| Draft | Team boards; JR enters Round 1 by hand from Draft Room recommendations; Madden auto-picks Rounds 2–7 | All 224 picks from team boards; JR picks only for his team (§4.2) |
| Coaching carousel | Project Mu fires and hires (spec §2.19); JR mirrors head-coach changes in Madden | The writer applies coach and coordinator changes (§4.3) |
| Scouting, progression | Project Mu / Madden | Project Mu (spec §2.18, `PROGRESSION_PLAN.md`) |
| Re-signings, extensions | Madden's CPU; read from League Transactions | Project Mu (§4.4) |
| Free agency, undrafted rookies | Madden's CPU | Project Mu (§4.5) |
| Cuts, waivers, practice squad | Madden's CPU | Project Mu (§4.6) |
| Depth charts | Madden auto-orders | Project Mu's coaches (§4.7) |
| Retirements | Madden | Project Mu (§4.8) |
| Injury recovery | Madden | Project Mu sets durations; Madden still decides who gets hurt (§4.9) |
| Awards, Hall of Fame | Captured from Madden; some Project Mu engines (COTY) | Media personas vote (§4.10) |
| Salary cap, rookie scale | Madden | Project Mu (§4.11) |
| Draft class | Madden-generated | CFB 27 bridge (migration §6), scouting on top |
| **Game simulation, in-game injuries, stats, schedule** | Madden | **Madden** |

---

## 2. Ground rules

* **Numbers stay deterministic** (migration plan V2.0). Engines decide values, offers, acceptances, picks, depth charts and outcomes, seeded and reproducible. Language models write dialogue, rationale and choose only among options the engine allows. This is the existing rule for GM agents ("the LLM never chooses or changes a deal").
* **Every write is read back.** After each write the loader reads the save and checks that it holds exactly what Project Mu decided (players' teams, contracts, depth charts, cap totals). A mismatch stops the run and is reported. The database and the game must never drift apart quietly.
* **Madden changes nothing between writes.** The weekly comparison from `PROGRESSION_PLAN.md` §3 covers rosters, contracts and depth charts too: a change Project Mu didn't make is reported, and Project Mu's version is written back or the cause is found.
* **JR's team plays by the same rules.** Cap, roster limits and acceptance rules apply to JR's offers exactly as to any GM's.
* **Edits happen with the franchise closed** (save and quit to the main menu, offline only), as every franchise tool requires.

---

## 3. Foundations

### 3.1 Roster moves in the writer

Most of §4 needs three moves on top of what `save_writer.mjs` does today:

| Move | What the writer does | From |
|---|---|---|
| **release** | Built (`release.mjs`): TeamIndex 32, ContractStatus FreeAgent, Contract null, removed from every team list and depth chart, appended to `League.FreeAgents`, roster counts lowered | — |
| **sign** | The reverse: team, ContractStatus Signed (or PracticeSquad), a new `PlayerContract` row, appended to `Roster` / `PracticeSquad`, removed from `League.FreeAgents`, roster counts raised, placed in the depth chart | `release.mjs`, `contract_edits.mjs` |
| **trade** | Move a signed player with his contract to another team: out of the old team's lists, into the new team's; contract kept; draft picks re-pointed (`record_edits.mjs` already moves `DraftPick.CurrentTeam`) | release + sign |

Plus the bookkeeping the release work left as PC checks: cap totals (`TEAM_SALARY`, `SalCapCapRoom`, …) recomputed by Project Mu or confirmed to be recomputed by Madden, dead money for releases and trades, and `PlayerContract` rows that releases leave unreferenced (reuse or trim them so the table doesn't fill).

### 3.2 Stopping Madden's CPU front office

Two ways, to choose on the PC:

* **A. All 32 teams user-controlled (preferred).** Madden makes no front-office decisions for user teams. Project Mu acts for all 32 between stages. Risk: Madden may refuse to advance until user teams have done things themselves (cut to 53, signed draft picks, a legal depth chart, responded to re-sign deadlines), and some stages may prompt for every user team. The writer must satisfy each requirement before JR advances. Also check: user teams' draft picks (does Madden auto-pick on the clock, or wait?), and whether 32 user teams slows advancing.
* **B. CPU teams with their automation off.** Franchise settings (CPU trades off, CPU auto-manage options, re-sign and free-agency settings as available in Madden 27) plus overwriting whatever the CPU still does. Simpler to run if A is blocked, but every CPU move has to be found and undone.

### 3.3 The stage loop

The save watcher (migration §5.2) knows the franchise's stage (`SeasonInfo.CurrentStage`, `CurrentWeekType`, `CurrentOffseasonStage`). For each stage:

```
[JR advances Madden to stage S, saves, quits to the menu]
          │
          ▼
[Watcher: read → load → engines → Project Mu decisions for stage S]
          │
          ▼
[JR's own decisions for stage S in Mu_Network (offers, picks, cuts)]
          │
          ▼
[Writer: moves for stage S → read back → check]
          │
          ▼
[Newsroom: stories of stage S] ──> [JR opens Madden and advances]
```

Today's `#debug` **Next offseason stage** job becomes this loop. In season the loop runs once a week with the weekly load (trades, waivers, depth charts, injury durations).

### 3.4 JR's team in `Mu_Network`

A front-office page for JR's team: roster and cap, depth chart, offers to free agents, re-signing talks with agents, trade talks with GMs (`engines/gm_negotiation.py` already does the money side), draft board and picks. JR's decisions go through the same writer and checks as every other team's.

---

## 4. Areas

### 4.1 Trades

* **Built:** proposals, value (`engines/trade_value.py`), legality, acceptance, budget and pacing, draft-day and round trades, JR's negotiation with a GM, rationales (spec §2.17, §3.3f).
* **Changes:** an executed proposal is written by the writer instead of JR's Madden steps (`steps_text` goes away). AI-to-AI trades get negotiation transcripts (V2.11), leaks through `gm_trade_sightings`, and failed talks become stories. Trade requests from players (§5.2) become a trigger alongside injuries.

### 4.2 Draft

* **Built:** consensus and team boards (`engines/scouting_boards.py`), the Draft Room, draft-day and round trades.
* **Changes:** Project Mu makes every pick, all seven rounds, from the owner's board, its needs and its GM persona. JR picks for his team in the Draft Room. Then the writer turns each drafted prospect into a signed rookie (ContractStatus Signed, team, rookie-scale contract from §4.11, `DraftPick` used, `DraftedPlayers`) and the undrafted into free agents for §4.5.
* **PC check:** whether to write all picks after Madden's draft stage (simplest: the draft runs with every team user-controlled and the picks are written afterwards) or pick by pick during it.

### 4.3 Coaching carousel

* **Built:** hot seat, firings, hiring cycle with poaching, coordinator changes (spec §2.19).
* **Changes:** the writer applies head-coach and coordinator changes to the save's coach tables. New coaches without a Madden counterpart are created there (the community's *M27 Coach Creator* is prior art, `MMC_MOD_SURVEY.md` §3). Coach traits from `PROGRESSION_PLAN.md` §7 (teacher, specialty, youth trust) become hiring factors for GMs.

### 4.4 Re-signings and extensions

* **Engine:** each expiring player gets a value to his own team (`V_T` in `trade_value.py`) and an asking price (market worth `W(ovr, group)` adjusted by age, dev trait and personality). The team decides keep / extend / let go from its direction, cap, needs and GM persona.
* **Agents:** a new persona type. Each player has an agent (one agent represents many players) who negotiates for him: asking price, years, guarantees, and how hard he pushes. Agents leak to insiders.
* **Players' side:** drive and `Focus` (from `PROGRESSION_PLAN.md` §6) decide money versus staying put; a player who wants to test the market refuses an extension; a contract-year holdout is possible for low-patience stars below market.
* **Timing:** extensions in season and in the offseason; re-signings at Madden's re-sign stage.

### 4.5 Free agency

* **Market:** each team's needs (spec §2.16), cap space, direction and GM persona produce offers; each free agent compares offers by money, guaranteed money, team quality, role (projected depth-chart spot) and location, weighted by his personality. Seeded rounds of offers and decisions, like real free agency's first days, then a slower market.
* **Undrafted rookies** after the draft: the same market at rookie-minimum money, decided mostly by role.
* **In season:** injury replacements from the free-agent pool.
* **Stories:** visits, mystery teams, a hometown discount, a star choosing a contender for less; JR's offers compete in the same market.

### 4.6 Cuts, waivers and practice squad

* Cut-down to 53 (or the roster-size mod's numbers) by each team's coach and GM from depth, value and cap savings (`pipeline/contracts.py` already computes cut savings and penalties).
* **Waivers:** claims in waiver order (worst record first), each team deciding from need and value; unclaimed players become free agents.
* **Practice squad:** up to Madden's fixed size (hard-coded; the sample save's `PracticeSquad` list has 12 slots, a PC check against the 16 the NFL allows), eligibility by years of experience; elevations in season.

### 4.7 Depth charts

* Each team's coaching staff sets the depth chart weekly: OVR, scheme fit, health, and the coach's youth-versus-veteran trust (`PROGRESSION_PLAN.md` §7), with position battles in camp for close calls.
* Depth charts decide snaps, which feed progression's playing-time term, so this is where coaches shape careers.
* **PC check:** whether Madden's in-game auto-substitution respects written depth charts, and whether its depth-chart auto-reorder setting must be off.

### 4.8 Retirements

* Each offseason: chance to retire from age, ratings decline, injuries, contract, team quality and personality (drive, love of the game); stars can announce a farewell season in advance.
* Replaces `player_retirements` coming from League Transactions; the season ledger (spec §3.3m) reads Project Mu's own decisions.
* Retirement Arbitrator (`MMC_MOD_SURVEY.md` §3) is a stopgap if Madden keeps retiring players before Project Mu's step runs.

### 4.9 Injuries

* Madden still decides who gets hurt in a game and the injury type.
* Project Mu decides how long: Madden's `MinDuration`/`MaxDuration` as the range, a seeded draw inside it adjusted by resilience (`PROGRESSION_PLAN.md` §6) and later team medical staff, and writes the duration back. Lasting effects of major injuries are in progression.
* IR decisions belong to the team (§4.6).

### 4.10 Awards and Hall of Fame

* **Voters:** media personas from the newsroom vote for MVP, OPOY, DPOY, rookies of the year, Comeback, All-Pro and the Hall of Fame, each from the data and their own biases (stat-heavy, narrative, winning). Ballots are stored and published, so writers can argue about them.
* COTY keeps its engine (spec §2.11), with a ballot added.
* The newsroom's awards slate (Editorial Matrix) reads the results.
* **Data rule:** Madden's own award screens may disagree with Project Mu's; Project Mu's are the league's record. Writing the winners into Madden's award tables is a PC check, not required.

### 4.11 Cap and rookie scale

* The cap for each new league year from a Project Mu rule (league revenue growth with some noise, seeded), written into the save (`salaryCapGenerator` in *Prime's Retro Franchise Tools* is prior art).
* The rookie scale by pick (used by §4.2), and minimums by experience.

---

## 5. Agents

The V2.0 rule holds for all of them: engines choose the numbers, models write the words.

| Actor | Decides (within engine limits) | Talks |
|---|---|---|
| **GMs** (built: `gm_profiles` traits) | trades, re-signings, free-agent offers, draft picks, cuts, waivers | rationale, negotiation (V2.11), inbox (V2.9) |
| **Coaches** (traits from `PROGRESSION_PLAN.md` §7) | depth charts, position battles, cuts with the GM | press conferences, player relationships |
| **Player agents** (new) | asking prices, how hard to push, leaks | negotiations with GMs and JR |
| **Players** (traits from `PROGRESSION_PLAN.md` §6) | free-agent choice, extension or test the market, holdouts, trade requests, retirement | Agent Wire, postgame quotes (V2.3) |
| **Owners** (new) | budgets, patience, firing the GM (today's hot seat rule as the owner's decision) | messages to JR (V2.9) |
| **Media** (built: newsroom personas) | award and Hall of Fame ballots | articles, takes |

### 5.1 Personas needed

Owners and agents are new persona types, seeded once with traits, like `gm_profiles`. Players and coaches get theirs from `PROGRESSION_PLAN.md` phases F and G.

### 5.2 Player requests

Players act on their situation: a starter who loses his job, a star on a losing team, a veteran stuck behind a rookie can ask for a trade or a release; a star below market can hold out. Requests become GM triggers (§4.1), inbox messages for JR, and stories.

---

## 6. Phases

| Phase | What | Needs |
|---|---|---|
| **A. PC checks** | §3.2 A or B (what Madden demands of user teams at each stage, draft behaviour with user teams), sign and trade written and accepted, cap totals and dead money, depth-chart and auto-sub behaviour, award tables | The PC, a test franchise |
| **B. Writer moves** | sign, trade, cap bookkeeping, `PlayerContract` row reuse; read-back checks | Phase A |
| **C. First PC season** | Trades through the writer; coaching changes through the writer; the whole draft by Project Mu. All three engines exist; this is wiring. CPU re-signing and free agency stay with Madden this season if the transactions engine isn't ready | B |
| **D. Transactions engine** | Re-signings, extensions, free agency, undrafted rookies, cuts, waivers, practice squad, rookie contracts, cap and rookie scale: one engine on shared value, cap and needs. Agents as personas. JR's front-office page in `Mu_Network` | B, `trade_value`, `PROGRESSION_PLAN.md` F (personalities) for player choices |
| **E. Depth charts** | Coach-set depth charts weekly | B, `PROGRESSION_PLAN.md` G (coach traits) |
| **F. Retirements, injuries** | §4.8, §4.9 | D |
| **G. Awards and Hall of Fame** | Media ballots | V2.8 league memory helps |
| **H. Requests and owners** | Holdouts, trade requests, owner personas and GM firing by owners | D, V2.9 inbox |

Each phase keeps a fallback: until a phase is built, Madden's CPU does that job (route B of §3.2 for just that area) and the loader records what it did, as today.

---

## 7. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| L1 | All 32 teams user-controlled (§3.2 A) or CPU with automation off (B)? | A, if Madden will advance with the writer meeting each stage's requirements |
| L2 | Which areas does Project Mu take in the first PC season? | Trades, coaching changes, the whole draft (phase C) |
| L3 | Does JR still play every Panthers game in Madden, and sim the rest? | Yes; the plan doesn't change who plays the games |
| L4 | Where JR makes his own team's decisions: `Mu_Network` only, or Madden's menus too? | `Mu_Network` only, so every move goes through the same rules and checks |
| L5 | Awards: Project Mu's ballots replace Madden's as the league's record? | Yes, from the first PC season's awards |
| L6 | How visible negotiations are: JR sees AI-to-AI talks as they happen, or only after (through leaks and stories)? | After, through leaks and stories; full transcripts in `#debug` |
| L7 | Agents: a small set of agencies each representing many players, or one agent per player? | About 20 agencies, so agents have reputations and rivalries |

---

## 8. Spec changes when built

The writer's sign and trade moves and the read-back check (spec §1 layout, the save reader section); the stage loop and the watcher's steps; new tables for negotiations, offers, agents, owners, ballots and depth charts in §3; the transactions engine and the award ballots in §2; `TEAM_MANAGEMENT_PLAN.md` D3 and the trade `steps_text` marked as PS5-era; the season ledger reading Project Mu's own transactions and retirements instead of League Transactions captures.
