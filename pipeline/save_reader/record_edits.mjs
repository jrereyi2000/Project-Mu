// Edits to table records other than Player, for save_writer.mjs's `records` list (the teleporter's draft picks and
// draft-class rows, pipeline/teleport_draft.py):
//
//   {"records": [{"table": "DraftPick", "tableId": 4540, "row": 17, "set": {"CurrentTeam": {"tableId": 5934, "row": 3}}},
//                {"table": "DraftPlayer", "tableId": 4135, "row": 1, "set": {"DraftPosition": "OLB"}}]}
//
// A save holds several tables of one name (three DraftPick tables: a 1-row default, the last draft's 224 selections and
// the 672 future picks), so an edit names the table by id and by name, and both must agree (draft_tables.mjs prints
// both). Plain fields are checked like Player fields (lib.mjs fieldValueError). A reference field is written only when
// its type is in WRITABLE_REFERENCES (Team: pick ownership), the value is {tableId, row}, that table has the field's
// type as its name, the row is a non-empty record, and the field already points into that same table (or nowhere): it
// re-points an existing link and never creates records.
import { decodeRef, fieldValueError } from './lib.mjs';

export const WRITABLE_REFERENCES = new Set(['Team']);

// 15-bit table id + 17-bit row (lib.mjs decodeRef's inverse).
export function encodeRef(tableId, row) {
  if (!Number.isInteger(tableId) || tableId <= 0 || tableId >= 2 ** 15) throw new Error(`bad table id ${tableId}`);
  if (!Number.isInteger(row) || row < 0 || row >= 2 ** 17) throw new Error(`bad row ${row}`);
  return tableId.toString(2).padStart(15, '0') + row.toString(2).padStart(17, '0');
}

function fieldMeta(record, key) {
  const field = record.getFieldByKey(key);
  if (!field) return null;
  const o = field.offset;
  return {
    type: o.type, isReference: o.isReference, minValue: o.minValue, maxValue: o.maxValue, maxLength: o.maxLength,
    enumMembers: o.enum ? o.enum.members.map(m => m.name) : null,
    enumValues: o.enum ? Object.fromEntries(o.enum.members.map(m => [m.name, m._value])) : null,
  };
}

// Why a reference value can't be written, or null. `tables`: Map tableId -> table (records read).
export function referenceError(meta, current, value, tables) {
  if (!WRITABLE_REFERENCES.has(meta.type)) return `${meta.type} references are not writable`;
  if (!value || typeof value !== 'object' || !Number.isInteger(value.tableId) || !Number.isInteger(value.row)) {
    return 'a reference value is {"tableId": N, "row": N}';
  }
  const target = tables.get(value.tableId);
  if (!target || target.name !== meta.type) return `table ${value.tableId} is not a ${meta.type} table`;
  if (value.row < 0 || value.row >= target.records.length || target.records[value.row].isEmpty) {
    return `${meta.type} row ${value.row} is not a record`;
  }
  const now = decodeRef(current);
  if (now && now.table !== value.tableId) return `the field points into table ${now.table}, not ${value.tableId}`;
  return null;
}

async function tableMap(franchise, ids) {
  const out = new Map();
  for (const id of ids) {
    const t = franchise.getTableById(id);
    if (!t) continue;
    await t.readRecords();
    out.set(id, t);
  }
  return out;
}

// Finds each edit's record and checks every value; returns { targets, errors } without changing anything.
export async function planRecordEdits(franchise, edits = []) {
  const ids = new Set(edits.map(e => e.tableId));
  for (const e of edits) for (const v of Object.values(e.set ?? {})) if (v && typeof v === 'object') ids.add(v.tableId);
  const tables = await tableMap(franchise, [...ids].filter(Number.isInteger));
  const targets = [];
  const errors = [];
  for (const edit of edits) {
    const where = `${edit.table}[${edit.tableId}] row ${edit.row}`;
    const table = tables.get(edit.tableId);
    if (!table || table.name !== edit.table) {
      errors.push(`${where}: no ${edit.table} table with that id`);
      continue;
    }
    if (edit.table === 'Player') {
      errors.push(`${where}: Player edits go in "players" (by pgid)`);
      continue;
    }
    const record = table.records[edit.row];
    if (!Number.isInteger(edit.row) || !record || record.isEmpty) {
      errors.push(`${where}: not a record`);
      continue;
    }
    for (const [key, value] of Object.entries(edit.set ?? {})) {
      const meta = fieldMeta(record, key);
      let err;
      if (!meta) err = `no such ${edit.table} field`;
      else if (meta.isReference) err = referenceError(meta, record[key], value, tables);
      else err = fieldValueError(meta, value);
      if (err) errors.push(`${where} ${key} = ${JSON.stringify(value)}: ${err}`);
      else targets.push({ where, tableId: edit.tableId, row: edit.row, record, key, meta,
        value: meta.isReference ? encodeRef(value.tableId, value.row) : value });
    }
  }
  return { targets, errors };
}

export function applyRecordEdits(targets) {
  for (const t of targets) t.record[t.key] = t.value;
}

// Compares every edited field in a re-opened save; returns the mismatches.
export async function verifyRecordEdits(franchise, targets) {
  const tables = await tableMap(franchise, [...new Set(targets.map(t => t.tableId))]);
  const mismatches = [];
  for (const t of targets) {
    const r = tables.get(t.tableId)?.records[t.row];
    const stored = v => (t.meta.enumValues ? t.meta.enumValues[v] : v);
    if (!r || stored(r[t.key]) !== stored(t.value)) mismatches.push(`${t.where} ${t.key}: wrote ${t.value}, read ${r?.[t.key]}`);
  }
  return mismatches;
}
