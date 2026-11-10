// #franchise Draft War Room module (V2_CORE_BUILD_PLAN §2.9, V2_MASTER_PLAN §2.7): the franchise team's view of the
// draft. Reads the existing war room (GET /api/draft/war-room?team={teamAbbr}: Round 1 slots, the pick on the clock, the
// team's private board) and the team's draft capital (GET /api/franchise/{abbr}/picks?seasons=3). Prospects open the
// shared ProspectModal from Draft Central. Recording picks, locking the order and starting rounds stay on the full
// war room (#draft?tab=war-room, components/draft/DraftWarRoom.tsx), which needs the debug token.
import { useEffect, useMemo, useState } from "react";
import { API_BASE } from "../../api";
import "../team/frontOffice.css";
import "../draft/draft.css";
import ProspectModal from "../draft/ProspectModal";
import type { PicksResponse } from "../../types/franchiseMarket";
import type { InboxThread } from "../../types/franchiseInbox";
import type { EligibleProspect, InterviewsResponse } from "../../types/scoutingReports";
import { SharedStyles, StateNote, franchiseFetch, useFranchiseData } from "./RosterCapModule";

// The subset of GET /api/draft/war-room this module reads (engines/draft_room.state + server/draft_api.py).
type WarRoomProspect = {
  prospect_key: string;
  full_name: string;
  position: string;
  college: string;
  consensus_rank: number;
  grade: number | null;
  round_label?: string;
  team_grade?: number | null;
  team_rank?: number | null;
  stat_summary?: string;
  choice_source?: string;
  pick_grade?: { letter: string; verdict: string; tone: string };
};

type WarRoomSlot = { pick: number; team: string; original_team: string; user_controlled?: boolean; selection?: WarRoomProspect | null };

type WarRoomState = {
  installed: boolean;
  draft_season: number;
  error?: string;
  step?: number;
  locked?: boolean;
  slots?: WarRoomSlot[];
  on_clock?: { pick: number; team: string; team_name: string; recommendation?: WarRoomProspect | null; rationale?: string | null } | null;
  team_board?: { team: string; rows: WarRoomProspect[] };
  rounds?: { started: number[]; current: number | null; next: number | null };
};

type Load = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; data: WarRoomState };

const WR_CSS = `
.dw-status { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 22px; }
.dw-status article { padding: 16px 18px; background: var(--white); border-top: 3px solid var(--ink); display: flex; flex-direction: column; gap: 6px; }
.dw-status article.hot { border-top-color: var(--team-color, var(--red)); background: #fbe7ea; }
.dw-status span { color: var(--gray-500); font: 700 8px var(--font-mono); letter-spacing: .07em; text-transform: uppercase; }
.dw-status strong { font: 700 20px var(--font-mono); letter-spacing: -.04em; }
.dw-status small { color: #55575d; font: 600 9px var(--font-mono); }
.dw-link { display: inline-flex; align-items: center; height: 32px; padding: 0 16px; margin-bottom: 18px; border-radius: 16px; color: var(--white); background: var(--ink); font: 700 9px var(--font-mono); letter-spacing: .05em; text-transform: uppercase; text-decoration: none; }
.dw-ledger { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.dw-ledger article { padding: 14px 16px; background: var(--white); border-top: 3px solid var(--ink); }
.dw-ledger h4 { margin: 0 0 8px; font: 700 14px var(--font-mono); }
.dw-ledger ul { margin: 0; padding: 0; list-style: none; }
.dw-ledger li { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid var(--gray-100); font: 600 10px var(--font-mono); }
.dw-ledger li span { color: var(--gray-500); }
.dw-board td button { padding: 0; border: 0; background: none; color: var(--ink); font: 700 11px var(--font-mono); text-align: left; cursor: pointer; }
.dw-board td button:hover { text-decoration: underline; }
.dw-board { min-width: 720px; }
@media (max-width: 800px) { .dw-status, .dw-ledger { grid-template-columns: 1fr 1fr; } }
@media (max-width: 480px) { .dw-ledger { grid-template-columns: 1fr; } }
`;

function useWarRoom(abbr: string): Load {
  const [state, setState] = useState<Load>({ status: "loading" });
  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    fetch(`${API_BASE}/api/draft/war-room?team=${encodeURIComponent(abbr)}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<WarRoomState>;
      })
      .then((data) => live && setState({ status: "ready", data }))
      .catch((e) => live && setState({ status: "error", message: e instanceof Error ? e.message : String(e) }));
    return () => { live = false; };
  }, [abbr]);
  return state;
}

const roundLabel = (r: number) => ["1st", "2nd", "3rd"][r - 1] ?? `${r}th`;

// ---- Interviews card (SCOUTING_REVAMP_PLAN §2.10) -----------------------------------------------------------------

type InterviewsLoad =
  | { status: "loading" }
  | { status: "unavailable" }
  | { status: "error"; message: string }
  | { status: "ready"; data: InterviewsResponse };

/** GET /api/draft/interviews?team= — a draft API, so fetch it directly (not through `useFranchiseData`). */
function useInterviews(abbr: string, tick: number): InterviewsLoad {
  const [state, setState] = useState<InterviewsLoad>({ status: "loading" });
  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    fetch(`${API_BASE}/api/draft/interviews?team=${encodeURIComponent(abbr)}`)
      .then(async (r) => {
        if (r.status === 404) return { status: "unavailable" } as const;
        if (!r.ok) return { status: "error", message: `HTTP ${r.status}` } as const;
        const data = (await r.json()) as InterviewsResponse;
        return { status: "ready", data } as const;
      })
      .then((s) => { if (live) setState(s); })
      .catch((e) => { if (live) setState({ status: "error", message: e instanceof Error ? e.message : String(e) }); });
    return () => { live = false; };
  }, [abbr, tick]);
  return state;
}

const pct = (used: number, total: number) => (total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0);

/** Budget meters, the window line, eligible prospects to interview, and the interviews already opened. */
function InterviewsCard({ abbr }: { abbr: string }) {
  const [tick, setTick] = useState(0);
  const interviews = useInterviews(abbr, tick);
  const [starting, setStarting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState<string | null>(null);

  const reload = () => setTick((t) => t + 1);

  // Interview writes go through the V2.9 inbox API (§2.8): POST /api/franchise/{abbr}/inbox with the PROSPECT body.
  const start = (p: EligibleProspect, kind: "COMBINE" | "VISIT") => {
    setStarting(`${p.prospect_uid}:${kind}`);
    setError(null);
    setStarted(null);
    franchiseFetch<InboxThread>(abbr, "/inbox", "POST", {
      kind: "PROSPECT",
      as: "GM",
      prospect_uid: p.prospect_uid,
      interview: kind,
    })
      .then(() => {
        setStarted(`Started a ${kind === "COMBINE" ? "combine interview" : "visit"} with ${p.full_name}. Open the Inbox tab to keep talking.`);
        reload();
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setStarting(null));
  };

  const data = interviews.status === "ready" ? interviews.data : null;

  return (
    <section className="fo-block">
      <div className="team-section-title">
        <span>{abbr} · {data ? `${data.draft_season} class` : "Scouting"}</span>
        <h2>Interviews</h2>
      </div>
      <p className="fo-basis">
        Combine interviews open until the re-signing stage; top-30 visits open for the two pre-draft steps.
        {data && !data.window.combine_open && !data.window.visit_open ? " No interview window is open right now." : ""}
      </p>

      {interviews.status === "loading" && <p className="fo-state">Loading interviews…</p>}
      {interviews.status === "unavailable" && (
        <p className="fo-state fr-unavailable">The interviews endpoint isn't available yet. It arrives with the scouting report backend.</p>
      )}
      {interviews.status === "error" && (
        <p className="fo-state">Interviews couldn't load ({interviews.message}). Try again in a moment.</p>
      )}

      {data && (
        <>
          <div className="sr-budget">
            <article>
              <div className="sr-budget-top">
                <span className="sr-budget-label">Combine interviews left</span>
                <strong>{data.budget.combine_left}</strong>
              </div>
              <small>{data.budget.combine_used} of {data.budget.combine_used + data.budget.combine_left} used</small>
              <div className="sr-meter"><div className="sr-meter-fill" style={{ width: `${pct(data.budget.combine_used, data.budget.combine_used + data.budget.combine_left)}%` }} /></div>
            </article>
            <article>
              <div className="sr-budget-top">
                <span className="sr-budget-label">Visits left</span>
                <strong>{data.budget.visits_left}</strong>
              </div>
              <small>{data.budget.visits_used} of {data.budget.visits_used + data.budget.visits_left} used</small>
              <div className="sr-meter"><div className="sr-meter-fill" style={{ width: `${pct(data.budget.visits_used, data.budget.visits_used + data.budget.visits_left)}%` }} /></div>
            </article>
          </div>

          {error && <p className="fr-warn" role="alert">Couldn't start the interview: {error}</p>}
          {started && <p className="fr-ok">{started}</p>}

          {data.eligible.length === 0 ? (
            <p className="fo-state">No prospects are eligible for a new interview right now.</p>
          ) : (
            <div className="fo-table-scroll" style={{ marginBottom: 22 }}>
              <table className="fo-table" aria-label="Eligible prospects">
                <thead><tr><th>Prospect</th><th>Position</th><th>College</th><th>Rank</th><th>Interview</th></tr></thead>
                <tbody>
                  {data.eligible.map((p) => (
                    <tr key={p.prospect_uid}>
                      <td><strong>{p.full_name}</strong></td>
                      <td>{p.position}</td>
                      <td>{p.college}</td>
                      <td>#{p.rank}</td>
                      <td>
                        <div className="fr-actions">
                          {p.kinds.map((kind) => (
                            <button key={kind} className="fr-btn" disabled={starting !== null} onClick={() => start(p, kind)}>
                              {kind === "COMBINE" ? "Combine interview" : "Visit"}
                            </button>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {data.interviews.length === 0 ? (
            <p className="fo-state">No interviews opened yet.</p>
          ) : (
            <div className="fo-table-scroll">
              <table className="fo-table" aria-label="Past interviews">
                <thead><tr><th>Prospect</th><th>Kind</th><th>Status</th><th>Questions left</th><th>Summary grade</th><th>Thread</th></tr></thead>
                <tbody>
                  {data.interviews.map((iv) => (
                    <tr key={iv.id}>
                      <td><strong>{iv.full_name}</strong></td>
                      <td>{iv.kind === "COMBINE" ? "Combine" : "Visit"}</td>
                      <td>{String(iv.status).replace(/_/g, " ").toLowerCase()}</td>
                      <td>{iv.questions_left}</td>
                      <td>{iv.summary_grade ?? "—"}</td>
                      <td>
                        {iv.thread_id != null ? (
                          <a className="fr-btn" href={`#franchise-inbox?team=${abbr}`}>Open in Inbox</a>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}

export default function DraftWarRoomModule({ teamAbbr }: { teamAbbr: string }) {
  const room = useWarRoom(teamAbbr);
  const [picks] = useFranchiseData<PicksResponse>(teamAbbr, "/picks?seasons=3");
  const [prospect, setProspect] = useState<string | null>(null);

  const wr = room.status === "ready" ? room.data : null;
  const mySlots = useMemo(() => (wr?.slots ?? []).filter((s) => s.team === teamAbbr), [wr, teamAbbr]);
  const recorded = (wr?.slots ?? []).filter((s) => s.selection).length;
  const onClock = wr?.on_clock ?? null;
  const mine = onClock?.team === teamAbbr;
  const bySeason = useMemo(() => {
    const out = new Map<number, PicksResponse["picks"]>();
    if (picks.status === "ready") {
      for (const p of picks.data.picks) out.set(p.season, [...(out.get(p.season) ?? []), p]);
    }
    return [...out.entries()].sort((a, b) => a[0] - b[0]);
  }, [picks]);

  return (
    <section className="team-data-view fo-view">
      <SharedStyles />
      <style href="fr-dw" precedence="default">{WR_CSS}</style>

      <section className="fo-block">
        <div className="team-section-title">
          <span>{teamAbbr} · {wr ? `${wr.draft_season} draft` : "Draft"}</span>
          <h2>Draft War Room</h2>
        </div>
        <a className="dw-link" href="#draft?tab=war-room">Open the full war room →</a>
        {room.status === "loading" && <p className="fo-state">Loading the war room…</p>}
        {room.status === "error" && <p className="fo-state">The war room couldn't load ({room.message}). Try again in a moment.</p>}
        {wr && !wr.installed && <p className="fo-state">The draft war room isn't installed in this database yet (draft room migrations).</p>}
        {wr && wr.installed && wr.error && <p className="fr-warn">{wr.error}</p>}
        {wr && wr.installed && !wr.error && (
          <>
            <div className="dw-status">
              <article>
                <span>Draft order</span>
                <strong>{wr.locked ? "Locked" : "Not locked"}</strong>
                <small>{wr.locked ? "Round 1 is live" : "Lock it on the full war room"}</small>
              </article>
              <article>
                <span>Round 1 picks made</span>
                <strong>{recorded}/{wr.slots?.length ?? 32}</strong>
                <small>{wr.rounds?.current ? `Round ${wr.rounds.current} started` : "Round 1"}</small>
              </article>
              <article className={mine ? "hot" : ""}>
                <span>On the clock</span>
                <strong>{onClock ? `#${onClock.pick} ${onClock.team}` : "—"}</strong>
                <small>{mine ? "You're on the clock" : onClock?.team_name ?? (wr.locked ? "Round 1 complete" : "Waiting for the order")}</small>
              </article>
              <article>
                <span>{teamAbbr} Round 1</span>
                <strong>{mySlots.length ? mySlots.map((s) => `#${s.pick}`).join(", ") : "None"}</strong>
                <small>{mySlots.length ? mySlots.filter((s) => s.original_team !== teamAbbr).map((s) => `#${s.pick} via ${s.original_team}`).join(", ") || "Own picks" : "No first-round pick"}</small>
              </article>
            </div>
            {mine && onClock?.recommendation && (
              <p className="fr-ok">
                Staff recommendation at #{onClock.pick}: <strong>{onClock.recommendation.full_name}</strong> ({onClock.recommendation.position}, {onClock.recommendation.college})
                {onClock.rationale ? `. ${onClock.rationale}` : ""}
              </p>
            )}
            {mySlots.some((s) => s.selection) && (
              <div className="fo-table-scroll" style={{ marginBottom: 22 }}>
                <table className="fo-table dw-board" aria-label="Your picks">
                  <thead><tr><th>Pick</th><th>Player</th><th>Position</th><th>Consensus</th><th>Grade</th></tr></thead>
                  <tbody>
                    {mySlots.filter((s) => s.selection).map((s) => (
                      <tr key={s.pick}>
                        <td><button onClick={() => setProspect(s.selection!.prospect_key)}>#{s.pick} {s.selection!.full_name}</button></td>
                        <td>{s.selection!.college}</td>
                        <td>{s.selection!.position}</td>
                        <td>#{s.selection!.consensus_rank}</td>
                        <td>{s.selection!.pick_grade ? `${s.selection!.pick_grade.letter} · ${s.selection!.pick_grade.verdict}` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>

      <InterviewsCard abbr={teamAbbr} />

      <section className="fo-block">
        <div className="team-section-title"><span>Next three drafts</span><h2>Draft Capital</h2></div>
        {picks.status !== "ready" && <StateNote state={picks} what="the draft capital ledger" />}
        {picks.status === "ready" && bySeason.length === 0 && <p className="fo-state">{teamAbbr} owns no picks in the next three drafts.</p>}
        {bySeason.length > 0 && (
          <div className="dw-ledger">
            {bySeason.map(([season, list]) => (
              <article key={season}>
                <h4>{season} · {list.length} pick{list.length === 1 ? "" : "s"}</h4>
                <ul>
                  {list.map((p) => (
                    <li key={`${p.round}-${p.original_team}`}>
                      <strong>{roundLabel(p.round)} round{p.overall_pick ? ` (#${p.overall_pick})` : ""}</strong>
                      <span>{p.original_team === teamAbbr ? "Own" : `via ${p.original_team}`}</span>
                    </li>
                  ))}
                </ul>
              </article>
            ))}
          </div>
        )}
      </section>

      {wr?.team_board && wr.team_board.rows.length > 0 && (
        <section className="fo-block">
          <div className="team-section-title"><span>{teamAbbr} private board · best available</span><h2>Team Board</h2></div>
          <div className="fo-table-scroll">
            <table className="fo-table dw-board" aria-label="Team draft board">
              <thead><tr><th>Prospect</th><th>Position</th><th>College</th><th>Team rank</th><th>Team grade</th><th>Consensus</th><th>Projection</th></tr></thead>
              <tbody>
                {wr.team_board.rows.slice(0, 30).map((p) => (
                  <tr key={p.prospect_key}>
                    <td><button onClick={() => setProspect(p.prospect_key)}>{p.full_name}</button>{p.stat_summary && <small>{p.stat_summary}</small>}</td>
                    <td>{p.position}</td>
                    <td>{p.college}</td>
                    <td>{p.team_rank ? `#${p.team_rank}` : "—"}</td>
                    <td>{p.team_grade != null ? p.team_grade.toFixed(1) : "—"}</td>
                    <td>#{p.consensus_rank}</td>
                    <td>{p.round_label ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="fo-basis">The team's own scouting board (engines/scouting_boards), prospects still on the board. Click a name for the full dossier.</p>
        </section>
      )}

      {prospect && <ProspectModal prospectKey={prospect} step={wr?.step ?? null} team={teamAbbr}
                                onClose={() => setProspect(null)} />}
    </section>
  );
}
