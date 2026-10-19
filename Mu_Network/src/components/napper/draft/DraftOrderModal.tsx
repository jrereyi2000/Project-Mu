import React, { useState, useEffect } from "react";
import { NapperDraftTeam, ARCHETYPE_METAS, NapperManagerArchetype, getArchetypeMeta } from "../../../types/napper";

interface DraftOrderModalProps {
  isOpen: boolean;
  onClose: () => void;
  teams: NapperDraftTeam[];
  onApplyOrder: (orderedTeams: NapperDraftTeam[]) => void;
}

export const DraftOrderModal: React.FC<DraftOrderModalProps> = ({
  isOpen,
  onClose,
  teams,
  onApplyOrder,
}) => {
  const [orderedTeams, setOrderedTeams] = useState<NapperDraftTeam[]>([]);
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);

  // Sync with prop when opened
  useEffect(() => {
    if (isOpen) {
      const sorted = [...teams].sort((a, b) => a.slot - b.slot);
      setOrderedTeams(sorted);
      setDraggedIndex(null);
      setDragOverIndex(null);
    }
  }, [isOpen, teams]);

  if (!isOpen) return null;

  // Move item from one index to another (drag-and-drop)
  const handleMove = (fromIndex: number, toIndex: number) => {
    if (fromIndex === toIndex) return;
    const updated = [...orderedTeams];
    const [moved] = updated.splice(fromIndex, 1);
    updated.splice(toIndex, 0, moved);
    // Re-index slots
    const reindexed = updated.map((t, idx) => ({ ...t, slot: idx + 1 }));
    setOrderedTeams(reindexed);
  };

  // Swap item with target slot from dropdown
  const handleSlotSelect = (currentIndex: number, targetSlot: number) => {
    const targetIndex = targetSlot - 1;
    if (targetIndex === currentIndex || targetIndex < 0 || targetIndex >= orderedTeams.length) {
      return;
    }
    const updated = [...orderedTeams];
    const temp = updated[currentIndex];
    updated[currentIndex] = updated[targetIndex];
    updated[targetIndex] = temp;
    const reindexed = updated.map((t, idx) => ({ ...t, slot: idx + 1 }));
    setOrderedTeams(reindexed);
  };

  // Move up by 1 slot
  const handleMoveUp = (index: number) => {
    if (index <= 0) return;
    handleMove(index, index - 1);
  };

  // Move down by 1 slot
  const handleMoveDown = (index: number) => {
    if (index >= orderedTeams.length - 1) return;
    handleMove(index, index + 1);
  };

  // Randomize all slots
  const handleRandomize = () => {
    const shuffled = [...orderedTeams];
    for (let i = shuffled.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    const reindexed = shuffled.map((t, idx) => ({ ...t, slot: idx + 1 }));
    setOrderedTeams(reindexed);
  };

  // Reset to original order when opened
  const handleReset = () => {
    const sorted = [...teams].sort((a, b) => a.slot - b.slot);
    setOrderedTeams(sorted);
  };

  // Update archetype for a team
  const handleArchetypeChange = (index: number, newArchetype: NapperManagerArchetype) => {
    const updated = [...orderedTeams];
    updated[index] = {
      ...updated[index],
      persona: newArchetype,
    };
    setOrderedTeams(updated);
  };

  // Reverse 1-N order
  const handleReverse = () => {
    const reversed = [...orderedTeams].reverse();
    const reindexed = reversed.map((t, idx) => ({ ...t, slot: idx + 1 }));
    setOrderedTeams(reindexed);
  };

  // Apply changes
  const handleApply = () => {
    onApplyOrder(orderedTeams);
    onClose();
  };

  const userTeam = orderedTeams.find((t) => t.is_user);
  const userSlot = userTeam ? userTeam.slot : 1;

  return (
    <div className="draft-modal-overlay" onClick={onClose}>
      <div
        className="draft-order-modal bg-[#121926] border border-[#223046] rounded-2xl w-[680px] max-w-[94vw] max-h-[90vh] flex flex-col shadow-[0_20px_60px_rgba(0,0,0,0.8)] overflow-hidden animate-fadeIn"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal Header */}
        <div className="px-6 py-5 border-b border-[#223046] flex items-center justify-between bg-[#172132]/60">
          <div className="space-y-1">
            <div className="flex items-center gap-2.5">
              <span className="text-xl">↕</span>
              <h2 className="text-lg font-black text-white tracking-tight uppercase">
                Customize Draft Order
              </h2>
              <span className="text-[11px] font-bold text-[#00ceb8] bg-[#00ceb8]/10 px-2 py-0.5 rounded-full border border-[#00ceb8]/30">
                {orderedTeams.length} Teams
              </span>
            </div>
            <p className="text-xs text-[#8a99ad]">
              Reorder slots, swap positions, or manually assign draft spots and archetypes for all teams.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-8 h-8 rounded-full bg-[#1e2a3c] hover:bg-[#283850] text-[#8a99ad] hover:text-white flex items-center justify-center text-sm font-bold transition-colors cursor-pointer"
            title="Close dialog"
          >
            ✕
          </button>
        </div>

        {/* Quick Actions Toolbar */}
        <div className="px-6 py-3 bg-[#0e1624] border-b border-[#1c293c] flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-bold uppercase tracking-wider text-[#63758e]">
              Quick Actions:
            </span>
            <button
              type="button"
              onClick={handleRandomize}
              className="px-2.5 py-1 rounded-lg bg-[#182335] hover:bg-[#202e45] border border-[#263750] hover:border-[#00ceb8]/50 text-xs font-bold text-white hover:text-[#00ceb8] transition-all flex items-center gap-1.5 cursor-pointer"
              title="Randomize the entire draft order"
            >
              <span>🎲</span>
              <span>Randomize</span>
            </button>
            <button
              type="button"
              onClick={handleReverse}
              className="px-2.5 py-1 rounded-lg bg-[#182335] hover:bg-[#202e45] border border-[#263750] hover:border-[#8a99ad] text-xs font-bold text-[#8a99ad] hover:text-white transition-all flex items-center gap-1.5 cursor-pointer"
              title="Reverse the current draft order"
            >
              <span>⇅</span>
              <span>Reverse</span>
            </button>
            <button
              type="button"
              onClick={handleReset}
              className="px-2.5 py-1 rounded-lg bg-[#182335] hover:bg-[#202e45] border border-[#263750] hover:border-[#8a99ad] text-xs font-bold text-[#8a99ad] hover:text-white transition-all flex items-center gap-1.5 cursor-pointer"
              title="Reset order to default"
            >
              <span>↺</span>
              <span>Reset</span>
            </button>
          </div>

          {/* User Slot Highlight Indicator */}
          <div className="text-[11px] font-bold text-[#8a99ad] flex items-center gap-1.5">
            <span>Your Spot:</span>
            <span className="text-[#00ceb8] bg-[#00ceb8]/15 px-2 py-0.5 rounded border border-[#00ceb8]/30 font-black">
              Slot #{userSlot}
            </span>
          </div>
        </div>

        {/* Teams List (Scrollable) */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-2 napper-scrollbar">
          {orderedTeams.map((team, index) => {
            const isUser = !!team.is_user;
            const meta = getArchetypeMeta(team.persona);
            const isDragging = draggedIndex === index;
            const isDragOver = dragOverIndex === index;

            return (
              <div
                key={`order-item-${team.id || team.name}-${index}`}
                draggable
                onDragStart={() => setDraggedIndex(index)}
                onDragOver={(e) => {
                  e.preventDefault();
                  if (dragOverIndex !== index) setDragOverIndex(index);
                }}
                onDragLeave={() => {
                  if (dragOverIndex === index) setDragOverIndex(null);
                }}
                onDrop={() => {
                  if (draggedIndex !== null) {
                    handleMove(draggedIndex, index);
                  }
                  setDraggedIndex(null);
                  setDragOverIndex(null);
                }}
                onDragEnd={() => {
                  setDraggedIndex(null);
                  setDragOverIndex(null);
                }}
                className={`draft-order-row flex items-center justify-between gap-3 p-2.5 rounded-xl border transition-all ${
                  isUser
                    ? "bg-[#00ceb8]/10 border-[#00ceb8]/40 shadow-[0_0_15px_rgba(0,206,184,0.12)]"
                    : "bg-[#162133] hover:bg-[#1a273c] border-[#223147]"
                } ${isDragging ? "opacity-40 scale-[0.98]" : ""} ${
                  isDragOver ? "border-t-2 border-t-[#00ceb8]" : ""
                }`}
              >
                {/* Left: Drag Handle, Slot Badge, Avatar, Team Info */}
                <div className="flex items-center gap-3 min-w-0 flex-1">
                  {/* Drag Handle */}
                  <span
                    className="text-xs text-[#52647d] hover:text-[#8a99ad] cursor-grab active:cursor-grabbing select-none px-1 shrink-0"
                    title="Drag to reorder"
                  >
                    ⠿
                  </span>

                  {/* Slot Pill */}
                  <div
                    className={`w-9 h-8 rounded-lg flex items-center justify-center text-xs font-black shrink-0 ${
                      isUser
                        ? "bg-[#00ceb8] text-[#090e17] shadow-[0_0_10px_rgba(0,206,184,0.4)]"
                        : "bg-[#0e1624] text-[#8a99ad] border border-[#24354c]"
                    }`}
                  >
                    #{team.slot}
                  </div>

                  {/* Avatar */}
                  <img
                    src={team.avatar_url || "/static/players/robot_avatar.webp"}
                    alt={team.name}
                    className="w-8 h-8 rounded-full object-cover bg-[#090e17] border border-[#283850] shrink-0"
                  />

                  {/* Team Name & Handle */}
                  <div className="min-w-0 flex flex-col">
                    <div className="flex items-center gap-2">
                      <span
                        className={`text-xs font-black truncate ${
                          isUser ? "text-white font-extrabold" : "text-[#d1d9e2]"
                        }`}
                      >
                        {team.name}
                      </span>
                    </div>
                    <div className="text-[11px] text-[#718299] truncate font-medium">
                      {team.manager_handle}
                    </div>
                  </div>
                </div>

                {/* Middle: Prominent AI Archetype Badge & Selector */}
                <div className="shrink-0 flex items-center">
                  {isUser ? (
                    <span className="px-2.5 py-1 rounded-lg text-[10px] font-black uppercase tracking-wider text-[#00ceb8] bg-[#00ceb8]/15 border border-[#00ceb8]/40 shadow-sm flex items-center gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-[#00ceb8]" />
                      <span>YOU</span>
                    </span>
                  ) : (
                    <div className="relative flex items-center">
                      <select
                        value={meta.key}
                        onChange={(e) => handleArchetypeChange(index, e.target.value as NapperManagerArchetype)}
                        className="appearance-none cursor-pointer pl-6 pr-6 py-1 rounded-lg text-[10px] font-black tracking-wider uppercase border outline-none transition-all hover:brightness-110 shadow-sm"
                        style={{
                          color: meta.color,
                          backgroundColor: meta.bgColor,
                          borderColor: meta.borderColor,
                        }}
                        title={`${meta.label} (${meta.tagline}): ${meta.description} (Click to customize archetype)`}
                      >
                        {Object.values(ARCHETYPE_METAS).map((aMeta) => (
                          <option
                            key={`opt-arch-${team.id || index}-${aMeta.key}`}
                            value={aMeta.key}
                            className="bg-[#121926] text-white py-1 text-xs"
                          >
                            {aMeta.label}
                          </option>
                        ))}
                      </select>
                      {/* Color Dot */}
                      <span
                        className="absolute left-2.5 w-1.5 h-1.5 rounded-full pointer-events-none"
                        style={{ backgroundColor: meta.color }}
                      />
                      {/* Down Caret */}
                      <span
                        className="absolute right-2 text-[8px] pointer-events-none opacity-80"
                        style={{ color: meta.color }}
                      >
                        ▼
                      </span>
                    </div>
                  )}
                </div>

                {/* Right: Slot Selector & Arrow Steppers */}
                <div className="flex items-center gap-2 shrink-0">
                  {/* Move directly to Slot Selector */}
                  <div className="flex items-center gap-1">
                    <span className="text-[10px] font-bold uppercase text-[#63758e] hidden sm:inline">
                      Slot:
                    </span>
                    <select
                      value={team.slot}
                      onChange={(e) => handleSlotSelect(index, Number(e.target.value))}
                      className="px-2 py-1 rounded-lg bg-[#0e1624] border border-[#24354c] text-xs font-bold text-white hover:border-[#00ceb8]/50 focus:border-[#00ceb8] outline-none cursor-pointer"
                      title={`Reassign ${team.name} to slot`}
                    >
                      {Array.from({ length: orderedTeams.length }, (_, sIdx) => sIdx + 1).map((s) => (
                        <option key={`opt-team-slot-${team.id || index}-${s}`} value={s}>
                          Slot {s}
                        </option>
                      ))}
                    </select>
                  </div>

                  {/* Move Up Button */}
                  <button
                    type="button"
                    onClick={() => handleMoveUp(index)}
                    disabled={index === 0}
                    className="w-7 h-7 rounded-lg bg-[#0e1624] hover:bg-[#202e45] disabled:opacity-30 disabled:hover:bg-[#0e1624] border border-[#24354c] text-[#8a99ad] hover:text-white flex items-center justify-center text-xs font-black transition-colors cursor-pointer disabled:cursor-not-allowed"
                    title="Move up 1 slot"
                  >
                    ▲
                  </button>

                  {/* Move Down Button */}
                  <button
                    type="button"
                    onClick={() => handleMoveDown(index)}
                    disabled={index === orderedTeams.length - 1}
                    className="w-7 h-7 rounded-lg bg-[#0e1624] hover:bg-[#202e45] disabled:opacity-30 disabled:hover:bg-[#0e1624] border border-[#24354c] text-[#8a99ad] hover:text-white flex items-center justify-center text-xs font-black transition-colors cursor-pointer disabled:cursor-not-allowed"
                    title="Move down 1 slot"
                  >
                    ▼
                  </button>
                </div>
              </div>
            );
          })}
        </div>

        {/* Modal Footer */}
        <div className="px-6 py-4 bg-[#151f2f] border-t border-[#223046] flex items-center justify-between">
          <div className="text-xs text-[#8a99ad]">
            Snake order reverses automatically every round.
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl bg-transparent hover:bg-[#1f2b3e] text-xs font-bold text-[#8a99ad] hover:text-white transition-colors cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleApply}
              className="px-5 py-2 rounded-xl bg-[#00ceb8] hover:bg-[#2bd9c6] text-[#090e17] text-xs font-black uppercase tracking-wider transition-all cursor-pointer shadow-[0_0_15px_rgba(0,206,184,0.3)] active:scale-95"
            >
              Save & Apply Order
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
