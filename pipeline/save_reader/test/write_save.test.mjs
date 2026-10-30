// Writes edits into a copy of the sample save and reads it back. Skips without a sample save (they're git-ignored).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'crypto';
import { copyFileSync, existsSync, mkdtempSync, readFileSync, rmSync } from 'fs';
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
const ACHANE = 22692;
const hash = path => createHash('sha256').update(readFileSync(path)).digest('hex');

test('edits land on one player, everything else reads back unchanged', { skip, timeout: 300_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-write-test-'));
  try {
    const before = hash(savePath);
    const out = join(dir, 'CAREER-EDITED');
    const edits = { players: [{ pgid: ACHANE, set: {
      FirstName: 'Mutest', LastName: 'Teleporter', SpeedRating: 99, PLYR_COMMENT: 4600, PLYR_PORTRAIT: 1234,
      TraitDevelopment: 'XFactor', PlayerType: 'HB_PowerBack', IsInjuredReserve: true } }] };
    const report = await writeSave(savePath, edits, out);
    assert.deepEqual([report.players, report.fields, report.verified], [1, 8, true]);
    assert.equal(hash(savePath), before, 'the input save is untouched');

    const [a, b] = [await readSave(savePath), await readSave(out)];
    assert.deepEqual(b.season, a.season);
    assert.deepEqual(b.teams, a.teams);
    assert.deepEqual(b.games, a.games);
    const pa = new Map(a.players.map(p => [p.pgid, p]));
    const changed = b.players.filter(p => JSON.stringify(p) !== JSON.stringify(pa.get(p.pgid))).map(p => p.pgid);
    assert.deepEqual(changed, [ACHANE]);
    const x = b.players.find(p => p.pgid === ACHANE);
    assert.deepEqual([x.first_name, x.last_name, x.ratings.Speed, x.appearance.commentary_id, x.appearance.portrait_id,
      x.dev_trait, x.archetype, x.injury.injured_reserve],
    // XFactor and College_Elite are one stored value; the library reads back the first name.
    ['Mutest', 'Teleporter', 99, 4600, 1234, 'College_Elite', 'HB_PowerBack', true]);
    assert.equal(x.overall, pa.get(ACHANE).overall, 'the library does not recompute OVR');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a bad edit writes nothing', { skip, timeout: 300_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-write-test-'));
  try {
    const out = join(dir, 'CAREER-BAD');
    const bad = async (set, pgid = ACHANE) => assert.rejects(writeSave(savePath, { players: [{ pgid, set }] }, out));
    await bad({ SpeedRating: 500 });
    await bad({ FirstName: 'X'.repeat(40) });
    await bad({ Position: 'Quarterback' });
    await bad({ Contract: '0'.repeat(32) });
    await bad({ NotAField: 1 });
    await bad({ SpeedRating: 90 }, 999999999);
    await bad({ SpeedRating: 90, AgilityRating: 500 });       // one bad field stops the good one too
    assert.equal(existsSync(out), false);
    await assert.rejects(writeSave(savePath, { players: [] }, savePath), /must not be the input/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a contract block edits the player\'s own contract record in place', { skip, timeout: 300_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-write-test-'));
  try {
    const out = join(dir, 'CAREER-CONTRACT');
    const contract = { Length: 3, CurrentYear: 0, NoTradeClause: true, VoidYears: 0, SalaryDistribution: 'Custom' };
    [900, 1000, 1100, 0, 0, 0, 0, 0, 0, 0].forEach((v, i) => { contract[`SalaryYear${i}`] = v; });
    [200, 200, 200, 0, 0, 0, 0].forEach((v, i) => { contract[`BonusYear${i}`] = v; });
    const report = await writeSave(savePath, { players: [{ pgid: ACHANE, set: { SpeedRating: 98 }, contract }] }, out);
    assert.deepEqual([report.players, report.contracts, report.fields, report.verified], [1, 1, 23, true]);

    const [a, b] = [await readSave(savePath), await readSave(out)];
    const pa = new Map(a.players.map(p => [p.pgid, p]));
    const changed = b.players.filter(p => JSON.stringify(p) !== JSON.stringify(pa.get(p.pgid))).map(p => p.pgid);
    assert.deepEqual(changed, [ACHANE], 'no other player (or their contract) changed');
    const c = b.players.find(p => p.pgid === ACHANE).contract;
    assert.deepEqual([c.length, c.current_year, c.no_trade_clause, c.void_years], [3, 0, true, 0]);
    assert.deepEqual(c.salary_by_year, [900, 1000, 1100, 0, 0, 0, 0, 0, 0, 0]);
    assert.deepEqual(c.bonus_by_year, [200, 200, 200, 0, 0, 0, 0]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a bad contract edit writes nothing', { skip, timeout: 300_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-write-test-'));
  try {
    const out = join(dir, 'CAREER-BAD-CONTRACT');
    const practiceSquad = (await readSave(savePath)).players.find(p => p.status === 'PracticeSquad' && !p.contract);
    const bad = (contract, pgid = ACHANE, re = undefined) =>
      assert.rejects(writeSave(savePath, { players: [{ pgid, set: { SpeedRating: 98 }, contract }] }, out), re);
    await bad({ SalaryYear0: 20000 }, ACHANE, /above 16383/);
    await bad({ SalaryYear0: -1 });
    await bad({ BonusYear7: 10 }, ACHANE, /no such PlayerContract field/);
    await bad({ Incentives: '0'.repeat(32) }, ACHANE, /reference/);
    await bad({ Length: 2, CurrentYear: 2 }, ACHANE, /outside Length/);
    await bad({ CurrentYear: 5 }, ACHANE, /outside Length/);             // Achane's Length is 5
    await bad({ Length: 2 }, practiceSquad.pgid, /no PlayerContract record/);
    assert.equal(existsSync(out), false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// Each team's Roster, PracticeSquad and depth chart (pgids, null for an empty slot) and roster counts, plus the draft
// tables. Opens a copy, never the file itself.
async function lists(path) {
  const dir = mkdtempSync(join(tmpdir(), 'mu-write-test-'));
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
    const teams = {};
    for (const t of (await biggest('Team')).records.filter(r => !r.isEmpty && r.TeamIndex < 32)) {
      const dcRef = decodeRef(t.DepthChart);
      const dct = f.getTableById(dcRef.table);
      await dct.readRecords();
      const dc = dct.records[dcRef.row];
      const depth = {};
      for (const pos of Object.keys(dc.fields)) if (pos !== 'LockedEntries') depth[pos] = await slots(dc[pos]);
      teams[t.TeamIndex] = { roster: (await slots(t.Roster)).filter(Boolean), ps: (await slots(t.PracticeSquad)).filter(Boolean),
        depth, counts: [t.ActiveRosterSize, t.SalCapRosterSize] };
    }
    return { teams, draft: await readDraftTables(f) };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test('players, records, release, sign, trade and depthCharts in one write', { skip, timeout: 600_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-write-test-'));
  try {
    const a = await readSave(savePath);
    const x = await lists(savePath);
    const NYG = 15, DAL = 10;
    const rowOf = i => x.draft.teams.find(t => t.team_index === i).row;
    const cut = x.teams[NYG].roster.slice(-2);
    const fa = a.players.find(p => p.status === 'FreeAgent').pgid;
    const outgoing = x.teams[NYG].roster[5];
    const incoming = x.teams[DAL].roster[7];
    const pick = x.draft.picks.find(p => p.current_team_row === rowOf(DAL) && !p.selected);
    const prospect = x.draft.prospects.find(p => p.contract_status === 'Draft');
    const qbs = x.teams[NYG].depth.QB.filter(g => g !== null && !cut.includes(g) && g !== outgoing);
    const edits = {
      players: [{ pgid: ACHANE, set: { SpeedRating: 97 } }, { pgid: incoming, set: { AwarenessRating: 77 } }],
      records: [{ table: 'DraftPlayer', tableId: x.draft.draft_player_table_id, row: prospect.row, set: { InitialDraftRank: 3 } }],
      release: cut,
      sign: [{ pgid: fa, team_index: NYG, list: 'Roster', contract: { Length: 2, SalaryYear0: 90, SalaryYear1: 95 } }],
      trade: [{ from_team_index: NYG, to_team_index: DAL, pgids: [outgoing] },
        { from_team_index: DAL, to_team_index: NYG, pgids: [incoming], picks: [{ tableId: x.draft.draft_pick_table_id, row: pick.row }] }],
      // The signed and the traded-in player can be listed in the same write.
      depthCharts: [{ team_index: NYG, slots: { QB: [fa, ...qbs.slice(0, 2)], KR: [incoming] } }],
    };
    const out = join(dir, 'CAREER-MIXED');
    const report = await writeSave(savePath, edits, out);
    assert.equal(report.verified, true);
    assert.deepEqual([report.players, report.records, report.release.released, report.sign.signed, report.trade.players,
      report.trade.picks, report.depthCharts.positions], [2, 1, 2, 1, 2, 1, 2]);

    const y = await lists(out);
    const n0 = x.teams[NYG], n1 = y.teams[NYG];
    assert.deepEqual(n1.roster, [...n0.roster.filter(g => !cut.includes(g) && g !== outgoing), fa, incoming]);
    assert.deepEqual(n1.counts, [n0.counts[0] - 2, n0.counts[1] - 2].map(c => c + 1 - 1 + 1));
    assert.deepEqual(n1.depth.QB, [fa, ...qbs.slice(0, 2), null, null, null]);
    assert.deepEqual(n1.depth.KR, [incoming, null, null, null, null, null]);
    for (const list of Object.values(n1.depth)) for (const g of [...cut, outgoing]) assert.ok(!list.includes(g));
    assert.deepEqual(y.teams[DAL].roster, [...x.teams[DAL].roster.filter(g => g !== incoming), outgoing]);
    assert.equal(y.draft.picks.find(p => p.row === pick.row).current_team_row, rowOf(NYG));
    assert.equal(y.draft.prospects.find(p => p.row === prospect.row).initial_draft_rank, 3);

    const b = new Map((await readSave(out)).players.map(p => [p.pgid, p]));
    assert.equal(b.get(ACHANE).ratings.Speed, 97);
    assert.deepEqual([b.get(incoming).team_index, b.get(incoming).ratings.Awareness], [NYG, 77]);
    assert.deepEqual([b.get(fa).status, b.get(fa).team_index, b.get(fa).contract.salary_by_year.slice(0, 3)], ['Signed', NYG, [90, 95, 0]]);
    for (const g of cut) assert.deepEqual([b.get(g).status, b.get(g).contract], ['FreeAgent', null]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a player in two moves, or in a move and a conflicting players edit, writes nothing', { skip, timeout: 300_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-write-test-'));
  try {
    const x = await lists(savePath);
    const a = await readSave(savePath);
    const fa = a.players.find(p => p.status === 'FreeAgent').pgid;
    const nyg = x.teams[15].roster;
    const pick = x.draft.picks.find(p => p.current_team_row === x.draft.teams.find(t => t.team_index === 15).row && !p.selected);
    const out = join(dir, 'CAREER-BAD');
    const bad = (edits, re) => assert.rejects(writeSave(savePath, edits, out), re);
    const trade = pgids => [{ from_team_index: 15, to_team_index: 10, pgids }];
    await bad({ release: [nyg[0]], trade: trade([nyg[0]]) }, /in more than one move \(release, trade\)/);
    await bad({ release: [nyg[1]], sign: [{ pgid: fa, team_index: 15, list: 'Roster', contract: { Length: 1 } }], trade: trade([nyg[1]]) },
      /in more than one move/);
    await bad({ sign: [{ pgid: fa, team_index: 0, list: 'PracticeSquad' }], trade: [{ from_team_index: 0, to_team_index: 1, pgids: [fa] }] },
      /in more than one move \(sign, trade\)/);
    await bad({ players: [{ pgid: nyg[2], set: { TeamIndex: 3 } }], trade: trade([nyg[2]]) }, /sets TeamIndex, which the trade move sets/);
    await bad({ players: [{ pgid: fa, set: { ContractStatus: 'Signed' } }], sign: [{ pgid: fa, team_index: 0, list: 'PracticeSquad' }] },
      /sets ContractStatus, which the sign move sets/);
    await bad({ records: [{ table: 'DraftPick', tableId: x.draft.draft_pick_table_id, row: pick.row, set: { CurrentTeam: { tableId: x.draft.team_table_id, row: 3 } } }],
      trade: [{ from_team_index: 15, to_team_index: 10, picks: [{ tableId: x.draft.draft_pick_table_id, row: pick.row }] }] }, /edited in "records" and traded/);
    assert.equal(existsSync(out), false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// Plan risk R7: a league office stage writes every team's moves at once. 50 releases, 68 signings (50 reusing the
// released contracts), 50 one-player trades and 32 depth charts = 200 moves in one writeSave.
test('a 200-move write (timed)', { skip, timeout: 600_000 }, async t => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-write-test-'));
  try {
    const x = await lists(savePath);
    const a = await readSave(savePath);
    const teams = Object.keys(x.teams).map(Number).sort((p, q) => p - q);
    const release = [], sign = [], trade = [];
    const fas = a.players.filter(p => p.status === 'FreeAgent').map(p => p.pgid);
    for (const [n, i] of teams.slice(0, 25).entries()) {
      for (const k of [10, 11]) release.push(x.teams[i].roster[k]);
      for (const k of [0, 1]) sign.push({ pgid: fas[2 * n + k], team_index: i, list: 'Roster', contract: { Length: 2, SalaryYear0: 80, SalaryYear1: 85 } });
    }
    let next = 50;
    for (const i of teams) {
      for (let room = 12 - x.teams[i].ps.length; room > 0 && sign.length < 68; room--) sign.push({ pgid: fas[next++], team_index: i, list: 'PracticeSquad' });
    }
    for (let n = 0; n < 50; n++) {
      const from = teams[n % 32], to = teams[(n + 1) % 32];
      trade.push({ from_team_index: from, to_team_index: to, pgids: [x.teams[from].roster[20 + Math.floor(n / 32)]] });
    }
    // Rosters after the moves, for the depth charts (each team's KR line: the last three players on its roster).
    const roster = new Map(teams.map(i => [i, [...x.teams[i].roster]]));
    for (const g of release) for (const list of roster.values()) if (list.includes(g)) list.splice(list.indexOf(g), 1);
    for (const s of sign) if (s.list === 'Roster') roster.get(s.team_index).push(s.pgid);
    for (const tr of trade) {
      for (const g of tr.pgids) {
        const from = roster.get(tr.from_team_index);
        from.splice(from.indexOf(g), 1);
        roster.get(tr.to_team_index).push(g);
      }
    }
    const depthCharts = teams.map(i => ({ team_index: i, slots: { KR: roster.get(i).slice(-3) } }));
    const edits = { release, sign, trade, depthCharts };
    assert.equal(release.length + sign.length + trade.length + depthCharts.length, 200);

    const out = join(dir, 'CAREER-200');
    const start = process.hrtime.bigint();
    const report = await writeSave(savePath, edits, out);
    const ms = Number(process.hrtime.bigint() - start) / 1e6;
    t.diagnostic(`R7: 200-move writeSave (plan, apply, save, read back) took ${ms.toFixed(0)} ms`);
    assert.equal(report.verified, true);
    assert.deepEqual([report.release.released, report.sign.signed, report.trade.players, report.depthCharts.teams], [50, 68, 50, 32]);
    const y = await lists(out);
    for (const i of teams) {
      assert.deepEqual(y.teams[i].roster, roster.get(i), `team ${i} roster`);
      assert.deepEqual(y.teams[i].counts, [roster.get(i).length, roster.get(i).length]);
      assert.deepEqual(y.teams[i].depth.KR.filter(Boolean), roster.get(i).slice(-3));
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
