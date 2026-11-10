# Save-capacity pruning (Madden 27 saves) — brief for a new session

**Status:** not started. Research spike, P2 blocker for the tuning league. Written 2026-11-10 at the end of
the `p2-tuning` session, from its measurements. Owner: a new session; label it e.g. `save-capacity`.

## 1. The problem

Project Mu's tuning league cannot complete a simulated season on the 65-man-roster baseline. At the postseason
(the Pro Bowl in 2026), Madden writes a save whose uncompressed content reaches exactly **20,971,520 bytes
(20 MiB)** and is truncated there; the game crashes on load. Reproduced twice (CAREER-TUNE-P2A and
CAREER-TUNE-P2B), so it is deterministic.

Everything measured (2026-11-10):

- The raw save file is a **fixed 5,662,392-byte slot** for every save in the folder.
- The container is `FBCHUNKS`: 0x52-byte header, then one zlib stream, then a tail structure (near its start
  the string `RelocationTracker`) that runs to the end of the slot. `madden-franchise` only inflates the zlib
  stream; its `postPackFile` preserves the tail by slicing the original raw file at the new compressed length.
- A save's **uncompressed** size is what hits the limit. Working saves in the folder top out at 20,911,398 B;
  the two broken ones decompress to exactly 20,971,520 B and stop mid-table.
- No-mod (53-man) saves stay under it: a vanilla full season at the Super Bowl is 20,455,240 B (3,005 players,
  loads fine). The modded season starts at 20,458,364 B (week 1, 3,238 players) and grows ~373 KB by the Wild
  Card. The roster mod's extra 233 players cost ~1.6 KB each in the save.
- **The game compacts at each season rollover**: from the Wild Card save (20,831,756 B) to 2027 preseason week 0
  (20,452,593 B), it collapsed 1,011 per-game stat tables into 6 (‑349,740 B) and dropped 66 players. Each
  season therefore starts near 20.45 MB — and crosses 20 MiB again at the next Pro Bowl. Row pruning of
  per-game stats is redundant with this and cannot buy recurring headroom.
- A pruned save (all past-game defensive stat singletons emptied, every reference to them nulled) **loaded in
  game and advanced through the whole offseason**, but crashed at 2027 preseason week 2. That is consistent
  with corruption from the nulled references (or a damaged container tail), not with size (a two-week season
  start cannot cross 20 MiB). Row-level pruning is judged a dead end.

**Goal of this spike:** shrink the save's *allocated table capacity* — not live rows — so the 65-man-roster
baseline gains ~0.4–1 MB of permanent headroom and a full simulated season plus offseason stays under 20 MiB.

## 2. Why capacity, not rows

The save's size is dominated by allocated table capacity. Measured on the good `CAREER-TUNE-P2B` (Wild Card):

| table group | bytes | tables/rows |
|---|---:|---|
| Persona* | 1,206,751 | 44 tables, 106k capacity rows |
| History* | 2,030,568 | 15 tables |
| Game-stat tables | 1,284,123 | 1,011 tables |
| UI* | 1,542,969 | 18 tables |
| Character* (visuals) | 1,985,351 | 3 tables |
| Player | 1,676,868 | 1 table |
| Talent* | 1,547,990 | 31 tables |
| Season-stat tables | 824,061 | 5 tables |
| Highlight | 723,774 | 2 tables |
| DraftBoard* | 463,084 | 5 tables |

Live vs capacity from `madden-franchise`:

| table | capacity | live |
|---|---:|---:|
| `PersonaNegotiationOptionAction` | 64,000 | **0** |
| `Highlight` | 3,200 | **0** |
| `GameDefensiveStats` | 13,500 | 13,500 |
| `DraftBoardPlayerEvaluation` | 14,400 | 12,448 |
| `SeasonStats[]` | 3,960 | 2,773 |

Emptying **all** Persona rows changed the file size by **0 bytes** (only 26 live rows existed across the 44
Persona tables). The 64,000-row Persona action table and the 3,200-row Highlight table are pure allocated
capacity — together ~1.2 MB. Those are the prime candidates. Other candidates must be audited the same way
(capacity high, live low, and Mu's database already holds the equivalent data if the table is game-side).

`GameDefensiveStats` is really 1,006 tables: one 13,500-row table plus **1,005 one-row per-game tables**, each
with `SeasonGame`/`OpposingTeam` refs. The on-row references come from `GameStats[].GameStats16/17` (841/164
references) and `TeamValue_RankingInt[]` (20). This is the table group the game itself compacts at rollover.

## 3. What the tooling already provides

- `pipeline/save_reader/` — `pc_save_reader.mjs` (read), `save_writer.mjs` (write via `madden-franchise` 4.4.1),
  `lib.mjs` helpers. `README`/tests there. `madden-franchise` 4.4.1 in `pipeline/save_reader/node_modules`.
- Opening a save: `Franchise.create(path, {autoParse:true, saveOnChange:false})`. `f.tables` (array of
  `FranchiseFileTable`), `f.getAllTablesByName(name)`, `f.getTableByName(name)`, `t.readRecords()`,
  `record.isEmpty`, and **`record.empty()`** (a supported method: it clears the fields, fires the table's
  `empty` event, links the row into the table's empty-record free list and zeroes its buffers).
- Fields: `record.fields` maps name → field. `field.isReference`, `field.referenceData` =
  `{tableId, rowNumber}` (guard it: degenerate records throw on read). A reference is a 32-bit bit string;
  nulling is `record[fieldName] = '0'.repeat(32)`.
- Saving: `f.save(outPath, {sync:true})` → `CommonAlgorithms.save(tables, unpackedFileContents)` updates the
  unpacked buffer **in place**. Removing entries from `f.tables` does **not** change the bytes: a test that
  dropped 1,005 tables (`3,004 → 1,999` in the array) produced a byte-identical file. A real shrink needs a
  container rebuild (below).

## 4. The format, as measured

Unpacked `Grtk` buffer:

- `[0x00]` `Grtk`, `[0x04]` assetTableOffset (128 in every save seen), `[0x08]` 4, `[0x0c]` 1.
- `[0x24]` assetTableEntries (318).
- Tables start at `assetTableOffset + assetTableEntries * 8` (2672 in every save seen) and run as a *sequence
  of self-describing table binaries* to the end of the buffer. No global table count.
- Per table header (M20/M24 strategy, offsets from the table start):
  - name = NUL-terminated string at table[0]; `isArray` if the name contains `[]`.
  - `tableId` u32 at 0x80, `uniqueId` at 0x84, `table2Capacity` at 0x88, store length at 0x80+36.
  - `headerOffset = 0x80 + 40 (+ storeLen)`; then `data1RecordCount` at +8, `table1Length` +16,
    `table2Length` +20, `data1Pad3` +24, `tableTotalLength` +40, `recordWords` +44, `numMembers` +52.
  - table length = `0xe8 + storeLen + (isArray ? data1RecordCount*4 : numMembers*4) + recordWords*4*data1RecordCount
    + table2Length + table3Length` (third table exists when `data1Pad3 != uniqueId`).
  - The walk is implemented in this session's scratch scripts (`/tmp/mu-p2/trace.cjs`, `census.cjs`,
    `group.cjs`, `capdiff.cjs`, `refscan.cjs`, `prune/empty_defense.cjs`; /tmp may be gone — re-derive from
    this brief).
- Raw `FBCHUNKS` slot:
  - magic `FBCHUNKS`, then fields; compressed data starts at **0x52**; `header[0x4a..0x4c]` holds the compressed
    length (the library sets it as hex bytes, high→low, of the deflated length).
  - After the zlib stream there is a tail that runs to the end of the fixed 5,662,392-byte slot. Its first
    non-zero byte starts the tail; its contents begin with `RelocationTracker` and then table-looking data.
  - Measured examples: MU2026 streamEnd 3,056,764, tail 2,605,617 B; P2B streamEnd 3,504,566, tail 1,835,879 B.
    The tail size varies, so it is not a fixed trailing blob. **Its semantics and whether it stores absolute
    file/stream offsets are unknown** and are the crux of the container rebuild.

## 5. Suggested order of work

1. **Audit** (read-only, no PC): list every table with `capacity >= 1000` and `live <= 5%`, grouped by name,
   with byte sizes. Confirm the Persona/Highlight numbers and find the rest. For each candidate, state whether
   Mu already holds the data (it does for game/season stats; not for UI/persona game-side state).
2. **Understand the RelocationTracker tail.** Dump it; decode it with the library's tables if it is structured
   (it looks like a table sequence). Decide whether its entries are asset ids/row ids (safe to shift) or
   absolute offsets (must be rebuilt). This decides the whole approach. If it is absolute offsets, model how
   the game computes them (likely stream offset + table offsets).
3. **Rebuild spike (offline):** build `tools/prune_save_capacity.mjs` that opens a save, picks candidate
   tables, rebuilds those tables at a smaller capacity (rows = live, free list rebuilt, header fields
   recomputed), and reassembles:
   - new unpacked = `u[0 .. tableStart]` + concatenation of the kept tables' binaries (rebuilt or original
     slices);
   - deflate it; rebuild the raw slot (header + stream + tail, with the tail placed correctly and any
     absolute offsets recalculated).
4. **Validate offline:** `pc_save_reader.mjs` must parse the result (players/games unchanged except pruned
   data), `node --test "pipeline/save_reader/test/*.test.mjs"` green, and the unpacked size must drop by the
   expected amount.
5. **Validate in game (ask JR; post `CLAIM · Madden PC` first):** load the shrunk copy, check a player's page,
   advance a week, quit, and confirm the new autosave (a) loads, (b) is smaller, (c) still parses with the
   reader. Then repeat across the season rollover into 2027 and a Pro Bowl advance — the cap must not be hit.
6. **Integrate** only after (5) passes: a `prune-save-capacity` step in the tuning season loop, run on the
   tuning DB's save after each ingest (before the pilot reloads it). Never on the live franchise without JR.

## 6. Files and safety

- Work under `pipeline/save_reader/` (new `prune_capacity.mjs` + tests) and `tools/`; commit with
  `git commit --only -- <paths>`, trailer `Model: <model>`.
- The live franchise database is `madden_mu2026`; never ingest a pruning experiment there. Tuning saves are
  `CAREER-TUNE-*` and the watcher refuses them outside a `madden_tune_*` `--dsn` (see
  `docs/reference/TUNING_LEAGUE.md`).
- The game's own compaction at the rollover is the baseline behaviour; do not break it.
- If the rebuild cannot be made safe, fall back to the roster-size change (cut-day 58–60 instead of 65), which
  was measured as needing only ~0.4 MB of headroom.

## 7. State left by the `p2-tuning` session (2026-11-10)

- Commits on `main`: `c751355`, `be40534`, `52485e2`, `169de1c`, `44dc576`, `21df1af`, `6c120e9`, `1383874`,
  `505140b`, `14d1117`, `bb0be7d`, `a2c06c9`, `19f0a8d`, `8f20513`, `68613e6` (pilot + tuning-league work).
- Saves on disk (Madden folder): `CAREER-TUNE-P2B` (good, Wild Card, 20,831,756 B), `CAREER-TUNE-P2B-PRUNE`
  (our in-place pruned copy, loads in game), `CAREER-TUNE-P2B-PRUNE-AUTOSAVE` (2027 preseason week 0,
  20,452,593 B, clean), `CAREER-NOV10-10h46m38a-AUTOSAVE` (JR's no-mod Super Bowl save, 20,455,240 B).
- `docs/reference/TUNING_LEAGUE.md` has the isolation recipe, the 20 MiB blocker and the P2-03 decision.
- `docs/reference/MADDEN_PILOT.md` documents every catalogued screen; the pilot now trains, drafts, advances
  (READY TO ADVANCE at week 1, FORCE ADVANCE after), and runs the postseason to the Staff stop.
