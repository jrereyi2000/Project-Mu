// /api/personality (server/personality_api.py; docs/done/PERSONALITY_DISPLAY_PLAN.md §2.1). Words, not
// engine numbers: the only numbers are the slider values, the profile revision and a public trade request's date.

export type PersonalityKind = "player" | "coach" | "coordinator" | "owner" | "gm" | "scout" | "writer";

export type PersonalitySlider = {
  name: string;
  value: number; // 0-10, the bar
  sentence: string | null; // the low phrase for 0-3, the high phrase for 7-10, nothing for 4-6
  low: string;
  high: string;
};

export type PersonalityTrait = { id: string; name: string; category: string | null; tendency: string | null };

export type PersonRef = { kind: PersonalityKind | string | null; ref: string | null; name: string };

export type PersonalityTie = { label: string; target: PersonRef };

export type PersonalityPromise = {
  direction: "made_to_him" | "made_by_him";
  by?: PersonRef;
  to?: PersonRef;
  text: string;
  status: "open";
};

export type PersonalityTradeRequest = {
  status: string; // the publicly disclosed status (ACTIVE, WITHDRAWN, ...)
  public_since: { season: number; tick: number } | null;
  current: boolean;
};

export type PersonalityBlock = "mood" | "changes" | "ties" | "attachment" | "promises" | "trade_request";

export type PersonalityProfile = {
  kind: PersonalityKind;
  ref: string;
  name: string | null;
  origin: "generated" | "real" | "unknown" | "legacy" | string;
  editable: boolean;
  revision: number | null;
  team: string | null;
  sliders: PersonalitySlider[];
  traits: PersonalityTrait[];
  mood: { primary: string; secondary: string[] } | null;
  changes: string[] | null;
  ties: PersonalityTie[] | null;
  attachment: { team: string | null; label: string } | null;
  promises: PersonalityPromise[] | null;
  trade_request: PersonalityTradeRequest | null;
  unavailable: Partial<Record<PersonalityBlock, string>>;
};

export type CatalogueTrait = {
  id: string;
  name: string;
  category: string | null;
  tendency: string | null;
  conflicts: string[];
};

export type PersonalityCatalogue = {
  role: string;
  traits: CatalogueTrait[];
  max_per_category: number;
  max_traits: number;
  slider_band: { min: number; max: number };
};

export type PersonalityOverride = {
  sliders?: Record<string, number>;
  add_traits?: string[];
  remove_traits?: string[];
  reason: string;
  expected_revision?: number;
};
