#!/usr/bin/env node
// Writes edits into a copy of a Madden 27 franchise save, then reads the copy back to verify them
// (docs/in_progress/PC_MIGRATION_PLAN.md §4.3, docs/in_progress/V2_CORE_BUILD_PLAN.md §2.3).
//
//   node save_writer.mjs <CAREER-save> <edits.json> --out <new-save> [--overwrite-out]
//
// edits.json, every key optional:
//   {"players": [{"pgid": 22692, "set": {"FirstName": "Mu", "SpeedRating": 99, "PLYR_COMMENT": 4600}}],
//    "records": [{"table": "DraftPick", "tableId": 4540, "row": 17, "set": {...}}],
//    "release": [12345, ...],
//    "sign":  [{"pgid": 123, "team_index": 3, "list": "Roster", "contract": {"Length": 3, "SalaryYear0": 105, ...}}],
//    "trade": [{"from_team_index": 3, "to_team_index": 12, "pgids": [123], "picks": [{"tableId": 4540, "row": 17}]}],
//    "depthCharts": [{"team_index": 3, "slots": {"QB": [22686, 9123], ...}}]}
// (pgid = Player.PresentationId, field names as in the save's Player table).
// * "players" sets Player fields. An edit's optional "contract" block, {"Length": 4, "CurrentYear": 0, "SalaryYear0":
//   1050, ...}, sets fields of the player's own PlayerContract record in place (contract_edits.mjs; money in $10,000
//   units).
// * "records" edits other tables by table id and row (record_edits.mjs: draft picks, draft-class rows).
// * "release" sends rostered players to free agency (release.mjs: status, team, contract, team lists and depth charts,
//   the free-agent list, roster counts).
// * "sign" signs free agents and draft prospects to a Roster (reusing an unreferenced PlayerContract row, e.g. one a
//   release in the same write leaves) or a PracticeSquad (sign.mjs).
// * "trade" moves players with their contracts, and draft picks, from one team to another (trade.mjs).
// * "depthCharts" replaces depth-chart positions with up to 6 rostered players each (depth_chart.mjs).
// A player may appear in only one of release / sign / trade, and a signed or traded player's "players" edit may not
// set a field the move sets (TeamIndex, ContractStatus, ...). Moves apply in the order release, sign, trade,
// depthCharts, each against the lists as the earlier ones left them, so a release can free the contract row a signing
// takes and a depth chart can list a player signed or traded in the same write.
//
// Safety: the input save is never written (it is copied first, and --out may not be the input); --out must not
// exist unless --overwrite-out. Every edit and move is planned and checked against the fields' schemas before anything
// is set (the library itself stores out-of-range numbers and over-long strings), so one bad edit writes nothing. The
// written file is re-opened and every edit and move read back. Reference fields outside the moves (Contract, team
// links: a contract is edited through its record) and OverallRating's recalculation are out of scope: Madden, not the
// library, recomputes OVR, which is a PC check.
import { copyFileSync, existsSync, mkdtempSync, readFileSync, renameSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { basename, join, resolve } from 'path';
import { fileURLToPath } from 'url';
import Franchise from 'madden-franchise';
import { fieldMeta, fieldValueError } from './lib.mjs';
import { contractRecordOf, planContractEdits } from './contract_edits.mjs';
import { applyRecordEdits, planRecordEdits, verifyRecordEdits } from './record_edits.mjs';
import { applyRelease, planRelease, releaseSummary, verifyRelease } from './release.mjs';
import { applySign, planSign, refreshPlan, signSummary, verifySign } from './sign.mjs';
import { applyTrade, planTrade, tradeSummary, verifyTrade } from './trade.mjs';
import { applyDepthCharts, depthChartSummary, planDepthCharts, verifyDepthCharts } from './depth_chart.mjs';

// Player fields each move writes; a "players" edit of the same player may not set them too.
const MOVE_FIELDS = {
  sign: ['TeamIndex', 'ContractStatus', 'Contract', 'Mindset', 'WeeksWithTeam'],
  trade: ['TeamIndex', 'ContractStatus', 'Contract', 'PrevTeamIndex', 'WeeksWithTeam', 'PLYR_ISCAPTAIN', 'TradeStatus'],
};

// Errors for a pgid named by more than one of release / sign / trade, or by a move and a "players" edit that sets a
// field the move sets (release.mjs itself refuses any "players" edit of a released player).
export function moveConflicts(edits) {
  const errors = [];
  const where = new Map();
  const note = (pgid, move) => where.set(pgid, [...new Set([...(where.get(pgid) ?? []), move])]);
  for (const pgid of edits.release ?? []) note(pgid, 'release');
  for (const s of edits.sign ?? []) note(s?.pgid, 'sign');
  for (const t of edits.trade ?? []) for (const pgid of t?.pgids ?? []) note(pgid, 'trade');
  for (const [pgid, moves] of where) if (moves.length > 1) errors.push(`pgid ${pgid}: in more than one move (${moves.join(', ')})`);
  for (const e of edits.players ?? []) {
    for (const move of where.get(e.pgid) ?? []) {
      const clash = Object.keys(e.set ?? {}).filter(k => (MOVE_FIELDS[move] ?? []).includes(k));
      if (clash.length) errors.push(`pgid ${e.pgid}: a players edit sets ${clash.join(', ')}, which the ${move} move sets`);
    }
  }
  const traded = new Set((edits.trade ?? []).flatMap(t => t?.picks ?? []).map(p => `${p?.tableId}:${p?.row}`));
  for (const r of edits.records ?? []) {
    if (traded.has(`${r.tableId}:${r.row}`)) errors.push(`${r.table}[${r.tableId}] row ${r.row}: edited in "records" and traded`);
  }
  return errors;
}

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
    errors.push(...moveConflicts(edits));
    const release = edits.release?.length
      ? await planRelease(franchise, edits.release, { exclude: new Set(targets.map(t => t.pgid)) }) : null;
    errors.push(...(release?.errors ?? []));
    const sign = edits.sign?.length ? await planSign(franchise, edits.sign, {
      freedContracts: (release?.plan.releases ?? []).map(r => r.contract).filter(Boolean) }) : null;
    errors.push(...(sign?.errors ?? []));
    const trade = edits.trade?.length ? await planTrade(franchise, edits.trade) : null;
    errors.push(...(trade?.errors ?? []));
    // Rosters as the other moves leave them, for the depth charts.
    const roster = { added: new Map(), removed: new Set() };
    const add = (team, row) => roster.added.set(team, new Set([...(roster.added.get(team) ?? []), row]));
    for (const r of release?.plan.releases ?? []) roster.removed.add(r.row);
    for (const s of sign?.plan.signs ?? []) if (s.list === 'Roster') add(s.teamIndex, s.row);
    for (const m of trade?.plan.moves ?? []) {
      roster.removed.add(m.row);
      if (m.list === 'Roster') add(m.toIndex, m.row);
    }
    const depth = edits.depthCharts?.length ? await planDepthCharts(franchise, edits.depthCharts, { roster }) : null;
    errors.push(...(depth?.errors ?? []));
    if (errors.length) throw new Error(`edits refused, nothing written:\n  ${errors.join('\n  ')}`);
    for (const t of targets) t.record[t.key] = t.value;
    applyRecordEdits(records.targets);
    if (release) applyRelease(release.plan);
    if (sign) applySign(sign.plan);
    if (trade) applyTrade(trade.plan);
    if (depth) applyDepthCharts(depth.plan);
    // Each move's read-back expects the lists and counts as the whole write left them.
    for (const m of [release, sign, trade, depth]) if (m) refreshPlan(m.plan);
    const staged = join(dir, 'out');
    await franchise.save(staged);
    try {
      renameSync(staged, out);
    } catch (err) {
      if (err.code === 'EXDEV') {
        copyFileSync(staged, out);
        rmSync(staged, { force: true });
      } else {
        throw err;
      }
    }

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
    if (sign) mismatches.push(...await verifySign(check, sign.plan));
    if (trade) mismatches.push(...await verifyTrade(check, trade.plan));
    if (depth) mismatches.push(...await verifyDepthCharts(check, depth.plan));
    if (mismatches.length) throw new Error(`read-back failed:\n  ${mismatches.join('\n  ')}`);
    return { out, players: new Set(targets.map(t => t.pgid)).size, fields: targets.length,
      contracts: new Set(targets.filter(t => t.contract).map(t => t.pgid)).size,
      records: new Set(records.targets.map(t => t.where)).size, recordFields: records.targets.length,
      release: release ? releaseSummary(release.plan) : null, sign: sign ? signSummary(sign.plan) : null,
      trade: trade ? tradeSummary(trade.plan) : null, depthCharts: depth ? depthChartSummary(depth.plan) : null,
      verified: true };
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
    if (report.sign) console.error(`signed: ${report.sign.signed} (${report.sign.roster} roster, ${report.sign.practice_squad} practice squad) ${JSON.stringify(report.sign)}`);
    if (report.trade) console.error(`traded: ${report.trade.players} players, ${report.trade.picks} picks ${JSON.stringify(report.trade)}`);
    if (report.depthCharts) console.error(`depth charts: ${report.depthCharts.positions} positions on ${report.depthCharts.teams} teams ${JSON.stringify(report.depthCharts)}`);
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  }
}
