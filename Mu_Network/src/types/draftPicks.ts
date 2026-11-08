/**
 * JSON contract for the compensatory draft pick additions (docs/in_progress/COMPENSATORY_PICKS_PLAN.md U3,
 * PROJECT_SPEC §3.3c / §4.6 / §5.2). These shapes mirror `server/draft_api.py`:
 *
 *   GET /api/draft/compensatory?draft_season=&season=  -> CompensatoryResponse
 *
 * and the additive fields now carried by each pick in `GET /api/draft/team-needs` (`CompPickFields`).
 * A standard pick has `is_compensatory: false` and an empty `pick_suffix`; a compensatory pick has
 * `source` COMP/JC2A, a non-empty `pick_suffix`, and a `comp_slot`.
 */

export type DraftPickSource = "SEED" | "TRADE" | "JR" | "COMP" | "JC2A" | string;
export type CompAwardKind = "CBA" | "JC2A";

/** One official compensatory pick award (`compensatory_pick_awards` joined to teams). */
export type CompensatoryAward = {
  id: number;
  draft_season: number;
  team_id: number;
  team: string;                 // abbreviation, e.g. "SF"
  team_name: string;            // "San Francisco 49ers"
  round: number;                // 3-7
  slot_in_round: number;        // the pick's position in the round (33 = first comp after the 32 standard)
  slot: number;                 // alias of slot_in_round
  overall_pick: number;
  award_kind: CompAwardKind;
  reason: string;               // the stored explanation, e.g. "Lost QB X (Round 3 tier)"
  source_player_id: number | null;
  source_player: string | null;
  source_coach_id: number | null;
  source_coach: string | null;
};

/** `GET /api/draft/compensatory` response. `awards` is [] when the table is absent or the class has none. */
export type CompensatoryResponse = {
  season: number;
  draft_season: number;
  total: number;
  awards: CompensatoryAward[];
};

/** Additive comp-pick fields on a `team-needs` pick object. */
export type CompPickFields = {
  source?: DraftPickSource;
  pick_suffix?: string;
  is_compensatory?: boolean;
  comp_slot?: number | null;
  award_kind?: string | null;
  award_reason?: string | null;
};
