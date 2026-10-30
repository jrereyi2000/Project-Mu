/**
 * JSON contract for the #franchise Depth Chart, Roster & Cap and Training Room modules (V2_CORE_BUILD_PLAN §2.7).
 *
 * These are the shapes U14 (`server/franchise_api.py`, prefix `/api/franchise`) must implement; the orchestrator
 * copies them into the plan. Money is whole dollars (not Madden's $10,000 units), `null` means "not recorded"
 * (shown as "—", never $0). Depth-chart slots use the save's 35 DepthChart field names exactly
 * (`engines/depth_chart_engine.POSITION_SLOTS`).
 *
 * Errors: any non-2xx answer carries FastAPI's `{"detail": string}`. A 404 on any of these paths means the franchise
 * API isn't mounted yet (or the team is unknown) and the modules show an "not available yet" state.
 *
 *   GET  /api/franchise/{abbr}/depth-chart            -> DepthChartResponse
 *   PUT  /api/franchise/{abbr}/depth-chart            DepthChartSaveRequest -> QueuedMoveResponse (kind DEPTH_CHART)
 *   POST /api/franchise/{abbr}/depth-chart/staff      {} -> DepthChartResponse (source MU_COACH, nothing stored)
 *   GET  /api/franchise/{abbr}/roster                 -> RosterResponse
 *   GET  /api/franchise/{abbr}/cap?years=5            -> CapResponse
 *   POST /api/franchise/{abbr}/moves/preview          MoveRequest -> MovePreview
 *   POST /api/franchise/{abbr}/moves                  MoveRequest -> QueuedMoveResponse (409 {"detail"} when illegal)
 *   GET  /api/franchise/{abbr}/medical                -> MedicalResponse
 */

// ---- Depth chart ---------------------------------------------------------------------------------------------

/** The save's 35 DepthChart field names, in Madden's order (`depth_chart_engine.POSITION_SLOTS`). */
export const DEPTH_CHART_SLOTS = [
  "3DRB", "C", "CB", "DT", "FB", "FS", "GAD", "HB", "K", "KOS", "KR", "LE", "LG", "LOLB", "LS", "LT", "MLB",
  "NT", "P", "PR", "PWHB", "QB", "RDT", "RE", "RG", "RLE", "ROLB", "RRE", "RT", "SLCB", "SLWR", "SS", "SUBLB",
  "TE", "WR",
] as const;
export type DepthChartSlot = (typeof DEPTH_CHART_SLOTS)[number];

/** Players per slot in the save's 6-entry `Player[]` record. */
export const DEPTH_PER_SLOT = 6;

/** Who set the chart: Madden's own (read from the save), the coaching staff (`depth_chart_engine`), or JR. */
export type DepthChartSource = "MADDEN" | "MU_COACH" | "JR";

export type DepthChartEntry = {
  player_id: number;
  name: string;
  position: string;          // the player's roster position (may differ from the slot, e.g. a CB at KR)
  overall: number | null;
  injured: boolean;
};

export type DepthChartResponse = {
  team: string;              // abbreviation
  season: number;
  week: number;
  source: DepthChartSource;
  /** A JR chart queued and not yet written to the save; `slots` already shows it. Absent/null when none. */
  pending: QueuedMoveResponse | null;
  /** Every one of the 35 slots is present, best first, 0–6 entries each. */
  slots: Record<string, DepthChartEntry[]>;
};

/** PUT body: slot -> player_ids best first (at most 6). All 35 slots are sent. */
export type DepthChartSaveRequest = { slots: Record<string, number[]> };

// ---- Moves (shared by Roster & Cap and Training Room) --------------------------------------------------------

export type MoveKind = "RELEASE" | "PS_PROMOTE" | "PS_DEMOTE" | "IR_PLACE" | "IR_ACTIVATE";

/** §2.7 `{kind, payload}`; `post_june1` only matters for RELEASE (defaults to false). */
export type MoveRequest = { kind: MoveKind; payload: { player_id: number; post_june1?: boolean } };

export type MovePreview = {
  kind: MoveKind;
  player_id: number;
  name: string;
  cap_hit: number | null;              // this season's cap hit before the move
  cap_savings: number;                 // cap_hit − dead_money this season (negative = the move costs cap room)
  dead_money: number;                  // charged this season
  dead_money_next_year: number;        // charged next season (post-June 1 split), else 0
  cap_room_before: number | null;
  cap_room_after: number | null;
  active_count_after: number;          // active roster size if the move goes through
  practice_squad_count_after: number;
  legal: boolean;
  reasons: string[];                   // why it is illegal, or notes (e.g. "PS eligibility: 2 seasons accrued")
};

export type MoveStatus = "PENDING" | "WRITTEN" | "REFUSED" | "CANCELLED";

/** A row of `franchise_pending_moves`. */
export type QueuedMoveResponse = {
  move_id: number;
  kind: MoveKind | "DEPTH_CHART";
  status: MoveStatus;
  created_at: string;                  // ISO timestamp
  player_id: number | null;            // null for DEPTH_CHART
};

// ---- Roster -------------------------------------------------------------------------------------------------

export type RosterContract = {
  length: number;
  years_left: number;
  through: number;                     // last season of the deal
  total_value: number | null;
  guaranteed: number | null;
  contract_type: string;               // player_contracts.contract_type (STANDARD, ROOKIE, ...)
};

export type RosterPlayer = {
  player_id: number;
  name: string;
  position: string;
  age: number | null;
  overall: number | null;
  injury: string | null;               // null when healthy
  weeks_out: number | null;
  contract: RosterContract | null;     // null for practice-squad players (they have no contract row in the save)
  cap_hit: number | null;              // this season
  ps_eligible: boolean;                // may be demoted to the practice squad
  ir_eligible: boolean;                // injured enough to be placed on IR
  pending_move: MoveKind | null;       // a queued, unwritten move for this player
};

export type RosterResponse = {
  team: string;
  season: number;
  week: number;
  limits: { active: number; practice_squad: number };   // 65 and 12 (D5)
  active: RosterPlayer[];
  practice_squad: RosterPlayer[];
  injured_reserve: RosterPlayer[];
};

// ---- Cap ----------------------------------------------------------------------------------------------------

export type CapYear = {
  season: number;
  cap: number;                         // league cap (projected with contracts_engine.next_cap after this season)
  committed: number;                   // sum of cap hits under contract
  dead_money: number;
  rookie_reserve: number;
  room: number;                        // cap − committed − dead_money − rookie_reserve
  estimated: boolean;                  // true for projected future seasons
};

export type CapResponse = { team: string; season: number; years: CapYear[] };

// ---- Medical ------------------------------------------------------------------------------------------------

export type InjuryStatus = "OUT" | "DOUBTFUL" | "QUESTIONABLE" | "HEALTHY";

export type MedicalPlayer = {
  player_id: number;
  name: string;
  position: string;
  overall: number | null;
  injury: string | null;               // null for HEALTHY
  weeks_out: number;                   // Project Mu's duration (injuries.mu_weeks_out), 0 when healthy
  status: InjuryStatus;
  on_ir: boolean;
  ir_eligible: boolean;
  pending_move: MoveKind | null;
};

export type MedicalResponse = { team: string; season: number; week: number; players: MedicalPlayer[] };
