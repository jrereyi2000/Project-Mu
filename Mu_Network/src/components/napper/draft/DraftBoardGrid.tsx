import React, { useRef, useEffect } from "react";
import type { NapperDraftState, NapperDraftPick } from "../../../types/napper";
import { ARCHETYPE_METAS } from "../../../types/napper";
import { getPlayerHeadshotUrl, handleImageError } from "../napperUtils";

interface DraftBoardGridProps {
  draftState: NapperDraftState;
  onClaimSlot: (slot: number) => void;
  onSelectPick?: (pick: NapperDraftPick) => void;
}

export const DraftBoardGrid: React.FC<DraftBoardGridProps> = ({
  draftState,
  onClaimSlot,
  onSelectPick,
}) => {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const activeCellRef = useRef<HTMLDivElement>(null);

  const {
    teams_count = 10,
    rounds_count = 15,
    current_pick_index,
    user_slot,
    status,
    teams,
    picks,
  } = draftState;

  // Auto-scroll to on-the-clock cell when pick index changes
  useEffect(() => {
    if (activeCellRef.current && scrollContainerRef.current) {
      activeCellRef.current.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
        inline: "nearest",
      });
    }
  }, [current_pick_index]);

  const handleScrollUp = () => {
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollBy({ top: -140, behavior: "smooth" });
    }
  };

  const handleScrollDown = () => {
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollBy({ top: 140, behavior: "smooth" });
    }
  };

  // Helper to get pick object for round r (1-based) and column c (1-based)
  const getPickForCell = (r: number, c: number): NapperDraftPick | undefined => {
    return picks.find((p) => p.round === r && p.team_slot === c);
  };

  const getPositionStyle = (pos?: string) => {
    switch (pos?.toUpperCase()) {
      case "QB":
        return { color: "#f43f5e", bg: "rgba(244, 63, 94, 0.2)", border: "1px solid rgba(244, 63, 94, 0.45)" };
      case "RB":
        return { color: "#10b981", bg: "rgba(16, 185, 129, 0.2)", border: "1px solid rgba(16, 185, 129, 0.45)" };
      case "WR":
        return { color: "#06b6d4", bg: "rgba(6, 182, 212, 0.2)", border: "1px solid rgba(6, 182, 212, 0.45)" };
      case "TE":
        return { color: "#f59e0b", bg: "rgba(245, 158, 11, 0.2)", border: "1px solid rgba(245, 158, 11, 0.45)" };
      case "K":
        return { color: "#8b5cf6", bg: "rgba(139, 92, 246, 0.2)", border: "1px solid rgba(139, 92, 246, 0.45)" };
      case "DEF":
        return { color: "#94a3b8", bg: "rgba(148, 163, 184, 0.2)", border: "1px solid rgba(148, 163, 184, 0.45)" };
      default:
        return { color: "#38bdf8", bg: "rgba(56, 189, 248, 0.2)", border: "1px solid rgba(56, 189, 248, 0.45)" };
    }
  };

  return (
    <div className="draft-board-wrapper">
      <div className="draft-board-scroll-container" ref={scrollContainerRef}>
        <div className="draft-board-table">
          {/* Claim Header Row */}
          <div className="draft-claim-row">
            {Array.from({ length: teams_count }, (_, i) => {
              const slotNum = i + 1;
              const team = teams.find((t) => t.slot === slotNum);
              const isClaimedByMe = user_slot === slotNum;
              const isDraftActive = status === "IN_PROGRESS" || status === "COMPLETED";
              const persona = team?.persona as any;
              const meta = persona && (persona in ARCHETYPE_METAS) ? (ARCHETYPE_METAS as any)[persona] : null;

              return (
                <div key={`col-head-${slotNum}`} className="draft-team-header-cell">
                  <button
                    type="button"
                    disabled={isDraftActive}
                    onClick={() => onClaimSlot(slotNum)}
                    className={`draft-claim-pill ${isClaimedByMe ? "claimed" : ""}`}
                    title={isDraftActive ? "Slot locked during draft" : `Click to draft from slot ${slotNum}`}
                  >
                    {isClaimedByMe ? "YOU" : "CLAIM"}
                  </button>
                  <div className="draft-team-name-label">
                    {team?.name || `Team ${slotNum}`}
                  </div>
                  <div className="draft-team-archetype-label mt-0.5">
                    {isClaimedByMe ? (
                      <span className="draft-archetype-pill user">YOU</span>
                    ) : meta ? (
                      <span
                        className="draft-archetype-pill"
                        style={{
                          color: meta.color,
                          backgroundColor: meta.bgColor,
                          borderColor: meta.borderColor,
                        }}
                        title={`${meta.label}: ${meta.tagline}`}
                      >
                        {meta.shortLabel}
                      </span>
                    ) : (
                      <span className="draft-archetype-pill ai">AI</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Rounds Grid */}
          {Array.from({ length: rounds_count }, (_, rIdx) => {
            const roundNum = rIdx + 1;
            const isOdd = roundNum % 2 === 1;

            return (
              <div key={`round-row-${roundNum}`} className="draft-round-row">
                {Array.from({ length: teams_count }, (_, cIdx) => {
                  const colNum = cIdx + 1;
                  const pickObj = getPickForCell(roundNum, colNum);
                  const isCurrentClock =
                    status === "IN_PROGRESS" &&
                    Boolean(pickObj && current_pick_index === pickObj.pick_overall - 1);
                  const isPicked = Boolean(pickObj?.player);
                  const arrowSymbol = isOdd
                    ? colNum < teams_count
                      ? "→"
                      : "↓"
                    : colNum > 1
                    ? "←"
                    : "↓";
                  const posStyle = isPicked ? getPositionStyle(pickObj?.player?.position) : null;
                  const isUserCol = user_slot === colNum;
                  const roundPick =
                    pickObj?.round_pick ?? (isOdd ? colNum : teams_count - colNum + 1);
                  const overallPick =
                    pickObj?.pick_overall ?? (roundNum - 1) * teams_count + roundPick;

                  return (
                    <div
                      key={`cell-${roundNum}-${colNum}`}
                      ref={isCurrentClock ? activeCellRef : null}
                      onClick={() => pickObj && onSelectPick?.(pickObj)}
                      className={`draft-cell ${isCurrentClock ? "active-clock" : ""} ${isUserCol ? "user-slot-cell" : ""}`}
                      title={`Round ${roundNum}, Pick ${roundPick} (Overall #${overallPick})${pickObj?.team_name ? ` • ${pickObj.team_name}` : ""}`}
                    >
                      {/* Cell Header: Pick Number and Arrow */}
                      <div className="flex items-center justify-between w-full">
                        <span className="draft-cell-index">
                          {roundNum}.{roundPick}
                        </span>
                        {!isPicked && (
                          <span className="draft-snake-arrow font-mono">{arrowSymbol}</span>
                        )}
                        {isCurrentClock && (
                          <span className="flex h-2 w-2 relative">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#00ceb8] opacity-75"></span>
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-[#00ceb8]"></span>
                          </span>
                        )}
                      </div>

                      {/* Cell Body: Player Info if picked, else empty slot */}
                      {isPicked && pickObj?.player ? (
                        <div className="draft-picked-content">
                          <div className="draft-picked-avatar-box">
                            <img
                              src={getPlayerHeadshotUrl(
                                pickObj.player.player_id,
                                pickObj.player.headshot_url
                              )}
                              alt={pickObj.player.name}
                              className="draft-picked-headshot"
                              onError={handleImageError}
                            />
                            <div className="draft-picked-team-chip">
                              {pickObj.player.team}
                            </div>
                          </div>
                          <div className="draft-picked-info">
                            <div className="draft-picked-name" title={pickObj.player.name}>
                              {pickObj.player.name}
                            </div>
                            <div className="draft-picked-meta">
                              {posStyle && (
                                <span
                                  className="draft-picked-pos-badge"
                                  style={{
                                    color: posStyle.color,
                                    backgroundColor: posStyle.bg,
                                    border: posStyle.border,
                                  }}
                                >
                                  {pickObj.player.position}
                                </span>
                              )}
                              <span className="draft-picked-team">
                                {pickObj.player.team}
                              </span>
                            </div>
                          </div>
                        </div>
                      ) : (
                        <div className="flex-1 flex items-center justify-center">
                          {isCurrentClock ? (
                            <span className="text-[10px] font-black text-[#00ceb8] tracking-wider animate-pulse uppercase">
                              ON CLOCK
                            </span>
                          ) : null}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>

      {/* Floating Scroll Nav Chevrons */}
      <div className="draft-floating-nav">
        <button
          type="button"
          onClick={handleScrollUp}
          className="draft-scroll-btn"
          title="Scroll Board Up"
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
            <polyline points="18 15 12 9 6 15" />
          </svg>
        </button>
        <button
          type="button"
          onClick={handleScrollDown}
          className="draft-scroll-btn"
          title="Scroll Board Down"
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      </div>
    </div>
  );
};

export default DraftBoardGrid;
