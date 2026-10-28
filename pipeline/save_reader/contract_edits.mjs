// Contract edits for save_writer.mjs: an edit's `contract` block sets fields of the player's own PlayerContract record,
// the one Player.Contract points at, in place. No record is created and no reference changes.
//
//   {"pgid": 22692, "set": {...}, "contract": {"Length": 4, "CurrentYear": 0, "SalaryYear0": 1050, "BonusYear0": 300}}
//
// Checked on CAREER-GIANTS-AUTOSAVE: every Signed player has a contract record and no two players share one (1,695
// references, all distinct; the only other reference into the table is one PersonaNegotiationPackage row, not a
// player's). Practice-squad players, free agents and draft prospects have none, so a contract edit on them is refused
// (giving one a record is a new record plus a reference change, out of scope here).
import { decodeRef, fieldMeta, fieldValueError } from './lib.mjs';

const CONTRACT_TABLE = 'PlayerContract';

// The PlayerContract record a player points at, or null.
export async function contractRecordOf(franchise, player) {
  const ref = decodeRef(player.Contract);
  if (!ref) return null;
  const table = franchise.getTableById(ref.table);
  if (!table || table.name !== CONTRACT_TABLE) return null;
  if (!table.records?.length) await table.readRecords();
  const record = table.records[ref.row];
  return record && !record.isEmpty ? record : null;
}

// Finds each contract edit's record and checks every value; returns { targets, errors } without changing anything.
// Targets have the shape of planEdits' with `contract: true`.
export async function planContractEdits(franchise, playerTable, edits) {
  const contractEdits = (edits.players ?? []).filter(e => e.contract && Object.keys(e.contract).length);
  const targets = [];
  const errors = [];
  if (!contractEdits.length) return { targets, errors };

  const players = playerTable.records.filter(r => !r.isEmpty);
  const holders = new Map();         // contract reference bits -> how many players point at it
  for (const r of players) if (decodeRef(r.Contract)) holders.set(r.Contract, (holders.get(r.Contract) ?? 0) + 1);

  for (const edit of contractEdits) {
    const hits = players.filter(r => r.PresentationId === edit.pgid);
    if (hits.length !== 1) continue;          // planEdits already reports it
    const player = hits[0];
    const record = await contractRecordOf(franchise, player);
    if (!record) {
      errors.push(`pgid ${edit.pgid}: no ${CONTRACT_TABLE} record (status ${player.ContractStatus})`);
      continue;
    }
    if (holders.get(player.Contract) !== 1) {
      errors.push(`pgid ${edit.pgid}: contract record shared by ${holders.get(player.Contract)} players`);
      continue;
    }
    let ok = true;
    for (const [key, value] of Object.entries(edit.contract)) {
      const meta = fieldMeta(record, key);
      const err = meta ? fieldValueError(meta, value) : `no such ${CONTRACT_TABLE} field`;
      if (err) {
        errors.push(`pgid ${edit.pgid} contract ${key} = ${JSON.stringify(value)}: ${err}`);
        ok = false;
      } else {
        targets.push({ pgid: edit.pgid, record, key, value, meta, contract: true });
      }
    }
    // Madden counts years 0..Length-1 with CurrentYear the one being played, so it must fall inside the deal.
    const length = edit.contract.Length ?? record.Length;
    const current = edit.contract.CurrentYear ?? record.CurrentYear;
    if (ok && !(length >= 1 && current < length)) {
      errors.push(`pgid ${edit.pgid} contract: CurrentYear ${current} outside Length ${length}`);
    }
  }
  return { targets, errors };
}
