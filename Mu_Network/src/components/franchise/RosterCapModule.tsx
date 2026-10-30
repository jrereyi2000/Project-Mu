// #franchise Roster & Cap module (V2_CORE_BUILD_PLAN §2.9, V2_MASTER_PLAN §2.3): the active roster, practice squad
// and IR against their limits, a 5-year cap sheet, and per-player moves (cut, PS promote/demote, IR). Every move is
// previewed first (POST /moves/preview: cap savings vs dead money, legality), then queued (POST /moves); the league
// office writes it into the save on its next run. Shapes: types/franchiseRoster.ts.
//
// Also exports the helpers the Training Room and Depth Chart modules share: `franchiseFetch`, `useFranchiseData`,
// `FranchiseState` and `MoveDialog`.
import { useCallback, useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { money } from "../team/format";
import "../team/frontOffice.css";
import type {
  CapResponse, MoveKind, MovePreview, MoveRequest, QueuedMoveResponse, RosterPlayer, RosterResponse,
} from "../../types/franchiseRoster";

// ---- Shared fetch helpers ------------------------------------------------------------------------------------

export class FranchiseApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

/** fetch against /api/franchise/{abbr}{path}; JSON in and out; a non-2xx answer throws FranchiseApiError. */
export async function franchiseFetch<T>(abbr: string, path: string, method = "GET", body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}/api/franchise/${abbr}${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j && typeof j.detail === "string") detail = j.detail;
    } catch { /* not JSON */ }
    throw new FranchiseApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export type FranchiseState<T> =
  | { status: "loading" }
  | { status: "unavailable" }           // 404: the franchise API isn't mounted yet
  | { status: "error"; message: string }
  | { status: "ready"; data: T };

export function toState<T>(err: unknown): FranchiseState<T> {
  if (err instanceof FranchiseApiError && err.status === 404) return { status: "unavailable" };
  return { status: "error", message: err instanceof Error ? err.message : String(err) };
}

/** GET a franchise path; `reload()` refetches without dropping the current data. */
export function useFranchiseData<T>(abbr: string, path: string): [FranchiseState<T>, () => void] {
  const [state, setState] = useState<FranchiseState<T>>({ status: "loading" });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    franchiseFetch<T>(abbr, path)
      .then((data) => live && setState({ status: "ready", data }))
      .catch((e) => live && setState(toState<T>(e)));
    return () => { live = false; };
  }, [abbr, path, tick]);
  useEffect(() => setState({ status: "loading" }), [abbr, path]);
  return [state, useCallback(() => setTick((t) => t + 1), [])];
}

export function StateNote({ state, what }: { state: FranchiseState<unknown>; what: string }) {
  if (state.status === "loading") return <p className="fo-state">Loading {what}…</p>;
  if (state.status === "unavailable") {
    return <p className="fo-state fr-unavailable">The franchise API isn't available yet ({what}). It arrives with the league office backend.</p>;
  }
  if (state.status === "error") return <p className="fo-state">{what[0].toUpperCase() + what.slice(1)} couldn't load ({state.message}). Try again in a moment.</p>;
  return null;
}

export const MOVE_LABEL: Record<MoveKind, string> = {
  RELEASE: "Cut",
  PS_PROMOTE: "Promote to active",
  PS_DEMOTE: "Demote to practice squad",
  IR_PLACE: "Place on IR",
  IR_ACTIVATE: "Activate from IR",
};

export const SHARED_CSS = `
.fr-unavailable { color: var(--ink); }
.fr-actions { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
.fr-btn { height: 28px; padding: 0 12px; border: 1px solid var(--gray-100); border-radius: 14px; color: var(--ink); background: var(--white); font: 700 8px var(--font-mono); letter-spacing: .05em; text-transform: uppercase; cursor: pointer; white-space: nowrap; }
.fr-btn:hover:not(:disabled) { border-color: var(--ink); }
.fr-btn:disabled { color: var(--gray-300); cursor: not-allowed; }
.fr-btn.primary { color: var(--white); background: var(--ink); border-color: var(--ink); }
.fr-btn.danger { color: var(--red); border-color: #f3c3cb; }
.fr-btn.primary:disabled { background: var(--gray-300); border-color: var(--gray-300); color: var(--white); }
.fr-scrim { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; padding: 16px; background: #10111488; }
.fr-dialog { width: min(480px, 100%); padding: 24px; background: var(--white); border-top: 3px solid var(--team-color, var(--red)); box-shadow: 0 20px 60px #0004; }
.fr-dialog > span { color: var(--team-color, var(--red)); font: 700 8px var(--font-mono); letter-spacing: .1em; text-transform: uppercase; }
.fr-dialog h3 { margin: 6px 0 16px; font: 700 22px var(--font-sans); letter-spacing: -.03em; }
.fr-figures { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 14px; }
.fr-figures div { padding: 12px; background: var(--paper); display: flex; flex-direction: column; gap: 4px; }
.fr-figures span { color: var(--gray-500); font: 700 7px var(--font-mono); letter-spacing: .07em; text-transform: uppercase; }
.fr-figures strong { font: 700 17px var(--font-mono); letter-spacing: -.04em; }
.fr-figures strong.neg { color: var(--red); }
.fr-figures strong.pos { color: #2e7d32; }
.fr-warn { margin: 0 0 12px; padding: 10px 14px; color: var(--ink); background: #fbe7ea; border-left: 3px solid var(--red); font: 600 10px/1.5 var(--font-mono); }
.fr-ok { margin: 0 0 12px; padding: 10px 14px; color: var(--ink); background: #e6efe4; border-left: 3px solid #2e7d32; font: 600 10px/1.5 var(--font-mono); }
.fr-reasons { margin: 0 0 12px; padding-left: 18px; color: #55575d; font: 500 10px/1.6 var(--font-mono); }
.fr-dialog footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.fr-check { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; color: #55575d; font: 600 10px var(--font-mono); }
.fr-pending { margin-left: 6px; }
@media (max-width: 560px) { .fr-figures { grid-template-columns: 1fr 1fr; } }
`;

export function SharedStyles() {
  return <style href="fr-shared" precedence="default">{SHARED_CSS}</style>;
}

/** Preview → confirm flow for one move. Calls onQueued after POST /moves succeeds. */
export function MoveDialog({ abbr, kind, player, onClose, onQueued }: {
  abbr: string;
  kind: MoveKind;
  player: { player_id: number; name: string; position: string };
  onClose: () => void;
  onQueued: (q: QueuedMoveResponse) => void;
}) {
  const [postJune1, setPostJune1] = useState(false);
  const [preview, setPreview] = useState<FranchiseState<MovePreview>>({ status: "loading" });
  const [sending, setSending] = useState(false);
  const [queued, setQueued] = useState<QueuedMoveResponse | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);

  const request: MoveRequest = {
    kind,
    payload: kind === "RELEASE" ? { player_id: player.player_id, post_june1: postJune1 } : { player_id: player.player_id },
  };

  useEffect(() => {
    let live = true;
    setPreview({ status: "loading" });
    franchiseFetch<MovePreview>(abbr, "/moves/preview", "POST", request)
      .then((p) => live && setPreview({ status: "ready", data: p }))
      .catch((e) => live && setPreview(toState<MovePreview>(e)));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [abbr, kind, player.player_id, postJune1]);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const confirm = () => {
    setSending(true);
    setSendError(null);
    franchiseFetch<QueuedMoveResponse>(abbr, "/moves", "POST", request)
      .then((q) => { setQueued(q); onQueued(q); })
      .catch((e) => setSendError(e instanceof Error ? e.message : String(e)))
      .finally(() => setSending(false));
  };

  const p = preview.status === "ready" ? preview.data : null;
  const sign = (v: number | null) => (v === null ? "" : v < 0 ? "neg" : v > 0 ? "pos" : "");
  return (
    <div className="fr-scrim" onClick={onClose}>
      <SharedStyles />
      <div className="fr-dialog" role="dialog" aria-modal="true" aria-label={`${MOVE_LABEL[kind]}: ${player.name}`} onClick={(e) => e.stopPropagation()}>
        <span>{MOVE_LABEL[kind]} · {player.position}</span>
        <h3>{player.name}</h3>
        {kind === "RELEASE" && !queued && (
          <label className="fr-check">
            <input type="checkbox" checked={postJune1} onChange={(e) => setPostJune1(e.target.checked)} />
            Post-June 1 designation (splits dead money over two seasons)
          </label>
        )}
        {preview.status !== "ready" && <StateNote state={preview} what="the move preview" />}
        {p && (
          <>
            <div className="fr-figures">
              <div><span>Cap savings</span><strong className={sign(p.cap_savings)}>{money(p.cap_savings)}</strong></div>
              <div><span>Dead money</span><strong className={p.dead_money > 0 ? "neg" : ""}>{money(p.dead_money)}</strong></div>
              <div><span>Dead next year</span><strong className={p.dead_money_next_year > 0 ? "neg" : ""}>{money(p.dead_money_next_year)}</strong></div>
              <div><span>Cap hit</span><strong>{money(p.cap_hit)}</strong></div>
              <div><span>Cap room after</span><strong className={sign(p.cap_room_after)}>{money(p.cap_room_after)}</strong></div>
              <div><span>Active / PS after</span><strong>{p.active_count_after} / {p.practice_squad_count_after}</strong></div>
            </div>
            {p.cap_savings < 0 && (
              <p className="fr-warn" role="alert">
                Warning: this move costs {money(-p.cap_savings)} of cap room. Dead money ({money(p.dead_money)}) is more than
                his cap hit ({money(p.cap_hit)}), so cap space goes down, not up.
              </p>
            )}
            {!p.legal && <p className="fr-warn" role="alert">This move isn't allowed:</p>}
            {p.reasons.length > 0 && <ul className="fr-reasons">{p.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
          </>
        )}
        {queued && <p className="fr-ok">Queued (move #{queued.move_id}). It is applied at the next league office write.</p>}
        {sendError && <p className="fr-warn" role="alert">Couldn't queue the move: {sendError}</p>}
        <footer>
          <button className="fr-btn" onClick={onClose}>{queued ? "Close" : "Cancel"}</button>
          {!queued && (
            <button className="fr-btn primary" disabled={!p || !p.legal || sending} onClick={confirm}>
              {sending ? "Queuing…" : `Confirm ${MOVE_LABEL[kind].toLowerCase()}`}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}

// ---- Roster & Cap --------------------------------------------------------------------------------------------

type Section = "active" | "practice_squad" | "injured_reserve";
const SECTION_LABEL: Record<Section, string> = { active: "Active Roster", practice_squad: "Practice Squad", injured_reserve: "Injured Reserve" };

function actionsFor(section: Section, p: RosterPlayer): MoveKind[] {
  if (section === "active") return ["RELEASE", ...(p.ps_eligible ? ["PS_DEMOTE" as const] : []), ...(p.ir_eligible ? ["IR_PLACE" as const] : [])];
  if (section === "practice_squad") return ["PS_PROMOTE", "RELEASE"];
  return ["IR_ACTIVATE", "RELEASE"];
}

const CAP_CSS = `
.fr-counts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 22px; }
.fr-counts article { padding: 18px 20px; background: var(--white); border-top: 3px solid var(--ink); display: flex; flex-direction: column; gap: 6px; }
.fr-counts article.over { border-top-color: var(--red); }
.fr-counts span { color: var(--gray-500); font: 700 8px var(--font-mono); letter-spacing: .07em; text-transform: uppercase; }
.fr-counts strong { font: 700 26px var(--font-mono); letter-spacing: -.05em; }
.fr-counts article.over strong { color: var(--red); }
.fr-counts small { color: var(--gray-500); font: 600 8px var(--font-mono); text-transform: uppercase; }
.fr-roster { min-width: 860px; }
.fr-roster td:nth-child(3) { color: #55575d; font-weight: 400; background: transparent; }
.fr-cap-table { min-width: 640px; }
.fr-cap-table td:nth-child(3) { color: #55575d; font-weight: 400; background: transparent; }
.fr-cap-table td.neg { color: var(--red); font-weight: 700; }
@media (max-width: 560px) { .fr-counts { grid-template-columns: 1fr; } }
`;

function RosterTable({ section, players, onAction }: {
  section: Section; players: RosterPlayer[]; onAction: (kind: MoveKind, p: RosterPlayer) => void;
}) {
  if (players.length === 0) return <p className="fo-state">No players on the {SECTION_LABEL[section].toLowerCase()}.</p>;
  const sorted = [...players].sort((a, b) => (b.cap_hit ?? -1) - (a.cap_hit ?? -1) || a.name.localeCompare(b.name));
  return (
    <div className="fo-table-scroll">
      <table className="fo-table fr-roster" aria-label={SECTION_LABEL[section]}>
        <thead>
          <tr>
            <th>Player</th><th>OVR</th><th>Age</th><th>Contract</th><th>Cap Hit</th><th>Injury</th><th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((p) => (
            <tr key={p.player_id}>
              <td>
                <a href={`#player-${p.player_id}`}><strong>{p.name}</strong></a>
                <small>
                  {p.position}
                  {p.contract && p.contract.years_left <= 1 && <b className="fo-tag contract-year">Contract year</b>}
                  {p.pending_move && <b className="fo-tag new-deal fr-pending">Queued: {MOVE_LABEL[p.pending_move]}</b>}
                </small>
              </td>
              <td>{p.overall ?? "—"}</td>
              <td>{p.age ?? "—"}</td>
              <td>
                {p.contract ? <>{p.contract.years_left} yr{p.contract.years_left === 1 ? "" : "s"} left<small>thru {p.contract.through} · {money(p.contract.total_value)}</small></> : "—"}
              </td>
              <td>{money(p.cap_hit)}</td>
              <td>{p.injury ? <>{p.injury}<small>{p.weeks_out ?? "?"} wk{p.weeks_out === 1 ? "" : "s"} out</small></> : "—"}</td>
              <td>
                <div className="fr-actions">
                  {actionsFor(section, p).map((k) => (
                    <button key={k} className={`fr-btn${k === "RELEASE" ? " danger" : ""}`} disabled={!!p.pending_move} onClick={() => onAction(k, p)}>
                      {MOVE_LABEL[k]}
                    </button>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CapSheet5({ cap }: { cap: CapResponse }) {
  if (cap.years.length === 0) return <p className="fo-state">No cap years recorded.</p>;
  return (
    <div className="fo-table-scroll">
      <table className="fo-table fr-cap-table" aria-label="Five-year cap sheet">
        <thead>
          <tr><th>Season</th><th>Cap</th><th>Committed</th><th>Dead Money</th><th>Rookie Reserve</th><th>Room</th></tr>
        </thead>
        <tbody>
          {cap.years.map((y) => (
            <tr key={y.season}>
              <td><strong>{y.season}</strong><small>{y.estimated ? "projected" : "current"}</small></td>
              <td className={y.estimated ? "fo-projected" : undefined}>{money(y.cap)}{y.estimated && <em> est.</em>}</td>
              <td>{money(y.committed)}</td>
              <td className={y.dead_money > 0 ? "neg" : undefined}>{money(y.dead_money)}</td>
              <td>{money(y.rookie_reserve)}</td>
              <td className={y.room < 0 ? "neg" : undefined}><strong>{money(y.room)}</strong></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function RosterCapModule({ teamAbbr }: { teamAbbr: string }) {
  const [roster, reloadRoster] = useFranchiseData<RosterResponse>(teamAbbr, "/roster");
  const [cap, reloadCap] = useFranchiseData<CapResponse>(teamAbbr, "/cap?years=5");
  const [section, setSection] = useState<Section>("active");
  const [move, setMove] = useState<{ kind: MoveKind; player: RosterPlayer } | null>(null);

  const r = roster.status === "ready" ? roster.data : null;
  const counts: [Section, number, number | null][] = r
    ? [["active", r.active.length, r.limits.active], ["practice_squad", r.practice_squad.length, r.limits.practice_squad], ["injured_reserve", r.injured_reserve.length, null]]
    : [];

  return (
    <section className="team-data-view fo-view">
      <SharedStyles />
      <style href="fr-cap" precedence="default">{CAP_CSS}</style>
      <section className="fo-block">
        <div className="team-section-title"><span>{teamAbbr} · {r ? `Season ${r.season} · Week ${r.week}` : "Roster"}</span><h2>Roster</h2></div>
        {!r && <StateNote state={roster} what="the roster" />}
        {r && (
          <>
            <div className="fr-counts">
              {counts.map(([s, n, limit]) => (
                <article key={s} className={limit !== null && n > limit ? "over" : ""}>
                  <span>{SECTION_LABEL[s]}</span>
                  <strong>{n}{limit !== null && <small> / {limit}</small>}</strong>
                  <small>{limit === null ? "no limit" : n > limit ? `${n - limit} over the limit` : `${limit - n} open`}</small>
                </article>
              ))}
            </div>
            <div className="fo-chips" role="group" aria-label="Roster section">
              {counts.map(([s, n]) => (
                <button key={s} className={section === s ? "active" : ""} onClick={() => setSection(s)}>{SECTION_LABEL[s]}<i>{n}</i></button>
              ))}
            </div>
            <RosterTable section={section} players={r[section]} onAction={(kind, player) => setMove({ kind, player })} />
            <p className="fo-basis">Moves are previewed, then queued; the league office writes them into the save on its next run.</p>
          </>
        )}
      </section>
      <section className="fo-block">
        <div className="team-section-title"><span>Next 5 Seasons</span><h2>Cap Sheet</h2></div>
        {cap.status === "ready" ? <CapSheet5 cap={cap.data} /> : <StateNote state={cap} what="the cap sheet" />}
        {cap.status === "ready" && <p className="fo-basis">Room = cap − committed − dead money − rookie reserve. <em>est.</em> marks a projected cap.</p>}
      </section>
      {move && (
        <MoveDialog abbr={teamAbbr} kind={move.kind} player={move.player} onClose={() => setMove(null)}
          onQueued={() => { reloadRoster(); reloadCap(); }} />
      )}
    </section>
  );
}
