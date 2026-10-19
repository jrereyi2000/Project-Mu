import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import type { NapperWaiverClaim, NapperWaiverPlayer, NapperWaiversResponse } from "../../types/napper";
import { getPlayerHeadshotUrl, handleImageError, InjuryPill } from "./napperUtils";

interface NapperPlayersTabProps {
  leagueId: number;
  onSelectPlayer?: (player: NapperWaiverPlayer) => void;
}

export function NapperPlayersTab({ leagueId, onSelectPlayer }: NapperPlayersTabProps) {
  const [position, setPosition] = useState<string>("ALL");
  const [search, setSearch] = useState<string>("");
  const [players, setPlayers] = useState<NapperWaiverPlayer[]>([]);
  const [context, setContext] = useState<Omit<NapperWaiversResponse, "players">>({});
  const [loading, setLoading] = useState<boolean>(true);
  const [selectedAdd, setSelectedAdd] = useState<NapperWaiverPlayer | null>(null);
  const [dropId, setDropId] = useState<number | "">("");
  const [claimStatus, setClaimStatus] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const roster = context.roster;
  const rosterFull = !!roster && roster.active >= roster.limit;
  const pendingClaims = context.claims?.pending ?? [];
  const lastRun = context.claims?.last_run ?? [];

  const flash = (msg: string) => {
    setClaimStatus(msg);
    setTimeout(() => setClaimStatus(null), 4000);
  };

  useEffect(() => {
    setLoading(true);
    const query = new URLSearchParams();
    if (position !== "ALL") query.set("position", position);
    if (search.trim()) query.set("q", search.trim());

    fetch(`${API_BASE}/api/napper/league/${leagueId}/waivers?${query.toString()}`)
      .then((res) => res.json())
      .then((data: NapperWaiversResponse) => {
        const { players: list, ...rest } = data;
        setPlayers(list || []);
        setContext(rest);
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });
  }, [leagueId, position, search, reloadKey]);

  const handleClaim = (player: NapperWaiverPlayer) => {
    setDropId("");
    setSelectedAdd(player);
  };

  const cancelClaim = (claim: NapperWaiverClaim) => {
    fetch(`${API_BASE}/api/napper/league/${leagueId}/waivers/claims/${claim.claim_id}`, { method: "DELETE" })
      .then((res) => {
        if (!res.ok) throw new Error("Could not cancel the claim");
        flash(`Cancelled your claim on ${claim.add_name}.`);
        setReloadKey((k) => k + 1);
      })
      .catch((err) => flash(err.message));
  };

  const confirmClaim = () => {
    if (!selectedAdd) return;
    fetch(`${API_BASE}/api/napper/league/${leagueId}/waivers/claim`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        team_id: 0, // 0 triggers user team resolution
        add_player_id: selectedAdd.player_id,
        drop_player_id: dropId === "" ? null : dropId,
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
      .then((d) => {
        flash(d.status === "queued"
          ? `${d.message} Waiver priority decides it.`
          : `Added ${selectedAdd.name}! Check your bench and league chat.`);
        setSelectedAdd(null);
        setReloadKey((k) => k + 1);
      })
      .catch((err) => {
        flash(`Claim failed: ${err.message}`);
      });
  };

  const statusColor: Record<string, string> = { WON: "#00ceb8", LOST: "#f87171", INVALID: "#fbbf24", CANCELLED: "#8a99ad" };

  const POSITIONS = ["ALL", "QB", "RB", "WR", "TE", "K", "DEF"];

  return (
    <div className="napper-players-tab" style={{ padding: "16px 24px" }}>
      {claimStatus && (
        <div style={{ background: "rgba(0, 206, 184, 0.15)", border: "1px solid var(--napper-teal)", color: "var(--napper-teal)", padding: "12px 16px", borderRadius: "8px", marginBottom: "16px" }}>
          {claimStatus}
        </div>
      )}

      {/* Roster limit and waiver priority */}
      {roster && (
        <div style={{ display: "flex", gap: "18px", flexWrap: "wrap", fontSize: "12px", color: "#8a99ad", marginBottom: "12px" }}>
          <span>
            Roster <strong style={{ color: rosterFull ? "#fbbf24" : "#e2e8f0" }}>{roster.active}/{roster.limit}</strong> active
            {roster.ir_limit > 0 && <> · IR {roster.ir}/{roster.ir_limit}</>}
          </span>
          {context.waiver_priority && (
            <span>
              Waiver priority <strong style={{ color: "#e2e8f0" }}>{context.waiver_priority.position ?? "—"} of {context.waiver_priority.teams}</strong>
              {" "}· next run week {context.waiver_priority.next_run_week}
            </span>
          )}
        </div>
      )}

      {/* Pending claims and the last run */}
      {(pendingClaims.length > 0 || lastRun.length > 0) && (
        <div style={{ background: "#1a2332", borderRadius: "10px", border: "1px solid #283850", padding: "12px 16px", marginBottom: "16px", fontSize: "12px" }}>
          {pendingClaims.length > 0 && (
            <>
              <div style={{ color: "#8a99ad", fontWeight: 700, textTransform: "uppercase", fontSize: "11px", marginBottom: "6px" }}>Your waiver claims</div>
              {pendingClaims.map((c) => (
                <div key={c.claim_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "4px 0", color: "#e2e8f0" }}>
                  <span>
                    {c.rank}. Add <strong>{c.add_name}</strong>{c.drop_name && <> · drop {c.drop_name}</>}
                    {c.waiver_week != null && <span style={{ color: "#8a99ad" }}> · week {c.waiver_week} run</span>}
                  </span>
                  <button onClick={() => cancelClaim(c)} style={{ background: "transparent", border: "1px solid #283850", color: "#8a99ad", padding: "2px 10px", borderRadius: "6px", cursor: "pointer", fontSize: "11px" }}>Cancel</button>
                </div>
              ))}
            </>
          )}
          {lastRun.length > 0 && (
            <>
              <div style={{ color: "#8a99ad", fontWeight: 700, textTransform: "uppercase", fontSize: "11px", margin: pendingClaims.length ? "10px 0 6px" : "0 0 6px" }}>
                Last waiver run (week {lastRun[0].week})
              </div>
              {lastRun.map((c) => (
                <div key={c.claim_id} style={{ padding: "2px 0", color: "#cbd5e1" }}>
                  <span style={{ color: statusColor[c.status] ?? "#cbd5e1", fontWeight: 700 }}>{c.status}</span>
                  {" "}{c.team_name}: {c.add_name}{c.drop_name && <> (dropped {c.drop_name})</>}
                  {c.note && <span style={{ color: "#8a99ad" }}> · {c.note}</span>}
                </div>
              ))}
            </>
          )}
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
                  <div style={{ color: "#8a99ad", fontSize: "11px", display: "flex", gap: "6px", alignItems: "center" }}>
                    {p.position} · {p.team} <InjuryPill player={p} />
                    {p.on_waivers && (
                      <span title={`Dropped since the last waiver run: claim only, decided in the week ${p.waiver_week} run`}
                        style={{ background: "rgba(251, 191, 36, 0.15)", color: "#fbbf24", borderRadius: "4px", padding: "0 5px", fontSize: "10px", fontWeight: 700 }}>
                        WAIVERS · WK {p.waiver_week}
                      </span>
                    )}
                  </div>
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
                    background: p.on_waivers ? "#fbbf24" : "#00ceb8",
                    color: "#0f1520",
                    border: "none",
                    borderRadius: "6px",
                    padding: "4px 12px",
                    fontWeight: "700",
                    fontSize: "12px",
                    cursor: "pointer",
                  }}
                >
                  {p.on_waivers ? "Claim" : "+ Add"}
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
            <h3 style={{ color: "#e2e8f0", marginTop: 0 }}>{selectedAdd.on_waivers ? "Claim" : "Add"} {selectedAdd.name}?</h3>
            <p style={{ color: "#8a99ad", fontSize: "13px" }}>
              {selectedAdd.on_waivers
                ? <>{selectedAdd.name} ({selectedAdd.position} - {selectedAdd.team}) is on waivers. Your claim is decided in the week {selectedAdd.waiver_week} waiver run, in waiver priority order (worst record first).</>
                : <>Add {selectedAdd.name} ({selectedAdd.position} - {selectedAdd.team}) to your bench now. This transaction will be logged in the league activity feed.</>}
            </p>
            {context.my_players && (
              <label style={{ display: "block", color: "#8a99ad", fontSize: "12px", marginTop: "12px" }}>
                {rosterFull ? `Your roster is full (${roster?.active}/${roster?.limit}). Choose a player to drop:` : "Drop a player (optional):"}
                <select
                  value={dropId}
                  onChange={(e) => setDropId(e.target.value === "" ? "" : Number(e.target.value))}
                  style={{ display: "block", width: "100%", marginTop: "6px", background: "#0f1520", color: "#e2e8f0", border: "1px solid #283850", borderRadius: "6px", padding: "6px 8px" }}
                >
                  <option value="">{rosterFull ? "Select a player…" : "No drop"}</option>
                  {context.my_players.map((mp) => (
                    <option key={mp.player_id} value={mp.player_id}>
                      {mp.name} · {mp.position ?? "?"} · {mp.slot}{mp.ros_rank ? ` · ROS ${mp.ros_rank}` : ""}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px", marginTop: "20px" }}>
              <button onClick={() => setSelectedAdd(null)} style={{ background: "transparent", border: "1px solid #283850", color: "#8a99ad", padding: "8px 16px", borderRadius: "6px", cursor: "pointer" }}>Cancel</button>
              <button
                onClick={confirmClaim}
                disabled={rosterFull && dropId === ""}
                style={{ background: "var(--napper-teal)", border: "none", color: "#0f1520", fontWeight: "700", padding: "8px 18px", borderRadius: "6px", cursor: rosterFull && dropId === "" ? "not-allowed" : "pointer", opacity: rosterFull && dropId === "" ? 0.5 : 1 }}
              >
                {selectedAdd.on_waivers ? "Place Claim" : "Confirm Add"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
