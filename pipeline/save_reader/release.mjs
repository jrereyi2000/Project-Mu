// Releases rostered players to free agency inside an open franchise (docs/PC_MIGRATION_PLAN.md §4.3: donors the
// teleporter leaves over). save_writer.mjs drives it from the edits file's "release": [pgid, ...]; it plans against
// the save before anything changes, applies, and verifies the written file.
//
// How the sample save (CAREER-GIANTS-AUTOSAVE) ties a player to a team:
//   * Player.TeamIndex (32 = no team) and Player.ContractStatus: Signed players are listed in their team's Roster
//     (Team.Roster → a Player[] row of 100 slots), practice-squad players in Team.PracticeSquad (12 slots). Both are
//     packed from slot 0, and membership matches status and TeamIndex for all 2,033 players.
//   * Team.DepthChart → one DepthChart row per team whose 35 position fields (QB, KR, 3DRB, ...) each point at a
//     Player[] row of 6 slots, best first (5 of 1,120 have a hole: starter, backup, empty, third);
//     LockedEntries → ForcedDepthChartEntry[] (empty in the sample).
//   * Smaller team lists: DraftedPlayers, MarketedPlayers, Offense/DefenseActiveAbilitiesPlayers, FocusTrainingList,
//     DrillCompletedList, MiniGameCompletedList, TradePlayerBlackList; league-wide
//     PlayerMarketingEval.TopMarketedPlayers.
//   * Team.ActiveRosterSize = SalCapRosterSize = the Roster count, SalCapNextYearRosterSize = Roster players whose
//     contract runs past this year (Length - CurrentYear > 1); all 32 teams match.
//   * Free agents: TeamIndex 32, ContractStatus FreeAgent, Contract null (their PlayerContract row is gone), listed in
//     League.FreeAgents (= Franchise.FreeAgents = the "FA" team's Roster, a Player[] row of 3,500 slots) in the
//     order they arrived. Practice-squad players are listed there too (any team may sign them).
//
// A release makes a player look like those free agents: TeamIndex 32, ContractStatus FreeAgent, Contract null,
// PrevTeamIndex the old team, Mindset FindATeam, not captain, not on IR, TradeStatus None, WeeksWithTeam 0; removes
// them from every team list and depth chart above (later entries move up, order kept); appends them to
// League.FreeAgents if not already there; and lowers the team's three roster counts. Left to Madden (PC checks): the old PlayerContract row
// stays in its table unreferenced (no dead money is charged), cap totals (TEAM_SALARY, SalCapCapRoom, ...) are not
// recomputed, and a depth-chart position can end up short or empty until the depth chart is reordered. History,
// stats, awards, draft picks and Pro Bowl rosters keep their references, as they do for real free agents.
import { arraySlots, decodeRef, NO_REF, packedRefs, packRefs, withoutRefs } from './lib.mjs';

export const FREE_AGENT_TEAM = 32;
export const TEAM_LISTS = [
  'Roster', 'PracticeSquad', 'DraftedPlayers', 'MarketedPlayers', 'OffenseActiveAbilitiesPlayers',
  'DefenseActiveAbilitiesPlayers', 'FocusTrainingList', 'DrillCompletedList', 'MiniGameCompletedList',
  'TradePlayerBlackList',
];
const RELEASABLE = { Signed: 'Roster', PracticeSquad: 'PracticeSquad' };
const FREE_AGENT_FIELDS = old => ({
  TeamIndex: FREE_AGENT_TEAM, ContractStatus: 'FreeAgent', Contract: NO_REF, PrevTeamIndex: old,
  Mindset: 'FindATeam', PLYR_ISCAPTAIN: false, IsInjuredReserve: false, TradeStatus: 'None', WeeksWithTeam: 0,
});
const ROSTER_COUNTS = ['ActiveRosterSize', 'SalCapRosterSize', 'SalCapNextYearRosterSize'];

function tableCache(franchise) {
  const byId = new Map();
  const load = async table => {
    if (!table) return null;
    if (!byId.has(table.header.tableId)) {
      await table.readRecords();
      byId.set(table.header.tableId, table);
    }
    return byId.get(table.header.tableId);
  };
  return {
    // Several tables share some names (Team: a 1-row one and the 35-row league table); take the biggest.
    byName: async name => {
      const all = await Promise.all(franchise.getAllTablesByName(name).map(load));
      return all.reduce((best, t) => (!best || t.records.length > best.records.length ? t : best), null);
    },
    byId: id => load(franchise.getTableById(id)),
  };
}

// One Player[] array record: its slot fields and current values.
async function readArray(tables, bits, label) {
  const ref = decodeRef(bits);
  if (!ref) return null;
  const table = await tables.byId(ref.table);
  if (!table?.name.endsWith('[]')) return null;
  const record = table.records[ref.row];
  const slots = arraySlots(Object.keys(record.fields));
  return { label, table: ref.table, tableName: table.name, row: ref.row, record, slots, before: slots.map(k => record[k]) };
}

// Every team-owned player list (TEAM_LISTS, depth chart positions) of the 32 teams, plus the league-wide ones.
async function playerLists(tables, teams) {
  const lists = [];
  const locked = [];
  for (const team of teams) {
    for (const key of TEAM_LISTS) {
      const a = await readArray(tables, team[key], `${team.ShortName} ${key}`);
      if (a) lists.push(a);
    }
    const dcRef = decodeRef(team.DepthChart);
    if (!dcRef) continue;
    const dc = (await tables.byId(dcRef.table)).records[dcRef.row];
    for (const pos of Object.keys(dc.fields)) {
      if (pos === 'LockedEntries') {
        const a = await readArray(tables, dc[pos], `${team.ShortName} depth chart locked entries`);
        for (const r of a ? packedRefs(a.before).refs : []) {
          const entry = (await tables.byId(r.table)).records[r.row];
          locked.push({ team: team.ShortName, player: decodeRef(entry.Player) });
        }
        continue;
      }
      const a = await readArray(tables, dc[pos], `${team.ShortName} depth chart ${pos}`);
      if (a?.tableName === 'Player[]') lists.push({ ...a, depth: pos });
    }
  }
  const marketing = await tables.byName('PlayerMarketingEval');
  const top = marketing && await readArray(tables, marketing.records[0]?.TopMarketedPlayers, 'league TopMarketedPlayers');
  if (top) lists.push(top);
  return { lists, locked };
}

// Plans releasing `pgids` without changing anything: { plan, errors }. `exclude` holds pgids that other edits in the
// same write touch (refused, so one player isn't both rewritten and released).
export async function planRelease(franchise, pgids, { exclude = new Set() } = {}) {
  const errors = [];
  const tables = tableCache(franchise);
  const playerTable = await tables.byName('Player');
  const teamTable = await tables.byName('Team');
  const league = await tables.byName('League');
  const teams = teamTable.records.filter(t => !t.isEmpty && t.TeamIndex < FREE_AGENT_TEAM);
  const teamByIndex = new Map(teams.map(t => [t.TeamIndex, t]));

  const rowsByPgid = new Map();
  playerTable.records.forEach((r, row) => {
    if (!r.isEmpty) rowsByPgid.set(r.PresentationId, [...(rowsByPgid.get(r.PresentationId) ?? []), row]);
  });
  const { lists, locked } = await playerLists(tables, teams);
  const freeAgents = await readArray(tables, league?.records[0]?.FreeAgents, 'League.FreeAgents');
  if (!freeAgents) errors.push('no League.FreeAgents list');
  else freeAgents.after = freeAgents.before;

  const releases = [];
  const seen = new Set();
  for (const pgid of pgids) {
    if (seen.has(pgid)) { errors.push(`pgid ${pgid}: listed twice`); continue; }
    seen.add(pgid);
    if (exclude.has(pgid)) { errors.push(`pgid ${pgid}: also has field edits`); continue; }
    const rows = rowsByPgid.get(pgid) ?? [];
    if (rows.length !== 1) { errors.push(`pgid ${pgid}: ${rows.length} players`); continue; }
    const row = rows[0];
    const p = playerTable.records[row];
    const listKey = RELEASABLE[p.ContractStatus];
    const team = teamByIndex.get(p.TeamIndex);
    if (!listKey || !team) { errors.push(`pgid ${pgid}: ${p.ContractStatus} on team ${p.TeamIndex}, not a rostered player`); continue; }
    const home = lists.find(l => l.label === `${team.ShortName} ${listKey}`);
    const hits = home ? packedRefs(home.before).refs.filter(r => r.table === playerTable.header.tableId && r.row === row) : [];
    if (hits.length !== 1) { errors.push(`pgid ${pgid}: in ${team.ShortName} ${listKey} ${hits.length} times`); continue; }
    if (decodeRef(team.FranchiseTaggedPlayer)?.row === row) { errors.push(`pgid ${pgid}: franchise-tagged by ${team.ShortName}`); continue; }
    if (locked.some(l => l.player?.row === row)) { errors.push(`pgid ${pgid}: locked in a depth chart`); continue; }
    let nextYear = false;
    const contractRef = decodeRef(p.Contract);
    if (contractRef) {
      const c = (await tables.byId(contractRef.table)).records[contractRef.row];
      nextYear = c.Length - c.CurrentYear > 1;
    }
    releases.push({ pgid, row, record: p, status: p.ContractStatus, teamIndex: p.TeamIndex, team: team.ShortName,
      contract: contractRef, nextYear, set: FREE_AGENT_FIELDS(p.TeamIndex) });
  }

  // Lists: take released players out and move the later entries up. Roster and PracticeSquad must be packed (their
  // count is the roster size); depth charts can hold a hole, which keeps its place among the others.
  const released = new Set(releases.map(r => r.row));
  const isReleased = ref => ref.table === playerTable.header.tableId && released.has(ref.row);
  for (const l of lists) {
    l.after = l.before;
    const removed = l.before.map(decodeRef).filter(r => r && isReleased(r)).length;
    if (!removed) continue;
    if (!l.depth && !packedRefs(l.before).packed) { errors.push(`${l.label}: not packed from slot 0`); continue; }
    l.after = withoutRefs(l.before, isReleased);
    l.removed = removed;
  }
  if (freeAgents) {
    const { refs, packed } = packedRefs(freeAgents.before);
    const add = releases.filter(r => !refs.some(x => x.table === playerTable.header.tableId && x.row === r.row))
      .map(r => ({ table: playerTable.header.tableId, row: r.row }));
    if (!packed) errors.push('League.FreeAgents: not packed from slot 0');
    else if (refs.length + add.length > freeAgents.slots.length) errors.push('League.FreeAgents: full');
    else freeAgents.after = packRefs([...refs, ...add], freeAgents.slots.length);
    freeAgents.added = add.length;
  }

  // Team roster counts.
  const teamEdits = [];
  for (const team of teams) {
    const out = releases.filter(r => r.teamIndex === team.TeamIndex && r.status === 'Signed');
    if (!out.length) continue;
    const drop = { ActiveRosterSize: out.length, SalCapRosterSize: out.length,
      SalCapNextYearRosterSize: out.filter(r => r.nextYear).length };
    const set = {};
    for (const k of ROSTER_COUNTS) {
      const v = team[k] - drop[k];
      if (!Number.isInteger(team[k]) || v < 0) errors.push(`${team.ShortName} ${k}: ${team[k]} - ${drop[k]}`);
      else set[k] = v;
    }
    teamEdits.push({ team: team.ShortName, row: teamTable.records.indexOf(team), record: team, before: Object.fromEntries(ROSTER_COUNTS.map(k => [k, team[k]])), set });
  }

  return {
    plan: { playerTable: playerTable.header.tableId, teamTable: teamTable.header.tableId, releases, lists, freeAgents, teamEdits },
    errors,
  };
}

export function applyRelease(plan) {
  for (const r of plan.releases) for (const [k, v] of Object.entries(r.set)) r.record[k] = v;
  for (const l of [...plan.lists, plan.freeAgents]) {
    if (l.after === l.before) continue;
    l.slots.forEach((k, i) => { if (l.record[k] !== l.after[i]) l.record[k] = l.after[i]; });
  }
  for (const t of plan.teamEdits) for (const [k, v] of Object.entries(t.set)) t.record[k] = v;
}

// Re-reads every planned list, released player and roster count from `franchise` (the written file); returns the
// mismatches. Lists without a released player must read back exactly as before.
export async function verifyRelease(franchise, plan) {
  const tables = tableCache(franchise);
  const bad = [];
  const players = await tables.byId(plan.playerTable);
  for (const r of plan.releases) {
    const p = players.records[r.row];
    if (p.PresentationId !== r.pgid) { bad.push(`pgid ${r.pgid}: row ${r.row} moved`); continue; }
    for (const [k, v] of Object.entries(r.set)) if (p[k] !== v) bad.push(`pgid ${r.pgid} ${k}: wrote ${v}, read ${p[k]}`);
  }
  for (const l of [...plan.lists, plan.freeAgents]) {
    const record = (await tables.byId(l.table)).records[l.row];
    const now = l.slots.map(k => record[k]);
    if (now.some((v, i) => v !== l.after[i])) bad.push(`${l.label}: does not read back as planned`);
  }
  const teams = await tables.byId(plan.teamTable);
  for (const t of plan.teamEdits) {
    for (const [k, v] of Object.entries(t.set)) {
      if (teams.records[t.row][k] !== v) bad.push(`${t.team} ${k}: wrote ${v}, read ${teams.records[t.row][k]}`);
    }
  }
  return bad;
}

// What the release did, for the writer's report.
export function releaseSummary(plan) {
  const emptied = plan.lists.filter(l => l.depth && l.removed && l.after.every(v => !decodeRef(v))).map(l => l.label);
  return {
    released: plan.releases.length,
    signed: plan.releases.filter(r => r.status === 'Signed').length,
    practice_squad: plan.releases.filter(r => r.status === 'PracticeSquad').length,
    by_team: plan.releases.reduce((acc, r) => ({ ...acc, [r.team]: (acc[r.team] ?? 0) + 1 }), {}),
    lists_changed: plan.lists.filter(l => l.removed).length,
    depth_entries_removed: plan.lists.filter(l => l.depth).reduce((n, l) => n + (l.removed ?? 0), 0),
    depth_positions_emptied: emptied,
    free_agents_added: plan.freeAgents?.added ?? 0,
    contracts_left_unreferenced: plan.releases.filter(r => r.contract).length,
  };
}
