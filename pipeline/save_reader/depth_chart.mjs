// Replaces depth-chart positions inside an open franchise, for save_writer.mjs's "depthCharts" list
// (docs/in_progress/V2_CORE_BUILD_PLAN.md §2.3):
//
//   {"depthCharts": [{"team_index": 3, "slots": {"QB": [22686, 9123], "KR": [...], ...}}]}
//
// Team.DepthChart points at one DepthChart row per team whose 35 position fields (QB, HB, WR, LT, ..., KR, PR, 3DRB,
// SLWR, ...: Madden's field names) each point at a Player[] row of 6 slots, best first (release.mjs). A listed
// position gets exactly the given players, packed from slot 0, the rest of its 6 slots empty (an empty list empties
// the position); positions not listed keep what they have. Every pgid must be on that team's Roster once the write's
// other moves are counted (a player signed onto or traded to the team in the same write may be listed; one released or
// traded away may not); practice-squad players are refused. A team whose chart has locked entries
// (LockedEntries → ForcedDepthChartEntry[], empty in the sample save) is refused. Depth charts are applied after
// release, sign and trade, so a listed position ends exactly as given.
import { decodeRef, packRefs } from './lib.mjs';
import { leagueContext, ListOps, moveTables, readList, verifyListsAndCounts } from './sign.mjs';
import { teamLists } from './trade.mjs';

export const DEPTH_SLOTS = 6;

// Plans the depth charts without changing anything: { plan, errors }. `roster`: the write's other moves, as
// { removed: Set(player row), added: Map(team_index -> Set(player row)) }; a team's roster is the save's, less the
// removed rows, plus its added ones (a traded player is removed and added to his new team).
export async function planDepthCharts(franchise, items = [], { roster = { added: new Map(), removed: new Set() } } = {}) {
  const errors = [];
  const tables = moveTables(franchise);
  const ctx = await leagueContext(tables);
  const { PID, teamTable } = ctx;
  const ops = new ListOps();
  const charts = [];
  const seenTeam = new Set();

  for (const item of items) {
    const where = `depthCharts team ${item?.team_index}`;
    const team = ctx.teamByIndex.get(item?.team_index);
    if (!team) { errors.push(`${where}: no such team`); continue; }
    if (seenTeam.has(team.TeamIndex)) { errors.push(`${where}: listed twice`); continue; }
    seenTeam.add(team.TeamIndex);
    if (!item.slots || typeof item.slots !== 'object' || Array.isArray(item.slots) || !Object.keys(item.slots).length) {
      errors.push(`${where}: slots must be {"QB": [pgid, ...], ...}`);
      continue;
    }
    const { depth, locked, depthChart } = await teamLists(tables, team);
    if (!depthChart) { errors.push(`${where}: ${team.ShortName} has no depth chart`); continue; }
    if (locked.length) { errors.push(`${where}: ${team.ShortName}'s depth chart has locked entries`); continue; }
    const rosterList = await readList(tables, team.Roster, `${team.ShortName} Roster`);
    const onRoster = new Set((rosterList?.before ?? []).map(decodeRef).filter(r => r && r.table === PID).map(r => r.row));
    for (const row of roster.removed) onRoster.delete(row);
    for (const row of roster.added.get(team.TeamIndex) ?? []) onRoster.add(row);

    const positions = [];
    for (const [pos, pgids] of Object.entries(item.slots)) {
      const w = `${where} ${pos}`;
      const list = depth.find(l => l.position === pos);
      if (!list) {
        errors.push(`${w}: not a depth-chart position (${depth.map(l => l.position).join(', ')})`);
        continue;
      }
      if (!Array.isArray(pgids)) { errors.push(`${w}: must be a list of pgids`); continue; }
      if (pgids.length > Math.min(DEPTH_SLOTS, list.slots.length)) { errors.push(`${w}: ${pgids.length} players, ${list.slots.length} slots`); continue; }
      if (new Set(pgids).size !== pgids.length) { errors.push(`${w}: a player listed twice`); continue; }
      const refs = [];
      for (const pgid of pgids) {
        const hit = ctx.playerRow(pgid);
        if (hit.error) { errors.push(`${w}: ${hit.error}`); continue; }
        if (!onRoster.has(hit.row)) { errors.push(`${w}: pgid ${pgid} is not on ${team.ShortName}'s Roster`); continue; }
        refs.push({ table: PID, row: hit.row });
      }
      if (refs.length !== pgids.length) continue;
      ops.replace(list, packRefs(refs, list.slots.length), { packed: false });
      positions.push({ position: pos, pgids: [...pgids] });
    }
    charts.push({ teamIndex: team.TeamIndex, team: team.ShortName, positions });
  }
  return { plan: { playerTable: PID, teamTable: teamTable.header.tableId, charts, ops, lists: ops.lists, teamEdits: [] }, errors };
}

export function applyDepthCharts(plan) {
  plan.ops.apply();
}

// Re-reads every replaced position from the written file.
export async function verifyDepthCharts(franchise, plan) {
  return verifyListsAndCounts(moveTables(franchise), plan);
}

export function depthChartSummary(plan) {
  return {
    teams: plan.charts.length,
    positions: plan.charts.reduce((n, c) => n + c.positions.length, 0),
    entries: plan.charts.reduce((n, c) => n + c.positions.reduce((m, p) => m + p.pgids.length, 0), 0),
  };
}
