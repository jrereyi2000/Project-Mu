// Capacity pruning. The unit tests build a synthetic table binary and check the rebuild
// arithmetic; the end-to-end test opens a real save ($MU_SAMPLE_SAVE, else
// CAREER-GIANTS-AUTOSAVE at the repo root) and skips when neither exists.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import zlib from 'node:zlib';
import Franchise from 'madden-franchise';
import { pruneSave, readStreamLength, rebuildTable, tableStartOf, writeStreamLength } from '../prune_capacity.mjs';

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const savePath = process.env.MU_SAMPLE_SAVE || join(repoRoot, 'CAREER-GIANTS-AUTOSAVE');
const skip = existsSync(savePath) ? false : `no sample save at ${savePath}`;

/** The free list the game walks: a bare row index per empty row, capacity = end of list. */
const chainOf = (records, recBytes, head, capacity) => {
  const rows = [];
  let cur = head;
  while (cur !== capacity) {
    rows.push(cur);
    if (rows.length > capacity) throw new Error('free list does not terminate');
    cur = records.readUInt32BE(cur * recBytes);
  }
  return { head, rows };
};

/** A synthetic non-array table: `name`, `capacity` rows of `words` u32s, `live` at the front. */
function syntheticTable({ name = 'TestTable', tableId = 4000, uniqueId = 4000, capacity = 8, words = 2, live = 2, numMembers = 2, poolPerRow = 0 }) {
  const offsetStart = 0xe8;
  const headerSize = offsetStart + numMembers * 4;
  const table1Start = headerSize;
  const recBytes = words * 4;
  const table2Length = capacity * poolPerRow;
  const table2Start = table1Start + capacity * recBytes;
  const data = Buffer.alloc(table2Start + table2Length);
  data.write(name + '\0', 0, 'latin1');
  data.writeUInt32BE(tableId, 0x80);
  data.writeUInt32BE(uniqueId, 0x84);
  data.writeUInt32BE(0, 0x88);
  data.writeUInt32BE(0, 0xa4); // tableStoreLength
  const hdr = 0xa8;
  data.writeUInt32BE(0, hdr + 0); // data1Offset
  data.writeUInt32BE(tableId, hdr + 4);
  data.writeUInt32BE(capacity, hdr + 8);
  data.writeUInt32BE(0, hdr + 12);
  const table1Length = 32 + numMembers * 4 + capacity * recBytes;
  data.writeUInt32BE(table1Length, hdr + 16);
  data.writeUInt32BE(table2Length, hdr + 20);
  data.writeUInt32BE(uniqueId, hdr + 24); // data1Pad3 == uniqueId: no third table
  data.writeUInt32BE(0, hdr + 28);
  data.write('BSFT', hdr + 32, 'latin1');
  data.writeUInt32BE(table1Length, hdr + 36);
  data.writeUInt32BE(table1Length + table2Length, hdr + 40);
  data.writeUInt32BE(words, hdr + 44);
  data.writeUInt32BE(capacity, hdr + 48);
  data.writeUInt32BE(numMembers, hdr + 52);
  data.writeUInt32BE(live, hdr + 60); // nextRecordToUse
  for (let i = 0; i < live; i++) for (let w = 0; w < words; w++) data.writeUInt32BE(1000 + i * 10 + w, table1Start + i * recBytes + w * 4);
  for (let i = live; i < capacity; i++) data.writeUInt32BE(i + 1 === capacity ? capacity : i + 1, table1Start + i * recBytes);
  if (table2Length) data.fill(0x41, table2Start, table2Start + poolPerRow); // one row's worth of "strings"
  const header = {
    name, tableId, uniqueId, isArray: false, tableStoreLength: 0, offsetStart, headerSize,
    table1StartIndex: table1Start, table2StartIndex: table2Start, table2Length, hasThirdTable: false,
    table3StartIndex: undefined, recordWords: words, recordCapacity: capacity, numMembers,
    nextRecordToUse: live,
  };
  return { data, header, table1Start, recBytes };
}

test('readStreamLength and writeStreamLength round-trip the 24-bit field', () => {
  const header = Buffer.alloc(0x52);
  writeStreamLength(header, 0x357469);
  assert.deepEqual([...header.subarray(0x4a, 0x4d)], [0x69, 0x74, 0x35]);
  assert.equal(readStreamLength(header), 0x357469);
  assert.throws(() => writeStreamLength(header, 0x1000000), /24-bit/);
});

test('rebuildTable keeps capacity rows, drops the tail and relinks the free list', () => {
  const { data, header, table1Start, recBytes } = syntheticTable({ capacity: 8, live: 2 });
  const out = rebuildTable(data, header, (i) => i >= 2, 4);
  assert.equal(out.length, table1Start + 4 * recBytes);
  // live rows are byte-for-byte
  for (let i = 0; i < 2; i++) {
    assert.deepEqual(out.subarray(table1Start + i * recBytes, table1Start + (i + 1) * recBytes), data.subarray(table1Start + i * recBytes, table1Start + (i + 1) * recBytes));
  }
  const hdr = 0xa8;
  assert.equal(out.readUInt32BE(hdr + 8), 4, 'data1RecordCount');
  assert.equal(out.readUInt32BE(hdr + 48), 4, 'recordCapacity');
  assert.equal(out.readUInt32BE(hdr + 60), 2, 'nextRecordToUse');
  assert.equal(out.readUInt32BE(hdr + 16), 32 + 2 * 4 + 4 * recBytes, 'table1Length');
  assert.equal(out.readUInt32BE(hdr + 36), out.readUInt32BE(hdr + 16), 'table1Length2');
  assert.equal(out.readUInt32BE(hdr + 40), out.readUInt32BE(hdr + 16), 'tableTotalLength');
  assert.deepEqual(chainOf(out.subarray(table1Start), recBytes, out.readUInt32BE(hdr + 60), 4), { head: 2, rows: [2, 3] });
});

test('rebuildTable zeroes empty rows and leaves a table with every row live without a free list', () => {
  const { data, header, table1Start, recBytes } = syntheticTable({ capacity: 6, live: 2 });
  const full = rebuildTable(data, header, () => false, 6);
  assert.equal(full.readUInt32BE(0xa8 + 60), 6, 'nextRecordToUse = capacity when nothing is empty');
  assert.equal(full.length, data.length);

  const partial = rebuildTable(data, header, (i) => i >= 2, 3);
  const row2 = partial.subarray(table1Start + 2 * recBytes, table1Start + 3 * recBytes);
  assert.equal(row2.readUInt32BE(0), 3, 'last empty row points at the capacity');
  assert.deepEqual([...row2.subarray(4)], [0, 0, 0, 0], 'the rest of an empty row is zeroed');
});

test('rebuildTable scales the string pool but never drops a non-zero byte', () => {
  const { data, header, table1Start, recBytes } = syntheticTable({ capacity: 8, live: 1, words: 2, poolPerRow: 8 });
  // The pool's first row-slot holds strings; a pool cut to one row's worth keeps them.
  const out = rebuildTable(data, header, (i) => i >= 1, 4);
  const table2Start = table1Start + 4 * recBytes;
  assert.equal(out.readUInt32BE(0xa8 + 20), 32, 'table2Length scales with capacity (4 rows x 8)');
  assert.equal(out.readUInt32BE(0xa8 + 40), out.readUInt32BE(0xa8 + 16) + 32, 'tableTotalLength counts the pool');
  assert.equal(out.subarray(table2Start, table2Start + 8).toString('latin1'), 'AAAAAAAA');

  // A string sitting far into the pool survives even when the new capacity is smaller.
  const far = syntheticTable({ capacity: 8, live: 1, words: 2, poolPerRow: 8 });
  far.data.fill(0x42, far.header.table2StartIndex + 56, far.header.table2StartIndex + 60); // row 7's slot
  const kept = rebuildTable(far.data, far.header, (i) => i >= 1, 2);
  assert.equal(kept.readUInt32BE(0xa8 + 20), 64, 'the pool keeps every non-zero byte');
  assert.equal(kept.subarray(kept.length - 8, kept.length - 4).toString('latin1'), 'BBBB');
});

test('rebuildTable refuses to grow a table or run past its record area', () => {
  const { data, header } = syntheticTable({ capacity: 8, live: 0 });
  assert.throws(() => rebuildTable(data, header, () => true, 9), /capacity 9 > current 8/);
  assert.throws(() => rebuildTable(data, header, () => true, 0), /capacity 0 < 1/);
});

test('tableStartOf reads the asset table offset and count', () => {
  const u = Buffer.alloc(0x1000);
  u.writeUInt32BE(128, 4);
  u.writeUInt32BE(300, 36);
  assert.equal(tableStartOf(u), 128 + 300 * 8);
});

test('pruneSave shrinks a real save, preserves its tail and every live row', { skip }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-prune-'));
  try {
    const out = join(dir, 'pruned');
    const report = await pruneSave({ save: savePath, out, policy: { Highlight: 64, NarrativeTag: 64, PlayerEditTransactionHistoryEntry: 32 }, maxFill: 0.2 });
    assert.deepEqual(report.problems, []);
    assert.ok(report.saved > 0, `expected some saving, got ${report.saved}`);

    const before = readFileSync(savePath);
    const after = readFileSync(out);
    assert.equal(after.length, before.length, 'the slot keeps its fixed size');
    assert.equal(after.subarray(0, 0x52).subarray(0, 0x4a).toString('hex'), before.subarray(0, 0x4a).toString('hex'));
    assert.ok(readStreamLength(after) < readStreamLength(before), 'the stream got shorter');
    assert.equal(zlib.inflateSync(after.subarray(0x52)).length, report.unpackedAfter, 'the new stream inflates to the rebuilt image');
    const oldEnd = 0x52 + readStreamLength(before);
    assert.deepEqual(after.subarray(oldEnd), before.subarray(oldEnd), 'the tail is untouched at its absolute offsets');

    const file = await Franchise.create(out, { autoParse: true, saveOnChange: false });
    for (const p of report.tables) {
      const base = p.name.endsWith('[]') ? p.name.slice(0, -2) : p.name;
      const t = file.tables.find((x) => x.header.tableId === p.tableId && (x.name.endsWith('[]') ? x.name.slice(0, -2) : x.name) === base);
      assert.ok(t, `${p.name} should still parse`);
      assert.equal(t.header.recordCapacity, p.newCapacity);
      await t.readRecords(null);
      assert.equal(t.records.filter((r) => !r.isEmpty).length, p.live);
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('pruneSave leaves every game-visible record identical', { skip }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-prune-'));
  try {
    const out = join(dir, 'pruned');
    await pruneSave({ save: savePath, out });
    const read = async (p) => {
      const f = await Franchise.create(p, { autoParse: true, saveOnChange: false });
      const out = {};
      for (const name of ['Player', 'Team', 'SeasonGame', 'Coach', 'DraftPick', 'DraftPlayer']) {
        out[name] = f.getAllTablesByName(name).map((t) => t.data.toString('hex'));
      }
      return out;
    };
    assert.deepEqual(await read(out), await read(savePath));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
