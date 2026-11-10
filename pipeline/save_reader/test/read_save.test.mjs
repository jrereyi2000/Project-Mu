// Reads a real save end to end. Uses $MU_SAMPLE_SAVE, else CAREER-GIANTS-AUTOSAVE at the repo root; skips if
// neither exists (save files are git-ignored, so a fresh checkout has none). The coaches / staff tests below use an
// in-memory synthetic fixture and always run; opening a real save is never required for them.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, statSync } from 'fs';
import { createHash } from 'crypto';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { READER_VERSION, readCoaches, readSave, readStaff } from '../pc_save_reader.mjs';
import { encodeRef, NO_REF } from '../lib.mjs';

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const savePath = process.env.MU_SAMPLE_SAVE || join(repoRoot, 'CAREER-GIANTS-AUTOSAVE');
const skip = existsSync(savePath) ? false : `no sample save at ${savePath}`;

// A synthetic Coach table (4168's id, five rows: 3 team coaches, 1 free agent, 1 empty), Team table and reader cache,
// enough to exercise reader_version 8 without a save file.
const COACH_TABLE_ID = 4168;
const TEAM_TABLE_ID = 1;
const coachRecord = (over = {}) => ({
  isEmpty: false, PresentationId: 0, FirstName: '', LastName: '', Age: 0, Position: 'HeadCoach', TeamIndex: 0,
  ContractStatus: 'Signed', ContractLength: 0, ContractSalary: 0, ContractYearsRemaining: 0,
  Archetype: 'OffensiveGuru', Level: 1, Portrait: 0,
  OffensivePlaybook: NO_REF, DefensivePlaybook: NO_REF, OffensiveScheme: NO_REF, DefensiveScheme: NO_REF,
  IsUserControlled: false, ...over,
});
const teamRecord = (over = {}) => ({
  isEmpty: false, TeamIndex: 0, HeadCoach: NO_REF, OffensiveCoordinator: NO_REF, DefensiveCoordinator: NO_REF, ...over,
});

function syntheticCoaches() {
  return {
    header: { tableId: COACH_TABLE_ID },
    records: [
      coachRecord({
        PresentationId: 86, FirstName: 'Todd', LastName: 'Bowles', Age: 62, Position: 'HeadCoach', TeamIndex: 5,
        ContractStatus: 'Signed', ContractLength: 4, ContractSalary: 450, ContractYearsRemaining: 2,
        Archetype: 'DefensiveGenius', Level: 17, Portrait: 5,
        OffensivePlaybook: encodeRef(16384, 104463), DefensivePlaybook: encodeRef(16384, 104496),
        OffensiveScheme: encodeRef(16485, 57913), DefensiveScheme: encodeRef(16485, 57914),
        IsUserControlled: false,
      }),
      coachRecord({
        PresentationId: 120, FirstName: 'Synthetic', LastName: 'Coordinator', Age: 41,
        Position: 'OffensiveCoordinator', TeamIndex: 5, Archetype: 'OffensiveGuru', Level: 9, Portrait: 321,
      }),
      coachRecord({
        PresentationId: 130, FirstName: 'Synthetic', LastName: 'Defensive', Age: 47,
        Position: 'DefensiveCoordinator', TeamIndex: 5, Archetype: 'DefensiveGenius', Level: 12, Portrait: 322,
      }),
      coachRecord({
        PresentationId: 300, FirstName: 'Synthetic', LastName: 'FreeAgent', Age: 52, Position: 'HeadCoach',
        TeamIndex: 32, ContractStatus: 'FreeAgent', Archetype: 'DevelopmentWizard', Level: 4, Portrait: 363,
      }),
      coachRecord({ isEmpty: true, PresentationId: 999, FirstName: 'Not', LastName: 'Live' }),
    ],
  };
}

function syntheticTeams() {
  return {
    header: { tableId: TEAM_TABLE_ID },
    records: [
      teamRecord({
        TeamIndex: 0, HeadCoach: encodeRef(COACH_TABLE_ID, 0), OffensiveCoordinator: encodeRef(COACH_TABLE_ID, 1),
        DefensiveCoordinator: encodeRef(COACH_TABLE_ID, 2),
      }),
      // one empty coordinator seat
      teamRecord({
        TeamIndex: 1, HeadCoach: encodeRef(COACH_TABLE_ID, 0), OffensiveCoordinator: NO_REF,
        DefensiveCoordinator: encodeRef(COACH_TABLE_ID, 2),
      }),
      // an empty Coach row and a reference into a table that isn't Coach both read as null
      teamRecord({
        TeamIndex: 2, HeadCoach: encodeRef(COACH_TABLE_ID, 4), OffensiveCoordinator: encodeRef(99, 0),
        DefensiveCoordinator: NO_REF,
      }),
      // the free-agent pseudo-team is not a real team and gets no staff entry
      teamRecord({ TeamIndex: 32 }),
    ],
  };
}

test('coaches: every live Coach record, free agents included, in the reader_version 8 shape', () => {
  assert.equal(READER_VERSION, 8);
  const coaches = readCoaches(syntheticCoaches());
  assert.deepEqual(coaches.map(c => c.row), [0, 1, 2, 3], 'the empty row is skipped');
  assert.deepEqual(coaches.find(c => c.pgid === 86), {
    pgid: 86, table: COACH_TABLE_ID, row: 0, first_name: 'Todd', last_name: 'Bowles', age: 62,
    position: 'HeadCoach', team_index: 5, contract_status: 'Signed', contract_length: 4,
    contract_salary: 450, contract_years_remaining: 2, archetype: 'DefensiveGenius', level: 17,
    portrait: 5, offensive_playbook: encodeRef(16384, 104463), defensive_playbook: encodeRef(16384, 104496),
    offensive_scheme: encodeRef(16485, 57913), defensive_scheme: encodeRef(16485, 57914), is_user_controlled: false,
  });
  const freeAgent = coaches.find(c => c.pgid === 300);
  assert.ok(freeAgent, 'the TeamIndex 32 free agent is in coaches');
  assert.deepEqual([freeAgent.team_index, freeAgent.contract_status], [32, 'FreeAgent']);
  assert.ok(coaches.every(c => c.table === COACH_TABLE_ID));
});

test('staff: Team refs resolve to coach pgids and an empty seat is null', async () => {
  const coachTable = syntheticCoaches();
  const tables = { byId: async id => (id === COACH_TABLE_ID ? coachTable : null) };
  const staff = await readStaff(tables, syntheticTeams(), COACH_TABLE_ID);
  assert.deepEqual(staff, [
    { team_index: 0, hc: 86, oc: 120, dc: 130 },
    { team_index: 1, hc: 86, oc: null, dc: 130 },
    { team_index: 2, hc: null, oc: null, dc: null },
  ]);
});

test('reads a real Madden 27 save without changing it', { skip, timeout: 120_000 }, async () => {
  const hash = () => createHash('sha256').update(readFileSync(savePath)).digest('hex');
  const before = { hash: hash(), mtime: statSync(savePath).mtimeMs };
  const s = await readSave(savePath);
  assert.equal(hash(), before.hash);
  assert.equal(statSync(savePath).mtimeMs, before.mtime);

  assert.equal(s.meta.game_year, 27);
  assert.ok(Number.isInteger(s.franchise.league_id) && s.franchise.league_id !== 0, 'franchise league id');
  assert.ok(s.teams.length >= 32);
  assert.ok(s.games.length > 0);
  assert.ok(s.players.length > 1000);

  const pgids = s.players.map(p => p.pgid);
  assert.equal(new Set(pgids).size, pgids.length, 'pgid must be unique');

  const teamIndexes = new Set(s.teams.map(t => t.team_index));
  for (const g of s.games) {
    assert.ok(teamIndexes.has(g.home_team_index) && teamIndexes.has(g.away_team_index));
    assert.equal(g.home_quarters.length, 4);
    if (g.status === 'HomeWon' || g.status === 'AwayWon') {
      const sum = q => q.reduce((a, b) => a + b, 0);
      assert.equal(sum(g.home_quarters) + g.home_ot, g.home_score, `home quarters add up (game row ${g.row})`);
      assert.equal(sum(g.away_quarters) + g.away_ot, g.away_score, `away quarters add up (game row ${g.row})`);
      for (const side of ['home', 'away']) {
        const ts = g[`${side}_team_stats`];
        assert.ok(ts && Number.isInteger(ts.OFFYARDS), `${side} team box score (game row ${g.row})`);
      }
    }
  }

  const graded = s.players.filter(p => p.archetype_grades.some(g => g > 0));
  const atMax = graded.filter(p => p.overall === Math.max(...p.archetype_grades));
  assert.ok(atMax.length / graded.length > 0.99, 'OVR is the highest archetype grade');

  const gameRows = new Set(s.games.map(g => g.row));
  const lines = s.players.flatMap(p => p.game_stats);
  if (lines.length > 0) {
    for (const line of lines) assert.ok(gameRows.has(line.game_row), 'every game line points at an exported game');
  }

  // reader_version 8: every live coach and each real team's HC / OC / DC seats (asserted below); reader_version 7:
  // per-team roster / practice squad and the league free-agent list.
  assert.equal(s.meta.reader_version, 8);

  // reader_version 6: top-level salary_cap and decoded team cap blocks in $10k units
  assert.ok(s.salary_cap, 'salary_cap block present');
  assert.ok(Number.isInteger(s.salary_cap.table_id));
  assert.equal(s.salary_cap.row, 0);
  assert.ok(Number.isInteger(s.salary_cap.raw_team_salary_cap));
  assert.equal(s.salary_cap.team_salary_cap, s.salary_cap.raw_team_salary_cap + 12000);
  assert.ok(Number.isInteger(s.salary_cap.initial_salary_cap));
  assert.ok(s.salary_cap.increase_table, 'salary_cap increase_table present');

  const realTeams = s.teams.filter(x => x.team_index < 32);
  assert.equal(realTeams.length, 32);
  for (const t of realTeams) {
    const c = t.cap;
    assert.ok(c, `${t.short_name} cap block present`);
    for (const k of ['rollover', 'cap_credits', 'roster_reserve', 'cap_penalties', 'offers_reserve',
                     'rookie_reserve', 'fill_reserve', 'this_year_offer_reserve', 'cap_room',
                     'spending_money', 'next_year_cap_room', 'team_salary']) {
      assert.ok(Number.isInteger(c[k]), `${t.short_name} cap.${k} must be integer`);
    }
    const expectedRoom = (s.salary_cap.team_salary_cap + c.rollover + c.cap_credits)
      - (c.roster_reserve + c.cap_penalties + c.offers_reserve);
    assert.equal(c.cap_room, expectedRoom, `${t.short_name} cap room satisfies balance equation`);
  }

  // reader_version 5: every future pick, wherever the save keeps it (one table, or one table per pick).
  const offsets = {};
  for (const p of s.draft_picks) offsets[p.year_offset] = (offsets[p.year_offset] || 0) + 1;
  assert.deepEqual(offsets, { 0: 224, 1: 224, 2: 224 }, 'three drafts of 224 picks');
  assert.ok(s.draft_picks.every(p => Number.isInteger(p.current_team_index) && p.round >= 1 && p.round <= 7));
  // reader_version 5: Madden's team ratings on every real team.
  for (const t of s.teams.filter(x => x.team_index < 32)) {
    for (const k of ['ovr', 'off', 'def']) {
      assert.ok(Number.isInteger(t.ratings?.[k]) && t.ratings[k] > 0 && t.ratings[k] <= 99, `${t.short_name} ${k} rating`);
    }
  }

  // reader_version 5: the coming draft class, one board rank each, every entry a status Draft player.
  const prospects = new Set(s.players.filter(p => p.status === 'Draft').map(p => p.pgid));
  assert.ok(s.draft_class.length > 200, 'draft class read');
  for (const d of s.draft_class) assert.ok(prospects.has(d.pgid), `draft class pgid ${d.pgid} is a prospect`);
  const colleges = s.players.filter(p => p.college?.table === 16384);
  assert.ok(colleges.length / s.players.length > 0.9, 'most players point into the college list (table 16384)');
  const ranks = s.draft_class.map(d => d.initial_draft_rank);
  assert.equal(new Set(ranks).size, ranks.length, 'board ranks are unique');

  // reader_version 4: depth charts, league settings, PT_* flags and ConfidenceRating.
  assert.deepEqual(s.depth_charts.map(d => d.team_index), [...Array(32).keys()], 'one depth chart per real team');
  const pgidSet = new Set(pgids);
  let filled = 0;
  for (const d of s.depth_charts) {
    assert.equal(Object.keys(d.slots).length, 35, `team ${d.team_index}: 35 positions`);
    assert.ok(!('LockedEntries' in d.slots));
    for (const [pos, list] of Object.entries(d.slots)) {
      assert.equal(list.length, 6, `team ${d.team_index} ${pos}: 6 slots`);
      for (const pgid of list) {
        if (pgid === null) continue;
        filled++;
        assert.ok(pgidSet.has(pgid), `team ${d.team_index} ${pos}: pgid ${pgid} is an exported player`);
      }
    }
  }
  assert.ok(filled > 32 * 35, 'depth charts are mostly filled');
  for (const d of s.depth_charts) assert.ok(d.slots.QB[0] !== null, `team ${d.team_index} has a starting QB`);

  // reader_version 7: each real team's roster and practice squad, in list order, and the league free-agent list.
  const playerByPgid = new Map(s.players.map(p => [p.pgid, p]));
  for (const t of realTeams) {
    for (const key of ['roster', 'practice_squad']) {
      const list = t[key];
      assert.ok(Array.isArray(list), `${t.short_name} ${key} array`);
      assert.equal(new Set(list).size, list.length, `${t.short_name} ${key} has no duplicates`);
      for (const pgid of list) assert.ok(pgidSet.has(pgid), `${t.short_name} ${key} pgid ${pgid} is an exported player`);
    }
    assert.ok(t.roster.length > 0, `${t.short_name} roster is not empty`);
    // The snapshot's cap block carries SalCapRosterSize; Team.ActiveRosterSize (the size Madden raises on a signing)
    // equals it on every team of the sample save, so the roster list is checked against it where it is present.
    if (Number.isInteger(t.cap?.SalCapRosterSize)) {
      assert.equal(t.roster.length, t.cap.SalCapRosterSize, `${t.short_name} roster length matches its roster size`);
    }
  }
  assert.ok(Array.isArray(s.free_agents), 'free_agents array present');
  assert.equal(new Set(s.free_agents).size, s.free_agents.length, 'free_agents has no duplicates');
  const freeAgentPgids = new Set(s.free_agents);
  for (const pgid of s.free_agents) {
    const p = playerByPgid.get(pgid);
    assert.ok(p, `free-agent list pgid ${pgid} is an exported player`);
    // League.FreeAgents also lists every practice-squad player (sign.mjs header), so its real free agents are the
    // status FreeAgent players on TeamIndex 32; a practice-squad entry is on its own team.
    if (p.status === 'FreeAgent') assert.equal(p.team_index, 32, `free agent pgid ${pgid} is on team 32`);
    else assert.equal(p.status, 'PracticeSquad', `free-agent list pgid ${pgid} is a free agent or on a practice squad`);
  }
  assert.ok(s.players.filter(p => p.status === 'FreeAgent').every(p => freeAgentPgids.has(p.pgid)),
    'every snapshot free agent is in free_agents');

  // reader_version 8: every live Coach record (free agents on TeamIndex 32 included) and a full staff block.
  assert.ok(Array.isArray(s.coaches) && s.coaches.length > 0, 'coaches array present');
  const coachPgids = new Set(s.coaches.map(c => c.pgid));
  assert.equal(coachPgids.size, s.coaches.length, 'coach pgid must be unique');
  assert.ok(s.coaches.some(c => c.team_index === 32), 'free-agent coaches are included');
  for (const c of s.coaches) {
    assert.ok(Number.isInteger(c.table) && Number.isInteger(c.row), `coach ${c.pgid} table/row`);
    assert.equal(typeof c.first_name, 'string');
    assert.equal(typeof c.last_name, 'string');
    for (const k of ['age', 'position', 'team_index', 'contract_status', 'archetype', 'level', 'portrait']) {
      assert.ok(c[k] !== null && c[k] !== undefined, `coach ${c.pgid} ${k}`);
    }
    assert.equal(typeof c.is_user_controlled, 'boolean');
  }
  assert.deepEqual(s.staff.map(x => x.team_index), [...Array(32).keys()], 'one staff entry per real team');
  assert.ok(s.staff.some(seat => seat.hc !== null), 'some head-coach seats are filled');
  for (const seat of s.staff) {
    for (const role of ['hc', 'oc', 'dc']) {
      if (seat[role] === null) continue;
      assert.ok(coachPgids.has(seat[role]), `team ${seat.team_index} ${role} is an exported coach`);
    }
  }

  const ls = s.league_settings;
  assert.equal(typeof ls.dev_trait_regression_enabled, 'boolean');
  assert.equal(typeof ls.breakout_scenarios_enabled, 'boolean');
  assert.ok(Number.isInteger(ls.league_setting.table_id) && ls.league_setting.row === 0);
  if (!process.env.MU_SAMPLE_SAVE || /CAREER-GIANTS-AUTOSAVE$/.test(savePath)) {
    // CAREER-GIANTS-AUTOSAVE (PROGRESSION_PLAN.md §2): LeagueSetting 4290, int[] 5949 rows 0/1, ProgressionXPSlider 4317 rows 0/1.
    assert.deepEqual(ls.league_setting, { table_id: 4290, row: 0 });
    // Spike R3: IsDevTraitRegressionEnabled = Field_88, IsDevTraitIncreaseScenarioEnabled = Field_86 under schema 620.
    assert.equal(ls.dev_trait_regression_field, 'Field_88');
    assert.equal(ls.breakout_scenarios_field, 'Field_86');
    const where = k => [ls.sliders[k].table_id, ls.sliders[k].row];
    assert.deepEqual(where('progression_by_age'), [5949, 0]);
    assert.deepEqual(where('progression_by_position'), [4317, 0]);
    assert.deepEqual(where('regression_by_age'), [5949, 1]);
    assert.deepEqual(where('regression_by_position'), [4317, 1]);
    assert.equal(ls.sliders.progression_by_position.values.LS, 100);
    assert.equal(ls.sliders.regression_by_position.values.K, 50);
    assert.equal(ls.sliders.regression_by_age.values.int9, 110);
  }
  for (const k of ['progression_by_age', 'progression_by_position', 'regression_by_age', 'regression_by_position']) {
    assert.ok(ls.sliders[k] && Object.keys(ls.sliders[k].values).length > 0, `${k} slider found`);
  }

  for (const p of s.players) {
    assert.ok(Number.isInteger(p.confidence), `pgid ${p.pgid} confidence`);
    assert.ok(Object.keys(p.traits).length > 50 && Object.keys(p.traits).every(k => k.startsWith('PT_')));
    assert.ok(Object.values(p.traits).every(v => typeof v === 'boolean'));
  }
  assert.ok(s.players.some(p => p.traits.PT_DISCIPLINED), 'some players carry PT_DISCIPLINED');
});
