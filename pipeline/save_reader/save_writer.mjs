#!/usr/bin/env node
// Writes edits into a copy of a Madden 27 franchise save, then reads the copy back to verify them
// (docs/done/PC_MIGRATION_PLAN.md §4.3, docs/done/V2_CORE_BUILD_PLAN.md §2.3).
//
//   node save_writer.mjs <CAREER-save> <edits.json> --out <new-save> [--overwrite-out]
//
// edits.json, every key optional:
//   {"players": [{"pgid": 22692, "set": {"FirstName": "Mu", "SpeedRating": 99, "PLYR_COMMENT": 4600}}],
//    "records": [{"table": "DraftPick", "tableId": 4540, "row": 17, "set": {...}}],
//    "staff": [{"action": "release", "pgid": 9001},
//              {"action": "assign", "pgid": 9002, "team_index": 7, "position": "DefensiveCoordinator", "contract": {...}},
//              {"action": "create", "key": "coach-311", "team_index": 7, "position": "HeadCoach",
//               "profile": {...}, "visuals_template": 9010, "contract": {...}}],
//    "release": [12345, ...],
//    "sign":  [{"pgid": 123, "team_index": 3, "list": "Roster", "contract": {"Length": 3, "SalaryYear0": 105, ...}}],
//    "trade": [{"from_team_index": 3, "to_team_index": 12, "pgids": [123], "picks": [{"tableId": 4540, "row": 17}]}],
//    "depthCharts": [{"team_index": 3, "slots": {"QB": [22686, 9123], ...}}]}
// (pgid = Player.PresentationId, field names as in the save's Player table; a staff pgid is Coach.PresentationId).
// * "players" sets Player fields. An edit's optional "contract" block, {"Length": 4, "CurrentYear": 0, "SalaryYear0":
//   1050, ...}, sets fields of the player's own PlayerContract record in place (contract_edits.mjs; money in $10,000
//   units).
// * "records" edits other tables by table id and row (record_edits.mjs: draft picks, draft-class rows).
// * "staff" writes head coaches and coordinators (staff.mjs): a release leaves for the coach free-agent pool
//   (TeamIndex 32; "retire": true makes him Retired), an assign moves an existing record to a team's HC/OC/DC seat, and
//   a create clones a template coach's visuals/talents into an empty Coach row and assigns him. A head-coach seat also
//   re-points the team's user (Team.UserCharacter, FranchiseUser.UserEntity/Team, Coach.IsUserControlled). This block
//   plans and applies BEFORE the player moves; its report lists each new PresentationId for Mu to store.
// * "release" sends rostered players to free agency (release.mjs: status, team, contract, team lists and depth charts,
//   the free-agent list, roster counts).
// * "sign" signs free agents and draft prospects to a Roster (reusing an unreferenced PlayerContract row, e.g. one a
//   release in the same write leaves, else filling an empty one: sign.mjs) or a PracticeSquad (sign.mjs). A sign item
//   with "switch": true declares a mid-season team switch: its pgid is also in "release", and the signing must be to a
//   different team than the one the release leaves (see below).
// * "trade" moves players with their contracts, and draft picks, from one team to another (trade.mjs).
// * "depthCharts" replaces depth-chart positions with up to 6 rostered players each (depth_chart.mjs).
// A player may appear in only one of release / sign / trade, and a signed or traded player's "players" edit may not
// set a field the move sets (TeamIndex, ContractStatus, ...). One exception is a declared switch: the same pgid in
// "release" and in a "sign" item with "switch": true, to a different team than the player's current one. The release
// writes the old deal (its contract row is left unreferenced, the dead cap Madden charges) and the sign writes the new
// contract to a different team; without the flag the same pgid in both moves is refused, as is a switch sign to the
// player's current team, a flagged sign with no release, and every other overlap. Moves apply in the order staff,
// release, sign, trade, depthCharts, each against the lists as the earlier ones left them, so a staff release can free
// the Coach seat a create fills, a release can free the contract row a signing takes, and a depth chart can list a
// player signed or traded in the same write.
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
import { encodeRef, fieldMeta, fieldValueError } from './lib.mjs';
import { contractRecordOf, planContractEdits } from './contract_edits.mjs';
import { applyRecordEdits, planRecordEdits, verifyRecordEdits } from './record_edits.mjs';
import { applyRelease, planRelease, releaseSummary, verifyRelease } from './release.mjs';
import { applyStaff, planStaff, staffSummary, verifyStaff } from './staff.mjs';
import { applySign, planSign, refreshPlan, signSummary, verifySign } from './sign.mjs';
import { applyTrade, planTrade, tradeSummary, verifyTrade } from './trade.mjs';
import { applyDepthCharts, depthChartSummary, planDepthCharts, verifyDepthCharts } from './depth_chart.mjs';

// Player fields each move writes; a "players" edit of the same player may not set them too.
const MOVE_FIELDS = {
  sign: ['TeamIndex', 'ContractStatus', 'Contract', 'Mindset', 'WeeksWithTeam'],
  trade: ['TeamIndex', 'ContractStatus', 'Contract', 'PrevTeamIndex', 'WeeksWithTeam', 'PLYR_ISCAPTAIN', 'TradeStatus'],
};

// Errors for a pgid named by more than one of release / sign / trade, or by a move and a "players" edit that sets a
// field the move sets (release.mjs itself refuses any "players" edit of a released player). One exception: a release
// plus a sign of the same player to a DIFFERENT team, when the sign item sets "switch": true. `currentTeams` maps
// pgid -> the player's TeamIndex in the save, so a switch back to the same team (a promotion, not a switch) is refused.
export function moveConflicts(edits, currentTeams = new Map()) {
  const errors = [];
  const where = new Map();
  const note = (pgid, move) => where.set(pgid, [...new Set([...(where.get(pgid) ?? []), move])]);
  const release = edits.release ?? [];
  const signs = edits.sign ?? [];
  for (const pgid of release) note(pgid, 'release');
  for (const s of signs) note(s?.pgid, 'sign');
  for (const t of edits.trade ?? []) for (const pgid of t?.pgids ?? []) note(pgid, 'trade');

  const declared = new Map();
  for (const s of signs) if (s?.switch === true) declared.set(s.pgid, s);

  for (const [pgid, moves] of where) {
    if (moves.length <= 1) continue;
    const sw = declared.get(pgid);
    const released = release.filter(x => x === pgid).length;
    const signed = signs.filter(x => x?.pgid === pgid).length;
    if (sw && moves.length === 2 && moves.includes('release') && moves.includes('sign')) {
      if (released !== 1 || signed !== 1) {
        errors.push(`pgid ${pgid}: a switch needs exactly one release and one sign (${released} release, ${signed} sign)`);
      } else if (currentTeams.has(pgid) && currentTeams.get(pgid) === sw.team_index) {
        errors.push(`pgid ${pgid}: switch sign is to the current team (${sw.team_index})`);
      }
      continue;
    }
    errors.push(`pgid ${pgid}: in more than one move (${moves.join(', ')})`);
  }
  // The flag never stands on its own: a switch sign must name a release as well.
  for (const pgid of declared.keys()) {
    if (!release.includes(pgid)) errors.push(`pgid ${pgid}: switch sign is not in release`);
  }
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

// A declared switch's old PlayerContract row is the deal Madden charges dead money on: it must stay unreferenced, so
// the signing takes a different contract row. planSign hands a released row to a signing, and after the release is
// applied that pool includes the switched player's own row; move any signing that landed on a switch's old row onto an
// empty row (or another released row). `set.Contract` is re-pointed with it. Returns error messages.
function avoidSwitchDealReuse(sign, release, switchPgids) {
  const errors = [];
  const signs = sign?.plan.signs ?? [];
  const records = sign?.plan.contractRecords;
  if (!records || !switchPgids.size) return errors;
  const table = signs.find(s => s.contract)?.contract.table;
  if (!table) return errors;
  const key = row => `${table}:${row}`;
  const protectedRows = new Set((release?.plan.releases ?? [])
    .filter(r => switchPgids.has(r.pgid) && r.contract?.table === table)
    .map(r => key(r.contract.row)));
  if (!protectedRows.size) return errors;
  const assigned = new Set(signs.filter(s => s.contract).map(s => key(s.contract.row)));
  const emptyRows = records.map((r, row) => row).filter(row => records[row].isEmpty);
  const freeRows = (release?.plan.releases ?? []).map(r => r.contract?.row).filter(row => Number.isInteger(row));
  const next = () => emptyRows.find(row => !assigned.has(key(row)))
    ?? freeRows.find(row => !assigned.has(key(row)) && !protectedRows.has(key(row)));
  for (const s of signs) {
    if (!s.contract || !protectedRows.has(key(s.contract.row))) continue;
    const row = next();
    if (row === undefined) {
      errors.push(`sign pgid ${s.pgid}: no free contract row to leave the released deal's dead cap`);
      continue;
    }
    assigned.delete(key(s.contract.row));
    assigned.add(key(row));
    s.contract = { ...s.contract, row, created: records[row].isEmpty };
    s.set.Contract = encodeRef(table, row);
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
    // autoUnempty: setting a field on an empty record turns it into a live one (a Roster signing with no free
    // PlayerContract row fills an empty one, sign.mjs / P1-08).
    const franchise = await Franchise.create(work, { autoParse: true, saveOnChange: false, autoUnempty: true });
    const players = await playerTable(franchise);
    const { targets, errors } = planEdits(players, edits);
    const contract = await planContractEdits(franchise, players, edits);
    targets.push(...contract.targets);
    errors.push(...contract.errors);
    const records = await planRecordEdits(franchise, edits.records);
    errors.push(...records.errors);
    // A switch sign names the player's release explicitly; the flag is the only way the same pgid may be in both.
    const switchPgids = new Set((edits.sign ?? []).filter(s => s?.switch === true).map(s => s.pgid));
    const currentTeams = new Map();
    for (const r of players.records) if (!r.isEmpty) currentTeams.set(r.PresentationId, r.TeamIndex);
    errors.push(...moveConflicts(edits, currentTeams));
    // The staff block runs before the player moves (coaches and players share no records); its own release/assign/
    // create order is internal (staff.mjs).
    const staff = edits.staff?.length ? await planStaff(franchise, edits.staff) : null;
    errors.push(...(staff?.errors ?? []));
    const release = edits.release?.length
      ? await planRelease(franchise, edits.release, { exclude: new Set(targets.map(t => t.pgid)) }) : null;
    errors.push(...(release?.errors ?? []));
    // A declared switch is one weekly move written as a release and then a signing of the same player. Plan and apply
    // the release first so the signing is planned against the free agent it really becomes (status, team, freed
    // contract row, free-agent list); the signing still applies after the other field edits, in release-then-sign
    // order. Without a switch the old plan-everything-then-apply order is unchanged.
    const releasedEarly = Boolean(switchPgids.size && release && !release.errors.length);
    if (releasedEarly) applyRelease(release.plan);
    const sign = edits.sign?.length ? await planSign(franchise, edits.sign, {
      freedContracts: (release?.plan.releases ?? []).map(r => r.contract).filter(Boolean) }) : null;
    errors.push(...(sign?.errors ?? []));
    if (sign) errors.push(...avoidSwitchDealReuse(sign, release, switchPgids));
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
    if (staff) await applyStaff(staff.plan);
    if (release && !releasedEarly) applyRelease(release.plan);
    if (sign) applySign(sign.plan);
    if (trade) applyTrade(trade.plan);
    if (depth) applyDepthCharts(depth.plan);
    // Each move's read-back expects the lists and counts as the whole write left them.
    for (const m of [release, sign, trade, depth]) if (m) refreshPlan(m.plan);
    // A switch player's release fields are overwritten by his signing: verifySign reads those back, so leave only the
    // fields the release alone wrote to verifyRelease.
    for (const s of sign?.plan.signs ?? []) {
      if (!switchPgids.has(s.pgid)) continue;
      const r = (release?.plan.releases ?? []).find(x => x.pgid === s.pgid);
      if (r) for (const k of Object.keys(s.set)) delete r.set[k];
    }
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
    if (staff) mismatches.push(...await verifyStaff(check, staff.plan));
    if (release) mismatches.push(...await verifyRelease(check, release.plan));
    if (sign) mismatches.push(...await verifySign(check, sign.plan));
    if (trade) mismatches.push(...await verifyTrade(check, trade.plan));
    if (depth) mismatches.push(...await verifyDepthCharts(check, depth.plan));
    if (mismatches.length) throw new Error(`read-back failed:\n  ${mismatches.join('\n  ')}`);
    const switches = [];
    for (const s of sign?.plan.signs ?? []) {
      const r = (release?.plan.releases ?? []).find(x => x.pgid === s.pgid);
      if (r && switchPgids.has(s.pgid)) switches.push({ pgid: s.pgid, from_team_index: r.teamIndex,
        from_team: r.team, to_team_index: s.teamIndex, to_team: s.team, list: s.list });
    }
    return { out, players: new Set(targets.map(t => t.pgid)).size, fields: targets.length,
      contracts: new Set(targets.filter(t => t.contract).map(t => t.pgid)).size,
      records: new Set(records.targets.map(t => t.where)).size, recordFields: records.targets.length,
      staff: staff ? staffSummary(staff.plan) : null,
      release: release ? releaseSummary(release.plan) : null, sign: sign ? signSummary(sign.plan) : null,
      trade: trade ? tradeSummary(trade.plan) : null, depthCharts: depth ? depthChartSummary(depth.plan) : null,
      switches, verified: true };
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
    if (report.staff) console.error(`staff: ${report.staff.released} released, ${report.staff.assigned} assigned, `
      + `${report.staff.created} created ${JSON.stringify(report.staff)}`);
    if (report.release) console.error(`released to free agency: ${JSON.stringify(report.release)}`);
    if (report.sign) console.error(`signed: ${report.sign.signed} (${report.sign.roster} roster, ${report.sign.practice_squad} practice squad) ${JSON.stringify(report.sign)}`);
    if (report.switches.length) console.error(`switched teams: ${report.switches.length} ${JSON.stringify(report.switches)}`);
    if (report.trade) console.error(`traded: ${report.trade.players} players, ${report.trade.picks} picks ${JSON.stringify(report.trade)}`);
    if (report.depthCharts) console.error(`depth charts: ${report.depthCharts.positions} positions on ${report.depthCharts.teams} teams ${JSON.stringify(report.depthCharts)}`);
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  }
}
