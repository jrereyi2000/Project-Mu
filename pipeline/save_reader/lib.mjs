// Pure helpers for pc_save_reader.mjs: no file access, no madden-franchise import, unit-tested in test/lib.test.mjs.

// A reference field is 32 bits: the first 15 are the table id, the last 17 the row index. All zeros is "no reference".
export function decodeRef(bits) {
  if (typeof bits !== 'string' || !/^[01]{32}$/.test(bits) || /^0+$/.test(bits)) return null;
  return { table: parseInt(bits.slice(0, 15), 2), row: parseInt(bits.slice(15), 2) };
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

// Game rows worth exporting: a real matchup (both teams set) that has been scheduled.
export const isExportableGame = game => game.home_team_index != null && game.away_team_index != null && game.status !== 'Invalid_';
