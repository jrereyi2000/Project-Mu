#!/usr/bin/env node
/**
 * Rebuild a Madden 27 franchise save with smaller allocated table capacity.
 *
 * The save's 20 MiB uncompressed limit counts allocated capacity, not live rows: a
 * table given 64,000 records costs 64,000 * recordWords * 4 bytes whether or not any
 * row is used. This rewrites the whole table sequence with the chosen tables rebuilt
 * at a smaller capacity (rows kept, free list rebuilt, header fields recomputed), then
 * re-deflates it into the FBCHUNKS slot.
 *
 * Usage:
 *   node prune_capacity.mjs <save> --out <new-save> [--dry-run] [--report report.json]
 *        [--policy policy.json] [--set Name=cap ...] [--max-fill 0.05]
 *
 * The container: FBCHUNKS magic, a zlib stream from 0x52 whose exact length is the
 * 24-bit LE field at 0x4a..0x4c, then a fixed tail. All 157 saves on JR's PC are a
 * fixed 5,662,392-byte slot; the tail (identical byte-for-byte across franchises)
 * never moves, so it is copied back at its original absolute offsets and the gap
 * left by a shorter stream is zero-filled, which is what Madden itself writes.
 */
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import zlib from 'node:zlib';
import Franchise from 'madden-franchise';

const COMPRESSED_DATA_OFFSET = 0x52;
const MAGIC = 'FBCHUNKS';

/**
 * Capacity ceiling per table name. `X` covers both `X` and `X[]` instances.
 *
 * Every table here was measured empty (or, for Highlight and the Persona group, within
 * a handful of rows) across eight saves spanning preseason, the regular season, the
 * postseason and the 2027 offseason, and Madden never resizes a table within a season,
 * so each target leaves a multiple of the highest live count ever seen.
 *
 * `UILogicForm` and `UIFlowForm` also carry large slack (~620 KB and ~385 KB), but the
 * schema cannot decode their string-pool references; add them with --set once the core
 * set has been validated in game.
 */
export const DEFAULT_POLICY = {
  Highlight: 512,
  NarrativeTag: 512,
  UIContractNegotiationForm: 128,
  PlayerEditTransactionHistoryEntry: 128,
  PlayerReSignNegotiation: 256,
  MindsetChangeEvent: 256,
  ReSign_TeamRequest: 128,
  ReSign_PlayerRequest: 128,
  EvaluateContractOffersRequest: 128,
  ContractOffer: 128,
  PlayerStatSnapShot: 2048,
  PersonaNegotiationOptionAction: 4096,
  PersonaNegotiationOption: 1024,
  PersonaNegotiationCelebrationInfoItem: 1024,
  PersonaNegotiationDealbreaker: 1024,
  PersonaNegotiationPackage: 256,
};

function parseArgs(argv) {
  const out = { tables: [], sets: [], maxFill: 0.05, dryRun: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--out') out.out = argv[++i];
    else if (a === '--save') out.save = argv[++i];
    else if (a === '--dry-run') out.dryRun = true;
    else if (a === '--report') out.report = argv[++i];
    else if (a === '--policy') out.policy = argv[++i];
    else if (a === '--set') out.sets.push(argv[++i]);
    else if (a === '--tables') out.tables.push(argv[++i]);
    else if (a === '--max-fill') out.maxFill = Number(argv[++i]);
    else if (!a.startsWith('--') && !out.save) out.save = a;
    else throw new Error(`unknown argument ${a}`);
  }
  if (!out.save) throw new Error('a save path is required');
  return out;
}

/** The 24-bit little-endian deflate length at 0x4a..0x4c. */
export function readStreamLength(raw) {
  return raw[0x4a] | (raw[0x4b] << 8) | (raw[0x4c] << 16);
}

export function writeStreamLength(header, length) {
  if (length > 0xffffff) throw new Error(`stream of ${length} bytes does not fit the 24-bit length field`);
  header[0x4a] = length & 0xff;
  header[0x4b] = (length >> 8) & 0xff;
  header[0x4c] = (length >> 16) & 0xff;
}

/** Offset of the first table in the unpacked image. */
export function tableStartOf(unpacked) {
  const assetTableOffset = unpacked.readUInt32BE(4);
  const entries = unpacked.readUInt32BE(36);
  return assetTableOffset + entries * 8;
}

/**
 * Rebuild one table's binary at `newCapacity` rows.
 *
 * Rows 0..newCapacity-1 are kept byte-for-byte; every empty row among them is relinked
 * into the free list the library and the game walk (first u32 = index of the next empty
 * row, the last pointing at the capacity as its terminator), and the rest of an empty
 * row is zeroed. The string pool (table 2) is cut to the same proportion of capacity,
 * never below the last non-zero byte of the old pool or the highest offset a kept row
 * still references.
 */
export function rebuildTable(data, header, recordIsEmpty, newCapacity) {
  const h = header;
  // The library's header.headerOffset is a constant 0xe8; the field block actually
  // starts at 0xa8 + tableStoreLength (see M20TableHeaderStrategy.parseHeader).
  const hdrOff = 0xa8 + h.tableStoreLength;
  const recBytes = h.recordWords * 4;
  const cap = h.recordCapacity;

  if (newCapacity < 1) throw new Error(`${h.name}: capacity ${newCapacity} < 1`);
  if (newCapacity > cap) throw new Error(`${h.name}: capacity ${newCapacity} > current ${cap}`);
  if (newCapacity * recBytes > (h.table2StartIndex - h.table1StartIndex)) {
    throw new Error(`${h.name}: capacity ${newCapacity} runs past the record area`);
  }

  let head;
  if (h.isArray) {
    head = Buffer.concat([data.subarray(0, h.offsetStart), data.subarray(h.offsetStart, h.offsetStart + newCapacity * 4)]);
  } else {
    head = Buffer.from(data.subarray(0, h.table1StartIndex));
  }
  const records = Buffer.from(data.subarray(h.table1StartIndex, h.table1StartIndex + newCapacity * recBytes));

  // String pool: scale with capacity, but never cut below what a kept row points at
  // or below the pool's last non-zero byte (the strings are still real data even when
  // the schema cannot decode which row owns them).
  const oldTable2 = h.table2Length || 0;
  let newTable2Length = 0;
  if (oldTable2 > 0) {
    const perRow = oldTable2 / cap;
    const pool = data.subarray(h.table2StartIndex, h.table2StartIndex + oldTable2);
    let lastNonZero = -1;
    for (let i = pool.length - 1; i >= 0; i--) if (pool[i]) { lastNonZero = i; break; }
    const keepUpTo = lastNonZero < 0 ? 0 : Math.ceil((lastNonZero + 1) / perRow) * perRow;
    newTable2Length = Math.min(oldTable2, Math.max(Math.ceil(newCapacity * perRow), keepUpTo));
  }
  const table2 = Buffer.from(data.subarray(h.table2StartIndex, h.table2StartIndex + newTable2Length));
  const table3 = h.hasThirdTable ? Buffer.from(data.subarray(h.table3StartIndex)) : Buffer.alloc(0);

  // Relink the free list over the rows we keep.
  const empty = [];
  for (let i = 0; i < newCapacity; i++) if (recordIsEmpty(i)) empty.push(i);
  for (let k = 0; k < empty.length; k++) {
    const row = empty[k];
    const next = k + 1 < empty.length ? empty[k + 1] : newCapacity;
    records.writeUInt32BE(next, row * recBytes);
    records.fill(0, row * recBytes + 4, (row + 1) * recBytes);
  }
  const nextRecordToUse = empty.length ? empty[0] : newCapacity;

  const table1Length = 32 + (h.isArray ? newCapacity * 4 : h.numMembers * 4) + newCapacity * recBytes;
  head.writeUInt32BE(newCapacity, hdrOff + 8); // data1RecordCount
  head.writeUInt32BE(table1Length, hdrOff + 16); // table1Length
  head.writeUInt32BE(newTable2Length, hdrOff + 20); // table2Length
  head.writeUInt32BE(table1Length, hdrOff + 36); // table1Length2
  head.writeUInt32BE(table1Length + newTable2Length + table3.length, hdrOff + 40); // tableTotalLength
  head.writeUInt32BE(newCapacity, hdrOff + 48); // recordCapacity
  head.writeUInt32BE(nextRecordToUse, hdrOff + 60);

  return Buffer.concat([head, records, table2, table3]);
}

/** Highest string-pool byte a kept row still references, or 0. */
export function referencedPoolEnd(records, newCapacity) {
  let end = 0;
  for (const record of records.slice(0, newCapacity)) {
    if (record.isEmpty) continue;
    for (const field of record.fieldsArray) {
      const offset = field.offset;
      if (!offset?.valueInSecondTable) continue;
      const byte = offset.offset / 8;
      // Some tables' offset entries point past the record (a schema/table mismatch);
      // the library cannot read those either, so they carry no usable reference.
      if (byte + 4 > record.data.length) continue;
      const index = record.data.readUInt32BE(byte);
      end = Math.max(end, index + (offset.maxLength || 0));
    }
  }
  return end;
}

function loadPolicy(args) {
  let policy = { ...DEFAULT_POLICY };
  if (args.policy) Object.assign(policy, JSON.parse(fs.readFileSync(args.policy, 'utf8')));
  for (const s of args.sets) {
    const at = s.lastIndexOf('=');
    if (at < 0) throw new Error(`--set expects Name=capacity, got ${s}`);
    policy[s.slice(0, at)] = Number(s.slice(at + 1));
  }
  return policy;
}

/**
 * Rebuild `save` at a smaller table capacity and write it to `out` (nothing is written
 * when `dryRun`). Returns the report; throws if the rebuilt file fails its own read-back.
 */
export async function pruneSave({ save, out, policy: policyOverride, maxFill = 0.05, tables = [], dryRun = false }) {
  if (!dryRun && !out) throw new Error('--out is required unless --dry-run');
  if (out && path.resolve(out) === path.resolve(save)) throw new Error('--out must differ from the input save');

  const raw = fs.readFileSync(save);
  if (raw.subarray(0, 8).toString('latin1') !== MAGIC) throw new Error(`not an ${MAGIC} save: ${save}`);
  const oldStreamLength = readStreamLength(raw);
  const oldStreamEnd = COMPRESSED_DATA_OFFSET + oldStreamLength;
  const unpacked = zlib.inflateSync(raw.subarray(COMPRESSED_DATA_OFFSET));

  const file = await Franchise.create(save, { autoParse: true, saveOnChange: false });
  const tableStart = tableStartOf(unpacked);
  const policy = { ...DEFAULT_POLICY, ...(policyOverride ?? {}) };
  const wanted = tables.length ? new Set(tables) : null;

  const plan = [];
  const rebuilt = new Map();
  for (const table of file.tables) {
    const base = table.name.endsWith('[]') ? table.name.slice(0, -2) : table.name;
    const target = wanted ? (wanted.has(table.name) ? policy[base] ?? policy[table.name] : undefined) : policy[base] ?? policy[table.name];
    if (target === undefined) continue;

    await table.readRecords(null);
    const cap = table.header.recordCapacity;
    const liveIndexes = [];
    for (const record of table.records) if (!record.isEmpty) liveIndexes.push(record.index);
    const maxLiveIndex = liveIndexes.length ? liveIndexes[liveIndexes.length - 1] : -1;
    const live = liveIndexes.length;

    let newCapacity = Math.min(cap, Math.max(target, maxLiveIndex + 1, 1));
    if (newCapacity >= cap) {
      plan.push({ name: table.name, tableId: table.header.tableId, capacity: cap, live, newCapacity: cap, bytes: table.data.length, newBytes: table.data.length, skipped: 'already at or below the target' });
      continue;
    }
    if (live / cap > maxFill) {
      plan.push({ name: table.name, tableId: table.header.tableId, capacity: cap, live, newCapacity: cap, bytes: table.data.length, newBytes: table.data.length, skipped: `fill ${(live / cap * 100).toFixed(1)}% > --max-fill` });
      continue;
    }

    // Never cut the string pool below what a kept row still reads.
    const poolEnd = referencedPoolEnd(table.records, newCapacity);
    const perRow = (table.header.table2Length || 0) / cap;
    if (poolEnd > Math.ceil(newCapacity * perRow)) {
      const needed = Math.ceil(poolEnd / perRow);
      if (needed > cap) throw new Error(`${table.name}: string pool reference at ${poolEnd} exceeds the table's pool`);
      newCapacity = Math.min(cap, needed);
    }

    const isEmpty = (i) => table.records[i]?.isEmpty ?? true;
    const bytes = rebuildTable(table.data, table.header, isEmpty, newCapacity);
    rebuilt.set(table.index, bytes);
    plan.push({ name: table.name, tableId: table.header.tableId, capacity: cap, live, newCapacity, bytes: table.data.length, newBytes: bytes.length });
  }

  const parts = [unpacked.subarray(0, tableStart)];
  for (const table of file.tables) parts.push(rebuilt.get(table.index) ?? table.data);
  const originalTableEnd = tableStart + file.tables.reduce((n, t) => n + t.data.length, 0);
  parts.push(unpacked.subarray(originalTableEnd)); // trailing bytes after the last table
  const newUnpacked = Buffer.concat(parts);

  const deflated = zlib.deflateSync(newUnpacked, { windowBits: 15 });

  const report = {
    save,
    out: out ?? null,
    unpackedBefore: unpacked.length,
    unpackedAfter: newUnpacked.length,
    saved: unpacked.length - newUnpacked.length,
    streamBefore: oldStreamLength,
    streamAfter: deflated.length,
    tables: plan.filter((p) => !p.skipped),
    skipped: plan.filter((p) => p.skipped),
    problems: [],
  };
  for (const p of report.tables) p.saved = p.bytes - p.newBytes;

  if (dryRun) return report;

  if (deflated.length > oldStreamLength) {
    throw new Error(`rebuilt stream (${deflated.length}) is longer than the original (${oldStreamLength}); the tail's fixed offsets would shift`);
  }

  // FBCHUNKS header, the new stream, zero fill up to the old stream end, original tail.
  const outContents = Buffer.from(raw);
  const header = Buffer.from(raw.subarray(0, COMPRESSED_DATA_OFFSET));
  writeStreamLength(header, deflated.length);
  header.copy(outContents, 0);
  outContents.fill(0, COMPRESSED_DATA_OFFSET + deflated.length, oldStreamEnd);
  deflated.copy(outContents, COMPRESSED_DATA_OFFSET);
  fs.writeFileSync(out, outContents);

  // Read back: the file must parse, every rebuilt table must show the new capacity,
  // and every live row must still be live.
  const check = await Franchise.create(out, { autoParse: true, saveOnChange: false });
  for (const p of report.tables) {
    const base = p.name.endsWith('[]') ? p.name.slice(0, -2) : p.name;
    const after = check.tables.find((t) => t.header.tableId === p.tableId && (t.name.endsWith('[]') ? t.name.slice(0, -2) : t.name) === base);
    if (!after) { report.problems.push(`${p.name} (id ${p.tableId}) missing after rebuild`); continue; }
    if (after.header.recordCapacity !== p.newCapacity) report.problems.push(`${p.name}: capacity ${after.header.recordCapacity} != ${p.newCapacity}`);
    await after.readRecords(null);
    const liveAfter = after.records.filter((r) => !r.isEmpty).length;
    if (liveAfter !== p.live) report.problems.push(`${p.name}: ${liveAfter} live rows after rebuild, expected ${p.live}`);
  }
  const livePlayersBefore = await liveCount(file, 'Player');
  const livePlayersAfter = await liveCount(check, 'Player');
  if (livePlayersAfter !== livePlayersBefore) report.problems.push(`Player live rows ${livePlayersBefore} -> ${livePlayersAfter}`);

  report.fileSize = outContents.length;
  report.fileSizeBefore = raw.length;
  return report;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const report = await pruneSave({
    save: args.save,
    out: args.out,
    policy: loadPolicy(args),
    maxFill: args.maxFill,
    tables: args.tables,
    dryRun: args.dryRun,
  });
  if (args.report && !args.dryRun) {
    fs.writeFileSync(args.report, JSON.stringify({ ...report, save: path.basename(report.save) }, null, 2));
  }
  printPlan(report);
  if (args.dryRun) return;
  console.log(`\nwrote ${args.out}`);
  console.log(`file size ${report.fileSize} (was ${report.fileSizeBefore}); stream ${report.streamAfter} (was ${report.streamBefore})`);
  if (report.problems.length) {
    console.error('\nREAD-BACK PROBLEMS:');
    for (const p of report.problems) console.error('  ' + p);
    process.exitCode = 1;
  } else {
    console.log('read-back clean: parses, capacities and live rows as planned');
  }
}

async function liveCount(file, name) {
  let n = 0;
  for (const t of file.getAllTablesByName(name)) {
    await t.readRecords(null);
    n += t.records.filter((r) => !r.isEmpty).length;
  }
  return n;
}

function printPlan(report) {
  const kb = (n) => (n / 1024).toFixed(1).padStart(9);
  console.log('table'.padEnd(42), 'capacity'.padStart(16), 'live'.padStart(6), 'bytes'.padStart(9), 'saved KB'.padStart(9));
  for (const p of report.tables) {
    console.log(p.name.padEnd(42), `${p.capacity} -> ${p.newCapacity}`.padStart(16), String(p.live).padStart(6), kb(p.bytes), kb(p.saved));
  }
  for (const p of report.skipped) console.log(p.name.padEnd(42), `${p.capacity} -> (kept)`.padStart(16), String(p.live).padStart(6), kb(p.bytes), '   -'.padStart(9), '  ' + p.skipped);
  console.log(`\nunpacked ${report.unpackedBefore} -> ${report.unpackedAfter} bytes (saved ${report.saved}, ${(report.saved / 1024).toFixed(1)} KB)`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err) => {
    console.error(err.stack ?? err.message);
    process.exitCode = 1;
  });
}
