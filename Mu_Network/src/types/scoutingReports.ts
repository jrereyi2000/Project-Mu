/**
 * JSON contract for the Scouting Revamp frontend (SCOUTING_REVAMP_PLAN §2.7, §2.10). These are the shapes
 * `server/draft_api.py` must implement for the report section in the prospect dossier and the Interviews card in
 * the Draft War Room module; the frontend is built against them before the endpoints exist (U6), so every fetch must
 * fail softly (a 404 hides the report section and the Interviews card reads "not available yet", never a crash).
 *
 *   GET /api/draft/prospect/{key}/report?draft_season=&edition=  -> ScoutReport
 *   GET /api/draft/interviews?team=CAR&draft_season=            -> InterviewsResponse
 *
 * Interview writes go through the V2.9 inbox API (`POST /api/franchise/{abbr}/inbox` with the PROSPECT body
 * `{kind: "PROSPECT", as: "GM", prospect_uid, interview: "COMBINE" | "VISIT"}`), not a draft endpoint.
 */

/** One trait observation. `score` is the class-relative 1–9 (one decimal), `word` its SCORE_WORDS label. */
export type ScoutTrait = { trait: string; label: string; score: number; word: string };

/** A public flag (INJURY_HISTORY, CONSISTENCY, …) with the one-line `words` shown on hover. */
export type ScoutFlag = { flag: string; words: string };

/** The report's prose sections (§2.3). Every section is a plain paragraph, never a number the fact sheet lacks. */
export type ScoutBody = {
  summary: string;
  strengths: string;
  concerns: string;
  production: string;
  athletic: string;
  projection: string;
  bottom_line: string;
};

/** One report edition (FALL | BOWLS | FINAL), public fields only — never a rating or hidden column. */
export type ScoutReport = {
  prospect_uid: string;
  prospect_key: string;
  draft_season: number;
  edition: string;
  step: number;
  step_label: string;
  grade: number;           // the step's consensus grade (copied, public)
  rank: number;            // the step's consensus rank
  round_label: string;
  role: string;            // ROLE_BANDS key
  role_words: string;      // the human phrase ("Day 1 starter", …)
  traits: ScoutTrait[];    // [] in traits-free (PS5) mode
  strengths: string[];     // trait labels
  concerns: string[];      // trait labels
  flags: ScoutFlag[];
  body: ScoutBody;
  text_source: "TEMPLATE" | "LLM" | string;
  scout: { name: string; scout_id: number | null };
  editions: string[];      // every edition stored for this prospect
};

/** The interview window: latest stored step, and whether combine / visit interviews can be opened now. */
export type InterviewWindow = {
  latest_step: number;
  combine_open: boolean;
  visit_open: boolean;
  visit_for_step: number | null;   // 25 | 26 | null
};

/** Combine interviews (15 per class) and top-30 visits (6 per step) left on this team's budget. */
export type InterviewBudget = {
  combine_used: number;
  combine_left: number;
  visits_used: number;
  visits_left: number;
};

/** A prospect JR can still interview, with the kinds open to him. */
export type EligibleProspect = {
  prospect_uid: string;
  prospect_key: string;
  full_name: string;
  position: string;
  college: string;
  rank: number;
  kinds: ("COMBINE" | "VISIT")[];
};

/** A past (or open) interview. `thread_id` links to its PROSPECT thread in the Inbox module. */
export type InterviewRow = {
  id: number;
  prospect_uid: string;
  prospect_key: string;
  full_name: string;
  kind: "COMBINE" | "VISIT" | string;
  status: "OPEN" | "CLOSED" | string;
  questions_left: number;
  summary_grade: string | null;    // ANSWER_GRADES value, set on close
  thread_id: number | null;
};

/** GET /api/draft/interviews: the window, the budget, who can still be interviewed, and the interviews already opened. */
export type InterviewsResponse = {
  team: string;
  draft_season: number;
  window: InterviewWindow;
  budget: InterviewBudget;
  eligible: EligibleProspect[];
  interviews: InterviewRow[];
};
