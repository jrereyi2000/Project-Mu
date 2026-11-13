# Project Mu: UI Type Scale and Container Plan

**Status:** approved by JR (2026-11-12); building, wave 2 of 3 — all five §8 recommendations accepted; built by `build-ui-type`.

**As built (wave 1, 2026-11-12):**
- **U1:** `:root` verbatim per §2.2 (`--gray-500` keeps `#717176`, D6); all 698 type declarations in `index.css` → rungs; 22 container blocks and every 42 px horizontal padding → `var(--gutter)`; 79 `color: var(--gray-500)` → `--text-muted`; 49 red/team text rules → `var(--accent-text, var(--red-text))`, with white-on-red and dark-surface text kept on `--red`/`--team-color`. Gap values (52–67, 69–103) took the nearest rung. **One visible growth:** the article H1 clamp max 87 → `--fs-104` (`index.css:662`) — the only rung above 64; flipping that line to `--fs-64` is the alternative if JR dislikes it. The author-page `var(--author-team-color, var(--red))` text rules were left alone: `--accent-text` is published only by the four team pages, so converting them would drop the author's colour.
- **U4:** all five stylesheets → `var(--fs-*)`; 34 muted-text rules → `--text-muted`; 7 red-text rules → `var(--accent-text, …)`; the history and draft containers → `var(--container)`. `napper.css` and `napperDraft.css` keep their own dark palette (only sizes changed).
- **Measured on `main` after wave 1** (14 surfaces × 2560/1440/1050/900, headless Chromium against the live site): **0 sideways scroll at every width** (was 177 px at 900), **0 leaves under 10 px** on every surface outside the franchise page (was 78–352), and the two §2.6 token groups pass AA. The franchise page's 8–9 px labels are wave 2's.
- **Plan defect found in wave 1 (recorded for JR):** §2.2's rule "no literal px sizes anywhere in `Mu_Network/src` except `:root`" and §2.8's invariant on `Mu_Network/src/**` reach files the ownership table does not list — `NapperPage.tsx`, `DebugPage.tsx` and 10 files under `components/napper/` still hold 130 literal sizes (27 under 10 px). A tail unit (U7) sweeps them before U6, so the invariant can be strict network-wide.
- **Contrast gate scope (§2.7 vs §1's non-goals):** the Mu Fantasy and Agent Wire palettes are explicitly out of colour scope in §1, so U6's tool hard-fails contrast on the Gridiron surfaces and reports the own-palette surfaces (fantasy, napper, social, draft) as advisory rows, with `--strict` for a full sweep. The ticker, disabled controls and `aria-hidden` text are excluded; gradient-backed leaves are reported as unmeasurable rather than failed.

**Builds on:** [`Mu_Network/PROJECT_SPEC.md`](../../Mu_Network/PROJECT_SPEC.md) §9 (Responsive Behavior), §10 (Visual Systems), §11 (Accessibility); root `PROJECT_SPEC.md` §1.1's `Mu_Network/` row; and the measurements in [`P8_PAGE_CHECKS_2026-11-12.md`](../reference/P8_PAGE_CHECKS_2026-11-12.md) §5.

**Goal:** JR reads the whole network on a 1440p screen without squinting. Today the label type is 5–9 px (`516` declarations under 10 px across 20 files), the content sits in a 1240 px column with 660 px gutters on a 2560-wide screen, one grey text colour carries 203 of its uses at 4.04–4.37:1 contrast, and between 1050 px and 801 px the entire page scrolls sideways. This plan puts every size on one ladder behind tokens, widens the shared container, fixes the shell breakpoint, wraps the franchise tab bar, takes label text over 4.5:1, and leaves a machine check so the next bump is one number instead of 700 rules.

**Delivers:**
- `:root` tokens in `Mu_Network/src/index.css`: the `--fs-*` ladder (10 px floor), `--container`, `--container-narrow`, `--gutter`, `--text-muted`, `--red-text`, `--accent-text`.
- `--accent-text` derived per team in `Mu_Network/src/lib/accent.ts` and set by the four pages that publish `--team-color`.
- A 1080 px shell breakpoint so the header stops overflowing at 1050–801 px.
- A two-row franchise tab bar below 1200 px.
- `tools/ui_layout_check.mjs` — the measurement tool (Node + Playwright, run by hand).
- `tests/test_ui_type_scale.py` — source invariants that stop the drift coming back.

**JR's answers (2026-11-12):**
- D1 **Scope:** understand what the whole network needs first, then builders implement. This plan is that understanding.
- D2 **Contrast:** the fix rides along ("yes, let the contrast fix run along").
- D3 **Type:** a 10 px floor and the rest scaled up ("bump type 4 according to the recommendation").
- D4 Empty `user_teams` means "nobody is mine" — **not this plan**: it is ticket 1 in board M-0880 (`server/inbox_api.py`, `franchise_api`, `press_api`). This plan changes no behaviour and no API.
- D5–D9 (2026-11-12, "approve all your recommendations"): the ladder as printed in §2.1; the container at 1440 flat for now; `--red-text #c4162f` accepted for labels on paper; the tab bar wraps to two rows below 1200 px; and only the 203 text uses of `--gray-500` move to `--text-muted` (borders and fills keep the old grey).

---

## 0. Survey (facts that set the scope)

| # | Fact | Consequence |
|---|---|---|
| S1 | **516 declarations set text under 10 px**: 124 `font-size:` and 392 `font:` shorthands (5 px × 7, 6 px × 42, 7 px × 100, 7.5 px × 3, 8 px × 135, 9 px × 105, counting `font-size:` too), across 20 files. | This is the bulk of the work: a mechanical but wide sweep. It is why the type reads small — the dominant label is 7–8 px mono. |
| S2 | **47 distinct sizes from 5 px to 104 px** are in use (`font-size:` values alone). | A proportional multiply cannot satisfy the floor (5 px + 25 % is still tiny) and would push body text to 15 px. A ladder is the only shape that keeps the hierarchy *and* the floor. |
| S3 | Type rules live in **20 files**: `index.css` 695, `components/history/history.css` 63, `components/team/frontOffice.css` 51, `components/draft/draft.css` 41, `components/napper/draft/napperDraft.css` 39, `napper.css` 26, `components/personality/personality.css` 20, and 13 franchise components whose CSS is an inline `<style>` block in the `.tsx` (`PressConferencePanel.tsx` 28, `InboxModule.tsx` 25, `TradeMachineModule.tsx` 22, `FreeAgencyModule.tsx` 16, `GameCenterModule.tsx` 15, `ReportCardPanel.tsx` 14, `RosterCapModule.tsx` 12, `DepthChartModule.tsx` 12, `FranchisePage.tsx` 8, `DraftWarRoomModule.tsx` 7, `StaffContractCard.tsx` 5, `TrainingRoomModule.tsx` 3, `ContractRoomModule.tsx` 3). | Five surfaces, five owners' worth of CSS. The tokens must live in the global `:root` (`index.css:4-15`) so the per-module inline blocks can reference them. |
| S4 | Colour **is** tokenised (`index.css:4-15`: `--ink`, `--paper`, `--red`, `--gray-100/300/500`, `--font-sans/mono`); size is not. | The precedent exists; the size tokens are the same pattern, one screen further. |
| S5 | `--gray-500: #717176` is used **203 times as a text colour** (and 3 times as a border, 58 in other properties), and measures **4.37:1 on paper, 4.06:1 on the table-head grey** — under the 4.5 AA line, on the smallest text in the app. | Darkening this one token fixes the largest contrast group in one line, because it is already a token. |
| S6 | Red is used as **text 112 times** (`var(--red)` / `var(--team-color, var(--red))`), at **4.25:1 on paper, 3.95:1 on the table head**. `--red` is *also* the ticker and identity colour. | Changing `--red` would change backgrounds. The fix is a text-only variant. |
| S7 | `--team-color` is set inline per page from the team's colour: `TeamPage.tsx:98`, `PlayerPage.tsx:207`, `SchedulePage.tsx:224`, `PowerRankingsPage.tsx:107`. Eyebrow text uses `var(--team-color, var(--red))`, so a light team colour fails arbitrarily badly. | A derived, darkened `--accent-text` is needed, computed once per page, not a fixed red. |
| S8 | **Container widths are mixed**: `max-width: 1240px` ×9, `1356px` ×8, `1440px` ×6, `1480px` ×3, `1380px` ×3, `1120px` ×4. Measured: the franchise content is 1240 px wide starting at x=660 on a 2560×1440 screen. | One shared `--container` (1440) plus a narrow one keeps the shapes but fills a 1440p display. |
| S9 | The shell's collapse point is `@media (max-width: 800px)` (`index.css:1534-1540`); the header's content needs ~1077 px (`index.css:43-56`). Measured `document.scrollWidth` 1077 at every width from 1050 down to 820, 0 at 800. | A 1080 px breakpoint for the shell closes the gap that makes every page scroll sideways. |
| S10 | The franchise tab bar is 11 tabs, 1147 px wide (`FranchisePage.tsx:85-89`, `overflow-x: auto`, no affordance); it fits exactly at 1240 and scrolls silently below. | One CSS change in the same file as the page. |
| S11 | Density is network-wide, not local: at 1440, leaves under 10 px / total leaves — standings 352/458, franchise roster 344/735, stats-team 230/336, schedule 111/242 (87 of them under 8), newsroom home 78/173, fantasy 43/65, social 18/38, napper 1/24, draft 0/43 (draft is already 11 px minimum). | "Whole network" is the right scope; `#draft` and `#napper` are already close and only need the token sweep. |
| S12 | No frontend test runner exists: `Mu_Network/package.json` has `dev`/`build`/`preview`/`format` only, and the repo's tests are `pytest`. | The permanent guard has to be a Python source check plus a hand-run Node measurement tool. |

**Measured today** (2026-11-12, from `docs/reference/P8_PAGE_CHECKS_2026-11-12.md` §5): at 2560×1440 the Command Center is a 1240 px column with 660 px gutters; the smallest labels are 7–9 px at 4.04–4.37:1; the header scrolls the page sideways by 177 px at 900.

---

## 1. Scope

**In:**
- One type ladder, as CSS custom properties on `:root`, applied to every type declaration in `Mu_Network/src`.
- One shared container token plus a narrow variant, applied to the 33 `max-width` blocks listed in S8.
- The shell breakpoint at 1080 px, and the franchise tab bar wrapping below 1200 px.
- Text contrast: the muted grey and a text-safe accent, including per-team derivation.
- A committed measurement tool and a pytest invariant so the floor cannot drift back.

**Out (non-goals):**
- **The empty-`user_teams` convention and the Inbox 403** — board M-0880 ticket 1, a server change with one UI empty state. Separate unit, separate approval.
- **Any new font, palette, spacing scale or component redesign.** Archivo and Roboto Mono stay; the paper/ink/red identity stays.
- **Mobile (`≤560 px`) layout changes.** The sweep applies below 560 too (a 6 px label is 6 px on a phone), but no layout is redesigned there.
- **Table column design.** Wider tables get more room; the columns that exist stay.
- **The dark Agent Wire palette and the Mu Fantasy navy palette** as *colours*. They get the ladder and the containers, not a restyle.
- **Motion, iconography, imagery.** Untouched.

---

## 2. Design

### 2.1 The type ladder

19 rungs, replacing 47 sizes. The 10 px floor is a hard invariant; everything at or above 16 px keeps its hierarchy on a 2 px grid.

| Today | Rung | Token |
|---|---|---|
| 5, 6, 7, 7.5, 8, 9 | **10** | `--fs-10` |
| 10 | 10 | `--fs-10` |
| 10.5, 11, 11.5 | 11 | `--fs-11` |
| 12 | 12 | `--fs-12` |
| 13, 13.5 | 13 | `--fs-13` |
| 14 | 14 | `--fs-14` |
| 15 | 15 | `--fs-15` |
| 16, 17 | 16 | `--fs-16` |
| 18, 19 | 18 | `--fs-18` |
| 20, 21 | 20 | `--fs-20` |
| 22, 23 | 22 | `--fs-22` |
| 24, 25, 26 | 24 | `--fs-24` |
| 27–30 | 28 | `--fs-28` |
| 31–34 | 32 | `--fs-32` |
| 35–38 | 36 | `--fs-36` |
| 39–43 | 40 | `--fs-40` |
| 44–46 | 44 | `--fs-44` |
| 48–51 | 48 | `--fs-48` |
| 68 | 64 | `--fs-64` |
| 104 | 104 | `--fs-104` |

The effect on the thing JR complained about: every 7–8 px mono label becomes 10 px (+25 % to +43 %), the 9 px labels become 10 px, 10–12 px body text stays, and the headline sizes move by at most one rung. Nothing gets smaller.

### 2.2 Tokens (`Mu_Network/src/index.css` `:root`)

```css
:root {
  --fs-10: 10px;  --fs-11: 11px;  --fs-12: 12px;  --fs-13: 13px;  --fs-14: 14px;
  --fs-15: 15px;  --fs-16: 16px;  --fs-18: 18px;  --fs-20: 20px;  --fs-22: 22px;
  --fs-24: 24px;  --fs-28: 28px;  --fs-32: 32px;  --fs-36: 36px;  --fs-40: 40px;
  --fs-44: 44px;  --fs-48: 48px;  --fs-64: 64px;  --fs-104: 104px;
  --container: 1440px;
  --container-narrow: 1120px;
  --gutter: 42px;
  --text-muted: #5c5d63;   /* was --gray-500 #717176 on paper: 4.37 → 5.91 */
  --red-text: #c4162f;     /* red for text on paper: 4.25 → 5.40 */
  --accent-text: var(--red-text);  /* overridden per team as a darkened team colour */
}
```

`--gray-500` stays for borders and fills (3 border uses, 58 others) and becomes `--gray-500: #5c5d63` **only if** the visual check shows the darker border is acceptable; otherwise the 203 text uses move to `var(--text-muted)` and `--gray-500` keeps its value. **Decision D6 below.**

Every declaration then names a token: `font: 700 var(--fs-11) var(--font-mono)`. No literal `px` sizes anywhere in `Mu_Network/src` after this plan except inside the `:root` block itself.

### 2.3 Container

```css
.fr-head, .fr-tabs, .page-grid, .social-shell, … { max-width: var(--container); }
```

- `1240`, `1356`, `1380`, `1440`, `1480` → `var(--container)` (1440 px).
- `1120` → `var(--container-narrow)`.
- The `--gutter` token replaces the `42px` horizontal padding in the same blocks so the 1440 and 1240 layouts keep their proportions.

At 2560 the Command Center then runs 1440 px wide (560 px gutters each side, down from 660), and nothing moves on a 1280–1440 laptop because the box was already capped at 1240–1440 there.

### 2.4 Shell breakpoint

A new `@media (max-width: 1080px)` block immediately above the existing 800 block, shrinking the shell instead of letting it overflow:

- `.brand { margin-right: 34px }`
- `.nav-links { gap: 22px }`
- `.nav-links > a, .teams-trigger, .stats-trigger { font-size: var(--fs-11) }`
- `.nav-tools { gap: 14px }`

Acceptance: `document.documentElement.scrollWidth === innerWidth` at 1050, 1000, 950, 900, 850, 820 and 800 px, and the search button and LIVE badge visible at 900.

### 2.5 Franchise tab bar below 1200 px

In `FranchisePage.tsx`'s `PAGE_CSS`: `@media (max-width: 1200px) { .fr-tabs { flex-wrap: wrap; overflow-x: visible; row-gap: 2px; } }`, so all eleven tabs are visible in two rows instead of three being off-screen. No markup change, no new component.

### 2.6 Contrast

| Group | Today | New | Measured on paper / table head |
|---|---|---|---|
| muted labels, table headers, helper lines (203 uses) | `#717176` | `#5c5d63` | 4.37 → **5.91** / 4.06 → **5.49** |
| red eyebrow / section labels (112 uses) | `#e31837` | `#c4162f` (`--red-text`) | 4.25 → **5.40** / 3.95 → **5.02** |
| per-team eyebrow | team colour as text | `var(--accent-text)` | derived to ≥4.5 on paper |

`--accent-text` is computed by a new pure helper, `Mu_Network/src/lib/accent.ts`:

```ts
/** A text-safe version of a team colour: mix toward ink until it clears 4.5:1 on --paper. */
export function accentText(hex: string): string
```

It parses the hex, blends toward `#101114` in 5 % steps, and returns the first blend that reaches 4.5:1 against `#f4f3f0` (worst case 0 % → `#101114`). The four pages that set `--team-color` (`TeamPage.tsx:98`, `PlayerPage.tsx:207`, `SchedulePage.tsx:224`, `PowerRankingsPage.tsx:107`) also set `--accent-text: accentText(colour)`. CSS rules that use red or team colour *as text* switch to `var(--accent-text, var(--red-text))`; rules that use it as a background, border or fill keep `var(--red)` / `var(--team-color)` untouched.

### 2.7 The measurement tool (`tools/ui_layout_check.mjs`)

Node + Playwright, run by hand, no dependency added to `Mu_Network/package.json` (the repo has no Node test runner and a browser download does not belong in `npm install`):

```
node tools/ui_layout_check.mjs [--url http://127.0.0.1:8000] [--widths 2560,1920,1440,1100,1050,900,800]
                               [--surfaces newsroom,team,standings,schedule,stats-team,records,awards,draft,social,fantasy,franchise]
                               [--shots DIR] [--json out.json]
```

For each surface × width it prints one row — document width vs viewport, the smallest font size and how many leaves are under the floor, the worst contrast pair, and the widest content block — and exits 4 if any surface scrolls sideways, any leaf is under 10 px, or any text leaf is under 4.5:1 (disabled controls and the ticker excluded). It installs its own browser on demand into a scratch cache (`npx playwright install chromium`) and refuses to run if `playwright` is not resolvable, printing the two-command fix.

### 2.8 The invariant test (`tests/test_ui_type_scale.py`)

A source check, not a browser: it reads `Mu_Network/src/**` and asserts

1. no `font-size:` or `font:` shorthand declaration carries a literal size below `10px`;
2. every literal `px` size is one of the ladder rungs (10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24, 28, 32, 36, 40, 44, 48, 64, 104);
3. every literal size outside `:root` is banned — i.e. each declaration uses `var(--fs-*)`;
4. `index.css` contains a `@media (max-width: 1080px)` block and the container blocks use `var(--container)` / `var(--container-narrow)`;
5. the tokens' contrast: `contrast(--text-muted, --paper) ≥ 4.5` and `contrast(--red-text, --paper) ≥ 4.5`, computed in Python from the hex values in `:root`.

It is the guard that makes "bump the type" one edit in future.

---

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | 516 declarations changed in one plan is a large visual diff; a reviewer cannot eyeball it in a PR. | The tool's before/after JSON plus screenshots at four widths, per surface, attached to each unit's report. | Land surface by surface (wave 2 has three units); revert the offending surface, keep the tokens. |
| R2 | The ladder changes the *look*, not only the size: 5–6 px kickers become 10 px and can feel heavy or wrap. | First unit's screenshots at 1440 and 2560 on the newsroom and a team page, before wave 2. | Per-block override tokens (`--fs-kicker: 10px`) rather than a lower floor; JR picks after seeing it. |
| R3 | Darkening red text (`--red-text #c4162f`) may read muddy next to `--red` backgrounds. | Screenshot a page with both (team hub eyebrow + ticker). | Keep `#e31837` for text *on white only* (4.72:1 passes) and use `--red-text` only on paper. |
| R4 | Some team colours cannot reach 4.5:1 by darkening alone (very dark teams: the blend has 0 % headroom). | `accent.ts`'s worst case is `--ink`; test one dark team (e.g. `#1b1d21`) and one light one (e.g. `#ffb612`). | For dark teams, mix toward paper instead; the helper returns the better of the two directions. |
| R5 | Wider tables at 1440 spread row measures and can look sparse. | Screenshot `#roster-cap` and `#standings` at 2560. | Keep `1240` for table-bearing pages via `--container-narrow`; JR chooses on the screenshot. |
| R6 | A wave-2 unit lands before its tokens if the wave order slips, breaking the page for a window. | The tokens are §2.2's exact block; U1 owns them and wave 2 depends on U1 merged. | Merge order is the rule: U1 first, no exceptions. |

**Spike first:** none — the baseline is already measured (2026-11-12, `P8_PAGE_CHECKS_2026-11-12.md` §5 and the survey script in `/tmp/opencode/check-d-ui/`). The only open question is taste (R2/R3/R5), answered by the first unit's screenshots.

---

## 4. Tests (offline, no database)

- `tests/test_ui_type_scale.py` → the five invariants in §2.8; fails on today's tree (516 violations), passes at the end of wave 3.
- `tools/ui_layout_check.mjs` → not pytest: the units run it as their Verify step against the built site; its JSON is the unit report's evidence.

Run with `pytest -m "not integration" tests/test_ui_type_scale.py`.

---

## 5. Work breakdown

Shared files (orchestrator only, never owned): `PROJECT_SPEC.md`, `Mu_Network/PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: tokens, shell and containers (`index.css`)

- **Owns:** `Mu_Network/src/index.css`
- **Reads:** §2.1–2.6 of this plan; `Mu_Network/PROJECT_SPEC.md` §9/§10
- **Depends on:** none
- **Done when:** the `:root` block carries the tokens; every `font-size:`/`font:` in `index.css` names `var(--fs-*)`; the five container widths are `var(--container)`/`var(--container-narrow)`; a `@media (max-width: 1080px)` block exists; the ticker, social and fantasy sections still render.
- **Verify:** `cd Mu_Network && npm run build` succeeds, and the layout tool on `--surfaces newsroom,team,standings,schedule,stats-team,records,awards,social,fantasy` at `--widths 2560,1440,1050,900,800` reports no sideways scroll and no leaf under 10 px (contrast rows for the red/pale groups may still be flagged until U6).

### U2: the franchise page and its modules

- **Owns:** `Mu_Network/src/components/franchise/FranchisePage.tsx`, `RosterCapModule.tsx`, `GameCenterModule.tsx`, `DepthChartModule.tsx`, `ContractRoomModule.tsx`, `TrainingRoomModule.tsx`, `ReportCardPanel.tsx`, `StaffContractCard.tsx`, and `Mu_Network/src/components/team/frontOffice.css`
- **Reads:** §2.1–2.5; `P8_PAGE_CHECKS_2026-11-12.md` §5
- **Depends on:** U1 (tokens must exist)
- **Done when:** every type declaration in those files uses `var(--fs-*)`, and the tab bar wraps to two rows below 1200 px.
- **Verify:** `npm run build`; the tool on `--surfaces franchise` at 2560/1440/1200/1050/900 → no sideways scroll at 900/1050, no leaf under 10 px, all eleven tabs visible at 900.

### U3: the conversation modules

- **Owns:** `Mu_Network/src/components/franchise/InboxModule.tsx`, `PressConferencePanel.tsx`, `TradeMachineModule.tsx`, `FreeAgencyModule.tsx`
- **Reads:** §2.1–2.2
- **Depends on:** U1
- **Done when:** every type declaration in those four files uses `var(--fs-*)`.
- **Verify:** `npm run build`; the tool on `--surfaces franchise` at 1440/900 → no leaf under 10 px.

### U4: the other surfaces' stylesheets

- **Owns:** `Mu_Network/src/components/history/history.css`, `Mu_Network/src/components/draft/draft.css`, `Mu_Network/src/components/napper/draft/napperDraft.css`, `Mu_Network/src/napper.css`, `Mu_Network/src/components/personality/personality.css`
- **Reads:** §2.1–2.2
- **Depends on:** U1
- **Done when:** every type declaration in those five files uses `var(--fs-*)` (draft and napper are already ≥11 px, so this is mostly mapping).
- **Verify:** `npm run build`; the tool on `--surfaces draft,records,awards` and the napper route at 1440/900 → no leaf under 10 px.

### U5: the accent helper and the four team pages

- **Owns:** `Mu_Network/src/lib/accent.ts` (new), `Mu_Network/src/components/team/TeamPage.tsx`, `Mu_Network/src/components/league/PlayerPage.tsx`, `Mu_Network/src/components/league/SchedulePage.tsx`, `Mu_Network/src/components/newsroom/PowerRankingsPage.tsx`
- **Reads:** §2.6; S7 in §0
- **Depends on:** U1
- **Done when:** `accentText()` returns a ≥4.5:1 colour for `#1b1d21`, `#ffb612`, `#e31837` and `#ffffff`, and the four pages set `--accent-text`; text rules that used team colour use it.
- **Verify:** `npm run build`; the tool on `--surfaces team,schedule` at 1440 → no text leaf under 4.5:1.

### U6: the tool and the invariant test

- **Owns:** `tools/ui_layout_check.mjs` (new), `tests/test_ui_type_scale.py` (new)
- **Reads:** §2.7, §2.8; the scratch harness in `/tmp/opencode/check-d-ui/` (`lib.js`, `survey.js`) for the shapes that already work
- **Depends on:** U1–U5 merged (the invariant test must pass at the end, and it is the last thing to land)
- **Done when:** the tool runs all 14 surfaces at all widths against the running site, exits 4 on a violation, and `pytest tests/test_ui_type_scale.py` is green on `main`.
- **Verify:** `pytest -m "not integration" tests/test_ui_type_scale.py` → 5 passed; `node tools/ui_layout_check.mjs --surfaces all` → exit 0.

---

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U4 | — | U1 changes `index.css` only; U4 changes five separate stylesheets. No shared file, no dependency. |
| 2 | U2, U3, U5 | U1 | All three consume the tokens from U1; their files are disjoint. |
| 3 | U6 | U1–U5 | The invariant test can only go green once every sweep has landed. |

**File ownership check:**

| File | Unit |
|---|---|
| `Mu_Network/src/index.css` | U1 |
| `Mu_Network/src/components/franchise/FranchisePage.tsx` | U2 |
| `Mu_Network/src/components/franchise/RosterCapModule.tsx` | U2 |
| `Mu_Network/src/components/franchise/GameCenterModule.tsx` | U2 |
| `Mu_Network/src/components/franchise/DepthChartModule.tsx` | U2 |
| `Mu_Network/src/components/franchise/ContractRoomModule.tsx` | U2 |
| `Mu_Network/src/components/franchise/TrainingRoomModule.tsx` | U2 |
| `Mu_Network/src/components/franchise/ReportCardPanel.tsx` | U2 |
| `Mu_Network/src/components/franchise/StaffContractCard.tsx` | U2 |
| `Mu_Network/src/components/team/frontOffice.css` | U2 |
| `Mu_Network/src/components/franchise/InboxModule.tsx` | U3 |
| `Mu_Network/src/components/franchise/PressConferencePanel.tsx` | U3 |
| `Mu_Network/src/components/franchise/TradeMachineModule.tsx` | U3 |
| `Mu_Network/src/components/franchise/FreeAgencyModule.tsx` | U3 |
| `Mu_Network/src/components/history/history.css` | U4 |
| `Mu_Network/src/components/draft/draft.css` | U4 |
| `Mu_Network/src/components/napper/draft/napperDraft.css` | U4 |
| `Mu_Network/src/napper.css` | U4 |
| `Mu_Network/src/components/personality/personality.css` | U4 |
| `Mu_Network/src/lib/accent.ts` | U5 |
| `Mu_Network/src/components/team/TeamPage.tsx` | U5 |
| `Mu_Network/src/components/league/PlayerPage.tsx` | U5 |
| `Mu_Network/src/components/league/SchedulePage.tsx` | U5 |
| `Mu_Network/src/components/newsroom/PowerRankingsPage.tsx` | U5 |
| `tools/ui_layout_check.mjs` | U6 |
| `tests/test_ui_type_scale.py` | U6 |

No path appears twice.

---

## 7. Spec changes when built

- `Mu_Network/PROJECT_SPEC.md` **§9 Breakpoints**: add the 1080 px shell step, and say the type floor is 10 px and the containers are `--container` (1440) / `--container-narrow` (1120).
- **§10 Visual Systems**: the token block — the ladder, the containers, `--text-muted`, `--red-text`, `--accent-text` — and the contrast targets.
- **§11 Accessibility**: label text is now ≥4.5:1; the known limitation about focus remains.
- **§5 Gridiron Screens / the franchise page**: the tab bar wraps to two rows below 1200 px.
- Root `PROJECT_SPEC.md` **§1.1**: `tools/ui_layout_check.mjs` row; `tests/test_ui_type_scale.py` named in the tests tree; the `Mu_Network/src/lib/` entry gains `accent.ts`.

---

## 8. Decisions for JR

All five were decided on 2026-11-12: JR approved the plan as written and accepted every recommendation below.

| # | Question | Decision (2026-11-12) |
|---|---|---|
| Q1 | **Ladder or a pure proportional scale?** A multiplier cannot lift 5–6 px to a readable size without pushing body text to 15 px; the ladder keeps the hierarchy and the floor. | **Ladder** as printed in §2.1. |
| Q2 | **How wide at the top?** 1440 flat (560 px gutters on a 2560 screen), or 1600 above 2000 px as well? | **1440 flat now**; look at a screenshot before going wider. |
| Q3 | **Red labels get darker** (`#c4162f`, 5.4:1) — accept that the eyebrow red no longer matches the ticker red exactly? | **Yes**; it is a 10 % shift and it is the only way past AA on paper. |
| Q4 | **Tab bar below 1200 px**: two rows (all tabs visible) or one row with a fade and horizontal scroll? | **Two rows**; nothing is hidden and no new control is introduced. |
| Q5 | **`--gray-500` itself darkens** (borders too, 3 uses) or only its 203 text uses move to `--text-muted`? | **Only the text uses**, so borders and fills do not change. |

---

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | `ui-type-u1` | `.claude/worktrees/ui-type-u1` | merged | `eb75f8d6` |
| U4 | 1 | `ui-type-u4` | `.claude/worktrees/ui-type-u4` | merged | `82890d85` |
