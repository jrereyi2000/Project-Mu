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

  // Propose Trade Modal State
  const [proposeOpen, setProposeOpen] = useState<boolean>(false);
  const [teamsLoading, setTeamsLoading] = useState<boolean>(false);
  const [teamsRoster, setTeamsRoster] = useState<any[]>([]);
  const [selectedTargetTeamId, setSelectedTargetTeamId] = useState<number | null>(null);
  const [offeredIds, setOfferedIds] = useState<number[]>([]);
  const [requestedIds, setRequestedIds] = useState<number[]>([]);
  const [rationale, setRationale] = useState<string>("");
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [modalError, setModalError] = useState<string | null>(null);

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

  const openProposeModal = () => {
    setProposeOpen(true);
    setTeamsLoading(true);
    setModalError(null);
    setOfferedIds([]);
    setRequestedIds([]);
    setRationale("");

    fetch(`${API_BASE}/api/napper/league/${leagueId}/teams_rosters`)
      .then((res) => res.json())
      .then((data) => {
        const teams = data.teams || [];
        setTeamsRoster(teams);
        // Default target to first non-user team
        const other = teams.find((t: any) => !t.is_user);
        if (other) setSelectedTargetTeamId(other.id);
        setTeamsLoading(false);
      })
      .catch(() => {
        setTeamsLoading(false);
        setModalError("Failed to load league rosters.");
      });
  };

  const handleProposeSubmit = () => {
    if (!selectedTargetTeamId) {
      setModalError("Please select a target team to trade with.");
      return;
    }
    if (offeredIds.length === 0) {
      setModalError("Please select at least 1 player to offer from your team.");
      return;
    }
    if (requestedIds.length === 0) {
      setModalError("Please select at least 1 player to request from their team.");
      return;
    }

    setSubmitting(true);
    setModalError(null);

    const userTeam = teamsRoster.find((t: any) => t.is_user);
    const proposerId = userTeam ? userTeam.id : 0;

    fetch(`${API_BASE}/api/napper/league/${leagueId}/trades/propose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        proposer_team_id: proposerId,
        target_team_id: selectedTargetTeamId,
        offered_player_ids: offeredIds,
        requested_player_ids: requestedIds,
        ai_rationale: rationale.trim() || undefined,
      }),
    })
      .then((res) => {
        if (!res.ok) {
          return res.json().then((d) => {
            throw new Error(d.detail || "Failed to submit trade proposal");
          });
        }
        return res.json();
      })
      .then(() => {
        setSubmitting(false);
        setProposeOpen(false);
        setActionNotice("Trade proposal sent successfully! The manager will evaluate it based on roster needs.");
        loadTrades();
        setTimeout(() => setActionNotice(null), 5000);
      })
      .catch((err) => {
        setSubmitting(false);
        setModalError(err.message);
      });
  };

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

  const myTeam = teamsRoster.find((t: any) => t.is_user);
  const targetTeam = teamsRoster.find((t: any) => t.id === selectedTargetTeamId);

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
          onClick={openProposeModal}
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
                      <img src={getPlayerHeadshotUrl(p.player_id ?? p.id, p.headshot_url || `/static/players/${p.id}.webp`, p.espn_id)} alt="" onError={handleImageError} style={{ width: "24px", height: "24px", borderRadius: "50%", objectFit: "cover" }} />
                      <span style={{ color: "#e2e8f0", fontSize: "13px", fontWeight: "600" }}>{p.name}</span>
                      <span style={{ color: "var(--napper-teal)", fontSize: "11px" }}>{p.position} · {p.proj_fpts != null ? `${Number(p.proj_fpts).toFixed(1)} proj` : ""}</span>
                    </div>
                  ))}
                </div>
                <div>
                  <div style={{ color: "#8a99ad", fontSize: "11px", fontWeight: "700", textTransform: "uppercase", marginBottom: "6px" }}>You Give Up:</div>
                  {trade.requested_players.map((p: any) => (
                    <div key={p.id} style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "4px" }}>
                      <img src={getPlayerHeadshotUrl(p.player_id ?? p.id, p.headshot_url || `/static/players/${p.id}.webp`, p.espn_id)} alt="" onError={handleImageError} style={{ width: "24px", height: "24px", borderRadius: "50%", objectFit: "cover" }} />
                      <span style={{ color: "#e2e8f0", fontSize: "13px", fontWeight: "600" }}>{p.name}</span>
                      <span style={{ color: "#cbd5e1", fontSize: "11px" }}>{p.position} · {p.proj_fpts != null ? `${Number(p.proj_fpts).toFixed(1)} proj` : ""}</span>
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

      {/* Propose Trade Modal */}
      {proposeOpen && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100, backdropFilter: "blur(4px)" }}>
          <div style={{ background: "#172233", border: "1px solid #283850", borderRadius: "14px", padding: "24px", maxWidth: "680px", width: "95%", maxHeight: "90vh", overflowY: "auto", boxShadow: "0 20px 40px rgba(0,0,0,0.5)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #223147", paddingBottom: "12px", marginBottom: "16px" }}>
              <h3 style={{ color: "#e2e8f0", margin: 0, fontSize: "18px", fontWeight: "800" }}>Propose Trade</h3>
              <button onClick={() => setProposeOpen(false)} style={{ background: "transparent", border: "none", color: "#8a99ad", fontSize: "18px", cursor: "pointer", fontWeight: "bold" }}>✕</button>
            </div>

            {modalError && (
              <div style={{ background: "rgba(255, 74, 110, 0.15)", border: "1px solid #ff4a6e", color: "#ff4a6e", padding: "10px 14px", borderRadius: "8px", marginBottom: "16px", fontSize: "12px" }}>
                {modalError}
              </div>
            )}

            {teamsLoading ? (
              <div style={{ padding: "30px", textAlign: "center", color: "#8a99ad" }}>Loading league rosters...</div>
            ) : (
              <div>
                {/* Select Partner */}
                <div style={{ marginBottom: "16px" }}>
                  <label style={{ display: "block", color: "#8a99ad", fontSize: "11px", fontWeight: "700", textTransform: "uppercase", marginBottom: "6px" }}>Trade With Team</label>
                  <select
                    value={selectedTargetTeamId ?? ""}
                    onChange={(e) => {
                      setSelectedTargetTeamId(Number(e.target.value));
                      setRequestedIds([]);
                    }}
                    style={{ width: "100%", background: "#111827", border: "1px solid #283850", color: "#e2e8f0", padding: "8px 12px", borderRadius: "8px", fontSize: "13px" }}
                  >
                    {teamsRoster.filter((t: any) => !t.is_user).map((t: any) => (
                      <option key={t.id} value={t.id}>
                        {t.team_name} ({t.manager_handle})
                      </option>
                    ))}
                  </select>
                </div>

                {/* Side-by-Side Player Selector */}
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", marginBottom: "16px" }}>
                  {/* Your Players to Offer */}
                  <div style={{ background: "#121926", border: "1px solid #202d40", borderRadius: "10px", padding: "12px" }}>
                    <div style={{ color: "var(--napper-teal)", fontSize: "12px", fontWeight: "700", textTransform: "uppercase", marginBottom: "8px" }}>
                      You Offer ({offeredIds.length})
                    </div>
                    <div style={{ maxHeight: "240px", overflowY: "auto", display: "flex", flexDirection: "column", gap: "6px" }}>
                      {myTeam?.players?.map((p: any) => {
                        const isChecked = offeredIds.includes(p.id);
                        return (
                          <label
                            key={p.id}
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: "8px",
                              padding: "6px 8px",
                              borderRadius: "6px",
                              background: isChecked ? "rgba(0, 206, 184, 0.15)" : "#182334",
                              border: isChecked ? "1px solid var(--napper-teal)" : "1px solid transparent",
                              cursor: "pointer",
                              fontSize: "12px",
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={isChecked}
                              onChange={(e) => {
                                if (e.target.checked) setOfferedIds((prev) => [...prev, p.id]);
                                else setOfferedIds((prev) => prev.filter((id) => id !== p.id));
                              }}
                            />
                            <img src={getPlayerHeadshotUrl(p.player_id ?? p.id, p.headshot_url || `/static/players/${p.id}.webp`, p.espn_id)} alt="" onError={handleImageError} style={{ width: "22px", height: "22px", borderRadius: "50%", objectFit: "cover" }} />
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div style={{ color: "#e2e8f0", fontWeight: "600", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{p.name}</div>
                              <div style={{ color: "#8a99ad", fontSize: "10px" }}>{p.position} · {Number(p.proj_fpts).toFixed(1)} proj</div>
                            </div>
                          </label>
                        );
                      })}
                    </div>
                  </div>

                  {/* Opponent Players to Request */}
                  <div style={{ background: "#121926", border: "1px solid #202d40", borderRadius: "10px", padding: "12px" }}>
                    <div style={{ color: "#38bdf8", fontSize: "12px", fontWeight: "700", textTransform: "uppercase", marginBottom: "8px" }}>
                      You Request ({requestedIds.length})
                    </div>
                    <div style={{ maxHeight: "240px", overflowY: "auto", display: "flex", flexDirection: "column", gap: "6px" }}>
                      {targetTeam?.players?.map((p: any) => {
                        const isChecked = requestedIds.includes(p.id);
                        return (
                          <label
                            key={p.id}
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: "8px",
                              padding: "6px 8px",
                              borderRadius: "6px",
                              background: isChecked ? "rgba(56, 189, 248, 0.15)" : "#182334",
                              border: isChecked ? "1px solid #38bdf8" : "1px solid transparent",
                              cursor: "pointer",
                              fontSize: "12px",
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={isChecked}
                              onChange={(e) => {
                                if (e.target.checked) setRequestedIds((prev) => [...prev, p.id]);
                                else setRequestedIds((prev) => prev.filter((id) => id !== p.id));
                              }}
                            />
                            <img src={getPlayerHeadshotUrl(p.player_id ?? p.id, p.headshot_url || `/static/players/${p.id}.webp`, p.espn_id)} alt="" onError={handleImageError} style={{ width: "22px", height: "22px", borderRadius: "50%", objectFit: "cover" }} />
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div style={{ color: "#e2e8f0", fontWeight: "600", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{p.name}</div>
                              <div style={{ color: "#8a99ad", fontSize: "10px" }}>{p.position} · {Number(p.proj_fpts).toFixed(1)} proj</div>
                            </div>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                </div>

                {/* Optional Trade Rationale */}
                <div style={{ marginBottom: "20px" }}>
                  <label style={{ display: "block", color: "#8a99ad", fontSize: "11px", fontWeight: "700", textTransform: "uppercase", marginBottom: "6px" }}>Trade Message / Note (Optional)</label>
                  <input
                    type="text"
                    placeholder="e.g., Solidifying my RB depth while sending you a WR1..."
                    value={rationale}
                    onChange={(e) => setRationale(e.target.value)}
                    style={{ width: "100%", background: "#111827", border: "1px solid #283850", color: "#e2e8f0", padding: "8px 12px", borderRadius: "8px", fontSize: "13px" }}
                  />
                </div>

                {/* Modal Buttons */}
                <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                  <button onClick={() => setProposeOpen(false)} style={{ background: "transparent", border: "1px solid #283850", color: "#8a99ad", padding: "8px 16px", borderRadius: "6px", cursor: "pointer", fontSize: "13px" }}>
                    Cancel
                  </button>
                  <button
                    onClick={handleProposeSubmit}
                    disabled={submitting}
                    style={{
                      background: "var(--napper-teal)",
                      color: "#0f1520",
                      border: "none",
                      padding: "8px 20px",
                      borderRadius: "6px",
                      fontWeight: "700",
                      fontSize: "13px",
                      cursor: submitting ? "not-allowed" : "pointer",
                      opacity: submitting ? 0.7 : 1,
                    }}
                  >
                    {submitting ? "Sending..." : "Send Proposal"}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
