/**
 * JSON contract for the #franchise Inbox module (FRONT_OFFICE_INBOX_PLAN §2.12–§2.13). These are the shapes
 * `server/inbox_api.py` (prefix `/api/franchise`) implements (U5); every fetch must fail softly (a 404 reads
 * "unavailable", never a crash).
 *
 *   GET    /api/franchise/{abbr}/inbox/actors                -> InboxActorsResponse
 *   POST   /api/franchise/{abbr}/inbox/actors                InboxTakeControlRequest -> Actor (201)
 *   DELETE /api/franchise/{abbr}/inbox/actors/{role}/{id}    -> 204 (no body)
 *   GET    /api/franchise/{abbr}/inbox?status=&kind=&as=&limit= -> InboxListResponse
 *   GET    /api/franchise/{abbr}/inbox/kinds                 -> InboxKindsResponse (an array)
 *   GET    /api/franchise/{abbr}/inbox/targets?kind=         -> InboxTargetsResponse
 *   GET    /api/franchise/{abbr}/inbox/{thread_id}           -> InboxThreadResponse (marks it read)
 *   POST   /api/franchise/{abbr}/inbox/{thread_id}/draft     {text} -> InboxDraftResponse (acts on nothing)
 *   POST   /api/franchise/{abbr}/inbox/{thread_id}/reply     InboxReplyRequest -> InboxThreadResponse
 *   POST   /api/franchise/{abbr}/inbox                       InboxStartRequest -> InboxThread (201)
 *   POST   /api/franchise/{abbr}/inbox/{thread_id}/close     -> InboxThread
 *
 * Errors are FastAPI's `{"detail": string}` (400, 403, 404, 409). Notes are at most 600 characters. Messages never
 * include `facts` or a GM's surplus. The inbox never carries a trust number: a counterpart's appraisal is an outcome
 * and a reason only.
 */

/** `Actor = {role, id, name}` (§2.12). `role` is GM / COACH / PLAYER; the id is the persona's row id as text. */
export type InboxActor = { role: string; id: string; name: string };

/** A candidate JR could take control of: the open GM tenure or the head coach (a coach-GM carries `also`). */
export type InboxCandidate = { role: string; persona_id: string; name: string; also?: string | null };

/** One message of a conversation. `source` is JR / LLM / TEMPLATE / ENGINE; a TEMPLATE line is tagged in the UI. */
export type InboxMessage = {
  id: number;
  seq: number;
  author: "JR" | "COUNTERPART" | "SYSTEM" | string;
  body: string;
  source: "JR" | "LLM" | "TEMPLATE" | "ENGINE" | string;
  option_id: string | null;
  engine_ref: Record<string, unknown> | null;
  /** The counterpart's judgment of the JR message this replies to. No trust number, only an outcome and a reason. */
  appraisal: InboxAppraisal | null;
  created_at: string;
};

/** `outcome` is positive / negative / mixed; `reason` is the one-line justification shown on hover. */
export type InboxAppraisal = { outcome: "positive" | "negative" | "mixed" | string; reason: string };

/** A conversation. Adds `actor` and `counterpart` and leaves out the kind-owned `state` (§2.12). */
export type InboxThread = {
  id: number;
  kind: string;
  actor: InboxActor;
  counterpart: InboxActor;
  subject: string;
  status: "OPEN" | "WAITING_JR" | "CLOSED" | "EXPIRED" | string;
  unread: boolean;
  season: number;
  week: number | null;
  actor_role?: string;
  actor_id?: string;
  counterpart_type?: string;
  counterpart_id?: string;
  counterpart_name?: string;
  started_by?: "JR" | "COUNTERPART" | "SYSTEM" | string;
  source_ref?: string;
  link_table?: string | null;
  link_id?: number | null;
  offseason_stage?: string | null;
  expires_season?: number | null;
  expires_week?: number | null;
  created_at?: string;
  updated_at?: string;
};

/**
 * A form id (§2.3, §2.13). A reply option names one and null is a plain button; a kind's `start_form` names the
 * picker the New Conversation panel renders before a conversation exists (`player_pick`, `team_pick`, wave 4).
 */
export type InboxFormKind = "contract_terms" | "trade_assets" | "promise" | "vision" | "player_pick" | "team_pick";

/** A reply option. `default` may seed the form's fields (the server owns its shape). */
export type InboxOption = {
  id: string;
  label: string;
  form: InboxFormKind | null;
  needs_note: boolean;
  default?: unknown;
};

/** A parsed draft: the option it would send, its payload, and the summary shown on the confirm card (§2.3). */
export type InboxDraft = {
  option_id: string;
  payload: Record<string, unknown> | null;
  summary: string;
};

// ---- Form payloads ---------------------------------------------------------------------------------------------

/** `contract_terms`: money in whole dollars, as in types/franchiseMarket.ts. */
export type InboxContractTerms = { years: number; total_value: number; bonus: number; guaranteed: number };

/** A pick in a `trade_assets` payload, matching the Trade Machine's `TradePick`. */
export type InboxTradePick = { season: number; round: number; original_team: string };

/** `trade_assets`: players by id and picks by draft. */
export type InboxTradeAssets = { players: number[]; picks: InboxTradePick[] };

/** `promise`: the depth-chart slot and depth the player reaches by the review week (§2.8 ROLE_TALK). */
export type InboxPromise = { slot: string; max_depth: number; due_week: number; due_season?: number };

/** `vision`: the three settings of §2.9. */
export type InboxVision = {
  direction: "CONTEND" | "RETOOL" | "REBUILD";
  emphasis: "OFFENSE" | "DEFENSE" | "BALANCED";
  playing_time: "YOUTH" | "VETERANS" | "MERIT";
};

// ---- Responses -------------------------------------------------------------------------------------------------

/** GET /inbox/actors: the personas JR controls and who he could take control of. */
export type InboxActorsResponse = { controlled: InboxActor[]; candidates: InboxCandidate[] };

/** POST /inbox/actors = InboxTakeControlRequest. 403 outside user_teams, 409 if the persona isn't on the team. */
export type InboxTakeControlRequest = { role: string; persona_id: string };

/** GET /inbox: one inbox per team. With no actors it is empty and `unread` is 0. */
export type InboxListResponse = {
  team: string;
  actors: InboxActor[];
  unread: number;
  threads: InboxThread[];
};

/** One entry of GET /inbox/kinds: a conversation kind a controlled actor can use. */
export type InboxKindInfo = {
  kind: string;
  actor_roles: string[];
  can_start: boolean;
  start_form: InboxFormKind | null;
};

/** GET /inbox/kinds answers with a bare array. */
export type InboxKindsResponse = InboxKindInfo[];

/**
 * One person a JR-started conversation can open with (§2.12 `Target`). `CONTRACT` / `ROLE_TALK` return the team's
 * active players (`id` is `players.id` as text, with position and overall); `GM_TALK` returns a team with an open GM
 * tenure (`id` is `gm_profiles.id`, `team` is the abbreviation posted back as `gm_team`).
 */
export type InboxTarget = {
  id: string;
  name: string;
  position?: string | null;
  overall?: number | null;
  team?: string | null;
};

/** GET /inbox/targets?kind=: the targets for one kind; an unknown or targetless kind answers an empty list. */
export type InboxTargetsResponse = { kind: string; targets: InboxTarget[] };

/** GET /inbox/{id} and a reply's answer: the thread with its messages and current options. */
export type InboxThreadResponse = {
  thread: InboxThread;
  messages: InboxMessage[];
  options: InboxOption[];
};

/** POST /inbox/{id}/draft: a draft, or null when the text is just talk. */
export type InboxDraftResponse = { draft: InboxDraft | null };

/** POST /inbox/{id}/reply. A plain message is `option_id: null` with the text as `note`. */
export type InboxReplyRequest = {
  option_id: string | null;
  payload?: Record<string, unknown> | null;
  note?: string | null;
};

/** POST /inbox: start a conversation. The kind's own fields sit beside `kind`, `as` and `note`. */
export type InboxStartRequest = { kind: string; as: string; note?: string | null } & Record<string, unknown>;
