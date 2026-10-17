import React, { useEffect, useState } from "react";
import { API_BASE } from "../../api";
import type { NapperMatchupView, NapperStarterPlayer, NapperStarterSlot } from "../../types/napper";
import {
  BlueRobotAvatar,
  PinkRobotAvatar,
  getPlayerHeadshotUrl,
  getSlotBadgeClass,
  handleImageError,
} from "./napperUtils";

interface NapperMatchupTabProps {
  leagueId: number;
  initialWeek?: number;
  onSelectPlayer?: (player: NapperStarterPlayer) => void;
}

// Baseline mock data matching the Sleeper reference screenshot exactly (Week 6)
const REFERENCE_MATCHUP: NapperMatchupView = {
  league_id: 1,
  week: 6,
  left_team: {
    id: 1,
    league_id: 1,
    user_id: 1,
    team_name: "The Straw Hat Pirates",
    manager_handle: "jrereyi2000",
    avatar_url: "",
    is_ai: false,
    wins: 5,
    losses: 0,
    ties: 0,
    points_for: 768.4,
    points_against: 612.3,
    score: 0.0,
    proj: 152.14,
    win_prob: 0.76,
    yet_to_play_summary: "Yet to play (10)\n2 QB, 2 RB, 4 WR, 2 TE",
  },
  right_team: {
    id: 3,
    league_id: 1,
    user_id: null,
    team_name: "Team 3",
    manager_handle: "-",
    avatar_url: "",
    is_ai: true,
    persona: "TRASH_TALKER",
    wins: 4,
    losses: 1,
    ties: 0,
    points_for: 710.2,
    points_against: 645.8,
    score: 25.4,
    proj: 113.95,
    win_prob: 0.24,
    yet_to_play_summary: "Yet to play (9)\n2 QB, 2 RB, 4 WR, TE",
  },
  starters: [
    {
      slot: "QB",
      slot_label: "QB",
      left_player: {
        id: 101,
        player_id: 3918298,
        name: "J. Allen",
        position: "QB",
        team: "BUF",
        opponent: "@ LV",
        kickoff: "Sun 4:25 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3918298.png",
        fpts: 0.0,
        proj_fpts: 24.95,
        is_yet_to_play: true,
        injury_status: null,
      },
      right_player: {
        id: 201,
        player_id: 2577417,
        name: "D. Prescott",
        position: "QB",
        team: "DAL",
        opponent: "@ GB",
        kickoff: "Sun 8:20 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/2577417.png",
        fpts: 0.0,
        proj_fpts: 18.45,
        is_yet_to_play: true,
        injury_status: null,
      },
    },
    {
      slot: "RB",
      slot_label: "RB",
      left_player: {
        id: 102,
        player_id: 4036378,
        name: "J. Love",
        position: "RB",
        team: "ARI",
        opponent: "@ LAR",
        kickoff: "Sun 4:05 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4036378.png",
        fpts: 0.0,
        proj_fpts: 0.0,
        is_yet_to_play: true,
        injury_status: "O",
      },
      right_player: {
        id: 202,
        player_id: 4567048,
        name: "K. Walker",
        position: "RB",
        team: "KC",
        opponent: "vs LAC",
        kickoff: "Sun 4:25 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4567048.png",
        fpts: 0.0,
        proj_fpts: 22.76,
        is_yet_to_play: true,
        injury_status: null,
      },
    },
    {
      slot: "RB",
      slot_label: "RB",
      left_player: {
        id: 103,
        player_id: 4259545,
        name: "D. Swift",
        position: "RB",
        team: "CHI",
        opponent: "@ ATL",
        kickoff: "Sun 1:00 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4259545.png",
        fpts: 0.0,
        proj_fpts: 16.69,
        is_yet_to_play: true,
        injury_status: null,
      },
      right_player: {
        id: 203,
        player_id: 4035538,
        name: "D. Montgomery",
        position: "RB",
        team: "HOU",
        opponent: "@ JAX",
        kickoff: "Sun 9:30 AM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4035538.png",
        fpts: 0.0,
        proj_fpts: 0.0,
        is_yet_to_play: true,
        injury_status: "O",
      },
    },
    {
      slot: "WR",
      slot_label: "WR",
      left_player: {
        id: 104,
        player_id: 4258169,
        name: "N. Collins",
        position: "WR",
        team: "HOU",
        opponent: "@ JAX",
        kickoff: "Sun 9:30 AM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4258169.png",
        fpts: 0.0,
        proj_fpts: 18.34,
        is_yet_to_play: true,
        injury_status: null,
      },
      right_player: {
        id: 204,
        player_id: 4430878,
        name: "J. Smith-Njigba",
        position: "WR",
        team: "SEA",
        opponent: "@ DEN",
        kickoff: "Final L 14-17",
        game_status: "12/16 REC, 134 YD",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4430878.png",
        fpts: 25.4,
        proj_fpts: 25.4,
        is_yet_to_play: false,
        injury_status: null,
      },
    },
    {
      slot: "WR",
      slot_label: "WR",
      left_player: {
        id: 105,
        player_id: 4426515,
        name: "P. Nacua",
        position: "WR",
        team: "LAR",
        opponent: "@ SF",
        kickoff: "Sun 1:00 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4426515.png",
        fpts: 0.0,
        proj_fpts: 17.5,
        is_yet_to_play: true,
        injury_status: null,
      },
      right_player: {
        id: 205,
        player_id: 4262921,
        name: "J. Jefferson",
        position: "WR",
        team: "MIN",
        opponent: "vs GB",
        kickoff: "Sun 1:00 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4262921.png",
        fpts: 0.0,
        proj_fpts: 19.8,
        is_yet_to_play: true,
        injury_status: null,
      },
    },
    {
      slot: "TE",
      slot_label: "TE",
      left_player: {
        id: 106,
        player_id: 4432665,
        name: "B. Bowers",
        position: "TE",
        team: "LV",
        opponent: "vs BUF",
        kickoff: "Sun 4:25 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4432665.png",
        fpts: 0.0,
        proj_fpts: 14.2,
        is_yet_to_play: true,
        injury_status: null,
      },
      right_player: {
        id: 206,
        player_id: 4430027,
        name: "S. LaPorta",
        position: "TE",
        team: "DET",
        opponent: "@ DAL",
        kickoff: "Sun 4:25 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4430027.png",
        fpts: 0.0,
        proj_fpts: 13.5,
        is_yet_to_play: true,
        injury_status: null,
      },
    },
    {
      slot: "FLEX",
      slot_label: "FLEX",
      left_player: {
        id: 107,
        player_id: 4427366,
        name: "B. Hall",
        position: "RB",
        team: "NYJ",
        opponent: "vs NE",
        kickoff: "Sun 1:00 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4427366.png",
        fpts: 0.0,
        proj_fpts: 16.8,
        is_yet_to_play: true,
        injury_status: null,
      },
      right_player: {
        id: 207,
        player_id: 4380150,
        name: "C. Lamb",
        position: "WR",
        team: "DAL",
        opponent: "vs DET",
        kickoff: "Sun 4:25 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4380150.png",
        fpts: 0.0,
        proj_fpts: 17.9,
        is_yet_to_play: true,
        injury_status: null,
      },
    },
    {
      slot: "SUPERFLEX",
      slot_label: "SUPERFLEX",
      left_player: {
        id: 108,
        player_id: 3139477,
        name: "P. Mahomes",
        position: "QB",
        team: "KC",
        opponent: "vs LAC",
        kickoff: "Sun 4:25 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3139477.png",
        fpts: 0.0,
        proj_fpts: 21.4,
        is_yet_to_play: true,
        injury_status: null,
      },
      right_player: {
        id: 208,
        player_id: 3916387,
        name: "L. Jackson",
        position: "QB",
        team: "BAL",
        opponent: "@ CIN",
        kickoff: "Sun 1:00 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3916387.png",
        fpts: 0.0,
        proj_fpts: 22.1,
        is_yet_to_play: true,
        injury_status: null,
      },
    },
  ],
  bench: [
    {
      slot: "BN",
      slot_label: "BN",
      left_player: {
        id: 109,
        player_id: 3042519,
        name: "A. Jones",
        position: "RB",
        team: "MIN",
        opponent: "vs GB",
        kickoff: "Sun 1:00 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3042519.png",
        fpts: 0.0,
        proj_fpts: 14.8,
        is_yet_to_play: true,
        injury_status: null,
      },
      right_player: {
        id: 209,
        player_id: 4372016,
        name: "J. Waddle",
        position: "WR",
        team: "MIA",
        opponent: "@ IND",
        kickoff: "Sun 1:00 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4372016.png",
        fpts: 0.0,
        proj_fpts: 13.2,
        is_yet_to_play: true,
        injury_status: null,
      },
    },
    {
      slot: "BN",
      slot_label: "BN",
      left_player: {
        id: 110,
        player_id: 4431452,
        name: "D. Maye",
        position: "QB",
        team: "NE",
        opponent: "@ NYJ",
        kickoff: "Sun 1:00 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4431452.png",
        fpts: 0.0,
        proj_fpts: 15.6,
        is_yet_to_play: true,
        injury_status: null,
      },
      right_player: {
        id: 210,
        player_id: 4361528,
        name: "T. McBride",
        position: "TE",
        team: "ARI",
        opponent: "@ LAR",
        kickoff: "Sun 4:05 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4361528.png",
        fpts: 0.0,
        proj_fpts: 12.8,
        is_yet_to_play: true,
        injury_status: null,
      },
    },
    {
      slot: "BN",
      slot_label: "BN",
      left_player: {
        id: 111,
        player_id: 4429022,
        name: "G. Wilson",
        position: "WR",
        team: "NYJ",
        opponent: "vs NE",
        kickoff: "Sun 1:00 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4429022.png",
        fpts: 0.0,
        proj_fpts: 15.1,
        is_yet_to_play: true,
        injury_status: null,
      },
      right_player: {
        id: 211,
        player_id: 4432708,
        name: "M. Harrison Jr.",
        position: "WR",
        team: "ARI",
        opponent: "@ LAR",
        kickoff: "Sun 4:05 PM",
        game_status: "Yet to play",
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4432708.png",
        fpts: 0.0,
        proj_fpts: 14.5,
        is_yet_to_play: true,
        injury_status: null,
      },
    },
  ],
};

export const NapperMatchupTab: React.FC<NapperMatchupTabProps> = ({
  leagueId,
  initialWeek = 6,
  onSelectPlayer,
}) => {
  const [week, setWeek] = useState<number>(initialWeek);
  const [weekMenuOpen, setWeekMenuOpen] = useState(false);
  const [matchup, setMatchup] = useState<NapperMatchupView>(REFERENCE_MATCHUP);
  const [benchOpen, setBenchOpen] = useState(true);

  // Fetch live matchup data from backend API with fallback
  useEffect(() => {
    let active = true;
    const fetchMatchup = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/napper/league/${leagueId}/matchup?week=${week}`);
        if (!res.ok) throw new Error("Matchup fetch failed");
        const data = await res.json();
        if (active && data?.left_team && data?.starters) {
          setMatchup(data);
        }
      } catch {
        // Fallback to reference matchup state
        if (active) {
          setMatchup((prev) => ({
            ...REFERENCE_MATCHUP,
            league_id: leagueId,
            week,
          }));
        }
      }
    };
    fetchMatchup();
    return () => {
      active = false;
    };
  }, [leagueId, week]);

  const leftTeam = matchup.left_team;
  const rightTeam = matchup.right_team;

  // Calculate win percentage displays
  const leftWinPct = Math.round((leftTeam.win_prob ?? 0.76) * 100);
  const rightWinPct = 100 - leftWinPct;

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
                  {leftTeam.wins}-{leftTeam.losses}
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
                  {rightTeam.wins}-{rightTeam.losses}
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
              {leftTeam.yet_to_play_summary || "Yet to play (10)\n2 QB, 2 RB, 4 WR, 2 TE"}
            </div>
            <div className="whitespace-pre-line text-right">
              {rightTeam.yet_to_play_summary || "Yet to play (9)\n2 QB, 2 RB, 4 WR, TE"}
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
        <div className="relative">
          <button
            type="button"
            onClick={() => setWeekMenuOpen(!weekMenuOpen)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#182333] border border-[#283850] text-xs font-bold text-white hover:bg-[#202d40] hover:border-[#00ceb8] transition-all cursor-pointer shadow-sm"
          >
            <span>Wk. {week}</span>
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
                    w === week
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
                      src={getPlayerHeadshotUrl(lp.name, lp.headshot_url)}
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
                        {lp.injury_status && (
                          <span
                            className={`napper-injury-pill ${
                              lp.injury_status === "O" ? "out" : "ques"
                            }`}
                          >
                            {lp.injury_status}
                          </span>
                        )}
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
                      {lp.fpts > 0 ? lp.fpts.toFixed(2) : "-"}
                    </div>
                    <div className="text-[11px] text-[#8a99ad] font-mono mt-1 font-medium">
                      {lp.proj_fpts.toFixed(2)}
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
                        rp.fpts > 0 ? "text-[#00ceb8]" : "text-white"
                      }`}
                    >
                      {rp.fpts > 0 ? rp.fpts.toFixed(2) : "-"}
                    </div>
                    <div className="text-[11px] text-[#8a99ad] font-mono mt-1 font-medium">
                      {rp.proj_fpts.toFixed(2)}
                    </div>
                  </div>

                  <div className="flex items-center justify-end gap-3 min-w-0 flex-1">
                    <div className="min-w-0 text-right">
                      <div className="flex items-center justify-end gap-1.5 flex-wrap">
                        {rp.injury_status && (
                          <span
                            className={`napper-injury-pill ${
                              rp.injury_status === "O" ? "out" : "ques"
                            }`}
                          >
                            {rp.injury_status}
                          </span>
                        )}
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
                      src={getPlayerHeadshotUrl(rp.name, rp.headshot_url)}
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
                          src={getPlayerHeadshotUrl(lp.name, lp.headshot_url)}
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
                          {lp.fpts > 0 ? lp.fpts.toFixed(2) : "-"}
                        </div>
                        <div className="text-[10px] text-[#8a99ad] font-mono mt-1 font-medium">
                          {lp.proj_fpts.toFixed(2)}
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
                          {rp.fpts > 0 ? rp.fpts.toFixed(2) : "-"}
                        </div>
                        <div className="text-[10px] text-[#8a99ad] font-mono mt-1 font-medium">
                          {rp.proj_fpts.toFixed(2)}
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
                          src={getPlayerHeadshotUrl(rp.name, rp.headshot_url)}
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
