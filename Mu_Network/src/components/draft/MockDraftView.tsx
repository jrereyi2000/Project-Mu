import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { teamLogo } from "../team/format";
import { gradeTierClass } from "./ConsensusBigBoard";

interface MockPickItem {
  pick: number;
  team_id: number;
  team: string;
  team_abbr: string;
  original_team_abbr?: string;
  traded?: boolean;
  record: string;
  locked: boolean;
  selection_status?: string;
  fit_score?: number;
  rationale: string;
  prospect: {
    prospect_key: string;
    full_name: string;
    position: string;
    position_group: string;
    college: string;
    school_display: string;
    conference?: string;
    height?: string;
    weight?: number;
    age?: number;
    class_year?: string;
    consensus_rank?: number;
    grade?: number;
    round_label?: string;
    stat_summary?: string;
  };
}

export const MockDraftView: React.FC<{
  onSelectProspect: (prospectKey: string) => void;
}> = ({ onSelectProspect }) => {
  const [picks, setPicks] = useState<MockPickItem[]>([]);
  const [draftSeason, setDraftSeason] = useState<number>(2037);
  const [analyst, setAnalyst] = useState<string>("Mel Kiper Jr. & Gridiron Draft Lab");
  const [loading, setLoading] = useState<boolean>(true);
  const [posFilter, setPosFilter] = useState<string>("ALL");
  const [search, setSearch] = useState<string>("");

  useEffect(() => {
    setLoading(true);
    fetch(`${API_BASE}/api/draft/mock`)
      .then((r) => r.json())
      .then((data) => {
        setPicks(data.picks || []);
        if (data.draft_season) setDraftSeason(data.draft_season);
        if (data.analyst) setAnalyst(data.analyst);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const filtered = picks.filter((pk) => {
    if (posFilter !== "ALL" && pk.prospect.position_group !== posFilter) return false;
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      const hay = `${pk.team} ${pk.team_abbr} ${pk.prospect.full_name} ${pk.prospect.college} ${pk.prospect.position}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
      {/* Mock Header Card */}
      <div className="draft-filter-card" style={{ borderLeft: "4px solid #f59e0b" }}>
        <div className="draft-filter-row">
          <div>
            <span style={{ fontSize: "11px", fontWeight: 900, textTransform: "uppercase", letterSpacing: "0.08em", color: "#b45309" }}>
              Official Media Projection · {analyst}
            </span>
            <h3 style={{ margin: "2px 0 0", fontSize: "20px", fontWeight: 900, color: "#0f172a" }}>
              {draftSeason} NFL Round 1 Mock Draft (Picks 1–32)
            </h3>
            <p style={{ margin: "4px 0 0", fontSize: "12.5px", color: "#475569" }}>
              Synthesizing public consensus scouting grades, franchise positional deficiencies, starter age cliffs, and Round 1 positional value multipliers.
            </p>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            <input
              type="search"
              className="draft-search-input"
              placeholder="Filter by team, prospect, school…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select className="draft-select" value={posFilter} onChange={(e) => setPosFilter(e.target.value)}>
              <option value="ALL">All Positions</option>
              {["QB", "RB", "WR", "TE", "OT", "IOL", "EDGE", "DT", "LB", "CB", "S"].map((g) => (
                <option key={g} value={g}>
                  {g}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* 32-Pick Cards */}
      {loading ? (
        <div className="draft-section-card" style={{ textAlign: "center", padding: "40px", color: "#64748b" }}>
          Simulating Round 1 Media Mock Draft…
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          {filtered.map((pk) => {
            const p = pk.prospect;
            return (
              <div
                key={pk.pick}
                className="draft-section-card"
                style={{
                  padding: "16px 20px",
                  display: "grid",
                  gridTemplateColumns: "64px 230px 1fr",
                  alignItems: "center",
                  gap: "18px",
                }}
              >
                {/* Pick Number */}
                <div
                  style={{
                    background: "#0f172a",
                    color: "#fbbf24",
                    borderRadius: "10px",
                    padding: "10px 6px",
                    textAlign: "center",
                  }}
                >
                  <div style={{ fontSize: "10px", fontWeight: 800, color: "#94a3b8", textTransform: "uppercase" }}>
                    PICK
                  </div>
                  <div style={{ fontSize: "22px", fontWeight: 900, lineHeight: 1.1 }}>#{pk.pick}</div>
                </div>

                {/* Team Info */}
                <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                  <img
                    src={teamLogo(pk.team_abbr)}
                    alt={pk.team_abbr}
                    style={{ width: "40px", height: "40px", objectFit: "contain" }}
                  />
                  <div>
                    <div style={{ fontWeight: 900, fontSize: "15px", color: "#0f172a" }}>
                      {pk.team}
                    </div>
                    <div style={{ fontSize: "11px", color: "#64748b", display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
                      {pk.traded && pk.original_team_abbr && (
                        <span style={{ background: "#fef3c7", color: "#92400e", fontWeight: 800, padding: "1px 6px", borderRadius: "4px" }}>
                          via {pk.original_team_abbr}
                        </span>
                      )}
                      <span>{pk.record ? `(${pk.record})` : ""}</span>
                      <span
                        style={{
                          fontWeight: 700,
                          color: pk.locked ? "#15803d" : "#64748b",
                        }}
                      >
                        {pk.locked ? "🔒 Locked" : "Provisional"}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Prospect & Kiper Rationale */}
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "10px" }}>
                    <div
                      onClick={() => onSelectProspect(p.prospect_key)}
                      style={{ display: "flex", alignItems: "center", gap: "10px", cursor: "pointer", flexWrap: "wrap" }}
                    >
                      <span style={{ fontSize: "17px", fontWeight: 900, color: "#0f172a", textDecoration: "underline", textDecorationColor: "#cbd5e1" }}>
                        {p.full_name}
                      </span>
                      <span className="draft-pos-badge">{p.position}</span>
                      <span style={{ fontWeight: 700, fontSize: "13px", color: "#334155" }}>
                        {p.school_display} {p.conference ? `(${p.conference})` : ""}
                      </span>
                      <span style={{ fontSize: "12px", color: "#64748b" }}>
                        {p.height || "—"} · {p.weight ? `${p.weight} lbs` : "—"}
                      </span>
                    </div>

                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      {p.stat_summary && <span className="draft-stat-chip">{p.stat_summary}</span>}
                      {p.grade != null && (
                        <span className={`draft-grade-pill ${gradeTierClass(p.grade)}`}>
                          #{p.consensus_rank} · {p.grade.toFixed(1)}
                        </span>
                      )}
                    </div>
                  </div>

                  <div
                    style={{
                      fontSize: "13px",
                      color: "#334155",
                      background: "#f8fafc",
                      borderLeft: "3px solid #cbd5e1",
                      padding: "6px 10px",
                      borderRadius: "0 6px 6px 0",
                    }}
                  >
                    <strong style={{ color: "#0f172a" }}>Kiper's Take:</strong> {pk.rationale}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default MockDraftView;
