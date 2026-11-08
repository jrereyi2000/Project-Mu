/**
 * JSON contract for the NFLPA team report card (NFLPA_REPORT_CARD_PLAN §2.5, U3; PROJECT_SPEC §4.2 / §5.2).
 * These are the shapes `server/front_office_api.py` implements under `/api/team/{abbr}`; the panel is built
 * against them, so a fetch that hasn't landed yet fails softly (a 404 reads "unavailable", never a crash).
 *
 *   GET  /api/team/{abbr}/report-card                 -> ReportCardResponse
 *   POST /api/team/{abbr}/facilities/upgrade          UpgradeRequest -> UpgradeResponse
 *   POST /api/team/{abbr}/facilities/policy           PolicyRequest -> PolicyResponse
 *
 * Grades are the engine's 14-grade letter scale (A+ … F-); money is whole dollars. A block the migration hasn't
 * created yet is `null` (the page reads it as "no data yet", never a fabricated grade).
 */

// ---- Shared values -------------------------------------------------------------------------------------------

/** The eight surveyed categories (`engines/report_card_engine.py` CATEGORIES). */
export type ReportCardCategory =
  | "weight_room"
  | "training_room"
  | "locker_room"
  | "food_nutrition"
  | "team_travel"
  | "family_treatment"
  | "head_coach"
  | "ownership";

/** The CapEx renovation tiers (engine CAPEX_TIERS). */
export type CapExTier = "MINOR" | "STANDARD" | "OVERHAUL";

/** The three annual OpEx policy toggles (engine OPEX_POLICIES). */
export type PolicyKey = "luxury_travel" | "family_daycare" | "private_chef";

// ---- GET /api/team/{abbr}/report-card ------------------------------------------------------------------------

/** One `team_report_cards` row: the eight grades, the composite and the summary text. */
export type ReportCard = {
  season: number;
  weight_room: string;
  training_room: string;
  locker_room: string;
  food_nutrition: string;
  team_travel: string;
  family_treatment: string;
  head_coach: string;
  ownership: string;
  overall_score: number;
  overall_grade: string;
  summary_text: string | null;
};

/** The current card's place among the league's cards for that season. */
export type LeagueRank = {
  rank: number;        // 1 = best overall score; ties share the top rank
  percentile: number;  // share of the league at or below this team, 0-100 (one decimal)
  teams: number;       // teams with a card that season
};

/** The team's three OpEx toggles and their summed annual cost. */
export type OperatingPolicies = {
  luxury_travel: boolean;
  family_daycare: boolean;
  private_chef: boolean;
  annual_opex_cost: number;
};

/** One open (IN_PROGRESS) CapEx renovation project. */
export type FacilityUpgrade = {
  id: number;
  season_ordered: number;
  category: string;
  cost_dollars: number;
  target_grade: string;
  completion_season: number;
  status: string;
};

export type ReportCardResponse = {
  season: number | null;
  team: { id: number; name: string; abbreviation: string };
  cards: ReportCard[] | null;      // newest season first; null without the migration or rows
  current: ReportCard | null;      // the requested season's card
  rank: LeagueRank | null;
  policies: OperatingPolicies | null;
  upgrades: FacilityUpgrade[] | null;
};

// ---- POST /api/team/{abbr}/facilities/upgrade ----------------------------------------------------------------

export type UpgradeRequest = { category: ReportCardCategory; tier: CapExTier };

export type UpgradeResponse = { team: string; season: number; order: FacilityUpgrade };

// ---- POST /api/team/{abbr}/facilities/policy -----------------------------------------------------------------

export type PolicyRequest = { policy: PolicyKey; enabled: boolean };

export type PolicyResponse = {
  team: string;
  season: number;
  policy: PolicyKey;
  enabled: boolean;
  annual_opex_cost: number;
  policies: Record<PolicyKey, boolean>;
  category: ReportCardCategory;
  grade: string;
  overall_score: number;
  overall_grade: string;
};
