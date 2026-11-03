// Reads a real save end to end. Uses $MU_SAMPLE_SAVE, else CAREER-GIANTS-AUTOSAVE at the repo root; skips if
// neither exists (save files are git-ignored, so a fresh checkout has none).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, statSync } from 'fs';
import { createHash } from 'crypto';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { readSave } from '../pc_save_reader.mjs';

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const savePath = process.env.MU_SAMPLE_SAVE || join(repoRoot, 'CAREER-GIANTS-AUTOSAVE');
const skip = existsSync(savePath) ? false : `no sample save at ${savePath}`;

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
  assert.ok(lines.length > 0, 'per-game stat lines present');
  for (const line of lines) assert.ok(gameRows.has(line.game_row), 'every game line points at an exported game');

  // reader_version 5: Madden's team ratings and cap fields on every real team (cap space = SalCapCapRoom - 160000, and
  // every team's cap works out to the same league cap: pipeline/load_save.py team_cap_values).
  const leagueCaps = new Set();
  for (const t of s.teams.filter(x => x.team_index < 32)) {
    const c = t.cap;
    assert.ok(c && Number.isInteger(c.SalCapCapRoom) && Number.isInteger(c.SalCapRosterReserve), `${t.short_name} cap fields`);
    leagueCaps.add(c.SalCapCapRoom - 160000 + c.SalCapRosterReserve + c.ThisYearCapPenalties - c.RolloverCap
      - c.ThisYearCapCredits);
  }
  assert.equal(leagueCaps.size, 1, 'one league cap');
  // reader_version 5: every future pick, wherever the save keeps it (one table, or one table per pick).
  const offsets = {};
  for (const p of s.draft_picks) offsets[p.year_offset] = (offsets[p.year_offset] || 0) + 1;
  assert.deepEqual(offsets, { 0: 224, 1: 224, 2: 224 }, 'three drafts of 224 picks');
  assert.ok(s.draft_picks.every(p => Number.isInteger(p.current_team_index) && p.round >= 1 && p.round <= 7));
  // reader_version 5: Madden's team ratings on every real team.
  assert.equal(s.meta.reader_version, 5);
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
