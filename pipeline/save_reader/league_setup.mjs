#!/usr/bin/env node
// The league office's Node side (docs/done/V2_CORE_BUILD_PLAN.md §2.6, U13): the one-time franchise setup and
// the league state the stage loop needs from a save that the snapshot doesn't carry.
//
//   node league_setup.mjs setup <CAREER-save> --out <new-save>
//   node league_setup.mjs state <CAREER-save>                 (JSON on stdout)
//
// setup (spike R2 parts 1-3, coach firing / career clock spike; port of verify_output/save_scripts/realusers3.mjs):
//   * Every real team that has no user gets a real FranchiseUser row, copied from the user's own (the first live
//     FranchiseUser row) with Team and UserEntity (the team's head coach) re-pointed, a distinct UserIdUpper =
//     2147483648 + k·2^24 (k = 1, 2, ... in the order users are added, skipping ids already taken), AdminLevel None,
//     ReadyToAdvance false, UserPlayerGrade '', YearlyAwardsUnlocked false, quests / objective progress / cross-arc
//     data / college import / story side-activity context null, and fresh per-user rows for FirstTimeAchievement,
//     the three weekly-goal arrays, NonRepeatableArcsTriggered (StoryArcMetadata[]), ScenarioCoachPromises and
//     TeamSetting (with its own AutoSubSlider). Array rows start empty; struct rows copy the user's values. The other
//     references (the one-row tables Madden duplicates per user: GamePointGrantMtxInfo, Story[], RewardEntry[],
//     DrillProgress[]) are shared with the user's, since the library can't create tables. Each new row is added to
//     UserRegistry.Users; Team.UserCharacter points at the head coach and Coach.IsUserControlled is set. Madden fills
//     the per-user request records itself after one Advance Week. Teams that already have a user are skipped, so a
//     second run adds nobody.
//   * Every TeamSetting row a team (Team.TeamSettingRef) or a user (FranchiseUser.TeamSetting) uses gets
//     IsCPUFillRosterEnabled false. The other staff switches (injury management, cut players, re-sign, offseason FA,
//     draft: they read backwards, true = Manual) and IsTradesEnabled are left as they are.
//   * Every live Player row gets ConfidenceRating 50 (morale neutral, D13), so the new save is the known-neutral
//     starting point progression's explicit bootstrap needs (PROGRESSION_PLAN §5.3a: bootstrap_pass requires every
//     player at ConfidenceRating 50 and neutral form; no Project Mu form overlay exists before the first write).
//   * LeagueSetting row 0: CoachFiring = 2 (OFF; enum CoachFiringSetting ON 0, CPU_ONLY 1, OFF 2) and
//     IsCareerClockEnabled = 0. Under schema 620 the row decodes with generic names, so each field is resolved by its
//     position in the schema's LeagueSetting attribute list (lib.mjs schemaFieldName: Field_44 and Field_82 on the
//     sample saves, also the fallback indexes).
//   The input save is never written (a temp copy is opened), --out must not exist, and the written file is re-opened
//   and checked: one user per real team, distinct user ids, each user's team pointing back at the user's coach
//   (UserCharacter = HeadCoach = UserEntity, Coach.IsUserControlled), every user in UserRegistry.Users, fill roster
//   off on every used TeamSetting row, CoachFiring OFF, career clock off, every live player at ConfidenceRating 50.
//   A failed check deletes the output.
//
// state: what pipeline/league_office.py needs beyond the snapshot to write cap fields and to know how many Roster
//   signings one write can take: {team_table_id, salary_cap (SalaryInfo.TeamSalaryCap, $10,000 units),
//   teams: [{team_index, row, TEAM_SALARY, SalCapCapRoom, SalCapNextYearCapRoom, SalCapSpendingMoney}],
//   spare_contract_rows: PlayerContract rows no schema field references (sign.mjs reuses them for Roster signings)}.
import { copyFileSync, existsSync, mkdtempSync, renameSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { basename, join, resolve } from 'path';
import { fileURLToPath } from 'url';
import Franchise from 'madden-franchise';
import { arraySlots, cloneInto, decodeRef, encodeRef, NO_REF, salaryCapUnits, schemaFieldName } from './lib.mjs';

export const REAL_TEAMS = 32;
export const USER_ID_BASE = 2147483648;
export const USER_ID_STEP = 2 ** 24;
// FranchiseUser references that get a fresh row per user (arrays start empty, structs copy the user's values).
export const PER_USER = new Set(['FirstTimeAchievement', 'LastWeekCompletedWeeklyGoalsArray',
  'DifficultyLevelPlayerWeeklyGoalsArray', 'CurrentCompletedWeeklyGoalsArray', 'NonRepeatableArcsTriggeredArray',
  'ScenarioCoachPromisesArray', 'TeamSetting']);
// FranchiseUser references Madden leaves empty for an added user.
export const NULLED = new Set(['CurrentPlayerQuestsData', 'ObjectiveProgressArray', 'CrossArcDataArray',
  'CollegeImportInfo', 'StorySideActivityContext']);
export const FILL_ROSTER = 'IsCPUFillRosterEnabled';
// LeagueSetting fields the setup writes: schema attribute name -> [value, attribute index under schema 620].
export const LEAGUE_SETTINGS = { CoachFiring: [2, 44], IsCareerClockEnabled: [0, 82] };
export const CONFIDENCE = 'ConfidenceRating';
export const NEUTRAL_CONFIDENCE = 50;
export const CAP_FIELDS = ['TEAM_SALARY', 'SalCapCapRoom', 'SalCapNextYearCapRoom', 'SalCapSpendingMoney'];
const CONTRACT_TABLE = 'PlayerContract';

const safe = (r, k) => { try { return r[k]; } catch { return undefined; } };
const isRef = v => typeof v === 'string' && /^[01]{32}$/.test(v);

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
    byId: id => load(franchise.getTableById(id)),
    byName: async name => {
      const all = await Promise.all(franchise.getAllTablesByName(name).map(load));
      return all.reduce((best, t) => (!best || t.records.length > best.records.length ? t : best), null);
    },
    first: async name => load(franchise.getAllTablesByName(name)[0]),
    allByName: name => Promise.all(franchise.getAllTablesByName(name).map(load)),
  };
}

function attrNames(franchise, table) {
  try {
    const schema = franchise.schemaList?.getSchema?.(table);
    return Array.isArray(schema?.attributes) ? schema.attributes.map(a => a.name) : null;
  } catch {
    return null;
  }
}

const liveUsers = fu => fu.records.map((r, row) => ({ r, row })).filter(x => !x.r.isEmpty);

async function userRegistry(tables) {
  const ur = await tables.first('UserRegistry');
  const ref = ur ? decodeRef(ur.records[0].Users) : null;
  if (!ref) throw new Error('no UserRegistry.Users list');
  const arr = (await tables.byId(ref.table)).records[ref.row];
  return { arr, slots: arraySlots(Object.keys(arr.fields)) };
}

// TeamSetting rows used by teams and users: Map row -> label.
function usedTeamSettings(team, fu) {
  const rows = new Map();
  for (const r of team.records) {
    if (r.isEmpty || r.TeamIndex >= REAL_TEAMS) continue;
    const d = decodeRef(r.TeamSettingRef);
    if (d) rows.set(`${d.table}:${d.row}`, d);
  }
  for (const { r } of liveUsers(fu)) {
    const d = decodeRef(r.TeamSetting);
    if (d) rows.set(`${d.table}:${d.row}`, d);
  }
  return [...rows.values()];
}

// Adds the missing users, turns fill roster off and writes the two LeagueSetting fields inside an open franchise.
export async function applySetup(franchise) {
  const tables = tableCache(franchise);
  const fuT = await tables.first('FranchiseUser');
  const team = await tables.byName('Team');
  const users = liveUsers(fuT);
  if (!users.length) throw new Error('no FranchiseUser row to copy (the save has no user)');
  const tpl = users[0].r;
  const userTeams = new Set(users.map(u => decodeRef(u.r.Team)).filter(Boolean).map(d => d.row));
  const takenIds = new Set(users.map(u => u.r.UserIdUpper));
  const used = new Set();
  const freeRow = t => {
    const i = t.records.findIndex((r, k) => r.isEmpty && !used.has(`${t.header.tableId}:${k}`));
    if (i < 0) throw new Error(`no free row in ${t.name} (table ${t.header.tableId})`);
    used.add(`${t.header.tableId}:${i}`);
    return i;
  };
  // A copy of a record in a free row of its table (lib.mjs cloneInto); array slots start empty, TeamSetting gets its
  // own AutoSubSlider.
  const clone = (tableId, templateRow, asEmptyArray) => cloneInto(tables, tableId, templateRow, asEmptyArray, freeRow);
  const registry = await userRegistry(tables);
  let slot = registry.slots.findIndex(k => !decodeRef(registry.arr[k]));
  let k = 0;
  const nextId = () => {
    let id;
    do { k += 1; id = USER_ID_BASE + k * USER_ID_STEP; } while (takenIds.has(id));
    takenIds.add(id);
    return id;
  };

  const created = [];
  for (const [ti, tr] of team.records.entries()) {
    if (tr.isEmpty || tr.TeamIndex >= REAL_TEAMS || userTeams.has(ti)) continue;
    const hc = decodeRef(tr.HeadCoach);
    if (!hc) throw new Error(`team ${tr.ShortName}: no head coach`);
    const coach = (await tables.byId(hc.table)).records[hc.row];
    if (slot < 0 || slot >= registry.slots.length) throw new Error('UserRegistry.Users is full');
    const row = freeRow(fuT);
    const nu = fuT.records[row];
    for (const key of Object.keys(tpl.fields)) {
      const v = safe(tpl, key);
      if (v === undefined) continue;
      const d = isRef(v) ? decodeRef(v) : null;
      if (key === 'Team') nu[key] = encodeRef(team.header.tableId, ti);
      else if (key === 'UserEntity') nu[key] = encodeRef(hc.table, hc.row);
      else if (d && PER_USER.has(key)) nu[key] = await clone(d.table, d.row, (await tables.byId(d.table)).name.endsWith('[]'));
      else if (key === 'AdminLevel') nu[key] = 'None';
      else if (key === 'UserIdUpper') nu[key] = nextId();
      else if (NULLED.has(key)) nu[key] = NO_REF;
      else if (key === 'UserPlayerGrade') nu[key] = '';
      else if (key === 'YearlyAwardsUnlocked' || key === 'ReadyToAdvance') nu[key] = false;
      else nu[key] = v;
    }
    if (nu.isEmpty) throw new Error('FranchiseUser row still empty after the copy');
    registry.arr[registry.slots[slot]] = encodeRef(fuT.header.tableId, row);
    slot = registry.slots.findIndex(s => !decodeRef(registry.arr[s]));
    tr.UserCharacter = encodeRef(hc.table, hc.row);
    coach.IsUserControlled = true;
    created.push(tr.ShortName);
  }
  // Teams that already had a user still get UserCharacter / IsUserControlled made consistent.
  for (const { r } of liveUsers(fuT)) {
    const t = decodeRef(r.Team);
    const tr = t && team.records[t.row];
    const hc = tr && decodeRef(tr.HeadCoach);
    if (!hc) continue;
    if (decodeRef(tr.UserCharacter)?.row !== hc.row || decodeRef(tr.UserCharacter)?.table !== hc.table) {
      tr.UserCharacter = encodeRef(hc.table, hc.row);
    }
    const coach = (await tables.byId(hc.table)).records[hc.row];
    if (coach.IsUserControlled !== true) coach.IsUserControlled = true;
  }

  let fillRosterChanged = 0;
  const settingRows = usedTeamSettings(team, fuT);
  for (const d of settingRows) {
    const r = (await tables.byId(d.table)).records[d.row];
    if (r[FILL_ROSTER] !== false) { r[FILL_ROSTER] = false; fillRosterChanged++; }
  }

  const ls = await tables.byName('LeagueSetting');
  const setting = ls?.records[0];
  if (!setting || setting.isEmpty) throw new Error('no LeagueSetting row');
  const names = Object.keys(setting.fields);
  const attrs = attrNames(franchise, 'LeagueSetting');
  const leagueSettings = {};
  for (const [name, [value, index]] of Object.entries(LEAGUE_SETTINGS)) {
    const field = schemaFieldName(names, attrs, name, index);
    if (!field) throw new Error(`LeagueSetting has no ${name} field`);
    const before = setting[field];
    const want = typeof before === 'boolean' ? Boolean(value) : value;
    if (before !== want) setting[field] = want;
    leagueSettings[name] = { field, before, after: want };
  }
  const salary = await tables.first('SalaryInfo');
  let capIncreaseZeroed = 0;
  if (salary && salary.records[0] && salary.records[0].SalaryCapIncreasePerYear) {
    const ref = decodeRef(salary.records[0].SalaryCapIncreasePerYear);
    if (ref) {
      const incTable = await tables.byId(ref.table);
      const incRecord = incTable?.records[ref.row];
      if (incRecord) {
        for (const k of Object.keys(incRecord.fields)) {
          if (!k.startsWith('int')) continue;
          if (incRecord[k] !== 0) {
            incRecord[k] = 0;
            capIncreaseZeroed++;
          }
        }
      }
    }
  }
  const confidence = await neutralConfidence(tables, true);
  return { usersCreated: created, teamSettingRows: settingRows.length, fillRosterChanged, leagueSettings,
    confidenceChanged: confidence.changed, players: confidence.players, capIncreaseZeroed };
}

// Every live Player row at ConfidenceRating 50 (`write` true) or a count of those that aren't (`write` false).
async function neutralConfidence(tables, write) {
  const player = await tables.byName('Player');
  if (!player || !player.records.some(r => !r.isEmpty && CONFIDENCE in r.fields)) {
    throw new Error(`no Player table with ${CONFIDENCE}`);
  }
  let changed = 0;
  let off = 0;
  let players = 0;
  for (const r of player.records) {
    if (r.isEmpty) continue;
    players++;
    if (r[CONFIDENCE] === NEUTRAL_CONFIDENCE) continue;
    if (write) { r[CONFIDENCE] = NEUTRAL_CONFIDENCE; changed++; } else off++;
  }
  return { changed, off, players };
}

// Checks a written save: returns a list of problems (empty = good) plus counts.
export async function verifySetup(franchise) {
  const tables = tableCache(franchise);
  const fuT = await tables.first('FranchiseUser');
  const team = await tables.byName('Team');
  const users = liveUsers(fuT);
  const problems = [];
  const ids = new Set(users.map(u => u.r.UserIdUpper));
  if (ids.size !== users.length) problems.push(`user ids not distinct: ${ids.size} ids for ${users.length} users`);
  const teamsWithUser = new Set();
  for (const { r, row } of users) {
    const tm = decodeRef(r.Team);
    const ue = decodeRef(r.UserEntity);
    const tr = tm && team.records[tm.row];
    if (!tr || tr.isEmpty) { problems.push(`user row ${row}: no team`); continue; }
    teamsWithUser.add(tr.TeamIndex);
    const hc = decodeRef(tr.HeadCoach);
    const uc = decodeRef(tr.UserCharacter);
    const same = (a, b) => a && b && a.table === b.table && a.row === b.row;
    if (!same(hc, ue) || !same(uc, ue)) problems.push(`user row ${row} (${tr.ShortName}): head coach, UserCharacter and UserEntity differ`);
    const coach = ue && (await tables.byId(ue.table)).records[ue.row];
    if (!coach || coach.IsUserControlled !== true) problems.push(`user row ${row} (${tr.ShortName}): coach not user-controlled`);
  }
  const real = team.records.filter(r => !r.isEmpty && r.TeamIndex < REAL_TEAMS).length;
  if (teamsWithUser.size !== real || users.length !== real) problems.push(`${users.length} users on ${teamsWithUser.size} of ${real} teams`);
  const registry = await userRegistry(tables);
  const listed = new Set(registry.slots.map(k => decodeRef(registry.arr[k])).filter(Boolean)
    .filter(d => d.table === fuT.header.tableId).map(d => d.row));
  const missing = users.filter(u => !listed.has(u.row)).length;
  if (missing) problems.push(`${missing} users missing from UserRegistry.Users`);
  const settingRows = usedTeamSettings(team, fuT);
  let fillOn = 0;
  for (const d of settingRows) if ((await tables.byId(d.table)).records[d.row][FILL_ROSTER] !== false) fillOn++;
  if (fillOn) problems.push(`fill roster still on for ${fillOn} TeamSetting rows`);
  const ls = await tables.byName('LeagueSetting');
  const setting = ls.records[0];
  const names = Object.keys(setting.fields);
  const attrs = attrNames(franchise, 'LeagueSetting');
  const settings = {};
  for (const [name, [value, index]] of Object.entries(LEAGUE_SETTINGS)) {
    const field = schemaFieldName(names, attrs, name, index);
    const got = field ? setting[field] : undefined;
    settings[name] = got;
    if (got !== value && got !== Boolean(value)) problems.push(`${name} (${field}) is ${got}, not ${value}`);
  }
  const confidence = await neutralConfidence(tables, false);
  if (confidence.off) problems.push(`${confidence.off} of ${confidence.players} players not at ${CONFIDENCE} ${NEUTRAL_CONFIDENCE}`);
  const salary = await tables.first('SalaryInfo');
  if (salary && salary.records[0] && salary.records[0].SalaryCapIncreasePerYear) {
    const ref = decodeRef(salary.records[0].SalaryCapIncreasePerYear);
    if (ref) {
      const incTable = await tables.byId(ref.table);
      const incRecord = incTable?.records[ref.row];
      if (incRecord) {
        for (const k of Object.keys(incRecord.fields)) {
          if (!k.startsWith('int')) continue;
          if (incRecord[k] !== 0) {
            problems.push(`SalaryCapIncreasePerYear slot ${k} is ${incRecord[k]}, not 0`);
          }
        }
      }
    }
  }
  return { problems, users: users.length, distinctIds: ids.size, registryListed: users.length - missing,
    teamSettingRows: settingRows.length, settings, playersChecked: confidence.players };
}

export async function setupLeague(inputPath, outPath) {
  const input = resolve(inputPath);
  const out = resolve(outPath);
  if (out === input) throw new Error('--out must not be the input save');
  if (existsSync(out)) throw new Error(`${out} exists`);
  const dir = mkdtempSync(join(tmpdir(), 'mu-setup-'));
  try {
    const work = join(dir, basename(input));
    copyFileSync(input, work);
    const franchise = await Franchise.create(work, { autoParse: true, saveOnChange: false, autoUnempty: true });
    const applied = await applySetup(franchise);
    const staged = join(dir, 'out');
    await franchise.save(staged);
    try {
      renameSync(staged, out);
    } catch (err) {
      if (err.code !== 'EXDEV') throw err;
      copyFileSync(staged, out);
    }
    const check = await verifySetup(await Franchise.create(out, { autoParse: true, saveOnChange: false }));
    if (check.problems.length) {
      rmSync(out, { force: true });
      throw new Error(`read-back failed, output removed:\n  ${check.problems.join('\n  ')}`);
    }
    return { out, ...applied, ...check, verified: true };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

// PlayerContract rows no schema field references (sign.mjs's rule for a reusable row).
async function spareContractRows(franchise, tables) {
  const fields = new Map();
  for (const s of franchise.schemaList?.schemas ?? []) {
    for (const a of s.attributes ?? []) if (a.type === CONTRACT_TABLE) fields.set(s.name, [...(fields.get(s.name) ?? []), a.name]);
  }
  fields.set('Player', [...new Set([...(fields.get('Player') ?? []), 'Contract'])]);
  const usedRefs = new Set();
  const counts = new Map();
  for (const [name, keys] of fields) {
    for (const t of await tables.allByName(name)) {
      for (const r of t.records) {
        if (r.isEmpty) continue;
        for (const k of keys) {
          const d = decodeRef(safe(r, k));
          if (!d) continue;
          usedRefs.add(`${d.table}:${d.row}`);
          if (name === 'Player') counts.set(d.table, (counts.get(d.table) ?? 0) + 1);
        }
      }
    }
  }
  const best = [...counts].sort((a, b) => b[1] - a[1])[0];
  const table = best ? await tables.byId(best[0]) : await tables.byName(CONTRACT_TABLE);
  if (!table || table.name !== CONTRACT_TABLE) return 0;
  return table.records.filter((r, row) => !r.isEmpty && !usedRefs.has(`${table.header.tableId}:${row}`)).length;
}

export async function leagueState(savePath) {
  const dir = mkdtempSync(join(tmpdir(), 'mu-state-'));
  try {
    const work = join(dir, basename(savePath));
    copyFileSync(savePath, work);
    const franchise = await Franchise.create(work, { autoParse: true, saveOnChange: false });
    const tables = tableCache(franchise);
    const team = await tables.byName('Team');
    const teams = [];
    team.records.forEach((r, row) => {
      if (r.isEmpty || r.TeamIndex >= REAL_TEAMS) return;
      teams.push({ team_index: r.TeamIndex, row, ...Object.fromEntries(CAP_FIELDS.map(k => [k, safe(r, k)])) });
    });
    const salary = await tables.first('SalaryInfo');
    const rawSalaryCap = salary?.records[0]?.TeamSalaryCap ?? null;
    return {
      team_table_id: team.header.tableId,
      salary_info_table_id: salary?.header.tableId ?? null,
      raw_salary_cap: rawSalaryCap,
      salary_cap: salaryCapUnits(rawSalaryCap),
      teams: teams.sort((a, b) => a.team_index - b.team_index),
      spare_contract_rows: await spareContractRows(franchise, tables),
    };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function usage() {
  console.error('usage: node league_setup.mjs setup <CAREER-save> --out <new-save>\n'
    + '       node league_setup.mjs state <CAREER-save>');
  process.exit(2);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  const [cmd, ...rest] = process.argv.slice(2);
  let save = null;
  let out = null;
  for (let i = 0; i < rest.length; i++) {
    if (rest[i] === '--out') out = rest[++i];
    else if (!save) save = rest[i];
  }
  try {
    if (cmd === 'setup') {
      if (!save || !out) usage();
      const r = await setupLeague(save, out);
      console.error(`users created ${r.usersCreated.length} (${r.usersCreated.join(' ') || 'none'}); users ${r.users}, `
        + `distinct ids ${r.distinctIds}, registry ${r.registryListed}; fill roster turned off on ${r.fillRosterChanged} `
        + `of ${r.teamSettingRows} TeamSetting rows; ${Object.entries(r.leagueSettings).map(([k, v]) => `${k} `
        + `${v.field} ${v.before} -> ${v.after}`).join(', ')}; ConfidenceRating 50 set on ${r.confidenceChanged} of `
        + `${r.players} players; cap increase zeroed on ${r.capIncreaseZeroed} slots; read back -> ${r.out}`);
      process.stdout.write(JSON.stringify(r));
    } else if (cmd === 'state') {
      if (!save) usage();
      process.stdout.write(JSON.stringify(await leagueState(save)));
    } else {
      usage();
    }
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  }
}
