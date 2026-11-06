# Project Mu: Personality on the Site Plan

**Status:** approved by JR (2026-11-03), Q1–Q3 settled as recommended (§8); building, wave 1 of 2 (2026-11-06). The other plans were updated at approval (§7).
**Builds on:** [`PERSONALITY_PLAN.md`](./PERSONALITY_PLAN.md) D3 (traits and sliders visible, mood as a tag, engine effects never as numbers), D4; [`PERSONALITY_CORE_EXTENSIONS_PLAN.md`](../done/PERSONALITY_CORE_EXTENSIONS_PLAN.md) (override, `history`, D2's deferred site editor); [`PERSONALITY_RELATIONSHIPS_PLAN.md`](../done/PERSONALITY_RELATIONSHIPS_PLAN.md) §2.3–§2.4 (ties, attachment, mood); [`PERSONALITY_EVOLUTION_PLAN.md`](../done/PERSONALITY_EVOLUTION_PLAN.md) (change notes); [`PERSONALITY_TRADE_REQUESTS_PLAN.md`](../done/PERSONALITY_TRADE_REQUESTS_PLAN.md) §2.5 (public projection); [`SCOUTING_REVAMP_PLAN.md`](../in_progress/SCOUTING_REVAMP_PLAN.md) (prospects' personalities stay hidden); PROJECT_SPEC §4, §5.

**Goal:** Let JR see who everyone in the league is. A Personality panel on player pages, coach and GM staff cards, the owner and scouting director on the front office tab, and writers' author pages shows:
- the sliders as plain sentences with a bar;
- the traits with what they tend to do;
- this week's mood tag;
- how the person has changed (evolution notes);
- his known relationships as words, his attachment to his team, open promises, and a public trade request.

No engine numbers are shown. JR edits any profile right on the page, through the audited core override.

**Delivers:**
- `server/personality_api.py` (router `/api/personality`, mounted in `server/api.py`): a profile read, a catalogue read and an override write.
- `Mu_Network/src/components/personality/PersonalityPanel.tsx`, `PersonalityEditor.tsx`, `personality.css`, and `Mu_Network/src/types/personality.ts`.
- The panel embedded in `PlayerPage.tsx`, `StaffCards.tsx`, `FrontOfficeTab.tsx` and `AuthorPage.tsx`.

**JR's answers (2026-11-03):**
- D1: Every player once he's in the league, and every coach, owner, GM, scout and writer, is shown in full. Draft prospects stay hidden until drafted; scouting interviews reveal hints, as the scouting plan intends.
- D2: Relationships show as labels, never numbers: known ties in words (trusts, wary of, holds a grudge against, mentor to), his attachment to his team, open promises, and an active trade request once it's public.
- D3: The editor is on the page. A Personality tab on each person's page has an Edit button for JR (the same token as `#debug`), and uses the audited override. `#debug` keeps working.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `Mu_Network/src/App.tsx:140` routes `#player-<id>` to `PlayerPage`, and `:141` `#author-<key>` to `AuthorPage`. Coaches show on the team page through `components/team/StaffCards.tsx:54`; GMs on `FrontOfficeTab.tsx`. There is no coach or owner page. | The panel goes where people already appear. No new routes. |
| S2 | `components/league/PlayerPage.tsx:132` hero, `:167` `PlayerHonors`, `:168` `PlayerCareerPath`. | The Personality panel sits after the career path. |
| S3 | `Mu_Network/src/DebugPage.tsx:21`, `:26` keep the token in localStorage (`TOKEN_KEY`) and send `X-Debug-Token`; `components/draft/DraftWarRoom.tsx:99`, `:141` reuse it. `server/debug_api.py:59` checks it against `DEBUG_TOKEN`. | The editor shows when a token is stored, and the write endpoint checks it the same way. |
| S4 | `server/api.py:144–149` mounts routers (`debug_api`, `front_office_api`, `franchise_api`, …). | One line mounts `personality_api.router`. |
| S5 | The core extensions plan gives `store.override`, `history`, `Identity.adopted` (writers keep `persona_id` like `BARNWELL`, GMs their `gm_id`), and `feature_view` with `view_version`. The relationships plan gives `relationship_context` (tier, ties, breaches) and `select_mood`. The evolution plan gives `change_notes`. The trade-requests plan gives `request_projection(..., audience='PUBLIC')`. | The API only reads these. When a module isn't built yet, its block is `null` with a reason and the panel hides it. |
| S6 | The lifecycle plan R6 and the scouting plan keep prospects' profiles out of public outputs. | The API refuses a prospect without a `db-player` alias (404 `hidden_until_drafted`), per D1. |
| S7 | `newsroom/personas.py:28–35` `SLIDER_WORDS` holds the low and high phrase for each slider (for example temperament "calm and even-keeled" ↔ "volatile and quick to boil over"), and leaves 4–6 out of the sentence. | Slider sentences reuse those phrases, so the site and the prompts describe a person the same way. |

## 1. Scope

**In:**
- A read API for any persona's display profile, a catalogue read for the editor, and an audited override write.
- The panel and the editor components.
- The panel on player pages, staff cards, the front office tab (GM, owner, scouting director) and author pages.

**Out (non-goals):**
- Showing engine numbers: coefficients, trust values, chances, draws.
- Prospects' personalities before the draft (D1).
- New pages or routes for coaches and owners.
- A league-wide personality browser.
- Changing who the newsroom may quote (visibility rules stay in their plans).

## 2. Design

### 2.1 API (`server/personality_api.py`)

```text
GET  /api/personality/{kind}/{ref}          kind: player | coach | coordinator | owner | gm | scout | writer
                                            ref:  players.id | coaches.id | coordinators.id | team abbr | gm_id | team abbr | writer key
GET  /api/personality/catalogue?role=R      traits eligible for the role: {id, name, category, tendency, conflicts}
POST /api/personality/{kind}/{ref}/override X-Debug-Token required; body {sliders?: {name: 0..10}, add_traits?: [], remove_traits?: [], reason}
```

The GET response:

```json
{"kind": "player", "ref": "4411", "name": "...", "origin": "generated", "editable": true,
 "sliders": [{"name": "temperament", "value": 7, "sentence": "volatile and quick to boil over", "low": "calm and even-keeled", "high": "volatile and quick to boil over"}],
 "traits": [{"id": "LOYAL", "name": "Loyal", "category": "Social", "tendency": "tends to stick with a team that treats him well"}],
 "mood": {"primary": "frustrated", "secondary": ["uncertain"]} ,
 "changes": ["Since the 2029 title he talks more about legacy than rings."],
 "ties": [{"label": "trusts", "target": {"kind": "coach", "ref": "12", "name": "..."}}],
 "attachment": {"team": "KC", "label": "deeply attached"},
 "promises": [{"direction": "made_to_him", "by": {"kind": "coach", "name": "..."}, "text": "WR1 by week 9", "status": "open"}],
 "trade_request": {"status": "ACTIVE", "public_since": {"season": 2029, "tick": 8}},
 "unavailable": {"mood": "relationships not built"}}
```

- **Resolution:** `kind` and `ref` resolve through core aliases (`db-player`, `db-coach`, `db-coordinator`, `db-owner`) or `Identity.adopted` (writers, GMs). An unresolved player who is only a prospect returns 404 `hidden_until_drafted`. Any other unresolved person returns 404 `no_profile`.
- **Sliders:** `sentence` picks the low phrase for 0–3, nothing for 4–6 and the high phrase for 7–10, from `newsroom/personas.SLIDER_WORDS`. The bar shows the 0–10 value. A slider value is a description, not an engine effect, so D3 allows it.
- **Ties and attachment labels (D2):**
  - Tie labels come from the relationships context's tier and breaches: `TRUST_LABELS` maps the known trust bands to words (≥ 0.5 trusts, 0.15–0.5 on good terms with, −0.5 to −0.15 wary of, ≤ −0.5 at odds with); an unresolved breach toward that person reads "holds a grudge against"; a mentorship tie reads "mentor to" or "mentored by".
  - The neutral band and unknown ties are omitted, and at most `MAX_TIES = 6` are shown, strongest first.
  - Attachment tiers read: none → no label; weak → "settling in"; established → "attached"; strong → "deeply attached".
- **Promises:** open ones he made or was given, from the relationships ledger.
- **Trade request:** only the trade-requests plan's `audience='PUBLIC'` projection.
- **Changes:** the last `MAX_CHANGE_NOTES` evolution notes.
- **Override:** POST calls `store.override` with `actor 'JR'`, the current `league_state` season and tick, and `expected_revision` from the profile. It returns the new GET body, 409 on a stale revision, and 422 with the core's validation message on an invalid edit.

### 2.2 Frontend

- **`types/personality.ts`:** the response and request types above.
- **`PersonalityPanel.tsx`** (`{kind, refId}`):
  - fetches the profile;
  - shows the slider rows (sentence plus bar), trait chips with their tendency on hover, the mood tag, change notes, ties as linked names, attachment, promises and a trade-request badge;
  - hides unavailable blocks;
  - a 404 `hidden_until_drafted` shows "Personality revealed after the draft";
  - shows Edit when a debug token is stored and `editable` is true.
- **`PersonalityEditor.tsx`:**
  - slider steppers with a live total that must stay within 24–36;
  - a trait picker from the catalogue that greys out conflicts, categories already at their limit and ineligible traits;
  - a required reason field;
  - Save posts the override and shows the server's message on 409 or 422.

  The checks run on the client for convenience; the server is the authority.
- **`personality.css`:** panel styles using the site's existing tokens; works at phone width.
- **Placement:**
  - `PlayerPage` after `PlayerCareerPath`;
  - a collapsible "Personality" row on each coach and coordinator card in `StaffCards`;
  - the GM, owner and scouting director in `FrontOfficeTab`;
  - `AuthorPage` under the author header.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Many feature modules aren't built when this builds. | Each block is `null` with a reason. | The panel shows what exists; nothing errors. |
| R2 | Slider bars read as engine numbers. | JR's review of the first build. | Hide the bars and keep the sentences (one flag in the panel). |
| R3 | The editor bypasses the evolution history's meaning. | Overrides are receipts in `history` with actor JR. | The changes list shows "Edited by JR" for overrides whose reason starts with `note:`, and nothing otherwise. |
| R4 | Shared files: `server/api.py` (many plans add a router line), `PlayerPage.tsx`, `StaffCards.tsx`, `FrontOfficeTab.tsx`, `AuthorPage.tsx` (other plans may touch them). | Build order. | U3 builds after those and re-reads them. |

**Spike first:** none.

## 4. Tests (synthetic, no live DB)

- `tests/test_personality_api.py`:
  - the GET shape for each kind through fake core, relationships, evolution and trade-request modules;
  - a prospect gets 404 `hidden_until_drafted`;
  - slider sentences at the band edges;
  - tie labels by band, breach as grudge, the neutral band omitted, `MAX_TIES`;
  - missing modules give `unavailable`;
  - no numbers outside `sliders[].value` anywhere in the body;
  - the catalogue respects role eligibility;
  - POST override: token required (403), stale revision 409, invalid 422, success returns the new body.
- Frontend: `npm run build` in `Mu_Network/`, plus fixture checks of the panel (hidden prospect, missing blocks, editor total limits, conflicts greyed out) in narrow and wide layouts.

Run with `pytest -m "not integration" tests/test_personality_api.py` and `npm run build` in `Mu_Network/`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: API

- **Owns:** `server/personality_api.py` (new), `server/api.py`, `tests/test_personality_api.py` (new)
- **Reads:** this plan §2.1; `server/debug_api.py:50–110` (token check); `newsroom/personas.py:24–35` (`SLIDER_WORDS`); the core extensions, relationships, evolution and trade-request plans' interfaces
- **Depends on:** none. Missing feature modules are handled as unavailable.
- **Done when:** the three endpoints work as §2.1 against fakes.
- **Verify:** `pytest -m "not integration" tests/test_personality_api.py -q` passes.

### U2: Panel and editor components

- **Owns:** `Mu_Network/src/components/personality/PersonalityPanel.tsx` (new), `Mu_Network/src/components/personality/PersonalityEditor.tsx` (new), `Mu_Network/src/components/personality/personality.css` (new), `Mu_Network/src/types/personality.ts` (new)
- **Reads:** §2.1 response shape, §2.2; `Mu_Network/src/DebugPage.tsx:15–30` (token); an existing card component's styles
- **Depends on:** none; it builds against the §2.1 shape.
- **Done when:** both components render the fixture cases and the build passes.
- **Verify:** `npm run build` in `Mu_Network/` passes.

### U3: Page placement

- **Owns:** `Mu_Network/src/components/league/PlayerPage.tsx`, `Mu_Network/src/components/team/StaffCards.tsx`, `Mu_Network/src/components/team/FrontOfficeTab.tsx`, `Mu_Network/src/components/newsroom/AuthorPage.tsx`
- **Reads:** U2; the four pages
- **Depends on:** U2
- **Done when:** the panel shows in the four places in §2.2.
- **Verify:** `npm run build` in `Mu_Network/` passes; a manual check of a player, a coach card, the front office tab and an author page.

## 6. Waves

| Wave | Units | Needs merged first | Why |
|---|---|---|---|
| 1 | U1, U2 | — | The API and the components build against the §2.1 shape. |
| 2 | U3 | U2 | Places the merged components. |

Building waits until every personality sub-plan is approved (JR, 2026-11-03). This plan can build early: it shows whatever exists.

**File ownership check:**

| File | Unit |
|---|---|
| `Mu_Network/src/components/league/PlayerPage.tsx` | U3 |
| `Mu_Network/src/components/newsroom/AuthorPage.tsx` | U3 |
| `Mu_Network/src/components/personality/PersonalityEditor.tsx` | U2 |
| `Mu_Network/src/components/personality/PersonalityPanel.tsx` | U2 |
| `Mu_Network/src/components/personality/personality.css` | U2 |
| `Mu_Network/src/components/team/FrontOfficeTab.tsx` | U3 |
| `Mu_Network/src/components/team/StaffCards.tsx` | U3 |
| `Mu_Network/src/types/personality.ts` | U2 |
| `server/api.py` | U1 |
| `server/personality_api.py` | U1 |
| `tests/test_personality_api.py` | U1 |

No path appears twice.

## 7. Spec changes when built

- §1.1 Repository Layout: `server/personality_api.py`, `Mu_Network/src/components/personality/`, `types/personality.ts`, the test, and this plan's entry.
- §4: a new subsection for `/api/personality/*` (the three endpoints, visibility, labels, token).
- §5.2: the Personality panel on the player page, staff cards, front office tab and author page, and the editor.
- §7.11: D3 as built (sentences and bars, labels, no engine numbers, prospects hidden until drafted).
- Other plans, at approval: the core extensions plan's D2 (site editor deferred) points here; PERSONALITY_PLAN D3 and the tracker point here.

## 8. Decisions for JR

JR approved all three recommendations on 2026-11-03.

| # | Question | Decision (JR, 2026-11-03) |
|---|---|---|
| Q1 | Show the 0–10 bar next to each slider sentence? | Yes: the bar is a description of the person, not an engine effect. If it reads too much like a stat, the panel can hide bars with one flag. |
| Q2 | Should overrides appear in the person's "how he's changed" list? | Only when you start the reason with `note:`; otherwise your edits stay out of his story. |
| Q3 | Where does the owner show? | On the team's Front Office tab, next to the GM and the scouting director. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
