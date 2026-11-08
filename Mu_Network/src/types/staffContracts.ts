/**
 * JSON contract for the staff-contract block (STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN §2.5, U3; PROJECT_SPEC §4.2 /
 * §5.2). These are the shapes `server/front_office_api.py` implements under `/api/team/{abbr}`:
 *
 *   GET  /api/team/{abbr}/front-office   -> the `staff_contracts` block (StaffContract[] | null)
 *   POST /api/team/{abbr}/staff/offer    StaffOfferRequest -> StaffOfferResponse
 *
 * Money is whole dollars. A block the migration hasn't created yet is `null` (the page reads it as "no staff
 * contracts on file", never a fabricated salary).
 */

export type StaffContractKind = "HC" | "GM" | "OC" | "DC";

/** One `staff_contracts` row: a head coach, GM or coordinator's deal. */
export type StaffContract = {
  person_id: number;        // polymorphic: HC -> coaches.id, GM -> gm_profiles.id, OC/DC -> coordinators.id
  person_kind: StaffContractKind;
  annual_salary: number;    // whole dollars
  total_years: number;
  remaining_years: number;
  signed_season: number;
  full_control: boolean;    // a head coach with the final 53-man roster say
};

export type StaffOfferRequest = {
  person_kind: StaffContractKind;
  person_id: number;
  annual_salary: number;
  total_years: number;
  full_control?: boolean;
  signed_season?: number;
};

export type StaffOfferResponse = {
  team: string;
  season: number;
  contract: StaffContract;
};
