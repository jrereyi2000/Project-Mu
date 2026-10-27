import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  arrayRefs, calendarYear, decodeRef, isExportableGame, isRefValue, numberedFields, pickRatings, plainFields, weightLbs,
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
