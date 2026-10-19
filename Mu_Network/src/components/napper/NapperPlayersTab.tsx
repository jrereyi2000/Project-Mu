import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import type { NapperWaiverPlayer } from "../../types/napper";
import { getPlayerHeadshotUrl, handleImageError } from "./napperUtils";

interface NapperPlayersTabProps {
  leagueId: number;
  onSelectPlayer?: (player: NapperWaiverPlayer) => void;
}

export function NapperPlayersTab({ leagueId, onSelectPlayer }: NapperPlayersTabProps) {
  const [position, setPosition] = useState<string>("ALL");
  const [search, setSearch] = useState<string>("");
  const [players, setPlayers] = useState<NapperWaiverPlayer[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [selectedAdd, setSelectedAdd] = useState<NapperWaiverPlayer | null>(null);
  const [claimStatus, setClaimStatus] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    const query = new URLSearchParams();
    if (position !== "ALL") query.set("position", position);
    if (search.trim()) query.set("q", search.trim());

    fetch(`${API_BASE}/api/napper/league/${leagueId}/waivers?${query.toString()}`)
      .then((res) => res.json())
      .then((data) => {
        setPlayers(data.players || []);
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });
  }, [leagueId, position, search]);

  const handleClaim = (player: NapperWaiverPlayer) => {
    setSelectedAdd(player);
  };

  const confirmClaim = () => {
    if (!selectedAdd) return;
    fetch(`${API_BASE}/api/napper/league/${leagueId}/waivers/claim`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        team_id: 0, // 0 triggers user team resolution
        add_player_id: selectedAdd.player_id,
      }),
    })
      .then((res) => {
        if (!res.ok) {
          return res.json().then((d) => {
            throw new Error(d.detail || "Failed to claim player");
          });
        }
        return res.json();
      })
      .then(() => {
        setClaimStatus(`Successfully claimed ${selectedAdd.name}! Check your bench and league chat.`);
        const claimedId = selectedAdd.player_id;
        setSelectedAdd(null);
        setPlayers((prev) => prev.filter((p) => p.player_id !== claimedId));
        setTimeout(() => setClaimStatus(null), 4000);
      })
      .catch((err) => {
        setClaimStatus(`Claim failed: ${err.message}`);
        setTimeout(() => setClaimStatus(null), 4000);
      });
  };

  const POSITIONS = ["ALL", "QB", "RB", "WR", "TE", "K", "DEF"];

  return (
    <div className="napper-players-tab" style={{ padding: "16px 24px" }}>
      {claimStatus && (
        <div style={{ background: "rgba(0, 206, 184, 0.15)", border: "1px solid var(--napper-teal)", color: "var(--napper-teal)", padding: "12px 16px", borderRadius: "8px", marginBottom: "16px" }}>
          {claimStatus}
        </div>
      )}

      {/* Filter and Search Bar */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px", flexWrap: "wrap", gap: "12px" }}>
        <div style={{ display: "flex", gap: "6px" }}>
          {POSITIONS.map((pos) => (
            <button
              key={pos}
              onClick={() => setPosition(pos)}
              style={{
                padding: "6px 14px",
                borderRadius: "20px",
                border: "none",
                background: position === pos ? "var(--napper-teal)" : "#202d40",
                color: position === pos ? "#0f1520" : "#8a99ad",
                fontWeight: position === pos ? "700" : "500",
                cursor: "pointer",
                fontSize: "12px",
              }}
            >
              {pos}
            </button>
          ))}
        </div>

        <input
          type="search"
          placeholder="Search available players..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{
            background: "#1a2332",
            border: "1px solid #283850",
            color: "#e2e8f0",
            padding: "8px 14px",
            borderRadius: "6px",
            fontSize: "13px",
            width: "240px",
          }}
        />
      </div>

      {/* Players List Table */}
      <div style={{ background: "#1a2332", borderRadius: "10px", border: "1px solid #283850", overflow: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr 1fr 100px", padding: "10px 16px", borderBottom: "1px solid #283850", color: "#8a99ad", fontSize: "11px", fontWeight: "700", textTransform: "uppercase" }}>
          <span>Player</span>
          <span>Opponent</span>
          <span style={{ textAlign: "right" }}>Proj Pts</span>
          <span style={{ textAlign: "right" }}>Avg Pts</span>
          <span style={{ textAlign: "center" }}>Action</span>
        </div>

        {loading ? (
          <div style={{ padding: "32px", textAlign: "center", color: "#8a99ad" }}>Loading free agents...</div>
        ) : players.length === 0 ? (
          <div style={{ padding: "32px", textAlign: "center", color: "#8a99ad" }}>No available players match your search.</div>
        ) : (
          players.map((p) => (
            <div
              key={p.player_id}
              style={{
                display: "grid",
                gridTemplateColumns: "2fr 1fr 1fr 1fr 100px",
                padding: "10px 16px",
                borderBottom: "1px solid #202d40",
                alignItems: "center",
                fontSize: "13px",
              }}
            >
              <button
                type="button"
                onClick={() => onSelectPlayer?.(p)}
                aria-label={`View ${p.name} player details`}
                style={{ display: "flex", alignItems: "center", gap: "10px", textAlign: "left", background: "transparent", border: 0, padding: 0, cursor: "pointer", minWidth: 0 }}
              >
                <img
                  src={getPlayerHeadshotUrl(p.player_id, p.headshot_url, p.espn_id)}
                  alt=""
                  onError={handleImageError}
                  style={{ width: "32px", height: "32px", borderRadius: "50%", background: "#202d40", objectFit: "cover" }}
                />
                <div>
                  <div style={{ color: "#e2e8f0", fontWeight: "600" }}>{p.name}</div>
                  <div style={{ color: "#8a99ad", fontSize: "11px" }}>{p.position} · {p.team}</div>
                </div>
              </button>
              <div style={{ color: "#8a99ad", fontSize: "11px" }}>
                <div>{p.kickoff || "Kickoff TBD"}</div>
                <div>{p.opponent || "Bye week / no matchup"}</div>
              </div>
              <div style={{ textAlign: "right", color: "var(--napper-teal)", fontWeight: "700" }}>
                {p.proj_fpts != null ? Number(p.proj_fpts).toFixed(1) : "—"}
              </div>
              <div style={{ textAlign: "right", color: "#cbd5e1" }}>
                {p.avg_fpts != null ? Number(p.avg_fpts).toFixed(1) : "—"}
              </div>
              <div style={{ textAlign: "center" }}>
                <button
                  onClick={() => handleClaim(p)}
                  style={{
                    background: "#00ceb8",
                    color: "#0f1520",
                    border: "none",
                    borderRadius: "6px",
                    padding: "4px 12px",
                    fontWeight: "700",
                    fontSize: "12px",
                    cursor: "pointer",
                  }}
                >
                  + Add
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Claim Confirmation Modal */}
      {selectedAdd && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}>
          <div style={{ background: "#1a2332", border: "1px solid #283850", borderRadius: "12px", padding: "24px", maxWidth: "420px", width: "90%" }}>
            <h3 style={{ color: "#e2e8f0", marginTop: 0 }}>Claim {selectedAdd.name}?</h3>
            <p style={{ color: "#8a99ad", fontSize: "13px" }}>
              Add {selectedAdd.name} ({selectedAdd.position} - {selectedAdd.team}) to your bench. This transaction will be logged in the league activity feed.
            </p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px", marginTop: "20px" }}>
              <button onClick={() => setSelectedAdd(null)} style={{ background: "transparent", border: "1px solid #283850", color: "#8a99ad", padding: "8px 16px", borderRadius: "6px", cursor: "pointer" }}>Cancel</button>
              <button onClick={confirmClaim} style={{ background: "var(--napper-teal)", border: "none", color: "#0f1520", fontWeight: "700", padding: "8px 18px", borderRadius: "6px", cursor: "pointer" }}>Confirm Add</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
