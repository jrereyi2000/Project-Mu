import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { getPlayerHeadshotUrl, handleImageError } from "./napperUtils";

interface NapperTradesTabProps {
  leagueId: number;
}

export function NapperTradesTab({ leagueId }: NapperTradesTabProps) {
  const [trades, setTrades] = useState<any[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [actionNotice, setActionNotice] = useState<string | null>(null);

  const loadTrades = () => {
    setLoading(true);
    fetch(`${API_BASE}/api/napper/league/${leagueId}/trades`)
      .then((res) => res.json())
      .then((data) => {
        setTrades(data.trades || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    loadTrades();
  }, [leagueId]);

  const respondTrade = (tradeId: number, action: "ACCEPT" | "REJECT") => {
    fetch(`${API_BASE}/api/napper/league/${leagueId}/trades/${tradeId}/respond`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    })
      .then((res) => res.json())
      .then(() => {
        setActionNotice(`Trade ${action.toLowerCase()}ed! Rosters and league feed have been updated.`);
        loadTrades();
        setTimeout(() => setActionNotice(null), 4000);
      });
  };

  const pendingTrades = trades.filter((t) => t.status === "PENDING");
  const completedTrades = trades.filter((t) => t.status !== "PENDING");

  return (
    <div className="napper-trades-tab" style={{ padding: "16px 24px" }}>
      {actionNotice && (
        <div style={{ background: "rgba(0, 206, 184, 0.15)", border: "1px solid var(--napper-teal)", color: "var(--napper-teal)", padding: "12px 16px", borderRadius: "8px", marginBottom: "16px" }}>
          {actionNotice}
        </div>
      )}

      {/* Header Bar */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
        <div>
          <h2 style={{ color: "#e2e8f0", margin: "0 0 4px 0", fontSize: "18px" }}>Trade Market</h2>
          <p style={{ color: "#8a99ad", margin: 0, fontSize: "12px" }}>Review incoming AI trade offers or initiate reciprocal trade proposals.</p>
        </div>
        <button
          onClick={() => alert("To propose a trade, select any team in the LEAGUE tab or message them directly in chat!")}
          style={{
            background: "var(--napper-teal)",
            color: "#0f1520",
            border: "none",
            borderRadius: "6px",
            padding: "8px 16px",
            fontWeight: "700",
            fontSize: "13px",
            cursor: "pointer",
          }}
        >
          + Propose New Trade
        </button>
      </div>

      {/* Pending Proposals Section */}
      <div style={{ marginBottom: "28px" }}>
        <h3 style={{ color: "#8a99ad", fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "12px" }}>
          Pending Proposals ({pendingTrades.length})
        </h3>

        {loading ? (
          <div style={{ color: "#8a99ad", padding: "20px" }}>Loading trade offers...</div>
        ) : pendingTrades.length === 0 ? (
          <div style={{ background: "#1a2332", border: "1px solid #283850", borderRadius: "10px", padding: "24px", textAlign: "center", color: "#8a99ad" }}>
            No pending trade offers right now. AI managers will propose trades based on roster surplus and weekly performance!
          </div>
        ) : (
          pendingTrades.map((trade) => (
            <div
              key={trade.id}
              style={{
                background: "#1a2332",
                border: "1px solid #283850",
                borderRadius: "10px",
                padding: "16px 20px",
                marginBottom: "14px",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span style={{ background: "rgba(0, 206, 184, 0.2)", color: "var(--napper-teal)", padding: "2px 8px", borderRadius: "4px", fontSize: "11px", fontWeight: "700" }}>
                    {trade.initiated_by_ai ? "AI INITIATED" : "PROPOSED"}
                  </span>
                  <span style={{ color: "#e2e8f0", fontWeight: "600", fontSize: "14px" }}>
                    {trade.proposer.team_name} ({trade.proposer.manager_handle}) → {trade.target.team_name}
                  </span>
                </div>
                <div style={{ display: "flex", gap: "8px" }}>
                  <button
                    onClick={() => respondTrade(trade.id, "ACCEPT")}
                    style={{ background: "var(--napper-teal)", color: "#0f1520", border: "none", padding: "6px 14px", borderRadius: "6px", fontWeight: "700", fontSize: "12px", cursor: "pointer" }}
                  >
                    Accept Trade
                  </button>
                  <button
                    onClick={() => respondTrade(trade.id, "REJECT")}
                    style={{ background: "#202d40", color: "#ff4a6e", border: "1px solid #ff4a6e", padding: "6px 14px", borderRadius: "6px", fontWeight: "700", fontSize: "12px", cursor: "pointer" }}
                  >
                    Decline
                  </button>
                </div>
              </div>

              {/* Trade Details Breakdown */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", background: "#131a27", padding: "12px 16px", borderRadius: "8px", border: "1px solid #202d40" }}>
                <div>
                  <div style={{ color: "#8a99ad", fontSize: "11px", fontWeight: "700", textTransform: "uppercase", marginBottom: "6px" }}>You Receive:</div>
                  {trade.offered_players.map((p: any) => (
                    <div key={p.id} style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "4px" }}>
                      <img src={getPlayerHeadshotUrl(p.id)} alt="" onError={handleImageError} style={{ width: "24px", height: "24px", borderRadius: "50%", objectFit: "cover" }} />
                      <span style={{ color: "#e2e8f0", fontSize: "13px", fontWeight: "600" }}>{p.name}</span>
                      <span style={{ color: "var(--napper-teal)", fontSize: "11px" }}>{p.position} · {p.proj_fpts ? `${p.proj_fpts} proj` : ""}</span>
                    </div>
                  ))}
                </div>
                <div>
                  <div style={{ color: "#8a99ad", fontSize: "11px", fontWeight: "700", textTransform: "uppercase", marginBottom: "6px" }}>You Give Up:</div>
                  {trade.requested_players.map((p: any) => (
                    <div key={p.id} style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "4px" }}>
                      <img src={getPlayerHeadshotUrl(p.id)} alt="" onError={handleImageError} style={{ width: "24px", height: "24px", borderRadius: "50%", objectFit: "cover" }} />
                      <span style={{ color: "#e2e8f0", fontSize: "13px", fontWeight: "600" }}>{p.name}</span>
                      <span style={{ color: "#cbd5e1", fontSize: "11px" }}>{p.position} · {p.proj_fpts ? `${p.proj_fpts} proj` : ""}</span>
                    </div>
                  ))}
                </div>
              </div>

              {trade.ai_rationale && (
                <div style={{ marginTop: "10px", fontSize: "12px", color: "#8a99ad", fontStyle: "italic" }}>
                  💡 Manager Rationale: "{trade.ai_rationale}"
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* Trade History */}
      <div>
        <h3 style={{ color: "#8a99ad", fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "12px" }}>
          Recent League Activity
        </h3>
        <div style={{ background: "#1a2332", border: "1px solid #283850", borderRadius: "10px", overflow: "hidden" }}>
          {completedTrades.length === 0 ? (
            <div style={{ padding: "20px", color: "#8a99ad", textAlign: "center" }}>No completed trades yet this season.</div>
          ) : (
            completedTrades.map((t) => (
              <div key={t.id} style={{ padding: "12px 16px", borderBottom: "1px solid #202d40", display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "13px" }}>
                <span style={{ color: "#cbd5e1" }}>
                  <b>{t.proposer.team_name}</b> and <b>{t.target.team_name}</b>
                </span>
                <span style={{ color: t.status === "ACCEPTED" ? "var(--napper-teal)" : "#8a99ad", fontWeight: "700" }}>
                  {t.status}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
