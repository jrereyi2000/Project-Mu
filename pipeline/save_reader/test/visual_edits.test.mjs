// Tests for visual_edits.mjs: CharacterVisuals.RawData skinTone edits and copy-on-write
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import Franchise from 'madden-franchise';
import { decodeRef } from '../lib.mjs';
import { writeSave } from '../save_writer.mjs';
import { planVisualEdits, runPythonBatch, visualRecordOf } from '../visual_edits.mjs';

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const savePath = process.env.MU_SAMPLE_SAVE || join(repoRoot, 'CAREER-GIANTS-AUTOSAVE');
const skip = existsSync(savePath) ? false : `no sample save at ${savePath}`;
const ACHANE = 22692;

test('runPythonBatch executes python helper cleanly', () => {
  const res = runPythonBatch('batch', []);
  assert.deepEqual(res, []);
});

test('visualRecordOf resolves CharacterVisuals record', { skip }, async () => {
  const franchise = await Franchise.create(savePath, { autoParse: true, saveOnChange: false });
  const playerTable = franchise.getTableByName('Player');
  await playerTable.readRecords();
  const player = playerTable.records.find(p => !p.isEmpty && p.PresentationId === ACHANE);
  assert.ok(player);
  const cvRec = await visualRecordOf(franchise, player);
  assert.ok(cvRec);
  assert.equal(cvRec.isEmpty, false);
});

test('planVisualEdits validates skinTone schema and rejects out of bounds values', { skip }, async () => {
  const franchise = await Franchise.create(savePath, { autoParse: true, saveOnChange: false });
  const playerTable = franchise.getTableByName('Player');
  await playerTable.readRecords();

  const badEdits = [
    { players: [{ pgid: ACHANE, visuals: { skinTone: 0 } }] },
    { players: [{ pgid: ACHANE, visuals: { skinTone: 9 } }] },
    { players: [{ pgid: ACHANE, visuals: { skinTone: 'medium' } }] },
    { players: [{ pgid: ACHANE, visuals: { skinTone: 3.5 } }] },
  ];

  for (const edit of badEdits) {
    const { targets, errors } = await planVisualEdits(franchise, playerTable, edit);
    assert.equal(targets.length, 0);
    assert.equal(errors.length, 1);
    assert.match(errors[0], /must be an integer 1\.\.8/);
  }
});

test('visuals block edits skinTone and reads back verified', { skip, timeout: 300_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'mu-visuals-test-'));
  try {
    const out = join(dir, 'CAREER-VISUALS');
    const edits = {
      players: [
        {
          pgid: ACHANE,
          set: { SpeedRating: 97 },
          visuals: { skinTone: 3 },
        },
      ],
    };
    const report = await writeSave(savePath, edits, out);
    assert.equal(report.verified, true);
    assert.equal(report.visuals, 1);

    // Read back and verify CharacterVisuals RawData skinTone
    const check = await Franchise.create(out, { autoParse: true, saveOnChange: false });
    const playerTable = check.getTableByName('Player');
    await playerTable.readRecords();
    const player = playerTable.records.find(p => !p.isEmpty && p.PresentationId === ACHANE);
    const cvRec = await visualRecordOf(check, player);
    assert.ok(cvRec);
    const rawBuf = cvRec.fields.RawData.thirdTableField.unformattedValue;
    const res = runPythonBatch('read-batch', [{ id: 0, raw_hex: rawBuf.toString('hex') }]);
    assert.equal(res[0].skinTone, 3);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('copy-on-write handles shared CharacterVisuals records', { skip }, async () => {
  const franchise = await Franchise.create(savePath, { autoParse: true, saveOnChange: false });
  const playerTable = franchise.getTableByName('Player');
  await playerTable.readRecords();

  const activePlayers = playerTable.records.filter(p => !p.isEmpty);
  const p1 = activePlayers[0];
  const p2 = activePlayers[1];

  // Artificially make p2 point to p1's CharacterVisuals record to simulate a shared record
  const originalRef = p1.CharacterVisuals;
  p2.CharacterVisuals = originalRef;

  const edits = {
    players: [
      {
        pgid: p2.PresentationId,
        visuals: { skinTone: 5 },
      },
    ],
  };

  const { targets, errors } = await planVisualEdits(franchise, playerTable, edits);
  assert.equal(errors.length, 0);
  assert.equal(targets.length, 1);
  const target = targets[0];
  assert.ok(target.newRef, 'copy-on-write must assign a new reference');
  assert.notEqual(target.newRef, originalRef, 'new reference must differ from shared reference');
  assert.notEqual(target.cvRecord.index, decodeRef(originalRef).row, 'target record must be a new row');
});
