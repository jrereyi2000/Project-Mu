#!/usr/bin/env node
// Reads a Madden 27 PC franchise save into one JSON snapshot for Project Mu (docs/done/PC_MIGRATION_PLAN.md §5.1).
//
//   node pc_save_reader.mjs <CAREER-save> [--out snapshot.json]
//
// The save is copied to a temp file and only the copy is opened, so Madden's file is never touched (and the
// library's save-on-change can't write to it). Players are keyed by `pgid` = Player.PresentationId, unique per
// player in the save (3,086 of 3,086 in CAREER-GIANTS-AUTOSAVE); `row` is the Player table row that references use.
// Per-game stat rows are reached through Player.GameStats (an array of references to the Game*Stats tables, each
// row pointing at its SeasonGame), season rows through Player.SeasonStats. Older weeks are purged by Madden, so
// the loader must read every week (§5.3).
//
// reader_version 4 (docs/done/V2_CORE_BUILD_PLAN.md §2.2) adds `depth_charts` (per real team, 35 positions × 6
// pgid slots, best first), `league_settings` (the four progression / regression slider records behind LeagueSetting
// row 0, plus the dev-trait regression and breakout-scenario flags) and, per player, `confidence` (ConfidenceRating) and
// `traits` (every PT_* flag).
//
// reader_version 5 adds, per team, `ratings`: Madden's own team ratings (Team.TEAM_RATINGOVR, OFF, DEF and the unit
// ratings QB, RB, WR, TE, OL, DL, LB, DB, ST, keyed lower-case), which pipeline/load_save.py stores as the season's
// team_seasons team_ovr / off_ovr / def_ovr (on PC there is no Team Overalls OCR capture), `cap`: the team's raw
// salary-cap fields (Team.SalCap*, RolloverCap, This/NextYearCapPenalties and Credits, TEAM_SALARY; $10,000 units, and
// SalCapCapRoom carries a 160000 offset: pipeline/load_save.py turns them into the Team Salary strip), and `draft_class`: one
// entry per DraftPlayer row whose Player is a ContractStatus Draft prospect ({pgid, draft_position, initial_draft_rank,
// true_overall_ranking, visible}; ranks 0-based, as draft_tables.mjs reads them), which load_save turns into the coming
// class's draft_prospects board when it has none. The prospects themselves are the `players` with status Draft. Each
// snapshot also carries `draft_picks`: every future pick (a DraftPick record with an OriginalTeam) as {year_offset (0 =
// the coming draft), round (1-7), pick_number (0-based overall slot, Madden's projection), original_team_index,
// current_team_index}. A fresh career keeps each future pick in its own one-row DraftPick table (674 of them in
// CAREER-STAFF), the older sample save in one 672-row table, so every DraftPick table is read. Each
// player also gets `college` = {table, row}: Player.College decoded, a reference into the game's static college list
// (table 16384 in Madden 27; the names aren't in the save, pipeline/colleges.py maps the rows), null when unset.
//
// reader_version 7 adds, per real team, `roster` and `practice_squad`: pgid arrays in list order, read from the team's
// Team.Roster and Team.PracticeSquad Player[] records. A reference to a player the snapshot doesn't export (deleted) is
// dropped. The snapshot also carries a top-level `free_agents`: League.FreeAgents (the sign.mjs pattern) as pgids.
// That list also holds every practice-squad player (sign.mjs header), so membership is not a status test; the actual
// free agents in it are status FreeAgent on TeamIndex 32.
//
// reader_version 8 adds `coaches`: one entry for every live Coach record, free agents (TeamIndex 32) included, as
// {pgid (PresentationId), table, row, first_name, last_name, age, position, team_index, contract_status,
// contract_length, contract_salary, contract_years_remaining, archetype, level, portrait, offensive_playbook,
// defensive_playbook, offensive_scheme, defensive_scheme, is_user_controlled}.
// It also adds `staff`: one {team_index, hc, oc, dc} per real team, each seat the referenced coach's pgid or null
// when the Team.HeadCoach / OffensiveCoordinator / DefensiveCoordinator reference is empty. Read only: no Coach field
// is written by the reader.
import { copyFileSync, mkdtempSync, rmSync, statSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { basename, join, resolve } from 'path';
import { fileURLToPath } from 'url';
import Franchise from 'madden-franchise';
import {
  arrayRefs, arraySlots, calendarYear, capRoomUnits, decodeRef, depthSlotPgids, flagValue, isExportableGame, LEAGUE_SETTING_FALLBACK_INDEX,
  numberedFields, orderSliderRefs, pickRatings, plainFields, playerTraits, refFromValue, salaryCapUnits, schemaFieldName, STAT_KINDS,
  weightLbs,
} from './lib.mjs';

export const READER_VERSION = 8;

// Real teams are TeamIndex 0..31 (32 = free agents; the Team table has a few more rows).
const REAL_TEAMS = 32;
const DEPTH_SLOTS = 6;

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

const TEAM_RATING_PREFIX = 'TEAM_RATING';

// Team.TEAM_RATING* → { ovr, off, def, qb, ... } (null when the schema has none of them).
function teamRatings(record) {
  const ratings = {};
  for (const name of fieldNames(record)) {
    if (name.startsWith(TEAM_RATING_PREFIX)) ratings[name.slice(TEAM_RATING_PREFIX.length).toLowerCase()] = record[name];
  }
  return Object.keys(ratings).length ? ratings : null;
}

// DraftPlayer rows → the coming draft class's board (the Player rows with ContractStatus Draft they link).
function readDraftClass(draftPlayerTable, playerTable) {
  if (!draftPlayerTable) return [];
  const out = [];
  for (const r of draftPlayerTable.records) {
    const ref = r.isEmpty ? null : decodeRef(r.Player);
    if (!ref || ref.table !== playerTable.header.tableId) continue;
    const p = playerTable.records[ref.row];
    if (!p || p.isEmpty || p.ContractStatus !== 'Draft') continue;
    out.push({ pgid: p.PresentationId, draft_position: r.DraftPosition, initial_draft_rank: r.InitialDraftRank,
      true_overall_ranking: r.TrueOverallRanking, visible: r.IsVisible });
  }
  return out;
}

// Real team salary-cap fields in true $10,000 units.
function teamCap(record) {
  if (record.SalCapCapRoom == null && record.SalCapRosterReserve == null) return null;
  return {
    rollover: record.RolloverCap,
    cap_credits: record.ThisYearCapCredits,
    roster_reserve: record.SalCapRosterReserve,
    cap_penalties: record.ThisYearCapPenalties,
    offers_reserve: record.SalCapOffersReserve,
    rookie_reserve: record.SalCapRookieReserve,
    fill_reserve: record.SalCapRosterFillReserve,
    this_year_offer_reserve: record.SalCapThisYearOfferReserve,
    cap_room: capRoomUnits(record.SalCapCapRoom),
    spending_money: capRoomUnits(record.SalCapSpendingMoney),
    next_year_cap_room: capRoomUnits(record.SalCapNextYearCapRoom),
    team_salary: record.TEAM_SALARY,
    SalCapRosterSize: record.SalCapRosterSize,
    SalCapNextYearRosterSize: record.SalCapNextYearRosterSize,
  };
}

// Every future pick across the DraftPick tables (see the header).
async function readDraftPicks(franchise, tables, teamTableId, teamIndexByRow) {
  const teamOf = bits => {
    const ref = decodeRef(bits);
    return ref && ref.table === teamTableId ? teamIndexByRow.get(ref.row) ?? null : null;
  };
  const picks = [];
  for (const table of franchise.getAllTablesByName('DraftPick')) {
    const t = await tables.byId(table.header.tableId);
    for (const r of t.records) {
      if (r.isEmpty || !decodeRef(r.OriginalTeam)) continue;
      picks.push({ year_offset: r.YearOffset, round: r.Round + 1, pick_number: r.PickNumber,
        original_team_index: teamOf(r.OriginalTeam), current_team_index: teamOf(r.CurrentTeam) });
    }
  }
  return picks;
}

function readTeams(teamTable) {
  const teams = [];
  const teamIndexByRow = new Map();
  teamTable.records.forEach((r, row) => {
    if (r.isEmpty) return;
    teamIndexByRow.set(row, r.TeamIndex);
    const isReal = r.TeamIndex < REAL_TEAMS;
    teams.push({
      team_index: r.TeamIndex, row, short_name: r.ShortName, long_name: r.LongName,
      display_name: r.DisplayName, nick_name: r.NickName, presentation_id: r.PresentationId,
      ratings: teamRatings(r),
      cap: isReal ? teamCap(r) : null,
    });
  });
  return { teams, teamIndexByRow };
}

// The Coach table (4168; the one-row practice template 5866 loses the byName "most records" test) → reader_version 8's
// coaches[]: every live record, free agents (TeamIndex 32) included. `table` and `row` identify the save record, the
// same way a player's `college` reference does. Playbook and scheme fields are exported as stored (32-bit references
// into the game's static tables) because those tables are not in the save.
export function readCoaches(coachTable) {
  if (!coachTable) return [];
  const coaches = [];
  coachTable.records.forEach((c, row) => {
    if (c.isEmpty) return;
    coaches.push({
      pgid: c.PresentationId,
      table: coachTable.header.tableId,
      row,
      first_name: c.FirstName,
      last_name: c.LastName,
      age: c.Age,
      position: c.Position,
      team_index: c.TeamIndex,
      contract_status: c.ContractStatus,
      // The deal as the save holds it, so the staff planner can tell whether a hire's contract still needs writing
      // (money in $10,000 units, as Madden stores it).
      contract_length: c.ContractLength,
      contract_salary: c.ContractSalary,
      contract_years_remaining: c.ContractYearsRemaining,
      archetype: c.Archetype,
      level: c.Level,
      portrait: c.Portrait,
      offensive_playbook: c.OffensivePlaybook,
      defensive_playbook: c.DefensivePlaybook,
      offensive_scheme: c.OffensiveScheme,
      defensive_scheme: c.DefensiveScheme,
      is_user_controlled: c.IsUserControlled,
    });
  });
  return coaches;
}

// Team.HeadCoach / OffensiveCoordinator / DefensiveCoordinator → reader_version 8's staff[]: one entry per real team,
// each seat the referenced Coach record's pgid (league_setup.mjs's read-a-seat pattern). An empty reference, or one
// that doesn't land on a live Coach row, is null — never dropped and never the number zero.
export async function readStaff(tables, teamTable, coachTableId) {
  if (!coachTableId) return [];
  const seat = async bits => {
    const ref = decodeRef(bits);
    if (!ref || ref.table !== coachTableId) return null;
    const coach = (await tables.byId(ref.table))?.records[ref.row];
    if (!coach || coach.isEmpty) return null;
    return coach.PresentationId;
  };
  const staff = [];
  for (const team of teamTable.records) {
    if (team.isEmpty || !(team.TeamIndex >= 0 && team.TeamIndex < REAL_TEAMS)) continue;
    staff.push({
      team_index: team.TeamIndex,
      hc: await seat(team.HeadCoach),
      oc: await seat(team.OffensiveCoordinator),
      dc: await seat(team.DefensiveCoordinator),
    });
  }
  return staff.sort((a, b) => a.team_index - b.team_index);
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
      home_team_stats: await readTeamStats(tables, g.HomeTeamStatCache),
      away_team_stats: await readTeamStats(tables, g.AwayTeamStatCache),
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

// SeasonGame.Home/AwayTeamStatCache → a TeamStats row: the team box score (yards, first downs, third downs,
// penalties, possession time, red zone, giveaways). Filled for played games, including the latest week, whose
// player lines Madden hasn't linked yet (§5.4).
async function readTeamStats(tables, bits) {
  const ref = decodeRef(bits);
  if (!ref) return null;
  const t = await tables.byId(ref.table);
  const s = t?.records[ref.row];
  if (!s || s.isEmpty) return null;
  return plainFields(s, fieldNames(s));
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
    let entry;
    try {
      entry = { kind: STAT_KINDS[t.name] ?? t.name, table: t.name };
      if (names.includes('SeasonGame')) entry.game_row = gameRowByRef(s.SeasonGame);
      if (names.includes('OpposingTeam')) entry.opponent_team_index = teamOfRef(s.OpposingTeam);
      entry.stats = plainFields(s, names, STAT_REF_FIELDS);
    } catch {
      // A degenerate row (a reference field with no bytes behind it, seen in a postseason
      // autosave 2026-11-10) must not fail the whole save: skip that row.
      continue;
    }
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
      college: decodeRef(p.College),
      jersey: p.JerseyNum,
      height_in: p.Height,
      weight_lbs: weightLbs(p.Weight),
      // Madden's morale (26-99, 50 the default; PROGRESSION_PLAN.md §2) and every PT_* behaviour flag (reader_version 4).
      confidence: p.ConfidenceRating,
      traits: playerTraits(p, names),
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

// A Player[] array record (Team.Roster, Team.PracticeSquad, League.FreeAgents) as pgids in slot order. A slot that is
// empty or points at a player the snapshot doesn't export (deleted) is dropped.
async function listPgids(tables, bits, playerTableId, pgidByRow) {
  const ref = decodeRef(bits);
  const arr = ref && await tables.byId(ref.table);
  if (!ref || arr?.name !== 'Player[]') return [];
  const holder = arr.records[ref.row];
  if (!holder) return [];
  const out = [];
  for (const slot of arraySlots(fieldNames(holder))) {
    const r = decodeRef(holder[slot]);
    if (!r || r.table !== playerTableId) continue;
    const pgid = pgidByRow.get(r.row);
    if (pgid != null) out.push(pgid);
  }
  return out;
}

// Team.Roster / Team.PracticeSquad → one entry per real team: { team_index, roster: [pgid], practice_squad: [pgid] },
// both in list order. readSave attaches the two arrays to the matching teams[] entry.
async function readLists(tables, teamTable, playerTableId, pgidByRow) {
  const lists = [];
  for (const team of teamTable.records) {
    if (team.isEmpty || !(team.TeamIndex >= 0 && team.TeamIndex < REAL_TEAMS)) continue;
    lists.push({
      team_index: team.TeamIndex,
      roster: await listPgids(tables, team.Roster, playerTableId, pgidByRow),
      practice_squad: await listPgids(tables, team.PracticeSquad, playerTableId, pgidByRow),
    });
  }
  return lists.sort((a, b) => a.team_index - b.team_index);
}

// League.FreeAgents (the one-row League table; the sign.mjs:290 pattern) as pgids in list order.
async function readFreeAgents(tables, playerTableId, pgidByRow) {
  const league = await tables.byName('League');
  return listPgids(tables, league?.records[0]?.FreeAgents, playerTableId, pgidByRow);
}

// Team.DepthChart → one DepthChart row per team whose position fields (QB, HB, ..., 3DRB, SLWR: 35 of them, plus
// LockedEntries) each point at a Player[] row of 6 slots, best first (release.mjs). One entry per real team:
// { team_index, slots: { <position>: [pgid | null × 6] } }. A slot pointing at a player the snapshot doesn't export
// (deleted) is null.
async function readDepthCharts(tables, teamTable, playerTableId, pgidByRow) {
  const charts = [];
  for (const team of teamTable.records) {
    if (team.isEmpty || !(team.TeamIndex >= 0 && team.TeamIndex < REAL_TEAMS)) continue;
    const dcRef = decodeRef(team.DepthChart);
    const dc = dcRef && (await tables.byId(dcRef.table))?.records[dcRef.row];
    if (!dc) continue;
    const slots = {};
    for (const pos of fieldNames(dc)) {
      if (pos === 'LockedEntries') continue;
      const ref = decodeRef(dc[pos]);
      const arr = ref && await tables.byId(ref.table);
      if (ref && arr?.name !== 'Player[]') continue;
      const holder = arr?.records[ref.row];
      const values = holder ? arraySlots(fieldNames(holder)).map(k => holder[k]) : [];
      slots[pos] = depthSlotPgids(values, playerTableId, row => pgidByRow.get(row), DEPTH_SLOTS);
    }
    charts.push({ team_index: team.TeamIndex, slots });
  }
  return charts.sort((a, b) => a.team_index - b.team_index);
}

// LeagueSetting row 0 → the four progression / regression slider records (PROGRESSION_PLAN.md §2) and two flags.
// Schema 620 lists LeagueSetting but the save's table doesn't match it, so the row decodes with madden-franchise's
// generic names: Field_N = the Nth attribute of the schema's LeagueSetting list. The slider references are found by
// value: every 32-bit field whose bits name a table and an existing row; the two int[] targets are by age, the two
// ProgressionXPSlider targets by position, the lower field number of each pair is progression (lib.orderSliderRefs).
// The flags are found by schema attribute index (lib.schemaFieldName; spike R3, V2_CORE_BUILD_PLAN.md):
// IsDevTraitRegressionEnabled (Field_88 under 620) and IsDevTraitIncreaseScenarioEnabled (Madden's Breakout Player
// scenarios, which raise dev traits; Field_86), each a 1-bit int read as true/false. A save whose LeagueSetting decodes
// with real names uses them.
function leagueSettingAttrNames(franchise) {
  try {
    const schema = franchise.schemaList?.getSchema?.('LeagueSetting');
    return Array.isArray(schema?.attributes) ? schema.attributes.map(a => a.name) : null;
  } catch {
    return null;
  }
}

async function readLeagueSettings(franchise, tables) {
  const settingTable = await tables.byName('LeagueSetting');
  const setting = settingTable?.records[0];
  const empty = {
    progression_by_age: null, progression_by_position: null, regression_by_age: null, regression_by_position: null,
  };
  const out = {
    sliders: empty,
    league_setting: settingTable ? { table_id: settingTable.header.tableId, row: 0 } : null,
    dev_trait_regression_field: null,
    dev_trait_regression_enabled: null,
    breakout_scenarios_field: null,
    breakout_scenarios_enabled: null,
  };
  if (!setting || setting.isEmpty) return out;
  const names = fieldNames(setting);
  const attrNames = leagueSettingAttrNames(franchise);
  const flag = name => {
    const field = schemaFieldName(names, attrNames, name, LEAGUE_SETTING_FALLBACK_INDEX[name]);
    return [field, field ? flagValue(setting[field]) : null];
  };
  [out.dev_trait_regression_field, out.dev_trait_regression_enabled] = flag('IsDevTraitRegressionEnabled');
  [out.breakout_scenarios_field, out.breakout_scenarios_enabled] = flag('IsDevTraitIncreaseScenarioEnabled');
  const candidates = [];
  for (const field of names) {
    const offset = setting.getFieldByKey(field)?.offset;
    if (!offset || (offset.length !== 32 && !offset.isReference)) continue;
    const ref = refFromValue(setting[field]);
    if (!ref || !franchise.getTableById(ref.table)) continue;
    const target = await tables.byId(ref.table);
    if (!target || ref.row >= target.records.length) continue;
    candidates.push({ field, offset: offset.offset, table: ref.table, row: ref.row, tableName: target.name });
  }
  const sliders = {};
  for (const [key, c] of Object.entries(orderSliderRefs(candidates))) {
    const rec = c && (await tables.byId(c.table)).records[c.row];
    sliders[key] = rec ? { table_id: c.table, row: c.row, field: c.field, values: plainFields(rec, fieldNames(rec)) } : null;
  }
  out.sliders = sliders;
  return out;
}

async function readSalaryCap(tables) {
  const salaryTable = await tables.byName('SalaryInfo');
  if (!salaryTable || !salaryTable.records.length) return null;
  const r = salaryTable.records[0];
  if (r.isEmpty) return null;
  const rawCap = r.TeamSalaryCap;
  const rawInit = r.InitialSalaryCap;

  let increaseTable = null;
  const incRef = decodeRef(r.SalaryCapIncreasePerYear);
  if (incRef) {
    const t = await tables.byId(incRef.table);
    const rec = t?.records[incRef.row];
    if (rec) {
      increaseTable = {
        table_id: incRef.table,
        row: incRef.row,
        values: plainFields(rec, fieldNames(rec)),
      };
    }
  }

  return {
    table_id: salaryTable.header.tableId,
    row: 0,
    raw_team_salary_cap: rawCap,
    team_salary_cap: salaryCapUnits(rawCap),
    initial_salary_cap: salaryCapUnits(rawInit),
    increase_table: increaseTable,
  };
}

export async function readSave(savePath) {
  const { franchise, cleanup } = await openCopy(savePath);
  try {
    const tables = tableReader(franchise);
    const info = (await tables.byName('SeasonInfo')).records[0];
    // Franchise.LeagueID is random per franchise and kept across saves and weeks (pipeline/watch_save.py routes on it).
    const franchiseInfo = (await tables.byName('Franchise')).records[0];
    const teamTable = await tables.byName('Team');
    const gameTable = await tables.byName('SeasonGame');
    const playerTable = await tables.byName('Player');
    const { teams, teamIndexByRow } = readTeams(teamTable);
    const season = readSeason(info);
    const games = await readGames(tables, gameTable, teamTable.header.tableId, teamIndexByRow, season.base_calendar_year);
    const players = await readPlayers(tables, playerTable, gameTable.header.tableId, teamTable.header.tableId, teamIndexByRow);
    const pgidByRow = new Map(players.map(p => [p.row, p.pgid]));
    // reader_version 7: attach each real team's roster and practice squad to its teams[] entry, and export the league
    // free-agent list at the top level.
    const teamByIndex = new Map(teams.map(t => [t.team_index, t]));
    for (const list of await readLists(tables, teamTable, playerTable.header.tableId, pgidByRow)) {
      const team = teamByIndex.get(list.team_index);
      if (team) Object.assign(team, { roster: list.roster, practice_squad: list.practice_squad });
    }
    const freeAgents = await readFreeAgents(tables, playerTable.header.tableId, pgidByRow);
    // reader_version 8: every live coach (free agents too) and each real team's HC / OC / DC seats.
    const coachTable = await tables.byName('Coach');
    const coaches = readCoaches(coachTable);
    const staff = await readStaff(tables, teamTable, coachTable?.header.tableId);
    const depthCharts = await readDepthCharts(tables, teamTable, playerTable.header.tableId, pgidByRow);
    const leagueSettings = await readLeagueSettings(franchise, tables);
    const draftClass = readDraftClass(await tables.byName('DraftPlayer'), playerTable);
    const draftPicks = await readDraftPicks(franchise, tables, teamTable.header.tableId, teamIndexByRow);
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
      franchise: { league_id: franchiseInfo.LeagueID },
      season,
      salary_cap: await readSalaryCap(tables),
      teams,
      games,
      players,
      coaches,
      staff,
      free_agents: freeAgents,
      depth_charts: depthCharts,
      league_settings: leagueSettings,
      draft_class: draftClass,
      draft_picks: draftPicks,
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
      + `${s.depth_charts.length} depth charts, ${s.draft_class.length} draft prospects, ${s.season.calendar_year} ${s.season.week_type} week index `
      + `${s.season.week_index} → ${out}`);
  } else {
    process.stdout.write(json);
  }
}
