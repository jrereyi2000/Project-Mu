/**
 * JSON contract for the postgame podium (PRESS_CONFERENCE_PLAN §2.11; PROJECT_SPEC §5.2, #franchise Game Center).
 * These are the shapes `server/press_api.py` (prefix `/api/franchise`) must implement; the panel is built against
 * them before the backend exists, so every fetch fails softly (a 404 reads "unavailable", never a crash).
 *
 *   GET    /api/franchise/{abbr}/press                      -> PressResponse
 *   POST   /api/franchise/{abbr}/press/{id}/start            PressStartRequest -> PressStartResponse
 *   POST   /api/franchise/{abbr}/press/{id}/pick             PressPickRequest -> PressPickResponse
 *   POST   /api/franchise/{abbr}/press/{id}/answer           PressAnswerRequest -> PressAnswerResponse
 *   POST   /api/franchise/{abbr}/press/{id}/end              PressEndRequest -> Conference (REVIEW)
 *   PUT    /api/franchise/{abbr}/press/{id}/turns/{turn_id}  PressEditTurnRequest -> Turn
 *   POST   /api/franchise/{abbr}/press/{id}/approve          PressApproveRequest -> PressApproveResponse
 *   GET    /api/franchise/{abbr}/press/history?season=&week= -> PressHistoryResponse
 *
 * Errors are FastAPI's `{"detail": string}` (400, 403 not user-controlled, 404, 409 wrong state). A `Hand` never
 * carries question text (§2.11): the question is drafted with the hand and is only revealed by `pick`.
 */

// ---- Shared values -------------------------------------------------------------------------------------------

/** The beat / national split of the cast (§2.4). */
export type PressRole = "BEAT" | "NATIONAL";

/** National pool tier (§2.4, `newsroom/config/press_pool.json`); beat reporters carry none. */
export type PressPoolTier = "INSIDER" | "REPORTER" | "COLUMNIST" | "TV";

export type PressSpeaker = "REPORTER" | "SUBJECT";

/** `press_turns.kind` (§2.7). */
export type PressTurnKind =
  | "OPENING"
  | "QUESTION"
  | "FOLLOW_UP"
  | "REACT"
  | "ANSWER"
  | "NO_COMMENT"
  | "CLOSING"
  | "NOTE";

/** `press_turns.source`: an AI line, JR's own words, a checked fallback, or an engine note. */
export type PressTurnSource = "AGENT" | "USER" | "FALLBACK" | "ENGINE";

/** `press_conferences.status` (§2.7). */
export type PressStatus = "SCHEDULED" | "LIVE" | "REVIEW" | "CLOSED" | "DECLINED" | "FAILED";

/** `press_conferences.end_reason` (§2.7). */
export type PressEndReason = "RAN_OUT" | "SUBJECT_ENDED" | "PATIENCE" | "CAP" | "DECLINED";

/** USER = one of the active franchise's user teams (§2.6 `subject_controller`); AGENT = an AI coach. */
export type PressController = "USER" | "AGENT";

/** The subject team's result: Win, Loss or Tie. */
export type PressResult = "W" | "L" | "T";

/** The game-interest tier the cast was drawn from (§2.4). */
export type PressTier = "QUIET" | "NOTABLE" | "BIG" | "MARQUEE";

/** One `press_facts.check_line` issue. `claim` is what the line said, `recorded` what the fact sheet has. */
export type CheckIssue = {
  kind: string;                        // SCORE / STAT / RECORD / STANDING / NAME / EVENT
  span: [number, number];              // character offsets of the claim in the turn text
  claim: string;
  recorded: string | number | null;
};

// ---- The fixed §2.11 shapes ----------------------------------------------------------------------------------

/** A reporter at the podium: the same person fields a Hand and a Turn carry, without question text. */
export type PressReporter = {
  agent_id: string;
  display_name: string;
  handle: string;
  role: PressRole;
  pool_tier: PressPoolTier | null;
};

/**
 * A raised hand (§2.11). `picked_so_far` counts how often this reporter has been picked in this presser;
 * `follow_up` marks a hand following the reporter's own previous question. There is deliberately no question text.
 */
export type Hand = {
  agent_id: string;
  display_name: string;
  handle: string;
  role: PressRole;
  pool_tier: PressPoolTier | null;
  picked_so_far: number;
  follow_up: boolean;
};

/** One turn of the transcript (§2.11); `reporter` is null when the subject is speaking. */
export type Turn = {
  id: number;
  seq: number;
  speaker: PressSpeaker;
  kind: PressTurnKind;
  reporter: PressReporter | null;
  text: string;
  reply_to: number | null;
  source: PressTurnSource;
  issues: CheckIssue[];
  on_record: boolean;
};

/** One `press_attendees` person in the room. The optional counters mirror that table (§2.7). */
export type Attendee = {
  agent_id: string;
  display_name: string;
  handle: string;
  role: PressRole;
  pool_tier: PressPoolTier | null;
  person_key?: string;
  questions?: number;
  passes?: number;
  hands?: number;
  picked?: number;
  done?: boolean;
};

/**
 * One press conference (§2.11). `team` and `opponent` are team abbreviations; `score` is the game's score line
 * from the subject team's side ("24-17"), null before the result is recorded. `hands` is a panel convenience:
 * when GET /press returns a LIVE conference it may include the current raised hands so a reopened panel can pick.
 */
export type Conference = {
  id: number;
  season: number;
  week: number;
  game_id: number;
  team: string;
  opponent: string;
  result: PressResult | null;
  score: string | null;
  subject_name: string;
  controller: PressController;
  status: PressStatus;
  end_reason: PressEndReason | null;
  tier: PressTier | null;
  attendees: Attendee[];
  turns: Turn[];
  hands?: Hand[];
};

// ---- GET /{abbr}/press ---------------------------------------------------------------------------------------

/** The opponent's presser for the same game, when one exists. */
export type OpponentPresser = { conference_id: number; status: PressStatus };

export type PressResponse = {
  conference: Conference | null;       // this team's presser for its last game
  opponent_presser: OpponentPresser | null;
};

// ---- POST /{abbr}/press/{id}/start ---------------------------------------------------------------------------

/** JR's opening statement is optional (§2.6). */
export type PressStartRequest = { opening?: string };

/** The first raised hands were drafted in advance, so picking is instant. */
export type PressStartResponse = { conference: Conference; hands: Hand[] };

// ---- POST /{abbr}/press/{id}/pick ----------------------------------------------------------------------------

export type PressPickRequest = { agent_id: string };

/** The picked reporter's question, drafted with the hand. */
export type PressPickResponse = { question: Turn };

// ---- POST /{abbr}/press/{id}/answer --------------------------------------------------------------------------

/** Exactly one of `answer` (JR's words) or `no_comment: true`. */
export type PressAnswerRequest = { turn_id: number; answer?: string; no_comment?: boolean };

/** The next round's hands, or the end of the presser. */
export type PressAnswerResponse = {
  answer: Turn;
  hands: Hand[];
  ended: boolean;
  end_reason: PressEndReason | null;
};

// ---- POST /{abbr}/press/{id}/end -----------------------------------------------------------------------------

/** `{}`; LIVE becomes REVIEW. */
export type PressEndRequest = Record<string, never>;

// ---- PUT /{abbr}/press/{id}/turns/{turn_id} ------------------------------------------------------------------

/** In REVIEW only, and only JR's own answer turns: edit the words, the on-record flag, or both. */
export type PressEditTurnRequest = { answer?: string; on_record?: boolean };

// ---- POST /{abbr}/press/{id}/approve -------------------------------------------------------------------------

/** `{}`; REVIEW becomes CLOSED. Judgments run in a background thread, then the beat wakeup. */
export type PressApproveRequest = Record<string, never>;

export type PressApproveResponse = {
  conference: Conference;
  effects_started: boolean;
  wakeup_started: boolean;
};

// ---- GET /{abbr}/press/history?season=&week= -----------------------------------------------------------------

/** Any team's closed pressers for the week, including AI coaches' (read-only links). */
export type PressHistoryResponse = { conferences: Conference[] };
