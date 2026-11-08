/**
 * JSON contract for the #franchise Free Agency, Contract Room and Trade Machine modules (V2_CORE_BUILD_PLAN §2.7,
 * U15). These are the shapes `server/franchise_api.py` (prefix `/api/franchise`) must implement; the orchestrator
 * reconciles them with U14's module docstring.
 *
 * Same style as §2.7a (`types/franchiseRoster.ts`): money is whole dollars (not Madden's $10,000 units), `null`
 * means "not recorded" (shown as "—", never $0). Errors are FastAPI's `{"detail": string}`; a 404 means the
 * franchise API isn't mounted yet (or the team / player is unknown).
 *
 *   GET  /api/franchise/free-agents?position&min_ovr&max_age&team  -> FreeAgentsResponse
 *   POST /api/franchise/{abbr}/offers                              OfferRequest -> NegotiationResponse
 *   GET  /api/franchise/{abbr}/contracts/expiring                  -> ExpiringResponse
 *   POST /api/franchise/{abbr}/contracts/{player_id}/propose       ProposeRequest -> NegotiationResponse
 *   POST /api/franchise/{abbr}/contracts/{player_id}/restructure-preview
 *                                                                  RestructureRequest -> RestructurePreview
 *   GET  /api/franchise/{abbr}/picks?seasons=3                     -> PicksResponse   (not in the §2.7 table yet)
 *   POST /api/franchise/{abbr}/trades/evaluate                     TradeRequest -> TradeEvaluation
 *   GET  /api/franchise/offers/{offer_id}/policy                   -> PolicyReceipt (contract-policy receipt)
 *   GET  /api/franchise/trade-talks?season=                        -> TradeTalksResponse (published CPU transcripts)
 *   GET  /api/franchise/trade-talks/{id}                           -> PublishedTradeTalk
 *
 * The Trade Machine also reads `GET /api/franchise/{abbr}/roster` (RosterResponse, §2.7a) for both teams.
 */
import type { RosterContract } from "./franchiseRoster";

/** The database's 32 team abbreviations (`teams.abbreviation`: AZ, not ARI). */
export const TEAM_ABBRS = [
  "AZ", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
  "LV", "LAC", "LAR", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WAS",
] as const;

// ---- Negotiation (free agents and extensions share one answer) ------------------------------------------------

/** Agent / player answer. Stored in `contract_offers.status` as ACCEPTED / REJECTED / COUNTERED. */
export type NegotiationStatus = "ACCEPT" | "REJECT" | "COUNTER";

/** The contract terms of an offer, a proposal, an asking price or a counter. */
export type ContractTerms = {
  years: number;          // 1..5
  total_value: number;
  bonus: number;          // signing bonus, prorated over min(years, 5)
  guaranteed: number;
};

/**
 * `free_agency_engine.offer_utility` scores, each 0..1 except `persona`: the signed personality adjustment
 * (PERSONALITY_CONTRACT_POLICY_PLAN), 0 unless the contract policy is active; it can be negative.
 * U = 0.45·financial + 0.25·opportunity + 0.20·contention + 0.10·scheme + persona (OFFER_WEIGHTS). U can exceed 1.
 */
export type UtilityParts = {
  financial: number;
  opportunity: number;
  contention: number;
  scheme: number;
  persona: number;
};

/** `free_agency_engine.W_DEFAULT`; the UI multiplies each part by its weight to draw the breakdown bar. */
export const OFFER_WEIGHTS: Record<Exclude<keyof UtilityParts, "persona">, number> = {
  financial: 0.45, opportunity: 0.25, contention: 0.2, scheme: 0.1,
};

/**
 * The server's explanation of the personality adjustment (present only while the contract policy is shadow or
 * active). `delta` is signed and already capped; in shadow it is computed but not applied (`applied` false).
 */
export type PolicyExplanation = {
  mode: "shadow" | "active";
  applied: boolean;
  base_utility: number;
  adjusted_utility: number;
  delta: number;                       // signed, capped (± aggregate cap)
  raw_delta: number;
  cap_applied: boolean;
  terms: { ambition: number; attachment: number; home: number; grudge: number };   // grudge <= 0
  reasons: string[];                   // e.g. money_first, ring_gate_secure_unknown, profile_missing
  policy_version: string;
  fingerprint: string;                 // send back as expected_fingerprint; a mismatch answers 409 + result
};

export type NegotiationResponse = {
  offer_id: number | null;             // contract_offers row (null only in a 409 refreshed result)
  status: NegotiationStatus;
  utility: number;                     // the weighted total; may exceed 1
  bar: number | null;                  // acceptance bar he measured it against (0.55..0.80), null if not reported
  parts: UtilityParts;
  counter: ContractTerms | null;       // set when status = COUNTER
  note: string | null;                 // one line from the agent ("He wants to be a starter."), null if none
  policy_explanation?: PolicyExplanation;
};

/** GET /offers/{offer_id}/policy: the immutable contract-policy receipt (JR's teams only). */
export type PolicyReceipt = {
  offer_id: number;
  decision_id: string;
  mode: "off" | "shadow" | "active";
  policy_version: string;
  input_hash: string;
  persona_id: string | null;
  revision: number | null;
  snapshot: Record<string, unknown>;
  result: Record<string, unknown>;
};

// ---- Free agency ----------------------------------------------------------------------------------------------

export type FreeAgent = {
  player_id: number;
  name: string;
  position: string;
  age: number | null;
  overall: number | null;
  dev_trait: string | null;            // Normal / Star / Superstar / X-Factor
  scheme_fit: number | null;           // 0..1 against `team`'s scheme, null when no team was given / not computed
  market_worth: number | null;         // W(ovr, group) from trade_value.worth, per year
  asking: { years: number; total_value: number; guaranteed: number } | null;
};

export type FreeAgentsResponse = {
  season: number;
  week: number;
  in_season: boolean;                  // street market (lower bar) vs offseason waves
  players: FreeAgent[];
};

/** POST /{abbr}/offers. Scored and stored as a contract_offers row; not a signing (the league office decides). */
export type OfferRequest = ContractTerms & { player_id: number; expected_fingerprint?: string };

// ---- Contract room --------------------------------------------------------------------------------------------

export type ExpiringPlayer = {
  player_id: number;
  name: string;
  position: string;
  overall: number | null;
  age: number | null;
  contract: RosterContract | null;
  cap_hit: number | null;
  eligible: boolean;                   // contracts_engine.extension_eligible
  eligible_reason: string;             // the CBA window, e.g. "Rookie deal: extensions open after week 18 of year 3"
  options: {
    tag_value?: number;                // franchise tag (non-exclusive) for next season
    transition_tag_value?: number;
    fifth_year_option?: { tier: 1 | 2 | 3 | 4; salary: number };   // 1st-round rookies only
  };
};

export type ExpiringResponse = { team: string; season: number; week: number; players: ExpiringPlayer[] };

/** POST /{abbr}/contracts/{player_id}/propose: an extension offer; answer is a NegotiationResponse. */
export type ProposeRequest = ContractTerms & { expected_fingerprint?: string };

/** POST /{abbr}/contracts/{player_id}/restructure-preview: convert `amount` of this year's base into bonus. */
export type RestructureRequest = { amount: number };

export type RestructurePreview = {
  player_id: number;
  amount: number;
  max_amount: number;                  // 90% of this year's base salary
  cap_hit_before: number;
  cap_hit_after: number;
  cap_savings_now: number;             // cap_hit_before − cap_hit_after this season
  added_dead_money_future: number;     // extra dead money if he's cut next season
  by_year: { season: number; cap_hit_before: number; cap_hit_after: number }[];
  legal: boolean;
  reasons: string[];
};

// ---- Trade machine --------------------------------------------------------------------------------------------

export type TradePick = { season: number; round: number; original_team: string };

export type OwnedPick = TradePick & { overall_pick: number | null };   // null until the order is set

/** Picks the team owns in the next `seasons` drafts. */
export type PicksResponse = { team: string; picks: OwnedPick[] };

export type TradeSide = { players: number[]; picks: TradePick[] };

/** `send` leaves {abbr}; `receive` leaves `other_team`. */
export type TradeRequest = { other_team: string; send: TradeSide; receive: TradeSide };

export type TradeVerdict = NegotiationStatus;

export type TradeEvaluation = {
  surplus_us: number;                  // value in − value out for {abbr}, dollars (trade_value V_T and pick curves)
  surplus_them: number;                // the same for other_team, by their GM's valuation
  verdict: TradeVerdict;
  counter: { send: TradeSide; receive: TradeSide } | null;   // the GM's version, from {abbr}'s side
  cap_legal_us: boolean;
  cap_legal_them: boolean;
  cap_room_after_us: number | null;
  cap_room_after_them: number | null;
  reasons: string[];
  dialogue: string | null;             // the AI GM's reply
};

// ---- Published trade talks (TRADE_NEGOTIATIONS_PLAN §2.3, §2.7) ----------------------------------------------

/** One asset of a turn or of the final deal, already labelled for display. Never a value (§2.3). */
export type PublishedTradeAsset = { from: string; to: string; label: string };

/**
 * A side of a published CPU talk. `role` is "initiator" on the GM who called (the §2.3 example omits it on the
 * other side), so it is optional here.
 */
export type PublishedTradeTeam = { abbr: string; gm: string; role?: string };

/** The moves a turn may name: an opening idea, a counter, or an end to the talk (§2.3 `options_for`). */
export type PublishedTalkAction = "OFFER" | "COUNTER" | "ACCEPT" | "REJECT" | "WALK_AWAY";

/** A GM's stance on a turn (§2.2); drawn as a chip, never as a number. */
export type PublishedTalkPosture = "FIRM" | "ANCHOR" | "SQUEEZE" | "MEET" | "HOLD";

/** One public turn. No surplus, options, choice_source or legality exists in the published shape (§2.3). */
export type PublishedTradeTurn = {
  turn_no: number;
  week: number;
  team: string;                        // the speaking team's abbreviation
  action: PublishedTalkAction;
  posture: PublishedTalkPosture;
  assets: PublishedTradeAsset[];
  message: string;
};

/**
 * `GET /api/franchise/trade-talks` item, exactly the public shape of §2.3: only completed CPU deals are published
 * (D6), so `kind` is CPU and `status` is AGREED. It carries no su/sg, options, choice_source or legality field.
 */
export type PublishedTradeTalk = {
  id: number;
  kind: "CPU";
  season: number;
  weeks: number[];
  status: "AGREED";
  proposal_id: number;
  teams: PublishedTradeTeam[];
  turns: PublishedTradeTurn[];
  final: { assets: PublishedTradeAsset[] };
};

/** `GET /api/franchise/trade-talks?season=` answers with the season's published talks, newest first. */
export type TradeTalksResponse = { talks: PublishedTradeTalk[] };
