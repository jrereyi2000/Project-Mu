// Visual edits for save_writer.mjs: an edit's `visuals` block sets CharacterVisuals.RawData fields
// (specifically body skinTone 1-8 inside the zstd-compressed ISON document).
//
//   {"pgid": 22692, "set": {...}, "visuals": {"skinTone": 7}}
//
// Background & Assets:
//   - Body tint is skinTone (1-8) inside CharacterVisuals.RawData.
//   - RawData is compressed with a 20KB zstd dictionary (dict.bin) and stored in Table3.
//   - Each player's Player.CharacterVisuals points to their CharacterVisuals record.
//   - If a CharacterVisuals record is shared by multiple players, copy-on-write allocates an empty
//     record in CharacterVisuals, copies the existing data, and re-points the player's reference.
import { spawnSync } from 'child_process';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { decodeRef, encodeRef } from './lib.mjs';

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
const CV_TABLE = 'CharacterVisuals';

/**
 * Runs Python pipeline.character_visuals batch command to process raw buffers.
 */
export function runPythonBatch(subcommand, payload) {
  const proc = spawnSync('python3', ['-m', 'pipeline.character_visuals', subcommand], {
    input: JSON.stringify(payload),
    encoding: 'utf8',
    cwd: REPO_ROOT,
    maxBuffer: 100 * 1024 * 1024,
  });
  if (proc.error) throw proc.error;
  if (proc.status !== 0) {
    throw new Error(`python character_visuals ${subcommand} failed (status ${proc.status}): ${proc.stderr}`);
  }
  return JSON.parse(proc.stdout);
}

/**
 * Resolves the CharacterVisuals record a player points at, or null.
 */
export async function visualRecordOf(franchise, player) {
  const ref = decodeRef(player.CharacterVisuals);
  if (!ref) return null;
  const table = franchise.getTableById(ref.table);
  if (!table || table.name !== CV_TABLE) return null;
  if (!table.records?.length) await table.readRecords();
  const record = table.records[ref.row];
  return record && !record.isEmpty ? record : null;
}

/**
 * Finds each visual edit's record, validates schema (skinTone 1-8), handles copy-on-write
 * if CharacterVisuals is shared, and computes new zstd-compressed RawData buffers.
 * Returns { targets, errors } without changing anything in the franchise.
 */
export async function planVisualEdits(franchise, playerTable, edits) {
  const visualEdits = (edits.players ?? []).filter(
    e => e.visuals && (e.visuals.skinTone !== undefined || e.visuals.skin_tone !== undefined)
  );
  const targets = [];
  const errors = [];
  if (!visualEdits.length) return { targets, errors };

  const cvTable = franchise.getTableByName(CV_TABLE);
  if (!cvTable) {
    errors.push(`no ${CV_TABLE} table in franchise`);
    return { targets, errors };
  }
  if (!cvTable.records?.length) await cvTable.readRecords();

  const players = playerTable.records.filter(r => !r.isEmpty);
  const byPgid = new Map();
  for (const p of players) {
    const list = byPgid.get(p.PresentationId) ?? [];
    list.push(p);
    byPgid.set(p.PresentationId, list);
  }

  // Count references to detect shared CharacterVisuals records
  const holders = new Map();
  for (const r of players) {
    if (r.CharacterVisuals && decodeRef(r.CharacterVisuals)) {
      holders.set(r.CharacterVisuals, (holders.get(r.CharacterVisuals) ?? 0) + 1);
    }
  }

  const batchRequests = [];
  const candidateTargets = [];

  for (const edit of visualEdits) {
    const hits = byPgid.get(edit.pgid) ?? [];
    if (hits.length !== 1) continue; // planEdits reports missing/ambiguous pgid
    const player = hits[0];

    const tone = edit.visuals.skinTone !== undefined ? edit.visuals.skinTone : edit.visuals.skin_tone;
    if (!Number.isInteger(tone) || tone < 1 || tone > 8) {
      errors.push(`pgid ${edit.pgid} visuals skinTone = ${JSON.stringify(tone)}: must be an integer 1..8`);
      continue;
    }

    const cvRef = decodeRef(player.CharacterVisuals);
    if (!cvRef) {
      errors.push(`pgid ${edit.pgid}: no ${CV_TABLE} reference`);
      continue;
    }

    let record = cvTable.records[cvRef.row];
    if (!record || record.isEmpty) {
      errors.push(`pgid ${edit.pgid}: ${CV_TABLE} row ${cvRef.row} is not a record`);
      continue;
    }

    let newRef = null;
    // Copy-on-write if shared by more than 1 player
    if ((holders.get(player.CharacterVisuals) ?? 0) > 1) {
      const freeRecord = cvTable.records.find(r => r.isEmpty);
      if (!freeRecord) {
        errors.push(`pgid ${edit.pgid}: no free ${CV_TABLE} record for copy-on-write`);
        continue;
      }
      freeRecord.isEmpty = false;
      record._data.copy(freeRecord._data);
      if (record.fields.RawData?.thirdTableField) {
        freeRecord.fields.RawData.thirdTableField.unformattedValue = Buffer.from(
          record.fields.RawData.thirdTableField.unformattedValue
        );
      }
      if (record.fields.Overflow) {
        freeRecord.fields.Overflow.value = record.fields.Overflow.value;
      }
      newRef = encodeRef(cvTable.header.tableId, freeRecord.index);
      holders.set(player.CharacterVisuals, holders.get(player.CharacterVisuals) - 1);
      holders.set(newRef, 1);
      record = freeRecord;
    }

    const t3 = record.fields.RawData?.thirdTableField;
    if (!t3 || !t3.unformattedValue) {
      errors.push(`pgid ${edit.pgid}: ${CV_TABLE} record has no RawData Table3 field`);
      continue;
    }

    const reqId = batchRequests.length;
    batchRequests.push({
      id: reqId,
      raw_hex: t3.unformattedValue.toString('hex'),
      skinTone: tone,
    });
    candidateTargets.push({
      reqId,
      pgid: edit.pgid,
      player,
      cvRecord: record,
      key: 'RawData',
      newRef,
      skinTone: tone,
      visual: true,
    });
  }

  if (errors.length || !batchRequests.length) {
    return { targets, errors };
  }

  // Batch compute the updated compressed buffers in Python
  try {
    const results = runPythonBatch('batch', batchRequests);
    const byReqId = new Map(results.map(r => [r.id, r]));
    for (const cand of candidateTargets) {
      const res = byReqId.get(cand.reqId);
      if (!res || !res.new_raw_hex) {
        errors.push(`pgid ${cand.pgid}: failed to encode CharacterVisuals RawData`);
      } else {
        cand.value = Buffer.from(res.new_raw_hex, 'hex');
        targets.push(cand);
      }
    }
  } catch (err) {
    errors.push(`CharacterVisuals batch processing error: ${err.message}`);
  }

  return { targets, errors };
}

/**
 * Applies visual targets to their respective CharacterVisuals records.
 */
export function applyVisualEdits(targets) {
  for (const t of targets) {
    if (t.newRef) {
      t.player.CharacterVisuals = t.newRef;
    }
    const t3 = t.cvRecord.fields.RawData?.thirdTableField;
    if (t3) {
      if (t.value.length > t3.maxLength + 2) {
        const mainData = t3.populateOverflowRecord(t.value);
        t3.unformattedValue = mainData;
      } else {
        t3.clearOverflowRecordIfExists();
        t3.unformattedValue = t.value;
      }
    }
  }
}

/**
 * Reads back each edited CharacterVisuals record from checkFranchise and verifies
 * that the stored skinTone matches the expected value.
 */
export async function verifyVisualEdits(checkFranchise, targets) {
  if (!targets?.length) return [];
  const cvTable = checkFranchise.getTableByName(CV_TABLE);
  if (!cvTable) return ['no CharacterVisuals table in written save'];
  if (!cvTable.records?.length) await cvTable.readRecords();

  const playerTable = checkFranchise.getTableByName('Player');
  if (!playerTable.records?.length) await playerTable.readRecords();

  const byPgid = new Map();
  for (const p of playerTable.records) {
    if (!p.isEmpty) byPgid.set(p.PresentationId, p);
  }

  const readRequests = [];
  const items = [];

  for (let i = 0; i < targets.length; i++) {
    const t = targets[i];
    const player = byPgid.get(t.pgid);
    if (!player) {
      items.push({ error: `pgid ${t.pgid}: player not found on read-back` });
      continue;
    }
    const ref = decodeRef(player.CharacterVisuals);
    if (!ref) {
      items.push({ error: `pgid ${t.pgid}: no CharacterVisuals reference on read-back` });
      continue;
    }
    const rec = cvTable.records[ref.row];
    if (!rec || rec.isEmpty) {
      items.push({ error: `pgid ${t.pgid}: CharacterVisuals record ${ref.row} missing on read-back` });
      continue;
    }
    const rawBuf = rec.fields.RawData?.thirdTableField?.unformattedValue;
    if (!rawBuf) {
      items.push({ error: `pgid ${t.pgid}: RawData missing on read-back` });
      continue;
    }
    readRequests.push({ id: i, raw_hex: rawBuf.toString('hex') });
    items.push({ target: t });
  }

  const mismatches = [];
  for (const it of items) {
    if (it.error) mismatches.push(it.error);
  }

  if (readRequests.length) {
    const results = runPythonBatch('read-batch', readRequests);
    const byId = new Map(results.map(r => [r.id, r]));
    for (let i = 0; i < items.length; i++) {
      if (items[i].error) continue;
      const t = items[i].target;
      const res = byId.get(i);
      const readTone = res?.skinTone;
      if (readTone !== t.skinTone) {
        mismatches.push(
          `pgid ${t.pgid} CharacterVisuals skinTone: wrote ${t.skinTone}, read back ${readTone}`
        );
      }
    }
  }

  return mismatches;
}
