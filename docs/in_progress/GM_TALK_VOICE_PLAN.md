# Project Mu: GM Talk Voice & Negotiation Dialogue Plan

**Status:** built and on main (2026-11-09); JR's blind voice read pending.

**As built — wave 1 (2026-11-09):** U1 added `gm_profiles.voice_card` / `voice_card_source` (`2027_02_13`), the
32 authored cards, `engines/gm_voices.py`, and card generation at seed, hot-seat hire and the Godfather coach-GM
hire (the trait draw order is restored, so the seeded traits are unchanged); `data/gm_voices_2026.json` is committed
(`!data/gm_voices_2026.json` in `.gitignore`). U2 added the v2 sheet and the loaders; `contract.guaranteed` is
`player_contracts.bonus`, `dead_money` is `cut_penalty`, `apy` is `total_value/length`; `read_books` reads the two
card columns in a separate query; `last3` is newest-first. U3 implemented the verbatim-restatement check against
the previous two lines, because `check_style` has no package object (the plan's wording implied one). A pre-existing
`db/migrate.checksum` line-ending mismatch on `2027_01_16_personality_core` was surfaced and deliberately left
alone (the ledger row stays `c31c1c…`, matching this machine's working tree); it needs its own follow-up.

**As built — wave 2 (2026-11-09):** U4 made CPU talks writer-only (`llm_choose`, `opening_line` and the key-in-JSON
prompt deleted; `choice_source` is always `POLICY`, the turn's `source` is the writer's LLM/TEMPLATE; the model
never sees the options). U5 moved the inbox reply onto the same writer; `message_prompt` stays for
`tools/llm_compare.py` and `tests/test_personas_db.py` but is off the reply path, `league` keeps its argument but
the writer has no league-name check, and JR's `note` rides in the writer's `history` as `JR: <note>`. The
`--no-llm` talk path still builds the v2 sheet (three read-only `recent_form` reads per turn); harmless, noted for
the P3 pacing work.

**Builds on:** `PROJECT_SPEC.md` §2.17 (Negotiation, Talks), §2.21b (Front-Office Inbox), §3.3f (GM agents); [`TRADE_NEGOTIATIONS_PLAN.md`](../done/TRADE_NEGOTIATIONS_PLAN.md) (V2.11); [`UNILATERAL_TRADE_ENGINE_PLAN.md`](../done/UNILATERAL_TRADE_ENGINE_PLAN.md) (U1–U2); [`FRONT_OFFICE_INBOX_PLAN.md`](../done/FRONT_OFFICE_INBOX_PLAN.md); [`CLOSE_OUT_CHECKLIST.md`](../CLOSE_OUT_CHECKLIST.md) (P1-25, P5-07, P8-03, P8-10).

**Goal:** Make CPU-to-CPU trade talks and JR's inbox GM negotiations read like two people who watch tape. Today the engine picks good moves but the words are corporate-analyst filler ("efficiency", "asset value", "framework") because the writer is given no performance facts and each GM's "voice" is a trait template in the same register. This plan gives the writer concrete facts, a per-GM speech card, a writer-only turn (the policy picks the move), and a style contract with a filler/repetition check.

**Delivers:**
- `db/migrations/2027_02_13_gm_voice_cards.sql` and its `db/init_db.sql` mirror: `gm_profiles.voice_card JSONB` and `voice_card_source TEXT`.
- `engines/gm_voices.py` (new): card schema and validation, `template_card`, `render_card`, the authored 2026 card file, `apply_file`, and a CLI (`apply`, `show`, `generate`).
- `data/gm_voices_2026.json` (new): the 32 per-GM speech cards for the 2026 cast.
- `engines/gm_writer.py` (new): the shared line writer and style contract (`write_line`, `check_style`), used by both CPU talks and the inbox.
- `engines/gm_rationale.build_fact_sheet` v2 and `engines/gm_trade_engine.read_books` stats: performance, role, contract structure, team stakes and rivalry in the fact sheet.
- `engines/gm_talks.py`: the policy picks every move; the model only writes the line (opening included).
- `engines/gm_negotiation.py`: the inbox GM reply uses the same writer, card and style contract.
- `engines/gm_personas.py`, `engines/coaching_carousel.py`: every new GM gets a card at hire, with a template fallback that never blocks a hire.

**JR's answers (2026-11-09):**
- **D1:** The policy/engine picks the move; the model only writes the line (writer-only), matching how JR's inbox talks already work.
- **D2:** Rewrite the 32 per-GM cards, and have the hiring pipeline write a card for a GM who does not have one.
- **D3:** The full concrete fact line: season stats, last three games, role, contract structure, team stakes and the rivalry tax.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `engines/gm_talks.py:301-329` `_choice_prompt` asks one model call for `{"choice", "message"}`; `engines/gm_talks.py:347-371` `llm_choose` falls back to the policy when the key is outside the list. | The model spends attention on a legal key and writes a justification for it. In the P1-25 model sample (24 talks) only 4 counters were made and many turns recorded `choice_source = POLICY` because the key fell back. |
| S2 | `engines/gm_rationale.py:27-62` `build_fact_sheet` carries names, OVR, age, dev trait, cap hit, years left, record, direction and needs. It has no stat line, role, contract structure or rivalry. | `engines/gm_rationale.py:184-220` `validate_rationale` rejects any number not on the sheet, so with no stats the writer is pushed into abstractions. |
| S3 | `engines/gm_personas.py:127-130` builds the template voice from three trait flags; `engines/gm_personas.py:155-165` asks the persona model for "one line of style notes for how he talks in trade rationale", capped at 200 characters (`:177-186`). | Every one of the 32 `gm_profiles.voice` values is written in analyst register ("framing moves around asset maximization"). The model mirrors it. |
| S4 | `engines/gm_negotiation.py:326-345` `gm_message` is already writer-only: `kind`, `counter_assets`, `mood` and `posture` are decided before `message_prompt`, and the model answers `{"message"}`. | The inbox already has the shape D1 chooses; CPU talks should match it rather than invent a second pattern. |
| S5 | GM creation paths: `engines/gm_personas.py:264` `insert_gm` (initial seed and `--replace`), `engines/gm_rollover.py:205-240` `_hire_gm` → `make_persona` → `insert_gm`, and `engines/coaching_carousel.py:556` inserts a coach-GM directly when the Godfather offer fills a dual vacancy. | One card field written by `insert_gm` covers seed and hot-seat hires; the Godfather path must be updated separately or it will keep the placeholder "the head coach's own voice". |
| S6 | Stats exist per season and per game: `season_stats` (offense), `season_stat_defense`, `season_stat_blocking`, `season_stat_kicking`, `season_stat_punting`, plus `player_stats` and `game_stat_defense` per game. The 2036 scratch copy holds 394 offensive and 349 defensive season rows. `player_seasons.role` carries "Impact Starter", "Depth Filler", "Veteran Presence", …. | The facts D3 asks for can be loaded without generating anything new. Season aggregates are small enough to load in `read_books`; the last three games are fetched only for the players in the deal. |
| S7 | `engines/gm_negotiation.py:262-269` `message_sheet` and `engines/gm_talks.py:518-522` `_sheet` both call the same pure `build_fact_sheet`. | One sheet change reaches both surfaces; no second copy of the facts. |
| S8 | `engines/gm_talks.py:887-905` `talk_json(public=True)` and `newsroom/roster_moves.py:456` `trade_line` publish stored messages; `engines/inbox/kinds/gm_talk.py` stores `gm_message` on the thread. | Better stored lines flow to the site, League Talks and articles with no further wiring. |

## 1. Scope

**In:**
- A per-GM speech card (`gm_profiles.voice_card`), authored for the 2026 cast and generated at hire for every later GM.
- Fact sheet v2: season performance line, last three games, role, contract structure, team stakes and the rivalry/contention tax.
- Writer-only turns in CPU talks: the policy picks, `gm_writer.write_line` speaks.
- The same writer for JR's inbox GM negotiations.
- A style contract: short spoken lines, no banned filler, no repeated phrasing, one retry, template fallback.
- A P1-25 re-run and a blind read by JR of old vs new lines.

**Out (non-goals):**
- No change to trade valuation, the rivalry tax matrix, the price ladder, budgets, stalls or carries; the mechanics that produced the P1-25 convergence numbers stay as they are.
- No new stat generation or backfill; the plan reads what the archive and Madden ingest already write.
- No article or Agent Wire prompt changes; their GM quotes come from the stored talks, so they inherit the voice (a separate read in P8-03).
- No draft-day or Rounds 2–7 talk changes (those are one-shot and never open a talk).
- No UI change; `talk_thread` and League Talks render `message` unchanged.

## 2. Design

### 2.1 The voice card (`gm_profiles.voice_card`)

Migration `2027_02_13_gm_voice_cards.sql` (mirrored at the end of `db/init_db.sql`, then `python3 -m db.migrate sync-init-db`):

```sql
ALTER TABLE gm_profiles
  ADD COLUMN IF NOT EXISTS voice_card JSONB,
  ADD COLUMN IF NOT EXISTS voice_card_source TEXT
    CHECK (voice_card_source IN ('AUTHORED', 'LLM', 'TEMPLATE'));
```

The card is a JSON object:

```json
{
  "register": "One sentence on how he talks, in speech terms, not adjectives.",
  "samples": ["A line he would actually say.", "A second, different line."],
  "tics": ["A verbal habit.", "Another one."],
  "avoids": ["A word or phrase he never uses."],
  "wants": "What he wants out of a call."
}
```

`engines/gm_voices.py`:

- `parse_card(obj) -> dict | None` — validates: `register` 10–200 chars; `samples` 2–3, each 8–160; `tics` 1–3; `avoids` 1–3; `wants` 5–120. Extra keys are dropped.
- `template_card(traits, role="GM") -> dict` — a deterministic card from traits (aggressiveness / risk / pick affinity / youth bias), with role-specific registers for "GM" and "COACH_GM". Never fails; the hiring fallback.
- `render_card(card) -> str` — the prompt block:
  `REGISTER: …` / `HOW HE SOUNDS: "…" / "…"` / `TICS: …` / `NEVER SAYS: …` / `WANTS: …`.
- `authored_card(abbr, season) -> dict | None` — the card from `data/gm_voices_2026.json` when its `season` matches.
- `apply_file(cur, path, season, *, dry_run=False, force=False) -> dict` — updates the open GM of each team whose abbreviation is in the file; refuses (and reports) a name mismatch unless `force`; writes `voice` (a rendered one-liner, for old callers) and `voice_card`, source `AUTHORED`.

CLI:

```
python3 -m engines.gm_voices show --team ATL [--dsn DSN]
python3 -m engines.gm_voices apply --file data/gm_voices_2026.json --season 2026 [--dry-run] [--force] [--dsn DSN]
python3 -m engines.gm_voices generate --team ATL [--no-llm] [--dsn DSN]
```

### 2.2 The 32 authored cards and the hiring generator

`data/gm_voices_2026.json`:

```json
{"season": 2026,
 "cards": {"ATL": {"gm": "Beth Novak", "register": "…", "samples": ["…", "…"],
                    "tics": ["…"], "avoids": ["…"], "wants": "…"}, "…": {}}}
```

The 32 cards are written against Appendix A. They must be speech, not personality adjectives: "Calls players by last name. Says 'look' before he disagrees. Never says 'asset'." JR reads the file before it is applied.

Hiring, so a later GM is never voiceless:

- `engines/gm_personas.persona_prompt` also asks for `"voice_card"`; `parse_persona` validates it with `gm_voices.parse_card` and rejects the whole answer only if name/bio/voice are bad (a bad card falls back alone).
- `make_persona` returns `voice_card` and `voice_card_source`. It prefers `authored_card(abbr, season)`, then the model's card (source `LLM`), then `template_card` (source `TEMPLATE`).
- `insert_gm` writes both columns. `seed`, `--replace` and `gm_rollover._hire_gm` need no change beyond the persona it already receives.
- `engines/coaching_carousel.py:556` (the Godfather dual-vacancy insert) writes `voice_card = template_card(..., role="COACH_GM")` and source `TEMPLATE` (or the model's card when the caller has one).
- `gm_personas backfill-bios` also fills a missing card (`voice_card IS NULL`) with `template_card`, so no legacy row stays voiceless.

### 2.3 Fact sheet v2

`engines/gm_rationale.build_fact_sheet(b, deal, recent=None)` stays pure and gains:

- `teams[side]["stakes"] = {"record", "last3": ["W 24-17", "L 20-23", …], "streak", "seed"}`
  (seed = conference position by wins, computed from `b["records"]`).
- `teams[side]["rivalry"] = {"relation": "division" | "conference" | null, "tax": 0.0–0.75}`
  from `gm_trade_engine.rivalry` and `rival_tax`.
- `assets[].role` — `player_seasons.role`.
- `assets[].contract = {"apy", "guaranteed", "years_left", "dead_money"}`.
- `assets[].performance = {"season": "72 rec, 986 yds, 7 TD", "games": 15, "last3": ["6-88-1 @ SEA", …]}`
  with the stat line chosen by position group:
  QB pass, RB rush+rec, WR/TE rec, OL `sacks_allowed`, DL/LB/DB `season_stat_defense`
  (tackles/sacks/INTs), K `season_stat_kicking`, P `season_stat_punting`.
  **A player with no stat row gets `performance: null`** (the 2026-11-09 spike found bench players with none in
  the archive), and the writer must not invent one. `last3` reads only games **before** the given week; in a
  retrospective replay the season totals are end-of-season (a live league's are as of that week).

`engines/gm_trade_engine`:

- `load_season_stats(cur, season) -> {player_id: dict}` — one query over the `season_stat_*` tables for the season, keyed by player. Small (≈750 rows in the 2036 archive).
- `load_team_form(cur, season, week) -> {team_id: {"last3", "streak"}}` — completed games before `week`.
- `recent_form(cur, player_ids, season, week) -> {player_id: [line, line, line]}` — the last three games from `player_stats` and `game_stat_defense`, only for the players in the deal.
- `read_books` adds `b["season_stats"]` and `b["form"]`; `b["gm"][tid]` also carries `voice_card` and `voice_card_source`.

`message_sheet` (inbox) and `_sheet` (talks) pass `recent=recent_form(cur, ids, season, week)`; `store()`'s rationale call passes `recent=None` (the proposal row does not need it).

### 2.4 Writer-only CPU talks (`engines/gm_talks.py`)

- `advance_talk` keeps `policy_choice` (and `_opening_choice` on turn 1) as the only chooser. `llm_choose` is no longer called by the talk path; the module keeps it only if another caller still needs it (none does after this plan).
- `_choose` becomes: pick `opt` with the policy, then `text, source = gm_writer.write_line(...)` with the move fixed (`action`, `posture`, assets) and a `fallback=_template_line(persona, opt, sheet)`. Returns `(opt, text, source)`.
- `open_talk` writes its opening line through `gm_writer.write_line` too; the package stays the search's choice.
- `_history` keeps the last three lines verbatim (not six), so the writer reacts to the man, not the deal.
- A `scene` dict is built once per turn: `week`, `days_to_deadline`, `who_called`, `mood`, `posture`, `stakes` (from the sheet) and the move.

### 2.5 The shared writer and style contract (`engines/gm_writer.py`)

```python
STYLE_BANNED = re.compile(r"\b(efficiency|asset value|framework|process|leverage|value proposition|"
                          r"market efficiency|floor[- ]raising)\b", re.I)
def check_style(text, prior, max_words=45) -> tuple[bool, str | None]
def write_line(llm, *, card_text, sheet, history, move, scene, fallback,
               max_words=45, timeout=None) -> tuple[str, str]
```

- `card_text` is `gm_voices.render_card(card)` (the caller renders it; the writer does not import `gm_voices`, so
  wave 1's units stay independent). `max_words` comes from the card when it sets one, else 45.
- `move` is `{"action": "OFFER" | "ACCEPT" | "COUNTER" | "REJECT" | "WALK_AWAY", "posture": str | None,
  "gets": [label, …], "gives": [label, …]}`; the labels are already-legal text from `asset_label`.
- `scene` is `{"week", "deadline_week", "who_called", "mood", "stakes", "relationship"}`; `stakes` and
  `relationship` are plain strings taken from the sheet.
- `check_style`: rejects a line over `max_words`, a banned phrase, more than one ellipsis, a sentence that repeats a
  5-word run from either of the previous two lines, and a line that restates the package verbatim. It runs *after*
  `gm_rationale.validate_rationale`, so facts still bind.
- `write_line`: one model call with `card_text`, the scene, the fact sheet JSON, the last three lines and
  the style rules; answers `{"message": "..."}`. On a failed fact or style check it retries once with the reason
  appended; then returns `(fallback, "TEMPLATE")`. `source` is `"LLM"` or `"TEMPLATE"`. With `llm=None` it
  returns the fallback immediately. `timeout` uses `gm_talks.call_with_timeout` (the inbox keeps its 8 s budget).

### 2.6 Inbox negotiations (`engines/gm_negotiation.py`)

`message_prompt` is replaced by a thin adapter that builds the same card/sheet/scene and calls
`gm_writer.write_line(... fallback=the existing template ...)`. `gm_message` gains one optional argument,
`recent=None`, which its caller fills with `recent_form(cur, ids, season, week)` (the caller at
`engines/gm_negotiation.py:551` already holds `cur`); the timeout behaviour, the reply text and the
`(text, source)` shape are unchanged, so `engines/inbox/kinds/gm_talk.py` and the stored thread need no change.

### 2.7 Evidence: the P1-25 re-run and the blind read

After the build, re-run the P1-25 replay on a fresh scratch copy of `madden_2036` (all teams CPU, history cleared,
model on), weeks 4–8, and compare with `verify_output/p1_25/voice/`:

- the convergence mix should stay close to the 2026-11-09 sample (9/9/6, 62.5% breakdown) because the mechanics
  are untouched; a large shift means the writer changed a move, which it must not;
- `choice_source` should record no key fallbacks, and the `TEMPLATE` share should be under 10%;
- JR blind-reads six lines (three old, three new) and scores voice, tics and whether the two men sound different.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Facts + card make every call slower and costlier. | Time the 24-talk replay; record p50 per turn and token counts. | Drop `last3` from the sheet first; keep season totals and the card. |
| R2 | The style check over-rejects, so most lines fall back to the template. | Log `source` counts per replay; the R2 threshold is a 10% template share. | Trim `STYLE_BANNED`, raise the word cap, or retry without the repetition rule. |
| R3 | A card written for a GM's old traits does not fit him after evolution changes his sliders. | The card is attached to the person, not the traits; a personality change does not rewrite speech. JR reads the 32 cards. | Re-author the card in the file; `apply --force` on that team only. |
| R4 | A hire happens in a league-office run with no model key and the card step fails. | `template_card` is pure and is the fallback in `make_persona` and the Godfather path. | A hire must never fail: log `TEMPLATE` and continue. |
| R5 | Applying the 32 cards to a live database is a write. | `gm_voices apply --dry-run` prints every team and match before writing. | `madden_mu2026` is applied only after JR has read the file. |

**Spike first:** author three cards (one shark, one measured, one old-school), add the stats block to the sheet for
one week, and have JR read six lines from one scratch replay. About 30 minutes of build time. If the lines still
read flat, stop before writing the other 29 cards.

**Spike result (run 2026-11-09 on `madden_tt_voice`, 2036 week 5, Julius Hobbs for a 2037 R4):** passed. The cards
and the stat block produced six distinct, spoken lines with real facts, e.g. measured: *"Nine and a half sacks is
what the tape says Julius Hobbs gives you. I'll send him your way for your 2037 fourth-round pick."*; old-school:
*"The kid's tough as hell on Sundays and holds down a locker room."* Three findings feed the units: (1) a bench
player can have no stat row in a replay — `performance` must be `null` and the writer must not invent one; (2) the
archive has the full season, so `recent_form` must read only games **before** the replayed week, and a retrospective
replay's season totals are end-of-season (a live league's are current); (3) a "pauses before the number" tic made
the model write ellipses, so `check_style` also rejects more than one ellipsis per line.

## 4. Tests (synthetic, no live DB)

- `tests/test_gm_voices.py` — `parse_card` accepts the documented shape and rejects a short register, one sample,
  a missing `avoids` and a 200-character sample; `template_card` is deterministic and differs by aggressiveness;
  `render_card` carries every section; `authored_card` matches only its season; `apply_file` refuses a name
  mismatch and writes both columns on a scripted cursor; the migration text adds both columns.
- `tests/test_gm_writer.py` — a clean line passes; a line with "efficiency" or a repeated 5-word run fails;
  a 60-word line fails; the retry prompt carries the reason; a second failure returns the fallback; `llm=None`
  returns the fallback; `timeout` is passed to `call_with_timeout`.
- `tests/test_gm_personas.py` — `make_persona` prefers the authored card, then the model's, then the template;
  `parse_persona` keeps a good card and falls back alone on a bad one; `insert_gm` writes `voice_card` and its
  source; `backfill-bios` fills a missing card.
- `tests/test_gm_rollover.py` — a hot-seat hire inserts a GM with a non-null card and source.
- `tests/test_godfather_gates.py` — the dual-vacancy insert writes a `COACH_GM` template card.
- `tests/test_gm_rationale.py` — the sheet carries `stakes`, `rivalry`, `role`, `contract` and `performance`;
  the stat line is chosen by position; `validate_rationale` still accepts a line that quotes a sheet stat and
  rejects an invented number.
- `tests/test_gm_trade_engine.py` — `load_season_stats` and `load_team_form` read the documented columns;
  `recent_form` returns at most three completed games before the week; `read_books` carries `season_stats`, `form`
  and each GM's card.
- `tests/test_gm_talks.py` — the policy alone picks every move (a model that returns a different key cannot change
  it); the writer receives the card, sheet and move; a style failure falls back to the template; the opening line
  is written through the writer.
- `tests/test_gm_negotiation.py` — the inbox reply uses the shared writer and the GM's card; the timeout still
  returns the template; the reply text and source shape are unchanged.
- `tests/test_inbox_kinds_gm_talk.py` — an inbox GM reply still stores `gm_message` and the queued move.

Run with `pytest -m "not integration" tests/test_gm_voices.py tests/test_gm_writer.py tests/test_gm_personas.py
tests/test_gm_rollover.py tests/test_godfather_gates.py tests/test_gm_rationale.py tests/test_gm_trade_engine.py
tests/test_gm_talks.py tests/test_gm_negotiation.py tests/test_inbox_kinds_gm_talk.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Voice cards, the 32 authored cards and the hiring generator

- **Owns:** `db/migrations/2027_02_13_gm_voice_cards.sql` (new), `db/init_db.sql`, `engines/gm_voices.py` (new),
  `data/gm_voices_2026.json` (new), `engines/gm_personas.py`, `engines/coaching_carousel.py`,
  `tests/test_gm_voices.py` (new), `tests/test_gm_personas.py`, `tests/test_gm_rollover.py`,
  `tests/test_godfather_gates.py`
- **Reads:** `engines/gm_personas.py`, `engines/gm_rollover.py`, `engines/coaching_carousel.py`, `db/migrate.py`,
  this plan's Appendix A
- **Depends on:** none
- **Done when:** the migration and its `init_db.sql` mirror exist; `gm_voices apply --dry-run` reports all 32
  teams; the 32 cards parse; a seeded and a hot-seat-hired GM both carry a card; the Godfather path writes one.
- **Verify:** `pytest -m "not integration" tests/test_gm_voices.py tests/test_gm_personas.py
  tests/test_gm_rollover.py tests/test_godfather_gates.py`; `python3 -m engines.gm_voices show --team ATL --dsn
  "host=localhost dbname=madden_test user=postgres password=postgres"` prints a card after the apply.

### U2: Fact sheet v2 and the stats loaders

- **Owns:** `engines/gm_rationale.py`, `engines/gm_trade_engine.py`, `tests/test_gm_rationale.py`,
  `tests/test_gm_trade_engine.py`
- **Reads:** `engines/gm_rationale.py`, `engines/gm_trade_engine.py` (`read_books`, `fact_sheet`, `rival_tax`),
  `db/init_db.sql` for the stat table shapes
- **Depends on:** none
- **Done when:** the sheet carries `performance`, `role`, `contract`, `stakes` and `rivalry`; `read_books` carries
  `season_stats`, `form` and each GM's `voice_card`; `recent_form` is bounded to three completed games.
- **Verify:** `pytest -m "not integration" tests/test_gm_rationale.py tests/test_gm_trade_engine.py`

### U3: The shared writer and the style contract

- **Owns:** `engines/gm_writer.py` (new), `tests/test_gm_writer.py` (new)
- **Reads:** `engines/gm_talks.py` (`_template_line`, `call_with_timeout`), `engines/gm_negotiation.py`
  (`message_prompt` for the existing register), `engines/gm_rationale.py` (`validate_rationale`)
- **Depends on:** none
- **Done when:** `write_line` builds a prompt from a card, scene, sheet, history and fixed move; a fact or style
  failure retries once and then returns the fallback; `check_style` rejects the banned filler, a repeated run and
  an over-long line.
- **Verify:** `pytest -m "not integration" tests/test_gm_writer.py`

### U4: Writer-only CPU talks

- **Owns:** `engines/gm_talks.py`, `tests/test_gm_talks.py`
- **Reads:** `engines/gm_talks.py`, `engines/gm_writer.py`, `engines/gm_trade_engine.py` (`read_books` outputs)
- **Depends on:** U1, U2, U3
- **Done when:** every talk turn's move comes from `policy_choice`/`_opening_choice` only; the model cannot change
  it; the opening and every response go through `write_line`; a model reply key is never parsed.
- **Verify:** `pytest -m "not integration" tests/test_gm_talks.py tests/test_gm_trade_engine.py`

### U5: Inbox negotiations on the shared writer

- **Owns:** `engines/gm_negotiation.py`, `tests/test_gm_negotiation.py`, `tests/test_inbox_kinds_gm_talk.py`
- **Reads:** `engines/gm_negotiation.py`, `engines/gm_writer.py`, `engines/inbox/kinds/gm_talk.py`
- **Depends on:** U1, U2, U3
- **Done when:** `gm_message` builds the card/sheet/scene and calls `write_line`, takes the optional `recent` map
  from its caller, and still applies the 8 s budget; the stored `gm_message` and queued move are unchanged.
- **Verify:** `pytest -m "not integration" tests/test_gm_negotiation.py tests/test_inbox_kinds_gm_talk.py`

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2, U3 | — | No shared files: cards/migration, facts, writer. Each is independently verifiable. |
| 2 | U4, U5 | U1–U3 | Talks and the inbox consume the card, the facts and the writer; they touch different files (`gm_talks.py` vs `gm_negotiation.py`). |

**After wave 2 (orchestrator, not a unit):** run §2.7 — the P1-25 replay on a fresh scratch copy, the source-mix
check, and JR's blind read of six lines. The plan is not done until JR signs off on the voice.

**File ownership check:**

| File | Unit |
|---|---|
| `db/init_db.sql` | U1 |
| `db/migrations/2027_02_13_gm_voice_cards.sql` | U1 |
| `data/gm_voices_2026.json` | U1 |
| `engines/coaching_carousel.py` | U1 |
| `engines/gm_negotiation.py` | U5 |
| `engines/gm_personas.py` | U1 |
| `engines/gm_rationale.py` | U2 |
| `engines/gm_talks.py` | U4 |
| `engines/gm_trade_engine.py` | U2 |
| `engines/gm_voices.py` | U1 |
| `engines/gm_writer.py` | U3 |
| `tests/test_gm_negotiation.py` | U5 |
| `tests/test_gm_personas.py` | U1 |
| `tests/test_gm_rationale.py` | U2 |
| `tests/test_gm_rollover.py` | U1 |
| `tests/test_gm_talks.py` | U4 |
| `tests/test_gm_trade_engine.py` | U2 |
| `tests/test_gm_voices.py` | U1 |
| `tests/test_gm_writer.py` | U3 |
| `tests/test_godfather_gates.py` | U1 |
| `tests/test_inbox_kinds_gm_talk.py` | U5 |

## 7. Spec changes when built

- §2.17 **Negotiation**: `gm_message` uses `gm_writer.write_line` with the GM's `voice_card`, the fact sheet's
  `performance`/`stakes`/`rivalry`, and the style contract; the timeout is unchanged.
- §2.17 **Talks**: the policy alone picks each move; `gm_writer.write_line` speaks every turn (opening included);
  `llm_choose`/the key-in-JSON prompt leave the talk path.
- §2.17 **Search / Acceptance / fact sheet**: `build_fact_sheet` gains `performance` (season line, last three
  games), `role`, `contract`, `stakes` and `rivalry`; `read_books` loads `season_stats`, `form` and the GM card;
  `load_season_stats`, `load_team_form` and `recent_form` are new.
- §3.3f **GM agents**: `gm_profiles.voice_card JSONB` and `voice_card_source`; `gm_personas` fills them at seed
  and hire, `coaching_carousel` at a coach-GM hire, `gm_personas backfill-bios` for a legacy row.
- §1.1 Repository Layout: `engines/gm_voices.py`, `engines/gm_writer.py`, `data/gm_voices_2026.json`,
  `db/migrations/2027_02_13_gm_voice_cards.sql`, the new tests; `engines/gm_personas.py`, `engines/gm_talks.py`,
  `engines/gm_negotiation.py`, `engines/gm_rationale.py`, `engines/gm_trade_engine.py` descriptions updated.
- §1.2 commands: `python3 -m engines.gm_voices show|apply|generate`.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Apply the 32 cards to the canonical franchise database `madden_mu2026` now, or only at P7-00c when `league_office setup` runs? | Apply after JR has read `data/gm_voices_2026.json`, with `--dry-run` first; the canonical league should carry them from its first save load. |
| Q2 | Keep `llm_choose` for any caller after the talks move to writer-only? | Remove it from the talk path and delete it if no caller remains; the tests that exercise a key-outside-the-list fallback move to `gm_writer`'s retry tests. |
| Q3 | Should the authored cards also be the source of GM quotes in articles (`roster_moves.trade_line`)? | No extra work: the quoted lines are the stored talk messages, so they improve with the writer automatically. Re-check in P8-03. |

**Answered by JR (2026-11-09): all three as recommended.** The 32 cards are applied to `madden_mu2026` after JR
reads the file (dry run first); `llm_choose` leaves the talk path and is deleted if no caller remains; articles need
no separate work.

---

## Appendix A — The 2026 GM cast (for the 32 cards)

Traits are from `gm_profiles` (open tenure, 2026), read 2026-11-09. The current `voice` is the trait template from
`gm_personas._style`, not a card. Write each card against the man, not the numbers.

| Team | GM | Agg | Risk | Picks | Youth |
|---|---|---|---|---|---|
| ATL | Beth Novak | 0.79 | 0.82 | 1.04 | 0.86 |
| AZ | Hollis Lockhart | 0.22 | 0.47 | 0.66 | 0.21 |
| BAL | Ulysses Dunmore | 0.34 | 0.72 | 1.16 | 0.25 |
| BUF | Silas Tremblay | 0.43 | 0.52 | 1.25 | -0.42 |
| CAR | Silas Jessup | 0.51 | 0.67 | 1.00 | 0.70 |
| CHI | Xavier Crenshaw | 0.49 | 0.93 | 1.31 | 0.60 |
| CIN | Renee Northcutt | 0.67 | 0.31 | 0.99 | -0.27 |
| CLE | Winston Ellsworth | 0.83 | 0.87 | 1.09 | 0.13 |
| DAL | Jerome Underhill | 0.45 | 0.40 | 0.80 | -0.89 |
| DEN | Hollis Quaid | 0.58 | 0.91 | 1.35 | -0.04 |
| DET | Gideon Ashcroft | 0.41 | 0.65 | 1.20 | -0.54 |
| GB | Priya Vasquez | 0.72 | 0.19 | 0.84 | -0.79 |
| HOU | Vera Abernathy | 0.66 | 0.44 | 1.08 | -0.62 |
| IND | Felicia Blackwood | 0.31 | 0.15 | 1.12 | -0.18 |
| JAX | Sterling Whitlock | 0.26 | 0.48 | 0.98 | 0.50 |
| KC | Dana Pemberton | 0.26 | 0.35 | 0.93 | -0.32 |
| LAC | Tamsin Delacroix | 0.15 | 0.07 | 1.06 | 0.56 |
| LAR | Harlan Zielinski | 0.84 | 0.59 | 1.23 | 0.30 |
| LV | Zora Zamora | 0.70 | 0.85 | 0.94 | 0.35 |
| MIA | Tamsin Fontaine | 0.39 | 0.79 | 0.89 | -0.12 |
| MIN | Lorraine Bellweather | 0.53 | 0.29 | 1.27 | -0.61 |
| NE | Everett Yarborough | 0.21 | 0.27 | 1.19 | -0.36 |
| NO | Vera Greer | 0.60 | 0.10 | 0.67 | 0.09 |
| NYG | Keaton Kingsley | 0.55 | 0.76 | 0.72 | 0.76 |
| NYJ | Desmond Oyelaran | 0.87 | 0.75 | 0.75 | -0.70 |
| PHI | Dana Ramsdell | 0.77 | 0.57 | 0.70 | 0.41 |
| PIT | Omar Wakefield | 0.30 | 0.62 | 0.85 | -0.74 |
| SEA | Zeke Marchetti | 0.63 | 0.24 | 0.81 | 0.80 |
| SF | Zora Iverson | 0.14 | 0.18 | 0.77 | -0.11 |
| TB | Hollis Ingram | 0.74 | 0.38 | 1.14 | -0.50 |
| TEN | Everett Lindqvist | 0.37 | 0.13 | 0.91 | 0.04 |
| WAS | Yolanda Radcliffe | 0.18 | 0.55 | 1.30 | 0.66 |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | worktree-agent-abb217dcf07714c1d | .claude/worktrees/agent-abb217dcf07714c1d | merged | 0994421 |
| U2 | 1 | worktree-agent-a2ba86f0829b7b5e2 | .claude/worktrees/agent-a2ba86f0829b7b5e2 | merged | b758c5b |
| U3 | 1 | worktree-agent-a5038c0efc02e1496 | .claude/worktrees/agent-a5038c0efc02e1496 | merged | 4485741 |
| U4 | 2 | worktree-agent-a6d640cabdc8747fe | .claude/worktrees/agent-a6d640cabdc8747fe | merged | d93b819 |
| U5 | 2 | worktree-agent-a606cf569d390dbfd | .claude/worktrees/agent-a606cf569d390dbfd | merged | 69bd764 |
