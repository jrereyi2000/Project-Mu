// The league office's one-time setup (league_setup.mjs) on a copy of the sample save, checked independently of the
// module's own read-back. Skips without MU_SAMPLE_SAVE (the sample save is git-ignored). Temp dirs only.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'crypto';
import { copyFileSync, existsSync, mkdtempSync, readFileSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import Franchise from 'madden-franchise';
import { decodeRef } from '../lib.mjs';
import { leagueState, setupLeague } from '../league_setup.mjs';

const savePath = process.env.MU_SAMPLE_SAVE;
const skip = !savePath ? 'MU_SAMPLE_SAVE not set' : existsSync(savePath) ? false : `no sample save at ${savePath}`;
const sha = p => createHash('sha256').update(readFileSync(p)).digest('hex');

// Users, their teams and coaches, the registry, used TeamSetting rows and the two LeagueSetting fields.
async function inspect(path) {
  const dir = mkdtempSync(join(tmpdir(), 'mu-setup-test-'));
  try {
    copyFileSync(path, join(dir, 'save'));
    const f = await Franchise.create(join(dir, 'save'), { autoParse: true, saveOnChange: false });
    const load = async id => { const t = f.getTableById(id); await t.readRecords(); return t; };
    const biggest = async name => {
      const all = f.getAllTablesByName(name);
      for (const t of all) await t.readRecords();
      return all.reduce((a, b) => (!a || b.records.length > a.records.length ? b : a), null);
    };
    const fu = f.getAllTablesByName('FranchiseUser')[0];
    await fu.readRecords();
    const team = await biggest('Team');
    const users = fu.records.map((r, row) => ({ r, row })).filter(x => !x.r.isEmpty);
    const out = { users: [], registry: [], settings: [], leagueSetting: {} };
    for (const { r, row } of users) {
      const tm = decodeRef(r.Team);
      const ue = decodeRef(r.UserEntity);
      const tr = team.records[tm.row];
      const hc = decodeRef(tr.HeadCoach);
      const uc = decodeRef(tr.UserCharacter);
      const coach = (await load(ue.table)).records[ue.row];
      out.users.push({ row, id: r.UserIdUpper, team: tr.TeamIndex, admin: r.AdminLevel,
        coachMatches: hc.table === ue.table && hc.row === ue.row && uc?.table === ue.table && uc?.row === ue.row,
        controlled: coach.IsUserControlled, setting: decodeRef(r.TeamSetting) });
    }
    const ur = f.getAllTablesByName('UserRegistry')[0];
    await ur.readRecords();
    const ua = decodeRef(ur.records[0].Users);
    const arr = (await load(ua.table)).records[ua.row];
    out.registry = Object.keys(arr.fields).map(k => decodeRef(arr[k])).filter(Boolean)
      .filter(d => d.table === fu.header.tableId).map(d => d.row).sort((a, b) => a - b);
    const rows = new Map();
    for (const r of team.records) {
      if (r.isEmpty || r.TeamIndex >= 32) continue;
      const d = decodeRef(r.TeamSettingRef);
      if (d) rows.set(`${d.table}:${d.row}`, d);
    }
    for (const u of out.users) if (u.setting) rows.set(`${u.setting.table}:${u.setting.row}`, u.setting);
    for (const d of rows.values()) {
      const s = (await load(d.table)).records[d.row];
      out.settings.push({ key: `${d.table}:${d.row}`, fill: s.IsCPUFillRosterEnabled, injury: s.IsInjuryManagementEnabled,
        trades: s.IsTradesEnabled });
    }
    out.settings.sort((a, b) => a.key.localeCompare(b.key));
    const ls = f.getAllTablesByName('LeagueSetting')[0];
    await ls.readRecords();
    out.leagueSetting = { coachFiring: ls.records[0].Field_44, careerClock: ls.records[0].Field_82 };
    out.realTeams = team.records.filter(r => !r.isEmpty && r.TeamIndex < 32).length;
    const player = await biggest('Player');
    const live = player.records.filter(r => !r.isEmpty);
    out.players = live.length;
    out.confidenceOff = live.filter(r => r.ConfidenceRating !== 50).length;
    return out;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test('setup makes all 32 teams real users with fill roster, coach firing and the career clock off', { skip }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-setup-test-'));
  try {
    const before = sha(savePath);
    const original = await inspect(savePath);
    const out1 = join(dir, 'once');
    const r1 = await setupLeague(savePath, out1);
    assert.equal(sha(savePath), before, 'the input save is never written');
    assert.equal(r1.verified, true);
    assert.equal(r1.usersCreated.length, original.realTeams - original.users.length);

    const s = await inspect(out1);
    assert.equal(s.users.length, 32);
    assert.equal(new Set(s.users.map(u => u.id)).size, 32, 'distinct user ids');
    assert.deepEqual([...new Set(s.users.map(u => u.team))].sort((a, b) => a - b), [...Array(32).keys()]);
    assert.ok(s.users.every(u => u.coachMatches && u.controlled === true), 'each user team points at its own coach');
    const added = s.users.filter(u => !original.users.some(o => o.row === u.row));
    assert.ok(added.every(u => u.admin === 'None' && (u.id - 2147483648) % 2 ** 24 === 0));
    assert.deepEqual(s.registry, s.users.map(u => u.row).sort((a, b) => a - b), 'every user in UserRegistry.Users');
    assert.ok(s.settings.length >= 32);
    assert.ok(s.settings.every(x => x.fill === false), 'fill roster off on every used TeamSetting row');
    // the other switches are left as they were for the rows that existed before
    for (const o of original.settings) {
      const n = s.settings.find(x => x.key === o.key);
      if (n) assert.deepEqual([n.injury, n.trades], [o.injury, o.trades]);
    }
    assert.deepEqual(s.leagueSetting, { coachFiring: 2, careerClock: 0 });
    assert.equal(s.players, original.players, 'no player rows added or removed');
    assert.equal(s.confidenceOff, 0, 'every player at ConfidenceRating 50 (neutral for the progression bootstrap)');
    assert.equal(r1.confidenceChanged, original.confidenceOff);

    // a second run adds nobody and changes nothing
    const out2 = join(dir, 'twice');
    const r2 = await setupLeague(out1, out2);
    assert.deepEqual(r2.usersCreated, []);
    assert.equal(r2.fillRosterChanged, 0);
    assert.equal(r2.confidenceChanged, 0);
    assert.deepEqual(await inspect(out2), s);

    await assert.rejects(setupLeague(savePath, out1), /exists/, 'an existing --out is refused');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('state reports the Team table, cap fields and spare contract rows', { skip }, async () => {
  const st = await leagueState(savePath);
  assert.ok(Number.isInteger(st.team_table_id));
  assert.equal(st.teams.length, 32);
  assert.deepEqual(st.teams.map(t => t.team_index), [...Array(32).keys()]);
  for (const t of st.teams) for (const k of ['TEAM_SALARY', 'SalCapCapRoom', 'SalCapNextYearCapRoom', 'SalCapSpendingMoney']) {
    assert.ok(Number.isInteger(t[k]), `${k} of team ${t.team_index}`);
  }
  assert.ok(Number.isInteger(st.spare_contract_rows) && st.spare_contract_rows >= 0);
});
