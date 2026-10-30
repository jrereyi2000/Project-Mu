// Trades players and draft picks between two teams inside an open franchise, for save_writer.mjs's "trade" list
// (docs/in_progress/V2_CORE_BUILD_PLAN.md §2.3):
//
//   {"trade": [{"from_team_index": 3, "to_team_index": 12, "pgids": [123], "picks": [{"tableId": 4540, "row": 17}]}]}
//
// One entry moves one direction; a swap is two entries. Each player keeps his status and his PlayerContract record
// (the reference doesn't change): a Signed player moves from the old team's Roster to the end of the new team's, a
// practice-squad player from PracticeSquad to PracticeSquad (and stays in League.FreeAgents, where practice-squad
// players are listed). He gets TeamIndex the new team, PrevTeamIndex the old one, WeeksWithTeam 0, not captain,
// TradeStatus None; leaves the old team's depth chart and its other player lists (release.mjs TEAM_LISTS: drafted,
// marketed, abilities, training and trade-blacklist lists; later entries move up); and moves the roster counts
// (ActiveRosterSize, SalCapRosterSize, and SalCapNextYearRosterSize when his deal runs past this year) from the old
// team to the new one. A franchise-tagged player or one locked in a depth chart is refused. The new team's depth chart
// is not touched (send "depthCharts" in the same write).
//
// A pick is a DraftPick record by table id and row (draft_tables.mjs prints them); its CurrentTeam is re-pointed at the
// new team's Team row under record_edits.mjs's reference rule (a Team table, a real row, the field already pointing
// into that table). The pick must belong to the old team and not have been used (SelectedPlayer empty).
// Left to Madden (PC checks): cap totals, trade history and news.
import { decodeRef, encodeRef } from './lib.mjs';
import { referenceError } from './record_edits.mjs';
import { TEAM_LISTS } from './release.mjs';
import {
  applyCounts, countDelta, countErrors, fieldErrors, fieldMismatches, leagueContext, ListOps, moveTables, readList,
  verifyListsAndCounts,
} from './sign.mjs';

const TRADABLE = { Signed: 'Roster', PracticeSquad: 'PracticeSquad' };

// A team's player lists other than Roster and PracticeSquad, its depth-chart positions, and its locked entries.
export async function teamLists(tables, team) {
  const lists = [];
  for (const key of TEAM_LISTS) {
    const a = await readList(tables, team[key], `${team.ShortName} ${key}`);
    if (a) lists.push({ ...a, key });
  }
  const depth = [];
  const locked = [];
  const dcRef = decodeRef(team.DepthChart);
  const dc = dcRef ? (await tables.byId(dcRef.table)).records[dcRef.row] : null;
  if (dc) {
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
  }
  return { lists, depth, locked, depthChart: dc };
}

// Plans the trades without changing anything: { plan, errors }.
export async function planTrade(franchise, items = []) {
  const errors = [];
  const tables = moveTables(franchise);
  const ctx = await leagueContext(tables);
  const { playerTable, teamTable, PID } = ctx;
  const ops = new ListOps();
  const teamEdits = [];
  const moves = [];
  const picks = [];
  const listCache = new Map();
  const listsOf = async team => {
    if (!listCache.has(team.TeamIndex)) listCache.set(team.TeamIndex, await teamLists(tables, team));
    return listCache.get(team.TeamIndex);
  };
  const tableMap = new Map([[teamTable.header.tableId, teamTable]]);
  const seenPgid = new Set();
  const seenPick = new Set();

  for (const [n, item] of items.entries()) {
    const where = `trade ${n}`;
    const from = ctx.teamByIndex.get(item?.from_team_index);
    const to = ctx.teamByIndex.get(item?.to_team_index);
    if (!from || !to) { errors.push(`${where}: no team ${!from ? item?.from_team_index : item?.to_team_index}`); continue; }
    if (from === to) { errors.push(`${where}: from and to are the same team`); continue; }
    if (!(item.pgids ?? []).length && !(item.picks ?? []).length) { errors.push(`${where}: no players or picks`); continue; }
    const fromLists = await listsOf(from);
    const fromRow = teamTable.records.indexOf(from);
    const toRow = teamTable.records.indexOf(to);

    for (const pgid of item.pgids ?? []) {
      const w = `${where} pgid ${pgid}`;
      if (seenPgid.has(pgid)) { errors.push(`${w}: listed twice`); continue; }
      seenPgid.add(pgid);
      const hit = ctx.playerRow(pgid);
      if (hit.error) { errors.push(`trade ${hit.error}`); continue; }
      const { row, record: p } = hit;
      const key = TRADABLE[p.ContractStatus];
      if (!key || p.TeamIndex !== from.TeamIndex) {
        errors.push(`${w}: ${p.ContractStatus} on team ${p.TeamIndex}, not on ${from.ShortName}'s roster or practice squad`);
        continue;
      }
      const home = await readList(tables, from[key], `${from.ShortName} ${key}`);
      const dest = await readList(tables, to[key], `${to.ShortName} ${key}`);
      const hits = home ? home.before.map(decodeRef).filter(r => r && r.table === PID && r.row === row).length : 0;
      if (hits !== 1) { errors.push(`${w}: in ${from.ShortName} ${key} ${hits} times`); continue; }
      if (!dest) { errors.push(`${w}: ${to.ShortName} has no ${key} list`); continue; }
      if (decodeRef(from.FranchiseTaggedPlayer)?.row === row) { errors.push(`${w}: franchise-tagged by ${from.ShortName}`); continue; }
      if (fromLists.locked.some(l => l.player?.table === PID && l.player?.row === row)) { errors.push(`${w}: locked in a depth chart`); continue; }
      const set = { TeamIndex: to.TeamIndex, PrevTeamIndex: from.TeamIndex, WeeksWithTeam: 0, PLYR_ISCAPTAIN: false, TradeStatus: 'None' };
      const errs = fieldErrors(p, set, 'Player');
      if (errs.length) { errors.push(...errs.map(e => `${w}: ${e}`)); continue; }

      const ref = { table: PID, row };
      ops.remove(home, ref);
      ops.append(dest, ref);
      for (const l of fromLists.lists) {
        if (l.key === 'Roster' || l.key === 'PracticeSquad') continue;
        if (l.before.some(v => { const d = decodeRef(v); return d && d.table === PID && d.row === row; })) ops.remove(l, ref);
      }
      for (const l of fromLists.depth) {
        if (l.before.some(v => { const d = decodeRef(v); return d && d.table === PID && d.row === row; })) ops.remove(l, ref, { packed: false });
      }
      if (key === 'Roster') {
        const c = decodeRef(p.Contract);
        const cr = c ? (await tables.byId(c.table)).records[c.row] : null;
        const nextYear = cr ? (cr.Length - cr.CurrentYear > 1 ? 1 : 0) : 0;
        countDelta(teamEdits, teamTable, from, { ActiveRosterSize: -1, SalCapRosterSize: -1, SalCapNextYearRosterSize: -nextYear });
        countDelta(teamEdits, teamTable, to, { ActiveRosterSize: 1, SalCapRosterSize: 1, SalCapNextYearRosterSize: nextYear });
      }
      moves.push({ pgid, row, record: p, status: p.ContractStatus, list: key, from: from.ShortName, to: to.ShortName,
        fromIndex: from.TeamIndex, toIndex: to.TeamIndex, contract: p.Contract, set });
    }

    for (const pk of item.picks ?? []) {
      const w = `${where} pick ${JSON.stringify(pk)}`;
      const id = `${pk?.tableId}:${pk?.row}`;
      if (seenPick.has(id)) { errors.push(`${w}: listed twice`); continue; }
      seenPick.add(id);
      const t = Number.isInteger(pk?.tableId) ? await tables.byId(pk.tableId) : null;
      if (!t || t.name !== 'DraftPick') { errors.push(`${w}: not a DraftPick table`); continue; }
      const r = Number.isInteger(pk.row) ? t.records[pk.row] : null;
      if (!r || r.isEmpty) { errors.push(`${w}: not a record`); continue; }
      if (decodeRef(r.SelectedPlayer)) { errors.push(`${w}: already used`); continue; }
      const owner = decodeRef(r.CurrentTeam);
      if (!owner || owner.table !== teamTable.header.tableId || owner.row !== fromRow) {
        errors.push(`${w}: owned by Team row ${owner?.row ?? 'none'}, not ${from.ShortName}`);
        continue;
      }
      const field = r.getFieldByKey('CurrentTeam');
      const err = referenceError({ type: field.offset.type }, r.CurrentTeam, { tableId: teamTable.header.tableId, row: toRow }, tableMap);
      if (err) { errors.push(`${w}: ${err}`); continue; }
      picks.push({ tableId: pk.tableId, row: pk.row, record: r, from: from.ShortName, to: to.ShortName,
        value: encodeRef(teamTable.header.tableId, toRow) });
    }
  }

  errors.push(...ops.compute().errors.map(e => `trade: ${e}`));
  errors.push(...countErrors(teamEdits).map(e => `trade: ${e}`));
  return { plan: { playerTable: PID, teamTable: teamTable.header.tableId, moves, picks, ops, lists: ops.lists, teamEdits }, errors };
}

export function applyTrade(plan) {
  for (const m of plan.moves) for (const [k, v] of Object.entries(m.set)) m.record[k] = v;
  for (const pk of plan.picks) pk.record.CurrentTeam = pk.value;
  plan.ops.apply();
  applyCounts(plan.teamEdits);
}

// Re-reads every traded player, pick, list and roster count from the written file.
export async function verifyTrade(franchise, plan) {
  const tables = moveTables(franchise);
  const bad = [];
  const players = await tables.byId(plan.playerTable);
  for (const m of plan.moves) {
    const p = players.records[m.row];
    if (p.PresentationId !== m.pgid) { bad.push(`trade pgid ${m.pgid}: row ${m.row} moved`); continue; }
    bad.push(...fieldMismatches(p, m.set, `trade pgid ${m.pgid}`));
    if (p.Contract !== m.contract) bad.push(`trade pgid ${m.pgid}: contract reference changed`);
  }
  for (const pk of plan.picks) {
    const r = (await tables.byId(pk.tableId)).records[pk.row];
    if (r.CurrentTeam !== pk.value) bad.push(`trade pick ${pk.tableId}:${pk.row} CurrentTeam: wrote ${pk.value}, read ${r.CurrentTeam}`);
  }
  bad.push(...await verifyListsAndCounts(tables, plan));
  return bad;
}

export function tradeSummary(plan) {
  const byPair = {};
  for (const x of [...plan.moves, ...plan.picks]) byPair[`${x.from}->${x.to}`] = (byPair[`${x.from}->${x.to}`] ?? 0) + 1;
  return {
    players: plan.moves.length,
    roster: plan.moves.filter(m => m.list === 'Roster').length,
    practice_squad: plan.moves.filter(m => m.list === 'PracticeSquad').length,
    picks: plan.picks.length,
    depth_entries_removed: plan.lists.filter(l => l.label.includes(' depth chart ')).reduce((n, l) => n + l.remove.size, 0),
    by_direction: byPair,
  };
}
