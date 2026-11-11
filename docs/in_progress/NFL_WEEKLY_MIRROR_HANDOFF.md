# NFL Weekly Mirror — orchestrator handoff (2026-11-11)

Written by `deepseek-v4.1-flash (build-nfl-mirror)` when JR handed waves 2–4 of
[`NFL_WEEKLY_MIRROR_PLAN.md`](NFL_WEEKLY_MIRROR_PLAN.md) to a fresh session to save this
one's context. It records exactly where the build stands so the successor can continue
without re-deriving anything. Delete this file once the successor has picked it up and
recorded it in the plan's Build log.

**Successor:** run `/execute-plan docs/in_progress/NFL_WEEKLY_MIRROR_PLAN.md` and start at
"Next steps" below. The plan's Status line and §9 Build log are the primary record; this
file only adds session state that is not in the repo history.

## State at handoff

`main` head when this was written: **`8595016`** (plan wave-2 updates). Plan status:
*approved by JR (2026-11-11); building, wave 2 of 4 — U3 and U4 merged; U5 waits on Spike C.*

| Unit | Wave | Merge sha | Notes |
|---|---|---|---|
| U1 | 1 | `534fb66` | save-writing CLI + store |
| U2 | 1 | `6953da6` | fetch + real_state |
| U3 | 2 | `67f8850` | diff + contracts (review fix `27b5cac`: snapshot college `{table,row}` resolved before linking) |
| U4 | 2 | `14db099` | injuries + depth (review fix `f8c780a`: only save-writable injury enums) |
| — | — | `ecd5806` | PROJECT_SPEC §3.3aa wave-2 text |
| — | — | `8595016` | plan: wave-2 as-built bullets + build log |
| — | — | `7d83d14` | orchestrator fix: `coaching_carousel` same-season tie-break `ct.id DESC` |
| — | — | `346e063` | orchestrator fix: `test_nfl_history_store` ignores `nfl_mirror_*` |

All four merged units passed their Verify commands on `main`. Nothing is applied to a
franchise DB: the migration `2027_02_27_nfl_mirror.sql` is only in `db/init_db.sql` and
scratch DBs; JR applies it (plan §8 note, M-0444).

## In flight when this was written

**U9 — wave-1 audit fixes F1/F2** (audit-3's PARTIAL, M-0473; answered in M-0482).
- Launched as a background subagent from the build-nfl-mirror session, worktree
  `.claude/worktrees/agent-ade8f1b34cbd6b018`, branch `worktree-agent-ade8f1b34cbd6b018`,
  base `220935e`.
- Owns `engines/nfl_mirror/staff.py`, `engines/coach_save_sync.py`,
  `engines/coaching_carousel.py`, `tests/test_nfl_mirror_staff.py`,
  `tests/test_coach_save_sync.py`, `tests/test_coaching_carousel_db.py`.
- **F1:** a matched existing HC/OC/DC with no `coach_madden_profiles` row gets a CREATED
  profile for the same person (HC passes `promote_from` so a promoted coordinator keeps
  his record). **F2:** among equal `start_season`, an open tenure (`end_season IS NULL`)
  sorts first, then `id DESC` — applied identically to `_current_hc`/`_current_coord`,
  `coach_save_sync._wanted`, `coaching_carousel.head_coaches`/`staff_in`. **F3 is not
  implemented** — promotion with no named replacement is JR's policy call.
- Its report required real-PG regressions (rollback-only), plus `python3 -m
  tools.offseason_check` `timelines` and `e2e` on a CAREER-MUBASE copy.
- **If the branch still has commits `main` does not** (`git log --oneline main..worktree-agent-ade8f1b34cbd6b018`),
  review and merge it exactly like any other unit (Verify in the worktree, merge, Verify on
  `main`, SPEC CHANGES, Build log row). The previous session may already have merged it and
  posted a NOTICE; check the board first. If the worktree has no such commits and the plan
  has no U9 row, ask on the board under `build-nfl-mirror` before redoing the work.

## Next steps, in order

1. **Spikes A–C with JR** (Madden PC) — the gate for U5. Plan §0 and M-0484 describe them.
   The PC is claimed for this plan; JR runs each spike at the keyboard. This is the
   session's only blocking item and needs JR present.
2. **U5** — created players + EA ratings (`ea_ratings.py`, `create.py`,
   `tests/test_nfl_mirror_create.py`, `tests/fixtures/ea_ratings_page.json`). Needs Spike C
   (created player plays a sim week).
3. **Wave 3: U6** — league office wiring (`pipeline/league_office.py`, new
   `pipeline/league_office_mirror.py`, tests). Must keep `7d83d14`'s tie-break. Needs the
   injuries toggle from Spike A.
4. **Wave 4: U7** (watcher gating + `python3 -m engines.nfl_mirror` CLI) and **U8** (pilot
   `catch-up` job in `tools/madden_pilot/jobs.py`). U8 branches from a main that includes
   pilot-menu's `0601b4b` (it does).
5. **Finish** per `/execute-plan` §3: every unit's Verify plus `pytest -m "not integration"`
   over the touched tests, status `built and on main`, `git mv` to `docs/done/`, fix links,
   spec layout line, commit, then a `to: audit, all` HANDOFF with the plan path, commit
   range, per-unit lines, deviations, open items and which DBs the migration was applied to
   (none, unless JR applied it). Remove merged worktrees you created (U3's
   `agent-ab477134fdc19c085` and U4's `agent-a3473b7f0b3d4ce3d` are merged but still
   present) — only ones you own; never force.
6. Continue the DeepSeek session-start routine: `NOTICE` with the label, the model inbox
   listener under Monitor (`python3 -m tools.agent_board_inbox deepseek --label <label>
   --watch`, 30-minute expiry, re-arm), sign off with a `NOTICE` when JR ends the session.

## Spikes A–C: what is ready

Run on a `CAREER-TUNE-*` clone, never on a real franchise save. Madden must not be running
when the offline tools open a save for reading (they work on a temp copy).

- **Spike A** (injuries toggle): toggle franchise Injuries in League Settings, snapshot
  before and after, diff the LeagueSetting rows to learn the exact field for
  `madden_injuries_field` (plan §2). Offline tooling already written and tested against
  `CAREER-DEBUGWEEK12` — in `/tmp/mu-scratch/` (transient; re-create if missing):
  - `league_settings_dump.mjs` — copies the save, opens it with
    `Franchise.create(copy, {autoParse: true, saveOnChange: false})` imported by **absolute
    path** from `pipeline/save_reader/node_modules/madden-franchise/dist/index.mjs`, and
    dumps every non-empty record of the LeagueSetting/League/Franchise-ish tables as JSON
    (~76 KB). Note `LeagueSetting#4290` carries generic `Field_N` names, so the diff can
    point at a generic field — correlate it with the UI.
  - `ls_diff.py` — field-by-field diff of two dumps.
- **Spike B** (injuries off + sim): write an Out injury and a heal with injuries off, sim a
  week, check who plays. The enums are already dumped offline (plan §0 S17); the writer
  refuses `None` and every `_`-suffixed enum member, so write only real members.
- **Spike C** (created player): rewrite a free-agent donor with `player_edit` as a made-up
  player, sign him, play-sim a week. This unblocks U5's rating fallback.

Results go into plan §0 (S-numbers) and, for Spike A, into the migration's settings row /
`madden_injuries_field` design.

## Board state at handoff (numbers matter; the board is append-only)

- **M-0484 — `CLAIM · Madden PC`** held by `build-nfl-mirror` for spikes A–C. It was
  released by pilot-menu (M-0476) and re-claimed by this plan; it transfers to the
  successor session (the handoff message says so). Post a `RELEASE` when the spikes are
  done or the PC is idle.
- **M-0482 — ANSWER/CLAIM to audit-3**: F1/F2 accepted, F3 left to JR, merge trailers
  noted. The file claim in M-0482 covers U9's files until U9's fix NOTICE.
- **M-0473** is audit-3's wave-1 report in
  `data/audit_reports/nfl_mirror_wave1/report.md`.
- **pilot-menu signed off** (M-0483). Its commits `0601b4b`, `02ea792`, `aa2daa9` are on
  main; U8 builds on them.
- Wave 1's original HANDOFF from `nfl-weekly-sync` is archived; read it only if a `Re:`
  points there.
- **Don't narrate board traffic in chat**; surface only messages `to: all` or `to:` this
  label. Never reply to messages addressed to another session.

## Working in this repo: the traps this build hit

- `git commit --only -- <paths>` **always** — other sessions keep staged hunks in the
  shared checkout. Put `-m` before `--only`.
- `PROJECT_SPEC.md` and `docs/MOD_INSTALL.md` currently carry **another session's staged +
  unstaged hunks** (`MM` in `git status`). Do not commit them with a bare `git add`; don't
  resolve their hunks for them. When you must commit your own spec text, either stage the
  identical scripted replacement into HEAD, the index and the worktree copies through a
  temporary index (`GIT_INDEX_FILE=$(mktemp)`, `git read-tree HEAD`,
  `git update-index --cacheinfo`, `git write-tree`, `git commit-tree`, `git update-ref`,
  then `unset GIT_INDEX_FILE`), or wait until the tree is clean. Verify with
  `git status --porcelain PROJECT_SPEC.md` and `git diff HEAD -- PROJECT_SPEC.md` first —
  a worktree commit elsewhere can move `main` without updating this checkout.
- If `git merge --no-ff` refuses because of those hunks, merge by plumbing:
  `git merge-tree --write-tree main <branch>` → `git commit-tree <tree> -p main -p <branch>`
  → `git update-ref refs/heads/main <sha>` → `git checkout <sha> -- <paths>`. The other
  sessions' index entries stay untouched. (U4 was merged this way: `14db099`.)
- Commit trailers: `Model: deepseek-v4.1-flash` (the session's own model) and
  `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- Tests must never touch the live `madden`, `madden_mu2026` or `madden_test` databases;
  use `mu_scratch_*` and roll everything back, or Docker's `madden-db` container with a
  scratch DB.
- Subagents run in their own worktree from current `main`; review for scope (only the
  unit's Owns list), read the diff, run Verify in the worktree, then merge and run Verify
  on `main`.
- The save writer (`pipeline/save_reader/lib.mjs:150-193`) rejects `None`/null and any
  `_`-suffixed enum member (`Invalid_`, `Max_`) and aborts the **whole** players edit — any
  field-writing code must filter through `fieldMeta` enum lists. This was U4's review
  defect.
- Node scratch scripts under `/tmp` must import `madden-franchise` by **absolute path**
  (ESM resolves from the script's directory).

## Open items carried into the successor's report

- Plan wave-1 item (b): `staff.py` writes no `staff_moves` / `staff_contracts` rows.
- F3 (promotion with no named replacement): JR's policy call, not implemented; the mirror
  refuses that week (U9 leaves it).
- Spike A's LeagueSetting field may come back generic (`Field_N`); the UI correlation is
  recorded when it does.
- Migration `2027_02_27_nfl_mirror.sql` is not applied to any franchise DB. JR applies it.
