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
import { moveConflicts, writeSave } from '../save_writer.mjs';

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const savePath = process.env.MU_SAMPLE_SAVE || join(repoRoot, 'CAREER-GIANTS-AUTOSAVE');
const skip = existsSync(savePath) ? false : `no sample save at ${savePath}`;
const ACHANE = 22692;
const hash = path => createHash('sha256').update(readFileSync(path)).digest('hex');

// moveConflicts is pure: it decides from the edits and the save's current teams, without opening the save.
test('moveConflicts accepts exactly a declared switch and refuses every other double move', () => {
  const current = new Map([[100, 3]]);
  const contract = { Length: 1, SalaryYear0: 70 };
  const sign = (team, extra = {}) => [{ pgid: 100, team_index: team, list: 'Roster', contract, ...extra }];
  // A release plus a flagged sign to a different team is the one allowed pair.
  assert.deepEqual(moveConflicts({ release: [100], sign: sign(4, { switch: true }) }, current), []);
  // Without the flag the same pgid in both moves is the existing error.
  assert.deepEqual(moveConflicts({ release: [100], sign: sign(4) }, current),
    ['pgid 100: in more than one move (release, sign)']);
  // The flag never stands alone: a switch sign with no release is refused.
  assert.match(moveConflicts({ sign: sign(4, { switch: true }) }, current).join('\n'),
    /pgid 100: switch sign is not in release/);
  // A flagged sign to the player's current team is a promotion, not a switch.
  assert.match(moveConflicts({ release: [100], sign: sign(3, { switch: true }) }, current).join('\n'),
    /pgid 100: switch sign is to the current team \(3\)/);
  // Every other overlap is still refused.
  assert.deepEqual(moveConflicts({ release: [100], trade: [{ from_team_index: 3, to_team_index: 4, pgids: [100] }] }, current),
    ['pgid 100: in more than one move (release, trade)']);
  assert.deepEqual(moveConflicts({ release: [100], sign: sign(4, { switch: true }),
    trade: [{ from_team_index: 3, to_team_index: 4, pgids: [100] }] }, current),
  ['pgid 100: in more than one move (release, sign, trade)']);
  assert.match(moveConflicts({ release: [100, 100], sign: sign(4, { switch: true }) }, current).join('\n'),
    /a switch needs exactly one release and one sign \(2 release, 1 sign\)/);
});

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

// A PlayerContract row's terms (the fields the writer sets), read from an open franchise.
async function contractTerms(f, ref) {
  const t = f.getTableById(ref.table);
  await t.readRecords();
  const c = t.records[ref.row];
  return { table: ref.table, row: ref.row, length: c.Length, current_year: c.CurrentYear,
    salary_by_year: Array.from({ length: c.Length }, (_, i) => c[`SalaryYear${i}`]),
    bonus_by_year: Array.from({ length: c.Length }, (_, i) => c[`BonusYear${i}`]) };
}

// A pgid's contract ref and terms in `path`, plus `old` (the terms at `oldRef`, when given) and how many Player rows
// point at `oldRef`. Opens a copy; the save itself is never opened by the library.
async function contractState(path, pgid, oldRef = null) {
  const dir = mkdtempSync(join(tmpdir(), 'mu-write-test-'));
  try {
    copyFileSync(path, join(dir, 'save'));
    const f = await Franchise.create(join(dir, 'save'), { autoParse: true, saveOnChange: false });
    const all = f.getAllTablesByName('Player');
    for (const t of all) await t.readRecords();
    const players = all.reduce((a, b) => (!a || b.records.length > a.records.length ? b : a), null);
    const row = players.records.find(r => !r.isEmpty && r.PresentationId === pgid);
    const ref = row ? decodeRef(row.Contract) : null;
    const refs = r => { const d = r.isEmpty ? null : decodeRef(r.Contract); return d; };
    return {
      ref,
      terms: ref ? await contractTerms(f, ref) : null,
      old: oldRef ? await contractTerms(f, oldRef) : null,
      oldHolders: oldRef ? players.records.filter(r => { const d = refs(r); return d && d.table === oldRef.table && d.row === oldRef.row; }).length : null,
    };
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
        { from_team_index: DAL, to_team_index: NYG, pgids: [incoming], picks: [{ tableId: pick.table_id, row: pick.row }] }],
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

test('a declared switch writes a release and a signing to a new team in one write', { skip, timeout: 600_000 }, async t => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-write-test-'));
  try {
    const x = await lists(savePath);
    const a = await readSave(savePath);
    const roster = x.teams;
    const teams = Object.keys(roster).map(Number).sort((p, q) => p - q);
    // A rostered non-starter (bench end) with a contract, not on IR (which would skew the roster counts).
    const signed = new Map(a.players.filter(p => p.status === 'Signed' && p.team_index < 32 && p.contract &&
      !p.injury?.injured_reserve).map(p => [p.pgid, p]));
    let from, pgid;
    for (const i of teams) {
      for (const g of [...roster[i].roster].reverse()) if (signed.has(g)) { from = i; pgid = g; break; }
      if (pgid !== undefined) break;
    }
    if (pgid === undefined) { t.skip('sample save has no rostered non-starter with a contract'); return; }
    const to = teams.find(i => i !== from);
    const before = hash(savePath);
    const oldRef = (await contractState(savePath, pgid)).ref;
    assert.ok(oldRef, 'the chosen player has a PlayerContract row');

    // A league-minimum-like deal: one year, $700k ($10,000 units), small enough for any team.
    const contract = { Length: 1, SalaryYear0: 70 };
    // The old deal alone: a release with no signing. Its dead-cap outcome is the row the release leaves behind.
    const releaseOut = join(dir, 'CAREER-RELEASE-ONLY');
    const released = await writeSave(savePath, { release: [pgid] }, releaseOut);
    assert.deepEqual([released.release.released, released.switches.length], [1, 0]);

    const out = join(dir, 'CAREER-SWITCH');
    const report = await writeSave(savePath,
      { release: [pgid], sign: [{ pgid, team_index: to, list: 'Roster', contract, switch: true }] }, out);
    assert.equal(report.verified, true);
    assert.equal(hash(savePath), before, 'the input save is untouched');
    assert.deepEqual(report.switches.map(s => [s.pgid, s.from_team_index, s.to_team_index]),
      [[pgid, from, to]], 'the report names the switch');
    assert.deepEqual([report.release.released, report.sign.signed], [1, 1]);

    // The same player pair is refused without the flag, and a flagged sign back to his current team is not a switch.
    // Neither attempt writes anything.
    const refused = join(dir, 'CAREER-REFUSED-SWITCH');
    await assert.rejects(writeSave(savePath,
      { release: [pgid], sign: [{ pgid, team_index: to, list: 'Roster', contract }] }, refused),
    /in more than one move \(release, sign\)/);
    await assert.rejects(writeSave(savePath,
      { release: [pgid], sign: [{ pgid, team_index: from, list: 'Roster', contract, switch: true }] }, refused),
    /switch sign is to the current team/);
    await assert.rejects(writeSave(savePath,
      { sign: [{ pgid, team_index: to, list: 'Roster', contract, switch: true }] }, refused),
    /switch sign is not in release/);
    assert.equal(existsSync(refused), false);

    const y = await lists(out);
    assert.ok(!y.teams[from].roster.includes(pgid), 'the old team roster dropped the switch');
    assert.ok(y.teams[to].roster.includes(pgid), 'new team roster gained the switch');
    assert.equal(y.teams[from].roster.length, roster[from].roster.length - 1);
    assert.equal(y.teams[to].roster.length, roster[to].roster.length + 1);
    assert.deepEqual(y.teams[from].counts, [roster[from].counts[0] - 1, roster[from].counts[1] - 1],
      'the old team has one fewer rostered player');
    assert.deepEqual(y.teams[to].counts, [roster[to].counts[0] + 1, roster[to].counts[1] + 1],
      'the new team has one more');
    const b = (await readSave(out)).players.find(p => p.pgid === pgid);
    assert.deepEqual([b.status, b.team_index], ['Signed', to]);
    assert.deepEqual([b.contract.length, b.contract.current_year, b.contract.salary_by_year.slice(0, 2)], [1, 0, [70, 0]]);

    // The signing takes its own contract row and the old deal stays exactly as a release alone leaves it
    // (unreferenced: the save writer leaves cap totals, and so dead money, to Madden).
    const switched = await contractState(out, pgid, oldRef);
    const alone = await contractState(releaseOut, pgid, oldRef);
    assert.notDeepEqual(switched.ref, oldRef, 'the new contract is a different PlayerContract row');
    assert.deepEqual(switched.terms, { table: oldRef.table, row: switched.ref.row, length: 1, current_year: 0,
      salary_by_year: [70], bonus_by_year: [0] });
    assert.deepEqual(switched.old, alone.old, 'the old deal reads the same as in a release-only write');
    assert.deepEqual(switched.old, (await contractState(savePath, pgid)).terms, 'the old deal keeps its original terms');
    assert.deepEqual([switched.oldHolders, alone.oldHolders], [0, 0], 'the old deal is left unreferenced');
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
    await bad({ records: [{ table: 'DraftPick', tableId: pick.table_id, row: pick.row, set: { CurrentTeam: { tableId: x.draft.team_table_id, row: 3 } } }],
      trade: [{ from_team_index: 15, to_team_index: 10, picks: [{ tableId: pick.table_id, row: pick.row }] }] }, /edited in "records" and traded/);
    assert.equal(existsSync(out), false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('the staff block runs before release in one write and the report lists the new PresentationId',
  { skip, timeout: 600_000 }, async t => {
    const dir = mkdtempSync(join(tmpdir(), 'mu-write-test-'));
    try {
      const a = await readSave(savePath);
      const free = a.coaches.find(c => c.team_index === 32 && c.contract_status === 'FreeAgent');
      const filled = a.staff.find(s => s.team_index < 32 && s.hc !== null && s.oc !== null);
      const open = a.staff.find(s => s.team_index < 32 && s.hc !== null && s.oc === null);
      if (!free || !filled || !open) {
        t.skip('sample save has no free-agent coach, full staff or open coordinator seat');
        return;
      }
      // A template coach with the sub-records a create clones, and an empty Coach row to fill.
      const inspectDir = mkdtempSync(join(dir, 'inspect-'));
      copyFileSync(savePath, join(inspectDir, 'save'));
      const f = await Franchise.create(join(inspectDir, 'save'), { autoParse: true, saveOnChange: false });
      const coachTables = f.getAllTablesByName('Coach');
      for (const table of coachTables) await table.readRecords();
      const coachTable = coachTables.reduce((best, x) => (!best || x.records.length > best.records.length ? x : best), null);
      const template = coachTable?.records.find(r => !r.isEmpty && decodeRef(r.StaffTalents) && decodeRef(r.CharacterVisuals));
      const hasEmpty = coachTable?.records.some(r => r.isEmpty);
      if (!template || !hasEmpty) { t.skip('no template coach with talents/visuals, or no empty Coach row'); return; }

      const x = await lists(savePath);
      const cut = (x.teams[filled.team_index]?.roster ?? []).slice(-2);
      if (cut.length < 2) { t.skip('no bench players to release'); return; }
      const profile = {
        first_name: 'Mu', last_name: 'Testcoach', age: 45, position: 'OffensiveCoordinator',
        archetype: 'OffensiveGuru', specialty: 'Quarterbacks', level: 3, years_coaching: 5,
        offensive_playbook: 'Panthers', defensive_playbook: 'Panthers',
        offensive_scheme: 'WestCoastZoneRun', defensive_scheme: 'Tampa2', personality: 'TeamPlayer',
        demeanor: 'Classic', body_type: 'Standard', height: 72, weight: 10, face_shape: 'Invalid_',
        generic_head: 'coachhead_M_0001_HS',
        talents: [{ slot: 'Gameday', talent: 'Field General', tier: 1 },
          { slot: 'Playsheet', talent: 'West Coast Playsheet', tier: 1 }],
        ratings: { COACH_QB: 70, COACH_RATING: 62 }, tendencies: { COACH_OFFTENDENCYRUNPASS: 55 },
        visuals_template: template.PresentationId, portrait_id: 0,
      };
      const contract = { ContractLength: 3, ContractSalary: 450, ContractYearsRemaining: 3 };
      const edits = {
        release: cut,
        staff: [
          { action: 'release', pgid: filled.oc },
          { action: 'assign', pgid: free.pgid, team_index: filled.team_index, position: 'OffensiveCoordinator', contract },
          { action: 'create', key: 'coach-test', team_index: open.team_index, position: 'OffensiveCoordinator',
            profile, contract },
        ],
      };
      const before = hash(savePath);
      const out = join(dir, 'CAREER-STAFF');
      const report = await writeSave(savePath, edits, out);
      assert.equal(hash(savePath), before, 'the input save is untouched');
      assert.equal(report.verified, true);
      assert.deepEqual([report.staff.released, report.staff.assigned, report.staff.created, report.release.released],
        [1, 1, 1, 2]);
      assert.equal(report.staff.presentation_ids.length, 1, 'the report carries the new PresentationId');
      assert.equal(report.staff.presentation_ids[0].key, 'coach-test');

      const b = await readSave(out);
      assert.equal(b.staff.find(s => s.team_index === filled.team_index).oc, free.pgid,
        'the free agent moved into the released seat');
      const created = report.staff.presentation_ids[0];
      assert.equal(b.staff.find(s => s.team_index === open.team_index).oc, created.presentation_id,
        'the created coach fills the open seat');
      const newCoach = b.coaches.find(c => c.pgid === created.presentation_id);
      assert.ok(newCoach, 'the created coach is a live Coach record');
      assert.deepEqual([newCoach.team_index, newCoach.position, newCoach.contract_status, newCoach.portrait],
        [open.team_index, 'OffensiveCoordinator', 'Signed', 0], 'no portrait yet: the silhouette path');
      const released = b.coaches.find(c => c.pgid === filled.oc);
      assert.deepEqual([released.team_index, released.contract_status], [32, 'FreeAgent']);
      const afterPlayers = new Map(b.players.map(p => [p.pgid, p]));
      for (const pgid of cut) assert.equal(afterPlayers.get(pgid).status, 'FreeAgent');
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
      // Both counts move with the net roster change. Compare the change, not counts == list length: a sample save with
      // IR players can carry an ActiveRosterSize that does not count every Roster row (CAREER-MUBASE does).
      const delta = roster.get(i).length - x.teams[i].roster.length;
      assert.deepEqual(y.teams[i].counts.map((c, k) => c - x.teams[i].counts[k]), [delta, delta], `team ${i} counts`);
      assert.deepEqual(y.teams[i].depth.KR.filter(Boolean), roster.get(i).slice(-3));
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
