// Writes edits into a copy of the sample save and reads it back. Skips without a sample save (they're git-ignored).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'crypto';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
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
