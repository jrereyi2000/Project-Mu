import React, { useState, useMemo } from "react";
import type {
  NapperDraftPlayer,
  NapperDraftTeam,
  NapperDraftComment,
} from "../../../types/napper";
import { ARCHETYPE_METAS } from "../../../types/napper";

interface DraftRosterQueueProps {
  teams: NapperDraftTeam[];
  userSlot: number;
  queuedPlayers: NapperDraftPlayer[];
  comments: NapperDraftComment[];
  isUserOnClock: boolean;
  onDraftPlayer: (player: NapperDraftPlayer) => void;
  onSelectPlayer?: (player: NapperDraftPlayer) => void;
  onRemoveFromQueue: (playerId: number) => void;
  onMoveQueueItem: (index: number, direction: "UP" | "DOWN") => void;
  onSendChatMessage: (text: string) => void;
}

export const DraftRosterQueue: React.FC<DraftRosterQueueProps> = ({
  teams,
  userSlot,
  queuedPlayers,
  comments,
  isUserOnClock,
  onDraftPlayer,
  onSelectPlayer,
  onRemoveFromQueue,
  onMoveQueueItem,
  onSendChatMessage,
}) => {
  const [activeTab, setActiveTab] = useState<"ROSTER" | "QUEUE" | "CHAT">("ROSTER");
  const [selectedSlot, setSelectedSlot] = useState<number>(userSlot);
  const [chatInput, setChatInput] = useState("");

  // Keep selected slot in sync if userSlot changes initially
  const currentTeam = useMemo(() => {
    return teams.find((t) => t.slot === selectedSlot) || teams[0];
  }, [teams, selectedSlot]);

  // Map drafted players to standard Sleeper roster slots
  const mappedSlots = useMemo(() => {
    const roster = currentTeam ? [...currentTeam.roster] : [];
    const slotsConfig = [
      { slotName: "QB", match: ["QB"] },
      { slotName: "RB", match: ["RB"] },
      { slotName: "RB", match: ["RB"] },
      { slotName: "WR", match: ["WR"] },
      { slotName: "WR", match: ["WR"] },
      { slotName: "TE", match: ["TE"] },
      { slotName: "FLEX", match: ["RB", "WR", "TE"] },
      { slotName: "K", match: ["K"] },
      { slotName: "DEF", match: ["DEF"] },
      { slotName: "BN", match: ["*"] },
      { slotName: "BN", match: ["*"] },
      { slotName: "BN", match: ["*"] },
      { slotName: "BN", match: ["*"] },
      { slotName: "BN", match: ["*"] },
      { slotName: "BN", match: ["*"] },
    ];

    const usedPlayerIds = new Set<number>();
    const result: Array<{ slotName: string; player: NapperDraftPlayer | null }> = [];

    // First pass: fill specific starters
    for (const sc of slotsConfig) {
      if (sc.slotName === "BN" || sc.slotName === "FLEX") continue;
      const found = roster.find(
        (p) => !usedPlayerIds.has(p.player_id) && sc.match.includes(p.position)
      );
      if (found) {
        usedPlayerIds.add(found.player_id);
        result.push({ slotName: sc.slotName, player: found });
      } else {
        result.push({ slotName: sc.slotName, player: null });
      }
    }

    // Second pass: fill FLEX
    const flexConfig = slotsConfig.find((s) => s.slotName === "FLEX");
    if (flexConfig) {
      const found = roster.find(
        (p) => !usedPlayerIds.has(p.player_id) && flexConfig.match.includes(p.position)
      );
      if (found) {
        usedPlayerIds.add(found.player_id);
        // Insert at FLEX index (index 6)
        result.splice(6, 0, { slotName: "FLEX", player: found });
      } else {
        result.splice(6, 0, { slotName: "FLEX", player: null });
      }
    }

    // Third pass: bench slots
    const remaining = roster.filter((p) => !usedPlayerIds.has(p.player_id));
    for (let i = 0; i < 6; i++) {
      if (i < remaining.length) {
        result.push({ slotName: "BN", player: remaining[i] });
      } else {
        result.push({ slotName: "BN", player: null });
      }
    }

    return result;
  }, [currentTeam]);

  const handleSendChat = (e: React.FormEvent) => {
    e.preventDefault();
    if (!chatInput.trim()) return;
    onSendChatMessage(chatInput.trim());
    setChatInput("");
  };

  const getSlotColor = (slot: string) => {
    switch (slot) {
      case "QB":
        return "#f43f5e";
      case "RB":
        return "#10b981";
      case "WR":
        return "#06b6d4";
      case "TE":
        return "#f59e0b";
      case "FLEX":
        return "#3b82f6";
      case "K":
        return "#8b5cf6";
      case "DEF":
        return "#64748b";
      default:
        return "#334155";
    }
  };

  return (
    <div className="draft-roster-queue-panel">
      {/* 3 Top Tabs: ROSTER, QUEUE, CHAT */}
      <div className="draft-side-tabs">
        <button
          type="button"
          onClick={() => setActiveTab("ROSTER")}
          className={`draft-side-tab-btn ${activeTab === "ROSTER" ? "active" : ""}`}
        >
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <path d="M3 9h18" />
            <path d="M9 21V9" />
          </svg>
          <span>ROSTER</span>
        </button>

        <button
          type="button"
          onClick={() => setActiveTab("QUEUE")}
          className={`draft-side-tab-btn ${activeTab === "QUEUE" ? "active" : ""}`}
        >
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
          </svg>
          <span>QUEUE ({queuedPlayers.length})</span>
        </button>

        <button
          type="button"
          onClick={() => setActiveTab("CHAT")}
          className={`draft-side-tab-btn ${activeTab === "CHAT" ? "active" : ""}`}
        >
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          <span>CHAT</span>
        </button>
      </div>

      {/* Tab 1: ROSTER */}
      {activeTab === "ROSTER" && (
        <div className="draft-side-content">
          <div className="draft-roster-subbar">
            <span className="draft-roster-heading">DRAFTED PLAYERS</span>
            <select
              value={selectedSlot}
              onChange={(e) => setSelectedSlot(Number(e.target.value))}
              className="draft-team-select"
            >
              {teams.map((t) => {
                const persona = t.persona as any;
                const meta = persona && persona in ARCHETYPE_METAS ? (ARCHETYPE_METAS as any)[persona] : null;
                const archTag = t.slot === userSlot ? "(YOU)" : meta ? `[${meta.shortLabel}]` : "";
                return (
                  <option key={t.id} value={t.slot}>
                    {t.slot === userSlot ? `★ ${t.name} (My Team)` : `${t.name} ${archTag}`}
                  </option>
                );
              })}
            </select>
          </div>

          {currentTeam && (
            <div className="px-3 py-1.5 bg-[#090e17] border-b border-[#182333] flex items-center justify-between text-[11px]">
              <span className="text-[#8a99ad] font-bold">{currentTeam.manager_handle}</span>
              {currentTeam.slot === userSlot ? (
                <span className="px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-wider bg-[#00ceb8]/15 border border-[#00ceb8]/40 text-[#00ceb8]">
                  YOU
                </span>
              ) : currentTeam.persona && currentTeam.persona in ARCHETYPE_METAS ? (
                <span
                  className="px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-wider border"
                  style={{
                    color: (ARCHETYPE_METAS as any)[currentTeam.persona].color,
                    backgroundColor: (ARCHETYPE_METAS as any)[currentTeam.persona].bgColor,
                    borderColor: (ARCHETYPE_METAS as any)[currentTeam.persona].borderColor,
                  }}
                  title={(ARCHETYPE_METAS as any)[currentTeam.persona].tagline}
                >
                  {(ARCHETYPE_METAS as any)[currentTeam.persona].label}
                </span>
              ) : null}
            </div>
          )}

          <div className="draft-roster-list">
            {mappedSlots.map((item, idx) => {
              const bgBadge = getSlotColor(item.slotName);

              return (
                <div key={`slot-${idx}-${item.slotName}`} className="draft-slot-row">
                  <div className="draft-slot-left">
                    <div
                      className="draft-slot-tag"
                      style={{ backgroundColor: bgBadge }}
                    >
                      {item.slotName}
                    </div>

                    {item.player ? (
                      <button
                        type="button"
                        className="flex flex-col text-left cursor-pointer group"
                        onClick={() => onSelectPlayer?.(item.player!)}
                        title={`View ${item.player.name}`}
                      >
                        <span className="draft-slot-filled-name group-hover:text-[#00ceb8]">
                          {item.player.name}
                        </span>
                        <span className="draft-slot-filled-meta">
                          {item.player.team} • Bye {item.player.bye ?? "—"}
                        </span>
                      </button>
                    ) : (
                      <span className="draft-slot-empty-label">Empty</span>
                    )}
                  </div>

                  {item.player && (
                    <div className="draft-slot-filled-pts">
                      {item.player.proj_pts == null ? "—" : item.player.proj_pts.toFixed(1)} <span className="text-[9px] text-[#64748b]">pts</span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Tab 2: QUEUE */}
      {activeTab === "QUEUE" && (
        <div className="draft-side-content">
          <div className="draft-roster-subbar">
            <span className="draft-roster-heading">STARRED QUEUE</span>
            <span className="text-[10px] text-[#64748b]">
              Auto-pick drafts top queue player
            </span>
          </div>

          {queuedPlayers.length === 0 ? (
            <div className="p-8 text-center text-[#64748b] text-xs space-y-2">
              <svg className="w-8 h-8 mx-auto text-[#243550]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
              </svg>
              <p>Your queue is empty.</p>
              <p className="text-[10px] text-[#475569]">
                Star players in the draft pool on the left to queue them up.
              </p>
            </div>
          ) : (
            <div className="draft-queue-view">
              {queuedPlayers.map((player, idx) => (
                <div key={player.player_id} className="draft-queue-item">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <span className="text-[11px] font-black text-[#64748b] w-4">
                      {idx + 1}
                    </span>
                    <span
                      className="text-[9px] font-extrabold px-1.5 py-0.5 rounded text-white"
                      style={{ backgroundColor: getSlotColor(player.position) }}
                    >
                      {player.position}
                    </span>
                    <div className="min-w-0 truncate">
                      <button
                        type="button"
                        className="block text-left truncate cursor-pointer group"
                        onClick={() => onSelectPlayer?.(player)}
                        title={`View ${player.name}`}
                      >
                        <div className="text-xs font-bold text-white group-hover:text-[#00ceb8] truncate">
                          {player.name}
                        </div>
                        <div className="text-[10px] text-[#64748b]">
                          {player.team} • ADP {player.adp == null ? "—" : player.adp.toFixed(1)} • {player.proj_pts == null ? "—" : player.proj_pts.toFixed(1)} pts
                        </div>
                      </button>
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5 shrink-0">
                    {/* Draft Button if user on clock */}
                    {isUserOnClock && (
                      <button
                        type="button"
                        onClick={() => onDraftPlayer(player)}
                        className="px-2 py-1 rounded bg-[#00ceb8] hover:bg-[#2bd9c6] text-[#090e17] text-[10px] font-black uppercase tracking-wider"
                      >
                        Draft
                      </button>
                    )}

                    {/* Move Up */}
                    <button
                      type="button"
                      disabled={idx === 0}
                      onClick={() => onMoveQueueItem(idx, "UP")}
                      className="p-1 rounded bg-[#1c2940] hover:bg-[#263756] text-[#94a3b8] disabled:opacity-30 disabled:cursor-not-allowed"
                      title="Move Up"
                    >
                      <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                        <polyline points="18 15 12 9 6 15" />
                      </svg>
                    </button>

                    {/* Move Down */}
                    <button
                      type="button"
                      disabled={idx === queuedPlayers.length - 1}
                      onClick={() => onMoveQueueItem(idx, "DOWN")}
                      className="p-1 rounded bg-[#1c2940] hover:bg-[#263756] text-[#94a3b8] disabled:opacity-30 disabled:cursor-not-allowed"
                      title="Move Down"
                    >
                      <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                        <polyline points="6 9 12 15 18 9" />
                      </svg>
                    </button>

                    {/* Remove */}
                    <button
                      type="button"
                      onClick={() => onRemoveFromQueue(player.player_id)}
                      className="p-1 rounded bg-[#1c2940] hover:bg-[#3b1824] text-[#94a3b8] hover:text-[#f43f5e]"
                      title="Remove from Queue"
                    >
                      <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                        <line x1="18" y1="6" x2="6" y2="18" />
                        <line x1="6" y1="6" x2="18" y2="18" />
                      </svg>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Tab 3: CHAT */}
      {activeTab === "CHAT" && (
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="draft-chat-feed">
            {comments.map((c) => (
              <div key={c.id} className="draft-chat-msg">
                <div className="draft-chat-author">
                  <span>{c.sender_name}</span>
                  <span className="text-[#64748b] text-[9px] font-normal">
                    {c.sender_handle}
                  </span>
                  <span className="text-[#475569] text-[9px] font-normal ml-auto">
                    {c.timestamp}
                  </span>
                </div>

                {c.pick_badge && (
                  <div className="flex items-center gap-2 my-1 px-2 py-1 rounded bg-[#090e17] border border-[#1f2d45]">
                    <span className="text-[9px] font-mono font-bold text-[#00ceb8]">
                      {c.pick_badge.pick_num}
                    </span>
                    <span
                      className="text-[8px] font-black px-1.5 rounded text-white"
                      style={{ backgroundColor: getSlotColor(c.pick_badge.pos) }}
                    >
                      {c.pick_badge.pos}
                    </span>
                    <span className="text-xs font-bold text-white">
                      {c.pick_badge.player_name}
                    </span>
                  </div>
                )}

                <div className="draft-chat-text">{c.message}</div>
              </div>
            ))}
          </div>

          <form onSubmit={handleSendChat} className="draft-chat-input-bar">
            <input
              type="text"
              placeholder="Send a message to draft room..."
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
            />
            <button
              type="submit"
              className="px-3 py-1.5 rounded bg-[#00ceb8] hover:bg-[#2bd9c6] text-[#090e17] text-xs font-bold"
            >
              Send
            </button>
          </form>
        </div>
      )}
    </div>
  );
};

export default DraftRosterQueue;
