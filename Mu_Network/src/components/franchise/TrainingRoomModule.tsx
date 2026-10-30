// #franchise Training Room module (V2_CORE_BUILD_PLAN §2.9, V2_MASTER_PLAN §2.8): the medical ledger from
// GET /api/franchise/{abbr}/medical (injury, weeks out, game status, IR) with IR place / activate moves through the
// same preview → confirm flow as Roster & Cap (POST /moves/preview, then POST /moves). Shapes: types/franchiseRoster.ts.
import { useMemo, useState } from "react";
import "../team/frontOffice.css";
import type { InjuryStatus, MedicalPlayer, MedicalResponse, MoveKind } from "../../types/franchiseRoster";
import { MOVE_LABEL, MoveDialog, SharedStyles, StateNote, useFranchiseData } from "./RosterCapModule";

const STATUS_ORDER: InjuryStatus[] = ["OUT", "DOUBTFUL", "QUESTIONABLE", "HEALTHY"];
type Filter = "Injured" | "IR" | "All";

const MED_CSS = `
.fr-med { min-width: 760px; }
.fr-med td:nth-child(3) { color: #55575d; font-weight: 400; background: transparent; }
.fr-status { padding: 3px 8px; border-radius: 3px; font: 700 8px var(--font-mono); letter-spacing: .05em; }
.fr-status.OUT { color: var(--white); background: var(--red); }
.fr-status.DOUBTFUL { color: var(--ink); background: #f7c9a1; }
.fr-status.QUESTIONABLE { color: var(--ink); background: #fff0b8; }
.fr-status.HEALTHY { color: var(--ink); background: #e6efe4; }
.fr-med-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 22px; }
.fr-med-summary article { padding: 16px 18px; background: var(--white); border-top: 3px solid var(--ink); display: flex; flex-direction: column; gap: 6px; }
.fr-med-summary span { color: var(--gray-500); font: 700 8px var(--font-mono); letter-spacing: .07em; text-transform: uppercase; }
.fr-med-summary strong { font: 700 26px var(--font-mono); letter-spacing: -.05em; }
@media (max-width: 560px) { .fr-med-summary { grid-template-columns: 1fr 1fr; } }
`;

function irAction(p: MedicalPlayer): MoveKind | null {
  if (p.on_ir) return "IR_ACTIVATE";
  if (p.ir_eligible) return "IR_PLACE";
  return null;
}

export default function TrainingRoomModule({ teamAbbr }: { teamAbbr: string }) {
  const [medical, reload] = useFranchiseData<MedicalResponse>(teamAbbr, "/medical");
  const [filter, setFilter] = useState<Filter>("Injured");
  const [move, setMove] = useState<{ kind: MoveKind; player: MedicalPlayer } | null>(null);

  const m = medical.status === "ready" ? medical.data : null;
  const rows = useMemo(() => {
    if (!m) return [];
    const list = filter === "All" ? m.players : filter === "IR" ? m.players.filter((p) => p.on_ir) : m.players.filter((p) => p.status !== "HEALTHY" || p.on_ir);
    return [...list].sort((a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status) || b.weeks_out - a.weeks_out || a.name.localeCompare(b.name));
  }, [m, filter]);
  const count = (s: InjuryStatus) => m?.players.filter((p) => p.status === s && !p.on_ir).length ?? 0;

  return (
    <section className="team-data-view fo-view">
      <SharedStyles />
      <style href="fr-med" precedence="default">{MED_CSS}</style>
      <section className="fo-block">
        <div className="team-section-title"><span>{teamAbbr} · {m ? `Season ${m.season} · Week ${m.week}` : "Medical"}</span><h2>Training Room</h2></div>
        {!m && <StateNote state={medical} what="the medical ledger" />}
        {m && (
          <>
            <div className="fr-med-summary">
              <article><span>Out</span><strong>{count("OUT")}</strong></article>
              <article><span>Doubtful</span><strong>{count("DOUBTFUL")}</strong></article>
              <article><span>Questionable</span><strong>{count("QUESTIONABLE")}</strong></article>
              <article><span>On IR</span><strong>{m.players.filter((p) => p.on_ir).length}</strong></article>
            </div>
            <div className="fo-chips" role="group" aria-label="Medical filter">
              {(["Injured", "IR", "All"] as Filter[]).map((f) => (
                <button key={f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>{f}</button>
              ))}
            </div>
            {rows.length === 0 ? (
              <p className="fo-state">{filter === "IR" ? "Nobody is on injured reserve." : filter === "Injured" ? "A clean bill of health: no injuries." : "No players listed."}</p>
            ) : (
              <div className="fo-table-scroll">
                <table className="fo-table fr-med" aria-label="Medical ledger">
                  <thead>
                    <tr><th>Player</th><th>Status</th><th>Injury</th><th>Weeks Out</th><th>IR</th><th>Actions</th></tr>
                  </thead>
                  <tbody>
                    {rows.map((p) => {
                      const action = irAction(p);
                      return (
                        <tr key={p.player_id}>
                          <td>
                            <a href={`#player-${p.player_id}`}><strong>{p.name}</strong></a>
                            <small>
                              {p.position}{p.overall !== null ? ` · ${p.overall} OVR` : ""}
                              {p.pending_move && <b className="fo-tag new-deal fr-pending">Queued: {MOVE_LABEL[p.pending_move]}</b>}
                            </small>
                          </td>
                          <td><span className={`fr-status ${p.status}`}>{p.status}</span></td>
                          <td>{p.injury ?? "—"}</td>
                          <td>{p.status === "HEALTHY" && !p.on_ir ? "—" : `${p.weeks_out} wk${p.weeks_out === 1 ? "" : "s"}`}</td>
                          <td>{p.on_ir ? "On IR" : p.ir_eligible ? "Eligible" : "—"}</td>
                          <td>
                            <div className="fr-actions">
                              {action && (
                                <button className="fr-btn" disabled={!!p.pending_move} onClick={() => setMove({ kind: action, player: p })}>
                                  {MOVE_LABEL[action]}
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <p className="fo-basis">Weeks out are Project Mu's injury durations. IR moves are previewed, then queued for the next league office write.</p>
          </>
        )}
      </section>
      {move && (
        <MoveDialog abbr={teamAbbr} kind={move.kind} player={move.player} onClose={() => setMove(null)} onQueued={reload} />
      )}
    </section>
  );
}
