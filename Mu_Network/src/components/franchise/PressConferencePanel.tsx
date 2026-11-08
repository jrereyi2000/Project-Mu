// #franchise press conference podium (PRESS_CONFERENCE_PLAN §2.11, U5; PROJECT_SPEC §5.2 #franchise Game Center):
// JR's postgame presser. It opens from the Game Center while the presser is SCHEDULED, LIVE or REVIEW and walks the
// room through every state the API shapes in types/press.ts describe: taking the podium, the raised hands, the
// picked question, answering / no comment / "that's all, thanks", the review screen (editable answers, on-record
// toggles, checker warnings) and approve-and-publish. Read-only links to the week's other pressers come from
// GET /press/history. Shapes: types/press.ts. Shared helpers: RosterCapModule.tsx.
import { useCallback, useEffect, useRef, useState } from "react";
import "../team/frontOffice.css";
import {
  SharedStyles, StateNote, franchiseFetch, toState, useFranchiseData, type FranchiseState,
} from "./RosterCapModule";
import type {
  Attendee, CheckIssue, Conference, Hand, OpponentPresser, PressAnswerRequest, PressAnswerResponse,
  PressApproveResponse, PressEditTurnRequest, PressEndReason, PressHistoryResponse, PressPickResponse,
  PressResponse, PressResult, PressRole, PressStartRequest, PressStartResponse, PressStatus, PressTurnKind,
  PressTurnSource, Turn,
} from "../../types/press";

/** The states in which the Game Center shows its "Go to the podium" button. */
const PODIUM_STATUSES: PressStatus[] = ["SCHEDULED", "LIVE", "REVIEW"];

/**
 * The Game Center's view of JR's presser: the `/press` body plus a reload, so closing the panel refreshes the
 * button (approving a presser must make it disappear). Exported beside the panel to keep the Game Center edit to
 * one import.
 */
export function useTeamPresser(teamAbbr: string): [PressResponse | null, () => void] {
  const [state, reload] = useFranchiseData<PressResponse>(teamAbbr, "/press");
  return [state.status === "ready" ? state.data : null, reload];
}

const STATUS_LABEL: Record<PressStatus, string> = {
  SCHEDULED: "Scheduled", LIVE: "Live", REVIEW: "In review", CLOSED: "Published", DECLINED: "Declined", FAILED: "Failed",
};
const END_REASON_LABEL: Record<PressEndReason, string> = {
  RAN_OUT: "Ran out of questions", SUBJECT_ENDED: "The coach ended it", PATIENCE: "Patience ran out",
  CAP: "Hit the question cap", DECLINED: "Declined",
};
const RESULT_LABEL: Record<PressResult, string> = { W: "Win", L: "Loss", T: "Tie" };
const ROLE_LABEL: Record<PressRole, string> = { BEAT: "Beat", NATIONAL: "National" };
const KIND_LABEL: Record<PressTurnKind, string> = {
  OPENING: "Opening", QUESTION: "Question", FOLLOW_UP: "Follow-up", REACT: "Reaction", ANSWER: "Answer",
  NO_COMMENT: "No comment", CLOSING: "Closing", NOTE: "Note",
};
const SOURCE_LABEL: Record<PressTurnSource, string> = {
  AGENT: "", USER: "Your words", FALLBACK: "Fallback", ENGINE: "Engine note",
};

/** The last turn is a reporter's question while the presser waits for JR's answer. */
function pendingQuestion(turns: Turn[]): Turn | null {
  const last = [...(turns ?? [])].reverse().find((t) => t.kind !== "NOTE");
  return last && last.speaker === "REPORTER" ? last : null;
}

function withTurn(conference: Conference, turn: Turn): Conference {
  const turns = conference.turns ?? [];
  if (turns.some((t) => t.id === turn.id)) return conference;
  return { ...conference, turns: [...turns, turn].sort((a, b) => a.seq - b.seq) };
}

function withReplacedTurn(conference: Conference, turn: Turn): Conference {
  return { ...conference, turns: (conference.turns ?? []).map((t) => (t.id === turn.id ? turn : t)) };
}

/** "CAR 24-17 ATL", or "CAR v ATL" before the score is recorded. */
function scoreLine(c: Conference): string {
  return c.score ? `${c.team} ${c.score} ${c.opponent}` : `${c.team} v ${c.opponent}`;
}

function busyLabel(busy: string | null): string {
  if (!busy) return "";
  if (busy === "start") return "Opening the room…";
  if (busy === "pick" || busy.startsWith("pick:")) return "Bringing the question…";
  if (busy === "answer") return "Hands going up…";
  if (busy === "end") return "Ending the presser…";
  if (busy === "approve") return "Publishing…";
  if (busy.startsWith("edit:")) return "Saving…";
  return "Working…";
}

const PRESS_CSS = `
.pc-scrim { position: fixed; inset: 0; z-index: 70; overflow-y: auto; padding: 24px 16px 48px; background: #10111488; }
.pc-panel { width: min(920px, 100%); margin: 0 auto; padding: 28px 30px 36px; background: var(--paper); border-top: 4px solid var(--team-color, var(--red)); box-shadow: 0 24px 70px #0005; }
.pc-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.pc-eyebrow { color: var(--team-color, var(--red)); font: 700 8px var(--font-mono); letter-spacing: .1em; text-transform: uppercase; }
.pc-head h2 { margin: 6px 0 6px; font: 700 28px var(--font-sans); letter-spacing: -.03em; }
.pc-meta { margin: 0; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; color: var(--gray-500); font: 600 9px var(--font-mono); letter-spacing: .04em; text-transform: uppercase; }
.pc-pill { padding: 3px 8px; border-radius: 3px; color: var(--gray-500); background: var(--gray-100); font: 700 8px var(--font-mono); letter-spacing: .05em; text-transform: uppercase; }
.pc-pill.live { color: var(--white); background: var(--red); }
.pc-pill.review { color: var(--ink); background: #fff4d6; }
.pc-pill.closed { color: var(--ink); background: #e6efe4; }
.pc-pill.w, .pc-pill.closed { color: var(--ink); background: #e6efe4; }
.pc-pill.l { color: var(--white); background: var(--red); }
.pc-pill.t { color: var(--ink); background: var(--gray-100); }
.pc-tier { padding: 2px 6px; border-radius: 3px; color: var(--white); background: var(--ink); font: 700 7px var(--font-mono); letter-spacing: .05em; text-transform: uppercase; }
.pc-section { margin-bottom: 22px; }
.pc-section h3, .pc-card h3, .pc-review h3, .pc-links h3 { margin: 0 0 10px; color: var(--gray-500); font: 700 9px var(--font-mono); letter-spacing: .08em; text-transform: uppercase; }
.pc-section h3 span, .pc-card h3 span, .pc-links h3 span { margin-left: 6px; color: var(--gray-300); }
.pc-attendees { display: flex; flex-wrap: wrap; gap: 8px; }
.pc-person { min-width: 160px; padding: 10px 12px; display: flex; flex-direction: column; gap: 3px; background: var(--white); border-top: 2px solid var(--gray-100); }
.pc-person strong { font: 700 12px var(--font-sans); }
.pc-person small { color: var(--gray-500); font: 600 8px var(--font-mono); text-transform: uppercase; }
.pc-person .pc-tier { align-self: flex-start; margin-top: 2px; }
.pc-card { margin-bottom: 22px; padding: 18px 20px; background: var(--white); border-top: 3px solid var(--ink); }
.pc-card.start { border-top-color: var(--team-color, var(--red)); }
.pc-lead { margin: 0 0 12px; color: #55575d; font: 500 10px/1.6 var(--font-mono); }
.pc-textarea { width: 100%; min-height: 110px; padding: 12px 14px; resize: vertical; color: var(--ink); background: var(--paper); border: 1px solid var(--gray-100); border-radius: 2px; font: 500 12px/1.6 var(--font-sans); }
.pc-textarea:focus { outline: 2px solid var(--team-color, var(--red)); outline-offset: 1px; }
.pc-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.pc-transcript { display: flex; flex-direction: column; gap: 10px; margin-bottom: 22px; }
.pc-turn { padding: 12px 14px; background: var(--white); border-left: 3px solid var(--gray-100); }
.pc-turn.reporter { border-left-color: var(--team-color, var(--red)); }
.pc-turn.subject { border-left-color: var(--ink); }
.pc-turn.off-record { background: #faf9f7; }
.pc-turn header { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; }
.pc-turn header strong { font: 700 12px var(--font-sans); }
.pc-turn header > span { color: var(--gray-500); font: 600 8px var(--font-mono); text-transform: uppercase; }
.pc-turn p { margin: 6px 0 0; color: #33353a; font: 400 13px/1.6 var(--font-sans); white-space: pre-wrap; }
.pc-kind { padding: 2px 6px; border-radius: 3px; color: var(--gray-500); background: var(--gray-100); font: 700 7px var(--font-mono); letter-spacing: .05em; text-transform: uppercase; }
.pc-flag { padding: 2px 6px; border-radius: 3px; color: var(--ink); background: #fff4d6; font: 700 7px var(--font-mono); letter-spacing: .05em; text-transform: uppercase; }
.pc-issues { margin: 8px 0 0; padding: 8px 10px 8px 24px; color: var(--ink); background: #fbe7ea; border-left: 3px solid var(--red); font: 600 9px/1.6 var(--font-mono); }
.pc-hands { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 8px; }
.pc-hand { padding: 12px; display: flex; flex-direction: column; gap: 4px; text-align: left; color: var(--ink); background: var(--white); border: 1px solid var(--gray-100); border-top: 3px solid var(--team-color, var(--red)); cursor: pointer; }
.pc-hand:hover:not(:disabled) { border-color: var(--ink); border-top-color: var(--team-color, var(--red)); }
.pc-hand:disabled { opacity: .55; cursor: wait; }
.pc-hand strong { font: 700 12px var(--font-sans); }
.pc-hand small { color: var(--gray-500); font: 600 8px var(--font-mono); text-transform: uppercase; }
.pc-hand-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; margin-top: 3px; }
.pc-followup { padding: 2px 6px; border-radius: 3px; color: var(--ink); background: #fff4d6; font: 700 7px var(--font-mono); letter-spacing: .05em; text-transform: uppercase; }
.pc-picked { color: var(--gray-500); font: 600 8px var(--font-mono); text-transform: uppercase; }
.pc-question { padding: 14px 16px; margin-bottom: 12px; background: var(--paper); border-left: 3px solid var(--team-color, var(--red)); }
.pc-question span { display: block; margin-bottom: 5px; color: var(--gray-500); font: 700 8px var(--font-mono); letter-spacing: .06em; text-transform: uppercase; }
.pc-question p { margin: 0; color: var(--ink); font: 400 14px/1.6 var(--font-sans); }
.pc-wait { padding: 26px 0; color: var(--gray-500); font: 600 11px var(--font-mono); }
.pc-wait::before { content: ""; display: inline-block; width: 7px; height: 7px; margin-right: 8px; border-radius: 50%; background: var(--team-color, var(--red)); animation: pcPulse 1.2s ease-in-out infinite; }
@keyframes pcPulse { 0%, 100% { opacity: .25; } 50% { opacity: 1; } }
.pc-editable { border-left-color: var(--team-color, var(--red)); }
.pc-editable .pc-textarea { margin-top: 10px; }
.pc-onrecord { margin-left: auto; display: flex; align-items: center; gap: 6px; color: #55575d; font: 600 9px var(--font-mono); text-transform: uppercase; }
.pc-opponent { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 0 12px; color: var(--gray-500); font: 600 9px var(--font-mono); text-transform: uppercase; }
.pc-link-list { margin: 0; padding: 0; list-style: none; display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; }
.pc-link { width: 100%; padding: 10px 12px; display: flex; flex-direction: column; gap: 3px; text-align: left; color: var(--ink); background: var(--white); border: 1px solid var(--gray-100); cursor: pointer; }
.pc-link:hover { border-color: var(--ink); }
.pc-link strong { font: 700 12px var(--font-sans); }
.pc-link small { color: var(--gray-500); font: 600 8px var(--font-mono); text-transform: uppercase; }
.pc-empty { padding: 30px 0; color: var(--gray-500); font: 600 11px/1.6 var(--font-mono); }
@media (max-width: 640px) { .pc-panel { padding: 20px 16px 28px; } .pc-hands { grid-template-columns: 1fr; } }
`;

function Issues({ issues }: { issues: CheckIssue[] }) {
  if (!issues || issues.length === 0) return null;
  return (
    <ul className="pc-issues" role="note">
      {issues.map((issue, i) => (
        <li key={`${issue.kind}-${i}`}>
          Checker ({issue.kind.toLowerCase()}): "{issue.claim}" — the record says{" "}
          {issue.recorded === null || issue.recorded === undefined ? "something else" : String(issue.recorded)}.
        </li>
      ))}
    </ul>
  );
}

function TurnRow({ turn, subject }: { turn: Turn; subject: string }) {
  const reporter = turn.reporter;
  return (
    <article className={`pc-turn ${turn.speaker.toLowerCase()}${turn.on_record ? "" : " off-record"}`}>
      <header>
        <strong>{turn.speaker === "SUBJECT" ? subject : reporter?.display_name ?? "Reporter"}</strong>
        {turn.speaker === "REPORTER" && reporter && <span>{reporter.handle} · {ROLE_LABEL[reporter.role]}</span>}
        <b className="pc-kind">{KIND_LABEL[turn.kind]}</b>
        {!turn.on_record && <b className="pc-flag">Off the record</b>}
        {SOURCE_LABEL[turn.source] && <span>{SOURCE_LABEL[turn.source]}</span>}
      </header>
      <p>{turn.text}</p>
      <Issues issues={turn.issues} />
    </article>
  );
}

function Transcript({ conference }: { conference: Conference }) {
  const endRef = useRef<HTMLDivElement | null>(null);
  const turns = [...(conference.turns ?? [])].sort((a, b) => a.seq - b.seq);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [turns.length]);
  if (turns.length === 0) return <p className="fo-state">No one has spoken yet.</p>;
  return (
    <div className="pc-transcript" aria-label="Transcript">
      {turns.map((t) => <TurnRow key={t.id} turn={t} subject={conference.subject_name} />)}
      <div ref={endRef} />
    </div>
  );
}

function PersonCard({ attendee }: { attendee: Attendee }) {
  return (
    <article className="pc-person">
      <strong>{attendee.display_name}</strong>
      <small>{attendee.handle} · {ROLE_LABEL[attendee.role]}</small>
      {attendee.role === "NATIONAL" && attendee.pool_tier && <b className="pc-tier">{attendee.pool_tier}</b>}
    </article>
  );
}

function Room({ conference }: { conference: Conference }) {
  const attendees = conference.attendees ?? [];
  return (
    <section className="pc-section">
      <h3>In the room <span>{attendees.length}</span></h3>
      {attendees.length === 0 ? (
        <p className="fo-state">No reporters were cast for this presser.</p>
      ) : (
        <div className="pc-attendees">
          {attendees.map((a) => <PersonCard key={a.agent_id} attendee={a} />)}
        </div>
      )}
    </section>
  );
}

function HandGrid({ hands, busy, onPick }: { hands: Hand[]; busy: string | null; onPick: (agentId: string) => void }) {
  return (
    <div className="pc-hands">
      {hands.map((h) => (
        <button key={h.agent_id} className="pc-hand" disabled={busy !== null} onClick={() => onPick(h.agent_id)}>
          <strong>{h.display_name}</strong>
          <small>{h.handle} · {ROLE_LABEL[h.role]}</small>
          <span className="pc-hand-meta">
            {h.role === "NATIONAL" && h.pool_tier && <b className="pc-tier">{h.pool_tier}</b>}
            {h.follow_up && <b className="pc-followup">Follow-up</b>}
            <b className="pc-picked">{h.picked_so_far === 0 ? "Not picked yet" : `Picked ${h.picked_so_far}×`}</b>
          </span>
        </button>
      ))}
    </div>
  );
}

function ReporterQuestion({ turn }: { turn: Turn }) {
  const reporter = turn.reporter;
  return (
    <div className="pc-question">
      <span>
        {reporter ? `${reporter.display_name} · ${reporter.handle}` : "Reporter"}
        {turn.kind === "FOLLOW_UP" ? " · follow-up" : ""}
      </span>
      <p>{turn.text}</p>
    </div>
  );
}

function EditableTurn({ turn, busy, onSave }: {
  turn: Turn;
  busy: string | null;
  onSave: (body: PressEditTurnRequest) => Promise<boolean>;
}) {
  const [text, setText] = useState(turn.text);
  const [onRecord, setOnRecord] = useState(turn.on_record);
  const [saved, setSaved] = useState(false);
  useEffect(() => { setText(turn.text); setOnRecord(turn.on_record); }, [turn.text, turn.on_record]);
  const saving = busy === `edit:${turn.id}`;
  const dirty = text !== turn.text;

  const saveText = async () => {
    const ok = await onSave({ answer: text.trim() });
    setSaved(ok);
  };
  const toggleRecord = async (next: boolean) => {
    setOnRecord(next);
    setSaved(await onSave({ on_record: next }));
  };

  return (
    <article className="pc-turn subject pc-editable">
      <header>
        <strong>Your answer</strong>
        <b className="pc-kind">{KIND_LABEL[turn.kind]}</b>
        <label className="pc-onrecord">
          <input type="checkbox" checked={onRecord} disabled={saving} onChange={(e) => void toggleRecord(e.target.checked)} />
          On the record
        </label>
        {saved && !dirty && <span className="pc-flag">Saved</span>}
      </header>
      <textarea
        className="pc-textarea"
        value={text}
        disabled={saving}
        aria-label="Your answer"
        onChange={(e) => { setText(e.target.value); setSaved(false); }}
      />
      <Issues issues={turn.issues} />
      <div className="pc-actions">
        <button className="fr-btn" disabled={saving || !dirty} onClick={() => void saveText()}>
          {saving ? "Saving…" : "Save answer"}
        </button>
      </div>
    </article>
  );
}

function ReviewScreen({ conference, busy, onEdit, onApprove }: {
  conference: Conference;
  busy: string | null;
  onEdit: (turnId: number, body: PressEditTurnRequest) => Promise<boolean>;
  onApprove: () => void;
}) {
  const editable = (t: Turn) => t.speaker === "SUBJECT" && (t.kind === "ANSWER" || t.kind === "NO_COMMENT");
  const turns = [...(conference.turns ?? [])].sort((a, b) => a.seq - b.seq);
  return (
    <section className="pc-review pc-section">
      <h3>Review and publish</h3>
      <p className="pc-lead">
        Edit your answers and set what is on the record. Checker warnings are advisory and never block publishing;
        "off the record" answers are not quoted in the beat coverage.
      </p>
      {turns.length === 0 ? (
        <p className="fo-state">Nothing was said at the podium.</p>
      ) : (
        <div className="pc-transcript">
          {turns.map((t) =>
            editable(t)
              ? <EditableTurn key={t.id} turn={t} busy={busy} onSave={(body) => onEdit(t.id, body)} />
              : <TurnRow key={t.id} turn={t} subject={conference.subject_name} />,
          )}
        </div>
      )}
      <div className="pc-actions">
        <button className="fr-btn primary" disabled={busy !== null} onClick={onApprove}>
          {busy === "approve" ? "Publishing…" : "Approve and publish"}
        </button>
      </div>
    </section>
  );
}

function HistoryLinks({ teamAbbr, conference, opponentPresser, onRead }: {
  teamAbbr: string;
  conference: Conference;
  opponentPresser: OpponentPresser | null;
  onRead: (conference: Conference) => void;
}) {
  const [state] = useFranchiseData<PressHistoryResponse>(
    teamAbbr, `/press/history?season=${conference.season}&week=${conference.week}`,
  );
  const all = state.status === "ready" ? state.data.conferences ?? [] : [];
  const others = all.filter((c) => c.id !== conference.id);
  const opponent = opponentPresser && opponentPresser.status === "CLOSED"
    ? others.find((c) => c.id === opponentPresser.conference_id) ?? null
    : null;

  return (
    <section className="pc-links pc-section">
      <h3>This week's press room <span>read-only</span></h3>
      {opponentPresser && (
        <p className="pc-opponent">
          <span>Opponent's presser</span>
          {opponent
            ? <button className="fr-btn" onClick={() => onRead(opponent)}>Read {opponent.subject_name}</button>
            : <b className={`pc-pill ${opponentPresser.status.toLowerCase()}`}>{STATUS_LABEL[opponentPresser.status]}</b>}
        </p>
      )}
      {state.status !== "ready" && <StateNote state={state} what="the week's other pressers" />}
      {state.status === "ready" && others.length === 0 && (
        <p className="pc-empty">No other pressers are filed for this week yet.</p>
      )}
      {others.length > 0 && (
        <ul className="pc-link-list">
          {others.map((c) => (
            <li key={c.id}>
              <button className="pc-link" onClick={() => onRead(c)}>
                <strong>{c.team} · {c.subject_name}</strong>
                <small>{STATUS_LABEL[c.status]}{c.end_reason ? ` · ${END_REASON_LABEL[c.end_reason]}` : ""}</small>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ReadOnlyConference({ conference }: { conference: Conference }) {
  return (
    <section className="pc-section">
      <p className="pc-lead">
        Read-only: this presser was run by its AI coach and is published. Nothing here can be changed.
      </p>
      <Room conference={conference} />
      <h3>Transcript</h3>
      <Transcript conference={conference} />
      {conference.end_reason && <p className="fo-basis">{END_REASON_LABEL[conference.end_reason]}.</p>}
    </section>
  );
}

export default function PressConferencePanel({ teamAbbr, onClose }: { teamAbbr: string; onClose: () => void }) {
  const [state, setState] = useState<FranchiseState<PressResponse>>({ status: "loading" });
  const [hands, setHands] = useState<Hand[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [opening, setOpening] = useState("");
  const [answerText, setAnswerText] = useState("");
  const [reading, setReading] = useState<Conference | null>(null);

  const conference = state.status === "ready" ? state.data.conference : null;
  const opponentPresser = state.status === "ready" ? state.data.opponent_presser : null;
  const pending = conference && conference.status === "LIVE" ? pendingQuestion(conference.turns) : null;

  const load = useCallback(() => {
    setState({ status: "loading" });
    franchiseFetch<PressResponse>(teamAbbr, "/press")
      .then((data) => {
        setState({ status: "ready", data });
        setHands(data.conference?.status === "LIVE" ? data.conference.hands ?? [] : []);
      })
      .catch((e) => setState(toState<PressResponse>(e)));
  }, [teamAbbr]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (reading) setReading(null);
      else onClose();
    };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose, reading]);

  const updateConference = useCallback((next: Conference) => {
    setState((s) => (s.status === "ready" ? { status: "ready", data: { ...s.data, conference: next } } : s));
  }, []);

  const appendTurn = useCallback((turn: Turn) => {
    setState((s) => {
      if (s.status !== "ready" || !s.data.conference) return s;
      return { status: "ready", data: { ...s.data, conference: withTurn(s.data.conference, turn) } };
    });
  }, []);

  const replaceTurn = useCallback((turn: Turn) => {
    setState((s) => {
      if (s.status !== "ready" || !s.data.conference) return s;
      return { status: "ready", data: { ...s.data, conference: withReplacedTurn(s.data.conference, turn) } };
    });
  }, []);

  async function run<T>(key: string, fn: () => Promise<T>): Promise<T | null> {
    setBusy(key);
    setActionError(null);
    try {
      return await fn();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
      return null;
    } finally {
      setBusy(null);
    }
  }

  const start = async () => {
    if (!conference) return;
    const body: PressStartRequest = opening.trim() ? { opening: opening.trim() } : {};
    const res = await run("start", () =>
      franchiseFetch<PressStartResponse>(teamAbbr, `/press/${conference.id}/start`, "POST", body));
    if (!res) return;
    updateConference(res.conference);
    setHands(res.hands);
    setOpening("");
    setNotice(null);
  };

  const pick = async (agentId: string) => {
    if (!conference) return;
    const res = await run(`pick:${agentId}`, () =>
      franchiseFetch<PressPickResponse>(teamAbbr, `/press/${conference.id}/pick`, "POST", { agent_id: agentId }));
    if (!res) return;
    appendTurn(res.question);
    setHands([]);
    setAnswerText("");
  };

  const submitAnswer = async (noComment: boolean) => {
    if (!conference || !pending) return;
    const answer = answerText.trim();
    if (!noComment && !answer) {
      setActionError("Type an answer, or use No comment.");
      return;
    }
    const body: PressAnswerRequest = noComment ? { turn_id: pending.id, no_comment: true } : { turn_id: pending.id, answer };
    const res = await run("answer", () =>
      franchiseFetch<PressAnswerResponse>(teamAbbr, `/press/${conference.id}/answer`, "POST", body));
    if (!res) return;
    appendTurn(res.answer);
    setAnswerText("");
    setHands(res.hands);
    if (!res.ended) return;
    setNotice(res.end_reason
      ? `${END_REASON_LABEL[res.end_reason]}. Read the transcript, edit your answers, then approve and publish.`
      : "That's all. Read the transcript, edit your answers, then approve and publish.");
    const showReviewLocally = () => setState((s) => (s.status === "ready" && s.data.conference
      ? { status: "ready", data: { ...s.data, conference: { ...s.data.conference, status: "REVIEW", end_reason: res.end_reason } } }
      : s));
    try {
      const fresh = await franchiseFetch<PressResponse>(teamAbbr, "/press");
      if (fresh.conference) setState({ status: "ready", data: fresh });
      else showReviewLocally();
      setHands([]);
    } catch {
      // The answer was recorded even if the refresh failed: show the review screen from local state.
      showReviewLocally();
    }
  };

  const endPresser = async () => {
    if (!conference) return;
    const res = await run("end", () =>
      franchiseFetch<Conference>(teamAbbr, `/press/${conference.id}/end`, "POST", {}));
    if (!res) return;
    updateConference(res);
    setHands([]);
    setNotice("That's all. Read the transcript, edit your answers, then approve and publish.");
  };

  const editTurn = async (turnId: number, body: PressEditTurnRequest): Promise<boolean> => {
    if (!conference) return false;
    const res = await run(`edit:${turnId}`, () =>
      franchiseFetch<Turn>(teamAbbr, `/press/${conference.id}/turns/${turnId}`, "PUT", body));
    if (!res) return false;
    replaceTurn(res);
    return true;
  };

  const approve = async () => {
    if (!conference) return;
    const res = await run("approve", () =>
      franchiseFetch<PressApproveResponse>(teamAbbr, `/press/${conference.id}/approve`, "POST", {}));
    if (!res) return;
    updateConference(res.conference);
    setNotice(res.effects_started || res.wakeup_started
      ? "Published. The reporters' verdicts and the beat writers' wakeup are running in the background."
      : "Published.");
  };

  const shown = reading ?? conference;

  return (
    <div className="pc-scrim" role="presentation" onClick={onClose}>
      <SharedStyles />
      <style href="fr-press" precedence="default">{PRESS_CSS}</style>
      <div
        className="pc-panel"
        role="dialog"
        aria-modal="true"
        aria-label={shown ? `${shown.subject_name} press conference` : "Postgame press conference"}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="pc-head">
          <div>
            <span className="pc-eyebrow">{reading ? "Read-only presser" : `The podium · ${teamAbbr}`}</span>
            <h2>{shown?.subject_name ?? "Postgame press conference"}</h2>
            {shown && (
              <p className="pc-meta">
                <span>Season {shown.season} · Week {shown.week}</span>
                <span>{scoreLine(shown)}</span>
                {shown.result && <b className={`pc-pill ${shown.result.toLowerCase()}`}>{RESULT_LABEL[shown.result]}</b>}
                <b className={`pc-pill ${shown.status.toLowerCase()}`}>{STATUS_LABEL[shown.status]}</b>
                {shown.tier && <span className="pc-tier">{shown.tier}</span>}
              </p>
            )}
          </div>
          <button className="fr-btn" onClick={reading ? () => setReading(null) : onClose}>
            {reading ? "Back to your presser" : "Close"}
          </button>
        </header>

        {actionError && <p className="fr-warn" role="alert">{actionError}</p>}
        {notice && <p className="fr-ok" role="status">{notice}</p>}
        {busy && busy !== "answer" && <p className="fo-basis" role="status">{busyLabel(busy)}</p>}

        {reading ? (
          <ReadOnlyConference conference={reading} />
        ) : (
          <>
            {state.status !== "ready" && <StateNote state={state} what="the postgame presser" />}
            {state.status === "ready" && !conference && (
              <section className="pc-card">
                <h3>No presser on file</h3>
                <p className="pc-empty">
                  There's no postgame press conference for {teamAbbr}'s last game yet. It appears after the week is
                  ingested; if Play Game is pressed for the next game first, it is recorded as declined.
                </p>
                {opponentPresser && (
                  <p className="fo-basis">The opponent's presser is {STATUS_LABEL[opponentPresser.status].toLowerCase()}.</p>
                )}
              </section>
            )}
            {state.status === "ready" && conference && (
              <>
                <Room conference={conference} />

                {conference.status === "SCHEDULED" && (
                  <section className="pc-card start">
                    <h3>Take the podium</h3>
                    <p className="pc-lead">
                      The reporters are seated. Add an opening statement if you want one, then take the first question.
                    </p>
                    <textarea
                      className="pc-textarea"
                      value={opening}
                      placeholder="Opening statement (optional)"
                      aria-label="Opening statement"
                      onChange={(e) => setOpening(e.target.value)}
                    />
                    <div className="pc-actions">
                      <button className="fr-btn primary" disabled={busy !== null} onClick={() => void start()}>
                        {busy === "start" ? "Opening the room…" : "Start the presser"}
                      </button>
                    </div>
                  </section>
                )}

                {conference.status === "LIVE" && (
                  <>
                    <Transcript conference={conference} />
                    {busy === "answer" ? (
                      <p className="pc-wait" role="status">Hands going up…</p>
                    ) : pending ? (
                      <section className="pc-card">
                        <ReporterQuestion turn={pending} />
                        <textarea
                          className="pc-textarea"
                          value={answerText}
                          placeholder="Your answer"
                          aria-label="Your answer"
                          onChange={(e) => setAnswerText(e.target.value)}
                        />
                        <div className="pc-actions">
                          <button className="fr-btn" disabled={busy !== null} onClick={() => void submitAnswer(true)}>
                            No comment
                          </button>
                          <button className="fr-btn" disabled={busy !== null} onClick={() => void endPresser()}>
                            That's all, thanks
                          </button>
                          <button className="fr-btn primary" disabled={busy !== null || !answerText.trim()} onClick={() => void submitAnswer(false)}>
                            Answer
                          </button>
                        </div>
                        <p className="fo-basis">
                          Your answers are checked for numbers against the box score; warnings show in the review
                          screen and never block you.
                        </p>
                      </section>
                    ) : (
                      <section className="pc-card">
                        <h3>Who has a hand up <span>{hands.length}</span></h3>
                        {hands.length > 0 ? (
                          <HandGrid hands={hands} busy={busy} onPick={(agentId) => void pick(agentId)} />
                        ) : (
                          <p className="pc-wait" role="status">Waiting for the next round of hands…</p>
                        )}
                        <div className="pc-actions">
                          <button className="fr-btn" disabled={busy !== null} onClick={() => void endPresser()}>
                            That's all, thanks
                          </button>
                        </div>
                      </section>
                    )}
                  </>
                )}

                {conference.status === "REVIEW" && (
                  <ReviewScreen
                    conference={conference}
                    busy={busy}
                    onEdit={editTurn}
                    onApprove={() => void approve()}
                  />
                )}

                {conference.status === "CLOSED" && (
                  <>
                    <p className="fr-ok" role="status">
                      Published{conference.end_reason ? ` · ${END_REASON_LABEL[conference.end_reason]}` : ""}.
                    </p>
                    <Transcript conference={conference} />
                  </>
                )}

                {conference.status === "DECLINED" && (
                  <>
                    <p className="fr-warn" role="status">
                      This presser was declined{conference.end_reason ? ` (${END_REASON_LABEL[conference.end_reason]})` : ""}.
                      Reporters were told at the podium.
                    </p>
                    {(conference.turns ?? []).length > 0 && <Transcript conference={conference} />}
                  </>
                )}

                {conference.status === "FAILED" && (
                  <p className="fr-warn" role="alert">
                    This presser failed to run. It gets one retry on the next week run.
                  </p>
                )}

                <HistoryLinks
                  teamAbbr={teamAbbr}
                  conference={conference}
                  opponentPresser={opponentPresser}
                  onRead={setReading}
                />
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
