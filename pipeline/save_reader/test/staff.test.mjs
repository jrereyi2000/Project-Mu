// The "staff" save-writer block (staff.mjs) on an in-memory synthetic franchise: release, assign and create plan,
// apply and read back without a save file. The fixture mirrors the measured Madden 27 layout (Spike A/B): Coach table
// 4168, the team seat refs, FranchiseUser wiring, and the CharacterVisuals / CoachingTalents / Talent[] / TalentTier[]
// sub-records. No real save or database is touched.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decodeRef, encodeRef, NO_REF } from '../lib.mjs';
import { applyStaff, planStaff, staffSummary, verifyStaff } from '../staff.mjs';

const COACH = 4168, TEAM = 5934, FRANCHISE_USER = 5000;
const VISUALS = 4215, COACHING_TALENTS = 4304, TALENT_ARRAY = 5713;
const GAMEDAY_TALENT = 4115, SEASON_TALENT = 4112, PLAYSHEET_TALENT = 4251;
const TIER_ARRAY = 5342, TIER = 4097;
const SLOTS = 8;

// ---- synthetic table / record helpers (autoUnempty: writing a field makes an empty record live) ----

const enumField = names => ({ offset: { type: 'int', enum: { members: names.map((name, i) => ({ name, _value: i })) } } });
const intField = (min = 0, max = 1_000_000) => ({ offset: { type: 'int', minValue: min, maxValue: max, length: 19 } });
const boolField = () => ({ offset: { type: 'bool' } });
const strField = (maxLength = 128) => ({ offset: { type: 'string', maxLength } });
const refField = () => ({ offset: { type: 'ref', isReference: true } });

const POSITIONS = ['HeadCoach', 'OffensiveCoordinator', 'DefensiveCoordinator'];
const RATINGS = ['COACH_RATING', 'COACH_QB', 'COACH_RB', 'COACH_WR', 'COACH_OL', 'COACH_DL', 'COACH_LB', 'COACH_DB',
  'COACH_S', 'COACH_K', 'COACH_P', 'COACH_OFFENSE', 'COACH_DEFENSE'];
const TENDENCIES = ['COACH_OFFTENDENCYRUNPASS', 'COACH_OFFTENDENCYAGGRESSCONSERV', 'COACH_DEFTENDENCYAGGRESSCONSERV',
  'COACH_DEFTENDENCYRUNPASS', 'COACH_RBTENDENCY'];

const COACH_DESCRIPTORS = {
  FirstName: strField(64), LastName: strField(64), Name: strField(80), AssetName: strField(64),
  GenericHeadAssetName: strField(64), Portrait_Swappable_Library_Path: strField(64),
  Age: intField(0, 120), Level: intField(0, 99), YearsCoaching: intField(0, 99), Height: intField(0, 120),
  Weight: intField(0, 400), PresentationId: intField(0, 1_000_000), TeamIndex: intField(0, 40),
  PrevTeamIndex: intField(0, 40), ContractLength: intField(0, 10), ContractSalary: intField(0, 100_000),
  ContractYearsRemaining: intField(0, 10), Portrait: intField(0, 100_000), OWNER_COMMENTID: intField(),
  OWNER_COMMENTTYPE: intField(), CurrentPurchasedTalentCosts: intField(),
  Position: enumField(POSITIONS), OriginalPosition: enumField(POSITIONS),
  ContractStatus: enumField(['Signed', 'FreeAgent', 'Retired', 'Deleted', 'Expiring', 'None']),
  Archetype: enumField(['OffensiveGuru', 'DefensiveGenius', 'PersonnelCzar', 'DevelopmentWizard', 'MasterMotivator']),
  COACH_SPECIALTY: enumField(['Quarterbacks', 'RunningBacks', 'Receivers', 'OffensiveLine', 'DefensiveLine',
    'Linebackers', 'Secondary', 'SpecialTeams']),
  Personality: enumField(['Leader', 'Intense', 'Entertainer', 'TeamPlayer', 'Unpredictable']),
  COACH_DEMEANOR: enumField(['Classic', 'Calm', 'Intense']),
  CharacterBodyType: enumField(['Standard', 'Thin', 'Muscular', 'Heavy', 'Freshman']),
  FaceShape: enumField(['Invalid_', 'MustBeUnique', 'ReidAndy']),
  IsUserControlled: boolField(), Portrait_Force_Silhouette: boolField(), IsCreated: boolField(),
  IsImported: boolField(), IsLegend: boolField(), IsMaxLevel: boolField(), Probation: boolField(),
  TraitExpertScout: boolField(),
  StaffTalents: refField(), CharacterVisuals: refField(), OffenseAudibles: refField(), DefenseAudibles: refField(),
  HasTrait: refField(), OffensivePlaybook: refField(), DefensivePlaybook: refField(), OffensiveScheme: refField(),
  DefensiveScheme: refField(), SimOffensivePlaybook: refField(), SimDefensivePlaybook: refField(),
  TeamPhilosophy: refField(), DefaultTeamPhilosophy: refField(),
};
for (const k of [...RATINGS, ...TENDENCIES]) COACH_DESCRIPTORS[k] = intField(0, 99);

const TEAM_DESCRIPTORS = {
  TeamIndex: intField(0, 40), ShortName: strField(16),
  HeadCoach: refField(), OffensiveCoordinator: refField(), DefensiveCoordinator: refField(),
  SpecialTeamsCoach: refField(), UserCharacter: refField(),
};
const USER_DESCRIPTORS = { Team: refField(), UserEntity: refField(), UserIdUpper: intField(0, 2 ** 31) };
const VISUAL_DESCRIPTORS = { RawData: strField(4096), Overflow: intField(-1, 100) };
const CT_DESCRIPTORS = { GamedayTalents: refField(), PlaysheetTalents: refField() };
const TALENT_DESCRIPTORS = {
  TalentInfo: refField(), CurrentTier: intField(0, 3),
  Status: enumField(['None', 'Unlocked', 'Upgraded', 'Mastered', 'Downgraded', 'Lost']),
  IsTalentActive: boolField(), OwnerPosition: enumField(POSITIONS), Tiers: refField(),
};
const TIER_ARRAY_DESCRIPTORS = Object.fromEntries([...Array(4)].map((_, i) => [`Tier${i}`, refField()]));
const TIER_DESCRIPTORS = { TierStatus: enumField(['NotOwned', 'Purchasable', 'Owned', 'Mastered']) };

function record(descriptors, values = {}, isEmpty = false) {
  const target = { isEmpty, fields: descriptors };
  for (const [k, v] of Object.entries(values)) target[k] = v;
  target.getFieldByKey = key => descriptors[key];
  return new Proxy(target, {
    set(obj, key, value) {
      obj[key] = value;
      if (typeof key === 'string' && descriptors[key]) obj.isEmpty = false;
      return true;
    },
  });
}

const table = (id, name, descriptors, records) => ({ header: { tableId: id }, name, records, readRecords: async () => {} });

const coach = over => ({
  FirstName: '', LastName: '', Name: '', Age: 40, Position: 'HeadCoach', OriginalPosition: 'HeadCoach',
  TeamIndex: 32, PrevTeamIndex: 0, ContractStatus: 'FreeAgent', ContractLength: 0, ContractSalary: 0,
  ContractYearsRemaining: 0, Archetype: 'OffensiveGuru', Level: 1, YearsCoaching: 0,
  COACH_SPECIALTY: 'Quarterbacks', Personality: 'TeamPlayer', COACH_DEMEANOR: 'Classic',
  CharacterBodyType: 'Standard', Height: 72, Weight: 10, FaceShape: 'Invalid_',
  GenericHeadAssetName: 'coachhead_M_0001_HS', AssetName: '', PresentationId: 0, Portrait: 0,
  Portrait_Force_Silhouette: false, Portrait_Swappable_Library_Path: 'library_icons_brt',
  IsUserControlled: false, IsCreated: false, IsImported: false, IsLegend: false, IsMaxLevel: false,
  Probation: false, TraitExpertScout: false, OWNER_COMMENTID: 0, OWNER_COMMENTTYPE: 0,
  CurrentPurchasedTalentCosts: 0, COACH_RATING: 50, COACH_QB: 50, COACH_RB: 50, COACH_WR: 50, COACH_OL: 50,
  COACH_DL: 50, COACH_LB: 50, COACH_DB: 50, COACH_S: 50, COACH_K: 50, COACH_P: 50, COACH_OFFENSE: 50,
  COACH_DEFENSE: 50, COACH_OFFTENDENCYRUNPASS: 50, COACH_OFFTENDENCYAGGRESSCONSERV: 50,
  COACH_DEFTENDENCYAGGRESSCONSERV: 50, COACH_DEFTENDENCYRUNPASS: 50, COACH_RBTENDENCY: 50,
  StaffTalents: NO_REF, CharacterVisuals: NO_REF, OffenseAudibles: NO_REF, DefenseAudibles: NO_REF,
  HasTrait: NO_REF, OffensivePlaybook: NO_REF, DefensivePlaybook: NO_REF, OffensiveScheme: NO_REF,
  DefensiveScheme: NO_REF, SimOffensivePlaybook: NO_REF, SimDefensivePlaybook: NO_REF,
  TeamPhilosophy: NO_REF, DefaultTeamPhilosophy: NO_REF, ...over,
});

const emptyTierArray = () => Object.fromEntries([...Array(4)].map((_, i) => [`Tier${i}`, NO_REF]));
const emptyRecord = descriptors => record(descriptors, {}, true);

// Two teams (0 Panthers, 1 Falcons), their staff, two free agents and three empty Coach rows. Team 1 has an open OC
// and DC seat; team 0's seats are full. The template coaches share two CoachingTalents rows, each with a Gameday and a
// Playsheet list in the 8-slot Talent[] table.
function fixture() {
  const talentArrayValues = i => ({ ...Object.fromEntries([...Array(SLOTS)].map((_, n) => [`Talent${n}`, NO_REF])), ...i });
  const tierRow = (row, status) => record(TIER_DESCRIPTORS, { TierStatus: status });
  const tierArray = (row, tiers) => record(TIER_ARRAY_DESCRIPTORS,
    Object.fromEntries(tiers.map((t, i) => [`Tier${i}`, encodeRef(TIER, t)])));
  const talentRow = (tableId, over) => record(TALENT_DESCRIPTORS, over);

  const coachTable = table(COACH, 'Coach', COACH_DESCRIPTORS, [
    record(COACH_DESCRIPTORS, coach({
      FirstName: 'Dave', LastName: 'Canales', Name: 'D. Canales', Age: 45, PresentationId: 100, TeamIndex: 0,
      Position: 'HeadCoach', OriginalPosition: 'HeadCoach', ContractStatus: 'Signed', ContractLength: 4,
      ContractSalary: 300, ContractYearsRemaining: 2, COACH_RATING: 60, IsUserControlled: true,
      StaffTalents: encodeRef(COACHING_TALENTS, 0), CharacterVisuals: encodeRef(VISUALS, 0),
    })),
    record(COACH_DESCRIPTORS, coach({
      FirstName: 'Bradley', LastName: 'Idzik', Name: 'B. Idzik', Age: 44, PresentationId: 101, TeamIndex: 0,
      Position: 'OffensiveCoordinator', OriginalPosition: 'OffensiveCoordinator', ContractStatus: 'Signed',
      ContractLength: 3, ContractSalary: 150, ContractYearsRemaining: 2, COACH_RATING: 55,
      StaffTalents: encodeRef(COACHING_TALENTS, 1), CharacterVisuals: encodeRef(VISUALS, 0),
    })),
    record(COACH_DESCRIPTORS, coach({
      FirstName: 'Darren', LastName: 'Young', Name: 'D. Young', Age: 38, PresentationId: 102, TeamIndex: 0,
      Position: 'DefensiveCoordinator', OriginalPosition: 'DefensiveCoordinator', ContractStatus: 'Signed',
      ContractLength: 2, ContractSalary: 120, ContractYearsRemaining: 1, COACH_RATING: 52,
      StaffTalents: encodeRef(COACHING_TALENTS, 0), CharacterVisuals: encodeRef(VISUALS, 1),
    })),
    record(COACH_DESCRIPTORS, coach({
      FirstName: 'Mike', LastName: 'Vrabel', Name: 'M. Vrabel', Age: 50, PresentationId: 200, TeamIndex: 32,
      Position: 'HeadCoach', OriginalPosition: 'HeadCoach', ContractStatus: 'FreeAgent',
      StaffTalents: encodeRef(COACHING_TALENTS, 0), CharacterVisuals: encodeRef(VISUALS, 0),
    })),
    record(COACH_DESCRIPTORS, coach({
      FirstName: 'Josh', LastName: 'McDaniels', Name: 'J. McDaniels', Age: 49, PresentationId: 201, TeamIndex: 32,
      Position: 'OffensiveCoordinator', OriginalPosition: 'OffensiveCoordinator', ContractStatus: 'FreeAgent',
      StaffTalents: encodeRef(COACHING_TALENTS, 1), CharacterVisuals: encodeRef(VISUALS, 1),
    })),
    record(COACH_DESCRIPTORS, coach({
      FirstName: 'Raheem', LastName: 'Morris', Name: 'R. Morris', Age: 48, PresentationId: 110, TeamIndex: 1,
      Position: 'HeadCoach', OriginalPosition: 'HeadCoach', ContractStatus: 'Signed', ContractLength: 4,
      ContractSalary: 250, ContractYearsRemaining: 3, COACH_RATING: 58, IsUserControlled: true,
      StaffTalents: encodeRef(COACHING_TALENTS, 0), CharacterVisuals: encodeRef(VISUALS, 0),
    })),
    emptyRecord(COACH_DESCRIPTORS), emptyRecord(COACH_DESCRIPTORS), emptyRecord(COACH_DESCRIPTORS),
  ]);

  const teamTable = table(TEAM, 'Team', TEAM_DESCRIPTORS, [
    record(TEAM_DESCRIPTORS, { TeamIndex: 0, ShortName: 'Panthers', HeadCoach: encodeRef(COACH, 0),
      OffensiveCoordinator: encodeRef(COACH, 1), DefensiveCoordinator: encodeRef(COACH, 2),
      SpecialTeamsCoach: NO_REF, UserCharacter: encodeRef(COACH, 0) }),
    record(TEAM_DESCRIPTORS, { TeamIndex: 1, ShortName: 'Falcons', HeadCoach: encodeRef(COACH, 5),
      OffensiveCoordinator: NO_REF, DefensiveCoordinator: NO_REF, SpecialTeamsCoach: NO_REF,
      UserCharacter: encodeRef(COACH, 5) }),
    // A third team with every seat open (a poached head coach can move here).
    record(TEAM_DESCRIPTORS, { TeamIndex: 2, ShortName: 'Bears', HeadCoach: NO_REF, OffensiveCoordinator: NO_REF,
      DefensiveCoordinator: NO_REF, SpecialTeamsCoach: NO_REF, UserCharacter: NO_REF }),
  ]);

  const userTable = table(FRANCHISE_USER, 'FranchiseUser', USER_DESCRIPTORS, [
    record(USER_DESCRIPTORS, { Team: encodeRef(TEAM, 0), UserEntity: encodeRef(COACH, 0), UserIdUpper: 2147483648 }),
    record(USER_DESCRIPTORS, { Team: encodeRef(TEAM, 1), UserEntity: encodeRef(COACH, 5), UserIdUpper: 2164260864 }),
    record(USER_DESCRIPTORS, { Team: encodeRef(TEAM, 2), UserEntity: encodeRef(COACH, 4), UserIdUpper: 2181038080 }),
    emptyRecord(USER_DESCRIPTORS),
  ]);

  const visualsTable = table(VISUALS, 'CharacterVisuals', VISUAL_DESCRIPTORS, [
    record(VISUAL_DESCRIPTORS, { RawData: '{"bodyType":3,"loadouts":[{"loadoutType":183}]}', Overflow: 0 }),
    record(VISUAL_DESCRIPTORS, { RawData: '{"bodyType":1,"loadouts":[]}', Overflow: 0 }),
    emptyRecord(VISUAL_DESCRIPTORS), emptyRecord(VISUAL_DESCRIPTORS),
  ]);

  const talentArrays = table(TALENT_ARRAY, 'Talent[]', Object.fromEntries(
    [...Array(SLOTS)].map((_, i) => [`Talent${i}`, refField()])),
  [
    record(Object.fromEntries([...Array(SLOTS)].map((_, i) => [`Talent${i}`, refField()])),
      talentArrayValues({ Talent0: encodeRef(GAMEDAY_TALENT, 0), Talent1: encodeRef(GAMEDAY_TALENT, 1) })),
    record(Object.fromEntries([...Array(SLOTS)].map((_, i) => [`Talent${i}`, refField()])),
      talentArrayValues({ Talent0: encodeRef(PLAYSHEET_TALENT, 0) })),
    record(Object.fromEntries([...Array(SLOTS)].map((_, i) => [`Talent${i}`, refField()])),
      talentArrayValues({ Talent0: encodeRef(GAMEDAY_TALENT, 2) })),
    record(Object.fromEntries([...Array(SLOTS)].map((_, i) => [`Talent${i}`, refField()])),
      talentArrayValues({ Talent0: encodeRef(PLAYSHEET_TALENT, 1) })),
    ...[...Array(6)].map(() => emptyRecord(talentArraysDescriptors())),
  ]);

  const coachingTalents = table(COACHING_TALENTS, 'CoachingTalents', CT_DESCRIPTORS, [
    record(CT_DESCRIPTORS, { GamedayTalents: encodeRef(TALENT_ARRAY, 0), PlaysheetTalents: encodeRef(TALENT_ARRAY, 1) }),
    record(CT_DESCRIPTORS, { GamedayTalents: encodeRef(TALENT_ARRAY, 2), PlaysheetTalents: encodeRef(TALENT_ARRAY, 3) }),
    emptyRecord(CT_DESCRIPTORS), emptyRecord(CT_DESCRIPTORS), emptyRecord(CT_DESCRIPTORS),
  ]);

  const tierRows = [record(TIER_DESCRIPTORS, { TierStatus: 'NotOwned' }),
    ...[...Array(200)].map(() => emptyRecord(TIER_DESCRIPTORS))];
  const tierArrays = [record(TIER_ARRAY_DESCRIPTORS, emptyTierArray()),
    ...[...Array(100)].map(() => emptyRecord(TIER_ARRAY_DESCRIPTORS))];
  // Every pre-existing talent has a four-slot tier list.
  const gameday = [
    record(TALENT_DESCRIPTORS, { TalentInfo: encodeRef(16458, 35154), CurrentTier: 1, Status: 'None',
      IsTalentActive: false, OwnerPosition: 'Quarterbacks', Tiers: encodeRef(TIER_ARRAY, 0) }),
    record(TALENT_DESCRIPTORS, { TalentInfo: encodeRef(16469, 52914), CurrentTier: 0, Status: 'None',
      IsTalentActive: false, OwnerPosition: 'Quarterbacks', Tiers: encodeRef(TIER_ARRAY, 0) }),
    record(TALENT_DESCRIPTORS, { TalentInfo: encodeRef(16469, 53034), CurrentTier: 2, Status: 'None',
      IsTalentActive: false, OwnerPosition: 'Linebackers', Tiers: encodeRef(TIER_ARRAY, 0) }),
    ...[...Array(20)].map(() => emptyRecord(TALENT_DESCRIPTORS)),
  ];
  const season = [...Array(20)].map(() => emptyRecord(TALENT_DESCRIPTORS));
  const playsheet = [
    record(TALENT_DESCRIPTORS, { TalentInfo: encodeRef(16474, 47010), CurrentTier: 1, Status: 'None',
      IsTalentActive: false, OwnerPosition: 'Quarterbacks', Tiers: encodeRef(TIER_ARRAY, 0) }),
    record(TALENT_DESCRIPTORS, { TalentInfo: encodeRef(16474, 47075), CurrentTier: 0, Status: 'None',
      IsTalentActive: false, OwnerPosition: 'Quarterbacks', Tiers: encodeRef(TIER_ARRAY, 0) }),
    ...[...Array(20)].map(() => emptyRecord(TALENT_DESCRIPTORS)),
  ];

  const tables = [
    coachTable, teamTable, userTable, visualsTable, talentArrays, coachingTalents,
    table(GAMEDAY_TALENT, 'GamedayTalent', TALENT_DESCRIPTORS, gameday),
    table(SEASON_TALENT, 'SeasonTalent', TALENT_DESCRIPTORS, season),
    table(PLAYSHEET_TALENT, 'PlaysheetTalent', TALENT_DESCRIPTORS, playsheet),
    table(TIER_ARRAY, 'TalentTier[]', TIER_ARRAY_DESCRIPTORS, tierArrays),
    table(TIER, 'TalentTier', TIER_DESCRIPTORS, tierRows),
  ];
  const franchise = {
    getTableById: id => tables.find(t => t.header.tableId === id) ?? null,
    getAllTablesByName: name => tables.filter(t => t.name === name),
  };
  return { franchise, coach: coachTable, team: teamTable, users: userTable, visuals: visualsTable,
    coachingTalents, talentArrays, gameday, season, playsheet, tierArrays, tierRows };
}

// The descriptors for a Talent[] row (the fixture builds the table before its descriptor helper is in scope).
function talentArraysDescriptors() {
  return Object.fromEntries([...Array(SLOTS)].map((_, i) => [`Talent${i}`, refField()]));
}

const CONTRACT = { ContractLength: 3, ContractSalary: 450, ContractYearsRemaining: 3 };
const profile = (over = {}) => ({
  first_name: 'Mu', last_name: 'Testcoach', age: 45, position: 'OffensiveCoordinator',
  archetype: 'OffensiveGuru', specialty: 'Quarterbacks', level: 3, years_coaching: 5,
  offensive_playbook: 'Falcons', defensive_playbook: 'Falcons',
  offensive_scheme: 'WestCoastZoneRun', defensive_scheme: 'Tampa2', team_philosophy: null,
  personality: 'TeamPlayer', demeanor: 'Classic', body_type: 'Standard', height: 72, weight: 10,
  face_shape: 'Invalid_', generic_head: 'coachhead_M_0001_HS',
  talents: [
    { slot: 'Gameday', talent: 'Field General', tier: 1 },
    { slot: 'Gameday', talent: 'Dr. Dots', tier: 0 },
    { slot: 'Playsheet', talent: 'West Coast Playsheet', tier: 2 },
  ],
  ratings: { COACH_QB: 70, COACH_RATING: 62 },
  tendencies: { COACH_OFFTENDENCYRUNPASS: 55 },
  visuals_template: 100, portrait_id: 0, ...over,
});

const workspace = f => JSON.stringify({
  coaches: f.coach.records.map(r => ({ empty: r.isEmpty, pgid: r.PresentationId, team: r.TeamIndex,
    status: r.ContractStatus, position: r.Position, years: r.ContractYearsRemaining, rating: r.COACH_RATING,
    control: r.IsUserControlled })),
  teams: f.team.records.map(r => [r.HeadCoach, r.OffensiveCoordinator, r.DefensiveCoordinator, r.UserCharacter]),
  users: f.users.records.map(r => [r.Team, r.UserEntity]),
});

test('create fills an empty Coach row and clones the template visuals and talents', async () => {
  const f = fixture();
  const result = await planStaff(f.franchise, [{ action: 'create', key: 'coach-311', team_index: 1,
    position: 'OffensiveCoordinator', profile: profile(), contract: CONTRACT }]);
  assert.deepEqual(result.errors, []);
  const create = result.plan.creates[0];
  assert.equal(create.row, 6, 'the first empty Coach row');
  assert.equal(create.presentationId, 202, 'max(existing PresentationId) + 1');
  assert.deepEqual(staffSummary(result.plan).presentation_ids,
    [{ key: 'coach-311', row: 6, presentation_id: 202 }]);

  await applyStaff(result.plan);
  assert.deepEqual(await verifyStaff(f.franchise, result.plan), []);

  const record = f.coach.records[6];
  assert.equal(record.isEmpty, false, 'the empty row is live');
  assert.deepEqual([record.FirstName, record.LastName, record.Name, record.Age], ['Mu', 'Testcoach', 'M. Testcoach', 45]);
  assert.deepEqual([record.TeamIndex, record.ContractStatus, record.Position, record.OriginalPosition],
    [1, 'Signed', 'OffensiveCoordinator', 'OffensiveCoordinator']);
  assert.deepEqual([record.ContractLength, record.ContractSalary, record.ContractYearsRemaining], [3, 450, 3]);
  assert.deepEqual([record.Level, record.YearsCoaching, record.COACH_SPECIALTY, record.Personality, record.COACH_DEMEANOR],
    [3, 5, 'Quarterbacks', 'TeamPlayer', 'Classic']);
  // Spike C in game: Portrait 0 with the silhouette flag renders nothing, so a coach with no face carries the
  // game's own placeholder (the value both placeholder coach rows use).
  assert.deepEqual([record.Portrait, record.Portrait_Force_Silhouette], [7999, false], 'no portrait: placeholder');
  assert.equal(record.IsCreated, true, 'a created coach is marked created');
  assert.equal(record.AssetName, '', 'a coordinator has no model asset key (94 of 127 real ones)');
  assert.equal(record.IsUserControlled, false, 'a coordinator is not the user');
  assert.equal(f.team.records[1].OffensiveCoordinator, encodeRef(COACH, 6));

  // CharacterVisuals: a new row with a plain-JSON copy of the template's RawData.
  const visuals = decodeRef(record.CharacterVisuals);
  assert.ok(visuals && visuals.row !== 0, 'a new CharacterVisuals row');
  assert.equal(f.visuals.records[visuals.row].RawData, f.visuals.records[0].RawData);

  // Talents: the cloned lists hold exactly the profile's talents, each a new row with a four-slot tier list.
  const ct = f.coachingTalents.records[decodeRef(record.StaffTalents).row];
  const readList = ref => f.talentArrays.records[decodeRef(ref).row];
  const gameday = readList(ct.GamedayTalents), playsheet = readList(ct.PlaysheetTalents);
  const talentOf = (list, slot) => {
    const d = decodeRef(list[`Talent${slot}`]);
    const t = f.franchise.getTableById(d.table).records[d.row];
    return { info: t.TalentInfo, tier: t.CurrentTier, tiers: t.Tiers };
  };
  const [g0, g1] = [talentOf(gameday, 0), talentOf(gameday, 1)];
  assert.deepEqual([g0.info, g0.tier], [encodeRef(16458, 35154), 1], 'Field General');
  assert.deepEqual([g1.info, g1.tier], [encodeRef(16469, 52914), 0], 'Dr. Dots');
  assert.equal(gameday.Talent2, NO_REF, 'the template had more slots; the profile has two Gameday talents');
  const p0 = talentOf(playsheet, 0);
  assert.deepEqual([p0.info, p0.tier], [encodeRef(16474, 47010), 2], 'West Coast Playsheet');
  assert.equal(playsheet.Talent1, NO_REF);
  // Tier statuses: 0..tier Owned, the rest Purchasable.
  const tierStatuses = (tiers) => {
    const array = f.franchise.getTableById(TIER_ARRAY).records[decodeRef(tiers).row];
    return [...Array(4)].map((_, i) => f.franchise.getTableById(TIER).records[decodeRef(array[`Tier${i}`]).row].TierStatus);
  };
  assert.deepEqual(tierStatuses(g0.tiers), ['Owned', 'Owned', 'Purchasable', 'Purchasable']);
  assert.deepEqual(tierStatuses(g1.tiers), ['Owned', 'Purchasable', 'Purchasable', 'Purchasable']);
  assert.deepEqual(tierStatuses(p0.tiers), ['Owned', 'Owned', 'Owned', 'Purchasable']);
});

test('assign moves a record and its Team ref, and writes Position/OriginalPosition on a role change', async () => {
  const f = fixture();
  const result = await planStaff(f.franchise, [
    { action: 'assign', pgid: 201, team_index: 1, position: 'OffensiveCoordinator', contract: CONTRACT },
    // A former head coach hired as a coordinator: both Position and OriginalPosition change.
    { action: 'assign', pgid: 200, team_index: 1, position: 'DefensiveCoordinator', contract: CONTRACT },
  ]);
  assert.deepEqual(result.errors, []);
  await applyStaff(result.plan);
  assert.deepEqual(await verifyStaff(f.franchise, result.plan), []);

  const oc = f.coach.records[4];
  assert.deepEqual([oc.TeamIndex, oc.ContractStatus, oc.Position, oc.ContractLength], [1, 'Signed', 'OffensiveCoordinator', 3]);
  assert.equal(f.team.records[1].OffensiveCoordinator, encodeRef(COACH, 4));

  const dc = f.coach.records[3];
  assert.deepEqual([dc.TeamIndex, dc.ContractStatus, dc.Position, dc.OriginalPosition],
    [1, 'Signed', 'DefensiveCoordinator', 'DefensiveCoordinator']);
  assert.equal(f.team.records[1].DefensiveCoordinator, encodeRef(COACH, 3));
  assert.equal(dc.IsUserControlled, false, 'a coordinator is not user-controlled');
  // The head coach seat and user wiring are untouched by coordinator assignments.
  assert.equal(f.team.records[1].HeadCoach, encodeRef(COACH, 5));
  assert.equal(f.team.records[1].UserCharacter, encodeRef(COACH, 5));
  assert.equal(f.users.records[1].UserEntity, encodeRef(COACH, 5));
});

test('assign takes an optional set of further Coach fields, validated like a players edit', async () => {
  const f = fixture();
  const result = await planStaff(f.franchise, [
    { action: 'assign', pgid: 201, team_index: 1, position: 'OffensiveCoordinator', contract: CONTRACT,
      set: { Portrait: 5324, Portrait_Force_Silhouette: false } },
  ]);
  assert.deepEqual(result.errors, []);
  await applyStaff(result.plan);
  assert.deepEqual(await verifyStaff(f.franchise, result.plan), []);
  const oc = f.coach.records[4];
  assert.deepEqual([oc.Portrait, oc.Portrait_Force_Silhouette], [5324, false],
    'the optional set wrote the portrait fields');

  const refuse = async set => (await planStaff(f.franchise, [
    { action: 'assign', pgid: 201, team_index: 1, position: 'OffensiveCoordinator', contract: CONTRACT, set },
  ])).errors.join('\n');
  assert.match(await refuse({ NotAField: 1 }), /no such Coach field/);
  assert.match(await refuse({ Portrait: 'seven' }), /set Portrait = "seven": must be an integer/);
  assert.match(await refuse({ TeamIndex: 0 }), /may not set TeamIndex/);
});

test('release clears the seat ref and leaves the record as a free agent (or Retired)', async () => {
  const f = fixture();
  const result = await planStaff(f.franchise, [{ action: 'release', pgid: 101 }]);
  assert.deepEqual(result.errors, []);
  await applyStaff(result.plan);
  assert.deepEqual(await verifyStaff(f.franchise, result.plan), []);

  const record = f.coach.records[1];
  assert.deepEqual([record.TeamIndex, record.PrevTeamIndex, record.ContractStatus, record.ContractYearsRemaining,
    record.COACH_RATING, record.IsUserControlled], [32, 0, 'FreeAgent', 0, 50, false]);
  assert.equal(record.Position, 'OffensiveCoordinator', 'a release leaves Position alone');
  assert.equal(f.team.records[0].OffensiveCoordinator, NO_REF, 'the team seat no longer points at him');
  assert.equal(f.team.records[0].HeadCoach, encodeRef(COACH, 0), 'the head coach is untouched');

  // A free agent unhired for three seasons retires (D8): same writes, Retired instead of FreeAgent.
  const retire = await planStaff(f.franchise, [{ action: 'release', pgid: 200, retire: true }]);
  assert.deepEqual(retire.errors, []);
  await applyStaff(retire.plan);
  assert.deepEqual(await verifyStaff(f.franchise, retire.plan), []);
  const retired = f.coach.records[3];
  assert.deepEqual([retired.ContractStatus, retired.TeamIndex], ['Retired', 32]);
  // Releasing an already-free agent without retire is refused.
  const again = await planStaff(f.franchise, [{ action: 'release', pgid: 101 }]);
  assert.match(again.errors.join('\n'), /already a free agent/);
});

test('the HC re-point keeps HeadCoach = UserCharacter = UserEntity, the league_setup user check', async () => {
  const f = fixture();
  const result = await planStaff(f.franchise, [
    { action: 'release', pgid: 100 },
    { action: 'assign', pgid: 200, team_index: 0, position: 'HeadCoach', contract: CONTRACT },
  ]);
  assert.deepEqual(result.errors, []);
  await applyStaff(result.plan);
  assert.deepEqual(await verifyStaff(f.franchise, result.plan), []);

  const team = f.team.records[0];
  const seat = decodeRef(team.HeadCoach);
  const same = (a, b) => a && b && a.table === b.table && a.row === b.row;
  assert.deepEqual(seat, { table: COACH, row: 3 });
  assert.ok(same(decodeRef(team.UserCharacter), seat), 'UserCharacter is the new head coach');
  const user = f.users.records[0];
  assert.ok(same(decodeRef(user.UserEntity), seat), 'FranchiseUser.UserEntity is the new head coach');
  assert.ok(same(decodeRef(user.Team), { table: TEAM, row: 0 }), 'FranchiseUser.Team is restored');
  assert.equal(f.coach.records[3].IsUserControlled, true, 'the new head coach is user-controlled');
  assert.equal(f.coach.records[0].IsUserControlled, false, 'the released head coach is not');
  assert.equal(f.coach.records[0].ContractStatus, 'FreeAgent');
});

test('an occupied seat refuses, and one pgid cannot be in two actions', async () => {
  const f = fixture();
  const occupied = await planStaff(f.franchise, [{ action: 'assign', pgid: 200, team_index: 0,
    position: 'HeadCoach', contract: CONTRACT }]);
  assert.match(occupied.errors.join('\n'), /not released or moved in this write/);

  const occupiedCreate = await planStaff(f.franchise, [{ action: 'create', key: 'coach-1', team_index: 0,
    position: 'HeadCoach', profile: profile({ position: 'HeadCoach' }), contract: CONTRACT }]);
  assert.match(occupiedCreate.errors.join('\n'), /not released or moved in this write/);

  const twice = await planStaff(f.franchise, [
    { action: 'release', pgid: 100 },
    { action: 'assign', pgid: 100, team_index: 0, position: 'OffensiveCoordinator', contract: CONTRACT },
  ]);
  assert.match(twice.errors.join('\n'), /in more than one staff action/);

  const filledTwice = await planStaff(f.franchise, [
    { action: 'release', pgid: 100 },
    { action: 'assign', pgid: 200, team_index: 0, position: 'HeadCoach', contract: CONTRACT },
    { action: 'assign', pgid: 110, team_index: 0, position: 'HeadCoach', contract: CONTRACT },
  ]);
  assert.match(filledTwice.errors.join('\n'), /filled twice in this write/);
});

test('a poached move clears the old team seat with no release edit', async () => {
  const f = fixture();
  const result = await planStaff(f.franchise, [{ action: 'assign', pgid: 101, team_index: 1,
    position: 'OffensiveCoordinator', contract: CONTRACT }]);
  assert.deepEqual(result.errors, []);
  assert.deepEqual(result.plan.assignments[0].clearSeats.map(s => [s.team.teamIndex, s.key]),
    [[0, 'OffensiveCoordinator']], 'the stale seat is part of the assign');
  await applyStaff(result.plan);
  assert.deepEqual(await verifyStaff(f.franchise, result.plan), []);
  assert.equal(f.team.records[0].OffensiveCoordinator, NO_REF, 'the old team seat is cleared');
  assert.equal(f.team.records[1].OffensiveCoordinator, encodeRef(COACH, 1), 'the new team seat points at him');
  assert.equal(f.coach.records[1].TeamIndex, 1);
});

test('a retread head coach moves teams and the target user follows him', async () => {
  const f = fixture();
  const result = await planStaff(f.franchise, [{ action: 'assign', pgid: 100, team_index: 2,
    position: 'HeadCoach', contract: CONTRACT }]);
  assert.deepEqual(result.errors, []);
  await applyStaff(result.plan);
  assert.deepEqual(await verifyStaff(f.franchise, result.plan), []);

  const team = f.team.records[2];
  const seat = decodeRef(team.HeadCoach);
  assert.deepEqual(seat, { table: COACH, row: 0 });
  assert.deepEqual(decodeRef(team.UserCharacter), seat);
  assert.deepEqual(decodeRef(f.users.records[2].UserEntity), seat, 'the target team user points at him');
  assert.deepEqual(decodeRef(f.users.records[2].Team), { table: TEAM, row: 2 });
  assert.equal(f.coach.records[0].IsUserControlled, true);
  assert.equal(f.team.records[0].HeadCoach, NO_REF, 'the old team seat is cleared');
});

test('two head coaches swapping teams are both treated as vacating, and stale clears do not undo fills', async () => {
  const f = fixture();
  // Team 1's coach is assigned to team 0 before team 0's coach is assigned to team 1: without mover-aware seat
  // occupation the first assign would refuse ("held by...").
  const result = await planStaff(f.franchise, [
    { action: 'assign', pgid: 110, team_index: 0, position: 'HeadCoach', contract: CONTRACT },
    { action: 'assign', pgid: 100, team_index: 1, position: 'HeadCoach', contract: CONTRACT },
  ]);
  assert.deepEqual(result.errors, []);
  await applyStaff(result.plan);
  assert.deepEqual(await verifyStaff(f.franchise, result.plan), []);
  assert.deepEqual(decodeRef(f.team.records[0].HeadCoach), { table: COACH, row: 5 });
  assert.deepEqual(decodeRef(f.team.records[1].HeadCoach), { table: COACH, row: 0 });
  assert.deepEqual(decodeRef(f.users.records[0].UserEntity), { table: COACH, row: 5 });
  assert.deepEqual(decodeRef(f.users.records[0].Team), { table: TEAM, row: 0 });
  assert.deepEqual(decodeRef(f.users.records[1].UserEntity), { table: COACH, row: 0 });
  assert.deepEqual(decodeRef(f.users.records[1].Team), { table: TEAM, row: 1 });
  assert.equal(f.coach.records[5].IsUserControlled, true);
  assert.equal(f.coach.records[0].IsUserControlled, true);
});

test('a head-coach assign re-points an orphaned user row when the team has none', async () => {
  const f = fixture();
  // Madden's own firing state: team 2's user row lost its Team, and the man it followed is a free agent.
  f.users.records[2].Team = NO_REF;
  f.users.records[2].UserEntity = encodeRef(COACH, 4);
  const result = await planStaff(f.franchise, [{ action: 'assign', pgid: 100, team_index: 2,
    position: 'HeadCoach', contract: CONTRACT }]);
  assert.deepEqual(result.errors, []);
  await applyStaff(result.plan);
  assert.deepEqual(await verifyStaff(f.franchise, result.plan), []);
  assert.deepEqual(decodeRef(f.users.records[2].UserEntity), { table: COACH, row: 0 });
  assert.deepEqual(decodeRef(f.users.records[2].Team), { table: TEAM, row: 2 });
});

test('one bad edit plans nothing and writes nothing', async () => {
  const f = fixture();
  const before = workspace(f);
  const result = await planStaff(f.franchise, [
    { action: 'release', pgid: 101 },
    { action: 'assign', pgid: 201, team_index: 1, position: 'OffensiveCoordinator',
      contract: { ContractLength: 0, ContractSalary: 100, ContractYearsRemaining: 1 } },
  ]);
  assert.ok(result.errors.length > 0);
  assert.match(result.errors.join('\n'), /ContractLength/);
  assert.equal(workspace(f), before, 'planning changes nothing');

  const badTalent = await planStaff(f.franchise, [{ action: 'create', key: 'coach-bad', team_index: 1,
    position: 'OffensiveCoordinator', profile: profile({ talents: [{ slot: 'Gameday', talent: 'Not A Talent', tier: 0 }] }),
    contract: CONTRACT }]);
  assert.match(badTalent.errors.join('\n'), /unknown talent/);
  assert.equal(workspace(f), before);
});

test('the read-back catches a mismatch', async () => {
  const f = fixture();
  const result = await planStaff(f.franchise, [{ action: 'create', key: 'coach-311', team_index: 1,
    position: 'OffensiveCoordinator', profile: profile(), contract: CONTRACT }]);
  assert.deepEqual(result.errors, []);
  await applyStaff(result.plan);
  assert.deepEqual(await verifyStaff(f.franchise, result.plan), []);

  f.coach.records[6].FirstName = 'Corrupted';
  assert.match((await verifyStaff(f.franchise, result.plan)).join('\n'), /FirstName: wrote Mu, read Corrupted/);
  f.team.records[1].OffensiveCoordinator = NO_REF;
  assert.match((await verifyStaff(f.franchise, result.plan)).join('\n'), /does not point at the record/);

  // A head-coach seat's user wiring is part of the read-back too.
  const hc = await planStaff(f.franchise, [
    { action: 'release', pgid: 110 },
    { action: 'assign', pgid: 200, team_index: 1, position: 'HeadCoach', contract: CONTRACT },
  ]);
  assert.deepEqual(hc.errors, []);
  await applyStaff(hc.plan);
  f.team.records[1].UserCharacter = NO_REF;
  assert.match((await verifyStaff(f.franchise, hc.plan)).join('\n'), /Team\.UserCharacter is not the head coach/);
});
