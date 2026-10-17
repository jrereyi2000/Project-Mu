import React, { useEffect, useState } from "react";
import "./napper.css";
import { API_BASE } from "./api";
import NapperMatchupTab from "./components/napper/NapperMatchupTab";
import NapperTeamTab from "./components/napper/NapperTeamTab";
import { NapperLeagueTab } from "./components/napper/NapperLeagueTab";
import { NapperPlayersTab } from "./components/napper/NapperPlayersTab";
import { NapperTradesTab } from "./components/napper/NapperTradesTab";
import { NapperScoresTab } from "./components/napper/NapperScoresTab";
import {
  BlueRobotAvatar,
  PinkRobotAvatar,
  NapperMoonIcon,
  getPlayerHeadshotUrl,
  handleImageError,
} from "./components/napper/napperUtils";
import type { NapperChatMessage, NapperStarterPlayer, NapperLeagueSummary, NapperUserProfile } from "./types/napper";

interface NapperPageProps {
  leagueId?: number;
  initialTab?: "MATCHUP" | "TEAM" | "LEAGUE" | "PLAYERS" | "TREND" | "TRADES" | "SCORES";
  onBackToMu?: () => void;
}

const FALLBACK_LEAGUES: NapperLeagueSummary[] = [
  {
    id: 1,
    name: "Solo 2026 8-Team SF PPR",
    season: 2035,
    teams_count: 8,
    scoring_type: "PPR",
    status: "IN_SEASON",
    avatar_url: "",
    user_team_id: 1,
    user_team_name: "The Straw Hat Pirates",
    user_score: 0.0,
    user_proj: 152.14,
    opponent_team_name: "Team 3",
    opponent_score: 25.4,
    opponent_proj: 113.95,
    unplayed_count: 10,
  },
  {
    id: 2,
    name: "The Justice League",
    season: 2035,
    teams_count: 12,
    scoring_type: "PPR",
    status: "IN_SEASON",
    avatar_url: "",
    user_team_id: 3,
    user_team_name: "The Straw Hat Pirates",
    user_score: 0.0,
    user_proj: 128.5,
    opponent_team_name: "Gotham Dark Knights",
    opponent_score: 0.0,
    opponent_proj: 122.0,
    unplayed_count: 9,
  },
  {
    id: 3,
    name: "The Avengers",
    season: 2035,
    teams_count: 8,
    scoring_type: "PPR",
    status: "PRE_DRAFT",
    avatar_url: "",
    user_team_id: 4,
    user_team_name: "The Straw Hat Pirates",
    user_score: 0.0,
    user_proj: 0.0,
    opponent_team_name: "Stark Industries",
    opponent_score: 0.0,
    opponent_proj: 0.0,
    unplayed_count: 0,
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
  const [profile, setProfile] = useState<NapperUserProfile | null>(null);
  const [pendingTradesCount, setPendingTradesCount] = useState<number>(0);
  const [leagues, setLeagues] = useState<NapperLeagueSummary[]>(FALLBACK_LEAGUES);
  const [chatMessages, setChatMessages] = useState<NapperChatMessage[]>([]);
  const [chatLoading, setChatLoading] = useState<boolean>(true);
  const [inputText, setInputText] = useState("");
  const [selectedPlayer, setSelectedPlayer] = useState<NapperStarterPlayer | null>(null);

  // Modal State
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [modalMode, setModalMode] = useState<"CREATE" | "SWITCH">("CREATE");
  const [createName, setCreateName] = useState("");
  const [createTeamsCount, setCreateTeamsCount] = useState<number>(8);
  const [createScoringType, setCreateScoringType] = useState<string>("PPR");
  const [createSuperflex, setCreateSuperflex] = useState<boolean>(true);
  const [createStatus, setCreateStatus] = useState<"IN_SEASON" | "PRE_DRAFT">("IN_SEASON");
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createSuccessMsg, setCreateSuccessMsg] = useState<string | null>(null);

  // 1. Fetch user profile
  useEffect(() => {
    fetch(`${API_BASE}/api/napper/profile`)
      .then((res) => res.json())
      .then((data) => {
        if (data.profile) {
          setProfile(data.profile);
        }
        if (typeof data.pending_trades_count === "number") {
          setPendingTradesCount(data.pending_trades_count);
        }
      })
      .catch(() => {});
  }, []);

  // 2. Fetch leagues list
  const loadLeagues = (newSelectedId?: number) => {
    fetch(`${API_BASE}/api/napper/leagues`)
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data.leagues)) {
          setLeagues(data.leagues);
          if (newSelectedId) {
            setSelectedLeagueId(newSelectedId);
          } else if (data.leagues.length > 0 && !data.leagues.some((l: any) => l.id === selectedLeagueId)) {
            setSelectedLeagueId(data.leagues[0].id);
          }
        }
      })
      .catch(() => {});
  };

  useEffect(() => {
    loadLeagues();
  }, []);

  const handleCreateLeague = (e: React.FormEvent) => {
    e.preventDefault();
    if (!createName.trim()) {
      setCreateError("Please enter a league name.");
      return;
    }
    setCreateSubmitting(true);
    setCreateError(null);

    fetch(`${API_BASE}/api/napper/leagues/create`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: createName.trim(),
        teams_count: createTeamsCount,
        scoring_type: createScoringType,
        is_superflex: createSuperflex,
        status: createStatus,
      }),
    })
      .then((res) => {
        if (!res.ok) {
          return res.json().then((d) => {
            throw new Error(d.detail || "Failed to create league");
          });
        }
        return res.json();
      })
      .then((data) => {
        setCreateSubmitting(false);
        setCreateSuccessMsg(`🎉 League "${data.league.name}" created! Placeholder seed leagues have been removed.`);
        const newLid = data.league.id;
        loadLeagues(newLid);
        setTimeout(() => {
          setCreateModalOpen(false);
          setCreateSuccessMsg(null);
          setCreateName("");
        }, 1800);
      })
      .catch((err) => {
        setCreateSubmitting(false);
        setCreateError(err.message);
      });
  };

  // 3. Fetch league chat stream
  const loadChat = () => {
    setChatLoading(true);
    fetch(`${API_BASE}/api/napper/league/${selectedLeagueId}/chat?limit=50`)
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data.messages)) {
          setChatMessages(data.messages);
        }
        setChatLoading(false);
      })
      .catch(() => setChatLoading(false));
  };

  useEffect(() => {
    loadChat();
  }, [selectedLeagueId]);

  // 4. Send chat message
  const handleSendMessage = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!inputText.trim()) return;

    const textToSend = inputText.trim();
    setInputText("");

    fetch(`${API_BASE}/api/napper/league/${selectedLeagueId}/chat/send`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sender_type: "USER",
        sender_name: profile?.display_name || "jrereyi2000",
        sender_handle: profile?.handle || "jrereyi2000",
        message_text: textToSend,
      }),
    })
      .then((res) => res.json())
      .then(() => {
        loadChat();
      })
      .catch(() => {
        // Optimistic append fallback
        setChatMessages((prev) => [
          ...prev,
          {
            id: Date.now(),
            league_id: selectedLeagueId,
            sender_type: "USER",
            sender_name: profile?.display_name || "jrereyi2000",
            sender_handle: profile?.handle || "jrereyi2000",
            avatar_url: "",
            message_text: textToSend,
            created_at: new Date().toISOString(),
          },
        ]);
      });
  };

  const currentLeague = leagues.find((l) => l.id === selectedLeagueId) || leagues[0] || FALLBACK_LEAGUES[0];

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
          <div
            onClick={() => setActiveTab("TRADES")}
            className="flex items-center justify-between px-3 py-2 rounded-xl text-xs font-bold text-[#8a99ad] hover:text-white hover:bg-[#182333] transition-colors cursor-pointer group"
          >
            <div className="flex items-center gap-3">
              <div className="relative">
                <svg className="w-4 h-4 text-[#8a99ad] group-hover:text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
                {pendingTradesCount > 0 && (
                  <span className="absolute -top-1.5 -right-2 bg-[#00ceb8] text-[#0d1722] text-[9px] font-black w-4 h-4 rounded-full flex items-center justify-center animate-pulse">
                    {pendingTradesCount}
                  </span>
                )}
              </div>
              <span>Trade Proposals</span>
            </div>
            {pendingTradesCount > 0 && (
              <span className="text-[10px] font-bold text-[#00ceb8] bg-[#00ceb8]/10 px-1.5 py-0.5 rounded">
                {pendingTradesCount} new
              </span>
            )}
          </div>

          <div
            onClick={() => setActiveTab("PLAYERS")}
            className="flex items-center justify-between px-3 py-2 rounded-xl text-xs font-bold text-[#8a99ad] hover:text-white hover:bg-[#182333] transition-colors cursor-pointer group"
          >
            <div className="flex items-center gap-3">
              <svg className="w-4 h-4 text-[#8a99ad] group-hover:text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" />
                <polyline points="22,6 12,13 2,6" />
              </svg>
              <span>Waivers & Agency</span>
            </div>
          </div>
        </div>

        {/* Leagues Header */}
        <div className="px-5 pt-3 pb-1 flex items-center justify-between">
          <span className="text-[10px] font-extrabold uppercase tracking-wider text-[#5c6c80]">
            Leagues ({leagues.length})
          </span>
          <button
            type="button"
            onClick={() => {
              setModalMode("CREATE");
              setCreateError(null);
              setCreateModalOpen(true);
            }}
            title="Create New League"
            className="w-5 h-5 rounded-full border border-[#283850] bg-[#172130] hover:border-[#00ceb8] hover:text-[#00ceb8] flex items-center justify-center text-xs text-[#8a99ad] cursor-pointer transition-colors"
          >
            +
          </button>
        </div>

        {/* Scrollable League List */}
        <div className="napper-left-rail-scroll px-3 py-1 space-y-1.5 flex-1 overflow-y-auto">
          {leagues.map((lg) => {
            const isSelected = selectedLeagueId === lg.id;
            const isPreDraft = lg.status === "PRE_DRAFT";

            return (
              <div
                key={lg.id}
                onClick={() => setSelectedLeagueId(lg.id)}
                className={`p-2.5 rounded-xl transition-all cursor-pointer border ${
                  isSelected
                    ? "bg-[#182436] border-[#00ceb8]/50 shadow-[0_0_12px_rgba(0,206,184,0.15)]"
                    : "bg-transparent border-transparent hover:bg-[#16202e]"
                }`}
              >
                <div className="flex items-start gap-2.5">
                  <div
                    className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 shadow-sm ${
                      lg.id === 1
                        ? "bg-gradient-to-br from-[#00ceb8] to-[#0ea5e9] text-[#0d1722]"
                        : lg.id === 2
                        ? "bg-gradient-to-br from-[#3b82f6] to-[#1d4ed8] text-white"
                        : "bg-gradient-to-br from-[#ea580c] to-[#b91c1c] text-white font-black text-xs"
                    }`}
                  >
                    {lg.id === 1 ? (
                      <NapperMoonIcon size={20} />
                    ) : (
                      <span>{lg.name.charAt(0)}</span>
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-bold text-white truncate">{lg.name}</div>
                    <div className="text-[10px] text-[#8a99ad] truncate">
                      {isPreDraft ? (
                        <span className="text-[#38bdf8] font-bold uppercase tracking-wider text-[9px]">
                          • PRE DRAFT ({lg.teams_count}T)
                        </span>
                      ) : (
                        <span>
                          {lg.user_team_name || "My Team"}{" "}
                          <span className="text-[#5c6c80]">vs.</span>{" "}
                          {lg.opponent_team_name || "Opponent"}
                        </span>
                      )}
                    </div>
                    {!isPreDraft && (
                      <div className="flex items-center justify-between text-[10px] font-mono mt-0.5">
                        <span className="text-white font-semibold">
                          {(lg.user_score ?? 0).toFixed(1)} pro: {(lg.user_proj ?? 0).toFixed(1)}
                        </span>
                        <span className="text-[#00ceb8] font-bold">
                          {(lg.opponent_score ?? 0).toFixed(1)}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {/* User Profile Footer */}
        <div className="p-3 border-t border-[#1e2a3c] bg-[#0e1520] flex items-center justify-between">
          <div className="flex items-center gap-2.5 min-w-0">
            <BlueRobotAvatar size={34} />
            <div className="min-w-0">
              <div className="text-xs font-bold text-white truncate">
                {profile?.handle || "jrereyi2000"}
              </div>
              <div className="flex items-center gap-1.5 text-[10px] text-[#8a99ad]">
                <span className="text-[#f59e0b]">🏆</span>
                <span className="font-mono font-bold text-[#cbd5e1]">
                  {profile?.trophies ?? 3}
                </span>
                <span className="text-[#5c6c80]">•</span>
                <span className="text-[#00ceb8] text-[9px] font-semibold">Online</span>
              </div>
            </div>
          </div>
          <button
            type="button"
            title="User Profile Settings & League Switcher"
            onClick={() => {
              setModalMode("SWITCH");
              setCreateModalOpen(true);
            }}
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
          Houses League Header, Nav Tab Pills, and Interactive Tabs
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
                  {currentLeague.name}
                </h1>
                <span className="text-xs text-[#8a99ad] font-medium">
                  {currentLeague.season} • {currentLeague.teams_count}-Team {currentLeague.scoring_type}
                </span>
                <span className="text-[10px] font-bold text-[#00ceb8] bg-[#00ceb8]/15 px-2 py-0.5 rounded-full border border-[#00ceb8]/30">
                  {currentLeague.status}
                </span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setActiveTab("LEAGUE")}
              title="League Standings & Bracket"
              className="px-3 py-1.5 rounded-xl bg-[#1a2332] hover:bg-[#202d40] border border-[#283850] text-xs font-semibold text-[#8a99ad] hover:text-white transition-colors cursor-pointer"
            >
              Standings
            </button>
            <button
              type="button"
              onClick={() => setCreateModalOpen(true)}
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

          {activeTab === "LEAGUE" && (
            <NapperLeagueTab leagueId={selectedLeagueId} />
          )}

          {activeTab === "PLAYERS" && (
            <NapperPlayersTab leagueId={selectedLeagueId} />
          )}

          {activeTab === "TRADES" && (
            <NapperTradesTab leagueId={selectedLeagueId} />
          )}

          {activeTab === "SCORES" && (
            <NapperScoresTab leagueId={selectedLeagueId} />
          )}

          {activeTab === "TREND" && (
            <div className="max-w-4xl mx-auto px-6 py-6 space-y-6">
              <div className="bg-[#1a2332] border border-[#283850] rounded-2xl p-5">
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-9 h-9 rounded-xl bg-[#00ceb8]/15 border border-[#00ceb8]/30 flex items-center justify-center text-[#00ceb8]">
                    <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
                      <polyline points="17 6 23 6 23 12" />
                    </svg>
                  </div>
                  <div>
                    <h3 className="text-base font-extrabold text-white">Market Intelligence & Waiver Heatmap</h3>
                    <p className="text-xs text-[#8a99ad]">Most added and trending free agents across Napper leagues over the last 48 hours.</p>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className="bg-[#141d2b] border border-[#223145] rounded-xl p-4">
                    <div className="text-xs font-bold uppercase tracking-wider text-[#00ceb8] mb-3 flex items-center justify-between">
                      <span>🔥 Top Added Players</span>
                      <span className="text-[10px] text-[#8a99ad]">Net adds</span>
                    </div>
                    <div className="space-y-2.5">
                      {[
                        { name: "Kenneth Walker III", team: "SEA", pos: "RB", adds: "+42.5%", fpts: 22.8 },
                        { name: "Jaxon Smith-Njigba", team: "SEA", pos: "WR", adds: "+31.2%", fpts: 15.6 },
                        { name: "David Montgomery", team: "DET", pos: "RB", adds: "+18.9%", fpts: 13.9 },
                        { name: "Sam LaPorta", team: "DET", pos: "TE", adds: "+14.0%", fpts: 12.8 },
                      ].map((p, idx) => (
                        <div key={idx} className="flex items-center justify-between text-xs">
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] font-mono text-[#5c6c80] w-3">{idx + 1}</span>
                            <span className="font-bold text-white">{p.name}</span>
                            <span className="text-[10px] text-[#8a99ad]">{p.pos} • {p.team}</span>
                          </div>
                          <div className="flex items-center gap-3">
                            <span className="font-mono text-emerald-400 text-[11px] font-semibold">{p.adds}</span>
                            <span className="font-mono text-[#cbd5e1] text-[11px]">{p.fpts} pts</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="bg-[#141d2b] border border-[#223145] rounded-xl p-4">
                    <div className="text-xs font-bold uppercase tracking-wider text-[#ff4458] mb-3 flex items-center justify-between">
                      <span>❄️ Most Dropped Players</span>
                      <span className="text-[10px] text-[#8a99ad]">Net drops</span>
                    </div>
                    <div className="space-y-2.5">
                      {[
                        { name: "Dallas Goedert", team: "PHI", pos: "TE", drops: "-12.4%", fpts: 8.7 },
                        { name: "Tyler Bass", team: "BUF", pos: "K", drops: "-15.2%", fpts: 8.0 },
                        { name: "Brock Purdy", team: "SF", pos: "QB", drops: "-8.1%", fpts: 17.1 },
                        { name: "Baker Mayfield", team: "TB", pos: "QB", drops: "-6.5%", fpts: 16.0 },
                      ].map((p, idx) => (
                        <div key={idx} className="flex items-center justify-between text-xs">
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] font-mono text-[#5c6c80] w-3">{idx + 1}</span>
                            <span className="font-bold text-white">{p.name}</span>
                            <span className="text-[10px] text-[#8a99ad]">{p.pos} • {p.team}</span>
                          </div>
                          <div className="flex items-center gap-3">
                            <span className="font-mono text-rose-400 text-[11px] font-semibold">{p.drops}</span>
                            <span className="font-mono text-[#cbd5e1] text-[11px]">{p.fpts} pts</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="mt-5 pt-4 border-t border-[#223145] flex items-center justify-between">
                  <span className="text-xs text-[#8a99ad]">Want to claim available talent? Jump directly to waivers.</span>
                  <button
                    type="button"
                    onClick={() => setActiveTab("PLAYERS")}
                    className="px-4 py-2 rounded-xl bg-[#00ceb8] text-[#0d1722] font-bold text-xs hover:bg-[#2bd9c6] transition-colors cursor-pointer"
                  >
                    Open Waiver Wire
                  </button>
                </div>
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
          <div className="flex items-center gap-2">
            <h2 className="text-base font-extrabold text-white tracking-wide">
              League Chat
            </h2>
            <span className="text-[10px] font-mono text-[#00ceb8] bg-[#00ceb8]/15 px-1.5 py-0.5 rounded font-bold">
              Lg #{selectedLeagueId}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={loadChat}
              title="Refresh Chat Stream"
              className="w-7 h-7 rounded-lg bg-[#172130] hover:bg-[#202d40] border border-[#283850] flex items-center justify-center text-[#8a99ad] hover:text-white transition-colors cursor-pointer"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="23 4 23 10 17 10" />
                <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
              </svg>
            </button>
          </div>
        </div>

        {/* Chat Feed (Scrollable) */}
        <div className="flex-1 overflow-y-auto p-3 space-y-3 scrollbar-thin">
          {chatLoading && chatMessages.length === 0 ? (
            <div className="p-6 text-center text-xs text-[#8a99ad]">
              Connecting to league stream...
            </div>
          ) : chatMessages.length === 0 ? (
            <div className="p-6 text-center text-xs text-[#8a99ad]">
              No messages yet in this league. Break the ice below!
            </div>
          ) : (
            chatMessages.map((msg) => {
              const card = msg.card_payload;

              // Benchwarmer Award Card
              if (card && (card.type === "BENCHWARMER" || card.award_type === "BENCHWARMER")) {
                const p = card.player || {
                  name: card.player_name || "Aaron Jones",
                  position: "RB",
                  team: "MIN",
                  stats: "Top scorer on bench",
                  fpts: card.points || 18.2,
                  headshot_url: "",
                };
                return (
                  <div key={msg.id} className="napper-chat-card-benchwarmer">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[11px] font-extrabold uppercase tracking-wider text-[#cbd5e1]">
                        {card.title || "BENCHWARMER OF THE WEEK"}
                      </span>
                      <span className="text-base font-serif text-[#8a99ad] leading-none">”</span>
                    </div>

                    <div className="text-[11px] text-[#8a99ad] mb-2 leading-relaxed">
                      {card.subtitle || msg.message_text}
                    </div>

                    <div className="napper-chat-inner-player">
                      <img
                        src={getPlayerHeadshotUrl(p.name, p.headshot_url)}
                        alt={p.name}
                        onError={handleImageError}
                        className="w-10 h-10 rounded-full object-cover border border-[#283850] shrink-0"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="text-xs font-bold text-white truncate">
                          {p.name}
                        </div>
                        <div className="text-[10px] text-[#8a99ad] font-semibold">
                          {p.position} - {p.team}
                        </div>
                        <div className="text-[9px] text-[#8a99ad] truncate mt-0.5 font-mono">
                          {p.stats}
                        </div>
                      </div>
                      <div className="text-right pl-1 shrink-0">
                        <div className="text-base font-extrabold text-white font-mono">
                          {typeof p.fpts === "number" ? p.fpts.toFixed(1) : p.fpts}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              }

              // Trade Alert Card
              if (card && (card.type === "TRADE_ALERT" || card.award_type === "TRADE_ALERT")) {
                return (
                  <div key={msg.id} className="bg-[#1e2333] border border-[#3b82f6]/40 rounded-xl p-3 shadow-md">
                    <div className="flex items-center gap-1.5 text-xs font-extrabold text-[#38bdf8] mb-1">
                      <span>🚨</span>
                      <span>{card.title || "TRADE ALERT"}</span>
                    </div>
                    <div className="text-xs text-white font-bold mb-1">
                      {card.subtitle || msg.message_text}
                    </div>
                    {card.details && (
                      <div className="text-[11px] text-[#8a99ad] space-y-1 bg-[#141926] p-2 rounded-lg mt-1 font-mono">
                        {card.details.offered && <div>⇄ Gives: <span className="text-white">{card.details.offered}</span></div>}
                        {card.details.requested && <div>⇄ Gets: <span className="text-white">{card.details.requested}</span></div>}
                        {card.details.rationale && <div className="text-[10px] italic text-[#94a3b8] mt-1 font-sans">"{card.details.rationale}"</div>}
                      </div>
                    )}
                  </div>
                );
              }

              // Waiver Claim Card
              if (card && card.type === "WAIVER_CLAIM") {
                return (
                  <div key={msg.id} className="bg-[#172533] border border-[#00ceb8]/40 rounded-xl p-3 shadow-sm">
                    <div className="flex items-center gap-1.5 text-[11px] font-extrabold text-[#00ceb8] mb-1">
                      <span>⚡</span>
                      <span>WAIVER WIRE TRANSACTION</span>
                    </div>
                    <p className="text-xs text-white font-medium leading-relaxed">
                      {msg.message_text}
                    </p>
                  </div>
                );
              }

              // Standard User or AI manager chat bubble
              const isUser = msg.sender_type === "USER";
              return (
                <div key={msg.id} className="bg-[#172130] border border-[#243449] rounded-xl p-3">
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-1.5">
                      {isUser ? <BlueRobotAvatar size={18} /> : <PinkRobotAvatar size={18} />}
                      <span className="text-xs font-bold text-white">
                        {msg.sender_handle || msg.sender_name}
                      </span>
                    </div>
                    <span className="text-[9px] text-[#5c6c80]">
                      {msg.created_at ? new Date(msg.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}
                    </span>
                  </div>
                  <p className="text-xs text-[#cbd5e1] leading-relaxed mt-1">
                    {msg.message_text}
                  </p>
                </div>
              );
            })
          )}
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
            onClick={() => {
              setInputText((prev) => prev + " 🏈");
            }}
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
            onClick={() => {
              setInputText((prev) => prev + " 🔥");
            }}
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

      {/* =========================================================================
          MODAL: CREATE / JOIN LEAGUE MODAL
          ========================================================================= */}
      {createModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4">
          <div className="bg-[#172233] border border-[#283850] rounded-2xl max-w-lg w-full p-6 shadow-2xl space-y-4 max-h-[90vh] overflow-y-auto">
            {/* Modal Header */}
            <div className="flex items-center justify-between border-b border-[#223147] pb-3">
              <div className="flex items-center gap-2">
                <NapperMoonIcon size={22} />
                <h3 className="text-base font-extrabold text-white">Napper Fantasy Leagues</h3>
              </div>
              <button
                type="button"
                onClick={() => setCreateModalOpen(false)}
                className="text-[#8a99ad] hover:text-white text-lg font-bold"
              >
                ✕
              </button>
            </div>

            {/* Mode Switcher Tabs */}
            <div className="flex bg-[#111827] p-1 rounded-xl border border-[#223147]">
              <button
                type="button"
                onClick={() => setModalMode("CREATE")}
                className={`flex-1 py-1.5 text-xs font-bold rounded-lg transition-colors cursor-pointer ${
                  modalMode === "CREATE"
                    ? "bg-[#00ceb8] text-[#0d1722]"
                    : "text-[#8a99ad] hover:text-white"
                }`}
              >
                + Create League
              </button>
              <button
                type="button"
                onClick={() => setModalMode("SWITCH")}
                className={`flex-1 py-1.5 text-xs font-bold rounded-lg transition-colors cursor-pointer ${
                  modalMode === "SWITCH"
                    ? "bg-[#00ceb8] text-[#0d1722]"
                    : "text-[#8a99ad] hover:text-white"
                }`}
              >
                Switch League ({leagues.length})
              </button>
            </div>

            {createError && (
              <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs font-semibold">
                {createError}
              </div>
            )}

            {createSuccessMsg && (
              <div className="p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-semibold">
                {createSuccessMsg}
              </div>
            )}

            {modalMode === "CREATE" ? (
              <form onSubmit={handleCreateLeague} className="space-y-4">
                {/* Seed removal notice */}
                <div className="p-3 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-300 text-[11px] leading-relaxed">
                  💡 <b>Auto-Cleanup:</b> Creating a new league will permanently remove all initial placeholder seed leagues, leaving only your active leagues.
                </div>

                {/* League Name */}
                <div>
                  <label className="block text-xs font-bold text-[#8a99ad] uppercase tracking-wider mb-1.5">
                    League Name
                  </label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. Grand Line Dynasty, All-Mighty 10..."
                    value={createName}
                    onChange={(e) => setCreateName(e.target.value)}
                    className="w-full bg-[#111827] border border-[#283850] rounded-xl px-3.5 py-2.5 text-sm text-white placeholder-[#5c6c80] focus:outline-none focus:border-[#00ceb8]"
                  />
                </div>

                {/* Teams Count */}
                <div>
                  <label className="block text-xs font-bold text-[#8a99ad] uppercase tracking-wider mb-1.5">
                    Number of Teams
                  </label>
                  <div className="grid grid-cols-3 gap-2">
                    {[8, 10, 12].map((cnt) => (
                      <button
                        key={cnt}
                        type="button"
                        onClick={() => setCreateTeamsCount(cnt)}
                        className={`py-2 text-xs font-bold rounded-xl border transition-colors cursor-pointer ${
                          createTeamsCount === cnt
                            ? "bg-[#00ceb8]/15 border-[#00ceb8] text-[#00ceb8]"
                            : "bg-[#121926] border-[#223147] text-[#8a99ad] hover:text-white"
                        }`}
                      >
                        {cnt} Teams
                      </button>
                    ))}
                  </div>
                </div>

                {/* Scoring Type */}
                <div>
                  <label className="block text-xs font-bold text-[#8a99ad] uppercase tracking-wider mb-1.5">
                    Scoring Format
                  </label>
                  <div className="grid grid-cols-3 gap-2">
                    {[
                      { id: "PPR", label: "Full PPR" },
                      { id: "HALF_PPR", label: "0.5 PPR" },
                      { id: "STANDARD", label: "Standard" },
                    ].map((sc) => (
                      <button
                        key={sc.id}
                        type="button"
                        onClick={() => setCreateScoringType(sc.id)}
                        className={`py-2 text-xs font-bold rounded-xl border transition-colors cursor-pointer ${
                          createScoringType === sc.id
                            ? "bg-[#00ceb8]/15 border-[#00ceb8] text-[#00ceb8]"
                            : "bg-[#121926] border-[#223147] text-[#8a99ad] hover:text-white"
                        }`}
                      >
                        {sc.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Superflex / 1QB */}
                <div>
                  <label className="block text-xs font-bold text-[#8a99ad] uppercase tracking-wider mb-1.5">
                    Roster Composition
                  </label>
                  <div className="grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      onClick={() => setCreateSuperflex(true)}
                      className={`py-2 text-xs font-bold rounded-xl border transition-colors cursor-pointer ${
                        createSuperflex
                          ? "bg-[#00ceb8]/15 border-[#00ceb8] text-[#00ceb8]"
                          : "bg-[#121926] border-[#223147] text-[#8a99ad] hover:text-white"
                      }`}
                    >
                      Superflex (2QB Capable)
                    </button>
                    <button
                      type="button"
                      onClick={() => setCreateSuperflex(false)}
                      className={`py-2 text-xs font-bold rounded-xl border transition-colors cursor-pointer ${
                        !createSuperflex
                          ? "bg-[#00ceb8]/15 border-[#00ceb8] text-[#00ceb8]"
                          : "bg-[#121926] border-[#223147] text-[#8a99ad] hover:text-white"
                      }`}
                    >
                      Standard 1QB
                    </button>
                  </div>
                </div>

                {/* Starting Phase */}
                <div>
                  <label className="block text-xs font-bold text-[#8a99ad] uppercase tracking-wider mb-1.5">
                    Initial Phase
                  </label>
                  <div className="grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      onClick={() => setCreateStatus("IN_SEASON")}
                      className={`py-2 text-xs font-bold rounded-xl border transition-colors cursor-pointer ${
                        createStatus === "IN_SEASON"
                          ? "bg-[#00ceb8]/15 border-[#00ceb8] text-[#00ceb8]"
                          : "bg-[#121926] border-[#223147] text-[#8a99ad] hover:text-white"
                      }`}
                    >
                      In-Season (Active Week 6)
                    </button>
                    <button
                      type="button"
                      onClick={() => setCreateStatus("PRE_DRAFT")}
                      className={`py-2 text-xs font-bold rounded-xl border transition-colors cursor-pointer ${
                        createStatus === "PRE_DRAFT"
                          ? "bg-[#38bdf8]/15 border-[#38bdf8] text-[#38bdf8]"
                          : "bg-[#121926] border-[#223147] text-[#8a99ad] hover:text-white"
                      }`}
                    >
                      Pre-Draft (Draft Room)
                    </button>
                  </div>
                </div>

                {/* Submit buttons */}
                <div className="pt-3 border-t border-[#223147] flex justify-end gap-2.5">
                  <button
                    type="button"
                    onClick={() => setCreateModalOpen(false)}
                    className="px-4 py-2 rounded-xl bg-[#1f2d42] text-white hover:bg-[#283850] text-xs font-bold transition-colors cursor-pointer"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={createSubmitting}
                    className="px-5 py-2 rounded-xl bg-[#00ceb8] hover:bg-[#2bd9c6] text-[#0d1722] text-xs font-extrabold transition-colors cursor-pointer disabled:opacity-60"
                  >
                    {createSubmitting ? "Creating League..." : "Create League"}
                  </button>
                </div>
              </form>
            ) : (
              <div className="space-y-3">
                <p className="text-xs text-[#8a99ad]">
                  Select an active league to view its matchup, roster, standings, and trades:
                </p>
                <div className="space-y-2 max-h-60 overflow-y-auto">
                  {leagues.map((lg) => (
                    <div
                      key={lg.id}
                      onClick={() => {
                        setSelectedLeagueId(lg.id);
                        setCreateModalOpen(false);
                      }}
                      className={`p-3 rounded-xl border flex items-center justify-between cursor-pointer transition-colors ${
                        selectedLeagueId === lg.id
                          ? "bg-[#1f2d42] border-[#00ceb8]"
                          : "bg-[#121926] border-[#223147] hover:border-[#38bdf8]"
                      }`}
                    >
                      <div>
                        <div className="text-xs font-bold text-white">{lg.name}</div>
                        <div className="text-[10px] text-[#8a99ad]">
                          {lg.teams_count} Teams • {lg.scoring_type} • {lg.status}
                        </div>
                      </div>
                      <button
                        type="button"
                        className="px-3 py-1 rounded-lg bg-[#00ceb8] text-[#0d1722] text-xs font-bold"
                      >
                        Select
                      </button>
                    </div>
                  ))}
                </div>
                <div className="pt-2 border-t border-[#223147] flex justify-end">
                  <button
                    type="button"
                    onClick={() => setCreateModalOpen(false)}
                    className="px-4 py-2 rounded-xl bg-[#1f2d42] text-white hover:bg-[#283850] text-xs font-bold transition-colors cursor-pointer"
                  >
                    Close
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default NapperPage;
