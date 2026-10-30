// Signs free agents and draft prospects inside an open franchise, for save_writer.mjs's "sign" list
// (docs/in_progress/V2_CORE_BUILD_PLAN.md §2.3):
//
//   {"sign": [{"pgid": 123, "team_index": 3, "list": "Roster",
//              "contract": {"Length": 3, "CurrentYear": 0, "SalaryYear0": 105, "BonusYear0": 50, ...}}]}
//
// Like release.mjs it plans against the save before anything changes, applies, and verifies the written file. It also
// exports the helpers trade.mjs and depth_chart.mjs share (a table cache, Player[] list reads, list operations that
// compose with the other moves of the same write, read-back of lists and roster counts).
//
// How CAREER-GIANTS-AUTOSAVE (schema M27_620_0) keeps what a signing changes (see release.mjs for the team lists):
//   * Signed players (status Signed, in Team.Roster) each point at their own PlayerContract row (table 4302); free
//     agents, practice-squad players and draft prospects point at none. The table has no spare row: its only non-empty
//     row no player references (27) is held by a PersonaNegotiationPackage. Every field typed PlayerContract in the
//     schema (Player.Contract, ContractOffer.Contract, InSeasonPlayerSigningTransaction.PlayerContract,
//     PersonaNegotiationPackage.Contract) is scanned, and a row none of them references is free. release.mjs leaves
//     its player's row unreferenced, so a release and a signing in one write hand the row on. With no free row a Roster
//     signing is refused (filling one of the table's empty rows would be a new record, out of scope).
//   * Practice-squad players (339) are listed in League.FreeAgents as well as their team's PracticeSquad, and have no
//     contract row; all have Mindset Content. Free agents (547) are in League.FreeAgents with Mindset FindATeam.
//   * Draft prospects (status Draft, TeamIndex 32) are not in League.FreeAgents. Each is linked from a DraftPlayer
//     row, which Franchise.DraftClassPlayers (a DraftPlayer[] of 455 slots, packed) and the teams' ScoutingFocus
//     FocusPlayers / PrivateWorkoutPlayers lists (DraftPlayer[] of 3) point at. The one prospect Madden already signed
//     (DraftPlayer row 734, now on a practice squad) kept his DraftPlayer row but left every DraftPlayer[] list.
//
// A signing sets TeamIndex, ContractStatus (Signed for "Roster", PracticeSquad for "PracticeSquad"), Mindset Content
// and WeeksWithTeam 0. A Roster signing takes a free PlayerContract row, resets its terms (salaries, bonuses, years,
// restructures, void years, no-trade clause, extra-year option, guaranteed years) and sets the contract block over
// them (money in Madden's $10,000 units, every value checked against the field's schema like contract_edits.mjs; the
// row's Incentives link is kept), points Player.Contract at it, appends the player to Team.Roster, takes him out of
// League.FreeAgents and raises ActiveRosterSize, SalCapRosterSize and SalCapNextYearRosterSize (when the deal runs past
// this year) the way release.mjs lowers them. A PracticeSquad signing takes no contract (a contract block is refused),
// appends to Team.PracticeSquad, keeps a free agent in League.FreeAgents (a prospect is added to it) and leaves the
// roster counts alone. A prospect leaves every DraftPlayer[] list; his DraftPlayer row stays. The depth chart is not
// touched (send "depthCharts" in the same write). Left to Madden (PC checks): cap totals (TEAM_SALARY, SalCapCapRoom)
// and draft-board evaluations (DraftBoardPlayerEvaluation) of a signed prospect.
import { arraySlots, decodeRef, encodeRef, fieldMeta, fieldValueError, packedRefs, packRefs, withoutRefs } from './lib.mjs';

export const FREE_AGENT_TEAM = 32;
export const ROSTER_COUNTS = ['ActiveRosterSize', 'SalCapRosterSize', 'SalCapNextYearRosterSize'];
const SIGNABLE = new Set(['FreeAgent', 'Draft']);
const STATUS_FOR_LIST = { Roster: 'Signed', PracticeSquad: 'PracticeSquad' };
const CONTRACT_TABLE = 'PlayerContract';
// Terms a reused PlayerContract row starts from before the contract block is set over them.
const CONTRACT_BASELINE = { CurrentYear: 0, TimesRestructured: 0, VoidYears: 0, NoTradeClause: false,
  ExtraYearOption: false, IsSalaryGuaranteedYears: 0 };

// ---- helpers shared with trade.mjs and depth_chart.mjs ----

// Tables by id or name, records read once. Several tables share some names (Team: a 1-row one and the 35-row league
// table); byName takes the biggest.
export function moveTables(franchise) {
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
    byName: async name => {
      const all = await Promise.all(franchise.getAllTablesByName(name).map(load));
      return all.reduce((best, t) => (!best || t.records.length > best.records.length ? t : best), null);
    },
    allByName: name => Promise.all(franchise.getAllTablesByName(name).map(load)),
    byId: id => load(franchise.getTableById(id)),
  };
}

// The player and team tables, the 32 teams, and each pgid's rows.
export async function leagueContext(tables) {
  const playerTable = await tables.byName('Player');
  const teamTable = await tables.byName('Team');
  const teams = teamTable.records.filter(t => !t.isEmpty && t.TeamIndex < FREE_AGENT_TEAM);
  const rowsByPgid = new Map();
  playerTable.records.forEach((r, row) => {
    if (!r.isEmpty) rowsByPgid.set(r.PresentationId, [...(rowsByPgid.get(r.PresentationId) ?? []), row]);
  });
  return {
    playerTable, teamTable, teams, rowsByPgid,
    PID: playerTable.header.tableId,
    teamByIndex: new Map(teams.map(t => [t.TeamIndex, t])),
    // The single player row for a pgid, or an error message.
    playerRow(pgid) {
      const rows = rowsByPgid.get(pgid) ?? [];
      return rows.length === 1 ? { row: rows[0], record: playerTable.records[rows[0]] } : { error: `pgid ${pgid}: ${rows.length} players` };
    },
  };
}

// One array record (Player[], DraftPlayer[], ...) as a list the moves can edit: slot fields and current values.
export async function readList(tables, bits, label) {
  const ref = decodeRef(bits);
  if (!ref) return null;
  const table = await tables.byId(ref.table);
  if (!table?.name.endsWith('[]')) return null;
  const record = table.records[ref.row];
  if (!record || record.isEmpty) return null;
  const slots = arraySlots(Object.keys(record.fields));
  return { label, table: ref.table, tableName: table.name, row: ref.row, record, slots, before: slots.map(k => record[k]) };
}

const refKey = r => `${r.table}:${r.row}`;

// A plan's list edits, merged per array record. An op removes references (later entries move up, holes in a depth
// chart keep their place), appends references (the list must be packed from slot 0), or replaces every slot. Ops
// run against the list's values when applied, so moves of one write that touch the same list compose.
export class ListOps {
  constructor() { this.byKey = new Map(); }
  get lists() { return [...this.byKey.values()]; }
  op(list, { packed = true } = {}) {
    const key = `${list.table}:${list.row}`;
    if (!this.byKey.has(key)) this.byKey.set(key, { ...list, packed, remove: new Set(), append: [], replace: null, after: list.before });
    return this.byKey.get(key);
  }
  remove(list, ref, opts) { this.op(list, opts).remove.add(refKey(ref)); }
  append(list, ref, opts) { this.op(list, opts).append.push(ref); }
  replace(list, values, opts) { this.op(list, opts).replace = values; }
  // Each list's values after the ops, from `values(list)` (its current slots); returns { results, errors }.
  compute(values = l => l.before) {
    const errors = [];
    const results = new Map();
    for (const l of this.lists) {
      const now = values(l);
      let after = now;
      if (l.replace) after = l.replace;
      else {
        if (l.remove.size) {
          if (l.packed && !packedRefs(now).packed) { errors.push(`${l.label}: not packed from slot 0`); continue; }
          after = withoutRefs(after, r => l.remove.has(refKey(r)));
        }
        if (l.append.length) {
          const { refs, packed } = packedRefs(after);
          if (!packed) { errors.push(`${l.label}: not packed from slot 0`); continue; }
          const seen = new Set(refs.map(refKey));
          if (l.append.some(r => seen.has(refKey(r)))) { errors.push(`${l.label}: already lists an added player`); continue; }
          if (refs.length + l.append.length > l.slots.length) {
            errors.push(`${l.label}: full (${refs.length} + ${l.append.length} > ${l.slots.length} slots)`);
            continue;
          }
          after = packRefs([...refs, ...l.append], l.slots.length);
        }
      }
      results.set(l, after);
    }
    return { results, errors };
  }
  // Writes the ops into the records, from their current values; throws (before anything is saved) if one doesn't fit.
  apply() {
    const { results, errors } = this.compute(l => l.slots.map(k => l.record[k]));
    if (errors.length) throw new Error(`edits refused, nothing written:\n  ${errors.join('\n  ')}`);
    for (const [l, after] of results) {
      l.slots.forEach((k, i) => { if (l.record[k] !== after[i]) l.record[k] = after[i]; });
      l.after = after;
    }
  }
}

// Roster-count changes per team, added to the team's values when applied.
export function countDelta(teamEdits, teamTable, team, delta) {
  let t = teamEdits.find(x => x.record === team);
  if (!t) {
    t = { team: team.ShortName, row: teamTable.records.indexOf(team), record: team, delta: {}, set: {} };
    teamEdits.push(t);
  }
  for (const [k, v] of Object.entries(delta)) t.delta[k] = (t.delta[k] ?? 0) + v;
}

// Checks the counts each team ends with (from `value(team, key)`); returns error messages.
export function countErrors(teamEdits, value = (t, k) => t.record[k]) {
  const errors = [];
  for (const t of teamEdits) {
    for (const [k, d] of Object.entries(t.delta)) {
      const now = value(t, k);
      const meta = fieldMeta(t.record, k);
      const err = !Number.isInteger(now) ? 'not a number' : meta && fieldValueError(meta, now + d);
      if (err) errors.push(`${t.team} ${k}: ${now} ${d < 0 ? '-' : '+'} ${Math.abs(d)}: ${err}`);
    }
  }
  return errors;
}

export function applyCounts(teamEdits) {
  const errors = countErrors(teamEdits);
  if (errors.length) throw new Error(`edits refused, nothing written:\n  ${errors.join('\n  ')}`);
  for (const t of teamEdits) {
    for (const [k, d] of Object.entries(t.delta)) {
      if (d) t.record[k] += d;
      t.set[k] = t.record[k];
    }
  }
}

// After every move of a write is applied, a plan's expected lists and counts are what the records now hold (a later
// move may have changed a list this one changed too). Works on release.mjs plans as well (lists, freeAgents,
// teamEdits[].set).
export function refreshPlan(plan) {
  for (const l of [...(plan.lists ?? []), plan.freeAgents].filter(Boolean)) l.after = l.slots.map(k => l.record[k]);
  for (const t of plan.teamEdits ?? []) {
    for (const k of Object.keys(t.delta ?? t.set)) t.set[k] = t.record[k];
  }
}

// Re-reads a plan's lists and counts from the written file; returns the mismatches.
export async function verifyListsAndCounts(tables, plan) {
  const bad = [];
  for (const l of plan.lists) {
    const record = (await tables.byId(l.table)).records[l.row];
    if (l.slots.some((k, i) => record[k] !== l.after[i])) bad.push(`${l.label}: does not read back as planned`);
  }
  const teams = await tables.byId(plan.teamTable);
  for (const t of plan.teamEdits) {
    for (const [k, v] of Object.entries(t.set)) {
      if (teams.records[t.row][k] !== v) bad.push(`${t.team} ${k}: wrote ${v}, read ${teams.records[t.row][k]}`);
    }
  }
  return bad;
}

// Checks plain field values against the record's schema; returns error messages.
export function fieldErrors(record, set, label, skip = []) {
  const errors = [];
  for (const [k, v] of Object.entries(set)) {
    if (skip.includes(k)) continue;
    const meta = fieldMeta(record, k);
    const err = meta ? fieldValueError(meta, v) : `no such ${label} field`;
    if (err) errors.push(`${k} = ${JSON.stringify(v)}: ${err}`);
  }
  return errors;
}

// Compares fields read back (enums by stored value: some enums give two names one value).
export function fieldMismatches(record, set, label) {
  const bad = [];
  for (const [k, v] of Object.entries(set)) {
    const meta = fieldMeta(record, k);
    const stored = x => (meta?.enumValues ? meta.enumValues[x] : x);
    if (stored(record[k]) !== stored(v)) bad.push(`${label} ${k}: wrote ${v}, read ${record[k]}`);
  }
  return bad;
}

// ---- signing ----

// Every PlayerContract row some record points at (all schema fields typed PlayerContract, in every table of that name).
async function referencedContracts(franchise, tables) {
  const fields = new Map();
  for (const s of franchise.schemaList?.schemas ?? []) {
    for (const a of s.attributes ?? []) if (a.type === CONTRACT_TABLE) fields.set(s.name, [...(fields.get(s.name) ?? []), a.name]);
  }
  fields.set('Player', [...new Set([...(fields.get('Player') ?? []), 'Contract'])]);
  const used = new Set();
  for (const [name, keys] of fields) {
    for (const t of await tables.allByName(name)) {
      for (const r of t.records) {
        if (r.isEmpty) continue;
        for (const k of keys) { const d = decodeRef(r[k]); if (d) used.add(refKey(d)); }
      }
    }
  }
  return used;
}

// The PlayerContract table players point into (the biggest one if none does yet).
async function contractTable(tables, players) {
  const counts = new Map();
  for (const r of players.records) {
    const d = r.isEmpty ? null : decodeRef(r.Contract);
    if (d) counts.set(d.table, (counts.get(d.table) ?? 0) + 1);
  }
  const best = [...counts].sort((a, b) => b[1] - a[1])[0];
  const t = best ? await tables.byId(best[0]) : await tables.byName(CONTRACT_TABLE);
  return t?.name === CONTRACT_TABLE ? t : null;
}

// Plans the signings without changing anything: { plan, errors }. `freedContracts`: PlayerContract references a
// release in the same write leaves unreferenced (release plan's releases[].contract), usable as free rows.
export async function planSign(franchise, items = [], { freedContracts = [] } = {}) {
  const errors = [];
  const tables = moveTables(franchise);
  const ctx = await leagueContext(tables);
  const { playerTable, teamTable, PID } = ctx;
  const league = await tables.byName('League');
  const freeAgents = await readList(tables, league?.records[0]?.FreeAgents, 'League.FreeAgents');
  const ops = new ListOps();
  const teamEdits = [];
  const signs = [];

  const contracts = items.some(i => i?.list === 'Roster') ? await contractTable(tables, playerTable) : null;
  let free = [];
  if (contracts) {
    const used = await referencedContracts(franchise, tables);
    const freed = freedContracts.filter(c => c && c.table === contracts.header.tableId).map(c => c.row);
    const spare = contracts.records.map((r, row) => row).filter(row => !contracts.records[row].isEmpty
      && !used.has(`${contracts.header.tableId}:${row}`) && !freed.includes(row));
    free = [...spare, ...freed];
  }

  // Draft-class lists, read only when a prospect is signed.
  let draft = null;
  const draftLists = async () => {
    if (draft) return draft;
    const dp = await tables.allByName('DraftPlayer');
    const arrays = [];
    for (const t of await tables.allByName('DraftPlayer[]')) {
      t.records.forEach((record, row) => {
        if (record.isEmpty) return;
        const slots = arraySlots(Object.keys(record.fields));
        arrays.push({ label: `DraftPlayer[] ${t.header.tableId} row ${row}`, table: t.header.tableId, tableName: t.name, row, record, slots, before: slots.map(k => record[k]) });
      });
    }
    draft = { dp, arrays };
    return draft;
  };

  const seen = new Set();
  for (const item of items) {
    const pgid = item?.pgid;
    const where = `sign pgid ${pgid}`;
    if (seen.has(pgid)) { errors.push(`${where}: listed twice`); continue; }
    seen.add(pgid);
    const hit = ctx.playerRow(pgid);
    if (hit.error) { errors.push(`sign ${hit.error}`); continue; }
    const { row, record: p } = hit;
    if (!SIGNABLE.has(p.ContractStatus)) { errors.push(`${where}: ${p.ContractStatus} on team ${p.TeamIndex}, not a free agent or draft prospect`); continue; }
    const team = ctx.teamByIndex.get(item.team_index);
    if (!team) { errors.push(`${where}: no team ${item.team_index}`); continue; }
    const status = STATUS_FOR_LIST[item.list];
    if (!status) { errors.push(`${where}: list must be Roster or PracticeSquad, not ${JSON.stringify(item.list)}`); continue; }
    const list = await readList(tables, team[item.list], `${team.ShortName} ${item.list}`);
    if (!list) { errors.push(`${where}: ${team.ShortName} has no ${item.list} list`); continue; }
    if (decodeRef(p.Contract)) { errors.push(`${where}: already points at a contract record`); continue; }

    const set = { TeamIndex: team.TeamIndex, ContractStatus: status, Mindset: 'Content', WeeksWithTeam: 0 };
    const errs = fieldErrors(p, set, 'Player');
    let contract = null;
    if (item.list === 'Roster') {
      const block = item.contract;
      if (!block || typeof block !== 'object' || !Number.isInteger(block.Length)) {
        errs.push('a Roster signing needs a contract block with Length');
      } else if (!contracts) {
        errs.push(`no ${CONTRACT_TABLE} table`);
      } else {
        const sample = contracts.records.find(r => !r.isEmpty);
        const terms = { ...CONTRACT_BASELINE };
        for (const k of Object.keys(sample.fields)) if (/^(Salary|Bonus)Year\d+$/.test(k)) terms[k] = 0;
        Object.assign(terms, block);
        errs.push(...fieldErrors(sample, block, CONTRACT_TABLE).map(e => `contract ${e}`));
        if (!(terms.Length >= 1 && terms.CurrentYear >= 0 && terms.CurrentYear < terms.Length)) {
          errs.push(`contract: CurrentYear ${terms.CurrentYear} outside Length ${terms.Length}`);
        }
        if (!errs.length) {
          if (!free.length) errs.push(`no free ${CONTRACT_TABLE} row to reuse (a release in the same write frees one)`);
          else contract = { table: contracts.header.tableId, row: free.shift(), set: terms };
        }
      }
    } else if (item.contract && Object.keys(item.contract).length) {
      errs.push('practice-squad players have no contract record; drop the contract block');
    }
    if (errs.length) { errors.push(...errs.map(e => `${where}: ${e}`)); continue; }
    if (contract) set.Contract = encodeRef(contract.table, contract.row);

    const ref = { table: PID, row };
    ops.append(list, ref);
    if (freeAgents) {
      const inFA = packedRefs(freeAgents.before).refs.some(r => r.table === PID && r.row === row);
      if (item.list === 'Roster' && inFA) ops.remove(freeAgents, ref);
      if (item.list === 'PracticeSquad' && !inFA) ops.append(freeAgents, ref);
    } else if (item.list === 'Roster' && p.ContractStatus === 'FreeAgent') {
      errors.push(`${where}: no League.FreeAgents list`);
      continue;
    }
    const draftRows = [];
    if (p.ContractStatus === 'Draft') {
      const { dp, arrays } = await draftLists();
      for (const t of dp) {
        t.records.forEach((r, i) => {
          const d = r.isEmpty ? null : decodeRef(r.Player);
          if (d && d.table === PID && d.row === row) draftRows.push({ table: t.header.tableId, row: i });
        });
      }
      const keys = new Set(draftRows.map(refKey));
      for (const a of arrays) {
        const hits = a.before.map(decodeRef).filter(r => r && keys.has(refKey(r)));
        for (const h of hits) ops.remove(a, h);
      }
    }
    if (item.list === 'Roster') {
      countDelta(teamEdits, teamTable, team, { ActiveRosterSize: 1, SalCapRosterSize: 1,
        SalCapNextYearRosterSize: contract.set.Length - contract.set.CurrentYear > 1 ? 1 : 0 });
    }
    signs.push({ pgid, row, record: p, from: p.ContractStatus, teamIndex: team.TeamIndex, team: team.ShortName,
      list: item.list, set, contract, draftRows });
  }

  const dry = ops.compute();
  errors.push(...dry.errors.map(e => `sign: ${e}`));
  errors.push(...countErrors(teamEdits).map(e => `sign: ${e}`));
  const contractRecords = contracts ? contracts.records : null;
  return {
    plan: { playerTable: PID, teamTable: teamTable.header.tableId, signs, ops, lists: ops.lists, teamEdits, contractRecords },
    errors,
  };
}

export function applySign(plan) {
  for (const s of plan.signs) {
    if (s.contract) {
      const c = plan.contractRecords[s.contract.row];
      for (const [k, v] of Object.entries(s.contract.set)) if (c[k] !== v) c[k] = v;
    }
    for (const [k, v] of Object.entries(s.set)) s.record[k] = v;
  }
  plan.ops.apply();
  applyCounts(plan.teamEdits);
}

// Re-reads every signed player, his contract row, the lists and roster counts from the written file.
export async function verifySign(franchise, plan) {
  const tables = moveTables(franchise);
  const bad = [];
  const players = await tables.byId(plan.playerTable);
  const holders = new Map();
  for (const r of players.records) if (!r.isEmpty && decodeRef(r.Contract)) holders.set(r.Contract, (holders.get(r.Contract) ?? 0) + 1);
  for (const s of plan.signs) {
    const p = players.records[s.row];
    if (p.PresentationId !== s.pgid) { bad.push(`sign pgid ${s.pgid}: row ${s.row} moved`); continue; }
    bad.push(...fieldMismatches(p, s.set, `sign pgid ${s.pgid}`));
    if (s.contract) {
      const c = (await tables.byId(s.contract.table)).records[s.contract.row];
      bad.push(...fieldMismatches(c, s.contract.set, `sign pgid ${s.pgid} contract`));
      if (holders.get(p.Contract) !== 1) bad.push(`sign pgid ${s.pgid}: contract row held by ${holders.get(p.Contract)} players`);
    }
  }
  bad.push(...await verifyListsAndCounts(tables, plan));
  return bad;
}

export function signSummary(plan) {
  return {
    signed: plan.signs.length,
    roster: plan.signs.filter(s => s.list === 'Roster').length,
    practice_squad: plan.signs.filter(s => s.list === 'PracticeSquad').length,
    prospects: plan.signs.filter(s => s.from === 'Draft').length,
    contracts_reused: plan.signs.filter(s => s.contract).map(s => s.contract.row),
    by_team: plan.signs.reduce((acc, s) => ({ ...acc, [s.team]: (acc[s.team] ?? 0) + 1 }), {}),
  };
}
