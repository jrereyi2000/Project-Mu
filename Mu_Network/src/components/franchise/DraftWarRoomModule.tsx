// #franchise Draft War Room module (V2_CORE_BUILD_PLAN §2.9, V2_MASTER_PLAN §2.7): the franchise team's view of the
// draft. Reads the existing war room (GET /api/draft/war-room?team={abbr}: Round 1 slots, the pick on the clock, the
// team's private board) and the team's draft capital (GET /api/franchise/{abbr}/picks?seasons=3). Prospects open the
// shared ProspectModal from Draft Central. Recording picks, locking the order and starting rounds stay on the full
// war room (#draft?tab=war-room, components/draft/DraftWarRoom.tsx), which needs the debug token.
import { useEffect, useMemo, useState } from "react";
import { API_BASE } from "../../api";
import "../team/frontOffice.css";
import "../draft/draft.css";
import ProspectModal from "../draft/ProspectModal";
import type { PicksResponse } from "../../types/franchiseMarket";
import { SharedStyles, StateNote, useFranchiseData } from "./RosterCapModule";

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

      {prospect && <ProspectModal prospectKey={prospect} step={wr?.step ?? null} onClose={() => setProspect(null)} />}
    </section>
  );
}
