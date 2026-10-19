import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { BlueRobotAvatar, PinkRobotAvatar } from "./napperUtils";
import { ARCHETYPE_METAS, NapperManagerArchetype } from "../../types/napper";
import ArchetypeCompositionModal from "./draft/ArchetypeCompositionModal";
import { NapperScoresTab } from "./NapperScoresTab";

interface NapperLeagueTabProps {
  leagueId: number;
}

export function NapperLeagueTab({ leagueId }: NapperLeagueTabProps) {
  const [league, setLeague] = useState<any>(null);
  const [teams, setTeams] = useState<any[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [strategyModalOpen, setStrategyModalOpen] = useState<boolean>(false);

  const fetchLeagueData = () => {
    fetch(`${API_BASE}/api/napper/league/${leagueId}`)
      .then((res) => res.json())
      .then((data) => {
        setLeague(data.league);
        setTeams(data.teams || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    fetchLeagueData();
  }, [leagueId]);

  if (loading) {
    return <div style={{ padding: "32px", color: "#8a99ad", textAlign: "center" }}>Loading standings & bracket...</div>;
  }

  const aiTeams = teams.filter((t) => t.is_ai);
  const aiSlots = aiTeams.length;
  const currentComposition: Record<string, number> = {};
  const canEditStrategies = league?.status === "PRE_DRAFT";
  aiTeams.forEach((t) => {
    const p = t.persona || "ANALYTICS_NERD";
    currentComposition[p] = (currentComposition[p] || 0) + 1;
  });

  const handleApplyStrategies = (newComposition: Record<string, number>, presetName?: string) => {
    fetch(`${API_BASE}/api/napper/league/${leagueId}/set_strategies`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        archetype_composition: newComposition,
        archetype_preset: presetName,
      }),
    })
      .then((res) => res.json())
      .then(() => {
        fetchLeagueData();
      })
      .catch(() => {});
  };

  return (
    <div className="napper-league-tab" style={{ padding: "16px 24px" }}>
      <NapperScoresTab leagueId={leagueId} embedded />

      {/* Standings Table */}
      <div style={{ marginBottom: "32px" }}>
        <h3 style={{ color: "#e2e8f0", fontSize: "16px", marginBottom: "12px", display: "flex", alignItems: "center", gap: "8px" }}>
          <span>🏆</span> League Standings
        </h3>
        <div style={{ background: "#1a2332", border: "1px solid #283850", borderRadius: "10px", overflow: "hidden" }}>
          <div style={{ display: "grid", gridTemplateColumns: "40px 2fr 1fr 1fr 1fr 80px", padding: "10px 16px", borderBottom: "1px solid #283850", color: "#8a99ad", fontSize: "11px", fontWeight: "700", textTransform: "uppercase" }}>
            <span>Rank</span>
            <span>Team & Manager</span>
            <span style={{ textAlign: "center" }}>W-L-T</span>
            <span style={{ textAlign: "right" }}>PF</span>
            <span style={{ textAlign: "right" }}>PA</span>
            <span style={{ textAlign: "center" }}>Seed</span>
          </div>

          {teams.map((t, idx) => (
            <div
              key={t.id}
              style={{
                display: "grid",
                gridTemplateColumns: "40px 2fr 1fr 1fr 1fr 80px",
                padding: "12px 16px",
                borderBottom: "1px solid #202d40",
                alignItems: "center",
                fontSize: "13px",
                background: t.is_user ? "rgba(0, 206, 184, 0.05)" : "transparent",
              }}
            >
              <span style={{ color: idx < 6 ? "var(--napper-teal)" : "#8a99ad", fontWeight: "700" }}>{t.seed ?? idx + 1}</span>
              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                {t.is_user ? <BlueRobotAvatar size={28} /> : <PinkRobotAvatar size={28} />}
                <div>
                  <div style={{ color: "#e2e8f0", fontWeight: "600" }}>
                    {t.team_name} {t.is_user && <span style={{ color: "var(--napper-teal)", fontSize: "11px" }}>(You)</span>}
                  </div>
                  <div style={{ color: "#8a99ad", fontSize: "11px", display: "flex", alignItems: "center", gap: "6px" }}>
                    <span>{t.manager_handle}</span>
                    {t.is_ai && (
                      <span
                        style={{
                          fontSize: "10px",
                          fontWeight: "700",
                          color: ARCHETYPE_METAS[t.persona as NapperManagerArchetype]?.color || "#8a99ad",
                          background: ARCHETYPE_METAS[t.persona as NapperManagerArchetype]?.bgColor || "rgba(255,255,255,0.06)",
                          border: `1px solid ${ARCHETYPE_METAS[t.persona as NapperManagerArchetype]?.borderColor || "transparent"}`,
                          padding: "1px 6px",
                          borderRadius: "4px",
                        }}
                      >
                        {ARCHETYPE_METAS[t.persona as NapperManagerArchetype]?.shortLabel || t.persona || "AI"}
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <div style={{ textAlign: "center", color: "#cbd5e1", fontWeight: "700" }}>{t.wins}-{t.losses}-{t.ties}</div>
              <div style={{ textAlign: "right", color: "var(--napper-teal)", fontWeight: "600" }}>{parseFloat(t.points_for).toFixed(1)}</div>
              <div style={{ textAlign: "right", color: "#8a99ad" }}>{parseFloat(t.points_against).toFixed(1)}</div>
              <div style={{ textAlign: "center" }}>
                {idx < 6 ? (
                  <span style={{ background: "#202d40", color: "var(--napper-teal)", padding: "2px 8px", borderRadius: "10px", fontSize: "11px", fontWeight: "700" }}>
                    #{t.seed ?? idx + 1}
                  </span>
                ) : (
                  <span style={{ color: "#64748b", fontSize: "11px" }}>—</span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Playoff Bracket Preview */}
      <div style={{ marginBottom: "32px" }}>
        <h3 style={{ color: "#e2e8f0", fontSize: "16px", marginBottom: "12px", display: "flex", alignItems: "center", gap: "8px" }}>
          <span>⚔️</span> Playoff Bracket (Weeks 15–17)
        </h3>
        <div style={{ background: "#1a2332", border: "1px solid #283850", borderRadius: "10px", padding: "20px" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "16px" }}>
            {/* Round 1: Quarterfinals */}
            <div style={{ background: "#131a27", border: "1px solid #202d40", borderRadius: "8px", padding: "12px" }}>
              <div style={{ color: "var(--napper-teal)", fontSize: "11px", fontWeight: "700", textTransform: "uppercase", marginBottom: "8px" }}>Round 1 · Wk 15</div>
              <div style={{ background: "#1a2332", padding: "8px", borderRadius: "6px", marginBottom: "8px" }}>
                <div style={{ color: "#e2e8f0", fontSize: "12px" }}>#3 Seed vs #6 Seed</div>
              </div>
              <div style={{ background: "#1a2332", padding: "8px", borderRadius: "6px" }}>
                <div style={{ color: "#e2e8f0", fontSize: "12px" }}>#4 Seed vs #5 Seed</div>
              </div>
            </div>

            {/* Round 2: Semifinals */}
            <div style={{ background: "#131a27", border: "1px solid #202d40", borderRadius: "8px", padding: "12px" }}>
              <div style={{ color: "var(--napper-teal)", fontSize: "11px", fontWeight: "700", textTransform: "uppercase", marginBottom: "8px" }}>Semifinals · Wk 16</div>
              <div style={{ background: "#1a2332", padding: "8px", borderRadius: "6px", marginBottom: "8px" }}>
                <div style={{ color: "#e2e8f0", fontSize: "12px" }}>#1 Seed (Bye) vs TBD</div>
              </div>
              <div style={{ background: "#1a2332", padding: "8px", borderRadius: "6px" }}>
                <div style={{ color: "#e2e8f0", fontSize: "12px" }}>#2 Seed (Bye) vs TBD</div>
              </div>
            </div>

            {/* Round 3: Championship */}
            <div style={{ background: "#131a27", border: "1px solid #202d40", borderRadius: "8px", padding: "12px" }}>
              <div style={{ color: "#ffc107", fontSize: "11px", fontWeight: "700", textTransform: "uppercase", marginBottom: "8px" }}>Championship · Wk 17</div>
              <div style={{ background: "#1a2332", padding: "12px", borderRadius: "6px", textAlign: "center" }}>
                <div style={{ color: "#ffc107", fontSize: "13px", fontWeight: "700" }}>🏆 Napper Champion</div>
                <div style={{ color: "#8a99ad", fontSize: "11px", marginTop: "4px" }}>Winner Semifinal 1 vs 2</div>
              </div>
            </div>
          </div>
        </div>
      </div>


    </div>
  );
}
