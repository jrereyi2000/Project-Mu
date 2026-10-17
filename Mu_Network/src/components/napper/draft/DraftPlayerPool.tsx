import React, { useState, useEffect, useRef, useMemo } from "react";
import type { NapperDraftPlayer } from "../../../types/napper";
import { getPlayerHeadshotUrl, handleImageError } from "../napperUtils";

interface DraftPlayerPoolProps {
  players: NapperDraftPlayer[];
  userRoster: NapperDraftPlayer[];
  queueIds: number[];
  isUserOnClock: boolean;
  onDraftPlayer: (player: NapperDraftPlayer) => void;
  onToggleQueue: (player: NapperDraftPlayer) => void;
  onSelectPlayerNotes?: (player: NapperDraftPlayer) => void;
}

export const DraftPlayerPool: React.FC<DraftPlayerPoolProps> = ({
  players,
  userRoster,
  queueIds,
  isUserOnClock,
  onDraftPlayer,
  onToggleQueue,
  onSelectPlayerNotes,
}) => {
  const [search, setSearch] = useState("");
  const [selectedPos, setSelectedPos] = useState<string>("ALL");
  const [showDrafted, setShowDrafted] = useState(false);
  const [showQueuedOnly, setShowQueuedOnly] = useState(false);
  const [selectedSeason, setSelectedSeason] = useState("'26");
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Keyboard shortcut: Cmd+U or Ctrl+U focuses search bar
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "u") {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Compute position counts for the user's current roster
  const counts = useMemo(() => {
    const qb = userRoster.filter((p) => p.position === "QB").length;
    const rb = userRoster.filter((p) => p.position === "RB").length;
    const wr = userRoster.filter((p) => p.position === "WR").length;
    const te = userRoster.filter((p) => p.position === "TE").length;
    const flex = Math.max(0, rb - 2) + Math.max(0, wr - 2) + Math.max(0, te - 1);
    const k = userRoster.filter((p) => p.position === "K").length;
    const def = userRoster.filter((p) => p.position === "DEF").length;
    const total = userRoster.length;
    return {
      total: `${total}/15`,
      qb: `${Math.min(qb, 1)}/1`,
      rb: `${Math.min(rb, 2)}/2`,
      wr: `${Math.min(wr, 2)}/2`,
      te: `${Math.min(te, 1)}/1`,
      flex: `${Math.min(flex, 2)}/2`,
      k: `${Math.min(k, 1)}/1`,
      def: `${Math.min(def, 1)}/1`,
    };
  }, [userRoster]);

  // Filter player list
  const filteredPlayers = useMemo(() => {
    return players.filter((p) => {
      // Drafted filter
      if (!showDrafted && p.is_drafted) return false;

      // Queue filter
      if (showQueuedOnly && !queueIds.includes(p.player_id)) return false;

      // Positional filter
      if (selectedPos !== "ALL") {
        if (selectedPos === "FLEX") {
          if (!["RB", "WR", "TE"].includes(p.position)) return false;
        } else if (p.position !== selectedPos) {
          return false;
        }
      }

      // Search filter
      if (search.trim()) {
        const q = search.toLowerCase();
        const matchesName = p.name.toLowerCase().includes(q);
        const matchesTeam = p.team.toLowerCase().includes(q);
        const matchesPos = p.position.toLowerCase().includes(q);
        if (!matchesName && !matchesTeam && !matchesPos) return false;
      }

      return true;
    });
  }, [players, showDrafted, showQueuedOnly, selectedPos, search, queueIds]);

  return (
    <div className="draft-player-pool-panel">
      {/* Top Toolbar: Search, Position Filter Pills, Season, Toggles */}
      <div className="draft-pool-toolbar">
        {/* Search Input with ⌘ U badge */}
        <div className="draft-search-box">
          <svg
            className="w-3.5 h-3.5 text-[#64748b] shrink-0"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            ref={searchInputRef}
            type="text"
            placeholder="Search Player"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <span className="draft-kbd-shortcut">⌘ U</span>
        </div>

        {/* Position Pills with (drafted/required) counts */}
        <div className="draft-pos-pills">
          <button
            type="button"
            onClick={() => {
              setSelectedPos("ALL");
              setShowQueuedOnly(false);
            }}
            className={`draft-pos-pill ${selectedPos === "ALL" && !showQueuedOnly ? "active" : ""}`}
          >
            ALL <span className="opacity-70 font-normal">({counts.total})</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setSelectedPos("QB");
              setShowQueuedOnly(false);
            }}
            className={`draft-pos-pill ${selectedPos === "QB" && !showQueuedOnly ? "active" : ""}`}
          >
            QB <span className="opacity-70 font-normal">({counts.qb})</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setSelectedPos("RB");
              setShowQueuedOnly(false);
            }}
            className={`draft-pos-pill ${selectedPos === "RB" && !showQueuedOnly ? "active" : ""}`}
          >
            RB <span className="opacity-70 font-normal">({counts.rb})</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setSelectedPos("WR");
              setShowQueuedOnly(false);
            }}
            className={`draft-pos-pill ${selectedPos === "WR" && !showQueuedOnly ? "active" : ""}`}
          >
            WR <span className="opacity-70 font-normal">({counts.wr})</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setSelectedPos("TE");
              setShowQueuedOnly(false);
            }}
            className={`draft-pos-pill ${selectedPos === "TE" && !showQueuedOnly ? "active" : ""}`}
          >
            TE <span className="opacity-70 font-normal">({counts.te})</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setSelectedPos("FLEX");
              setShowQueuedOnly(false);
            }}
            className={`draft-pos-pill ${selectedPos === "FLEX" && !showQueuedOnly ? "active" : ""}`}
          >
            FLEX <span className="opacity-70 font-normal">({counts.flex})</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setSelectedPos("K");
              setShowQueuedOnly(false);
            }}
            className={`draft-pos-pill ${selectedPos === "K" && !showQueuedOnly ? "active" : ""}`}
          >
            K <span className="opacity-70 font-normal">({counts.k})</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setSelectedPos("DEF");
              setShowQueuedOnly(false);
            }}
            className={`draft-pos-pill ${selectedPos === "DEF" && !showQueuedOnly ? "active" : ""}`}
          >
            DEF <span className="opacity-70 font-normal">({counts.def})</span>
          </button>

          {/* Star Filter Pill for Queued Players */}
          <button
            type="button"
            onClick={() => setShowQueuedOnly(!showQueuedOnly)}
            className={`draft-pos-pill flex items-center gap-1 ${showQueuedOnly ? "active" : ""}`}
            title="Show Queued Players Only"
          >
            <svg
              className={`w-3 h-3 ${showQueuedOnly ? "fill-[#090e17]" : "fill-none stroke-current"}`}
              viewBox="0 0 24 24"
              strokeWidth="2"
            >
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
            </svg>
            <span>({queueIds.length})</span>
          </button>
        </div>

        {/* Right Toolbar Controls: Drafted Checkbox, Season, Compare, Sliders */}
        <div className="draft-pool-actions">
          <label className="flex items-center gap-1.5 text-[11px] font-bold text-[#8a99ad] cursor-pointer hover:text-white select-none">
            <input
              type="checkbox"
              checked={showDrafted}
              onChange={(e) => setShowDrafted(e.target.checked)}
              className="accent-[#00ceb8] rounded"
            />
            <span>Drafted</span>
          </label>

          {/* Season dropdown */}
          <select
            value={selectedSeason}
            onChange={(e) => setSelectedSeason(e.target.value)}
            className="bg-[#141f33] border border-[#20314d] text-white text-[11px] font-bold py-1 px-2 rounded-md outline-none cursor-pointer"
          >
            <option value="'26">'26</option>
            <option value="'25">'25</option>
          </select>

          {/* Compare icon button */}
          <button
            type="button"
            className="draft-pool-icon-btn"
            title="Compare Players"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M16 3h5v5" />
              <path d="M4 20L21 3" />
              <path d="M21 16v5h-5" />
              <path d="M15 15l6 6" />
              <path d="M4 4l5 5" />
            </svg>
          </button>

          {/* Sliders filter icon */}
          <button
            type="button"
            className="draft-pool-icon-btn"
            title="Advanced Filters"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="4" y1="21" x2="4" y2="14" />
              <line x1="4" y1="10" x2="4" y2="3" />
              <line x1="12" y1="21" x2="12" y2="12" />
              <line x1="12" y1="8" x2="12" y2="3" />
              <line x1="20" y1="21" x2="20" y2="16" />
              <line x1="20" y1="12" x2="20" y2="3" />
              <line x1="1" y1="14" x2="7" y2="14" />
              <line x1="9" y1="8" x2="15" y2="8" />
              <line x1="17" y1="16" x2="23" y2="16" />
            </svg>
          </button>
        </div>
      </div>

      {/* Main Player Table */}
      <div className="draft-pool-table-wrap">
        <table className="draft-pool-table">
          <thead>
            <tr>
              <th style={{ width: "70px" }}>DRAFT</th>
              <th style={{ width: "35px" }}>RK</th>
              <th style={{ minWidth: "190px" }}>PLAYER</th>
              <th style={{ width: "55px" }}>ADP</th>
              <th style={{ width: "40px" }}>BYE</th>
              <th style={{ width: "45px" }}>AGE</th>
              <th style={{ width: "55px" }}>ROS%</th>
              <th style={{ width: "55px" }} title="Projected Season Total Fantasy Points (PPR)">PTS</th>
              <th style={{ width: "45px" }} title="Projected Points Per Game (PPG)">AVG</th>
              <th style={{ width: "50px" }}>RUSH</th>
              <th style={{ width: "55px" }}>YDS</th>
              <th style={{ width: "40px" }}>TD</th>
              <th style={{ width: "45px" }}>REC</th>
              <th style={{ width: "55px" }}>YDS</th>
              <th style={{ width: "40px" }}>TD</th>
            </tr>
          </thead>
          <tbody>
            {filteredPlayers.length === 0 ? (
              <tr>
                <td colSpan={15} className="py-12 text-center text-[#64748b]">
                  No matching players found in draft pool.
                </td>
              </tr>
            ) : (
              filteredPlayers.map((player) => {
                const isQueued = queueIds.includes(player.player_id);
                const canDraft = isUserOnClock && !player.is_drafted;

                return (
                  <tr
                    key={player.player_id}
                    className={player.is_drafted ? "opacity-40" : ""}
                  >
                    {/* DRAFT Action Button */}
                    <td>
                      <button
                        type="button"
                        disabled={!canDraft}
                        onClick={() => onDraftPlayer(player)}
                        className={`draft-table-pick-btn ${canDraft ? "on-clock" : ""}`}
                        title={
                          player.is_drafted
                            ? "Already drafted"
                            : canDraft
                            ? "Draft this player now"
                            : "Wait for your turn to draft"
                        }
                      >
                        {player.is_drafted ? "TAKEN" : "DRAFT"}
                      </button>
                    </td>

                    {/* Rank */}
                    <td className="font-bold text-[#8a99ad]">{player.adp_rank}</td>

                    {/* Player Info with Headshot, Team Badge, Name, Subtitle, Star & Note */}
                    <td>
                      <div className="draft-player-cell-content">
                        <div className="draft-table-avatar-box">
                          <img
                            src={getPlayerHeadshotUrl(player.player_id, player.headshot_url)}
                            alt={player.name}
                            className="draft-table-avatar"
                            onError={handleImageError}
                          />
                          <div className="draft-table-team-badge">{player.team}</div>
                        </div>

                        <div className="draft-table-name-block">
                          <span className="draft-table-player-name">{player.name}</span>
                          <span className="draft-table-player-sub">
                            <span
                              className="font-bold"
                              style={{
                                color:
                                  player.position === "QB"
                                    ? "#f43f5e"
                                    : player.position === "RB"
                                    ? "#10b981"
                                    : player.position === "WR"
                                    ? "#06b6d4"
                                    : player.position === "TE"
                                    ? "#f59e0b"
                                    : "#8b5cf6",
                              }}
                            >
                              {player.position}
                            </span>
                            <span>•</span>
                            <span>{player.team}</span>
                            <span>({player.bye})</span>
                          </span>
                        </div>

                        {/* Star / Watchlist Toggle */}
                        <button
                          type="button"
                          onClick={() => onToggleQueue(player)}
                          title={isQueued ? "Remove from Queue" : "Add to Queue"}
                          className="text-[#64748b] hover:text-[#f59e0b] ml-1 p-1 transition-colors"
                        >
                          <svg
                            className={`w-3.5 h-3.5 ${
                              isQueued ? "fill-[#f59e0b] text-[#f59e0b]" : "fill-none stroke-current"
                            }`}
                            viewBox="0 0 24 24"
                            strokeWidth="2"
                          >
                            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
                          </svg>
                        </button>

                        {/* Player Notes Icon */}
                        <button
                          type="button"
                          onClick={() => onSelectPlayerNotes?.(player)}
                          title="View Scouting Notes"
                          className="w-5 h-5 rounded-md bg-[#1e2a3f] hover:bg-[#283854] text-[#818cf8] flex items-center justify-center p-0.5 transition-colors"
                        >
                          <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                            <polyline points="14 2 14 8 20 8" />
                            <line x1="16" y1="13" x2="8" y2="13" />
                            <line x1="16" y1="17" x2="8" y2="17" />
                            <polyline points="10 9 9 9 8 9" />
                          </svg>
                        </button>
                      </div>
                    </td>

                    {/* ADP */}
                    <td>
                      <span className="draft-adp-pill">{player.adp.toFixed(1)}</span>
                    </td>

                    {/* BYE */}
                    <td className="text-[#94a3b8]">{player.bye}</td>

                    {/* AGE */}
                    <td className="text-[#94a3b8]">{player.age.toFixed(1)}</td>

                    {/* ROS% */}
                    <td className="text-[#94a3b8]">{player.ros_pct.toFixed(1)}%</td>

                    {/* PROJ PTS */}
                    <td className="font-bold text-white">{player.proj_pts.toFixed(1)}</td>

                    {/* PROJ AVG */}
                    <td className="text-[#94a3b8]">{player.proj_avg.toFixed(1)}</td>

                    {/* RUSH ATT */}
                    <td className="text-[#94a3b8]">{(player.rush_att ?? 0).toFixed(1)}</td>

                    {/* RUSH YDS */}
                    <td className="text-[#94a3b8]">{(player.rush_yds ?? 0).toFixed(1)}</td>

                    {/* RUSH TD */}
                    <td className="text-[#94a3b8]">{(player.rush_td ?? 0).toFixed(1)}</td>

                    {/* REC */}
                    <td className="text-[#94a3b8]">{(player.rec ?? 0).toFixed(1)}</td>

                    {/* REC YDS */}
                    <td className="text-[#94a3b8]">{(player.rec_yds ?? 0).toFixed(1)}</td>

                    {/* REC TD */}
                    <td className="text-[#94a3b8]">{(player.rec_td ?? 0).toFixed(1)}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default DraftPlayerPool;
