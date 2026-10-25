import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { gradeTierClass } from "./ConsensusBigBoard";

interface AwardPlayer {
  prospect_key: string;
  full_name: string;
  position: string;
  college: string;
  conference?: string;
  rank: number;
  grade: number;
  round_label: string;
  delta_rank?: number;
  heisman_rank?: number;
  is_award_finalist?: boolean;
  stat_summary: string;
  headline?: string;
  blurb?: string;
  events?: Array<{ kind: string; headline: string; blurb: string }>;
}

interface AwardBucket {
  award: string;
  positions: string[];
  finalists: AwardPlayer[];
  watchlist: AwardPlayer[];
}

export const CollegeAwardsView: React.FC<{
  draftSeason?: number;
  selectedStep: number | null;
  onSelectProspect: (prospectKey: string) => void;
}> = ({ draftSeason, selectedStep, onSelectProspect }) => {
  const [heisman, setHeisman] = useState<AwardPlayer[]>([]);
  const [awards, setAwards] = useState<AwardBucket[]>([]);
  const [risers, setRisers] = useState<AwardPlayer[]>([]);
  const [fallers, setFallers] = useState<AwardPlayer[]>([]);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams();
    if (draftSeason) params.set("draft_season", String(draftSeason));
    if (selectedStep != null) params.set("step", String(selectedStep));

    const moversParams = new URLSearchParams();
    if (selectedStep != null) moversParams.set("step", String(selectedStep));
    moversParams.set("limit", "8");

    Promise.all([
      fetch(`${API_BASE}/api/draft/awards?${params.toString()}`).then((r) => r.json()),
      fetch(`${API_BASE}/api/draft/movers?${moversParams.toString()}`).then((r) => r.json()),
    ])
      .then(([awData, movData]) => {
        setHeisman(awData.heisman_watch || []);
        setAwards(awData.awards || []);
        setRisers(movData.risers || []);
        setFallers(movData.fallers || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [draftSeason, selectedStep]);

  if (loading) {
    return (
      <div className="draft-section-card" style={{ textAlign: "center", padding: "40px", color: "#64748b" }}>
        Loading College Honors, Heisman Watch &amp; Stock Movers…
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "26px" }}>
      {/* Section 1: National Player of the Year / Heisman Watch */}
      <div className="draft-section-card" style={{ background: "linear-gradient(135deg, #0f172a 0%, #1e293b 100%)", color: "#ffffff", border: "1px solid #f59e0b" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px", flexWrap: "wrap", gap: "10px" }}>
          <div>
            <span style={{ fontSize: "11px", fontWeight: 900, color: "#fbbf24", textTransform: "uppercase", letterSpacing: "0.08em" }}>
              Collegiate Trophy Race
            </span>
            <h3 style={{ margin: "2px 0 0", fontSize: "20px", fontWeight: 900 }}>
              🏆 Heisman &amp; National Player of the Year Watch
            </h3>
          </div>
          <span style={{ fontSize: "12px", color: "#94a3b8" }}>
            Click any contender to open their game-by-game dossier
          </span>
        </div>

        <div className="draft-grid-2">
          {heisman.map((p) => (
            <div
              key={p.prospect_key}
              onClick={() => onSelectProspect(p.prospect_key)}
              style={{
                background: "rgba(255, 255, 255, 0.06)",
                border: "1px solid rgba(255, 255, 255, 0.12)",
                borderRadius: "10px",
                padding: "14px 16px",
                cursor: "pointer",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: "12px",
              }}
            >
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                  <span style={{ background: "#f59e0b", color: "#090e1a", fontWeight: 900, fontSize: "11px", padding: "2px 7px", borderRadius: "4px" }}>
                    #{p.heisman_rank}
                  </span>
                  <span style={{ fontWeight: 800, fontSize: "15px", color: "#ffffff" }}>{p.full_name}</span>
                  <span style={{ fontSize: "11px", fontWeight: 800, color: "#fbbf24" }}>{p.position}</span>
                  <span style={{ fontSize: "12px", color: "#cbd5e1" }}>· {p.college}</span>
                </div>
                <div style={{ fontFamily: "monospace", fontSize: "12px", color: "#e2e8f0" }}>
                  {p.stat_summary}
                </div>
              </div>
              <div style={{ textAlign: "right", flexShrink: 0 }}>
                <div style={{ fontSize: "11px", color: "#94a3b8" }}>Board #{p.rank}</div>
                <div style={{ fontSize: "17px", fontWeight: 900, color: "#fbbf24" }}>{p.grade?.toFixed(1)}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Section 2: Weekly Stock Risers & Fallers Radar */}
      <div className="draft-grid-2">
        {/* Risers */}
        <div className="draft-section-card" style={{ borderTop: "4px solid #10b981" }}>
          <h3 style={{ margin: "0 0 14px", fontSize: "16px", fontWeight: 900, color: "#0f172a" }}>
            📈 Biggest Weekly Stock Risers
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {risers.length === 0 ? (
              <div style={{ color: "#64748b", fontSize: "13px" }}>No major risers recorded for this step.</div>
            ) : (
              risers.map((r) => (
                <div
                  key={r.prospect_key}
                  onClick={() => onSelectProspect(r.prospect_key)}
                  style={{
                    padding: "10px 12px",
                    border: "1px solid #e2e8f0",
                    borderRadius: "8px",
                    cursor: "pointer",
                    background: "#f8fafc",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <span className="draft-delta-badge up">▲{r.delta_rank}</span>
                      <strong style={{ fontSize: "14px", color: "#0f172a" }}>{r.full_name}</strong>
                      <span className="draft-pos-badge">{r.position}</span>
                      <span style={{ fontSize: "12px", color: "#475569" }}>{r.college}</span>
                    </div>
                    <span style={{ fontSize: "12px", fontWeight: 800, color: "#0f172a" }}>
                      Now #{r.rank} ({r.grade?.toFixed(1)})
                    </span>
                  </div>
                  {r.events && r.events[0] && (
                    <div style={{ fontSize: "12px", color: "#334155", marginTop: "4px" }}>
                      {r.events[0].blurb || r.events[0].headline}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        {/* Fallers */}
        <div className="draft-section-card" style={{ borderTop: "4px solid #ef4444" }}>
          <h3 style={{ margin: "0 0 14px", fontSize: "16px", fontWeight: 900, color: "#0f172a" }}>
            📉 Biggest Weekly Stock Fallers
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {fallers.length === 0 ? (
              <div style={{ color: "#64748b", fontSize: "13px" }}>No major fallers recorded for this step.</div>
            ) : (
              fallers.map((f) => (
                <div
                  key={f.prospect_key}
                  onClick={() => onSelectProspect(f.prospect_key)}
                  style={{
                    padding: "10px 12px",
                    border: "1px solid #e2e8f0",
                    borderRadius: "8px",
                    cursor: "pointer",
                    background: "#f8fafc",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <span className="draft-delta-badge down">▼{Math.abs(f.delta_rank || 0)}</span>
                      <strong style={{ fontSize: "14px", color: "#0f172a" }}>{f.full_name}</strong>
                      <span className="draft-pos-badge">{f.position}</span>
                      <span style={{ fontSize: "12px", color: "#475569" }}>{f.college}</span>
                    </div>
                    <span style={{ fontSize: "12px", fontWeight: 800, color: "#0f172a" }}>
                      Now #{f.rank} ({f.grade?.toFixed(1)})
                    </span>
                  </div>
                  {f.events && f.events[0] && (
                    <div style={{ fontSize: "12px", color: "#334155", marginTop: "4px" }}>
                      {f.events[0].blurb || f.events[0].headline}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Section 3: National Positional Awards Showcase */}
      <div>
        <h3 style={{ margin: "0 0 14px", fontSize: "17px", fontWeight: 900, color: "#0f172a" }}>
          🏅 National Positional Awards &amp; Watchlists
        </h3>
        <div className="draft-grid-3">
          {awards.map((aw) => {
            const list = aw.finalists.length > 0 ? aw.finalists : aw.watchlist;
            const isFinalistStage = aw.finalists.length > 0;
            return (
              <div key={aw.award} className="draft-section-card">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
                  <div>
                    <h4 style={{ margin: 0, fontSize: "15px", fontWeight: 900, color: "#0f172a" }}>{aw.award}</h4>
                    <div style={{ fontSize: "11px", color: "#64748b", fontWeight: 700 }}>
                      Positions: {aw.positions.join(" / ")}
                    </div>
                  </div>
                  <span
                    style={{
                      fontSize: "10px",
                      fontWeight: 800,
                      padding: "3px 8px",
                      borderRadius: "999px",
                      background: isFinalistStage ? "#fef3c7" : "#f1f5f9",
                      color: isFinalistStage ? "#92400e" : "#475569",
                    }}
                  >
                    {isFinalistStage ? "OFFICIAL FINALISTS" : "WATCHLIST"}
                  </span>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  {list.slice(0, 5).map((p) => (
                    <div
                      key={p.prospect_key}
                      onClick={() => onSelectProspect(p.prospect_key)}
                      style={{
                        padding: "8px 10px",
                        borderRadius: "6px",
                        border: "1px solid #f1f5f9",
                        background: "#f8fafc",
                        cursor: "pointer",
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <div>
                          <strong style={{ fontSize: "13px", color: "#0f172a" }}>{p.full_name}</strong>{" "}
                          <span style={{ fontSize: "11px", color: "#64748b" }}>
                            ({p.position} · {p.college})
                          </span>
                        </div>
                        <span className={`draft-grade-pill ${gradeTierClass(p.grade)}`} style={{ fontSize: "11px", padding: "2px 7px" }}>
                          #{p.rank} · {p.grade?.toFixed(1)}
                        </span>
                      </div>
                      {p.stat_summary && (
                        <div style={{ fontFamily: "monospace", fontSize: "11px", color: "#475569", marginTop: "4px" }}>
                          {p.stat_summary}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default CollegeAwardsView;
