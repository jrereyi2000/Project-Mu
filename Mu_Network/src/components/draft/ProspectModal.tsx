import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import CollegeGameLog, { type GameLogEntry, type SeasonStatsBlock } from "./CollegeGameLog";
import CombineCard, { type CombineProfileData } from "./CombineCard";

interface ProspectDossier {
  prospect_key: string;
  draft_season: number;
  profile: {
    full_name: string;
    position: string;
    position_group: string;
    position_words: string;
    college: string;
    school_display: string;
    nickname?: string | null;
    conference?: string | null;
    level?: string | null;
    height?: string | null;
    height_in?: number | null;
    weight?: number | null;
    age?: number | null;
    class_year?: string | null;
    status: string;
    rank: number;
    grade: number;
    round_label: string;
    delta_rank?: number | null;
    delta_grade?: number | null;
    start_rank: number;
    start_grade: number;
    net_rank_change: number;
    peak_rank: number;
    peak_grade: number;
    top_headline?: string | null;
  };
  season_stats: SeasonStatsBlock;
  game_log: GameLogEntry[];
  combine: CombineProfileData;
  grade_history: Array<{
    step: number;
    step_label: string;
    week?: number | null;
    stage?: number | null;
    rank: number;
    grade: number;
    round_label: string;
    delta_rank?: number | null;
    delta_grade?: number | null;
  }>;
  timeline: Array<{
    step: number;
    step_label: string;
    phase: string;
    kind: string;
    team?: string | null;
    headline?: string | null;
    blurb?: string | null;
  }>;
}

export const ProspectModal: React.FC<{
  prospectKey: string;
  step?: number | null;
  onClose: () => void;
}> = ({ prospectKey, step, onClose }) => {
  const [dossier, setDossier] = useState<ProspectDossier | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string>("");
  const [tab, setTab] = useState<"gamelog" | "combine" | "timeline">("gamelog");

  useEffect(() => {
    setLoading(true);
    setError("");
    const q = step != null ? `?step=${step}` : "";
    fetch(`${API_BASE}/api/draft/prospect/${encodeURIComponent(prospectKey)}${q}`)
      .then((r) => {
        if (!r.ok) throw new Error(`Failed to load dossier (${r.status})`);
        return r.json();
      })
      .then((data) => {
        setDossier(data);
        setLoading(false);
      })
      .catch((e) => {
        setError(String(e.message || e));
        setLoading(false);
      });
  }, [prospectKey, step]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const p = dossier?.profile;

  return (
    <div className="draft-modal-backdrop" onClick={onClose}>
      <div className="draft-modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="draft-modal-header">
          <button className="draft-modal-close" onClick={onClose} aria-label="Close dossier">
            ✕
          </button>
          {loading ? (
            <div style={{ padding: "12px 0", color: "#94a3b8" }}>Loading Scouting Dossier…</div>
          ) : error || !p ? (
            <div style={{ padding: "12px 0", color: "#fca5a5" }}>{error || "Prospect not found."}</div>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: "18px", paddingRight: "36px" }}>
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "6px" }}>
                  <span style={{ background: "#f59e0b", color: "#090e1a", fontWeight: 900, fontSize: "13px", padding: "3px 9px", borderRadius: "6px" }}>
                    RANK #{p.rank}
                  </span>
                  <span style={{ background: "rgba(255,255,255,0.12)", color: "#f8fafc", fontWeight: 800, fontSize: "12px", padding: "3px 9px", borderRadius: "6px" }}>
                    {p.position} · {p.position_words.toUpperCase()}
                  </span>
                  <span style={{ color: "#fbbf24", fontWeight: 700, fontSize: "12px" }}>
                    {p.round_label.toUpperCase()}
                  </span>
                  {p.net_rank_change !== 0 && (
                    <span
                      style={{
                        fontSize: "11px",
                        fontWeight: 800,
                        padding: "2px 8px",
                        borderRadius: "999px",
                        background: p.net_rank_change > 0 ? "rgba(16, 185, 129, 0.2)" : "rgba(239, 68, 68, 0.2)",
                        color: p.net_rank_change > 0 ? "#6ee7b7" : "#fca5a5",
                      }}
                    >
                      {p.net_rank_change > 0 ? `▲ +${p.net_rank_change}` : `▼ ${p.net_rank_change}`} since Wk 4
                    </span>
                  )}
                </div>
                <h2 style={{ margin: 0, fontSize: "26px", fontWeight: 900, letterSpacing: "0.02em" }}>
                  {p.full_name}
                </h2>
                <div style={{ marginTop: "6px", fontSize: "13px", color: "#cbd5e1", display: "flex", flexWrap: "wrap", gap: "12px" }}>
                  <span>
                    <strong style={{ color: "#ffffff" }}>
                      {p.school_display} {p.nickname || ""}
                    </strong>
                    {p.conference ? ` (${p.conference})` : ""}
                  </span>
                  <span>·</span>
                  <span>
                    {p.height || "—"} / {p.weight ? `${p.weight} lbs` : "—"}
                  </span>
                  <span>·</span>
                  <span style={{ textTransform: "capitalize" }}>
                    Age {p.age || "—"} ({p.class_year || "Upperclassman"})
                  </span>
                </div>
              </div>

              {/* Consensus Grade Box */}
              <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
                <div
                  style={{
                    background: "rgba(255,255,255,0.07)",
                    border: "1px solid rgba(245, 158, 11, 0.45)",
                    borderRadius: "12px",
                    padding: "10px 18px",
                    textAlign: "center",
                  }}
                >
                  <div style={{ fontSize: "10px", fontWeight: 800, color: "#fbbf24", letterSpacing: "0.08em", textTransform: "uppercase" }}>
                    Consensus Grade
                  </div>
                  <div style={{ fontSize: "28px", fontWeight: 900, color: "#ffffff", lineHeight: 1.1 }}>
                    {p.grade?.toFixed(1)}
                  </div>
                  <div style={{ fontSize: "11px", color: "#94a3b8", marginTop: "2px" }}>
                    Peak #{p.peak_rank} ({p.peak_grade?.toFixed(1)})
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Dossier Sub-Tabs */}
          {dossier && (
            <div style={{ display: "flex", gap: "8px", marginTop: "18px", borderTop: "1px solid rgba(255,255,255,0.1)", paddingTop: "12px" }}>
              <button
                onClick={() => setTab("gamelog")}
                style={{
                  background: tab === "gamelog" ? "#f59e0b" : "rgba(255,255,255,0.08)",
                  color: tab === "gamelog" ? "#090e1a" : "#e2e8f0",
                  border: "none",
                  borderRadius: "6px",
                  padding: "7px 14px",
                  fontSize: "12px",
                  fontWeight: 800,
                  cursor: "pointer",
                }}
              >
                📊 College Game Log &amp; Season Stats ({dossier.season_stats.games})
              </button>
              <button
                onClick={() => setTab("combine")}
                style={{
                  background: tab === "combine" ? "#f59e0b" : "rgba(255,255,255,0.08)",
                  color: tab === "combine" ? "#090e1a" : "#e2e8f0",
                  border: "none",
                  borderRadius: "6px",
                  padding: "7px 14px",
                  fontSize: "12px",
                  fontWeight: 800,
                  cursor: "pointer",
                }}
              >
                ⏱️ Combine &amp; Athleticism
              </button>
              <button
                onClick={() => setTab("timeline")}
                style={{
                  background: tab === "timeline" ? "#f59e0b" : "rgba(255,255,255,0.08)",
                  color: tab === "timeline" ? "#090e1a" : "#e2e8f0",
                  border: "none",
                  borderRadius: "6px",
                  padding: "7px 14px",
                  fontSize: "12px",
                  fontWeight: 800,
                  cursor: "pointer",
                }}
              >
                📰 Scouting Storylines &amp; Stock Trajectory ({dossier.timeline.length})
              </button>
            </div>
          )}
        </div>

        {/* Modal Body */}
        {dossier && (
          <div className="draft-modal-body">
            {tab === "gamelog" && (
              <CollegeGameLog seasonStats={dossier.season_stats} gameLog={dossier.game_log} />
            )}

            {tab === "combine" && (
              <CombineCard combine={dossier.combine} position={dossier.profile.position_group} />
            )}

            {tab === "timeline" && (
              <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
                {/* Stock Trajectory Progression Strip */}
                <div className="draft-section-card">
                  <div style={{ fontSize: "11px", fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.06em", color: "#64748b", marginBottom: "12px" }}>
                    Weekly Consensus Board Progression
                  </div>
                  <div style={{ display: "flex", gap: "8px", overflowX: "auto", paddingBottom: "6px" }}>
                    {dossier.grade_history.map((gh) => (
                      <div
                        key={gh.step}
                        style={{
                          minWidth: "88px",
                          background: "#f8fafc",
                          border: "1px solid #e2e8f0",
                          borderRadius: "8px",
                          padding: "8px 10px",
                          textAlign: "center",
                          flexShrink: 0,
                        }}
                      >
                        <div style={{ fontSize: "10px", fontWeight: 700, color: "#64748b" }}>Step {gh.step}</div>
                        <div style={{ fontSize: "16px", fontWeight: 900, color: "#0f172a", marginTop: "2px" }}>
                          #{gh.rank}
                        </div>
                        <div style={{ fontSize: "11px", fontWeight: 700, color: "#b45309" }}>
                          {gh.grade?.toFixed(1)}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Chronological Event Feed */}
                <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                  {dossier.timeline.length === 0 ? (
                    <div style={{ color: "#64748b", fontSize: "13px" }}>No scouting notes recorded yet.</div>
                  ) : (
                    dossier.timeline.map((ev, i) => (
                      <div
                        key={`${ev.step}-${i}`}
                        className="draft-section-card"
                        style={{
                          padding: "14px 18px",
                          borderLeft:
                            ev.kind === "AWARD"
                              ? "4px solid #f59e0b"
                              : ev.kind === "STORYLINE"
                              ? "4px solid #3b82f6"
                              : ev.kind === "VISIT"
                              ? "4px solid #8b5cf6"
                              : "4px solid #cbd5e1",
                        }}
                      >
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                          <span style={{ fontSize: "11px", fontWeight: 800, color: "#64748b", textTransform: "uppercase" }}>
                            {ev.step_label} · {ev.kind}
                            {ev.team ? ` (${ev.team})` : ""}
                          </span>
                        </div>
                        {ev.headline && (
                          <div style={{ fontSize: "14px", fontWeight: 800, color: "#0f172a", marginBottom: "3px" }}>
                            {ev.headline}
                          </div>
                        )}
                        {ev.blurb && <div style={{ fontSize: "13px", color: "#334155" }}>{ev.blurb}</div>}
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default ProspectModal;
