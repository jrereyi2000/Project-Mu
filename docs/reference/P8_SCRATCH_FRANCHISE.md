# P8-08 / P8-09 scratch franchise (CAR as the user team)

**Disposable.** This is a throwaway copy of the live franchise made so the P8-08 (Inbox) and, later, P8-09 (press
conference) page walks can be done with a real **user team** instead of the live franchise's 403-only state. It never
writes the live franchise, the live registry, the live site on `:8000`, `madden_mu2026` or any save, and nothing in it
is worth keeping: the database can be dropped and rebuilt in about ten seconds at any time.

JR approved it on 2026-11-12 ("We can make that scratch franchise where Carolinas set as the user team for P8 08"),
after `docs/reference/P8_PAGE_CHECKS_2026-11-12.md` §2 showed the Inbox module stuck on the 403 the live registry
produces. The convention it exercises is the one `p8-08-rootcause` implements (M-0885): an **empty `user_teams`
means nobody is yours**, and `db/franchises.py` `user_team_abbrs` / `user_team_refusal` / `registry_team_is_user`
is the only implementation of it.

## What it is

| Piece | Value |
|---|---|
| Database | `mu_scratch_p808` — a `pg_dump` copy of `madden_mu2026`, never a `madden_*` name |
| Registry | `/tmp/opencode/p808/franchises.json` — `user_teams: ["CAR"]`, the live `data/franchises.json` untouched |
| API | `http://127.0.0.1:8011`, a second uvicorn; the live site stays on `:8000` |
| Server log / pid | `/tmp/opencode/p808/server.log`, `/tmp/opencode/p808/server.pid` |
| User team | `CAR` (Carolina), the only team the registry marks as JR's |

Unlike the trade-talks proxy in `P8_PAGE_CHECKS_2026-11-12.md` §4 (read-only, `default_transaction_read_only=on`),
this instance is **writable**, because P8-08's walk needs exactly one take-control POST. Every write it takes lands
in `mu_scratch_p808` and nowhere else.

## Commands

The whole thing is one script with `--help`:

```bash
tools/p8_scratch_franchise.sh up       # create/copy the DB, write the registry, start :8011, take control, prove it
tools/p8_scratch_franchise.sh status   # DB + registry + server state and the CAR-vs-CHI checks
tools/p8_scratch_franchise.sh refresh  # stop :8011, drop and re-copy the DB from the live source, restart, take control
tools/p8_scratch_franchise.sh take-control [ROLE]   # the one POST the page needs (default GM); idempotent
tools/p8_scratch_franchise.sh down     # stop :8011 only (DB and registry stay, for a later walk)
tools/p8_scratch_franchise.sh drop     # stop :8011 and DROP DATABASE mu_scratch_p808
```

`up` on an already-running scratch is a no-op apart from the checks. `refresh` is the way back to a clean copy after a
walk has left state behind. Env overrides: `MU_SCRATCH_SOURCE_DB` (default `madden_mu2026`), `MU_PG_CONTAINER`
(default `madden-db`), `MU_SCRATCH_DIR` (default `/tmp/opencode/p808`), `MU_SCRATCH_PORT` (default `8011`).

### The same steps by hand

Postgres runs in the `madden-db` container, so there is no host `psql`/`pg_dump`:

```bash
# 1. the scratch database, a copy of the live one (no CREATE DATABASE ... TEMPLATE: the live server holds connections)
docker exec madden-db psql -U postgres -d postgres -c "CREATE DATABASE mu_scratch_p808"
docker exec madden-db pg_dump -U postgres -d madden_mu2026 \
  | docker exec -i madden-db psql -q -v ON_ERROR_STOP=1 -U postgres -d mu_scratch_p808

# 2. the scratch registry, so the format is db.franchises' own (then the db is pointed at the scratch copy)
MU_FRANCHISE_REGISTRY=/tmp/opencode/p808/franchises.json .venv/bin/python - <<'PY'
from db import franchises as f
with f.editing_registry() as reg:
    entry = f.register(reg, 1287775425, name="P8-08 scratch (CAR user team)",
                       user_teams=["CAR"], save_name="CAREER-MUBASE")
    entry["db"] = "mu_scratch_p808"
PY

# 3. the second server
MU_FRANCHISE_REGISTRY=/tmp/opencode/p808/franchises.json \
DATABASE_URL="host=localhost dbname=mu_scratch_p808 user=postgres password=postgres" \
  .venv/bin/python -m uvicorn server.api:app --host 127.0.0.1 --port 8011
```

`1287775425` is the franchise's `league_state.madden_league_id` (the registry key), read from the copy; the script
reads it rather than hard-coding it. `db.register` would name the database `madden_<slug>`, so the script overrides
`db` with the scratch name — the JSON shape (and its `created_at` / `saves` fields) is the real one.

## URLs

| What | URL |
|---|---|
| The Inbox tab the P8-08 walk clicks | `http://127.0.0.1:8011/#franchise-inbox?team=CAR` |
| CAR overview (expect `user_controlled: true`) | `http://127.0.0.1:8011/api/franchise/CAR/overview` |
| CHI overview (expect `user_controlled: false`) | `http://127.0.0.1:8011/api/franchise/CHI/overview` |
| CAR inbox (expect 200) | `http://127.0.0.1:8011/api/franchise/CAR/inbox` |
| CAR actors (expect 200) | `http://127.0.0.1:8011/api/franchise/CAR/inbox/actors` |
| CHI inbox (expect 403, and that is correct) | `http://127.0.0.1:8011/api/franchise/CHI/inbox` |
| CAR presser (P8-09: expect a `USER` `SCHEDULED` conference) | `http://127.0.0.1:8011/api/franchise/CAR/press` |
| CAR press history (P8-09: empty until a conference closes) | `http://127.0.0.1:8011/api/franchise/CAR/press/history` |

The site is served from `Mu_Network/dist`, so a changed frontend needs `cd Mu_Network && npm run build` before the
walk; the scratch server serves the same built site as `:8000`.

## Proof recorded on 2026-11-12 (21:1x–21:3x)

`tools/p8_scratch_franchise.sh up` / `status` against `mu_scratch_p808`:

```
db:       mu_scratch_p808 (present)          -- 216 tables, schema_migrations head = 2027_02_30_fact_gate
registry: /tmp/opencode/p808/franchises.json -- active 1287775425, db mu_scratch_p808, user_teams [CAR]
server:   http://127.0.0.1:8011
check:    CAR user_controlled=True, CHI user_controlled=False, CAR inbox=200, CHI inbox=403
actors:   {"controlled":[{"role":"GM","id":"5","name":"Silas Jessup"}], ...}
```

The one write, the take-control the page needs (a person, not a data edit):

```
POST /api/franchise/CAR/inbox/actors  {"role":"GM","persona_id":"5"}
  -> {"role":"GM","id":"5","name":"Silas Jessup"}  [201]
```

After it, `GET /api/franchise/CAR/inbox` returns four `WAITING_JR` threads (one `VISION`, three `CONTRACT`) and
`unread: 4`; all of them exist only in `mu_scratch_p808`. The live database still read 0 `controlled_personas` and
0 `inbox_threads` when this was written, and `data/franchises.json` still had `user_teams: []`.

The live site on `:8000` was never restarted and never written; its `overview` still showed the pre-fix
`user_controlled: true` for both teams because that process predates `p8-08-rootcause`'s commit — the scratch server
runs the current tree.

## P8-09 presser — the one approved cast (2026-11-12)

JR approved **one** presser cast into this scratch database so P8-09 can be walked too. It was made at 21:4x and
nothing of it touched `madden_mu2026`.

- **What was cast.** `newsroom.press_room.schedule_week(cur, 2026, 1)` — the product path — with
  `press_cast.cast_week` narrowed to CAR's week-1 game (**game 8, CHI at CAR**) so no other game's conference rows
  or game scripts are built. Two rows resulted: **CAR `USER` SCHEDULED** (subject: head coach Dave Canales) and
  CHI `AGENT` SCHEDULED (Ben Johnson), each with three beat reporters in the room.
- **The one model round.** The CAR conference's first round of hands was drafted (what `start_user` would otherwise
  do on the page's **start** click): **`gemini-3.8-flash`, 3 calls, 8,449 input / 312 output tokens**, recorded as
  three `llm_usage` rows (`source = press`) in `mu_scratch_p808`; the conference row reads `calls = 3`. No other
  model call was made; the CHI row is inert (`calls = 0`).
- **One fixture write the page needs.** `GET /api/franchise/{abbr}/press` reads the team's **last completed game**,
  and the copy's week 1 had CAR–CHI still `SCHEDULED` 0–0 (JR's own game is the one the live franchise has not
  played). So game 8 is marked **`COMPLETED` 24–17 CAR** in `mu_scratch_p808` only, and the conference's frozen
  facts were rebuilt from it. **That score is fabricated for the fixture**; it exists nowhere else, and `refresh`
  (a fresh copy) removes it.
- **What answers now** on `:8011`: `GET /api/franchise/CAR/press` → `conference` id 1, `team CAR`, `opponent CHI`,
  `controller USER`, `status SCHEDULED`, `result W`, `score 24-17`, 3 attendees with 3 hands, plus
  `opponent_presser {conference_id: 2, status: SCHEDULED}`; `GET /api/franchise/CAR/press/history` → empty until a
  conference closes; `GET /api/franchise/CHI/press` → CHI's AGENT podium. P8-09's clicks (podium → pick reporter →
  answer → no comment → end → edit → approve → review → history) are POST/PUT and are allowed **on `:8011` only**.
- **After `refresh` the cast is gone** (a fresh copy has no conferences and no completed CAR game). To redo it, re-run
  the two steps: the narrowed `schedule_week(cur, 2026, 1, llm=None)` with `press_cast.cast_week` filtered to the
  game, then `press_room._draft_first_round(cur, press_room.get_conference_row(cur, <id>), press_room._local_meter(
  press_room._default_llm()))` — that is the one model round — then the game-8 fixture update
  (`status = 'COMPLETED'`, 24–17) and a facts refresh.

## Boundaries

- Never point the live server, `db/*.sh` or a pipeline step at `mu_scratch_p808`; it is a page fixture, not a league.
- Never register a user team in `data/franchises.json` to get P8-08 to pass — the live registry's empty list is the
  decided state, and the honest 403 is the correct live answer.
- No saves are read or written and no Madden PC run is involved.
- P8-09 (the presser) is walkable on this scratch franchise after the one approved cast (above). The cast spent
  three press-model calls; nothing else is spent by the script, and nothing is ever cast into `madden_mu2026`.
- The database and the `:8011` server stay up only as long as a walk needs them. Drop both when the P8-08/P8-09 rows
  are checked: `tools/p8_scratch_franchise.sh drop`.
