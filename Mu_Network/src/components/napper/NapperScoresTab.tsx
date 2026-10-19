import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import NapperMatchupTab from "./NapperMatchupTab";

interface ScoreTeam {
  id: number;
  team_name: string;
  manager_handle: string;
  wins: number;
  losses: number;
  ties: number;
  seed: number;
  is_user: boolean;
}

interface ScoreMatchup {
  id: number;
  home: ScoreTeam;
  away: ScoreTeam;
  home_score: number;
  away_score: number;
  home_proj: number;
  away_proj: number;
  has_started: boolean;
  is_final: boolean;
}

interface NapperScoresTabProps {
  leagueId: number;
  embedded?: boolean;
}

export function NapperScoresTab({ leagueId, embedded = false }: NapperScoresTabProps) {
  const [matchups, setMatchups] = useState<ScoreMatchup[]>([]);
  const [week, setWeek] = useState<number | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedMatchup, setSelectedMatchup] = useState<ScoreMatchup | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const query = week == null ? "" : `?week=${week}`;
    fetch(`${API_BASE}/api/napper/league/${leagueId}/scores${query}`)
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Could not load league scores.");
        return data;
      })
      .then((data) => {
        if (cancelled) return;
        setMatchups(data.matchups || []);
        if (data.week) setWeek(data.week);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || "Could not load league scores.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [leagueId, week]);

  useEffect(() => {
    if (!selectedMatchup) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelectedMatchup(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [selectedMatchup]);

  return (
    <div className="napper-scores-tab" style={{ padding: embedded ? "0 0 24px" : "16px 24px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
        <div>
          <h2 style={{ color: "#e2e8f0", margin: "0 0 4px 0", fontSize: "18px" }}>Matchups</h2>
          <p style={{ color: "#8a99ad", margin: 0, fontSize: "12px" }}>Weekly starter points, projections, and team records.</p>
        </div>
        <select
          value={week ?? 1}
          onChange={(e) => setWeek(Number(e.target.value))}
          aria-label="Scoreboard week"
            style={{ background: "#273247", border: "1px solid #283850", color: "#e2e8f0", padding: "9px 14px", borderRadius: "10px", fontSize: "14px", fontWeight: 700, cursor: "pointer" }}
        >
          {Array.from({ length: 17 }, (_, i) => i + 1).map((w) => (
            <option key={w} value={w}>Wk. {w}</option>
          ))}
        </select>
      </div>

      {loading ? (
        <div style={{ color: "#8a99ad", padding: "20px" }}>Loading scoreboard…</div>
      ) : error ? (
        <div role="alert" style={{ color: "#ff8798", padding: "20px" }}>{error}</div>
      ) : matchups.length === 0 ? (
        <div style={{ color: "#8a99ad", padding: "20px" }}>No matchups are scheduled for Week {week} in this league.</div>
      ) : (
        <div style={{ display: "grid", gap: "16px" }}>
          {matchups.map((matchup) => {
            const pointsSum = matchup.has_started
              ? matchup.home_score + matchup.away_score
              : matchup.home_proj + matchup.away_proj;
            const homePct = pointsSum > 0
              ? Math.round(((matchup.has_started ? matchup.home_score : matchup.home_proj) / pointsSum) * 100)
              : 50;
            const awayPct = 100 - homePct;
            const renderSide = (side: "home" | "away") => {
              const team = matchup[side];
              const score = matchup[`${side}_score`];
              const projection = matchup[`${side}_proj`];
              const percent = side === "home" ? homePct : awayPct;
              const isHome = side === "home";
              return (
                <div key={team.id} style={{ minWidth: 0, flex: 1, textAlign: isHome ? "left" : "right" }}>
                  <div style={{ color: "#8a99ad", fontSize: "11px", lineHeight: "16px", fontWeight: 600, letterSpacing: ".02em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", paddingRight: isHome ? 0 : 36 }}>
                    {team.manager_handle ? `@${team.manager_handle}` : ""}
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", flexDirection: isHome ? "row" : "row-reverse", marginTop: "2px" }}>
                    <div style={{ color: "#fff", fontSize: "16px", lineHeight: 1.2, fontWeight: 800, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{team.team_name}{team.is_user ? " · You" : ""}</div>
                    <div style={{ color: "#fff", fontSize: "20px", lineHeight: 1, fontWeight: 900, letterSpacing: "-.02em", whiteSpace: "nowrap" }}>{score > 0 ? score.toFixed(2) : "–"}</div>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", color: "#8a99ad", fontSize: "11px", marginTop: "3px", fontWeight: 600 }}>
                    <span style={{ fontFamily: "var(--font-mono, monospace)" }}>{isHome ? `Proj ${projection.toFixed(2)}` : `${team.wins}-${team.losses}${team.ties ? `-${team.ties}` : ""}`}</span>
                    <span style={{ color: "#00ceb8", fontWeight: 900, fontSize: "12px" }}>{isHome ? `${percent}%` : ""}</span>
                    <span style={{ color: "#00ceb8", fontWeight: 900, fontSize: "12px" }}>{isHome ? "" : `${percent}%`}</span>
                    <span style={{ fontFamily: "var(--font-mono, monospace)" }}>{isHome ? `${team.wins}-${team.losses}${team.ties ? `-${team.ties}` : ""}` : `Proj ${projection.toFixed(2)}`}</span>
                  </div>
                </div>
              );
            };
            return (
              <div
                key={matchup.id}
                className="napper-matchup-banner napper-league-matchup-card cursor-pointer transition-transform hover:-translate-y-0.5 hover:border-[#00ceb8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00ceb8]"
                style={{ position: "relative" }}
                role="button"
                tabIndex={0}
                aria-label={`Open ${matchup.home.team_name} versus ${matchup.away.team_name} matchup details`}
                onClick={() => setSelectedMatchup(matchup)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelectedMatchup(matchup);
                  }
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
                  {renderSide("home")}
                  <div aria-hidden="true" style={{ zIndex: 1, flex: "0 0 36px", height: 36, borderRadius: "50%", background: "#202b3f", color: "#5c6c80", display: "grid", placeItems: "center", fontSize: "11px", fontWeight: 900 }}>VS</div>
                  {renderSide("away")}
                </div>
                <div className="napper-winprob-track napper-league-matchup-gauge" aria-label={`${homePct}% to ${awayPct}% points share`}>
                  <div className="napper-winprob-fill-left" style={{ width: `${homePct}%` }} />
                  <div className="napper-winprob-fill-right" style={{ width: `${awayPct}%` }} />
                </div>
                <span style={{ position: "absolute", top: 10, right: 18, color: "#8392a8", fontSize: "10px", fontWeight: 800 }}>{matchup.is_final ? "FINAL" : "LIVE"}</span>
              </div>
            );
          })}
        </div>
      )}

      {selectedMatchup && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-[#080d16]/80 p-3 backdrop-blur-sm sm:p-6"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget) setSelectedMatchup(null); }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="napper-matchup-modal-title"
            className="flex max-h-[94vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-[#283850] bg-[#121926] shadow-2xl"
          >
            <header className="flex shrink-0 items-center justify-between border-b border-[#283850] bg-[#1a2332] px-5 py-3">
              <div className="min-w-0">
                <h2 id="napper-matchup-modal-title" className="truncate text-sm font-extrabold text-white">
                  {selectedMatchup.home.team_name} <span className="text-[#5c6c80]">vs</span> {selectedMatchup.away.team_name}
                </h2>
                <p className="mt-0.5 text-[11px] font-semibold uppercase tracking-wider text-[#8a99ad]">Week {week}</p>
              </div>
              <button
                type="button"
                aria-label="Close matchup details"
                onClick={() => setSelectedMatchup(null)}
                className="ml-3 grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-[#283850] text-[#8a99ad] hover:border-[#00ceb8] hover:text-white"
              >
                <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true">
                  <path d="m6 6 12 12M18 6 6 18" />
                </svg>
              </button>
            </header>
            <div className="min-h-0 overflow-y-auto">
              <NapperMatchupTab
                key={`${leagueId}-${week}-${selectedMatchup.id}`}
                leagueId={leagueId}
                initialWeek={week ?? undefined}
                matchupId={selectedMatchup.id}
                showWeekSelector={false}
              />
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
