import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import type { NapperMatchupView, NapperStarterPlayer } from "../../types/napper";
import {
  BlueRobotAvatar,
  PinkRobotAvatar,
  getPlayerHeadshotUrl,
  getSlotBadgeClass,
  handleImageError,
  InjuryPill,
} from "./napperUtils";

interface NapperMatchupTabProps {
  leagueId: number;
  initialWeek?: number;
  matchupId?: number;
  showWeekSelector?: boolean;
  onSelectPlayer?: (player: NapperStarterPlayer) => void;
}

export const NapperMatchupTab: React.FC<NapperMatchupTabProps> = ({
  leagueId,
  initialWeek,
  matchupId,
  showWeekSelector = true,
  onSelectPlayer,
}) => {
  const [week, setWeek] = useState<number | null>(initialWeek ?? null);
  const [weekMenuOpen, setWeekMenuOpen] = useState(false);
  const [matchup, setMatchup] = useState<NapperMatchupView | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [benchOpen, setBenchOpen] = useState(true);

  // Fetch the selected league's matchup from the backend
  useEffect(() => {
    let active = true;
    const fetchMatchup = async () => {
      try {
        setLoading(true);
        setLoadError(null);
        const params = new URLSearchParams();
        if (week != null) params.set("week", String(week));
        if (matchupId != null) params.set("matchup_id", String(matchupId));
        const query = params.toString();
        const res = await fetch(`${API_BASE}/api/napper/league/${leagueId}/matchup${query ? `?${query}` : ""}`);
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.detail || "Could not load this league matchup.");
        }
        const data = await res.json();
        if (active && data?.left_team && Array.isArray(data.starters)) {
          setMatchup(data);
          setWeek(data.week);
        } else if (active) {
          throw new Error("No matchup is available for this league and week.");
        }
      } catch (error) {
        if (active) {
          setMatchup(null);
          setLoadError(error instanceof Error ? error.message : "Could not load this league matchup.");
        }
      } finally {
        if (active) setLoading(false);
      }
    };
    fetchMatchup();
    return () => {
      active = false;
    };
  }, [leagueId, week, matchupId]);

  if (!matchup) {
    return (
      <div className="px-6 py-12 text-center text-sm text-[#8a99ad]" role={loadError ? "alert" : undefined}>
        {loading ? "Loading this league’s matchup…" : loadError || "No matchup is available for this week."}
      </div>
    );
  }

  const leftTeam = matchup.left_team;
  const rightTeam = matchup.right_team;

  // Calculate win percentage displays
  const leftWinPct = Math.round((leftTeam.win_prob ?? 0.5) * 100);
  const rightWinPct = 100 - leftWinPct;
  const formatRecord = (team: typeof leftTeam) => `${team.wins ?? 0}-${team.losses ?? 0}${team.ties ? `-${team.ties}` : ""}`;

  return (
    <div className="flex-1 px-5 py-4 max-w-6xl mx-auto w-full">
      {/* =========================================================================
          VERSUS BANNER (Sleeper Desktop Matchup Header Card)
          ========================================================================= */}
      <div className="napper-matchup-banner mb-6">
        <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] items-center gap-2 md:gap-4">
          {/* Left Team Overview */}
          <div className="flex items-center gap-3 min-w-0">
            <BlueRobotAvatar size={48} />
            <div className="min-w-0 flex-1">
              {/* Manager Handle */}
              <div className="text-[11px] font-semibold tracking-wide text-[#8a99ad] truncate">
                @{leftTeam.manager_handle || "jrereyi2000"}
              </div>
              {/* Team Name */}
              <div className="text-base font-extrabold text-white truncate leading-snug">
                {leftTeam.team_name}
              </div>
              {/* Win Probability & Record under Team Name */}
              <div className="flex items-center gap-2 mt-1">
                <span className="text-xs font-black text-[#00ceb8]">{leftWinPct}%</span>
                <span className="text-xs text-[#8a99ad] font-semibold">
                  {formatRecord(leftTeam)}
                </span>
              </div>
            </div>

            {/* Left Scores (Live Score vs Projected Points) */}
            <div className="text-right pl-3 shrink-0">
              <div className="text-3xl font-black tracking-tight text-white leading-none">
                {leftTeam.score > 0 ? leftTeam.score.toFixed(2) : "-"}
              </div>
              <div className="text-xs text-[#8a99ad] font-mono mt-1 font-medium">
                {leftTeam.proj.toFixed(2)}
              </div>
            </div>
          </div>

          {/* VS Divider */}
          <div className="flex flex-col items-center justify-center px-2 shrink-0">
            <span className="text-[11px] font-extrabold tracking-widest text-[#5c6c80] uppercase">
              VS
            </span>
          </div>

          {/* Right Team Overview */}
          <div className="flex items-center justify-end gap-3 min-w-0">
            {/* Right Scores (Live Score vs Projected Points) */}
            <div className="text-left pr-3 shrink-0">
              <div className="text-3xl font-black tracking-tight text-white leading-none">
                {rightTeam.score > 0 ? rightTeam.score.toFixed(2) : "-"}
              </div>
              <div className="text-xs text-[#8a99ad] font-mono mt-1 font-medium">
                {rightTeam.proj.toFixed(2)}
              </div>
            </div>

            {/* Right Team Info */}
            <div className="min-w-0 flex-1 text-right">
              {/* Manager Handle */}
              <div className="text-[11px] font-semibold tracking-wide text-[#8a99ad] truncate">
                {rightTeam.manager_handle || "-"}
              </div>
              {/* Team Name */}
              <div className="text-base font-extrabold text-white truncate leading-snug">
                {rightTeam.team_name}
              </div>
              {/* Win Probability & Record */}
              <div className="flex items-center justify-end gap-2 mt-1">
                <span className="text-xs text-[#8a99ad] font-semibold">
                  {formatRecord(rightTeam)}
                </span>
                <span className="text-xs font-black text-[#cbd5e1]">{rightWinPct}%</span>
              </div>
            </div>

            <PinkRobotAvatar size={48} />
          </div>
        </div>

        {/* Win Probability Gauge (76% vs 24% Gradient Bar) */}
        <div className="mt-4 pt-3 border-t border-[#202d40]">
          <div className="napper-winprob-track">
            <div
              className="napper-winprob-fill-left"
              style={{ width: `${leftWinPct}%` }}
            />
            <div
              className="napper-winprob-fill-right"
              style={{ width: `${rightWinPct}%` }}
            />
          </div>

          {/* Unplayed Player Count Summary ("Yet to play (10) 2 QB, 2 RB, 4 WR, 2 TE") */}
          <div className="flex items-start justify-between text-xs text-[#8a99ad] mt-2.5 font-medium leading-relaxed">
            <div className="whitespace-pre-line text-left">
              {leftTeam.yet_to_play_summary || ""}
            </div>
            <div className="whitespace-pre-line text-right">
              {rightTeam.yet_to_play_summary || ""}
            </div>
          </div>
        </div>
      </div>

      {/* =========================================================================
          STARTERS SECTION HEADER & WEEK SELECTOR
          ========================================================================= */}
      <div className="flex items-center justify-between mb-3 px-1">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-extrabold text-white tracking-wide">
            Starters
          </h2>
        </div>

        {/* Week Selector Dropdown */}
        {showWeekSelector && (
        <div className="relative">
          <button
            type="button"
            onClick={() => setWeekMenuOpen(!weekMenuOpen)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#182333] border border-[#283850] text-xs font-bold text-white hover:bg-[#202d40] hover:border-[#00ceb8] transition-all cursor-pointer shadow-sm"
          >
            <span>Wk. {week ?? matchup.week}</span>
            <svg
              className={`w-3.5 h-3.5 text-[#8a99ad] transition-transform ${weekMenuOpen ? "rotate-180" : ""}`}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
            >
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>

          {weekMenuOpen && (
            <div className="absolute right-0 mt-1 w-32 max-h-60 overflow-y-auto bg-[#172130] border border-[#283850] rounded-xl shadow-2xl z-30 py-1 scrollbar-thin">
              {Array.from({ length: 18 }, (_, i) => i + 1).map((w) => (
                <button
                  key={w}
                  type="button"
                  onClick={() => {
                    setWeek(w);
                    setWeekMenuOpen(false);
                  }}
                  className={`w-full text-left px-3 py-1.5 text-xs font-semibold cursor-pointer transition-colors ${
                    w === (week ?? matchup.week)
                      ? "bg-[#00ceb8] text-[#0d1722]"
                      : "text-[#e2e8f0] hover:bg-[#202d40]"
                  }`}
                >
                  Week {w}
                </button>
              ))}
            </div>
          )}
        </div>
        )}
      </div>

      {/* =========================================================================
          STARTERS COMPARISON ROWS (Pixel-perfect Sleeper layout)
          Headshot, player name, position/team, kickoff time, live/projected score
          ========================================================================= */}
      <div className="space-y-2 mb-6">
        {matchup.starters.map((starterSlot, idx) => {
          const lp = starterSlot.left_player;
          const rp = starterSlot.right_player;
          const slotClass = getSlotBadgeClass(starterSlot.slot);

          return (
            <div key={`${starterSlot.slot}-${idx}`} className="napper-matchup-row">
              {/* Left Player Card */}
              {lp ? (
                <div
                  onClick={() => onSelectPlayer?.(lp)}
                  className="napper-player-card justify-between cursor-pointer group"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <img
                      src={getPlayerHeadshotUrl(lp.player_id, lp.headshot_url, lp.espn_id)}
                      alt={lp.name}
                      onError={handleImageError}
                      className="napper-headshot-crop"
                    />
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="text-xs font-bold text-white group-hover:text-[#00ceb8] transition-colors truncate">
                          {lp.name}
                        </span>
                        <span className="text-[11px] text-[#8a99ad] font-semibold">
                          {lp.position} - {lp.team}
                        </span>
                        <InjuryPill player={lp} />
                      </div>
                      <div className="text-[11px] text-[#8a99ad] flex items-center gap-1.5 mt-0.5 truncate">
                        <span>{lp.kickoff}</span>
                        <span>{lp.opponent}</span>
                        {lp.game_status && (
                          <>
                            <span className="text-[#5c6c80]">•</span>
                            <span className="text-[10px] text-[#8a99ad] truncate">
                              {lp.game_status}
                            </span>
                          </>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Left Player Scores */}
                  <div className="text-right pl-3 shrink-0">
                    <div className="text-sm font-black text-white font-mono leading-none">
                      {!lp.is_yet_to_play ? lp.fpts.toFixed(2) : "-"}
                    </div>
                    <div className="text-[11px] text-[#8a99ad] font-mono mt-1 font-medium">
                      {lp.proj_fpts != null ? lp.proj_fpts.toFixed(2) : "—"}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="napper-player-card items-center justify-center text-xs text-[#5c6c80] border-dashed">
                  Empty Slot
                </div>
              )}

              {/* Centered Position Slot Pill Badge */}
              <div className="flex justify-center">
                <div className={`napper-slot-badge ${slotClass}`}>
                  {starterSlot.slot_label || starterSlot.slot}
                </div>
              </div>

              {/* Right Player Card (Mirrored) */}
              {rp ? (
                <div
                  onClick={() => onSelectPlayer?.(rp)}
                  className="napper-player-card justify-between cursor-pointer group"
                >
                  {/* Right Player Scores */}
                  <div className="text-left pr-3 shrink-0">
                    <div
                      className={`text-sm font-black font-mono leading-none ${
                        !rp.is_yet_to_play ? "text-[#00ceb8]" : "text-white"
                      }`}
                    >
                      {!rp.is_yet_to_play ? rp.fpts.toFixed(2) : "-"}
                    </div>
                    <div className="text-[11px] text-[#8a99ad] font-mono mt-1 font-medium">
                      {rp.proj_fpts != null ? rp.proj_fpts.toFixed(2) : "—"}
                    </div>
                  </div>

                  <div className="flex items-center justify-end gap-3 min-w-0 flex-1">
                    <div className="min-w-0 text-right">
                      <div className="flex items-center justify-end gap-1.5 flex-wrap">
                        <InjuryPill player={rp} />
                        <span className="text-[11px] text-[#8a99ad] font-semibold">
                          {rp.position} - {rp.team}
                        </span>
                        <span className="text-xs font-bold text-white group-hover:text-[#00ceb8] transition-colors truncate">
                          {rp.name}
                        </span>
                      </div>
                      <div className="text-[11px] text-[#8a99ad] flex items-center justify-end gap-1.5 mt-0.5 truncate">
                        {rp.game_status && (
                          <>
                            <span className="text-[10px] text-[#8a99ad] truncate">
                              {rp.game_status}
                            </span>
                            <span className="text-[#5c6c80]">•</span>
                          </>
                        )}
                        <span>{rp.kickoff}</span>
                        <span>{rp.opponent}</span>
                      </div>
                    </div>
                    <img
                      src={getPlayerHeadshotUrl(rp.player_id, rp.headshot_url, rp.espn_id)}
                      alt={rp.name}
                      onError={handleImageError}
                      className="napper-headshot-crop"
                    />
                  </div>
                </div>
              ) : (
                <div className="napper-player-card items-center justify-center text-xs text-[#5c6c80] border-dashed">
                  Empty Slot
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* =========================================================================
          BENCH SECTION ACCORDION
          ========================================================================= */}
      <div className="mb-8">
        <button
          type="button"
          onClick={() => setBenchOpen(!benchOpen)}
          className="flex items-center justify-between w-full py-2 px-1 text-xs font-extrabold text-[#8a99ad] hover:text-white transition-colors cursor-pointer"
        >
          <div className="flex items-center gap-2">
            <span className="tracking-wider uppercase">BENCH</span>
            <span className="text-[10px] px-1.5 py-0.5 bg-[#1a2332] border border-[#283850] rounded text-[#8a99ad] font-mono">
              {matchup.bench.length}
            </span>
          </div>
          <svg
            className={`w-4 h-4 transition-transform ${benchOpen ? "rotate-180" : ""}`}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>

        {benchOpen && (
          <div className="space-y-2 mt-2">
            {matchup.bench.map((benchSlot, idx) => {
              const lp = benchSlot.left_player;
              const rp = benchSlot.right_player;

              return (
                <div key={`bn-${idx}`} className="napper-matchup-row">
                  {/* Left Bench Player */}
                  {lp ? (
                    <div
                      onClick={() => onSelectPlayer?.(lp)}
                      className="napper-player-card justify-between cursor-pointer opacity-85 hover:opacity-100"
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <img
                          src={getPlayerHeadshotUrl(lp.player_id, lp.headshot_url, lp.espn_id)}
                          alt={lp.name}
                          onError={handleImageError}
                          className="napper-headshot-crop opacity-90"
                        />
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5 truncate">
                            <span className="text-xs font-bold text-white truncate">{lp.name}</span>
                            <span className="text-[11px] text-[#8a99ad] font-semibold">
                              {lp.position} - {lp.team}
                            </span>
                          </div>
                          <div className="text-[11px] text-[#8a99ad] mt-0.5">
                            {lp.kickoff} {lp.opponent}
                          </div>
                        </div>
                      </div>
                      <div className="text-right pl-3 shrink-0">
                        <div className="text-xs font-black text-white font-mono leading-none">
                          {!lp.is_yet_to_play ? lp.fpts.toFixed(2) : "-"}
                        </div>
                        <div className="text-[10px] text-[#8a99ad] font-mono mt-1 font-medium">
                          {lp.proj_fpts != null ? lp.proj_fpts.toFixed(2) : "—"}
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="napper-player-card items-center justify-center text-xs text-[#5c6c80] border-dashed">
                      Empty
                    </div>
                  )}

                  {/* Centered BN Slot Badge */}
                  <div className="flex justify-center">
                    <div className="napper-slot-badge slot-bn">BN</div>
                  </div>

                  {/* Right Bench Player */}
                  {rp ? (
                    <div
                      onClick={() => onSelectPlayer?.(rp)}
                      className="napper-player-card justify-between cursor-pointer opacity-85 hover:opacity-100"
                    >
                      <div className="text-left pr-3 shrink-0">
                        <div className="text-xs font-black text-white font-mono leading-none">
                          {!rp.is_yet_to_play ? rp.fpts.toFixed(2) : "-"}
                        </div>
                        <div className="text-[10px] text-[#8a99ad] font-mono mt-1 font-medium">
                          {rp.proj_fpts != null ? rp.proj_fpts.toFixed(2) : "—"}
                        </div>
                      </div>
                      <div className="flex items-center justify-end gap-3 min-w-0 flex-1">
                        <div className="min-w-0 text-right">
                          <div className="flex items-center justify-end gap-1.5 truncate">
                            <span className="text-[11px] text-[#8a99ad] font-semibold">
                              {rp.position} - {rp.team}
                            </span>
                            <span className="text-xs font-bold text-white truncate">{rp.name}</span>
                          </div>
                          <div className="text-[11px] text-[#8a99ad] mt-0.5">
                            {rp.kickoff} {rp.opponent}
                          </div>
                        </div>
                        <img
                          src={getPlayerHeadshotUrl(rp.player_id, rp.headshot_url, rp.espn_id)}
                          alt={rp.name}
                          onError={handleImageError}
                          className="napper-headshot-crop opacity-90"
                        />
                      </div>
                    </div>
                  ) : (
                    <div className="napper-player-card items-center justify-center text-xs text-[#5c6c80] border-dashed">
                      Empty
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default NapperMatchupTab;
