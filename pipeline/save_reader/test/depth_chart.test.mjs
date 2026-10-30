// Replaces depth-chart positions in a copy of the sample save and checks the result independently of depth_chart.mjs.
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

// Every team's depth chart as {position: [pgid | null] x 6}, and its Roster and PracticeSquad pgids.
async function charts(path) {
  const dir = mkdtempSync(join(tmpdir(), 'mu-depth-test-'));
  try {
    copyFileSync(path, join(dir, 'save'));
    const f = await Franchise.create(join(dir, 'save'), { autoParse: true, saveOnChange: false });
    const biggest = async name => {
      const all = f.getAllTablesByName(name);
      for (const t of all) await t.readRecords();
      return all.reduce((a, b) => (!a || b.records.length > a.records.length ? b : a), null);
    };
    const players = await biggest('Player');
    const slots = async bits => {
      const r = decodeRef(bits);
      const t = f.getTableById(r.table);
      await t.readRecords();
      const rec = t.records[r.row];
      return Object.keys(rec.fields).sort((a, b) => Number(/\d+$/.exec(a)[0]) - Number(/\d+$/.exec(b)[0]))
        .map(k => decodeRef(rec[k])).map(d => (d ? players.records[d.row].PresentationId : null));
    };
    const out = {};
    for (const t of (await biggest('Team')).records.filter(r => !r.isEmpty && r.TeamIndex < 32)) {
      const dcRef = decodeRef(t.DepthChart);
      const dct = f.getTableById(dcRef.table);
      await dct.readRecords();
      const dc = dct.records[dcRef.row];
      const depth = {};
      for (const pos of Object.keys(dc.fields)) if (pos !== 'LockedEntries') depth[pos] = await slots(dc[pos]);
      out[t.TeamIndex] = { depth, roster: (await slots(t.Roster)).filter(Boolean), ps: (await slots(t.PracticeSquad)).filter(Boolean) };
    }
    return out;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test('replaces the listed positions\' slots and leaves every other position alone', { skip, timeout: 600_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-depth-test-'));
  try {
    const x = await charts(savePath);
    const NYG = 15, NYJ = 17;
    const qbs = x[NYG].depth.QB.filter(Boolean);
    const wrs = x[NYG].depth.WR.filter(Boolean);
    // NYJ's QB line has a hole (starter, backup, empty, third): writing it packs the players from slot 0.
    const jets = x[NYJ].depth.QB.filter(Boolean);
    const edits = { depthCharts: [
      { team_index: NYG, slots: { QB: [...qbs].reverse(), WR: wrs.slice(0, 2), KR: [], PR: [wrs[0], wrs[1], wrs[2], qbs[0], wrs[3], wrs[4]] } },
      { team_index: NYJ, slots: { QB: jets } },
    ] };
    const out = join(dir, 'CAREER-DEPTH');
    const report = await writeSave(savePath, edits, out);
    assert.equal(report.verified, true);
    assert.deepEqual(report.depthCharts, { teams: 2, positions: 5, entries: qbs.length + 2 + 6 + jets.length });

    const y = await charts(out);
    const pad = list => [...list, ...Array(6 - list.length).fill(null)];
    assert.deepEqual(y[NYG].depth.QB, pad([...qbs].reverse()));
    assert.deepEqual(y[NYG].depth.WR, pad(wrs.slice(0, 2)));
    assert.deepEqual(y[NYG].depth.KR, pad([]));
    assert.deepEqual(y[NYG].depth.PR, edits.depthCharts[0].slots.PR);
    assert.deepEqual(y[NYJ].depth.QB, pad(jets));
    assert.notDeepEqual(x[NYJ].depth.QB, pad(jets), 'the NYJ hole was there to begin with');
    for (const [team, c] of Object.entries(x)) {
      for (const [pos, list] of Object.entries(c.depth)) {
        if ((team === String(NYG) && ['QB', 'WR', 'KR', 'PR'].includes(pos)) || (team === String(NYJ) && pos === 'QB')) continue;
        assert.deepEqual(y[team].depth[pos], list, `team ${team} ${pos}`);
      }
      assert.deepEqual([y[team].roster, y[team].ps], [c.roster, c.ps]);
    }
    const [a, b] = [await readSave(savePath), await readSave(out)];
    assert.deepEqual(b.players, a.players);
    assert.deepEqual(b.teams, a.teams);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a depth chart naming a player not on the team\'s roster writes nothing', { skip, timeout: 300_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-depth-test-'));
  try {
    const x = await charts(savePath);
    const NYG = 15, DAL = 10;
    const qbs = x[NYG].depth.QB.filter(Boolean);
    const out = join(dir, 'CAREER-BAD');
    const bad = (depthCharts, re) => assert.rejects(writeSave(savePath, { depthCharts }, out), re);
    await bad([{ team_index: NYG, slots: { QB: [qbs[0], x[DAL].roster[0]] } }], /is not on NYG's Roster/);
    await bad([{ team_index: NYG, slots: { QB: [x[NYG].ps[0]] } }], /is not on NYG's Roster/);     // practice squad
    await bad([{ team_index: NYG, slots: { QB: [999999999] } }], /0 players/);
    await bad([{ team_index: NYG, slots: { Quarterback: qbs } }], /not a depth-chart position/);
    await bad([{ team_index: NYG, slots: { QB: x[NYG].roster.slice(0, 7) } }], /7 players, 6 slots/);
    await bad([{ team_index: NYG, slots: { QB: [qbs[0], qbs[0]] } }], /listed twice/);
    await bad([{ team_index: NYG, slots: { QB: qbs } }, { team_index: NYG, slots: { WR: [] } }], /listed twice/);
    await bad([{ team_index: 40, slots: { QB: qbs } }], /no such team/);
    // A released player can't be listed in the same write; one bad team stops the good one.
    await assert.rejects(writeSave(savePath, { release: [qbs[0]], depthCharts: [{ team_index: NYG, slots: { QB: qbs } }] }, out), /is not on NYG's Roster/);
    await bad([{ team_index: DAL, slots: { QB: [x[DAL].roster[0]] } }, { team_index: NYG, slots: { QB: [x[DAL].roster[0]] } }], /is not on NYG's Roster/);
    assert.equal(existsSync(out), false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
