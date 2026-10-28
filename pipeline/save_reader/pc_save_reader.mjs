#!/usr/bin/env node
// Reads a Madden 27 PC franchise save into one JSON snapshot for Project Mu (docs/PC_MIGRATION_PLAN.md §5.1).
//
//   node pc_save_reader.mjs <CAREER-save> [--out snapshot.json]
//
// The save is copied to a temp file and only the copy is opened, so Madden's file is never touched (and the
// library's save-on-change can't write to it). Players are keyed by `pgid` = Player.PresentationId, unique per
// player in the save (3,086 of 3,086 in CAREER-GIANTS-AUTOSAVE); `row` is the Player table row that references use.
// Per-game stat rows are reached through Player.GameStats (an array of references to the Game*Stats tables, each
// row pointing at its SeasonGame), season rows through Player.SeasonStats. Older weeks are purged by Madden, so
// the loader must read every week (§5.3).
import { copyFileSync, mkdtempSync, rmSync, statSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { basename, join, resolve } from 'path';
import { fileURLToPath } from 'url';
import Franchise from 'madden-franchise';
import {
  arrayRefs, calendarYear, decodeRef, isExportableGame, numberedFields, pickRatings, plainFields, STAT_KINDS, weightLbs,
} from './lib.mjs';

export const READER_VERSION = 2;

const STAT_REF_FIELDS = ['SeasonGame', 'OpposingTeam'];

async function openCopy(savePath) {
  const dir = mkdtempSync(join(tmpdir(), 'mu-save-'));
  const copy = join(dir, basename(savePath));
  copyFileSync(savePath, copy);
  const franchise = await Franchise.create(copy, { autoParse: true, saveOnChange: false });
  return { franchise, cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

// Reads tables lazily and caches them by id, so following references doesn't re-read a table.
function tableReader(franchise) {
  const byId = new Map();
  const load = async table => {
    if (!table) return null;
    if (!byId.has(table.header.tableId)) {
      await table.readRecords();
      byId.set(table.header.tableId, table);
    }
    return byId.get(table.header.tableId);
  };
  return {
    // Some names have several tables (Team: a 1-row one and the 35-row league table), so take the one with the
    // most records.
    byName: async name => {
      const all = await Promise.all(franchise.getAllTablesByName(name).map(load));
      return all.reduce((best, t) => (!best || t.records.length > best.records.length ? t : best), null);
    },
    byId: id => load(franchise.getTableById(id)),
  };
}

const fieldNames = record => Object.keys(record.fields);

function readSeason(info) {
  return {
    calendar_year: info.CurrentSeasonYear,
    franchise_year: info.CurrentYear,
    base_calendar_year: info.BaseCalendarYear,
    week_index: info.CurrentWeek,
    week_type: info.CurrentWeekType,
    stage: info.CurrentStage,
    offseason_stage: info.CurrentOffseasonStage,
  };
}

function readTeams(teamTable) {
  const teams = [];
  const teamIndexByRow = new Map();
  teamTable.records.forEach((r, row) => {
    if (r.isEmpty) return;
    teamIndexByRow.set(row, r.TeamIndex);
    teams.push({
      team_index: r.TeamIndex, row, short_name: r.ShortName, long_name: r.LongName,
      display_name: r.DisplayName, nick_name: r.NickName, presentation_id: r.PresentationId,
    });
  });
  return { teams, teamIndexByRow };
}

async function readGames(tables, gameTable, teamTableId, teamIndexByRow, baseCalendarYear) {
  const teamOf = bits => {
    const ref = decodeRef(bits);
    return ref && ref.table === teamTableId ? teamIndexByRow.get(ref.row) ?? null : null;
  };
  const games = [];
  for (const [row, g] of gameTable.records.entries()) {
    if (g.isEmpty) continue;
    const names = fieldNames(g);
    const game = {
      row,
      franchise_year: g.SeasonYear,
      calendar_year: calendarYear(baseCalendarYear, g.SeasonYear),
      week_type: g.SeasonWeekType,
      week_index: g.SeasonWeek,
      status: g.GameStatus,
      simmed: g.IsSimmed,
      home_team_index: teamOf(g.HomeTeam),
      away_team_index: teamOf(g.AwayTeam),
      home_score: g.HomeScore,
      away_score: g.AwayScore,
      home_quarters: numberedFields(g, names, 'HomeScoreQuarter'),
      away_quarters: numberedFields(g, names, 'AwayScoreQuarter'),
      home_ot: g.HomeScoreOT,
      away_ot: g.AwayScoreOT,
      overtime: g.IsOvertimeGame,
      game_of_the_week: g.IsGameOfTheWeek,
      time_of_day: g.TimeOfDay,
      weather: {
        weather: g.Weather, icon: g.WeatherIconId, temperature: g.Temperature, wind: g.Wind, wind_speed: g.WindSpeed,
        precipitation: g.Precipitation, cloud_cover: g.CloudCover,
      },
      scoring_plays: await readScoringPlays(tables, g.ScoringSummaries),
    };
    if (isExportableGame(game)) games.push(game);
  }
  return games;
}

// SeasonGame.ScoringSummaries → ScoringSummary[] → ScoringSummary rows. Madden keeps these only for recent weeks.
async function readScoringPlays(tables, bits) {
  const ref = decodeRef(bits);
  if (!ref) return [];
  const arr = await tables.byId(ref.table);
  const holder = arr?.records[ref.row];
  if (!holder) return [];
  const plays = [];
  for (const item of arrayRefs(holder, fieldNames(holder))) {
    const t = await tables.byId(item.table);
    const s = t?.records[item.row];
    if (!s || s.isEmpty) continue;
    plays.push({
      quarter: s.Quarter, seconds_left: s.TimeStampInSec, conversion: s.Conversion,
      away_before: s.AwayPreviousScore, home_before: s.HomePreviousScore,
      away_after: s.AwayCurrentScore, home_after: s.HomeCurrentScore,
    });
  }
  return plays;
}

async function readContract(tables, bits) {
  const ref = decodeRef(bits);
  if (!ref) return null;
  const t = await tables.byId(ref.table);
  const c = t?.records[ref.row];
  if (!c || c.isEmpty) return null;
  const names = fieldNames(c);
  return {
    length: c.Length, current_year: c.CurrentYear, void_years: c.VoidYears,
    salary_by_year: numberedFields(c, names, 'SalaryYear'), bonus_by_year: numberedFields(c, names, 'BonusYear'),
    no_trade_clause: c.NoTradeClause, extra_year_option: c.ExtraYearOption, times_restructured: c.TimesRestructured,
    salary_distribution: c.SalaryDistribution,
  };
}

// Follows an array reference (Player.GameStats / Player.SeasonStats) to its stat rows.
async function readStatRows(tables, bits, gameRowByRef, teamOfRef) {
  const ref = decodeRef(bits);
  if (!ref) return [];
  const arr = await tables.byId(ref.table);
  const holder = arr?.records[ref.row];
  if (!holder) return [];
  const rows = [];
  for (const item of arrayRefs(holder, fieldNames(holder))) {
    const t = await tables.byId(item.table);
    const s = t?.records[item.row];
    if (!s || s.isEmpty) continue;
    const names = fieldNames(s);
    const entry = { kind: STAT_KINDS[t.name] ?? t.name, table: t.name };
    if (names.includes('SeasonGame')) entry.game_row = gameRowByRef(s.SeasonGame);
    if (names.includes('OpposingTeam')) entry.opponent_team_index = teamOfRef(s.OpposingTeam);
    entry.stats = plainFields(s, names, STAT_REF_FIELDS);
    rows.push(entry);
  }
  return rows;
}

async function readPlayers(tables, playerTable, gameTableId, teamTableId, teamIndexByRow) {
  const gameRowByRef = bits => {
    const ref = decodeRef(bits);
    return ref && ref.table === gameTableId ? ref.row : null;
  };
  const teamOfRef = bits => {
    const ref = decodeRef(bits);
    return ref && ref.table === teamTableId ? teamIndexByRow.get(ref.row) ?? null : null;
  };
  const names = fieldNames(playerTable.records[0]);
  const players = [];
  for (const [row, p] of playerTable.records.entries()) {
    if (p.isEmpty || !p.FirstName || p.ContractStatus === 'Deleted') continue;
    players.push({
      pgid: p.PresentationId,
      row,
      first_name: p.FirstName,
      last_name: p.LastName,
      position: p.Position,
      archetype: p.PlayerType,
      status: p.ContractStatus,
      team_index: p.TeamIndex,
      age: p.Age,
      years_pro: p.YearsPro,
      year_drafted: p.YearDrafted,
      jersey: p.JerseyNum,
      height_in: p.Height,
      weight_lbs: weightLbs(p.Weight),
      dev_trait: p.TraitDevelopment,
      overall: p.OverallRating,
      // OverallGrade0-4: the player's OVR under each archetype of their position, in Madden's slot order (engines/
      // ovr_weights maps slots to archetypes). OverallRating is the highest; PlayerType names it.
      archetype_grades: numberedFields(p, names, 'OverallGrade'),
      ratings: pickRatings(p, names),
      cap_salary: p.PLYR_CAPSALARY,
      contract: await readContract(tables, p.Contract),
      appearance: {
        asset_name: p.PLYR_ASSETNAME, generic_head: p.GenericHeadAssetName, body_type: p.CharacterBodyType,
        portrait_id: p.PLYR_PORTRAIT, commentary_id: p.PLYR_COMMENT,
      },
      injury: {
        status: p.InjuryStatus, type: p.InjuryType, severity: p.InjurySeverity, side: p.InjurySide,
        min_weeks: p.MinInjuryDuration, max_weeks: p.MaxInjuryDuration, injured_reserve: p.IsInjuredReserve,
      },
      season_stats: await readStatRows(tables, p.SeasonStats, gameRowByRef, teamOfRef),
      game_stats: await readStatRows(tables, p.GameStats, gameRowByRef, teamOfRef),
    });
  }
  return players;
}

export async function readSave(savePath) {
  const { franchise, cleanup } = await openCopy(savePath);
  try {
    const tables = tableReader(franchise);
    const info = (await tables.byName('SeasonInfo')).records[0];
    const teamTable = await tables.byName('Team');
    const gameTable = await tables.byName('SeasonGame');
    const playerTable = await tables.byName('Player');
    const { teams, teamIndexByRow } = readTeams(teamTable);
    const season = readSeason(info);
    const games = await readGames(tables, gameTable, teamTable.header.tableId, teamIndexByRow, season.base_calendar_year);
    const players = await readPlayers(tables, playerTable, gameTable.header.tableId, teamTable.header.tableId, teamIndexByRow);
    const stat = statSync(savePath);
    return {
      meta: {
        reader_version: READER_VERSION,
        source: resolve(savePath),
        source_modified: stat.mtime.toISOString(),
        read_at: new Date().toISOString(),
        game_year: franchise.gameYear,
        schema_expected: franchise.expectedSchemaVersion,
        schema_used: franchise.schema.meta ?? null,
      },
      season,
      teams,
      games,
      players,
    };
  } finally {
    cleanup();
  }
}

function parseArgs(argv) {
  const args = { save: null, out: null };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--out') args.out = argv[++i];
    else if (!args.save) args.save = argv[i];
  }
  return args;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  const { save, out } = parseArgs(process.argv.slice(2));
  if (!save) {
    console.error('usage: node pc_save_reader.mjs <CAREER-save> [--out snapshot.json]');
    process.exit(2);
  }
  const snapshot = await readSave(save);
  const json = JSON.stringify(snapshot);
  if (out) {
    writeFileSync(out, json);
    const s = snapshot;
    console.error(`${s.players.length} players, ${s.games.length} games, ${s.teams.length} teams, `
      + `${s.season.calendar_year} ${s.season.week_type} week index ${s.season.week_index} → ${out}`);
  } else {
    process.stdout.write(json);
  }
}
