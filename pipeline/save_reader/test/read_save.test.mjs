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
});
