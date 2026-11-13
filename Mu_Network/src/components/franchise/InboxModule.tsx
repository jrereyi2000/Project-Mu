// #franchise Inbox module (FRONT_OFFICE_INBOX_PLAN §2.13, V2_MASTER_PLAN §2): JR plays through the personas he has
// taken control of (a GM, a head coach, later a player) and talks to the other agents of the league, one conversation
// per pair. The engines decide every outcome; the other side answers in its own voice and may privately appraise how
// it was treated — shown here as a + / − / ~ marker with the reason on hover, never a trust number. Shapes:
// types/franchiseInbox.ts. Endpoints live in server/inbox_api.py (U5); every fetch fails softly through
// `useFranchiseData` / `StateNote`, so a missing or resting backend never crashes the page.
//
// The module has four parts: the take-control panel (no actors yet), the list (acting-as filter, open/all, kind chips,
// unread dots), the conversation (messages, template tags, appraisal markers, options, the four forms, a text box and
// its draft card, a Depth Chart link in role talks, and a "go to the page this notice points at" link driven by the
// thread's link_table), and the new-conversation picker from /inbox/kinds (with the wave-4 player and team pickers
// fed by /inbox/targets).
import { useCallback, useEffect, useMemo, useState } from "react";
import { API_BASE } from "../../api";
import "../team/frontOffice.css";
import { DEPTH_CHART_SLOTS, DEPTH_PER_SLOT } from "../../types/franchiseRoster";
import type {
  InboxActor, InboxActorsResponse, InboxCandidate, InboxDraft, InboxDraftResponse, InboxFormKind, InboxKindInfo,
  InboxKindsResponse, InboxListResponse, InboxMessage, InboxOption, InboxReplyRequest, InboxStartRequest, InboxTarget,
  InboxTargetsResponse, InboxThread, InboxThreadResponse, InboxTradePick,
} from "../../types/franchiseInbox";
import { NegotiationStyles, TermsForm } from "./FreeAgencyModule";
import { SharedStyles, StateNote, franchiseFetch, useFranchiseData, type FranchiseState } from "./RosterCapModule";

const KIND_LABEL: Record<string, string> = {
  GM_TALK: "Trade talks", CONTRACT: "Contract", ROLE_TALK: "Role", VISION: "Team vision", REPORTER: "Interview",
  PRESS: "Press", PROSPECT: "Prospect", SCOUT: "Scout", OWNER: "Owner",
};
const ROLE_LABEL: Record<string, string> = {
  GM: "General Manager", COACH: "Head Coach", PLAYER: "Player", WRITER: "Beat writer",
};
const OUTCOME_MARK: Record<string, { cls: string; sym: string }> = {
  positive: { cls: "pos", sym: "+" },
  negative: { cls: "neg", sym: "−" },
  mixed: { cls: "mix", sym: "~" },
};
// A notice carries link_table/link_id, not a URL (types/franchiseInbox.ts). Each kind-owned page is resolved here:
// the PRESS notice's press_conferences row lives on the Game Center podium (P1-15).
const NOTICE_PAGES: Record<string, { slug: string; label: string }> = {
  press_conferences: { slug: "game-center", label: "Go to Game Center" },
};
const noticePage = (thread: InboxThread) => (thread.link_table ? NOTICE_PAGES[thread.link_table] ?? null : null);

const kindLabel = (kind: string) => KIND_LABEL[kind] ?? kind.replace(/_/g, " ").toLowerCase();
const roleLabel = (role: string) => ROLE_LABEL[role] ?? role;
const statusLabel = (status: string) => status.replace(/_/g, " ").toLowerCase();

// db/franchises.py:user_team_refusal() says this when the active franchise's registry lists no user team. An empty
// `user_teams` means nobody is JR's (2026-11-12), so it is a designed empty state, not a load error (P8-08).
const NO_USER_TEAM = "no user team is registered for this franchise";

function NoUserTeamNote({ teamAbbr }: { teamAbbr: string }) {
  return (
    <>
      <p className="fo-state">
        No user team is registered for this franchise, so there is no {teamAbbr} inbox to open: a conversation belongs
        to a team you control, and this franchise's registry entry lists none.
      </p>
      <p className="fo-state">
        Add the team you run to the active franchise's <code>user_teams</code> in <code>data/franchises.json</code>
        {" "}(<code>python3 -m db.franchises list</code> shows the entry), then reload this page. The take-control
        panel appears here after that, and the personas you take control of get their conversations.
      </p>
    </>
  );
}

const INBOX_CSS = `
.fr-inbox-top { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 14px; }
.fr-inbox-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin-bottom: 16px; }
.fr-inbox-bar .fo-chips { margin-bottom: 0; }
.fr-inbox-as { display: flex; align-items: center; gap: 6px; color: var(--text-muted); font: 700 var(--fs-10) var(--font-mono); letter-spacing: .06em; text-transform: uppercase; }
.fr-inbox-as select { height: 30px; padding: 0 10px; border: 1px solid var(--gray-100); border-radius: 3px; color: var(--ink); background: var(--white); font: 600 var(--fs-11) var(--font-mono); }
.fr-inbox { display: grid; grid-template-columns: 340px minmax(0, 1fr); gap: 16px; align-items: start; }
.fr-inbox-list { margin: 0; padding: 0; list-style: none; background: var(--white); border-top: 3px solid var(--ink); box-shadow: 0 10px 30px #11111109; max-height: 640px; overflow: auto; }
.fr-inbox-list li + li { border-top: 1px solid var(--gray-100); }
.fr-thread { width: 100%; padding: 12px 14px; border: 0; background: transparent; color: var(--ink); text-align: left; cursor: pointer; display: flex; flex-direction: column; gap: 5px; }
.fr-thread:hover { background: #f5f4f1; }
.fr-thread.active { background: #f1efe9; box-shadow: inset 3px 0 0 var(--team-color, var(--red)); }
.fr-thread-top { display: flex; align-items: center; gap: 7px; }
.fr-thread-top b { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 700 var(--fs-12) var(--font-sans); }
.fr-thread.unread .fr-thread-top b { font-weight: 800; }
.fr-dot { flex: none; width: 7px; height: 7px; border-radius: 50%; background: var(--team-color, var(--red)); }
.fr-status { flex: none; padding: 2px 6px; border-radius: 3px; color: var(--text-muted); background: var(--gray-100); font: 700 var(--fs-10) var(--font-mono); font-style: normal; letter-spacing: .05em; text-transform: uppercase; }
.fr-status-open, .fr-status-waiting_jr { color: var(--ink); background: #e6efe4; }
.fr-status-closed, .fr-status-expired { color: var(--white); background: var(--gray-300); }
.fr-thread-meta { display: flex; flex-direction: column; gap: 2px; color: var(--text-muted); font: 600 var(--fs-10) var(--font-mono); text-transform: uppercase; letter-spacing: .04em; }
.fr-convo { min-width: 0; background: var(--white); border-top: 3px solid var(--team-color, var(--red)); box-shadow: 0 10px 30px #11111109; padding: 18px 20px; }
.fr-convo > header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; padding-bottom: 12px; border-bottom: 1px solid var(--gray-100); }
.fr-convo > header h3 { margin: 0; flex: 1; font: 700 var(--fs-18) var(--font-sans); letter-spacing: -.03em; }
.fr-convo > header small { color: var(--text-muted); font: 600 var(--fs-10) var(--font-mono); text-transform: uppercase; letter-spacing: .04em; }
.fr-convo-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.fr-convo-actions a.fr-btn { display: inline-flex; align-items: center; text-decoration: none; }
.fr-msgs { margin: 16px 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 10px; max-height: 460px; overflow: auto; }
.fr-msg { padding: 10px 14px; background: var(--paper); border-left: 3px solid var(--gray-300); }
.fr-msg.jr { margin-left: 40px; background: #f1efe9; border-left-color: var(--ink); }
.fr-msg.counterpart { margin-right: 40px; border-left-color: var(--team-color, var(--red)); }
.fr-msg.system { background: transparent; border-left-color: var(--gray-100); }
.fr-msg header { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; margin-bottom: 5px; }
.fr-msg header strong { font: 700 var(--fs-10) var(--font-mono); letter-spacing: .04em; text-transform: uppercase; }
.fr-msg header small { color: var(--text-muted); font: 600 var(--fs-10) var(--font-mono); }
.fr-src { padding: 1px 5px; border-radius: 2px; color: var(--text-muted); background: var(--gray-100); font: 700 var(--fs-10) var(--font-mono); letter-spacing: .05em; text-transform: uppercase; }
.fr-appraise { display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; border-radius: 50%; color: var(--white); font: 700 var(--fs-10) var(--font-mono); cursor: help; }
.fr-appraise.pos { background: #2e7d32; }
.fr-appraise.neg { background: var(--red); }
.fr-appraise.mix { background: #a58a00; }
.fr-msg-body { margin: 0; color: var(--ink); font: 500 var(--fs-12)/1.55 var(--font-sans); white-space: pre-wrap; }
.fr-msg.system .fr-msg-body { color: var(--text-muted); font: 500 var(--fs-10)/1.5 var(--font-mono); }
.fr-opts { display: flex; flex-wrap: wrap; gap: 7px; margin: 0 0 12px; }
.fr-opt-panel { margin-bottom: 14px; padding: 14px 16px; background: var(--paper); border-left: 3px solid var(--ink); }
.fr-opt-panel h4 { margin: 0 0 10px; color: var(--text-muted); font: 700 var(--fs-10) var(--font-mono); letter-spacing: .08em; text-transform: uppercase; }
.fr-note-label { display: flex; flex-direction: column; gap: 5px; margin-bottom: 10px; color: var(--text-muted); font: 700 var(--fs-10) var(--font-mono); letter-spacing: .07em; text-transform: uppercase; }
.fr-note-label textarea { padding: 8px 10px; border: 1px solid var(--gray-100); border-radius: 3px; color: var(--ink); background: var(--white); font: 500 var(--fs-11)/1.5 var(--font-sans); resize: vertical; }
.fr-compose { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--gray-100); }
.fr-compose textarea { width: 100%; box-sizing: border-box; min-height: 70px; padding: 10px 12px; border: 1px solid var(--gray-100); border-radius: 3px; color: var(--ink); background: var(--white); font: 500 var(--fs-12)/1.5 var(--font-sans); resize: vertical; }
.fr-compose footer { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.fr-draft { margin-top: 12px; padding: 14px 16px; background: #fff0b8; border-left: 3px solid #a58a00; }
.fr-draft > span { color: #6d5c00; font: 700 var(--fs-10) var(--font-mono); letter-spacing: .1em; text-transform: uppercase; }
.fr-draft p { margin: 6px 0 12px; font: 600 var(--fs-12)/1.5 var(--font-sans); }
.fr-draft footer { display: flex; flex-wrap: wrap; gap: 8px; }
.fr-personas { position: relative; }
.fr-personas-menu { position: absolute; z-index: 20; top: calc(100% + 6px); left: 0; width: 300px; padding: 14px; background: var(--white); border-top: 3px solid var(--team-color, var(--red)); box-shadow: 0 18px 50px #0003; }
.fr-personas-menu h4 { margin: 0 0 8px; color: var(--text-muted); font: 700 var(--fs-10) var(--font-mono); letter-spacing: .08em; text-transform: uppercase; }
.fr-personas-menu h4 + h4, .fr-persona-row + h4 { margin-top: 14px; }
.fr-persona-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--gray-100); }
.fr-persona-row span { display: flex; flex-direction: column; gap: 2px; font: 700 var(--fs-11) var(--font-sans); }
.fr-persona-row small { color: var(--text-muted); font: 600 var(--fs-10) var(--font-mono); text-transform: uppercase; letter-spacing: .04em; }
.fr-candidates { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 12px; }
.fr-candidate { padding: 18px 20px; background: var(--white); border-top: 3px solid var(--ink); display: flex; flex-direction: column; gap: 8px; }
.fr-candidate > span { color: var(--text-muted); font: 700 var(--fs-10) var(--font-mono); letter-spacing: .07em; text-transform: uppercase; }
.fr-candidate strong { font: 700 var(--fs-18) var(--font-sans); letter-spacing: -.02em; }
.fr-candidate .fr-btn { align-self: flex-start; margin-top: 4px; }
.fr-inbox-intro { margin: 0 0 16px; color: #55575d; font: 500 var(--fs-11)/1.6 var(--font-mono); }
.fr-new { padding: 16px 18px; background: var(--white); border-top: 3px solid var(--ink); }
.fr-form-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.fr-form-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.fr-new footer, .fr-opt-panel footer { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
@media (max-width: 900px) { .fr-inbox { grid-template-columns: 1fr; } .fr-inbox-list { max-height: 300px; } .fr-form-3, .fr-form-2 { grid-template-columns: 1fr; } .fr-msg.jr { margin-left: 14px; } .fr-msg.counterpart { margin-right: 14px; } }
`;

// ---- Small helpers ----------------------------------------------------------------------------------------------

function timeLabel(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString();
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

const num = (v: unknown, fallback: number): number => {
  const n = typeof v === "number" ? v : typeof v === "string" ? Number(v) : NaN;
  return Number.isFinite(n) ? n : fallback;
};

async function deleteRequest(abbr: string, path: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/franchise/${abbr}${path}`, { method: "DELETE" });
  if (res.ok) return;                                    // 204: no body
  let detail = `HTTP ${res.status}`;
  try {
    const j = await res.json();
    if (j && typeof j.detail === "string") detail = j.detail;
  } catch { /* not JSON */ }
  throw new Error(detail);
}

// ---- Appraisal marker -------------------------------------------------------------------------------------------

/** +/−/~ with the appraisal's reason on hover. Never a trust number. */
function AppraisalMark({ appraisal }: { appraisal: InboxMessage["appraisal"] }) {
  if (!appraisal) return null;
  const mark = OUTCOME_MARK[(appraisal.outcome || "").toLowerCase()] ?? OUTCOME_MARK.mixed;
  return (
    <span className={`fr-appraise ${mark.cls}`} title={appraisal.reason} aria-label={`Appraisal: ${appraisal.outcome}. ${appraisal.reason}`}>
      {mark.sym}
    </span>
  );
}

function MessageBubble({ m, counterpart }: { m: InboxMessage; counterpart: string }) {
  const who = m.author === "JR" ? "You" : m.author === "SYSTEM" ? "System" : m.author === "COUNTERPART" ? counterpart : m.author;
  const cls = m.author === "JR" ? "jr" : m.author === "SYSTEM" ? "system" : "counterpart";
  return (
    <li className={`fr-msg ${cls}`}>
      <header>
        <strong>{who}</strong>
        {m.created_at && <small>{timeLabel(m.created_at)}</small>}
        {m.source === "TEMPLATE" && <b className="fr-src" title="Fixed template text (the model's reply failed its check or timed out)">template</b>}
        {m.author === "COUNTERPART" && <AppraisalMark appraisal={m.appraisal} />}
      </header>
      <p className="fr-msg-body">{m.body}</p>
    </li>
  );
}

// ---- Forms -----------------------------------------------------------------------------------------------------

type FormSubmit = (payload: Record<string, unknown>) => void;

function contractSeed(seed: unknown): { years: number; total_value: number; bonus: number; guaranteed: number } {
  const o = asRecord(seed);
  return {
    years: Math.min(5, Math.max(1, Math.round(num(o?.years, 3)))),
    total_value: Math.max(0, Math.round(num(o?.total_value, 0))),
    bonus: Math.max(0, Math.round(num(o?.bonus, 0))),
    guaranteed: Math.max(0, Math.round(num(o?.guaranteed, 0))),
  };
}

function parsePlayers(text: string): number[] {
  return text.split(/[,\s]+/).map((s) => parseInt(s, 10)).filter((n) => Number.isFinite(n) && n > 0);
}

function parsePicks(text: string, team: string): InboxTradePick[] {
  return text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean).flatMap((s) => {
    const m = s.match(/^(\d{4})\s*R\s*(\d{1,2})/i);
    return m ? [{ season: Number(m[1]), round: Number(m[2]), original_team: team }] : [];
  });
}

function TradeForm({ team, seed, busy, blocked, submitLabel, onSubmit }: {
  team: string; seed: unknown; busy: boolean; blocked: boolean; submitLabel: string; onSubmit: FormSubmit;
}) {
  const o = asRecord(seed);
  const [players, setPlayers] = useState(() => Array.isArray(o?.players) ? (o.players as unknown[]).map((p) => String(p)).join(", ") : "");
  const [picks, setPicks] = useState(() => Array.isArray(o?.picks)
    ? (o.picks as unknown[]).map((p) => { const r = asRecord(p); return r ? `${num(r.season, 0)} R${num(r.round, 0)}` : ""; }).filter(Boolean).join(", ")
    : "");
  const payload: Record<string, unknown> = { players: parsePlayers(players), picks: parsePicks(picks, team) };
  const empty = (payload.players as number[]).length === 0 && (payload.picks as InboxTradePick[]).length === 0;
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (!empty) onSubmit(payload); }}>
      <div className="fr-form fr-form-2">
        <label>Players you offer (IDs)
          <input value={players} placeholder="e.g. 8013, 9021" onChange={(e) => setPlayers(e.target.value)} />
          <small>Comma-separated player ids.</small>
        </label>
        <label>Picks you offer
          <input value={picks} placeholder="e.g. 2027 R2, 2028 R4" onChange={(e) => setPicks(e.target.value)} />
          <small>{team}'s own picks unless it says otherwise.</small>
        </label>
      </div>
      {empty && <p className="fr-form-note">Name at least one player or pick.</p>}
      <footer>
        <button type="submit" className="fr-btn primary" disabled={empty || busy || blocked}>{busy ? "Sending…" : submitLabel}</button>
      </footer>
    </form>
  );
}

function PromiseForm({ week, seed, busy, blocked, submitLabel, onSubmit }: {
  week: number | null; seed: unknown; busy: boolean; blocked: boolean; submitLabel: string; onSubmit: FormSubmit;
}) {
  const o = asRecord(seed);
  const [slot, setSlot] = useState(() => (typeof o?.slot === "string" && (DEPTH_CHART_SLOTS as readonly string[]).includes(o.slot) ? o.slot : ""));
  const [depth, setDepth] = useState(() => Math.min(DEPTH_PER_SLOT, Math.max(1, Math.round(num(o?.max_depth, 1)))));
  const [reviewWeek, setReviewWeek] = useState(() => Math.max(1, Math.round(num(o?.due_week, (week ?? 0) + 2))));
  const minWeek = week === null ? 1 : week + 1;
  const problem = !slot ? "Pick the slot he reaches." : reviewWeek < minWeek ? `The review week must be at least week ${minWeek}.` : null;
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (!problem) onSubmit({ slot, max_depth: depth, due_week: reviewWeek }); }}>
      <div className="fr-form fr-form-3">
        <label>Slot
          <select value={slot} onChange={(e) => setSlot(e.target.value)}>
            <option value="">Pick a slot</option>
            {DEPTH_CHART_SLOTS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label>Depth he reaches
          <select value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
            {Array.from({ length: DEPTH_PER_SLOT }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        <label>Review week
          <input type="number" min={minWeek} value={reviewWeek} onChange={(e) => setReviewWeek(Number(e.target.value))} />
        </label>
      </div>
      {problem && <p className="fr-form-note">{problem}</p>}
      <footer>
        <button type="submit" className="fr-btn primary" disabled={!!problem || busy || blocked}>{busy ? "Sending…" : submitLabel}</button>
      </footer>
    </form>
  );
}

const DIRECTIONS = ["CONTEND", "RETOOL", "REBUILD"] as const;
const EMPHASES = ["OFFENSE", "DEFENSE", "BALANCED"] as const;
const PLAYING_TIME = ["YOUTH", "VETERANS", "MERIT"] as const;

function VisionForm({ seed, busy, blocked, submitLabel, onSubmit }: {
  seed: unknown; busy: boolean; blocked: boolean; submitLabel: string; onSubmit: FormSubmit;
}) {
  const o = asRecord(seed);
  const [direction, setDirection] = useState(() => (typeof o?.direction === "string" ? o.direction : ""));
  const [emphasis, setEmphasis] = useState(() => (typeof o?.emphasis === "string" ? o.emphasis : ""));
  const [playingTime, setPlayingTime] = useState(() => (typeof o?.playing_time === "string" ? o.playing_time : ""));
  const problem = !direction || !emphasis || !playingTime ? "Choose all three settings." : null;
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (!problem) onSubmit({ direction, emphasis, playing_time: playingTime }); }}>
      <div className="fr-form fr-form-3">
        <label>Direction
          <select value={direction} onChange={(e) => setDirection(e.target.value)}>
            <option value="">Choose…</option>
            {DIRECTIONS.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        <label>Emphasis
          <select value={emphasis} onChange={(e) => setEmphasis(e.target.value)}>
            <option value="">Choose…</option>
            {EMPHASES.map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        </label>
        <label>Playing time
          <select value={playingTime} onChange={(e) => setPlayingTime(e.target.value)}>
            <option value="">Choose…</option>
            {PLAYING_TIME.map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        </label>
      </div>
      {problem && <p className="fr-form-note">{problem}</p>}
      <footer>
        <button type="submit" className="fr-btn primary" disabled={!!problem || busy || blocked}>{busy ? "Sending…" : submitLabel}</button>
      </footer>
    </form>
  );
}

function FormFor({ form, team, week, seed, busy, blocked, submitLabel, onSubmit }: {
  form: InboxFormKind;
  team: string;
  week: number | null;
  seed: unknown;
  busy: boolean;
  blocked: boolean;
  submitLabel: string;
  onSubmit: FormSubmit;
}) {
  // TermsForm resets its fields whenever the seed's identity changes, so memoize the derived seed (its values only
  // change when the server sends new defaults).
  const contractTerms = useMemo(() => contractSeed(seed), [seed]);
  switch (form) {
    case "contract_terms":
      return (
        <TermsForm seed={contractTerms} submitLabel={submitLabel} busy={busy || blocked}
          onSubmit={(t) => onSubmit({ years: t.years, total_value: t.total_value, bonus: t.bonus, guaranteed: t.guaranteed })} />
      );
    case "trade_assets":
      return <TradeForm team={team} seed={seed} busy={busy} blocked={blocked} submitLabel={submitLabel} onSubmit={onSubmit} />;
    case "promise":
      return <PromiseForm week={week} seed={seed} busy={busy} blocked={blocked} submitLabel={submitLabel} onSubmit={onSubmit} />;
    case "vision":
      return <VisionForm seed={seed} busy={busy} blocked={blocked} submitLabel={submitLabel} onSubmit={onSubmit} />;
    default:
      return null;
  }
}

// ---- Take control ----------------------------------------------------------------------------------------------

function TakeControlPanel({ abbr, data, onChanged }: {
  abbr: string; data: InboxActorsResponse; onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const take = (c: InboxCandidate) => {
    setBusy(`${c.role}:${c.persona_id}`);
    setError(null);
    franchiseFetch<InboxActor>(abbr, "/inbox/actors", "POST", { role: c.role, persona_id: c.persona_id })
      .then(() => onChanged())
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(null));
  };
  return (
    <section className="fo-block">
      <div className="team-section-title"><span>{abbr} · Front office</span><h2>Take Control</h2></div>
      <p className="fr-inbox-intro">
        You don't control anyone on {abbr} yet. Take control of a persona to open the inbox for that side of the
        conversation. Nothing is taken by default.
      </p>
      {data.candidates.length === 0 ? (
        <p className="fo-state">No personas are available to take control of on this team.</p>
      ) : (
        <div className="fr-candidates">
          {data.candidates.map((c) => (
            <article key={`${c.role}:${c.persona_id}`} className="fr-candidate">
              <span>{roleLabel(c.role)}{c.also ? ` · ${c.also}` : ""}</span>
              <strong>{c.name}</strong>
              <button className="fr-btn primary" disabled={busy !== null} onClick={() => take(c)}>
                {busy === `${c.role}:${c.persona_id}` ? "Taking…" : "Take control"}
              </button>
            </article>
          ))}
        </div>
      )}
      {error && <p className="fr-warn" role="alert">Couldn't take control: {error}</p>}
    </section>
  );
}

/** The small "Personas" menu: release a persona, or take one of the remaining candidates. */
function PersonasMenu({ abbr, controlled, candidates, onChanged }: {
  abbr: string; controlled: InboxActor[]; candidates: InboxCandidate[]; onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const take = (c: InboxCandidate) => {
    setBusy(`take:${c.role}:${c.persona_id}`);
    setError(null);
    franchiseFetch<InboxActor>(abbr, "/inbox/actors", "POST", { role: c.role, persona_id: c.persona_id })
      .then(() => onChanged())
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(null));
  };
  const release = (a: InboxActor) => {
    setBusy(`release:${a.role}:${a.id}`);
    setError(null);
    deleteRequest(abbr, `/inbox/actors/${a.role}/${encodeURIComponent(a.id)}`)
      .then(() => onChanged())
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(null));
  };
  const available = candidates.filter((c) => !controlled.some((a) => a.role === c.role && a.id === c.persona_id));
  return (
    <div className="fr-personas">
      <button className="fr-btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}>Personas</button>
      {open && (
        <div className="fr-personas-menu">
          <h4>Acting as</h4>
          {controlled.map((a) => (
            <div key={`${a.role}:${a.id}`} className="fr-persona-row">
              <span>{a.name}<small>{roleLabel(a.role)}</small></span>
              <button className="fr-btn danger" disabled={busy !== null} onClick={() => release(a)}>
                {busy === `release:${a.role}:${a.id}` ? "Releasing…" : "Release"}
              </button>
            </div>
          ))}
          {available.length > 0 && <h4>Available</h4>}
          {available.map((c) => (
            <div key={`${c.role}:${c.persona_id}`} className="fr-persona-row">
              <span>{c.name}<small>{roleLabel(c.role)}{c.also ? ` · ${c.also}` : ""}</small></span>
              <button className="fr-btn" disabled={busy !== null} onClick={() => take(c)}>
                {busy === `take:${c.role}:${c.persona_id}` ? "Taking…" : "Take control"}
              </button>
            </div>
          ))}
          {error && <p className="fr-warn" role="alert">{error}</p>}
        </div>
      )}
    </div>
  );
}

// ---- Conversation ----------------------------------------------------------------------------------------------

function Conversation({ abbr, threadId, onChanged }: { abbr: string; threadId: number; onChanged: () => void }) {
  const [detail, reload] = useFranchiseData<InboxThreadResponse>(abbr, `/inbox/${threadId}`);
  const [active, setActive] = useState<InboxOption | null>(null);
  const [note, setNote] = useState("");
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<InboxDraft | null>(null);
  const [draftFallback, setDraftFallback] = useState(false);
  const [busy, setBusy] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Opening a thread marks it read: refresh the list so the unread dots and count follow once.
  const [notifiedRead, setNotifiedRead] = useState(false);
  useEffect(() => {
    if (detail.status === "ready" && !notifiedRead) {
      setNotifiedRead(true);
      onChanged();
    }
  }, [detail.status, notifiedRead, onChanged]);

  const send = useCallback((optionId: string | null, payload: Record<string, unknown> | null, noteText: string | null) => {
    setBusy(true);
    setError(null);
    const body: InboxReplyRequest = { option_id: optionId, payload, note: noteText };
    const page = detail.status === "ready" ? noticePage(detail.data.thread) : null;
    franchiseFetch<InboxThreadResponse>(abbr, `/inbox/${threadId}/reply`, "POST", body)
      .then(() => {
        setActive(null);
        setNote("");
        setText("");
        setDraft(null);
        setDraftFallback(false);
        reload();
        onChanged();
        // Opening a notice is what the notice is for: record the act, then land on its page (P1-15).
        if (optionId === "OPEN_PRESSER" && page) window.location.hash = `#franchise-${page.slug}?team=${abbr}`;
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  }, [abbr, threadId, detail, reload, onChanged]);

  const draftIt = () => {
    const t = text.trim();
    if (!t) return;
    setDrafting(true);
    setError(null);
    franchiseFetch<InboxDraftResponse>(abbr, `/inbox/${threadId}/draft`, "POST", { text: t })
      .then((r) => { setDraft(r.draft); setDraftFallback(!r.draft); })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setDrafting(false));
  };

  const choose = (o: InboxOption) => {
    if (!o.form && !o.needs_note) {
      send(o.id, null, null);
      return;
    }
    setActive(o);
    setNote("");
  };

  if (detail.status !== "ready") {
    return <div className="fr-convo"><StateNote state={detail} what="this conversation" /></div>;
  }
  const { thread, messages, options } = detail.data;
  const closed = thread.status === "CLOSED" || thread.status === "EXPIRED";
  const sorted = [...messages].sort((a, b) => a.seq - b.seq);
  const needNote = !!active?.needs_note && !note.trim();
  const counterpart = thread.counterpart?.name ?? thread.counterpart_name ?? "The other side";
  const actorName = thread.actor?.name ?? roleLabel(thread.actor_role ?? "You");
  const notice = noticePage(thread);
  return (
    <div className="fr-convo">
      <header>
        <h3>{thread.subject}</h3>
        <small>{kindLabel(thread.kind)} · {actorName} → {counterpart} · {statusLabel(thread.status)}</small>
        <div className="fr-convo-actions">
          {thread.kind === "ROLE_TALK" && (
            <a className="fr-btn" href={`#franchise-depth-chart?team=${abbr}`}>Go to Depth Chart</a>
          )}
          {notice && (
            <a className="fr-btn" href={`#franchise-${notice.slug}?team=${abbr}`}>{notice.label}</a>
          )}
          {!closed && (
            <button className="fr-btn" disabled={busy} onClick={() => {
              setBusy(true);
              setError(null);
              franchiseFetch<InboxThread>(abbr, `/inbox/${threadId}/close`, "POST")
                .then(() => { reload(); onChanged(); })
                .catch((e) => setError(e instanceof Error ? e.message : String(e)))
                .finally(() => setBusy(false));
            }}>Close</button>
          )}
        </div>
      </header>

      {sorted.length === 0 ? <p className="fo-state">No messages yet.</p> : (
        <ul className="fr-msgs">
          {sorted.map((m) => <MessageBubble key={m.id} m={m} counterpart={counterpart} />)}
        </ul>
      )}

      {error && <p className="fr-warn" role="alert">The reply didn't go through: {error}</p>}

      {closed ? (
        <p className="fr-form-note">This conversation is closed. It stays readable, but it can't be answered.</p>
      ) : (
        <>
          {options.length > 0 && (
            <div className="fr-opts" role="group" aria-label="Reply options">
              {options.map((o) => (
                <button key={o.id} className={`fr-btn${o.id === active?.id ? " primary" : ""}`} disabled={busy} onClick={() => choose(o)}>
                  {o.label}
                </button>
              ))}
            </div>
          )}

          {active && (
            <div className="fr-opt-panel">
              <h4>{active.label}</h4>
              {(active.form || active.needs_note) && (
                <label className="fr-note-label">{active.needs_note ? "Note (required)" : "Note (optional)"}
                  <textarea rows={3} maxLength={600} value={note} onChange={(e) => setNote(e.target.value)} />
                </label>
              )}
              {active.form ? (
                <FormFor form={active.form} team={abbr} week={thread.week} seed={active.default}
                  busy={busy} blocked={needNote} submitLabel="Send"
                  onSubmit={(payload) => { if (!needNote) send(active.id, payload, note.trim() || null); }} />
              ) : (
                <footer>
                  <button className="fr-btn primary" disabled={busy || needNote} onClick={() => send(active.id, null, note.trim() || null)}>
                    {busy ? "Sending…" : "Send"}
                  </button>
                  <button className="fr-btn" onClick={() => { setActive(null); setNote(""); }}>Cancel</button>
                </footer>
              )}
            </div>
          )}

          <div className="fr-compose">
            <textarea value={text} maxLength={600} placeholder="Write something, or type an offer like “3 years, 40 million, 15 guaranteed” and check it."
              onChange={(e) => { setText(e.target.value); setDraft(null); setDraftFallback(false); }} />
            <footer>
              <button className="fr-btn primary" disabled={busy || drafting || !text.trim()} onClick={draftIt}>
                {drafting ? "Reading…" : "Check text"}
              </button>
              <button className="fr-btn" disabled={busy || !text.trim()} onClick={() => send(null, null, text.trim() || null)}>
                Send as message
              </button>
            </footer>
            {draft && (
              <div className="fr-draft">
                <span>Draft</span>
                <p>{draft.summary}</p>
                <footer>
                  <button className="fr-btn primary" disabled={busy} onClick={() => send(draft.option_id, draft.payload, null)}>Send</button>
                  <button className="fr-btn" onClick={() => setDraft(null)}>Edit</button>
                  <button className="fr-btn" disabled={busy} onClick={() => send(null, null, text.trim() || null)}>Just send as a message</button>
                </footer>
              </div>
            )}
            {draftFallback && !draft && (
              <p className="fr-form-note">That reads as a plain message, not a structured action.</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ---- New conversation ------------------------------------------------------------------------------------------

/**
 * The start-time pickers of wave 4, fed by GET /inbox/targets?kind= (never mock data). `player_pick` lists the
 * team's active players as NAME · POS · OVR and posts `player_id`; `team_pick` lists teams with an open GM tenure as
 * NAME · TEAM and posts that team's abbreviation as `gm_team`. A failed fetch leaves Start disabled.
 */
function TargetPicker({ abbr, kind, form, busy, blocked, submitLabel, onSubmit }: {
  abbr: string; kind: string; form: "player_pick" | "team_pick"; busy: boolean; blocked: boolean;
  submitLabel: string; onSubmit: FormSubmit;
}) {
  const [targets] = useFranchiseData<InboxTargetsResponse>(abbr, `/inbox/targets?kind=${encodeURIComponent(kind)}`);
  const [picked, setPicked] = useState("");
  const list = targets.status === "ready" ? targets.data.targets : [];
  const selected = list.find((t) => t.id === picked) ?? null;
  // player_pick posts the player id as text; team_pick posts the team abbreviation, which the API reads as `gm_team`.
  const payload: Record<string, unknown> | null = form === "player_pick"
    ? (selected ? { player_id: selected.id } : null)
    : (selected?.team ? { gm_team: selected.team } : null);
  const targetLabel = (t: InboxTarget) => form === "player_pick"
    ? [t.name, t.position, t.overall === null || t.overall === undefined ? null : String(t.overall)].filter(Boolean).join(" · ")
    : [t.name, t.team].filter(Boolean).join(" · ");
  const failed = targets.status === "unavailable" || targets.status === "error";

  if (targets.status === "ready" && list.length === 0) {
    return <p className="fo-state">There's nobody to start this conversation with.</p>;
  }

  return (
    <form onSubmit={(e) => { e.preventDefault(); if (payload) onSubmit(payload); }}>
      <div className="fr-form fr-form-2">
        <label>{form === "player_pick" ? "Player" : "Team / GM"}
          <select value={picked} onChange={(e) => setPicked(e.target.value)} disabled={targets.status !== "ready" || busy || blocked}>
            <option value="">{form === "player_pick" ? "Pick a player" : "Pick a team"}</option>
            {list.map((t) => <option key={t.id} value={t.id}>{targetLabel(t)}</option>)}
          </select>
        </label>
      </div>
      {targets.status === "loading" && <p className="fr-form-note">Loading targets…</p>}
      {failed && <p className="fo-state fr-unavailable">Targets unavailable.</p>}
      <footer>
        <button type="submit" className="fr-btn primary" disabled={!payload || busy || blocked}>{busy ? "Starting…" : submitLabel}</button>
      </footer>
    </form>
  );
}

function NewConversation({ abbr, kinds, actors, onStarted }: {
  abbr: string; kinds: FranchiseState<InboxKindsResponse>; actors: InboxActor[]; onStarted: (t: InboxThread) => void;
}) {
  const [open, setOpen] = useState(false);
  const [kindKey, setKindKey] = useState("");
  const [asRole, setAsRole] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const startable = kinds.status === "ready" ? kinds.data.filter((k) => k.can_start) : [];
  const info: InboxKindInfo | null = startable.find((k) => k.kind === kindKey) ?? null;
  const roles = info ? info.actor_roles.filter((r) => actors.some((a) => a.role === r)) : [];
  const effectiveAs = roles.includes(asRole) ? asRole : roles[0] ?? "";

  const chooseKind = (k: InboxKindInfo) => {
    setKindKey(k.kind);
    const rs = k.actor_roles.filter((r) => actors.some((a) => a.role === r));
    setAsRole(rs[0] ?? "");
  };

  const start = (extra: Record<string, unknown> | null) => {
    if (!info || !effectiveAs) return;
    setBusy(true);
    setError(null);
    const body: InboxStartRequest = { kind: info.kind, as: effectiveAs, note: note.trim() || null, ...(extra ?? {}) };
    franchiseFetch<InboxThread>(abbr, "/inbox", "POST", body)
      .then((t) => { onStarted(t); setOpen(false); setKindKey(""); setAsRole(""); setNote(""); })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  };

  return (
    <section className="fo-block">
      <div className="team-section-title"><span>Start a conversation</span><h2>New Conversation</h2></div>
      {kinds.status !== "ready" ? <StateNote state={kinds} what="the conversation kinds" /> : !open ? (
        <button className="fr-btn" disabled={startable.length === 0} onClick={() => setOpen(true)}>
          {startable.length === 0 ? "No conversation kinds available" : "New conversation"}
        </button>
      ) : (
        <div className="fr-new">
          <div className="fo-chips" role="group" aria-label="Conversation kind">
            {startable.map((k) => (
              <button key={k.kind} className={kindKey === k.kind ? "active" : ""} onClick={() => chooseKind(k)}>{kindLabel(k.kind)}</button>
            ))}
          </div>
          {info && (
            <>
              <div className="fr-form fr-form-2">
                <label>Acting as
                  <select value={effectiveAs} onChange={(e) => setAsRole(e.target.value)}>
                    {roles.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
                  </select>
                </label>
                <label>Topic / opening note
                  <input value={note} maxLength={600} placeholder="Optional" onChange={(e) => setNote(e.target.value)} />
                </label>
              </div>
              {info.start_form === "player_pick" || info.start_form === "team_pick" ? (
                <TargetPicker key={info.kind} abbr={abbr} kind={info.kind} form={info.start_form}
                  busy={busy} blocked={!effectiveAs} submitLabel="Start conversation" onSubmit={(payload) => start(payload)} />
              ) : info.start_form ? (
                <FormFor form={info.start_form} team={abbr} week={null} seed={undefined} busy={busy} blocked={!effectiveAs}
                  submitLabel="Start conversation" onSubmit={(payload) => start(payload)} />
              ) : (
                <footer><button className="fr-btn primary" disabled={busy || !effectiveAs} onClick={() => start(null)}>{busy ? "Starting…" : "Start conversation"}</button></footer>
              )}
            </>
          )}
          {error && <p className="fr-warn" role="alert">Couldn't start the conversation: {error}</p>}
          <footer><button className="fr-btn" onClick={() => { setOpen(false); setError(null); }}>Cancel</button></footer>
        </div>
      )}
    </section>
  );
}

// ---- List ------------------------------------------------------------------------------------------------------

function ThreadList({ threads, selected, onSelect }: {
  threads: InboxThread[]; selected: number | null; onSelect: (id: number) => void;
}) {
  if (threads.length === 0) return <p className="fo-state">No conversations in this view.</p>;
  return (
    <ul className="fr-inbox-list" aria-label="Conversations">
      {threads.map((t) => (
        <li key={t.id}>
          <button className={`fr-thread${t.unread ? " unread" : ""}${selected === t.id ? " active" : ""}`} onClick={() => onSelect(t.id)}>
            <span className="fr-thread-top">
              {t.unread && <i className="fr-dot" aria-label="Unread" />}
              <b>{t.subject}</b>
              <em className={`fr-status fr-status-${t.status.toLowerCase()}`}>{statusLabel(t.status)}</em>
            </span>
            <span className="fr-thread-meta">
              <span>{t.counterpart?.name ?? t.counterpart_name ?? "The other side"} · {kindLabel(t.kind)}</span>
              <span>{t.actor?.name ?? roleLabel(t.actor_role ?? "You")} · {t.week === null || t.week === undefined ? `Season ${t.season}` : `Week ${t.week}`}</span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

// ---- Module ----------------------------------------------------------------------------------------------------

export default function InboxModule({ teamAbbr }: { teamAbbr: string }) {
  const [actors, reloadActors] = useFranchiseData<InboxActorsResponse>(teamAbbr, "/inbox/actors");
  const [kinds, reloadKinds] = useFranchiseData<InboxKindsResponse>(teamAbbr, "/inbox/kinds");
  const [status, setStatus] = useState<"open" | "all">("open");
  const [asRole, setAsRole] = useState("");
  const [kind, setKind] = useState("");
  const [selected, setSelected] = useState<number | null>(null);

  const listPath = useMemo(() => {
    const qs = new URLSearchParams({ limit: "50" });
    if (status === "open") qs.set("status", "OPEN");
    if (asRole) qs.set("as", asRole);
    if (kind) qs.set("kind", kind);
    return `/inbox?${qs.toString()}`;
  }, [status, asRole, kind]);
  const [list, reloadList] = useFranchiseData<InboxListResponse>(teamAbbr, listPath);

  const refresh = useCallback(() => { reloadActors(); reloadList(); }, [reloadActors, reloadList]);
  const refreshAll = useCallback(() => { reloadActors(); reloadList(); reloadKinds(); }, [reloadActors, reloadList, reloadKinds]);

  const controlled = actors.status === "ready" ? actors.data.controlled : [];
  const candidates = actors.status === "ready" ? actors.data.candidates : [];
  const roles = useMemo(() => Array.from(new Set(controlled.map((a) => a.role))), [controlled]);

  useEffect(() => {
    if (asRole && !roles.includes(asRole)) setAsRole("");
  }, [asRole, roles]);

  const kindKeys = useMemo(() => {
    const set = new Set<string>();
    for (const k of kinds.status === "ready" ? kinds.data : []) set.add(k.kind);
    for (const t of list.status === "ready" ? list.data.threads : []) set.add(t.kind);
    return Array.from(set).sort();
  }, [kinds, list]);

  const unread = list.status === "ready" ? list.data.unread : 0;

  const onStarted = (t: InboxThread) => {
    refreshAll();
    setSelected(t.id);
  };

  if (actors.status !== "ready") {
    // P8-08: the inbox belongs to a team JR controls, and the registry can list none (`user_teams: []` means NOBODY
    // is his — JR, 2026-11-12), in which case every team 403s. That is a designed state, not a broken page. The
    // string is the server's own (db/franchises.py:user_team_refusal); anything else stays a load error.
    const noUserTeam = actors.status === "error" && actors.message === NO_USER_TEAM;
    return (
      <section className="team-data-view fo-view">
        <SharedStyles />
        <NegotiationStyles />
        <style href="fr-inbox" precedence="default">{INBOX_CSS}</style>
        <section className="fo-block">
          <div className="team-section-title"><span>{teamAbbr} · Inbox</span><h2>Inbox</h2></div>
          {noUserTeam ? <NoUserTeamNote teamAbbr={teamAbbr} /> : <StateNote state={actors} what="the inbox" />}
        </section>
      </section>
    );
  }

  if (controlled.length === 0) {
    return (
      <section className="team-data-view fo-view">
        <SharedStyles />
        <NegotiationStyles />
        <style href="fr-inbox" precedence="default">{INBOX_CSS}</style>
        <TakeControlPanel abbr={teamAbbr} data={actors.data} onChanged={refresh} />
      </section>
    );
  }

  return (
    <section className="team-data-view fo-view">
      <SharedStyles />
      <NegotiationStyles />
      <style href="fr-inbox" precedence="default">{INBOX_CSS}</style>
      <section className="fo-block">
        <div className="team-section-title">
          <span>{teamAbbr} · {unread} unread · {controlled.length} persona{controlled.length === 1 ? "" : "s"}</span>
          <h2>Inbox</h2>
        </div>
        <div className="fr-inbox-top">
          <PersonasMenu abbr={teamAbbr} controlled={controlled} candidates={candidates} onChanged={refresh} />
        </div>
        <div className="fr-inbox-bar">
          <div className="fo-chips" role="group" aria-label="Open or all">
            <button className={status === "open" ? "active" : ""} onClick={() => setStatus("open")}>Open</button>
            <button className={status === "all" ? "active" : ""} onClick={() => setStatus("all")}>All</button>
          </div>
          {roles.length > 0 && (
            <label className="fr-inbox-as">Acting as
              <select value={asRole} onChange={(e) => setAsRole(e.target.value)}>
                <option value="">All</option>
                {roles.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
              </select>
            </label>
          )}
          <div className="fo-chips" role="group" aria-label="Kind">
            <button className={kind === "" ? "active" : ""} onClick={() => setKind("")}>All kinds</button>
            {kindKeys.map((k) => (
              <button key={k} className={kind === k ? "active" : ""} onClick={() => setKind(k)}>{kindLabel(k)}</button>
            ))}
          </div>
        </div>

        {list.status !== "ready" ? <StateNote state={list} what="the inbox list" /> : (
          <div className="fr-inbox">
            <ThreadList threads={list.data.threads} selected={selected} onSelect={setSelected} />
            {selected !== null ? (
              <Conversation key={selected} abbr={teamAbbr} threadId={selected} onChanged={refresh} />
            ) : (
              <p className="fo-state">Choose a conversation to read it.</p>
            )}
          </div>
        )}
        <p className="fo-basis">
          Engines decide every outcome; the other side answers in its own voice and may privately appraise how it was
          treated (+ positive, − negative, ~ mixed; hover a marker for the reason). The inbox never shows a trust number.
        </p>
      </section>

      <NewConversation abbr={teamAbbr} kinds={kinds} actors={controlled} onStarted={onStarted} />
    </section>
  );
}
