// Moves a player between his team's Roster and PracticeSquad without releasing him, for save_writer.mjs's "lists"
// list (audit-5 U18 R3: a same-team list change is not a release + signing, which the writer only accepts to another
// team):
//
//   {"lists": [{"pgid": 123, "team_index": 3, "list": "Roster", "contract": {"Length": 3, "SalaryYear0": 105, ...}},
//              {"pgid": 456, "team_index": 7, "list": "PracticeSquad"}]}
//
// A promotion (PracticeSquad -> Roster) gives him a new contract from the same free/empty PlayerContract pool a
// signing uses (sign.mjs contractSlots/allocateContract), points Player.Contract at it, takes him out of
// League.FreeAgents (practice-squad players are listed there) and raises his team's ActiveRosterSize,
// SalCapRosterSize and SalCapNextYearRosterSize (when the deal runs past this year). A demotion (Roster ->
// PracticeSquad) clears Player.Contract (his old row stays in the table unreferenced, the dead money a release
// leaves), appends him to League.FreeAgents, takes him out of his team's depth-chart positions (later entries move
// up) and lowers those three counts. Both keep TeamIndex (he stays on the same team), set Mindset Content and
// WeeksWithTeam 0, and a demotion also clears the captain flag. The same-team lists do not take a contract row freed
// by a demotion in the same write: only rows a release freed, the save's spare rows and empty rows are used, because
// the old row is left unreferenced on purpose.
//
// The trade block composes a trade with the same list move; see trade.mjs. Left to Madden (PC checks): cap totals and
// the in-game effects of the contract change.
import { NO_REF, decodeRef, encodeRef, packedRefs } from './lib.mjs';
import {
  allocateContract, applyCounts, contractSlots, countDelta, countErrors, fieldErrors, fieldMismatches,
  leagueContext, ListOps, moveTables, readList, verifyListsAndCounts,
} from './sign.mjs';

const LIST_STATUS = { Roster: 'Signed', PracticeSquad: 'PracticeSquad' };

// The current list key of a player record (Signed -> Roster, PracticeSquad -> PracticeSquad), or null.
export function currentList(status) {
  return status === 'Signed' ? 'Roster' : status === 'PracticeSquad' ? 'PracticeSquad' : null;
}

// A team's depth-chart position lists (Player[] of 6, packed or with holes) and the players locked in them.
export async function depthLists(tables, team) {
  const depth = [];
  const locked = [];
  const dcRef = decodeRef(team.DepthChart);
  if (!dcRef) return { depth, locked };
  const dc = (await tables.byId(dcRef.table)).records[dcRef.row];
  for (const pos of Object.keys(dc.fields)) {
    if (pos === 'LockedEntries') {
      const a = await readList(tables, dc[pos], `${team.ShortName} depth chart locked entries`);
      for (const r of a ? a.before.map(decodeRef).filter(Boolean) : []) {
        const entry = (await tables.byId(r.table)).records[r.row];
        locked.push({ team: team.ShortName, player: decodeRef(entry.Player) });
      }
      continue;
    }
    const a = await readList(tables, dc[pos], `${team.ShortName} depth chart ${pos}`);
    if (a?.tableName === 'Player[]') depth.push({ ...a, position: pos });
  }
  return { depth, locked };
}

// Plans the same-team list changes without changing anything: { plan, errors }. `contractTaken` excludes
// PlayerContract rows another move of this write already assigned; `project` maps `${table}:${row}` to the list values
// the earlier moves of the write leave, so a plan-time capacity check sees their result.
export async function planListChanges(franchise, items = [], { contractTaken = new Set(), project = new Map() } = {}) {
  const errors = [];
  const tables = moveTables(franchise);
  const ctx = await leagueContext(tables);
  const { playerTable, teamTable, PID } = ctx;
  const league = await tables.byName('League');
  const freeAgents = await readList(tables, league?.records[0]?.FreeAgents, 'League.FreeAgents');
  const ops = new ListOps();
  const teamEdits = [];
  const moves = [];
  const seen = new Set();
  let slots = null;      // the contract pool, made once and handed out in order

  for (const item of items) {
    const pgid = item?.pgid;
    const where = `list pgid ${pgid}`;
    if (seen.has(pgid)) { errors.push(`${where}: listed twice`); continue; }
    seen.add(pgid);
    const hit = ctx.playerRow(pgid);
    if (hit.error) { errors.push(`list ${hit.error}`); continue; }
    const { row, record: p } = hit;
    const fromKey = currentList(p.ContractStatus);
    if (!fromKey) { errors.push(`${where}: ${p.ContractStatus} on team ${p.TeamIndex}, not on a roster`); continue; }
    const team = ctx.teamByIndex.get(item?.team_index);
    if (!team) { errors.push(`${where}: no team ${item?.team_index}`); continue; }
    if (p.TeamIndex !== team.TeamIndex) {
      errors.push(`${where}: on team ${p.TeamIndex}, not ${team.ShortName}`);
      continue;
    }
    const toKey = item?.list;
    if (!LIST_STATUS[toKey]) { errors.push(`${where}: list must be Roster or PracticeSquad, not ${JSON.stringify(toKey)}`); continue; }
    if (toKey === fromKey) { errors.push(`${where}: already on ${team.ShortName} ${fromKey}`); continue; }
    const home = await readList(tables, team[fromKey], `${team.ShortName} ${fromKey}`);
    const dest = await readList(tables, team[toKey], `${team.ShortName} ${toKey}`);
    if (!home) { errors.push(`${where}: ${team.ShortName} has no ${fromKey} list`); continue; }
    if (!dest) { errors.push(`${where}: ${team.ShortName} has no ${toKey} list`); continue; }
    const hits = home.before.map(decodeRef).filter(r => r && r.table === PID && r.row === row).length;
    if (hits !== 1) { errors.push(`${where}: in ${team.ShortName} ${fromKey} ${hits} times`); continue; }
    if (decodeRef(team.FranchiseTaggedPlayer)?.row === row) { errors.push(`${where}: franchise-tagged by ${team.ShortName}`); continue; }

    const ref = { table: PID, row };
    let contract = null;
    let nextYear = 0;
    const set = { ContractStatus: LIST_STATUS[toKey], Mindset: 'Content', WeeksWithTeam: 0 };
    if (toKey === 'Roster') {
      if (!slots) {
        slots = await contractSlots(franchise, tables, playerTable, { exclude: contractTaken });
      }
      const alloc = allocateContract(slots, item?.contract);
      if (alloc.errors.length) { errors.push(...alloc.errors.map(e => `${where}: ${e}`)); continue; }
      contract = alloc.contract;
      nextYear = contract.set.Length - contract.set.CurrentYear > 1 ? 1 : 0;
    } else {
      if (item?.contract && Object.keys(item.contract).length) {
        errors.push(`${where}: practice-squad players have no contract record; drop the contract block`);
        continue;
      }
      const { depth, locked } = await depthLists(tables, team);
      if (locked.some(l => l.player?.table === PID && l.player?.row === row)) {
        errors.push(`${where}: locked in a depth chart`); continue;
      }
      for (const l of depth) {
        if (l.before.some(v => { const d = decodeRef(v); return d && d.table === PID && d.row === row; })) {
          ops.remove(l, ref, { packed: false });
        }
      }
      const c = decodeRef(p.Contract);
      if (c) {
        const cr = (await tables.byId(c.table)).records[c.row];
        nextYear = cr.Length - cr.CurrentYear > 1 ? 1 : 0;
      }
      set.PLYR_ISCAPTAIN = false;
    }
    // Contract is a reference field, written after the plain-field check (sign.mjs does the same).
    const errs = fieldErrors(p, set, 'Player');
    if (errs.length) { errors.push(...errs.map(e => `${where}: ${e}`)); continue; }
    set.Contract = contract ? encodeRef(contract.table, contract.row) : NO_REF;

    ops.remove(home, ref);
    ops.append(dest, ref);
    if (freeAgents) {
      const inFA = packedRefs(freeAgents.before).refs.some(r => r.table === PID && r.row === row);
      if (toKey === 'Roster' && inFA) ops.remove(freeAgents, ref);
      if (toKey === 'PracticeSquad' && !inFA) ops.append(freeAgents, ref);
    } else if (toKey === 'PracticeSquad') {
      errors.push(`${where}: no League.FreeAgents list`);
      continue;
    }
    if (toKey === 'Roster') {
      countDelta(teamEdits, teamTable, team, { ActiveRosterSize: 1, SalCapRosterSize: 1,
        SalCapNextYearRosterSize: nextYear });
    } else {
      countDelta(teamEdits, teamTable, team, { ActiveRosterSize: -1, SalCapRosterSize: -1,
        SalCapNextYearRosterSize: -nextYear });
    }
    moves.push({ pgid, row, record: p, from: fromKey, list: toKey, teamIndex: team.TeamIndex, team: team.ShortName,
      set, contract, created: Boolean(contract?.created) });
  }

  const projectValues = l => project.get(`${l.table}:${l.row}`) ?? l.before;
  errors.push(...ops.compute(projectValues).errors.map(e => `list: ${e}`));
  errors.push(...countErrors(teamEdits).map(e => `list: ${e}`));
  const contractRecords = slots?.contracts.records ?? null;
  return {
    plan: { playerTable: PID, teamTable: teamTable.header.tableId, moves, ops, lists: ops.lists, teamEdits,
      contractRecords },
    errors,
  };
}

export function applyListChanges(plan) {
  for (const m of plan.moves) {
    if (m.contract) {
      const c = plan.contractRecords[m.contract.row];
      for (const [k, v] of Object.entries(m.contract.set)) if (c[k] !== v) c[k] = v;
    }
    for (const [k, v] of Object.entries(m.set)) m.record[k] = v;
  }
  plan.ops.apply();
  applyCounts(plan.teamEdits);
}

// Re-reads every changed player, his contract row, the lists and roster counts from the written file.
export async function verifyListChanges(franchise, plan) {
  const tables = moveTables(franchise);
  const bad = [];
  const players = await tables.byId(plan.playerTable);
  const holders = new Map();
  for (const r of players.records) if (!r.isEmpty && decodeRef(r.Contract)) holders.set(r.Contract, (holders.get(r.Contract) ?? 0) + 1);
  for (const m of plan.moves) {
    const p = players.records[m.row];
    if (p.PresentationId !== m.pgid) { bad.push(`list pgid ${m.pgid}: row ${m.row} moved`); continue; }
    bad.push(...fieldMismatches(p, m.set, `list pgid ${m.pgid}`));
    if (m.contract) {
      const c = (await tables.byId(m.contract.table)).records[m.contract.row];
      bad.push(...fieldMismatches(c, m.contract.set, `list pgid ${m.pgid} contract`));
      if (holders.get(p.Contract) !== 1) bad.push(`list pgid ${m.pgid}: contract row held by ${holders.get(p.Contract)} players`);
    }
  }
  bad.push(...await verifyListsAndCounts(tables, plan));
  return bad;
}

export function listChangeSummary(plan) {
  return {
    changed: plan.moves.length,
    promoted: plan.moves.filter(m => m.list === 'Roster').length,
    demoted: plan.moves.filter(m => m.list === 'PracticeSquad').length,
    contracts_reused: plan.moves.filter(m => m.contract && !m.contract.created).map(m => m.contract.row),
    contracts_created: plan.moves.filter(m => m.contract && m.contract.created).map(m => m.contract.row),
    by_team: plan.moves.reduce((acc, m) => ({ ...acc, [m.team]: (acc[m.team] ?? 0) + 1 }), {}),
  };
}
