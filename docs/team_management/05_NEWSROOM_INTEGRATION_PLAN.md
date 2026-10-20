# Step 5: Newsroom integration (sub-plan)

Written 2026-10-20 for [`TEAM_MANAGEMENT_PLAN.md`](../TEAM_MANAGEMENT_PLAN.md) step 5. This plan reads step 1's contract tables (§2.1) and step 2's `transactions` (§2.2) as they are. It **adds no tables and changes no schema**. Steps 3 (§2.4 `gm_trade_proposals`) and 4 (§2.5 risers and fallers) are used only in the phases marked as depending on them (Phases 8 and 9). Every other phase can ship now.

**Status (2026-10-20):** Phases 1–7 are built and on main (see PROJECT_SPEC §7). Phase 6 is 6a only (JR dropped 6b). Projected yearly hits are shown labelled `est.` (§6 Q2). Notability was retuned by JR (2026-10-20): only players rated 85+ are notable; §2.3's other conditions now only describe and rank a move. On the 2036 Weeks 1–4 data that gives 17 of 181. Seams for Phase 8: `roster_moves.notability` tags `TRADED` rows with a "trade" reason (an 85+ player's trade is notable; whether every trade should be is a Phase 8 call) and `move_line` formats them; `txn_visible` treats a NULL `capture_id` as loaded in time; `get_league_transactions`'s note says trades appear once completed; the R6/R7 trade-package lift and `get_trade_rumors` are not built.

**Delivers:**
- Three writer tools in `newsroom/db_tools.py`: `get_team_cap_sheet`, `get_player_contract` and `get_league_transactions`. They respect the article's week cutoff, and they label every number as scanned, from a signing row, or derived. Projected numbers are kept away from writers.
- Released players drop off team views in the writer tools.
- **Proposed** rule changes for JR: which "no contracts / signings / trades" rules lift and where.
- Roster moves in the national and beat briefs.
- Agent Wire posts for moves.
- Fantasy and Napper context for moves.
- Later hooks for trades and rumors (step 3) and for draft risers and fallers (step 4).

---

## 0. Survey (code and data facts that set the scope)

| # | Fact (file:line / live query) | Consequence for step 5 |
|---|---|---|
| S1 | Writer tools are `@tool` functions in `newsroom/db_tools.py` (32 of them) that return a string, usually `json.dumps(...)` with a `note`. They get their season from `resolve_season` (`:23`) and their week from `run_context.cutoff_week` (imported at `:17`). | The new tools follow the same shape. Each SQL fetch (`fetch_*(cur, …)`) is kept apart from a **pure** formatter, so the tests never need a DB. |
| S2 | `SwarmDispatcher.__init__` lists the base tools in `self.tools` (`swarm_dispatcher.py:772`). `tools_for` (`:809`) adds `FANTASY_TOOLS` only for `FANTASY_*` types. Every tool's description goes into the prompt (`:884`). | Registering the tools once gives them to every writer, fantasy included. Which article types *use* them is set by directive lines and brief keys, not by gating. Three tools, not four: `get_league_transactions(team_identifier=…)` covers the team view. |
| S3 | `run_context.cutoff_week` / `is_visible_week` hide `week >= N`. `OFFSEASON_EVENT_ORDER` has `signings: 3` and `is_offseason_event_visible` (`run_context.py:66–86`, `cutoff_week` `:89`). `get_team_injury_report` reads `week <= cutoff`, so a Week N article sees Week N's report (`db_tools.py:2181–2198`). | Moves headed `WEEK N` happen before Week N's games, like the injury report. The rule in §2.2 shows them to a Week N article. Offseason rows use the offseason event cascade. |
| S4 | Live: `league_state` = 2036 W4. `transactions` 2036: W1 6 REL / 52 RE_SIGNED / 12 RESTRUCTURED / 2 SIGNED; W2 9 / 45 / 0 / 17; W3 5 / 16 / 0 / 9; W4 4 REL / 4 SIGNED. All 181 rows have a `player_id`. All come from capture 20 (`captures.mode = transactions`, `captures.week = 4`). `transactions.week` is NULL for offseason headings (spec §3.3e). | The Week 1 re-signing wave (52 deals) is the first big story. `captures.week` makes a per-week visibility rule possible (§2.2). |
| S5 | Live: 1,695 `ACTIVE`/`SCAN` contracts. All 26 `TRANSACTION` contracts are `SUPERSEDED`, because the Season scan (capture 21) loaded after the backfill (capture 20). `player_contract_years.projected` = 0 rows. 0 contracts carry `extension_years`. 0 of 32 `team_cap_seasons` rows are derived (`derived_at > scanned_at`). | Today every number is scanned. Projected years and derived cap rows start with the next weekly load, so the labels (§2.1) have to work from day one, even though nothing is labelled yet. |
| S6 | `vw_team_cap_sheet` has `cap_hit_y0…y4` but no `projected` flag and no `derived_at` (`pg_get_viewdef`). | The tools read `player_contract_years.projected` and `team_cap_seasons.derived_at/scanned_at` directly. No view change and no migration. |
| S7 | Live: IND `cap_space` −$29.5M (dead money $37.8M), CHI −$0.2M, LAR +$0.26M. | There are cap stories now, e.g. teams over the cap and dead money. |
| S8 | Live: 90 of the 181 moves involve players rated 80+ OVR, because ratings run high. | "Notable" can't mean OVR alone (§2.3). |
| S9 | 18 `player_seasons` rows (2036) have `released_week`. `grep released_week newsroom/` finds nothing; only engines, `server/napper_api.py` and `pipeline/` read it. | Writer tools still list released players on their old team: `get_team_roster_depth_chart` (`db_tools.py:1711`), `get_team_context` top 3 (`:495`), `get_team_draft_needs` (`:1594`), `get_franchise_rebuild_status` (`:1824`), the beat ranked roster (`swarm_dispatcher.py:1162–1230`), `fantasy_tools._roster` (`:67`), and the social injury context (`social_desk.load_week_context`, `:204`). Phase 2 fixes them. |
| S10 | The beat brief already gets a team block: `compile_agent_prompt` (`swarm_dispatcher.py:845`) adds `team_injury_report` for `LOCAL_BEAT_REPORTER` (`:1162–1235`), with a nested `is_starter_rank` (`:1203`). The national brief comes from `build_weekly_assignment_brief` (`:132`), which already adds `key_injuries` per game (`:283–312`). | Moves go in the same two places. `is_starter_rank` moves to a shared pure module so notability can reuse it. |
| S11 | The "no contracts / trades / signings" rules found: `EDITORIAL_MATRIX.md:138–147` (the cascade), `:155`, `:158`, `:162`, `:120`/`:161`; `article_types.json:578` (PLAYOFF_AUTOPSY), `:670`, `:750`, `:780`, `:806` (FRANCHISE_ARCHITECTURE_BLUEPRINT), `:834` (DRAFT_BLUEPRINT_ROSTER_AUDIT), `:864` (OFFSEASON_MOCK_DRAFT), `:1557–1558`, `:1588–1589`, `:1617–1618`, `:1646–1647`, `:1676–1677`; `playoff_overrides.json` `SLATE_LOOKAHEAD_PREDICTIONS[1]` (Tale of the Tape); `article_structure_directives.json:9–12`; `offseason_service.py:682`, `:740`, `:769`; `social_writer.py:24` (`SOCIAL_RULES`); `SOCIAL_MATRIX.md:92`; PROJECT_SPEC §7 "Invented detail vs. data" ("…contract details … are never used"). | §2.6 goes through every one of them, line by line, as proposals. |
| S12 | `tests/test_writer_config.py:204` `test_no_contract_asks` asserts that no persona or type asks for "cap space", "contract-year", "extension value"…, with the comment "Contracts aren't modelled". | It has to be reworked in the same phase as the rule changes (Phase 3), or it fails as soon as a directive names cap data. |
| S13 | `SUPER_BOWL_RUNNER_UP_POSTMORTEM.structure_directive` (`article_types.json:1619`) already asks for "contract/cap pressures", with no data behind it. | It becomes grounded once `get_team_cap_sheet` exists (Phase 4 adds its `CAP DATA:` line). |
| S14 | Offseason signings come from a hand-entered manifest (`offseason_service.py:48–125`: 90+ OVR checked, free-text `contract_notes` `:262`/`:760`). | Phase 7 (optional) can build the candidate list from offseason `SIGNED` rows. JR still picks the 2–3 players. |
| S15 | Agent Wire: `BUCKET_SIZES` (`social_desk.py:32`). A beat note becomes `BEAT_INJURY` when the team has a starter injury (`:1625–1644`). `INJURY_SHOCK` die-hard posts are appended to reactions (`:1724–1732`). `plan_team_wakeup` (`:1930`). `KIND_INSTRUCTIONS` (`social_writer.py:35`). The insiders `SCHEFTER_TWEET`/`RAPOPORT` are only a follower tier (`social_desk.json:45`, `social_publish.py:156`), yet the Schefter persona already opens with "Breaking:"/"Trade:" (`agent_personas.json:18`). | Moves follow the injury pattern: a beat-note override, die-hard reactions, and a small insider bucket. The insiders finally get news to break. |
| S16 | Fantasy: `FANTASY_TOOLS` (`fantasy_tools.py:890`), `get_fantasy_waiver_wire` (`:368`), `get_fantasy_player_profile` (`:495`), `FANTASY_DESK_GUIDELINES` (`fantasy_desk.py:150`). Projections already zero released players (`fantasy_projection_engine.py:289`). | Fantasy needs context (who moved, what role), not new projections. |
| S17 | Napper: has-game uses `released_week` (`napper_waiver_engine.py:261`, `napper_lineup_engine.py:143`). League chat takes `SYSTEM_BOT` cards (`napper_chat_engine.py:231`). The weekly step is `napper_weekly.run_league_week` (`:47`). | An optional "NFL move" card for rostered players (Phase 6b). |
| S18 | `tests/test_fantasy_tools.py` tests tool helpers offline under `article_run_context`. `tests/test_napper_api.py` / `test_napper_engines.py` write to the live DB (memory). `tools/verify_newsroom.py` runs read-only checks against the live DB with no Gemini calls. | New tests stay pure. Live checks go in `verify_newsroom.py`, which is read-only. |
| S19 | `news_feed` 2036 already has W1–W4 (149 / 121 / 123 / 123 articles). | The first edition that can use any of this is Week 5. Nothing is regenerated. |

## 1. Console facts to check

Nothing in this step needs the console. Two data facts to watch on the next weekly scans. Neither blocks a build.
1. **Late `WEEK N` rows.** A scan at league week N+1 can add new rows headed `WEEK N`. If Madden logs some of week N's moves when the week advances, those rows would leak into a regenerated Week N article. The §2.2 rule (`captures.week`) already covers this, so the check is just a count: `SELECT week, capture_id, count(*) FROM transactions GROUP BY 1,2`.
2. **Offseason signing rows** carry multi-year terms in the list (Phase 7 depends on it). CPU free-agent deals in season are 1-year only (master §3.3).

---

## 2. Design

### 2.1 Labels: what a writer may state as fact

Every money figure a tool prints carries its **basis**. The pure helper `roster_moves.money_text(int) -> "$16.8M"` formats it.

| Source | Basis label printed | Writer may say |
|---|---|---|
| `player_contracts.source = 'SCAN'`, `player_contract_years.projected = false` | `scanned <date>` | As fact. |
| `source = 'TRANSACTION'` (a CPU signing) | `from the signing` | Length and total as fact. Bonus, cut savings and cut penalty are NULL and print as **"not known"**, never as $0. |
| A re-signing or extension (`extension_years/value`, `projected = true` years) | `new deal: N years, $X total, starts YYYY; year-by-year split not known yet` | The headline terms (years and total come from the transaction row) as fact. **Tools never print projected per-year hits**, and future-year totals leave them out (with a count of the deals left out). |
| A restructure (this season's hit −X, later years +X/n `projected`) | `restructured Week N: freed $X this season` | The freed amount as fact. The later-year spread is projected and not printed. |
| `team_cap_seasons` with `derived_at > scanned_at` | `estimated: last cap scan <date> plus N roster moves since; dead money from in-season signings is unknown` | As an approximate figure ("roughly $4M in space"). |
| `team_cap_seasons` otherwise | `scanned <date>` | As fact. |
| A gm proposal that isn't `EXECUTED` (Phase 8) | `RUMOR, not a done deal` | Only as a rumor (§2.8). |

**JR's decision (2026-10-20): projected year-by-year hits *are* shown, each labelled `est.`** (overrides the withholding in the two rows above). The tools print them as `2037: $14.2M est.`. The CONTRACT FACTS RULE in the research blocks and the R1 Matrix section tell writers: when a writer cites a projected yearly figure, the prose must hedge it ("roughly", "projected", "about"); headline terms (years, total) are stated as fact. A Phase 1 test checks that every projected value in tool output carries `est.`.

### 2.2 Week cutoff (`newsroom/roster_moves.py`, pure)

`txn_visible(season, week, capture_week, ctx_season, ctx_week, offseason_event_ok) -> bool`:
- With no article context, or a row from an earlier season than the article's, the row is visible.
- A row from a later season is hidden.
- In the same season:
  - With `week` set: visible if `week < N`, or if `week == N` and the row was loaded by a capture taken at week ≤ N (`captures.week`; a row with a NULL `capture_id`, such as step 3's confirmed-trade rows, counts as visible when `week <= N`). This keeps late `WEEK N` rows (§1.1) out of a regenerated Week N article.
  - With `week` NULL (the offseason): visible only when the article is Week 24 and `is_offseason_event_visible("signings")` holds, so the Super Bowl stories and the Coaching Carousel don't see them (EDITORIAL_MATRIX cascade).

`new_this_week(...)` gives the rows a Week N article sees that the Week N−1 article didn't. The briefs use it.

The contract and cap tables have no per-week history. Like rosters, they show the **current** books (PROJECT_SPEC §7 "Not cut off"). One exception: `get_player_contract` hides the extension block when its `extended_transaction_id` isn't visible, so a regenerated old week can't leak a later extension. In forward runs, which is how the newsroom runs, the books and the week agree.

### 2.3 Notability (pure, `roster_moves.notability`)

**JR's decision (2026-10-20): a move is notable only when the player is rated 85+.** The conditions below only describe and rank a move (OVR still never printed). The original proposal, kept for reference: a move is **notable** when any of these holds:
- the player is a starter for his team (the shared `is_starter_rank` from S10);
- his `popularity_index` is 50 or more;
- a re-signing or extension totals $20M or more;
- a signing totals $5M or more;
- a restructure frees $5M or more;
- a release leaves $5M or more of dead money (stored `cut_penalty`);
- his OVR is 88 or more (used only to rank, never printed).

The thresholds are constants. The Phase 1 exit check prints how many of the 181 live moves pass. The aim is roughly 15–35, and JR tunes it if it misses (open question 3). Notable moves are what the briefs and the Agent Wire carry. The tools list everything and flag the notable ones.

### 2.4 Writer tools (`newsroom/db_tools.py`, registered in `self.tools`)

All three return JSON text, read `resolve_season(season)`, apply §2.2, and resolve teams with `_resolve_team_id` and players the same way `get_player_season_stats` does.

- **`get_team_cap_sheet(team_identifier: str = "", season: int = 0, limit: int = 20) -> str`**
  - **With a team:** `cap_basis` (§2.1), then `cap`, `adjusted_cap`, `cap_space`, `salaries`, `dead_money` (`cap_penalty`), `total_spend` and `next_rookie_reserve` as money text, plus the league rank in cap space.
  - `top_contracts`: the `limit` largest cap hits this season, each with player, position, cap hit, `through` (`contract_end_season`), total value, bonus, cut savings and penalty ("not known" when NULL), no-trade clause and basis.
  - `expiring`: deals with `years_remaining = 1`, i.e. players in a contract year.
  - `committed_by_year`: the sum of scanned hits for season+1 … season+4, plus `excludes_new_deals: K`.
  - `recent_moves`: the count of this team's visible moves, pointing to `get_league_transactions`.
  - **With no team:** all 32 teams by cap space (space, total spend, dead money, basis), like `get_opponent_adjusted_credit` does.
  - The `note` says to cite cap figures with their basis, to treat an estimated figure as approximate, and never to give a number the tool doesn't show.
- **`get_player_contract(player_identifier: str, season: int = 0) -> str`**
  - The `ACTIVE` contract: team, length, years remaining, `through`, total, bonus, savings, penalty, no-trade clause, incentive flag, basis, and this season's cap hit.
  - The extension block (§2.1).
  - Status history this season: `RELEASED` / `TRADED` / `SUPERSEDED` rows, each with the move that caused it.
  - The player's visible moves this season.
  - With no contract on file: `"No contract on file"`, and the note says not to invent one.
- **`get_league_transactions(team_identifier: str = "", week: int = 0, types: str = "", position: str = "", notable_only: bool = False, limit: int = 40, season: int = 0) -> str`**
  - The visible moves, newest week first, one line each, e.g. `Week 4 · SIGNED · Jaguars signed WR Devonta Taylor (free agent): 1 year, $3.93M · now WR4 on the depth chart · notable: no`.
  - Releases add the dead money and "free agent since Week N".
  - Re-signings show the headline terms and "starts 2038".
  - `week` limits the list to one week; a hidden week is refused, like `get_game_box_score`. `types` is a comma list.
  - `counts` by type. `rows_with_no_player` lists unmatched rows (`player_id` NULL) by name only.
  - `TRADED` rows appear once step 3 writes them (Phase 8). Until then the note says trades are reported only when completed.

The research blocks (`swarm_dispatcher.py` national `:990–1022`, beat `:1025–1040`) get one line per tool plus a **CONTRACT FACTS RULE**: terms, cap figures and moves come only from these tools or the brief; estimated figures are approximate; never a projected year-by-year figure; invent the colour (§2.5).

### 2.5 Hallucination policy for contracts (D6, memory "invent untracked freely, never contradict data")

- **Never invented and never contradicted:**
  - that a move happened: who signed, released, re-signed or restructured whom, which team, which week;
  - its terms: years, total, this season's cap hit, bonus, savings, dead money, no-trade clause;
  - team cap space, spend and dead money;
  - who is on which roster, and whether a released player is a free agent;
  - which year a contract runs through.
- **Free to invent (untracked):**
  - negotiation colour: how long talks took, the agent's stance, "both sides wanted it done before the opener";
  - anonymous front-office or agent quotes, the player's motives, locker-room reaction;
  - why a team cut a player (as long as the reason doesn't contradict data: no inventing an injury, and no "cut for cap space" when the team has room, unless framed as opinion);
  - what the move means for the depth chart;
  - unnamed interest from "other teams". It stays **unnamed and unpriced**, because naming a team and a figure can collide with that team's real cap sheet.
- **Never invented even though it's untracked:** guaranteed money (Madden shows only a bonus), incentive details (only a yes/no flag), option years, and any number for a projected year.
- **Trades:** a trade is "done" only when its proposal is `EXECUTED` and visible. `PROPOSED`/`APPROVED`/`REJECTED` proposals are rumors at most (§2.8). No writer invents a trade, or a trade package, as a fact.

### 2.6 Editorial rule changes (**proposals for JR; nothing changes before he approves**)

| # | Where | Today | Proposal |
|---|---|---|---|
| R1 | `EDITORIAL_MATRIX.md`: a new section after *Slot Targeting Rules*, **"Roster Moves & Contracts"** | (none) | Writers may report the season's signings, releases, re-signings and restructures and cite contract terms and cap figures from the tools, under §2.1/§2.5. Moves get no slate slot of their own in Weeks 1–23 and feed the existing slots (§2.7). A trade is reported as done only when executed; anything else is a rumor (Phase 8). |
| R2 | `EDITORIAL_MATRIX.md:155` and `:138` (Super Bowl articles); `article_types.json:1557–1558`, `:1588–1589`, `:1617–1618`; `offseason_service.py:682` | No "free-agent departures/signings … as completed facts" | **Narrow** to *offseason* moves (after Week 23). In-season signings and releases are part of the season, e.g. a Week 9 signing who started in the Super Bowl. |
| R3 | `EDITORIAL_MATRIX.md:158` and `:141` (Coaching Carousel); `article_types.json:1646–1647`; `offseason_service.py:740` | No "free-agent signings, trades, or rookie draft selections" | **Narrow** to offseason signings, offseason trades and draft selections. In-season moves are history. The draft ban stays as it is. |
| R4 | `EDITORIAL_MATRIX.md:162` (Signing Grades: no draft) | No draft | **Stays.** It's correct anti-leakage. |
| R5 | `EDITORIAL_MATRIX.md:120`, `:161`; `article_types.json:1676–1677` (`FREE_AGENCY_SIGNING_GRADES`: "Never infer unstated contract figures") | Terms only from the manifest's free-text notes | Terms **cited from `get_player_contract` / the signing row**; never infer one that's missing. The 90+ curation and the OVR-tier wording stay. |
| R6 | `article_types.json:578` (PLAYOFF_AUTOPSY: "without speculating on specific salary figures or trade proposals") | No salary figures, no trades | Lift **salary**: real cap space, expiring deals and dead money from the tools. Keep "no invented trade proposals". |
| R7 | `article_types.json:806` (FRANCHISE_ARCHITECTURE_BLUEPRINT: "rather than … salary cap numbers or raw trade packages") | No cap numbers, no trade packages | Lift **cap numbers**. Keep "no trade packages". |
| R8 | `article_types.json:834` (DRAFT_BLUEPRINT_ROSTER_AUDIT: "without citing specific player salary numbers or speculative trades") | No salaries, no trades | Lift **salaries**, including `next_rookie_reserve`. Keep "no speculative trades". |
| R9 | `article_types.json:864` (OFFSEASON_MOCK_DRAFT: no speculative trades) | No trades | **Stays.** Draft-day trades belong to steps 3/4. |
| R10 | `article_types.json:670`, `:750`, `:780`; the `playoff_overrides.json` Tale of the Tape | No contract figures in game previews | **Stay.** These are game pieces. (Lifting them is possible, but nothing gains from it.) |
| R11 | `article_structure_directives.json:9–12` ("Do not invent … contract details") | No invented contract details | **Stays.** It *is* the policy. |
| R12 | `social_writer.py:24` `SOCIAL_RULES` ("Never invent … a contract figure …, and never state a trade or signing as fact"); `SOCIAL_MATRIX.md:92` | No contracts, no signings or trades as fact | → "Contract figures, signings, releases and trades only from FACTS; a trade is done only if FACTS says it was executed; a rumor stays a rumor; never invent a contract figure." |
| R13 | PROJECT_SPEC §7 "Invented detail vs. data" | "…contract details … are never used" | Rewrite to §2.5 (spec edit, made in Phase 3). |
| R14 | `tests/test_writer_config.py:204` `test_no_contract_asks` | Fails on any cap ask | → `test_contract_asks_name_the_cap_tools`: a type may ask for cap or contract content only if its `data_queried` / `curatorial_autonomy` names `get_team_cap_sheet` or `get_player_contract`. Personas still may not ask. |

**JR's decisions (2026-10-20):**
- **R1: approved, with a relevance rule.** Moves and cap figures appear **only when they matter to the article's subject**. Most articles should carry no cap numbers. The new Matrix section and every lifted directive say so, and the tools' docstrings tell writers to call them only when a move or contract bears on the story.
- **R2, R3: narrowed** as proposed.
- **R4: stays.**
- **R5: approved** as proposed. Terms are cited from the tools.
- **R6: salary lifted.** Trade packages are allowed too, by JR's question "shouldn't it be able to do real trade packages?". The R6/R7 trade-package rule is:
  - **Real trades are always reportable.** Executed trades come from the transactions and `gm_trade_proposals` (Phase 8).
  - **Hypothetical packages are allowed** when framed as the writer's idea ("a deal they could chase"), never as reported talks.
  - Each package uses only real players and picks with real contract numbers from the tools, and has to pass step 3's legality check (cap, no-trade clause, roster limits, deadline). A helper tool for that check ships in Phase 8.
  - Only `rumor_ok` proposals may be described as actual talks.
  - Until step 3 lands, the packages part stays off: the lift ships in Phase 8, and salary/cap lifts ship in Phase 3.
- **R7: cap numbers lifted.** Trade packages follow the same rule as R6 (Phase 8).
- **R8: salaries lifted** (including `next_rookie_reserve`). Its "no speculative trades" rule stays.
- **R9, R10, R11: stay.**
- **R12: approved** as proposed (Agent Wire may use real contract figures and state real moves as fact).
- **R13: spec rewrite** (Phase 3).
- **R14: test updated** to `test_contract_asks_name_the_cap_tools` (Phase 3).
- **Still open:** §6 questions 2–7.

### 2.7 Coverage: transactions feed existing slots; no new regular-season slot

- **National brief** (`build_weekly_assignment_brief`): `league_transactions` = {`through_week`, `notable_moves` (≤ 12 lines from `new_this_week`, notable first), `counts`, `cap_watch` (teams over the cap or under $2M), `note`}. It's omitted in a week with no visible new moves.
- **Directive data lines**, like the existing `COACHING DATA:` lines, appended to `curatorial_autonomy`:
  - `CAP DATA:` (the cap sheet, expiring deals, dead money, what a contract year means): `FRANCHISE_STATE_OF_THE_UNION` (from W15, the eliminated team's next-year space), `SEASON_PROGRESS_AUDIT`, `PLAYOFF_AUTOPSY`, `SUPER_BOWL_RUNNER_UP_POSTMORTEM` (grounds S13), `FRANCHISE_ARCHITECTURE_BLUEPRINT`, `DRAFT_BLUEPRINT_ROSTER_AUDIT`, `PLAYER_DEEP_PROFILE` (contract status), `LOCAL_SEASON_PROGRESS_REPORT`, `LOCAL_PLAYER_SPOTLIGHT`.
  - `ROSTER MOVES:` (notable moves since last week, from `get_league_transactions`): `POWER_RANKINGS` (a blurb may mention a move; never rank a team on an invented one), `SEASON_PROGRESS_AUDIT`, `LOCAL_MATCHUP_PRIMER` (a new signing who may play), `LOCAL_POSTGAME_REPORT_CARD` (next man up after a release).
- **Local beat** (the `compile_agent_prompt` LOCAL_BEAT_REPORTER block, `:1162`): `team_transactions` = {the team's moves new this week, a one-line cap snapshot with its basis, `reporting_guidance`: a notable move gets a paragraph; terms from facts only; invent the negotiation colour}. There is **no new wakeup trigger**. Wakeups stay on-demand page views, and the portfolio (`local_portfolio.py`) is unchanged. A dedicated `LOCAL_TRANSACTION_REPORT` type is open question 4.
- **Offseason package:** FA grades per R5, plus Phase 7.

### 2.8 Agent Wire (`social_desk.py`, `social_writer.py`)

- `load_week_context` adds `ctx["moves"]` (visible to week N, with notability, the new team's depth slot and the team's cap snapshot) and drops released players from `injuries_by_team` (Phase 2).
- New kinds, all from facts packets (no tools), window "Mon – Wed":

| Kind | Who | Posts per week | Facts |
|---|---|---|---|
| `MOVE_NEWS` | an insider (`SCHEFTER_TWEET`, `RAPOPORT`) | 0–3, the most notable moves | The move line and money text, the team's record, the new role (depth slot). A "Breaking:" style report. |
| `BEAT_MOVE` | the team's beat reporter | Replaces the team's `BEAT` note when the team has a notable move (a starter injury still comes first, as `BEAT_INJURY` does today) | The team's moves this week and the cap snapshot. |
| `MOVE_REACTION` | a die-hard of the team | 0–2, appended like `INJURY_SHOCK` | The move, plus how long the player has been with the team when `player_seasons` history shows it. |
| `CAP_TAKE` | one pundit | 0–1, only when a team is over the cap or a release left ≥ $10M dead money | The team's cap line and basis. |

- `plan_team_wakeup` uses `BEAT_MOVE` the same way.
- Posts count toward the ~100 target, and the existing `TRIM_ORDER` absorbs them. `kind_factor` entries go in `social_desk.json`.
- **Money guard** (`social_writer.clean_post`): a `$` amount in the post that isn't among the facts packet's money strings is a problem. The post gets the one existing rewrite, and is then `[NOT SAVED]`.
- `SOCIAL_MATRIX.md`: add these kinds to the §2 table, replace the §3 grounding line per R12 (**proposals**, Phase 5, after R12 is approved).

### 2.9 Fantasy and Napper
- `get_fantasy_waiver_wire` adds `nfl_moves`: skill-position players (QB/HB/FB/WR/TE) signed in the last 2 visible weeks, with their new team, depth slot and rostered %, plus released players who are still rostered (drop candidates, projected 0).
- `get_fantasy_player_profile` adds `team_moves`: his moves this season.
- `FANTASY_DESK_GUIDELINES` adds a rule: a released player has no game and projects 0 until he signs; a signing is a pickup only if the depth chart gives him a role; never invent a signing.
- **Napper (optional, open question 5):** `run_league_week` posts one `NFL_MOVE` `SYSTEM_BOT` card per newly visible move that touches a player rostered in that league ("released by the Jaguars: no game until he signs" / "signed with the Bears"). A pure planner `napper_move_cards(moves, rostered_ids)` builds the cards. Rendering the card in Mu_Network's Napper chat is part of the same phase.

---

## 3. Phases

Phases 1–2 need no approval. Phases 3–7 come after JR rules on §2.6, and Phase 3 is the approved rule text itself. Phases 8 and 9 wait on steps 3 and 4 and touch nothing the others ship.

### Phase 1: Facts module and writer tools
**Files**
- New `newsroom/roster_moves.py`, pure (no psycopg): `money_text`, `cap_basis`, `contract_basis`, `txn_visible`, `new_this_week`, `is_starter_rank` (moved from `compile_agent_prompt`, which now imports it), `notability`, `move_line`.
- `newsroom/db_tools.py`: the three tools (§2.4), each as `fetch_*(cur, …)` plus a call to the pure formatters.
- `swarm_dispatcher.py`: import them, add them to `self.tools`, and add the research-block lines and the CONTRACT FACTS RULE (§2.4).

**Tests** (offline)
- New `tests/test_roster_moves.py`:
  - money formatting ($200K, $3.93M, $16.8M, negative cap space);
  - every basis label in §2.1, including NULL bonus → "not known";
  - projected years never in the formatter output, and `excludes_new_deals` counted;
  - `txn_visible`: same week before or after capture week, an earlier season, a later season, an offseason row with and without the `signings` event, a NULL capture;
  - notability on hand-built rows;
  - `new_this_week`.
- `tests/test_writer_config.py`: the three tools are registered and named in both research blocks (the same pattern as `test_coach_profile_is_registered_and_hooked_up`).
- **No test opens a DB connection.**

**Exit check**
- The tests pass.
- `tools/verify_newsroom.py` gets read-only checks:
  - the three tools return valid JSON for IND, for a player re-signed in Week 1, and for the league (no team);
  - under `article_run_context(2036, 3)` no Week 3+ row shows, and under `(2036, 4)` the Week 4 rows do;
  - the notable count on the live data is printed for JR.
- No prompt asks for the tools yet beyond the research blocks.

### Phase 2: Released players leave team views
**Files**
- `db_tools.py`: `get_team_roster_depth_chart`, `get_team_context` (top 3), `get_team_draft_needs`, `get_franchise_rebuild_status`, and the team labels in `get_top_rated_players` / `get_top_popular_players` / `get_player_season_stats`. A player counts as on the team for a Week N article iff `released_week IS NULL OR released_week > N`. That's Napper's has-game rule (S17), with N = the cutoff week, or `league_state` with no context. A released player's team prints as "free agent (released by X, Week N)".
- `swarm_dispatcher.py`: the beat ranked roster (`:1162`).
- `fantasy_tools._roster`.
- `social_desk.load_week_context` (injuries).

**Tests:** a pure `on_roster(released_week, week)` in `roster_moves`, with unit tests. Formatter tests for the "free agent" label.

**Exit check:** `verify_newsroom.py` asserts that none of the 18 live released players appears on his old team's depth chart, and that each shows as a free agent in `get_player_context`.

### Phase 3: Approved rule changes (gated on JR's §2.6 decisions)
**Files:** exactly the approved rows of §2.6 (R1–R3, R5–R8, R12 and R14, whichever JR approves): `docs/EDITORIAL_MATRIX.md`, `newsroom/config/article_types.json`, `newsroom/offseason_service.py` (the `anti_leakage_boundary` strings), `newsroom/social_writer.py` `SOCIAL_RULES`, `tests/test_writer_config.py`.

**Tests**
- R14 rework.
- A new `test_rumor_and_trade_wording`: no directive says a trade may be reported unless it is executed.
- `tests/test_social_writer.py`: the new `SOCIAL_RULES` text.

**Exit check:** the writer-config, social-writer and offseason tests pass, and a `compile_agent_prompt` dry run for FSOTU and the Coaching Carousel prints the new wording.

### Phase 4: Briefs, beat and directive data lines
**Files**
- `swarm_dispatcher.py`: `league_transactions` in `build_weekly_assignment_brief`, and `team_transactions` in the beat block (§2.7).
- `article_types.json`: the `CAP DATA:` / `ROSTER MOVES:` lines and their `data_queried` entries.

**Tests**
- `test_writer_config.py`: each listed type carries its line and names its tool.
- Brief shaping as a pure `roster_moves.brief_block(rows, …)`, tested on hand-built rows (≤ 12 lines, notable first, omitted when empty, never an OVR number).

**Exit check:** a dry run (`dispatch_single_archetype(..., live=False)`) for 2036 W5 shows both blocks. JR reads one live FSOTU and one beat article from the next real run.

### Phase 5: Agent Wire moves (needs R12)
**Files:** `social_desk.py` (`ctx["moves"]`, the four kinds, `plan_team_wakeup`), `social_writer.py` (`KIND_INSTRUCTIONS`, the money guard in `clean_post`), `social_desk.json` (kind factors), `docs/SOCIAL_MATRIX.md` (the approved text).

**Tests**
- `tests/test_social_desk.py` synthetic league: moves → at most 3 `MOVE_NEWS` from insiders only, `BEAT_MOVE` replacing `BEAT`, but not when the team has a starter injury, `CAP_TAKE` only when its trigger holds, still ~100 posts in total.
- `tests/test_social_writer.py`: the money guard.

**Exit check:** `python3 -m newsroom.social_desk --season 2036 --week 5` (the dry plan, no model call) shows the move posts.

### Phase 6: Fantasy (6a; the 6b Napper card was dropped by JR)
**Files**
- 6a: `fantasy_tools.py` (`nfl_moves`, `team_moves`), `fantasy_desk.py` (the guideline), and the `FANTASY_WAIVER_WIRE` curatorial line.
- 6b: `engines/napper_weekly.py` and the Napper chat card in Mu_Network.

**Tests**
- 6a: in `tests/test_fantasy_tools.py`, the helpers on hand-built rows.
- 6b: a new `tests/test_napper_move_cards.py`, pure. **Not** in `test_napper_api.py` / `test_napper_engines.py`, which hit the live DB.

**Exit check:** `verify_newsroom.py` prints the W5 `nfl_moves`. For 6b, JR sees a card in a test league after a weekly step he runs himself.

### Phase 7: Offseason signings from transactions (in scope, §6 Q6)
**Files:** `offseason_service.py`. `preview` lists offseason `SIGNED` rows of 90+ players with their terms as candidates, and JR still picks 2–3 for the manifest. The packet cites terms per R5.

**Tests:** extend `tests/test_offseason_service.py` with fake rows.

**Exit check:** a dry-run preview on 2036 data lists none (there's no offseason yet) and doesn't fail.

### Phase 8: Trades and rumors (**depends on step 3**)
**What step 3 provides** (`03_GM_AGENTS_PLAN.md`, written in parallel; check it again when this phase starts):
- One `TRADED` `transactions` row per leg, with `trade_group = 'gm:<id>'`, `gm_proposal_id`, `txn_date = '<season> SEASON - WEEK <week>'` and `capture_id` NULL (its §2.7).
- `gm_trade_proposals.rumor_ok` (seeded, about 40% of non-executed proposals), plus `week`, `status`, `approved_at` / `executed_at`.
- The read helpers `engines/front_office.executed_trades(cur, season, week)` and `trade_rumors(cur, season, week)` (its §2.9).
- Step 5 only reads these and doesn't pick its own leaks.

**Request to step 3:** the status timestamps are wall-clock times, not league weeks. Rumors are therefore cut off by the proposal's `week` (visible when `week < N`). An `EXPIRED`/`REJECTED` proposal reads as "talks stalled" only from the week after its `expires_week`.

**Files**
- `get_league_transactions` shows `TRADED` rows: the teams, players and picks moving, and the cap effect for each team read from the contract tables after the move. Its §2.2 rule treats `capture_id` NULL with `week == N` as visible, because JR confirms a trade before Week N's games.
- New `get_trade_rumors(team_identifier="", season=0)`, a wrapper around `front_office.trade_rumors`. It gives the teams and the asset types and positions only: no package, no cap figures, `status: RUMOR`.
- Agent Wire `TRADE_NEWS` (insiders, plus die-hards of both teams) and `TRADE_RUMOR` (insiders, at most 2 a week, only through the trade deadline).
- **Proposal for JR:** in the week after an executed trade, a `TRADE_GRADES` type (Sando or Barnwell) takes one Feature Trio slot. This is a matrix change.

**Tests:** pure tests for the rumor and trade formatters on hand-built proposals: never an `EXECUTED` proposal as a rumor, never a pending one worded as done, nothing at or after the article's week.

**Exit check:** the dry social plan and a dry FSOTU prompt for a week with a fake executed trade, against a restored DB copy only.

### Phase 9: Draft risers and fallers (**depends on step 4**)
**Interface request to step 4:** a weekly consensus board (season, week, prospect, grade, rank) and the per-team hit-rate inputs (`q_team`).

**Files**
- A new `get_draft_risers_fallers(season=0, limit=10)` that respects the cutoff.
- `@MockDraftMarty` `DRAFT` posts from risers and fallers.
- `OFFSEASON_MOCK_DRAFT` / `DRAFT_BLUEPRINT_ROSTER_AUDIT` / `FSOTU` lines that cite the board and the hit-rate facts ("three straight first-round misses", master §2.5).

**Tests:** pure formatter tests.

---

## 4. `PROJECT_SPEC.md` edits (CLAUDE.md rule, made in the same phase)

| Phase | Sections |
|---|---|
| 1 | §1.1 layout (`newsroom/roster_moves.py`); §7 "Database Context & Research Tools" (the three tools); §7 "Season & week scope" (the transactions visibility rule, the contract books not cut off, the extension hiding) |
| 2 | §7 "Season & week scope" (the on-roster rule); §7.2 (the beat roster); §7.3.1 (`_roster`); §7.8 (social injuries) |
| 3 | §7 "Invented detail vs. data" (R13, §2.5); §7.1 entries for the changed types; §7.8 posting rules; §7.10 (the anti-leakage narrowing) |
| 4 | §7.1 (the CAP DATA / ROSTER MOVES lines); §7.2 (`team_transactions`); §7.3 (the `league_transactions` brief key) |
| 5 | §7.8 (the new kinds, the money guard, the team wakeup) |
| 6 | §7.3.1 (fantasy context); 6b: §3.4b `card_payload` types, §4.4b (the weekly step posts move cards) |
| 7 | §7.10 (candidates from transactions) |
| 8 | §7 tools (`get_trade_rumors`, `TRADED` lines); §7.8 (`TRADE_NEWS` / `TRADE_RUMOR`); §7.3 if `TRADE_GRADES` is approved |
| 9 | §7 tools; §7.8 draft posts |

Phases 3–5 also edit `docs/EDITORIAL_MATRIX.md` / `docs/SOCIAL_MATRIX.md`, and only with JR's approved text.

## 5. Out of scope
- Any schema change: §2.1 and §2.2 are used as they are. `vw_team_cap_sheet` stays.
- Making moves (step 2), trade logic, proposals and approval (step 3), scouting grades (step 4).
- New regular-season slate slots (proposed only; R1 and the Phase 8 `TRADE_GRADES`).
- Regenerating Weeks 1–4 of 2036.
- Per-week contract or roster history.
- Gemini calls in tests or checks.

## 6. Open questions, answered (JR, 2026-10-20)
1. **§2.6 rule changes:** all of R1–R14 decided (see JR's decisions under §2.6).
2. **Projected numbers:** shown year by year, labelled `est.` (§2.1).
3. **Notability thresholds (§2.3):** accepted as the starting point.
4. **Beat coverage:** moves feed the existing beat articles. No new beat article type.
5. **Napper `NFL_MOVE` cards (6b):** dropped. They aren't needed as long as every move, trades included, updates the player's team. Signings and releases already do this through the transactions load; step 3's trade confirmation does it for trades. Phase 6 is 6a only.
6. **Offseason signing grades (Phase 7):** in scope. The candidate list is built from transactions.
7. **Trades (Phase 8):** yes to all three: rumors from `rumor_ok` proposals (about 40%), rumors only through the Week 8 deadline, and a `TRADE_GRADES` Feature Trio slot the week after an executed trade.
