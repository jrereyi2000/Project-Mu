// Writes Project Mu's head coaches and coordinators into an open franchise, for save_writer.mjs's "staff" list
// (docs/in_progress/COACH_SAVE_WRITES_PLAN.md §2.5). Three actions:
//
//   {"staff": [
//     {"action": "release", "pgid": 9001},                       // a fired coach leaves for the free-agent pool
//     {"action": "release", "pgid": 9001, "retire": true},        // D8: unhired for three seasons -> Retired
//     {"action": "assign",  "pgid": 9002, "team_index": 7, "position": "DefensiveCoordinator",
//      "contract": {"ContractLength": 3, "ContractSalary": 450, "ContractYearsRemaining": 3},
//      "set": {"Portrait": 5324, "Portrait_Force_Silhouette": false}},   // optional extra Coach fields
//     {"action": "create",  "key": "coach-311", "team_index": 7, "position": "HeadCoach",
//      "profile": { ...coach_madden_profiles fields... }, "visuals_template": 9010,
//      "contract": {"ContractLength": 3, "ContractSalary": 450, "ContractYearsRemaining": 3}}
//   ]}
//
// A `pgid` is Coach.PresentationId. The block plans against the save before anything changes, applies, and verifies
// the written file, like release.mjs / sign.mjs / trade.mjs.
//
// Measured facts this module is built on (docs/reference/MADDEN_COACH_FIRING_DIFF.md, Spike B; MADDEN_COACH_RECORDS.md,
// Spike A):
//   * There is no free-agent coach array in a Madden 27 save. The pool is simply Coach.TeamIndex = 32. A release is
//     field writes on one record plus a null on the team's seat ref: PrevTeamIndex = TeamIndex, TeamIndex = 32,
//     ContractStatus = FreeAgent, ContractYearsRemaining = 0, COACH_RATING = 50, IsUserControlled = false. Position is
//     left alone (Madden does). With retire: true only ContractStatus differs (Retired).
//   * An assign moves an existing record to Team.HeadCoach / OffensiveCoordinator / DefensiveCoordinator, writes
//     Position (and OriginalPosition when the role changes), TeamIndex, ContractStatus = Signed and the contract. It also
//     nulls any other team's seat ref still pointing at him: U5 emits no release for a man who is moving (a poached
//     coordinator, a retread head coach), so the stale seat is the assign's job. A target seat held by a coach neither
//     released nor moved in this same write is refused; a coach assigned elsewhere in the write counts as moved.
//     (Clear every stale seat before filling any target, so a clear cannot undo a fill in the same write.)
//   * A create fills an empty Coach row (the writer opens with autoUnempty) or one a release in the same write frees,
//     clones the template coach's CharacterVisuals, StaffTalents/talent lists and audibles, then assigns him.
//     CharacterVisuals.RawData is plain JSON in schema 620, so a clone is a plain field copy (pipeline/character_visuals.py
//     is NOT imported). PresentationId = max(existing) + 1, reported back so Mu can store it.
//   * A head-coach seat re-points the same four links league_setup.mjs sets: Team.UserCharacter, the team's
//     FranchiseUser.UserEntity, FranchiseUser.Team and Coach.IsUserControlled. The user row is the target team's own
//     (so a swap leaves one row per team); when Madden's own firing left that row with a null Team, the orphaned row
//     that followed the coach or the man he replaces is re-pointed instead. Madden's own firing nulls UserCharacter and
//     FranchiseUser.Team and leaves the others stale; Mu's change must leave the team with a live user.
//   * Team head coaches store OffensiveScheme/DefensiveScheme as references into the team-specific static table 16485;
//     coordinators use the 16384 SCHEME_ROWS. Playbook and scheme refs come from Spike A §7.3.
//
// Spike C (2026-11-10, in game with JR): the created record is accepted and selectable with no crash, and a
// created head coach shows on the staff screen. Portrait 0 with Portrait_Force_Silhouette = true renders no
// portrait at all, so a coach without a face now carries the game's own placeholder (7999) instead. Still to see:
// whether IsCreated = true opens the in-game editor, whether a created head coach's AssetName yields a 3D model,
// and whether a ContractStatus = Retired coach stays out of the pool.
import { arraySlots, cloneInto, decodeRef, encodeRef, fieldMeta, fieldValueError, NO_REF } from './lib.mjs';
import { fieldErrors, fieldMismatches, moveTables } from './sign.mjs';

export const FREE_AGENT_TEAM = 32;
export const POSITIONS = ['HeadCoach', 'OffensiveCoordinator', 'DefensiveCoordinator'];
const POSITION_SET = new Set(POSITIONS);
const SIDES = ['Gameday', 'Playsheet'];
const SIDE_FIELD = { Gameday: 'GamedayTalents', Playsheet: 'PlaysheetTalents' };
const CONTRACT_KEYS = ['ContractLength', 'ContractSalary', 'ContractYearsRemaining'];
const COACH_WEIGHT = 10;
// The game's own "no portrait" value: both placeholder coach rows carry it, and a coach with no face of his own is
// drawn as the default silhouette from it.
const COACH_NO_PORTRAIT = 7999;
// Team.DisplayName as the save spells it -> the key the static playbook maps use (the static maps follow Mu's
// CITY_TO_TEAM names, which say FortyNiners). Every other team's DisplayName is already a key.
const TEAM_BOOK_ALIASES = { '49ers': 'FortyNiners' };
// A head coach's 3D model asset key, in the form all 32 real head coaches use ("BowlesTodd_C_PRO").
const assetKey = profile => `${profile.first_name}${profile.last_name}_C_PRO`.replace(/[^A-Za-z0-9_]/g, '');

// Uniform values every live coach record carries (Spike A §7.5). The writer sets them explicitly rather than trusting
// an empty row's defaults.
const COACH_DEFAULTS = {
  Portrait_Swappable_Library_Path: 'library_icons_brt',
  IsCreated: false, IsImported: false, IsLegend: false, IsMaxLevel: false,
  Probation: false, TraitExpertScout: false,
  OWNER_COMMENTID: 0, OWNER_COMMENTTYPE: 0, CurrentPurchasedTalentCosts: 0,
};

// Static table rows (Spike A §7.3). Playbooks and (coordinator / free-agent) schemes are 16384 references; a team head
// coach's schemes are the team-specific 16485 pair from §3.10.
export const OFF_PLAYBOOK_ROWS = {
  FortyNiners: 104461, Bears: 102775, Bengals: 104440, Bills: 104438, Broncos: 104443, Browns: 104441,
  Buccaneers: 104463, Cardinals: 104435, Chargers: 104450, Chiefs: 104449, Colts: 104447, Commanders: 104434,
  Cowboys: 104442, Dolphins: 104452, Eagles: 104459, Falcons: 104436, Giants: 104456, Jaguars: 104448,
  Jets: 104457, Lions: 104444, Packers: 104445, Panthers: 104439, Patriots: 104454, Raiders: 104458,
  Rams: 104451, Ravens: 104437, Saints: 104455, Seahawks: 104462, Steelers: 104460, Texans: 104446,
  Titans: 104464, Vikings: 104453, BalancedPass: 104466, BalancedRun: 104467, Balanced: 104465, Pistol: 104471,
  PowerRun: 104468, RunandShoot: 104472, RunnGun: 104470, WestCoast: 104469,
};
export const DEF_PLAYBOOK_ROWS = {
  FortyNiners: 104494, Bears: 102773, Bengals: 104473, Bills: 104504, Broncos: 104476, Browns: 104474,
  Buccaneers: 104496, Cardinals: 104433, Chargers: 104483, Chiefs: 104482, Colts: 104480, Commanders: 104498,
  Cowboys: 104475, Dolphins: 104485, Eagles: 104492, Falcons: 104506, Giants: 104489, Jaguars: 104481,
  Jets: 104490, Lions: 104477, Packers: 104478, Panthers: 104505, Patriots: 104487, Raiders: 104491,
  Rams: 104484, Ravens: 104507, Saints: 104488, Seahawks: 104495, Steelers: 104493, Texans: 104479,
  Titans: 104497, Vikings: 104486, ThreeFour: 104500, FourThree: 104499, FourSix: 104501, Cover2: 104502,
  MultipleD: 104503,
};
export const SCHEME_ROWS = {
  WestCoastZoneRun: 104574, WestCoastPowerRun: 104579, VerticalZoneRun: 104575, MultipleZoneRun: 104577,
  MultiplePowerRun: 104576, VerticalPowerRun: 104578, Spread: 104572, RunAndShoot: 104580, AirRaid: 122541,
  Pistol: 122509, WestCoastSpread: 122479, Base4_3: 104581, Under4_3: 104582, Base3_4: 104584, Under3_4: 104583,
  Tampa2: 104586, Quarters4_3: 122543, Disguise3_4: 122544, Storm3_4: 122542, Cover3_4_3: 122649, Defense_46: 104585,
};
// TeamIndex -> { offense, defense } rows of the team-specific scheme table 16485 (Spike A §3.10).
export const HC_SCHEME_ROWS = [
  { offense: 57861, defense: 57862 }, { offense: 57873, defense: 57874 }, { offense: 57865, defense: 57866 },
  { offense: 57887, defense: 57888 }, { offense: 57875, defense: 57876 }, { offense: 57913, defense: 57914 },
  { offense: 57915, defense: 57916 }, { offense: 57893, defense: 57872 }, { offense: 57889, defense: 57890 },
  { offense: 57881, defense: 57882 }, { offense: 57895, defense: 57896 }, { offense: 57887, defense: 57868 },
  { offense: 57897, defense: 57898 }, { offense: 57909, defense: 57910 }, { offense: 57921, defense: 57922 },
  { offense: 57899, defense: 57900 }, { offense: 57883, defense: 57884 }, { offense: 57860, defense: 57858 },
  { offense: 57857, defense: 57858 }, { offense: 57903, defense: 57904 }, { offense: 57907, defense: 57908 },
  { offense: 57869, defense: 57870 }, { offense: 57891, defense: 57892 }, { offense: 57917, defense: 57920 },
  { offense: 57871, defense: 57920 }, { offense: 57901, defense: 57902 }, { offense: 57911, defense: 57912 },
  { offense: 57863, defense: 57864 }, { offense: 57877, defense: 57878 }, { offense: 57885, defense: 57886 },
  { offense: 57905, defense: 57906 }, { offense: 57879, defense: 57880 },
];

// Talent name -> its static TalentInfo row and the save row kind it lives in (Spike A Appendix B). The writer creates a
// row of that kind, points it at the static info, gives it a four-slot TalentTier[] and appends it to a Talent[] list.
export const TALENT_INFO = {
  "3-3-5 Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47117 },
  "3-4 Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47111 },
  "4-2-5 Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47123 },
  "4-3 Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47105 },
  "Aerial Suppression": { kind: 'GamedayTalent', table: 16469, row: 53046 },
  "After School Tutor": { kind: 'SeasonTalent', table: 16474, row: 66053 },
  "Ahead of the Chains": { kind: 'GamedayTalent', table: 16469, row: 52968 },
  "Air Raid Gun Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47069 },
  "Air Raid Pistol Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47063 },
  "Always Watching": { kind: 'GamedayTalent', table: 16469, row: 53064 },
  "Angry Runner": { kind: 'GamedayTalent', table: 16469, row: 52920 },
  "Batch O' Blitzes Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47022 },
  "Behind the Chains": { kind: 'GamedayTalent', table: 16469, row: 53094 },
  "Big Back": { kind: 'GamedayTalent', table: 16469, row: 52986 },
  "Blitz Buster": { kind: 'GamedayTalent', table: 16469, row: 52932 },
  "Bookend Busters": { kind: 'GamedayTalent', table: 16469, row: 53010 },
  "Cool Under Pressure": { kind: 'GamedayTalent', table: 16469, row: 52902 },
  "Corner Czar": { kind: 'SeasonTalent', table: 16474, row: 66102 },
  "Cover 2 Guru": { kind: 'GamedayTalent', table: 16469, row: 53052 },
  "Cover 4 Guru": { kind: 'GamedayTalent', table: 16469, row: 53058 },
  "Dashing Dots": { kind: 'GamedayTalent', table: 16469, row: 52938 },
  "Dime Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47158 },
  "Dollar Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47163 },
  "Dr. Dots": { kind: 'GamedayTalent', table: 16469, row: 52914 },
  "Eagle Eye": { kind: 'SeasonTalent', table: 16474, row: 66087 },
  "Edge Focus": { kind: 'SeasonTalent', table: 16474, row: 66094 },
  "Edge Wizards": { kind: 'GamedayTalent', table: 16470, row: 56945 },
  "Field General": { kind: 'GamedayTalent', table: 16458, row: 35154 },
  "Fit Finder": { kind: 'SeasonTalent', table: 16474, row: 66085 },
  "Fool Me Never": { kind: 'GamedayTalent', table: 16469, row: 53028 },
  "Gadget Playsheet": { kind: 'PlaysheetTalent', table: 16476, row: 54203 },
  "Ground Guardians": { kind: 'GamedayTalent', table: 16470, row: 57028 },
  "Happier Feet": { kind: 'GamedayTalent', table: 16469, row: 52926 },
  "Head of the Class": { kind: 'SeasonTalent', table: 16474, row: 66055 },
  "Heavy I Playsheet": { kind: 'PlaysheetTalent', table: 16476, row: 54215 },
  "Heavy Sets Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47151 },
  "Heavy Strong Playsheet": { kind: 'PlaysheetTalent', table: 16476, row: 54221 },
  "Hitamus Stickumus": { kind: 'GamedayTalent', table: 16469, row: 53112 },
  "Inside Scoop": { kind: 'SeasonTalent', table: 16474, row: 66093 },
  "Inside Stuffed": { kind: 'GamedayTalent', table: 16469, row: 53070 },
  "Insider Trading": { kind: 'GamedayTalent', table: 16469, row: 52944 },
  "Interior Dominating": { kind: 'GamedayTalent', table: 16469, row: 52896 },
  "Interior Havoc": { kind: 'GamedayTalent', table: 16469, row: 53016 },
  "Island Kings": { kind: 'GamedayTalent', table: 16470, row: 57065 },
  "Linebacker Lens": { kind: 'SeasonTalent', table: 16474, row: 66096 },
  "Load the Box": { kind: 'GamedayTalent', table: 16470, row: 57089 },
  "Mano a Mano": { kind: 'GamedayTalent', table: 16469, row: 53040 },
  "Master of Disguise Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47087 },
  "Move the Chains": { kind: 'GamedayTalent', table: 16458, row: 35196 },
  "Mr. Dream": { kind: 'GamedayTalent', table: 16469, row: 53004 },
  "Nickel Mug Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47135 },
  "Nickel Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47129 },
  "No Fly Zone": { kind: 'GamedayTalent', table: 16469, row: 53034 },
  "Outside the Club": { kind: 'GamedayTalent', table: 16469, row: 52950 },
  "Passing Paranoia": { kind: 'GamedayTalent', table: 16469, row: 53022 },
  "People Movers": { kind: 'GamedayTalent', table: 16458, row: 35258 },
  "Pitch Perfect Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47057 },
  "Play Fake Magician": { kind: 'GamedayTalent', table: 16469, row: 52908 },
  "Practician (CB)": { kind: 'SeasonTalent', table: 16458, row: 35341 },
  "Practician (DL)": { kind: 'SeasonTalent', table: 16458, row: 35317 },
  "Practician (HB)": { kind: 'SeasonTalent', table: 16458, row: 35280 },
  "Practician (LB)": { kind: 'SeasonTalent', table: 16458, row: 35329 },
  "Practician (OL)": { kind: 'SeasonTalent', table: 16458, row: 35299 },
  "Practician (QB)": { kind: 'SeasonTalent', table: 16458, row: 35274 },
  "Practician (S)": { kind: 'SeasonTalent', table: 16458, row: 35347 },
  "Practician (WR/TE)": { kind: 'SeasonTalent', table: 16458, row: 35286 },
  "Pressure Posse": { kind: 'GamedayTalent', table: 16470, row: 56981 },
  "QB Contain Playsheet": { kind: 'PlaysheetTalent', table: 16476, row: 63825 },
  "QB Runs Playsheet": { kind: 'PlaysheetTalent', table: 16476, row: 54233 },
  "RB Reconnaissance": { kind: 'SeasonTalent', table: 16474, row: 66089 },
  "Read Blocktion": { kind: 'GamedayTalent', table: 16469, row: 52956 },
  "Receiver Radar": { kind: 'SeasonTalent', table: 16474, row: 66090 },
  "Red Zone Passing Playsheet": { kind: 'PlaysheetTalent', table: 16476, row: 54209 },
  "Riot Shield": { kind: 'GamedayTalent', table: 16458, row: 35246 },
  "RPOh No": { kind: 'GamedayTalent', table: 16469, row: 53082 },
  "Run N Shoot Gun Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47051 },
  "Run N Shoot Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47039 },
  "Run Stop Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47099 },
  "Safety First": { kind: 'SeasonTalent', table: 16474, row: 66103 },
  "Screen Game Playsheet": { kind: 'PlaysheetTalent', table: 16476, row: 54227 },
  "Sheddables": { kind: 'GamedayTalent', table: 16469, row: 53100 },
  "Shield Finder": { kind: 'SeasonTalent', table: 16474, row: 66092 },
  "Shot Plays Playsheet": { kind: 'PlaysheetTalent', table: 16476, row: 54239 },
  "Shotgun 2-Back Playsheet": { kind: 'PlaysheetTalent', table: 16476, row: 54245 },
  "Sideline 2 Sideline": { kind: 'GamedayTalent', table: 16469, row: 53076 },
  "Stamina Stealer": { kind: 'GamedayTalent', table: 16469, row: 52962 },
  "Stim Shot": { kind: 'GamedayTalent', table: 16469, row: 53088 },
  "Stud Finder": { kind: 'SeasonTalent', table: 16474, row: 66088 },
  "Sure Hands": { kind: 'GamedayTalent', table: 16469, row: 52884 },
  "Talent Evaluator": { kind: 'SeasonTalent', table: 16474, row: 66086 },
  "Tight End Tracker": { kind: 'SeasonTalent', table: 16474, row: 66091 },
  "Trench Barons": { kind: 'GamedayTalent', table: 16469, row: 52974 },
  "Trench Goblins": { kind: 'GamedayTalent', table: 16470, row: 56993 },
  "Trimmed Edges": { kind: 'GamedayTalent', table: 16469, row: 52890 },
  "Triple Option Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47033 },
  "Two Minute Playsheet": { kind: 'PlaysheetTalent', table: 16476, row: 120992 },
  "West Coast Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47010 },
  "Where's the Beef?": { kind: 'SeasonTalent', table: 16474, row: 66095 },
  "Wide Empty Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47075 },
  "Wide Slot Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47081 },
  "Wide Trips Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47145 },
  "Wildcat Playsheet": { kind: 'PlaysheetTalent', table: 16474, row: 47045 },
  "Work Horse": { kind: 'GamedayTalent', table: 16458, row: 35166 },
};

// ---- helpers ----

const isInt = Number.isInteger;
const nameOf = profile => `${String(profile.first_name ?? '').trim()[0] ?? ''}. ${String(profile.last_name ?? '').trim()}`.trim();
const teamLabel = (team, index) => team?.record?.ShortName ?? `team ${index}`;

function usedRow(plan, tableId, row) {
  return plan.usedRows.get(tableId)?.has(row) ?? false;
}

function markUsed(plan, tableId, row) {
  const set = plan.usedRows.get(tableId) ?? new Set();
  set.add(row);
  plan.usedRows.set(tableId, set);
}

// The rows a create may fill: an empty Coach row first (so a released coach stays in the pool), then rows a caller
// declares free (`freed`), then records a release in this same write frees as a last resort.
function takeCoachRow(plan, coachTable, freedRows, releases, where, errors) {
  const tableId = coachTable.header.tableId;
  let row = coachTable.records.findIndex((r, i) => r.isEmpty && !usedRow(plan, tableId, i));
  if (row < 0) for (const i of freedRows) {
    if (coachTable.records[i] && !usedRow(plan, tableId, i)) { row = i; break; }
  }
  if (row < 0) for (const release of releases) {
    if (!release.reused && !usedRow(plan, tableId, release.row)) { row = release.row; release.reused = true; break; }
  }
  if (row < 0) { errors.push(`${where}: no empty Coach row (and none freed in this write)`); return null; }
  markUsed(plan, tableId, row);
  return row;
}

// The FranchiseUser row a head-coach seat re-points: the target team's own user row first (so a swap never leaves two
// rows on one team, and a retread's old row stays with the team he leaves for its next coach). If Madden's own firing
// left that row with a null Team, the orphaned row that already follows this coach, or the one that followed the man he
// replaces, is re-pointed instead.
function teamUser(fuTable, teamTableId, teamRow, subject, outgoing) {
  if (!fuTable) return { error: 'no FranchiseUser table' };
  const live = fuTable.records.map((r, row) => ({ r, row })).filter(x => !x.r.isEmpty);
  const same = (a, b) => a && b && a.table === b.table && a.row === b.row;
  const atTeam = live.filter(x => same(decodeRef(x.r.Team), { table: teamTableId, row: teamRow }));
  if (atTeam.length === 1) return atTeam[0];
  if (atTeam.length > 1) return { error: `${atTeam.length} FranchiseUser rows point at the team` };
  for (const ref of [subject, outgoing]) {
    if (!ref) continue;
    const orphan = live.filter(x => same(decodeRef(x.r.UserEntity), ref) && !decodeRef(x.r.Team));
    if (orphan.length === 1) return orphan[0];
    if (orphan.length > 1) return { error: `${orphan.length} free FranchiseUser rows point at the coach` };
  }
  const orphans = live.filter(x => !decodeRef(x.r.Team));
  if (orphans.length === 1) return orphans[0];
  return { error: 'no FranchiseUser row points at the team or is free to re-point' };
}

// The contract fields to write, or an empty object when the caller has none yet.
//
// A hire has no `staff_contracts` row at the moment the staff write runs: `staff_contracts_run.annual` files them,
// and the league office only calls it after a written FREE_AGENCY write. Requiring a contract here refused every
// OFFSEASON_START / RESIGN / FREE_AGENCY write — player moves included — and the annual step that would file the
// contracts never ran, so it deadlocked (audit B1, 2026-11-10). An action without a contract still moves the seat and
// sets TeamIndex / ContractStatus / Position, and the next catch-up write fills the Contract* fields once annual has
// filed the deal.
function contractSet(item, where, errors) {
  const contract = item?.contract;
  if (contract === undefined || contract === null) return {};
  if (typeof contract !== 'object' || Array.isArray(contract)) {
    errors.push(`${where}: contract must be an object`); return {};
  }
  const out = { ContractLength: contract.ContractLength, ContractSalary: contract.ContractSalary ?? 0,
    ContractYearsRemaining: contract.ContractYearsRemaining ?? 0 };
  for (const k of Object.keys(contract)) {
    if (!CONTRACT_KEYS.includes(k)) errors.push(`${where}: contract field ${k} is not one of ${CONTRACT_KEYS.join(', ')}`);
  }
  if (!isInt(out.ContractLength) || out.ContractLength < 1) errors.push(`${where}: ContractLength must be an integer of at least 1`);
  if (!isInt(out.ContractSalary) || out.ContractSalary < 0) errors.push(`${where}: ContractSalary must be a non-negative integer ($10,000 units)`);
  if (!isInt(out.ContractYearsRemaining) || out.ContractYearsRemaining < 0) errors.push(`${where}: ContractYearsRemaining must be a non-negative integer`);
  return out;
}

// ---- planning ----

// Plans the staff actions without changing anything: { plan, errors }. `freed` names Coach rows (indices, or Coach refs)
// a caller says are free for a create to fill; rows released by this write are also usable.
export async function planStaff(franchise, staff = [], { freed = [] } = {}) {
  const errors = [];
  const tables = moveTables(franchise);
  const coachTable = await tables.byName('Coach');
  const teamTable = await tables.byName('Team');
  const plan = {
    tables, coachTable: coachTable?.header.tableId ?? null, teamTable: teamTable?.header.tableId ?? null,
    franchiseUserTable: null, releases: [], assignments: [], creates: [], usedRows: new Map(),
    freedRows: [], nextPresentationId: null, kindTables: {}, tierArrayTable: null, tierTable: null,
  };
  if (!coachTable) { errors.push('staff: no Coach table'); return { plan, errors }; }
  if (!teamTable) { errors.push('staff: no Team table'); return { plan, errors }; }
  const CID = coachTable.header.tableId, TID = teamTable.header.tableId;

  const teams = [];
  teamTable.records.forEach((r, row) => {
    if (!r.isEmpty && isInt(r.TeamIndex) && r.TeamIndex >= 0 && r.TeamIndex < FREE_AGENT_TEAM) {
      teams.push({ record: r, row, teamIndex: r.TeamIndex });
    }
  });
  const teamByIndex = new Map(teams.map(t => [t.teamIndex, t]));
  const coachRows = new Map();
  coachTable.records.forEach((r, row) => {
    if (!r.isEmpty && isInt(r.PresentationId)) {
      coachRows.set(r.PresentationId, [...(coachRows.get(r.PresentationId) ?? []), row]);
    }
  });
  const livePresentationIds = coachTable.records.filter(r => !r.isEmpty && isInt(r.PresentationId)).map(r => r.PresentationId);

  plan.freedRows = freed.map(x => {
    if (isInt(x)) return x;
    const d = decodeRef(x);
    return d && d.table === CID ? d.row : null;
  }).filter(isInt);

  if (!Array.isArray(staff)) { errors.push('staff: must be a list'); return { plan, errors }; }
  for (const item of staff) {
    if (!item || typeof item !== 'object') { errors.push('staff: every entry must be an object'); continue; }
    if (!['release', 'assign', 'create'].includes(item.action)) {
      errors.push(`staff: unknown action ${JSON.stringify(item.action)}`);
    }
  }

  const fuTable = await tables.byName('FranchiseUser');
  plan.franchiseUserTable = fuTable?.header.tableId ?? null;

  // 1. Releases (planned first so an assign/create can see which seats this write frees).
  const releasedRows = new Set();
  const releasedPgids = new Set();
  for (const item of staff.filter(s => s?.action === 'release')) {
    const pgid = item.pgid;
    const where = `staff release pgid ${pgid}`;
    if (!isInt(pgid)) { errors.push(`${where}: needs an integer pgid (Coach.PresentationId)`); continue; }
    if (releasedPgids.has(pgid)) { errors.push(`${where}: listed twice`); continue; }
    const rows = coachRows.get(pgid) ?? [];
    if (rows.length !== 1) { errors.push(`${where}: ${rows.length} coaches`); continue; }
    const row = rows[0];
    const record = coachTable.records[row];
    const retire = item.retire === true;
    if (record.ContractStatus === 'Retired') { errors.push(`${where}: already Retired`); continue; }
    if (!retire && record.TeamIndex === FREE_AGENT_TEAM && record.ContractStatus === 'FreeAgent') {
      errors.push(`${where}: already a free agent`); continue;
    }
    const seats = [];
    for (const team of teams) for (const key of POSITIONS) {
      const d = decodeRef(team.record[key]);
      if (d && d.table === CID && d.row === row) seats.push({ team, key });
    }
    // A retire runs on a coach already in the free-agent pool (TeamIndex 32), where copying TeamIndex into
    // PrevTeamIndex would lose the team he last coached (audit B11, 2026-11-10).
    const leaving = record.TeamIndex !== FREE_AGENT_TEAM;
    const set = { TeamIndex: FREE_AGENT_TEAM, PrevTeamIndex: leaving ? record.TeamIndex : record.PrevTeamIndex,
      ContractStatus: retire ? 'Retired' : 'FreeAgent', ContractYearsRemaining: 0, COACH_RATING: 50,
      IsUserControlled: false };
    errors.push(...fieldErrors(record, set, 'Coach').map(e => `${where}: ${e}`));
    plan.releases.push({ pgid, row, record, retire, fromTeam: record.TeamIndex,
      team: teamLabel(teamByIndex.get(record.TeamIndex), record.TeamIndex), seats, set, reused: false });
    releasedRows.add(row);
    releasedPgids.add(pgid);
  }
  const seenPgids = new Set(releasedPgids);
  const filledSeats = new Set();

  // A coach assigned elsewhere in this write is vacating his old seat even though U5's planner emits no release for a
  // man who is moving (a poached coordinator, a retread head coach): his assign clears the stale seat itself.
  const plannedSeats = new Map();
  for (const item of staff.filter(s => s?.action === 'assign')) {
    const rows = isInt(item?.pgid) ? (coachRows.get(item.pgid) ?? []) : [];
    const position = item?.position ?? item?.profile?.position;
    const team = teamByIndex.get(item?.team_index);
    if (rows.length === 1 && team && POSITION_SET.has(position)) plannedSeats.set(rows[0], position);
  }
  const vacating = row => releasedRows.has(row) || plannedSeats.has(row);
  const staleSeats = (row, teamIndex, key) => {
    const out = [];
    for (const t of teams) for (const k of POSITIONS) {
      if (t.teamIndex === teamIndex && k === key) continue;
      const d = decodeRef(t.record[k]);
      if (d && d.table === CID && d.row === row) out.push({ team: t, key: k });
    }
    return out;
  };

  // The seat ref an assign/create fills, refusing a holder not released in this write.
  const seatFor = (where, team, position, coachRow) => {
    const seatKey = position;
    const holderRef = decodeRef(team.record[seatKey]);
    let holder = null;
    if (holderRef) {
      if (holderRef.table !== CID) { errors.push(`${where}: Team.${seatKey} points into table ${holderRef.table}`); return null; }
      holder = coachTable.records[holderRef.row];
      if (!holder || holder.isEmpty) { errors.push(`${where}: Team.${seatKey} points at empty Coach row ${holderRef.row}`); return null; }
      if (holderRef.row !== coachRow && !vacating(holderRef.row)) {
        errors.push(`${where}: ${teamLabel(team, team.teamIndex)} ${position} is held by ${holder.FirstName} ${holder.LastName} (pgid ${holder.PresentationId}), not released or moved in this write`);
        return null;
      }
    }
    const key = `${team.teamIndex}:${position}`;
    if (filledSeats.has(key)) { errors.push(`${where}: ${teamLabel(team, team.teamIndex)} ${position} filled twice in this write`); return null; }
    filledSeats.add(key);
    return { seatKey, holderRef, holder, ref: encodeRef(CID, coachRow) };
  };

  // The user wiring a head-coach seat needs (league_setup.mjs's four links; the fourth is IsUserControlled).
  const wiringFor = (where, team, seat, position) => {
    if (position !== 'HeadCoach') return { user: null, userSet: null, teamSet: null };
    // Madden's own firing nulls Team.HeadCoach AND FranchiseUser.Team, so on an empty seat nothing in the save links
    // the orphaned user row to this team — but the fired coach's PrevTeamIndex still does. Hand that man to teamUser
    // as the outgoing occupant so it can find the row that still follows him.
    let outgoing = seat?.holderRef ?? null;
    if (!outgoing) {
      // The man Madden fired from this team: a free agent whose PrevTeamIndex is this team and who coached it as head
      // coach. If several match, the most recently freed one (highest PresentationId) is the plausible tie; any other
      // ambiguity is left alone rather than guessed (audit B11, 2026-11-10).
      const firedRows = coachTable.records
        .map((c, row) => ({ c, row }))
        .filter(x => !x.c.isEmpty && x.c.TeamIndex === FREE_AGENT_TEAM && x.c.PrevTeamIndex === team.teamIndex &&
          (x.c.Position === 'HeadCoach' || x.c.OriginalPosition === 'HeadCoach'))
        .sort((a, b) => (b.c.PresentationId ?? 0) - (a.c.PresentationId ?? 0));
      if (firedRows.length) outgoing = { table: CID, row: firedRows[0].row };
    }
    // teamUser compares decoded refs, so the new coach's encoded seat ref is decoded first.
    const user = teamUser(fuTable, TID, team.row, seat?.ref ? decodeRef(seat.ref) : null, outgoing);
    if (user.error) { errors.push(`${where}: ${user.error}, so the team's user cannot be re-pointed`); return null; }
    return { user, userSet: { UserEntity: seat.ref, Team: encodeRef(TID, team.row) },
      teamSet: { UserCharacter: seat.ref } };
  };

  const validatePlacement = (item, where) => {
    const position = item.position ?? item.profile?.position;
    if (!POSITION_SET.has(position)) {
      errors.push(`${where}: position must be HeadCoach, OffensiveCoordinator or DefensiveCoordinator`);
      return null;
    }
    const team = teamByIndex.get(item.team_index);
    if (!team) { errors.push(`${where}: no team ${item.team_index}`); return null; }
    return { position, team };
  };

  // 2. Assigns.
  for (const item of staff.filter(s => s?.action === 'assign')) {
    const pgid = item.pgid;
    const where = `staff assign pgid ${pgid}`;
    if (!isInt(pgid)) { errors.push(`${where}: needs an integer pgid (Coach.PresentationId)`); continue; }
    if (seenPgids.has(pgid)) { errors.push(`${where}: in more than one staff action`); continue; }
    seenPgids.add(pgid);
    const rows = coachRows.get(pgid) ?? [];
    if (rows.length !== 1) { errors.push(`${where}: ${rows.length} coaches`); continue; }
    const row = rows[0];
    const record = coachTable.records[row];
    const placed = validatePlacement(item, where);
    if (!placed) continue;
    const { position, team } = placed;
    const seat = seatFor(where, team, position, row);
    if (!seat) continue;
    const wiring = wiringFor(where, team, seat, position);
    if (!wiring) continue;
    const contract = contractSet(item, where, errors);
    if (!contract) continue;
    const set = { TeamIndex: team.teamIndex, ContractStatus: 'Signed', Position: position, ...contract };
    if (record.Position !== position) set.OriginalPosition = position;
    set.IsUserControlled = position === 'HeadCoach';
    // A head coach's schemes are the target team's 16485 pair. Without this a coach moving teams kept the pair of the
    // team he left, and his Mu scheme was never written at all (audit B9, 2026-11-10).
    const assignRefs = {};
    if (position === 'HeadCoach') {
      const pair = HC_SCHEME_ROWS[team.teamIndex];
      if (!pair) errors.push(`${where}: no 16485 head-coach scheme pair for team ${team.teamIndex}`);
      else {
        assignRefs.OffensiveScheme = encodeRef(16485, pair.offense);
        assignRefs.DefensiveScheme = encodeRef(16485, pair.defense);
      }
    }
    // An optional "set" of further Coach fields on the same record (the portrait pack pushes Portrait and
    // Portrait_Force_Silhouette through it: Mu knows the coach's pgid, not his save row). Same shape as a "players"
    // edit's set, validated the same way; it may not set a field the assign itself writes.
    const extra = item.set ?? {};
    if (typeof extra !== 'object' || Array.isArray(extra)) {
      errors.push(`${where}: set must be an object`);
    } else {
      for (const [k, v] of Object.entries(extra)) {
        if (k in set) { errors.push(`${where}: set may not set ${k}, which the assign writes`); continue; }
        const meta = fieldMeta(record, k);
        const err = meta ? fieldValueError(meta, v) : 'no such Coach field';
        if (err) errors.push(`${where} set ${k} = ${JSON.stringify(v)}: ${err}`);
        else set[k] = v;
      }
    }
    errors.push(...fieldErrors(record, set, 'Coach').map(e => `${where}: ${e}`));
    plan.assignments.push({ kind: 'assign', pgid, row, record, teamIndex: team.teamIndex,
      team: teamLabel(team, team.teamIndex), teamRow: team.row, teamRecord: team.record, position,
      seatKey: seat.seatKey, seatRef: seat.ref, clearSeats: staleSeats(row, team.teamIndex, position),
      set, refs: assignRefs, user: wiring.user, userSet: wiring.userSet, teamSet: wiring.teamSet });
  }

  // 3. Creates. Resolve the sub-record tables a create needs once.
  const createItems = staff.filter(s => s?.action === 'create');
  const kindTables = {};
  const kindTable = async kind => {
    if (!kindTables[kind]) kindTables[kind] = await tables.byName(kind);
    return kindTables[kind];
  };
  let tierArrayTable = null, tierTable = null;
  if (createItems.length) {
    for (const kind of ['GamedayTalent', 'SeasonTalent', 'PlaysheetTalent']) {
      const t = await kindTable(kind);
      if (t) plan.kindTables[kind] = t.header.tableId;
    }
    tierArrayTable = await tables.byName('TalentTier[]');
    tierTable = await tables.byName('TalentTier');
    if (tierArrayTable) plan.tierArrayTable = tierArrayTable.header.tableId;
    if (tierTable) plan.tierTable = tierTable.header.tableId;
    // The sub-record fields buildTalent writes must exist (a wrong name would set a JS property, not a save field).
    const requireFields = (table, fields, label) => {
      const sample = table.records[0];
      if (!sample) { errors.push(`staff create: ${label} table ${table.header.tableId} is empty`); return; }
      for (const k of fields) if (!fieldMeta(sample, k)) errors.push(`staff create: ${label} has no ${k} field`);
    };
    for (const [kind, id] of Object.entries(plan.kindTables)) {
      requireFields(await tables.byId(id), ['TalentInfo', 'CurrentTier', 'Tiers'], kind);
    }
    if (tierArrayTable) {
      const sample = tierArrayTable.records[0];
      if (!sample || arraySlots(Object.keys(sample.fields)).length < 4) {
        errors.push('staff create: TalentTier[] must have at least four slots');
      }
    }
    if (tierTable) requireFields(tierTable, ['TierStatus'], 'TalentTier');
  }

  let nextPid = livePresentationIds.length ? Math.max(...livePresentationIds) + 1 : 1;
  const createKeys = new Set();
  for (const item of createItems) {
    const key = item.key;
    const where = `staff create ${JSON.stringify(key)}`;
    if (typeof key !== 'string' || !key.trim()) { errors.push(`${where}: needs a key`); continue; }
    if (createKeys.has(key)) { errors.push(`${where}: listed twice`); continue; }
    createKeys.add(key);
    const profile = item.profile;
    if (!profile || typeof profile !== 'object') { errors.push(`${where}: needs a profile object`); continue; }
    const placed = validatePlacement(item, where);
    if (!placed) continue;
    const { position, team } = placed;

    const templatePgid = item.visuals_template ?? profile.visuals_template;
    const templateRows = isInt(templatePgid) ? (coachRows.get(templatePgid) ?? []) : [];
    if (templateRows.length !== 1) {
      errors.push(`${where}: visuals_template ${templatePgid} is ${templateRows.length} coaches, not one`);
      continue;
    }
    const templateRow = templateRows[0];
    const template = coachTable.records[templateRow];

    const row = takeCoachRow(plan, coachTable, plan.freedRows, plan.releases, where, errors);
    if (row < 0 || row === null) continue;
    const record = coachTable.records[row];

    const contract = contractSet(item, where, errors);
    if (!contract) continue;
    const seat = seatFor(where, team, position, row);
    if (!seat) continue;
    const wiring = wiringFor(where, team, seat, position);
    if (!wiring) continue;

    // Identity and the fields the profile owns.
    for (const k of ['first_name', 'last_name', 'age', 'archetype', 'level']) {
      if (profile[k] === undefined || profile[k] === null || profile[k] === '') {
        errors.push(`${where}: profile.${k} is required`);
      }
    }
    const presentationId = nextPid++;
    // A coach with no portrait of their own gets the game's own "no portrait" placeholder (7999, the value both
    // placeholder coach rows carry) rather than Portrait 0 with the silhouette flag: Spike C showed that combination
    // renders nothing at all, where the placeholder is the state the game already draws for a coach with no face.
    const portrait = isInt(profile.portrait_id) && profile.portrait_id > 0 ? profile.portrait_id : COACH_NO_PORTRAIT;
    const set = {
      FirstName: profile.first_name, LastName: profile.last_name, Name: nameOf(profile), Age: profile.age,
      Position: position, OriginalPosition: position, Archetype: profile.archetype, Level: profile.level,
      TeamIndex: team.teamIndex, ContractStatus: 'Signed', PresentationId: presentationId,
      Portrait: portrait, Portrait_Force_Silhouette: false, ...COACH_DEFAULTS,
      Weight: isInt(profile.weight) ? profile.weight : COACH_WEIGHT,
      // Every real head coach carries a <First><Last>_C_PRO asset key (all 32); the 94 without one are coordinators,
      // whose model comes from GenericHeadAssetName. Marking the record created is what the in-game editor keys on.
      AssetName: position === 'HeadCoach' ? assetKey(profile) : '', IsCreated: true,
      IsUserControlled: position === 'HeadCoach', ...contract,
    };
    // Optional look fields: write them when the profile has them rather than writing null over a row default.
    for (const [k, v] of [['CharacterBodyType', profile.body_type], ['Height', profile.height],
      ['FaceShape', profile.face_shape], ['GenericHeadAssetName', profile.generic_head]]) {
      if (v !== undefined && v !== null) set[k] = v;
    }
    for (const k of ['specialty', 'personality', 'demeanor', 'years_coaching']) {
      if (profile[k] === undefined || profile[k] === null) continue;
      set[{ specialty: 'COACH_SPECIALTY', personality: 'Personality', demeanor: 'COACH_DEMEANOR',
        years_coaching: 'YearsCoaching' }[k]] = profile[k];
    }
    for (const [k, v] of Object.entries(profile.ratings ?? {})) set[k] = v;
    for (const [k, v] of Object.entries(profile.tendencies ?? {})) set[k] = v;

    // Playbooks and schemes: static references. A team head coach uses its 16485 pair; coordinators use 16384.
    const staticRefs = {};
    // A profile with no playbook keeps the team's own, keyed by nickname ("Panthers"), which is what the static maps
    // hold; ShortName is the abbreviation ("CAR") and is not a key. DisplayName, not NickName: NickName is slang for
    // nine teams ("G-Men", "Niners", "Pats", "Bolts", "Phins", "Jags", "Pack", "Vikes", "Bucs") and is not a map key
    // (audit B2, 2026-11-10). San Francisco is the one DisplayName the map spells differently.
    const displayName = team.record.DisplayName ?? null;
    const teamBook = displayName == null ? null : (TEAM_BOOK_ALIASES[displayName] ?? displayName);
    const offName = profile.offensive_playbook ?? teamBook;
    const defName = profile.defensive_playbook ?? teamBook;
    if (offName != null) {
      if (!(offName in OFF_PLAYBOOK_ROWS)) errors.push(`${where}: no static row for offensive playbook ${JSON.stringify(offName)}`);
      else staticRefs.OffensivePlaybook = encodeRef(16384, OFF_PLAYBOOK_ROWS[offName]);
    }
    if (defName != null) {
      if (!(defName in DEF_PLAYBOOK_ROWS)) errors.push(`${where}: no static row for defensive playbook ${JSON.stringify(defName)}`);
      else staticRefs.DefensivePlaybook = encodeRef(16384, DEF_PLAYBOOK_ROWS[defName]);
    }
    if (position === 'HeadCoach') {
      const pair = HC_SCHEME_ROWS[team.teamIndex];
      if (!pair) errors.push(`${where}: no 16485 head-coach scheme pair for team ${team.teamIndex}`);
      else {
        staticRefs.OffensiveScheme = encodeRef(16485, pair.offense);
        staticRefs.DefensiveScheme = encodeRef(16485, pair.defense);
      }
    } else {
      for (const [k, side] of [['OffensiveScheme', 'offensive_scheme'], ['DefensiveScheme', 'defensive_scheme']]) {
        const name = profile[side];
        if (name == null) { staticRefs[k] = NO_REF; continue; }
        if (!(name in SCHEME_ROWS)) errors.push(`${where}: no static row for ${side} ${JSON.stringify(name)}`);
        else staticRefs[k] = encodeRef(16384, SCHEME_ROWS[name]);
      }
    }

    // References with no profile field: copy the template's (sim playbooks, team philosophy), null the rest.
    const refs = { ...staticRefs, OffenseAudibles: NO_REF, DefenseAudibles: NO_REF, HasTrait: NO_REF };
    for (const k of ['SimOffensivePlaybook', 'SimDefensivePlaybook', 'TeamPhilosophy', 'DefaultTeamPhilosophy']) {
      if (fieldMeta(template, k) && template[k] !== undefined) refs[k] = template[k];
    }

    // Clone the template's sub-records: CharacterVisuals (plain JSON), audibles, and the talent-list structure.
    const cloneSpecs = [];
    for (const field of ['CharacterVisuals', 'OffenseAudibles', 'DefenseAudibles']) {
      const d = decodeRef(template[field]);
      if (d) cloneSpecs.push({ field, table: d.table, row: d.row });
    }

    const talents = Array.isArray(profile.talents) ? profile.talents : [];
    const wanted = { Gameday: [], Playsheet: [] };
    for (const [i, t] of talents.entries()) {
      if (!t || !(t.slot in wanted)) { errors.push(`${where}: talent ${i} slot must be Gameday or Playsheet`); continue; }
      const info = TALENT_INFO[t.talent];
      if (!info) { errors.push(`${where}: unknown talent ${JSON.stringify(t.talent)}`); continue; }
      const tier = t.tier === undefined ? 0 : t.tier;
      if (!isInt(tier) || tier < 0 || tier > 3) { errors.push(`${where}: talent ${JSON.stringify(t.talent)} tier must be 0-3`); continue; }
      wanted[t.slot].push({ kind: info.kind, table: info.table, row: info.row, tier });
    }

    // The template's CoachingTalents row and its two Talent[] lists (the lists a create fills).
    const stRef = decodeRef(template.StaffTalents);
    let talentPlan = null;
    if (!stRef) {
      errors.push(`${where}: the template coach has no StaffTalents list to clone`);
    } else {
      const ctTable = await tables.byId(stRef.table);
      const ct = ctTable?.records?.[stRef.row];
      if (!ct || ct.isEmpty) {
        errors.push(`${where}: the template's StaffTalents row ${stRef.table}:${stRef.row} is empty`);
      } else {
        const lists = {};
        for (const side of SIDES) {
          const d = decodeRef(ct[SIDE_FIELD[side]]);
          if (!d) { errors.push(`${where}: the template's ${SIDE_FIELD[side]} list is empty`); continue; }
          const listTable = await tables.byId(d.table);
          const listRecord = listTable?.records?.[d.row];
          if (!listRecord || listRecord.isEmpty || !listTable.name.endsWith('[]')) {
            errors.push(`${where}: the template's ${SIDE_FIELD[side]} does not point at a Talent[] row`);
            continue;
          }
          const slots = arraySlots(Object.keys(listRecord.fields));
          if (wanted[side].length > slots.length) {
            errors.push(`${where}: ${wanted[side].length} ${side} talents do not fit ${slots.length} slots`);
          }
          lists[side] = { table: d.table, row: d.row, slots: slots.length };
        }
        if (SIDES.every(s => lists[s])) {
          for (const side of SIDES) for (const t of wanted[side]) {
            if (!plan.kindTables[t.kind]) errors.push(`${where}: no ${t.kind} table for a ${side} talent`);
          }
          if (!tierArrayTable) errors.push(`${where}: no TalentTier[] table`);
          if (!tierTable) errors.push(`${where}: no TalentTier table`);
          talentPlan = { coachingTalents: { table: stRef.table, row: stRef.row }, lists, ...wanted };
        }
      }
    }

    // FaceShape is a CoachFace enum whose real value `Invalid_` ends with the sentinel `_`; the shared enum check would
    // treat it as structural, so it is checked here against the member list itself.
    errors.push(...fieldErrors(record, set, 'Coach', ['FaceShape']).map(e => `${where}: ${e}`));
    if (profile.face_shape != null) {
      const faceMeta = fieldMeta(record, 'FaceShape');
      if (!faceMeta || !faceMeta.enumMembers?.includes(profile.face_shape)) {
        errors.push(`${where}: FaceShape ${JSON.stringify(profile.face_shape)} is not a CoachFace member`);
      }
    }
    plan.creates.push({ kind: 'create', key, row, record, presentationId, position,
      teamIndex: team.teamIndex, team: teamLabel(team, team.teamIndex), teamRow: team.row,
      teamRecord: team.record, seatKey: seat.seatKey, seatRef: seat.ref,
      clearSeats: staleSeats(row, team.teamIndex, position), set, refs, cloneSpecs, talentPlan,
      user: wiring.user, userSet: wiring.userSet, teamSet: wiring.teamSet, cloned: {}, talents: {} });
  }

  if (plan.creates.length) plan.nextPresentationId = nextPid;
  // B8 (audit, 2026-11-10): a head coach moved to another team leaves his old team with no coach, and with no
  // replacement in the same write that team loses its user — which is the state Madden's own firing leaves and the
  // one Mu exists to avoid. Refuse it rather than write it.
  for (const r of plan.releases) {
    for (const stale of r.seats ?? []) {
      if (stale.key !== 'HeadCoach') continue;
      if (!filledSeats.has(`${stale.team.teamIndex}:HeadCoach`)) {
        errors.push(`${r.retire ? 'staff retire' : 'staff release'} pgid ${r.pgid}: leaves `
          + `${teamLabel(stale.team, stale.team.teamIndex)} without a head coach; fill that seat in the same write`);
      }
    }
  }
  for (const a of [...plan.assignments, ...plan.creates]) {
    for (const stale of a.clearSeats ?? []) {
      if (stale.key !== 'HeadCoach') continue;
      if (!filledSeats.has(`${stale.team.teamIndex}:HeadCoach`)) {
        errors.push(`${a.kind === 'create' ? `staff create ${a.key}` : `staff assign pgid ${a.pgid}`}: leaves `
          + `${teamLabel(stale.team, stale.team.teamIndex)} without a head coach; fill that seat in the same write`);
      }
    }
  }
  return { plan, errors };
}

// ---- applying ----

// A row allocator over the plan's used-row ledger (planned Coach rows are already marked).
function allocator(plan) {
  return table => {
    const tableId = table.header.tableId;
    const row = table.records.findIndex((r, i) => r.isEmpty && !usedRow(plan, tableId, i));
    if (row < 0) throw new Error(`no free row in ${table.name} (table ${tableId})`);
    markUsed(plan, tableId, row);
    return row;
  };
}

// One talent row with a four-slot TalentTier[] (tier 0..tier Owned, the rest Purchasable), for a profile talent.
async function buildTalent(plan, alloc, talent, position) {
  const kindTable = await plan.tables.byId(plan.kindTables[talent.kind]);
  const row = alloc(kindTable);
  const record = kindTable.records[row];
  record.TalentInfo = encodeRef(talent.table, talent.row);
  record.CurrentTier = talent.tier;
  if (fieldMeta(record, 'Status')) record.Status = 'None';
  if (fieldMeta(record, 'IsTalentActive')) record.IsTalentActive = false;
  if (fieldMeta(record, 'OwnerPosition')) record.OwnerPosition = position;
  const tierArrayTable = await plan.tables.byId(plan.tierArrayTable);
  const arrayRow = alloc(tierArrayTable);
  const arrayRecord = tierArrayTable.records[arrayRow];
  const slots = arraySlots(Object.keys(arrayRecord.fields)).slice(0, 4);
  const tierTable = await plan.tables.byId(plan.tierTable);
  for (const [i, slot] of slots.entries()) {
    const tierRow = alloc(tierTable);
    tierTable.records[tierRow].TierStatus = i <= talent.tier ? 'Owned' : 'Purchasable';
    arrayRecord[slot] = encodeRef(plan.tierTable, tierRow);
  }
  record.Tiers = encodeRef(plan.tierArrayTable, arrayRow);
  return encodeRef(kindTable.header.tableId, row);
}

async function applyCreate(plan, alloc, create) {
  const tables = plan.tables;
  // Clone the simple sub-records (CharacterVisuals is plain JSON, so its RawData copies as a field).
  for (const spec of create.cloneSpecs) {
    const ref = await cloneInto(tables, spec.table, spec.row, false, alloc);
    create.record[spec.field] = ref;
    create.cloned[spec.field] = ref;
  }
  // Clone the talent-list structure, then write the profile's talents into the cloned lists.
  if (create.talentPlan) {
    const tp = create.talentPlan;
    const ctRef = await cloneInto(tables, tp.coachingTalents.table, tp.coachingTalents.row, false, alloc);
    const ctTable = await tables.byId(decodeRef(ctRef).table);
    const ctRecord = ctTable.records[decodeRef(ctRef).row];
    for (const side of SIDES) {
      const listTable = await tables.byId(tp.lists[side].table);
      const templateList = listTable.records[tp.lists[side].row];
      // A template list can be empty (a level-0 coach may have no playsheet talents); cloning it would leave the new
      // row empty, so a fresh row is used in that case and filled from the profile.
      const hasEntries = arraySlots(Object.keys(templateList.fields)).some(k => decodeRef(templateList[k]));
      const listRef = hasEntries
        ? await cloneInto(tables, tp.lists[side].table, tp.lists[side].row, false, alloc)
        : encodeRef(tp.lists[side].table, alloc(listTable));
      ctRecord[SIDE_FIELD[side]] = listRef;
      const refs = [];
      for (const talent of tp[side]) refs.push(await buildTalent(plan, alloc, talent, create.position));
      const listRecord = listTable.records[decodeRef(listRef).row];
      const slots = arraySlots(Object.keys(listRecord.fields));
      slots.forEach((k, i) => { listRecord[k] = i < refs.length ? refs[i] : NO_REF; });
      create.talents[side] = refs;
    }
    create.record.StaffTalents = ctRef;
    create.cloned.StaffTalents = ctRef;
  }
  for (const [k, v] of Object.entries(create.refs)) create.record[k] = v;
  for (const [k, v] of Object.entries(create.set)) create.record[k] = v;
  create.teamRecord[create.seatKey] = create.seatRef;
  if (create.user) for (const [k, v] of Object.entries(create.userSet)) create.user.r[k] = v;
  if (create.teamSet) for (const [k, v] of Object.entries(create.teamSet)) create.teamRecord[k] = v;
}

// Applies a planned staff block. Releases first, then every stale seat a moving coach leaves, then the target seats,
// so a clear can never undo a fill done in the same write.
export async function applyStaff(plan) {
  for (const r of plan.releases) {
    for (const [k, v] of Object.entries(r.set)) r.record[k] = v;
    for (const seat of r.seats) seat.team.record[seat.key] = NO_REF;
  }
  for (const a of [...plan.assignments, ...plan.creates]) {
    for (const seat of a.clearSeats ?? []) seat.team.record[seat.key] = NO_REF;
  }
  for (const a of plan.assignments) {
    for (const [k, v] of Object.entries(a.set)) a.record[k] = v;
    for (const [k, v] of Object.entries(a.refs ?? {})) a.record[k] = v;
    a.teamRecord[a.seatKey] = a.seatRef;
    if (a.user) for (const [k, v] of Object.entries(a.userSet)) a.user.r[k] = v;
    if (a.teamSet) for (const [k, v] of Object.entries(a.teamSet)) a.teamRecord[k] = v;
  }
  const alloc = allocator(plan);
  for (const c of plan.creates) await applyCreate(plan, alloc, c);
}

// ---- verifying ----

const sameRef = (a, b) => Boolean(a && b && a.table === b.table && a.row === b.row);

// A reference written as NO_REF reads back as null, so "no reference" compares equal either way; fieldMismatches
// compares strictly and would report every cleared reference as a mismatch.
const unsetRef = v => v == null || v === NO_REF;   // eslint-disable-line eqeqeq - null or undefined
function refMismatches(record, refs, label) {
  const bad = [];
  for (const [k, v] of Object.entries(refs)) {
    const raw = record[k];
    if (unsetRef(v) ? !unsetRef(raw) : raw !== v) bad.push(`${label} ${k}: wrote ${v}, read ${raw}`);
  }
  return bad;
}

// Re-reads every release, assignment and create from the written file; returns the mismatches. For a head-coach seat it
// checks the same four user links league_setup.mjs sets.
export async function verifyStaff(check, plan) {
  const tables = moveTables(check);
  const bad = [];
  const coachTable = await tables.byId(plan.coachTable);
  const teamTable = await tables.byId(plan.teamTable);
  const fuTable = plan.franchiseUserTable ? await tables.byId(plan.franchiseUserTable) : null;

  for (const r of plan.releases) {
    const record = coachTable.records[r.row];
    if (!r.reused) {
      if (record.PresentationId !== r.pgid) bad.push(`staff release pgid ${r.pgid}: row ${r.row} moved`);
      bad.push(...fieldMismatches(record, r.set, `staff release pgid ${r.pgid}`));
    }
    for (const seat of r.seats) {
      const got = decodeRef(teamTable.records[seat.team.row][seat.key]);
      if (got && got.table === plan.coachTable && got.row === r.row) {
        bad.push(`staff release pgid ${r.pgid}: ${seat.team.record.ShortName} ${seat.key} still points at him`);
      }
    }
  }

  for (const a of [...plan.assignments, ...plan.creates]) {
    const label = a.kind === 'create' ? `staff create ${a.key}` : `staff assign pgid ${a.pgid}`;
    const record = coachTable.records[a.row];
    if (a.kind === 'create' && record.PresentationId !== a.presentationId) {
      bad.push(`${label}: PresentationId wrote ${a.presentationId}, read ${record.PresentationId}`);
    }
    bad.push(...fieldMismatches(record, a.set, label));
    bad.push(...refMismatches(record, a.refs ?? {}, `${label} references`));
    const team = teamTable.records[a.teamRow];
    const seat = decodeRef(team[a.seatKey]);
    if (!sameRef(seat, { table: plan.coachTable, row: a.row })) {
      bad.push(`${label}: ${team.ShortName} ${a.seatKey} does not point at the record`);
    }
    for (const stale of a.clearSeats ?? []) {
      const got = decodeRef(teamTable.records[stale.team.row][stale.key]);
      if (got && got.table === plan.coachTable && got.row === a.row) {
        bad.push(`${label}: a stale seat (${stale.team.record.ShortName} ${stale.key}) still points at him`);
      }
    }
    if (a.position === 'HeadCoach') {
      if (!sameRef(decodeRef(team.UserCharacter), seat)) bad.push(`${label}: Team.UserCharacter is not the head coach`);
      if (!fuTable) bad.push(`${label}: no FranchiseUser table`);
      else if (!a.user) bad.push(`${label}: no FranchiseUser row was re-pointed`);
      else {
        const user = fuTable.records[a.user.row];
        if (!sameRef(decodeRef(user.UserEntity), seat)) bad.push(`${label}: FranchiseUser.UserEntity is not the head coach`);
        if (!sameRef(decodeRef(user.Team), { table: plan.teamTable, row: a.teamRow })) {
          bad.push(`${label}: FranchiseUser.Team does not point at the team`);
        }
      }
      if (record.IsUserControlled !== true) bad.push(`${label}: coach is not user-controlled`);
    }
    if (a.kind === 'create') bad.push(...await verifyCreate(tables, plan, a, label));
  }
  return bad;
}

async function verifyCreate(tables, plan, create, label) {
  const bad = [];
  const ref = x => decodeRef(create.cloned[x]);
  // CharacterVisuals copied as plain JSON: compare RawData with the template's.
  const visuals = ref('CharacterVisuals');
  if (visuals) {
    const spec = create.cloneSpecs.find(s => s.field === 'CharacterVisuals');
    const got = (await tables.byId(visuals.table)).records[visuals.row];
    const want = (await tables.byId(spec.table)).records[spec.row];
    // Asset names are case-insensitive and the library lowercases them on write ("CoachWardrobe_Black_Pants" comes
    // back as "coachwardrobe_black_pants"), so compare without case rather than reporting a difference that isn't one.
    const sameBlob = String(got.RawData).toLowerCase() === String(want.RawData).toLowerCase();
    if (!sameBlob) bad.push(`${label}: cloned CharacterVisuals RawData differs from the template`);
  } else if (create.cloneSpecs.some(s => s.field === 'CharacterVisuals')) {
    bad.push(`${label}: CharacterVisuals was not cloned`);
  }
  // The talent lists, their profile talents in order and each talent's four tier statuses.
  const ct = ref('StaffTalents');
  if (!create.talentPlan) return bad;
  if (!ct) { bad.push(`${label}: StaffTalents was not cloned`); return bad; }
  const ctRecord = (await tables.byId(ct.table)).records[ct.row];
  for (const side of SIDES) {
    const listRef = decodeRef(ctRecord[SIDE_FIELD[side]]);
    if (!listRef) { bad.push(`${label}: cloned ${SIDE_FIELD[side]} is empty`); continue; }
    const listRecord = (await tables.byId(listRef.table)).records[listRef.row];
    const slots = arraySlots(Object.keys(listRecord.fields));
    const wanted = create.talentPlan[side];
    for (const [i, talent] of wanted.entries()) {
      const got = decodeRef(listRecord[slots[i]]);
      if (!got) { bad.push(`${label}: ${side} talent ${i} is empty`); continue; }
      const talentRecord = (await tables.byId(got.table)).records[got.row];
      if (talentRecord.TalentInfo !== encodeRef(talent.table, talent.row)) {
        bad.push(`${label}: ${side} talent ${i} points at ${talentRecord.TalentInfo}, not ${encodeRef(talent.table, talent.row)}`);
      }
      if (talentRecord.CurrentTier !== talent.tier) bad.push(`${label}: ${side} talent ${i} tier wrote ${talent.tier}, read ${talentRecord.CurrentTier}`);
      const tiersRef = decodeRef(talentRecord.Tiers);
      if (!tiersRef) { bad.push(`${label}: ${side} talent ${i} has no tier list`); continue; }
      const tiersRecord = (await tables.byId(tiersRef.table)).records[tiersRef.row];
      const tierSlots = arraySlots(Object.keys(tiersRecord.fields)).slice(0, 4);
      for (const [n, slot] of tierSlots.entries()) {
        const tierRef = decodeRef(tiersRecord[slot]);
        if (!tierRef) { bad.push(`${label}: ${side} talent ${i} tier ${n} is empty`); continue; }
        const want = n <= talent.tier ? 'Owned' : 'Purchasable';
        const gotStatus = (await tables.byId(tierRef.table)).records[tierRef.row].TierStatus;
        if (gotStatus !== want) bad.push(`${label}: ${side} talent ${i} tier ${n} status wrote ${want}, read ${gotStatus}`);
      }
    }
    for (let i = wanted.length; i < slots.length; i++) {
      if (decodeRef(listRecord[slots[i]])) bad.push(`${label}: cloned ${SIDE_FIELD[side]} has an entry at slot ${i}, not in the profile`);
    }
  }
  return bad;
}

// What the staff block did, for the writer's report. `presentation_ids` are the new ids Mu writes back to
// coach_madden_profiles.
export function staffSummary(plan) {
  const byTeam = {};
  for (const x of [...plan.releases, ...plan.assignments, ...plan.creates]) {
    byTeam[x.team] = (byTeam[x.team] ?? 0) + 1;
  }
  return {
    released: plan.releases.length,
    retired: plan.releases.filter(r => r.retire).length,
    assigned: plan.assignments.length,
    created: plan.creates.length,
    by_team: byTeam,
    presentation_ids: plan.creates.map(c => ({ key: c.key, row: c.row, presentation_id: c.presentationId })),
  };
}
