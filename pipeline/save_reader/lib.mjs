// Pure helpers for pc_save_reader.mjs: no file access, no madden-franchise import, unit-tested in test/lib.test.mjs.

// A reference field is 32 bits: the first 15 are the table id, the last 17 the row index. All zeros is "no reference".
export function decodeRef(bits) {
  if (typeof bits !== 'string' || !/^[01]{32}$/.test(bits) || /^0+$/.test(bits)) return null;
  return { table: parseInt(bits.slice(0, 15), 2), row: parseInt(bits.slice(15), 2) };
}

export const NO_REF = '0'.repeat(32);

// The inverse of decodeRef (release.mjs writes references into Player[] arrays).
export function encodeRef(table, row) {
  if (!Number.isInteger(table) || table < 1 || table >= 2 ** 15 || !Number.isInteger(row) || row < 0 || row >= 2 ** 17) {
    throw new Error(`not a reference: table ${table}, row ${row}`);
  }
  return table.toString(2).padStart(15, '0') + row.toString(2).padStart(17, '0');
}

// An array record's slot fields (Player0, Player1, ..., Player99) in index order; Object.keys order isn't promised.
export function arraySlots(fieldNames) {
  return fieldNames
    .map(k => [k, /(\d+)$/.exec(k)])
    .filter(([, m]) => m)
    .sort((a, b) => Number(a[1][1]) - Number(b[1][1]))
    .map(([k]) => k);
}

// Madden packs arrays like Team.Roster from slot 0 with no gaps (checked on every team in the sample save). Given the
// slot values in order, returns the refs and whether they are packed.
export function packedRefs(values) {
  const refs = values.map(decodeRef);
  const n = refs.findIndex(r => !r);
  const count = n === -1 ? refs.length : n;
  return { refs: refs.slice(0, count), packed: refs.slice(count).every(r => !r) };
}

// Slot values with every reference `drop(ref)` picks taken out and the later slots moved up, empty slots at the end.
// Holes elsewhere stay where they are relative to the other entries (5 of 1,120 depth-chart lists in the sample save
// have one, e.g. NYJ QB: starter, backup, empty, third).
export function withoutRefs(values, drop) {
  const kept = values.filter(v => { const r = decodeRef(v); return !(r && drop(r)); });
  return [...kept, ...Array(values.length - kept.length).fill(NO_REF)];
}

// Slot values for `refs` packed from slot 0 into `slots` slots, the rest empty.
export function packRefs(refs, slots) {
  if (refs.length > slots) throw new Error(`${refs.length} references don't fit ${slots} slots`);
  return Array.from({ length: slots }, (_, i) => (i < refs.length ? encodeRef(refs[i].table, refs[i].row) : NO_REF));
}

export function isRefValue(value) {
  return typeof value === 'string' && /^[01]{32}$/.test(value);
}

// Madden stores weight as pounds - 160 (De'Von Achane: raw 28 = 188 lbs).
export const WEIGHT_OFFSET = 160;
export const weightLbs = raw => (Number.isFinite(raw) ? raw + WEIGHT_OFFSET : null);

// SeasonGame.SeasonYear and stat SEAS_YEAR count franchise years from 0; BaseCalendarYear is year 0's calendar year.
export const calendarYear = (baseCalendarYear, franchiseYear) => baseCalendarYear + franchiseYear;

// Current ratings only: `<Name>Rating` without the `Original` copies and without the non-numeric/meta ones.
const NOT_RATINGS = new Set(['OverallRating', 'PersonalityRating', 'RunningStyleRating', 'ConfidenceRating']);
export function pickRatings(record, fieldNames) {
  const out = {};
  for (const k of fieldNames) {
    if (!k.endsWith('Rating') || k.startsWith('Original') || NOT_RATINGS.has(k)) continue;
    const v = record[k];
    if (typeof v === 'number') out[k.slice(0, -'Rating'.length)] = v;
  }
  return out;
}

// Every non-reference field of a record as a plain object (stat rows, contracts).
export function plainFields(record, fieldNames, skip = []) {
  const out = {};
  for (const k of fieldNames) {
    if (skip.includes(k)) continue;
    const v = record[k];
    if (isRefValue(v)) continue;
    out[k] = v;
  }
  return out;
}

// Numbered fields (SalaryYear0..9, HomeScoreQuarter1..4) as an array in index order.
export function numberedFields(record, fieldNames, prefix) {
  const rx = new RegExp(`^${prefix}(\\d+)$`);
  return fieldNames
    .map(k => [k, rx.exec(k)])
    .filter(([, m]) => m)
    .sort((a, b) => Number(a[1][1]) - Number(b[1][1]))
    .map(([k]) => record[k]);
}

// The non-empty references held by an array-table record (e.g. Player.GameStats → GameStats[] row).
export function arrayRefs(record, fieldNames) {
  return fieldNames.map(k => decodeRef(record[k])).filter(Boolean);
}

// Stat tables a player's GameStats / SeasonStats references can land in, keyed to a short kind.
export const STAT_KINDS = {
  GameOffensiveStats: 'offense',
  GameDefensiveStats: 'defense',
  GameKickingStats: 'kicking',
  GameOLineStats: 'oline',
  GameOffensiveKPReturnStats: 'off_return',
  GameDefensiveKPReturnStats: 'def_return',
  SeasonOffensiveStats: 'offense',
  SeasonDefensiveStats: 'defense',
  SeasonKickingStats: 'kicking',
  SeasonOLineStats: 'oline',
  SeasonOffensiveKPReturnStats: 'off_return',
  SeasonDefensiveKPReturnStats: 'def_return',
};

// A field's schema as fieldValueError reads it, or null when the record has no such field.
export function fieldMeta(record, key) {
  const field = record.getFieldByKey(key);
  if (!field) return null;
  const o = field.offset;
  return {
    type: o.type, isReference: o.isReference, minValue: o.minValue, maxValue: o.maxValue, maxLength: o.maxLength,
    enumMembers: o.enum ? o.enum.members.map(m => m.name) : null,
    // Some enums give two names one stored value (TraitDevelopment: College_Elite = XFactor = 3), and the library
    // reads back the first, so the read-back compares stored values.
    enumValues: o.enum ? Object.fromEntries(o.enum.members.map(m => [m.name, m._value])) : null,
  };
}

// Checks a value against a field's schema before save_writer.mjs sets it: the library rejects bad enum values but
// silently stores out-of-range numbers (Speed 500) and over-long strings. Returns an error message, or null.
// meta: { type, isReference, minValue, maxValue, maxLength, enumMembers }.
export function fieldValueError(meta, value) {
  if (meta.isReference) return 'reference fields are not writable here';
  if (meta.enumMembers) {
    const usable = meta.enumMembers.filter(m => !m.endsWith('_'));
    return usable.includes(value) ? null : `not one of ${usable.slice(0, 8).join(', ')}${usable.length > 8 ? ', …' : ''}`;
  }
  if (meta.type === 'string') {
    if (typeof value !== 'string') return 'must be a string';
    return meta.maxLength && value.length > meta.maxLength ? `longer than ${meta.maxLength} characters` : null;
  }
  if (meta.type === 'bool') return typeof value === 'boolean' ? null : 'must be true or false';
  if (meta.type === 'int' || meta.type === 'uint' || meta.type === 's_int') {
    if (!Number.isInteger(value)) return 'must be an integer';
    if (Number.isFinite(meta.minValue) && value < meta.minValue) return `below ${meta.minValue}`;
    if (Number.isFinite(meta.maxValue) && value > meta.maxValue) return `above ${meta.maxValue}`;
    return null;
  }
  return `field type ${meta.type} is not writable here`;
}

// Game rows worth exporting: a real matchup (both teams set) that has been scheduled.
export const isExportableGame = game => game.home_team_index != null && game.away_team_index != null && game.status !== 'Invalid_';
