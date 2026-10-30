// Trades players and a pick in a copy of the sample save and checks the result independently of trade.mjs. Skips
// without a sample save (it's git-ignored).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { copyFileSync, existsSync, mkdtempSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import Franchise from 'madden-franchise';
import { decodeRef } from '../lib.mjs';
import { readDraftTables } from '../draft_tables.mjs';
import { readSave } from '../pc_save_reader.mjs';
import { writeSave } from '../save_writer.mjs';

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const savePath = process.env.MU_SAMPLE_SAVE || join(repoRoot, 'CAREER-GIANTS-AUTOSAVE');
const skip = existsSync(savePath) ? false : `no sample save at ${savePath}`;

// Per team: Roster, PracticeSquad, every depth-chart position and the smaller lists as pgids, and the roster counts;
// plus the draft tables and each player's raw Contract reference.
async function inspect(path) {
  const dir = mkdtempSync(join(tmpdir(), 'mu-trade-test-'));
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
      if (!r) return [];
      const t = f.getTableById(r.table);
      await t.readRecords();
      const rec = t.records[r.row];
      return Object.keys(rec.fields).sort((a, b) => Number(/\d+$/.exec(a)[0]) - Number(/\d+$/.exec(b)[0]))
        .map(k => decodeRef(rec[k])).map(d => (d ? players.records[d.row].PresentationId : null));
    };
    const packed = async bits => (await slots(bits)).filter(g => g !== null);
    const teams = {};
    for (const t of (await biggest('Team')).records.filter(r => !r.isEmpty && r.TeamIndex < 32)) {
      const dcRef = decodeRef(t.DepthChart);
      const dct = f.getTableById(dcRef.table);
      await dct.readRecords();
      const dc = dct.records[dcRef.row];
      const depth = {};
      for (const pos of Object.keys(dc.fields)) if (pos !== 'LockedEntries') depth[pos] = await slots(dc[pos]);
      const other = {};
      for (const k of ['DraftedPlayers', 'MarketedPlayers', 'TradePlayerBlackList', 'FocusTrainingList']) other[k] = await packed(t[k]);
      teams[t.TeamIndex] = { roster: await packed(t.Roster), ps: await packed(t.PracticeSquad), depth, other,
        counts: [t.ActiveRosterSize, t.SalCapRosterSize, t.SalCapNextYearRosterSize] };
    }
    const contractRef = new Map(players.records.filter(r => !r.isEmpty).map(r => [r.PresentationId, r.Contract]));
    return { teams, contractRef, draft: await readDraftTables(f) };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test('trades roster and practice-squad players and a pick between two teams', { skip, timeout: 600_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-trade-test-'));
  try {
    const a = await readSave(savePath);
    const x = await inspect(savePath);
    const NYG = 15, DAL = 10;
    const rowOf = i => x.draft.teams.find(t => t.team_index === i).row;
    // NYG's starting QB (on several depth-chart lines), a practice-squad player, and an unused NYG pick go to Dallas;
    // a Dallas roster player goes to New York.
    const qb = x.teams[NYG].depth.QB[0];
    const ps = x.teams[NYG].ps[0];
    const back = x.teams[DAL].roster.find(g => a.players.find(p => p.pgid === g).contract.length > 1);
    const pick = x.draft.picks.find(p => p.current_team_row === rowOf(NYG) && !p.selected);
    const edits = { trade: [
      { from_team_index: NYG, to_team_index: DAL, pgids: [qb, ps], picks: [{ tableId: x.draft.draft_pick_table_id, row: pick.row }] },
      { from_team_index: DAL, to_team_index: NYG, pgids: [back] },
    ] };
    // DAL's practice squad has room (9 of 12).
    const out = join(dir, 'CAREER-TRADED');
    const report = await writeSave(savePath, edits, out);
    assert.equal(report.verified, true);
    assert.deepEqual([report.trade.players, report.trade.roster, report.trade.practice_squad, report.trade.picks], [3, 2, 1, 1]);

    const y = await inspect(out);
    const [n0, n1, d0, d1] = [x.teams[NYG], y.teams[NYG], x.teams[DAL], y.teams[DAL]];
    assert.deepEqual(n1.roster, [...n0.roster.filter(g => g !== qb), back]);
    assert.deepEqual(d1.roster, [...d0.roster.filter(g => g !== back), qb]);
    assert.deepEqual(n1.ps, n0.ps.filter(g => g !== ps));
    assert.deepEqual(d1.ps, [...d0.ps, ps]);
    // Counts move with the roster players (both have deals past this year: QB check below).
    const nextYear = g => { const c = a.players.find(p => p.pgid === g).contract; return c.length - c.current_year > 1 ? 1 : 0; };
    assert.deepEqual(n1.counts, [n0.counts[0], n0.counts[1], n0.counts[2] - nextYear(qb) + nextYear(back)]);
    assert.deepEqual(d1.counts, [d0.counts[0], d0.counts[1], d0.counts[2] - nextYear(back) + nextYear(qb)]);
    // Old depth charts drop the players (later entries move up); the new team's chart is untouched.
    for (const [pos, list] of Object.entries(n0.depth)) {
      const without = list.filter(g => g !== qb);
      assert.deepEqual(n1.depth[pos], [...without, ...Array(list.length - without.length).fill(null)], `NYG ${pos}`);
    }
    assert.ok(Object.values(n0.depth).filter(l => l.includes(qb)).length >= 1);
    for (const [pos, list] of Object.entries(d0.depth)) {
      const without = list.filter(g => g !== back);
      assert.deepEqual(d1.depth[pos], [...without, ...Array(list.length - without.length).fill(null)], `DAL ${pos}`);
    }
    for (const [k, list] of Object.entries(n1.other)) assert.ok(!list.includes(qb) && !list.includes(ps), `NYG ${k}`);
    // Contracts travel by reference, unchanged.
    for (const g of [qb, ps, back]) assert.equal(y.contractRef.get(g), x.contractRef.get(g));
    // The pick changes hands; no other pick does.
    const pa = new Map(x.draft.picks.map(p => [p.row, p]));
    for (const p of y.draft.picks) {
      if (p.row === pick.row) assert.deepEqual([p.current_team_row, p.original_team_row], [rowOf(DAL), pick.original_team_row]);
      else assert.deepEqual(p, pa.get(p.row));
    }

    const b = await readSave(out);
    const after = new Map(b.players.map(p => [p.pgid, p]));
    const before = new Map(a.players.map(p => [p.pgid, p]));
    for (const [g, team] of [[qb, DAL], [ps, DAL], [back, NYG]]) {
      const p = after.get(g);
      assert.equal(p.team_index, team);
      assert.deepEqual({ ...p, team_index: 0 }, { ...before.get(g), team_index: 0 }, 'status and contract unchanged');
    }
    for (const p of a.players) if (![qb, ps, back].includes(p.pgid)) assert.deepEqual(after.get(p.pgid), p);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a trade that can\'t be done writes nothing', { skip, timeout: 300_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-trade-test-'));
  try {
    const x = await inspect(savePath);
    const NYG = 15, DAL = 10;
    const rowOf = i => x.draft.teams.find(t => t.team_index === i).row;
    const nygPlayer = x.teams[NYG].roster[0];
    const dalPick = x.draft.picks.find(p => p.current_team_row === rowOf(DAL) && !p.selected);
    const out = join(dir, 'CAREER-BAD');
    const bad = (trade, re) => assert.rejects(writeSave(savePath, { trade }, out), re);
    await bad([{ from_team_index: DAL, to_team_index: NYG, pgids: [nygPlayer] }], /not on DAL's roster/);
    await bad([{ from_team_index: NYG, to_team_index: NYG, pgids: [nygPlayer] }], /same team/);
    await bad([{ from_team_index: NYG, to_team_index: DAL, picks: [{ tableId: x.draft.draft_pick_table_id, row: dalPick.row }] }], /not NYG/);
    await bad([{ from_team_index: NYG, to_team_index: DAL, picks: [{ tableId: x.draft.team_table_id, row: 0 }] }], /not a DraftPick table/);
    await bad([{ from_team_index: NYG, to_team_index: DAL, pgids: [nygPlayer] }, { from_team_index: NYG, to_team_index: 3, pgids: [nygPlayer] }], /listed twice/);
    // NYG's practice squad is full (12): a practice-squad player can't go there.
    await bad([{ from_team_index: DAL, to_team_index: NYG, pgids: [x.teams[DAL].ps[0]] }], /NYG PracticeSquad: full/);
    // One bad player stops the good one too.
    await bad([{ from_team_index: NYG, to_team_index: DAL, pgids: [nygPlayer, 999999999] }], /0 players/);
    assert.equal(existsSync(out), false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
