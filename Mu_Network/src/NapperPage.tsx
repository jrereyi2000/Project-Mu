import React, { useState } from "react";
import "./napper.css";
import NapperMatchupTab from "./components/napper/NapperMatchupTab";
import NapperTeamTab from "./components/napper/NapperTeamTab";
import {
  BlueRobotAvatar,
  PinkRobotAvatar,
  NapperMoonIcon,
  getPlayerHeadshotUrl,
  handleImageError,
} from "./components/napper/napperUtils";
import type { NapperChatMessage, NapperStarterPlayer } from "./types/napper";

interface NapperPageProps {
  leagueId?: number;
  initialTab?: "MATCHUP" | "TEAM" | "LEAGUE" | "PLAYERS" | "TREND" | "TRADES" | "SCORES";
  onBackToMu?: () => void;
}

// Sleeper benchmark benchwarmer cards from data/images/sleeper_desktop_reference.png
const DEFAULT_CHAT_MESSAGES: NapperChatMessage[] = [
  {
    id: 1,
    league_id: 1,
    sender_type: "SYSTEM_BOT",
    sender_name: "Sleeper Bot",
    sender_handle: "sleeperbot",
    avatar_url: "",
    message_text: "@Team 2 had the best RB benchwarmer this week!",
    card_payload: {
      type: "BENCHWARMER",
      title: "RB BENCHWARMER OF THE WEEK",
      subtitle: "@Team 2 had the best RB benchwarmer this week!",
      player: {
        name: "Aaron Jones",
        position: "RB",
        team: "MIN",
        stats: "25 CAR, 113 YD, 1 TD, 3/3 REC, 4 YD, 1 FUM LOST",
        fpts: 18.7,
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/3042519.png",
      },
    },
    created_at: "2026-10-14T09:15:00Z",
  },
  {
    id: 2,
    league_id: 1,
    sender_type: "SYSTEM_BOT",
    sender_name: "Sleeper Bot",
    sender_handle: "sleeperbot",
    avatar_url: "",
    message_text: "@jrereyi2000 had the best WR benchwarmer this week!",
    card_payload: {
      type: "BENCHWARMER",
      title: "WR BENCHWARMER OF THE WEEK",
      subtitle: "@jrereyi2000 had the best WR benchwarmer this week!",
      player: {
        name: "Puka Nacua",
        position: "WR",
        team: "LAR",
        stats: "2 CAR, 9 YD, 17/24 REC, 138 YD, 1 TD",
        fpts: 37.7,
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4426515.png",
      },
    },
    created_at: "2026-10-14T09:16:00Z",
  },
  {
    id: 3,
    league_id: 1,
    sender_type: "SYSTEM_BOT",
    sender_name: "Sleeper Bot",
    sender_handle: "sleeperbot",
    avatar_url: "",
    message_text: "@Team 6 had the best TE benchwarmer this week!",
    card_payload: {
      type: "BENCHWARMER",
      title: "TE BENCHWARMER OF THE WEEK",
      subtitle: "@Team 6 had the best TE benchwarmer this week!",
      player: {
        name: "Brock Bowers",
        position: "TE",
        team: "LV",
        stats: "8/10 REC, 110 YD, 1 TD, 1 FUM LOST",
        fpts: 21.0,
        headshot_url: "https://a.espncdn.com/i/headshots/nfl/players/full/4432665.png",
      },
    },
    created_at: "2026-10-14T09:17:00Z",
  },
];

export const NapperPage: React.FC<NapperPageProps> = ({
  leagueId = 1,
  initialTab = "MATCHUP",
  onBackToMu,
}) => {
  const [activeTab, setActiveTab] = useState<
    "MATCHUP" | "TEAM" | "LEAGUE" | "PLAYERS" | "TREND" | "TRADES" | "SCORES"
  >(initialTab);
  const [selectedLeagueId, setSelectedLeagueId] = useState<number>(leagueId);
  const [chatMessages, setChatMessages] = useState<NapperChatMessage[]>(DEFAULT_CHAT_MESSAGES);
  const [inputText, setInputText] = useState("");
  const [selectedPlayer, setSelectedPlayer] = useState<NapperStarterPlayer | null>(null);

  const handleSendMessage = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!inputText.trim()) return;

    const newMsg: NapperChatMessage = {
      id: Date.now(),
      league_id: selectedLeagueId,
      sender_type: "USER",
      sender_name: "jrereyi2000",
      sender_handle: "jrereyi2000",
      avatar_url: "",
      message_text: inputText.trim(),
      created_at: new Date().toISOString(),
    };

    setChatMessages((prev) => [...prev, newMsg]);
    setInputText("");
  };

  return (
    <div className="napper-shell">
      {/* =========================================================================
          PANEL 1: LEFT NAVIGATION RAIL (260px)
          Matches Sleeper desktop left sidebar
          ========================================================================= */}
      <aside className="napper-left-rail">
        {/* Sleeper / Napper Brand Header */}
        <div className="p-4 flex items-center justify-between border-b border-[#1e2a3c]">
          <div className="flex items-center gap-2.5">
            <NapperMoonIcon size={26} />
            <div className="flex items-baseline gap-1.5">
              <span className="text-xl font-black tracking-tight text-white font-sans lowercase">
                sleeper
              </span>
              <span className="text-[10px] uppercase font-extrabold tracking-widest text-[#00ceb8] bg-[#00ceb8]/15 px-1.5 py-0.5 rounded border border-[#00ceb8]/30">
                napper
              </span>
            </div>
          </div>
          {onBackToMu && (
            <button
              type="button"
              onClick={onBackToMu}
              title="Return to Mu Network"
              className="text-[11px] font-semibold text-[#8a99ad] hover:text-[#00ceb8] transition-colors cursor-pointer bg-[#172130] hover:bg-[#202d40] px-2 py-1 rounded border border-[#283850]"
            >
              Exit
            </button>
          )}
        </div>

        {/* Top Direct Navigation */}
        <div className="px-3 py-2 space-y-1">
          <div className="flex items-center justify-between px-3 py-2 rounded-xl text-xs font-bold text-[#8a99ad] hover:text-white hover:bg-[#182333] transition-colors cursor-pointer group">
            <div className="flex items-center gap-3">
              <div className="relative">
                <svg className="w-4 h-4 text-[#8a99ad] group-hover:text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
                <span className="absolute -top-1.5 -right-2 bg-[#00ceb8] text-[#0d1722] text-[9px] font-black w-4 h-4 rounded-full flex items-center justify-center">
                  2
                </span>
              </div>
              <span>Direct Messages</span>
            </div>
          </div>

          <div className="flex items-center justify-between px-3 py-2 rounded-xl text-xs font-bold text-[#8a99ad] hover:text-white hover:bg-[#182333] transition-colors cursor-pointer group">
            <div className="flex items-center gap-3">
              <svg className="w-4 h-4 text-[#8a99ad] group-hover:text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" />
                <polyline points="22,6 12,13 2,6" />
              </svg>
              <span>Inbox</span>
            </div>
          </div>
        </div>

        {/* Leagues Header */}
        <div className="px-5 pt-3 pb-1 flex items-center justify-between">
          <span className="text-[10px] font-extrabold uppercase tracking-wider text-[#5c6c80]">
            Leagues
          </span>
          <button
            type="button"
            title="Create or Join League"
            className="w-5 h-5 rounded-full border border-[#283850] bg-[#172130] hover:border-[#00ceb8] hover:text-[#00ceb8] flex items-center justify-center text-xs text-[#8a99ad] cursor-pointer transition-colors"
          >
            +
          </button>
        </div>

        {/* Mock Drafts Item */}
        <div className="px-3 py-1">
          <div className="flex items-center gap-3 px-3 py-2 rounded-xl text-xs font-bold text-[#8a99ad] hover:text-white hover:bg-[#182333] transition-colors cursor-pointer">
            <svg className="w-4 h-4 text-[#5c6c80]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <path d="M3 9h18M3 15h18M9 3v18M15 3v18" />
            </svg>
            <span className="tracking-wide">MOCK DRAFTS</span>
          </div>
        </div>

        {/* Scrollable League List */}
        <div className="napper-left-rail-scroll px-3 py-1 space-y-1.5">
          {/* League 1 (Active Solo League from Reference) */}
          <div
            onClick={() => setSelectedLeagueId(1)}
            className={`p-2.5 rounded-xl transition-all cursor-pointer border ${
              selectedLeagueId === 1
                ? "bg-[#182436] border-[#00ceb8]/50 shadow-[0_0_12px_rgba(0,206,184,0.15)]"
                : "bg-transparent border-transparent hover:bg-[#16202e]"
            }`}
          >
            <div className="flex items-start gap-2.5">
              <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[#00ceb8] to-[#0ea5e9] flex items-center justify-center shrink-0 shadow-sm">
                <NapperMoonIcon size={20} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-xs font-bold text-white truncate">Solo</div>
                <div className="text-[10px] text-[#8a99ad] truncate">
                  The Straw... <span className="text-[#5c6c80]">vs.</span> Team 3
                </div>
                <div className="flex items-center justify-between text-[10px] font-mono mt-0.5">
                  <span className="text-white font-semibold">- 152.14</span>
                  <span className="text-[#00ceb8] font-bold">25.40 113.95</span>
                </div>
              </div>
            </div>
          </div>

          {/* League 2: Justice League Pick'em */}
          <div
            onClick={() => setSelectedLeagueId(2)}
            className={`p-2.5 rounded-xl transition-all cursor-pointer border ${
              selectedLeagueId === 2
                ? "bg-[#182436] border-[#00ceb8]/50"
                : "bg-transparent border-transparent hover:bg-[#16202e]"
            }`}
          >
            <div className="flex items-start gap-2.5">
              <div className="w-9 h-9 rounded-xl bg-[#092d3b] border border-[#0284c7]/40 flex items-center justify-center shrink-0 text-[#38bdf8] font-black text-[9px] tracking-tight">
                PICK'EM
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-xs font-bold text-[#cbd5e1] truncate">Justice League Pick...</div>
                <div className="text-[10px] text-[#5c6c80] truncate">2026 500-Team Pick'ems</div>
              </div>
            </div>
          </div>

          {/* League 3: The Justice League */}
          <div
            onClick={() => setSelectedLeagueId(3)}
            className={`p-2.5 rounded-xl transition-all cursor-pointer border ${
              selectedLeagueId === 3
                ? "bg-[#182436] border-[#00ceb8]/50"
                : "bg-transparent border-transparent hover:bg-[#16202e]"
            }`}
          >
            <div className="flex items-start gap-2.5">
              <div className="w-9 h-9 rounded-xl bg-white flex items-center justify-center shrink-0 overflow-hidden">
                <img
                  src="https://sleepercdn.com/images/v2/icons/league_avatar_default.png"
                  alt="Justice League"
                  onError={handleImageError}
                  className="w-full h-full object-cover"
                />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-xs font-bold text-[#cbd5e1] truncate">The Justice League</div>
                <div className="text-[10px] text-[#8a99ad] truncate">
                  Free Eats <span className="text-[#5c6c80]">vs.</span> Zesty Willi...
                </div>
                <div className="flex items-center justify-between text-[10px] font-mono mt-0.5">
                  <span className="text-[#8a99ad]">5.20 119.73</span>
                  <span className="text-[#00ceb8]">25.40 135.08</span>
                </div>
              </div>
            </div>
          </div>

          {/* League 4: The Avengers */}
          <div
            onClick={() => setSelectedLeagueId(4)}
            className={`p-2.5 rounded-xl transition-all cursor-pointer border ${
              selectedLeagueId === 4
                ? "bg-[#182436] border-[#00ceb8]/50"
                : "bg-transparent border-transparent hover:bg-[#16202e]"
            }`}
          >
            <div className="flex items-start gap-2.5">
              <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[#ea580c] to-[#b91c1c] flex items-center justify-center shrink-0 text-white font-black text-xs">
                A
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-xs font-bold text-[#cbd5e1] truncate">The Avengers</div>
                <div className="text-[10px] text-[#8a99ad] truncate">2026 8-Team Dynasty PPR</div>
                <div className="inline-flex items-center gap-1 mt-0.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-[#38bdf8]" />
                  <span className="text-[9px] font-bold text-[#38bdf8] tracking-wider uppercase">
                    PRE DRAFT
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* User Profile Footer */}
        <div className="p-3 border-t border-[#1e2a3c] bg-[#0e1520] flex items-center justify-between">
          <div className="flex items-center gap-2.5 min-w-0">
            <BlueRobotAvatar size={34} />
            <div className="min-w-0">
              <div className="text-xs font-bold text-white truncate">jrereyi2000</div>
              <div className="flex items-center gap-1.5 text-[10px] text-[#8a99ad]">
                <span className="text-[#f59e0b]">🍪</span>
                <span className="font-mono font-bold text-[#cbd5e1]">0</span>
              </div>
            </div>
          </div>
          <button
            type="button"
            title="User Settings"
            className="w-7 h-7 rounded-lg bg-[#172130] hover:bg-[#202d40] border border-[#283850] flex items-center justify-center text-[#8a99ad] hover:text-white transition-colors cursor-pointer"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
        </div>
      </aside>

      {/* =========================================================================
          PANEL 2: CENTER STAGE (flex-1)
          Houses League Header, Nav Tab Pills, and Matchup/Team Views
          ========================================================================= */}
      <main className="napper-center-stage">
        {/* League Top Header Bar */}
        <header className="px-6 py-4 flex items-center justify-between border-b border-[#1c2738] bg-[#121926]/90 backdrop-blur sticky top-0 z-20">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[#00ceb8] to-[#0ea5e9] flex items-center justify-center shadow-md">
              <NapperMoonIcon size={22} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-black tracking-tight text-white font-sans">
                  Solo
                </h1>
                <span className="text-xs text-[#8a99ad] font-medium">
                  2026 8-Team SF PPR
                </span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              title="League Settings"
              className="w-9 h-9 rounded-xl bg-[#1a2332] hover:bg-[#202d40] border border-[#283850] flex items-center justify-center text-[#8a99ad] hover:text-white transition-colors cursor-pointer"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
            </button>
          </div>
        </header>

        {/* Sleeper Pill Nav Tabs Bar */}
        <div className="px-6 pt-4 pb-2">
          <div className="flex items-center gap-2 overflow-x-auto pb-1 scrollbar-none">
            <button
              type="button"
              onClick={() => setActiveTab("MATCHUP")}
              className={`napper-nav-tab ${activeTab === "MATCHUP" ? "active" : ""}`}
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <rect x="3" y="3" width="7" height="7" />
                <rect x="14" y="3" width="7" height="7" />
                <rect x="14" y="14" width="7" height="7" />
                <rect x="3" y="14" width="7" height="7" />
              </svg>
              <span>Matchup</span>
            </button>

            <button
              type="button"
              onClick={() => setActiveTab("TEAM")}
              className={`napper-nav-tab ${activeTab === "TEAM" ? "active" : ""}`}
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" />
                <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                <path d="M16 3.13a4 4 0 0 1 0 7.75" />
              </svg>
              <span>Team</span>
            </button>

            <button
              type="button"
              onClick={() => setActiveTab("LEAGUE")}
              className={`napper-nav-tab ${activeTab === "LEAGUE" ? "active" : ""}`}
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
              </svg>
              <span>League</span>
            </button>

            <button
              type="button"
              onClick={() => setActiveTab("PLAYERS")}
              className={`napper-nav-tab ${activeTab === "PLAYERS" ? "active" : ""}`}
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <span>Players</span>
            </button>

            <button
              type="button"
              onClick={() => setActiveTab("TREND")}
              className={`napper-nav-tab ${activeTab === "TREND" ? "active" : ""}`}
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
                <polyline points="17 6 23 6 23 12" />
              </svg>
              <span>Trend</span>
            </button>

            <button
              type="button"
              onClick={() => setActiveTab("TRADES")}
              className={`napper-nav-tab ${activeTab === "TRADES" ? "active" : ""}`}
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <polyline points="17 1 21 5 17 9" />
                <path d="M3 11V9a4 4 0 0 1 4-4h14" />
                <polyline points="7 23 3 19 7 15" />
                <path d="M21 13v2a4 4 0 0 1-4 4H3" />
              </svg>
              <span>Trades</span>
            </button>

            <button
              type="button"
              onClick={() => setActiveTab("SCORES")}
              className={`napper-nav-tab ${activeTab === "SCORES" ? "active" : ""}`}
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
              <span>Scores</span>
            </button>
          </div>
        </div>

        {/* Tab Body View */}
        <div className="flex-1 pb-10">
          {activeTab === "MATCHUP" && (
            <NapperMatchupTab
              leagueId={selectedLeagueId}
              onSelectPlayer={(p) => setSelectedPlayer(p)}
            />
          )}

          {activeTab === "TEAM" && (
            <NapperTeamTab
              leagueId={selectedLeagueId}
              onSelectPlayer={(p) => setSelectedPlayer(p)}
            />
          )}

          {activeTab !== "MATCHUP" && activeTab !== "TEAM" && (
            <div className="max-w-4xl mx-auto px-6 py-12 text-center">
              <div className="w-14 h-14 mx-auto rounded-2xl bg-[#1a2332] border border-[#283850] flex items-center justify-center text-[#00ceb8] mb-4">
                <NapperMoonIcon size={32} />
              </div>
              <h3 className="text-lg font-bold text-white mb-2">
                {activeTab} Overview
              </h3>
              <p className="text-xs text-[#8a99ad] max-w-md mx-auto">
                Live simulated fantasy records, automated AI trade offers, and waiver wire depth are active for League #{selectedLeagueId}.
              </p>
              <div className="mt-6 flex justify-center gap-3">
                <button
                  type="button"
                  onClick={() => setActiveTab("MATCHUP")}
                  className="px-4 py-2 rounded-xl bg-[#00ceb8] text-[#0d1722] font-bold text-xs hover:bg-[#2bd9c6] transition-colors cursor-pointer"
                >
                  Return to Matchup
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab("TEAM")}
                  className="px-4 py-2 rounded-xl bg-[#1a2332] text-white border border-[#283850] font-bold text-xs hover:bg-[#202d40] transition-colors cursor-pointer"
                >
                  Manage Roster
                </button>
              </div>
            </div>
          )}
        </div>
      </main>

      {/* =========================================================================
          PANEL 3: RIGHT CHAT RAIL (340px)
          Houses League Chat, Benchwarmer Awards, and Interactive Input
          ========================================================================= */}
      <aside className="napper-right-rail">
        {/* Chat Rail Header */}
        <div className="p-4 flex items-center justify-between border-b border-[#1e2a3c]">
          <h2 className="text-base font-extrabold text-white tracking-wide">
            League Chat
          </h2>
          <div className="flex items-center gap-2">
            <button
              type="button"
              title="Pin Chat"
              className="w-7 h-7 rounded-lg bg-[#172130] hover:bg-[#202d40] border border-[#283850] flex items-center justify-center text-[#8a99ad] hover:text-white transition-colors cursor-pointer"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="12" y1="17" x2="12" y2="22" />
                <path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24Z" />
              </svg>
            </button>
            <button
              type="button"
              title="Notifications"
              className="w-7 h-7 rounded-lg bg-[#172130] hover:bg-[#202d40] border border-[#283850] flex items-center justify-center text-[#8a99ad] hover:text-white transition-colors cursor-pointer"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
              </svg>
            </button>
          </div>
        </div>

        {/* Chat Feed (Scrollable) */}
        <div className="flex-1 overflow-y-auto p-3 space-y-3 scrollbar-thin">
          {chatMessages.map((msg) => {
            const card = msg.card_payload;

            if (card && card.type === "BENCHWARMER" && card.player) {
              return (
                <div key={msg.id} className="napper-chat-card-benchwarmer">
                  {/* Benchwarmer Header & Quote Icon */}
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] font-extrabold uppercase tracking-wider text-[#cbd5e1]">
                      {card.title}
                    </span>
                    <span className="text-base font-serif text-[#8a99ad] leading-none">”</span>
                  </div>

                  {/* Recognition Text */}
                  <div className="text-[11px] text-[#8a99ad] mb-2 leading-relaxed">
                    {card.subtitle}
                  </div>

                  {/* Player Box Card */}
                  <div className="napper-chat-inner-player">
                    <img
                      src={getPlayerHeadshotUrl(card.player.name, card.player.headshot_url)}
                      alt={card.player.name}
                      onError={handleImageError}
                      className="w-10 h-10 rounded-full object-cover border border-[#283850] shrink-0"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="text-xs font-bold text-white truncate">
                        {card.player.name}
                      </div>
                      <div className="text-[10px] text-[#8a99ad] font-semibold">
                        {card.player.position} - {card.player.team}
                      </div>
                      <div className="text-[9px] text-[#8a99ad] truncate mt-0.5 font-mono">
                        {card.player.stats}
                      </div>
                    </div>
                    <div className="text-right pl-1 shrink-0">
                      <div className="text-base font-extrabold text-white font-mono">
                        {card.player.fpts.toFixed(1)}
                      </div>
                    </div>
                  </div>
                </div>
              );
            }

            // Normal User/AI chat message
            return (
              <div key={msg.id} className="bg-[#172130] border border-[#243449] rounded-xl p-3">
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-1.5">
                    {msg.sender_type === "USER" ? (
                      <BlueRobotAvatar size={18} />
                    ) : (
                      <PinkRobotAvatar size={18} />
                    )}
                    <span className="text-xs font-bold text-white">
                      {msg.sender_handle}
                    </span>
                  </div>
                  <span className="text-[9px] text-[#5c6c80]">
                    {new Date(msg.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </span>
                </div>
                <p className="text-xs text-[#cbd5e1] leading-relaxed mt-1">
                  {msg.message_text}
                </p>
              </div>
            );
          })}
        </div>

        {/* Chat Input Bar */}
        <form onSubmit={handleSendMessage} className="napper-chat-input-container">
          <button
            type="button"
            title="Attach Media"
            className="w-8 h-8 rounded-full bg-[#1a2536] hover:bg-[#223147] border border-[#283850] flex items-center justify-center text-sm font-bold text-[#8a99ad] hover:text-white transition-colors cursor-pointer shrink-0"
          >
            +
          </button>
          <button
            type="button"
            title="Insert GIF"
            className="px-2 py-1 rounded bg-[#1a2536] hover:bg-[#223147] border border-[#283850] text-[10px] font-black text-[#8a99ad] hover:text-white transition-colors cursor-pointer shrink-0"
          >
            GIF
          </button>
          <input
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            placeholder="Enter Message"
            className="napper-chat-input-pill"
          />
          <button
            type="button"
            title="Insert Emoji"
            className="text-base text-[#8a99ad] hover:text-white cursor-pointer px-1 shrink-0"
          >
            😊
          </button>
          <button
            type="submit"
            disabled={!inputText.trim()}
            title="Send Message"
            className="w-8 h-8 rounded-full bg-[#00ceb8] disabled:bg-[#1a2536] disabled:text-[#5c6c80] text-[#0d1722] hover:bg-[#2bd9c6] flex items-center justify-center transition-colors cursor-pointer disabled:cursor-not-allowed shrink-0"
          >
            <svg className="w-4 h-4 translate-x-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </button>
        </form>
      </aside>
    </div>
  );
};

export default NapperPage;
