// Signs free agents and draft prospects in a copy of the sample save and checks the result independently of sign.mjs.
// Skips without a sample save (it's git-ignored).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { copyFileSync, existsSync, mkdtempSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import Franchise from 'madden-franchise';
import { decodeRef } from '../lib.mjs';
import { readSave } from '../pc_save_reader.mjs';
import { writeSave } from '../save_writer.mjs';

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const savePath = process.env.MU_SAMPLE_SAVE || join(repoRoot, 'CAREER-GIANTS-AUTOSAVE');
const skip = existsSync(savePath) ? false : `no sample save at ${savePath}`;

// Team lists, roster counts, the free-agent list, draft-class lists and each player's status and contract row.
async function inspect(path) {
  const dir = mkdtempSync(join(tmpdir(), 'mu-sign-test-'));
  try {
    copyFileSync(path, join(dir, 'save'));
    const f = await Franchise.create(join(dir, 'save'), { autoParse: true, saveOnChange: false });
    const biggest = async name => {
      const all = f.getAllTablesByName(name);
      for (const t of all) await t.readRecords();
      return all.reduce((a, b) => (!a || b.records.length > a.records.length ? b : a), null);
    };
    const refs = async bits => {
      const r = decodeRef(bits);
      if (!r) return [];
      const t = f.getTableById(r.table);
      await t.readRecords();
      const rec = t.records[r.row];
      return Object.keys(rec.fields).sort((a, b) => Number(/\d+$/.exec(a)[0]) - Number(/\d+$/.exec(b)[0]))
        .map(k => decodeRef(rec[k])).filter(Boolean);
    };
    const players = await biggest('Player');
    const pgidOf = r => players.records[r.row].PresentationId;
    const teams = {};
    for (const t of (await biggest('Team')).records.filter(r => !r.isEmpty && r.TeamIndex < 32)) {
      teams[t.TeamIndex] = { name: t.ShortName, roster: (await refs(t.Roster)).map(pgidOf), ps: (await refs(t.PracticeSquad)).map(pgidOf),
        counts: [t.ActiveRosterSize, t.SalCapRosterSize, t.SalCapNextYearRosterSize] };
    }
    const freeAgents = (await refs((await biggest('League')).records[0].FreeAgents)).map(pgidOf);
    const listedDraftRows = new Set();
    for (const t of f.getAllTablesByName('DraftPlayer[]')) {
      await t.readRecords();
      for (const r of t.records) if (!r.isEmpty) for (const k of Object.keys(r.fields)) { const d = decodeRef(r[k]); if (d) listedDraftRows.add(d.row); }
    }
    const draftListed = new Set();
    (await biggest('DraftPlayer')).records.forEach((r, row) => {
      const d = r.isEmpty ? null : decodeRef(r.Player);
      if (d && listedDraftRows.has(row)) draftListed.add(players.records[d.row].PresentationId);
    });
    const player = new Map();
    for (const r of players.records) {
      if (r.isEmpty) continue;
      player.set(r.PresentationId, { status: r.ContractStatus, team: r.TeamIndex, mindset: r.Mindset, contractRow: decodeRef(r.Contract)?.row ?? null });
    }
    return { teams, freeAgents, draftListed, player };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test('signs a free agent and a prospect to rosters (reusing released contract rows) and two players to practice squads',
  { skip, timeout: 600_000 }, async () => {
    const dir = mkdtempSync(join(tmpdir(), 'mu-sign-test-'));
    try {
      const a = await readSave(savePath);
      const x = await inspect(savePath);
      const NYG = 15, DAL = 10, CHI = 0;
      const cut = a.players.filter(p => p.team_index === NYG && p.status === 'Signed').slice(0, 2);
      const fa = a.players.filter(p => p.status === 'FreeAgent');
      const pros = a.players.filter(p => p.status === 'Draft');
      const contract = { Length: 3, SalaryYear0: 120, SalaryYear1: 130, SalaryYear2: 140, BonusYear0: 30, BonusYear1: 30, BonusYear2: 30 };
      const edits = {
        release: cut.map(p => p.pgid),
        sign: [
          { pgid: fa[0].pgid, team_index: NYG, list: 'Roster', contract },
          { pgid: pros[0].pgid, team_index: DAL, list: 'Roster', contract: { Length: 1, SalaryYear0: 75 } },
          { pgid: fa[1].pgid, team_index: CHI, list: 'PracticeSquad' },
          { pgid: pros[1].pgid, team_index: DAL, list: 'PracticeSquad' },
        ],
      };
      const out = join(dir, 'CAREER-SIGNED');
      const report = await writeSave(savePath, edits, out);
      assert.equal(report.verified, true);
      assert.deepEqual([report.sign.signed, report.sign.roster, report.sign.practice_squad, report.sign.prospects], [4, 2, 2, 2]);
      const freed = cut.map(p => x.player.get(p.pgid).contractRow);
      assert.deepEqual([...report.sign.contracts_reused].sort(), [...freed].sort(), 'the released players\' contract rows are reused');

      const y = await inspect(out);
      const [nyg0, nyg1, dal0, dal1, chi0, chi1] = [x.teams[NYG], y.teams[NYG], x.teams[DAL], y.teams[DAL], x.teams[CHI], y.teams[CHI]];
      const gone = new Set(cut.map(p => p.pgid));
      assert.deepEqual(nyg1.roster, [...nyg0.roster.filter(g => !gone.has(g)), fa[0].pgid], 'signed player appended after the release');
      assert.deepEqual(dal1.roster, [...dal0.roster, pros[0].pgid]);
      assert.deepEqual(dal1.ps, [...dal0.ps, pros[1].pgid]);
      assert.deepEqual(chi1.ps, [...chi0.ps, fa[1].pgid]);
      assert.deepEqual(chi1.roster, chi0.roster);
      // ActiveRosterSize / SalCapRosterSize follow the Roster; SalCapNextYearRosterSize counts deals past this year.
      const nextYear = cut.filter(p => p.contract.length - p.contract.current_year > 1).length;
      assert.deepEqual(nyg1.counts, [nyg0.counts[0] - 1, nyg0.counts[1] - 1, nyg0.counts[2] - nextYear + 1]);
      assert.deepEqual(dal1.counts, [dal0.counts[0] + 1, dal0.counts[1] + 1, dal0.counts[2]], 'a 1-year deal adds no next-year spot');
      assert.deepEqual(chi1.counts, chi0.counts, 'practice squad signings leave the counts');
      assert.equal(y.freeAgents.includes(fa[0].pgid), false, 'a roster signing leaves the free-agent list');
      assert.equal(y.freeAgents.filter(g => g === fa[1].pgid).length, 1, 'practice-squad players stay listed as free agents');
      assert.equal(y.freeAgents.filter(g => g === pros[1].pgid).length, 1, 'a prospect on a practice squad joins the list');
      for (const p of [pros[0], pros[1]]) {
        assert.equal(x.draftListed.has(p.pgid), true);
        assert.equal(y.draftListed.has(p.pgid), false, 'a signed prospect leaves the draft-class lists');
      }
      assert.equal(y.draftListed.size, x.draftListed.size - 2);
      assert.deepEqual(y.player.get(fa[1].pgid), { status: 'PracticeSquad', team: CHI, mindset: 'Content', contractRow: null });

      const b = await readSave(out);
      const after = new Map(b.players.map(p => [p.pgid, p]));
      const s = after.get(fa[0].pgid);
      assert.deepEqual([s.status, s.team_index], ['Signed', NYG]);
      assert.deepEqual([s.contract.length, s.contract.current_year, s.contract.void_years, s.contract.times_restructured],
        [3, 0, 0, 0]);
      assert.deepEqual(s.contract.salary_by_year, [120, 130, 140, 0, 0, 0, 0, 0, 0, 0]);
      assert.deepEqual(s.contract.bonus_by_year, [30, 30, 30, 0, 0, 0, 0]);
      const r = after.get(pros[0].pgid);
      assert.deepEqual([r.status, r.team_index, r.contract.length, r.contract.salary_by_year[0], r.contract.bonus_by_year[0]],
        ['Signed', DAL, 1, 75, 0]);
      assert.deepEqual([after.get(pros[1].pgid).status, after.get(pros[1].pgid).contract], ['PracticeSquad', null]);
      // Everyone else reads back as before.
      const moved = new Set([...gone, ...edits.sign.map(e => e.pgid)]);
      for (const p of a.players) if (!moved.has(p.pgid)) assert.deepEqual(after.get(p.pgid), p);
      assert.deepEqual(b.teams, a.teams);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

test('a signing that can\'t be done writes nothing', { skip, timeout: 300_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-sign-test-'));
  try {
    const a = await readSave(savePath);
    const fa = a.players.filter(p => p.status === 'FreeAgent');
    const signed = a.players.find(p => p.status === 'Signed' && p.team_index < 32);
    const out = join(dir, 'CAREER-BAD');
    const one = { Length: 1, SalaryYear0: 75 };
    const bad = (sign, re, extra = {}) => assert.rejects(writeSave(savePath, { ...extra, sign }, out), re);
    // The sample save has no unreferenced PlayerContract row: a roster signing needs a release in the same write.
    await bad([{ pgid: fa[0].pgid, team_index: 0, list: 'Roster', contract: one }], /no free PlayerContract row/);
    await bad([{ pgid: fa[0].pgid, team_index: 0, list: 'Roster', contract: one }, { pgid: fa[1].pgid, team_index: 0, list: 'Roster', contract: one }],
      /no free PlayerContract row/, { release: [signed.pgid] });
    await bad([{ pgid: signed.pgid, team_index: 0, list: 'PracticeSquad' }], /not a free agent or draft prospect/);
    await bad([{ pgid: fa[0].pgid, team_index: 0, list: 'PracticeSquad', contract: one }], /no contract record/);
    await bad([{ pgid: fa[0].pgid, team_index: 0, list: 'Bench' }], /Roster or PracticeSquad/);
    await bad([{ pgid: fa[0].pgid, team_index: 40, list: 'PracticeSquad' }], /no team 40/);
    await bad([{ pgid: fa[0].pgid, team_index: 0, list: 'Roster', contract: { Length: 2, SalaryYear0: 99999 } }], /above/, { release: [signed.pgid] });
    await bad([{ pgid: fa[0].pgid, team_index: 0, list: 'Roster', contract: { Length: 2, CurrentYear: 2 } }], /outside Length/, { release: [signed.pgid] });
    await bad([{ pgid: fa[0].pgid, team_index: 0, list: 'Roster' }], /needs a contract block/, { release: [signed.pgid] });
    await bad([{ pgid: fa[0].pgid, team_index: 15, list: 'PracticeSquad' }], /NYG PracticeSquad: full/);   // NYG carries 12
    await bad([{ pgid: fa[0].pgid, team_index: 0, list: 'PracticeSquad' }, { pgid: fa[0].pgid, team_index: 0, list: 'PracticeSquad' }], /listed twice/);
    assert.equal(existsSync(out), false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
