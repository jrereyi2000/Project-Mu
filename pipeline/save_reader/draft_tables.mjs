#!/usr/bin/env node
// Reads a Madden 27 franchise save's draft tables into JSON for the teleporter's draft step (pipeline/teleport_draft.py,
// docs/PC_MIGRATION_PLAN.md §4.3 Step 5): the future draft picks with their owners, and the draft-class rows.
//
//   node draft_tables.mjs <CAREER-save> [--out draft.json]
//
// How the save keeps them (CAREER-GIANTS-AUTOSAVE, schema M27_620_0):
// * Picks are DraftPick records. Three tables share the name: a 1-row default, the last draft's selections (224 rows,
//   SelectedPlayer set, no OriginalTeam) and the future picks (672 = 3 drafts x 7 rounds x 32 teams). A future pick is
//   YearOffset (0 = the coming draft), Round (0-6), PickNumber (0-223, the projected overall slot, reordered by
//   Madden) and two Team references: OriginalTeam and CurrentTeam (the owner). A traded pick has CurrentTeam !=
//   OriginalTeam; no team keeps its own pick list (DraftPick[] is one league-wide list of all 672).
// * The class is Player records with ContractStatus Draft (TeamIndex 32), each linked from a DraftPlayer record
//   (DraftPosition, the board's InitialDraftRank and the hidden TrueOverallRanking, both 0-based, plus combine and pro
//   day results). Player.College is a reference into the game's static college list (table 16384), not into the save.
import { existsSync, writeFileSync } from 'fs';
import { resolve } from 'path';
import { fileURLToPath } from 'url';
import Franchise from 'madden-franchise';
import { decodeRef } from './lib.mjs';

const READER_VERSION = 1;

async function readAll(franchise, name) {
  const all = franchise.getAllTablesByName(name);
  for (const t of all) await t.readRecords();
  return all;
}

const largest = tables => tables.reduce((b, t) => (!b || t.records.length > b.records.length ? t : b), null);
const live = t => t.records.filter(r => !r.isEmpty);

// The future-picks table: the DraftPick table whose records carry an OriginalTeam.
export function futurePicksTable(tables) {
  const withOwners = tables.filter(t => live(t).some(r => decodeRef(r.OriginalTeam)));
  return largest(withOwners);
}

export async function readDraftTables(franchise) {
  const teamTable = largest(await readAll(franchise, 'Team'));
  const players = largest(await readAll(franchise, 'Player'));
  const picksTable = futurePicksTable(await readAll(franchise, 'DraftPick'));
  const draftPlayers = largest(await readAll(franchise, 'DraftPlayer'));
  const teamRow = bits => {
    const d = decodeRef(bits);
    if (!d) return null;
    if (d.table !== teamTable.header.tableId) throw new Error(`a pick points at table ${d.table}, not Team ${teamTable.header.tableId}`);
    return d.row;
  };
  const teams = teamTable.records.map((r, row) => (r.isEmpty ? null : {
    row, team_index: r.TeamIndex, short_name: r.ShortName, display_name: r.DisplayName })).filter(Boolean);
  const picks = picksTable ? picksTable.records.map((r, row) => (r.isEmpty ? null : {
    row, year_offset: r.YearOffset, round: r.Round + 1, pick_number: r.PickNumber,
    original_team_row: teamRow(r.OriginalTeam), current_team_row: teamRow(r.CurrentTeam),
    selected: Boolean(decodeRef(r.SelectedPlayer)) })).filter(Boolean) : [];
  const prospects = [];
  for (const [row, r] of draftPlayers.records.entries()) {
    const d = r.isEmpty ? null : decodeRef(r.Player);
    if (!d || d.table !== players.header.tableId) continue;
    const p = players.records[d.row];
    if (!p || p.isEmpty) continue;
    prospects.push({ row, pgid: p.PresentationId, contract_status: p.ContractStatus, draft_position: r.DraftPosition,
      initial_draft_rank: r.InitialDraftRank, true_overall_ranking: r.TrueOverallRanking, visible: r.IsVisible });
  }
  return {
    meta: { draft_tables_version: READER_VERSION },
    team_table_id: teamTable.header.tableId,
    draft_pick_table_id: picksTable?.header.tableId ?? null,
    draft_player_table_id: draftPlayers.header.tableId,
    teams, picks, prospects,
  };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  let save = null;
  let out = null;
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--out') out = argv[++i];
    else if (!save) save = argv[i];
  }
  if (!save || !existsSync(save)) {
    console.error('usage: node draft_tables.mjs <CAREER-save> [--out draft.json]');
    process.exit(2);
  }
  const franchise = await Franchise.create(save, { autoParse: true, saveOnChange: false });
  const doc = await readDraftTables(franchise);
  doc.meta.source = resolve(save);
  const text = JSON.stringify(doc, null, 1);
  if (out) {
    writeFileSync(out, text);
    console.error(`${doc.picks.length} picks, ${doc.prospects.length} draft-class rows → ${out}`);
  } else {
    process.stdout.write(text);
  }
}
