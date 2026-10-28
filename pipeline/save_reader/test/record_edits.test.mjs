// record_edits.mjs (unit) and save_writer.mjs's "records" edits on a copy of the sample save (skipped without one).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'crypto';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import Franchise from 'madden-franchise';
import { decodeRef } from '../lib.mjs';
import { encodeRef, referenceError } from '../record_edits.mjs';
import { readDraftTables } from '../draft_tables.mjs';
import { writeSave } from '../save_writer.mjs';

test('encodeRef is decodeRef inverted', () => {
  assert.equal(encodeRef(5934, 26), '00101110010111000000000000011010');
  assert.deepEqual(decodeRef(encodeRef(4540, 131071)), { table: 4540, row: 131071 });
  assert.throws(() => encodeRef(0, 1));
  assert.throws(() => encodeRef(5934, 2 ** 17));
});

test('reference checks', () => {
  const team = { name: 'Team', records: [{ isEmpty: false }, { isEmpty: true }] };
  const tables = new Map([[5934, team], [4236, { name: 'Player', records: [{ isEmpty: false }] }]]);
  const meta = { type: 'Team', isReference: true };
  const now = encodeRef(5934, 0);
  assert.equal(referenceError(meta, now, { tableId: 5934, row: 0 }, tables), null);
  assert.equal(referenceError(meta, '0'.repeat(32), { tableId: 5934, row: 0 }, tables), null);   // unset link
  assert.match(referenceError(meta, now, { tableId: 5934, row: 1 }, tables), /not a record/);
  assert.match(referenceError(meta, now, { tableId: 5934, row: 9 }, tables), /not a record/);
  assert.match(referenceError(meta, now, { tableId: 4236, row: 0 }, tables), /not a Team table/);
  assert.match(referenceError(meta, encodeRef(5343, 0), { tableId: 5934, row: 0 }, tables), /points into table 5343/);
  assert.match(referenceError(meta, now, '00101110010111000000000000000000', tables), /tableId/);
  assert.match(referenceError({ type: 'Player', isReference: true }, now, { tableId: 4236, row: 0 }, tables), /not writable/);
});

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const savePath = process.env.MU_SAMPLE_SAVE || join(repoRoot, 'CAREER-GIANTS-AUTOSAVE');
const skip = existsSync(savePath) ? false : `no sample save at ${savePath}`;
const hash = path => createHash('sha256').update(readFileSync(path)).digest('hex');
const open = path => Franchise.create(path, { autoParse: true, saveOnChange: false });

test('a pick changes owner and a prospect changes group; nothing else moves', { skip, timeout: 300_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-records-test-'));
  try {
    const before = await readDraftTables(await open(savePath));
    const pick = before.picks.find(p => p.current_team_row === p.original_team_row);
    const owner = before.teams.find(t => t.team_index < 32 && t.row !== pick.current_team_row);
    const prospect = before.prospects.find(p => p.contract_status === 'Draft' && p.draft_position !== 'LS');
    const edits = { records: [
      { table: 'DraftPick', tableId: before.draft_pick_table_id, row: pick.row,
        set: { CurrentTeam: { tableId: before.team_table_id, row: owner.row }, PickNumber: 223 } },
      { table: 'DraftPlayer', tableId: before.draft_player_table_id, row: prospect.row,
        set: { DraftPosition: 'LS', InitialDraftRank: 401 } }] };
    const saveHash = hash(savePath);
    const out = join(dir, 'CAREER-RECORDS');
    const report = await writeSave(savePath, edits, out);
    assert.deepEqual([report.players, report.records, report.recordFields, report.verified], [0, 2, 4, true]);
    assert.equal(hash(savePath), saveHash, 'the input save is untouched');

    const after = await readDraftTables(await open(out));
    const changedPicks = after.picks.filter((p, i) => JSON.stringify(p) !== JSON.stringify(before.picks[i]));
    assert.deepEqual(changedPicks, [{ ...pick, current_team_row: owner.row, pick_number: 223 }]);
    const changedRows = after.prospects.filter((p, i) => JSON.stringify(p) !== JSON.stringify(before.prospects[i]));
    assert.deepEqual(changedRows, [{ ...prospect, draft_position: 'LS', initial_draft_rank: 401 }]);
    assert.deepEqual(after.teams, before.teams);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a bad record edit writes nothing', { skip, timeout: 300_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-records-test-'));
  try {
    const t = await readDraftTables(await open(savePath));
    const out = join(dir, 'CAREER-BAD');
    const pick = (set, extra = {}) => ({ table: 'DraftPick', tableId: t.draft_pick_table_id, row: 0, set, ...extra });
    const bad = async records => assert.rejects(writeSave(savePath, { records }, out));
    const team = { tableId: t.team_table_id, row: t.teams[0].row };
    await bad([pick({ CurrentTeam: team }, { tableId: t.draft_player_table_id })]);    // id of another table
    await bad([pick({ CurrentTeam: team }, { row: 999999 })]);
    await bad([pick({ CurrentTeam: { tableId: t.team_table_id, row: 9999 } })]);
    await bad([pick({ CurrentTeam: { tableId: t.draft_player_table_id, row: 0 } })]);
    await bad([pick({ SelectedPlayer: { tableId: 4236, row: 1 } })]);                  // Player references stay closed
    await bad([pick({ CurrentTeam: '00101110010111000000000000000001' })]);           // raw bits are refused
    await bad([pick({ Round: 500 })]);
    await bad([pick({ NoSuchField: 1 })]);
    await bad([pick({ PickNumber: 3 }), pick({ Round: -5 })]);                          // one bad edit stops all
    await bad([{ table: 'Player', tableId: 4236, row: 1, set: { SpeedRating: 90 } }]);
    assert.equal(existsSync(out), false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
