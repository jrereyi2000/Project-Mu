import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import { BlueRobotAvatar, PinkRobotAvatar } from "./napperUtils";
import { ARCHETYPE_METAS, NapperManagerArchetype, NapperPlayoffGame, NapperPlayoffs, NapperPlayoffSide } from "../../types/napper";
import ArchetypeCompositionModal from "./draft/ArchetypeCompositionModal";
import { NapperScoresTab } from "./NapperScoresTab";
import { NapperLeagueHistory } from "./NapperLeagueHistory";

interface NapperLeagueTabProps {
  leagueId: number;
  onRestarted?: (newLeagueId: number) => void;
}

export function NapperLeagueTab({ leagueId, onRestarted }: NapperLeagueTabProps) {
  const [league, setLeague] = useState<any>(null);
  const [teams, setTeams] = useState<any[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [strategyModalOpen, setStrategyModalOpen] = useState<boolean>(false);
  const [playoffs, setPlayoffs] = useState<NapperPlayoffs | null>(null);

  const fetchLeagueData = () => {
    fetch(`${API_BASE}/api/napper/league/${leagueId}`)
      .then((res) => res.json())
      .then((data) => {
        setLeague(data.league);
        setTeams(data.teams || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
    fetch(`${API_BASE}/api/napper/league/${leagueId}/playoffs`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => setPlayoffs(data))
      .catch(() => setPlayoffs(null));
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

      {/* Playoff Bracket */}
      {playoffs && playoffs.status !== "NONE" && <PlayoffBracket playoffs={playoffs} />}
      <NapperLeagueHistory leagueId={leagueId} onRestarted={onRestarted} />

    </div>
  );
}

const STATUS_TEXT: Record<string, string> = {
  PROJECTED: "Projected · if the season ended today",
  IN_PROGRESS: "In progress",
  COMPLETE: "Final",
};

function BracketSide({ side, winnerId, decided }: { side: NapperPlayoffSide; winnerId: number | null; decided: boolean }) {
  const won = decided && side.team_id === winnerId;
  const lost = decided && side.team_id !== winnerId;
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", padding: "4px 0", opacity: lost ? 0.5 : 1 }}>
      <span style={{ color: won ? "var(--napper-teal)" : "#e2e8f0", fontSize: "12px", fontWeight: won ? 800 : 600, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        <span style={{ color: "#8a99ad", marginRight: "6px" }}>#{side.seed}</span>
        {side.team_name ?? "TBD"}
        {side.is_user ? " · You" : ""}
      </span>
      <span style={{ color: won ? "var(--napper-teal)" : "#cbd5e1", fontSize: "12px", fontWeight: 700, fontFamily: "var(--font-mono, monospace)" }}>
        {side.score != null ? side.score.toFixed(2) : side.proj ? `Proj ${side.proj.toFixed(1)}` : ""}
      </span>
    </div>
  );
}

function BracketGame({ game }: { game: NapperPlayoffGame }) {
  const decided = game.winner_team_id != null;
  return (
    <div style={{ background: "#1a2332", padding: "8px 10px", borderRadius: "6px", marginBottom: "8px" }}>
      <BracketSide side={game.home} winnerId={game.winner_team_id} decided={decided} />
      <BracketSide side={game.away} winnerId={game.winner_team_id} decided={decided} />
    </div>
  );
}

function Placeholder({ text }: { text: string }) {
  return (
    <div style={{ background: "#1a2332", padding: "8px 10px", borderRadius: "6px", marginBottom: "8px", color: "#8a99ad", fontSize: "12px" }}>{text}</div>
  );
}

function PlayoffBracket({ playoffs }: { playoffs: NapperPlayoffs }) {
  const [qf, sf, final] = playoffs.rounds;
  const bye = (i: number) => playoffs.byes[i];
  const roundTitle = (r: { label: string; week: number }, gold = false) => (
    <div style={{ color: gold ? "#ffc107" : "var(--napper-teal)", fontSize: "11px", fontWeight: 700, textTransform: "uppercase", marginBottom: "8px" }}>
      {r.label} · Wk {r.week}
    </div>
  );
  return (
    <div style={{ marginBottom: "32px" }}>
      <h3 style={{ color: "#e2e8f0", fontSize: "16px", marginBottom: "12px", display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
        <span>⚔️</span> Playoff Bracket (Weeks 15–17)
        <span style={{ background: "#202d40", color: "#8a99ad", padding: "2px 8px", borderRadius: "10px", fontSize: "11px", fontWeight: 700 }}>
          {STATUS_TEXT[playoffs.status]}
        </span>
      </h3>
      <div style={{ background: "#1a2332", border: "1px solid #283850", borderRadius: "10px", padding: "20px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "16px" }}>
          <div style={{ background: "#131a27", border: "1px solid #202d40", borderRadius: "8px", padding: "12px" }}>
            {roundTitle(qf)}
            {qf.games.map((g, i) => <BracketGame key={i} game={g} />)}
            <div style={{ color: "#64748b", fontSize: "11px" }}>
              Byes: {playoffs.byes.map((b) => `#${b.seed} ${b.team_name}${b.is_user ? " (You)" : ""}`).join(", ")}
            </div>
          </div>
          <div style={{ background: "#131a27", border: "1px solid #202d40", borderRadius: "8px", padding: "12px" }}>
            {roundTitle(sf)}
            {sf.games.length
              ? sf.games.map((g, i) => <BracketGame key={i} game={g} />)
              : [0, 1].map((i) => (
                  <Placeholder key={i} text={`#${bye(i)?.seed} ${bye(i)?.team_name ?? ""} vs ${i === 0 ? "lowest" : "highest"} remaining seed`} />
                ))}
          </div>
          <div style={{ background: "#131a27", border: "1px solid #202d40", borderRadius: "8px", padding: "12px" }}>
            {roundTitle(final, true)}
            {final.games.length ? final.games.map((g, i) => <BracketGame key={i} game={g} />) : <Placeholder text="Semifinal winners" />}
            <div style={{ background: "#1a2332", padding: "12px", borderRadius: "6px", textAlign: "center" }}>
              <div style={{ color: "#ffc107", fontSize: "13px", fontWeight: 700 }}>
                🏆 {playoffs.champion ? `${playoffs.champion.team_name}${playoffs.champion.is_user ? " (You)" : ""}` : "Napper Champion"}
              </div>
              <div style={{ color: "#8a99ad", fontSize: "11px", marginTop: "4px" }}>
                {playoffs.champion ? `#${playoffs.champion.seed} seed · league champion` : "Decided in Week 17"}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
