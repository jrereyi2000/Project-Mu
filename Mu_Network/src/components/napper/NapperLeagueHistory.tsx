import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { NapperLeagueHistory as History, NapperHistorySeason } from "../../types/napper";

// League History (continuity plan phase 3): every season of this league's lineage, from GET /league/{id}/history.
// A finished season's records, playoff finishes and ranks were frozen when its final was decided.

const FINISH_TEXT: Record<string, string> = {
  CHAMPION: "🏆 Champion",
  RUNNER_UP: "Runner-up",
  SEMIFINAL: "Semifinals",
  QUARTERFINAL: "Quarterfinals",
  MISSED: "Missed playoffs",
};

// Short forms for the standings table, which sits in the narrow center column.
const FINISH_SHORT: Record<string, string> = {
  CHAMPION: "🏆 Champ",
  RUNNER_UP: "2nd",
  SEMIFINAL: "Semis",
  QUARTERFINAL: "Quarters",
  MISSED: "Out",
};

function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function SeasonCard({ season, open, onToggle }: { season: NapperHistorySeason; open: boolean; onToggle: () => void }) {
  const finished = season.status === "COMPLETE";
  return (
    <div style={{ background: "#131a27", border: "1px solid #202d40", borderRadius: "8px", marginBottom: "10px" }}>
      <button
        type="button"
        onClick={onToggle}
        style={{ width: "100%", display: "grid", gridTemplateColumns: "64px 1fr auto", gap: "12px", alignItems: "center",
                 padding: "12px 14px", background: "transparent", border: 0, color: "inherit", cursor: "pointer", textAlign: "left" }}
      >
        <span style={{ color: "#e2e8f0", fontWeight: 800, fontSize: "15px" }}>{season.season}</span>
        <span>
          <span style={{ color: "#ffc107", fontWeight: 700, fontSize: "13px" }}>
            {finished ? `🏆 ${season.champion ?? "—"}` : "Season in progress"}
          </span>
          <span style={{ display: "block", color: "#8a99ad", fontSize: "11px", marginTop: "2px" }}>
            {season.user_team ? `${season.user_team}: ${season.user_record ?? "—"}` : "—"}
            {finished && season.user_rank ? ` · finished ${ordinal(season.user_rank)}` : ""}
            {finished && season.user_finish ? ` · ${FINISH_TEXT[season.user_finish] ?? season.user_finish}` : ""}
          </span>
        </span>
        <span style={{ color: "#64748b", fontSize: "11px" }}>{open ? "Hide" : "Standings"} ▾</span>
      </button>
      {open && (
        <div style={{ borderTop: "1px solid #202d40", padding: "6px 14px 12px" }}>
          {season.teams.map((t) => (
            <div key={t.id} style={{ display: "grid", gridTemplateColumns: "20px minmax(0, 1fr) 40px 52px 64px", gap: "6px",
                                     alignItems: "center", padding: "6px 0", borderBottom: "1px solid #1a2332", fontSize: "11px" }}>
              <span style={{ color: "#64748b", fontWeight: 700 }}>{t.final_rank ?? t.seed ?? "—"}</span>
              <span style={{ color: t.is_user ? "var(--napper-teal)" : "#e2e8f0", fontWeight: 600, overflow: "hidden",
                             textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={t.team_name}>
                {t.team_name}{t.is_user ? " (You)" : ""}
              </span>
              <span style={{ color: "#cbd5e1", textAlign: "center" }}>{t.wins}-{t.losses}{t.ties ? `-${t.ties}` : ""}</span>
              <span style={{ color: "#8a99ad", textAlign: "right" }}>{t.points_for.toFixed(1)}</span>
              <span style={{ color: t.playoff_finish === "CHAMPION" ? "#ffc107" : "#8a99ad", textAlign: "right", fontSize: "10px",
                             whiteSpace: "nowrap" }} title={t.playoff_finish ? FINISH_TEXT[t.playoff_finish] : undefined}>
                {t.playoff_finish ? FINISH_SHORT[t.playoff_finish] ?? t.playoff_finish : ""}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function NapperLeagueHistory({ leagueId, onRestarted }: { leagueId: number; onRestarted?: (newLeagueId: number) => void }) {
  const [history, setHistory] = useState<History | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setHistory(null);
    fetch(`${API_BASE}/api/napper/league/${leagueId}/history`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => setHistory(data))
      .catch(() => setHistory(null));
  }, [leagueId]);

  if (!history || history.seasons.length === 0) return null;

  const restart = () => {
    if (!history.restart_league_id || restarting) return;
    setRestarting(true);
    setError(null);
    fetch(`${API_BASE}/api/napper/league/${history.restart_league_id}/restart`, { method: "POST" })
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Restart failed");
        onRestarted?.(data.league.id);
      })
      .catch((e) => setError(e.message))
      .finally(() => setRestarting(false));
  };

  const seasons = [...history.seasons].reverse();
  return (
    <div style={{ marginTop: "24px" }}>
      <h3 style={{ color: "#e2e8f0", fontSize: "16px", fontWeight: 700, marginBottom: "12px", display: "flex", alignItems: "center", gap: "8px" }}>
        <span>📜</span> League History
        <span style={{ background: "#202d40", color: "#8a99ad", padding: "2px 8px", borderRadius: "10px", fontSize: "11px", fontWeight: 700 }}>
          {history.seasons.length} season{history.seasons.length === 1 ? "" : "s"}
        </span>
      </h3>
      {history.restartable && (
        <div style={{ background: "#16303a", border: "1px solid #00ceb8", borderRadius: "8px", padding: "12px 14px", marginBottom: "12px",
                      display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px" }}>
          <span style={{ color: "#cbd5e1", fontSize: "12px" }}>
            This season is over. Start a new one with the same managers; last season's last-place team picks first.
          </span>
          <button type="button" onClick={restart} disabled={restarting}
                  style={{ background: "#00ceb8", color: "#0d1722", border: 0, borderRadius: "6px", padding: "7px 12px",
                           fontWeight: 800, fontSize: "12px", cursor: "pointer", whiteSpace: "nowrap" }}>
            {restarting ? "Starting…" : "Start the new season"}
          </button>
        </div>
      )}
      {error && <div style={{ color: "#f87171", fontSize: "12px", marginBottom: "10px" }}>{error}</div>}
      {seasons.map((s) => (
        <SeasonCard key={s.league_id} season={s} open={open === s.league_id}
                    onToggle={() => setOpen(open === s.league_id ? null : s.league_id)} />
      ))}
    </div>
  );
}
