# P8-08 … P8-11 page checks — 2026-11-12

**Lane:** D (never-checked close-out items that need a browser) · **session:** `check-d-ui` · **model:** `deepseek-v4.1-flash`
**Rows:** `docs/CLOSE_OUT_CHECKLIST.md` §10.3 P8-08 (inbox), P8-09 (presser), P8-10 (Trade Machine + League Talks), P8-11 (Command Center sweep).
**Target:** the live API on `http://127.0.0.1:8000` — active franchise `1287775425` / `madden_mu2026`, league 2026 week 1, no user teams registered.
**Browser:** headless Chromium (Playwright `chromium-headless-shell 158.0.8127.18`) from `/tmp/opencode/check-d-ui` (`npm i -D playwright`, `npx playwright install chromium`; nothing installed in the repo). Screenshots: `/tmp/opencode/check-d-ui/shots/`.

**Read-only by construction.** The browser context aborts every non-GET/HEAD/OPTIONS request and records it, so a walk cannot write the franchise database even by accident. Across all runs `blocked: []` — **not one write was attempted**, and no reply, send, take-control, approve, offer or accept was pressed. Anything that needs a write is recorded below as *not exercised* with the reason.

---

## 1. Verdicts

| Row | State | What was actually seen |
|---|---|---|
| **P8-08** inbox | **Blocked — not exercisable on the live franchise** | The Inbox page renders its chrome and one line: “The inbox couldn't load (CAR isn't one of your teams in the franchise registry).” All four inbox GETs 403 for **every** team. Cause: the active franchise registry has `user_teams: []` and `server/inbox_api.py:84-88` refuses any team when that list is empty. Take-control, the list, a thread, the reply form and the new-conversation pickers were **never on screen**. |
| **P8-09** presser | **Blocked — not exercisable** | No podium button exists: `GET /press` returns `{"conference": null}`, `GET /press/history` returns `[]`, and `press_conferences` has 0 rows. The podium box only renders for a conference with `controller === "USER"` and status SCHEDULED/LIVE/REVIEW (`GameCenterModule.tsx:276`), and conferences are created by `press_room.schedule_week` — a write plus model spend. Podium → pick → answer → no-comment → end → edit → approve → review → history links: **none reached**. |
| **P8-10** Trade Machine + League Talks | **Partial** — package built; transcript read on scratch data; 409 not hit | Trade Machine walked: 95 players + next-3-drafts picks per side, ticking a player and a pick updates both “You send / You receive” counters and enables **Evaluate trade** / **Talk to their GM**. Neither was clicked (both POST). League Talks on the **live** DB is the correct empty state (`{"talks":[]}`). The transcript view was exercised for real against `madden_tune_p2h` (5 published CPU talks) through a second read-only API: 5 rows, `<details>` opens to turns with posture badges, asset lines, quotes and a FINAL DEAL block, no overflow at 1440 or 900. |
| **P8-11** Command Center sweep | **Swept; defects found, none fixed (no code fixes in this lane)** | All 11 modules at 2560×1440, 1920×1080, 1440×900 and 900×900; all 11 tab links navigate and set `aria-current`; the team picker persists and falls back correctly. Two real layout defects (F1, F2 below), two legibility observations. |

---

## 2. P8-08 — Inbox page

**Steps.** `#franchise-inbox?team=CAR` at 1440×900 and 900×900 → screenshot → read the module text → read every endpoint the module calls (`/inbox/actors`, `/inbox/kinds`, `/inbox/targets?kind=GM_TALK`, `/inbox?limit=50&status=OPEN`) → switch the team picker to CHI and repeat → check the unread badge.

**What the page shows** (the same at both widths):

```
CAR · INBOX
INBOX
The inbox couldn't load (CAR isn't one of your teams in the franchise registry). Try again in a moment.
```

The tab bar, header, “YOUR TEAM” badge, team picker and footer all render normally; there is no unread badge, no take-control panel, no list, no thread, no reply form, no new-conversation picker. Four requests 403 and four console errors follow (`InboxModule.tsx:806-816` is the `StateNote` branch).

**API, exactly:**

```
GET /api/franchise/CAR/inbox/actors               403 {"detail":"CAR isn't one of your teams in the franchise registry"}
GET /api/franchise/CAR/inbox/kinds                403 (same)
GET /api/franchise/CAR/inbox/targets?kind=GM_TALK 403 (same)
GET /api/franchise/CAR/inbox?limit=50&status=OPEN 403 (same)
GET /api/franchise/CHI/inbox/actors               403 {"detail":"CHI isn't one of your teams in the franchise registry"}
```

**Why (finding P8-08-F1, HIGH for this check).** The active registry entry is

```
data/franchises.json → 1287775425: {db: madden_mu2026, name: mu2026, user_teams: []}
```

and three call sites disagree about what an empty `user_teams` means:

| Site | Empty list means |
|---|---|
| `server/inbox_api.py:84-88` (`_team`) and `engines/inbox/actors.py:122-127` (`_require_team`) | **refuse every team (403)** |
| `server/franchise_api.py:255-258` (`require_user_team`) | allow every team (`if teams and …`) |
| `server/franchise_api.py:1100` (`overview.user_controlled`) | `True` for every team (`(not teams) or …`) |
| `server/press_api.py` | GET never gates on it at all |

So on this franchise the site prints **“YOUR TEAM”** for all 32 teams, the Game Center's “isn't one of your user teams: launching and every move will be refused (403)” warning can never appear (`GameCenterModule.tsx:321`), and only the inbox hard-fails — with a raw internal message instead of an empty state. The inbox tables exist and are empty (`inbox_threads` 0, `inbox_messages` 0, `controlled_personas` 0), so this is the registry gate, not missing schema.

**What would let the check run** (either is a write, so neither was done here):
1. Register a user team in the franchise registry (`user_teams: ["CAR"]`) — then the page opens on the **take-control panel**, because `controlled_personas` is empty; and
2. press take-control (`POST /inbox/actors`) so a persona exists, then the list/thread/reply/pickers become reachable.

Even after (1), (2) is a POST, so the reply/send forms can only be *looked at*, never submitted, in a read-only pass. The clean way to check this row is a **scratch franchise with a user team** (a `CAREER-TUNE-*` clone plus its own database), where both writes are allowed.

---

## 3. P8-09 — Presser page

**Steps.** `#franchise-game-center?team=CAR` at 1440 and 900 → look for the podium block and button → `GET /press`, `GET /press/history` for CAR and CHI → check every `madden_*` database for a conference that could be shown.

**What was seen.** Game Center renders normally (next game vs CHI, record, cap room, roster counts, pending moves, dossier, matchups). **No podium block, no “Go to the podium” button, no press-conference text anywhere** (`podiumBtn: false`, zero elements matching /press conference/).

```
GET /api/franchise/CAR/press        200 {"conference":null,"opponent_presser":null}
GET /api/franchise/CAR/press/history 200 {"conferences":[]}
GET /api/franchise/CHI/press        200 {"conference":null,"opponent_presser":null}
```

**Why.** `GameCenterModule.tsx:276-277` sets `podiumReady` only for `pressConference.controller === "USER"` with status SCHEDULED/LIVE/REVIEW, and `:306-317` renders the box and the button from that. `press_conferences` is empty on `madden_mu2026`; conferences are cast by `newsroom/press_room.py:1131 schedule_week()` (a write, and it drafts each user subject's first round of hands when a model is supplied). No `madden_*` database in the repo holds a USER conference: the only one with rows, `madden_press_scratch` (32), is all `controller = AGENT` (31 SCHEDULED, 1 CLOSED).

**Not exercised, with reasons.** podium → pick reporter → answer → no-comment → end → edit → approve → review screen → history links are all **POST/PUT** (`server/press_api.py:149-198`) and need a conference that does not exist. Both the conference and the answers would write `press_conferences`/`press_turns` and spend model money on the reporter's and the coach's lines.

**What would let it run:** a franchise with a user team and one `schedule_week` cast (pressers exist for every game of the week, so a single run makes a podium), then the reply/answer flow, which is write-heavy by nature — that belongs in a scratch franchise, driven by whoever may write it.

---

## 4. P8-10 — Trade Machine + League Talks

### 4.1 Trade Machine (live, read-only)

`#franchise-trade-machine?team=CAR`. Trade partner dropdown defaults to `AZ` and lists all 31 others (save abbreviations: `AZ`, `WAS`, not `ARI`/`WSH`). 191 checkbox rows total: 95 players and 3 drafts × 7 rounds of picks per side, with `CLEAR`, `TALK TO THEIR GM`, `EVALUATE TRADE` disabled at zero.

Ticking **Derrick Brown (DT 96, $10.94M)** on the CAR side and **2029 7th round (AZ)** on theirs:

```
You send · 1 player, 0 picks        You receive · 0 players, 1 pick
CAR SENDS   Derrick Brown (DT)      CAR RECEIVES   2029 7th round
```

Both buttons enable. **Evaluate trade** (`POST /{abbr}/trades/evaluate`) and **Talk to their GM** (`POST /{abbr}/inbox`) were **not clicked** — they are POSTs. The build-a-package half of the row is done.

### 4.2 The 409

`TradeMachineModule.tsx:336-346` posts the package to `/inbox` as a `GM_TALK`; a 409 sets `talkNote.conflict` and renders *“A talk with {team}'s GM is already open. **Open the inbox**”* (`:372-378`), a link to `#franchise-inbox`. **Not exercised:** a 409 needs an already-open talk, which needs a successful POST first — and on this franchise `/inbox` answers **403**, so the path would show “Couldn't open the talk: CAR isn't one of your teams in the franchise registry” instead of the conflict note. That is P8-08-F1 leaking into P8-10.

### 4.3 League Talks — the published transcript (exercised for real)

- **Live DB:** `GET /api/franchise/trade-talks?season=2026` → `{"talks":[]}`; the section renders “No CPU trade talks have been published this season yet.” That is correct: `gm_trade_negotiations` has 0 rows on `madden_mu2026`.
- **Read-only proxy walk:** a second uvicorn on `127.0.0.1:8011` with `DATABASE_URL` set to `madden_tune_p2h` under `options='-c default_transaction_read_only=on'` and a scratch registry in `/tmp` (the live server was never touched or restarted). That database holds 5 published CPU tracks (`kind = CPU`, `published_at` set, all AGREED); the page re-rendered them by rewriting only the `/trade-talks` requests to `:8011`, everything else stayed on the live API.

Results (1440 and 900): five rows — `PHI ↔ DET Week 4 AGREED`, `DAL ↔ WAS Week 4`, `GB ↔ NYG Week 4`, `ATL ↔ CIN Week 3`, `PHI ↔ NYJ Week 3`. Opening the first gives the full transcript:

```
PHI  FIRM  OFFER · Week 4      DET → PHI: FS Stephen Griffin 76 / PHI → DET: SS Marcus Epps 75
“Dana Ramsdell: We have an offer on the table. Here's where we'd start.”
DET  REJECT · Week 4           “Gideon Ashcroft: I can't find a version of this that works for us.”
PHI  MEET  ACCEPT · Week 4     …
FINAL DEAL  FS Stephen Griffin 76 (DET → PHI) / SS Marcus Epps 75 (PHI → DET)
```

Posture badges, actions, one-directional asset lines, quotes and the final deal all render; boxes are 1156 px at 1440 and 732 px at 900 with **no child overflow**; five transcripts open at once still read cleanly. This settles the row's last clause on data the live franchise cannot supply until the CPU trade engine publishes a season's talks on it.

---

## 5. P8-11 — Command Center sweep

All 11 modules (`#franchise-<slug>?team=CAR`) at **2560×1440 (a 1440p display at 100% scaling)**, **1920×1080**, **1440×900** and **900×900**. Tab links all resolve to the right hash and module, `aria-current` follows, and the picker persists a team in `localStorage` (`unknown team` in the URL falls back to the stored team; `AZ`/`WAS` render as “AZ CARDINALS”/“WAS COMMANDERS”).

| Module | Data on screen (live) | 1440 | 900 |
|---|---|---|---|
| Game Center | next game, 4 tiles, pending moves (none), dossier table, matchups (empty until games) | 0 overflow | header only |
| Inbox | **403 error state** (P8-08) | 0 | header only |
| Depth Chart | 3-column slot grid (offense / defense / specials / situational), save disabled | 0 | header only |
| Roster & Cap | 65 players + 2026–2030 cap sheet | 0 | inner-scroll tables (`fo-table-scroll` 1060→732) |
| Free Agency | 100 free agents | 0 | inner-scroll table |
| Contract Room | 29 players, tabs 29/3/26 | 0 | inner-scroll table |
| Trade Machine | 95 + 95 rows, picks; League Talks empty on live | 0 | header only |
| Draft War Room | “no scouting board stored for the 2027 class”, interviews empty, 3 drafts of capital | 0 | header only |
| Training Room | OUT 1 / DOUBTFUL 2 / QUESTIONABLE 0 / ON IR 0, 3-row injury table | 0 | inner-scroll table |
| Report Card | two empty states (no NFLPA card, no opex policies) | 0 | header only |
| Staff Contracts | empty state (“No staff contracts are on file…”) | 0 | header only |

No JavaScript errors on any module except the four inbox 403s; nothing is clipped vertically; the full-page captures are in `shots/`.

### F1 — the site header overflows sideways between 801 px and 1050 px (MED)

`document.documentElement.scrollWidth` measured per width on `#franchise-game-center`:

| viewport | 1440 | 1280 | 1200 | 1100 | 1077 | 1050 | 1000 | 950 | 900 | 850 | 820 | 800 | 768 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| doc width | 1440 | 1280 | 1200 | 1100 | 1077 | **1077** | **1077** | **1077** | **1077** | **1077** | **1077** | 800 | 768 |
| sideways scroll | 0 | 0 | 0 | 0 | 0 | **27** | **77** | **127** | **177** | **227** | **257** | 0 | 0 |

The header's content needs ~1077 px (`.nav-inner` `padding: 0 42px`, `.brand` `margin-right: 68px`, `.nav-links` `gap: 38px`, `index.css:43-56`), but the layout only collapses at `@media (max-width: 800px)` (`index.css:1534-1540`). Between those points the whole page scrolls horizontally and the search button and the LIVE badge sit off-screen — the reader sees a cut-off “FRANCHISE” and nothing after it. This is site-wide chrome, seen from the Command Center; it is the only cause of horizontal page scroll in these walks. Fix: lower the collapse breakpoint to ~1080 px, or let `.nav-links` shrink/wrap in a `@media (max-width: 1080px)` block.

### F2 — the franchise tab bar needs 1147 px and silently clips below 1200 px (LOW-MED)

11 tabs at `gap: 4px`, `padding: 12px 14px`, 9 px uppercase (`FranchisePage.tsx:85-89`) measure **1147 px**; the bar is `overflow-x: auto` with no arrows, fade or wrap. At 1440 the tabs fit exactly (1240/1240 — no slack), at 1200 it is already scrolling (1147/1116), and at 900 three tabs (**TRAINING ROOM, REPORT CARD, STAFF CONTRACTS**) are off-screen with nothing on screen to say so. Fix: a fade/“more” affordance, or wrap to two rows under ~1200 px.

### F3 — at a real 1440p display the Command Center is a 1240 px column with 660 px gutters (LOW, design call)

At 2560×1440 the module content is **1240 px wide starting at x = 660**, i.e. 660 px of empty paper on each side; the tab bar and the header share that width, so the page looks like a tablet layout on a large monitor. This is `max-width: 1240px` on `.fr-head`/`.fr-tabs`/`.fo-view` (`FranchisePage.tsx:74,85,90`). Raising the container (or scaling the type) at ≥1600 px is the usual fix; whether to is a design call for JR.

### F4 — 7–9 px micro-labels, several under AA contrast (LOW, design call)

Smallest rendered text inside the modules is **8 px on Game Center/Roster/Depth Chart** and **7 px in table cells**, and the measured contrast ratios of the meta labels are:

| element | size | colours | ratio |
|---|---|---|---|
| table headers (`PLAYER`, `OVR`, `CAP HIT`, …) | 8 px | `#717176` on `#ebebe7` | **4.04** |
| section eyebrows (`CAR · SEASON 2026 · WEEK 1`, `FRONT OFFICE`, `UNIT V UNIT`) | 8 px | `#e31837` on `#f4f3f0` | **4.25** |
| helper lines (`Click a player, then another…`, `OFFENSE`) | 9 px | `#717176` on `#f4f3f0` | **4.37** |

WCAG AA wants 4.5 for normal text, so the three groups sit just below it while also being the smallest type on the page. The disabled buttons (`CLEAR`, `TALK TO THEIR GM`, `EVALUATE TRADE` before a package, depth-chart `SAVE`) are 1.82:1, which is fine for disabled controls. This is the house style of the whole network, not a Command Center regression; recorded rather than “fixed”.

### Small things worth a line

- The trade-partner dropdown and the team picker offer save abbreviations (`AZ`, `WAS`, `LAR`), while the ticker, team hubs and article text use `ARI`, `WSH`. The alias map handles a typed `ARI`, but the picker never offers it.
- The scouting-dossier “PER GAME (0 V 0 GAMES)” heading reads oddly; everywhere else it says “vs”.
- Empty modules (Report Card, Staff Contracts, Draft War Room interviews) say so in one sentence each — good; they are waiting on data, not broken.
- The depth-chart `SAVE` button is disabled with no explanation until a swap is made (correct, but the only hint is its colour).

---

## 6. What was not exercised (all write-gated)

| Step | Row | Why it was not done |
|---|---|---|
| Take control, open a thread, reply, draft, close, new conversation (pick kind/player/team) | P8-08 | The inbox 403s before any of it; and each is a POST into `inbox_threads`/`inbox_messages` |
| Start a presser, pick a reporter, answer, no-comment, end, edit a turn, approve | P8-09 | No conference exists; starting/casting one writes `press_conferences`/`press_turns` and spends press-model money |
| Evaluate trade, Talk to their GM, hit the 409 | P8-10 | Both POST; the 409 needs a prior write and the inbox 403s anyway |
| Launch the game (dry run included), any roster/cap/contract/depth-chart save | P8-11 | POSTs, and the PC is another session's (`check-d` holds it) |

---

## 7. Reproduce

```bash
mkdir -p /tmp/opencode/check-d-ui && cd /tmp/opencode/check-d-ui
npm init -y && npm i -D playwright && npx playwright install chromium
node sweep.js 1440 900            # P8-11: 11 modules, screenshots + overflow/inner-scroll measurements
node sweep.js 900 900
node walks.js                     # P8-08 / P8-09 / P8-10: page text, controls, API bodies at 1440 and 900
node ladder.js                    # header overflow per width (F1)
node wide.js                      # 2560x1440 and 1920x1080 (F3)
node legible.js                   # smallest text + contrast (F4)
node interact.js                  # tab links, team picker, persistence
# League Talks transcript (second read-only server, never the live one):
cd /home/jrereyi/Project-Mu && MU_FRANCHISE_REGISTRY=/tmp/opencode/check-d-ui/registry_p2h.json \
  DATABASE_URL="host=localhost dbname=madden_tune_p2h user=postgres password=postgres options='-c default_transaction_read_only=on'" \
  .venv/bin/python -m uvicorn server.api:app --host 127.0.0.1 --port 8011 &
cd /tmp/opencode/check-d-ui && \
  REWRITE="http://127.0.0.1:8000/api/franchise/trade-talks->http://127.0.0.1:8011/api/franchise/trade-talks" node talks2.js
```

Helpers live in `lib.js`: viewport setup, the GET-only request guard (aborts and records any non-GET), console/HTTP-error capture, screenshots to `shots/`, and the `REWRITE` pass-through used only for the League Talks proxy.

**Screenshots** (all in `/tmp/opencode/check-d-ui/shots/`): `P8-08-inbox-1440.png`, `P8-08-inbox-900.png`, `P8-08-inbox-team-switch-1440.png`, `P8-08-inbox-team-switch-900.png`; `P8-09-game-center-1440.png`, `P8-09-game-center-900.png`; `P8-10-trade-machine-package-1440.png`, `P8-10-trade-machine-package-900.png`, `P8-10-league-talks-1440.png`, `P8-10-league-talks-900.png`, `P8-10-transcript-open-1440.png`, `P8-10-transcript-open-900.png`, `P8-10-transcripts-all-open-1440.png`, `P8-10-transcripts-all-open-900.png`; `P8-11-<module>-1440.png` and `P8-11-<module>-900.png` for all eleven modules, plus `P8-11-game-center-2560x1440.png`, `P8-11-roster-cap-2560x1440.png`, `P8-11-game-center-1920x1080.png`, `P8-11-roster-cap-1920x1080.png`, `P8-11-header-900-viewport.png`, `P8-11-header-1100-viewport.png`, `P8-11-picker-BUF.png`.

---

## 8. What this means for the close-out

1. **P8-08 and P8-09 are not visual checks that failed — they cannot be run on this franchise.** The live database is the deliberate 2026 CPU league with `user_teams: []` (P1-04: “no user team”), so the inbox refuses every team and no presser exists. Both rows need a franchise with a user team before they can be ticked: the cheapest route is one `CAREER-TUNE-*` clone whose database is registered with a user team, where take-control and a `schedule_week` presser are a handful of writes.
2. **P8-08-F1 is a real defect independent of the checklist**: three parts of the franchise API disagree about an empty `user_teams`, so the UI claims “YOUR TEAM” for all 32 teams while the inbox alone 403s with an internal message. Either an empty registry should mean “allow” everywhere (as `franchise_api` does) or the UI should show a proper empty state; it should not be one endpoint.
3. **P8-10 is 2 of 3**: the package builder and the published-transcript view are verified; the 409 is only verifiable on a franchise where the inbox works.
4. **P8-11 is swept and needs a fix pass** (F1 header overflow, F2 tab bar), which this lane may not do — no code fixes. F3/F4 are JR's design calls.

No files outside `docs/reference/` and the four checklist rows were changed by this session; no database was written, no save touched, no PC claimed, no model spend.
