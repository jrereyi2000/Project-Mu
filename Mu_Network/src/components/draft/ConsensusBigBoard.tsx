import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";

export interface BoardProspect {
  prospect_key: string;
  draft_season: number;
  step: number;
  rank: number;
  grade: number;
  round_label: string;
  delta_rank?: number | null;
  delta_grade?: number | null;
  full_name: string;
  position: string;
  position_group: string;
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
  top_headline?: string | null;
  stat_group: string;
  games: number;
  stat_summary: string;
}

const POS_GROUPS = ["ALL", "QB", "RB", "WR", "TE", "OT", "IOL", "EDGE", "DT", "LB", "CB", "S", "SPECIALISTS"];
const ROUND_TIERS = [
  { key: "ALL", label: "All Prospects" },
  { key: "R1", label: "Round 1 (1–32)" },
  { key: "DAY2", label: "Day 2 (33–100)" },
  { key: "DAY3", label: "Day 3 (101–224)" },
  { key: "UDFA", label: "Priority UDFA" },
];

export function gradeTierClass(grade?: number | null): string {
  const g = grade || 0;
  if (g >= 88.0) return "elite";
  if (g >= 79.5) return "r1";
  if (g >= 72.0) return "day2";
  return "day3";
}

export const ConsensusBigBoard: React.FC<{
  draftSeason?: number;
  selectedStep: number | null;
  storedSteps: Array<{ step: number; label: string }>;
  onSelectStep: (step: number | null) => void;
  onSelectProspect: (prospectKey: string) => void;
}> = ({ draftSeason, selectedStep, storedSteps, onSelectStep, onSelectProspect }) => {
  const [prospects, setProspects] = useState<BoardProspect[]>([]);
  const [conferences, setConferences] = useState<string[]>([]);
  const [totalMatching, setTotalMatching] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(true);

  const [posFilter, setPosFilter] = useState<string>("ALL");
  const [roundFilter, setRoundFilter] = useState<string>("ALL");
  const [confFilter, setConfFilter] = useState<string>("ALL");
  const [search, setSearch] = useState<string>("");

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams();
    if (draftSeason) params.set("draft_season", String(draftSeason));
    if (selectedStep != null) params.set("step", String(selectedStep));
    if (posFilter !== "ALL") params.set("position", posFilter);
    if (roundFilter !== "ALL") params.set("round_filter", roundFilter);
    if (confFilter !== "ALL") params.set("conference", confFilter);
    if (search.trim()) params.set("search", search.trim());
    params.set("limit", "250");

    fetch(`${API_BASE}/api/draft/board?${params.toString()}`)
      .then((r) => r.json())
      .then((data) => {
        setProspects(data.prospects || []);
        if (data.conferences?.length && conferences.length === 0) {
          setConferences(data.conferences);
        }
        setTotalMatching(data.total_matching || 0);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [draftSeason, selectedStep, posFilter, roundFilter, confFilter, search]);

  return (
    <div>
      {/* Filter Controls Card */}
      <div className="draft-filter-card">
        <div className="draft-filter-row">
          <div className="draft-pill-group">
            {POS_GROUPS.map((pos) => (
              <button
                key={pos}
                className={`draft-filter-pill ${posFilter === pos ? "active" : ""}`}
                onClick={() => setPosFilter(pos)}
              >
                {pos}
              </button>
            ))}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            <input
              type="search"
              className="draft-search-input"
              placeholder="Search name, school, conference…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {storedSteps.length > 0 && (
              <select
                className="draft-select"
                value={selectedStep ?? ""}
                onChange={(e) => onSelectStep(e.target.value ? Number(e.target.value) : null)}
                aria-label="Scouting step"
              >
                <option value="">Latest Step ({storedSteps[storedSteps.length - 1]?.label})</option>
                {[...storedSteps].reverse().map((s) => (
                  <option key={s.step} value={s.step}>
                    Step {s.step}: {s.label}
                  </option>
                ))}
              </select>
            )}
          </div>
        </div>

        <div className="draft-filter-row" style={{ borderTop: "1px solid #f1f5f9", paddingTop: "12px" }}>
          <div className="draft-pill-group">
            {ROUND_TIERS.map((rt) => (
              <button
                key={rt.key}
                className={`draft-filter-pill ${roundFilter === rt.key ? "active" : ""}`}
                onClick={() => setRoundFilter(rt.key)}
              >
                {rt.label}
              </button>
            ))}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span style={{ fontSize: "12px", fontWeight: 700, color: "#64748b" }}>Conference / Tier:</span>
            <select
              className="draft-select"
              value={confFilter}
              onChange={(e) => setConfFilter(e.target.value)}
              aria-label="Filter by conference"
            >
              <option value="ALL">All Conferences</option>
              <option value="P4">Power 4 (SEC / Big Ten / ACC / Big 12)</option>
              <option value="FBS">Group of 5 / FBS</option>
              <option value="FCS">FCS / Small School</option>
              {conferences.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <span style={{ fontSize: "12px", fontWeight: 700, color: "#475569" }}>
              {totalMatching} Prospects
            </span>
          </div>
        </div>
      </div>

      {/* Consensus Board Table */}
      <div className="draft-table-card">
        <div className="draft-table-scroll">
          <table className="draft-board-table">
            <thead>
              <tr>
                <th style={{ width: "90px" }}>Rank</th>
                <th>Prospect</th>
                <th>Pos</th>
                <th>School &amp; Conf</th>
                <th>Measurables</th>
                <th>Consensus Grade</th>
                <th>Projection</th>
                <th>College Season Statline</th>
                <th>Latest Scouting Note</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={9} style={{ textAlign: "center", padding: "36px", color: "#64748b" }}>
                    Loading Consensus Big Board…
                  </td>
                </tr>
              ) : prospects.length === 0 ? (
                <tr>
                  <td colSpan={9} style={{ textAlign: "center", padding: "36px", color: "#64748b" }}>
                    No prospects match the active filters.
                  </td>
                </tr>
              ) : (
                prospects.map((p) => {
                  const dr = p.delta_rank || 0;
                  return (
                    <tr key={p.prospect_key} onClick={() => onSelectProspect(p.prospect_key)}>
                      <td>
                        <div className="draft-rank-cell">
                          <span>#{p.rank}</span>
                          {dr > 0 ? (
                            <span className="draft-delta-badge up" title={`Rose ${dr} spots this step`}>
                              ▲{dr}
                            </span>
                          ) : dr < 0 ? (
                            <span className="draft-delta-badge down" title={`Fell ${Math.abs(dr)} spots this step`}>
                              ▼{Math.abs(dr)}
                            </span>
                          ) : (
                            <span className="draft-delta-badge flat">—</span>
                          )}
                        </div>
                      </td>
                      <td>
                        <div style={{ fontWeight: 800, color: "#0f172a", fontSize: "14px" }}>{p.full_name}</div>
                        <div style={{ fontSize: "11px", color: "#64748b", textTransform: "capitalize" }}>
                          {p.class_year || "Upperclassman"} {p.age ? `· Age ${p.age}` : ""}
                        </div>
                      </td>
                      <td>
                        <span className="draft-pos-badge">{p.position}</span>
                      </td>
                      <td>
                        <div style={{ fontWeight: 700, color: "#1e293b" }}>{p.school_display}</div>
                        <div style={{ fontSize: "11px", color: "#64748b" }}>
                          {p.conference || "Independent"} {p.level ? `· ${p.level}` : ""}
                        </div>
                      </td>
                      <td style={{ whiteSpace: "nowrap", fontSize: "12.5px", color: "#334155", fontWeight: 600 }}>
                        {p.height || "—"} · {p.weight ? `${p.weight} lbs` : "—"}
                      </td>
                      <td>
                        <span className={`draft-grade-pill ${gradeTierClass(p.grade)}`}>
                          {p.grade != null ? p.grade.toFixed(1) : "—"}
                        </span>
                      </td>
                      <td style={{ whiteSpace: "nowrap", fontSize: "12px", fontWeight: 700, color: "#334155" }}>
                        {p.round_label}
                      </td>
                      <td>
                        <span className="draft-stat-chip">{p.stat_summary}</span>
                      </td>
                      <td style={{ fontSize: "12px", color: "#475569", maxWidth: "240px" }}>
                        {p.top_headline ? (
                          <span style={{ fontWeight: 600, color: "#0f172a" }}>{p.top_headline}</span>
                        ) : (
                          <span style={{ color: "#94a3b8" }}>Click to open full game log &amp; dossier →</span>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default ConsensusBigBoard;
