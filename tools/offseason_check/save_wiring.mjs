// offseason_check's read-only save inspection: user wiring and empty staff seats, as one JSON line on stdout.
//
//   node tools/offseason_check/save_wiring.mjs <save>
//
// The four user links league_setup.mjs sets and staff.mjs re-points on every head-coach move (Team.HeadCoach,
// Team.UserCharacter, FranchiseUser.UserEntity / FranchiseUser.Team, Coach.IsUserControlled) are checked on all 32
// teams by league_setup.mjs's own verifySetup, the same check the auditor ran after every write. The save is
// opened with saveOnChange off and never saved.
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const readerDir = join(here, '..', '..', 'pipeline', 'save_reader');
const require = createRequire(join(readerDir, 'package.json'));
const Franchise = require('madden-franchise');
const { verifySetup } = await import(join(readerDir, 'league_setup.mjs'));
const { decodeRef } = await import(join(readerDir, 'lib.mjs'));

const save = process.argv[2];
if (!save) {
  console.error('usage: node save_wiring.mjs <save>');
  process.exit(2);
}
const open = () => Franchise.create(save, { autoParse: true, saveOnChange: false });
const f = await open();
const teams = f.getAllTablesByName('Team');
for (const t of teams) await t.readRecords();
const team = teams.reduce((b, t) => (!b || t.records.length > b.records.length ? t : b), null);
const empty = [];
for (const t of team.records) {
  if (t.isEmpty || t.TeamIndex >= 32) continue;
  for (const k of ['HeadCoach', 'OffensiveCoordinator', 'DefensiveCoordinator']) {
    if (!decodeRef(t[k])) empty.push(`${t.ShortName}:${k}`);
  }
}
const v = await verifySetup(await open());
console.log(JSON.stringify({ problems: v.problems, users: v.users, distinct_ids: v.distinctIds, empty_seats: empty }));
