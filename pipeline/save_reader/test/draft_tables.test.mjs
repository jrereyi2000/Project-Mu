// draft_tables.mjs: future picks from every DraftPick table (unit), and the sample save's 3 x 224 picks (skipped without
// one). MU_SAMPLE_SAVE can point at a fresh career (CAREER-STAFF, CAREER-WEEK1: one table per pick) or at
// CAREER-GIANTS-AUTOSAVE (one 672-row table).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import Franchise from 'madden-franchise';
import { encodeRef } from '../record_edits.mjs';
import { futurePicks, readDraftTables } from '../draft_tables.mjs';

test('futurePicks reads every DraftPick table, not only the largest', () => {
  const pick = (team, extra = {}) => ({ isEmpty: false, OriginalTeam: encodeRef(5934, team), ...extra });
  const unset = '0'.repeat(32);
  const tables = [
    { header: { tableId: 4540 }, records: [{ isEmpty: true }, { isEmpty: true }, { isEmpty: true }] },  // emptied 672-row table
    { header: { tableId: 4602 }, records: [pick(3)] },
    { header: { tableId: 4601 }, records: [pick(2)] },
    { header: { tableId: 4600 }, records: [pick(1), { isEmpty: false, OriginalTeam: unset }] },        // a selection: no owner
  ];
  assert.deepEqual(futurePicks(tables).map(p => [p.table.header.tableId, p.row]), [[4600, 0], [4601, 0], [4602, 0]]);
});

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const savePath = process.env.MU_SAMPLE_SAVE || join(repoRoot, 'CAREER-GIANTS-AUTOSAVE');
const skip = existsSync(savePath) ? false : `no sample save at ${savePath}`;

test('the sample save has 224 future picks per draft, each with its own table and row', { skip, timeout: 300_000 }, async () => {
  const d = await readDraftTables(await Franchise.create(savePath, { autoParse: true, saveOnChange: false }));
  const perYear = new Map();
  for (const p of d.picks) perYear.set(p.year_offset, (perYear.get(p.year_offset) ?? 0) + 1);
  assert.deepEqual([...perYear].sort(), [[0, 224], [1, 224], [2, 224]]);
  assert.equal(new Set(d.picks.map(p => `${p.table_id}:${p.row}`)).size, 672);
  assert.deepEqual(d.draft_pick_table_ids, [...new Set(d.picks.map(p => p.table_id))]);
  for (const p of d.picks) {
    assert.ok(d.draft_pick_table_ids.includes(p.table_id));
    assert.ok(p.round >= 1 && p.round <= 7);
    assert.notEqual(p.original_team_row, null);
  }
});
