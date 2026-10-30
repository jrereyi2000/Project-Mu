import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  arraySlots, arrayRefs, calendarYear, decodeRef, depthSlotPgids, encodeRef, fieldValueError, isExportableGame, isRefValue, NO_REF,
  numberedFields, orderSliderRefs, packedRefs, packRefs, pickRatings, plainFields, playerTraits, refFromValue, weightLbs,
  withoutRefs,
} from '../lib.mjs';

test('decodeRef splits 15 table bits and 17 row bits', () => {
  // Table 4164 (SeasonGame in CAREER-GIANTS-AUTOSAVE), row 226.
  assert.deepEqual(decodeRef('00100000100010000000000011100010'), { table: 4164, row: 226 });
});

test('decodeRef treats all zeros and non-references as no reference', () => {
  assert.equal(decodeRef('0'.repeat(32)), null);
  assert.equal(decodeRef('HB'), null);
  assert.equal(decodeRef(12), null);
  assert.equal(decodeRef('0101'), null);
});

test('isRefValue only matches 32-bit strings', () => {
  assert.equal(isRefValue('0'.repeat(32)), true);
  assert.equal(isRefValue('1'.repeat(31)), false);
  assert.equal(isRefValue(5), false);
});

test('weightLbs adds the 160 lb offset', () => {
  assert.equal(weightLbs(28), 188);
  assert.equal(weightLbs(undefined), null);
});

test('calendarYear counts franchise years from the base year', () => {
  assert.equal(calendarYear(2026, 0), 2026);
  assert.equal(calendarYear(2026, 1), 2027);
});

test('pickRatings keeps current numeric ratings and drops originals and meta ratings', () => {
  const rec = { SpeedRating: 96, OriginalSpeedRating: 95, OverallRating: 96, RunningStyleRating: 'Default', PersonalityRating: 50, AwarenessRating: 80 };
  assert.deepEqual(pickRatings(rec, Object.keys(rec)), { Speed: 96, Awareness: 80 });
});

test('plainFields drops references and skipped fields', () => {
  const rec = { SeasonGame: '0'.repeat(31) + '1', PASSYARDS: 264, DOWNSPLAYED: 65, OpposingTeam: '1'.repeat(32) };
  assert.deepEqual(plainFields(rec, Object.keys(rec), ['DOWNSPLAYED']), { PASSYARDS: 264 });
});

test('numberedFields orders by the number, not by key order', () => {
  const rec = { SalaryYear2: 30, SalaryYear0: 10, SalaryYear10: 99, SalaryYear1: 20, BonusYear0: 1 };
  assert.deepEqual(numberedFields(rec, Object.keys(rec), 'SalaryYear'), [10, 20, 30, 99]);
});

test('arrayRefs keeps only set references', () => {
  const rec = { GameStats0: '00100000000100100000110110000110', GameStats1: '0'.repeat(32) };
  assert.deepEqual(arrayRefs(rec, Object.keys(rec)), [decodeRef(rec.GameStats0)]);
});

test('isExportableGame needs both teams and a real status', () => {
  assert.equal(isExportableGame({ home_team_index: 1, away_team_index: 2, status: 'Unplayed' }), true);
  assert.equal(isExportableGame({ home_team_index: null, away_team_index: 2, status: 'HomeWon' }), false);
  assert.equal(isExportableGame({ home_team_index: 1, away_team_index: 2, status: 'Invalid_' }), false);
});

test('fieldValueError enforces the ranges and lengths the library lets through', () => {
  const speed = { type: 'int', minValue: 0, maxValue: 127 };
  assert.equal(fieldValueError(speed, 99), null);
  assert.match(fieldValueError(speed, 500), /above 127/);
  assert.match(fieldValueError(speed, -1), /below 0/);
  assert.match(fieldValueError(speed, 9.5), /integer/);
  const name = { type: 'string', maxLength: 17 };
  assert.equal(fieldValueError(name, 'Mu'), null);
  assert.match(fieldValueError(name, 'X'.repeat(18)), /longer than 17/);
  assert.match(fieldValueError(name, 5), /string/);
  assert.equal(fieldValueError({ type: 'bool' }, true), null);
  assert.match(fieldValueError({ type: 'bool' }, 'yes'), /true or false/);
});

test('fieldValueError checks enums, hides sentinel members and refuses references', () => {
  const dev = { type: 'TraitDevelopment', enumMembers: ['First_', 'Normal', 'Star', 'XFactor', 'Invalid_'] };
  assert.equal(fieldValueError(dev, 'Star'), null);
  assert.match(fieldValueError(dev, 'Invalid_'), /not one of Normal, Star, XFactor/);
  assert.match(fieldValueError({ type: 'PlayerContract', isReference: true }, '0'.repeat(32)), /reference/);
  assert.match(fieldValueError({ type: 'float' }, 1.5), /not writable/);
});

test('encodeRef is the inverse of decodeRef', () => {
  assert.equal(encodeRef(4164, 226), '00100000100010000000000011100010');
  assert.deepEqual(decodeRef(encodeRef(4236, 3086)), { table: 4236, row: 3086 });
  assert.throws(() => encodeRef(0, 1));
  assert.throws(() => encodeRef(4236, 2 ** 17));
});

test('arraySlots orders Player0..N by number', () => {
  assert.deepEqual(arraySlots(['Player10', 'Player2', 'Player0', 'Player1']), ['Player0', 'Player1', 'Player2', 'Player10']);
});

test('packedRefs, packRefs and withoutRefs keep slot order', () => {
  const [a, b, c] = [encodeRef(4236, 1), encodeRef(4236, 2), encodeRef(4236, 3)];
  assert.deepEqual(packedRefs([a, b, NO_REF]), { refs: [decodeRef(a), decodeRef(b)], packed: true });
  assert.equal(packedRefs([a, NO_REF, c]).packed, false);
  assert.deepEqual(packRefs([decodeRef(c), decodeRef(a)], 3), [c, a, NO_REF]);
  assert.throws(() => packRefs([decodeRef(a), decodeRef(b)], 1));
  const isB = r => r.row === 2;
  assert.deepEqual(withoutRefs([a, b, c, NO_REF], isB), [a, c, NO_REF, NO_REF]);
  // A hole keeps its place among the other entries (NYJ QB: starter, backup, empty, third).
  assert.deepEqual(withoutRefs([b, a, NO_REF, c], isB), [a, NO_REF, c, NO_REF]);
  assert.deepEqual(withoutRefs([a, c], isB), [a, c]);
});

test('refFromValue reads a reference stored as bits or as a generic 32-bit int', () => {
  // LeagueSetting.Field_165 in CAREER-GIANTS-AUTOSAVE: 779747328 = int[] (table 5949) row 0.
  assert.deepEqual(refFromValue(779747328), { table: 5949, row: 0 });
  assert.deepEqual(refFromValue(565837825), { table: 4317, row: 1 });
  assert.deepEqual(refFromValue(encodeRef(4164, 226)), { table: 4164, row: 226 });
  assert.equal(refFromValue(0), null);
  assert.equal(refFromValue(45), null, 'table id 0 is no reference');
  assert.equal(refFromValue(1.5), null);
  assert.equal(refFromValue(NO_REF), null);
});

test('depthSlotPgids maps a 6-slot Player[] row to pgids, holes kept, best first', () => {
  const pgidOfRow = row => ({ 1: 22686, 2: 9123, 3: 501 })[row];
  const values = [encodeRef(4236, 1), encodeRef(4236, 2), NO_REF, encodeRef(4236, 3), encodeRef(9999, 1), encodeRef(4236, 77)];
  assert.deepEqual(depthSlotPgids(values, 4236, pgidOfRow), [22686, 9123, null, 501, null, null]);
  assert.deepEqual(depthSlotPgids([encodeRef(4236, 2)], 4236, pgidOfRow), [9123, null, null, null, null, null]);
});

test('orderSliderRefs pairs the int[] and ProgressionXPSlider refs, lower field = progression', () => {
  const c = (field, table, row, tableName) => ({ field, table, row, tableName });
  const got = orderSliderRefs([
    c('Field_72', 4141, 0, 'GeneratorAdjustor'), c('Field_168', 4317, 1, 'ProgressionXPSlider'),
    c('Field_167', 5949, 1, 'int[]'), c('Field_166', 4317, 0, 'ProgressionXPSlider'), c('Field_165', 5949, 0, 'int[]'),
  ]);
  assert.equal(got.progression_by_age.field, 'Field_165');
  assert.equal(got.progression_by_position.field, 'Field_166');
  assert.equal(got.regression_by_age.field, 'Field_167');
  assert.deepEqual([got.regression_by_position.table, got.regression_by_position.row], [4317, 1]);
  const short = orderSliderRefs([c('Field_165', 5949, 0, 'int[]'), c('Field_166', 4317, 0, 'ProgressionXPSlider')]);
  assert.deepEqual(short, { progression_by_age: null, progression_by_position: null, regression_by_age: null, regression_by_position: null });
});

test('playerTraits keeps every PT_ flag as a boolean', () => {
  const rec = { PT_HAPPYFEET: true, PT_SHOWBOAT: 0, SpeedRating: 90 };
  assert.deepEqual(playerTraits(rec, ['PT_HAPPYFEET', 'SpeedRating', 'PT_SHOWBOAT']), { PT_HAPPYFEET: true, PT_SHOWBOAT: false });
});
