// Releases players in a copy of the sample save and checks the result independently of release.mjs. Skips without a
// sample save (it's git-ignored).
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

// Every player row each team (TeamIndex < 32) references from Roster, PracticeSquad and its depth chart, plus the
// league free-agent list and the roster counts.
async function teamRefs(path) {
  const dir = mkdtempSync(join(tmpdir(), 'mu-release-test-'));
  try {
    copyFileSync(path, join(dir, 'save'));
    const f = await Franchise.create(join(dir, 'save'), { autoParse: true, saveOnChange: false });
    const biggest = async name => {
      const all = f.getAllTablesByName(name);
      for (const t of all) await t.readRecords();
      return all.reduce((a, b) => (!a || b.records.length > a.records.length ? b : a), null);
    };
    const record = async bits => {
      const r = decodeRef(bits);
      if (!r) return null;
      const t = f.getTableById(r.table);
      await t.readRecords();
      return t.records[r.row];
    };
    const rows = async bits => {
      const rec = await record(bits);
      return rec ? Object.keys(rec.fields).map(k => decodeRef(rec[k])).filter(Boolean).map(x => x.row) : [];
    };
    const players = await biggest('Player');
    const teams = {};
    for (const t of (await biggest('Team')).records.filter(r => !r.isEmpty && r.TeamIndex < 32)) {
      const dc = await record(t.DepthChart);
      const depth = [];
      for (const pos of Object.keys(dc.fields)) if (pos !== 'LockedEntries') depth.push(...await rows(dc[pos]));
      teams[t.ShortName] = { roster: await rows(t.Roster), ps: await rows(t.PracticeSquad), depth,
        active: t.ActiveRosterSize, salCap: t.SalCapRosterSize };
    }
    const league = await biggest('League');
    const pgidOf = row => players.records[row].PresentationId;
    return { teams, freeAgents: await rows(league.records[0].FreeAgents), pgidOf };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test('released players leave every team list and join free agency; nothing else changes', { skip, timeout: 600_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-release-test-'));
  try {
    const a = await readSave(savePath);
    const find = (team, pred) => a.players.find(p => p.team_index === team && pred(p));
    const dart = find(15, p => p.last_name === 'Dart');                          // NYG starting QB
    const stroud = find(17, p => p.last_name === 'Stroud' && p.position === 'QB');  // NYJ QB2, before a depth hole
    const ps = find(15, p => p.status === 'PracticeSquad');
    const pgids = [dart.pgid, stroud.pgid, ps.pgid];
    const out = join(dir, 'CAREER-RELEASED');
    const report = await writeSave(savePath, { release: pgids }, out);
    assert.equal(report.verified, true);
    assert.deepEqual([report.release.released, report.release.signed, report.release.practice_squad], [3, 2, 1]);
    assert.equal(report.release.free_agents_added, 2, 'practice-squad players are already in the free-agent list');

    const [x, y] = [await teamRefs(savePath), await teamRefs(out)];
    const gone = new Set(pgids);
    for (const [name, t] of Object.entries(y.teams)) {
      for (const row of [...t.roster, ...t.ps, ...t.depth]) assert.ok(!gone.has(y.pgidOf(row)), `${name} still lists a released player`);
      const was = x.teams[name];
      const cut = was.roster.filter(r => gone.has(x.pgidOf(r))).length;
      assert.deepEqual(t.roster, was.roster.filter(r => !gone.has(x.pgidOf(r))), `${name} roster order kept`);
      assert.deepEqual(t.ps, was.ps.filter(r => !gone.has(x.pgidOf(r))));
      assert.deepEqual([t.active, t.salCap], [was.active - cut, was.salCap - cut]);
      assert.equal(t.active, t.roster.length);
    }
    for (const g of pgids) assert.equal(y.freeAgents.filter(r => y.pgidOf(r) === g).length, 1);
    assert.deepEqual(y.freeAgents.slice(0, x.freeAgents.length), x.freeAgents, 'existing free agents keep their order');
    // NYJ QB was starter, Stroud, empty, third: Stroud goes, the hole moves up with the rest.
    assert.equal(y.teams.NYJ.depth.length, x.teams.NYJ.depth.length - 1);

    const b = await readSave(out);
    assert.deepEqual(b.games, a.games);
    assert.deepEqual(b.season, a.season);
    assert.deepEqual(b.teams, a.teams);
    const before = new Map(a.players.map(p => [p.pgid, p]));
    for (const p of b.players) {
      if (!gone.has(p.pgid)) { assert.deepEqual(p, before.get(p.pgid)); continue; }
      assert.deepEqual([p.status, p.team_index, p.contract], ['FreeAgent', 32, null]);
      assert.deepEqual({ ...p, status: 0, team_index: 0, contract: 0 }, { ...before.get(p.pgid), status: 0, team_index: 0, contract: 0 });
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a release that can\'t be done writes nothing', { skip, timeout: 300_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-release-test-'));
  try {
    const a = await readSave(savePath);
    const signed = a.players.find(p => p.status === 'Signed' && p.team_index < 32).pgid;
    const freeAgent = a.players.find(p => p.status === 'FreeAgent').pgid;
    const out = join(dir, 'CAREER-BAD');
    await assert.rejects(writeSave(savePath, { release: [freeAgent] }, out), /not a rostered player/);
    await assert.rejects(writeSave(savePath, { release: [999999999] }, out), /0 players/);
    await assert.rejects(writeSave(savePath, { release: [signed, signed] }, out), /listed twice/);
    await assert.rejects(writeSave(savePath, { players: [{ pgid: signed, set: { SpeedRating: 90 } }], release: [signed] }, out),
      /also has field edits/);
    await assert.rejects(writeSave(savePath, { release: [signed, freeAgent] }, out), /not a rostered player/);  // all or nothing
    assert.equal(existsSync(out), false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
