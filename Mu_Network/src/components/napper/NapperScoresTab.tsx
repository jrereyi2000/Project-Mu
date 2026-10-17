import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { BlueRobotAvatar, PinkRobotAvatar } from "./napperUtils";

interface NapperScoresTabProps {
  leagueId: number;
}

export function NapperScoresTab({ leagueId }: NapperScoresTabProps) {
  const [matchups, setMatchups] = useState<any[]>([]);
  const [week, setWeek] = useState<number>(6);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    setLoading(true);
    // Fetch league info and standings
    fetch(`${API_BASE}/api/napper/league/${leagueId}`)
      .then((res) => res.json())
      .then((data) => {
        const teams = data.teams || [];
        // Group pairs into mock matchups for the week
        const pairs = [];
        for (let i = 0; i < teams.length; i += 2) {
          if (teams[i + 1]) {
            pairs.push({
              home: teams[i],
              away: teams[i + 1],
              home_score: teams[i].id === 1 ? 0.0 : 118.4 + (teams[i].id * 4.2) % 30,
              away_score: teams[i + 1].id === 3 ? 25.4 : 102.1 + (teams[i + 1].id * 3.8) % 25,
              home_proj: teams[i].id === 1 ? 152.14 : 124.5,
              away_proj: teams[i + 1].id === 3 ? 113.95 : 119.0,
            });
          }
        }
        setMatchups(pairs);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [leagueId, week]);

  return (
    <div className="napper-scores-tab" style={{ padding: "16px 24px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
        <div>
          <h2 style={{ color: "#e2e8f0", margin: "0 0 4px 0", fontSize: "18px" }}>League Scoreboard</h2>
          <p style={{ color: "#8a99ad", margin: 0, fontSize: "12px" }}>Weekly head-to-head scores and live projections across the league.</p>
        </div>
        <select
          value={week}
          onChange={(e) => setWeek(Number(e.target.value))}
          style={{
            background: "#1a2332",
            border: "1px solid #283850",
            color: "#e2e8f0",
            padding: "6px 12px",
            borderRadius: "6px",
            fontSize: "13px",
            cursor: "pointer",
          }}
        >
          {Array.from({ length: 14 }, (_, i) => i + 1).map((w) => (
            <option key={w} value={w}>Week {w}</option>
          ))}
        </select>
      </div>

      {loading ? (
        <div style={{ color: "#8a99ad", padding: "20px" }}>Loading scoreboard...</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: "16px" }}>
          {matchups.map((m, idx) => (
            <div
              key={idx}
              style={{
                background: "#1a2332",
                border: "1px solid #283850",
                borderRadius: "10px",
                padding: "16px",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", borderBottom: "1px solid #202d40", paddingBottom: "8px" }}>
                <span style={{ color: "var(--napper-teal)", fontSize: "11px", fontWeight: "700" }}>MATCHUP {idx + 1}</span>
                <span style={{ color: "#8a99ad", fontSize: "11px" }}>WEEK {week} · LIVE</span>
              </div>

              {/* Team 1 */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  {m.home.is_user ? <BlueRobotAvatar size={24} /> : <PinkRobotAvatar size={24} />}
                  <div>
                    <div style={{ color: "#e2e8f0", fontWeight: "600", fontSize: "13px" }}>{m.home.team_name}</div>
                    <div style={{ color: "#8a99ad", fontSize: "11px" }}>{m.home.wins}-{m.home.losses} · Proj: {m.home_proj.toFixed(1)}</div>
                  </div>
                </div>
                <div style={{ fontSize: "18px", fontWeight: "800", color: m.home.id === 1 ? "#8a99ad" : "var(--napper-teal)" }}>
                  {m.home.id === 1 ? "—" : m.home_score.toFixed(1)}
                </div>
              </div>

              {/* Team 2 */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  <PinkRobotAvatar size={24} />
                  <div>
                    <div style={{ color: "#e2e8f0", fontWeight: "600", fontSize: "13px" }}>{m.away.team_name}</div>
                    <div style={{ color: "#8a99ad", fontSize: "11px" }}>{m.away.wins}-{m.away.losses} · Proj: {m.away_proj.toFixed(1)}</div>
                  </div>
                </div>
                <div style={{ fontSize: "18px", fontWeight: "800", color: "#cbd5e1" }}>
                  {m.away_score.toFixed(1)}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
