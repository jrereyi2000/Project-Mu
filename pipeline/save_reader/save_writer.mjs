#!/usr/bin/env node
// Writes Player field edits into a copy of a Madden 27 franchise save, then reads the copy back to verify them
// (docs/PC_MIGRATION_PLAN.md §4.3; the teleporter will build on this).
//
//   node save_writer.mjs <CAREER-save> <edits.json> --out <new-save> [--overwrite-out]
//
// edits.json: {"players": [{"pgid": 22692, "set": {"FirstName": "Mu", "SpeedRating": 99, "PLYR_COMMENT": 4600}}],
//              "release": [12345, ...]}
// (pgid = Player.PresentationId, field names as in the save's Player table). "release" sends rostered players to free
// agency (release.mjs: status, team, contract, team lists and depth charts, the free-agent list, roster counts).
// An edit's optional "contract" block, {"Length": 4, "CurrentYear": 0, "SalaryYear0": 1050, ...}, sets fields of the
// player's own PlayerContract record in place (contract_edits.mjs; money in $10,000 units), with the same checks and
// read-back.
// An optional "records" list edits other tables by table id and row (record_edits.mjs: draft picks, draft-class
// rows), with the same checks and read-back.
//
// Safety: the input save is never written (it is copied first, and --out may not be the input); --out must not
// exist unless --overwrite-out. Every edit is checked against the field's schema before anything is set (the
// library itself stores out-of-range numbers and over-long strings), so a bad edit writes nothing. Reference fields
// (Contract, team links: a contract is edited through its record, never re-pointed) and OverallRating's recalculation are out of scope: Madden, not the library, recomputes
// OVR, which is a PC check.
import { copyFileSync, existsSync, mkdtempSync, readFileSync, renameSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { basename, join, resolve } from 'path';
import { fileURLToPath } from 'url';
import Franchise from 'madden-franchise';
import { fieldMeta, fieldValueError } from './lib.mjs';
import { contractRecordOf, planContractEdits } from './contract_edits.mjs';
import { applyRecordEdits, planRecordEdits, verifyRecordEdits } from './record_edits.mjs';
import { applyRelease, planRelease, releaseSummary, verifyRelease } from './release.mjs';

async function playerTable(franchise) {
  const all = franchise.getAllTablesByName('Player');
  for (const t of all) await t.readRecords();
  return all.reduce((best, t) => (!best || t.records.length > best.records.length ? t : best), null);
}

// Finds each edit's record and checks every value; returns { targets, errors } without changing anything.
export function planEdits(table, edits) {
  const byPgid = new Map();
  table.records.forEach(r => {
    if (r.isEmpty) return;
    const list = byPgid.get(r.PresentationId) ?? [];
    list.push(r);
    byPgid.set(r.PresentationId, list);
  });
  const targets = [];
  const errors = [];
  for (const edit of edits.players ?? []) {
    const hits = byPgid.get(edit.pgid) ?? [];
    if (hits.length !== 1) {
      errors.push(`pgid ${edit.pgid}: ${hits.length} players`);
      continue;
    }
    const record = hits[0];
    for (const [key, value] of Object.entries(edit.set ?? {})) {
      const meta = fieldMeta(record, key);
      const err = meta ? fieldValueError(meta, value) : 'no such Player field';
      if (err) errors.push(`pgid ${edit.pgid} ${key} = ${JSON.stringify(value)}: ${err}`);
      else targets.push({ pgid: edit.pgid, record, key, value, meta });
    }
  }
  return { targets, errors };
}

export async function writeSave(inputPath, edits, outPath, { overwriteOut = false } = {}) {
  const input = resolve(inputPath);
  const out = resolve(outPath);
  if (out === input) throw new Error('--out must not be the input save');
  if (existsSync(out) && !overwriteOut) throw new Error(`${out} exists (pass --overwrite-out to replace it)`);

  const dir = mkdtempSync(join(tmpdir(), 'mu-write-'));
  try {
    const work = join(dir, basename(input));
    copyFileSync(input, work);
    const franchise = await Franchise.create(work, { autoParse: true, saveOnChange: false });
    const players = await playerTable(franchise);
    const { targets, errors } = planEdits(players, edits);
    const contract = await planContractEdits(franchise, players, edits);
    targets.push(...contract.targets);
    errors.push(...contract.errors);
    const records = await planRecordEdits(franchise, edits.records);
    errors.push(...records.errors);
    const release = edits.release?.length
      ? await planRelease(franchise, edits.release, { exclude: new Set(targets.map(t => t.pgid)) }) : null;
    errors.push(...(release?.errors ?? []));
    if (errors.length) throw new Error(`edits refused, nothing written:\n  ${errors.join('\n  ')}`);
    for (const t of targets) t.record[t.key] = t.value;
    applyRecordEdits(records.targets);
    if (release) applyRelease(release.plan);
    const staged = join(dir, 'out');
    await franchise.save(staged);
    renameSync(staged, out);

    // Read back from the written file and compare every edited field.
    const check = await Franchise.create(out, { autoParse: true, saveOnChange: false });
    const table = await playerTable(check);
    const mismatches = [];
    for (const t of targets) {
      const player = table.records.find(x => !x.isEmpty && x.PresentationId === t.pgid);
      const r = t.contract && player ? await contractRecordOf(check, player) : player;
      const stored = v => (t.meta.enumValues ? t.meta.enumValues[v] : v);
      if (!r || stored(r[t.key]) !== stored(t.value)) {
        mismatches.push(`pgid ${t.pgid} ${t.contract ? 'contract ' : ''}${t.key}: wrote ${t.value}, read ${r?.[t.key]}`);
      }
    }
    mismatches.push(...await verifyRecordEdits(check, records.targets));
    if (release) mismatches.push(...await verifyRelease(check, release.plan));
    if (mismatches.length) throw new Error(`read-back failed:\n  ${mismatches.join('\n  ')}`);
    return { out, players: new Set(targets.map(t => t.pgid)).size, fields: targets.length,
      contracts: new Set(targets.filter(t => t.contract).map(t => t.pgid)).size,
      records: new Set(records.targets.map(t => t.where)).size, recordFields: records.targets.length,
      release: release ? releaseSummary(release.plan) : null, verified: true };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function parseArgs(argv) {
  const args = { save: null, edits: null, out: null, overwriteOut: false };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--out') args.out = argv[++i];
    else if (argv[i] === '--overwrite-out') args.overwriteOut = true;
    else if (!args.save) args.save = argv[i];
    else if (!args.edits) args.edits = argv[i];
  }
  return args;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  const a = parseArgs(process.argv.slice(2));
  if (!a.save || !a.edits || !a.out) {
    console.error('usage: node save_writer.mjs <CAREER-save> <edits.json> --out <new-save> [--overwrite-out]');
    process.exit(2);
  }
  try {
    const report = await writeSave(a.save, JSON.parse(readFileSync(a.edits, 'utf8')), a.out, a);
    console.error(`${report.fields} fields on ${report.players} players (${report.contracts} contracts) and `
      + `${report.recordFields} on ${report.records} other records written and read back → ${report.out}`);
    if (report.release) console.error(`released to free agency: ${JSON.stringify(report.release)}`);
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  }
}
