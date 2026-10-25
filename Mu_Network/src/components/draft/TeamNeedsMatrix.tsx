import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { teamLogo } from "../team/format";

interface TeamNeedItem {
  rank: number;
  group: string;
  label: string;
  score: number;
  urgency: "CRITICAL" | "HIGH" | "MODERATE" | "DEPTH" | string;
  reasons: string[];
}

interface TeamDraftPick {
  round: number;
  round_pick: number;
  overall_pick: number;
  original_team: string;
  owner_team: string;
  status: "OWN" | "ACQUIRED";
  value_points: number;
  locked: boolean;
}

interface TeamNeedsRow {
  team_id: number;
  abbreviation: string;
  city: string;
  name: string;
  full_name: string;
  conference: string;
  division: string;
  team_color?: string;
  record: string;
  team_ovr?: number | null;
  direction: string;
  gm_name?: string | null;
  needs: TeamNeedItem[];
  picks: TeamDraftPick[];
  round_1_picks: number[];
  total_picks: number;
  draft_capital_points: number;
  capital_rank: number;
  base_draft_slot: number;
}

function urgencyStyle(urgency: string): React.CSSProperties {
  if (urgency === "CRITICAL") return { background: "#fee2e2", color: "#991b1b", border: "1px solid #fca5a5" };
  if (urgency === "HIGH") return { background: "#fef3c7", color: "#92400e", border: "1px solid #fcd34d" };
  if (urgency === "MODERATE") return { background: "#e0f2fe", color: "#075985", border: "1px solid #7dd3fc" };
  return { background: "#f1f5f9", color: "#475569", border: "1px solid #cbd5e1" };
}

function directionStyle(dir: string): React.CSSProperties {
  if (dir === "CONTEND") return { background: "#dcfce7", color: "#166534" };
  if (dir === "REBUILD") return { background: "#fee2e2", color: "#991b1b" };
  return { background: "#e0f2fe", color: "#075985" };
}

export const TeamNeedsMatrix: React.FC = () => {
  const [teams, setTeams] = useState<TeamNeedsRow[]>([]);
  const [draftSeason, setDraftSeason] = useState<number>(2037);
  const [loading, setLoading] = useState<boolean>(true);
  const [confFilter, setConfFilter] = useState<string>("ALL");
  const [dirFilter, setDirFilter] = useState<string>("ALL");
  const [sortBy, setSortBy] = useState<"slot" | "capital">("slot");
  const [search, setSearch] = useState<string>("");

  useEffect(() => {
    setLoading(true);
    fetch(`${API_BASE}/api/draft/team-needs`)
      .then((r) => r.json())
      .then((data) => {
        setTeams(data.teams || []);
        if (data.draft_season) setDraftSeason(data.draft_season);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const filtered = teams
    .filter((t) => {
      if (confFilter !== "ALL" && t.conference !== confFilter) return false;
      if (dirFilter !== "ALL" && t.direction !== dirFilter) return false;
      if (search.trim()) {
        const q = search.trim().toLowerCase();
        const needsStr = t.needs.map((n) => `${n.group} ${n.label}`).join(" ").toLowerCase();
        if (!`${t.full_name} ${t.abbreviation} ${t.gm_name || ""} ${needsStr}`.toLowerCase().includes(q)) return false;
      }
      return true;
    })
    .sort((a, b) => (sortBy === "capital" ? a.capital_rank - b.capital_rank : a.base_draft_slot - b.base_draft_slot));

  return (
    <div>
      {/* Filter Bar */}
      <div className="draft-filter-card">
        <div className="draft-filter-row">
          <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
            <div className="draft-pill-group">
              {(["ALL", "AFC", "NFC"] as const).map((c) => (
                <button
                  key={c}
                  className={`draft-filter-pill ${confFilter === c ? "active" : ""}`}
                  onClick={() => setConfFilter(c)}
                >
                  {c === "ALL" ? "All 32 Teams" : c}
                </button>
              ))}
            </div>

            <div className="draft-pill-group">
              {(["ALL", "CONTEND", "RETOOL", "REBUILD"] as const).map((d) => (
                <button
                  key={d}
                  className={`draft-filter-pill ${dirFilter === d ? "active" : ""}`}
                  onClick={() => setDirFilter(d)}
                >
                  {d === "ALL" ? "All Directions" : d}
                </button>
              ))}
            </div>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            <input
              type="search"
              className="draft-search-input"
              placeholder="Filter by team, GM, or position need (e.g. QB)…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select
              className="draft-select"
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as "slot" | "capital")}
            >
              <option value="slot">Sort by Base Draft Order (1–32)</option>
              <option value="capital">Sort by Total Draft Capital Rank (#1–32)</option>
            </select>
          </div>
        </div>
      </div>

      {/* 32-Team Needs & Draft Capital Table */}
      <div className="draft-table-card">
        <div className="draft-table-scroll">
          <table className="draft-board-table">
            <thead>
              <tr>
                <th>Slot</th>
                <th>Franchise &amp; GM</th>
                <th>Direction</th>
                <th>Top Positional Needs (Urgency)</th>
                <th>{draftSeason} Draft Capital Index</th>
                <th>Owned {draftSeason} Picks (Rounds 1–7)</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={6} style={{ textAlign: "center", padding: "36px", color: "#64748b" }}>
                    Loading 32-Team Draft Capital &amp; Roster Needs…
                  </td>
                </tr>
              ) : (
                filtered.map((t) => (
                  <tr key={t.team_id} style={{ cursor: "default" }}>
                    <td style={{ fontWeight: 900, fontSize: "15px", color: "#0f172a" }}>#{t.base_draft_slot}</td>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                        <img
                          src={teamLogo(t.abbreviation)}
                          alt={t.abbreviation}
                          style={{ width: "32px", height: "32px", objectFit: "contain" }}
                        />
                        <div>
                          <div style={{ fontWeight: 800, color: "#0f172a", fontSize: "14px" }}>
                            {t.full_name}{" "}
                            <span style={{ fontWeight: 600, color: "#64748b", fontSize: "12px" }}>({t.record})</span>
                          </div>
                          <div style={{ fontSize: "11px", color: "#64748b" }}>
                            GM: {t.gm_name || "Front Office"} {t.team_ovr ? `· ${t.team_ovr} OVR` : ""}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <span
                        style={{
                          ...directionStyle(t.direction),
                          fontSize: "11px",
                          fontWeight: 800,
                          padding: "3px 9px",
                          borderRadius: "999px",
                        }}
                      >
                        {t.direction}
                      </span>
                    </td>
                    <td>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                        {t.needs.slice(0, 5).map((n) => (
                          <span
                            key={n.group}
                            title={`${n.label}: ${n.reasons.join(", ") || "Roster depth"} (Need score ${n.score})`}
                            style={{
                              ...urgencyStyle(n.urgency),
                              fontSize: "11px",
                              fontWeight: 800,
                              padding: "3px 8px",
                              borderRadius: "6px",
                              display: "inline-flex",
                              alignItems: "center",
                              gap: "4px",
                            }}
                          >
                            <span>{n.group}</span>
                            <span style={{ fontSize: "9px", opacity: 0.8 }}>{n.urgency}</span>
                          </span>
                        ))}
                      </div>
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <div style={{ fontWeight: 900, fontSize: "14px", color: "#0f172a" }}>
                        Rank #{t.capital_rank}{" "}
                        <span style={{ fontWeight: 600, fontSize: "12px", color: "#64748b" }}>
                          ({t.draft_capital_points.toLocaleString()} pts)
                        </span>
                      </div>
                      <div style={{ fontSize: "11px", color: "#475569" }}>
                        {t.total_picks} total picks ·{" "}
                        {t.round_1_picks.length > 0
                          ? `R1: ${t.round_1_picks.map((p) => `#${p}`).join(", ")}`
                          : "No Round 1 pick"}
                      </div>
                    </td>
                    <td>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: "5px", maxWidth: "420px" }}>
                        {t.picks.map((pk) => (
                          <span
                            key={`${pk.round}-${pk.overall_pick}`}
                            style={{
                              fontSize: "11px",
                              fontWeight: 700,
                              padding: "2px 7px",
                              borderRadius: "5px",
                              background:
                                pk.round === 1
                                  ? "#fef3c7"
                                  : pk.status === "ACQUIRED"
                                  ? "#dcfce7"
                                  : "#f1f5f9",
                              color:
                                pk.round === 1
                                  ? "#92400e"
                                  : pk.status === "ACQUIRED"
                                  ? "#166534"
                                  : "#334155",
                              border:
                                pk.round === 1
                                  ? "1px solid #f59e0b"
                                  : pk.status === "ACQUIRED"
                                  ? "1px solid #86efac"
                                  : "1px solid #e2e8f0",
                            }}
                          >
                            R{pk.round} #{pk.overall_pick}
                            {pk.status === "ACQUIRED" ? ` (via ${pk.original_team})` : ""}
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default TeamNeedsMatrix;
